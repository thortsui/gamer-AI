# -*- coding: utf-8 -*-
"""10/23 實驗分枝 exp-ask-claude：Claude 回答 問Claude 的題目（之後要退回）。

  python 工具版/回答問題.py 列            ← 列出還沒回答的題目（含畫面路徑）
  python 工具版/回答問題.py <題號> "<回答>" [--img 圖] [--box x1,y1,x2,y2 ...] [--caption 圖說]
     --box：在題目的畫面（或 --img 給的圖）上畫紅框，存成 問Claude畫面/<題號>_答.png 附給他
  題號 0＝Claude 主動告訴他（不是他問的）
"""
import argparse
import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
D = os.path.join(os.path.dirname(HERE), "專案", "動物醫院", "記憶", "工具版")


def load(f):
    try:
        return [json.loads(x) for x in open(os.path.join(D, f), encoding="utf-8") if x.strip()]
    except OSError:
        return []


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "列":
        done = {a["id"] for a in load("問Claude_回答.jsonl")}
        for q in load("問Claude.jsonl"):
            if q["id"] not in done:
                print(f"第 {q['id']} 題 {q['時間']}｜小任務:{q.get('小任務', '')}｜{q['問題']}｜畫面:{q.get('畫面', '')}")
        return
    ap = argparse.ArgumentParser()
    ap.add_argument("id", type=int)
    ap.add_argument("answer")
    ap.add_argument("--img", default="")
    ap.add_argument("--box", nargs="*", default=[])
    ap.add_argument("--caption", default="紅框是答案說的位置")
    a = ap.parse_args()
    q = next((x for x in load("問Claude.jsonl") if x["id"] == a.id), {})
    if a.id and not q:
        sys.exit(f"沒有第 {a.id} 題")
    if a.id in {x["id"] for x in load("問Claude_回答.jsonl")}:
        sys.exit(f"第 {a.id} 題已經回答過")
    img = os.path.abspath(a.img or q.get("畫面", ""))
    out = ""
    if a.box:
        from PIL import Image, ImageDraw
        im = Image.open(img).convert("RGB")
        im = im.resize((1024, int(1024 * im.height / im.width)))
        dr = ImageDraw.Draw(im)
        for b in a.box:
            x1, y1, x2, y2 = [float(v) for v in b.split(",")]
            dr.rectangle((x1 * im.width, y1 * im.height, x2 * im.width, y2 * im.height), outline=(255, 0, 0), width=4)
        os.makedirs(os.path.join(D, "問Claude畫面"), exist_ok=True)
        out = os.path.join(D, "問Claude畫面", f"{a.id}_答.png")
        im.save(out)
    elif a.img:
        out = img
    rec = {"id": a.id, "時間": datetime.now().strftime("%m/%d %H:%M:%S"), "問題": q.get("問題", ""), "回答": a.answer,
           "圖": out, "圖說": a.caption if out else ""}
    with open(os.path.join(D, "問Claude_回答.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print("已回答", json.dumps(rec, ensure_ascii=False))


if __name__ == "__main__":
    main()
