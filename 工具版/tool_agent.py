# -*- coding: utf-8 -*-
"""工具版代理（2026-10-15，分枝 tool-agent）。

使用者：「把它所有的技能跟任務拆成一個一個的工具，讓他自己去挑選自己要載入什麼。
全部裡面只預設載入：他是誰、他在玩什麼遊戲、他的遊戲目標（使用者給的）、他具有什麼工具。」

每一輪：
  system ＝ 身分＋遊戲＋使用者目標＋鐵律＋工具清單（一行一個）
  user   ＝ 現在的畫面＋【工具回傳】（上一輪他叫的工具回了什麼）＋【最近三輪】＋輸出格式
  他回   ＝ {"看到":"…","想":"因為:…｜所以要:…｜動作:…｜結果應該是:…","用":["前進(0.6)","讀字"]}
  程式照順序執行「用」裡的工具，結果下一輪回給他。程式不替他做任何沒點名的事。

用法：
  python 工具版/tool_agent.py --game 動物醫院 --dry-run --image <截圖> --rounds 1   # 離線考一輪
  python 工具版/tool_agent.py --game 動物醫院 --dry-run --rounds 20                  # 抓真畫面、只印不做
  python 工具版/tool_agent.py --game 動物醫院 --live --rounds 200                    # 真的按（舊版代理要先停）
"""
import argparse
import base64
import io
import json
import os
import re
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
NL = "\n"

# ---------------------------------------------------------------- 目錄與 system
def custom_path(game):
    return os.path.join(ROOT, "專案", game, "記憶", "工具版", "自訂工具.json")


def load_catalog(game=None):
    """目錄＋他自己定義的工具（新工具）＋他補的用途（補用途）。

    2026-10-15 使用者：「遇到他自己不知道或工具沒列出來的問題，他就要去思考該動用哪個工具，
    並且把它列入這個工具之後的使用名單」→ 他定義的存在 專案/<遊戲>/記憶/工具版/自訂工具.json，
    每次開機與每一輪都合併進清單。
    """
    cat = json.load(open(os.path.join(HERE, "工具目錄.json"), encoding="utf-8"))
    if not game:
        return cat
    try:
        cus = json.load(open(custom_path(game), encoding="utf-8"))
    except Exception:  # noqa: BLE001
        cus = {"新工具": [], "補用途": []}
    byname = {t["名稱"]: t for t in cat["工具"]}
    for t in cus.get("新工具", []):
        if t.get("名稱") and t["名稱"] not in byname:
            entry = {"名稱": t["名稱"], "類別": "自訂", "用途": t.get("用途", ""), "用法": t["名稱"],
                     "參數": {}, "回傳": "照做法裡的工具一個一個做、各自的回傳", "實作": "組合",
                     "做法": t.get("做法", ""), "自訂": True}
            cat["工具"].append(entry)
            byname[t["名稱"]] = entry
    for u in cus.get("補用途", []):
        t = byname.get(u.get("工具"))
        if t and u.get("情況"):
            t["用途"] = t["用途"] + f"；也用在：{u['情況']}"
    return cat


def catalog_lines(cat, 類別=None):
    out = []
    for t in cat["工具"]:
        if 類別 and t["類別"] != 類別:
            continue
        tag = "" if t["實作"] != "未實作" else "（未實作）"
        if t.get("自訂"):
            tag = "（你自己定義的）"
        out.append(f"{t['用法']}｜{t['用途']}{tag}")
    return out


def read_text(path, tail=None):
    try:
        s = open(path, encoding="utf-8").read()
    except OSError:
        return ""
    if tail:
        lines = s.splitlines()
        s = NL.join(lines[-tail:])
    return s


def build_system(game, cat):
    gdir = os.path.join(ROOT, "專案", game)
    identity = read_text(os.path.join(ROOT, "核心", "共用", "00_身分與現在的任務.md"))
    identity = re.sub(r"^---.*?---\s*", "", identity, flags=re.S).strip().replace("{game}", game)
    try:
        gj = json.load(open(os.path.join(gdir, "遊戲.json"), encoding="utf-8"))
    except Exception:  # noqa: BLE001
        gj = {}
    gname = gj.get("全名") or game
    goal = read_text(os.path.join(gdir, "記憶", "任務指示.txt")).strip() or "（使用者還沒交代目標）"
    limits = ["不准花任何錢：Robux、金幣、購買、解鎖、升級、付費復活、任何「Buy／Purchase／$」按鈕；看到付費視窗只能用 付費視窗 工具關掉"]
    if gj.get("禁止換職業"):
        limits.append("不准換職業：不點「職業」按鈕、不點護士／醫生／實習生那類選項")
    if gj.get("禁止上下轉頭"):
        limits.append("不准上下轉頭")
    limits.append("不准離開這隻遊戲去玩別的；只有卡死才用 退出重進，而且要回到同一隻遊戲")
    limits.append("點選類的頁面（選單、按鈕、面板）：**任何點擊都是兩步**——點 或 點字 只是預約，程式不會馬上點；"
                  "下一輪你會拿到放大的目標圖，必須再看一次並審核上面每一條絕對限制，確定才送 確認點(審核)，不對就 取消點。沒有確認就永遠不會點下去")
    rules = ("【絕對限制】（使用者定的，違反就是錯，沒有例外）" + NL
             + NL.join(f"{i + 1}. {l}" for i, l in enumerate(limits)) + NL
             + "「看到」只寫畫面上真的看得到的東西：物件、文字、方位、數字；不形容氣氛、不推測。" + NL
             + "所有文字欄位都是表格式短語（欄:值｜欄:值），不寫連接詞與贅字。")
    groups = ["校準", "文件", "看", "移動", "視角", "互動", "意外", "記憶", "流程", "自訂"]
    tool_txt = NL.join(f"【{g}】" + NL + NL.join("  " + l for l in catalog_lines(cat, g))
                       for g in groups if catalog_lines(cat, g))
    return (f"{identity}" + NL
            + f"遊戲：{gname}（Roblox place {gj.get('大廳place', '?')}）。詳細介紹自己叫 遊戲介紹。" + NL
            + "使用者給你的目標：" + NL + goal + NL * 2
            + rules + NL * 2
            + "你有這些工具（格式 用法｜用途）。要什麼自己叫，程式只做你點名的事，結果下一輪回給你：" + NL
            + tool_txt + NL * 2
            + "【開工先量，逐一回報】（使用者定的）每次登入／重進／切空間、或畫面大小變了，第一件事送 開工測試：程式逐項量 ①畫面尺寸 ②每度 dx ③每度 dy ④深度比例，"
              "⑤第幾人稱由你看畫面用 回報人稱 回報。五項都有才算開工完成，面板【開工數值】會一直顯示量了沒。"
              "之後每一次 視角／移動 工具的回傳都會附「預期 vs 實測」，對不上就重量；工具出來的結果要跟你腦中的一致才能繼續。" + NL
            + "【大任務】你只會帶著「目前的大任務」做事：先用 設大任務 定一個（名稱＋成功條件），"
              "做完送 大任務完成，程式會把這個任務期間的工具回傳與紀錄清空，接著做下一個（排大任務 排過的會自動接上，沒有就再 設大任務）。" + NL
            + "【遇到不知道、工具清單沒有的問題】先想：這件事該用哪個工具？"
              "既有工具做得到、只是用途沒寫這情況 → 補用途(工具,情況)，之後清單就會寫上；"
              "沒有任何工具做得到 → 新工具(名稱,用途,做法)，做法用現有工具一個接一個寫（例如「後退(0.4)→視角右(90)→讀字」），"
              "定義完它就在清單裡、下一輪就能叫。" + NL * 2
            + "每一輪只輸出一個 JSON：" + cat["輸出格式"] + NL
            + "「用」最多 3 個，照順序做；字串「名稱(參數)」或 {\"工具\":\"名稱\",\"參數\":{...}} 都可以。"
              "不知道怎麼做就先叫文件類工具看一眼，再動。")


# ---------------------------------------------------------------- 速記解析
_ONE = re.compile(r"^\s*([^\s(（]+)\s*(?:[(（]\s*(.*?)\s*[)）])?\s*$")


def parse_call(item, cat):
    """「名稱(參數)」或 dict → (名稱, {參數名: 值})；看不懂回 (None, 原字)。"""
    byname = {t["名稱"]: t for t in cat["工具"]}
    if isinstance(item, dict):
        name = str(item.get("工具") or item.get("t") or item.get("name") or "")
        params = item.get("參數") or {k: v for k, v in item.items() if k not in ("工具", "t", "name")}
        return (name if name in byname else None), (params if isinstance(params, dict) else {})
    m = _ONE.match(str(item))
    if not m:
        return None, str(item)
    name, raw = m.group(1), m.group(2)
    if name not in byname:
        return None, str(item)
    keys = list(byname[name]["參數"].keys())
    if raw and raw.strip().startswith("{"):             # 他有時寫 點字({"字":"X"})：括號裡是 JSON 就直接當參數
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict):
                return name, {k: str(v) for k, v in obj.items()}
        except json.JSONDecodeError:
            pass
    args = [x.strip() for x in re.split(r"[,，、]", raw)] if raw and raw.strip() else []
    params = {}
    if len(keys) == 1 and args:
        params[keys[0]] = raw.strip()
    else:
        for k, v in zip(keys, args):
            params[k] = v
    return name, params


def num(v, default):
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------- 工具實作
class Tools:
    def __init__(self, game, cat, live=False, image_path=None):
        self.game, self.cat, self.live = game, cat, live
        self.gdir = os.path.join(ROOT, "專案", game)
        self.mdir = os.path.join(self.gdir, "記憶", "工具版")
        os.makedirs(self.mdir, exist_ok=True)
        self.image_path = image_path
        self.extra_images = []          # 看四周／迷路 拍的圖，下一輪附上
        self.win = None
        try:
            cal = json.load(open(os.path.join(self.gdir, "記憶", "視角校準.json"), encoding="utf-8"))
            self.dx_deg = float(cal.get("水平", {}).get("每度dx") or 2.5)
            self.dy_deg = float(cal.get("垂直", {}).get("每度dy") or 3.65)
        except Exception:  # noqa: BLE001
            self.dx_deg, self.dy_deg = 2.5, 3.65
        self._ocr = None
        self._eyes = None

    # ---- 畫面
    def grab(self):
        """現在的畫面（PIL）。--image 模式永遠回同一張。"""
        from PIL import Image
        if self.image_path:
            return Image.open(self.image_path).convert("RGB")
        try:
            import mss
            from play_agent import RobloxWindow
            if self.win is None:
                self.win = RobloxWindow()
            bbox = self.win.client_bbox()
            if not bbox:
                return None
            with mss.mss() as sct:
                shot = sct.grab(bbox)
                return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        except Exception as e:  # noqa: BLE001
            print(f"   抓畫面失敗：{e}")
            return None

    @staticmethod
    def diff(a, b):
        if a is None or b is None:
            return 0.0
        ga = a.convert("L").resize((160, 90))
        gb = b.convert("L").resize((160, 90))
        pa, pb = ga.tobytes(), gb.tobytes()
        return sum(abs(x - y) for x, y in zip(pa, pb)) / len(pa)

    def _changed(self, before):
        if self.image_path:
            return "（離線模式，畫面不會變）"
        after = self.grab()
        d = self.diff(before, after)
        return f"畫面變化 {d:.0f}" + ("（沒變＝可能被擋）" if d < 2 else "")

    # ---- 文件類
    def doc_game(self, 遊戲名=""):
        g = 遊戲名 or self.game
        gdir = os.path.join(ROOT, "專案", g)
        if not os.path.isdir(gdir):
            return f"沒有「{g}」這個遊戲的專案；有的：" + "、".join(sorted(os.listdir(os.path.join(ROOT, "專案"))))
        parts = ["【遊戲.json】" + read_text(os.path.join(gdir, "遊戲.json"))[:800],
                 "【玩法摘要】" + (read_text(os.path.join(gdir, "記憶", "玩法摘要.md"), tail=30) or "（還沒有）"),
                 "【使用者交代】" + (read_text(os.path.join(gdir, "記憶", "任務指示.txt")) or "（沒有）")]
        return NL.join(parts)

    def doc_read(self, 名稱=""):
        files = []
        for sub in ("共用", "基礎操作", "學習模組"):
            d = os.path.join(ROOT, "核心", sub)
            files += [os.path.join(d, f) for f in sorted(os.listdir(d)) if f.endswith(".md")]
        key = str(名稱).strip()
        hit = [p for p in files if key and key in os.path.basename(p)]
        if not hit:
            names = "、".join(re.sub(r"^\d+_|\.md$", "", os.path.basename(p)) for p in files)
            return f"沒有叫「{key}」的文件。有的：{names}"
        body = read_text(hit[0])
        body = re.sub(r"^---.*?---\s*", "", body, flags=re.S)
        return f"【{os.path.basename(hit[0])}】" + NL + body[:6000]

    def doc_memory(self, 類別=""):
        m = os.path.join(self.gdir, "記憶")
        table = {"玩法摘要": "玩法摘要.md", "成功做法": "成功檢討.md", "卡住檢討": "卡住檢討.md", "死亡檢討": "死亡檢討.md",
                 "操作手冊": "操作手冊.md", "遇到的問題": "遇到的問題.md", "因果": "因果.md", "學到的": "學到的.md",
                 "地圖": "研究筆記.md", "筆記": os.path.join("工具版", "筆記.md")}
        key = str(類別).strip()
        f = next((v for k, v in table.items() if key and key in k), None)
        if not f:
            return "沒有這一類。有的：" + "、".join(table)
        body = read_text(os.path.join(m, f), tail=60)
        return f"【{key}】" + NL + (body or "（還是空的）")

    def doc_tool(self, 名稱=""):
        t = next((t for t in self.cat["工具"] if t["名稱"] == str(名稱).strip()), None)
        if not t:
            return "沒有這個工具。叫 工具清單 看有哪些。"
        return json.dumps({k: t[k] for k in ("名稱", "用途", "用法", "參數", "回傳")}, ensure_ascii=False)

    def doc_list(self, 類別=""):
        return NL.join(catalog_lines(self.cat, str(類別).strip() or None)) or "沒有這一類"

    # ---- 看
    def see_text(self):
        im = self.grab()
        if im is None:
            return "抓不到畫面"
        try:
            import local_text
            res = local_text._win_read(im)
        except Exception as e:  # noqa: BLE001
            return f"OCR 不可用：{e}"
        W, H = im.size
        out = []
        for r in (res or []):
            try:
                box, txt = r[0], str(r[1]).strip()          # local_text._win_read：[(box, text, score)]，box 是像素四角
                if not txt:
                    continue
                xs = [p[0] for p in box]
                ys = [p[1] for p in box]
                cx, cy = (min(xs) + max(xs)) / 2 / W, (min(ys) + max(ys)) / 2 / H
                out.append(f"「{txt}」({cx:.2f},{cy:.2f})")
            except Exception:  # noqa: BLE001
                continue
        return "、".join(out[:40]) or "畫面上沒讀到字"

    def see_find(self, 標籤=""):
        labels = [x.strip() for x in re.split(r"[,，、]", str(標籤)) if x.strip()]
        if not labels:
            return "要給英文標籤，例如 找東西(door,table)"
        im = self.grab()
        if im is None:
            return "抓不到畫面"
        try:
            if self._eyes is None:
                from local_vision import Eyes
                self._eyes = Eyes()
                self._eyes.load()
            self._eyes.watch(labels)
            dets = self._eyes._detect(im, labels)
        except Exception as e:  # noqa: BLE001
            return f"眼睛不可用：{e}"
        if not dets:
            return "沒框到：" + "、".join(labels)
        out = []
        for lab, ds in dets.items():
            d = ds[0]
            out.append(f"{lab}：中心({d['cx']:.2f},{d['cy']:.2f}) 高{d['h']:.2f} 信心{d['score']}")
        return "；".join(out)

    def see_around(self):
        if self.image_path:
            return "離線模式，看四周沒有新畫面"
        shots = []
        for i in range(4):
            self.look_right(90)
            time.sleep(0.4)
            im = self.grab()
            if im is not None:
                shots.append((f"右轉 {90 * (i + 1)} 度", im))
        self.extra_images = shots
        return "拍了 4 張（右轉 90／180／270／360 度），下一輪附給你；方向已轉回原點"

    # ---- 移動
    def _key(self, k, d):
        import pydirectinput
        pydirectinput.keyDown(k)
        time.sleep(d)
        pydirectinput.keyUp(k)

    def _move(self, k, d, lo, hi):
        d = max(lo, min(hi, d))
        before = self.grab()
        if self.live:
            self._key(k, d)
        else:
            print(f"   [dry-run] 按 {k} {d:.1f}s")
        return f"按 {k} {d:.1f} 秒；" + self._changed(before)

    def move_w(self, 秒=""):
        return self._move("w", num(秒, 0.5), 0.2, 2.0)

    def move_s(self, 秒=""):
        return self._move("s", num(秒, 0.4), 0.2, 1.0)

    def move_a(self, 秒=""):
        return self._move("a", num(秒, 0.5), 0.2, 2.0)

    def move_d(self, 秒=""):
        return self._move("d", num(秒, 0.5), 0.2, 2.0)

    def move_jump(self):
        return self._move("space", 0.1, 0.1, 0.1)

    # ---- 視角
    def _drag(self, dx, dy):
        """壓右鍵拖曳（跟舊版同一套）；一次最多 220 像素，分段。"""
        import pydirectinput
        if not self.live:
            print(f"   [dry-run] 轉視角 dx {dx:.0f} dy {dy:.0f}")
            return
        bbox = self.win.client_bbox() if self.win else None
        cx = (bbox["left"] + bbox["width"] // 2) if bbox else 960
        cy = (bbox["top"] + bbox["height"] // 2) if bbox else 500
        rest_x, rest_y = int(dx), int(dy)
        while rest_x or rest_y:
            sx = max(-220, min(220, rest_x))
            sy = max(-120, min(120, rest_y))
            rest_x -= sx
            rest_y -= sy
            pydirectinput.moveTo(cx, cy)
            time.sleep(0.03)
            pydirectinput.mouseDown(button="right")
            n = 8
            done_x = done_y = 0
            for i in range(1, n + 1):
                tx, ty = sx * i // n, sy * i // n
                pydirectinput.moveRel(tx - done_x, ty - done_y, relative=True)
                done_x, done_y = tx, ty
                time.sleep(0.02)
            pydirectinput.mouseUp(button="right")
            pydirectinput.moveTo(cx, cy)
            time.sleep(0.08)

    # ---- 實測（2026-10-15 使用者：他要能確保工具出來的結果跟他腦中的一致 → 每次轉視角都量畫面實際位移）
    @staticmethod
    def shift_x(a, b):
        """兩張圖的水平位移（b 相對 a），回傳佔畫面寬的比例（右轉鏡頭＝景物往左＝負）。"""
        if a is None or b is None:
            return None
        ga = a.convert("L").resize((320, 180))
        gb = b.convert("L").resize((320, 180))
        pa, pb = ga.tobytes(), gb.tobytes()
        W, H = 320, 180
        def col(p, x):
            return sum(p[y * W + x] for y in range(0, H, 6))
        ca = [col(pa, x) for x in range(W)]
        cb = [col(pb, x) for x in range(W)]
        best, best_s = None, 0
        for s in range(-150, 151, 2):
            n = 0
            tot = 0
            for x in range(0, W, 2):
                xx = x + s
                if 0 <= xx < W:
                    tot += abs(ca[x] - cb[xx])
                    n += 1
            if n < 60:
                continue
            v = tot / n
            if best is None or v < best:
                best, best_s = v, s
        return best_s / W          # 景物往左移＝負（右轉鏡頭）

    def _look(self, deg, axis, sign, lo, hi, default):
        deg = max(lo, min(hi, num(deg, default)))
        px = deg * (self.dx_deg if axis == "x" else self.dy_deg) * sign
        before = self.grab()
        self._drag(px if axis == "x" else 0, px if axis == "y" else 0)
        if self.image_path or not self.live:
            return f"轉了 {deg:.0f} 度（{'dx' if axis == 'x' else 'dy'} {px:.0f}）"
        time.sleep(0.25)
        after = self.grab()
        d = self.diff(before, after)
        if axis == "x":
            sh = self.shift_x(before, after)
            sh_txt = f"畫面景物實測往{'左' if (sh or 0) < 0 else '右'}移了 {abs(sh or 0) * 100:.0f}% 畫面寬" if sh is not None else ""
            try:
                self._append("刻度樣本.md", f"dx {px:.0f}｜預期 {deg:.0f} 度｜實測位移 {abs(sh or 0) * 100:.0f}%｜變化 {d:.0f}")
            except Exception:  # noqa: BLE001
                pass
            return (f"預期：轉 {deg:.0f} 度（dx {px:.0f}，照刻度每度 {self.dx_deg}）→ 實測：{sh_txt}、畫面變化 {d:.0f}。"
                    "跟你腦中的對不上就 量刻度 重量")
        return (f"預期：轉 {deg:.0f} 度（dy {px:.0f}，照刻度每度 {self.dy_deg}）→ 實測：畫面變化 {d:.0f}"
                + ("（幾乎沒變＝可能已經頂到上下限）" if d < 2 else "") + "。對不上就 量刻度 重量")

    # ---- 校準（2026-10-15 使用者：他一開始還是需要量測所有的數據，確保工具出來的結果和他腦中的一致；
    #        「還是要按照之前我要求的幾個數值逐一回報」：畫面尺寸、每度 dx、每度 dy、深度比例、第幾人稱）
    startup = {}      # 開工數值：畫面／每度dx／每度dy／深度比例／人稱／時間

    def startup_text(self):
        s = self.startup
        if not s:
            return "還沒量。登入／重進第一件事：開工測試（程式逐項量：①畫面尺寸 ②每度 dx ③每度 dy ④深度比例；⑤人稱你看畫面回報）"
        return (f"①畫面 {s.get('畫面', '?')}｜②每度 dx {s.get('每度dx', '?')}｜③每度 dy {s.get('每度dy', '?')}"
                f"｜④深度比例 {s.get('深度比例', '?')}｜⑤人稱 {s.get('人稱', '（你還沒回報，用 回報人稱(第幾人稱)）')}｜量於 {s.get('時間', '?')}")

    @staticmethod
    def zoom_factor(a, b):
        """走一步之後畫面中央放大了幾倍（深度比例）：拿 a 的中央區放大 k 倍跟 b 比，挑最像的 k。"""
        if a is None or b is None:
            return None
        from PIL import Image
        ga = a.convert("L").resize((320, 180))
        gb = b.convert("L").resize((320, 180))
        W, H = 320, 180
        pb = gb.crop((80, 45, 240, 135)).tobytes()
        best, best_k = None, 1.0
        for k10 in range(10, 21):
            k = k10 / 10.0
            w, h = 160 / k, 90 / k
            box = (int(160 - w / 2), int(90 - h / 2), int(160 + w / 2), int(90 + h / 2))
            pa = ga.crop(box).resize((160, 90), Image.BILINEAR).tobytes()
            v = sum(abs(x - y) for x, y in zip(pa, pb)) / len(pb)
            if best is None or v < best:
                best, best_k = v, k
        return best_k

    def cal_depth(self):
        if self.image_path or not self.live:
            return "離線／dry-run 不能量比例"
        before = self.grab()
        self._key("w", 1.0)
        time.sleep(0.3)
        after = self.grab()
        d = self.diff(before, after)
        if d < 2:
            return "走 1 秒畫面沒變＝前方被擋，換個有空間的地方再量比例"
        k = self.zoom_factor(before, after)
        self.startup["深度比例"] = f"走1秒中央放大{k:.1f}倍"
        p = os.path.join(self.gdir, "記憶", "視角校準.json")
        try:
            cal = json.load(open(p, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            cal = {}
        cal["深度"] = {"走1秒放大倍數": k, "方法": "中央區放大比對（工具版 量比例）", "時間": datetime.now().strftime("%m/%d %H:%M")}
        with open(p, "w", encoding="utf-8") as f:
            json.dump(cal, f, ensure_ascii=False, indent=1)
        return f"深度比例：走 1 秒畫面中央放大 {k:.1f} 倍（畫面變化 {d:.0f}）。之後估「前方幾步」用這個換算"

    def cal_startup(self):
        """開工測試：逐項量、逐項回報（使用者要求的幾個數值）。"""
        if self.image_path or not self.live:
            return "離線／dry-run 不能開工測試"
        im = self.grab()
        r1 = self.cal_screen()
        self.startup["畫面"] = f"{im.width}×{im.height}" if im is not None else "?"
        r2 = self.cal_scale()
        self.startup["每度dx"], self.startup["每度dy"] = self.dx_deg, self.dy_deg
        r3 = self.cal_depth()
        self.startup["時間"] = datetime.now().strftime("%H:%M")
        self.startup.pop("人稱", None)
        line = f"🧪 開工測試：{self.startup_text()}"
        print("   " + line)
        self._append("開工數值.md", self.startup_text())
        return ("開工測試逐項回報：" + NL
                + f"① 畫面尺寸：{self.startup['畫面']}（{r1}）" + NL
                + f"② 每度 dx：{self.dx_deg}；③ 每度 dy：{self.dy_deg}（{r2}）" + NL
                + f"④ 深度比例：{self.startup.get('深度比例', '沒量到')}（{r3}）" + NL
                + "⑤ 第幾人稱：你看現在的畫面回報——看得到自己背影＝第三人稱，畫面中央有準心＝第一人稱 → 送 回報人稱(第幾人稱)。"
                  "五項都有了才算開工完成；之後每次視角／移動回傳的實測跟這些對不上就重量")

    def cal_persp(self, 人稱=""):
        v = str(人稱).strip()
        if not v:
            return "要寫 第一人稱 或 第三人稱"
        self.startup["人稱"] = v
        self._append("開工數值.md", f"人稱:{v}")
        print(f"   🧪 人稱回報：{v}｜開工數值：{self.startup_text()}")
        return f"記下：{v}。開工數值現在：{self.startup_text()}"

    def cal_screen(self):
        bbox = None
        try:
            from play_agent import RobloxWindow
            if self.win is None:
                self.win = RobloxWindow()
            bbox = self.win.client_bbox()
        except Exception:  # noqa: BLE001
            pass
        im = self.grab()
        size = f"{im.width}×{im.height}" if im is not None else "抓不到"
        return (f"畫面 {size}（視窗 {bbox}）｜目前刻度：每度 dx {self.dx_deg}、每度 dy {self.dy_deg}"
                f"｜來源 記憶/視角校準.json｜轉 90 度＝dx {90 * self.dx_deg:.0f}、抬頭 30 度＝dy {30 * self.dy_deg:.0f}")

    def cal_scale(self):
        """程式帶他量：水平一圈（轉到畫面回起點）→ 每度 dx；垂直掃到頂、掃到底 → 每度 dy。寫進 視角校準.json。"""
        if self.image_path or not self.live:
            return "離線／dry-run 不能量刻度"
        start = self.grab()
        if start is None:
            return "抓不到畫面"
        # 水平：每步 dx 60，轉到跟起點最像的位置
        step, total, best, best_total, diffs = 60, 0, None, None, []
        for i in range(1, 41):
            self._drag(step, 0)
            total += step
            time.sleep(0.2)
            cur = self.grab()
            d = self.diff(start, cur)
            diffs.append(d)
            if i >= 6 and (best is None or d < best):
                best, best_total = d, total
            if i >= 8 and d < 6 and d <= min(diffs[:-1]) * 0.6:
                break
            if total >= 1500:
                break
        loop_dx = best_total or total
        dx_deg = round(loop_dx / 360.0, 2)
        # 垂直：先往上掃到頂（畫面不再變），再往下掃到底，算全程
        up = 0
        for _ in range(14):
            b = self.grab()
            self._drag(0, -80)
            time.sleep(0.15)
            if self.diff(b, self.grab()) < 1.5:
                break
            up += 80
        down = 0
        for _ in range(20):
            b = self.grab()
            self._drag(0, 80)
            time.sleep(0.15)
            if self.diff(b, self.grab()) < 1.5:
                break
            down += 80
        full_dy = max(down, 80)
        dy_deg = round(full_dy / 178.0, 2)
        self._drag(0, -full_dy // 2)        # 回到中間
        self.dx_deg, self.dy_deg = dx_deg, dy_deg
        p = os.path.join(self.gdir, "記憶", "視角校準.json")
        try:
            cal = json.load(open(p, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            cal = {}
        now = datetime.now().strftime("%m/%d %H:%M")
        cal["水平"] = {"一圈dx": loop_dx, "每度dx": dx_deg, "方法": "轉到畫面回起點（工具版 量刻度）", "時間": now}
        cal["垂直"] = {"全程dy": full_dy, "每度dy": dy_deg, "方法": "機械掃描（工具版 量刻度）", "時間": now}
        cal["視窗"] = {"寬": start.width, "高": start.height, "時間": now}
        with open(p, "w", encoding="utf-8") as f:
            json.dump(cal, f, ensure_ascii=False, indent=1)
        self._append("大任務.md", f"量刻度：一圈 dx {loop_dx}（每度 {dx_deg}）、全程 dy {full_dy}（每度 {dy_deg}）")
        return (f"量好了：水平一圈 dx {loop_dx} → 每度 dx {dx_deg}；垂直全程 dy {full_dy} → 每度 dy {dy_deg}；"
                f"已寫進 視角校準.json，之後 視角左(90) 會送 dx {90 * dx_deg:.0f}。鏡頭已轉回起點、上下回到中間。"
                "接著用 視角右(30) 之類試一次，看回傳的實測跟你預期一不一致")

    def look_left(self, 度=""):
        return self._look(度, "x", -1, 5, 180, 30)

    def look_right(self, 度=""):
        return self._look(度, "x", +1, 5, 180, 30)

    def look_up(self, 度=""):
        return self._look(度, "y", -1, 5, 60, 15)

    def look_down(self, 度=""):
        return self._look(度, "y", +1, 5, 60, 15)

    def look_180(self):
        for _ in range(4):
            self._look(45, "x", +1, 45, 45, 45)
            time.sleep(0.15)
        return "轉了 180 度（分四段各 45 度），現在看的是原本的背後；新畫面下一輪給你"

    # ---- 互動
    def key_press(self, 鍵="", 秒=""):
        k = str(鍵).strip().lower() or "e"
        return self._move(k, num(秒, 0.15), 0.05, 2.0)

    def key_hold(self, 鍵="", 秒=""):
        k = str(鍵).strip().lower() or "e"
        return self._move(k, num(秒, 1.5), 0.5, 4.0)

    # ---- 點擊兩步（2026-10-15 01:0x 使用者：點選類的頁面，無論要點什麼都必須再檢查一次才能點，包含絕對禁忌的審核）
    TABOO = ("robux", "buy", "purchase", "$", "購買", "解鎖", "升級", "復活", "付費", "職業", "護士", "醫生", "實習")
    pending_click = None

    def _near_text(self, fx, fy):
        txt = self.see_text()
        near = []
        for m in re.finditer(r"「([^」]*)」\(([\d.]+),([\d.]+)\)", txt):
            if abs(float(m.group(2)) - fx) < 0.12 and abs(float(m.group(3)) - fy) < 0.06:
                near.append(m.group(1))
        return near

    def click_xy(self, x="", y="", 字=""):
        fx, fy = num(x, -1), num(y, -1)
        if not (0 <= fx <= 1 and 0 <= fy <= 1):
            return "座標要 0~1"
        near = self._near_text(fx, fy)
        hit = [w for w in self.TABOO if any(w in n.lower() for n in near)]
        if hit:
            self.pending_click = None
            return f"拒絕預約：目標附近的字 {near} 碰到絕對限制（{'、'.join(hit)}）。不點。"
        im = self.grab()
        if im is not None:
            W, H = im.size
            box = (max(0, int(fx * W) - 220), max(0, int(fy * H) - 90), min(W, int(fx * W) + 220), min(H, int(fy * H) + 90))
            self.extra_images = [(f"預約要點的位置 ({fx:.2f},{fy:.2f}) 放大圖", im.crop(box))]
        self.pending_click = {"x": fx, "y": fy, "字": str(字 or ""), "附近的字": near, "時間": time.time()}
        return (f"已預約點 ({fx:.2f},{fy:.2f}){'「' + str(字) + '」' if 字 else ''}；附近的字：{near or '無'}。"
                "**還沒點**。下一輪看放大圖，逐條審核絕對限制（不花錢／不換職業／不離開），確定才送 確認點(審核)，不對就 取消點")

    def click_confirm(self, 審核=""):
        p = self.pending_click
        if not p:
            return "沒有預約中的點擊。先用 點 或 點字 預約"
        chk = str(審核).strip()
        if not chk or chk in ("審核", "審核結果") or len(chk) < 6:
            return "確認點 要寫真的審核內容，逐條：不花錢｜不換職業｜不離開｜這是什麼按鈕。不能照抄「審核」兩個字"
        if time.time() - p["時間"] > 180:
            self.pending_click = None
            return "預約超過 3 分鐘，畫面可能變了，取消；要點就重新預約"
        self._append("點擊審核.md", f"點({p['x']:.2f},{p['y']:.2f}){p['字']}｜附近:{p['附近的字']}｜審核:{審核}")
        self.pending_click = None
        return "確認通過 → " + self._do_click(p["x"], p["y"])

    def click_cancel(self):
        self.pending_click = None
        return "取消了預約的點擊"

    def _do_click(self, fx, fy):
        before = self.grab()
        if self.live:
            import pydirectinput
            bbox = self.win.client_bbox() if self.win else None
            if not bbox:
                return "找不到 Roblox 視窗"
            pydirectinput.moveTo(int(bbox["left"] + fx * bbox["width"]), int(bbox["top"] + fy * bbox["height"]))
            time.sleep(0.05)
            pydirectinput.mouseDown()
            time.sleep(0.08)
            pydirectinput.mouseUp()
        else:
            print(f"   [dry-run] 點 ({fx:.2f},{fy:.2f})")
        return f"點了 ({fx:.2f},{fy:.2f})；" + self._changed(before)

    def click_text(self, 字=""):
        key = str(字).strip()
        txt = self.see_text()
        m = re.search(r"「([^」]*" + re.escape(key) + r"[^」]*)」\(([\d.]+),([\d.]+)\)", txt)
        if not m:
            return f"畫面上沒有「{key}」。讀到的字：{txt[:200]}"
        return f"找到「{m.group(1)}」→ " + self.click_xy(m.group(2), m.group(3), 字=m.group(1))

    def scroll(self, 格=""):
        n = int(num(格, -3))
        n = max(-5, min(5, n))
        if self.live:
            import pydirectinput
            pydirectinput.scroll(n)
        else:
            print(f"   [dry-run] 捲 {n}")
        return f"捲了 {n} 格"

    def wait(self, 秒=""):
        d = max(0.2, min(5.0, num(秒, 1.0)))
        if self.live:
            time.sleep(d)
        return f"等了 {d:.1f} 秒"

    # ---- 意外
    def fix_stuck(self):
        a = self.move_s(0.4)
        b = self.move_d(0.5)
        c = self.look_180()
        return "脫困：" + a + "；" + b + "；" + c

    def fix_panel(self):
        before = self.grab()
        r = self.key_press("esc", 0.1)
        if self.image_path:
            return "關掉面板：按了 esc（離線看不出有沒有關掉）"
        after = self.grab()
        if self.diff(before, after) < 2:
            self.scroll(-3)
            return "按 esc 畫面沒變，已捲 -3 拉遠；面板還在就用 點字 點它的叉叉或「關閉」"
        return "關掉面板：" + r

    def fix_paywall(self):
        return "付費視窗：" + self.key_press("esc", 0.1) + "（鐵律：不花錢）"

    def fix_rejoin(self):
        self.key_press("esc", 0.1)
        time.sleep(0.5 if self.live else 0)
        self.key_press("l", 0.1)
        time.sleep(0.5 if self.live else 0)
        self.key_press("enter", 0.1)
        if self.live:
            time.sleep(8)
        return "退出重進：esc→l→enter，等了 8 秒；新畫面下一輪給你"

    def fix_respawn(self):
        if self.live:
            time.sleep(5)
        for w in ("重生", "繼續", "Respawn", "Continue"):
            r = self.click_text(w)
            if r.startswith("找到"):
                return "死亡重生：" + r + "（一樣要 確認點 才會點）"
        return "死亡重生：等了 5 秒，畫面上沒有重生／繼續的字；讀字看看"

    def fix_lost(self):
        a = self.see_around()
        b = self.doc_memory("地圖")
        return "迷路：" + a + NL + b

    # ---- 記憶
    def _append(self, fname, line):
        p = os.path.join(self.mdir, fname)
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"- {datetime.now().strftime('%m/%d %H:%M')} {line}" + NL)
        return f"存到 記憶/工具版/{fname}"

    def mem_note(self, 內容=""):
        return self._append("筆記.md", str(內容))

    def mem_place(self, 名稱="", 樣子=""):
        return self._append("地點.md", f"{名稱}｜{樣子}")

    def mem_cause(self, 做了="", 發生=""):
        return self._append("因果.md", f"做了:{做了}｜發生:{發生}")

    def mem_stuck(self, 情況="", 解法=""):
        return self._append("卡法.md", f"情況:{情況}｜解法:{解法}")

    def mem_question(self, 問題=""):
        p = os.path.join(self.gdir, "記憶", "遇到的問題.md")
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"- {datetime.now().strftime('%m/%d %H:%M')}（工具版）{問題}" + NL)
        return "存到 記憶/遇到的問題.md（使用者會看）"

    # ---- 流程
    def flow_step_done(self, 做了什麼=""):
        return self._append("步驟.md", f"做完:{做了什麼}")

    def flow_task_done(self, 任務=""):
        return self._append("任務.md", f"完成:{任務}")

    def flow_give_up(self, 為什麼=""):
        return self._append("放棄.md", f"放棄:{為什麼}")

    # ---- 大任務（2026-10-15 使用者：每次導入的只有前面那些加上目前的大任務，執行完再清空接下一個）
    big = None            # {"名稱","成功條件","開始輪"}
    big_queue = []
    clear_flag = False    # 主迴圈看到就把這個任務期間的紀錄清掉

    def flow_big_set(self, 名稱="", 成功條件=""):
        if not str(名稱).strip():
            return "要給名稱"
        self.big = {"名稱": str(名稱).strip(), "成功條件": str(成功條件).strip(), "開始": datetime.now().strftime("%H:%M")}
        self._append("大任務.md", f"開始:{self.big['名稱']}｜成功條件:{self.big['成功條件']}")
        return f"目前的大任務＝「{self.big['名稱']}」（成功條件：{self.big['成功條件'] or '沒寫'}）"

    def flow_big_queue(self, 名稱=""):
        names = [x.strip() for x in re.split(r"[,，、→]", str(名稱)) if x.strip()]
        self.big_queue += names
        return "排好了：" + " → ".join(self.big_queue)

    def flow_big_done(self, 結果=""):
        name = self.big["名稱"] if self.big else "（沒設大任務）"
        self._append("大任務.md", f"完成:{name}｜結果:{結果}")
        self.clear_flag = True
        if self.big_queue:
            nxt = self.big_queue.pop(0)
            self.big = {"名稱": nxt, "成功條件": "", "開始": datetime.now().strftime("%H:%M")}
            return f"「{name}」完成。這個任務的紀錄已清空。下一個大任務＝「{nxt}」（成功條件自己用 設大任務 補）"
        self.big = None
        return f"「{name}」完成。紀錄已清空。還沒有下一個，用 設大任務 定一個。"

    # ---- 自訂工具（2026-10-15 使用者：工具沒列出來的問題 → 想該用哪個工具，列入之後的使用名單）
    def _load_custom(self):
        try:
            return json.load(open(custom_path(self.game), encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {"新工具": [], "補用途": []}

    def _save_custom(self, cus):
        with open(custom_path(self.game), "w", encoding="utf-8") as f:
            json.dump(cus, f, ensure_ascii=False, indent=1)
        self.cat = load_catalog(self.game)       # 下一輪清單就有

    def tool_new(self, 名稱="", 用途="", 做法=""):
        name = str(名稱).strip()
        if not name or not str(做法).strip():
            return "要給 名稱、用途、做法（做法＝現有工具用 → 接起來）"
        if any(t["名稱"] == name for t in self.cat["工具"]):
            return f"已經有「{name}」了；要補情況用 補用途"
        steps = [s.strip() for s in re.split(r"[→>；;]", str(做法)) if s.strip()]
        bad = [s for s in steps if parse_call(s, self.cat)[0] is None]
        if bad:
            return "做法裡這些不是現有工具：" + "、".join(bad) + "。做法只能用清單上的工具。"
        cus = self._load_custom()
        cus.setdefault("新工具", []).append({"名稱": name, "用途": str(用途).strip(), "做法": "→".join(steps),
                                            "時間": datetime.now().strftime("%m/%d %H:%M")})
        self._save_custom(cus)
        return f"新工具「{name}」定義好了（用途：{用途}；做法：{'→'.join(steps)}），已列入清單，下一輪就能叫"

    def tool_add_use(self, 工具="", 情況=""):
        name = str(工具).strip()
        if not any(t["名稱"] == name for t in self.cat["工具"]):
            return f"沒有「{name}」這個工具"
        if not str(情況).strip():
            return "要寫情況"
        cus = self._load_custom()
        cus.setdefault("補用途", []).append({"工具": name, "情況": str(情況).strip(),
                                            "時間": datetime.now().strftime("%m/%d %H:%M")})
        self._save_custom(cus)
        return f"「{name}」的用途補上：{情況}。清單下一輪就會寫上"

    def run_compound(self, t):
        steps = [s for s in str(t.get("做法", "")).split("→") if s.strip()]
        outs = []
        for s in steps:
            name, params = parse_call(s, self.cat)
            if not name:
                outs.append(f"{s}→看不懂")
                continue
            outs.append(f"{s}→{self.run(name, params)}")
        return f"「{t['名稱']}」：" + "；".join(outs)

    # ---- 派工
    def run(self, name, params):
        t = next((t for t in self.cat["工具"] if t["名稱"] == name), None)
        if not t:
            return f"沒有「{name}」這個工具"
        impl = t["實作"]
        if impl == "組合":
            return self.run_compound(t)
        if impl == "未實作":
            return f"「{name}」目錄有列、程式還沒做（未實作）。先用別的工具。"
        fn = getattr(self, impl, None)
        if fn is None:
            return f"「{name}」的實作 {impl} 找不到"
        try:
            return str(fn(**{k: v for k, v in params.items() if k in t["參數"]}))
        except TypeError:
            return str(fn())
        except Exception as e:  # noqa: BLE001
            return f"「{name}」執行失敗：{e}"


# ---------------------------------------------------------------- 主迴圈
def repair_json(raw):
    """他兩種常見的壞 JSON（01:3x 統計 11/120 輪）：
    1. 「想」欄寫成 …｜結果應該是": "畫面會變…"  → 引號提早關掉；把 結果應該是": " 改回 結果應該是:
    2. 一輪吐兩個 JSON 物件 → 取最後一個完整的。"""
    s = str(raw or "")
    s = re.sub(r'結果應該是"\s*:\s*"', "結果應該是:", s)
    objs = re.findall(r"\{.*?\}(?=\s*\{|\s*$)", s, flags=re.S)
    if len(objs) > 1:
        for cand in reversed(objs):
            try:
                json.loads(cand)
                return cand
            except json.JSONDecodeError:
                continue
    return s


def img_uri(im, width=1024):
    im = im.resize((width, int(width * im.height / im.width)))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=72)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def detect_model(endpoint):
    import urllib.request
    try:
        with urllib.request.urlopen(endpoint.rstrip("/") + "/models", timeout=8) as r:
            return json.load(r)["data"][0]["id"]
    except Exception as e:  # noqa: BLE001
        sys.exit(f"8888 沒回應，沒有大腦：{e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", default="動物醫院")
    ap.add_argument("--rounds", type=int, default=1)
    ap.add_argument("--dry-run", action="store_true", help="只印不做（預設）")
    ap.add_argument("--live", action="store_true", help="真的按鍵盤滑鼠（舊版代理要先停）")
    ap.add_argument("--image", default="", help="離線：用這張截圖當畫面")
    ap.add_argument("--endpoint", default="http://192.168.0.126:8888/v1")
    ap.add_argument("--deadline", type=float, default=90.0)
    ap.add_argument("--max-tokens", type=int, default=900)
    args = ap.parse_args()
    live = bool(args.live) and not args.image
    from play_agent import stream_json, parse_json_loose, LAST_REASONING

    cat = load_catalog(args.game)
    tools = Tools(args.game, cat, live=live, image_path=args.image or None)
    model = detect_model(args.endpoint)
    system = build_system(args.game, cat)
    log_path = os.path.join(tools.mdir, datetime.now().strftime("%Y-%m-%d") + ".jsonl")
    print(f"[工具版] 遊戲 {args.game}｜大腦 {model}｜{'LIVE 真的按' if live else 'dry-run 只印'}｜system {len(system)} 字｜工具 {len(cat['工具'])} 個")
    print(f"   紀錄 {log_path}")

    history = []        # 這個大任務期間的輪次摘要（大任務完成就清空）
    tool_returns = ""   # 上一輪工具回傳
    task_round = 0
    for rnd in range(1, args.rounds + 1):
        if os.path.exists(os.path.join(ROOT, "stop.txt")):          # 停止：worktree 根目錄放 stop.txt
            print("[stop.txt] 結束")
            break
        cat = tools.cat                                   # 他定義的新工具／補用途會讓清單變
        system = build_system(args.game, cat)
        im = tools.grab()
        if im is None:
            print(f"[{rnd}] 抓不到畫面（Roblox 不在前景？），等 3 秒")
            time.sleep(3)
            continue
        content = [{"type": "image_url", "image_url": {"url": img_uri(im)}}]
        for tag, shot in tools.extra_images:
            content.append({"type": "text", "text": f"（看四周：{tag}）"})
            content.append({"type": "image_url", "image_url": {"url": img_uri(shot, 640)}})
        tools.extra_images = []
        task_round += 1
        big = tools.big
        big_txt = (f"「{big['名稱']}」｜成功條件：{big['成功條件'] or '（沒寫）'}｜{big['開始']} 起、第 {task_round} 輪"
                   if big else "（還沒設。先用 設大任務(名稱,成功條件) 定一個，再做事）")
        panel = ("【開工數值】" + tools.startup_text() + NL
                 + "【目前的大任務】" + big_txt + NL * 2
                 + "【工具回傳】（上一輪你叫的工具）" + NL + (tool_returns or "（上一輪沒叫工具）") + NL * 2
                 + "【這個大任務到目前為止】" + NL + (NL.join(history[-6:]) or "（剛開始）") + NL * 2
                 + "看畫面，決定這一輪要用哪些工具。只輸出 JSON：" + cat["輸出格式"])
        content.append({"type": "text", "text": panel})
        t0 = time.time()
        raw, trunc, ttft, _ = stream_json(args.endpoint, model, system, content, args.max_tokens,
                                          time.time() + args.deadline, 0.2, effort="快思推理")
        dt = time.time() - t0
        reason = LAST_REASONING.get("text", "")
        LAST_REASONING["text"] = ""
        o = parse_json_loose(repair_json(raw)) or {}
        seen, think = str(o.get("看到") or o.get("s") or "")[:60], str(o.get("想") or "")[:200]
        calls = o.get("用") or o.get("a") or []
        if isinstance(calls, str):
            calls = [calls]
        print(f"[{rnd}] {dt:.1f}s 看到「{seen}」想「{think}」")
        rets, did = [], []
        for item in list(calls)[:3]:
            name, params = parse_call(item, cat)
            if not name:
                rets.append(f"✗ 看不懂「{params}」（工具名要照清單）")
                continue
            r = tools.run(name, params)
            did.append(name + (f"({json.dumps(params, ensure_ascii=False)})" if params else ""))
            rets.append(f"● {name}{'(' + ','.join(str(v) for v in params.values()) + ')' if params else ''} → {r}")
            print(f"   ▶ {name} {params} → {r[:120].replace(NL, ' ')}")
        tool_returns = NL.join(rets)
        history.append(f"[{rnd}] 看到:{seen}｜想:{think[:80]}｜用:{'、'.join(did) or '無'}")
        if tools.clear_flag:                              # 大任務完成：這個任務期間的紀錄全部清空
            tools.clear_flag = False
            history, task_round = [], 0
            tool_returns = "（上一個大任務已完成、紀錄清空）" + NL + tool_returns
            print("   🧹 大任務完成，紀錄清空")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"時間": datetime.now().strftime("%H:%M:%S"), "輪": rnd, "秒": round(dt, 1),
                                "看到": seen, "想": think, "用": calls, "回傳": rets, "原文": raw,
                                "推理": reason, "截斷": trunc}, ensure_ascii=False) + NL)
        if not args.image and not live:
            time.sleep(1.0)


if __name__ == "__main__":
    main()
