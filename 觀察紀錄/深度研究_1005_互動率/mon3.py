# 動物醫院監督用（只讀）：看 stdout.log 最近 N 分鐘
import io, os, re, sys, statistics, subprocess
from datetime import datetime, timedelta
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
MIN = int(sys.argv[1]) if len(sys.argv) > 1 else 30
GAME = sys.argv[2] if len(sys.argv) > 2 else "動物醫院"
p = os.path.join(r"D:\Documents\ThorWorkSpace\gamer AI\專案", GAME, "記憶", "stdout.log")
L = io.open(p, encoding="utf-8", errors="ignore").read().splitlines()
now = datetime.now()
rnd = re.compile(r"^\[(\d{2}):(\d{2}):(\d{2})\] .*? (\d+(?:\.\d+)?)s｜(.*)→ (.*)$")
rows = []; cur = None; seg = []
for i, l in enumerate(L):
    m = rnd.match(l)
    if m:
        t = now.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=int(m.group(3)), microsecond=0)
        if t > now + timedelta(minutes=5): t -= timedelta(days=1)
        cur = {"t": t, "secs": float(m.group(4)), "see": m.group(5), "act": m.group(6), "i": i, "think": None}
        rows.append(cur)
    elif cur is not None and "⏱ 截圖" in l and cur["think"] is None:
        m2 = re.search(r"快思 (\d+(?:\.\d+)?)s", l)
        if m2: cur["think"] = float(m2.group(1))
alive = subprocess.run(["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | Where-Object { $_.CommandLine -like '*flow_agent.py*' }).ProcessId; (Get-Process RobloxPlayerBeta -ErrorAction SilentlyContinue).Id"], capture_output=True, text=True).stdout.split()
print(f"現在 {now:%m/%d %H:%M:%S}｜代理／Roblox PID：{alive or '都不在'}｜總輪數 {len(rows)}｜最後一輪 {rows[-1]['t']:%H:%M:%S}（{(now-rows[-1]['t']).seconds} 秒前）" if rows else "還沒有輪次")
rec = [r for r in rows if r["t"] >= now - timedelta(minutes=MIN)]
if rec:
    i0 = rec[0]["i"]; txt = L[i0:]
    cnt = lambda s: sum(1 for l in txt if s in l)
    think = [r["think"] for r in rec if r["think"] is not None]
    longs = [x for x in think if x >= 12]
    gaps = [(rec[k]["t"] - rec[k - 1]["t"]).seconds for k in range(1, len(rec))]
    E = [r for r in rec if "長按 E" in r["act"] or re.search(r"按 e \d", r["act"])]
    print(f"最近 {MIN} 分鐘：{len(rec)} 輪｜整輪中位 {statistics.median(r['secs'] for r in rec):.0f}s｜要想的輪 {len(longs)}（中位 {statistics.median(longs) if longs else 0:.0f}s、最長 {max(longs) if longs else 0:.0f}s、頂到時限的 {sum(1 for x in longs if x >= 89)}）｜不思考的短輪 {len(think)-len(longs)}")
    print(f"  兩輪之間最久 {max(gaps) if gaps else 0}s｜沒動作 {sum(1 for r in rec if r['act'].startswith('沒動作'))}｜只換快思 {sum(1 for r in rec if '換快思' in r['act'])}｜🤐 {cnt('🤐')}｜↻ {cnt('↻ 快思沒講完')}")
    print(f"  按E {len(E)} 次（程式記成功 {sum(1 for r in E if '成功）' in r['act'])}）｜檢查輪 ✅{cnt('✅ 反射：')} 🖐{cnt('🖐 反射：')} ✋{cnt('✋ 反射：')}｜✍ 檢查輪寫了 {cnt('✍ 檢查輪寫了')}｜👀 {cnt('👀 一次看畫面只按一個')}｜改提示 {cnt('✍ 他改了自己的提示')}")
    print(f"  懲罰 {cnt('⛔ 懲罰')}｜獎勵 {cnt('🏆 獎勵')}（成功進場 {cnt('成功進場')}、撐完一整輪班 {cnt('撐完一整輪班')}）｜🔁 送回 {cnt('🔁 人不在遊戲裡')}｜🚨 {cnt('🚨')}｜付費字 {cnt('🚫 偵測到付費')}｜Traceback {cnt('Traceback')}")
    tasks = [re.search(r"🎯 右上角任務：(.*)", l).group(1)[:22] for l in txt if "🎯 右上角任務：" in l]
    print("  任務換成：" + (" → ".join(tasks[-8:]) or "（沒換）"))
    sh = [int(x) for l in txt for x in re.findall(r"進入輪班 (\d+)", l)]
    print("  輪班：" + (str(sh[-6:]) if sh else "（沒看到）"))
    for r in rec[-6:]:
        print(f"  [{r['t']:%H:%M:%S}] {r['secs']:.0f}s｜{r['see'][:40]} → {r['act'][:46]}")
