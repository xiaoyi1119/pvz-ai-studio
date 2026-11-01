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
from .pvz_native.offsets import PLANT_NAMES, PLANT_SUN_COST, PLANT_UPGRADE_MAP
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

        flow = classify_flow(
            app_scene=state.game_ui,
            game_mode=state.game_mode,
            adventure_level=state.adventure_level,
            background=state.scene,
            seed_chooser_ready=state.seed_chooser_ready,
        ).to_dict()
        conveyor = flow["seed_system"] == "conveyor"

        cards = []
        for seed in state.seeds:
            actual_type = seed.imitator_type if seed.imitator_type >= 0 else seed.plant_type
            # Conveyor banks keep unused array slots at type -1.  They are not
            # cards and must never be exposed to the model as actionable slots.
            if actual_type not in range(48):
                continue
            cards.append({
                "index": seed.index, "type": seed.plant_type, "actual_type": actual_type,
                "name": seed.name, "cost": seed.sun_cost,
                "refresh_counter_cs": seed.cd,
                "cooldown_cs": seed.cooldown_remaining,
                "cooldown_seconds": round(seed.cooldown_remaining / 100, 2),
                "cooldown": round(seed.cooldown_remaining / 100, 2),
                "initial_cooldown_cs": seed.initial_cd, "cooldown_progress": round(seed.cd_progress, 3),
                "usable_flag": seed.is_usable, "refreshing_flag": seed.is_refreshing,
                "ready": seed.is_ready and (conveyor or state.sun >= seed.sun_cost),
                "affordable": conveyor or state.sun >= seed.sun_cost,
                "requires_sun": not conveyor, "imitator_type": seed.imitator_type,
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

        # Give the model the same conservative legality result used by the
        # executor.  This prevents it from repeatedly cross-joining graves,
        # occupied layers and cards from a large raw state document.
        for card in cards:
            plant_type = int(card["actual_type"])
            card["legal_targets"] = [
                [row, col]
                for row in range(board_rows)
                for col in range(9)
                if check_placement(
                    plant_type, row, col, state.plants, state.grid_items
                ).allowed
            ]

        board = build_board(state.plants)
        grave_cells = [
            [item.row, item.col] for item in state.grid_items
            if item.item_type == 1 and item.row in range(board_rows)
        ]
        shovel_targets = [
            {
                "cell": [cell["row"], cell["col"]],
                "plants": [
                    plant["name"]
                    for layer in cell["layers"].values()
                    for plant in layer
                ],
            }
            for cell in board
            if cell["row"] in range(board_rows) and not cell["empty"]
        ]
        decision_board = {
            "coordinate_format": "[row,col]",
            "grave_cells": grave_cells,
            "normal_main_open_cells": [
                [row, col]
                for row in range(board_rows)
                for col in range(9)
                if check_placement(0, row, col, state.plants, state.grid_items).allowed
            ],
            "shovel_targets": shovel_targets,
        }

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
        available_seed_types = (
            available_adventure_seed_types(state.adventure_level)
            if state.game_mode == 0 and flow["seed_system"] == "chooser" else []
        )
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
                "available_types": available_seed_types,
                "available_plants": [
                    {
                        "type": plant_type,
                        "name": PLANT_NAMES.get(plant_type, str(plant_type)),
                        "cost": PLANT_SUN_COST.get(plant_type, 0),
                    }
                    for plant_type in available_seed_types
                ],
                "decision_owner": "ai",
                "plugin_policy": "validate_and_execute_only_no_presets_no_random_fill",
                "ui_visible": state.seed_chooser_visible,
                "ui_age": state.seed_chooser_age,
            },
            "cards": cards, "plants": plants, "zombies": zombies,
            "items": items, "grid_items": grid_items, "lawn_mowers": mowers,
            "board": board, "decision_board": decision_board,
            "lane_threats": lane_threats,
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
        # Strategy prose is injected by DeepSeekAgent only when the optional
        # assist switch is enabled.  Never leak it through the ordinary model
        # state, otherwise "off" would be cosmetic.
        model_flow = {key: value for key, value in state["flow"].items() if key != "strategy"}
        occupied_cells = []
        for cell in state["board"]:
            if cell["empty"]:
                continue
            occupied_cells.append({
                "row": cell["row"], "col": cell["col"],
                "layers": {
                    layer: [
                        {
                            "type": plant["type"], "name": plant["name"],
                            "hp_ratio": plant["hp_ratio"],
                            "sleeping": plant["sleeping"],
                        }
                        for plant in values
                    ]
                    for layer, values in cell["layers"].items() if values
                },
            })
        compact = {
            "game": state["game"], "flow": model_flow, "cards": state["cards"],
            "decision_board": state.get("decision_board", {}),
            "occupied_cells": occupied_cells,
            "zombies": [
                {
                    key: zombie[key] for key in (
                        "name", "row", "x", "estimated_col", "hp_total",
                        "eating", "slowed_for", "frozen_for",
                    )
                }
                for zombie in state["zombies"]
            ],
            "grid_items": state["grid_items"],
            "lawn_mowers": state["lawn_mowers"],
            "lane_threats": state["lane_threats"],
            "terrain_rows": state["terrain"].get("rows", []),
        }
        if model_flow.get("phase") == "seed_selection":
            compact["spawn_plan"] = state["spawn_plan"]
            compact["seed_selection"] = state["seed_selection"]
            compact["terrain"] = state["terrain"]
        else:
            current_wave = max(0, int(state["game"].get("wave") or 0))
            future = state["spawn_plan"][current_wave:]
            remaining_names = [name for wave in future for name in wave["names"]]
            compact["remaining_spawn_summary"] = {
                name: remaining_names.count(name) for name in sorted(set(remaining_names))
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
                if plant_type not in range(48):
                    raise ValueError(f"卡槽 {card_index} 当前为空，不能执行种植")
                flow = classify_flow(
                    app_scene=state.game_ui,
                    game_mode=state.game_mode,
                    adventure_level=state.adventure_level,
                    background=state.scene,
                    seed_chooser_ready=state.seed_chooser_ready,
                )
                conveyor = flow.seed_system == "conveyor"
                check = check_placement(
                    plant_type, row, col, state.plants, state.grid_items
                )
                if not check.allowed:
                    raise ValueError(check.reason)
                if seed.is_ready and (conveyor or state.sun >= seed.sun_cost):
                    before_types = [
                        p.plant_type for p in state.plants
                        if p.row == row and p.col == col
                    ]
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
                requirement = "传送带卡片" if conveyor else f"sun={state.sun}/{seed.sun_cost}"
                raise TimeoutError(
                    f"等待 {seed.name} 可用超时 "
                    f"(active={seed.is_usable}, remaining={seed.cooldown_remaining}, {requirement})"
                )
            if not waiting_logged:
                self.emit("action", "waiting_resources", action_id=action_id, card_index=card_index,
                          plant=seed.name, cooldown_cs=seed.cooldown_remaining, sun=state.sun,
                          required_sun=0 if conveyor else seed.sun_cost, conveyor=conveyor)
                waiting_logged = True
            time.sleep(0.1)

        time.sleep(0.35)
        assert self._reader
        after_state = self._reader.read_state()
        after = [p for p in after_state.plants if p.row == row and p.col == col]
        accepted = self._verify_placement(plant_type, row, col, before_types, after)
        if card_index < len(after_state.seeds):
            after_card_type = after_state.seeds[card_index].plant_type
            if conveyor:
                accepted = accepted or after_card_type != seed.plant_type
            else:
                accepted = accepted or after_state.seeds[card_index].is_refreshing
            if not conveyor and seed.sun_cost > 0:
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
            after = [p for p in after_state.plants if p.row == row and p.col == col]
            accepted = self._verify_placement(plant_type, row, col, before_types, after)
            if card_index < len(after_state.seeds):
                after_card_type = after_state.seeds[card_index].plant_type
                if conveyor:
                    accepted = accepted or after_card_type != seed.plant_type
                else:
                    accepted = accepted or after_state.seeds[card_index].is_refreshing
                if not conveyor and seed.sun_cost > 0:
                    accepted = accepted or after_state.sun <= state.sun - seed.sun_cost
        if not accepted:
            if conveyor:
                raise RuntimeError(
                    f"传送带没有接受种植动作: {seed.name} → ({row},{col})；"
                    "为防止空槽或卡槽移动造成崩溃，已禁用强制内存种植回退"
                )
            # Never call PutPlant as a fallback here.  It runs an internal game
            # routine in a remote thread.  On the localized 1.0.0.1051 build the
            # routine can wait on the main loop, hit the injector timeout and
            # terminate the whole game process.  A rejected background input is
            # recoverable; a crashed game is not.  Let the Agent observe again
            # and choose another legal action/target instead.
            self.emit(
                "action", "plant_input_not_accepted", level="warning",
                action_id=action_id, plant=seed.name, row=row, col=col,
                safety="native_memory_fallback_disabled",
            )
            raise RuntimeError(
                f"游戏未接受种植操作: {seed.name} → ({row},{col})；"
                "已禁用可导致闪退的强制内存种植，请重新读取状态后换一个合法动作"
            )
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
                seed_chooser_ready=state.seed_chooser_ready,
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
            if not state.seed_chooser_ready:
                raise ValueError("选卡界面尚未完全出现或不可操作，请稍后再试")
            required_count = len(state.seeds)
            if not required_count:
                raise ValueError("暂时读不到本关卡槽数量，插件不会代选或随机补卡")
            if len(seeds) != required_count:
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
