# 同一套題目問不同的大腦（不碰遊戲、不碰他的記憶）。用法：battery.py <endpoint base> <標籤>
import sys, io, os, json, base64, urllib.request, time, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
BASE = sys.argv[1].rstrip("/")
TAG = sys.argv[2]
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"battery_{TAG}.jsonl")
MODEL = json.loads(urllib.request.urlopen(BASE + "/models", timeout=10).read())["data"][0]["id"]
R = r"D:\Documents\ThorWorkSpace\gamer AI\專案\農場遊戲\記憶"
POS = [(os.path.join(R, "關鍵動作", "1004_084034_第一次做成某件事.jpg"), "695", True),
       (os.path.join(R, "關鍵動作", "1004_090642_第一次做成某件事.jpg"), "695", True),
       (os.path.join(R, "畫面", "085048_定時.jpg"), None, False)]          # 第三張中央被面板蓋住，不考「中央是誰」
NEG = [(os.path.join(R, "畫面", n + "_定時.jpg"), g) for n, g in
       [("203611", "2732"), ("003542", "2862"), ("050623", "2932"), ("100920", "3057"), ("140914", "3152"), ("161137", "3197"), ("124456", "3122")]]
LOG = open(OUT, "w", encoding="utf-8")
TOK = [0, 0.0]


def ask(system, text, img=None, temp=0.65, max_tokens=220):
    content = []
    if img:
        content.append({"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(open(img, "rb").read()).decode()}})
    content.append({"type": "text", "text": text})
    payload = {"model": MODEL, "max_tokens": max_tokens, "temperature": temp, "top_p": 0.85, "top_k": 20,
               "chat_template_kwargs": {"enable_thinking": False},
               "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}]}
    t0 = time.time()
    r = json.loads(urllib.request.urlopen(urllib.request.Request(BASE + "/chat/completions", data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                   headers={"Content-Type": "application/json"}), timeout=180).read().decode("utf-8"))
    dt = time.time() - t0
    out = (r["choices"][0]["message"].get("content") or "").strip().replace("\n", " ")
    TOK[0] += r.get("usage", {}).get("completion_tokens", 0); TOK[1] += dt
    return out, dt


def js(out):
    m = re.search(r"\{.*\}", out, re.S)
    try:
        return json.loads(m.group(0)) if m else {}
    except Exception:
        return {}


def rec(test, img, out, dt, **score):
    LOG.write(json.dumps({"test": test, "img": os.path.basename(img) if img else "", "out": out, "秒": round(dt, 1), **score}, ensure_ascii=False) + "\n"); LOG.flush()


def presses_e(d):
    return any(isinstance(a, dict) and str(a.get("k", "")).lower() == "e" for a in (d.get("a") or []))


def mentions_e(d):
    return bool(re.search(r"E ?提示|E ?鍵|按 ?E|E按鍵|互動提示", str(d.get("s", "")) + str(d.get("想", ""))))


SYS_Q = "你是在玩 Roblox 的代理。只看圖回答，看不到就說看不到，不要猜。只輸出 JSON。"
SYS_A = '你是在玩 Roblox 的代理。看畫面，只輸出一個 JSON：{"s":"看到什麼","想":"一句","a":[動作...]}。動作例：{"t":"key","k":"w","d":1.5}、{"t":"hold","k":"e","d":1.5}、{"t":"look","dx":40,"dy":0}。'
Q = '問題：(1) 畫面上有沒有鍵盤「E」的互動提示框？有的話大概在畫面哪裡、旁邊寫什麼字？(2) 畫面正中央那個金髮、拿黑色武器的角色是誰：你自己、別的玩家、還是不會動的人偶？(3) 左上角金幣數字是多少？輸出 {"有E提示":true/false,"位置":"","旁邊的字":"","中央是誰":"","金幣":""}'
NO = "【眼睛看到】框到 person（中中）；字：695｜鏡頭在看水平\n【畫面上有沒有 E 提示】（程式用影像判斷的，比你用眼睛猜準）\n❌ **畫面上現在沒有 E 提示** → 你按 E 一定沒有反應。先靠近一步、用 a/d 橫移對正、或換一個目標；提示跳出來再按。\n看畫面，決定接下來做什麼。"
YES = "【眼睛看到】框到 person（中中）；字：2 , 732｜鏡頭在看水平\n【畫面上有沒有 E 提示】（程式用影像判斷的，比你用眼睛猜準）\n✅ **畫面上有 E 提示**（在 (0.5,0.62)）→ 現在按 E 就有效：{\"t\":\"hold\",\"k\":\"e\",\"g\":\"提示指的東西\"}，按 1.5 秒就好。\n看畫面，決定接下來做什麼。"
PLAIN = "看畫面，決定接下來做什麼。"
S = {}

# T1 只看圖
ok_e = ok_c = ok_g = n_c = n_g = 0
for img, gold, center in POS:
    out, dt = ask(SYS_Q, Q, img, 0.0, 200); d = js(out)
    e = d.get("有E提示") is True; ok_e += e
    c = "自己" in str(d.get("中央是誰", "")) if center else None
    if center: n_c += 1; ok_c += bool(c)
    g = None
    if gold: n_g += 1; g = re.sub(r"\D", "", str(d.get("金幣", ""))) == gold; ok_g += g
    rec("T1", img, out, dt, 應有提示=True, 答對提示=e, 答對中央=c, 答對金幣=g)
for img, gold in NEG:
    out, dt = ask(SYS_Q, Q, img, 0.0, 200); d = js(out)
    e = d.get("有E提示") is False; ok_e += e
    c = "自己" in str(d.get("中央是誰", "")); n_c += 1; ok_c += c
    g = re.sub(r"\D", "", str(d.get("金幣", ""))) == gold; n_g += 1; ok_g += g
    rec("T1", img, out, dt, 應有提示=False, 答對提示=e, 答對中央=c, 答對金幣=g)
S["T1 只看圖：有沒有E提示"] = f"{ok_e}/10"; S["T1 只看圖：中央是自己"] = f"{ok_c}/{n_c}"; S["T1 只看圖：讀金幣數字"] = f"{ok_g}/{n_g}"

# T3 圖上有提示、不給面板的字
pe = me = n = 0
for img, _, _ in POS:
    for _ in range(3):
        out, dt = ask(SYS_A, PLAIN, img); d = js(out); n += 1; pe += presses_e(d); me += mentions_e(d)
        rec("T3", img, out, dt, 按E=presses_e(d), 提到E=mentions_e(d))
S["T3 圖上有提示、沒給字：提到E提示"] = f"{me}/{n}"; S["T3 圖上有提示、沒給字：按E"] = f"{pe}/{n}"

# T2a 圖上有提示、字說沒有
pe = me = cp = n = 0
for img, _, _ in POS:
    for _ in range(3):
        out, dt = ask(SYS_A, NO, img); d = js(out); n += 1; pe += presses_e(d); me += mentions_e(d); c = str(d.get("s", "")).startswith("框到 person"); cp += c
        rec("T2a", img, out, dt, 按E=presses_e(d), 提到E=mentions_e(d), 照抄=c)
S["T2 圖上有提示、字說沒有：還是看得出有提示"] = f"{me}/{n}"; S["T2 圖上有提示、字說沒有：s 逐字照抄面板"] = f"{cp}/{n}"

# T2b 圖上沒提示、字說有
pe = cp = n = 0
for img, _ in NEG[:3]:
    for _ in range(3):
        out, dt = ask(SYS_A, YES, img); d = js(out); n += 1; pe += presses_e(d); c = str(d.get("s", "")).startswith("框到 person"); cp += c
        rec("T2b", img, out, dt, 按E=presses_e(d), 照抄=c)
S["T2 圖上沒提示、字說有：被騙去按E"] = f"{pe}/{n}"; S["T2 圖上沒提示、字說有：s 逐字照抄面板"] = f"{cp}/{n}"

# T4 會不會懷疑程式（純文字）
SYS_R = "你是在玩 Roblox 的代理。只輸出 JSON。"
T4 = ("【上一步】你送出：長按 E 互動「田」\n　結果：畫面變化 0（幾乎沒動）　⚠ **那一下沒有效果**。同樣的做法不會突然有效——這一輪換別的做法，不要再做一次一樣的事。"
      "{auth}\n【按之前畫面上的字】高麗菜／已成熟·剩 3 支／收取 1／77 / 365\n【按之後畫面上的字】收成 3 支·獲得 27 經驗／收取／104 / 365\n"
      '問：那一下按 E 到底有沒有效果？輸出 {"有效果":true/false,"為什麼":"<=30字","下一次遇到一樣的畫面":"<=20字"}')
for name, auth in [("T4 程式說沒效果、但字變了：他判斷有效果", ""), ("T4 同上，再加一句「程式比你的印象可靠」", "\n（結果是程式量出來的，比你的印象可靠）")]:
    okn = 0
    for _ in range(5):
        out, dt = ask(SYS_R, T4.replace("{auth}", auth), None, 0.65, 160); d = js(out); v = d.get("有效果") is True; okn += v
        rec("T4", None, out, dt, 加權威=bool(auth), 判有效果=v)
    S[name] = f"{okn}/5"

# T5 面板把自己列成目標
T5 = ("【你自己長什麼樣】你記過的自己：金髮、黑裝、持黑色武器，第三人稱背影\n【眼睛現在框到】person 在 (0.51,0.55) 大小 0.19\n"
      "【YOLO 算給你的下一步】目標「person」已經對正、高度 0.19（還有距離） → 直接按 w 走 1.5 秒\n【可選目標】1. person（中中）\n"
      '看畫面。輸出 {"person是誰":"自己／別的玩家／人偶","s":"看到什麼","a":[動作...]}')
okn = n = 0
for img, _ in NEG[:3]:
    for _ in range(2):
        out, dt = ask(SYS_R, T5, img, 0.65, 200); d = js(out); v = "自己" in str(d.get("person是誰", "")); okn += v; n += 1
        rec("T5", img, out, dt, 認出自己=v)
S["T5 面板把 person 列成目標：他認出那是自己"] = f"{okn}/{n}"

# T6 速度
out, dt = ask("你是助手。", "用繁體中文寫一段 250 字左右的短文，介紹怎麼種高麗菜。", None, 0.7, 320)
S["速度（純文字，字／秒）"] = "–"
r = TOK[0] / max(TOK[1], 0.01)
S["整套題目：平均每題秒數"] = f"{TOK[1] / max(sum(1 for _ in open(OUT, encoding='utf-8')), 1):.1f}"
S["整套題目：輸出 token／秒（含讀圖時間）"] = f"{r:.1f}"
LOG.close()
print(f"===== {TAG}（{MODEL}）")
for k, v in S.items():
    print(f"  {k}：{v}")
json.dump({"tag": TAG, "model": MODEL, "S": S}, open(OUT.replace(".jsonl", "_sum.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
