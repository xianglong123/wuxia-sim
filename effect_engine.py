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
    # 蚩尤: 兵主 — 战斗开始全体+30%攻击+15%减伤
    "chiyou": [{
        "event": "on_battle_start",
        "actions": [{"type": "buff_team", "stat": "atk", "pct": 0.30, "dur": -1, "per_lv": {3: 0.50}},
                     {"type": "buff_team", "stat": "dmg_reduce", "pct": 0.15, "dur": -1, "per_lv": {3: 0.25}}],
        "lv5_extra": {"type": "team_invincible_if_hp_above", "pct": 0.70},
        "lv7_extra": {"type": "self_buff_on_invincible_tigger"}
    }],
}


# ============================================================
# 引擎核心
# ============================================================

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
                # 如果已存在, 更新
                for d in e.get("debuffs", []):
                    if d["stat"] == stat:
                        d["pct"] = min(d.get("pct", 0), pct) if pct < 0 else max(d.get("pct", 0), pct)
                        found = True
                        break
                if not found:
                    e["debuffs"].append({"stat": stat, "pct": pct, "dur": dur})

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

    elif atype == "buff_team":
        stat = action.get("stat", "atk")
        pct = effective_action.get("pct", 0.1)
        dur = action.get("dur", -1)
        for a in allies:
            if a.get("alive", True):
                a["buffs"].append({"stat": stat, "pct": pct, "dur": dur})

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
        for a in allies:
            if a.get("alive", True):
                a["shield"] = int(a["max_hp"] * pct)

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

    return None


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
