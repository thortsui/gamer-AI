#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dashboard_server.py — 即時儀表板：直接看代理的螢幕與思考流程

只用標準函式庫 + mss + Pillow。啟動後開 http://localhost:8300
  /            儀表板網頁（每 1 秒更新畫面、每 3 秒更新思考流程）
  /shot.jpg    現在的螢幕（960 寬 JPEG）
  /api/state   代理狀態 JSON（最近幾輪、計畫、死亡檢討、玩法摘要、統計）

用法：python 網站/dashboard_server.py [--port 8300] [--game 動物醫院]
"""
import argparse
import io
import json
import os
import subprocess
import sys
import threading
import time
from collections import Counter
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import urllib.request

import mss
from PIL import Image

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GAME = "動物醫院"
NL_CONST = chr(10)
PAGE_PATH = os.path.join(HERE, "dashboard.html")
THINK_PATH = os.path.join(HERE, "thinking.html")
CHAT_PATH = os.path.join(HERE, "chat.html")

_shot_lock = threading.Lock()
_shot_cache = {"t": 0.0, "data": b""}



def game_dir(root, game):
    """2026-09-14 專案結構：專案/<遊戲>/記憶；找不到就退回舊的 資料/<遊戲>。"""
    import os
    new = os.path.join(root, "專案", game, "記憶")
    return new if os.path.isdir(new) else os.path.join(root, "資料", game)

def roblox_region():
    """Roblox 視窗客戶區（不含標題列）；找不到回 None → 抓全螢幕。"""
    try:
        import win32gui
        found = []

        def cb(h, _):
            if win32gui.IsWindowVisible(h) and not win32gui.IsIconic(h):
                t = win32gui.GetWindowText(h)
                if (t == "Roblox" or t.startswith("Roblox")) and "Studio" not in t:
                    found.append(h)
        win32gui.EnumWindows(cb, None)
        if not found:
            return None
        l, t, r, b = win32gui.GetClientRect(found[0])
        sx, sy = win32gui.ClientToScreen(found[0], (l, t))
        if r - l < 200 or b - t < 150:
            return None
        return {"left": sx, "top": sy, "width": r - l, "height": b - t}
    except Exception:  # noqa: BLE001
        return None


def grab_jpeg(width=960, quality=60):
    now = time.time()
    with _shot_lock:
        if now - _shot_cache["t"] < 0.4 and _shot_cache["data"]:
            return _shot_cache["data"]
        with (getattr(mss, "MSS", None) or mss.mss)() as sct:
            shot = sct.grab(roblox_region() or sct.monitors[1])
            im = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        im = im.resize((width, int(width * im.height / im.width)), Image.BILINEAR)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=quality)
        _shot_cache.update(t=now, data=buf.getvalue())
        return _shot_cache["data"]


def read_tail(path, n):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.readlines()[-n:]
    except OSError:
        return []


def read_text(path, max_chars=6000):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()[-max_chars:]
    except OSError:
        return ""


_pid_cache = {"t": 0.0, "pids": []}


def agent_pids():
    now = time.time()
    if now - _pid_cache["t"] < 10:
        return _pid_cache["pids"]
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object {$_.CommandLine -like '*play_agent.py*'} | Select-Object -ExpandProperty ProcessId"],
                             capture_output=True, text=True, timeout=15).stdout.split()
        pids = [p for p in out if p.isdigit()]
    except Exception:  # noqa: BLE001
        pids = []
    _pid_cache.update(t=now, pids=pids)
    return pids


def state():
    d = game_dir(ROOT, GAME)
    rows = []
    for line in read_tail(os.path.join(d, "行動日誌.jsonl"), 160):
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    notes = [l.rstrip() for l in read_tail(os.path.join(d, "研究筆記.md"), 12) if l.startswith("- ")]
    summary = [l[2:].strip() for l in read_text(os.path.join(d, "玩法摘要.md")).splitlines() if l.startswith("- ")]
    plan = read_text(os.path.join(d, "計畫.md"), 2500)
    death = read_text(os.path.join(d, "死亡檢討.md"), 2500)
    instr = read_text(os.path.join(d, "任務指示.txt"), 2000)
    try:
        with open(os.path.join(d, "成績.json"), encoding="utf-8") as f:
            score = json.load(f)
    except (OSError, json.JSONDecodeError):
        score = {}
    # 從最近的「看到」抓 $ 與輪班
    money, shift = None, None
    import re
    for r in reversed(rows):
        s = r.get("看到") or ""
        if money is None:
            m = re.search(r"\$\s?(\d{1,6})", s)
            if m:
                money = int(m.group(1))
        if shift is None:
            m = re.search(r"輪班\s?(\d+)", s)
            if m:
                shift = int(m.group(1))
        if money is not None and shift is not None:
            break
    modes = Counter(r.get("模式") for r in rows)
    log_path = os.path.join(d, "行動日誌.jsonl")
    try:
        idle = time.time() - os.path.getmtime(log_path)
    except OSError:
        idle = None
    events = [r for r in rows if r.get("模式") in ("思考", "死亡檢討", "脫困走位", "強制離開重進", "youtube開始", "youtube結束")]
    return {
        "時間": datetime.now().strftime("%H:%M:%S"),
        "程序": agent_pids(),
        "閒置秒": round(idle, 1) if idle is not None else None,
        "最近": rows[-80:],
        "事件": events[-10:],
        "模式統計": dict(modes),
        "付費視窗": sum(1 for r in rows if r.get("robux") == 1),
        "擋掉": sum(len(r.get("擋掉") or []) for r in rows),
        "金幣": money, "輪班": shift,
        "筆記": notes, "玩法摘要": summary, "計畫": plan, "死亡檢討": death, "任務指示": instr,
        "成績": score,
    }


def _talk():
    import importlib.util
    spec = importlib.util.spec_from_file_location("talk", os.path.join(ROOT, "talk.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def chat_with_agent(question, history):
    talk = _talk()
    endpoint = talk.cfg()["端點"]
    with urllib.request.urlopen(endpoint.rstrip("/") + "/models", timeout=10) as r:
        model = json.loads(r.read().decode("utf-8"))["data"][0]["id"]
    d = game_dir(ROOT, GAME)
    ans, dt = talk.ask(endpoint, model, GAME, d, question, history or [])
    nl = chr(10)
    line = "**[" + datetime.now().strftime("%m/%d %H:%M") + "] 使用者（手機）**：" + question + nl + nl + "**它**：" + ans + nl
    with open(os.path.join(d, "對話紀錄.md"), "a", encoding="utf-8") as f:
        f.write(nl + line)
    return {"答": ans, "秒": round(dt, 1)}


def set_instruction(text):
    talk = _talk()
    d = game_dir(ROOT, GAME)
    if text.strip():
        talk.set_instr(d, text)
    else:
        talk.clear_instr(d)
    return {"ok": True}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 安靜
        pass

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        p = urlparse(self.path).path
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
            if p == "/api/chat":
                out = chat_with_agent(str(body.get("q", ""))[:800], body.get("history") or [])
            elif p == "/api/instruct":
                out = set_instruction(str(body.get("text", ""))[:2000])
            else:
                self._send(404, "text/plain", b"not found"); return
            self._send(200, "application/json; charset=utf-8", json.dumps(out, ensure_ascii=False).encode("utf-8"))
        except Exception as e:  # noqa: BLE001
            self._send(500, "application/json; charset=utf-8",
                       json.dumps({"錯誤": str(e)}, ensure_ascii=False).encode("utf-8"))

    def do_GET(self):
        p = urlparse(self.path).path
        try:
            if p == "/":
                with open(PAGE_PATH, "rb") as f:
                    self._send(200, "text/html; charset=utf-8", f.read())
            elif p in ("/chat", "/talk", "/chat/"):
                with open(CHAT_PATH, "rb") as f:
                    self._send(200, "text/html; charset=utf-8", f.read())
            elif p in ("/think", "/thinking", "/think/"):
                with open(THINK_PATH, "rb") as f:
                    self._send(200, "text/html; charset=utf-8", f.read())
            elif p == "/shot.jpg":
                self._send(200, "image/jpeg", grab_jpeg())
            elif p in ("/prompt", "/prompt.txt"):
                # 他這一輪真正收到的整份提示（system 模組 + user 面板）
                fp = os.path.join(game_dir(ROOT, GAME), "最新提示.txt")
                try:
                    with open(fp, encoding="utf-8") as f:
                        body = f.read()
                except OSError:
                    body = "（還沒有提示檔，代理可能剛啟動）"
                self._send(200, "text/plain; charset=utf-8", body.encode("utf-8"))
            elif p in ("/flow", "/flow/", "/思考"):
                with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "flow.html"), "rb") as f:
                    self._send(200, "text/html; charset=utf-8", f.read())
            elif p == "/api/flow":
                # 他自己的思考流程：看到什麼 → 想做什麼 → 做了什麼
                fp = os.path.join(game_dir(ROOT, GAME), "思考流程.jsonl")
                rows = []
                try:
                    with open(fp, encoding="utf-8") as f:
                        for line in f.readlines()[-400:]:
                            line = line.strip()
                            if line:
                                try:
                                    rows.append(json.loads(line))
                                except Exception:  # noqa: BLE001
                                    pass
                except OSError:
                    pass
                self._send(200, "application/json; charset=utf-8",
                           json.dumps(rows[::-1], ensure_ascii=False).encode("utf-8"))
            elif p == "/api/state":
                self._send(200, "application/json; charset=utf-8", json.dumps(state(), ensure_ascii=False).encode("utf-8"))
            else:
                self._send(404, "text/plain", b"not found")
        except Exception as e:  # noqa: BLE001
            self._send(500, "text/plain; charset=utf-8", str(e).encode("utf-8"))


def main():
    global GAME
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8300)
    ap.add_argument("--game", default=GAME)
    a = ap.parse_args()
    GAME = a.game
    srv = ThreadingHTTPServer(("0.0.0.0", a.port), H)
    print(f"[dashboard] http://localhost:{a.port}  （遊戲：{GAME}）")
    srv.serve_forever()


if __name__ == "__main__":
    main()
