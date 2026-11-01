from __future__ import annotations

import json
import threading
import time
from typing import Any

import httpx

from .plugin import PvZPlugin


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
                "enabled": {"type": "boolean"},
                "time": {"type": "number", "minimum": 0.1, "maximum": 10},
                "status": {"type": "string", "enum": ["success", "failure"]},
            },
        },
    },
}


class DeepSeekAgent:
    def __init__(self, plugin: PvZPlugin, base_url: str, api_key: str, model: str) -> None:
        self.plugin = plugin
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
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
        return {
            "running": self.running, "goal": self._goal, "last_error": self.last_error,
            "model": self.model, "iteration": self.iteration, "last_action": self.last_action,
            "phase": self.phase, "system_failures": self.system_failures,
            "action_rejections": self.action_rejections,
        }

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
                    self._advance_reward_locally(game, flow)
                    self._stop.wait(0.35)
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

                self.iteration += 1
                self.plugin.emit("agent", "iteration_started", iteration=self.iteration, goal=self._goal)
                self.plugin.snapshot_context(context, reason="agent_observation")
                self.phase = "thinking"
                call = self._request_action(context, feedback=self._action_feedback)
                action = call.pop("action")
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
                    self._action_feedback = {
                        "status": "rejected",
                        "action": action,
                        "arguments": call,
                        "error": str(exc),
                        "instruction": "Do not repeat the same invalid action. Re-read occupied_cells and choose a legal alternative.",
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
                    self.phase = "recovering_action"
                    self._stop.wait(0.1)
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
        completed_level = self._last_adventure_level or int(game.get("level") or 0)
        key = f"adventure:{completed_level:02d}:{flow.get('reward_flow', 'next_level')}"
        now = time.monotonic()
        if self._reward_key != key:
            self._reward_key = key
            self._reward_step = 0
            # AwardScreen fades in for 180 ticks; wait before pressing its button.
            self._reward_next_at = now + 3.5
        self.phase = f"advancing_reward_{flow.get('reward_flow', 'next_level')}"
        if now < self._reward_next_at:
            return
        result = self.plugin.advance_reward_flow(completed_level, self._reward_step)
        self.last_action = {
            "action": "advance_reward_flow",
            "arguments": {"completed_level": completed_level, "step": self._reward_step},
            "result": result,
        }
        self._reward_step += 1
        # Modal Almanac/store screens need longer to finish opening before Esc.
        self._reward_next_at = now + (3.0 if self._reward_step == 1 else 2.0)

    def _context_with_level_memory(self, observed: dict[str, Any]) -> tuple[str, str]:
        flow = observed.get("flow", {})
        game = observed.get("game", {})
        level_key = str(flow.get("level_key") or game.get("level_key") or "unknown")
        history = self._level_memories.get(level_key, [])[-16:]
        enriched = dict(observed)
        enriched["level_memory"] = {
            "level_key": level_key,
            "scope": "current_level_only",
            "recent_actions": history,
        }
        return json.dumps(enriched, ensure_ascii=False, separators=(",", ":")), level_key

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
            for key in ("status", "reason", "error", "row", "col", "verification")
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

    def _request_action(self, context: str, feedback: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            observed = json.loads(context)
        except (TypeError, ValueError, json.JSONDecodeError):
            observed = {}
        selection_turn = observed.get("flow", {}).get("phase") == "seed_selection"
        system = (
            "You are a Plants vs. Zombies 1 strategy agent. You receive exact structured memory state. "
            "Use exactly one pvz_action per turn. Never request screenshots, Windows mouse coordinates, keyboard input, or prose. "
            "Cards, rows and columns are zero-based. COLUMN ORIENTATION IS CRITICAL: col=0 is the back line nearest the house/lawn mowers; "
            "col=8 is the front line nearest the zombie spawn. For normal ranged attackers and economy plants, prefer back columns 0-2. "
            "Never place pea shooters, sunflowers, repeaters, snow peas, kernel-pults, melon-pults or other persistent ranged/economy plants "
            "in columns 7-8 unless every safer rear cell in that row is unavailable and the placement is an explicit emergency. "
            "Use front columns mainly for disposable blockers, short-range plants, mines, squash, chompers, garlic or instant-use plants. "
            "Before every place_plant call, verify the selected column follows these orientation and role rules. "
            "The plugin waits locally for sun/cooldown. "
            "Stacking is supported: lily pad/flower pot are support, pumpkin is armor, coffee bean wakes a sleeping mushroom. "
            "Upgrade cards must target their base plant; cob cannon targets the left of two adjacent kernel-pults. "
            "Use fire_cob for an existing cob cannon. Call select_seeds only when flow.phase is seed_selection. "
            "Seed selection is part of your evaluation: the plugin has no preset deck and will never fill or replace your choices. "
            "You must choose the loadout yourself from seed_selection.available_plants, select exactly "
            "seed_selection.required_count distinct useful plants, and call select_seeds once. "
            "When flow.seed_system is conveyor, never call select_seeds; use the currently available card indexes and place_plant. "
            "Conveyor cards do not consume sun; ignore their normal sun_cost. Only cards present in the cards array are valid—"
            "never guess or use a missing/empty card index. "
            "Automatic collection is humanized and delayed; do not call collect_items while it is enabled. "
            "occupied_cells is authoritative. Never place a main-layer plant on an occupied main layer. "
            "If previous_action_feedback reports a rejected action, do not repeat that action at the same coordinates."
        )
        if selection_turn:
            system += (
                " THIS TURN IS ONLY FOR AI SEED SELECTION. Analyze the full spawn_plan, terrain, scene, costs and exact slot count. "
                "Build a functional deck with economy, sustained damage, required terrain support, threat counters and emergency control. "
                "Do not mechanically choose the first N plant IDs, and do not copy a prior deck unless the current level evidence justifies it. "
                "Return action=select_seeds and include a concise selection_reason proving why these exact cards fit this level."
            )
        user_content = f"goal: {self._goal}\n<game_state>\n{context}\n</game_state>"
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
            "thinking": {"type": "disabled"},
            "max_tokens": 384 if selection_turn else 256,
            "temperature": 0.2,
        }
        request_started = time.perf_counter()
        self.plugin.emit(
            "model", "request", iteration=self.iteration, model=self.model,
            context_chars=len(context), feedback=feedback, goal=self._goal,
            tool_choice="required", thinking="disabled",
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
            raise RuntimeError(f"DeepSeek 网络请求失败: {exc}") from exc
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

