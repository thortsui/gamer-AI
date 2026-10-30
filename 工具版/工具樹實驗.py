# -*- coding: utf-8 -*-
"""10/29 使用者實驗「把工具分成之後再給他，把移動那些分為好幾層工具，讓他一層一層往下找，減少 Context 的使用」。
代理用 --tree-rotate N 每 N 分鐘輪流 樹／全列，每輪紀錄的 工具模式 欄寫 樹／全列；這支按 工具模式 分組印同一套量測，
多加 context 大小（system字＋面板字）、打開 次數、叫錯工具名、用過幾種工具。
用法：python 工具版/工具樹實驗.py --date 2026-10-29 [--since 09:30]
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
    _i = next((i for i, r in enumerate(R) if r["時間"][-8:] >= a.since), None)
    R = R[_i:] if _i is not None else []
R = [r for r in R if r.get("工具模式")]


def med(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return round(statistics.median(xs), 1) if xs else None


def stat(rows):
    n2 = full = 0
    hold = hold_ok = 0
    opens = bad = 0
    used = set()
    for r in rows:
        fl = r.get("符合") or []
        if len(fl) == 2:
            n2 += 1
            full += all(x is True for x in fl)
        for call, ret in zip(r.get("用", []), r.get("回傳", []) or []):
            name = str(call).split("(")[0]
            used.add(name)
            opens += name == "打開"
            if name == "長按":
                hold += 1
                hold_ok += ("沒變" not in ret and "沒反應" not in ret)
        bad += sum(1 for x in (r.get("回傳") or []) if str(x).startswith("✗") and "看不懂" in str(x)[:40])  # 10/30 只算被退回的，工具說明裡的字不算
    return {"輪": len(rows), "局內": sum(1 for r in rows if "輪班" in str(r.get("看到", ""))), "全符合": (full, n2),
            "長按有反應": (hold_ok, hold), "任務字換": sum(1 for r in rows if r.get("任務字換")),
            "送任務完成": sum(1 for r in rows for x in (r.get("用") or []) if str(x).startswith("任務完成")),
            "死亡": sum(1 for r in rows if "你死了" in str(r.get("看到", ""))),
            "每輪中位秒": med([r.get("秒") for r in rows]), "system字": med([r.get("system字") for r in rows]),
            "面板字": med([r.get("面板字") for r in rows]), "打開": opens, "叫錯": bad, "用過工具種類": len(used - {"打開"})}


def fmt(k, d):
    f, n = d["全符合"]; h, ht = d["長按有反應"]
    return (f"{k}：{d['輪']} 輪（局內 {d['局內']}）｜全符合 {f}/{n}" + (f"（{f * 100 // n}%）" if n else "")
            + f"｜長按有反應 {h}/{ht}｜任務字換 {d['任務字換']}｜送任務完成 {d['送任務完成']}｜死亡 {d['死亡']}"
            + f"｜每輪中位 {d['每輪中位秒']} 秒｜system 中位 {d['system字']} 字＋面板 {d['面板字']} 字"
            + f"｜打開 {d['打開']} 次｜叫錯 {d['叫錯']}｜用過 {d['用過工具種類']} 種工具")


for k in ("樹", "全列"):
    rows = [r for r in R if r.get("工具模式") == k]
    if rows:
        print(fmt(k, stat(rows)), f"｜時間 {rows[0]['時間'][-8:]}～{rows[-1]['時間'][-8:]}")
    else:
        print(f"{k}：還沒有資料")
