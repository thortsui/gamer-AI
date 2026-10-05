# 找農場遊戲裡「畫面真的跳出收成／獲得經驗／種下」字樣的時刻，對照前一兩輪送出的動作與程式的判定（只讀）
import gzip, json, sys, io, os, re, glob
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = r"D:\Documents\ThorWorkSpace\gamer AI\專案\農場遊戲\記憶\面板"


def sec(u, head):
    i = u.find(head)
    if i < 0:
        return ""
    j = u.find("\n【", i + 1)
    return u[i:(j if j > 0 else len(u))]


for p in sorted(glob.glob(os.path.join(ROOT, "*.jsonl.gz"))):
    day = os.path.basename(p)[5:10]
    rows = []
    with gzip.open(p, "rt", encoding="utf-8", errors="ignore") as f:
        for line in f:
            try:
                o = json.loads(line)
            except Exception:
                continue
            u = o.pop("user", "")
            o["_ocr"] = " ".join(re.findall(r"\] ([^\n　]+)　中心", u))
            last = sec(u, "【上一步】")
            m = re.search(r"你送出：([^\n]*)", last); o["_sent"] = m.group(1) if m else ""
            m = re.search(r"結果：([^\n]*)", last); o["_res"] = (m.group(1) if m else "")[:40]
            m = re.search(r"(\d{1,4}) ?/ ?(365|625|\d{3,4})", o["_ocr"]); o["_xp"] = m.group(0) if m else ""
            rows.append(o)
    print(f"===== {day}")
    lastt = ""
    n = 0
    for i, o in enumerate(rows):
        txt = o["_ocr"] + " " + str(o.get("任務", ""))
        m = re.search(r"收成[^｜]{0,12}(經驗|經|支)|獲得 ?\d+ ?經|已種下|種下了|種植成功|賣出|獲得 ?\d+ ?金", txt)
        if not m:
            continue
        key = m.group(0)[:14]
        t = str(o.get("時間"))
        if key == lastt:
            continue
        lastt = key; n += 1
        prev = [f"{rows[j].get('時間')} {rows[j].get('模式')} 送出「{rows[j+1]['_sent'][:36]}」→ 程式判「{rows[j+1]['_res'][:22]}」" for j in range(max(0, i - 3), i)]
        print(f"  {t} 畫面出現「{key}」　XP {o['_xp']}")
        for s in prev:
            print("      前：", s)
    print(f"  共 {n} 次")
