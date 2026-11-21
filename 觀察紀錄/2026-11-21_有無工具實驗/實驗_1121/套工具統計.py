"""11/21 使用者：Sonnet 的成績如果套用地端工具（stats.py）的算法，大任務／遊戲任務完成／死亡重生 各是多少。
死亡判斷整段照抄 stats.py（你死了／再玩一次面板、局內理智≤5%回大廳、他自己說死亡），三分鐘內算同一次。
Sonnet 沒有「設大任務／任務完成」工具：任務完成改用它的「完成」欄（＝地端的 任務完成 工具），大任務＝無此功能。"""
import json, os, re, sys
D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sonnet_動物醫院")
recs = [json.loads(l) for l in open(os.path.join(D, "rounds.jsonl"), encoding="utf8")]
recs = [r for r in recs if "輪" in r]
SELF_DW = re.compile(r"死亡|死了|死掉|倒下|倒地|陣亡")
SELF_NEG = re.compile(r"不是|不算|沒有|沒死|非死|別|如果|若|可能|或|會不會|避免|以免|免得|準則|教訓|檢查|防|怕|才會|會死|前|\?|？")
def self_judged(r):
    for k in ("看到", "想"):
        for seg in re.split(r"[｜|；;。\n]", r.get(k) or ""):
            if SELF_DW.search(seg) and not SELF_NEG.search(seg):
                return True
    return False
respawn = self_death = 0; last = None; sanity, in_game = None, False; ev = []
for r in recs:
    sv = r.get("看到") or ""; s = r["時間"]; t = int(s[:2]) * 3600 + int(s[2:4]) * 60 + int(s[4:])
    why = None
    if self_judged(r):
        why = "他自己說死亡"
    else:
        died = bool(re.search(r"(?<!海報)(?<!招牌)(?<!看板)(?<!告示)(你死了|再玩一次)", sv)) and not re.search(r"(海報|招牌|看板|告示)[^，；。、]{0,4}你死了", sv)
        tw = r.get("任務字")
        lobby_hud = bool(re.search(r"大廳|商店|邀請|職業|日誌|最高轉移速度|配對踏板|角色扮演", sv)) or "ANIMAL" in sv.upper()
        lobby = lobby_hud and "輪班" not in sv and not tw
        if died: why = "看到死亡面板"
        elif in_game and lobby and sanity is not None and sanity <= 5: why = "理智≤5%後回大廳"
    if why:
        if last is None or t - last > 180:
            respawn += 1; self_death += why == "他自己說死亡"; ev.append((r["輪"], f"{s[:2]}:{s[2:4]}", why, sv[:70]))
        last = t; in_game, sanity = False, None; continue
    ms = re.search(r"(?:理智\s*(\d+)\s*%|(\d+)\s*%\s*理智|(\d+)\s*%\s*[/、]?\s*\$|左下(?:理智)?\s*(\d+)\s*%)", sv)
    if ms: sanity = int(next(g for g in ms.groups() if g))
    else:
        pcs = [int(x) for x in re.findall(r"(\d+)\s*%", sv) if int(x) <= 100]
        if len(pcs) == 1: sanity = pcs[0]
    if "輪班" in sv or r.get("任務字"): in_game = True
    elif lobby: in_game, sanity = False, None
done = [r for r in recs if r.get("完成")]
print(f"範圍 {recs[0]['時間']}～{recs[-1]['時間']}，{len(recs)} 輪")
print(f"大任務：設 0、完成 0（Sonnet 沒有這個工具）｜遊戲任務完成：{len(done)} 件｜死亡重生：{respawn} 次（他自己判斷死亡 {self_death}）")
for r in done: print("  完成", r["輪"], r["時間"], r["完成"][:80])
for e in ev: print("  死亡", *e)
