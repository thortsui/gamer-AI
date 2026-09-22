# -*- coding: utf-8 -*-
"""本機文字眼睛：背景執行緒持續把畫面上的字讀出來（PP-OCR / rapidocr onnxruntime）。

為什麼要獨立一顆：
YOLO 認得「東西」，認不得「字」。但這款遊戲最關鍵的線索幾乎都是字——
按鈕上寫什麼、便條寫什麼、門牌幾號、有沒有出現 Press E、輪班第幾班、選單上哪個是關閉。
以前這些字他只能從壓縮過的截圖用猜的，錯得很兇（把大廳看板當 UI、把「出口」兩個字當成死亡畫面）。

跑在背景，每 1.2 秒讀一次，主迴圈永遠不用等它。
實測 1280x720 全螢幕約 0.45 秒／次（第一次載模型 1.5 秒）。
"""
import threading
import time

import mss
from PIL import Image

_OCR = None
_WIN = None
_WIN_TRIED = False
_AsyncStatus = None
LANG = "zh-Hant-TW"          # 這台機器已安裝的辨識器；換語言改這裡


def _is_cjk(ch):
    return "\u3400" <= ch <= "\u9fff" or "\uf900" <= ch <= "\ufaff" or "\u3000" <= ch <= "\u303f"


def _join_cjk(text):
    """Windows OCR 把每個漢字當一個 word，之間會有空格：「開 始 遊 戲」→「開始遊戲」。

    只有「兩邊都是漢字」的空格才拿掉，英文與數字之間的空格保留。
    """
    out = []
    for i, ch in enumerate(text):
        if ch == " " and 0 < i < len(text) - 1 and _is_cjk(text[i - 1]) and _is_cjk(text[i + 1]):
            continue
        out.append(ch)
    return "".join(out)


def _win_engine():
    """Windows 內建 OCR（Windows.Media.Ocr）＝ iPhone「原況文字」在 Windows 上的對應品。

    2026-09-18 使用者問「不能像 iPhone 直接從畫面擷取字嗎」。可以，而且這台已經裝了
    繁體中文（台灣）辨識器。同一張遊戲畫面實測：
        rapidocr（PP-OCRv3 mobile，簡體為主）1090 ms → 「朋始戴」0.50
        Windows OCR（zh-Hant-TW）              61 ms → 「開 始 遊 戲」「試 槍」全對
    差別在模型：rapidocr 是 10MB 的行動版、訓練資料以簡體為主，繁體會被對到形近的簡體字；
    Windows 這顆是系統內建、針對繁體訓練，還跑在系統最佳化過的路徑上。
    """
    global _WIN, _WIN_TRIED
    if _WIN is not None or _WIN_TRIED:
        return _WIN
    _WIN_TRIED = True
    try:
        try:                                  # 新版：PyWinRT 的分包（有 Python 3.13 的 wheel）
            from winrt.windows.globalization import Language
            from winrt.windows.media.ocr import OcrEngine
        except ImportError:                   # 舊版：winsdk 單一大包
            from winsdk.windows.globalization import Language
            from winsdk.windows.media.ocr import OcrEngine
        eng = OcrEngine.try_create_from_language(Language(LANG))
        if eng is None:
            eng = OcrEngine.try_create_from_user_profile_languages()
        _WIN = eng
    except Exception as e:  # noqa: BLE001
        print(f"[文字] Windows OCR 用不了（{type(e).__name__}: {e}），改用 rapidocr")
        _WIN = None
    return _WIN


def _win_read(img):
    """用 Windows OCR 讀一張 PIL 圖，回傳跟 rapidocr 同樣格式的 [(box, text, score)]。"""
    import numpy as np
    try:
        from winrt.windows.graphics.imaging import (BitmapAlphaMode, BitmapPixelFormat,
                                                    SoftwareBitmap)
        from winrt.windows.storage.streams import DataWriter
        from winrt.windows.foundation import AsyncStatus
    except ImportError:
        from winsdk.windows.graphics.imaging import (BitmapAlphaMode, BitmapPixelFormat,
                                                     SoftwareBitmap)
        from winsdk.windows.storage.streams import DataWriter
        from winsdk.windows.foundation import AsyncStatus
    eng = _win_engine()
    if eng is None:
        return None
    global _AsyncStatus
    _AsyncStatus = AsyncStatus
    rgba = img.convert("RGBA")
    w, h = rgba.size
    bgra = np.asarray(rgba)[:, :, [2, 1, 0, 3]].tobytes()
    dw = DataWriter()
    dw.write_bytes(bgra)
    buf = dw.detach_buffer()
    bmp = SoftwareBitmap(BitmapPixelFormat.BGRA8, w, h, BitmapAlphaMode.STRAIGHT)
    bmp.copy_from_buffer(buf)
    # 不用 asyncio：WinRT 的非同步物件直接輪詢狀態就好（每輪起一個事件迴圈更貴）
    op = eng.recognize_async(bmp)
    t_end = time.time() + 5.0
    while op.status == _AsyncStatus.STARTED and time.time() < t_end:
        time.sleep(0.003)
    res = op.get_results()
    out = []
    for line in res.lines:
        txt = _join_cjk(str(line.text or "").strip())
        if not txt:
            continue
        xs, ys = [], []
        for word in line.words:
            r = word.bounding_rect
            xs += [r.x, r.x + r.width]
            ys += [r.y, r.y + r.height]
        if not xs:
            continue
        box = [[min(xs), min(ys)], [max(xs), min(ys)], [max(xs), max(ys)], [min(xs), max(ys)]]
        out.append((box, txt, 0.9))       # Windows OCR 不給每行信心，統一給 0.9
    return out



def _engine():
    global _OCR
    if _OCR is None:
        from rapidocr_onnxruntime import RapidOCR
        _OCR = RapidOCR()
    return _OCR


class TextEyes:
    """背景 OCR。latest() 回傳 (lines, stamp)；每一行是 dict(t 文字, x, y 都是 0~1, s 信心)。"""

    # 2026-09-18 使用者：「按鈕上的字讀不到」的實測結論（同一張畫面掃各種解析度）：
    #     960 寬 0.88s → 2 行（讀不到任何按鈕）
    #    1280 寬 1.10s → 3 行（開始遊戲＝「朋始游戴」0.59）
    #  ★ 1536 寬 1.34s → 5 行（開始遊戲＋踩踏板搭乘都讀到）  ← 最好
    #    1920 寬 2.01s → 4 行（**反而讀不到開始遊戲**）
    #    2400 寬 2.45s → 5 行（讀到但慢一倍）
    # 原因：PP-OCR 的偵測器用 limit_type=min（短邊不足 736 才放大），
    # 畫面太大反而不會放大、字在模型眼裡就變小；太小則細節不夠。1536 是這台機器的甜蜜點。
    # 所以不是「不要縮」，是**縮到 1536 這個尺寸**（原本寫死 1280 讀不到按鈕）。
    MAX_W = 2048

    def __init__(self, bbox_fn=None, interval=1.2, width=1536, min_score=0.45,
                 engine="auto"):
        # engine: auto＝先用 Windows 內建 OCR（繁體準又快），用不了才退回 rapidocr；
        #         win＝只用 Windows；rapid＝只用 rapidocr（舊行為）
        self.engine = str(engine or "auto").lower()
        self.use_win = False
        self.bbox_fn = bbox_fn
        self.interval = max(0.4, float(interval))
        self.width = width
        self.min_score = min_score
        self._lock = threading.Lock()
        self._lines = []
        self._stamp = 0.0
        self._ms = []
        self._stop = threading.Event()
        self._thread = None
        self.ready = threading.Event()

    def start(self):
        if self._thread:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="TextEyes")
        self._thread.start()

    def stop(self):
        self._stop.set()

    def latest(self):
        with self._lock:
            return list(self._lines), self._stamp

    def ms(self):
        return sum(self._ms[-10:]) / max(1, len(self._ms[-10:]))

    def _grab(self):
        box = self.bbox_fn() if self.bbox_fn else None
        with (getattr(mss, "MSS", None) or mss.mss)() as sct:
            shot = sct.grab(box or sct.monitors[1])
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        if self.use_win:                # Windows OCR：原尺寸最準，不要動它
            if img.width > self.MAX_W:
                img = img.resize((self.MAX_W, int(self.MAX_W * img.height / img.width)), Image.LANCZOS)
            return img
        cap = min(self.width or self.MAX_W, self.MAX_W)
        if img.width != cap:            # 一律縮放到甜蜜點（太大要縮、太小要放大，兩邊都會影響偵測）
            img = img.resize((cap, int(cap * img.height / img.width)), Image.LANCZOS)
        return img

    def _loop(self):
        import numpy as np
        if self.engine in ("auto", "win") and _win_engine() is not None:
            self.use_win = True
            print(f"[文字] 用 Windows 內建 OCR（{LANG}）——繁體準、每次約 60~80 ms"
                  + ("；讀不到東西時自動退回 rapidocr" if self.engine == "auto" else ""))
        if self.engine != "win":
            try:
                _engine()
            except Exception as e:  # noqa: BLE001
                if not self.use_win:
                    print(f"[文字] OCR 載入失敗，畫面上的字這一輪起讀不到：{e}")
                    return
                print(f"[文字] rapidocr 後備載不起來（{e}），只用 Windows OCR")
        if not self.use_win:
            print("[文字] 本機 OCR 就緒（rapidocr onnxruntime）")
        self.ready.set()
        while not self._stop.is_set():
            t0 = time.time()
            try:
                im = self._grab()
                W, H = im.size
                res = None
                if self.use_win:
                    try:
                        res = _win_read(im)
                    except Exception as e:  # noqa: BLE001
                        if time.time() - getattr(self, "_win_err_at", 0) > 60:
                            self._win_err_at = time.time()
                            print(f"[文字] Windows OCR 這次失敗：{e}")
                        res = None
                    if not res and self.engine == "auto":
                        # Windows OCR 什麼都沒讀到 → 這一輪用 rapidocr 補一次（兩顆互補）
                        small = im if im.width <= 1536 else im.resize(
                            (1536, int(1536 * im.height / im.width)), Image.LANCZOS)
                        W, H = small.size
                        res, _ = _engine()(np.asarray(small))
                else:
                    res, _ = _engine()(np.asarray(im))
                lines = []
                for box, txt, score in (res or []):
                    try:
                        sc = float(score)
                    except (TypeError, ValueError):
                        sc = 0.0
                    txt = str(txt).strip()
                    if not txt or sc < self.min_score:
                        continue
                    xs = [p[0] for p in box]
                    ys = [p[1] for p in box]
                    lines.append({"t": txt[:60], "s": round(sc, 2),
                                  "x": round(sum(xs) / 4 / W, 3),
                                  "y": round(sum(ys) / 4 / H, 3),
                                  "h": round((max(ys) - min(ys)) / H, 3),
                                  # 2026-09-17 使用者：要把**文字所在的框**也給他，
                                  # 這樣「開始遊戲」這種按鈕就有明確的中心與大小，不必猜座標。
                                  "w": round((max(xs) - min(xs)) / W, 3),
                                  "x0": round(min(xs) / W, 3), "y0": round(min(ys) / H, 3),
                                  "x1": round(max(xs) / W, 3), "y1": round(max(ys) / H, 3)})
                lines.sort(key=lambda d: (d["y"], d["x"]))
                with self._lock:
                    self._lines = lines
                    self._stamp = time.time()
                self._ms.append((time.time() - t0) * 1000)
                self._ms = self._ms[-30:]
            except Exception as e:  # noqa: BLE001
                if time.time() - getattr(self, "_err_at", 0) > 30:
                    self._err_at = time.time()
                    print(f"[文字] 這一次讀字失敗：{e}（之後 30 秒內不再重複印）")
                time.sleep(1.0)
            time.sleep(max(0.05, self.interval - (time.time() - t0)))
