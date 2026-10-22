# -*- coding: utf-8 -*-
"""大檢討（2026-10-15 22:5x 使用者：「先停掉，讓他自己開始跑大檢討」）。

代理停著的時候跑一次：把今天整天的紀錄給他（統計、檢討、大任務、開工數值、點擊審核、他自己記的、工具使用率），
用最高推理強度、不限時間，要他自己寫大檢討。他列出來的 新工具／補用途／記卡法／記筆記 會真的寫進他的記憶與工具清單
（跟他在遊戲裡叫工具一樣），大檢討全文存 記憶/工具版/大檢討.md，下次開機 system 會帶「明天開場要做什麼」那段。

用法：python 工具版/big_review.py --game 動物醫院 [--date 2026-10-15] [--effort 慢想] [--max-tokens 6000]
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
NL = "\n"


def read(p, tail=None):
    try:
        s = open(p, encoding="utf-8").read()
    except OSError:
        return ""
    if tail:
        s = NL.join(s.splitlines()[-tail:])
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", default="動物醫院")
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--effort", default="慢想")
    ap.add_argument("--max-tokens", type=int, default=6000)
    ap.add_argument("--endpoint", default="http://192.168.0.126:8888/v1")
    ap.add_argument("--no-apply", action="store_true", help="不把新工具／補用途／記卡法／記筆記／記準則寫進記憶（上次已寫過時用）")
    ap.add_argument("--reuse", action="store_true", help="不叫大腦，用上次存的 大檢討_日期.json 的原文／推理重新解析與寫入（10/19 05:2x 解析失敗補救）")
    a = ap.parse_args()
    import tool_agent as T
    from play_agent import stream_json, parse_json_loose, LAST_REASONING

    m = os.path.join(ROOT, "專案", a.game, "記憶", "工具版")
    cat = T.load_catalog(a.game)
    tools = T.Tools(a.game, cat, live=False)
    stats = subprocess.run([sys.executable, os.path.join(HERE, "stats.py"), "--game", a.game, "--date", a.date],
                           capture_output=True, text=True, encoding="utf-8").stdout
    # 工具使用率
    use = Counter()
    recs = [json.loads(l) for l in open(os.path.join(m, a.date + ".jsonl"), encoding="utf-8")]
    for r in recs:
        for ret in r["回傳"]:
            mm = re.match(r"● (\S+?)(?:\(|\s→)", ret)
            if mm:
                use[mm.group(1)] += 1
    unused = [t["名稱"] for t in cat["工具"] if use.get(t["名稱"], 0) == 0]
    use_txt = "、".join(f"{k} {v}" for k, v in use.most_common()) + NL + "一次都沒用過：" + "、".join(unused)
    # 檢討：全部太多，給分組計數＋最後 80 條
    reviews = [l for l in read(os.path.join(m, "檢討.md")).splitlines() if l.strip()]
    kw = Counter()
    for l in reviews:
        for k in ("撞牆", "貼牆", "沒對正", "E", "按E", "沒反應", "面板", "HUD", "蓋印表格", "門", "走過頭", "轉", "讀字", "箭頭", "付費", "死", "理智"):
            if k in l:
                kw[k] += 1
    rev_txt = f"今天檢討共 {len(reviews)} 條；關鍵字出現次數：" + "、".join(f"{k} {v}" for k, v in kw.most_common()) + NL + NL.join(reviews[-80:])
    # 卡最久：同一「看到」連續輪、同工具連續
    seen_runs = Counter()
    last, run = None, 0
    for r in recs:
        s = (r["看到"] or "")[:30]
        if s == last:
            run += 1
        else:
            if run >= 4:
                seen_runs[last] += 1
            last, run = s, 1
    stuck_txt = "「看到」連續 5 輪以上沒變的畫面：" + "；".join(f"{k}（{v} 次）" for k, v in seen_runs.most_common(10))
    # 他自己記的
    mine = ""
    for f in ("小任務.md", "筆記.md", "地點.md", "因果.md", "卡法.md", "大任務.md", "開工數值.md", "點擊審核.md"):
        body = read(os.path.join(m, f), tail=40)
        if body:
            mine += f"【{f}】" + NL + body + NL
    custom = read(T.custom_path(a.game))
    goal = read(os.path.join(ROOT, "專案", a.game, "記憶", "任務指示.txt"))
    tool_list = NL.join(T.catalog_lines(cat))

    system = (f"你是 gamer AI，正在學 Roblox「{a.game}」。現在遊戲停著，你在做今天的大檢討——不是玩，是回頭看自己一整天。" + NL
              + "使用者給你的目標：" + goal.strip() + NL
              + "規則：只寫畫面與紀錄裡真的有的事，不推測；表格式短語；每一條都要能照著做。" + NL              + "（使用者 10/21 交代）先逐一確認每個小任務是不是一次就做對（看【小任務.md】的輪數、✗、檢討數），再寫其他欄位。" + NL
              + "你有這些工具（之後玩的時候可以叫）：" + NL + tool_list)
    user = ("【今天的統計】" + NL + stats + NL
            + "【工具使用率】" + NL + use_txt + NL + NL
            + "【卡最久的畫面】" + NL + stuck_txt + NL + NL
            + "【你今天寫的檢討】" + NL + rev_txt + NL + NL
            + "【你自己記的東西】" + NL + mine + NL
            + "【你自己定義過的工具】" + NL + (custom or "（沒有）") + NL + NL
            + "請做大檢討，只輸出一個 JSON，欄位：" + NL
            + '{"小任務一次做對":[{"小任務":"","一次做對":true,"沒一次做對的原因":"","下次怎麼一次做對":""}],'
              '"今天做了什麼":"<=150字","卡最久的三件事":[{"情況":"","卡了多久或幾次":"","為什麼":"","當時該怎麼做":""}],'
              '"大任務為什麼沒完成":"<=200字（對照統計：按E有反應率、點擊、死亡、大任務完成/放棄次數、文件類工具 0 次）",'
              '"卡法歸納":[{"卡法":"","原因":"","解法":"用現有工具寫出順序，例如 退(0.4)→轉(180)→讀字"}],'
              '"明天開場要做什麼":["第一步","第二步","…（具體到工具名與參數，含開工測試、驗開工、要不要先 讀文件/我的記憶、大任務怎麼設怎麼結案）"],'
              '"新工具":[{"名稱":"","用途":"","做法":"現有工具用 → 接起來"}],'
              '"補用途":[{"工具":"","情況":""}],'
              '"記卡法":[{"情況":"","解法":""}],'
              '"記筆記":["一句一條，表格式，確定的事才寫"],'
              '"記準則":[{"對象":"東西或情況","準則":"一句：能不能、怎麼做（你今天自己發現的）"}],'
              '"要改的習慣":["例如：E 一律先長按 1.5 秒再試短按；右上角黑底白字是任務不是按鈕；填 false 當輪就檢討"]}')
    t0 = time.time()
    if a.reuse:
        old = json.load(open(os.path.join(m, f"大檢討_{a.date}.json"), encoding="utf-8"))
        raw, reason, dt, trunc = old.get("原文") or "", old.get("推理") or "", float(old.get("秒") or 0), False
        print(f"[大檢討] --reuse：用存好的原文 {len(raw)} 字、推理 {len(reason)} 字")
    else:
        print(f"[大檢討] system {len(system)} 字、user {len(user)} 字、effort {a.effort}、max_tokens {a.max_tokens}…")
        raw, trunc, ttft, _ = stream_json(a.endpoint, T.detect_model(a.endpoint), system,
                                          [{"type": "text", "text": user}], a.max_tokens, time.time() + 900, 0.3, effort=a.effort)
        dt = time.time() - t0
        reason = LAST_REASONING.get("text", "")
    o = parse_json_loose(T.repair_json(raw)) or {}
    from_reason = False
    if not o and reason:      # 10/19 05:2x：他把整份 JSON 寫在推理裡、輸出 0 字 → 從推理取出來
        # 13:2x：他把同一份 JSON 重複寫了 4 次、第 5 次寫一半被截斷 → 每一份都解析，挑欄位最多的（同樣多取後面的）
        best = {}
        for mm in re.finditer(r'\{\s*"今天做了什麼"', reason):
            cand = parse_json_loose(T.repair_json(reason[mm.start():])) or {}
            if len(cand) >= len(best):
                best = cand
        o, from_reason = best, bool(best)
    print(f"[大檢討] {dt:.0f}s 截斷={trunc} 推理 {len(reason)} 字 輸出 {len(raw)} 字 解析{'成功' if o else '失敗'}{'（從推理取出）' if from_reason else ''}")
    # 10/20 08:2x：輸出寫到「記筆記」一半就斷（推理 5.6k＋輸出 2k 字超過 max_tokens），有開場沒習慣、沒記準則也照樣蓋掉
    # → 開場跟習慣兩個都要有；輸出缺就再從推理裡找完整的一份
    # 10/22 15:5x：沒截斷、寫到最後的 記準則 了，只是漏寫「要改的習慣」也算完整（以前一律當失敗、整份 539 秒白跑）
    def whole(x):
        return bool(x.get("明天開場要做什麼") and (x.get("要改的習慣") or "記準則" in x))
    if o and not whole(o) and reason:
        for mm in re.finditer(r'\{\s*"今天做了什麼"', reason):
            cand = parse_json_loose(T.repair_json(reason[mm.start():])) or {}
            if whole(cand):
                o, from_reason = cand, True
    if o and not whole(o):   # 13:2x：殘缺的（缺開場或習慣）也不蓋掉上一份
        print(f"[大檢討] 解析出來的缺 明天開場／要改的習慣（只有 {list(o.keys())}），當成失敗")
        o = {}
    if not o:                 # 解析不出來：不覆蓋上一份 大檢討.md（system 會繼續帶上一份），失敗的另存
        with open(os.path.join(m, "大檢討_失敗.md"), "w", encoding="utf-8") as f:
            f.write(f"# 大檢討解析失敗 {datetime.now().strftime('%m/%d %H:%M')}" + NL * 2 + "## 原文" + NL + raw + NL * 2 + "## 推理" + NL + reason)
        if not a.reuse:
            with open(os.path.join(m, f"大檢討_{a.date}.json"), "w", encoding="utf-8") as f:
                json.dump({"輸出": o, "原文": raw, "推理": reason, "秒": dt}, f, ensure_ascii=False, indent=1)
        print("[大檢討] 解析失敗，沒有覆蓋 大檢討.md（另存 大檢討_失敗.md）")
        return

    # 把他列的東西真的寫進他的記憶／工具清單（跟他在遊戲裡叫工具一樣）
    applied = []
    if a.no_apply:
        applied.append("（--no-apply：這次不寫入；同一份大檢討的內容 13:26 已寫進記憶）")
        o_apply = {}
    else:
        o_apply = o
    for t in o_apply.get("新工具") or []:
        if isinstance(t, dict):
            applied.append("新工具 → " + tools.tool_new(t.get("名稱", ""), t.get("用途", ""), t.get("做法", "")))
    for t in o_apply.get("補用途") or []:
        if isinstance(t, dict):
            applied.append("補用途 → " + tools.tool_add_use(t.get("工具", ""), t.get("情況", "")))
    for t in o_apply.get("記卡法") or []:
        if isinstance(t, dict):
            applied.append("記卡法 → " + tools.mem_stuck(t.get("情況", ""), t.get("解法", "")))
    for t in o_apply.get("記筆記") or []:
        if isinstance(t, str) and t.strip():
            applied.append("記筆記 → " + tools.mem_note(t))
    for t in o_apply.get("記準則") or []:          # 10/19 12:4x 使用者：「他也能按照他的發現去自己製作新的準則」
        if isinstance(t, dict):
            applied.append("記準則 → " + tools.mem_rule(t.get("對象", ""), t.get("準則", "")))

    out = [f"# 大檢討 {a.date}（{datetime.now().strftime('%m/%d %H:%M')}，{a.effort}，{dt:.0f} 秒{'，JSON 從推理取出' if from_reason else ''}）", ""]
    for k in ("小任務一次做對", "今天做了什麼", "卡最久的三件事", "大任務為什麼沒完成", "卡法歸納", "明天開場要做什麼", "新工具", "補用途", "記卡法", "記筆記", "記準則", "要改的習慣"):
        v = o.get(k)
        if v is None:
            continue
        out.append(f"## {k}")
        if isinstance(v, list):
            for x in v:
                out.append("- " + (json.dumps(x, ensure_ascii=False) if isinstance(x, dict) else str(x)))
        else:
            out.append(str(v))
        out.append("")
    out.append("## 程式替你寫進記憶／工具清單的")
    out += ["- " + x for x in applied] or ["（沒有）"]
    out.append("")
    out.append("## 他的內部推理（節錄）")
    out.append((reason or "")[:3000])
    p = os.path.join(m, "大檢討.md")
    with open(p, "w", encoding="utf-8") as f:
        f.write(NL.join(out))
    with open(os.path.join(m, f"大檢討_{a.date}.json"), "w", encoding="utf-8") as f:
        json.dump({"輸出": o, "原文": raw, "推理": reason, "秒": dt}, f, ensure_ascii=False, indent=1)
    print(f"[大檢討] 存 {p}")
    print(NL.join(out[:60]))


if __name__ == "__main__":
    main()
