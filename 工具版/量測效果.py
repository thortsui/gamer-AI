"""量測修正效果：python 工具版/量測效果.py [--file jsonl | --game 動物醫院 --date 2026-10-25] [--since HH:MM]
印：走 量不出比例、視角移動到 找不到／已對準比例、轉 分段實測一致率、長按 任務字變了次數、修水平 沒修好次數、中央釘著提示次數（全部／只算局內）。
10/25 工具迭代用：每輪監督拿它跟 工具迭代.md 的基準比。"""
import argparse
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ap = argparse.ArgumentParser()
ap.add_argument("--file", default="")
ap.add_argument("--game", default="動物醫院")
ap.add_argument("--date", default="")
ap.add_argument("--since", default="")
a = ap.parse_args()
d = os.path.join(ROOT, "專案", a.game, "記憶", "工具版")
f = a.file or os.path.join(d, (a.date or sorted(x for x in os.listdir(d) if re.fullmatch(r"\d{4}-\d\d-\d\d\.jsonl", x))[-1][:-6]) + ".jsonl")
R = [json.loads(l) for l in open(f, encoding="utf-8") if l.strip()]
if a.since:
    R = [r for r in R if r["時間"] >= a.since]


def seg(rows, label):
    c = {"走量不出": [0, 0], "視角移動到找不到": [0, 0], "視角移動到對準": [0, 0], "轉分段一致": [0, 0],
         "長按": 0, "長按任務字變了": 0, "修水平": 0, "修水平沒修好": 0, "中央釘著提示": 0}
    full = n = 0
    for r in rows:
        fl = r.get("符合") or []
        if len(fl) == 2:
            n += 1
            full += all(x is True for x in fl)
        for call, ret in zip(r.get("用", []), r.get("回傳", []) or []):
            t = call.split("(")[0]
            if t == "走":
                c["走量不出"][1] += 1
                c["走量不出"][0] += ("中央放大量不出來" in ret)
                c["中央釘著提示"] += ("走路時完全沒動" in ret)
            elif t == "視角移動到":
                c["視角移動到找不到"][1] += 1
                c["視角移動到找不到"][0] += ("找不到原本那一塊" in ret)
                c["視角移動到對準"][1] += 1
                c["視角移動到對準"][0] += ("已對準" in ret)
            elif t == "轉" and "分段" in ret:
                c["轉分段一致"][1] += 1
                c["轉分段一致"][0] += ("【二次驗證 一致】轉頭合計" in ret)
            elif t == "長按":
                c["長按"] += 1
                c["長按任務字變了"] += ("任務字變了" in ret)
            elif t == "修水平":
                c["修水平"] += 1
                c["修水平沒修好"] += ("沒修好" in ret)

    def pct(v):
        return f"{v[0]}/{v[1]}" + (f"（{v[0] * 100 // v[1]}%）" if v[1] else "")
    print(f"{label}：{len(rows)} 輪｜全符合 {full}/{n}｜走 量不出 {pct(c['走量不出'])}｜視角移動到 找不到 {pct(c['視角移動到找不到'])}、已對準 {pct(c['視角移動到對準'])}"
          f"｜轉 分段一致 {pct(c['轉分段一致'])}｜長按 {c['長按']} 次、任務字變了 {c['長按任務字變了']}｜修水平 {c['修水平']} 次、沒修好 {c['修水平沒修好']}｜中央釘著提示 {c['中央釘著提示']}")


seg(R, "全部")
seg([r for r in R if "輪班" in r.get("看到", "")], "局內")
