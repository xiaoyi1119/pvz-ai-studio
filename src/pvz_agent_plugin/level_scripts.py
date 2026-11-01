from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class AdventureLevelScript:
    level: int
    mechanic: str
    seed_system: str
    strategy: str
    reward_flow: str = "next_level"

    def to_dict(self) -> dict:
        return asdict(self)


def _script(level: int, mechanic: str, seed_system: str, strategy: str,
            reward_flow: str = "next_level") -> AdventureLevelScript:
    return AdventureLevelScript(level, mechanic, seed_system, strategy, reward_flow)


# First-adventure scripts.  They intentionally describe every stage rather than
# guessing from the current screen: levels 1-7 use the fixed tutorial seed bank,
# while the game's chooser is only enabled from 1-8 onward.
ADVENTURE_LEVEL_SCRIPTS = {
    1: _script(1, "tutorial_one_lane", "fixed", "按教程在唯一开放草坪种植豌豆射手；不要尝试选卡"),
    2: _script(2, "tutorial_three_lanes", "fixed", "先建立向日葵经济，再用豌豆射手覆盖开放路线；不要尝试选卡"),
    3: _script(3, "tutorial_full_lawn", "fixed", "向日葵放后排，豌豆射手逐路补齐，樱桃炸弹只处理紧急密集僵尸"),
    4: _script(4, "tutorial_wallnut", "fixed", "后排经济和射手，坚果放前排拖延；这是固定卡组教程"),
    5: _script(5, "wallnut_bowling", "special", "坚果保龄球使用独立点击玩法，普通种植 Agent 停止"),
    6: _script(6, "tutorial_potato_mine", "fixed", "土豆雷争取启动经济时间，稳定后补豌豆与坚果"),
    7: _script(7, "tutorial_snow_pea", "fixed", "寒冰射手用于减速危险路线，保持后排向日葵"),
    8: _script(8, "day", "chooser", "首次自由选卡；经济、持续输出、阻挡和应急爆发保持均衡"),
    9: _script(9, "day", "chooser", "针对铁桶等高血量目标保留爆发和坚果，输出放后排"),
    10: _script(10, "day_miniboss", "conveyor", "传送带卡片及时消耗，优先补空路并把防御植物放前排", "zombie_note"),
    11: _script(11, "night", "chooser", "首个夜间关尚未解锁阳光菇；用向日葵维持经济，小喷菇低成本铺路，墓碑吞噬者清关键墓碑"),
    12: _script(12, "night", "chooser", "夜间经济优先，魅惑菇用于高威胁单体，保持每路持续输出"),
    13: _script(13, "night", "chooser", "胆小菇放后排并用坚果保护，墓碑多时保留墓碑吞噬者"),
    14: _script(14, "night", "chooser", "寒冰菇作为全场控制，毁灭菇只在大波且能接受弹坑时使用"),
    15: _script(15, "whack_a_zombie", "special", "锤僵尸为独立鼠标玩法，普通种植 Agent 停止", "almanac"),
    16: _script(16, "pool", "chooser", "荷叶覆盖水路需求，陆路和水路分别建立经济与输出"),
    17: _script(17, "pool", "chooser", "水路用荷叶承载植物，倭瓜和缠绕海藻负责近身应急"),
    18: _script(18, "pool", "chooser", "三线射手居中发挥跨路输出，但先保证经济和荷叶"),
    19: _script(19, "pool", "chooser", "用高坚果或坚果稳定水陆前线，辣椒处理整行危机"),
    20: _script(20, "pool_miniboss", "conveyor", "传送带按水陆地形及时落位，避免卡槽堆满", "zombie_note"),
    21: _script(21, "pool", "chooser", "针对冰车使用地刺/辣椒，水路仍必须携带荷叶"),
    22: _script(22, "pool", "chooser", "火炬树桩放在豌豆系射手前方，保留水路承载与防御"),
    23: _script(23, "pool", "chooser", "高坚果抵挡跳跃威胁，持续输出与经济不可缺失"),
    24: _script(24, "pool", "chooser", "水兵菇可低成本守水路，陆路维持成熟的经济输出阵型"),
    25: _script(25, "little_trouble", "conveyor", "水池传送带，快速使用收到的植物并优先填补薄弱路线", "store_intro"),
    26: _script(26, "fog", "chooser", "雾夜携带荷叶、阳光菇和路灯花，保证水路与视野"),
    27: _script(27, "fog", "chooser", "有气球威胁时必须携带三叶草或仙人掌，三叶草优先应急"),
    28: _script(28, "fog", "chooser", "裂荚射手防矿工绕后，南瓜头保护关键后排"),
    29: _script(29, "fog", "chooser", "磁力菇处理金属装备，配咖啡豆唤醒并用南瓜保护"),
    30: _script(30, "fog_miniboss", "conveyor", "雾夜传送带，卡片及时使用并优先处理气球与后排威胁", "zombie_note"),
    31: _script(31, "fog", "chooser", "磁力菇加咖啡豆处理铁器，三叶草处理气球，水路携带荷叶"),
    32: _script(32, "fog", "chooser", "南瓜保护关键经济和磁力菇，避免前线过度堆放昂贵植物"),
    33: _script(33, "fog", "chooser", "杨桃适合交叉火力，但仍以稳定经济和逐路覆盖为先"),
    34: _script(34, "fog", "chooser", "大波用寒冰菇控制，磁力菇与三叶草覆盖特殊僵尸"),
    35: _script(35, "vasebreaker", "special", "砸罐子为独立开罐玩法，普通种植 Agent 停止", "store_intro"),
    36: _script(36, "fog", "chooser", "成熟雾夜阵容：阳光菇、荷叶、三叶草、磁力菇/咖啡豆与主输出"),
    37: _script(37, "fog", "chooser", "矿工和气球并存时同时准备后向输出与三叶草"),
    38: _script(38, "fog", "chooser", "使用南瓜保护高价值植物，爆发卡留给密集大波"),
    39: _script(39, "fog", "chooser", "优先完整覆盖六路，水路防线不可因雾区而忽略"),
    40: _script(40, "stormy_night", "conveyor", "暴风雨传送带，依据短暂照明和内存路线威胁快速落位", "zombie_note"),
    41: _script(41, "roof", "chooser", "屋顶必须携带花盆，投手类作为主输出，后排优先放经济"),
    42: _script(42, "roof", "chooser", "花盆与卷心菜投手打底，保留倭瓜/辣椒处理近身威胁", "store_intro"),
    43: _script(43, "roof", "chooser", "玉米投手提供控制，所有非花盆植物先确认承载层"),
    44: _script(44, "roof", "chooser", "屋顶建立多路投手输出，用南瓜或高坚果保护核心阵地"),
    45: _script(45, "bungee_blitz", "conveyor", "蹦极闪电战传送带，优先覆盖被偷取后的空位", "zen_tutorial"),
    46: _script(46, "roof", "chooser", "叶子保护伞覆盖关键区域以抵御蹦极和投篮，花盆不可缺"),
    47: _script(47, "roof", "chooser", "玉米与卷心菜稳定铺场，控制和爆发应对高血量目标"),
    48: _script(48, "roof", "chooser", "为巨人准备高伤害投手、控制与即时爆发，保护后排经济"),
    49: _script(49, "roof", "chooser", "优先西瓜投手形成高伤害范围火力；花盆、经济、防御和应急卡配套"),
    50: _script(50, "final_boss", "conveyor", "僵王传送带：冰菇打断火球、辣椒处理冰球，投手持续输出", "credits_note"),
}


def get_adventure_script(level: int) -> AdventureLevelScript:
    return ADVENTURE_LEVEL_SCRIPTS.get(
        level, _script(level, "unknown", "chooser", "根据实时地形和僵尸配置选择均衡阵容")
    )


def _award_seed_limit(level: int) -> int:
    """Exclusive first-adventure seed ID limit, matching GetAwardSeedForLevel."""
    if level <= 0:
        return 0
    area = (level - 1) // 10 + 1
    sub = (level - 1) % 10 + 1
    count = (area - 1) * 8 + sub
    if sub >= 10:
        count -= 2
    elif sub >= 5:
        count -= 1
    return min(count, 40)


def available_adventure_seed_types(level: int) -> list[int]:
    """Plants available before this first-adventure stage, for AI context."""
    return list(range(_award_seed_limit(level)))
