#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
screen_watch.py — 讓 DGX 上的千問「即時看你的螢幕」

做法：
  1. 背景執行緒用 mss 以 5 fps 連續擷取螢幕（或指定視窗），縮到 640 寬，放進環形緩衝。
  2. 主迴圈每一輪拿「最近 1 秒的 5 張連續畫面」，包成一段短影片（mp4，預設）或 5 張圖，
     連同簡短指令送給模型（思考模式關、max_tokens 小、串流讀取）。
  3. 從「開始擷取」到「拿到答案」有一條硬截止線（預設 5 秒）：時間到就切斷連線、
     用已收到的部分文字，絕不等超過。
  4. 答案印在終端機，也寫到 資料/最新解讀.json（給其他程式讀）與 資料/螢幕解讀.jsonl（歷史）。

用法：
  python screen_watch.py                    # 全螢幕，持續看
  python screen_watch.py --window Roblox    # 只看標題含 Roblox 的視窗
  python screen_watch.py --once             # 只解讀一次就結束
  python screen_watch.py --bench            # 實測 影片 vs 多圖 兩種送法的延遲
  python screen_watch.py --mode images      # 改用多圖送法
  python screen_watch.py --ask "我現在該按哪裡？"   # 自訂問題（預設是「現在在哪／下一步」）

只用標準函式庫 + mss + Pillow + OpenCV（筆電都已裝）。
"""
import argparse
import base64
import io
import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from datetime import datetime

import mss
from PIL import Image

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "資料")
CONFIG_PATH = os.path.join(HERE, "設定.json")

DEFAULTS = {
    "端點": "http://192.168.0.126:8888/v1",
    "模型名": "",
    "知識庫": "知識庫/Roblox玩家入門說明書.md",
    "螢幕fps": 5,            # 擷取頻率
    "每輪張數": 5,           # 每次送給模型幾張（5 張 @5fps = 最近 1 秒）
    "畫面寬": 640,           # 縮圖寬度（越小越快，太小字看不清）
    "jpeg品質": 60,
    "送法": "video",         # video = 包成短 mp4；images = 多張 image_url
    "截止秒": 5.0,           # 硬截止：從開始擷取到拿到答案
    "看畫面max_tokens": 80,  # 回答上限；越小越快
    "看畫面溫度": 0.2,
    "帶入說明書": False,     # True = system prompt 夾整份說明書。實測多模態請求吃不到 prefix cache，
                             # 每輪會多 1.3～2.2 秒首字時間；預設用上面的濃縮重點就好。
}

SYSTEM_SHORT = (
    "你是 gamer AI，正在即時看一位小朋友玩家的電腦螢幕。你會收到最近 1 秒的連續畫面（時間順序）。"
    "回答一律繁體中文、台灣用語、不超過 60 字、不要 markdown、不要 emoji。"
    "格式固定兩行：\n畫面：<現在在哪個畫面／正在發生什麼>\n下一步：<玩家現在該做的一件事>\n"
    "如果不是 Roblox，第一行說明看到什麼，第二行寫「回到 Roblox」。看不清楚就老實說看不清楚。\n"
    "\nRoblox 重點（判斷畫面用）：\n"
    "- 網站/App 主畫面：左側選單 首頁/探索/虛擬人偶/好友；遊戲頁面有綠色大按鈕 Play、遊玩人數、說明、伺服器分頁。\n"
    "- 按 Play 後：黑底進度條＝載入中（10～60 秒）；有些遊戲先進大廳選關卡/隊伍，有些直接落地。\n"
    "- 遊戲內：左上聊天框、下方數字格＝工具列（1～0 切換）、Esc 開選單（玩家/設定/重置角色/離開）。\n"
    "- 操作：WASD 走、空白鍵跳、按住右鍵轉視角、滾輪拉近拉遠、E 互動、Shift 鎖定視角、/ 打字聊天。\n"
    "- 常見錯誤碼：277/279/17 網路斷線→重試或換伺服器；267 被踢→換遊戲；524 只限好友→從遊戲頁重進；"
    "「Roblox 已在執行中」→工作管理員關掉 RobloxPlayerBeta 再按 Play。\n"
    "- 畫面中央浮字＝教學提示，照做；發光/箭頭/圓圈＝指引方向；靠近物件出現「E」提示＝可互動。\n"
    "- 免費 Robux、外掛、腳本一律是詐騙/會封號，要提醒別點。"
)
DEFAULT_ASK = "這是玩家螢幕最近 1 秒的連續畫面。現在在哪個畫面、發生什麼事？玩家下一步該做什麼？"


# ---------------------------------------------------------------- 設定
def load_config():
    cfg = dict(DEFAULTS)
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg.update(json.load(f))
    return cfg


def detect_model(endpoint, timeout=5):
    req = urllib.request.Request(endpoint.rstrip("/") + "/models")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8"))
    return data["data"][0]["id"]


def build_system(cfg):
    if not cfg.get("帶入說明書"):
        return SYSTEM_SHORT
    path = os.path.join(HERE, cfg["知識庫"])
    try:
        with open(path, encoding="utf-8") as f:
            manual = f.read()
    except OSError:
        return SYSTEM_SHORT
    return SYSTEM_SHORT + "\n\n以下是你的 Roblox 知識，判斷畫面時參考：\n===== 說明書開始 =====\n" + manual + "\n===== 說明書結束 ====="


# ---------------------------------------------------------------- 擷取
class ScreenGrabber(threading.Thread):
    """背景以固定 fps 擷取，保留最近 N 張 (timestamp, PIL.Image)。"""

    def __init__(self, fps, width, window_title=None, keep=15, bbox_fn=None):
        super().__init__(daemon=True)
        self.bbox_fn = bbox_fn          # 可傳一個函式回傳 {"left","top","width","height"}（例如 Roblox 視窗的客戶區）
        self.fps = fps
        self.width = width
        self.window_title = window_title
        self.frames = deque(maxlen=keep)
        # 2026-09-16：局內回放緩衝。主緩衝只留 3 秒（15 幀），死了之後根本沒東西可回看；
        # 這條每秒存一張 320 寬的小圖，留 120 秒，給「事後分析剛才怎麼死的」用。
        self.slow = deque(maxlen=120)
        self._slow_at = 0.0
        self.lock = threading.Lock()
        self.stop_flag = threading.Event()
        self.bbox = None
        self.actual_fps = 0.0

    def _find_window_bbox(self):
        if not self.window_title:
            return None
        try:
            import pygetwindow as gw
            wins = [w for w in gw.getWindowsWithTitle(self.window_title) if w.width > 100 and w.height > 100]
            if not wins:
                return None
            w = wins[0]
            return {"left": max(w.left, 0), "top": max(w.top, 0), "width": w.width, "height": w.height}
        except Exception:  # noqa: BLE001
            return None

    def run(self):
        interval = 1.0 / self.fps
        with (getattr(mss, "MSS", None) or mss.mss)() as sct:
            mon = sct.monitors[1]
            last_bbox_check = 0
            count, t_win = 0, time.time()
            next_t = time.time()
            while not self.stop_flag.is_set():
                now = time.time()
                if (self.window_title or self.bbox_fn) and now - last_bbox_check > 1:
                    try:
                        self.bbox = self.bbox_fn() if self.bbox_fn else self._find_window_bbox()
                    except Exception:  # noqa: BLE001
                        self.bbox = None
                    last_bbox_check = now
                region = self.bbox or mon
                try:
                    shot = sct.grab(region)
                    img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                    if img.width > self.width:
                        h = int(img.height * self.width / img.width)
                        img = img.resize((self.width, h), Image.BILINEAR)
                    with self.lock:
                        self.frames.append((now, img))
                        if now - self._slow_at >= 1.0:
                            self._slow_at = now
                            small = img
                            if img.width > 320:
                                small = img.resize((320, int(img.height * 320 / img.width)), Image.BILINEAR)
                            self.slow.append((now, small))
                except Exception as e:  # noqa: BLE001
                    # 電腦鎖定時 BitBlt 會一直失敗：30 秒印一次就好，並且退一秒
                    if time.time() - getattr(self, "_err_at", 0) > 30:
                        self._err_at = time.time()
                        print(f"[擷取錯誤] {e}（之後 30 秒內不再重複印）")
                    time.sleep(1.0)
                count += 1
                if now - t_win >= 2:
                    self.actual_fps = count / (now - t_win)
                    count, t_win = 0, now
                next_t += interval
                sleep = next_t - time.time()
                if sleep > 0:
                    time.sleep(sleep)
                else:
                    next_t = time.time()

    def latest(self, n):
        with self.lock:
            return list(self.frames)[-n:]

    def latest_slow(self, secs=40.0):
        """最近 secs 秒的回放（每秒一張小圖）。事後分析用。"""
        now = time.time()
        with self.lock:
            return [(t, im) for (t, im) in self.slow if now - t <= secs]


# ---------------------------------------------------------------- 編碼
def jpeg_data_uri(img, quality):
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii"), buf.tell()


def mp4_data_uri(frames, fps):
    """把 PIL 影格寫成 mp4（OpenCV mp4v），回傳 data URI 與位元組數。"""
    import cv2
    import numpy as np
    w, h = frames[0].size
    w -= w % 2
    h -= h % 2
    fd, path = tempfile.mkstemp(suffix=".mp4")
    os.close(fd)
    try:
        vw = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
        for im in frames:
            arr = np.asarray(im.convert("RGB"))[:h, :w, ::-1]
            vw.write(np.ascontiguousarray(arr))
        vw.release()
        with open(path, "rb") as f:
            data = f.read()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    return "data:video/mp4;base64," + base64.b64encode(data).decode("ascii"), len(data)


def build_content(frames, cfg, ask, mode):
    imgs = [im for _, im in frames]
    if mode == "video" and len(imgs) >= 2:
        uri, nbytes = mp4_data_uri(imgs, cfg["螢幕fps"])
        return [{"type": "video_url", "video_url": {"url": uri}}, {"type": "text", "text": ask}], nbytes
    parts, total = [], 0
    for im in imgs:
        uri, n = jpeg_data_uri(im, cfg["jpeg品質"])
        parts.append({"type": "image_url", "image_url": {"url": uri}})
        total += n
    parts.append({"type": "text", "text": ask})
    return parts, total


# ---------------------------------------------------------------- 送出（串流 + 硬截止）
def stream_chat(endpoint, model, system_text, content, cfg, deadline_at):
    """串流讀取；到 deadline_at 就停。回傳 (文字, 是否截斷, ttft, usage或None)。"""
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system_text},
                     {"role": "user", "content": content}],
        "temperature": cfg["看畫面溫度"],
        "max_tokens": cfg["看畫面max_tokens"],
        "stream": True,
        "stream_options": {"include_usage": True},
        "chat_template_kwargs": {"enable_thinking": False},
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(endpoint.rstrip("/") + "/chat/completions", data=data,
                                 headers={"Content-Type": "application/json"})
    remaining = deadline_at - time.time()
    if remaining <= 0.2:
        return "", True, None, None
    text, ttft, usage, truncated = [], None, None, False
    t0 = time.time()
    resp = urllib.request.urlopen(req, timeout=remaining)
    try:
        while True:
            remaining = deadline_at - time.time()
            if remaining <= 0:
                truncated = True
                break
            # socket 逾時控制：每次讀一行前重設
            resp.fp.raw._sock.settimeout(max(remaining, 0.05)) if hasattr(resp.fp, "raw") else None
            try:
                line = resp.readline()
            except (TimeoutError, OSError):
                truncated = True
                break
            if not line:
                break
            line = line.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            body = line[5:].strip()
            if body == "[DONE]":
                break
            try:
                obj = json.loads(body)
            except json.JSONDecodeError:
                continue
            if obj.get("usage"):
                usage = obj["usage"]
            for ch in obj.get("choices", []):
                delta = ch.get("delta", {})
                piece = delta.get("content")
                if piece:
                    if ttft is None:
                        ttft = time.time() - t0
                    text.append(piece)
    finally:
        try:
            resp.close()
        except Exception:  # noqa: BLE001
            pass
    return "".join(text).strip(), truncated, ttft, usage


# ---------------------------------------------------------------- 主流程
def one_cycle(grabber, endpoint, model, system_text, cfg, ask, mode, deadline_s):
    t_start = time.time()
    deadline_at = t_start + deadline_s
    frames = grabber.latest(cfg["每輪張數"])
    if not frames:
        return None
    content, nbytes = build_content(frames, cfg, ask, mode)
    t_enc = time.time() - t_start
    try:
        text, truncated, ttft, usage = stream_chat(endpoint, model, system_text, content, cfg, deadline_at)
        err = None
    except urllib.error.HTTPError as e:
        text, truncated, ttft, usage = "", False, None, None
        err = f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}"
    except Exception as e:  # noqa: BLE001
        text, truncated, ttft, usage = "", False, None, None
        err = f"{type(e).__name__}: {e}"
    total = time.time() - t_start
    return {
        "時間": datetime.now().strftime("%H:%M:%S"),
        "送法": mode,
        "張數": len(frames),
        "畫面跨秒": round(frames[-1][0] - frames[0][0], 2),
        "編碼秒": round(t_enc, 2),
        "首字秒": round(ttft, 2) if ttft else None,
        "總秒": round(total, 2),
        "截斷": truncated,
        "payload_KB": nbytes // 1024,
        "prompt_tokens": (usage or {}).get("prompt_tokens"),
        "completion_tokens": (usage or {}).get("completion_tokens"),
        "解讀": text,
        "錯誤": err,
    }


def save_result(res):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(os.path.join(DATA_DIR, "最新解讀.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    with open(os.path.join(DATA_DIR, "螢幕解讀.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(res, ensure_ascii=False) + "\n")


def print_result(res):
    flag = " ⚠截斷" if res["截斷"] else ""
    ttft = f"首字 {res['首字秒']}s, " if res["首字秒"] else ""
    print(f"\n[{res['時間']}] ({ttft}總 {res['總秒']}s, {res['張數']}張/{res['畫面跨秒']}s, "
          f"{res['payload_KB']}KB, prompt {res['prompt_tokens']} tok){flag}")
    if res["錯誤"]:
        print("  錯誤:", res["錯誤"])
    else:
        for line in (res["解讀"] or "（沒有回覆）").splitlines():
            print("  " + line)


def main():
    ap = argparse.ArgumentParser(description="讓 DGX 千問即時看螢幕")
    ap.add_argument("--window", help="只擷取標題含此字串的視窗（例：Roblox）")
    ap.add_argument("--once", action="store_true", help="只解讀一次")
    ap.add_argument("--bench", action="store_true", help="實測 video / images 兩種送法各 3 次")
    ap.add_argument("--mode", choices=["video", "images"], help="送法（預設看設定.json）")
    ap.add_argument("--ask", help="自訂要問模型的話")
    ap.add_argument("--deadline", type=float, help="硬截止秒數（預設看設定.json，5 秒）")
    ap.add_argument("--interval", type=float, default=0.0, help="兩輪之間額外等待秒數（預設 0，連續）")
    ap.add_argument("--no-manual", action="store_true", help="system prompt 不夾整份說明書（更快）")
    ap.add_argument("--width", type=int, help="縮圖寬度（預設看設定.json）")
    ap.add_argument("--frames", type=int, help="每輪送幾張（預設看設定.json）")
    args = ap.parse_args()

    cfg = load_config()
    if args.no_manual:
        cfg["帶入說明書"] = False
    if args.width:
        cfg["畫面寬"] = args.width
    if args.frames:
        cfg["每輪張數"] = args.frames
    mode = args.mode or cfg["送法"]
    deadline_s = args.deadline or float(cfg["截止秒"])
    ask = args.ask or DEFAULT_ASK
    endpoint = cfg["端點"]
    model = cfg.get("模型名") or detect_model(endpoint)
    system_text = build_system(cfg)

    grabber = ScreenGrabber(cfg["螢幕fps"], cfg["畫面寬"], args.window)
    grabber.start()
    print(f"[screen_watch] 模型 {model} @ {endpoint}；擷取 {cfg['螢幕fps']} fps × {cfg['每輪張數']} 張，"
          f"寬 {cfg['畫面寬']}，送法 {mode}，截止 {deadline_s}s，"
          f"視窗={'全螢幕' if not args.window else args.window}")
    time.sleep(cfg["每輪張數"] / cfg["螢幕fps"] + 0.2)  # 等緩衝先裝滿一輪

    if args.bench:
        for m in ("video", "images"):
            print(f"\n===== 送法 {m} =====")
            for _ in range(3):
                res = one_cycle(grabber, endpoint, model, system_text, cfg, ask, m, deadline_s)
                print_result(res)
        return

    try:
        while True:
            res = one_cycle(grabber, endpoint, model, system_text, cfg, ask, mode, deadline_s)
            if res is None:
                time.sleep(0.2)
                continue
            print_result(res)
            save_result(res)
            if args.once:
                break
            if args.interval > 0:
                time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n[結束] 實際擷取 fps ≈ %.1f" % grabber.actual_fps)
    finally:
        grabber.stop_flag.set()


if __name__ == "__main__":
    main()
