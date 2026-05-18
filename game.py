#!/usr/bin/env python3
"""🏯 江湖经营 - 速度轴+能量+卡牌战斗版"""
import json, urllib.request, ssl, random, os, sys, re, math, sqlite3, copy
from datetime import datetime
from pathlib import Path
from functools import wraps
from flask import Flask, render_template, jsonify, session, redirect, request
from werkzeug.security import generate_password_hash, check_password_hash
from effect_engine import process_all_battle_start, process_all_post_action, process_effects, apply_equip_passive_buffs, process_equip_battle_start, check_death_revive

app = Flask(__name__)
app.secret_key = "wuxia-hero-online"

try: import certifi; SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except: SSL_CTX = ssl.create_default_context()

def get_api_key():
    f = Path.home() / ".hermes" / ".env"
    if f.exists():
        for l in open(f):
            if l.startswith("DEEPSEEK_API_KEY="):
                return l.split("=",1)[1].strip()
    return os.environ.get("DEEPSEEK_API_KEY", "")
API_KEY = get_api_key()

# ═══ DB ═══
DB_PATH = Path(__file__).parent / "data" / "wuxia.db"
def get_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, created_at TEXT DEFAULT (datetime('now','localtime')))")
    conn.execute("CREATE TABLE IF NOT EXISTS game_saves (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER UNIQUE NOT NULL, game_data TEXT NOT NULL, updated_at TEXT DEFAULT (datetime('now','localtime')), FOREIGN KEY (user_id) REFERENCES users(id))")
    conn.execute("CREATE TABLE IF NOT EXISTS invite_codes (code TEXT PRIMARY KEY, max_uses INTEGER DEFAULT 10, used_count INTEGER DEFAULT 0, created_at TEXT DEFAULT (datetime('now','localtime')))")
    for seed in ["WUXIA2026","JIANGHU","WANFA","LONGCHENG","YIJIAN"]:
        conn.execute("INSERT OR IGNORE INTO invite_codes (code) VALUES (?)", (seed,))
    return conn

def _prefix(path):
    """如果请求通过 /wuxia 前缀进来，给重定向路径也加 /wuxia"""
    p = request.path
    if p.startswith("/wuxia/") or p == "/wuxia":
        return "/wuxia" + path
    return path

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            if request.path.startswith("/api/"):
                return jsonify({"error":"未登录","need_login":True})
            return redirect(_prefix("/login"))
        return f(*args, **kwargs)
    return decorated

# ═══ Auth Routes ═══
@app.route("/login")
def login_page():
    return render_template("login.html")

@app.route("/api/register", methods=["POST"])
def api_register():
    data = request.get_json()
    if not data: return jsonify({"error":"无效的请求"})
    username = data.get("username","").strip()
    password = data.get("password","")
    if len(username) < 2: return jsonify({"error":"用户名至少2个字符"})
    if len(password) < 4: return jsonify({"error":"密码至少4个字符"})
    invite = data.get("invite","").strip().upper()
    if not invite: return jsonify({"error":"请输入邀请码"})
    conn = get_db()
    ic=conn.execute("SELECT used_count, max_uses FROM invite_codes WHERE code=?", (invite,)).fetchone()
    if not ic:
        conn.close(); return jsonify({"error":"邀请码无效"})
    if ic["used_count"] >= ic["max_uses"]:
        conn.close(); return jsonify({"error":"该邀请码已达使用上限"})
    if conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone():
        conn.close(); return jsonify({"error":"用户名已存在"})
    conn.execute("UPDATE invite_codes SET used_count=used_count+1 WHERE code=?", (invite,))
    conn.execute("INSERT INTO users (username, password_hash) VALUES (?,?)", (username, generate_password_hash(password)))
    conn.commit()
    uid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    g = new_game_inner()
    conn.execute("INSERT OR REPLACE INTO game_saves (user_id, game_data) VALUES (?,?)", (uid, json.dumps(g, ensure_ascii=False)))
    conn.commit(); conn.close()
    session["user_id"] = uid; session["username"] = username
    return jsonify({"ok":True, "username":username})

@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json()
    if not data: return jsonify({"error":"无效的请求"})
    username = data.get("username","").strip()
    password = data.get("password","")
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    conn.close()
    if not user or not check_password_hash(user["password_hash"], password):
        return jsonify({"error":"用户名或密码错误"})
    session["user_id"] = user["id"]; session["username"] = user["username"]
    return jsonify({"ok":True, "username":user["username"]})

@app.route("/api/logout")
def api_logout():
    session.clear(); return jsonify({"ok":True})

@app.route("/api/me")
def api_me():
    if "user_id" in session:
        return jsonify({"logged_in":True, "username":session.get("username","")})
    return jsonify({"logged_in":False})

def load_game():
    uid = session.get("user_id")
    if not uid: return None
    conn = get_db()
    row = conn.execute("SELECT game_data FROM game_saves WHERE user_id=?", (uid,)).fetchone()
    conn.close()
    if row:
        g = json.loads(row["game_data"])
        g.setdefault("stage_index",0); g.setdefault("msg",""); g.setdefault("battle_result",None)
        g.setdefault("last_active_time",datetime.now().timestamp()); g.setdefault("offline_msg","")
        for inv in g.get("inventory",[]):
            inv.setdefault("level",1); inv.setdefault("exp",0)
        for eb in g.get("equip_bag",[]):
            if isinstance(eb,dict): eb.setdefault("upgrade_lv",0)
        return g
    return None

def save_game(g):
    uid = session.get("user_id")
    if not uid: return
    conn = get_db()
    data = json.dumps(g, ensure_ascii=False)
    conn.execute("INSERT OR REPLACE INTO game_saves (user_id, game_data, updated_at) VALUES (?,?,datetime('now','localtime'))", (uid, data))
    conn.commit(); conn.close()

# ═══════════════════════════════════════════
# 英雄数据（速度轴+能量+普攻版）
# ═══════════════════════════════════════════
HERO_DATA = {}
def reg(h): HERO_DATA[h["id"]] = h; return h

# 凡品
reg({"id":"lisi","name":"李四","class":"战士","quality":"凡品","color":"#888",
    "hp":800,"atk":100,"crit":5,"spd":90,"skill_cost":70,
    "skill_name":"乱砍","skill_desc":"对单个敌人造成80%伤害，30%概率降低目标攻击10%×2回合",
    "skill_aoe":False,"skill_target":"single","skill_special":[],"skill_debuffs":[{"stat":"atk","pct":-0.1,"dur":2,"chance":0.3}],
    "skill_upgrades":{3:"伤害120%, 削弱概率50%", 9:"#1攻击+15%"},
    "basic_name":"横劈","basic_desc":"挥刀横斩，造成100%伤害","basic_dmg_pct":1.0,"basic_energy_gain":40,
    "basic_aoe":False,"basic_target":"single","basic_special":[],
    "passive_name":"蛮力","passive_desc":"攻击力+10%","passive_upgrades":{3:"攻击力+20%"}})
reg({"id":"wangdazhuang","name":"王大壮","class":"肉盾","quality":"凡品","color":"#888",
    "hp":2000,"atk":60,"crit":2,"spd":80,"skill_cost":80,
    "skill_name":"站住别跑","skill_desc":"嘲讽一个敌人，自身减伤30%×2回合",
    "skill_aoe":False,"skill_target":"single","skill_special":["taunt"],"skill_buffs":[{"stat":"dmg_reduce","pct":0.3,"dur":2}],
    "skill_upgrades":{3:"嘲讽两个敌人，减伤50%", 9:"#2血量+20%"},
    "basic_name":"盾击","basic_desc":"盾牌猛击，造成80%伤害并自身减伤5%×1回合","basic_dmg_pct":0.8,"basic_energy_gain":40,
    "basic_aoe":False,"basic_target":"single","basic_special":[{"type":"self_buff","stat":"dmg_reduce","pct":0.05,"dur":1}],
    "passive_name":"皮厚","passive_desc":"血量+15%","passive_upgrades":{3:"血量+25%"}})
reg({"id":"xiaocui","name":"小翠","class":"奶妈","quality":"凡品","color":"#888",
    "hp":600,"atk":50,"crit":2,"spd":105,"skill_cost":80,
    "skill_name":"包扎","skill_desc":"回复一个队友20%血量，增加防御15%×2回合",
    "skill_aoe":False,"skill_target":"lowest_hp_ally","skill_heal_pct":0.2,
    "skill_buffs":[{"stat":"dmg_reduce","pct":0.15,"dur":2}],
    "skill_upgrades":{3:"回复30%，防御加成25%", 9:"#3治疗+15%"},
    "basic_name":"针灸","basic_desc":"银针轻刺，回复最低血量队友15%","basic_dmg_pct":0,"basic_energy_gain":50,
    "basic_aoe":False,"basic_target":"lowest_hp_ally","basic_special":[{"type":"heal","pct":0.15}],
    "passive_name":"细心","passive_desc":"治疗量+10%","passive_upgrades":{3:"治疗量+20%"}})
reg({"id":"zhangtiezhu","name":"张铁柱","class":"射手","quality":"凡品","color":"#888",
    "hp":700,"atk":120,"crit":8,"spd":120,"skill_cost":70,
    "skill_name":"扔石头","skill_desc":"对单个敌人造成100%伤害，20%概率眩晕",
    "skill_aoe":False,"skill_target":"single","skill_special":["stun"],
    "skill_upgrades":{3:"伤害120%，眩晕概率35%", 9:"#4暴击+8%"},
    "basic_name":"射击","basic_desc":"瞄准射击，造成120%伤害","basic_dmg_pct":1.2,"basic_energy_gain":50,
    "basic_aoe":False,"basic_target":"single","basic_special":[],
    "passive_name":"鹰眼","passive_desc":"命中率+10%","passive_upgrades":{3:"命中率+20%"}})

# 良品
reg({"id":"huangzhong","name":"黄忠老将","class":"射手","quality":"良品","color":"#5adb7a",
    "hp":1000,"atk":220,"crit":15,"spd":95,"skill_cost":90,
    "skill_name":"百步穿杨","skill_desc":"狙击敌方后排，造成200%伤害，必定暴击",
    "skill_aoe":False,"skill_target":"back_row","skill_special":["guaranteed_crit"],
    "skill_upgrades":{3:"伤害250%，爆伤翻倍",5:"攻击后排全体", 9:"#4攻击+20%"},
    "basic_name":"劲射","basic_desc":"强力射击，造成120%伤害","basic_dmg_pct":1.2,"basic_energy_gain":50,
    "basic_aoe":False,"basic_target":"single","basic_special":[],
    "passive_name":"老当益壮","passive_desc":"高于50%血量时攻击+20%","passive_upgrades":{3:"攻击+35%",5:"触发条件降至30%"}})
reg({"id":"guojia","name":"郭奉孝","class":"法师","quality":"良品","color":"#5adb7a",
    "hp":800,"atk":280,"crit":12,"spd":135,"skill_cost":110,
    "skill_name":"冰霜术","skill_desc":"对全体敌人造成80%伤害，40%概率冰冻1回合",
    "skill_aoe":True,"skill_target":"all_enemy","skill_special":["freeze"],
    "skill_upgrades":{3:"冰冻概率60%，伤害120%",5:"冰冻持续2回合", 9:"#5能量+40"},
    "basic_name":"凝冰","basic_desc":"冰晶飞射，造成80%伤害，10%概率冰冻","basic_dmg_pct":0.8,"basic_energy_gain":40,
    "basic_aoe":False,"basic_target":"single","basic_special":[{"type":"debuff","stat":"stun","chance":0.1,"dur":1}],
    "passive_name":"奇谋","passive_desc":"战斗开始回复全体15%血量","passive_upgrades":{3:"回复25%",5:"额外增加10%攻击buff"}})
reg({"id":"yanshisan","name":"燕十三","class":"刺客","quality":"良品","color":"#5adb7a",
    "hp":500,"atk":350,"crit":35,"spd":170,"skill_cost":90,
    "skill_name":"背刺","skill_desc":"对血量最低敌人造成250%伤害，击杀后刷新技能",
    "skill_aoe":False,"skill_target":"lowest_hp","skill_special":["refresh_on_kill"],
    "skill_upgrades":{3:"伤害350%",5:"击杀后攻击+20%×2回合", 9:"#6暴击+15%"},
    "basic_name":"暗影刺","basic_desc":"迅捷突刺，造成130%伤害，能量回复+10","basic_dmg_pct":1.3,"basic_energy_gain":50,
    "basic_aoe":False,"basic_target":"single","basic_special":[],
    "passive_name":"影步","passive_desc":"闪避率+15%","passive_upgrades":{3:"闪避+25%",5:"闪避后回血10%"}})
reg({"id":"zhoucang","name":"周仓","class":"肉盾","quality":"良品","color":"#5adb7a",
    "hp":2800,"atk":80,"crit":3,"spd":85,"skill_cost":80,
    "skill_name":"护卫","skill_desc":"为最低血量队友承担50%伤害×3秒，自身减伤20%",
    "skill_aoe":False,"skill_target":"lowest_hp_ally","skill_special":["protect"],
    "skill_upgrades":{3:"承伤降低40%持续4秒",5:"保护期间自身回血10%"},
    "basic_name":"掩护","basic_desc":"横盾格挡，造成70%伤害并援护最低血量队友","basic_dmg_pct":0.7,"basic_energy_gain":40,
    "basic_aoe":False,"basic_target":"single","basic_special":[{"type":"protect","pct":0.3}],
    "passive_name":"忠勇","passive_desc":"保护队友时自身回复5%","passive_upgrades":{3:"回复10%",5:"回复全体5%"}})

# 极品
reg({"id":"xiahoudun","name":"夏侯惇","class":"战士","quality":"极品","color":"#4a8eff",
    "hp":2800,"atk":340,"crit":15,"spd":100,"skill_cost":120,
    "skill_name":"拔矢啖睛","skill_desc":"自损10%血量，对全体造成血量×6伤害，50%吸血",
    "skill_aoe":True,"skill_target":"all_enemy","skill_special":["lifesteal"],
    "skill_upgrades":{3:"伤害系数×10，自损5%",5:"获得护盾(吸收30%最大血量)", 9:"#1血量+30%+反伤"},
    "basic_name":"怒斩","basic_desc":"含怒挥刀，造成100%伤害，吸血20%","basic_dmg_pct":1.0,"basic_energy_gain":40,
    "basic_aoe":False,"basic_target":"single","basic_special":[{"type":"lifesteal","pct":0.2}],
    "passive_name":"刚烈","passive_desc":"受到暴击时反弹150%伤害","passive_upgrades":{3:"反弹200%",5:"任何攻击30%反弹"}})
reg({"id":"caiwenji","name":"蔡文姬","class":"奶妈","quality":"极品","color":"#4a8eff",
    "hp":1500,"atk":180,"crit":8,"spd":115,"skill_cost":130,
    "skill_name":"胡笳十八拍","skill_desc":"全体回复20%+驱散所有负面+攻击+20%×2回合",
    "skill_aoe":True,"skill_target":"all_ally","skill_heal_pct":0.2,"skill_special":["cleanse"],"skill_buffs":[{"stat":"atk","pct":0.2,"dur":2}],
    "skill_upgrades":{3:"额外获得30%护盾",5:"攻击加成提升至35%", 9:"#3护盾25%"},
    "basic_name":"抚琴","basic_desc":"轻拨琴弦，全体回复8%血量","basic_dmg_pct":0,"basic_energy_gain":50,
    "basic_aoe":True,"basic_target":"all_ally","basic_special":[{"type":"heal","pct":0.08}],
    "passive_name":"悲歌","passive_desc":"队友死亡时全体回复25%","passive_upgrades":{3:"回复40%",5:"触发时自身无敌2秒"}})
reg({"id":"ganning","name":"甘宁","class":"刺客","quality":"极品","color":"#4a8eff",
    "hp":900,"atk":400,"crit":40,"spd":145,"skill_cost":110,
    "skill_name":"锦帆夜袭","skill_desc":"突袭敌方后排全体，造成180%伤害，暴击时击晕",
    "skill_aoe":True,"skill_target":"back_row","skill_special":["stun_on_crit"],
    "skill_upgrades":{3:"伤害250%，击杀后额外行动",5:"必定暴击，伤害350%", 9:"#6攻击+25%"},
    "basic_name":"铃铛斩","basic_desc":"铃响刀至，造成120%伤害","basic_dmg_pct":1.2,"basic_energy_gain":50,
    "basic_aoe":False,"basic_target":"single","basic_special":[],
    "passive_name":"铃铛","passive_desc":"战斗开始降低敌方全体10%攻击","passive_upgrades":{3:"降低20%",5:"额外降低5%暴击"}})
reg({"id":"dianwei","name":"典韦","class":"肉盾","quality":"极品","color":"#4a8eff",
    "hp":4000,"atk":200,"crit":8,"spd":88,"skill_cost":100,
    "skill_name":"古之恶来","skill_desc":"狂暴:攻击+60%+吸血40%+反弹50%×3回合",
    "skill_aoe":False,"skill_target":"self","skill_special":["lifesteal","reflect"],"skill_buffs":[{"stat":"atk","pct":0.6,"dur":3}],
    "skill_upgrades":{3:"狂暴期间免疫控制",5:"结束时对全体造成200%伤害", 9:"#2血量+40%"},
    "basic_name":"巨力挥击","basic_desc":"铁戟横扫，造成120%伤害，回复自身5%血量","basic_dmg_pct":1.2,"basic_energy_gain":40,
    "basic_aoe":False,"basic_target":"single","basic_special":[{"type":"self_heal","pct":0.05}],
    "passive_name":"死战","passive_desc":"血量低于20%时攻击翻倍","passive_upgrades":{3:"触发阈值30%",5:"血量低于20%无敌2秒"}})

# 绝品
reg({"id":"yangyouji","name":"养由基","class":"射手","quality":"绝品","color":"#b84aff",
    "hp":1800,"atk":520,"crit":30,"spd":110,"skill_cost":110,
    "skill_name":"穿云箭","skill_desc":"穿透全体敌人造成200%伤害，无视护盾，暴击伤害翻倍",
    "skill_aoe":True,"skill_target":"all_enemy","skill_special":["ignore_shield"],
    "skill_upgrades":{3:"穿透后暴击率+30%",5:"暴击时4倍伤害",7:"一箭双雕:攻击两次", 9:"#4攻击+30%+首击必暴"},
    "basic_name":"精准射击","basic_desc":"百步穿杨，造成150%伤害","basic_dmg_pct":1.5,"basic_energy_gain":50,
    "basic_aoe":False,"basic_target":"single","basic_special":[],
    "passive_name":"百发百中","passive_desc":"无视闪避，暴击率+15%","passive_upgrades":{3:"暴击率+25%",5:"爆伤+50%",7:"每暴击一次攻击+5%"}})
reg({"id":"luobu","name":"吕布","class":"战士","quality":"绝品","color":"#b84aff",
    "hp":3500,"atk":480,"crit":18,"spd":130,"skill_cost":130,
    "skill_name":"方天画戟","skill_desc":"横扫前排全体造成180%伤害，降低目标攻击20%×2回合",
    "skill_aoe":True,"skill_target":"front_row","skill_debuffs":[{"stat":"atk","pct":-0.2,"dur":2}],
    "skill_upgrades":{3:"击退附带眩晕1回合",5:"只命中一个敌人时伤害翻倍",7:"技能范围扩大至全体", 9:"#1攻击+40%"},
    "basic_name":"横戟","basic_desc":"方天画戟横扫，对前排全体造成80%伤害","basic_dmg_pct":0.8,"basic_energy_gain":40,
    "basic_aoe":True,"basic_target":"front_row","basic_special":[],
    "passive_name":"无双","passive_desc":"每击败一个敌人攻击+20%(最多3层)","passive_upgrades":{3:"每层+25%最多4层",5:"每层额外+10%暴击",7:"满层技能无冷却"}})
reg({"id":"zhugeliang","name":"诸葛亮","class":"法师","quality":"绝品","color":"#b84aff",
    "hp":2200,"atk":420,"crit":20,"spd":140,"skill_cost":130,
    "skill_name":"借东风","skill_desc":"召唤暴风攻击全体敌人，造成150%伤害+降低攻击20%×3回合",
    "skill_aoe":True,"skill_target":"all_enemy","skill_debuffs":[{"stat":"atk","pct":-0.2,"dur":3}],
    "skill_upgrades":{3:"暴风附带闪电:额外50%伤害",5:"降低攻击30%+减速",7:"暴风持续3回合叠加", 9:"#5能量+60+技能增伤"},
    "basic_name":"羽扇纶巾","basic_desc":"轻摇羽扇，造成100%伤害","basic_dmg_pct":1.0,"basic_energy_gain":50,
    "basic_aoe":False,"basic_target":"single","basic_special":[],
    "passive_name":"空城计","passive_desc":"血量低于30%时隐身2秒","passive_upgrades":{3:"隐身每秒回血5%",5:"隐身结束全队回血10%",7:"隐身技能加速2倍"}})
reg({"id":"pangtong","name":"庞统","class":"法师","quality":"绝品","color":"#b84aff",
    "hp":1800,"atk":460,"crit":22,"spd":125,"skill_cost":120,
    "skill_name":"连环计","skill_desc":"对全体敌人施加锁链造成120%伤害+灼烧(每回合15%×2回合)",
    "skill_aoe":True,"skill_target":"all_enemy","skill_special":["burn"],
    "skill_upgrades":{3:"灼烧期间无法治疗",5:"锁链爆炸额外150%伤害",7:"灼烧传播至新敌人", 9:"#5锁链全体易伤"},
    "basic_name":"锁链击","basic_desc":"铁索横空，造成100%伤害并5%灼烧×2回合","basic_dmg_pct":1.0,"basic_energy_gain":40,
    "basic_aoe":False,"basic_target":"single","basic_special":[{"type":"burn","pct":0.05,"dur":2}],
    "passive_name":"铁索连舟","passive_desc":"战斗开始锁住全体敌人2秒","passive_upgrades":{3:"锁住3秒",5:"锁住期间受伤+30%",7:"解锁时造成200%伤害"}})

# 传说
reg({"id":"lihai","name":"李白","class":"战士","quality":"传说","color":"#ffd700",
    "hp":2800,"atk":580,"crit":25,"spd":160,"skill_cost":140,
    "skill_name":"青莲剑诀","skill_desc":"掷出佩剑化为漫天剑光，攻击全体敌人3次，每剑80%伤害",
    "skill_aoe":True,"skill_target":"all_enemy","skill_dmg_pct":0.8,"skill_special":["multi_hit"],
    "skill_upgrades":{3:"第四剑追击+暴击率+20%",5:"剑气纵横:每剑120%",7:"剑开天门:9999真实伤害必定暴击", 9:"#6攻击+50%+15%吸血"},
    "basic_name":"月下独酌","basic_desc":"剑光如月，攻击2次每次80%伤害","basic_dmg_pct":0.8,"basic_energy_gain":45,
    "basic_aoe":False,"basic_target":"single","basic_special":[{"type":"multi_hit","count":2}],
    "passive_name":"斗酒诗百篇","passive_desc":"每击败一个敌人攻击+12%(最多5层)","passive_upgrades":{3:"上限8层",5:"每层+20%",7:"满层技能必定暴击"}})
reg({"id":"zhangfei","name":"张飞","class":"肉盾","quality":"传说","color":"#ffd700",
    "hp":4800,"atk":320,"crit":10,"spd":80,"skill_cost":140,
    "skill_name":"当阳怒吼","skill_desc":"全屏嘲讽全体敌人3回合，全体队友获得护盾(吸收25%最大血量)",
    "skill_aoe":True,"skill_target":"all_enemy","skill_special":["taunt","shield_ally"],
    "skill_upgrades":{3:"怒吼降低敌人攻击25%",5:"护盾破碎爆炸",7:"全体队友无敌1回合", 9:"#2血量+50%+护盾30%"},
    "basic_name":"蛇矛突刺","basic_desc":"丈八蛇矛突刺，造成120%伤害，自身减伤5%×1回合","basic_dmg_pct":1.2,"basic_energy_gain":40,
    "basic_aoe":False,"basic_target":"single","basic_special":[{"type":"self_buff","stat":"dmg_reduce","pct":0.05,"dur":1}],
    "passive_name":"万人敌","passive_desc":"每受一次攻击+5%攻击(最多10层)","passive_upgrades":{3:"上限15层",5:"每层额外+5%减伤",7:"满层反击100%"}})
reg({"id":"diaochan","name":"貂蝉","class":"刺客","quality":"传说","color":"#ffd700",
    "hp":1800,"atk":650,"crit":45,"spd":200,"skill_cost":130,
    "skill_name":"闭月之舞","skill_desc":"闪避攻击后瞬移至后排连刺，对全体敌人造成150%伤害，必定暴击",
    "skill_aoe":True,"skill_target":"all_enemy","skill_special":["guaranteed_crit"],
    "skill_upgrades":{3:"击杀后刷新闪避",5:"刺击附加灼烧每回合15%×2",7:"溅射周围50%伤害", 9:"#6暴击+15+闪避"},
    "basic_name":"轻舞","basic_desc":"翩若惊鸿，造成80%伤害，闪避下次攻击","basic_dmg_pct":0.8,"basic_energy_gain":50,
    "basic_aoe":False,"basic_target":"single","basic_special":[{"type":"dodge","dur":1}],
    "passive_name":"离间","passive_desc":"战斗开始魅惑一个敌人3秒","passive_upgrades":{3:"被魅惑敌人受伤+30%",5:"魅惑结束眩晕2秒",7:"魅惑两个敌人"}})
reg({"id":"huatuo","name":"华佗","class":"奶妈","quality":"传说","color":"#ffd700",
    "hp":2200,"atk":220,"crit":8,"spd":110,"skill_cost":140,
    "skill_name":"麻沸散","skill_desc":"全体回复30%+免疫伤害2回合+攻击+30%×2回合",
    "skill_aoe":True,"skill_target":"all_ally","skill_heal_pct":0.3,"skill_special":["immunity"],"skill_buffs":[{"stat":"atk","pct":0.3,"dur":2}],
    "skill_upgrades":{3:"免疫期间暴击率+20%",5:"回复40%+附加护盾",7:"免疫结束重置所有冷却", 9:"#3护盾30%+回血15%"},
    "basic_name":"望闻问切","basic_desc":"施以针术，回复最低血量队友20%","basic_dmg_pct":0,"basic_energy_gain":50,
    "basic_aoe":False,"basic_target":"lowest_hp_ally","basic_special":[{"type":"heal","pct":0.2}],
    "passive_name":"妙手回春","passive_desc":"队友低于30%自动回复15%(每场2次)","passive_upgrades":{3:"触发次数+1",5:"回复30%",7:"触发时全队驱散"}})
reg({"id":"zhaoyun","name":"赵云","class":"战士","quality":"传说","color":"#ffd700",
    "hp":3200,"atk":420,"crit":22,"spd":150,"skill_cost":130,
    "skill_name":"七进七出","skill_desc":"冲入敌阵连续冲锋7次，每次对全体敌人造成30%伤害+10%吸血",
    "skill_aoe":True,"skill_target":"all_enemy","skill_dmg_pct":0.3,"skill_special":["multi_hit","lifesteal"],
    "skill_upgrades":{3:"冲锋吸血20%",5:"优先攻击后排",7:"终结一击:15%以下斩杀", 9:"#1开局满能量"},
    "basic_name":"龙胆亮银","basic_desc":"亮银枪出如龙，攻击2次每次100%伤害","basic_dmg_pct":1.0,"basic_energy_gain":40,
    "basic_aoe":False,"basic_target":"single","basic_special":[{"type":"multi_hit","count":2}],
    "passive_name":"一身是胆","passive_desc":"每损失10%血量攻击+8%","passive_upgrades":{3:"每损失10%额外+5%暴击",5:"低于30%无敌1秒",7:"损失血量加成翻倍"}})
reg({"id":"guanyu","name":"关羽","class":"战士","quality":"传说","color":"#ffd700",
    "hp":3500,"atk":500,"crit":28,"spd":155,"skill_cost":120,
    "skill_name":"青龙偃月","skill_desc":"蓄力后挥出惊世一刀，对全体敌人造成300%伤害",
    "skill_aoe":True,"skill_target":"all_enemy","skill_special":["execute"],
    "skill_upgrades":{3:"蓄力期间免疫控制",5:"刀气留痕每秒20%×3回合",7:"血量低于20%直接斩杀", 9:"#1攻击+30%+武圣"},
    "basic_name":"拖刀斩","basic_desc":"拖刀蓄力势如破竹，造成100%伤害","basic_dmg_pct":1.0,"basic_energy_gain":40,
    "basic_aoe":False,"basic_target":"single","basic_special":[],
    "passive_name":"武圣","passive_desc":"开局第一刀必定暴击伤害+50%","passive_upgrades":{3:"第一刀伤害翻倍",5:"前三刀必定暴击",7:"武圣降临:第一次技能真实伤害"}})

# ═══ 神卡 (红色) ═══
reg({"id":"wukong","name":"孙悟空","class":"战士","quality":"神卡","color":"#ff3333",
    "hp":6000,"atk":880,"crit":40,"spd":190,"skill_cost":150,
    "skill_name":"大闹天宫","skill_desc":"金箍棒横扫全体3次，每次150%伤害+40%吸血，必定暴击",
    "skill_aoe":True,"skill_target":"all_enemy","skill_dmg_pct":1.5,"skill_special":["multi_hit","lifesteal","guaranteed_crit"],
    "skill_upgrades":{3:"每次伤害220%，吸血60%",5:"额外2次攻击(共5次)",7:"金箍棒:血量低于15%直接斩杀", 9:"#1攻击+60%+15%吸血"},
    "basic_name":"如意棒","basic_desc":"金箍棒横扫，对全体敌人造成100%伤害","basic_dmg_pct":1.0,"basic_energy_gain":50,
    "basic_aoe":True,"basic_target":"all_enemy","basic_special":[],
    "passive_name":"齐天大圣","passive_desc":"每击败一个敌人攻击+20%(最多5层)","passive_upgrades":{3:"上限8层",5:"每层30%",7:"满层技能暴击伤害+200%"}})
reg({"id":"qinshihuang","name":"秦始皇","class":"法师","quality":"神卡","color":"#ff3333",
    "hp":4000,"atk":820,"crit":30,"spd":140,"skill_cost":150,
    "skill_name":"焚书坑儒","skill_desc":"对全体敌人造成250%伤害+降低攻击40%×3回合+降低暴击20%×3回合",
    "skill_aoe":True,"skill_target":"all_enemy","skill_dmg_pct":2.5,"skill_special":[],
    "skill_debuffs":[{"stat":"atk","pct":-0.4,"dur":3},{"stat":"crit","pct":-0.2,"dur":3}],
    "skill_upgrades":{3:"额外降低防御30%",5:"伤害350%+召唤陶俑护卫",7:"全体沉默+无法治疗×2回合", 9:"#5能量+80+受伤+40%"},
    "basic_name":"法家术","basic_desc":"法家令行禁止，对全体敌人造成90%伤害","basic_dmg_pct":0.9,"basic_energy_gain":50,
    "basic_aoe":True,"basic_target":"all_enemy","basic_special":[],
    "passive_name":"千古一帝","passive_desc":"战斗开始全体敌人攻击-20%+自身攻击+30%","passive_upgrades":{3:"敌人攻击-30%",5:"自身攻击+50%",7:"自身开局满能量"}})
reg({"id":"xingtian","name":"刑天","class":"战士","quality":"神卡","color":"#ff3333",
    "hp":7000,"atk":750,"crit":20,"spd":120,"skill_cost":160,
    "skill_name":"刑天舞干戚","skill_desc":"以乳为目自损30%当前血量，对全体敌人造成400%伤害+100%吸血",
    "skill_aoe":True,"skill_target":"all_enemy","skill_dmg_pct":4.0,"skill_special":["lifesteal"],
    "skill_upgrades":{3:"伤害600%+自损15%",5:"击杀后重置冷却",7:"血量低于30%时伤害翻倍", 9:"#2血量+60%+反伤"},
    "basic_name":"巨斧横劈","basic_desc":"挥舞巨斧，对全体敌人造成120%伤害","basic_dmg_pct":1.2,"basic_energy_gain":40,
    "basic_aoe":True,"basic_target":"all_enemy","basic_special":[],
    "passive_name":"不屈","passive_desc":"血量低于30%时攻击翻倍+减伤50%","passive_upgrades":{3:"触发阈值50%",5:"低于30%无敌2秒",7:"首次死亡复活60%血量"}})
reg({"id":"houyi","name":"后羿","class":"射手","quality":"神卡","color":"#ff3333",
    "hp":3500,"atk":980,"crit":50,"spd":175,"skill_cost":130,
    "skill_name":"射日","skill_desc":"对血量最低的敌人造成500%伤害+必定暴击+血量低于50%直接斩杀",
    "skill_aoe":False,"skill_target":"lowest_hp","skill_dmg_pct":5.0,"skill_special":["guaranteed_crit","execute"],
    "skill_upgrades":{3:"伤害800%+斩杀阈值70%",5:"击杀后溢出伤害溅射全体",7:"对BOSS也生效(伤害减半)", 9:"#4攻击+40%+首击必暴"},
    "basic_name":"穿云箭","basic_desc":"一箭穿云，对单个敌人造成200%伤害","basic_dmg_pct":2.0,"basic_energy_gain":50,
    "basic_aoe":False,"basic_target":"single","basic_special":[],
    "passive_name":"射日神弓","passive_desc":"战斗开始锁定血量最高敌人，降低其50%血量上限","passive_upgrades":{3:"降低60%",5:"额外降低30%攻击",7:"锁定目标死亡时全体敌人眩晕1回合"}})
reg({"id":"nuwa","name":"女娲","class":"奶妈","quality":"神卡","color":"#ff3333",
    "hp":5000,"atk":300,"crit":10,"spd":130,"skill_cost":180,
    "skill_name":"补天","skill_desc":"全体回复50%+免疫伤害3回合+攻击+50%×3回合+复活已死亡队友(20%血量)",
    "skill_aoe":True,"skill_target":"all_ally","skill_heal_pct":0.5,"skill_special":["revive","immunity"],"skill_buffs":[{"stat":"atk","pct":0.5,"dur":3}],
    "skill_upgrades":{3:"回复70%+免疫4回合",5:"复活血量40%+附加30%护盾",7:"补天:全队无敌+重置所有技能冷却", 9:"#3护盾50%+回血30%"},
    "basic_name":"抟土造人","basic_desc":"造化之力，全体回复15%血量","basic_dmg_pct":0,"basic_energy_gain":50,
    "basic_aoe":True,"basic_target":"all_ally","basic_special":[{"type":"heal","pct":0.15}],
    "passive_name":"创世","passive_desc":"战斗开始全体获得30%护盾+回复10%","passive_upgrades":{3:"护盾50%+回复20%",5:"队友死亡时立即复活一次(每场1次)",7:"复活时全队无敌1回合"}})
reg({"id":"chiyou","name":"蚩尤","class":"肉盾","quality":"神卡","color":"#ff3333",
    "hp":9000,"atk":400,"crit":10,"spd":90,"skill_cost":140,
    "skill_name":"兵主降临","skill_desc":"全体队友获得50%护盾+攻击+60%×3回合, 嘲讽全体敌人3回合, 自身减伤60%×3回合",
    "skill_aoe":False,"skill_target":"self","skill_special":["taunt","shield_ally"],"skill_buffs":[{"stat":"atk","pct":0.6,"dur":3},{"stat":"dmg_reduce","pct":0.6,"dur":3}],
    "skill_upgrades":{3:"护盾80%+嘲讽期间敌人受伤+30%",5:"兵主:全体队友免疫控制3回合",7:"兵主降世:全体队友无敌2回合", 9:"#2血量+60%+护盾40%"},
    "basic_name":"蚩尤旗","basic_desc":"挥动蚩尤旗，对全体敌人造成100%伤害+降低攻击15%","basic_dmg_pct":1.0,"basic_energy_gain":40,
    "basic_aoe":True,"basic_target":"all_enemy","basic_special":[],
    "passive_name":"兵主","passive_desc":"战斗开始给全体队友+30%攻击+15%减伤","passive_upgrades":{3:"攻击+50%+减伤25%",5:"血量高于70%时全队无敌1回合",7:"触发无敌时自身永久免疫+30%攻击"}})

HERO_IDS = list(HERO_DATA.keys())
QUALITY_ORDER = {"凡品":0,"良品":1,"极品":2,"绝品":3,"传说":4,"神卡":5}
QUALITY_WEIGHTS = {"凡品":40,"良品":30,"极品":20,"绝品":8,"传说":2}
RARITY_COLORS = {"凡品":"#888","良品":"#5adb7a","极品":"#4a8eff","绝品":"#b84aff","传说":"#ffd700","神卡":"#ff3333"}
# 重复英雄转换为经验池(抽到同名英雄时获得)
DUPE_EXP = {"凡品":30,"良品":80,"极品":200,"绝品":500,"传说":1500,"神卡":5000}
# 技能升级所需经验
SKILL_UPGRADE_COST = {2:100, 3:200, 4:350, 5:550, 6:800, 7:1200, 8:1800, 9:2800}
# ═══ 铸魂池常量 ═══
EQUIP_PITY_WEIGHTS = {"凡品":35,"良品":30,"极品":20,"绝品":10,"传说":4,"神卡":1}
EQUIP_PITY_BOOST = {"极品":40,"绝品":30,"传说":22,"神卡":8}  # 保底10抽时
EQUIP_DUPE_EXP = {"凡品":10,"良品":25,"极品":60,"绝品":150,"传说":400,"神卡":1200}

# ═══ 装备 ═══
EQUIP_DATA = {}
def req(e): EQUIP_DATA[e["id"]] = e; return e
req({"id":"wood_sword","name":"新手木剑","type":"武器","quality":"凡品","color":"#888","atk":30,"hp":0,"crit":0,"desc":"削尖的木头","exclusive":None,"special":None})
req({"id":"cloth_armor","name":"布甲","type":"防具","quality":"凡品","color":"#888","atk":0,"hp":150,"crit":0,"desc":"粗布护甲","exclusive":None,"special":None})
req({"id":"straw_sandal","name":"草鞋","type":"饰品","quality":"凡品","color":"#888","atk":0,"hp":0,"crit":2,"desc":"防滑耐磨","exclusive":None,"special":None})
req({"id":"iron_sword","name":"玄铁剑","type":"武器","quality":"良品","color":"#5adb7a","atk":80,"hp":0,"crit":0,"desc":"重剑无锋","exclusive":None,"special":None})
req({"id":"chain_armor","name":"锁子甲","type":"防具","quality":"良品","color":"#5adb7a","atk":0,"hp":350,"crit":0,"desc":"铁环甲","exclusive":None,"special":None})
req({"id":"bronze_mirror","name":"护心镜","type":"饰品","quality":"良品","color":"#5adb7a","atk":0,"hp":100,"crit":3,"desc":"护住要害","exclusive":None,"special":"暴击减伤20%"})
req({"id":"longquan_sword","name":"龙泉剑","type":"武器","quality":"极品","color":"#4a8eff","atk":200,"hp":0,"crit":5,"desc":"削铁如泥","exclusive":None,"special":"暴击率+5%"})
req({"id":"mingguang_armor","name":"明光铠","type":"防具","quality":"极品","color":"#4a8eff","atk":0,"hp":600,"crit":0,"desc":"刀枪不入","exclusive":None,"special":"减伤8%"})
req({"id":"jade_pendant","name":"玉佩","type":"饰品","quality":"极品","color":"#4a8eff","atk":0,"hp":200,"crit":5,"desc":"温润如玉","exclusive":None,"special":"开局10%护盾"})
req({"id":"halberd","name":"方天画戟","type":"武器","quality":"绝品","color":"#b84aff","atk":450,"hp":0,"crit":8,"desc":"吕布兵器","exclusive":"luobu","special":"技能后普攻翻倍"})
req({"id":"qilin_armor","name":"麒麟甲","type":"防具","quality":"绝品","color":"#b84aff","atk":0,"hp":900,"crit":0,"desc":"麒麟鳞甲","exclusive":"zhugeliang","special":"受击20%回血10%"})
req({"id":"pojun_bow","name":"破军弓","type":"武器","quality":"绝品","color":"#b84aff","atk":400,"hp":0,"crit":10,"desc":"一箭破军","exclusive":"yangyouji","special":"暴击伤害+50%"})
req({"id":"bagua_mirror","name":"八卦镜","type":"饰品","quality":"绝品","color":"#b84aff","atk":0,"hp":300,"crit":10,"desc":"洞察先机","exclusive":None,"special":"暴击+10%闪避+10%"})
req({"id":"qinglian_sword","name":"青莲剑","type":"武器","quality":"传说","color":"#ffd700","atk":1000,"hp":0,"crit":15,"desc":"一剑光寒十九洲","exclusive":"lihai","special":"技能伤害翻倍+攻击+30%"})
req({"id":"zhangba_spear","name":"丈八蛇矛","type":"武器","quality":"传说","color":"#ffd700","atk":600,"hp":2000,"crit":5,"desc":"当阳桥头吼","exclusive":"zhangfei","special":"怒吼全屏+护盾40%"})
req({"id":"qinglong_blade","name":"青龙偃月刀","type":"武器","quality":"传说","color":"#ffd700","atk":800,"hp":500,"crit":20,"desc":"武圣之刃","exclusive":"guanyu","special":"青龙偃月真实伤害"})
req({"id":"chitu","name":"赤兔马","type":"饰品","quality":"传说","color":"#ffd700","atk":100,"hp":1000,"crit":15,"desc":"马中赤兔","exclusive":None,"special":"开局全体30%护盾"})
req({"id":"heshi_bi","name":"和氏璧","type":"饰品","quality":"传说","color":"#ffd700","atk":200,"hp":800,"crit":25,"desc":"完璧归赵","exclusive":None,"special":"全属性+15%"})
# ═══ 神卡专属武器 ═══
req({"id":"golden_staff","name":"金箍棒","type":"武器","quality":"神卡","color":"#ff3333","atk":2000,"hp":500,"crit":20,"desc":"如意金箍棒","exclusive":"wukong","special":"攻击+40%，技能额外2次","lv20":"暴击伤害+50%","lv40":"技能必定暴击","lv60":"斩杀阈值+20%","lv80":"每击附带20%真实伤害","lv100":"大闹天宫:全体5次300%"})
req({"id":"she_sun_bow","name":"射日弓","type":"武器","quality":"神卡","color":"#ff3333","atk":1800,"hp":0,"crit":30,"desc":"后羿神弓","exclusive":"houyi","special":"斩杀阈值+20%，暴击伤害+80%","lv20":"攻击+15%","lv40":"必定暴击","lv60":"斩杀阈值翻倍","lv80":"溢出伤害溅射全体","lv100":"射日:9999真实伤害"})
req({"id":"bagua_furnace","name":"八卦炉","type":"防具","quality":"神卡","color":"#ff3333","atk":0,"hp":3000,"crit":10,"desc":"八卦神炉","exclusive":"qinshihuang","special":"技能伤害+50%，燃烧延长2回合","lv20":"减伤+10%","lv40":"技能吸血30%","lv60":"燃烧变为群体","lv80":"每回合对敌全体5%伤害","lv100":"焚天:全体500%+沉默"})
req({"id":"xingtian_axe","name":"刑天斧","type":"武器","quality":"神卡","color":"#ff3333","atk":2500,"hp":1000,"crit":10,"desc":"干戚神斧","exclusive":"xingtian","special":"技能伤害+60%，吸血+20%","lv20":"血量+20%","lv40":"技能AOE扩大","lv60":"反伤+20%","lv80":"击杀后重置技能","lv100":"开天:全屏600%+破甲"})
req({"id":"nuwa_stone","name":"补天石","type":"饰品","quality":"神卡","color":"#ff3333","atk":300,"hp":2000,"crit":25,"desc":"五色补天石","exclusive":"nuwa","special":"治疗+40%，护盾+30%","lv20":"免疫+1回合","lv40":"治疗暴击","lv60":"复活队友+50%血","lv80":"全队开局60%护盾","lv100":"创世:全队无敌2回合"})
req({"id":"chiyou_flag","name":"蚩尤旗","type":"防具","quality":"神卡","color":"#ff3333","atk":500,"hp":4000,"crit":5,"desc":"兵主战旗","exclusive":"chiyou","special":"减伤+25%，护盾+40%","lv20":"血量+30%","lv40":"嘲讽时全队回血10%","lv60":"受击反弹30%","lv80":"全体队友减伤+20%","lv100":"兵主:全队无敌+攻击翻倍"})
EQ_QUALITY_ORDER = {"凡品":0,"良品":1,"极品":2,"绝品":3,"传说":4,"神卡":5}

# ═══ 羁绊 ═══
BONDS = [
    {"id":"poem_sword","name":"诗剑双绝","members":["lihai","zhaoyun"],"desc":"攻击+25%","effect":{"atk_pct":25}},
    {"id":"peach_garden","name":"桃园结义","members":["guanyu","zhangfei"],"desc":"减伤+20%","effect":{"dmg_reduce":20}},
    {"id":"sleeping_phoenix","name":"卧龙凤雏","members":["zhugeliang","pangtong"],"desc":"技能伤害+35%","effect":{"skill_dmg":35}},
    {"id":"beauty","name":"国色天香","members":["diaochan","caiwenji"],"desc":"闪避+15%","effect":{"dodge":15}},
    {"id":"sharpshooter","name":"百步穿杨","members":["yangyouji","huangzhong"],"desc":"暴击+15%爆伤+30%","effect":{"crit":15,"crit_dmg":30}},
    {"id":"rivalry","name":"宿命对决","members":["luobu","guanyu"],"desc":"攻击+20%","effect":{"atk_pct":20}},
]

# ═══ 无限关卡 ═══
ENEMY_NAMES=["山贼","流寇","马匪","土匪","响马","刀客","剑奴","毒蜂","蛊师","修罗","刺客","死士","铁卫","血手","暗影","疯丐","头陀","镖师","捕快","逃兵","力士","拳师"]
LOCATIONS=["黑风岭","断魂崖","乱葬岗","野猪林","枯木渡","寒潭","毒瘴谷","赤焰山","冰封峡","天绝峰","忘川河","落凤坡"]
TITLES=["遭遇战","伏击","夜袭","追捕","突围","守卫","劫杀","闯关","破阵","讨伐"]
ECS=["战士","肉盾","刺客","法师","射手","奶妈"]

def gen_stage_name(si):
    return f"{random.choice(ENEMY_NAMES)}·{random.choice(LOCATIONS)}·{random.choice(TITLES)}"

def gen_stage(si):
    """根据关卡进度固定生成关卡，不依赖玩家战力"""
    boss = si > 0 and si % 5 == 0  # 每5关一个BOSS(不含第0关)
    ep = 2000 + si * 500  # 更高血量，更有挑战
    if si < 3: pl=["wood_sword","cloth_armor","straw_sandal"]; jb=5; ec=0.2
    elif si < 8: pl=["iron_sword","chain_armor","bronze_mirror"]; jb=10; ec=0.3
    elif si < 15: pl=["longquan_sword","mingguang_armor","jade_pendant"]; jb=18; ec=0.4
    elif si < 25: pl=["halberd","qilin_armor","pojun_bow","bagua_mirror"]; jb=30; ec=0.5
    else: pl=["qinglian_sword","zhangba_spear","qinglong_blade","chitu","heshi_bi"]; jb=45; ec=0.55
    if boss: jb = int(jb * 1.5)
    return {"id":f"s{si}","name":gen_stage_name(si),"boss":boss,"_index":si,"power":ep,
            "drops":{"jade":jb,"equip_chance":min(ec+si*0.005,0.7),"equip_pool":pl}}

# ═══ 游戏状态 ═══
def new_game_inner():
    inv=[{"hero_id":sid,"skill_lv":1,"level":1,"exp":0,"equipped":{"武器":None,"防具":None,"饰品":None},"active":True} for sid in ["lisi","wangdazhuang","xiaocui"]]
    return {"jade":10,"inventory":inv,"equip_bag":[{"id":"wood_sword","count":1}],"lineup":["lisi","wangdazhuang","xiaocui"],
            "pull_count":0,"pity_counter":0,"equip_pity_counter":0,"equip_exp_total":0,"stage_index":0,"ticks":0,"msg":"☯ 欢迎来到江湖！","dupe_exp_total":0,
            "selected_hero":None,"game_over":False,"won":False,"last_active_time":datetime.now().timestamp(),"offline_msg":""}

def calc_pow(all_data, lineup, inventory, equip_bag):
    t=0
    for hid in lineup:
        inv=next((i for i in inventory if i["hero_id"]==hid),None)
        if not inv: continue
        hd=all_data.get(hid)
        if not hd: continue
        lv=inv.get("level",1)
        bonus=hero_level_bonus(lv)
        hp=int(hd["hp"]*bonus); atk=int(hd["atk"]*bonus)
        spd=hd.get("spd",100)
        crit=calc_level_crit(hd, lv)
        for s,eq in inv["equipped"].items():
            if eq and eq in EQUIP_DATA:
                e=EQUIP_DATA[eq]
                ulv=0
                if equip_bag:
                    eb=next((x for x in equip_bag if isinstance(x,dict) and x.get("id")==eq),None)
                    if eb: ulv=eb.get("upgrade_lv",0)
                mult=1.0+ulv*0.25
                if ulv>=10: mult+=0.5
                elif ulv>=5: mult+=0.25
                hp+=int(e.get("hp",0)*mult); atk+=int(e.get("atk",0)*mult)
        p=hp+atk*2+spd*2
        p*=(1+(inv["skill_lv"]-1)*0.1); t+=p
    return int(t)

def calc_hp(hid, sl, eq, inv=None, equip_bag=None):
    hd=HERO_DATA.get(hid)
    if not hd: return 0
    lv=inv.get("level",1) if inv else 1
    bonus=hero_level_bonus(lv)
    hp=int(hd["hp"]*bonus); atk=int(hd["atk"]*bonus)
    for s,eid in eq.items():
        if eid and eid in EQUIP_DATA:
            e=EQUIP_DATA[eid]
            ulv=0
            if equip_bag:
                eb=next((x for x in equip_bag if isinstance(x,dict) and x.get("id")==eid),None)
                if eb: ulv=eb.get("upgrade_lv",0)
            mult=1.0+ulv*0.25
            if ulv>=10: mult+=0.5
            elif ulv>=5: mult+=0.25
            hp+=int(e.get("hp",0)*mult); atk+=int(e.get("atk",0)*mult)
    p=hp+atk*2+int(hd.get("spd",100)*2)
    p*=(1+(sl-1)*0.1); return int(p)

def get_bonds(lineup):
    return [b for b in BONDS if all(m in lineup for m in b["members"])]

def rq(pity):
    if pity>=10: return random.choices(["极品","绝品","传说","神卡"],weights=[40,28,18,14])[0]
    tw=sum(QUALITY_WEIGHTS.values()); rl=random.randint(1,tw); cum=0
    for q,w in sorted(QUALITY_WEIGHTS.items(),key=lambda x:QUALITY_ORDER[x[0]]):
        cum+=w
        if rl<=cum: return q
    return "凡品"

def pull_h(pity):
    q=rq(pity)
    pl=[hid for hid,h in HERO_DATA.items() if h["quality"]==q]
    hid=random.choice(pl) if pl else "lisi"
    return {"hero_id":hid,"quality":q,"skill_lv":1,"level":1,"exp":0,"equipped":{"武器":None,"防具":None,"饰品":None},"active":False}

# ═══ 铸魂抽卡 ═══
EQUIP_IDS_BY_QUALITY = {}
for eid, ed in EQUIP_DATA.items():
    q = ed["quality"]
    EQUIP_IDS_BY_QUALITY.setdefault(q, []).append(eid)

def eq_rq(pity):
    if pity >= 10:
        return random.choices(list(EQUIP_PITY_BOOST.keys()), weights=list(EQUIP_PITY_BOOST.values()))[0]
    tw = sum(EQUIP_PITY_WEIGHTS.values())
    rl = random.randint(1, tw)
    cum = 0
    for q, w in sorted(EQUIP_PITY_WEIGHTS.items(), key=lambda x: EQ_QUALITY_ORDER.get(x[0], 0)):
        cum += w
        if rl <= cum:
            return q
    return "凡品"

def pull_equip(pity):
    q = eq_rq(pity)
    pool = EQUIP_IDS_BY_QUALITY.get(q, [])
    if not pool:
        pool = ["wood_sword"]
    eid = random.choice(pool)
    return {"id": eid, "quality": q}

def equip_upgrade_cost(lv):
    """武器升级消耗铸魂经验"""
    return lv * 5 + 10  # Lv0→1:10, Lv1→2:15, Lv99→100:505

# ═══════════════════════════════════════════
# 敌方职业系统
# ═══════════════════════════════════════════

ENEMY_PROFESSIONS = {
    "战士": {
        "icon":"🗡️","target":"front_row","spd_factor":1.0,"hp_factor":1.0,"atk_factor":1.2,
        "skill_name":"破甲斩",
        "skill_desc":"对前排单体造成200%伤害+降防30%×2回合",
        "skill_aoe":False,"skill_target":"front_row",
        "skill_dmg_pct":2.0,
        "skill_debuffs":[{"stat":"dmg_reduce","pct":-0.3,"dur":2}],
        "basic_dmg_pct":0.8,
        "skill_cost":100,
        "skill_buffs":[],
        "skill_heal":0,
    },
    "铁卫": {
        "icon":"🛡️","target":"front_row","spd_factor":0.7,"hp_factor":2.5,"atk_factor":0.5,
        "skill_name":"铁壁",
        "skill_desc":"自身减伤50%×2回合+嘲讽全体敌人",
        "skill_aoe":False,"skill_target":"self",
        "skill_dmg_pct":0,
        "skill_special":["taunt"],
        "skill_buffs":[{"stat":"dmg_reduce","pct":0.5,"dur":2}],
        "basic_dmg_pct":0.5,
        "skill_cost":100,
        "skill_debuffs":[],
        "skill_heal":0,
    },
    "刺客": {
        "icon":"💀","target":"lowest_hp","spd_factor":1.5,"hp_factor":0.6,"atk_factor":1.8,
        "skill_name":"暗杀",
        "skill_desc":"对血量最低敌人造成250%伤害，击杀重置技能",
        "skill_aoe":False,"skill_target":"lowest_hp",
        "skill_dmg_pct":2.5,
        "skill_special":["refresh_on_kill"],
        "basic_dmg_pct":1.0,
        "skill_cost":100,
        "skill_debuffs":[],
        "skill_buffs":[],
        "skill_heal":0,
    },
    "术士": {
        "icon":"🔥","target":"all_enemy","spd_factor":1.1,"hp_factor":0.8,"atk_factor":1.4,
        "skill_name":"烈焰风暴",
        "skill_desc":"全体120%伤害+灼烧10%×2回合",
        "skill_aoe":True,"skill_target":"all_enemy",
        "skill_dmg_pct":1.2,
        "skill_special":["burn"],
        "basic_dmg_pct":0.6,
        "skill_cost":110,
        "skill_debuffs":[],
        "skill_buffs":[],
        "skill_heal":0,
    },
    "弓手": {
        "icon":"🎯","target":"back_row","spd_factor":1.2,"hp_factor":0.7,"atk_factor":1.6,
        "skill_name":"狙击",
        "skill_desc":"对血量最低的敌人造成300%伤害+50%斩杀",
        "skill_aoe":False,"skill_target":"lowest_hp",
        "skill_dmg_pct":3.0,
        "skill_special":["execute"],
        "basic_dmg_pct":0.9,
        "skill_cost":100,
        "skill_debuffs":[],
        "skill_buffs":[],
        "skill_heal":0,
    },
    "巫医": {
        "icon":"💚","target":"lowest_hp_ally","spd_factor":1.0,"hp_factor":0.9,"atk_factor":0.6,
        "skill_name":"治愈",
        "skill_desc":"回复最低血量队友25%血量+驱散负面",
        "skill_aoe":False,"skill_target":"lowest_hp_ally",
        "skill_heal_pct":0.25,
        "skill_special":["cleanse"],
        "basic_dmg_pct":0.4,
        "skill_cost":90,
        "skill_debuffs":[],
        "skill_buffs":[],
        "skill_heal":0,
    },
}

BOSS_SKILLS = [
    {"name":"血怒","type":"passive","desc":"血量低于50%时，攻击+100%、速度+50%","effect":"berserk"},
    {"name":"雷霆","type":"active","desc":"全体200%伤害+眩晕1回合","aoe":True,"dmg_pct":2.0,"debuffs":[{"stat":"stun","pct":1.0,"dur":1}]},
    {"name":"焚天","type":"active","desc":"全体150%伤害+灼烧20%×3回合","aoe":True,"dmg_pct":1.5,"special":"burn"},
    {"name":"铁壁阵","type":"active","desc":"全体获得30%护盾+减伤25%×2回合","aoe":True,"buff":"shield_team","shield_pct":0.3,"team_buffs":[{"stat":"dmg_reduce","pct":0.25,"dur":2}]},
    {"name":"死亡标记","type":"active","desc":"锁定最低血量目标+易伤50%","aoe":False,"target":"lowest_hp","debuffs":[{"stat":"dmg_reduce","pct":-0.5,"dur":2}]},
    {"name":"狂风","type":"active","desc":"全体减速30%×2回合","aoe":True,"debuffs":[{"stat":"spd","pct":-0.30,"dur":2}]},
    {"name":"回春阵","type":"active","desc":"全体回复30%血量","aoe":True,"heal_pct":0.30},
    {"name":"狂暴","type":"passive","desc":"每击杀一个目标攻击+30%","effect":"rage"},
]

BOSS_PASSIVES = [
    {"name":"浴血","desc":"HP<30%时攻击翻倍+吸血50%+免疫控制","effect":"bloodbath"},
    {"name":"反伤甲","desc":"受到伤害时反弹15%","effect":"thorn"},
    {"name":"不屈","desc":"首次死亡回复50%血量+无敌1回合","effect":"undying"},
    {"name":"召唤","desc":"每3回合召唤一个分身小弟","effect":"summon"},
    {"name":"暴君","desc":"HP>80%时伤害+50%","effect":"tyrant"},
    {"name":"瘟疫","desc":"每回合全体5%最大血量伤害","effect":"plague"},
]

ENEMY_PROF_KEYS = list(ENEMY_PROFESSIONS.keys())
ENEMY_ICONS = {p: d["icon"] for p, d in ENEMY_PROFESSIONS.items()}

def gen_enemy_formation(ps, boss=False):
    """生成6个敌人，每种职业各一个"""
    used_names = set()
    enemies = []
    profs_taken = set()
    
    if boss:
        # BOSS关: 1个BOSS + 5个小弟
        boss_prof = random.choice(ENEMY_PROF_KEYS)
        profs_taken.add(boss_prof)
        bs = random.sample(BOSS_SKILLS, min(2, len(BOSS_SKILLS)))
        bp = random.choice(BOSS_PASSIVES)
        
        name = random.choice([x for x in ENEMY_NAMES if x not in used_names] or ENEMY_NAMES)
        used_names.add(name)
        boss_unit = gen_enemy(name, ps, profession=boss_prof, boss=True, boss_skills=bs, boss_passive=bp)
        enemies.append(boss_unit)
        
        # 5个小弟，剩下的职业
        remaining_profs = [p for p in ENEMY_PROF_KEYS if p != boss_prof]
        random.shuffle(remaining_profs)
        for i in range(5):
            prof = remaining_profs[i % len(remaining_profs)]
            n = random.choice([x for x in ENEMY_NAMES if x not in used_names] or ENEMY_NAMES)
            used_names.add(n)
            enemies.append(gen_enemy(n, ps * 0.8, profession=prof, boss=False))
    else:
        # 普通关: 每种职业各一个
        shuffled_profs = list(ENEMY_PROF_KEYS)
        random.shuffle(shuffled_profs)
        for prof in shuffled_profs:
            n = random.choice([x for x in ENEMY_NAMES if x not in used_names] or ENEMY_NAMES)
            used_names.add(n)
            enemies.append(gen_enemy(n, ps, profession=prof, boss=False))
    
    return enemies

def gen_enemy(name, ps, profession, boss=False, boss_skills=None, boss_passive=None):
    pd = ENEMY_PROFESSIONS[profession]
    
    if boss:
        base_hp = int(ps * random.uniform(2.0, 5.0))
        hp = base_hp * 8
        atk = int(ps * random.uniform(0.15, 0.35) * pd["atk_factor"])
        spd = int(100 * pd["spd_factor"] * random.uniform(1.0, 1.3))
        q = random.choices(["绝品","传说","神卡"], weights=[50,35,15])[0]
        crit = random.randint(20, 50)
        name = "【BOSS】" + name
        skill_cost = 120
    else:
        hp = int(ps * random.uniform(1.0, 2.5) * pd["hp_factor"])
        atk = int(ps * random.uniform(0.05, 0.12) * pd["atk_factor"])
        spd = int(100 * pd["spd_factor"] * random.uniform(0.9, 1.1))
        q = random.choices(["凡品","良品","极品","绝品","传说"],weights=[25,25,25,18,7])[0]
        crit = random.randint(5, 30)
        skill_cost = pd.get("skill_cost", 100)
    
    unit = {
        "name": name, "class": profession, "quality": q, "color": RARITY_COLORS.get(q, "#888"),
        "hp": hp, "max_hp": hp, "atk": atk, "crit": crit, "crit_dmg": 1.5, "dmg_reduce": 0.0,
        "spd": spd, "alive": True, "shield": 0, "buffs": [], "debuffs": [],
        "stunned": False, "frozen": False, "reflect": False,
        "skill_name": pd["skill_name"], "skill_desc": pd["skill_desc"],
        "skill_aoe": pd["skill_aoe"], "skill_target": pd["skill_target"],
        "skill_dmg_pct": pd.get("skill_dmg_pct", 1.0),
        "skill_special": pd.get("skill_special", []),
        "skill_debuffs": pd.get("skill_debuffs", []),
        "skill_buffs": pd.get("skill_buffs", []),
        "skill_heal": pd.get("skill_heal", 0),
        "skill_heal_pct": pd.get("skill_heal_pct", 0),
        "energy": 0, "skill_cost": skill_cost,
        "energy_gain": 25, "basic_dmg_pct": pd.get("basic_dmg_pct", 0.6),
        "side": "enemy", "profession": profession, "prof_icon": pd["icon"],
    }
    
    if boss and boss_skills:
        unit["boss_skills"] = [{"name": s["name"], "desc": s["desc"]} for s in boss_skills]
        unit["boss_passive_name"] = boss_passive["name"]
        unit["boss_passive_desc"] = boss_passive["desc"]
        unit["boss_passive_effect"] = boss_passive["effect"]
    
    return unit

def h2f(hid, inv, equip_bag=None):
    hd=HERO_DATA.get(hid)
    if not hd: return None
    lv=inv.get("level",1)
    # 基础属性
    hp=int(hd["hp"]*hero_level_bonus(lv)); atk=int(hd["atk"]*hero_level_bonus(lv))
    crit=calc_level_crit(hd, lv)  # 等级暴击率(500级达60%)
    crit=min(crit, 100)  # 暴击率上限100%
    crit_dmg=hd.get("crit_dmg", 1.5)  # 基础爆伤150%
    dmg_reduce=hd.get("dmg_reduce", 0.0)  # 基础减伤
    for s,eq in inv["equipped"].items():
        if eq and eq in EQUIP_DATA:
            e=EQUIP_DATA[eq]
            ulv=0
            if equip_bag:
                eb=next((x for x in equip_bag if isinstance(x,dict) and x.get("id")==eq),None)
                if eb: ulv=eb.get("upgrade_lv",0)
            mult=1.0+ulv*0.25
            if ulv>=10: mult+=0.5
            elif ulv>=5: mult+=0.25
            hp+=int(e.get("hp",0)*mult); atk+=int(e.get("atk",0)*mult)
            crit+=e.get("crit",0)  # 装备暴击
    unit = {"id":hid,"name":hd["name"],"class":hd["class"],"quality":hd["quality"],"color":hd["color"],
            "hp":hp,"max_hp":hp,"atk":atk,"crit":crit,"crit_dmg":crit_dmg,"dmg_reduce":dmg_reduce,
            "spd":hd.get("spd",100),"_skill_cost":hd.get("skill_cost",100),
            "alive":True,"shield":0,"buffs":[],"debuffs":[],"stunned":False,"frozen":False,"reflect":False,
            "energy":0,"action_bar":random.randint(0,400),
            "_hd":hd,"_skill_lv":inv.get("skill_lv",1),"side":"ally",
            "_equipped":inv.get("equipped",{}),"_equip_bag":equip_bag or []}
    apply_equip_passive_buffs(unit)
    return unit

def calc_level_crit(hd, lv):
    """等级提供的暴击率：平滑增长，500级达到60%"""
    base = hd.get("crit", 0)
    return int(base + (60 - base) * min(lv, 500) / 500.0)

def get_eff_stats(hd, inv, equip_bag):
    """计算英雄有效属性，含装备被动加成。用于API展示。"""
    lv=inv.get("level",1)
    bonus=hero_level_bonus(lv)
    hp=int(hd["hp"]*bonus); atk=int(hd["atk"]*bonus)
    crit=min(calc_level_crit(hd, lv), 100)
    crit_dmg=hd.get("crit_dmg", 1.5)
    dmg_reduce=hd.get("dmg_reduce", 0.0)
    for s,eq_id in inv["equipped"].items():
        if not eq_id or eq_id not in EQUIP_DATA: continue
        e=EQUIP_DATA[eq_id]
        ulv=0
        if equip_bag:
            eb=next((x for x in equip_bag if isinstance(x,dict) and x.get("id")==eq_id),None)
            if eb: ulv=eb.get("upgrade_lv",0)
        mult=1.0+ulv*0.25
        if ulv>=10: mult+=0.5
        elif ulv>=5: mult+=0.25
        hp+=int(e.get("hp",0)*mult)
        atk+=int(e.get("atk",0)*mult)
        crit+=e.get("crit",0)
    # 装备被动效果
    unit = {"_hd":hd, "_equipped":inv.get("equipped",{}), "_equip_bag":equip_bag or [],
            "buffs":[], "debuffs":[], "crit_dmg":crit_dmg, "dmg_reduce":dmg_reduce,
            "hp":hp, "max_hp":hp, "atk":atk, "crit":crit, "spd":hd.get("spd",100)}
    apply_equip_passive_buffs(unit)
    for b in unit.get("buffs",[]):
        if b["stat"]=="crit_dmg": crit_dmg+=b["pct"]
        if b["stat"]=="dmg_reduce": dmg_reduce+=b["pct"]
    return {"hp":unit["hp"],"max_hp":unit["max_hp"],"atk":unit["atk"],"crit":unit["crit"],
            "crit_dmg":crit_dmg,"dmg_reduce":dmg_reduce,"spd":unit.get("spd",100)}

def hero_level_bonus(lv):
    """等级属性倍率: 每级+8%, 每10级突破额外+30%"""
    b=1.0+(lv-1)*0.08
    b+=int(lv/10)*0.3  # 10级+0.3, 20级+0.6...
    return b

def equip_effective_stat(ed, stat):
    """装备强化后的有效属性"""
    base=ed.get(stat,0)
    ulv=ed.get("_upgrade_lv",0)
    if ulv<=0: return base
    mult=1.0+ulv*0.25
    if ulv>=10: mult+=0.5
    elif ulv>=5: mult+=0.25
    return int(base*mult)

def exp_for_stage(si):
    """关卡经验奖励"""
    return 50+si*8  # 0关=50, 10关=130, 50关=450

def add_exp_to_heroes(g, stage_idx):
    """给上阵英雄加经验, 处理升级"""
    msgs=[]
    for inv in g["inventory"]:
        if inv["hero_id"] not in g["lineup"]: continue
        exp_gain=exp_for_stage(stage_idx)
        inv["exp"]=inv.get("exp",0)+exp_gain
        lv=inv.get("level",1)
        changed=False
        while True:
            needed=lv*100
            if inv["exp"]>=needed:
                inv["exp"]-=needed
                lv+=1
                inv["level"]=lv
                changed=True
            else: break
        if changed:
            msgs.append(f"{HERO_DATA.get(inv['hero_id'],{}).get('name','?')}升到{lv}级!")
    return msgs

def cstat(u, stat, base):
    """Buff计算. atk/spd用base*pct累加; crit用加法(百分比点); dmg_reduce单独处理"""
    v=base
    for b in u.get("buffs",[]):
        if b["stat"]==stat:
            if stat in ("spd",):
                v+=int(b.get("pct",0))  # spd绝对值
            elif stat=="crit":
                v+=int(b.get("pct",0)*100)  # crit加法：+20%直加20点
            else:
                v+=int(base*b.get("pct",0))  # atk等比例
    for d in u.get("debuffs",[]):
        if d["stat"]==stat:
            if stat in ("spd",):
                v+=int(d.get("pct",0))
            elif stat=="crit":
                v+=int(d.get("pct",0)*100)
            else:
                v+=int(base*d.get("pct",0))
    return max(1 if stat in ("atk","hp","spd","crit") else 0, int(v))

def get_effective_crit_dmg(unit):
    """计算有效爆伤倍率: 基础+ buff/debuff 中的 crit_dmg"""
    cd = unit.get("crit_dmg", 1.5)
    for b in unit.get("buffs", []):
        if b["stat"] == "crit_dmg":
            cd += b["pct"]
    for d in unit.get("debuffs", []):
        if d["stat"] == "crit_dmg":
            cd += d["pct"]
    return max(1.0, cd)  # 最低1.0倍

def get_dmg_reduce(t):
    """独立计算减伤(0~0.8): dmg_reduce直接从buff累加pct, 含基础值"""
    dr = t.get("dmg_reduce", 0.0)
    for b in t.get("buffs",[]):
        if b["stat"]=="dmg_reduce": dr+=b["pct"]
    for d in t.get("debuffs",[]):
        if d["stat"]=="dmg_reduce": dr+=d["pct"]
    return max(-0.5, min(0.8, dr))

def get_targets(units, mode):
    alive=[u for u in units if u.get("alive",True)]
    if not alive: return []
    if mode=="single": return [random.choice(alive)]
    elif mode=="lowest_hp": return [min(alive,key=lambda x:x["hp"])]
    elif mode=="back_row": return alive[-max(1,len(alive)//2):]
    elif mode=="front_row": return alive[:max(1,len(alive)//2)]
    elif mode=="all_enemy" or mode=="all_ally": return alive
    return [random.choice(alive)]

def apply_dmg(t, raw, ignore_shield=False):
    for b in t.get("buffs",[]):
        if b["stat"]=="immune" and b["dur"]>0:
            return {"damage":0,"shield_damage":0,"immune":True}
    sh=t.get("shield",0)
    if ignore_shield:
        t["hp"]-=raw
        return {"damage":raw,"shield_damage":0,"immune":False}
    sd=min(sh,raw); ad=raw-sd; t["shield"]=sh-sd; t["hp"]-=ad
    return {"damage":ad,"shield_damage":sd,"immune":False}

def tick_buffs(units):
    for u in units:
        u["buffs"]=[b for b in u.get("buffs",[]) if b["dur"]>1]
        for b in u.get("buffs",[]):
            b["dur"]-=1
        u["debuffs"]=[d for d in u.get("debuffs",[]) if d["dur"]>1]
        for d in u.get("debuffs",[]):
            d["dur"]-=1
        u["stunned"]=False; u["frozen"]=False

def _removed_pick_best_card(): return None

def reset_dead_buffs(units):
    for u in units:
        if not u.get("alive",True):
            u["buffs"]=[]; u["debuffs"]=[]; u["stunned"]=False; u["frozen"]=False

def run_speed_battle(my_heroes, enemies, stage):
    """速度轴战斗: 每个单位按速度行动, 普攻攒能, 技能消耗"""
    all_u = my_heroes + enemies
    for u in all_u:
        u["action_bar"] = u.get("action_bar", random.randint(0, 400))
        u["energy"] = 0
        u.setdefault("side","ally") if u in my_heroes else u.setdefault("side","enemy")
    # 神卡初始能量100（女娲满能量200）
    for h in my_heroes:
        hd = h.get("_hd", {})
        if hd.get("quality") == "神卡":
            h["energy"] = 200 if hd.get("id") == "nuwa" else 100
    # 位置系统
    for i, h in enumerate(my_heroes):
        h["position"] = i + 1
    for i, e in enumerate(enemies):
        e["position"] = 7 + i

    # 卡牌系统
    all_actions = []

    # 被动系统上下文
    passive_ctx = {"all_actions": all_actions, "passive_counters": {}, "triggered":set()}

    # 战斗开始被动 (效果引擎统一处理)
    process_all_battle_start(my_heroes, enemies, passive_ctx)

    # 装备战斗开始效果
    for h in my_heroes:
        process_equip_battle_start(h, my_heroes, enemies, passive_ctx)

    # Lv9位置天赋（all_actions清零后，但initial_state快照前）
    lv9_msgs=apply_lv9_bonuses(my_heroes, enemies)
    for b in lv9_msgs:
        all_actions.append({"side":"ally","type":"passive","attacker_name":"天赋","skill":b["desc"],"aoe":False,"target_name":"","msg":b["desc"]})
    _lv9_buffs = lv9_msgs

    # 初始状态快照（被动+天赋后）
    initial_state = {
        "lv9_buffs": _lv9_buffs,
        "my_heroes": [{"name":f["name"],"class":f["class"],"quality":f["quality"],"color":f["color"],
            "max_hp":f["max_hp"],"atk":f["atk"],"crit":f["crit"],
            "eff_atk":cstat(f,"atk",f["atk"]),"eff_crit":cstat(f,"crit",f["crit"]),
            "hp_pct":max(0,f["hp"]/max(1,f["max_hp"])),"alive":f.get("alive",True),
            "energy":f.get("energy",0),"spd":f.get("spd",100),"position":f.get("position",0),
            "buffs":[b["stat"] for b in f.get("buffs",[])],
            "debuffs":[d["stat"] for d in f.get("debuffs",[])]} for f in my_heroes],
        "enemies": [{"name":e["name"],"class":e["class"],"quality":e["quality"],"color":e["color"],
            "max_hp":e["max_hp"],"atk":e["atk"],
            "eff_atk":cstat(e,"atk",e["atk"]),"eff_crit":cstat(e,"crit",e["crit"]),
            "hp_pct":max(0,e["hp"]/max(1,e["max_hp"])),"alive":e.get("alive",True),
            "energy":e.get("energy",0),
            "buffs":[b["stat"] for b in e.get("buffs",[])],
            "debuffs":[d["stat"] for d in e.get("debuffs",[])],
            "prof_icon":e.get("prof_icon",""),
            "skill_name":e.get("skill_name",""),
            "skill_desc":e.get("skill_desc",""),
            "boss_passive_name":e.get("boss_passive_name",""),
            "boss_passive_desc":e.get("boss_passive_desc","")} for e in enemies]
    }

    tick_no = 0
    actions_since_draw = 0
    all_actions = []

    max_ticks = 250

    while tick_no < max_ticks:
        tick_no += 1
        alive_h = [u for u in my_heroes if u.get("alive",True)]
        alive_e = [u for u in enemies if u.get("alive",True)]
        if not alive_h or not alive_e: break

        # 速度tick
        for u in all_u:
            if u.get("alive",True):
                spd = cstat(u, "spd", u.get("spd",100))
                u["action_bar"] += spd

        # 按行动条排序，找出所有>=1000的单位
        ready = sorted(
            [u for u in all_u if u.get("alive",True) and u["action_bar"] >= 1000],
            key=lambda x: -x["action_bar"]
        )

        if not ready:
            continue  # 无人在这一tick行动

        for unit in ready:
            if not unit.get("alive",True): continue
            unit["action_bar"] -= 1000
            actions_since_draw += 1

            # ===== 敌方行动 =====
            if unit.get("side") == "enemy":
                can_skill = unit["energy"] >= unit.get("skill_cost", 100)
                if can_skill:
                    unit["energy"] -= unit.get("skill_cost", 100)
                    act = enemy_use_skill(unit, my_heroes, enemies)
                else:
                    unit["energy"] += min(40, unit.get("energy_gain", 25))
                    act = enemy_basic_attack(unit, my_heroes, enemies)
                all_actions.append(act)
                # 注入当时HP状态（非最终态）+ 实时有效攻暴
                a=act
                if a.get("type")!="card_play":
                    a["attacker_idx"]=next((i for i,uu in enumerate(enemies if a.get("side")=="enemy" else my_heroes) if uu.get("name")==a.get("attacker_name")),0)
                    # 注入攻击者当时的有效攻暴（让前端能实时感知buff变化）
                    if a.get("side")=="ally" or a.get("side")=="heal":
                        a["hero_eff_atk"]=cstat(unit,"atk",unit["atk"])
                        a["hero_eff_crit"]=cstat(unit,"crit",unit["crit"])
                        a["hero_base_atk"]=unit["atk"]
                        a["hero_base_crit"]=unit["crit"]
                        a["hero_buffs"]=[b["stat"] for b in unit.get("buffs",[])]
                        a["hero_debuffs"]=[d["stat"] for d in unit.get("debuffs",[])]
                    if a.get("aoe") and a.get("targets"):
                        for tg in a["targets"]:
                            tlist=enemies if a.get("side")=="ally" else my_heroes
                            uu=next((uu2 for uu2 in tlist if uu2.get("name")==tg.get("name")),None)
                            if uu: tg["hp_pct"]=max(0,uu["hp"]/max(1,uu["max_hp"])); tg["max_hp"]=uu["max_hp"]; tg["idx"]=next((i for i,uu2 in enumerate(tlist) if uu2.get("name")==tg.get("name")),0)
                            if tg.get("killed") and uu: tg["hp_pct"]=0
                    elif a.get("target_name"):
                        tlist=enemies if a.get("side")=="ally" else my_heroes
                        uu=next((uu2 for uu2 in tlist if uu2.get("name")==a["target_name"]),None)
                        if uu: a["target_hp_pct"]=max(0,uu["hp"]/max(1,uu["max_hp"])); a["target_max_hp"]=uu["max_hp"]; a["target_idx"]=next((i for i,uu2 in enumerate(tlist) if uu2.get("name")==a["target_name"]),0)
                        if a.get("killed") and uu: a["target_hp_pct"]=0

            # ===== 我方行动 =====
            else:
                hd = unit.get("_hd")
                sk_cost = unit.get("_skill_cost", 100)
                can_skill = unit["energy"] >= sk_cost

                if can_skill:
                    unit["energy"] -= sk_cost
                    act = hero_use_skill(unit, my_heroes, enemies)
                    # 吸血治疗记录
                    if act.get("lifesteal_heal", 0) > 0:
                        heal_act = {"side":"heal","type":"lifesteal","attacker_name":unit["name"],
                                     "skill":"吸血","aoe":False,"target_name":unit["name"],
                                     "heal":act["lifesteal_heal"],
                                     "msg":f"{unit['name']}吸血+{act['lifesteal_heal']}❤️"}
                        all_actions.append(heal_act)
                    # 武圣/on_skill被动 (效果引擎)
                    process_effects("on_skill", unit, unit.get("_hd"), unit.get("_skill_lv",1), my_heroes, enemies, passive_ctx)
                else:
                    eg = hd.get("basic_energy_gain", 25) if hd else 25
                    unit["energy"] += eg
                    if unit["energy"] > 200: unit["energy"] = 200
                    act = hero_basic_attack(unit, my_heroes, enemies)
                act["energy_after"] = unit["energy"]
                # 先注入idx/max_hp到原始动作（expand需要它们）
                for tg in act.get("targets", []):
                    tlist = enemies if act.get("side") == "ally" else my_heroes
                    uu = next((uu2 for uu2 in tlist if uu2.get("name") == tg.get("name")), None)
                    if uu:
                        tg["idx"] = next((i for i, uu2 in enumerate(tlist) if uu2.get("name") == tg.get("name")), 0)
                        tg["max_hp"] = uu["max_hp"]
                # 多段拆分
                mhc=act.get("multi_hit_count",1)
                sub_acts=expand_multi_hit_action(act,mhc) if mhc>1 else [act]
                for sa in sub_acts:
                    # 为子动作注入攻击者信息(但不覆盖HP)
                    if sa.get("total_hits") and sa.get("aoe"):
                        sa["attacker_idx"]=next((i for i,uu in enumerate(enemies if sa.get("side")=="enemy" else my_heroes) if uu.get("name")==sa.get("attacker_name")),0)
                        sa["hero_eff_atk"]=cstat(unit,"atk",unit["atk"])
                        sa["hero_eff_crit"]=cstat(unit,"crit",unit["crit"])
                        sa["hero_base_atk"]=unit["atk"]
                        sa["hero_base_crit"]=unit["crit"]
                        sa["hero_buffs"]=[b["stat"] for b in unit.get("buffs",[])]
                        sa["hero_debuffs"]=[d["stat"] for d in unit.get("debuffs",[])]
                        sa["attacker_color"]=unit.get("color","#888")
                        sa["attacker_class"]=unit.get("class","")
                        sa["attacker_quality"]=unit.get("quality","")
                        all_actions.append(sa)
                        continue
                    all_actions.append(sa)
                    # 注入当时HP状态
                    a2=sa
                    if a2.get("type")!="card_play":
                        a2["attacker_idx"]=next((i for i,uu in enumerate(my_heroes) if uu.get("name")==a2.get("attacker_name")),0)
                        a2["hero_eff_atk"]=cstat(unit,"atk",unit["atk"])
                        a2["hero_eff_crit"]=cstat(unit,"crit",unit["crit"])
                        a2["hero_base_atk"]=unit["atk"]
                        a2["hero_base_crit"]=unit["crit"]
                        a2["hero_buffs"]=[b["stat"] for b in unit.get("buffs",[])]
                        a2["hero_debuffs"]=[d["stat"] for d in unit.get("debuffs",[])]
                        a2["attacker_color"]=unit.get("color","#888")
                        a2["attacker_class"]=unit.get("class","")
                        a2["attacker_quality"]=unit.get("quality","")
                        if a2.get("aoe") and a2.get("targets"):
                            tlist=enemies if a2.get("side")=="ally" else my_heroes
                            for tg in a2["targets"]:
                                uu=next((uu2 for uu2 in tlist if uu2.get("name")==tg.get("name")),None)
                                if uu: tg["hp_pct"]=max(0,uu["hp"]/max(1,uu["max_hp"])); tg["max_hp"]=uu["max_hp"]; tg["idx"]=next((i for i,uu2 in enumerate(tlist) if uu2.get("name")==tg.get("name")),0)
                                if tg.get("killed") and uu: tg["hp_pct"]=0
                        elif a2.get("target_name"):
                            tlist=enemies if a2.get("side")=="ally" else my_heroes
                            uu=next((uu2 for uu2 in tlist if uu2.get("name")==a2["target_name"]),None)
                            if uu: a2["target_hp_pct"]=max(0,uu["hp"]/max(1,uu["max_hp"])); a2["target_max_hp"]=uu["max_hp"]; a2["target_idx"]=next((i for i,uu2 in enumerate(tlist) if uu2.get("name")==a2["target_name"]),0)
                # (card system removed)

            # 复活检查（被动复活：女娲/刑天）
            if act.get("killed"):
                for h in my_heroes:
                    if not h.get("alive", True):
                        check_death_revive(h, my_heroes, enemies, passive_ctx)

            # 被动触发检查(效果引擎统一处理)
            process_all_post_action(my_heroes, enemies, passive_ctx, unit, act)

            # 检查战斗结束
            alive_h = [u for u in my_heroes if u.get("alive",True)]
            alive_e = [u for u in enemies if u.get("alive",True)]
            if not alive_h or not alive_e: break

        # 每轮(所有人行动完)抽牌+恢复打牌能量
        if actions_since_draw >= len([u for u in all_u if u.get("alive",True)]):
            actions_since_draw = 0
            tick_buffs(all_u)
        else:
            # 每2个动作轻微恢复
            if tick_no % 3 == 0:
                tick_buffs(all_u)

        alive_h = [u for u in my_heroes if u.get("alive",True)]
        alive_e = [u for u in enemies if u.get("alive",True)]
        if not alive_h: break
        if not alive_e: break

    win = len(alive_h) > 0 and len(alive_e) == 0

    # 构建战斗结果
    r = {"win": win, "rounds": tick_no, "initial_state": initial_state, "actions": all_actions}
    r["my_heroes"]=[{"name":f["name"],"class":f["class"],"quality":f["quality"],"color":f["color"],
        "max_hp":f["max_hp"],"atk":f["atk"],"crit":f["crit"],
        "eff_atk":cstat(f,"atk",f["atk"]),"eff_crit":cstat(f,"crit",f["crit"]),
        "hp_pct":max(0,f["hp"]/max(1,f["max_hp"])),"alive":f.get("alive",True),
        "shield_pct":f.get("shield",0)/max(1,f["max_hp"]),
        "energy":f.get("energy",0),"spd":f.get("spd",100),"position":f.get("position",0),
        "buffs":[b["stat"] for b in f.get("buffs",[])],
        "debuffs":[d["stat"] for d in f.get("debuffs",[])]} for f in my_heroes]
    r["enemies"]=[{"name":e["name"],"class":e["class"],"quality":e["quality"],"color":e["color"],
        "max_hp":e["max_hp"],"atk":e["atk"],
        "eff_atk":cstat(e,"atk",e["atk"]),"eff_crit":cstat(e,"crit",e["crit"]),
        "hp_pct":max(0,e["hp"]/max(1,e["max_hp"])),"alive":e.get("alive",True),
        "energy":e.get("energy",0),
        "buffs":[b["stat"] for b in e.get("buffs",[])],
        "debuffs":[d["stat"] for d in e.get("debuffs",[])],
        "prof_icon":e.get("prof_icon",""),
        "skill_name":e.get("skill_name",""),
        "skill_desc":e.get("skill_desc",""),
        "boss_passive_name":e.get("boss_passive_name",""),
        "boss_passive_desc":e.get("boss_passive_desc","")} for e in enemies]

    if win:
        dr=stage["drops"]; jr=dr["jade"]+random.randint(-3,8); jr=max(3,jr)
        r["jade_reward"]=jr
        r["equip_reward"]=None
        if random.random()<dr["equip_chance"] and dr["equip_pool"]:
            eid=random.choice(dr["equip_pool"])
            r["equip_reward"]=EQUIP_DATA.get(eid,{}).get("name","?")
    else:
        r["jade_reward"]=max(2,stage["drops"]["jade"]//4)
        r["failed"]=True

    return r

# ═══ 交互式战斗会话（杀戮尖塔式手牌） ═══
import copy as _copy
BATTLE_SESSIONS = {}

class BattleSession:
    def __init__(self, my_heroes, enemies, stage):
        self.my_heroes = my_heroes
        self.enemies = enemies
        self.stage = stage
        self.all_u = my_heroes + enemies
        self.all_actions = []
        self.tick_no = 0
        self.actions_since_draw = 0
        self.max_ticks = 250
        self.done = False
        self.result_cache = None
        self._last_sent = 0
        self.auto_mode = False
        self.passive_ctx = {"all_actions": self.all_actions, "passive_counters": {}, "triggered": set()}
        # 位置系统: 英雄按上阵顺序获得1-6号位
        for i, h in enumerate(my_heroes):
            h["position"] = i + 1
        for i, e in enumerate(enemies):
            e["position"] = 7 + i
        for u in self.all_u:
            u["action_bar"] = u.get("action_bar", random.randint(0, 400))
        # 神卡初始能量100（女娲满能量200）
        for h in self.my_heroes:
            hd = h.get("_hd", {})
            if hd.get("quality") == "神卡":
                h["energy"] = 200 if hd.get("id") == "nuwa" else 100
        self._init_passives()
        # 保存初始状态（用于前端第一帧渲染）
        self._initial_state = {
            "my_heroes": self._get_heroes_state(),
            "enemies": self._get_enemies_state(),
            "lv9_buffs": getattr(self, '_lv9_buffs', [])
        }

    def _init_passives(self):
        # 战斗开始被动 (效果引擎统一处理)
        process_all_battle_start(self.my_heroes, self.enemies, self.passive_ctx)

        # 装备战斗开始效果
        for h in self.my_heroes:
            process_equip_battle_start(h, self.my_heroes, self.enemies, self.passive_ctx)

        # Lv9位置天赋
        self._lv9_buffs=apply_lv9_bonuses(self.my_heroes, self.enemies)
        for b in self._lv9_buffs:
            self.all_actions.append({"side":"ally","type":"passive","attacker_name":"天赋","skill":b["desc"],"aoe":False,"target_name":"","msg":b["desc"]})

    def _inject_action_hp(self, a):
        """注入实时HP/idx到action"""
        # 多段AOE子动作: 只注入攻击者信息, 不覆盖渐进HP
        if a.get("total_hits") and a.get("aoe"):
            unit2 = next((u for u in self.my_heroes if u.get("name") == a.get("attacker_name")), None)
            if unit2:
                a["attacker_idx"] = next((i for i, uu in enumerate(self.my_heroes) if uu.get("name") == a.get("attacker_name")), 0)
                a["hero_eff_atk"] = cstat(unit2, "atk", unit2["atk"])
                a["hero_eff_crit"] = cstat(unit2, "crit", unit2["crit"])
                a["hero_base_atk"] = unit2["atk"]
                a["hero_base_crit"] = unit2["crit"]
                a["hero_buffs"] = [b["stat"] for b in unit2.get("buffs", [])]
                a["hero_debuffs"] = [d["stat"] for d in unit2.get("debuffs", [])]
                a["attacker_color"] = unit2.get("color", "#888")
                a["attacker_class"] = unit2.get("class", "")
                a["attacker_quality"] = unit2.get("quality", "")
            return
        u2=self.my_heroes+self.enemies
        a["attacker_idx"]=next((i for i,uu in enumerate(self.enemies if a.get("side")=="enemy" else self.my_heroes) if uu.get("name")==a.get("attacker_name")),0)
        if a.get("side")=="ally" or a.get("side")=="heal":
            unit=next((u for u in self.my_heroes if u.get("name")==a.get("attacker_name")),None)
            if unit:
                a["hero_eff_atk"]=cstat(unit,"atk",unit["atk"])
                a["hero_eff_crit"]=cstat(unit,"crit",unit["crit"])
                a["hero_base_atk"]=unit["atk"]
                a["hero_base_crit"]=unit["crit"]
                a["hero_buffs"]=[b["stat"] for b in unit.get("buffs",[])]
                a["hero_debuffs"]=[d["stat"] for d in unit.get("debuffs",[])]
        if a.get("aoe") and a.get("targets"):
            tlist=self.enemies if a.get("side")=="ally" else self.my_heroes
            for tg in a["targets"]:
                uu=next((uu2 for uu2 in tlist if uu2.get("name")==tg.get("name")),None)
                if uu: tg["hp_pct"]=max(0,uu["hp"]/max(1,uu["max_hp"])); tg["max_hp"]=uu["max_hp"]; tg["idx"]=next((i for i,uu2 in enumerate(tlist) if uu2.get("name")==tg.get("name")),0)
                if tg.get("killed") and uu: tg["hp_pct"]=0
        elif a.get("target_name"):
            tlist=self.enemies if a.get("side")=="ally" else self.my_heroes
            uu=next((uu2 for uu2 in tlist if uu2.get("name")==a["target_name"]),None)
            if uu: a["target_hp_pct"]=max(0,uu["hp"]/max(1,uu["max_hp"])); a["target_max_hp"]=uu["max_hp"]; a["target_idx"]=next((i for i,uu2 in enumerate(tlist) if uu2.get("name")==a["target_name"]),0)
            if a.get("killed") and uu: a["target_hp_pct"]=0

    def _run_one_unit(self, unit):
        """执行一个单位的行动(速度轴)"""
        u2=self.my_heroes+self.enemies
        if unit.get("side")=="enemy":
            can_skill=unit["energy"]>=unit.get("skill_cost",100)
            if can_skill:
                unit["energy"]-=unit.get("skill_cost",100)
                act=enemy_use_skill(unit,self.my_heroes,self.enemies)
            else:
                unit["energy"]+=min(40,unit.get("energy_gain",25))
                act=enemy_basic_attack(unit,self.my_heroes,self.enemies)
            self.all_actions.append(act)
            self._inject_action_hp(act)
        else:
            hd=unit.get("_hd")
            sk_cost=unit.get("_skill_cost",100)
            can_skill=unit["energy"]>=sk_cost
            if can_skill:
                unit["energy"]-=sk_cost
                act=hero_use_skill(unit,self.my_heroes,self.enemies)
                # 吸血治疗记录
                if act.get("lifesteal_heal", 0) > 0:
                    self.all_actions.append({"side":"heal","type":"lifesteal","attacker_name":unit["name"],
                        "skill":"吸血","aoe":False,"target_name":unit["name"],
                        "heal":act["lifesteal_heal"],
                        "msg":f"{unit['name']}吸血+{act['lifesteal_heal']}❤️"})
                # 武圣/on_skill被动 (效果引擎)
                process_effects("on_skill", unit, hd, unit.get("_skill_lv",1), self.my_heroes, self.enemies, self.passive_ctx)
            else:
                eg=hd.get("basic_energy_gain",25) if hd else 25
                unit["energy"]+=eg
                if unit["energy"]>200: unit["energy"]=200
                act=hero_basic_attack(unit,self.my_heroes,self.enemies)
            act["energy_after"]=unit["energy"]
            # 多段拆分
            mhc=act.get("multi_hit_count",1)
            sub_acts=expand_multi_hit_action(act,mhc) if mhc>1 else [act]
            for sa in sub_acts:
                self.all_actions.append(sa)
                self._inject_action_hp(sa)
            # 复活检查（被动复活：女娲/刑天）
            if act.get("killed"):
                for h in self.my_heroes:
                    if not h.get("alive", True):
                        check_death_revive(h, self.my_heroes, self.enemies, self.passive_ctx)

            # 被动触发检查 (效果引擎统一处理)
            process_all_post_action(self.my_heroes, self.enemies, self.passive_ctx, unit, act)

    def step_until_card(self):
        """运行自动行动直到需要打牌或战斗结束。返回 {'phase':'card'|'done'|'continue', hand, card_energy, ...}"""
        while self.tick_no < self.max_ticks and not self.done:
            self.tick_no += 1
            alive_h=[u for u in self.my_heroes if u.get("alive",True)]
            alive_e=[u for u in self.enemies if u.get("alive",True)]
            if not alive_h or not alive_e: break

            for u in self.all_u:
                if u.get("alive",True):
                    spd=cstat(u,"spd",u.get("spd",100))
                    u["action_bar"]+=spd

            ready=sorted(
                [u for u in self.all_u if u.get("alive",True) and u["action_bar"]>=1000],
                key=lambda x:-x["action_bar"]
            )
            if not ready: continue

            for unit in ready:
                if not unit.get("alive",True): continue
                unit["action_bar"]-=1000
                self.actions_since_draw+=1
                self._run_one_unit(unit)

                # 检查战斗结束
                alive_h=[u for u in self.my_heroes if u.get("alive",True)]
                alive_e=[u for u in self.enemies if u.get("alive",True)]
                if not alive_h or not alive_e: break
            if not alive_h or not alive_e: break

            tick_buffs(self.all_u)
            continue

        # 战斗结束
        self.done=True
        return self._make_result()

    def _get_new_actions(self):
        acts = self.all_actions[self._last_sent:]
        self._last_sent = len(self.all_actions)
        return acts

    # (card system removed)

    def _get_heroes_state(self):
        return [{"name":f["name"],"class":f["class"],"quality":f["quality"],"color":f["color"],
            "max_hp":f["max_hp"],"atk":f["atk"],"crit":f["crit"],
            "eff_atk":cstat(f,"atk",f["atk"]),"eff_crit":cstat(f,"crit",f["crit"]),
            "hp_pct":max(0,f["hp"]/max(1,f["max_hp"])),"alive":f.get("alive",True),
            "energy":f.get("energy",0),"spd":f.get("spd",100),"position":f.get("position",0),
            "buffs":[b["stat"] for b in f.get("buffs",[])],
            "debuffs":[d["stat"] for d in f.get("debuffs",[])]} for f in self.my_heroes]

    def _get_enemies_state(self):
        return [{"name":e["name"],"class":e["class"],"quality":e["quality"],"color":e["color"],
            "max_hp":e["max_hp"],"atk":e["atk"],
            "hp_pct":max(0,e["hp"]/max(1,e["max_hp"])),"alive":e.get("alive",True),
            "energy":e.get("energy",0), "spd":e.get("spd",100),
            "buffs":[b["stat"] for b in e.get("buffs",[])],
            "debuffs":[d["stat"] for d in e.get("debuffs",[])],
            "prof_icon":e.get("prof_icon",""),
            "skill_name":e.get("skill_name",""),
            "skill_desc":e.get("skill_desc",""),
            "boss_passive_name":e.get("boss_passive_name",""),
            "boss_passive_desc":e.get("boss_passive_desc","")} for e in self.enemies]

    def _make_result(self):
        win=len([u for u in self.my_heroes if u.get("alive",True)])>0 and len([u for u in self.enemies if u.get("alive",True)])==0
        r={"win":win,"rounds":self.tick_no,"phase":"done","initial_state":self._initial_state,
           "actions":self._get_new_actions(),
           "my_heroes":self._get_heroes_state(),"enemies":self._get_enemies_state()}
        if win:
            dr=self.stage["drops"]; jr=dr["jade"]+random.randint(-3,8); jr=max(3,jr)
            r["jade_reward"]=jr
            r["exp_reward"]=exp_for_stage(self.stage.get("_index",1)) if self.stage else 50
            if random.random()<dr["equip_chance"] and dr["equip_pool"]:
                eid=random.choice(dr["equip_pool"])
                r["equip_reward"]=EQUIP_DATA.get(eid,{}).get("name","?")
        else:
            r["jade_reward"]=max(2,self.stage["drops"]["jade"]//4)
            r["failed"]=True
        r["phase"]="done"
        return r

    def play_card(self, card_id):
        return self.step_until_card()
    def skip_card(self):
        return self.step_until_card()
    def auto_play_card(self):
        pass
    def _do_auto_play(self, card):
        pass

    def _get_current_phase(self):
        if self.done:
            return "done"
        alive_h=[u for u in self.my_heroes if u.get("alive",True)]
        alive_e=[u for u in self.enemies if u.get("alive",True)]
        if not alive_h or not alive_e:
            return "done"
        return "card"

def init_battle_session(g):
    """从游戏状态创建战斗会话"""
    stage=gen_stage(g["stage_index"])
    my_heroes=[]
    for hid in g["lineup"]:
        inv=next((i for i in g["inventory"] if i["hero_id"]==hid),None)
        if not inv: continue
        f=h2f(hid,inv,g.get("equip_bag",[]))
        if f: my_heroes.append(f)
    enemies=gen_enemy_formation(stage["power"]/6.0, boss=stage["boss"])
    return BattleSession(my_heroes, enemies, stage)

# ═══ 交互式战斗API ═══
@app.route("/api/battle/start", methods=["POST"])
@login_required
def api_battle_start():
    g=load_game()
    if not g: return jsonify({"error":"未找到存档"})
    if not g["lineup"]: return jsonify({"error":"请先上阵英雄"})
    sess=init_battle_session(g)
    uid=session.get("user_id")
    BATTLE_SESSIONS[uid]=sess
    result=sess.step_until_card()
    result["auto"]=True
    if result.get("phase")=="done":
        _apply_battle_rewards(result, uid)
    return jsonify(result)

def _apply_battle_rewards(result,uid):
    """战斗结束: 保存奖励+经验"""
    if result.get("phase")!="done": return
    del BATTLE_SESSIONS[uid]
    g=load_game()
    if not g: return
    if result["win"]:
        jr=result.get("jade_reward",3)
        si=g["stage_index"]
        g["stage_index"]+=1
        if result.get("equip_reward"):
            ex=next((e for e in g["equip_bag"] if isinstance(e,dict) and e.get("id")==result["equip_reward"]),None)
            if ex: ex["count"]+=1
            else: g["equip_bag"].append({"id":result["equip_reward"],"count":1})
        exp_msg=add_exp_to_heroes(g,si)
        result["exp_msg"]=exp_msg
    else:
        g["jade"]+=result.get("jade_reward",2)
    g["ticks"]+=1; save_game(g)
    r2=to_client(g) if g else {}
    result["stage_name"]=r2.get("stage",{}).get("name","")
    result["new_stage"]=r2.get("stage",{}).get("name","")
    result["game_state"]=r2  # 返回完整游戏状态供前端直接用

def hero_basic_attack(unit, allies, enemies):
    hd = unit.get("_hd")
    if not hd: return {"side":"ally","type":"basic","attacker_name":unit["name"],"damage":0}
    bdmg = hd.get("basic_dmg_pct", 1.0)
    target_mode = hd.get("basic_target", "single")
    is_aoe = hd.get("basic_aoe", False)
    skill_name = hd.get("basic_name", "攻击")

    targets = []
    if target_mode == "all_ally":
        targets = [a for a in allies if a.get("alive",True)]
        # 治疗型普攻
        for t in targets:
            heal_pct = 0
            for sp in hd.get("basic_special", []):
                if sp.get("type") == "heal":
                    heal_pct = sp.get("pct", 0)
            heal = int(t["max_hp"] * heal_pct)
            t["hp"] = min(t["max_hp"], t["hp"] + heal)
        return {"side":"heal","type":"basic","attacker_name":unit["name"],"skill":skill_name,
                "aoe":True,"targets":[{"name":t["name"],"heal":int(t["max_hp"]*(next((s["pct"] for s in hd.get("basic_special",[]) if s["type"]=="heal"),0)))} for t in targets]}
    elif target_mode == "lowest_hp_ally":
        targets = [min([a for a in allies if a.get("alive",True)], key=lambda x: x["hp"])] if [a for a in allies if a.get("alive",True)] else []
        if targets:
            heal_pct = 0
            for sp in hd.get("basic_special", []):
                if sp.get("type") == "heal":
                    heal_pct = sp.get("pct", 0)
            heal = int(targets[0]["max_hp"] * heal_pct)
            targets[0]["hp"] = min(targets[0]["max_hp"], targets[0]["hp"] + heal)
            return {"side":"heal","type":"basic","attacker_name":unit["name"],"skill":skill_name,
                    "aoe":False,"target_name":targets[0]["name"],"heal":heal}
        return {"side":"heal","type":"basic","attacker_name":unit["name"],"skill":skill_name,"damage":0}

    # 攻击型普攻
    alive_e = [e for e in enemies if e.get("alive",True)]
    tars = get_targets(alive_e, target_mode)
    if not tars: return {"side":"ally","type":"basic","attacker_name":unit["name"],"skill":skill_name,"damage":0}

    if is_aoe and len(tars) > 1:
        targets_data=[]
        for t in tars:
            if not t.get("alive",True): continue
            ba = cstat(unit, "atk", unit["atk"])
            d = int(ba * bdmg * random.uniform(0.85, 1.0))
            cr = random.random() < unit["crit"]/100
            if cr: d = int(d * get_effective_crit_dmg(unit))
            dr = get_dmg_reduce(t)
            d = int(d * (1 - dr))
            r = apply_dmg(t, d)
            killed = t["hp"] <= 0
            if killed: t["alive"] = False
            targets_data.append({"name":t["name"],"damage":r["damage"],"crit":cr,"killed":killed})
        # 应用普攻特殊效果
        apply_basic_specials(unit, hd, tars)
        return {"side":"ally","type":"basic","attacker_name":unit["name"],"skill":skill_name,
                "aoe":True,"targets":targets_data}
    else:
        t = tars[0]
        ba = cstat(unit, "atk", unit["atk"])
        d = int(ba * bdmg * random.uniform(0.85, 1.0))
        cr = random.random() < unit["crit"]/100
        if cr: d = int(d * get_effective_crit_dmg(unit))
        dr = get_dmg_reduce(t)
        d = int(d * (1 - dr))
        r = apply_dmg(t, d)
        killed = t["hp"] <= 0
        if killed: t["alive"] = False
        # 多段hit
        hit_count = 1
        for sp in hd.get("basic_special", []):
            if sp.get("type") == "multi_hit":
                hit_count = sp.get("count", 1)
        if hit_count > 1:
            for _ in range(hit_count - 1):
                d2 = int(ba * bdmg * random.uniform(0.85, 1.0))
                cr2 = random.random() < unit["crit"]/100
                if cr2: d2 = int(d2 * get_effective_crit_dmg(unit))
                dr2 = get_dmg_reduce(t)
                d2 = int(d2 * (1 - dr2))
                r2 = apply_dmg(t, d2)
                d += r2["damage"]
                if t["hp"] <= 0: t["alive"]=False; break
        apply_basic_specials(unit, hd, [t])
        return {"side":"ally","type":"basic","attacker_name":unit["name"],"skill":skill_name,
                "aoe":False,"target_name":t["name"],"damage":d,"crit":cr,"killed":killed}

def apply_basic_specials(unit, hd, targets):
    for sp in hd.get("basic_special", []):
        t = sp.get("type")
        if t == "self_buff":
            unit["buffs"].append({"stat":sp["stat"],"pct":sp["pct"],"dur":sp.get("dur",1)})
        elif t == "heal":
            pass  # 已在heal逻辑处理
        elif t == "lifesteal":
            pass  # 简化
        elif t == "self_heal":
            heal = int(unit["max_hp"] * sp.get("pct", 0.05))
            unit["hp"] = min(unit["max_hp"], unit["hp"] + heal)
        elif t == "debuff":
            for tar in targets:
                if random.random() < sp.get("chance", 1.0):
                    tar["debuffs"].append({"stat":"stun","pct":1,"dur":sp.get("dur",1)})
                    tar["stunned"] = True
        elif t == "protect":
            pass
        elif t == "dodge":
            unit["buffs"].append({"stat":"dodge","pct":1.0,"dur":sp.get("dur",1)})
        elif t == "burn":
            for tar in targets:
                tar["debuffs"].append({"stat":"burn","pct":sp.get("pct",0.05),"dur":sp.get("dur",2)})

# ═══ 技能升级 & 被动触发系统 ═══
def apply_skill_upgrades(unit, hd, sk_lv, allies, enemies, sk_context):
    """根据技能等级应用升级效果。返回额外buff/debuff列表。"""
    if not hd: return {}
    hid=hd["id"]; extra={}
    if sk_lv<3: return extra

    # ─── Lv3 ───
    if sk_lv>=3:
        if hid=="huatuo":
            if not extra.get("extra_buffs"): extra["extra_buffs"]=[]
            extra["extra_buffs"].append({"stat":"crit","pct":0.2,"dur":2})
        if hid=="caiwenji":
            for t in (allies if hd.get("skill_target")=="all_ally" else []):
                if t.get("alive",True): t["shield"]=int(t["max_hp"]*0.3)
        if hid=="xiahoudun":
            pass  # 自损从10%→5%在hero_use_skill处理
        if hid=="dianwei":
            if not extra.get("extra_buffs"): extra["extra_buffs"]=[]
            extra["extra_buffs"].append({"stat":"immune","pct":1.0,"dur":hd.get("skill_buffs",[{"dur":3}])[0]["dur"]})
        if hid=="zhangfei":
            for e in enemies:
                if e.get("alive",True): e["debuffs"].append({"stat":"atk","pct":-0.25,"dur":3})
        if hid=="guanyu":
            if not extra.get("extra_buffs"): extra["extra_buffs"]=[]
            extra["extra_buffs"].append({"stat":"immune","pct":1.0,"dur":1})
        if hid=="lihai":
            extra["extra_hits"]=1  # 第四剑
        if hid=="zhaoyun":
            extra["lifesteal_pct"]=0.2  # 赵云Lv3: 吸血20%
        if hid=="luobu":
            extra["stun_all"]=True  # 吕布Lv3: 击退眩晕
        if hid=="diaochan":
            if not extra.get("extra_buffs"): extra["extra_buffs"]=[]
            extra["extra_buffs"].append({"stat":"dodge_refresh","pct":1.0,"dur":1})
        if hid=="zhugeliang":
            extra["extra_damage"]=0.5  # 诸葛亮Lv3: 额外50%攻击伤害
        if hid=="yangyouji":
            if not extra.get("extra_buffs"): extra["extra_buffs"]=[]
            extra["extra_buffs"].append({"stat":"crit","pct":0.3,"dur":2})
        # ─── 神卡 Lv3 ───
        if hid=="wukong":
            extra["lifesteal_pct"]=0.6  # 孙悟空Lv3: 吸血60%

    # ─── Lv5 ───
    if sk_lv>=5:
        if hid=="zhaoyun":
            extra["prefer_back_row"]=True  # 赵云Lv5: 优先攻击后排
        if hid=="zhangfei":
            extra["extra_shield_pct"]=True  # 张飞Lv5: 护盾破碎爆炸(额外15%护盾)
        if hid=="guanyu":
            extra["bleed_pct"]=0.2  # 关羽Lv5: 刀气留痕20%×3回合
        if hid=="lihai":
            extra["hit_dmg_pct"]=1.2  # 李白Lv5: 每剑120%
        if hid=="diaochan":
            if not extra.get("extra_buffs"): extra["extra_buffs"]=[]
            extra["extra_buffs"].append({"stat":"burn_dmg","pct":0.15,"dur":2})  # 貂蝉Lv5: 灼烧
        if hid=="huatuo":
            if not extra.get("extra_buffs"): extra["extra_buffs"]=[]
            extra["extra_buffs"].append({"stat":"shield_bonus","pct":0.15,"dur":2})  # 华佗Lv5: 附加护盾
        if hid=="yangyouji":
            extra["quad_crit"]=True  # 养由基Lv5: 暴击4倍
        if hid=="luobu":
            pass  # 吕布Lv5: 单目标伤害翻倍(在_run_one_unit里处理)
        # ─── 神卡 Lv5 ───
        if hid=="wukong":
            extra["extra_hits"]=2  # 孙悟空Lv5: 额外2次攻击

    # ─── Lv7 ───
    if sk_lv>=7:
        if hid=="zhaoyun":
            extra["execute_pct"]=0.15  # 赵云Lv7: 终结一击，血量低于15%直接斩杀
        if hid=="zhangfei":
            if not extra.get("extra_buffs"): extra["extra_buffs"]=[]
            extra["extra_buffs"].append({"stat":"invincible","pct":1.0,"dur":1})  # 张飞Lv7: 全体无敌1回合
        if hid=="guanyu":
            extra["execute_pct"]=0.2  # 关羽Lv7: 血量低于20%斩杀
        if hid=="lihai":
            extra["guaranteed_crit"]=True  # 李白Lv7: 剑开天门必定暴击
            extra["extra_damage"]=5.0  # 剑开天门: ATK×5真实伤害
        if hid=="diaochan":
            pass  # 貂蝉Lv7: 溅射50%
        if hid=="huatuo":
            pass  # 华佗Lv7: 免疫结束重置冷却
        # ─── 神卡 Lv7 ───
        if hid=="wukong":
            extra["execute_pct"]=0.15  # 孙悟空Lv7: 15%斩杀
            extra["guaranteed_crit"]=True
        if hid=="houyi":
            extra["execute_pct"]=0.7  # 后羿Lv7: 70%斩杀(虽然数据写了50%基础，但Lv7升级)
    return extra

# ═══ Lv9 位置天赋系统 ═══
HERO_LV9_EFFECTS = {
    "lisi":         {"pos":1,"desc":"开局给1号位+15%攻击","type":"stat","stat":"atk","pct":0.15},
    "wangdazhuang": {"pos":2,"desc":"开局给2号位+20%血量","type":"stat","stat":"hp","pct":0.20},
    "xiaocui":      {"pos":3,"desc":"开局给3号位+15%治疗量","type":"buff","buff":"heal_buff","dur":-1},
    "zhangtiezhu":  {"pos":4,"desc":"开局给4号位+8%暴击","type":"stat","stat":"crit","val":8},
    "huangzhong":   {"pos":4,"desc":"开局给4号位+20%攻击","type":"stat","stat":"atk","pct":0.20},
    "guojia":       {"pos":5,"desc":"开局给5号位+40能量","type":"energy","val":40},
    "yanshisan":    {"pos":6,"desc":"开局给6号位+15%暴击","type":"stat","stat":"crit","val":15},
    "xiahoudun":    {"pos":1,"desc":"开局给1号位+30%血量+反弹","type":"combo","hp_pct":0.3,"buff":"reflect"},
    "caiwenji":     {"pos":3,"desc":"开局给3号位25%护盾","type":"shield","pct":0.25},
    "ganning":      {"pos":6,"desc":"开局给6号位+25%攻击","type":"stat","stat":"atk","pct":0.25},
    "dianwei":      {"pos":2,"desc":"开局给2号位+40%血量","type":"stat","stat":"hp","pct":0.40},
    "huatuo":       {"pos":3,"desc":"开局给3号位30%护盾+回血15%","type":"combo","shield_pct":0.3,"heal_pct":0.15},
    "yangyouji":    {"pos":4,"desc":"开局给4号位+30%攻击+首击必暴","type":"combo","atk_pct":0.30,"buff":"guaranteed_crit"},
    "luobu":        {"pos":1,"desc":"开局给1号位+40%攻击","type":"stat","stat":"atk","pct":0.40},
    "zhugeliang":   {"pos":5,"desc":"开局给5号位+60能量+技能增伤","type":"combo","energy":60,"buff":"skill_dmg"},
    "pangtong":     {"pos":5,"desc":"开局给5号位锁链(全体易伤)","type":"debuff_aura"},
    "lihai":        {"pos":6,"desc":"开局给6号位+50%攻击+15%吸血","type":"combo","atk_pct":0.50,"buff":"lifesteal"},
    "zhangfei":     {"pos":2,"desc":"开局给2号位+50%血量+30%护盾","type":"combo","hp_pct":0.50,"shield_pct":0.30},
    "diaochan":     {"pos":6,"desc":"开局给6号位+15暴击+魅惑闪避","type":"combo","crit_val":15,"buff":"dodge"},
    "zhaoyun":      {"pos":1,"desc":"开局给1号位满能量","type":"full_energy"},
    "guanyu":       {"pos":1,"desc":"开局给1号位+30%攻击+武圣降临","type":"combo","atk_pct":0.30,"buff":"guaranteed_crit"},
    "wukong":       {"pos":1,"desc":"开局给1号位+60%攻击+15%吸血","type":"combo","atk_pct":0.60,"buff":"lifesteal"},
    "qinshihuang":  {"pos":5,"desc":"开局给5号位+80能量+受伤+40%","type":"combo","energy":80,"buff":"dmg_taken_up"},
    "xingtian":     {"pos":2,"desc":"开局给2号位+60%血量+反伤","type":"combo","hp_pct":0.60,"buff":"reflect"},
    "houyi":        {"pos":4,"desc":"开局给4号位+40%攻击+首击必暴","type":"combo","atk_pct":0.40,"buff":"guaranteed_crit"},
    "nuwa":         {"pos":3,"desc":"开局给3号位50%护盾+回血30%","type":"combo","shield_pct":0.50,"heal_pct":0.30},
    "chiyou":       {"pos":2,"desc":"开局给2号位+60%血量+40%护盾","type":"combo","hp_pct":0.60,"shield_pct":0.40},
}

def apply_lv9_bonuses(my_heroes, enemies):
    """根据阵中Lv9英雄，给对应位置施加开局增益"""
    applied = []
    for u in my_heroes:
        hd = u.get("_hd")
        if not hd: continue
        sk_lv = u.get("_skill_lv", 1)
        if sk_lv < 9: continue
        hid = hd["id"]
        ef = HERO_LV9_EFFECTS.get(hid)
        if not ef: continue
        target_pos = ef["pos"]
        target = next((x for x in my_heroes if x.get("position") == target_pos), None)
        if not target: continue
        etype = ef["type"]

        if etype == "stat":
            if ef["stat"] == "atk":
                target["atk"] = int(target["atk"] * (1 + ef["pct"]))
            elif ef["stat"] == "hp":
                target["max_hp"] = int(target["max_hp"] * (1 + ef["pct"]))
                target["hp"] = int(target["hp"] * (1 + ef["pct"]))
            elif ef["stat"] == "crit":
                target["crit"] += ef.get("val", 0)
            applied.append({"pos":target_pos,"desc":ef["desc"]})

        elif etype == "energy":
            target["energy"] = min(200, target["energy"] + ef.get("val", 0))
            applied.append({"pos":target_pos,"desc":ef["desc"]})

        elif etype == "shield":
            target["shield"] = int(target["max_hp"] * ef["pct"])
            applied.append({"pos":target_pos,"desc":ef["desc"]})

        elif etype == "full_energy":
            target["energy"] = 200
            applied.append({"pos":target_pos,"desc":"开局满能量!"})

        elif etype == "buff":
            if ef.get("buff") == "heal_buff":
                target["buffs"].append({"stat":"atk","pct":0.15,"dur":-1})
            applied.append({"pos":target_pos,"desc":ef["desc"]})

        elif etype == "combo":
            if ef.get("atk_pct"):
                target["atk"] = int(target["atk"] * (1 + ef["atk_pct"]))
            if ef.get("hp_pct"):
                target["max_hp"] = int(target["max_hp"] * (1 + ef["hp_pct"]))
                target["hp"] = int(target["hp"] * (1 + ef["hp_pct"]))
            if ef.get("crit_val"):
                target["crit"] += ef["crit_val"]
            if ef.get("energy"):
                target["energy"] = min(200, target["energy"] + ef["energy"])
            if ef.get("shield_pct"):
                target["shield"] = int(target["max_hp"] * ef["shield_pct"])
            if ef.get("heal_pct"):
                target["hp"] = min(target["max_hp"], target["hp"] + int(target["max_hp"] * ef["heal_pct"]))
            b = ef.get("buff")
            if b == "reflect": target["buffs"].append({"stat":"reflect","pct":1.0,"dur":-1})
            elif b == "guaranteed_crit": target["buffs"].append({"stat":"guaranteed_crit","pct":1.0,"dur":-1})
            elif b == "lifesteal": target["buffs"].append({"stat":"lifesteal","pct":0.15,"dur":-1})
            elif b == "dodge": target["buffs"].append({"stat":"dodge","pct":1.0,"dur":-1})
            elif b == "skill_dmg": target["buffs"].append({"stat":"atk_mult","pct":0.25,"dur":-1})
            applied.append({"pos":target_pos,"desc":ef["desc"]})

        elif etype == "debuff_aura":
            for e in enemies:
                if e.get("alive", True):
                    e["debuffs"].append({"stat":"dmg_reduce","pct":-0.25,"dur":-1})
            applied.append({"pos":0,"desc":"全体敌人易伤25%"})
    return applied

def check_passives(unit, hd, sk_lv, allies, enemies, event, context):
    """检查并触发被动。event: 'on_ally_low_hp','on_kill','on_start','on_hit'"""
    if not hd: return
    hid=hd["id"]
    # 华佗: 妙手回春 - 队友低于30%自动回复15%(每场2次)
    if hid=="huatuo" and event=="on_ally_low_hp":
        # 通过context.get("passive_counter",0)追踪触发次数
        triggers=context.get("passive_counters",{}).get("huatuo_miaoshou",0)
        max_triggers=2+(1 if sk_lv>=3 else 0)
        if triggers<max_triggers:
            lowest=min([a for a in allies if a.get("alive",True)],key=lambda x:x["hp"]/max(1,x["max_hp"]))
            if lowest and lowest["hp"]/max(1,lowest["max_hp"])<0.3:
                heal_pct=0.15+(0.15 if sk_lv>=5 else 0)
                heal=int(lowest["max_hp"]*heal_pct)
                lowest["hp"]=min(lowest["max_hp"],lowest["hp"]+heal)
                if "passive_counters" not in context: context["passive_counters"]={}
                context["passive_counters"]["huatuo_miaoshou"]=triggers+1
                context["all_actions"].append({"side":"heal","type":"passive","attacker_name":hd["name"],
                    "skill":"妙手回春","aoe":False,"target_name":lowest["name"],"heal":heal,"msg":f"被动触发:回复{lowest['name']}{heal}❤️"})
    # 蔡文姬: 悲歌 - 队友死亡全体回复25%
    if hid=="caiwenji" and event=="on_ally_die":
        heal_pct=0.25+(0.15 if sk_lv>=3 else 0)
        for a in allies:
            if a.get("alive",True):
                heal=int(a["max_hp"]*heal_pct)
                a["hp"]=min(a["max_hp"],a["hp"]+heal)
    # 吕布: 无双 - 每击败敌人+20%攻击
    if hid=="luobu" and event=="on_kill":
        max_layer=3+(1 if sk_lv>=3 else 0)
        layer=context.get("passive_counters",{}).get("luobu_wushuang",0)+1
        if layer<=max_layer:
            if "passive_counters" not in context: context["passive_counters"]={}
            context["passive_counters"]["luobu_wushuang"]=layer
            atk_pct=0.2+(0.05 if sk_lv>=5 else 0)
            unit["buffs"].append({"stat":"atk","pct":atk_pct,"dur":-1})
    # 关羽: 武圣 - 第一次技能必定暴击+伤害50%
    if hid=="guanyu" and event=="on_skill":
        if context.get("passive_counters",{}).get("guanyu_wusheng",0)==0:
            if "passive_counters" not in context: context["passive_counters"]={}
            context["passive_counters"]["guanyu_wusheng"]=1
            unit["buffs"].append({"stat":"guaranteed_crit","pct":1.0,"dur":1})
            unit["buffs"].append({"stat":"skill_dmg_pct","pct":0.5,"dur":1})
    # 张飞: 万人敌 - 每受一次攻击+5%攻击(最多10层)
    if hid=="zhangfei" and event=="on_hit":
        max_layer=10+(5 if sk_lv>=3 else 0)
        counter=context.get("passive_counters",{}).get("zhangfei_wanrendi",0)
        if counter<max_layer:
            layer=counter+1
            if "passive_counters" not in context: context["passive_counters"]={}
            context["passive_counters"]["zhangfei_wanrendi"]=layer
            atk_pct=0.05
            if sk_lv>=7: atk_pct=0.1  # Lv7: 翻倍
            unit["buffs"].append({"stat":"atk","pct":atk_pct,"dur":-1})
            # Lv5: 每层额外5%减伤
            if sk_lv>=5:
                unit["buffs"].append({"stat":"dmg_reduce","pct":0.05,"dur":-1})
    # 赵云: 一身是胆 - 每损失10%血量+8%攻击
    if hid=="zhaoyun" and event=="on_hit":
        hp_pct=unit["hp"]/max(1,unit["max_hp"])
        lost=1.0-hp_pct
        layers=int(lost//0.1)  # 每10%一层
        counter_name="zhaoyun_yishen"
        current=context.get("passive_counters",{}).get(counter_name,0)
        if layers>current:
            if "passive_counters" not in context: context["passive_counters"]={}
            context["passive_counters"][counter_name]=layers
            # 移除旧buff重新加
            unit["buffs"]=[b for b in unit.get("buffs",[]) if b["stat"]!="zhaoyun_atk" and b["stat"]!="zhaoyun_crit"]
            atk_pct=0.08+(0.08 if sk_lv>=7 else 0)  # Lv7翻倍
            for _ in range(layers):
                unit["buffs"].append({"stat":"atk","pct":atk_pct,"dur":-1})
            # Lv3: 每10%额外+5%暴击
            if sk_lv>=3:
                crit_pct=0.05
                for _ in range(layers):
                    unit["buffs"].append({"stat":"crit","pct":crit_pct,"dur":-1})
    # 李白: 斗酒诗百篇 - 每击败敌人+12%攻击(最多5层)
    if hid=="lihai" and event=="on_kill":
        max_layer=5+(3 if sk_lv>=3 else 0)
        counter=context.get("passive_counters",{}).get("lihai_doujiu",0)
        if counter<max_layer:
            layer=counter+1
            if "passive_counters" not in context: context["passive_counters"]={}
            context["passive_counters"]["lihai_doujiu"]=layer
            atk_pct=0.12+(0.08 if sk_lv>=5 else 0)  # Lv5: +20%
            unit["buffs"].append({"stat":"atk","pct":atk_pct,"dur":-1})

def hero_use_skill(unit, allies, enemies):
    hd = unit.get("_hd")
    if not hd: return {"side":"ally","type":"skill","attacker_name":unit["name"],"damage":0}
    sk_name = hd["skill_name"]
    is_aoe = hd.get("skill_aoe", False)
    target_mode = hd.get("skill_target", "all_enemy")
    specials = hd.get("skill_special", [])

    # 治疗型技能
    if hd.get("skill_heal_pct", 0) > 0:
        sk_lv=unit.get("_skill_lv",1)
        if target_mode == "all_ally":
            targets = [a for a in allies if a.get("alive",True)]
            for t in targets:
                heal = int(t["max_hp"] * hd["skill_heal_pct"])
                t["hp"] = min(t["max_hp"], t["hp"] + heal)
            buff_tags=[]
            for b in hd.get("skill_buffs",[]):
                for t in targets:
                    t["buffs"].append({"stat":b["stat"],"pct":b["pct"],"dur":b["dur"]})
                buff_tags.append(b["stat"])
            if "immunity" in specials:
                for t in targets:
                    t["buffs"].append({"stat":"immune","pct":1.0,"dur":2})
                buff_tags.append("immune")
            if "revive" in specials:
                # 女娲补天: 复活已死亡队友
                rpct = 0.20  # 基础复活血量20%
                if sk_lv >= 5: rpct = 0.40  # lv5: 40%
                revived = []
                for a in allies:
                    if not a.get("alive", True):
                        a["alive"] = True
                        a["hp"] = int(a["max_hp"] * rpct)
                        a["shield"] = 0
                        a["buffs"] = []
                        a["debuffs"] = []
                        a["stunned"] = False
                        a["frozen"] = False
                        revived.append(a["name"])
                        if sk_lv >= 5:
                            a["shield"] = int(a["max_hp"] * 0.30)
                if revived:
                    buff_tags.append(f"复活:{','.join(revived)}")
            # 技能升级效果
            up=apply_skill_upgrades(unit, hd, sk_lv, allies, enemies, {})
            if up and up.get("extra_buffs"):
                for b in up["extra_buffs"]:
                    for t in targets:
                        t["buffs"].append({"stat":b["stat"],"pct":b["pct"],"dur":b["dur"]})
                    buff_tags.append(b["stat"])
            return {"side":"heal","type":"skill","attacker_name":unit["name"],"skill":sk_name,
                    "aoe":True,"targets":[{"name":t["name"],"heal":int(t["max_hp"]*hd["skill_heal_pct"])} for t in targets],
                    "buff_effects":buff_tags}
        else:
            targets = [min([a for a in allies if a.get("alive",True)], key=lambda x: x["hp"])] if [a for a in allies if a.get("alive",True)] else []
            if targets:
                heal = int(targets[0]["max_hp"] * hd["skill_heal_pct"])
                targets[0]["hp"] = min(targets[0]["max_hp"], targets[0]["hp"] + heal)
                return {"side":"heal","type":"skill","attacker_name":unit["name"],"skill":sk_name,
                        "aoe":False,"target_name":targets[0]["name"],"heal":heal}
            return {"side":"heal","type":"skill","attacker_name":unit["name"],"skill":sk_name,"damage":0}

    # 攻击型技能
    alive_e = [e for e in enemies if e.get("alive",True)]
    sk_lv=unit.get("_skill_lv",1)
    if target_mode == "self":
        # 自身buff技能 (典韦)
        buff_tags=[]
        for b in hd.get("skill_buffs",[]):
            unit["buffs"].append({"stat":b["stat"],"pct":b["pct"],"dur":b["dur"]})
            buff_tags.append(b["stat"])
        if "lifesteal" in specials:
            unit["buffs"].append({"stat":"lifesteal","pct":0.4,"dur":3})
            buff_tags.append("lifesteal")
        if "reflect" in specials:
            unit["buffs"].append({"stat":"reflect","pct":0.5,"dur":3})
            buff_tags.append("reflect")
        up=apply_skill_upgrades(unit, hd, sk_lv, allies, enemies, {})
        if up and up.get("extra_buffs"):
            for b in up["extra_buffs"]:
                unit["buffs"].append({"stat":b["stat"],"pct":b["pct"],"dur":b["dur"]})
                buff_tags.append(b["stat"])
        return {"side":"ally","type":"skill","attacker_name":unit["name"],"skill":sk_name,
                "buff_effects":buff_tags,
                "aoe":False,"target_name":unit["name"],"damage":0,"buffed":True}

    tars = get_targets(alive_e, target_mode)
    if not tars: return {"side":"ally","type":"skill","attacker_name":unit["name"],"skill":sk_name,"damage":0}

    # 计算技能伤害参数
    dmg_pct = hd.get("skill_dmg_pct", 1.0)
    
    # 获取技能等级升级效果
    up=apply_skill_upgrades(unit, hd, sk_lv, allies, enemies, {})
    
    # === 多段攻击处理 (multi_hit) ===
    multi_hit_count=1
    if "multi_hit" in specials:
        if hd["id"]=="zhaoyun": multi_hit_count=7
        elif hd["id"]=="lihai": multi_hit_count=3
        elif hd["id"]=="wukong": multi_hit_count=3
    multi_hit_count+=up.get("extra_hits",0) if up else 0

    # === multi_hit提升(李白Lv5: 每剑120%) ===
    hit_dmg_pct=dmg_pct
    if up and up.get("hit_dmg_pct"):
        hit_dmg_pct=up["hit_dmg_pct"]

    # === 自损技能 (夏侯惇) ===
    if "lifesteal" in specials and "拔矢" in sk_name:
        self_pct=0.1-(0.05 if sk_lv>=3 else 0)
        unit["hp"]=max(1,unit["hp"]-int(unit["max_hp"]*self_pct))

    # === 护盾(张飞: shield_ally) ===
    if "shield_ally" in specials:
        shield_pct=0.25+(0.15 if up and up.get("extra_shield_pct") else 0)
        for a in allies:
            if a.get("alive",True):
                a["shield"]=int(a["max_hp"]*shield_pct)

    # === 单目标伤害计算 (含特殊效果) ===
    def _process_dmg_target(t, is_aoe_flag):
        if not t.get("alive",True): return None
        ba=cstat(unit,"atk",unit["atk"])
        atk_mult=1.0
        for b in unit.get("buffs",[]):
            if b["stat"]=="atk_mult": atk_mult*=(1+b["pct"])
        d=int(ba*hit_dmg_pct*random.uniform(0.8,1.0)*atk_mult)
        cr=random.random()<unit["crit"]/100
        if "guaranteed_crit" in specials or (up and up.get("guaranteed_crit")):
            cr=True
        # 关羽武圣被动
        for b in unit.get("buffs",[]):
            if b["stat"]=="guaranteed_crit": cr=True
        if cr: d=int(d*get_effective_crit_dmg(unit))
        # 养由基Lv5: 暴击4倍
        if cr and up and up.get("quad_crit"): d*=4
        # 斩杀(先查技能升级阈值，再查基础斩杀)
        execute_threshold = None
        if up and up.get("execute_pct"):
            execute_threshold = up["execute_pct"]
        elif "execute" in specials:
            execute_threshold = 0.5  # 基础斩杀阈值50%
        if execute_threshold and t["hp"]/max(1,t["max_hp"])<execute_threshold:
            d=t["hp"]
        # 减伤
        dr=get_dmg_reduce(t)
        d=int(d*(1-dr))
        ignore="ignore_shield" in specials
        r=apply_dmg(t,d,ignore)
        killed=t["hp"]<=0
        if killed: t["alive"]=False
        # 吸血
        ls_heal = 0
        if "lifesteal" in specials:
            hid=unit.get("_hd",{}).get("id","")
            ls_pct=0.3
            if hid=="wukong": ls_pct=0.4  # 孙悟空基础40%吸血
            if up and up.get("lifesteal_pct"): ls_pct=up["lifesteal_pct"]
            ls_heal = int(r["damage"]*ls_pct)
            unit["hp"]=min(unit["max_hp"],unit["hp"]+ls_heal)
        return {"name":t["name"],"damage":r["damage"],"crit":cr,"killed":killed,"immune":r.get("immune",False),
                "max_hp":t["max_hp"],"hp_pct":max(0,t["hp"]/max(1,t["max_hp"])),
                "lifesteal_heal":ls_heal,
                "idx":next((i for i,e in enumerate(enemies) if e.get("name")==t["name"]),0)}

    # 收集总伤害结果
    all_targets_data=[]
    total_dmg=0
    
    if is_aoe and multi_hit_count > 1:
        # AOE多段: 真正循环multi_hit_count次, 每次攻击全体存活敌人
        remaining_hits = multi_hit_count
        while remaining_hits > 0:
            alive_tars = [t for t in tars if t.get("alive", True)]
            if not alive_tars: break
            round_data = []
            for t in alive_tars:
                td = _process_dmg_target(t, True)
                if td:
                    round_data.append(td)
                    total_dmg += td["damage"]
            all_targets_data.append(round_data)
            remaining_hits -= 1
        # 保存真实轮次数据, 让expand直接用它拆
        _round_data = all_targets_data
        # 重置targets为flat列表（兼容旧代码）
        flat = []
        for rd in all_targets_data:
            flat.extend(rd)
        all_targets_data = flat
    elif is_aoe:
        # 单发AOE
        for t in tars:
            if not t.get("alive",True): continue
            td=_process_dmg_target(t,True)
            if td: all_targets_data.append(td); total_dmg+=td["damage"]
    else:
        # ST: 打multi_hit次（可能多个目标不同）
        remaining_hits=multi_hit_count
        while remaining_hits>0:
            alive_targets=[e for e in enemies if e.get("alive",True)]
            if not alive_targets: break
            # 赵云Lv5: 优先后排
            if up and up.get("prefer_back_row"):
                t=min(alive_targets,key=lambda x:x["hp"])
            elif target_mode=="lowest_hp":
                t=min(alive_targets,key=lambda x:x["hp"])
            else:
                t=alive_targets[0] if target_mode!="single" else (tars[0] if tars else alive_targets[0])
            td=_process_dmg_target(t,False)
            if td: all_targets_data.append(td); total_dmg+=td["damage"]
            remaining_hits-=1

    # 技能升级额外效果
    if up and up.get("extra_damage"):
        ba=cstat(unit,"atk",unit["atk"])
        for e in enemies:
            if e.get("alive",True):
                extra=max(1, int(ba * up["extra_damage"] * random.uniform(0.9, 1.1)))
                apply_dmg(e,extra)
                if e["hp"]<=0: e["alive"]=False

    # 应用debuff
    for deb in hd.get("skill_debuffs", []):
        for t in tars:
            if t.get("alive",True) and random.random()<deb.get("chance",1.0):
                t["debuffs"].append({"stat":deb["stat"],"pct":deb["pct"],"dur":deb["dur"]})

    # 特殊效果(通用处理)
    for sp in specials:
        for t in tars:
            if not t.get("alive",True): continue
            if sp=="stun" and random.random()<0.2: t["stunned"]=True
            if sp=="freeze" and random.random()<0.4: t["frozen"]=True
            if sp=="burn": t["debuffs"].append({"stat":"burn","pct":0.15,"dur":2})
            if sp=="taunt": t["debuffs"].append({"stat":"taunted","pct":1.0,"dur":3})
            # 眩晕升级(吕布Lv3)
            if up and up.get("stun_all") and sp=="taunt": t["stunned"]=True

    # 统计吸血总治疗量
    total_ls_heal = sum(td.get("lifesteal_heal", 0) for td in all_targets_data)
    
    if is_aoe:
        rv = {"side":"ally","type":"skill","attacker_name":unit["name"],"skill":sk_name,
                "aoe":True,"targets":all_targets_data,"multi_hit_count":multi_hit_count}
        if '_round_data' in dir() and _round_data:
            rv["round_data"] = _round_data
        if total_ls_heal > 0:
            rv["lifesteal_heal"] = total_ls_heal
        return rv
    else:
        # 单目标多段: 取最后一个目标显示
        last=all_targets_data[-1] if all_targets_data else {"name":"","damage":0,"crit":False,"killed":False}
        rv = {"side":"ally","type":"skill","attacker_name":unit["name"],"skill":sk_name,
                "aoe":False,"target_name":last["name"],"damage":last["damage"],"crit":last["crit"],"killed":last["killed"],"multi_hit_count":multi_hit_count,"all_hits":all_targets_data}
        if total_ls_heal > 0:
            rv["lifesteal_heal"] = total_ls_heal
        return rv

# ═══ 多段攻击拆分 ═══
def expand_multi_hit_action(act, multi_hit_count):
    """将多段技能的一个action拆成多个独立动作，前端依次播放"""
    if multi_hit_count <= 1 or act.get("type") != "skill":
        return [act]
    is_aoe = act.get("aoe", False)
    all_hits = act.get("all_hits") or act.get("targets", [])
    if not all_hits:
        return [act]
    new_acts = []
    # 如果有round_data（AOE多段真实循环）, 直接拆分
    if is_aoe and act.get("round_data"):
        for hi, rd in enumerate(act["round_data"]):
            new_act = dict(act)
            new_act["targets"] = rd
            new_act["hit_no"] = hi + 1
            new_act["total_hits"] = len(act["round_data"])
            new_acts.append(new_act)
        return new_acts
    if is_aoe and len(all_hits) > 0:
        # AOE多段: 每次对全体敌人造成完全相同伤害, HP逐击递减
        enemy_data = {}
        for t in all_hits:
            name = t.get("name","")
            max_hp = t.get("max_hp", 0)
            dmg = max(1, t.get("damage", 0))  # 已经是单次命中伤害, 不除
            enemy_data[name] = {"max_hp": max_hp, "hit_dmg": dmg, "idx": t.get("idx", 0)}

        for hi in range(multi_hit_count):
            hit_targets = []
            for name, info in enemy_data.items():
                hp_before = max(0, info["max_hp"] - hi * info["hit_dmg"])
                dmg_this_hit = min(info["hit_dmg"], hp_before)
                hp_after = max(0, hp_before - dmg_this_hit)
                hp_pct = hp_after / max(1, info["max_hp"])
                killed = hp_after <= 0
                hit_targets.append({
                    "name": name,
                    "damage": dmg_this_hit,
                    "crit": False,
                    "killed": killed,
                    "idx": info["idx"],
                    "max_hp": info["max_hp"],
                    "hp_pct": hp_pct
                })
            new_act = dict(act)
            new_act["targets"] = hit_targets
            new_act["hit_no"] = hi + 1
            new_act["total_hits"] = multi_hit_count
            new_acts.append(new_act)
    else:
        # ST多段: 按顺序分组
        per_hit_size = len(all_hits) // multi_hit_count
        if per_hit_size == 0:
            return [act]
        for hi in range(multi_hit_count):
            start = hi * per_hit_size
            end = start + per_hit_size if hi < multi_hit_count - 1 else len(all_hits)
            hit_targets = []
            for j in range(start, end):
                if j < len(all_hits):
                    t = all_hits[j]
                    hit_targets.append({
                        "name": t.get("name",""),
                        "damage": t.get("damage", 0),
                        "crit": t.get("crit", False),
                        "killed": t.get("killed", False),
                        "idx": t.get("idx", 0),
                        "max_hp": t.get("max_hp", 0),
                        "hp_pct": t.get("hp_pct", 0)
                    })
            new_act = dict(act)
            if hit_targets:
                new_act["target_name"] = hit_targets[0]["name"]
                new_act["damage"] = hit_targets[0]["damage"]
                new_act["crit"] = hit_targets[0]["crit"]
                new_act["killed"] = hit_targets[0]["killed"]
            new_act["hit_no"] = hi + 1
            new_act["total_hits"] = multi_hit_count
            new_acts.append(new_act)
    return new_acts

def enemy_basic_attack(unit, allies, enemies):
    alive_h = [a for a in allies if a.get("alive",True)]
    if not alive_h: return {"side":"enemy","type":"basic","attacker_name":unit["name"],"damage":0}
    t = random.choice(alive_h)
    ba = cstat(unit, "atk", unit["atk"])
    dmg_pct = unit.get("basic_dmg_pct", 0.6)
    d = int(ba * dmg_pct * random.uniform(0.8, 1.0))
    cr = random.random() < unit["crit"]/100
    if cr: d = int(d * get_effective_crit_dmg(unit))
    dr = get_dmg_reduce(t)
    d = int(d * (1 - dr))
    r = apply_dmg(t, d)
    killed = t["hp"] <= 0
    if killed: t["alive"] = False
    return {"side":"enemy","type":"basic","attacker_name":unit["name"],"skill":unit.get("skill_name","攻击"),
            "aoe":False,"target_name":t["name"],"damage":r["damage"],"crit":cr,"killed":killed}

def enemy_use_skill(unit, allies, enemies):
    alive_h = [a for a in allies if a.get("alive", True)]
    if not alive_h: return {"side":"enemy","type":"skill","attacker_name":unit["name"],"damage":0}
    
    prof = unit.get("profession", "")
    pd = ENEMY_PROFESSIONS.get(prof, {})
    
    # 巫医: 治疗
    heal_pct = unit.get("skill_heal_pct", 0)
    if heal_pct > 0:
        alive_e = [e for e in enemies if e.get("alive", True)]
        if alive_e:
            target = min(alive_e, key=lambda x: x["hp"] / max(1, x["max_hp"]))
            heal = int(target["max_hp"] * heal_pct)
            target["hp"] = min(target["max_hp"], target["hp"] + heal)
            return {"side":"enemy","type":"skill","attacker_name":unit["name"],
                    "skill":unit.get("skill_name","治愈"),"aoe":False,"target_name":target["name"],"heal":heal}
    
    # BOSS技能（每2次行动放一次，简化：每次技能都有机会触发）
    if unit.get("boss_skills"):
        bs_list = unit["boss_skills"]
        # 随机选一个BOSS主动技(简化:用第一个active的)
        for bs in [s for s in BOSS_SKILLS if s["name"] in [x["name"] for x in bs_list]]:
            if bs.get("type") == "active" and random.random() < 0.4:
                return _apply_boss_skill(unit, bs, allies, enemies)
    
    # 职业技能
    is_aoe = unit.get("skill_aoe", False)
    skill_target = unit.get("skill_target", "single")
    dmg_pct = unit.get("skill_dmg_pct", 1.0)
    
    # 选择目标
    if skill_target in ("front_row",):
        tars = get_targets(alive_h, "front_row")
    elif skill_target in ("lowest_hp",):
        tars = get_targets(alive_h, "lowest_hp")
    elif skill_target in ("back_row",):
        tars = get_targets(alive_h, "back_row")
    elif skill_target in ("all_enemy",):
        tars = alive_h
    elif skill_target in ("self",):
        # 自身buff技能(铁卫)
        for b in unit.get("skill_buffs", []):
            unit["buffs"].append({"stat":b["stat"],"pct":b["pct"],"dur":b["dur"]})
        if "taunt" in unit.get("skill_special", []):
            unit["debuffs"].append({"stat":"taunted","pct":1.0,"dur":2})
        return {"side":"enemy","type":"skill","attacker_name":unit["name"],
                "skill":unit.get("skill_name","铁壁"),"buff_effects":[b["stat"] for b in unit.get("skill_buffs",[])]}
    else:
        tars = [random.choice(alive_h)]
    
    if not tars: return {"side":"enemy","type":"skill","attacker_name":unit["name"],"damage":0}
    
    total_dmg = 0
    targets_data = []
    for t in tars:
        if not t.get("alive", True): continue
        ba = cstat(unit, "atk", unit["atk"])
        d = int(ba * dmg_pct * random.uniform(0.8, 1.0))
        cr = random.random() < unit["crit"] / 100
        if cr: d = int(d * get_effective_crit_dmg(unit))
        
        # 弓手: execute斩杀
        if "execute" in unit.get("skill_special", []) and t["hp"] / max(1, t["max_hp"]) < 0.5:
            d = t["hp"]
        
        dr = get_dmg_reduce(t)
        d = int(d * (1 - dr))
        r = apply_dmg(t, d)
        total_dmg += r["damage"]
        killed = t["hp"] <= 0
        if killed: t["alive"] = False
        targets_data.append({"name":t["name"],"damage":r["damage"],"crit":cr,"killed":killed,"max_hp":t["max_hp"],"hp_pct":max(0,t["hp"]/max(1,t["max_hp"]))})
        
        # 技能debuff
        for deb in unit.get("skill_debuffs", []):
            if random.random() < deb.get("chance", 1.0):
                t["debuffs"].append({"stat":deb["stat"],"pct":deb["pct"],"dur":deb["dur"]})
    
    # 技能special(burn)
    for sp in unit.get("skill_special", []):
        if sp == "burn":
            for t in tars:
                if t.get("alive", True):
                    t["debuffs"].append({"stat":"burn","pct":0.1,"dur":2})
    
    first = tars[0] if tars else alive_h[0]
    if is_aoe:
        return {"side":"enemy","type":"skill","attacker_name":unit["name"],
                "skill":unit.get("skill_name","技能"),"aoe":True,"targets":targets_data}
    else:
        return {"side":"enemy","type":"skill","attacker_name":unit["name"],
                "skill":unit.get("skill_name","技能"),"aoe":False,
                "target_name":first["name"],"damage":total_dmg,"crit":False,"killed":False}

def _apply_boss_skill(unit, bs, allies, enemies):
    """应用BOSS主动技能"""
    is_aoe = bs.get("aoe", True)
    dmg_pct = bs.get("dmg_pct", 2.0)
    alive_h = [a for a in allies if a.get("alive", True)]
    
    # 治疗技能
    if bs.get("heal_pct"):
        alive_e = [e for e in enemies if e.get("alive", True)]
        for t in alive_e:
            heal = int(t["max_hp"] * bs["heal_pct"])
            t["hp"] = min(t["max_hp"], t["hp"] + heal)
        return {"side":"enemy","type":"skill","attacker_name":unit["name"],
                "skill":bs["name"],"aoe":True,
                "targets":[{"name":t["name"],"heal":int(t["max_hp"]*bs["heal_pct"])} for t in alive_e]}
    
    # 护盾技能
    if bs.get("buff") == "shield_team":
        shield_pct = bs.get("shield_pct", 0.3)
        alive_e = [e for e in enemies if e.get("alive", True)]
        for t in alive_e:
            t["shield"] = int(t["max_hp"] * shield_pct)
        for tb in bs.get("team_buffs", []):
            for t in alive_e:
                t["buffs"].append({"stat":tb["stat"],"pct":tb["pct"],"dur":tb["dur"]})
        return {"side":"enemy","type":"skill","attacker_name":unit["name"],
                "skill":bs["name"],"aoe":True,"msg":"全体护盾!"}
    
    # 伤害技能
    if bs.get("target") == "lowest_hp":
        tars = get_targets(alive_h, "lowest_hp") if alive_h else []
    elif is_aoe:
        tars = alive_h
    else:
        tars = [random.choice(alive_h)] if alive_h else []
    
    if not tars: return {"side":"enemy","type":"skill","attacker_name":unit["name"],"skill":bs["name"],"damage":0}
    
    total_dmg = 0
    targets_data = []
    for t in tars:
        if not t.get("alive", True): continue
        ba = cstat(unit, "atk", unit["atk"])
        d = int(ba * dmg_pct * random.uniform(0.9, 1.0))
        cr = random.random() < unit["crit"] / 100
        if cr: d = int(d * get_effective_crit_dmg(unit))
        dr = get_dmg_reduce(t)
        d = int(d * (1 - dr))
        r = apply_dmg(t, d)
        total_dmg += r["damage"]
        killed = t["hp"] <= 0
        if killed: t["alive"] = False
        targets_data.append({"name":t["name"],"damage":r["damage"],"crit":cr,"killed":killed})
    
    # BOSS技能debuff
    for deb in bs.get("debuffs", []):
        for t in tars:
            if t.get("alive", True) and random.random() < deb.get("chance", 1.0):
                if deb["stat"] == "stun":
                    t["stunned"] = True
                else:
                    t["debuffs"].append({"stat":deb["stat"],"pct":deb["pct"],"dur":deb["dur"]})
    
    if is_aoe:
        return {"side":"enemy","type":"skill","attacker_name":unit["name"],
                "skill":bs["name"],"aoe":True,"targets":targets_data}
    else:
        first = tars[0] if tars else alive_h[0]
        return {"side":"enemy","type":"skill","attacker_name":unit["name"],
                "skill":bs["name"],"aoe":False,"target_name":first["name"],"damage":total_dmg}

def _removed_card_effect(): return {}
# ═══ 战斗API ═══
@app.route("/api/speed_battle", methods=["POST"])
@login_required
def api_speed_battle():
    g = load_game()
    if not g: return jsonify({"error":"未找到存档"})
    if not g["lineup"]: return jsonify({"error":"请先上阵英雄"})
    stage = gen_stage(g["stage_index"])

    my_heroes = []
    for hid in g["lineup"]:
        inv = next((i for i in g["inventory"] if i["hero_id"] == hid), None)
        if not inv: continue
        f = h2f(hid, inv)
        if f: my_heroes.append(f)

    enemies = gen_enemy_formation(stage["power"] / 6.0, boss=stage["boss"])

    result = run_speed_battle(my_heroes, enemies, stage)

    g["ticks"] += 1
    if result["win"]:
        jr = result.get("jade_reward", 3)
        g["jade"] += jr
        g["stage_index"] += 1
        if result.get("equip_reward"):
            equip_bag = g.get("equip_bag", [])
            ex = next((e for e in equip_bag if isinstance(e, dict) and e.get("id") == result["equip_reward"]), None)
            if ex: ex["count"] += 1
            else: equip_bag.append({"id": result["equip_reward"], "count": 1})
    else:
        g["jade"] += result.get("jade_reward", 2)
    save_game(g)

    r = to_client(g)
    r["battle_result"] = result
    return jsonify(r)

# ═══ 旧战斗API（兼容） ═══
@app.route("/api/battle")
@login_required
def api_battle():
    return api_speed_battle()

# ═══ 原有API ═══
@app.route("/")
@login_required
def index(): return render_template("wuxia.html")

@app.route("/api/load")
@login_required
def api_load():
    g=load_game()
    if not g: g=new_game_inner(); save_game(g)
    now=datetime.now().timestamp()
    secs=now-g.get("last_active_time",now)
    if secs>60:
        power=calc_pow(HERO_DATA,g["lineup"],g["inventory"],g["equip_bag"])
        jpm=max(0.3,power/5000); mins=min(secs/60,480); oj=int(mins*jpm)
        if oj>0: g["jade"]+=oj; g["offline_msg"]=f"⏰ 离线{int(mins)}分钟，获得💎{oj}玉璧" if mins<120 else f"⏰ 离线{int(mins/60)}小时，获得💎{oj}玉璧"
    g["last_active_time"]=now; save_game(g)
    r=to_client(g)
    if g.get("offline_msg"): g["offline_msg"]=""
    return jsonify(r)

@app.route("/api/tick")
@login_required
def api_tick():
    g=load_game()
    if not g: return api_new()
    g["last_active_time"]=datetime.now().timestamp()
    power=calc_pow(HERO_DATA,g["lineup"],g["inventory"],g["equip_bag"])
    jpm=max(0.3,power/5000); tj=max(1,int(jpm*0.5))
    g["jade"]+=tj; save_game(g)
    return jsonify({"jade":g["jade"],"tick_gain":tj,"jade_per_min":round(jpm,1)})

@app.route("/api/sweep")
@login_required
def api_sweep():
    g=load_game()
    if not g: return api_new()
    if not g["lineup"]: return jsonify({"error":"请先上阵英雄",**to_client(g)})
    tj=0; te=[]
    for _ in range(3):
        stage=gen_stage(g["stage_index"])
        my_heroes = []
        for hid in g["lineup"]:
            inv=next((i for i in g["inventory"] if i["hero_id"]==hid),None)
            if not inv: continue
            f=h2f(hid,inv)
            if f: my_heroes.append(f)
        enemies=gen_enemy_formation(stage["power"]/6.0, boss=stage["boss"])
        result=run_speed_battle(my_heroes, enemies, stage)
        g["ticks"]+=1; tj+=result.get("jade_reward",0)
        if result.get("equip_reward"): te.append(result["equip_reward"])
        if result.get("win",False):
            g["stage_index"] += 1
        else:
            break
    save_game(g)
    r=to_client(g); r["sweep_result"]={"total_jade":tj,"total_equips":te,"count":len(te)}
    return jsonify(r)

@app.route("/api/new")
@login_required
def api_new():
    g=new_game_inner(); save_game(g); return jsonify(to_client(g))

@app.route("/api/pull")
@login_required
def api_pull():
    g=load_game()
    if not g: return api_new()
    if g["jade"]<3: return jsonify({"error":"玉璧不足",**to_client(g)})
    g["jade"]-=3; g["pull_count"]+=1
    card=pull_h(g["pity_counter"])
    if QUALITY_ORDER.get(card["quality"],0)>=2: g["pity_counter"]=0
    else: g["pity_counter"]+=1
    ex=next((i for i in g["inventory"] if i["hero_id"]==card["hero_id"]),None)
    if ex:
        gained = DUPE_EXP.get(card["quality"], 30)
        g["dupe_exp_total"] = g.get("dupe_exp_total", 0) + gained
        ex["skill_lv"] = min(9, ex["skill_lv"] + 1)
        g["msg"]=f"🎴 抽到{card['quality']}{HERO_DATA[card['hero_id']]['name']}！技能Lv{ex['skill_lv']}+{gained}💠"
    else: g["inventory"].append(card); g["msg"]=f"🎴 抽到{card['quality']}{HERO_DATA[card['hero_id']]['name']}！"
    save_game(g)
    r=to_client(g); r["pull"]={"hero_id":card["hero_id"],"quality":card["quality"],"hero_name":HERO_DATA[card["hero_id"]]["name"],"hero_class":HERO_DATA[card["hero_id"]]["class"]}
    return jsonify(r)

@app.route("/api/pull10")
@login_required
def api_pull10():
    g=load_game()
    if not g: return api_new()
    if g["jade"]<25: return jsonify({"error":"玉璧不足",**to_client(g)})
    g["jade"]-=25; g["pull_count"]+=10; cards=[]; bq="凡品"
    for _ in range(10):
        card=pull_h(g["pity_counter"])
        if QUALITY_ORDER.get(card["quality"],0)>=2: g["pity_counter"]=0
        else: g["pity_counter"]+=1
        if QUALITY_ORDER.get(card["quality"],0)>QUALITY_ORDER.get(bq,0): bq=card["quality"]
        ex=next((i for i in g["inventory"] if i["hero_id"]==card["hero_id"]),None)
        if ex:
            gained = DUPE_EXP.get(card["quality"], 30)
            g["dupe_exp_total"] = g.get("dupe_exp_total", 0) + gained
            ex["skill_lv"] = min(9, ex["skill_lv"] + 1)
        else: g["inventory"].append(card)
        cards.append(card)
    g["msg"]=f"🎴 十连最高{bq}"; save_game(g)
    r=to_client(g); r["pull"]=[{"hero_id":c["hero_id"],"quality":c["quality"],"hero_name":HERO_DATA[c["hero_id"]]["name"],"hero_class":HERO_DATA[c["hero_id"]]["class"]} for c in cards]; r["is_10_pull"]=True
    return jsonify(r)

@app.route("/api/pull100")
@login_required
def api_pull100():
    g=load_game()
    if not g: return api_new()
    if g["jade"]<235: return jsonify({"error":"玉璧不足",**to_client(g)})
    g["jade"]-=235; g["pull_count"]+=100
    pulls=[]; qcounts={q:0 for q in ["凡品","良品","极品","绝品","传说","神卡"]}
    bq="凡品"
    for _ in range(100):
        card=pull_h(g["pity_counter"])
        if QUALITY_ORDER.get(card["quality"],0)>=2: g["pity_counter"]=0
        else: g["pity_counter"]+=1
        qcounts[card["quality"]]=qcounts.get(card["quality"],0)+1
        if QUALITY_ORDER.get(card["quality"],0)>QUALITY_ORDER.get(bq,0): bq=card["quality"]
        ex=next((i for i in g["inventory"] if i["hero_id"]==card["hero_id"]),None)
        if ex:
            gained = DUPE_EXP.get(card["quality"], 30)
            g["dupe_exp_total"] = g.get("dupe_exp_total", 0) + gained
            ex["skill_lv"] = min(9, ex["skill_lv"] + 1)
        else: g["inventory"].append(card)
        pulls.append(card)
    g["msg"]=f"🎴 百抽最高{bq}! {qcounts['传说']}传说 {qcounts['绝品']}绝品 {qcounts['极品']}极品 {qcounts['神卡']}神卡"
    save_game(g)
    highlights=[c for c in pulls if QUALITY_ORDER.get(c["quality"],0)>=2]
    r=to_client(g); r["pull"]={"qcounts":qcounts,"highlights":[{"hero_id":c["hero_id"],"quality":c["quality"],"hero_name":HERO_DATA[c["hero_id"]]["name"],"hero_class":HERO_DATA[c["hero_id"]]["class"]} for c in highlights],"count":100}
    return jsonify(r)

# ═══ 英雄等级升级(消耗经验池) ═══
@app.route("/api/level_up/<hero_id>", methods=["POST"])
@login_required
def api_level_up(hero_id):
    g=load_game()
    if not g: return api_new()
    inv=next((i for i in g["inventory"] if i["hero_id"]==hero_id),None)
    if not inv: return jsonify({"error":"未找到该英雄"})
    lv=inv.get("level",1)
    if lv>=500: return jsonify({"error":"已满级"})
    cost=lv * 80  # Lv1→2=80, Lv2→3=160, Lv10→11=800
    pool=g.get("dupe_exp_total",0)
    if pool<cost: return jsonify({"error":f"💠不足，需要{cost}💠(当前{pool})"})
    g["dupe_exp_total"]=pool-cost
    inv["level"]=lv+1
    save_game(g)
    return jsonify({"ok":True,"hero_id":hero_id,"level":inv["level"],
        "dupe_exp_total":g["dupe_exp_total"],"cost":cost,
        "msg":f"⬆ {HERO_DATA.get(hero_id,{}).get('name','?')}升至Lv.{inv['level']}！",**to_client(g)})

@app.route("/api/lineup/<hero_id>")
@login_required
def api_lineup_toggle(hero_id):
    g=load_game()
    if not g: return api_new()
    if hero_id in g["lineup"]:
        g["lineup"].remove(hero_id)
    elif len(g["lineup"])>=6:
        return jsonify({"error":"阵容最多6人",**to_client(g)})
    else:
        g["lineup"].append(hero_id)
        inv=next((i for i in g["inventory"] if i["hero_id"]==hero_id),None)
        if inv: inv["active"]=True
    save_game(g); return jsonify(to_client(g))

@app.route("/api/lineup/reorder", methods=["POST"])
@login_required
def api_lineup_reorder():
    g=load_game()
    if not g: return api_new()
    data=request.get_json()
    new_lineup=data.get("lineup",[])
    if not isinstance(new_lineup,list) or len(new_lineup)>6:
        return jsonify({"error":"无效的阵容"})
    g["lineup"]=new_lineup
    save_game(g)
    return jsonify({"ok":True,**to_client(g)})

@app.route("/api/equip/<hero_id>/<slot>/<eq_id>")
@login_required
def api_equip(hero_id,slot,eq_id):
    g=load_game()
    if not g: return api_new()
    inv=next((i for i in g["inventory"] if i["hero_id"]==hero_id),None)
    if not inv: return jsonify(to_client(g))
    if eq_id=="none": inv["equipped"][slot]=None
    elif eq_id in EQUIP_DATA and EQUIP_DATA[eq_id]["type"]==slot: inv["equipped"][slot]=eq_id
    save_game(g); return jsonify(to_client(g))

@app.route("/api/hero_detail/<hero_id>")
@login_required
def api_hero_detail(hero_id):
    g=load_game()
    if not g: return api_new()
    inv=next((i for i in g["inventory"] if i["hero_id"]==hero_id),None)
    if not inv: return jsonify({"error":"未找到"})
    hd=HERO_DATA.get(hero_id)
    if not hd: return jsonify({"error":"不存在"})
    eq={}
    for s,eid in inv["equipped"].items():
        if eid and eid in EQUIP_DATA:
            e=EQUIP_DATA[eid]; eq[s]={"name":e["name"],"quality":e["quality"],"color":e["color"],"atk":e.get("atk",0),"hp":e.get("hp",0),"crit":e.get("crit",0),"special":e.get("special",""),"exclusive":e["exclusive"]==hero_id}
    bonds=get_bonds(g["lineup"])
    lv=inv.get("level",1)
    bonus=hero_level_bonus(lv)
    eff_hp=int(hd["hp"]*bonus); eff_atk=int(hd["atk"]*bonus)
    eff_crit=calc_level_crit(hd, lv)
    equip_bag=g.get("equip_bag",[])
    for s,eid in inv["equipped"].items():
        if eid and eid in EQUIP_DATA:
            e=EQUIP_DATA[eid]
            ulv=0
            if equip_bag:
                eb=next((x for x in equip_bag if isinstance(x,dict) and x.get("id")==eid),None)
                if eb: ulv=eb.get("upgrade_lv",0)
            mult=1.0+ulv*0.25
            if ulv>=10: mult+=0.5
            elif ulv>=5: mult+=0.25
            eff_hp+=int(e.get("hp",0)*mult); eff_atk+=int(e.get("atk",0)*mult)
            eff_crit+=e.get("crit",0)
    dupe_exp_total=g.get("dupe_exp_total",0)
    level_up_cost=inv.get("level",1) * 80
    next_skill_cost=SKILL_UPGRADE_COST.get(inv.get("skill_lv",1)+1, None)
    effective = get_eff_stats(hd, inv, g.get("equip_bag",[]))
    return jsonify({"hero_id":hero_id,"name":hd["name"],"class":hd["class"],"quality":hd["quality"],"color":hd["color"],
        "hp":effective["hp"],"atk":effective["atk"],"crit":effective["crit"],
        "base_hp":hd["hp"],"base_atk":hd["atk"],"base_crit":hd.get("crit",0),
        "crit_dmg":effective["crit_dmg"],"dmg_reduce":effective["dmg_reduce"],
        "spd":hd.get("spd",100),"skill_cost":hd.get("skill_cost",100),
        "skill_name":hd["skill_name"],"skill_desc":hd["skill_desc"],
        "skill_aoe":hd.get("skill_aoe",False),"skill_lv":inv["skill_lv"],
        "level":inv.get("level",1),"exp":inv.get("exp",0),"exp_next":inv.get("level",1)*100,
        "skill_upgrades":hd["skill_upgrades"],"passive_name":hd["passive_name"],"passive_desc":hd["passive_desc"],
        "passive_upgrades":hd["passive_upgrades"],"power":calc_hp(hero_id,inv["skill_lv"],inv["equipped"],inv,g.get("equip_bag",[])),
        "basic_name":hd.get("basic_name","攻击"),"basic_desc":hd.get("basic_desc","普通攻击"),
        "basic_energy_gain":hd.get("basic_energy_gain",25),
        "dupe_exp_total":dupe_exp_total,"level_up_cost":level_up_cost,"next_skill_cost":next_skill_cost,
        "equipped":eq,"bonds":bonds,"in_lineup":hero_id in g["lineup"]})

@app.route("/api/inventory_heroes")
@login_required
def api_inventory_heroes():
    g=load_game()
    if not g: return jsonify([])
    equip_bag = g.get("equip_bag", [])
    hh=[]
    for inv in g["inventory"]:
        hd=HERO_DATA.get(inv["hero_id"])
        if hd:
            eff = get_eff_stats(hd, inv, equip_bag)
            hh.append({"hero_id":inv["hero_id"],"name":hd["name"],"class":hd["class"],"quality":hd["quality"],
            "color":hd["color"],"skill_lv":inv["skill_lv"],"level":inv.get("level",1),
            "power":calc_hp(inv["hero_id"],inv["skill_lv"],inv["equipped"],inv,equip_bag),
            "atk":eff["atk"],"hp":eff["hp"],"crit":eff["crit"],
            "crit_dmg":eff["crit_dmg"],"dmg_reduce":eff["dmg_reduce"],
            "equipped":inv["equipped"],"in_lineup":inv["hero_id"] in g["lineup"]})
    hh.sort(key=lambda h:(QUALITY_ORDER.get(h["quality"],0),h["power"]),reverse=True)
    return jsonify(hh)

@app.route("/api/equip_bag")
@login_required
def api_equip_bag():
    g=load_game()
    items=[]
    for e in g["equip_bag"]:
        eid=e["id"] if isinstance(e,dict) else e; cnt=e["count"] if isinstance(e,dict) else 1
        if eid in EQUIP_DATA:
            ed=EQUIP_DATA[eid]; items.append({"id":eid,"name":ed["name"],"type":ed["type"],"quality":ed["quality"],
                "color":ed["color"],"atk":ed.get("atk",0),"hp":ed.get("hp",0),"crit":ed.get("crit",0),"special":ed.get("special",""),"count":cnt,
                "upgrade_lv":e.get("upgrade_lv",0) if isinstance(e,dict) else 0,
                "exclusive":ed.get("exclusive",None),
                "lv20":ed.get("lv20",""),"lv40":ed.get("lv40",""),"lv60":ed.get("lv60",""),"lv80":ed.get("lv80",""),"lv100":ed.get("lv100","")})
    items.sort(key=lambda x:(EQ_QUALITY_ORDER.get(x["quality"],0),x["atk"]),reverse=True)
    return jsonify(items)

# ═══ 图鉴 API ═══
@app.route("/api/tuji")
@login_required
def api_tuji():
    heroes=[]
    for hid,hd in HERO_DATA.items():
        heroes.append({"id":hid,"name":hd["name"],"class":hd["class"],"quality":hd["quality"],"color":hd["color"],
            "hp":hd["hp"],"atk":hd["atk"],"crit":hd["crit"],"spd":hd.get("spd",100),
            "skill_name":hd["skill_name"],"skill_desc":hd["skill_desc"],"skill_aoe":hd.get("skill_aoe",False),
            "skill_cost":hd.get("skill_cost",100),
            "skill_upgrades":hd["skill_upgrades"],
            "basic_name":hd.get("basic_name",""),"basic_desc":hd.get("basic_desc",""),
            "basic_energy_gain":hd.get("basic_energy_gain",25),
            "passive_name":hd["passive_name"],"passive_desc":hd["passive_desc"],
            "passive_upgrades":hd["passive_upgrades"]})
    equips=[]
    for eid,ed in EQUIP_DATA.items():
        excl_name = ""
        if ed.get("exclusive") and ed["exclusive"] in HERO_DATA:
            excl_name = HERO_DATA[ed["exclusive"]]["name"]
        equips.append({"id":eid,"name":ed["name"],"type":ed["type"],"quality":ed["quality"],"color":ed["color"],
            "atk":ed.get("atk",0),"hp":ed.get("hp",0),"crit":ed.get("crit",0),
            "desc":ed.get("desc",""),"special":ed.get("special",""),"exclusive":ed["exclusive"],
            "exclusive_name":excl_name,
            "lv20":ed.get("lv20",""),"lv40":ed.get("lv40",""),"lv60":ed.get("lv60",""),
            "lv80":ed.get("lv80",""),"lv100":ed.get("lv100","")})
    bonds=[{"id":b["id"],"name":b["name"],"members":[HERO_DATA.get(m,{}).get("name",m) for m in b["members"]],
        "desc":b["desc"],"effect":b["effect"]} for b in BONDS]
    return jsonify({"heroes":heroes,"equips":equips,"bonds":bonds})

def to_client(g):
    power=calc_pow(HERO_DATA,g["lineup"],g["inventory"],g["equip_bag"])
    sp=2000+g["stage_index"]*500; sp=max(50,min(sp,999999))
    sn=g.get("stage_name") or gen_stage_name(g["stage_index"])
    stage_info = gen_stage(g["stage_index"])
    pi={"count":g["pity_counter"],"next_guaranteed":10-g["pity_counter"]}
    bl=[{"name":b["name"],"desc":b["desc"]} for b in get_bonds(g["lineup"])]
    return {"jade":g["jade"],"pull_count":g["pull_count"],"pity":pi,"power":power,
        "stage":{"name":sn,"power":sp,"index":g["stage_index"],"boss":stage_info["boss"]},
        "lineup":g["lineup"],"lineup_count":len(g["lineup"]),
        "bonds":bl,"msg":g.get("msg",""),"ticks":g["ticks"],
        "inventory_count":len(g["inventory"]),"username":session.get("username",""),
        "offline_msg":g.get("offline_msg",""),"jade_per_min":round(max(0.3,power/5000),1),
        "dupe_exp_total":g.get("dupe_exp_total",0),
        "equip_exp_total":g.get("equip_exp_total",0),
        "equip_pity":g.get("equip_pity_counter",0)}

# ═══ 铸魂池API ═══
@app.route("/api/equip_pull")
@login_required
def api_equip_pull():
    g=load_game()
    if not g: return api_new()
    if g["jade"]<5: return jsonify({"error":"玉璧不足",**to_client(g)})
    g["jade"]-=5
    card=pull_equip(g.get("equip_pity_counter",0))
    if EQ_QUALITY_ORDER.get(card["quality"],0)>=2: g["equip_pity_counter"]=0
    else: g["equip_pity_counter"]=g.get("equip_pity_counter",0)+1
    ed=EQUIP_DATA.get(card["id"],{})
    ex=next((e for e in g["equip_bag"] if isinstance(e,dict) and e.get("id")==card["id"]),None)
    if ex:
        gained=EQUIP_DUPE_EXP.get(card["quality"],10)
        g["equip_exp_total"]=g.get("equip_exp_total",0)+gained
        ex["count"]+=1
        g["msg"]=f"🔮 抽到{card['quality']}{ed.get('name','?')}！+{gained}💫"
    else:
        g["equip_bag"].append({"id":card["id"],"count":1,"upgrade_lv":0})
        g["msg"]=f"🔮 抽到{card['quality']}{ed.get('name','?')}！"
    save_game(g)
    r=to_client(g)
    r["equip_pull"]={"id":card["id"],"quality":card["quality"],"name":ed.get("name","?"),"type":ed.get("type","?"),"color":ed.get("color","#888")}
    return jsonify(r)

@app.route("/api/equip_pull10")
@login_required
def api_equip_pull10():
    g=load_game()
    if not g: return api_new()
    if g["jade"]<45: return jsonify({"error":"玉璧不足",**to_client(g)})
    g["jade"]-=45; cards=[]; bq="凡品"
    for _ in range(10):
        card=pull_equip(g.get("equip_pity_counter",0))
        if EQ_QUALITY_ORDER.get(card["quality"],0)>=2: g["equip_pity_counter"]=0
        else: g["equip_pity_counter"]=g.get("equip_pity_counter",0)+1
        if EQ_QUALITY_ORDER.get(card["quality"],0)>EQ_QUALITY_ORDER.get(bq,0): bq=card["quality"]
        ed=EQUIP_DATA.get(card["id"],{})
        ex=next((e for e in g["equip_bag"] if isinstance(e,dict) and e.get("id")==card["id"]),None)
        if ex:
            gained=EQUIP_DUPE_EXP.get(card["quality"],10)
            g["equip_exp_total"]=g.get("equip_exp_total",0)+gained
            ex["count"]+=1
        else:
            g["equip_bag"].append({"id":card["id"],"count":1,"upgrade_lv":0})
        cards.append({"id":card["id"],"quality":card["quality"],"name":ed.get("name","?"),"type":ed.get("type","?"),"color":ed.get("color","#888")})
    g["msg"]=f"🔮 铸魂十连最高{bq}"; save_game(g)
    r=to_client(g); r["equip_pull"]=cards; r["is_10"]=True
    return jsonify(r)

@app.route("/api/equip_pull100")
@login_required
def api_equip_pull100():
    g=load_game()
    if not g: return api_new()
    if g["jade"]<430: return jsonify({"error":"玉璧不足",**to_client(g)})
    g["jade"]-=430; cards=[]; bq="凡品"
    qcounts={q:0 for q in ["凡品","良品","极品","绝品","传说","神卡"]}
    for _ in range(100):
        card=pull_equip(g.get("equip_pity_counter",0))
        if EQ_QUALITY_ORDER.get(card["quality"],0)>=2: g["equip_pity_counter"]=0
        else: g["equip_pity_counter"]=g.get("equip_pity_counter",0)+1
        qcounts[card["quality"]]=qcounts.get(card["quality"],0)+1
        if EQ_QUALITY_ORDER.get(card["quality"],0)>EQ_QUALITY_ORDER.get(bq,0): bq=card["quality"]
        ed=EQUIP_DATA.get(card["id"],{})
        ex=next((e for e in g["equip_bag"] if isinstance(e,dict) and e.get("id")==card["id"]),None)
        if ex:
            gained=EQUIP_DUPE_EXP.get(card["quality"],10)
            g["equip_exp_total"]=g.get("equip_exp_total",0)+gained
            ex["count"]+=1
        else:
            g["equip_bag"].append({"id":card["id"],"count":1,"upgrade_lv":0})
        cards.append({"id":card["id"],"quality":card["quality"],"name":ed.get("name","?"),"type":ed.get("type","?"),"color":ed.get("color","#888")})
    g["msg"]=f"🔮 铸魂百抽最高{bq}! {qcounts['传说']}传说 {qcounts['绝品']}绝品 {qcounts['极品']}极品"
    save_game(g)
    highlights=[c for c in cards if EQ_QUALITY_ORDER.get(c["quality"],0)>=2]
    r=to_client(g); r["equip_pull"]={"qcounts":qcounts,"highlights":highlights,"all":cards,"count":100}
    return jsonify(r)

# ═══ 武器升级API ═══
@app.route("/api/equip_upgrade/<eid>", methods=["POST"])
@login_required
def api_equip_upgrade(eid):
    g=load_game()
    if not g: return api_new()
    if eid not in EQUIP_DATA: return jsonify({"error":"装备不存在"})
    eb=next((e for e in g["equip_bag"] if isinstance(e,dict) and e.get("id")==eid),None)
    if not eb: return jsonify({"error":"没有该装备"})
    ulv=eb.get("upgrade_lv",0)
    if ulv>=100: return jsonify({"error":"已满级"})
    cost=equip_upgrade_cost(ulv)
    pool=g.get("equip_exp_total",0)
    if pool<cost: return jsonify({"error":f"💫不足，需要{cost}💫(当前{pool})"})
    g["equip_exp_total"]=pool-cost
    eb["upgrade_lv"]=ulv+1
    save_game(g)
    return jsonify({"ok":True,"eid":eid,"upgrade_lv":eb["upgrade_lv"],
        "equip_exp_total":g["equip_exp_total"],"cost":cost,
        "msg":f"⬆ {EQUIP_DATA[eid]['name']}升至+{eb['upgrade_lv']}！",**to_client(g)})

@app.route("/api/equip/enhance/<eid>", methods=["POST"])
@login_required
def api_equip_enhance(eid):
    g=load_game()
    if not g: return api_new()
    if eid not in EQUIP_DATA: return jsonify({"error":"装备不存在"})
    ed=EQUIP_DATA[eid]
    eb=next((e for e in g["equip_bag"] if isinstance(e,dict) and e.get("id")==eid),None)
    if not eb: return jsonify({"error":"没有该装备"})
    ulv=eb.get("upgrade_lv",0)
    if ulv>=15: return jsonify({"error":"已满级"})
    cost_jade=(ulv+1)*15+10  # +0=25, +5=100, +10=175
    cost_count=1+int(ulv/3)  # +0~2要1件, +3~5要2件...
    if g["jade"]<cost_jade: return jsonify({"error":f"玉璧不足(需要{cost_jade})"})
    if eb.get("count",0)<cost_count+1: return jsonify({"error":f"需要{cost_count+1}件同名装备"})
    g["jade"]-=cost_jade
    eb["count"]-=cost_count
    eb["upgrade_lv"]=ulv+1
    new_lv=ulv+1
    save_game(g)
    return jsonify({"ok":True,"upgrade_lv":new_lv,"jade":g["jade"],
        "msg":f"{ed['name']}强化至+{new_lv}！","stats":{
            "atk":int(ed.get("atk",0)*(1+new_lv*0.25)),
            "hp":int(ed.get("hp",0)*(1+new_lv*0.25)),
            "crit":int(ed.get("crit",0)+new_lv//3)
        }})

@app.route("/api/hero/consume", methods=["POST"])
@login_required
def api_hero_consume():
    g=load_game()
    if not g: return api_new()
    data=request.get_json()
    if not data: return jsonify({"error":"无效请求"})
    target_id=data.get("hero_id","")
    consume_ids=data.get("consumed",[])
    if not target_id or not consume_ids: return jsonify({"error":"缺少参数"})
    # 检查目标
    t_inv=next((i for i in g["inventory"] if i["hero_id"]==target_id),None)
    if not t_inv: return jsonify({"error":"未找到该英雄"})
    if target_id in consume_ids: return jsonify({"error":"不能吃自己"})
    # 经验倍率
    RARITY_EXP={"凡品":300,"良品":800,"极品":2000,"绝品":5000,"传说":12000}
    RARITY_EXP_LV={"凡品":50,"良品":100,"极品":200,"绝品":400,"传说":800}
    total_exp=0; names=[]
    for cid in consume_ids:
        ci=next((i for i in g["inventory"] if i["hero_id"]==cid),None)
        if not ci: continue
        hd=HERO_DATA.get(cid)
        if not hd: continue
        if cid in g["lineup"]: return jsonify({"error":f"{hd['name']}在上阵中,不能吞噬"})
        lv=ci.get("level",1)
        sk_lv=ci.get("skill_lv",1)
        base=RARITY_EXP.get(hd["quality"],300)
        per_lv=RARITY_EXP_LV.get(hd["quality"],50)
        exp=base+(lv-1)*per_lv
        # 技能等级加成: Lv2=1.5x, Lv3=2x ... Lv7=4x
        sk_mult=1.0+(sk_lv-1)*0.5
        exp=int(exp*sk_mult)
        total_exp+=exp
        names.append(f"{hd['name']}(Lv{sk_lv})")
        g["inventory"].remove(ci)
    # 加经验
    t_inv["exp"]=t_inv.get("exp",0)+total_exp
    old_lv=t_inv.get("level",1)
    new_lv=old_lv
    while True:
        needed=new_lv*100
        if t_inv["exp"]>=needed:
            t_inv["exp"]-=needed
            new_lv+=1
            t_inv["level"]=new_lv
        else: break
    t_hd=HERO_DATA.get(target_id,{})
    save_game(g)
    return jsonify({"ok":True,"hero_id":target_id,"name":t_hd.get("name","?"),
        "consumed":names,"total_exp":total_exp,
        "old_level":old_lv,"new_level":new_lv,
        "now_exp":t_inv.get("exp",0),"exp_next":new_lv*100,
        **to_client(g)})

app.jinja_env.auto_reload = True  # 模板修改后自动刷新

# ═══ 排行榜 ═══
@app.route("/api/leaderboard")
def api_leaderboard():
    conn=get_db()
    rows=conn.execute("""
        SELECT u.username, g.game_data FROM users u
        JOIN game_saves g ON u.id=g.user_id
        ORDER BY u.id
    """).fetchall()
    rankings=[]
    for row in rows:
        try:
            gd=json.loads(row["game_data"])
            power=calc_pow(HERO_DATA, gd.get("lineup",[]), gd.get("inventory",[]), gd.get("equip_bag",[]))
            stage=gd.get("stage_index",0)
            rankings.append({"name":row["username"],"power":power,"stage":stage})
        except: pass
    rankings.sort(key=lambda x:-x["power"])
    rankings=rankings[:50]
    for i,r in enumerate(rankings):
        r["rank"]=i+1
    conn.close()
    return jsonify(rankings)

if __name__ == "__main__":
    port=int(sys.argv[1]) if len(sys.argv)>1 else 5002
    print(f"🏯 江湖经营 (速度轴+能量+卡牌版)"); print(f"   http://127.0.0.1:{port}")
    app.run(host="127.0.0.1",port=port,debug=False)
