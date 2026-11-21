"""11/22 使用者「記錄好後幫我上 git，放在 gamer AI 裡面」→ 把 11/21～22 實驗紀錄複製到 gamer AI/觀察紀錄/2026-11-21_有無工具實驗/（不含截圖、.out/.err、備份檔）。可重跑。"""
import os, shutil, subprocess, difflib
HERE = os.path.dirname(os.path.abspath(__file__))
SIT = os.path.dirname(HERE)
DST = r"D:\Documents\ThorWorkSpace\gamer AI\觀察紀錄\2026-11-21_有無工具實驗"
TOOL = r"D:\Documents\ThorWorkSpace\gamer AI 工具版\工具版"
SKIP_DIR = {"shots", "__pycache__"}
for root, dirs, files in os.walk(HERE):
    dirs[:] = [d for d in dirs if d not in SKIP_DIR]
    for f in files:
        if f.endswith((".out", ".err", ".jpg", ".pyc")) or ".bak" in f:
            continue
        src = os.path.join(root, f); rel = os.path.relpath(src, HERE)
        dst = os.path.join(DST, "實驗_1121", rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True); shutil.copy2(src, dst)
for f in ("2026-11-21.md", "2026-11-22.md"):
    p = os.path.join(SIT, "工作日誌", f)
    if os.path.exists(p):
        os.makedirs(os.path.join(DST, "工作日誌"), exist_ok=True); shutil.copy2(p, os.path.join(DST, "工作日誌", f))
# tool_agent.py 今天的改動（多嘗試、探索⑥、隊友檢查掛勾）存成 diff
old = open(os.path.join(TOOL, "tool_agent.bak_多嘗試前_1121.py"), encoding="utf-8").read().splitlines(True)
new = open(os.path.join(TOOL, "tool_agent.py"), encoding="utf-8").read().splitlines(True)
open(os.path.join(DST, "tool_agent_1121改動.diff"), "w", encoding="utf-8").writelines(
    difflib.unified_diff(old, new, "tool_agent.py（11/21 多嘗試前）", "tool_agent.py（11/22）"))
print("ok", DST)
