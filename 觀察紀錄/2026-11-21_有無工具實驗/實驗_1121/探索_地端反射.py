"""（反射版）11/22 12:5x 使用者「完成後再測試只用反射或者快想的強度」→ 等 探索_地端.py 結束後，地端 沒工具／有工具 都用 反射 再各一段。
11/22 使用者「本地的 27b 也各自試試看」→ 地端 gamer-27b-ft-run3（DGX 8888）沒有工具／有工具 各一段，規則同 探索2.py：
單人局（ESC 查只有 thortsui）、第一局單人局結束就停（最長 2 小時）、記進場輪數、鼓勵探索紀錄。思考兩邊都是 慢想。
等 探索2.py（PID 參數）結束（它會把地端記憶搬回）→ 再搬走、任務指示加探索段 → 地端原生 → 地端＋工具（--try-first，記憶清空）→ 搬回。"""
import os, shutil, subprocess, sys, time
from datetime import datetime, timedelta

WT = r"D:\Documents\ThorWorkSpace\gamer AI 工具版"
HERE = os.path.dirname(os.path.abspath(__file__))
G = os.path.join(WT, "專案", "動物醫院")
MEM = os.path.join(G, "記憶", "工具版")
OLD = os.path.join(G, "_舊記憶_1121", "工具版")
TASK = os.path.join(G, "記憶", "任務指示.txt")
HEAD = "[2026-11-21 使用者]\n使用工具，盡可能多探索這支遊戲，發現的東西都記錄下來，紀錄越多越好。\n\n"
STOP = os.path.join(WT, "stop.txt")
PY = r"C:\Python313\python.exe"
LOG = os.path.join(HERE, "探索_地端反射.log")
RAW = os.path.join(HERE, "raw_agent.py")
RUN = os.path.join(HERE, "sonnet_工具版_run.py")
CAP_H = 2


def log(m):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%m/%d %H:%M:%S} {m}\n")


def roblox():
    subprocess.run(["taskkill", "/IM", "RobloxPlayerBeta.exe", "/F"], capture_output=True)
    time.sleep(5)
    os.startfile("roblox://placeId=78515283254292")
    time.sleep(75)
    log("Roblox 重開回大廳")


def run(label, args, env_extra, cwd, out):
    if os.path.exists(STOP):
        os.remove(STOP)
    roblox()
    until = (datetime.now() + timedelta(hours=CAP_H)).strftime("%Y-%m-%d %H:%M")
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1", SOLO_GUARD="1", SOLO_GUARD_DIR=HERE, SOLO_STOP_AFTER="1", SOLO_STOP_FILE=STOP, **env_extra)   # 隊友檢查
    with open(out, "a", encoding="utf-8") as o:
        p = subprocess.Popen([PY, "-u"] + args + ["--until", until], cwd=cwd, env=env, stdout=o, stderr=subprocess.STDOUT,
                             creationflags=subprocess.CREATE_NO_WINDOW)
        log(f"{label} 啟動 PID {p.pid} 到 {until}")
        p.wait()
    log(f"{label} 結束 rc {p.returncode}")
    time.sleep(20)


def audit(outdir):
    """原生段跑完稽核：工具使用.jsonl 有沒有連網、搜尋、讀別的專案（使用者：絕對禁止搜尋以及找攻略）。"""
    import re
    f = os.path.join(HERE, outdir, "工具使用.jsonl")
    if not os.path.exists(f):
        log(f"稽核 {outdir}：沒有 工具使用.jsonl（沒用任何 Claude 工具）"); return
    pat = re.compile(r"http|www\.|urllib|requests|socket|curl|wget|WebSearch|WebFetch|攻略|wiki|gamer AI|專案[\\/]動物醫院|_舊記憶|Sitcon 2026[\\/](簡報|大綱)", re.I)
    lines = open(f, encoding="utf-8").read().splitlines()
    hit = [l[:300] for l in lines if pat.search(l)]
    log(f"稽核 {outdir}：工具使用 {len(lines)} 次，可疑 {len(hit)} 次")
    with open(os.path.join(HERE, outdir, "稽核.txt"), "w", encoding="utf-8") as o:
        o.write("\n".join(hit) or "沒有可疑紀錄")


def stash(name):
    dst = os.path.join(G, f"_{name}_1121")
    os.makedirs(dst, exist_ok=True)
    shutil.move(MEM, os.path.join(dst, "工具版"))
    os.makedirs(MEM, exist_ok=True)
    log(f"記憶搬到 {dst}")



WAIT = sys.argv[1]
log(f"等 探索_地端.py PID {WAIT}")
while WAIT in subprocess.run(["tasklist", "/FI", f"PID eq {WAIT}"], capture_output=True, text=True).stdout:
    time.sleep(30)
log("探索_地端.py 結束")
time.sleep(15)
if os.path.exists(OLD):
    log("⚠ _舊記憶_1121/工具版 已存在，停下來"); sys.exit(1)
shutil.move(MEM, OLD); os.makedirs(MEM, exist_ok=True)
log("地端記憶搬到 _舊記憶_1121/工具版")
s = open(TASK, encoding="utf-8").read()
if not s.startswith(HEAD):
    open(TASK, "w", encoding="utf-8").write(HEAD + s)

def tool(label, brain, eff, name):
    run(label, [RUN, "--brain", brain, "--effort", eff, "--try-first"], {}, WT, os.path.join(HERE, f"{name}.out"))
    stash(name)

def raw(label, model, eff, out):
    run(label, [RAW, "--effort", eff], {"RAW_MODEL": model, "RAW_OUT": out, "RAW_EXPLORE": "1"}, HERE, os.path.join(HERE, f"{out}.out"))
    audit(out)

try:
    run("L3 地端 原生 反射", [RAW], {"RAW_MODEL": "local", "RAW_OUT": "地端_反射_探索", "RAW_LOCAL_EFFORT": "反射", "RAW_EXPLORE": "1"},
        HERE, os.path.join(HERE, "地端_反射_探索.out"))
    audit("地端_反射_探索")
    tool("L4 地端＋工具 反射", "", "反射", "地端探索反射")
finally:
    if os.path.isdir(OLD):
        if os.path.isdir(MEM) and os.listdir(MEM):
            stash("探索2中斷")
        if os.path.isdir(MEM):
            os.rmdir(MEM)
        shutil.move(OLD, MEM)
        log("地端原本的記憶已搬回 記憶/工具版")
    s = open(TASK, encoding="utf-8").read()
    if s.startswith(HEAD):
        open(TASK, "w", encoding="utf-8").write(s[len(HEAD):])
        log("任務指示探索那段已拿掉")
    if os.path.exists(STOP):
        os.remove(STOP)
    log("全部結束")
