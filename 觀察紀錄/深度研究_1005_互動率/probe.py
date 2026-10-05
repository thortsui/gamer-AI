# 拿存下來的截圖直接問 DGX 上的 27B：他是看圖，還是看面板上的字？（不碰遊戲、不碰他的記憶）
import sys, io, os, json, base64, urllib.request, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
EP = "http://192.168.0.126:8888/v1/chat/completions"
MODEL = "gamer-27b-ft-run1"
ROOT = r"D:\Documents\ThorWorkSpace\gamer AI\專案\農場遊戲\記憶"
IMG_PROMPT = os.path.join(ROOT, "關鍵動作", "1004_084034_第一次做成某件事.jpg")   # 右下真的有 E 提示「種植」，中央是他自己
IMG_NONE = os.path.join(ROOT, "畫面", sys.argv[1] if len(sys.argv) > 1 else "203611_定時.jpg")  # 沒有 E 提示


def ask(system, text, img, temp=0.65, n=1, max_tokens=220):
    b64 = base64.b64encode(open(img, "rb").read()).decode()
    outs = []
    for _ in range(n):
        payload = {"model": MODEL, "max_tokens": max_tokens, "temperature": temp, "top_p": 0.85, "top_k": 20,
                   "chat_template_kwargs": {"enable_thinking": False},
                   "messages": [{"role": "system", "content": system},
                                {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}},
                                                             {"type": "text", "text": text}]}]}
        req = urllib.request.Request(EP, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        t0 = time.time()
        r = json.loads(urllib.request.urlopen(req, timeout=120).read().decode("utf-8"))
        outs.append((round(time.time() - t0, 1), r["choices"][0]["message"]["content"].strip().replace("\n", " ")))
    return outs


SYS_Q = "你是在玩 Roblox 的代理。只看圖回答，看不到就說看不到，不要猜。只輸出 JSON。"
SYS_A = '你是在玩 Roblox 的代理。看畫面，只輸出一個 JSON：{"s":"看到什麼","想":"一句","a":[動作...]}。動作例：{"t":"key","k":"w","d":1.5}、{"t":"hold","k":"e","d":1.5}、{"t":"look","dx":40,"dy":0}。'
Q = '問題：(1) 畫面上有沒有鍵盤「E」的互動提示框？有的話大概在畫面哪裡（用圖上的格線座標）、旁邊寫什麼字？(2) 畫面正中央那個金髮、拿黑色武器的角色是誰：你自己、別的玩家、還是不會動的人偶？輸出 {"有E提示":true/false,"位置":"","旁邊的字":"","中央是誰":"","為什麼":""}'
NO = "【眼睛看到】框到 person（中中）；字：695｜鏡頭在看水平\n【畫面上有沒有 E 提示】（程式用影像判斷的，比你用眼睛猜準）\n❌ **畫面上現在沒有 E 提示** → 你按 E 一定沒有反應。先靠近一步、用 a/d 橫移對正、或換一個目標；提示跳出來再按。\n看畫面，決定接下來做什麼。"
YES = "【眼睛看到】框到 person（中中）；字：2 , 732｜鏡頭在看水平\n【畫面上有沒有 E 提示】（程式用影像判斷的，比你用眼睛猜準）\n✅ **畫面上有 E 提示**（在 (0.5,0.62)）→ 現在按 E 就有效：{\"t\":\"hold\",\"k\":\"e\",\"g\":\"提示指的東西\"}，按 1.5 秒就好。\n看畫面，決定接下來做什麼。"
PLAIN = "看畫面，決定接下來做什麼。"

tests = [
    ("1 只問圖（圖上真的有 E 提示）", SYS_Q, Q, IMG_PROMPT, 0.0, 1, 260),
    ("2 只問圖（圖上沒有 E 提示）", SYS_Q, Q, IMG_NONE, 0.0, 1, 260),
    ("3 圖上有提示、面板的字沒講 → 他做什麼", SYS_A, PLAIN, IMG_PROMPT, 0.65, 3, 200),
    ("4 圖上有提示、面板的字說「❌ 沒有」→ 他做什麼", SYS_A, NO, IMG_PROMPT, 0.65, 3, 200),
    ("5 圖上沒提示、面板的字沒講 → 他做什麼", SYS_A, PLAIN, IMG_NONE, 0.65, 3, 200),
    ("6 圖上沒提示、面板的字說「✅ 有」→ 他做什麼", SYS_A, YES, IMG_NONE, 0.65, 3, 200),
]
for name, s, t, img, temp, n, mt in tests:
    print("=====", name, "｜圖：", os.path.basename(img))
    try:
        for secs, o in ask(s, t, img, temp, n, mt):
            print(f"  （{secs}s）{o[:420]}")
    except Exception as e:  # noqa: BLE001
        print("  失敗：", e)
