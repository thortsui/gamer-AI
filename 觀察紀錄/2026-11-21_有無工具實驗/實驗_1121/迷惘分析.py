"""11/21 使用者：記錄他有沒有「不知道自己在幹什麼」的行為。
讀 rounds.jsonl＋shots，逐輪標出迷惘訊號，輸出 迷惘紀錄.md（摘要、每 30 分鐘分布、每類例子含截圖檔名）。
這些是程式標記的「可疑」，不是定論；例子要人看過截圖再下結論。
用法：python 迷惘分析.py [資料夾]（預設 sonnet_動物醫院）
"""
import json, os, re, sys
from collections import Counter, defaultdict
from PIL import Image, ImageChops, ImageStat

D = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "sonnet_動物醫院")
R = [json.loads(l) for l in open(os.path.join(D, "rounds.jsonl"), encoding="utf8")]
R = [r for r in R if "輪" in r]
SH = {int(f[:4]): f for f in os.listdir(os.path.join(D, "shots")) if f[:4].isdigit()}

UNSURE = ["不確定", "不知道", "不清楚", "搞不清", "不明", "迷路", "找不到", "卡住", "看不出", "不懂", "為什麼", "怎麼回事",
          "奇怪", "沒反應", "沒有反應", "沒效", "不對", "走錯", "搞錯", "誤", "？", "?"]
MOVE = re.compile(r"按\((w|a|s|d|space)")
TURN = re.compile(r"(拖|移滑鼠)\(")


def t(r):
    s = r["時間"]; return f"{s[:2]}:{s[2:4]}:{s[4:]}"


def acts(r):
    return [a.split("→")[0] for a in (r.get("動作結果") or [])]


def diff(a, b):
    try:
        A = Image.open(os.path.join(D, "shots", SH[a])).convert("L").resize((96, 54))
        B = Image.open(os.path.join(D, "shots", SH[b])).convert("L").resize((96, 54))
        return ImageStat.Stat(ImageChops.difference(A, B)).mean[0]
    except Exception:
        return None


flags = defaultdict(list)   # 類別 -> [(輪, 說明)]
by_r = {r["輪"]: r for r in R}

for i, r in enumerate(R):
    n = r["輪"]; th = r.get("想") or ""; seen = r.get("看到") or ""; A = acts(r)
    # 1 自己說不確定／沒反應／搞錯
    hit = [w for w in UNSURE if w in th or w in seen]
    if hit:
        flags["1 自己說不確定、沒反應或搞錯"].append((n, "、".join(hit[:4])))
    # 2 解析失敗、沒動作
    if r.get("解析失敗") or not A:
        flags["2 回覆壞掉或沒有動作"].append((n, r.get("錯誤", "")[:60] or "沒有動作"))
    # 3 說的和做的不一樣
    ae = " ".join(A)
    if re.search(r"按\s*E|長按\s*E|按E|E\s*鍵", th) and "按(e" not in ae and "按(E" not in ae:
        flags["3 想裡說要按 E、動作卻沒按"].append((n, th[:50]))
    if re.search(r"前進|往前走|走過去|靠近", th) and not MOVE.search(ae):
        flags["3 想裡說要走、動作卻沒移動"].append((n, th[:50]))
    if i == 0:
        continue
    p = R[i - 1]
    # 4 跟上一輪一模一樣的動作
    if A and A == acts(p):
        flags["4 連續兩輪一模一樣的動作"].append((n, " ".join(A)[:60]))
    # 5 畫面沒變卻以為有進展（動作有移動，前後截圖幾乎一樣）
    if MOVE.search(" ".join(acts(p))):
        d = diff(p["輪"], n)
        if d is not None and d < 2.0:
            flags["5 走了但畫面幾乎沒變"].append((n, f"差異 {d:.1f}"))
    # 6 來回反覆：上一輪 w 這輪 s，或 a/d 互換
    pa, ca = " ".join(acts(p)), ae
    for x, y in (("w", "s"), ("s", "w"), ("a", "d"), ("d", "a")):
        if f"按({x}" in pa and f"按({y}" in ca and f"按({x}" not in ca:
            flags["6 前後兩輪方向相反（來回）"].append((n, f"{x}→{y}")); break

# 7 只轉視角不移動連續 ≥4 輪（原地打轉）
run = []
for r in R + [{"輪": -1, "動作結果": ["按(w,1)"]}]:
    ae = " ".join(acts(r))
    if TURN.search(ae) and not MOVE.search(ae) and "按(e" not in ae and "點(" not in ae:
        run.append(r["輪"])
    else:
        if len(run) >= 4:
            flags["7 連續只轉視角不前進（原地打轉）"].append((run[0], f"{len(run)} 輪（{run[0]}～{run[-1]}）"))
        run = []

# 8 「看到」幾乎不變連續 ≥5 輪（卡在同一個地方）
def sim(a, b):
    sa, sb = set(a), set(b)
    return len(sa & sb) / max(1, len(sa | sb))
run = [R[0]["輪"]] if R else []
for i in range(1, len(R) + 1):
    if i < len(R) and sim(R[i].get("看到") or "", R[i - 1].get("看到") or "") > 0.7:
        run.append(R[i]["輪"])
    else:
        if len(run) >= 5:
            flags["8 連續 5 輪以上看到幾乎一樣（卡住）"].append((run[0], f"{len(run)} 輪（{run[0]}～{run[-1]}）"))
        run = [R[i]["輪"]] if i < len(R) else []

# 9 自評完成
for l in open(os.path.join(D, "自評完成.jsonl"), encoding="utf8") if os.path.exists(os.path.join(D, "自評完成.jsonl")) else []:
    j = json.loads(l)
    flags["9 自己宣告完成（要對截圖核實）"].append((j["輪"], j["完成"][:60]))

# 輸出
out = [f"# 迷惘紀錄：{os.path.basename(D)}", "",
       f"- 範圍：第 {R[0]['輪']}～{R[-1]['輪']} 輪，{t(R[0])}～{t(R[-1])}，共 {len(R)} 輪",
       "- 方法：程式從每輪的「看到／想／動作」和前後截圖標記可疑訊號；這是「可疑」不是定論，例子要對截圖看",
       "", "## 各類次數", "", "| 類別 | 次數 | 佔輪數 |", "|---|---|---|"]
for k in sorted(flags):
    out.append(f"| {k} | {len(flags[k])} | {len(flags[k]) * 100 // max(1, len(R))}% |")
# 每 30 分鐘
out += ["", "## 每 30 分鐘分布（每輪至少中一類就算一輪迷惘）", "", "| 時段 | 輪數 | 有迷惘訊號 | 比例 |", "|---|---|---|---|"]
hitr = {n for v in flags.values() for n, _ in v if not v is flags.get("9 自己宣告完成（要對截圖核實）")}
slot = defaultdict(lambda: [0, 0])
for r in R:
    h, m = int(r["時間"][:2]), int(r["時間"][2:4]); k = f"{h:02d}:{m // 30 * 30:02d}"
    slot[k][0] += 1; slot[k][1] += r["輪"] in hitr
for k in sorted(slot):
    a, b = slot[k]; out.append(f"| {k} | {a} | {b} | {b * 100 // max(1, a)}% |")
# 例子
out += ["", "## 每類例子（最多 12 筆；截圖在 shots/）", ""]
for k in sorted(flags):
    out += [f"### {k}", ""]
    for n, why in flags[k][:12]:
        r = by_r.get(n, {})
        out.append(f"- 第 {n} 輪 {t(r) if r else ''}（{SH.get(n, '')}）：{why}")
        if r:
            out.append(f"  - 看到：{(r.get('看到') or '')[:90]}")
            out.append(f"  - 想：{(r.get('想') or '')[:90]}")
            out.append(f"  - 動作：{'；'.join(acts(r))[:90]}")
    if len(flags[k]) > 12:
        out.append(f"- …其餘 {len(flags[k]) - 12} 筆在 迷惘標記.jsonl")
    out.append("")
open(os.path.join(D, "迷惘紀錄.md"), "w", encoding="utf8").write("\n".join(out))
with open(os.path.join(D, "迷惘標記.jsonl"), "w", encoding="utf8") as f:
    for k, v in flags.items():
        for n, why in v:
            f.write(json.dumps({"類別": k, "輪": n, "說明": why}, ensure_ascii=False) + "\n")
print("\n".join(out[:40]))
