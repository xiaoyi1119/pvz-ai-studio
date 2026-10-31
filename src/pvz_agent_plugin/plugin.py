from __future__ import annotations

import json
import random
import threading
import time
from pathlib import Path
from typing import Any

from .audit import AuditLog
from .flow import classify_flow
from .level_scripts import available_adventure_seed_types, get_adventure_script
from .pvz_native import PvZCodeInjector, PvZMemory, PvZStateReader
from .pvz_native.offsets import PLANT_NAMES, PLANT_UPGRADE_MAP
from .rules import build_board, check_placement, plant_layer


class PvZPlugin:
    """PvZ 1.0 semantic plugin: memory observation plus in-game function calls."""

    schema_version = "1.0"

    def __init__(self, log_dir: str | Path = "logs") -> None:
        self._lock = threading.RLock()
        self._memory: PvZMemory | None = None
        self._reader: PvZStateReader | None = None
        self._injector: PvZCodeInjector | None = None
        self._audit = AuditLog(log_dir)
        self._auto_collect = False
        self._action_seq = 0
        # game_ui stays at SELECT_CARD for part of the level-intro camera pan.
        # Remember a successful submission so repeated Agent turns cannot call
        # ChooseCard/Rock again and restart that transition over and over.
        self._selection_submitted = False
        self._collector_stop = threading.Event()
        self._collector_thread: threading.Thread | None = None

    @property
    def connected(self) -> bool:
        return bool(self._memory and self._memory.is_connected() and self._reader and self._injector)

    def connect(self) -> dict[str, Any]:
        started = time.perf_counter()
        with self._lock:
            if self.connected:
                return self.status()
            # Dispose stale handles before attaching to a restarted game process.
            if self._injector:
                self._injector.close()
            if self._memory:
                self._memory.disconnect()
            self._memory = self._reader = self._injector = None
            memory = PvZMemory()
            if not memory.connect():
                self.emit("connection", "connect_failed", level="error", reason="process_not_found")
                raise RuntimeError("没有找到受支持的 Plants vs. Zombies 进程")
            injector = PvZCodeInjector(memory)
            reader = PvZStateReader(memory)
            self._memory, self._injector, self._reader = memory, injector, reader
            self._selection_submitted = False
            try:
                # Preserve a user pause. Writing only the game's pause flag does not
                # close the visible pause dialog and can leave the lawn animating
                # behind it.
                # Disable the instant-collect patch. A delayed collector below keeps
                # each item visible for a human-like amount of time.
                injector.set_auto_collect(False)
                self._auto_collect = True
            except Exception as exc:
                self.emit("connection", "startup_option_failed", level="warning", error=str(exc))
            self.emit(
                "connection", "connected", version=memory.version_name, pid=memory._pid,
                auto_collect=self._auto_collect, duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )
            self._start_collector()
            return self.status()

    def disconnect(self) -> None:
        self._collector_stop.set()
        collector = self._collector_thread
        if collector and collector.is_alive() and collector is not threading.current_thread():
            collector.join(timeout=1.5)
        with self._lock:
            if self._injector:
                self._injector.close()
            if self._memory:
                self._memory.disconnect()
            self._memory = self._reader = self._injector = None
            self._auto_collect = False
            self._selection_submitted = False
            self._collector_thread = None
            self.emit("connection", "disconnected")

    def status(self) -> dict[str, Any]:
        memory = self._memory
        return {
            "connected": self.connected,
            "version": memory.version_name if memory and memory.is_connected() else "",
            "pid": memory._pid if memory and memory.is_connected() else None,
            "auto_collect": self._auto_collect,
            "schema_version": self.schema_version,
            "session_id": self._audit.session_id,
        }

    def read_state(self, *, snapshot_reason: str | None = None) -> dict[str, Any]:
        started = time.perf_counter()
        with self._lock:
            self._require_connected()
            assert self._memory and self._reader and self._injector
            state = self._reader.read_state()
            if state.game_ui != 2:
                self._selection_submitted = False

        cards = []
        for seed in state.seeds:
            actual_type = seed.imitator_type if seed.imitator_type >= 0 else seed.plant_type
            cards.append({
                "index": seed.index, "type": seed.plant_type, "actual_type": actual_type,
                "name": seed.name, "cost": seed.sun_cost,
                "cooldown_cs": seed.cd, "cooldown_seconds": round(seed.cd / 100, 2),
                "cooldown": round(seed.cd / 100, 2),
                "initial_cooldown_cs": seed.initial_cd, "cooldown_progress": round(seed.cd_progress, 3),
                "usable_flag": seed.is_usable, "ready": seed.cd == 0 and state.sun >= seed.sun_cost,
                "affordable": state.sun >= seed.sun_cost, "imitator_type": seed.imitator_type,
                "ui_rect": {"x": seed.x, "y": seed.y, "width": seed.width, "height": seed.height},
            })

        plants = [{
            "index": p.index, "type": p.plant_type, "name": p.name,
            "row": p.row, "col": p.col, "layer": plant_layer(p.plant_type),
            "hp": p.hp, "hp_max": p.hp_max, "hp_ratio": round(p.hp_ratio, 3),
            "state": p.state, "sleeping": p.is_sleeping, "crushed": p.is_crushed,
            "shoot_countdown": p.shoot_countdown,
        } for p in state.plants]
        zombies = [{
            "index": z.index, "type": z.zombie_type, "name": z.name,
            "row": z.row, "x": round(z.abscissa, 2), "estimated_col": z.col_estimate,
            "speed": round(z.speed, 4), "hp_body": z.hp, "hp_armor_1": z.one_hp,
            "hp_armor_2": z.two_hp, "hp_total": z.total_hp, "hp": z.total_hp, "state": z.state,
            "eating": z.is_eat, "wave_spawned": z.at_wave,
            "slowed_for": z.slow_countdown, "buttered_for": z.fixation_countdown,
            "frozen_for": z.freeze_countdown, "hammering": z.is_hammering,
        } for z in state.zombies]
        items = [{
            "index": item.index, "type": item.item_type, "name": item.name,
            "x": round(item.x, 2), "y": round(item.y, 2), "is_sun": item.is_sun,
            "collected": item.is_collected,
        } for item in state.items]
        grid_items = [{
            "index": item.index, "type": item.item_type, "name": item.name,
            "row": item.row, "col": item.col, "value": item.value,
        } for item in state.grid_items]
        mowers = [{"index": mower.index, "row": mower.row, "alive": mower.is_alive} for mower in state.lawn_mowers]

        lane_threats = []
        board_rows = 6 if state.scene in (2, 3) else 5
        for row in range(board_rows):
            lane = [z for z in zombies if z["row"] == row]
            lane_threats.append({
                "row": row, "count": len(lane), "total_hp": sum(z["hp_total"] for z in lane),
                "nearest_x": min((z["x"] for z in lane), default=None),
                "nearest_col": min((z["estimated_col"] for z in lane), default=None),
                "eating_count": sum(1 for z in lane if z["eating"]),
                "types": sorted({z["name"] for z in lane}),
            })

        terrain_names = {0: "none", 1: "land", 2: "water", 3: "roof"}
        terrain = {
            "rows": [
                {"row": row, "type": value, "name": terrain_names.get(value, f"unknown:{value}")}
                for row, value in enumerate(state.row_types)
            ],
            "grid": [
                [
                    {"type": value, "name": terrain_names.get(value, f"unknown:{value}")}
                    for value in row
                ]
                for row in state.grid_types
            ],
        }
        spawn_plan = [
            {
                "wave": index + 1,
                "types": wave,
                "names": [self._zombie_name(value) for value in wave],
                "counts": {
                    self._zombie_name(value): wave.count(value) for value in sorted(set(wave))
                },
            }
            for index, wave in enumerate(state.wave_zombies)
        ]
        flow = classify_flow(
            app_scene=state.game_ui,
            game_mode=state.game_mode,
            adventure_level=state.adventure_level,
            background=state.scene,
            seed_chooser_ready=state.seed_chooser_ready,
        ).to_dict()

        result = {
            "schema_version": self.schema_version, "captured_at": time.time(),
            "read_duration_ms": round((time.perf_counter() - started) * 1000, 2),
            "game": {
                "ui": state.game_ui, "mode": state.game_mode, "in_battle": state.in_battle,
                "app_scene_id": state.game_ui, "app_scene": flow["app_scene"],
                "level": state.adventure_level, "level_key": flow["level_key"],
                "level_label": flow["level_label"],
                "scene_id": state.scene, "scene": state.scene_name, "paused": state.is_paused,
                "clock": state.game_clock, "sun": state.sun,
                "wave": state.wave, "total_wave": state.total_wave,
                "refresh_countdown": state.refresh_countdown,
                "huge_wave_countdown": state.huge_wave_countdown,
                "level_end_countdown": state.level_end_countdown,
            },
            # Compatibility keys for the current basic frontend.
            "sun": state.sun, "wave": state.wave, "total_wave": state.total_wave,
            "scene": state.scene_name, "clock": state.game_clock, "paused": state.is_paused,
            "level": state.adventure_level, "level_label": flow["level_label"],
            "board_rows": board_rows, "flow": flow,
            "seed_selection": {
                "required_count": len(state.seeds) or 6,
                "available_types": (
                    available_adventure_seed_types(state.adventure_level)
                    if state.game_mode == 0 and flow["seed_system"] == "chooser" else []
                ),
                "decision_owner": "ai",
            },
            "cards": cards, "plants": plants, "zombies": zombies,
            "items": items, "grid_items": grid_items, "lawn_mowers": mowers,
            "board": build_board(state.plants), "lane_threats": lane_threats,
            "terrain": terrain, "spawn_plan": spawn_plan,
            "counts": {
                "cards": len(cards), "plants": len(plants), "zombies": len(zombies),
                "items": len(items), "grid_items": len(grid_items),
                "mowers_alive": sum(1 for m in mowers if m["alive"]),
            },
        }
        if snapshot_reason:
            self._audit.snapshot(result, reason=snapshot_reason)
        return result

    def context_text(self, *, snapshot: bool = True) -> str:
        state = self.read_state()
        compact = {
            "game": state["game"], "flow": state["flow"], "cards": state["cards"],
            "occupied_cells": [cell for cell in state["board"] if not cell["empty"]],
            "zombies": state["zombies"], "items": state["items"],
            "grid_items": state["grid_items"], "lawn_mowers": state["lawn_mowers"],
            "lane_threats": state["lane_threats"], "terrain": state["terrain"],
            "spawn_plan": state["spawn_plan"], "seed_selection": state["seed_selection"],
        }
        text = json.dumps(compact, ensure_ascii=False, separators=(",", ":"))
        if snapshot:
            self._audit.snapshot(compact, reason="agent_observation")
        return text

    def snapshot_context(self, context: str, *, reason: str) -> None:
        """Persist the compact model context without another memory read."""
        self._audit.snapshot(json.loads(context), reason=reason)

    def execute(self, action: str, _cancel_event: threading.Event | None = None, **args: Any) -> dict[str, Any]:
        self._action_seq += 1
        action_id = f"a{self._action_seq:06d}"
        started = time.perf_counter()
        self.emit("action", "requested", action_id=action_id, action=action, arguments=args)
        try:
            audited_actions = {"place_plant", "shovel", "fire_cob", "select_seeds"}
            if self.connected and action in audited_actions:
                before = self.read_state(snapshot_reason=f"before_action:{action_id}:{action}")
                flow = before["flow"]
                allowed_by_phase = {
                    "seed_selection": {"select_seeds"},
                    "battle": {"place_plant", "shovel", "fire_cob"},
                }
                if action not in allowed_by_phase.get(flow["phase"], set()):
                    raise ValueError(f"当前流程“{flow['phase']}”不允许执行 {action}: {flow['reason']}")
            if action == "place_plant":
                result = self.place_plant(int(args["card_index"]), int(args["row"]), int(args["col"]), action_id=action_id)
            elif action == "shovel":
                result = self.shovel(int(args["row"]), int(args["col"]), action_id=action_id)
            elif action == "fire_cob":
                result = self.fire_cob(int(args["row"]), int(args["col"]), int(args["target_row"]), int(args["target_col"]), action_id=action_id)
            elif action == "select_seeds":
                result = self.select_seeds([int(v) for v in args.get("seeds", [])], action_id=action_id)
            elif action == "collect_items":
                result = self.collect_items(action_id=action_id)
            elif action == "set_auto_collect":
                result = self.set_auto_collect(bool(args.get("enabled", True)), action_id=action_id)
            elif action == "wait":
                seconds = max(0.1, min(float(args.get("time", 1.0)), 10.0))
                interrupted = bool(_cancel_event and _cancel_event.wait(seconds))
                if not _cancel_event:
                    time.sleep(seconds)
                result = {
                    "status": "ok", "action": "wait", "seconds": seconds,
                    "interrupted": interrupted, "action_id": action_id,
                }
            elif action == "terminate":
                result = {"status": "ok", "action": "terminate", "result": args.get("status", "success"), "action_id": action_id}
            else:
                raise ValueError(f"不支持的动作: {action}")
            if self.connected and action in audited_actions:
                self.read_state(snapshot_reason=f"after_action:{action_id}:{action}")
            self.emit("action", "completed", action_id=action_id, action=action, result=result,
                      duration_ms=round((time.perf_counter() - started) * 1000, 2))
            return result
        except Exception as exc:
            self.emit("action", "failed", level="error", action_id=action_id, action=action,
                      arguments=args, error_type=type(exc).__name__, error=str(exc),
                      duration_ms=round((time.perf_counter() - started) * 1000, 2))
            raise

    def place_plant(self, card_index: int, row: int, col: int, *, action_id: str = "manual", timeout: float = 30.0) -> dict[str, Any]:
        self._validate_grid(row, col)
        deadline = time.monotonic() + timeout
        waiting_logged = False
        while True:
            with self._lock:
                self._require_connected()
                assert self._reader and self._injector
                state = self._reader.read_state()
                if card_index not in range(len(state.seeds)):
                    raise ValueError(f"卡片序号不存在: {card_index}")
                seed = state.seeds[card_index]
                plant_type = seed.imitator_type if seed.imitator_type >= 0 else seed.plant_type
                check = check_placement(plant_type, row, col, state.plants)
                if not check.allowed:
                    raise ValueError(check.reason)
                if seed.cd == 0 and state.sun >= seed.sun_cost:
                    before_types = [p.plant_type for p in state.plants if p.row == row and p.col in {col, col + 1}]
                    self.emit("action", "placement_precheck", action_id=action_id, card_index=card_index,
                              plant_type=plant_type, plant=seed.name, row=row, col=col, sun=state.sun,
                              cost=seed.sun_cost, check=check.as_dict(), before_types=before_types)
                    card_x = seed.x + seed.width // 2 if seed.x > 0 else 105 + seed.index * 51
                    card_y = seed.y + seed.height // 2 if seed.y > 0 else 35
                    grid_x, grid_y = self._injector.grid_to_pixel(row, col)
                    self._injector.release_mouse()
                    interaction = "window_drag"
                    try:
                        self._injector.window_drag(
                            card_x, card_y, grid_x, grid_y,
                            duration=random.uniform(0.38, 0.58),
                            steps=random.randint(14, 22),
                        )
                    except Exception as exc:
                        interaction = "native_click_fallback"
                        self.emit(
                            "action", "drag_transport_failed", level="warning",
                            action_id=action_id, error=str(exc),
                        )
                        self._injector.mouse_click(card_x, card_y)
                        time.sleep(random.uniform(0.08, 0.16))
                        self._injector.mouse_click(grid_x, grid_y)
                    break
            if time.monotonic() >= deadline:
                raise TimeoutError(f"等待 {seed.name} 冷却或阳光超时 (cd={seed.cd}, sun={state.sun}/{seed.sun_cost})")
            if not waiting_logged:
                self.emit("action", "waiting_resources", action_id=action_id, card_index=card_index,
                          plant=seed.name, cooldown_cs=seed.cd, sun=state.sun, required_sun=seed.sun_cost)
                waiting_logged = True
            time.sleep(0.1)

        time.sleep(0.35)
        assert self._reader
        after_state = self._reader.read_state()
        after = [p for p in after_state.plants if p.row == row and p.col in {col, col + 1}]
        accepted = self._verify_placement(plant_type, row, col, before_types, after)
        if card_index < len(after_state.seeds):
            accepted = accepted or after_state.seeds[card_index].cd > 0
            if seed.sun_cost > 0:
                accepted = accepted or after_state.sun <= state.sun - seed.sun_cost
        if not accepted and interaction == "window_drag":
            self.emit(
                "action", "drag_not_accepted_fallback", level="warning",
                action_id=action_id, plant=seed.name, row=row, col=col,
            )
            with self._lock:
                assert self._injector
                self._injector.release_mouse()
                self._injector.mouse_click(card_x, card_y)
                time.sleep(random.uniform(0.08, 0.16))
                self._injector.mouse_click(grid_x, grid_y)
            interaction = "native_click_fallback"
            time.sleep(0.45)
            after_state = self._reader.read_state()
            after = [p for p in after_state.plants if p.row == row and p.col in {col, col + 1}]
            accepted = self._verify_placement(plant_type, row, col, before_types, after)
            if card_index < len(after_state.seeds):
                accepted = accepted or after_state.seeds[card_index].cd > 0
                if seed.sun_cost > 0:
                    accepted = accepted or after_state.sun <= state.sun - seed.sun_cost
        if not accepted:
            # Some localized 1.0.0.1051 builds ignore background window mouse
            # messages even though memory state and coordinates are valid. Keep
            # human-like drag/click as the primary path, then use the game's own
            # planting routine as a resource-constrained reliability fallback.
            self.emit(
                "action", "native_plant_fallback", level="warning",
                action_id=action_id, plant=seed.name, row=row, col=col,
            )
            with self._lock:
                assert self._injector
                self._injector.put_plant(
                    row, col, plant_type,
                    imitater=seed.imitator_type >= 0,
                    sun_cost=seed.sun_cost,
                )
                self._injector.consume_seed_card(card_index, seed.initial_cd)
            interaction = "native_plant_fallback"
            time.sleep(0.2)
            after_state = self._reader.read_state()
            after = [p for p in after_state.plants if p.row == row and p.col in {col, col + 1}]
            accepted = self._verify_placement(plant_type, row, col, before_types, after)
        if not accepted:
            raise RuntimeError(f"游戏未接受种植动作: {seed.name} → ({row},{col})")
        return {
            "status": "ok", "action": "place_plant", "action_id": action_id,
            "plant": seed.name, "plant_type": plant_type, "row": row, "col": col,
            "layer": check.layer, "interaction": interaction,
            "verification": "memory_confirmed", "cell_plants": [p.name for p in after],
        }

    def shovel(self, row: int, col: int, *, action_id: str = "manual") -> dict[str, Any]:
        self._validate_grid(row, col)
        with self._lock:
            self._require_connected()
            assert self._reader and self._injector
            before = [p for p in self._reader.read_state().plants if p.row == row and p.col == col]
            if not before:
                raise ValueError(f"目标格 ({row},{col}) 没有植物")
            x, y = self._injector.grid_to_pixel(row, col)
            self._injector.shovel(x, y)
        time.sleep(0.2)
        assert self._reader
        after = [p for p in self._reader.read_state().plants if p.row == row and p.col == col]
        if len(after) >= len(before):
            raise RuntimeError(f"铲除未生效: ({row},{col})")
        return {"status": "ok", "action": "shovel", "action_id": action_id, "row": row, "col": col,
                "removed": [p.name for p in before if p.index not in {a.index for a in after}],
                "remaining": [p.name for p in after], "verification": "memory_confirmed"}

    def fire_cob(self, row: int, col: int, target_row: int, target_col: int, *, action_id: str = "manual") -> dict[str, Any]:
        self._validate_grid(row, col)
        self._validate_grid(target_row, target_col)
        with self._lock:
            self._require_connected()
            assert self._reader and self._injector
            cannons = [p for p in self._reader.read_state().plants if p.row == row and p.col == col and p.plant_type == 47]
            if not cannons:
                raise ValueError(f"({row},{col}) 不是玉米加农炮")
            x1, y1 = self._injector.grid_to_pixel(row, col)
            x2, y2 = self._injector.grid_to_pixel(target_row, target_col)
            self._injector.mouse_click(x1, y1)
            time.sleep(0.1)
            self._injector.mouse_click(x2, y2)
        return {"status": "ok", "action": "fire_cob", "action_id": action_id,
                "cannon": {"row": row, "col": col}, "target": {"row": target_row, "col": target_col}}

    def select_seeds(self, seeds: list[int], *, action_id: str = "manual") -> dict[str, Any]:
        if not seeds or len(seeds) > 10 or any(seed not in range(48) for seed in seeds):
            raise ValueError("seeds 必须是 1~10 个 0~47 的植物类型")
        with self._lock:
            self._require_connected()
            assert self._memory and self._reader and self._injector
            state = self._reader.read_state()
            flow = classify_flow(
                app_scene=state.game_ui,
                game_mode=state.game_mode,
                adventure_level=state.adventure_level,
                background=state.scene,
                seed_chooser_ready=bool(self._memory.get_select_card_ui_ptr()),
            )
            if state.is_paused:
                raise ValueError("游戏已暂停，请先在游戏内继续后再选卡")
            if flow.phase != "seed_selection":
                raise ValueError("当前不在选卡界面")
            if self._selection_submitted:
                self.emit("action", "seed_selection_already_submitted", level="warning", action_id=action_id)
                return {
                    "status": "waiting", "action": "select_seeds", "action_id": action_id,
                    "reason": "selection_already_submitted",
                }
            if not self._memory.get_select_card_ui_ptr():
                raise ValueError("选卡界面仍在加载，请稍后再试")
            required_count = len(state.seeds)
            if required_count and len(seeds) != required_count:
                raise ValueError(f"本关需要由 AI 选择 {required_count} 张不同植物卡，当前提交 {len(seeds)} 张")
            if len(set(seeds)) != len(seeds):
                raise ValueError("选卡不能包含重复植物")
            if state.game_mode == 0 and state.adventure_level > 0:
                available = set(available_adventure_seed_types(state.adventure_level))
                unavailable = [seed for seed in seeds if seed not in available]
                if unavailable:
                    raise ValueError(f"本关尚未解锁这些植物类型: {unavailable}")
            for seed in seeds:
                self._injector.choose_card(seed)
                self.emit("action", "seed_selected", action_id=action_id, plant_type=seed,
                          plant=PLANT_NAMES.get(seed, str(seed)))
                time.sleep(random.uniform(0.35, 0.65))
            # Old callers with no readable packet count retain the safe fill;
            # normal AI selection submits the exact bank size and stays fully
            # attributable to the model rather than random completion.
            if not required_count:
                self._injector.pick_random_seeds()
                time.sleep(random.uniform(0.45, 0.8))
            self._injector.rock()
            self._selection_submitted = True
        return {"status": "ok", "action": "select_seeds", "action_id": action_id,
                "requested_types": seeds, "requested_names": [PLANT_NAMES.get(v, str(v)) for v in seeds]}

    def advance_reward_flow(self, completed_level: int, step: int) -> dict[str, Any]:
        """Advance the award UI using the script for the completed stage.

        The primary award button is common to seed packets, notes and trophies.
        Almanac/store intros are modal and need an Escape on the following step;
        window messages avoid blocking a remote injected callback.
        """
        with self._lock:
            self._require_connected()
            assert self._injector
            script = get_adventure_script(completed_level)
            modal = script.reward_flow in {"almanac", "store_intro"}
            if step == 0:
                self._injector.window_click(402, 522)
                operation = "click_primary_award_button"
            elif step == 1 and modal:
                self._injector.window_escape()
                operation = "close_modal_followup"
            elif step >= 1 and script.reward_flow == "zen_tutorial":
                # The Zen tutorial is a separate non-planting mode.  A blind
                # sequence could sell or move plants, so transition into it but
                # deliberately stop the combat Agent there.
                operation = "zen_tutorial_handoff"
            else:
                self._injector.window_click(402, 522)
                operation = "retry_primary_award_button"
        result = {
            "status": "ok", "action": "advance_reward_flow",
            "completed_level": completed_level, "step": step,
            "reward_flow": script.reward_flow, "operation": operation,
        }
        self.emit("flow", "reward_step", **result)
        return result

    def collect_items(self, *, action_id: str = "manual") -> dict[str, Any]:
        with self._lock:
            self._require_connected()
            assert self._reader and self._injector
            items = self._reader.read_state().items
            count = self._injector.collect_all_sun([(round(i.x), round(i.y)) for i in items])
        return {"status": "ok", "action": "collect_items", "action_id": action_id, "attempted": count}

    def set_auto_collect(self, enabled: bool, *, action_id: str = "manual") -> dict[str, Any]:
        with self._lock:
            self._require_connected()
            assert self._injector
            # Keep the instant patch disabled; this flag controls our delayed queue.
            self._injector.set_auto_collect(False)
            self._auto_collect = enabled
        self.emit("collector", "toggled", action_id=action_id, enabled=enabled, mode="delayed_humanized")
        return {"status": "ok", "action": "set_auto_collect", "action_id": action_id,
                "enabled": enabled, "mode": "delayed_humanized", "minimum_delay_seconds": 1.0}

    def _start_collector(self) -> None:
        self._collector_stop.clear()
        if self._collector_thread and self._collector_thread.is_alive():
            return
        self._collector_thread = threading.Thread(
            target=self._collector_loop, name="pvz-delayed-collector", daemon=True,
        )
        self._collector_thread.start()
        self.emit("collector", "started", minimum_delay_seconds=1.0, jitter_seconds=0.25)

    def _collector_loop(self) -> None:
        pending: dict[tuple[int, int], dict[str, Any]] = {}
        handled_until: dict[tuple[int, int], float] = {}
        while not self._collector_stop.wait(0.2):
            if not self._auto_collect or not self.connected:
                pending.clear()
                continue
            now = time.monotonic()
            try:
                with self._lock:
                    if not self.connected or not self._reader or not self._injector:
                        continue
                    items = self._reader.read_items()
                live_keys: set[tuple[int, int]] = set()
                for item in items:
                    key = (item.index, item.item_type)
                    live_keys.add(key)
                    if handled_until.get(key, 0) > now:
                        continue
                    if key not in pending:
                        base_delay = 1.0 if item.is_sun else 1.25
                        delay = base_delay + random.uniform(0.0, 0.25 if item.is_sun else 0.5)
                        pending[key] = {
                            "seen": now, "due": now + delay, "delay": delay,
                            "name": item.name, "type": item.item_type,
                        }
                        self.emit(
                            "collector", "item_seen", item_index=item.index,
                            item_type=item.item_type, item=item.name,
                            scheduled_delay_seconds=round(delay, 3),
                        )
                    entry = pending[key]
                    if now >= entry["due"]:
                        with self._lock:
                            if self.connected and self._injector:
                                self._injector.collect_sun_at(round(item.x), round(item.y))
                        age = now - entry["seen"]
                        self.emit(
                            "collector", "item_collected", item_index=item.index,
                            item_type=item.item_type, item=item.name,
                            x=round(item.x, 1), y=round(item.y, 1), age_seconds=round(age, 3),
                        )
                        handled_until[key] = now + 2.0
                        pending.pop(key, None)
                        time.sleep(random.uniform(0.08, 0.18))
                for key in list(pending):
                    if key not in live_keys:
                        pending.pop(key, None)
                for key, expiry in list(handled_until.items()):
                    if expiry <= now:
                        handled_until.pop(key, None)
            except Exception as exc:
                self.emit("collector", "loop_error", level="warning", error=str(exc))
                time.sleep(0.25)

    def events(self, limit: int = 100, category: str | None = None) -> list[dict[str, Any]]:
        return self._audit.tail(limit, category)

    def emit(self, category: str, event: str, *, level: str = "info", **data: Any) -> dict[str, Any]:
        return self._audit.emit(category, event, level=level, **data)

    def _emit(self, kind: str, message: str, **data: Any) -> None:
        self.emit(kind, message, **data)

    def _require_connected(self) -> None:
        if not self.connected:
            raise RuntimeError("插件尚未连接游戏")

    @staticmethod
    def _validate_grid(row: int, col: int) -> None:
        if row not in range(6) or col not in range(9):
            raise ValueError("row 必须为 0~5，col 必须为 0~8")

    @staticmethod
    def _verify_placement(plant_type: int, row: int, col: int, before_types: list[int], after: list[Any]) -> bool:
        after_types = [p.plant_type for p in after]
        if plant_type == 35:
            return any(p.row == row and p.col == col and not p.is_sleeping for p in after)
        if plant_type in PLANT_UPGRADE_MAP:
            return plant_type in after_types
        return after_types.count(plant_type) > before_types.count(plant_type)

    @staticmethod
    def _zombie_name(zombie_type: int) -> str:
        from .pvz_native.offsets import ZOMBIE_NAMES
        return ZOMBIE_NAMES.get(zombie_type, f"未知({zombie_type})")
