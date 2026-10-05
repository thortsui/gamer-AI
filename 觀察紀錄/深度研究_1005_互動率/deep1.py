# 深度研究 1：三個專案逐日的「互動」到底有多少、成功多少、他把輪次花在哪（只讀 agent.log）
import io, os, re, collections, sys
from datetime import datetime, timedelta
ROOT = r"D:\Documents\ThorWorkSpace\gamer AI"
GAMES = ["新遊戲", "動物醫院", "農場遊戲"]
start_re = re.compile(r"^===== (\d{4}-\d{2}-\d{2}) (\d{2}):(\d{2}):(\d{2}) 啟動")
round_re = re.compile(r"^\[(\d{2}):(\d{2}):(\d{2})\] .*? (\d+(?:\.\d+)?)s｜(.*)→ (.*)$")
rew_re = re.compile(r"🏆 獎勵 \+(\d+)：(\S+?)　")
pun_re = re.compile(r"⛔ 懲罰 −(\d+)：(\S+?)　")
D = collections.OrderedDict()
CLK = collections.defaultdict(collections.Counter)      # (game, period) -> click targets
REW = collections.defaultdict(collections.Counter)
PUN = collections.defaultdict(collections.Counter)
EOBJ = collections.defaultdict(collections.Counter)     # E 成功的對象

def key_period(g, d):
    return (g, d)

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
            if m:
                secs = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
                if prev is not None and secs < prev - 6 * 3600:
                    day += timedelta(days=1)
                prev = secs
                k = (g, day.strftime("%m/%d"))
                c = D.setdefault(k, collections.Counter())
                act = m.group(6); see = m.group(5)
                c["輪"] += 1
                isE = ("長按 E" in act) or bool(re.search(r"按 e \d", act)) or ("互動" in act and "E" in act)
                isClick = ("點「" in act) or ("點字" in act) or bool(re.search(r"(^|、)點 ", act)) or ("選「" in act)
                isMove = bool(re.search(r"按 [wasd] \d", act)) or ("走到" in act) or ("走向" in act)
                isLook = ("轉視角" in act) or ("準心" in act)
                if act.startswith("沒動作"): c["沒動作"] += 1
                elif "換快思" in act and not (isE or isClick or isMove or isLook): c["只換快思"] += 1
                elif isE: c["互動E"] += 1
                elif isClick: c["點擊"] += 1
                elif isMove: c["走"] += 1
                elif isLook: c["轉頭"] += 1
                elif act.startswith("用工具") or act.startswith("等"): c["工具或等"] += 1
                else: c["其他"] += 1
                if "長按 E" in act and "成功）" in act:
                    c["E成功"] += 1
                    mm = re.search(r"互動「([^」]*)」", act)
                    EOBJ[k][(mm.group(1) if mm else "?")[:10]] += 1
                if "長按 E" in act and "沒反應）" in act: c["E沒反應"] += 1
                for t in re.findall(r"點「([^」]+)」|點字」（([^）]+)）", act):
                    CLK[k][(t[0] or t[1])[:12]] += 1
                # 他描述裡把什麼當目標
                if re.search(r"人偶|person|背影", see): c["描述有人偶或背影"] += 1
                if re.search(r"選單|面板", see): c["描述有選單面板"] += 1
                continue
            k = (g, day.strftime("%m/%d"))
            c = D.setdefault(k, collections.Counter())
            m = rew_re.search(line)
            if m: REW[k][m.group(2)[:12]] += 1; c["獎勵"] += 1
            m = pun_re.search(line)
            if m: PUN[k][m.group(2)[:12]] += 1; c["懲罰"] += 1
            if "🔎" in line and "之後" in line:
                if "字沒變" in line or "完全沒變" in line: c["點完沒變"] += 1
                elif "多出來的字" in line or "不見的字" in line: c["點完有變"] += 1
            if "登記成功" in line or "Check In 成功" in line or "報到成功" in line: c["登記字樣"] += 1

P = lambda a, n: f"{100*a/n:.0f}" if n else "–"
print("遊戲 日｜輪｜各類輪次佔比%：走／轉頭／按E／點擊／工具或等／只換快思／沒動作｜按E每百輪｜E成功｜點擊每百輪｜點完有變每百輪｜獎勵每百輪｜懲罰每百輪｜描述有人偶%｜描述有選單面板%")
for (g, d), c in D.items():
    n = c["輪"]
    if n < 100: continue
    print(f"{g} {d}｜{n}｜{P(c['走'],n)}／{P(c['轉頭'],n)}／{P(c['互動E'],n)}／{P(c['點擊'],n)}／{P(c['工具或等'],n)}／{P(c['只換快思'],n)}／{P(c['沒動作'],n)}"
          f"｜{100*c['互動E']/n:.1f}｜{c['E成功']}／{c['E成功']+c['E沒反應']}｜{100*c['點擊']/n:.1f}｜{100*c['點完有變']/n:.1f}"
          f"｜{100*c['獎勵']/n:.0f}｜{100*c['懲罰']/n:.0f}｜{P(c['描述有人偶或背影'],n)}｜{P(c['描述有選單面板'],n)}")
print()
sel = [("新遊戲", "09/21"), ("新遊戲", "09/24"), ("動物醫院", "09/25"), ("動物醫院", "09/26"), ("動物醫院", "09/28"), ("動物醫院", "10/01"), ("動物醫院", "10/02"), ("農場遊戲", "10/04"), ("農場遊戲", "10/05")]
for k in sel:
    if k not in D: continue
    print(f"== {k[0]} {k[1]}（{D[k]['輪']} 輪）")
    print("  點最多的：", "、".join(f"{t}×{v}" for t, v in CLK[k].most_common(12)))
    print("  E 成功的對象：", "、".join(f"{t}×{v}" for t, v in EOBJ[k].most_common(8)) or "（無）")
    print("  獎勵：", "、".join(f"{t}×{v}" for t, v in REW[k].most_common(8)))
    print("  懲罰：", "、".join(f"{t}×{v}" for t, v in PUN[k].most_common(8)))
