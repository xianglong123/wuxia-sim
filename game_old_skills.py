#!/usr/bin/env python3
"""🏯 江湖经营 - 英雄抽卡版（用户系统 + AI无限关卡）"""
import json, urllib.request, ssl, random, os, sys, re, math, sqlite3
from datetime import datetime
from pathlib import Path
from functools import wraps
from flask import Flask, render_template, jsonify, session, redirect, request
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "wuxia-hero-2026-online"

# ── SSL / DeepSeek ──
try:
    import certifi
    SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except: SSL_CTX = ssl.create_default_context()

def get_api_key():
    f = Path.home() / ".hermes" / ".env"
    if f.exists():
        for l in open(f):
            if l.startswith("DEEPSEEK_API_KEY="):
                return l.split("=",1)[1].strip()
    return os.environ.get("DEEPSEEK_API_KEY", "")
API_KEY = get_api_key()

def call_deepseek(messages, temp=0.7, max_tokens=500):
    if not API_KEY: return None
    payload = json.dumps({
        "model": "deepseek-chat", "messages": messages,
        "temperature": temp, "max_tokens": max_tokens
    }).encode()
    req = urllib.request.Request(
        url="https://api.deepseek.com/v1/chat/completions", data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {API_KEY}"},
        method="POST")
    try:
        with urllib.request.urlopen(req, context=SSL_CTX, timeout=15) as r:
            return json.loads(r.read())["choices"][0]["message"]["content"]
    except: return None

# ═══════════════════════════════════════════
# 数据库
# ═══════════════════════════════════════════

DB_PATH = Path(__file__).parent / "data" / "wuxia.db"

def get_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS game_saves (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER UNIQUE NOT NULL,
        game_data TEXT NOT NULL,
        updated_at TEXT DEFAULT (datetime('now','localtime')),
        FOREIGN KEY (user_id) REFERENCES users(id)
    )""")
    return conn

# ── Auth 装饰器 ──

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            if request.path.startswith("/api/"):
                return jsonify({"error":"未登录","need_login":True})
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated

# ═══════════════════════════════════════════
# 用户路由
# ═══════════════════════════════════════════

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
    existing = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
    if existing:
        conn.close()
        return jsonify({"error":"用户名已存在"})
    pw_hash = generate_password_hash(password)
    conn.execute("INSERT INTO users (username, password_hash) VALUES (?,?)", (username, pw_hash))
    user_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    # 创建初始存档
    game_data = json.dumps(new_game_inner(), ensure_ascii=False)
    conn.execute("INSERT INTO game_saves (user_id, game_data) VALUES (?,?)", (user_id, game_data))
    conn.commit()
    conn.close()
    session["user_id"] = user_id
    session["username"] = username
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
    session["user_id"] = user["id"]
    session["username"] = user["username"]
    return jsonify({"ok":True, "username":user["username"]})

@app.route("/api/logout")
def api_logout():
    session.clear()
    return jsonify({"ok":True})

@app.route("/api/me")
def api_me():
    if "user_id" in session:
        return jsonify({"logged_in":True, "username":session.get("username","")})
    return jsonify({"logged_in":False})

# ═══════════════════════════════════════════
# 存档读写
# ═══════════════════════════════════════════

def load_game():
    user_id = session.get("user_id")
    if not user_id: return None
    conn = get_db()
    row = conn.execute("SELECT game_data FROM game_saves WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    if row:
        g = json.loads(row["game_data"])
        # 兼容旧存档
        g.setdefault("stage_index", 0)
        g.setdefault("msg", "")
        g.setdefault("battle_result", None)
        return g
    return None

def save_game(g):
    user_id = session.get("user_id")
    if not user_id: return
    conn = get_db()
    data = json.dumps(g, ensure_ascii=False)
    conn.execute("""INSERT INTO game_saves (user_id, game_data, updated_at)
        VALUES (?,?, datetime('now','localtime'))
        ON CONFLICT(user_id) DO UPDATE SET game_data=?, updated_at=datetime('now','localtime')""",
        (user_id, data, data))
    conn.commit()
    conn.close()

# ═══════════════════════════════════════════
# 英雄数据（同前，略缩）
# ═══════════════════════════════════════════

HERO_DATA = {}
def reg(h):
    HERO_DATA[h["id"]] = h; return h

reg({"id":"lisi","name":"李四","title":"街头混混","class":"战士","quality":"凡品","color":"#888","hp":800,"atk":100,"crit":5,"skill_name":"乱砍","skill_desc":"对单个敌人造成80%攻击伤害","skill_upgrades":{3:"伤害提升至120%"},"passive_name":"蛮力","passive_desc":"攻击力+10%","passive_upgrades":{3:"攻击力+20%"}})
reg({"id":"wangdazhuang","name":"王大壮","title":"铁匠学徒","class":"肉盾","quality":"凡品","color":"#888","hp":2000,"atk":60,"crit":2,"skill_name":"站住别跑","skill_desc":"嘲讽一个敌人2秒","skill_upgrades":{3:"嘲讽两个敌人"},"passive_name":"皮厚","passive_desc":"血量+15%","passive_upgrades":{3:"血量+25%"}})
reg({"id":"xiaocui","name":"小翠","title":"采药女","class":"奶妈","quality":"凡品","color":"#888","hp":600,"atk":50,"crit":2,"skill_name":"包扎","skill_desc":"回复一个队友15%血量","skill_upgrades":{3:"回复量提升至25%"},"passive_name":"细心","passive_desc":"治疗量+10%","passive_upgrades":{3:"治疗量+20%"}})
reg({"id":"zhangtiezhu","name":"张铁柱","title":"猎户","class":"射手","quality":"凡品","color":"#888","hp":700,"atk":120,"crit":8,"skill_name":"扔石头","skill_desc":"对单个敌人造成100%攻击伤害","skill_upgrades":{3:"有20%概率眩晕1秒"},"passive_name":"鹰眼","passive_desc":"命中率+10%","passive_upgrades":{3:"命中率+20%"}})
reg({"id":"huangzhong","name":"黄忠老将","title":"百步穿杨","class":"射手","quality":"良品","color":"#5adb7a","hp":1000,"atk":220,"crit":15,"skill_name":"百步穿杨","skill_desc":"对后排敌人造成150%攻击伤害","skill_upgrades":{3:"伤害提升至200%，必中",5:"暴击率+20%"},"passive_name":"老当益壮","passive_desc":"血量高于50%时攻击+20%","passive_upgrades":{3:"攻击加成提升至35%",5:"血量高于30%即可触发"}})
reg({"id":"guojia","name":"郭奉孝","title":"鬼才","class":"法师","quality":"良品","color":"#5adb7a","hp":800,"atk":280,"crit":12,"skill_name":"冰霜术","skill_desc":"对全体敌人造成80%伤害，减速20%","skill_upgrades":{3:"减速效果提升至40%",5:"伤害提升至120%"},"passive_name":"奇谋","passive_desc":"战斗开始时回复全体15%血量","passive_upgrades":{3:"回复量提升至25%",5:"额外增加10%攻击buff"}})
reg({"id":"yanshisan","name":"燕十三","title":"影子刺客","class":"刺客","quality":"良品","color":"#5adb7a","hp":500,"atk":350,"crit":35,"skill_name":"背刺","skill_desc":"对血量最低的敌人造成200%伤害","skill_upgrades":{3:"击杀后刷新冷却",5:"伤害提升至300%"},"passive_name":"影步","passive_desc":"闪避率+15%","passive_upgrades":{3:"闪避率+25%",5:"闪避后回复10%血量"}})
reg({"id":"zhoucang","name":"周仓","title":"扛刀护卫","class":"肉盾","quality":"良品","color":"#5adb7a","hp":2800,"atk":80,"crit":3,"skill_name":"护卫","skill_desc":"为血量最低的队友承担50%伤害，持续3秒","skill_upgrades":{3:"承伤降低40%，持续4秒",5:"护卫期间自身减伤+20%"},"passive_name":"忠勇","passive_desc":"保护队友时自身回复5%血量","passive_upgrades":{3:"回复量提升至10%",5:"回复全体队友5%"}})
reg({"id":"xiahoudun","name":"夏侯惇","title":"独眼将军","class":"战士","quality":"极品","color":"#4a8eff","hp":2800,"atk":340,"crit":15,"skill_name":"拔矢啖睛","skill_desc":"损失10%当前血量，对敌人造成损失血量×5的伤害","skill_upgrades":{3:"伤害系数提升至×8",5:"使用后回复20%血量"},"passive_name":"刚烈","passive_desc":"受到暴击时反弹100%伤害给攻击者","passive_upgrades":{3:"反弹伤害提升至150%",5:"受到任何攻击都有20%反弹"}})
reg({"id":"caiwenji","name":"蔡文姬","title":"悲歌才女","class":"奶妈","quality":"极品","color":"#4a8eff","hp":1500,"atk":180,"crit":8,"skill_name":"胡笳十八拍","skill_desc":"弹奏古琴，全体回复18%血量，驱散一个负面效果","skill_upgrades":{3:"额外增加30%护盾",5:"驱散全部负面效果"},"passive_name":"悲歌","passive_desc":"队友死亡时，全体回复25%血量","passive_upgrades":{3:"回复量提升至40%",5:"触发时自身无敌2秒"}})
reg({"id":"ganning","name":"甘宁","title":"锦帆贼","class":"刺客","quality":"极品","color":"#4a8eff","hp":900,"atk":400,"crit":40,"skill_name":"锦帆夜袭","skill_desc":"突袭敌方后排，造成250%伤害，优先攻击法师","skill_upgrades":{3:"击杀后额外行动一次",5:"伤害提升至350%，必定暴击"},"passive_name":"铃铛","passive_desc":"战斗开始时，降低敌方全体10%攻击","passive_upgrades":{3:"降低攻击效果提升至20%",5:"额外降低5%暴击"}})
reg({"id":"dianwei","name":"典韦","title":"恶来","class":"肉盾","quality":"极品","color":"#4a8eff","hp":4000,"atk":200,"crit":8,"skill_name":"古之恶来","skill_desc":"进入狂暴状态，攻击+50%，吸血+30%，持续5秒","skill_upgrades":{3:"狂暴期间免疫控制",5:"狂暴结束时对周围造成200%伤害"},"passive_name":"死战","passive_desc":"血量低于20%时，攻击翻倍","passive_upgrades":{3:"触发阈值提升至30%",5:"血量低于20%时无敌2秒"}})
reg({"id":"yangyouji","name":"养由基","title":"神射","class":"射手","quality":"绝品","color":"#b84aff","hp":1800,"atk":520,"crit":30,"skill_name":"穿云箭","skill_desc":"蓄力一箭穿透敌人，造成250%伤害，无视护盾","skill_upgrades":{3:"穿透后攻击后排",5:"暴击时伤害翻倍(4倍)",7:"一箭双雕(攻击两个敌人)"},"passive_name":"百发百中","passive_desc":"攻击无视闪避，暴击率+15%","passive_upgrades":{3:"暴击率额外+10%",5:"暴击伤害+50%",7:"每暴击一次攻击+5%(可叠加)"}})
reg({"id":"luobu","name":"吕布","title":"飞将","class":"战士","quality":"绝品","color":"#b84aff","hp":3500,"atk":480,"crit":18,"skill_name":"方天画戟","skill_desc":"对前排敌人造成200%伤害+击退","skill_upgrades":{3:"击退附带眩晕1秒",5:"若只命中一个敌人伤害翻倍",7:"技能范围扩大至全体前排"},"passive_name":"无双","passive_desc":"每击败一个敌人攻击+20%(最多3层)","passive_upgrades":{3:"每层+25%，最多4层",5:"每层额外+10%暴击",7:"满层时技能无冷却"}})
reg({"id":"zhugeliang","name":"诸葛亮","title":"卧龙","class":"法师","quality":"绝品","color":"#b84aff","hp":2200,"atk":420,"crit":20,"skill_name":"借东风","skill_desc":"召唤暴风攻击全体敌人，造成120%伤害，降低速度30%","skill_upgrades":{3:"暴风附带雷电：额外50%伤害",5:"降低敌人攻击20%",7:"暴风持续3回合叠加"},"passive_name":"空城计","passive_desc":"血量低于30%时隐身2秒(不可被攻击)","passive_upgrades":{3:"隐身期间每秒回复5%",5:"隐身结束全队回复10%",7:"隐身时技能冷却加速2倍"}})
reg({"id":"pangtong","name":"庞统","title":"凤雏","class":"法师","quality":"绝品","color":"#b84aff","hp":1800,"atk":460,"crit":22,"skill_name":"连环计","skill_desc":"对全体敌人施加锁链，造成100%伤害，被锁者受到伤害+25%","skill_upgrades":{3:"锁链持续期间敌人无法治疗",5:"锁链爆炸造成额外150%伤害",7:"锁链传播至新上场的敌人"},"passive_name":"铁索连舟","passive_desc":"战斗开始时，锁住敌方全体2秒(无法行动)","passive_upgrades":{3:"锁住时间延长至3秒",5:"锁住期间敌人受到伤害+30%",7:"解锁时造成200%伤害"}})
reg({"id":"lihai","name":"李白","title":"谪仙人","class":"战士","quality":"传说","color":"#ffd700","hp":2800,"atk":580,"crit":25,"skill_name":"青莲剑诀","skill_desc":"掷出佩剑化为三道剑光，对前排造成3次伤害，每剑80%攻击","skill_upgrades":{3:"第四道剑光：额外追击一次",5:"剑气纵横：变为全体伤害",7:"剑开天门：9999真实伤害"},"passive_name":"斗酒诗百篇","passive_desc":"每击败一个敌人攻击+12%(最多5层)","passive_upgrades":{3:"上限提升至8层",5:"每层+20%",7:"满层时技能必定暴击"}})
reg({"id":"zhangfei","name":"张飞","title":"万人敌","class":"肉盾","quality":"传说","color":"#ffd700","hp":4800,"atk":320,"crit":10,"skill_name":"当阳怒吼","skill_desc":"嘲讽全体敌人3秒，获得护盾(吸收20%最大生命)","skill_upgrades":{3:"怒吼附带恐惧：敌人攻击-20%",5:"护盾破碎时爆炸造成护盾值伤害",7:"全体队友获得护盾"},"passive_name":"万人敌","passive_desc":"每受一次攻击攻击+5%(最多10层)","passive_upgrades":{3:"上限提升至15层",5:"每层额外+5%减伤",7:"满层时反击造成100%伤害"}})
reg({"id":"diaochan","name":"貂蝉","title":"闭月","class":"刺客","quality":"传说","color":"#ffd700","hp":1800,"atk":650,"crit":45,"skill_name":"闭月之舞","skill_desc":"闪避下一次攻击，瞬移到血量最低的敌人身边刺出致命一击，造成200%伤害(必定暴击)","skill_upgrades":{3:"击杀后刷新闪避效果",5:"刺击后留下毒药每2秒10%持续伤害",7:"对目标周围溅射50%伤害"},"passive_name":"离间","passive_desc":"战斗开始时魅惑一个敌人3秒(攻击队友)","passive_upgrades":{3:"被魅惑敌人受到伤害+30%",5:"魅惑结束时眩晕2秒",7:"魅惑两个敌人"}})
reg({"id":"huatuo","name":"华佗","title":"神医","class":"奶妈","quality":"传说","color":"#ffd700","hp":2200,"atk":220,"crit":8,"skill_name":"麻沸散","skill_desc":"回复血量最低队友25%血量，使其免疫伤害3秒","skill_upgrades":{3:"免疫期间攻击+30%",5:"回复全体20%血量",7:"免疫结束时重置所有冷却"},"passive_name":"妙手回春","passive_desc":"队友血量低于30%时自动回复15%(每场2次)","passive_upgrades":{3:"触发次数+1",5:"回复量提升至30%",7:"触发时全队驱散负面效果"}})
reg({"id":"zhaoyun","name":"赵云","title":"常胜将军","class":"战士","quality":"传说","color":"#ffd700","hp":3200,"atk":420,"crit":22,"skill_name":"七进七出","skill_desc":"冲向敌方后排连续冲锋7次，每次对随机敌人造成40%攻击伤害","skill_upgrades":{3:"每次冲锋附带10%吸血",5:"优先锁定奶妈/法师",7:"冲锋结束后追加500%终结一击"},"passive_name":"一身是胆","passive_desc":"每损失10%血量攻击+8%","passive_upgrades":{3:"每损失10%额外+5%暴击",5:"血量低于30%时无敌1秒",7:"损失血量加成翻倍"}})
reg({"id":"guanyu","name":"关羽","title":"武圣","class":"战士","quality":"传说","color":"#ffd700","hp":3500,"atk":500,"crit":28,"skill_name":"青龙偃月","skill_desc":"蓄力后挥出惊天一刀，对全体敌人造成250%伤害","skill_upgrades":{3:"蓄力期间免疫控制",5:"刀气留痕：每秒造成20%伤害×3秒",7:"一刀两断：血量低于50%的敌人直接斩杀"},"passive_name":"武圣","passive_desc":"战斗开始第一刀必定暴击，伤害+50%","passive_upgrades":{3:"第一刀伤害翻倍",5:"前三刀必定暴击",7:"武圣降临：第一次技能造成真实伤害"}})

HERO_IDS = list(HERO_DATA.keys())
QUALITY_ORDER = {"凡品":0,"良品":1,"极品":2,"绝品":3,"传说":4}
QUALITY_WEIGHTS = {"凡品":40,"良品":30,"极品":20,"绝品":8,"传说":2}
RARITY_COLORS = {"凡品":"#888","良品":"#5adb7a","极品":"#4a8eff","绝品":"#b84aff","传说":"#ffd700"}

# ═══════════════════════════════════════════
# 装备数据
# ═══════════════════════════════════════════

EQUIP_DATA = {}
def req(e):
    EQUIP_DATA[e["id"]] = e; return e
req({"id":"wood_sword","name":"新手木剑","type":"武器","quality":"凡品","color":"#888","atk":30,"hp":0,"crit":0,"desc":"就是一根削尖的木头","exclusive":None,"special":None})
req({"id":"cloth_armor","name":"布甲","type":"防具","quality":"凡品","color":"#888","atk":0,"hp":150,"crit":0,"desc":"粗布制成的简易护甲","exclusive":None,"special":None})
req({"id":"straw_sandal","name":"草鞋","type":"饰品","quality":"凡品","color":"#888","atk":0,"hp":0,"crit":2,"desc":"防滑耐磨","exclusive":None,"special":None})
req({"id":"iron_sword","name":"玄铁剑","type":"武器","quality":"良品","color":"#5adb7a","atk":80,"hp":0,"crit":0,"desc":"重剑无锋，大巧不工","exclusive":None,"special":None})
req({"id":"chain_armor","name":"锁子甲","type":"防具","quality":"良品","color":"#5adb7a","atk":0,"hp":350,"crit":0,"desc":"铁环编织而成","exclusive":None,"special":None})
req({"id":"bronze_mirror","name":"护心镜","type":"饰品","quality":"良品","color":"#5adb7a","atk":0,"hp":100,"crit":3,"desc":"护住要害","exclusive":None,"special":"受到暴击时减伤20%"})
req({"id":"longquan_sword","name":"龙泉剑","type":"武器","quality":"极品","color":"#4a8eff","atk":200,"hp":0,"crit":5,"desc":"削铁如泥","exclusive":None,"special":"暴击率+5%"})
req({"id":"mingguang_armor","name":"明光铠","type":"防具","quality":"极品","color":"#4a8eff","atk":0,"hp":600,"crit":0,"desc":"刀枪不入","exclusive":None,"special":"受到伤害-8%"})
req({"id":"jade_pendant","name":"玉佩","type":"饰品","quality":"极品","color":"#4a8eff","atk":0,"hp":200,"crit":5,"desc":"温润如玉","exclusive":None,"special":"战斗开始获得10%护盾"})
req({"id":"halberd","name":"方天画戟","type":"武器","quality":"绝品","color":"#b84aff","atk":450,"hp":0,"crit":8,"desc":"吕布兵器", "exclusive":"luobu","special":"技能后普攻翻倍"})
req({"id":"qilin_armor","name":"麒麟甲","type":"防具","quality":"绝品","color":"#b84aff","atk":0,"hp":900,"crit":0,"desc":"麒麟鳞甲", "exclusive":"zhugeliang","special":"受击20%回血10%"})
req({"id":"pojun_bow","name":"破军弓","type":"武器","quality":"绝品","color":"#b84aff","atk":400,"hp":0,"crit":10,"desc":"一箭破军", "exclusive":"yangyouji","special":"暴击伤害+50%"})
req({"id":"bagua_mirror","name":"八卦镜","type":"饰品","quality":"绝品","color":"#b84aff","atk":0,"hp":300,"crit":10,"desc":"洞察先机","exclusive":None,"special":"暴击率+10%，闪避率+10%"})
req({"id":"qinglian_sword","name":"青莲剑","type":"武器","quality":"传说","color":"#ffd700","atk":1000,"hp":0,"crit":15,"desc":"一剑光寒十九洲", "exclusive":"lihai","special":"青莲剑诀伤害翻倍；李白装备攻击+30%"})
req({"id":"zhangba_spear","name":"丈八蛇矛","type":"武器","quality":"传说","color":"#ffd700","atk":600,"hp":2000,"crit":5,"desc":"喝断桥梁水倒流", "exclusive":"zhangfei","special":"当阳怒吼全屏，护盾40%"})
req({"id":"qinglong_blade","name":"青龙偃月刀","type":"武器","quality":"传说","color":"#ffd700","atk":800,"hp":500,"crit":20,"desc":"武圣之刃", "exclusive":"guanyu","special":"青龙偃月真实伤害"})
req({"id":"chitu","name":"赤兔马","type":"饰品","quality":"传说","color":"#ffd700","atk":100,"hp":1000,"crit":15,"desc":"马中赤兔","exclusive":None,"special":"全队速度+20%，开局30%护盾"})
req({"id":"heshi_bi","name":"和氏璧","type":"饰品","quality":"传说","color":"#ffd700","atk":200,"hp":800,"crit":25,"desc":"完璧归赵","exclusive":None,"special":"全属性+15%"})

EQ_QUALITY_ORDER = {"凡品":0,"良品":1,"极品":2,"绝品":3,"传说":4}

# ═══════════════════════════════════════════
# 羁绊
# ═══════════════════════════════════════════

BONDS = [
    {"id":"poem_sword","name":"诗剑双绝","members":["lihai","zhaoyun"],"desc":"攻击+25%","effect":{"atk_pct":25}},
    {"id":"peach_garden","name":"桃园结义","members":["guanyu","zhangfei"],"desc":"减伤+20%","effect":{"dmg_reduce":20}},
    {"id":"sleeping_phoenix","name":"卧龙凤雏","members":["zhugeliang","pangtong"],"desc":"技能伤害+35%","effect":{"skill_dmg":35}},
    {"id":"beauty","name":"国色天香","members":["diaochan","caiwenji"],"desc":"闪避+15%","effect":{"dodge":15}},
    {"id":"sharpshooter","name":"百步穿杨","members":["yangyouji","huangzhong"],"desc":"暴击+15%","effect":{"crit":15,"crit_dmg":30}},
    {"id":"rivalry","name":"宿命对决","members":["luobu","guanyu"],"desc":"攻击+20%","effect":{"atk_pct":20}},
]

# ═══════════════════════════════════════════
# AI 关卡生成
# ═══════════════════════════════════════════

ENEMY_NAMES_POOL = [
    "山贼","流寇","马匪","土匪","响马","刀客","剑奴","毒蜂",
    "蛊师","修罗","刺客","死士","铁卫","血手","暗影","疯丐",
    "头陀","喇嘛","艄公","船匪","海贼","镖师","捕快","逃兵",
    "力士","拳师","枪兵","盾卫","弓手","弩手","火夫","兽医",
    "尼姑","道童","书生","伶人","商贾","衙役","狱卒","更夫"
]

ENEMY_PREFIXES = [
    "黑风","血影","幽冥","赤焰","天煞","地煞","玄冰","紫电",
    "狂风","怒涛","烈火","寒铁","鬼面","妖瞳","尸骨","白骨",
    "金甲","银袍","铜皮","铁骨","千手","百足","九头","三眼",
]

LOCATION_NAMES = [
    "黑风岭","断魂崖","乱葬岗","野猪林","枯木渡","寒潭",
    "毒瘴谷","赤焰山","冰封峡","天绝峰","忘川河","奈何桥",
    "落凤坡","伏龙岭","埋骨地","骷髅庙","破庙","古墓","密道",
]

STAGE_TITLES = [
    "遭遇战","伏击","夜袭","追捕","突围","守卫","劫杀",
    "闯关","破阵","讨伐","剿匪","探索","潜伏","突袭",
]

def generate_stage_name(stage_index):
    """生成关卡名"""
    loc = random.choice(LOCATION_NAMES)
    prefix = random.choice(ENEMY_PREFIXES)
    enemy = random.choice(ENEMY_NAMES_POOL)
    title = random.choice(STAGE_TITLES)
    return f"{prefix}{enemy}·{loc}·{title}"

def generate_stage(player_power, stage_index):
    """根据玩家战力动态生成关卡"""
    # 难度曲线：前期轻松，后期有挑战
    diff_mult = 0.45 + stage_index * 0.015
    diff_mult = min(diff_mult, 2.0)
    enemy_power = int(player_power * diff_mult)
    enemy_power = max(50, enemy_power)

    # 掉落品质随关卡提升
    if stage_index < 3:
        equip_pool = ["wood_sword","cloth_armor","straw_sandal"]
        jade_base = 5
        equip_chance = 0.2
    elif stage_index < 8:
        equip_pool = ["iron_sword","chain_armor","bronze_mirror"]
        jade_base = 10
        equip_chance = 0.3
    elif stage_index < 15:
        equip_pool = ["longquan_sword","mingguang_armor","jade_pendant"]
        jade_base = 18
        equip_chance = 0.4
    elif stage_index < 25:
        equip_pool = ["halberd","qilin_armor","pojun_bow","bagua_mirror"]
        jade_base = 30
        equip_chance = 0.5
    else:
        equip_pool = ["qinglian_sword","zhangba_spear","qinglong_blade","chitu","heshi_bi"]
        jade_base = 45
        equip_chance = 0.55

    name = generate_stage_name(stage_index)
    return {
        "id": f"stage_{stage_index}",
        "name": name,
        "power": enemy_power,
        "drops": {"jade": jade_base, "equip_chance": min(equip_chance + stage_index * 0.005, 0.7), "equip_pool": equip_pool},
    }

def ai_generate_stage_flavor(stage_index, stage_name, player_power, enemy_power):
    """用 DeepSeek 生成关卡描述（可选）"""
    prompt = f"""你是一个武侠世界的叙事者。为第{stage_index}关生成一句场景描述（15字以内）。

关卡名：{stage_name}
玩家战力：{player_power}
敌方战力：{enemy_power}

只输出一句古风描述，不要多余内容。"""
    reply = call_deepseek([
        {"role": "system", "content": "你是古风武侠叙事者，语言精炼。"},
        {"role": "user", "content": prompt}
    ])
    if reply and len(reply) < 50:
        return reply.strip()
    return None

# ═══════════════════════════════════════════
# 游戏状态
# ═══════════════════════════════════════════

def new_game_inner():
    starter_ids = ["lisi","wangdazhuang","xiaocui"]
    inventory = []
    for sid in starter_ids:
        inventory.append({"hero_id":sid,"skill_lv":1,"equipped":{"武器":None,"防具":None,"饰品":None},"active":True})
    return {
        "jade": 10,
        "inventory": inventory,
        "equip_bag": [{"id":"wood_sword","count":1}],
        "lineup": [sid for sid in starter_ids],
        "pull_count": 0,
        "pity_counter": 0,
        "stage_index": 0,
        "ticks": 0,
        "msg": "☯ 欢迎来到江湖！",
        "selected_hero": None,
        "game_over": False,
        "won": False,
        "last_active_time": datetime.now().timestamp(),
        "offline_msg": "",
    }

# ── 工具 ──

def calc_power(all_data, lineup, inventory, equip_bag):
    total = 0
    for hero_id in lineup:
        inv = next((i for i in inventory if i["hero_id"] == hero_id and i["active"]), None)
        if not inv: continue
        hd = all_data.get(hero_id)
        if not hd: continue
        p = hd["hp"] + hd["atk"] * 2
        for slot, eq_id in inv["equipped"].items():
            if eq_id and eq_id in EQUIP_DATA:
                eq = EQUIP_DATA[eq_id]
                p += eq["atk"] * 2 + eq["hp"]
        p *= (1 + (inv["skill_lv"] - 1) * 0.1)
        total += p
    return int(total)

def calc_hero_power(hero_id, skill_lv, equipped):
    hd = HERO_DATA.get(hero_id)
    if not hd: return 0
    p = hd["hp"] + hd["atk"] * 2
    for slot, eq_id in equipped.items():
        if eq_id and eq_id in EQUIP_DATA:
            eq = EQUIP_DATA[eq_id]
            p += eq.get("atk",0) * 2 + eq.get("hp",0)
    p *= (1 + (skill_lv - 1) * 0.1)
    return int(p)

def get_active_bonds(lineup):
    active = []
    for b in BONDS:
        if all(m in lineup for m in b["members"]):
            active.append(b)
    return active

# ═══════════════════════════════════════════
# 抽卡
# ═══════════════════════════════════════════

def roll_quality(pity):
    if pity >= 10:
        return random.choices(["极品","绝品","传说"], weights=[50,35,15])[0]
    total_w = sum(QUALITY_WEIGHTS.values())
    roll = random.randint(1, total_w)
    cum = 0
    for q, w in sorted(QUALITY_WEIGHTS.items(), key=lambda x: QUALITY_ORDER[x[0]]):
        cum += w
        if roll <= cum: return q
    return "凡品"

def pull_hero(pity):
    q = roll_quality(pity)
    pool = [hid for hid, h in HERO_DATA.items() if h["quality"] == q]
    hero_id = random.choice(pool) if pool else "lisi"
    return {"hero_id": hero_id, "quality": q, "skill_lv": 1, "equipped": {"武器":None,"防具":None,"饰品":None}, "active": False}

# ═══════════════════════════════════════════
# 战斗系统
# ═══════════════════════════════════════════

ENEMY_CLASSES = ["战士","肉盾","刺客","法师","射手","奶妈"]

def _generate_enemy(name, power_share):
    cls = random.choice(ENEMY_CLASSES)
    hp = int(power_share * random.uniform(1.2, 1.8))
    atk = int(power_share * random.uniform(0.08, 0.15))
    q = random.choices(["凡品","良品","极品","绝品","传说"], weights=[30,30,25,12,3])[0]
    return {"name": name, "class": cls, "quality": q, "color": RARITY_COLORS.get(q, "#888"),
            "hp": hp, "max_hp": hp, "atk": atk, "crit": random.randint(5, 30), "alive": True}

def _hero_to_fighter(hero_id, inv):
    hd = HERO_DATA.get(hero_id)
    if not hd: return None
    hp = hd["hp"]; atk = hd["atk"]
    for slot, eq_id in inv["equipped"].items():
        if eq_id and eq_id in EQUIP_DATA:
            eq = EQUIP_DATA[eq_id]; hp += eq.get("hp",0); atk += eq.get("atk",0)
    return {"id": hero_id, "name": hd["name"], "class": hd["class"], "quality": hd["quality"],
            "color": hd["color"], "hp": hp, "max_hp": hp, "atk": atk, "crit": hd["crit"],
            "skill_name": hd["skill_name"], "alive": True}

def run_battle(g, stage):
    my_heroes = []
    for hero_id in g["lineup"]:
        inv = next((i for i in g["inventory"] if i["hero_id"] == hero_id), None)
        if not inv: continue
        f = _hero_to_fighter(hero_id, inv)
        if f: my_heroes.append(f)

    enemy_count = min(len(my_heroes) + random.randint(-1, 1), 5)
    enemy_count = max(1, enemy_count)
    power_per = stage["power"] / max(1, enemy_count)
    enemies = []
    used_names = set()
    for _ in range(enemy_count):
        name = random.choice([n for n in ENEMY_NAMES_POOL if n not in used_names] or ENEMY_NAMES_POOL)
        used_names.add(name)
        enemies.append(_generate_enemy(name, power_per))

    active_bonds = get_active_bonds(g["lineup"])
    turns = []
    max_rounds = 20
    alive_heroes = [f for f in my_heroes]
    alive_enemies = [e for e in enemies]

    for round_idx in range(max_rounds):
        if not alive_heroes or not alive_enemies: break
        round_turns = []
        # 我方攻击
        for attacker in list(alive_heroes):
            if not alive_enemies: break
            target = random.choice(alive_enemies)
            target_idx = enemies.index(target) if target in enemies else 0
            dmg = int(attacker["atk"] * random.uniform(0.6, 1.0))
            crit = random.random() < attacker["crit"] / 100
            if crit: dmg = int(dmg * 1.5)
            target["hp"] -= dmg
            killed = target["hp"] <= 0
            if killed:
                target["alive"] = False
                alive_enemies = [e for e in alive_enemies if e["alive"]]
            attacker_idx = my_heroes.index(attacker) if attacker in my_heroes else 0
            round_turns.append({"side":"ally","attacker_name":attacker["name"],"attacker_class":attacker["class"],
                "attacker_color":attacker["color"],"attacker_idx":attacker_idx,"skill":attacker.get("skill_name","攻击"),
                "target_name":target["name"],"target_class":target["class"],"target_color":target["color"],
                "target_idx":target_idx,"damage":dmg,"crit":crit,"killed":killed,
                "target_hp_pct":max(0,target["hp"]/max(1,target["max_hp"])),"heal":0})
        # 敌方攻击
        if alive_enemies:
            for attacker in list(alive_enemies):
                if not alive_heroes: break
                target = random.choice(alive_heroes)
                target_idx = my_heroes.index(target) if target in my_heroes else 0
                dmg = int(attacker["atk"] * random.uniform(0.4, 0.8))
                crit = random.random() < attacker["crit"] / 100
                if crit: dmg = int(dmg * 1.5)
                target["hp"] -= dmg
                killed = target["hp"] <= 0
                if killed:
                    target["alive"] = False
                    alive_heroes = [f for f in alive_heroes if f["alive"]]
                attacker_idx = enemies.index(attacker) if attacker in enemies else 0
                round_turns.append({"side":"enemy","attacker_name":attacker["name"],"attacker_class":attacker["class"],
                    "attacker_color":attacker["color"],"attacker_idx":attacker_idx,"skill":"攻击",
                    "target_name":target["name"],"target_class":target["class"],"target_color":target["color"],
                    "target_idx":target_idx,"damage":dmg,"crit":crit,"killed":killed,
                    "target_hp_pct":max(0,target["hp"]/max(1,target["max_hp"])),"heal":0})
        # 治疗
        for f in alive_heroes:
            if f["class"] == "奶妈" and random.random() < 0.5:
                heal_target = min(alive_heroes, key=lambda x: x["hp"]/max(1,x["max_hp"]))
                heal = int(heal_target["max_hp"] * 0.15)
                heal_target["hp"] = min(heal_target["max_hp"], heal_target["hp"] + heal)
                heal_idx = my_heroes.index(heal_target) if heal_target in my_heroes else 0
                att_idx = my_heroes.index(f) if f in my_heroes else 0
                round_turns.append({"side":"heal","attacker_name":f["name"],"attacker_class":f["class"],
                    "attacker_color":f["color"],"attacker_idx":att_idx,"skill":"回春术",
                    "target_name":heal_target["name"],"target_class":heal_target["class"],
                    "target_color":heal_target["color"],"target_idx":heal_idx,"damage":0,"crit":False,
                    "killed":False,"target_hp_pct":heal_target["hp"]/max(1,heal_target["max_hp"]),"heal":heal})
        if round_turns:
            turns.append({"round": round_idx + 1, "actions": round_turns})

    win = len(alive_heroes) > 0 and len(alive_enemies) == 0
    result = {"win": win, "rounds": len(turns), "turns": turns}

    result["my_heroes"] = [{"name":f["name"],"class":f["class"],"quality":f["quality"],"color":f["color"],
        "max_hp":f["max_hp"],"atk":f["atk"],"hp_pct":max(0,f["hp"]/max(1,f["max_hp"])),"alive":f["alive"]}
        for f in my_heroes]
    result["enemies"] = [{"name":e["name"],"class":e["class"],"quality":e["quality"],"color":e["color"],
        "max_hp":e["max_hp"],"atk":e["atk"],"hp_pct":max(0,e["hp"]/max(1,e["max_hp"])),"alive":e["alive"]}
        for e in enemies]
    result["bonds"] = [{"name":b["name"],"desc":b["desc"]} for b in active_bonds]

    if win:
        drops = stage["drops"]
        jade_reward = drops["jade"] + random.randint(-3, 8)
        jade_reward = max(3, jade_reward)
        g["jade"] += jade_reward
        result["jade_reward"] = jade_reward
        result["equip_reward"] = None
        if random.random() < drops["equip_chance"] and drops["equip_pool"]:
            eq_id = random.choice(drops["equip_pool"])
            existing = next((e for e in g["equip_bag"] if isinstance(e,dict) and e["id"]==eq_id), None)
            if existing: existing["count"] += 1
            else: g["equip_bag"].append({"id": eq_id, "count": 1})
            eq = EQUIP_DATA.get(eq_id)
            if eq:
                result["equip_reward"] = {"name":eq["name"],"quality":eq["quality"],"color":eq["color"],
                    "type":eq["type"],"atk":eq.get("atk",0),"hp":eq.get("hp",0)}
        g["stage_index"] += 1
        stage_name = generate_stage_name(g["stage_index"])
        result["new_stage"] = stage_name
    else:
        consolation = max(2, stage["drops"]["jade"] // 4)
        g["jade"] += consolation
        result["jade_reward"] = consolation
        result["failed"] = True

    g["stage_name"] = stage["name"]
    g["battle_result"] = result
    return result

# ═══════════════════════════════════════════
# Flask API 路由
# ═══════════════════════════════════════════

@app.route("/")
@login_required
def index():
    return render_template("wuxia.html")

@app.route("/api/load")
@login_required
def api_load():
    g = load_game()
    if not g:
        g = new_game_inner()
        save_game(g)
    # 离线收益计算
    now = datetime.now().timestamp()
    last_active = g.get("last_active_time", now)
    seconds_away = now - last_active
    if seconds_away > 60:  # 超过1分钟算离线
        power = calc_power(HERO_DATA, g["lineup"], g["inventory"], g["equip_bag"])
        jade_per_min = max(0.3, power / 5000)
        offline_minutes = min(seconds_away / 60, 480)  # 最多8小时
        offline_jade = int(offline_minutes * jade_per_min)
        if offline_jade > 0:
            g["jade"] += offline_jade
            g["offline_msg"] = f"⏰ 离线{int(offline_minutes)}分钟，获得💎{offline_jade}玉璧" if offline_minutes < 120 else f"⏰ 离线{int(offline_minutes/60)}小时，获得💎{offline_jade}玉璧"
    g["last_active_time"] = now
    save_game(g)
    r = to_client(g)
    # 显示后清除离线消息
    if g.get("offline_msg"):
        g["offline_msg"] = ""
    return jsonify(r)

@app.route("/api/new")
@login_required
def api_new():
    g = new_game_inner()
    save_game(g)
    return jsonify(to_client(g))

@app.route("/api/pull")
@login_required
def api_pull():
    g = load_game()
    if not g: return api_new()
    if g["jade"] < 3:
        return jsonify({"error":f"玉璧不足(需要3，当前{g['jade']})", **to_client(g)})
    g["jade"] -= 3; g["pull_count"] += 1
    card = pull_hero(g["pity_counter"])
    q_idx = QUALITY_ORDER.get(card["quality"], 0)
    if q_idx >= 2: g["pity_counter"] = 0
    else: g["pity_counter"] += 1
    existing = next((i for i in g["inventory"] if i["hero_id"] == card["hero_id"]), None)
    if existing:
        existing["skill_lv"] = min(7, existing["skill_lv"] + 1)
        g["msg"] = f"🎴 抽到【{card['quality']}】{HERO_DATA[card['hero_id']]['name']}！技能升级至Lv.{existing['skill_lv']}"
    else:
        g["inventory"].append(card)
        g["msg"] = f"🎴 抽到【{card['quality']}】{HERO_DATA[card['hero_id']]['name']}！"
    save_game(g)
    r = to_client(g)
    r["pull"] = {"hero_id":card["hero_id"],"quality":card["quality"],
                 "hero_name":HERO_DATA[card["hero_id"]]["name"],"hero_class":HERO_DATA[card["hero_id"]]["class"]}
    return jsonify(r)

@app.route("/api/pull10")
@login_required
def api_pull10():
    g = load_game()
    if not g: return api_new()
    if g["jade"] < 25:
        return jsonify({"error":f"玉璧不足(需要25，当前{g['jade']})", **to_client(g)})
    g["jade"] -= 25; g["pull_count"] += 10
    cards = []; best_q = "凡品"
    for _ in range(10):
        card = pull_hero(g["pity_counter"])
        q_idx = QUALITY_ORDER.get(card["quality"], 0)
        if q_idx >= 2: g["pity_counter"] = 0
        else: g["pity_counter"] += 1
        if QUALITY_ORDER.get(card["quality"], 0) > QUALITY_ORDER.get(best_q, 0):
            best_q = card["quality"]
        existing = next((i for i in g["inventory"] if i["hero_id"] == card["hero_id"]), None)
        if existing: existing["skill_lv"] = min(7, existing["skill_lv"] + 1)
        else: g["inventory"].append(card)
        cards.append(card)
    g["msg"] = f"🎴 十连结束！最高品质【{best_q}】"
    save_game(g)
    r = to_client(g)
    r["pull"] = [{"hero_id":c["hero_id"],"quality":c["quality"],
                  "hero_name":HERO_DATA[c["hero_id"]]["name"],"hero_class":HERO_DATA[c["hero_id"]]["class"]} for c in cards]
    r["is_10_pull"] = True
    return jsonify(r)

@app.route("/api/lineup/<hero_id>")
@login_required
def api_lineup_toggle(hero_id):
    g = load_game()
    if not g: return api_new()
    if hero_id in g["lineup"]:
        g["lineup"].remove(hero_id)
        inv = next((i for i in g["inventory"] if i["hero_id"] == hero_id), None)
        if inv: inv["active"] = False
        g["msg"] = f"{HERO_DATA[hero_id]['name']}已下阵"
    else:
        if len(g["lineup"]) >= 6:
            return jsonify({"error":"阵容最多6人", **to_client(g)})
        g["lineup"].append(hero_id)
        inv = next((i for i in g["inventory"] if i["hero_id"] == hero_id), None)
        if inv: inv["active"] = True
        g["msg"] = f"{HERO_DATA[hero_id]['name']}已上阵"
    save_game(g)
    return jsonify(to_client(g))

@app.route("/api/equip/<hero_id>/<slot>/<eq_id>")
@login_required
def api_equip(hero_id, slot, eq_id):
    g = load_game()
    if not g: return api_new()
    inv = next((i for i in g["inventory"] if i["hero_id"] == hero_id), None)
    if not inv: return jsonify(to_client(g))
    if eq_id == "none": inv["equipped"][slot] = None
    elif eq_id in EQUIP_DATA and EQUIP_DATA[eq_id]["type"] == slot: inv["equipped"][slot] = eq_id
    save_game(g)
    return jsonify(to_client(g))

@app.route("/api/tick")
@login_required
def api_tick():
    """在线收益：每30秒客户端调一次"""
    g = load_game()
    if not g: return api_new()
    g["last_active_time"] = datetime.now().timestamp()
    power = calc_power(HERO_DATA, g["lineup"], g["inventory"], g["equip_bag"])
    jade_per_min = max(0.3, power / 5000)
    # 每次tick给半分钟的收益
    tick_jade = max(1, int(jade_per_min * 0.5))
    g["jade"] += tick_jade
    save_game(g)
    return jsonify({"jade":g["jade"],"tick_gain":tick_jade,"jade_per_min":round(jade_per_min,1)})

@app.route("/api/sweep")
@login_required
def api_sweep():
    """一键扫荡：连续打3次，跳过动画直接返回结果"""
    g = load_game()
    if not g: return api_new()
    if not g["lineup"]: return jsonify({"error":"请先上阵英雄", **to_client(g)})
    total_jade = 0
    total_equips = []
    battles_results = []
    for _ in range(3):
        power = calc_power(HERO_DATA, g["lineup"], g["inventory"], g["equip_bag"])
        stage = generate_stage(power, g["stage_index"])
        result = run_battle(g, stage)
        g["ticks"] += 1
        total_jade += result.get("jade_reward", 0)
        if result.get("equip_reward"):
            total_equips.append(result["equip_reward"])
        battles_results.append({"win":result.get("win",False),"stage_name":result.get("new_stage",""),"jade":result.get("jade_reward",0)})
        if not result.get("win", False):
            break  # 失败就停
    save_game(g)
    r = to_client(g)
    r["sweep_result"] = {
        "total_jade": total_jade,
        "total_equips": total_equips,
        "battles": battles_results,
        "count": len(battles_results),
    }
    return jsonify(r)

@app.route("/api/battle")
@login_required
def api_battle():
    g = load_game()
    if not g: return api_new()
    if not g["lineup"]: return jsonify({"error":"请先上阵英雄", **to_client(g)})
    power = calc_power(HERO_DATA, g["lineup"], g["inventory"], g["equip_bag"])
    stage = generate_stage(power, g["stage_index"])
    result = run_battle(g, stage)
    g["ticks"] += 1
    save_game(g)
    r = to_client(g)
    r["battle_result"] = result
    return jsonify(r)

@app.route("/api/hero_detail/<hero_id>")
@login_required
def api_hero_detail(hero_id):
    g = load_game()
    if not g: return api_new()
    inv = next((i for i in g["inventory"] if i["hero_id"] == hero_id), None)
    if not inv: return jsonify({"error":"未找到该英雄"})
    hd = HERO_DATA.get(hero_id)
    if not hd: return jsonify({"error":"英雄数据不存在"})
    eq_info = {}
    for slot, eq_id in inv["equipped"].items():
        if eq_id and eq_id in EQUIP_DATA:
            eq = EQUIP_DATA[eq_id]
            eq_info[slot] = {"name":eq["name"],"quality":eq["quality"],"color":eq["color"],
                             "atk":eq.get("atk",0),"hp":eq.get("hp",0),"crit":eq.get("crit",0),
                             "special":eq.get("special",""),"exclusive":eq["exclusive"]==hero_id}
    bonds = get_active_bonds(g["lineup"])
    return jsonify({"hero_id":hero_id,"name":hd["name"],"title":hd["title"],"class":hd["class"],
        "quality":hd["quality"],"color":hd["color"],"hp":hd["hp"],"atk":hd["atk"],"crit":hd["crit"],
        "skill_name":hd["skill_name"],"skill_desc":hd["skill_desc"],"skill_lv":inv["skill_lv"],
        "skill_upgrades":hd["skill_upgrades"],"passive_name":hd["passive_name"],"passive_desc":hd["passive_desc"],
        "passive_upgrades":hd["passive_upgrades"],"power":calc_hero_power(hero_id,inv["skill_lv"],inv["equipped"]),
        "equipped":eq_info,"bonds":bonds,"in_lineup":hero_id in g["lineup"]})

@app.route("/api/inventory_heroes")
@login_required
def api_inventory_heroes():
    g = load_game()
    if not g: return jsonify([])
    heroes = []
    for inv in g["inventory"]:
        hd = HERO_DATA.get(inv["hero_id"])
        if hd:
            heroes.append({"hero_id":inv["hero_id"],"name":hd["name"],"title":hd["title"],
                "class":hd["class"],"quality":hd["quality"],"color":hd["color"],
                "skill_lv":inv["skill_lv"],"power":calc_hero_power(inv["hero_id"],inv["skill_lv"],inv["equipped"]),
                "equipped":inv["equipped"],"active":inv["active"],"in_lineup":inv["hero_id"] in g["lineup"]})
    heroes.sort(key=lambda h: (QUALITY_ORDER.get(h["quality"],0), h["power"]), reverse=True)
    return jsonify(heroes)

@app.route("/api/equip_bag")
@login_required
def api_equip_bag():
    g = load_game()
    if not g: return jsonify([])
    items = []
    for e in g["equip_bag"]:
        eid = e["id"] if isinstance(e,dict) else e; cnt = e["count"] if isinstance(e,dict) else 1
        if eid in EQUIP_DATA:
            ed = EQUIP_DATA[eid]
            items.append({"id":eid,"name":ed["name"],"type":ed["type"],"quality":ed["quality"],
                "color":ed["color"],"atk":ed.get("atk",0),"hp":ed.get("hp",0),"crit":ed.get("crit",0),
                "special":ed.get("special",""),"exclusive":ed.get("exclusive"),"count":cnt})
    items.sort(key=lambda x: (EQ_QUALITY_ORDER.get(x["quality"],0), x["atk"]), reverse=True)
    return jsonify(items)

# ═══════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════

def to_client(g):
    power = calc_power(HERO_DATA, g["lineup"], g["inventory"], g["equip_bag"])
    stage_power = int(power * (0.45 + g["stage_index"] * 0.015))
    stage_power = max(50, min(stage_power, 99999))
    stage_name = g.get("stage_name") or generate_stage_name(g["stage_index"])
    pity_info = {"count":g["pity_counter"],"next_guaranteed":10-g["pity_counter"]}
    bonds = get_active_bonds(g["lineup"])
    return {
        "jade":g["jade"],"pull_count":g["pull_count"],"pity":pity_info,"power":power,
        "stage":{"name":stage_name,"power":stage_power,"index":g["stage_index"]},
        "lineup":g["lineup"],"lineup_count":len(g["lineup"]),
        "bonds":[{"name":b["name"],"desc":b["desc"]} for b in bonds],
        "msg":g.get("msg",""),"ticks":g["ticks"],
        "inventory_count":len(g["inventory"]),"username":session.get("username",""),
        "offline_msg":g.get("offline_msg",""),
        "jade_per_min":round(max(0.3, power / 5000),1),
    }

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5002
    print(f"🏯 江湖经营 (用户系统 + AI无限关卡)")
    print(f"   http://127.0.0.1:{port}")
    app.run(host="127.0.0.1", port=port, debug=False)
