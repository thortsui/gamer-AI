"""11/21 使用者「盡可能用比較較弱的模型，將數字看起來越漂亮越好」→ 套用工具前／後，各 1 小時、同一把尺量有反應：
等接續實驗（接續.py PID 40484）結束後依序：
  1 Haiku 原生（raw_agent，RAW_MODEL=haiku，effort low）
  2 Haiku＋工具版（tool_agent --brain haiku，快思推理＝low），記憶清空
  3 地端 原生（raw_agent，RAW_MODEL=local，慢想）
  4 地端＋工具版（run_until.py，慢想），記憶清空
每段前：重開 Roblox 回大廳、刪 stop.txt。工具版每段跑完把記憶搬去 _<名稱>_1121；全部結束把地端原本的記憶（_舊記憶_1121）搬回來、拿掉任務指示第一段。"""
import os, shutil, subprocess, sys, time
from datetime import datetime, timedelta

WT = r"D:\Documents\ThorWorkSpace\gamer AI 工具版"
HERE = os.path.dirname(os.path.abspath(__file__))
G = os.path.join(WT, "專案", "動物醫院")
MEM = os.path.join(G, "記憶", "工具版")
STOP = os.path.join(WT, "stop.txt")
PY = r"C:\Python313\python.exe"
LOG = os.path.join(HERE, "弱模型.log")


def log(m):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%m/%d %H:%M:%S} {m}\n")


def alive(pid):
    return str(pid) in subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True).stdout


def stash(name):
    """把現在的工具版記憶搬走存起來（不刪）。"""
    if os.path.isdir(MEM) and os.listdir(MEM):
        dst = os.path.join(G, f"_{name}_1121")
        os.makedirs(dst, exist_ok=True)
        shutil.move(MEM, os.path.join(dst, "工具版"))
        log(f"記憶搬到 {dst}")
    os.makedirs(MEM, exist_ok=True)


def roblox():
    subprocess.run(["taskkill", "/IM", "RobloxPlayerBeta.exe", "/F"], capture_output=True)
    time.sleep(5)
    os.startfile("roblox://placeId=78515283254292")
    time.sleep(75)
    sys.path[:0] = [WT, os.path.join(WT, "工具版")]
    try:
        import tool_agent
        t = tool_agent.Tools("動物醫院", tool_agent.load_catalog("動物醫院"), live=True)
        t._fg_ok(); time.sleep(2)
        t.grab().save(os.path.join(HERE, f"大廳_{datetime.now():%H%M}.jpg"))
    except Exception as e:  # noqa: BLE001
        log(f"截大廳圖失敗 {e}")
    log("Roblox 重開回大廳")


def run(label, args, env_extra, cwd, out):
    for s in (STOP,):
        if os.path.exists(s):
            os.remove(s)
    roblox()
    until = (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M")
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1", **env_extra)
    with open(out, "a", encoding="utf-8") as o:
        p = subprocess.Popen([PY, "-u"] + args + ["--until", until], cwd=cwd, env=env, stdout=o, stderr=subprocess.STDOUT,
                             creationflags=subprocess.CREATE_NO_WINDOW)
        log(f"{label} 啟動 PID {p.pid} 到 {until}")
        p.wait()
    log(f"{label} 結束 rc {p.returncode}")
    time.sleep(20)


log("等接續實驗 PID 40484")
while alive(40484):
    time.sleep(30)
log("接續實驗結束")
stash("sonnet")                                    # Sonnet 工具版 3h＋接續 1h 的記憶

RAW = os.path.join(HERE, "raw_agent.py")
run("1 Haiku 原生", [RAW, "--effort", "low"], {"RAW_MODEL": "haiku", "RAW_OUT": "haiku_動物醫院"}, HERE,
    os.path.join(HERE, "haiku_原生.out"))
run("2 Haiku＋工具版", [os.path.join(HERE, "sonnet_工具版_run.py"), "--brain", "haiku", "--effort", "快思推理"], {}, WT,
    os.path.join(HERE, "haiku_工具版.out"))
stash("haiku")
run("3 地端 原生", [RAW], {"RAW_MODEL": "local", "RAW_OUT": "地端_動物醫院", "RAW_LOCAL_EFFORT": "慢想"}, HERE,
    os.path.join(HERE, "地端_原生.out"))
run("4 地端＋工具版", [os.path.join("工具版", "run_until.py"), "--game", "動物醫院"], {}, WT, os.path.join(HERE, "地端_工具版.out"))
stash("地端重學")

# 還原地端原本的記憶與任務指示
old = os.path.join(G, "_舊記憶_1121", "工具版")
if os.path.isdir(old) and not os.listdir(MEM):
    os.rmdir(MEM)
    shutil.move(old, MEM)
    log("地端原本的記憶已搬回 記憶/工具版")
p = os.path.join(G, "記憶", "任務指示.txt")
s = open(p, encoding="utf-8").read()
head = "[2026-11-21 使用者]\n使用工具，盡可能多研究這支遊戲的內容。\n\n"
if s.startswith(head):
    open(p, "w", encoding="utf-8").write(s[len(head):])
    log("任務指示第一段已拿掉")
if os.path.exists(STOP):
    os.remove(STOP)
log("全部結束")
