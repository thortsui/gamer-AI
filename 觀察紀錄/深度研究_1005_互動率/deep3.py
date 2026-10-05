# 深度研究 3：農場遊戲的客觀進度（任務欄位換了幾次、金幣）＋每一次按 E 的前後（只讀）
import gzip, json, sys, io, os, re, glob, collections
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = r"D:\Documents\ThorWorkSpace\gamer AI\專案"
game = sys.argv[1]
what = sys.argv[2]            # tasks | epress
days = sys.argv[3:]


def sec(u, head):
    i = u.find(head)
    if i < 0:
        return None
    j = u.find("\n【", i + 1)
    return u[i:(j if j > 0 else len(u))]


def norm(t):
    t = re.sub(r"[\s·。．,，:：()（）/／]", "", str(t))
    return t[:8]


for p in sorted(glob.glob(os.path.join(ROOT, game, "記憶", "面板", "*.jsonl.gz"))):
    day = os.path.basename(p)[5:10]
    if days and day not in days:
        continue
    rows = []
    with gzip.open(p, "rt", encoding="utf-8", errors="ignore") as f:
        for line in f:
            try:
                o = json.loads(line)
            except Exception:
                continue
            u = o.pop("user", "") or ""
            o["_last"] = sec(u, "【上一步】") or ""
            ep = sec(u, "【畫面上有沒有 E 提示】")
            o["_ep"] = ("有" if ep and "✅" in ep else ("沒" if ep else "–"))
            oc = sec(u, "【畫面上的字】") or ""
            o["_ocr"] = " ".join(re.findall(r"\] ([^\n　]+)　中心", oc))[:120]
            rows.append(o)
    print(f"===== {game} {day}（{len(rows)} 列）")
    if what == "tasks":
        last = None; run = 0; seq = []
        for o in rows:
            t = norm(o.get("任務", ""))
            if not t:
                continue
            if t != last:
                seq.append([o.get("時間"), str(o.get("任務", ""))[:26], 1]); last = t
            else:
                seq[-1][2] += 1
        # 只留連續出現 3 列以上的（濾掉 OCR 抖動）
        keep = [s for s in seq if s[2] >= 3]
        out = []
        for s in keep:
            if out and norm(out[-1][1]) == norm(s[1]):
                out[-1][2] += s[2]
            else:
                out.append(s)
        print(f"  任務欄位換了 {max(len(out)-1,0)} 次：")
        for s in out:
            print(f"   {s[0]}  {s[1]}（{s[2]} 列）")
        # 金幣：反射看到 裡的「字：N , NNN」
        gold = []
        for o in rows:
            m = re.search(r"字：[^｜]*?(\d) ?, ?(\d{3})(?!\d)", str(o.get("反射看到", "")))
            if m:
                gold.append((o.get("時間"), int(m.group(1)) * 1000 + int(m.group(2))))
        if gold:
            step = max(len(gold) // 14, 1)
            print("  金幣（OCR）：" + "、".join(f"{t[:5]} {v}" for t, v in gold[::step]) + f"、最後 {gold[-1][0][:5]} {gold[-1][1]}")
    if what == "epress":
        for i in range(1, len(rows)):
            last = rows[i]["_last"]
            sent = re.search(r"你送出：([^\n]*)", last)
            sent = sent.group(1) if sent else ""
            if not (("長按 E" in sent) or re.search(r"按 e \d", sent)):
                continue
            pv = rows[i - 1]
            res = re.search(r"結果：([^\n]*)", last)
            try:
                d = json.loads(str(pv.get("輸出", "")))
            except Exception:
                d = {}
            print(f"  {pv.get('時間')} [{pv.get('模式')}·E提示{pv['_ep']}] 看到「{str(d.get('s') or pv.get('反射看到'))[:46]}」想「{str(d.get('想',''))[:40]}」→ {sent[:40]}｜{(res.group(1) if res else '')[:50]}｜之後字：{rows[i]['_ocr'][:60]}")
