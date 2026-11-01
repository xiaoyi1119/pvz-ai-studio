from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .pvz_native.offsets import PLANT_NAMES, PLANT_UPGRADE_MAP


SUPPORT_TYPES = {16, 33}  # 荷叶、花盆
ARMOR_TYPES = {30}  # 南瓜头
CONSUMABLE_OVERLAY_TYPES = {35}  # 咖啡豆
MUSHROOM_TYPES = {8, 9, 10, 12, 13, 14, 15, 24, 31, 42}
AQUATIC_TYPES = {16, 19, 24}


@dataclass(frozen=True)
class PlacementCheck:
    allowed: bool
    reason: str
    layer: str
    requirements: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "layer": self.layer,
            "requirements": list(self.requirements),
        }


def plant_layer(plant_type: int) -> str:
    if plant_type in SUPPORT_TYPES:
        return "support"
    if plant_type in ARMOR_TYPES:
        return "armor"
    if plant_type in CONSUMABLE_OVERLAY_TYPES:
        return "consumable"
    if plant_type in PLANT_UPGRADE_MAP:
        return "upgrade"
    return "main"


def plants_at(plants: Iterable[Any], row: int, col: int) -> list[Any]:
    return [p for p in plants if p.row == row and p.col == col]


def check_placement(
    plant_type: int,
    row: int,
    col: int,
    plants: Iterable[Any],
    grid_items: Iterable[Any] = (),
) -> PlacementCheck:
    """Conservative PvZ stacking/upgrade validation.

    Final terrain and special-level legality is deliberately left to the game UI path.
    This check prevents obvious bad calls while allowing supported multi-layer cells.
    """
    cell = plants_at(plants, row, col)
    types = {p.plant_type for p in cell}
    layer = plant_layer(plant_type)
    name = PLANT_NAMES.get(plant_type, f"类型{plant_type}")
    graves = [
        item for item in grid_items
        if item.row == row and item.col == col and item.item_type == 1
    ]

    # A grave is not represented in the plant array.  Without this explicit
    # check, the native PutPlant fallback can bypass the game's UI rejection
    # and create an illegal plant/grave overlap.
    if plant_type == 11:
        if not graves:
            return PlacementCheck(
                False, "墓碑吞噬者必须种在真实墓碑格上", layer, ("grave_at_target",)
            )
        if cell:
            return PlacementCheck(False, "墓碑格已有植物，不能放墓碑吞噬者", layer)
        return PlacementCheck(True, "目标格存在墓碑，可执行清除", layer, ("grave_at_target",))
    if graves:
        return PlacementCheck(
            False, "目标格有墓碑；普通植物不能种在墓碑上，请改选空格或使用墓碑吞噬者",
            layer, ("no_grave_at_target",),
        )

    if plant_type in SUPPORT_TYPES:
        if types & SUPPORT_TYPES:
            return PlacementCheck(False, f"该格已有承载植物，不能再放{name}", layer)
        return PlacementCheck(True, "承载层可用；最终地形合法性由游戏判断", layer)

    if plant_type == 30:
        if 30 in types:
            return PlacementCheck(False, "该格已有南瓜头", layer)
        return PlacementCheck(True, "南瓜头可与承载层/主体植物叠放", layer)

    if plant_type == 35:
        sleepers = [p for p in cell if p.plant_type in MUSHROOM_TYPES and p.is_sleeping]
        if not sleepers:
            return PlacementCheck(False, "咖啡豆必须放在正在睡觉的蘑菇上", layer, ("sleeping_mushroom",))
        return PlacementCheck(True, f"将唤醒{sleepers[0].name}", layer, ("sleeping_mushroom",))

    base_type = PLANT_UPGRADE_MAP.get(plant_type)
    if base_type is not None:
        base_name = PLANT_NAMES.get(base_type, str(base_type))
        if plant_type == 47:
            left = base_type in types
            right = any(
                p.row == row and p.col == col + 1 and p.plant_type == base_type
                for p in plants
            )
            if not (left and right):
                return PlacementCheck(
                    False,
                    "玉米加农炮需要目标格和右侧格各有一个玉米投手",
                    layer,
                    ("kernel_pult_at_target", "kernel_pult_at_right"),
                )
            return PlacementCheck(True, "满足相邻双玉米投手合成条件", layer)
        if base_type not in types:
            return PlacementCheck(False, f"{name}必须种在{base_name}上", layer, (f"base_type:{base_type}",))
        return PlacementCheck(True, f"满足{base_name}升级条件", layer)

    main_types = types - SUPPORT_TYPES - ARMOR_TYPES
    if main_types:
        existing = PLANT_NAMES.get(next(iter(main_types)), str(next(iter(main_types))))
        return PlacementCheck(False, f"主体层已有{existing}", layer)
    return PlacementCheck(True, "主体层可用；最终地形合法性由游戏判断", layer)


def build_board(plants: Iterable[Any], rows: int = 6, cols: int = 9) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    plant_list = list(plants)
    for row in range(rows):
        for col in range(cols):
            cell = plants_at(plant_list, row, col)
            layers: dict[str, list[dict[str, Any]]] = {
                "support": [], "main": [], "armor": [], "upgrade": [], "consumable": []
            }
            for plant in cell:
                layer = plant_layer(plant.plant_type)
                layers[layer].append({
                    "index": plant.index,
                    "type": plant.plant_type,
                    "name": plant.name,
                    "hp": plant.hp,
                    "hp_max": plant.hp_max,
                    "hp_ratio": round(plant.hp_ratio, 3),
                    "state": plant.state,
                    "sleeping": plant.is_sleeping,
                    "crushed": plant.is_crushed,
                })
            result.append({"row": row, "col": col, "empty": not cell, "layers": layers})
    return result
