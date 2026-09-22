#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
play_agent.py — 讓 DGX 上的千問「自己玩 Roblox 遊戲並研究它」

迴圈（每輪從擷取到拿到決策硬截止 5 秒）：
  看：最近 1 秒的 5 張螢幕畫面（5 fps）包成短影片 → 模型
  想：模型只回一個精簡 JSON（看到什麼／有沒有 Robux 購買視窗／要做的動作／學到的筆記）
  守：程式端硬性規則 —— 任何 Robux／付費相關的目標一律擋掉；看到購買視窗只准 Esc；
       畫面大變化（和上一輪差很多）→ 這一輪只「看清楚」不動作
  做：pydirectinput 送鍵盤滑鼠給 Roblox 視窗（只在 Roblox 視窗是前景時才動）
  記：資料/<遊戲>/行動日誌.jsonl、研究筆記.md、畫面/（大變化時存圖）

安全開關：
  - 滑鼠甩到螢幕左上角 → pyautogui FAILSAFE 立刻停
  - 專案資料夾放一個「停止.txt」→ 下一輪停
  - 按 F12 → 停（需要 keyboard 套件抓得到）
  - 時間到（預設 16 小時）→ 停

用法：
  python play_agent.py --game 動物醫院 --hours 16
  python play_agent.py --game 動物醫院 --dry-run     # 只看不動（測試決策）
"""
import argparse
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime

import pyautogui  # 先 import：它會把行程設成 DPI aware，之後座標都是實體像素
import pydirectinput
import mss
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from screen_watch import ScreenGrabber, mp4_data_uri, jpeg_data_uri, load_config  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

pyautogui.FAILSAFE = True
pydirectinput.FAILSAFE = True
EDGE_MARGIN = 6          # 2026-09-18：我們自己送的滑鼠座標一律離螢幕邊緣至少這麼多像素


def safe_xy(x, y):
    """把滑鼠目標夾在螢幕內側，**永遠不會落在角落**。

    2026-09-18 踩過的坑：09/18 01:48 代理自己停了，原因是滑鼠停在 (0,0)——
    那是使用者設計的「甩到左上角＝緊急停止」手勢的位置。游標一旦停在那裡，
    pydirectinput 的 fail-safe 會讓**每一個**滑鼠呼叫都失敗（點字、轉視角全滅），
    最後 pyautogui 直接丟 FailSafeException 把整個代理收掉。
    修法不是關掉 fail-safe（那是使用者唯一的手動煞車），而是**我們自己送的座標永遠不碰角落**；
    這樣游標出現在角落就一定是真人的手，手勢的意義反而更乾淨。
    """
    try:
        import ctypes
        u = ctypes.windll.user32
        w, h = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
    except Exception:  # noqa: BLE001
        w, h = 1920, 1080
    m = EDGE_MARGIN
    return (int(max(m, min(w - 1 - m, int(x)))), int(max(m, min(h - 1 - m, int(y)))))


def move_safe(x, y):
    x, y = safe_xy(x, y)
    pydirectinput.moveTo(x, y)
    return x, y
pydirectinput.PAUSE = 0.02

HERE = os.path.dirname(os.path.abspath(__file__))
STOP_FILE = os.path.join(HERE, "停止.txt")

# ------------------------------------------------------------------ 硬規則
FORBIDDEN_WORDS = [  # 任何花錢的動作都擋：Robux、真錢、遊戲幣（金幣／現金／寶石）通通不准
    "robux", "r$", "premium", "gamepass", "game pass", "通行證", "儲值", "加值", "訂閱", "subscribe",
    "信用卡", "credit card", "禮物卡", "gift card", "結帳", "checkout",
    "購買", "買", "buy", "purchase", "付費", "付款", "pay", "price", "價格", "花費", "spend", "兌換", "redeem",
    "升級", "upgrade", "vip", "會員", "解鎖", "unlock",
    "復活", "revive", "respawn", "重生道具", "禮包", "儲值包",   # 2026-09-07 11:45：日誌分析發現「自我復活」被點 95 次未擋（付費視窗都被 esc，沒花錢）
]
ALLOWED_KEYS = {
    # 2026-09-22：加 "l"——核心文件教他卡住就 esc→l→enter 退出重進，但 l 一直不在清單裡，
    # 送了也會被擋。這就是他「知道要走卻走不了」的原因之一。
    "w", "a", "s", "d", "space", "e", "q", "r", "f", "l", "shift", "esc", "tab", "enter", "backspace",
    "1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "`", "up", "down", "left", "right", "i", "o",
}
MAX_KEY_HOLD = 3.0
MAX_ACTIONS_PER_CYCLE = 2
BIG_CHANGE_THRESHOLD = 28.0   # 0~255 灰階平均差，超過就算「大變化」

SYSTEM_PROMPT = """你是 gamer AI，一個正在用 Windows 電腦玩 Roblox 的玩家。
任務：玩名為「{game}」的 Roblox 遊戲，並研究它的玩法。你只能透過下面的動作操作電腦，沒有人會幫你。
你會收到：最近 1 秒的螢幕連續畫面（影片，時間順序）、你最近幾輪做過的事、你目前的研究筆記摘要。
注意：畫面上淡黃色的直線橫線與 0.1～0.9 的小數字，是「程式」疊上去給你報座標用的尺，遊戲裡沒有這東西。它不會遮住任何東西、不需要關掉、不要按 F9 或任何鍵去處理它，也不要在 s 寫「被網格遮擋」。

【每一輪都照這四步想，缺一不可，順序不能跳】
步驟0 確認平視角：先看【你現在的視角】。鏡頭偏上或偏下就先把它轉回水平（v 填 0，這輪的動作就是轉視角），水平了才往下做（v 填 1）。
步驟1 了解目標：現在到底要達成什麼？看右上角任務名稱、對話框文字、黃色箭頭、以及【工作流程】的 👉 那一步。把它寫進 o。
步驟2 找到目標物品：那個目標在畫面的哪個位置？寫進 f（例如「右前方的木門」「畫面中央的機台」）。找不到就寫「找不到」，這輪的動作就是轉視角或往前走去找。
步驟3 執行對應互動：找到了才動手——先 aim 對準它，再 w 走近、按 e 互動或點擊。

【你最大的問題：至今 Check In 次數 0】你的本業是幫病人登記賺錢，不是喝咖啡（你喝了 645 次）。
櫃檯有病人 → 長按 E 印資料 → 比對照片 → 看 CCTV → 正常按「Check In」、異常關快門。沒登記就沒錢。
理智低於 66% 才去咖啡機，其他時間都待在櫃檯做登記。

鐵律（違反會被程式擋下並記錄）：
1. 絕對禁止花任何錢：Robux、真錢、遊戲幣（金幣、現金、寶石、點數）通通不准花。任何「購買／解鎖／升級／兌換」按鈕都不准點；看到購買或付費視窗一律 esc 或點「取消／關閉」。只能用免費的方式玩（走路、互動、做任務、撿東西）。
2. 畫面和上一輪差很多時，先把畫面看清楚再決定，不要亂點。
3. 不改帳號設定、不離開 Roblox、不在聊天室打字。唯一例外：真的卡住學不會時，可以用 wiki 動作去查文字攻略（程式會幫你開網頁並在時間到時關掉回到遊戲；每 10 分鐘最多一次）。**YouTube 已經停用，不要用 youtube 動作**。查資料時同樣不准花任何資源、不准登入、不准訂閱、不准點廣告或加入會員。
4. 不確定按鈕是做什麼的，先靠近觀察或小步嘗試，一次只做一件事。
5. 邊玩邊學：試過的做法要把結果記下來，成功的方法重複用，失敗的不要再試。
6. **照【工作流程】做事**：每輪先看 👉 指到第幾步，就做那一步；做完了在 p 回報新的步驟號。不要亂跳步驟，也不要每輪重新想一遍。
6b. 你不必每一輪都動作。可以用 think 動作停下來想（整理現況、找死因、擬計畫），想完再玩；也可以 wait 觀察。
10. 每次剛進遊戲、剛重生、剛脫困、或畫面只看得到地板時：**第一步先把鏡頭抬到水平**（look dy 用負值約 -60~-120，一次就好），看到前方的路和牆面再行動。
10b. **黃色箭頭永遠是對的，最優先跟它走**：畫面上出現黃色箭頭時，先用 aim 把準心對到箭頭指的方向，再 w 直走過去，不要自己另外找路、不要懷疑它。看不到箭頭才用其他線索。
11. **移動前先用 aim 對準目標**：把畫面正中央的準心轉到你要去或要互動的東西上，再往前走或按 e。看到黃色箭頭、NPC、門、機台時都先 aim。
11a. **不要貼牆走**：畫面變成一大片單色、看不到地板與物件時，代表你臉貼在牆上。這時**先後退一步再轉身 180 度**（look dx 用一個完整畫面寬的量），絕對不要繼續往前頂。走路盡量走在走廊中間，離牆一段距離才看得到自己在哪。
11b. **互動用長按，不要連點**：任何互動（開門、印表機、電腦、咖啡機、撿東西、Check In）都先用 hold 動作，程式會按到畫面有反應為止；按一下沒反應就重複按是最常見的浪費。畫面出現「按住 E」時更要用 hold。
12. **記住你剛做過什麼**：提示裡有【你剛才做了什麼、有沒有效果】。同一個動作做過兩次沒效果就換做法，尤其不要一直後退（s）。
13. **距離用時間控制**：key 的 d 就是按住秒數，按住越久走越遠。要走到看得見的遠處目標用 d=2~3，走一小段用 d=0.5~1，微調用 d=0.2。不要用 0.1 秒一步一步挪。
14. 移動以**直走為主**：用 w 往前走（反射 c 也用 w），走不動再轉頭換方向；不要原地一直微調視角或左右挪。
9. 畫面上出現文字或指示（對話框、任務提示、教學浮字、告示牌、系統訊息）時，優先把它讀完、理解意思，再決定動作；s 欄要寫出你讀到的重點，不要看都沒看就點掉。
8. 遇到你不知道的事、或不是你要做的事被觸發（突然出現的視窗、被傳送、被攻擊、任務突然變了…）：先用 think 分析「為什麼會觸發、該怎麼做」，把結論寫進筆記；想不通就用 wiki 動作查攻略（打開瀏覽器搜尋，看完自動回遊戲），學到的也寫進筆記。
7. 死了要知道自己死了：看到倒地／理智 0%／死亡畫面時，先在 s 寫「我死了」，之後程式會請你檢討死因，檢討結果會出現在下一輪的【上次死因與檢討】，照著避免再死。


你有 10 秒可以想，想清楚再回答。只輸出一個 JSON（不要 markdown、不要多餘文字、不要換行）：
{{"s":"看到什麼(<=12字)","v":0或1(0=視角還沒水平，這輪先轉正;1=已水平),"o":"現在的目標(<=10字)","f":"目標在畫面哪裡(<=10字)或找不到","r":0或1(畫面有購買/付費/花錢視窗=1),"a":[動作...最多2個],"c":反射動作或null,"p":工作流程第幾步(數字),"n":"真的學到新規則才寫(<=25字)，否則空字串"}}
「c」是**反射動作**：你在想的時候（約 3~4 秒）程式會每 1 秒替你重複做一次，並自己判斷要不要繼續（畫面變了、前面是牆、走不動了就自動停）。
用在「還要再走一段路」「還要繼續轉頭找目標」這種連續動作，例如 {{"t":"key","k":"w","d":0.3}}（繼續往前走）或 {{"t":"look","dx":80,"dy":0}}（繼續往右轉頭）。
不需要連續動作（正在讀對話、等咖啡、要點按鈕）時 c 給 null。c 只能是 key(w/a/s/d/e)、look、wait，不能點擊（所以絕不會誤買東西）。
最常用也最有效的反射是 {{"t":"key","k":"w","d":0.4}}（一直往前走）與 {{"t":"key","k":"e","d":0.1}}（一路走一路按 E 找互動）。
動作格式（座標是畫面比例 0~1，左上角 0,0）：
  {{"t":"click","x":0.52,"y":0.31,"g":"點的目標名稱"}}   左鍵點一下
  {{"t":"move","x":0.52,"y":0.31}}   只把滑鼠移到該位置（懸停、看提示、拖曳前定位）
  {{"t":"hold","k":"e","g":"要互動的東西"}}   **按住 E 直到互動完成**（程式會盯著畫面，一有反應就放開，最多 4 秒）。
      看到「按住 E」「Hold E」的提示、或按一下 E 沒反應時，一律改用這個，不要重複點按。
  {{"t":"aim","x":0.62,"y":0.55,"g":"目標名稱"}}   **把畫面正中央的準心轉到這個位置**（程式會依實測靈敏度換算成轉視角）。
      要走向、要互動、要看清楚某個東西時，先 aim 對準它，再 w 直走或按 e，比亂轉視角準得多。
  {{"t":"key","k":"w","d":1.0}}      按住鍵 d 秒（0.05~3）；可用鍵：w a s d space e q r f shift esc tab enter 1-9 0 ` 方向鍵
  {{"t":"look","dx":120,"dy":0}}     按住右鍵拖曳轉視角（像素）：dx 正=往右看、負=往左看；dy 正=往下看、負=往上看（-100~400）
      換算（本機實測，供你估算）：{look_hint}
      ⚠ 不要連續用負的 dy 往上看：鏡頭一抬到天空／天花板就什麼都看不到。看不到路時先用正的 dy（往下看地面），程式也會在你抬太高時自動幫你壓回。
  {{"t":"scroll","v":-3}}            滾輪（負=拉遠/往下）
  {{"t":"type","x":"文字","g":"要打字的欄位"}}  在搜尋欄打字（禁止聊天室）
  {{"t":"wait","d":1.0}}             什麼都不做等 d 秒（例如載入中）
  {{"t":"wiki","q":"搜尋關鍵字"}}   查攻略／wiki（例：「Animal Anomaly roblox wiki 異常」「動物醫院 roblox 攻略」）；程式開瀏覽器給你看最多 3 分鐘，每 10 分鐘最多一次，不准登入、不准點廣告或購買
  {{"t":"think","q":"要想清楚的問題"}}   停下來思考（不動作，程式會給你較長時間整理，結果記進計畫）；每 2 分鐘最多一次
r=1 時 a 只能放 esc 或點取消。"""

CAREFUL_PROMPT = """畫面剛剛發生大變化。請只描述你現在看到的畫面，這一輪不要做任何動作。
只輸出 JSON：{"s":"畫面描述(<=60字，含畫面上的主要按鈕/文字)","r":0或1(有購買/付費/花錢視窗=1，不管Robux還是金幣)}"""

CONFIRM_WORDS = ["確認", "確定", "ok", "yes", "是", "接受", "accept", "繼續", "continue", "submit", "送出",
                 "confirm", "同意", "agree", "領取", "claim", "get", "取得", "免費", "free", "開啟", "open", "解鎖", "unlock",
                 "購買", "買", "buy", "purchase", "付費", "vip", "upgrade", "升級", "會員", "商店", "shop", "價格", "price"]

VERIFY_PROMPT = """我準備點擊畫面上紅框標示的位置（第一張是整個畫面，第二張是紅框附近的放大圖）。
請看清楚：那個按鈕/物件上寫什麼？點下去會不會花任何錢——Robux、真錢、或遊戲幣（金幣、現金、寶石、有價格數字、購買/解鎖/升級字樣）？
只輸出 JSON：{"b":"按鈕上的文字或物件(<=20字)","p":0或1(會花任何錢=1，不確定也算1)}"""

LEARN_PROMPT = """你現在在{where}學「{game}」怎麼玩（第一張是最近 1 秒影片，第二張是清楚畫面，有黃色座標格線）。你想弄懂的是：{q}
規則：不准登入、不准訂閱、不准點廣告、不准加入會員、不准買任何東西；跳過廣告的「略過」可以點；cookie/同意視窗可以點「接受」或關閉。
你可以：點搜尋結果或連結、scroll 往下讀、wait 看內容、按 space 暫停/播放、按 esc。
把看到的玩法要點寫進 n（具體：去哪裡、按什麼、目標是什麼、什麼東西會觸發什麼）。看夠了或內容沒用就 d=1 結束。
只輸出 JSON：{{"s":"看到什麼(<=15字)","a":[動作...最多2個],"n":"學到的玩法(<=40字)或空字串","d":0或1(結束回遊戲=1)}}"""

SUMMARY_PROMPT = """以下是你玩「{game}」累積的研究筆記（很多重複）。請濃縮成一份「玩法摘要」：最多 10 條、每條 <=25 字，
只留對「怎麼玩、目標是什麼、東西在哪、什麼不能做」有用的規則；卡牆／鏡頭／選單操作類最多留 1 條。
只輸出 JSON：{{"summary":["...","..."]}}

筆記：
{notes}"""

THINK_PROMPT = """你決定停下來想一想。問題：{q}
以下是你的目標、最近紀錄、玩法摘要與上次死因。請用 <=120 字寫下你的想法與接下來的計畫（具體到要去哪、做什麼、避免什麼）。
只輸出 JSON：{{"plan":"你的計畫(<=120字)","n":"若有新學到的規則寫這裡(<=30字)，否則空字串"}}

目標與指示：
{instr}

玩法摘要：
{summary}

上次死因與檢討：
{death}

最近紀錄：
{history}"""

DEATH_PROMPT = """你剛剛在「{game}」裡死了（角色倒地／理智歸零）。這是第 {n} 次死亡。請根據最近紀錄檢討：
1. 死因是什麼（理智歸零？被什麼攻擊？做了什麼導致的？）
2. 死前哪個決定是錯的
3. 下次要怎麼避免（具體）

⚠ **死因只能從下面「死前三十秒實際做的事」那份逐筆紀錄裡找**。
畫面上剛好有什麼東西（商店面板、看板、某個 NPC）**不等於**它害死你——
同時出現不是因果。紀錄裡看不出死因就老實寫「看不出來」，
不要硬掰一個，硬掰出來的規則會讓你之後一直做沒有用的事。

只輸出 JSON：{{"cause":"死因(<=40字)","mistake":"錯誤決定(<=40字)","avoid":"下次怎麼避免(<=60字)"}}

上次死因與檢討（若有）：
{last}

死前三十秒你實際做了什麼（程式一筆一筆記的，不會漏）：
{trace}

死前的紀錄（每輪一條的摘要）：
{history}"""

REFLECT_PROMPT = """以下是你最近玩「{game}」的觀察與行動紀錄。請把「確定學到的玩法」整理成最多 5 條筆記，每條 <=30 字，
不要重複已有筆記，不確定的不要寫。只輸出 JSON：{{"notes":["...","..."]}}

已有筆記：
{notes}

最近紀錄：
{history}"""


# ------------------------------------------------------------------ 工具
def draw_grid(img):
    """疊一層淡格線＋座標標籤（每 0.1），讓模型報座標更準。回傳新影像。"""
    from PIL import ImageDraw
    im = img.copy()
    d = ImageDraw.Draw(im, "RGBA")
    w, h = im.size
    for i in range(1, 10):
        x, y = int(w * i / 10), int(h * i / 10)
        d.line((x, 0, x, h), fill=(255, 255, 0, 38), width=1)
        d.line((0, y, w, y), fill=(255, 255, 0, 38), width=1)
        d.text((x + 2, 2), f"{i/10:.1f}", fill=(255, 255, 0, 200))
        d.text((2, y + 2), f"{i/10:.1f}", fill=(255, 255, 0, 200))
    return im


LOCATE_PROMPT = """這張圖上有黃色座標格線（每 0.1 一格，左上角 0,0）。請找出「{target}」在畫面上的正中央位置。
只輸出 JSON：{{"x":0.00,"y":0.00,"f":0或1(找不到=0)}}"""


def forbidden(text):
    t = (text or "").lower()
    return any(w in t for w in FORBIDDEN_WORDS)


def parse_json_loose(s):
    """模型輸出被截斷時，盡量救回一個 dict。"""
    s = s.strip()
    s = re.sub(r"^```(?:json)?|```$", "", s, flags=re.M).strip()
    try:
        obj = json.loads(s)
        if isinstance(obj, list):  # 模型偶爾回一個 list：取第一個 dict
            obj = next((o for o in obj if isinstance(o, dict)), {"s": "(回傳格式怪)", "r": 0, "a": []})
        return obj if isinstance(obj, dict) else {"s": str(obj)[:30], "r": 0, "a": []}
    except json.JSONDecodeError:
        pass
    start = s.find("{")
    if start < 0:
        return None
    frag = s[start:]
    for tail in ("", "\"}", "\"]}", "}]}", "\"}]}", "]}", "}}"):
        try:
            return json.loads(frag + tail)
        except json.JSONDecodeError:
            continue
    m = re.search(r'"s"\s*:\s*"([^"]*)"', frag)
    return {"s": m.group(1) if m else "(無法解析)", "r": 1 if '"r":1' in frag.replace(" ", "") else 0, "a": []}


def frame_diff(a, b):
    """兩張 PIL 影像的灰階平均差（0~255）。"""
    if a is None or b is None:
        return 0.0
    ga = a.convert("L").resize((160, 90))
    gb = b.convert("L").resize((160, 90))
    pa, pb = ga.tobytes(), gb.tobytes()
    return sum(abs(x - y) for x, y in zip(pa, pb)) / len(pa)


class RobloxWindow:
    def __init__(self):
        self.hwnd = None

    def find(self):
        """先用標題找；找不到就用行程名 RobloxPlayerBeta 找（在大廳時視窗標題是空的）。"""
        import win32gui
        found, by_proc = [], []

        def cb(h, _):
            if not win32gui.IsWindowVisible(h) or win32gui.IsIconic(h):
                return
            t = win32gui.GetWindowText(h)
            if t == "Roblox" or (t.startswith("Roblox") and "Studio" not in t):
                found.append(h)
                return
            try:
                import win32process
                import psutil
                _, pid = win32process.GetWindowThreadProcessId(h)
                if psutil.Process(pid).name().lower() == "robloxplayerbeta.exe":
                    l, t2, r, b = win32gui.GetClientRect(h)
                    if (r - l) > 400 and (b - t2) > 300:
                        by_proc.append(((r - l) * (b - t2), h))
            except Exception:  # noqa: BLE001
                pass
        win32gui.EnumWindows(cb, None)
        if found:
            self.hwnd = found[0]
        elif by_proc:
            by_proc.sort(reverse=True)
            self.hwnd = by_proc[0][1]
        else:
            self.hwnd = None
        return self.hwnd

    def is_foreground(self):
        import win32gui
        return self.hwnd is not None and win32gui.GetForegroundWindow() == self.hwnd

    def client_bbox(self):
        """Roblox 視窗客戶區（不含標題列）的螢幕座標；找不到回 None。"""
        import win32gui
        if self.hwnd is None and not self.find():
            return None
        try:
            if not win32gui.IsWindowVisible(self.hwnd) or win32gui.IsIconic(self.hwnd):
                return None
            l, t, r, b = win32gui.GetClientRect(self.hwnd)
            sx, sy = win32gui.ClientToScreen(self.hwnd, (l, t))
            w, h = r - l, b - t
            if w < 200 or h < 150:
                return None
            return {"left": sx, "top": sy, "width": w, "height": h}
        except Exception:  # noqa: BLE001
            self.hwnd = None
            return None

    def activate(self):
        import win32gui
        if self.hwnd is None and not self.find():
            return False
        if self.is_foreground():
            return True
        try:
            # ALT 小技巧讓 SetForegroundWindow 在多數情況下生效
            pydirectinput.keyDown("alt"); pydirectinput.keyUp("alt")
            win32gui.SetForegroundWindow(self.hwnd)
            time.sleep(0.25)
        except Exception:  # noqa: BLE001
            pass
        return self.is_foreground()


# ------------------------------------------------------------------ 模型呼叫（串流 + 硬截止）
# 2026-09-14 使用者：**推理全部開啟。**
# 以前一律 enable_thinking=False（每輪 1 秒），但官方模型卡明講：
#   「多輪 agentic 任務中，降低推理努力不一定縮短總完成時間；
#     它讓單輪變快，卻造成分析不足、更多失敗、反覆重試，反而拉高總延遲與 token。」
# 這正是這隻代理的症狀（四萬輪、重複同一動作、報到長期掛零）。
# 這台 DGX 上實測（短 prompt）：關閉 1.0s／low 7.0s／medium 11.0s／xhigh 10.2s。
# 檔位只有 xhigh（預設）、medium、low 三種，而且要放在**請求頂層**，不是 chat_template_kwargs。
# ══ 2026-09-14 使用者定的三層思考 ══
#   反射 = 完全不思考（enable_thinking False）——要馬上有反應的場合
#   快思 = reasoning_effort low ——每一輪的決策
#   慢想 = reasoning_effort xhigh ——慢想、檢討、提案這種不趕時間的
# 這台 DGX 實測（短 prompt）：反射 1.0s／快思 7.0s／慢想 10.2s。
# 檔位只有 xhigh（預設）、medium、low，而且要放在請求**頂層**，不是 chat_template_kwargs。
反射, 快思, 中想, 慢想 = "反射", "快思", "中想", "慢想"
THINK_MODES = {
    # 模式: (要不要思考, effort, temperature, top_p)
    反射: (False, None,    0.70, 0.80),   # 官方給非思考模式的取樣建議
    # 2026-09-21 使用者：**試著把快思的碎唸關掉，記錄差異**。
    # 離線實測（同一批 4 個真實面板＋同一份 42K 前綴，各跑一次）：
    #   碎唸不限 → 中位 35.7s、平均碎唸 494 token、4/4 有動作
    #   關掉碎唸 → 中位  5.6s、碎唸 0、4/4 有動作（「想」欄反而寫得更準）
    #   只寫死規則不截斷 → 中位 31.7s、碎唸 247，但 1/4 把思考寫進答案欄、整輪沒吐 JSON
    #   規則＋384 安全網 → 中位 17.1s、碎唸 272、4/4
    # 關鍵發現：他碎唸時是**用英文**把整張中文面板翻譯＋複述一遍，494 token 幾乎都花在這裡。
    # 面板已經把該想的攤在他眼前了，所以關掉碎唸不會讓他變笨，只是不再抄一遍。
    # 要還原就把下面這一行換回：快思: (True, "low", 0.65, 0.85)
    快思: (False, None,   0.65, 0.85),   # 原本：(True, "low", ...) 官方建議 1.0 但實測會胡言亂語
    中想: (True,  "medium", 0.75, 0.88),  # 2026-09-17：慢想只有 60 秒，xhigh 會想不完（實測中位數 94 秒）
    慢想: (True,  "xhigh", 0.80, 0.90),   # （「牆壁在呼吸」「不不不不是我在抖」那種）
    # 溫度太低會「重複同一個動作」、太高會把幻覺寫進 s 欄位再存成記憶——
    # 0.7~0.9 是這兩個失敗模式之間的區間。
}
EFFORT_DEEP = 慢想          # 舊名字留著，指向慢想


def stream_json(endpoint, model, system_text, content, max_tokens, deadline_at,
                temperature=0.2, effort=快思):
    think, eff, temp, topp = THINK_MODES.get(effort, THINK_MODES[快思])
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system_text}, {"role": "user", "content": content}],
        "max_tokens": max_tokens, "stream": True,
        "stream_options": {"include_usage": True},
        "chat_template_kwargs": {"enable_thinking": think},
        "temperature": temp, "top_p": topp, "top_k": 20,
    }
    if eff:
        payload["reasoning_effort"] = eff
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
            try:
                if hasattr(resp.fp, "raw"):
                    resp.fp.raw._sock.settimeout(max(remaining, 0.05))
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
                piece = ch.get("delta", {}).get("content")
                if piece:
                    if ttft is None:
                        ttft = time.time() - t0
                    text.append(piece)
    finally:
        try:
            resp.close()
        except Exception:  # noqa: BLE001
            pass
    return "".join(text), truncated, ttft, usage


# ------------------------------------------------------------------ 每個遊戲的設定
# 2026-09-15：換遊戲不要再改程式。每個專案放一份 專案/<遊戲>/遊戲.json：
#   全名／作者／places（placeId → 這個場景叫什麼）／大廳place／詞彙（YOLO 一開機就盯的字）
#   ／模組（動物醫院專屬的功能開關：櫃檯・輪班計畫・報到課程・理智）
# 沒有這個檔也能跑，只是程式不知道你在哪個 place、也不會替你關掉別的遊戲的功能。
GAME_CFG_DEFAULT = {
    "全名": "", "作者": "", "places": {}, "大廳place": 0,
    "詞彙": ["wall", "door", "window", "table", "chair", "box", "shelf",
             "person", "sign", "stairs", "button"],
    "模組": {"櫃檯": False, "輪班計畫": False, "報到課程": False, "理智": False,
             "學習課程": False, "任務樹": False},
}


def load_game_cfg(game):
    """讀 專案/<遊戲>/遊戲.json；沒有就回預設（全部遊戲專屬模組關閉）。"""
    cfg = json.loads(json.dumps(GAME_CFG_DEFAULT))
    path = os.path.join(HERE, "專案", game, "遊戲.json")
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        for k, v in d.items():
            if k == "模組" and isinstance(v, dict):
                cfg["模組"].update({str(a): bool(b) for a, b in v.items()})
            else:
                cfg[k] = v
        cfg["places"] = {int(k): str(v) for k, v in (cfg.get("places") or {}).items()}
        print(f"[遊戲] {game}：{cfg.get('全名') or game}"
              + (f"（作者 {cfg['作者']}）" if cfg.get("作者") else "")
              + f"　place {list(cfg['places']) or '未設定'}")
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        cfg["places"] = {}
        print(f"[遊戲] {game}：沒有 遊戲.json，用通用設定（遊戲專屬模組全關）")
    return cfg


# ------------------------------------------------------------------ 代理本體
class Agent:
    def mod(self, name):
        """這個遊戲有沒有開這個專屬模組（櫃檯／輪班計畫／報到課程／理智）。"""
        return bool((getattr(self, "gcfg", None) or {}).get("模組", {}).get(name, False))

    def __init__(self, args):
        self.args = args
        self.cfg = load_config()
        self.endpoint = self.cfg["端點"]
        self.model = self.cfg.get("模型名") or self._detect_model()
        self.game = args.game
        self.deadline = args.deadline
        self.dry = args.dry_run
        self.win = RobloxWindow()
        # 2026-09-14 改成「專案」結構：每個遊戲一個專案，核心（基礎操作／學習模組）共用。
        # 舊路徑 資料/<遊戲>/ 仍然支援，舊專案不用手動搬。
        self.dir = os.path.join(HERE, "專案", self.game, "記憶")
        old_dir = os.path.join(HERE, "資料", self.game)
        if not os.path.isdir(self.dir) and os.path.isdir(old_dir):
            self.dir = old_dir
        os.makedirs(os.path.join(self.dir, "畫面"), exist_ok=True)
        self.gcfg = load_game_cfg(self.game)
        self.notes_path = os.path.join(self.dir, "研究筆記.md")
        self.log_path = os.path.join(self.dir, "行動日誌.jsonl")
        self.notes = self._load_notes()
        self._load_score()
        self.load_calib()
        self.load_core()
        self.load_sop()
        self.death_count = len([1 for l in open(os.path.join(self.dir, "死亡檢討.md"), encoding="utf-8")
                                if l.startswith("## 第")]) if os.path.exists(os.path.join(self.dir, "死亡檢討.md")) else 0
        self.death_note = self._last_death_note()
        self.look_samples = []
        self.pitch = 0
        self.still_since = None
        self.trail = []
        self.move_stats = {}
        self.hold_stats = []
        self.wall_hits = 0
        self.last_turn = 0
        self._look_measured = None
        self.next_reflex = None
        self.summary = []
        try:
            with open(os.path.join(self.dir, "玩法摘要.md"), encoding="utf-8") as f:
                self.summary = [l[2:].strip() for l in f if l.startswith("- ")]
        except OSError:
            pass
        self.history = []          # 最近幾輪 (時間, 看到, 動作摘要)
        self.last_frame = None
        self.stop = threading.Event()
        self.screen_w, self.screen_h = pyautogui.size()
        self.blocked = 0
        self.cycles = 0
        self.last_reflect = time.time()
        self.last_snapshot = 0
        self.repeat = 0
        self.last_key = None
        self.same_count = 0
        self.stuck_hits = 0
        self.last_force_respawn = 0
        self.grabber = ScreenGrabber(self.cfg["螢幕fps"], self.cfg["畫面寬"], None, bbox_fn=self.win.client_bbox)
        self.grabber.start()
        try:
            import keyboard
            keyboard.add_hotkey("ctrl+f12", lambda: self.stop.set())
        except Exception:  # noqa: BLE001
            pass

    def _detect_model(self):
        with urllib.request.urlopen(self.endpoint.rstrip("/") + "/models", timeout=5) as r:
            return json.loads(r.read().decode("utf-8"))["data"][0]["id"]

    def _load_notes(self):
        if os.path.exists(self.notes_path):
            with open(self.notes_path, encoding="utf-8") as f:
                return [l[2:].strip() for l in f if l.startswith("- ")]
        return []

    def add_note(self, note):
        note = (note or "").strip()
        if not note or note in self.notes or len(note) < 4:
            return
        self.notes.append(note)
        with open(self.notes_path, "a", encoding="utf-8") as f:
            if os.path.getsize(self.notes_path) == 0 if os.path.exists(self.notes_path) else True:
                f.write(f"# 「{self.game}」研究筆記（gamer AI 自己寫的）\n\n")
            f.write(f"- {note}  ({datetime.now().strftime('%m/%d %H:%M')})\n")

    def log(self, rec):
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def grab_full(self):
        """抓 Roblox 視窗客戶區的全解析度畫面（找不到視窗就抓全螢幕）。"""
        with (getattr(mss, "MSS", None) or mss.mss)() as sct:
            shot = sct.grab(self.region())
            return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

    def grab_still(self, width):
        """抓一張視窗畫面並縮到指定寬度。"""
        full = self.grab_full()
        return full.resize((width, int(width * full.height / full.width)), Image.BILINEAR)

    def snapshot(self, tag):
        frames = self.grabber.latest(1)
        if not frames:
            return None
        path = os.path.join(self.dir, "畫面", f"{datetime.now().strftime('%H%M%S')}_{tag}.jpg")
        frames[-1][1].save(path, "JPEG", quality=70)
        return path

    # ---------------- 一輪
    def cycle(self):
        t0 = time.time()
        deadline_at = t0 + self.deadline
        frames = self.grabber.latest(self.cfg["每輪張數"])
        if len(frames) < 2:
            time.sleep(0.2)
            return
        cur = frames[-1][1]
        diff = frame_diff(self.last_frame, cur)
        big = self.last_frame is not None and diff > BIG_CHANGE_THRESHOLD
        video_uri, _ = mp4_data_uri([draw_grid(im) for _, im in frames], self.cfg["螢幕fps"])

        if big:
            self.next_reflex = None
            # 看清楚模式：多給一張較清楚的單張圖，不做動作
            hi_uri, _ = jpeg_data_uri(draw_grid(self.grab_still(1024)), 70)
            content = [{"type": "video_url", "video_url": {"url": video_uri}},
                       {"type": "image_url", "image_url": {"url": hi_uri}},
                       {"type": "text", "text": CAREFUL_PROMPT}]
            raw, trunc, ttft, usage = stream_json(self.endpoint, self.model, self.sys_prompt(),
                                                  content, 120, deadline_at)
            obj = parse_json_loose(raw) or {"s": raw[:60], "r": 0}
            self.update_score(str(obj.get("s") or ""))   # 看清楚輪的描述最常帶「輪班 N」與「$」
            shot = self.snapshot("大變化")
            rec = {"時間": datetime.now().strftime("%H:%M:%S"), "模式": "看清楚", "差異": round(diff, 1),
                   "看到": obj.get("s"), "robux": obj.get("r"), "動作": [], "截斷": trunc,
                   "秒": round(time.time() - t0, 2), "圖": shot}
            if obj.get("r") == 1:
                rec["動作"] = [{"t": "key", "k": "esc", "d": 0.05, "強制": "偵測到付費視窗→Esc"}]
                self.execute(rec["動作"])
            self.history.append(f"[{rec['時間']}] 畫面大變化：{obj.get('s')}")
            self.last_frame = cur
            self.log(rec)
            self.print_rec(rec)
            return

        # 一般模式：決策 + 動作
        hist = "\n".join(self.history[-4:]) or "（還沒開始）"
        trail = self.trail_text()
        view = self.view_state() + "；" + self.hold_hint()
        sop_text = ("【你現在的視角】" + view + chr(10) + chr(10)) if view else ""
        sop_text += trail + self.sop_text()
        core_text = ("【核心知識（查過 wiki 與死亡教訓，最重要）】" + chr(10) +
                     chr(10).join(f"- {x}" for x in getattr(self, "core", [])) + chr(10) + chr(10)) if getattr(self, "core", None) else ""
        notes = sop_text + core_text + "【最近筆記】" + chr(10) + (chr(10).join(f"- {n}" for n in self.notes[-2:]) or "（還沒有）")
        instr = ""
        try:
            with open(os.path.join(self.dir, "任務指示.txt"), encoding="utf-8") as f:
                instr = f.read().strip()
        except OSError:
            pass
        instr_text = f"【來自使用者的任務指示，優先照做】\n{instr}\n\n" if instr else ""
        sc = getattr(self, "score", {})
        instr_text += (f"【你的成績】最高輪班 {sc.get('最高輪班', 0)}、最高金幣 ${sc.get('最高金幣', 0)}、完成輪班報告 {sc.get('輪班報告次數', 0)} 次；"
                       f"現在輪班 {sc.get('目前輪班', '?')}、現在金幣 ${sc.get('目前金幣', '?')}。目標是刷新最高輪班與金幣。" + chr(10) + chr(10))
        if getattr(self, "death_note", ""):
            instr_text += "【上次死因與檢討】" + chr(10) + self.death_note + chr(10) + chr(10)
        if getattr(self, "plan", ""):
            instr_text += "【你自己擬的計畫】" + chr(10) + self.plan + chr(10) + chr(10)
        user_text = (f"{instr_text}最近幾輪：\n{hist}\n\n研究筆記摘要：\n{notes}\n\n"
                     f"這是最近 1 秒的畫面。決定下一步，只輸出 JSON。")
        still = self.grab_still(1024)
        still_uri, _ = jpeg_data_uri(draw_grid(still), 70)
        content = [{"type": "video_url", "video_url": {"url": video_uri}},
                   {"type": "image_url", "image_url": {"url": still_uri}},
                   {"type": "text", "text": "（影片＝最近 1 秒動態；單張圖＝現在的清楚畫面；淡黃格線是程式畫的座標尺，不是遊戲畫面的一部分，報座標以它為準即可）" + chr(10) + user_text}]
        reflex = getattr(self, "next_reflex", None)
        reflex_n = [0]
        th = None
        if reflex:
            th = threading.Thread(target=lambda: reflex_n.__setitem__(0, self.run_reflex(reflex, deadline_at - 0.3, cur)), daemon=True)
            th.start()
        raw, trunc, ttft, usage = stream_json(self.endpoint, self.model, self.sys_prompt(),
                                              content, self.args.max_tokens, deadline_at)
        if th:
            th.join(timeout=1.5)
        obj = parse_json_loose(raw) or {"s": "(無法解析)", "r": 0, "a": []}
        try:
            np_ = int(obj.get("p"))
            if getattr(self, "sop", None) and 1 <= np_ <= len(self.sop) and np_ != self.step:
                print(f"   ▶ 工作流程進到第 {np_} 步：{self.sop[np_-1]}")
                self.step = np_; self.save_step()
        except (TypeError, ValueError):
            pass
        nxt = obj.get("c")
        self.next_reflex = nxt if self.reflex_ok(nxt) and obj.get("r") != 1 else None
        actions = obj.get("a") or []
        if not isinstance(actions, list):
            actions = []
        actions, blocked = self.guard(actions, obj.get("r") == 1)
        rec = {"時間": datetime.now().strftime("%H:%M:%S"), "模式": "決策", "差異": round(diff, 1),
               "看到": obj.get("s"), "視角水平": obj.get("v"), "目標": obj.get("o"), "目標位置": obj.get("f"),
               "robux": obj.get("r"), "動作": actions, "擋掉": blocked, "筆記": obj.get("n") or "",
               "反射": (self.describe(reflex) if reflex else None), "反射次數": reflex_n[0],
               "截斷": trunc, "首字秒": round(ttft, 2) if ttft else None, "決策秒": round(time.time() - t0, 2),
               "prompt_tokens": (usage or {}).get("prompt_tokens")}
        self.last_frame = cur
        if obj.get("n"):
            self.add_note(obj["n"])
        seen = str(obj.get("s") or "")
        self.update_score(seen)
        near_wall, tex = self.wall_score()
        if near_wall:
            self.wall_hits = getattr(self, "wall_hits", 0) + 1
            if self.wall_hits >= 2 and time.time() - getattr(self, "last_turn", 0) > 20:
                self.last_turn = time.time()
                self.turn_around(f"畫面紋理只有 {tex:.0f}，臉貼牆")
                self.wall_hits = 0
                actions = []
                rec["動作"] = [{"t": "轉身180", "原因": "貼牆"}]
        else:
            self.wall_hits = 0
        # 畫面幾乎沒變化：累積靜止時間，超過 10 秒就強制思考
        if diff < 6.0:
            if getattr(self, "still_since", None) is None:
                self.still_since = t0
            still = time.time() - self.still_since
            if still >= 10.0:
                self.emergency_think(f"畫面已經 {int(still)} 秒完全沒變化，我可能卡住或做錯了")
                self.still_since = time.time()
        else:
            self.still_since = None
        if any(k in seen for k in ("我死了", "死亡", "倒地", "理智0%", "理智 0%", "理智歸零", "自我復活", "前往大廳"))                 and time.time() - getattr(self, "last_death", 0) > 180:
            self.last_death = time.time()
            self.death_count = getattr(self, "death_count", 0) + 1
            try:
                self.death_review()
                self.emergency_think("剛死掉，要重新規劃")
            except Exception as e:  # noqa: BLE001
                print(f"   死亡檢討失敗: {e}")
        self.stuck_hits = (self.stuck_hits + 1) if any(k in seen for k in ("天花板", "卡牆", "仰視", "卡住", "動不了", "重生", "貼牆", "朝下", "地磚", "地板")) else max(0, self.stuck_hits - 1)
        if self.stuck_hits >= 3 and time.time() - self.last_force_respawn > 120:
            self.emergency_think("卡住無法前進")
            self.unstuck_count = getattr(self, "unstuck_count", 0) + 1
            if self.unstuck_count >= 3 and time.time() - getattr(self, "last_rejoin", 0) > 900:
                self.force_leave()
                self.unstuck_count = 0
                rec["動作"] = [{"t": "強制離開重進", "原因": "脫困走位 3 次仍卡牆"}]
            else:
                self.force_respawn()
                rec["動作"] = [{"t": "脫困走位", "原因": "連續多輪卡牆/仰視天花板"}]
            self.stuck_hits = 0
            actions = []
        elif self.stuck_hits == 0 and getattr(self, "unstuck_count", 0) and time.time() - self.last_force_respawn > 600:
            self.unstuck_count = 0  # 十分鐘沒再卡就歸零
        self.execute(actions)
        try:
            self.track_action(actions, cur)
        except Exception:  # noqa: BLE001
            pass
        rec["總秒"] = round(time.time() - t0, 2)
        summ = "、".join(self.describe(a) for a in actions) or "沒動作"
        # 同樣的動作連做三次畫面都沒變 → 提醒模型換方法（避免卡死重複）
        key = json.dumps([{k: v for k, v in a.items() if k in ("t", "k", "g")} for a in actions], ensure_ascii=False)
        self.repeat = self.repeat + 1 if key == getattr(self, "last_key", None) and key != "[]" else 0
        self.last_key = key
        hint = f"　⚠ 這個動作已連續做了 {self.repeat + 1} 次都沒效果，請換不同的做法（換位置、換按鍵、或先觀察）" if self.repeat >= 2 else ""
        self.history.append(f"[{rec['時間']}] 看到：{obj.get('s')} → 做：{summ}" + ("（有動作被擋）" if blocked else "") + hint)
        self.log(rec)
        self.print_rec(rec)

    def force_respawn(self):
        """程式端脫困走位（這款遊戲的 R 重生被停用）：後退、側移、鏡頭拉遠、往下看；若選單開著先關掉。"""
        self.last_force_respawn = time.time()
        print("   🛟 程式端脫困走位（卡牆/仰視太久）")
        if self.dry or not self.win.activate():
            return
        try:
            pydirectinput.keyDown("s"); time.sleep(2.0); pydirectinput.keyUp("s")
            pydirectinput.keyDown("d"); time.sleep(1.0); pydirectinput.keyUp("d")
            for _ in range(3):  # 鏡頭拉到最遠（第三人稱），貼牆/貼地時最有效
                pyautogui.scroll(-10 * 120); time.sleep(0.15)
            # 視角拉回水平。
            # 舊版寫成「先往上 8×25 再往下 5×20」，淨值是 -100（往上）——
            # 脫困走位一天跑幾十次，等於程式自己一直把鏡頭推到天花板，
            # 之後模型再花大量輪次想往下修。現在改成只往下壓，寧可略低頭也不要抬頭。
            self.raw_look(0, 140)
            pydirectinput.keyDown("w"); time.sleep(1.0); pydirectinput.keyUp("w")
        except Exception as e:  # noqa: BLE001
            print(f"   脫困走位失敗: {e}")
        self.pitch = 60      # 脫困後是略微低頭，不要謊報成水平
        self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 程式幫你做了脫困走位（後退、側移、拉遠、往下壓視角、前進）；R 重生在這款遊戲不能用，別再點")
        self.log({"時間": datetime.now().strftime("%H:%M:%S"), "模式": "脫困走位"})

    def force_leave(self):
        """程式端強制離開遊戲（esc → L → enter），之後由模型從首頁重新進入。"""
        self.last_rejoin = time.time()
        self.last_force_respawn = time.time()
        print("   🚪 程式端強制離開遊戲重進（脫困走位多次無效）")
        if self.dry or not self.win.activate():
            return
        try:
            pydirectinput.press("esc"); time.sleep(1.0)
            pydirectinput.press("l"); time.sleep(1.2)
            pydirectinput.press("enter"); time.sleep(3.0)
        except Exception as e:  # noqa: BLE001
            print(f"   強制離開失敗: {e}")
        self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 你卡牆太久，程式已幫你離開遊戲。現在請從 Roblox 首頁的「最近玩過／繼續」找到「動物醫院」點進去按 Play 重新進入，進入後先按 s 後退離開牆邊")
        self.log({"時間": datetime.now().strftime("%H:%M:%S"), "模式": "強制離開重進"})

    # ---------------- 守門
    def guard(self, actions, robux_flag):
        ok, blocked = [], []
        for a in actions[:MAX_ACTIONS_PER_CYCLE]:
            if not isinstance(a, dict):
                continue
            t = a.get("t")
            target = str(a.get("g", "")) + " " + str(a.get("x", "")) if t == "type" else str(a.get("g", ""))
            if t in ("click", "type") and forbidden(target):
                blocked.append({**a, "原因": "目標含付費/Robux 關鍵字"}); continue
            if robux_flag and not (t == "key" and a.get("k") == "esc") and not (t == "click" and any(
                    w in str(a.get("g", "")).lower() for w in ("取消", "關閉", "cancel", "close", "x"))):
                blocked.append({**a, "原因": "有付費視窗時只准 esc/取消"}); continue
            if t == "type":
                txt = str(a.get("x", ""))
                if len(txt) > 20 or "http" in txt.lower() or "聊天" in target or "chat" in target.lower():
                    blocked.append({**a, "原因": "打字內容/目標不允許"}); continue
            if t == "think":
                if time.time() - getattr(self, "last_think", 0) < 120:
                    blocked.append({**a, "原因": "think 每 2 分鐘最多一次"}); continue
                ok.append(a); continue
            if t in ("wiki", "看介紹"):
                if time.time() - getattr(self, "last_wiki", 0) < 10 * 60:
                    blocked.append({**a, "原因": "查資料／看介紹 每 10 分鐘最多一次"}); continue
                ok.append(a); continue
            if t == "youtube":
                # 使用者要求：YouTube 全面停用（影片容易被誤導），只准查文字 wiki
                blocked.append({**a, "原因": "YouTube 已停用，改用 wiki 查文字攻略"}); continue
            # 2026-09-22 使用者：**允許他所有按鍵**。原本的白名單連 l（退出重進）都沒有，
            # 文件教的做法送了就被擋。現在只擋 pydirectinput 根本不認得的鍵名。
            if t in ("key", "hold") and str(a.get("k", "")).lower() not in pydirectinput.KEYBOARD_MAPPING:
                blocked.append({**a, "原因": "pydirectinput 不認得這個鍵名"}); continue
            ok.append(a)
        if blocked:
            self.blocked += len(blocked)
            if robux_flag and not ok:
                ok = [{"t": "key", "k": "esc", "d": 0.05, "強制": "付費視窗→Esc"}]
        return ok, blocked

    # ---------------- 點擊前看圖確認（防誤買）
    def needs_verify(self, a):
        g = str(a.get("g", "")).strip().lower()
        return (not g) or any(w in g for w in CONFIRM_WORDS)

    def verify_click(self, a):
        """抓全解析度畫面，畫紅框＋局部放大，問模型會不會花錢。回傳 (可以點?, 原因)。"""
        from PIL import ImageDraw
        full = self.grab_full()
        fx = int(max(0.0, min(1.0, float(a["x"]))) * full.width)
        fy = int(max(0.0, min(1.0, float(a["y"]))) * full.height)
        box = (max(0, fx - 320), max(0, fy - 200), min(full.width, fx + 320), min(full.height, fy + 200))
        crop = full.crop(box)
        marked = full.copy()
        d = ImageDraw.Draw(marked)
        d.rectangle((fx - 40, fy - 25, fx + 40, fy + 25), outline=(255, 0, 0), width=6)
        marked = marked.resize((1024, int(1024 * full.height / full.width)))
        d2 = ImageDraw.Draw(crop)
        cx, cy = fx - box[0], fy - box[1]
        d2.rectangle((cx - 40, cy - 25, cx + 40, cy + 25), outline=(255, 0, 0), width=4)
        u1, _ = jpeg_data_uri(marked, 70)
        u2, _ = jpeg_data_uri(crop, 85)
        content = [{"type": "image_url", "image_url": {"url": u1}},
                   {"type": "image_url", "image_url": {"url": u2}},
                   {"type": "text", "text": VERIFY_PROMPT}]
        raw, trunc, _, _ = stream_json(self.endpoint, self.model, self.sys_prompt(),
                                       content, 60, time.time() + 4.0, 0.0)
        obj = parse_json_loose(raw) or {}
        btn = str(obj.get("b", ""))
        pay = obj.get("p", 1)
        path = os.path.join(self.dir, "畫面", f"{datetime.now().strftime('%H%M%S')}_確認_{'擋' if pay == 1 else '放行'}.jpg")
        crop.save(path, "JPEG", quality=80)
        if trunc and not obj:
            return False, "確認看圖逾時，保守不點"
        if pay == 1 or forbidden(btn):
            return False, f"看圖確認：按鈕「{btn}」可能花錢"
        return True, f"看圖確認：按鈕「{btn}」不花錢"

    def locate(self, target):
        """用高解析度＋格線的單張圖，請模型重新定位目標。回傳 (x, y) 或 None。"""
        full = self.grab_full()
        im = draw_grid(full.resize((1024, int(1024 * full.height / full.width))))
        uri, _ = jpeg_data_uri(im, 75)
        raw, _, _, _ = stream_json(self.endpoint, self.model, self.sys_prompt(),
                                   [{"type": "image_url", "image_url": {"url": uri}},
                                    {"type": "text", "text": LOCATE_PROMPT.format(target=target)}],
                                   40, time.time() + 4.0, 0.0)
        obj = parse_json_loose(raw) or {}
        try:
            if obj.get("f", 1) == 0:
                return None
            return float(obj["x"]), float(obj["y"])
        except (KeyError, TypeError, ValueError):
            return None

    # ---------------- 執行
    def describe(self, a):
        t = a.get("t")
        if t == "click":
            return f"點「{a.get('g', '')}」({a.get('x')},{a.get('y')})"
        if t == "move":
            return f"移滑鼠到({a.get('x')},{a.get('y')})"
        if t == "aim":
            return f"準心對準「{a.get('g', '')}」({a.get('x')},{a.get('y')})"
        if t == "hold":
            return f"長按 {str(a.get('k', 'e')).upper()} 互動「{a.get('g', '')}」" + (f"（{a['結果']}）" if a.get("結果") else "")
        if t == "key":
            return f"按 {a.get('k')} {a.get('d', 0.1)}s"
        if t == "look":
            return f"轉視角 {a.get('dx', 0)},{a.get('dy', 0)}"
        if t == "scroll":
            return f"滾輪 {a.get('v', 0)}"
        if t == "type":
            return f"在「{a.get('g', '')}」打「{a.get('x', '')}」"
        if t == "wait":
            return f"等 {a.get('d', 1)}s"
        if t == "youtube":
            return f"去 YouTube 查「{a.get('q', '')}」"
        if t == "wiki":
            return f"查攻略「{a.get('q', '')}」"
        if t == "看介紹":
            return "去看這個遊戲自己的 Roblox 頁面（介紹／圖片／說明）"
        if t == "think":
            return f"停下來想「{a.get('q', '')}」"
        return str(a)

    def remember_act(self, actions):
        """記住這一輪最後一個真的動到遊戲的動作，卡住時可以原封不動重做一次。"""
        for a in reversed(actions or []):
            if isinstance(a, dict) and a.get("t") in ("key", "hold", "click", "look"):
                self._last_act = dict(a)
                self._last_act_at = time.time()
                return

    # ---------------- 行動軌跡（帶時間戳的細流水帳）
    #
    # 2026-09-12 使用者指示：檢討的時候他常常亂歸因（把「死掉」怪到剛好在畫面上的
    # 購買面板），因為檢討時餵給他的只有「每輪一條」的摘要與當下那張畫面，
    # 看不到出事前那三十秒到底按了什麼。這裡記的是**原子動作**：
    # 每一次按鍵、轉視角、點擊、長按、扣分、死亡，都帶秒級時間戳，
    # 死亡檢討與扣分檢討會把出事前 N 秒原封不動攤給他看。
    TRACE_MAX = 500

    def trace_add(self, text, kind="動作"):
        tr = getattr(self, "trace", None)
        if tr is None:
            tr = self.trace = []
        tr.append((time.time(), str(kind)[:6], str(text)[:90]))
        if len(tr) > self.TRACE_MAX:
            del tr[:len(tr) - self.TRACE_MAX]

    def trace_text(self, secs=30.0, end=None, limit=45, label="出事"):
        """出事前 secs 秒的流水帳，時間寫成「出事前幾秒」。

        end＝出事的時刻（預設現在）。檢討本身會花四十幾秒，所以一定要用
        出事當下的時間往前抓，不然抓到的全是檢討期間的空白。
        """
        tr = getattr(self, "trace", None) or []
        t1 = float(end or time.time())
        rows = [(t, k, s) for (t, k, s) in tr if 0 <= t1 - t <= secs]
        if not rows:
            return "（這段時間沒有紀錄）"
        rows = rows[-limit:]
        out = []
        for t, k, s in rows:
            out.append(f"　{label}前 {t1 - t:4.1f} 秒　[{k}] {s}")
        return chr(10).join(out)

    def execute(self, actions):
        if not actions:
            return
        if self.dry:
            print("   （dry-run，不執行）")
            return
        if not self.win.activate():
            print("   ⚠ Roblox 視窗不在前景，這輪不動作")
            return
        for a in actions:
            t = a.get("t")
            if t in ("key", "click", "wait", "hold", "scroll"):
                # 2026-09-17：留一份「可重播的動作流水帳」，錄成程序時直接用
                try:
                    if hasattr(self, "act_log"):
                        self.act_log(a)
                except Exception:  # noqa: BLE001
                    pass
            if t != "look":              # 轉視角由 raw_look 自己記，這裡記會變兩筆
                try:
                    self.trace_add(self.describe(a))
                except Exception:  # noqa: BLE001
                    pass
            try:
                if t == "click" and getattr(self, "repeat", 0) >= 1 and a.get("g"):
                    loc = self.locate(a["g"])
                    if loc:
                        print(f"   📍 重新定位「{a['g']}」：({a.get('x')},{a.get('y')}) → ({loc[0]:.2f},{loc[1]:.2f})")
                        a["x"], a["y"] = loc
                if t == "click" and self.needs_verify(a):
                    ok, why = self.verify_click(a)
                    a["確認"] = why
                    print(f"   🔍 {why}")
                    if not ok:
                        self.blocked += 1
                        continue
                if t == "click":
                    ok_r, why_r = self.reachable(float(a["x"]), float(a["y"]))
                    if not ok_r:
                        print(f"   🚫 點不到 ({a['x']}, {a['y']}) —— {why_r}")
                        a["結果"] = f"點不到：{why_r}"
                        self.offscreen_hit = getattr(self, "offscreen_hit", 0) + 1
                        continue
                    x, y = self.to_screen(a["x"], a["y"])
                    # Roblox 的 UI 對「瞬間按放」常常沒反應：先移過去停一下，按住 80ms 再放
                    move_safe(x, y)
                    time.sleep(0.12)
                    pydirectinput.moveRel(1, 0, relative=True)
                    time.sleep(0.05)
                    pydirectinput.mouseDown(button="left")
                    time.sleep(0.08)
                    pydirectinput.mouseUp(button="left")
                    time.sleep(0.15)
                elif t == "hold":
                    ok_h, held = self.hold_interact(str(a.get("k", "e")).lower())
                    a["結果"] = f"按了 {held}s，{'成功' if ok_h else '沒反應'}"
                elif t == "aim":
                    self.aim(float(a["x"]), float(a["y"]))
                elif t == "move":
                    x, y = self.to_screen(a["x"], a["y"])
                    move_safe(x, y)
                    time.sleep(0.1)
                elif t == "key":
                    k = str(a["k"]).lower()
                    d = max(0.05, min(MAX_KEY_HOLD, float(a.get("d", 0.1))))
                    pydirectinput.keyDown(k); time.sleep(d); pydirectinput.keyUp(k)
                elif t == "look":
                    self._look_measured = self.grab_still(320)
                    # 2026-09-14 使用者：**取消限制，讓他隨意轉頭。**
                    # 舊上限（左右 ±400、往上只有 100）是當初怕鏡頭被推到天花板才加的，
                    # 但實測 dy 50 只有 7 度——上限 100 等於「往上一次最多 15 度」，
                    # 他從地板爬回水平要送四五輪，每一輪都在被扣分。
                    # raw_look 本來就會把大位移拆成好幾段、每段都把游標移回視窗中央，
                    # 所以放大不會觸發 fail-safe。留一個很寬的護欄純粹是防呆（轉一圈半）。
                    dx = int(max(-2400, min(2400, float(a.get("dx", 0)))))
                    dy = int(max(-1200, min(1200, float(a.get("dy", 0)))))
                    self.raw_look(dx, dy)           # 絕對座標拖曳
                    self.after_look(dx, dy)
                elif t == "scroll":
                    pyautogui.scroll(int(max(-10, min(10, float(a.get("v", 0))))) * 120)
                elif t == "type":
                    txt = str(a["x"])
                    if txt.isascii():
                        pydirectinput.typewrite(txt, interval=0.03)
                    else:
                        try:
                            import keyboard
                            keyboard.write(txt, delay=0.03)   # Unicode SendInput，中文可用
                        except Exception:  # noqa: BLE001
                            import pyperclip
                            pyperclip.copy(txt)
                            pydirectinput.keyDown("ctrl"); pydirectinput.press("v"); pydirectinput.keyUp("ctrl")
                elif t == "wait":
                    time.sleep(max(0.1, min(3.0, float(a.get("d", 1)))))
                elif t == "youtube":
                    print("   🚫 YouTube 已停用（使用者要求），這個動作不做")
                elif t == "wiki":
                    self.youtube_session(str(a.get("q") or f"{self.game} roblox wiki"), kind="wiki")
                elif t == "看介紹":
                    self.youtube_session("", kind="介紹")
                elif t == "think":
                    self.think(str(a.get("q") or "現在該做什麼？"))
            except pyautogui.FailSafeException:
                raise
            except Exception as e:  # noqa: BLE001
                print(f"   動作失敗 {a}: {e}")

    # ---------------- 最近做過什麼（避免重複無效動作）
    def track_action(self, actions, before_frame):
        """把這一輪做的動作與它造成的畫面變化記進軌跡。"""
        self.remember_act(actions)
        try:
            if hasattr(self, "score_behaviour"):
                self.score_behaviour(actions)      # 不擋他，只記分
        except Exception as e:  # noqa: BLE001
            print(f"   行為計分失敗：{e}")
        if not hasattr(self, "trail"):
            self.trail = []
        time.sleep(0.15)
        after = self.grabber.latest(1)
        effect = frame_diff(before_frame, after[-1][1]) if after and before_frame is not None else 0.0
        for a in actions or []:
            t = a.get("t")
            label = self.describe(a)
            if t == "key":
                d = float(a.get("d", 0.1))
                self.move_stats.setdefault(str(a.get("k", "")).lower(), []).append((d, effect))
            self.trail.append({"時": datetime.now().strftime("%H:%M:%S"), "做": label, "效果": round(effect, 1)})
        self.trail = self.trail[-10:]

    def trail_text(self):
        tr = getattr(self, "trail", [])
        if not tr:
            return ""
        lines = []
        for x in tr[-6:]:
            e = x["效果"]
            tag = "沒變化" if e < 6 else ("有點變" if e < 20 else "畫面大變")
            lines.append(f"- {x['時']} {x['做']} → {tag}(Δ{e})")
        t = "【你剛才做了什麼、有沒有效果】" + chr(10) + chr(10).join(lines)
        # 重複警告
        recent = [x["做"] for x in tr[-6:]]
        for m in set(recent):
            if recent.count(m) >= 3 and all(x["效果"] < 6 for x in tr[-6:] if x["做"] == m):
                t += chr(10) + f"⚠ 你已經連續做了 {recent.count(m)} 次「{m}」而且畫面幾乎沒變——這個做法沒用，換別的。"
                break
        ms = getattr(self, "move_stats", {})
        walk = []
        for k in ("w", "s", "a", "d"):
            v = [e for d, e in ms.get(k, [])[-12:] if d > 0]
            if len(v) >= 3:
                import statistics as st
                walk.append(f"{k.upper()} 平均一次帶來 Δ{st.median(v):.0f} 的畫面變化")
        if walk:
            t += chr(10) + "【移動經驗】" + "；".join(walk) + "；按住越久走越遠，1 秒約走 1~2 步，要走遠就用 d=1.5~3。"
        return t + chr(10) + chr(10)

    # ---------------- 我現在的視角（給模型理解自己朝哪裡看）
    def dy_recent_sum(self):
        """最近一分鐘往上／往下轉了多少（正＝淨往下轉）。"""
        now = time.time()
        return sum(d for t, d in (getattr(self, "_dy_recent", None) or []) if now - t <= 60)

    def view_state(self):
        """用畫面上下半部的內容推估鏡頭朝向，回傳一句話。"""
        desc = []
        try:
            import numpy as np
            im = self.grab_still(320)
            g = np.asarray(im.convert("L"), dtype=np.float32)
            h = g.shape[0]
            top, bot = g[: h // 3], g[-h // 3:]
            # 紋理量（標準差）：地板通常有格線紋理，天花板/天空通常平坦
            st_top, st_bot = float(top.std()), float(bot.std())
            br_top, br_bot = float(top.mean()), float(bot.mean())
            if st_top < 14 and st_bot > st_top * 1.6:
                desc.append("鏡頭偏上（上半部一片平坦，多半是天花板或天空）")
            elif st_bot < 14 and st_top > st_bot * 1.6:
                desc.append("鏡頭偏下（下半部一片平坦，多半是貼著地板）")
            else:
                desc.append("鏡頭大致水平（上下都看得到東西）")
            if br_top + br_bot < 90:
                desc.append("畫面偏暗")
        except Exception:  # noqa: BLE001
            pass
        try:
            nw, tex = self.wall_score()
            desc.append(f"畫面紋理 {tex:.0f}" + ("（太單調，你可能臉貼著牆，先退開並轉身）" if nw else ""))
        except Exception:  # noqa: BLE001
            pass
        pitch = getattr(self, "pitch", 0)
        if pitch <= -120:
            desc.append(f"你累積往上看了 {abs(pitch)}（偏高，想看路要用正的 dy 往下壓）")
        elif pitch >= 120:
            desc.append(f"你累積往下看了 {pitch}（偏低，想看遠一點用負的 dy）")
        else:
            desc.append("俯仰接近中間")
        r = getattr(self, "px_per_pct", None)
        if r:
            desc.append(f"實測：拖 {int(r)} 像素 ≈ 畫面轉 1%，轉半個畫面 ≈ dx {int(r*50)}")
        return "；".join(desc)

    # ---------------- 貼牆偵測與脫離
    WALL_STD = 11.0        # 整張畫面灰階標準差低於此值 → 幾乎沒有內容，多半是貼著牆
    def wall_score(self):
        """回傳 (是否貼牆, 紋理值)。畫面中央大片單調＝臉貼牆。"""
        try:
            import numpy as np
            g = np.asarray(self.grab_still(320).convert("L"), dtype=np.float32)
            h, w = g.shape
            centre = g[h // 4: h * 3 // 4, w // 4: w * 3 // 4]
            return float(centre.std()) < self.WALL_STD, float(centre.std())
        except Exception:  # noqa: BLE001
            return False, 99.0

    def turn_around(self, why="貼牆"):
        """轉身 180 度並後退一步，脫離牆面。"""
        r = getattr(self, "px_per_pct", None) or 12.0
        dx = int(max(400, min(1400, r * 100)))     # 轉一個完整畫面寬 ≈ 180 度視野
        print(f"   ↩ {why}：轉身 180 度（dx≈{dx}）並退開")
        if self.dry or not self.win.activate():
            return
        try:
            pydirectinput.keyDown("s"); time.sleep(0.8); pydirectinput.keyUp("s")
            self.raw_look(dx, 0, steps=10)
            self.raw_look(dx, 0, steps=10)     # 兩次，確保接近 180 度
            self.raw_look(0, 60)               # 轉身後順手往下壓一點，避免留在抬頭狀態
            self.pitch = 40
            pydirectinput.keyDown("w"); time.sleep(0.6); pydirectinput.keyUp("w")
        except Exception as e:  # noqa: BLE001
            print(f"   轉身失敗: {e}")
        self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 你臉貼在牆上，程式幫你轉身 180 度並退開；之後走路要離牆遠一點")
        self.log({"時間": datetime.now().strftime("%H:%M:%S"), "模式": "轉身脫離牆面", "原因": why})

    # ---------------- 長按互動（按到成功為止）
    def hold_interact(self, key="e", max_hold=4.0, need=12.0):
        """按住鍵直到畫面明顯改變（互動完成）或超過 max_hold。回傳 (成功?, 實際按了幾秒)。"""
        frames = self.grabber.latest(1)
        base = frames[-1][1] if frames else None
        t0 = time.time()
        changed = False
        try:
            pydirectinput.keyDown(key)
            while time.time() - t0 < max_hold:
                time.sleep(0.25)
                f = self.grabber.latest(1)
                if f and base is not None and frame_diff(base, f[-1][1]) > need:
                    changed = True
                    time.sleep(0.15)   # 多按一下確保進度條走完
                    break
        finally:
            try:
                pydirectinput.keyUp(key)
            except Exception:  # noqa: BLE001
                pass
        held = round(time.time() - t0, 2)
        # 記住這個互動要按多久
        if changed:
            self.hold_stats.append(held)
            self.hold_stats = self.hold_stats[-20:]
        print(f"   {'✅' if changed else '⌛'} 長按 {key.upper()} {held}s{'（畫面有反應）' if changed else '（沒反應）'}")
        return changed, held

    def hold_hint(self):
        hs = getattr(self, "hold_stats", [])
        if len(hs) < 3:
            return "長按 E 的經驗還不夠；不確定就用 hold 動作，程式會幫你按到有反應為止。"
        import statistics as st
        return f"實測：長按 E 平均要 {st.median(hs):.1f} 秒才會完成（最短 {min(hs):.1f}、最長 {max(hs):.1f}）。"

    # ---------------- 準心對位
    def aim(self, rx, ry, tries=2):
        """把畫面正中央的準心轉到 (rx, ry)：用實測靈敏度換算，做完再量一次校正。"""
        for i in range(tries):
            ex, ey = rx - 0.5, ry - 0.5
            if abs(ex) < 0.03 and abs(ey) < 0.05:
                break
            # 每「1% 畫面」需要的拖曳像素；沒校準時用保守估計
            r = getattr(self, "px_per_pct", None) or 12.0
            dx = int(max(-350, min(350, ex * 100 * r)))
            dy = int(max(-120, min(300, ey * 100 * r * 0.9)))
            before = self.grab_still(320)
            self.raw_look(dx, dy, steps=6)
            self.pitch = getattr(self, "pitch", 0) + dy
            time.sleep(0.15)
            # 用畫面實際位移校正靈敏度
            try:
                import numpy as np, cv2, statistics as st
                a1 = np.asarray(before.convert("L"), dtype=np.uint8)
                # 對著平牆／天花板時畫面沒有特徵，量出來會是「轉了 0%」→ 靈敏度被推到爆表。
                # 紋理太低就不要拿這一次來校正。
                if float(a1.std()) < 18.0:
                    raise ValueError("畫面太單調，不拿來校正靈敏度")
                b1 = np.asarray(self.grab_still(320).convert("L"), dtype=np.uint8)
                orb = cv2.ORB_create(500)
                ka, da = orb.detectAndCompute(a1, None); kb, db = orb.detectAndCompute(b1, None)
                if da is not None and db is not None and len(ka) > 8 and len(kb) > 8 and abs(dx) >= 40:
                    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
                    ms = sorted(bf.match(da, db), key=lambda m: m.distance)[:60]
                    xs = [kb[m.trainIdx].pt[0] - ka[m.queryIdx].pt[0] for m in ms]
                    if len(xs) >= 8:
                        mx = st.median(xs)
                        good = [x for x in xs if abs(x - mx) < 25]
                        if len(good) >= 6:
                            moved_pct = abs(st.median(good)) / 320 * 100
                            if moved_pct > 0.5:
                                new_r = abs(dx) / moved_pct
                                # 這款遊戲牆面紋理重複，ORB 量測偶爾會歪掉（量出 23 像素/1%，實際 3~4），
                                # 所以更新後鎖在合理區間，避免靈敏度飄掉讓每次轉頭都甩過頭。
                                _r = new_r if not getattr(self, "px_per_pct", None) else                                      0.7 * self.px_per_pct + 0.3 * new_r
                                self.px_per_pct = max(2.0, min(10.0, _r))
                                self.look_samples.append((abs(dx), moved_pct))
                                if len(self.look_samples) % 8 == 0:
                                    self.update_look_hint()
            except Exception:  # noqa: BLE001
                pass
        return True

    # ---------------- 反射層（模型思考時每 0.4 秒動一次）
    REFLEX_PERIOD = 1.0
    REFLEX_STOP_DIFF = 18.0

    def reflex_ok(self, a):
        if not isinstance(a, dict):
            return False
        t = a.get("t")
        if t == "key":
            return str(a.get("k", "")).lower() in ("w", "a", "s", "d", "e")
        return t in ("look", "wait")

    def run_reflex(self, action, until_ts, base_frame):
        """在模型思考期間重複執行一個安全的連續動作；畫面大變或時間到就停。"""
        if not self.reflex_ok(action) or self.dry:
            return 0
        n = 0
        while time.time() < until_ts and not self.stop.is_set():
            time.sleep(self.REFLEX_PERIOD)
            if time.time() >= until_ts:
                break
            frames = self.grabber.latest(1)
            if frames and base_frame is not None and frame_diff(base_frame, frames[-1][1]) > self.REFLEX_STOP_DIFF:
                print("   ⚡ 反射判斷：畫面變了，交回給模型")
                break
            if action.get("t") == "key" and str(action.get("k", "")).lower() in ("w", "a", "s", "d"):
                near, tex = self.wall_score()
                if near:
                    print(f"   ⚡ 反射判斷：前面是牆（紋理 {tex:.0f}），停止前進")
                    self.wall_hits = getattr(self, "wall_hits", 0) + 2
                    break
            if frames and getattr(self, "_reflex_prev", None) is not None and                     frame_diff(self._reflex_prev, frames[-1][1]) < 2.0:
                self._reflex_still = getattr(self, "_reflex_still", 0) + 1
                if self._reflex_still >= 3:
                    print("   ⚡ 反射判斷：走不動了，停止")
                    self._reflex_still = 0
                    break
            else:
                self._reflex_still = 0
            if frames:
                self._reflex_prev = frames[-1][1]
            if not self.win.is_foreground():
                break
            try:
                t = action.get("t")
                if t == "key":
                    k = str(action["k"]).lower()
                    d = max(0.05, min(0.5, float(action.get("d", 0.3))))
                    pydirectinput.keyDown(k); time.sleep(d); pydirectinput.keyUp(k)
                elif t == "look":
                    dx = int(max(-200, min(200, float(action.get("dx", 0)))))
                    dy = int(max(-60, min(200, float(action.get("dy", 0)))))
                    self.raw_look(dx, dy, steps=4)
                    self.pitch = getattr(self, "pitch", 0) + dy
            except pyautogui.FailSafeException:
                raise
            except Exception:  # noqa: BLE001
                break
            n += 1
        return n

    # ---------------- 工作流程 SOP
    SOP_PATH = "工作流程.md"

    def load_sop(self):
        self.sop = []
        self.step = 1
        f = os.path.join(self.dir, self.SOP_PATH)
        if os.path.exists(f):
            for l in open(f, encoding="utf-8"):
                l = l.strip()
                m = re.match(r"^(\d+)[.、)]\s*(.+)$", l)
                if m:
                    self.sop.append(m.group(2).strip())
        try:
            self.step = int(json.load(open(os.path.join(self.dir, "進度.json"), encoding="utf-8")).get("步驟", 1))
        except (OSError, json.JSONDecodeError, ValueError):
            self.step = 1

    def make_sop(self):
        """開場先想：請模型依核心知識與筆記，列出這款遊戲的工作流程步驟。"""
        print("   📋 開場思考：擬定工作流程")
        raw, _, _, _ = stream_json(self.endpoint, self.model, "你是正在玩 Roblox 遊戲的玩家，只輸出 JSON。",
            [{"type": "text", "text":
              f"你要玩「{self.game}」，目標是賺最多遊戲幣，且任何錢都不准花。" + chr(10) +
              "以下是你已知的核心知識與玩法摘要：" + chr(10) +
              chr(10).join(f"- {x}" for x in getattr(self, "core", [])) + chr(10) +
              chr(10).join(f"- {x}" for x in getattr(self, "summary", [])) + chr(10) + chr(10) +
              "請列出一份「每一輪班從頭到尾要做的事」的工作流程，8~12 步，每步 <=25 字、具體可執行、照順序做；" +
              "再列出 4~6 條「緊急狀況與立即處置」。只輸出 JSON：" +
              '{"sop":["1. ...","2. ..."],"emergency":["狀況→處置", "..."]}'}],
            900, time.time() + 120.0, 0.2)
        obj = parse_json_loose(raw) or {}
        sop = [str(x) for x in (obj.get("sop") or []) if str(x).strip()][:12]
        emg = [str(x) for x in (obj.get("emergency") or []) if str(x).strip()][:6]
        if not sop:
            return
        self.sop = [re.sub(r"^\d+[.、)]\s*", "", x).strip() for x in sop]
        self.emergency_rules = emg
        with open(os.path.join(self.dir, self.SOP_PATH), "w", encoding="utf-8") as f:
            f.write(f"# 「{self.game}」工作流程（{datetime.now().strftime('%m/%d %H:%M')} 由它自己擬定）" + chr(10) + chr(10))
            f.write("## 一輪班的步驟" + chr(10))
            for i, x in enumerate(self.sop, 1):
                f.write(f"{i}. {x}" + chr(10))
            f.write(chr(10) + "## 緊急狀況與處置" + chr(10))
            for x in emg:
                f.write(f"- {x}" + chr(10))
        self.step = 1
        self.save_step()
        print(f"   📋 工作流程 {len(self.sop)} 步、緊急規則 {len(emg)} 條 → {self.SOP_PATH}")
        self.log({"時間": datetime.now().strftime("%H:%M:%S"), "模式": "擬定工作流程", "步驟數": len(self.sop), "步驟": self.sop})

    def save_step(self):
        try:
            json.dump({"步驟": self.step, "更新": datetime.now().strftime("%m/%d %H:%M:%S")},
                      open(os.path.join(self.dir, "進度.json"), "w", encoding="utf-8"), ensure_ascii=False)
        except OSError:
            pass

    def sop_text(self):
        if not getattr(self, "sop", None):
            return ""
        lines = []
        for i, x in enumerate(self.sop, 1):
            mark = "👉" if i == self.step else "　"
            lines.append(f"{mark}{i}. {x}")
        emg = getattr(self, "emergency_rules", [])
        t = "【工作流程（照順序做，👉是你現在該做的那步）】" + chr(10) + chr(10).join(lines)
        if emg:
            t += chr(10) + "【緊急狀況】" + chr(10) + chr(10).join(f"- {x}" for x in emg)
        return t + chr(10) + chr(10)

    # ---------------- 核心記憶（每輪必回灌）
    def load_core(self):
        """核心知識 = 手寫核心知識.md（若有）＋ 所有（wiki）筆記＋最近 3 次死亡檢討的「下次避免」。"""
        core = []
        f_core = os.path.join(self.dir, "核心知識.md")
        if os.path.exists(f_core):
            core += [l[2:].strip() for l in open(f_core, encoding="utf-8") if l.startswith("- ")]
        core += [n.split("  (")[0] for n in self.notes if n.startswith("（wiki）")]
        f_death = os.path.join(self.dir, "死亡檢討.md")
        if os.path.exists(f_death):
            avoid = [l.split("：", 1)[1].strip() for l in open(f_death, encoding="utf-8") if l.startswith("- 下次避免：")]
            for a in avoid[-3:]:
                core.append(f"死過的教訓：{a}")
        # 去重、保序
        self.core = list(dict.fromkeys(x for x in core if len(x) >= 4))[:16]
        print(f"   🧠 核心知識 {len(self.core)} 條（每輪回灌）")

    # ---------------- 視角：俯仰追蹤、自動回正、線上量測
    PITCH_LIMIT = 260          # 累積往上看超過這個像素量就自動壓回
    def after_look(self, dx, dy):
        # 線上量測：拖曳前後畫面平移多少（只在有拖曳量時做）
        try:
            if getattr(self, "_look_measured", None) is not None and abs(dx) >= 60:
                import numpy as np, cv2, statistics as st
                a = np.asarray(self._look_measured.convert("L"), dtype=np.uint8)
                b = np.asarray(self.grab_still(320).convert("L"), dtype=np.uint8)
                orb = cv2.ORB_create(600)
                ka, da = orb.detectAndCompute(a, None); kb, db = orb.detectAndCompute(b, None)
                if da is not None and db is not None and len(ka) > 8 and len(kb) > 8:
                    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
                    ms = sorted(bf.match(da, db), key=lambda m: m.distance)[:80]
                    if len(ms) >= 8:
                        xs = [kb[m.trainIdx].pt[0] - ka[m.queryIdx].pt[0] for m in ms]
                        mx = st.median(xs)
                        good = [x for x in xs if abs(x - mx) < 25]
                        if len(good) >= 6:
                            # 320 寬的畫面平移 → 換算成佔畫面百分比
                            pct = abs(st.median(good)) / 320 * 100
                            # 對著平牆或天花板時 ORB 會量出「轉了 0%」，那是假的：
                            # 收進樣本會把靈敏度推到天文數字（曾經算出 dx 18166 才轉四分之一畫面）。
                            if pct >= 3.0 and float(a.std()) >= 18.0:
                                self.look_samples.append((abs(dx), pct))
                            if len(self.look_samples) >= 8:
                                self.update_look_hint()
        except Exception:  # noqa: BLE001
            pass
        # 2026-09-14 使用者：水平儀要定期驗證方向對不對。
        # 不另外動鏡頭，直接用他自己送的這一次 dy 來驗：
        # 鏡頭往下轉（dy>0）時，畫面裡的東西會往上跑（特徵點的 y 變小），
        # 所以正確的話 sign(畫面位移) 應該跟 sign(dy) 相反。
        try:
            # 只在「中等幅度」時驗證：轉太大（例如一次 600＝90 度）前後畫面幾乎不重疊，
            # ORB 配到的是雜訊；磁磚／天花板格線又是週期性的，很容易整格配錯。
            # 2026-09-14 的教訓：第一批 47 筆樣本全是 dy -600，算出 19% 正確率，
            # 那個數字沒有意義，卻差點讓他以為 dy 的正負是反的。
            if (getattr(self, "_look_measured", None) is not None
                    and 40 <= abs(dy) <= 200 and abs(dx) <= 60):
                import numpy as np, cv2, statistics as st
                a2 = np.asarray(self._look_measured.convert("L"), dtype=np.uint8)
                b2 = np.asarray(self.grab_still(320).convert("L"), dtype=np.uint8)
                if float(a2.std()) >= 18.0:
                    orb2 = cv2.ORB_create(600)
                    ka2, da2 = orb2.detectAndCompute(a2, None)
                    kb2, db2 = orb2.detectAndCompute(b2, None)
                    if da2 is not None and db2 is not None and len(ka2) > 8 and len(kb2) > 8:
                        bf2 = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
                        ms2 = sorted(bf2.match(da2, db2), key=lambda m: m.distance)[:80]
                        if len(ms2) >= 8:
                            ys = [kb2[m.trainIdx].pt[1] - ka2[m.queryIdx].pt[1] for m in ms2]
                            my = st.median(ys)
                            # 一致性檢查：多數匹配點要往同一個方向移動才採信
                            same = sum(1 for v in ys if (v < 0) == (my < 0))
                            if abs(my) >= 4 and same >= len(ys) * 0.7:
                                ok = (my < 0) == (dy > 0)
                                c = getattr(self, "gyro_check", None)
                                if c is None:
                                    c = self.gyro_check = {"對": 0, "錯": 0, "最後": "", "連錯": 0}
                                c["對" if ok else "錯"] += 1
                                c["連錯"] = 0 if ok else c.get("連錯", 0) + 1
                                c["最後"] = (f"送 dy {dy:+d} → 畫面往{'上' if my < 0 else '下'}移 "
                                             f"{abs(my):.0f}px｜{'方向正確' if ok else '**方向相反**'}")
                                if not ok and c["連錯"] >= 3:
                                    print(f"   ⚠⚠ 水平儀方向驗證連續 {c['連錯']} 次相反：{c['最後']}")
        except Exception:  # noqa: BLE001
            pass
        self._look_measured = None
        # 俯仰追蹤：dy 負=往上看
        self.pitch = getattr(self, "pitch", 0) + dy
        if hasattr(self, "g") and not self.g("自動視角"):
            # 使用者要他自己學會控制視角：程式只記數字，不再自己動鏡頭
            self.pitch = max(-self.PITCH_LIMIT, min(self.PITCH_LIMIT, self.pitch))
            return
        if self.pitch < -self.PITCH_LIMIT:
            over = -self.pitch - self.PITCH_LIMIT + 80
            print(f"   🔻 視角過度上抬（累積 {self.pitch}），自動壓回 {int(over)}")
            self.raw_look(0, int(over))
            self.pitch = -self.PITCH_LIMIT + 80
            self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 程式偵測到你把鏡頭抬太高，已幫你壓回水平；別再往上看（dy 用正值）")
        elif self.pitch > self.PITCH_LIMIT:
            # 以前這裡只是把數字夾住、不做事，所以鏡頭往下漂到底也沒人救 → 補上往上回正
            over = self.pitch - self.PITCH_LIMIT + 80
            print(f"   🔺 視角過度下壓（累積 {self.pitch}），自動抬回 {int(over)}")
            self.raw_look(0, -int(over))
            self.pitch = self.PITCH_LIMIT - 80
            self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 程式偵測到你一直低頭看地板，"
                                f"已幫你抬回水平；東西都在水平視線上，不要低頭")

    def raw_look(self, dx, dy, steps=8):
        """轉視角：壓住右鍵，用「小步滑動」（相對位移）把鏡頭帶過去。

        滑動比一次跳到絕對座標順，遊戲的視角轉動也比較線性；
        但每次滑動前先把游標移回視窗正中央、滑完再移回中央，
        這樣相對位移永遠不會累積把游標推到螢幕角落（會觸發 fail-safe 卡死整個代理）。
        位移太大時自動拆成好幾段，每段都從中央重新開始滑。
        """
        try:
            b = self.region()
            cx, cy = b["left"] + b["width"] // 2, b["top"] + b["height"] // 2
            span_x = max(60, b["width"] // 2 - 60)
            span_y = max(60, b["height"] // 2 - 60)
        except Exception:  # noqa: BLE001
            return
        dx, dy = int(dx), int(dy)
        # ══ 2026-09-14 撤銷：dy 的符號本來就是對的 ══
        # 這天稍早我用 ORB 量「送 dy 之後畫面往哪移」，量到 5/5 相反、一致度 100%，
        # 就在這裡把 dy 反號了。**那個測量是錯的**：
        # 當時鏡頭正對著磁磚天花板，而磁磚格線是**週期性紋理**——
        # ORB 會把每條格線配到隔壁那條，整批一起偏移，所以「一致度 100%」反而是假訊號。
        # 用無可爭議的方法重驗（送到底再看畫面是天花板還是地板）：
        #   送負的 dy → 看到地板（桌腳、紙張）＝ 鏡頭往下 → 跟「負＝往上」相反？不，
        #   反號生效時才會這樣；把反號拿掉之後，正的 dy 才真的是往下看。
        # 教訓：**畫面位移法不能用在重複紋理上**，要用「看到天花板還是地板」這種語意判準。
        dy_mouse = dy
        if dy:
            # 最近一分鐘的 dy 淨值：眼睛框不到東西的時候，這是唯一客觀的俯仰線索
            # （2026-09-13：以前那個 fallback 是拿他自己講的話當證據，但他正好分不清
            #   天花板格紋與地板磁磚——他說錯一次，程式就把視角判反、扣錯分、還校正錯 gyro。）
            rec = getattr(self, "_dy_recent", None)
            if rec is None:
                rec = self._dy_recent = []
            now = time.time()
            rec.append((now, dy))
            self._dy_recent = [x for x in rec if now - x[0] <= 60][-80:]
        if dx or dy:
            try:
                self.trace_add(f"轉視角 dx {dx:+d}、dy {dy:+d}"
                               + ("（往上）" if dy < 0 else "（往下）" if dy > 0 else ""))
            except Exception:  # noqa: BLE001
                pass
        while dx or dy_mouse:
            sx = max(-span_x, min(span_x, dx))
            sy = max(-span_y, min(span_y, dy_mouse))
            dx -= sx
            dy_mouse -= sy
            n = max(steps, 6)
            try:
                move_safe(cx, cy)
                time.sleep(0.03)
                pydirectinput.mouseDown(button="right")
                done_x = done_y = 0
                for i in range(1, n + 1):
                    tx, ty = sx * i // n, sy * i // n          # 累積到第 i 步該走到哪
                    pydirectinput.moveRel(tx - done_x, ty - done_y, relative=True)
                    done_x, done_y = tx, ty
                    time.sleep(0.02)
                pydirectinput.mouseUp(button="right")
                move_safe(cx, cy)
                time.sleep(0.08)
                try:
                    if hasattr(self, "gyro_add"):
                        self.gyro_add(sx, sy)      # 水平儀：所有轉視角都會經過這裡
                except Exception:  # noqa: BLE001
                    pass
            except Exception as e:  # noqa: BLE001
                try:
                    pydirectinput.mouseUp(button="right")
                except Exception:  # noqa: BLE001
                    pass
                print(f"   轉視角失敗: {e}")
                return

    def update_look_hint(self):
        import statistics as st
        if getattr(self, "px_per_pct", None):
            pass
        by = {}
        for d, p in self.look_samples[-40:]:
            by.setdefault(round(d / 50) * 50, []).append(p)
        parts = [f"dx≈{k}→畫面轉{st.median(v):.0f}%" for k, v in sorted(by.items()) if len(v) >= 2]
        if not parts:
            return
        ratios = [p / d for d, p in self.look_samples[-40:] if d]
        r = st.median(ratios)
        if r > 0:
            parts.append(f"換算：轉四分之一畫面≈dx {int(25/r)}、半個畫面≈dx {int(50/r)}；微調用 dx {max(30, int(5/r))}")
        self.look_hint = "；".join(parts) + "（本機遊玩實測）"
        try:
            with open(os.path.join(self.dir, "視角校準.json"), "w", encoding="utf-8") as f:
                json.dump({"時間": datetime.now().strftime("%m/%d %H:%M"), "樣本數": len(self.look_samples),
                           "提示": self.look_hint,
                           "樣本": [{"dx": d, "畫面轉%": round(p, 1)} for d, p in self.look_samples[-40:]]},
                          f, ensure_ascii=False, indent=2)
        except OSError:
            pass
        print(f"   📐 視角換算更新：{self.look_hint}")

    # ---------------- 系統提示（帶入視角換算）
    def sys_prompt(self):
        hint = getattr(self, "look_hint", "") or "尚未校準；dx=100 大約轉一點點，先小步試（dx=80~150）再看畫面變化。"
        return SYSTEM_PROMPT.format(game=self.game, look_hint=hint)

    def load_calib(self):
        self.look_hint = ""
        try:
            with open(os.path.join(self.dir, "視角校準.json"), encoding="utf-8") as f:
                c = json.load(f)
            self.look_hint = c.get("提示", "")
        except (OSError, json.JSONDecodeError):
            pass

    # ---------------- 座標：比例 → 螢幕實體像素（以 Roblox 視窗客戶區為準）
    def region(self):
        return self.win.client_bbox() or {"left": 0, "top": 0, "width": self.screen_w, "height": self.screen_h}

    # 2026-09-16：視窗被拉到比螢幕大（或移出螢幕）時，**畫面上有些地方滑鼠到不了**。
    # 那不是他操作不好，是物理上碰不到——所以程式要照實告訴他哪一塊點不到，
    # 並且擋掉點在那裡的動作（不然他會對著一個永遠沒反應的按鈕試一整天）。
    # 使用者規定：不准移動視窗，他要自己想別的辦法。
    @staticmethod
    def _monitors():
        """所有實體螢幕的矩形。**不能用「虛擬桌面」那個大矩形**——
        螢幕排成階梯狀時，大矩形裡會有沒有任何螢幕的『洞』，滑鼠進不去那裡。"""
        import win32api
        return [m[2] for m in win32api.EnumDisplayMonitors()]

    def _on_screen(self, x, y):
        return any(l <= x < r and t <= y < b for (l, t, r, b) in self._monitors())

    def reachable(self, rx, ry):
        """這個畫面比例座標，滑鼠碰得到嗎？回 (碰得到, 說明)。"""
        try:
            b = self.win.client_bbox()
            if not b:
                return True, ""
            x = b["left"] + int(max(0.0, min(1.0, float(rx))) * b["width"])
            y = b["top"] + int(max(0.0, min(1.0, float(ry))) * b["height"])
            if self._on_screen(x, y):
                return True, ""
            return False, f"畫面 ({rx:.2f}, {ry:.2f}) 換算到螢幕座標 ({x}, {y})，那裡沒有螢幕，滑鼠到不了"
        except Exception:  # noqa: BLE001
            return True, ""

    def offscreen(self):
        """畫面四邊各有多少比例滑鼠到不了（沿中線掃描量出來的）。全 0 ＝整個畫面都碰得到。"""
        out = {"上": 0.0, "下": 0.0, "左": 0.0, "右": 0.0}
        try:
            b = self.win.client_bbox()
            if not b:
                return out
            cx, cy = 0.5, 0.5
            if not self.reachable(cx, cy)[0]:      # 連中間都碰不到就不用量了
                return out
            step = 0.01
            v = 0.0
            while v < 0.5 and not self.reachable(cx, v)[0]:
                v += step
            out["上"] = round(v, 3)
            v = 0.0
            while v < 0.5 and not self.reachable(cx, 1.0 - v - 1e-6)[0]:
                v += step
            out["下"] = round(v, 3)
            v = 0.0
            while v < 0.5 and not self.reachable(v, cy)[0]:
                v += step
            out["左"] = round(v, 3)
            v = 0.0
            while v < 0.5 and not self.reachable(1.0 - v - 1e-6, cy)[0]:
                v += step
            out["右"] = round(v, 3)
        except Exception:  # noqa: BLE001
            pass
        return out

    def to_screen(self, rx, ry):
        b = self.region()
        x = b["left"] + int(max(0.0, min(1.0, float(rx))) * b["width"])
        y = b["top"] + int(max(0.0, min(1.0, float(ry))) * b["height"])
        return x, y

    # ---------------- 成績（最高輪班／金幣）
    def _load_score(self):
        self.score = {"最高輪班": 0, "最高金幣": 0, "目前輪班": None, "目前金幣": None, "輪班報告次數": 0}
        try:
            with open(os.path.join(self.dir, "成績.json"), encoding="utf-8") as f:
                self.score.update(json.load(f))
        except (OSError, json.JSONDecodeError):
            pass

    def update_score(self, seen):
        changed = False
        m = re.search(r"輪班\s?(\d{1,3})", seen)
        if m:
            n = int(m.group(1))
            if n != self.score.get("目前輪班"):
                self.score["目前輪班"] = n; changed = True
            if n > self.score["最高輪班"]:
                self.score["最高輪班"] = n; changed = True
                self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 新紀錄：最高輪班 {n}")
                print(f"   🏆 最高輪班更新：{n}")
        m = re.search(r"\$\s?(\d{1,6})", seen)
        if m:
            v = int(m.group(1))
            if v != self.score.get("目前金幣"):
                self.score["目前金幣"] = v; changed = True
            if v > self.score["最高金幣"]:
                self.score["最高金幣"] = v; changed = True
                print(f"   🏆 最高金幣更新：${v}")
        if "輪班報告" in seen and time.time() - getattr(self, "last_report_seen", 0) > 120:
            self.last_report_seen = time.time()
            self.score["輪班報告次數"] = self.score.get("輪班報告次數", 0) + 1; changed = True
        if changed:
            self.score["更新"] = datetime.now().strftime("%m/%d %H:%M:%S")
            try:
                with open(os.path.join(self.dir, "成績.json"), "w", encoding="utf-8") as f:
                    json.dump(self.score, f, ensure_ascii=False, indent=2)
            except OSError:
                pass

    # ---------------- 死亡檢討 / 自主思考
    def _read_instr(self):
        try:
            with open(os.path.join(self.dir, "任務指示.txt"), encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            return ""

    def _last_death_note(self):
        f = os.path.join(self.dir, "死亡檢討.md")
        if not os.path.exists(f):
            return ""
        blocks = open(f, encoding="utf-8").read().split("## 第")
        if len(blocks) < 2:
            return ""
        last = blocks[-1]
        get = lambda k: next((l.split("：", 1)[1].strip() for l in last.splitlines() if l.startswith(f"- {k}：")), "")
        return f"上次死亡：死因「{get('死因')}」；下次「{get('下次避免')}」"

    def replay_shots(self, secs=40.0, end=None):
        """最近 secs 秒的回放（每秒一張，最多 8 張，標著「幾秒前」）。

        2026-09-16 使用者：這是**通用的檢討方式**——出事當下來不及看清楚，
        事後把那幾十秒的畫面攤出來自己看。死亡、卡住、扣分檢討都用它。
        """
        try:
            rows = self.grabber.latest_slow(secs)
        except Exception:  # noqa: BLE001
            return []
        if len(rows) < 4:
            return []
        pick = rows[:: max(1, len(rows) // 8)][-8:]          # 最多 8 張，等距
        out = []
        t_end = float(end or rows[-1][0])
        for t, im in pick:
            try:
                uri, _ = jpeg_data_uri(im, 62)
                out.append((round(t_end - t, 1), uri))
            except Exception:  # noqa: BLE001
                pass
        return out

    def death_review(self):
        n = getattr(self, "death_count", 1)
        print(f"   💀 第 {n} 次死亡，請模型檢討死因")
        shots = self.replay_shots()
        content = []
        if shots:
            print(f"   🎞 附上死前 {shots[0][0]:.0f} 秒的回放 {len(shots)} 張，讓他自己事後看")
            for ago, uri in shots:
                content.append({"type": "text", "text": f"【死前 {ago:.0f} 秒】"})
                content.append({"type": "image_url", "image_url": {"url": uri}})
            content.append({"type": "text", "text":
                            "上面是**死前這段時間的回放**（每張都標了死前幾秒）。"
                            "當下太快看不清楚沒關係，現在一張一張看：是什麼東西靠近你、"
                            "你的血條／狀態什麼時候開始掉、你當時在做什麼。**從畫面找死因，不要用猜的。**"})
        content.append({"type": "text", "text": "【死時的視角】" + self.view_state() + chr(10) + DEATH_PROMPT.format(game=self.game, n=n,
                                     last=getattr(self, "death_note", "") or "（無）",
                                     trace=self.trace_text(40.0, end=getattr(self, "last_death", None)),
                                     history=chr(10).join(self.history[-25:]))})
        raw, _, _, _ = stream_json(self.endpoint, self.model, "你是正在玩 Roblox 遊戲的玩家，只輸出 JSON。",
                                   content, 1500, time.time() + 180.0, 0.1, effort=慢想)
        obj = parse_json_loose(raw) or {}
        cause, mistake, avoid = obj.get("cause", ""), obj.get("mistake", ""), obj.get("avoid", "")
        if cause or avoid:
            self.death_note = f"第 {n} 次死亡：死因「{cause}」；錯誤「{mistake}」；下次「{avoid}」"
            self.add_note(f"（死亡檢討 #{n}）死因：{cause}；避免：{avoid}")
            with open(os.path.join(self.dir, "死亡檢討.md"), "a", encoding="utf-8") as f:
                f.write(f"## 第 {n} 次 {datetime.now().strftime('%m/%d %H:%M')}" + chr(10) + f"- 死因：{cause}" + chr(10) +
                        f"- 錯誤決定：{mistake}" + chr(10) + f"- 下次避免：{avoid}" + chr(10) +
                        "- 死前三十秒實際做的事（拿來核對上面的死因是不是亂歸因）：" + chr(10) +
                        self.trace_text(40.0, end=getattr(self, "last_death", None)) + chr(10) + chr(10))
            self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 你死了（第 {n} 次）。檢討：{self.death_note}")
            print(f"   💀 檢討：{self.death_note}")
        self.log({"時間": datetime.now().strftime("%H:%M:%S"), "模式": "死亡檢討", "次數": n, "死因": cause, "錯誤": mistake, "避免": avoid})

    def emergency_think(self, why):
        """緊急情況由程式強制觸發思考（模型自己很少主動用 think）。"""
        if time.time() - getattr(self, "last_think", 0) < 45:
            return
        print(f"   🚨 緊急思考：{why}")
        self.think(f"緊急狀況：{why}。我現在該怎麼辦？下一步具體要做什麼？")

    def think(self, q):
        self.last_think = time.time()
        print(f"   🤔 思考：{q}")
        raw, _, _, _ = stream_json(self.endpoint, self.model, "你是正在玩 Roblox 遊戲的玩家，只輸出 JSON。",
                                   [{"type": "text", "text": "【你現在的視角】" + self.view_state() + chr(10) + self.trail_text() + THINK_PROMPT.format(q=q, instr=self._read_instr() or "（無）",
                                     summary=chr(10).join(f"- {x}" for x in getattr(self, "summary", [])) or "（無）",
                                     death=getattr(self, "death_note", "") or "（無）", history=chr(10).join(self.history[-20:]))}],
                                   1500, time.time() + 150.0, 0.3, effort=慢想)
        obj = parse_json_loose(raw) or {}
        plan = str(obj.get("plan") or "").strip()
        if plan:
            self.plan = plan
            with open(os.path.join(self.dir, "計畫.md"), "a", encoding="utf-8") as f:
                f.write(f"- {datetime.now().strftime('%m/%d %H:%M')} 問：{q}" + chr(10) + f"  計畫：{plan}" + chr(10))
            self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 你停下來想了「{q}」→ 計畫：{plan}")
            print(f"   🤔 計畫：{plan}")
        if obj.get("n"):
            self.add_note(str(obj["n"]))
        self.log({"時間": datetime.now().strftime("%H:%M:%S"), "模式": "思考", "問題": q, "計畫": plan})

    # ---------------- YouTube 學習模式
    def activate_title(self, substr):
        import win32gui
        found = []

        def cb(h, _):
            if win32gui.IsWindowVisible(h) and substr in win32gui.GetWindowText(h):
                found.append(h)
        win32gui.EnumWindows(cb, None)
        if not found:
            return False
        try:
            pydirectinput.keyDown("alt"); pydirectinput.keyUp("alt")
            win32gui.SetForegroundWindow(found[0]); time.sleep(0.3)
            return win32gui.GetForegroundWindow() == found[0]
        except Exception:  # noqa: BLE001
            return False

    def youtube_session(self, query, max_seconds=240, kind="youtube"):
        import urllib.parse, webbrowser
        if kind == "介紹":
            # 2026-09-16 使用者：卡住的時候可以去看**這個遊戲自己的頁面**
            # （介紹、圖片、玩法說明），那不是攻略，是遊戲自己講的。
            pid = 0
            try:
                pid = int((self.gcfg.get("places") or {}) and next(iter(self.gcfg["places"])) or 0)
                pid = int(self.gcfg.get("大廳place") or pid)
            except Exception:  # noqa: BLE001
                pid = 0
            if not pid:
                print("   ⚠ 遊戲.json 沒有 placeId，開不了遊戲頁面")
                return
            self.last_wiki = time.time(); max_seconds = min(max_seconds, 180)
            url = f"https://www.roblox.com/games/{pid}/"
            where, title_key, label = "這個遊戲自己的 Roblox 頁面（介紹／圖片／說明）", "Roblox", "看介紹"
        elif kind == "wiki":
            self.last_wiki = time.time(); max_seconds = 180
            url = "https://www.google.com/search?q=" + urllib.parse.quote(query)
            where, title_key, label = "網頁上查攻略／wiki", "Google", "wiki"
        else:
            self.last_youtube = time.time()
            url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote(query)
            where, title_key, label = "YouTube 上看影片", "YouTube", "youtube"
        self._learn_where, self._learn_q, self._learn_title = where, query, title_key
        print(f"   ▶ {label} 學習：{query}")
        self.log({"時間": datetime.now().strftime("%H:%M:%S"), "模式": f"{label}開始", "查詢": query})
        webbrowser.open(url)
        time.sleep(5)
        t_end = time.time() + max_seconds
        learned = 0
        while time.time() < t_end and not self.stop.is_set() and not os.path.exists(STOP_FILE)                 and not os.path.exists(os.path.join(HERE, "stop.txt")):
            t0 = time.time()
            frames = self.grabber.latest(self.cfg["每輪張數"])
            if len(frames) < 2:
                time.sleep(0.3); continue
            video_uri, _ = mp4_data_uri([im for _, im in frames], self.cfg["螢幕fps"])
            still_uri, _ = jpeg_data_uri(draw_grid(self.grab_still(1024)), 70)
            content = [{"type": "video_url", "video_url": {"url": video_uri}},
                       {"type": "image_url", "image_url": {"url": still_uri}},
                       {"type": "text", "text": LEARN_PROMPT.format(game=self.game, where=self._learn_where, q=self._learn_q) +
                        chr(10) + "目前筆記：" + chr(10) + chr(10).join(f"- {n}" for n in self.notes[-6:])}]
            raw, trunc, _, _ = stream_json(self.endpoint, self.model, self.sys_prompt(),
                                           content, 100, t0 + self.deadline)
            obj = parse_json_loose(raw) or {}
            if obj.get("n"):
                self.add_note(f"（{label}）" + obj["n"]); learned += 1
            acts, blocked = self.guard([a for a in (obj.get("a") or []) if isinstance(a, dict)
                                        and a.get("t") in ("click", "scroll", "wait", "key", "move")], False)
            acts = [a for a in acts if not (a.get("t") == "key" and a.get("k") not in ("space", "esc", "down", "up"))]
            rec = {"時間": datetime.now().strftime("%H:%M:%S"), "模式": "youtube", "看到": obj.get("s"),
                   "動作": acts, "擋掉": blocked, "筆記": obj.get("n") or "", "結束": obj.get("d")}
            self.log(rec)
            print(f"[{rec['時間']}] YouTube  看到：{obj.get('s')}  做：{'、'.join(self.describe(a) for a in acts) or '—'}"
                  + (f"  記：{obj['n']}" if obj.get("n") else ""))
            if not (self.activate_title(self._learn_title) or self.activate_title("Google Chrome") or self.activate_title("Edge")):
                print("   ⚠ 找不到 YouTube 視窗，結束學習"); break
            for a in acts:
                try:
                    if a["t"] == "click":
                        if self.needs_verify(a):
                            okk, why = self.verify_click(a); print(f"   🔍 {why}")
                            if not okk: continue
                        x = int(float(a["x"]) * self.screen_w); y = int(float(a["y"]) * self.screen_h)
                        move_safe(x, y); time.sleep(0.1)
                        pydirectinput.mouseDown(); time.sleep(0.08); pydirectinput.mouseUp()
                    elif a["t"] == "scroll":
                        pyautogui.scroll(int(max(-10, min(10, float(a.get("v", 0))))) * 120)
                    elif a["t"] == "wait":
                        time.sleep(max(0.5, min(3.0, float(a.get("d", 2)))))
                    elif a["t"] == "key":
                        pydirectinput.press(a["k"])
                    elif a["t"] == "move":
                        move_safe(int(float(a["x"]) * self.screen_w), int(float(a["y"]) * self.screen_h))
                except Exception as e:  # noqa: BLE001
                    print(f"   動作失敗 {a}: {e}")
            if obj.get("d") == 1:
                break
        # 關掉 YouTube 分頁，回到 Roblox
        if self.activate_title(self._learn_title) or self.activate_title("Google Chrome") or self.activate_title("Edge"):
            pydirectinput.keyDown("ctrl"); pydirectinput.press("w"); pydirectinput.keyUp("ctrl")
            time.sleep(0.5)
        self.win.activate()
        self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 去{self._learn_where}查了「{query}」，新增 {learned} 條筆記，已回到遊戲")
        self.log({"時間": datetime.now().strftime("%H:%M:%S"), "模式": f"{label}結束", "新筆記": learned})
        print(f"   ◀ {label} 結束，學到 {learned} 條，回到 Roblox")
        # 查過幾次沒拿到東西 → 記著，讓他自己判斷「這遊戲查不到，別再查了」（使用者 2026-09-16）
        if not learned:
            self.wiki_useless = int(getattr(self, "wiki_useless", 0) or 0) + 1
            print(f"   📖 這次查資料什麼都沒拿到（累計 {self.wiki_useless} 次白查）"
                  f"——要不要再查是你自己的判斷")

    # ---------------- 反思（整理筆記）
    def reflect(self):
        if not self.history:
            return
        prompt = REFLECT_PROMPT.format(game=self.game, notes="\n".join(f"- {n}" for n in self.notes[-15:]) or "（無）",
                                       history="\n".join(self.history[-25:]))
        raw, trunc, _, _ = stream_json(self.endpoint, self.model, "你是正在研究一款 Roblox 遊戲的玩家，只輸出 JSON。",
                                       [{"type": "text", "text": prompt}], 400, time.time() + 120.0, 0.1)
        obj = parse_json_loose(raw) or {}
        for n in (obj.get("notes") or [])[:5]:
            if isinstance(n, str):
                self.add_note(n)
        print(f"   🗒 整理筆記：{len(obj.get('notes') or [])} 條（目前共 {len(self.notes)} 條）")
        uniq = list(dict.fromkeys(list(getattr(self, "core", [])) + [n.split("  (")[0] for n in self.notes][-150:]))
        raw2, _, _, _ = stream_json(self.endpoint, self.model, "你是正在研究一款 Roblox 遊戲的玩家，只輸出 JSON。",
                                    [{"type": "text", "text": SUMMARY_PROMPT.format(game=self.game, notes=chr(10).join(f"- {n}" for n in uniq))}],
                                    300, time.time() + 120.0, 0.1)
        obj2 = parse_json_loose(raw2) or {}
        summ = [x for x in (obj2.get("summary") or []) if isinstance(x, str) and x.strip()][:10]
        if summ:
            self.summary = summ
            with open(os.path.join(self.dir, "玩法摘要.md"), "w", encoding="utf-8") as f:
                f.write(f"# 「{self.game}」玩法摘要（{datetime.now().strftime('%m/%d %H:%M')} 自動濃縮）" + chr(10) + chr(10))
                f.write(chr(10).join(f"- {x}" for x in summ) + chr(10))
            print(f"   📌 玩法摘要更新：{len(summ)} 條")

    def print_rec(self, rec):
        if rec.get("反射") and rec.get("反射次數"):
            print(f"   ⚡ 思考時反射 {rec['反射次數']} 次：{rec['反射']}")
        acts = "、".join(self.describe(a) for a in rec.get("動作", [])) or "—"
        flag = ""
        if rec.get("robux") == 1:
            flag += " 🚫付費視窗"
        if rec.get("擋掉"):
            flag += f" ⛔擋掉{len(rec['擋掉'])}"
        if rec.get("截斷"):
            flag += " ⚠截斷"
        print(f"[{rec['時間']}] {rec['模式']} Δ{rec['差異']} {rec.get('總秒', rec.get('秒'))}s{flag}")
        print(f"   看到：{rec.get('看到')}")
        if rec.get("目標") is not None:
            print(f"   0視角{'水平' if rec.get('視角水平') else '要轉正'} · 1目標：{rec.get('目標')} · 2位置：{rec.get('目標位置')}")
        print(f"   做：{acts}")
        if rec.get("筆記"):
            print(f"   記：{rec['筆記']}")

    # ---------------- 主迴圈
    def run(self):
        t_end = time.time() + self.args.hours * 3600
        if not getattr(self, "sop", None):
            try:
                self.make_sop()
            except Exception as e:  # noqa: BLE001
                print(f"   擬定工作流程失敗：{e}")
        print(f"[play_agent] 遊戲「{self.game}」 模型 {self.model} 螢幕 {self.screen_w}x{self.screen_h} "
              f"截止 {self.deadline}s/輪 時限 {self.args.hours}h {'(dry-run)' if self.dry else ''}")
        print("   停止方式：滑鼠甩到左上角 / 按 Ctrl+F12 / 專案夾放 stop.txt")
        try:                      # 2026-09-18：開機時游標若卡在角落，先挪到安全處再開始
            import ctypes
            pt = ctypes.wintypes.POINT() if hasattr(ctypes, "wintypes") else None
            if pt is not None and ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
                if safe_xy(pt.x, pt.y) != (pt.x, pt.y):
                    b = self.region()
                    move_safe(b["left"] + b["width"] // 2, b["top"] + b["height"] // 2)
                    print(f"   🖱 游標本來停在 ({pt.x},{pt.y})＝螢幕角落（會誤觸緊急停止），已挪到視窗中央")
        except Exception as e:  # noqa: BLE001
            print(f"   游標位置檢查略過：{e}")
        time.sleep(1.2)
        try:
            while not self.stop.is_set():
                if time.time() > t_end:
                    print("[時間到] 結束"); break
                if os.path.exists(STOP_FILE) or os.path.exists(os.path.join(HERE, "stop.txt")):
                    print("[停止.txt] 結束"); break
                try:
                    self.cycle()
                except pyautogui.FailSafeException:
                    raise
                except Exception as e:  # noqa: BLE001
                    import traceback
                    print(f"[錯誤] 這一輪失敗：{type(e).__name__}: {e}")
                    print(traceback.format_exc())
                    time.sleep(1.0)
                self.cycles += 1
                if time.time() - self.last_reflect > self.args.reflect_min * 60:
                    try:
                        self.reflect()
                    except Exception as e:  # noqa: BLE001
                        print(f"[錯誤] 整理筆記失敗：{e}")
                    self.last_reflect = time.time()
                if time.time() - self.last_snapshot > 300:
                    self.snapshot("定時"); self.last_snapshot = time.time()
        except pyautogui.FailSafeException:
            print("[FAILSAFE] 滑鼠在左上角，緊急停止")
        except KeyboardInterrupt:
            print("[Ctrl+C] 結束")
        except Exception as e:  # noqa: BLE001
            import traceback
            print(f"[致命錯誤] {type(e).__name__}: {e}")
            print(traceback.format_exc())
        finally:
            if self.stop.is_set():
                print("[熱鍵 Ctrl+F12 / stop 事件] 結束")
            for k in ("w", "a", "s", "d", "shift", "space"):
                try:
                    pydirectinput.keyUp(k)
                except Exception:  # noqa: BLE001
                    pass
            try:
                pydirectinput.mouseUp(button="right")
            except Exception:  # noqa: BLE001
                pass
            self.grabber.stop_flag.set()
            if hasattr(self, "maybe_train_eyes"):
                try:
                    self.maybe_train_eyes()      # 收工＝GPU 空下來，這時才訓練眼睛
                except Exception as e:  # noqa: BLE001
                    print(f"   訓練眼睛啟動失敗：{e}")
            print(f"[結束] 共 {self.cycles} 輪，擋掉 {self.blocked} 個動作，筆記 {len(self.notes)} 條 → {self.dir}")


class Tee:
    """同時印到終端機與 agent.log。"""

    def __init__(self, path, orig):
        self.f = open(path, "a", encoding="utf-8")
        self.orig = orig

    def write(self, s):
        self.orig.write(s)
        self.f.write(s)
        self.f.flush()

    def flush(self):
        self.orig.flush()
        self.f.flush()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", default="動物醫院")
    ap.add_argument("--hours", type=float, default=16.0)
    ap.add_argument("--deadline", type=float, default=10.0)
    ap.add_argument("--max-tokens", type=int, default=180)
    ap.add_argument("--reflect-min", type=float, default=10.0, help="每幾分鐘整理一次筆記")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    log_dir = os.path.join(HERE, "資料", args.game)
    os.makedirs(log_dir, exist_ok=True)
    sys.stdout = Tee(os.path.join(log_dir, "agent.log"), sys.stdout)
    sys.stderr = Tee(os.path.join(log_dir, "agent.log"), sys.stderr)
    print(f"\n===== {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} 啟動 =====")
    Agent(args).run()


if __name__ == "__main__":
    main()
