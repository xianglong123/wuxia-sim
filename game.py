#!/usr/bin/env python3
"""🏯 江湖经营 - 英雄抽卡版（技能大改）"""
import json, urllib.request, ssl, random, os, sys, re, math, sqlite3
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
    uid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    g = new_game_inner()
    conn.execute("INSERT INTO game_saves (user_id, game_data) VALUES (?,?)", (uid, json.dumps(g, ensure_ascii=False)))
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
# 英雄数据（全新技能）
# ═══════════════════════════════════════════
HERO_DATA = {}
def reg(h): HERO_DATA[h["id"]] = h; return h

# 凡品
reg({"id":"lisi","name":"李四","class":"战士","quality":"凡品","color":"#888","hp":800,"atk":100,"crit":5,
    "skill_name":"乱砍","skill_desc":"对单个敌人造成80%伤害，30%概率降低目标攻击10%×2回合",
    "skill_aoe":False,"skill_target":"single","skill_debuffs":[{"stat":"atk","pct":-0.1,"dur":2,"chance":0.3}],
    "skill_upgrades":{3:"伤害提升至120%，削弱概率50%"},
    "passive_name":"蛮力","passive_desc":"攻击力+10%","passive_upgrades":{3:"攻击力+20%"}})
reg({"id":"wangdazhuang","name":"王大壮","class":"肉盾","quality":"凡品","color":"#888","hp":2000,"atk":60,"crit":2,
    "skill_name":"站住别跑","skill_desc":"嘲讽一个敌人2秒，自身减伤30%×2回合",
    "skill_aoe":False,"skill_target":"single","skill_special":["taunt"],"skill_buffs":[{"stat":"dmg_reduce","pct":0.3,"dur":2}],
    "skill_upgrades":{3:"嘲讽两个敌人，减伤50%"},
    "passive_name":"皮厚","passive_desc":"血量+15%","passive_upgrades":{3:"血量+25%"}})
reg({"id":"xiaocui","name":"小翠","class":"奶妈","quality":"凡品","color":"#888","hp":600,"atk":50,"crit":2,
    "skill_name":"包扎","skill_desc":"回复一个队友20%血量，增加防御15%×2回合",
    "skill_aoe":False,"skill_target":"lowest_hp_ally","skill_heal_pct":0.2,"skill_buffs":[{"stat":"dmg_reduce","pct":0.15,"dur":2}],
    "skill_upgrades":{3:"回复30%，防御加成25%"},
    "passive_name":"细心","passive_desc":"治疗量+10%","passive_upgrades":{3:"治疗量+20%"}})
reg({"id":"zhangtiezhu","name":"张铁柱","class":"射手","quality":"凡品","color":"#888","hp":700,"atk":120,"crit":8,
    "skill_name":"扔石头","skill_desc":"对单个敌人造成100%伤害，20%概率眩晕",
    "skill_aoe":False,"skill_target":"single","skill_special":["stun"],
    "skill_upgrades":{3:"伤害120%，眩晕概率35%"},
    "passive_name":"鹰眼","passive_desc":"命中率+10%","passive_upgrades":{3:"命中率+20%"}})

# 良品
reg({"id":"huangzhong","name":"黄忠老将","class":"射手","quality":"良品","color":"#5adb7a","hp":1000,"atk":220,"crit":15,
    "skill_name":"百步穿杨","skill_desc":"狙击敌方后排，造成200%伤害，必定暴击",
    "skill_aoe":False,"skill_target":"back_row","skill_special":["guaranteed_crit"],
    "skill_upgrades":{3:"伤害250%，爆伤翻倍",5:"攻击后排全体"},
    "passive_name":"老当益壮","passive_desc":"高于50%血量时攻击+20%","passive_upgrades":{3:"攻击+35%",5:"触发条件降至30%"}})
reg({"id":"guojia","name":"郭奉孝","class":"法师","quality":"良品","color":"#5adb7a","hp":800,"atk":280,"crit":12,
    "skill_name":"冰霜术","skill_desc":"对全体敌人造成80%伤害，40%概率冰冻1回合",
    "skill_aoe":True,"skill_target":"all_enemy","skill_special":["freeze"],
    "skill_upgrades":{3:"冰冻概率60%，伤害120%",5:"冰冻持续2回合"},
    "passive_name":"奇谋","passive_desc":"战斗开始回复全体15%血量",
    "passive_upgrades":{3:"回复25%",5:"额外增加10%攻击buff"}})
reg({"id":"yanshisan","name":"燕十三","class":"刺客","quality":"良品","color":"#5adb7a","hp":500,"atk":350,"crit":35,
    "skill_name":"背刺","skill_desc":"对血量最低敌人造成250%伤害，击杀后刷新技能",
    "skill_aoe":False,"skill_target":"lowest_hp","skill_special":["refresh_on_kill"],
    "skill_upgrades":{3:"伤害350%",5:"击杀后攻击+20%×2回合"},
    "passive_name":"影步","passive_desc":"闪避率+15%","passive_upgrades":{3:"闪避+25%",5:"闪避后回血10%"}})
reg({"id":"zhoucang","name":"周仓","class":"肉盾","quality":"良品","color":"#5adb7a","hp":2800,"atk":80,"crit":3,
    "skill_name":"护卫","skill_desc":"为最低血量队友承担50%伤害×3秒，自身减伤20%",
    "skill_aoe":False,"skill_target":"lowest_hp_ally","skill_special":["protect"],
    "skill_upgrades":{3:"承伤降低40%持续4秒",5:"保护期间自身回血10%"},
    "passive_name":"忠勇","passive_desc":"保护队友时自身回复5%","passive_upgrades":{3:"回复10%",5:"回复全体5%"}})

# 极品
reg({"id":"xiahoudun","name":"夏侯惇","class":"战士","quality":"极品","color":"#4a8eff","hp":2800,"atk":340,"crit":15,
    "skill_name":"拔矢啖睛","skill_desc":"自损10%血量，对全体敌人造成血量×6伤害，50%吸血",
    "skill_aoe":True,"skill_target":"all_enemy","skill_special":["lifesteal"],
    "skill_upgrades":{3:"伤害系数×10，自损降至5%",5:"获得护盾(吸收30%最大血量)"},
    "passive_name":"刚烈","passive_desc":"受到暴击时反弹150%伤害","passive_upgrades":{3:"反弹200%",5:"任何攻击30%反弹"}})
reg({"id":"caiwenji","name":"蔡文姬","class":"奶妈","quality":"极品","color":"#4a8eff","hp":1500,"atk":180,"crit":8,
    "skill_name":"胡笳十八拍","skill_desc":"全体回复20%+驱散所有负面+攻击+20%×2回合",
    "skill_aoe":True,"skill_target":"all_ally","skill_heal_pct":0.2,"skill_special":["cleanse"],"skill_buffs":[{"stat":"atk","pct":0.2,"dur":2}],
    "skill_upgrades":{3:"额外获得30%护盾",5:"攻击加成提升至35%"},
    "passive_name":"悲歌","passive_desc":"队友死亡时全体回复25%","passive_upgrades":{3:"回复40%",5:"触发时自身无敌2秒"}})
reg({"id":"ganning","name":"甘宁","class":"刺客","quality":"极品","color":"#4a8eff","hp":900,"atk":400,"crit":40,
    "skill_name":"锦帆夜袭","skill_desc":"突袭敌方后排全体，造成180%伤害，暴击时击晕",
    "skill_aoe":True,"skill_target":"back_row","skill_special":["stun_on_crit"],
    "skill_upgrades":{3:"伤害250%，击杀后额外行动",5:"必定暴击，伤害350%"},
    "passive_name":"铃铛","passive_desc":"战斗开始降低敌方全体10%攻击",
    "passive_upgrades":{3:"降低20%",5:"额外降低5%暴击"}})
reg({"id":"dianwei","name":"典韦","class":"肉盾","quality":"极品","color":"#4a8eff","hp":4000,"atk":200,"crit":8,
    "skill_name":"古之恶来","skill_desc":"狂暴:攻击+60%+吸血40%+反弹50%×3回合",
    "skill_aoe":False,"skill_target":"self","skill_special":["lifesteal","reflect"],"skill_buffs":[{"stat":"atk","pct":0.6,"dur":3}],
    "skill_upgrades":{3:"狂暴期间免疫控制",5:"结束时对全体造成200%伤害"},
    "passive_name":"死战","passive_desc":"血量低于20%时攻击翻倍","passive_upgrades":{3:"触发阈值30%",5:"血量低于20%无敌2秒"}})

# 绝品
reg({"id":"yangyouji","name":"养由基","class":"射手","quality":"绝品","color":"#b84aff","hp":1800,"atk":520,"crit":30,
    "skill_name":"穿云箭","skill_desc":"穿透全体敌人造成200%伤害，无视护盾，暴击伤害翻倍",
    "skill_aoe":True,"skill_target":"all_enemy","skill_special":["ignore_shield"],
    "skill_upgrades":{3:"穿透后暴击率+30%",5:"暴击时4倍伤害",7:"一箭双雕:攻击两次"},
    "passive_name":"百发百中","passive_desc":"无视闪避，暴击率+15%","passive_upgrades":{3:"暴击率+25%",5:"爆伤+50%",7:"每暴击一次攻击+5%"}})
reg({"id":"luobu","name":"吕布","class":"战士","quality":"绝品","color":"#b84aff","hp":3500,"atk":480,"crit":18,
    "skill_name":"方天画戟","skill_desc":"横扫前排全体造成180%伤害，降低目标攻击20%×2回合",
    "skill_aoe":True,"skill_target":"front_row","skill_debuffs":[{"stat":"atk","pct":-0.2,"dur":2}],
    "skill_upgrades":{3:"击退附带眩晕1回合",5:"只命中一个敌人时伤害翻倍",7:"技能范围扩大至全体"},
    "passive_name":"无双","passive_desc":"每击败一个敌人攻击+20%(最多3层)",
    "passive_upgrades":{3:"每层+25%最多4层",5:"每层额外+10%暴击",7:"满层技能无冷却"}})
reg({"id":"zhugeliang","name":"诸葛亮","class":"法师","quality":"绝品","color":"#b84aff","hp":2200,"atk":420,"crit":20,
    "skill_name":"借东风","skill_desc":"召唤暴风攻击全体敌人，造成150%伤害+降低攻击20%×3回合",
    "skill_aoe":True,"skill_target":"all_enemy","skill_debuffs":[{"stat":"atk","pct":-0.2,"dur":3}],
    "skill_upgrades":{3:"暴风附带闪电:额外50%伤害",5:"降低攻击30%+减速",7:"暴风持续3回合叠加"},
    "passive_name":"空城计","passive_desc":"血量低于30%时隐身2秒",
    "passive_upgrades":{3:"隐身每秒回血5%",5:"隐身结束全队回血10%",7:"隐身技能加速2倍"}})
reg({"id":"pangtong","name":"庞统","class":"法师","quality":"绝品","color":"#b84aff","hp":1800,"atk":460,"crit":22,
    "skill_name":"连环计","skill_desc":"对全体敌人施加锁链造成120%伤害+灼烧(每回合15%×2回合)",
    "skill_aoe":True,"skill_target":"all_enemy","skill_special":["burn"],
    "skill_upgrades":{3:"灼烧期间无法治疗",5:"锁链爆炸额外150%伤害",7:"灼烧传播至新敌人"},
    "passive_name":"铁索连舟","passive_desc":"战斗开始锁住全体敌人2秒",
    "passive_upgrades":{3:"锁住3秒",5:"锁住期间受伤+30%",7:"解锁时造成200%伤害"}})

# 传说
reg({"id":"lihai","name":"李白","class":"战士","quality":"传说","color":"#ffd700","hp":2800,"atk":580,"crit":25,
    "skill_name":"青莲剑诀","skill_desc":"掷出佩剑化为漫天剑光，攻击全体敌人3次，每剑80%伤害",
    "skill_aoe":True,"skill_target":"all_enemy","skill_special":["multi_hit"],
    "skill_upgrades":{3:"第四剑追击+暴击率+20%",5:"剑气纵横:每剑120%",7:"剑开天门:9999真实伤害必定暴击"},
    "passive_name":"斗酒诗百篇","passive_desc":"每击败一个敌人攻击+12%(最多5层)",
    "passive_upgrades":{3:"上限8层",5:"每层+20%",7:"满层技能必定暴击"}})
reg({"id":"zhangfei","name":"张飞","class":"肉盾","quality":"传说","color":"#ffd700","hp":4800,"atk":320,"crit":10,
    "skill_name":"当阳怒吼","skill_desc":"全屏嘲讽全体敌人3回合，全体队友获得护盾(吸收25%最大血量)",
    "skill_aoe":True,"skill_target":"all_enemy","skill_special":["taunt","shield_ally"],
    "skill_upgrades":{3:"怒吼降低敌人攻击25%",5:"护盾破碎爆炸",7:"全体队友无敌1回合"},
    "passive_name":"万人敌","passive_desc":"每受一次攻击+5%攻击(最多10层)",
    "passive_upgrades":{3:"上限15层",5:"每层额外+5%减伤",7:"满层反击100%"}})
reg({"id":"diaochan","name":"貂蝉","class":"刺客","quality":"传说","color":"#ffd700","hp":1800,"atk":650,"crit":45,
    "skill_name":"闭月之舞","skill_desc":"闪避攻击后瞬移至后排连刺，对全体敌人造成150%伤害，必定暴击",
    "skill_aoe":True,"skill_target":"all_enemy","skill_special":["guaranteed_crit"],
    "skill_upgrades":{3:"击杀后刷新闪避",5:"刺击附加灼烧每回合15%×2",7:"溅射周围50%伤害"},
    "passive_name":"离间","passive_desc":"战斗开始魅惑一个敌人3秒",
    "passive_upgrades":{3:"被魅惑敌人受伤+30%",5:"魅惑结束眩晕2秒",7:"魅惑两个敌人"}})
reg({"id":"huatuo","name":"华佗","class":"奶妈","quality":"传说","color":"#ffd700","hp":2200,"atk":220,"crit":8,
    "skill_name":"麻沸散","skill_desc":"全体回复30%+免疫伤害2回合+攻击+30%×2回合",
    "skill_aoe":True,"skill_target":"all_ally","skill_heal_pct":0.3,"skill_special":["immunity"],"skill_buffs":[{"stat":"atk","pct":0.3,"dur":2}],
    "skill_upgrades":{3:"免疫期间暴击率+20%",5:"回复40%+附加护盾",7:"免疫结束重置所有冷却"},
    "passive_name":"妙手回春","passive_desc":"队友低于30%自动回复15%(每场2次)",
    "passive_upgrades":{3:"触发次数+1",5:"回复30%",7:"触发时全队驱散"}})
reg({"id":"zhaoyun","name":"赵云","class":"战士","quality":"传说","color":"#ffd700","hp":3200,"atk":420,"crit":22,
    "skill_name":"七进七出","skill_desc":"冲入敌阵连续冲锋7次，每次对全体敌人造成30%伤害+10%吸血",
    "skill_aoe":True,"skill_target":"all_enemy","skill_special":["multi_hit","lifesteal"],
    "skill_upgrades":{3:"冲锋吸血20%",5:"优先攻击后排",7:"终结一击:全体500%伤害"},
    "passive_name":"一身是胆","passive_desc":"每损失10%血量攻击+8%",
    "passive_upgrades":{3:"每损失10%额外+5%暴击",5:"低于30%无敌1秒",7:"损失血量加成翻倍"}})
reg({"id":"guanyu","name":"关羽","class":"战士","quality":"传说","color":"#ffd700","hp":3500,"atk":500,"crit":28,
    "skill_name":"青龙偃月","skill_desc":"蓄力后挥出惊世一刀，对全体敌人造成300%伤害",
    "skill_aoe":True,"skill_target":"all_enemy","skill_special":["execute"],
    "skill_upgrades":{3:"蓄力期间免疫控制",5:"刀气留痕每秒20%×3回合",7:"血量低于50%的敌人直接斩杀"},
    "passive_name":"武圣","passive_desc":"开局第一刀必定暴击伤害+50%",
    "passive_upgrades":{3:"第一刀伤害翻倍",5:"前三刀必定暴击",7:"武圣降临:第一次技能真实伤害"}})

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

def gen_stage(power, si):
    dm = 0.45 + si * 0.015; dm = min(dm, 2.0)
    ep = max(50, int(power * dm))
    if si < 3: pl=["wood_sword","cloth_armor","straw_sandal"]; jb=5; ec=0.2
    elif si < 8: pl=["iron_sword","chain_armor","bronze_mirror"]; jb=10; ec=0.3
    elif si < 15: pl=["longquan_sword","mingguang_armor","jade_pendant"]; jb=18; ec=0.4
    elif si < 25: pl=["halberd","qilin_armor","pojun_bow","bagua_mirror"]; jb=30; ec=0.5
    else: pl=["qinglian_sword","zhangba_spear","qinglong_blade","chitu","heshi_bi"]; jb=45; ec=0.55
    return {"id":f"s{si}","name":gen_stage_name(si),"power":ep,"drops":{"jade":jb,"equip_chance":min(ec+si*0.005,0.7),"equip_pool":pl}}

# ═══ 游戏状态 ═══
def new_game_inner():
    inv=[{"hero_id":sid,"skill_lv":1,"equipped":{"武器":None,"防具":None,"饰品":None},"active":True} for sid in ["lisi","wangdazhuang","xiaocui"]]
    return {"jade":10,"inventory":inv,"equip_bag":[{"id":"wood_sword","count":1}],"lineup":["lisi","wangdazhuang","xiaocui"],
            "pull_count":0,"pity_counter":0,"stage_index":0,"ticks":0,"msg":"☯ 欢迎来到江湖！",
            "selected_hero":None,"game_over":False,"won":False,"last_active_time":datetime.now().timestamp(),"offline_msg":""}

def calc_pow(all_data, lineup, inventory, equip_bag):
    t=0
    for hid in lineup:
        inv=next((i for i in inventory if i["hero_id"]==hid and i["active"]),None)
        if not inv: continue
        hd=all_data.get(hid)
        if not hd: continue
        p=hd["hp"]+hd["atk"]*2
        for s,eq in inv["equipped"].items():
            if eq and eq in EQUIP_DATA: e=EQUIP_DATA[eq]; p+=e["atk"]*2+e["hp"]
        p*=(1+(inv["skill_lv"]-1)*0.1); t+=p
    return int(t)

def calc_hp(hid, sl, eq):
    hd=HERO_DATA.get(hid)
    if not hd: return 0
    p=hd["hp"]+hd["atk"]*2
    for s,eid in eq.items():
        if eid and eid in EQUIP_DATA: e=EQUIP_DATA[eid]; p+=e.get("atk",0)*2+e.get("hp",0)
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
    return {"hero_id":hid,"quality":q,"skill_lv":1,"equipped":{"武器":None,"防具":None,"饰品":None},"active":False}

# ═══════════════════════════════════════════
# 战斗引擎
# ═══════════════════════════════════════════

def gen_enemy(name, ps):
    c=random.choice(ECS)
    hp=int(ps*random.uniform(2.0, 3.5)); atk=int(ps*random.uniform(0.08,0.15))
    q=random.choices(["凡品","良品","极品","绝品","传说"],weights=[30,30,25,12,3])[0]
    return {"name":name,"class":c,"quality":q,"color":RARITY_COLORS.get(q,"#888"),
            "hp":hp,"max_hp":hp,"atk":atk,"crit":random.randint(5,30),
            "alive":True,"shield":0,"buffs":[],"debuffs":[],"stunned":False,"frozen":False,"reflect":False}

def h2f(hid, inv):
    hd=HERO_DATA.get(hid)
    if not hd: return None
    hp=hd["hp"]; atk=hd["atk"]
    for s,eq in inv["equipped"].items():
        if eq and eq in EQUIP_DATA: e=EQUIP_DATA[eq]; hp+=e.get("hp",0); atk+=e.get("atk",0)
    return {"id":hid,"name":hd["name"],"class":hd["class"],"quality":hd["quality"],"color":hd["color"],
            "hp":hp,"max_hp":hp,"atk":atk,"crit":hd["crit"],"skill_name":hd["skill_name"],
            "alive":True,"shield":0,"buffs":[],"debuffs":[],"stunned":False,"frozen":False,"reflect":False,"_hd":hd}

def dmg(t, raw):
    sh=t.get("shield",0); sd=min(sh,raw); ad=raw-sd; t["shield"]=sh-sd; t["hp"]-=ad
    return {"damage":ad,"shield_damage":sd}

def tick_b(units):
    for u in units:
        u["buffs"]=[b for b in u.get("buffs",[]) if b["dur"]>1]; [b.update({"dur":b["dur"]-1}) for b in u.get("buffs",[]) if b["dur"]>1]
        u["debuffs"]=[d for d in u.get("debuffs",[]) if d["dur"]>1]; [d.update({"dur":d["dur"]-1}) for d in u.get("debuffs",[]) if d["dur"]>1]
        u["stunned"]=False; u["frozen"]=False

def cstat(u, stat, base):
    v=base
    for b in u.get("buffs",[]):
        if b["stat"]==stat: v*=(1+b["pct"])
    for d in u.get("debuffs",[]):
        if d["stat"]==stat: v*=(1+d["pct"])
    return int(v)

def run_battle(g, stage):
    my_h=[]
    for hid in g["lineup"]:
        inv=next((i for i in g["inventory"] if i["hero_id"]==hid),None)
        if not inv: continue
        f=h2f(hid,inv)
        if f: my_h.append(f)
    ec=max(1,min(len(my_h)+random.randint(-1,1),5)); pp=stage["power"]/max(1,ec)
    ene=[]; un=set()
    for _ in range(ec):
        n=random.choice([x for x in ENEMY_NAMES if x not in un] or ENEMY_NAMES); un.add(n)
        ene.append(gen_enemy(n,pp))
    bonds=get_bonds(g["lineup"]); turns=[]; max_r=30
    alive_h=[f for f in my_h]; alive_e=[e for e in ene]
    for ri in range(max_r):
        if not alive_h or not alive_e: break
        rt=[]; tick_b(alive_h+alive_e)
        # 我方
        for at in list(alive_h):
            if not at.get("alive",True) or at.get("stunned") or at.get("frozen"): continue
            hd=at.get("_hd"); aoe=hd.get("skill_aoe",False) if hd else False; tgt="all_enemy"
            tars=[]
            if aoe: tars=list(alive_e)
            elif tgt=="back_row": tars=alive_e[-max(1,len(alive_e)//2):]
            elif tgt=="front_row": tars=alive_e[:max(1,len(alive_e)//2)]
            elif tgt=="lowest_hp": tars=[min(alive_e,key=lambda x:x["hp"])] if alive_e else []
            else: tars=[random.choice(alive_e)] if alive_e else []
            for t in tars:
                if not t.get("alive",True): continue
                ba=cstat(at,"atk",at["atk"]); d=int(ba*random.uniform(0.6,1.0))
                sl=next((i["skill_lv"] for i in g["inventory"] if i["hero_id"]==at.get("id")),1)
                d=int(d*(1+(sl-1)*0.15))
                cr=random.random()<at["crit"]/100
                if cr: d=int(d*1.5)
                dr=cstat(t,"dmg_reduce",0); d=int(d*(1-dr))
                r=dmg(t,d); ad=r["damage"]+r["shield_damage"]
                killed=t["hp"]<=0
                if killed: t["alive"]=False
                ref=0
                if t.get("reflect") and ad>0: ref=int(ad*0.5); dmg(at,ref)
                ls=0
                if hd and "lifesteal" in hd.get("skill_special",[]): ls=int(ad*0.3); at["hp"]=min(at["max_hp"],at["hp"]+ls)
                ai=my_h.index(at) if at in my_h else 0
                ti=ene.index(t) if t in ene else 0
                rt.append({"side":"ally","attacker_name":at["name"],"attacker_class":at["class"],
                    "attacker_color":at["color"],"attacker_idx":ai,
                    "skill":hd["skill_name"] if hd else "攻击","aoe":aoe,
                    "target_name":t["name"],"target_class":t["class"],"target_color":t["color"],"target_idx":ti,
                    "damage":ad,"crit":cr,"killed":killed,"heal":0,"reflect_dmg":ref,"lifesteal":ls,
                    "target_hp_pct":max(0,t["hp"]/max(1,t["max_hp"]))})
            if hd:
                for b in hd.get("skill_buffs",[]): at["buffs"].append({"stat":b["stat"],"pct":b["pct"],"dur":b["dur"]})
                for d_ in hd.get("skill_debuffs",[]):
                    for t in tars:
                        if random.random()<(d_.get("chance",1.0)): t["debuffs"].append({"stat":d_["stat"],"pct":d_["pct"],"dur":d_["dur"]})
        # 敌方
        for at in list(alive_e):
            if not at.get("alive",True): continue
            t=random.choice(alive_h) if alive_h else None
            if not t: continue
            ba=cstat(at,"atk",at["atk"]); d=int(ba*random.uniform(0.4,0.8))
            cr=random.random()<at["crit"]/100
            if cr: d=int(d*1.5)
            dr=cstat(t,"dmg_reduce",0); d=int(d*(1-dr))
            r=dmg(t,d); ad=r["damage"]+r["shield_damage"]
            killed=t["hp"]<=0
            if killed: t["alive"]=False
            ref=0
            if t.get("reflect") and ad>0: ref=int(ad*0.5); dmg(at,ref)
            ti=my_h.index(t) if t in my_h else 0
            ai=ene.index(at) if at in ene else 0
            rt.append({"side":"enemy","attacker_name":at["name"],"attacker_class":at["class"],
                "attacker_color":at["color"],"attacker_idx":ai,
                "skill":"攻击","aoe":False,
                "target_name":t["name"],"target_class":t["class"],"target_color":t["color"],"target_idx":ti,
                "damage":ad,"crit":cr,"killed":killed,"heal":0,"reflect_dmg":ref,"lifesteal":0,
                "target_hp_pct":max(0,t["hp"]/max(1,t["max_hp"]))})
        # 治疗
        for f in alive_h:
            if not f.get("alive",True): continue
            hd=f.get("_hd")
            if hd and hd["class"]=="奶妈" and hd.get("skill_heal_pct",0) and random.random()<0.6:
                hpct=hd["skill_heal_pct"]
                if hd.get("skill_target")=="all_ally":
                    tars=[x for x in alive_h if x.get("alive",True)]
                else:
                    wk=min([x for x in alive_h if x.get("alive",True)],key=lambda x:x["hp"]/max(1,x["max_hp"]))
                    tars=[wk]
                for t in tars:
                    heal=int(t["max_hp"]*hpct)
                    t["hp"]=min(t["max_hp"],t["hp"]+heal)
                    ti=my_h.index(t) if t in my_h else 0; ai=my_h.index(f) if f in my_h else 0
                    rt.append({"side":"heal","attacker_name":f["name"],"attacker_class":f["class"],
                        "attacker_color":f["color"],"attacker_idx":ai,
                        "skill":"回春术","aoe":len(tars)>1,
                        "target_name":t["name"],"target_class":t["class"],"target_color":t["color"],"target_idx":ti,
                        "damage":0,"crit":False,"killed":False,"heal":heal,"reflect_dmg":0,"lifesteal":0,
                        "target_hp_pct":t["hp"]/max(1,t["max_hp"])})
        if rt: turns.append({"round":ri+1,"actions":rt})
        alive_h=[f for f in my_h if f.get("alive",True)]
        alive_e=[e for e in ene if e.get("alive",True)]
    win=len(alive_h)>0 and len(alive_e)==0
    r={"win":win,"rounds":len(turns),"turns":turns}
    r["my_heroes"]=[{"name":f["name"],"class":f["class"],"quality":f["quality"],"color":f["color"],
        "max_hp":f["max_hp"],"atk":f["atk"],"hp_pct":max(0,f["hp"]/max(1,f["max_hp"])),"alive":f.get("alive",True),
        "shield_pct":f.get("shield",0)/max(1,f["max_hp"]),
        "buffs":[b["stat"] for b in f.get("buffs",[])],
        "debuffs":[d["stat"] for d in f.get("debuffs",[])]} for f in my_h]
    r["enemies"]=[{"name":e["name"],"class":e["class"],"quality":e["quality"],"color":e["color"],
        "max_hp":e["max_hp"],"atk":e["atk"],"hp_pct":max(0,e["hp"]/max(1,e["max_hp"])),"alive":e.get("alive",True),
        "buffs":[b["stat"] for b in e.get("buffs",[])],
        "debuffs":[d["stat"] for d in e.get("debuffs",[])]} for e in ene]
    r["bonds"]=[{"name":b["name"],"desc":b["desc"]} for b in bonds]
    if win:
        dr=stage["drops"]; jr=dr["jade"]+random.randint(-3,8); jr=max(3,jr)
        g["jade"]+=jr; r["jade_reward"]=jr; r["equip_reward"]=None
        if random.random()<dr["equip_chance"] and dr["equip_pool"]:
            eid=random.choice(dr["equip_pool"])
            ex=next((e for e in g["equip_bag"] if isinstance(e,dict) and e["id"]==eid),None)
            if ex: ex["count"]+=1
            else: g["equip_bag"].append({"id":eid,"count":1})
            eq=EQUIP_DATA.get(eid)
            if eq: r["equip_reward"]={"name":eq["name"],"quality":eq["quality"],"color":eq["color"],"type":eq["type"],"atk":eq.get("atk",0),"hp":eq.get("hp",0)}
        g["stage_index"]+=1; r["new_stage"]=gen_stage_name(g["stage_index"])
    else:
        g["jade"]+=max(2,stage["drops"]["jade"]//4); r["jade_reward"]=max(2,stage["drops"]["jade"]//4); r["failed"]=True
    g["stage_name"]=stage["name"]; g["battle_result"]=r
    return r

# ═══ API ═══
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
        power=calc_pow(HERO_DATA,g["lineup"],g["inventory"],g["equip_bag"])
        stage=gen_stage(power,g["stage_index"])
        result=run_battle(g,stage); g["ticks"]+=1; tj+=result.get("jade_reward",0)
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
    if ex: ex["skill_lv"]=min(7,ex["skill_lv"]+1); g["msg"]=f"🎴 抽到{card['quality']}{HERO_DATA[card['hero_id']]['name']}！技能升级至Lv.{ex['skill_lv']}"
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
        if ex: ex["skill_lv"]=min(7,ex["skill_lv"]+1)
        else: g["inventory"].append(card)
        cards.append(card)
    g["msg"]=f"🎴 十连最高{bq}"; save_game(g)
    r=to_client(g); r["pull"]=[{"hero_id":c["hero_id"],"quality":c["quality"],"hero_name":HERO_DATA[c["hero_id"]]["name"],"hero_class":HERO_DATA[c["hero_id"]]["class"]} for c in cards]; r["is_10_pull"]=True
    return jsonify(r)

@app.route("/api/lineup/<hero_id>")
@login_required
def api_lineup_toggle(hero_id):
    g=load_game()
    if not g: return api_new()
    if hero_id in g["lineup"]: g["lineup"].remove(hero_id)
    elif len(g["lineup"])>=6: return jsonify({"error":"阵容最多6人",**to_client(g)})
    else: g["lineup"].append(hero_id)
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

@app.route("/api/battle")
@login_required
def api_battle():
    g=load_game()
    if not g: return api_new()
    if not g["lineup"]: return jsonify({"error":"请先上阵英雄",**to_client(g)})
    power=calc_pow(HERO_DATA,g["lineup"],g["inventory"],g["equip_bag"])
    stage=gen_stage(power,g["stage_index"])
    result=run_battle(g,stage); g["ticks"]+=1; save_game(g)
    r=to_client(g); r["battle_result"]=result
    return jsonify(r)

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
    return jsonify({"hero_id":hero_id,"name":hd["name"],"class":hd["class"],"quality":hd["quality"],"color":hd["color"],
        "hp":hd["hp"],"atk":hd["atk"],"crit":hd["crit"],"skill_name":hd["skill_name"],"skill_desc":hd["skill_desc"],
        "skill_aoe":hd.get("skill_aoe",False),"skill_lv":inv["skill_lv"],
        "skill_upgrades":hd["skill_upgrades"],"passive_name":hd["passive_name"],"passive_desc":hd["passive_desc"],
        "passive_upgrades":hd["passive_upgrades"],"power":calc_hp(hero_id,inv["skill_lv"],inv["equipped"]),
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
            "color":hd["color"],"skill_lv":inv["skill_lv"],"power":calc_hp(inv["hero_id"],inv["skill_lv"],inv["equipped"]),
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
                "color":ed["color"],"atk":ed.get("atk",0),"hp":ed.get("hp",0),"crit":ed.get("crit",0),"special":ed.get("special",""),"count":cnt})
    items.sort(key=lambda x:(EQ_QUALITY_ORDER.get(x["quality"],0),x["atk"]),reverse=True)
    return jsonify(items)

def to_client(g):
    power=calc_pow(HERO_DATA,g["lineup"],g["inventory"],g["equip_bag"])
    sp=int(power*(0.45+g["stage_index"]*0.015)); sp=max(50,min(sp,99999))
    sn=g.get("stage_name") or gen_stage_name(g["stage_index"])
    pi={"count":g["pity_counter"],"next_guaranteed":10-g["pity_counter"]}
    bl=[{"name":b["name"],"desc":b["desc"]} for b in get_bonds(g["lineup"])]
    return {"jade":g["jade"],"pull_count":g["pull_count"],"pity":pi,"power":power,
        "stage":{"name":sn,"power":sp,"index":g["stage_index"]},
        "lineup":g["lineup"],"lineup_count":len(g["lineup"]),
        "bonds":bl,"msg":g.get("msg",""),"ticks":g["ticks"],
        "inventory_count":len(g["inventory"]),"username":session.get("username",""),
        "offline_msg":g.get("offline_msg",""),"jade_per_min":round(max(0.3,power/5000),1)}

if __name__ == "__main__":
    port=int(sys.argv[1]) if len(sys.argv)>1 else 5002
    print(f"🏯 江湖经营 (技能大改版)"); print(f"   http://127.0.0.1:{port}")
    app.run(host="127.0.0.1",port=port,debug=False)
