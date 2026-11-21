"""11/21 晚 使用者「不能有隊友」「你需要幫她按 ESC 檢查團隊裡面是否只有他一人還是有更多人」
程式（不是模型）按 ESC 開 Roblox 選單 → 截圖、OCR 讀人物清單 → 再按 ESC 關掉。模型不知道這件事。
check() 回傳 (人數或 None, 讀到的名字, 截圖路徑)。"""
import os, re, sys, time
from datetime import datetime

WT = r"D:\Documents\ThorWorkSpace\gamer AI 工具版"
HERE = os.path.dirname(os.path.abspath(__file__))
DIR = os.path.join(HERE, "隊友檢查")
sys.path[:0] = [WT, os.path.join(WT, "工具版")]
import tool_agent  # noqa: E402

_T = {}


def tools():
    if "t" not in _T:
        _T["t"] = tool_agent.Tools("動物醫院", tool_agent.load_catalog("動物醫院"), live=True)
    return _T["t"]


def ocr(im):
    import local_text
    out = []
    for r in (local_text._win_read(im) or []):
        box, txt = r[0], str(r[1]).strip()
        if txt:
            xs = [p[0] for p in box]; ys = [p[1] for p in box]
            out.append((min(xs), min(ys), txt))
    return sorted(out, key=lambda x: (x[1], x[0]))


def check(tag=""):
    import pydirectinput
    t = tools()
    t._fg_ok()
    pydirectinput.press("esc"); time.sleep(1.8)
    im = t.grab()
    pydirectinput.press("esc"); time.sleep(0.8)
    os.makedirs(DIR, exist_ok=True)
    p = os.path.join(DIR, f"{datetime.now():%H%M%S}_{tag}.jpg")
    im.convert("RGB").save(p)
    words = ocr(im)
    names = [w for _, _, w in words if w.startswith("@")]
    return (len(names) if names else None), names, words, p


if __name__ == "__main__":
    n, names, words, p = check("test")
    print("人數", n, names); print(p)
    for x, y, w in words:
        print(int(x), int(y), w)


# ── 每輪開頭由程式呼叫（raw_agent／tool_agent 都用），模型不知道 ──
# 進局（畫面上方中間有「輪班」）且這一局還沒查過 → 按 ESC 看「在此伺服器中」清單；
# 有「新增好友」按鈕＝有別的玩家 → 關掉 Roblox 重開回大廳（程式做的，不是模型離開遊戲）。
import json  # noqa: E402

STATE = {"查過這局": False, "單人中": False, "單人局結束": False, "在局": False, "嘗試輪": 0, "嘗試開始": None, "嘗試次": 0}
# 11/22 01:2x 使用者「花幾個步驟才成功進入遊戲也要記錄（每一次會重新計算，取最少次的那次）」
# → 每次從大廳開始算一次「嘗試」：數到進局（上方出現輪班）用了幾輪、幾秒；重開或回大廳就重新算
# 11/22 01:1x 使用者「至少排到一局，統計就是從那一局開始到那一局結束」→ 第一次單人局結束（回到大廳）時記下來；
# SOLO_STOP_AFTER=1 時放 stop.txt（SOLO_STOP_FILE）讓這一段結束
LOGF = os.path.join(DIR, "紀錄.jsonl")
PLACE = "roblox://placeId=78515283254292"


def _log(d):
    os.makedirs(DIR, exist_ok=True)
    d = dict({"時間": datetime.now().strftime("%m/%d %H:%M:%S")}, **d)
    with open(LOGF, "a", encoding="utf-8") as f:
        f.write(json.dumps(d, ensure_ascii=False) + "\n")


def in_match(im):
    W, H = im.size
    top = im.crop((int(W * .3), 0, int(W * .7), int(H * .08)))
    return any("輪班" in w for _, _, w in ocr(top))


ME = "thortsui"   # 11/21 使用者：「我的 ID 是 thortsui」


def _rows(ys):
    ys = sorted(ys)
    return [y for i, y in enumerate(ys) if i == 0 or y - ys[i - 1] > 30]


def in_lobby(im):
    W, H = im.size
    ws = "".join(w for x, y, w in ocr(im) if x < W * .14 and H * .25 < y < H * .8)   # 整張讀比裁切準（OCR 會把兩顆按鈕併成一行）
    return sum(c in ws for c in "商店邀請職業日誌表情") >= 5


def count_others(words):
    """清單裡 @帳號 不是 thortsui 的列數；另外「新增好友」按鈕（別的玩家才有）也算，取兩者較大。
    OCR 會把模糊背景讀成第二份，用 y 合併（列距約 85 像素）。"""
    # 11/22 00:00 誤判：模糊背景把 @Thortsui 讀成 @ThORsui → 以「列」判斷：同一列（±40 像素）任何字有 thortsui 就是自己
    me_y = [y for x, y, w in words if ME in w.lower()]
    at_rows = _rows(y for x, y, w in words if w.startswith("@") and 450 < x < 700)
    other_at = [y for y in at_rows if not any(abs(y - m) <= 40 for m in me_y)]
    friend_btn = _rows(y for x, y, w in words if "新增好友" in w)
    return max(len(other_at), len(friend_btn))


def me_listed(words):
    return any(ME in w.lower() for _, _, w in words)


def guard(t, label=""):
    """t＝tool_agent.Tools。回傳一句說明（給紀錄用，不給模型）或空字串。"""
    try:
        im = t.grab()
        if im is None:
            return ""
        if STATE["嘗試開始"] is None:
            STATE["嘗試開始"] = time.time(); STATE["嘗試次"] += 1
        im_in = in_match(im)
        if im_in and not STATE["在局"]:
            STATE["在局"] = True
            _log({"段": label, "結果": "進場", "第幾次嘗試": STATE["嘗試次"], "輪數": STATE["嘗試輪"],
                  "秒": round(time.time() - STATE["嘗試開始"])})
        if not STATE["在局"]:
            STATE["嘗試輪"] += 1
        if not im_in:
            # 11/21 23:4x：局裡開電腦／表格時上方「輪班」會被蓋住，不能當成回大廳；看到大廳左側按鈕（商店／職業／日誌）才重設
            if in_lobby(im):
                STATE["查過這局"] = False
                if STATE["在局"]:                       # 回到大廳＝下一次嘗試重新算
                    STATE["在局"] = False; STATE["嘗試輪"] = 0; STATE["嘗試開始"] = time.time(); STATE["嘗試次"] += 1
                if STATE["單人中"]:
                    STATE["單人中"] = False
                    first = not STATE["單人局結束"]
                    STATE["單人局結束"] = True
                    _log({"段": label, "結果": "單人局結束（回到大廳）" + ("，第一局" if first else "")})
                    sf = os.environ.get("SOLO_STOP_FILE")
                    if first and os.environ.get("SOLO_STOP_AFTER") == "1" and sf:
                        open(sf, "w").close()
                        _log({"段": label, "結果": f"第一局單人局結束 → 放 {sf}，這段結束"})
                        return "單人局結束"
            return ""
        if STATE["查過這局"]:
            return ""
        import pydirectinput
        t._fg_ok()
        pydirectinput.press("esc"); time.sleep(1.8)
        im2 = t.grab()
        pydirectinput.press("esc"); time.sleep(0.8)
        p = os.path.join(DIR, f"{datetime.now():%H%M%S}_{label}.jpg")
        os.makedirs(DIR, exist_ok=True)
        im2.convert("RGB").save(p, quality=75)
        words = ocr(im2)
        if not any("伺服器" in w for _, _, w in words):
            _log({"段": label, "結果": "沒看到人物清單，下輪再查", "圖": p})
            return ""
        n = count_others(words)
        if not me_listed(words):
            _log({"段": label, "結果": "清單裡沒讀到 thortsui，下輪再查", "圖": p})
            return ""
        STATE["查過這局"] = True
        if n == 0:
            STATE["單人中"] = True
            _log({"段": label, "結果": "只有 thortsui 一人" + ("（第一局單人局開始）" if not STATE["單人局結束"] else ""), "圖": p})
            return "只有自己"
        _log({"段": label, "結果": f"有 {n} 位別的玩家 → 重開回大廳", "圖": p})
        import subprocess
        subprocess.run(["taskkill", "/IM", "RobloxPlayerBeta.exe", "/F"], capture_output=True)
        time.sleep(5)
        os.startfile(PLACE)
        time.sleep(75)
        try:
            t.win = None
            t._screen_ok()
        except Exception:  # noqa: BLE001
            pass
        STATE["查過這局"] = False
        STATE["在局"] = False; STATE["嘗試輪"] = 0; STATE["嘗試開始"] = time.time(); STATE["嘗試次"] += 1   # 重開＝重新算
        return f"有隊友 {n} 位，已重開"
    except Exception as e:  # noqa: BLE001
        _log({"段": label, "結果": f"檢查失敗 {e}"})
        return ""
