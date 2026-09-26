# -*- coding: utf-8 -*-
"""
ab_stats.py — 從 專案/<遊戲>/記憶/ 的日誌算一段時間內的實戰指標，拿來比 Flash-Next 與微調 27B（A/B）。

  python ab_stats.py --game 動物醫院 --from "09/26 14:14" --to "09/26 15:45" --label Flash-Next
  python ab_stats.py --game 動物醫院 --last 30 --label 27B-ft --append 訓練資料/finetune/AB對照.md

來源（stdout.log 每次開機會被覆蓋，所以不用它）：
  行動日誌.jsonl   每輪一筆（模式 流程:…、有 動作／秒）＋ 懲罰／死亡檢討 列；只有 HH:MM:SS，日期從檔案時間往回推
  關鍵動作.jsonl   成功進場（有日期）
指標：輪數、每輪平均秒、純上下轉頭輪%、沒動作輪%、成功進場、死亡、懲罰總分、斷線輪、最高輪班。只讀，不動任何東西。
"""
import argparse
import json
import os
import re
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))


def jl(path):
    rows = []
    if os.path.exists(path):
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return rows


def with_dates(rows, path):
    """只有 HH:MM:SS 的列：從檔案修改時間的日期往回推（時間往前跳超過 6 小時＝前一天）。"""
    day = datetime.fromtimestamp(os.path.getmtime(path)).replace(hour=0, minute=0, second=0, microsecond=0)
    out, nxt = [None] * len(rows), None
    for i in range(len(rows) - 1, -1, -1):
        try:
            h, m, s = (int(x) for x in str(rows[i].get("時間", "")).split(":")[:3])
        except ValueError:
            continue
        t = day.replace(hour=h, minute=m, second=s)
        if nxt is not None and t > nxt + timedelta(hours=6):
            day -= timedelta(days=1)
            t = day.replace(hour=h, minute=m, second=s)
        out[i], nxt = t, t
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", default="動物醫院")
    ap.add_argument("--from", dest="t0", default="")
    ap.add_argument("--to", dest="t1", default="")
    ap.add_argument("--last", type=int, default=0, help="最近 N 分鐘")
    ap.add_argument("--label", default="")
    ap.add_argument("--append", default="", help="把一行結果追加到這個 md（表格列）")
    args = ap.parse_args()
    now = datetime.now()
    if args.last:
        t0, t1 = now - timedelta(minutes=args.last), now
    else:
        t0 = datetime.strptime(f"{now.year}/{args.t0}", "%Y/%m/%d %H:%M")
        t1 = datetime.strptime(f"{now.year}/{args.t1}", "%Y/%m/%d %H:%M")
    mem = os.path.join(HERE, "專案", args.game, "記憶")
    p = os.path.join(mem, "行動日誌.jsonl")
    rows = jl(p)
    when = with_dates(rows, p)
    m = {"輪數": 0, "秒": 0.0, "純上下": 0, "沒動作": 0, "死亡": 0, "懲罰": 0, "斷線": 0, "輪班": 0}
    for r, t in zip(rows, when):
        if t is None or not (t0 <= t <= t1):
            continue
        mode = str(r.get("模式", ""))
        if mode.startswith("流程:") and "動作" in r:
            m["輪數"] += 1
            m["秒"] += float(r.get("秒") or 0)
            acts = r.get("動作") or []
            if isinstance(acts, list):
                looks = [a for a in acts if isinstance(a, dict) and a.get("t") == "look"]
                if looks and all(not a.get("dx") and a.get("dy") for a in looks) and len(looks) == len(acts):
                    m["純上下"] += 1
                if not acts:
                    m["沒動作"] += 1
            else:
                act = str(acts)
                if re.search(r"轉視角 0,-?\d+", act) and not re.search(r"轉視角 -?[1-9]\d*,", act):
                    m["純上下"] += 1
                if act.strip() in ("沒動作", ""):
                    m["沒動作"] += 1
            seen = str(r.get("看到", ""))
            if "斷線" in seen or "連線中斷" in seen:
                m["斷線"] += 1
            zm = re.search(r"輪班\s?(\d+)", seen)
            if zm:
                m["輪班"] = max(m["輪班"], int(zm.group(1)))
        elif mode == "懲罰":
            m["懲罰"] += int(r.get("扣分") or 0)
        elif mode == "死亡檢討":
            m["死亡"] += 1
    entries = 0
    for r in jl(os.path.join(mem, "關鍵動作.jsonl")):
        try:
            t = datetime.strptime(f"{now.year}/{r['時間']}", "%Y/%m/%d %H:%M:%S")
        except (KeyError, ValueError):
            continue
        if t0 <= t <= t1 and "進場" in str(r.get("項目", "")):
            entries += 1
    n = max(1, m["輪數"])
    row = {"標籤": args.label, "區間": f"{t0:%m/%d %H:%M}–{t1:%H:%M}", "輪數": m["輪數"],
           "每輪秒": round(m["秒"] / n, 1), "純上下%": round(100 * m["純上下"] / n, 1),
           "沒動作%": round(100 * m["沒動作"] / n, 1), "進場": entries, "死亡": m["死亡"],
           "懲罰": m["懲罰"], "斷線輪": m["斷線"], "最高輪班": m["輪班"]}
    print(json.dumps(row, ensure_ascii=False))
    if args.append:
        new = not os.path.exists(args.append)
        with open(args.append, "a", encoding="utf-8") as f:
            if new:
                f.write("| 標籤 | 區間 | 輪數 | 每輪秒 | 純上下% | 沒動作% | 進場 | 死亡 | 懲罰 | 斷線輪 | 最高輪班 |\n"
                        "|---|---|---|---|---|---|---|---|---|---|---|\n")
            f.write("| " + " | ".join(str(row[k]) for k in row) + " |\n")


if __name__ == "__main__":
    main()
