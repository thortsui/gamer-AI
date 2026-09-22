#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
daily_report.py — 讓代理自己寫「今天的進步日誌」

從當天的行動日誌、筆記、成績、死亡檢討算出客觀數字，與昨天比較，
再請 DGX 上的模型用第一人稱寫成日誌，存到 資料/<遊戲>/進步日誌/YYYY-MM-DD.md，
並更新 資料/<遊戲>/進步日誌/README.md 索引與 資料/<遊戲>/成績歷史.jsonl。

用法：
    python daily_report.py                    # 今天
    python daily_report.py --date 2026-09-07  # 指定日期（行動日誌只有時間沒有日期，僅用於命名）
    python daily_report.py --game 動物醫院
"""
import argparse
import collections
import json
import os
import re
import statistics
import sys
import time
import urllib.request
from datetime import datetime, timedelta

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))



def game_dir(root, game):
    """2026-09-14 專案結構：專案/<遊戲>/記憶；找不到就退回舊的 資料/<遊戲>。"""
    import os
    new = os.path.join(root, "專案", game, "記憶")
    return new if os.path.isdir(new) else os.path.join(root, "資料", game)

def load_cfg():
    try:
        with open(os.path.join(HERE, "設定.json"), encoding="utf-8") as f:
            return json.load(f)
    except OSError:
        return {"端點": "http://192.168.0.126:8888/v1"}


def chat(endpoint, system, user, max_tokens=1200, temperature=0.3):
    model = ""
    with urllib.request.urlopen(endpoint.rstrip("/") + "/models", timeout=10) as r:
        model = json.loads(r.read().decode("utf-8"))["data"][0]["id"]
    payload = {"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
               "max_tokens": max_tokens, "temperature": temperature, "chat_template_kwargs": {"enable_thinking": False}}
    req = urllib.request.Request(endpoint.rstrip("/") + "/chat/completions",
                                 data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=400) as r:
        return json.loads(r.read().decode("utf-8"))["choices"][0]["message"]["content"].strip()


def collect(d):
    """讀當天資料，回傳統計 dict。"""
    rows = []
    path = os.path.join(d, "行動日誌.jsonl")
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except OSError:
        pass
    modes = collections.Counter(r.get("模式") for r in rows)
    dec = [r.get("決策秒") for r in rows if r.get("模式") == "決策" and r.get("決策秒")]
    stuck = sum(1 for r in rows if any(k in (r.get("看到") or "")
                                       for k in ("天花板", "卡牆", "仰視", "貼牆", "地磚", "朝下", "卡住")))
    notes = [l.strip()[2:] for l in open(os.path.join(d, "研究筆記.md"), encoding="utf-8")
             if l.startswith("- ")] if os.path.exists(os.path.join(d, "研究筆記.md")) else []
    try:
        score = json.load(open(os.path.join(d, "成績.json"), encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        score = {}
    death = open(os.path.join(d, "死亡檢討.md"), encoding="utf-8").read() if os.path.exists(os.path.join(d, "死亡檢討.md")) else ""
    return {
        "總輪數": len(rows),
        "決策": modes.get("決策", 0), "看清楚": modes.get("看清楚", 0),
        "脫困走位": modes.get("脫困走位", 0), "強制離開重進": modes.get("強制離開重進", 0),
        "死亡檢討": modes.get("死亡檢討", 0), "思考": modes.get("思考", 0),
        "wiki": modes.get("wiki開始", 0), "youtube": modes.get("youtube開始", 0),
        "決策秒中位": round(statistics.median(dec), 2) if dec else None,
        "付費視窗": sum(1 for r in rows if r.get("robux") == 1),
        "擋掉動作": sum(len(r.get("擋掉") or []) for r in rows),
        "卡住輪": stuck, "卡住比例": round(100 * stuck / len(rows), 1) if rows else 0,
        "筆記數": len(notes), "最新筆記": [n.split("  (")[0] for n in notes[-15:]],
        "成績": score, "死亡檢討內容": death,
        "區間": f"{rows[0]['時間']}→{rows[-1]['時間']}" if rows else "-",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", default="動物醫院")
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    args = ap.parse_args()
    cfg = load_cfg()
    d = game_dir(HERE, args.game)
    out_dir = os.path.join(d, "進步日誌")
    os.makedirs(out_dir, exist_ok=True)

    today = collect(d)
    hist_path = os.path.join(d, "成績歷史.jsonl")
    prev = None
    if os.path.exists(hist_path):
        for line in open(hist_path, encoding="utf-8"):
            try:
                r = json.loads(line)
                if r.get("日期") != args.date:
                    prev = r
            except json.JSONDecodeError:
                pass

    def diff(k, sub=None):
        a = today["成績"].get(k) if sub == "成績" else today.get(k)
        if not prev:
            return f"{a}（第一天）"
        b = (prev.get("成績") or {}).get(k) if sub == "成績" else prev.get(k)
        if a is None or b is None:
            return str(a)
        try:
            dd = a - b
            return f"{a}（{'+' if dd >= 0 else ''}{dd}）"
        except TypeError:
            return str(a)

    table = "\n".join([
        f"| 總輪數 | {diff('總輪數')} |",
        f"| 最高輪班 | {diff('最高輪班', '成績')} |",
        f"| 最高金幣 | {diff('最高金幣', '成績')} |",
        f"| 輪班報告次數 | {diff('輪班報告次數', '成績')} |",
        f"| 死亡次數 | {diff('死亡檢討')} |",
        f"| 卡住比例 | {today['卡住比例']}%（前次 {prev['卡住比例'] if prev else '-'}%） |",
        f"| 筆記條數 | {diff('筆記數')} |",
        f"| 付費視窗／擋掉動作 | {today['付費視窗']} / {today['擋掉動作']}（花費 0） |",
        f"| 自己思考 think / 查 wiki / 看影片 | {today['思考']} / {today['wiki']} / {today['youtube']} |",
        f"| 決策秒中位 | {today['決策秒中位']} |",
    ])

    sysmsg = (f"你是 gamer AI，正在玩 Roblox「{args.game}」。使用者要你寫「今天的進步日誌」。"
              "只根據下面自己的數字與筆記，用第一人稱、繁體中文寫 350 字內，分四段："
              "1) 今天做了什麼；2) 我比昨天進步的地方（用數字說）；3) 我還是做不好的地方；"
              "4) 明天要試的三件具體事。不知道就說不確定，不要編造。只輸出純文字。")
    user = (f"今天（{args.date}）的數字：\n{json.dumps({k: v for k, v in today.items() if k not in ('最新筆記', '死亡檢討內容')}, ensure_ascii=False, indent=1)}\n\n"
            f"昨天的數字：\n{json.dumps({k: v for k, v in (prev or {}).items() if k not in ('最新筆記', '死亡檢討內容')}, ensure_ascii=False, indent=1) if prev else '（沒有昨天的紀錄，今天是第一天）'}\n\n"
            f"今天的最新筆記：\n" + "\n".join(f"- {n}" for n in today["最新筆記"]) +
            f"\n\n死亡檢討：\n{today['死亡檢討內容'][-1500:]}")
    try:
        text = chat(cfg["端點"], sysmsg, user)
    except Exception as e:  # noqa: BLE001
        text = f"（模型無法回應：{e}）"

    md = (f"# {args.date} 進步日誌（{args.game}）\n\n"
          f"> 由 gamer AI 自己寫；數字由 `daily_report.py` 從當天行動日誌算出。運行區間 {today['區間']}。\n\n"
          f"| 項目 | 今天（與前次相比） |\n|---|---|\n{table}\n\n## 它自己寫的\n\n{text}\n")
    out_path = os.path.join(out_dir, f"{args.date}.md")
    open(out_path, "w", encoding="utf-8").write(md)

    rec = {"日期": args.date, **{k: v for k, v in today.items() if k not in ("最新筆記", "死亡檢討內容")}}
    with open(hist_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    idx = os.path.join(out_dir, "README.md")
    if not os.path.exists(idx):
        open(idx, "w", encoding="utf-8").write(f"# 「{args.game}」進步日誌\n\n每天一篇，由代理自己寫，數字由程式算。\n\n| 日期 | 最高輪班 | 最高金幣 | 死亡 | 卡住% | 筆記 |\n|---|---|---|---|---|---|\n")
    s = today["成績"]
    with open(idx, "a", encoding="utf-8") as f:
        f.write(f"| [{args.date}]({args.date}.md) | {s.get('最高輪班', '-')} | {s.get('最高金幣', '-')} | {today['死亡檢討']} | {today['卡住比例']} | {today['筆記數']} |\n")
    print(md)
    print(f"→ 已寫入 {out_path}")


if __name__ == "__main__":
    main()
