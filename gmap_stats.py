# -*- coding: utf-8 -*-
"""
gmap_stats.py — 陣列地圖上線前後的對照（2026-09-30 使用者：「我要看是否改善」）。只讀，不動任何東西。

  python gmap_stats.py --game 動物醫院 --mark        # 在現在這個時間點畫一條線（上線那一刻做一次）
  python gmap_stats.py --game 動物醫院               # 印出「線之前 N 輪」對「線之後到現在」
  python gmap_stats.py --game 動物醫院 --out 觀察紀錄/陣列地圖對照.md   # 追加一段到報告

來源：記憶/行動日誌.jsonl（每輪列＋懲罰／獎勵列）、記憶/陣列地圖紀錄.jsonl（掃描、量比例、撞牆、核對、路線、物件）、
      記憶/陣列地圖_*.json（陣列本身）。
"""
import argparse
import glob
import json
import os
import sys
from collections import Counter
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
# 跟「走路／位置／地圖」有關的扣分：陣列地圖如果有用，這幾項每百輪的次數應該下降
MOVE_PEN = ("貼牆", "面壁", "沒有目的地就亂走", "一直待在原地", "原地空轉", "說了要去哪但沒走到", "走不動", "撞牆")


def load_rows(p):
    out = []
    try:
        for l in open(p, encoding="utf-8", errors="ignore"):
            if l.strip().startswith("{"):
                try:
                    out.append(json.loads(l))
                except json.JSONDecodeError:
                    out.append({})
    except OSError:
        pass
    return out


def summarize(rows):
    rounds = [r for r in rows if isinstance(r.get("耗時"), dict)]
    n = len(rounds) or 1
    pen = Counter()
    pen_pts = 0
    for r in rows:
        if r.get("模式") == "懲罰":
            nm = str(r.get("名") or r.get("項目") or r.get("原因") or r.get("what") or "")
            if not nm:
                nm = next((str(v) for k, v in r.items() if k not in ("時間", "模式") and isinstance(v, str)), "?")
            pen[nm[:14]] += 1
            try:
                pen_pts += abs(int(r.get("分") or r.get("扣") or r.get("分數") or 0))
            except (TypeError, ValueError):
                pass
    tot = [sum(v for v in r["耗時"].values() if isinstance(v, (int, float))) for r in rounds]
    fast = [r for r in rounds if r["耗時"].get("快思", 0) >= 10]
    move_pen = sum(v for k, v in pen.items() if any(m in k for m in MOVE_PEN))
    guided = sum(1 for r in rounds if r.get("地圖帶路"))
    said = sum(1 for r in rounds if str(r.get("腦內地圖") or "").startswith(("在:", "新:", "校正", "新增")))
    asked = sum(1 for r in rounds if "重問" in str(r.get("腦內地圖") or ""))
    return {
        "輪數": len(rounds),
        "每輪秒（平均）": round(sum(tot) / n, 1),
        "快思輪比例": f"{len(fast) * 100 // n}%",
        "陣列地圖每輪秒": round(sum(r["耗時"].get("陣列地圖", 0) for r in rounds) / n, 2),
        "登記": max([int(r.get("登記") or 0) for r in rounds] or [0]) - min([int(r.get("登記") or 0) for r in rounds] or [0]),
        "懲罰次數／百輪": round(sum(pen.values()) * 100 / n, 1),
        "走路與位置類懲罰／百輪": round(move_pen * 100 / n, 1),
        "地圖帶路輪數／百輪": round(guided * 100 / n, 1),
        "自己說出「我在」／百輪": round(said * 100 / n, 1),
        "要程式重問「我在」／百輪": round(asked * 100 / n, 1),
        "卡住檢討／百輪": round(sum(1 for r in rows if r.get("模式") == "卡住檢討") * 100 / n, 1),
        "成功回推／百輪": round(sum(1 for r in rows if r.get("模式") == "成功回推") * 100 / n, 1),
        "_懲罰明細": pen.most_common(10),
    }


def gmap_events(mem, since_line_time=None):
    ev = load_rows(os.path.join(mem, "陣列地圖紀錄.jsonl"))
    c = Counter(e.get("事件") for e in ev)
    scans = [e for e in ev if e.get("事件") == "掃描"]
    checks = [e for e in ev if e.get("事件") == "核對"]
    routes = [e for e in ev if e.get("事件") == "路線"]
    out = {"事件次數": dict(c)}
    if scans:
        out["掃描"] = f"{len(scans)} 次，平均 {sum(e.get('秒', 0) for e in scans) / len(scans):.1f} 秒，平均新畫 {sum(e.get('新牆', 0) for e in scans) / len(scans):.1f} 格牆"
    if checks:
        ok = sum(1 for e in checks if e.get("符合"))
        out["視覺核對"] = f"{len(checks)} 次，對得上 {ok} 次（{ok * 100 // len(checks)}%），平均誤差 {sum(e.get('誤差', 0) for e in checks) / len(checks):.2f} 步"
    if routes:
        found = [e for e in routes if e.get("結果") != "找不到路"]
        arrived = sum(1 for e in found if isinstance(e.get("離目標"), (int, float)) and e["離目標"] <= 1.5)
        out["照陣列帶路"] = f"{len(routes)} 次，找到路 {len(found)} 次，走到目標 1.5 步內 {arrived} 次"
    maps = []
    for p in glob.glob(os.path.join(mem, "陣列地圖_*.json")):
        if "基準線" in p:
            continue
        try:
            d = json.load(open(p, encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        flat = "".join(d.get("陣列") or [])
        maps.append(f"{os.path.basename(p)}：牆壁 {flat.count('2')}、空地 {flat.count('1')}、互動物 {flat.count('3')}、觸碰物 {flat.count('4')}、"
                    f"掃過 {len(d.get('掃過的位置') or [])} 處、深度比例 {d.get('深度比例')}")
    out["陣列檔"] = maps
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", default="動物醫院")
    ap.add_argument("--mark", action="store_true", help="在現在畫一條線：之前＝沒有陣列地圖，之後＝有")
    ap.add_argument("--before", type=int, default=1100, help="線之前取幾輪當基準")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    mem = os.path.join(HERE, "專案", args.game, "記憶")
    logp = os.path.join(mem, "行動日誌.jsonl")
    markp = os.path.join(mem, "陣列地圖_基準線.json")
    rows = load_rows(logp)
    if args.mark:
        json.dump({"行動日誌列數": len(rows), "時間": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
                  open(markp, "w", encoding="utf-8"), ensure_ascii=False)
        print(f"基準線畫在第 {len(rows)} 列（{datetime.now():%m/%d %H:%M:%S}）")
        return
    try:
        mark = json.load(open(markp, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        print("還沒畫基準線：先跑一次 --mark")
        return
    n0 = int(mark["行動日誌列數"])
    before_rows = rows[:n0]
    idx = [i for i, r in enumerate(before_rows) if isinstance(r.get("耗時"), dict)]
    start = idx[-args.before] if len(idx) > args.before else (idx[0] if idx else 0)
    a, b = summarize(before_rows[start:]), summarize(rows[n0:])
    lines = [f"## 陣列地圖前後對照（基準線 {mark['時間']}；{datetime.now():%m/%d %H:%M} 產生）", "",
             "| 指標 | 之前（沒有陣列地圖） | 之後（有） |", "|---|---|---|"]
    for k in a:
        if not k.startswith("_"):
            lines.append(f"| {k} | {a[k]} | {b[k]} |")
    lines += ["", "懲罰前十名（之前）：" + "、".join(f"{k}×{v}" for k, v in a["_懲罰明細"]),
              "懲罰前十名（之後）：" + "、".join(f"{k}×{v}" for k, v in b["_懲罰明細"]), ""]
    g = gmap_events(mem)
    lines.append("陣列地圖自己的紀錄：")
    for k, v in g.items():
        if isinstance(v, list):
            lines += [f"- {x}" for x in v]
        else:
            lines.append(f"- {k}：{v}")
    lines.append("")
    lines.append("注意：之前那段同時沒有「反射只回 ok」「看畫面不寫」（同一晚上線），每輪秒數的差別不全是陣列地圖造成的。")
    text = "\n".join(lines)
    print(text)
    if args.out:
        with open(os.path.join(HERE, args.out), "a", encoding="utf-8") as f:
            f.write(text + "\n\n")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
