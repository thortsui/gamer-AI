# -*- coding: utf-8 -*-
"""舊版（flow_agent／play_agent）有的學習與記憶機制，全部包成工具等他叫（2026-10-15 23:0x 使用者：
「把學習這些的也包裝成一個個的工具，之前的 AI 有的所有東西全部包裝成工具等他使用」）。

這裡是 Tools 的 mixin：學習（大檢討／死亡檢討／成功回推／學習整理／慢想／進步日誌／思考強度／教眼睛）、
記憶（記遊戲／記按鈕／忘記按鈕／記站位／記流程／記互動物／記觸碰物／標點／切空間／記自己／改提示）、
程序（錄成程序→就是 新工具；執行程序＝叫那個名字）、看（追蹤／停止追蹤／放大／拆兩段）。
靠水平儀與座標地圖的（鎖定／走到／設目標／走到記過的／我在／到標點／修水平／水平歸零／量牆距）與看介紹／wiki／訓練眼睛／錄影檢討
先標未實作，目錄有列、叫了回「未實作」。
"""
import json
import os
import re
import subprocess
import sys
import time
from collections import deque
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
NL = "\n"


class ExtraTools:
    rounds = deque(maxlen=60)        # 最近幾輪：{"輪","看到","想","用","回傳"}（死亡檢討／成功回推／錄成程序用）
    watch_labels = []                # 追蹤(標籤)：每輪開頭 YOLO 找一次放進面板
    effort_override = None           # 思考強度(強度)
    plan = None                      # 慢想排的大動作序列
    endpoint = "http://192.168.0.126:8888/v1"
    model = ""

    # ---------------- 共用
    def _ask(self, system, user, effort="慢想", max_tokens=2500, deadline=300, image=None):
        from play_agent import stream_json, parse_json_loose, LAST_REASONING
        import tool_agent as T
        content = [{"type": "text", "text": user}]
        if image is not None:                       # 10/19 突發狀況：帶現在的畫面
            content = [{"type": "image_url", "image_url": {"url": T.img_uri(image)}}] + content
        raw, trunc, _, _ = stream_json(self.endpoint, self.model or T.detect_model(self.endpoint), system,
                                       content, max_tokens, time.time() + deadline, 0.3, effort=effort)
        reason = LAST_REASONING.get("text", "")
        LAST_REASONING["text"] = ""
        o = parse_json_loose(T.repair_json(raw)) or {}
        if not o and reason:                        # 10/19 05:2x：JSON 寫在推理、輸出空 → 從推理取；13:2x 挑欄位最多的那份
            best = {}
            for m in re.finditer(r"\{\s*\"", reason):
                cand = parse_json_loose(T.repair_json(reason[m.start():])) or {}
                if isinstance(cand, dict) and len(cand) >= max(2, len(best)):
                    best = cand
            o = best
        return o, raw

    def remember_round(self, rnd, seen, think, did, rets):
        self.rounds.append({"輪": rnd, "看到": seen, "想": think, "用": list(did), "回傳": [r[:160] for r in rets]})

    def _recent_text(self, n=12):
        return NL.join(f"[{r['輪']}] 看到:{r['看到'][:60]}｜想:{r['想'][:90]}｜用:{'、'.join(r['用']) or '無'}｜回傳:{' / '.join(x[:60] for x in r['回傳'])}"
                       for r in list(self.rounds)[-n:]) or "（還沒有紀錄）"

    def _identity(self):
        return f"你是 gamer AI，正在學 Roblox「{self.game}」。只寫紀錄裡真的有的事，表格式短語，每條都要能照著做。"

    # ---------------- 描述畫面（2026-10-21 19:xx 使用者：「給他製作一個工具，按照目前的畫面給他一段形容的文字，
    # 讓他來更好的辨識遊戲；所有遊戲都要通用，只辨識畫面、不需要訓練」）
    # → 另開一次大腦，只當「畫面描述員」：不玩、不給建議、不帶遊戲名稱與任何遊戲知識；OCR 讀到的字一起給它對照。
    DESC_EFFORT = "快思"
    last_desc = None         # {"類型","總結","時間"}
    desc_changed = ""        # 畫面類型變了（給 重新想任務 用）

    def _ocr_list(self, im):
        try:
            import local_text
            res = local_text._win_read(im) or []
        except Exception:  # noqa: BLE001
            return []
        W, H = im.size
        out = []
        for r in res:
            try:
                box, txt = r[0], str(r[1]).strip()
                if txt:
                    xs, ys = [p[0] for p in box], [p[1] for p in box]
                    out.append(f"「{txt}」({(min(xs) + max(xs)) / 2 / W:.2f},{(min(ys) + max(ys)) / 2 / H:.2f})")
            except Exception:  # noqa: BLE001
                continue
        return out[:40]

    # 20:xx 使用者：「描述畫面要更快，不要進入思考，只用最簡單的方式給出答案，一次最多 20 秒」
    # → 不思考（快思）、逐行短格式（JSON 太長要 45 秒；逐行約 13 秒）、整個工具（含 OCR）限 20 秒，時間到就用已經收到的行
    DESC_LIMIT = 20.0
    desc_items = []          # 描述畫面給的點：[{"編號","名稱","x","y","種類"}]（走過去／追蹤 用）
    DESC_KEYS = ("類型", "視角", "自己", "擋住", "互動提示", "場景", "介面", "道具欄")

    def see_describe(self, 重點="", image=None):
        if image is None:
            image = getattr(self, "_test_image", None)          # 離線實驗用
        if image is None and not self.live and not self.image_path:      # 19:xx 測試：dry-run 會抓到桌面（瀏覽器），不描述
            return "離線／dry-run 不能描述畫面"
        t0 = time.time()
        im = image if image is not None else self.grab()
        if im is None:
            return "抓不到畫面"
        ocr = self._ocr_list(im)[:25]
        t_ocr = time.time() - t0
        from play_agent import stream_json, LAST_REASONING
        import tool_agent as T
        system = ("你是畫面描述員。只描述這張遊戲截圖上看得到的東西：不玩、不給建議、不猜遊戲規則，任何遊戲都一樣。"
                  "看不清楚就寫看不清楚。直接回答，不要思考。位置一律寫 (x,y)，0～1，左上 (0,0)、正中央 (0.5,0.5)。")
        user = ("【程式讀到的字（OCR，可能讀錯；以圖為準）】" + ("、".join(ocr) or "沒讀到字") + NL
                + (f"【想特別看清楚的】{重點}" + NL if str(重點).strip() else "")
                + "照下面格式逐行回答，越短越好，每項只寫幾個字：" + NL
                + "類型：遊戲中／大廳／載入或黑畫面／彈出視窗／死亡畫面／選單／其他" + NL
                + "視角：第一人稱／第三人稱／俯視／2D" + NL
                + "自己：自己的角色在哪（看不到寫看不到）" + NL
                + "擋住：中間有沒有東西擋住" + NL
                + "互動提示：(x,y) [按鍵] 字｜在什麼上；最多 2 項，沒有寫沒有" + NL
                + "場景：東西 (x,y) 近／中／遠；最多 4 項，由近到遠" + NL
                + "介面：內容（可能是什麼）；最多 4 項，不寫座標" + NL
                + "道具欄：每格有什麼，沒有寫沒有")
        left = max(4.0, self.DESC_LIMIT - (time.time() - t0) - 0.3)
        try:
            raw, trunc, _, _ = stream_json(self.endpoint, self.model or T.detect_model(self.endpoint), system,
                                           [{"type": "image_url", "image_url": {"url": T.img_uri(im)}}, {"type": "text", "text": user}],
                                           300, time.time() + left, 0.3, effort="快思")
        except Exception as e:  # noqa: BLE001
            return f"描述畫面失敗：{e}"
        LAST_REASONING["text"] = ""
        dt = time.time() - t0
        d = {}
        for line in raw.splitlines():
            m = re.match(r"\s*[-*•]?\s*\**(" + "|".join(self.DESC_KEYS) + r")\**\s*[:：]\s*(.*)", line)
            if m and m.group(1) not in d:
                d[m.group(1)] = m.group(2).strip()
        if not d:
            return f"描述畫面沒有回答（{dt:.0f} 秒）；OCR 讀到：" + ("、".join(ocr[:15]) or "沒有字")
        items, n = [], 0
        for k in ("互動提示", "場景"):
            parts = []
            for part in re.split(r"[；;／]", d.get(k, "")):
                m = re.search(r"\(?\s*([01](?:\.\d+)?|\.\d+)\s*[,，]\s*([01](?:\.\d+)?|\.\d+)\s*\)?", part)
                if m and part.strip():
                    n += 1
                    name = re.sub(r"\(.*?\)|\[.*?\]|[01]?\.\d+\s*[,，]\s*[01]?\.\d+", "", part)
                    if "｜" in name:                         # 互動提示：用「在什麼東西上」當名字，前面的字附上
                        pre, on = name.split("｜", 1)
                        name = re.sub(r"^在|上$", "", on.strip()) + "（" + pre.strip() + "）"
                    name = re.sub(r"\s+", " ", name).strip(" ，,、")[:30]
                    items.append({"編號": n, "名稱": name, "x": float(m.group(1)), "y": float(m.group(2)), "種類": k})
                    parts.append(f"[{n}]" + part.strip())
                elif part.strip():
                    parts.append(part.strip())
            if parts:
                d[k] = "；".join(parts)
        self.desc_items, self.desc_at = items, time.time()
        kind = d.get("類型", "？")
        prev = self.last_desc
        if prev and prev.get("類型") and kind != "？" and prev["類型"] != kind:
            self.desc_changed = f"畫面類型從「{prev['類型']}」變成「{kind}」"
        self.last_desc = {"類型": kind, "總結": d.get("總結", ""), "時間": datetime.now().strftime("%H:%M")}
        lines = [f"【畫面描述】（描述員只看這張圖、不懂遊戲規則；{dt:.0f} 秒{'，時間到只收到前面幾行' if trunc else ''}）"]
        self.desc_timing = (round(t_ocr, 1), round(dt, 1), len(raw))
        lines += [f"{k}：{d[k]}" for k in self.DESC_KEYS if k in d]
        if items:
            lines.append("→ 要走去其中一個：走過去(編號)；要每輪盯著：追蹤(編號)")
        if self.desc_changed:
            lines.append("⚑ " + self.desc_changed)
        self._append("畫面描述.md", " ｜ ".join(lines[1:])[:700])
        return NL.join(lines)

    # ---------------- 追蹤描述畫面給的點（20:xx 使用者：「追蹤是要去使用他前面畫面描述去追蹤那個點」）
    # 不用訓練、任何遊戲通用：在那個點切一小塊圖當目標，之後每張畫面用影像比對找它（cv2 matchTemplate）
    patch_watch = []         # [{"名稱","tpl","h0","x","y"}]
    TRK_W = 480

    def _trk_gray(self, im):
        import numpy as np
        h = max(1, int(self.TRK_W * im.height / im.width))
        return np.asarray(im.convert("L").resize((self.TRK_W, h)), dtype=np.uint8)

    @staticmethod
    def _trk_crop(g, x, y, pw, ph):
        H, W = g.shape
        x0 = int(max(0, min(W - pw, x * W - pw / 2)))
        y0 = int(max(0, min(H - ph, y * H - ph / 2)))
        return g[y0:y0 + ph, x0:x0 + pw].copy()

    @staticmethod
    def _trk_find(g, tpl, near=None, r=0.3, scales=(1.0,)):
        """→ (x, y, 相似度, 倍數)；near＝(x,y) 只在附近 r 範圍找。"""
        import cv2
        H, W = g.shape
        best = None
        for sc in scales:
            t = tpl if sc == 1.0 else cv2.resize(tpl, (max(6, int(tpl.shape[1] * sc)), max(6, int(tpl.shape[0] * sc))))
            th, tw = t.shape
            if th >= H or tw >= W:
                continue
            x0 = y0 = 0
            area = g
            if near is not None:
                x0 = int(max(0, (near[0] - r) * W - tw / 2))
                y0 = int(max(0, (near[1] - r) * H - th / 2))
                x1 = int(min(W, (near[0] + r) * W + tw / 2))
                y1 = int(min(H, (near[1] + r) * H + th / 2))
                area = g[y0:y1, x0:x1]
                if area.shape[0] <= th or area.shape[1] <= tw:
                    continue
            res = cv2.matchTemplate(area, t, cv2.TM_CCOEFF_NORMED)
            _, mx, _, loc = cv2.minMaxLoc(res)
            if best is None or mx > best[2]:
                best = ((x0 + loc[0] + tw / 2) / W, (y0 + loc[1] + th / 2) / H, float(mx), sc)
        return best

    def _resolve_target(self, 目標="", y=""):
        import tool_agent as T
        q = str(目標).strip()
        fx, fy = T.num(q, -1), T.num(y, -1)
        if 0 <= fx <= 1 and 0 <= fy <= 1 and not re.fullmatch(r"\[?\d+\]?", q):
            return fx, fy, f"({fx:.2f},{fy:.2f})"
        m = re.search(r"\(?\s*([01]?\.\d+|[01])\s*[,，]\s*([01]?\.\d+|[01])\s*\)?", q)
        if m and not re.fullmatch(r"\[?\d+\]?", q):
            return float(m.group(1)), float(m.group(2)), f"({float(m.group(1)):.2f},{float(m.group(2)):.2f})"
        mn = re.fullmatch(r"\[?(\d+)\]?", q)
        # 2x:xx 測試：上次描述的編號在畫面換了之後還被拿來用（座標是舊的）→ 描述 60 秒內才算數；名字先用照片在現在的畫面找
        age = time.time() - getattr(self, "desc_at", 0)
        fresh = self.desc_items if age <= 60 else []
        it = None
        if mn:
            if self.desc_items and not fresh:
                return f"描述畫面是 {age:.0f} 秒前的，畫面可能已經變了；先重新 描述畫面 再用編號"
            it = next((i for i in fresh if i["編號"] == int(mn.group(1))), None)
        elif q and not self._look_name(q):
            it = next((i for i in fresh if q in i["名稱"] or i["名稱"].split("（")[0].split(" ")[0] in q), None)
        if it:
            return it["x"], it["y"], f"[{it['編號']}]{it['名稱']}"
        if q and not mn:                                 # 20:xx 記過長相的東西：直接拿照片在現在的畫面找
            name = self._look_name(q)
            if name:
                im = self.grab()
                f = self._look_find(name, im) if im is not None else None
                if f:
                    self.look_how = f"{name} 在 ({f['x']:.2f},{f['y']:.2f})，{f['法']}"
                    return f["x"], f["y"], name
                return f"畫面上用照片找不到「{name}」（記了 {len(self._look_files(name))} 張）；轉頭看看，或先 描述畫面"
        if not self.desc_items:
            return "還沒有描述畫面給的點、也沒有記過它的長相：先叫 描述畫面，再用 編號／名稱；或直接給座標，例：走過去(0.72,0.55)"
        return "描述畫面的點裡沒有「" + q + "」；有：" + "、".join(f"[{i['編號']}]{i['名稱']}" for i in self.desc_items)

    # ---------------- 長相照片（20:xx 使用者：「他需要可以對東西進行截圖後，直接訓練他自己去對這個東西更認識——
    # 例如他要找電腦，就用腦中之前截到的電腦照片直接去尋找，這樣會更快」）
    # 不是訓練模型：每個東西存幾張截圖，找的時候拿每張照片、好幾種大小在畫面上比對（cv2，一次不到 1 秒），任何遊戲都通用。
    # 20:xx 實驗：只用影像比對在別的畫面找，25 次對 0 次（椅子到處都「找到」）；
    # 把照片＋現在的畫面給大腦、不思考、只回 (x,y)：12 次對 11 次，一次 0.4～1.5 秒；照片不附名字比較準（名字取錯會被帶偏）
    # → 先影像比對（≥0.85＝幾乎同一個角度，直接用），不夠像再問大腦
    LOOK_NORM = 1024          # 截照片前先把畫面縮成這個寬（跟給大腦的圖一樣）
    LOOK_FAST = 0.85

    def _look_dir(self):
        return os.path.join(self.mdir, "長相")

    # 2x:xx 使用者：「不用近景圖，讓他先把看到的東西框下來，再拿那張有框、標記好的圖去看」
    # 實驗（同 6 張圖、相機＋紫色表格）：近景圖 8/12（沒有的圖誤報 3 次）；整張圖＋紅框 11/12（沒有的 6/6 都對）
    # → 每張記兩個檔：*_框.png＝整張畫面＋紅框（給大腦認）、*_近.png＝框裡那塊（給影像比對快速找）
    def _look_files(self, name, kind="框"):
        d = os.path.join(self._look_dir(), name)
        try:
            return sorted(os.path.join(d, f) for f in os.listdir(d) if f.endswith(f"_{kind}.png"))
        except OSError:
            return []

    def _look_names(self):
        try:
            return sorted(n for n in os.listdir(self._look_dir()) if self._look_files(n))
        except OSError:
            return []

    def _look_name(self, q):
        names = self._look_names()
        return next((n for n in names if n == q), None) or next((n for n in names if q in n or n in q), None)

    def _look_ask(self, files, im):
        """照片＋現在的畫面給大腦（不思考、只回一行）→ ((x,y) 或 None, 秒)"""
        from play_agent import stream_json, LAST_REASONING
        from PIL import Image
        import tool_agent as T
        content = []
        for i, fp in enumerate(files):
            try:
                content += [{"type": "text", "text": f"【以前的畫面 {i + 1}，紅框框起來的是要找的東西】"},
                            {"type": "image_url", "image_url": {"url": T.img_uri(Image.open(fp).convert("RGB"))}}]
            except OSError:
                continue
        if not content:
            return None, 0.0
        content += [{"type": "text", "text": "【現在的畫面】"}, {"type": "image_url", "image_url": {"url": T.img_uri(im)}},
                    {"type": "text", "text": "現在的畫面上有沒有紅框裡的那個東西（同一個東西，角度、遠近可能不同；現在的畫面沒有紅框）？"
                                             "有就只回一行：有 (x,y)，x,y 是它中心在現在畫面上的位置 0～1；沒有就只回：沒有"}]
        t0 = time.time()
        try:
            raw, _, _, _ = stream_json(self.endpoint, self.model or T.detect_model(self.endpoint),
                                       "你是找東西的眼睛。前面是以前的畫面（紅框是要找的東西），最後一張是現在的遊戲畫面。不要思考，直接回答。",
                                       content, 40, time.time() + 10, 0.2, effort="快思")
        except Exception:  # noqa: BLE001
            return None, time.time() - t0
        LAST_REASONING["text"] = ""
        m = re.search(r"有\s*[(（]?\s*([01]?\.\d+|[01])\s*[,，]\s*([01]?\.\d+|[01])", raw)
        return ((float(m.group(1)), float(m.group(2))) if m else None), time.time() - t0

    def _look_find(self, name, im):
        """→ {"x","y","分","法","秒"} 或 None"""
        import numpy as np
        from PIL import Image
        t0 = time.time()
        files = self._look_files(name, "近")[-12:]
        g = self._trk_gray(im)
        k = self.TRK_W / self.LOOK_NORM
        best = None
        for fp in files:
            try:
                p = Image.open(fp).convert("L")
                tpl = np.asarray(p.resize((max(6, int(p.width * k)), max(6, int(p.height * k)))), dtype=np.uint8)
            except OSError:
                continue
            f = self._trk_find(g, tpl, scales=(0.8, 1.0, 1.25))
            if f and (best is None or f[2] > best[2]):
                best = f
        if best and best[2] >= self.LOOK_FAST:
            return {"x": best[0], "y": best[1], "分": best[2], "法": f"照片比對 相似度 {best[2]:.2f}", "秒": time.time() - t0}
        xy, _ = self._look_ask(self._look_files(name)[-2:], im)
        if xy:
            return {"x": xy[0], "y": xy[1], "分": 1.0, "法": "大腦對照片認出來", "秒": time.time() - t0}
        return None

    def _look_box(self, big, x, y, hint):
        """叫眼睛把 (x,y) 那裡的東西整個框起來（不思考）→ (x1,y1,x2,y2) 或 None"""
        from play_agent import stream_json, LAST_REASONING
        import tool_agent as T
        try:
            raw, _, _, _ = stream_json(self.endpoint, self.model or T.detect_model(self.endpoint), "你是畫面標記員。不要思考，直接回答。",
                                       [{"type": "image_url", "image_url": {"url": T.img_uri(big)}},
                                        {"type": "text", "text": f"畫面上 ({x:.2f},{y:.2f}) 那裡的{hint}，把整個東西框起來。只回一行：x1,y1,x2,y2（0～1，左上到右下）"}],
                                       40, time.time() + 10, 0.2, effort="快思")
        except Exception:  # noqa: BLE001
            return None
        LAST_REASONING["text"] = ""
        v = [float(n) for n in re.findall(r"[01]?\.\d+|[01]", raw)[:4]]
        if len(v) == 4 and v[0] < v[2] and v[1] < v[3] and v[0] <= x <= v[2] and v[1] <= y <= v[3]:
            return tuple(v)
        return None

    def mem_look(self, 名稱="", 目標=""):
        from PIL import ImageDraw
        name = re.sub(r"[\\/:*?\"<>|\s]+", "", str(名稱))[:20]
        if not name:
            return ("記長相 要給 名稱,目標，例：記長相(電腦,3)＝把 描述畫面 的 [3] 框起來記成「電腦」；"
                    "目標也可以給座標 0.72,0.55，或自己框 x1,y1,x2,y2（例：記長相(電腦,0.40,0.20,0.60,0.55)）")
        if not self.live and not self.image_path and not getattr(self, "_test_image", None):
            return "離線／dry-run 不能截圖"
        tgt = str(目標).strip() or name
        nums = [float(n) for n in re.findall(r"[01]?\.\d+|[01]", tgt)]
        im = getattr(self, "_test_image", None) or self.grab()
        if im is None:
            return "抓不到畫面"
        big = im.convert("RGB").resize((self.LOOK_NORM, max(1, int(self.LOOK_NORM * im.height / im.width))))
        W, H = big.size
        if len(nums) >= 4 and nums[0] < nums[2] and nums[1] < nums[3]:      # 他自己框的
            box, src = tuple(nums[:4]), "你自己框的"
        else:
            r = self._resolve_target(tgt)
            if isinstance(r, str):
                return r
            x, y, src = r
            hint = re.sub(r"^\[\d+\]|（.*", "", src).strip() or name
            box = self._look_box(big, x, y, hint)
            if not box:                                  # 眼睛沒框好：用點附近一塊
                box = (max(0.0, x - 0.08), max(0.0, y - 0.11), min(1.0, x + 0.08), min(1.0, y + 0.11))
                src += "（眼睛沒框好，用點附近一塊）"
            else:
                src += "（眼睛框的）"
        x1, y1, x2, y2 = int(box[0] * W), int(box[1] * H), int(box[2] * W), int(box[3] * H)
        d = os.path.join(self._look_dir(), name)
        os.makedirs(d, exist_ok=True)
        ts = datetime.now().strftime("%m%d_%H%M%S")
        big.crop((x1, y1, x2, y2)).save(os.path.join(d, ts + "_近.png"))
        marked = big.copy()
        ImageDraw.Draw(marked).rectangle((x1, y1, x2, y2), outline=(255, 0, 0), width=4)
        marked.save(os.path.join(d, ts + "_框.png"))
        n = len(self._look_files(name))
        return (f"記下「{name}」的長相（第 {n} 張：整張畫面＋紅框 ({box[0]:.2f},{box[1]:.2f})～({box[2]:.2f},{box[3]:.2f})，{src}）。"
                f"之後 找長相({name})、走過去({name})、追蹤({name}) 都會拿這張框好的圖去找（約 1 秒）；不同角度、遠近多記幾張會更準")

    def see_look(self, 名稱=""):
        q = str(名稱).strip()
        names = [self._look_name(q)] if q else self._look_names()[:6]
        names = [n for n in names if n]
        if not names:
            return (f"沒有記過「{q}」的長相；" if q else "還沒記過任何長相；") + "先 描述畫面，再 記長相(名稱,編號)"
        if not self.live and not self.image_path and not getattr(self, "_test_image", None):
            return "離線／dry-run 不能找"
        im = getattr(self, "_test_image", None) or self.grab()
        if im is None:
            return "抓不到畫面"
        t0 = time.time()
        found, miss = [], []
        for n in names:
            f = self._look_find(n, im)
            if f:
                found.append(f"{n}：在 ({f['x']:.2f},{f['y']:.2f})（{f['法']}）")
            else:
                miss.append(n)
        return (f"用照片找（{time.time() - t0:.1f} 秒）：" + ("；".join(found) if found else "都沒找到")
                + (("｜畫面上沒有：" + "、".join(miss)) if miss else "") + ("｜→ 走過去(名稱)" if found else ""))

    def see_watch(self, 標籤=""):
        q = str(標籤).strip()
        if q in ("停", "停止", "不用", "關"):
            return self.see_unwatch()                   # 停止追蹤 併進來
        r = self._resolve_target(q) if (self.desc_items or re.search(r"\d?\.\d", q) or self._look_name(q)) else "英文標籤"
        if not isinstance(r, str):                      # 描述畫面的編號／名稱／座標 → 用圖塊追蹤
            im = self.grab()
            if im is None:
                return "抓不到畫面"
            x, y, name = r
            g = self._trk_gray(im)
            tpl = self._trk_crop(g, x, y, int(0.09 * g.shape[1]), int(0.12 * g.shape[0]))
            self.patch_watch = ([w for w in self.patch_watch if w["名稱"] != name] + [{"名稱": name, "tpl": tpl, "h0": tpl.shape[0], "x": x, "y": y}])[-4:]
            return (f"開始每輪追蹤「{name}」（從 ({x:.2f},{y:.2f}) 切一塊圖當它的長相）；面板【追蹤】每輪告訴你它在哪、變大還變小；"
                    "要走過去就 走過去(" + name.split("]")[0].strip("[") + ")")
        labels = [x.strip() for x in re.split(r"[,，、]", q) if x.strip()]
        if not labels:
            return "追蹤 要給 描述畫面的編號、座標，或英文標籤，例：追蹤(2)、追蹤(0.72,0.55)、追蹤(door)"
        self.watch_labels = labels[:4]
        return "開始每輪追蹤：" + "、".join(self.watch_labels) + "（面板【追蹤】每輪告訴你它在哪、多大）"

    def see_unwatch(self):
        self.watch_labels = []
        self.patch_watch = []
        return "停止追蹤"

    def patch_watch_text(self, im):
        if not self.patch_watch or im is None:
            return ""
        g = self._trk_gray(im)
        out = []
        for w in self.patch_watch:
            f = self._trk_find(g, w["tpl"], scales=(0.8, 1.0, 1.25, 1.5))
            if not f or f[2] < 0.55:
                out.append(f"{w['名稱']}：畫面上找不到（最像的相似度 {f[2] if f else 0:.2f}；可能轉開了、被擋住或已經走到面前）")
                continue
            x, y, sc, k = f
            size = w["tpl"].shape[0] * k / w["h0"]
            out.append(f"{w['名稱']}：在 ({x:.2f},{y:.2f})（{'左邊' if x < 0.45 else '右邊' if x > 0.55 else '正前方'}），"
                       f"大小是剛開始的 {size:.1f} 倍，相似度 {sc:.2f}")
            if sc >= 0.75 and k != 1.0:                 # 長相跟著變大變小更新
                w["tpl"] = self._trk_crop(g, x, y, int(w["tpl"].shape[1] * k), int(w["tpl"].shape[0] * k))
            w["x"], w["y"] = x, y
        return "；".join(out)

    def move_track(self, 目標="", y=""):
        """走過去：盯著描述畫面給的點（或座標），程式自己 轉正→走一小步→再找它，到面前／出現新字／被擋／找不到 就停。"""
        r = self._resolve_target(目標, y)
        if isinstance(r, str):
            return r
        x, y, name = r
        if self.image_path or not self.live:
            return f"[dry-run] 會盯著「{name}」({x:.2f},{y:.2f}) 邊轉邊走過去"
        im = self.grab()
        if im is None:
            return "抓不到畫面"
        g = self._trk_gray(im)
        GH, GW = g.shape
        tpl = self._trk_crop(g, x, y, int(0.09 * GW), int(0.12 * GH))
        h0 = tpl.shape[0]
        is_new = self._new_word_checker(im)
        log, reason, lost = [], "", 0
        for i in range(8):
            yaw, _ = self._angles_to(x, 0.5, im.width, im.height)
            if abs(yaw) >= 3:
                self._drag(yaw * self.dx_deg, 0)
                im = self._settled()
                g = self._trk_gray(im)
                f = self._trk_find(g, tpl, near=(0.5, y), r=0.2)
                if f and f[2] >= 0.5:
                    x, y = f[0], f[1]
                else:
                    x = 0.5                               # 轉到中間被自己的角色擋住常找不到：當作已轉正，走一步再找
            before = im
            self._key("w", 0.4)
            time.sleep(0.25)
            im = self.grab()
            if im is None:
                reason = "抓不到畫面"
                break
            g = self._trk_gray(im)
            if self.diff(before, im) < 2:
                reason = "走了畫面沒變＝前面被擋住"
                break
            f = self._trk_find(g, tpl, near=(x, y), r=0.25, scales=(1.0, 1.1, 1.2, 1.35))
            if not f or f[2] < 0.5:
                lost += 1
                log.append(f"第{i + 1}步 找不到它（相似度 {f[2] if f else 0:.2f}）")
                if lost >= 2:
                    reason = "連兩步找不到它（可能被擋住、已經走到面前或走過頭）"
                    break
                continue
            lost = 0
            x, y, sc, k = f
            nw, nh = min(int(0.4 * GW), int(tpl.shape[1] * k)), min(int(0.45 * GH), int(tpl.shape[0] * k))
            tpl = self._trk_crop(g, x, y, nw, nh)
            size = tpl.shape[0] / h0
            log.append(f"第{i + 1}步 在 ({x:.2f},{y:.2f}) 大小 {size:.1f} 倍（相似度 {sc:.2f}）")
            words = self._words(im)
            new = [w for w in words if is_new(w)]
            if new:
                reason = "跳出原本沒有的字：" + "、".join(f"「{w}」({words[w][0]:.2f},{words[w][1]:.2f})" for w in new[:4])
                break
            if size >= 2.5 or tpl.shape[0] >= 0.4 * GH:
                reason = f"它已經變大 {size:.1f} 倍＝到面前了"
                break
        else:
            reason = "走滿 8 步"
        self._append("刻度樣本.md", f"走過去 {name}｜{len(log)} 步｜停：{reason[:50]}")
        return (f"走過去「{name}」：" + "；".join(log) + f"。停下原因：{reason}。現在的畫面下一輪給你"
                + ("；到了就試 長按(e,1.5) 或 讀字" if "面前" in reason or "字" in reason else ""))

    # ---------------- 學習
    def learn_big_review(self):
        """大檢討：把今天整天的紀錄給你自己回顧（會花 3～6 分鐘，遊戲這段沒人操作）。"""
        date = datetime.now().strftime("%Y-%m-%d")
        r = subprocess.run([sys.executable, os.path.join(HERE, "big_review.py"), "--game", self.game, "--date", date,
                            "--endpoint", self.endpoint], capture_output=True, text=True, encoding="utf-8")
        big = ""
        try:
            big = open(os.path.join(self.mdir, "大檢討.md"), encoding="utf-8").read()
        except OSError:
            pass
        m = re.search(r"## 明天開場要做什麼\n(.*?)(?=\n## |\Z)", big, flags=re.S)
        return ("大檢討寫好了（記憶/工具版/大檢討.md，之後開機會帶進來）。你排的開場：" + NL + (m.group(1).strip() if m else "（沒解析到）")
                if big else "大檢討失敗：" + (r.stderr or r.stdout)[-300:])

    def learn_death_review(self):
        """死亡檢討：剛死掉就叫，回顧死前最近 15 輪。"""
        o, raw = self._ask(self._identity(),
                           "你剛剛死了（理智歸零或倒下）。這是死前最近幾輪：" + NL + self._recent_text(15) + NL * 2
        # 10/21 06:5x：max_tokens 1200 被慢想推理吃光、回「沒解析出來」→ 學習類一律 8000（跟主程式一樣）
                           + '只輸出 JSON：{"死前在做什麼":"<=40字","死因":"<=40字","錯在哪":"<=60字","下次從哪一步開始改":"用工具寫，例如 先 我的記憶(卡法) 再 走(0.5)","要記的規則":"一句"}',
                           effort="慢想", max_tokens=8000, deadline=240)
        if not o:
            return "死亡檢討沒解析出來"
        line = "｜".join(f"{k}:{v}" for k, v in o.items() if isinstance(v, str))
        self._append("死亡檢討.md", line)
        self.reviews.append(datetime.now().strftime("%H:%M") + " 死亡檢討：" + line)
        if o.get("要記的規則"):
            self.mem_note(str(o["要記的規則"]))
        return "死亡檢討記下了：" + line

    def learn_success(self, 做成了什麼=""):
        """成功回推：剛做成一件事（E 有反應、任務字換了、進了門）就叫，把最近幾輪回推成可重複的步驟。"""
        o, raw = self._ask(self._identity(),
                           f"你剛做成了：{做成了什麼}。這是最近幾輪：" + NL + self._recent_text(12) + NL * 2
                           + '把它回推成下次可以直接照做的步驟。只輸出 JSON：{"做成的事":"","關鍵是哪一步":"","步驟":["工具(參數)","…"],"下次怎麼更快":""}',
                           effort="中想", max_tokens=8000, deadline=180)
        if not o:
            return "成功回推沒解析出來"
        steps = o.get("步驟") or []
        line = f"做成:{o.get('做成的事', 做成了什麼)}｜關鍵:{o.get('關鍵是哪一步', '')}｜步驟:{'→'.join(map(str, steps))}｜更快:{o.get('下次怎麼更快', '')}"
        self._append("成功做法.md", line)
        return ("成功做法記下了：" + line + "。想變成一個工具就 新工具(名稱,用途," + "→".join(map(str, steps)) + ")"
                + "；這是你自己的新發現，也可以留一條 記準則(對象,準則)")

    def learn_organize(self):
        """學習整理：把筆記／卡法／因果／檢討濃縮成最多 12 條規則，之後每輪開機帶著。"""
        def rd(f, n=80):
            try:
                return NL.join(open(os.path.join(self.mdir, f), encoding="utf-8").read().splitlines()[-n:])
            except OSError:
                return ""
        raw_notes = "【筆記】" + rd("筆記.md") + NL + "【卡法】" + rd("卡法.md") + NL + "【因果】" + rd("因果.md") + NL + "【檢討】" + rd("檢討.md", 60)
        o, raw = self._ask(self._identity(),
                           "以下是你自己寫的筆記、卡法、因果、檢討（很多重複）。濃縮成最多 12 條確定的規則，每條 <=30 字、表格式、能照做。" + NL + raw_notes[:9000] + NL
                           + '只輸出 JSON：{"規則":["…"]}', effort="慢想", max_tokens=8000, deadline=300)
        rules = [str(x) for x in (o.get("規則") or []) if str(x).strip()]
        if not rules:
            return "學習整理沒整理出東西"
        with open(os.path.join(self.mdir, "學習整理.md"), "w", encoding="utf-8") as f:
            f.write(f"# 學習整理（{datetime.now().strftime('%m/%d %H:%M')}，你自己整理的）" + NL + NL.join("- " + r for r in rules) + NL)
        return f"整理成 {len(rules)} 條（記憶/工具版/學習整理.md，之後開機帶著）：" + "；".join(rules[:6]) + "…"

    def learn_slow_think(self, 問題=""):
        """慢想：不趕時間地想接下來的大動作序列（xhigh）。"""
        big = self.big or {}
        o, raw = self._ask(self._identity(),
                           f"目前的大任務：{big.get('名稱', '（沒設）')}｜成功條件：{big.get('成功條件', '')}" + NL
                           + (f"你想的問題：{問題}" + NL if 問題 else "")
                           + "最近幾輪：" + NL + self._recent_text(10) + NL
                           + '排出接下來的大動作序列與第一步的細步驟。只輸出 JSON：{"大動作序列":["…","…"],"第一步細步驟":["工具(參數)","…"],"為什麼這樣排":"<=60字"}',
                           effort="慢想", max_tokens=8000, deadline=300)
        if not o:
            return "慢想沒解析出來"
        self.plan = {"序列": [str(x) for x in (o.get("大動作序列") or [])], "細步驟": [str(x) for x in (o.get("第一步細步驟") or [])],
                     "為什麼": str(o.get("為什麼這樣排", "")), "時間": datetime.now().strftime("%H:%M")}
        self._append("慢想.md", "序列:" + "→".join(self.plan["序列"]) + "｜第一步:" + "→".join(self.plan["細步驟"]) + "｜" + self.plan["為什麼"])
        return "慢想排好了（之後面板【慢想排的】會一直顯示）：" + "→".join(self.plan["序列"]) + "；第一步：" + "→".join(self.plan["細步驟"])

    def learn_daily_report(self):
        """進步日誌：程式把今天的數字＋大檢討摘要寫成一份（不叫模型）。"""
        date = datetime.now().strftime("%Y-%m-%d")
        stats = subprocess.run([sys.executable, os.path.join(HERE, "stats.py"), "--game", self.game, "--date", date],
                               capture_output=True, text=True, encoding="utf-8").stdout
        big = ""
        try:
            big = open(os.path.join(self.mdir, "大檢討.md"), encoding="utf-8").read()[:2500]
        except OSError:
            pass
        d = os.path.join(self.mdir, "進步日誌")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, date + ".md")
        with open(p, "w", encoding="utf-8") as f:
            f.write(f"# 進步日誌 {date}" + NL + NL + "## 數字" + NL + stats + NL + "## 大檢討摘要" + NL + big)
        return f"進步日誌寫好：記憶/工具版/進步日誌/{date}.md" + NL + stats[:600]

    def learn_effort(self, 強度=""):
        """思考強度：低／中／高（每一輪的推理深度）。"""
        v = str(強度).strip()
        table = {"低": "快思推理", "中": "中想", "高": "慢想", "快": "快思推理", "慢": "慢想", "反射": "反射"}
        if v not in table:
            return "思考強度 要給 低／中／高（低＝快、字少；高＝想久、每輪 60 秒以上）"
        if getattr(self, "effort_locked", False):     # 10/18 使用者：「這次挑戰都用高思考」
            return f"這次挑戰使用者定了思考強度（{getattr(self, 'effort_fixed', '')}），不能改；照這個繼續"
        self.effort_override = table[v]
        return f"之後每輪用「{v}」（{table[v]}）；想改回來再叫一次"

    def learn_teach_eyes(self, 看到="", 其實是=""):
        """教眼睛：YOLO 認錯了（看到 X 其實是 Y）→ 記進眼睛更正，收工訓練用。"""
        if not str(其實是).strip():
            return "教眼睛 要寫 看到,其實是，例如 教眼睛(chair,coffee machine)"
        p = os.path.join(self.gdir, "記憶", "眼睛更正.jsonl")
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps({"時間": datetime.now().strftime("%m/%d %H:%M"), "看到": 看到, "其實是": 其實是, "來源": "工具版"}, ensure_ascii=False) + NL)
        return f"記下：眼睛把「{看到}」看成「{其實是}」（記憶/眼睛更正.jsonl；訓練眼睛 還沒做，先累積）"

    # ---------------- 記憶（舊版有的）
    def mem_game(self, 名稱="", 作者="", 封面特徵="", 進入步驟=""):
        p = os.path.join(self.gdir, "記憶", "工具版", "遊戲身分.json")
        d = {"名稱": 名稱, "作者": 作者, "封面特徵": 封面特徵, "進入步驟": 進入步驟, "時間": datetime.now().strftime("%m/%d %H:%M")}
        with open(p, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        return "記住這隻遊戲的身分：" + json.dumps(d, ensure_ascii=False) + "（回到遊戲／退出重進會對照）"

    def mem_button(self, 名稱="", x="", y="", 效果=""):
        p = os.path.join(self.mdir, "按鈕簿.json")
        try:
            book = json.load(open(p, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            book = {}
        if not str(名稱).strip():
            return "記按鈕 要給名稱，例如 記按鈕(立即開始,0.62,0.80,進場)"
        if str(x).strip() in ("刪", "刪除", "忘記") or str(效果).strip() in ("刪", "刪除", "忘記"):
            return self.mem_forget_button(名稱)          # 忘記按鈕 併進來
        last = self.pending_click or {}
        fx = x if str(x).strip() else last.get("x", "")
        fy = y if str(y).strip() else last.get("y", "")
        book[str(名稱)] = {"x": fx, "y": fy, "效果": 效果, "時間": datetime.now().strftime("%m/%d %H:%M")}
        with open(p, "w", encoding="utf-8") as f:
            json.dump(book, f, ensure_ascii=False, indent=1)
        return f"按鈕簿記下「{名稱}」({fx},{fy}) 效果:{效果}。之後 查準則(按鈕簿) 看得到，直接 點(x,y)"

    def mem_forget_button(self, 名稱=""):
        p = os.path.join(self.mdir, "按鈕簿.json")
        try:
            book = json.load(open(p, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return "按鈕簿是空的"
        if str(名稱) in book:
            del book[str(名稱)]
            with open(p, "w", encoding="utf-8") as f:
                json.dump(book, f, ensure_ascii=False, indent=1)
            return f"刪了「{名稱}」"
        return f"按鈕簿沒有「{名稱}」"

    def mem_stance(self, 名稱="", 樣子=""):
        return self._append("站位.md", f"{名稱}｜{樣子}｜（這版沒座標，只記名字和樣子，之後用 我的記憶(站位) 回想）")

    def mem_flow(self, 名字="", 步驟=""):
        steps = [s.strip() for s in re.split(r"[→>；;,，]", str(步驟)) if s.strip()]
        if not str(名字).strip() or not steps:
            return "記流程 要給 名字,步驟（用 → 隔開）"
        return self._append("流程.md", f"{名字}｜" + "→".join(steps)) + "。想一次做完就 新工具(" + 名字 + ",用途," + "→".join(steps) + ")"

    def mem_interact(self, 名稱="", 功能="", 用法=""):
        return self._append("互動物.md", f"{名稱}｜功能:{功能}｜用法:{用法}")

    def mem_touch(self, 名稱="", 功能="", 用法=""):
        return self._append("觸碰物.md", f"{名稱}｜站上去:{功能}｜{用法}")

    def mem_landmark(self, 名稱="", 樣子="", 方向=""):
        return self._append("標點.md", f"{名稱}｜樣子:{樣子}｜方向:{方向}｜（這版沒座標地圖，只記文字）")

    def mem_space(self, 名稱="", 為什麼=""):
        self._append("空間.md", f"切到:{名稱}｜{為什麼}")
        self.startup.pop("驗證", None)
        return f"切到空間「{名稱}」：開工數值要重驗（驗開工）"

    def mem_self(self, 樣子=""):
        return self._append("自己的樣子.md", str(樣子)) + "。之後靠看不看得到自己判斷第一／第三人稱"

    def mem_prompt(self, 內容=""):
        """改提示：他自己寫、每輪開機帶進 system 的一段（最多 600 字）。"""
        p = os.path.join(self.mdir, "我的提示.md")
        s = str(內容).strip()[:600]
        with open(p, "w", encoding="utf-8") as f:
            f.write(s)
        return f"你的提示改成（{len(s)} 字，之後每輪帶著）：{s[:80]}…"

    # ---------------- 準則（2026-10-19 12:2x 使用者：「為什麼越記憶越多越慢？檢討後是製作成工具或只留準則，
    # 例如：吃了椅子發現椅子不能吃，在他去確認能不能吃時才會呼叫出來看能不能吃」）
    # 面板不再每輪帶全部紀錄與檢討；教訓存成準則，要做某件事前用 查準則 叫出來。
    def _rules_load(self):
        try:
            return json.load(open(os.path.join(self.mdir, "準則.json"), encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def _rules_save(self, rules):
        with open(os.path.join(self.mdir, "準則.json"), "w", encoding="utf-8") as f:
            json.dump(rules, f, ensure_ascii=False, indent=1)

    @staticmethod
    def _bg(x):
        x = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", str(x))
        return {x[i:i + 2] for i in range(len(x) - 1)}

    # 10/21 18:5x 使用者：記憶只會「加次數」，沒有「照著做了還是失敗」的反證，錯得越多次的準則排越前面
    # → 查準則 叫出來的準則，接下來 3 輪的 符合 記回那條（照做✓／照做✗）；查準則 照成功率排序、每條附讀數。只給讀數，不刪不改。
    RULE_WATCH = 3
    rules_watch = {}          # "對象｜準則" → 還要記幾輪

    @staticmethod
    def _rule_key(r):
        return f"{r['對象']}｜{r['準則']}"

    def _rule_bad(self, r):
        return r.get("照做✗", 0) + 0.5 * max(0, r.get("次數", 1) - 1)     # 同一條又記一次＝同樣情況又遇到

    def _rule_reading(self, r):
        ok, ng, again = r.get("照做✓", 0), r.get("照做✗", 0), max(0, r.get("次數", 1) - 1)
        parts = []
        if again:
            parts.append(f"記了 {r.get('次數', 1)} 次＝同樣情況又遇到 {again} 次")
        parts.append(f"查出來後 {self.RULE_WATCH} 輪內 ✓{ok} ✗{ng}" if ok or ng else "還沒有照做結果")
        if ng >= 3 and ng > 2 * ok:
            parts.append("⚠ 照它做多半是 ✗")
        return "（" + "｜".join(parts) + "）"

    def rules_outcome(self, ok):
        """主迴圈每輪拿到 符合 之後叫：記回最近查出來的準則。"""
        if ok is None or not self.rules_watch:
            return
        rules = self._rules_load()
        by = {self._rule_key(r): r for r in rules}
        for k in list(self.rules_watch):
            r = by.get(k)
            if r is not None:
                r["照做✓" if ok else "照做✗"] = r.get("照做✓" if ok else "照做✗", 0) + 1
            self.rules_watch[k] -= 1
            if self.rules_watch[k] <= 0 or r is None:
                del self.rules_watch[k]
        self._rules_save(rules)

    def mem_rule(self, 對象="", 準則=""):
        obj, rule = str(對象).strip(), str(準則).strip()
        if len(re.sub(r"\W", "", obj)) < 1 or len(re.sub(r"\W", "", rule)) < 3:
            return "記準則 要給 對象,準則，例：記準則(咖啡機,長按E泡咖啡要等93秒，喝了加理智)"
        rules = self._rules_load()
        now = datetime.now().strftime("%m/%d %H:%M")
        if rule.startswith("更正"):              # 發現以前的準則不對：取代這個對象的舊準則
            new = re.sub(r"^更正\s*[:：]?\s*", "", rule)
            old = [r for r in rules if r["對象"] == obj]
            rules = [r for r in rules if r["對象"] != obj] + [{"對象": obj, "準則": new, "時間": now, "次數": 1}]
            self._rules_save(rules)
            return f"已更正：{obj} 的 {len(old)} 條舊準則換成「{new}」"
        for r in rules:
            if r["對象"] == obj and self._wf_similar(r["準則"], rule):
                r["次數"] += 1
                r["準則"], r["時間"] = rule, now
                self._rules_save(rules)
                ret = f"這條跟以前的準則一樣，更新成：{obj}｜{rule}" + self._rule_reading(r)
                if r.get("照做✗", 0) >= 3 and r.get("照做✗", 0) > 2 * r.get("照做✓", 0):
                    ret += NL + f"→ 照這條做的結果多半是 ✗，它可能不對；要改就 記準則({obj},更正:新的做法)"
                return ret
        rules.append({"對象": obj, "準則": rule, "時間": now, "次數": 1})
        self._rules_save(rules)
        return f"準則記下了：{obj}｜{rule}（共 {len(rules)} 條；要對 {obj} 動手前用 查準則({obj}) 叫出來）"

    def _mem_lines(self, fname, tail=400):
        try:
            return [l.strip("- ").strip() for l in open(os.path.join(self.mdir, fname), encoding="utf-8").read().splitlines()[-tail:] if l.strip()]
        except OSError:
            return []

    def mem_rule_find(self, 對象或情況=""):
        q = str(對象或情況).strip()
        if not q:
            return "查準則 要給 對象或情況，例：查準則(咖啡機)"
        if q in ("玩法摘要", "成功做法", "卡住檢討", "死亡檢討", "操作手冊", "遇到的問題", "因果", "學到的", "地圖", "筆記",
                 "大檢討", "卡法", "按鈕簿", "站位", "流程", "學習整理", "慢想", "互動物", "標點", "地點"):
            return self.doc_memory(q)                    # 我的記憶 併進來：直接叫出那一類
        qb = self._bg(q)
        rmap = {}
        for r in self._rules_load():
            rmap[self._rule_key(r) + self._rule_reading(r)] = r
        pool = [("準則", t) for t in rmap]
        pool += [(src, l) for src, f in (("筆記", "筆記.md"), ("卡法", "卡法.md"), ("互動物", "互動物.md"),
                                          ("地點", "地點.md"), ("成功做法", "成功做法.md"), ("因果", "因果.md")) for l in self._mem_lines(f)]
        pool += [("檢討", r) for r in list(self.reviews)] + [("舊檢討", l) for l in self._mem_lines("檢討.md", 300)]
        # 10/20：以前只要一半字組對上就算，一次回報幾百條 → 收緊：整句出現，或至少對上 2 組且 6 成（短的 8 成）；
        # 再把相近的合併成一條（×N＝同一件事記了 N 次）
        need = 0.8 if len(qb) <= 3 else 0.6
        scored, seen = [], set()
        for src, txt in pool:
            key = re.sub(r"^\d\d/\d\d \d\d:\d\d\s*", "", txt)
            if key in seen:
                continue
            seen.add(key)
            hit = q in txt
            tb = self._bg(key)
            m = len(qb & tb)
            if hit or (m >= 2 and m / max(1, len(qb)) >= need):
                sc = (10 if hit else 0) + m / max(1, len(qb)) * 5 + {"準則": 3, "檢討": 1}.get(src, 0)
                if src == "準則":            # 照做成功率：✓ 多往前、✗（含又遇到）多往後
                    r = rmap[txt]
                    ok, bad = r.get("照做✓", 0), self._rule_bad(r)
                    sc += 4 * (ok - bad) / (ok + bad + 2)
                scored.append([sc, src, txt, tb, 1])
        scored.sort(key=lambda x: -x[0])
        kept = []
        for x in scored:
            near = next((y for y in kept if len(x[3] & y[3]) / max(1, len(x[3] | y[3])) >= 0.45), None)
            if near:
                near[4] += 1
            else:
                kept.append(x)
        if not kept:
            return f"沒有跟「{q}」有關的準則或紀錄。做了之後學到什麼就 記準則({q},…)"
        n_rule = sum(1 for x in kept if x[1] == "準則")
        for x in kept[:8]:                      # 這幾條接下來 3 輪的 符合 記回去
            if x[1] == "準則":
                self.rules_watch[self._rule_key(rmap[x[2]])] = self.RULE_WATCH
                if self.small is not None:
                    seen = self.small.setdefault("查過的準則", [])
                    if self._rule_key(rmap[x[2]]) not in seen:
                        seen.append(self._rule_key(rmap[x[2]]))
                        del seen[:-10]
        return (f"跟「{q}」有關的：準則 {n_rule} 條、其他 {len(kept) - n_rule} 種（相近的已合併，×N＝同一件事記了 N 次；"
                f"準則照「查出來後照做的 ✓／✗」排，接下來 {self.RULE_WATCH} 輪的結果會記回這幾條；列前 8）：" + NL
                + NL.join(f"- [{src}{f' ×{n}' if n > 1 else ''}] {txt[:200]}" for _, src, txt, _, n in kept[:8]))

    def mem_history_find(self, 關鍵字=""):
        q = str(關鍵字).strip()
        try:
            hist = json.load(open(os.path.join(self.mdir, "目前大任務.json"), encoding="utf-8")).get("紀錄") or []
        except (OSError, ValueError):
            hist = []
        if not q:
            return f"這個大任務存了 {len(hist)} 輪；查紀錄 要給 關鍵字，例：查紀錄(咖啡機)"
        qb = self._bg(q)
        hits = [h for h in hist if q in json.dumps(h, ensure_ascii=False) or len(qb & self._bg(json.dumps(h, ensure_ascii=False))) / max(1, len(qb)) >= 0.6]
        if not hits:
            return f"這個大任務 {len(hist)} 輪裡沒有「{q}」"
        return f"「{q}」出現在 {len(hits)} 輪（列最近 6 輪）：" + NL + NL.join(
            f"- {h.get('時間', '')[:5]} 看到:{str(h.get('看到', ''))[:50]}｜用:{'、'.join(h.get('用') or [])[:80]}｜實際:{str(h.get('實際', ''))[:50]}｜符合:{h.get('符合')}"
            for h in hits[-6:])

    def rules_panel(self, seen=""):
        """面板只給指標：幾條準則＋畫面上看到你記過的東西（內容要 查準則 才叫出來）。"""
        rules = self._rules_load()
        names = {r["對象"] for r in rules}
        for l in self._mem_lines("互動物.md"):
            m = re.sub(r"^\d\d/\d\d \d\d:\d\d\s*", "", l).split("｜")[0].strip()
            if 2 <= len(m) <= 8:
                names.add(m)
        here = sorted({n for n in names if n and n in str(seen)})[:6]
        return (f"【準則】{len(rules)} 條準則＋{len(self.reviews)} 條這個大任務的檢討（不放面板；要做某件事前用 查準則(對象或情況) 叫出來，舊的輪用 查紀錄(關鍵字)）"
                + (f"｜畫面上有你記過的：{'、'.join(here)} → 要碰之前先 查準則" if here else ""))

    # ---------------- 突發狀況與工作流（2026-10-19 07:5x 使用者：「新增工具：突發狀況——出現不合預期的情況時使用，
    # 用於在突然出現不符合預期的時候可以去思考這個情況是否符合工作流的合理性」「如果符合就先加入工作流的可能事項」
    # 「連續遇到多次就新增到工作流內」）
    WF_PROMOTE = 3          # 同一件可能事項連續碰到幾次就寫進工作流

    def _wf_load(self):
        try:
            return json.load(open(os.path.join(self.mdir, "工作流.json"), encoding="utf-8"))
        except (OSError, ValueError):
            return {"工作流": [], "可能事項": []}

    def _wf_save(self, wf):
        with open(os.path.join(self.mdir, "工作流.json"), "w", encoding="utf-8") as f:
            json.dump(wf, f, ensure_ascii=False, indent=1)

    @staticmethod
    def _wf_similar(a, b):
        def bg(x):
            x = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", str(x))
            return {x[i:i + 2] for i in range(len(x) - 1)}
        A, B = bg(a), bg(b)
        return bool(A and B) and len(A & B) / len(A | B) >= 0.4

    def workflow_text(self):
        """面板用：工作流（確定的）＋可能事項（還在觀察的，附連續次數）。"""
        wf = self._wf_load()
        if not wf["工作流"] and not wf["可能事項"]:
            return ""
        out = []
        if wf["工作流"]:
            out.append("【工作流】（突發狀況連續碰到 %d 次的事項，程式寫進來的）" % self.WF_PROMOTE)
            out += [f"- {w['事項']}" for w in wf["工作流"][-12:]]
        if wf["可能事項"]:
            out.append("【工作流的可能事項】（突發狀況判斷合理、還在觀察；連續 %d 次就寫進工作流）" % self.WF_PROMOTE)
            out += [f"- {p['事項']}（遇到 {p['次數']} 次、連續 {p['連續']}）" for p in sorted(wf["可能事項"], key=lambda p: -p["次數"])[:8]]
        return NL.join(out)

    def learn_surprise(self, 情況="", 預期=""):
        """突發狀況：看現在的畫面＋最近幾步＋工作流，想這個意外在工作流裡合不合理；合理就進可能事項，連續多次就進工作流。"""
        what = str(情況).strip()
        if len(re.sub(r"\W", "", what)) < 3:
            return "突發狀況 要寫發生了什麼意外，例：突發狀況(長按E後畫面變成攝影機畫面、下方有辦理入住和出口,預期跳登記畫面)"
        im = self.grab() if hasattr(self, "grab") else None
        words = ""
        try:
            words = self.see_text()
        except Exception:  # noqa: BLE001
            pass
        big = self.big or {}
        wf = self._wf_load()
        last = list(getattr(self, "step_log", []))[-3:]
        steps = NL.join(f"- 做:{x.get('做')}｜結果應該是:{x.get('結果應該是') or '（沒寫）'}｜程式回傳:{str(x.get('回傳', ''))[:140]}" for x in last) or "（沒有）"
        o, raw = self._ask(
            self._identity() + "現在出現了你沒預期到的情況。先別急著繞開或重試，想清楚這件事在你的工作流裡合不合理。",
            f"目前的大任務：{big.get('名稱', '（沒設）')}｜成功條件：{big.get('成功條件', '')}" + NL
            + f"突發狀況：{what}" + NL + (f"你原本預期：{預期}" + NL if str(預期).strip() else "")
            + "現在畫面上讀到的字：" + (words or "（沒讀到）") + NL
            + "最後三步：" + NL + steps + NL
            + "最近幾輪：" + NL + self._recent_text(6) + NL
            + "你的工作流：" + NL + (self.workflow_text() or "（還沒有）") + NL * 2
            + "看附的現在畫面，想：這個意外到底是什麼（是障礙？是要找的目標？是換到新畫面／新模式？是遊戲事件？是你自己的角色？是程式量測的問題？），"
              "發生在工作流的哪一步、在工作流裡合不合理（合理＝照工作流做下去本來就可能遇到）。"
              '只輸出 JSON：{"是什麼":"<=40字","在哪一步":"<=30字","合理":true,"為什麼":"<=80字",'
              '"可能事項":"一句：之後遇到這情況要怎麼看待、怎麼做（合理才寫）","下一步":"工具(參數)"}',
            effort="中想", max_tokens=8000, deadline=240, image=im)
        if not o:
            self._append("突發狀況.md", f"{what}｜（沒解析出來）")
            return "突發狀況沒想出結果（沒解析出來）；先用 檢討 記下這次的意外"
        ok = o.get("合理")
        if isinstance(ok, str):
            ok = ok.strip().lower() in ("true", "是", "合理", "yes")
        line = (f"{what}｜是什麼:{o.get('是什麼', '')}｜在哪一步:{o.get('在哪一步', '')}｜合理:{'是' if ok else '否'}"
                f"｜為什麼:{o.get('為什麼', '')}｜可能事項:{o.get('可能事項', '')}｜下一步:{o.get('下一步', '')}")
        self._append("突發狀況.md", line)
        ret = (f"突發狀況想完：是什麼＝{o.get('是什麼', '')}｜在哪一步＝{o.get('在哪一步', '')}｜合理＝{'是' if ok else '否'}"
               f"｜為什麼＝{o.get('為什麼', '')}｜下一步＝{o.get('下一步', '')}")
        item = str(o.get("可能事項") or "").strip()
        if ok and item:
            hit = next((p for p in wf["可能事項"] if self._wf_similar(item + what, p["事項"] + p.get("情況", ""))), None)
            for p in wf["可能事項"]:
                if p is not hit:
                    p["連續"] = 0
            now = datetime.now().strftime("%H:%M")
            if hit:
                hit["次數"] += 1
                hit["連續"] += 1
                hit["最近"] = now
            else:
                hit = {"事項": item, "情況": what, "次數": 1, "連續": 1, "最近": now, "加入": now}
                wf["可能事項"].append(hit)
            if hit["連續"] >= self.WF_PROMOTE:
                wf["可能事項"].remove(hit)
                wf["工作流"].append({"事項": hit["事項"], "加入": now, "次數": hit["次數"]})
                ret += NL + f"⚑ 這件事連續遇到 {hit['連續']} 次 → 已寫進【工作流】：{hit['事項']}"
            else:
                ret += NL + f"合理 → 加進【工作流的可能事項】：{hit['事項']}（遇到 {hit['次數']} 次、連續 {hit['連續']}；連續 {self.WF_PROMOTE} 次就寫進工作流）"
            self._wf_save(wf)
        elif not ok:
            ret += NL + "不合理 → 沒有加進工作流；照 為什麼 想清楚，有錯就 檢討"
        ret += NL + "這次發現了什麼（某個東西是什麼、能不能做、怎麼做）就自己 記準則(對象,準則)"
        return ret

    # ---------------- 程序
    def proc_record(self, 名稱="", 用途="", 幾輪=""):
        """錄成程序：把最近 N 輪真的做過的動作串錄成一個新工具。"""
        n = int(float(幾輪 or 3))
        acts = []
        for r in list(self.rounds)[-n:]:
            acts += [a for a in r["用"] if a and not a.startswith(("檢討", "讀字", "找東西", "記", "這步", "設大", "大任務"))]
        if not acts:
            return "最近幾輪沒有可錄的動作"
        return self.tool_new(名稱, 用途 or f"最近 {n} 輪做過的串", "→".join(acts))

    # ---------------- 看
    def see_zoom(self, x="", y=""):
        import tool_agent as T
        fx, fy = T.num(x, -1), T.num(y, -1)
        if not (0 <= fx <= 1 and 0 <= fy <= 1):
            return "放大 要給 x,y（0~1）"
        im = self.grab()
        if im is None:
            return "抓不到畫面"
        W, H = im.size
        box = (max(0, int(fx * W) - 240), max(0, int(fy * H) - 135), min(W, int(fx * W) + 240), min(H, int(fy * H) + 135))
        self.extra_images.append((f"放大 ({fx:.2f},{fy:.2f})", im.crop(box)))
        return f"放大了 ({fx:.2f},{fy:.2f}) 附近，下一輪附圖給你"

    def move_two_part(self, 秒=""):
        """拆兩段：先走七成、停下來抓一張給你看，要不要走剩下三成你下一輪決定。"""
        import tool_agent as T
        d = max(0.4, min(2.0, T.num(秒, 1.0)))
        first = round(d * 0.7, 2)
        r = self.move_w(first)
        im = self.grab()
        if im is not None:
            self.extra_images.append((f"走了七成（{first} 秒）後的畫面", im))
        return f"拆兩段：先走 {first} 秒（{r}）；剩下 {round(d - first, 2)} 秒看下一輪的圖再決定要不要 走({round(d - first, 2)})"

    # ---------------- 未實作（舊版靠水平儀／座標地圖／瀏覽器的）
    def not_impl(self, *a, **k):
        return "這個工具舊版有、這版還沒做（未實作）：它靠水平儀／座標地圖／瀏覽器。先用 看四周、找東西、估距、記地點 代替"
