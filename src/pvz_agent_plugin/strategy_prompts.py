from __future__ import annotations


# Optional strategic knowledge.  This is deliberately separate from the
# always-on tool/safety protocol so the user-facing assist switch is a real
# hard gate rather than a cosmetic setting.
GLOBAL_STRATEGY_PROMPT = """
【PVZ 辅助增强：整体玩法】
目标是在不让僵尸越过左侧房屋的前提下，以最少浪费建立可持续防线。每轮先读实时内存，再按“立即致命威胁→补齐空路输出→经济与阵型升级→保留应急资源”的顺序决策；不要为了整齐忽略已逼近的僵尸，也不要重复被拒绝的动作。

坐标：col=0 靠房屋，col=8 靠僵尸出生点。向日葵、阳光菇和永久远程输出通常放 0–2 列；坚果等阻挡放输出前方；土豆雷、倭瓜、大嘴花、地刺和一次性爆发按射程与触发方式靠前。逐路保证持续伤害，危险路优先补火力或控制；割草机只是最后保险，失去割草机的路线提高优先级。

资源：开局尽快建立经济，但已有僵尸逼近且该路无杀伤时先防守。廉价植物用于争取昂贵阵容的成型时间；昂贵卡、长冷却爆发和全场控制不要打在单个普通僵尸上。临近大波保留至少一种即时处理手段。卡片冷却或阳光不足时等待短时间并重新观察，不凭空猜卡槽。

选卡：只从本关 available_plants 中，由 AI 自己组成经济、稳定输出、阻挡/拖延、地形承载、针对性克制和应急爆发；先看 spawn_plan 再选，不能机械拿前 N 张。白天通常用向日葵，夜间优先阳光菇与免费小喷菇；泳池必须准备荷叶，雾夜还要处理视野和气球，屋顶必须准备花盆并优先投手。传送带不选卡、不耗阳光，及时使用现有有效卡，绝不操作空卡槽。

克制：铁桶/橄榄/巨人需要集中高伤、减速和爆发；铁门可用大喷菇穿透或磁力菇卸甲；撑杆、海豚、跳跳用坚果/高坚果破坏跳跃节奏；冰车与雪橇优先地刺、地刺王或辣椒；气球必须有仙人掌、三叶草或香蒲；矿工用裂荚、杨桃或磁力菇保护后排；蹦极和投篮用叶子保护伞；小丑在接近核心前尽快击杀或卸除金属机关。

组合与地形：水面先荷叶、屋顶先花盆；南瓜是保护层，咖啡豆用于唤醒白天沉睡的蘑菇；升级植物必须种在对应基础植物上，玉米加农炮需要相邻两株玉米投手。不要在已有主体植物的格子强种另一主体，除非规则明确允许覆盖层或升级。
""".strip()


def build_strategy_assist_prompt(level_label: str, level_strategy: str) -> str:
    """Build the optional prompt with only the current level's short hint."""
    prompt = GLOBAL_STRATEGY_PROMPT
    if level_strategy:
        prompt += f"\n\n【当前关卡：{level_label}】\n{level_strategy}"
    return prompt
