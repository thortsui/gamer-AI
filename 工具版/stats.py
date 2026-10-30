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
    if m and "（沒變" in ret.split("；")[0] + ret[:80]:   # 10/18 12:1x：變化 1.6 顯示成「2」但程式判「沒變」→ 以程式的判斷為準
        return False
    return (int(m.group(1)) >= 2) if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", default="動物醫院")
    ap.add_argument("--date", default="")
    ap.add_argument("--since", default="", help="只算這個時刻之後（HH:MM）")
    ap.add_argument("--file", default="", help="直接讀這個 jsonl（10/24 模型比較實驗：每個模型那一小時切出來的檔）")
    a = ap.parse_args()
    d = os.path.join(ROOT, "專案", a.game, "記憶", "工具版")
    if a.file:
        date = os.path.splitext(os.path.basename(a.file))[0]
        recs = [json.loads(l) for l in open(a.file, encoding="utf-8") if l.strip()]
    else:
        date = a.date or sorted(f for f in os.listdir(d) if re.fullmatch(r"\d{4}-\d\d-\d\d\.jsonl", f))[-1][:-6]   # 10/23：問Claude.jsonl 不算
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
        _i = next((i for i, r in enumerate(recs) if r["時間"] >= a.since), None)   # 10/27：跨午夜用檔案順序
        recs = recs[_i:] if _i is not None else []
    if not recs:
        print("沒有紀錄")
        return
    secs = [r["秒"] for r in recs]
    print(f"【{date}{'（' + a.since + ' 起）' if a.since else ''}】{recs[0]['時間']}～{recs[-1]['時間']}")
    print(f"速度：{len(recs)} 輪｜每輪 平均 {statistics.mean(secs):.1f}s、中位 {statistics.median(secs):.1f}s、最慢 {max(secs):.1f}s")
    think_time(recs)

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
                if ret.startswith("✗"):  # 10/30 拆開：叫錯工具名 vs 被程式擋下（先記放遊戲動作、格式不對等）
                    fails["看不懂的工具" if "看不懂" in ret[:40] else "被程式擋下"] += 1
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
        # 10/27 09:0x：08:59 看到「左海報你死了」（遊戲牆上的海報）被算成死亡 → 海報／招牌／看板／告示 後面接的「你死了」不算
        died = bool(re.search(r"(?<!海報)(?<!招牌)(?<!看板)(?<!告示)(你死了|再玩一次)", sv)) and not re.search(r"(海報|招牌|看板|告示)[^，；。、]{0,4}你死了", sv)
        # 10/26 18:2x：18:18 理智 5%→回大廳漏算——他寫「道具欄5%」、回大廳那輪只寫「商店邀請職業日誌按鈕列」沒寫「大廳」。
        # 改：程式側 右上字（任務框 OCR）有字＝局內；這輪 右上字 空而且看到大廳 HUD 字（商店／邀請／職業／日誌／最高轉移速度／配對踏板）＝大廳
        tw = r.get("右上字")
        lobby_hud = bool(re.search(r"大廳|商店|邀請|職業|日誌|最高轉移速度|配對踏板|角色扮演", sv)) or "ANIMAL" in sv.upper()
        lobby = lobby_hud and "輪班" not in sv and not tw
        if not died and in_game and lobby and sanity is not None and sanity <= 5:
            died = True
        if died:
            if last is None or t - last > 180:
                respawn += 1
            last = t
            in_game, sanity = False, None       # 看到面板＝這局結束；之後回大廳不再另算一次
            continue
        # 10/18 08:1x：他寫「底左1%理智」「5%理智」（數字在前）→ 漏算 08:08 那次死亡，加 (\d+)%理智
        ms = re.search(r"(?:理智\s*(\d+)\s*%|(\d+)\s*%\s*理智|(\d+)\s*%\s*[/、]?\s*\$|左下(?:理智)?\s*(\d+)\s*%)", sv)
        if ms:
            sanity = int(next(g for g in ms.groups() if g))
        else:
            # 10/26 18:2x：「道具欄5%」「左下精神12%」這種寫法——整段只有一個百分比就當理智
            pcs = [int(x) for x in re.findall(r"(\d+)\s*%", sv) if int(x) <= 100]
            if len(pcs) == 1:
                sanity = pcs[0]
        if "輪班" in sv or tw:
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
    trunc = sum(1 for r in recs if r.get("截斷"))
    print(f"截斷：{trunc} 輪（{trunc / len(recs) * 100:.0f}%）")
    task_changes(recs)
    review_stats(recs)
    plan3_stats(recs)
    verify2_stats(recs)


def _trim(vals):
    """去極端值（IQR 法：Q1−1.5×IQR～Q3+1.5×IQR 之外的丟掉）→ (留下的, 丟掉的)。"""
    if len(vals) < 4:
        return list(vals), []
    q = statistics.quantiles(vals, n=4)
    lo, hi = q[0] - 1.5 * (q[2] - q[0]), q[2] + 1.5 * (q[2] - q[0])
    keep = [v for v in vals if lo <= v <= hi]
    return keep, [v for v in vals if not lo <= v <= hi]


def think_time(recs):
    """10/18 使用者「思考時間也要回報（去除極端值）」：大腦想的秒數（jsonl「秒」）＋一輪總時間（相鄰兩輪的時間差＝想＋做三步），都去極端值。"""
    def line(name, vals):
        if not vals:
            return f"{name}：沒有資料"
        keep, out = _trim(vals)
        return (f"{name}（去極端值）：平均 {statistics.mean(keep):.1f}s、中位 {statistics.median(keep):.1f}s、"
                f"範圍 {min(keep):.0f}～{max(keep):.0f}s｜去掉 {len(out)} 個" + (f"（{'、'.join(f'{v:.0f}' for v in sorted(out))}s）" if out else ""))
    gaps = []
    for a, b in zip(recs, recs[1:]):
        g = _hms(b["時間"]) - _hms(a["時間"])
        if 0 < g < 1800:                       # 重啟、停機的空檔不算
            gaps.append(float(g))
    print(line("思考時間", [r["秒"] for r in recs]))
    print(line("一輪總時間（想＋做）", gaps))


def _hms(t):
    h, m, s_ = (int(x) for x in t.split(":"))
    return h * 3600 + m * 60 + s_


HUD_WORDS = ("輪班", "理智", "保安", "道具", "金幣", "商店", "邀請", "職業", "日誌", "啟用", "重生", "繼續")


def task_changes(recs):
    """10/18 使用者「stats 加任務字換了幾次、隔多久」：每輪程式 OCR 右上／右側的字（jsonl「右上字」），
    出現一段原本沒有的字（4 個字以上、不是 HUD）而且下一輪還在 → 算任務字換一次。"""
    import difflib
    new_rows = [r for r in recs if r.get("任務字讀法")]
    if new_rows:            # 10/19 起：程式裁右上任務列放大讀，每輪記「任務字換」事件（連兩輪讀到、前面沒出現過）
        events = [(r["時間"], r["任務字換"]) for r in new_rows if r.get("任務字換")]
        read = sum(1 for r in new_rows if r.get("右上字"))
        sent = done = 0
        for r in new_rows:
            for ret in r.get("回傳") or []:
                if ret.startswith("● 任務完成"):
                    sent += "還沒算" in ret
                    done += "記下了" in ret
        line = f"任務字（裁切讀法）：{len(new_rows)} 輪有 {read} 輪讀到字｜換了 {len(events)} 次"
        if len(events) >= 2:
            gaps = [(_hms(events[i][0]) - _hms(events[i - 1][0])) / 60 for i in range(1, len(events))]
            line += f"，平均隔 {statistics.mean(gaps):.0f} 分鐘（最短 {min(gaps):.0f}、最長 {max(gaps):.0f}）"
        line += f"｜他送 任務完成：第一次 {sent}、回顧後記下 {done}"
        if events:
            line += "｜" + "、".join(f"{t[:5]}「{e['舊'][:8]}→{e['新'][:8]}」" for t, e in events[-8:])
        print(line)
        recs = [r for r in recs if not r.get("任務字讀法")]
        if not recs:
            return
        print("（以下是舊讀法的輪）")
    rows = [r for r in recs if "右上字" in r]
    if not rows:
        print("任務字：這段沒有「右上字」紀錄（10/18 起才有）")
        return

    def norm(t):
        return re.sub(r"[\s\d\W_]+", "", t)

    def words(r):
        return {norm(w[0]) for w in r.get("右上字") or [] if len(norm(w[0])) >= 4 and not any(h in w[0] for h in HUD_WORDS)}

    def known(w, pool):
        return any(w in p or p in w or difflib.SequenceMatcher(None, w, p).ratio() >= 0.6 for p in pool)

    seen_recent, events = [], []
    for i, r in enumerate(rows):
        cur = words(r)
        pool = set().union(*seen_recent[-5:]) if seen_recent else set()
        new = [w for w in cur if not known(w, pool)]
        if new and seen_recent and i + 1 < len(rows) and any(known(w, words(rows[i + 1])) for w in new):
            events.append((r["時間"], new[0]))
        if cur:
            seen_recent.append(cur)
    if not events:
        print(f"任務字：換了 0 次（{len(rows)} 輪有讀右上字）")
        return
    gaps = [(_hms(events[i][0]) - _hms(events[i - 1][0])) / 60 for i in range(1, len(events))]
    print(f"任務字：換了 {len(events)} 次"
          + (f"，平均隔 {statistics.mean(gaps):.0f} 分鐘（最短 {min(gaps):.0f}、最長 {max(gaps):.0f}）" if gaps else "")
          + "｜" + "、".join(f"{t[:5]}「{w[:10]}」" for t, w in events[-8:]))


def plan3_stats(recs):
    """10/18 N 步規劃：每輪排幾步、各步他自評符合率（下一輪的「符合」清單）、程式量到有反應率、全符合率；按設定步數分開算。
    12:5x 起改兩步（jsonl「步數」）；還有小任務完成的回顧（送了幾次、回顧後記下幾次）。"""
    rows = [r for r in recs if "下三步" in r]
    if not rows:
        return

    def rt(ok):
        return f"{ok[0]}/{ok[1]}（{ok[0] / ok[1] * 100:.0f}%）" if ok[1] else "0/0"
    groups = {}
    for r in rows:
        groups.setdefault(r.get("步數") or 3, []).append(r)
    for n, rs in sorted(groups.items()):
        n_steps = Counter(len(r["下三步"]) for r in rs)
        self_ok = [[0, 0] for _ in range(n)]
        react = [[0, 0] for _ in range(n)]
        all_ok = [0, 0]
        for r in rs:
            m = r.get("符合")
            if isinstance(m, list) and m:
                for i, v in enumerate(m[:n]):
                    self_ok[i][1] += 1
                    self_ok[i][0] += int(v is True)
                all_ok[1] += 1
                all_ok[0] += int(all(v is True for v in m))
            for i, st in enumerate(r["下三步"][:n]):
                c = changed(str(st.get("回傳", "")))
                if c is not None:
                    react[i][1] += 1
                    react[i][0] += int(c)
        print(f"{n}步規劃：{len(rs)} 輪｜排了幾步 " + "、".join(f"{k} 步 {v} 輪" for k, v in sorted(n_steps.items()))
              + f"｜全符合 {rt(all_ok)}")
        print("  他自評符合率：" + "、".join(f"第{i + 1}步 {rt(self_ok[i])}" for i in range(n))
              + "｜程式量到有反應：" + "、".join(f"第{i + 1}步 {rt(react[i])}" for i in range(n)))
    sent = done = 0
    for r in recs:
        for ret in r.get("回傳") or []:
            if re.match(r"● (任務完成|這步做完)", ret):
                if "還沒算" in ret:
                    sent += 1
                elif "記下了" in ret:
                    done += 1
    if sent or done:
        print(f"小任務完成回顧：送出等回顧 {sent} 次、回顧後記下 {done} 次")


def review_stats(recs):
    """10/19 使用者「多多檢討多多找錯 避免重複犯錯」：檢討幾次（符合時也檢討幾次）、程式比對出同樣的錯幾次。"""
    tot = rep_n = while_ok = 0
    for r in recs:
        rets = [x for x in r.get("回傳") or [] if x.startswith("● 檢討")]
        ok = [x for x in rets if "檢討記下了" in x]
        tot += len(ok)
        rep_n += sum(1 for x in ok if "已經檢討過" in x)
        m = r.get("符合")
        if ok and (m is True or (isinstance(m, list) and m and all(v is True for v in m))):
            while_ok += len(ok)
    rounds = len(recs)
    print(f"檢討：{tot} 次（{rounds} 輪，每 {rounds / tot:.1f} 輪一次）" if tot else f"檢討：0 次（{rounds} 輪）", end="")
    print(f"｜符合時也檢討 {while_ok}｜同樣的錯又犯 {rep_n}")


def verify2_stats(recs):
    """10/18 量測二次驗證：每種量測 一致／不一致 次數。"""
    cnt = {}
    for r in recs:
        for ret in r.get("回傳") or []:
            for m in re.finditer(r"【二次驗證 (一致|不一致)】(.+?) 第一次", ret):
                c = cnt.setdefault(m.group(2), [0, 0])
                c[1] += 1
                c[0] += int(m.group(1) == "一致")
    if not cnt:
        return
    tot = [sum(v[0] for v in cnt.values()), sum(v[1] for v in cnt.values())]
    print(f"二次驗證：一致 {tot[0]}/{tot[1]}（{tot[0] / tot[1] * 100:.0f}%）｜"
          + "、".join(f"{k} {v[0]}/{v[1]}" for k, v in sorted(cnt.items(), key=lambda kv: -kv[1][1])))


if __name__ == "__main__":
    main()
