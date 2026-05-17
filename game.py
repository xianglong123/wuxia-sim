#!/usr/bin/env python3
"""🏯 江湖经营 - 速度轴+能量+卡牌战斗版"""
import json, urllib.request, ssl, random, os, sys, re, math, sqlite3, copy
from datetime import datetime
from pathlib import Path
from functools import wraps
from flask import Flask, render_template, jsonify, session, redirect, request
from werkzeug.security import generate_password_hash, check_password_hash

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
    return conn

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            if request.path.startswith("/api/"):
                return jsonify({"error":"未登录","need_login":True})
            return redirect("/login")
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
    conn = get_db()
    if conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone():
        conn.close(); return jsonify({"error":"用户名已存在"})
    conn.execute("INSERT INTO users (username, password_hash) VALUES (?,?)", (username, generate_password_hash(password)))
    conn.commit()
    uid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    g = new_game_inner()
    conn.execute("INSERT INTO game_saves (user_id, game_data) VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET game_data=?", (uid, json.dumps(g, ensure_ascii=False), json.dumps(g, ensure_ascii=False)))
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
    conn.execute("INSERT INTO game_saves (user_id, game_data, updated_at) VALUES (?,?,datetime('now','localtime')) ON CONFLICT(user_id) DO UPDATE SET game_data=?, updated_at=datetime('now','localtime')", (uid, data, data))
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
    "skill_upgrades":{3:"冲锋吸血20%",5:"优先攻击后排",7:"终结一击:全体500%伤害", 9:"#1开局满能量"},
    "basic_name":"龙胆亮银","basic_desc":"亮银枪出如龙，攻击2次每次100%伤害","basic_dmg_pct":1.0,"basic_energy_gain":40,
    "basic_aoe":False,"basic_target":"single","basic_special":[{"type":"multi_hit","count":2}],
    "passive_name":"一身是胆","passive_desc":"每损失10%血量攻击+8%","passive_upgrades":{3:"每损失10%额外+5%暴击",5:"低于30%无敌1秒",7:"损失血量加成翻倍"}})
reg({"id":"guanyu","name":"关羽","class":"战士","quality":"传说","color":"#ffd700",
    "hp":3500,"atk":500,"crit":28,"spd":155,"skill_cost":120,
    "skill_name":"青龙偃月","skill_desc":"蓄力后挥出惊世一刀，对全体敌人造成300%伤害",
    "skill_aoe":True,"skill_target":"all_enemy","skill_special":["execute"],
    "skill_upgrades":{3:"蓄力期间免疫控制",5:"刀气留痕每秒20%×3回合",7:"血量低于50%的敌人直接斩杀", 9:"#1攻击+30%+武圣"},
    "basic_name":"拖刀斩","basic_desc":"拖刀蓄力势如破竹，造成100%伤害","basic_dmg_pct":1.0,"basic_energy_gain":40,
    "basic_aoe":False,"basic_target":"single","basic_special":[],
    "passive_name":"武圣","passive_desc":"开局第一刀必定暴击伤害+50%","passive_upgrades":{3:"第一刀伤害翻倍",5:"前三刀必定暴击",7:"武圣降临:第一次技能真实伤害"}})

HERO_IDS = list(HERO_DATA.keys())
QUALITY_ORDER = {"凡品":0,"良品":1,"极品":2,"绝品":3,"传说":4}
QUALITY_WEIGHTS = {"凡品":40,"良品":30,"极品":20,"绝品":8,"传说":2}
RARITY_COLORS = {"凡品":"#888","良品":"#5adb7a","极品":"#4a8eff","绝品":"#b84aff","传说":"#ffd700"}

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
EQ_QUALITY_ORDER = {"凡品":0,"良品":1,"极品":2,"绝品":3,"传说":4}

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
    boss = si > 0 and si % 10 == 0  # 每10关一个BOSS(不含第0关)
    ep = 1200 + si * 300  # 更高血量，更有挑战
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
            "pull_count":0,"pity_counter":0,"stage_index":0,"ticks":0,"msg":"☯ 欢迎来到江湖！",
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
        crit=hd["crit"]+int(lv/5)
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

def calc_hp(hid, sl, eq, inv=None):
    hd=HERO_DATA.get(hid)
    if not hd: return 0
    lv=inv.get("level",1) if inv else 1
    bonus=hero_level_bonus(lv)
    hp=int(hd["hp"]*bonus); atk=int(hd["atk"]*bonus)
    for s,eid in eq.items():
        if eid and eid in EQUIP_DATA: e=EQUIP_DATA[eid]; hp+=e.get("hp",0); atk+=e.get("atk",0)
    p=hp+atk*2+int(hd.get("spd",100)*2)
    p*=(1+(sl-1)*0.1); return int(p)

def get_bonds(lineup):
    return [b for b in BONDS if all(m in lineup for m in b["members"])]

def rq(pity):
    if pity>=10: return random.choices(["极品","绝品","传说"],weights=[50,35,15])[0]
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

# ═══════════════════════════════════════════
# 卡牌系统
# ═══════════════════════════════════════════
BATTLE_CARDS = {
    "power_strike":{"id":"power_strike","name":"蓄力一击","desc":"下次攻击伤害×2","cost":1,"rarity":"凡品",
        "effect":{"type":"buff_current","stat":"atk_mult","value":1.0,"dur":1}},
    "iron_wall":{"id":"iron_wall","name":"铁壁","desc":"全体减伤30%×2回合","cost":1,"rarity":"良品",
        "effect":{"type":"buff_all_ally","stat":"dmg_reduce","value":0.3,"dur":2}},
    "first_aid":{"id":"first_aid","name":"急救","desc":"回复血量最低队友25%","cost":1,"rarity":"良品",
        "effect":{"type":"heal","target":"lowest_hp","value":0.25}},
    "energize":{"id":"energize","name":"充能","desc":"全体+40能量","cost":2,"rarity":"极品",
        "effect":{"type":"energy_all_ally","value":40}},
    "weaken":{"id":"weaken","name":"虚弱","desc":"全体敌人攻击-20%×2回合","cost":1,"rarity":"凡品",
        "effect":{"type":"debuff_all_enemy","stat":"atk","value":-0.2,"dur":2}},
    "inspire":{"id":"inspire","name":"鼓舞","desc":"全体速度+30×1回合","cost":2,"rarity":"良品",
        "effect":{"type":"buff_all_ally","stat":"spd","value":30,"dur":1}},
    "assassinate":{"id":"assassinate","name":"暗算","desc":"对血量最低敌人造成200%伤害","cost":2,"rarity":"极品",
        "effect":{"type":"dmg","target":"lowest_hp","value":2.0}},
    "group_shield":{"id":"group_shield","name":"群体护盾","desc":"全体获得20%护盾","cost":2,"rarity":"良品",
        "effect":{"type":"shield_all_ally","value":0.2}},
    "taunt_card":{"id":"taunt_card","name":"嘲讽","desc":"强制敌人攻击最肉英雄×1回合","cost":1,"rarity":"凡品",
        "effect":{"type":"taunt","target":"tankiest","dur":1}},
    "double_strike":{"id":"double_strike","name":"连击","desc":"当前英雄本回合额外行动一次","cost":2,"rarity":"绝品",
        "effect":{"type":"extra_action"}},
    "anti_heal":{"id":"anti_heal","name":"禁疗","desc":"全体敌人无法治疗×2回合","cost":2,"rarity":"绝品",
        "effect":{"type":"debuff_all_enemy","stat":"heal_block","value":1,"dur":2}},
    "ultimate":{"id":"ultimate","name":"终极爆发","desc":"全体英雄满能量","cost":3,"rarity":"传说",
        "effect":{"type":"energy_all_ally","value":200}},
    "speed_up":{"id":"speed_up","name":"疾行","desc":"全体速度+50×2回合","cost":2,"rarity":"绝品",
        "effect":{"type":"buff_all_ally","stat":"spd","value":50,"dur":2}},
    "counter":{"id":"counter","name":"以牙还牙","desc":"本回合受击反弹100%伤害","cost":1,"rarity":"良品",
        "effect":{"type":"buff_all_ally","stat":"reflect","value":1.0,"dur":1}},
    "blood_thirst":{"id":"blood_thirst","name":"血怒","desc":"全体吸血30%×1回合","cost":2,"rarity":"绝品",
        "effect":{"type":"buff_all_ally","stat":"lifesteal","value":0.3,"dur":1}},
}

DECK_TEMPLATE = ["power_strike","power_strike","weaken","weaken","taunt_card","taunt_card",
    "iron_wall","iron_wall","first_aid","first_aid","inspire","speed_up",
    "energize","assassinate","group_shield","counter","counter","blood_thirst",
    "double_strike","anti_heal","ultimate"]

def init_deck():
    d = [copy.deepcopy(BATTLE_CARDS[cid]) for cid in DECK_TEMPLATE]
    random.shuffle(d)
    return d

# ═══════════════════════════════════════════
# 速度轴战斗引擎
# ═══════════════════════════════════════════

ENEMY_SKILLS = [
    {"name":"横扫","aoe":True,"desc":"对全体造成伤害","debuffs":[],"buffs":[]},
    {"name":"重击","aoe":False,"desc":"对单体造成伤害","debuffs":[],"buffs":[]},
    {"name":"破甲","aoe":False,"desc":"降低目标防御","debuffs":[{"stat":"dmg_reduce","pct":-0.2,"dur":2}],"buffs":[]},
    {"name":"威吓","aoe":True,"desc":"降低全体攻击","debuffs":[{"stat":"atk","pct":-0.15,"dur":2}],"buffs":[]},
    {"name":"嗜血","aoe":False,"desc":"攻击并吸血","debuffs":[],"buffs":[]},
    {"name":"鬼哭","aoe":True,"desc":"全体攻击+降低暴击","debuffs":[{"stat":"crit","pct":-0.2,"dur":2}],"buffs":[]},
    {"name":"狂暴","aoe":False,"desc":"提升自身攻击","debuffs":[],"buffs":[{"stat":"atk","pct":0.25,"dur":3}]},
    {"name":"铁壁","aoe":True,"desc":"提升全体防御","debuffs":[],"buffs":[{"stat":"dmg_reduce","pct":0.2,"dur":2}]},
    {"name":"邪咒","aoe":True,"desc":"全体攻击+降低暴击率","debuffs":[{"stat":"atk","pct":-0.1,"dur":2,"chance":0.8}],"buffs":[]},
    {"name":"回春","aoe":True,"desc":"回复全体血量","debuffs":[],"buffs":[],"heal":0.15},
]

def gen_enemy(name, ps, boss=False):
    c=random.choice(ECS)
    skill=random.choice(ENEMY_SKILLS)
    if boss:
        hp=int(ps*random.uniform(4.0, 7.0))
        atk=int(ps*random.uniform(0.05, 0.10))
        q=random.choices(["极品","绝品","传说"],weights=[50,35,15])[0]
        crit=random.randint(15,40)
        spd=random.randint(100,160)
        name="【BOSS】"+name
    else:
        # 血多攻少，让战斗有回合感
        hp=int(ps*random.uniform(2.5, 4.0))
        atk=int(ps*random.uniform(0.025, 0.045))
        q=random.choices(["凡品","良品","极品","绝品","传说"],weights=[30,30,25,12,3])[0]
        crit=random.randint(5,30)
        spd=random.randint(80,180)
    return {"name":name,"class":c,"quality":q,"color":RARITY_COLORS.get(q,"#888"),
            "hp":hp,"max_hp":hp,"atk":atk,"crit":crit,"spd":spd,
            "alive":True,"shield":0,"buffs":[],"debuffs":[],"stunned":False,"frozen":False,"reflect":False,
            "skill_name":skill["name"],"skill_aoe":skill["aoe"],"skill_debuffs":skill.get("debuffs",[]),
            "skill_buffs":skill.get("buffs",[]),"skill_heal":skill.get("heal",0),
            "energy":0,"skill_cost":random.randint(80,150),
            "energy_gain":25,"basic_dmg_pct":0.6,"side":"enemy"}

def h2f(hid, inv, equip_bag=None):
    hd=HERO_DATA.get(hid)
    if not hd: return None
    lv=inv.get("level",1)
    # 基础属性
    hp=int(hd["hp"]*hero_level_bonus(lv)); atk=int(hd["atk"]*hero_level_bonus(lv))
    crit=hd["crit"]+int(lv/5)  # 每5级+1暴击
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
    return {"id":hid,"name":hd["name"],"class":hd["class"],"quality":hd["quality"],"color":hd["color"],
            "hp":hp,"max_hp":hp,"atk":atk,"crit":crit,"spd":hd.get("spd",100),"_skill_cost":hd.get("skill_cost",100),
            "alive":True,"shield":0,"buffs":[],"debuffs":[],"stunned":False,"frozen":False,"reflect":False,
            "energy":0,"action_bar":random.randint(0,400),
            "_hd":hd,"_skill_lv":inv.get("skill_lv",1),"side":"ally"}

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

def get_dmg_reduce(t):
    """独立计算减伤(0~0.8): dmg_reduce直接从buff累加pct"""
    dr=0
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

def pick_best_card(hand, my_heroes, enemies, current_hero=None):
    """AI自动选牌：根据当前战况选最优卡"""
    if not hand: return None
    alive_h=[u for u in my_heroes if u.get("alive",True)]
    alive_e=[u for u in enemies if u.get("alive",True)]
    # 优先级: 急救(有人残血) > 冷疗(敌有奶) > 虚弱 > 鼓舞 > 充能 > 暗算 > 铁壁 > 群体护盾 > 蓄力
    lowest_hp_ratio = min([u["hp"]/max(1,u["max_hp"]) for u in alive_h]) if alive_h else 1
    # 有人血量 < 30% 且手上有急救
    if lowest_hp_ratio < 0.3:
        for c in hand:
            if c["id"]=="first_aid": return c
    # 有人残血且华佗/小翠有能量
    if lowest_hp_ratio < 0.4:
        for c in hand:
            if c["id"]=="iron_wall": return c
    # 己方有群奶就加强势
    has_group_healer = any(h.get("_hd") and h["_hd"].get("skill_aoe") and h["_hd"].get("skill_heal_pct") for h in alive_h)
    # 暗算打残血
    if alive_e:
        lowest_hp_enemy = min(alive_e, key=lambda x: x["hp"])
        if lowest_hp_enemy["hp"] < lowest_hp_enemy["max_hp"] * 0.4:
            for c in hand:
                if c["id"]=="assassinate": return c
    if len(alive_e) >= 4:
        for c in hand:
            if c["id"]=="energize": return c
        for c in hand:
            if c["id"]=="group_shield": return c
    for c in hand:
        if c["id"]=="energize": return c
    for c in hand:
        if c["id"]=="first_aid": return c
    for c in hand:
        if c["id"]=="iron_wall": return c
    for c in hand:
        if c["id"]=="power_strike" and current_hero and current_hero["energy"]>=current_hero.get("_skill_cost",100):
            return c
    for c in hand:
        if c["rarity"] in ("传说","绝品"):
            return c
    return hand[0] if hand else None

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
    # 位置系统
    for i, h in enumerate(my_heroes):
        h["position"] = i + 1
    for i, e in enumerate(enemies):
        e["position"] = 7 + i

    # 卡牌系统
    deck = init_deck()
    hand = []
    for _ in range(min(3, len(deck))):
        hand.append(deck.pop(0))
    card_energy = 1
    max_card_energy = 3

    all_actions = []
    logs = []  # 战斗记录

    # 被动系统上下文
    passive_ctx = {"all_actions": all_actions, "passive_counters": {}, "triggered":set()}

    # 战斗开始被动
    for u in my_heroes:
        hd=u.get("_hd")
        if hd:
            sk_lv=u.get("_skill_lv",1)
            # 郭奉孝: 奇谋 - 战斗开始回复全体15%血量
            if hd["id"]=="guojia":
                heal_pct=0.15+(0.1 if sk_lv>=3 else 0)
                for a in my_heroes:
                    if a.get("alive",True):
                        heal=int(a["max_hp"]*heal_pct)
                        a["hp"]=min(a["max_hp"],a["hp"]+heal)
                all_actions.append({"side":"heal","type":"passive","attacker_name":"郭奉孝",
                    "skill":"奇谋","aoe":True,"targets":[{"name":a["name"],"heal":int(a["max_hp"]*heal_pct)} for a in my_heroes if a.get("alive",True)]})
            # 甘宁: 铃铛 - 降低敌方全体10%攻击
            if hd["id"]=="ganning":
                atk_pct=-0.1-(0.1 if sk_lv>=3 else 0)
                for e in enemies:
                    if e.get("alive",True):
                        e["debuffs"].append({"stat":"atk","pct":atk_pct,"dur":-1})
            # 貂蝉: 离间 - 魅惑一个敌人3秒
            if hd["id"]=="diaochan":
                alive_e=[e for e in enemies if e.get("alive",True)]
                if alive_e:
                    target=random.choice(alive_e)
                    target["debuffs"].append({"stat":"charm","pct":1.0,"dur":3})
                    all_actions.append({"side":"enemy","type":"passive","attacker_name":"貂蝉",
                        "skill":"离间","aoe":False,"target_name":target["name"],"msg":"魅惑!"})

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
            "debuffs":[d["stat"] for d in e.get("debuffs",[])]} for e in enemies]
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
                    # 关羽武圣被动
                    if unit.get("_hd") and unit["_hd"]["id"]=="guanyu":
                        check_passives(unit, unit["_hd"], unit.get("_skill_lv",1), my_heroes, enemies, "on_skill", passive_ctx)
                else:
                    eg = hd.get("basic_energy_gain", 25) if hd else 25
                    unit["energy"] += eg
                    if unit["energy"] > 200: unit["energy"] = 200
                    act = hero_basic_attack(unit, my_heroes, enemies)
                act["energy_after"] = unit["energy"]
                all_actions.append(act)
                # 注入当时HP状态(分敌我列表计算idx)
                a2=act
                if a2.get("type")!="card_play":
                    a2["attacker_idx"]=next((i for i,uu in enumerate(my_heroes) if uu.get("name")==a2.get("attacker_name")),0)
                    # 注入攻击者当时的有效攻暴
                    a2["hero_eff_atk"]=cstat(unit,"atk",unit["atk"])
                    a2["hero_eff_crit"]=cstat(unit,"crit",unit["crit"])
                    a2["hero_base_atk"]=unit["atk"]
                    a2["hero_base_crit"]=unit["crit"]
                    a2["hero_buffs"]=[b["stat"] for b in unit.get("buffs",[])]
                    a2["hero_debuffs"]=[d["stat"] for d in unit.get("debuffs",[])]
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
                        if a2.get("killed") and uu: a2["target_hp_pct"]=0

                # ===== 打牌阶段 (AI自动选牌) =====
                if hand and card_energy > 0:
                    played = pick_best_card(hand, my_heroes, enemies, unit)
                    if played and played["cost"] <= card_energy:
                        hand.remove(played)
                        card_energy -= played["cost"]
                        card_result = apply_card_effect(played, my_heroes, enemies, unit)
                        card_result.update({
                            "card": played,
                            "type": "card_play",
                            "attacker_name": unit["name"]
                        })
                        all_actions.append(card_result)
                        logs.append(f"🎴 {unit['name']} 使用 {played['name']}！{played['desc']}")

            # 被动触发检查(击杀/队友低血/队友死亡/受击)
            for passive_unit in my_heroes:
                phd=passive_unit.get("_hd")
                if not phd or not passive_unit.get("alive",True): continue
                psk_lv=passive_unit.get("_skill_lv",1)
                # 检查刚被击杀的目标(吕布无双等)
                if unit.get("side")=="ally" and act.get("killed") and act.get("target_name"):
                    check_passives(passive_unit, phd, psk_lv, my_heroes, enemies, "on_kill", passive_ctx)
                # 受击被动(张飞万人敌/赵云一身是胆)
                if unit.get("side")=="enemy" and act.get("target_name"):
                    if passive_unit["name"]==act.get("target_name"):
                        check_passives(passive_unit, phd, psk_lv, my_heroes, enemies, "on_hit", passive_ctx)
                    elif act.get("aoe") and act.get("targets"):
                        for tg in act["targets"]:
                            if passive_unit["name"]==tg.get("name"):
                                check_passives(passive_unit, phd, psk_lv, my_heroes, enemies, "on_hit", passive_ctx)
                # 华佗: 检查最低血量
                if phd["id"]=="huatuo":
                    lowest=min([a for a in my_heroes if a.get("alive",True)],key=lambda x:x["hp"]/max(1,x["max_hp"]),default=None)
                    if lowest and lowest["hp"]/max(1,lowest["max_hp"])<0.3 and lowest!=passive_unit:
                        check_passives(passive_unit, phd, psk_lv, my_heroes, enemies, "on_ally_low_hp", passive_ctx)
                # 蔡文姬: 检查队友死亡
                if phd["id"]=="caiwenji" and act.get("killed") and act.get("side")=="enemy" and act.get("target_name") and any(u.get("name")==act["target_name"] and u.get("alive")==False for u in my_heroes):
                    check_passives(passive_unit, phd, psk_lv, my_heroes, enemies, "on_ally_die", passive_ctx)

            # 检查战斗结束
            alive_h = [u for u in my_heroes if u.get("alive",True)]
            alive_e = [u for u in enemies if u.get("alive",True)]
            if not alive_h or not alive_e: break

        # 每轮(所有人行动完)抽牌+恢复打牌能量
        if actions_since_draw >= len([u for u in all_u if u.get("alive",True)]):
            actions_since_draw = 0
            card_energy = min(card_energy + 1, max_card_energy)
            if len(hand) < 5:
                if deck:
                    hand.append(deck.pop(0))
                else:
                    deck = init_deck()
                    hand.append(deck.pop(0))
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
        "debuffs":[d["stat"] for d in e.get("debuffs",[])]} for e in enemies]

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
    r["cards_played"] = len([a for a in all_actions if a.get("type")=="card_play"])
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
        self.deck = init_deck()
        self.hand = []
        for _ in range(min(3, len(self.deck))):
            self.hand.append(self.deck.pop(0))
        self.card_energy = 1
        self.max_card_energy = 3
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
        self._init_passives()
        # 保存初始状态（用于前端第一帧渲染）
        self._initial_state = {
            "my_heroes": self._get_heroes_state(),
            "enemies": self._get_enemies_state(),
            "lv9_buffs": getattr(self, '_lv9_buffs', [])
        }

    def _init_passives(self):
        for u in self.my_heroes:
            hd=u.get("_hd")
            if not hd: continue
            sk_lv=u.get("_skill_lv",1)
            if hd["id"]=="guojia":
                heal_pct=0.15+(0.1 if sk_lv>=3 else 0)
                for a in self.my_heroes:
                    if a.get("alive",True):
                        heal=int(a["max_hp"]*heal_pct)
                        a["hp"]=min(a["max_hp"],a["hp"]+heal)
                self.all_actions.append({"side":"heal","type":"passive","attacker_name":"郭奉孝",
                    "skill":"奇谋","aoe":True,"targets":[{"name":a["name"],"heal":int(a["max_hp"]*heal_pct)} for a in self.my_heroes if a.get("alive",True)]})
            if hd["id"]=="ganning":
                atk_pct=-0.1-(0.1 if sk_lv>=3 else 0)
                for e in self.enemies:
                    if e.get("alive",True):
                        e["debuffs"].append({"stat":"atk","pct":atk_pct,"dur":-1})
            if hd["id"]=="diaochan":
                alive_e=[e for e in self.enemies if e.get("alive",True)]
                if alive_e:
                    target=random.choice(alive_e)
                    target["debuffs"].append({"stat":"charm","pct":1.0,"dur":3})
                    self.all_actions.append({"side":"enemy","type":"passive","attacker_name":"貂蝉",
                        "skill":"离间","aoe":False,"target_name":target["name"],"msg":"魅惑!"})

        # Lv9位置天赋
        self._lv9_buffs=apply_lv9_bonuses(self.my_heroes, self.enemies)
        for b in self._lv9_buffs:
            self.all_actions.append({"side":"ally","type":"passive","attacker_name":"天赋","skill":b["desc"],"aoe":False,"target_name":"","msg":b["desc"]})

    def _inject_action_hp(self, a):
        """注入实时HP/idx到action"""
        if a.get("type")=="card_play": return
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
                # 关羽武圣被动
                if hd and hd["id"]=="guanyu":
                    check_passives(unit,hd,unit.get("_skill_lv",1),self.my_heroes,self.enemies,"on_skill",self.passive_ctx)
            else:
                eg=hd.get("basic_energy_gain",25) if hd else 25
                unit["energy"]+=eg
                if unit["energy"]>200: unit["energy"]=200
                act=hero_basic_attack(unit,self.my_heroes,self.enemies)
            act["energy_after"]=unit["energy"]
            self.all_actions.append(act)
            self._inject_action_hp(act)
            # 被动触发检查
            for pu in self.my_heroes:
                phd=pu.get("_hd")
                if not phd or not pu.get("alive",True): continue
                psk_lv=pu.get("_skill_lv",1)
                if unit.get("side")=="ally" and act.get("killed") and act.get("target_name"):
                    check_passives(pu,phd,psk_lv,self.my_heroes,self.enemies,"on_kill",self.passive_ctx)
                # 受击被动(张飞万人敌/赵云一身是胆)
                if unit.get("side")=="enemy" and act.get("target_name"):
                    if pu["name"]==act.get("target_name"):
                        check_passives(pu,phd,psk_lv,self.my_heroes,self.enemies,"on_hit",self.passive_ctx)
                    elif act.get("aoe") and act.get("targets"):
                        for tg in act["targets"]:
                            if pu["name"]==tg.get("name"):
                                check_passives(pu,phd,psk_lv,self.my_heroes,self.enemies,"on_hit",self.passive_ctx)
                if phd["id"]=="huatuo":
                    lowest=min([a for a in self.my_heroes if a.get("alive",True)],key=lambda x:x["hp"]/max(1,x["max_hp"]),default=None)
                    if lowest and lowest["hp"]/max(1,lowest["max_hp"])<0.3 and lowest!=pu:
                        check_passives(pu,phd,psk_lv,self.my_heroes,self.enemies,"on_ally_low_hp",self.passive_ctx)
                if phd["id"]=="caiwenji" and act.get("killed") and any(u.get("name")==act.get("target_name") and u.get("alive")==False for u in self.my_heroes):
                    check_passives(pu,phd,psk_lv,self.my_heroes,self.enemies,"on_ally_die",self.passive_ctx)

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

            # 每轮结束后抽牌+恢复能量
            total_alive=len([u for u in self.all_u if u.get("alive",True)])
            if total_alive>0 and self.actions_since_draw>=total_alive:
                self.actions_since_draw=0
                # 恢复1点打牌能量
                self.card_energy=min(self.card_energy+1,self.max_card_energy)
                # 自动抽1张
                if len(self.hand)<5:
                    if self.deck:
                        self.hand.append(self.deck.pop(0))
                    else:
                        self.deck=init_deck()
                        self.hand.append(self.deck.pop(0))
                tick_buffs(self.all_u)
                # 有新牌和有能量 → 打牌阶段
                if self.hand and self.card_energy>0:
                    if self.auto_mode:
                        # 托管模式：自动选牌出牌
                        self.auto_play_card()
                        continue
                    return self._make_card_state()
                continue
            elif self.tick_no%3==0:
                tick_buffs(self.all_u)

        # 战斗结束
        self.done=True
        return self._make_result()

    def _get_new_actions(self):
        acts = self.all_actions[self._last_sent:]
        self._last_sent = len(self.all_actions)
        return acts

    def _make_card_state(self):
        return {
            "phase":"card",
            "initial_state":self._initial_state,
            "hand":[{"id":c["id"],"name":c["name"],"desc":c["desc"],"cost":c["cost"],"rarity":c["rarity"]} for c in self.hand],
            "card_energy":self.card_energy,
            "my_heroes":self._get_heroes_state(),
            "enemies":self._get_enemies_state(),
            "actions":self._get_new_actions()
        }

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
            "debuffs":[d["stat"] for d in e.get("debuffs",[])]} for e in self.enemies]

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
        r["cards_played"]=len([a for a in self.all_actions if a.get("type")=="card_play"])
        r["phase"]="done"
        return r

    def play_card(self, card_id):
        """玩家打出一张牌，应用效果后继续自动行动"""
        card=next((c for c in self.hand if c["id"]==card_id),None)
        if not card: return {"error":"没有这张牌"}
        if card["cost"]>self.card_energy: return {"error":"能量不足"}
        self.hand.remove(card)
        self.card_energy-=card["cost"]
        # 找到当前行动的人
        current_hero=next((u for u in self.my_heroes if u.get("alive",True)),None)
        card_result=apply_card_effect(card,self.my_heroes,self.enemies,current_hero)
        card_result.update({"card":card,"type":"card_play","attacker_name":current_hero["name"] if current_hero else ""})
        self.all_actions.append(card_result)
        self._inject_action_hp(card_result)
        # 继续自动行动直到下次打牌或结束
        return self.step_until_card()

    def skip_card(self):
        """跳过打牌，继续自动行动"""
        return self.step_until_card()

    def auto_play_card(self):
        """托管模式自动选牌出牌"""
        if not self.hand or self.card_energy<=0:
            return
        affordable=[c for c in self.hand if c["cost"]<=self.card_energy]
        if not affordable:
            return
        # 选牌策略
        # 1. 有敌方低血量→暗算
        alive_e=[e for e in self.enemies if e.get("alive",True)]
        if alive_e:
            lowest_e=min(alive_e, key=lambda x: x["hp"])
            if lowest_e["hp"]/max(1,lowest_e["max_hp"])<0.3:
                assassinate=next((c for c in affordable if c["id"]=="assassinate"),None)
                if assassinate:
                    self._do_auto_play(assassinate)
                    return
        # 2. 我方低血量→急救
        alive_a=[a for a in self.my_heroes if a.get("alive",True)]
        if alive_a:
            lowest_a=min(alive_a, key=lambda x: x["hp"]/max(1,x["max_hp"]))
            if lowest_a["hp"]/max(1,lowest_a["max_hp"])<0.35:
                heal=next((c for c in affordable if c["id"]=="first_aid"),None)
                if heal:
                    self._do_auto_play(heal)
                    return
        # 3. 选稀有度最高的可支付卡
        rarity_order={"凡品":0,"良品":1,"极品":2,"绝品":3,"传说":4}
        affordable.sort(key=lambda c: -rarity_order.get(c["rarity"],0))
        self._do_auto_play(affordable[0])

    def _do_auto_play(self, card):
        """内部执行出牌"""
        self.hand.remove(card)
        self.card_energy-=card["cost"]
        current_hero=next((u for u in self.my_heroes if u.get("alive",True)),None)
        card_result=apply_card_effect(card,self.my_heroes,self.enemies,current_hero)
        card_result.update({"card":card,"type":"card_play","attacker_name":current_hero["name"] if current_hero else ""})
        self.all_actions.append(card_result)
        self._inject_action_hp(card_result)

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
    enemies=[]
    used_names=set()
    for _ in range(6):
        n=random.choice([x for x in ENEMY_NAMES if x not in used_names] or ENEMY_NAMES)
        used_names.add(n)
        pp=stage["power"]/6.0
        enemies.append(gen_enemy(n,pp))
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
    data=request.get_json()
    if data and data.get("auto"):
        sess.auto_mode=True
    # 第一步: 运行初始被动, 然后直到card点(托管模式直接打完)
    result=sess.step_until_card()
    result["auto"]=sess.auto_mode
    return jsonify(result)

@app.route("/api/battle/toggle-auto", methods=["POST"])
@login_required
def api_battle_toggle_auto():
    uid=session.get("user_id")
    sess=BATTLE_SESSIONS.get(uid)
    if not sess: return jsonify({"error":"没有活跃战斗"})
    sess.auto_mode=not sess.auto_mode
    # 如果切到自动，立即继续战斗
    if sess.auto_mode:
        result=sess.step_until_card()
        result["auto"]=True
        # 战斗结束处理
        if result.get("phase")=="done":
            _apply_battle_rewards(result,uid)
    else:
        result={"ok":True,"auto":False,"phase":sess._get_current_phase()}
    return jsonify(result)

@app.route("/api/battle/card", methods=["POST"])
@login_required
def api_battle_card():
    uid=session.get("user_id")
    sess=BATTLE_SESSIONS.get(uid)
    if not sess: return jsonify({"error":"没有活跃战斗"})
    data=request.get_json()
    card_id=data.get("card_id","") if data else ""
    if card_id:
        result=sess.play_card(card_id)
    else:
        result=sess.skip_card()
    # 如果战斗结束，清除会话+保存奖励
    if result.get("phase")=="done":
        _apply_battle_rewards(result,uid)
    return jsonify(result)

@app.route("/api/battle/skip", methods=["POST"])
@login_required
def api_battle_skip():
    uid=session.get("user_id")
    sess=BATTLE_SESSIONS.get(uid)
    if not sess: return jsonify({"error":"没有活跃战斗"})
    result=sess.skip_card()
    _apply_battle_rewards(result,uid)
    return jsonify(result)

def _apply_battle_rewards(result,uid):
    """战斗结束: 保存奖励+经验"""
    if result.get("phase")!="done": return
    del BATTLE_SESSIONS[uid]
    g=load_game()
    if not g: return
    if result["win"]:
        jr=result.get("jade_reward",3)
        si=g["stage_index"]  # 存当前关数用于经验
        g["stage_index"]+=1
        if result.get("equip_reward"):
            ex=next((e for e in g["equip_bag"] if isinstance(e,dict) and e.get("id")==result["equip_reward"]),None)
            if ex: ex["count"]+=1
            else: g["equip_bag"].append({"id":result["equip_reward"],"count":1})
        # 经验奖励
        exp_msg=add_exp_to_heroes(g,si)
        result["exp_msg"]=exp_msg
    else:
        g["jade"]+=result.get("jade_reward",2)
    g["ticks"]+=1; save_game(g)
    r2=to_client(g) if g else {}
    result["stage_name"]=r2.get("stage",{}).get("name","")
    result["new_stage"]=r2.get("stage",{}).get("name","")

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
            if cr: d = int(d * 1.5)
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
        if cr: d = int(d * 1.5)
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
                if cr2: d2 = int(d2 * 1.5)
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
            extra["extra_damage"]=0.05  # 诸葛亮Lv3: 额外50%闪电伤害(=5%maxHP)
        if hid=="yangyouji":
            if not extra.get("extra_buffs"): extra["extra_buffs"]=[]
            extra["extra_buffs"].append({"stat":"crit","pct":0.3,"dur":2})

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

    # ─── Lv7 ───
    if sk_lv>=7:
        if hid=="zhaoyun":
            extra["execute_pct"]=True  # 赵云Lv7: 终结一击500%伤害
            extra["extra_damage"]=1.5  # 额外伤害
        if hid=="zhangfei":
            if not extra.get("extra_buffs"): extra["extra_buffs"]=[]
            extra["extra_buffs"].append({"stat":"invincible","pct":1.0,"dur":1})  # 张飞Lv7: 全体无敌1回合
        if hid=="guanyu":
            extra["execute_pct"]=True  # 关羽Lv7: 血量低于50%斩杀
        if hid=="lihai":
            extra["guaranteed_crit"]=True  # 李白Lv7: 剑开天门
            extra["extra_damage"]=2.0  # 9999其实=200%maxHP
        if hid=="diaochan":
            pass  # 貂蝉Lv7: 溅射50%
        if hid=="huatuo":
            pass  # 华佗Lv7: 免疫结束重置冷却
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
        if cr: d=int(d*1.5)
        # 养由基Lv5: 暴击4倍
        if cr and up and up.get("quad_crit"): d*=4
        # 关羽Lv7: 斩杀(HP<50%直接杀)
        if up and up.get("execute_pct") and t["hp"]/max(1,t["max_hp"])<0.5:
            d=t["hp"]
        # 减伤
        dr=get_dmg_reduce(t)
        d=int(d*(1-dr))
        ignore="ignore_shield" in specials
        r=apply_dmg(t,d,ignore)
        killed=t["hp"]<=0
        if killed: t["alive"]=False
        # 吸血
        if "lifesteal" in specials:
            ls_pct=0.3
            if up and up.get("lifesteal_pct"): ls_pct=up["lifesteal_pct"]
            unit["hp"]=min(unit["max_hp"],unit["hp"]+int(r["damage"]*ls_pct))
        return {"name":t["name"],"damage":r["damage"],"crit":cr,"killed":killed,"immune":r.get("immune",False)}

    # 收集总伤害结果
    all_targets_data=[]
    total_dmg=0
    
    if is_aoe:
        # AOE: 真实命中multi_hit次, 每次对全体存活敌人造成伤害
        remaining_hits = multi_hit_count
        while remaining_hits > 0:
            alive_tars = [t for t in tars if t.get("alive", True)]
            if not alive_tars: break
            for t in alive_tars:
                td = _process_dmg_target(t, True)
                if td: all_targets_data.append(td); total_dmg += td["damage"]
            remaining_hits -= 1
        # 内部已拆开每击伤害, 不让expand再次拆分
        multi_hit_count = 1
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
        for e in enemies:
            if e.get("alive",True):
                extra=int(e["max_hp"]*up["extra_damage"])
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

    if is_aoe:
        return {"side":"ally","type":"skill","attacker_name":unit["name"],"skill":sk_name,
                "aoe":True,"targets":all_targets_data}
    else:
        # 单目标多段: 取最后一个目标显示
        last=all_targets_data[-1] if all_targets_data else {"name":"","damage":0,"crit":False,"killed":False}
        return {"side":"ally","type":"skill","attacker_name":unit["name"],"skill":sk_name,
                "aoe":False,"target_name":last["name"],"damage":last["damage"],"crit":last["crit"],"killed":last["killed"]}

def enemy_basic_attack(unit, allies, enemies):
    alive_h = [a for a in allies if a.get("alive",True)]
    if not alive_h: return {"side":"enemy","type":"basic","attacker_name":unit["name"],"damage":0}
    t = random.choice(alive_h)
    ba = cstat(unit, "atk", unit["atk"])
    dmg_pct = unit.get("basic_dmg_pct", 0.6)
    d = int(ba * dmg_pct * random.uniform(0.8, 1.0))
    cr = random.random() < unit["crit"]/100
    if cr: d = int(d * 1.5)
    dr = get_dmg_reduce(t)
    d = int(d * (1 - dr))
    r = apply_dmg(t, d)
    killed = t["hp"] <= 0
    if killed: t["alive"] = False
    return {"side":"enemy","type":"basic","attacker_name":unit["name"],"skill":unit.get("skill_name","攻击"),
            "aoe":False,"target_name":t["name"],"damage":r["damage"],"crit":cr,"killed":killed}

def enemy_use_skill(unit, allies, enemies):
    alive_h = [a for a in allies if a.get("alive",True)]
    if not alive_h: return {"side":"enemy","type":"skill","attacker_name":unit["name"],"damage":0}
    is_aoe = unit.get("skill_aoe", False)
    heal_pct = unit.get("skill_heal", 0)

    if heal_pct > 0 and random.random() < 0.35:
        alive_e = [e for e in enemies if e.get("alive",True)]
        for t in alive_e:
            heal = int(t["max_hp"] * heal_pct)
            t["hp"] = min(t["max_hp"], t["hp"] + heal)
        return {"side":"enemy","type":"skill","attacker_name":unit["name"],"skill":unit.get("skill_name","回春"),
                "aoe":True,"targets":[{"name":t["name"],"heal":int(t["max_hp"]*heal_pct)} for t in alive_e]}

    if is_aoe:
        tars = alive_h
    else:
        tars = [random.choice(alive_h)]
    total_dmg = 0
    for t in tars:
        ba = cstat(unit, "atk", unit["atk"])
        d = int(ba * random.uniform(0.4, 0.8))
        cr = random.random() < unit["crit"]/100
        if cr: d = int(d * 1.5)
        dr = get_dmg_reduce(t)
        d = int(d * (1 - dr))
        r = apply_dmg(t, d)
        total_dmg += r["damage"]
        killed = t["hp"] <= 0
        if killed: t["alive"] = False
        for deb in unit.get("skill_debuffs", []):
            if random.random() < deb.get("chance", 1.0):
                t["debuffs"].append({"stat":deb["stat"],"pct":deb["pct"],"dur":deb["dur"]})

    ei = 0 if tars else 0
    t = tars[0] if tars else alive_h[0]
    return {"side":"enemy","type":"skill","attacker_name":unit["name"],"skill":unit.get("skill_name","攻击"),
            "aoe":is_aoe,"target_name":t["name"],"damage":total_dmg,"crit":False,"killed":False}

def apply_card_effect(card, allies, enemies, current_hero):
    """应用卡牌效果"""
    e = card["effect"]
    etype = e["type"]

    if etype == "buff_current":
        current_hero["buffs"].append({"stat":e["stat"],"pct":e["value"],"dur":e["dur"]})
        return {"msg":f"{card['name']}: {e.get('desc','')}"}
    elif etype == "buff_all_ally":
        for a in allies:
            if a.get("alive",True):
                a["buffs"].append({"stat":e["stat"],"pct":e["value"],"dur":e["dur"]})
        return {"msg":f"全体{e.get('desc','')}"}
    elif etype == "heal":
        alive_h = [a for a in allies if a.get("alive",True)]
        if e["target"] == "lowest_hp":
            t = min(alive_h, key=lambda x: x["hp"]) if alive_h else None
            if t:
                heal = int(t["max_hp"] * e["value"])
                t["hp"] = min(t["max_hp"], t["hp"] + heal)
                return {"msg":f"回复 {t['name']}+{heal}❤️", "target_name":t["name"], "heal":heal}
        return {"msg":f"回复{e.get('desc','')}"}
    elif etype == "energy_all_ally":
        for a in allies:
            if a.get("alive",True):
                a["energy"] = min(a["energy"] + e["value"], 200)
        return {"msg":f"全体+{e['value']}能量"}
    elif etype == "debuff_all_enemy":
        for e_ in enemies:
            if e_.get("alive",True):
                e_["debuffs"].append({"stat":e["stat"],"pct":e["value"],"dur":e["dur"]})
        return {"msg":f"全体敌人{e.get('desc','')}"}
    elif etype == "dmg":
        alive_e = [e_ for e_ in enemies if e_.get("alive",True)]
        if e["target"] == "lowest_hp":
            t = min(alive_e, key=lambda x: x["hp"]) if alive_e else None
            if t:
                d = int(t["atk"] * e["value"] * random.uniform(2, 3))
                apply_dmg(t, d)
                killed = t["hp"] <= 0
                if killed: t["alive"] = False
                return {"msg":f"对 {t['name']} 造成 {d} 伤害", "target_name":t["name"], "damage":d, "killed":killed}
        return {"msg":f"{e.get('desc','')}"}
    elif etype == "shield_all_ally":
        for a in allies:
            if a.get("alive",True):
                a["shield"] = int(a["max_hp"] * e["value"])
        return {"msg":"全体获得护盾"}
    elif etype == "taunt":
        alive_e = [e_ for e_ in enemies if e_.get("alive",True)]
        for e_ in alive_e:
            e_["buffs"].append({"stat":"taunted","pct":1.0,"dur":e.get("dur",1)})
        return {"msg":"嘲讽敌人"}
    elif etype == "extra_action":
        current_hero["action_bar"] = 1500  # 强制下次行动
        return {"msg":"额外行动"}
    return {"msg":card.get("desc","")}

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

    enemies = []
    used_names = set()
    for _ in range(6):
        n = random.choice([x for x in ENEMY_NAMES if x not in used_names] or ENEMY_NAMES)
        used_names.add(n)
        pp = stage["power"] / 6.0
        enemies.append(gen_enemy(n, pp))

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
        enemies=[]
        used_names=set()
        for _ in range(6):
            n=random.choice([x for x in ENEMY_NAMES if x not in used_names] or ENEMY_NAMES)
            used_names.add(n)
            enemies.append(gen_enemy(n, stage["power"]/6.0))
        result=run_speed_battle(my_heroes, enemies, stage)
        g["ticks"]+=1; tj+=result.get("jade_reward",0)
        if result.get("equip_reward"): te.append(result["equip_reward"])
        if not result.get("win",False): break
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
    if ex: ex["skill_lv"]=min(9,ex["skill_lv"]+1); g["msg"]=f"🎴 抽到{card['quality']}{HERO_DATA[card['hero_id']]['name']}！技能升级至Lv.{ex['skill_lv']}"
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
        if ex: ex["skill_lv"]=min(9,ex["skill_lv"]+1)
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
    pulls=[]; qcounts={q:0 for q in ["凡品","良品","极品","绝品","传说"]}
    bq="凡品"
    for _ in range(100):
        card=pull_h(g["pity_counter"])
        if QUALITY_ORDER.get(card["quality"],0)>=2: g["pity_counter"]=0
        else: g["pity_counter"]+=1
        qcounts[card["quality"]]=qcounts.get(card["quality"],0)+1
        if QUALITY_ORDER.get(card["quality"],0)>QUALITY_ORDER.get(bq,0): bq=card["quality"]
        ex=next((i for i in g["inventory"] if i["hero_id"]==card["hero_id"]),None)
        if ex: ex["skill_lv"]=min(9,ex["skill_lv"]+1)
        else: g["inventory"].append(card)
        pulls.append(card)
    g["msg"]=f"🎴 百抽最高{bq}! {qcounts['传说']}传说 {qcounts['绝品']}绝品 {qcounts['极品']}极品"
    save_game(g)
    highlights=[c for c in pulls if QUALITY_ORDER.get(c["quality"],0)>=2]
    r=to_client(g); r["pull"]={"qcounts":qcounts,"highlights":[{"hero_id":c["hero_id"],"quality":c["quality"],"hero_name":HERO_DATA[c["hero_id"]]["name"],"hero_class":HERO_DATA[c["hero_id"]]["class"]} for c in highlights],"count":100}
    return jsonify(r)

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
    eff_crit=hd["crit"]+int(lv/5)
    for s,eid in inv["equipped"].items():
        if eid and eid in EQUIP_DATA:
            e=EQUIP_DATA[eid]; eff_hp+=e.get("hp",0); eff_atk+=e.get("atk",0)
    return jsonify({"hero_id":hero_id,"name":hd["name"],"class":hd["class"],"quality":hd["quality"],"color":hd["color"],
        "hp":eff_hp,"atk":eff_atk,"crit":eff_crit,"base_hp":hd["hp"],"base_atk":hd["atk"],"base_crit":hd.get("crit",0),"spd":hd.get("spd",100),"skill_cost":hd.get("skill_cost",100),
        "skill_name":hd["skill_name"],"skill_desc":hd["skill_desc"],
        "skill_aoe":hd.get("skill_aoe",False),"skill_lv":inv["skill_lv"],
        "level":inv.get("level",1),"exp":inv.get("exp",0),"exp_next":inv.get("level",1)*100,
        "skill_upgrades":hd["skill_upgrades"],"passive_name":hd["passive_name"],"passive_desc":hd["passive_desc"],
        "passive_upgrades":hd["passive_upgrades"],"power":calc_hp(hero_id,inv["skill_lv"],inv["equipped"],inv),
        "basic_name":hd.get("basic_name","攻击"),"basic_desc":hd.get("basic_desc","普通攻击"),
        "basic_energy_gain":hd.get("basic_energy_gain",25),
        "equipped":eq,"bonds":bonds,"in_lineup":hero_id in g["lineup"]})

@app.route("/api/inventory_heroes")
@login_required
def api_inventory_heroes():
    g=load_game()
    if not g: return jsonify([])
    hh=[]
    for inv in g["inventory"]:
        hd=HERO_DATA.get(inv["hero_id"])
        if hd: hh.append({"hero_id":inv["hero_id"],"name":hd["name"],"class":hd["class"],"quality":hd["quality"],
            "color":hd["color"],"skill_lv":inv["skill_lv"],"level":inv.get("level",1),"power":calc_hp(inv["hero_id"],inv["skill_lv"],inv["equipped"],inv),
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
                "upgrade_lv":e.get("upgrade_lv",0) if isinstance(e,dict) else 0})
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
        equips.append({"id":eid,"name":ed["name"],"type":ed["type"],"quality":ed["quality"],"color":ed["color"],
            "atk":ed.get("atk",0),"hp":ed.get("hp",0),"crit":ed.get("crit",0),
            "desc":ed.get("desc",""),"special":ed.get("special",""),"exclusive":ed["exclusive"]})
    bonds=[{"id":b["id"],"name":b["name"],"members":[HERO_DATA.get(m,{}).get("name",m) for m in b["members"]],
        "desc":b["desc"],"effect":b["effect"]} for b in BONDS]
    return jsonify({"heroes":heroes,"equips":equips,"bonds":bonds})

def to_client(g):
    power=calc_pow(HERO_DATA,g["lineup"],g["inventory"],g["equip_bag"])
    sp=1200+g["stage_index"]*300; sp=max(50,min(sp,99999))
    sn=g.get("stage_name") or gen_stage_name(g["stage_index"])
    pi={"count":g["pity_counter"],"next_guaranteed":10-g["pity_counter"]}
    bl=[{"name":b["name"],"desc":b["desc"]} for b in get_bonds(g["lineup"])]
    return {"jade":g["jade"],"pull_count":g["pull_count"],"pity":pi,"power":power,
        "stage":{"name":sn,"power":sp,"index":g["stage_index"]},
        "lineup":g["lineup"],"lineup_count":len(g["lineup"]),
        "bonds":bl,"msg":g.get("msg",""),"ticks":g["ticks"],
        "inventory_count":len(g["inventory"]),"username":session.get("username",""),
        "offline_msg":g.get("offline_msg",""),"jade_per_min":round(max(0.3,power/5000),1)}

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

if __name__ == "__main__":
    port=int(sys.argv[1]) if len(sys.argv)>1 else 5002
    print(f"🏯 江湖经营 (速度轴+能量+卡牌版)"); print(f"   http://127.0.0.1:{port}")
    app.run(host="127.0.0.1",port=port,debug=False)
