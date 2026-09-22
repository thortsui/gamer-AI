#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_agent.py — 快速健康檢查：代理活著嗎？卡住了嗎？最近在做什麼？（輸出很短，省 token）"""
import json
import os
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
GAME = sys.argv[1] if len(sys.argv) > 1 else "動物醫院"
D = game_dir(HERE, GAME)

# 1) 程序還在嗎
try:
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object {$_.CommandLine -like '*play_agent.py*'} | Select-Object -ExpandProperty ProcessId"],
                         capture_output=True, text=True, timeout=20).stdout.split()
    pids = [p for p in out if p.isdigit()]
except Exception:  # noqa: BLE001
    pids = ["?"]
print(f"程序: {'活著 PID ' + ','.join(pids) if pids else '❌ 沒在跑'}")

# 2) 最近的紀錄
rows = []
try:
    with open(os.path.join(D, "行動日誌.jsonl"), encoding="utf-8") as f:
        for line in f.readlines()[-60:]:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
except OSError:
    pass
if not rows:
    print("沒有行動日誌"); sys.exit()

last = rows[-1]
mtime = os.path.getmtime(os.path.join(D, "行動日誌.jsonl"))
idle_min = (time.time() - mtime) / 60
print(f"最後一筆: {last.get('時間')}（{idle_min:.1f} 分鐘前）{'⚠ 太久沒動' if idle_min > 3 else ''}")

seen = [r.get("看到") or "" for r in rows[-20:] if r.get("模式") in ("決策", "看清楚")]
top = Counter(seen).most_common(1)
if top and top[0][1] >= 12:
    print(f"⚠ 疑似卡住：最近 20 輪有 {top[0][1]} 輪都是「{top[0][0][:30]}」")
acts = Counter(json.dumps([{k: v for k, v in a.items() if k in ('t', 'k', 'g')} for a in (r.get('動作') or [])], ensure_ascii=False)
               for r in rows[-20:] if r.get("模式") == "決策")
ta = acts.most_common(1)
if ta and ta[0][1] >= 8 and ta[0][0] != "[]":
    print(f"⚠ 重複動作：最近 20 輪有 {ta[0][1]} 輪都做 {ta[0][0][:80]}")

blocked = sum(len(r.get("擋掉") or []) for r in rows)
robux = sum(1 for r in rows if r.get("robux") == 1)
yt = sum(1 for r in rows if str(r.get("模式", "")).startswith("youtube"))
slow = sum(1 for r in rows if (r.get("決策秒") or 0) > 5)
print(f"最近 {len(rows)} 筆: 擋掉 {blocked} 個動作, 付費視窗 {robux} 次, YouTube {yt} 筆, 決策超過5秒 {slow} 次")

print("最近 5 輪:")
for r in rows[-5:]:
    a = "、".join((x.get("g") or x.get("k") or x.get("t") or "") for x in (r.get("動作") or [])) or "—"
    print(f"  {r.get('時間')} {r.get('模式')} 看到「{(r.get('看到') or '')[:28]}」 做「{a[:40]}」")

# 3) 錯誤
try:
    with open(os.path.join(D, "agent.log"), encoding="utf-8", errors="replace") as f:
        tail = f.readlines()[-200:]
    errs = [l.strip() for l in tail if "[錯誤]" in l or "[致命錯誤]" in l or "Traceback" in l or "[結束]" in l or "[FAILSAFE]" in l]
    if errs:
        print("log 異常/結束訊息:", " | ".join(e[:80] for e in errs[-4:]))
except OSError:
    pass

# 4) 筆記
try:
    with open(os.path.join(D, "研究筆記.md"), encoding="utf-8") as f:
        notes = [l for l in f if l.startswith("- ")]
    print(f"筆記 {len(notes)} 條，最新：{notes[-1].strip()[:60] if notes else '—'}")
except OSError:
    pass

def game_dir(root, game):
    """2026-09-14 專案結構：專案/<遊戲>/記憶；找不到就退回舊的 資料/<遊戲>。"""
    import os
    new = os.path.join(root, "專案", game, "記憶")
    return new if os.path.isdir(new) else os.path.join(root, "資料", game)

