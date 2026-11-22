"""11/21 使用者：Sonnet 玩動物醫院 3 小時，不能搜尋，目標盡可能研究更多遊戲內容，規則跟地端一樣，token 不用省。
參數照 run_until.py（地端 Minecraft 這次用的同一組），只多 --brain sonnet。當掉 60 秒後重開；時間到放 stop.txt。
用法：python -u sonnet_run.py --until "2026-11-21 08:45"
"""
import argparse, os, subprocess, sys, time
from datetime import datetime

ROOT = r"D:\Documents\ThorWorkSpace\gamer AI 工具版"
STOP = os.path.join(ROOT, "stop.txt")
GAME = "動物醫院"
MDIR = os.path.join(ROOT, "專案", GAME, "記憶", "工具版")

ap = argparse.ArgumentParser(); ap.add_argument("--until", required=True)
ap.add_argument("--effort", default="慢想")
ap.add_argument("--try-first", action="store_true")
ap.add_argument("--v3", action="store_true")   # 11/22 第三版工具   # 11/21 多嘗試
ap.add_argument("--brain", default="sonnet")   # 11/21 弱模型實驗：haiku   # 11/21 接續實驗用 快思推理（=low，最低）
a = ap.parse_args()
until = datetime.strptime(a.until, "%Y-%m-%d %H:%M")
LOG = os.path.join(MDIR, "跑到時間.log")


def log(m):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%m/%d %H:%M:%S} {m}\n")


def start():
    stamp = datetime.now().strftime("%m%d_%H%M")
    for n in ("stdout", "stderr"):
        p = os.path.join(MDIR, n + ".log")
        if os.path.exists(p) and os.path.getsize(p):
            os.replace(p, os.path.join(MDIR, f"{n}_{stamp}.log"))
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1", GAMER_TOOLFIX="1", GAMER_VERIFY="1")
    cmd = [sys.executable, "-u", os.path.join("工具版", "tool_agent.py"), "--game", GAME, "--live", "--rounds", "5000",
           "--deadline", "600", "--max-tokens", "8000", "--effort", a.effort, "--lock-effort", "--verify2", "--steps", "2",
           "--no-small-update", "--tree-rotate", "30", "--tree-t0", datetime.now().strftime("%H:%M"), "--slim-system",
           "--mem-check", "120", "--brain", a.brain] + (["--try-first"] if a.try_first else []) + (["--v3"] if a.v3 else [])
    out = open(os.path.join(MDIR, "stdout.log"), "w", encoding="utf-8")
    err = open(os.path.join(MDIR, "stderr.log"), "w", encoding="utf-8")
    p = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=out, stderr=err, creationflags=subprocess.CREATE_NO_WINDOW)
    log(f"啟動代理（sonnet）PID {p.pid}")
    return p


log(f"開始（{a.brain}，{a.effort}），跑到 {a.until}")
proc = start()
while True:
    time.sleep(20)
    if datetime.now() >= until and not os.path.exists(STOP):
        open(STOP, "w").close(); log("時間到，放 stop.txt")
    if os.path.exists(STOP):
        try:
            proc.wait(900); log(f"收到 stop.txt，代理結束（rc {proc.returncode}）")
        except subprocess.TimeoutExpired:
            log("收到 stop.txt，等 900 秒代理還沒結束")
        break
    rc = proc.poll()
    if rc is not None:
        log(f"代理意外結束（rc {rc}），60 秒後重開"); time.sleep(60)
        if not os.path.exists(STOP):
            proc = start()
