# -*- coding: utf-8 -*-

"""

build_finetune_set.py — 從他的日誌撈 fine-tune 用的資料集（只讀，不動他任何檔案）



  python build_finetune_set.py                 # 兩隻遊戲都撈，輸出到 訓練資料/finetune/

  python build_finetune_set.py --game 新遊戲    # 只撈一隻



原則（2026-09-26 使用者定的）：

  - 只燒「不會變的」：規則、判斷、行為、學習方法、輸出格式。數字與作法留在文件。

  - 核心／訓練集不能有任何一隻遊戲的痕跡：輸入裡的遊戲名詞一律遮成「某物」，

    輸出（要教他的那一半）只要含遊戲名詞整筆丟掉。

  - 失敗輪不當正例：只收有生效、沒被扣分、符合規則的輪。



六類：

  A 規則內化   核心文件切成段落（之後拿來生成問答，這支只切段）

  B 狀態判斷   基礎確認.jsonl：看到什麼 → 視角／人稱／游標

  C 成功軌跡   關鍵動作.jsonl：成功前後那幾輪，畫面 → 動作 JSON

  D 錯誤→修正  犯錯紀錄.jsonl＋卡住／扣分／死亡檢討：畫面＋他做錯的 → 檢討寫的「下次」

  E 輸出格式   s 欄位正例（只寫看得到的）／反例（形容氣氛、推測）

  F 學習方法   慢想、成功回推、卡住／死亡／放棄／卡牆／走過頭檢討、程序：怎麼拆步驟、怎麼回推、怎麼檢討

  G 四欄想     思考流程.jsonl：畫面 → 因為｜所以要｜動作｜結果應該是 ＋ 動作 JSON（2026-10-13 run2 新增）

輸出：訓練資料/finetune/<類>.jsonl（chat 格式）＋ 統計.json

"""

import argparse

import glob

import json

import os

import random

import re

from collections import Counter, defaultdict



HERE = os.path.dirname(os.path.abspath(__file__))

OUT = os.path.join(HERE, "訓練資料", "finetune")

CORE = os.path.join(HERE, "核心")



# ---------------------------------------------------------------- 遊戲名詞（要遮／要丟）

GAME_WORDS = {

    "動物醫院": ["咖啡機", "咖啡", "櫃檯", "病人", "病患", "理智", "蓋印表格", "蓋章", "報到", "輪班", "醫院",

                 "護士", "實習生", "診間", "CCTV", "監視器", "拍照", "拍立得", "相機", "哈洛", "訪客", "採集樣本",

                 "採樣", "治療", "動物", "白貓", "藍貓", "黑貓", "貓角色", "貓頭", "小狗", "兔子", "鹿頭", "怪獸",

                 "Animal", "ANIMAL", "HOSPITAL", "Anomaly", "辦理入住", "入住", "救護車", "鐵絲網", "顯影",

                 "三腳架", "分析台", "電擊", "巧克力", "馬克杯"],

    # 一個字的詞（波、蓋、槍、貓…）會誤傷一般句子，一律用兩個字以上；傳送／電梯是 Roblox 通用概念，不遮

    "新遊戲":   ["殭屍", "喪屍", "zombie", "Zombie", "波次", "第幾波", "試槍", "測試場", "靶子", "靶場", "地圖選擇",

                 "選擇地圖", "開始出發", "配對電梯", "球坑", "防禦", "全員倒下", "隊友", "武器", "手槍", "步槍",

                 "子彈", "換彈", "建造", "方塊", "準備期", "無敵", "Build", "survive", "集結", "競技場", "對戰"],

}

ALL_GAME_WORDS = sorted({w for ws in GAME_WORDS.values() for w in ws}, key=len, reverse=True)

NAMES = ["JoJoChao99", "JojoChao99", "Thortsui", "thortsui", "idontlueyou"]

MASK = "某物"



ATMOSPHERE = ["呼吸", "詭異", "滲", "陰森", "好像", "彷彿", "似乎", "氣氛", "血跡", "邪惡", "恐怖", "陰暗", "低語",

              "扭曲", "不祥", "可怕", "毛骨", "凝視", "蠕動"]





SAFE = ["互動物", "觸碰物"]      # 2026-10-13：「記互動物」是動作名，不是「動物」；先遮起來再查





def has_game_word(s):

    s = s or ""

    for k in SAFE:

        s = s.replace(k, "")

    return any(w in s for w in ALL_GAME_WORDS)





def mask(s):

    s = s or ""

    for n in NAMES:

        s = s.replace(n, "玩家")

    for i, k in enumerate(SAFE):

        s = s.replace(k, f"〔SAFE{i}〕")

    for w in ALL_GAME_WORDS:

        s = s.replace(w, MASK)

    for i, k in enumerate(SAFE):

        s = s.replace(f"〔SAFE{i}〕", k)

    s = re.sub(r"(某物){2,}", MASK, s)

    return s





# ---------------------------------------------------------------- 「做了」字串 → 動作 JSON

_RE = [

    (re.compile(r"^按 (\w+) ([\d.]+)s$"), lambda m: {"t": "key", "k": m.group(1), "d": float(m.group(2))}),

    (re.compile(r"^轉視角 (-?\d+),(-?\d+)$"), lambda m: {"t": "look", "dx": int(m.group(1)), "dy": int(m.group(2))}),

    (re.compile(r"^點「(.+?)」\(([\d.]+),([\d.]+)\)$"),

     lambda m: {"t": "click", "x": float(m.group(2)), "y": float(m.group(3)), "g": m.group(1)}),

    (re.compile(r"^點字「(.+?)」$"), lambda m: {"t": "點字", "字": m.group(1)}),

    (re.compile(r"^長按 (\w) 互動「(.+?)」"), lambda m: {"t": "hold", "k": m.group(1).lower(), "g": m.group(2)}),

    (re.compile(r"^滾輪 (-?\d+)$"), lambda m: {"t": "scroll", "v": int(m.group(1))}),

    (re.compile(r"^等 ([\d.]+)s$"), lambda m: {"t": "wait", "s": float(m.group(1))}),

    (re.compile(r"^沒動作$"), lambda m: None),

]





def parse_actions(done):

    """「按 w 2.0s、轉視角 100,0」→ [{"t":"key",...},{"t":"look",...}]；解析不了回 None。"""

    done = re.sub(r"（按了 [\d.]+s，(沒反應|成功)）", "", done or "").strip()

    acts = []

    for part in [p.strip() for p in done.split("、") if p.strip()]:

        ok = False

        for rx, fn in _RE:

            m = rx.match(part)

            if m:

                a = fn(m)

                if a:

                    acts.append(a)

                ok = True

                break

        if not ok:

            return None

    return acts





def is_rule_ok(acts, seen):

    """行為要符合不會變的規則，才准當正例。"""

    for a in acts:

        if a["t"] == "look":

            if abs(a.get("dx", 0)) > 400 or abs(a.get("dy", 0)) > 200:   # 大幅度轉頭／上下甩

                return False

            if a.get("dy") and not re.search(r"天花板|地板|抬頭|低頭|偏上|偏下|水平", seen or ""):

                return False                                    # 沒理由動 dy

        if a["t"] == "hold" and a.get("k") == "e" and not re.search(r"E|Press|按住|互動提示", seen or ""):

            return False                                        # 沒 E 提示就按 E

    return True





# ---------------------------------------------------------------- 讀檔

def jl(path):

    rows = []

    if not os.path.exists(path):

        return rows

    with open(path, encoding="utf-8") as f:

        for line in f:

            try:

                rows.append(json.loads(line))

            except json.JSONDecodeError:

                pass

    return rows





def chat(system, user, assistant, **meta):

    d = {"messages": [{"role": "system", "content": system},

                      {"role": "user", "content": user},

                      {"role": "assistant", "content": assistant}]}

    d.update(meta)

    return d





SYS_ACT = "你是在玩 Roblox 的代理。看畫面描述，只輸出一個 JSON：{\"s\":\"看到什麼\",\"想\":\"一句\",\"a\":[動作...]}。"

SYS_JUDGE = "你是在玩 Roblox 的代理。根據畫面描述判斷自己的狀態，只輸出 JSON。"

SYS_LEARN = "你是在玩 Roblox 的代理，正在整理自己學到的東西。只輸出 JSON。"



stats = defaultdict(Counter)





def drop(cls, why):

    stats[cls]["丟:" + why] += 1





# ---------------------------------------------------------------- A 規則段落

def build_A():

    """2026-10-13 使用者：訓練所有核心規則，但「夾到遊戲內專有名詞不要」→ 夾到遊戲名詞的段落整段丟；等級表一條一段；動作表分組。"""

    out = []

    for sub in ("共用", "基礎操作", "學習模組"):

        for p in sorted(glob.glob(os.path.join(CORE, sub, "*.md"))):

            raw = open(p, encoding="utf-8").read()

            if raw.startswith("---"):

                head, _, raw = raw[3:].partition("---")

                if "停用: 是" in head:

                    continue

            for n in NAMES:

                raw = raw.replace(n, "玩家")

            # 【】段落之外的本文（例如 90_動作表 整份是表格、沒有【】）：照「## 」標題切

            pre = raw.split("\n【", 1)[0]

            if len(pre) > 300 and "指令等級表" not in os.path.basename(p):

                fname = os.path.basename(p).split("_", 1)[-1].rsplit(".", 1)[0]

                # 動作表是一行一個動作（行首兩格＋{"t"），接續行縮更多；每 ~900 字一組

                items = [x for x in re.split(r"\n(?=  \{\"t\")", pre) if x.strip()]

                groups, cur = [], ""

                for it in items:

                    if cur and len(cur) + len(it) > 900:

                        groups.append(cur); cur = ""

                    cur += ("\n" if cur else "") + it.rstrip()

                if cur:

                    groups.append(cur)

                for i, sec in enumerate(groups, 1):

                    if len(sec) < 60:

                        continue

                    if has_game_word(sec):

                        stats["A"]["遮:含遊戲名詞"] += 1; sec = mask(sec)

                    title = f"{fname} 第 {i}／{len(groups)} 組"

                    out.append({"class": "A", "file": os.path.relpath(p, CORE), "title": title,

                                "text": f"【{title}】\n{sec.strip()}"})

            if "指令等級表" in os.path.basename(p):

                # 2026-10-13：等級表是「1. 標題（日期）。內文」一條一條，沒有【】；一條就是一段（不因遊戲名詞丟）

                for m in re.finditer(r"^(\d+)\. (.*?)(?=^\d+\. |\Z)", raw, re.S | re.M):

                    body = m.group(2).strip()

                    tm = re.match(r"\*\*(.+?)\*\*", body)

                    title = tm.group(1) if tm else re.split(r"[（(。；;]", body)[0][:24]

                    chunk = f"【{title}】{body}"

                    if len(chunk) < 30:            # 等級表一條常常只有一句，門檻比【】段落低

                        continue

                    if has_game_word(chunk):

                        stats["A"]["遮:含遊戲名詞"] += 1; chunk = mask(chunk)

                    out.append({"class": "A", "file": os.path.relpath(p, CORE), "title": title, "text": chunk,

                                "等級": int(m.group(1))})

                continue

            for chunk in re.split(r"\n(?=【)", raw):

                chunk = chunk.strip()

                if len(chunk) < 60 or not chunk.startswith("【"):

                    continue

                if has_game_word(chunk):

                    stats["A"]["遮:含遊戲名詞"] += 1; chunk = mask(chunk)

                title = chunk[1:chunk.index("】")] if "】" in chunk else chunk[:20]

                out.append({"class": "A", "file": os.path.relpath(p, CORE), "title": title, "text": chunk})

    return out





# ---------------------------------------------------------------- A_text 背規則（2026-10-13 使用者：訓練所有規則進去）

SYS_RULE = "你是在玩 Roblox 的代理。有人問你某一條基礎操作規則，把那條規則完整說出來。"





def build_A_text(a_rows):

    out = []

    for r in a_rows:

        q = f"規則【{r['title']}】是什麼？" if "等級" not in r else f"指令等級表第 {r['等級']} 條（{r['title']}）是什麼？"

        out.append(chat(SYS_RULE, q, r["text"], **{"class": "A_text", "file": r["file"], "title": r["title"]}))

    return out





# ---------------------------------------------------------------- A_act 動作有什麼（2026-10-13 使用者：讓他了解動作有什麼等）
SYS_ACTLIST = "你是在玩 Roblox 的代理。回答你可以送出的動作有哪些、每個動作怎麼寫、是做什麼用的。"


def build_A_act():
    """90_動作表.md：一行一個動作 → ①這個動作怎麼寫／做什麼 ②全部動作一覽。"""
    p = os.path.join(CORE, "基礎操作", "90_動作表.md")
    if not os.path.exists(p):
        return []
    raw = open(p, encoding="utf-8").read()
    if raw.startswith("---"):
        raw = raw[3:].partition("---")[2]
    pre = raw.split("\n【", 1)[0]
    items = [x.rstrip() for x in re.split(r"\n(?=  \{\"t\")", pre) if x.strip().startswith("{\"t\"")]
    out, names = [], []
    for it in items:
        if has_game_word(it):
            drop("A_act", "含遊戲名詞"); continue
        ts = re.findall(r"\{\"t\":\"([^\"]+)\"", it)
        if not ts:
            continue
        names += [t for t in ts if t not in names]
        body = re.sub(r"[ \t]+", " ", it.strip())
        for t in ts[:1]:
            out.append(chat(SYS_ACTLIST, f"動作「{t}」怎麼寫？是做什麼用的？", body,
                            **{"class": "A_act", "動作": t}))
        if len(ts) > 1:
            out.append(chat(SYS_ACTLIST, "這幾個動作怎麼寫：" + "、".join(ts) + "？", body,
                            **{"class": "A_act", "動作": ",".join(ts)}))
    if names:
        out.append(chat(SYS_ACTLIST, "你可以送出的動作有哪些？只列名稱。", "、".join(names),
                        **{"class": "A_act", "動作": "全部"}))
        out.append(chat(SYS_ACTLIST, "一次最多可以送幾個動作？座標怎麼寫？",
                        "一次最多 2 個；座標 x、y 用 0～1 的比例。", **{"class": "A_act", "動作": "規則"}))
    return out


# ---------------------------------------------------------------- B 狀態判斷

def build_B(game, mem):

    out = []

    for r in jl(os.path.join(mem, "基礎確認.jsonl")):

        seen = r.get("看到", "")

        if not seen or not r.get("人稱") or not r.get("游標"):

            drop("B", "欄位空"); continue

        user = f"畫面描述：{mask(seen)}\n為什麼要確認：{mask(r.get('為什麼', ''))}"

        ans = {"視角": r.get("視角", ""), "人稱": r.get("人稱", ""), "游標": r.get("游標", "")}

        if has_game_word(json.dumps(ans, ensure_ascii=False)):

            drop("B", "答案含遊戲名詞"); continue

        out.append(chat(SYS_JUDGE, user, json.dumps(ans, ensure_ascii=False),

                        **{"class": "B", "game": game, "輪": r.get("輪"), "需人工抽查": True}))

    return out





# ---------------------------------------------------------------- C 成功軌跡

_LINE = re.compile(r"看到「(.*?)」→ (?:想「(.*?)」→ )?做了「(.*?)」→ 畫面變化 (\d+)")





def build_C(game, mem):

    out = []

    for r in jl(os.path.join(mem, "關鍵動作.jsonl")):

        lines = list(r.get("前", [])) + [r.get("當下", "")] + list(r.get("後", []))

        for ln in lines:

            m = _LINE.search(ln or "")

            if not m:

                continue

            seen, think, done, delta = m.group(1), m.group(2) or "", m.group(3), int(m.group(4))

            if done == "沒動作" or delta < 5:

                drop("C", "沒動作或畫面沒變"); continue

            acts = parse_actions(done)

            if acts is None or not acts:

                drop("C", "動作解析不了"); continue

            if not is_rule_ok(acts, seen):

                drop("C", "違反規則"); continue

            if has_game_word(done) or has_game_word(think):

                drop("C", "輸出含遊戲名詞"); continue

            user = f"區：{mask(r.get('區', ''))}\n任務：{mask(r.get('任務', '')) or '無'}\n畫面：{mask(seen)}"

            ans = {"s": mask(seen), "想": mask(think), "a": acts}

            out.append(chat(SYS_ACT, user, json.dumps(ans, ensure_ascii=False),

                            **{"class": "C", "game": game, "項目": r.get("項目"), "輪": r.get("輪")}))

    return out





# ---------------------------------------------------------------- D 錯誤→修正

TARGET_FAULTS = ("重複同一個動作", "原地空轉", "上下來回甩視角", "卡在選單", "面壁", "一直待在原地",

                 "大幅度轉頭", "鏡頭頂到", "步數超支")





def build_D(game, mem):

    out = []

    for r in jl(os.path.join(mem, "犯錯紀錄.jsonl")):

        fault = r.get("犯了什麼", "")

        if not any(fault.startswith(t) for t in TARGET_FAULTS):

            continue

        seen, done, rule = r.get("他說看到", ""), r.get("他送的操作", ""), r.get("他自己訂的規則", "")

        if not seen or not done or not rule:

            drop("D", "欄位空"); continue

        if has_game_word(rule):

            drop("D", "修正含遊戲名詞"); continue

        bad = parse_actions(done)

        user = (f"區／流程：{mask(r.get('在做什麼', ''))}\n視角：{mask(r.get('視角', ''))}\n畫面：{mask(seen)}\n"

                f"最近十五秒：{mask('；'.join(r.get('前十五秒', [])[-4:]))}")

        out.append({"class": "D", "game": game, "prompt": user, "system": SYS_ACT,

                    "rejected": {"a": bad, "原文": done}, "犯了什麼": fault, "為什麼扣": mask(r.get("為什麼扣", "")),

                    "correction_text": rule, "chosen": None, "備註": "chosen 要從 correction_text 轉成動作，第二步再做"})

    # 檢討也算：畫面 → 錯在哪 → 下次怎麼做

    for r in jl(os.path.join(mem, "行動日誌.jsonl")):

        if r.get("模式") not in ("卡住檢討", "扣分檢討", "死亡檢討"):

            continue

        cause, wrong, nxt = r.get("原因") or r.get("死因", ""), r.get("錯誤", ""), r.get("避免") or r.get("改善", "")

        if not (cause and wrong and nxt):

            drop("D", "檢討欄位空"); continue

        if has_game_word(nxt) or has_game_word(wrong):

            drop("D", "檢討含遊戲名詞"); continue

        out.append({"class": "D", "game": game, "prompt": f"情況：{mask(cause)}", "system": SYS_ACT,

                    "rejected": {"原文": mask(wrong)}, "犯了什麼": r.get("模式"), "correction_text": nxt, "chosen": None})

    return out





# ---------------------------------------------------------------- E 輸出格式（s 欄位）

def build_E(game, mem):

    out, pos, neg = [], 0, 0

    for r in jl(os.path.join(mem, "思考流程.jsonl")):

        s = r.get("看到", "") or ""

        if len(s) < 6:

            continue

        bad = any(w in s for w in ATMOSPHERE)

        if bad and neg >= 300:

            continue

        if not bad and (pos >= 300 or len(s) > 60 or has_game_word(s)):

            continue

        if bad:

            neg += 1

            ans = {"合格": False, "為什麼": "形容氣氛或推測，不是看到的東西",

                   "改成": "只寫物件、文字、方位、數字"}

        else:

            pos += 1

            ans = {"合格": True}

        out.append(chat("檢查代理輸出的 s 欄位：只能寫畫面上真的看得到的東西（物件名稱、文字、方位、數字），"

                        "不准形容氣氛、不准推測。只輸出 JSON。",

                        f"s 欄位：{mask(s)}", json.dumps(ans, ensure_ascii=False),

                        **{"class": "E", "game": game, "輪": r.get("輪")}))

    return out





# ---------------------------------------------------------------- F 學習方法

def build_F(game, mem):

    out = []

    for r in jl(os.path.join(mem, "行動日誌.jsonl")):

        m = r.get("模式")

        if m == "慢想":

            steps = r.get("詳細步驟") or []

            if not (2 <= len(steps) <= 6) or any(len(s) > 40 for s in steps):

                drop("F", "慢想步驟太長或太多"); continue

            ans = {"我在哪": r.get("我在哪", ""), "怎麼看出來的": r.get("怎麼看出來的", ""),

                   "現在的大動作": r.get("現在的大動作", ""), "詳細步驟": steps,

                   "下一個大動作": r.get("下一個大動作", ""), "要注意": r.get("要注意", "")}

            js = json.dumps(ans, ensure_ascii=False)

            if has_game_word(js):

                drop("F", "慢想含遊戲名詞"); continue

            out.append(chat(SYS_LEARN, "慢想：把現在要做的事排成「大動作 → 兩輪內做得完的細步驟 → 下一個大動作」。"

                            f"\n上一步：{mask(r.get('上一步', ''))}", js, **{"class": "F", "kind": "慢想", "game": game}))

        elif m == "成功回推":

            js = json.dumps({k: r.get(k) for k in r if k not in ("時間", "模式")}, ensure_ascii=False)

            if has_game_word(js):

                drop("F", "回推含遊戲名詞"); continue

            out.append(chat(SYS_LEARN, "剛才成功了。回推：做了什麼、關鍵一步是哪一步、怎麼重現、差點失敗在哪。",

                            js, **{"class": "F", "kind": "成功回推", "game": game}))

        elif m == "放棄檢討":

            keys = [k for k in ("放棄的時候", "改做了什麼", "為什麼", "下次") if r.get(k)]

            if len(keys) < 3:

                drop("F", "檢討欄位不足"); continue

            js = json.dumps({k: r[k] for k in keys}, ensure_ascii=False)

            if has_game_word(js):

                drop("F", "檢討含遊戲名詞"); continue

            out.append(chat(SYS_LEARN, f"右上角的任務「{mask(r.get('任務', ''))}」沒完成就結束了（{mask(r.get('怎麼結束', ''))}）。"

                            "檢討：放棄的時候在做什麼、改做了什麼、為什麼覺得那比較重要、下次怎麼做（每格短語）。",

                            js, **{"class": "F", "kind": m, "game": game}))

        elif m in ("卡牆檢討", "走過頭檢討"):

            keys = [k for k in ("原因", "錯誤", "避免", "補充") if r.get(k)]

            if len(keys) < 3:

                drop("F", "檢討欄位不足"); continue

            js = json.dumps({k: r[k] for k in keys}, ensure_ascii=False)

            if has_game_word(js) or has_game_word(r.get("事件", "")):

                drop("F", "檢討含遊戲名詞"); continue

            out.append(chat(SYS_LEARN, f"{mask(r.get('事件', ''))}。檢討：原因、錯在哪、下次怎麼做、現在怎麼離開（每格短語）。",

                            js, **{"class": "F", "kind": m, "game": game}))

        elif m in ("卡住檢討", "扣分檢討", "死亡檢討"):

            keys = [k for k in ("原因", "死因", "錯誤", "避免", "改善", "記憶") if r.get(k)]

            if len(keys) < 3:

                drop("F", "檢討欄位不足"); continue

            js = json.dumps({k: r[k] for k in keys}, ensure_ascii=False)

            if has_game_word(js):

                drop("F", "檢討含遊戲名詞"); continue

            trig = {"卡住檢討": "你卡住了", "扣分檢討": f"你被扣分：{mask(r.get('項目', ''))}", "死亡檢討": "你死了"}[m]

            out.append(chat(SYS_LEARN, f"{trig}。檢討：原因、錯在哪、下次怎麼做（一句一件，要具體到可以照做）。",

                            js, **{"class": "F", "kind": m, "game": game}))

    # 他編譯過、而且真的成功過的程序

    for p in glob.glob(os.path.join(mem, "程序", "*.json")):

        try:

            d = json.load(open(p, encoding="utf-8"))

        except (OSError, json.JSONDecodeError):

            continue

        if not d.get("成功") or has_game_word(json.dumps(d.get("步驟"), ensure_ascii=False)):

            drop("F", "程序沒成功過或含遊戲名詞"); continue

        out.append(chat(SYS_LEARN, "把剛才做成的事寫成程序（每一步：做什麼、對什麼、等到什麼）。",

                        json.dumps({"名": mask(d.get("名", "")), "步驟": d.get("步驟")}, ensure_ascii=False),

                        **{"class": "F", "kind": "程序", "game": game}))

    return out





# ---------------------------------------------------------------- G 四欄想（看畫面 → 四欄想 ＋ 動作）

SYS_ACT4 = ("你是在玩 Roblox 的代理。看畫面描述，只輸出一個 JSON："

            "{\"s\":\"看到什麼\",\"想\":\"因為:看到什麼｜所以要:目的｜動作:做什麼｜結果應該是:畫面會變怎樣\",\"a\":[動作...]}。"

            "想 固定四欄短語，不寫其他連接詞與贅字；a 就是「動作」那一欄。")





def build_G(game, mem):

    """思考流程.jsonl 裡 想 已經是四欄的輪，配 步驟日誌 的畫面變化，只收做了有生效、合規則、沒遊戲名詞的。"""

    out = []

    delta = defaultdict(int)

    for s in jl(os.path.join(mem, "步驟日誌.jsonl")):

        m = re.search(r"畫面變化 (\d+)", str(s.get("結果", "")))

        if m and s.get("輪") is not None:

            delta[s["輪"]] = max(delta[s["輪"]], int(m.group(1)))

    for r in jl(os.path.join(mem, "思考流程.jsonl")):

        think, seen, done = str(r.get("想") or ""), r.get("看到", "") or "", r.get("做了", "") or ""

        if think.count("｜") < 3 or not think.startswith("因為:"):

            continue

        if delta.get(r.get("輪"), 0) < 5:

            drop("G", "畫面沒變"); continue

        acts = parse_actions(done)

        if not acts:

            drop("G", "動作解析不了或沒動作"); continue

        if not is_rule_ok(acts, seen):

            drop("G", "違反規則"); continue

        if has_game_word(think) or has_game_word(done):

            drop("G", "輸出含遊戲名詞"); continue

        cols = [c.strip() for c in think.split("｜")]

        if len(cols) != 4 or any(len(c) > 40 for c in cols):

            drop("G", "四欄不整齊或太長"); continue

        user = (f"區：{mask(r.get('區', ''))}\n任務：{mask(r.get('任務', '')) or '無'}\n視角：{mask(r.get('視角', ''))}\n"

                f"畫面：{mask(seen)}")

        ans = {"s": mask(seen), "想": "｜".join(cols), "a": acts}

        out.append(chat(SYS_ACT4, user, json.dumps(ans, ensure_ascii=False),

                        **{"class": "G", "game": game, "輪": r.get("輪")}))

    return out





# ---------------------------------------------------------------- main

def main():

    ap = argparse.ArgumentParser()

    ap.add_argument("--game", default="", help="只撈這一隻；空＝全部")

    ap.add_argument("--seed", type=int, default=7)

    args = ap.parse_args()

    random.seed(args.seed)

    os.makedirs(OUT, exist_ok=True)



    games = [args.game] if args.game else ["新遊戲", "動物醫院"]

    mems = [(g, os.path.join(HERE, "專案", g, "記憶")) for g in games]

    if not args.game:

        mems.append(("動物醫院(舊)", os.path.join(HERE, "專案", "動物醫院", "_舊_0924", "記憶")))   # 只拿 D 類反例



    data = {"A": build_A(), "B": [], "C": [], "D": [], "E": [], "F": [], "G": []}

    data["A_text"] = build_A_text(data["A"])
    data["A_act"] = build_A_act()

    for g, mem in mems:

        if not os.path.isdir(mem):

            continue

        if g.endswith("(舊)"):

            data["D"] += build_D(g, mem)

            continue

        data["B"] += build_B(g, mem)

        data["C"] += build_C(g, mem)

        data["D"] += build_D(g, mem)

        data["E"] += build_E(g, mem)

        data["F"] += build_F(g, mem)

        data["G"] += build_G(g, mem)



    summary = {}

    for cls, rows in data.items():

        random.shuffle(rows)

        n_val = max(1, len(rows) // 10) if len(rows) >= 10 else 0

        for name, part in (("train", rows[n_val:]), ("val", rows[:n_val])):

            with open(os.path.join(OUT, f"{cls}_{name}.jsonl"), "w", encoding="utf-8") as f:

                for r in part:

                    f.write(json.dumps(r, ensure_ascii=False) + "\n")

        by_game = Counter(r.get("game", "核心") for r in rows)

        summary[cls] = {"總數": len(rows), "train": len(rows) - n_val, "val": n_val,

                        "依遊戲": dict(by_game), "丟掉": dict(stats[cls])}

    json.dump(summary, open(os.path.join(OUT, "統計.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    names = {"A": "規則段落", "A_text": "背規則", "A_act": "動作有什麼", "B": "狀態判斷", "C": "成功軌跡", "D": "錯誤→修正", "E": "輸出格式", "F": "學習方法", "G": "四欄想"}

    for cls, s in summary.items():

        print(f"{cls} {names[cls]}：{s['總數']} 筆（train {s['train']}／val {s['val']}）"

              f"　依遊戲 {s['依遊戲']}　丟掉 {s['丟掉']}")

    print(f"→ {OUT}")





if __name__ == "__main__":

    main()

