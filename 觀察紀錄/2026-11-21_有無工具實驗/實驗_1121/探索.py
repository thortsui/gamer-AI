"""11/21 晚 使用者「重新跑一次 兩個 X High 的 工具，用鼓勵探索的新版本 盡可能的去鼓勵探索紀錄越多越好；完成後 low 的再來一次，各一個小時」
四段各 1 小時，Sonnet，動物醫院，全部從零（原生新 session 新資料夾；工具版記憶清空），每段前重開 Roblox 回大廳、刪 stop.txt：
  1 Sonnet 原生 xhigh（RAW_EXPLORE=1）          → sonnet_xhigh_探索/
  2 Sonnet＋工具版 慢想(xhigh) --try-first（⑥探索紀錄）→ 記憶存 _sonnet探索xhigh_1121
  3 Sonnet 原生 low（RAW_EXPLORE=1）             → sonnet_low_探索/
  4 Sonnet＋工具版 快思推理(low) --try-first     → 記憶存 _sonnet探索low_1121
工具版那兩段：地端記憶搬去 _舊記憶_1121/工具版，任務指示最上面加探索那段；全部結束搬回、拿掉。"""
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
LOG = os.path.join(HERE, "探索.log")
RAW = os.path.join(HERE, "raw_agent.py")
RUN = os.path.join(HERE, "sonnet_工具版_run.py")


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
    until = (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M")
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1", SOLO_GUARD="1", SOLO_GUARD_DIR=HERE, **env_extra)   # 隊友檢查
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


# 準備：地端記憶搬走、任務指示加探索段
if os.path.exists(OLD):
    log("⚠ _舊記憶_1121/工具版 已存在，停下來"); sys.exit(1)
shutil.move(MEM, OLD); os.makedirs(MEM, exist_ok=True)
log("地端記憶搬到 _舊記憶_1121/工具版")
s = open(TASK, encoding="utf-8").read()
if not s.startswith(HEAD):
    open(TASK, "w", encoding="utf-8").write(HEAD + s)
    log("任務指示加探索那段")

try:
    run("1 Sonnet 原生 xhigh 探索", [RAW, "--effort", "xhigh"], {"RAW_MODEL": "sonnet", "RAW_OUT": "sonnet_xhigh_探索", "RAW_EXPLORE": "1"},
        HERE, os.path.join(HERE, "sonnet_xhigh_探索.out"))
    audit("sonnet_xhigh_探索")
    run("2 Sonnet＋工具版 慢想 多嘗試探索", [RUN, "--brain", "sonnet", "--effort", "慢想", "--try-first"], {}, WT,
        os.path.join(HERE, "sonnet工具_xhigh_探索.out"))
    stash("sonnet探索xhigh")
    run("3 Sonnet 原生 low 探索", [RAW, "--effort", "low"], {"RAW_MODEL": "sonnet", "RAW_OUT": "sonnet_low_探索", "RAW_EXPLORE": "1"},
        HERE, os.path.join(HERE, "sonnet_low_探索.out"))
    audit("sonnet_low_探索")
    run("4 Sonnet＋工具版 快思推理 多嘗試探索", [RUN, "--brain", "sonnet", "--effort", "快思推理", "--try-first"], {}, WT,
        os.path.join(HERE, "sonnet工具_low_探索.out"))
    stash("sonnet探索low")
finally:
    # 還原地端記憶與任務指示
    if os.path.isdir(OLD):
        if os.path.isdir(MEM) and os.listdir(MEM):
            stash("探索中斷")
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
