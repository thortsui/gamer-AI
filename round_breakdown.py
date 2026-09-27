# -*- coding: utf-8 -*-
"""
round_breakdown.py — 拆解他每一輪的時間花在哪、用了哪些工具、叫了幾層模型（子代理）。只讀，不動任何東西。

  python round_breakdown.py --game 動物醫院 --last 60
  python round_breakdown.py --game 動物醫院 --last 60 --out 訓練資料/finetune/每輪拆解.md   # 追加一段到報告

來源：記憶/行動日誌.jsonl 的每輪列（2026-09-27 起有 耗時／要的工具／模型），加上 慢想／檢討 列。
「子代理」＝這個架構裡每輪由程式叫起來的各層模型呼叫：反射（看畫面）、快思（決定動作）、反射重問（快思沒答完時）、
慢想（每 12 輪）、卡住／扣分／死亡檢討。他自己不會開子代理；眼睛（YOLO）與 OCR 是本機執行緒，不算模型。
"""
import argparse
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL_STAGES = {"反射": "反射（看畫面）", "快思": "快思（決定動作）", "反射重問": "反射重問（快思沒答完）"}
REVIEW_MODES = ("慢想", "卡住檢討", "扣分檢討", "死亡檢討", "成功回推", "整理學習")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", default="動物醫院")
    ap.add_argument("--last", type=int, default=60, help="最近 N 分鐘")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    p = os.path.join(HERE, "專案", args.game, "記憶", "行動日誌.jsonl")
    rows = [json.loads(l) for l in open(p, encoding="utf-8", errors="ignore") if l.strip().startswith("{")]
    # 只有 HH:MM:SS：從檔案時間往回推日期
    day = datetime.fromtimestamp(os.path.getmtime(p)).replace(hour=0, minute=0, second=0, microsecond=0)
    t_end = datetime.now()
    t0 = t_end - timedelta(minutes=args.last)
    nxt, sel = None, []
    for r in reversed(rows):
        try:
            h, m, s = (int(x) for x in str(r.get("時間", "")).split(":")[:3])
        except ValueError:
            continue
        t = day.replace(hour=h, minute=m, second=s)
        if nxt and t > nxt + timedelta(hours=6):
            day -= timedelta(days=1)
            t = day.replace(hour=h, minute=m, second=s)
        nxt = t
        if t < t0:
            break
        sel.append(r)
    sel.reverse()
    rounds = [r for r in sel if str(r.get("模式", "")).startswith("流程:") and "耗時" in r]
    reviews = Counter(r["模式"] for r in sel if r.get("模式") in REVIEW_MODES)
    n = len(rounds)
    if not n:
        print("這段時間沒有帶耗時的輪（2026-09-27 12:3x 以後才開始記）")
        return
    stage_sum, stage_n = defaultdict(float), Counter()
    for r in rounds:
        for k, v in (r.get("耗時") or {}).items():
            stage_sum[k] += float(v)
            stage_n[k] += 1
    total = sum(float(r.get("秒") or 0) for r in rounds)
    tools = Counter(t for r in rounds for t in (r.get("要的工具") or []))
    per_round_tools = Counter(len(r.get("要的工具") or []) for r in rounds)
    model_calls = sum(stage_n[k] for k in MODEL_STAGES) + sum(reviews.values())
    models = Counter(r.get("模型", "") for r in rounds)

    L = [f"## 每輪拆解 {t0:%m/%d %H:%M}–{t_end:%H:%M}（{n} 輪，模型 {dict(models)}）", "",
         f"每輪平均 **{total / n:.1f} 秒**", "", "| 環節 | 平均秒（每輪） | 佔比 | 有發生的輪數 |", "|---|---|---|---|"]
    for k in sorted(stage_sum, key=lambda k: -stage_sum[k]):
        L.append(f"| {MODEL_STAGES.get(k, k)} | {stage_sum[k] / n:.1f} | {100 * stage_sum[k] / max(total, 1):.0f}% | {stage_n[k]} |")
    L += ["", f"**工具**：共要了 {sum(tools.values())} 次，每輪平均 {sum(tools.values()) / n:.2f} 個；"
          f"每輪個數分布 {dict(sorted(per_round_tools.items()))}", "",
          "| 工具 | 次數 |", "|---|---|"]
    L += [f"| {t} | {c} |" for t, c in tools.most_common()]
    L += ["", f"**子代理（模型呼叫）**：共 {model_calls} 次，每輪平均 {model_calls / n:.2f} 次。"
          f"反射 {stage_n['反射']}、快思 {stage_n['快思']}、反射重問 {stage_n['反射重問']}；"
          + "、".join(f"{k} {v}" for k, v in reviews.items()) + "。",
          "他自己不會開子代理；上面這些是程式每輪依序叫起來的各層。眼睛（YOLO）與 OCR 是本機執行緒，不算模型呼叫。", ""]
    # 腦內地圖、反射、登記、他自己記的問題
    MAP_TOOLS = ("我在", "標點", "到標點", "走到", "走到記過的", "拍地標", "記位置", "記站位", "切空間")
    map_n = sum(tools[t] for t in MAP_TOOLS)
    L += [f"**腦內地圖**：用了 {map_n} 次（" + "、".join(f"{t} {tools[t]}" for t in MAP_TOOLS if tools[t]) + "）"
          + ("" if map_n else "——這段完全沒用"),
          "**強制腦內地圖（每輪「我在」）**：" + (lambda c: "、".join(f"{k} {v}" for k, v in c.items()) or "（沒開或還沒資料）")(
              Counter(("重問後" if str(r.get("腦內地圖", "")).startswith("重問後") else "")
                      + (str(r.get("腦內地圖", "")).replace("重問後", "").split(":")[0] or "（空）")
                      for r in rounds if "腦內地圖" in r)),
          "**要去（地圖帶路）**：" + (lambda c: "、".join(f"{k} {v}" for k, v in c.most_common(8)) or "（沒有）")(
              Counter(str(r.get("腦內地圖", "")).split("要去:")[1] for r in rounds if "要去:" in str(r.get("腦內地圖", "")))),
          f"**反射**：{stage_n['反射']}/{n} 輪都有跑；他喊「換快思」{tools['換快思']} 次。",
          f"**登記（報到成功）**：這段最後累計 {max([int(r.get('登記') or 0) for r in rounds] or [0])} 次。"]
    pp = os.path.join(HERE, "專案", args.game, "記憶", "遇到的問題.jsonl")
    probs = []
    if os.path.exists(pp):
        for l in open(pp, encoding="utf-8"):
            try:
                q = json.loads(l)
                if datetime.strptime(f"{t_end.year}/{q['時間']}", "%Y/%m/%d %H:%M:%S") >= t0:
                    probs.append(q)
            except (ValueError, KeyError):
                pass
    src = Counter(q.get("來源", "他自己") for q in probs)
    L.append(f"**遇到的問題**：{len(probs)} 筆（" + ("、".join(f"{k} {v}" for k, v in src.items()) or "這段沒有") + "）")
    L += [f"- {q['時間']}｜［{q.get('來源', '他自己')}］{q.get('在做什麼', '')}｜{q['問題']}"
          + (f"｜原因：{q['原因']}" if q.get("原因") else "") + (f"｜試過：{q['試過']}" if q.get("試過") else "")
          for q in probs]
    L.append("")
    text = "\n".join(L)
    print(text)
    if args.out:
        with open(args.out, "a", encoding="utf-8") as f:
            f.write(text + "\n")


if __name__ == "__main__":
    main()
