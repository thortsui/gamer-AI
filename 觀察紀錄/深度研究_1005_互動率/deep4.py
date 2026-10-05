# 深度研究 4：每小時真的按了幾下（只讀 agent.log）
import io, os, re, collections, sys, statistics
from datetime import datetime, timedelta
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = r"D:\Documents\ThorWorkSpace\gamer AI"
GAMES = ["新遊戲", "動物醫院", "農場遊戲"]
start_re = re.compile(r"^===== (\d{4}-\d{2}-\d{2}) (\d{2}):(\d{2}):(\d{2}) 啟動")
round_re = re.compile(r"^\[(\d{2}):(\d{2}):(\d{2})\] .*? (\d+(?:\.\d+)?)s｜(.*)→ (.*)$")
D = collections.OrderedDict(); DUR = collections.defaultdict(list)
for g in GAMES:
    p = os.path.join(ROOT, "專案", g, "記憶", "agent.log")
    day = None; prev = None
    with io.open(p, encoding="utf-8", errors="ignore") as f:
        for line in f:
            m = start_re.match(line)
            if m:
                day = datetime.strptime(m.group(1), "%Y-%m-%d"); prev = int(m.group(2)) * 3600; continue
            if day is None:
                continue
            m = round_re.match(line)
            if not m:
                continue
            secs = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
            if prev is not None and secs < prev - 6 * 3600:
                day += timedelta(days=1)
            prev = secs
            k = (g, day.strftime("%m/%d")); c = D.setdefault(k, collections.Counter())
            dur = float(m.group(4)); act = m.group(6); see = m.group(5)
            c["輪"] += 1; c["秒"] += dur; DUR[k].append(dur)
            if dur >= 12: c["長輪"] += 1; c["長輪秒"] += dur
            isE = ("長按 E" in act) or bool(re.search(r"按 e \d", act))
            isClick = ("點「" in act) or ("點字" in act) or ("選「" in act)
            isMove = bool(re.search(r"按 [wasd] \d", act)) or ("走到" in act) or ("走向" in act)
            isLook = ("轉視角" in act) or ("準心" in act)
            if isE or isClick or isMove or isLook or re.search(r"按 \S+ \d", act) or "滾輪" in act:
                c["有按"] += 1
            if isE: c["E"] += 1
            if isE and "成功）" in act: c["E成功"] += 1
            if isClick: c["點"] += 1
            if isMove: c["走"] += 1
            if re.search(r"選單蓋住|好友選單|離開遊戲|確定要離開", see): c["Roblox選單"] += 1
            if re.search(r"繼續|關閉|紅叉|不要離開|返回|離開", act) and isClick: c["點的是關選單類"] += 1
print("遊戲 日｜跑了幾小時｜每小時幾輪｜每小時有按鍵的輪｜每小時按E｜每小時E成功｜每小時點擊（其中關選單／繼續類%）｜每小時走路｜一輪中位秒｜要想的長輪：佔比、中位秒｜畫面是Roblox選單的輪%")
for (g, d), c in D.items():
    n = c["輪"]
    if n < 100: continue
    h = c["秒"] / 3600
    longs = [x for x in DUR[(g, d)] if x >= 12]
    print(f"{g} {d}｜{h:.1f}｜{n/h:.0f}｜{c['有按']/h:.0f}｜{c['E']/h:.1f}｜{c['E成功']/h:.2f}｜{c['點']/h:.1f}（{100*c['點的是關選單類']/max(c['點'],1):.0f}%）｜{c['走']/h:.0f}｜{statistics.median(DUR[(g,d)]):.0f}｜{100*len(longs)/n:.0f}%、{(statistics.median(longs) if longs else 0):.0f}｜{100*c['Roblox選單']/n:.0f}%")
