# -*- coding: utf-8 -*-
"""
gen_rule_qa.py — 把 訓練資料/finetune/A_*.jsonl（核心文件切好的段落）交給 DGX 上的模型生成問答，
當作 fine-tune「A 規則內化」那一類的教材。

  python gen_rule_qa.py            # 讀 A_train + A_val，各段生成 4～6 組，寫到 訓練資料/finetune/A_qa_{train,val}.jsonl
  python gen_rule_qa.py --limit 5  # 先試 5 段看品質

規則（2026-09-26 使用者定的）：例子只能用通用名詞，不准出現任何一隻遊戲的東西；生成後再用同一份名詞清單過濾一次。
"""
import argparse
import json
import os
import sys
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "訓練資料", "finetune")
sys.path.insert(0, HERE)
from build_finetune_set import has_game_word, chat  # noqa: E402

ENDPOINT = "http://192.168.0.126:8888/v1/chat/completions"
MODEL = "qwen3.8-flash-next"

SYS = ("你在幫一個玩 Roblox 的 AI 代理出教材。給你一段它的基礎操作規則，請把這段規則拆成 4～6 組問答，"
       "讓它讀完就能在任何一款遊戲裡照做。要求：\n"
       "1. 問法要混合：直問（這種情況該怎麼做？）、情境（描述一個畫面，問它現在是什麼狀態／該做什麼）、反向（如果做了 X 會怎樣？）。\n"
       "2. 答案要短、要具體、要可以照做；規則裡有數字的，答案不要背數字，改講「為什麼」與「怎麼確認」。\n"
       "3. 只能用通用名詞（門、牆、按鈕、面板、地板、天花板、角色、道具、狀態條…），"
       "絕對不准出現任何一款遊戲的名字、角色、物件、任務名。\n"
       "4. 只輸出 JSON：{\"qa\":[{\"q\":\"…\",\"a\":\"…\"},…]}，不要別的字。")

SYS_STUDENT = "你是在玩 Roblox 的代理。回答關於基礎操作的問題，答案要短、具體、可以照做。"


def ask(text, tries=3):
    body = {"model": MODEL, "temperature": 0.4, "max_tokens": 1500,
            "messages": [{"role": "system", "content": SYS},
                         {"role": "user", "content": "規則段落：\n" + text}],
            "chat_template_kwargs": {"enable_thinking": False}}
    for i in range(tries):
        try:
            r = requests.post(ENDPOINT, json=body, timeout=180)
            r.raise_for_status()
            s = r.json()["choices"][0]["message"]["content"]
            s = s[s.index("{"): s.rindex("}") + 1]
            qa = json.loads(s).get("qa") or []
            return [x for x in qa if isinstance(x, dict) and x.get("q") and x.get("a")]
        except Exception as e:  # noqa: BLE001
            print(f"   第 {i + 1} 次失敗：{e}")
            time.sleep(3)
    return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    for split in ("train", "val"):
        src = os.path.join(OUT, f"A_{split}.jsonl")
        if not os.path.exists(src):
            continue
        rows = [json.loads(l) for l in open(src, encoding="utf-8")]
        if args.limit:
            rows = rows[: args.limit]
        out, n_drop = [], 0
        t0 = time.time()
        for i, r in enumerate(rows, 1):
            qa = ask(r["text"])
            for x in qa:
                if has_game_word(x["q"]) or has_game_word(x["a"]):
                    n_drop += 1
                    continue
                out.append(chat(SYS_STUDENT, x["q"], x["a"], **{"class": "A", "file": r["file"], "title": r["title"]}))
            print(f"[{split}] {i}/{len(rows)} {r['title'][:24]} → {len(qa)} 組（累計 {len(out)}，丟 {n_drop}）"
                  f"　{time.time() - t0:.0f}s", flush=True)
        with open(os.path.join(OUT, f"A_qa_{split}.jsonl"), "w", encoding="utf-8") as f:
            for r in out:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"→ A_qa_{split}.jsonl：{len(out)} 筆（丟掉含遊戲名詞 {n_drop}）")


if __name__ == "__main__":
    main()
