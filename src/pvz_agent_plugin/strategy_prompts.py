from __future__ import annotations


# Optional strategic knowledge.  This is deliberately separate from the
# always-on tool/safety protocol so the user-facing assist switch is a real
# hard gate rather than a cosmetic setting.
GLOBAL_STRATEGY_PROMPT = """
【PVZ 辅助增强：整体玩法】
每轮严格按这个顺序判断：①僵尸已在近处、正在啃食或该路割草机已失去时先救线；②让每条会来怪的路线至少有持续输出；③补经济；④把临时阵型替换成中后期阵型；⑤安全时才清墓碑、升级和整理。不要为了阵型整齐忽略近身威胁，也不要无目标重复同一张卡。

合法性：坐标 col=0 靠房屋、col=8 靠出生点。place_plant 必须从所选卡片的 legal_targets 中取坐标；墓碑只能用墓碑吞噬者，普通主体不能覆盖墓碑或已有主体。水面先荷叶、屋顶先花盆；升级卡必须落在对应基础植物上。若没有合适合法落点，短暂 wait 后重新观察，不猜卡槽或坐标。

阵型：经济和贵重远程火力通常放后部，阻挡放在火力前方；短射程植物必须处在能命中僵尸的位置。先做“一路一份可靠伤害”，再给高压路叠第二份火力。廉价植物负责开局拖延，阳光充足后要转成稳定输出、减速、阻挡与针对性克制；不要让免费植物或单一植物长期占据大多数格子。大波前保留一种爆发或全场控制。

经济：早期用土豆雷、小喷菇等低费手段换取经济时间，但无持续火力的受压路线优先防守。阳光已足以补齐缺失角色时就使用，不长期囤积；后期经济明显过剩且阵型缺火力时，才考虑铲掉多余经济植物换主力。

铲子：铲除必须服务于明确替换，不能把仍在承担唯一输出、阻挡或经济的植物随便挖掉。安全路线可铲掉已失去价值的小喷菇、位置过差的植物或多余经济植物，并在下一轮立刻把同格换成更强且已可用的卡；僵尸已逼近、替换卡未就绪或阳光不足时禁止铲除。

墓碑：优先清除占据计划阵型、靠左或会妨碍关键落点的墓碑；临近最后一波时处理剩余高风险墓碑，减少墓碑出怪。某路正在受压、阳光刚够救线或墓碑吞噬者会被立即啃食时，推迟清墓碑。一次只清一座并重新观察。

选卡与克制：AI 根据 spawn_plan 自选经济、持续输出、阻挡/拖延、地形承载、威胁克制和应急卡，不能机械拿前 N 张。铁门用大喷菇或磁力菇；高血量目标用集中火力、减速和爆发；跳跃类用坚果/高坚果破坏节奏；冰车用地刺或辣椒；气球必须有仙人掌、三叶草或香蒲；矿工保护后排。传送带不选卡、不耗阳光，及时使用现有卡。
""".strip()


def build_strategy_assist_prompt(level_label: str, level_strategy: str) -> str:
    """Build the optional prompt with only the current level's short hint."""
    prompt = GLOBAL_STRATEGY_PROMPT
    if level_strategy:
        prompt += f"\n\n【当前关卡：{level_label}】\n{level_strategy}"
    return prompt
