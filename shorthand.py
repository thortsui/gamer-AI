# -*- coding: utf-8 -*-
"""動作速記（2026-10-14 使用者：「視角左／視角右／前進／後退……直接改成一個參數後面配括號多少，
減少他自己要去接龍的字數」）。

他回的 JSON 裡，"a" 陣列的每一項除了原本的 dict，也可以是一個字串「名字(量)」：
    "前進(0.6)"  → {"t":"key","k":"w","d":0.6}
    "視角左(100)" → {"t":"look","dx":-100,"dy":0}
    "長按(e)"    → {"t":"hold","k":"e"}
    "點字(開始遊戲)" → {"t":"點字","字":"開始遊戲"}
程式在解析回覆之後先把字串翻成 dict，後面所有流程（拆兩段、一次只按一個、上一步摘要…）照舊。
整個 "a" 也可以是一個字串，用空格或頓號隔開多個動作。舊寫法完全不受影響。
"""
import re

# 走路：名字 → 鍵；括號裡是秒數（沒寫＝0.5 秒，跟拆兩段的預設一樣）
WALK = {"前進": "w", "後退": "s", "左移": "a", "右移": "d",
        "前": "w", "後": "s", "左": "a", "右": "d", "向前": "w", "向後": "s", "向左": "a", "向右": "d"}
# 轉視角：名字 → (dx 方向, dy 方向)；括號裡是像素（跟 look 的 dx／dy 同單位、同刻度）
LOOK = {"視角左": (-1, 0), "視角右": (1, 0), "視角上": (0, -1), "視角下": (0, 1),
        "左轉": (-1, 0), "右轉": (1, 0), "抬頭": (0, -1), "低頭": (0, 1), "往上看": (0, -1), "往下看": (0, 1)}
# 一個名字參數的工具：名字 → 欄位名
NAMED = {"鎖定": "target", "追蹤": "target", "走到": "target", "找": "labels",
         "走到記過的": "name", "設目標": "name", "我在": "name", "到標點": "name", "記站位": "name",
         "記位置": "name", "拍地標": "name", "忘記按鈕": "name", "切空間": "name", "記按鈕": "name",
         "執行程序": "名", "編譯程序": "名", "錄成程序": "名", "換快思": "為什麼", "點字": "字", "點文字": "字",
         "wiki": "q", "think": "q", "思考強度": "強度", "記問題": "問題", "定比例": "比例",
         "改提示": "內容", "選": "n"}
KEYS = {"w", "a", "s", "d", "space", "e", "esc", "tab", "enter", "l", "shift", "ctrl", "f", "q", "r",
        "up", "down", "left", "right", "1", "2", "3", "4", "5", "6", "7", "8", "9", "0"}
KEY_ALIAS = {"跳": "space", "跳躍": "space", "互動": "e", "退出": "esc", "確認": "enter"}

_ITEM = re.compile(r"[^\s,，、;；()（）]+(?:\s*[(（][^)）]*[)）])?")
_ONE = re.compile(r"^\s*([^\s(（]+)\s*(?:[(（]\s*(.*?)\s*[)）])?\s*$")


def _num(s, default=None):
    try:
        return float(str(s).strip())
    except (TypeError, ValueError):
        return default


def _args(s):
    if s is None or not str(s).strip():
        return []
    return [x.strip() for x in re.split(r"[,，、]", str(s)) if x.strip()]


def _clean(v):
    """整數就印整數，不要 100.0。"""
    return int(v) if float(v).is_integer() else round(float(v), 3)


def expand_one(s):
    """一個速記字串 → 動作 dict；看不懂就回 None。"""
    m = _ONE.match(str(s))
    if not m:
        return None
    name, raw = m.group(1), m.group(2)
    args = _args(raw)
    low = name.lower()
    if name in WALK:
        d = _num(args[0], 0.5) if args else 0.5
        return {"t": "key", "k": WALK[name], "d": _clean(d)}
    if name in LOOK:
        n = _num(args[0]) if args else None
        if n is None:
            return None
        sx, sy = LOOK[name]
        return {"t": "look", "dx": int(round(sx * n)), "dy": int(round(sy * n))}
    if low in ("look", "視角", "轉視角"):
        dx = _num(args[0], 0) if args else 0
        dy = _num(args[1], 0) if len(args) > 1 else 0
        return {"t": "look", "dx": int(round(dx)), "dy": int(round(dy))}
    if name in KEY_ALIAS:
        a = {"t": "key", "k": KEY_ALIAS[name]}
        if args and _num(args[0]) is not None:
            a["d"] = _clean(_num(args[0]))
        return a
    if low in ("按", "key", "按鍵"):
        if not args:
            return None
        a = {"t": "key", "k": args[0].lower()}
        if len(args) > 1 and _num(args[1]) is not None:
            a["d"] = _clean(_num(args[1]))
        return a
    if low in ("長按", "hold"):
        a = {"t": "hold", "k": (args[0].lower() if args else "e")}
        if len(args) > 1:
            a["g"] = args[1]
        return a
    if low in ("點", "click", "點擊"):
        if len(args) < 2 or _num(args[0]) is None or _num(args[1]) is None:
            return None
        a = {"t": "click", "x": _num(args[0]), "y": _num(args[1])}
        if len(args) > 2:
            a["g"] = args[2]
        return a
    if low in ("瞄", "aim", "對準"):
        if len(args) < 2 or _num(args[0]) is None or _num(args[1]) is None:
            return None
        a = {"t": "aim", "x": _num(args[0]), "y": _num(args[1])}
        if len(args) > 2:
            a["g"] = args[2]
        return a
    if low in ("等", "wait", "等待"):
        return {"t": "wait", "d": _clean(_num(args[0], 1.0) if args else 1.0)}
    if low in ("捲", "scroll", "滾"):
        return {"t": "scroll", "v": int(_num(args[0], -3) if args else -3)}
    if low in KEYS:
        a = {"t": "key", "k": low}
        if args and _num(args[0]) is not None:
            a["d"] = _clean(_num(args[0]))
        return a
    if name in NAMED:
        field = NAMED[name]
        if not args:
            return {"t": name}
        if field == "labels":
            return {"t": name, "labels": args}
        if field == "n":
            return {"t": name, "n": int(_num(args[0], 1)), "做": (args[1] if len(args) > 1 else "aim")}
        if field == "比例":
            return {"t": name, "比例": _num(args[0], 0.5)}
        return {"t": name, field: raw.strip() if len(args) == 1 else args[0]}
    # 其他工具：沒參數就只有 t（水平歸零、這步做完、大動作完成、重新定位、量刻度、量比例、解除鎖定…）
    if not args:
        return {"t": name}
    return {"t": name, "name": raw.strip()}


def split_items(s):
    """「前進(0.6) 視角左(100)」→ ["前進(0.6)", "視角左(100)"]"""
    return [x.strip() for x in _ITEM.findall(str(s)) if x.strip()]


def expand_list(lst):
    """a 陣列 → (翻好的 dict 陣列, 翻譯紀錄 [(原字串, dict或None)…])"""
    if isinstance(lst, str):
        lst = split_items(lst)
    if not isinstance(lst, list):
        return [], []
    out, log = [], []
    for item in lst:
        if isinstance(item, dict):
            out.append(item)
        elif isinstance(item, str):
            for piece in split_items(item):
                a = expand_one(piece)
                log.append((piece, a))
                if a:
                    out.append(a)
        # 其他型別（數字、None）直接丟掉
    return out, log


def expand_reply(o):
    """整份回覆：翻 o["a"] 與 o["步"][i]["a"]。回傳翻譯紀錄（空＝他這輪沒用速記）。"""
    if not isinstance(o, dict):
        return []
    log = []
    if "a" in o and o["a"] is not None:
        new, lg = expand_list(o["a"])
        if lg:
            o["a"] = new
        log += lg
    for key in ("步", "steps"):
        steps = o.get(key)
        if isinstance(steps, list):
            for st in steps:
                if isinstance(st, dict) and st.get("a") is not None:
                    new, lg = expand_list(st["a"])
                    if lg:
                        st["a"] = new
                    log += lg
    return log


def describe(log):
    """翻譯紀錄 → 一行給 stdout／【上一步】看。"""
    import json
    parts = []
    for s, a in log:
        parts.append(f"{s}→" + (json.dumps(a, ensure_ascii=False) if a else "看不懂，丟掉"))
    return "、".join(parts)
