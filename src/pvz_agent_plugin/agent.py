from __future__ import annotations

import json
import threading
import time
from collections import Counter, deque
from typing import Any
from urllib.parse import urlparse

import httpx


def uses_deepseek_extensions(base_url: str) -> bool:
    """Only send DeepSeek-specific request fields to the official DeepSeek host."""
    host = (urlparse(base_url).hostname or "").lower()
    return host == "deepseek.com" or host.endswith(".deepseek.com")

from .plugin import PvZPlugin
from .level_scripts import get_adventure_script
from .strategy_prompts import build_strategy_assist_prompt


PVZ_TOOL = {
    "type": "function",
    "function": {
        "name": "pvz_action",
        "description": "Operate PvZ only through semantic actions. Sun is collected automatically.",
        "parameters": {
            "type": "object",
            "required": ["action"],
            "properties": {
                "action": {"type": "string", "enum": [
                    "place_plant", "shovel", "fire_cob", "select_seeds",
                    "collect_items", "set_auto_collect", "wait", "terminate"
                ]},
                "card_index": {"type": "integer"},
                "row": {"type": "integer", "minimum": 0, "maximum": 5},
                "col": {"type": "integer", "minimum": 0, "maximum": 8},
                "target_row": {"type": "integer", "minimum": 0, "maximum": 5},
                "target_col": {"type": "integer", "minimum": 0, "maximum": 8},
                "seeds": {"type": "array", "items": {"type": "integer", "minimum": 0, "maximum": 47}, "maxItems": 10},
                "selection_reason": {"type": "string", "maxLength": 240},
                "decision_reason": {"type": "string", "maxLength": 180},
                "user_reply": {"type": "string", "maxLength": 120},
                "replacement_card_index": {"type": "integer", "minimum": 0},
                "enabled": {"type": "boolean"},
                "time": {"type": "number", "minimum": 0.1, "maximum": 10},
                "status": {"type": "string", "enum": ["success", "failure"]},
            },
        },
    },
}


class DeepSeekAgent:
    def __init__(
        self,
        plugin: PvZPlugin,
        base_url: str,
        api_key: str,
        model: str,
        strategy_assist_enabled: bool = False,
    ) -> None:
        self.plugin = plugin
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.strategy_assist_enabled = bool(strategy_assist_enabled)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._goal = ""
        self.last_error = ""
        self.iteration = 0
        self.last_action: dict[str, Any] | None = None
        self.phase = "idle"
        self.system_failures = 0
        self.action_rejections = 0
        self._action_feedback: dict[str, Any] | None = None
        self._selection_submitted = False
        self._selection_ready_key = ""
        self._selection_ready_since = 0.0
        self._level_memories: dict[str, list[dict[str, Any]]] = {}
        self._last_adventure_level = 0
        self._reward_key = ""
        self._reward_step = 0
        self._reward_next_at = 0.0
        self._battle_clock_value: int | None = None
        self._battle_clock_changed_at = time.monotonic()
        self._freeze_reported = False
        self._instruction_condition = threading.Condition()
        self._instruction_sequence = 0
        self._instruction_level_key = ""
        self._pending_instructions: deque[dict[str, Any]] = deque()
        self._active_instructions: list[dict[str, Any]] = []
        self._instruction_records: dict[str, dict[str, Any]] = {}
        self._latest_instruction_id = ""
        self._client = self._new_client()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self, goal: str) -> None:
        if self.running:
            return
        if not self.api_key:
            raise RuntimeError("未配置 DEEPSEEK_API_KEY")
        if not self.plugin.connected:
            self.plugin.connect()
        self._goal = goal.strip() or "完成当前关卡"
        self._stop.clear()
        self.last_error = ""
        self.iteration = 0
        self.last_action = None
        self.phase = "starting"
        self.system_failures = 0
        self.action_rejections = 0
        self._action_feedback = None
        self._selection_submitted = False
        self._selection_ready_key = ""
        self._selection_ready_since = 0.0
        self._battle_clock_value = None
        self._battle_clock_changed_at = time.monotonic()
        self._freeze_reported = False
        self._thread = threading.Thread(target=self._run, name="pvz-agent", daemon=True)
        self._thread.start()
        self.plugin.emit("agent", "started", goal=self._goal, model=self.model, base_url=self.base_url)

    def stop(self) -> None:
        self._stop.set()
        self.plugin.emit("agent", "stopping")

    def configure(self, *, base_url: str, api_key: str | None, model: str) -> None:
        if self.running:
            raise RuntimeError("请先停止 Agent，再修改 API 设置")
        self.base_url = base_url.rstrip("/")
        if api_key:
            self.api_key = api_key.strip()
        self.model = model.strip()
        self._reset_client()

    def status(self) -> dict[str, Any]:
        instruction_status = self.user_instruction_status()
        return {
            "running": self.running, "goal": self._goal, "last_error": self.last_error,
            "model": self.model, "iteration": self.iteration, "last_action": self.last_action,
            "phase": self.phase, "system_failures": self.system_failures,
            "action_rejections": self.action_rejections,
            "strategy_assist_enabled": self.strategy_assist_enabled,
            "user_instruction": instruction_status,
        }

    def submit_user_instruction(
        self, text: str, *, auto_start_goal: str | None = None,
    ) -> dict[str, Any]:
        instruction = text.strip()
        if not instruction:
            raise ValueError("用户指令不能为空")
        if len(instruction) > 500:
            raise ValueError("用户指令不能超过 500 个字符")
        with self._instruction_condition:
            self._instruction_sequence += 1
            instruction_id = f"ui-{self._instruction_sequence:06d}"
            record = {
                "id": instruction_id,
                "text": instruction,
                "state": "queued",
                "reply": "",
                "action": None,
                "level_key": self._instruction_level_key,
                "created_at": time.time(),
                "updated_at": time.time(),
                "version": 1,
                "error": "",
            }
            self._pending_instructions.append(record)
            self._instruction_records[instruction_id] = record
            self._latest_instruction_id = instruction_id
            self._instruction_condition.notify_all()
        self.plugin.emit(
            "agent", "user_instruction_queued",
            instruction_id=instruction_id, instruction=instruction,
        )
        if not self.running and auto_start_goal is not None:
            try:
                self.start(auto_start_goal)
            except Exception as exc:
                self._fail_instruction(instruction_id, str(exc))
                raise
        elif not self.running:
            self._fail_instruction(instruction_id, "Agent 尚未运行，请先启动 Agent")
            raise RuntimeError("Agent 尚未运行，请先启动 Agent")
        return self._public_instruction(record)

    def user_instruction_status(self, instruction_id: str | None = None) -> dict[str, Any]:
        with self._instruction_condition:
            if instruction_id:
                record = self._instruction_records.get(instruction_id)
                if not record:
                    raise KeyError(instruction_id)
                return self._public_instruction(record)
            latest = self._instruction_records.get(self._latest_instruction_id)
            return {
                "level_key": self._instruction_level_key,
                "pending_count": len(self._pending_instructions),
                "active": [self._public_instruction(item) for item in self._active_instructions],
                "latest": self._public_instruction(latest) if latest else None,
            }

    def wait_user_instruction(
        self, instruction_id: str, version: int, timeout: float = 1.0,
    ) -> dict[str, Any]:
        with self._instruction_condition:
            record = self._instruction_records.get(instruction_id)
            if not record:
                raise KeyError(instruction_id)
            if int(record.get("version") or 0) <= version and record.get("state") not in {"active", "failed", "expired"}:
                self._instruction_condition.wait(timeout=max(0.0, timeout))
            record = self._instruction_records.get(instruction_id)
            if not record:
                raise KeyError(instruction_id)
            return self._public_instruction(record)

    @staticmethod
    def _public_instruction(record: dict[str, Any] | None) -> dict[str, Any] | None:
        if not record:
            return None
        return {
            key: record.get(key)
            for key in (
                "id", "text", "state", "reply", "action", "level_key",
                "created_at", "updated_at", "version", "error",
            )
        }

    def configure_strategy_assist(self, enabled: bool) -> None:
        self.strategy_assist_enabled = bool(enabled)
        self.plugin.emit(
            "agent", "strategy_assist_changed",
            enabled=self.strategy_assist_enabled,
        )

    def _run(self) -> None:
        while not self._stop.is_set():
            iteration_started = time.perf_counter()
            try:
                self.phase = "observing"
                context = self.plugin.context_text(snapshot=False)
                observed = json.loads(context)
                game = observed.get("game", {})
                flow = observed.get("flow", {})
                if game.get("mode") == 0 and game.get("ui") in (2, 3):
                    self._last_adventure_level = int(game.get("level") or 0)
                if game.get("paused"):
                    self.phase = "waiting_for_user_resume"
                    self.last_error = ""
                    self._stop.wait(0.5)
                    continue
                if flow.get("phase") == "battle" and flow.get("agent_enabled", False):
                    if not self._battle_clock_is_moving(game):
                        self.phase = "game_frozen"
                        self.last_error = "游戏时钟超过 10 秒没有变化，已停止 Agent，防止继续请求或注入"
                        if not self._freeze_reported:
                            self.plugin.emit(
                                "error", "game_clock_frozen", level="error",
                                clock=game.get("clock"), level_key=flow.get("level_key"),
                            )
                            self._freeze_reported = True
                        break
                else:
                    self._battle_clock_value = None
                    self._battle_clock_changed_at = time.monotonic()
                if flow.get("phase") == "reward":
                    completed_level = self._last_adventure_level or int(game.get("level") or 0)
                    reward_key = f"adventure:{completed_level:02d}:{flow.get('reward_flow', 'next_level')}"
                    self.phase = "waiting_reward_manual"
                    self.last_error = ""
                    if self._reward_key != reward_key:
                        self._reward_key = reward_key
                        self.plugin.emit(
                            "flow", "reward_manual_handoff",
                            completed_level=completed_level,
                            reward_flow=flow.get("reward_flow", "next_level"),
                            reason="奖励领取和下一关切换由玩家手动完成",
                        )
                    self._stop.wait(0.5)
                    continue
                if flow and not flow.get("agent_enabled", False):
                    self._reset_selection_stability()
                    self.phase = f"waiting_{flow.get('phase', 'manual_transition')}"
                    self.last_error = ""
                    self._stop.wait(0.5)
                    continue
                if game.get("ui") != 2:
                    self._selection_submitted = False
                    self._reset_selection_stability()
                elif self._selection_submitted:
                    self.phase = "waiting_for_level_intro"
                    self.last_error = ""
                    self._stop.wait(0.5)
                    continue
                if flow.get("phase") == "seed_selection":
                    selection_key = str(flow.get("level_key") or game.get("level") or "chooser")
                    if not self._selection_ui_is_stable(selection_key):
                        self.phase = "waiting_seed_selection_ui_stable"
                        self.last_error = ""
                        self._stop.wait(0.25)
                        continue
                else:
                    self._reset_selection_stability()
                if not game.get("in_battle") and game.get("ui") != 2:
                    self.phase = "waiting_for_manual_transition"
                    self.last_error = ""
                    self._stop.wait(0.5)
                    continue

                context, level_key = self._context_with_level_memory(observed)
                self._sync_instruction_level(level_key)
                pending_instruction = self._peek_pending_instruction()

                self.iteration += 1
                self.plugin.emit("agent", "iteration_started", iteration=self.iteration, goal=self._goal)
                self.plugin.snapshot_context(context, reason="agent_observation")
                self.phase = "thinking_user_instruction" if pending_instruction else "thinking"
                if pending_instruction:
                    self._update_instruction(pending_instruction["id"], state="thinking")
                try:
                    call = self._request_action(
                        context,
                        feedback=self._action_feedback,
                        pending_instruction=pending_instruction,
                        active_instructions=self._active_instruction_payload(),
                    )
                except Exception as exc:
                    if pending_instruction:
                        self._fail_instruction(pending_instruction["id"], str(exc))
                    raise
                user_reply = str(call.pop("user_reply", "") or "").strip()
                action = call.pop("action")
                if pending_instruction:
                    self._complete_instruction(
                        pending_instruction["id"], user_reply, action, call, level_key,
                    )
                if action == "terminate":
                    self.plugin.emit("agent", "terminated", iteration=self.iteration, status=call.get("status", "success"))
                    break
                self.phase = "acting"
                try:
                    result = self.plugin.execute(action, _cancel_event=self._stop, **call)
                except (ValueError, TimeoutError, RuntimeError) as exc:
                    if not self.plugin.connected:
                        raise
                    self.action_rejections += 1
                    self.system_failures = 0
                    self.last_error = str(exc)
                    is_injection_timeout = "远程线程执行超时" in str(exc)
                    self._action_feedback = {
                        "status": "rejected",
                        "action": action,
                        "arguments": call,
                        "error": str(exc),
                        "instruction": (
                            "The native action timed out. Re-observe the current phase and cards; never reuse a stale card index."
                            if is_injection_timeout else
                            "Do not repeat the same invalid action. Use the selected card's legal_targets and choose a legal alternative."
                        ),
                    }
                    self.last_action = {
                        "action": action, "arguments": call,
                        "result": {"status": "rejected", "error": str(exc)},
                    }
                    self._remember_level_action(
                        level_key, action, call, "rejected", {"error": str(exc)}
                    )
                    self.plugin.emit(
                        "agent", "action_rejected", level="warning", iteration=self.iteration,
                        action=action, arguments=call, error_type=type(exc).__name__, error=str(exc),
                        action_rejections=self.action_rejections,
                        duration_ms=round((time.perf_counter() - iteration_started) * 1000, 2),
                    )
                    self.phase = "recovering_injection" if is_injection_timeout else "recovering_action"
                    self._stop.wait(1.0 if is_injection_timeout else 0.15)
                    continue
                self.last_action = {"action": action, "arguments": call, "result": result}
                self._remember_level_action(level_key, action, call, result.get("status", "ok"), result)
                if action == "select_seeds" and result.get("status") in {"ok", "waiting"}:
                    self._selection_submitted = True
                self._action_feedback = None
                self.last_error = ""
                self.plugin.emit(
                    "agent", "iteration_completed", iteration=self.iteration, action=action,
                    arguments=call, result=result,
                    duration_ms=round((time.perf_counter() - iteration_started) * 1000, 2),
                )
                self.system_failures = 0
            except Exception as exc:
                self.system_failures += 1
                self.last_error = str(exc)
                self.plugin.emit(
                    "error", "agent_error", level="error", iteration=self.iteration,
                    error_type=type(exc).__name__, error=str(exc), failures=self.system_failures,
                    duration_ms=round((time.perf_counter() - iteration_started) * 1000, 2),
                )
                if not self.plugin.connected and not self._stop.is_set():
                    try:
                        self.phase = "reconnecting_game"
                        self.plugin.connect()
                        self._selection_submitted = False
                        self.plugin.emit("agent", "game_reconnected", iteration=self.iteration)
                        self.system_failures = 0
                        self.last_error = ""
                        continue
                    except Exception as reconnect_error:
                        self.last_error = str(reconnect_error)
                if self.system_failures >= 5:
                    self.plugin.emit("agent", "stopped_after_failures", level="error", failures=self.system_failures)
                    break
                self.phase = "recovering_system"
                self._stop.wait(min(0.5 * (2 ** (self.system_failures - 1)), 5.0))
        self._stop.set()
        self._fail_all_pending_instructions("Agent 已停止，指令未执行")
        self.phase = "stopped"
        self.plugin.emit("agent", "stopped", iteration=self.iteration, last_error=self.last_error)

    def _battle_clock_is_moving(self, game: dict[str, Any]) -> bool:
        clock = int(game.get("clock") or 0)
        now = time.monotonic()
        if self._battle_clock_value != clock:
            self._battle_clock_value = clock
            self._battle_clock_changed_at = now
            self._freeze_reported = False
            return True
        return now - self._battle_clock_changed_at <= 10.0

    def _selection_ui_is_stable(self, level_key: str) -> bool:
        """Require a visible chooser to remain available before asking the AI."""
        now = time.monotonic()
        if self._selection_ready_key != level_key:
            self._selection_ready_key = level_key
            self._selection_ready_since = now
            return False
        return now - self._selection_ready_since >= 1.0

    def _reset_selection_stability(self) -> None:
        self._selection_ready_key = ""
        self._selection_ready_since = 0.0

    def _advance_reward_locally(self, game: dict[str, Any], flow: dict[str, Any]) -> None:
        """Retained for compatibility; reward screens are deliberately manual."""
        completed_level = self._last_adventure_level or int(game.get("level") or 0)
        self.phase = "waiting_reward_manual"
        self.last_error = ""
        self._reward_key = f"adventure:{completed_level:02d}:{flow.get('reward_flow', 'next_level')}"

    def _context_with_level_memory(self, observed: dict[str, Any]) -> tuple[str, str]:
        flow = observed.get("flow", {})
        game = observed.get("game", {})
        level_key = str(flow.get("level_key") or game.get("level_key") or "unknown")
        all_history = self._level_memories.get(level_key, [])
        history = all_history[-8:]
        rejected_targets: Counter[str] = Counter()
        for record in all_history:
            if record.get("status") != "rejected":
                continue
            arguments = record.get("arguments", {})
            if "row" in arguments and "col" in arguments:
                key = f"{record.get('action')}@[{arguments['row']},{arguments['col']}]"
                rejected_targets[key] += 1
        enriched = dict(observed)
        enriched["level_memory"] = {
            "level_key": level_key,
            "scope": "current_level_only",
            "recent_actions": history,
            "rejected_targets": dict(rejected_targets.most_common(8)),
        }
        return json.dumps(enriched, ensure_ascii=False, separators=(",", ":")), level_key

    def _sync_instruction_level(self, level_key: str) -> None:
        with self._instruction_condition:
            if self._instruction_level_key == level_key:
                return
            previous = self._instruction_level_key
            for record in self._active_instructions:
                record["state"] = "expired"
                record["updated_at"] = time.time()
                record["version"] = int(record.get("version") or 0) + 1
            self._active_instructions.clear()
            self._instruction_level_key = level_key
            self._instruction_condition.notify_all()
        if previous:
            self.plugin.emit(
                "agent", "user_instructions_cleared",
                previous_level_key=previous, level_key=level_key,
            )

    def _peek_pending_instruction(self) -> dict[str, Any] | None:
        with self._instruction_condition:
            return dict(self._pending_instructions[0]) if self._pending_instructions else None

    def _active_instruction_payload(self) -> list[dict[str, str]]:
        with self._instruction_condition:
            return [
                {"id": str(item["id"]), "text": str(item["text"])}
                for item in self._active_instructions
            ]

    def _update_instruction(self, instruction_id: str, **changes: Any) -> None:
        with self._instruction_condition:
            record = self._instruction_records.get(instruction_id)
            if not record:
                return
            record.update(changes)
            record["updated_at"] = time.time()
            record["version"] = int(record.get("version") or 0) + 1
            self._instruction_condition.notify_all()

    def _complete_instruction(
        self,
        instruction_id: str,
        reply: str,
        action: str,
        arguments: dict[str, Any],
        level_key: str,
    ) -> None:
        fallback = "已读取，将在本局中持续遵守。"
        concise_reply = (reply or fallback).strip()[:120]
        with self._instruction_condition:
            record = self._instruction_records.get(instruction_id)
            if not record:
                return
            if self._pending_instructions and self._pending_instructions[0].get("id") == instruction_id:
                self._pending_instructions.popleft()
            else:
                self._pending_instructions = deque(
                    item for item in self._pending_instructions if item.get("id") != instruction_id
                )
            record.update({
                "state": "active",
                "reply": concise_reply,
                "action": {"action": action, "arguments": dict(arguments)},
                "level_key": level_key,
                "updated_at": time.time(),
                "version": int(record.get("version") or 0) + 1,
            })
            self._active_instructions.append(record)
            self._instruction_condition.notify_all()
        self.plugin.emit(
            "agent", "user_instruction_completed",
            instruction_id=instruction_id, reply=record["reply"],
            action=action, arguments=arguments, level_key=level_key,
        )

    def _fail_instruction(self, instruction_id: str, error: str) -> None:
        with self._instruction_condition:
            if self._pending_instructions and self._pending_instructions[0].get("id") == instruction_id:
                self._pending_instructions.popleft()
            else:
                self._pending_instructions = deque(
                    item for item in self._pending_instructions if item.get("id") != instruction_id
                )
        self._update_instruction(instruction_id, state="failed", error=error[:500])
        self.plugin.emit(
            "error", "user_instruction_failed", level="error",
            instruction_id=instruction_id, error=error[:500],
        )

    def _fail_all_pending_instructions(self, error: str) -> None:
        with self._instruction_condition:
            pending_ids = [str(item.get("id")) for item in self._pending_instructions]
        for instruction_id in pending_ids:
            self._fail_instruction(instruction_id, error)

    def _remember_level_action(
        self,
        level_key: str,
        action: str,
        arguments: dict[str, Any],
        status: str,
        result: dict[str, Any],
    ) -> None:
        history = self._level_memories.setdefault(level_key, [])
        compact_result = {
            key: result[key]
            for key in (
                "status", "reason", "error", "plant", "plant_type", "row", "col",
                "removed", "remaining", "verification",
            )
            if key in result
        }
        history.append({
            "iteration": self.iteration,
            "action": action,
            "arguments": arguments,
            "status": status,
            "result": compact_result,
        })
        if len(history) > 32:
            del history[:-32]

    @staticmethod
    def _strategy_snapshot(observed: dict[str, Any]) -> str:
        """Turn the exact board into a short, model-friendly composition audit."""
        plants: list[dict[str, Any]] = []
        for cell in observed.get("occupied_cells", []):
            for layer in cell.get("layers", {}).values():
                for plant in layer:
                    item = dict(plant)
                    item["row"] = cell.get("row")
                    item["col"] = cell.get("col")
                    plants.append(item)
        counts = Counter(str(p.get("name") or p.get("type")) for p in plants)
        count_text = "、".join(f"{name}×{count}" for name, count in counts.most_common()) or "无植物"
        rows = int(observed.get("game", {}).get("board_rows") or 5)
        lane_text = []
        for row in range(rows):
            names = [str(p.get("name")) for p in plants if p.get("row") == row]
            lane_text.append(f"{row}:{'/'.join(names) if names else '空'}")
        lines = [f"【本回合阵型审计】总量：{count_text}。逐路：{'；'.join(lane_text)}。"]
        threats = {int(item.get("row", -1)): item for item in observed.get("lane_threats", [])}
        urgent_rows = sorted(
            row for row, item in threats.items()
            if int(item.get("eating_count") or 0) > 0
            or (item.get("nearest_col") is not None and float(item["nearest_col"]) <= 3)
        )
        if urgent_rows:
            lines.append(f"紧急路线：{urgent_rows}；这些路线先救线，禁止铲植物或清墓碑。")

        sustained_types = {1, 5, 7, 10, 13, 18, 21, 22, 29, 32, 34, 36, 39, 40, 41, 42, 44, 45, 47}
        sustained_by_row = {
            row: sum(1 for p in plants if p.get("row") == row and int(p.get("type", -1)) in sustained_types)
            for row in range(rows)
        }
        active_rows = {
            row for row, item in threats.items() if int(item.get("count") or 0) > 0
        }
        uncovered = sorted(row for row in active_rows if sustained_by_row.get(row, 0) == 0)
        if uncovered:
            lines.append(f"缺持续输出的来怪路线：{uncovered}；优先补这些路，而不是继续堆已有火力的路线。")

        economy_types = {0, 9}
        economy_count = sum(1 for p in plants if int(p.get("type", -1)) in economy_types)
        wave = int(observed.get("game", {}).get("wave") or 0)
        total_wave = max(1, int(observed.get("game", {}).get("total_wave") or 1))
        if not urgent_rows and wave <= total_wave // 2 and economy_count < 5:
            lines.append(f"经济植物仅{economy_count}株：在不耽误缺火力路线的前提下补到约5株。")
        if counts:
            dominant_name, dominant_count = counts.most_common(1)[0]
            if dominant_count >= 6 and dominant_count * 2 > len(plants):
                lines.append(f"{dominant_name}占比过高（{dominant_count}/{len(plants)}）：本轮优先补缺失职责，不再无目标重复。")

        cards = observed.get("cards", [])
        ready_replacements = [
            f"{card.get('index')}:{card.get('name')}"
            for card in cards
            if card.get("ready") and int(card.get("actual_type", -1)) in sustained_types
        ]
        shovel_candidates = []
        for plant in plants:
            row = int(plant.get("row", -1))
            if (
                int(plant.get("type", -1)) == 8
                and row not in urgent_rows
                and sustained_by_row.get(row, 0) > 0
            ):
                shovel_candidates.append([row, int(plant.get("col", -1))])
        if shovel_candidates and ready_replacements:
            lines.append(
                f"可计划替换：小喷菇{shovel_candidates[:3]}；可用持续火力卡{ready_replacements[:3]}。"
                "仅在阳光与卡片已就绪时铲一个，并在下一回合把同格换上。"
            )

        recent_actions = observed.get("level_memory", {}).get("recent_actions", [])
        if recent_actions:
            last = recent_actions[-1]
            arguments = last.get("arguments", {})
            if last.get("action") == "shovel" and last.get("status") not in {"rejected", "failure"}:
                replacement_index = arguments.get("replacement_card_index")
                target = [arguments.get("row"), arguments.get("col")]
                replacement = next((c for c in cards if c.get("index") == replacement_index), None)
                if replacement and target in replacement.get("legal_targets", []) and replacement.get("ready"):
                    lines.append(
                        f"上一轮已为替换铲除{target}：现在优先用卡{replacement_index}:{replacement.get('name')}种回同格，"
                        "不要留下防线空洞。"
                    )

        graves = observed.get("decision_board", {}).get("grave_cells", [])
        grave_buster = next(
            (card for card in cards if int(card.get("actual_type", -1)) == 11), None
        )
        if graves and grave_buster:
            safe_graves = [cell for cell in graves if int(cell[0]) not in urgent_rows]
            if safe_graves and grave_buster.get("ready"):
                ordered = sorted(safe_graves, key=lambda cell: (cell[1], cell[0]))
                lines.append(f"安全清墓碑候选：{ordered[:3]}（越靠左越优先）；本轮至多清一座。")
        puff = counts.get("小喷菇", 0)
        sun_shroom = counts.get("阳光菇", 0)
        sustained = sum(sustained_by_row.values())
        level = int(observed.get("game", {}).get("level") or 0)
        if 11 <= level <= 19:
            lines.append(
                f"夜间结构：过渡小喷菇{puff}、阳光菇{sun_shroom}、持续远程火力{sustained}。"
                "小喷菇只负责开局补漏和近距离拖延；已有基本逐路掩护后，禁止因为零费而无目标铺满。"
                "阳光允许时优先补后排阳光菇、各路大喷菇/已解锁持续火力和前排阻挡。"
            )
            if puff >= 6 and puff > sun_shroom + sustained:
                lines.append("当前小喷菇明显过量：除非某路正要漏怪，本轮不要再种小喷菇，必须转经济、持续火力、防御或安全清墓碑。")
        return "\n".join(lines)

    def _request_action(
        self,
        context: str,
        feedback: dict[str, Any] | None = None,
        pending_instruction: dict[str, Any] | None = None,
        active_instructions: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        try:
            observed = json.loads(context)
        except (TypeError, ValueError, json.JSONDecodeError):
            observed = {}
        selection_turn = observed.get("flow", {}).get("phase") == "seed_selection"
        game = observed.get("game", {})
        flow = observed.get("flow", {})
        system = (
            "You are a Plants vs. Zombies 1 strategy agent. You receive exact structured memory state. "
            "Use exactly one pvz_action per turn. Never request screenshots, Windows mouse coordinates, keyboard input, or prose. "
            "Cards, rows and columns are zero-based; col=0 is nearest the house and col=8 is nearest zombie spawn. "
            "The plugin waits locally for sun/cooldown. "
            "Stacking is supported: lily pad/flower pot are support, pumpkin is armor, coffee bean wakes a sleeping mushroom. "
            "Upgrade cards must target their base plant; cob cannon targets the left of two adjacent kernel-pults. "
            "Use fire_cob for an existing cob cannon. Call select_seeds only when flow.phase is seed_selection. "
            "The plugin has no preset deck and never fills or replaces seed choices. Choose the loadout yourself from "
            "seed_selection.available_plants, select exactly "
            "seed_selection.required_count distinct useful plants, and call select_seeds once. "
            "When flow.seed_system is conveyor, never call select_seeds; use the currently available card indexes and place_plant. "
            "Conveyor cards do not consume sun; ignore their normal sun_cost. Only cards present in the cards array are valid—"
            "never guess or use a missing/empty card index. "
            "Automatic collection is humanized and delayed; do not call collect_items while it is enabled. "
            "occupied_cells is authoritative. Never place a main-layer plant on an occupied main layer. "
            "For every place_plant, the [row,col] MUST appear in that exact card's legal_targets; an empty legal_targets means do not use it. "
            "If previous_action_feedback reports a rejected action, "
            "do not repeat that action at the same coordinates."
        )
        if selection_turn and self.strategy_assist_enabled:
            system += (
                " THIS TURN IS ONLY FOR AI SEED SELECTION. Analyze the full spawn_plan, terrain, scene, costs and exact slot count. "
                "Build a functional deck with economy, sustained damage, required terrain support, threat counters and emergency control. "
                "Do not mechanically choose the first N plant IDs, and do not copy a prior deck unless the current level evidence justifies it. "
                "Return action=select_seeds and include a concise selection_reason proving why these exact cards fit this level."
            )
        if self.strategy_assist_enabled:
            system += (
                " Use decision_board.shovel_targets for shovel. Shovel only with a ready, affordable replacement card; "
                "include replacement_card_index, then prioritize planting that card at the same cell next turn if still legal. "
                "Include a short decision_reason for place_plant or shovel."
            )
            level_strategy = ""
            if game.get("mode") == 0:
                level = int(game.get("level") or 0)
                if 1 <= level <= 50:
                    level_strategy = get_adventure_script(level).strategy
            system += "\n\n" + build_strategy_assist_prompt(
                str(flow.get("level_label") or game.get("level_label") or "当前关卡"),
                level_strategy,
            )
            if not selection_turn:
                system += "\n\n" + self._strategy_snapshot(observed)
        if active_instructions or pending_instruction:
            system += (
                " Current-match user instructions are binding strategy constraints when legal and safe. "
                "They never override legal_targets, available cards, game phase, resource checks, or tool schema. "
                "Use the same single pvz_action tool call for both the game decision and any user acknowledgement. "
                "When new_user_instruction is present, read it fully once and include one concise Chinese user_reply (120 characters maximum) in the same tool call. "
                "Do not make a separate conversational response or a second model request. Existing match instructions remain active, "
                "but only the new instruction needs user_reply."
            )
        user_content = f"goal: {self._goal}\n<game_state>\n{context}\n</game_state>"
        if active_instructions:
            user_content += "\n<current_match_user_instructions>\n" + json.dumps(
                active_instructions, ensure_ascii=False, separators=(",", ":")
            ) + "\n</current_match_user_instructions>"
        if pending_instruction:
            user_content += "\n<new_user_instruction>\n" + json.dumps(
                {"id": pending_instruction.get("id"), "text": pending_instruction.get("text")},
                ensure_ascii=False, separators=(",", ":"),
            ) + "\n</new_user_instruction>"
        if feedback:
            user_content += "\n<previous_action_feedback>\n" + json.dumps(
                feedback, ensure_ascii=False, separators=(",", ":")
            ) + "\n</previous_action_feedback>"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
            "tools": [PVZ_TOOL],
            "tool_choice": "required",
            "max_tokens": 384 if selection_turn or pending_instruction else 256,
            "temperature": 0.2,
        }
        if uses_deepseek_extensions(self.base_url):
            payload["thinking"] = {"type": "disabled"}
        request_started = time.perf_counter()
        self.plugin.emit(
            "model", "request", iteration=self.iteration, model=self.model,
            context_chars=len(context), feedback=feedback, goal=self._goal,
            tool_choice="required",
            thinking="disabled" if "thinking" in payload else "provider_default",
            instruction_id=pending_instruction.get("id") if pending_instruction else None,
        )
        try:
            reply = self._client.post(f"{self.base_url}/chat/completions", json=payload)
            reply.raise_for_status()
            response = reply.json()
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            self.plugin.emit(
                "model", "transport_error", level="error", iteration=self.iteration,
                error_type=type(exc).__name__, error=str(exc),
                latency_ms=round((time.perf_counter() - request_started) * 1000, 2),
            )
            self._reset_client()
            raise RuntimeError(f"模型服务网络请求失败: {exc}") from exc
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[-1000:]
            self.plugin.emit(
                "model", "http_error", level="error", iteration=self.iteration,
                status_code=exc.response.status_code, response=detail,
                latency_ms=round((time.perf_counter() - request_started) * 1000, 2),
            )
            raise RuntimeError(f"DeepSeek HTTP {exc.response.status_code}: {detail}") from exc
        except (ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"DeepSeek 返回了无效 JSON: {exc}") from exc
        self.plugin.emit(
            "model", "response", iteration=self.iteration,
            latency_ms=round((time.perf_counter() - request_started) * 1000, 2),
            response_id=response.get("id"), usage=response.get("usage"),
            finish_reason=(response.get("choices") or [{}])[0].get("finish_reason"),
            response=response,
        )
        if response.get("error"):
            raise RuntimeError(str(response["error"]))
        calls = response["choices"][0]["message"].get("tool_calls") or []
        if not calls:
            raise RuntimeError("模型没有返回工具调用")
        raw = calls[0]["function"].get("arguments") or "{}"
        args = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(args.get("arguments"), dict) and len(args) == 1:
            args = args["arguments"]
        if "action" not in args:
            raise RuntimeError(f"工具参数缺少 action: {args}")
        self.plugin.emit("model", "tool_call_parsed", iteration=self.iteration, arguments=args)
        return args

    def close(self) -> None:
        self.stop()
        self._client.close()

    def _new_client(self) -> httpx.Client:
        timeout = httpx.Timeout(connect=10.0, read=45.0, write=10.0, pool=5.0)
        limits = httpx.Limits(max_connections=2, max_keepalive_connections=1, keepalive_expiry=60.0)
        return httpx.Client(
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            timeout=timeout, limits=limits,
        )

    def _reset_client(self) -> None:
        try:
            self._client.close()
        finally:
            self._client = self._new_client()

