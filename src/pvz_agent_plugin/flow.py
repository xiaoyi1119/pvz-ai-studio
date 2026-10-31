from __future__ import annotations

from dataclasses import asdict, dataclass

from .level_scripts import get_adventure_script


APP_SCENE_NAMES = {
    0: "加载中",
    1: "主菜单",
    2: "关卡开场",
    3: "战斗中",
    4: "僵尸获胜",
    5: "奖励页面",
    6: "制作人员名单",
    7: "模式选择",
}

# Adventure levels whose mechanics are not ordinary semantic planting.
ADVENTURE_SPECIAL_LEVELS = {
    5: "坚果保龄球",
    15: "锤僵尸",
    35: "砸罐子",
}

# Conveyor levels that still use normal plants and grid placement.  These can
# be played by place_plant; they simply do not have a seed chooser.
ADVENTURE_CONVEYOR_LEVELS = {10, 20, 25, 30, 40, 45, 50}

# GameMode values follow the original 1.0.0.1051 enum.
MODE_NAMES = {
    0: "冒险模式",
    17: "坚果保龄球",
    18: "老虎机",
    19: "雨中种植物",
    20: "宝石迷阵",
    23: "僵尸水族馆",
    24: "宝石迷阵转转看",
    30: "锤僵尸",
    33: "坚果保龄球2",
    43: "禅境花园",
    46: "铲子谜题",
    49: "松鼠",
    50: "智慧树",
    71: "演示页面",
    72: "开场演示",
}

# These modes need clicks, matching, mallets, vase opening, zombie placement,
# garden tools, or other mechanics not represented by the semantic plant API.
NON_PLANTING_MODES = {
    17, 18, 19, 20, 23, 24, 30, 33, 43, 46, 49, 50,
    *range(51, 61),  # Vasebreaker / Scary Potter
    *range(61, 71),  # I, Zombie
    71, 72,
}

# Original Board::HasConveyorBeltSeedBank modes that still use grid planting.
CONVEYOR_MODES = {21, 25, 26, 27, 35, 47, 48}


@dataclass(frozen=True)
class FlowDecision:
    app_scene_id: int
    app_scene: str
    phase: str
    kind: str
    seed_system: str
    agent_enabled: bool
    reason: str
    level_key: str
    level_label: str
    mode_name: str
    board_rows: int
    strategy: str = ""
    reward_flow: str = "manual"

    def to_dict(self) -> dict:
        return asdict(self)


def _adventure_label(level: int) -> str:
    if level <= 0:
        return "冒险模式"
    world = (level - 1) // 10 + 1
    stage = (level - 1) % 10 + 1
    return f"冒险 {world}-{stage}"


def _level_identity(mode: int, level: int) -> tuple[str, str, str]:
    mode_name = MODE_NAMES.get(mode, f"模式 {mode}")
    if mode == 0:
        return f"adventure:{level:02d}", _adventure_label(level), mode_name
    return f"mode:{mode}", mode_name, mode_name


def classify_flow(
    *,
    app_scene: int,
    game_mode: int,
    adventure_level: int,
    background: int,
    seed_chooser_ready: bool,
) -> FlowDecision:
    """Classify the current PvZ screen before any model request is made."""
    scene_name = APP_SCENE_NAMES.get(app_scene, f"未知流程 {app_scene}")
    level_key, level_label, mode_name = _level_identity(game_mode, adventure_level)
    board_rows = 6 if background in (2, 3) else 5
    script = get_adventure_script(adventure_level) if game_mode == 0 else None
    strategy = script.strategy if script else ""
    reward_flow = script.reward_flow if script else "manual"

    if app_scene == 5:
        return FlowDecision(app_scene, scene_name, "reward", "manual", "none", False,
                            "奖励流程由本地逐关脚本处理，不请求模型", level_key,
                            level_label, mode_name, board_rows, strategy, reward_flow)
    if app_scene == 4:
        return FlowDecision(app_scene, scene_name, "lost", "manual", "none", False,
                            "失败页面不调用模型", level_key, level_label, mode_name, board_rows,
                            strategy, reward_flow)
    if app_scene not in (2, 3):
        return FlowDecision(app_scene, scene_name, "manual_transition", "manual", "none", False,
                            f"{scene_name}由玩家手动操作", level_key, level_label,
                            mode_name, board_rows, strategy, reward_flow)

    special_name = ADVENTURE_SPECIAL_LEVELS.get(adventure_level) if game_mode == 0 else None
    if special_name or game_mode in NON_PLANTING_MODES:
        name = special_name or mode_name
        return FlowDecision(app_scene, scene_name, "special_level", "special", "none", False,
                            f"{name}不是标准种植关，停止模型请求", level_key,
                            level_label, mode_name, board_rows, strategy, reward_flow)

    conveyor = (
        (game_mode == 0 and adventure_level in ADVENTURE_CONVEYOR_LEVELS)
        or game_mode in CONVEYOR_MODES
    )
    fixed_tutorial = bool(game_mode == 0 and script and script.seed_system == "fixed")
    seed_system = "conveyor" if conveyor else "fixed" if fixed_tutorial else "chooser"
    kind = "conveyor" if conveyor else "planting"

    if app_scene == 2:
        if not conveyor and not fixed_tutorial and seed_chooser_ready:
            return FlowDecision(app_scene, scene_name, "seed_selection", kind, seed_system, True,
                                "选卡界面已就绪", level_key, level_label, mode_name, board_rows,
                                strategy, reward_flow)
        return FlowDecision(app_scene, scene_name, "level_intro", kind, seed_system, False,
                            "等待关卡开场完成，不调用模型", level_key, level_label,
                            mode_name, board_rows, strategy, reward_flow)

    return FlowDecision(app_scene, scene_name, "battle", kind, seed_system, True,
                        "标准种植决策" if not conveyor else "传送带种植决策",
                        level_key, level_label, mode_name, board_rows, strategy, reward_flow)
