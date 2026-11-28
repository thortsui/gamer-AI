"""跑到指定時間：代理意外結束就重開，時間到放 stop.txt。使用者放 stop.txt 就一起停，不再重開。
11/04 使用者「繼續跑 不要停 到明天晚上」。
用法：python -u 工具版/run_until.py --until "2026-11-05 23:00" --game Minecraft --window javaw.exe
"""
import argparse
import os
import subprocess
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
STOP = os.path.join(ROOT, "stop.txt")
RESTART_WAIT = 60       # 當掉後等多久再重開，免得連續當掉時狂開
STOP_WAIT = 900         # 放 stop.txt 後最多等代理收尾多久（一輪上限 600 秒）


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--until", required=True)
    ap.add_argument("--game", required=True)
    ap.add_argument("--window", default="")
    ap.add_argument("--tools-only", action="store_true")   # 11/13 只輸出工具
    ap.add_argument("--effort", default="慢想")   # 11/13 使用者「預設最低的反射思考」→ 傳 反射
    ap.add_argument("--try-first", action="store_true")   # 11/28 使用者「開」（多嘗試優先＋盡量探索）
    a = ap.parse_args()
    until = datetime.strptime(a.until, "%Y-%m-%d %H:%M")
    mdir = os.path.join(ROOT, "專案", a.game, "記憶", "工具版")
    log_path = os.path.join(mdir, "跑到時間.log")

    def log(msg):
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%m/%d %H:%M:%S} {msg}\n")

    def start():
        stamp = datetime.now().strftime("%m%d_%H%M")
        for name in ("stdout", "stderr"):
            p = os.path.join(mdir, name + ".log")
            if os.path.exists(p) and os.path.getsize(p):
                os.replace(p, os.path.join(mdir, f"{name}_{stamp}.log"))
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1", GAMER_TOOLFIX="1", GAMER_VERIFY="1")
        if a.window:
            env["GAMER_WINDOW_PROC"] = a.window
        cmd = [sys.executable, "-u", os.path.join("工具版", "tool_agent.py"), "--game", a.game, "--live", "--rounds", "5000",
               "--deadline", "600", "--max-tokens", "8000", "--effort", a.effort, "--lock-effort", "--verify2", "--steps", "2",
               "--no-small-update", "--tree-rotate", "30", "--tree-t0", datetime.now().strftime("%H:%M"), "--slim-system", "--mem-check", "120"] + (["--tools-only"] if a.tools_only else []) + (["--try-first"] if a.try_first else [])
        out = open(os.path.join(mdir, "stdout.log"), "w", encoding="utf-8")
        err = open(os.path.join(mdir, "stderr.log"), "w", encoding="utf-8")
        proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=out, stderr=err, creationflags=subprocess.CREATE_NO_WINDOW)
        log(f"啟動代理 PID {proc.pid}")
        return proc

    log(f"開始，跑到 {a.until}")
    proc = start()
    while True:
        time.sleep(20)
        if datetime.now() >= until and not os.path.exists(STOP):
            open(STOP, "w").close()
            log("時間到，放 stop.txt")
        if os.path.exists(STOP):
            try:
                proc.wait(STOP_WAIT)
                log(f"收到 stop.txt，代理結束（rc {proc.returncode}）")
            except subprocess.TimeoutExpired:
                log(f"收到 stop.txt，等 {STOP_WAIT} 秒代理還沒結束")
            return
        rc = proc.poll()
        if rc is not None:
            log(f"代理意外結束（rc {rc}），{RESTART_WAIT} 秒後重開")
            time.sleep(RESTART_WAIT)
            if not os.path.exists(STOP):
                proc = start()


if __name__ == "__main__":
    main()
