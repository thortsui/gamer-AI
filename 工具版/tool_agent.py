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
# 10/24 20:2x 使用者「工具你去修復好」：量測修正都用這個開關（模型比較重跑期間保持舊版，條件才一致；本地代理用 GAMER_TOOLFIX=1 開）
TOOLFIX = os.environ.get("GAMER_TOOLFIX") == "1"
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
        if t.get("隱藏"):             # 10/20 20:4x 使用者：相似度太高、可替換的合併 → 舊名字不列，叫了照樣能用
            continue
        tag = "" if t["實作"] != "未實作" else "（未實作）"
        if t.get("自訂"):
            tag = "（你自己定義的）"
        # 12:4x 他會把用法裡的參數名照抄（轉(度)、檢討(原因,預期…)）→ 清單上寫實際範例值，參數說明放工具說明
        exs = t.get("例子") or []
        out.append(f"{t.get('例') or t['用法']}｜{t['用途']}{tag}" + ("｜例：" + "；".join(exs) if exs else ""))
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


CN_NUM = {1: "一", 2: "兩", 3: "三", 4: "四", 5: "五"}
_PLAN_FORMAT = ('{"看到":"儀器讀數，把畫面上看得到的東西、字、方位、數字都寫出來(<=80字)",'
                '"上一輪實際":"上一輪K步做完之後實際發生了什麼(<=60字；第一輪寫 無)",'
                '"符合":[true或false,…上一輪K步各一個：那一步的工具回傳＋現在畫面，跟它的「結果應該是」一不一樣；第一輪寫 []],'
                '"想":"因為:原因｜所以要:目的｜K步:為什麼這樣排",'
                '"先記":["記錄／檢討／文件／學習類工具，最多 2 個，不佔K步；有 false 就在這裡叫 檢討(…)；不用就 []"],'
                '"下K步":[EX]}')


def plan_format(n):
    k = CN_NUM.get(n, str(n))
    ex = ",".join(['{"做":"工具(參數)","結果應該是":"這一步做完應該看到什麼"}'] + ['{"做":"…","結果應該是":"…"}'] * (n - 1))
    return _PLAN_FORMAT.replace("K", k).replace("EX", ex)


PLAN3_FORMAT = plan_format(3)
PLAN3_PRE_GROUPS = ("文件", "記憶", "流程", "學習")
PLAN3_PRE_TOOLS = ("記應該人稱", "回報人稱")   # 10/19 10:4x：校準類裡只記錄、不動遊戲的，也可以放 先記（他兩次放先記被擋）


NO_SMALL_UPDATE = False   # --no-small-update（10/23）
LIVE = {"輪": 0, "階段": "啟動", "開始": 0.0, "做": []}   # 10/23 直播網站：這一輪在做什麼
ASK_CLAUDE = True         # 10/23 實驗分枝 exp-ask-claude：問Claude


def build_system(game, cat, plan3=False):
    s = _build_system(game, cat, plan3)
    if NO_SMALL_UPDATE:     # 程式不停下來請他寫，說明也拿掉，改成自己想
        s = re.sub(r"\*\*出現變動就重新想任務\*\*.*?提早檢討，不要等卡一百輪。",
                   "出現變動（死掉、任務字換了、畫面從全黑／載入回來、你叫了 突發狀況）時，自己重新想一次「現在的任務應該是什麼」，該換就 設小任務。",
                   s, flags=re.S)
    return s


def _build_system(game, cat, plan3=False):
    gdir = os.path.join(ROOT, "專案", game)
    identity = read_text(os.path.join(ROOT, "核心", "共用", "00_身分與現在的任務.md"))
    identity = re.sub(r"^---.*?---\s*", "", identity, flags=re.S).strip().replace("{game}", game)
    try:
        gj = json.load(open(os.path.join(gdir, "遊戲.json"), encoding="utf-8"))
    except Exception:  # noqa: BLE001
        gj = {}
    gname = gj.get("全名") or game
    goal = read_text(os.path.join(gdir, "記憶", "任務指示.txt")).strip() or "（使用者還沒交代目標）"
    limits = ["不准花任何錢：Robux、金幣、購買、解鎖、升級、付費復活、任何「Buy／Purchase／$」按鈕；看到付費視窗只能用 關掉 工具關掉"]
    if gj.get("禁止換職業"):
        limits.append("不准換職業：不點「職業」按鈕、不點護士／醫生／實習生那類選項")
    if gj.get("禁止上下轉頭"):
        limits.append("不准上下轉頭")
    limits.append("不准離開這隻遊戲去玩別的；只有卡死才用 退出重進，而且要回到同一隻遊戲")
    limits.append("點選類的頁面（選單、按鈕、面板）：**任何點擊都是兩步**——點 或 點字 只是預約，程式不會馬上點；"
                  "下一輪你會拿到放大的目標圖，必須再看一次並審核上面每一條絕對限制，確定才送 確認點(審核)，不對就 取消點。沒有確認就永遠不會點下去")
    rules = ("【絕對限制】（使用者定的，違反就是錯，沒有例外）" + NL
             + NL.join(f"{i + 1}. {l}" for i, l in enumerate(limits)) + NL
             + "「看到」只寫畫面上真的看得到的東西：物件、文字、方位、數字；不形容氣氛、不推測；只看這一輪的畫面寫，不要抄前幾輪。" + NL
             + "所有文字欄位都是表格式短語（欄:值｜欄:值），不寫連接詞與贅字。")
    # 2026-10-15 22:5x 使用者：「讓他自己開始跑大檢討」→ 他寫的大檢討裡「明天開場要做什麼」「要改的習慣」每次開機帶進來（他自己的話）
    review_txt = ""
    try:
        big = open(os.path.join(gdir, "記憶", "工具版", "大檢討.md"), encoding="utf-8").read()
        keep = []
        for sec in ("明天開場要做什麼", "要改的習慣", "卡法歸納"):
            mm = re.search(r"## " + sec + r"\n(.*?)(?=\n## |\Z)", big, flags=re.S)
            if mm:
                keep.append(f"〔{sec}〕" + NL + mm.group(1).strip()[:900])
        if keep:
            review_txt = "【你自己上次收工寫的大檢討（你自己寫的，照著做）】" + NL + NL.join(keep) + NL * 2
    except OSError:
        pass
    groups = ["校準", "文件", "看", "移動", "視角", "互動", "意外", "記憶", "流程", "學習", "自訂"]
    tool_txt = NL.join(f"【{g}】" + NL + NL.join("  " + l for l in catalog_lines(cat, g))
                       for g in groups if catalog_lines(cat, g))
    # 他自己整理的規則（學習整理）與他自己改的提示（改提示）——都是他的話，每輪帶著
    mine_txt = ""
    for fname, title in (("學習整理.md", "【你自己整理的規則（學習整理）】"), ("我的提示.md", "【你自己寫的提示（改提示）】")):
        try:
            body = open(os.path.join(gdir, "記憶", "工具版", fname), encoding="utf-8").read().strip()
            if body:
                mine_txt += title + NL + body[:900] + NL * 2
        except OSError:
            pass
    review_txt = mine_txt + review_txt
    return (f"{identity}" + NL
            + f"遊戲：{gname}（Roblox place {gj.get('大廳place', '?')}）。詳細介紹自己叫 遊戲介紹。" + NL
            + "使用者給你的目標：" + NL + goal + NL * 2
            + rules + NL * 2
            + review_txt
            + "你有這些工具（格式 範例｜用途；括號裡是範例值，**填你自己的數字和內容，不要照抄範例或參數名**）。要什麼自己叫，程式只做你點名的事，結果下一輪回給你：" + NL
            + tool_txt + NL * 2
            + "【開工先量，逐一回報】（使用者定的）每次登入／重進／切空間、或畫面大小變了，第一件事送 開工測試：程式逐項量 ①畫面尺寸 ②每度 dx ③每度 dy ④深度比例，"
              "⑤第幾人稱由你看畫面用 回報人稱 回報。五項都有才算開工完成，面板【開工數值】會一直顯示量了沒。"
              "量完**必須驗**：送 驗開工（程式照你的數值轉一圈、上下來回、走退來回，三項都回到原點才算開工完成）；"
              "哪一項 ✗ 就照指示重量再驗。也可以自己算一圈 dx 用 驗一圈(dx) 驗，對了 定刻度(每度dx)。開工沒驗完不要做別的事。"
              "之後每一次 視角／移動 工具的回傳都會附「預期 vs 實測」，對不上就重量；工具出來的結果要跟你腦中的一致才能繼續。" + NL
            + "【每個行為三件事】（使用者定的）每一輪的「想」要寫清楚 ①做這個行為的原因（因為）②這個行為應該有的結果（結果應該是）；"
              "下一輪看到新畫面，先在「上一輪實際」寫 ③實際的結果、在「符合」填 true／false。"
              "**實際跟預期不同就直接開檢討**：同一輪叫 檢討(原因,預期,實際,錯在哪,下次)，不要跳過、不要等。符合才繼續下一步。" + NL
            + "【這次測試：多檢討、多找錯】（使用者 10/19 交代）不是只有 符合:false 才檢討：每一輪都看上一輪有沒有做錯、做慢、做白工、可以做得更好的地方，"
              "找到就在 先記 叫 檢討(原因,預期,實際,錯在哪,下次)。寫之前先 查準則(這件事) 看以前有沒有犯過：同樣的錯又出現，就在 錯在哪 寫「又犯：…」，"
              "下次 要換一個跟之前不一樣、做得到的做法，避免重複犯錯。跑一段時間也可以自己叫 大檢討 做中間檢討。" + NL
            + "【突發狀況】（使用者 10/19 交代）出現不合預期的情況（畫面突然變了、走不動、跳出沒看過的東西）時，可以先叫 突發狀況(情況,預期) 想這個情況在你的工作流裡合不合理；"
              "合理的會加進【工作流的可能事項】，同一件事連續遇到 3 次就寫進【工作流】。" + NL
            + "【場景切換可能影響視角轉動速度】（使用者 10/19 交代）換場景之後（進出建築或房間、傳送、死亡重生、退出重進），"
              "視角轉動速度（每度 dx／dy）可能跟之前量的不一樣；轉 的預期跟實測對不上時，先想是不是剛換過場景。" + NL
            + "【遊戲任務字】右上角那塊是 HUD 任務框（介面字，不是場景物件）。面板每輪列程式讀到的右上角任務字（可能讀錯）；標「任務字換了」可能＝上一件做完了，要不要送 任務完成（先回顧三步）你自己決定。" + NL
            + "【準則：要用時才叫出來】（使用者 10/19 12:2x 交代）記憶不要每輪全部帶著——檢討之後把教訓做成工具（新工具），或只留一條準則（記準則(對象,準則)），"
              "例：吃了椅子發現椅子不能吃 → 記準則(椅子,不能吃)；之後要去確認椅子能不能吃時才 查準則(椅子) 叫出來看。"
              "要對某個東西或情況動手前先 查準則；以前的輪要回頭看用 查紀錄(關鍵字)。"
              "**不只檢討後**：你自己的任何發現（試出能做的、發現不能做的、認出某個東西是什麼）都可以隨時自己 記準則——準則是你自己定的，"
              "發現以前的準則不對，就 記準則(對象,更正:新的準則) 取代掉。" + NL
            + "【第幾人稱】（使用者 10/19 交代）要記錄應該是第幾人稱：用 記應該人稱(第一人稱或第三人稱,為什麼) 記下。"
              "如果畫面是第三人稱（應該是第一人稱）就要放大鏡頭：中鍵 捲(正數) 拉近；相反也一樣：捲(負數) 拉遠。"
              "不確定現在第幾人稱就叫 確認人稱（程式量三件事：畫面中間有沒有東西、會不會跟著你移動、判斷第幾人稱）。"
              "調完再 確認人稱。**但是如果有對鏡頭做縮放就要重新測量**：開工測試＋驗開工（面板【開工數值】會標「捲過」直到重量）。面板【人稱】每輪比 應該 跟 現在。" + NL
            + "【少用中鍵縮放】（使用者 10/18 交代；切換人稱除外）捲（滑鼠中鍵）在遊戲畫面上會把鏡頭拉近拉遠，一縮放刻度、深度比例就變了、要重新量測；"
              "能不捲就不捲，要對準東西用 視角移動到(x,y) 或 轉。" + NL
            + "【大任務與小任務】（使用者 10/21 交代）大任務只是一個段落，例：大任務叫「做飯」，小任務就是「買菜」「切菜」「炒菜」…。"
              "**你每一輪的目標是現在的小任務，不是大任務。**用 設大任務(名稱,目標) 定段落、設小任務(名稱,怎麼做) 定現在要做的那一件；"
              "小任務做完送 任務完成（先回顧三步），再設下一個小任務。"
              "你只需要記得三件事：大任務的目標是什麼、前面的小任務完成了什麼、現在這個小任務要怎麼做——面板每輪就列這三件；"
              "前面每一輪的詳細細節不用記（都存在檔案，真的要找用 查紀錄(關鍵字)）。"
              "大任務完成要送兩次：第一次程式列出每個小任務的輪數、✗ 步數、檢討數，你先逐一確認每個小任務是不是一次就做對；"
              "沒一次做對的寫為什麼、下次怎麼一次做對，確定再送 大任務完成(結果,確認:…)，程式才清空這個段落接下一個。"
              "**出現變動就重新想任務**（使用者 10/21 交代）：死掉、特殊事件、文字變化（任務字換了）、畫面從全黑／載入回來、畫面類型變了、"
              "你叫了 突發狀況——只要出現變動，就要重新想一次「現在的任務應該是什麼」；程式會停下來先給你一段畫面描述，再請你寫。"
              "同一件小任務每做 15 輪也會請你寫一次（小任務更新）：遇到的問題、做法有沒有用（看 ✗ 和準則照做的 ✓／✗）、要不要換——提早檢討，不要等卡一百輪。"
              "看不懂畫面就叫 描述畫面（約 15 秒，每個東西會給編號和座標）；要走去其中一個就 走過去(編號)，程式會盯著它邊轉邊走；要每輪盯著就 追蹤(編號)。"
              "認得的東西就 記長相(名稱,編號) 把它截下來存成照片；之後 找長相(名稱)／走過去(名稱) 直接拿照片找，不到 1 秒，不用再描述畫面。"
              "認出一樣東西是什麼（能不能互動、怎麼用）就 登記物品，下次畫面上又看到它，面板【登記的物品】會直接把你登記的內容帶出來。"
              + ("不知道的事可以 問Claude(問題)：Claude 是幫你架這個程式、在旁邊看你玩的人，知道這個遊戲；回答幾分鐘內出現在面板【Claude 的回答】，不用停下來等。" if ASK_CLAUDE else "") + NL
            + "【遇到不知道、工具清單沒有的問題】先想：這件事該用哪個工具？"
              "既有工具做得到、只是用途沒寫這情況 → 補用途(工具,情況)，之後清單就會寫上；"
              "沒有任何工具做得到 → 新工具(名稱,用途,做法)，做法用現有工具一個接一個寫（例如「後退(0.4)→視角右(90)→讀字」），"
              "定義完它就在清單裡、下一輪就能叫。" + NL * 2
            + (plan_rule(int(plan3)) + NL * 2 + "每一輪只輸出一個 JSON：" + plan_format(int(plan3)) if plan3 else
               "每一輪只輸出一個 JSON：" + cat["輸出格式"] + NL
               + "「用」一輪最多 5 個小動作，照順序做（12:3x 使用者：一輪排四到五個小動作加速）；字串「名稱(參數)」或 {\"工具\":\"名稱\",\"參數\":{...}} 都可以。"
                 "不知道怎麼做就先叫文件類工具看一眼，再動。"))


# 10/18 使用者：「他要直接規劃好下三個步驟再直接執行，中間不檢查」；12:5x「改成 預測兩步驟」
_PLAN_RULE = ("【K步規劃】（使用者 10/18 定的，這次挑戰照這個做）每一輪先把**接下來K步**規劃好：每一步一個工具（走／轉／按／點字…遊戲動作都可以），"
              "每一步都寫它的「結果應該是」。程式一口氣照順序做完K步，**中間不停下來給你看**，K步都做完才截下一張畫面給你。"
              "所以排之前要先想清楚：第一步做完畫面會變成怎樣、下一步要接在那個狀態上。"
              "下一輪看K個工具回傳＋新畫面，在「符合」逐步填 [true/false, …]；有 false 就在「先記」叫 檢討。"
              "記錄／檢討／文件／學習類（記筆記、檢討、讀文件、大任務完成…）放「先記」，不佔K步、在K步之前做。"
              "點擊還是兩步：點 或 點字 只是預約，確認點 要排在下一輪（可以當下一輪的第一步）。"
              "本來的「上一輪實際／符合／檢討」規則一樣適用，只是 符合 改成K步各一個。" + NL
              + "【小任務完成要先回顧三步】（使用者 10/18 12:5x 定的）任務完成、這步做完 都要送兩次："
              "第一次送，程式不會記完成，會把完成前最後三步（做了什麼、結果應該是、程式回傳、你填的符合）列給你；"
              "你逐步回顧是不是真的做成了，確定才在之後的輪再送一次，並寫 回顧（三步各自確認的結果，要寫字不能只寫數字）。沒做成就不要送，繼續做。")


def plan_rule(n):
    return _PLAN_RULE.replace("K", CN_NUM.get(n, str(n)))


PLAN3_RULE = plan_rule(3)


# ---------------------------------------------------------------- 速記解析
_ONE = re.compile(r"^\s*([^\s(（]+)\s*(?:[(（]\s*(.*?)\s*[)）])?\s*$")


# 隱藏別名（10:5x 精簡後舊名字還能用，不列在清單上）：舊名 → (新名, 參數轉換)
ALIAS = {
    "前進": ("走", lambda p: p), "後退": ("退", lambda p: p),
    "視角右": ("轉", lambda p: {"度": str(num(p.get("度"), 30))}),
    "視角左": ("轉", lambda p: {"度": str(-num(p.get("度"), 30))}),
    "視角上": ("仰", lambda p: {"度": str(num(p.get("度"), 15))}),
    "視角下": ("仰", lambda p: {"度": str(-num(p.get("度"), 15))}),
    "180度轉頭": ("轉", lambda p: {"度": "180"}),
    "關掉面板": ("關掉", lambda p: {}), "付費視窗": ("關掉", lambda p: {}),
    "look": ("轉", lambda p: {"度": str(num(p.get("dx"), 0) / (CUR_DX[0] or 2.5))}),   # 10/25 審查：不寫死 2.5
}


CUR_DX = [2.5]        # 現在的每度 dx（_look／定刻度／量刻度 會更新；給舊名 look(dx) 換算用）


def parse_call(item, cat):
    """「名稱(參數)」或 dict → (名稱, {參數名: 值})；看不懂回 (None, 原字)。"""
    name, params = _parse_call(item, cat)
    if name is not None:
        return name, params
    old = ""
    raw = ""
    if isinstance(item, dict):
        old = str(item.get("工具") or item.get("t") or "")
        p = item.get("參數") or {}
        raw = ",".join(str(v) for v in (p.values() if isinstance(p, dict) else []))
    else:
        m = _ONE.match(str(item))
        if m:
            old, raw = m.group(1), (m.group(2) or "")
    if old in ALIAS:
        new, conv = ALIAS[old]
        args = [x.strip() for x in re.split(r"[,，、]", raw)] if raw.strip() else []
        key = "度" if old.startswith(("視角", "180")) else ("dx" if old == "look" else "秒")
        return new, conv({key: args[0]} if args else {})
    return name, params


def _split_top(raw, seps):
    """只在括號外切（10/25 23:0x：檢討 裡寫「視角移動到(x,y)沒對到」，括號裡的逗號把五格切錯位 → 工具名變成「視角移動到(0」）。"""
    out, buf, depth = [], [], 0
    for c in raw:
        if c in "([（［":
            depth += 1
        elif c in ")]）］":
            depth = max(0, depth - 1)
        if depth == 0 and c in seps:
            out.append("".join(buf).strip())
            buf = []
        else:
            buf.append(c)
    out.append("".join(buf).strip())
    return out


def _parse_call(item, cat):
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
    # 10/24 22:1x 稽核：「、」也當分隔 → 檢討／突發狀況／新工具 內容寫了「、」就錯位 → 只用逗號分；逗號分不夠格數、又有「、」才退回連「、」一起分
    args = _split_top(raw, ",，") if raw and raw.strip() else []
    if raw and "、" in raw and len(args) == 1 and len(keys) > 1:      # 10/25 審查：有逗號但格數不夠（選填沒寫）不能再用「、」切
        args = _split_top(raw, ",，、")
    if raw and len(args) == 1 and len(keys) > 1 and raw.count("；") >= len(keys) - 1:   # 10/25 04:0x：Claude 用「；」分五格 → 檢討 沒記下
        args = [x.strip() for x in re.split(r"[；;]", raw)]
    params = {}
    if len(keys) == 1 and args:
        params[keys[0]] = raw.strip()
    else:
        for k, v in zip(keys, args):
            params[k] = v
        if keys and len(args) > len(keys):          # 10/18：回顧／下次 寫了逗號 → 多出來的併進最後一格，不丟掉
            params[keys[-1]] = "，".join(args[len(keys) - 1:])
    return name, params


def num(v, default):
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return default


def z2_needed(dd):
    """走路第一次量不出放大倍數、但畫面有動：第二次也量一次放大倍數（看是不是真的量不出）。"""
    return dd >= 2


# ---------------------------------------------------------------- 工具實作
from tools_extra import ExtraTools  # noqa: E402  舊版的學習／記憶／程序／追蹤 全部包成工具（2026-10-15 23:0x）


class Tools(ExtraTools):
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
        self.pitch_acc = 0.0            # 水平儀：仰 累計的度數（正＝抬頭）；修水平／水平歸零／量刻度 歸零
        # 10/23 使用者：「網站上製作一個水平儀 上面標注著他覺得自己目前面對的角度（包含是否低頭等）」
        #   左右：每次拖曳都照他的刻度換成度數累計（正＝右轉），開工測試時歸零＝「開工那時面對的方向」；上下用 pitch_acc
        self.yaw_acc = 0.0
        self.yaw_zero = ""
        self.level_meas = None          # 最近一次 修水平 量到的（時間, 結果）
        self.verify2 = False            # 10/18 使用者：「量測都要二次驗證」（--verify2）
        self.effort_locked = False      # 10/18 使用者：「這次挑戰都用高思考」（--lock-effort）

    def _v2(self, name, v1, v2, ok, unit=""):
        """二次驗證：同一個量測做兩次，回報兩個值、一不一致，記進 二次驗證.md（stats 算一致率）。"""
        tag = "一致" if ok else "不一致"
        try:
            self._append("二次驗證.md", f"{name}｜第一次 {v1}{unit}｜第二次 {v2}{unit}｜{tag}")
        except Exception:  # noqa: BLE001
            pass
        return f"【二次驗證 {tag}】{name} 第一次 {v1}{unit}、第二次 {v2}{unit}"

    def _cmp(self, name, want, got, ok, unit=""):
        """10/25 審查：「要轉 vs 實測」不是同一量測做兩次，另起標籤、記進 對照.md，不混進 二次驗證 的一致率。"""
        tag = "一致" if ok else "不一致"
        try:
            self._append("對照.md", f"{name}｜要 {want}{unit}｜實測 {got}{unit}｜{tag}")
        except Exception:  # noqa: BLE001
            pass
        return f"【對照 {tag}】{name} 要 {want}{unit}、實測 {got}{unit}"

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

    def _grab_noise(self):
        """10/16 08:0x：第三人稱場景本來就在動（角色、動物），按 E 前後變化 4～19 但畫面沒換 → 先隔 0.2 秒拍兩張量背景。"""
        b1 = self.grab()
        if self.image_path or not self.live:
            return b1, 0.0
        time.sleep(0.2)
        b2 = self.grab()
        return b2, self.diff(b1, b2)

    def _changed(self, before, noise=None):
        out = self._changed_px(before, noise)
        if self.live and not self.image_path:
            tn = self._task_note(before)
            if TOOLFIX and tn and ("看不出" in out or "沒變" in out):
                # 10/24 20:2x 工具修正：任務完成時常常只有右上字換，像素差很小 → 原本寫「看不出有反應」誤導他
                out = re.sub(r"（[^）]*(看不出有反應|沒變＝可能被擋)[^）]*）", "（畫面變化小，但右上任務字變了＝**有反應**）", out)
                out = out.replace("（兩次都看不出反應）", "（像素看不出，但任務字變了，以任務字為準＝有反應）")
            out += tn
        return out

    def _task_note(self, before):
        """10/20 05:3x：長按 E 蓋印，右上任務字當場「蓋印表格→拍照」，回傳卻寫「畫面變化 4＝看不出有反應」，他檢討成「E 沒反應」
        → 按之前／之後各讀一次右上任務框，換字就寫在回傳（只給讀數）。"""
        try:
            def words(im):          # 14:4x：病人的對話泡泡飄到右上被讀進來誤報 → 只看任務框那一格（x≥0.85、y 0.12～0.30）
                return [TaskWords.norm(w[0]) for w in top_right_words(im) if w[1] >= 0.85 and 0.12 <= w[2] <= 0.30 and len(TaskWords.norm(w[0])) >= 2]
            a, b = words(before), words(self.grab())
        except Exception:  # noqa: BLE001
            return ""
        import difflib
        # 06:5x「列印章→列印徽章」OCR 漏一個字被報成換字 → 長短不一的也用相似度比
        def like(w, x):          # 19:03「盜印表嵍→蓋印表格」OCR 讀壞兩個字也報 → 一半以上的字一樣也算同一個
            return (TaskWords.same(w, x) or difflib.SequenceMatcher(None, w, x).ratio() >= 0.6
                    or len(set(w) & set(x)) >= max(2, min(len(w), len(x)) / 2))
        new = [w for w in b if not any(like(w, x) for x in a)]
        gone = [x for x in a if not any(like(w, x) for w in b)]      # 舊字還在、只是多了字（泡泡、雜訊）不算換
        if a and new and gone:
            return f"；⚑ 右上任務字變了：「{'、'.join(a)}」→「{'、'.join(b)}」（按之前／之後各讀一次任務框）"
        return ""

    def _changed_px(self, before, noise=None):
        if self.image_path:
            return "（離線模式，畫面不會變）"
        th = 2.0 if noise is None else max(2.0, noise * 2 + 4)
        bg = "" if noise is None else f"；沒按時 0.2 秒內背景本來就動 {noise:.0f}"
        after = self.grab()
        d = self.diff(before, after)
        v2 = ""
        if d < th and self.live:
            # 10/16 03:0x：死亡面板點「再玩一次」立刻量 1、下一輪已在大廳 → 介面換頁要幾秒，沒變就等 1.2 秒再量一次
            time.sleep(1.2)
            d2 = self.diff(before, self.grab())
            if d2 >= th:                                  # 10/25 審查：原本還要 d2 ≥ d+3，畫面真的變了卻回「沒變」
                return (f"畫面變化 {d2:.0f}（剛按完 {d:.0f}，等 1.2 秒後才變：反應慢{bg}）"
                        + ("；" + self._v2("有沒有反應", f"{d:.0f}", f"{d2:.0f}", True) + "（第一次還沒反應、第二次有＝反應慢，兩次結論都是有反應）" if self.verify2 else ""))
            if self.verify2:
                v2 = "；" + self._v2("有沒有反應", f"{d:.0f}", f"{d2:.0f}", d2 < th) + "（兩次都看不出反應）"
        elif self.verify2 and self.live:
            time.sleep(0.6)
            d2 = self.diff(before, self.grab())
            v2 = "；" + self._v2("有沒有反應", f"{d:.0f}", f"{d2:.0f}", d2 >= th) + ("（0.6 秒後再量也有變）" if d2 >= th else "（0.6 秒後又跟按之前差不多＝只閃一下、可能沒真的反應）")
        if d < 2:
            return f"畫面變化 {d:.0f}" + ("（沒變＝可能被擋；等 1.2 秒再看也沒變）" if self.live else "（沒變＝可能被擋）") + v2
        if d < th:
            return f"畫面變化 {d:.0f}（跟背景差不多＝看不出有反應{bg}；等 1.2 秒再看也一樣）" + v2
        return f"畫面變化 {d:.0f}" + (f"（比背景大{bg}）" if noise is not None else "") + v2

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
        if not hit and key:
            # 10/24 22:1x 稽核：目錄寫的名稱（卡住就重開、開場三件事…）不是檔名的子字串 → 字有一半以上對得上也算
            import difflib
            def score(p):
                n = re.sub(r"^\d+_|\.md$", "", os.path.basename(p))
                return max(difflib.SequenceMatcher(None, key, n).ratio(), len(set(key) & set(n)) / max(1, len(set(key))))
            best = max(files, key=score) if files else None
            hit = [best] if best and score(best) >= 0.5 else []
        if not hit:
            names = "、".join(re.sub(r"^\d+_|\.md$", "", os.path.basename(p)) for p in files)
            return f"沒有叫「{key}」的文件。有的：{names}"
        body = read_text(hit[0])
        body = re.sub(r"^---.*?---\s*", "", body, flags=re.S)
        return f"【{os.path.basename(hit[0])}】" + NL + body[:6000]

    def doc_memory(self, 類別=""):
        m = os.path.join(self.gdir, "記憶")
        table = {"玩法摘要": "玩法摘要.md", "成功檢討": "成功檢討.md", "卡住檢討": os.path.join("工具版", "檢討.md"), "死亡檢討": os.path.join("工具版", "死亡檢討.md"),
                 "操作手冊": "操作手冊.md", "遇到的問題": "遇到的問題.md", "因果": "因果.md", "學到的": "學到的.md",
                 "地圖": "研究筆記.md", "筆記": os.path.join("工具版", "筆記.md"),
                 "大檢討": os.path.join("工具版", "大檢討.md"), "卡法": os.path.join("工具版", "卡法.md"),
                 "按鈕簿": os.path.join("工具版", "按鈕簿.json"), "站位": os.path.join("工具版", "站位.md"),
                 "流程": os.path.join("工具版", "流程.md"), "成功做法": os.path.join("工具版", "成功做法.md"),
                 "死亡": os.path.join("工具版", "死亡檢討.md"), "學習整理": os.path.join("工具版", "學習整理.md"),
                 "慢想": os.path.join("工具版", "慢想.md"), "互動物": os.path.join("工具版", "互動物.md"),
                 "標點": os.path.join("工具版", "標點.md"), "地點": os.path.join("工具版", "地點.md"),
                 "觸碰物": os.path.join("工具版", "觸碰物.md"), "檢討": os.path.join("工具版", "檢討.md")}   # 10/24 稽核：工具版的檔叫得出來
        key = str(類別).strip()
        f = next((v for k, v in table.items() if key and key in k), None)
        if not f:
            return "沒有這一類。有的：" + "、".join(table)
        body = read_text(os.path.join(m, f), tail=60)
        return f"【{key}】" + NL + (body or "（還是空的）")

    def doc_tool(self, 名稱=""):
        key = str(名稱).strip()
        groups = {t["類別"] for t in self.cat["工具"]}
        if not key or key in groups or key == "全部":          # 工具清單 併進來：給類別名就列那一類
            return self.doc_list("" if key == "全部" else key)
        t = next((t for t in self.cat["工具"] if t["名稱"] == key), None)
        if not t:
            return "沒有這個工具。叫 工具說明(類別名) 看那一類有哪些：" + "、".join(sorted(groups))
        return json.dumps({k: t[k] for k in ("名稱", "用途", "用法", "例", "例子", "參數", "回傳") if k in t}, ensure_ascii=False)   # 10/26 00:0x 使用者實驗：每個工具多 3～5 個範例（例子）

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
            self._drag(90 * self.dx_deg, 0)              # 10/25 審查：不走 轉 的分段停住邏輯，四張才真的是 90／180／270／360
            time.sleep(0.4)
            im = self.grab()
            if im is not None:
                shots.append((f"右轉 {90 * (i + 1)} 度", im))
        self.extra_images = list(getattr(self, "extra_images", None) or []) + shots
        # 10/24 稽核：目錄說每張附 OCR 到的字，原本沒做 → 每張讀字（讀不到就略過）
        words = []
        for lab, im in shots:
            try:
                ws = list(self._words(im).keys())[:8]
            except Exception:  # noqa: BLE001
                ws = []
            if ws:
                words.append(f"{lab}：" + "、".join(ws))
        return ("拍了 4 張（右轉 90／180／270／360 度），下一輪附給你；方向已轉回原點"
                + (("。每張讀到的字：" + "｜".join(words)) if words else "。四張都沒讀到字"))

    # ---- 10/20 12:1x：12:06～12:08 Roblox Studio（使用者自己的專案）跑到前景，程式照樣按 a、拖右鍵、esc→l→enter，
    #      按鍵全送進 Studio → 送任何輸入前先確認前景是遊戲視窗。使用者 60 秒內有動滑鼠鍵盤＝有人在用電腦，不搶前景、不按；
    #      60 秒都沒人動才把遊戲切回前景
    FG_IDLE = 60
    # 10/23 11:2x：使用者開著 Chrome 視訊會議（「…的個人會議室」）只聽不動 66 秒 → 代理把 Roblox 切到前景搶走會議視窗。
    #   前景是會議視窗就不搶，不管多久沒人動
    NO_STEAL = re.compile(r"會議|meet|zoom|teams|webex|視訊|通話", re.I)

    def _may_steal(self, title, idle):
        return idle >= self.FG_IDLE and not self.NO_STEAL.search(str(title or ""))

    @staticmethod
    def _user_idle():
        import ctypes

        class LII(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
        li = LII()
        li.cbSize = ctypes.sizeof(LII)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(li)):
            return 0.0
        return (ctypes.windll.kernel32.GetTickCount() - li.dwTime) / 1000.0

    def fg_state(self):
        """回 (前景是不是遊戲, 前景視窗標題)。"""
        import win32gui
        from play_agent import RobloxWindow
        if self.win is None:
            self.win = RobloxWindow()
        if self.win.hwnd is None:
            self.win.find()
        if self.win.is_foreground():
            return True, ""
        try:
            title = win32gui.GetWindowText(win32gui.GetForegroundWindow())
        except Exception:  # noqa: BLE001
            title = "?"
        return False, title

    # 10/23 使用者：「她只能在最左邊的畫面使用(鎖死)」→ 送任何輸入前確認 Roblox 整個在最左邊的螢幕；
    #      跑到別的螢幕：沒人用電腦（60 秒）就搬回最左螢幕並最大化，有人在用就不按
    @staticmethod
    def _left_monitor():
        import mss
        with mss.mss() as sct:
            mons = sct.monitors[1:]
        return min(mons, key=lambda m: (m["left"], m["top"]))

    def _screen_ok(self):
        bbox = self.win.client_bbox() if self.win else None
        if not bbox:
            return
        m = self._left_monitor()
        inside = (bbox["left"] >= m["left"] and bbox["top"] >= m["top"]
                  and bbox["left"] + bbox["width"] <= m["left"] + m["width"] and bbox["top"] + bbox["height"] <= m["top"] + m["height"])
        if inside:
            return
        if self._user_idle() >= self.FG_IDLE:
            try:
                import win32gui
                win32gui.ShowWindow(self.win.hwnd, 9)                       # 先還原
                win32gui.MoveWindow(self.win.hwnd, m["left"] + 40, m["top"] + 40, 1280, 760, True)
                win32gui.ShowWindow(self.win.hwnd, 3)                       # 在最左螢幕最大化
                time.sleep(1.0)
                print(f"   ⚠ Roblox 不在最左邊的螢幕 → 已搬回最左螢幕（{m['width']}×{m['height']}）")
                return
            except Exception as e:  # noqa: BLE001
                print(f"   ⚠ 搬 Roblox 回最左螢幕失敗：{e}")
        raise RuntimeError("Roblox 視窗不在最左邊的螢幕（使用者規定只能在最左邊的畫面），這一步沒按；等一下再試")

    def _fg_ok(self):
        if not self.live or self.image_path:
            return
        self._screen_ok()
        ok, title = self.fg_state()
        if ok:
            return
        idle = self._user_idle()
        if self._may_steal(title, idle) and self.win.activate():
            print(f"   ⚠ 遊戲不在前景（前景是「{title}」，{idle:.0f} 秒沒人動）→ 已切回遊戲")
            return
        raise RuntimeError(f"遊戲視窗不在前景（前景是「{title}」，{idle:.0f} 秒前有人動過滑鼠鍵盤），這一步沒按，免得按到別的程式；等一下再試")

    # ---- 移動
    def _key(self, k, d):
        import pydirectinput
        self._fg_ok()
        pydirectinput.keyDown(k)
        time.sleep(d)
        pydirectinput.keyUp(k)

    def _walk_zoom(self, before, after, d, dd):
        """走路放大倍數量測鏈：中央 → 挖掉自己 → 中央以外鎖定的東西取中位 → (倍數或 None, 說明)。第一次與二次驗證都用同一條。"""
        z = self.zoom_factor(before, after)
        if z is None and TOOLFIX and self.self_mask is not None:
            z = self._zoom_factor_masked(before, after)          # 10/25 工具迭代：找自己 量到的那塊挖掉再量
        note = ""
        if z is None and TOOLFIX and dd >= 8:
            # 10/25 07:2x 第 14 輪：第一人稱貼近櫃檯／電腦時中央一片平、放大量不出 → 改看中央以外鎖定的東西放大幾倍（量比例 同一套），取中位
            try:
                pz = self._patch_zoom(before, after, d)
            except Exception:  # noqa: BLE001
                pz = []
            if pz:
                rs = sorted(x["r"] for x in pz)
                z = rs[len(rs) // 2]
                note = f"（中央量不出，改看旁邊 {len(pz)} 個東西的放大取中位）"
        return z, note

    DEFAULT_WALK_SPEED = 3.0        # 身高/秒（還沒校準時的預設；量比例 鎖到描述畫面給了距離的東西後換成實測）

    def walk_speed(self):
        """走路速度（每秒幾個自己的身高）→ (值, 是不是預設)。10/25 使用者：距離單位一律改成移動時間，描述畫面估的「幾個身高」要用它換算成秒。"""
        m = re.search(r"([\d.]+)", str(self.startup.get("速度", "")))
        try:
            v = float(m.group(1)) if m else 0.0
        except ValueError:
            v = 0.0
        return (v, False) if v > 0 else (self.DEFAULT_WALK_SPEED, True)

    def _heights_to_sec(self, text):
        """把字串裡的「約 N 個身高（遠）」換成「約 X 秒走路（遠）」（10/25 使用者：所有距離單位不要用公尺、改成移動時間）。"""
        v, _ = self.walk_speed()

        def rep(m):
            try:
                h = float(m.group(1))
            except ValueError:
                return m.group(0)
            return f"約 {h / v:.1f} 秒走路" + ("遠" if m.group(2) else "")
        return re.sub(r"約?\s*(\d+(?:\.\d+)?)\s*個?\s*身高(\s*遠)?", rep, str(text))

    def depth_k(self):
        """走 1 秒畫面中央放大幾倍（開工測試／量比例量的）；沒有就讀校準檔；都沒有回 None。"""
        s = str(self.startup.get("深度比例", ""))
        m = re.search(r"放大([\d.]+)倍", s)
        if m:
            return float(m.group(1))
        try:
            cal = json.load(open(os.path.join(self.gdir, "記憶", "視角校準.json"), encoding="utf-8"))
            return float(cal.get("深度", {}).get("走1秒放大倍數") or 0) or None
        except Exception:  # noqa: BLE001
            return None

    def _move(self, k, d, lo, hi):
        d = max(lo, min(hi, d))
        noise = None
        if k in ("w", "s", "a", "d", "space"):
            before = self.grab()
        else:
            before, noise = self._grab_noise()
        if self.live:
            self._key(k, d)
        else:
            print(f"   [dry-run] 按 {k} {d:.1f}s")
        if k in ("w", "s") and self.live and not self.image_path:
            # 12:3x 使用者：「深度確認也要（計算相對距離來更好走路）」→ 走／退 回傳實測中央放大倍數，對照深度比例的預期
            time.sleep(0.25)
            after = self.grab()
            dd = self.diff(before, after)
            z, z_note = self._walk_zoom(before, after, d, dd) if k == "w" else (None, "")
            if k == "w":
                self._verify_dump("走", before, after, 秒=d, 放大=z, 畫面變化=dd)
                # 10/26 01:4x 第 29 輪：記下這次走完「中央那個東西還要幾秒」，長按 E 沒反應時把它帶上（01:23～01:28 三次按 E 沒反應，01:26 走 才量到還要 8 秒）
                self._eta = (time.time(), (f"中央那個東西還要走約 {d / (z - 1):.1f} 秒才到" if (z and z > 1.02) else ("幾乎沒靠近中央那個東西" if z else "中央放大量不出、距離不知道")))
            kk = self.depth_k()
            exp = f"（照深度比例每秒 {kk} 倍，走 {d:.1f} 秒預期約 {kk ** d:.1f} 倍）" if (kk and k == "w") else ""
            # 10/26 15:0x 第 45 輪：14:52～15:03 走 0.3～0.5 秒量到放大 1.0～1.2、回「還要 7.5／12.5 秒」，他當成「離很遠」繼續往機器裡走 12 輪
            #   → 放大不到預期的四成（走了人卻沒往前＝貼著東西或被擋）就不給秒數，直說幾乎沒靠近
            # 15:3x：大廳量到深度比例 2.5 倍/秒（人群把比對撐大）→ 走 0.5 秒放大 1.12 也被說「幾乎沒往前」（9 輪 11 次）→ 預期上限 1.8 倍/秒、而且放大要真的 <1.08 才算沒往前
            kk_c = min(kk, 1.8) if kk else kk
            stuck_z = bool(z) and bool(kk) and k == "w" and (kk_c ** d - 1) > 0.03 and z < 1.08 and (z - 1) < 0.4 * (kk_c ** d - 1)
            if stuck_z:
                self._eta = (time.time(), f"走 {d:.1f} 秒旁邊的東西只放大 {z:.2f} 倍（預期 {kk_c ** d:.2f}）＝幾乎沒往前、可能貼著東西或被擋")
            z_txt = (f"；實測畫面中央放大 {z:.1f} 倍{z_note}{exp}" + (f"＝走了 {d:.1f} 秒卻只放大 {z:.2f}（預期 {kk_c ** d:.2f}）＝**幾乎沒往前**，人可能已經貼著東西或被擋住，不算距離" if stuck_z else (f"＝中央那個東西從現在位置還要走約 {d / (z - 1):.1f} 秒才到" + ("（放大倍數只有 {:.1f}、換算誤差大，可能差到一倍；只算畫面正中央那一小塊，旁邊的牆可能更近）".format(z) if z < 1.3 else "（只算畫面正中央那一小塊，旁邊的牆可能更近）") if z > 1.02 else "＝幾乎沒靠近中央那個東西")) if z else
                     ("；中央放大量不出來（這次不算距離）" + self._center_note(dd, before, after)) if (k == "w" and dd >= 2) else "")
            if TOOLFIX and k == "w" and z is None and dd >= 8:
                # 10/25 工具迭代：走路時畫面在變、中央卻有一大塊完全沒動＝釘在畫面上的東西（照片／面板／提示），不是場景裡擋路的
                sb = self.self_box
                fixed = [(x, y) for x, y in self._fixed_blocks(before, after)
                         if 0.3 <= x <= 0.7 and 0.2 <= y <= 0.8 and not (0.40 <= x <= 0.60 and y >= 0.35)      # 10/25 審查：第三人稱自己那一欄不算
                         and not (sb and sb[0] <= x <= sb[2] and sb[1] <= y <= sb[3])]
                if len(fixed) >= 4:
                    z_txt += (f"；⚑ 中央有 {len(fixed)} 格走路時完全沒動（約 ({sum(x for x, _ in fixed) / len(fixed):.2f},{sum(y for _, y in fixed) / len(fixed):.2f})）"
                              "＝釘在畫面上的東西（照片、面板、提示框），不是場景裡擋路的；不確定就 確認是否是UI，要關就 關掉 或 點 它的叉叉")
            if self.verify2:
                time.sleep(0.3)
                after2 = self.grab()
                dd2 = self.diff(before, after2)
                if k == "w" and (z or z2_needed(dd)):
                    z2, _ = self._walk_zoom(before, after2, d, dd2)      # 10/25 08:0x 第 15 輪：第二次也走同一條量測鏈（原本只量中央 → 旁邊量到的每次都「第二次量不出、不一致」）
                    ok = (z is None and z2 is None) or (z is not None and z2 is not None and abs(z - z2) <= 0.15)
                    z_txt += "；" + self._v2("走路放大倍數", f"{z:.1f}" if z else "量不出", f"{z2:.1f}" if z2 else "量不出", ok)
                else:
                    z_txt += "；" + self._v2("走了有沒有動", f"{dd:.0f}", f"{dd2:.0f}", (dd >= 2) == (dd2 >= 2))
            return f"按 {k} {d:.1f} 秒；畫面變化 {dd:.0f}" + ("（沒變＝可能被擋）" + self._nomove_note(True) if dd < 2 else self._nomove_note(False)) + z_txt
        out = self._changed(before, noise)
        if k in ("a", "d"):
            out += self._nomove_note("沒變" in out or "看不出" in out)
        if k == "e" and ("沒變" in out or "看不出" in out) and "有反應**" not in out:
            out += self._eta_note()
        elif k == "e" and ("有反應" in out or "任務字變了" in out):
            # 10/26 16:5x 使用者：「會去互動的東西都要拍照記錄」→ 按了有反應就提醒（只告訴）
            out += "（使用者交代：按了有反應的東西要 記長相 拍下來、再 記互動物 寫怎麼用，之後 找長相／走過去 直接用照片）"
        return f"按 {k} {d:.1f} 秒；" + out

    _eta = None
    _nomove = 0

    def _nomove_note(self, nomove):
        """10/26 11:5x 第 43 輪：11:36～11:48 走／右移／左移 連 8 輪畫面變化 ≤2（被碰撞體夾住），每次只回「沒變＝可能被擋」他就換個方向再試
        → 連續第 3 次起在回傳加上累計次數（只給讀數）。"""
        self._nomove = self._nomove + 1 if nomove else 0
        if self._nomove >= 3:
            return f"（**已連續 {self._nomove} 次 走／橫移 畫面都沒變＝人沒在動**，換方向平移也一樣就是被卡住了）"
        return ""

    def _eta_note(self):
        """長按 E 沒反應時，把上次 走 量到的距離讀數帶上（只給讀數，不替他決定）。3 分鐘內的才算。"""
        e = self._eta
        if not e or time.time() - e[0] > 180:
            return ""
        return f"；上次 走（{time.time() - e[0]:.0f} 秒前）量到：{e[1]}（這之後有沒有轉頭／橫移、距離還算不算數，自己判斷）"

    def see_dist(self, 標籤=""):
        r = self._see_dist_once(標籤)
        if not self.verify2 or self.image_path or not self.live or not isinstance(r, tuple):
            return r if isinstance(r, str) else r[0]
        txt, h1 = r
        time.sleep(0.3)
        r2 = self._see_dist_once(標籤)
        h2 = r2[1] if isinstance(r2, tuple) else None
        ok = h1 is not None and h2 is not None and abs(h1 - h2) <= max(0.03, 0.2 * h1)
        return txt + "；" + self._v2("估距框高", f"{h1:.2f}" if h1 is not None else "沒框到", f"{h2:.2f}" if h2 is not None else "沒框到", ok)

    def _see_dist_once(self, 標籤=""):
        """估距：用 YOLO 框高＋深度比例算「再走幾秒到面前」（目標框高到 0.5 算到面前）。"""
        labels = [x.strip() for x in re.split(r"[,，、]", str(標籤)) if x.strip()]
        if not labels:
            return "要給英文標籤，例如 估距(door)"
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
            return "沒框到：" + "、".join(labels) + "；先 轉 找到它再估"
        kk = self.depth_k()
        out = []
        import math
        for lab, ds in dets.items():
            d0 = ds[0]
            h = max(d0["h"], 0.01)
            side = "正中央" if abs(d0["cx"] - 0.5) < 0.08 else ("偏左" if d0["cx"] < 0.5 else "偏右") + f"{abs(d0['cx'] - 0.5) * 100:.0f}%"
            if h >= 0.5:
                out.append(f"{lab}：框高 {h:.2f}，已經在面前（{side}）")
            elif kk and kk > 1.05:
                secs = math.log(0.5 / h) / math.log(kk)
                out.append(f"{lab}：框高 {h:.2f}、{side}；照深度比例每秒 {kk} 倍，約再走 {secs:.1f} 秒到面前（深度比例是對校準時那個距離量的，近的東西放大得更快、這個秒數偏多、只當上限；先對正、分兩次走、中間看一眼）")
            else:
                out.append(f"{lab}：框高 {h:.2f}、{side}；深度比例還沒量（量比例），只能用框高估：越小越遠")
        first = next(iter(dets.values()))[0]["h"]
        return "；".join(out), first

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
    DRAG_FX, DRAG_FY = 0.30, 0.40    # 10/26 05:1x 第 35 輪：拖曳起點改到左上方（避開中央角色的名牌／E框，壓在 UI 上鏡頭不會動）
    # 10/26 18:5x 第 49 輪：18:40～18:51 每段都只轉到送出的 25～35%（-17→-4／-6、-15→-5、-30→-8，標點與特徵點都這麼說），
    #   起點 (0.30,0.40) 正好壓在左側黑螢幕／E框 上。量到連續轉不足就換起點輪著試；量不出的段不再「照送出算」，改用最近幾段的實測比例估
    DRAG_STARTS = ((0.30, 0.40), (0.70, 0.40), (0.50, 0.80), (0.30, 0.25))
    _turn_hist = []
    _drag_idx = 0

    def _turn_record(self, sent, measured):
        """記下一次量到的轉角（送出度、實測度），只留最近 3 分鐘 8 筆。"""
        now = time.time()
        self._turn_hist = [h for h in self._turn_hist if now - h[0] <= 600][-9:] + [(now, float(sent), float(measured))]   # 第 51 輪：留 10 分鐘

    def _recent_ratio(self):
        """最近 3 分鐘量到的「實測÷送出」合計比例（≥2 筆、每筆送出 ≥8 度才算；反方向算 0）；不夠筆數回 None。"""
        now = time.time()
        hs = [h for h in self._turn_hist if now - h[0] <= 180 and abs(h[1]) >= 8]
        if len(hs) < 2:
            return None
        sent = sum(abs(h[1]) for h in hs)
        got = sum(abs(h[2]) if (h[2] > 0) == (h[1] > 0) else 0.0 for h in hs)
        return got / sent if sent else None

    def _under_turn_note(self, sent, measured):
        """量到的不到送出的六成（送出 ≥10 度）→ 下一次換拖曳起點；回說明（沒事回空字串）。"""
        if measured is None or abs(sent) < 10:
            return ""
        if (measured > 0) == (sent > 0) and abs(measured) >= 0.6 * abs(sent):
            return ""
        old = (self.DRAG_FX, self.DRAG_FY)
        self._drag_idx = (self._drag_idx + 1) % len(self.DRAG_STARTS)
        self.DRAG_FX, self.DRAG_FY = self.DRAG_STARTS[self._drag_idx]
        pct = abs(measured) / abs(sent) * 100 if (measured > 0) == (sent > 0) else 0.0
        return (f"（這段只轉到送出的 {pct:.0f}%：拖曳起點 ({old[0]:.2f},{old[1]:.2f}) 可能壓在 UI／面板上，"
                f"下一次改從 ({self.DRAG_FX:.2f},{self.DRAG_FY:.2f}) 開始拖）")

    COMP_MAX = 12.0

    def _comp_deg(self):
        """10/26 19:2x 第 50 輪：18:40 起每段固定少轉 3～6 度（-20→-14、-22→-17／-18、+15→+9／+10、-11→-5／-6），四個拖曳起點都一樣（48～56%）
        → 不是起點壓到 UI。最近 3 分鐘 ≥3 段（送出 8～35 度、同方向）「送出−實測」中位 4～12 度、而且彼此差 ≤6 → 這次每段多送這麼多度；
        量到的照實說、跟要的比，所以補錯了下一段會看到。驗一圈／量刻度 不補（它們在量刻度本身）。"""
        # 第 51 輪（19:5x）：19:30～19:50 他每 1～2 分鐘才轉一次、一半量不出，3 分鐘內湊不到 3 筆，補償從沒觸發（+19→+14、-11→-5、+17→+12／+6 都沒補）
        #   → 窗口放到 10 分鐘、彼此差放到 8 度
        now = time.time()
        hs = [h for h in self._turn_hist if now - h[0] <= 600 and 8 <= abs(h[1]) <= 35 and (h[2] > 0) == (h[1] > 0)]
        if len(hs) < 3:
            return 0.0
        losses = sorted(abs(h[1]) - abs(h[2]) for h in hs[-5:])
        med = losses[len(losses) // 2]
        spread = losses[-1] - losses[0]
        if med < 4 or spread > 8:       # 平常每段就少 2～3 度（30→27／28），那是正常誤差、不補
            return 0.0
        return float(min(self.COMP_MAX, round(med)))

    @staticmethod
    def _comp_note(c):
        return f"（最近幾段每段都少轉約 {c:.0f} 度，這次每段多送 {c:.0f} 度補；量到的照實說）" if c else ""

    def _unmeasured_est(self, sent):
        """量不出的一段要算多少：最近幾段比例 <0.6 就照比例估（回 (估的度數, 說明)），否則照送出算。"""
        ratio = self._recent_ratio()
        if ratio is not None and ratio < 0.6:
            est = sent * ratio
            self._chunk_unsure = True
            return est, f"最近幾段只轉到送出的 {ratio * 100:.0f}%，這段多半也沒轉到，照同樣比例估約 {est:+.0f} 度（不確定）"
        return sent, ""

    def _drag(self, dx, dy, fy=None, fx=None):
        """壓右鍵拖曳（跟舊版同一套）；一次最多 220 像素，分段。fx／fy＝從畫面哪裡開始拖（預設 0.30,0.40；原本 0.5,0.5 正中央常壓到角色身上的 UI）。"""
        fy = self.DRAG_FY if fy is None else fy
        fx = self.DRAG_FX if fx is None else fx
        import pydirectinput
        if not self.live:
            print(f"   [dry-run] 轉視角 dx {dx:.0f} dy {dy:.0f}")
            return
        self._fg_ok()
        bbox = self.win.client_bbox() if self.win else None
        cx = (bbox["left"] + int(bbox["width"] * fx)) if bbox else int(1920 * fx)
        cy = (bbox["top"] + int(bbox["height"] * fy)) if bbox else int(1000 * fy)
        rest_x, rest_y = int(round(dx)), int(round(dy))   # 10/25 審查：int() 截斷 → 慢慢轉 120 步少轉 24 度
        while rest_x or rest_y:
            sx = max(-220, min(220, rest_x))
            sy = max(-120, min(120, rest_y))
            rest_x -= sx
            rest_y -= sy
            pydirectinput.moveTo(cx, cy)
            time.sleep(0.03)
            pydirectinput.mouseDown(button="right")
            # 10/26 20:0x 第 52 輪：18:40 起每次拖曳只轉到送出的 0～60%（四個起點、各種步距都一樣；驗一圈 卻回原點；20:03 兩次拖畫面全沒變、下一步 走 卻正常）
            #   → 猜是遊戲掉幀時右鍵按下後鏡頭還沒進入拖曳模式、頭幾個位移被當成游標移動吃掉。按下後先等 0.1 秒、每步放慢到 0.03、放開前等 0.05
            time.sleep(0.10)
            n = 8
            done_x = done_y = 0
            for i in range(1, n + 1):
                tx, ty = sx * i // n, sy * i // n
                pydirectinput.moveRel(tx - done_x, ty - done_y, relative=True)
                done_x, done_y = tx, ty
                time.sleep(0.03)
            time.sleep(0.05)
            pydirectinput.mouseUp(button="right")
            pydirectinput.moveTo(cx, cy)
            time.sleep(0.08)
        if self.dx_deg:
            y = self.yaw_acc + dx / self.dx_deg
            self.yaw_acc = (y + 180) % 360 - 180

    def _drag_staged(self, dx, stages=3, fy=None, pause=0.3):
        """10/26 03:0x 使用者：「每出 10 度就中斷掉轉頭來量測，去頭去尾只取中間那段算一度要多少像素」
        → 一次壓著右鍵不放，分 stages 段拖，每段拖完停 pause 秒抓一張（按鍵不放、不會有新的起點死區）。回 [起點, 第1段後, 第2段後, …]。"""
        import pydirectinput
        frames = []
        f0 = self.grab()
        if f0 is None or not self.live:
            return frames
        frames.append(f0)
        fy = self.DRAG_FY if fy is None else fy
        self._fg_ok()
        bbox = self.win.client_bbox() if self.win else None
        cx = (bbox["left"] + int(bbox["width"] * self.DRAG_FX)) if bbox else int(1920 * self.DRAG_FX)
        cy = (bbox["top"] + int(bbox["height"] * fy)) if bbox else int(1000 * fy)
        total = int(round(dx))
        pydirectinput.moveTo(cx, cy)
        time.sleep(0.03)
        pydirectinput.mouseDown(button="right")
        time.sleep(0.10)        # 第 52 輪：跟 _drag 一樣，按下後先等鏡頭進入拖曳模式
        done = 0
        try:
            for k in range(1, stages + 1):
                target = total * k // stages
                seg = target - done
                n = max(4, abs(seg) // 10)
                sub = 0
                for i in range(1, n + 1):
                    t = seg * i // n
                    pydirectinput.moveRel(t - sub, 0, relative=True)
                    sub = t
                    time.sleep(0.02)
                done = target
                time.sleep(pause)
                frames.append(self.grab())
        finally:
            pydirectinput.mouseUp(button="right")
            pydirectinput.moveTo(cx, cy)
            time.sleep(0.08)
        if self.dx_deg:
            y = self.yaw_acc + dx / self.dx_deg
            self.yaw_acc = (y + 180) % 360 - 180
        return frames

    def _mid_sample(self, px):
        """壓著不放分三段轉 px 像素，各段用特徵點量實際度數 → (每度像素_中段, 頭段少轉度, 各段度數, 說明)。"""
        fr = self._drag_staged(px, 3)
        if len(fr) < 4 or any(f is None for f in fr):
            return None, None, [], "抓不到畫面"
        degs = []
        seg = px / 3.0
        for a, b in zip(fr, fr[1:]):
            y, _p, n, c, _t = self._feat_deg(a, b, want=(seg / self.dx_deg if self.dx_deg else None))
            degs.append(None if y is None else round(y, 1))
        txt = "三段各量到 " + "／".join("量不出" if d is None else f"{d:+.1f}" for d in degs) + f" 度（每段送 {seg:+.0f} 像素）"
        mid = degs[1]
        if mid is None or abs(mid) < 2:
            return None, None, degs, txt + "；中段量不出、這次不算"
        scale = round(abs(seg) / abs(mid), 2)
        head_loss = None if degs[0] is None else round(abs(mid) - abs(degs[0]), 1)
        return scale, head_loss, degs, txt

    def cal_mid(self, 度=""):
        """量刻度中段：壓著右鍵不放轉 30 度、每 10 度停一下抓圖，去頭去尾只用中間 10 度那段的像素÷實測度數算每度 dx；
        再反向轉回來量第二次。頭段比中段少轉的度數＝拖曳起點死區（像素）。"""
        if self.image_path or not self.live:
            return "離線／dry-run 不能量刻度中段"
        deg = max(15.0, min(60.0, num(度, 30.0) or 30.0))
        px = deg * self.dx_deg
        can, dchk = self._cam_can_turn()
        if not can:
            return f"量刻度中段：{self.CAM_LOCKED_MSG}（試拖 40 像素畫面變化 {dchk:.0f}）"
        s1, h1, d1, t1 = self._mid_sample(px)
        time.sleep(0.3)
        s2, h2, d2, t2 = self._mid_sample(-px)
        out = [f"量刻度中段：壓著不放轉 {deg:.0f} 度（送 {px:.0f} 像素、分三段每段停 0.3 秒抓圖），只用中間那段算每度像素，再反向轉回來量第二次。",
               f"去：{t1}" + (f" → 中段每度 {s1} 像素" if s1 else ""),
               f"回：{t2}" + (f" → 中段每度 {s2} 像素" if s2 else "")]
        heads = [h for h in (h1, h2) if h is not None]
        scales = [x for x in (s1, s2) if x]
        try:
            self._append("刻度樣本.md", f"量刻度中段 {deg:.0f} 度｜去 {d1}｜回 {d2}｜中段每度 {s1}／{s2}｜頭段少轉 {h1}／{h2} 度｜目前 {self.dx_deg}")
        except Exception:  # noqa: BLE001
            pass
        if not scales:
            return "；".join(out) + "；兩次中段都量不出（畫面沒紋理或人太多），換個地方再量"
        if len(scales) == 2 and abs(scales[0] - scales[1]) > 0.08 * max(scales):
            return "；".join(out) + f"；去回兩次差太多（{s1} vs {s2}），不採用、刻度維持 {self.dx_deg}；換個有明顯東西的地方再量"
        scale = round(sum(scales) / len(scales), 2)
        dead = round(sum(heads) / len(heads) * scale, 1) if heads else None
        out.append(f"中段法每度 dx ＝ {scale}（目前用 {self.dx_deg}）" + (f"；頭段比中段少轉 {sum(heads) / len(heads):.1f} 度＝每次拖曳起點死區約 {dead} 像素" if dead is not None else ""))
        p = os.path.join(self.gdir, "記憶", "視角校準.json")
        try:
            cal = json.load(open(p, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            cal = {}
        _h = cal.get("水平") or {}
        r18 = self._rule18("水平每度 dx（中段法）", scale, _h.get("量測每度dx"), self.dx_deg, False)
        out.append(r18)
        if r18.endswith("不採用；"):
            return "；".join(out) + f"刻度維持 {self.dx_deg}"
        self.dx_deg = scale
        CUR_DX[0] = scale
        cal["水平"] = {"一圈dx": int(round(scale * 360)), "每度dx": scale, "量測每度dx": scale, "方法": "中段法（壓著不放轉 30 度、取中間 10 度的像素÷實測度數）",
                      "時間": datetime.now().strftime("%m/%d %H:%M"), "死區像素": dead}
        with open(p, "w", encoding="utf-8") as f:
            json.dump(cal, f, ensure_ascii=False, indent=1)
        self.startup["每度dx"] = scale
        self.startup.pop("驗證", None)
        return "；".join(out) + f"已採用、寫進校準檔；之後 轉(90) 會送 dx {90 * scale:.0f}。要確認就 驗一圈"

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

    @staticmethod
    def shift_y(a, b):
        """兩張圖的垂直位移（b 相對 a），佔畫面高的比例（景物往下移＝正＝鏡頭往上抬）。只看左右兩側 1/3，避開中央的角色。"""
        import numpy as np
        if a is None or b is None:
            return None
        W, H = 240, 180
        ga = np.asarray(a.convert("L").resize((W, H)), dtype=np.float32)
        gb = np.asarray(b.convert("L").resize((W, H)), dtype=np.float32)
        cols = np.r_[0:W // 3, 2 * W // 3:W]
        ra, rb = ga[:, cols].mean(axis=1), gb[:, cols].mean(axis=1)
        best, best_s = None, 0
        for sft in range(-120, 121):
            if sft >= 0:
                x, y = ra[:H - sft], rb[sft:]
            else:
                x, y = ra[-sft:], rb[:H + sft]
            if len(x) < 50:
                continue
            v = float(np.mean(np.abs(x - y)))
            if best is None or v < best:
                best, best_s = v, sft
        return best_s / H

    @staticmethod
    def vshift_pc(a, b):
        """12:0x：左右兩側各做一次二維相位相關（cv2.phaseCorrelate），回 [(上下位移比例, 可信度), …]。
        比列平均（shift_y）穩：地板、牆面這種上下紋理少的畫面，列平均常量成 0 → 誤判頂到。"""
        import cv2
        import numpy as np
        W, H = 240, 180
        ga = np.asarray(a.convert("L").resize((W, H)), dtype=np.float32)
        gb = np.asarray(b.convert("L").resize((W, H)), dtype=np.float32)
        win = cv2.createHanningWindow((80, H), cv2.CV_32F)
        out = []
        for x0 in (0, 160):
            (dx, dy), resp = cv2.phaseCorrelate(ga[:, x0:x0 + 80], gb[:, x0:x0 + 80], win)
            out.append((dy / H, float(resp)))
        return out

    @staticmethod
    def shift_x_moving(a, b):
        """只拿轉頭前後有變的像素量水平位移（扣掉釘在畫面上的東西）；回傳 (比例, 用到的像素比例) 或 None。
        10/17 03:0x：他手上拿拍立得照片時照片蓋在畫面中央，整欄加總的 shift_x 被釘著的照片拉到 0%，
        畫面變化 26～36 卻連報 8 次「位移 0%」，他以為游標鎖住。"""
        if a is None or b is None:
            return None
        W, H = 160, 90
        pa = a.convert("L").resize((W, H)).tobytes()
        pb = b.convert("L").resize((W, H)).tobytes()
        rows = range(0, H, 3)
        moving = {(y, x) for y in rows for x in range(W) if abs(pa[y * W + x] - pb[y * W + x]) >= 6}
        if len(moving) < len(rows) * W * 0.15:
            return None
        best, best_s = None, 0
        for s in range(-72, 73):
            tot = n = 0
            for (y, x) in moving:
                xx = x + s
                if 0 <= xx < W and (y, xx) in moving:
                    tot += abs(pa[y * W + x] - pb[y * W + xx])
                    n += 1
            if n < 300:
                continue
            v = tot / n
            if best is None or v < best:
                best, best_s = v, s
        return (best_s / W, len(moving) / (len(rows) * W)) if best is not None else None

    def _look(self, deg, axis, sign, lo, hi, default):
        CUR_DX[0] = self.dx_deg or 2.5
        deg = max(lo, min(hi, num(deg, default)))
        px = deg * (self.dx_deg if axis == "x" else self.dy_deg) * sign
        comp = self._comp_deg() if (axis == "x" and self.live and not self.image_path) else 0.0
        px_send = px + comp * self.dx_deg * sign if comp else px     # 第 50 輪：每段固定少轉就多送
        before = self.grab()
        words_before = self._words(before) if (self.live and not self.image_path) else {}
        self._drag(px_send if axis == "x" else 0, px if axis == "y" else 0)
        if self.image_path or not self.live:
            return f"轉了 {deg:.0f} 度（{'dx' if axis == 'x' else 'dy'} {px:.0f}）"
        after = self._settled()                   # 10/25 02:1x：原本固定等 0.25 秒就拍，鏡頭還在滑（第一次量 6%、0.3 秒後 21%）
        d = self.diff(before, after)
        retry_note = self._comp_note(comp)
        if d < 3:
            # 10/16 19:0x：中央剛好有 E 提示（註冊／蓋印表格）時，從中央壓右鍵會按在提示上、鏡頭不轉（變化 0～4、位移 0%）
            # → 幾乎沒轉就改從畫面上方 25% 的地方重拖一次（第一次沒轉，重拖不會轉兩倍）
            self._drag(px_send if axis == "x" else 0, px if axis == "y" else 0, fy=0.25)
            after2 = self._settled()
            d2 = self.diff(before, after2)
            retry_note += (f"（第一次從畫面中央拖，畫面變化 {d:.0f} 幾乎沒轉——中央可能有提示擋住；改從畫面上方重拖一次，"
                           + ("這次轉了）" if d2 >= 3 else "還是沒轉）"))
            after, d = after2, d2
        if d < 3 and self.live and not self.image_path:
            # 10/25 09:3x 第 18 輪：開著「辦理入住／出口」面板時滑鼠拖曳被面板吃掉、鏡頭完全不動，原本還去量標點／整張位移 → 寫成「重複花紋、僅參考」誤導他 8 輪
            wa = self._words(after)
            ui = self._ui_words([w for w, _ in self._fixed_words(words_before, wa)] + [w for w, (x, y) in wa.items() if y >= 0.6])
            return (f"**鏡頭沒轉**（從畫面中央、上方各拖一次，畫面變化都只有 {d:.0f}）＝鏡頭現在轉不動"
                    + (f"：畫面上釘著這些不是 HUD 的字，多半是開著的面板／選單按鈕：" + "、".join(f"「{w}」" for w in ui[:5]) + "；先 關掉 或 點字 把它處理掉" if ui
                       else "：可能有面板／選單擋住或游標被鎖；先 關掉，或看畫面上有沒有按鈕要點")
                    + "，再轉。這次沒量到角度、不判一致")
        hud = self._fixed_words(words_before, self._words(after))
        hud_txt = ("；轉前轉後都釘在畫面同一位置的字（HUD／面板上的字，不是場景裡的東西，轉頭對不正、要用 點字 或走近）："
                   + "、".join(f"「{w}」({x:.2f},{y:.2f})" for w, (x, y) in hud[:6])) if hud else ""
        # 10:10 又對著右上 0.90 的「蓋印表格」連轉右 6 次：那個字 OCR 讀不穩，靠字比對抓不到 → 改用純影像：
        # 整個畫面有轉動、但某幾塊幾乎沒變的區域＝釘在畫面上的 HUD／面板，直接把位置列給他
        fixed = self._fixed_blocks(before, after) if d >= 8 else []
        if fixed:
            hud_txt += "；轉頭時畫面上這幾塊完全沒動（釘在畫面上的 HUD／面板，不是場景）：" + "、".join(f"({x:.2f},{y:.2f})" for x, y in fixed[:6])
        if axis == "x":
            sh, pinned_note = self._shift(before, after, d)
            v2_txt = ""
            if self.verify2:                       # 10/18 二次驗證：隔 0.3 秒再拍一張，再量一次位移
                time.sleep(0.3)
                after2 = self.grab()
                sh2, _ = self._shift(before, after2, self.diff(before, after2))
                ok = sh is not None and sh2 is not None and abs(sh - sh2) <= 0.04
                v2_txt = "；" + self._v2("轉頭位移", f"{(sh or 0) * 100:+.0f}" if sh is not None else "量不出",
                                         f"{(sh2 or 0) * 100:+.0f}" if sh2 is not None else "量不出", ok, "%")
            # 10/16 07:0x：10/15～16 回測：|dx|>130（約 50 度以上）方向對 81、相反 70、卡搜尋上限(≥45%) 95 → 前後畫面沒重疊、量不出來
            # 第 51 輪：19:35 轉(-30) 位移 -47% 碰到搜尋上限就被判「轉太大量不出」，但 30 度本來就會移 43～47% 畫面、特徵點量 30 度一段整天都量得到
            #   → 只有超過 45 度才算轉太大；位移碰上限的照常用特徵點／標點量
            unmeasurable = sh is not None and deg > 45   # 10/25 審查：用度數判，不用像素（刻度不同時 45 度就被判量不出）
            if unmeasurable:
                sh_txt = ("轉太大，前後畫面幾乎沒有重疊，量不出景物位移和實際轉向（這次的位移不算數）；"
                          "要知道實際轉多少就分成 50 度以內一次一次轉")
            else:
                # 10/16 03:3x：他一直把「景物往左移」讀成「鏡頭往左轉」寫進檢討（刻度反）→ 量測後面標鏡頭實際轉向
                sh_txt = ((f"畫面景物實測往{'左' if (sh or 0) < 0 else '右'}移了 {abs(sh or 0) * 100:.0f}% 畫面寬"
                           + (f"（景物往{'左' if sh < 0 else '右'}移＝鏡頭往{'右' if sh < 0 else '左'}轉了）" if abs(sh) >= 0.03 else "")) + pinned_note) if sh is not None else ""
                if sh is not None or d >= 3:
                    # 10/25 使用者：工具要回饋他轉了幾度，並用一個物品標點確認（稽核後：標點優先、整張位移改左右兩帶換算、沒標點只寫僅參考不判一致）
                    want = deg * sign
                    lm_deg, lm_txt, deg_scene = self._landmark_verify(before, after, want, sh)
                    scene_txt = f"整張位移（左右兩帶）換算 ≈ 鏡頭轉了 {deg_scene:+.0f} 度" if deg_scene is not None else "整張位移換算不出度數"
                    self._verify_dump("轉", before, after, 送出度=want, 標點度=(None if getattr(self, "_feat_used", False) else lm_deg), 整張度=deg_scene, 畫面變化=d, 重複花紋=bool(getattr(self, "_rep_flag", False)), 特徵度=getattr(self, "_feat_last", None))
                    if getattr(self, "_rep_flag", False):
                        sh_txt += f"；{scene_txt}；{lm_txt}；（{self.REP_NOTE}）"
                    elif lm_deg is not None:
                        ok = abs(lm_deg - want) <= max(5.0, 0.15 * abs(want))
                        self._turn_record(want + comp * sign, lm_deg)
                        sh_txt += (f"；{scene_txt}；{lm_txt}；**你實際轉了約 {lm_deg:+.0f} 度（要轉 {want:+.0f} 度）**；"
                                   + self._cmp("轉了幾度", f"{want:+.0f}", f"{lm_deg:+.0f}", ok, "度") + self._under_turn_note(want, lm_deg))
                    elif deg_scene is not None:
                        sh_txt += f"；{lm_txt}；{scene_txt}（沒有標點，{deg_scene:+.0f} 度僅參考、不判一致；要轉 {want:+.0f} 度）"
                    else:
                        sh_txt += f"；{lm_txt}；{scene_txt}（這次量不出實際轉了幾度）"
                try:
                    self._append("刻度樣本.md", f"dx {px:.0f}｜預期 {deg:.0f} 度｜實測位移 {abs(sh or 0) * 100:.0f}%｜變化 {d:.0f}")
                except Exception:  # noqa: BLE001
                    pass
            return ((f"⚠ 刻度是捲鏡頭之前量的（{self.zoomed_at}），還沒重量；" if getattr(self, "zoomed_at", "") else "")
                    + f"預期：轉 {deg:.0f} 度（dx {px:.0f}，照刻度每度 {self.dx_deg}）→ 實測：{sh_txt}、畫面變化 {d:.0f}{retry_note}{hud_txt}{v2_txt}。"
                    "跟你腦中的對不上就 量刻度 重量")
        v2_txt = ""
        if self.verify2:
            time.sleep(0.3)
            d2 = self.diff(before, self.grab())
            v2_txt = "；" + self._v2("上下轉有沒有動", f"{d:.0f}", f"{d2:.0f}", (d >= 2) == (d2 >= 2))
        return ((f"⚠ 刻度是捲鏡頭之前量的（{self.zoomed_at}），還沒重量；" if getattr(self, "zoomed_at", "") else "")
                + f"預期：轉 {deg:.0f} 度（dy {px:.0f}，照刻度每度 {self.dy_deg}）→ 實測：畫面變化 {d:.0f}{retry_note}"
                + ("（幾乎沒變＝可能已經頂到上下限）" if d < 2 else "") + v2_txt + "。對不上就 量刻度 重量")

    @staticmethod
    def _repetitive(im):
        """畫面是不是重複花紋（天花板方磚、地磚、格線牆）→ (是不是, 週期佔畫面寬的比例)。
        10/25 04:0x：Claude 原生對著天花板磚量 轉(90) 三段都量到 12～14（標點認到隔壁同款磚），實際轉了幾度根本量不準。
        自相關（FFT）沿水平／垂直的剖面：先降到谷、再升回 ≥0.5 的峰（比谷高 ≥0.25）＝真的有週期；只看「離中心還很像」會把平滑畫面也當重複。"""
        import numpy as np
        try:
            g = np.asarray(im.convert("L").resize((240, 124)), dtype=np.float32)
        except Exception:  # noqa: BLE001
            return False, 0.0
        g = g - g.mean()
        if g.std() < 4:
            return False, 0.0
        F = np.fft.rfft2(g)
        ac = np.fft.irfft2(F * np.conj(F), s=g.shape)
        ac = ac / (ac[0, 0] + 1e-6)
        best = (False, 0.0)
        for prof, lim, scale in ((ac[0, :120], 100, 240.0), (ac[:62, 0], 50, 124.0)):   # 水平剖面、垂直剖面（未 shift：lag 從 0 起）
            prof = prof[:lim]
            i = 1
            while i + 1 < len(prof) and prof[i + 1] <= prof[i]:
                i += 1
            valley = float(prof[i]) if i < len(prof) else 1.0
            if i + 4 >= len(prof):
                continue
            j = i + 1 + int(np.argmax(prof[i + 1:]))
            peak = float(prof[j])
            if peak >= 0.5 and peak - valley >= 0.25 and j >= 6:
                best = (True, j / scale)
                break
        return best

    REP_NOTE = "⚠ 畫面是重複花紋（天花板磚／地磚／格線），標點和整張位移都可能認到隔壁同款、量到的度數會偏小——僅參考、不判一致；轉到有明顯不同東西的地方再量"

    def _landmark_turn(self, before, after, sign, want=None, pitch=0.0):
        """10/25 01:0x 使用者：「為了讓他理解自己是轉了幾度，工具也要回饋說他轉了幾度，並且也要有一個物品類的標點來確認轉動是否正確」
        轉前在畫面「轉向那一側」挑一塊最有紋理的東西當標點（右轉挑右邊、左轉挑左邊，轉完它還會在畫面裡），
        轉後找它跑到哪 → 用針孔模型換算鏡頭實際轉了幾度。回 (度數或 None, 說明)。正＝右轉。"""
        import math
        import numpy as np
        from PIL import Image
        W, H = 240, 124
        g = np.asarray(before.convert("L").resize((W, H)), dtype=np.float32)
        pw, ph = 24, 16
        xs = range(int(0.62 * W), int(0.92 * W) - pw, 4) if sign > 0 else range(int(0.08 * W), int(0.38 * W) - pw, 4)
        cands = []
        for y in range(int(0.25 * H), int(0.65 * H) - ph, 4):
            for x in xs:
                sd = float(g[y:y + ph, x:x + pw].std())
                if sd >= 12:
                    cands.append((sd, x, y))
        cands.sort(reverse=True)
        picked = []
        for sd, x, y in cands:                       # 10/25 審查：只挑最有紋理的一塊常是 HUD → 取前幾名、彼此不重疊，釘住的就換下一個
            if all(abs(x - px_) >= 8 or abs(y - py_) >= 8 for _, px_, py_ in picked):
                picked.append((sd, x, y))
            if len(picked) >= 6:
                break
        if not picked:
            return None, "標點：畫面那一側挑不到有紋理的東西"
        Wf, Hf = before.size
        th = math.tan(math.radians(self.V_FOV / 2)) * Wf / Hf
        ang = lambda v: math.degrees(math.atan((v - 0.5) * 2 * th))   # noqa: E731
        pin_thr = max(0.02, 0.3 * abs(want) * 0.0065) if want else 0.02   # 小角度時釘住的門檻跟預期位移（約 0.65%/度）成比例
        why = []
        ambiguous = 0
        self._lm_ambiguous = False
        # 10/25 05:5x：視角移動到 同時有上下角度時，標點在轉後畫面的 y 會跟著移（抬頭＝景物往下）→ 搜尋中心用預期的 y
        dy_exp = math.tan(math.radians(pitch)) / (2 * math.tan(math.radians(self.V_FOV / 2))) if pitch else 0.0
        for _, x, y in picked:
            patch = Image.fromarray(g[y:y + ph, x:x + pw].astype(np.uint8))
            x0, y0 = (x + pw / 2) / W, (y + ph / 2) / H
            y_exp = min(0.95, max(0.05, y0 + dy_exp))
            loc = self._locate(patch, after, near=(0.5, y_exp, 0.55, 0.08), second=True)   # 10/25 稽核：純左右轉 y 幾乎不變
            if not loc or loc[2] < 0.8:
                why.append(f"({x0:.2f},{y0:.2f}) 轉後找不到")
                continue
            x1, y1, sim, sim2 = loc
            if abs(y1 - y_exp) > 0.08:
                why.append(f"({x0:.2f},{y0:.2f}) 轉後上下差太多、找錯")
                continue
            if sim2 >= max(0.75, sim - 0.08):
                # 10/25 04:1x：天花板磚／地磚／格線牆上，這塊東西在轉後畫面有好幾個一樣的（第二像 ≥0.75 且跟最像差不到 0.08）
                #   → 認到隔壁同款、度數會偏小（Claude 原生 轉(90) 三段都量到 12～14）。這塊不能當標點
                why.append(f"({x0:.2f},{y0:.2f}) 轉後有好幾塊一樣的（最像 {sim:.2f}、第二像 {sim2:.2f}）＝重複花紋")
                ambiguous += 1
                continue
            if abs(x1 - x0) < pin_thr:
                why.append(f"({x0:.2f},{y0:.2f}) 轉後沒動＝釘在畫面上的字／面板")
                continue
            deg = ang(x0) - ang(x1)
            return deg, f"標點：轉前 ({x0:.2f},{y0:.2f}) 的那塊東西轉後在 ({x1:.2f},{y1:.2f})（相似度 {sim:.2f}）→ 鏡頭實際轉了 {deg:+.0f} 度"
        if ambiguous >= 2 or (ambiguous and ambiguous >= len(picked) - 1):
            self._lm_ambiguous = True
            return None, "標點：" + "、".join(why[:3]) + "，都不能當標點——畫面是重複花紋，整張位移也會認到隔壁同款、僅參考"
        return None, "標點：" + "、".join(why[:3]) + "，都不能當標點"

    @staticmethod
    def _pin_ok(dx_found, dy_found, exp_dx, exp_dy):
        """那塊東西／那個字轉完算不算「釘在畫面上」：移動量不到預期位移（目標到中央要移多少）的四成才算。
        門檻最少 0.012（找位置／OCR 會抖）、最多 0.03（搜尋半徑）。小角度時預期位移本來就小，跟著景物移了就不是 UI。"""
        thr_x = min(0.03, max(0.012, 0.4 * abs(exp_dx) + 0.005))
        thr_y = min(0.03, max(0.012, 0.4 * abs(exp_dy) + 0.005))
        return abs(dx_found) < thr_x and abs(dy_found) < thr_y

    def _pitch_from_vshift(self, before, after):
        """上下位移（兩側相位相關）換算鏡頭上下轉了幾度：景物往下移＝抬頭＝正。→ (度 或 None, 說明)"""
        import math
        try:
            pcs = self.vshift_pc(before, after)
        except Exception as e:  # noqa: BLE001
            return None, f"相位相關失敗：{e}"
        good = [d for d, r in pcs if r >= 0.15]
        if not good:
            return None, "兩側遠景都看不出上下移（可信度太低）"
        if len(good) == 2 and abs(good[0] - good[1]) > 0.04:
            return None, f"左右兩側上下位移不一致（{good[0] * 100:+.0f}%／{good[1] * 100:+.0f}%）"
        dy = sum(good) / len(good)
        deg = math.degrees(math.atan(dy * 2 * math.tan(math.radians(self.V_FOV / 2))))
        return deg, f"景物上下移了 {dy * 100:+.0f}% 畫面高 ≈ 鏡頭{'抬頭' if deg > 0 else '低頭'} {abs(deg):.0f} 度"

    _feat_mod = None

    def _feat_deg(self, before, after, want=None):
        """10/26 01:1x 第 28 輪：用 獨立驗證.py 的特徵點＋RANSAC＋針孔模型量實際轉了幾度（整張畫面幾百個點、近的東西會被當離群值剔掉）。
        大廳第三人稱時標點／整張位移常只量到真值一半（標點落在旁邊的玩家、地面——鏡頭繞著自己轉時近的東西移得少），
        獨立驗證 65 筆：特徵點法跟送出差中位 1.8°、標點說 -10 時真值 -18。→ (yaw, pitch, 點數, 可信, 說明)；量不出時 yaw None。"""
        if self._feat_mod is None:
            try:
                import importlib
                import sys as _sys
                here = os.path.dirname(os.path.abspath(__file__))
                if here not in _sys.path:
                    _sys.path.insert(0, here)
                type(self)._feat_mod = importlib.import_module("獨立驗證")
            except Exception as e:  # noqa: BLE001
                type(self)._feat_mod = False
                self._feat_err = str(e)[:80]
        if not self._feat_mod or before is None or after is None:
            return None, None, 0, 0.0, "特徵點法不能用"
        try:
            yaw, pitch, n, conf, _diag = self._feat_mod.estimate_yaw_pitch(before, after, sweep=False)
        except Exception as e:  # noqa: BLE001
            return None, None, 0, 0.0, f"特徵點法失敗：{str(e)[:60]}"
        self._feat_vert = (f"（鏡頭正低頭／抬頭約 {_diag.get('俯仰估計')} 度看，畫面是繞著轉的；照旋轉軸算左右轉了 {yaw:+.0f} 度）"
                           if (_diag or {}).get("繞垂直軸") else "")
        self._feat_how = "ORB"
        self._feat_bad = ""
        self._feat_note = ""
        if yaw is not None and conf >= 0.5 and want is not None and abs(yaw - want) > max(15.0, abs(want)):
            # 10/26 03:3x 第 33 輪：03:23 送 +17 特徵點量到 +63。看圖是真的（第一人稱貼著咖啡桌、鏡頭被帶走抬頭 19 度），ORB 跟 LoFTR 都量到 63
            #   → 差太多時用 LoFTR 再量一次：兩法一致就信（鏡頭真的被別的東西帶走了，照實說）；不一致就都不信、當量不出
            try:
                y2, _p2, n2, c2, _d2 = self._feat_mod.estimate_yaw_pitch_loftr(before, after)
            except Exception:  # noqa: BLE001
                y2, c2 = None, 0.0
            if y2 is not None and c2 >= 0.5 and abs(y2 - yaw) <= max(5.0, 0.2 * abs(yaw)):
                self._feat_note = f"（跟送出的 {want:+.0f} 差很多，但 LoFTR 也量到 {y2:+.0f}、兩法一致＝鏡頭真的轉了這麼多：可能貼著東西被帶走、或遊戲自己轉了鏡頭）"
            else:
                self._feat_bad = f"特徵點量到 {yaw:+.0f} 跟送出的 {want:+.0f} 差太多，LoFTR {'量不出' if y2 is None else f'量到 {y2:+.0f}'}、兩法不一致，都不信"
                yaw, conf = None, 0.0
        if yaw is None or conf < 0.5 or n < 15:
            # 10/26 02:5x 轉角基準（工具版/轉角基準.py、轉角估計_模型比較_1026.md）：ORB 量不出的 9 對 LoFTR 救回 4 對；合成 45 度 ORB 2/28 量不出、LoFTR 0/28 → 退路
            try:
                y2, p2, n2, c2, _d2 = self._feat_mod.estimate_yaw_pitch_loftr(before, after)
            except Exception:  # noqa: BLE001
                y2, n2, c2 = None, 0, 0.0
            if self._feat_bad:
                return None, None, n, conf, self._feat_bad
            if y2 is not None and c2 >= 0.5 and n2 >= 15 and want is not None and abs(y2 - want) > max(15.0, abs(want)):
                self._feat_bad = f"ORB 量不出、LoFTR 量到 {y2:+.0f} 跟送出的 {want:+.0f} 差太多，單一方法不信"
                y2 = None
            if y2 is not None and c2 >= 0.5 and n2 >= 15:
                self._feat_how = "LoFTR"
                return y2, p2, n2, c2, f"特徵點比對（ORB 量不出、改用 LoFTR：{n2} 個點、可信 {c2:.2f}）量到 鏡頭轉了 {y2:+.0f} 度"
            return None, None, n, conf, (self._feat_bad or f"特徵點法量不出（ORB {n} 點、可信 {conf:.2f}；LoFTR 也量不出）")
        return yaw, pitch, n, conf, f"特徵點比對（整張畫面 {n} 個點、可信 {conf:.2f}）量到 鏡頭轉了 {yaw:+.0f} 度" + self._feat_note + getattr(self, "_feat_vert", "")

    def _landmark_verify(self, before, after, want, sh=None, pitch=0.0):
        """10/25 03:0x：標點量實際轉了幾度 ＋ 跟整張位移交叉檢查（跟 _turn_chunks 同一套規則）→ (度數或 None, 說明, 整張位移換算度數或 None)。正＝右。"""
        import math
        lm_deg, lm_txt = self._landmark_turn(before, after, 1 if want > 0 else -1, want, pitch=pitch)
        self._rep_flag = bool(self._repetitive(before)[0]) or bool(getattr(self, "_lm_ambiguous", False))
        if self._rep_flag and lm_deg is not None:
            lm_txt += f"（{self.REP_NOTE}）"
        scene_deg = self._scene_deg(before, after)[0] if (sh is None or abs(sh) < 0.45) else None   # 10/25 稽核：左右兩帶換算
        if lm_deg is not None and scene_deg is not None and abs(lm_deg - scene_deg) > max(10.0, 0.5 * abs(scene_deg)):
            if abs(lm_deg - want) <= max(5.0, 0.3 * abs(want)):
                # 10/25 03:2x 第 8 輪：大廳人多時整張位移常是錯的（要 -9 量到 +11、要 -15 量到 +42），標點跟送出的角度接近就信標點
                lm_txt += f"（整張位移 {scene_deg:+.0f} 跟標點差很多，但標點跟送出的 {want:+.0f} 接近、信標點；人多時整張位移不可靠）"
            else:
                lm_txt += f"（跟整張位移 {scene_deg:+.0f} 差太多、跟送出的 {want:+.0f} 也差太多，標點可能認錯，不用）"
                lm_deg = None
        if lm_deg is not None and scene_deg is None and abs(lm_deg - want) > max(15.0, abs(want)):
            lm_txt += f"（整張量不出、標點 {lm_deg:+.0f} 又跟送出的 {want:+.0f} 差太多，不信）"
            lm_deg = None
        self._feat_used = False
        f_yaw, _fp, _fn, _fc, f_txt = self._feat_deg(before, after, want=want)
        self._feat_last = f_yaw
        if f_yaw is None and lm_deg is not None and abs(lm_deg) >= 5 and abs(want) >= 5 and (lm_deg > 0) != (want > 0):
            lm_txt += f"（標點 {lm_deg:+.0f} 跟送出的 {want:+.0f} 反方向、特徵點又量不出＝畫面沒紋理認錯了，不信）"   # 10/26 第 36 輪
            lm_deg = None
        if f_yaw is not None:
            # 10/26 第 28 輪：特徵點法可信就以它為準（大廳第三人稱時標點常只量到一半）；標點／整張留著當參考
            ref = []
            if lm_deg is not None:
                ref.append(f"標點 {lm_deg:+.0f}")
            if scene_deg is not None:
                ref.append(f"整張位移 {scene_deg:+.0f}")
            lm_txt = f_txt + (f"（{'、'.join(ref)}，僅參考）" if ref else "")
            lm_deg = f_yaw
            self._feat_used = True
            self._rep_flag = False      # 特徵點法有 RANSAC、不怕重複花紋
        return lm_deg, lm_txt, scene_deg

    SCENE_BANDS = ((0.10, 0.24), (0.24, 0.38), (0.62, 0.76), (0.76, 0.90))

    VERIFY = os.environ.get("GAMER_VERIFY", "1") == "1"

    def _verify_dump(self, tool, before, after, **info):
        """獨立驗證用：存前後畫面（960 寬 JPEG）＋工具送出／宣稱的值到 記憶/工具版/驗證/；之後用跟工具無關的方法（特徵點）算真值對照。"""
        if not self.VERIFY or before is None or after is None:
            return
        try:
            d = os.path.join(self.gdir, "記憶", "工具版", "驗證")
            os.makedirs(d, exist_ok=True)
            tag = datetime.now().strftime("%H%M%S_%f")[:9] + "_" + tool
            for nm, im in (("before", before), ("after", after)):
                w = 960
                im.convert("RGB").resize((w, int(im.height * w / im.width))).save(os.path.join(d, f"{tag}_{nm}.jpg"), quality=80)
            rec = {"時間": datetime.now().strftime("%H:%M:%S"), "工具": tool, "檔": tag, "dx_deg": self.dx_deg, "dy_deg": self.dy_deg}
            rec.update({k: (round(v, 3) if isinstance(v, float) else v) for k, v in info.items()})
            with open(os.path.join(d, "驗證.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:  # noqa: BLE001
            pass

    def _scene_deg(self, before, after):
        """整張位移換算鏡頭轉了幾度（10/25 稽核修正）：原本拿整寬的平均位移直接 atan，但針孔模型邊緣位移比中央大 1.4～2 倍，
        第三人稱中央又釘著自己 → 合成純轉 10/20/30 度量成 20/31/40。
        改成：避開中央，左右各兩條窄帶 × 上下兩塊（共 8 塊）當 2D 樣板在轉後畫面找（NCC ≥0.7），每塊用它自己的角度換算，
        只信「往中央移」的塊（轉離那一側的塊會跑出畫面、比對到亂的），取中位數。合成純轉 10 張截圖 ×7 個角度：中位誤差 0.6 度、最大 4.1。
        → (度 或 None, 說明)；正＝右轉。"""
        import math
        import numpy as np
        if before is None or after is None:
            return None, "沒畫面"
        W, H = 240, 124
        small = before.resize((W, H))
        Wf, Hf = before.size
        th = math.tan(math.radians(self.V_FOV / 2)) * Wf / Hf
        acc = []
        for x0, x1 in self.SCENE_BANDS:
            for y0, y1 in ((0.22, 0.48), (0.48, 0.74)):
                patch = small.crop((int(x0 * W), int(y0 * H), int(x1 * W), int(y1 * H)))
                if np.asarray(patch.convert("L")).std() < 6:
                    continue
                cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
                loc = self._locate(patch, after, near=(cx, cy, 0.6, 0.05))
                if not loc or loc[2] < 0.7:
                    continue
                sh = loc[0] - cx
                if abs(sh) < 0.004:
                    continue
                if (cx < 0.5 and sh < 0) or (cx > 0.5 and sh > 0):
                    continue                              # 往畫面外移＝轉離的那一側，不信
                a0 = math.atan((cx - 0.5) * 2 * th)
                a1 = math.atan((loc[0] - 0.5) * 2 * th)
                acc.append((math.degrees(a0 - a1), loc[2]))
        if not acc:
            return None, "左右兩側都找不到能比對的東西（沒紋理或都跑出畫面）"
        pos = [a for a in acc if a[0] > 0]
        neg = [a for a in acc if a[0] < 0]
        wp, wn = sum(sim for _, sim in pos), sum(sim for _, sim in neg)
        if pos and neg and abs(wp - wn) < 0.3:
            return None, f"左右兩側各說往一邊轉（{len(pos)} 塊說右、{len(neg)} 塊說左，中間可能有東西在動）"
        grp = pos if wp > wn else neg
        vals = sorted(v for v, _ in grp)
        return vals[len(vals) // 2], f"整張位移（{len(vals)} 塊往中央移的景物，取中位）換算 {vals[len(vals) // 2]:+.0f} 度"

    def _shift(self, before, after, d):
        """水平位移（整張比對；被釘著的東西拉成 0% 時改只看有變的像素）→ (比例或 None, 說明)。"""
        sh = self.shift_x(before, after)
        note = ""
        if sh is not None and abs(sh) < 0.03 and d >= 15:
            mv = self.shift_x_moving(before, after)
            if mv and abs(mv[0]) >= 0.03:
                sh = mv[0]
                note = (f"（整張比對被畫面上釘著不動的東西拉成 0%——像手上拿的東西或面板；"
                        f"只看有變的 {mv[1] * 100:.0f}% 畫面重量）")
        return sh, note

    @staticmethod
    def _ui_words(words):
        """釘在畫面上的字裡，不是常駐 HUD（輪班／理智／$…）的那些 → 多半是面板／選單的按鈕字。"""
        out = []
        for w in words:
            w = re.sub(r"^[\s)(\]\[）（｜|、,，.:：]+|[\s)(\]\[）（｜|、,，.:：]+$", "", str(w))
            if not w or any(h in w for h in TASK_HUD):
                continue
            cjk = len(re.findall(r"[一-鿿]", w))
            if cjk < 2 and len(re.findall(r"[A-Za-z]", w)) < 3:      # 10/25 09:5x：OCR 雜訊「) 561」被當成面板字
                continue
            if w not in out:
                out.append(w)
        return out

    def _words(self, im):
        """畫面上的字 → {字: (x, y)}（給轉頭前後比對用；05:00 他對著右側 0.87 固定不動的「蓋印表格」字連轉右 23 次）。"""
        try:
            import local_text
            res = local_text._win_read(im) or []
        except Exception:  # noqa: BLE001
            return {}
        W, H = im.size
        out = {}
        for r in res:
            try:
                box, txt = r[0], str(r[1]).strip()
                if len(txt) < 2:
                    continue
                xs = [p[0] for p in box]
                ys = [p[1] for p in box]
                out[txt] = ((min(xs) + max(xs)) / 2 / W, (min(ys) + max(ys)) / 2 / H)
            except Exception:  # noqa: BLE001
                continue
        if not hasattr(self, "_seen_words"):
            self._seen_words = set()
        self._seen_words |= set(out.keys())          # 這段對話讀過的字（慢慢轉拿來當 HUD 基準）
        return out

    @staticmethod
    def _fixed_blocks(a, b, cols=16, rows=9, thr=4.0):
        """轉頭前後幾乎沒變的區塊中心（0~1）。整張圖有變（呼叫端先檢查）才有意義。"""
        if a is None or b is None:
            return []
        ga = a.convert("L").resize((cols * 20, rows * 20))
        gb = b.convert("L").resize((cols * 20, rows * 20))
        pa, pb = ga.tobytes(), gb.tobytes()
        W = cols * 20
        out = []
        textured = 0
        for r in range(rows):
            for c in range(cols):
                tot, vals = 0, []
                for y in range(r * 20, r * 20 + 20, 2):
                    base = y * W
                    for x in range(c * 20, c * 20 + 20, 2):
                        tot += abs(pa[base + x] - pb[base + x])
                        vals.append(pa[base + x])
                mean = sum(vals) / len(vals)
                texture = sum(abs(v - mean) for v in vals) / len(vals)
                if texture < 6:                     # 平的牆／地板沒紋理，轉了也看不出變化，不能當 HUD
                    continue
                textured += 1
                if tot / 100 < thr:
                    out.append(((c + 0.5) / cols, (r + 0.5) / rows))
        return out if textured and len(out) <= textured * 0.3 else []      # 三成以上有紋理的區塊都沒動＝根本沒轉，不算 HUD

    @staticmethod
    def _fixed_words(a, b):
        fixed = []
        for w, (x, y) in a.items():
            if w in b and abs(b[w][0] - x) < 0.02 and abs(b[w][1] - y) < 0.02:
                fixed.append((w, (x, y)))
        return fixed

    # ---- 校準（2026-10-15 使用者：他一開始還是需要量測所有的數據，確保工具出來的結果和他腦中的一致；
    #        「還是要按照之前我要求的幾個數值逐一回報」：畫面尺寸、每度 dx、每度 dy、深度比例、第幾人稱）
    startup = {}      # 開工數值：畫面／每度dx／每度dy／深度比例／人稱／時間

    def startup_text(self):
        s = self.startup
        if not s:
            return "還沒量。登入／重進第一件事：開工測試（程式逐項量：①畫面尺寸 ②每度 dx ③每度 dy ④深度比例；⑤人稱你看畫面回報；⑥再用 驗開工 確認全部正確）"
        v = s.get("驗證")
        v_txt = ("⑥驗證 " + "、".join(f"{k}{'✓' if ok else '✗'}" for k, ok in v.items())
                 + ("（全過，開工完成）" if all(v.values()) else "（有 ✗：照回傳的指示重量再驗）")) if v else "⑥驗證 還沒驗（用 驗開工；全過才算開工完成）"
        zoom = ("⚠ 量完之後捲過鏡頭（" + self.zoomed_at + "）：②③④ 是捲之前量的、已經不準——要重新 開工測試＋驗開工（使用者交代：縮放過就要重新測量）｜"
                if getattr(self, "zoomed_at", "") else "")
        return (zoom + f"①畫面 {s.get('畫面', '?')}｜②每度 dx {s.get('每度dx', '?')}｜③每度 dy {s.get('每度dy', '?')}"
                f"｜④深度比例 {s.get('深度比例', '?')}｜⑤人稱 {s.get('人稱', '（你還沒回報，用 回報人稱(第幾人稱)）')}｜{v_txt}"
                f"｜走路速度 {s.get('速度', '還沒校準（描述畫面的距離先用預設 ' + format(self.DEFAULT_WALK_SPEED, 'g') + ' 身高/秒 換成秒；量比例 時鎖到描述畫面有給距離的東西就會校準）')}｜量於 {s.get('時間', '?')}")

    def cal_verify(self):
        y0 = self.yaw_acc
        try:
            return self._cal_verify()
        finally:
            self.yaw_acc = y0

    def _cal_verify(self):
        """驗開工：開場必做（2026-10-15 11:2x 使用者：「這個是他開場必須做的事情，確認他所有的測量都是正確的」）。
        水平：照目前刻度轉 360 度回不回原點；垂直：抬 30 再低 30 回不回原點；深度：走 1 秒再退 1 秒回不回原點。"""
        if self.image_path or not self.live:
            return "離線／dry-run 不能驗開工"
        res, notes = {}, []
        # ① 水平一圈
        r1 = self.cal_loop()
        res["一圈"] = "**回到原點**" in r1
        notes.append("水平：" + r1)
        # ② 垂直來回
        L = self._pitch_lim()                                 # 10/23：上下 ±45 度內、離極限 15% 全程以上，往空間大的那邊
        up_room = L - self.pitch_acc
        sgn = 1 if up_room >= L + self.pitch_acc else -1
        room = up_room if sgn > 0 else L + self.pitch_acc
        amp = 5.0                                            # 11:3x：從 5 度開始，畫面變化不夠才加大，最多 15 度

        def updown1(a_):
            b = self.grab()
            self._drag(0, -sgn * a_ * self.dy_deg)
            time.sleep(0.3)
            mid = self.grab()
            self._drag(0, sgn * a_ * self.dy_deg)
            time.sleep(0.3)
            a = self.grab()
            mv, bk = self.diff(b, mid), self.diff(b, a)
            self._depth_log(種類="驗開工 上下", 度數=a_, 方向=("抬" if sgn > 0 else "低"), 轉出去變化=round(mv, 1), 回來差=round(bk, 1),
                            過=(mv >= 8 and bk < 10))
            return mv, bk

        def updown():
            nonlocal amp
            mv = bk = 0.0
            for a_ in (5.0, 10.0, 15.0):
                amp = min(a_, max(3.0, room))
                mv, bk = updown1(amp)
                if mv >= 8:
                    break
            return mv, bk
        moved, back = updown()
        res["上下"] = moved >= 8 and back < 10
        if self.verify2:
            m2, b2 = updown1(amp)
            ok2 = m2 >= 8 and b2 < 10
            notes.append(self._v2("驗開工 上下", "✓" if res["上下"] else "✗", "✓" if ok2 else "✗", res["上下"] == ok2)
                         + f"（第二次抬頭變化 {m2:.0f}、回來差 {b2:.0f}）")
            res["上下"] = res["上下"] and ok2
        notes.append(f"垂直（上下限 ±{self.PITCH_LIM:.0f} 度內）：{'抬' if sgn > 0 else '低'} {amp:.0f} 度（dy {amp * self.dy_deg:.0f}）畫面變化 {moved:.0f}，再{'低' if sgn > 0 else '抬'} {amp:.0f} 度回來跟起點差 {back:.0f}"
                     + ("，回到原點" if res["上下"] else ("，抬頭時畫面沒變＝已頂到上限或刻度太小，先 仰(-20) 回到水平再驗" if moved < 8 else "，沒回到原點：每度 dy 可能不對，重 量刻度")))
        # ③ 深度來回（10/23：先轉到前方有空間的方向；從最短 0.15 秒走起，走出去畫面沒變夠才加長；驗完轉回原本方向）
        turned, how = self._face_open()
        if how:
            notes.append("深度：" + how)

        def walkback1(t, tag):
            b = self.grab()
            self._key("w", t)
            time.sleep(0.3)
            mid = self.grab()
            self._key("s", t)
            time.sleep(0.3)
            a = self.grab()
            mv, bk = self.diff(b, mid), self.diff(b, a)
            self._depth_log(種類=tag, 秒=t, 走出去變化=round(mv, 1), 退回來差=round(bk, 1), 過=(mv >= 8 and bk < mv * 0.8))
            return mv, bk

        used = {"t": self.DEPTH_TS[-1]}

        def walkback():
            mv = bk = 0.0
            for t in self.DEPTH_TS:
                mv, bk = walkback1(t, "驗開工 深度")
                used["t"] = t
                if mv >= 8:
                    break
            return mv, bk
        moved, back = walkback()
        # 12:1x 實測：退 1 秒跟起點差 63（第三人稱退走角色會轉身、鏡頭跟著擺），回不到原點是常態 →
        # 改判「有走出去、退回來有比走出去那一點更接近起點」就算深度可用
        res["深度"] = moved >= 8 and back < moved * 0.8
        if self.verify2:
            m2, b2 = walkback1(used["t"], "驗開工 深度 第二次")
            ok2 = m2 >= 8 and b2 < m2 * 0.8
            notes.append(self._v2("驗開工 深度", "✓" if res["深度"] else "✗", "✓" if ok2 else "✗", res["深度"] == ok2)
                         + f"（第二次走出去變化 {m2:.0f}、退回來差 {b2:.0f}）")
            res["深度"] = res["深度"] and ok2
        self._turn_back(turned or 0)
        notes.append(f"深度：走 {used['t']} 秒畫面變化 {moved:.0f}，退 {used['t']} 秒回來跟起點差 {back:.0f}"
                     + ("，有走出去也退得回來，深度可用" if res["深度"] else ("，走了畫面沒變＝前方被擋，換有空間的地方再驗" if moved < 8 else "，退回來反而離起點更遠（退路被擋），換個地方再驗")))
        self.startup["驗證"] = res
        self._append("開工數值.md", "驗開工：" + "、".join(f"{k}{'✓' if ok else '✗'}" for k, ok in res.items()))
        print(f"   🧪 驗開工：{self.startup_text()}")
        return ("驗開工結果：" + "、".join(f"{k} {'✓' if ok else '✗'}" for k, ok in res.items()) + NL + NL.join(notes) + NL
                + ("三項都過，開工完成。" if all(res.values()) else "有 ✗ 的照上面指示重量，再 驗開工 一次；全過才算開工完成。"))

    @staticmethod
    def zoom_factor(a, b):
        """走一步之後畫面中央放大了幾倍（深度比例）：拿 a 的中央區放大 k 倍跟 b 比，挑最像的 k。"""
        if a is None or b is None:
            return None
        if TOOLFIX:
            # 10/25 00:3x 實測：只比四周一圈 6/6 量不出（走路時兩側景物是視差、不是單純放大）→ 先照舊比中央，量不出才退回比四周
            r = Tools._zoom_factor_center(a, b)
            return r if r is not None else Tools.zoom_factor_ring(a, b)
        return Tools._zoom_factor_center(a, b)

    def _zoom_factor_masked(self, a, b):
        """挖掉 找自己 量到的自己那塊（self_mask 240×124）再比中央放大；自己佔掉中央 70% 以上就退回比四周。"""
        import numpy as np
        from PIL import Image
        if a is None or b is None or self.self_mask is None:
            return None
        W, H = 320, 180
        m = np.asarray(Image.fromarray(self.self_mask.astype(np.uint8) * 255).resize((W, H), Image.NEAREST)) > 0
        x0, y0, x1, y1 = 80, 45, 240, 135
        valid = ~m[y0:y1, x0:x1]
        if valid.mean() < 0.3:
            return Tools.zoom_factor_ring(a, b)
        ga = a.convert("L").resize((W, H))
        gb = np.asarray(b.convert("L").resize((W, H)), dtype=np.float32)[y0:y1, x0:x1][valid]
        best, best_k, v1 = None, 1.0, None
        for k10 in range(50, 101):
            k = k10 / 50.0
            w, h = 160 / k, 90 / k
            box = (int(160 - w / 2), int(90 - h / 2), int(160 + w / 2), int(90 + h / 2))
            pa = np.asarray(ga.crop(box).resize((160, 90), Image.BILINEAR), dtype=np.float32)[valid]
            v = float(np.abs(pa - gb).mean())
            if k10 == 50:
                v1 = v
            if best is None or v < best:
                best, best_k = v, k
        if best_k in (1.0, 2.0) or v1 is None or (v1 - best) < 0.08 * v1:
            return None
        return best_k

    @staticmethod
    def _zoom_factor_center(a, b):
        from PIL import Image
        ga = a.convert("L").resize((320, 180))
        gb = b.convert("L").resize((320, 180))
        W, H = 320, 180
        pb = gb.crop((80, 45, 240, 135)).tobytes()
        best, best_k, v1 = None, 1.0, None
        for k10 in range(50, 101):                 # 10/23：短距離量深度放大只有 1.1 上下 → 0.02 一格（原本 0.1）
            k = k10 / 50.0
            w, h = 160 / k, 90 / k
            box = (int(160 - w / 2), int(90 - h / 2), int(160 + w / 2), int(90 + h / 2))
            pa = ga.crop(box).resize((160, 90), Image.BILINEAR).tobytes()
            v = sum(abs(x - y) for x, y in zip(pa, pb)) / len(pb)
            if k10 == 50:
                v1 = v
            if best is None or v < best:
                best, best_k = v, k
        # 10/16 09:0x：今天 232 次走路，結果擠在 1.0（86）和 2.0（56）兩端、走 0.5 秒和 1.5 秒分佈一樣＝量不出來
        # （第三人稱角色擋在中央、鏡頭跟著走）→ 卡在搜尋兩端、或比「不放大」好不到 8% 就回 None
        if best_k in (1.0, 2.0) or v1 is None or (v1 - best) < 0.08 * v1:
            return None
        return best_k

    @staticmethod
    def zoom_factor_ring(a, b):
        """10/24 20:2x 工具修正：原本只比畫面正中央那塊（第三人稱時正好是自己的角色）→ 走路量距離 30～70% 量不出。
        改成比「中央四周一圈」：畫面 15%～85% 寬、10%～90% 高，挖掉自己角色常站的中間（x 0.40～0.60、y 0.35～0.95）。"""
        import numpy as np
        from PIL import Image
        W, H = 320, 180
        ga = a.convert("L").resize((W, H))
        gb = np.asarray(b.convert("L").resize((W, H)), dtype=np.float32)
        x0, x1, y0, y1 = int(.15 * W), int(.85 * W), int(.10 * H), int(.90 * H)
        rw, rh = x1 - x0, y1 - y0
        mask = np.ones((rh, rw), bool)
        mask[int(.35 * H) - y0:int(.95 * H) - y0, int(.40 * W) - x0:int(.60 * W) - x0] = False
        pb = gb[y0:y1, x0:x1][mask]
        best, best_k, v1 = None, 1.0, None
        for k10 in range(50, 101):
            k = k10 / 50.0
            w, h = rw / k, rh / k
            box = (int(W / 2 - w / 2), int(H / 2 - h / 2), int(W / 2 + w / 2), int(H / 2 + h / 2))
            pa = np.asarray(ga.crop(box).resize((rw, rh), Image.BILINEAR), dtype=np.float32)[mask]
            v = float(np.abs(pa - pb).mean())
            if k10 == 50:
                v1 = v
            if best is None or v < best:
                best, best_k = v, k
        if best_k in (1.0, 2.0) or v1 is None or (v1 - best) < 0.05 * v1:
            return None
        return best_k

    # 10/23 使用者：「工具有問題 深度量測的 要讓他避開牆壁 只移動短距離比較好」
    #   原本 量比例 往前走 1 秒×2（不退回）、驗開工 深度 走 1 秒退 1 秒×2 → 撞牆量不出、還會走到別處（大廳踩上配對踏板）
    #   → 先往前探 0.3 秒看兩側背景有沒有跟著動（沒動＝前面是牆／被擋），被擋就轉 90 度再探，最多四個方向；
    #     量的時候只走 0.4 秒、量完退回原地，最後轉回原本的方向
    #   11:0x 使用者：「盡量移動最少距離 拿作為實驗數據」→ 從最短 0.15 秒開始，量不出才加長（0.25→0.4）；
    #   每一次探／量都記進 記憶/工具版/深度量測.jsonl（走幾秒、哪個方向、兩側背景變化、畫面變化、放大、成不成）
    last_locks = []
    DEPTH_TS = (0.15, 0.25, 0.4)
    RATIO_TS = (0.4,)             # 11:4x 使用者「0.4秒試試看」：量比例 固定走 0.4 秒（0.15／0.25 中央放大幾乎都量不出）；驗開工 深度 照舊由短到長
    DEPTH_T = 0.4                 # 驗開工 深度、說明文字用（實際量比例 由短到長）
    PROBE_T = 0.15

    def _depth_log(self, **kw):
        try:
            with open(os.path.join(self.mdir, "深度量測.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps({"時間": datetime.now().strftime("%m/%d %H:%M:%S"), **kw}, ensure_ascii=False) + NL)
        except OSError:
            pass

    # 11:3x 使用者：「測量移動時 要避開障礙物」→ 探一小步時三種都算擋：
    #   ①兩側背景沒跟著動（貼牆／卡住）②走 0.15 秒中央就放大超過 NEAR_ZOOM 倍（前面很近有東西，再走會撞）
    #   ③退回來跟起點差很多（後面有東西、退不回原位）
    NEAR_ZOOM = 1.15

    def _blocked_ahead(self):
        b = self.grab()
        self._key("w", self.PROBE_T)
        time.sleep(0.25)
        a = self.grab()
        self._key("s", self.PROBE_T)
        time.sleep(0.25)
        c = self.grab()
        r = self._pc_pair(b, a) if (a is not None and b is not None) else None
        z = self.zoom_factor(b, a)
        out_d, back_d = self.diff(b, a), self.diff(b, c)
        why = ("兩側背景沒動（貼牆／卡住）" if (r is None or r[1] < 6) else
               f"前面很近有障礙物（走 {self.PROBE_T} 秒中央就放大 {z:.2f} 倍）" if (z and z >= self.NEAR_ZOOM) else
               "後面有東西、退不回原位" if (out_d >= 8 and back_d > out_d * 0.9) else "")
        self._depth_log(種類="探前方", 秒=self.PROBE_T, 兩側背景變=(round(r[1], 1) if r else None),
                        中央變=(round(r[0], 1) if r else None), 畫面變化=round(out_d, 1), 中央放大=z,
                        退回差=round(back_d, 1), 被擋=bool(why), 原因=why)
        return bool(why), (f"{why}" if why else f"兩側背景變 {round(r[1], 1) if r else None}")

    def _face_open(self):
        """→ (轉了幾度 或 None＝四面都擋, 說明)"""
        turned, tried = 0, []
        for _ in range(4):
            blocked, side = self._blocked_ahead()
            tried.append(f"{turned} 度{'擋：' if blocked else '有空間：'}{side}")
            if not blocked:
                return turned, ("" if turned == 0 else "前面是牆／被擋，" + "、".join(tried) + f" → 改朝 {turned} 度那邊量")
            self._drag(90 * self.dx_deg, 0)
            time.sleep(0.4)
            turned += 90
        if turned % 360:                               # 10/25 審查：四面都擋時 turned=360，不用再轉一整圈回來
            self._drag(-(turned % 360) * self.dx_deg, 0)
            time.sleep(0.4)
        return None, "四個方向往前都被擋（" + "、".join(tried) + "）"

    def _turn_back(self, turned):
        if turned:
            self._drag(-turned * self.dx_deg, 0)
            time.sleep(0.4)

    # 11:5x 使用者：「量測距離 能用 鎖定一個物品 並且朝他稍微移動 就能知道相對移動速度了（相似三角能算出來）」
    #   原本比畫面中央放大（第三人稱中央是自己角色 → 一半以上量不出）→ 鎖定中央以外的 1～3 個東西，走 t 秒後用多倍數圖塊比對找它變大幾倍 r：
    #   相似三角：還要走 t×r/(r−1) 秒才會碰到它；描述畫面有給它「約幾個身高遠」→ 走路速度＝身高 ÷ 秒
    def _pick_patches(self, im, n=3):
        import numpy as np
        W, H = 480, 248
        g = np.asarray(im.convert("L").resize((W, H)), dtype=np.float32)
        pw, ph = 48, 32
        out = []
        if self.desc_items and time.time() - getattr(self, "desc_at", 0) < 120:      # 先用描述畫面剛認出、中央以外的東西
            for it in self.desc_items:
                x, y = it["x"], it["y"]
                if 0.12 <= x <= 0.88 and 0.1 <= y <= 0.8 and not (0.38 <= x <= 0.62 and y >= 0.35) and "自己" not in it["名稱"]:
                    out.append({"名稱": it["名稱"], "x": x, "y": y, "身高": it.get("身高")})
        gy, gx = np.gradient(g)
        en = np.abs(gx) + np.abs(gy)
        cands = []
        for yy in range(int(0.1 * H), int(0.8 * H) - ph, 8):
            for xx in range(int(0.1 * W), int(0.9 * W) - pw, 8):
                cx, cy = (xx + pw / 2) / W, (yy + ph / 2) / H
                if 0.35 <= cx <= 0.65 and cy >= 0.3:                # 中央下方是自己的角色
                    continue
                cands.append((float(en[yy:yy + ph, xx:xx + pw].mean()), cx, cy))
        cands.sort(reverse=True)
        for e, cx, cy in cands:
            if len(out) >= n:
                break
            if all(abs(cx - o["x"]) > 0.12 or abs(cy - o["y"]) > 0.12 for o in out):
                out.append({"名稱": "自動挑的紋理塊", "x": cx, "y": cy, "身高": None})
        return out[:n], g

    def _patch_zoom(self, before, after, t):
        """→ [{名稱, r, 相似度, 秒到, 身高, 速度}]（只留找得到、有放大的）"""
        import numpy as np
        pats, g0 = self._pick_patches(before)
        W, H = 480, 248
        g1 = np.asarray(after.convert("L").resize((W, H)), dtype=np.float32)
        pw, ph = 48, 32
        res = []
        for p in pats:
            x0, y0 = int(p["x"] * W - pw / 2), int(p["y"] * H - ph / 2)
            if x0 < 0 or y0 < 0 or x0 + pw > W or y0 + ph > H:
                continue
            tpl = g0[y0:y0 + ph, x0:x0 + pw]
            if tpl.std() < 6:
                continue
            f = self._trk_find(g1, tpl, near=(p["x"], p["y"]), r=0.3, scales=tuple(x / 50 for x in range(50, 101)))   # 10/25 稽核：1.6 以上找不到
            if not f or f[2] < 0.7 or f[3] <= 1.01 or f[3] >= 1.98:        # 卡在上限＝不信
                continue
            r = f[3]
            sec = t * r / (r - 1)
            res.append({"名稱": p["名稱"], "r": round(r, 2), "相似度": round(f[2], 2), "秒到": round(sec, 2),
                        "身高": p["身高"], "速度": (round(p["身高"] / sec, 2) if p["身高"] else None)})
        return res

    def _depth_try(self, t, tag):
        before = self.grab()
        self._key("w", t)
        time.sleep(0.3)
        after = self.grab()
        self._key("s", t)
        time.sleep(0.3)
        back = self.grab()
        locks = self._patch_zoom(before, after, t) if (before is not None and after is not None) else []
        if locks:                                           # 鎖定的東西放大倍數取中位數
            rs = sorted(x["r"] for x in locks)
            k = rs[len(rs) // 2]
        else:
            k = self.zoom_factor(before, after)             # 鎖不到才退回比畫面中央
        d = self.diff(before, after)
        k1 = round(k ** (1 / t), 2) if k else None
        self.last_locks = locks
        # 11:4x 使用者：「0.4秒試試看 但是絕對要在範圍內」→ 走出去兩側背景要有動（沒卡住）、退回來要回到原位（比走出去那點更近起點），不然不算
        r = self._pc_pair(before, after) if (before is not None and after is not None) else None
        back_d = self.diff(before, back)
        in_range = (r is not None and r[1] >= 6) and back_d < d * 0.8
        ok = k is not None and k > 1.01 and d >= 4 and in_range
        self._depth_log(種類=tag, 秒=t, 畫面變化=round(d, 1), 兩側背景變=(round(r[1], 1) if r else None), 退回差=round(back_d, 1),
                        在範圍內=in_range, 放大=k, 換算1秒=k1, 量到=ok, 方法=("鎖定物品" if locks else "畫面中央"), 鎖定=locks)
        if not in_range:
            print(f"   ⚠ 量比例 {t} 秒：{'走的時候卡住' if not (r and r[1] >= 6) else '退回來沒回到原位'}，這次不算")
        return d, (k1 if ok else None), t

    def _depth_once(self, t=None):
        """從最短的距離開始量（量不出才加長），每次量完退回原地 → (畫面變化, 換算成走 1 秒的放大倍數, 用了幾秒)"""
        d, k1, used = 0.0, None, None
        for tt in ([t] if t else self.RATIO_TS):
            d, k1, used = self._depth_try(tt, "量比例" if not t else "量比例 第二次")
            if k1:
                break
        return d, k1, used

    def cal_depth(self):
        if self.image_path or not self.live:
            return "離線／dry-run 不能量比例"
        turned, how = self._face_open()
        if turned is None:
            return how + "，這次沒量；先離開牆角（退／橫移）到有空間的地方再量比例"
        try:
            r = self._cal_depth_measure()
        finally:
            self._turn_back(turned)
        return ((how + "（量完已轉回原本方向）；") if how else "") + r

    def _cal_depth_measure(self):
        d, k, used = self._depth_once()
        v2 = ""
        if self.verify2:                           # 10/18 二次驗證：接著再量第二次（10/23 起用第一次量到的最短秒數、量完退回）
            d2, k2, _ = self._depth_once(used if k else None)
            good = lambda dd, kk: kk is not None and kk > 1.05 and dd >= 15
            # 11:4x：k 是「換算成走 1 秒」（原始放大的 1/秒 次方），短距離的小差距換算後會被放大（0.4 秒 1.36／1.44 → 2.16／2.49）
            #   → 比原始放大（走 used 秒量到的），差 0.1 內算一致
            ok = good(d, k) and good(d2, k2) and abs(k ** used - k2 ** used) <= 0.10
            v2 = ((f"（二次驗證：兩次都量不出、不計一致率；走 {used} 秒量）") if (k is None and k2 is None) else   # 10/25 稽核：相同結果不算不一致
                  self._v2("量比例 走1秒放大", f"{k:.2f}" if k else "量不出", f"{k2:.2f}" if k2 else "量不出", ok, "倍") + f"（走 {used} 秒量、換算成 1 秒）")
            if not ok:
                if k is None and k2 is None and d >= 15 and d2 >= 15:
                    # 10/18 04:4x：兩次都「有走、但放大量不出」他只看到「換空曠處再量」→ 17 輪叫了 12 次量比例。這種是量法本身的限制，要講清楚
                    return (v2 + f"（兩次都有走出去：畫面變化 {d:.0f}、{d2:.0f}，但中央放大量不出來" + self._center_note(d) + "——"
                            "換地方重量也一樣量不出；校準檔沒改）。深度比例目前量不準，估距離用 找東西／估距 的框高")
                return (v2 + "（兩次原始放大差超過 0.1 倍或有一次量不出＝這次不算，校準檔沒改）；"
                        f"第一次畫面變化 {d:.0f}、第二次 {d2:.0f}。")
            k = round((k + k2) / 2, 2)
            v2 = "；" + v2 + "（用平均）"
        if d < 2:
            return f"走 {used} 秒畫面沒變＝前方被擋，換個有空間的地方再量比例"
        if k is None:
            return (f"走 {used} 秒畫面變化 {d:.0f}，但中央放大量不出來" + self._center_note(d) + "，這次不算，校準檔沒改；"
                    "深度比例目前量不準，估距離用 找東西／估距 的框高")
        if k <= 1.05 or d < 15:
            # 10/16 03:1x：貼牆走畫面變化 6～7、放大 1.0～1.1 倍也被寫進校準檔 → 這種不算量到
            return (f"走 1 秒中央只放大 {k:.2f} 倍、畫面變化 {d:.0f}＝幾乎沒往前（貼牆／被擋／原地），這次不算，校準檔沒改；"
                    "換前方有空間的地方再量比例")
        if k > 2.5:
            # 10/25 02:1x：大廳人群讓放大比對卡在上限 1.96（0.4 秒）→ 換算 1 秒 5.4 倍寫進校準檔，之後每次走路都「預期 2.3 倍、實測 1.1」
            return (f"走 {used} 秒換算 1 秒放大 {k:.2f} 倍——不合理（走 1 秒不可能超過 2.5 倍；多半是中央有角色／人群讓比對卡在上限），"
                    "這次不算，校準檔沒改；換前方開闊、沒有人的地方再 量比例")
        self.startup["深度比例"] = f"走1秒中央放大{k:.1f}倍"
        p = os.path.join(self.gdir, "記憶", "視角校準.json")
        try:
            cal = json.load(open(p, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            cal = {}
        cal["深度"] = {"走1秒放大倍數": k, "方法": f"中央區放大比對（工具版 量比例；走 {used} 秒量、量完退回，換算成 1 秒）", "時間": datetime.now().strftime("%m/%d %H:%M")}
        with open(p, "w", encoding="utf-8") as f:
            json.dump(cal, f, ensure_ascii=False, indent=1)
        lk = self.last_locks
        lk_txt = ""
        if lk:
            lk_txt = "；鎖定 " + "、".join(
                f"{x['名稱']}（放大 {x['r']} 倍＝還要走約 {x['秒到']} 秒" + (f"，描述畫面估它的距離換算後 → 走路速度校準 每秒 {x['速度']} 個身高（之後描述畫面的距離都用它換成秒）" if x.get("速度") else "") + "）"
                for x in lk)
            sp = [x["速度"] for x in lk if x.get("速度")]
            if sp:
                self.startup["速度"] = f"約 {sorted(sp)[len(sp) // 2]} 身高/秒"
        return (f"深度比例：換算成走 1 秒放大 {k:.1f} 倍（{'鎖定物品' if lk else '畫面中央'}，走 {used} 秒量、量完退回原地；畫面變化 {d:.0f}）{v2}{lk_txt}。"
                "相似三角：走 t 秒東西放大 r 倍＝**從走之前的位置**算還要 t×r/(r−1) 秒、**從現在位置**算還要 t/(r−1) 秒（量比例 有退回原地，所以用前者；走 沒退回，用後者）")

    def cal_startup(self):
        """開工測試：逐項量、逐項回報（使用者要求的幾個數值）。"""
        if self.image_path or not self.live:
            return "離線／dry-run 不能開工測試"
        self.yaw_acc, self.yaw_zero = 0.0, datetime.now().strftime("%H:%M")
        im = self.grab()
        r1 = self.cal_screen()
        self.startup["畫面"] = f"{im.width}×{im.height}" if im is not None else "?"
        r2 = self.cal_scale()
        self.startup["每度dx"], self.startup["每度dy"] = self.dx_deg, self.dy_deg
        r3 = self.cal_depth()
        self.startup["時間"] = datetime.now().strftime("%H:%M")
        self.startup.pop("人稱", None)
        self.startup.pop("驗證", None)          # 重量過就要重驗
        self.zoomed_at = ""                      # 10/19 10:3x：重量過，捲過的標記清掉
        line = f"🧪 開工測試：{self.startup_text()}"
        print("   " + line)
        self._append("開工數值.md", self.startup_text())
        return ("開工測試逐項回報：" + NL
                + f"① 畫面尺寸：{self.startup['畫面']}（{r1}）" + NL
                + f"② 每度 dx：{self.dx_deg}；③ 每度 dy：{self.dy_deg}（{r2}）" + NL
                + f"④ 深度比例：{self.startup.get('深度比例', '沒量到')}（{r3}）" + NL
                + "⑤ 第幾人稱：你看現在的畫面回報——看得到自己背影＝第三人稱，畫面中央有準心＝第一人稱 → 送 回報人稱(第幾人稱)。"
                + (f"（程式量到的證據：{self.person_hint}）" if self.person_hint else "") + NL
                + "⑥ 然後**必須** 驗開工：程式照這些數值轉一圈、上下來回、走退來回，確認全部回到原點；全過才算開工完成，之後才做別的")

    # ---- 10/19 10:2x 使用者：「他要記錄應該是第幾人稱，如果畫面是第三人稱就要放大鏡頭（中鍵），相反也一樣」
    def _persp_should(self):
        if not hasattr(self, "_persp"):
            try:
                self._persp = json.load(open(os.path.join(self.mdir, "人稱.json"), encoding="utf-8"))
            except (OSError, ValueError):
                self._persp = {}
        return self._persp

    def cal_persp_should(self, 人稱="", 為什麼=""):
        v = str(人稱).strip()
        if v not in ("第一人稱", "第三人稱"):
            return "要寫 第一人稱 或 第三人稱，例：記應該人稱(第一人稱,看得到正前方不會被自己的角色擋)"
        self._persp = {"應該": v, "為什麼": str(為什麼).strip(), "時間": datetime.now().strftime("%H:%M")}
        with open(os.path.join(self.mdir, "人稱.json"), "w", encoding="utf-8") as f:
            json.dump(self._persp, f, ensure_ascii=False)
        self._append("開工數值.md", f"應該人稱:{v}｜為什麼:{為什麼}")
        return f"記下：應該是 {v}（{為什麼 or '沒寫為什麼'}）。之後每輪面板【人稱】會比 應該 跟 現在（你 回報人稱 的）。" + NL + self.persp_text()

    def persp_text(self):
        should = self._persp_should().get("應該")
        now = self.startup.get("人稱")
        head = (f"【人稱】應該：{should or '（還沒記，用 記應該人稱(第一人稱或第三人稱,為什麼) 記下）'}"
                f"｜現在：{now or '（還沒確認，用 確認人稱 讓程式量，或看畫面 回報人稱）'}")
        if should and now and should not in now and now in ("第一人稱", "第三人稱"):
            # 10/26 14:3x 使用者：「叫他不要去捲鏡頭」→ 不再提示用 捲 切人稱；照現在的人稱玩
            head += "｜不一樣，但使用者 10/26 交代：**不要捲鏡頭**，照現在的人稱玩就好"
        return head

    # ---- 10/19 10:5x 使用者：「他需要知道自己在第幾人稱，需要三個：這個工具可以確保第幾人稱、是否畫面中間有東西、
    #      而且會跟隨玩家移動」→ 確認人稱：程式自己轉頭／走一下，量中央那塊跟不跟著動
    PC_CENTER = (0.42, 0.40, 0.58, 0.82)                                  # 第三人稱自己的角色在這附近
    PC_SIDES = ((0.14, 0.15, 0.32, 0.70), (0.66, 0.15, 0.82, 0.70))       # 兩側背景（避開左側按鈕、右側任務框、左下 HUD）

    @staticmethod
    def _gray(im):
        import numpy as np
        return np.asarray(im.convert("L").resize((320, 180)), dtype=float)

    @staticmethod
    def _box(g, b):
        h, w = g.shape
        return g[int(b[1] * h):int(b[3] * h), int(b[0] * w):int(b[2] * w)]

    def _pc_pair(self, a, b):
        """一對畫面：中央變化、兩側變化、比值、跟不跟著動。"""
        import numpy as np
        ga, gb = self._gray(a), self._gray(b)
        c = float(np.mean(np.abs(self._box(ga, self.PC_CENTER) - self._box(gb, self.PC_CENTER))))
        side = float(np.mean([np.mean(np.abs(self._box(ga, x) - self._box(gb, x))) for x in self.PC_SIDES]))
        if side < 6:
            return c, side, None, None                        # 背景沒動：量不出
        ratio = c / side
        return c, side, ratio, ratio < 0.5

    def _pc_track(self, a, b):
        """12:5x：追中央一小塊在轉頭後跑到哪（俯視第三人稱自己的角色很小，中央那塊大多是地磚，變化量法會判錯）。
        回 (中央位移, 背景位移, 中央相似度, 跟不跟著)；背景沒動回 None。"""
        import math
        W, H = 240, 124
        ga = a.convert("L").resize((W, H))
        res = {}
        for name, (cx, cy) in (("中", (0.50, 0.52)), ("左", (0.24, 0.40)), ("右", (0.76, 0.40))):
            pw, ph = 24, 24
            x0, y0 = int(cx * W - pw / 2), int(cy * H - ph / 2)
            patch = ga.crop((x0, y0, x0 + pw, y0 + ph))
            r = self._locate(patch, b, near=(cx, cy, 0.35))
            res[name] = (math.hypot(r[0] - cx, r[1] - cy), r[2]) if r else (None, 0.0)
        bg = [v[0] for k, v in res.items() if k != "中" and v[0] is not None and v[1] >= 0.4]
        bgd = max(bg) if bg else 0.0
        cd, _ = res["中"]
        # 直接比前後兩張「正中央同一個位置」像不像（跟著你的東西會一直在那裡）
        import numpy as np
        pa = np.asarray(ga.crop((108, 52, 132, 76)), dtype=np.float32)
        pb = np.asarray(b.convert("L").resize((W, H)).crop((108, 52, 132, 76)), dtype=np.float32)
        pa, pb = pa - pa.mean(), pb - pb.mean()
        stay = float((pa * pb).sum() / (np.sqrt((pa ** 2).sum() * (pb ** 2).sum()) + 1e-6))
        if bgd < 0.03 or float(np.sqrt((pa ** 2).mean())) < 15:      # 背景沒動、或中央沒紋理（白牆）→ 量不出
            return cd, bgd, stay, None
        # 12:5x 實測：大廳第三人稱 原位相似 0.51～0.53、別處最像 0.57～0.61（地磚湊巧像）；第一人稱中央被轉走、原位很低
        best = res["中"][1]
        return cd, bgd, stay, (stay >= 0.4 and stay >= best - 0.15)

    def cal_persp_check(self):
        """確認人稱：①畫面中間有沒有東西 ②那個東西跟不跟著玩家移動（轉頭兩次＋走一下）③判斷第幾人稱並記下。"""
        if self.image_path or not self.live:
            return "離線／dry-run 不能確認人稱"
        import numpy as np
        a = self.grab()
        if a is None:
            return "抓不到畫面"
        g = self._gray(a)
        center, ring = self._box(g, self.PC_CENTER), np.concatenate([self._box(g, x).ravel() for x in self.PC_SIDES])
        tex = float(np.std(center))
        contrast = abs(float(np.mean(center)) - float(np.mean(ring)))
        has_thing = tex >= 12 or contrast >= 20
        px = 20 * self.dx_deg
        self._drag(px, 0)
        time.sleep(0.4)
        b = self.grab()
        self._drag(-px, 0)
        time.sleep(0.4)
        c = self.grab()
        self._key("w", 0.4)
        time.sleep(0.3)
        d = self.grab()
        self._key("s", 0.4)
        time.sleep(0.2)
        t1, t2, wk = self._pc_track(a, b), self._pc_track(b, c), self._pc_pair(c, d)

        def fmt(name, r):
            cc, ss, ratio, fol = r
            if ratio is None:
                return f"{name}：背景只變 {ss:.0f}（幾乎沒動），量不出"
            return f"{name}：兩側背景變 {ss:.0f}、中央變 {cc:.0f}（比值 {ratio:.2f}）"

        def fmt_t(name, r):
            cd, bgd, sim, fol = r
            if fol is None:              # 10/22 12:2x：背景有動但中央沒紋理也走這裡，原本一律寫「幾乎沒動」誤導
                if bgd < 0.03:
                    return f"{name}：背景只移 {bgd * 100:.0f}% 畫面（幾乎沒動），量不出"
                return f"{name}：背景移了 {bgd * 100:.0f}% 畫面，但正中央一片平、沒有花紋可以比（可能是牆、單色的東西），量不出"
            return (f"{name}：背景移了 {bgd * 100:.0f}% 畫面、正中央前後還像不像 {sim:.2f} → "
                    + ("中央那塊留在畫面中間＝跟著你" if fol else "中央那塊跟著景物被轉走"))
        # 12:4x：只用轉頭兩票判斷（第三人稱轉頭時角色站著不動、可靠）；走路時角色在播動畫，那票只列數字參考
        votes = [r[3] for r in (t1, t2) if r[3] is not None]
        if len(votes) == 2 and votes[0] == votes[1]:
            verdict = "第三人稱" if votes[0] else "第一人稱"
        else:
            verdict = None
        v2 = ""
        if self.verify2 and t1[3] is not None and t2[3] is not None:
            v2 = "；" + self._v2("確認人稱 轉頭", "跟著" if t1[3] else "不跟", "跟著" if t2[3] else "不跟", t1[3] == t2[3])
        lines = [f"① 畫面中間有沒有東西：{'有' if has_thing else '看不太出來'}（中央紋理 {tex:.0f}、跟兩側亮度差 {contrast:.0f}）",
                 "② 會不會跟著你移動：" + "；".join([fmt_t("轉右 20 度", t1), fmt_t("轉回來", t2), fmt("走 0.4 秒（只參考，不算票：第三人稱走路時角色在播動畫）", wk)]) + v2]
        if verdict:
            self.startup["人稱"] = verdict
            self._append("開工數值.md", f"人稱:{verdict}（確認人稱 量的：中央位移 {t1[0]}／{t2[0]}、背景 {t1[1]}／{t2[1]}）")
            # 10/26 18:3x 第 48 輪：18:24 確認人稱 兩次都量到第三人稱，18:25 回報人稱 卻提醒「程式量到的證據是第一人稱」
            #   （引用的是更早的自動偵測）→ 確認人稱 量出來就把它當最新證據
            self.person_hint = f"{verdict}（確認人稱 量的：轉頭時中央那塊{'留在畫面中間' if verdict == '第三人稱' else '跟著景物被轉走'}、背景移了 {t1[1]}／{t2[1]}）"
            lines.append(f"③ 判斷：**{verdict}**（{'中間有東西、而且轉頭和走路時都留在畫面中間＝你自己的角色' if verdict == '第三人稱' else '中間沒有跟著你的東西，整張畫面一起動'}）→ 已記成現在的人稱")
        else:
            lines.append("③ 判斷：量不出來（背景沒動或結果互相矛盾；可能開著選單、貼著牆）→ 沒改現在的人稱；離開選單／離牆再叫一次，或自己看畫面 回報人稱")
        lines.append(self.persp_text())
        return NL.join(lines)

    def _center_note(self, d, a=None, b=None):
        """10/19 11:0x：原本寫死「角色擋在畫面中央」——醫院裡是第一人稱、中央根本沒有角色。
        12:4x：用走路前後畫面判斷也不可靠（第三人稱走路時角色在播動畫、貼著欄杆走畫面也會變）→ 不解讀，只給量到的數字。"""
        r = self._pc_pair(a, b) if (a is not None and b is not None) else None
        if r is None:
            return "（原因不確定；不知道自己第幾人稱就叫 確認人稱）"
        side = r[1]
        if side < 6:
            return f"（兩側背景只變 {side:.0f}＝背景幾乎沒動，可能被擋住了；畫面變化多半是角色動作）"
        return f"（兩側背景變 {side:.0f}＝背景有在動；中央量不出的原因程式判斷不了，不知道自己第幾人稱就叫 確認人稱）"

    # ---- 10/20 11:0x 使用者：「工具：確認是否是UI｜用途：確認物品是否是UI｜方式：轉一下頭看有沒有動後再轉回來（雙重驗證）
    #      ｜直接是程式不要思考，只要回報哪裡是UI」→ 不叫大腦：轉右 15 度、轉回來，兩次都沒動的區塊＝UI
    @staticmethod
    def _still_cells(a, b, cols=16, rows=9, thr=4.0):
        """回 (沒動的格子, 有紋理的格子)；格子 = (欄, 列)。沒紋理的平牆／地板看不出動沒動，不算。"""
        ga = a.convert("L").resize((cols * 20, rows * 20))
        gb = b.convert("L").resize((cols * 20, rows * 20))
        pa, pb = ga.tobytes(), gb.tobytes()
        Wd = cols * 20
        still, textured = set(), set()
        for r in range(rows):
            for c in range(cols):
                tot, vals = 0, []
                for y in range(r * 20, r * 20 + 20, 2):
                    base = y * Wd
                    for x in range(c * 20, c * 20 + 20, 2):
                        tot += abs(pa[base + x] - pb[base + x])
                        vals.append(pa[base + x])
                mean = sum(vals) / len(vals)
                if sum(abs(v - mean) for v in vals) / len(vals) < 6:
                    continue
                textured.add((c, r))
                if tot / 100 < thr:
                    still.add((c, r))
        return still, textured

    def cal_ui_check(self, 位置=""):
        """確認是否是UI：轉右 15 度拍一張、轉回來再拍一張；兩次轉頭都沒動的區塊＝釘在畫面上的 UI。只回報位置，不判斷別的。"""
        if self.image_path or not self.live:
            return "離線／dry-run 不能確認UI"
        cols, rows = 32, 18                       # 16×9 太粗：右上任務框跨兩列、每格都混到場景就抓不到
        a = self.grab()
        if a is None:
            return "抓不到畫面"
        wa = self._words(a)
        px = 15 * self.dx_deg
        self._drag(px, 0)
        time.sleep(0.4)
        b = self.grab()
        wb = self._words(b)
        self._drag(-px, 0)
        time.sleep(0.4)
        c = self.grab()
        wc = self._words(c)
        d1, d2 = self.diff(a, b), self.diff(b, c)
        s1, t1 = self._still_cells(a, b, cols, rows)
        s2, t2 = self._still_cells(b, c, cols, rows)
        # 有轉到的證據：轉過去、轉回來都有 3 格以上明顯動了（面對平牆整張變化小，但牆角、線條還是會動）
        if len(t1 - s1) < 3 or len(t2 - s2) < 3:
            return (f"確認是否是UI：轉頭時看不出畫面有動（轉過去變 {d1:.0f}、有動的格子 {len(t1 - s1)}；轉回來變 {d2:.0f}、{len(t2 - s2)}）"
                    "——可能開著選單、整面平牆或沒轉到，量不出哪裡是 UI；離牆／關掉選單再叫一次")
        ui = s1 & s2                                   # 雙重驗證：轉過去、轉回來都沒動
        # 相鄰的格子併成一塊
        regions, seen = [], set()
        for cell in sorted(ui):
            if cell in seen:
                continue
            stack, comp = [cell], []
            seen.add(cell)
            while stack:
                x, y = stack.pop()
                comp.append((x, y))
                for nb in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                    if nb in ui and nb not in seen:
                        seen.add(nb)
                        stack.append(nb)
            xs, ys = [q[0] for q in comp], [q[1] for q in comp]
            regions.append((min(xs) / cols, (max(xs) + 1) / cols, min(ys) / rows, (max(ys) + 1) / rows))
        import difflib

        def stay(w, xy, other):            # 另一張同一個位置（±0.02）有一樣或很像的字（OCR 會抖）
            return any(abs(x2 - xy[0]) < 0.02 and abs(y2 - xy[1]) < 0.02 and (w2 == w or difflib.SequenceMatcher(None, w, w2).ratio() >= 0.6)
                       for w2, (x2, y2) in other.items())
        def cell_of(xy):
            return (min(cols - 1, int(xy[0] * cols)), min(rows - 1, int(xy[1] * rows)))
        # 以畫面格子為準（OCR 會漏讀，不能拿「另一張沒讀到」當成有動）：格子沒動或字兩次都在原位＝UI；格子轉頭時有動＝場景
        ui_words = [(w, xy) for w, xy in wa.items() if cell_of(xy) in ui or (stay(w, xy, wb) and stay(w, xy, wc))]
        moved_words = [(w, xy) for w, xy in wa.items() if (w, xy) not in ui_words
                       and cell_of(xy) in (t1 - s1) and cell_of(xy) in (t2 - s2)]

        def inside(xy, rg):
            return rg[0] - 0.02 <= xy[0] <= rg[1] + 0.02 and rg[2] - 0.02 <= xy[1] <= rg[3] + 0.02
        lines = [f"確認是否是UI（轉右 15 度再轉回來，兩次都沒動＝UI；轉過去畫面變 {d1:.0f}、轉回來變 {d2:.0f}）"]
        # 畫面正中央那塊轉頭也沒動＝跟著鏡頭轉的東西，不是介面，另外列（只給讀數）
        center = [r for r in regions if 0.35 <= (r[0] + r[1]) / 2 <= 0.65 and 0.30 <= (r[2] + r[3]) / 2 <= 0.85]
        regions = [r for r in regions if r not in center]
        ui_words = [(w, xy) for w, xy in ui_words if not any(inside(xy, r) for r in center)]
        if center:
            lines.append("畫面中央這塊轉頭時也沒動（跟著鏡頭一起轉的東西，不算 UI）：" + "；".join(
                f"x {r[0]:.2f}～{r[1]:.2f}、y {r[2]:.2f}～{r[3]:.2f}" for r in center[:3]))
        if regions:
            lines.append("UI（釘在畫面上，轉頭不會動、走不過去、不用對正）：" + "；".join(
                f"x {r[0]:.2f}～{r[1]:.2f}、y {r[2]:.2f}～{r[3]:.2f}"
                + (("（字：" + "、".join(f"「{w}」" for w, xy in ui_words if inside(xy, r)) + "）") if any(inside(xy, r) for _, xy in ui_words) else "")
                for r in regions[:10]))
        else:
            lines.append("UI：沒有找到兩次轉頭都沒動的區塊")
        loose = [(w, xy) for w, xy in ui_words if not any(inside(xy, r) for r in regions)]
        if loose:
            lines.append("UI 上的字（兩次轉頭位置都沒變）：" + "、".join(f"「{w}」({xy[0]:.2f},{xy[1]:.2f})" for w, xy in loose[:10]))
        if moved_words:
            lines.append("不是 UI（轉頭時有跟著動＝場景裡的字）：" + "、".join(f"「{w}」({xy[0]:.2f},{xy[1]:.2f})" for w, xy in moved_words[:8]))
        m = re.findall(r"[-+]?\d*\.?\d+", str(位置))
        if len(m) >= 2:
            x, y = float(m[0]), float(m[1])
            cell = (min(cols - 1, int(x * cols)), min(rows - 1, int(y * rows)))
            near_ui = [w for w, xy in ui_words if abs(xy[0] - x) < 0.05 and abs(xy[1] - y) < 0.04]
            in_center = any(r[0] <= x <= r[1] and r[2] <= y <= r[3] for r in center)
            if in_center:
                lines.append(f"你問的 ({x:.2f},{y:.2f})：在畫面中央跟著鏡頭轉的那塊裡（不算 UI）")
            elif cell in ui or near_ui:
                lines.append(f"你問的 ({x:.2f},{y:.2f})：**是 UI**（兩次轉頭都沒動" + (f"；那裡的字「{near_ui[0]}」" if near_ui else "") + "）")
            elif cell in t1 and cell in t2:
                lines.append(f"你問的 ({x:.2f},{y:.2f})：**不是 UI**（轉頭時有動＝場景裡的東西）")
            else:
                lines.append(f"你問的 ({x:.2f},{y:.2f})：那一塊太平（沒紋理），看不出動沒動")
        return NL.join(lines)

    def cal_persp(self, 人稱=""):
        v = str(人稱).strip()
        if not v:
            return "要寫 第一人稱 或 第三人稱"
        self.startup["人稱"] = v
        self._append("開工數值.md", f"人稱:{v}")
        # 12:3x：兩種人稱上下全程不同（第一 240、第三 520）→ 照他回報的人稱換上下極限用的全程（像素）
        note = ""
        mode = "第一人稱" if "第一" in v else ("第三人稱" if "第三" in v else "")
        try:
            vr = (json.load(open(os.path.join(self.gdir, "記憶", "視角校準.json"), encoding="utf-8")).get("垂直") or {}).get(f"全程_{mode}")
        except Exception:  # noqa: BLE001
            vr = None
        if mode and vr:
            self.vrange_px = int(vr)
            note = f"（上下極限改用{mode}量到的全程 {vr} 像素：離極限 15% 以上、±45 度內）"
        if self.person_hint and mode and not self.person_hint.startswith(mode):
            note += f"（提醒：程式量到的證據是「{self.person_hint}」，跟你回報的不一樣）"
        print(f"   🧪 人稱回報：{v}｜開工數值：{self.startup_text()}")
        return f"記下：{v}{note}。開工數值現在：{self.startup_text()}"

    def _cam_can_turn(self):
        """10/26 08:5x 第 40 輪：攝影機監看模式／面板鎖住時鏡頭完全轉不動，驗一圈 900 卻回「回到原點、位移 0%」、量刻度 900／900 二次一致
        → 量之前先拖 40 像素看畫面有沒有變（再拖回來）。回 (轉得動, 畫面變化)。"""
        a = self.grab()
        if a is None:
            return True, 0.0
        time.sleep(0.25)
        a2 = self.grab() or a
        noise = self.diff(a, a2)
        self._drag(40, 0)
        time.sleep(0.25)
        b = self.grab()
        d = self.diff(a2, b)
        self._drag(-40, 0)
        time.sleep(0.2)
        return d >= max(3.0, noise * 2 + 2), d

    CAM_LOCKED_MSG = ("**鏡頭轉不動**（拖了畫面沒變）——這次不算、不是回到原點／不是刻度對。可能有面板、選單或監看模式把滑鼠鎖住；"
                      "先看畫面上有什麼擋著、用 關掉 或點面板自己的按鈕，鏡頭會轉了再量")

    def cal_loop(self, dx=""):
        """驗一圈：他自己算「轉 360 度要送多少 dx」填進來（不寫＝照目前刻度 360×每度dx），程式拖完比對起點畫面，
        回報有沒有回到原點、偏多少（2026-10-15 11:1x 使用者：「他自己需要試試看，按照他的計算，測試轉 360 度是否回到原點，製作成工具」）。"""
        if self.image_path or not self.live:
            return "離線／dry-run 不能驗一圈"
        total = num(dx, None)
        if total is None:
            total = 360 * self.dx_deg
            src = f"照目前刻度 360×{self.dx_deg}"
        else:
            src = "照你填的"
        total = int(round(total))
        if not (200 <= abs(total) <= 3000):
            return f"dx {total} 不合理（一圈通常 500～1500）"
        start0 = self.grab()
        if start0 is None:
            return "抓不到畫面"
        can, dchk = self._cam_can_turn()
        if not can:
            return f"驗一圈：{self.CAM_LOCKED_MSG}（試拖 40 像素畫面變化 {dchk:.0f}）"
        # 10/16 13:0x：大廳裡 8 次驗一圈，畫面差 40～69、位移卡在 ±47% 上限也照給「調大／調小」，最後一次差 50、位移 4% 被判「回到原點」
        # → 他把刻度從 2.5 改成 5.25。改：先量背景（沒轉時 0.4 秒內本來就動多少），差要在背景範圍內＋位移 <4% 才算回到原點
        time.sleep(0.4)
        start = self.grab() or start0
        noise = self.diff(start0, start)
        self._drag(total, 0)
        time.sleep(0.4)
        end = self.grab()
        d = self.diff(start, end)
        sh = self.shift_x(start, end)
        tol = max(10.0, noise * 2 + 6)
        same_view = d <= tol
        back = same_view and sh is not None and abs(sh) < 0.04
        implied = round(abs(total) / 360.0, 2)
        if back:
            hint = "終點畫面跟起點幾乎重合"
        elif sh is None or abs(sh) < 0.04:
            hint = (f"畫面差 {d:.0f} 遠大於背景（沒轉時本來就動 {noise:.0f}），位移卻量不出偏移＝前後不是同一個畫面、差很多，"
                    "這次看不出該調大還是調小；用 量刻度 重量")
        elif abs(sh) >= 0.30:
            hint = (f"終點偏了 {abs(sh) * 100:.0f}% 畫面寬以上，前後幾乎沒重疊，量不準偏多少和往哪邊；"
                    "這次看不出該調大還是調小，用 量刻度 重量比一直猜快")
        else:
            hint = (f"終點畫面比起點{'偏左' if sh < 0 else '偏右'} {abs(sh) * 100:.0f}% 畫面寬＝轉{'過頭' if sh < 0 else '不夠'}；"
                    f"你填的每度 dx {implied} 要{'調小' if sh < 0 else '調大'}一點再驗")
        try:
            self._append("刻度樣本.md", f"驗一圈 dx {total}（{src}）｜跟起點差 {d:.0f}｜位移 {(sh or 0) * 100:+.0f}%｜{'回到原點' if back else '沒回到原點'}")
        except Exception:  # noqa: BLE001
            pass
        v2 = ""
        if self.verify2:                           # 10/18 二次驗證：同樣的 dx 再轉一圈，第二圈也要回到起點
            self._drag(total, 0)
            time.sleep(0.4)
            end2 = self.grab()
            dd2 = self.diff(start, end2)
            sh2 = self.shift_x(start, end2)
            back2 = dd2 <= tol and sh2 is not None and abs(sh2) < 0.04
            v2 = "；" + self._v2("驗一圈 位移", f"{(sh or 0) * 100:+.0f}", f"{(sh2 or 0) * 100:+.0f}", back == back2, "%") + \
                 f"（第二圈跟起點差 {dd2:.0f}、{'回到原點' if back2 else '沒回到原點'}）"
            back = back and back2
        return (f"驗一圈：送了 dx {total}（{src}，等於每度 {implied}）→ 跟起點畫面差 {d:.0f}（背景本來就動 {noise:.0f}）、位移 {(sh or 0) * 100:+.0f}% 畫面寬 → "
                + ("**回到原點**，刻度對" if back else "**沒回到原點**；" + hint) + v2
                + "。確定對了就用 定刻度(每度dx) 寫進校準檔")

    def cal_set(self, 每度dx=""):
        v = num(每度dx, None)
        if v is None or not (0.5 <= v <= 20):
            return "定刻度 要給每度 dx（0.5～20），例如 定刻度(2.5)"
        old = self.dx_deg
        if self.live and not self.image_path and abs(v - old) >= 0.05:
            # 10/25 23:4x 第 25 輪：定刻度 以前不驗就寫（他把大廳量壞的 1300/360＝3.7 寫進去，之後每次轉都過頭 48%）→ 先照新刻度轉一圈驗
            self.dx_deg = round(v, 2)
            r = self.cal_loop("")
            if "**回到原點**" not in r:
                self.dx_deg = old
                CUR_DX[0] = old
                return (f"定刻度({v})：照它轉一圈**沒回到原點**，不寫進校準檔、刻度維持 {old}。驗一圈說：" + r[:260]
                        + "；大廳人多或畫面沒紋理時一圈量不準，到局內有明顯東西的地方再 量刻度／驗一圈")
        self.dx_deg = round(v, 2)
        p = os.path.join(self.gdir, "記憶", "視角校準.json")
        try:
            cal = json.load(open(p, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            cal = {}
        measured = (cal.get("水平") or {}).get("量測每度dx")        # 10/16 13:1x：手動定的不蓋掉量刻度量到的值（1.8 倍規則拿它比）
        cal["水平"] = {"一圈dx": int(round(v * 360)), "每度dx": self.dx_deg, "方法": "他自己驗一圈後定的", "時間": datetime.now().strftime("%m/%d %H:%M")}
        if measured:
            cal["水平"]["量測每度dx"] = measured
        with open(p, "w", encoding="utf-8") as f:
            json.dump(cal, f, ensure_ascii=False, indent=1)
        self.startup["每度dx"] = self.dx_deg
        self.startup.pop("驗證", None)                   # 10/25 審查：刻度改了，之前的驗證不算
        CUR_DX[0] = self.dx_deg
        return f"每度 dx 改成 {self.dx_deg}（一圈 {int(round(v * 360))}），已寫進校準檔；之後 轉(90) 會送 dx {90 * self.dx_deg:.0f}"

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
        if self.verify2:
            time.sleep(0.2)
            im2 = self.grab()
            size2 = f"{im2.width}×{im2.height}" if im2 is not None else "抓不到"
            size += "；" + self._v2("畫面尺寸", size, size2, size == size2 and im is not None)
        return (f"畫面 {size}（視窗 {bbox}）｜目前刻度：每度 dx {self.dx_deg}、每度 dy {self.dy_deg}"
                f"｜來源 記憶/視角校準.json｜轉 90 度＝dx {90 * self.dx_deg:.0f}、抬頭 30 度＝dy {30 * self.dy_deg:.0f}")

    def cal_scale(self):
        y0 = self.yaw_acc
        try:
            return self._cal_scale()
        finally:
            self.yaw_acc = y0

    def _cal_scale(self):
        """程式帶他量：水平一圈（轉到畫面回起點）→ 每度 dx；垂直掃到頂、掃到底 → 每度 dy。寫進 視角校準.json。"""
        if self.image_path or not self.live:
            return "離線／dry-run 不能量刻度"
        start = self.grab()
        if start is None:
            return "抓不到畫面"
        can, dchk = self._cam_can_turn()
        if not can:
            return f"量刻度：{self.CAM_LOCKED_MSG}（試拖 40 像素畫面變化 {dchk:.0f}）"
        loop1 = self._loop_once()
        loop_dx = loop1
        v2_note = ""
        if self.verify2:                          # 10/18 二次驗證：水平一圈再量一次
            loop2 = self._loop_once()
            ok = abs(loop1 - loop2) <= 0.10 * (loop1 + loop2) / 2
            v2_note += self._v2("量刻度 一圈dx", loop1, loop2, ok) + ("（相差 10% 內，用平均）" if ok else "（相差超過 10%，水平這次不採用）") + "；"
            loop_dx = int(round((loop1 + loop2) / 2)) if ok else None
        dx_deg = round(loop_dx / 360.0, 2) if loop_dx else self.dx_deg
        # 垂直：先往上掃到頂（畫面不再變），再往下掃到底，算全程
        # 04:09 量到全程 dy 1600（每度 8.99，前兩次 560／3.15）：掃到底時畫面還有別人在動、diff 降不到 1.5 → 掃滿 20 步。
        # 改成「連續兩步變化都很小」就算到底，而且全程最多 1000。
        # 10/23 11:2x 使用者：「他上下抬頭低頭量測時也不要超過」（±45 度）→ 不再掃到頂／底；
        #   在限制內往有空間的那邊轉 20 度（照目前刻度的 dy），量畫面上下位移、用針孔模型（垂直視角 70 度）換算實際轉了幾度，量完轉回來
        # 11:3x 使用者：「上下抬頭測量時 轉動幅度越小越好 必須改善」→ 從 5 度開始，量不出（位移 <2.5% 畫面高）才加到 8、12 度
        # 11:5x 使用者：「5度 只是如果是全新遊戲他怎麼知道5度是多少？所以有沒有辦法每次用少量移動（例如10像素 慢慢動直到停了
        #   再去回推移動了多少像素 先知道極限值在開始量（注意真正在量時 不要靠近極限值」
        #   → 每步 10 像素慢慢往上，看兩側遠景有沒有跟著上下移（不用「畫面有沒有變」：別人在動畫面永遠在變）；連三步沒移＝頂到了。
        #     再每步 10 像素往下數到底＝上下全程幾像素；從底往上拉一半＝水平（不靠累計）。
        #     度數：全程當 160 度（Roblox 鏡頭上下 ±80 度；10/25 稽核：原本 178 低估每度 dy 約 10%）換每度 dy；之後轉頭／量測都離兩端至少 15% 全程
        self_note = self.find_self() if self.live and not self.image_path else ""
        acc0 = self.pitch_acc
        (top_extra, steps0), full_dy, full2, steps2 = self._find_vlimit(-1), None, None, 0
        self._vl_track = []
        full_dy, steps1 = self._find_vlimit(+1)
        if full_dy and self.self_mask is not None:  # 從頂往下掃的每一步：離底＝全程−從頂往下走了多少
            self.self_table = [(max(0, full_dy - m), v) for m, v in self._vl_track if m <= full_dy]
        self._person_verdict(getattr(self, "_vl_diffs", []))
        self._vl_track = []
        dy_ok = full_dy is not None and full_dy >= 100
        # 12:0x 使用者：「量測到極限之後記得要先切回水平再量測」→ 一路記住鏡頭離底端幾像素（pos），最後一律拉到全程一半＝水平才做下一項
        #   （11:59 二次驗證從底往上 90 像素就誤判頂到，原本以為在頂、往下拉半程 → 其實拉到底，對著地板做後面的量測）
        pos = 0                                    # 剛往下找到底＝0
        est = full_dy or int(160 * self.dy_deg)     # 找不到全程時用舊刻度估
        if self.verify2 and dy_ok:                # 二次驗證：從底再慢慢往上數到頂，全程再量一次
            full2, steps2 = self._find_vlimit(-1)
            pos = self._vl_moved                   # 往上真的動了多少（誤判停下也照實算）
            dy_ok = full2 is not None and abs(full_dy - full2) <= 0.10 * (full_dy + full2) / 2
            v2_note += (self._v2("量刻度 上下全程（10 像素慢慢找極限）", full_dy, full2 if full2 is not None else "量不出", dy_ok, "像素")
                        + ("（相差 10% 內，用平均）" if dy_ok else "（相差超過 10%，垂直這次不採用）") + "；")
            if dy_ok:
                full_dy = est = int(round((full_dy + full2) / 2))
        if dy_ok:
            self._drag(0, pos - est // 2)                # 正＝往下：從離底 pos 拉到離底 est/2＝水平
            time.sleep(0.3)
            self._depth_log(種類="回水平", 離底像素=pos, 全程=est, 拉=pos - est // 2)
            self.pitch_acc = 0.0
        else:
            # 10/25 07:0x：找不到極限時的「頂到」多半是誤判（沒紋理、動 0 像素就停在離頂 40 像素）→ 原本當成在底、往上拉半程＝拉到天花板，
            #   之後水平儀還歸零、他接下來每次低頭都被 ±45 限制擋住。改成把掃過的像素原路拉回、水平儀維持量之前的值
            undo = self.VSTEP * (steps0 - steps1 + steps2)
            self._drag(0, undo)
            time.sleep(0.3)
            self._depth_log(種類="回水平", 離底像素=None, 全程=est, 拉=undo, 結果="垂直量不出、原路拉回")
            self.pitch_acc = acc0
        if dy_ok:
            self.vrange_px = full_dy
        v2_note += ((self_note + "；轉到底沒：看兩側遠景整片位移（遠景看不清才看自己那塊），自己的樣子每步記成對照表（" + (f"{len(self.self_table)} 步" if self.self_table else "沒記到") + "）；")
                    if self.self_mask is not None else (self_note + "；看不到自己，改看兩側遠景；" if self_note else ""))
        v2_note += (f"垂直：每步 10 像素慢慢找上下極限（往上先多走了 {top_extra} 像素才頂到），全程 {full_dy} 像素，回到中間＝水平；"
                    if full_dy else "垂直：慢慢找極限沒找到（兩側遠景看不出上下移；鏡頭已原路拉回量之前的位置、不保證水平），")
        # 12:3x 使用者「目前的更動繼續執行到完成」→ 待決 1 照建議 ①：第一人稱全程 240、第三人稱 520，「全程＝178 度」只對第三人稱成立
        #   → 兩種人稱分開存全程（離極限 15%、回水平都用像素，不靠度數）；每度 dy 只用第三人稱的全程換（第一人稱不改每度 dy）
        first = self.person_hint.startswith("第一人稱")
        mode = "第一人稱" if first else ("第三人稱" if self.person_hint.startswith("第三人稱") else "")
        dy_from_range = dy_ok and not first
        dy_deg = round(full_dy / 160.0, 2) if dy_from_range else self.dy_deg
        if dy_ok and first:
            v2_note += f"第一人稱全程 {full_dy} 像素（跟第三人稱不同，只拿來當極限與回水平，不換每度 dy）；"
        # 使用者規則（09/23 開工順序）：量歪 1.8 倍不採用——跟上一次的刻度差太多就留舊值、只回報
        note = v2_note
        old_dx, old_dy = self.dx_deg, self.dy_deg
        # 10/16 13:1x：他用 定刻度 手動改成 5.25（驗一圈 bug 誤導），之後 量刻度 量到 2.5 被 1.8 倍規則擋掉、沿用錯的 5.25
        # → 1.8 倍規則拿「上次 量刻度 量到的值」比；目前值是手動定的、又沒有量測紀錄，就不套規則（並說明）
        dx_rej, dy_rej = False, False
        try:
            _h = (json.load(open(os.path.join(self.gdir, "記憶", "視角校準.json"), encoding="utf-8")).get("水平") or {})
        except Exception:  # noqa: BLE001
            _h = {}
        # 10/18 使用者「都要」：兩個基準都比、都報——①上次 量刻度 實測值 ②目前用的值；任一個在 1.8 倍內就採用，兩個都超才擋
        try:
            _v = (json.load(open(os.path.join(self.gdir, "記憶", "視角校準.json"), encoding="utf-8")).get("垂直") or {})
        except Exception:  # noqa: BLE001
            _v = {}
        if loop_dx:
            r18 = self._rule18("水平每度 dx", dx_deg, _h.get("量測每度dx"), old_dx, "他自己" in str(_h.get("方法", "")))
            note += r18
            if r18.endswith("不採用；"):
                dx_deg = old_dx
                dx_rej = True
        if dy_from_range:
            r18 = self._rule18("垂直每度 dy", dy_deg, _v.get("量測每度dy"), old_dy, False)
            note += r18
            if r18.endswith("不採用；"):
                dy_deg = old_dy
                dy_rej = True
        self.dx_deg, self.dy_deg = dx_deg, dy_deg
        p = os.path.join(self.gdir, "記憶", "視角校準.json")
        try:
            cal = json.load(open(p, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            cal = {}
        now = datetime.now().strftime("%m/%d %H:%M")
        # 10/25 23:4x 第 25 輪：不採用的量到值不能寫進「量測每度」（那是 1.8 倍規則的基準）；不然下一次就拿壞值當基準採用
        cal["水平"] = {"一圈dx": (loop_dx if (loop_dx and not dx_rej) else _h.get("一圈dx")), "每度dx": dx_deg, "方法": "轉到畫面回起點（工具版 量刻度）", "時間": now,
                     "量測每度dx": round(loop_dx / 360.0, 2) if (loop_dx and not dx_rej) else _h.get("量測每度dx")}
        cal["垂直"] = {"全程dy": (full_dy if (dy_from_range and not dy_rej) else _v.get("全程dy")), "每度dy": dy_deg,
                     "方法": "每步 10 像素慢慢找上下極限（工具版 量刻度，10/23；每度 dy 只用第三人稱全程換）", "時間": now,
                     "量測每度dy": round(full_dy / 160.0, 2) if (dy_from_range and not dy_rej) else _v.get("量測每度dy"),
                     "全程_第一人稱": (full_dy if (dy_ok and mode == "第一人稱") else _v.get("全程_第一人稱")),
                     "全程_第三人稱": (full_dy if (dy_ok and mode == "第三人稱") else _v.get("全程_第三人稱"))}
        cal["視窗"] = {"寬": start.width, "高": start.height, "時間": now}
        with open(p, "w", encoding="utf-8") as f:
            json.dump(cal, f, ensure_ascii=False, indent=1)
        self._append("大任務.md", f"量刻度：一圈 dx {loop_dx}（每度 {dx_deg}）、全程 dy {full_dy}（每度 {dy_deg}）")
        self.startup["每度dx"], self.startup["每度dy"] = self.dx_deg, self.dy_deg   # 10/25 審查：單獨叫 量刻度 時面板的開工數值要跟著換、驗證要重做
        self.startup.pop("驗證", None)
        return (note + f"量好了：水平一圈 dx {loop_dx or '（不採用）'} → 每度 dx {dx_deg}；垂直 " + (f"量到每度 dy {dy_deg}" if dy_ok else f"這次沒量準、沿用每度 dy {dy_deg}") + "；"
                f"已寫進 視角校準.json，之後 視角左(90) 會送 dx {90 * dx_deg:.0f}。鏡頭已轉回起點"
                + ("、上下回到中間。" if dy_ok else "；上下：這次沒找到極限、**鏡頭已原路拉回量之前的位置、不保證水平**（看下一張畫面：滿是天花板／地板就用 仰 自己拉，或到有牆有地的地方 修水平）。")
                + "接著用 視角右(30) 之類試一次，看回傳的實測跟你預期一不一致")

    def _loop_once(self):
        """水平轉一圈：每步 dx 60，轉到跟起點最像的位置，回傳一圈 dx（鏡頭停在約起點）。"""
        start = self.grab()
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
        if total > loop_dx:                      # 轉過頭的部分轉回來，第二次量才從起點開始
            self._drag(loop_dx - total, 0)
            time.sleep(0.2)
        # 10/25 稽核：60 像素一步解析度太粗（每度 dx 只可能 2.33／2.5／2.67）→ 在 best 附近 ±40 以 10 像素細找最像起點的位置
        self._drag(-40, 0)
        time.sleep(0.2)
        pos = loop_dx - 40
        fine = (self.diff(start, self.grab()), pos)
        for _ in range(8):
            self._drag(10, 0)
            pos += 10
            time.sleep(0.15)
            dfine = self.diff(start, self.grab())
            if dfine < fine[0]:
                fine = (dfine, pos)
        if fine[1] != pos:
            self._drag(fine[1] - pos, 0)
            time.sleep(0.2)
        return fine[1]

    def _rule18(self, name, new, ref_measured, ref_current, manual):
        """1.8 倍規則兩個基準：比值都算給他看；任一個在 1.8 倍內就採用，兩個都超過才不採用（字串結尾「不採用；」給呼叫端判斷）。"""
        def within(r):
            return 1 / 1.8 <= r <= 1.8
        parts, oks = [], []
        if ref_measured:
            r = new / float(ref_measured)
            parts.append(f"跟上次量刻度實測 {ref_measured} 比 {r:.2f} 倍{'✓' if within(r) else '✗'}")
            oks.append(within(r))
        if ref_current:
            r = new / float(ref_current)
            parts.append(f"跟目前用的 {ref_current} 比 {r:.2f} 倍{'✓' if within(r) else '✗'}")
            oks.append(within(r))
        if not oks:
            return f"{name} 量到 {new}，沒有基準可比，直接採用；"
        head = f"1.8 倍規則（{name} 量到 {new}）：" + "、".join(parts)
        if manual and not ref_measured:
            return head + "；目前值是你用 定刻度 手動定的、沒有量刻度紀錄，直接採用；"
        return head + ("；有一個在 1.8 倍內 → 採用；" if any(oks) else f"；兩個都超過 1.8 倍 → 沿用 {ref_current}、不採用；")

    # 2026-10-15 10:5x 使用者：「脫困不要，改成轉 180 單位（按照他計算結果直接輸入）……需要更多簡潔的方式」
    # → 轉(度) 正右負左、仰(度) 正上負下，他算好直接填；舊名字（視角左／右／上／下、180度轉頭）留作隱藏別名
    def look_turn(self, 度=""):
        import math
        d = num(度, None)
        if d is None:
            return "轉 要給度數：正＝右、負＝左，例如 轉(90)、轉(-30)、轉(180)"
        d0 = d
        d = max(-90, min(90, d))                       # 10/25 使用者：一次最多 90 度，確保前後畫面還有同一個東西
        cap_note = f"（左右一次最多 90 度（使用者限制）：你要 {d0:+.0f}，只轉 {d:+.0f}；剩下的下一輪再轉）" if abs(d - d0) >= 1 else ""
        if abs(d) < 3:
            return "度數太小（<3 度）沒意義"
        # 11:0x 使用者：「轉幾度要按照他輸入的計算才對」→ 他填幾度就送 幾度×每度dx，不分段、不改值
        # （底層 _drag 自己會把超過 220 像素的拖曳拆成小段，但總量照他的數字）
        if abs(d) > 30 and self.live and not self.image_path:
            # 10/25 工具迭代：超過 30 度就分段轉、每段實測、合計（審查：45 度一段只剩 55% 重疊、整張位移不穩 → 改 30）；
            # 使用者：「確保至少還有一個東西維持相同的畫面」→ 哪一段量不出就停在那裡、照實說（那段已送出，照送出的算）
            meas, parts, done, stopped, lm_used = self._turn_chunks(d)
            ok = abs(meas - d) <= max(5.0, 0.15 * abs(d))        # 10/26 03:5x 第 34 輪：補轉後送出總量 done 比要的多，要跟「要的 d」比（03:11 轉(90) 實測 88 vs 送出 106 被判不一致）
            stop_note = f"；**中途量不出，停在 {done:+.0f} 度沒再轉（要轉 {d:+.0f}，剩下的下一輪再轉）**" if stopped else ""
            n_seg = max(1, int(math.ceil(abs(d) / 30.0)))
            segs_ok = bool(getattr(self, "_seg_ok", [])) and all(self._seg_ok)
            if getattr(self, "_rep_flag", False) and not ((lm_used >= n_seg - 1 or (lm_used >= 1 and segs_ok)) and ok):
                v2 = f"（{self.REP_NOTE}）"
            elif getattr(self, "_chunk_unsure", False) and not ok:
                v2 = f"（有一段整張位移量到不合理的值、照送出算，合計 {meas:+.0f} 僅參考、不判一致）"
            elif getattr(self, "_chunk_unsure", False):
                v2 = f"（有一段量不出、是估的，合計 {meas:+.0f} 僅參考、不判一致；看下一張畫面確認）"   # 第 49 輪：18:50 補轉量不出照送出算 → 判一致、說已對準，其實沒轉到
            else:
                v2 = (self._cmp("轉頭合計", f"{d:+.0f}", f"{meas:+.0f}", ok, "度") + ("（特徵點實測合計）" if "特徵點" in parts else "（標點實測合計）")
                      + ("（畫面有重複花紋，但標點段都跟送出一致）" if getattr(self, "_rep_flag", False) else "") if lm_used
                      else f"（沒有找到標點，{meas:+.0f} 度是整張位移估的，僅參考、不算一致／不一致）")
            return (f"轉 {d:+.0f} 度：分段轉（每段 ≤30 度、每段實測）{parts}；**你實際轉了約 {meas:+.0f} 度（送出 {done:+.0f}）**{stop_note}；" + v2 + cap_note)
        return self._look(abs(d), "x", 1 if d > 0 else -1, 3, 90, 30) + cap_note

    def _turn_chunks(self, deg):
        """把 deg 度拆成 ≤30 度的段，一段一段拖、每段用標點（優先）／整張位移量實際轉了幾度
        → (合計實測度數, 每段說明, 實際送出的度數, 有沒有中途停, 用到標點的段數)。正＝右。
        哪一段前後畫面找不到同一個東西就停、不再往下轉（使用者：確保至少還有一個東西維持相同的畫面）；
        那一段鏡頭已經轉出去了，合計照送出的度數算進去（10/25 審查：原本沒算進去，他會以為少轉、再轉一次就過頭）。"""
        import math
        n = max(1, int(math.ceil(abs(deg) / 30.0)))
        chunk = deg / n
        meas, parts, done, stopped, lm_used = 0.0, [], 0.0, False, 0
        self._rep_flag = False
        self._chunk_unsure = False
        self._seg_ok = []
        comp = self._comp_deg() if (self.live and not self.image_path) else 0.0     # 第 50 輪
        sgn = 1 if deg > 0 else -1
        if comp:
            parts.append(self._comp_note(comp))
        # 10/26 20:2x 第 53 輪：18:40 起每段只轉到送出的 0～70%、而且時好時壞（20:18 兩段 107%、20:17 44%），跨呼叫的中位補償（第 50 輪）抓不到
        #   → 同一次呼叫內閉環：前面量到的段合計只轉到 20～75%，後面的段和補轉就按 1/比例多送（最多 2 倍），量到的照實說
        c_sent, c_got = 0.0, 0.0

        def _k():
            if c_sent <= 0:
                return 1.0, None
            rc = c_got / c_sent
            return (min(2.0, 1.0 / rc), rc) if 0.2 <= rc < 0.75 else (1.0, rc)

        for i in range(n):
            before = self.grab()
            if before is None:
                parts.append(f"第{i + 1}段 抓不到畫面，沒轉")
                stopped = True
                break
            if self._repetitive(before)[0]:
                self._rep_flag = True
            k, rc = _k()
            send_deg = (chunk + comp * sgn) * k
            self._drag(send_deg * self.dx_deg, 0)
            after = self._settled()
            dd = self.diff(before, after)
            if dd < 3:                                           # 中央有提示擋住拖不動 → 從上方重拖
                self._drag(send_deg * self.dx_deg, 0, fy=0.25)
                after = self._settled()
                dd = self.diff(before, after)
            k_txt = f"（前面只轉到送出的 {rc * 100:.0f}%，這段多送 {(k - 1) * 100:.0f}%：送 {send_deg:+.0f} 度）" if k > 1.0 else ""
            sh, _ = self._shift(before, after, dd)
            done += chunk
            lm_deg, lm_txt = self._landmark_turn(before, after, 1 if chunk > 0 else -1, chunk)
            if getattr(self, "_lm_ambiguous", False):
                self._rep_flag = True
            scene_deg = self._scene_deg(before, after)[0] if (sh is None or abs(sh) < 0.45) else None   # 10/25 稽核：左右兩帶換算
            if lm_deg is not None and scene_deg is not None and abs(lm_deg - scene_deg) > max(10.0, 0.5 * abs(scene_deg)):
                if abs(lm_deg - chunk) <= max(5.0, 0.3 * abs(chunk)):     # 10/25 03:2x：跟送出的接近就信標點（人多時整張位移不可靠）
                    lm_txt += f"（整張位移 {scene_deg:+.0f} 跟標點差很多，但標點跟送出的 {chunk:+.0f} 接近、信標點）"
                else:
                    lm_txt += f"（跟整張位移 {scene_deg:+.0f} 差太多、跟送出的也差太多，標點可能認錯，改用整張位移）"   # 10/25 02:1x
                    lm_deg = None
            if lm_deg is not None and scene_deg is None and abs(lm_deg - chunk) > max(15.0, abs(chunk)):
                lm_txt += f"（整張量不出、標點 {lm_deg:+.0f} 又跟送出的 {chunk:+.0f} 差太多，不信）"
                lm_deg = None
            f_yaw, _fp, _fn, _fc, f_txt = self._feat_deg(before, after, want=chunk)
            feat_here = f_yaw is not None
            if not feat_here and getattr(self, "_feat_bad", ""):
                lm_txt += f"（{self._feat_bad}）"
            if not feat_here and lm_deg is not None and abs(lm_deg) >= 5 and (lm_deg > 0) != (chunk > 0):
                # 10/26 05:3x 第 36 輪：05:27 轉(90) 第 2 段送 +30、標點 −26、整張 −46（紅牆沒紋理、特徵點也量不出）→ 合計 +29「不一致」。
                #   拖 30 度不可能反方向；特徵點量不出時標點／整張反方向就不信、這段照送出算（僅參考）
                lm_txt += f"（標點 {lm_deg:+.0f} 跟送出的 {chunk:+.0f} 反方向、特徵點又量不出＝畫面沒紋理認錯了，不信）"
                lm_deg = None
                scene_deg = None
                self._chunk_unsure = True
                meas += chunk
                parts.append(f"第{i + 1}段 {chunk:+.0f}→量到反方向不信、照送出算（僅參考；{lm_txt[-60:]}）")
                self._verify_dump("轉段", before, after, 送出度=chunk, 標點度=None, 整張度=None, 用的=None, 畫面變化=dd, 特徵度=None, 反方向不信=True)
                continue
            if feat_here:                                        # 10/26 第 28 輪：特徵點優先（見 _feat_deg）
                lm_deg, lm_txt = f_yaw, f_txt
                if getattr(self, "_lm_ambiguous", False):
                    self._rep_flag = False
            if lm_deg is None and scene_deg is not None and abs(scene_deg - chunk) > max(10.0, 0.5 * abs(chunk)):
                # 10/25 05:5x：大廳人多／地磚時整張位移仍會給 -63（要 -30）、+9（要 -30）這種值，加進合計就整個錯 → 這段照送出算、標僅參考
                self._chunk_unsure = True
                meas += chunk
                parts.append(f"第{i + 1}段 {chunk:+.0f}→整張位移量到 {scene_deg:+.0f}（跟送出差太多、不合理，這段照送出的 {chunk:+.0f} 算、僅參考）")
                continue
            if lm_deg is None and scene_deg is None:
                est, est_txt = self._unmeasured_est(chunk)
                meas += est
                self._verify_dump("轉段", before, after, 送出度=chunk, 標點度=None, 整張度=None, 用的=None, 畫面變化=dd, 量不出=True, 特徵度=None)
                parts.append(f"第{i + 1}段 已送出 {chunk:+.0f} 但量不出（前後畫面找不到同一個東西；{lm_txt}）→ "
                             + (est_txt if est_txt else "這段照送出的算") + "、停在這裡")
                stopped = True
                break
            turned = lm_deg if lm_deg is not None else scene_deg   # 特徵點＞標點＞整張位移（左右兩帶換算）
            self._verify_dump("轉段", before, after, 送出度=chunk, 標點度=(None if feat_here else lm_deg), 整張度=scene_deg, 用的=turned, 畫面變化=dd, 特徵度=f_yaw)
            self._seg_ok.append(abs(turned - chunk) <= max(5.0, 0.15 * abs(chunk)))
            lm_used += lm_deg is not None
            meas += turned
            if feat_here or lm_deg is not None:
                self._turn_record(send_deg, turned)
                if (turned > 0) == (send_deg > 0):
                    c_sent += abs(send_deg)
                    c_got += abs(turned)
            parts.append(f"第{i + 1}段 {chunk:+.0f}→實測 {turned:+.0f}（{'特徵點' if feat_here else ('標點' if lm_deg is not None else '整張位移，僅參考')}）" + k_txt
                         + (self._under_turn_note(send_deg, turned) if (feat_here or lm_deg is not None) else ""))
        # 10/26 01:5x 第 30 輪：每段拖曳固定少 2～3 度（30→27／28、17→13）、合計常差到不一致（01:39 要 34 量到 27），他下一輪得再轉一次。
        #   → 合計跟要的差超過容許（5 度或 15%）就補轉一次差額、照樣實測加進合計；只補一次、補完照量到的說
        if not stopped and lm_used >= 1 and self.live and not self.image_path:
            for rep_i in range(2):                      # 第 53 輪：補轉最多兩次（50% 時補一次還差一半）
                gap = deg - meas
                if not (max(5.0, 0.15 * abs(deg)) < abs(gap) <= 30):
                    break
                before = self.grab()
                gsgn = 1 if gap > 0 else -1
                k, rc = _k()
                send_deg = (gap + comp * gsgn) * k
                self._drag(send_deg * self.dx_deg, 0)
                after = self._settled()
                dd = self.diff(before, after)
                f_yaw, _fp, _fn, _fc, f_txt = self._feat_deg(before, after, want=gap)
                turned = f_yaw
                how = "特徵點"
                if turned is None:
                    turned, _lt = self._landmark_turn(before, after, 1 if gap > 0 else -1, gap)
                    how = "標點"
                done += gap
                label = "補轉" if rep_i == 0 else "再補轉"
                k_txt = f"（只轉到 {rc * 100:.0f}%、多送 {(k - 1) * 100:.0f}%：送 {send_deg:+.0f}）" if k > 1.0 else ""
                if turned is None:
                    est, est_txt = self._unmeasured_est(gap)
                    meas += est
                    self._chunk_unsure = True
                    parts.append(f"{label} {gap:+.0f}→量不出、" + (est_txt if est_txt else "照送出算（僅參考）"))
                    self._verify_dump("轉段", before, after, 送出度=gap, 標點度=None, 整張度=None, 用的=None, 畫面變化=dd, 特徵度=None, 補轉=True)
                    break
                meas += turned
                lm_used += 1
                self._turn_record(send_deg, turned)
                if (turned > 0) == (send_deg > 0):
                    c_sent += abs(send_deg)
                    c_got += abs(turned)
                self._seg_ok.append(abs(turned - gap) <= max(5.0, 0.15 * abs(gap)))
                parts.append(f"{label} {gap:+.0f}→實測 {turned:+.0f}（{how}）" + k_txt + self._under_turn_note(send_deg, turned))
                self._verify_dump("轉段", before, after, 送出度=gap, 標點度=(turned if how == "標點" else None), 整張度=None, 用的=turned, 畫面變化=dd, 特徵度=f_yaw, 補轉=True)
        return meas, "、".join(parts), done, stopped, lm_used

    # 10/23 11:1x 使用者：「新增限制（抬頭低頭只能上下45度 共90度的範圍）」→ 照水平儀累計，超過的部分不轉
    PITCH_LIM = 45.0

    vrange_px = None      # 量刻度 找到的上下全程（像素）

    def _pitch_lim(self):
        """上下能轉到幾度：使用者的 ±45，和「離極限至少 15% 全程」取小的"""
        lim = self.PITCH_LIM
        if self.vrange_px and self.dy_deg:
            lim = min(lim, (self.vrange_px * 0.5 - self.vrange_px * 0.15) / self.dy_deg)
        return lim

    LEVEL_HINT = "（水平儀是程式累計、常跟真的差：量刻度／修水平 量不出時不會歸零、轉到極限也照算；畫面其實是水平的就先 水平歸零 再轉）"

    def _pitch_room(self, d):
        """想轉 d 度（正＝抬頭）→ 限制內實際能轉幾度"""
        L = self._pitch_lim()
        return max(-L, min(L, self.pitch_acc + d)) - self.pitch_acc

    def look_pitch(self, 度=""):
        d = num(度, None)
        if d is None:
            return "仰 要給度數：正＝抬頭、負＝低頭，例如 仰(-20)"
        want = max(-60, min(60, d))
        d = self._pitch_room(want)
        lim_note = ""
        if abs(d - want) >= 1:
            lim_note = (f"｜上下只能 ±{self.PITCH_LIM:.0f} 度（使用者限制）：你要{'抬' if want > 0 else '低'} {abs(want):.0f} 度，"
                        f"現在已經{'抬頭' if self.pitch_acc > 0 else '低頭'} {abs(self.pitch_acc):.0f} 度，只轉了 {abs(d):.0f} 度" + self.LEVEL_HINT)
        if abs(d) < 3:
            return (f"上下只能 ±{self.PITCH_LIM:.0f} 度（使用者限制），現在已經{'抬頭' if self.pitch_acc > 0 else '低頭'} {abs(self.pitch_acc):.0f} 度，"
                    f"再往{'上' if want > 0 else '下'}就超過了，這次沒轉" + self.LEVEL_HINT if abs(want) >= 3 else "度數太小（<3 度）沒意義")
        r = self._look(abs(d), "y", -1 if d > 0 else 1, 3, 60, 15) + lim_note
        self.pitch_acc = max(-89.0, min(89.0, self.pitch_acc + d))
        lv = ("水平" if abs(self.pitch_acc) < 3 else f"{'抬頭' if self.pitch_acc > 0 else '低頭'} {abs(self.pitch_acc):.0f} 度")
        return r + f"｜水平儀（程式累計你送的 仰）：現在約 {lv}；歪了用 修水平 一次拉回"

    VSTEP = 10            # 11:5x 使用者：每次 10 像素慢慢動
    _vl_moved = 0
    VSTILL = 0.006        # 兩側遠景上下位移 < 0.6% 畫面高＝這一步沒動

    # 12:1x 使用者：「有背景在動是因為 有其他玩家可能在畫面中移 所以必須要先找出你的角色長什麼樣子 以及只看鏡頭相對角色的角度」
    #   ①找自己：鏡頭左右各轉一點點（每步 10 像素），背景整片滑動、自己的角色留在中央 → 中央「轉頭時幾乎不變」的像素＝自己（遮罩＋框），
    #     不轉頭拍兩張量待機動作雜訊當門檻；存 長相/自己
    #   ②找極限時只看自己那塊：鏡頭還在轉＝自己看起來的角度在變；頂到了＝不再變（別的玩家怎麼走都沒關係）
    #   ③每一步記「離底幾像素 ↔ 自己的樣子」，修水平 可以拿現在的樣子對照，知道鏡頭相對角色的角度
    self_mask = None            # 240×124 的 bool 遮罩
    self_box = None             # (x0,y0,x1,y1) 0～1
    self_noise = 0.0
    self_table = []             # [(離底像素, 自己那塊的灰階向量)]
    _vl_track = []
    SG = (240, 124)

    def _gray_small(self, im):
        import numpy as np
        return np.asarray(im.convert("L").resize(self.SG), dtype=np.float32)

    def find_self(self):
        import numpy as np
        import cv2
        if not self.live or self.image_path:
            return "離線／dry-run 不能找自己"
        W, H = self.SG
        f0 = self.grab()
        time.sleep(0.3)
        f0b = self.grab()
        g0, g0b = self._gray_small(f0), self._gray_small(f0b)
        noise_map = np.abs(g0 - g0b)
        changes, preds = [], []
        for sign in (1, -1):                        # 右轉 2 步再轉回、左轉 2 步再轉回（每步 10 像素，總共約 ±8 度）
            for _ in range(2):
                self._drag(sign * self.VSTEP, 0)
                time.sleep(0.12)
                gi = self._gray_small(self.grab())
                (sx, sy), _r = cv2.phaseCorrelate(g0, gi)           # 背景整片移了多少
                M = np.float32([[1, 0, sx], [0, 1, sy]])
                pred = cv2.warpAffine(g0, M, (W, H), borderMode=cv2.BORDER_REPLICATE)
                changes.append(np.abs(gi - g0))
                preds.append(np.abs(gi - pred))                     # 實際跟「當它是背景」的預測差多少（背景＝小；跟著鏡頭的＝大）
            self._drag(-sign * 2 * self.VSTEP, 0)
            time.sleep(0.15)
        ch = np.mean(changes, axis=0)
        pr = np.mean(preds, axis=0)
        bg = float(np.median(ch))                   # 背景平均變多少
        if bg < 4:
            self._self_status = "看不出"
            return f"轉頭時畫面幾乎沒變（背景變化 {bg:.1f}），找不到自己——可能被擋住或游標鎖住"
        # 12:3x：「沒變」不夠——純色背景轉頭也不會變。要「當它是背景的預測跟實際差很多（不是跟背景走）、實際卻幾乎沒變」才算跟著鏡頭
        cand = ((pr > 10) & (ch < pr * 0.35)).astype(np.uint8)
        core = cand.copy()                           # 真的「留在原位」的核心像素（判斷是角色還是 UI 用，不含補起來的）
        cand = cv2.morphologyEx(cand, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))      # 角色身體純色：邊緣算到了，中間補起來
        cand[:, : int(W * 0.3)] = 0                  # 只看中央：兩邊、最上面是 HUD／天空
        cand[:, int(W * 0.7):] = 0
        cand[: int(H * 0.2), :] = 0
        # 12:2x 模擬：角色身體多半是純色（加紋理條件會只剩輪廓、改挑到地板）→ 不看紋理；
        wide = cv2.morphologyEx(cand, cv2.MORPH_OPEN, np.ones((1, int(W * 0.25)), np.uint8))   # 連續超過 1/4 畫面寬的橫條＝地板／墊子
        cand[wide > 0] = 0
        cand[:, : int(W * 0.3)] = 0                  # 只看中央：兩邊、最上面是 HUD／天空
        cand[:, int(W * 0.7):] = 0
        cand[: int(H * 0.2), :] = 0
        cand = cv2.morphologyEx(cand, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        n, lab, stats, cent = cv2.connectedComponentsWithStats(cand)
        best, bscore = None, 0
        for i in range(1, n):
            x, y, w, h, area = stats[i]
            cx, cy = cent[i]
            if not (x <= W * 0.55 and x + w >= W * 0.45):      # 第三人稱角色在畫面正中間那一欄
                continue
            score = area / (1 + abs(cx - W / 2) / W * 4)
            if area >= 0.004 * W * H and score > bscore:
                best, bscore = i, score
        if best is None:
            self.self_mask = None
            self._self_status = "沒有"
            return f"中央沒有「轉頭時留在原位」的一塊（背景變化 {bg:.1f}）＝看不到自己（可能第一人稱）"
        m = lab == best
        self.self_area = float(stats[best][4]) / (W * H)
        self.self_core = m & (core > 0)
        self._self_status = "有"
        x, y, w, h, area = stats[best]
        self.self_mask, self.self_box = m, (x / W, y / H, (x + w) / W, (y + h) / H)
        self.self_noise = float(noise_map[m].mean())
        try:
            from PIL import ImageDraw
            d = os.path.join(self.mdir, "長相", "自己")
            os.makedirs(d, exist_ok=True)
            big = f0.convert("RGB").resize((1024, int(1024 * f0.height / f0.width)))
            bx = [int(v * s) for v, s in zip(self.self_box, (big.width, big.height, big.width, big.height))]
            ts = datetime.now().strftime("%m%d_%H%M%S")
            big.crop(bx).save(os.path.join(d, ts + "_近.png"))
            mk = big.copy()
            ImageDraw.Draw(mk).rectangle(bx, outline=(255, 0, 0), width=4)
            mk.save(os.path.join(d, ts + "_框.png"))
        except Exception:  # noqa: BLE001
            pass
        self._depth_log(種類="找自己", 背景變化=round(bg, 1), 自己框=[round(v, 2) for v in self.self_box],
                        面積比=round(area / (W * H), 3), 待機雜訊=round(self.self_noise, 1))
        return (f"找到自己：畫面 ({self.self_box[0]:.2f},{self.self_box[1]:.2f})～({self.self_box[2]:.2f},{self.self_box[3]:.2f})"
                f"（轉頭時背景變 {bg:.0f}、這一塊幾乎不變；待機動作雜訊 {self.self_noise:.1f}），存成長相「自己」")

    # 12:2x 使用者：「第三人稱時視角無論怎麼移動你的玩家都會在正中心 這個也要作為判斷的依據 因為第一人稱時你視角左右移動是不可能有一個東西持續停留在畫面中間除了UI」
    #   → 轉頭時中央有一塊一直留著 ＋ 上下看時它的樣子會變（看的角度變了）＝自己的角色＝第三人稱；
    #     留著但上下看也完全不變＝UI（準心、面板）＝第一人稱；中央沒有留著的＝第一人稱。只給證據，人稱還是他自己回報
    person_hint = ""
    self_core = None
    self_area = 0.0
    SELF_MIN_AREA = 0.012       # 第三人稱角色在大廳截圖約佔 4～5%；準心之類的 UI 通常只有一小點
    _core_diffs = []
    _self_status = ""
    _vl_diffs = []

    def _person_verdict(self, self_diffs):
        if self._self_status in ("", "看不出"):
            self.person_hint = "看不出（轉頭時畫面幾乎沒變，可能被擋住或游標鎖住）" if self._self_status else ""
        elif self.self_mask is None:
            self.person_hint = "第一人稱（轉頭時中央沒有一塊一直留著——第一人稱轉頭除了 UI 不會有東西停在中間）"
        elif self.self_area < self.SELF_MIN_AREA:
            # 12:3x：用「上下看會不會變」分 UI／角色，模擬 12 次只對 7 次（角色上下看時在畫面上幾乎不動）→ 改看大小
            self.person_hint = (f"第一人稱（中央只有一小塊一直留著（佔畫面 {self.self_area * 100:.1f}%），像準心之類的 UI，不像角色）")
            self.self_mask, self.self_table = None, []
        else:
            self.person_hint = (f"第三人稱（轉頭時中央有一塊一直在正中間、佔畫面 {self.self_area * 100:.1f}%＝自己的角色）")
        self._depth_log(種類="人稱證據", 結論=self.person_hint, 面積比=round(self.self_area, 3),
                        核心上下看變化=(self._core_diffs[:60] if self._core_diffs else None))
        return self.person_hint

    def _self_vec(self, im):
        return self._gray_small(im)[self.self_mask] if self.self_mask is not None else None

    @staticmethod
    def _self_diff(a, b):
        """自己那塊的變化：去掉變最多的 20%（別的玩家剛好走過去擋到一部分）再平均"""
        import numpy as np
        d = np.sort(np.abs(a - b))
        return float(d[: max(1, int(len(d) * 0.8))].mean())

    def _find_vlimit(self, sign, max_px=2000):
        """每步 VSTEP 像素往上（sign -1）／往下（+1）慢慢拖，直到連三步兩側遠景都沒上下移＝頂到 → (真的有動的像素, 步數)。"""
        import numpy as np
        prev = self.grab()
        moved, still, steps, blind = 0, 0, 0, 0
        use_self = self.self_mask is not None
        thr = max(2.5, self.self_noise * 2 + 1.0)
        diffs, cdiffs = [], []
        while moved + still * self.VSTEP < max_px:
            self._drag(0, sign * self.VSTEP)
            time.sleep(0.12)
            cur = self.grab()
            steps += 1
            pcs = self.vshift_pc(prev, cur)          # 主判斷：兩側遠景整片有沒有上下移（相位相關量整片位移，別的玩家只占一小塊）
            weak = any(r < 0.15 for _, r in pcs)
            is_still = not weak and all(abs(d) < self.VSTILL for d, _ in pcs)
            if use_self:                            # 12:1x：自己那塊——每一步記樣子（對照表）；遠景看不清時改用它判斷
                if sign > 0:
                    self._vl_track.append((moved + self.VSTEP * (still + 1), self._self_vec(cur)))
                dv = self._self_diff(self._self_vec(cur), self._self_vec(prev))
                diffs.append(round(dv, 1))
                if self.self_core is not None and self.self_core.any():
                    gc, gp = self._gray_small(cur), self._gray_small(prev)
                    cdiffs.append(round(float(np.abs(gc[self.self_core] - gp[self.self_core]).mean()), 1))
                if weak:
                    is_still, weak = dv < thr, False
            if is_still:
                still += 1
                if still >= 4:
                    break
            else:
                blind += int(weak)
                moved += self.VSTEP * (still + 1)       # 中間短暫沒量到也算有動
                still = 0
            prev = cur
        self._depth_log(種類="找上下極限", 方向=("上" if sign < 0 else "下"), 每步像素=self.VSTEP, 步數=steps, 有動像素=moved,
                        頂到=(still >= 4), 紋理太弱步數=blind, 看=("兩側遠景＋自己" if use_self else "兩側遠景"),
                        門檻=(round(thr, 1) if use_self else None), 自己變化=(diffs[:80] if use_self else None))
        self._vl_moved = moved                        # 沒頂到也要知道真的動了多少（回水平用）
        self._vl_diffs = diffs if (use_self and sign > 0) else getattr(self, "_vl_diffs", [])
        if use_self and sign > 0:
            self._core_diffs = cdiffs
        return (moved if still >= 4 else None), steps

    def _sweep_pitch(self, sign, max_steps, step=80):
        """上下掃到頂／底（連續兩步畫面幾乎不變＝頂到了），回傳掃了多少 dy。sign -1＝往上、+1＝往下。"""
        moved, small = 0, 0
        for _ in range(max_steps):
            b = self.grab()
            self._drag(0, sign * step)
            time.sleep(0.15)
            dd = self.diff(b, self.grab())
            if dd < 3.0:
                small += 1
                if small >= 2:
                    break
            else:
                small = 0
                moved += step
        return moved

    def look_level(self):
        """修水平：10/23 使用者限制上下 ±45 度（量測也不能超過）→ 不再往下掃到底再拉 89 度；
        照水平儀（程式累計你送的上下度數）反向拉回 0，拉之前、之後各拍一張量上下位移當驗證。"""
        if self.image_path or not self.live:
            return f"[dry-run] 會照水平儀反向拉 {-self.pitch_acc:+.0f} 度回水平"
        was = self.pitch_acc
        # 17:4x 實測：水平儀（累計）會跟真的鏡頭差很多——開著 Roblox 選單時拖了不會轉但照樣累計、轉到極限停了也照樣累計
        #   → 他看著天花板、水平儀卻說低頭 30～45 度，修水平 照錯的累計拉、越修越歪。
        #   改用使用者教的方法：每步 10 像素慢慢往下拖到停（底端極限），再往上拉這個人稱全程的一半＝真的水平，不靠累計
        # 18:1x 實測：他在醫院裡（第一人稱，全程 240）卻回報第三人稱 → 用了第三人稱全程 520 的一半 260 往上拉 → 拉過頭看天花板。
        #   → 全程不靠回報、當場量：慢慢往下到底 → 慢慢往上數到頂＝這次的全程 → 往下拉一半
        if True:
            bottom, _ = self._find_vlimit(+1)
            down_px = self._vl_moved
            top, steps_up = self._find_vlimit(-1)
            if down_px == 0 and self._vl_moved == 0:
                # 10/25 稽核：往下、往上都量到 0 像素（不可能同時在頂又在底）→ 原本照樣用記的全程盲拉 240 像素再把水平儀歸零
                self._depth_log(種類="修水平 找底再找頂", 往下像素=0, 往上全程=0, 用的全程=None, 水平儀原本=round(was, 1), 結果="兩方向都 0、沒拉")
                return ("修水平：每步 10 像素慢慢往下、往上都量不到鏡頭有在動（0 像素）＝畫面沒紋理（整片天花板／地板／牆）或鏡頭被鎖，"
                        "**沒有拉、水平儀沒歸零**（原本累計 " + f"{was:+.0f} 度）；先 仰(-30) 或 轉(30) 看畫面會不會變，到看得到牆和地的地方再 修水平")
            full = top if top else (self.vrange_px or int(160 * self.dy_deg))
            ref = self.vrange_px or int(160 * self.dy_deg)
            if top and ref and top < 0.6 * ref:
                # 10/26 15:2x 第 46 輪：15:15 往上 180 像素就判「頂到」（天花板沒紋理），照 180 的一半拉 → 還是看天花板，他再 仰(-15) 自己拉；
                #   15:07 同房間量到 470、記的 520 → 量到的全程比記的小太多就不信它，改用記的全程從底算：往上補到 ref/2
                delta = ref // 2 - top
                self._drag(0, -delta)
                time.sleep(0.3)
                self.pitch_acc = 0.0
                lv = (f"**不一定水平**：往上只走 {top} 像素就看不出鏡頭在動（天花板／牆沒紋理、太早當成頂到），跟記的全程 {ref} 差太多，不信它；"
                      f"改照記的全程從底算、往{'上' if delta > 0 else '下'}補 {abs(delta)} 像素。看下一張畫面：滿是天花板就 仰(-15)、滿是地板就 仰(15)")
                self.level_meas = (datetime.now().strftime("%H:%M"), lv)
                self._depth_log(種類="修水平 找底再找頂", 往下像素=down_px, 往上全程=top, 用的全程=ref, 水平儀原本=round(was, 1), 結果="量到的全程太小、改用記的")
                return (f"修水平：每步 10 像素慢慢往下{'到底' if bottom else '（沒找到底）'}（往下動了 {down_px} 像素）→ 再慢慢往上 → {lv}；"
                        f"水平儀原本以為{'抬頭' if was > 0 else '低頭'} {abs(was):.0f} 度，已歸零")
            if top:
                self._drag(0, full // 2)                       # 在頂：往下拉一半
            else:
                # 10/25 14:2x 第 21 輪：往上找不到頂（地板沒紋理、4 步就誤判頂到）時鏡頭其實還在底附近，原本照樣「往下拉一半」→ 壓在地板上，
                #   他 仰(30)→修水平 又被拉回地板，循環 4 次。改：從底往上拉到一半（扣掉往上已送出的像素）
                up_sent = steps_up * self.VSTEP
                self._drag(0, -max(0, full // 2 - up_sent))
            time.sleep(0.3)
            if TOOLFIX and not top:
                # 10/24 20:2x 工具修正：整片重複花紋（地磚、地毯）時量不出鏡頭在動 → 原本照樣寫「已歸零」，他以為修好了
                self.pitch_acc = 0.0
                lv = (f"**沒修好**：往上找不到頂（畫面是重複花紋或被擋，量不出鏡頭有沒有在動），照記的全程 {full} 從底往上拉到一半（往上已送 {steps_up * self.VSTEP} 像素），"
                      "**現在不一定是水平**——看下一張畫面：看得到地平線／牆和天花板各一半才算水平；還是滿地板就用 仰(30) 自己抬")
            else:
                self.pitch_acc = 0.0
                lv = (f"回到水平（當場量到全程 {full} 像素，從頂端往下拉一半）" if top else
                      f"往上沒找到頂（畫面看不出停），照記的全程 {full} 從底往上拉到一半，可能不準")
            self.level_meas = (datetime.now().strftime("%H:%M"), lv)
            self._depth_log(種類="修水平 找底再找頂", 往下像素=down_px, 往上全程=top, 用的全程=full, 水平儀原本=round(was, 1))
            return (f"修水平：每步 10 像素慢慢往下{'到底' if bottom else '（沒找到底，量不出）'}（往下動了 {down_px} 像素）→ 再慢慢往上數到頂 → 往下拉一半＝{lv}。"
                    f"水平儀原本以為{'抬頭' if was > 0 else '低頭'} {abs(was):.0f} 度（累計會跟真的差：開選單時拖、轉到極限都還在算），已歸零")
        # 10/25 審查：原本下面還有「看自己對照表」「照水平儀拉回」兩段，上面的 if True 已經 return，永遠到不了 → 刪掉

    V_FOV = 70.0          # Roblox 預設垂直視角；10/18 刻度樣本：轉 10／20／30 度實測位移 6／12／19%，照這個針孔模型算 6.5／13／21%

    def _angles_to(self, fx, fy, W, H):
        import math
        tv = math.tan(math.radians(self.V_FOV / 2))
        th = tv * W / H
        yaw = math.degrees(math.atan((fx - 0.5) * 2 * th))        # 正＝往右
        pitch = math.degrees(math.atan((0.5 - fy) * 2 * tv / math.sqrt(1 + ((fx - 0.5) * 2 * th) ** 2)))      # 正＝往上（10/25 稽核：偏離中央的點要除以 √(1+x²)）
        return yaw, pitch

    @staticmethod
    def _locate(patch_im, im, near=None, second=False):
        """在 im 裡找 patch（灰階縮小後 NCC 比對）→ (x, y 0~1, 相似度 0~1)；near＝預期位置附近優先。
        second=True 多回第 4 項：離最像那塊 ≥10 像素的「第二像」相似度（10/25 04:1x：重複花紋時第二像跟最像差不多＝認不出是哪一塊）。"""
        import numpy as np
        W, H = 240, 124
        a = np.asarray(im.convert("L").resize((W, H)), dtype=np.float32)
        p = np.asarray(patch_im.convert("L"), dtype=np.float32)
        ph, pw = p.shape
        if ph >= H or pw >= W:
            return None
        from numpy.lib.stride_tricks import sliding_window_view
        win = sliding_window_view(a, (ph, pw))
        pm = p - p.mean()
        wm = win - win.mean(axis=(2, 3), keepdims=True)
        num = (wm * pm).sum(axis=(2, 3))
        den = np.sqrt((wm ** 2).sum(axis=(2, 3)) * (pm ** 2).sum()) + 1e-6
        ncc = num / den
        if near is not None:              # 只在預期位置附近找（18:1x～18:4x 整張找：相似度 0.6～0.8 的錯位置被當真，補轉越轉越偏）
            nx, ny, r = near[:3]
            ry = near[3] if len(near) > 3 else r          # 10/25 稽核：純左右轉時 y 幾乎不變，標點搜尋 y 要收窄
            cy0, cx0 = ny * H - ph / 2, nx * W - pw / 2
            yy, xx = np.mgrid[0:ncc.shape[0], 0:ncc.shape[1]]
            ncc = np.where((np.abs(xx - cx0) <= r * W) & (np.abs(yy - cy0) <= ry * H), ncc, -1.0)
        iy, ix = np.unravel_index(int(np.argmax(ncc)), ncc.shape)
        if not second:
            return ((ix + pw / 2) / W, (iy + ph / 2) / H, float(ncc[iy, ix]))
        yy2, xx2 = np.mgrid[0:ncc.shape[0], 0:ncc.shape[1]]
        far = (np.abs(xx2 - ix) >= 10) | (np.abs(yy2 - iy) >= 10)
        sim2 = float(ncc[far].max()) if far.any() else -1.0
        return ((ix + pw / 2) / W, (iy + ph / 2) / H, float(ncc[iy, ix]), sim2)

    def _settled(self, prev=None):
        """轉完等畫面停下來再拍（19:28：0.3 秒就拍，遊戲畫面還沒更新 → 誤判「沒轉」又拖一次＝轉兩倍）。"""
        time.sleep(0.35)
        a = self.grab()
        for _ in range(5):
            time.sleep(0.25)
            b = self.grab()
            if self.diff(a, b) < 3:
                return b
            a = b
        return a

    def look_at(self, x="", y=""):
        import math
        fx, fy = num(x, -1), num(y, -1)
        if not (0 <= fx <= 1 and 0 <= fy <= 1):
            return "視角移動到 要給畫面上的座標（0~1），例如 視角移動到(0.72,0.45)：x 是左右、y 是上下"
        if self.image_path or not self.live:
            im = self.grab()
            W, H = (im.width, im.height) if im is not None else (1920, 991)
            yaw, pitch = self._angles_to(fx, fy, W, H)
            if abs(yaw) < 1.5 and abs(pitch) < 1.5:
                return f"({fx:.2f},{fy:.2f}) 已經在畫面正中央（離中央不到 1.5 度），不用轉；要互動就 走 靠近或 長按(e)"
            return f"[dry-run] 會轉 左右 {yaw:+.0f} 度、上下 {pitch:+.0f} 度，讓 ({fx:.2f},{fy:.2f}) 到畫面正中央"
        before = self.grab()
        if before is None:
            return "抓不到畫面"
        W, H = before.width, before.height
        # 目標附近一小塊當「長相」，轉完在新畫面找它，確認到了中央沒有
        SW, SH = 240, 124
        small = before.resize((SW, SH))
        cx, cy = int(fx * SW), int(fy * SH)
        pw, ph = 24, 16
        box = (max(0, cx - pw // 2), max(0, cy - ph // 2), min(SW, cx + pw // 2), min(SH, cy + ph // 2))
        patch = small.crop(box)
        yaw, pitch = self._angles_to(fx, fy, W, H)
        if abs(yaw) < 1.5 and abs(pitch) < 1.5:
            return f"({fx:.2f},{fy:.2f}) 已經在畫面正中央（離中央不到 1.5 度），不用轉；要互動就 走 靠近或 長按(e)"
        # 10/24 20:2x 工具修正：第三人稱時目標轉到正中央就被自己的角色蓋住（轉完找不到 30～70%）→ 對到中央偏上 0.38（自己頭頂上方），左右還是正中
        # 10/25 00:0x 實測 41 輪後撤回：每次對準都多低頭 9.5 度，對幾次就把鏡頭壓到 -45 度極限（之後 dy 一律 0、目標到不了 0.38、找不到 15/22 反而更差）
        #   → 回到對正中央；被自己擋住時靠下面的「整張畫面位移」驗證。第三人稱擋中央的問題留給 走路量距離（已挖掉中央）處理
        ay = 0.5
        if ay != 0.5:
            pitch -= self._angles_to(0.5, ay, W, H)[1]
        pitch0 = max(-60.0, min(60.0, pitch))
        pitch = self._pitch_room(pitch0)                  # 10/23：上下只能 ±45 度
        lim_note = (f"（上下只能 ±{self.PITCH_LIM:.0f} 度，上下只轉了 {pitch:+.0f} 度、原本要 {pitch0:+.0f} 度" + self.LEVEL_HINT + "）" if abs(pitch - pitch0) >= 1 else "")
        chunk_meas = None
        yaw_calc = yaw
        yaw_send = yaw
        if abs(yaw) >= 10 and self.live and not self.image_path:
            # 10/25：超過 30 度分段轉、每段實測（使用者：確保前後畫面還有同一個東西）；第 53 輪：≥10 度都走這條（有實測比例閉環＋補轉）
            meas_, parts_, done_, stopped_, lm_used_ = self._turn_chunks(yaw)
            chunk_meas = (meas_, parts_ + (f"；中途量不出，停在 {done_:+.0f} 度" if stopped_ else ""), lm_used_, stopped_, done_)
            yaw = done_
            self._drag(0, -pitch * self.dy_deg)
        else:
            comp = self._comp_deg() if (abs(yaw) >= 3 and self.live and not self.image_path) else 0.0     # 第 50 輪
            yaw_send = yaw + comp * (1 if yaw > 0 else -1) if comp else yaw
            lim_note += self._comp_note(comp)
            self._drag(yaw_send * self.dx_deg, -pitch * self.dy_deg)
        self.pitch_acc = max(-89.0, min(89.0, self.pitch_acc + pitch))
        after = self._settled()
        if chunk_meas is None:
            self._verify_dump("視角移動到", before, after, 送出度=yaw, 送出上下=pitch, 目標x=fx, 目標y=fy)
        redrag = ""
        if self.diff(before, after) < 3 and abs(yaw) >= 3:
            # 17:40 第一次轉完目標還在 0.75（沒轉）：中央有 E 提示時從中央壓右鍵會按到提示 → 跟 轉 一樣改從畫面上方重拖
            self._drag(yaw_send * self.dx_deg, -pitch * self.dy_deg, fy=0.25)
            after = self._settled()
            redrag = "（從中央拖畫面沒動——中央可能有提示擋住，改從畫面上方重拖一次）"
            if self.diff(before, after) < 3:
                # 10/18 21:56：中央、上方都拖不動（整張畫面沒變）又補轉 → 直接回報，不補轉
                return (f"視角移動到：算好要轉 左右 {yaw:+.0f}、上下 {pitch:+.0f} 度，但從畫面中央、上方拖兩次畫面都沒變——"
                        "鏡頭現在轉不動（可能有面板／選單擋住滑鼠，或游標被鎖）；沒有補轉。先 關掉 面板或看看畫面上有什麼擋著")
        out = [f"算好：({fx:.2f},{fy:.2f}) 離中央 左右 {yaw_calc:+.0f} 度、上下 {pitch:+.0f} 度 → 送 dx {yaw * self.dx_deg:+.0f}、dy {-pitch * self.dy_deg:+.0f}"
               + (f"（分段後實際送出左右 {yaw:+.0f} 度）" if abs(yaw - yaw_calc) >= 1 else "") + lim_note]
        GOOD = 0.85                        # 18:4x：0.6 太鬆，認錯的位置也拿來補轉
        # 19:53：中央蓋著動物新聞海報（釘在畫面上的面板），他要對準的那塊在海報上 → 轉完原地相似度 1.00，又補轉越補越偏
        pinned = self._locate(patch, after, near=(fx, fy, 0.03)) if (abs(yaw) >= 5 and self.diff(before, after) >= 8) else None
        exp_dx, exp_dy = 0.5 - fx, ay - fy
        if pinned and not self._pin_ok(pinned[0] - fx, pinned[1] - fy, exp_dx, exp_dy):
            # 10/25 03:2x 第 8 輪：對 (0.54,0.52) 只轉 6 度、預期位移才 4%，那塊東西移到 (0.51,0.50)（跟著景物動）卻被當「釘住」轉回去
            #   → E 框明明到了中央還被轉回。釘住＝幾乎沒動（不到預期位移的四成）才算
            pinned = None
        # 10/20 11:3x：對右上任務字「在電腦上註冊」(0.91,0.21) 連叫 12 輪：第一次影像比對抓到「釘在畫面上」，之後任務框是半透明、
        # 背後的場景變了，相似度掉到 0.5～0.6 → 回「沒對準、再微調」，每次又往上轉 22 度，最後鏡頭對著天花板
        # → 影像比不出來時，再用字比：目標附近的字轉完還在原位＝釘在畫面上；釘住就把鏡頭轉回原方向
        pin_word = ""
        if not (pinned and pinned[2] >= 0.95) and abs(yaw) >= 5:
            import difflib
            def words2(im):              # 整張讀＋右上任務框放大讀（半透明框在亮背景上整張讀常讀不到）
                d = dict(self._words(im))
                if fx >= 0.75 and fy <= 0.45:
                    try:
                        d.update({w: (x, y) for w, x, y in top_right_words(im)})
                    except Exception:  # noqa: BLE001
                        pass
                return d
            near = [(w, xy) for w, xy in words2(before).items() if abs(xy[0] - fx) < 0.05 and abs(xy[1] - fy) < 0.05]
            if near:
                wa = words2(after)
                pin_word = next((w for w, xy in near for w2, (x2, y2) in wa.items()
                                 if abs(x2 - xy[0]) < 0.03 and abs(y2 - xy[1]) < 0.03
                                 and self._pin_ok(x2 - xy[0], y2 - xy[1], exp_dx, exp_dy)
                                 and (w2 == w or difflib.SequenceMatcher(None, w, w2).ratio() >= 0.6)), "")
        if (pinned and pinned[2] >= 0.95) or pin_word:
            try:
                self._append("刻度樣本.md", f"視角移動到({fx:.2f},{fy:.2f})｜目標釘在畫面上沒動（{'相似' + format(pinned[2], '.2f') if pinned and pinned[2] >= 0.95 else '字「' + pin_word + '」'}）")
            except Exception:  # noqa: BLE001
                pass
            self._drag(-yaw * self.dx_deg, pitch * self.dy_deg)          # 轉回原方向，免得越轉越偏
            self.pitch_acc = max(-89.0, min(89.0, self.pitch_acc - pitch))
            how = (f"那一塊還在原位 ({pinned[0]:.2f},{pinned[1]:.2f})（相似度 {pinned[2]:.2f}）" if pinned and pinned[2] >= 0.95
                   else f"那裡的字「{pin_word}」轉完還在原位")
            return ("；".join(out) + f"；轉完{how}＝它是釘在畫面上的東西（面板、海報、HUD、任務框、提示框），不是場景，"
                    "轉頭不會讓它移到中央；**已把鏡頭轉回原本的方向**，沒有補轉。要對準場景裡的東西；不確定哪裡是 UI 就叫 確認是否是UI")
        loc1 = self._locate(patch, after, near=(0.5, ay, 0.3)) if (patch.width >= 8 and patch.height >= 8) else None
        final = loc1
        bad_comp = False
        if loc1 and loc1[2] >= GOOD and (abs(loc1[0] - 0.5) > 0.2 or abs(loc1[1] - ay) > 0.2) and max(abs(yaw), abs(pitch)) >= 5:   # 10/26 16:5x：純上下轉（yaw 0）也要擋（16:46 送上下 −16、「找到」在 (0.27,0.57)、補轉 −32 跑到 (0.76,0.23)）
            # 10/26 11:2x 第 42 輪：11:09 送 +15 度，「找到」目標在 (0.27,0.77)（離中央 23%／27%＝差了 30 多度），補轉 −32/−17 更偏。
            #   轉完目標理應在中央附近；差超過 20% 畫面就是認錯（大廳人多、地磚同款），不拿它補轉，改用特徵點量實際轉了幾度
            out.append(f"轉完最像的一塊在 ({loc1[0]:.2f},{loc1[1]:.2f})（相似度 {loc1[2]:.2f}），離中央太遠、跟送出的角度對不上＝多半認錯，不拿它補轉")
            loc1 = None
            final = None
        if loc1 and loc1[2] >= GOOD:
            out.append(f"轉完找目標：在 ({loc1[0]:.2f},{loc1[1]:.2f})（相似度 {loc1[2]:.2f}）")
            if abs(loc1[0] - 0.5) > 0.04 or abs(loc1[1] - ay) > 0.06:      # 偏了補一次
                y2, p2 = self._angles_to(loc1[0], loc1[1], W, H)
                p2 -= self._angles_to(0.5, ay, W, H)[1] if ay != 0.5 else 0
                p2 = self._pitch_room(p2)
                self._drag(y2 * self.dx_deg, -p2 * self.dy_deg)
                self.pitch_acc = max(-89.0, min(89.0, self.pitch_acc + p2))
                after = self._settled()
                final = self._locate(patch, after, near=(0.5, ay, 0.3))
                out.append(f"偏了，補轉 左右 {y2:+.0f}、上下 {p2:+.0f} 度" + (f" → 現在在 ({final[0]:.2f},{final[1]:.2f})（相似度 {final[2]:.2f}）" if final else ""))
                d1 = abs(loc1[0] - 0.5) + abs(loc1[1] - ay)
                d2 = (abs(final[0] - 0.5) + abs(final[1] - ay)) if (final and final[2] >= GOOD) else None
                if (final and final[2] >= GOOD and abs(final[0] - loc1[0]) < 0.05 and abs(final[1] - loc1[1]) < 0.06
                        and abs(loc1[0] - fx) < 0.05 and abs(loc1[1] - fy) < 0.06 and (abs(y2) >= 8 or abs(p2) >= 8)):
                    # 10/26 09:5x 第 41 輪：角色身上的剪貼簿 E框 會上下晃 0.03，原本 0.02 門檻抓不到（09:39／09:41 各補轉一次還是「不一致」）
                    # 10/26 05:1x 第 35 輪：05:03 送 -14 目標從 (0.41,0.47) 沒動、補轉 -14 還是沒動，卻回「偏了…沒有完全對準」
                    return ("；".join(out) + f"；**轉了兩次目標都沒動（還在 ({final[0]:.2f},{final[1]:.2f})）＝鏡頭根本沒轉**：右鍵壓到的地方可能是 UI（角色名牌／E框／面板）或游標被鎖；"
                            "這塊若是釘在畫面上的字也會這樣。先 關掉 面板、或 確認是否是UI；要對準場景裡的東西")
                if d2 is not None and d2 > d1 + 0.08:
                    # 10/25 13:0x 第 20 輪：第一次「找到目標」在 (0.41,0.77) 其實認錯（相似 0.88），照它補轉 -14/-20 後目標跑到 (0.77,0.27) 更偏
                    #   → 補轉後比補轉前更偏＝第一次認錯，轉回補轉前、不算對準
                    self._drag(-y2 * self.dx_deg, p2 * self.dy_deg)
                    self.pitch_acc = max(-89.0, min(89.0, self.pitch_acc - p2))
                    after = self._settled()
                    final = None
                    bad_comp = True
                    out.append(f"補轉後反而更偏（離中央 {d1:.2f}→{d2:.2f}）＝第一次找到的那塊可能認錯；**已轉回補轉前**，這次沒對準，看下一張畫面再對")
        else:
            final = None
        ok = bool(final and final[2] >= GOOD and abs(final[0] - 0.5) <= 0.05 and abs(final[1] - ay) <= 0.08) and not bad_comp
        if bad_comp:
            pass
        elif not (loc1 and loc1[2] >= GOOD):
            # 17:38～17:43 三次「找不到」：目標轉到正中央剛好被自己的角色（第三人稱）擋住 → 改用整張畫面的左右位移驗證有沒有轉對
            d_all = self.diff(before, after)
            sh, _ = self._shift(before, after, d_all)
            if chunk_meas is not None:
                meas, parts, lm_used, stopped_c, done_c = chunk_meas
                ok = abs(meas - yaw_calc) <= max(5.0, 0.15 * abs(yaw_calc))   # 10/26 第 34 輪：yaw 已是送出總量（含補轉），要跟算好的 yaw_calc 比（03:43 要 44 實測 43 被判不一致）
                short = stopped_c and abs(done_c - yaw_calc) >= 3   # 10/25 06:5x：第一段就量不出 → 停在 20 度卻回「已對準正中央」（要 41 度）；10/26 02:0x：分段後 yaw 已改成 done、補轉後會超過算的 → 只有中途停才算「只轉了」
                if short or lm_used == 0:
                    ok = False
                out.append(f"轉完在中央附近找不到原本那一塊（轉到中央常被你自己的角色擋住）→ 每段實測：{parts}；"
                           f"你實際轉了約 {meas:+.0f} 度（送出 {yaw:+.0f}、原本算要 {yaw_calc:+.0f}）")
                n_seg = max(1, int(math.ceil(abs(yaw) / 30.0)))
                segs_ok = bool(getattr(self, "_seg_ok", [])) and all(self._seg_ok)
                if short:
                    out.append(f"**只轉了 {done_c:+.0f} 度（要 {yaw_calc:+.0f}），目標還沒到中央**；剩下的 {yaw_calc - done_c:+.0f} 度下一輪再 視角移動到（目標現在應該在畫面{'左' if yaw_calc < 0 else '右'}半邊）")
                elif lm_used == 0:
                    out.append("（沒有標點、沒量到實際轉了幾度，不能說已對準；看下一張畫面確認）")
                elif getattr(self, "_rep_flag", False) and not ((lm_used >= n_seg - 1 or (lm_used >= 1 and segs_ok)) and ok):
                    out.append(f"（{self.REP_NOTE}）")
                    ok = False
                elif getattr(self, "_chunk_unsure", False) and not ok:
                    out.append(f"（有一段整張位移量到不合理的值、照送出算，合計 {meas:+.0f} 僅參考、不判一致）")
                elif getattr(self, "_chunk_unsure", False):
                    ok = False      # 第 49 輪：18:50 兩段各只轉到 -4／-6，補轉 -24 量不出照送出算 → 合計 -34「一致、已對準」，其實目標還在左邊
                    out.append(f"（有一段量不出、是估的，合計 {meas:+.0f} 僅參考、不能說已對準；看下一張畫面確認目標在哪）")
                else:
                    out.append((self._cmp("視角移動到 分段實測", f"{yaw_calc:+.0f}", f"{meas:+.0f}", ok, "度") + ("（特徵點實測合計）" if "特徵點" in parts else "（標點實測合計）")
                                + ("（畫面有重複花紋，但標點段都跟送出一致）" if getattr(self, "_rep_flag", False) else "")) if lm_used
                               else "（沒有找到標點，度數是整張位移估的，僅參考）")
            else:
                # 10/25 03:0x 第 7 輪：≤30 度這條路原本只回「整張位移 %」，找不到 56% 局內（基準 62%）幾乎沒改善
                #   → 跟 轉 一樣用標點量「實際轉了幾度」（使用者：工具要回饋轉了幾度＋物品標點），跟整張位移交叉檢查；一致就算對準
                lm_deg, lm_txt, scene_deg = self._landmark_verify(before, after, yaw, sh, pitch=pitch)
                head = ("轉完在中央附近找不到原本那一塊（轉到中央常被你自己的角色擋住"
                        + (f"；最像的一塊相似度只有 {loc1[2]:.2f}，不算數、不補轉" if loc1 else "") + "）")
                if lm_deg is not None and abs(yaw) >= 3 and getattr(self, "_rep_flag", False):
                    ok = False
                    out.append(head + f"→ 量到約 {lm_deg:+.0f} 度（要轉 {yaw:+.0f}），但 {self.REP_NOTE}")
                elif lm_deg is not None and abs(yaw) >= 3:
                    ok = abs(lm_deg - yaw) <= max(5.0, 0.15 * abs(yaw))
                    out.append(head + f"→ 量實際轉了幾度：{lm_txt}；你實際轉了約 {lm_deg:+.0f} 度（要轉 {yaw:+.0f} 度）"
                               + (f"；整張位移換算 ≈ {scene_deg:+.0f} 度" if (scene_deg is not None and not getattr(self, "_feat_used", False)) else ""))
                    self._turn_record(yaw_send, lm_deg)
                    out.append(self._cmp("視角移動到 特徵點實測" if getattr(self, "_feat_used", False) else "視角移動到 標點實測", f"{yaw:+.0f}", f"{lm_deg:+.0f}", ok, "度")
                               + ("（目標應該就在正中央、被你自己的角色擋住看不到）" if ok else "（差太多，可能沒轉到、或滑鼠拖到一半被擋）") + self._under_turn_note(yaw_send, lm_deg))
                elif scene_deg is not None and abs(yaw) >= 3:
                    ok = abs(scene_deg - yaw) <= max(5.0, 0.15 * abs(yaw))      # 10/25 稽核：改比度數、用 _cmp（原本「要 vs 實測」混進二次驗證一致率）
                    out.append(head + f"→ 標點量不到（{lm_txt}）、改用整張位移（左右兩帶）換算：鏡頭轉了約 {scene_deg:+.0f} 度（要轉 {yaw:+.0f} 度；畫面變化 {d_all:.0f}）")
                    out.append(self._cmp("視角移動到 整張位移", f"{yaw:+.0f}", f"{scene_deg:+.0f}", ok, "度") + "（沒有標點，整張位移沒標點準）")
                elif abs(yaw) < 3 and abs(pitch) >= 3:
                    # 10/25 06:5x：純上下時標點 x 不會動、整張位移是水平的 → 全「量不出」；改量上下位移換算度數
                    pm, pm_txt = self._pitch_from_vshift(before, after)
                    if pm is not None:
                        ok = abs(pm - pitch) <= max(3.0, 0.3 * abs(pitch))
                        out.append(head + f"→ 只有上下轉，改量上下位移：{pm_txt}（要轉 {pitch:+.0f} 度）")
                        out.append(self._cmp("視角移動到 上下", f"{pitch:+.0f}", f"{pm:+.0f}", ok, "度") + ("（目標應該在正中央、可能被自己擋住）" if ok else ""))
                    else:
                        out.append(head + f"→ 只有上下轉，上下位移也量不出（{pm_txt}；畫面變化 {d_all:.0f}），看下一張畫面確認")
                else:
                    out.append(f"轉完找不到原本那一塊，標點（{lm_txt}）和整張畫面的位移也量不出來（畫面變化 {d_all:.0f}），看下一張畫面確認")
        if final and final[2] >= GOOD and loc1 and loc1[2] >= GOOD:
            out.append(self._v2("視角移動到", f"({loc1[0]:.2f},{loc1[1]:.2f})",
                                f"({final[0]:.2f},{final[1]:.2f})", ok) + f"（第一次＝轉完、第二次＝補轉後；目標應在 0.50,{ay:.2f} 附近）")
        if redrag:
            out.insert(1, redrag)
        out.append(("**已對準正中央**" if ay == 0.5 else f"**已對準：左右正中、上下在 {ay:.2f}（第三人稱對到自己頭頂上方，免得被自己擋；往前走一樣會走向它）**")
                   if ok else "沒有完全對準；看下一張畫面，再用 視角移動到 或 轉 微調")
        try:
            self._append("刻度樣本.md", f"視角移動到({fx:.2f},{fy:.2f})｜算 左右{yaw:+.0f} 上下{pitch:+.0f}｜結果 "
                         + (f"({final[0]:.2f},{final[1]:.2f}) 相似{final[2]:.2f}" if (final and final[2] >= GOOD) else "找不到") + f"｜{'對準' if ok else '沒對準'}")
        except Exception:  # noqa: BLE001
            pass
        return "；".join(out)

    def look_level_zero(self):
        """水平歸零：你確認畫面已經水平，把水平儀（仰 的累計）歸零。"""
        old = self.pitch_acc
        self.pitch_acc = 0.0
        return f"水平儀歸零（原本累計 {old:+.0f} 度）"

    def _new_word_checker(self, start):
        """慢慢轉／走到有變化 共用：起點的字＋這段對話讀過的字當基準，回傳判斷「這個字是不是新出現的 UI」的函式。"""
        # 起點讀兩次當基準（OCR 對 HUD 的讀法會抖：「蓋印表格」→「/ 、印表」、理智數字每秒變、「$ 20」空格不同）
        base_words = set(self._words(start).keys())
        time.sleep(0.15)
        base_words |= set(self._words(self.grab() or start).keys())
        # 19:4x 四次都被 HUD 字停下（「輪班 1」「拍照」「3Ø%」「~ 0」「$2e」）：起點兩次 OCR 沒讀到它們。
        # 改：這段對話裡讀過的所有字都算基準（HUD 字之前一定出現過）；新字要連續兩步都讀到才算（OCR 閃一下不算）
        base_words |= set(getattr(self, "_seen_words", set()))

        def is_new(w):
            n = re.sub(r"[\s,，、.．:：/／|｜%$\-—_~]+", "", w)
            digits = sum(ch.isdigit() or ch in "%$ØO○~.,:/" for ch in n)
            if not n or digits >= max(1, len(n) / 2):                  # 一半以上是數字符號＝HUD 計數器（含 Ø、~ 誤讀）
                return False
            if len(n) < 2 and n.upper() != "E":                         # 單字只認 E（互動提示）
                return False
            try:
                import zh_text
                for b in base_words:
                    bn = re.sub(r"[\s,，、.．:：/／|｜%$\-—_]+", "", b)
                    if not bn:
                        continue
                    if n in bn or bn in n or zh_text.similar(n, bn) >= 0.6:
                        return False
            except Exception:  # noqa: BLE001
                for b in base_words:
                    if n in b or b in n:
                        return False
            return True

        return is_new

    def look_slow(self, 方向=""):
        """慢慢轉（2026-10-15 18:0x 使用者）：一次 3 度慢慢轉，直到跳出 UI（畫面上出現原本沒有的字，例如 E 提示）
        或畫面有比較大的變動（由本機視覺判斷：OCR 新字＋前後幀差異），最多轉一圈。"""
        d = str(方向).strip().lower()
        sign = -1 if d in ("左", "l", "left", "-") else (1 if d in ("右", "r", "right", "+", "") else None)
        if sign is None:
            return "慢慢轉 要給方向：左 或 右，例如 慢慢轉(右)"
        if self.image_path or not self.live:
            return f"[dry-run] 會往{'右' if sign > 0 else '左'}每次 3 度慢慢轉，直到出現新字或畫面大變"
        step_px = 3 * self.dx_deg * sign
        start = self.grab()
        if start is None:
            return "抓不到畫面"
        is_new = self._new_word_checker(start)

        prev = start
        turned = 0
        reason = ""
        pending = set()
        for i in range(120):                      # 120 × 3 度 = 一圈
            self._drag(step_px, 0)
            turned += 3
            time.sleep(0.12)
            cur = self.grab()
            if cur is None:
                reason = "抓不到畫面"
                break
            d_step = self.diff(prev, cur)
            words = self._words(cur)
            cand = {re.sub(r"[\s,，、.．:：/／|｜%$\-—_~]+", "", w): w for w in words if is_new(w)}
            new = [cand[k] for k in cand if k in pending]       # 連續兩步都讀到才算新字
            pending = set(cand.keys())
            if new:
                reason = "跳出原本沒有的字（UI）：" + "、".join(f"「{w}」({words[w][0]:.2f},{words[w][1]:.2f})" for w in new[:5])
                break
            if d_step >= 35:
                reason = f"畫面大幅變動（這 3 度前後差 {d_step:.0f}）"
                break
            prev = cur
        else:
            reason = "轉滿 360 度沒有新字、也沒有大變動"
        self._append("刻度樣本.md", f"慢慢轉{'右' if sign > 0 else '左'} {turned} 度｜停：{reason[:60]}")
        return (f"慢慢轉{'右' if sign > 0 else '左'}：轉了 {turned} 度（每次 3 度）後停下，原因：{reason}。"
                "現在的畫面下一輪給你；要繼續就再叫一次")

    def walk_until(self, 方向="", 秒上限=""):
        """走到畫面有變化（2026-10-16 10:4x 使用者）：按著方向鍵一直走，直到畫面突然大變（例如跳出 UI／E 提示）才放開。
        停的條件：①出現原本沒有的字（連續兩次讀到）②這一瞬間的變化遠大於走路平常的變化 ③連續幾次畫面不動＝被擋 ④到秒上限。"""
        d = str(方向).strip().lower()
        keymap = {"": "w", "前": "w", "w": "w", "往前": "w", "後": "s", "s": "s", "退": "s",
                  "左": "a", "a": "a", "右": "d", "d": "d"}
        k = keymap.get(d)
        if k is None:
            return "走到有變化 的方向要寫：前、後、左、右（不寫＝前），例如 走到有變化(前)"
        name = {"w": "前", "s": "後", "a": "左", "d": "右"}[k]
        limit = max(1.0, min(10.0, num(秒上限, 5.0)))
        if self.image_path or not self.live:
            return f"[dry-run] 會按著 {k} 往{name}走，最多 {limit:.0f} 秒，畫面突然大變／出現新字／被擋就停"
        start = self.grab()
        if start is None:
            return "抓不到畫面"
        is_new = self._new_word_checker(start)
        import pydirectinput
        prev, diffs, pending, reason = start, [], set(), ""
        t0 = time.time()
        self._fg_ok()
        pydirectinput.keyDown(k)
        try:
            while True:
                time.sleep(0.12)
                el = time.time() - t0
                cur = self.grab()
                if cur is None:
                    reason = "抓不到畫面"
                    break
                dd = self.diff(prev, cur)
                diffs.append(dd)
                prev = cur
                if el > 0.5 and len(diffs) >= 4:
                    base = sorted(diffs[:-1])[len(diffs[:-1]) // 2]          # 走路平常的變化（中位數）
                    if dd >= max(35.0, base * 2.5 + 10):
                        reason = f"畫面突然大變（這一下變化 {dd:.0f}，走路平常約 {base:.0f}）"
                        break
                    if all(x < 2 for x in diffs[-4:]):
                        reason = "連續幾次畫面都不動＝前面被擋"
                        break
                words = self._words(cur)
                cand = {re.sub(r"[\s,，、.．:：/／|｜%$\-—_~]+", "", w): w for w in words if is_new(w)}
                new = [cand[x] for x in cand if x in pending]
                pending = set(cand.keys())
                if new:
                    reason = "跳出原本沒有的字（UI）：" + "、".join(f"「{w}」({words[w][0]:.2f},{words[w][1]:.2f})" for w in new[:5])
                    break
                if time.time() - t0 >= limit:
                    reason = f"走滿 {limit:.0f} 秒沒有大變化、沒有新字"
                    break
        finally:
            pydirectinput.keyUp(k)
        el = time.time() - t0
        total = self.diff(start, self.grab())
        return (f"走到有變化：按著 {k} 往{name}走了 {el:.1f} 秒後放開，原因：{reason}。"
                f"起點到現在畫面變化 {total:.0f}。現在的畫面下一輪給你；要繼續就再叫一次")

    def look_left(self, 度=""):
        return self.look_turn(-num(度, 30))

    def look_right(self, 度=""):
        return self.look_turn(num(度, 30))

    def look_up(self, 度=""):
        return self.look_pitch(num(度, 15))

    def look_down(self, 度=""):
        return self.look_pitch(-num(度, 15))

    def look_180(self):
        return self.look_turn(180)

    # ---- 互動
    def key_press(self, 鍵="", 秒=""):
        """按(鍵)：一下 0.15 秒（10:5x 使用者：「按 為什麼需要秒」→ 秒拿掉；要長就用 長按）。"""
        k = str(鍵).split(",")[0].split("，")[0].strip().lower() or "e"     # 他寫 按(e,1.5) 也只取鍵名
        return self._move(k, 0.15, 0.15, 0.15)

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
            # 10/24 稽核：同一輪先 看四周 再 點，原本會把四張環視圖蓋掉 → 接在後面
            self.extra_images = list(getattr(self, "extra_images", None) or []) + [(f"預約要點的位置 ({fx:.2f},{fy:.2f}) 放大圖", im.crop(box))]
        self.pending_click = {"x": fx, "y": fy, "字": str(字 or ""), "附近的字": near, "時間": time.time()}
        return (f"已預約點 ({fx:.2f},{fy:.2f}){'「' + str(字) + '」' if 字 else ''}；附近的字：{near or '無'}。"
                "**還沒點**。下一輪看放大圖，逐條審核絕對限制（不花錢／不換職業／不離開），確定才送 確認點(審核)，不對就 取消點")

    def click_confirm(self, 審核=""):
        p = self.pending_click
        if not p:
            return "沒有預約中的點擊。先用 點 或 點字 預約"
        chk = str(審核).strip()
        words = re.sub(r"[\d\s.,，、。:：;；|｜()（）\[\]\-+_/xXyY%]+", "", chk)   # 10/18：「0.56,0.48」純座標也過了 → 扣掉數字符號後要有字
        if not chk or chk in ("審核", "審核結果") or len(chk) < 6 or len(words) < 4:
            return ("確認點 要寫真的審核內容（文字，不是座標或數字），逐條：不花錢｜不換職業｜不離開｜這是什麼按鈕。"
                    "不能照抄「審核」兩個字")
        if time.time() - p["時間"] > 180:
            self.pending_click = None
            return "預約超過 3 分鐘，畫面可能變了，取消；要點就重新預約"
        self._append("點擊審核.md", f"點({p['x']:.2f},{p['y']:.2f}){p['字']}｜附近:{p['附近的字']}｜審核:{審核}")
        self.last_click = p                # 10/24 稽核：點完再 記按鈕(名稱,,,效果) 要拿得到剛點的位置
        self.pending_click = None
        return "確認通過 → " + self._do_click(p["x"], p["y"])

    def click_cancel(self):
        self.pending_click = None
        return "取消了預約的點擊"

    def _do_click(self, fx, fy):
        before, noise = self._grab_noise()
        if self.live:
            import pydirectinput
            bbox = self.win.client_bbox() if self.win else None
            if not bbox:
                return "找不到 Roblox 視窗"
            self._fg_ok()
            pydirectinput.moveTo(int(bbox["left"] + fx * bbox["width"]), int(bbox["top"] + fy * bbox["height"]))
            time.sleep(0.05)
            # 10/17 13:3x：Roblox 介面按鈕（ESC 繼續、辦理入住、出口）點了畫面變化 0 的居多（10/16～17 約七成）；
            # 游標瞬移過去 Roblox 常沒認到滑到按鈕上 → 原地小晃一下再按
            pydirectinput.moveRel(3, 0, relative=True)
            time.sleep(0.04)
            pydirectinput.moveRel(-3, 0, relative=True)
            time.sleep(0.06)
            pydirectinput.mouseDown()
            time.sleep(0.08)
            pydirectinput.mouseUp()
        else:
            print(f"   [dry-run] 點 ({fx:.2f},{fy:.2f})")
        return f"點了 ({fx:.2f},{fy:.2f})；" + self._changed(before, noise)

    def click_text(self, 字=""):
        """點字：10:5x 統計 56 次只找到 14 次（找到的全對），42 次是 OCR 讀歪（「亻呆安」「你的況很要」）
        → 改用主專案 zh_text 的繁簡／形近字模糊比對，回傳附相似度；真的對不對還是靠下一輪放大圖審核。"""
        key = str(字).strip()
        txt = self.see_text()
        items = re.findall(r"「([^」]*)」\(([\d.]+),([\d.]+)\)", txt)
        if not items:
            return f"畫面上沒讀到任何字，點不了「{key}」。改用 點(x,y) 或走近一點再讀"
        texts = [t for t, _, _ in items]
        hit = None
        try:
            import zh_text
            cands = zh_text.find(key, texts, min_ratio=0.6)
            if cands:
                i, ratio, how = cands[0][0], cands[0][1], (cands[0][2] if len(cands[0]) > 2 else "")
                hit = (items[i], ratio, how)
        except Exception:  # noqa: BLE001
            for it in items:
                if key in it[0]:
                    hit = (it, 1.0, "包含")
                    break
        if not hit:
            near = "、".join(texts[:12])
            return f"畫面上沒有像「{key}」的字（相似度都 <0.6）。讀到的字：{near}。對的字在畫面上但讀不出來就用 點(x,y)"
        (t, x, y), ratio, how = hit
        return (f"找到「{t}」（跟「{key}」相似度 {ratio:.2f}{'，' + how if how else ''}）→ "
                + self.click_xy(x, y, 字=t))

    def scroll(self, 格=""):
        if not str(格).strip():
            return "捲 要給格數（-5～5，負＝拉遠）；捲過之後刻度／深度會失效，要重新 開工測試"
        n = int(num(格, -3))
        n = max(-5, min(5, n))
        if self.live:
            import ctypes
            self._fg_ok()
            for _ in range(abs(n)):          # pydirectinput 沒有 scroll（04:0x 執行失敗 4 次）→ 用 Win32 滾輪事件
                ctypes.windll.user32.mouse_event(0x0800, 0, 0, 120 if n > 0 else -120, 0)
                time.sleep(0.05)
        else:
            print(f"   [dry-run] 捲 {n}")
        # 10/18 16:5x 使用者：「減少中鍵用於縮放視角，避免每次都要重新量測」→ 回傳提醒（不擋）
        # 10/19 10:2x：切換第一／第三人稱就是要捲 → 捲過人稱可能變了，先標成要重新回報
        if "人稱" in self.startup:
            self.startup["人稱"] = "（捲過，用 確認人稱 重新量）"
        # 10/19 10:3x 使用者：「但是如果有對鏡頭做縮放就要重新測量」→ 量過的刻度標成失效、驗證作廢，直到再 開工測試
        if self.live and n:
            self.zoomed_at = datetime.now().strftime("%H:%M") + f" 捲 {n} 格"
            self.startup.pop("驗證", None)
        return (f"捲了 {n} 格（正＝往上／拉近，負＝往下／拉遠）。在遊戲畫面上捲＝鏡頭遠近變了，"
                "每度 dx／dy、深度比例、轉頭位移都會跟著變，量過的刻度就不準了。"
                "要切換第一／第三人稱才捲（使用者交代）；捲完看畫面用 回報人稱 回報現在是第幾人稱，"
                "**縮放過就要重新測量**（使用者交代）：開工測試＋驗開工，面板【開工數值】會標「捲過」直到重量")

    def wait(self, 秒=""):
        d = max(0.2, min(5.0, num(秒, 1.0)))
        if self.live:
            time.sleep(d)
        return f"等了 {d:.1f} 秒"

    # ---- 意外
    def fix_close(self):
        """關掉：面板／選單／付費視窗都是 esc（10:5x 合併 關掉面板＋付費視窗；脫困、迷路、死亡重生拿掉，他自己用 轉／退／點字 組）。"""
        return self.fix_panel()

    def fix_stuck(self):
        return "脫困 已經拿掉：自己組——退(0.4)、轉(180) 看背後、再決定；或 看四周"

    def fix_panel(self):
        before = self.grab()
        r = self.key_press("esc", 0.1)
        if self.image_path:
            return "關掉面板：按了 esc（離線看不出有沒有關掉）"
        after = self.grab()
        if self.diff(before, after) < 2:
            # 10/25 審查：原本自動 捲(-3) 會把刻度／深度／人稱整套作廢（使用者 10/18 也說少用中鍵縮放）
            return "按 esc 畫面沒變（可能本來就沒有面板）；面板還在就用 點字 點它的叉叉或「關閉」" + self._panel_buttons(after)
        # 10/20 07:1x：購買視窗按 esc 沒關到，反而打開 Roblox 平台選單（帳號／邀請好友／ESC 繼續），他花兩輪才關回
        # → 按完看一下，是平台選單就再按一次 esc 關回，回傳講清楚
        menu = ("帳號", "邀請好友", "在此伺服器中", "協助")
        if sum(1 for w in self._words(after) if any(m in w for m in menu)) >= 2:
            self.key_press("esc", 0.1)
            time.sleep(0.3)
            still = sum(1 for w in self._words(self.grab()) if any(m in w for m in menu)) >= 2
            return ("關掉面板：按 esc 打開的是 Roblox 平台選單（帳號／邀請好友／設定），不是遊戲裡的視窗；"
                    + ("已再按一次 esc，選單還在——點底部藍色「繼續」" if still else "已再按一次 esc 關回遊戲")
                    + "。遊戲裡的視窗（購買道具等）要點它右上角的 X" + ("" if still else (time.sleep(0.5) or self._panel_buttons(self.grab()))))
        return "關掉面板：" + r

    def _panel_buttons(self, im):
        """10/26 08:5x 第 40 輪：監看模式底部有「< 辦理入住 >」「出口」，esc 關不掉、他 14 輪在關掉／走／轉之間打轉
        → 把畫面下半（HUD 左下角以外）讀到的字列出來，讓他自己決定點哪個（只給讀數）。"""
        try:
            words = self._words(im)
        except Exception:  # noqa: BLE001
            return ""
        cand = [(w, xy) for w, xy in words.items() if xy[1] >= 0.65 and 0.20 <= xy[0] <= 0.85 and len(w.strip()) >= 2]   # 09:06 關掉 沒列到「辦理入住」(0.50,0.80)／「出口」(0.50,0.88)：esc 關回後馬上抓、字還沒出來
        if not cand:
            return ""
        return "。畫面下半現在讀到這些字（esc 關不掉的面板通常要點它自己的按鈕）：" + "、".join(f"「{w}」({xy[0]:.2f},{xy[1]:.2f})" for w, xy in cand[:6])

    def fix_paywall(self):
        return "付費視窗：" + self.key_press("esc", 0.1) + "（鐵律：不花錢）"

    def _place(self):
        try:
            gj = json.load(open(os.path.join(self.gdir, "遊戲.json"), encoding="utf-8"))
            return gj.get("大廳place") or next(iter(gj.get("places", {})), None)
        except Exception:  # noqa: BLE001
            return None

    def fix_return(self):
        """回到遊戲：用深層連結直接開這隻遊戲（04:0x 他 esc→l→enter 之後掉到 Roblox 平台頁，點封面卡片 6 次都沒進去）。"""
        place = self._place()
        if not place:
            return "遊戲.json 沒有 place，不知道要開哪隻"
        if not self.live:
            return f"[dry-run] 會開 roblox://experiences/start?placeId={place}"
        try:
            os.startfile(f"roblox://experiences/start?placeId={place}")
        except Exception as e:  # noqa: BLE001
            return f"開深層連結失敗：{e}"
        time.sleep(40)
        try:
            from play_agent import RobloxWindow
            self.win = RobloxWindow()
            self.win.find()
            self.win.activate()
        except Exception:  # noqa: BLE001
            pass
        self._append("大任務.md", f"回到遊戲：深層連結 place {place}")
        return f"已用深層連結開 place {place}（等了 40 秒）。下一輪看畫面：應該在這隻遊戲的大廳或進場畫面；不是就再叫一次"

    def fix_rejoin(self):
        self.key_press("esc", 0.1)
        time.sleep(0.5 if self.live else 0)
        self.key_press("l", 0.1)
        time.sleep(0.5 if self.live else 0)
        self.key_press("enter", 0.1)
        if self.live:
            time.sleep(6)
        r = self.fix_return()           # 離開之後一定掉到 Roblox 平台頁，直接用深層連結拉回同一隻遊戲
        return "退出重進：esc→l→enter 離開，然後 " + r

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

    def mem_place(self, 名稱="", 樣子="", 種類=""):
        # 記站位、標點 併進來：種類＝地點（預設）／站位／特徵物
        return self._append("地點.md", (f"[{種類}]" if str(種類).strip() else "") + f"{名稱}｜{樣子}")

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
    step_log = []          # 程式做過的每一步：{"做","結果應該是","回傳","符合"}（主迴圈填）
    pending_done = None    # 送了一次、等回顧的小任務完成
    last_done = None       # 最近一次記下的完成 {"種類","內容","時間"}

    def _recall3(self):
        last = self.step_log[-3:]
        if not last:
            return "（還沒有做過的步驟紀錄）"
        fz = {True: "true", False: "false"}
        return NL.join(f"倒數第{len(last) - i}步 做:{st['做']}｜結果應該是:{st.get('結果應該是') or '（沒寫）'}"
                       f"｜程式回傳:{str(st.get('回傳', ''))[:140]}｜你填的符合:{fz.get(st.get('符合'), '還沒填')}"
                       for i, st in enumerate(last))

    def _done_review(self, kind, what, review, fname, prefix):
        """10/18 12:5x 使用者：「送完成前先回顧三步」——第一次送只列出完成前最後三步，回顧過再送一次才記完成。"""
        rv = re.sub(r"^\s*回顧\s*[:：]\s*", "", str(review or "")).strip()
        words = re.sub(r"[\d\s.,，、。:：;；|｜()（）\[\]\-+_/%]+", "", rv)
        p = self.pending_done
        # 10/20 06:0x：他一輪送兩次 任務完成(拍照,回顧…)：第一次記下、第二次沒有等回顧的又被打回「還沒算」，
        # 他看最後一個回傳以為沒算，連送 6 輪（記了 5 次）→ 剛記過的同一件再送，直接說已經記下了
        ld = self.last_done
        if ld and ld["種類"] == kind and time.time() - ld["時間"] <= 900 and (
                not what or not ld["內容"] or what in ld["內容"] or ld["內容"] in what):
            return f"{kind}「{ld['內容']}」{int((time.time() - ld['時間']) // 60)} 分鐘前已經記下了，不用再送；接著做下一件"
        if p and p["種類"] == kind and len(words) >= 6 and time.time() - p["時間"] <= 900:
            self.pending_done = None
            name = what or p["內容"]
            self.last_done = {"種類": kind, "內容": name, "時間": time.time()}
            self._append(fname, f"{prefix}{name}｜回顧:{rv}｜完成前三步:{' → '.join(str(x['做']) for x in p['三步']) or '無'}")
            if kind == "任務完成":
                return f"{kind} 記下了：{name}（回顧：{rv}）" + NL + self._small_finish(name, rv, p["三步"])
            return f"{kind} 記下了：{name}（回顧：{rv}）"
        self.pending_done = {"種類": kind, "內容": what, "時間": time.time(), "三步": [dict(x) for x in self.step_log[-3:]]}
        head = ""
        if rv:
            head = ("回顧要寫真的內容（文字：三步各自確認了什麼），這次不算；" if (p and p["種類"] == kind)
                    else "要先看過完成前的三步，下一輪再寫回顧；")
        return (head + f"還沒算 {kind}。先回顧完成前最後三步：" + NL + self._recall3() + NL
                + f"→ 逐步看是不是真的做成了（回傳有沒有反應、畫面有沒有變成預期）。確定就之後再送一次 {kind}({what or '…'},回顧:三步各自確認的結果)；沒做成就不要送，繼續做")

    def flow_step_done(self, 做了什麼="", 回顧=""):
        return self._done_review("這步做完", str(做了什麼).strip(), 回顧, "步驟.md", "做完:")

    def flow_task_done(self, 任務="", 回顧=""):
        return self._done_review("任務完成", str(任務).strip(), 回顧, "任務.md", "完成:")

    def flow_give_up(self, 為什麼=""):
        return self._append("放棄.md", f"放棄:{為什麼}")

    # ---- 檢討（2026-10-15 12:1x 使用者：每個行為列 原因／應有的結果／實際結果，實際跟預期不同就直接開檢討）
    reviews = []

    def flow_review(self, 原因="", 預期="", 實際="", 錯在哪="", 下次=""):
        vals = {"原因": 原因, "預期": 預期, "實際": 實際, "錯在哪": 錯在哪, "下次": 下次}
        bad = [k for k, v in vals.items() if not str(v).strip() or str(v).strip() == k or len(str(v).strip()) < 2]   # 10/25 審查：「沒動」兩個字也算有寫
        if bad:          # 12:1x 他把五格填成「原因、預期…」字面（跟早上審核照抄一樣）→ 退回
            # 10/19 03:1x：他只給 3～4 格被退回，連送 6 次同樣格式 → 回傳列出每格收到什麼，讓他看到少哪格
            got = "、".join(f"{k}=「{str(v).strip()[:30] or '空'}」" for k, v in vals.items())
            return ("檢討 沒記下（五格要各寫一格、用逗號分開）：" + "、".join(bad) + " 沒寫。程式收到的是：" + got
                    + "。例：檢討(按E想開門,門打開,門沒動,距離太遠E沒亮,先走近看到E提示再按)")
        line = f"原因:{原因}｜預期:{預期}｜實際:{實際}｜錯在哪:{錯在哪}｜下次:{下次}"
        # 10/19 使用者「多多檢討多多找錯 避免重複犯錯」：跟這個大任務之前的檢討比，同樣的錯就告訴他第幾次、上次的下次寫什麼（只告訴，不擋）
        same = [r for r in self.reviews if similar_review(line, r)]
        if self.small:
            self.small["檢討"] += 1
        self.reviews.append(datetime.now().strftime("%H:%M") + " " + line)
        self._append("檢討.md", line + (f"｜（重複第 {len(same) + 1} 次）" if same else ""))
        ret = ("檢討記下了（不放面板，查準則 查得到）：" + line + NL
               + "→ 這個教訓之後還會用到，就做成工具（新工具）或留一條準則：記準則(對象,準則)（使用者交代）")
        if same:
            m = re.search(r"下次:(.*)$", same[0])
            ret += (NL + f"⚠ 這個錯在這個大任務已經檢討過 {len(same)} 次（第一次 {same[0][:5]}），那次 下次 寫「{m.group(1) if m else '？'}」。"
                    "那次的下次為什麼沒擋住這次？這次的 下次 要寫一個不一樣、做得到的做法")
        return ret

    # ---- 大任務（2026-10-15 使用者：每次導入的只有前面那些加上目前的大任務，執行完再清空接下一個）
    big = None            # {"名稱","成功條件","開始輪"}
    big_queue = []
    clear_flag = False    # 主迴圈看到就把這個任務期間的紀錄清掉
    # 10/21 使用者：「他每次的目標都是小任務，而不是大任務，大任務只是一個段落（例如大任務叫做飯，小任務就會是買菜切菜…）；
    # 他只需要記得前面的小任務完成了什麼跟這個任務要怎麼做，以及大任務的目標是什麼，不需要記得前面的詳細的細節」
    # 「大任務檢討 他要先確認他小任務是否一次內做對」
    small = None          # 現在的小任務 {"名稱","怎麼做","開始","輪","✗","檢討"}（＋"上次更新","問題","查過的準則"）
    no_small = 0          # 沒設小任務的輪數
    SMALL_EVERY = 15      # 10/21 18:5x 使用者「定期更新小任務，寫出他遇到的問題並提早檢討」
    small_done = []       # 這個大任務裡完成的小任務
    pending_big = None    # 送了一次、等確認的大任務完成

    def flow_small_set(self, 名稱="", 怎麼做=""):
        name, how = str(名稱).strip(), str(怎麼做).strip()
        if not name:
            return "設小任務 要給 名稱,怎麼做，例：設小任務(買菜,走到市場長按E拿菜)"
        note = ""
        if self.small and self.small["名稱"] != name:
            note = (f"（原本的小任務「{self.small['名稱']}」還沒記完成就換掉了，做了 {self.small['輪']} 輪；"
                    f"它真的做完的話，之後回顧送 任務完成({self.small['名稱']},回顧:…) 照樣算到它）")
            self._append("小任務.md", f"換掉:{self.small['名稱']}｜{self.small['輪']} 輪、✗{self.small['✗']}、檢討{self.small['檢討']}")
            # 10/21 05:4x：他常先 設小任務(下一件) 再回顧送上一件的 任務完成 → 換掉的留著，之後送完成時算回它
            self.small_prev = dict(self.small, 換掉=time.time())
        if self.small and self.small["名稱"] == name:
            self.small["怎麼做"] = how or self.small["怎麼做"]
            return f"現在的小任務還是「{name}」，怎麼做更新成：{self.small['怎麼做'] or '（沒寫）'}"
        self.small = {"名稱": name, "怎麼做": how, "開始": datetime.now().strftime("%H:%M"), "輪": 0, "✗": 0, "檢討": 0, "上次更新": 0}
        self.no_small = 0
        self._append("小任務.md", f"開始:{name}｜怎麼做:{how}｜大任務:{(self.big or {}).get('名稱', '沒設')}")
        return (note + f"現在的小任務＝「{name}」（怎麼做：{how or '沒寫，想好就 設小任務(' + name + ',怎麼做) 補上'}）；"
                "每一輪的目標就是它，做完送 任務完成")

    def _small_finish(self, name, review, three):
        def _hit(x):
            return bool(x) and (not name or name in x["名稱"] or x["名稱"] in name)
        prev = getattr(self, "small_prev", None)
        if _hit(self.small):
            sm, from_prev = self.small, False
        elif prev and time.time() - prev.get("換掉", 0) <= 1800 and _hit(prev):
            sm, from_prev = prev, True
        else:
            sm, from_prev = None, False
        e = {"名稱": (sm or {}).get("名稱") or name, "怎麼做": (sm or {}).get("怎麼做", ""),
             "做法": " → ".join(str(x["做"]) for x in three)[:160], "回顧": review[:120],
             "輪": (sm or {}).get("輪", 0), "✗": (sm or {}).get("✗", 0), "檢討": (sm or {}).get("檢討", 0),
             "設過": bool(sm), "時間": datetime.now().strftime("%H:%M")}
        e["一次做對"] = bool(sm) and e["✗"] == 0 and e["檢討"] == 0
        self.small_done.append(e)
        if sm and from_prev:
            self.small_prev = None
            self._append("小任務.md", f"完成:{e['名稱']}｜{e['輪']} 輪、✗{e['✗']}、檢討{e['檢討']}｜做法:{e['做法']}")
            return (f"小任務「{e['名稱']}」完成：{e['輪']} 輪、✗ {e['✗']} 步、檢討 {e['檢討']} 條"
                    f"（程式看{'是一次就做對' if e['一次做對'] else '不是一次就做對'}）。現在的小任務還是「{(self.small or {}).get('名稱', '沒設')}」")
        if sm:
            self.small = None
        self._append("小任務.md", f"完成:{e['名稱']}｜{e['輪']} 輪、✗{e['✗']}、檢討{e['檢討']}｜做法:{e['做法']}")
        if not sm:
            return f"（這件沒先 設小任務，程式沒數輪數；下一件先 設小任務(名稱,怎麼做)）"
        return (f"小任務「{e['名稱']}」完成：{e['輪']} 輪、✗ {e['✗']} 步、檢討 {e['檢討']} 條"
                f"（程式看{'是一次就做對' if e['一次做對'] else '不是一次就做對'}）。接著 設小任務(下一件,怎麼做)")

    def small_lines(self):
        if not self.small_done:
            return "（這個大任務還沒有完成的小任務）"
        return NL.join(f"{i + 1}. {e['名稱']}｜完成了：{e.get('回顧', '')[:60] or '？'}｜怎麼做的：{(e['做法'] or e['怎麼做'] or '？')[:80]}"
                       + (f"｜{e['輪']} 輪、✗{e['✗']}、檢討{e['檢討']}" if e.get("設過") else "｜（沒先設小任務）")
                       for i, e in enumerate(self.small_done[-12:]))

    def small_now_text(self):
        sm = self.small
        if not sm:
            return "（還沒設。用 設小任務(名稱,怎麼做) 定現在要做的那一件，例：大任務 做飯 → 設小任務(買菜,走到市場長按E拿菜)）"
        return (f"「{sm['名稱']}」｜怎麼做：{sm['怎麼做'] or '（沒寫）'}｜{sm['開始']} 起、第 {sm['輪']} 輪"
                + (f"、到現在 ✗ {sm['✗']} 步、檢討 {sm['檢討']} 條" if sm["✗"] or sm["檢討"] else "")
                + (f"｜你 {sm.get('更新時間', '')} 寫的遇到的問題：{sm['問題']}" if sm.get("問題") else "")
                + ("" if NO_SMALL_UPDATE else f"｜下次小任務更新：第 {sm.get('上次更新', 0) + self.SMALL_EVERY} 輪"))

    # 10/21 19:xx 使用者：「如果出現了變動就要重新思考一次任務要是什麼（例如發生了死亡、特殊事件、文字變化或者之類的）」
    DEATH_RE = r"你死了|死掉|陣亡|再玩一次|復活|died|game ?over|wasted|defeat"
    pending_events = []
    last_bright = None

    def change_events(self, task_ev, history, im):
        """這一輪開頭看得出來的變動（任何遊戲都通用的訊號）。"""
        ev = []
        for e in ([task_ev] if isinstance(task_ev, dict) else list(task_ev or [])):
            if isinstance(e, dict):
                ev.append(f"文字變化：右上角任務字「{e.get('舊', '')}」→「{e.get('新', '')}」")
        s1 = str(history[-1].get("看到", "")) if history else ""
        s0 = str(history[-2].get("看到", "")) if len(history) > 1 else ""
        if re.search(self.DEATH_RE, s1, re.I) and not re.search(self.DEATH_RE, s0, re.I):
            ev.append("死亡：你上一輪看到死亡／結束畫面")
        if history and any(str(x).startswith("突發狀況") for x in history[-1].get("用") or []):
            ev.append("特殊事件：你上一輪叫了 突發狀況")
        if im is not None:
            try:
                import numpy as np
                b = float(np.asarray(im.convert("L").resize((64, 36)), dtype=float).mean())
            except Exception:  # noqa: BLE001
                b = None
            if b is not None and self.last_bright is not None and self.last_bright < 20 <= b:
                ev.append("換場景：畫面從全黑／載入回來（可能換地方、重新開局）")
            self.last_bright = b
        if self.desc_changed:
            ev.append("畫面變了：" + self.desc_changed)
            self.desc_changed = ""
        return ev

    def small_update_reason(self, events):
        for e in events:
            if e not in self.pending_events:
                self.pending_events.append(e)
        sm = self.small
        since = (sm["輪"] - sm.get("上次更新", 0)) if sm else None
        if self.pending_events and (since is None or since >= 2):     # 連續兩輪都有變動（死→載入→新局）就合在一起問
            r = "；".join(self.pending_events)
            self.pending_events = []
            return r
        if not sm:
            return f"已經 {self.no_small} 輪沒設小任務" if self.no_small >= 10 and self.no_small % 10 == 0 else ""
        if since >= self.SMALL_EVERY:
            return f"這件小任務又做了 {since} 輪（定期更新）"
        return ""

    def small_update(self, reason, image, history, task_words):
        """10/21 18:5x 使用者：「定期更新小任務，它寫出來他遇到的問題並也提早檢討」→ 程式請他停下來寫。"""
        sm = self.small
        n = min(self.SMALL_EVERY, (sm["輪"] - sm.get("上次更新", 0)) if sm else self.no_small) or 5
        recent = NL.join(f"{h.get('時間', '')[:5]} 看到:{str(h.get('看到', ''))[:50]}｜用:{'、'.join(h.get('用') or [])[:90]}｜實際:{str(h.get('實際', ''))[:40]}｜符合:{h.get('符合')}"
                         for h in history[-n:]) or "（沒有）"
        revs = NL.join(self.reviews[-min(8, (sm or {}).get("檢討", 0)):]) if sm and sm.get("檢討") else "（這件還沒寫檢討）"
        by = {self._rule_key(r): r for r in self._rules_load()}
        used = [by[k] for k in (sm or {}).get("查過的準則", []) if k in by]
        rules_txt = NL.join(f"- {self._rule_key(r)}{self._rule_reading(r)}" for r in used) or "（這件小任務沒查過準則）"
        big = self.big or {}
        desc = ""
        if image is not None and "定期更新" not in reason:           # 出現變動：先給畫面描述（任何遊戲都通用）
            desc = self.see_describe(image=image)
            self.desc_changed = ""
        user = ("【為什麼現在要重新想】" + reason + NL
                + ((desc + NL) if desc else "")
                + f"【大任務】{big.get('名稱', '沒設')}｜目標：{big.get('成功條件', '') or '沒寫'}" + NL
                + "【前面完成的小任務】" + NL + self.small_lines() + NL
                + "【現在的小任務】" + self.small_now_text() + NL
                + "【右上角任務字（程式讀的，可能讀錯）】" + ("、".join(str(w[0]) if isinstance(w, (list, tuple)) else str(w) for w in task_words) if task_words else "（沒讀到）") + NL
                + f"【這件小任務最近 {n} 輪】" + NL + recent + NL
                + "【這件小任務寫的檢討（最近 8 條）】" + NL + revs + NL
                + "【這件小任務查過的準則，查出來後照做的結果】" + NL + rules_txt + NL * 2
                + "看現在的畫面（和畫面描述）與上面的數字：先重新想一次現在的任務應該是什麼，再提早檢討。只輸出 JSON：" + NL
                + '{"發生了什麼變動":"","現在的任務應該是什麼":"看畫面上的任務字、剛發生的事重新想","根據":"畫面上的哪個字／哪個東西",'
                  '"遇到的問題":[{"問題":"","幾次":"","為什麼":""}],'
                  '"現在的做法有沒有用":"用上面的 ✗ 和 ✓／✗ 數字回答",'
                  '"準則檢查":[{"準則":"對象｜準則（照上面原文）","照做結果":"","要不要更正":"不用／更正:新的做法"}],'
                  '"小任務":{"名稱":"還是同一件就寫一樣的名稱；做完了或該換就寫新的","怎麼做":"接下來怎麼做（做法沒用就寫不一樣的）"},'
                  '"接下來先做":"一句，用工具名"}')
        system = (self._identity() + NL + "現在是重新想任務／小任務更新（使用者交代）：遊戲先停一下。出現變動就重新想一次任務應該是什麼；"
                  "再寫出你遇到的問題、提早檢討，不要等卡一百輪才換做法。"
                  "只寫紀錄和畫面裡真的有的事。")
        t0 = time.time()
        try:
            o, raw = self._ask(system, user, effort="中想", max_tokens=8000, deadline=240, image=image)
        except Exception as e:  # noqa: BLE001
            o, raw = {}, str(e)
        dt = time.time() - t0
        if sm:
            sm["上次更新"] = sm["輪"]
        else:
            self.no_small += 1                    # 沒解析出來也不要下一輪又叫
        if not o:
            self._append("小任務.md", f"更新失敗:{reason}｜{dt:.0f} 秒沒解析出來")
            return f"小任務更新（{reason}）沒解析出來，下次再寫"
        probs = [p for p in (o.get("遇到的問題") or []) if isinstance(p, dict)][:3]
        ptxt = "；".join(f"{p.get('問題', '')}（{p.get('幾次', '')}，{p.get('為什麼', '')}）" for p in probs)[:240]
        useful = str(o.get("現在的做法有沒有用") or "")[:120]
        task_now = str(o.get("現在的任務應該是什麼") or "")[:80]
        basis = str(o.get("根據") or "")[:60]
        nxt = str(o.get("接下來先做") or "")[:80]
        out = []
        for c in (o.get("準則檢查") or [])[:5]:
            if isinstance(c, dict) and str(c.get("要不要更正", "")).strip().startswith("更正"):
                obj = str(c.get("準則", "")).split("｜")[0].strip()
                if obj:
                    out.append(self.mem_rule(obj, str(c["要不要更正"]).strip()))
        st = o.get("小任務") if isinstance(o.get("小任務"), dict) else {}
        name, how = str(st.get("名稱") or "").strip(), str(st.get("怎麼做") or "").strip()
        if name and (not self.small or name != self.small["名稱"]):
            out.append(self.flow_small_set(name, how))
        elif self.small and how:
            self.small["怎麼做"] = how
        if self.small:
            self.small["問題"] = ptxt
            self.small["更新時間"] = datetime.now().strftime("%H:%M")
            if not (name and name != (sm or {}).get("名稱")):
                self.small["上次更新"] = self.small["輪"]
        cur = self.small or {}
        self._append("小任務.md", f"更新:{cur.get('名稱', '沒設')}｜第 {cur.get('輪', 0)} 輪、✗{cur.get('✗', 0)}、檢討{cur.get('檢討', 0)}"
                     f"｜為什麼更新:{reason}｜任務應該是:{task_now}（根據:{basis}）｜問題:{ptxt}｜做法有沒有用:{useful}"
                     f"｜怎麼做:{cur.get('怎麼做', '')}｜接下來:{nxt}")
        print(f"   📝 小任務更新（{reason}）{dt:.0f}s｜問題:{ptxt[:80]}｜接下來:{nxt[:40]}")
        return (f"（{reason}，你花 {dt:.0f} 秒寫的）現在的任務應該是：{task_now or '沒寫'}（根據：{basis or '沒寫'}）"
                f"｜遇到的問題：{ptxt or '沒寫'}｜做法有沒有用：{useful or '沒寫'}"
                f"｜現在的小任務：「{cur.get('名稱', '沒設')}」怎麼做：{cur.get('怎麼做', '') or '沒寫'}｜接下來先做：{nxt or '沒寫'}"
                + ((NL + NL.join(out)) if out else ""))

    def flow_big_set(self, 名稱="", 成功條件=""):
        if not str(名稱).strip():
            return "要給名稱"
        if "→" in str(名稱):              # 排大任務 併進來：一次給好幾件，第一件是現在的、其他排隊
            names = [x.strip() for x in str(名稱).split("→") if x.strip()]
            r = self.flow_big_set(names[0], 成功條件)
            self.big_queue = names[1:]
            return r + "；之後排隊：" + " → ".join(self.big_queue)
        if self.big and self.big["名稱"] != str(名稱).strip():
            self.small, self.small_done, self.pending_big = None, [], None
        self.big = {"名稱": str(名稱).strip(), "成功條件": str(成功條件).strip(), "開始": datetime.now().strftime("%H:%M")}
        self._append("大任務.md", f"開始:{self.big['名稱']}｜成功條件:{self.big['成功條件']}")
        return f"目前的大任務＝「{self.big['名稱']}」（成功條件：{self.big['成功條件'] or '沒寫'}）"

    def flow_big_queue(self, 名稱=""):
        names = [x.strip() for x in re.split(r"[,，、→]", str(名稱)) if x.strip()]
        self.big_queue += names
        return "排好了：" + " → ".join(self.big_queue)

    def flow_big_done(self, 結果="", 確認=""):
        name = self.big["名稱"] if self.big else "（沒設大任務）"
        ck = re.sub(r"^\s*確認\s*[:：]\s*", "", str(確認 or "")).strip()
        m = re.search(r"確認\s*[:：]\s*(.*)$", str(結果))
        if not ck and m:
            ck, 結果 = m.group(1).strip(), str(結果)[:m.start()].rstrip("，, ")
        words = re.sub(r"[\d\s.,，、。:：;；|｜()（）\[\]\-+_/%✓✗]+", "", ck)
        p = self.pending_big
        if not (p and time.time() - p <= 900 and len(words) >= 6):
            self.pending_big = time.time()
            return ((("確認要寫字（每個小任務一次做對沒有、沒做對的為什麼），這次不算；" if ck else "") if p else "")
                    + f"還沒算 大任務完成。先逐一確認「{name}」裡每個小任務是不是一次就做對：" + NL + self.small_lines() + NL
                    + "→ 一次做對的寫 ✓；不是一次做對的寫為什麼、下次怎麼一次做對。確定再送 大任務完成(" + (str(結果) or "結果")
                    + ",確認:買菜✓；切菜✗切了三次才對，下次先看E框再按)")
        self.pending_big = None
        self._append("大任務.md", f"完成:{name}｜結果:{結果}｜小任務 {len(self.small_done)} 件、一次做對 "
                     f"{sum(1 for e in self.small_done if e['一次做對'])}｜確認:{ck}")
        self.clear_flag = True
        self.reviews = []
        self.small, self.small_done = None, []
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
        m = re.fullmatch(r"\s*(?:最近)?\s*(\d+)\s*輪?\s*", str(做法))
        if m:                               # 錄成程序 併進來：做法寫「最近3輪」＝把剛才幾輪真的做過的動作錄起來
            return self.proc_record(名稱, 用途, m.group(1))
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
        keys = list(t["參數"].keys())
        kw = {k: v for k, v in params.items() if k in keys}
        if keys and not kw and params:           # 12:5x 他用英文鍵名 {"labels": "door"}：鍵名對不上就照順序塞
            kw = {k: v for k, v in zip(keys, params.values())}
        try:
            import inspect
            try:
                inspect.signature(fn).bind(**kw)
            except TypeError:                     # 10/25 審查：原本工具裡任何 TypeError 都會無參數再叫一次 → 有副作用的工具做兩次；改成只在參數對不上時才不帶參數
                kw = {}
            return str(fn(**kw))
        except Exception as e:  # noqa: BLE001
            return f"「{name}」執行失敗：{e}"


# ---------------------------------------------------------------- 主迴圈
def _acted_last(history, n=2):
    """最近 n 輪有沒有叫互動類工具（長按／按／點／點字／確認點）→ 有就回那個工具名、沒有回 ""。沒有 history 回 "？"（第一輪不提醒）。
    10/25 22:2x：長按 E 後遊戲要 1～2 輪才換任務字（22:17 長按、22:18 換字），只看上一輪會誤標「不是你做的」→ 看最近兩輪。"""
    if not history:
        return "？"
    for h in reversed(history[-n:]):
        for u in (h.get("用") or []):
            name = (u if isinstance(u, str) else str(u.get("工具") or u.get("做") or "")).split("(")[0].strip()
            if name in ("長按", "按", "點", "點字", "確認點", "按住", "雙擊"):
                return name
    return ""


def json_from_reason(reason):
    """10/25 09:2x 第 17 輪：大腦把整段 JSON 寫在推理裡、答案欄空白（這版 2/31 輪變空輪）→ 從推理尾端往前找最後一個「{"看到"」開頭、能解析的物件。"""
    from play_agent import parse_json_loose
    text = str(reason or "")
    if not text:
        return {}
    import re
    starts = [m.start() for m in re.finditer(r'\{\s*"看到"', text)]

    def first_obj(i):                      # 從 i 開始配對大括號（跳過字串裡的括號）→ 第一個完整物件的字串
        depth, in_str, esc = 0, False, False
        for j in range(i, len(text)):
            c = text[j]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return text[i:j + 1]
        return text[i:]
    for i in reversed(starts[-4:]):
        for cand in (first_obj(i), text[i:]):
            try:
                o = parse_json_loose(repair_json(cand)) or {}
            except Exception:  # noqa: BLE001
                o = {}
            if isinstance(o, dict) and o.get("看到") is not None:
                return o
    return {}


def repair_json(raw):
    """他兩種常見的壞 JSON（01:3x 統計 11/120 輪）：
    1. 「想」欄寫成 …｜結果應該是": "畫面會變…"  → 引號提早關掉；把 結果應該是": " 改回 結果應該是:
    2. 一輪吐兩個 JSON 物件 → 取最後一個完整的。
    3. 10/16 01:0x（3/42 輪）：整個物件寫完只少最後的 } 或 ] → 數括號（略過字串內）補上。
    4. 10/16 02:2x：結尾多一個 }（Extra data）→ 取第一個完整物件；結尾多了 "（]"}）→ 從尾巴退幾個字再補括號。"""
    s = str(raw or "")
    # 10/24 14:3x：Claude 常把 JSON 包在 ```json … ``` 裡 → 先拿掉，不然合法 JSON 會被下面的改寫弄壞（Haiku、Sonnet、Opus 都有輪因此 無法解析）
    s = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", s)
    try:                                   # 10/16 12:0x：本來就合法的 JSON 不動（他把「結果應該是」寫成獨立欄位時，下面的改寫會把它弄壞）
        if isinstance(json.loads(s.strip()), dict):
            return s
    except json.JSONDecodeError:
        pass
    s = re.sub(r'｜結果應該是"\s*:\s*"', "｜結果應該是:", s)   # 10/25 審查：只管「想」欄裡的，別把當鍵用的 "結果應該是":"…" 改壞
    # 10/24 21:0x：Sonnet 寫成 {"做":"…","結果應該是:鏡頭…"}（冒號跑進字串、少了值）→ 補回 "結果應該是":"…"
    s = s.replace(',"結果應該是:', ',"結果應該是":"')   # 只有鍵前面才會是 ,"（想 欄裡的「｜結果應該是:」不會被改）
    objs = re.findall(r"\{.*?\}(?=\s*\{|\s*$)", s, flags=re.S)
    if len(objs) > 1:
        for cand in reversed(objs):
            try:
                json.loads(cand)
                return cand
            except json.JSONDecodeError:
                continue
    try:
        json.loads(s.strip())
        return s
    except json.JSONDecodeError:
        pass
    body = s.strip()
    start = body.find("{")
    if start >= 0:
        try:                                              # 結尾多餘的 } 或字：取第一個完整物件
            obj, _ = json.JSONDecoder().raw_decode(body[start:])
            if isinstance(obj, dict):
                return json.dumps(obj, ensure_ascii=False)
        except json.JSONDecodeError:
            pass
        for cut in range(0, 6):                           # 尾巴退 0～5 字再補括號
            cand = _close_brackets(body[start:len(body) - cut] if cut else body[start:])
            if cand:
                return cand
    return s


def _close_brackets(frag):
    """數括號（略過字串內）補上缺的結尾；補完能解析就回傳，否則 None。"""
    stack, in_str, esc = [], False, False
    for ch in frag:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]" and stack:
            stack.pop()
    if not in_str:
        cand = frag + "".join(reversed(stack))
        try:
            if isinstance(json.loads(cand), dict):
                return cand
        except json.JSONDecodeError:
            pass
    return None


TASK_HUD = ("輪班", "理智", "保安", "道具", "金幣", "商店", "邀請", "職業", "日誌", "啟用", "重生", "繼續", "速度", "好友", "最高", "咖啡師", "外科", "醫生", "電擊", "震驚", "可疑", "攝影機", "傳送",
            # 10/19 02:42 誤判：長按 E 開了職業面板，右側說明「（等級 3）開始時配備電擊槍和可部署攝影機…」被當成任務字換了 → 面板／選單用字
            "護士", "護十", "秘書", "實習生", "心理學家", "醫護", "外觀", "裝備", "等級", "配備", "恢復", "次數", "攝影機", "伺服器", "設定", "協助", "帳號",
            # 10/20 04:16 誤判：大廳右側看板「下一次更新」被當成任務字換了
            "更新",
            # 10/20 04:32 誤判：右側道具 HUD「火焰範圍 : 10」被當成任務字
            "火焰", "範圍",
            # 10/20 07:59 誤判：咖啡機的「咖啡 : 準備好了」字（y 0.36）疊在任務框下面被當成換字
            "咖啡", "準備好",
            # 10/20 09:09 誤判：大廳 HUD「最高轉移速度」OCR 讀成「轉移速广」
            "轉移",
            # 10/20 19:51 誤判：大廳「角色扮演」招牌（讀成「色扮演」）被當成任務字
            "扮演")


def top_right_words(im):
    """10/19 任務字整併：只裁畫面右緣任務列（x 0.78～1、y 0.06～0.48）放大兩倍再 OCR（10/18 整張讀多是海報／招牌亂碼）；
    去掉 HUD、純英文招牌、少於 3 個中文字的。回 [(字, x, y)]，座標換回整張畫面比例、由上往下排。"""
    try:
        import local_text
        from PIL import Image
        W, H = im.size
        # 右緣任務框（10/18 真的任務字 y 0.14～0.37）＋ 10/19 03:1x 加上方橫帶（「檢查病人的情況」10/18 讀在 0.64,0.08，輪班 旁邊）
        res = []
        for fx0, fy0, fx1, fy1 in ((0.78, 0.06, 1.0, 0.40), (0.30, 0.03, 0.78, 0.14)):
            x0, y0 = int(W * fx0), int(H * fy0)
            crop = im.crop((x0, y0, int(W * fx1), int(H * fy1)))
            crop = crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS)
            res += [(r, x0, y0) for r in (local_text._win_read_once(crop) or [])]   # 小裁切讀一次就好（不切四塊）
    except Exception:  # noqa: BLE001
        return []
    out = []
    for r, x0, y0 in res:
        try:
            box, txt = r[0], str(r[1]).strip()
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            cx, cy = (x0 + (min(xs) + max(xs)) / 4) / W, (y0 + (min(ys) + max(ys)) / 4) / H
            han = len(re.findall(r"[\u4e00-\u9fff]", txt))
            # 任務字是短句（在電腦上註冊／蓋印表格／檢查病人的情況）；超過 12 個中文字或帶逗號句號的是面板說明
            # 14:1x：任務字可以只有兩個字（「拍照」）——原本 ≥3 把它濾掉，任務換了面板沒標
            if 2 <= han <= 12 and not re.search(r"[，。,]", txt) and not any(h in txt for h in TASK_HUD):
                out.append([txt, round(cx, 2), round(cy, 2)])
        except Exception:  # noqa: BLE001
            continue
    out.sort(key=lambda w: w[2])
    return out[:8]


class TaskWords:
    """10/19 任務字進工作流程：連兩輪都讀到、而且前面幾輪沒出現過的字 → 任務字換了。只給讀數，不替他判斷完成。"""

    def __init__(self):
        self.hist, self.current, self.events, self.last_text, self.prev_text = [], [], [], "", ""
        self.miss = 0          # 連續幾輪右上任務框一個字都沒讀到

    @staticmethod
    def norm(t):
        return "".join(re.findall(r"[\u4e00-\u9fff]", str(t)))

    @staticmethod
    def same(a, b):
        import difflib
        r = difflib.SequenceMatcher(None, a, b).ratio()
        if min(len(a), len(b)) <= 3:          # 14:4x：短字 OCR 抖動（拍照／拍昭）被當成換字兩次 → 短字差一個字、長度一樣也算同一個
            return a == b or a in b or b in a or (len(a) == len(b) and r >= 0.5)
        return a in b or b in a or r >= 0.6

    def update(self, words):
        """回 (這輪讀到的字串, 這輪的換字事件或 None)"""
        # 10/19 07:3x：上方橫帶讀到伺服器／玩家名稱（「索利克斯胡巴咍托 2」）被當成換字 → 換字事件只認右緣任務框（x≥0.78），上方橫帶只顯示
        # 10/26 18:3x 第 48 輪：大廳招牌「頂尖輪／你的頂尖輪」（y 0.39）連兩輪讀到被當成「拍照→你的頂尖輪」換字
        #   → 換字事件只認任務框那一帶（y ≤ 0.37；10/18～10/26 真的任務字都在 0.14～0.37），再下面的字只顯示
        cur = [self.norm(w[0]) for w in words if len(self.norm(w[0])) >= 2 and w[1] >= 0.78 and w[2] <= 0.37]
        self.miss = 0 if cur else self.miss + 1
        # 10/20 04:3x：遊戲自己連跳「完成簽到→跟隨病人到他的房間→從房間3中的病人身上採集樣本」，每個只讀到一輪、要連兩輪才算 → 全漏掉。
        # 4 個中文字以上、正好在任務框那一格（x 0.88～0.94、y 0.17～0.25；10/19～20 真的任務字都在這裡，對話泡泡、道具 HUD 不在）的字讀到一次就算
        sure = {self.norm(w[0]) for w in words if len(self.norm(w[0])) >= 4 and 0.88 <= w[1] <= 0.94 and 0.17 <= w[2] <= 0.25}
        text = "、".join(w[0] for w in words) or "（沒讀到）"
        ev = None
        if self.hist or sure:
            last = self.hist[-1] if self.hist else []
            confirmed = [w for w in cur if w in sure or any(self.same(w, x) for x in last)]
            # 14:2x：原本也跟「最近讀過但沒確認」的字比，14:17 讀過一次「拍照」→ 14:20 確認時被當舊字、換字事件漏掉；只跟目前認定的比
            new = [w for w in confirmed if not any(self.same(w, p) for p in self.current)]
            if new and (self.current or self.events):
                ev = {"時間": datetime.now().strftime("%H:%M"), "舊": "、".join(self.current) or "（沒有）", "新": "、".join(new)}
                self.events.append(ev)
            # 兩行的任務字只讀到其中一行時不縮小（10/19「從房間3中的病人／身上採集樣本」一行一行輪流讀到被當成換字）
            if new:
                self.current = confirmed
            elif confirmed:
                self.current = self.current + [w for w in confirmed if not any(self.same(w, p) for p in self.current)]
        self.hist.append(cur)
        self.hist = self.hist[-10:]
        self.prev_text, self.last_text = self.last_text, text
        return text, ev

    def panel(self, ev, big, acted=True):
        # 10/20 使用者：面板直接標出右上角那塊是 HUD 任務框（他有時寫成牆上的字、機器）
        line = ("【遊戲任務字】右上角那塊是 HUD 任務框：釘在畫面上的介面字，不是場景裡的牆、海報或機器——轉頭不會動、走不過去、不用對正"
                "（不確定哪裡是 UI 就叫 確認是否是UI）。"
                f"（程式每輪裁右上角任務列放大讀，可能讀錯；只給讀數）現在：「{self.last_text}」｜上一輪：「{self.prev_text or '（沒讀）'}」"
                + (f"｜目前認定的任務字：已經 {self.miss} 輪讀不到右上角任務框（可能不在局內——大廳沒有任務框——或被擋住）；上次讀到的是「{'、'.join(self.current) or '（沒有）'}」"
                   if self.miss >= 4 else f"｜目前認定的任務字：「{'、'.join(self.current) or '（還沒有）'}」"))
        # 10/20 08:3x～09:2x：死後在大廳 55 分鐘，面板一直寫「目前認定的任務字：蓋印表格」，他在大廳找「E框印表機」「蓋印表格海報」（大廳沒有）
        # → 連 4 輪讀不到任務框就照實說讀不到（只給讀數）
        if ev:
            line += (NL + f"⚑ 任務字換了：「{ev['舊']}」→「{ev['新']}」（{ev['時間']}）——換了可能＝上一件做完了；"
                     "要不要送 任務完成（照規則先回顧三步）你自己決定")
            if acted == "":
                # 10/25 21:3x 第 22 輪：他進局 4 分鐘沒按任何東西，任務字自己連跳 蓋印表格→完成簽到→採樣→治療病人（同一局別的玩家做的），
                #   他照樣送 任務完成／大任務完成、把「拍照✓註冊✓簽到✓」記成自己做完 → 只給讀數：最近兩輪沒互動就照實標出
                line += (NL + "⚠ 但最近兩輪你沒有 長按／按／點 任何東西，任務字是自己換的——多半是同一局別的玩家做完了、或遊戲自動推進，"
                         "不是你做的；送 任務完成 前先想這一點")
            elif acted not in ("？", True):
                # 10/25 22:2x：長按 E 當下畫面看不出反應、1～2 輪後任務字才換 → 他記成「按 E 沒反應不能互動」。照實標：這多半就是那次按的結果
                line += (NL + f"⚑ 最近兩輪你叫過 {acted}，任務字接著換了＝那次多半有反應、只是遊戲反應慢（按完當下畫面看不出）；"
                         "別把它記成「按了沒反應」")
            if big and re.search(r"任務字|右上|任務列|下一件", str(big.get("成功條件", ""))):
                line += NL + f"⚑ 你的大任務「{big['名稱']}」成功條件寫到右上任務字（{big['成功條件']}）——要不要送 大任務完成 你自己決定"
        elif self.events:
            e = self.events[-1]
            line += f"｜上次換：{e['時間']}「{e['舊']}」→「{e['新']}」"
        return line


# ---- 10/19 使用者「記憶改成大任務完成前都記住」：大任務、這個任務的輪次紀錄與檢討存檔，程式重開也接著留，大任務完成才清
def _fz(m):
    if isinstance(m, list):
        return "".join("✓" if x else "✗" for x in m)
    return {True: "✓", False: "✗"}.get(m, "")


def history_text(history, full_n=8, budget=9000):
    if not history:
        return "（剛開始）"
    from collections import Counter as _C
    full, old = history[-full_n:], history[:-full_n]

    def compact(h):
        names = [str(d).split("(")[0] for d in h.get("用") or []]
        return (f"[{h['輪']}]{h.get('時間', '')[:5]} 想:{str(h.get('想', ''))[:24]}｜用:{'、'.join(names) or '無'}"
                + (f"｜符合:{_fz(h.get('符合'))}" if h.get("符合") is not None else ""))

    lines = [compact(h) for h in old]
    head, i = [], 0
    while sum(len(x) for x in head + lines[i:]) > budget and i < len(old):
        chunk = old[i:i + 20]
        cnt = _C(str(d).split("(")[0] for h in chunk for d in h.get("用") or [])
        ok = sum(1 for h in chunk if _fz(h.get("符合")) and "✗" not in _fz(h.get("符合")))
        head.append(f"[{chunk[0]['輪']}～{chunk[-1]['輪']} 輪 {chunk[0].get('時間', '')[:5]}～{chunk[-1].get('時間', '')[:5]}] "
                    f"用最多:{'、'.join(f'{k}×{v}' for k, v in cnt.most_common(4))}｜全符合 {ok}/{len(chunk)}")
        i += 20
    out = head + lines[i:]
    # 10/20 使用者：拿掉「看到」——他會照抄前幾輪的畫面描述；「看到」每輪只看這一輪的畫面寫
    out += [f"[{h['輪']}] {h.get('時間', '')[:5]} 想:{str(h.get('想', ''))[:80]}｜用:{'、'.join(h.get('用') or []) or '無'}"
            + (f"｜上一輪實際:{h.get('實際', '')}｜符合:{h.get('符合')}" if h.get("符合") is not None else "") for h in full]
    return NL.join(out)


def _bigrams(s):
    s = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", s)
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _review_core(line):
    m = re.search(r"錯在哪:(.*?)(?:｜|$)", line)
    n = re.search(r"實際:(.*?)(?:｜|$)", line)
    return (m.group(1) if m else line) + (n.group(1) if n else "")


def similar_review(a, b):
    A, B = _bigrams(_review_core(a)), _bigrams(_review_core(b))
    return bool(A and B) and len(A & B) / len(A | B) >= 0.4


def reviews_text(reviews, budget=5000):
    """這個大任務的檢討全部列，同類的錯合併成一條＋次數與時間。"""
    groups = []
    for r in reviews:
        for g in groups:
            if similar_review(r, g["最新"]):
                g["最新"], g["次"] = r, g["次"] + 1
                g["時間"].append(r[:5])
                break
        else:
            groups.append({"最新": r, "次": 1, "時間": [r[:5]]})
    lines = [("- " + g["最新"] + (f"（⚠ 同樣的錯 ×{g['次']}：{'、'.join(g['時間'][-6:])}）" if g["次"] > 1 else "")) for g in groups]
    while sum(len(x) for x in lines) > budget:     # 太長：最舊的只留 錯在哪＋下次
        for k, x in enumerate(lines):
            if not x.startswith("- (舊)"):
                m = re.search(r"錯在哪:(.*?)(?:｜|$)", x)
                n = re.search(r"下次:(.*?)(?:（|$)", x)
                lines[k] = "- (舊)" + (f"錯在哪:{m.group(1)}｜下次:{n.group(1) if n else ''}" if m else x[2:60])
                break
        else:
            break
    return NL.join(lines)


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
    ap.add_argument("--effort", default="快思推理",
                    help="推理強度：快思推理（low）／中想（medium）／慢想（xhigh）。2026-10-15 08:0x 使用者：「拉長他能說的字數、思考強度拉高」")
    # 10/18 使用者：「這次挑戰都用高思考、量測都要二次驗證、他要直接規劃好下三個步驟再直接執行中間不檢查」
    ap.add_argument("--lock-effort", action="store_true", help="思考強度 工具不能改（全程用 --effort）")
    ap.add_argument("--verify2", action="store_true", help="量測二次驗證：每個量測做兩次、回報兩個值與一不一致")
    ap.add_argument("--plan3", action="store_true", help="三步規劃：每輪排下三步，程式一口氣做完才截下一張圖")
    ap.add_argument("--no-small-update", action="store_true",
                    help="10/23 使用者「切換任務的更新先暫時關掉」：不做 出現變動就重新想任務／定期小任務更新（10/22 停 43 次共 105 分鐘沒人操作）")
    ap.add_argument("--steps", type=int, default=0, help="每輪排幾步（10/18 12:5x 使用者「改成 預測兩步驟」→ 2）；0＝看 --plan3")
    # 10/24 使用者：模型比較實驗（haiku／sonnet／sonnet 自由搜尋 各一小時，其他一模一樣）→ claude_brain.py
    ap.add_argument("--brain", default="", help="空＝本地 8888；haiku／sonnet＝每次叫大腦開一個新的 claude -p")
    ap.add_argument("--brain-search", action="store_true", help="--brain 時多給 WebSearch／WebFetch")
    ap.add_argument("--brain-native", action="store_true",
                    help="10/24 使用者「原生的Claude 不要洗掉」：保留 Claude Code 原本 system＋原生工具、整段同一個 session")
    args = ap.parse_args()
    if args.brain:
        import claude_brain
        claude_brain.setup(args.brain, args.brain_search,
                           os.path.join(ROOT, "專案", args.game, "記憶", "工具版", "claude大腦.jsonl"),
                           args.brain + ("+原生" if args.brain_native else "+搜尋" if args.brain_search else ""),
                           native=args.brain_native)
    global NO_SMALL_UPDATE
    NO_SMALL_UPDATE = args.no_small_update
    plan_n = max(0, min(5, args.steps)) or (3 if args.plan3 else 0)
    kn = CN_NUM.get(plan_n, str(plan_n))
    live = bool(args.live) and not args.image
    from play_agent import stream_json, parse_json_loose, LAST_REASONING

    cat = load_catalog(args.game)
    tools = Tools(args.game, cat, live=live, image_path=args.image or None)
    if live:                                              # 10/23 直播網站：每 1.5 秒把正在想／正在做寫進 即時.json
        import threading
        from play_agent import LIVE_STREAM

        def _live_writer():
            path = os.path.join(tools.mdir, "即時.json")
            while True:
                try:
                    st = dict(LIVE_STREAM)
                    snap = {"更新": datetime.now().strftime("%H:%M:%S"), "輪": LIVE["輪"], "階段": LIVE["階段"],
                            "已經秒": round(time.time() - LIVE["開始"], 1) if LIVE["開始"] else 0,
                            "思考強度": st.get("effort", ""),
                            "推理": "".join(st.get("reason") or [])[-6000:] if LIVE["階段"] == "想" else "",
                            "輸出": "".join(st.get("text") or [])[-3000:] if LIVE["階段"] == "想" else "",
                            "做": [{k: v for k, v in x.items()} for x in LIVE["做"]],
                            "視角": {"左右": round(tools.yaw_acc, 1), "上下": round(tools.pitch_acc, 1), "左右從": tools.yaw_zero,
                                     "修水平量到": tools.level_meas, "每度dx": tools.dx_deg, "每度dy": tools.dy_deg}}
                    tmp = path + ".tmp"
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump(snap, f, ensure_ascii=False, default=str)
                    os.replace(tmp, path)
                except Exception:  # noqa: BLE001
                    pass
                time.sleep(1.5)
        threading.Thread(target=_live_writer, daemon=True).start()
    tools.verify2, tools.effort_locked = bool(args.verify2), bool(args.lock_effort)
    tools.effort_fixed = args.effort
    model = ("claude-" + args.brain + ("+原生" if args.brain_native else "+搜尋" if args.brain_search else "")) if args.brain else detect_model(args.endpoint)
    tools.endpoint, tools.model = args.endpoint, model
    system = build_system(args.game, cat, plan_n)
    if live:
        # 10/18 防休眠：10/17 03:05 另開的 keep_awake.ps1 被系統因記憶體不足停掉 → 改由代理自己的主執行緒設定，代理在跑就不休眠、不關螢幕
        try:
            import ctypes
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000003)      # CONTINUOUS|SYSTEM_REQUIRED|DISPLAY_REQUIRED
            print("   防休眠：已設定（代理結束就恢復）")
        except Exception as e:  # noqa: BLE001
            print(f"   防休眠設定失敗：{e}")
    log_path = os.path.join(tools.mdir, datetime.now().strftime("%Y-%m-%d") + ".jsonl")
    print(f"[工具版] 遊戲 {args.game}｜大腦 {model}｜{'LIVE 真的按' if live else 'dry-run 只印'}｜system {len(system)} 字｜工具 {len(cat['工具'])} 個"
          f"｜推理 {args.effort}{'（鎖定）' if args.lock_effort else ''}｜max_tokens {args.max_tokens}｜時限 {args.deadline:.0f}s"
          f"｜二次驗證 {'開' if args.verify2 else '關'}｜規劃 {plan_n or '關'} 步")
    print(f"   紀錄 {log_path}")

    history = []        # 這個大任務期間的輪次紀錄（dict；大任務完成才清空，10/19 起存檔、重開接續）
    tool_returns = ""   # 上一輪工具回傳
    task_round = 0
    tw = TaskWords()
    state_path = os.path.join(tools.mdir, "目前大任務.json")
    if live:
        try:
            st0 = json.load(open(state_path, encoding="utf-8"))
            tools.big, tools.big_queue = st0.get("大任務"), list(st0.get("排大任務") or [])
            history, task_round = list(st0.get("紀錄") or []), int(st0.get("輪數") or 0)
            tools.reviews = list(st0.get("檢討") or [])
            tools.small, tools.small_done = st0.get("小任務"), list(st0.get("完成的小任務") or [])
            tw.current, tw.events = list(st0.get("任務字", {}).get("現在") or []), list(st0.get("任務字", {}).get("換") or [])
            if tools.big or history or tools.reviews:
                tool_returns = (f"（程式重開，接續上次存的：大任務「{(tools.big or {}).get('名稱', '沒設')}」、"
                                f"前面 {len(history)} 輪紀錄、{len(tools.reviews)} 條檢討都還在）")
                print(f"   接續存檔（{st0.get('存檔', '?')}）：大任務「{(tools.big or {}).get('名稱', '沒設')}」、{len(history)} 輪、{len(tools.reviews)} 條檢討")
        except (OSError, ValueError):
            pass

    def save_state():
        try:
            with open(state_path + ".tmp", "w", encoding="utf-8") as f:
                json.dump({"存檔": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "大任務": tools.big, "排大任務": tools.big_queue,
                           "輪數": task_round, "紀錄": history, "檢討": tools.reviews,
                           "小任務": tools.small, "完成的小任務": tools.small_done,
                           "任務字": {"現在": tw.current, "換": tw.events[-20:]}}, f, ensure_ascii=False)
            os.replace(state_path + ".tmp", state_path)
        except OSError as e:
            print(f"   存檔失敗：{e}")
    prev = None         # 上一輪的 原因／預期／做了（12:1x 使用者：三件事＋不符就檢討）
    missed_review = False
    match_stat = {"符合": 0, "不符": 0, "不符沒檢討": 0}
    for rnd in range(1, args.rounds + 1):
        if os.path.exists(os.path.join(ROOT, "stop.txt")):          # 停止：worktree 根目錄放 stop.txt
            print("[stop.txt] 結束")
            break
        cat = tools.cat                                   # 他定義的新工具／補用途會讓清單變
        system = build_system(args.game, cat, plan_n)
        if live:
            fg_ok, fg_title = tools.fg_state()
            if not fg_ok:
                idle = tools._user_idle()
                if not tools._may_steal(fg_title, idle) or not tools.win.activate():
                    why = "是會議視窗、不搶" if tools.NO_STEAL.search(str(fg_title or "")) else f"{idle:.0f} 秒前有人動過"
                    print(f"[{rnd}] 遊戲不在前景（前景是「{fg_title}」，{why}），不按、等 10 秒")
                    time.sleep(10)
                    continue
                print(f"   ⚠ 遊戲不在前景（前景是「{fg_title}」，{idle:.0f} 秒沒人動）→ 已切回遊戲")
        im = tools.grab()
        if im is None:
            print(f"[{rnd}] 抓不到畫面（Roblox 不在前景？），等 3 秒")
            time.sleep(3)
            continue
        task_words = top_right_words(im) if live else []      # 10/19：裁右上任務列放大讀，進面板＋stats
        _, task_ev = tw.update(task_words)
        content = [{"type": "image_url", "image_url": {"url": img_uri(im)}}]
        ask_txt, ask_imgs = tools.claude_panel()          # 10/23 實驗：問Claude 的回答（附圖的前兩輪附上）
        tools.extra_images = list(ask_imgs) + list(tools.extra_images)
        for tag, shot in tools.extra_images:
            content.append({"type": "text", "text": f"（看四周：{tag}）"})
            content.append({"type": "image_url", "image_url": {"url": img_uri(shot, 640)}})
        tools.extra_images = []
        task_round += 1
        if tools.small:
            tools.small["輪"] += 1
        else:
            tools.no_small += 1
        small_up = ""
        up_reason = "" if args.no_small_update else tools.small_update_reason(tools.change_events(task_ev, history, im))
        if up_reason:                                     # 10/21：出現變動就重新想任務＋定期小任務更新
            small_up = tools.small_update(up_reason, im, history, task_words)
        watch_txt = ""
        if tools.patch_watch:                            # 20:xx 追蹤 描述畫面給的點（圖塊比對）
            watch_txt += "【追蹤】" + tools.patch_watch_text(im) + NL
        if tools.watch_labels:                           # 追蹤(標籤)：每輪開頭眼睛找一次
            try:
                watch_txt += "【追蹤】" + tools.see_find(",".join(tools.watch_labels)) + NL
            except Exception as e:  # noqa: BLE001
                watch_txt += f"【追蹤】眼睛出錯：{e}" + NL
        plan_txt = ""
        if tools.plan:
            plan_txt = ("【慢想排的】（你自己 " + tools.plan["時間"] + " 排的）" + "→".join(tools.plan["序列"])
                        + "｜第一步：" + "→".join(tools.plan["細步驟"]) + NL)
        big = tools.big
        big_txt = (f"「{big['名稱']}」｜目標（成功條件）：{big['成功條件'] or '（沒寫）'}｜{big['開始']} 起、第 {task_round} 輪"
                   if big else "（還沒設。先用 設大任務(名稱,成功條件) 定一個，再做事）")
        if prev and prev.get("三步") is not None:
            prev_txt = ("原因：" + prev.get("原因", "？") + NL
                        + NL.join(f"第{i + 1}步 做:{st['做']}｜結果應該是:{st['結果應該是'] or '（你沒寫）'}｜程式回傳:{st['回傳'][:160]}"
                                  for i, st in enumerate(prev["三步"]))
                        + NL + f"→ 看現在的畫面＋{len(prev['三步'])}個回傳，「上一輪實際」寫實際結果、「符合」逐步填 [true/false,…]；有 false 就在 先記 叫 檢討")
        else:
            prev_txt = ("原因：" + prev.get("原因", "？") + "｜預期結果：" + prev.get("預期", "？") + "｜做了：" + prev.get("做了", "無")
                        + NL + "→ 看現在的畫面，在「上一輪實際」寫實際結果、「符合」填 true/false；不符就同一輪叫 檢討") if prev else "（第一輪，沒有上一輪）"
        remind = ("⚠ 上一輪你填「符合:false」卻沒叫 檢討——這一輪先 檢討(原因,預期,實際,錯在哪,下次)" + NL) if missed_review else ""
        last_seen = (history[-1].get("看到", "") if history else "")
        disp_txt = tools.dispatch_panel()
        panel = ((disp_txt + NL * 2) if disp_txt else "") + ((ask_txt + NL * 2) if ask_txt else "") + ("【開工數值】" + tools.startup_text() + NL
                 + "【大任務】（一個段落；目標）" + big_txt + NL
                 + "【前面完成的小任務】" + NL + tools.small_lines() + NL
                 + "【現在的小任務】（這一輪的目標是它）" + tools.small_now_text() + NL
                 + tw.panel(task_ev, big, acted=_acted_last(history)) + NL
                 + tools.persp_text() + NL
                 + ((tools.workflow_text() + NL) if tools.workflow_text() else "")
                 + plan_txt + watch_txt + NL
                 + "【上一輪：原因 → 應有的結果 → 實際？】" + NL + prev_txt + NL + remind + NL
                 + "【工具回傳】（上一輪你叫的工具）" + NL + (tool_returns or "（上一輪沒叫工具）") + NL * 2
                 + ((tools.items_panel(last_seen) + NL) if tools.items_panel(last_seen) else "")
                 + tools.rules_panel(last_seen) + NL * 2
                 + f"（前面每一輪的細節不放面板：這個大任務共 {len(history)} 輪都存在檔案裡，真的要找用 查紀錄(關鍵字)）" + NL * 2
                 + (f"看畫面，規劃接下來{kn}步（程式會一口氣做完、中間不給你看）。只輸出 JSON：" + plan_format(plan_n) if plan_n else
                    "看畫面，決定這一輪要用哪些工具。只輸出 JSON：" + cat["輸出格式"]))
        content.append({"type": "text", "text": panel})
        t0 = time.time()
        LIVE.update({"輪": rnd, "階段": "想", "開始": t0, "做": []})
        raw, trunc, ttft, _ = stream_json(args.endpoint, model, system, content, args.max_tokens,
                                          time.time() + args.deadline, 0.2, effort=(tools.effort_override or args.effort))
        dt = time.time() - t0
        reason = LAST_REASONING.get("text", "")
        LAST_REASONING["text"] = ""
        o = parse_json_loose(repair_json(raw)) or {}
        from_reason = False
        if not (isinstance(o, dict) and (o.get("看到") or o.get("下兩步") or o.get("用"))) and reason:
            o2 = json_from_reason(reason)
            if o2:
                o, from_reason = o2, True
        seen, think = str(o.get("看到") or o.get("s") or "")[:80], str(o.get("想") or "")[:300]
        calls = o.get("用") or o.get("a") or []
        if isinstance(calls, str):
            calls = [calls]
        pre, steps, plan_notes = [], [], []
        if from_reason:
            plan_notes.append("✗ 這輪的 JSON 寫在思考裡、答案欄是空的（程式從思考裡撈出來用了）；JSON 要放在最後的答案，不要放在思考裡")
        if plan_n:
            pre = o.get("先記") or []
            pre = [pre] if isinstance(pre, (str, dict)) else list(pre)
            raw_steps = o.get(f"下{kn}步") or o.get("下兩步") or o.get("下三步") or o.get("下幾步") or []
            if isinstance(raw_steps, (str, dict)):
                raw_steps = [raw_steps]
            for st in raw_steps:
                if isinstance(st, dict) and ("做" in st or "工具" in st):
                    steps.append({"做": st.get("做") if "做" in st else {k: v for k, v in st.items() if k != "結果應該是"},
                                  "結果應該是": str(st.get("結果應該是") or "")[:80]})
                elif st:
                    steps.append({"做": st, "結果應該是": ""})
            if not steps and calls:                       # 他沒照格式、寫了舊的「用」：前 N 個當 N 步
                steps = [{"做": c, "結果應該是": ""} for c in calls[:plan_n]]
                plan_notes.append(f"✗ 這次挑戰要寫「下{kn}步」（每步 做＋結果應該是）；你寫了「用」，程式拿前{kn}個當{kn}步做")
            if len(steps) > plan_n:
                plan_notes.append(f"✗ 你排了 {len(steps)} 步，只做前{kn}步")
                steps = steps[:plan_n]
            elif 0 < len(steps) < plan_n:
                plan_notes.append(f"✗ 你只排了 {len(steps)} 步（這次挑戰每輪排滿{kn}步）")
            calls = [x for x in pre[:2]] + [st["做"] for st in steps]
        actual = str(o.get("上一輪實際") or "")[:80]
        match = o.get("符合")
        if isinstance(match, str):
            match = match.strip().lower() in ("true", "是", "符合", "yes")
        if isinstance(match, list):
            match = [(m if isinstance(m, bool) else str(m).strip().lower() in ("true", "是", "符合", "yes")) for m in match][:5]
            match_all = (all(match) if match else None)
            if prev and prev.get("三步"):                 # 他填的符合寫回上一輪那幾步（小任務完成的回顧要用）
                n_prev = len(prev["三步"])
                for i, m in enumerate(match[:n_prev]):
                    if len(tools.step_log) >= n_prev:
                        tools.step_log[-n_prev + i]["符合"] = m
        else:
            match_all = match
        names_called = [parse_call(x, cat)[0] for x in list(calls)[:5]]
        if prev is not None and match_all is not None:
            if match_all:
                match_stat["符合"] += 1
            else:
                match_stat["不符"] += 1
                if "檢討" not in names_called:
                    match_stat["不符沒檢討"] += 1
        missed_review = (prev is not None and match_all is False and "檢討" not in names_called)
        if prev is not None:                              # 10/21 18:5x：查出來的準則記回 照做✓／✗
            tools.rules_outcome(match_all)
        if prev is not None and tools.small:              # 10/21：小任務一次做對沒有——數 ✗ 步
            tools.small["✗"] += (sum(1 for x in match if x is False) if isinstance(match, list) else int(match_all is False))
        print(f"[{rnd}] {dt:.1f}s 看到「{seen}」" + (f"上一輪實際「{actual}」符合={match} " if prev else "") + f"想「{think}」")
        rets, did = ([f"● 小任務更新 → {small_up}"] if small_up else []) + list(plan_notes), []

        def do(item, tag=""):
            name, params = parse_call(item, cat)
            if not name:
                rets.append(f"✗ {tag}看不懂「{params}」（工具名要照清單）")
                return f"看不懂「{params}」"
            LIVE["階段"] = "做"
            LIVE["做"].append({"工具": f"{tag}{name}", "參數": params, "預計": LIVE.pop("_預計", ""), "回傳": ""})
            r = tools.run(name, params)
            LIVE["做"][-1]["回傳"] = str(r)[:300]
            did.append(name + (f"({json.dumps(params, ensure_ascii=False)})" if params else ""))
            rets.append(f"● {name}{'(' + ','.join(str(v) for v in params.values()) + ')' if params else ''} → {r}")
            print(f"   ▶ {tag}{name} {params} → {r[:120].replace(NL, ' ')}")
            return r

        if plan_n:
            groups = {t["名稱"]: t["類別"] for t in cat["工具"]}
            for item in pre[:2]:
                name = parse_call(item, cat)[0]
                if name and groups.get(name) not in PLAN3_PRE_GROUPS and name not in PLAN3_PRE_TOOLS:
                    rets.append(f"✗ 先記 只放 記錄／檢討／文件／學習類；「{name}」是遊戲動作，要排進 下{kn}步（這次沒做）")
                    continue
                do(item, "先記 ")
            for i, st in enumerate(steps):           # N 步一口氣做完，中間不回大腦
                LIVE["_預計"] = str(st.get("結果應該是") or "")
                st["回傳"] = do(st["做"], f"第{i + 1}步 ")
                tools.step_log.append({"做": st["做"] if isinstance(st["做"], str) else json.dumps(st["做"], ensure_ascii=False),
                                       "結果應該是": st["結果應該是"], "回傳": str(st["回傳"]), "符合": None})
            tools.step_log = tools.step_log[-20:]
        else:
            for item in list(calls)[:5]:
                do(item)
        tool_returns = NL.join(rets)
        tools.remember_round(rnd, seen, think, did, rets)
        history.append({"輪": rnd, "時間": datetime.now().strftime("%H:%M:%S"), "看到": seen, "想": think[:120], "用": did,
                        "實際": actual, "符合": (match if prev else None)})
        m_reason = re.search(r"因為[:：]\s*(.*?)(?:｜|\|)", think)
        m_expect = re.search(r"結果應該是[:：]\s*(.*?)(?:｜|\||$)", think)
        prev = {"原因": (m_reason.group(1) if m_reason else think[:40]), "預期": (m_expect.group(1) if m_expect else "（你沒寫 結果應該是）"),
                "做了": "、".join(did) or "無"}
        if plan_n:
            prev["三步"] = [{"做": (st["做"] if isinstance(st["做"], str) else json.dumps(st["做"], ensure_ascii=False)),
                            "結果應該是": st["結果應該是"], "回傳": str(st.get("回傳", ""))} for st in steps]
        if tools.clear_flag:                              # 大任務完成：這個任務期間的紀錄全部清空
            tools.clear_flag = False
            history, task_round = [], 0
            tool_returns = "（上一個大任務已完成、紀錄與檢討清空）" + NL + tool_returns
            print("   🧹 大任務完成，紀錄清空")
        if live:
            save_state()
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"時間": datetime.now().strftime("%H:%M:%S"), "輪": rnd, "秒": round(dt, 1),
                                "看到": seen, "想": think, "用": calls, "回傳": rets, "原文": raw,
                                "推理": reason, "截斷": trunc, "上一輪實際": actual, "符合": match,
                                "符合統計": dict(match_stat), "右上字": task_words, "任務字讀法": "裁切1019", "任務字換": task_ev,
                                **({"先記": pre[:2], "步數": plan_n,
                                    "下三步": [{"做": st["做"], "結果應該是": st["結果應該是"], "回傳": str(st.get("回傳", ""))[:300]}
                                              for st in steps]} if plan_n else {})},
                               ensure_ascii=False) + NL)
        if not args.image and not live:
            time.sleep(1.0)


if __name__ == "__main__":
    main()
