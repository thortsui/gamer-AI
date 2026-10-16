# -*- coding: utf-8 -*-
"""工具版統計（2026-10-15 使用者：「成功率那些的你需要回報給我 包含速度」）。

讀 專案/<遊戲>/記憶/工具版/YYYY-MM-DD.jsonl，逐項印：
  速度：輪數、每輪秒數（平均／中位／最慢）
  移動有反應率：前進／後退／左移／右移／跳 之後「畫面變化」≥2 的比例
  視角有反應率：視角左右上下／180度轉頭 之後畫面有變的比例、實測位移平均
  互動：按／長按 e 次數與有反應率；點擊：預約／拒絕／確認／取消／確認後有反應
  大任務：設了幾個、完成幾個；任務完成（遊戲右上換掉）幾件；死亡重生幾次
  失誤：JSON 無法解析輪、看不懂的工具、執行失敗、空轉（連續三輪同一工具同參數）
用法：python 工具版/stats.py [--game 動物醫院] [--date 2026-10-15] [--since HH:MM]
"""
import argparse
import json
import os
import re
import statistics
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MOVE = ("前進", "後退", "走", "退", "左移", "右移", "跳")
LOOK = ("視角左", "視角右", "視角上", "視角下", "180度轉頭", "轉", "仰")


def changed(ret):
    m = re.search(r"畫面變化 (\d+)", ret)
    if m and "跟背景差不多" in ret:          # 10/16 08:0x：比背景大才算有反應
        return False
    return (int(m.group(1)) >= 2) if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", default="動物醫院")
    ap.add_argument("--date", default="")
    ap.add_argument("--since", default="", help="只算這個時刻之後（HH:MM）")
    a = ap.parse_args()
    d = os.path.join(ROOT, "專案", a.game, "記憶", "工具版")
    date = a.date or sorted(f for f in os.listdir(d) if f.endswith(".jsonl"))[-1][:-6]
    recs = [json.loads(l) for l in open(os.path.join(d, date + ".jsonl"), encoding="utf-8")]
    # 10/16 04:3x：「不符沒檢討」原本取最後一筆的程序累計值（重啟歸零、不管 --since）→ 逐筆算增量
    prev_miss = 0
    for r in recs:
        cur = (r.get("符合統計") or {}).get("不符沒檢討")
        if cur is None:
            r["_miss"] = 0
            continue
        r["_miss"] = cur - prev_miss if cur >= prev_miss else cur   # 變小＝程序重啟、從 0 重算
        prev_miss = cur
    if a.since:
        recs = [r for r in recs if r["時間"] >= a.since]
    if not recs:
        print("沒有紀錄")
        return
    secs = [r["秒"] for r in recs]
    print(f"【{date}{'（' + a.since + ' 起）' if a.since else ''}】{recs[0]['時間']}～{recs[-1]['時間']}")
    print(f"速度：{len(recs)} 輪｜每輪 平均 {statistics.mean(secs):.1f}s、中位 {statistics.median(secs):.1f}s、最慢 {max(secs):.1f}s")

    use = Counter()
    move_ok = [0, 0]
    look_ok = [0, 0]
    shifts = []
    e_ok = [0, 0]
    click = Counter()
    click_ok = [0, 0]
    fails = Counter()
    big_set = big_done = task_done = respawn = 0
    seq = []
    for r in recs:
        if r["看到"] in ("(無法解析)", "") and not r["用"]:
            fails["JSON 無法解析／空輪"] += 1
        for ret in r["回傳"]:
            m = re.match(r"● (\S+?)(?:\((.*?)\))? → (.*)", ret, re.S)
            if not m:
                if ret.startswith("✗"):
                    fails["看不懂的工具"] += 1
                continue
            name, arg, body = m.group(1), m.group(2) or "", m.group(3)
            use[name] += 1
            seq.append((name, arg))
            if "執行失敗" in body:
                fails["執行失敗"] += 1
            if "未實作" in body:
                fails["叫到未實作"] += 1
            if name in MOVE:
                c = changed(body)
                if c is not None:
                    move_ok[1] += 1
                    move_ok[0] += int(c)
            elif name in LOOK:
                c = changed(body)
                if c is not None:
                    look_ok[1] += 1
                    look_ok[0] += int(c)
                s = re.search(r"移了 (\d+)% 畫面寬", body)
                if s:
                    shifts.append(int(s.group(1)))
            elif name in ("按", "長按") and (re.match(r"e\b", arg.strip().lower()) or not arg):   # 10/16 05:3x：原本 startswith("e") 把 esc 也算成 E
                c = changed(body)
                if c is not None:
                    e_ok[1] += 1
                    e_ok[0] += int(c)
            elif name in ("點", "點字"):
                click["預約"] += 1
                if "拒絕" in body:
                    click["被拒絕（碰到絕對限制）"] += 1
                if "沒有「" in body:
                    click["找不到字"] += 1
            elif name == "確認點":
                if "確認通過" in body:
                    click["確認點下去"] += 1
                    c = changed(body)
                    if c is not None:
                        click_ok[1] += 1
                        click_ok[0] += int(c)
                else:
                    click["確認被退回"] += 1
            elif name == "取消點":
                click["取消"] += 1
            elif name == "設大任務":
                big_set += 1
            elif name == "大任務完成":
                big_done += 1
            elif name == "任務完成":
                task_done += 1
            elif name == "死亡重生":
                respawn += 1
            if name == "確認點" and "確認通過" in body and re.search(r"再玩一次|重生|復活|Respawn", body + arg):
                respawn += 1          # 他自己確認點下「再玩一次／重生」＝死過一次（預約那一步不算，免得重複）
    idle = 0
    for i in range(2, len(seq)):
        if seq[i] == seq[i - 1] == seq[i - 2] and seq[i][0] in MOVE + LOOK:
            idle += 1
    # 05:00 他連轉右 23 次（度數不同）、「看到」九輪一字不差：同一方向連轉 ≥5 次、或「看到」連續 ≥5 輪相同，也算空轉
    run, spin = 1, 0
    for i in range(1, len(seq)):
        if seq[i][0] == seq[i - 1][0] and seq[i][0] in ("視角左", "視角右"):
            run += 1
            if run == 5:
                spin += 1
        else:
            run = 1
    # 死亡＝看到「你死了／再玩一次」的輪次，三分鐘內的算同一次（09:4x 核對：全天 9 次，之前靠確認點算只抓到 4 次）
    # 10/16 14:3x：14:09 理智 1%→14:11 直接回大廳、沒看到死亡面板 → 漏算。加：局內（看到有「輪班」）下一筆變大廳、
    # 最後看到的理智 ≤5% 也算死亡（跟面板三分鐘內的算同一次）
    respawn = 0
    last = None
    sanity, in_game = None, False
    for r in recs:
        sv = r["看到"] or ""
        h, m, s_ = (int(x) for x in r["時間"].split(":"))
        t = h * 3600 + m * 60 + s_
        died = bool(re.search(r"你死了|再玩一次", sv))
        lobby = ("大廳" in sv or "ANIMAL" in sv.upper()) and "輪班" not in sv
        if not died and in_game and lobby and sanity is not None and sanity <= 5:
            died = True
        if died:
            if last is None or t - last > 180:
                respawn += 1
            last = t
            in_game, sanity = False, None       # 看到面板＝這局結束；之後回大廳不再另算一次
            continue
        ms = re.search(r"(?:理智\s*(\d+)\s*%|(\d+)\s*%\s*[/、]?\s*\$|左下(?:理智)?\s*(\d+)\s*%)", sv)
        if ms:
            sanity = int(next(g for g in ms.groups() if g))
        if "輪班" in sv:
            in_game = True
        elif lobby:
            in_game, sanity = False, None
    same, stuck_seen = 1, 0
    for i in range(1, len(recs)):
        if recs[i]["看到"] and recs[i]["看到"] == recs[i - 1]["看到"]:
            same += 1
            if same == 5:
                stuck_seen += 1
        else:
            same = 1

    def rate(ok):
        return f"{ok[0]}/{ok[1]}（{ok[0] / ok[1] * 100:.0f}%）" if ok[1] else "0/0"

    print(f"移動有反應率：{rate(move_ok)}｜視角有反應率：{rate(look_ok)}"
          + (f"，實測位移平均 {statistics.mean(shifts):.0f}% 畫面寬" if shifts else ""))
    print(f"按 E 有反應率：{rate(e_ok)}｜點擊：" + "、".join(f"{k} {v}" for k, v in click.items()) + (f"，確認後有反應 {rate(click_ok)}" if click_ok[1] else "") if (e_ok[1] or click) else "按 E／點擊：還沒用過")
    print(f"大任務：設 {big_set}、完成 {big_done}｜遊戲任務完成：{task_done} 件｜死亡重生：{respawn} 次")
    # 10/16 05:3x：遊戲自己的進度（輪班數、錢）從「看到」字面抓；多人伺服器，不全是他做的
    shifts_seen, money_seen = [], []
    for r in recs:
        sv = r.get("看到") or ""
        m1 = re.search(r"輪班\s*(\d+)", sv)
        m2 = re.search(r"\$\s*([\d,]+)", sv)
        if m1:
            shifts_seen.append(int(m1.group(1)))
        if m1 and m2:                                  # 有「輪班」＝局內；大廳的金幣不算
            try:
                money_seen.append(int(m2.group(1).replace(",", "")))
            except ValueError:
                pass
    if shifts_seen or money_seen:
        print("遊戲進度（看到欄字面）："
              + (f"輪班 {shifts_seen[0]}→{shifts_seen[-1]}（最高 {max(shifts_seen)}）" if shifts_seen else "輪班 沒看到")
              + (f"、局內錢 ${money_seen[0]}→${money_seen[-1]}（最高 ${max(money_seen)}）" if money_seen else ""))
    print(f"失誤：" + ("、".join(f"{k} {v}" for k, v in fails.items()) or "無")
          + f"｜空轉：連三輪同一動作 {idle} 次、同方向連轉 5 次以上 {spin} 次、「看到」連續 5 輪沒變 {stuck_seen} 次")
    # 12:1x 使用者：原因／預期／實際三件事，不符就檢討 → 預期符合率、不符沒檢討次數、檢討次數
    ok = sum(1 for r in recs if r.get("符合") is True)
    no = sum(1 for r in recs if r.get("符合") is False)
    rev = use.get("檢討", 0)
    miss = sum(r.get("_miss", 0) for r in recs)
    if ok + no:
        print(f"預期 vs 實際：符合 {ok}、不符 {no}（符合率 {ok / (ok + no) * 100:.0f}%）｜檢討 {rev} 次｜不符卻沒檢討 {miss} 次")
    top = "、".join(f"{k} {v}" for k, v in use.most_common(12))
    print(f"工具使用：{sum(use.values())} 次｜{top}")
    doc = sum(v for k, v in use.items() if k in ("遊戲介紹", "讀文件", "我的記憶", "工具說明", "工具清單"))
    cal = sum(v for k, v in use.items() if k in ("開工測試", "量刻度", "量畫面", "量比例", "回報人稱"))
    print(f"文件類工具 {doc} 次｜校準類 {cal} 次｜自訂：新工具 {use.get('新工具', 0)}、補用途 {use.get('補用途', 0)}")


if __name__ == "__main__":
    main()
