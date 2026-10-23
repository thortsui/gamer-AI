"""10/24 使用者：「這個挑戰完成後進行另一個挑戰：sub Agent 用 haiku 玩一小時、再 sonnet 一小時、最後 sonnet 開自由搜尋
一小時，能用的工具、能取得的資訊都跟本地 AI 一模一樣，記錄一樣的數據；目的是看模型本身的智力、對圖片的判斷程度和速度
會不會影響任務成功率和產出。這個會作為實驗數據需要重點保存。」

跑法（本地代理收工、停著之後）：python 工具版/模型比較實驗.py [--minutes 60]
1. 存起點：記憶/工具版 整個＋記憶/ 頂層檔案 → 實驗_模型比較_1024/_起點/（三個模型都從這同一份記憶開始）；
   順便切出本地模型最後一小時（收工前 60 分鐘）的紀錄算 stats 當對照。
2. 每個模型：還原起點 → 照本地同一組參數＋--brain 啟動 → 滿 60 分鐘放 stop.txt → 等結束 →
   切出這一小時的紀錄（jsonl 新增的部分）、claude大腦.jsonl、stdout/stderr、記憶有變的文字檔 → stats。
3. 三個都跑完：還原起點（本地模型的記憶回到收工時的樣子）、寫 結果.md、commit+push。
中途掛掉重跑會接著做：起點已存就不重存、已有 stats.txt 的模型跳過。
"""
import argparse
import filecmp
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MEM = os.path.join(ROOT, "專案", "動物醫院", "記憶")
TV = os.path.join(MEM, "工具版")
OUT = os.path.join(HERE, "實驗_模型比較_1024")
START = os.path.join(OUT, "_起點")
STOP = os.path.join(ROOT, "stop.txt")
ARGS = ["--game", "動物醫院", "--live", "--rounds", "5000", "--deadline", "600", "--max-tokens", "8000",
        "--effort", "慢想", "--lock-effort", "--verify2", "--steps", "2", "--no-small-update"]   # 跟本地 10/24 挑戰同一組
RUNS = [("haiku", False, "1_haiku"), ("sonnet", False, "2_sonnet"), ("sonnet", True, "3_sonnet搜尋")]
ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
TEXT = (".md", ".json", ".jsonl", ".txt", ".log")
SKIP = ("stdout.log", "stderr.log", "claude大腦.jsonl")


def log(msg):
    line = time.strftime("%m/%d %H:%M:%S ") + msg
    print(line, flush=True)
    with open(os.path.join(OUT, "進度.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def top_files(d):
    return [f for f in os.listdir(d) if os.path.isfile(os.path.join(d, f))]


def is_round_file(f):
    return f[:4].isdigit() and f.endswith(".jsonl")


def snapshot():
    if os.path.isdir(START):
        log("起點已存在（續跑），不重存")
        return
    os.makedirs(os.path.join(START, "頂層"))
    shutil.copytree(TV, os.path.join(START, "工具版"))
    for f in top_files(MEM):
        shutil.copy2(os.path.join(MEM, f), os.path.join(START, "頂層", f))
    log("起點存好")


def restore():
    shutil.rmtree(TV)
    shutil.copytree(os.path.join(START, "工具版"), TV)
    keep = set(top_files(os.path.join(START, "頂層")))
    for f in top_files(MEM):
        if f not in keep:
            os.remove(os.path.join(MEM, f))
    for f in keep:
        shutil.copy2(os.path.join(START, "頂層", f), os.path.join(MEM, f))
    log("記憶還原成起點")


def agent_running():
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
          "Where-Object { $_.CommandLine -like '*tool_agent.py*' } | Measure-Object | ForEach-Object Count")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True)
    return (r.stdout.strip() or "0") != "0"


def read_lines(p):
    return open(p, encoding="utf-8", errors="replace").read().splitlines() if os.path.exists(p) else []


def run_stats(d):
    st = subprocess.run([sys.executable, os.path.join(HERE, "stats.py"), "--file", os.path.join(d, "每輪紀錄.jsonl")],
                        capture_output=True, text=True, encoding="utf-8", errors="replace", env=ENV)
    open(os.path.join(d, "stats.txt"), "w", encoding="utf-8").write(st.stdout + st.stderr)


def collect(label):
    d = os.path.join(OUT, label)
    os.makedirs(os.path.join(d, "記憶有變的檔"), exist_ok=True)
    rounds = []
    for f in sorted(os.listdir(TV)):
        if is_round_file(f):                     # 起點有這個檔就取多出來的行，沒有就整個檔
            rounds += read_lines(os.path.join(TV, f))[len(read_lines(os.path.join(START, "工具版", f))):]
    with open(os.path.join(d, "每輪紀錄.jsonl"), "w", encoding="utf-8") as fo:
        fo.write("\n".join(rounds) + ("\n" if rounds else ""))
    for f in ("stdout.log", "stderr.log"):
        if os.path.exists(os.path.join(TV, f)):
            shutil.copy2(os.path.join(TV, f), os.path.join(d, f))
    calls = read_lines(os.path.join(TV, "claude大腦.jsonl"))[len(read_lines(os.path.join(START, "工具版", "claude大腦.jsonl"))):]
    with open(os.path.join(d, "claude大腦.jsonl"), "w", encoding="utf-8") as fo:
        fo.write("\n".join(calls) + ("\n" if calls else ""))
    changed = []
    for dp, _, fn in os.walk(TV):
        for f in fn:
            if not f.endswith(TEXT) or f in SKIP or is_round_file(f):
                continue
            p = os.path.join(dp, f)
            rel = os.path.relpath(p, TV)
            o = os.path.join(START, "工具版", rel)
            if not os.path.exists(o) or not filecmp.cmp(p, o, shallow=False):
                dst = os.path.join(d, "記憶有變的檔", rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(p, dst)
                changed.append(rel)
    for f in top_files(MEM):
        o = os.path.join(START, "頂層", f)
        if f.endswith(TEXT) and (not os.path.exists(o) or not filecmp.cmp(os.path.join(MEM, f), o, shallow=False)):
            shutil.copy2(os.path.join(MEM, f), os.path.join(d, "記憶有變的檔", "頂層_" + f))
            changed.append("頂層/" + f)
    run_stats(d)
    log(f"{label}：{len(rounds)} 輪、記憶有變 {len(changed)} 個檔、stats 寫好")


def brain_sum(label):
    p = os.path.join(OUT, label, "claude大腦.jsonl")
    rs = [json.loads(l) for l in read_lines(p) if l.strip()]
    if not rs:
        return {}
    ok = [r for r in rs if r.get("結果") == "success"]
    sec = sorted(r["秒"] for r in ok) or [0]
    return {"呼叫": len(rs), "成功": len(ok), "錯誤": len(rs) - len(ok), "中位秒": sec[len(sec) // 2],
            "費用USD": round(sum(r.get("費用") or 0 for r in rs), 3),
            "輸出token": sum(r.get("輸出") or 0 for r in rs), "思考token": sum(r.get("思考字") or 0 for r in rs),
            "搜尋": sum(1 for r in rs for t in r.get("工具", []) if t.startswith("WebSearch")),
            "抓網頁": sum(1 for r in rs for t in r.get("工具", []) if t.startswith("WebFetch"))}


def local_baseline(minutes):
    """本地模型收工前最後 N 分鐘（同一組參數、慢想）當對照。"""
    d = os.path.join(OUT, "0_本地最後一小時")
    os.makedirs(d, exist_ok=True)
    recs = []
    for f in sorted(f for f in os.listdir(TV) if is_round_file(f))[-2:]:
        recs += [l for l in read_lines(os.path.join(TV, f)) if l.strip()]
    if not recs:
        return

    def sec(l):
        h, m, s = map(int, json.loads(l)["時間"].split(":"))
        return h * 3600 + m * 60 + s
    end = sec(recs[-1])
    pick = [l for l in recs[-2000:] if (end - sec(l)) % 86400 <= minutes * 60]
    open(os.path.join(d, "每輪紀錄.jsonl"), "w", encoding="utf-8").write("\n".join(pick) + "\n")
    run_stats(d)
    log(f"本地對照：{len(pick)} 輪")


def run_one(model, search, label, minutes):
    if os.path.exists(os.path.join(OUT, label, "stats.txt")):
        log(f"{label} 已跑過，跳過")
        return
    restore()
    cmd = [sys.executable, "-u", os.path.join(HERE, "tool_agent.py")] + ARGS + ["--brain", model] + (["--brain-search"] if search else [])
    log(f"{label} 開始：{' '.join(cmd[3:])}")
    t0 = time.time()
    with open(os.path.join(TV, "stdout.log"), "w", encoding="utf-8") as so, \
            open(os.path.join(TV, "stderr.log"), "w", encoding="utf-8") as se:
        p = subprocess.Popen(cmd, cwd=ROOT, stdout=so, stderr=se, env=ENV)
        while p.poll() is None and time.time() - t0 < minutes * 60:
            time.sleep(5)
        if p.poll() is None:
            open(STOP, "w", encoding="utf-8").write("模型比較實驗：時間到")
            log(f"{label} 滿 {minutes} 分鐘，放 stop.txt")
            try:
                p.wait(timeout=900)
            except subprocess.TimeoutExpired:
                p.kill()
                log(f"{label} 15 分鐘沒停，強制結束")
        else:
            log(f"{label} 提早結束（exit {p.returncode}）")
    if os.path.exists(STOP):
        os.remove(STOP)
    log(f"{label} 實際 {round((time.time() - t0) / 60, 1)} 分鐘")
    collect(label)


def report(minutes):
    rows = []
    for label in ["0_本地最後一小時"] + [r[2] for r in RUNS]:
        p = os.path.join(OUT, label, "stats.txt")
        rows.append(f"## {label}\n" + (f"大腦呼叫：{json.dumps(brain_sum(label), ensure_ascii=False)}\n" if label[0] != "0" else "")
                    + "```\n" + (open(p, encoding="utf-8").read() if os.path.exists(p) else "（沒有）") + "```\n")
    open(os.path.join(OUT, "結果.md"), "w", encoding="utf-8").write(
        f"# 模型比較實驗（10/24，各 {minutes:g} 分鐘）\n\n"
        "三個模型都從同一份記憶（本地模型收工時）開始，同一組參數（慢想鎖定、二次驗證、兩步、時限 600、max_tokens 8000、"
        "--no-small-update），同樣的 system、面板、截圖、103 個工具；每次叫大腦都是全新的 `claude -p`（不帶上一輪對話，跟本地一樣）。"
        "搜尋版多了 WebSearch／WebFetch。遊戲現場（在大廳或局內、別的玩家）沒辦法還原，每個模型開場的畫面不同。\n\n"
        + "\n".join(rows))
    log("結果.md 寫好")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=60)
    ap.add_argument("--test", default="", help="測試用：另一個遊戲資料夾＋離線截圖（例：claude測試,截圖路徑），不碰真的記憶、不 push")
    a = ap.parse_args()
    global MEM, TV, OUT, START
    if a.test:
        game, img = a.test.split(",", 1)
        MEM = os.path.join(ROOT, "專案", game, "記憶")
        TV = os.path.join(MEM, "工具版")
        OUT = os.path.join(ROOT, "專案", game, "實驗測試")
        START = os.path.join(OUT, "_起點")
        ARGS[ARGS.index("--game") + 1] = game
        ARGS[ARGS.index("--live")] = "--image"
        ARGS.insert(ARGS.index("--image") + 1, img)
    os.makedirs(OUT, exist_ok=True)
    if agent_running() and not a.test:
        sys.exit("本地代理還在跑，先收工再開實驗")
    snapshot()
    if not os.path.exists(os.path.join(OUT, "0_本地最後一小時", "stats.txt")):
        local_baseline(a.minutes)
    for model, search, label in RUNS:
        run_one(model, search, label, a.minutes)
    restore()
    report(a.minutes)
    if a.test:
        return
    subprocess.run(["git", "add", "-A", OUT], cwd=ROOT)
    subprocess.run(["git", "commit", "-qm", "10/24 模型比較實驗結果（haiku／sonnet／sonnet 搜尋 各一小時）\n\n"
                    "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>\n"
                    "Claude-Session: https://claude.ai/code/session_01L3jYTqqjiukHgtLXxRHTLT"], cwd=ROOT)
    subprocess.run(["git", "push", "-q"], cwd=ROOT)
    log("全部完成、已 commit+push")


if __name__ == "__main__":
    main()
