"""11/21 使用者「修改工具 讓他優先去進行多嘗試而非畫面的敘述…目標是多多嘗試重新用 Haiku 模型跑一次」
等 弱模型.py（PID 47412）全部結束（地端記憶已搬回）→ 再把地端記憶搬去 _舊記憶_1121、記憶清空、任務指示加回研究那段
→ Haiku＋工具版 --try-first（快思推理＝low，跟上一次 Haiku 工具版同條件，只差多嘗試）1 小時
→ 記憶搬去 _haiku多嘗試_1121，地端記憶搬回、任務指示那段拿掉、刪 stop.txt。"""
import os, shutil, subprocess, sys, time
from datetime import datetime, timedelta

WT = r"D:\Documents\ThorWorkSpace\gamer AI 工具版"
HERE = os.path.dirname(os.path.abspath(__file__))
G = os.path.join(WT, "專案", "動物醫院")
MEM = os.path.join(G, "記憶", "工具版")
OLD = os.path.join(G, "_舊記憶_1121", "工具版")
TASK = os.path.join(G, "記憶", "任務指示.txt")
HEAD = "[2026-11-21 使用者]\n使用工具，盡可能多研究這支遊戲的內容。\n\n"
STOP = os.path.join(WT, "stop.txt")
PY = r"C:\Python313\python.exe"
LOG = os.path.join(HERE, "多嘗試.log")


def log(m):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%m/%d %H:%M:%S} {m}\n")


def alive(pid):
    return str(pid) in subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True).stdout


log("等 弱模型.py PID 47412")
while alive(47412):
    time.sleep(30)
log("弱模型.py 結束")

# 地端記憶再搬走（只在 _舊記憶 那格是空的時候搬，避免蓋掉）
if os.path.isdir(MEM) and os.listdir(MEM) and not os.path.exists(OLD):
    shutil.move(MEM, OLD)
    log("地端記憶搬到 _舊記憶_1121/工具版")
elif os.path.exists(OLD):
    log("⚠ _舊記憶_1121/工具版 已存在，沒搬（停下來）"); sys.exit(1)
os.makedirs(MEM, exist_ok=True)
s = open(TASK, encoding="utf-8").read()
if not s.startswith(HEAD):
    open(TASK, "w", encoding="utf-8").write(HEAD + s)
    log("任務指示加回研究那段")
if os.path.exists(STOP):
    os.remove(STOP)

subprocess.run(["taskkill", "/IM", "RobloxPlayerBeta.exe", "/F"], capture_output=True)
time.sleep(5)
os.startfile("roblox://placeId=78515283254292")
time.sleep(75)
log("Roblox 重開回大廳")
until = (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M")
env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
with open(os.path.join(HERE, "haiku_多嘗試.out"), "a", encoding="utf-8") as o:
    p = subprocess.Popen([PY, "-u", os.path.join(HERE, "sonnet_工具版_run.py"), "--brain", "haiku", "--effort", "快思推理",
                          "--try-first", "--until", until], cwd=WT, env=env, stdout=o, stderr=subprocess.STDOUT,
                         creationflags=subprocess.CREATE_NO_WINDOW)
    log(f"Haiku＋工具版（多嘗試）啟動 PID {p.pid} 到 {until}")
    p.wait()
log(f"Haiku＋工具版（多嘗試）結束 rc {p.returncode}")
time.sleep(20)

dst = os.path.join(G, "_haiku多嘗試_1121")
os.makedirs(dst, exist_ok=True)
shutil.move(MEM, os.path.join(dst, "工具版"))
log(f"記憶搬到 {dst}")
if os.path.isdir(OLD):
    shutil.move(OLD, MEM)
    log("地端原本的記憶已搬回 記憶/工具版")
s = open(TASK, encoding="utf-8").read()
if s.startswith(HEAD):
    open(TASK, "w", encoding="utf-8").write(s[len(HEAD):])
    log("任務指示那段已拿掉")
if os.path.exists(STOP):
    os.remove(STOP)
log("全部結束")
