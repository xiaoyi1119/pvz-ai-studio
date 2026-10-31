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
                game = json.loads(context).get("game", {})
                if not game.get("in_battle") and game.get("ui") != 2:
                    self.phase = "waiting_for_manual_transition"
                    self.last_error = ""
                    self._stop.wait(0.5)
                    continue

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

    def _request_action(self, context: str, feedback: dict[str, Any] | None = None) -> dict[str, Any]:
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
            "Use fire_cob for an existing cob cannon and select_seeds only on the card selection screen. "
            "Automatic collection is humanized and delayed; do not call collect_items while it is enabled. "
            "occupied_cells is authoritative. Never place a main-layer plant on an occupied main layer. "
            "If previous_action_feedback reports a rejected action, do not repeat that action at the same coordinates."
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
            "max_tokens": 256,
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

