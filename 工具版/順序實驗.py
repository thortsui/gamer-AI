# -*- coding: utf-8 -*-
"""10/28 使用者實驗「context 順序差異：重要的事情在頭／中／尾，看哪一個任務成功率最高」。
代理用 --ctx-rotate 60 每小時輪流，每輪紀錄的 順序 欄寫 頭／中／尾；這支按 順序 分組印同一套量測。
用法：python 工具版/順序實驗.py --date 2026-10-28 [--since 01:20]
"""
import argparse, json, os, statistics, sys

sys.path.insert(0, os.path.dirname(__file__))
ap = argparse.ArgumentParser()
ap.add_argument("--date", required=True)
ap.add_argument("--since", default="")
ap.add_argument("--game", default="動物醫院")
a = ap.parse_args()
HERE = os.path.dirname(os.path.abspath(__file__))
p = os.path.join(HERE, "..", "專案", a.game, "記憶", "工具版", f"{a.date}.jsonl")
R = [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]
if a.since:
    _i = next((i for i, r in enumerate(R) if r["時間"] >= a.since), None)
    R = R[_i:] if _i is not None else []
R = [r for r in R if r.get("順序")]


def stat(rows):
    n2 = full = 0
    hold = hold_ok = 0
    ev = sum(1 for r in rows if r.get("任務字換"))
    dead = sum(1 for r in rows if "你死了" in str(r.get("看到", "")))
    rev = sum(1 for r in rows for x in (r.get("先記") or []) if str(x).startswith("檢討"))
    secs = [r.get("秒") for r in rows if isinstance(r.get("秒"), (int, float))]
    sent_done = sum(1 for r in rows for x in (r.get("用") or []) if str(x).startswith("任務完成"))
    for r in rows:
        fl = r.get("符合") or []
        if len(fl) == 2:
            n2 += 1
            full += all(x is True for x in fl)
        for call, ret in zip(r.get("用", []), r.get("回傳", []) or []):
            if call.split("(")[0] == "長按":
                hold += 1
                hold_ok += ("沒變" not in ret and "沒反應" not in ret)
    return {"輪": len(rows), "局內": sum(1 for r in rows if "輪班" in str(r.get("看到", ""))), "全符合": (full, n2),
            "長按有反應": (hold_ok, hold), "任務字換": ev, "送任務完成": sent_done, "死亡": dead, "檢討": rev,
            "每輪中位秒": round(statistics.median(secs), 1) if secs else None}


def fmt(k, d):
    f, n = d["全符合"]; h, ht = d["長按有反應"]
    return (f"{k}：{d['輪']} 輪（局內 {d['局內']}）｜全符合 {f}/{n}" + (f"（{f * 100 // n}%）" if n else "")
            + f"｜長按有反應 {h}/{ht}｜任務字換 {d['任務字換']}｜送任務完成 {d['送任務完成']}｜死亡 {d['死亡']}｜檢討 {d['檢討']}｜每輪中位 {d['每輪中位秒']} 秒")


for k in ("頭", "中", "尾"):
    rows = [r for r in R if r.get("順序") == k]
    if rows:
        print(fmt(k, stat(rows)), f"｜時間 {rows[0]['時間']}～{rows[-1]['時間']}")
    else:
        print(f"{k}：還沒有資料")
