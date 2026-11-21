"""11/21 使用者「完成後再實驗 換成最低的思考程度各自接續一個小時」：
等工具版 3 小時（PID 27172）結束 → Sonnet＋工具版 快思推理(low) 接續 1 小時（記憶不清，接著用）
→ Sonnet 原始操作版 --effort low --resume 原 session 接續 1 小時。每段開始前刪 stop.txt。"""
import os, subprocess, sys, time
from datetime import datetime, timedelta
WT = r"D:\Documents\ThorWorkSpace\gamer AI 工具版"; STOP = os.path.join(WT, "stop.txt")
HERE = os.path.dirname(os.path.abspath(__file__)); LOG = os.path.join(HERE, "接續.log")
PY = r"C:\Python313\python.exe"; SESSION = "c7cb5bab-c765-4db0-9c3e-61f121cfe977"
def log(m):
    open(LOG, "a", encoding="utf-8").write(f"{datetime.now():%H:%M:%S} {m}\n")
def alive(pid):
    return str(pid) in subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True).stdout
def run(args, cwd, out):
    for s in (STOP, os.path.join(HERE, "sonnet_動物醫院", "stop.txt")):
        if os.path.exists(s): os.remove(s)
    until = (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M")
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    p = subprocess.Popen([PY, "-u"] + args + ["--until", until], cwd=cwd, env=env,
                         stdout=open(out, "a", encoding="utf-8"), stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
    log(f"啟動 {args[0]} PID {p.pid} 到 {until}"); p.wait(); log(f"結束 rc {p.returncode}")
log("等 PID 27172")
while alive(27172): time.sleep(30)
log("工具版 3 小時結束"); time.sleep(20)
run([os.path.join(HERE, "sonnet_工具版_run.py"), "--effort", "快思推理"], WT, os.path.join(HERE, "接續_工具版.out"))
time.sleep(20)
run([os.path.join(HERE, "raw_agent.py"), "--effort", "low", "--resume", SESSION], HERE, os.path.join(HERE, "sonnet_動物醫院", "stdout.log"))
log("全部結束")
