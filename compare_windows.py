# -*- coding: utf-8 -*-
"""改善前後比較表（2026-10-12 使用者：「給我改善前後的 成功率 速度 任務完成輪數…的比較表」）

用法：python compare_windows.py --game 動物醫院 --cut 15:03 [--cut 12:27 ...] [--out 觀察紀錄/xxx.md]
資料來源：行動日誌.jsonl（每輪）、拆兩段紀錄.jsonl、任務紀錄.jsonl、放棄／卡牆／走過頭檢討（行動日誌裡的模式）。
視窗：前一段（上次開跑那一天）、今天各個 --cut 切出來的區段。只讀不改。
"""
import argparse, io, json, os, statistics, sys, re
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))


def load_jsonl(p):
    if not os.path.exists(p):
        return []
    out = []
    for l in io.open(p, encoding="utf-8"):
        l = l.strip()
        if not l:
            continue
        try:
            out.append(json.loads(l))
        except json.JSONDecodeError:
            pass
    return out


def tsec(t):
    try:
        h, m, s = str(t).split(":")
        return int(h) * 3600 + int(m) * 60 + int(s)
    except Exception:  # noqa: BLE001
        return None


def split_days(rows):
    """行動日誌沒有日期：時間往回跳就是換天。回傳 [[rows...], ...] 最後一個是今天。"""
    days, cur, prev = [], [], None
    for r in rows:
        t = tsec(r.get("時間"))
        if t is None:
            cur.append(r)
            continue
        if prev is not None and t < prev - 3600:      # 倒退超過一小時＝新的一天
            days.append(cur)
            cur = []
        cur.append(r)
        prev = t
    if cur:
        days.append(cur)
    return days


def window_stats(rows, two, tasks, label, t0=None, t1=None, thoughts=None):
    def inwin(t):
        s = tsec(t)
        if s is None:
            return False
        if t0 is not None and s < t0:
            return False
        if t1 is not None and s >= t1:
            return False
        return True

    rounds = [r for r in rows if str(r.get("模式", "")).startswith("流程:") and inwin(r.get("時間"))]
    secs = [float(r.get("秒") or 0) for r in rounds if r.get("秒")]
    fast = [float((r.get("耗時") or {}).get("快思") or 0) for r in rounds if (r.get("耗時") or {}).get("快思")]
    holds = ok = 0
    acts = Counter()
    for r in rounds:
        for a in r.get("動作") or []:
            if not isinstance(a, dict):
                continue
            t = a.get("t")
            if t == "hold":
                holds += 1
                if "成功" in str(a.get("結果", "")):
                    ok += 1
            if t == "key" and str(a.get("k", "")).lower() in ("w", "a", "s", "d"):
                acts["走"] += 1
            elif t == "look":
                acts["轉"] += 1
            elif t in ("hold", "click", "點字"):
                acts["按"] += 1
    noact = sum(1 for r in rounds if not (r.get("動作") or []))
    modes = Counter(str(r.get("模式", "")) for r in rows if inwin(r.get("時間")))
    pen = sum(int(r.get("扣分") or 0) for r in rows if r.get("模式") == "懲罰" and inwin(r.get("時間")))
    rew = sum(int(r.get("加分") or 0) for r in rows if r.get("模式") == "獎勵" and inwin(r.get("時間")))
    # 想 欄位（思考流程.jsonl 有存）：平均字數、四欄比例
    thinks = [str(r.get("想") or "") for r in (thoughts or []) if r.get("想") and inwin(r.get("時間"))]
    think_len = statistics.mean(len(x) for x in thinks) if thinks else None
    four = sum(1 for x in thinks if x.count("｜") >= 3) / len(thinks) if thinks else None
    # 拆兩段
    tw = [r for r in two if inwin(r.get("時間"))]
    tw_block = sum(1 for r in tw if "畫面沒變" in str(r.get("結果", "")) or "叫停" in str(r.get("結果", "")))
    tw_go = sum(1 for r in tw if "說繼續" in str(r.get("結果", "")))
    tw_ask = sum(1 for r in tw if r.get("問了他"))
    # 任務紀錄
    tk = [r for r in tasks if inwin(r.get("時間"))]
    tk_done = [r for r in tk if r.get("判定") == "完成"]
    tk_lost = [r for r in tk if r.get("判定") in ("換局", "重置")]
    done_rounds = statistics.mean(r.get("花了幾輪", 0) for r in tk_done) if tk_done else None
    lost_rounds = statistics.mean(r.get("花了幾輪", 0) for r in tk_lost) if tk_lost else None
    n = len(rounds)
    per_hour = (lambda v: f"{v / (n / 3600 * (statistics.mean(secs) if secs else 50)):.1f}/時" if n else "—")
    return {
        "區段": label,
        "輪數": n,
        "每輪秒（平均）": f"{statistics.mean(secs):.1f}" if secs else "—",
        "快思秒（中位）": f"{statistics.median(fast):.1f}" if fast else "—",
        "想 字數／四欄比例": (f"{think_len:.0f} 字／{100 * four:.0f}%" if think_len is not None else "（沒存）"),
        "沒動作輪": noact,
        "按 E 次／成功": f"{holds}／{ok}" + (f"（{100 * ok // holds}%）" if holds else ""),
        "任務完成 件／平均輪": (f"{len(tk_done)}／{done_rounds:.0f}" if tk_done else f"{len(tk_done)}／—") if tk is not None else "—",
        "任務沒完成 件／平均輪": f"{len(tk_lost)}／{lost_rounds:.0f}" if tk_lost else f"{len(tk_lost)}／—",
        "拆兩段 次／問他／答繼續／被擋": f"{len(tw)}／{tw_ask}／{tw_go}／{tw_block}",
        "卡住檢討": modes.get("卡住檢討", 0),
        "死亡檢討": modes.get("死亡檢討", 0),
        "放棄／卡牆／走過頭檢討": f"{modes.get('放棄檢討', 0)}／{modes.get('卡牆檢討', 0)}／{modes.get('走過頭檢討', 0)}",
        "扣分／加分": f"{pen}／{rew}",
        "動作分布 走／轉／按": f"{acts['走']}／{acts['轉']}／{acts['按']}",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", default="動物醫院")
    ap.add_argument("--cut", action="append", default=[], help="HH:MM，今天切成幾段（可多個）")
    ap.add_argument("--labels", default="", help="逗號分隔，給今天各段的名字")
    ap.add_argument("--out", default="")
    ap.add_argument("--prev", action="store_true", help="也算上一次開跑那一天")
    args = ap.parse_args()
    d = os.path.join(HERE, "專案", args.game, "記憶")
    rows = load_jsonl(os.path.join(d, "行動日誌.jsonl"))
    two_all = load_jsonl(os.path.join(d, "拆兩段紀錄.jsonl"))
    tasks_all = load_jsonl(os.path.join(d, "任務紀錄.jsonl"))
    thoughts = load_jsonl(os.path.join(d, "思考流程.jsonl"))
    days = split_days(rows)
    today = days[-1] if days else []
    tdays = split_days(thoughts)
    thoughts_today = tdays[-1] if tdays else []
    # 拆兩段／任務紀錄也要按天切（2026-10-13 修：跨天之後把昨天的也算進今天）
    twod = split_days(two_all)
    taskd = split_days(tasks_all)
    two = twod[-1] if twod else []
    tasks = taskd[-1] if taskd else []
    two_prev = twod[-2] if len(twod) >= 2 else []
    tasks_prev = taskd[-2] if len(taskd) >= 2 else []
    cuts = sorted(tsec(c + ":00") for c in args.cut)
    labels = [x.strip() for x in args.labels.split(",") if x.strip()]
    wins = []
    if args.prev and len(days) >= 2:
        wins.append(window_stats(days[-2], two_prev, tasks_prev, "前一天整天", thoughts=(tdays[-2] if len(tdays) >= 2 else [])))
    edges = [None] + cuts + [None]
    for i in range(len(edges) - 1):
        lab = labels[i] if i < len(labels) else f"今天 第{i + 1}段"
        wins.append(window_stats(today, two, tasks, lab, edges[i], edges[i + 1], thoughts_today))
    wins = [w for w in wins if w["輪數"]] or wins          # 空的時段（例如午夜切出來的）不列
    keys = [k for k in wins[0].keys() if k != "區段"]
    out = ["| 項目 | " + " | ".join(w["區段"] for w in wins) + " |", "|---|" + "---|" * len(wins)]
    for k in keys:
        out.append(f"| {k} | " + " | ".join(str(w[k]) for w in wins) + " |")
    text = "\n".join(out)
    print(text)
    if args.out:
        with io.open(os.path.join(HERE, args.out), "a", encoding="utf-8") as f:
            f.write("\n" + text + "\n")


if __name__ == "__main__":
    main()
