"""11/21 使用者（後來改：可用 Claude Code 預設工具，只擋搜尋／連網；同一個 session 接著）：Sonnet 玩動物醫院 3 小時。不能上網搜尋、限制跟地端全部一樣，但「不能動用我的工具」。
→ 這支不用 gamer AI 工具版 的任何工具（量測、走過去、找長相、記憶工具…），只有最底層的鍵盤滑鼠：
   按鍵、點、壓滑鼠、拖滑鼠、移滑鼠、滾輪、等。每輪：截圖 → 全新的 claude -p（sonnet，不給任何 Claude 工具＝不能搜尋）→ 照它寫的按。
- 限制跟地端一樣（借用工具版的同一套擋法：視窗不在前景不按、TABOO 字附近不點、會跳離遊戲的鍵不按）：
  不花錢、不換職業、不離開遊戲、不讀遊戲內部資料、不給攻略。
- 它沒有記憶工具：只給它上一輪的畫面、最近 10 輪自己寫的話、和它自己寫的一段筆記（每輪可整段改寫）。
- 紀錄（全部在 OUT 資料夾）：rounds.jsonl 每輪時間、秒數、費用、token、它寫的、每個動作的結果、右上角任務字（OCR）；
  shots/ 每輪截圖；任務字.jsonl 任務字變了就記一筆；自評完成.jsonl 它自己說完成的。
用法：python -u raw_agent.py --until "2026-11-21 08:45"   （停：worktree 根目錄或 OUT 放 stop.txt）
"""
import argparse, base64, io, json, os, re, subprocess, sys, tempfile, time
from datetime import datetime

WT = r"D:\Documents\ThorWorkSpace\gamer AI 工具版"
sys.path.insert(0, os.path.join(WT, "工具版")); sys.path.insert(0, WT)
os.chdir(WT)
import tool_agent  # noqa: E402

GAME = "動物醫院"
HERE = os.path.dirname(os.path.abspath(__file__))
# 11/21 使用者「盡可能用比較弱的模型」→ 環境變數換模型／資料夾：RAW_MODEL＝sonnet／haiku／local（地端 8888）
RAW_MODEL = os.environ.get("RAW_MODEL", "sonnet")
LOCAL_EP, LOCAL_MODEL = "http://192.168.0.126:8888/v1", "gamer-27b-ft-run3"
LOCAL_EFFORT = os.environ.get("RAW_LOCAL_EFFORT", "慢想")
OUT = os.path.join(HERE, os.environ.get("RAW_OUT", "sonnet_動物醫院"))
SHOTS = os.path.join(OUT, "shots")
os.makedirs(SHOTS, exist_ok=True)
STOPS = [os.path.join(WT, "stop.txt"), os.path.join(OUT, "stop.txt")]
MODEL, EFFORT, MAX_OUT, DEADLINE = RAW_MODEL, "xhigh", 16000, 600
LEAVE = ("leave", "離開", "退出", "quit", "exit", "disconnect", "中斷")
NO_KEYS = {"win", "winleft", "winright", "apps", "alt", "altleft", "altright", "f11", "f12", "printscreen", "ctrl", "ctrlleft", "ctrlright"}
CONFIRM = ("enter", "return", "space", "numpadenter")
CWD = os.path.join(OUT, "workspace"); os.makedirs(CWD, exist_ok=True)
SESSION = {"id": ""}
DENY = ["WebSearch", "WebFetch", "Bash(curl:*)", "Bash(wget:*)", "Bash(Invoke-WebRequest:*)", "Bash(iwr:*)", "Bash(Invoke-RestMethod:*)", "Bash(irm:*)", "Bash(start http:*)", "Bash(explorer http:*)", "PowerShell(Invoke-WebRequest:*)", "PowerShell(Invoke-RestMethod:*)", "PowerShell(iwr:*)", "PowerShell(irm:*)", "PowerShell(curl:*)", "PowerShell(wget:*)", "PowerShell(Start-Process http:*)",
        # 11/21 晚 使用者「原生只用原生工具，絕對禁止搜尋以及找攻略」→ 子代理（Agent/Task）可能不繼承這份封鎖，一起關掉
        "Agent", "Task", "Workflow", "SendMessage", "ListAgents", "RemoteTrigger", "DesignSync", "CronCreate"]
# 同上：它自己寫的 python／curl 程式連網，靠代理環境變數擋（指到不存在的 127.0.0.1:9）；只放行 Claude 自己的 API 網域
_PX = "http://127.0.0.1:9"
NO_NET_ENV = {k: _PX for k in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy")}
NO_NET_ENV.update({k: "anthropic.com,.anthropic.com,claude.ai,.claude.ai,claude.com,.claude.com,localhost,127.0.0.1"
                   for k in ("NO_PROXY", "no_proxy")})

SYSTEM = """你在玩一款 Roblox 遊戲，叫做「Animal Anomaly」（動物醫院）。你從來沒玩過，也沒有任何攻略。
目標：盡可能研究更多的遊戲內容——這遊戲有哪些地方、東西、系統、任務，怎麼做到。剩多少時間每輪會告訴你。
EXPLORE
遊戲裡你只能看螢幕截圖、送鍵盤和滑鼠，跟一般人一樣（照下面的動作格式寫，程式替你按）。
另外你可以用 Claude Code 預設的工具幫自己（讀寫檔案、執行指令、寫程式等），工作資料夾是 WORKDIR，可以在裡面存筆記、截圖、程式。
唯一禁止：不能上網搜尋或抓網頁，包括用任何指令或程式連網查資料、看攻略、看影片。也不要讀這台電腦上別人的專案資料夾（裡面有別的 AI 學過這遊戲的紀錄，看了就等於看攻略）。
每一輪你會收到：現在的畫面、上一輪的畫面。前面的對話你都記得（同一個 session），也可以自己寫檔案記。

絕對限制（違反的動作程式會擋掉）：
- 不准花任何錢：Robux、購買、解鎖、升級、付費復活、任何 Buy／Purchase／$ 按鈕。
- 不准換職業（護士、醫生、實習等選項不要點）。
- 不准離開遊戲（Leave、離開、退出之類的按鈕不要點）。

使用者之前交代過的話（跟給地端模型的一樣）：
- 這隻遊戲從頭重新學一次，自己理解，自己玩。
- 通常配對的正確地方，會在重生點正面對著的地方，而不是在背後；背後通常是訓練場之類的，並不是正式遊戲。
- 大部分遊戲裡面像是磁磚、有格子線的牆壁之類的，每一格的大小都會是固定或者很接近的，可以當作一種單位：數有幾格就知道多長、多遠。
- 不要去捲鏡頭（不要用滾輪拉近／拉遠切換人稱）。

你可以用的動作（每輪最多 5 個，依序執行，座標 x,y 是畫面比例 0～1，左上角是 0,0）：
- 按(鍵,秒)：按住一個鍵幾秒（0.05～5）。例：按(w,1.5)、按(e,1.5)、按(space,0.1)、按(esc,0.1)、按(1,0.1)
- 點(左或右,x,y)：游標移到那裡點一下。例：點(左,0.50,0.83)
- 壓滑鼠(左或右,秒)：在游標現在的位置壓住滑鼠鍵幾秒（0.05～5）
- 拖(右,dx,dy)：壓住滑鼠右鍵往右 dx、往下 dy 像素拖（Roblox 常用來轉鏡頭；負數往左／往上，各 ±1500 內）
- 移滑鼠(dx,dy)：不按鍵，游標相對移動 dx,dy 像素
- 滾輪(格)：-5～5
- 等(秒)：什麼都不按，等 0.5～10 秒

只輸出一個 JSON 物件，不要其他字：
{"看到":"這張畫面有什麼（具體）","想":"你的判斷與下一步為什麼這樣做","動作":["按(w,1.0)","..."],
 "筆記":"（選填）要整段換掉的新筆記，寫你學到的遊戲內容、地圖、怎麼做到的；不填就保留舊的。最多 3000 字",
 "完成":"（選填）你覺得剛剛完成了遊戲裡的哪一件任務，沒有就不填"}
"""
# 11/21 晚 使用者「用鼓勵探索的新版本 盡可能的去鼓勵探索 紀錄越多越好」→ RAW_EXPLORE=1 才加（跟工具版 --try-first ⑥ 同一段意思）
EXPLORE_TXT = ("盡量探索、紀錄越多越好：優先去沒去過的地方、試沒試過的東西和介面；每發現一個新地方、新東西、新介面、新規則或任務字變了，"
               "當輪就寫進筆記（或工作資料夾的檔案）。沒有新發現的那輪也記一句「試了什麼、沒反應」。\n")
SYSTEM = SYSTEM.replace("EXPLORE", EXPLORE_TXT if os.environ.get("RAW_EXPLORE") == "1" else "", 1)


def now():
    return datetime.now().strftime("%H:%M:%S")


def jdump(path, obj):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def b64(im, w=1280, q=80):
    im = im.copy(); im.thumbnail((w, w))
    buf = io.BytesIO(); im.convert("RGB").save(buf, "JPEG", quality=q)
    return base64.b64encode(buf.getvalue()).decode()


def ask(content):
    sp = os.path.join(OUT, "system.txt")
    open(sp, "w", encoding="utf-8").write(SYSTEM.replace("WORKDIR", CWD))
    # 11/21 使用者：「他可以動用 cloud預設的工具 但是唯一封鎖的是不能搜尋其他都可」→ Claude Code 預設工具全開、同一個 session 接著；只擋搜尋與連網
    cmd = ["claude", "-p", "--model", MODEL, "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
           "--setting-sources", "", "--strict-mcp-config", "--effort", EFFORT, "--append-system-prompt-file", sp,
           "--dangerously-skip-permissions", "--disallowedTools", *DENY, "--max-turns", "40"] + (["--resume", SESSION["id"]] if SESSION["id"] else [])
    env = dict(os.environ, CLAUDE_CODE_MAX_OUTPUT_TOKENS=str(MAX_OUT), **NO_NET_ENV)
    msg = {"type": "user", "message": {"role": "user", "content": content}}
    t0, rec, texts, raw, tools_used = time.time(), {}, [], "", []
    try:
        p = subprocess.run(cmd, input=json.dumps(msg, ensure_ascii=False) + "\n", capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=DEADLINE, cwd=CWD, env=env)
        for line in p.stdout.splitlines():
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if o.get("type") == "assistant":
                for b in (o.get("message") or {}).get("content") or []:
                    if b.get("type") == "text":
                        texts.append(b.get("text") or "")
                    elif b.get("type") in ("tool_use", "server_tool_use"):
                        tools_used.append(b.get("name"))
                        jdump(os.path.join(OUT, "工具使用.jsonl"), {"時間": now(), "工具": b.get("name"), "輸入": b.get("input")})
            elif o.get("type") == "result":
                raw = o.get("result") or ""
                if o.get("session_id"):
                    SESSION["id"] = o["session_id"]
                u = o.get("usage") or {}
                tot = o.get("total_cost_usd") or 0
                rec.update(費用=round(max(0.0, tot - SESSION.get("cost", 0.0)), 5), 結果=o.get("subtype"), 輸入=u.get("input_tokens"),
                           快取讀=u.get("cache_read_input_tokens"), 快取寫=u.get("cache_creation_input_tokens"), 輸出=u.get("output_tokens"))
        if not raw and texts:
            raw = "".join(texts)
        if not raw:
            rec["錯誤"] = (p.stderr or p.stdout)[-400:]
    except subprocess.TimeoutExpired:
        rec["錯誤"] = "頂時限"
    rec["秒"] = round(time.time() - t0, 1)
    rec["Claude工具"] = tools_used
    if "tot" in locals():
        SESSION["cost"] = max(SESSION.get("cost", 0.0), tot)
    return raw, rec


def ask_local(content):
    """11/21：RAW_MODEL=local → 地端 gamer-27b-ft-run3（8888），沒有 Claude Code 工具；同一份 system 與每輪內容，圖改成 OpenAI 格式。"""
    import play_agent
    sysx = re.sub(r"另外你可以用 Claude Code 預設的工具[^\n]*\n", "", SYSTEM).replace("前面的對話你都記得（同一個 session），也可以自己寫檔案記。", "前面的事只記得筆記和最近幾輪。")
    oc = []
    for c in content:
        if c.get("type") == "image":
            oc.append({"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + c["source"]["data"]}})
        else:
            oc.append(c)
    t0 = time.time()
    try:
        ret = play_agent.stream_json(LOCAL_EP, LOCAL_MODEL, sysx, oc, 8000, time.time() + DEADLINE, effort=LOCAL_EFFORT)
        raw = ret[0] or ""
        rec = {"結果": "success" if raw else "空", "費用": 0.0, "截斷": bool(ret[1])}
    except Exception as e:  # noqa: BLE001
        raw, rec = "", {"錯誤": str(e)[:300]}
    rec["秒"] = round(time.time() - t0, 1); rec["Claude工具"] = []
    return raw, rec


def parse(raw):
    t = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", raw or "").strip()
    m = re.search(r"\{.*\}", t, re.S)
    try:
        return json.loads(m.group(0)) if m else {}
    except ValueError:
        return {}


class Hands:
    """只有鍵盤滑鼠；擋法借工具版 Tools 的（前景檢查、TABOO 字），回傳只說按了沒有，不給量測或解讀。"""

    def __init__(self):
        cat = tool_agent.load_catalog(GAME)
        self.t = tool_agent.Tools(GAME, cat, live=True)
        self.taboo = tuple(tool_agent.Tools.TABOO) + LEAVE

    def grab(self):
        return self.t.grab()

    def bbox(self):
        return self.t.win.client_bbox() if self.t.win else None

    def near_bad(self, fx, fy):
        near = self.t._near_text(fx, fy)
        hit = [w for w in self.taboo if any(w in n.lower() for n in near)]
        return near, hit

    MEASURE = ("按", "點", "壓滑鼠", "拖", "移滑鼠")

    def do(self, act):
        """11/21：照工具版同一把尺量「有沒有反應」（按前 0.2 秒量背景、門檻 max(2, 背景×2+4)、沒變等 1.2 秒再量），只寫 操作量測.jsonl，不回給模型。"""
        name = (re.match(r"\s*([^\s(（]+)", act or "") or [None, ""])[1]
        if name not in self.MEASURE:
            return self._do(act)
        try:
            b0 = self.grab(); time.sleep(0.2); before = self.grab()
            noise = self.t.diff(b0, before)
        except Exception:  # noqa: BLE001
            return self._do(act)
        r = self._do(act)
        if not r.endswith("了"):
            return r
        try:
            th = max(2.0, noise * 2 + 4)
            d = self.t.diff(before, self.grab()); d2 = None
            if d < th:
                time.sleep(1.2); d2 = self.t.diff(before, self.grab())
            ok = d >= th or (d2 is not None and d2 >= th)
            k = act.split("(")[0].strip(); a0 = (re.findall(r"[(（]\s*([^,，)）]+)", act) or [""])[0].strip().lower()
            kind = ("按E" if k == "按" and a0 == "e" else "移動" if k == "按" and a0 in ("w", "a", "s", "d") else
                    "按其他鍵" if k == "按" else "點擊" if k in ("點", "壓滑鼠") else "視角")
            jdump(os.path.join(OUT, "操作量測.jsonl"), {"時間": now(), "動作": act, "類": kind, "背景": round(noise, 1),
                                                    "變化": round(d, 1), "再量": None if d2 is None else round(d2, 1), "有反應": ok})
        except Exception:  # noqa: BLE001
            pass
        return r

    def _do(self, act):
        import pydirectinput
        m = re.fullmatch(r"\s*([^\s(（]+)\s*[(（](.*)[)）]\s*", act or "")
        if not m:
            return "看不懂這個動作，沒做"
        name, args = m.group(1), [a.strip() for a in re.split(r"[,，]", m.group(2)) if a.strip()]
        f = lambda i, d: float(args[i]) if len(args) > i and re.fullmatch(r"-?\d+(\.\d+)?", args[i]) else d
        try:
            if name == "等":
                time.sleep(max(0.5, min(10, f(0, 1)))); return "等了"
            self.t._fg_ok()
            if name == "按":
                k = (args[0] if args else "").lower()
                if not k:
                    return "沒給鍵"
                if k in NO_KEYS:
                    return f"沒按 {k}：這個鍵會跳離遊戲視窗"
                if k in CONFIRM:
                    txt = self.t.see_text().lower()
                    hit = [w for w in self.taboo if w in txt and w not in ("$",)]
                    if any(w in LEAVE for w in hit) or any(w in ("buy", "purchase", "購買", "robux") for w in hit):
                        return f"沒按 {k}：畫面上有限制的字（{'、'.join(hit)}），這時按它可能選到那個按鈕"
                pydirectinput.keyDown(k); time.sleep(max(0.05, min(5, f(1, 0.1)))); pydirectinput.keyUp(k)
                return "按了"
            if name == "點":
                btn = "right" if args and args[0].startswith("右") else "left"
                fx, fy = f(1, -1), f(2, -1)
                if not (0 <= fx <= 1 and 0 <= fy <= 1):
                    return "座標要 0～1，沒點"
                near, hit = self.near_bad(fx, fy)
                if hit:
                    return f"沒點：那裡附近的字 {near} 碰到絕對限制（{'、'.join(hit)}）"
                b = self.bbox()
                pydirectinput.moveTo(int(b["left"] + fx * b["width"]), int(b["top"] + fy * b["height"]))
                time.sleep(0.05); pydirectinput.moveRel(3, 0, relative=True); time.sleep(0.04); pydirectinput.moveRel(-3, 0, relative=True)
                time.sleep(0.05); pydirectinput.mouseDown(button=btn); time.sleep(0.08); pydirectinput.mouseUp(button=btn)
                return "點了"
            if name == "壓滑鼠":
                btn = "right" if args and args[0].startswith("右") else "left"
                b = self.bbox(); cx, cy = pydirectinput.position()
                near, hit = self.near_bad((cx - b["left"]) / max(1, b["width"]), (cy - b["top"]) / max(1, b["height"]))
                if hit:
                    return f"沒壓：游標附近的字 {near} 碰到絕對限制"
                pydirectinput.mouseDown(button=btn)
                try:
                    time.sleep(max(0.05, min(5, f(1, 0.5))))
                finally:
                    pydirectinput.mouseUp(button=btn)
                return "壓了"
            if name == "拖":
                dx, dy = int(max(-1500, min(1500, f(1, 0)))), int(max(-1500, min(1500, f(2, 0))))
                pydirectinput.mouseDown(button="right")
                try:
                    n = max(1, int(max(abs(dx), abs(dy)) / 40))
                    for i in range(n):
                        pydirectinput.moveRel(int(dx * (i + 1) / n) - int(dx * i / n), int(dy * (i + 1) / n) - int(dy * i / n), relative=True)
                        time.sleep(0.01)
                finally:
                    pydirectinput.mouseUp(button="right")
                return "拖了"
            if name == "移滑鼠":
                pydirectinput.moveRel(int(max(-1500, min(1500, f(0, 0)))), int(max(-1500, min(1500, f(1, 0)))), relative=True)
                return "移了"
            if name == "滾輪":
                import ctypes
                n = int(max(-5, min(5, f(0, 0))))
                for _ in range(abs(n)):
                    ctypes.windll.user32.mouse_event(0x0800, 0, 0, 120 if n > 0 else -120, 0); time.sleep(0.05)
                return "滾了"
            return f"沒有「{name}」這個動作，沒做"
        except Exception as e:  # noqa: BLE001
            return f"沒做：{e}"

    def task_text(self, im):
        """右上角任務字（OCR，只記錄用，不給它看）。"""
        try:
            import local_text
            W, H = im.size
            crop = im.crop((int(W * 0.62), 0, W, int(H * 0.35)))
            res = local_text._win_read(crop) or []
            return " ".join(str(r[1]).strip() for r in res if str(r[1]).strip())[:200]
        except Exception as e:  # noqa: BLE001
            return f"OCR失敗:{e}"


def main():
    global EFFORT
    ap = argparse.ArgumentParser(); ap.add_argument("--until", required=True)
    # 11/21 使用者「完成後再實驗 換成最低的思考程度各自接續一個小時」→ --effort low --resume <session>：接同一個 session、筆記、輪數
    ap.add_argument("--effort", default=EFFORT); ap.add_argument("--resume", default="")
    a = ap.parse_args(); EFFORT = a.effort
    until = datetime.strptime(a.until, "%Y-%m-%d %H:%M")
    h = Hands()
    notes, hist, prev, rnd, cost, last_task = "", [], None, 0, 0.0, None
    if a.resume:
        SESSION["id"] = a.resume
        old = [json.loads(l) for l in open(os.path.join(OUT, "rounds.jsonl"), encoding="utf-8")]
        rr = [r for r in old if "輪" in r]
        rnd = rr[-1]["輪"]; SESSION["cost"] = rr[-1].get("累計費用", 0.0)   # CLI 回的是整個 session 累計 → 從舊累計往上減
        hist = [f"[{r['輪']}] 看到:{str(r.get('看到',''))[:150]}｜想:{str(r.get('想',''))[:200]}｜動作:{'；'.join(r.get('動作結果') or []) or '無'}" for r in rr[-10:]]
        np_ = os.path.join(OUT, "筆記.md")
        notes = open(np_, encoding="utf-8").read() if os.path.exists(np_) else ""
    jdump(os.path.join(OUT, "rounds.jsonl"), {"事件": "開始", "時間": now(), "跑到": a.until, "模型": MODEL, "思考": EFFORT, "接續": a.resume})
    while datetime.now() < until and not any(os.path.exists(s) for s in STOPS):
        rnd += 1
        # 11/21 晚 使用者「不能有隊友…幫她按 ESC 檢查團隊裡面是否只有他一人」→ 程式查，模型不知道
        if os.environ.get("SOLO_GUARD") == "1":
            import 隊友檢查
            g = 隊友檢查.guard(h.t, os.path.basename(OUT))
            if g:
                jdump(os.path.join(OUT, "rounds.jsonl"), {"事件": "隊友檢查", "時間": now(), "輪": rnd, "結果": g})
        im = h.grab()
        if im is None:
            time.sleep(5); continue
        ts = datetime.now().strftime("%H%M%S")
        im.convert("RGB").save(os.path.join(SHOTS, f"{rnd:04d}_{ts}.jpg"), quality=75)
        task = h.task_text(im)
        if task != last_task:
            jdump(os.path.join(OUT, "任務字.jsonl"), {"輪": rnd, "時間": now(), "任務字": task, "之前": last_task})
            last_task = task
        left = (until - datetime.now()).total_seconds() / 60
        content = [{"type": "text", "text": f"第 {rnd} 輪，現在 {now()}，剩約 {left:.0f} 分鐘。\n【你的筆記】\n{notes or '（還沒寫）'}\n"
                    f"【最近幾輪你寫的】\n" + ("\n".join(hist[-10:]) or "（這是第一輪）")}]
        if prev is not None:
            content += [{"type": "text", "text": "上一輪做動作之前的畫面："},
                        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64(prev, 960, 70)}}]
        content += [{"type": "text", "text": "現在的畫面："},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64(im)}}]
        raw, rec = (ask_local if RAW_MODEL == "local" else ask)(content)
        cost += rec.get("費用") or 0
        o = parse(raw)
        acts = [str(x) for x in (o.get("動作") or [])][:5]
        res = []
        for x in acts:
            if any(os.path.exists(s) for s in STOPS):
                break
            res.append(f"{x}→{h.do(x)}")
            time.sleep(0.15)
        if isinstance(o.get("筆記"), str) and o["筆記"].strip():
            notes = o["筆記"].strip()[:3000]
            open(os.path.join(OUT, "筆記.md"), "w", encoding="utf-8").write(notes)
        if o.get("完成"):
            jdump(os.path.join(OUT, "自評完成.jsonl"), {"輪": rnd, "時間": now(), "完成": o["完成"], "任務字": task})
        hist.append(f"[{rnd} {now()}] 看到:{str(o.get('看到',''))[:150]}｜想:{str(o.get('想',''))[:200]}｜動作:{'；'.join(res) or '無'}")
        rec.update(輪=rnd, 時間=ts, 任務字=task, 看到=o.get("看到"), 想=o.get("想"), 動作結果=res, 完成=o.get("完成"),
                   解析失敗=(not o), 累計費用=round(cost, 3))
        if not o:
            rec["原文"] = (raw or "")[:1500]
        jdump(os.path.join(OUT, "rounds.jsonl"), rec)
        print(f"[{rnd}] {rec['秒']}s ${cost:.2f} {task[:30]}｜{'；'.join(res)[:160]}", flush=True)
        prev = im
    jdump(os.path.join(OUT, "rounds.jsonl"), {"事件": "結束", "時間": now(), "輪數": rnd, "累計費用": round(cost, 3)})


if __name__ == "__main__":
    main()
