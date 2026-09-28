# -*- coding: utf-8 -*-
"""
inout_stats.py — 用 Roblox 用戶端自己的紀錄算「場內／場外時間」與「每一局平均到第幾輪班」。只讀，不動任何東西。

  python inout_stats.py            # 印出各模型階段的比較表
  python inout_stats.py --out 訓練資料/finetune/場內場外.md

場內／場外：Roblox log 每次傳送都有「! Joining game ... place <id>」與時間戳（UTC）。
  大廳 place＝場外，遊戲內 place＝場內（遊戲.json 的 places；第一個是大廳）。
  兩次 Joining 之間就是待在那個 place 的時間；超過 30 分鐘沒有任何 log 的空檔（Roblox 沒開／當掉）不算。
每局輪班：一次「進到場內 → 離開場內」算一局；這段時間裡代理看到的最大「輪班 N」＝這局到第幾輪班。
  用「每局平均」而不是「最高」，避免某一局被隊友帶到很後面的輪班把數字拉高。
"""
import argparse
import glob
import json
import os
import re
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
TZ = timedelta(hours=8)          # log 是 UTC，換成台灣時間
JOIN = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)\.\d+Z.*! Joining game '[^']*' place (\d+)")
STAMP = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)\.\d+Z")

PHASES = [  # (名稱, 開始, 結束) 台灣時間
    ("Flash-Next", "2026-09-26 02:35", "2026-09-26 13:40"),
    ("27B FP8", "2026-09-27 02:16", "2026-09-27 09:04"),
    ("27B NVFP4 09/27", "2026-09-27 09:40", "2026-09-27 21:00"),
    ("27B NVFP4 09/28", "2026-09-27 23:24", "2026-09-28 21:00"),
]


def load_events():
    """所有 Player log 的 Joining 事件與「最後一行時間」（用來知道 Roblox 什麼時候停了）。"""
    d = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Roblox", "logs")
    joins, spans = [], []
    for p in glob.glob(os.path.join(d, "*Player*.log")):
        first = last = None
        with open(p, encoding="utf-8", errors="ignore") as f:
            for line in f:
                m = STAMP.match(line)
                if not m:
                    continue
                t = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S") + TZ
                first = first or t
                last = t
                j = JOIN.match(line)
                if j:
                    joins.append((t, j.group(2)))
        if first:
            spans.append((first, last))
    joins.sort()
    return joins, spans


def with_dates(rows, path):
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
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    cfg = json.load(open(os.path.join(HERE, "專案", args.game, "遊戲.json"), encoding="utf-8"))
    lobby = str(cfg.get("大廳place"))
    inside = {k for k in (cfg.get("places") or {}) if k != lobby}

    joins, spans = load_events()
    # 每輪看到的「輪班 N」（代理自己的行動日誌）
    lp = os.path.join(HERE, "專案", args.game, "記憶", "行動日誌.jsonl")
    rows = [json.loads(l) for l in open(lp, encoding="utf-8", errors="ignore") if l.strip().startswith("{")]
    when = with_dates(rows, lp)
    shifts = []
    for r, t in zip(rows, when):
        m = re.search(r"輪班\s?(\d+)", str(r.get("看到", "")))
        if t and m and str(r.get("模式", "")).startswith("流程:"):
            shifts.append((t, int(m.group(1))))

    def alive(t):
        return any(a <= t <= b for a, b in spans)

    # 把 join 事件切成區段：[開始, 結束) 在哪個 place
    segs = []
    for (t0, pl), nxt in zip(joins, joins[1:] + [(None, None)]):
        t1 = nxt[0] if nxt[0] else max(b for _, b in spans)
        # 這段 log 最後一行之後 Roblox 沒在跑的部分切掉
        ends = [b for a, b in spans if a <= t0 <= b]
        if ends:
            t1 = min(t1, max(ends))
        segs.append((t0, t1, pl))

    L = ["| 階段 | 統計小時 | 場內小時 | 場外小時 | 場內比例 | 進場局數 | 每局平均分鐘 | 每局平均到第幾輪班 | 中位數 | 有看到輪班的局數 |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for name, a, b in PHASES:
        A, B = datetime.strptime(a, "%Y-%m-%d %H:%M"), datetime.strptime(b, "%Y-%m-%d %H:%M")
        tin = tout = 0.0
        games = []
        for t0, t1, pl in segs:
            s, e = max(t0, A), min(t1, B)
            if e <= s:
                continue
            dur = (e - s).total_seconds()
            if pl in inside:
                tin += dur
                if A <= t0 <= B:
                    mx = max([n for t, n in shifts if t0 <= t <= t1] or [0])
                    games.append((dur, mx))
            elif pl == lobby:
                tout += dur
        tot = tin + tout
        seen = [mx for _, mx in games if mx]
        seen_sorted = sorted(seen)
        med = seen_sorted[len(seen_sorted) // 2] if seen_sorted else 0
        L.append(f"| {name} | {tot / 3600:.1f} | {tin / 3600:.1f} | {tout / 3600:.1f} | "
                 f"{100 * tin / tot if tot else 0:.0f}% | {len(games)} | "
                 f"{(sum(d for d, _ in games) / len(games) / 60) if games else 0:.1f} | "
                 f"{(sum(seen) / len(seen)) if seen else 0:.1f} | {med} | {len(seen)} |")
    text = "\n".join(L)
    print(text)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write("# 場內／場外時間與每局輪班（" + datetime.now().strftime("%m/%d %H:%M") + "，inout_stats.py）\n\n" + text + "\n")


if __name__ == "__main__":
    main()
