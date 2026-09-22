#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sum_game.py — 讓他自己把「這隻遊戲目前研究到怎麼玩」整理成一份文件

使用者 2026-09-16 要求：每隻遊戲都要有一份他自己寫的玩法總結，放在那隻遊戲自己的資料夾。

- 輸入：只有**他自己累積的東西**（知識/*.md、學習整理、成功做法、卡住與扣分檢討、
  因果、地圖與空間、最近幾則自我回報）。不餵任何外部攻略，也不餵別的遊戲的資料。
- 輸出：`專案/<遊戲>/記憶/我理解的遊戲.md`（覆寫，檔頭有時間戳與資料來源）。
- 內容是他寫的，程式只負責蒐集素材與存檔。

用法：
    python sum_game.py                 # 所有 專案/*（每隻遊戲各一份）
    python sum_game.py --game 新遊戲    # 只做一隻
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime

from play_agent import HERE, stream_json, 快思
from screen_watch import load_config

ASK = """【整理時間】把你到現在為止對「{game}」的研究，整理成一份給自己看的文件。

規則：
1. **只能寫你自己看過、試過、記錄裡有的東西。** 沒驗證過的不要寫成事實。
2. 分成四段：
   - 這是什麼遊戲（你看到的畫面與機制，一兩句）
   - **怎麼玩（我已經驗證過的）**：一步一步寫，要具體到可以照著做
   - 我還不確定的：列出來，每條後面寫「要怎麼驗證」
   - 下次進去先做什麼：三件事以內，照順序
3. 寫給**以後的自己**看：以後的你不記得今天，所以不要寫「如同上面」「那個按鈕」這種話，
   要寫清楚是哪個東西、在哪裡、按什麼。

只輸出 Markdown 內文（不要用 ``` 包起來，不要寫標題以外的客套話）。"""


def read(path, limit=4000):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()[:limit]
    except OSError:
        return ""


def gather(game):
    """把他自己累積的東西蒐集起來（只有這隻遊戲的）。"""
    d = os.path.join(HERE, "專案", game, "記憶")
    if not os.path.isdir(d):
        d = os.path.join(HERE, "資料", game)          # 舊結構
    if not os.path.isdir(d):
        return None, None
    parts = []
    kd = os.path.join(d, "知識")
    if os.path.isdir(kd):
        for fn in sorted(os.listdir(kd)):
            if fn.endswith(".md"):
                body = read(os.path.join(kd, fn), 1500).strip()
                if body:
                    parts.append(f"【知識/{fn}】\n{body}")
    for fn, cap in (("學習整理.md", 2500), ("操作手冊.md", 1200), ("因果.md", 1500),
                    ("工作流程.md", 1500), ("卡住檢討.md", 1500), ("死亡檢討.md", 1200),
                    ("差異筆記.md", 800), ("成功檢討.md", 2000), ("學到的.md", 2000)):
        body = read(os.path.join(d, fn), cap).strip()
        if body:
            parts.append(f"【{fn}】\n{body}")
    for fn in ("成功做法.json", "空間.json", "互動點.json", "地圖.json"):
        body = read(os.path.join(d, fn), 1200).strip()
        if body and body not in ("{}", "[]"):
            parts.append(f"【{fn}】\n{body}")
    rep = read(os.path.join(d, "自我回報.md"), 100000)
    if rep:
        parts.append("【最近幾則自我回報】\n" + rep[-2500:])
    return d, "\n\n".join(parts)


def one(game, cfg):
    d, material = gather(game)
    if not d:
        print(f"[{game}] 找不到記憶資料夾，跳過")
        return False
    if not (material or "").strip():
        print(f"[{game}] 他還沒累積任何東西，沒得整理")
        return False
    print(f"[{game}] 素材 {len(material)} 字 → 請他整理…")
    t0 = time.time()
    # 素材有一萬多字，xhigh 的思考會把 token 全部吃光（實測一個字都寫不出來）→
    # 這件事本來也不需要極深思考：改用快思、把額度放大。
    raw, trunc, _, usage = stream_json(
        cfg["端點"], cfg.get("模型名") or "qwen3.8-flash-next", "",
        [{"type": "text", "text": "這些是你自己累積的紀錄：\n\n" + material
                                  + "\n\n" + ASK.format(game=game)}],
        6000, time.time() + 420.0, 0.3, effort=快思)
    body = (raw or "").strip()
    if body.startswith("```"):
        body = body.strip("`")
        body = body.split("\n", 1)[-1] if "\n" in body else body
    if len(body) < 80:
        print(f"[{game}] 他沒寫出東西（{len(body)} 字），這次跳過"
              f"　截斷={trunc}　usage={usage}")
        return False
    out = os.path.join(d, "我理解的遊戲.md")
    head = (f"# 我理解的「{game}」（我自己整理，{datetime.now().strftime('%Y-%m-%d %H:%M')}）\n\n"
            f"> 這份是**我自己**根據自己的紀錄寫的，沒有看任何攻略。\n"
            f"> 素材：知識/、學習整理、操作手冊、因果、檢討、成功做法、地圖與空間、自我回報。\n\n")
    with open(out, "w", encoding="utf-8") as f:
        f.write(head + body + "\n")
    print(f"[{game}] ✅ 寫好了（{len(body)} 字、{time.time() - t0:.0f} 秒）→ {out}")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", default="", help="只做這一隻；留空＝所有 專案/*")
    args = ap.parse_args()
    cfg = load_config()
    games = [args.game] if args.game else sorted(
        g for g in os.listdir(os.path.join(HERE, "專案"))
        if os.path.isdir(os.path.join(HERE, "專案", g)) and not g.startswith(("_", ".")))
    if not games:
        print("找不到任何遊戲專案")
        return 1
    print(f"要整理的遊戲：{'、'.join(games)}")
    ok = 0
    for g in games:
        try:
            ok += 1 if one(g, cfg) else 0
        except Exception as e:  # noqa: BLE001
            print(f"[{g}] 整理失敗：{e}")
    print(f"完成 {ok}/{len(games)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
