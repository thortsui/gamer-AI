#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
talk.py — 直接跟正在玩遊戲的代理對話

它會帶著自己的完整脈絡回答你：現在看到的畫面、成績、工作流程與目前步驟、
核心知識、最近做過什麼、死亡檢討、計畫。你也可以直接下指令，它會寫進
資料/<遊戲>/任務指示.txt，下一輪它就照做（代理不用重啟）。

用法：
    python talk.py                       # 進入對話
    python talk.py "你現在在做什麼？"      # 問一句就結束

對話中的指令：
    /指示 <內容>   把這句話加進任務指示（代理下一輪就會讀到）
    /清除指示      清掉你剛才加的臨時指示
    /看           重新抓一張現在的畫面給它看
    /狀態         印出成績、目前步驟、最近幾輪
    /離開
"""
import argparse
import base64
import io
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime

import mss
from PIL import Image

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
MARK = "＝＝＝ 使用者臨時交代（最新） ＝＝＝"



def game_dir(root, game):
    """2026-09-14 專案結構：專案/<遊戲>/記憶；找不到就退回舊的 資料/<遊戲>。"""
    import os
    new = os.path.join(root, "專案", game, "記憶")
    return new if os.path.isdir(new) else os.path.join(root, "資料", game)

def cfg():
    try:
        return json.load(open(os.path.join(HERE, "設定.json"), encoding="utf-8"))
    except OSError:
        return {"端點": "http://192.168.0.126:8888/v1"}


def roblox_region():
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
        return {"left": sx, "top": sy, "width": r - l, "height": b - t} if r - l > 200 else None
    except Exception:  # noqa: BLE001
        return None


def shot_uri(width=760, quality=60):
    with (getattr(mss, "MSS", None) or mss.mss)() as sct:
        s = sct.grab(roblox_region() or sct.monitors[1])
        im = Image.frombytes("RGB", s.size, s.bgra, "raw", "BGRX")
    im = im.resize((width, int(width * im.height / im.width)), Image.BILINEAR)
    buf = io.BytesIO(); im.save(buf, "JPEG", quality=quality)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def read(p, n=4000):
    try:
        return open(p, encoding="utf-8", errors="replace").read()[-n:]
    except OSError:
        return ""


def context(d):
    rows = []
    try:
        for l in open(os.path.join(d, "行動日誌.jsonl"), encoding="utf-8").readlines()[-40:]:
            try:
                rows.append(json.loads(l))
            except json.JSONDecodeError:
                pass
    except OSError:
        pass
    recent = []
    for r in rows[-12:]:
        acts = "、".join((a.get("g") or a.get("k") or a.get("t") or "") for a in (r.get("動作") or [])) or "—"
        recent.append(f"[{r.get('時間')}] {r.get('模式')} 看到「{r.get('看到','')}」目標「{r.get('目標','')}」做「{acts}」")
    score = read(os.path.join(d, "成績.json"), 400)
    step = read(os.path.join(d, "進度.json"), 200)
    return (f"【你的成績】{score}\n【目前步驟】{step}\n"
            f"【你的工作流程】\n{read(os.path.join(d, '工作流程.md'), 1500)}\n"
            f"【你的核心知識】\n{read(os.path.join(d, '核心知識.md'), 2500)}\n"
            f"【使用者給你的任務指示】\n{read(os.path.join(d, '任務指示.txt'), 1500)}\n"
            f"【你最近做的事】\n" + "\n".join(recent) + "\n"
            f"【你的死亡檢討】\n{read(os.path.join(d, '死亡檢討.md'), 1200)}\n"
            f"【你自己擬的計畫】\n{read(os.path.join(d, '計畫.md'), 800)}")


SYS = ("你是 gamer AI，一個正在用這台電腦玩 Roblox「{game}」的玩家。現在使用者直接跟你說話。"
       "你會看到自己現在的遊戲畫面與完整脈絡。請用第一人稱、繁體中文（台灣用語）誠實回答，"
       "不知道就說不知道，不要編造。回答簡短口語，除非使用者要求細節。"
       "使用者若下指令，回答你會怎麼照做（程式會把指令寫進你的任務指示，你下一輪就會讀到）。")


def ask(endpoint, model, game, d, question, history, with_shot=True):
    parts = []
    if with_shot:
        try:
            parts.append({"type": "image_url", "image_url": {"url": shot_uri()}})
        except Exception:  # noqa: BLE001
            pass
    parts.append({"type": "text", "text": context(d) + "\n\n【使用者說】" + question})
    msgs = [{"role": "system", "content": SYS.format(game=game)}]
    msgs += history[-8:]
    msgs.append({"role": "user", "content": parts})
    payload = {"model": model, "messages": msgs, "max_tokens": 700, "temperature": 0.4,
               "chat_template_kwargs": {"enable_thinking": False}}
    req = urllib.request.Request(endpoint.rstrip("/") + "/chat/completions",
                                 data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=300) as r:
        out = json.loads(r.read().decode("utf-8"))
    return out["choices"][0]["message"]["content"].strip(), time.time() - t0


def set_instr(d, text):
    p = os.path.join(d, "任務指示.txt")
    cur = read(p, 100000)
    cur = cur.split(MARK)[0].rstrip()
    with open(p, "w", encoding="utf-8") as f:
        f.write(cur + "\n\n" + MARK + "\n" + text.strip() + "\n")


def clear_instr(d):
    p = os.path.join(d, "任務指示.txt")
    cur = read(p, 100000).split(MARK)[0].rstrip()
    open(p, "w", encoding="utf-8").write(cur + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("words", nargs="*")
    ap.add_argument("--game", default="動物醫院")
    a = ap.parse_args()
    c = cfg()
    endpoint = c["端點"]
    with urllib.request.urlopen(endpoint.rstrip("/") + "/models", timeout=10) as r:
        model = json.loads(r.read().decode("utf-8"))["data"][0]["id"]
    d = game_dir(HERE, a.game)
    log = os.path.join(d, "對話紀錄.md")
    history = []

    def say(q, shot=True):
        ans, dt = ask(endpoint, model, a.game, d, q, history, shot)
        history.append({"role": "user", "content": q})
        history.append({"role": "assistant", "content": ans})
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"\n**[{datetime.now().strftime('%m/%d %H:%M')}] 使用者**：{q}\n\n**它**：{ans}\n")
        print(f"\n它> {ans}\n[{dt:.1f}s]")

    if a.words:
        say(" ".join(a.words)); return
    print(f"[跟 gamer AI 對話] 遊戲：{a.game}　模型：{model}")
    print("指令：/指示 <內容>　/清除指示　/看　/狀態　/離開\n")
    while True:
        try:
            q = input("你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print(); break
        if not q:
            continue
        if q in ("/離開", "/quit", "/exit"):
            break
        if q == "/狀態":
            print(context(d)[:2500]); continue
        if q == "/看":
            say("這是我現在的畫面，你看到什麼？現在該做什麼？"); continue
        if q.startswith("/指示 "):
            body = q[4:].strip()
            set_instr(d, body)
            print("[已寫進任務指示，它下一輪就會讀到]")
            say(f"使用者剛剛交代：「{body}」。你會怎麼做？", shot=False); continue
        if q == "/清除指示":
            clear_instr(d); print("[已清除臨時指示]"); continue
        say(q)


if __name__ == "__main__":
    main()
