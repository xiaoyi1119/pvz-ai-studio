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
    1: _script(1, "tutorial_one_lane", "fixed", "单路教学且卡组固定；等僵尸路线明确后在后排种豌豆射手，不要选卡。"),
    2: _script(2, "tutorial_three_lanes", "fixed", "三路教学；后排先补向日葵，僵尸出现的路线及时放豌豆射手。"),
    3: _script(3, "tutorial_full_lawn", "fixed", "五路开放；向日葵放后排、豌豆逐路覆盖，樱桃仅用于密集或漏防。"),
    4: _script(4, "tutorial_wallnut", "fixed", "固定卡组；输出后置，坚果放在受压路线前方，为射手争取击杀时间。"),
    5: _script(5, "wallnut_bowling", "special", "坚果保龄球使用独立点击玩法，普通种植 Agent 停止"),
    6: _script(6, "tutorial_potato_mine", "fixed", "用土豆雷低成本拖住首只僵尸，先扩经济，再补豌豆和坚果。"),
    7: _script(7, "tutorial_snow_pea", "fixed", "寒冰射手优先覆盖压力路减速；维持后排向日葵并补齐其余路线。"),
    8: _script(8, "day", "chooser", "首次自由选卡；从已解锁植物中兼顾向日葵、持续输出、坚果和应急卡。"),
    9: _script(9, "day", "chooser", "铁桶血量高；后排叠加持续输出，用坚果拖延并保留樱桃处理聚集威胁。"),
    10: _script(10, "day_miniboss", "conveyor", "传送带不耗阳光；及时消耗有效卡，先补空路，阻挡植物放在火力前方。", "zombie_note"),
    11: _script(11, "night", "chooser", "首个夜晚尚无阳光菇；向日葵放后排，小喷菇每条受威胁路线先放1株近程过渡，已有防守的空路不要盲目刷满。"),
    12: _script(12, "night", "chooser", "先在后排建立约5株阳光菇；小喷菇只补最早来怪路线，经济成型后用豌豆、寒冰等持续火力接管。"),
    13: _script(13, "night", "chooser", "后排阳光菇供能，每路逐步配置大喷菇；它能穿透铁门和成群目标，小喷菇只负责大喷菇成型前的近程补漏。"),
    14: _script(14, "night", "chooser", "2-4先建5–7株后排阳光菇，各路转大喷菇；小喷菇早期每路约1株，仅紧急补漏。安全时再清关键墓碑，坚果处理撑杆压力。"),
    15: _script(15, "whack_a_zombie", "special", "锤僵尸为独立鼠标玩法，普通种植 Agent 停止", "almanac"),
    16: _script(16, "night", "chooser", "魅惑菇留给铁桶等高价值目标；阳光菇和大喷菇构成长期阵型，小喷菇仅在开局或漏防路线临时拖延。"),
    17: _script(17, "night", "chooser", "胆小菇放后排作长程主火力，大喷菇补中程穿透；前方用坚果拖延，小喷菇只补短暂缺口，别挤占主力格。"),
    18: _script(18, "night", "chooser", "寒冰菇留给大波或多路同时逼近；平时以阳光菇经济、胆小菇长程和大喷菇穿透维持五路，不再把小喷菇当主力。"),
    19: _script(19, "night", "chooser", "毁灭菇只处理大波或崩线并避开核心格的弹坑；常规阵型以阳光菇、胆小菇/大喷菇和坚果成型，小喷菇只应急。"),
    20: _script(20, "night_miniboss", "conveyor", "夜间传送带不耗阳光；卡片及时分配到空路，爆发卡留给密集波。", "zombie_note"),
    21: _script(21, "pool", "chooser", "首次泳池关必须选荷叶；水路先铺承载再建经济和输出，陆路保持常规防线。"),
    22: _script(22, "pool", "chooser", "倭瓜用于近身应急；荷叶保障两条水路，先经济后逐路形成持续火力。"),
    23: _script(23, "pool", "chooser", "三线射手放在能同时覆盖三路的位置，但先确保荷叶、经济和基础逐路防守。"),
    24: _script(24, "pool", "chooser", "缠绕海藻低费处理水路高威胁；陆路与水路都要有稳定输出，别只守岸上。"),
    25: _script(25, "little_trouble", "conveyor", "小僵尸水池传送带；快速补齐六路，荷叶承载水面植物，避免卡槽积压。", "store_intro"),
    26: _script(26, "pool", "chooser", "辣椒可清整行并融冰；仍以荷叶、经济和六路持续输出为骨架，爆发只救急。"),
    27: _script(27, "pool", "chooser", "冰车会碾压并留冰道；用地刺提前拦截，必要时辣椒清冰，水路仍带荷叶。"),
    28: _script(28, "pool", "chooser", "火炬树桩放在豌豆系射手前方增伤；不要挡住非豌豆弹道，保持水路承载。"),
    29: _script(29, "pool", "chooser", "高坚果可拦撑杆和海豚跳跃；前方高坚果配后排持续输出，六路经济均衡。"),
    30: _script(30, "pool_miniboss", "conveyor", "泳池传送带不耗阳光；按水陆位置及时用卡，优先修补无输出或无阻挡路线。", "zombie_note"),
    31: _script(31, "fog", "chooser", "首次雾夜；带荷叶和阳光菇，水兵菇低费守水路，依据内存威胁补雾中路线。"),
    32: _script(32, "fog", "chooser", "路灯花用于稳定视野但需保护；荷叶、阳光菇和六路输出优先于过度照明。"),
    33: _script(33, "fog", "chooser", "出现气球时必须带仙人掌；先保证水陆基础阵型，再在受威胁路线部署对空。"),
    34: _script(34, "fog", "chooser", "三叶草是气球和浓雾的即时解法；保留冷却应急，不替代常规六路输出。"),
    35: _script(35, "vasebreaker", "special", "砸罐子为独立开罐玩法，普通种植 Agent 停止", "store_intro"),
    36: _script(36, "fog", "chooser", "裂荚射手保护后排免受矿工反向进攻；同时带三叶草或仙人掌处理气球。"),
    37: _script(37, "fog", "chooser", "杨桃可形成交叉火力并兼顾后方，但仍先建立阳光菇、荷叶和逐路基础输出。"),
    38: _script(38, "fog", "chooser", "南瓜保护路灯花、经济和关键输出；别把保护层当作持续伤害的替代品。"),
    39: _script(39, "fog", "chooser", "磁力菇可卸除铁桶、铁门、矿工工具等金属；夜间无需咖啡豆，放在受保护位置。"),
    40: _script(40, "stormy_night", "conveyor", "暴风雨传送带；依靠内存路线威胁及时落位，优先补空路并保留群体应急卡。", "zombie_note"),
    41: _script(41, "roof", "chooser", "首次屋顶已有固定花盆；卷心菜投手越过坡面输出，优先保护有限花盆和后排经济。"),
    42: _script(42, "roof", "chooser", "必须带花盆扩展可种区域；卷心菜投手作主输出，倭瓜或辣椒处理近身危机。", "store_intro"),
    43: _script(43, "roof", "chooser", "玉米投手兼具输出和黄油控制；每株非花盆植物先确认脚下已有花盆。"),
    44: _script(44, "roof", "chooser", "咖啡豆只用于唤醒屋顶白天的蘑菇；若不带蘑菇就别浪费卡槽，主力用投手。"),
    45: _script(45, "bungee_blitz", "conveyor", "蹦极闪电战传送带；快速填补被偷后的空位，优先保住各路投手与花盆。", "zen_tutorial"),
    46: _script(46, "roof", "chooser", "大蒜可改道但需配合相邻路线火力；花盆、经济和投手仍是屋顶阵型核心。"),
    47: _script(47, "roof", "chooser", "叶子保护伞覆盖周围关键格，克制蹦极和投篮；用投手维持每路伤害。"),
    48: _script(48, "roof", "chooser", "金盏花不是战斗核心；优先花盆、经济、投手、阻挡和爆发，富余时才考虑产币。"),
    49: _script(49, "roof", "chooser", "西瓜投手提供高额范围伤害；先让花盆和经济成型，再集中升级危险路线火力。"),
    50: _script(50, "final_boss", "conveyor", "僵王传送带：投手持续输出；冰菇应对火球，辣椒应对冰球，并及时补回被压毁阵地。", "credits_note"),
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
