# 印出面板存檔某段時間每一列的：模式、眼睛看到、OCR 字、上一步送出與結果、輸出（只讀）
import gzip, json, sys, io, os, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
game, day, t0, t1 = sys.argv[1:5]
p = os.path.join(r"D:\Documents\ThorWorkSpace\gamer AI\專案", game, "記憶", "面板", day + ".jsonl.gz")


def sec(u, head):
    i = u.find(head)
    if i < 0:
        return ""
    j = u.find("\n【", i + 1)
    return u[i:(j if j > 0 else len(u))]


with gzip.open(p, "rt", encoding="utf-8", errors="ignore") as f:
    for line in f:
        try:
            o = json.loads(line)
        except Exception:
            continue
        t = str(o.get("時間", ""))
        if not (t0 <= t <= t1):
            continue
        u = o.get("user", "")
        last = sec(u, "【上一步】").replace("\n", " ")[:230]
        ocr = " ｜ ".join(re.findall(r"\] ([^\n　]+)　中心", sec(u, "【畫面上的字】")))[:260]
        ep = sec(u, "【畫面上有沒有 E 提示】")
        print(f"[{t}] {o.get('模式')}｜任務欄「{o.get('任務')}」｜E提示判定：{'有' if '✅' in ep else ('沒' if ep else '–')}")
        print(f"    上一步：{last}")
        print(f"    OCR：{ocr}")
        print(f"    輸出：{str(o.get('輸出'))[:260]}")
