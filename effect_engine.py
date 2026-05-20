"""
⚙️ 效果引擎 — 统一处理英雄被动、技能升级、装备效果、羁绊效果
数据驱动，零 per-hero if/else。两个战斗路径共用同一引擎。
"""

import random

# ============================================================
# 英雄效果数据库
# 格式: hero_id → [{"event": str, "cond": dict|None, "actions": [dict]}]
# event: on_battle_start | on_skill | on_kill | on_hit | on_ally_low_hp | on_ally_die
# ============================================================

HERO_EFFECTS = {
    # ─── 良品 ───
    "guojia": [{
        "event": "on_battle_start",
        "actions": [{"type": "heal_team", "pct": 0.15, "per_lv": {3: 0.25}}],
        "lv5_extra": {"type": "buff_team", "stat": "atk", "pct": 0.1}
    }],
    "ganning": [{
        "event": "on_battle_start",
        "actions": [{"type": "debuff_enemies", "stat": "atk", "pct": -0.1, "per_lv": {3: -0.2}}],
        "lv5_extra": {"type": "debuff_enemies", "stat": "crit", "pct": -0.05}
    }],
    "diaochan": [{
        "event": "on_battle_start",
        "actions": [{"type": "charm_enemy", "dur": 3, "per_lv": {7: 2}}]
    }],
    # 华佗: 妙手回春 — 队友低于30%自动回复
    "huatuo": [{
        "event": "on_ally_low_hp",
        "cond": {"type": "hp_threshold", "pct": 0.3},
        "counter": {"key": "huatuo_miaoshou", "max": 2, "per_lv": {3: 3}},
        "actions": [{"type": "heal_lowest_ally", "pct": 0.15, "per_lv": {5: 0.30}}],
        "lv7_extra": {"type": "cleanse_team_on_trigger"}
    }],
    # 蔡文姬: 悲歌 — 队友死亡全体回复
    "caiwenji": [{
        "event": "on_ally_die",
        "actions": [{"type": "heal_team", "pct": 0.25, "per_lv": {3: 0.40}}],
        "lv5_extra": {"type": "buff_self_on_ally_die", "stat": "immune", "dur": 2}
    }],
    # 吕布: 无双 — 每击败一个敌人攻击+20%
    "luobu": [{
        "event": "on_kill",
        "counter": {"key": "luobu_wushuang", "max": 3, "per_lv": {3: 4}},
        "actions": [{"type": "stack_buff", "stat": "atk", "pct": 0.20, "per_lv": {5: 0.25}}],
        "lv5_extra": {"type": "stack_buff", "stat": "crit", "pct": 0.10, "per_lv": {5: 0.10}},
        "lv7_extra": {"type": "max_stacks_skill_no_cd"}
    }],
    # 关羽: 武圣 — 第一刀必定暴击伤害+50%
    "guanyu": [{
        "event": "on_skill",
        "one_shot": True,
        "actions": [{"type": "buff_self", "stat": "guaranteed_crit", "pct": 1.0, "dur": 1},
                     {"type": "buff_self", "stat": "skill_dmg_pct", "pct": 0.5, "dur": 1}],
        "per_lv": {3: {"type": "buff_self", "stat": "skill_dmg_pct", "pct": 1.0, "dur": 1}},
        "lv5_extra": {"type": "buff_self", "stat": "guaranteed_crit", "pct": 1.0, "dur": 3},
        "lv7_extra": {"type": "true_dmg_on_first_skill"}
    }],
    # 张飞: 万人敌 — 每受一次攻击+5%攻击
    "zhangfei": [{
        "event": "on_hit",
        "counter": {"key": "zhangfei_wanrendi", "max": 10, "per_lv": {3: 15}},
        "actions": [{"type": "stack_buff", "stat": "atk", "pct": 0.05, "per_lv": {7: 0.10}},
                     {"type": "stack_buff", "stat": "dmg_reduce", "pct": 0.05, "min_lv": 5}],
        "lv7_extra": {"type": "counter_attack_100pct"}
    }],
    # 赵云: 一身是胆 — 每损失10%血量+8%攻击
    "zhaoyun": [{
        "event": "on_hit",
        "actions": [{"type": "atk_per_hp_lost", "pct_per_10pct": 0.08, "per_lv": {7: 0.16}},
                     {"type": "crit_per_hp_lost", "pct_per_10pct": 0.05, "min_lv": 3}],
        "lv5_extra": {"type": "buff_self_below_hp", "stat": "immune", "dur": 1, "hp_pct": 0.3}
    }],
    # 李白: 斗酒诗百篇 — 每击败一个敌人+12%攻击
    "lihai": [{
        "event": "on_kill",
        "counter": {"key": "lihai_doujiu", "max": 5, "per_lv": {3: 8}},
        "actions": [{"type": "stack_buff", "stat": "atk", "pct": 0.12, "per_lv": {5: 0.20}}],
        "lv7_extra": {"type": "max_stacks_skill_crit"}
    }],
    # 夏侯惇: 刚烈
    "xiahoudun": [{
        "event": "on_hit",
        "actions": [{"type": "reflect_on_crit", "dmg_pct": 1.50, "per_lv": {3: 2.00}}],
        "lv5_extra": {"type": "reflect_on_any_hit", "chance": 0.30}
    }],
    # 黄忠: 老当益壮
    "huangzhong": [{
        "event": "on_battle_start",
        "actions": [{"type": "buff_self_if_hp_above", "stat": "atk", "pct": 0.20, "hp_pct": 0.50, "per_lv": {3: 0.35}}],
        "lv5_extra": {"type": "lower_hp_threshold_to", "pct": 0.30}
    }],
    # 养由基: 百发百中
    "yangyouji": [{
        "event": "on_battle_start",
        "actions": [{"type": "buff_self", "stat": "ignore_dodge", "pct": 1.0, "dur": -1},
                     {"type": "buff_self", "stat": "crit", "pct": 0.15, "dur": -1, "per_lv": {3: 0.25}}],
        "lv5_extra": {"type": "buff_self", "stat": "crit_dmg", "pct": 0.50, "dur": -1},
        "lv7_extra": {"type": "atk_per_crit"}
    }],
    # 貂蝉: 离间lv效果
    "diaochan_lv": [{
        "event": "on_battle_start",
        "actions": [{"type": "buff_self", "stat": "dodge", "pct": 0.15, "dur": -1}],
        "lv3_extra_battle_start": {"type": "charmed_ally_takes_more_dmg"},
        "lv5_extra_battle_start": {"type": "charm_end_stun"}
    }],
    # 典韦: 死战
    "dianwei": [{
        "event": "on_hit",
        "cond": {"type": "hp_below", "pct": 0.20},
        "actions": [{"type": "buff_self", "stat": "atk_mult", "pct": 1.0, "dur": -1, "per_lv": {3: 1.0}}],
        "lv5_extra": {"type": "buff_self_below_hp", "stat": "immune", "dur": 2, "hp_pct": 0.20}
    }],
    # 诸葛: 空城计
    "zhugeliang": [{
        "event": "on_hit",
        "cond": {"type": "hp_below", "pct": 0.30},
        "actions": [{"type": "buff_self", "stat": "invisible", "pct": 1.0, "dur": 2}],
        "lv3_extra_battle": {"type": "invisible_heal_per_sec", "pct": 0.05},
        "lv5_extra_battle": {"type": "invisible_end_heal_team", "pct": 0.10},
        "lv7_extra_battle": {"type": "invisible_skill_speed_2x"}
    }],
    # 庞统: 铁索连舟
    "pangtong": [{
        "event": "on_battle_start",
        "actions": [{"type": "chain_enemies", "dur": 2, "per_lv": {3: 3}}],
        "lv5_extra": {"type": "chained_take_more_dmg", "pct": 0.30},
        "lv7_extra": {"type": "chain_explode", "pct": 2.00}
    }],
    # 周仓: 忠勇
    "zhoucang": [{
        "event": "on_protect",
        "actions": [{"type": "heal_self", "pct": 0.05, "per_lv": {3: 0.10}}],
        "lv5_extra": {"type": "heal_team", "pct": 0.05}
    }],
    # 燕十三: 影步
    "yanshisan": [{
        "event": "on_battle_start",
        "actions": [{"type": "buff_self", "stat": "dodge", "pct": 0.15, "dur": -1, "per_lv": {3: 0.25}}],
        "lv5_extra": {"type": "heal_on_dodge", "pct": 0.10}
    }],

    # ═══════════════ 神卡 ═══════════════
    # 孙悟空: 齐天大圣 — 每击败一个敌人+20%攻击
    "wukong": [{
        "event": "on_kill",
        "counter": {"key": "wukong_qitian", "max": 5, "per_lv": {3: 8}},
        "actions": [{"type": "stack_buff", "stat": "atk", "pct": 0.20, "per_lv": {5: 0.30}}],
        "lv7_extra": {"type": "max_stacks_skill_crit_dmg_200"}
    }],
    # 秦始皇: 千古一帝 — 战斗开始全体敌人攻击-20%+自身攻击+30%
    "qinshihuang": [{
        "event": "on_battle_start",
        "actions": [{"type": "debuff_enemies", "stat": "atk", "pct": -0.20, "per_lv": {3: -0.30}},
                     {"type": "buff_self", "stat": "atk", "pct": 0.30, "dur": -1, "per_lv": {5: 0.50}}],
        "lv7_extra": {"type": "full_energy_self"}
    }],
    # 刑天: 不屈 — 血量低于30%时攻击翻倍+减伤50%
    "xingtian": [{
        "event": "on_hit",
        "cond": {"type": "hp_below", "pct": 0.30},
        "actions": [{"type": "buff_self", "stat": "atk_mult", "pct": 1.0, "dur": -1},
                     {"type": "buff_self", "stat": "dmg_reduce", "pct": 0.5, "dur": -1}],
        "lv3_extra": {"type": "lower_hp_threshold_to", "pct": 0.50},
        "lv5_extra": {"type": "buff_self_below_hp", "stat": "immune", "dur": 2, "hp_pct": 0.30},
        "lv7_extra": {"type": "revive_on_death", "pct": 0.60}
    }],
    # 后羿: 射日神弓 — 战斗开始锁定最高血量敌人降低50%血上限
    "houyi": [{
        "event": "on_battle_start",
        "actions": [{"type": "debuff_highest_hp_enemy", "hp_reduce_pct": 0.50, "per_lv": {3: 0.60}}],
        "lv5_extra": {"type": "debuff_highest_hp_enemy_atk", "pct": -0.30},
        "lv7_extra": {"type": "highest_hp_death_stun"}
    }],
    # 女娲: 创世 — 战斗开始全体30%护盾+回复10%
    "nuwa": [{
        "event": "on_battle_start",
        "actions": [{"type": "shield_team", "pct": 0.30, "per_lv": {3: 0.50}},
                     {"type": "heal_team", "pct": 0.10, "per_lv": {3: 0.20}}],
        "lv5_extra": {"type": "revive_ally_on_death", "max_per_battle": 1},
        "lv7_extra": {"type": "revive_team_invincible"}
    }],
    # 蚩尤: 兵主 — 战斗开始全体+30%攻击+15%减伤+自身HP50%护盾
    "chiyou": [{
        "event": "on_battle_start",
        "actions": [{"type": "buff_team", "stat": "atk", "pct": 0.30, "dur": -1, "per_lv": {3: 0.50}},
                     {"type": "buff_team", "stat": "dmg_reduce", "pct": 0.15, "dur": -1, "per_lv": {3: 0.25}},
                     {"type": "shield_team_from_self", "pct": 0.50, "per_lv": {3: 0.70}}],
        "lv5_extra": {"type": "team_invincible_if_hp_above", "pct": 0.70},
        "lv7_extra": {"type": "self_buff_on_invincible_tigger"}
    }],
}


# ═══════════════ 装备效果数据库 ═══════════════
# 结构化装备效果, 按eid索引
# 事件类型: passive(常驻) | battle_start(战斗开始) | on_skill(技能释放) | on_hit(受击)
# 条件: exclusive限制只在专属英雄生效
EQUIP_EFFECTS = {
    # ── 良品 ──
    "bronze_mirror": {"passive_buffs": [{"stat": "crit_resist", "pct": 0.20}]},  # 暴击减伤20%
    # ── 极品 ──
    "longquan_sword": {"passive_buffs": [{"stat": "crit", "pct": 0.05}]},  # 暴击率+5%
    "mingguang_armor": {"passive_buffs": [{"stat": "dmg_reduce", "pct": 0.08}]},  # 减伤8%
    "jade_pendant": {"battle_start": [{"type": "shield_self", "pct": 0.10}]},  # 开局10%护盾
    # ── 绝品 ──
    "halberd": {"exclusive": "luobu", "on_skill": [{"type": "basic_attack_again"}]},  # 技能后普攻翻倍
    "qilin_armor": {"exclusive": "zhugeliang", "on_hit": [{"chance": 0.20, "type": "heal_self", "pct": 0.10}]},  # 受击20%回血10%
    "pojun_bow": {"exclusive": "yangyouji", "passive_buffs": [{"stat": "crit_dmg", "pct": 0.50}]},  # 暴击伤害+50%
    "bagua_mirror": {"passive_buffs": [{"stat": "crit", "pct": 0.10}, {"stat": "dodge", "pct": 0.10}]},  # 暴击+10%闪避+10%
    # ── 传说 ──
    "qinglian_sword": {"exclusive": "lihai", "on_skill": [{"type": "dmg_mult", "value": 2.0}],
                       "passive_buffs": [{"stat": "atk_mult", "pct": 0.30}]},  # 技能伤害翻倍+攻击+30%
    "zhangba_spear": {"exclusive": "zhangfei", "on_skill": [{"type": "taunt_all_enemies"}, {"type": "shield_team", "pct": 0.40}]},
    "qinglong_blade": {"exclusive": "guanyu", "on_skill": [{"type": "true_damage", "pct": 1.0}]},  # 青龙偃月真实伤害
    "chitu": {"battle_start": [{"type": "shield_team", "pct": 0.30}]},  # 开局全体30%护盾
    "heshi_bi": {"passive_buffs": [{"stat": "all_stats_pct", "pct": 0.15}]},  # 全属性+15%
    # ── 神卡 (金箍棒, 射日弓, 八卦炉, 刑天斧, 补天石, 蚩尤旗) ──
    "golden_staff": {
        "exclusive": "wukong",
        "passive_buffs": [{"stat": "atk_mult", "pct": 0.40}],
        "on_skill": [{"type": "extra_hits", "count": 2}],
        "milestones": {
            20: [{"type": "buff_self", "stat": "crit_dmg", "pct": 0.50, "dur": -1}],
            40: [{"type": "buff_self", "stat": "guaranteed_skill_crit", "pct": 1.0, "dur": -1}],
            60: [{"type": "buff_self", "stat": "execute_pct", "pct": 0.20, "dur": -1}],
            80: [{"type": "buff_self", "stat": "true_dmg_pct", "pct": 0.20, "dur": -1}],
            100: [{"type": "skill_upgrade", "hits": 5, "dmg_pct": 3.0}],
        }
    },
    "she_sun_bow": {
        "exclusive": "houyi",
        "passive_buffs": [{"stat": "execute_pct", "pct": 0.20}, {"stat": "crit_dmg", "pct": 0.80}],
        "milestones": {
            20: [{"type": "buff_self", "stat": "atk_mult", "pct": 0.15, "dur": -1}],
            40: [{"type": "buff_self", "stat": "guaranteed_crit", "pct": 1.0, "dur": -1}],
            60: [{"type": "execute_double_threshold"}],
            80: [{"type": "splash_damage"}],
            100: [{"type": "true_damage_nuke", "dmg": 9999}],
        }
    },
    "bagua_furnace": {
        "exclusive": "qinshihuang",
        "passive_buffs": [{"stat": "skill_dmg_pct", "pct": 0.50}],
        "on_skill": [{"type": "extend_burn", "dur": 2}],
        "milestones": {
            20: [{"type": "buff_self", "stat": "dmg_reduce", "pct": 0.10, "dur": -1}],
            40: [{"type": "skill_lifesteal", "pct": 0.30}],
            60: [{"type": "burn_aoe"}],
            80: [{"type": "aoe_dot", "pct": 0.05}],
            100: [{"type": "skill_upgrade", "dmg_pct": 5.0, "aoe": True, "extra_effect": "silence"}],
        }
    },
    "xingtian_axe": {
        "exclusive": "xingtian",
        "passive_buffs": [{"stat": "skill_dmg_pct", "pct": 0.60}, {"stat": "lifesteal", "pct": 0.20}],
        "milestones": {
            20: [{"type": "buff_self", "stat": "hp_mult", "pct": 0.20, "dur": -1}],
            40: [{"type": "aoe_expand"}],
            60: [{"type": "buff_self", "stat": "reflect", "pct": 0.20, "dur": -1}],
            80: [{"type": "reset_on_kill"}],
            100: [{"type": "skill_upgrade", "dmg_pct": 6.0, "aoe": True, "extra_effect": "armor_break"}],
        }
    },
    "nuwa_stone": {
        "exclusive": "nuwa",
        "passive_buffs": [{"stat": "heal_pct", "pct": 0.40}, {"stat": "shield_pct", "pct": 0.30}],
        "milestones": {
            20: [{"type": "buff_self", "stat": "immune_dur", "pct": 1.0, "dur": -1}],  # 免疫+1回合
            40: [{"type": "heal_crit"}],
            60: [{"type": "revive_ally", "pct": 0.50}],
            80: [{"type": "battle_start_shield", "pct": 0.60}],
            100: [{"type": "team_invincible", "dur": 2}],
        }
    },
    "chiyou_flag": {
        "exclusive": "chiyou",
        "passive_buffs": [{"stat": "dmg_reduce", "pct": 0.25}, {"stat": "shield_pct", "pct": 0.40}],
        "milestones": {
            20: [{"type": "buff_self", "stat": "hp_mult", "pct": 0.30, "dur": -1}],
            40: [{"type": "taunt_heal_team", "pct": 0.10}],
            60: [{"type": "buff_self", "stat": "reflect", "pct": 0.30, "dur": -1}],
            80: [{"type": "team_dmg_reduce", "pct": 0.20}],
            100: [{"type": "team_invincible_and_atk_double"}],
        }
    },
    "da_shen_bian": {"on_hit": [{"type": "debuff_target_atk", "pct": -0.05, "max_stacks": 3}]},
    "shanhe_sheji": {"battle_start": [{"type": "seal_random_enemy", "dur": 2}]},
    "zhanxian_feidao": {"passive_buffs": [{"stat": "crit", "pct": 0.05}], "on_hit": [{"chance": 0.30, "type": "extra_dmg_lowest", "pct": 0.40}]},
    "liuhun_fan": {"on_kill": [{"type": "heal_team_pct", "pct": 0.12}]},
    "xinghuang_qi": {"passive_buffs": [{"stat": "dmg_reduce", "pct": 0.08}], "on_hit": [{"chance": 0.30, "type": "reflect_true_dmg", "pct": 0.50}]},
    "tianming_dun": {"passive_buffs": [{"stat": "dmg_reduce", "pct": 0.10}]},
    "xuanwu_jia": {"passive_buffs": [{"stat": "dmg_reduce", "pct": 0.20}]},
    "bumie_jinshen": {"passive_buffs": [{"stat": "dmg_reduce", "pct": 0.35}, {"stat": "hp_mult", "pct": 0.70}], "battle_start": [{"type": "regen_per_turn", "pct": 0.05}]},
}


def get_equip_effects(eid, upgrade_lv):
    """获取装备生效效果，含里程碑升级检测"""
    base = EQUIP_EFFECTS.get(eid, {})
    result = dict(base)  # 浅拷贝
    # 里程碑效果
    ml = {}
    for lv, effects in base.get("milestones", {}).items():
        if upgrade_lv >= lv:
            ml[lv] = effects
    result["_active_milestones"] = ml
    return result


def apply_equip_passive_buffs(unit, allies=None, enemies=None, context=None):
    """将装备的常驻被动buff注入到战斗单位。
    
    处理 passive_buffs 和里程碑中已激活的 buff_self / hp_mult 效果。
    检查 exclusive 条件，非专属英雄跳过专属效果。
    
    Args:
        unit: 战斗单位（会原地修改 unit["buffs"] 和 unit["skill_dmg_pct"]）
        allies: 我方所有单位（可选，用于 battle_start 中的 shield_team 等）
        enemies: 敌方所有单位（可选）
        context: 战斗上下文（可选）
    """
    eq = unit.get("_equipped", {})
    eq_bag = unit.get("_equip_bag", [])
    hid = unit.get("_hd", {}).get("id")
    
    for slot, eid in eq.items():
        if not eid:
            continue
        ed = EQUIP_EFFECTS.get(eid, {})
        if not ed:
            continue
        
        # 检查专属
        exclusive = ed.get("exclusive")
        if exclusive and exclusive != hid:
            continue
        
        ulv = 0
        if eq_bag:
            eb = next((x for x in eq_bag if isinstance(x, dict) and x.get("id") == eid), None)
            if eb:
                ulv = eb.get("upgrade_lv", 0)
        
        # 从 get_equip_effects 获取完整效果（含里程碑）
        full_eff = get_equip_effects(eid, ulv)
        
        # 1) passive_buffs
        for pb in full_eff.get("passive_buffs", []):
            stat = pb.get("stat", "")
            pct = pb.get("pct", 0)
            
            if stat == "all_stats_pct":
                unit["atk"] = int(unit["atk"] * (1 + pct))
                unit["hp"] = int(unit["hp"] * (1 + pct))
                unit["max_hp"] = int(unit["max_hp"] * (1 + pct))
                if "spd" in unit:
                    unit["spd"] = int(unit["spd"] * (1 + pct))
            elif stat == "hp_mult":
                unit["hp"] = int(unit["hp"] * (1 + pct))
                unit["max_hp"] = int(unit["max_hp"] * (1 + pct))
            elif stat == "skill_dmg_pct":
                # 技能伤害百分比直接设 unit 属性
                unit["skill_dmg_pct"] = unit.get("skill_dmg_pct", 1.0) * (1 + pct)
            else:
                unit["buffs"].append({"stat": stat, "pct": pct, "dur": -1})
        
        # 2) 里程碑中已激活的 buff_self 效果
        for lv, actions in full_eff.get("_active_milestones", {}).items():
            for action in actions:
                atype = action.get("type", "")
                if atype == "buff_self":
                    stat = action.get("stat", "")
                    pct = action.get("pct", 0)
                    dur = action.get("dur", -1)
                    unit["buffs"].append({"stat": stat, "pct": pct, "dur": dur})
                elif atype == "hp_mult":
                    pct = action.get("pct", 0)
                    unit["hp"] = int(unit["hp"] * (1 + pct))
                    unit["max_hp"] = int(unit["max_hp"] * (1 + pct))
                elif atype in ("heal_pct", "shield_pct", "execute_pct", "dmg_reduce", "lifesteal", 
                              "reflect", "true_dmg_pct", "dodge", "crit_dmg", "crit_resist",
                              "guaranteed_crit", "guaranteed_skill_crit"):
                    # 将这些也作为常驻 buff 注入
                    pct = action.get("pct", 0)
                    dur = action.get("dur", -1)
                    stat = action.get("stat", atype)
                    unit["buffs"].append({"stat": stat, "pct": pct, "dur": dur})


def process_equip_battle_start(unit, allies, enemies, context):
    """触发装备的战斗开始效果"""
    eq = unit.get("_equipped", {})
    eq_bag = unit.get("_equip_bag", [])
    hid = unit.get("_hd", {}).get("id")
    results = []
    
    for slot, eid in eq.items():
        if not eid:
            continue
        ed = EQUIP_EFFECTS.get(eid, {})
        if not ed:
            continue
        
        # 检查专属
        exclusive = ed.get("exclusive")
        if exclusive and exclusive != hid:
            continue
        
        ulv = 0
        if eq_bag:
            eb = next((x for x in eq_bag if isinstance(x, dict) and x.get("id") == eid), None)
            if eb:
                ulv = eb.get("upgrade_lv", 0)
        
        # battle_start 效果
        for action in ed.get("battle_start", []):
            r = _execute_equip_action(action, unit, allies, enemies, context)
            if r:
                results.append(r)
        
        # 里程碑 effect（跳过已由 apply_equip_passive_buffs 处理的常驻类型）
        for lv, actions in ed.get("milestones", {}).items():
            if ulv >= lv:
                for action in actions:
                    atype = action.get("type", "")
                    if atype in ("buff_self", "hp_mult",
                                 "heal_pct", "shield_pct", "execute_pct",
                                 "dmg_reduce", "lifesteal", "reflect",
                                 "true_dmg_pct", "dodge", "crit_dmg", "crit_resist",
                                 "guaranteed_crit", "guaranteed_skill_crit"):
                        continue  # 已由 apply_equip_passive_buffs 处理
                    r = _execute_equip_action(action, unit, allies, enemies, context)
                    if r:
                        results.append(r)
    
    return results if results else None


def _execute_equip_action(action, unit, allies, enemies, context):
    """执行单个装备效果动作"""
    atype = action.get("type")
    if not atype:
        return None
    
    # shield_self
    if atype == "shield_self":
        pct = action.get("pct", 0.1)
        unit["shield"] = int(unit["max_hp"] * pct)
        context.setdefault("all_actions", []).append({
            "side": "ally", "type": "passive", "attacker_name": unit["name"],
            "skill": "装备", "aoe": False, "target_name": unit["name"],
            "msg": f"装备护盾{int(pct*100)}%"
        })
    
    # shield_team
    elif atype == "shield_team":
        pct = action.get("pct", 0.3)
        for a in allies:
            if a.get("alive", True):
                a["shield"] = int(a["max_hp"] * pct)
        context.setdefault("all_actions", []).append({
            "side": "ally", "type": "passive", "attacker_name": unit["name"],
            "skill": "装备", "aoe": True,
            "msg": f"全体护盾{int(pct*100)}%"
        })
    
    # team_invincible
    elif atype == "team_invincible":
        dur = action.get("dur", 2)
        for a in allies:
            if a.get("alive", True):
                a["buffs"].append({"stat": "immune", "pct": 1.0, "dur": dur})
        context.setdefault("all_actions", []).append({
            "side": "ally", "type": "passive", "attacker_name": unit["name"],
            "skill": "装备", "aoe": True,
            "msg": f"全体无敌{dur}次伤害"
        })
    
    # team_dmg_reduce
    elif atype == "team_dmg_reduce":
        pct = action.get("pct", 0.2)
        for a in allies:
            if a.get("alive", True):
                a["buffs"].append({"stat": "dmg_reduce", "pct": pct, "dur": -1})
        context.setdefault("all_actions", []).append({
            "side": "ally", "type": "passive", "attacker_name": unit["name"],
            "skill": "装备", "aoe": True,
            "msg": f"全体减伤{int(pct*100)}%"
        })
    
    # battle_start_shield → 同 shield_team
    elif atype == "battle_start_shield":
        pct = action.get("pct", 0.3)
        for a in allies:
            if a.get("alive", True):
                a["shield"] = int(a["max_hp"] * pct)
        context.setdefault("all_actions", []).append({
            "side": "ally", "type": "passive", "attacker_name": unit["name"],
            "skill": "装备", "aoe": True,
            "msg": f"全体护盾{int(pct*100)}%"
        })
    
    # team_invincible_and_atk_double: 全体无敌 + 攻击翻倍
    elif atype == "team_invincible_and_atk_double":
        for a in allies:
            if a.get("alive", True):
                a["buffs"].append({"stat": "immune", "pct": 1.0, "dur": 2})
                a["buffs"].append({"stat": "atk_mult", "pct": 1.0, "dur": -1})
        context.setdefault("all_actions", []).append({
            "side": "ally", "type": "passive", "attacker_name": unit["name"],
            "skill": "装备", "aoe": True,
            "msg": "全体无敌+攻击翻倍"
        })
    
    return None

def process_effects(event, unit, hd, sk_lv, allies, enemies, context):
    """处理所有匹配event的英雄被动效果。
    
    Args:
        event: str — 'on_battle_start', 'on_skill', 'on_kill', 'on_hit', 'on_ally_low_hp', 'on_ally_die'
        unit: dict — 触发的战斗单元
        hd: dict — 英雄数据(HERO_DATA条目)
        sk_lv: int — 技能等级
        allies: list — 我方所有单位
        enemies: list — 敌方所有单位
        context: dict — 战斗上下文(passive_counters, all_actions等)
    
    Returns:
        dict — 激发的事件信息，用于前端显示，或None
    """
    if not hd:
        return None
    hid = hd["id"]
    effects_list = HERO_EFFECTS.get(hid, [])
    if not effects_list:
        return None
    
    results = []
    for entry in effects_list:
        if entry.get("event") != event:
            continue
        # 检查条件
        if not _check_condition(entry.get("cond"), unit):
            continue
        # 检查一次触发限制
        if entry.get("one_shot"):
            key = f"one_shot_{hid}_{event}"
            if key in context.get("triggered", set()):
                continue
            context.setdefault("triggered", set()).add(key)
        # 检查使用次数限制
        counter_def = entry.get("counter")
        if counter_def:
            ckey = counter_def["key"]
            max_cnt = counter_def.get("max", 1)
            max_cnt = _apply_upgrade(max_cnt, sk_lv, counter_def.get("per_lv", {}))
            current = context.setdefault("passive_counters", {}).get(ckey, 0)
            if current >= max_cnt:
                continue
            context.setdefault("passive_counters", {})[ckey] = current + 1
        
        # 执行actions
        for action in entry.get("actions", []):
            result = _execute_action(action, unit, sk_lv, allies, enemies, context, hid)
            if result:
                results.append(result)
        
        # 处理lv5/lv7额外效果
        for lv_key in ("lv5_extra", "lv7_extra"):
            needed_lv = int(lv_key.replace("lv", "").replace("_extra", ""))
            extra = entry.get(lv_key)
            if extra and sk_lv >= needed_lv:
                result = _execute_action(extra, unit, sk_lv, allies, enemies, context, hid)
                if result:
                    results.append(result)
    
    return results if results else None


def _check_condition(cond, unit):
    """检查效果触发条件"""
    if not cond:
        return True
    ctype = cond.get("type")
    if ctype == "hp_threshold":
        hp_pct = unit["hp"] / max(1, unit["max_hp"])
        return hp_pct < cond.get("pct", 0.3)
    elif ctype == "hp_below":
        hp_pct = unit["hp"] / max(1, unit["max_hp"])
        return hp_pct < cond.get("pct", 0.2)
    elif ctype == "hp_above":
        hp_pct = unit["hp"] / max(1, unit["max_hp"])
        return hp_pct > cond.get("pct", 0.5)
    return True


def execute_other_condition(cond, allies):
    """独立检查非自身条件（如华佗检查队友HP）"""
    if not cond:
        return True
    ctype = cond.get("type")
    if ctype == "hp_threshold":
        lowest = min([a for a in allies if a.get("alive", True)],
                     key=lambda x: x["hp"] / max(1, x["max_hp"]), default=None)
        if not lowest:
            return False
        return lowest["hp"] / max(1, lowest["max_hp"]) < cond.get("pct", 0.3)
    return True


def _apply_upgrade(base_val, sk_lv, per_lv):
    """技能等级升级值"""
    if not per_lv:
        return base_val
    for lv, val in sorted(per_lv.items(), key=lambda x: -x[0]):
        if sk_lv >= lv:
            return val
    return base_val


def _execute_action(action, unit, sk_lv, allies, enemies, context, hid):
    """执行单个效果动作"""
    atype = action.get("type")
    if not atype:
        return None
    
    # 统一处理 per_lv 和 min_lv
    effective_action = dict(action)
    if action.get("per_lv"):
        for k, v in action.items():
            if k in ("pct", "val", "dur"):
                effective_action[k] = _apply_upgrade(action[k], sk_lv, action["per_lv"])
    if action.get("min_lv") and sk_lv < action["min_lv"]:
        return None
    
    # --- 治疗型 ---
    if atype == "heal_team":
        pct = effective_action.get("pct", 0.15)
        heal = 0
        for a in allies:
            if a.get("alive", True):
                h = int(a["max_hp"] * pct)
                a["hp"] = min(a["max_hp"], a["hp"] + h)
                heal += h
        if heal > 0:
            context.setdefault("all_actions", []).append({
                "side": "heal", "type": "passive", "attacker_name": unit["name"],
                "skill": "被动", "aoe": True,
                "targets": [{"name": a["name"], "heal": int(a["max_hp"] * pct)}
                           for a in allies if a.get("alive", True)]
            })
            return {"msg": f"治疗全体{int(pct*100)}%"}

    elif atype == "heal_lowest_ally":
        pct = effective_action.get("pct", 0.15)
        alive = [a for a in allies if a.get("alive", True)]
        if not alive:
            return None
        lowest = min(alive, key=lambda x: x["hp"] / max(1, x["max_hp"]))
        if lowest["hp"] / max(1, lowest["max_hp"]) >= 0.3:
            return None
        heal = int(lowest["max_hp"] * pct)
        lowest["hp"] = min(lowest["max_hp"], lowest["hp"] + heal)
        context.setdefault("all_actions", []).append({
            "side": "heal", "type": "passive", "attacker_name": unit["name"],
            "skill": "被动", "aoe": False, "target_name": lowest["name"],
            "heal": heal, "msg": f"回复{lowest['name']}{heal}❤️"
        })
        return {"msg": f"回复{lowest['name']}{heal}❤️"}

    # --- Debuff敌人 ---
    elif atype == "debuff_enemies":
        pct = effective_action.get("pct", -0.1)
        stat = action.get("stat", "atk")
        dur = action.get("dur", -1)
        for e in enemies:
            if e.get("alive", True):
                found = False
                for d in e.get("debuffs", []):
                    if d["stat"] == stat:
                        d["pct"] = min(d.get("pct", 0), pct) if pct < 0 else max(d.get("pct", 0), pct)
                        found = True
                        break
                if not found:
                    e["debuffs"].append({"stat": stat, "pct": pct, "dur": dur})
        label = {"atk":"⚔️攻击","crit":"💥暴击","dmg_reduce":"🛡️减伤"}.get(stat, stat)
        dir_str = "↓" if pct < 0 else "↑"
        context.setdefault("all_actions", []).append({
            "side": "enemy", "type": "passive", "attacker_name": unit["name"],
            "skill": "被动", "aoe": True,
            "msg": f"敌人全体{label}{dir_str}{int(abs(pct)*100)}%"
        })

    # --- 魅惑敌人 ---
    elif atype == "charm_enemy":
        dur = action.get("dur", 3)
        alive_e = [e for e in enemies if e.get("alive", True)]
        if alive_e:
            count = 1
            if sk_lv >= 7:
                count = 2
            for _ in range(count):
                if not alive_e:
                    break
                target = random.choice(alive_e)
                target["debuffs"].append({"stat": "charm", "pct": 1.0, "dur": dur})
                context.setdefault("all_actions", []).append({
                    "side": "enemy", "type": "passive", "attacker_name": unit["name"],
                    "skill": "离间", "aoe": False, "target_name": target["name"], "msg": "魅惑!"
                })
                alive_e.remove(target)

    # --- 叠层Buff ---
    elif atype == "stack_buff":
        stat = action.get("stat", "atk")
        pct = effective_action.get("pct", 0.05)
        counter_key = context.get("passive_counters", {}).get(f"{hid}_stacks")
        unit["buffs"].append({"stat": stat, "pct": pct, "dur": -1})
        ctx_all = context.setdefault("all_actions", [])
        ctx_all.append({
            "side": "ally", "type": "passive", "attacker_name": unit["name"],
            "skill": "被动", "aoe": False, "target_name": unit["name"],
            "msg": f"{unit['name']}{'⚔️攻击' if stat=='atk' else '💥暴击' if stat=='crit' else stat}+{int(pct*100)}%"
        })

    # --- HP损失攻击加成 ---
    elif atype == "atk_per_hp_lost":
        pct_per = effective_action.get("pct_per_10pct", 0.08)
        hp_pct = unit["hp"] / max(1, unit["max_hp"])
        lost = 1.0 - hp_pct
        layers = int(lost // 0.1)
        ckey = f"{hid}_hp_lost"
        current = context.get("passive_counters", {}).get(ckey, 0)
        if layers > current:
            context.setdefault("passive_counters", {})[ckey] = layers
            unit["buffs"] = [b for b in unit.get("buffs", [])
                            if b["stat"] not in ("zhaoyun_atk", "zhaoyun_crit")]
            for _ in range(layers):
                unit["buffs"].append({"stat": "zhaoyun_atk", "pct": pct_per, "dur": -1})

    elif atype == "crit_per_hp_lost":
        pct_per = effective_action.get("pct_per_10pct", 0.05)
        if action.get("min_lv") and sk_lv < action["min_lv"]:
            return None
        hp_pct = unit["hp"] / max(1, unit["max_hp"])
        layers = int((1.0 - hp_pct) // 0.1)
        for _ in range(layers):
            unit["buffs"].append({"stat": "crit", "pct": pct_per, "dur": -1})

    # --- 自身Buff ---
    elif atype == "buff_self":
        stat = action.get("stat", "atk")
        pct = effective_action.get("pct", 1.0)
        dur = action.get("dur", -1)
        unit["buffs"].append({"stat": stat, "pct": pct, "dur": dur})
        label = {"atk":"⚔️攻击","crit":"💥暴击","crit_dmg":"🎯爆伤","dmg_reduce":"🛡️减伤",
                 "immune":"🛡️免疫","dodge":"👻闪避","speed":"👟速度","lifesteal":"🩸吸血"}.get(stat, stat)
        dur_str = f"×{dur}次伤害" if dur > 0 and stat == "immune" else (f"×{dur}回合" if dur > 0 else "永久")
        context.setdefault("all_actions", []).append({
            "side": "ally", "type": "passive", "attacker_name": unit["name"],
            "skill": "被动", "aoe": False, "target_name": unit["name"],
            "msg": f"{unit['name']}{label}+{int(pct*100)}%{dur_str}"
        })

    elif atype == "buff_team":
        stat = action.get("stat", "atk")
        pct = effective_action.get("pct", 0.1)
        dur = action.get("dur", -1)
        for a in allies:
            if a.get("alive", True):
                a["buffs"].append({"stat": stat, "pct": pct, "dur": dur})
        label = {"atk":"⚔️攻击","crit":"💥暴击","crit_dmg":"🎯爆伤","dmg_reduce":"🛡️减伤",
                 "immune":"🛡️免疫","dodge":"👻闪避"}.get(stat, stat)
        dur_str = f"×{dur}次伤害" if dur > 0 and stat == "immune" else (f"×{dur}回合" if dur > 0 else "永久")
        context.setdefault("all_actions", []).append({
            "side": "ally", "type": "passive", "attacker_name": unit["name"],
            "skill": "被动", "aoe": True,
            "msg": f"全体{label}+{int(pct*100)}%{dur_str}"
        })

    elif atype == "buff_self_if_hp_above":
        hp_pct = action.get("hp_pct", 0.5)
        if unit["hp"] / max(1, unit["max_hp"]) >= hp_pct:
            stat = action.get("stat", "atk")
            pct = effective_action.get("pct", 0.2)
            unit["buffs"].append({"stat": stat, "pct": pct, "dur": -1})

    elif atype == "buff_self_below_hp":
        hp_pct = action.get("hp_pct", 0.2)
        if unit["hp"] / max(1, unit["max_hp"]) < hp_pct:
            stat = action.get("stat", "immune")
            pct = action.get("pct", 1.0)
            dur = action.get("dur", 1)
            unit["buffs"].append({"stat": stat, "pct": pct, "dur": dur})

    elif atype == "reflect_on_crit":
        pass  # 在受击时由战斗代码处理, 这里不作修改

    elif atype == "chain_enemies":
        dur = effective_action.get("dur", 2)
        for e in enemies:
            if e.get("alive", True):
                e["debuffs"].append({"stat": "chained", "pct": 1.0, "dur": dur})
        context.setdefault("all_actions", []).append({
            "side": "enemy", "type": "passive", "attacker_name": unit["name"],
            "skill": "连锁", "aoe": True,
            "msg": "锁住全体敌人!"
        })

    # --- 神卡效果 ---
    elif atype == "shield_team":
        pct = effective_action.get("pct", 0.3)
        for b in unit.get("buffs", []):
            if b["stat"] == "shield_pct":
                pct += b["pct"]
        for a in allies:
            if a.get("alive", True):
                a["shield"] = int(a["max_hp"] * pct)
        context.setdefault("all_actions", []).append({
            "side": "ally", "type": "passive", "attacker_name": unit["name"],
            "skill": "被动", "aoe": True,
            "msg": f"全体护盾{int(pct*100)}%"
        })

    elif atype == "shield_team_from_self":
        # 基于自身血量给队友护盾(蚩尤)
        pct = effective_action.get("pct", 0.5)
        for b in unit.get("buffs", []):
            if b["stat"] == "shield_pct":
                pct += b["pct"]
        shield_val = int(unit["max_hp"] * pct)
        for a in allies:
            if a.get("alive", True):
                a["shield"] = shield_val
        context.setdefault("all_actions", []).append({
            "side": "ally", "type": "passive", "attacker_name": unit["name"],
            "skill": "兵主", "aoe": True,
            "msg": f"蚩尤之力！全体护盾{shield_val}"
        })

    elif atype == "debuff_highest_hp_enemy":
        hp_reduce = effective_action.get("hp_reduce_pct", 0.5)
        alive_e = [e for e in enemies if e.get("alive", True)]
        if alive_e:
            target = max(alive_e, key=lambda x: x["max_hp"])
            reduced_hp = int(target["max_hp"] * (1 - hp_reduce))
            target["max_hp"] = reduced_hp
            target["hp"] = min(target["hp"], reduced_hp)

    elif atype == "full_energy_self":
        unit["energy"] = 200
        context.setdefault("all_actions", []).append({
            "side": "ally", "type": "passive", "attacker_name": unit["name"],
            "skill": "被动", "aoe": False, "msg": "满能量!"
        })

    elif atype == "heal_self":
        pct = effective_action.get("pct", 0.1)
        heal = int(unit["max_hp"] * pct)
        unit["hp"] = min(unit["max_hp"], unit["hp"] + heal)

    # --- 复活 ---
    elif atype == "revive_ally_on_death":
        # 女娲lv5被动: 队友死亡时复活一次
        mx = action.get("max_per_battle", 1)
        context.setdefault("_revive_ally", {"count": 0, "max": mx, "unit_name": unit["name"]})
    
    elif atype == "revive_team_invincible":
        # 女娲lv7被动: 复活时全队无敌1次伤害
        context.setdefault("_revive_team_invincible", True)
    
    elif atype == "revive_on_death":
        # 刑天lv7被动: 首次死亡复活
        pct = effective_action.get("pct", 0.60)
        unit["_revive_on_death"] = pct

    return None


def check_death_revive(target_unit, allies, enemies, context):
    """检查单位死亡时是否有复活效果，有则复活并返回True。
    
    支持:
    - 女娲被动 revive_ally_on_death (队友死亡时复活)
    - 刑天被动 revive_on_death (自身死亡时复活)
    """
    hid = target_unit.get("_hd", {}).get("id")
    
    # 刑天自身复活
    rpct = target_unit.pop("_revive_on_death", None)
    if rpct:
        target_unit["alive"] = True
        target_unit["hp"] = int(target_unit["max_hp"] * rpct)
        target_unit["buffs"] = []
        target_unit["debuffs"] = []
        target_unit["stunned"] = False
        target_unit["frozen"] = False
        context.setdefault("all_actions", []).append({
            "side": "heal", "type": "passive", "attacker_name": target_unit["name"],
            "skill": "不屈", "aoe": False,
            "msg": f"{target_unit['name']}复活{int(rpct*100)}%!"
        })
        return True
    
    # 女娲队友复活
    ra = context.get("_revive_ally")
    if ra and ra["count"] < ra["max"]:
        ra["count"] += 1
        target_unit["alive"] = True
        target_unit["hp"] = int(target_unit["max_hp"] * 0.30)
        target_unit["buffs"] = []
        target_unit["debuffs"] = []
        target_unit["stunned"] = False
        target_unit["frozen"] = False
        # lv7: 复活时全队无敌
        if context.get("_revive_team_invincible"):
            for a in allies:
                if a.get("alive", True):
                    a["buffs"].append({"stat": "immune", "pct": 1.0, "dur": 1})
        context.setdefault("all_actions", []).append({
            "side": "heal", "type": "passive", "attacker_name": ra["unit_name"],
            "skill": "创世", "aoe": False,
            "msg": f"{ra['unit_name']}复活了{target_unit['name']}!"
        })
        return True
    
    return False


# ============================================================
# 旧接口兼容 — 逐步迁移
# ============================================================

def process_all_battle_start(allies, enemies, context):
    """处理所有英雄的战斗开始被动"""
    results = []
    for u in allies:
        hd = u.get("_hd")
        sk_lv = u.get("_skill_lv", 1)
        if not hd:
            continue
        r = process_effects("on_battle_start", u, hd, sk_lv, allies, enemies, context)
        if r:
            results.extend(r)
    return results


def process_all_post_action(allies, enemies, context, unit, act):
    """处理行动后的所有被动检查(on_kill, on_hit, on_ally_low_hp, on_ally_die)"""
    results = []
    for pu in allies:
        phd = pu.get("_hd")
        if not phd or not pu.get("alive", True):
            continue
        psk_lv = pu.get("_skill_lv", 1)
        
        # on_kill — 击杀触发
        if unit.get("side") == "ally" and act.get("killed"):
            r = process_effects("on_kill", pu, phd, psk_lv, allies, enemies, context)
            if r:
                results.extend(r)
        
        # on_hit — 受击触发
        if unit.get("side") == "enemy":
            is_target = (pu.get("name") == act.get("target_name"))
            is_aoe_target = (
                act.get("aoe") and act.get("targets") and
                any(tg.get("name") == pu.get("name") for tg in act.get("targets", []))
            )
            if is_target or is_aoe_target:
                r = process_effects("on_hit", pu, phd, psk_lv, allies, enemies, context)
                if r:
                    results.extend(r)
        
        # on_ally_low_hp — 队友低血(华佗)
        r2 = process_effects("on_ally_low_hp", pu, phd, psk_lv, allies, enemies, context)
        if r2:
            results.extend(r2)
        
        # on_ally_die — 队友死亡(蔡文姬)
        if act.get("killed") and any(
            u.get("name") == act.get("target_name") and u.get("alive") == False
            for u in allies
        ):
            r3 = process_effects("on_ally_die", pu, phd, psk_lv, allies, enemies, context)
            if r3:
                results.extend(r3)
    
    return results
