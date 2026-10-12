#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
flow_agent.py — 工作流程版代理（取代寫死的技能狀態機）

想法（2026-09-07 使用者指示）：
  「取消所有的 skill，改成一個一個的工作流程的文檔，讓他知道要做什麼而已。」

  後來又改（同日）：「取消，讓他參考攻略後自己摸索，不給他總結東西，但是要他去參考攻略影片。」
  所以連步驟都不給了——`工作流程/*.md` 只剩下標籤名字（報到／開始上班／治療／喝咖啡／卡住或死掉），
  讓他知道自己現在在幹哪一件事而已；怎麼做要他自己用 wiki／youtube 動作去查攻略、自己試。

  程式只做四件事：
    1. 給他看畫面（1024 寬、疊座標格線、加上本機偵測器每秒 5 次框出來的青色框）
    2. 給他看標籤清單、他自己寫的筆記、他自己回報的進度
    3. 執行他要的動作（滑鼠一律絕對定位），擋掉任何會花錢的動作
    4. 記錄：進度、筆記、成績、卡住與死亡；每 30 分鐘讓他自己寫一份回報給使用者

用法：
  python flow_agent.py --game 動物醫院 --hours 8
  python flow_agent.py --game 動物醫院 --dry-run
停止：專案夾放 stop.txt / Ctrl+F12 / 滑鼠甩左上角
"""
import argparse
import glob
import json
import math
import os
import re
import sys
import queue
import threading
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from play_agent import (Agent, Tee, HERE, stream_json, parse_json_loose,  # noqa: E402
                        draw_grid, jpeg_data_uri, frame_diff, move_safe, safe_xy,
                        反射, 快思, 中想, 慢想)
import pydirectinput  # noqa: E402
from local_vision import Eyes  # noqa: E402
from local_text import TextEyes  # noqa: E402
import zh_text  # noqa: E402  繁簡／OCR 形近字的容錯比對（2026-09-18）
import builtins  # noqa: E402

# 2026-09-29 使用者：「他需要知道自己上一步按了什麼、為什麼要用這個按鈕、和他原本想要怎麼做」。
# 程式替他按鍵的地方有幾十處（自動 esc、壓平視角、後退、帶路…），每一處動手前都會先印一句原因。
# 這裡記住最近印出的那一句，程式替他動手時就拿它當「為什麼」，寫進他的【上一步】。
_LAST_PRINT = {"line": ""}


def print(*args, **kw):  # noqa: A001
    s = " ".join(str(x) for x in args).strip()
    if s:
        _LAST_PRINT["line"] = s
    builtins.print(*args, **kw)


def log_says_left(raw):
    """Roblox 用戶端 log 的一段（bytes）→ 人是不是已經離開遊戲回到 Roblox 自己的頁面。
    最後一筆 leaveUGCGameInternal 比最後一筆 Joining game 晚＝True；反過來＝False；兩種都沒有＝None。"""
    j = raw.rfind(b"! Joining game")
    lv = raw.rfind(b"leaveUGCGameInternal")
    if j < 0 and lv < 0:
        return None
    return lv > j

# 2026-09-14 專案結構（使用者要求）：
#   核心/共用・基礎操作・學習模組 ← 每個遊戲都載入，換遊戲照用
#   專案/<遊戲>/提示・工作流程・記憶・素材 ← 只有玩這個遊戲時才載入
# 他登入遊戲時看到的 = 基礎操作 + 學習模組 + 這個遊戲的專案，沒有別的遊戲的東西。
CORE_DIRS = [os.path.join(HERE, "核心", x) for x in ("共用", "基礎操作", "學習模組")]


def project_dir(game, *parts):
    return os.path.join(HERE, "專案", game, *parts)
NL = chr(10)
ACT_SNAP = chr(123) + '"t":"拍地標","name":"地名"' + chr(125)
ACT_TASK = chr(123) + '"t":"訂任務","名":"...","分數":80,"正負":"正","為什麼":"...","預計幾分鐘":10' + chr(125)
ACT_GOAL_OK = chr(123) + '"t":"目標達成"' + chr(125)
ACT_GOAL_NO = chr(123) + '"t":"目標放棄"' + chr(125)
ACT_HERE = chr(123) + '"t":"我在","name":"%s"' + chr(125)

SYS = """你是 gamer AI，在玩 Roblox「{game}」（Animal Anomaly，動物醫院）。
本業是幫櫃檯窗口的病人做報到（Check In）賺錢，其次是治療登記進來的病人。

【現在唯一要做的事：Check In】
不要探索、不要研究、不要跑去別的地方——**走到櫃檯窗口，一直做 Check In**。
一輪做完就等下一位，繼續做。除非理智歸零（那就是死了），否則不要做別的事。
右上角任務、地圖、階段那些都先放一邊，**先把 Check In 做出來一次**。
卡住就在原地換做法解決（橫移繞過、換角度、關掉 UI），不要跑掉。

【遇到任何新遊戲都照這個順序來（通用準則）】
1 先搞清楚遊戲身分（完整名稱、作者、封面、怎麼進去）→ 2 把基礎操作量清楚（視角、模式、轉幾度、走多遠）
→ 3 研究玩法（目的是什麼、做什麼會發生什麼、把特殊物品加給 YOLO 偵測、看別人怎麼做）
→ 4 把常做的事整合成連續流程。（查資料不是階段，是不知道怎麼做時才用的工具。）
**先認識這隻遊戲，再決定怎麼玩**。你現在在哪一階段、還差什麼，每輪提示的最上面都會寫。

【最優先】做事照畫面右上角那個黑底白字的任務（例如「在電腦上註冊」「蓋印表格」）。
每輪把它原文抄進 task 欄位；它換了就代表上一件事完成。

【這是找差異的遊戲】病人正不正常靠比對：本人 vs 資料上的照片、本人 vs CCTV、資料本身有沒有矛盾。
看不清就用 放大 動作，程式會裁切放大給你。每次比對把「比了什麼、差在哪」寫進 diff 欄位。

【滾輪／中鍵是捲動視窗內容，不是轉視角】
- 在**選單、清單、網頁**上（伺服器列表、好友列表、商店頁面）：滾輪是**上下捲動內容**——
  想看清單下面還有什麼、找人多的伺服器，就用 {{"t":"scroll","v":-3}} 往下捲、{{"t":"scroll","v":3}} 往上捲。
- 在**遊戲畫面**上：滾輪是拉遠／拉近鏡頭。
- **轉視角只有一種方法：look**（dx 左右、dy 上下）。不要拿中鍵去轉視角。

【上下不要亂動】沒有明確理由就**不要動 dy**，只轉左右（dx）。
你要找的東西幾乎都在左右，不在上面也不在下面；每動一次上下都可能把鏡頭弄歪，
歪了又要花好幾輪修回來。dy 只有兩種情況才填：程式的【視角提醒】說你在看天花板（往下修）
或說你在低頭看地板（往上修）時。
其他時候一律 dy = 0。

【視角數據要看】提示裡的【程式量到的視角】與【視角提醒】是**量出來的**，
比你用眼睛猜準。你自己承認過「沒看程式提示、過度依賴視覺判斷」——每一輪動 dy 之前，
先看那兩行；它說水平就不要動，它說偏上就往下修、**它說你在低頭就要往上修**（負的 dy，一次 40~80）。

【視角】**保持水平**——抬頭看天花板什麼都沒有，低頭看地板一樣什麼都沒有。
櫃檯上的東西、病人、按鈕、E 的互動提示，全部都在**水平視線上**，你一低頭就全部看不到。
偏上就用小的正 dy 往下修，偏下就用小的負 dy 往上修，一次 40~80，修完看一眼再說。
兩個方向程式都會擋、都會計次；以【程式量到的視角】那一行為準，不要自己猜。

【用「角度」記東西在哪，不要用「感覺」】
本機實測：拖 {ppp} 像素 ≈ 畫面轉 1%，整個畫面寬約 70 度，所以**轉 1 度 ≈ dx {deg_px}**。
記東西的時候順便記角度：站在同一個位置，往右轉幾度會看到什麼、往左轉幾度會看到什麼。
你記過的櫃檯物品與站位，程式每輪都會換算成「往右轉 35 度（look dx +200）再直走 2 秒」這種指令給你。
好的站位就用 記站位 存起來，下次直接照指令走回去，不用再亂找。

【最先要做的事：認識櫃檯（在做出第一次 Check In 之前）】
你到現在一次 Check In 都沒做出來，真正的原因不是判斷力，是**你不知道自己在哪、東西在你的哪一邊**。
所以先做這四件事，每輪提示最上面會告訴你還差什麼：
  1. 走到櫃檯前面站好。
  2. {{"t":"掃視櫃檯"}}——程式會帶你左轉 60 度、每 30 度停一次掃到右邊 60 度，
     用 YOLO 量出「哪個角度看到什麼」，掃完自動轉回原本的朝向。你不用自己亂轉。
  3. 照掃描結果用 記櫃檯 把每樣東西命名（**正對著那樣東西的時候才記**，
     程式會把你當下的朝向當成它的角度；一次記一樣最準）。
  4. {{"t":"記站位","name":"櫃檯工作位"}} 把這個位置存起來。
做完之後，每一輪你都會拿到一張「以你現在的朝向當 0 度，每樣東西在你左邊還是右邊、要轉幾度」的圖，
從此不用再猜——照著轉、照著按就好。這四件事沒做完之前，不要急著長按 E。

【櫃檯上的東西要記四件事】用 記櫃檯 動作，每樣東西記：
  1.**用途**（這東西是幹嘛的）2.**順序**（第幾步用）3.**相對位置**（在你面前的左手邊／正前方／右手邊／上方）
  4.**絕對位置**（程式自動量：你當下的朝向角度＋站的座標）
記完之後每輪都會給你看，而且程式會換算成「往右轉 35 度（look dx +200）」這種指令。
**Check In 之後接著要做的一連串動作**（拿東西→用在誰身上→確認完成）也要照順序記下來：
用 列步驟 把它拆成幾步，做完一步就 下一步；這樣你才知道自己現在在整個流程的哪裡。

【紫色 = 任務目標】畫面上發紫光、紫色外框、紫色標示的東西，就是現在這個任務要你去互動的目標。
看到紫色的優先走過去、優先互動；找不到任務目標時就找紫色。

【找不到目標】**先左右看，不要上下看**：東西幾乎都在你的左右兩邊，不在天花板也不在腳下。
用 look 小步左右掃（dx 100~150 一次，掃完停一下看清楚再掃下一次），轉滿一圈還是沒有才換房間。
上下看只會浪費時間而且容易把鏡頭卡住。

【走位】**少用 s 後退**：後退看不到你要去的地方，而且很容易退進另一面牆。
被擋住時優先用 a／d 橫移繞過去，或轉個方向用 w 走；只有整個貼在牆上時才退一小步（0.5 秒以內）。
面對與移動分兩步：先轉正對目標，下一輪再走（同輪兩者兼具時移動會被拿掉）。
最後那一點偏差用 a/d 橫移對正，不要再轉頭。走廊走中間，看【左右牆距】修正。
擋住你的不只是牆——櫃子桌子椅子都會擋；按 w 後畫面沒變就是被擋住了，別再往前頂，矮的可以按 space 跳。

【眼睛】本機 YOLO 每秒看 5 次：框畫在圖上，也會在你走路時當煞車（快撞到或快走過頭就自動停）。
用 {{"t":"選","n":1,"做":"aim"}} 依編號選【可選目標】比你報座標準；用 找 換要偵測的詞（英文，動物要用 cat/dog/rabbit）。
**要 YOLO 一直盯著某樣東西、幫你把它維持在畫面正中央**，就用 {{"t":"鎖定","target":"cat"}}——
盯著的時候連你在想事情的那幾秒它都在幫你修正，睜眼時目標就在中央，不用再重新找。不需要了就 解除鎖定。

【理智】20% 以上都夠用，先做報到與治療；低於 20% 才去補，補完馬上回櫃檯。

【每一輪先確認有沒有 UI 開著】
動作之前先看一眼：畫面上有沒有蓋著一塊面板／視窗／選單／對話框？
有的話，**先把它關掉**，不然你按什麼、走什麼都不會有反應（很多次「按了沒用」都是這個原因）。
關 UI 的優先順序：
  1. **先找叉叉（X）**——通常在那塊面板的右上角，點它最乾淨。
  2. 面板上有寫「關閉／返回／繼續」的按鈕，就點那個。
  3. 點面板以外的空白處。
  4. **esc 放到最後**——esc 是開關，按了可能反而又打開別的選單，而且很容易變成一直按。
關掉之後再看一次畫面確認真的關了，才繼續做事。

【選單怎麼關掉】
esc 是**開關**：按一下開選單、再按一下關。所以選單開著的時候一直按 esc，
等於自己把它開開關關，永遠出不來（你今天在這裡浪費了幾百輪）。
選單開著的時候：**看選單下方或按鈕上寫的字**，它會直接告訴你哪個鍵對應什麼
（例如「繼續」「離開」「重生」，前面會標對應的按鍵）。想繼續玩就選「繼續」，
要離開這場就選「離開」。用滑鼠點按鈕，或按它標示的那個鍵。
點選單以外的空白處通常也能關掉。

【右上角沒有提示的時候，自己去查】
遊戲不會一直給你任務提示。到後面右上角那塊會空著——**那不代表沒事做**，
而是要你自己判斷現在該做什麼。這時候不要在原地繞：
  1. 先看畫面上有沒有別的線索（紫色標示、黃色箭頭、窗口有沒有人、手上有什麼東西）。
  2. 想不出來就**自己去查**：用 {{"t":"wiki","q":"..."}}（**YouTube 已經停用，不要用**），
     查「這個階段接下來要做什麼」「這個東西怎麼用」，查到什麼寫進 n 與筆記。
  3. 查完回來照著試一次，成功就把它變成步驟（列步驟），失敗就檢討再查。
**沒有人會告訴你答案，自己查、自己試，這是你唯一的辦法。**

【沒有出現 E 的提示 = 按了也沒用】
這遊戲只要能互動，畫面上就會跳出提示（例如「按住 E」「按 E」或一個 E 的圖示）。
**看不到那個提示，就代表你現在按 E 一定沒有反應**——原因只有兩種：
  1. **你沒對準它**：準心沒有落在那樣東西上，或站得太遠／太近、角度不對
     → 先用 a/d 橫移對正、走近一步，看提示有沒有跳出來。
  2. **它本來就不能互動**：椅子、病人本體、其他玩家、裝飾品都不能按
     → 別再試了，換一個目標（看【哪些東西按得動】那份清單）。
所以順序永遠是：**先讓提示出現，再按 E**。提示沒出來就不要按，那只是浪費時間。

【按 E 之前先確認畫面是不是你要的】
不要看到東西就按。按之前先問自己三件事：
  1. 準心（畫面正中央）是不是真的落在那樣東西上？（不是在旁邊、不是在牆上）
  2. 畫面有沒有出現互動提示（像「按住 E」「按 E」那種字）？沒有提示就代表按了也沒用。
  3. 這樣東西是不是右上角任務要的？不是的話按了只是浪費時間。
三個都確認了再按；只要有一個不確定，先靠近一步、對正、或用 放大 看清楚再按。
你今天大量的時間都花在對著錯的東西一直長按 E。

【做 Check In 的時候人是不會動的】
一旦開始在櫃檯作業（登記、看文件、看照片、看 CCTV），角色就被固定住了——
**按 w/a/d 完全沒有用，畫面不會變**。這時候要換的是「視線」不是「位置」：
用 look 小幅左右轉視角去看窗口／文件／照片／CCTV，用滑鼠 click 去按畫面上的按鈕，
細節看不清就用 放大。想離開櫃檯作業狀態再按 esc 或退開。
如果你按了 w 好幾次畫面都沒變，先想想是不是正在櫃檯作業中，而不是被牆擋住。

【理智歸零就自己離開遊戲】
理智掉到 0% 你就死了，角色會倒在地上動不了，**留在那裡什麼都做不了、只是浪費時間**。
看到理智 0%、角色倒地、或出現死亡畫面：**自己按 esc 打開選單、選離開遊戲（Leave），
回大廳重新開始**。絕對不要點「自我復活」那種要花錢的東西。
**離開之後要自己重新進去**：回到大廳後，找到動物醫院重新加入（優先進有其他玩家的場），
進去之後照舊：先確認自己在哪（上方有沒有「輪班 N」、是不是第一人稱）、再看右上角任務。
離開之前先想一下這次為什麼死，等一下要寫進檢討。
整個循環是：**理智 0 → esc 離開 → 回大廳 → 重新加入 → 確認位置 → 繼續做事**，不要停在任何一步。

【什麼時候才可以離開遊戲】**只有一種情況：理智歸零（你死了）。**
除此之外一律不准退出——卡住了就想辦法在裡面解決（後退、橫移繞路、換方向、換目標、關掉 UI）。
退出重進要花好幾分鐘載入，而且進去又要從頭找路，比在裡面慢慢解決還糟。
（程式會擋掉沒有理由的離開。）

【卡太久怎麼辦（不是退出）】同一個地方卡超過三分鐘，**不要退出遊戲**，改用這些方法：
後退一步→換方向走／用 a/d 橫移繞過去／轉到別的方向找路／換一個目標做／先關掉擋住的 UI。
把卡住的原因寫進檢討，下次避免。**只有理智歸零才可以退出。**

【怎麼分辨自己在上班還是回到大廳】
- **在上班（醫院裡）**：畫面正上方有「輪班 N」，而且是**第一人稱**（看不到自己的身體）。
- **回到大廳**：正上方的「輪班 N」**不見了**，而且切回**第三人稱**（看得到自己的角色背影），
  左邊會出現商店／邀請／職業／日誌那排按鈕。
  看到這兩個變化就代表這一輪班已經結束（或你死了／離開了），要重新進醫院才會再開始上班。
每輪先確認這兩點，再決定要做什麼——不要在大廳裡找櫃檯、也不要在醫院裡找 Play。

【你在哪】遊戲裡的戶外大廳（3D 場景、ANIMAL HOSPITAL 建築、商店/邀請/職業/日誌按鈕）**不是** Roblox 首頁，
不要再點 Play。程式每輪會直接告訴你答案，以它為準。
【特殊任務：找隊友】（暫時性，之後會取消）配對時**一定要挑人多的場**：
在遊戲頁面往下捲有「伺服器」清單，每個伺服器都寫著人數（像 6/8、3/8）——**挑人數多的那個加入**。
直接按大大的播放鍵常常會把你丟進空場。人多的場才學得到東西（看別人怎麼做），也比較不會一個人卡死。，盡量不要自己一個人玩：
在大廳看人數顯示（0/4、2/4），有人就加入；整場只有你一個人就退出重新配對。
有人的時候看他們往哪走、按什麼、先做什麼再做什麼，寫進筆記再自己照著試一次。

【學習】沒有人教你：自己查攻略（只能用 wiki，**YouTube 已停用**）、自己實測（實驗）、自己記地點（記位置）、
自己列步驟（列步驟／下一步）、學到的寫進 n。

動作（一次最多 2 個，座標 0~1）：
  {{"t":"click","x":.5,"y":.4,"g":"點的東西"}}  {{"t":"hold","k":"e","g":"互動對象"}}
  {{"t":"aim","x":.6,"y":.5,"g":"目標"}}        {{"t":"key","k":"w","d":1.0}}（w a s d space e esc tab 1-9）
  {{"t":"look","dx":150,"dy":0}}（**dy 沒事就填 0**；dx 一次最多 {max_dx}、dy 最多 {max_dy}；實測拖 {ppp} 像素≈畫面轉 1%，四分之一畫面≈dx {quarter}）
  {{"t":"scroll","v":-3}}（在選單/清單上＝往下捲內容；在遊戲畫面上＝鏡頭拉遠）  {{"t":"wait","d":1.0}}
  {{"t":"鎖定","target":"cat"}}                        叫 YOLO 一直盯著它、幫你把它維持在畫面正中央（你想事情的時候它也在盯）
  {{"t":"解除鎖定"}}                                    不用盯了
  {{"t":"選","n":1,"做":"aim"}}  {{"t":"放大","x":.6,"y":.4,"g":"要看的東西"}}  {{"t":"走到","target":"cat"}}
  {{"t":"找","labels":["cat","door"]}}  {{"t":"記遊戲","名稱":"...","作者":"...","封面特徵":"...","進入步驟":"...","視角":"第一人稱","模式":"大廳+局內"}}
      記住這個遊戲的身分（會順便存一張封面截圖），之後才不會進錯遊戲；欄位有哪個填哪個
  {{"t":"記因果","做了":"長按E開電腦","發生":"跳出登記畫面"}}   把「做了什麼→發生什麼」記下來
  {{"t":"記流程","名字":"報到一個病人","步驟":["走到櫃檯","長按E開電腦","比對照片","蓋章"]}}
      把常常連續做的事整合成一條工作流程，之後照著做就好
  {{"t":"記櫃檯","items":[{{"名字":"電腦","用途":"登記病人","順序":1,"相對位置":"正前方","怎麼用":"長按E"}}]}}
      每樣東西要記四件事：1.用途　2.順序（第幾步用）　3.相對位置（左手邊／正前方／右手邊／上方）
      4.絕對位置＝程式自動幫你量（你當下的朝向角度＋站的座標），下次它會直接告訴你要往哪轉幾度
  {{"t":"掃視櫃檯"}}                                     程式帶你左右各掃 60 度，量出每個角度看到什麼（掃完自動轉回來）
  {{"t":"記站位","name":"櫃檯工作位"}}                   記住「你現在站的位置＋朝向」，之後程式會告訴你怎麼走回來、要轉幾度
  {{"t":"設目標","name":"櫃檯窗口"}}                    從地圖上挑下一個要去的地點，地圖會用紅圈標出來並畫一條線給你
  {{"t":"我在","name":"櫃檯窗口"}}                      認出自己站在記過的地方就用它校正座標（推算會漂，這是唯一的修正方法）
  {{"t":"記位置","name":"名字"}}  {{"t":"列步驟","steps":["..."]}}  {{"t":"下一步"}}  {{"t":"重想"}}  {{"t":"拍地標","name":"地名"}}  {{"t":"目標達成"}}  {{"t":"目標放棄"}}  {{"t":"訂任務","名":"...","分數":80,"正負":"正","預計幾分鐘":10}}
  {{"t":"實驗","k":"look","dx":100}}  {{"t":"wiki","q":"..."}}  {{"t":"think","q":"..."}}
  物品欄：按數字鍵 1~9 拿在手上，再左鍵或 E 使用。

鐵律：不准花任何錢（Robux、金幣、購買、解鎖、升級、自我復活）；看到付費視窗只能按 esc。
只輸出一個 JSON，不要 markdown、不要多餘文字。"""

ASK = """【程式確認：你現在看的是什麼視窗】{where}
{where_fix}
【任務樹：大任務 → 中任務 → 現在專注】（使用者定的學習結構，每輪都在這裡）
{tasktree}

【程序化執行：學會的中任務要寫成程式步驟，之後不用問你】
{proc}
{proc_hint}
【你現在的學習階段】
{stage}

【遊戲要你做的事（右上角黑底白字）】{task}
　→ 這是最優先的，你自己想做的事都排在它後面。

【你現在做的事】{flow}（第 {step} 步，這是你自己標的）
可以用的標籤：{flow_list}

【這個流程的做法】（你自己做成功過的那一套，照著做，不要重新亂試）
{flow_doc}

【這一班的計畫】
{shift_plan}

【你自己的遊戲手冊】（你自己整理的，分成幾個部分）
{organised}

【你自己實測的操作手冊】
{manual}

【報到課程】（你現在唯一的目標就是學會 Check In，一關一關過）
{course}

【你現在在哪、櫃檯設備在你的哪一邊】（程式用你自己記的角度即時換算）
{desk_layout}

【櫃檯掃描結果】（程式帶你掃的，不是你猜的）
{desk_scan}

【你記過的站位】（怎麼走回去、要轉幾度）
{stands}

【你記下的櫃檯配置】（東西在你面前的哪一邊、照什麼順序用）
{desk}

【你自己列的步驟】（箭頭是你回報做到哪一步了）
{steps}

【程式確認：你現在在哪一個遊戲】（讀 Roblox 自己的紀錄，這是事實）
{place}

【你記過的遊戲身分】（重進遊戲前一定要比對，絕對不能進錯遊戲）
{game_id}

【你記過的地方】（只有名字——**地圖長什麼樣子、彼此的相對位置，要你自己在腦中想**）
{map}
{route}

【你比對過的差異（你自己寫的）】
{diffs}

【你自己查到、自己記下來的筆記】（沒有人幫你整理，這些是你自己寫的）
{notes}

【你剛才連續做的三步】（含實際效果，接著這三步繼續想，不要每輪重來）
{last3}

【再往前幾輪】
{history}

【你自己寫的檢討報告】（上次為什麼死掉／為什麼卡住，這次不要再犯）
{lesson}

{score_reset_hint}{reward_hint}{train_hint}{propose_hint}{no_award_hint}{punish_hint}{lesson_hint}{study_hint}{task_hint}{retry_hint}{item_hint}{pay_hint}{death_hint}{aim_tip}{frozen}{flow_lock}{ui_hint}{hold_block}{leave_hint}{click_hint}{esc_hint}{longstuck}{osc_hint}{blocked}{brake_hint}{two_part_hint}{turn_move_hint}{dark_hint}{repeat_hint}{hold_hint}{spin_hint}【YOLO 算給你的下一步】{ycmd}
（這是偵測器的座標換算出來的建議，不是命令；你可以照做，也可以自己判斷不要照做。
　要它直接帶你走過去就用 {{"t":"走到","target":"..."}}）
【對正提示】{align}
【前方障礙（YOLO 把關）】{obstacle}
【左右牆距（走廊置中用）】{center}
【程式估的可走方向】{open_dir}
{look_up_hint}【目前視角（水平儀）】{gyro}
（這是你自己的視角度數表。程式**不會**再幫你把鏡頭壓回水平，也不會擋你亂轉——
　抬到天花板就會一直卡在那裡，只能靠你自己看著這個讀數修回來。今天你想抬頭 {up_n} 次、想低頭 {down_n} 次。）
【視角提醒】{level}（視角要**水平**：抬頭只看得到天花板，低頭只看得到地板，兩邊都做不了事）
【程式量到的視角】{view}
{pitch_hint}
（dy 正的＝往下看看到地板，dy 負的＝往上看看到天花板。程式量到的方向比你用眼睛猜的準，以它為準。）

【你的 YOLO 眼睛正在找】{watching}
（要換就用 找 動作。它每秒看 5 次，框到的東西也會畫在你看到的圖上。）

【YOLO 正在盯住的東西】{lock}

【畫面上有沒有 E 提示】（程式用影像判斷的，比你用眼睛猜準）
{eprompt}

【程式框到的按鈕】（YOLO 只認實體物件、不認 UI 文字，所以按鈕改由程式用顏色框給你）
{buttons}

【你自己訂的目標】（不是別人叫你做的，是你自己說要做到的）
{goal}

【倒數獎金】（拖越久拿越少：200 分起跳，每秒 −2，最低 50）
{clock}

【任務分數表】（每件事值幾分是**你自己訂的**，程式只擋明顯偏袒自己的評分）
{tasks}

【你自己訂的改善規則】（每次被扣分檢討出來的，程式在盯有沒有效）
{lessons}

【計分板】（做成該做的事有大獎，做不該做的事會扣分）
{score}

【畫面上的字（本機 OCR 讀出來的，不是你猜的）】
{ocr}

【探索地圖】（走過的、撞過牆的、還沒去過的——牆和未探索的邊界都標出來了）
{explore}

【腦中地圖對不對？（用真的畫面印證）】
{verify}

【畫面變化（特別注意）】（同一個東西變色、場地多出東西、東西不見了——這款遊戲的異變就長這樣）
{changes}

【慢想排出來的計畫】（不趕時間的那一層排的：現在這個大動作的完整步驟＋下一個大動作）
{plan}

【腦中的地圖】（你在哪、朝哪、每樣東西在你的哪個方向多遠——這是俯視圖，用想的）
{mindmap}

【走過去要按幾秒】（程式用框的大小幫你換算——這是你自己算不出來的）
{dist}

【畫面外的東西在哪】（你轉開頭之後它們還在，別忘了）
{space}

【你自己的重複度】（沒有人擋你，這是你自己的紀錄，自己看著辦）
{mirror}

【可選目標】（眼睛框到的東西，用編號選比報座標準：{{"t":"選","n":1,"做":"aim"}}）
{cands}

【眼睛現在框到】（位置、大小、信心分數）
{eyes}

{join}{quickdeath}{offscreen}
【你把這隻遊戲分成哪幾區】（"區" 欄位你自己填，程式只記帳）
{zones}

【你自己長什麼樣】（看得到自己＝第三人稱）
{selfie}

【你的畫面有多大】（滑鼠位移是像素；畫面大小變了，你的刻度就要跟著變）
{screen}

【你自己的遊戲資料夾】（這隻遊戲的知識，你自己寫、自己改、自己分類）
{know}

【你把場地切成哪幾個空間】（座標是一個空間一套；被傳送就切一個新的）
{spaces}

【你成功過的做法】（程式自動記的：那一次你是怎麼做到的，照著重做）
{wins}

【你自己回推出來的成功做法】（成功之後你自己從紀錄倒推的，不是別人教的）
{wins_how}

【哪些東西按得動、哪些按不動】（你自己試出來的，不要重複試沒用的）
{tries}

【你的操作熟練度】{skills}
（現在的目標就是把這幾個數字練好：互動要按得到、移動要真的有前進、不要再抬頭）

【成績】完成報到 {checkin} 次、拒絕異常 {reject} 次；最高輪班 {shift}、最高金幣 ${coin}

【使用者交代】
{instr}

看畫面，決定接下來做什麼。只輸出 JSON，**能省就省、不要多寫**（2026-10-01 使用者：輸出減少字樣）：
{{"s":"看到什麼(<=12字)","區":"哪一區(<=8字)","task":"右上角任務原文（沒有就空字串）",
 "想":"為什麼做這個(<=20字)","a":[動作...最多2個]}}
只有要換流程才加 "flow"、真的學到新東西才加 "n"(<=25字)、真的比對出差異才加 "diff"(<=30字)；否則這三個都不要寫。"""


def single_instance_or_die(game):
    """同一個遊戲只准跑一個代理。

    踩過的坑：重啟時舊的還沒讀到 stop.txt 就啟了新的，結果四個代理同時搶滑鼠、
    一起打模型，首字延遲飆到 6 秒、一堆輪次來不及輸出動作。
    """
    import psutil
    me = os.getpid()
    rows = []
    for pr in psutil.process_iter(["pid", "ppid", "name", "cmdline"]):
        try:
            if "python" not in (pr.info["name"] or "").lower():
                continue
            cl = " ".join(pr.info["cmdline"] or [])
            if "flow_agent.py" in cl and game in cl:
                rows.append(pr)
        except Exception:  # noqa: BLE001
            continue
    # torch/ultralytics 會生出 cmdline 一模一樣的子行程，只算「父行程不是代理」的那些才是真的代理
    pids = {pr.info["pid"] for pr in rows}
    others = [pr for pr in rows
              if pr.info["pid"] != me and pr.info["ppid"] not in pids and pr.info["ppid"] != me]
    if others:
        print(f"⚠ 已經有 {len(others)} 個代理在跑（PID {[p.pid for p in others]}），先把它們關掉")
        for pr in others:
            try:
                pr.terminate()
            except Exception:  # noqa: BLE001
                pass
        gone, alive = psutil.wait_procs(others, timeout=8)
        for pr in alive:
            try:
                pr.kill()
            except Exception:  # noqa: BLE001
                pass
        print("⚠ 舊代理已關閉，這個實例接手")
        time.sleep(1.5)



# ---------------------------------------------------------------- 學習階段（使用者指定的課程）
STAGES = [
    {"名": "1 如何登入遊戲",
     "做什麼": "把這個遊戲的身分記清楚，之後才不會進錯遊戲：完整名稱、作者、封面長什麼樣、怎麼進去（步驟）。",
     "要完成": "用 記遊戲 動作填好：名稱／作者／封面特徵／進入步驟，四項都要。"},
    {"名": "2 基礎操作",
     "做什麼": "把操作量清楚：第幾人稱、有哪些模式（大廳／局內是不是分開的）、"
              "轉視角 X／Y 各拖多少像素等於幾度（送 量刻度，程式會帶你機械地量；圖片只輔助）、"
              "每個移動鍵按幾秒會走多遠。",
     "要完成": "操作手冊要有 look 三種幅度＋w/a/s/d 各一筆（用 實驗 動作量），"
              "並用 記遊戲 補上『視角』與『模式』兩欄。全程不准大幅度瞬間轉頭，保持水平。"},
    {"名": "2.5 探索與空間感",
     "做什麼": "在腦中建立這張地圖：你自己的座標與朝向、每個地點在哪個方向多遠、"
              "可以互動的東西在地圖上的哪裡。走到明顯的地方就 記位置；"
              "任何一次互動成功，程式會自動把那個點記進【腦中的地圖】。"
              "認出自己站在記過的地方時，用 我在 校正位置（航位推算會漂）。"
              "同時練兩件事：轉頭之前先想「我要找的東西在哪一邊」（看【畫面外的東西在哪】）、"
              "走之前先看「這段路要按幾秒」（看【走過去要按幾秒】），不要一次 0.5 秒試十遍。",
     "要完成": "地圖上至少 6 個地點、腦中地圖至少 3 個互動點、"
              "走路換算至少 3 筆實測樣本、而且用過至少 1 次 我在 校正位置。"},
    {"名": "3 研究玩法",
     "做什麼": "搞懂這遊戲要做什麼、目的是什麼、做了某件事會發生什麼。"
              "看到不認識的特殊物品，就用 找 動作把它的英文名加給 YOLO，讓它幫你盯著。"
              "也可以看其他玩家怎麼做然後模仿。",
     "要完成": "因果紀錄（做了X→發生Y）至少 8 條，並且自己加過至少 3 個 YOLO 偵測詞。"},
    {"名": "4 整合任務",
     "做什麼": "把常常連續做的事整合成一條完整工作流程，之後照著做就好，不用每次重新摸索。"
              "（像『煮飯＝準備食材→炒→吃』那樣，一條龍寫下來。）",
     "要完成": "用 記流程 建立至少 2 條連續流程，每條至少 3 步。"},
]

GUARD_NAMES = ["抬頭", "低頭", "幅度", "兩步", "吸附", "振盪", "煞車", "打轉", "重複", "脫困",
               "理智", "空轉aim", "鏡頭復位", "esc", "離開", "互動",
               "擋視角", "自動視角"]


CHECKIN_COURSE = [
    {"key": "C1", "名": "找到櫃檯、站到定位",
     "目標": "走到櫃檯窗口前面站好，並且把這個位置存起來，以後隨時走得回來。",
     "怎麼做": '走到櫃檯前站好 → {"t":"記站位","name":"櫃檯工作位"}',
     "驗收": "站位裡有「櫃檯工作位」"},
    {"key": "C2", "名": "認識櫃檯佈局",
     "目標": "知道櫃檯上有哪些設備、每樣在你的哪一邊、第幾步用。",
     "怎麼做": '{"t":"掃視櫃檯"} 讓程式帶你掃一圈 → 正對著每樣東西用 記櫃檯'
               '（至少 3 樣，每樣都要寫「用途」與「順序」）',
     "驗收": "掃描過，而且至少 3 樣設備寫了用途與順序"},
    {"key": "C3", "名": "找出怎麼開始作業",
     "目標": "搞清楚按哪一樣東西、怎麼按，才會跳出登記畫面。",
     "怎麼做": '對準設備長按 E（沒有 E 提示就先對正、走近）；'
               '有反應就用 {"t":"記因果","做了":"長按E開電腦","發生":"跳出登記畫面"} 記下來',
     "驗收": "跟櫃檯有關的因果紀錄至少 3 條"},
    {"key": "C4", "名": "看懂登記畫面",
     "目標": "知道畫面上有什麼、要拿什麼跟什麼比對。",
     "怎麼做": '看不清就用 放大；每一輪把「比了什麼、差在哪」寫進 diff 欄位',
     "驗收": "diff 欄位寫過至少 3 次"},
    {"key": "C5", "名": "把一個病人做完",
     "目標": "照順序把一次 Check In 從頭做到尾。",
     "怎麼做": '用 列步驟 把它拆成幾步、做完一步就 下一步；成功之後用 記流程 存成一條流程',
     "驗收": "完成報到至少 1 次，而且「報到」列過步驟"},
    {"key": "C6", "名": "做穩",
     "目標": "不靠運氣，照自己記下來的步驟連續做。",
     "怎麼做": "照你自己的步驟一個接一個做；卡住就寫檢討、改做法再試",
     "驗收": "完成報到至少 5 次"},
]


from procedure import ProcedureMixin  # noqa: E402


class FlowAgent(ProcedureMixin, Agent):
    def g(self, name):
        """這道防線開著嗎？（花錢攔截永遠開著，不可關）"""
        return bool(self.guards.get(name, True))

    def __init__(self, args):
        single_instance_or_die(args.game)
        super().__init__(args)
        # 門檻 0.30 在這種卡通畫風常常什麼都框不到；放寬到 0.18，分數會一起給他判斷
        self.eyes = Eyes(bbox_fn=self.win.client_bbox, fps=args.vision_fps,
                         short_edge=args.vision_size, threshold=0.18)
        # YOLO-World 是開放詞彙，一開機盯哪些字寫在 專案/<遊戲>/遊戲.json 的「詞彙」，
        # 換遊戲改那份檔案就好；他自己拍地標學到的詞會再加進來。
        self.eyes.watch(list(self.gcfg.get("詞彙") or []))
        self.eyes.start()
        # 2026-09-18 使用者：按鈕上的字讀不到 → 改用 Windows 內建 OCR（繁體、每次約 100 ms），
        # rapidocr 留作後備（見 local_text.py）。快了十倍，所以順便把間隔從 1.2s 縮到 0.8s，
        # 他看到的字更接近「現在」；width 只有退回 rapidocr 時才用得到。
        self.text_eyes = TextEyes(bbox_fn=self.win.client_bbox, interval=0.8,
                                  width=1536, engine="auto")
        self.text_eyes.start()
        self.watching = list(self.eyes.labels)
        self.flows = self.load_flows()
        try:                                  # 乾淨的自訂詞一開機就盯（例如 entrance、animal hospital）
            extra = sorted(w for w in getattr(self, "custom_labels", set()) if w not in self.watching)
            if extra:
                self.eyes.watch(self.watching + extra[:20])
                self.watching = list(self.eyes.labels)
        except Exception:  # noqa: BLE001
            pass
        self.flow = args.flow if args.flow in self.flows else next(iter(self.flows), "")
        self.step = 1
        self.san = None
        # 防線開關：預設全開；--guards off 全關、--guards 抬頭,煞車 只留指定的；設定.json 的「防線」也可設
        raw = (getattr(args, "guards", "") or "").strip()
        cfgg = (self.cfg.get("防線") or {}) if isinstance(self.cfg.get("防線"), dict) else {}
        if raw.lower() in ("off", "none", "全關"):
            self.guards = {k: False for k in GUARD_NAMES}
        elif raw and raw.lower() not in ("on", "all", "全開"):
            keep = {x.strip() for x in raw.split(",") if x.strip()}
            self.guards = {k: (k in keep) for k in GUARD_NAMES}
        else:
            self.guards = {k: bool(cfgg.get(k, True)) for k in GUARD_NAMES}
        off = [k for k, v in self.guards.items() if not v]
        print(f"[防線] 開啟 {sum(self.guards.values())}/{len(GUARD_NAMES)}"
              + (f"；關閉：{'、'.join(off)}" if off else "（全開）") + "　（花錢攔截永遠開著）")
        self.load_memory()
        self.load_spaces()
        self.load_map()
        self.load_manual()
        self.load_steps()
        self.load_desk()
        self.load_scan()
        self.load_plan()
        self.load_appear()
        self.load_classes()
        self.load_marks()
        self.load_penalty()
        self.load_reward()
        self.load_tasks()
        self.load_lessons()
        self.load_procs()
        self.load_landmark_words()
        self.load_goal()
        self.load_course()
        self.load_wins()
        self.load_wins_how()
        self.load_selfie()
        self.load_walk_cal()
        self.load_spots()
        self.count_diffs()
        self.load_try()
        self.load_stage()
        self.load_stands()
        self.show_map = str(getattr(args, "map", "off")).lower() in ("on", "1", "yes", "開")
        print(f"[地圖] 程式畫的地圖與方位換算：{'開' if self.show_map else '關（讓他自己想像地圖的樣子）'}")
        self.no_prompt_dir = str(getattr(args, "prompt", "模組")).lower() in ("off", "內建", "none")
        if self.no_prompt_dir:
            print("[提示] 用程式內建的完整 SYS（沒有讀 提示/）")
        else:
            self.load_prompt_parts()
        raw_lock = (args.lock_flow or "").replace("、", ",")
        self.lock_flows = [x.strip() for x in raw_lock.split(",") if x.strip()]
        self.lock_flow = self.lock_flows[0] if self.lock_flows else ""
        if self.lock_flows:
            self.flow, self.step = self.lock_flows[0], 1
            print(f"[鎖定] 這一輪只做「{'、'.join(self.lock_flows)}」，不准換別的流程")
        self._boot = time.time()
        self._last_snap = time.time() - 1500      # 開機五分鐘後先存一次
        self._last_study = 0.0
        self._stuck_count = 0
        self._last_organise = time.time() - 1800      # 開機半小時後就整理一次
        self.organised = {}
        op = os.path.join(self.dir, "學習整理.md")
        if os.path.exists(op):
            cat = None
            for line in open(op, encoding="utf-8"):
                if line.startswith("## "):
                    cat = line[3:].strip()
                    self.organised.setdefault(cat, [])
                elif line.startswith("- ") and cat:
                    self.organised[cat].append(line[2:].strip())
            print(f"[整理] 載入他自己整理過的 {sum(len(v) for v in self.organised.values())} 條")
        self.load_progress()
        self.load_sensitivity()
        # 半小時回報的計時要跨重啟（改程式重啟很頻繁，不然永遠等不到 30 分鐘）
        rp = os.path.join(self.dir, "自我回報.md")
        self._last_report = os.path.getmtime(rp) if os.path.exists(rp) else time.time()
        self.stat_path = os.path.join(self.dir, "流程統計.json")
        self.stat = {"登記": 0, "拒絕": 0, "輪數": 0, "換流程": 0, "擋掉": 0,
                     "想抬頭被擋": 0, "想低頭被擋": 0,
                     "程式抬頭": 0, "程式壓頭": 0}
        try:
            with open(self.stat_path, encoding="utf-8") as f:
                self.stat.update({k: v for k, v in json.load(f).items() if isinstance(v, int)})
        except (OSError, json.JSONDecodeError):
            pass
        # 2026-09-17 使用者加的：短期記憶（最近幾輪的畫面＋做了什麼）與死後改善
        self.load_short_mem()
        self.load_improve()
        self.load_key_moves()
        self.load_points()
        self.load_buttons_memo()
        self.load_macros()


    # ---------------- 死亡／卡住的檢討報告（他自己寫，下一輪就回灌給他）
    STUCK_PROMPT = """你剛剛卡住了：{why}
請檢討：1) 為什麼會卡在這裡 2) 剛才哪個做法是錯的 3) 下次遇到同樣情況要怎麼做才對（具體）
只輸出 JSON：{{"cause":"為什麼卡住(<=40字)","mistake":"錯在哪(<=40字)","avoid":"下次怎麼做(<=60字)"}}"""

    def note_progress(self, what=""):
        """有進展就重新計時（移動真的有前進、互動成功、任務換了、報到成功…）。"""
        self._alive_since = time.time()
        if what:
            self._alive_why = what

    def frozen_secs(self):
        """完全動不了幾秒了（沒有任何一件事成功）。"""
        return time.time() - getattr(self, "_alive_since", getattr(self, "_boot", time.time()))

    def frozen_hint(self):
        # 2026-10-01 使用者：「卡 N 分鐘取消」——他把這個數字當成離開遊戲的理由（今天 63 次「卡住 N 分鐘，直接退出重進」），
        # 而且計時器在他有進展時沒歸零，一路累積到 77 分鐘。面板不再顯示；內部的 frozen_secs 其他地方照舊用。
        return ""
        s = self.frozen_secs()
        if s < 120:
            return ""
        if s < 180:
            return (f"⚠ 你已經 {int(s)} 秒沒有任何一件事成功了（沒走成、沒按到、任務也沒變）。"
                    f"再一分鐘還是這樣就該退出重進了——先試最後一次：換方向、橫移繞過、換目標。" + NL)
        return (f"🚪 你已經 {int(s // 60)} 分鐘完全動不了——**這種情況准你退出重進**"
                f"（找出口／離開按鈕，或 esc → 離開）。" + NL
                + "**但是絕對不能進錯遊戲**：回到大廳之後照【你記過的遊戲身分】那一段比對"
                + "名稱、作者、封面，確認是同一隻才加入。" + NL)

    def game_id_text(self):
        g = getattr(self, "game_id", {}) or {}
        if not g:
            return ("（你還沒記過這個遊戲的身分——用 記遊戲 把名稱／作者／封面特徵／進入步驟填好，"
                    "不然重進的時候你會進錯遊戲。）")
        rows = [f"{k}：{v}" for k, v in g.items() if str(v).strip()]
        miss = [k for k in ("名稱", "作者", "封面特徵", "進入步驟") if not str(g.get(k, "")).strip()]
        if miss:
            rows.append("還沒填：" + "、".join(miss) + "（用 記遊戲 補上）")
        rows.append("重進之前一定要比對這幾項，**進錯遊戲比不進更糟**。")
        return NL.join(rows)

    def long_stuck_hint(self):
        """卡太久就提醒他自己退出重來（程式只提醒，讓他自己決定）。"""
        t0 = getattr(self, "_stuck_since", None)
        if t0 is None:
            return ""
        secs = time.time() - t0
        if secs < 180:
            return ""
        # 2026-09-23 使用者更正：「在同一個狀況卡住」就可以離開，不用等「完全動不了」那一行
        # （那個計時會被基礎確認歸零，等於永遠不會放行——他因此在大廳卡了五小時沒退出過）。
        return (f"⚠ 你在同一個狀況卡了 {int(secs // 60)} 分鐘了。換過兩三種做法還是一樣的話，"
                f"**這種情況你可以直接退出重進**（esc → l → enter，回遊戲頁面按藍色箭頭），不用等別的許可；"
                f"還沒換過做法就先換：後退一步走別的方向、用 a/d 橫移繞過去、換一個目標做、先關掉擋住畫面的 UI。"
                f"回大廳之後一定要比對【你記過的遊戲身分】，不能進錯遊戲。" + NL)

    def maybe_rejoin(self):
        """真的走不下去（短時間內一直卡住）就離開遊戲重進——重進比在原地耗著划算。"""
        self._stuck_times = [t for t in getattr(self, "_stuck_times", []) if time.time() - t < 420]
        self._stuck_times.append(time.time())
        return False

    def retry_last(self):
        """卡住的時候先把剛剛那個動作原封不動再做一次。

        使用者的觀察：他很多「卡住」其實是上一個動作沒生效（點到邊緣、按太短、
        畫面還沒切完），再按一次就過去了。與其判他卡住、寫檢討、換做法，
        不如先重試一次——重試成功就什麼事都沒有，重試還是不動才算真的卡住。
        """
        a = getattr(self, "_last_act", None)
        if not a or time.time() - getattr(self, "_last_act_at", 0) > 40:
            return False
        if getattr(self, "_retried_at", 0) and time.time() - self._retried_at < 20:
            return False              # 剛剛才重試過，不要一直重
        self._retried_at = time.time()
        t = a.get("t")
        print(f"   🔁 卡住了，先把剛剛那個動作再做一次：{json.dumps(a, ensure_ascii=False)[:60]}")
        if t not in ("key", "hold", "click", "look"):
            return False
        try:
            self.execute([dict(a)])
        except Exception as e:  # noqa: BLE001
            print(f"   重試失敗：{e}")
            return False
        self.retry_hint = ("🔁 剛才程式判斷你卡住，**先把你上一個動作原封不動再做了一次**"
                           "（很多「按了沒反應」其實只是沒生效）。這一輪先看看有沒有變化，"
                           "有變化就照原本的計畫繼續，還是沒變才換做法。" + NL)
        return True

    # ---------------- 檢討背景化（2026-09-29 使用者：「檢討不擋迴圈」）
    # 09/29 全天 1,545 輪的「其他」時間 21,421 秒裡，卡住檢討／扣分檢討／成功回推佔了六成：
    # 每一次都是同步叫 27B 想 40～100 秒，主迴圈停著等，最慢的輪三個檢討疊在一起 150 秒。
    # 現在檢討丟到一條背景執行緒排隊跑：主迴圈馬上進下一輪，結果出來從下一輪開始回灌。
    # 同一種檢討在跑或排隊中就不再排；排隊超過 REVIEW_MAX_PENDING 就略過。慢想維持同步（它在排下一步）。
    REVIEW_MAX_PENDING = 3

    def _in_review_thread(self):
        return threading.current_thread() is getattr(self, "_review_thread", None)

    def review_async(self, name, fn, *args, after=None):
        q = getattr(self, "_review_q", None)
        if q is None:
            q = self._review_q = queue.Queue()
            self._review_pending = set()
            self._review_running = ""
            t = threading.Thread(target=self._review_worker, name="review", daemon=True)
            self._review_thread = t
            t.start()
        if name in self._review_pending or self._review_running == name:
            print(f"   ⏳ 「{name}」已在背景排隊／進行中，這次不再排")
            return False
        if q.qsize() >= self.REVIEW_MAX_PENDING:
            print(f"   ⏳ 背景檢討已排 {q.qsize()} 個，「{name}」這次略過")
            return False
        self._review_pending.add(name)
        q.put((name, fn, args, after))
        print(f"   🧵 「{name}」丟到背景執行（不擋這一輪），排隊 {q.qsize()} 個")
        return True

    def _review_worker(self):
        while True:
            name, fn, args, after = self._review_q.get()
            self._review_pending.discard(name)
            self._review_running = name
            t0 = time.time()
            try:
                fn(*args)
                if after:
                    after()
                print(f"   🧵✅ 背景「{name}」做完（{time.time() - t0:.0f} 秒）")
            except Exception as e:  # noqa: BLE001
                print(f"   🧵 背景「{name}」失敗：{e}")
            finally:
                self._review_running = ""
                self.stat["背景檢討"] = self.stat.get("背景檢討", 0) + 1

    def review_status_text(self):
        """給提示用：背景有檢討在跑就講一句，免得他以為沒人理他。"""
        r = getattr(self, "_review_running", "")
        n = self._review_q.qsize() if getattr(self, "_review_q", None) else 0
        if not r and not n:
            return ""
        if not r:
            return f"（背景排了 {n} 個檢討還沒開始，結果出來會放進下一輪）"
        return f"（背景正在做「{r}」" + (f"，另外排了 {n} 個" if n else "") + "，結果出來會放進下一輪）"

    def stuck_review(self, why):
        """卡住也要交報告書，跟死亡一樣。"""
        if time.time() - getattr(self, "_last_stuck_review", 0) < 120:
            return
        self._last_stuck_review = time.time()
        self._stuck_count = getattr(self, "_stuck_count", 0) + 1
        self._stuck_at = time.time()          # 卡住之後三分鐘內，想退出重進就放行（使用者 09/16）
        if getattr(self, "_stuck_since", None) is None:
            self._stuck_since = time.time()
        self.maybe_rejoin()
        if self.retry_last():
            print(f"   📋 先重試上一個動作，這次不寫檢討（原因：{why}）")
            return
        self.review_async("卡住檢討", self._stuck_review_llm, why)   # 2026-09-29：叫模型那一半丟背景，不擋迴圈

    def _stuck_review_llm(self, why):
        """卡住檢討真正叫模型的那一半（背景執行緒）。前面的重試／重進判斷仍在主迴圈做。"""
        print(f"   📋 卡住檢討：{why}")
        uri, _ = jpeg_data_uri(self.draw_eyes(draw_grid(self.grab_still(1024))), 70)
        # 2026-09-17 使用者：卡住檢討也要看短期記憶——「一直卡在同一個地方」這件事
        # 從最近幾輪的畫面一比就看得出來，光看文字紀錄他分不出是原地打轉還是真的在前進。
        content = [{"type": "image_url", "image_url": {"url": uri}}]
        sm = self.short_mem_content()
        content += sm
        _j = self.step_journal_text()
        if _j:
            content.append({"type": "text", "text": _j})
        content.append({"type": "text", "text": "最近做過的事：" + NL
                        + NL.join(self.history[-12:]) + NL + NL
                        + ("（上面有你最近幾輪的畫面：先比對**是不是一直在同一個地方做同一件事**，再回答為什麼卡住。）" + NL + NL if sm else "")
                        + "【卡住前三十秒你實際做了什麼】（程式一筆一筆記的）" + NL
                        + self.trace_text(30.0, end=self._last_stuck_review) + NL + NL
                        + self.STUCK_PROMPT.format(why=why)})
        raw, _, _, _ = stream_json(self.endpoint, self.model, self.sys_prompt(), content,
                                   1200, time.time() + 90, 0.2, effort=慢想)
        o = parse_json_loose(raw) or {}
        cause, mistake, avoid = o.get("cause", ""), o.get("mistake", ""), o.get("avoid", "")
        if not (cause or avoid):
            return
        self.stuck_note = f"卡住：原因「{cause}」；錯在「{mistake}」；下次「{avoid}」"
        with open(os.path.join(self.dir, "卡住檢討.md"), "a", encoding="utf-8") as f:
            f.write(f"## {datetime.now().strftime('%m/%d %H:%M')} {why}" + NL
                    + f"- 原因：{cause}" + NL + f"- 錯在哪：{mistake}" + NL
                    + f"- 下次怎麼做：{avoid}" + NL + NL)
        if avoid:
            self.add_note(f"（卡住檢討）{avoid}")
        print(f"   📋 {self.stuck_note}")
        self.log({"時間": datetime.now().strftime("%H:%M:%S"), "模式": "卡住檢討",
                  "原因": cause, "錯誤": mistake, "避免": avoid})
        # 2026-09-27 使用者：「如果一次沒有完成他目前要完成的任務，就算要記錄問題」
        # 卡住檢討的觸發（步數超支、5 分鐘沒進展、重複沒效…）都代表這一次沒做完 → 用他自己的檢討寫成一筆問題。
        self.record_problem(f"沒一次做完：{self.macro_name() or self.flow}（{why[:60]}）",
                            tried=mistake, cause=cause, source="卡住檢討")

    def record_problem(self, problem, tried="", cause="", source="他自己"):
        """寫一筆到 記憶/遇到的問題.jsonl／.md（他自己記的，或程式在他沒一次做完時代記）。"""
        # 同一個大動作一直超支會每 3 輪觸發一次檢討；程式代記的同一件事 5 分鐘內只記一次
        key = (source, self.macro_name() or self.flow)
        if source not in ("他自己", "死亡檢討") and time.time() - getattr(self, "_prob_last", {}).get(key, 0) < 300:
            return
        self._prob_last = getattr(self, "_prob_last", {})
        self._prob_last[key] = time.time()
        try:
            rec = {"時間": datetime.now().strftime("%m/%d %H:%M:%S"), "輪": self.stat.get("輪數", 0),
                   "問題": str(problem)[:200], "原因": str(cause)[:200], "試過": str(tried)[:200],
                   "在做什麼": f"{self.flow} 第{self.step}步", "大動作": self.macro_name() or "",
                   "區": str(getattr(self, "cur_zone", "") or ""), "任務": getattr(self, "cur_task", "") or "",
                   "位置": [round(v, 1) for v in (getattr(self, "pos", None) or [0, 0])],
                   "來源": source, "模型": getattr(self, "model", "")}
            with open(os.path.join(self.dir, "遇到的問題.jsonl"), "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + NL)
            with open(os.path.join(self.dir, "遇到的問題.md"), "a", encoding="utf-8") as fh:
                fh.write(f"- {rec['時間']}｜［{source}］{rec['在做什麼']}｜{rec['問題']}"
                         + (f"｜原因：{rec['原因']}" if rec["原因"] else "")
                         + (f"｜試過：{rec['試過']}" if rec["試過"] else "") + NL)
            print(f"   🆘 記下問題（{source}）：{rec['問題'][:60]}")
        except Exception as e:  # noqa: BLE001
            print(f"   記問題失敗：{e}")

    def lesson_text(self):
        rows = []
        if getattr(self, "death_note", ""):
            rows.append("☠ " + self.death_note)
        if getattr(self, "stuck_note", ""):
            rows.append("🧱 " + self.stuck_note)
        return NL.join(rows) or "（還沒有檢討報告）"

    # ---------------- 記憶：去重、過濾廢話、依重要性回灌
    JUNK = ("視角", "轉視角", "天花板", "地板", "鏡頭", "卡牆", "貼牆", "調整", "後退", "轉頭", "水平")

    def load_memory(self):
        """讀 學到的.md：每條記一個次數（同一件事重複學到越多次，代表越常用）。"""
        self.mem = {}
        p = os.path.join(self.dir, "學到的.md")
        if os.path.exists(p):
            for line in open(p, encoding="utf-8"):
                if line.startswith("- ("):
                    try:
                        n = int(line[3:line.index(")")])
                        text = line[line.index(")") + 1:].split("  (")[0].strip()
                        for bad, good in self.BANNED.items():
                            text = text.replace(bad, good)
                        if text:
                            self.mem[text] = n
                    except (ValueError, IndexError):
                        pass
        before = len(self.mem)
        self.consolidate_memory()
        print(f"[記憶] 載入 {before} 條 → 整併成 {len(self.mem)} 條")

    def consolidate_memory(self):
        """開機時把講同一件事的記憶合併（次數相加）。

        不合併的話會長成兩千條同義句（「先大幅後退並轉向」「先 s 後退並轉頭找路」…），
        回灌時全被這種操作雜項佔滿。
        """
        merged = []
        for t, n in sorted(self.mem.items(), key=lambda kv: -kv[1]):
            g = self._grams(t)
            hit = None
            for i, (mt, mn, mg) in enumerate(merged):
                if len(g & mg) / max(1, min(len(g), len(mg))) >= 0.62:
                    hit = i
                    break
            if hit is None:
                merged.append([t, n, g])
            else:
                merged[hit][1] += n
        self.mem = {t: n for t, n, _ in merged}
        self.save_memory()

    def save_memory(self):
        p = os.path.join(self.dir, "學到的.md")
        rows = sorted(self.mem.items(), key=lambda kv: -kv[1])
        with open(p, "w", encoding="utf-8") as f:
            f.write("# 他自己學到的東西（去重、記次數；次數越多＝越常遇到）\n\n")
            for text, n in rows:
                f.write(f"- ({n}) {text}\n")

    @staticmethod
    def _norm(t):
        return "".join(c for c in t if c.isalnum())

    @staticmethod
    def _grams(t):
        """字元二元組集合，用來判斷兩句話是不是在講同一件事。"""
        n = "".join(c for c in t if c.isalnum())
        return {n[i:i + 2] for i in range(len(n) - 1)} or {n}

    @classmethod
    def _same_thing(cls, a, b):
        """同義判斷：只比字串包含會漏掉「先大幅後退並轉向」vs「先 s 後退並轉頭找路」這種改寫。"""
        ga, gb = cls._grams(a), cls._grams(b)
        if not ga or not gb:
            return False
        inter = len(ga & gb)
        return inter / min(len(ga), len(gb)) >= 0.62

    BANNED = {"主人": "使用者"}          # 使用者不喜歡「主人」這個稱呼，載入與寫入時一律換掉

    def add_note(self, note):
        """覆寫父類別：過濾掉「視角朝下需調整」這種每輪都會寫一次的廢話，其餘去重累計。"""
        note = (note or "").strip()
        for bad, good in self.BANNED.items():
            note = note.replace(bad, good)
        if len(note) < 5:
            return
        if sum(1 for k in self.JUNK if k in note) >= 1 and len(note) <= 20:
            return                                   # 操作雜訊，不值得記
        key = self._norm(note)
        for old in list(self.mem):
            o = self._norm(old)
            if key == o or (len(key) > 6 and (key in o or o in key)) or self._same_thing(note, old):
                self.mem[old] = self.mem[old] + 1     # 同一件事又學到一次
                self.save_memory()
                return
        self.mem[note] = 1
        self.save_memory()
        super().add_note(note)                        # 原始流水帳仍寫進研究筆記.md
        print(f"   📝 記住：{note}")

    def memory_text(self):
        """回灌給他的記憶。

        踩過的坑：「貼牆卡住時先 s 後退」這種操作類的教訓會被重複學到幾十次，
        次數一高就佔滿回灌名單，結果他連前方開闊時都一直後退。
        所以把記憶分成「操作雜項」與「遊戲知識」，操作類最多只留 2 條。
        """
        if not self.mem:
            return "（還沒有，自己去查攻略、自己試，學到什麼就寫進 n）"
        rows = sorted(self.mem.items(), key=lambda kv: -kv[1])
        chore = [t for t, _ in rows if any(k in t for k in self.JUNK)]
        know = [t for t, _ in rows if not any(k in t for k in self.JUNK)]
        latest = [t for t in list(self.mem)[-3:]]
        picked = know[:8] + chore[:2]
        picked += [t for t in latest if t not in picked]
        return NL.join(f"- {t}" for t in picked)

    # ---------------- 工作流程文件
    def load_flows(self):
        flows = {}
        flow_dir = project_dir(self.game, "工作流程")
        if not os.path.isdir(flow_dir):
            flow_dir = os.path.join(HERE, "工作流程")        # 舊結構
        for p in sorted(glob.glob(os.path.join(flow_dir, "*.md"))):
            name = os.path.basename(p)[:-3]
            name = name.split("_", 1)[1] if "_" in name[:3] else name
            with open(p, encoding="utf-8") as f:
                flows[name] = f.read().strip()
        print(f"[流程] 讀到 {len(flows)} 份：{'、'.join(flows)}")
        return flows

    def flow_doc(self):
        """現在這個流程的做法（工作流程/*.md 的內容）。

        以前這幾份檔案是空的（使用者要他自己摸索），現在 20_報到.md 裡寫的是
        **他自己做成功過的那一套**，所以要餵給他看、照著做。
        """
        body = (self.flows.get(self.flow) or "").strip()
        if not body or len(body.split(chr(10))) <= 3:
            return "（這個流程還沒有寫做法——你自己試出來的東西，用 記因果／列步驟 記下來。）"
        return body[:2600]

    def flow_list(self):
        """只給標籤名字，不給步驟——怎麼做要他自己查攻略、自己摸索。"""
        # 沒有工作流程檔＝這個遊戲還沒有任何流程，標籤要他自己長出來，不要給別的遊戲的。
        return "、".join(self.flows) or "（還沒有任何流程；你自己做出一套之後用 記流程 存起來）"

    def load_progress(self):
        try:
            with open(os.path.join(self.dir, "流程進度.json"), encoding="utf-8") as f:
                d = json.load(f)
            if d.get("流程") in self.flows:
                self.flow, self.step = d["流程"], int(d.get("步驟", 1))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass

    def save_progress(self):
        try:
            with open(os.path.join(self.dir, "流程進度.json"), "w", encoding="utf-8") as f:
                json.dump({"流程": self.flow, "步驟": self.step,
                           "更新": datetime.now().strftime("%m/%d %H:%M:%S")}, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    SNAP_FILES = ["學到的.md", "學習整理.md", "我理解的遊戲.md", "操作手冊.md", "地圖.json", "地圖.png",
                  "櫃檯配置.json", "站位.json", "步驟.json", "流程進度.json", "流程統計.json",
                  "死亡檢討.md", "卡住檢討.md", "差異筆記.md", "自我回報.md", "任務指示.txt"]

    def snapshot_save(self):
        """定期整包存檔：他累積的知識與紀錄複製一份到 存檔/時間戳/。"""
        import shutil
        dst = os.path.join(self.dir, "存檔", datetime.now().strftime("%m%d_%H%M"))
        os.makedirs(dst, exist_ok=True)
        n = 0
        for f in self.SNAP_FILES:
            src = os.path.join(self.dir, f)
            if os.path.exists(src):
                try:
                    shutil.copy2(src, os.path.join(dst, f))
                    n += 1
                except OSError:
                    pass
        kd = os.path.join(self.dir, "知識")
        if os.path.isdir(kd):
            try:
                shutil.copytree(kd, os.path.join(dst, "知識"), dirs_exist_ok=True)
                n += len(os.listdir(kd))
            except OSError:
                pass
        print(f"   💾 存檔：{n} 個檔案 → {dst}")
        self.log({"時間": datetime.now().strftime("%H:%M:%S"), "模式": "存檔", "檔案數": n, "路徑": dst})

    def skill_stats(self):
        """把他的操作熟練度算給他自己看（他要靠這個知道自己哪裡還不行）。"""
        t = self.stat
        tot = max(1, t.get("互動次數", 0))
        mov = max(1, t.get("移動次數", 0))
        return (f"互動成功率 {t.get('互動成功', 0)}/{t.get('互動次數', 0)}"
                f"（{100 * t.get('互動成功', 0) / tot:.0f}%）"
                f"｜移動有效率 {t.get('移動有效', 0)}/{t.get('移動次數', 0)}"
                f"（{100 * t.get('移動有效', 0) / mov:.0f}%）"
                f"｜想抬頭被擋 {t.get('想抬頭被擋', 0)} 次"
                f"｜卡住檢討 {getattr(self, '_stuck_count', 0)} 次")

    def save_stat(self):
        try:
            with open(self.stat_path, "w", encoding="utf-8") as f:
                json.dump({**self.stat, "更新": datetime.now().strftime("%m/%d %H:%M:%S")},
                          f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    # ---------------- 視角靈敏度（實測值，程式預設的 12 太小）
    def load_sensitivity(self):
        """只採用最近 20 筆樣本的中位數，而且限制在合理範圍。

        踩過的坑：舊校準檔混了不同狀態的樣本，中位數算出 26 像素/1%，
        但實際遊玩時量到的是 3~4 像素/1%（差七倍）——照舊值轉頭每次都會甩過頭。
        程式在遊玩中會持續用畫面位移重新量測並更新 self.px_per_pct。
        """
        import statistics as st
        try:
            with open(os.path.join(self.dir, "視角校準.json"), encoding="utf-8") as f:
                d = json.load(f)
            rows = d.get("樣本", [])
            try:
                self.dx_per_deg = float((d.get("水平") or {}).get("每度dx") or 0) or None
            except (TypeError, ValueError, AttributeError):
                self.dx_per_deg = None
            rs = [x["dx"] / x["畫面轉%"] for x in rows[-20:] if x.get("畫面轉%", 0) >= 3]
            if len(rs) >= 5:
                self.px_per_pct = max(1.5, min(15.0, round(st.median(rs), 1)))
            # 2026-09-14 晚：垂直靈敏度用機械掃描量到（上下全程 178 度 ≈ dy 600 → 每度 3.37）。
            # 以前用水平的 ppp 換算，少算一半——「一口氣送 600 回水平」其實是從天花板直接掃到地板。
            try:
                self.dy_per_deg = float((d.get("垂直") or {}).get("每度dy") or 0) or None
                # 2026-09-27：09/27 12:11 量到全程 dy 50（每度 0.28）→ 程式叫他「抬頭 15 度送 dy +4」，
                # 送了等於沒送、水平儀又以為修好了，他就一直停在天花板。合理範圍外一律不採用，退回 09/14 實測的 3.37。
                if self.dy_per_deg and not (1.5 <= self.dy_per_deg <= 25):
                    print(f"   ⚠ 視角校準.json 的垂直每度 dy {self.dy_per_deg} 不合理（全程 dy {178 * self.dy_per_deg:.0f}），改用 3.37")
                    self.dy_per_deg = 3.37
            except (TypeError, ValueError, AttributeError):
                self.dy_per_deg = None
            if getattr(self, "dx_per_deg", None):
                self.px_per_pct = max(1.5, min(15.0, round(self.dx_per_deg * self.DEG_PER_PCT, 1)))
            if len(rs) >= 5 or getattr(self, "dx_per_deg", None):
                print(f"[校準] 靈敏度：拖 {self.px_per_pct} 像素 ≈ 畫面轉 1%"
                      + (f"｜水平每度 dx {self.dx_per_deg}" if getattr(self, "dx_per_deg", None) else "")
                      + (f"｜垂直每度 dy {self.dy_per_deg}" if getattr(self, "dy_per_deg", None) else ""))
                return
        except (OSError, json.JSONDecodeError, ZeroDivisionError, KeyError):
            pass
        self.px_per_pct = 4.0          # 沒樣本時用保守的小值，寧可轉不夠也不要甩過頭
        print("[校準] 沒有可用樣本，用保守值 4 像素/1%")

    # 一次轉頭的上限改成「佔畫面幾成」，再依實測靈敏度換算成像素——
    # 寫死像素會因為靈敏度估錯而甩過頭（曾經估成 26 像素/1%，實際只有 3.7）。
    TURN_PCT = 30         # 左右一次最多轉 30% 畫面
    PITCH_PCT = 10        # 上下一次最多轉 10% 畫面

    @property
    def MAX_DX(self):
        return int(max(30, min(300, (getattr(self, "px_per_pct", 4.0) or 4.0) * self.TURN_PCT)))

    @property
    def MAX_DY(self):
        return int(max(15, min(120, (getattr(self, "px_per_pct", 4.0) or 4.0) * self.PITCH_PCT)))

    SNAP = {  # 他講的東西 → 眼睛的類別（有框到就把座標吸附過去）
        "咖啡機": "coffee machine", "咖啡": "coffee machine", "coffee": "coffee machine",
        "櫃檯": "counter desk", "桌": "table", "桌子": "table",
        "門": "door", "出口": "door", "窗": "window", "窗口": "window",
        "印表機": "printer", "電腦": "computer monitor", "螢幕": "computer monitor",
        "監視": "computer monitor", "椅": "chair", "椅子": "chair",
        "病人": "cat", "貓": "cat", "狗": "dog", "兔": "rabbit", "動物": "animal", "人": "person",
    }

    def snap_to_eyes(self, a):
        """他報的座標常常是用猜的。眼睛（YOLO）如果剛好框到同一類東西，就把座標吸附到框的中心。

        這正是「讓 YOLO 輔助他看」：判斷還是他做，位置交給偵測器，準很多。
        """
        if not self.g("吸附") or a.get("t") not in ("aim", "click"):
            return a
        g = str(a.get("g") or "")
        label = next((v for k, v in self.SNAP.items() if k in g), None)
        if not label:
            return a
        d = self.eyes.best(label, max_age=2.0)
        if not d:
            return a
        try:
            ox, oy = float(a.get("x", 0.5)), float(a.get("y", 0.5))
        except (TypeError, ValueError):
            ox = oy = 0.5
        if abs(ox - d["cx"]) < 0.04 and abs(oy - d["cy"]) < 0.04:
            return a
        ny = d["cy"]
        desc, need = self.pitch_from_eyes()
        lim2 = 0.42 if need == 0 else (0.35 if need is None else 0.50)
        if self.g("抬頭") and ny < lim2 and need != -1:
            # 吸附是在夾角限制之後才跑的，如果直接寫回 YOLO 的原始座標，
            # 就會繞過「不准抬頭」的防護（他就是這樣又抬頭的）。
            print(f"   ⛔ 吸附座標 y={ny:.2f} 在畫面上半部（等於抬頭）→ 壓到 0.45")
            ny = 0.45
            self.up_count = getattr(self, "up_count", 0) + 1
            self.stat["想抬頭被擋"] = self.stat.get("想抬頭被擋", 0) + 1
        elif self.g("低頭") and ny > 0.66:
            # 只擋上半部、不擋下半部，等於每次吸附都把鏡頭往地板帶——這是低頭的第二個來源
            print(f"   ⛔ 吸附座標 y={ny:.2f} 太靠畫面下緣（等於低頭）→ 提到 0.62")
            ny = 0.62
            self.stat["想低頭被擋"] = self.stat.get("想低頭被擋", 0) + 1
        print(f"   🎯 眼睛框到「{label}」，把「{g}」的座標從 ({ox:.2f},{oy:.2f}) 吸附到 ({d['cx']:.2f},{ny:.2f})")
        self._last_snapped = True
        a["x"], a["y"] = d["cx"], ny
        return a

    def split_turn_move(self, a):
        """把「面對」和「移動」拆成兩步（防線「兩步」）：同一輪如果又轉頭又走路，只留轉頭。

        一邊轉一邊走會走成弧線、走偏、撞牆——先對準、下一輪再走，比較穩。
        （這個方法要配合 cycle 裡先掃一遍動作，見 self._has_turn。）
        """
        if (self.g("兩步") and a.get("t") == "key"
                and str(a.get("k", "")).lower() in ("w", "s", "a", "d") and getattr(self, "_has_turn", False)):
            print(f"   ✂ 這一輪已經在轉頭了，先不要移動（{self.describe(a)} 這次不做，下一輪對準了再走）")
            self.turn_move_hint = ("⚠ 上一輪你想一邊轉頭一邊移動，移動被拿掉了。**先對準，下一輪再走**。" + NL)
            return None
        return a

    # ---------------- 一個動作拆兩段、中間看一眼、寧少勿多（2026-10-12 使用者）
    # 使用者：「讓他把一個動作拆成兩個並且來回檢視，來確認是否達到；寧願少、不要多——
    # 往前走到 100 他可以先走 70 再走 20，也不要走 110。」
    # 程式照這條做：走路（w/a/d ≥ 0.6 秒）與轉頭（|dx| ≥ 80 或 |dy| ≥ 60）先做七成，停下來抓一張畫面：
    # ①程式先看畫面有沒有變（沒變＝被擋／頂到底，後半段不做）②把畫面給他看，問剩下的兩成還要不要做；
    # 他說要才做，答不出來或逾時一律不做。合計最多九成，永遠不會超過他要的量。
    TWO_PART_FIRST, TWO_PART_SECOND = 0.7, 0.2
    TWO_PART_SYS = ("你是正在玩 Roblox 的玩家自己的眼睛。看一張畫面回答一個是非題，"
                    "原則是「寧願少、不要多」：拿不準就答 false。只輸出 JSON。")

    def two_part_on(self):
        if (os.environ.get("GAMER_TWO_PART", "on") or "on").strip().lower() in ("off", "0", "no"):
            return False
        return bool((getattr(self, "gcfg", None) or {}).get("拆兩段", True))

    def two_part_ask(self, desc, done, rest, mid):
        """中間的檢視：做了七成之後的畫面給他看，問剩下的兩成還要不要做。答不出來、逾時 → 不做。"""
        try:
            uri, _ = jpeg_data_uri(draw_grid(mid), 60)
            dl = float(os.environ.get("GAMER_TWO_PART_DEADLINE", "15") or 15)
            raw, _, _, _ = stream_json(
                self.endpoint, self.model, self.TWO_PART_SYS,
                [{"type": "image_url", "image_url": {"url": uri}},
                 {"type": "text", "text": (
                     f"你這一輪要做「{desc}」。程式先替你做了七成（{done}），這是做完七成之後現在的畫面。"
                     f"剩下的兩成（{rest}）還要不要做？" + NL
                     + "原則：**寧願少、不要多**。目標已經到了／已經對正／再做會撞到或過頭／畫面跳出沒看過的東西／"
                     + "畫面被面板蓋住 → false；還差一點、方向對、前面是空的 → true。" + NL
                     + '只輸出 JSON：{"再做":true} 或 {"再做":false,"為什麼":"10 字以內"}')}],
                60, time.time() + dl, 0.2, effort=反射)
            o = parse_json_loose(raw) or {}
            go = o.get("再做")
            if isinstance(go, str):
                go = go.strip().lower() in ("true", "是", "要", "yes", "1")
            return bool(go), str(o.get("為什麼") or "")[:20]
        except Exception as e:  # noqa: BLE001
            print(f"   ✂ 拆兩段檢視失敗（寧少勿多，剩下的不做）：{e}")
            return False, "檢視失敗"

    def _two_part_note(self, desc, what, asked):
        self.stat["拆兩段"] = self.stat.get("拆兩段", 0) + 1
        print(f"   ✂ {what}")
        self.two_part_hint = (f"✂ 上一輪「{desc}」程式替你拆成兩段（先七成、看一眼、再兩成，合計九成，寧少勿多）：{what}。"
                              "還差的量下一輪看畫面再補。" + NL)
        try:
            self._prog_log = (getattr(self, "_prog_log", []) + [(f"「{desc}」拆兩段", what[:60])])[-6:]
        except Exception:  # noqa: BLE001
            pass
        try:
            with open(os.path.join(self.dir, "拆兩段紀錄.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps({"時間": datetime.now().strftime("%H:%M:%S"), "輪": self.stat.get("輪數", 0),
                                    "動作": desc, "問了他": asked, "結果": what}, ensure_ascii=False) + NL)
        except OSError:
            pass

    def walk_two_part(self, k, d, a=None):
        """走路拆兩段：先走七成 → 看一眼（程式＋他自己）→ 再走兩成。回傳 (實際走了幾秒, 原因)。"""
        try:
            d = float(d or 0.5)
        except (TypeError, ValueError):
            d = 0.5
        if not self.two_part_on() or d < 0.6 or k not in ("w", "a", "d"):
            return self.walk_guarded(k, d)
        d1, d2 = round(d * self.TWO_PART_FIRST, 2), round(d * self.TWO_PART_SECOND, 2)
        desc = f"按 {k} {d:.1f}s"
        print(f"   ✂ 拆兩段：{desc} → 先走 {d1:.1f}s，看一眼，再走 {d2:.1f}s（合計 {d1 + d2:.1f}s，寧少勿多）")
        before = self.grab_still(320)
        real1, reason1 = self.walk_guarded(k, d1)
        if reason1:
            self._two_part_note(desc, f"前半段走 {real1:.1f}s 就被叫停（{reason1[:24]}），後半段沒走", False)
            return real1, reason1
        time.sleep(0.25)
        mid = self.grab_still(320)
        if frame_diff(before, mid) < 2.0:
            self._two_part_note(desc, f"走了 {real1:.1f}s 畫面沒變＝被擋住，後半段 {d2:.1f}s 不走", False)
            return real1, "拆兩段：前半段畫面沒變，後半段不走"
        go, why = self.two_part_ask(desc, f"按 {k} {real1:.1f}s", f"按 {k} {d2:.1f}s", mid)
        if not go:
            self._two_part_note(desc, f"走了 {real1:.1f}s，你看了畫面說不用再走" + (f"（{why}）" if why else ""), True)
            return real1, ""
        real2, reason2 = self.walk_guarded(k, d2)
        self._two_part_note(desc, f"走 {real1:.1f}s → 你看了一眼說繼續 → 再走 {real2:.1f}s"
                            + (f"（被叫停：{reason2[:20]}）" if reason2 else ""), True)
        return round(real1 + real2, 2), reason2

    def look_two_part(self, a):
        """轉頭拆兩段：先轉七成 → 看一眼 → 再轉兩成。小幅度（|dx|<80 且 |dy|<60）不拆，回 False 照舊做。"""
        try:
            dx, dy = float(a.get("dx", 0) or 0), float(a.get("dy", 0) or 0)
        except (TypeError, ValueError):
            return False
        if abs(dx) < 80 and abs(dy) < 60:
            return False
        desc = f"轉視角 {int(dx)},{int(dy)}"
        s1 = {"t": "look", "dx": int(round(dx * self.TWO_PART_FIRST)), "dy": int(round(dy * self.TWO_PART_FIRST))}
        s2 = {"t": "look", "dx": int(round(dx * self.TWO_PART_SECOND)), "dy": int(round(dy * self.TWO_PART_SECOND))}
        print(f"   ✂ 拆兩段：{desc} → 先轉 {s1['dx']},{s1['dy']}，看一眼，再轉 {s2['dx']},{s2['dy']}（寧少勿多）")
        before = self.grab_still(320)
        self.execute([s1])
        time.sleep(0.3)
        mid = self.grab_still(320)
        if frame_diff(before, mid) < 2.0:
            self._two_part_note(desc, f"轉了 {s1['dx']},{s1['dy']} 畫面沒變＝鏡頭頂到底或被鎖住，後半段不轉", False)
            return True
        go, why = self.two_part_ask(desc, f"轉 {s1['dx']},{s1['dy']}", f"轉 {s2['dx']},{s2['dy']}", mid)
        if not go:
            self._two_part_note(desc, f"轉了 {s1['dx']},{s1['dy']}，你看了畫面說不用再轉" + (f"（{why}）" if why else ""), True)
            return True
        self.execute([s2])
        self._two_part_note(desc, f"轉 {s1['dx']},{s1['dy']} → 你看了一眼說繼續 → 再轉 {s2['dx']},{s2['dy']}", True)
        return True

    def check_click(self, actions, before):
        """點完之後看畫面有沒有變：沒變就代表點歪了（差幾十像素），直接告訴他怎麼微調。"""
        clicks = [a for a in actions if isinstance(a, dict) and a.get("t") == "click"]
        if not clicks:
            return
        try:
            after = self.grab_still(320)
        except Exception:  # noqa: BLE001
            return
        d_click = frame_diff(before, after)
        if d_click >= 3.0:
            self.click_miss = 0
            self.click_hint = ""
            a_ok = clicks[-1]
            try:
                self.watch_click(a_ok.get("g", ""), a_ok.get("x"), a_ok.get("y"))
            except Exception:  # noqa: BLE001
                pass
            try:      # 2026-09-18：點了**有反應**才把座標記進按鈕簿
                self._last_click_xy = [float(a_ok.get("x", 0.5)), float(a_ok.get("y", 0.5))]
                self.note_button(a_ok.get("g", ""), self._last_click_xy[0],
                                 self._last_click_xy[1], True, diff=d_click)
            except Exception as e:  # noqa: BLE001
                print(f"   記按鈕失敗：{e}")
            return
        a = clicks[-1]
        self.click_miss = getattr(self, "click_miss", 0) + 1
        try:
            self.watch_click(a.get("g", ""), a.get("x"), a.get("y"))
        except Exception:  # noqa: BLE001
            pass
        try:
            x, y = float(a.get("x", 0.5)), float(a.get("y", 0.5))
        except (TypeError, ValueError):
            return
        alt = f"({x:.2f},{min(0.97, y + 0.03):.2f}) 或 ({x:.2f},{max(0.03, y - 0.03):.2f})"
        self.click_hint = (f"⚠ 你點了「{a.get('g', '')}」({x:.2f},{y:.2f}) 但畫面完全沒變＝**點歪了**"
                           f"（差個幾十像素就會落在按鈕外面）。第 {self.click_miss} 次了。"
                           f"不要用同一個座標再點：試 {alt}，"
                           f"或先用 {{\"t\":\"放大\",\"x\":{x:.2f},\"y\":{y:.2f}}} 看清楚按鈕的正中央在哪再點。" + NL)
        print(f"   🎯 點了沒反應（第 {self.click_miss} 次）：{a.get('g','')} ({x:.2f},{y:.2f})")
        try:
            self._last_click_xy = [x, y]
            self.note_button(a.get("g", ""), x, y, False)
        except Exception:  # noqa: BLE001
            pass

    def guard_leave(self, actions):
        """離開遊戲要有正當理由，不然他會一直退出重進什麼都不做。

        允許離開的情況：理智 0（死了）／卡住超過 3 分鐘／整場只有自己一個人。
        """
        if not self.g("離開"):
            return actions
        san = self.san
        # 使用者指定：除非理智歸零，否則一律不准自己退出遊戲。
        # 例外：遊戲已經結束／失敗（畫面出現出口、Exit、遊戲結束、你失敗了、輪班失敗、
        # 觀戰之類的字樣）——那時候留在裡面什麼都做不了，出口按鈕就是給你按的。
        seen = str(getattr(self, "last_seen", ""))
        # 只有「遊戲真的結束／失敗」才算。光看到「出口」兩個字不算——
        # 他面壁的時候也會說「有出口」，照著放行等於讓他隨便退出（00:57 就發生過）。
        strong = ("遊戲結束", "Game Over", "GAME OVER", "遊戲失敗", "輪班失敗", "你失敗",
                  "觀戰", "旁觀", "spectate", "你死了", "死亡畫面", "已死亡", "角色倒地")
        hit = any(k in seen for k in strong)
        # 而且要連續兩輪都這樣講才算數（他的描述常常是猜的）
        self._fail_streak = (getattr(self, "_fail_streak", 0) + 1) if hit else 0
        failed = self._fail_streak >= 3 or bool(self.has_text('game over', '遊戲結束', 'you died', 'spectate'))
        # 2026-09-16 使用者：**卡住就讓他重開遊戲**。原本要 480 秒（8 分鐘）完全沒進展才放行，
        # 那太久了——他會對著同一個東西耗掉整個小時。改成 120 秒，
        # 而且「程式判定卡住」（卡住檢討剛跑過）也直接放行。
        frozen = (self.frozen_secs() >= 120
                  or time.time() - getattr(self, "_stuck_at", 0) < 180)
        black = self.screen_black()          # 2026-09-19 使用者：全黑畫面＝直接准他退出重進
        lobby = self.in_lobby()
        ok = ((san is not None and san <= 1) or failed or frozen or black) and not lobby
        if lobby:
            self.leave_hint = ("⚠ 你已經**在大廳**了（看得到商店／邀請／伺服器那排東西）。"
                               "大廳沒有東西可以離開——這裡按「離開」是退出整個遊戲。"
                               "現在要做的是**重新加入**：比對【你記過的遊戲身分】、"
                               "往下捲伺服器清單挑**人多**的場加入。" + NL)
        if black:
            print("   🚪 畫面幾乎全黑 → 准他退出重進，不扣分（使用者規則）")
            self.leave_hint = ("🖤 **畫面幾乎全黑**——這種時候留在裡面什麼都做不了。"
                               "直接退出重進：esc → l → enter，回到遊戲頁面按藍色箭頭。"
                               "**這樣退出不會扣分。**" + NL)
        elif frozen and not failed:
            print(f"   🚪 {int(self.frozen_secs())} 秒完全沒有任何進展 → 准他退出重進（使用者規則）")
        if failed:
            print(f"   🚪 遊戲真的結束／失敗（{seen[:20]}）→ 准他用出口離開")
        out = []
        for a in actions:
            g = str(a.get("g") or "")
            leaving = a.get("t") == "click" and any(k in g for k in ("離開", "Leave", "leave", "退出",
                                                                      "出口", "Exit", "exit"))
            if leaving and not ok:
                try:
                    self.punish("沒事退出遊戲",
                                f"沒有失敗畫面、才 {int(self.frozen_secs())} 秒沒進展"
                                f"（卡住滿 120 秒、或畫面全黑才算數）。"
                                f"退出重進本身是好工具，只是不要在還有事可做的時候用。")
                except Exception:  # noqa: BLE001
                    pass
                out.append(a)
                continue
            out.append(a)
        return out

    UI_WORDS = ("選單", "視窗", "面板", "對話框", "UI", "列表", "遮擋", "擋住", "彈窗", "弹窗")

    @staticmethod
    def _btn_cands(hsv, mask, w, h, ker):
        import cv2
        import numpy as np
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones(ker, np.uint8))
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        out = []
        for c in cnts:
            x, y, bw, bh = cv2.boundingRect(c)
            area = bw * bh
            if not (0.0015 * w * h < area < 0.20 * w * h):
                continue
            if not (1.3 <= bw / max(1, bh) <= 9.0) or bh < h * 0.02:
                continue
            if cv2.contourArea(c) / max(1, area) < 0.5:
                continue
            roi = hsv[y:y + bh, x:x + bw]
            out.append({"cx": round((x + bw / 2) / w, 3), "cy": round((y + bh / 2) / h, 3),
                        "w": round(bw / w, 3), "h": round(bh / h, 3),
                        "hue": int(np.median(roi[:, :, 0])), "sat": int(np.median(roi[:, :, 1])),
                        "val": int(np.median(roi[:, :, 2]))})
        return out

    def e_prompt(self, max_age=1.0):
        """畫面上有沒有那個白色的「E」互動提示（有提示＝按得到，沒提示＝按了一定沒用）。"""
        if time.time() - getattr(self, "_e_at", 0) < max_age:
            return getattr(self, "_e_hits", [])
        self._e_at = time.time()
        self._e_hits = []
        try:
            import cv2
            import numpy as np
            im = self.grab_still(640)
            img = cv2.cvtColor(np.asarray(im), cv2.COLOR_RGB2BGR)
            h, w = img.shape[:2]
            hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
            s, v = hsv[:, :, 1], hsv[:, :, 2]
            white = ((s < 60) & (v > 170)).astype(np.uint8) * 255
            white = cv2.morphologyEx(white, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
            cnts, _ = cv2.findContours(white, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            out = []
            for c in cnts:
                x, y, bw, bh = cv2.boundingRect(c)
                if not (w * 0.012 <= bw <= w * 0.06) or not (h * 0.02 <= bh <= h * 0.09):
                    continue
                if not (0.7 <= bw / max(1, bh) <= 1.5):
                    continue
                if cv2.contourArea(c) / max(1, bw * bh) < 0.55:
                    continue
                cx, cy = (x + bw / 2) / w, (y + bh / 2) / h
                if not (0.12 <= cy <= 0.85):
                    continue
                if cx < 0.18 and cy < 0.20:
                    continue          # 左上角是 Roblox 自己的按鈕列，不是互動提示
                out.append({"cx": round(cx, 3), "cy": round(cy, 3)})
            self._e_hits = out[:3]
        except Exception as e:  # noqa: BLE001
            print(f"   找 E 提示失敗：{e}")
        return self._e_hits

    def e_prompt_text(self):
        hits = self.e_prompt()
        if not hits:
            return ("❌ **畫面上現在沒有 E 提示** → 你按 E 一定沒有反應。"
                    "先靠近一步、用 a/d 橫移對正、或換一個目標；提示跳出來再按。")
        pos = "、".join(f"({h['cx']},{h['cy']})" for h in hits)
        return (f"✅ **畫面上有 E 提示**（在 {pos}）→ 現在按 E 就有效："
                f'{{"t":"hold","k":"e","g":"提示指的東西"}}，按 1.5 秒就好。')

    def ui_buttons(self, max_age=1.5):
        """用顏色＋形狀框出畫面上的按鈕（YOLO 不認 UI，這是補那個洞）。"""
        if time.time() - getattr(self, "_btn_at", 0) < max_age:
            return getattr(self, "_btns", [])
        self._btn_at = time.time()
        self._btns = []
        try:
            import cv2
            import numpy as np
            im = self.grab_still(640)
            img = cv2.cvtColor(np.asarray(im), cv2.COLOR_RGB2BGR)
            h, w = img.shape[:2]
            hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
            s, v = hsv[:, :, 1], hsv[:, :, 2]
            # 亮按鈕（一般畫面）與暗但飽和的按鈕（死亡畫面會把全螢幕壓暗）
            a = self._btn_cands(hsv, ((s > 80) & (v > 90)).astype(np.uint8) * 255, w, h, (3, 25))
            b = self._btn_cands(hsv, ((s > 55) & (v > 15) & (v <= 90)).astype(np.uint8) * 255,
                                w, h, (3, 21))
            out = []
            for c in a + b:
                if any(abs(c["cx"] - o["cx"]) < 0.05 and abs(c["cy"] - o["cy"]) < 0.05 for o in out):
                    continue
                out.append(c)
            out.sort(key=lambda d: -(d["w"] * d["h"]))
            self._btns = out[:5]
        except Exception as e:  # noqa: BLE001
            print(f"   找按鈕失敗：{e}")
        return self._btns

    def buttons_text(self):
        bs = self.ui_buttons()
        note = ""
        if sum(1 for b in bs if 120 <= b["hue"] <= 165 and b["cy"] > 0.55) >= 2:
            note = (NL + "⚠ 現在有兩顆紫色按鈕上下排著：**上面那顆是「自我復活」（要花錢，絕對不准點）**，"
                    "**下面那顆才是「前往大廳」**，要離開就點下面那顆。")
        if not bs:
            return "（程式現在沒有框到明顯的按鈕）"
        rows = []
        for i, b in enumerate(bs, 1):
            colour = ("紫色" if 120 <= b["hue"] <= 165 else
                      "藍色" if 90 <= b["hue"] < 120 else
                      "紅色" if b["hue"] <= 10 or b["hue"] >= 170 else
                      "綠色" if 35 <= b["hue"] < 90 else "橘黃")
            rows.append(f'B{i}) {colour}按鈕 在 ({b["cx"]},{b["cy"]})　大小 {b["w"]}x{b["h"]}'
                        f'　→ 點它就用 {{"t":"click","x":{b["cx"]},"y":{b["cy"]},"g":"這顆按鈕"}}')
        return NL.join(rows) + note + NL + "（這是程式用顏色框出來的，字寫什麼要你自己看畫面判斷）"

    def lobby_button_hint(self):
        """死亡畫面上該點哪一顆——**程式只告訴他，不代點**（要他自己記住）。

        ⚠ 死亡畫面上有**兩顆**紫色按鈕，上面那顆是「自我復活」（要花錢，絕對不准點），
        下面那顆才是「前往大廳」。所以一律挑**最下面**的那顆。
        """
        cands = [b for b in self.ui_buttons(max_age=1.0)
                 if 120 <= b["hue"] <= 165 and b["cy"] > 0.55 and b["w"] > 0.08]
        if not cands:
            return ("☠ 你死了。死亡畫面上有兩顆紫色按鈕，上下疊著："
                    "**上面那顆是「自我復活」——要花錢，絕對不准點**；"
                    "**下面那顆是「前往大廳」——點這一顆**，回大廳再重新加入。" + NL)
        b = max(cands, key=lambda x: x["cy"])
        y = b["cy"] + (b["h"] * 0.22 if b["h"] > 0.13 else 0)
        y = min(0.96, y)
        return (f"☠ 你死了。程式框到那組紫色按鈕在 x≈{b['cx']}、y≈{b['cy']}（整塊高 {b['h']}）。"
                f"**上面那顆是「自我復活」，要花錢，絕對不准點**；"
                f"**下面那顆是「前往大廳」，你要點的是它** → "
                f'{{"t":"click","x":{b["cx"]},"y":{round(y, 3)},"g":"前往大廳"}}。'
                f"點完回大廳，比對【你記過的遊戲身分】再重新加入。" + NL)

    def ui_rescue(self):
        """他被 Roblox 自己的選單／好友面板蓋住時會完全出不來（esc 又被限流擋著）。
        連續 4 輪都說有 UI 蓋著就由程式按一次 esc 幫他關掉——限流只擋他，不擋這裡。"""
        seen = str(getattr(self, "last_seen", ""))
        if any(k in seen for k in self.UI_WORDS):
            self.ui_stuck = getattr(self, "ui_stuck", 0) + 1
        else:
            self.ui_stuck = 0
            return
        # 連續好幾次都關不掉 → 那多半**不是 UI**，是場景裡的 3D 物件（看板、告示、螢幕）。
        # 2026-09-10 實例：他對著大廳的排行榜看板按了 41 次 esc、耗掉整整 30 分鐘。
        if getattr(self, "_ui_rescue_n", 0) >= 3:
            self._ui_rescue_n = 0
            self.ui_stuck = 0
            self.ui_hint_extra = (
                "⚠ 你已經試了很多次都關不掉——**那八成不是 UI 面板，是場景裡的一塊看板／螢幕／告示**"
                "（大廳的排行榜就是這種東西）。判斷方法：轉一下視角，**會跟著畫面一起移動的就是場景物件**，"
                "不會動的才是 UI。場景物件關不掉也不用關：**轉身走開就好**。" + NL)
            self.add_note("關不掉的多半不是UI是場景看板：轉頭會跟著動的就是場景，走開就好")
            print("   🪧 連續關不掉 → 判定那是場景裡的看板，不是 UI（已告訴他走開）")
            return
        if self.ui_stuck >= 4 and time.time() - getattr(self, "_ui_rescue_at", 0) > 30:
            self._ui_rescue_n = getattr(self, "_ui_rescue_n", 0) + 1
            self._ui_rescue_at = time.time()
            hard = self.ui_stuck >= 8      # 已經卡很久：加重力道
            self.ui_stuck = 0
            print(f"   🧹 連續被 UI 蓋著 → 程式幫你關掉（{'加強版' if hard else '一般'}）")
            self.press_key("esc", 0.08)
            time.sleep(0.7)
            if hard:
                # Roblox 自己的選單有時候一次 esc 關不掉：再按一次，並點畫面中央的空白處
                self.press_key("esc", 0.08)
                time.sleep(0.7)
                try:
                    self.execute([{"t": "click", "x": 0.5, "y": 0.5, "g": "面板外的空白處"}])
                except Exception:  # noqa: BLE001
                    pass
            self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] "
                                f"你被面板蓋住出不來，程式幫你按了一次 esc；"
                                f"下次先點面板右上角的叉叉，不要一直按 esc")

    def ui_open_hint(self):
        """他常常沒發現畫面上蓋著 UI 就一直按東西。這裡只提醒，關法由他自己選。"""
        seen = str(getattr(self, "last_seen", ""))
        extra = getattr(self, "ui_hint_extra", "") or ""
        if extra:
            self.ui_hint_extra = ""
            return extra
        if any(k in seen for k in self.UI_WORDS):
            return ("⚠ 你上一輪說畫面有 UI 蓋著。**先關掉再做事**——"
                    "優先找那塊面板右上角的**叉叉 X** 點掉，其次點「關閉／返回／繼續」，"
                    "再不然點面板外的空白處；**esc 放最後**（它是開關，容易越按越糟）。" + NL)
        return ""

    def guard_pay_click(self, actions):
        """座標防線：死亡畫面上面那顆紫色按鈕是「自我復活」（要花錢），點下去一律擋掉。

        關鍵字防線只看他自己寫的標籤（他可能寫「紫色按鈕」就矇混過去），
        所以這裡用程式框到的按鈕位置判斷：落在**上面那顆**的範圍就不准點。
        （花錢攔截是不可關的防線。）
        """
        bs = [b for b in self.ui_buttons(max_age=1.0)
              if 120 <= b["hue"] <= 165 and b["cy"] > 0.55 and b["w"] > 0.08]
        if not bs:
            return actions
        bands = []          # (x0, x1, y0, y1) = 不准點的範圍（自我復活那顆）
        block = max(bs, key=lambda x: x["cy"])
        if block["h"] > 0.13:      # 兩顆疊在一起被框成同一塊：上半＝自我復活
            bands.append((block["cx"] - block["w"] / 2 - 0.02, block["cx"] + block["w"] / 2 + 0.02,
                          block["cy"] - block["h"] / 2 - 0.01, block["cy"] - block["h"] * 0.06))
        elif len(bs) >= 2:         # 分開框到：cy 比較小的那顆＝上面那顆
            up = min(bs, key=lambda x: x["cy"])
            bands.append((up["cx"] - up["w"] / 2 - 0.02, up["cx"] + up["w"] / 2 + 0.02,
                          up["cy"] - up["h"] / 2 - 0.02, up["cy"] + up["h"] / 2 + 0.02))
        if not bands:
            return actions
        out = []
        for a in actions:
            if a.get("t") != "click":
                out.append(a)
                continue
            try:
                x, y = float(a.get("x", -1)), float(a.get("y", -1))
            except (TypeError, ValueError):
                out.append(a)
                continue
            if any(x0 <= x <= x1 and y0 <= y <= y1 for x0, x1, y0, y1 in bands):
                self.stat["擋錢"] = self.stat.get("擋錢", 0) + 1
                print(f"   💰⛔ 擋掉：({x:.2f},{y:.2f}) 落在死亡畫面**上面那顆「自我復活」**上——那要花錢")
                self.pay_hint = ("💰⛔ 你剛才點的是死亡畫面**上面那顆「自我復活」**——那要花錢，"
                                 "程式已經擋掉了。**要離開請點下面那顆「前往大廳」**"
                                 "（看【程式框到的按鈕】給你的座標，往下一點）。" + NL)
                self.add_note("死亡畫面上面那顆是自我復活(要花錢不准點)，下面那顆前往大廳才是出口")
                continue
            out.append(a)
        return out

    JOB_WORDS = ("職業", "護士", "醫生", "實習", "警衛", "清潔", "Job", "job", "Nurse", "Doctor", "Intern")

    def ban_text(self):
        """2026-09-27 使用者：「禁止的東西他有知道嗎？用 prompt 去約束操作」——
        遊戲.json 裡開著的禁令，每輪放在面板最前面先告訴他，不要等他做了才被擋。"""
        rows = []
        if self.gcfg.get("禁止換職業"):
            rows.append("- **不准換職業**：不要點「職業」那顆按鈕，也不要點護士／醫生／實習生那類選項；"
                        "維持現在的職業去做事。點了程式會直接擋掉，只是白費一輪。")
        if self.gcfg.get("禁止上下轉頭"):
            rows.append("- **不准上下轉頭**：dy 一律無效，只有左右（dx）會轉。")
        out = ("【使用者禁止的事（程式會擋，做了只是浪費一輪）】" + NL + NL.join(rows)) if rows else ""
        mf = self.map_force_text()
        return (out + (NL + NL if out and mf else "") + mf) if (out or mf) else ""

    def map_force_text(self):
        """2026-09-27 使用者：「腦內地圖 學習時強制每次使用，讓他知道場地佈局和他在哪」。"""
        if not self.gcfg.get("強制腦內地圖"):
            return ""
        names = [k for k in (getattr(self, "map", {}) or {}) if self.same_scene(k)]
        return ("【腦內地圖：每一輪都要用（使用者規定）】" + NL
                + '每一輪的 JSON 都要多兩個欄位：**"我在":"地點名"**（你現在在哪）、**"要去":"地點名"**（你要去哪）。' + NL
                + "**你不用想怎麼走**——「要去」寫的是地圖上有的地點，程式會用地圖算出方向和距離，直接帶你走過去"
                + "（這一輪你自己的走路、轉頭動作會被取代）。不需要移動就寫 \"要去\":\"這裡\"，照你自己的動作做（點東西、按 E…）。" + NL
                + "要去的地方地圖上還沒有，就寫 \"這裡\"，自己走過去，到了用「我在」記成新地點。" + NL
                + "- 認得出是記過的地點 → 寫那個名字（程式會把你的座標校正到那裡）。" + NL
                + '- 不在任何記過的地點 → 寫 **"新:你取的名字"**（例如 "新:大廳南邊的門"），程式會把現在的位置記起來。' + NL
                + "- 沒寫的話程式會多問你一次，只是白費時間。" + NL
                + "你記過的地點：" + ("、".join(names[:40]) if names else "（還沒有，第一輪就寫一個新的）") + NL
                + ((getattr(self, "map_mismatch", "") or "") + NL if getattr(self, "map_mismatch", "") else "")
                + ((getattr(self, "vision_note", "") or "") + NL if getattr(self, "vision_note", "") else "")
                + self.map_nav_text())

    def yolo_sig(self, n=4):
        """現在眼睛框到的東西（排除自己），取最明顯的幾個：[[詞, cx, h], ...]——當地點的視覺特徵。"""
        dets, stamp = self.eyes.latest()
        if not stamp or time.time() - stamp > 2.5:
            return []
        rows = []
        for lab, v in (dets or {}).items():
            for d in v[:1]:
                if self.eyes.is_self(d) or d.get("score", 0) < 0.22:
                    continue
                rows.append([lab, round(d["cx"], 2), round(d["h"], 2), d.get("score", 0)])
        rows.sort(key=lambda r: -(r[2] * r[3]))
        return [r[:3] for r in rows[:n]]

    def remember_sig(self, nm):
        sig = self.yolo_sig()
        if sig:
            self.map_sig = getattr(self, "map_sig", {}) or {}
            self.map_sig[nm] = sig

    VISION_CHECK_EVERY = 6

    def vision_check(self):
        """2026-09-27 使用者：「他要用視覺定期檢查是不是對的，並且配合 YOLO 畫地圖和移動」。
        每 6 輪拿現在 YOLO 看到的東西，跟同場景各地點記下的視覺特徵比對：
        最像的地點夠像 → 把位置校正過去（視覺比推算準）；跟他說的「我在」不一樣 → 提醒他，並記成問題。"""
        if not self.gcfg.get("強制腦內地圖") or self.stat.get("輪數", 0) % self.VISION_CHECK_EVERY:
            return ""
        now = {r[0]: r for r in self.yolo_sig(6)}
        if not now:
            self.vision_note = "👁 視覺檢查：眼睛現在框不到東西（對著牆、地板或天花板？），先轉到看得到東西的方向。"
            return self.vision_note
        best, best_sc = None, 0.0
        for nm, sig in (getattr(self, "map_sig", {}) or {}).items():
            if nm not in self.map or not self.same_scene(nm):
                continue
            sc = 0.0
            for lab, cx, h in sig:
                if lab in now:
                    sc += 1.0 - min(1.0, abs(now[lab][1] - cx) + abs(now[lab][2] - h))
            if sc > best_sc:
                best, best_sc = nm, sc
        said = (getattr(self, "_map_used", "") or "").split("在:")[-1].split("｜")[0] if "在:" in (getattr(self, "_map_used", "") or "") else ""
        self.stat["視覺檢查"] = self.stat.get("視覺檢查", 0) + 1
        if best and best_sc >= 1.2:
            ox, oy = self.pos
            self.pos = [float(self.map[best][0]), float(self.map[best][1])]
            msg = f"👁 視覺檢查：你看到的東西（{'、'.join(now)}）最像「{best}」（相似 {best_sc:.1f}），位置已照畫面校正"
            if said and said != best:
                msg += f"——你剛才說在「{said}」，兩個對不上，以畫面為準"
                self.record_problem(f"腦內地圖對不上畫面：說在「{said}」，畫面像「{best}」", source="視覺檢查")
            print(f"   👁🗺 {msg}（{ox:+.1f},{oy:+.1f}）→（{self.pos[0]:+.1f},{self.pos[1]:+.1f}）")
            self.vision_note = msg
        else:
            self.vision_note = (f"👁 視覺檢查：你看到的東西（{'、'.join(now)}）跟記過的地點都不像——"
                                "可能是新地方，到了就用「我在」記一個新地點（程式會順便記下這裡看得到的東西）。")
            print(f"   👁🗺 視覺檢查：看到 {list(now)}，沒有像的地點")
        return self.vision_note

    def same_scene(self, nm):
        """這個地點跟我現在是不是同一個場景（Roblox place）。沒標記的舊地點當作未知＝不帶路。"""
        tag = (getattr(self, "map_scene", {}) or {}).get(nm)
        cur = self.current_place()
        return tag is not None and cur is not None and tag == cur

    def map_nav_text(self):
        """2026-09-27 使用者：「他能用地圖判斷該如何移動」——把最近幾個地點的方向與距離直接放在規則區，
        反射輪也看得到；要去哪就照方向走，或讓程式帶他走。"""
        import math
        places = getattr(self, "map", {}) or {}
        yaw = getattr(self, "yaw", 0.0)
        rows = ["**用地圖決定怎麼走**：要去哪裡，先看它在你的哪個方向、幾秒的路，再動——"
                '轉到那個方向走過去，或直接送 {"t":"走到記過的","name":"地點名"} 讓程式幫你轉向、走那麼多秒。'
                "不知道要去哪，就去離你最近、還沒探索過的方向，走到之後記一個新地點。"]
        items = []
        for nm, (x, y) in places.items():
            if not self.same_scene(nm):
                continue                        # 別的場景（例如大廳 vs 醫院裡）的地點不列，座標不相干
            vx, vy = x - self.pos[0], y - self.pos[1]
            dist = math.hypot(vx, vy)
            if dist < 0.3:
                continue
            ang = (math.degrees(math.atan2(vx, vy)) - yaw + 540) % 360 - 180
            a = abs(ang)
            side = ("正前方" if a <= 20 else ("右前方" if ang > 0 else "左前方") if a <= 70 else
                    ("正右邊" if ang > 0 else "正左邊") if a <= 110 else
                    ("右後方" if ang > 0 else "左後方") if a <= 160 else "正後方")
            items.append((dist, f"　{nm}：{side} {a:.0f}°、約 {dist:.1f} 秒"))
        items.sort()
        if items:
            rows += [r[1] for r in items[:5]]
        else:
            rows.append("　（記過的地點都跟你在同一個位置，或還沒有——先走動再記，地點才分得開）")
        return NL.join(rows)

    def enforce_map(self, o, uri):
        r = self._enforce_map(o, uri)
        dest = str(o.get("要去") or "").strip()
        if self.gcfg.get("強制腦內地圖") and dest and dest not in ("這裡", "原地", "不動") and dest in (self.map or {}):
            if not self.same_scene(dest):
                print(f"   🗺✋ 「{dest}」不在你現在這個場景（或沒標場景），不帶路")
            else:
                self._goto_place = dest
        return r + (f"｜要去:{dest}" if dest else "")

    def _enforce_map(self, o, uri):
        """每輪檢查他有沒有說「我在」；沒說就用反射重問一次。回傳這輪用了哪一種（校正／新增／重問後…／沒答）。"""
        if not self.gcfg.get("強制腦內地圖"):
            return ""
        self._goto_place = ""
        if getattr(self, "_reflex_check", False):
            return ""          # 2026-09-30：檢查輪只回 ok，不回「我在」，也不為了它再問一次模型
        here = str(o.get("我在") or "").strip()
        how = ""
        if not here:
            names = list((getattr(self, "map", {}) or {}).keys())
            q = ("只回答兩件事：你現在站在腦內地圖的哪裡？要去哪裡？" + NL
                 + "記過的地點：" + ("、".join(names[:40]) if names else "（還沒有）") + NL
                 + '我在：認得就寫那個名字；不在任何記過的地點就寫 "新:你取的名字"。'
                 + '要去：地圖上的地點名；不需要移動或要去的地方還沒記過就寫 "這裡"。'
                 + '只輸出 JSON：{"我在":"...","要去":"..."}')
            try:
                raw, _, _, _ = stream_json(self.endpoint, self.model, self.sys_prompt(反射),
                                           [{"type": "image_url", "image_url": {"url": uri}},
                                            {"type": "text", "text": q}], 80, time.time() + 20.0, 0.2, effort=反射)
                _ans = parse_json_loose(raw) or {}
                here = str(_ans.get("我在") or "").strip()
                if not str(o.get("要去") or "").strip():
                    o["要去"] = str(_ans.get("要去") or "").strip()
            except Exception as e:  # noqa: BLE001
                print(f"   地圖重問失敗：{e}")
            self.tick("地圖重問")
            how = "重問後"
            self.stat["地圖重問"] = self.stat.get("地圖重問", 0) + 1
        if not here:
            self.stat["地圖沒答"] = self.stat.get("地圖沒答", 0) + 1
            print("   🗺⚠ 這輪沒說自己在腦內地圖的哪裡（重問也沒答）")
            return "沒答"
        # 2026-09-27 13:20 修：每輪的「我在」**只當標籤，不拉座標**。
        # 之前每輪都把座標拉到他說的地點 → 地點都在 (0,0) → 他一走就被拉回原點 → 新地點又記在 (0,0)，整張地圖疊成一點。
        # 要校正座標，他自己送 {"t":"我在","name":...}（那個動作還是會拉）。
        import math
        if here.startswith(("新:", "新：")):
            here = here.split(":", 1)[-1].split("：", 1)[-1].strip()
        nm = here[:12]
        if nm in self.map:
            x, y = self.map[nm]
            d = math.hypot(x - self.pos[0], y - self.pos[1])
            if d <= 3.0 and nm not in (getattr(self, "map_sig", {}) or {}):
                self.remember_sig(nm)
            if d <= 3.0 and nm not in (getattr(self, "map_scene", {}) or {}):
                self.map_scene = getattr(self, "map_scene", {}) or {}
                self.map_scene[nm] = self.current_place()      # 舊地點補標場景（他確認人就在那附近）
                self.save_map()
            if d > 3.0:
                self.map_mismatch = (f"⚠ 你說你在「{nm}」，但腦內地圖推算你離它約 {d:.1f} 秒的路。"
                                     f"如果你確定就在那裡，送 {{\"t\":\"我在\",\"name\":\"{nm}\"}} 校正座標；"
                                     f"不確定就記一個新地點。")
                print(f"   🗺⚠ [地圖] 他說在「{nm}」，推算離那裡 {d:.1f}（沒拉座標）")
            else:
                self.map_mismatch = ""
            return how + "在:" + nm
        # 新地點：附近 1.5 以內已經有地點就不重複記（避免疊點）
        near = [k for k, (x, y) in self.map.items() if math.hypot(x - self.pos[0], y - self.pos[1]) < 1.5]
        if near:
            print(f"   🗺 [地圖] 他說在新地點「{nm}」，但這裡已經有「{near[0]}」，不重複記")
            return how + "在:" + near[0]
        self.map[nm] = (round(self.pos[0], 2), round(self.pos[1], 2))
        self.map_scene = getattr(self, "map_scene", {}) or {}
        self.map_scene[nm] = self.current_place()
        self.remember_sig(nm)
        self.save_map()
        print(f"   🗺 [地圖] 新地點「{nm}」記在 ({self.pos[0]:+.1f},{self.pos[1]:+.1f})（場景 {self.map_scene[nm]}，"
              f"看得到 {[r[0] for r in self.map_sig.get(nm, [])]}）")
        return how + "新增:" + nm

    def guard_job(self, actions):
        """2026-09-25 使用者：「要阻止他換職業」。遊戲.json 開「禁止換職業」時，
        標籤或要點的字跟職業有關的 click／點字，一律擋掉。"""
        if not self.gcfg.get("禁止換職業"):
            return actions
        out = []
        for a in actions:
            t = a.get("t")
            txt = str(a.get("g") or "") if t == "click" else (
                str(a.get("字") or "") + str(a.get("g") or "") if t in ("點字", "點文字") else "")
            if txt and any(w in txt for w in self.JOB_WORDS):
                self.stat["擋換職業"] = self.stat.get("擋換職業", 0) + 1
                print(f"   ⛔ 擋掉換職業：{t}「{txt[:20]}」")
                self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 使用者不准你換職業，"
                                    f"點職業／護士／實習生那類按鈕已經擋掉；維持現在的職業去做事。")
                continue
            out.append(a)
        return out

    def guard_esc(self, actions):
        """擋掉連按 esc：esc 是開關，關掉之後再按等於又打開，他會永遠出不來。

        實測：最近 600 輪裡有 187 輪只做「esc＋等待」，連續 7 次同一動作的段落有 17 段。
        """
        if not self.g("esc"):
            return actions
        out = []
        for a in actions:
            is_esc = a.get("t") == "key" and str(a.get("k", "")).lower() == "esc"
            if is_esc:
                self.esc_hits = [t for t in getattr(self, "esc_hits", []) if time.time() - t < 60]
                if len(self.esc_hits) >= 1:      # esc 是最後手段，一分鐘一次就夠
                    print(f"   ⛔ 一分鐘內已經按過 {len(self.esc_hits)} 次 esc，這次擋掉（esc 是開關，越按越糟）")
                    self.esc_hint = ("⚠ **esc 是開關（按一下開、再按一下關）**，你已經連按很多次，"
                                     "等於自己把選單開開關關。選單開著的時候不要再按 esc——"
                                     "**直接點畫面上的按鈕**（選單下方通常寫著要按哪個鍵，例如 繼續／離開／重生），"
                                     "或者點選單以外的空白處。" + NL)
                    continue
                self.esc_hits.append(time.time())
            out.append(a)
        return out


    def clamp_aim(self, actions):
        """執行前的最後一道防線：aim 不准對到畫面上半部（那等於抬頭）。"""
        if not self.g("抬頭"):
            return actions
        for a in actions:
            if isinstance(a, dict) and a.get("t") == "aim":
                try:
                    y = float(a.get("y", 0.5))
                except (TypeError, ValueError):
                    continue
                desc, need = self.pitch_from_eyes()
                lim3 = 0.42 if need == 0 else (0.35 if need is None else 0.50)
                if self.g("抬頭") and y < lim3 and need != -1:
                    print(f"   ⛔ 執行前攔截：aim y={y:.2f} 太高 → 0.45")
                    a["y"] = 0.45
                    self.up_count = getattr(self, "up_count", 0) + 1
                    self.stat["想抬頭被擋"] = self.stat.get("想抬頭被擋", 0) + 1
                elif self.g("低頭") and y > 0.66:
                    print(f"   ⛔ 執行前攔截：aim y={y:.2f} 太低（等於低頭）→ 0.62")
                    a["y"] = 0.62
                    self.stat["想低頭被擋"] = self.stat.get("想低頭被擋", 0) + 1
        return actions

    def tame_look(self, a):
        """把滑鼠幅度壓小：左右一次最多 200、上下最多 90，而且不准連續往同一方向上下轉。

        第一人稱的視角很敏感，一次拖太多就會整個甩過頭、看到天花板或轉過目標，
        寧可小步轉、看一眼、再轉。aim 也一樣要收斂。
        """
        if a.get("t") == "aim":
            # aim 換算出來的拖曳量同樣要收斂：離中心太遠時只轉一半，下一輪再修
            try:
                x, y = float(a.get("x", 0.5)), float(a.get("y", 0.5))
            except (TypeError, ValueError):
                return a
            if self.g("空轉aim") and abs(x - 0.5) < 0.05 and abs(y - 0.5) < 0.05:
                # 準心本來就在畫面正中央，aim 到正中央等於什麼都沒做（他很常這樣空轉）
                print("   ✂ aim 到畫面正中央＝沒有動作，這一輪跳過")
                self.noop_aim = getattr(self, "noop_aim", 0) + 1
                if self.noop_aim >= 3:
                    self.noop_aim = 0
                    self.hold_fail_hint = ("⚠ 你一直把準心對到畫面正中央（0.5,0.5），那等於什麼都沒做。"
                                           "要互動的東西如果不在正中央，就寫它真正的座標；"
                                           "已經對準了就直接 hold E 或往前走一步。" + NL)
                return None
            a["x"] = 0.5 + (x - 0.5) * 0.6
            a["y"] = 0.5 + (y - 0.5) * 0.4
            y_want = a["y"]      # 只照鏡子不擋他：提醒照給，最後還是照他要的做
            # aim 到畫面上半部＝等於抬頭，這是他抬頭的隱藏漏洞
            desc, need = self.pitch_from_eyes()
            lim = 0.42 if need == 0 else (0.35 if need is None else 0.50)
            if self.g("抬頭") and a["y"] < lim and need != -1:
                print(f"   ⛔ aim 的 y={y:.2f} 在畫面上半部（等於抬頭）→ 壓到 0.45")
                a["y"] = 0.45
                self.up_count = getattr(self, "up_count", 0) + 1
                self.stat["想抬頭被擋"] = self.stat.get("想抬頭被擋", 0) + 1
            elif self.g("低頭") and a["y"] > 0.66:
                print(f"   ⛔ aim 的 y={y:.2f} 太靠畫面下緣（等於低頭）→ 提到 0.62")
                a["y"] = 0.62
                self.stat["想低頭被擋"] = self.stat.get("想低頭被擋", 0) + 1
            if not self.g("擋視角"):
                a["y"] = y_want      # 使用者要他自己學會，不替他改
            return a
        if a.get("t") != "look":
            self._same_dy = 0
            return a
        try:
            dx = float(a.get("dx", 0) or 0)
        except (TypeError, ValueError):
            dx = 0.0
        if self.g("幅度") and abs(dx) > self.MAX_DX:
            print(f"   ✂ 左右轉頭 {dx:.0f} 太大，收斂成 {self.MAX_DX if dx > 0 else -self.MAX_DX}")
            dx = self.MAX_DX if dx > 0 else -self.MAX_DX
        a["dx"] = int(dx)
        try:
            dy = float(a.get("dy", 0) or 0)
        except (TypeError, ValueError):
            dy = 0.0
        dy_want = dy         # 只照鏡子不擋他：下面照樣提醒、照樣計次，但不改他的 dy
        edge = getattr(self, "pitch_edge", "")
        if edge and time.time() - getattr(self, "pitch_edge_at", 0) < 120 and dy:
            if (edge == "下" and dy > 0) or (edge == "上" and dy < 0):
                where = "最下面（正對著地板）" if edge == "下" else "最上面（正對著天花板）"
                back = "負的 dy 往上" if edge == "下" else "正的 dy 往下"
                print(f"   ⛔ 鏡頭已經頂到{where}，往同方向的 dy={dy:.0f} 沒有意義 → 歸零")
                self.stat["想低頭被擋" if edge == "下" else "想抬頭被擋"] =                     self.stat.get("想低頭被擋" if edge == "下" else "想抬頭被擋", 0) + 1
                self.look_up_hint = (f"⚠ 程式量到你的鏡頭已經**頂到{where}**（剛才轉了畫面完全沒變）。"
                                     f"再往同一個方向轉是白費——要看得到東西只能用{back}轉回來，"
                                     f"或者乾脆 dy=0 只轉左右。" + NL)
                dy = 0.0
        desc0, need0 = self.pitch_from_eyes()
        # 往下（dy 正）以前是「一律放行」，結果每一道防線都只把鏡頭往下推，沒有人把它推回來，
        # 鏡頭就一路漂到地板。現在往下也要有理由：確定在看天花板 (need0 == 1) 才准大幅往下。
        if dy > 0 and self.g("低頭"):
            if need0 == -1:
                print(f"   ⛔ 擋掉往下看 dy={dy:.0f}（你本來就在低頭看地板了）")
                self.stat["想低頭被擋"] = self.stat.get("想低頭被擋", 0) + 1
                self.look_up_hint = ("⚠ 你又想往下看（正的 dy）——程式擋掉了。**你現在已經在低頭看地板**，"
                                     "櫃檯上的東西、病人、按鈕全在你的視線上面，低頭只會什麼都看不到。"
                                     "要修就用小的負 dy（-40 左右）把頭抬回水平。" + NL)
                self.add_note("低頭看地板的時候不要再往下修，要用負的 dy 抬回水平")
                dy = 0.0
            elif need0 == 0 and dy > 0:
                seen0 = str(getattr(self, "last_seen", ""))
                if "天花板" in seen0:
                    dy = min(dy, 60.0)       # 他自己說在看天花板：放行往下修（別跟往上擋互鎖）
                    print(f"   ↓ 你說你在看天花板 → 放行往下修 {dy:.0f}")
                else:
                    dy = min(dy, 30.0)       # 量到水平就只准小幅微調，不歸零（歸零會造成死鎖）
                    print(f"   ✂ 視角量起來是水平的 → 往下只准微調 {dy:.0f}")
                    self.look_up_hint = ("視角量起來已經水平了。要微調可以，但幅度放小；"
                                         "確定看到天花板或地板才需要大幅修。" + NL)
            elif dy > 60:
                dy = 60.0      # 真的在看天花板時才准往下，而且一次別壓太多
        if dy < 0 and need0 == 0 and self.g("抬頭"):
            seen0 = str(getattr(self, "last_seen", ""))
            if "地板" in seen0 and "桌" not in seen0:
                dy = max(dy, -40.0)          # 他自己說在看地板：准他小幅抬回來
                print(f"   ↑ 你說你在看地板 → 放行往上修 {dy:.0f}")
            else:
                dy = max(dy, -30.0)          # 量到水平：只准微調，不歸零
                print(f"   ✂ 視角量起來是水平的 → 往上只准微調 {dy:.0f}")
                self.look_up_hint = ("視角量起來已經水平了，沒事不要往上——真的要調就小幅度。" + NL)
        if dy < 0 and self.g("抬頭"):
            desc, need = self.pitch_from_eyes()
            if need == 1:      # 確定正在看天花板，卻還想再往上 → 這是最糟的一種，一定擋
                self.up_count = getattr(self, "up_count", 0) + 1
                self.stat["想抬頭被擋"] = self.stat.get("想抬頭被擋", 0) + 1
                print(f"   ⛔ 擋掉往上看 dy={dy:.0f}——程式量到你**正在看天花板**，再往上就完全回不來了")
                self.look_up_hint = ("⚠ 程式量到你正在看天花板，你卻還想往上轉——已經擋掉。"
                                     "**現在唯一該做的是用正的 dy 往下修**（一次 40~80），"
                                     "修到上下都看得到東西為止。" + NL)
                dy = 0.0
            elif need == 0:    # 確定視角正常，卻還想往上看 → 擋掉（不知道就不擋）
                self.up_count = getattr(self, "up_count", 0) + 1
                self.stat["想抬頭被擋"] = self.stat.get("想抬頭被擋", 0) + 1
                print(f"   ⛔ 擋掉往上看 dy={dy:.0f}（你不是在看地板，抬頭只會看到天花板；第 {self.up_count} 次）")
                self.look_up_hint = (f"⚠ 你又想抬頭（dy {dy:.0f}）——程式量到你的視角是**水平的**，所以擋掉了。"
                                     f"今天你已經想抬頭 {self.up_count} 次。水平就是對的：**dy 填 0，只轉左右**，"
                                     f"不要往上也不要往下。" + NL)
                self.add_note("視角已經水平時就把 dy 填 0，不要往上也不要往下，只轉左右")
                dy = 0.0
            elif need == -1 and dy < -90:
                dy = -90.0     # 真的在看地板時准他一次抬多一點，不然要修幾十輪才回得來
            elif need is None:
                # 量不出俯仰（眼睛什麼都沒框到）＝最危險的狀態：多半已經對著天花板或空牆，
                # 這時候再往上就永遠回不來（看天花板→更框不到東西→更量不出來）。
                # 只有畫面紋理判斷「確定偏下」才准往上，而且一次最多 40。
                v = self.view_state_cached()
                if "偏下" in v:
                    if dy < -40:
                        dy = -40.0
                    print(f"   ✂ 量不出俯仰、但畫面偏下 → 准你往上 {abs(dy):.0f}（一次最多 40）")
                else:
                    print(f"   ⛔ 量不出俯仰（眼睛什麼都沒框到），往上 dy={dy:.0f} 擋掉——"
                          f"這種狀態多半是你已經在看天花板或空牆")
                    self.stat["想抬頭被擋"] = self.stat.get("想抬頭被擋", 0) + 1
                    self.up_count = getattr(self, "up_count", 0) + 1
                    self.look_up_hint = ("⚠ 眼睛什麼都沒框到＝程式量不出你的俯仰，這通常代表你正對著"
                                         "天花板或一片空牆。**這種時候往上轉是最糟的選擇**，程式擋掉了。"
                                         "先 dy=0、用 look 左右轉頭找東西；框到東西之後才有辦法判斷要不要修視角。" + NL)
                    dy = 0.0
        if not self.g("擋視角"):
            # 使用者指示：不要擋他，讓他自己學會不抬頭。上面的提醒與計次都留著，只是不動手改。
            if dy != dy_want:
                print(f"   👀 上面那些提醒照給，但不擋你：dy 照你要的 {dy_want:.0f} 執行")
            dy = dy_want
        if self.g("幅度") and abs(dy) > self.MAX_DY:
            print(f"   ✂ 上下轉頭 {dy:.0f} 太大，收斂成 {self.MAX_DY if dy > 0 else -self.MAX_DY}")
            dy = self.MAX_DY if dy > 0 else -self.MAX_DY
        last = getattr(self, "_last_dy", 0)
        if dy and last and (dy > 0) == (last > 0):
            self._same_dy = getattr(self, "_same_dy", 0) + 1
        else:
            self._same_dy = 0
        # 上下來回擺（+38 → -38 → +38…）跟一直往同一邊一樣糟：他在原地抖，什麼事都沒做。
        if dy and last and (dy > 0) != (last > 0):
            self._flip_dy = getattr(self, "_flip_dy", 0) + 1
            if self._flip_dy >= 2:
                print(f"   ✂ 你在上下來回擺（前一次 {last:+.0f}、這次 {dy:+.0f}）"
                      + ("→ dy 歸零" if self.g("擋視角") else "（只提醒，不擋你）"))
                self.look_up_hint = ("⚠ 你連續好幾輪在上下來回轉（一下 +dy 一下 -dy），這是在原地抖，"
                                     "什麼都沒做成。**dy 填 0**，先左右轉頭把目標找出來，"
                                     "視角要不要修看【程式量到的視角】那一行就好。" + NL)
                self._flip_dy = 0
                if self.g("擋視角"):
                    dy = 0.0
        elif dy:
            self._flip_dy = 0
        if self._same_dy >= 2 and dy and self.g("擋視角"):
            print("   ✂ 已經連續往同一個方向上下轉頭，這次不轉了（先看清楚再說）")
            dy = 0
        self._last_dy = dy
        a["dy"] = int(dy)
        if not a["dx"] and not a["dy"]:
            # 兩邊都被收斂成 0＝這一輪等於什麼都沒做（他今天大量的輪次是這樣浪費掉的）
            self.noop_look = getattr(self, "noop_look", 0) + 1
            print("   ✂ look 被收斂成 0,0＝沒有動作，丟掉")
            if self.noop_look >= 2:
                self.noop_look = 0
                self.look_up_hint = ("⚠ 你連續好幾輪的 look 都被程式收斂成 0（方向不對或沒必要），"
                                     "等於原地發呆。**換個做法**：用 a/d 橫移、按 w 走一步、"
                                     "或用 {\"t\":\"選\",\"n\":1,\"做\":\"aim\"} 直接選一個看得到的目標。" + NL)
                # 他自己修不回來的話程式幫他修一次視角（往上只會修 60，且有連續失敗保護）
                try:
                    if self.g("自動視角"):
                        self.level_camera(1, why="連續空轉，幫你把視角壓平")
                except Exception:  # noqa: BLE001
                    pass
            return None
        return a

    def zoom_out(self, why=""):
        """鏡頭被夾住／貼太近時：滾輪拉遠回第三人稱、後退一步、再把視角壓平。"""
        print(f"   🔭 拉遠鏡頭（{why}）")
        if self.dry or not self.win.activate():
            return
        try:
            import pyautogui
            for _ in range(3):
                pyautogui.scroll(-10 * 120)
                time.sleep(0.15)
            self.press_key("s", 0.8)
            self.level_camera(3)
        except Exception as e:  # noqa: BLE001
            print(f"   拉遠失敗：{e}")
        self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 鏡頭被夾住，程式幫你滾輪拉遠並後退；"
                            f"以後看不到自己的角色就先 scroll 負值拉遠，不要一直 look")
        self.stuck_review(why or "鏡頭被夾在物件裡，上下左右都轉不動")

    def guard_hold(self, actions):
        """對「試過 5 次以上都按不動」的東西直接擋掉——他會一直重試同一個沒用的目標。"""
        if not self.g("互動"):
            return actions
        out = []
        for a in actions:
            if a.get("t") == "hold":
                g = str(a.get("g") or "").strip()[:16]
                d = self.tries_log.get(g, {})
                if d.get("成功", 0) == 0 and d.get("失敗", 0) >= 5:
                    print(f"   ⛔「{g}」已經試過 {d['失敗']} 次全部失敗 → 擋掉，換別的目標")
                    self.hold_block_hint = (f"⚠ 你又想長按「{g}」——這東西你已經試過 {d['失敗']} 次，"
                                            f"**每一次都沒反應**，代表它根本不能互動（或者要換方式）。"
                                            f"別再按了，換一個目標，或先確認畫面有沒有出現互動提示。" + NL)
                    self._last_hold_target = ""
                    continue
                self._last_hold_target = g
            out.append(a)
        return out

    def hold_interact(self, key="e", max_hold=4.0, need=12.0):
        """覆寫：長按一直沒反應時縮短時間（每次白等 4 秒太浪費），並提醒他為什麼沒反應。"""
        fails = getattr(self, "hold_fails", 0)
        if fails >= 2:
            max_hold = 1.5          # 已經知道按不到了，不要再耗 4 秒
        # 畫面上沒有 E 提示＝這一按一定不會有反應，那就別耗滿 4 秒（今天大量時間耗在這裡）
        try:
            if max_hold > 1.6 and not self.e_prompt(max_age=1.5):
                max_hold = 1.5
                self.hold_fail_hint = ("（畫面上沒有 E 提示，程式把這次長按縮短成 1.5 秒——"
                                       "沒有提示就是按不到，**先靠近一步或用 a/d 橫移對正**，"
                                       "看到白色的 E 再按。）" + NL
                                       + (getattr(self, "hold_fail_hint", "") or ""))
        except Exception:  # noqa: BLE001
            pass
        self.stat["互動次數"] = self.stat.get("互動次數", 0) + 1
        ok, held = super().hold_interact(key, max_hold=max_hold, need=need)
        if not ok:
            try:
                self.punish("空按E", f"按了 {held:.1f} 秒完全沒反應。"
                                     f"沒有互動提示、或沒對準目標，按再久也沒用——"
                                     f"先把準心對到東西上，看到提示再按。")
            except Exception:  # noqa: BLE001
                pass
        if ok:
            self.stat["互動成功"] = self.stat.get("互動成功", 0) + 1
            try:
                w = getattr(self, "_goal_walk", None)
                if w and time.time() - w["時間"] < 45:
                    ok_goal, why_goal = self.goal_reached(w)
                    if ok_goal:
                        self.award("走到目標並互動成功",
                                   f"你走向「{w['目標']}」，然後在那裡互動成功了"
                                   f"（{why_goal}）——"
                                   f"**這才是一次完整的動作**（走過去＋按成功）。")
                        self._goal_walk = None
                    else:
                        # 不扣分，但要讓他知道為什麼沒拿到分，不然他會以為隨便按到就有 10 分
                        self.no_award_hint = (
                            f"ℹ 這次互動成功了，但**沒有 +10**：{why_goal}。"
                            f"+10 要「走到你說的那個目標前面」**而且**「在那裡互動成功」兩件事都做到。"
                            + NL)
                        print(f"   ⚪ 互動成功但不給 +10：{why_goal}")
            except Exception:  # noqa: BLE001
                pass
            # 他自己提的操作加分：按 E 之前先把目標對到畫面中央（這是空按 E 的正解）
            try:
                am = str(getattr(self, "_last_aim", "") or "")
                if am.startswith("("):
                    ax, ay = (float(x) for x in am.strip("()").split(","))
                    if abs(ax - 0.5) <= 0.18 and abs(ay - 0.5) <= 0.22:
                        self.op_award("互動前的對準",
                                      f"你先把目標對到畫面中央 ({ax:.2f},{ay:.2f}) 才按 E，"
                                      f"這一次就按到了——這就是不會空按的做法。")
            except (TypeError, ValueError):
                pass
            self.note_progress("互動成功")
            try:
                self.note_spot(getattr(self, "_last_hold_target", ""), f"長按 E {held:.1f} 秒")
            except Exception:  # noqa: BLE001
                pass
            try:
                self.note_win("長按 E", getattr(self, "_last_hold_target", "?"), held)
            except Exception as e:  # noqa: BLE001
                print(f"   記成功做法失敗：{e}")
        self.note_try(getattr(self, "_last_hold_target", "?"), ok)
        if ok:
            self.hold_fails = 0
            self.hold_fail_hint = ""
        else:
            self.hold_fails = fails + 1
            if self.hold_fails >= 2:
                self.hold_fail_hint = (
                    f"⚠ 長按 E 已經連續 {self.hold_fails} 次沒反應。這代表**你根本不在互動範圍內**，"
                    f"不是按不夠久——再按幾次也一樣。先換位置：往前走一步或退一步、換個角度對準，"
                    f"畫面出現「按住 E」之類的提示才按得到。" + NL)
        return ok, held

    # ---------------- 他送的 vs 實際做的 vs 程式替他做的（2026-09-29 使用者）
    def act_note(self, what):
        """有東西真的動到遊戲時呼叫：他這一輪的動作記成「實際執行」，其他一律記成「程式替你做」。"""
        if getattr(self, "_in_prog_exec", False):
            return                                  # 已經在 execute 那層記過了，不重複
        if getattr(self, "_his_exec", False):
            self._exec_log = (getattr(self, "_exec_log", []) + [what])[-8:]
            return
        why = str(_LAST_PRINT.get("line") or "").lstrip("⚠🚫🔻🔺🧹🚑🔁🌀🔎🧭⏸✂↕🗺🚶📍 ").strip()
        log = getattr(self, "_prog_log", [])
        if log and log[-1][1] == why[:60]:
            log[-1] = (log[-1][0] + "、" + what, log[-1][1])   # 同一個原因連續按好幾下就併成一筆
        else:
            log.append((what, why[:60]))
        self._prog_log = log[-6:]

    def execute(self, actions):
        if actions and not getattr(self, "_his_exec", False) and not getattr(self, "_in_prog_exec", False):
            self.act_note("、".join(self.describe(a) for a in actions if isinstance(a, dict))[:60])
            self._in_prog_exec = True
            try:
                return super().execute(actions)
            finally:
                self._in_prog_exec = False
        if actions and getattr(self, "_his_exec", False):
            self._exec_log = (getattr(self, "_exec_log", []) +
                              [self.describe(a) for a in actions if isinstance(a, dict)])[-8:]
            self._in_prog_exec = True               # 他的動作在這層記完，底下的按鍵不再重複記
            try:
                return super().execute(actions)
            finally:
                self._in_prog_exec = False
        return super().execute(actions)

    def raw_look(self, dx, dy, steps=8):
        try:
            self.act_note(f"轉視角 {int(dx)},{int(dy)}")
        except (TypeError, ValueError):
            pass
        return super().raw_look(dx, dy, steps)

    def step_record(self):
        """一輪結束時整理：他原本送了什麼、實際執行了什麼、被擋掉什麼、程式替他做了什麼。"""
        orig = [a for a in (getattr(self, "_asked_full", []) or []) if isinstance(a, dict)]
        orig_s = "、".join(self.describe(a) if a.get("t") in ("key", "hold", "click", "look", "aim", "scroll", "wait")
                          else f"工具「{a.get('t')}」" for a in orig)[:90]
        exe_s = "、".join(getattr(self, "_exec_log", []) or [])[:90]
        blk = getattr(self, "_blocked_now", []) or []
        blk_s = "、".join(str(b) for b in blk)[:60]
        prog = getattr(self, "_prog_log", []) or []
        prog_s = "；".join(f"{w}（程式說：{y}）" if y else w for w, y in prog)[:160]
        self._exec_log, self._prog_log, self._blocked_now = [], [], []
        self._his_exec = False                  # 保險：一輪結束一定關掉，免得中途例外讓之後的程式動作被算成他的
        self._last_step_rec = {"原本": orig_s, "實際": exe_s, "擋": blk_s, "程式": prog_s}
        return dict(self._last_step_rec)

    def press_key(self, k, d=0.3):
        import pydirectinput
        try:
            self.act_note(f"按 {k} {float(d):.1f}s")
        except (TypeError, ValueError):
            pass
        try:
            self.trace_add(f"按 {k} {float(d):.1f}s")
            self.route_note(str(k).lower(), float(d))       # 在大廳的話，錄下這一步（走到門口的路線）
        except Exception:  # noqa: BLE001
            pass
        pydirectinput.keyDown(k)
        time.sleep(d)
        pydirectinput.keyUp(k)

    def brighten(self, im):
        """畫面太暗（黑走廊、停電）時自動提亮再送給他看——不然他只會回「全黑」。

        回傳 (影像, 有沒有提亮)。
        """
        try:
            import numpy as np
            from PIL import ImageOps, ImageEnhance
            mean = float(np.asarray(im.convert("L"), dtype=np.float32).mean())
            if mean >= 45:
                return im, False
            out = ImageOps.autocontrast(im, cutoff=1)
            out = ImageEnhance.Brightness(out).enhance(min(3.0, 60.0 / max(8.0, mean)))
            return out, True
        except Exception:  # noqa: BLE001
            return im, False

    def screen_black(self):
        """畫面是不是（幾乎）全黑——載入卡住、被踢、渲染爆掉都長這樣。

        2026-09-19 使用者：**遇到全黑或卡住就退出重進，而且不扣分。**
        判準：整張圖的平均亮度很低**而且**幾乎沒有變化（不是暗房間，是真的沒東西）。
        """
        try:
            import numpy as np
            im = getattr(self, "_frame_im", None) or self.grab_still(320)
            a = np.asarray(im.convert("L"), dtype=np.float32)
            return bool(a.mean() < 16 and a.std() < 12)
        except Exception:  # noqa: BLE001
            return False

    def wall_in_front(self):
        """三欄紋理都很低＝整個畫面被牆佔滿。比 wall_score 的單一門檻可靠。"""
        try:
            import numpy as np
            g = np.asarray(self.grab_still(320).convert("L"), dtype=np.float32)
            h, w = g.shape
            band = g[int(h * 0.30):int(h * 0.85), :]
            stds = [float(band[:, i * w // 3:(i + 1) * w // 3].std()) for i in range(3)]
            return max(stds) < 22.0, stds
        except Exception:  # noqa: BLE001
            return False, []

    # ---------------- 他自己實測出來的操作手冊
    def load_manual(self):
        self.manual = {}
        p = os.path.join(self.dir, "操作手冊.md")
        if os.path.exists(p):
            for line in open(p, encoding="utf-8"):
                if line.startswith("- "):
                    k, _, v = line[2:].partition("：")
                    if v.strip():
                        self.manual[k.strip()] = v.strip()
        print(f"[手冊] 他自己測出來的操作數值 {len(self.manual)} 條")

    def save_manual(self):
        try:
            with open(os.path.join(self.dir, "操作手冊.md"), "w", encoding="utf-8") as f:
                f.write("# 我自己實測出來的操作數值（不是別人教的）" + NL + NL)
                for k, v in self.manual.items():
                    f.write(f"- {k}：{v}" + NL)
        except OSError:
            pass

    def manual_text(self):
        if not getattr(self, "manual", None):
            return ("（還沒測過。用 {\"t\":\"實驗\",\"k\":\"look\",\"dx\":100} 這種動作，"
                    "程式會幫你做一次並量出結果，寫進你的手冊。）")
        return "；".join(f"{k}：{v}" for k, v in list(self.manual.items())[:8])

    def run_experiment(self, a):
        """他自己指定的操作實驗：程式實際做一次、量出畫面變化、寫進他的手冊。"""
        k = str(a.get("k", "look")).lower()
        before = self.grab_still(320)
        if k == "look":
            try:
                dx = int(float(a.get("dx", 100) or 100))
            except (TypeError, ValueError):
                dx = 100
            dy = int(float(a.get("dy", 0) or 0))
            self._look_measured = before
            self.raw_look(dx, dy)
            self.after_look(dx, dy)
            ppp = getattr(self, "px_per_pct", None) or 4.0
            pct = abs(dx) / ppp
            deg = pct * 0.70          # 整個畫面寬大約 70 度視野
            key = f"滑鼠拖 {abs(dx)} 像素"
            val = f"畫面轉約 {pct:.0f}%、大約 {deg:.0f} 度（{'右' if dx > 0 else '左'}）"
        else:
            d = float(a.get("d", 1.0) or 1.0)
            tgt = getattr(self, "_track", "") or ""
            b4 = self.eyes.best(tgt, max_age=1.5) if tgt else None
            self.press_key(k if k in ("w", "a", "s", "d") else "w", d)
            time.sleep(0.25)
            after = self.grab_still(320)
            diff = frame_diff(before, after)
            key = f"按 {k} {d:.1f} 秒"
            af = self.eyes.best(tgt, max_age=1.5) if tgt else None
            if b4 and af and b4["h"] > 0.02:
                grow = af["h"] / b4["h"]
                val = (f"「{tgt}」的框從 {b4['h']:.2f} 變成 {af['h']:.2f}（放大 {grow:.2f} 倍）"
                       f"＝距離大約縮短 {max(0, (1 - 1 / grow)) * 100:.0f}%；畫面變化量 {diff:.0f}")
            else:
                val = f"畫面變化量 {diff:.0f}（0＝完全沒動被擋住；越大代表移動越多）"
        self.manual[key] = val
        self.save_manual()
        print(f"   🧪 實驗：{key} → {val}")
        self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 實驗結果：{key} → {val}")

    # ---------------- 他自己列的步驟（下一步要做什麼）
    def load_stands(self):
        self.stands = {}
        try:
            with open(self.space_file("站位.json"), encoding="utf-8") as f:
                self.stands = {k: v for k, v in json.load(f).items() if isinstance(v, dict)}
        except (OSError, json.JSONDecodeError, TypeError):
            pass
        print(f"[站位] 他自己記過 {len(self.stands)} 個站位")

    def save_stands(self):
        try:
            with open(self.space_file("站位.json"), "w", encoding="utf-8") as f:
                json.dump(self.stands, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def stands_text(self):
        """告訴他：回到每個記過的站位要轉幾度、走幾秒、到了之後朝哪。"""
        import math
        if not getattr(self, "stands", None):
            return ("（你還沒記過站位。站到好用的位置時用 記站位，"
                    "之後我會告訴你怎麼走回來、要轉幾度。）")
        rows = []
        for nm, v in self.stands.items():
            vx, vy = v.get("x", 0) - self.pos[0], v.get("y", 0) - self.pos[1]
            dist = math.hypot(vx, vy)
            if dist < 1.0:
                face = (v.get("yaw", 0) - getattr(self, "yaw", 0.0) + 540) % 360 - 180
                rows.append(f"{nm}：你就站在這裡了" +
                            (f"，但朝向差 {abs(face):.0f} 度"
                             f"（往{'右' if face > 0 else '左'}轉，look dx {self.deg_dx(face)}）"
                             if abs(face) > 15 else "，朝向也對了"))
                continue
            ang = (math.degrees(math.atan2(vx, vy)) - getattr(self, "yaw", 0.0) + 540) % 360 - 180
            rows.append(f"{nm}：往{'右' if ang > 0 else '左'}轉 {abs(ang):.0f} 度"
                        f"（look dx {self.deg_dx(ang)}）再直走約 {dist:.1f} 秒")
        return NL.join(rows)

    def load_stage(self):
        self.stage = 0
        self.game_id = {}
        self.cause = []
        self.chains = {}
        self.custom_labels = set()
        self.wiki_hits = 0
        try:
            with open(os.path.join(self.dir, "學習階段.json"), encoding="utf-8") as f:
                d = json.load(f)
            self.stage = int(d.get("階段", 0))
            self.game_id = d.get("遊戲身分", {}) or {}
            self.custom_labels = set(w for w in d.get("自訂偵測詞", []) if self.good_label(w))
            self.wiki_hits = int(d.get("查wiki次數", 0))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
        try:
            self.cause = [l[2:].strip() for l in open(os.path.join(self.dir, "因果.md"), encoding="utf-8")
                          if l.startswith("- ")]
        except OSError:
            pass
        try:
            with open(os.path.join(self.dir, "連續流程.json"), encoding="utf-8") as f:
                self.chains = {k: v for k, v in json.load(f).items() if isinstance(v, list)}
        except (OSError, json.JSONDecodeError, TypeError):
            pass
        if self.mod("學習課程"):
            print(f"[學習] 目前在第 {self.stage + 1} 階段：{STAGES[min(self.stage, len(STAGES)-1)]['名']}"
                  f"（因果 {len(self.cause)} 條、連續流程 {len(self.chains)} 條、查 wiki {self.wiki_hits} 次）")
        else:
            print(f"[學習] 這個遊戲沒有排課程，他自己學"
                  f"（因果 {len(self.cause)} 條、連續流程 {len(self.chains)} 條）")

    @staticmethod
    def good_label(w):
        """YOLO-World 是英文開放詞彙：只收 1~3 個英文單字（含底線／連字號），其他（中文長句）一律退回。
        2026-09-15 清掉「右下角紅色的玩家頭部」這種 40 個垃圾詞——它們會拖垮整個偵測。"""
        w = str(w or "").strip().lower()
        return bool(w) and len(w) <= 30 and all(ord(c) < 128 for c in w) and 1 <= len(w.split()) <= 3

    def save_stage(self):
        try:
            with open(os.path.join(self.dir, "學習階段.json"), "w", encoding="utf-8") as f:
                json.dump({"階段": self.stage, "階段名": STAGES[min(self.stage, len(STAGES)-1)]["名"],
                           "遊戲身分": self.game_id, "自訂偵測詞": sorted(self.custom_labels),
                           "查wiki次數": self.wiki_hits,
                           "更新": datetime.now().strftime("%m/%d %H:%M:%S")},
                          f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def stage_missing(self):
        """這一階段還差什麼（程式驗收，內容都是他自己填的）。"""
        g, m = self.game_id, []
        if self.stage == 0:
            for k in ("名稱", "作者", "封面特徵", "進入步驟"):
                if not str(g.get(k, "")).strip():
                    m.append(f"遊戲身分的「{k}」還沒填")
            if not os.path.exists(os.path.join(self.dir, "封面.jpg")):
                m.append("還沒存封面截圖（用 記遊戲 時程式會自動存當下畫面）")
        elif self.stage == 1:
            man = getattr(self, "manual", {})
            looks = [k for k in man if "滑鼠拖" in k]
            keys = [k for k in man if k.startswith("按 ")]
            if "滑鼠拖 刻度" not in man:
                m.append('還沒量轉視角刻度（送 {"t":"量刻度"}，程式會帶你把上下全程、左右一圈掃一遍）')
            elif len(looks) < 3:
                m.append(f"轉視角只量了 {len(looks)} 種幅度（要 3 種，用 實驗）")
            if len(keys) < 4:
                m.append(f"移動鍵只量了 {len(keys)} 個（w/a/s/d 都要，用 實驗）")
            for k in ("視角", "模式"):
                if not str(g.get(k, "")).strip():
                    m.append(f"還沒記「{k}」（用 記遊戲 填）")
        elif self.stage == 2:      # 2.5 探索與空間感（每個遊戲通用）
            if len(getattr(self, "map", {})) < 6:
                m.append(f"地圖上只有 {len(getattr(self, 'map', {}))} 個地點（要 6 個，用 記位置）")
            if len(getattr(self, "spots", {})) < 3:
                m.append(f"腦中地圖只有 {len(getattr(self, 'spots', {}))} 個互動點"
                         f"（要 3 個；互動成功會自動記，所以去把東西按成功）")
            if len(getattr(self, "walk_samples", [])) < 3:
                m.append(f"走路換算只有 {len(getattr(self, 'walk_samples', []))} 筆樣本"
                         f"（要 3 筆：對著看得到的東西按 w 走一段就會自動量到）")
            if getattr(self, "fix_count", 0) < 1:
                m.append("還沒用過 我在 校正位置（認出自己站在記過的地方時就用）")
        elif self.stage == 3:
            if len(self.cause) < 8:
                m.append(f"因果紀錄只有 {len(self.cause)} 條（要 8 條，用 記因果）")
            if len(self.custom_labels) < 3:
                m.append(f"自己加給 YOLO 的偵測詞只有 {len(self.custom_labels)} 個（要 3 個，用 找）")
        elif self.stage == 4:
            good = [k for k, v in self.chains.items() if len(v) >= 3]
            if len(good) < 2:
                m.append(f"連續流程只有 {len(good)} 條合格（要 2 條、每條至少 3 步，用 記流程）")
        return m

    def stage_text(self):
        # 五階段課程是我（程式）替他排的學習順序。新遊戲預設關掉：
        # 使用者要他自己決定怎麼學、自己啟動學習，不是照別人排好的課表走。
        if not self.mod("學習課程"):
            return ""
        i = min(self.stage, len(STAGES) - 1)
        st = STAGES[i]
        miss = self.stage_missing()
        body = (f"**第 {i + 1} 階段：{st['名']}**" + NL + st["做什麼"] + NL
                + "驗收標準：" + st["要完成"] + NL)
        if miss:
            body += "還差：" + "；".join(miss)
        else:
            body += "✅ 這一階段的東西都齊了，程式會幫你進到下一階段。"
        if self.chains:
            body += NL + "你建立過的連續流程：" + "／".join(
                f"{k}（{len(v)}步）" for k, v in list(self.chains.items())[:4])
        return body

    def check_stage(self):
        if not self.mod("學習課程"):
            return
        if self.stage >= len(STAGES) - 1 and not self.stage_missing():
            return
        if self.stage_missing():
            return
        self.stage = min(self.stage + 1, len(STAGES) - 1)
        self.save_stage()
        nm = STAGES[self.stage]["名"]
        print(f"   🎓 上一階段完成！進到第 {self.stage + 1} 階段：{nm}")
        self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 你完成了上一階段，現在是「{nm}」")
        self.add_note(f"（階段）完成上一階段，進入 {nm}")

    def load_try(self):
        self.tries_log = {}
        try:
            with open(os.path.join(self.dir, "互動嘗試.json"), encoding="utf-8") as f:
                self.tries_log = {k: v for k, v in json.load(f).items() if isinstance(v, dict)}
        except (OSError, json.JSONDecodeError, TypeError):
            pass
        bad = [k for k, v in self.tries_log.items() if v.get("成功", 0) == 0 and v.get("失敗", 0) >= 5]
        print(f"[互動] 累積 {len(self.tries_log)} 種目標；已知按不動的 {len(bad)} 種")

    def save_try(self):
        try:
            with open(os.path.join(self.dir, "互動嘗試.json"), "w", encoding="utf-8") as f:
                json.dump(self.tries_log, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def note_try(self, target, ok):
        t = (target or "?").strip()[:16]
        d = self.tries_log.setdefault(t, {"成功": 0, "失敗": 0})
        d["成功" if ok else "失敗"] += 1
        self.save_try()

    def try_text(self):
        if not getattr(self, "tries_log", None):
            return "（還沒有紀錄。每次長按 E 的結果都會記在這裡，累積起來你就知道哪些東西按得動。）"
        good = [(k, v) for k, v in self.tries_log.items() if v.get("成功", 0) > 0]
        bad = [(k, v) for k, v in self.tries_log.items() if v.get("成功", 0) == 0 and v.get("失敗", 0) >= 3]
        rows = []
        if good:
            rows.append("✅ 按得動：" + "、".join(f"{k}（成功{v['成功']}/{v['成功']+v['失敗']}）" for k, v in good[:6]))
        if bad:
            rows.append("❌ 試過都按不動（別再按了）：" + "、".join(f"{k}（{v['失敗']}次全失敗）" for k, v in bad[:8]))
        return NL.join(rows) or "（還在累積）"

    def load_desk(self):
        self.desk = {}
        p = os.path.join(self.dir, "櫃檯配置.json")
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            self.desk = {k: v for k, v in d.items() if isinstance(v, dict)}
        except (OSError, json.JSONDecodeError, TypeError):
            pass
        print(f"[櫃檯] 他自己記過 {len(self.desk)} 樣櫃檯上的東西")

    def save_desk(self):
        try:
            with open(os.path.join(self.dir, "櫃檯配置.json"), "w", encoding="utf-8") as f:
                json.dump(self.desk, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def desk_text(self):
        if not self.mod("櫃檯"):
            return ""
        if not getattr(self, "desk", None):
            return ("（你還沒記過櫃檯上有什麼。站到櫃檯前就用 記櫃檯 動作，"
                    "把每樣東西在你面前的相對位置（左手邊／正前方／右手邊／上方）"
                    "與使用順序記下來，之後每輪都看得到。）")
        def key(kv):
            o = kv[1].get("順序")
            return (o if isinstance(o, int) else 99, kv[0])
        rows = []
        for nm, v in sorted(self.desk.items(), key=key):
            o = v.get("順序")
            ang = v.get("角度")
            rel = ""
            if isinstance(ang, (int, float)):
                dd = (ang - getattr(self, "yaw", 0.0) + 540) % 360 - 180
                rel = (f"｜現在要看它：往{'右' if dd > 0 else '左'}轉 {abs(dd):.0f} 度"
                       f"（look dx {self.deg_dx(dd)}）" if abs(dd) > 8 else "｜就在你正前方")
            xy = v.get("座標") or []
            absp = (f"角度 {v['角度']:.0f}°" if isinstance(v.get("角度"), (int, float)) else "角度?") +                    (f"、記錄座標({xy[0]},{xy[1]})" if len(xy) == 2 else "")
            rows.append(
                f"{('第' + str(o) + '步') if isinstance(o, int) else '順序?'}　{nm}"
                f"｜用途：{v.get('用途') or '?'}"
                f"｜相對：{v.get('相對位置') or '?'}"
                f"｜絕對：{absp}"
                f"{('｜' + v['怎麼用']) if v.get('怎麼用') else ''}{rel}")
        return NL.join(rows)

    # ---------------- 認識櫃檯：先知道自己站在哪、每樣設備在自己的哪一邊
    DESK_LABELS = ["computer", "monitor", "screen", "keyboard", "printer", "stamp",
                   "paper", "document", "folder", "camera", "telephone",
                   "counter desk", "chair", "person"]

    def deg_dx(self, deg):
        """幾度 → 要拖幾像素（帶正負號，直接抄進 look 就好）。

        會先夾在單次上限 MAX_DX 之內：算出來超過上限的話，執行時本來就會被砍到上限，
        他卻以為自己轉了那麼多度（這是「照著轉還是對不準」的老原因之一），
        所以這裡直接告訴他這一輪只能轉多少、總共要轉幾次。
        """
        ppp = getattr(self, "px_per_pct", 4.0) or 4.0
        want = int(abs(deg) / 0.70 * ppp)
        cap = self.MAX_DX
        sign = "+" if deg > 0 else "-"
        if want <= cap:
            return f"{sign}{want}"
        n = max(2, -(-want // cap))          # 無條件進位
        return f"{sign}{cap}，要連續轉 {n} 次才轉得完（單次上限就是 {cap}）"

    def turn_by(self, deg):
        """程式自己轉視角，並同步更新航位推算的朝向。"""
        ppp = getattr(self, "px_per_pct", 4.0) or 4.0
        dx = int(round(deg / 0.70 * ppp))
        if dx:
            self._look_measured = None
            self.raw_look(dx, 0)      # 朝向由水平儀 gyro_add 在 raw_look 裡累加，這裡不要再加一次
        return dx

    def load_scan(self):
        self.desk_scan = None
        try:
            with open(os.path.join(self.dir, "櫃檯掃描.json"), encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict) and d.get("rows"):
                self.desk_scan = d
        except (OSError, json.JSONDecodeError, TypeError):
            pass

    def save_scan(self):
        try:
            with open(os.path.join(self.dir, "櫃檯掃描.json"), "w", encoding="utf-8") as f:
                json.dump(self.desk_scan or {}, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def find_and_go(self, labels, why="", budget=6.0):
        """原地轉一圈用 YOLO 找這些東西，找到就走過去。找不到回 False。"""
        old_watch = list(getattr(self, "watching", []) or self.eyes.labels)
        need = [l for l in labels if l not in old_watch]
        if need:
            self.eyes.watch(old_watch + need)
            time.sleep(1.5)          # 換偵測詞之後要等眼睛跑過幾幀，不然抓到的還是舊的
        found = None
        want = [l.lower() for l in labels]
        print(f"   🔎 程式幫你轉一圈找：{'、'.join(labels)}（{why}）")
        # 靈敏度時常量不準，與其相信「45 度」，不如固定用單次上限轉 12 次掃過去
        for i in range(12):
            dets, stamp = self.eyes.latest()
            if stamp and time.time() - stamp < 2.0:
                for lab, arr in (dets or {}).items():
                    ll = str(lab).lower()
                    if not any(w in ll or ll in w for w in want):
                        continue
                    for d in arr[:2]:
                        if self.eyes.is_self(d) or d["score"] < 0.15:
                            continue
                        if found is None or d["score"] > found[1]["score"]:
                            found = (lab, d)
            if found:
                break
            self.turn_by(self.MAX_DX / max(1.0, (getattr(self, "px_per_pct", 4.0) or 4.0)) * 0.70)
            time.sleep(0.6)
        if need:
            self.eyes.watch(old_watch)
        if not found:
            print("   🔎 轉了一圈都沒看到 → 交回給他自己想辦法")
            return False
        lab, d = found
        print(f"   🔎 看到「{lab}」（大小 {d['h']:.2f}）→ 程式帶你走過去")
        try:
            self.goto_target(lab, budget=budget)
        except Exception as e:  # noqa: BLE001
            print(f"   走過去失敗：{e}")
        self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] "
                            f"你找不到路，程式轉一圈找到「{lab}」並帶你走過去（{why}）；"
                            f"到了之後自己對正、確認有 E 提示再按")
        self.note_progress("程式帶路")
        # 帶到了就叫 YOLO 幫他盯住這個目標，免得他下一輪又走掉（他很常走到一半跑去看牆）
        try:
            if lab in ("counter desk", "computer monitor", "printer"):
                self.start_lock(lab)
                self.brake_hint = ("✅ 程式已經把你帶到櫃檯前面，而且叫 YOLO 幫你盯住它了。"
                                   "**不要再走開**——照【你自己列的步驟】做：先看病人、拍照、比對、"
                                   "再蓋章登記。要移動只用 a/d 小步對正。" + NL)
        except Exception:  # noqa: BLE001
            pass
        return True

    def scan_desk(self, a=None):
        """程式帶著他左右各掃 60 度，把「哪個角度看到什麼」量出來。

        他一直做不出 Check In，很大一部分是不知道自己面前有什麼、東西在左邊還是右邊。
        這一段不靠他的視覺判斷：直接用 YOLO 在五個角度各看一次，
        產生「左手邊有什麼／正前方有什麼／右手邊有什麼」的清單給他命名。
        """
        old = list(getattr(self, "watching", []) or self.eyes.labels)
        self.eyes.watch(self.DESK_LABELS)
        time.sleep(0.9)
        yaw0 = getattr(self, "yaw", 0.0)
        rows = []
        print("   🔭 開始掃視櫃檯（左 60 度 → 右 60 度，每 30 度停一次）")
        self.turn_by(-60)
        for i, ang in enumerate((-60, -30, 0, 30, 60)):
            if i:
                self.turn_by(30)
            time.sleep(0.9)
            dets, stamp = self.eyes.latest()
            seen = []
            if stamp and time.time() - stamp < 3.0:
                for k, v in (dets or {}).items():
                    for d in v[:1]:
                        if self.eyes.is_self(d):
                            continue
                        pos = "正中" if 0.42 <= d["cx"] <= 0.58 else ("偏右" if d["cx"] > 0.58 else "偏左")
                        seen.append(f"{k}（大小 {d['h']:.2f}、信心 {d['score']:.2f}、畫面{pos}）")
            rows.append([ang, seen])
            print(f"      {ang:+d} 度：{('、'.join(seen) or '什麼都沒框到')}")
            try:
                p = os.path.join(self.dir, "畫面",
                                 f"{datetime.now().strftime('%H%M%S')}_掃櫃檯{ang:+d}.jpg")
                self.grab_still(640).save(p, "JPEG", quality=70)
            except Exception:  # noqa: BLE001
                pass
        self.turn_by(-60)          # 轉回原本的朝向
        self.yaw = yaw0
        self.eyes.watch(old or self.DESK_LABELS)
        self.watching = old
        self.desk_scan = {"時間": datetime.now().strftime("%m/%d %H:%M:%S"),
                          "朝向": round(yaw0),
                          "座標": [round(self.pos[0], 2), round(self.pos[1], 2)],
                          "rows": rows}
        self.save_scan()
        self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 你掃視了一次櫃檯，"
                            f"結果在【櫃檯掃描】那一段，記得用 記櫃檯 把東西命名")

    def scan_text(self):
        if not self.mod("櫃檯"):
            return ""
        s = getattr(self, "desk_scan", None)
        if not s:
            return ('（還沒掃過。站到櫃檯前面用 {"t":"掃視櫃檯"}，'
                    "程式會帶你左右各轉 60 度、每 30 度停一次，"
                    "把每個角度看到什麼量給你，最後轉回原本的朝向。）")
        names = {-60: "左手邊", -30: "左前方", 0: "正前方", 30: "右前方", 60: "右手邊"}
        xy = s.get("座標") or [0, 0]
        rows = [f"（{s.get('時間')} 你站在 x{xy[0]:+.1f} y{xy[1]:+.1f}、朝向 {s.get('朝向')} 度時掃的）"]
        for ang, seen in s.get("rows", []):
            rows.append(f"{names.get(ang, str(ang))}（{ang:+d} 度，look dx {self.deg_dx(ang or 0.1)}）："
                        + ("、".join(seen) or "什麼都沒框到"))
        rows.append("→ 照這份清單用 記櫃檯 把每樣東西命名、寫下用途與第幾步用（一次記一樣最準）。")
        return NL.join(rows)

    def desk_layout_text(self):
        """他現在站在哪、記過的每樣設備在他的哪一邊——這是他最缺的那一塊。"""
        if not self.mod("櫃檯"):
            return ""
        import math
        yaw = getattr(self, "yaw", 0.0)
        rows = [f"你現在站在 x{self.pos[0]:+.1f} y{self.pos[1]:+.1f}、朝向 {yaw:.0f} 度"]
        st = (getattr(self, "stands", {}) or {}).get("櫃檯工作位")
        if st:
            vx, vy = st.get("x", 0) - self.pos[0], st.get("y", 0) - self.pos[1]
            dist = math.hypot(vx, vy)
            if dist < 1.0:
                face = (st.get("yaw", 0) - yaw + 540) % 360 - 180
                rows.append("✅ 你就站在櫃檯工作位上" +
                            (f"，但朝向差 {abs(face):.0f} 度 → 往{'右' if face > 0 else '左'}轉"
                             f"（look dx {self.deg_dx(face)}）" if abs(face) > 15
                             else "，朝向也對了，可以直接開始櫃檯作業（這時候按 w/a/d 是不會動的）"))
            else:
                ang = (math.degrees(math.atan2(vx, vy)) - yaw + 540) % 360 - 180
                rows.append(f"↩ 你離開櫃檯工作位了 → 往{'右' if ang > 0 else '左'}轉 {abs(ang):.0f} 度"
                            f"（look dx {self.deg_dx(ang)}）再直走約 {dist:.1f} 秒就回得去")
        else:
            rows.append('✗ 你還沒存「櫃檯工作位」，所以程式沒辦法告訴你怎麼走回櫃檯。'
                        '站到櫃檯前面就用 {"t":"記站位","name":"櫃檯工作位"}。')
        d = getattr(self, "desk", {}) or {}
        if not d:
            rows.append("（你還沒記過櫃檯上有任何東西，所以相對位置圖是空的）")
            return NL.join(rows)
        items = []
        for nm, v in d.items():
            ang = v.get("角度")
            if not isinstance(ang, (int, float)):
                continue
            items.append(((ang - yaw + 540) % 360 - 180, nm, v))
        items.sort()
        if not items:
            rows.append("（記過的東西都沒有角度：正對著那樣東西的時候再用 記櫃檯 重記一次）")
            return NL.join(rows)
        rows.append("以你現在的朝向當 0 度，每樣東西在你的哪一邊：")
        for dd, nm, v in items:
            if abs(dd) <= 10:
                side = "正前方（已經對準）"
            elif abs(dd) <= 45:
                side = "左前方" if dd < 0 else "右前方"
            elif abs(dd) <= 100:
                side = "左手邊" if dd < 0 else "右手邊"
            else:
                side = "在你後面（要轉大約半圈）"
            o = v.get("順序")
            rows.append(f"　{('第' + str(o) + '步') if isinstance(o, int) else '順序?'}　{nm}"
                        f"：{side} {abs(dd):.0f} 度"
                        + ("" if abs(dd) <= 10 else f" → look dx {self.deg_dx(dd)}")
                        + (f"｜{v.get('用途')}" if v.get("用途") else "")
                        + (f"｜{v.get('怎麼用')}" if v.get("怎麼用") else ""))
        rows.append("左 ←────── 你的正前方 ──────→ 右")
        rows.append("　" + "　".join(f"[{nm} {dd:+.0f}°]" for dd, nm, _ in items))
        return NL.join(rows)

    def desk_need(self):
        """認識櫃檯這件事還差什麼——沒補齊之前，Check In 幾乎不可能做出來。
        （櫃檯是動物醫院專屬的，別的遊戲在 遊戲.json 把「櫃檯」關掉就不會出現。）"""
        if not self.mod("櫃檯"):
            return []
        miss = []
        if "櫃檯工作位" not in (getattr(self, "stands", {}) or {}):
            miss.append('還沒存櫃檯前的站位（站到櫃檯前用 {"t":"記站位","name":"櫃檯工作位"}）')
        if not getattr(self, "desk_scan", None):
            miss.append('還沒掃過櫃檯（站到櫃檯前用 {"t":"掃視櫃檯"}）')
        d = getattr(self, "desk", {}) or {}
        if len(d) < 3:
            miss.append(f"櫃檯上的東西只記了 {len(d)} 樣，至少要 3 樣（用 記櫃檯，正對著它的時候記）")
        bad = [k for k, v in d.items()
               if not str(v.get("用途") or "").strip() or not isinstance(v.get("順序"), int)]
        if bad:
            miss.append("這幾樣還沒寫用途或第幾步用：" + "、".join(bad[:4]))
        return miss


    # ---------------- 這幾個「記」的動作以前只寫在提示裡、程式沒接（他做了等於白做）
    def record_desk(self, a):
        """記櫃檯：他寫用途/順序/相對位置，程式自動補上他當下的朝向角度與座標。"""
        items = a.get("items")
        if isinstance(items, dict):
            items = [items]
        if not isinstance(items, list) or not items:
            items = [a]                     # 他把欄位直接攤平寫在動作裡也接受
        n = 0
        for it in items:
            if not isinstance(it, dict):
                continue
            nm = str(it.get("名字") or it.get("name") or "").strip()[:12]
            if not nm:
                continue
            try:
                o = int(it.get("順序") if it.get("順序") is not None else it.get("order"))
            except (TypeError, ValueError):
                o = None
            rec = self.desk.get(nm) or {}
            for k, src in (("用途", ("用途", "use")), ("相對位置", ("相對位置",)),
                           ("怎麼用", ("怎麼用", "how"))):
                v = ""
                for s in src:
                    v = str(it.get(s) or "").strip()
                    if v:
                        break
                if v:
                    rec[k] = v[:30]
            if o is not None:
                rec["順序"] = o
            rec["角度"] = round(getattr(self, "yaw", 0.0), 0)
            rec["座標"] = [round(self.pos[0], 2), round(self.pos[1], 2)]
            rec["記於"] = datetime.now().strftime("%m/%d %H:%M")
            self.desk[nm] = rec
            n += 1
            print(f"   🗄 記下櫃檯物品「{nm}」：{rec.get('用途') or '用途?'}"
                  f"｜第 {rec.get('順序', '?')} 步｜{rec.get('相對位置') or '?'}"
                  f"｜角度 {rec['角度']:.0f} 度")
        if n:
            self.save_desk()
            self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] "
                                f"你記下 {n} 樣櫃檯物品（現在共 {len(self.desk)} 樣）")

    def record_cause(self, a):
        did = str(a.get("做了") or a.get("do") or "").strip()[:40]
        got = str(a.get("發生") or a.get("result") or "").strip()[:40]
        if not (did and got):
            return
        line = f"{did} → {got}"
        if line in self.cause:
            return
        self.cause.append(line)
        try:
            with open(os.path.join(self.dir, "因果.md"), "a", encoding="utf-8") as f:
                f.write("- " + line + NL)
        except OSError:
            pass
        print(f"   🔗 記下因果：{line}（共 {len(self.cause)} 條）")

    def record_chain(self, a):
        nm = str(a.get("名字") or a.get("name") or "").strip()[:16]
        sts = [str(x).strip()[:30] for x in (a.get("步驟") or a.get("steps") or []) if str(x).strip()][:8]
        if not (nm and sts):
            return
        self.chains[nm] = sts
        try:
            with open(os.path.join(self.dir, "連續流程.json"), "w", encoding="utf-8") as f:
                json.dump(self.chains, f, ensure_ascii=False, indent=2)
        except OSError:
            pass
        print(f"   🔁 記下連續流程「{nm}」（{len(sts)} 步）：" + "／".join(sts))

    def record_game(self, a):
        n = 0
        for k in ("名稱", "作者", "封面特徵", "進入步驟", "視角", "模式"):
            v = str(a.get(k) or "").strip()[:60]
            if v:
                self.game_id[k] = v
                n += 1
        cover = os.path.join(self.dir, "封面.jpg")
        if not os.path.exists(cover):
            try:
                self.grab_still(720).save(cover, "JPEG", quality=75)
                print("   📸 順便存了一張封面截圖")
            except Exception:  # noqa: BLE001
                pass
        if n:
            self.save_stage()
            print(f"   🎮 記下遊戲身分 {n} 個欄位：" +
                  "、".join(f"{k}={v}" for k, v in self.game_id.items()))

    # ---------------- 報到課程：一關一關學會 Check In
    def count_diffs(self):
        try:
            with open(os.path.join(self.dir, "差異筆記.md"), encoding="utf-8") as fh:
                self.diff_count = sum(1 for l in fh if l.startswith("- "))
        except OSError:
            self.diff_count = 0

    def load_spots(self):
        """腦中地圖上的互動點：哪裡有可以按的東西、當時朝哪、怎麼按成功的。"""
        self.spots = {}
        try:
            with open(self.space_file("互動點.json"), encoding="utf-8") as fh:
                d = json.load(fh)
            self.spots = {k: v for k, v in d.items() if isinstance(v, dict)}
        except (OSError, json.JSONDecodeError, TypeError):
            pass
        print(f"[互動點] 腦中地圖上已知 {len(self.spots)} 個可以互動的點")

    def save_spots(self):
        try:
            with open(self.space_file("互動點.json"), "w", encoding="utf-8") as fh:
                json.dump(self.spots, fh, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def note_spot(self, name, how="長按E"):
        """互動成功 → 把「這個東西在地圖的哪裡」記下來（他自己不會記）。"""
        nm = (name or "?").strip()[:16]
        if not nm or nm == "?":
            return
        old = self.spots.get(nm) or {}
        n = int(old.get("成功次數", 0)) + 1
        if n >= 3 and "x" in old:
            # 這個東西已經是可靠地標了：互動成功＝你就站在它面前，
            # 所以反過來用它把**漂掉的推算座標**拉回去（自動版的「我在」）。
            dx, dy = old["x"] - self.pos[0], old["y"] - self.pos[1]
            if (dx * dx + dy * dy) ** 0.5 > 0.8:
                print(f"   🧭 用地標「{nm}」自動校正座標："
                      f"({self.pos[0]:+.1f},{self.pos[1]:+.1f}) → ({old['x']:+.1f},{old['y']:+.1f})")
                self.pos = [float(old["x"]), float(old["y"])]
                self.fix_count = getattr(self, "fix_count", 0) + 1
            self.spots[nm] = {**old, "怎麼用": how, "成功次數": n,
                              "最後": datetime.now().strftime("%m/%d %H:%M")}
        else:
            self.spots[nm] = {"x": round(self.pos[0], 2), "y": round(self.pos[1], 2),
                              "朝向": round(getattr(self, "yaw", 0.0)),
                              "怎麼用": how, "成功次數": n,
                              "最後": datetime.now().strftime("%m/%d %H:%M")}
        self.save_spots()
        print(f"   🗺 腦中地圖記下互動點「{nm}」：x{self.pos[0]:+.1f} y{self.pos[1]:+.1f} 朝向 {self.yaw:.0f}°")
        try:
            if self.gmap_on() and getattr(self, "gmap", None) is not None:
                th = math.radians(getattr(self, "yaw", 0.0))
                ox, oy = self.pos[0] + math.sin(th), self.pos[1] + math.cos(th)      # 互動物在他面前那一格
                self.gmap.add_object("互動物", ox, oy, nm, "", how)
                self.gmap.save()
                self.gmap_log("記物件", 種類="互動物", 名稱=nm, 格=list(self.gmap.cell_of(ox, oy)), 用法=how, 來源="互動成功自動記")
        except Exception as e:  # noqa: BLE001
            print(f"   陣列地圖記互動物失敗：{e}")

    def goto_remembered(self, name, why=""):
        """走到腦中地圖上記過的點（互動點或地點）——先轉到那個方位，再走那麼多秒。"""
        import math
        nm = (name or "").strip()
        tgt = None
        if nm in (getattr(self, "spots", {}) or {}):
            v = self.spots[nm]
            tgt = (v.get("x", 0.0), v.get("y", 0.0), "互動點")
        elif nm in (getattr(self, "map", {}) or {}):
            x, y = self.map[nm]
            tgt = (x, y, "地點")
        if tgt is None and self.gmap_on() and getattr(self, "gmap", None) is not None:
            o = self.gmap.find_object(nm)
            if o:
                ox, oy = self.gmap.center_of(*o["格"])
                tgt = (ox, oy, o["種類"])
        if tgt is None:
            print(f"   ⚠ 腦中地圖上沒有「{nm}」")
            return False
        x, y, kind = tgt
        # 看得到就用眼睛走：視覺閉環比航位推算準得多（推算會漂，方位會忽左忽右）
        # 2026-09-27：改用這個地點自己記下的視覺特徵（原本寫死咖啡機、櫃檯這些遊戲詞）
        for lab, _cx, _h in (getattr(self, "map_sig", {}) or {}).get(nm, []):
            d = self.eyes.best(lab, max_age=2.0)
            if d and d["score"] >= 0.25 and not self.eyes.is_self(d):
                print(f"   👁 「{nm}」的特徵「{lab}」現在看得到 → 用眼睛閉環走過去（比地圖推算準）")
                self.goto_target(lab, budget=6.0)
                self.note_progress("看著目標走過去")
                return True
        vx, vy = x - self.pos[0], y - self.pos[1]
        dist = math.hypot(vx, vy)
        if dist < 0.4:
            print(f"   🧭 你已經在「{nm}」旁邊了（{dist:.1f}）")
            return True
        try:
            if self.gmap_walk(nm, x, y):        # 2026-09-30：有陣列地圖就照陣列找一條不穿牆的路
                return True
        except Exception as e:  # noqa: BLE001
            print(f"   照陣列地圖走失敗，改走直線：{e}")
        ang = (math.degrees(math.atan2(vx, vy)) - getattr(self, "yaw", 0.0) + 540) % 360 - 180
        print(f"   🧭 照腦中地圖走去「{nm}」（{kind}）：轉 {ang:+.0f}°、走 {min(dist, 6.0):.1f} 秒"
              + (f"（{why}）" if why else ""))
        left = ang
        for _ in range(6):                    # 單次轉頭有上限，分幾次轉完
            if abs(left) < 6:
                break
            step = max(-1, min(1, left)) * min(abs(left), self.MAX_DX / (self.px_per_pct or 4.0) * 0.70)
            self.turn_by(step)
            left -= step
            time.sleep(0.15)
        secs = max(0.3, min(6.0, dist))
        self._prog_walk = True
        try:
            _w, _ = self.walk_guarded("w", secs)
        finally:
            self._prog_walk = False
        _th = math.radians(getattr(self, "yaw", 0.0))      # 2026-09-30：程式帶路走的也要算進位置（以前沒算，地圖會漂）
        self.pos[0] += math.sin(_th) * _w
        self.pos[1] += math.cos(_th) * _w
        self.note_progress("照腦中地圖走過去")
        self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] "
                            f"你照腦中地圖走去「{nm}」（轉 {ang:+.0f} 度、走 {secs:.1f} 秒）")
        return True

    def mind_map_text(self):
        """腦中的俯視圖（用文字講）：我在哪、朝哪、每樣東西在我的哪個方向多遠。"""
        import math
        yaw = getattr(self, "yaw", 0.0)
        head = ("北" if yaw < 22.5 or yaw >= 337.5 else "東北" if yaw < 67.5 else
                "東" if yaw < 112.5 else "東南" if yaw < 157.5 else "南" if yaw < 202.5 else
                "西南" if yaw < 247.5 else "西" if yaw < 292.5 else "西北")
        rows = [f"**你在 (x{self.pos[0]:+.1f}, y{self.pos[1]:+.1f})，臉朝 {yaw:.0f}°（{head}）**"
                f"　（座標是從你進場那一刻開始推算的；y 是你一開始面對的方向）"]

        def rel(x, y):
            vx, vy = x - self.pos[0], y - self.pos[1]
            dist = math.hypot(vx, vy)
            ang = (math.degrees(math.atan2(vx, vy)) - yaw + 540) % 360 - 180
            a = abs(ang)
            side = ("正前方" if a <= 20 else ("右前方" if ang > 0 else "左前方") if a <= 70 else
                    ("正右邊" if ang > 0 else "正左邊") if a <= 110 else
                    ("右後方" if ang > 0 else "左後方") if a <= 160 else "正後方")
            return dist, ang, side

        spots = getattr(self, "spots", {}) or {}
        if spots:
            rows.append("── 可以互動的點（成功過才會在這裡）──")
            items = []
            for nm, v in spots.items():
                dist, ang, side = rel(v.get("x", 0), v.get("y", 0))
                items.append((dist, f"　{nm}：{side} {abs(ang):.0f}°、約 {dist:.1f} 秒的路"
                                    f"（look dx {self.deg_dx(ang) if abs(ang) > 8 else '0，已經對著'}）"
                                    f"｜{v.get('怎麼用', '長按E')}｜成功過 {v.get('成功次數', 1)} 次"))
            items.sort()
            rows += [r[1] for r in items[:6]]
        else:
            rows.append("── 可以互動的點：還沒有。**任何一次互動成功，程式就會自動把那個點記進來** ──")

        places = getattr(self, "map", {}) or {}
        if places:
            rows.append("── 你記過的地點 ──")
            items = []
            for nm, (x, y) in places.items():
                dist, ang, side = rel(x, y)
                items.append((dist, f"　{nm}：{side} {abs(ang):.0f}°、約 {dist:.1f} 秒的路"))
            items.sort()
            rows += [r[1] for r in items[:6]]
            near = min(places.items(), key=lambda kv: math.hypot(kv[1][0] - self.pos[0],
                                                                kv[1][1] - self.pos[1]))
            nd = math.hypot(near[1][0] - self.pos[0], near[1][1] - self.pos[1])
            rows.append(f"　→ 離你最近的是「{near[0]}」（約 {nd:.1f} 秒）。"
                        f"**如果你認得出自己就站在某個記過的地方，用 {{\"t\":\"我在\",\"name\":\"地名\"}} 校正**"
                        f"——座標是推算的，走久了會漂掉，校正一次全部就準了。")
        else:
            rows.append("── 你記過的地點：還沒有。走到明顯的地方就用 記位置 記起來 ──")
        return NL.join(rows)

    def load_wins(self):
        self.wins = []
        try:
            with open(os.path.join(self.dir, "成功紀錄.json"), encoding="utf-8") as fh:
                d = json.load(fh)
            if isinstance(d, list):
                self.wins = [x for x in d if isinstance(x, dict)][-40:]
        except (OSError, json.JSONDecodeError, TypeError):
            pass
        print(f"[成功紀錄] 他成功過的做法 {len(self.wins)} 筆")

    def save_wins(self):
        try:
            with open(os.path.join(self.dir, "成功紀錄.json"), "w", encoding="utf-8") as fh:
                json.dump(self.wins[-40:], fh, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def note_win(self, how, target, secs=None):
        """成功了就把整套做法記下來——他最缺的就是「上次成功是怎麼做到的」。"""
        rec = {"時間": datetime.now().strftime("%m/%d %H:%M:%S"),
               "任務": (getattr(self, "cur_task", "") or "")[:20],
               "做法": how, "目標": (target or "?")[:16],
               "秒數": round(float(secs), 2) if secs is not None else None,
               "準心": getattr(self, "_last_aim", None),
               "吸附": bool(getattr(self, "_last_snapped", False)),
               "朝向": round(getattr(self, "yaw", 0.0)),
               "前一步": (getattr(self, "_prev_move", "") or "")[:24]}
        # 2026-09-14 補接：「第一次做成某件事」+20 以前也是死碼。
        # 判準：這個目標在 tries_log 裡以前一次都沒成功過。
        try:
            tl = (getattr(self, "tries_log", None) or {}).get((target or "?")[:16], {})
            if int(tl.get("成功", 0)) <= 1:
                self.award("第一次做成某件事",
                           f"「{(target or '?')[:16]}」你以前從來沒做成過，這是第一次。"
                           f"把做法記牢：{how}"
                           + (f"、按 {float(secs):.1f} 秒" if secs is not None else ""))
        except Exception:  # noqa: BLE001
            pass
        self.wins = (getattr(self, "wins", []) + [rec])[-40:]
        self.save_wins()
        print(f"   🏆 記下成功做法：{how}「{rec['目標']}」"
              + (f" {rec['秒數']}秒" if rec["秒數"] else "")
              + (f"｜準心{rec['準心']}" if rec["準心"] else "")
              + (f"｜前一步 {rec['前一步']}" if rec["前一步"] else ""))
        self.add_note(f"（成功）{how}{rec['目標']}"
                      + (f" 按{rec['秒數']}秒" if rec["秒數"] else "")
                      + (f"，先{rec['前一步']}" if rec["前一步"] else ""))

    def wins_text(self):
        w = getattr(self, "wins", [])
        if not w:
            return ("（還沒有成功紀錄。只要有一次互動真的有反應，程式就會把你當時的做法"
                    "整套記在這裡，之後照著重做。）")
        rows = []
        for r in w[-3:][::-1]:
            bits = [f"[{r.get('時間', '')}]"]
            if r.get("任務"):
                bits.append(f"任務「{r['任務']}」")
            if r.get("前一步"):
                bits.append(f"先{r['前一步']}")
            if r.get("準心"):
                bits.append(f"準心對到 {r['準心']}" + ("（YOLO 吸附過）" if r.get("吸附") else ""))
            bits.append(f"→ {r.get('做法', '')}「{r.get('目標', '')}」"
                        + (f" 按 {r['秒數']} 秒" if r.get("秒數") else ""))
            bits.append("→ 有反應 ✅")
            rows.append("　".join(bits))
        return NL.join(rows) + NL + "→ 遇到同樣的東西就照上面的做法重做一次，不要從頭亂試。"

    PATIENT_WORDS = ("病人", "patient", "顧客", "客人", "cat", "dog", "rabbit", "貓", "狗", "兔")
    SHIFT_CHECKIN_GOAL = 4       # 使用者指定：接待到病人 4 之後就去嘗試治療

    def patient_at_window(self):
        """窗口有沒有人在等（先看眼睛，再看他自己講的話）。"""
        dets, stamp = self.eyes.latest()
        if stamp and time.time() - stamp < 2.5:
            for lab, arr in (dets or {}).items():
                l = str(lab).lower()
                if any(w in l for w in ("person", "cat", "dog", "rabbit", "patient")):
                    for d in arr[:2]:
                        if not self.eyes.is_self(d) and d["score"] >= 0.30:
                            return True
        seen = str(getattr(self, "last_seen", ""))
        if any(w in seen for w in ("沒有病人", "沒人", "窗口空", "沒有人")):
            return False
        return any(w in seen for w in self.PATIENT_WORDS)

    LOBBY_WORDS = ("大廳", "商店", "邀請", "職業", "日誌", "伺服器", "Play", "播放",
                   "配對", "遊戲頁面", "第三人稱")

    @property
    def GAME_PLACES(self):
        """這個遊戲的 placeId → 場景名。內容在 專案/<遊戲>/遊戲.json，換遊戲不用改程式。"""
        return (self.gcfg.get("places") or {})

    @property
    def LOBBY_PLACE(self):
        return int(self.gcfg.get("大廳place") or 0) or next(iter(self.GAME_PLACES), 0)

    def current_place(self):
        """從 Roblox 用戶端的 log 讀出「現在人在哪個 place」——這是事實，不用猜畫面。"""
        if time.time() - getattr(self, "_place_at", 0) < 20:
            return getattr(self, "_place", None)
        self._place_at = time.time()
        pid = None
        try:
            import glob as _glob
            import re as _re
            d = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Roblox", "logs")
            # 2026-10-04：只看 Player 的 log。Studio 開著時它的 log 比較新，裡面也有「Joining game … place N」
            # （試玩／Team Create），會被當成「現在人在那個 place」→ 假的進錯遊戲、每 3 分鐘被深層連結重連一次
            # （10/02 發現的坑；10/04 23:14 實際發生：Studio 開著 89593991832201，他人在 105778205563598）。
            files = sorted([f for f in _glob.glob(os.path.join(d, "*Player*.log"))
                            if "CrashHandler" not in os.path.basename(f)],
                           key=os.path.getmtime, reverse=True)[:3]
            for fp in files:
                try:
                    size = os.path.getsize(fp)
                    with open(fp, "rb") as fh:              # 這個 log 會長到十幾 MB，只讀尾巴
                        fh.seek(max(0, size - 20_000_000))
                        txt = fh.read().decode("utf-8", "ignore")
                except OSError:
                    continue
                ms = _re.findall(r"Joining game '[^']+' place (\d+)", txt)
                if ms:
                    pid = int(ms[-1])
                    break
        except Exception:  # noqa: BLE001
            pid = None
        self._place = pid
        return pid

    def current_job(self):
        """從 Roblox log 讀「現在這一場」的 job id（每加入一場就換一個）。"""
        if time.time() - getattr(self, "_job_at", 0) < 10:
            return getattr(self, "_job", None)
        self._job_at = time.time()
        job = None
        try:
            import glob as _glob
            import re as _re
            d = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Roblox", "logs")
            # 2026-10-04：只看 Player 的 log。Studio 開著時它的 log 比較新，裡面也有「Joining game … place N」
            # （試玩／Team Create），會被當成「現在人在那個 place」→ 假的進錯遊戲、每 3 分鐘被深層連結重連一次
            # （10/02 發現的坑；10/04 23:14 實際發生：Studio 開著 89593991832201，他人在 105778205563598）。
            files = sorted([f for f in _glob.glob(os.path.join(d, "*Player*.log"))
                            if "CrashHandler" not in os.path.basename(f)],
                           key=os.path.getmtime, reverse=True)[:3]
            for fp in files:
                try:
                    size = os.path.getsize(fp)
                    with open(fp, "rb") as fh:
                        fh.seek(max(0, size - 4_000_000))
                        txt = fh.read().decode("utf-8", "ignore")
                except OSError:
                    continue
                ms = _re.findall(r"Joining game '([0-9a-f-]+)' place (\d+)", txt)
                if ms:
                    job = ms[-1][0]
                    break
        except Exception:  # noqa: BLE001
            job = None
        self._job = job
        return job

    def watch_join(self):
        """job id 換了＝他真的進到一場新的遊戲 → 加分（使用者 2026-09-17）。"""
        job = self.current_job()
        if not job:
            return
        prev = getattr(self, "_job_prev", None)
        self._job_prev = job
        if prev is None or job == prev:
            return
        self._enter_at = time.time()          # 這條命從現在開始算
        try:
            self.award("成功進場", "Roblox 紀錄顯示你加入了新的一場（job 換了）")
        except Exception:  # noqa: BLE001
            pass
        # 使用者 2026-09-17：**進場成功要他自己記下是怎麼進來的**。
        # 這是他卡最久的一件事，做成一次卻沒記＝下次還是從頭亂試。
        self._join_ask_until = self.stat.get("輪數", 0) + 6
        self.join_hint = (
            "🎉 **你剛剛真的進到一場新的遊戲裡了**（Roblox 的紀錄變了，不是你自己猜的）。" + NL
            + "這是你卡最久的一件事——**現在立刻把你剛才是怎麼進來的寫下來**，"
            + "不然下一次你還是要從頭亂試：" + NL
            + '　{"t":"記知識","檔":"如何進場","內容":"從哪一區、對準什麼、點哪個座標、'
              '按什麼鍵、等幾秒、看到什麼畫面代表成功（一步一句）"}' + NL
            + "寫完之後，等一下程式會讓你回推一次，那時再補「哪一步是關鍵」。" + NL)

    def wrong_game(self):
        """現在是不是在別的遊戲裡。回傳 (是不是, placeid)。"""
        pid = self.current_place()
        if pid is None or not self.GAME_PLACES:
            return False, pid                      # 遊戲.json 沒寫 places 就不做這個判斷
        return (pid not in self.GAME_PLACES), pid

    def relaunch_roblox_if_gone(self):
        """2026-10-03 使用者：Roblox 被關掉（10/03 09:31 那次空等了 3 小時）就自己用深層連結重開回大廳。"""
        try:
            import subprocess
            out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq RobloxPlayerBeta.exe", "/NH"],
                                 capture_output=True, text=True, timeout=15).stdout
        except Exception:  # noqa: BLE001
            return False
        if "RobloxPlayerBeta" in out or not self.LOBBY_PLACE:
            return False
        url = f"roblox://experiences/start?placeId={self.LOBBY_PLACE}"
        print(f"   🔁 Roblox 不在了 → 程式用深層連結重開回「{self.game}」大廳：{url}")
        try:
            os.startfile(url)          # noqa: S606  (Windows 專用)
        except Exception as e:  # noqa: BLE001
            print(f"   重開 Roblox 失敗：{e}")
            return False
        self.stat["重開Roblox"] = self.stat.get("重開Roblox", 0) + 1
        self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] ⚙ Roblox 被關掉了，程式替你重開並連回「{self.game}」大廳——"
                            "畫面回來後先做開場確認三件事")
        time.sleep(30)
        return True

    # 離開遊戲後超過這麼久還沒回到任何一場，程式就用深層連結送回。
    # 10/05 02:16 實跑：原本設 60 秒，他離開 39 秒後就點了頁面上的藍色播放鍵——那一頁是別人遊戲的頁面——
    # 又進錯遊戲 3 分鐘才被拉回。改成 0＝程式一發現他不在遊戲裡（離開後的下一輪）就送回，不等他去點。
    OUT_OF_GAME_SECS = 0
    BACK_COOLDOWN = 90           # 送回之後這麼久之內不重送（等它加入完）

    def out_of_game(self):
        """2026-10-04 使用者：「改掉」離開後回不來。
        人是不是已經離開遊戲、回到 Roblox 自己的頁面（首頁／別的遊戲的頁面／商店頁）——看 Roblox log：
        最後一筆 leaveUGCGameInternal 比最後一筆 Joining game 晚＝離開之後沒有再加入任何一場。
        這時 current_place() 還是舊的 place，wrong_game() 不會動；Roblox 行程也還在，relaunch 也不會動
        （10/04 11:16 他就這樣停在別人遊戲的 399 Robux 購買頁 95 分鐘）。讀不到紀錄就當作還在遊戲裡。"""
        # 快取只留 3 秒：10/05 07:26 實跑，15 秒快取讓「離開後的下一輪」拿到舊的 False，
        # 晚了一輪才送回，他已經點了別人遊戲的播放鍵（進去 2 秒就被這裡拉回）。
        if time.time() - getattr(self, "_out_at", 0) < 3:
            return getattr(self, "_out", False)
        self._out_at = time.time()
        out = False
        try:
            d = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Roblox", "logs")
            files = [f for f in glob.glob(os.path.join(d, "*Player*.log"))
                     if "CrashHandler" not in os.path.basename(f)]       # Studio 的 log 不看
            for fp in sorted(files, key=os.path.getmtime, reverse=True)[:3]:
                try:
                    size = os.path.getsize(fp)
                    with open(fp, "rb") as fh:
                        fh.seek(max(0, size - 20_000_000))
                        raw = fh.read()
                except OSError:
                    continue
                left = log_says_left(raw)
                if left is None:
                    continue                    # 深層連結轉接用的短 log 沒有這兩種紀錄，看下一個
                out = left
                break
        except Exception:  # noqa: BLE001
            out = False
        self._out = out
        return out

    def back_into_game(self, secs):
        """人不在任何一場遊戲裡超過 OUT_OF_GAME_SECS → 用 roblox:// 深層連結送回這個遊戲的大廳。"""
        # 冷卻只是為了不要在「正在加入」的那幾十秒裡重複送連結（實測加入最慢 53 秒）。
        # 10/05 03:25 實跑：原本跟進錯遊戲共用 180 秒，他被送回後 2 分半又離開，差 1 秒被冷卻擋掉，
        # 又自己點進別人的遊戲。改成自己的 90 秒；剛做過進錯遊戲救援的 60 秒內也不重送。
        now = time.time()
        if (now - getattr(self, "_back_at", 0) < self.BACK_COOLDOWN
                or now - getattr(self, "_rejoin_at", 0) < 60 or not self.LOBBY_PLACE):
            return False
        self._back_at = now
        url = f"roblox://experiences/start?placeId={self.LOBBY_PLACE}"
        print(f"   🔁 人不在遊戲裡已經 {secs:.0f} 秒（Roblox 紀錄：離開之後沒有再加入）"
              f"→ 程式用深層連結送回「{self.game}」：{url}")
        try:
            os.startfile(url)          # noqa: S606  (Windows 專用)
        except Exception as e:  # noqa: BLE001
            print(f"   深層連結失敗：{e}")
            return False
        self.stat["送回遊戲"] = self.stat.get("送回遊戲", 0) + 1
        self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] ⚙ 程式替你做了：你離開遊戲之後 {secs:.0f} 秒都沒有回到遊戲裡，"
                            f"程式用連結把你送回「{self.game}」大廳——畫面回來後先做開場確認三件事")
        self._out_since, self._out_at = 0, 0
        time.sleep(12)
        return True

    def rejoin_correct_game(self, why=""):
        """用 roblox:// 深層連結直接跳回正確的遊戲大廳（進錯遊戲時的唯一可靠救援）。"""
        if time.time() - getattr(self, "_rejoin_at", 0) < 180:
            return False
        self._rejoin_at = time.time()
        if not self.LOBBY_PLACE:
            print("   ⚠ 進錯遊戲，但 遊戲.json 沒寫 大廳place，程式沒辦法把你送回去")
            return False
        url = f"roblox://experiences/start?placeId={self.LOBBY_PLACE}"
        print(f"   🚨 進錯遊戲（{why}）→ 程式用深層連結把你丟回「{self.game}」：{url}")
        try:
            os.startfile(url)          # noqa: S606  (Windows 專用)
        except Exception as e:  # noqa: BLE001
            print(f"   深層連結失敗：{e}")
            return False
        self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 你進錯遊戲了，"
                            f"程式已經把你送回「{self.game}」；下次加入之前一定要先比對遊戲身分")
        self.add_note("加入遊戲前一定要比對名稱與封面，進錯遊戲會浪費整段時間")
        time.sleep(12)
        return True

    def place_text(self):
        pid = self.current_place()
        if pid is None:
            return "（讀不到 Roblox 的紀錄，用畫面自己判斷你在哪）"
        if not self.GAME_PLACES:
            return f"（你現在在 place {pid}；這個遊戲還沒記過 placeId，記起來以後就認得出進錯遊戲）"
        if pid in self.GAME_PLACES:
            return f"✅ 你現在在正確的遊戲：{self.GAME_PLACES[pid]}（place {pid}）"
        return (f"🚨 **你現在在別的遊戲裡**（place {pid}，不是「{self.game}」）——"
                f"這是最嚴重的錯誤。立刻離開這個遊戲回大廳，重新加入「{self.game}」；"
                f"程式也會自動把你送回去。")

    def in_lobby(self):
        """人在戶外大廳（還沒進醫院上班）。placeid 說了算，其次才看畫面。"""
        pid = self.current_place()
        if pid == self.LOBBY_PLACE:
            return True
        if pid in self.GAME_PLACES:
            return False
        seen = str(getattr(self, "last_seen", ""))
        if "輪班" in seen:
            return False
        return any(k in seen for k in self.LOBBY_WORDS)

    def shift_plan(self):
        """回傳 (該做哪個流程, 為什麼)。使用者的規則：
           報到滿 4 位 → 去治療；這一波沒有病人 → 去治療；否則繼續報到。
           （理智低於 20% 一律先去補，撐完整班比多做一個病人重要。）
           這整套是動物醫院的班表；別的遊戲在 遊戲.json 把「輪班計畫」關掉就不跑。"""
        if not self.mod("輪班計畫"):
            return self.flow, ""
        if self.in_lobby():
            return "開始上班", ("你人在**大廳**（沒有「輪班 N」）——現在唯一該做的是重新進醫院："
                              "比對【你記過的遊戲身分】→ 往下捲伺服器清單挑人多的場 → 加入 → "
                              "看到「輪班 N」才算進去了")
        if self.san is not None and self.san <= 20:
            return "喝咖啡", f"理智只剩 {self.san}%——先去補，補完馬上回來"
        done = self.stat.get("登記", 0) - getattr(self, "shift_base_checkin", 0)
        if done >= self.SHIFT_CHECKIN_GOAL:
            return "治療", f"這一班已經報到 {done} 位（滿 {self.SHIFT_CHECKIN_GOAL} 位）"
        if self.patient_at_window():
            self.no_patient = 0
            return "報到", "窗口有病人在等"
        self.no_patient = getattr(self, "no_patient", 0) + 1
        if self.no_patient >= 3:
            return "治療", f"連續 {self.no_patient} 輪窗口都沒有病人，不要空等"
        return "報到", f"暫時沒看到病人（第 {self.no_patient} 輪，再看一下）"

    def drop_game_lines(self, tpl):
        """把只有某些遊戲才有意義的整行從面板模板拿掉（模組關掉的那些）。"""
        drop = []
        if not self.mod("輪班計畫"):
            drop.append("【成績】")
        if not self.mod("櫃檯"):
            drop += ["【你現在在哪、櫃檯設備在你的哪一邊】", "【櫃檯掃描結果】", "【你記下的櫃檯配置】"]
        if not self.mod("報到課程"):
            drop.append("【報到課程】")
        if not drop:
            return tpl
        return NL.join(l for l in tpl.split(NL) if not any(l.startswith(d) for d in drop))

    def shift_plan_text(self):
        if not self.mod("輪班計畫"):
            return ""
        want, why = self.shift_plan()
        done = self.stat.get("登記", 0) - getattr(self, "shift_base_checkin", 0)
        shift = self.score.get("目前輪班", "?")
        rows = [f"**目標：靠自己把現在這一個輪班從頭撐到尾**（畫面上方寫的是輪班 "
                f"{shift if shift not in (None, '?') else '?'}；以畫面為準）"
                "——不要死、不要離開遊戲；理智 20% 以上就繼續工作",
                f"這一班已經報到 {done}/{self.SHIFT_CHECKIN_GOAL} 位病人。",
                f"現在該做的是「**{want}**」——{why}。"]
        if want != self.flow:
            rows.append(f"你現在標的流程是「{self.flow}」，**這一輪就把 flow 換成「{want}」**。")
        rows.append("規則：有病人就報到；報到滿 4 位、或這一波沒有病人，就去治療；"
                    "治療完或又有病人來了就回來報到。整班都要有事做，不要在原地空等。")
        return NL.join(rows)

    def load_course(self):
        self.course = {"通過": {}, "進關輪數": 0, "目前": ""}
        try:
            with open(os.path.join(self.dir, "報到課程.json"), encoding="utf-8") as fh:
                d = json.load(fh)
            if isinstance(d, dict):
                self.course.update({k: d.get(k, self.course[k]) for k in self.course})
        except (OSError, json.JSONDecodeError, TypeError):
            pass
        done = len(self.course.get("通過") or {})
        print(f"[報到課程] 已經過了 {done}/{len(CHECKIN_COURSE)} 關")

    def save_course(self):
        try:
            with open(os.path.join(self.dir, "報到課程.json"), "w", encoding="utf-8") as fh:
                json.dump(self.course, fh, ensure_ascii=False, indent=2)
        except OSError:
            pass

    DESK_WORDS = ("櫃檯", "電腦", "登記", "表格", "蓋章", "印章", "文件", "照片",
                  "螢幕", "窗口", "check", "Check", "報到", "病人資料")

    def course_check(self, key):
        """每一關的驗收：回傳 (過了沒, 還差什麼)。全部用他自己累積的資料判斷。"""
        if key == "C1":
            ok = "櫃檯工作位" in (getattr(self, "stands", {}) or {})
            return ok, "" if ok else "還沒存「櫃檯工作位」這個站位"
        if key == "C2":
            miss = self.desk_need()
            return (not miss), "；".join(miss)
        if key == "C3":
            n = len([c for c in getattr(self, "cause", []) if any(w in c for w in self.DESK_WORDS)])
            return n >= 3, f"跟櫃檯有關的因果只有 {n} 條（要 3 條，用 記因果）"
        if key == "C4":
            n = getattr(self, "diff_count", 0)
            return n >= 3, f"diff 只寫過 {n} 次（要 3 次：比了什麼、差在哪）"
        if key == "C5":
            got = self.stat.get("登記", 0)
            has = bool((self.steps.get("報到") or {}).get("steps"))
            miss = []
            if got < 1:
                miss.append("還沒完成過一次報到")
            if not has:
                miss.append("「報到」還沒列步驟（用 列步驟）")
            return (not miss), "；".join(miss)
        if key == "C6":
            got = self.stat.get("登記", 0)
            return got >= 5, f"完成報到 {got} 次（要 5 次）"
        return False, ""

    def course_now(self):
        """現在在第幾關（第一個還沒過的），順便把新過的關記起來。"""
        cur = None
        for i, st in enumerate(CHECKIN_COURSE):
            ok, miss = self.course_check(st["key"])
            if ok:
                if st["key"] not in self.course["通過"]:
                    self.course["通過"][st["key"]] = {
                        "時間": datetime.now().strftime("%m/%d %H:%M:%S"),
                            "輪數": self.stat.get("輪數", 0)}
                    self.save_course()
                    print(f"   🎓 報到課程過了第 {i + 1} 關：{st['名']}")
                    self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] "
                                        f"你學會了「{st['名']}」（報到課程第 {i + 1} 關）")
                    self.add_note(f"（課程）學會了{st['名']}")
                continue
            cur = (i, st, miss)
            break
        if cur is None:
            return None
        i, st, miss = cur
        if self.course.get("目前") != st["key"]:
            self.course["目前"] = st["key"]
            self.course["進關輪數"] = self.stat.get("輪數", 0)
            self.save_course()
        return i, st, miss

    def course_text(self):
        if not self.mod("報到課程"):
            return ""
        cur = self.course_now()
        if cur is None:
            return ("✅ 六關都過了——你已經學會 Check In 了。接下來照自己記的步驟一直做，"
                    "遇到新狀況就補進步驟與因果。")
        i, st, miss = cur
        stuck = self.stat.get("輪數", 0) - int(self.course.get("進關輪數", 0) or 0)
        rows = [f"**你在第 {i + 1} 關／共 {len(CHECKIN_COURSE)} 關：{st['名']}**（這一關已經練了 {stuck} 輪）",
                "目標：" + st["目標"],
                "怎麼做：" + st["怎麼做"],
                "驗收：" + st["驗收"] + ("　→ 還差：" + miss if miss else "")]
        done = [f"{c['key']} {c['名']}" for c in CHECKIN_COURSE[:i]]
        if done:
            rows.append("已經過的關：" + "、".join(done))
        later = [c["名"] for c in CHECKIN_COURSE[i + 1:]]
        if later:
            rows.append("後面還有（現在不用管）：" + "、".join(later))
        if stuck >= 60:
            rows.append("⚠ 這一關卡太久了：換個做法——用 實驗 量一次、用 wiki 查一次、"
                        "或換一樣設備試試看，不要重複同一個失敗的動作。")
        return NL.join(rows)

    def load_steps(self):
        self.steps = {}
        p = os.path.join(self.dir, "步驟.json")
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            self.steps = {k: {"steps": list(v.get("steps", [])), "idx": int(v.get("idx", 0))}
                          for k, v in d.items() if isinstance(v, dict)}
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
        n = sum(len(v["steps"]) for v in self.steps.values())
        print(f"[步驟] 他自己列過 {len(self.steps)} 組、共 {n} 步")

    def save_steps(self):
        try:
            with open(os.path.join(self.dir, "步驟.json"), "w", encoding="utf-8") as f:
                json.dump(self.steps, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def steps_text(self):
        d = self.steps.get(self.flow)
        if not d or not d["steps"]:
            return ("（你還沒替「" + self.flow + "」列步驟。用 列步驟 動作把它拆成幾步，"
                    "之後每輪我都會提醒你做到第幾步。）")
        rows = []
        for i, st in enumerate(d["steps"]):
            rows.append(("👉 " if i == d["idx"] else "   ") + f"{i + 1}. {st}")
        return NL.join(rows)

    # ---------------- 自己畫的地圖（航位推算）
    # ---------------- 場地劃分：他覺得「這是另一個空間」就切一個（使用者 2026-09-15 要求）
    # 為什麼要切：座標是航位推算的，被傳送、搭電梯、換樓層之後，舊座標系整個作廢——
    # 繼續用同一張圖，記過的每個點都會指到錯的地方。切了空間＝換一套乾淨的座標與地圖。
    def space_file(self, base):
        """這個空間自己的座標檔。第一個空間沿用原檔名（舊專案不用搬）。"""
        sp = getattr(self, "space", "") or ""
        if not sp or sp == getattr(self, "SPACE0", "主空間"):
            return os.path.join(self.dir, base)
        bad = set(chr(92) + '/:*?"<>|')
        safe = "".join(ch for ch in sp if ch not in bad)[:20]
        return os.path.join(self.dir, base.replace(".json", f"_{safe}.json"))

    SPACE0 = "主空間"

    def load_spaces(self):
        self.spaces = {}
        self.space = self.SPACE0
        try:
            with open(os.path.join(self.dir, "空間.json"), encoding="utf-8") as f:
                d = json.load(f)
            self.spaces = {k: v for k, v in (d.get("空間") or {}).items() if isinstance(v, dict)}
            self.space = str(d.get("目前") or self.SPACE0)
        except (OSError, json.JSONDecodeError, TypeError):
            pass
        self.spaces.setdefault(self.space, {"建立": datetime.now().strftime("%m/%d %H:%M"), "怎麼過去": ""})
        print(f"[空間] 他把場地切成 {len(self.spaces)} 個：{'、'.join(self.spaces)}　現在在「{self.space}」")

    def save_spaces(self):
        try:
            with open(os.path.join(self.dir, "空間.json"), "w", encoding="utf-8") as f:
                json.dump({"目前": self.space, "空間": self.spaces}, f, ensure_ascii=False, indent=1)
        except OSError:
            pass

    def switch_space(self, name, why=""):
        """切到別的空間（沒有就新建）。舊空間的地圖存起來，新空間座標歸零重來。"""
        nm = (name or "").strip()[:16]
        if not nm:
            print("   ⚠ 切空間要給名字")
            return False
        if nm == self.space:
            print(f"   （你已經在「{nm}」了）")
            return False
        for fn in (self.save_map, self.save_spots, self.save_stands):
            try:
                fn()
            except Exception:  # noqa: BLE001
                pass
        old = self.space
        self.spaces.setdefault(old, {})["最後離開"] = datetime.now().strftime("%m/%d %H:%M")
        new = nm not in self.spaces
        self.space = nm
        self.spaces.setdefault(nm, {"建立": datetime.now().strftime("%m/%d %H:%M")})
        if why:
            self.spaces[nm]["怎麼過去"] = str(why)[:60]
        self.spaces[nm]["最後進入"] = datetime.now().strftime("%m/%d %H:%M")
        self.save_spaces()
        for fn in (self.load_map, self.load_spots, self.load_stands):
            try:
                fn()
            except Exception:  # noqa: BLE001
                pass
        self.pos = [0.0, 0.0]
        self.yaw = 0.0
        self.trail = []
        print(f"   🚪 空間切換：「{old}」→「{nm}」{'（新的）' if new else '（回到記過的）'}"
              f"　座標與朝向歸零，這個空間的地圖從現在開始畫")
        self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 你把場地切成新空間「{nm}」"
                            + (f"（{why}）" if why else "") + "：座標歸零、地圖分開記")
        self.log({"時間": datetime.now().strftime("%H:%M:%S"), "模式": "切空間",
                  "從": old, "到": nm, "為什麼": why})
        return True

    def note_zone(self, z):
        """他自己標的『區』：程式只記帳（停留幾輪、從哪裡來、這一區有沒有成功過）。
        使用者 2026-09-17：要他**明確把遊戲分割成幾區**，分法他自己定。"""
        z = str(z or "").strip()[:8]
        if not z:
            return
        self.zones = getattr(self, "zones", None) or {}
        prev = getattr(self, "cur_zone", "")
        d = self.zones.setdefault(z, {"輪數": 0, "第一次": datetime.now().strftime("%m/%d %H:%M"),
                                      "來自": {}, "成功": 0})
        d["輪數"] += 1
        d["最後"] = datetime.now().strftime("%m/%d %H:%M")
        if prev and prev != z:
            d["來自"][prev] = d["來自"].get(prev, 0) + 1
            self._zone_since = self.stat.get("輪數", 0)
            print(f"   🚩 你把這裡標成另一區：「{prev}」→「{z}」")
            self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 你從「{prev}」進到「{z}」")
        self.cur_zone = z
        try:
            with open(os.path.join(self.dir, "區段.json"), "w", encoding="utf-8") as f:
                json.dump({"目前": z, "區": self.zones}, f, ensure_ascii=False, indent=1)
        except OSError:
            pass

    def zones_text(self):
        zs = getattr(self, "zones", None) or {}
        cur = getattr(self, "cur_zone", "")
        if not zs:
            return ('（你還沒把這隻遊戲分過區。每一輪的 "區" 欄位就是在問你：'
                    "**你現在在哪一區**——自己命名，看到畫面整個換了就換一個名字。）")
        rows = []
        for k, v in sorted(zs.items(), key=lambda kv: -kv[1]["輪數"]):
            mark = "👉 " if k == cur else "　 "
            src = "、".join(f"{a}→此 {b} 次" for a, b in list(v.get("來自", {}).items())[:3])
            rows.append(f"{mark}**{k}**：待過 {v['輪數']} 輪、成功 {v.get('成功', 0)} 次"
                        + (f"｜怎麼來的：{src}" if src else ""))
        stuck = self.stat.get("輪數", 0) - int(getattr(self, "_zone_since", 0) or 0)
        if cur and stuck > 40:
            rows.append(f"⚠ 你在「{cur}」已經連續 {stuck} 輪沒換過區。"
                        f"**這一區你想做的事做得出來嗎？做不出來就換一區、或換一條路試**。")
        return NL.join(rows)

    def quickdeath_text(self):
        """一進場就死：程式照實講，怎麼辦是他自己決定。"""
        lives = getattr(self, "_lives", None) or []
        if not lives:
            return ""
        short = [x for x in lives[-5:] if x is not None and x < 90]
        if len(short) < 2:
            return ""
        avg = sum(short) / len(short)
        return (f"⚡ **你最近 {len(short)} 次都是進場不到 {avg:.0f} 秒就死了。**"
                f"這種節奏下，你一輪要想 20~40 秒——**想完的時候你已經死了**。" + NL
                + "程式已經幫你把死前的畫面錄下來（每秒一張），死後的檢討會把那幾十秒攤給你看，"
                + "所以**局內不用急著看清楚，事後再分析**。" + NL
                + "你可以自己決定要不要換做法，例如：進場前先把要做的幾步寫成程序"
                + '（{"t":"寫程序","名":"...","步驟":[...]}），進場後直接執行程序照跑；'
                + '或把思考強度調成反射（{"t":"思考強度","強度":"反射"}）先活下來，'
                + "安全了再調回快思、把欠的檢討補完。")

    def relocate_window(self):
        """【重新定位】技能（使用者 2026-09-16）：視窗被改過之後，重新量一次自己的畫面，
        並把滑鼠相關的換算跟著改。程式做的是**算術**（比例換算），
        算出來準不準要他自己送一個 dx 驗證。"""
        self.win.hwnd = None                     # 強制重找，視窗可能換了 hwnd
        b = self.win.client_bbox()
        if not b:
            print("   ⚠ 重新定位：抓不到遊戲視窗")
            self.proc_hint = (getattr(self, "proc_hint", "") or "") + "⚠ 重新定位失敗：抓不到遊戲視窗。" + NL
            return
        w, h = b["width"], b["height"]
        path = os.path.join(self.dir, "視角校準.json")
        cal = {}
        try:
            with open(path, encoding="utf-8") as f:
                cal = json.load(f) or {}
        except (OSError, json.JSONDecodeError):
            pass
        old = cal.get("視窗") or {}
        ow, oh = old.get("寬"), old.get("高")
        msg = [f"🎯 重新定位：畫面現在 **{w} × {h}**（左上角在螢幕 {b['left']},{b['top']}）"]
        o = self.offscreen()
        bad = {k: v for k, v in o.items() if v > 0.005}
        msg.append("整個畫面滑鼠都碰得到。" if not bad else
                   "碰不到的邊：" + "、".join(f"{k} {v*100:.0f}%" for k, v in bad.items()))
        if ow and oh:
            rx, ry = w / float(ow), h / float(oh)
            odx = (cal.get("水平") or {}).get("每度dx")
            ody = (cal.get("垂直") or {}).get("每度dy")
            msg.append(f"上次量刻度時是 {ow} × {oh} → 現在是 {rx:.2f} 倍寬、{ry:.2f} 倍高。")
            if odx:
                ndx = round(float(odx) * rx, 2)
                cal.setdefault("水平", {})["每度dx"] = ndx
                cal["水平"]["方法"] = f"比例換算（{odx}×{rx:.2f}）"
                cal["水平"]["時間"] = datetime.now().strftime("%m/%d %H:%M")
                self.dx_per_deg = ndx
                self.px_per_pct = max(1.5, min(15.0, round(ndx * self.DEG_PER_PCT, 1)))
                msg.append(f"每度 dx：{odx} → **{ndx}**（比例推的）")
            if ody:
                ndy = round(float(ody) * ry, 2)
                cal.setdefault("垂直", {})["每度dy"] = ndy
                cal["垂直"]["方法"] = f"比例換算（{ody}×{ry:.2f}）"
                cal["垂直"]["時間"] = datetime.now().strftime("%m/%d %H:%M")
                self.dy_per_deg = ndy
                msg.append(f"每度 dy：{ody} → **{ndy}**（比例推的）")
            msg.append("⚠ 這是**用比例算的，不是量的**：送一個已知的 dx／dy，"
                       "看水平儀的度數實際變多少來驗證；差太多就送 {\"t\":\"量刻度\"} 重量一次。")
        else:
            msg.append("沒有『上次量刻度時的畫面大小』可以比 → 這次沒辦法用比例推，"
                       "要重新量：{\"t\":\"量刻度\"}（量完程式就會把這次的尺寸記起來）。")
        cal["視窗"] = {"寬": w, "高": h, "時間": datetime.now().strftime("%m/%d %H:%M")}
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(cal, f, ensure_ascii=False, indent=2)
        except OSError:
            pass
        text = NL.join(msg)
        print("   " + text.replace(NL, NL + "   "))
        self.proc_hint = (getattr(self, "proc_hint", "") or "") + text + NL
        self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 你重新定位了視窗：{w}×{h}")
        self.note_progress("重新定位")

    def act_log(self, a, extra=None):
        """把剛做的動作記成**可重播**的形式（使用者 2026-09-17：重複步驟要能包成 function）。"""
        lg = getattr(self, "_acts", None)
        if lg is None:
            lg = self._acts = []
        lg.append((time.time(), dict(a or {}), dict(extra or {})))
        del lg[:-300]

    @staticmethod
    def _act_sig(act):
        """把一個動作壓成可比對的簽名（座標與秒數先分桶，免得差一點就算不同）。"""
        t = str(act.get("t") or "")
        if t == "key":
            return f"key:{str(act.get('k'))[:4]}:{round(float(act.get('d') or 0.5), 1)}"
        if t in ("點字", "點文字"):
            return f"txt:{str(act.get('字') or '')[:10]}"
        if t == "click":
            return f"clk:{round(float(act.get('x') or 0), 1)},{round(float(act.get('y') or 0), 1)}"
        if t == "scroll":
            return f"scr:{int(act.get('v') or 0)}"
        if t == "wait":
            return "wait"
        if t == "hold":
            return f"hold:{str(act.get('k') or 'e')[:2]}"
        return ""

    def watch_repeats(self):
        """**同一串動作重複做很多次，就自動把它包成 function**（使用者 2026-09-17）。

        他重複的東西很多（進場、離牆、修視角再點按鈕），但從來不會自己去寫程序。
        所以程式自己看：最近的動作流水帳裡，有沒有長度 2~5 的連續片段出現 10 次以上；
        有的話就直接存成程序，並告訴他「這串你做了 N 次，已經幫你存成 X，以後一句話跑完」。
        """
        lg = getattr(self, "_acts", None) or []
        if len(lg) < 30 or self.stat.get("輪數", 0) % 20 != 0:
            return
        sigs = [self._act_sig(act) for _, act, _ in lg[-200:]]
        sigs = [x for x in sigs if x]
        made = getattr(self, "_auto_proc_made", None)
        if made is None:
            made = self._auto_proc_made = set()
        best = None
        for n in (5, 4, 3, 2):
            counts = {}
            for i in range(len(sigs) - n + 1):
                key = "→".join(sigs[i:i + n])
                counts[key] = counts.get(key, 0) + 1
            for key, c in counts.items():
                if c >= 10 and key not in made and len(set(key.split("→"))) > 1:
                    if best is None or c * n > best[1] * len(best[0].split("→")):
                        best = (key, c, n)
            if best:
                break
        if not best:
            return
        key, cnt, n = best
        # 把簽名還原成程序步驟（用最後一次出現的實際參數）
        want = key.split("→")
        steps, idx = [], None
        for i in range(len(lg) - n, -1, -1):
            seg = [self._act_sig(act) for _, act, _ in lg[i:i + n]]
            if seg == want:
                idx = i
                break
        if idx is None:
            return
        for _, act, ex in lg[idx:idx + n]:
            t = str(act.get("t") or "")
            if t == "key":
                steps.append({"做": "按鍵", "鍵": str(act.get("k")), "秒": round(float(act.get("d") or 0.5), 2)})
            elif t in ("點字", "點文字"):
                steps.append({"做": "點", "位置": [round(float(ex.get("x", 0.5)), 3),
                                                 round(float(ex.get("y", 0.5)), 3)],
                              "文字": str(act.get("字") or "")[:20]})
            elif t == "click":
                steps.append({"做": "點", "位置": [round(float(act.get("x", 0.5)), 3),
                                                 round(float(act.get("y", 0.5)), 3)],
                              "文字": str(act.get("g") or "")[:20]})
            elif t == "wait":
                steps.append({"做": "等待", "條件": "畫面變", "秒": round(float(act.get("d") or 1.0), 1)})
        ok, _bad = self.validate_steps(steps)
        if len(ok) < 2:
            made.add(key)
            return
        zone = (getattr(self, "cur_zone", "") or "常做").strip()[:6]
        name = f"{zone}{len(self.procs) + 1}"
        self.save_proc({"名": name, "步驟": ok, "來源": f"程式自動偵測：這串動作你做了 {cnt} 次",
                        "錄於": datetime.now().strftime("%m/%d %H:%M"),
                        "區": getattr(self, "cur_zone", ""), "成功": 0, "失敗": 0})
        made.add(key)
        detail = "；".join(json.dumps(x, ensure_ascii=False) for x in ok)
        print(f"   🔁 這串動作你做了 {cnt} 次 → 自動存成程序「{name}」（{len(ok)} 步）：{detail[:160]}")
        self.proc_hint = (getattr(self, "proc_hint", "") or "") + (
            f"🔁 **你這串動作做了 {cnt} 次了**：{detail[:120]}" + NL
            + f"程式已經把它存成一個 function「**{name}**」。"
            + f'以後要做同一件事，直接送 {{"t":"執行程序","名":"{name}"}}，'
            + "程式一輪做一步、不問模型，比你每次重想快很多。" + NL
            + "如果這串其實沒用（你只是在原地重複），那就**不要執行它**，改做別的——"
            + "重複十次沒有結果，本身就是該換做法的訊號。" + NL)
        self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] "
                            f"程式發現你重複做了 {cnt} 次同一串動作，已存成程序「{name}」")

    def record_procedure(self, a):
        """【錄成程序】把最近這段**實際做過而且有效**的動作，直接變成一個可以呼叫的 function。

        使用者 2026-09-17：進場位置與移動方向幾乎是固定的，重複的那串操作不該每次重想——
        做成功一次就把它錄下來，之後 {"t":"執行程序","名":"…"} 一次跑完。
        """
        name = str(a.get("名") or a.get("name") or "").strip()[:12]
        if not name:
            print('   ⚠ 錄成程序要給名字：{"t":"錄成程序","名":"進場","秒":120}')
            return
        secs = float(a.get("秒") or a.get("往前幾秒") or 180)
        lg = getattr(self, "_acts", None) or []
        now = time.time()
        steps, last_txt = [], None
        for t, act, ex in lg:
            if now - t > secs:
                continue
            k = str(act.get("t") or "")
            if k == "key":
                kk = str(act.get("k") or "").lower()
                if kk in ("w", "a", "s", "d", "space", "e", "esc", "1", "2", "3", "4", "5"):
                    steps.append({"做": "按鍵", "鍵": kk, "秒": round(float(act.get("d") or 0.5), 2)})
            elif k in ("點字", "點文字"):
                txt = str(act.get("字") or "")[:20]
                if txt and txt != last_txt:
                    steps.append({"做": "點", "位置": [round(float(ex.get("x", 0.5)), 3),
                                                     round(float(ex.get("y", 0.5)), 3)], "文字": txt})
                    last_txt = txt
            elif k == "click":
                steps.append({"做": "點", "位置": [round(float(act.get("x", 0.5)), 3),
                                                 round(float(act.get("y", 0.5)), 3)],
                              "文字": str(act.get("g") or "")[:20]})
            elif k == "wait":
                steps.append({"做": "等待", "條件": "畫面變", "秒": round(float(act.get("d") or 1.0), 1)})
        # 連續重複的同一步合併，最多 20 步
        clean = []
        for st in steps:
            if clean and clean[-1] == st and st.get("做") == "按鍵":
                continue
            clean.append(st)
        clean = clean[-20:]
        if len(clean) < 2:
            print(f"   ⚠ 最近 {secs:.0f} 秒裡沒有足夠可重播的動作（只有 {len(clean)} 步），沒錄成")
            return
        ok, bad = self.validate_steps(clean)
        if not ok:
            print(f"   ⚠ 錄到的步驟都被退回：{bad[:2]}")
            return
        self.save_proc({"名": name, "步驟": ok, "來源": "從實際做過的動作錄下來",
                        "錄於": datetime.now().strftime("%m/%d %H:%M"),
                        "區": getattr(self, "cur_zone", ""), "成功": 0, "失敗": 0})
        print(f"   🎬 錄成程序「{name}」共 {len(ok)} 步（取最近 {secs:.0f} 秒實際做過的動作）：")
        for i, st in enumerate(ok, 1):
            print(f"      {i}. {json.dumps(st, ensure_ascii=False)}")
        self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 你把剛才那串動作錄成程序「{name}」"
                            f"（{len(ok)} 步），以後用 執行程序 一次跑完")
        self.note_progress("錄成程序")

    def click_text(self, a):
        """【點字】使用者 2026-09-17：畫面上的字都有框，要點哪個字就用它的框中心去點，
        不要自己猜座標——那是他一直點不到「開始遊戲」的主因之一。"""
        want = str(a.get("字") or a.get("text") or a.get("g") or "").strip()
        if not want:
            print('   ⚠ 點字要寫「字」：{"t":"點字","字":"開始遊戲"}')
            return
        lines, stamp = self.text_lines()
        if not stamp or not lines:
            print("   ⚠ 這一刻 OCR 沒讀到任何字，點不了")
            return
        # 2026-09-18 使用者：OCR 的字典以簡體為主，繁體介面會被讀成形近的別字
        #（實測「開始遊戲」→「開始游戴」、「重新連線」→「重新连综」、「閒置」→「間置」）。
        # 直接比字串會找不到按鈕，他就只好退回猜座標。這裡改成三關：
        # 完全包含 → 繁簡／標點正規化後包含 → 形近字模糊比對（見 zh_text.py）。
        texts = [str(l["t"]) for l in lines]
        found = zh_text.find(want, texts)
        if not found:
            print(f"   ⚠ 畫面上找不到「{want}」（現在讀到：" 
                  + "、".join(str(l["t"])[:10] for l in lines[:6]) + "）")
            return
        best = found[0][1]
        hit = [lines[i] for i, r, _ in found if r >= best - 0.05]
        how = found[0][2]
        if best < 1.0:
            print(f"   🔤 OCR 讀到的是「{texts[found[0][0]]}」，跟你要的「{want}」不完全一樣（{how}），當成同一個")
        t = max(hit, key=lambda l: (l.get("w", 0) or 0) * (l.get("h", 0) or 0))
        x, y = float(t["x"]), float(t["y"])
        ok_r, why_r = self.reachable(x, y)
        if not ok_r:
            print(f"   🚫 「{t['t']}」在 ({x:.2f},{y:.2f})，但{why_r}")
            return
        sx, sy = self.to_screen(x, y)
        print(f"   🔤 點字「{t['t']}」→ 框中心 ({x:.2f},{y:.2f})、框 "
              f"{t.get('w', 0):.2f}×{t.get('h', 0):.2f} → 螢幕 ({sx},{sy})")
        sx, sy = move_safe(sx, sy)          # 2026-09-18：永遠不讓游標落在螢幕角落
        try:
            _b4 = self.grab_still(320)
        except Exception:  # noqa: BLE001
            _b4 = None
        time.sleep(0.12)
        pydirectinput.moveRel(1, 0, relative=True)
        time.sleep(0.05)
        pydirectinput.mouseDown(button="left")
        time.sleep(0.08)
        pydirectinput.mouseUp(button="left")
        time.sleep(0.2)
        try:      # 2026-09-18：點字也進按鈕簿——這是他最常用的點法，位置又最準
            self._last_click_xy = [x, y]
            self.watch_click(t["t"], x, y)       # 2026-09-20：按完要看字
            if _b4 is not None:
                _d = frame_diff(_b4, self.grab_still(320))
                self.note_button(t["t"], x, y, _d >= 3.0, diff=_d)
        except Exception as e:  # noqa: BLE001
            print(f"   點字記帳失敗：{e}")
        try:
            self.trace_add(f"點字「{t['t']}」({x:.2f},{y:.2f})")
            self.act_log(a, {"x": x, "y": y})
        except Exception:  # noqa: BLE001
            pass
        self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 你用點字按了「{t['t']}」"
                            f"（OCR 量到的框中心 {x:.2f},{y:.2f}）")
        self.note_progress("點字")

    def record_self(self, a):
        """【記自己】看得到自己＝第三人稱。認得出自己，才判斷得出人稱、也才看得出自己有沒有在動。"""
        look = str(a.get("樣子") or a.get("內容") or a.get("desc") or "").strip()[:120]
        if not look:
            print('   ⚠ 記自己要寫「樣子」：{"t":"記自己","樣子":"你在畫面上看到的自己長什麼樣"}')
            return
        d = {"樣子": look, "記於": datetime.now().strftime("%m/%d %H:%M"),
             "區": getattr(self, "cur_zone", "")}
        try:
            self.grab_still(900).save(os.path.join(self.dir, "我的樣子.jpg"), "JPEG", quality=80)
            d["畫面"] = "我的樣子.jpg"
        except Exception:  # noqa: BLE001
            pass
        self.selfie = d
        try:
            with open(os.path.join(self.dir, "我的樣子.json"), "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=1)
        except OSError:
            pass
        print(f"   🧍 記住自己的樣子：{look[:50]}")
        self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 你記住了自己的樣子：{look[:40]}")
        self.note_progress("記自己")

    def load_selfie(self):
        self.selfie = {}
        try:
            with open(os.path.join(self.dir, "我的樣子.json"), encoding="utf-8") as f:
                self.selfie = json.load(f) or {}
        except (OSError, json.JSONDecodeError):
            pass
        if self.selfie.get("樣子"):
            print(f"[自己] 他記過自己的樣子：{self.selfie['樣子'][:40]}")

    def selfie_text(self):
        d = getattr(self, "selfie", None) or {}
        if not d.get("樣子"):
            return ('（你還沒記過自己長什麼樣。**看得到自己的身體或背影＝第三人稱**，'
                    '那時候就把自己記下來：{"t":"記自己","樣子":"顏色、頭、手上拿什麼、身高比例"}。'
                    "記過之後，你才分得出畫面裡哪一個是你、也才看得出自己有沒有在動。）")
        return (f"你記過的自己：**{d['樣子']}**（{d.get('記於', '')}"
                + (f"，在「{d['區']}」" if d.get("區") else "") + "）" + NL
                + "→ 畫面上**看得到這個樣子＝你現在是第三人稱**（滑鼠自由、點得到 UI、轉視角要按右鍵）；"
                + "**看不到自己（頂多看到手）＝第一人稱**（滑鼠變成中央的準心；有些遊戲那個準心就是滑鼠，"
                  "把它對到按鈕上按左鍵就點得到，試過沒反應才算真的鎖住）。")

    def screen_text(self):
        """他的畫面現在多大、跟他量刻度當時差多少——**只給數字，不替他換算新刻度**。"""
        b = self.win.client_bbox() or {}
        w, h = b.get("width"), b.get("height")
        if not w or not h:
            return "（抓不到遊戲視窗大小）"
        rows = [f"遊戲畫面現在是 **{w} × {h} 像素**（滑鼠的 dx／dy 是**像素**，不是角度）"]
        cal = {}
        try:
            with open(os.path.join(self.dir, "視角校準.json"), encoding="utf-8") as f:
                cal = json.load(f) or {}
        except (OSError, json.JSONDecodeError):
            pass
        old = cal.get("視窗") or {}
        ow, oh = old.get("寬"), old.get("高")
        if ow and oh:
            rx, ry = w / float(ow), h / float(oh)
            same = abs(rx - 1) < 0.02 and abs(ry - 1) < 0.02
            rows.append(f"你上次量刻度時畫面是 {ow} × {oh}（{old.get('時間', '?')}）"
                        + ("——尺寸沒變，刻度還能用。" if same else
                           f"——**現在是當時的 {rx:.2f} 倍寬、{ry:.2f} 倍高，刻度已經不準了**。"))
            if not same:
                rows.append("同樣的 dx 在不同大小的畫面上轉出來的角度**不一樣**。"
                            '要嘛自己重新量（{"t":"量刻度"}），'
                            "要嘛自己用比例推一組新的，再送幾次驗證對不對——"
                            "驗證方法：送一個 dx，看水平儀的度數實際變了多少。")
        else:
            rows.append("（還沒有『量刻度當時的畫面大小』這筆紀錄——下次量刻度就會記下來。）")
        v = (cal.get("垂直") or {}).get("每度dy")
        x = (cal.get("水平") or {}).get("每度dx")
        if v or x:
            rows.append(f"你現在用的刻度：每度 dx {x or '?'}、每度 dy {v or '?'}"
                        f"（{(cal.get('水平') or {}).get('時間', '?')} 量的）")
        return NL.join(rows)

    def offscreen_text(self):
        """畫面有哪一塊滑鼠碰不到（視窗比螢幕大／被移出螢幕時）。照實講，不替他解決。"""
        o = self.offscreen()
        bad = {k: v for k, v in o.items() if v > 0.005}
        if not bad:
            return ""
        rows = ["🚫 **遊戲視窗比螢幕大（或有一部分在螢幕外），下面這幾塊你看得到、但滑鼠碰不到**："]
        for k, v in bad.items():
            if k == "上":
                rows.append(f"　- 畫面**最上面 {v*100:.0f}%**（y < {v:.2f}）：點不到")
            elif k == "下":
                rows.append(f"　- 畫面**最下面 {v*100:.0f}%**（y > {1-v:.2f}）：點不到")
            elif k == "左":
                rows.append(f"　- 畫面**最左邊 {v*100:.0f}%**（x < {v:.2f}）：點不到")
            else:
                rows.append(f"　- 畫面**最右邊 {v*100:.0f}%**（x > {1-v:.2f}）：點不到")
        n = int(getattr(self, "offscreen_hit", 0) or 0)
        if n:
            rows.append(f"　（你已經有 {n} 次點在碰不到的地方，程式直接擋掉了）")
        rows.append("**視窗不會被移回來**——這是你現在的環境，要自己想別的辦法："
                    "鍵盤（Enter／空白／數字鍵／Esc）、遊戲裡的其他入口、走過去用 E 互動、"
                    "或先讓那個東西出現在畫面中間再操作。")
        return NL.join(rows)

    def spaces_text(self):
        hint = getattr(self, "space_hint", "") or ""
        sp = getattr(self, "spaces", None) or {}
        cur = getattr(self, "space", self.SPACE0)
        rows = [f"你現在在「**{cur}**」（座標 x{self.pos[0]:+.1f} y{self.pos[1]:+.1f}、"
                f"朝向 {getattr(self, 'yaw', 0.0) % 360:.0f} 度，都是**這個空間自己的**）"]
        for k, v in sp.items():
            if k == cur:
                continue
            rows.append(f"　- {k}" + (f"（怎麼過去：{v.get('怎麼過去')}）" if v.get("怎麼過去") else "")
                        + (f"　最後去是 {v.get('最後進入')}" if v.get("最後進入") else ""))
        if len(sp) <= 1:
            rows.append("　（目前只有一個空間。)")
        if hint:
            rows.insert(0, hint.rstrip())
        rows.append('覺得剛才被傳送、搭了電梯、換了樓層、或畫面整個換成另一個場景——'
                    '那就是另一個空間：送 {"t":"切空間","name":"你自己取的名字","為什麼":"怎麼過來的"}。'
                    "切了之後座標歸零、地圖分開；回到舊的地方就再切回那個名字，舊地圖會整套回來。")
        return NL.join(rows)

    # ---------------- 他自己維護這個遊戲的資料夾（使用者 2026-09-16）
    # 學到就要記；記在哪、怎麼分類、哪一條不對要改掉，都是他自己的事。
    # 跨遊戲都成立的那幾條，才准往上搬到「基本知識」（核心），而且要說得出在哪幾隻遊戲遇過。
    KNOW_DIR = "知識"
    UPGRADE_FILE = os.path.join(HERE, "核心", "基礎操作", "95_他自己升級上來的.md")

    def know_path(self, name):
        bad = set(chr(92) + '/:*?"<>|')
        nm = "".join(ch for ch in str(name or "").strip() if ch not in bad)[:20] or "還不確定的"
        d = os.path.join(self.dir, self.KNOW_DIR)
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, nm + ".md"), nm

    def know_text(self):
        d = os.path.join(self.dir, self.KNOW_DIR)
        rows = []
        try:
            for fn in sorted(os.listdir(d)):
                if not fn.endswith(".md"):
                    continue
                body = open(os.path.join(d, fn), encoding="utf-8").read()
                n = len([l for l in body.split(NL) if l.startswith("- ")])
                rows.append(f"　- {fn[:-3]}（{n} 條）")
        except OSError:
            pass
        if not rows:
            return ('（你這個遊戲的資料夾還是空的。學到什麼就自己寫進去：'
                    '{"t":"記知識","檔":"分類名","內容":"一句話"}）')
        return (NL.join(rows) + NL
                + '寫：{"t":"記知識","檔":"基礎操作","內容":"..."}　'
                  '改：加上 "取代":"舊的那一句"　刪：{"t":"刪知識","檔":"...","內容":"那一句"}' + NL
                + "這是**你自己的**資料夾，分類、內容、對錯都你自己負責；發現寫錯就自己改掉。")

    def edit_knowledge(self, a):
        t = a.get("t")
        body = str(a.get("內容") or a.get("content") or "").strip()[:200]
        if t == "升級知識":
            games = a.get("遊戲") or a.get("games") or []
            if isinstance(games, str):
                games = [x.strip() for x in games.replace("、", ",").split(",") if x.strip()]
            have = [g for g in games if os.path.isdir(os.path.join(HERE, "專案", g))]
            if not body:
                print("   ⚠ 升級知識要寫 內容")
                return
            if len(have) < 2:
                print(f"   ⛔ 升級被擋下來：你只舉了 {have or '（沒有）'}。"
                      f"**要在兩隻以上的遊戲都遇過**才算通用，不然那是這隻遊戲的事，"
                      f"寫進自己的資料夾就好（核心不能被單一遊戲污染）。")
                self.lesson_hint = (getattr(self, "lesson_hint", "") or "") + (
                    "⛔ 你想把一條知識升級到基本知識，但沒有舉出兩隻以上都遇過的遊戲——"
                    "先確認它在別的遊戲也成立，再升級。" + NL)
                return
            why = str(a.get("為什麼") or "")[:80]
            os.makedirs(os.path.dirname(self.UPGRADE_FILE), exist_ok=True)
            if not os.path.exists(self.UPGRADE_FILE):
                with open(self.UPGRADE_FILE, "w", encoding="utf-8") as f:
                    f.write("---" + NL + "章節: §9 他自己發現、在多隻遊戲都成立的事" + NL
                            + "適用: 全部" + NL + "---" + NL + NL
                            + "（這一份是**他自己**升級上來的：在兩隻以上的遊戲都遇過，才准搬到這裡。）" + NL + NL)
            with open(self.UPGRADE_FILE, "a", encoding="utf-8") as f:
                f.write(f"- {body}　（{datetime.now().strftime('%m/%d')}　在「"
                        + "」「".join(have) + "」都遇過" + (f"：{why}" if why else "") + "）" + NL)
            print(f"   ⬆ 升級到基本知識（{'、'.join(have)} 都遇過）：{body[:40]}")
            self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] "
                                f"你把一條知識升級到基本知識（{'、'.join(have)} 都成立）：{body[:30]}")
            return
        path, nm = self.know_path(a.get("檔") or a.get("file"))
        old = ""
        if os.path.exists(path):
            old = open(path, encoding="utf-8").read()
        else:
            old = f"# {nm}（我自己整理）" + NL + NL
        rep_line = str(a.get("取代") or a.get("舊") or "").strip()
        if t == "刪知識":
            keep = [l for l in old.split(NL) if body and body not in l]
            with open(path, "w", encoding="utf-8") as f:
                f.write(NL.join(keep))
            print(f"   🗑 {nm}：刪掉「{body[:30]}」")
            return
        if not body:
            print("   ⚠ 記知識要寫 內容")
            return
        if rep_line:
            lines = old.split(NL)
            hit = [i for i, l in enumerate(lines) if rep_line in l]
            if hit:
                lines[hit[0]] = "- " + body
                with open(path, "w", encoding="utf-8") as f:
                    f.write(NL.join(lines))
                print(f"   ✏ {nm}：把「{rep_line[:24]}」改成「{body[:30]}」")
                return
            print(f"   ⚠ {nm} 裡找不到「{rep_line[:24]}」，改成新增一條")
        if body in old:
            print(f"   （{nm} 已經有這一條了，沒重複寫）")
            return
        with open(path, "a", encoding="utf-8") as f:
            f.write("- " + body + NL)
        print(f"   📗 記進 {nm}：{body[:40]}")
        self.note_progress("記知識")

    def load_map(self):
        self.map = {}
        self.trail_pts = []
        self.wall_pts = []
        p = self.space_file("地圖.json")
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            self.map = {k: (float(v[0]), float(v[1])) for k, v in d.get("地點", {}).items()}
            self.trail_pts = [tuple(x) for x in d.get("走過的路", [])][-4000:]
            self.wall_pts = [tuple(x) for x in d.get("撞牆點", [])][-1500:]
            self.map_scene = {k: v for k, v in (d.get("場景") or {}).items()}
            self.map_sig = {k: v for k, v in (d.get("視覺") or {}).items()}
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
        self.map_scene = getattr(self, "map_scene", {}) or {}
        self.map_sig = getattr(self, "map_sig", {}) or {}
        self.pos = [0.0, 0.0]
        self.yaw = 0.0
        print(f"[地圖] 他自己記過 {len(self.map)} 個地方：{'、'.join(self.map) or '（還沒有）'}")

    def save_map(self):
        self.gmap_save()
        try:
            with open(self.space_file("地圖.json"), "w", encoding="utf-8") as f:
                json.dump({"地點": {k: [round(v[0], 2), round(v[1], 2)] for k, v in self.map.items()},
                           "走過的路": [[round(x, 1), round(y, 1)] for x, y in self.trail_pts[-4000:]],
                           "撞牆點": [[round(x, 1), round(y, 1)] for x, y in self.wall_pts[-1500:]],
                           "場景": {k: v for k, v in (getattr(self, "map_scene", {}) or {}).items() if k in self.map},
                           "視覺": {k: v for k, v in (getattr(self, "map_sig", {}) or {}).items() if k in self.map},
                           "更新": datetime.now().strftime("%m/%d %H:%M:%S")},
                          f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def mark_trail(self):
        """把走過的位置記成軌跡——走廊與房間的形狀就是這樣長出來的。"""
        import math
        pts = getattr(self, "trail_pts", [])
        if not pts or math.hypot(self.pos[0] - pts[-1][0], self.pos[1] - pts[-1][1]) > 0.4:
            pts.append((round(self.pos[0], 1), round(self.pos[1], 1)))
            self.trail_pts = pts[-4000:]
        try:
            if self.gmap_on() and getattr(self, "gmap", None) is not None:
                self.gmap.mark_walked(self.pos[0], self.pos[1])
        except Exception:  # noqa: BLE001
            pass

    def mark_wall(self):
        """被擋住的時候，把「前方一步」的位置記成障礙——累積起來就是牆的形狀。"""
        import math
        th = math.radians(getattr(self, "yaw", 0.0))
        wx = self.pos[0] + math.sin(th) * 0.8
        wy = self.pos[1] + math.cos(th) * 0.8
        w = getattr(self, "wall_pts", [])
        if not any(math.hypot(wx - a, wy - b) < 0.5 for a, b in w[-60:]):
            w.append((round(wx, 1), round(wy, 1)))
            self.wall_pts = w[-1500:]
        try:
            if self.gmap_on() and getattr(self, "gmap", None) is not None:
                c = self.gmap.mark_bump(self.pos[0], self.pos[1], getattr(self, "yaw", 0.0))
                if c:
                    self.stat["陣列地圖撞牆"] = self.stat.get("陣列地圖撞牆", 0) + 1
                    self.gmap_log("撞牆", 格=list(c))
        except Exception:  # noqa: BLE001
            pass

    def odometry(self, actions):
        """用他自己做的動作推算位置與朝向（走幾秒＝幾步，轉多少像素＝轉幾度）。"""
        import math
        for a in actions or []:
            t = a.get("t")
            if t == "look":
                # 朝向已經由水平儀在 raw_look 裡累加過了，這裡不重複加（以前會多算一次）
                pass
            elif t == "key":
                k = str(a.get("k", "")).lower()
                try:
                    d = float(a.get("d", 0) or 0)
                except (TypeError, ValueError):
                    d = 0.0
                if k not in ("w", "s", "a", "d") or d <= 0:
                    continue
                if isinstance(a.get("實走"), (int, float)):
                    d = float(a["實走"])
                th = math.radians(self.yaw)
                fx, fy = math.sin(th), math.cos(th)
                rx, ry = math.cos(th), -math.sin(th)
                if k == "w":
                    self.pos[0] += fx * d; self.pos[1] += fy * d
                elif k == "s":
                    self.pos[0] -= fx * d; self.pos[1] -= fy * d
                elif k == "d":
                    self.pos[0] += rx * d; self.pos[1] += ry * d
                elif k == "a":
                    self.pos[0] -= rx * d; self.pos[1] -= ry * d

    def cjk_font(self, size=13):
        """PIL 預設字型沒有中文，會變成一排方框——他讀不懂地圖標籤。"""
        from PIL import ImageFont
        key = f"_font{size}"
        if getattr(self, key, None) is None:
            f = None
            for path in (r"C:\Windows\Fonts\msjh.ttc", r"C:\Windows\Fonts\msjhl.ttc",
                         r"C:\Windows\Fonts\mingliu.ttc", r"C:\Windows\Fonts\simsun.ttc",
                         r"C:\Windows\Fonts\seguiemj.ttf"):
                try:
                    f = ImageFont.truetype(path, size)
                    break
                except OSError:
                    continue
            setattr(self, key, f or ImageFont.load_default())
        return getattr(self, key)

    ICONS = [(("櫃檯", "窗口", "counter"), (80, 170, 255), "▣", "櫃檯"),
             (("咖啡", "coffee"), (200, 140, 60), "☕", "咖啡"),
             (("門", "出口", "入口", "door"), (120, 220, 120), "▯", "門"),
             (("電腦", "螢幕", "computer"), (0, 230, 230), "▤", "電腦"),
             (("病人", "cat", "dog", "rabbit", "兔"), (255, 130, 190), "☻", "病人"),
             (("拍照", "相機", "camera"), (230, 230, 120), "◎", "相機"),
             (("房間", "走廊", "room"), (170, 170, 200), "◇", "房間"),
             (("儲藏", "倉", "box"), (200, 160, 110), "▨", "儲藏")]

    def icon_of(self, name):
        for keys, col, mark, kind in self.ICONS:
            if any(k in name for k in keys):
                return col, mark, kind
        return (0, 220, 220), "●", "地點"

    def map_image(self, size=560):
        """圖示版俯視地圖：你在哪、走過哪、撞到哪、每個地點用不同圖示，下一個目標畫線標出來。"""
        import math
        from PIL import Image, ImageDraw
        im = Image.new("RGB", (size, size), (16, 16, 20))
        d = ImageDraw.Draw(im)
        f = self.cjk_font(13)
        pts_all = list(getattr(self, "trail_pts", [])) + list(getattr(self, "wall_pts", []))                   + [tuple(v) for v in getattr(self, "map", {}).values()] + [tuple(self.pos)]
        xs = [p[0] for p in pts_all] or [0.0]
        ys = [p[1] for p in pts_all] or [0.0]
        minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
        span = max(maxx - minx, maxy - miny, 6.0)
        pad = size * 0.10
        sc = (size - 2 * pad) / span
        cx0, cy0 = (minx + maxx) / 2, (miny + maxy) / 2

        def to_px(x, y):
            return (size / 2 + (x - cx0) * sc, size / 2 - (y - cy0) * sc + 12)

        for g in range(-40, 41, 5):
            d.line([to_px(g, miny - 5), to_px(g, maxy + 5)], fill=(30, 30, 38))
            d.line([to_px(minx - 5, g), to_px(maxx + 5, g)], fill=(30, 30, 38))
        trail = [to_px(x, y) for x, y in getattr(self, "trail_pts", [])]
        if len(trail) > 1:
            d.line(trail, fill=(70, 115, 95), width=6)
        for x, y in getattr(self, "wall_pts", []):
            px, py = to_px(x, y)
            d.rectangle((px - 3, py - 3, px + 3, py + 3), fill=(205, 70, 70))
        goal = getattr(self, "goal", "")
        px_me, py_me = to_px(self.pos[0], self.pos[1])
        for name, (x, y) in getattr(self, "map", {}).items():
            px, py = to_px(x, y)
            col, mark, kind = self.icon_of(name)
            if name == goal:
                d.ellipse((px - 13, py - 13, px + 13, py + 13), outline=(255, 80, 80), width=3)
                d.line([(px_me, py_me), (px, py)], fill=(255, 120, 120), width=2)
            d.ellipse((px - 8, py - 8, px + 8, py + 8), fill=col)
            d.text((px - 5, py - 8), mark, fill=(20, 20, 24), font=f)
            d.text((px + 11, py - 8), name, fill=col, font=f)
        th = math.radians(getattr(self, "yaw", 0.0))
        fx, fy = math.sin(th), math.cos(th)
        d.polygon([(px_me + fx * 16, py_me - fy * 16),
                   (px_me - fy * 9 - fx * 6, py_me - fx * 9 + fy * 6),
                   (px_me + fy * 9 - fx * 6, py_me + fx * 9 + fy * 6)], fill=(255, 170, 0))
        d.ellipse((px_me - 4, py_me - 4, px_me + 4, py_me + 4), fill=(255, 230, 150))
        d.text((6, 4), "橘箭頭=你（尖端是你面對的方向）　綠線=走過的路　紅點=撞到的牆", fill=(205, 205, 210), font=f)
        d.text((6, 20), "▣櫃檯 ☕咖啡 ▯門 ▤電腦 ☻病人 ◎相機 ◇房間 ▨儲藏　紅圈=下一個目標", fill=(170, 170, 185), font=f)
        route = self.route_text()
        d.text((6, size - 24), route[:46], fill=(255, 200, 120), font=f)
        try:
            im.save(os.path.join(self.dir, "地圖.png"))
        except OSError:
            pass
        return im

    def route_text(self):
        """下一個目標在哪、怎麼走過去（角度＋秒數）。關閉時不給答案，只提醒他自己想。"""
        import math
        if not getattr(self, "show_map", False):
            g = getattr(self, "goal", "")
            return (f"你設的下一個目標是「{g}」——**方位要你自己判斷**："
                    f"回想你是怎麼從那裡走過來的、經過哪些東西，自己想清楚往哪轉、走多久。"
                    if g else "還沒設下一個目標（用 設目標 挑一個，方位要自己判斷）")
        g = getattr(self, "goal", "")
        if isinstance(g, dict):          # 2026-09-27：goal 後來改成 dict（任務目標），地圖路線只認地點名
            g = str(g.get("地點") or g.get("目標") or "")
        if not g or g not in getattr(self, "map", {}):
            return "還沒設下一個目標（用 設目標 從地圖挑一個）"
        gx, gy = self.map[g]
        vx, vy = gx - self.pos[0], gy - self.pos[1]
        dist = math.hypot(vx, vy)
        if dist < 1.0:
            return f"你已經站在「{g}」了"
        ang = (math.degrees(math.atan2(vx, vy)) - getattr(self, "yaw", 0.0) + 540) % 360 - 180
        ppp = getattr(self, "px_per_pct", 4.0) or 4.0
        dx = int(abs(ang) / 0.70 * ppp)
        blocked = ""
        for wx, wy in getattr(self, "wall_pts", [])[-400:]:
            t = ((wx - self.pos[0]) * vx + (wy - self.pos[1]) * vy) / max(0.01, dist * dist)
            if 0.1 < t < 0.9:
                px, py = self.pos[0] + vx * t, self.pos[1] + vy * t
                if math.hypot(wx - px, wy - py) < 0.9:
                    blocked = "（直線上有你撞過的牆，要繞）"
                    break
        return (f"去「{g}」：往{'右' if ang > 0 else '左'}轉 {abs(ang):.0f} 度"
                f"（look dx {'+' if ang > 0 else '-'}{dx}）再直走約 {dist:.1f} 秒{blocked}")

    def map_text(self):
        """關閉狀態只列他記過的地名，不給座標與方位——地圖的樣子要他自己想。"""
        if not getattr(self, "show_map", False):
            if not getattr(self, "map", None):
                return "（你還沒記過任何地方。走到明顯的地方就用 記位置 記起來，名字自己取。）"
            return ("、".join(self.map) + NL
                    + "（這些是你記過的地方。它們彼此在哪個方向、隔多遠、怎麼走過去，"
                    + "程式不會告訴你——你要自己回想走過的路，在腦中把地圖拼出來。）")
        return self.map_text_full()

    def map_text_full(self):
        """告訴他：他自己記過的地方，現在在他的哪個方向、大約幾步。"""
        import math
        if not getattr(self, "map", None):
            return ("（你的地圖還是空的。想記住一個地方就用 {\"t\":\"記位置\",\"name\":\"...\"}，"
                    "之後我就會告訴你它在你的哪個方向、大約幾步。）")
        rows = []
        for name, (x, y) in self.map.items():
            vx, vy = x - self.pos[0], y - self.pos[1]
            dist = math.hypot(vx, vy)
            ang = (math.degrees(math.atan2(vx, vy)) - self.yaw + 540) % 360 - 180
            side = "正前方" if abs(ang) < 20 else ("正後方" if abs(ang) > 160 else
                                                 (f"右邊 {abs(ang):.0f} 度" if ang > 0 else f"左邊 {abs(ang):.0f} 度"))
            rows.append(f"{name}：{side}，大約走 {dist:.1f} 秒")
        return "；".join(rows) + f"（你現在大約在出發點的 x{self.pos[0]:+.1f} y{self.pos[1]:+.1f}、朝向 {self.yaw:.0f} 度）"

    OPPOSITE = {"w": "s", "s": "w", "a": "d", "d": "a"}

    def block_oscillation(self, k):
        if not self.g("振盪"):
            return False
        """擋掉「前進↔後退」「左↔右」的原地振盪。

        他每輪只看到一張靜態畫面，沒有「我剛才往哪走、結果如何」的體感，
        所以看到牆就退、退開看到空間又前進，來回抵銷等於原地不動。
        同一組動作來回兩次就擋下來，強迫他換方向。
        """
        last = getattr(self, "_last_move", "")
        if last and self.OPPOSITE.get(k) == last:
            self._osc = getattr(self, "_osc", 0) + 1
        else:
            self._osc = 0
        self._last_move = k
        if self._osc >= 2:
            self._osc = 0
            print(f"   🔄 前後振盪：剛才按 {last}、現在又按 {k}，等於原地不動 → 擋掉，改成轉向走別的方向")
            self.osc_hint = ("⚠ 你剛才在原地前後（或左右）來回移動，等於沒有移動——這是今天最浪費時間的行為。"
                             "程式已經幫你轉了 90 度。**不要再往回走**：往新的方向直走一段，"
                             "看清楚新的畫面再決定。" + NL)
            self.raw_look(int(self.MAX_DX * 2), 0)
            self.press_key("w", 1.0)
            return True
        return False

    def note_blocked_dir(self):
        """把「走不動」的朝向記下來，之後面對同一個方向時提醒他。"""
        self.blocked_dirs = [(y, t) for y, t in getattr(self, "blocked_dirs", [])
                             if time.time() - t < 180][-8:]
        self.blocked_dirs.append((round(getattr(self, "yaw", 0.0), 0), time.time()))

    def blocked_hint(self):
        cur = getattr(self, "yaw", 0.0)
        for y, t in getattr(self, "blocked_dirs", []):
            if min(abs(cur - y), 360 - abs(cur - y)) < 25 and time.time() - t < 180:
                return (f"⚠ 你現在面對的方向（{cur:.0f} 度）在 {int(time.time() - t)} 秒前才試過，"
                        f"往這邊走不動。換一個方向，不要再往這裡頂。")
        return ""

    OBSTACLES = ("wall", "cabinet", "shelf", "table", "chair", "box", "bed",
                 "counter desk", "trash can", "plant", "computer monitor", "printer")

    OBSTACLE_KEEP = ["wall", "cabinet", "shelf", "table", "chair", "box"]

    def front_obstacle(self, exclude=""):
        """YOLO 把關：前方有沒有東西擋著（框又大又在正中央＝快撞上了）。

        紋理判斷只認得「一片平面的牆」，家具有紋理它看不出來——
        這裡改用 YOLO 的框：只要障礙物的框佔畫面高度過半、又落在畫面中央，就是擋在路上。
        """
        dets, stamp = self.eyes.latest()
        if not stamp or time.time() - stamp > 1.5:
            return None
        ex = (exclude or "").lower()
        best = None
        for lab, arr in (dets or {}).items():
            l = str(lab).lower()
            if ex and (ex in l or l in ex):
                continue                      # 要去的目標本身不算障礙
            if not any(o in l for o in self.OBSTACLES):
                continue
            for d in arr[:2]:
                if self.eyes.is_self(d):
                    continue
                if abs(d["cx"] - 0.5) > 0.28:
                    continue                  # 不在正前方就不擋路
                near = d["h"] >= 0.45 or (d["cy"] + d["h"] / 2) > 0.90
                if near and (best is None or d["h"] > best[1]["h"]):
                    best = (str(lab), d)
        return best

    def obstacle_text(self):
        o = self.front_obstacle(getattr(self, "_track", ""))
        if not o:
            return "前方沒有明顯障礙（YOLO 沒框到擋路的東西）"
        lab, d = o
        side = "正前方" if abs(d["cx"] - 0.5) < 0.1 else ("右前" if d["cx"] > 0.5 else "左前")
        return (f"⚠ YOLO 看到「{lab}」擋在{side}（高度 {d['h']:.2f}、位置 {d['cx']:.2f}）"
                f" → 直走會撞上，先橫移繞過或換方向")

    def score_move(self, k, walked, reason):
        """移動計分：走到說好的地方 +10，說了卻沒走到 −3，根本沒說要去哪 −3。

        使用者 09/11：不再罰他走弧線，改成罰「沒有目的地的移動」。
        """
        if k not in ("w", "a", "s", "d"):
            return
        if getattr(self, "_prog_walk", False):
            return                      # 2026-10-01：程式照地圖帶路走的，不是他的動作，不計分也不罰
        try:
            arrived = bool(reason) and "到眼前" in reason
            target = (str(getattr(self, "_track", "") or "").strip()
                      or str(getattr(self, "_named", "") or "").strip())
            if not target:
                self.punish("沒有目的地就亂走",
                            "你按了移動鍵，但**沒有說要去哪**。"
                            "每個移動動作都帶上 g（你要去的東西或地方）。")
                return
            # 使用者定的規則：+10 要「走到目標位置**而且**互動成功」才算，
            # 光是走一步不給分。這裡只記下他正在走向誰，等互動成功時再結算。
            self._goal_walk = {"目標": target, "時間": time.time(),
                               "到了": bool(arrived), "理由": reason or ""}
            if reason and not arrived:
                self.punish("走不到目的地",
                            f"你想走向「{target}」但沒走到：{reason}。"
                            f"換條路、先橫移繞過去，或換一個看得到的目標。")
            self.score_move_quality(k, walked, reason, target)
        except Exception:  # noqa: BLE001
            pass

    # 他提的操作加分，每一項的冷卻秒數（同一項不能連發，不然變成刷分）
    OP_COOLDOWN = 20.0

    def op_award(self, kind, why):
        """他自己提的操作加分：有證據才給，而且同一項要隔一段時間。

        他提的分數（15/12/10/8）比「基礎動作最多 10 分」的自訂上限高，
        使用者指示照他的表做，所以這裡不砍分數，改用冷卻擋刷分。
        """
        last = (getattr(self, "_op_at", None) or {}).get(kind, 0)
        if time.time() - last < self.OP_COOLDOWN:
            return False
        self._op_at = getattr(self, "_op_at", {})
        self._op_at[kind] = time.time()
        self.award(kind, why)
        return True

    def score_move_quality(self, k, walked, reason, target):
        """移動做得好不好——他自己提的前兩條操作加分。

        「一次到位的移動」：這一輪只送了一個 w、而且真的走滿了（他自己的說法是
        「一次按 w 走對距離比按五次 0.5 秒有效得多，代表我有在算距離」）。
        「有效的橫移避障」：前方有障礙的時候改用 a/d 繞，而不是硬撞或後退。
        """
        blocked = bool(reason) and ("障礙" in reason or "貼到" in reason or "沒變" in reason)
        if k == "w":
            n_w = sum(1 for a in (getattr(self, "_acts_now", None) or [])
                      if a.get("t") == "key" and str(a.get("k", "")).lower() == "w")
            far = walked >= 1.5 and (not reason or "到眼前" in reason)
            if n_w == 1 and far:
                self.op_award("一次到位的移動",
                              f"你一次就按滿 {walked:.1f} 秒走向「{target}」"
                              f"（不是拆成好幾次 0.5 秒試探）——這代表你有在算距離。")
        elif k in ("a", "d") and walked >= 0.4 and not blocked:
            if getattr(self, "_front_blocked_at", 0) and time.time() - self._front_blocked_at < 25:
                self._front_blocked_at = 0
                self.op_award("有效的橫移避障",
                              f"前面被擋住的時候你用 {k} 橫移 {walked:.1f} 秒繞過去，"
                              f"沒有硬撞也沒有退回去重來。")

    # 「就在眼前」的門檻：框高佔畫面這麼多，或框底壓到畫面下緣
    GOAL_NEAR_H = 0.45
    GOAL_NEAR_BOTTOM = 0.90

    def goal_reached(self, w):
        """互動成功了，但這次互動真的發生在「他說要去的那個目標」身上嗎？

        2026-09-12：原本只要「這 45 秒內走過路」＋「互動成功」就給 +10，
        兩者不必是同一件事——所以他填個 g（連「關閉好友選單」這種不是地點的字都行）、
        隨便按到一個東西就有 10 分。煞車關掉之後 `到了` 永遠是 False，也沒人檢查。
        現在要有下面三種證據之一才算數。
        """
        tgt = str((w or {}).get("目標") or "").strip()
        if not tgt:
            return False, "你當初沒有說要去哪"
        if w.get("到了"):
            return True, "YOLO 在你走路時判定已經到目標眼前"
        ht = str(getattr(self, "_last_hold_target", "") or "").strip()
        if ht and (ht in tgt or tgt in ht):
            return True, f"你按的就是目標本身（「{ht}」）"
        try:
            b = self.eyes.best(tgt, max_age=2.0)
            if b and (b["h"] >= self.GOAL_NEAR_H or b["cy"] + b["h"] / 2 > self.GOAL_NEAR_BOTTOM):
                return True, f"眼睛現在看到「{tgt}」就在你眼前（高度 {b['h']:.2f}）"
        except Exception:  # noqa: BLE001
            pass
        return False, (f"你說要去「{tgt}」，但沒有任何證據顯示你人已經到它前面"
                       + (f"（你按的是「{ht}」，不是它）" if ht and ht != tgt else ""))

    def walk_guarded(self, k, d):
        # 走之前先記下目標現在的框高，走完拿來學「一秒能縮短多少視覺距離」
        try:
            if k == "w" and getattr(self, "_track", ""):
                b4 = self.eyes.best(self._track, max_age=1.5)
                self._walk_h0 = b4["h"] if b4 else None
        except Exception:  # noqa: BLE001
            self._walk_h0 = None
        """按住方向鍵前進，但讓 YOLO 每秒看 5 次當「煞車」：快撞上東西或快到目標就提早鬆手。

        這就是本機偵測器最該做的事——它比模型快得多（一輪 5 秒，它看 25 次），
        所以「靠近牆了要停」「快走過頭了要停」交給它，模型只負責決定要往哪走。
        回傳 (實際走了幾秒, 停下來的原因)。
        """
        import numpy as np
        d = max(0.05, min(3.0, float(d)))
        if self.dry or not self.win.activate():
            return 0.0, ""
        if not self.g("煞車"):
            self.press_key(k, d)
            self.score_move(k, d, "")
            return d, ""
        if k == "w" and self.g("煞車"):
            ob0 = self.front_obstacle(getattr(self, "_track", ""))
            if ob0:
                self._front_blocked_at = time.time()
                print(f"   🛑 還沒起步就被 YOLO 攔下：「{ob0[0]}」擋在正前方（高度 {ob0[1]['h']:.2f}）")
                self.brake_hint = (f"⚠ YOLO 看到「{ob0[0]}」就擋在你正前方（高度 {ob0[1]['h']:.2f}），"
                                   f"**這一步沒有讓你走**。直走一定撞上——先用 a/d 橫移繞過去，"
                                   f"或轉到別的方向再走。" + NL)
                self.mark_wall()
                return 0.0, "前方有障礙，沒有起步"
        t0 = time.time()
        reason = ""
        base = None
        still_ticks = 0
        try:
            pydirectinput.keyDown(k)
            while time.time() - t0 < d:
                time.sleep(0.15)
                try:
                    im = self.grab_still(320)
                except Exception:  # noqa: BLE001
                    continue
                g = np.asarray(im.convert("L"), dtype=np.float32)
                h, w = g.shape
                band = g[int(h * 0.30):int(h * 0.85), :]
                mid = float(band[:, int(w * 0.35): int(w * 0.65)].std())
                # 1) 前方變成一片平面＝牆或櫃子貼到臉上了
                if mid < 14 and k == "w":
                    reason = f"前方已經貼到東西了（正前方紋理只剩 {mid:.0f}）"
                    break
                # 1b) YOLO 看到障礙物擋在正前方（家具有紋理，紋理法看不出來）
                if k == "w":
                    ob = self.front_obstacle(getattr(self, "_track", ""))
                    if ob:
                        reason = f"YOLO 看到「{ob[0]}」擋在正前方（高度 {ob[1]['h']:.2f}）"
                        break
                # 2) 目標已經到眼前（框變得很大或壓到畫面底部）＝再走就過頭了
                dt = self.eyes.best(getattr(self, "_track", "") or "", max_age=1.0) if getattr(self, "_track", "") else None
                if dt and (dt["h"] >= 0.55 or dt["cy"] + dt["h"] / 2 > 0.93):
                    reason = f"「{self._track}」已經到眼前（高度 {dt['h']:.2f}），再走就過頭了"
                    break
                # 3) 走不動：畫面連續幾次幾乎沒變
                if base is not None and frame_diff(base, im) < 2.0:
                    still_ticks += 1
                    if still_ticks >= 3:
                        reason = "連續走了快半秒畫面都沒變，前面被擋住了"
                        break
                else:
                    still_ticks = 0
                base = im
        finally:
            try:
                pydirectinput.keyUp(k)
            except Exception:  # noqa: BLE001
                pass
        walked = round(time.time() - t0, 2)
        self.stat["移動次數"] = self.stat.get("移動次數", 0) + 1
        try:
            self.trace_add(f"按 {k} 走了 {walked:.1f}s"
                           + (f"（被叫停：{reason[:34]}）" if reason else "（走完）"))
        except Exception:  # noqa: BLE001
            pass
        try:
            if k == "w" and getattr(self, "_walk_h0", None) and self._track:
                af = self.eyes.best(self._track, max_age=1.5)
                if af:
                    self.learn_walk(self._track, self._walk_h0, af["h"], walked)
            self._walk_h0 = None
        except Exception:  # noqa: BLE001
            pass
        if not reason or ("到眼前" in reason):
            self.stat["移動有效"] = self.stat.get("移動有效", 0) + 1
            self.note_progress("走成了一步")
        self.score_move(k, walked, reason)
        if reason and "沒變" in reason and self.flow in ("報到", "治療"):
            self.desk_lock = getattr(self, "desk_lock", 0) + 1
            if self.desk_lock >= 2:
                self.brake_hint = ("⚠ 你在櫃檯作業中，按 w/a/d 是不會動的（角色被固定住）。"
                                   "改用 look 轉視線去看窗口／文件／照片／CCTV，用 click 按畫面上的按鈕，"
                                   "看不清就用 放大。" + NL)
                print("   🧷 判定：正在櫃檯作業，移動無效 → 已告訴他改用轉視角與點擊")
        elif reason:
            self.desk_lock = 0
        if reason and ("障礙" in reason or "貼到" in reason or "沒變" in reason):
            self._front_blocked_at = time.time()
        if reason:
            print(f"   🛑 走了 {walked}s 就被眼睛叫停：{reason}")
            if k == "w" and ("沒變" in reason or "貼到" in reason):
                self.note_blocked_dir()
                self.mark_wall()
            self.brake_hint = (f"⚠ 上一輪你想走 {d:.1f} 秒，YOLO 眼睛在第 {walked:.1f} 秒把你叫停了：{reason}。"
                               f"（它每秒看 5 次，所以能在你撞上去或走過頭之前停下來。）" + NL)
        else:
            self.brake_hint = ""
        return walked, reason

    # ---------- 重複自覺：把他自己的行為統計攤給他看（不擋，只照鏡子）
    def repeat_mirror(self):
        w = getattr(self, "_sig_win", [])
        if len(w) < 4:
            return "（還沒累積足夠的動作紀錄）"
        from collections import Counter
        c = Counter(w)
        top, n = c.most_common(1)[0]
        kinds = len(c)
        frozen = int(self.frozen_secs())
        try:
            import json as _j
            names = "、".join(f"{a.get('t')}{a.get('k', '')}" for a in _j.loads(top)) or "沒動作"
        except Exception:  # noqa: BLE001
            names = "某個動作"
        rows = [f"最近 {len(w)} 輪你只用了 **{kinds} 種**不同的做法；"
                f"其中「{names}」就佔了 **{n} 輪**。"]          # 2026-10-01：「N 秒沒成功」那句拿掉（卡 N 分鐘取消）
        if n >= 4 and frozen > 60:
            rows.append("**你自己看**：同一招做了這麼多次都沒用，它就是沒用。"
                        "這一輪請換一件完全不同的事——換目標、換方向、先站定看清楚、"
                        "或用【可選目標】的編號選一個你還沒試過的東西。")
        elif n >= 3:
            rows.append("開始重複了。再做一次之前先問自己：上一次做這個發生了什麼？沒發生什麼就換做法。")
        else:
            rows.append("做法有在變化，很好，保持這樣。")
        return NL.join(rows)

    # ---------- 走路時間：用框的大小估距離，換算成「要按 w 幾秒」
    def load_walk_cal(self):
        self.walk_k = 1.6          # 預設值：((1/h_現在)-(1/h_到位)) × walk_k ≈ 要走的秒數
        self.walk_samples = []
        try:
            with open(os.path.join(self.dir, "走路校準.json"), encoding="utf-8") as fh:
                d = json.load(fh)
            self.walk_k = float(d.get("k", self.walk_k))
            self.walk_samples = [float(x) for x in d.get("樣本", [])][-40:]
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
        print(f"[走路] 視覺距離換算：k={self.walk_k:.2f}（樣本 {len(self.walk_samples)} 筆）")

    def save_walk_cal(self):
        try:
            with open(os.path.join(self.dir, "走路校準.json"), "w", encoding="utf-8") as fh:
                json.dump({"k": round(self.walk_k, 3), "樣本": [round(x, 3) for x in self.walk_samples[-40:]],
                           "說明": "秒數 ≈ (1/現在框高 - 1/到位框高0.55) × k",
                           "更新": datetime.now().strftime("%m/%d %H:%M:%S")},
                          fh, ensure_ascii=False, indent=2)
        except OSError:
            pass

    ARRIVE_H = 0.55      # 框高到這個值就等於站在它面前了

    def learn_walk(self, label, h0, h1, secs):
        """走了 secs 秒，框高從 h0 變 h1 → 學到「一秒能縮短多少視覺距離」。"""
        if not label or secs <= 0.15 or h0 <= 0.02 or h1 <= 0.02 or h1 <= h0:
            return
        dinv = (1.0 / h0) - (1.0 / h1)
        if dinv <= 0.05:
            return
        k = secs / dinv
        if not (0.4 <= k <= 6.0):
            return
        self.walk_samples = (getattr(self, "walk_samples", []) + [k])[-40:]
        import statistics as st
        # 樣本太少就先用預設值（1.6）；只有累積夠了才信自己量的中位數
        self.walk_k = st.median(self.walk_samples) if len(self.walk_samples) >= 3 else 1.6
        self.walk_k = max(0.4, min(6.0, self.walk_k))
        self.save_walk_cal()
        print(f"   📏 學到走路換算：走 {secs:.2f}s 讓「{label}」從 {h0:.2f} 變 {h1:.2f} → k={self.walk_k:.2f}")

    def walk_secs_for(self, h):
        """框高 h 的東西，還要按 w 幾秒才會走到它面前。"""
        if h is None or h <= 0.02:
            return None
        if h >= self.ARRIVE_H:
            return 0.0
        secs = ((1.0 / h) - (1.0 / self.ARRIVE_H)) * getattr(self, "walk_k", 1.6)
        return max(0.2, min(8.0, secs))

    def distance_text(self):
        """把眼睛框到的東西換算成「要按 w 幾秒」——他自己算不出這件事。"""
        dets, stamp = self.eyes.latest()
        if not stamp or time.time() - stamp > 2.5:
            return "（眼睛現在沒框到東西，沒辦法幫你算距離）"
        rows = []
        for lab, arr in (dets or {}).items():
            for d in arr[:1]:
                if self.eyes.is_self(d):
                    continue
                secs = self.walk_secs_for(d["h"])
                if secs is None:
                    continue
                side = ("正前方" if abs(d["cx"] - 0.5) < 0.08 else
                        f"偏{'右' if d['cx'] > 0.5 else '左'} {abs(d['cx'] - 0.5) * 140:.0f} 度")
                rows.append((secs, f"{lab}：{side}、框高 {d['h']:.2f} → "
                                   + ("**已經到它面前了，直接按 E**" if secs <= 0.3
                                      else f"大約按 w **{secs:.1f} 秒**")))
        if not rows:
            return "（框到的東西都太小／太遠，估不出距離）"
        rows.sort()
        return (NL.join(r[1] for r in rows[:5]) + NL
                + f"（換算式：秒數 ≈ (1/框高 − 1/{self.ARRIVE_H}) × {getattr(self, 'walk_k', 1.6):.2f}，"
                  f"這個 {getattr(self, 'walk_k', 1.6):.2f} 是你自己走出來的實測值）")

    # ---------- 空間記憶：畫面外的東西在哪
    FOV_DEG = 70.0

    def remember_space(self):
        """把眼睛看到的東西換算成世界座標記起來——之後轉開頭也還記得它在哪。"""
        dets, stamp = self.eyes.latest()
        if not stamp or time.time() - stamp > 2.0:
            return
        import math
        mem = getattr(self, "space_mem", None)
        if mem is None:
            mem = self.space_mem = {}
        yaw = getattr(self, "yaw", 0.0)
        for lab, arr in (dets or {}).items():
            for d in arr[:1]:
                if self.eyes.is_self(d) or d["score"] < 0.25 or d["h"] < 0.03:
                    continue
                bearing = yaw + (d["cx"] - 0.5) * self.FOV_DEG
                secs = self.walk_secs_for(d["h"]) or 0.0
                th = math.radians(bearing)
                mem[lab] = {"x": self.pos[0] + math.sin(th) * secs,
                            "y": self.pos[1] + math.cos(th) * secs,
                            "秒": round(secs, 2), "時間": time.time(),
                            "分數": round(d["score"], 2)}
        # 只留最近 12 樣、10 分鐘內的
        for k in [k for k, v in mem.items() if time.time() - v["時間"] > 600]:
            del mem[k]
        if len(mem) > 12:
            for k in sorted(mem, key=lambda k: mem[k]["時間"])[:len(mem) - 12]:
                del mem[k]

    def space_text(self):
        """畫面外的東西在你的哪個方向、大概多遠——他最缺的空間感。"""
        import math
        mem = getattr(self, "space_mem", {}) or {}
        if not mem:
            return ("（還沒有可以回想的東西。看到什麼，程式就會幫你記下它在哪，"
                    "之後你轉開頭也還知道它在你的哪個方向。）")
        dets, stamp = self.eyes.latest()
        now_vis = set()
        if stamp and time.time() - stamp < 2.0:
            now_vis = {k for k, v in (dets or {}).items() if v}
        yaw = getattr(self, "yaw", 0.0)
        rows = []
        for lab, v in mem.items():
            vx, vy = v["x"] - self.pos[0], v["y"] - self.pos[1]
            dist = math.hypot(vx, vy)
            if dist < 0.15:
                continue
            ang = (math.degrees(math.atan2(vx, vy)) - yaw + 540) % 360 - 180
            a = abs(ang)
            side = ("正前方" if a <= 20 else
                    ("右前方" if ang > 0 else "左前方") if a <= 70 else
                    ("正右邊" if ang > 0 else "正左邊") if a <= 110 else
                    ("右後方" if ang > 0 else "左後方") if a <= 160 else "正後方")
            age = int(time.time() - v["時間"])
            tag = "（現在還看得到）" if lab in now_vis else f"（{age} 秒前看到的，現在不在畫面上）"
            rows.append((a if lab in now_vis else a + 1000,
                         f"{lab}：{side} {a:.0f} 度、大約 {dist:.1f} 秒的路"
                         f"　→ 要看它就往{'右' if ang > 0 else '左'}轉（look dx {self.deg_dx(ang)}）{tag}"))
        if not rows:
            return "（記過的東西都在你腳邊，沒什麼好回想的）"
        rows.sort()
        return (NL.join(r[1] for r in rows[:6]) + NL
                + "（這是程式用你走過的路推算的，不是現在看到的。**轉頭之前先想一下你要找的東西在哪一邊**，"
                  "不要盲目繞圈。）")

    def last3_text(self):
        r = getattr(self, "recent3", [])
        if not r:
            return "（還沒開始）"
        return NL.join(f"{i + 1} 步前：{x}" for i, x in enumerate(reversed(r)))

    # ---------------- 短期記憶（最近幾輪：畫面＋當時做了什麼）
    # 2026-09-17 使用者：「給他增加短期記憶功能（能記住最近步驟的照片和他做了什麼）」。
    # 原本每輪只有 recent3 那三行文字（做了什麼→畫面變化多少），他看不到**上一輪長什麼樣子**，
    # 所以分不出「按了 w 畫面沒變」是被牆擋住、還是根本還沒進場、還是又繞回同一個地方。
    # 現在把最近 N 輪的縮圖連同「看到／想／做了／結果」一起回灌；縮圖壓到 384 寬省 token。
    SHORT_MEM_N = 4            # 記最近幾輪
    SHORT_MEM_IMG = 3          # 其中最新幾輪附畫面（其餘只留文字）
    SHORT_MEM_W = 384          # 縮圖寬度
    SHORT_MEM_FILE = "短期記憶.json"

    def short_mem_keep(self):
        return max(0, int(getattr(self.args, "short_mem", self.SHORT_MEM_N) or 0))

    def short_mem_imgs(self):
        return max(0, int(getattr(self.args, "short_mem_img", self.SHORT_MEM_IMG) or 0))

    def load_short_mem(self):
        """重啟後把文字的部分接回來（圖不存檔，重開就只剩文字，那是刻意的）。"""
        self.short_mem = []
        try:
            with open(os.path.join(self.dir, self.SHORT_MEM_FILE), encoding="utf-8") as f:
                rows = json.load(f)
            if isinstance(rows, list):
                self.short_mem = [dict(r, **{"圖": ""}) for r in rows[-self.short_mem_keep():]
                                  if isinstance(r, dict)]
        except (OSError, json.JSONDecodeError, TypeError):
            pass

    def save_short_mem(self):
        try:
            with open(os.path.join(self.dir, self.SHORT_MEM_FILE), "w", encoding="utf-8") as f:
                json.dump([{k: v for k, v in r.items() if k != "圖"} for r in self.short_mem],
                          f, ensure_ascii=False, indent=1)
        except OSError:
            pass

    def short_mem_push(self, seen, want, did, eff, im=None):
        """一輪結束時收進短期記憶：那一輪的畫面縮圖＋看到／想／做了／結果。"""
        keep = self.short_mem_keep()
        if keep <= 0:
            return
        rec = {"輪": self.stat.get("輪數", 0),
               "時": datetime.now().strftime("%H:%M:%S"),
               "看到": str(seen or "")[:60], "想": str(want or "")[:40],
               "做了": str(did or "")[:80], "結果": str(eff or "")[:40],
               "區": str(getattr(self, "cur_zone", "") or ""), "圖": ""}
        try:
            rec.update(self.step_record())      # 原本送／實際執行／被擋／程式替你做（2026-09-29）
        except Exception as e:  # noqa: BLE001
            print(f"   上一步紀錄失敗：{e}")
        im = getattr(self, "_frame_im", None) if im is None else im
        if im is not None:
            try:
                w, h = im.size
                th = im.resize((self.SHORT_MEM_W, max(1, int(h * self.SHORT_MEM_W / max(1, w)))))
                rec["圖"], _ = jpeg_data_uri(th, 55)
            except Exception:  # noqa: BLE001
                rec["圖"] = ""
        self.short_mem = (getattr(self, "short_mem", []) + [rec])[-keep:]
        self.save_short_mem()

    def short_mem_text(self, n=None):
        """純文字版：反射輪、檢討、對話都用這一份（不花圖片的 token）。"""
        rows = list(getattr(self, "short_mem", []))[-(n or self.short_mem_keep()):]
        if not rows:
            return ""
        out = ["【短期記憶：你最近做了什麼（最下面那筆最新）】"]
        for i, r in enumerate(rows):
            ago = len(rows) - i
            out.append(f"　{ago} 輪前（{r.get('時', '')}）在「{r.get('區') or '沒標區'}」："
                       f"看到「{r.get('看到', '')}」→ 想「{r.get('想', '')}」"
                       f"→ 做了「{r.get('做了', '')}」"
                       + (f"［被擋：{r['擋'][:30]}］" if r.get("擋") else "")
                       + (f"［程式替你：{r['程式'][:50]}］" if r.get("程式") else "")
                       + f"→ {r.get('結果', '')}")
        return NL.join(out)

    # ---------------- 任務完成另外記錄（2026-10-12 使用者：「任務成功呢？例如完成任務這些的，也要另外記錄」）
    # 右上角任務換成另一件事＝上一件事做完了（遊戲自己推進的，不是程式判的）。
    # 但換掉不一定是完成：死掉回大廳、重新進場、OCR 讀歪（「檢查病的況」）都會換。所以：
    #   ・字差不多（OCR 歪掉）→ 不算換
    #   ・換的時候人在大廳、或新字是「正在傳送／等待／在醫院工作直到…」這種不是任務的字 → 記「換局」
    #   ・上一件任務開始之後死過 → 記「重置」
    #   ・其餘 → 記「完成」
    # 只記錄、只給他看，不加分不扣分。寫 記憶/任務紀錄.jsonl，面板最上面一行「任務完成 N 次」。
    TASK_LOG = "任務紀錄.jsonl"
    TASK_NOT = ("正在傳送", "等待", "在醫院工作直到", "大廳", "載入", "前往")

    @staticmethod
    def task_same(a, b):
        import difflib
        a, b = str(a or "").strip(), str(b or "").strip()
        if not a or not b:
            return False
        if a == b or (len(a) >= 4 and (a in b or b in a)):
            return True
        return difflib.SequenceMatcher(None, a, b).ratio() >= 0.6

    def task_log(self, old, new):
        if self.task_same(old, new):
            self._task_since = getattr(self, "_task_since", None) or (self.stat.get("輪數", 0), getattr(self, "death_count", 0))
            return
        since = getattr(self, "_task_since", None) or (self.stat.get("輪數", 0), getattr(self, "death_count", 0))
        rounds = max(0, self.stat.get("輪數", 0) - since[0])
        died = getattr(self, "death_count", 0) > since[1]
        try:
            lobby = bool(self.in_lobby())
        except Exception:  # noqa: BLE001
            lobby = False
        not_task = any(k in str(new) for k in self.TASK_NOT) or any(k in str(old) for k in self.TASK_NOT)
        if not old:
            kind = "開始"
        elif lobby or not_task:
            kind = "換局"
        elif died:
            kind = "重置"
        else:
            kind = "完成"
        rec = {"時間": datetime.now().strftime("%H:%M:%S"), "輪": self.stat.get("輪數", 0),
               "輪班": self.score.get("目前輪班"), "從": str(old)[:20], "到": str(new)[:20],
               "判定": kind, "花了幾輪": rounds, "中間死過": died, "在大廳": lobby}
        try:
            with open(os.path.join(self.dir, self.TASK_LOG), "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + NL)
        except OSError:
            pass
        self._task_since = (self.stat.get("輪數", 0), getattr(self, "death_count", 0))
        if kind == "完成":
            self.stat["任務完成"] = self.stat.get("任務完成", 0) + 1
            self.task_done_log = (list(getattr(self, "task_done_log", []) or []) + [(rec["時間"], str(old)[:20], rounds)])[-6:]
            print(f"   ✅ 任務完成（第 {self.stat['任務完成']} 件）：「{old}」花了 {rounds} 輪 → 下一件「{new}」")
            self.history.append(f"[{rec['時間']}] ✅ 任務「{old}」完成（花了 {rounds} 輪），下一件：「{new}」")
        else:
            self.stat["任務換掉"] = self.stat.get("任務換掉", 0) + 1
            print(f"   ↻ 任務換掉（{kind}）：「{old}」→「{new}」，{rounds} 輪" + ("，中間死過" if died else ""))

    def task_done_text(self):
        """面板最上面：今天完成了幾件任務（只有遊戲自己把右上角換成下一件才算）。"""
        n = self.stat.get("任務完成", 0)
        log = list(getattr(self, "task_done_log", []) or [])
        if not n:
            return "📋 **任務完成：0 件**（右上角的任務被遊戲換成下一件才算完成；換局、死掉重來不算）"
        last = "；".join(f"{t}「{nm}」{r} 輪" for t, nm, r in log[-3:])
        return f"📋 **任務完成：{n} 件**（最近：{last}）"

    def top_notes_text(self):
        """2026-10-03 使用者：「記錄好 都做」——放在面板最上面的三件事。只告訴他，不替他做、不加賞罰。
        ① 量比例提醒（放在【上一步】最後面他沒看到）；② 他自己上次收工寫的檢討（以前只存檔、沒讀回來）；
        ③ 他寫「視角水平」但水平儀顯示歪了——把矛盾指給他看。"""
        out = []
        try:
            out.append(self.acc_text())        # 2026-10-05 使用者：給他目標「先優先提高正確率」
        except Exception:  # noqa: BLE001
            pass
        try:
            out.append(self.task_done_text())  # 2026-10-12 使用者：任務完成要另外記錄
        except Exception:  # noqa: BLE001
            pass
        try:
            cn = self.check_notes_text()       # 2026-10-05 使用者：檢查輪可以簡單寫一句 → 快思要讀得到
            if cn:
                out.append(cn)
        except Exception:  # noqa: BLE001
            pass
        gm = getattr(self, "gmap", None)
        if self.gmap_on() and gm is not None and not gm.scale:
            out.append("📏 **提醒：這個場景的深度比例你還沒量，地圖畫不出牆**"
                       + (f"（已經替你存著 {len(gm.pending)} 面等著畫）" if gm.pending else "")
                       + '。要畫地圖第一步就是量比例：找一個正對牆、前方有 3 步以上空間、沒有面板蓋著的時候，送 {"t":"量比例"}。')
        rv = self.last_review_text()
        if rv:
            out.append(rv)
        try:
            pd = float(getattr(self, "pitch_deg", 0.0) or 0.0)
            seen = str(getattr(self, "last_seen", "") or "") + str(getattr(self, "_last_think", "") or "")
            if abs(pd) > 20 and "水平" in seen:
                out.append(f"🧭 **你上一輪寫「視角水平」，但水平儀顯示{'抬頭' if pd > 0 else '低頭'} {abs(pd):.0f} 度**——"
                           "以水平儀為準（等級表第 18 條），先把視角修回水平再做別的。")
        except (TypeError, ValueError):
            pass
        return NL.join(out)

    # ---------------- 正確率（2026-10-05 使用者：「給他目標 先優先提高正確率」）
    ACC_KEEP = 40

    def acc_note(self, summ, dn):
        """這一輪按下去的那一下有沒有反應。按 E 用程式判的成功／沒反應；其他用畫面有沒有變（變化 ≥3）。
        程式帶路走的、只用工具或只等的輪次不算。只記錄給他看，不加分也不扣分。"""
        import re as _re
        if getattr(self, "_routed", ""):
            return
        s = str(summ or "")
        if "長按 E" in s and ("成功）" in s or "沒反應）" in s):
            kind, ok = "按E", "成功）" in s
        elif dn is None or dn < 0:
            return
        elif "長按 E" in s or _re.search(r"按 e ", s):
            kind, ok = "按E", dn >= 3
        elif "點「" in s or "點字" in s:
            kind, ok = "點", dn >= 3
        elif _re.search(r"按 [wasd] ", s):
            kind, ok = "走", dn >= 3
        elif "轉視角" in s:
            kind, ok = "轉", dn >= 3
        else:
            return
        self.acc_log = (list(getattr(self, "acc_log", []) or []) + [(kind, bool(ok))])[-self.ACC_KEEP:]
        self.stat["有反應" if ok else "沒反應"] = self.stat.get("有反應" if ok else "沒反應", 0) + 1

    def acc_text(self):
        """面板最上面那一行：使用者給的目標＋他最近幾下的正確率。"""
        head = ("🎯 **使用者給你的目標（10/05）：先優先提高正確率**——每一下按下去之前先看畫面確認，按下去要有反應；"
                "寧可少按，不要空按。")
        log = list(getattr(self, "acc_log", []) or [])
        tail = ('想提高它，你可以自己改【你自己寫的提示】：{"t":"改提示","內容":"你要每一輪都看到的話"}')
        if len(log) < 5:
            return head + NL + "　（你的正確率還在累計，按幾下之後這裡會出現數字。）" + tail
        n, okn = len(log), sum(1 for _k, ok in log if ok)
        parts = []
        for k in ("按E", "點", "走", "轉"):
            sub = [ok for kk, ok in log if kk == k]
            if sub:
                parts.append(f"{k} {sum(sub)}/{len(sub)}")
        return (head + NL + f"　你最近 {n} 下：**有反應 {okn} 下（{100 * okn // n}%）**｜" + "、".join(parts) + "。" + tail)

    # ---------------- 他自己寫的提示（2026-10-05 使用者：「你讓他自己去更新 Prompt」）
    MY_PROMPT_FILE = "98_我自己寫的提示.md"
    MY_PROMPT_MAX = 1200

    def edit_my_prompt(self, a):
        """{"t":"改提示","內容":"…"}：整段換成他寫的；加 "加":"一句" 是接在後面；"刪":"舊的那一句" 是拿掉那一句。
        寫進 專案/<遊戲>/提示/98_我自己寫的提示.md（system 提示的倒數第二段，每一輪都會讀到），舊版留在 記憶/我的提示歷史.jsonl。"""
        d = project_dir(self.game, "提示")
        os.makedirs(d, exist_ok=True)
        fp = os.path.join(d, self.MY_PROMPT_FILE)
        head = ("---" + NL + "章節: §98 我自己寫的提示" + NL + "適用: 全部" + NL + "---" + NL * 2
                + "【你自己寫的提示】（這一段是你自己寫的、你自己可以改：" + '{"t":"改提示","內容":"…"}'
                + "；屬於第 3 級，跟上面的文件抵觸時以上面為準）" + NL)
        old = ""
        try:
            raw = open(fp, encoding="utf-8").read()
            i = raw.find("抵觸時以上面為準）")
            old = raw[i + len("抵觸時以上面為準）"):].strip() if i >= 0 else ""
        except OSError:
            pass
        new = old
        body = str(a.get("內容") or a.get("content") or "").strip()
        add = str(a.get("加") or "").strip()
        cut = str(a.get("刪") or "").strip()
        if body:
            new = body
        if cut and cut in new:
            new = new.replace(cut, "").strip()
        if add:
            new = (new + NL + add).strip()
        new = NL.join(l.rstrip() for l in new.splitlines() if l.strip())[:self.MY_PROMPT_MAX]
        if not new or new == old:
            print("   ✍ 改提示：內容是空的或跟原本一樣，沒有改")
            return False
        with open(fp, "w", encoding="utf-8") as f:
            f.write(head + new + NL)
        try:
            with open(os.path.join(self.dir, "我的提示歷史.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps({"時間": datetime.now().strftime("%m/%d %H:%M:%S"), "輪": self.stat.get("輪數", 0),
                                    "舊": old, "新": new}, ensure_ascii=False) + NL)
        except OSError:
            pass
        self.stat["改提示"] = self.stat.get("改提示", 0) + 1
        print(f"   ✍ 他改了自己的提示（{len(old)} → {len(new)} 字）：{new[:80]}")
        self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 你改了【你自己寫的提示】，下一輪起每一輪都會讀到："
                            + new[:60])
        self.note_progress("改提示")
        return True

    def last_review_text(self):
        """讀這支遊戲最近一次收工檢討（記憶/進步日誌/最新一天.md 的「它自己寫的」），開機讀一次就快取。"""
        if getattr(self, "_review_cache", None) is None:
            self._review_cache = ""
            try:
                import glob as _glob
                fs = sorted(f for f in _glob.glob(os.path.join(self.dir, "進步日誌", "20*.md")))
                if fs:
                    txt = open(fs[-1], encoding="utf-8").read()
                    i = txt.find("## 它自己寫的")
                    if i >= 0:
                        body = txt[i + len("## 它自己寫的"):]
                        body = body.split("→ 已寫入")[0].split(NL + "## ")[0].strip()
                        keep = [l.strip() for l in body.splitlines() if l.strip().startswith(("我還是做不好", "明天要試", "我比昨天進步"))]
                        if keep:
                            self._review_cache = ("📓 **你上次收工自己寫的檢討**（" + os.path.basename(fs[-1])[:-3]
                                                  + "，是你自己寫的，照著做）：" + NL + NL.join("　" + l for l in keep))
            except Exception:  # noqa: BLE001
                pass
        return self._review_cache

    def last_step_text(self):
        """2026-09-22 使用者：**上一步做了什麼、為什麼做、結果如何**要單獨拉出來講。

        短期記憶已經記了四輪，但那是一整段；他需要的是「**上一步**」這一行擺在眼前，
        才不會把剛剛失敗的動作再做一次。重複與否由他自己判斷、自己處理——
        這裡只把事實攤開，不擋、不罰。
        """
        rows = list(getattr(self, "short_mem", []))
        if not rows:
            return ""
        r = rows[-1]
        did = str(r.get("做了") or "").strip() or "沒動作"
        why = str(r.get("想") or "").strip()
        eff = str(r.get("結果") or "").strip()
        dead = ("幾乎沒動" in eff or "變化 0" in eff or "沒生效" in eff)
        # 2026-09-29 使用者：「他需要知道自己上一步按了什麼、為什麼要用這個按鈕、和他原本想要怎麼做」，
        # 也要知道**程式替他按了什麼**——不然選單被程式打開，他只會以為是自己按壞的。
        orig, exe = str(r.get("原本") or ""), str(r.get("實際") or "")
        lines = [f"【上一步】你原本想：{why or '（沒寫）'}"]
        lines.append(f"　你送出：{orig or did}")
        def _kinds(x):
            # 「1s」跟「1.0s」、標籤文字不同不算不一樣；鍵、方向、秒數、dx/dy 數值不同就算（程式改過他的動作）
            return sorted(re.sub(r"「.*?」", "", re.sub(r"(\d+)\.0(?!\d)", r"\1", p)).strip()
                          for p in x.split("、") if p and not p.startswith("工具"))
        if exe and _kinds(exe) != _kinds(orig):
            lines.append(f"　實際按下去的：{exe}（跟你送的不一樣＝程式改過或只做了一部分）")
        elif not exe and any(not p.startswith("工具") for p in orig.split("、") if p):
            lines.append("　實際按下去的：（沒有）——你送的動作一個都沒真的做")
        if r.get("擋"):
            lines.append(f"　被擋掉沒做：{r['擋']}")
        if r.get("程式"):
            lines.append(f"　⚙ **程式替你做了**：{r['程式']}——這不是你按的，畫面的變化可能是它造成的")
        if eff:
            lines.append(f"　結果：{eff}")
        out = NL.join(lines)
        if dead:
            out += ("　⚠ **那一下沒有效果**。同樣的做法不會突然有效——"
                    "這一輪換別的做法（換方向、先關掉擋住的東西、先確認游標有沒有被鎖），"
                    "不要再做一次一樣的事。")
        return out + NL

    def repeat_alarm(self, n=6):
        """把「你正在重複」這件事算出來，用一句話講在最前面。

        2026-09-18 使用者：「他需要有前幾步驟，才知道自己是不是卡住了在重複操作，
        讓他可以更快發現並檢討」。短期記憶已經把前幾輪攤給他看了，但他自己承認
        「只盯著當下這一張圖做反應，沒有把那 4 筆拿去比對」——所以這裡改成**程式先比對好**，
        直接把結論放在提示的最上面，而且反射輪也看得到（他 3/4 的輪次都是反射）。
        """
        rows = list(getattr(self, "short_mem", []))[-n:]
        if len(rows) < 3:
            return ""
        from collections import Counter

        def norm(t):
            """把「同一件事」對齊：座標四捨五入到 0.05 格、丟掉秒數與他自己取的標籤。

            2026-09-18 實測踩到的坑：他連點三次同一顆按鈕，紀錄卻是
            「點「頂部按鈕」(0.44,0.05)、等 0.5s」「點「頂部左按鈕」(0.43,0.05)、等 0.8s」
            ——名字與秒數每次都差一點點，用完全比對永遠湊不到 3 次，警報就一次都沒響。
            現在只比「做了什麼動作＋大概哪個位置」。
            """
            import re as _re
            t = str(t or "")
            t = _re.sub(r"「[^」]*」", "", t)                    # 丟掉標籤（頂部按鈕／頂部左按鈕…）
            t = _re.sub(r"[\d.]+\s*s", "", t)                 # 丟掉秒數（0.5s／1.0s）
            t = _re.sub(r"等\s*[\d.]+", "", t)                   # 丟掉「等 1.0」
            def _grid(m):
                return "(%.2f,%.2f)" % (round(float(m.group(1)) * 20) / 20,
                                        round(float(m.group(2)) * 20) / 20)
            t = _re.sub(r"\(([\d.]+)\s*,\s*([\d.]+)\)", _grid, t)   # 座標對齊到 0.05 格
            t = _re.sub(r"[+-]?\d{2,}", "N", t)                  # 轉視角幅度之類的大數字
            return "".join(ch for ch in t if not ch.isspace())[:40]

        c = Counter(norm(r.get("做了")) for r in rows if norm(r.get("做了")))
        if not c:
            return ""
        top, k = c.most_common(1)[0]
        if k < 3:
            return ""
        dead = sum(1 for r in rows
                   if norm(r.get("做了")) == top and ("幾乎沒動" in str(r.get("結果")) or "變化 0" in str(r.get("結果"))))
        raw = next((str(r.get("做了")) for r in rows if norm(r.get("做了")) == top), top)
        line = (f"⚠⚠ **重複警報：最近 {len(rows)} 輪裡，你做了 {k} 次一模一樣的「{raw[:40]}」**"
                + (f"，其中 {dead} 次畫面幾乎沒有變。" if dead else "。"))
        if k >= 4 or dead >= 2:
            line += ("　**同一招做這麼多次都沒有結果，它就是沒用。**這一輪**不要再做它**——"
                     "換一個完全不同的做法（換目標、換方向、先關掉擋住的東西、"
                     "或把已經會做的那串用 {\"t\":\"錄成程序\",\"名\":\"...\",\"秒\":180} 錄起來再換事情做）。")
        else:
            line += "　再做一次之前先問自己：上一次做這個發生了什麼？沒發生什麼就換做法。"
        return line

    def short_mem_content(self, n_img=None):
        """多模態版：最近幾輪的縮圖，每張前面標清楚是幾輪前、當時做了什麼。"""
        rows = list(getattr(self, "short_mem", []))[-self.short_mem_keep():]
        if not rows:
            return []
        n_img = self.short_mem_imgs() if n_img is None else n_img
        has = [i for i, r in enumerate(rows) if r.get("圖")]
        with_img = set(has[-n_img:]) if n_img > 0 else set()
        out = [{"type": "text", "text":
                "【短期記憶：最近幾輪的畫面與你當時做的事】"
                "（**這些是過去的畫面，不是現在的**。用它比對：你做了那件事之後畫面有沒有變、"
                "是不是又繞回同一個地方、剛才點的東西有沒有反應。）"}]
        for i, r in enumerate(rows):
            ago = len(rows) - i
            out.append({"type": "text", "text":
                        f"【{ago} 輪前】看到「{r.get('看到', '')}」→ 想「{r.get('想', '')}」"
                        f"→ 做了「{r.get('做了', '')}」→ 結果：{r.get('結果', '')}"})
            if i in with_img:
                out.append({"type": "image_url", "image_url": {"url": r["圖"]}})
        return out

    # ---------------- 死亡檢討：檢討→改善→他自己決定這條記到哪裡
    # 2026-09-17 使用者：「每次死亡自我檢討與改善，並且保持記憶，讓他自己分類是屬於哪個
    # （基礎技藝還是遊戲記憶）」。程式只做分流：他寫什麼、分哪一類，都是他自己的判斷。
    IMPROVE_FILE = "死亡改善.json"
    CAND_FILE = os.path.join(HERE, "核心", "基礎操作", "_待升級候選.jsonl")

    DEATH_ASK = """你剛剛在「{game}」裡死了。這是第 {n} 次。

上面有死前那段時間的回放、以及你最近幾輪的畫面與動作。**先看畫面再下結論。**

⚠ 死因只能從「死前實際做的事」那份逐筆紀錄與回放裡找。畫面上剛好有什麼東西
（某個面板、某個 NPC）**不等於**它害死你——同時出現不是因果。看不出來就老實寫「看不出來」，
硬掰出來的規則會讓你之後一直做沒有用的事。

這次要多回答兩件事：
1. **改善**：下次從重生到同樣的地方，你**具體**要換掉哪一個動作？寫成你真的做得到的一兩句
   （像「重生後先按 w 走 2 秒再轉頭」那種程度，不是「更小心」這種空話）。
   ⚖ 寫之前先對【指令等級表】：改善是第 3 級，跟第 0～2 級抵觸就會被退件
   （例如高階說「卡住可以離開重進、離開確認視窗按 enter」，你就不能訂「彈窗點繼續不點離開」）。
2. **這條教訓要記到哪裡**——你自己分類：
   - 「基礎技藝」＝換一隻遊戲也成立的操作技巧（走位、視角、點 UI、判斷卡住…）。
     這類會被放進跨遊戲候選，等你在**第二隻遊戲**也遇到同一件事，才會升級成基本知識。
   - 「遊戲記憶」＝只有「{game}」才成立的事（這張地圖、這個怪、這個按鈕）。
     這類寫進你自己的知識資料夾，換遊戲就不帶走。
   分不出來、或這次根本沒學到東西，就填「不記」——寧可不記，也不要記一條錯的。

只輸出 JSON：
{{"cause":"死因(<=40字)","mistake":"錯誤決定(<=40字)","avoid":"下次怎麼避免(<=60字)",
 "改善":"寫成「開局第 N 秒：做 X」(<=60字，N 從錄影裡找出來，不要寫「小心一點」)",
 "記憶":"要記住的那一句(<=50字，沒有就留空)",
 "分類":"基礎技藝 或 遊戲記憶 或 不記",
 "檔":"遊戲記憶要放哪個分類檔(<=8字，例如 地圖／怪物／UI)",
 "為什麼分這類":"(<=40字)"}}

上次死因與檢討（若有）：
{last}

死前你實際做了什麼（程式一筆一筆記的，不會漏）：
{trace}

死前的紀錄（每輪一條的摘要）：
{history}

你自己上幾次死後訂的改善（做到了嗎？沒做到就講實話）：
{improve}"""

    def load_improve(self):
        self.improve = []
        try:
            with open(os.path.join(self.dir, self.IMPROVE_FILE), encoding="utf-8") as f:
                rows = json.load(f)
            if isinstance(rows, list):
                self.improve = [r for r in rows if isinstance(r, dict)][-20:]
        except (OSError, json.JSONDecodeError):
            pass

    def add_improve(self, body, n):
        body = str(body or "").strip()[:80]
        if not body:
            return
        self.improve = (getattr(self, "improve", []) + [{
            "次": n, "時間": datetime.now().strftime("%m/%d %H:%M"), "做法": body}])[-20:]
        self.save_improve()

    def save_improve(self):
        try:
            with open(os.path.join(self.dir, self.IMPROVE_FILE), "w", encoding="utf-8") as f:
                json.dump(getattr(self, "improve", []), f, ensure_ascii=False, indent=1)
        except OSError:
            pass

    # ---------------- 指令等級表（2026-09-23 使用者定）：高階規則優先，抵觸的低階規則刪掉
    # 表在 核心/共用/01_指令等級表.md（第 0 級鐵律、第 1 級使用者強制指令、第 2 級文件、第 3 級他自己訂的）。
    # 他自己訂的規則（扣分檢討規則、死後改善）進來時先對表；抵觸就退件。開機與表更新時整批重審，抵觸的刪掉。
    LEVEL_FILE = os.path.join(HERE, "核心", "共用", "01_指令等級表.md")
    AUDIT_ASK = """下面是【指令等級表】（第 0 級最高、第 3 級最低）和一串「他自己訂的規則」（全部是第 3 級）。
逐條判斷：這條第 3 級規則有沒有跟第 0～2 級的任何一條**抵觸**？
抵觸的判定要嚴：**只有「照著這條做，在某個具體情況下必然違反高階規則」才算**。
例：高階說「卡住就離開重進，離開確認視窗出現時按 enter 或點離開」，而這條說「看到繼續/離開/重生只准 esc」
或「彈窗點繼續不點離開」——照做就永遠離不開，這是抵觸。
不算抵觸：只是講不同的事；只是更細的做法；只是比高階更保守但沒擋住高階（例如高階說可以做 A，這條說做 A 之前先做 B）；
用詞相似但情況不同（「按 E 復活」是免費的隊友救援，不是花錢的自我復活）。拿不準就不算。
每一條抵觸都要附「確定」欄：true＝你能講出具體情況下它會擋住哪一條高階規則；false＝只是感覺像。
只輸出 JSON：{{"抵觸":[{{"編號":1,"高階規則":"抵觸到的那一條（原句 20 字內）","理由":"<=30字","確定":true}}]}}；沒有就 {{"抵觸":[]}}

【指令等級表】
{table}

【他自己訂的規則】
{rules}"""

    def level_table_text(self):
        try:
            with open(self.LEVEL_FILE, encoding="utf-8") as f:
                t = f.read()
            if t.startswith("---"):
                t = t.split("---", 2)[-1]
            return t.strip()
        except OSError:
            return ""

    def rule_audit(self, rules, why=""):
        """rules: [(來源, 鍵, 文字), ...] → {index: (抵觸到的高階規則, 理由)}。
        用模型判斷（整批用中想、單條用快思），只收「確定」的；拿不準就不刪。"""
        table = self.level_table_text()
        if not table or not rules:
            return {}
        listed = NL.join(f"{i + 1}. （{src}）{txt}" for i, (src, _k, txt) in enumerate(rules))
        # 實測（09/23）：中想 95 秒還常常想不完；快思 6 秒但偶爾誤判（把免費的按 E 復活當成付費復活）。
        # 所以用快思問三次、三次都說「確定抵觸」才算——刪錯規則比漏刪嚴重。
        votes = {}
        n_runs = 3
        for _ in range(n_runs):
            try:
                raw, _, _, _ = stream_json(self.endpoint, self.model,
                                           "你是規則審核員：只判斷低階規則有沒有抵觸高階規則，不加意見。",
                                           self.AUDIT_ASK.format(table=table, rules=listed),
                                           1200, time.time() + 60.0, 0.3, effort=快思)
            except Exception as e:  # noqa: BLE001
                print(f"   ⚖ 規則審核失敗（{why}）：{e}")
                return {}
            o = parse_json_loose(raw) or {}
            for h in (o.get("抵觸") or []):
                try:
                    i = int(h.get("編號")) - 1
                except (TypeError, ValueError):
                    continue
                sure = h.get("確定", False)
                sure = sure if isinstance(sure, bool) else str(sure).lower() in ("true", "1", "是")
                if 0 <= i < len(rules) and sure:
                    c, _hi, _r = votes.get(i, (0, "", ""))
                    votes[i] = (c + 1, str(h.get("高階規則", "")).strip()[:40], str(h.get("理由", "")).strip()[:40])
        return {i: (hi, r) for i, (c, hi, r) in votes.items() if c >= n_runs}

    def _audit_log(self, lines):
        try:
            with open(os.path.join(self.dir, "規則審核.md"), "a", encoding="utf-8") as f:
                f.write(NL.join(lines) + NL)
        except OSError:
            pass

    def audit_own_rules(self, why="開機"):
        """把他自己訂的規則整批對等級表，抵觸的刪掉並記到 記憶/規則審核.md。"""
        rules = [("扣分檢討·" + k, k, v.get("規則", "")) for k, v in (getattr(self, "lessons", {}) or {}).items()
                 if v.get("規則")]
        rules += [(f"第{r.get('次')}次死後改善", r, r.get("做法", ""))
                  for r in (getattr(self, "improve", []) or []) if r.get("做法")]
        if not rules:
            return 0
        bad = self.rule_audit(rules, why)
        if not bad:
            print(f"   ⚖ 規則審核（{why}）：{len(rules)} 條都沒抵觸等級表")
            return 0
        stamp = datetime.now().strftime("%m/%d %H:%M")
        lines, gone = [], []
        for i, (hi, reason) in bad.items():
            src, key, txt = rules[i]
            if src.startswith("扣分檢討·"):
                self.lessons.pop(key, None)
            else:
                self.improve = [r for r in self.improve if r is not key]
            lines.append(f"- {stamp}（{why}）刪掉「{txt}」（{src}）——抵觸「{hi}」：{reason}")
            gone.append(f"「{txt}」（抵觸：{hi}）")
            print(f"   ⚖ 抵觸高階規則，刪掉：{txt}　← {hi}（{reason}）")
        self.save_lessons()
        self.save_improve()
        self._audit_log(lines)
        self.lesson_hint = (getattr(self, "lesson_hint", "") or "") + (
            "⚖ **規則審核**：你自己訂的這幾條跟【指令等級表】的高階規則抵觸，已經作廢、刪掉了："
            + "；".join(gone) + "。以後照高階的做。" + NL)
        return len(bad)

    def level_tick(self):
        """等級表檔案改了（使用者加了新指令）就整批重審一次。"""
        try:
            m = os.path.getmtime(self.LEVEL_FILE)
        except OSError:
            return
        if m != getattr(self, "_level_mtime", None):
            first = getattr(self, "_level_mtime", None) is None
            self._level_mtime = m
            self.audit_own_rules("開機" if first else "等級表更新")

    def improve_text(self, n=3):
        rows = list(getattr(self, "improve", []))[-n:]
        if not rows:
            return ""
        return ("【你自己死後訂的改善（是你自己寫的，做不到等於白檢討）】" + NL
                + NL.join(f"　- 第 {r.get('次')} 次死後：{r.get('做法')}" for r in rows))

    def note_candidate(self, body, why=""):
        """基礎技藝的跨遊戲候選：同一件事在第二隻遊戲又出現，才准升級成基本知識。

        沿用 升級知識 的規矩（核心不能被單一遊戲污染），差別只在這裡是**他自己死出來的**，
        由程式幫他記帳，不用他自己記得曾經在別的遊戲遇過。
        """
        rows = []
        try:
            with open(self.CAND_FILE, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            rows.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
        except OSError:
            pass
        hit = None
        for r in rows:
            if self._same_thing(str(r.get("內容", "")), body):
                hit = r
                break
        if hit is None:
            hit = {"內容": body, "遊戲": [], "次數": 0, "為什麼": why[:60]}
            rows.append(hit)
        hit.setdefault("遊戲", [])
        if self.game not in hit["遊戲"]:
            hit["遊戲"].append(self.game)
        hit["次數"] = int(hit.get("次數", 0)) + 1
        hit["最後"] = datetime.now().strftime("%m/%d %H:%M")
        try:
            os.makedirs(os.path.dirname(self.CAND_FILE), exist_ok=True)
            with open(self.CAND_FILE, "w", encoding="utf-8") as f:
                for r in rows[-200:]:
                    f.write(json.dumps(r, ensure_ascii=False) + NL)
        except OSError:
            pass
        return hit

    def remember_lesson(self, body, kind, file="", why="", src=""):
        """他自己分類之後，程式只負責放到對的地方。回傳實際記到哪。"""
        body = str(body or "").strip()[:120]
        kind = str(kind or "").strip()
        if not body or "不記" in kind:
            print("   （這次他選擇不記，沒寫任何東西）")
            return ""
        tag = f"（{src}）" if src else ""
        if "基礎" in kind:
            self.edit_knowledge({"t": "記知識", "檔": "基礎技藝", "內容": body + tag})
            hit = self.note_candidate(body, why)
            games = hit.get("遊戲", [])
            print(f"   🧱 他把這條分成**基礎技藝**：{body[:40]}"
                  f"（跨遊戲候選，目前在 {'、'.join(games)} 出現過）")
            if len(games) >= 2:
                self.edit_knowledge({"t": "升級知識", "內容": body,
                                     "遊戲": games, "為什麼": why or "他自己死後檢討出來的"})
                self.history.append(
                    f"[{datetime.now().strftime('%H:%M:%S')}] 你這條基礎技藝在"
                    f"「{'」「'.join(games)}」都遇過了，已經升級成基本知識：{body[:30]}")
            else:
                self.lesson_hint = (getattr(self, "lesson_hint", "") or "") + (
                    f"🧱 你剛才把「{body[:30]}」分成**基礎技藝**。它先放在你自己的「基礎技藝」檔"
                    f"與跨遊戲候選裡；等你在別的遊戲也遇到同一件事，才會升級成基本知識。" + NL)
            return "基礎技藝"
        self.edit_knowledge({"t": "記知識", "檔": (file or "死亡教訓"), "內容": body + tag})
        print(f"   📗 他把這條分成**遊戲記憶**（{file or '死亡教訓'}）：{body[:40]}")
        return "遊戲記憶"

    def _after_death_review(self):
        if getattr(self, "death_note", ""):
            self.add_note(f"（死亡檢討）{self.death_note[:60]}")

    def death_review(self):
        if not self._in_review_thread():
            return self.review_async("死亡檢討", self.death_review, after=self._after_death_review)
        """覆寫父類別：回放＋短期記憶一起看，檢討要多出「改善」與「他自己分類要記到哪」。"""
        n = getattr(self, "death_count", 1)
        print(f"   💀 第 {n} 次死亡，請他自己檢討、訂改善、並決定這條記到哪裡")
        content = []
        # 2026-09-23 使用者（學習的知識）：先錄影再檢討——回放要從**進場那一秒**開始攤，
        # 每張標「開局第 N 秒」，改善要寫成「開局第 N 秒：做 X」，下次在那一秒改做法。
        born = getattr(self, "_enter_at", 0) or 0
        life = (time.time() - born) if born else None
        secs = 40.0 if life is None else max(40.0, min(118.0, life + 3.0))
        shots = self.replay_shots(secs)
        if shots:
            print(f"   🎞 附上回放 {len(shots)} 張（涵蓋死前 {shots[0][0]:.0f} 秒"
                  + (f"，這條命活了 {life:.0f} 秒" if life is not None else "") + "）")
            for ago, uri in shots:
                if life is not None:
                    at = max(0.0, life - ago)
                    content.append({"type": "text", "text": f"【開局第 {at:.0f} 秒｜死前 {ago:.0f} 秒】"})
                else:
                    content.append({"type": "text", "text": f"【死前 {ago:.0f} 秒】"})
                content.append({"type": "image_url", "image_url": {"url": uri}})
            content.append({"type": "text", "text":
                            "上面是**這條命從進場開始的錄影**（每張標了「開局第幾秒」與「死前幾秒」）。"
                            "從第一張往下看，不要只看最後一張：**第幾秒**出現了讓你死的東西？那一秒你在做什麼？"
                            "改善要寫成「開局第 N 秒：做 X」，下次到那一秒就照做。"})
        content += self.short_mem_content()
        _j = self.step_journal_text()
        if _j:
            content.append({"type": "text", "text": _j})
        ask = self.DEATH_ASK.format(
            game=self.game, n=n,
            last=getattr(self, "death_note", "") or "（無）",
            trace=self.trace_text(40.0, end=getattr(self, "last_death", None)),
            history=NL.join(self.history[-25:]),
            improve=self.improve_text(5) or "（還沒訂過）")
        content.append({"type": "text", "text": "【死時的視角】" + self.view_state() + NL + ask + NL
                        + "（2026-09-28 使用者要求）另外**一定要**多給兩個欄位："
                        + '"錯誤類型":["從這份清單挑一到三個：沒互動到、走不到、地圖不對、看錯畫面、被面板擋住、不知道下一步、狀態沒顧（血量／體力之類）、太慢、方向錯、其他"],'
                        + '"為什麼是這類":"一句話，指出回放裡哪一秒看得出來"。'})
        raw, _, _, _ = stream_json(self.endpoint, self.model,
                                   "你是正在玩 Roblox 遊戲的玩家，只輸出 JSON。",
                                   content, 2600, time.time() + 240.0, 0.1, effort=慢想)
        o = parse_json_loose(raw) or {}
        if not (o.get("cause") or o.get("avoid") or o.get("改善")):
            # 2026-09-18 實戰第一次就踩到：欄位太多＋慢想的思考吃掉額度，模型沒講完。
            # 先把原文印出來（才知道是沒講完還是格式不對），再用**三個欄位**的精簡版補問一次。
            print(f"   ⚠ 死亡檢討第一次沒解析出來，原文開頭：{str(raw)[:160]}")
            short = ("你剛剛死了（第 %d 次）。看上面的回放與最近幾輪，只回答三件事，**不要說明理由**：" % n
                     + NL + '{"cause":"死因(<=30字，看不出來就寫看不出來)",'
                            '"改善":"下次具體換掉哪個動作(<=40字)",'
                            '"分類":"基礎技藝 或 遊戲記憶 或 不記"}')
            raw2, _, _, _ = stream_json(self.endpoint, self.model,
                                        "你是正在玩 Roblox 遊戲的玩家，只輸出 JSON。",
                                        content[:-1] + [{"type": "text", "text": short}],
                                        800, time.time() + 90.0, 0.2, effort=快思)
            o2 = parse_json_loose(raw2) or {}
            if o2:
                print("   ↻ 用精簡版補問後拿到檢討")
                o = {**o2, **{k: v for k, v in o.items() if v}}
        cause = str(o.get("cause") or "")[:60]
        mistake = str(o.get("mistake") or "")[:60]
        avoid = str(o.get("avoid") or "")[:80]
        fix = str(o.get("改善") or o.get("fix") or "")[:80]
        lesson = str(o.get("記憶") or o.get("lesson") or "")[:120]
        kind = str(o.get("分類") or o.get("kind") or "")
        fname = str(o.get("檔") or o.get("file") or "")[:8]
        why = str(o.get("為什麼分這類") or o.get("why") or "")[:60]
        if not (cause or avoid or fix):
            print("   ⚠ 死亡檢討沒拿到內容（模型沒講完），這次不記")
            self.record_problem(f"第{n}次死亡／遊戲結束，檢討沒拿到內容", source="死亡檢討")
            return
        # 2026-09-28 使用者：死亡後要檢討為什麼、可能是什麼錯誤（沒互動到、走不到、地圖不對…）→ 記成問題
        et = o.get("錯誤類型") or o.get("error_type") or []
        et = "、".join(str(x) for x in (et if isinstance(et, list) else [et]) if x) or "（沒分類）"
        self.record_problem(f"第{n}次死亡／遊戲結束［{et}］：{cause}",
                            tried=mistake, cause=str(o.get("為什麼是這類") or "")[:120], source="死亡檢討")
        where = ""
        try:
            where = self.remember_lesson(lesson, kind, fname, why, src=f"第{n}次死亡")
        except Exception as e:  # noqa: BLE001
            print(f"   記教訓失敗：{e}")
        if fix:
            bad = self.rule_audit([(f"第{n}次死後改善", None, fix)], "死後改善")
            if bad:
                hi, reason = bad[0]
                self._audit_log([f"- {datetime.now():%m/%d %H:%M}（死後改善退件）「{fix}」（第{n}次死亡）——抵觸「{hi}」：{reason}"])
                print(f"   ⚖ 死後改善抵觸高階規則，退件：{fix}　← {hi}（{reason}）")
                self.lesson_hint = (getattr(self, "lesson_hint", "") or "") + (
                    f"⚖ 你第 {n} 次死後訂的改善「{fix}」跟【指令等級表】的高階規則抵觸（{hi}：{reason}），**不收**。" + NL)
                fix = ""
            else:
                self.add_improve(fix, n)
        self.death_note = (f"第 {n} 次死亡：死因「{cause}」；錯誤「{mistake}」；"
                           f"下次「{avoid}」；改善「{fix}」")
        self.add_note(f"（死亡檢討 #{n}）死因：{cause}；改善：{fix or avoid}")
        try:
            with open(os.path.join(self.dir, "死亡檢討.md"), "a", encoding="utf-8") as f:
                _life = getattr(self, "_last_life", None)
                f.write(f"## 第 {n} 次 {datetime.now().strftime('%m/%d %H:%M')}"
                        + (f"（這條命活了 {_life:.0f} 秒）" if _life else "") + NL
                        + f"- 死因：{cause}" + NL
                        + f"- 錯誤決定：{mistake}" + NL
                        + f"- 下次避免：{avoid}" + NL
                        + f"- **改善（他自己訂的具體做法）**：{fix or '（沒訂）'}" + NL
                        + f"- 要記住的一句：{lesson or '（他選擇不記）'}" + NL
                        + f"- 他自己分類：{kind or '（沒分）'}"
                        + (f" → 記進{where}" if where else " → 沒記")
                        + (f"（{why}）" if why else "") + NL
                        + "- 死前實際做的事（拿來核對上面的死因是不是亂歸因）：" + NL
                        + self.trace_text(40.0, end=getattr(self, "last_death", None)) + NL + NL)
        except OSError as e:
            print(f"   寫死亡檢討失敗：{e}")
        self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 你死了（第 {n} 次）。"
                            f"檢討：{self.death_note}")
        print(f"   💀 檢討：{self.death_note}")
        self.log({"時間": datetime.now().strftime("%H:%M:%S"), "模式": "死亡檢討", "次數": n,
                  "死因": cause, "錯誤": mistake, "避免": avoid, "改善": fix,
                  "記憶": lesson, "分類": kind, "記到": where})

    # ---------------- 關鍵動作：加分項的「前 → 當下 → 後」
    # 2026-09-17 使用者：「他需要記錄關鍵動作（開始遊戲等加分項）他前後做了什麼」。
    # 成功進場 +30、Check In +100 這種事一天只發生幾次，發生的當下他正忙著看畫面，
    # 事後只留下一行「+30 成功進場」，**不知道自己是怎麼弄進去的、進去之後又做了什麼**。
    # 現在加分達到門檻就自動開一筆紀錄：前幾輪做了什麼（短期記憶）、當下那一輪、
    # 之後幾輪又做了什麼，寫進 記憶/關鍵動作.jsonl 與 關鍵動作.md，並每輪回灌摘要。
    KEY_MIN = 20                      # 幾分以上算關鍵動作（成功進場 30、報到 100、治好 600…）
    KEY_AFTER = 3                     # 事後再追幾輪
    KEY_FILE = "關鍵動作.jsonl"
    KEY_MD = "關鍵動作.md"
    KEY_SHOT_DIR = "關鍵動作"

    def key_min(self):
        return int(getattr(self.args, "key_move_min", self.KEY_MIN) or self.KEY_MIN)

    def load_key_moves(self):
        self.key_moves, self._key_pending = [], []
        try:
            with open(os.path.join(self.dir, self.KEY_FILE), encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            self.key_moves.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
        except OSError:
            pass
        self.key_moves = self.key_moves[-50:]
        if self.key_moves:
            print(f"[關鍵動作] 他過去記下 {len(self.key_moves)} 次關鍵動作："
                  + "、".join(f"{r.get('項目')}×1" for r in self.key_moves[-5:]))

    def key_move_start(self, kind, n, why=""):
        """加分達到門檻 → 開一筆關鍵動作紀錄，先把「前幾輪做了什麼」封存起來。"""
        if int(n) < self.key_min():
            return
        rows = list(getattr(self, "short_mem", []))
        rec = {"時間": datetime.now().strftime("%m/%d %H:%M:%S"),
               "輪": self.stat.get("輪數", 0), "項目": str(kind), "加分": int(n),
               "區": getattr(self, "cur_zone", "") or "",
               "流程": str(getattr(self, "flow", "")),
               "任務": getattr(self, "cur_task", ""),
               "為什麼給分": str(why)[:80],
               "前": [f"{len(rows) - i} 輪前：看到「{r.get('看到', '')}」→ 想「{r.get('想', '')}」"
                      f"→ 做了「{r.get('做了', '')}」→ {r.get('結果', '')}"
                      for i, r in enumerate(rows)],
               "當下": "", "後": [], "畫面": ""}
        im = getattr(self, "_frame_im", None)
        if im is not None:
            try:
                d = os.path.join(self.dir, self.KEY_SHOT_DIR)
                os.makedirs(d, exist_ok=True)
                fn = (datetime.now().strftime("%m%d_%H%M%S_")
                      + "".join(ch for ch in str(kind) if ch.isalnum())[:12] + ".jpg")
                im.save(os.path.join(d, fn), "JPEG", quality=70)
                rec["畫面"] = f"{self.KEY_SHOT_DIR}/{fn}"
            except Exception:  # noqa: BLE001
                pass
        self._key_pending = (getattr(self, "_key_pending", []) + [rec])[-5:]
        print(f"   🔑 關鍵動作開始記錄：{kind} +{n}（前 {len(rec['前'])} 輪已封存，"
              f"再追 {self.KEY_AFTER} 輪看你之後做了什麼）")

    def key_moves_tick(self, seen, did, eff):
        """每輪結束呼叫：把當下與之後幾輪補進還沒收尾的關鍵動作。"""
        line = (f"看到「{str(seen or '')[:40]}」→ 做了「{str(did or '')[:60]}」"
                f"→ {str(eff or '')[:30]}")
        for rec in list(getattr(self, "_key_pending", [])):
            if not rec.get("當下"):
                rec["當下"] = line
                continue
            rec["後"].append(line)
            if len(rec["後"]) >= self.KEY_AFTER:
                self._key_pending.remove(rec)
                self.key_move_finish(rec)

    def key_move_finish(self, rec):
        self.key_moves = (getattr(self, "key_moves", []) + [rec])[-50:]
        try:
            with open(os.path.join(self.dir, self.KEY_FILE), "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + NL)
            with open(os.path.join(self.dir, self.KEY_MD), "a", encoding="utf-8") as f:
                f.write(f"## {rec['時間']}　{rec['項目']} +{rec['加分']}"
                        f"（第 {rec['輪']} 輪，在「{rec['區'] or '沒標區'}」）" + NL
                        + (f"- 畫面：`{rec['畫面']}`" + NL if rec.get("畫面") else "")
                        + "- **之前做了什麼**：" + NL
                        + (NL.join(f"  - {x}" for x in rec["前"]) or "  -（沒有紀錄）") + NL
                        + f"- **當下那一輪**：{rec.get('當下') or '（沒抓到）'}" + NL
                        + "- **之後做了什麼**：" + NL
                        + (NL.join(f"  - {x}" for x in rec["後"]) or "  -（沒有紀錄）") + NL + NL)
        except OSError as e:
            print(f"   寫關鍵動作失敗：{e}")
        print(f"   🔑✅ 關鍵動作記完：{rec['項目']} +{rec['加分']}（前 {len(rec['前'])} 輪、"
              f"後 {len(rec['後'])} 輪都留下來了，寫進 {self.KEY_MD}）")
        self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] "
                            f"關鍵動作已記錄：{rec['項目']} +{rec['加分']}，"
                            f"當下你做的是「{(rec.get('當下') or '')[:40]}」")

    def key_moves_text(self, n=2, before=3):
        """回灌摘要：最近幾次關鍵動作的前後經過——這是他**自己做成過**的路，照著再做一次。"""
        rows = list(getattr(self, "key_moves", []))[-n:]
        pend = list(getattr(self, "_key_pending", []))
        if not rows and not pend:
            return ""
        out = ["【你的關鍵動作（你自己做成過的事，前後經過都留著）】"]
        for r in rows:
            out.append(f"　★ {r['時間']}　**{r['項目']} +{r['加分']}**"
                       f"（在「{r.get('區') or '沒標區'}」）")
            for x in r.get("前", [])[-before:]:
                out.append(f"　　之前：{x}")
            if r.get("當下"):
                out.append(f"　　**當下：{r['當下']}**")
            for x in r.get("後", [])[:2]:
                out.append(f"　　之後：{x}")
        for r in pend:
            out.append(f"　⏳ 正在記錄中：{r['項目']} +{r['加分']}"
                       f"（還要追 {max(0, self.KEY_AFTER - len(r.get('後', [])))} 輪，"
                       f"這幾輪你做的事會被留下來當成功做法的一部分）")
        out.append("　→ 要再做成同一件事，**先照這條路走**，不要每次都從頭亂試；"
                   "完整紀錄在 " + self.KEY_MD + "。")
        return NL.join(out)

    # ---------------- 標點：在自己的地圖上釘一個「看得見的特徵」
    # 2026-09-18 使用者：「給他新增留下標點的技能——去找地圖中比較有特色的東西，
    # 讓他更好判斷腦內地圖跟實際位置」。這是基礎知識，不是這隻遊戲的攻略。
    # 跟既有的東西分工：
    #   拍地標 = 存「這個地方長什麼樣」的照片＋畫面簽名（比對用）
    #   記站位 = 存「我站在這裡、朝這個方向」（走回來用）
    #   標點   = 在地圖上釘一個**他看到的特徵物**（可以在遠處），隨時回灌相對方位與距離，
    #            他一抬頭就能對照「我以為它在左前方 4 步，實際看到的是不是？」
    POINT_FILE = "標點.json"
    POINT_SHOT_DIR = "標點"

    def load_points(self):
        self.points = {}
        try:
            with open(os.path.join(self.dir, self.POINT_FILE), encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                self.points = d
        except (OSError, json.JSONDecodeError, TypeError):
            pass
        if self.points:
            print(f"[標點] 他釘過 {len(self.points)} 個標點：" + "、".join(list(self.points)[:8]))

    def save_points(self):
        try:
            with open(os.path.join(self.dir, self.POINT_FILE), "w", encoding="utf-8") as f:
                json.dump(self.points, f, ensure_ascii=False, indent=1)
        except OSError:
            pass

    def point_xy(self, ahead=0.0, deg=0.0):
        """從現在的位置往「正前方偏 deg 度、ahead 步」推一個座標出來。"""
        import math
        yaw = (getattr(self, "yaw", 0.0) + float(deg)) % 360
        r = math.radians(yaw)
        return (round(self.pos[0] + math.sin(r) * float(ahead), 2),
                round(self.pos[1] + math.cos(r) * float(ahead), 2))

    def add_point(self, a):
        """{"t":"標點","name":"紅色招牌","樣子":"牆上紅底白字","前":3,"角度":-20}

        前＝它在你前方幾步（0 或不寫＝就在你腳下）；角度＝正前方偏幾度（負左正右）。
        """
        nm = str(a.get("name") or a.get("名") or "").strip()[:16]
        if not nm:
            print('   ⚠ 標點要寫 name：{"t":"標點","name":"紅色招牌","樣子":"牆上紅底白字","前":3}')
            return
        look = str(a.get("樣子") or a.get("特徵") or a.get("desc") or "").strip()[:60]
        try:
            ahead = float(a.get("前", a.get("幾步", 0)) or 0)
        except (TypeError, ValueError):
            ahead = 0.0
        try:
            deg = float(a.get("角度", 0) or 0)
        except (TypeError, ValueError):
            deg = 0.0
        ahead = max(0.0, min(30.0, ahead))
        deg = max(-90.0, min(90.0, deg))
        x, y = self.point_xy(ahead, deg)
        old = self.points.get(nm) or {}
        rec = {"x": x, "y": y, "樣子": look or old.get("樣子", ""),
               "空間": str(getattr(self, "space", "") or ""),
               "區": getattr(self, "cur_zone", "") or "",
               "從哪看的": {"x": round(self.pos[0], 2), "y": round(self.pos[1], 2),
                        "朝向": round(getattr(self, "yaw", 0.0) % 360), "距離": ahead},
               "釘於": old.get("釘於", datetime.now().strftime("%m/%d %H:%M")),
               "更新": datetime.now().strftime("%m/%d %H:%M"),
               "對過幾次": int(old.get("對過幾次", 0))}
        im = getattr(self, "_frame_im", None)
        if im is not None:
            try:
                d = os.path.join(self.dir, self.POINT_SHOT_DIR)
                os.makedirs(d, exist_ok=True)
                safe = "".join(ch for ch in nm if ch not in '\\/:*?"<>|')[:16] or "標點"
                im.convert("RGB").resize((360, int(360 * im.height / im.width))).save(
                    os.path.join(d, safe + ".jpg"), "JPEG", quality=80)
                rec["畫面"] = f"{self.POINT_SHOT_DIR}/{safe}.jpg"
            except Exception:  # noqa: BLE001
                pass
        self.points[nm] = rec
        self.save_points()
        where = "你腳下" if ahead <= 0.01 else f"你前方 {ahead:.0f} 步"
        print(f"   📌 釘下標點「{nm}」在 x{x:+.1f} y{y:+.1f}（{where}"
              + (f"、偏 {deg:+.0f} 度" if deg else "") + f"）{('｜' + look) if look else ''}")
        self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 你釘下標點「{nm}」"
                            f"（{look or '沒寫樣子'}）在 x{x:+.1f} y{y:+.1f}")
        self.note_progress("標點")

    def hit_point(self, a):
        """{"t":"到標點","name":"紅色招牌"}＝我現在就站在它旁邊。

        這是**用眼睛校正推算**：走久了座標會漂，站到自己釘過的標點旁邊說一聲，
        程式就把座標拉回那個點，腦中地圖跟實際位置重新對齊。
        """
        nm = str(a.get("name") or a.get("名") or "").strip()[:16]
        p = self.points.get(nm)
        if not p:
            print(f"   ⚠ 沒有標點「{nm}」（現有：{'、'.join(list(self.points)[:8]) or '一個都沒有'}）")
            return
        import math
        gap = math.hypot(p["x"] - self.pos[0], p["y"] - self.pos[1])
        self.pos = [float(p["x"]), float(p["y"])]
        p["對過幾次"] = int(p.get("對過幾次", 0)) + 1
        p["最後對到"] = datetime.now().strftime("%m/%d %H:%M")
        self.save_points()
        self.fix_count = getattr(self, "fix_count", 0) + 1
        print(f"   🧭 對到標點「{nm}」→ 座標校正（原本以為差了 {gap:.1f} 步）"
              f"，現在 x{p['x']:+.1f} y{p['y']:+.1f}")
        self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 你走到標點「{nm}」，"
                            f"推算座標本來差了 {gap:.1f} 步，已經校正回來")
        if gap > 4.0:
            self.lesson_hint = (getattr(self, "lesson_hint", "") or "") + (
                f"🧭 你剛才對到「{nm}」時，推算座標差了 {gap:.1f} 步——**走一段就要對一次標點**，"
                f"不然腦中的地圖會越走越歪。" + NL)
        self.note_progress("對標點")

    REQUIRED_POINTS = (("可互動的踏板", ("踏板", "板", "pad", "傳送", "電梯", "觸發")),
                       ("牆壁", ("牆", "wall", "壁")))

    def points_missing(self):
        """2026-09-19 使用者：**至少**要釘「可互動的踏板」與「牆壁」這兩種標點。

        踏板是他的入口（站上去才會跳 UI），牆是他最常撞到、最該記住位置的東西；
        這兩種釘起來，他的腦中地圖才有「能用的點」與「不能過的界」。
        """
        pts = getattr(self, "points", None) or {}
        blob = " ".join((k + " " + str(v.get("樣子", ""))) for k, v in pts.items()).lower()
        return [name for name, keys in self.REQUIRED_POINTS
                if not any(k.lower() in blob for k in keys)]

    def points_text(self, n=6):
        """每輪回灌：每個標點現在在你的哪個方向、多遠——拿來跟眼前看到的對照。"""
        pts = getattr(self, "points", None) or {}
        miss = self.points_missing()
        need = (NL + "　⚠ **使用者規定你至少要釘這兩種標點，現在還缺：" + "、".join(miss) + "**"
                + NL + '　　踏板：{"t":"標點","name":"進場踏板","樣子":"地上會發光、站上去會跳面板","前":2}'
                + NL + '　　牆壁：{"t":"標點","name":"大廳北牆","樣子":"擋住去路的灰色牆面","前":1}'
                + NL + "　　（踏板是你的入口，牆是你的界線——這兩種記起來，地圖才有用。）") if miss else ""
        if not pts:
            return (need.lstrip(NL) + NL + '【標點】你還沒釘過任何標點。看到**不會動、好認、遠遠就看得到**的東西'
                    '（招牌、電梯、雕像、顏色特別的牆），就釘一個：'
                    '{"t":"標點","name":"你取的名字","樣子":"長什麼樣","前":3}'
                    '——之後每一輪程式都會告訴你它在你的哪個方向、多遠，'
                    '你就對得出「腦中的地圖」跟「眼前看到的」有沒有差。')
        import math
        yaw = getattr(self, "yaw", 0.0)
        rows = []
        for nm, p in pts.items():
            vx, vy = p["x"] - self.pos[0], p["y"] - self.pos[1]
            dist = math.hypot(vx, vy)
            ang = (math.degrees(math.atan2(vx, vy)) - yaw + 540) % 360 - 180
            a = abs(ang)
            side = ("正前方" if a <= 20 else ("右前方" if ang > 0 else "左前方") if a <= 70 else
                    ("正右邊" if ang > 0 else "正左邊") if a <= 110 else
                    ("右後方" if ang > 0 else "左後方") if a <= 160 else "正後方")
            rows.append((dist, f"　📌 {nm}：{side} {dist:.1f} 步"
                               + (f"（{p.get('樣子')}）" if p.get("樣子") else "")
                               + (f"　對過 {p['對過幾次']} 次" if p.get("對過幾次") else "")))
        rows.sort()
        return ("【你釘的標點】（這是**用推算**算出來的方位，不是現在看到的）" + need + NL
                + NL.join(r[1] for r in rows[:n]) + NL
                + "　→ 抬頭看一眼：**該在那個方向的東西真的在那裡嗎？**"
                + "在，你的地圖就是對的；不在，就是推算漂了——"
                + '走到你認得的那個標點旁邊送 {"t":"到標點","name":"..."} 把座標拉回去。')

    # ---------------- 按鈕簿：把「這顆按鈕在哪、點了有沒有用」用文字記下來
    # 2026-09-18 使用者：「他多次點擊同一個按鈕、發現重複的位置，就可以直接用文字的形式
    # 記錄那個按鈕的位置，下次更好點到」。
    # 所以這裡做兩件事：
    #   1. 自動記帳：每次點擊之後看畫面有沒有變，把「名字→座標→有沒有效」累積起來。
    #   2. 他自己也能寫：{"t":"記按鈕","name":"開始遊戲","x":0.43,"y":0.05,"畫面":"大廳"}
    # 每一輪把這本簿子回灌給他，他就不必每次重新找、重新猜座標。
    BTN_FILE = "按鈕.json"

    def load_buttons_memo(self):
        self.btn_memo = {}
        try:
            with open(os.path.join(self.dir, self.BTN_FILE), encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                self.btn_memo = d
        except (OSError, json.JSONDecodeError, TypeError):
            pass
        if self.btn_memo:
            ok = [k for k, v in self.btn_memo.items() if v.get("成功", 0)]
            print(f"[按鈕簿] 記過 {len(self.btn_memo)} 顆按鈕，其中 {len(ok)} 顆按了真的有反應")

    def save_buttons_memo(self):
        try:
            with open(os.path.join(self.dir, self.BTN_FILE), "w", encoding="utf-8") as f:
                json.dump(self.btn_memo, f, ensure_ascii=False, indent=1)
        except OSError:
            pass

    def note_button(self, name, x, y, ok, where="", diff=None):
        """記一次點擊的結果。名字取不到就不記（沒名字的座標對他沒用）。"""
        nm = str(name or "").strip()[:16]
        if not nm or nm in ("？", "?"):
            return
        try:
            x, y = round(float(x), 3), round(float(y), 3)
        except (TypeError, ValueError):
            return
        b = self.btn_memo.get(nm) or {"成功": 0, "沒反應": 0}
        if ok:
            # 有效的那一次才更新座標——沒反應的座標留著只會害他下次又點同一個空位
            n = int(b.get("成功", 0))
            b["x"] = round((b.get("x", x) * n + x) / (n + 1), 3) if n else x
            b["y"] = round((b.get("y", y) * n + y) / (n + 1), 3) if n else y
            b["成功"] = n + 1
        else:
            b["沒反應"] = int(b.get("沒反應", 0)) + 1
            b.setdefault("x", x)
            b.setdefault("y", y)
            b["最後沒反應的座標"] = [x, y]
        b["在哪個畫面"] = (where or b.get("在哪個畫面") or getattr(self, "cur_zone", "") or "")[:12]
        b["最後"] = datetime.now().strftime("%m/%d %H:%M")
        self.btn_memo[nm] = b
        self.save_buttons_memo()
        if ok and b["成功"] == 1:
            print(f"   🔘 記下按鈕「{nm}」：({b['x']:.2f},{b['y']:.2f}) 點了**有反應**"
                  + (f"（畫面變化 {diff:.0f}）" if diff is not None else ""))
        elif ok and b["成功"] in (3, 5, 10):
            print(f"   🔘 按鈕「{nm}」已經在 ({b['x']:.2f},{b['y']:.2f}) 成功 {b['成功']} 次——可靠了")

    def add_button_memo(self, a):
        """{"t":"記按鈕","name":"開始遊戲","x":0.43,"y":0.05,"畫面":"大廳","效果":"跳出選地圖"}

        x／y 不寫就用你**上一次點的位置**。
        """
        nm = str(a.get("name") or a.get("名") or "").strip()[:16]
        if not nm:
            print('   ⚠ 記按鈕要寫 name：{"t":"記按鈕","name":"開始遊戲","x":0.43,"y":0.05}')
            return
        last = getattr(self, "_last_click_xy", None)
        try:
            x = float(a.get("x", (last or [None])[0]))
            y = float(a.get("y", (last or [None, None])[1]))
        except (TypeError, ValueError):
            print("   ⚠ 記按鈕要有 x／y（或先點一次，我就用你剛才點的位置）")
            return
        b = self.btn_memo.get(nm) or {"成功": 0, "沒反應": 0}
        b.update({"x": round(x, 3), "y": round(y, 3),
                  "在哪個畫面": str(a.get("畫面") or a.get("在") or getattr(self, "cur_zone", ""))[:12],
                  "效果": str(a.get("效果") or a.get("會怎樣") or "")[:40],
                  "他自己寫的": True, "最後": datetime.now().strftime("%m/%d %H:%M")})
        self.btn_memo[nm] = b
        self.save_buttons_memo()
        print(f"   🔘 他自己記下按鈕「{nm}」：({x:.2f},{y:.2f})"
              + (f"　{b['效果']}" if b.get("效果") else ""))
        self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 你把按鈕「{nm}」的位置"
                            f"({x:.2f},{y:.2f}) 記下來了")
        self.note_progress("記按鈕")

    def forget_button(self, a):
        nm = str(a.get("name") or a.get("名") or "").strip()[:16]
        if nm in self.btn_memo:
            del self.btn_memo[nm]
            self.save_buttons_memo()
            print(f"   🗑 忘掉按鈕「{nm}」")

    def btn_memo_text(self, n=8):
        rows = sorted((getattr(self, "btn_memo", None) or {}).items(),
                      key=lambda kv: -int(kv[1].get("成功", 0)))[:n]
        if not rows:
            return ('【按鈕簿】還是空的。**同一顆按鈕點到第二次就該把它記起來**：'
                    '{"t":"記按鈕","name":"開始遊戲","x":0.43,"y":0.05,"畫面":"大廳"}'
                    '（x／y 不寫就用你剛才點的位置）——下次直接照座標點，不用再找、再猜。')
        out = ["【按鈕簿：你記過的按鈕在哪】（點過有反應的座標，直接拿來用）"]
        for nm, b in rows:
            s, f = int(b.get("成功", 0)), int(b.get("沒反應", 0))
            mark = "✅" if s >= 3 else ("○" if s else ("✍" if b.get("他自己寫的") else "⚠"))
            tally = (f"　有反應 {s} 次" + (f"／沒反應 {f} 次" if f else "")) if (s or f) else "　（你自己寫的，還沒驗證過）"
            out.append(f"　{mark} {nm}：({b.get('x', 0):.2f},{b.get('y', 0):.2f})"
                       + (f"　在「{b['在哪個畫面']}」" if b.get("在哪個畫面") else "")
                       + (f"　{b['效果']}" if b.get("效果") else "")
                       + tally)
        out.append("　→ 要按其中一顆就直接 "
                   '{"t":"click","x":座標,"y":座標,"g":"按鈕名"}；'
                   "⚠ 的那幾顆代表你點過很多次都沒反應——**換個做法，不要再點同一個位置**。")
        return NL.join(out)

    # ---------------- 大動作：主任務 → 大動作序列 → 細步驟
    # 2026-09-20 使用者定的三層分工：
    #   慢想：看全部，**排出大動作序列**（主任務：做一道菜 → 挑菜／洗菜／切菜／下鍋炒）
    #   快思：看基礎操作＋**那一份大動作文檔**＋現況，把大動作切成**這個情況的細步驟**
    #         （洗菜 → 拿菜、放到水裡、手上下搓…）
    #   反射：只看基礎操作＋**現在這一步**，照著做；情況對不上就喊「換快思」
    # 「進場（開始遊戲）」是必要的大動作——細步驟記下來之後就不用每次重想。
    MACRO_FILE = "大動作.json"
    MACRO_DIR = "大動作"

    def load_macros(self):
        self.macro = {"主任務": "", "序列": [], "第幾個": 0, "第幾步": 0, "步驟": {}}
        try:
            with open(os.path.join(self.dir, self.MACRO_FILE), encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                self.macro.update(d)
        except (OSError, json.JSONDecodeError, TypeError):
            pass
        seq = self.macro.get("序列") or []
        if seq:
            print(f"[大動作] 主任務「{self.macro.get('主任務', '')}」"
                  f"共 {len(seq)} 個大動作，現在第 {self.macro.get('第幾個', 0) + 1} 個："
                  f"{seq[min(self.macro.get('第幾個', 0), len(seq) - 1)]}")
        else:
            print("[大動作] 還沒排過大動作序列（慢想時會請他排）")

    def save_macros(self):
        try:
            with open(os.path.join(self.dir, self.MACRO_FILE), "w", encoding="utf-8") as f:
                json.dump(self.macro, f, ensure_ascii=False, indent=1)
        except OSError:
            pass

    def macro_name(self):
        seq = self.macro.get("序列") or []
        i = int(self.macro.get("第幾個", 0) or 0)
        return seq[i] if 0 <= i < len(seq) else ""

    def macro_steps(self):
        return list((self.macro.get("步驟") or {}).get(self.macro_name(), []))

    def macro_step_idx(self):
        return max(0, int(self.macro.get("第幾步", 0) or 0))

    def macro_doc_path(self, name):
        d = project_dir(self.game, self.MACRO_DIR)
        os.makedirs(d, exist_ok=True)
        safe = "".join(ch for ch in str(name or "") if ch not in '\\/:*?"<>|')[:20] or "未命名"
        return os.path.join(d, safe + ".md")

    def macro_doc(self, name=None):
        """那一份大動作的文檔（他自己寫的：怎麼做、怎麼算成功、踩過什麼坑）。"""
        p = self.macro_doc_path(name or self.macro_name())
        try:
            return open(p, encoding="utf-8").read()[:1500]
        except OSError:
            return ""

    def set_macros(self, a):
        """{"t":"排大動作","主任務":"打完一場","序列":["進場","撐過第一波","回大廳"]}（慢想用）"""
        seq = a.get("序列") or a.get("大動作") or []
        if isinstance(seq, str):
            seq = [x.strip() for x in seq.replace("、", ",").split(",") if x.strip()]
        seq = [str(x)[:12] for x in seq][:8]
        if not seq:
            print('   ⚠ 排大動作要寫 序列：{"t":"排大動作","主任務":"...","序列":["進場","..."]}')
            return
        self.macro["主任務"] = str(a.get("主任務") or a.get("任務") or self.macro.get("主任務") or "")[:24]
        self.macro["序列"] = seq
        self.macro["第幾個"] = 0
        self.macro["第幾步"] = 0
        self.macro["起輪"] = int(self.stat.get("輪數", 0))
        self.macro["罰過"] = 0
        self.save_macros()
        print(f"   🧩 排好大動作：主任務「{self.macro['主任務']}」→ " + " → ".join(seq))
        self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 你排好了大動作序列："
                            + " → ".join(seq))

    def write_macro_steps(self, a):
        """{"t":"寫步驟","大動作":"進場","步驟":["點開始遊戲","等地圖面板","點開始出發","站上踏板等傳送"]}

        快思用：把一個大動作切成**這個情況下**真的做得出來的細步驟。
        寫進 大動作.json，同時存成 專案/<遊戲>/大動作/<名字>.md 讓使用者也看得到。
        """
        nm = str(a.get("大動作") or a.get("name") or self.macro_name() or "").strip()[:12]
        steps = a.get("步驟") or a.get("steps") or []
        if isinstance(steps, str):
            steps = [x.strip() for x in steps.replace("、", ",").split(",") if x.strip()]
        steps = [str(x)[:40] for x in steps][:10]
        if not nm or not steps:
            print('   ⚠ 寫步驟要有 大動作 與 步驟：{"t":"寫步驟","大動作":"進場","步驟":["...","..."]}')
            return
        self.macro.setdefault("步驟", {})[nm] = steps
        aim = str(a.get("目的") or a.get("為什麼") or "").strip()[:40]
        if aim:
            self.macro.setdefault("目的", {})[nm] = aim
        if nm == self.macro_name():
            self.macro["第幾步"] = 0
        self.save_macros()
        try:
            with open(self.macro_doc_path(nm), "w", encoding="utf-8") as f:
                f.write(f"# 大動作：{nm}（他自己切的細步驟）" + NL
                        + f"（{datetime.now().strftime('%m/%d %H:%M')} 由快思寫入）" + NL * 2
                        + NL.join(f"{i + 1}. {s}" for i, s in enumerate(steps)) + NL * 2
                        + "（怎麼算做完、踩過什麼坑，他自己往下補。）" + NL)
        except OSError:
            pass
        _sim = self.macro_similar(nm)
        if _sim:
            print(f"   🧩 提醒：「{nm}」跟 "
                  + "、".join(f"「{k}」({r:.0%})" for r, k in _sim)
                  + " 很像——同一件事就併成一個名字")
        print(f"   🧩 「{nm}」切成 {len(steps)} 步：" + " → ".join(steps))
        if nm == self.macro_name():
            self.macro_budget_start("重新切步驟")
        self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 你把大動作「{nm}」"
                            f"切成 {len(steps)} 步：" + " → ".join(steps))
        self.note_progress("寫步驟")

    def describe_tools(self):
        """這一輪他用了哪些工具（已經被移出 actions 的那些），寫成一句給【上一步】用。"""
        out = []
        for a in getattr(self, "_asked_full", []) or []:
            t = str(a.get("t") or "")
            if not t or t in ("key", "hold", "click", "look", "aim", "move", "scroll", "wait", "type"):
                continue
            extra = a.get("字") or a.get("g") or a.get("名") or a.get("大動作") or ""
            out.append(f"用工具「{t}」" + (f"（{str(extra)[:16]}）" if extra else ""))
        return "、".join(out)

    def macro_step_done(self, a=None):
        """{"t":"這步做完"}：往前推一步；最後一步做完就換下一個大動作。"""
        steps = self.macro_steps()
        i = self.macro_step_idx()
        if steps and i < len(steps) - 1:
            self.macro["第幾步"] = i + 1
            self.save_macros()
            print(f"   ✅ 「{self.macro_name()}」第 {i + 1} 步做完 → 第 {i + 2} 步：{steps[i + 1]}")
            self.mark_step(f"第 {i + 1} 步做完")
        else:
            self.macro_done()
        self.note_progress("推進步驟")

    def macro_done(self, a=None):
        """{"t":"大動作完成"}：這個大動作做完了，換序列裡的下一個。"""
        seq = self.macro.get("序列") or []
        i = int(self.macro.get("第幾個", 0) or 0)
        nm = self.macro_name()
        _bud = self.macro_budget()
        if i < len(seq) - 1:
            self.macro["第幾個"] = i + 1
            self.macro["第幾步"] = 0
            self.save_macros()
            print(f"   🧩✅ 大動作「{nm}」完成 → 換「{self.macro_name()}」")
            self.mark_step(f"大動作「{nm}」完成")
            self.macro_budget_close(nm, _bud)
            self.macro_budget_start("換了新的大動作")
            self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 大動作「{nm}」完成，"
                                f"接下來是「{self.macro_name()}」")
        else:
            print(f"   🧩✅ 大動作「{nm}」完成——序列跑完了，下次慢想再排新的")
            self.macro_budget_close(nm, _bud)
            self._slow_due = True
        self.note_progress("大動作完成")

    # ---------------- 步數預算
    # 2026-09-21 使用者：**同一個大動作，他拆成幾步就要在幾輪之內做完，不然就扣分。**
    # 目的是逼他把步驟壓縮：切得太細（八步走完一段路）或切得不對（照著做沒用），
    # 都會在輪數上現形。步驟本身就是他對「這件事要花多久」的承諾。
    # 2026-09-22 使用者：**一個步驟最多兩輪**（原本是一步一輪）。
    # 原因：一步裡常常要先試一次才會中（那顆按鈕成功率不到五成），一比一是在罰運氣。
    ROUNDS_PER_STEP = 2

    def macro_budget(self):
        """這個大動作的預算輪數＝步數 × 每步 2 輪。沒切步驟就沒有預算。"""
        return len(self.macro_steps()) * self.ROUNDS_PER_STEP

    def macro_used(self):
        """這個大動作已經花掉幾輪。"""
        st = self.macro.get("起輪")
        if st is None:
            return 0
        return max(0, int(self.stat.get("輪數", 0)) - int(st))

    def macro_budget_start(self, why=""):
        """重新開始計輪：寫了新步驟、或換到新的大動作時呼叫。"""
        self.macro["起輪"] = int(self.stat.get("輪數", 0))
        self.macro["罰過"] = 0
        self.save_macros()
        if why and self.macro_budget():
            print(f"   ⏳ 「{self.macro_name()}」預算 {self.macro_budget()} 輪（{why}）")

    def macro_budget_tick(self):
        """每輪檢查：超過自己訂的步數就扣分，並且要求他重切更少步。"""
        bud = self.macro_budget()
        if not bud:
            self.budget_hint = ""
            return
        # 2026-09-29 使用者：**沒有直接完成小動作要處罰**（−20）。
        # 大動作整體超支原本只扣 3 分；這裡細到每一個小步驟：同一步做了兩輪還沒送「這步做完」，
        # 第三輪起每輪扣一次。換步、換大動作、重寫步驟都重新計。
        try:
            key = (self.macro_name(), self.macro_step_idx(), len(self.macro_steps()),
                   self.macro.get("起輪"))
            now = int(self.stat.get("輪數", 0))
            if key != getattr(self, "_step_key", None):
                self._step_key, self._step_start = key, now
            step_used = now - self._step_start
            if step_used > self.ROUNDS_PER_STEP:
                steps = self.macro_steps()
                i = self.macro_step_idx()
                cur = steps[i] if i < len(steps) else ""
                self.punish("小步驟沒直接做完",
                            f"「{self.macro_name()}」第 {i + 1} 步「{str(cur)[:30]}」已經做了 {step_used} 輪"
                            f"（一步最多 {self.ROUNDS_PER_STEP} 輪）。這一輪就把它做掉送"
                            '{"t":"這步做完"}，做不到就用工具重切成做得到的小步驟。')
        except Exception as e:  # noqa: BLE001
            print(f"   小步驟計輪失敗：{e}")
        if self.macro.get("起輪") is None:
            # 開機後接手一個已經切好步驟的大動作：從現在開始算，不追溯上次開機
            self.macro["起輪"] = int(self.stat.get("輪數", 0))
            self.macro["罰過"] = 0
            self.save_macros()
            print(f"   ⏳ 接手「{self.macro_name()}」：預算 {bud} 輪，從這一輪開始算")
        used = self.macro_used()
        over = used - bud
        if over <= 0:
            self.budget_hint = (f"⏳ **步數預算**：你把「{self.macro_name()}」切成 "
                                f"{len(self.macro_steps())} 步，**一步最多兩輪**，"
                                f"所以預算是 {bud} 輪。已用 {used} 輪，**還剩 {bud - used} 輪**。"
                                + '做完一步就馬上送 {"t":"這步做完"}，不要拖。' + NL)
            return
        self.budget_hint = (f"⛔ **超支了**：「{self.macro_name()}」你自己切成 "
                            f"{len(self.macro_steps())} 步（一步兩輪＝預算 {bud} 輪），"
                            f"卻已經花了 {used} 輪（超支 {over} 輪）。"
                            f"這代表**步驟切錯了或你沒照著做**。現在二選一："
                            + '馬上把這一步做掉送 {"t":"這步做完"}；或送 '
                            + '{"t":"寫步驟","大動作":"' + self.macro_name() + '","步驟":[...]} '
                            + "**用更少的步驟重切**（一步就是一輪做得完的事）。" + NL)
        # 第一次超支就扣，之後每多拖 3 輪再扣一次（不要每輪洗版）
        n = int(self.macro.get("罰過", 0) or 0)
        if over == 1 or (over > 1 and over - n >= 3):
            self.macro["罰過"] = over
            self.save_macros()
            try:
                self.punish("步數超支",
                            f"「{self.macro_name()}」你切了 {len(self.macro_steps())} 步"
                            f"（一步兩輪＝預算 {bud} 輪），實際花了 {used} 輪。"
                            f"步驟要壓縮：一步＝兩輪之內做得完的事。")
            except Exception as e:  # noqa: BLE001
                print(f"   步數超支扣分失敗：{e}")
            # 2026-09-22 使用者：**一超支就檢討**（不要只扣分、不要排隊、也不受兩分鐘冷卻限制）
            try:
                self._last_stuck_review = 0          # 清掉冷卻，這一次一定要檢討
                self.stuck_review(f"「{self.macro_name()}」你切了 {len(self.macro_steps())} 步"
                                  f"（一步兩輪＝預算 {bud} 輪），已經花了 {used} 輪"
                                  f"（超支 {over} 輪）")
            except Exception as e:  # noqa: BLE001
                print(f"   超支檢討失敗：{e}")

    def macro_budget_close(self, nm, bud):
        """大動作做完時結算：在預算內完成就加分。"""
        used = self.macro_used()
        if not bud:
            return
        if used <= bud:
            print(f"   ⏳ 「{nm}」做完了：你說 {bud} 步，實際 {used} 輪")
        else:
            print(f"   ⏳ 「{nm}」做完了，但花了 {used} 輪（預算 {bud} 輪）")

    # ---------------- 基礎確認（2026-09-21 使用者）
    # 「場景切換就要先做基礎確認：視角、游標狀態……等，當作新場景的第一步。」
    # 為什麼：實測他點同一顆 UI 按鈕，第三人稱時生效率 58%、第一人稱（畫面有準心）只有 35%——
    # 因為第一人稱時 Roblox 把游標鎖在畫面中央，點擊打到的是 3D 世界、不是 UI。
    # 所以每次場景變了（大廳↔關卡↔面板），先花一輪把「我現在是什麼狀態」確認清楚，
    # 後面每一次點擊都會變準。結果另外存一份 基礎確認.jsonl，不跟步驟日誌混在一起。
    CHECK_FILE = "基礎確認.jsonl"

    def scene_sig(self):
        """這一刻的場景指紋：畫面上的字（集合）＋他自己標的區。"""
        try:
            return self.text_set(), str(getattr(self, "cur_zone", "") or "")
        except Exception:  # noqa: BLE001
            return set(), ""

    def scene_tick(self):
        """每輪比一次：場景換了就要求他先做基礎確認。"""
        now, zone = self.scene_sig()
        prev, pzone = getattr(self, "_scene_prev", (set(), ""))
        self._scene_prev = (now, zone)
        if not now and not prev:
            return
        inter = len(now & prev)
        union = len(now | prev) or 1
        same = inter / union
        changed = (zone != pzone and zone and pzone) or (same < 0.34 and len(prev) >= 3)
        if changed and not getattr(self, "_check_done_at", None) == zone:
            self._need_check = True
            why = "你標的區從「%s」變成「%s」" % (pzone, zone) if zone != pzone else "畫面上的字整批換掉了"
            self._check_why = why
            print(f"   🧭 場景切換（{why}）→ 這一輪先做基礎確認")

    def basic_check_text(self):
        """面板用：場景剛換，先確認這幾件事（反射輪也看得到）。"""
        if not getattr(self, "_need_check", False):
            return ""
        return ("🧭 **場景換了**（" + str(getattr(self, "_check_why", "")) + "）。"
                + "要不要先確認一下自己現在的狀態，你自己決定；要確認就送：" + NL
                + '{"t":"基礎確認","視角":"水平/抬頭N度/低頭N度","人稱":"第三/第一",'
                  '"游標":"解鎖/被鎖住","區":"你現在在哪","看到":"畫面上最重要的字或東西"}' + NL
                + "　**人稱怎麼看**：看得到自己的背影＝第三人稱（游標解鎖，點得到 UI）；"
                + "畫面中央有準心＝第一人稱。**有些遊戲那個十字就是滑鼠**：先把十字對到按鈕上按一次左鍵，"
                + "有反應就這樣點；沒反應才是真的鎖住，再滾輪拉遠。" + NL
                + "　實測：第三人稱點按鈕生效率 58%，第一人稱只有 35%。" + NL)

    def basic_check(self, a):
        """{"t":"基礎確認",...}：把現在的狀態寫進 基礎確認.jsonl，並解除提醒。"""
        rec = {"時間": datetime.now().strftime("%m/%d %H:%M:%S"),
               "輪": self.stat.get("輪數", 0),
               "為什麼": str(getattr(self, "_check_why", ""))[:30],
               "視角": str(a.get("視角") or "")[:16],
               "人稱": str(a.get("人稱") or "")[:8],
               "游標": str(a.get("游標") or "")[:8],
               "區": str(a.get("區") or getattr(self, "cur_zone", ""))[:12],
               "看到": str(a.get("看到") or "")[:40],
               "水平儀": f"{getattr(self, 'pitch_deg', 0):.0f}度/朝{getattr(self, 'yaw', 0) % 360:.0f}"}
        try:
            with open(os.path.join(self.dir, self.CHECK_FILE), "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + NL)
        except OSError:
            pass
        self._need_check = False
        self._check_done_at = rec["區"]
        self.last_check = rec
        print(f"   🧭 基礎確認：{rec['人稱']}／游標{rec['游標']}／視角{rec['視角']}　"
              f"在「{rec['區']}」看到「{rec['看到']}」")
        self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 基礎確認："
                            f"{rec['人稱']}、游標{rec['游標']}、{rec['視角']}")
        if "第一" in rec["人稱"] or "鎖" in rec["游標"]:
            self.check_hint = ("⚠ 你剛才自己確認了**游標是被鎖住的**（第一人稱）。"
                               "這種狀態點 UI 會沒反應——**先滾輪拉遠切第三人稱**，"
                               "看到自己的背影再點。" + NL)
        else:
            self.check_hint = ""
        self.note_progress("基礎確認")

    def check_text(self):
        """面板用：上一次基礎確認的結果（讓他記得自己現在是什麼狀態）。"""
        r = getattr(self, "last_check", None)
        if not r:
            return ""
        return (f"【上次基礎確認 {r['時間'][-8:]}】{r['人稱']}／游標{r['游標']}／視角{r['視角']}"
                f"／在「{r['區']}」")

    # ---------------- 大動作相似度（2026-09-21 使用者：要他自己去併）
    def macro_similar(self, nm, n=3, floor=0.55):
        """找出跟這個名字很像的既有大動作。270 個大動作裡有一大半是同一件事換個講法。"""
        import difflib
        base = zh_text.norm(str(nm or ""))
        out = []
        for k in (self.macro.get("步驟") or {}):
            if k == nm:
                continue
            other = zh_text.norm(k)
            r = difflib.SequenceMatcher(None, base, other).ratio()
            # 名字長度差很多時，單看 ratio 會漏掉「進場」vs「進場：解鎖滑鼠後點開始遊」
            # 這種「一個是另一個的開頭／子字串」的情況——那其實是同一件事換個講法。
            a, b = (base, other) if len(base) <= len(other) else (other, base)
            if a and a in b:
                r = max(r, 0.9)
            elif a and len(a) >= 2 and b.startswith(a[:2]):
                r = max(r, 0.6)
            if r >= floor:
                out.append((round(r, 2), k))
        return sorted(out, reverse=True)[:n]

    def macro_dup_text(self):
        """面板用：現在這個大動作跟哪些舊的很像 → 要他自己決定併不併。"""
        nm = self.macro_name()
        if not nm:
            return ""
        sim = self.macro_similar(nm)
        if not sim:
            return ""
        rows = "、".join(f"「{k}」({r:.0%}像)" for r, k in sim)
        return ("🧩 **你有好幾個大動作在講同一件事**：現在的「" + nm + "」很像 " + rows + "。"
                + "名字一換，步驟和計輪就整個重來——**同一件事只留一個名字**。"
                + '要併就送 {"t":"併大動作","保留":"' + nm + '","併掉":["'
                + sim[0][1] + '"]}（步驟會合併到你保留的那一個）。' + NL)

    def merge_macros(self, a):
        """{"t":"併大動作","保留":"進場","併掉":["進場：解鎖滑鼠","按下開始遊戲"]}"""
        keep = str(a.get("保留") or a.get("keep") or self.macro_name() or "").strip()[:12]
        drop = a.get("併掉") or a.get("drop") or []
        if isinstance(drop, str):
            drop = [x.strip() for x in drop.replace("、", ",").split(",") if x.strip()]
        drop = [str(x).strip()[:12] for x in drop if str(x).strip() and str(x).strip() != keep]
        steps = self.macro.setdefault("步驟", {})
        if not keep or not drop:
            print('   ⚠ 併大動作要寫 保留 與 併掉：{"t":"併大動作","保留":"進場","併掉":["..."]}')
            return
        merged = list(steps.get(keep) or [])
        gone = []
        for d in drop:
            if d in steps:
                for st in steps[d]:
                    if st not in merged:
                        merged.append(st)
                del steps[d]
                gone.append(d)
        if not gone:
            print(f"   ⚠ 「{'、'.join(drop)}」不在大動作簿裡，沒東西可以併")
            return
        steps[keep] = merged[:10]
        seq = [keep if x in gone else x for x in (self.macro.get("序列") or [])]
        out, seen = [], set()
        for x in seq:                      # 同一個名字在序列裡只留一次
            if x not in seen:
                out.append(x)
                seen.add(x)
        self.macro["序列"] = out
        self.save_macros()
        print(f"   🧩 併好了：「{'」「'.join(gone)}」→ 併進「{keep}」"
              f"（現在 {len(steps[keep])} 步）")
        self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 你把 {len(gone)} 個"
                            f"重複的大動作併進「{keep}」")
        self.note_progress("併大動作")

    def last_plan_text(self):
        """慢想用：上一次排的計畫是什麼、花了幾輪、做到第幾步——沒有這一段它只會重排。"""
        nm = self.macro_name()
        if not nm:
            return "【上一次慢想排的計畫】（還沒有）"
        steps, si = self.macro_steps(), self.macro_step_idx()
        used = self.macro_used()
        n_fail = int((self.macro.get("失敗次數") or {}).get(nm, 0) or 0)
        rows = [f"【上一次慢想排的計畫】大動作「{nm}」，切了 {len(steps)} 步，"
                f"已經花了 {used} 輪，做到第 {si + 1} 步"
                + (f"；這個名字的計畫已經重排過 {n_fail} 次還沒走完" if n_fail else "")]
        for k, st in enumerate(steps):
            rows.append(f"　{k + 1}. {st}" + ("　← 卡在這一步" if k == si else ""))
        sim = self.macro_similar(nm, 3)
        if sim:
            rows.append("　（跟它講同一件事的舊名字：" + "、".join(f"「{k}」" for _, k in sim) + "——都是同一件事，不要再取新名字）")
        return NL.join(rows)

    def macro_text(self):
        """面板用：主任務 → 大動作序列 → 現在這一步（反射輪也吃這一段）。"""
        seq = self.macro.get("序列") or []
        if not seq:
            return ('【大動作】你還沒排過大動作序列。慢想的時候排一次：'
                    '{"t":"排大動作","主任務":"你現在要達成什麼","序列":["進場","...","..."]}'
                    '——**進場（開始遊戲）一定要是其中一個**。')
        i = int(self.macro.get("第幾個", 0) or 0)
        nm = self.macro_name()
        steps, si = self.macro_steps(), self.macro_step_idx()
        rows = [f"【大動作】主任務：{self.macro.get('主任務') or '（沒寫）'}",
                "　（2026-09-21 使用者：**大動作屬於「遊戲記憶」**——它只對這個遊戲成立，"
                "換一個遊戲就不能用。所以大動作寫進 記憶/；"
                "像基礎確認、視角控制、怎麼分辨 UI 這種**跨遊戲都成立的才是「基礎技藝」**。)",
                "　序列：" + " → ".join(f"**{s}**" if k == i else s for k, s in enumerate(seq)),
                f"　現在這個大動作：**{nm}**"]
        if steps:
            rows.append("　細步驟：")
            for k, s in enumerate(steps):
                mark = "**→ 現在這一步**" if k == si else ""
                rows.append(f"　　{k + 1}. {s} {mark}")
            _b = len(steps)
            rows.append(f"　⏳ **步數預算：{_b} 步 × 每步 2 輪＝{_b * self.ROUNDS_PER_STEP} 輪**"
                        f"（已用 {self.macro_used()} 輪）"
                        f"——你自己切幾步，就要在幾輪內做完，超過會扣分。"
                        f"做不完就代表步驟太細或切錯，重切成更少步。")
            rows.append('　做完這一步送 {"t":"這步做完"}；整個大動作做完送 {"t":"大動作完成"}。')
            rows.append("　（反射那一層只會看到**這一步與接下來兩步**，加上你寫的目的——"
                        "所以步驟要小到照著做不用想，目的要一句話講清楚為什麼。）")
        else:
            rows.append('　⚠ 這個大動作**還沒有細步驟**。快思的時候把它切開（記得寫 目的）：'
                        '{"t":"寫步驟","大動作":"' + nm + '","步驟":["第一步","第二步","..."]}'
                        '——切好之後反射輪照著做就行，不用每輪重想。')
        return NL.join(rows)

    def macro_window_text(self, n=3):
        """反射看的東西：**快思交下來的三個小動作＋它們的目的**，不是整串清單。

        2026-09-20 使用者：「反射還需要載入快思傳來的三個小動作，以及這三個小動作的目的，
        減少反射的計算時間。」——看得越少、越明確，出手越快。
        """
        nm = self.macro_name()
        if not nm:
            return ('【現在做什麼】還沒有大動作。送 {"t":"換快思"} 讓會思考的那一層排一個給你。')
        steps, si = self.macro_steps(), self.macro_step_idx()
        aim = (self.macro.get("目的") or {}).get(nm, "")
        if not steps:
            return (f"【現在這個大動作】{nm}" + NL
                    + '　⚠ 還沒有步驟。送 {"t":"換快思"}，讓快思把它切成小動作再做。')
        win = steps[si:si + n]
        rows = [f"【現在做什麼】大動作：**{nm}**" + (f"（目的：{aim}）" if aim else "")]
        for k, st in enumerate(win):
            tag = "**① 現在這一步**" if k == 0 else f"　　② 接著" if k == 1 else "　　③ 然後"
            rows.append(f"　{tag}：{st}")
        rows.append(f"　（整個大動作共 {len(steps)} 步，你在第 {si + 1} 步）")
        rows.append('　做完①就送 {"t":"這步做完"}；三步都做完或情況不對就送 {"t":"換快思"}。')
        return NL.join(rows)

    # ---------------- 快思三步佇列（2026-09-29 使用者：快思直接出前後三步的詳細動作，反射只檢查）
    PLAN3_ACTS = ("key", "hold", "click", "look", "aim", "scroll", "wait", "點字", "走到記過的", "水平歸零",
                  "這步做完", "大動作完成", "寫步驟", "我在", "標點", "切空間", "記知識", "記問題", "基礎確認",
                  "記互動物", "記觸碰物", "量比例", "定比例", "改提示")

    # 2026-10-06 使用者：「盡量都要開思考、減少反射的方式遊玩，但注意不要閒置太久」
    # GAMER_PLAN_STEPS=1：快思（開推理）每一輪只決定「現在這一步」，做完下一輪再看畫面、再想一次——
    # 不排後面的步，所以幾乎沒有不思考的反射檢查輪（只剩他同一輪送兩個按鍵時，第二個還是先由檢查輪看過再按）。
    # 不用寫 步，輸出短，一輪想的時間也短一點（人站著不動的時間）。預設 3＝09/29 的三步＋反射檢查。
    try:
        PLAN_STEPS = max(1, min(3, int(os.environ.get("GAMER_PLAN_STEPS", "3") or 3)))
    except ValueError:
        PLAN_STEPS = 3

    def plan3_ask(self):
        """快思那一輪多要的東西：三步、每步附動作與「做完會看到什麼」。"""
        if self.PLAN_STEPS <= 1:
            return ("【這一輪只決定現在這一步】（2026-10-06 使用者：盡量都開思考、減少反射）" + NL
                    + "現在每一輪你都是先想過再決定，所以**不用排後面的步、不用寫 步**：只寫這一輪的 a。"
                    + "做完之後，下一輪你會再看一次畫面、再想一次。" + NL
                    + "**一次看畫面只按一個**：a 裡只放一個會動到遊戲的動作（走、轉頭、按 E、點、滾輪擇一）；"
                    + "不動到遊戲的工具（這步做完、我在、標點、記知識…）可以一起放。放了兩個，程式只先做第一個，"
                    + "第二個排到下一輪、由檢查輪看畫面確認再按。" + NL
                    + "**不要閒置太久**（使用者交代）：你在想的時候，人是站著不動的。想清楚「現在畫面是什麼 → 這一下按什麼 → "
                    + "按完應該看到什麼」就送出，不用把面板重講一遍。" + NL
                    + '做完大動作的一步，把 {"t":"這步做完"} 放進 a。')
        return ("【快思要直接給三步的動作】（2026-09-29 使用者定）" + NL
                + "除了這一輪的 a，還要把**現在這一步、接著那一步、然後那一步**各自的動作寫清楚，放在 步 裡：" + NL
                + '　"步":[{"做":"後退離開牆面","a":[{"t":"key","k":"s","d":1.0}],"成":"畫面拉開、看得到走廊"},' + NL
                + '　　　　{"做":"轉向電腦","a":[{"t":"look","dx":-90,"dy":0,"g":"電腦"}],"成":"電腦螢幕在畫面中央"},' + NL
                + '　　　　{"做":"走到電腦前","a":[{"t":"key","k":"w","d":2.0,"g":"電腦"}],"成":"電腦變大、出現E提示"}]' + NL
                + "規矩：①的 a 就是這一輪要做的（a 可以省略，程式會用 步[0].a）；每一步小到照著做不用想；"
                + "「成」寫**畫面上看得到的變化**，反射就靠它判斷有沒有做成。" + NL
                + "接下來的輪次由反射檢查：上一步做成了、**而且下一步要按的東西現在的畫面上對得上**，程式才做②、再做③；"
                + "沒做成、畫面對不上、或三步做完，就回到你這裡重排。" + NL
                + "**一次看畫面只按一個**：同一步裡放兩個會動到遊戲的動作（例如轉頭＋走、走＋按 E），程式只先做第一個，"
                + "第二個排到下一輪、先看畫面確認再按。所以要按 E／點按鈕的那一步，前一步的「成」就要寫清楚：看到 E 提示、看到那顆按鈕。" + NL
                + '做完大動作的一步，把 {"t":"這步做完"} 放進那一步的 a。')

    def plan3_apply(self, mode, o):
        """快思：把 步[0..2] 收進佇列，這一輪做 ①。反射：看 ok 決定做下一步還是換快思。"""
        def norm(st):
            if not isinstance(st, dict):
                return None
            acts = [a for a in (st.get("a") or []) if isinstance(a, dict) and str(a.get("t") or "") in self.PLAN3_ACTS]
            return {"做": str(st.get("做") or st.get("do") or "")[:40], "a": acts[:3],
                    "成": str(st.get("成") or st.get("done") or "")[:40]}
        if mode == 快思:
            raw = o.get("步") or o.get("steps") or []
            steps = [s for s in (norm(x) for x in (raw if isinstance(raw, list) else [])) if s and s["a"]]
            if not steps:
                self._plan_q, self._plan_cur = [], None
                if raw:
                    print("   ⚠ 快思的「步」裡沒有可執行的動作，這輪照 a 做、下輪還是快思")
                return o
            first = steps[0]
            # ①的 a 就是這一輪要做的：他另外在 a 裡放的工具（記知識、我在、標點…）保留，
            # 動到遊戲的動作以 步[0].a 為準——不然反射下一輪會拿「①做成了嗎」去檢查一個根本沒做的動作
            game = ("key", "hold", "click", "look", "aim", "scroll", "wait", "點字", "走到記過的", "水平歸零")
            tools = [a for a in (o.get("a") or []) if isinstance(a, dict) and str(a.get("t") or "") not in game]
            o["a"] = tools + list(first["a"])
            self._plan_cur = first
            self._plan_q = steps[1:self.PLAN_STEPS]        # GAMER_PLAN_STEPS=1 → 不排後面的步，下一輪還是快思
            self.stat["快思三步"] = self.stat.get("快思三步", 0) + 1
            print("   🧠 快思排了三步：" + " → ".join(f"{i + 1}.{s['做']}" for i, s in enumerate(steps[:3]))
                  + f"（這輪做 1，佇列 {len(self._plan_q)}）")
            if self.PLAN_STEPS > 1:
                self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 快思排三步："
                                    + " → ".join(s["做"] for s in steps[:3]))
            return o
        if not getattr(self, "_reflex_check", False):
            return o
        ok = o.get("ok")
        if isinstance(ok, str):
            ok = ok.strip().lower() in ("true", "是", "yes", "ok", "有", "1", "正常")
        why = str(o.get("為什麼") or o.get("why") or o.get("想") or "")[:40]
        cur = getattr(self, "_plan_cur", None) or {}
        try:
            self.check_note(o)                 # 2026-10-05：檢查輪可以多寫一句（只收下、給快思看，不加分不扣分）
        except Exception as e:  # noqa: BLE001
            print(f"   檢查輪那一句沒收到：{e}")
        go = o.get("可以按")
        if go is None:
            go = o.get("go")
        if isinstance(go, str):
            go = go.strip().lower() in ("true", "是", "yes", "ok", "可以", "1", "對")
        if ok and not go:
            # 2026-10-05 使用者：按下去之前要先確認過畫面。上一步做成了，但下一步現在的畫面對不上（或他沒回答）→ 不按
            nx = (self._plan_q[0] if self._plan_q else {})
            self.stat["按前確認沒過"] = self.stat.get("按前確認沒過", 0) + 1
            print(f"   🖐 反射：上一步「{cur.get('做', '')}」做成了，但下一步「{nx.get('做', '')}」"
                  f"（{self.press_desc(nx.get('a'))}）現在的畫面對不上{'' if go is False else '（沒回答可以按）'} → 不按，換快思重排")
            self._plan_q = []
            o["a"] = [{"t": "換快思"}]
            o["想"] = f"（反射）下一步現在不能按：{nx.get('做', '')}"
            self.mark_step(f"按前確認沒過：{nx.get('做', '')}")
            return o
        if ok:
            nxt = self._plan_q.pop(0)
            self._plan_cur = nxt
            self.stat["反射檢查通過"] = self.stat.get("反射檢查通過", 0) + 1
            print(f"   ✅ 反射：上一步「{cur.get('做', '')}」做成了、下一步畫面確認可以按 → 做「{nxt['做']}」（佇列剩 {len(self._plan_q)}）")
            o["a"] = list(nxt["a"])
            o["想"] = f"（快思排的）{nxt['做']}"
            if not self._plan_q:
                self._force_fast = True          # 三步做完：下一輪回到快思重排
        else:
            self.stat["反射檢查失敗"] = self.stat.get("反射檢查失敗", 0) + 1
            print(f"   ✋ 反射：上一步「{cur.get('做', '')}」沒做成（{why}）→ 換快思重排")
            self._plan_q = []
            o["a"] = [{"t": "換快思"}]
            o["想"] = f"（反射）上一步沒做成：{why}"
            self.mark_step(f"反射檢查沒過：{why}")
        return o

    # ---------------- 檢查輪可以簡單寫一句（2026-10-05 使用者：「給反射動作添加可以簡單撰寫」）
    CHECK_NOTE_MAX = 30          # 一句最多幾個字
    CHECK_NOTE_KEEP = 6          # 留最近幾句
    CHECK_NOTE_SHOW_SECS = 600   # 多久以內的才放上快思的面板

    def check_note(self, o):
        """檢查輪回的 JSON 裡有「寫」就收下：進這一輪的「看到」、進歷史、留給下一次快思看。只告訴，不賞不罰。"""
        raw = o.get("寫") or o.get("記") or o.get("note") or ""
        note = " ".join(str(raw).split())[:self.CHECK_NOTE_MAX]
        if not note or note in ("無", "沒有", "none", "None", "…"):
            return ""
        stamp = datetime.now().strftime("%H:%M:%S")
        rows = list(getattr(self, "check_notes", []) or [])
        rows.append({"時": stamp, "t": time.time(), "寫": note})
        self.check_notes = rows[-self.CHECK_NOTE_KEEP:]
        self.stat["檢查輪寫了"] = self.stat.get("檢查輪寫了", 0) + 1
        o["s"] = f"（檢查輪自己寫的）{note}"       # 這一輪短期記憶的「看到」用他自己寫的這一句
        print(f"   ✍ 檢查輪寫了一句：{note}")
        self.history.append(f"[{stamp}] 檢查輪你自己寫：{note}")
        try:
            with open(os.path.join(self.dir, "檢查輪寫的.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps({"日期": datetime.now().strftime("%Y-%m-%d"), "時間": stamp, "寫": note},
                                   ensure_ascii=False) + NL)
        except OSError:
            pass
        return note

    def check_notes_text(self):
        """快思面板最上面：他在檢查輪自己寫下的那幾句（十分鐘內的）。"""
        now = time.time()
        rows = [r for r in (getattr(self, "check_notes", []) or []) if now - r.get("t", 0) <= self.CHECK_NOTE_SHOW_SECS]
        if not rows:
            return ""
        return ("📝 **你在檢查輪自己寫下的**（那時候畫面上看到的；現在畫面上可能已經沒有了）："
                + "；".join(f"{r['時']}「{r['寫']}」" for r in rows[-4:]))

    def reflex_check_panel(self):
        """反射有佇列時的提示：檢查「上一步有沒有正常執行」＋確認「接下來要按的那一下，現在的畫面可不可以按」。不決定動作。"""
        cur = getattr(self, "_plan_cur", None) or {}
        q = list(getattr(self, "_plan_q", []) or [])
        nxt = q[0] if q else {}
        parts = [self.ban_text(), self.last_step_text(),
                 "【快思交代的上一步】做「" + cur.get("做", "") + "」，做完應該看到：「" + (cur.get("成") or "（沒寫）") + "」",
                 "【接下來要按的】" + (("做「" + nxt.get("做", "") + "」：" + self.press_desc(nxt.get("a"))) if nxt else "（這是最後一步）"),
                 "【再後面排好的】" + ("；".join(f"{i + 3}.{s['做']}" for i, s in enumerate(q[1:])) if q[1:] else "（沒有了）"),
                 "【眼睛看到】" + (getattr(self, "_seen_reflex", "") or ""),
                 "【目前視角】" + self.gyro_text()[:200],
                 "【畫面上的字】" + self.text_text()[:400],
                 "【右上角任務】" + (getattr(self, "cur_task", "") or "（沒讀到）"),
                 self.short_mem_text(2)]
        body = NL.join(p for p in parts if p and p.strip())
        # 2026-09-30 使用者：反射輪只檢查快思交代的事有沒有做到，不需要輸出別的（只回 ok）。
        # 2026-10-05 使用者：「所有的按鍵按下去之前都需要確認過畫面再按」——10/05 實測，照排好的步直接送的 E
        # 97 次、有結果的 66 次全部沒反應：按的那一輪沒有人看畫面上有沒有 E 提示。
        # 現在檢查輪多回一個「可以按」：接下來要按的那一下，現在的畫面對不對得上。
        return (body + NL * 2
                + "**你只做兩件事：檢查上一步、確認下一步。**都要看畫面回答。" + NL
                + "ok＝上一步有沒有正常執行（畫面是不是變成快思說的樣子、或至少朝那個方向變了）。"
                + "沒做成、畫面沒變、跳出沒看過的東西、被傳送、死掉 → false。" + NL
                + "可以按＝【接下來要按的】那一下，現在的畫面對不對得上：要按 E，畫面上現在要看得到 E 提示／互動提示；"
                + "要點按鈕或點字，那顆按鈕／那個字現在要看得到；要走或轉頭，前面沒有面板蓋住、方向對。對不上就 false。" + NL
                + "兩個都 true 程式才會按下一步；有一個 false 就換快思重排。" + NL
                # 2026-10-05 使用者：「給反射動作添加可以簡單撰寫」——檢查輪看到的東西以前沒有地方寫，
                # 字只出現在這一輪的畫面上，下一次快思就看不到了。
                + "**可以多寫一句**（不一定要寫）：這一輪畫面上有快思該知道的事——跳出新的字、數字變了、出現或消失了什麼、"
                + "跟快思說的不一樣——就加一個「寫」，20 字以內，寫你在畫面上看到的原樣。下一次快思會讀到這一句。" + NL
                + '**不要自己送動作。**輸出：{"ok":true,"可以按":true}（兩個都要寫，各自 true 或 false）'
                + '；有要寫的就 {"ok":true,"可以按":true,"寫":"看到的那一句"}')

    # ---------------- 一次看畫面只按一個（2026-10-05 使用者：所有的按鍵按下去之前都需要確認過畫面再按）
    PRESS_ACTS = ("key", "hold", "click", "look", "aim", "scroll", "點字", "點文字", "走到記過的", "走到", "水平歸零")

    def press_desc(self, acts):
        """把一步的動作寫成一行字，給檢查輪看「接下來要按什麼」。"""
        out = []
        for a in (acts or []):
            if not isinstance(a, dict):
                continue
            try:
                out.append(self.describe(a))
            except Exception:  # noqa: BLE001
                out.append(str(a.get("t") or ""))
        return "、".join(x for x in out if x) or "（沒有動作）"

    def one_press_per_look(self, o):
        """這一輪的 a 裡有兩個以上會動到遊戲的動作 → 只做第一個，其餘排到下一輪：先看畫面確認再按。

        以前快思一輪可以送「轉頭＋走」「走＋按 E」「準心對準＋按 e」，第二個動作是在第一個改變畫面之後、
        沒有人再看一眼的情況下按出去的。等／記知識／這步做完這類不動到遊戲的，跟著它前面那個動作走。"""
        acts = [a for a in (o.get("a") or []) if isinstance(a, dict)]
        idx = [i for i, a in enumerate(acts) if str(a.get("t") or "") in self.PRESS_ACTS]
        if len(idx) <= 1:
            return o
        cut = idx[1]
        now, later = acts[:cut], acts[cut:]
        cur = getattr(self, "_plan_cur", None) or {}
        do = (cur.get("做") or str(o.get("想") or o.get("why") or "") or "接著按")[:34]
        rest = {"做": do + "（接著按）", "a": later, "成": cur.get("成") or ""}
        self._plan_cur = {"做": do, "a": now, "成": "這一下有按出去、畫面有跟著變"}
        self._plan_q = [rest] + list(getattr(self, "_plan_q", []) or [])
        self._force_fast = False                 # 還有排著的動作：下一輪是檢查輪，不是快思
        o["a"] = now
        self.stat["一次只按一個"] = self.stat.get("一次只按一個", 0) + 1
        print(f"   👀 一次看畫面只按一個：這輪先做「{self.press_desc(now)}」，"
              f"「{self.press_desc(later)}」排到下一輪，先看畫面確認再按")
        return o

    STEP_LOG = "步驟日誌.jsonl"

    def log_step(self, seen, did, eff):
        """2026-09-20 使用者：**中間的每一個小動作都要有紀錄＋當時畫面的簡單形容**。

        這樣檢討的時候可以從頭看：哪一步卡住、卡了幾輪、當時畫面長什麼樣、畫面有沒有變。
        跟短期記憶的差別：短期記憶只有最近四輪（給當下用），這一份是**整個大動作從頭到尾**。
        """
        nm = self.macro_name()
        steps, si = self.macro_steps(), self.macro_step_idx()
        rec = {"時間": datetime.now().strftime("%H:%M:%S"), "輪": self.stat.get("輪數", 0),
               "大動作": nm, "第幾步": si + 1,
               "步驟": (steps[si] if 0 <= si < len(steps) else ""),
               "看到": str(seen or "")[:50], "做了": str(did or "")[:60],
               "結果": str(eff or "")[:30], "區": getattr(self, "cur_zone", "") or ""}
        self.step_log = (getattr(self, "step_log", []) + [rec])[-120:]
        try:
            with open(os.path.join(self.dir, self.STEP_LOG), "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + NL)
        except OSError:
            pass

    def mark_step(self, what):
        """步驟推進／換大動作／喊換快思時留一個標記，檢討時看得出分界。"""
        rec = {"時間": datetime.now().strftime("%H:%M:%S"), "輪": self.stat.get("輪數", 0),
               "標記": str(what)[:40], "大動作": self.macro_name(),
               "第幾步": self.macro_step_idx() + 1}
        self.step_log = (getattr(self, "step_log", []) + [rec])[-120:]
        try:
            with open(os.path.join(self.dir, self.STEP_LOG), "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + NL)
        except OSError:
            pass

    def step_journal_text(self, n=40):
        """檢討用：這個大動作從頭到現在，每一步花了幾輪、畫面是什麼、有沒有變。"""
        rows = list(getattr(self, "step_log", []))[-n:]
        if not rows:
            return ""
        out = ["【這個大動作從頭到現在（每一個小動作的紀錄）】"]
        cur = None
        same = 0
        for r in rows:
            if r.get("標記"):
                out.append(f"　—— {r['時間']} {r['標記']} ——")
                cur = None
                continue
            key = (r.get("大動作"), r.get("第幾步"))
            if key != cur:
                cur, same = key, 1
                out.append(f"　第 {r.get('第幾步')} 步「{r.get('步驟') or '（沒步驟）'}」")
            else:
                same += 1
            dead = "（畫面沒變）" if ("幾乎沒動" in str(r.get("結果")) or "變化 0" in str(r.get("結果"))) else ""
            out.append(f"　　[{r['時間']}] 看到「{r.get('看到')}」→ 做了「{r.get('做了')}」{dead}")
        out.append("　→ 從頭看一遍：**哪一步一直重複、畫面一直沒變**，那一步就是你卡住的地方。")
        return NL.join(out)

    def reflex_panel(self, uri_text=""):
        """反射輪的提示：**只有基礎操作會用到的儀表＋現在這一步**，不帶學習模組。

        2026-09-20 使用者：反射要減少的是「要想的東西」，不是「看得到的東西」——
        所以儀表（視角、標點、按鈕簿、畫面上的字、短期記憶、重複警報）全部留著，
        砍掉的是那些長篇教學與計分說明（那些慢想的時候再讀）。
        情況跟現在這一步對不上，就喊換快思，不要自己亂改計畫。
        """
        parts = [self.ban_text(), self.follow_text(), self.repeat_alarm(), self.last_step_text(), self.basic_check_text(),
                 getattr(self, "check_hint", ""), self.check_text(),
                 getattr(self, "budget_hint", ""),
                 getattr(self, "click_effect_hint", ""), self.macro_window_text(),
                 "【右上角任務】" + (getattr(self, "cur_task", "") or "（沒讀到）"),
                 "【眼睛看到】" + (getattr(self, "_seen_reflex", "") or ""),
                 "【目前視角】" + self.gyro_text()[:260],
                 "【對正提示】" + self.align_hint(),
                 "【左右牆距】" + self.path_center(),
                 "【畫面上的字】" + self.text_text()[:700],
                 self.points_text(4), self.btn_memo_text(5),
                 self.short_mem_text(3), self.improve_text(2),
                 ("【提醒】" + (getattr(self, "punish_hint", "") or "")
                  + (getattr(self, "lesson_hint", "") or "")
                  + (getattr(self, "proc_hint", "") or ""))[:500],
                 "【你現在標的區】" + str(getattr(self, "cur_zone", "") or "（還沒標）"),
                 ]
        body = NL.join(p for p in parts if p and p.strip())
        return (body + NL * 2
                + "照**現在這一步**做，馬上做一件事。" + NL
                + "如果畫面跟這一步對不上（步驟過期、跳出沒看過的東西、被傳送、死掉），"
                + '**不要自己亂改計畫**，送 {"t":"換快思"} 讓會思考的那一層重新切步驟。' + NL
                + '只輸出 JSON：{"s":"看到什麼(<=14字)","區":"你現在在哪一區(<=8字)",'
                  '"想":"為什麼做這個(<=20字)","a":[動作…最多2個],"task":"右上角任務原文"}'
                + NL + "**a 不可以是空的**——真的沒事做就送 "
                + '{"t":"換快思"}。')

    def make_zoom(self, a):
        """把畫面上某一塊裁切放大，下一輪多送一張放大圖給他——比差異要看得清楚才比得出來。"""
        try:
            x = float(a.get("x", 0.5)); y = float(a.get("y", 0.5))
        except (TypeError, ValueError):
            return
        full = self.grab_full()
        w, h = full.size
        bw, bh = int(w * 0.22), int(h * 0.28)
        cx, cy = int(x * w), int(y * h)
        box = (max(0, cx - bw), max(0, cy - bh), min(w, cx + bw), min(h, cy + bh))
        crop = full.crop(box).resize((720, int(720 * (box[3] - box[1]) / max(1, box[2] - box[0]))))
        self.zoom_img = crop
        self.zoom_of = str(a.get("g") or "")[:20]
        print(f"   🔍 放大「{self.zoom_of}」({x:.2f},{y:.2f})，下一輪給你看")

    def where_am_i(self):
        """程式直接查前景視窗是哪個程式——他很常把遊戲裡的大廳誤認成 Roblox 首頁。"""
        try:
            import win32gui, win32process, psutil
            h = win32gui.GetForegroundWindow()
            title = win32gui.GetWindowText(h)
            _, pid = win32process.GetWindowThreadProcessId(h)
            name = psutil.Process(pid).name().lower()
        except Exception:  # noqa: BLE001
            return "（查不到）", "unknown"
        if "roblox" in name:
            return f"Roblox 遊戲視窗（{title or '無標題'}）＝你正在遊戲裡", "game"
        if any(k in name for k in ("chrome", "msedge", "firefox", "brave")):
            return f"瀏覽器（{title[:30]}）＝這不是遊戲畫面", "browser"
        return f"{name}（{title[:30]}）＝不是遊戲畫面", "other"

    # ---------------- 水平儀（他自己的視角度數表）
    DEG_PER_PCT = 0.70          # 畫面寬約 70 度＝100%
    PITCH_LIMIT = 89.0

    def dy_per_deg_v(self):
        """垂直：1 度要拖幾個 dy。優先用機械掃描量到的值；沒有就退回水平換算。"""
        v = getattr(self, "dy_per_deg", None)
        if v and v > 0:
            return v
        return (getattr(self, "px_per_pct", 4.0) or 4.0) / self.DEG_PER_PCT

    def gyro_add(self, dx, dy):
        """所有轉視角都會呼叫這裡：把滑鼠位移換算成度數，累加成俯仰角與方位角。

        這是航位推算，會漂；所以下面 gyro_sync() 每輪用眼睛量到的結果把它拉回來，
        頂到上下極限（轉了畫面完全沒變）時直接校正成 ±89 度。
        """
        ppp = getattr(self, "px_per_pct", 4.0) or 4.0
        dxd = getattr(self, "dx_per_deg", None)
        self.yaw = (getattr(self, "yaw", 0.0) + (dx / dxd if dxd else dx / ppp * self.DEG_PER_PCT)) % 360.0
        p = getattr(self, "pitch_deg", 0.0) - dy / self.dy_per_deg_v()      # dy 正＝往下＝俯角變負
        self.pitch_deg = max(-self.PITCH_LIMIT, min(self.PITCH_LIMIT, p))
        self.pitch_lo = min(getattr(self, "pitch_lo", 0.0), self.pitch_deg)
        self.pitch_hi = max(getattr(self, "pitch_hi", 0.0), self.pitch_deg)

    def gyro_sync(self):
        """2026-09-14 使用者定案：**水平儀以算出來的度數為主，畫面只是輔助。**

        以前每輪讓畫面判斷直接覆寫角度（說水平就 ×0.3、說天花板就設 60），
        畫面一看錯整個讀數就跟著錯。現在：
          - 硬錨點只有 pitch_edge（轉了畫面完全沒變＝真的頂到 ±89），這是機械事實
          - 畫面（反射／YOLO）**只在跟度數差很多時，每輪最多拉 8 度**，並記下「相符／不符」
          - 沒有任何「說水平就歸零」的動作
        """
        edge = getattr(self, "pitch_edge", "")
        if edge and time.time() - getattr(self, "pitch_edge_at", 0) < 8:
            self.pitch_deg = self.PITCH_LIMIT if edge == "上" else -self.PITCH_LIMIT
            self._gyro_agree = "頂到極限（機械校正）"
            return
        p = getattr(self, "pitch_deg", 0.0)
        try:
            _, need = self.pitch_from_eyes()
        except Exception:  # noqa: BLE001
            need = None
        src = getattr(self, "_pitch_src", "")
        if need is None or src not in ("反射", "YOLO"):
            self._gyro_agree = ""
            return
        target = {1: 70.0, -1: -70.0, 0: 0.0}[need]
        gap = target - p
        ok = (need == 1 and p >= 45) or (need == -1 and p <= -45) or (need == 0 and abs(p) <= 30)
        if ok:
            self._gyro_agree = f"畫面（{src}）相符"
            return
        step = max(-8.0, min(8.0, gap * 0.25))
        self.pitch_deg = max(-self.PITCH_LIMIT, min(self.PITCH_LIMIT, p + step))
        word = "天花板" if need == 1 else ("地板" if need == -1 else "水平")
        self._gyro_agree = f"畫面（{src}）說{word}，跟度數不符，微調 {step:+.0f} 度"

    COMPASS = ["北", "東北", "東", "東南", "南", "西南", "西", "西北"]

    def compass(self, y):
        """方位角換成方向名。出生的時候面朝正北 0 度，往右轉角度變大（北 0／東 90／南 180／西 270）。"""
        return self.COMPASS[int((y % 360 + 22.5) // 45) % 8]

    def gyro_zero(self, why="出生"):
        """把水平儀歸零：現在這個姿勢就是『完全水平、面朝正北』，之後全部相對這裡算。"""
        self.pitch_deg = 0.0
        self.yaw = 0.0
        self.pitch_hi = 0.0
        self.pitch_lo = 0.0
        self.gyro_zero_at = datetime.now().strftime("%H:%M:%S")
        print(f"   🧭 水平儀歸零（{why}）：現在＝完全水平、面朝正北 0 度")

    GYRO_CHECK_EVERY = 5        # 使用者 09/14：每五輪驗證一次水平儀的方向對不對

    def gyro_check_text(self):
        """水平儀方向驗證：每五輪報一次。

        不另外動鏡頭——用他自己送的 dy 去量畫面實際往哪移動
        （往下轉時畫面裡的東西應該往上跑）。累積對／錯的次數，
        連續三次相反就當場警告，代表 dy 的正負在這台機器上是反的。
        """
        c = getattr(self, "gyro_check", None) or {}
        n_ok, n_bad = int(c.get("對", 0)), int(c.get("錯", 0))
        tot = n_ok + n_bad
        if self.stat.get("輪數", 0) % self.GYRO_CHECK_EVERY and tot:
            # 不是驗證輪就只給一行摘要
            return f"　水平儀方向驗證：{n_ok}/{tot} 次正確"
        if not tot:
            return "　水平儀方向驗證：還沒量到（要送出 |dy|≥60 的轉視角，而且畫面要有東西才量得到）"
        if tot < 8:
            return (f"　水平儀方向驗證：樣本還不夠（{tot} 筆，要 8 筆才算數）——"
                    f"只有幅度 40~200 的 dy 量得準，一次轉 600 度那種前後畫面不重疊、量不出來")
        if c.get("連錯", 0) >= 3 and n_bad > n_ok:
            # 2026-09-14 的教訓：這個量測**在磁磚、格線這種重複紋理上會整批配錯**
            # （每條格線配到隔壁那條，一起偏移，所以「一致度 100%」反而是假訊號）。
            # 我曾經照它的結論把 dy 反號，結果把原本正確的方向弄反、害他整個下午對著天花板。
            # 所以它只報「量到什麼」，**不下「符號相反」的結論**。
            return (f"　ℹ 畫面位移量測連續 {c['連錯']} 次跟預期不符（{c.get('最後', '')}）。"
                    f"**這個量測在天花板／地板這種重複格線上不可靠**（格線會整批配錯一格），"
                    f"看到這行不用改變做法——以【目前視角】第一行的畫面判斷為準。")
        return (f"　水平儀方向驗證（每 {self.GYRO_CHECK_EVERY} 輪一次）："
                f"{n_ok}/{tot} 次正確｜最近一次：{c.get('最後', '（還沒有）')}")

    def gyro_text(self):
        """給他看的視角讀數：跟一開始（出生時完全水平、面朝正北）差了多少。"""
        self.gyro_sync()
        p = getattr(self, "pitch_deg", 0.0)
        y = getattr(self, "yaw", 0.0) % 360
        if abs(p) <= 3:
            pit = "完全水平"
        elif p > 0:
            pit = f"抬頭 {p:.0f} 度"
        else:
            pit = f"低頭 {abs(p):.0f} 度"
        if p > 55:
            note = "（幾乎正對天花板，什麼都看不到）"
        elif p < -55:
            note = "（幾乎正對地板，什麼都看不到）"
        elif abs(p) <= 8:
            note = "（這就是對的，維持住）"
        else:
            note = ""
        # 2026-09-14 使用者：**水平儀的用處就是讓他一口氣調回水平。**
        # 原本這裡算完 need_dy 之後還加一句「分兩三次修，一次別超過 50」——
        # dy 50 只有 7 度，正對天花板（89 度）要修 12 輪，每一輪都在被扣 15 分。
        # 實測：dy 50≈7度、150≈22度、400≈60度（往下單次上限 400、往上 100）。
        chk = self.gyro_check_text()
        fix = ""
        if abs(p) > 8:
            need_dy = int(round(p * self.dy_per_deg_v()))           # 正＝往下；從 +89 回水平 ≈ +300
            need_dy = max(-600, min(600, need_dy))
            fix = (f"　→ 要回到完全水平：**一口氣送 dy {need_dy:+d}**，一次修完，不要分次。"
                   f"（實測每 100 ≈ 30 度，上下全程只有 600——送 600 會從天花板直接掃到地板）")
        agree = getattr(self, "_gyro_agree", "") or ""
        if agree:
            fix += f"　［畫面輔助：{agree}］"
        ppp2 = getattr(self, "px_per_pct", 4.0) or 4.0
        per = self.DEG_PER_PCT / ppp2
        import math
        hx, hy = math.sin(math.radians(y)), math.cos(math.radians(y))    # 東 +X、北 +Y
        px, py = (self.pos[0], self.pos[1]) if getattr(self, "pos", None) else (0.0, 0.0)
        try:
            _, vneed = self.pitch_from_eyes()
        except Exception:  # noqa: BLE001
            vneed = None
        _src = getattr(self, "_pitch_src", "") or "無"
        if vneed == 1:
            vline = f"（畫面輔助·{_src}：看起來像天花板）"
        elif vneed == -1:
            vline = f"（畫面輔助·{_src}：看起來像地板）"
        elif vneed == 0:
            vline = f"（畫面輔助·{_src}：看起來大致水平）"
        else:
            vline = "（畫面輔助：這一輪判不出來）"
        lines = [
            chk,
            f"**目前視角：{pit}**{note}　面朝**{self.compass(y)}**（{y:.0f} 度）{fix}",
            f"　{vline}",
            f"　朝向兩軸：X {hx:+.2f}／Y {hy:+.2f}"
            f"（往前走一秒，你的 X 大約 {hx:+.2f}、Y 大約 {hy:+.2f}；"
            f"X 正的是往東、Y 正的是往北）",
            f"　你現在的位置：X {px:+.1f}／Y {py:+.1f}"
            f"（出生點是 X 0／Y 0，這是用你走過的步數推算的，會慢慢漂；"
            f"走到你記過的地方就用 {{\"t\":\"我在\",\"name\":\"地名\"}} 校正一次）",
            f"　{self.gyro_bar(p)}",
            (f"　這是跟你**一開始出生時**的姿勢比出來的：出生那一刻＝完全水平、面朝正北 0 度"
             f"（{getattr(self, 'gyro_zero_at', '開機')} 歸零）。"),
            (f"　北 0 度／東 90 度／南 180 度／西 270 度，往右轉角度變大。"
             f"上下的極限是抬頭 90 度、低頭 90 度，頂到就再也轉不動。"
             f"換算：dy 每 1 ≈ {per:.2f} 度，dx 每 1 也是 ≈ {per:.2f} 度。"),
            (f"　你今天最高抬到 {getattr(self, 'pitch_hi', 0.0):+.0f} 度、最低低到 "
             f"{getattr(self, 'pitch_lo', 0.0):+.0f} 度。"),
        ]
        return NL.join(lines)

    def gyro_bar(self, p):
        """一條看得懂的尺：↑ 天花板 ────•──── 地板 ↓"""
        n = 21
        i = int(round((1 - (p + 90) / 180) * (n - 1)))
        i = max(0, min(n - 1, i))
        cells = ["─"] * n
        cells[n // 2] = "┼"          # 水平在正中間
        cells[i] = "●"
        return "抬頭90度 " + "".join(cells) + " 低頭90度"

    def gyro_save(self):
        import math
        try:
            with open(os.path.join(self.dir, "水平儀.json"), "w", encoding="utf-8") as f:
                json.dump({
                    "俯仰": round(getattr(self, "pitch_deg", 0.0), 1),
                    "讀法": ("完全水平" if abs(getattr(self, "pitch_deg", 0.0)) <= 3 else
                             ("抬頭 %.0f 度" % getattr(self, "pitch_deg", 0.0) if getattr(self, "pitch_deg", 0.0) > 0
                              else "低頭 %.0f 度" % abs(getattr(self, "pitch_deg", 0.0)))),
                    "方位": round(getattr(self, "yaw", 0.0) % 360, 1),
                    "面朝": self.compass(getattr(self, "yaw", 0.0)),
                    "朝向X": round(math.sin(math.radians(getattr(self, "yaw", 0.0))), 3),
                    "朝向Y": round(math.cos(math.radians(getattr(self, "yaw", 0.0))), 3),
                    "位置X": round((getattr(self, "pos", [0, 0]) or [0, 0])[0], 2),
                    "位置Y": round((getattr(self, "pos", [0, 0]) or [0, 0])[1], 2),
                    "今天最高": round(getattr(self, "pitch_hi", 0.0), 1),
                    "今天最低": round(getattr(self, "pitch_lo", 0.0), 1),
                    "方向驗證": dict(getattr(self, "gyro_check", None) or {}),
                "輪數": self.stat.get("輪數", 0),
                    "時間": datetime.now().strftime("%H:%M:%S"),
                }, f, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            pass

    def level_hint(self):
        """平視提醒：低頭看地板或抬頭看天花板時，直接告訴他該往哪修。"""
        desc, need = self.pitch_from_eyes()
        if need is None:
            return ("（現在量不出俯仰：眼睛框不到東西。**量不出來就不要動 dy**——"
                    "上下都不要動，先左右轉頭找東西，讓眼睛框到東西再說）"
                    )
        need = need or 0
        if need > 0:
            return "⚠ 你在抬頭看天花板，看不到路也看不到東西——先用正的 dy（往下）修回平視再做別的事"
        if need < 0:
            return ("⚠ 你在低頭看地板——櫃檯、病人、按鈕、E 的提示全在你的視線上面，這樣什麼都做不成。"
                    "用小的負 dy（-40 到 -80）把頭抬回水平，一次一點點，抬到上下都看得到東西為止")
        return "視角大致平視，很好，走路和互動都要保持這樣（不要低頭看地板）"

    def goto_target(self, target, budget=8.0):
        """用 YOLO 的回饋閉環走到目標前面（他自己叫的，程式只負責執行這一段）。

        每 0.15 秒看一次：偏了就橫移對正，正了就前進，夠近或撞到東西就停。
        這只是「把眼睛的座標換成鍵盤動作」的機械控制，沒有任何判斷——
        要去哪、去了之後做什麼，都是他自己決定。
        """
        target = (target or "").strip()
        if not target:
            return
        self._track = target
        t0 = time.time()
        why = "時間到"
        print(f"   🚶 用 YOLO 閉環走向「{target}」")
        while time.time() - t0 < budget:
            d = self.eyes.best(target, max_age=1.2)
            if d is None:
                why = "眼睛看不到這個東西"
                break
            ex = d["cx"] - 0.5
            if d["h"] >= 0.55 or d["cy"] + d["h"] / 2 > 0.93:
                why = f"已經到它面前（高度 {d['h']:.2f}）"
                break
            if abs(ex) > 0.08:
                self.press_key("d" if ex > 0 else "a", 0.25)
            else:
                walked, r = self.walk_guarded("w", 0.6)
                if r:
                    why = r
                    break
            time.sleep(0.05)
        secs = round(time.time() - t0, 1)
        print(f"   🚶 走了 {secs} 秒，停下原因：{why}")
        self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 你叫程式走向「{target}」，"
                            f"走了 {secs} 秒後停下：{why}")
        self.brake_hint = f"⚠ 你剛才叫程式走向「{target}」，{secs} 秒後停下：{why}。" + NL

    # ---------------- YOLO 的工作：持續把目標維持在畫面中央
    def start_lock(self, target):
        """開一條背景執行緒，用 YOLO 每 0.2 秒的回饋把目標維持在畫面正中央。

        這樣千問在想事情的那幾秒裡，目標也不會跑掉——他睜眼時目標就在正中央，
        判斷與互動都容易得多。只修左右，不動上下（上下亂修會把鏡頭弄歪）。
        """
        target = (target or "").strip()
        if not target:
            return
        self.stop_lock("換目標")
        self.lock_target = target
        self._track = target
        self.lock_stop = threading.Event()
        self.lock_hits = 0

        def loop():
            lost = 0
            while not self.lock_stop.is_set() and not self.stop.is_set():
                time.sleep(0.2)
                if getattr(self, "_busy", False):     # 他自己在做動作時不要插手
                    continue
                d = self.eyes.best(self.lock_target, max_age=1.0)
                if d is None:
                    lost += 1
                    if lost > 25:                     # 五秒都看不到就放棄
                        print(f"   🔓 盯著「{self.lock_target}」超過 5 秒看不到，自動解除")
                        self.lock_target = ""
                        return
                    continue
                lost = 0
                ex = d["cx"] - 0.5
                if abs(ex) < 0.06:
                    continue
                ppp = getattr(self, "px_per_pct", 4.0) or 4.0
                dx = int(max(-120, min(120, ex * 100 * ppp * 0.5)))
                if dx and self.win.is_foreground():
                    self.raw_look(dx, 0)
                    self.lock_hits += 1

        self.lock_thread = threading.Thread(target=loop, daemon=True)
        self.lock_thread.start()
        print(f"   🔒 YOLO 開始盯住「{target}」，會一直幫你把它維持在畫面中央")
        self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 你叫 YOLO 盯住「{target}」")

    def stop_lock(self, why=""):
        ev = getattr(self, "lock_stop", None)
        if ev is not None:
            ev.set()
        if getattr(self, "lock_target", ""):
            print(f"   🔓 停止盯住「{self.lock_target}」（{why}）")
        self.lock_target = ""

    # ---------------- 追蹤（2026-09-27 使用者：「讓 YOLO 持續追蹤某個東西，用它的大小確認距離與方向是否正確」）
    def start_follow(self, target):
        """只量不動：每輪量那樣東西在畫面哪裡、多大，跟上一輪比，告訴他距離與方向有沒有對。"""
        target = (target or "").strip()
        if not target:
            return
        self.follow_target, self.follow_hist = target, []
        try:
            if target not in self.watching:
                self.eyes.watch(self.watching + [target])
                self.watching = list(self.eyes.labels)
        except Exception as e:  # noqa: BLE001
            print(f"   追蹤加詞失敗：{e}")
        print(f"   👁 開始追蹤「{target}」（YOLO 持續標記，每輪回報大小與方向）")
        self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 開始追蹤「{target}」")

    def follow_sample(self):
        """每輪開頭量一次，並記下上一輪做了什麼動作（對照用）。"""
        t = getattr(self, "follow_target", "")
        if not t:
            return
        d = self.eyes.best(t, max_age=1.5)
        rec = {"時間": time.time(), "動作": getattr(self, "_last_summ", "") or "",
               "cx": round(d["cx"], 3) if d else None, "h": round(d["h"], 3) if d else None,
               "yaw": round(getattr(self, "yaw", 0.0) % 360, 1)}
        self.follow_hist = (getattr(self, "follow_hist", []) + [rec])[-8:]

    def follow_text(self):
        t = getattr(self, "follow_target", "")
        if not t:
            return ""
        h = getattr(self, "follow_hist", []) or []
        rows = [f"【追蹤中：{t}】（YOLO 每輪量一次；大小＝框的高度，越大越近；位置 0.5＝正中央）"]
        if not h or h[-1]["h"] is None:
            rows.append("　這一輪**看不到它**——它可能在畫面外、被擋住，或你轉離了方向。先左右轉找回來。")
        else:
            cur = h[-1]
            off = cur["cx"] - 0.5
            side = "正中央" if abs(off) < 0.06 else (f"偏右 {abs(off):.2f}" if off > 0 else f"偏左 {abs(off):.2f}")
            rows.append(f"　現在：位置 {cur['cx']:.2f}（{side}）、大小 {cur['h']:.2f}")
            prev = next((x for x in reversed(h[:-1]) if x["h"]), None)
            if prev:
                ratio = cur["h"] / max(prev["h"], 1e-3)
                if ratio > 1.08:
                    dist = f"變大 {ratio:.2f} 倍＝**離它更近了**（距離大約剩原本的 {100 / ratio:.0f}%）"
                elif ratio < 0.92:
                    dist = f"變小到 {ratio:.2f} 倍＝**離它更遠了**——走錯方向或被推開"
                else:
                    dist = "大小幾乎沒變＝距離沒變（沒走動，或被擋住）"
                drift = cur["cx"] - prev["cx"]
                dirn = ("方向維持住了" if abs(drift) < 0.05 else
                        f"它往{'右' if drift > 0 else '左'}跑了 {abs(drift):.2f}＝你的方向偏{'左' if drift > 0 else '右'}了")
                rows.append(f"　跟上一輪比（你上一輪做了：{cur['動作'] or '—'}）：{dist}；{dirn}")
            seq = "、".join(f"{x['h']:.2f}" if x["h"] else "看不到" for x in h[-6:])
            rows.append(f"　最近幾輪大小：{seq}")
        rows.append('　不用了就送 {"t":"停止追蹤"}；要它幫你轉鏡頭對準，用 鎖定。')
        return NL.join(rows)

    def lock_text(self):
        t = getattr(self, "lock_target", "")
        if not t:
            return "（沒有在盯任何東西。要 YOLO 幫你把某樣東西一直維持在畫面中央，用 鎖定 動作。）"
        d = self.eyes.best(t, max_age=1.5)
        pos = f"現在在 ({d['cx']:.2f},{d['cy']:.2f})、大小 {d['h']:.2f}" if d else "現在暫時看不到"
        return f"正在盯住「{t}」：{pos}；已經幫你修正 {getattr(self, 'lock_hits', 0)} 次"

    def candidates(self):
        """把眼睛框到的東西整理成有編號的候選清單，他用編號選就不用猜座標。"""
        dets, stamp = self.eyes.latest()
        rows = []
        if stamp and time.time() - stamp < 2.5:
            for k, v in (dets or {}).items():
                for d in v[:2]:
                    if self.eyes.is_self(d):
                        continue
                    rows.append((k, d))
        rows.sort(key=lambda r: -r[1]["score"])
        self.cands = rows[:6]
        if not self.cands:
            return "（眼睛現在沒框到東西；先轉頭掃一圈，或用 找 動作換要偵測的詞）"
        out = []
        for i, (k, d) in enumerate(self.cands, 1):
            side = "正中" if abs(d["cx"] - 0.5) < 0.08 else ("右" if d["cx"] > 0.5 else "左")
            out.append(f"{i}) {k}　位置({d['cx']:.2f},{d['cy']:.2f}) {side}　大小{d['h']:.2f}　信心{d['score']:.2f}")
        return NL.join(out)

    def pick(self, a):
        """他用編號選一個目標，程式幫他對準（可選：順便走過去或互動）。"""
        try:
            n = int(a.get("n"))
        except (TypeError, ValueError):
            return
        rows = getattr(self, "cands", [])
        if not (1 <= n <= len(rows)):
            print(f"   ⚠ 沒有第 {n} 個候選目標")
            return
        lab, d = rows[n - 1]
        do = str(a.get("做") or a.get("do") or "aim")
        print(f"   🎯 他選了 {n}) {lab}（{d['cx']:.2f},{d['cy']:.2f}）→ {do}")
        self._track = lab
        self.execute([{"t": "aim", "x": d["cx"], "y": min(0.62, max(0.42, d["cy"])), "g": lab}])
        if "走" in do:
            self.goto_target(lab, budget=6.0)
        elif "互動" in do or "e" in do.lower():
            self.execute([{"t": "hold", "k": "e", "g": lab}])
        self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 你選了候選 {n}「{lab}」並{do}")

    def yolo_command(self):
        """把 YOLO 的框換算成一句明確的下一步指令。

        偵測器本身沒有智力（它只會說「有什麼、在哪、多大」），
        這裡是程式幫他把座標換算成「往哪邊移幾秒、還要走幾步」。
        他可以照做，也可以不理——判斷還是他自己的。
        """
        tgt = getattr(self, "_track", "") or ""
        d = self.eyes.best(tgt, max_age=2.0) if tgt else None
        if d is None:
            dets, stamp = self.eyes.latest()
            best = None
            for k, v in (dets or {}).items():
                for x in v[:1]:
                    if not self.eyes.is_self(x) and (best is None or x["score"] > best[1]["score"]):
                        best = (k, x)
            if best is None:
                return "眼睛現在沒框到任何目標 → 先小步轉頭掃一圈（look dx 100 左右），看到東西再說"
            tgt, d = best
        ex = d["cx"] - 0.5
        if abs(ex) > 0.06:
            key = "d" if ex > 0 else "a"
            secs = 0.3 if abs(ex) < 0.15 else 0.5
            return (f"目標「{tgt}」偏{'右' if ex > 0 else '左'} {abs(ex) * 100:.0f}%"
                    f" → 先按 {key} 橫移 {secs} 秒對正（不要轉頭）")
        if d["h"] >= 0.55 or d["cy"] + d["h"] / 2 > 0.93:
            return f"目標「{tgt}」已經在你面前（高度 {d['h']:.2f}）→ 不要再走，直接長按 E 互動"
        walk = 1.5 if d["h"] < 0.20 else (1.0 if d["h"] < 0.35 else 0.5)
        return (f"目標「{tgt}」已經對正、高度 {d['h']:.2f}（還有距離）"
                f" → 直接按 w 走 {walk} 秒（走過頭之前眼睛會幫你踩煞車）")

    def align_hint(self):
        """他正在找的東西現在偏左還是偏右，該往哪邊橫移才會完全正對。"""
        dets, stamp = self.eyes.latest()
        if not stamp or time.time() - stamp > 2.0 or not dets:
            return "（眼睛現在沒框到東西，沒辦法幫你對正）"
        best, lab = None, ""
        for k, v in dets.items():
            for d in v[:1]:
                if self.eyes.is_self(d):
                    continue
                if best is None or d["score"] > best["score"]:
                    best, lab = d, k
        if best is None:
            return "（眼睛只框到你自己，沒有可以對正的目標）"
        ex = best["cx"] - 0.5
        if abs(ex) < 0.05:
            return f"「{lab}」在畫面 {best['cx']:.2f}，已經正對著它了——可以直接按 w 走過去"
        side = "右" if ex > 0 else "左"
        key = "d" if ex > 0 else "a"
        secs = 0.3 if abs(ex) < 0.15 else 0.5
        return (f"「{lab}」在畫面 {best['cx']:.2f}，偏{side} {abs(ex) * 100:.0f}%"
                f" → 不要再轉頭，按 {key} 橫移 {secs} 秒把它挪到正中央")

    def path_center(self):
        """量左右兩側牆有多近，讓他自己走在路中央。

        兩側邊緣如果是一大片沒有紋理的色塊，代表牆貼很近；哪一邊紋理低就是哪一邊太近。
        回傳一句話給他看（程式不代他走，他自己決定要不要橫移）。
        """
        try:
            import numpy as np
            g = np.asarray(self.grab_still(320).convert("L"), dtype=np.float32)
            h, w = g.shape
            band = g[int(h * 0.35):int(h * 0.78), :]
            L = float(band[:, : int(w * 0.18)].std())
            C = float(band[:, int(w * 0.40): int(w * 0.60)].std())
            R = float(band[:, int(w * 0.82):].std())
        except Exception:  # noqa: BLE001
            return "（量不出來）"
        def near(v):
            return "貼很近" if v < 10 else ("有點近" if v < 20 else "還有空間")
        msg = f"左側 {L:.0f}（{near(L)}）／正前 {C:.0f}（{near(C)}）／右側 {R:.0f}（{near(R)}）"
        if L < 12 and R >= 20:
            msg += " → 你太靠左邊的牆了，先按 d 往右橫移 0.6~1 秒回到路中央再往前走"
        elif R < 12 and L >= 20:
            msg += " → 你太靠右邊的牆了，先按 a 往左橫移 0.6~1 秒回到路中央再往前走"
        elif L < 12 and R < 12:
            msg += " → 兩邊都貼牆，你在窄縫或角落裡，先後退再換方向"
        elif C < 12:
            msg += " → 正前方是牆，不要直走，先往紋理高的那一側轉"
        else:
            msg += " → 位置還行，可以直走"
        return msg

    def open_direction(self):
        """看畫面下半部（接近地面的那一帶）的紋理，估計左／中／右哪邊比較開闊。

        牆面貼在臉上時那一塊會變成一片幾乎沒有紋理的色塊；地板與走道則有磁磚線、家具、
        遠處的門，紋理明顯多。這只是估計，給他當提示，最後還是他自己看畫面決定。
        """
        try:
            import numpy as np
            g = np.asarray(self.grab_still(320).convert("L"), dtype=np.float32)
            h, w = g.shape
            band = g[int(h * 0.45):int(h * 0.85), :]
            cols = [band[:, : w // 3], band[:, w // 3: w * 2 // 3], band[:, w * 2 // 3:]]
            stds = [float(c.std()) for c in cols]
        except Exception:  # noqa: BLE001
            return "（估不出來）"
        names = ["左邊", "正前方", "右邊"]
        best = max(range(3), key=lambda i: stds[i])
        blocked = [names[i] for i in range(3) if stds[i] < 12]
        txt = f"左 {stds[0]:.0f}／前 {stds[1]:.0f}／右 {stds[2]:.0f}（數字越大越開闊）→ 最開闊的是 {names[best]}"
        if blocked:
            txt += f"；{'、'.join(blocked)} 幾乎是一片平面，多半是牆，不要往那邊走"
        return txt

    DESK_LIKE = ("counter", "desk", "table", "computer", "monitor", "screen",
                 "keyboard", "printer", "paper", "document", "folder")

    def view_state_cached(self, max_age=1.0):
        if time.time() - getattr(self, "_vs_at", 0) > max_age:
            self._vs = self.view_state()
            self._vs_at = time.time()
        return getattr(self, "_vs", "")

    def at_desk_surface(self):
        """畫面下半有沒有桌面類的東西——有的話「下面很平」是桌子，不是地板。"""
        dets, stamp = self.eyes.latest()
        if not stamp or time.time() - stamp > 2.5:
            return False
        for lab, arr in (dets or {}).items():
            l = str(lab).lower()
            if not any(w in l for w in self.DESK_LIKE):
                continue
            for d in arr[:2]:
                if not self.eyes.is_self(d) and d["cy"] > 0.45:
                    return True
        return False

    def pitch_from_eyes(self):
        """用 YOLO 框到的東西在畫面上的高度判斷俯仰。

        比紋理判斷可靠：天花板有格線、地板有磁磚，紋理法都會誤判成「水平」，
        但東西（桌子、門、人、螢幕）如果全擠在畫面下半，就一定是在看天花板。
        回傳 (說明, 需要的修正方向)：+1 要往下看、-1 要往上看、0 不用修。
        """
        # 2026-09-14 晚：**優先用反射層的語意判斷**（它會說「天花板／地板／水平」）。
        # 框位置法在極端角度會搞反：正對地板時椅腳擠在畫面最底部（cy≈0.9），
        # 這裡就把它判成「東西都在下方＝在看天花板」，gyro_sync 再把角度硬改成 +35——
        # 實錄：低頭 61 度、送 dy +600 更往下，讀數卻翻成「抬頭 35 度」。
        self._pitch_src = ""
        pv, pt = getattr(self, "_seen_pitch", ("不確定", 0.0))
        # 客觀交叉檢查：這一分鐘明明淨往上轉很多，反射卻說地板（或反過來）→ 反射看錯，當作不確定
        try:
            net = self.dy_recent_sum()
            if (pv == "地板" and net <= -300) or (pv == "天花板" and net >= 300):
                pv = "不確定"
        except Exception:  # noqa: BLE001
            pass
        if time.time() - pt < 60 and pv != "不確定":
            if pv == "天花板":
                self._pitch_src = "反射"
                return "反射看到的是天花板（嵌燈／格板）＝你在抬頭，要往下看（正的 dy）", 1
            if pv == "地板":
                self._pitch_src = "反射"
                return "反射看到的是地板（椅腳／地毯／地面物）＝你在低頭，要往上看（負的 dy）", -1
            self._pitch_src = "反射"
            return "反射看到牆／物件／人＝視角大致水平", 0
        dets, stamp = self.eyes.latest()
        if not stamp or time.time() - stamp > 3.0:
            return "（眼睛暫時沒資料，無法判斷俯仰）", None      # None = 不知道，不是正常
        cys = [d["cy"] for v in dets.values() for d in v[:2]]
        if len(cys) < 2:
            # 框不到東西本身就是線索：多半正對著牆或天花板
            v = self.view_state()
            if "偏上" in v:
                self._pitch_src = "紋理"
                return "眼睛框不到東西，而且畫面上半部平坦＝多半在看天花板，往下修", 1
            if "偏下" in v and not self.at_desk_surface():
                self._pitch_src = "紋理"
                return "眼睛框不到東西，而且畫面下半部平坦＝多半在看地板", -1
            if "偏下" in v:
                return "畫面下半平坦但那是桌面（你在櫃檯前），不是地板——dy 不要動", 0
            # 2026-09-13：**絕對不要拿他自己講的話當俯仰證據。**
            # 他分不清天花板的格線與地板的磁磚縫（實測：畫面明明是磁磚地板，
            # 他說「天花板格紋」，程式就信了 → 判成抬頭 → 扣「鏡頭頂到天花板」
            # → gyro 被校正成 +35 → 提示叫他往下修 → 他更埋進地板。自我強化的錯誤迴路。
            # 改用他**實際送出去的 dy**：這是客觀紀錄，不會說謊。
            d = self.dy_recent_sum()
            if d >= 200:
                self._pitch_src = "dy"
                return (f"眼睛框不到東西；但你這一分鐘淨往下轉了 {d:.0f}"
                        f"＝八成正低頭看地板，要往上修（負的 dy）"), -1
            if d <= -200:
                self._pitch_src = "dy"
                return (f"眼睛框不到東西；但你這一分鐘淨往上轉了 {abs(d):.0f}"
                        f"＝八成正對著天花板，要往下修（正的 dy）"), 1
            return ("（眼睛框不到東西，**現在量不出俯仰**——這種時候 dy 一律不要動，"
                    "先左右轉頭找到東西再說）"), None
        cys.sort()
        m = cys[len(cys) // 2]
        if m > 0.72:
            self._pitch_src = "YOLO"
            return f"眼睛框到的東西都擠在畫面下方（中位 {m:.2f}）＝你正在看天花板，要往下看（正的 dy）", 1
        if m < 0.38 and len(cys) >= 3 and not self.at_desk_surface():
            # 門檻 0.28 太嚴（低頭幾乎驗不出來），但只憑 1~2 個框就判「在看地板」又太吵，
            # 而且站在櫃檯前時桌面本來就佔畫面下半——那不是地板。三個條件都成立才算數。
            self._pitch_src = "YOLO"
            return f"眼睛框到的東西都擠在畫面上方（中位 {m:.2f}）＝你正在低頭看地板，要往上看（負的 dy）", -1
        if m < 0.38:
            return (f"東西偏畫面上方（中位 {m:.2f}），但框太少或你正對著桌面——"
                    f"不確定是不是低頭，dy 先不要動"), 0
        return f"眼睛框到的東西分布正常（中位 {m:.2f}），視角大致水平", 0

    def level_camera(self, tries=5, why=""):
        """把鏡頭修回水平。這是**程式自己動鏡頭**，所以每一次都要印出來（以前完全沒 log，
        導致「他又抬頭了」查不到兇手：其實是這裡每 5 輪自己往上抬）。

        往上抬特別危險（抬到天花板就卡住），所以：
          - 站在桌子／櫃檯前時畫面下半本來就是桌面，一律不往上抬
          - 往上一次最多 60，而且**每次呼叫最多抬一次**
          - 往下（看天花板時）維持果斷，一次 100
        """
        ups = 0
        for _ in range(tries):
            desc, need = self.pitch_from_eyes()          # 先信眼睛看到的東西在哪
            if need is None or need == 0:
                v = self.view_state()
                need = 1 if "偏上" in v else (-1 if "偏下" in v else 0)
                if need < 0 and self.at_desk_surface():
                    need = 0                              # 桌面不是地板
            need = need or 0
            if need > 0:
                print(f"   🔻 程式壓平視角：往下 100（{why or '定期校正'}／{desc[:24]}）")
                self.stat["程式壓頭"] = self.stat.get("程式壓頭", 0) + 1
                self.raw_look(0, 100)        # 在看天花板 → 果斷往下壓
            elif need < 0:
                if ups >= 1 or self.at_desk_surface():
                    print("   ⏸ 已經幫你抬過一次（或你正對著桌面），不再往上抬")
                    break
                # 連續好幾次校正都說「要往上」卻一直沒變正常＝下面那片平的不是地板
                # （是桌面、是牆、是關著的門），再抬下去就會慢慢把鏡頭推到天花板。
                streak = getattr(self, "_up_fix_streak", 0)
                if streak >= 2:
                    if time.time() - getattr(self, "_up_fix_pause", 0) > 600:
                        self._up_fix_pause = time.time()
                        self._up_fix_streak = 0
                    print(f"   ⏸ 已經連續 {streak} 次自動抬頭都沒有變正常＝下面那片平的不是地板，"
                          f"停止自動抬頭（改成左右轉頭找東西）")
                    self.pitch_hint = ("⚠ 程式連續幾次把鏡頭往上抬都沒用——你面前那片平的不是地板，"
                                       "是桌面或牆。**不要再動 dy**，改用 look 左右轉頭找目標。" + NL)
                    break
                self._up_fix_streak = streak + 1
                ups += 1
                print(f"   🔺 程式壓平視角：往上 60（{why or '定期校正'}／{desc[:24]}）")
                self.stat["程式抬頭"] = self.stat.get("程式抬頭", 0) + 1
                self.raw_look(0, -60)
            else:
                self.pitch = 0
                self._up_fix_streak = 0      # 回到正常＝剛才的抬頭是對的
                return True
            time.sleep(0.15)
        self.pitch = 0
        return False

    # ---------------- 眼睛
    def eyes_text(self):
        dets, stamp = self.eyes.latest()
        if not stamp:
            return "（眼睛還在暖機）"
        rows = []
        for k, v in dets.items():
            if not v:
                continue
            d = v[0]
            me = "（這是你自己）" if Eyes.is_self(d) else ""
            rows.append(f"{k} 在 ({d['cx']:.2f},{d['cy']:.2f}) 大小 {d['h']:.2f}{me}")
        return "、".join(rows) or "（什麼都沒框到）"

    def draw_eyes(self, im):
        from PIL import ImageDraw
        dets, stamp = self.eyes.latest()
        if not dets or not stamp or time.time() - stamp > 2.0:
            return im
        d = ImageDraw.Draw(im)
        W, H = im.size
        for lab, arr in dets.items():
            for det in arr[:2]:
                x0 = int((det["cx"] - det["w"] / 2) * W); y0 = int((det["cy"] - det["h"] / 2) * H)
                x1 = int((det["cx"] + det["w"] / 2) * W); y1 = int((det["cy"] + det["h"] / 2) * H)
                colour = (255, 140, 0) if Eyes.is_self(det) else (0, 255, 255)
                d.rectangle((x0, y0, x1, y1), outline=colour, width=3)
                d.text((x0 + 4, max(0, y0 - 12)),
                       ("你自己" if Eyes.is_self(det) else f"{lab} {det['score']:.2f}"), fill=colour)
        return im

    def dump_prompt(self, text):
        """把這一輪真正送進模型的提示整份寫出來（system + user），方便使用者檢查。

        `最新提示.txt` 永遠是最後一輪；每 100 輪另外存一份 `提示存檔/` 當歷史。
        """
        self._panel_text = text          # 2026-09-26：留給 log_panel 存整輪面板（fine-tune 要完整輸入）
        try:
            sysp = self.sys_prompt()
            body = ("===== system（這一輪載入的提示模組："
                    + "、".join(getattr(self, "_prompt_used", []) or ["?"]) + "） =====" + NL + sysp + NL * 2
                    + "===== user（每輪重算；圖片另外送：遊戲畫面，必要時再加地圖／放大圖） =====" + NL + text)
            with open(os.path.join(self.dir, "最新提示.txt"), "w", encoding="utf-8") as fh:
                fh.write(f"（第 {self.stat.get('輪數', 0)} 輪　"
                         f"{datetime.now().strftime('%m/%d %H:%M:%S')}）" + NL + body)
            if self.stat.get("輪數", 0) % 100 == 1:
                d = os.path.join(self.dir, "提示存檔")
                os.makedirs(d, exist_ok=True)
                with open(os.path.join(d, f"{datetime.now().strftime('%m%d_%H%M%S')}.txt"),
                          "w", encoding="utf-8") as fh:
                    fh.write(body)
        except OSError:
            pass

    def take_reasoning(self):
        """拿出上一次模型呼叫的內部推理（reasoning_content），順便留一份給 思考流程／儀表板。"""
        try:
            import play_agent as _pa
            t = str(_pa.LAST_REASONING.get("text") or "")
            _pa.LAST_REASONING["text"] = ""
        except Exception:  # noqa: BLE001
            t = ""
        if t:
            self._last_reasoning = t
        return t

    def log_panel(self, mode, content_use, raw):
        """2026-09-26 使用者要做 fine-tune：把每一輪真正送進模型的 user 面板＋模型原始輸出存下來。

        以前只有 最新提示.txt（只留最後一輪）與每 100 輪一份存檔，成功軌跡的輸入只剩反射那句「看到」，
        訓練輸入跟實戰差太多。現在每輪一筆，寫進 記憶/面板/YYYY-MM-DD.jsonl.gz（gzip 追加，一天約幾 MB）。
        圖片不存（截圖已在 畫面/ 與 標註/），system 前綴固定、存 hash 就好。
        """
        try:
            import gzip
            import hashlib
            texts = [c.get("text", "") for c in content_use if isinstance(c, dict) and c.get("type") == "text"]
            sysp = self.sys_prompt(mode)
            rec = {"時間": datetime.now().strftime("%H:%M:%S"), "輪": self.stat.get("輪數", 0),
                   "模式": str(mode), "流程": str(getattr(self, "flow", "")),
                   "區": str(getattr(self, "cur_zone", "") or ""), "任務": getattr(self, "cur_task", "") or "",
                   "反射看到": getattr(self, "_seen_reflex", "") or "",
                   "system_hash": hashlib.md5(sysp.encode("utf-8")).hexdigest()[:12],
                   "system_len": len(sysp), "user": NL.join(texts), "輸出": raw or "",
                   "推理": self.take_reasoning()}     # 2026-09-29：模型內部推理全文（以前只跑不存）
            d = os.path.join(self.dir, "面板")
            os.makedirs(d, exist_ok=True)
            with gzip.open(os.path.join(d, datetime.now().strftime("%Y-%m-%d") + ".jsonl.gz"), "ab") as fh:
                fh.write((json.dumps(rec, ensure_ascii=False) + NL).encode("utf-8"))
            if not getattr(self, "_sys_dumped", False):
                self._sys_dumped = True
                with open(os.path.join(d, f"system_{rec['system_hash']}.txt"), "w", encoding="utf-8") as fh:
                    fh.write(sysp)
        except Exception as e:  # noqa: BLE001
            print(f"   面板存檔失敗：{e}")

    def instr_text(self):
        """使用者寫在 任務指示.txt 的交代。

        以前只餵最後 300 個字，整份交代裡除了最新那一段以外全部被丟掉
        （使用者交代過的走位原則、優先順序、按 E 之前的檢查……他根本沒看到）。
        """
        s = (self._read_instr() or "").strip()
        if not s:
            return "（沒有）"
        if len(s) <= 2500:
            return s
        return "（前面較舊的交代先省略）" + NL + s[-2500:]

    def load_prompt_parts(self):
        """讀 提示/*.md（照基礎遊玩手冊的章節拆好的系統提示模組）。

        跟 工作流程/*.md 同樣的做法：內容放檔案、程式只負責載入，
        使用者隨時可以改檔案，下一輪就生效（用檔案的修改時間判斷要不要重讀）。
        每份檔案開頭的 --- 區塊寫：章節（對應手冊哪一節）、適用（什麼情境才載入）。
        """
        dirs = [d for d in CORE_DIRS if os.path.isdir(d)]
        proj = project_dir(self.game, "提示")
        if os.path.isdir(proj):
            dirs.append(proj)
        if not dirs:
            dirs = [os.path.join(HERE, "提示")]              # 舊結構
        files = []                                            # (顯示名, 完整路徑)
        for d in dirs:
            try:
                for x in sorted(os.listdir(d)):
                    if x.endswith(".md") and not x.startswith(("README", "_", ".")):
                        files.append((x, os.path.join(d, x)))
            except OSError:
                pass
        files.sort(key=lambda t: t[0])
        sig = []
        for n, full in files:
            try:
                sig.append((n, os.path.getmtime(full)))
            except OSError:
                pass
        if getattr(self, "_prompt_sig", None) == sig:
            return self._prompt_parts
        parts = []
        for n, full in files:
            try:
                raw = open(full, encoding="utf-8").read()
            except OSError:
                continue
            meta, body = {}, raw
            if raw.startswith("---"):
                head, _, rest = raw[3:].partition("---")
                for line in head.strip().split(chr(10)):
                    if ":" in line:
                        k, _, v = line.partition(":")
                        meta[k.strip()] = v.strip()
                body = rest
            body = body.strip()
            if not body or str(meta.get("停用", "")).strip() in ("是", "true", "1"):
                continue
            when = [x.strip() for x in
                    str(meta.get("適用", "全部")).replace("、", ",").split(",") if x.strip()]
            parts.append({"檔": n, "夾": os.path.basename(os.path.dirname(full)),
                          "章節": meta.get("章節", ""), "適用": when or ["全部"], "內容": body})
        self._prompt_sig, self._prompt_parts = sig, parts
        print(f"[提示] 從 提示/ 載入 {len(parts)} 份模組："
              + "、".join(x["檔"].split("_", 1)[-1][:-3] for x in parts))
        return parts

    def prompt_context(self):
        """現在算哪一種情境——決定要載入哪幾份提示模組。"""
        ctx = {"全部"}
        ctx.add("遊戲中" if getattr(self, "_where_kind", "") == "game" else "遊戲外")
        ctx.add("櫃檯作業" if getattr(self, "desk_lock", 0) >= 2 else "非櫃檯作業")
        ctx.add("認識櫃檯" if self.desk_need() else "熟悉櫃檯")
        ctx.add("流程:" + str(getattr(self, "flow", "")))
        return ctx

    def fill_vars(self, body):
        ppp = getattr(self, "px_per_pct", 4.0) or 4.0
        for k, v in (("{game}", self.game), ("{ppp}", str(round(ppp, 1))),
                     ("{deg_px}", str(int(ppp / 0.70))), ("{max_dx}", str(self.MAX_DX)),
                     ("{max_dy}", str(self.MAX_DY)), ("{quarter}", str(int(ppp * 25)))):
            body = body.replace(k, v)
        return body

    # 2026-09-20 使用者定的三層：反射只吃「基礎操作＋共用」，學習模組留給快思與慢想。
    REFLEX_DIRS = ("共用", "基礎操作")

    def sys_prompt(self, mode=None):
        """**固定前綴**：只放「適用: 全部」的模組，每一輪一字不差。

        2026-09-14 使用者要求改成前綴的形式。原因：
        vLLM 的 prefix cache 是對 token 序列的**共同前綴**做快取，
        前綴只要差一個字，後面整段都要重新 prefill。
        原本 sys_prompt 每輪依情境挑模組（遊戲中／櫃檯作業／流程:報到…），
        情境一換整份系統提示就變 → 兩萬多字每輪從頭算一次，快取一次都沒命中。
        現在固定的 63%（約 16,300 字）留在 system 當前綴，
        會隨情境變的 37% 移到 user 訊息裡（見 context_prompt）。
        """
        if getattr(self, "no_prompt_dir", False):
            self._prompt_used = ["（內建 SYS）"]
            return self.fill_vars(SYS.replace("{{", "{").replace("}}", "}"))
        parts = self.load_prompt_parts()
        if not parts:
            self._prompt_used = ["（內建 SYS）"]
            return self.fill_vars(SYS.replace("{{", "{").replace("}}", "}"))
        fixed = [x for x in parts if "全部" in x["適用"]]
        if mode == 反射:
            # 反射不需要「怎麼學習」那一套（回推、分數制、程序化、查資料…），
            # 它只要知道「怎麼操作」與「現在這一步」。前綴變短，反射也更容易出手。
            fixed = [x for x in fixed if x.get("夾") in self.REFLEX_DIRS or "這是" in x["檔"]]
        self._prompt_used = [x["檔"] for x in fixed]
        return self.fill_vars((NL * 2).join(x["內容"] for x in fixed))

    def context_prompt(self):
        """**每輪變動的那一段**：依情境挑出來的模組，放進 user 訊息，不污染前綴。"""
        if getattr(self, "no_prompt_dir", False):
            return ""
        parts = self.load_prompt_parts()
        if not parts:
            return ""
        ctx = self.prompt_context()
        picked = [x for x in parts if "全部" not in x["適用"]
                  and any(w in ctx for w in x["適用"])]
        if not picked:
            return ""
        self._prompt_used = getattr(self, "_prompt_used", []) + [x["檔"] for x in picked]
        return ("【現在這個情境才有的說明】" + NL
                + (NL * 2).join(x["內容"] for x in picked))

    # ---------------- 一輪
    PROPOSE_ASK = """【提案時間】現在**不趕時間**，停下來想清楚再回答。

使用者想看的是**你自己的判斷**：以你現在在做的事來說，什麼該加分、什麼該扣分、各值多少。

**這只是草案**：不會變成你的計分標準、不會影響你現在的分數、不會被駁回。
所以不要猜使用者想聽什麼，也不要替自己留退路——照你真正認為合理的寫。

怎麼想：
1. 分數要針對**你現在這個任務**（{flow}），不是泛泛的好習慣。
2. 一件事值多少，看它對「把這一班做完」有多大貢獻。會直接推進任務的值錢，只是過程的不值錢。
3. 扣分同理：會把進度清掉的扣得夠重，只是慢一點的不用扣太多。
4. 「怎麼檢查」要寫程式**看得到**的證據（畫面上哪個數字變了、任務提示變成什麼、
   什麼東西出現在畫面上）。檢查不了的項目等於沒訂。
5. 你現在的表如果有哪裡不合理，就提你認為對的，在「為什麼」裡講清楚差在哪。

加分至少 4 條、扣分至少 4 條。只輸出 JSON：
{{"加分":[{{"名":"","分數":0,"為什麼":"","怎麼檢查":""}}],
 "扣分":[{{"名":"","分數":0,"為什麼":"","怎麼檢查":""}}]}}"""

    def propose_session(self, topic=""):
        """停下來一次問完整張提案分數表（跟扣分檢討一樣，是程式安排的專門時段）。

        他每輪只有十秒、注意力全在畫面上，寫在任務指示裡的長交代會被排到後面。
        使用者要看他的判斷，就得給他一段不趕時間、只做這件事的時間。
        """
        topic = str(topic or "").strip()[:200]
        tline = (NL + NL + f"【這一次使用者指定的題目】{topic}" + NL
                 + "　這一輪只提這個題目的項目，別的先不要提；已經提過的不要重複。") if topic else ""
        print(f"   📝 提案時間{('：' + topic) if topic else ''}（不趕時間，不會生效）…")
        try:
            uri, _ = jpeg_data_uri(self.draw_eyes(draw_grid(self.grab_still(1024))), 70)
            raw, _, _, _ = stream_json(
                self.endpoint, self.model, self.sys_prompt(),
                [{"type": "image_url", "image_url": {"url": uri}},
                 {"type": "text", "text": "【你現在的計分板】" + NL + self.score_text() + NL + NL
                                          + "【你自己訂過的任務分數】" + NL + self.task_score_text() + NL + NL
                                          + "你最近做過的事：" + NL + NL.join(self.history[-10:]) + NL + NL
                                          + self.PROPOSE_ASK.format(flow=self.flow or "現在這件事")
                                          + tline}],
                2500, time.time() + 150.0, 0.5, effort=慢想)
        except Exception as e:  # noqa: BLE001
            print(f"   提案請求失敗：{e}")
            return 0
        d = parse_json_loose(raw) or {}
        n = 0
        for sign, key in (("正", "加分"), ("負", "扣分")):
            for row in (d.get(key) or [])[:10]:
                if not isinstance(row, dict):
                    continue
                row = dict(row)
                row["正負"] = sign
                try:
                    self.propose_score(row)
                    n += 1
                except Exception as e:  # noqa: BLE001
                    print(f"   寫提案失敗：{e}")
        print(f"   📝✅ 他提了 {n} 條（草案，不影響計分）")
        return n

    SEE_SYS = ("你是正在玩 Roblox 遊戲的玩家的眼睛。只回報畫面上**真的看得到**的東西，"
               "只輸出 JSON。不准形容氣氛、不准推測、不准編造沒看到的細節——"
               "你講的話會被存成記憶，寫錯會害到之後的自己。")
    SEE_ASK = ("看這張遊戲畫面，只回答「看得到什麼」。"
               "要寫：物件名稱與它在畫面的哪一邊、畫面上的文字（任務名、數字、按鍵提示）、"
               "有沒有 E／Press／按住 的互動提示、正前方是不是一片牆。"
               "另外判斷鏡頭在看哪裡——**用內容判斷，不是用格紋**："
               "看到嵌燈、燈管、管線、格板從上方延伸＝天花板；"
               "看到椅腳、桌腳、地毯、掉在地上的東西、自己的影子＝地板；"
               "看到牆、櫃檯、門、螢幕、人＝水平。牆面的灰色格紋**不是**天花板。"
               "**發光的白色方塊嵌在格板裡＝天花板的嵌燈，地板上不會有發光的東西**；"
               "格板邊緣接著木頭橫樑、畫面裡沒有任何腳或家具底部＝天花板。分不出就寫不確定。"
               "不要寫該做什麼，那不是你的工作。"
               "{yolo}"
               '只輸出 JSON：{"s":"(<=30字，全部是看得到的事實)","俯仰":"天花板|地板|水平|不確定",'
               '"yolo更正":[{"yolo":"它說的名字","其實是":"正確名字"}],"沒框到":["YOLO漏掉但重要的東西"]}')
    YOLO_HINT = ("【YOLO 眼睛這一刻框到的】（**框的位置可信，名字可能錯**）{rows}"
                 "→ 逐一確認名字：錯的寫進 yolo更正；它沒框到但畫面上明明有、而且重要的東西"
                 "（櫃檯設備、病人、按鈕、門）寫進 沒框到（用英文名，例如 printer）。沒有就給空陣列。")

    def yolo_rows(self):
        """把 YOLO 這一刻的框整理成一行一個，給反射當底稿。"""
        try:
            dets, stamp = self.eyes.latest()
        except Exception:  # noqa: BLE001
            return []
        if not stamp or time.time() - stamp > 3.0 or not dets:
            return []
        rows = []
        for lab, arr in dets.items():
            for d in arr[:2]:
                if d.get("score", 0) < 0.22 or self.eyes.is_self(d):
                    continue
                side = "左" if d["cx"] < 0.36 else ("右" if d["cx"] > 0.64 else "中")
                vert = "上" if d["cy"] < 0.36 else ("下" if d["cy"] > 0.64 else "中")
                size = "大" if d.get("area", 0) > 0.12 else ("小" if d.get("area", 0) < 0.01 else "中")
                rows.append(f"{lab}（{side}{vert}、{size}、{d['score']:.2f}）")
        return rows[:10]

    # YOLO 詞 → 腦中地圖上的點（互動點／地標／站位）。拍地標時會自動長出來；這幾個是已知的對應。
    # 2026-09-15 使用者原則：不能替他把 YOLO 的詞對到他的地點——那是關卡知識。
    # 這張表留空；詞↔地標的對應只能來自他自己拍地標時反射給的代表物（記憶/地標詞.json）。
    LANDMARK_ALIAS = {}

    def load_landmark_words(self):
        self.landmark_words = dict(self.LANDMARK_ALIAS)
        try:
            with open(os.path.join(self.dir, "地標詞.json"), encoding="utf-8") as f:
                self.landmark_words.update(json.load(f) or {})
        except Exception:  # noqa: BLE001
            pass

    def save_landmark_word(self, label, name):
        self.load_landmark_words() if not hasattr(self, "landmark_words") else None
        self.landmark_words[label] = name
        try:
            with open(os.path.join(self.dir, "地標詞.json"), "w", encoding="utf-8") as f:
                json.dump({k: v for k, v in self.landmark_words.items() if k not in self.LANDMARK_ALIAS},
                          f, ensure_ascii=False, indent=1)
        except Exception:  # noqa: BLE001
            pass

    def landmark_pose(self, name):
        """腦中地圖上這個名字的座標（與拍它時的朝向，若有）。找互動點→地標照片→站位→地點。"""
        v = (getattr(self, "spots", {}) or {}).get(name)
        if v:
            return float(v["x"]), float(v["y"]), v.get("朝向")
        v = (getattr(self, "marks", {}) or {}).get(name)
        if v:
            return float(v["x"]), float(v["y"]), v.get("yaw")
        v = (getattr(self, "stands", {}) or {}).get(name)
        if v:
            return float(v["x"]), float(v["y"]), v.get("yaw")
        v = (getattr(self, "map", {}) or {}).get(name)
        if v:
            return float(v[0]), float(v[1]), None
        return None

    def fix_count_recent(self):
        """開機後校正過幾次（第一次校正允許大跳，因為開機座標本來就是 0,0）。"""
        return int(getattr(self, "fix_count", 0) or 0)

    def localize_by_landmark(self):
        """看著畫面知道自己在小地圖的哪裡（使用者 09/15 要的）：
        YOLO 框到一個**綁定了地圖座標**的地標、而且框夠大（＝就在眼前）、又在畫面中央附近，
        就把推算座標拉到那個點的前方一步、朝向拉到當初拍它的朝向。同一地標 60 秒只校正一次。
        這是航位推算之外唯一「看畫面」的定位，讓程式執行期不必問大模型「我在哪」。
        """
        if not hasattr(self, "landmark_words"):
            self.load_landmark_words()
        try:
            dets, stamp = self.eyes.latest()
        except Exception:  # noqa: BLE001
            return
        if not stamp or time.time() - stamp > 2.0 or not dets:
            return
        import math
        now = time.time()
        seen = getattr(self, "_lm_fix_at", None) or {}
        for lab, arr in dets.items():
            name = self.landmark_words.get(str(lab).lower())
            if not name or not arr:
                continue
            d = arr[0]
            if d.get("score", 0) < 0.35 or d["h"] < 0.35 or abs(d["cx"] - 0.5) > 0.18 or self.eyes.is_self(d):
                continue
            if now - seen.get(name, 0) < 60:
                continue
            pose = self.landmark_pose(name)
            if not pose:
                continue
            x, y, yaw0 = pose
            th = math.radians(float(yaw0)) if yaw0 is not None else math.radians(getattr(self, "yaw", 0.0))
            ox, oy = self.pos[0], self.pos[1]
            jump = math.hypot(x - ox, y - oy)
            if jump > 20.0 and self.fix_count_recent() > 0:
                # 機制保險：一次校正把人瞬移 20 步以上＝地圖上那個點的舊座標本身就漂了，不採用
                seen[name] = now
                self._lm_fix_at = seen
                print(f"   🧭👁 看到地標「{name}」但它記的座標離現在 {jump:.0f} 步，太可疑，不用它校正")
                return
            # 站在它前方一步（面對它），所以我的位置＝它的位置往後退一步
            self.pos = [round(x - math.sin(th) * 1.0, 2), round(y - math.cos(th) * 1.0, 2)]
            oyaw = getattr(self, "yaw", 0.0)
            if yaw0 is not None:
                # 框偏左＝我面向它的右邊一點：yaw = 拍它時的朝向 + 偏移角
                self.yaw = (float(yaw0) + (d["cx"] - 0.5) * 70.0) % 360
            seen[name] = now
            self._lm_fix_at = seen
            self.fix_count = getattr(self, "fix_count", 0) + 1
            print(f"   🧭👁 看到地標「{name}」（{lab} 高 {d['h']:.2f}）→ 座標 ({ox:+.1f},{oy:+.1f})→({self.pos[0]:+.1f},{self.pos[1]:+.1f})"
                  + (f"、朝向 {oyaw:.0f}°→{self.yaw:.0f}°" if yaw0 is not None else ""))
            try:
                self.note_progress("看地標校正位置")
            except Exception:  # noqa: BLE001
                pass
            return

    def teach_eyes_landmark(self, name, im):
        """拍地標時順便教眼睛（使用者 09/15）：問反射這個地標最顯眼的東西英文叫什麼，
        把畫面連框存進訓練資料、那個詞加進 YOLO 詞彙、並記成「沒框到」讓訓練時補框。
        之後 YOLO 認得這個地標，就能反推自己在地圖的哪裡。"""
        if not self.screen_ok():
            return
        label = ""
        try:
            uri, _ = jpeg_data_uri(im, 60)
            raw, _, _, _ = stream_json(self.endpoint, self.model, self.SEE_SYS,
                                       [{"type": "image_url", "image_url": {"url": uri}},
                                        {"type": "text", "text": f"這裡是地標「{name}」。畫面裡**最能代表這個地標、"
                                         "而且固定不動**的一樣東西是什麼？用 1～3 個英文單字（YOLO 偵測詞），"
                                         '例如 "vending machine"、"blue door"。只輸出 JSON：{"label":"..."}'}],
                                       60, time.time() + 12.0, 0.5, effort=反射)
            label = str((parse_json_loose(raw) or {}).get("label") or "").strip().lower()[:30]
        except Exception:  # noqa: BLE001
            pass
        frame = datetime.now().strftime("%Y%m%d_%H%M%S_") + str(self.stat.get("輪數", 0)) + "_地標"
        try:
            self.save_label(im, name=frame)
        except Exception:  # noqa: BLE001
            pass
        if label:
            self.save_landmark_word(label, name)
            if label not in self.custom_labels:
                self.custom_labels.add(label)
                try:
                    self.eyes.watch(sorted(set(getattr(self, "watching", []) or []) | {label} | set(self.OBSTACLE_KEEP)))
                except Exception:  # noqa: BLE001
                    pass
                self.save_stage()
            try:
                with open(os.path.join(self.dir, "眼睛更正.jsonl"), "a", encoding="utf-8") as f:
                    f.write(json.dumps({"時間": datetime.now().strftime("%m/%d %H:%M:%S"), "畫面": frame,
                                        "yolo更正": [], "沒框到": [label], "地標": name}, ensure_ascii=False) + NL)
            except Exception:  # noqa: BLE001
                pass
            self.eye_fix_n = getattr(self, "eye_fix_n", 0) + 1
        print(f"   📷👁 拍地標「{name}」順便教眼睛：{('代表物 ' + label) if label else '（反射沒給代表物）'}，畫面已存進訓練資料")

    def note_eye_corrections(self, o, yolo_rows):
        """反射說 YOLO 認錯或漏掉的東西 → 記下來（這就是他教眼睛的素材），
        並把這一張畫面連同修正後的標籤存進訓練資料；漏掉的詞立刻加進 YOLO 的詞彙。"""
        if not self.screen_ok():
            return
        blob = json.dumps(o, ensure_ascii=False)
        if any(k in blob for k in ("PIN", "輸入您的", "股票", "天氣", "鎖定", "登入")):
            print("   👁✏ 這不是遊戲畫面（像是鎖定／登入畫面），不教眼睛")
            return
        fixes = [r for r in (o.get("yolo更正") or []) if isinstance(r, dict)
                 and str(r.get("yolo", "")).strip() and str(r.get("其實是", "")).strip()]
        missing = [str(w).strip()[:30].lower() for w in (o.get("沒框到") or []) if self.good_label(w)][:5]
        if not fixes and not missing:
            return
        name = datetime.now().strftime("%Y%m%d_%H%M%S_") + str(self.stat.get("輪數", 0))
        rec = {"時間": datetime.now().strftime("%m/%d %H:%M:%S"), "畫面": name,
               "yolo更正": [{"yolo": str(r["yolo"])[:30], "其實是": str(r["其實是"])[:30]} for r in fixes][:5],
               "沒框到": missing, "yolo當時框到": yolo_rows[:6]}
        try:
            with open(os.path.join(self.dir, "眼睛更正.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + NL)
        except Exception:  # noqa: BLE001
            pass
        self.eye_fix_n = getattr(self, "eye_fix_n", 0) + 1
        # 這一張畫面連框一起存（train_eyes.py 會套用更正、替漏掉的詞補框）
        try:
            im = getattr(self, "_frame_im", None)
            if im is not None:
                self.save_label(im, name=name)
        except Exception as e:  # noqa: BLE001
            print(f"   存更正畫面失敗：{e}")
        if missing:
            new = [w.lower() for w in missing if w.lower() not in self.custom_labels]
            if new:
                self.custom_labels |= set(new)
                try:
                    self.eyes.watch(sorted(set(getattr(self, "watching", []) or []) | set(new) | set(self.OBSTACLE_KEEP)))
                except Exception:  # noqa: BLE001
                    pass
                self.save_stage()
        print("   👁✏ 教眼睛：" + "；".join([f"{r['yolo']}→{r['其實是']}" for r in rec["yolo更正"]]
                                       + [f"沒框到 {w}" for w in missing]))

    # 2026-09-30 使用者：「試試看不寫」——看畫面那一步慢在**寫字**（約 80 個 token＝7 秒），不是慢在看。
    # 預設不叫模型寫描述：「看到什麼」由程式用 YOLO 的框＋OCR 的字組出來（0 秒），
    # 模型只回一個詞（鏡頭在看 天花板／地板／水平），水平儀校正照舊每輪都有。
    # 代價：這個模式下不收 yolo更正／沒框到（教眼睛的素材不累積）。
    # 要切回舊的（模型寫一句描述＋更正）：環境變數 GAMER_SEE_WRITE=on。
    SEE_WRITE = str(os.environ.get("GAMER_SEE_WRITE", "off")).lower() in ("on", "1", "yes", "開")
    SEE_PITCH_ASK = ("看這張遊戲畫面，只判斷鏡頭在看哪裡——用內容判斷，不是用格紋："
                     "看到嵌燈、燈管、管線、格板從上方延伸＝天花板；"
                     "看到椅腳、桌腳、地毯、掉在地上的東西、自己的影子＝地板；"
                     "看到牆、櫃檯、門、螢幕、人＝水平。牆面的灰色格紋不是天花板。"
                     "只輸出一個詞：天花板、地板、水平、不確定。")

    def seen_by_program(self):
        """不叫模型：用 YOLO 框到的東西＋OCR 讀到的字，組成「看到什麼」那一句。"""
        parts = []
        try:
            rows = self.yolo_rows()
            if rows:
                parts.append("框到 " + "、".join(r.split("、")[0] + "）" if "、" in r else r for r in rows[:5]))
        except Exception:  # noqa: BLE001
            pass
        try:
            lines, stamp = self.text_lines()
            if stamp and lines:
                words = [str(l["t"])[:10] for l in sorted(lines, key=lambda d: -d["h"])[:5]]
                parts.append("字：" + "／".join(words))
        except Exception:  # noqa: BLE001
            pass
        return ("；".join(parts) or "（眼睛沒框到東西、也沒讀到字）")[:60]

    def reflex_see_nowrite(self, uri):
        """看畫面但不寫描述：描述由程式組，模型只回俯仰一個詞。"""
        seen = self.seen_by_program()
        pv = "不確定"
        try:
            raw, _, _, _ = stream_json(
                self.endpoint, self.model, self.SEE_SYS,
                [{"type": "image_url", "image_url": {"url": uri}},
                 {"type": "text", "text": self.SEE_PITCH_ASK}],
                8, time.time() + 8.0, 0.2, effort=反射)
            pv = next((k for k in ("天花板", "地板", "水平") if k in str(raw or "")), "不確定")
        except Exception as e:  # noqa: BLE001
            print(f"   反射看俯仰失敗：{e}")
        self._seen_pitch = (pv, time.time())
        if pv != "不確定":
            seen = f"{seen}｜鏡頭在看{pv}"
        self.stat["反射看到"] = self.stat.get("反射看到", 0) + 1
        print(f"   👁 眼睛（程式組的，沒叫模型寫）：{seen}")
        return seen

    def reflex_see(self, uri):
        """反射層：先用**不思考**的模式看一眼畫面（使用者 2026-09-14 定的分工）。

        為什麼感知不能交給思考模式：思考區塊是自由生成、沒有格式約束的，
        模型會在裡面把恐怖遊戲的情境戲劇化，然後把推演出來的東西當成看到的寫出來
        （實測拿到過「牆壁裂縫滲血跡」「牆壁在呼吸」這種，而那個欄位會被存成記憶）。
        思考是用可驗證獎勵（數學、程式、邏輯）訓練出來的——那對**決策**有用，
        對「畫面上有什麼」這種感知題只會給它編造的空間。
        所以：看＝反射，做＝快思，診斷＝慢想。
        """
        if not self.SEE_WRITE:
            return self.reflex_see_nowrite(uri)
        try:
            rows = self.yolo_rows()
            yolo = (self.YOLO_HINT.format(rows=NL + NL.join("　" + r for r in rows) + NL) if rows else "")
            raw, _, _, _ = stream_json(
                self.endpoint, self.model, self.SEE_SYS,
                [{"type": "image_url", "image_url": {"url": uri}},
                 {"type": "text", "text": self.SEE_ASK.replace("{yolo}", yolo)}],
                320, time.time() + 14.0, 0.7, effort=反射)
            o = parse_json_loose(raw) or {}
            seen = str(o.get("s") or "").strip()[:60]
            self.note_eye_corrections(o, rows)
            pv = str(o.get("俯仰") or "").strip()
            pv = next((k for k in ("天花板", "地板", "水平") if k in pv), "不確定")
            self._seen_pitch = (pv, time.time())          # pitch_from_eyes 與 gyro_sync 優先用這個
            if pv != "不確定":
                seen = f"{seen}｜鏡頭在看{pv}"
            if seen:
                self.stat["反射看到"] = self.stat.get("反射看到", 0) + 1
                print(f"   👁 反射看到：{seen}")
            return seen
        except Exception as e:  # noqa: BLE001
            print(f"   反射看畫面失敗：{e}")
            return ""

    def screen_ok(self):
        """電腦鎖定或 Roblox 不在前景時回 False。

        2026-09-15 事故：電腦被鎖定，截圖回「BitBlt 存取被拒」、反射看到的是 Windows 鎖定畫面
        （PIN 碼、天氣、股票），他還把那些當遊戲教給眼睛、寫進訓練資料。
        畫面不是遊戲，這一輪就不該呼叫模型、不該記帳、更不該學任何東西。
        """
        try:
            import ctypes
            u = ctypes.windll.user32
            h = u.GetForegroundWindow()
            if h == 0:
                return False
            buf = ctypes.create_unicode_buffer(256)
            u.GetWindowTextW(h, buf, 256)
            title = buf.value or ""
            # 鎖定畫面的前景視窗標題是「Windows 預設鎖定畫面」／「Windows Default Lock Screen」
            if any(k in title for k in ("鎖定畫面", "Lock Screen", "LockApp")):
                return False
        except Exception:  # noqa: BLE001
            pass
        try:
            if not self.win.find():
                return False
        except Exception:  # noqa: BLE001
            return False
        try:
            self.grab_still(64)
        except Exception as e:  # noqa: BLE001
            if "BitBlt" in str(e) or "存取被拒" in str(e) or "ScreenShot" in type(e).__name__:
                return False
        return True

    def tick(self, name):
        """量每個環節花多少時間（使用者 2026-09-17 要研究時間都花在哪）。"""
        now = time.time()
        self._tk = getattr(self, "_tk", {})
        self._tk[name] = round(now - getattr(self, "_tk_at", now), 2)
        self._tk_at = now

    def cycle(self):
        t0 = time.time()
        self._tk, self._tk_at = {}, t0
        if not self.screen_ok():
            self._blind = getattr(self, "_blind", 0) + 1
            if self._blind in (1, 5) or self._blind % 30 == 0:
                print(f"   🔒 畫面拿不到（電腦鎖定或 Roblox 不在前景），第 {self._blind} 次——"
                      f"這一輪不呼叫模型、不記帳、不學東西；等畫面回來")
            if self._blind % 15 == 3:
                self.relaunch_roblox_if_gone()
            time.sleep(20)
            return
        if getattr(self, "_blind", 0):
            print(f"   🔓 畫面回來了（空等了 {self._blind} 輪）")
            self._blind = 0
        self.stat["輪數"] += 1
        try:
            self.place_watch()            # placeid 換了：進大廳歸零並錄路線、進醫院存路線
            self.watch_join()             # job 換了＝真的進了一場新的 → +30
            self.watch_repeats()          # 同一串動作重複十次 → 自動包成 function
        except Exception as e:  # noqa: BLE001
            print(f"   place_watch 失敗：{e}")
        try:
            self.localize_by_landmark()   # 看到綁了座標的地標就校正推算（程式與大模型兩條路都用）
        except Exception as e:  # noqa: BLE001
            print(f"   地標定位失敗：{e}")
        # 2026-09-15 程序化：現在專注的中任務有程式化步驟 → 程式自己做，這一輪不呼叫大模型
        try:
            if self.exec_mode_ready():
                self.exec_round()
                self.proc_supervise()        # 快思定期小檢查：方向對不對、該接哪一項
                return
        except Exception as e:  # noqa: BLE001
            print(f"   程式執行失敗，交回大模型：{e}")
            self.proc = None
        self._last_snapped = False
        # 專案夾放 提案.txt ＝ 下一輪停下來列一次提案分數表，列完刪掉旗標
        try:
            flag = os.path.join(os.path.dirname(os.path.abspath(__file__)), "提案.txt")
            if os.path.exists(flag):
                try:
                    topic = open(flag, encoding="utf-8").read().strip()
                except Exception:  # noqa: BLE001
                    topic = ""
                os.remove(flag)
                self.propose_session(topic)
        except Exception as e:  # noqa: BLE001
            print(f"   提案時間失敗：{e}")
        self.death_hint = ""
        self.aim_tip = ""
        self.pay_hint = ""
        self.item_hint = ""
        self._last_aim = None
        self._prev_move = ""
        # 貼牆先脫困（程式端，不浪費模型輪次）
        near, tex = self.wall_score()
        # 牆面有紋理時 wall_score 抓不到，但他自己會講「貼牆卡住」——他講的也算數
        said_stuck = any(k in str(getattr(self, "last_seen", "")) for k in ("貼牆", "卡住", "走不動", "卡牆"))
        flat, stds = self.wall_in_front()
        if flat:
            print(f"   🧱 三欄紋理 {['%.0f' % x for x in stds]} 都很低＝整片牆擋在前面")
        if near or said_stuck or flat:
            self.wall_rounds = getattr(self, "wall_rounds", 0) + 1
        else:
            self.wall_rounds = 0
        near = near or self.wall_rounds >= 2
        # 2026-09-13 使用者：「他一直面壁」。真因是這個扣分本來包在 g("脫困") 裡，
        # 而防線 09/12 全部關掉之後整段都不跑——**關掉防線不該連罰也一起關掉**。
        # 現在扣分獨立於防線：罰照罰（15 秒冷卻），幫不幫他脫困才看防線開關。
        if near and time.time() - getattr(self, "_wall_pen_at", 0) > 15:
            self._wall_pen_at = time.time()
            try:
                self.punish("面壁", f"你的臉貼在牆上（正前方紋理只有 {tex:.0f}，一片單色）。"
                                    f"貼著牆什麼都看不到、走也走不動——**先退後或橫移離開牆面**，"
                                    f"再轉身找看得到東西的方向。")
            except Exception:  # noqa: BLE001
                pass
        if near and self.g("脫困") and time.time() - getattr(self, "last_turn", 0) > 10:
            self.last_turn = time.time()
            n = self.wall_rounds
            print(f"   🧱 貼牆第 {n} 次（紋理 {tex:.0f}）")
            if n <= 2:
                self.turn_around(f"畫面紋理只有 {tex:.0f}，臉貼牆")
            elif n <= 5:
                # 轉身沒用 → 後退、跳一下（Roblox 卡在邊角常常跳一下就出來）、往程式估的開闊方向走
                print("   🧱 轉身沒用：後退＋跳＋往開闊的方向走")
                self.press_key("s", 1.2)
                self.press_key("space", 0.1)
                d = self.open_direction()
                if "最開闊的是 左邊" in d:
                    self.press_key("a", 1.5)
                    self.raw_look(-int(self.MAX_DX), 0)
                elif "最開闊的是 右邊" in d:
                    self.press_key("d", 1.5)
                    self.raw_look(int(self.MAX_DX), 0)
                else:
                    self.raw_look(int(self.MAX_DX * 2), 0)
                self.press_key("space", 0.1)
                self.press_key("w", 1.2)
            else:
                print("   🧱 一直貼牆，程式端脫困走位＋拉遠鏡頭")
                self.zoom_out("一直貼牆脫不了困")
                self.force_respawn()
                self.wall_rounds = 0
            self.stuck_review(f"臉貼在牆上第 {n} 次，畫面一片單色走不動")
            return
        # 鏡頭卡在天花板／地板：模型會一直丟 dy 但轉不動（多半是鏡頭貼牆的碰撞），
        # 連續幾輪都這樣就用程式端復位（後退、側移、滾輪拉遠、壓平視角、前進）。
        v = self.view_state()
        eye_desc, eye_need = self.pitch_from_eyes()
        eye_need = eye_need or 0
        if eye_desc:
            v = eye_desc + "；" + v
        if self.g("鏡頭復位") and ((eye_need or 0) or "偏上" in v or "偏下" in v):
            self.pitch_stuck = getattr(self, "pitch_stuck", 0) + 1
        else:
            self.pitch_stuck = 0
        if self.pitch_stuck >= 8:
            print(f"   🔄 鏡頭卡住 {self.pitch_stuck} 輪（{v[:20]}），程式端復位")
            self.force_respawn()
            self.level_camera(2)
            self.pitch_stuck = 0
            self.stuck_review(f"鏡頭卡住轉不動（{v[:20]}），連續 8 輪都只看得到天花板或地板")
            self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 鏡頭卡住轉不動，"
                                f"程式幫你後退、拉遠鏡頭、壓平視角；別再一直丟 look dy，先往後退離開牆邊")
            return
        try:
            bad, pid = self.wrong_game()
            if bad:
                self.wrong_game_rounds = getattr(self, "wrong_game_rounds", 0) + 1
                print(f"   🚨 現在的 place {pid} 不是「{self.game}」（第 {self.wrong_game_rounds} 輪）")
                if self.wrong_game_rounds >= 2:
                    self.wrong_game_rounds = 0
                    self.rejoin_correct_game(f"place {pid}")
            else:
                self.wrong_game_rounds = 0
        except Exception as e:  # noqa: BLE001
            print(f"   檢查遊戲身分失敗：{e}")
        # 2026-10-04 使用者「改掉」：他 esc＋l 離開之後人在 Roblox 首頁／別的遊戲的頁面，沒加入任何 place，
        # 上面那個檢查不會動 → 超過一分鐘還沒回到遊戲裡，程式送他回來
        try:
            if self.out_of_game():
                self._out_since = getattr(self, "_out_since", 0) or time.time()
                gone = time.time() - self._out_since
                if gone >= self.OUT_OF_GAME_SECS:
                    self.back_into_game(gone)
            else:
                self._out_since = 0
        except Exception as e:  # noqa: BLE001
            print(f"   檢查是否還在遊戲裡失敗：{e}")
        # 櫃檯的東西他一直對不準（電腦成功率只有兩成）：眼睛看得很清楚的時候，程式直接幫他對準按一次
        try:
            if (self.flow in ("報到", "治療") and not self.in_lobby()
                    and time.time() - getattr(self, "_auto_e_at", 0) > 60):
                best = None
                for lab in ("computer monitor", "printer", "counter desk"):
                    d = self.eyes.best(lab, max_age=2.0)
                    if d and d["score"] >= 0.28 and d["h"] >= 0.18 and abs(d["cx"] - 0.5) < 0.22:
                        if best is None or d["score"] > best[1]["score"]:
                            best = (lab, d)
                if best:
                    lab, d = best
                    self._auto_e_at = time.time()
                    # 只告訴他該怎麼做，不幫他做（要他自己學會對準與按 E）
                    self.aim_tip = (f"🎯 眼睛清楚框到「{lab}」在 ({d['cx']:.2f},{d['cy']:.2f})、"
                                    f"框高 {d['h']:.2f}。**用編號選它最準**："
                                    f'{{"t":"選","n":?,"做":"互動"}}（編號看【可選目標】），'
                                    f"或直接 aim 到這個座標再 hold e。" + NL)
        except Exception as e:  # noqa: BLE001
            print(f"   代按 E 失敗：{e}")
        # 死掉之後卡在死亡／旁觀畫面：那個畫面上的按鈕他點不動（今天已經浪費好幾個半小時），
        # 直接用深層連結重新加入大廳，這是唯一 100% 有效的脫困方式。
        try:
            seen0 = str(getattr(self, "last_seen", ""))
            dead = ((self.san is not None and self.san <= 1)
                    or any(k in seen0 for k in ("死亡畫面", "理智0", "理智 0", "你死了", "重生", "旁觀", "觀戰")))
            if dead:
                self._dead_since = getattr(self, "_dead_since", 0) or time.time()
                if time.time() - self._dead_since > 8:
                    self.death_hint = self.lobby_button_hint()
                if time.time() - self._dead_since > 60:
                    self._dead_since = 0
                    if self.rejoin_correct_game("死掉／卡在死亡畫面超過一分鐘"):
                        self.pos, self.yaw = [0.0, 0.0], 0.0
                        self.note_progress("重新加入")
                        self.flow, self.step = "開始上班", 1
            else:
                self._dead_since = 0
        except Exception as e:  # noqa: BLE001
            print(f"   死亡脫困失敗：{e}")
        # 理智快沒了又找不到咖啡：程式轉一圈找咖啡機／杯子並帶他過去（他一直死在這件事上）
        try:
            if (self.mod("理智") and self.san is not None and self.san <= 25 and not self.in_lobby()
                    and getattr(self, "desk_lock", 0) < 2
                    and time.time() - getattr(self, "_auto_coffee_at", 0) > 120):
                self._auto_coffee_at = time.time()
                known = next((n for n in ("咖啡機", "咖啡杯", "coffee machine")
                              if n in (getattr(self, "spots", {}) or {})), None)
                if known and self.san <= 15:
                    self.goto_remembered(known, why=f"理智只剩 {self.san}%，照腦中地圖走過去")
                else:
                    self.find_and_go(["coffee machine", "cup", "coffee"], why=f"理智只剩 {self.san}%")
        except Exception as e:  # noqa: BLE001
            print(f"   自動找咖啡失敗：{e}")
        # 在大廳卡住：他找不到入口（動物醫院那天卡了半小時）→ 幫他把門找出來。
        # 這是替他找路，等於替他玩：只有開了「輪班計畫」的遊戲才做，新遊戲一律他自己找。
        try:
            if (self.mod("輪班計畫") and self.in_lobby() and self.frozen_secs() > 90
                    and time.time() - getattr(self, "_auto_door_at", 0) > 120):
                self._auto_door_at = time.time()
                self.find_and_go(["door", "entrance", "elevator", "glowing purple object"],
                                 why="在大廳找不到入口")
        except Exception as e:  # noqa: BLE001
            print(f"   找入口失敗：{e}")
        # 報到卡住太久：程式直接用 YOLO 帶他走到櫃檯（他自己找不到路，這是最花時間的一段）
        try:
            if (self.flow in ("報到", "治療") and not self.in_lobby()
                    and getattr(self, "desk_lock", 0) < 2
                    and time.time() - getattr(self, "_auto_goto_at", 0) > 90
                    and not any(self.eyes.best(l, max_age=3.0)
                                for l in ("counter desk", "computer monitor", "printer"))):
                self._auto_goto_at = time.time()
                self.find_and_go(["counter desk", "computer monitor", "printer"],
                                 why=f"{int(self.frozen_secs())} 秒沒進展、又看不到櫃檯")
            elif (self.flow == "報到" and not self.in_lobby() and self.frozen_secs() > 90
                    and time.time() - getattr(self, "_auto_goto_at", 0) > 150):
                tgt = None
                for lab in ("counter desk", "computer monitor", "printer", "table"):
                    if self.eyes.best(lab, max_age=3.0):
                        tgt = lab
                        break
                if tgt:
                    self._auto_goto_at = time.time()
                    print(f"   🧭 你 {int(self.frozen_secs())} 秒沒有任何進展 → 程式用眼睛帶你走去「{tgt}」")
                    self.goto_target(tgt, budget=6.0)
                    self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] "
                                        f"你太久沒進展，程式幫你走到「{tgt}」前面；"
                                        f"接下來自己對正、看有沒有 E 提示再按")
        except Exception as e:  # noqa: BLE001
            print(f"   自動走到櫃檯失敗：{e}")
        try:
            self.remember_space()
        except Exception as e:  # noqa: BLE001
            print(f"   空間記憶失敗：{e}")
        try:
            self.ui_rescue()
        except Exception as e:  # noqa: BLE001
            print(f"   UI 脫困失敗：{e}")
        try:
            if self.stat.get("輪數", 0) % 20 == 0:
                self.save_map()          # 走過的路與撞牆點要定期存，不然重開探索區歸零
        except Exception as e:  # noqa: BLE001
            print(f"   存地圖失敗：{e}")
        if self.stat["輪數"] % 5 == 0 and getattr(self, "desk_lock", 0) < 2 and self.g("自動視角"):
            # 櫃檯作業中不要動鏡頭：那時候看桌面是對的，程式一插手就把他推去看天花板
            self.level_camera(2, why="每 5 輪定期校正")

        im, brightened = self.brighten(self.grab_still(1024))
        try:
            self.scene_changes(im)          # 變化偵測要用還沒畫框的原圖
        except Exception as e:  # noqa: BLE001
            print(f"   變化偵測失敗：{e}")
        try:
            self.reward_poll()              # 報到／輪班成功就發大獎
        except Exception as e:  # noqa: BLE001
            print(f"   發獎失敗：{e}")
        try:
            self.check_shop()               # 跳出購買框＝扣分
        except Exception as e:  # noqa: BLE001
            print(f"   購買框檢查失敗：{e}")
        try:
            self.verify_place(im)           # 用真的畫面印證腦中的地圖對不對
        except Exception as e:  # noqa: BLE001
            print(f"   印證失敗：{e}")
        try:
            if self.stat.get("輪數", 0) % self.LABEL_EVERY == 0:
                self.save_label(im)         # 邊玩邊存訓練資料
        except Exception as e:  # noqa: BLE001
            print(f"   存標註失敗：{e}")
        self._frame = im
        # 疑似被傳送：畫面整幅換掉，而這一輪他根本沒走幾步 → 提醒他「這可能是另一個空間」。
        # 只提醒，不替他切——切不切、叫什麼名字是他的判斷（使用者：學習時不要幫他）。
        try:
            prev = getattr(self, "_space_prev", None)
            if prev is not None:
                d = frame_diff(prev, im)
                moved = len(getattr(self, "trail", []) or [])
                if d > 28.0 and moved == getattr(self, "_space_trail_n", moved):
                    self.space_hint = (
                        f"🚪 畫面整幅換掉了（差異 {d:.0f}），而你這一輪幾乎沒有移動——**這很像被傳送了**。"
                        f"如果你認得出這是另一個場景，就切一個新空間："
                        '{"t":"切空間","name":"你取的名字","為什麼":"怎麼過來的"}；'
                        "如果只是開了選單或畫面閃一下，就不用切。" + NL)
                else:
                    self.space_hint = ""
            self._space_prev = im
            self._space_trail_n = len(getattr(self, "trail", []) or [])
        except Exception:  # noqa: BLE001
            self.space_hint = ""
        im = self.draw_eyes(draw_grid(im))
        self._frame_im = im
        uri, _ = jpeg_data_uri(im, 72)
        # 反射層先看一眼（不思考）。**一定要在組面板之前**：水平儀、扣分、視角建議
        # 全都要用這一輪的感知，放在後面等於用上一輪的。
        self.tick("截圖")
        self._seen_reflex = self.reflex_see(uri)
        self.tick("反射")
        self.gmap_tick()                   # 2026-09-30：陣列地圖（沒掃過的地方用視覺掃一圈；平常只核對）
        self.tick("陣列地圖")
        try:
            self.scene_tick()              # 2026-09-21：場景換了就先做基礎確認
            self.level_tick()              # 2026-09-23：等級表（開機一次；表改了就重審他自己的規則）
        except Exception as e:  # noqa: BLE001
            print(f"   場景比對失敗：{e}")
        try:
            self.check_click_effect()      # 上一次點擊把畫面變成什麼了？
        except Exception as e:  # noqa: BLE001
            print(f"   比對點擊效果失敗：{e}")
        try:
            self.gyro_sync()               # 反射剛看完，先用它校正再印
            self.gyro_save()
            _p = getattr(self, "pitch_deg", 0.0)
            _y = getattr(self, "yaw", 0.0) % 360
            _pt = "完全水平" if abs(_p) <= 3 else (f"抬頭 {_p:.0f} 度" if _p > 0 else f"低頭 {abs(_p):.0f} 度")
            print(f"   🧭 目前視角：{_pt}、面朝{self.compass(_y)} {_y:.0f} 度")
        except Exception:  # noqa: BLE001
            pass
        sc = self.score
        if brightened:
            self.dark_hint = "（畫面本來很暗，程式已經幫你提亮了；暗的地方理智掉得快，盡量走明亮的路）" + NL
        else:
            self.dark_hint = ""
        cur_task = getattr(self, "cur_task", "") or "（還沒讀到，這一輪先看右上角）"
        if getattr(self, "no_task_rounds", 0) >= 6:
            cur_task = ("（右上角已經連續好幾輪沒有提示了——這代表要你自己判斷該做什麼。"
                        "先看畫面線索，想不出來就用 wiki 自己查（YouTube 已停用），查完回來試。）")
        where, kind = self.where_am_i()
        self._where_kind = kind
        wfix = ""
        if kind == "game" and any(k in getattr(self, "last_seen", "") for k in ("首頁", "Play", "進入遊戲", "開始遊戲")):
            wfix = ("⚠ 你上一輪說這是「Roblox 首頁」——**不對**。程式確認前景就是 Roblox 遊戲視窗，"
                    "你已經在遊戲裡了，看到的是遊戲內的戶外大廳。不要再點 Play。" + NL)
            self.wrong_screen = getattr(self, "wrong_screen", 0) + 1
        text = self.drop_game_lines(ASK).format(task=cur_task, where=where, where_fix=wfix, stage=self.stage_text(), flow_list=self.flow_list(), flow=self.flow, step=self.step,
                          notes=self.memory_text(), map=self.map_text(), route=self.route_text(),
                          diffs=(NL.join("- " + x for x in getattr(self, "diffs", [])) or "（還沒比對過）"), manual=self.manual_text(),
                          steps=self.steps_text(), desk=self.desk_text(), stands=self.stands_text(),
                          course=self.course_text(), flow_doc=self.flow_doc(),
                          shift_plan=self.shift_plan_text(), frozen=self.frozen_hint(),
                          death_hint=getattr(self, "death_hint", "") or "",
                          pay_hint=getattr(self, "pay_hint", "") or "",
                          item_hint=getattr(self, "item_hint", "") or "",
                          aim_tip=getattr(self, "aim_tip", "") or "",
                          game_id=self.game_id_text(), place=self.place_text(), desk_layout=self.desk_layout_text(),
                          desk_scan=self.scan_text(),
                          organised=self.organised_text() or "（還沒整理過）",
                          history="\n".join(self.history[-4:]) or "（還沒開始）", view=v, look_up_hint=getattr(self, "look_up_hint", "") or "", up_n=getattr(self, "up_count", 0), down_n=self.stat.get("想低頭被擋", 0), gyro=self.gyro_text(), plan=self.plan_text(), changes=self.changes_text(), explore=self.explore_text(), ocr=self.text_text(), goal=self.goal_text(), tasktree=self.task_tree_text(), proc=self.proc_text(), proc_hint=getattr(self, "proc_hint", "") or "", score=self.score_text(), clock=self.clock_text(), tasks=self.task_score_text(), lessons=self.lessons_text(), verify=self.verify_text(), last3=self.last3_text(), level=self.level_hint(),
                          pitch_hint=getattr(self, "pitch_hint", "") or "",
                          spin_hint=getattr(self, "spin_hint", "") or "",
                          hold_hint=getattr(self, "hold_fail_hint", "") or "",
                          open_dir=self.open_direction(), center=self.path_center(),
                          align=self.align_hint(), ycmd=self.yolo_command(),
                          obstacle=self.obstacle_text(),
                          turn_move_hint=getattr(self, "turn_move_hint", "") or "",
                          brake_hint=getattr(self, "brake_hint", "") or "",
                          two_part_hint=getattr(self, "two_part_hint", "") or "",
                          reward_hint=getattr(self, "reward_hint", "") or "",
                          no_award_hint=getattr(self, "no_award_hint", "") or "",
                          propose_hint=getattr(self, "propose_hint", "") or "",
                          train_hint=getattr(self, "train_hint", "") or "",
                          score_reset_hint=getattr(self, "score_reset_hint", "") or "",
                          punish_hint=getattr(self, "punish_hint", "") or "",
                          retry_hint=getattr(self, "retry_hint", "") or "",
                          task_hint=getattr(self, "task_hint", "") or "",
                          lesson_hint=getattr(self, "lesson_hint", "") or "",
                          study_hint=getattr(self, "study_hint", "") or "",
                          osc_hint=getattr(self, "osc_hint", "") or "",
                          blocked=self.blocked_hint(), longstuck=self.long_stuck_hint(),
                          esc_hint=getattr(self, "esc_hint", "") or "",
                          click_hint=getattr(self, "click_hint", "") or "",
                          leave_hint=getattr(self, "leave_hint", "") or "",
                          hold_block=getattr(self, "hold_block_hint", "") or "",
                          ui_hint=self.ui_open_hint(),
                          flow_lock=getattr(self, "flow_lock_hint", "") or "",
                          repeat_hint=getattr(self, "repeat_hint", "") or "",
                          dark_hint=getattr(self, "dark_hint", "") or "",
                          lesson=self.lesson_text(),
                          eyes=self.eyes_text(), cands=self.candidates(), lock=self.lock_text(),
                          buttons=self.buttons_text(), eprompt=self.e_prompt_text(),
                          dist=self.distance_text(), space=self.space_text(),
                          mindmap=self.mind_map_text(),
                          mirror=self.repeat_mirror(),
                          skills=self.skill_stats(), tries=self.try_text(), wins=self.wins_text(), wins_how=self.wins_how_text(), spaces=self.spaces_text(), know=self.know_text(), screen=self.screen_text(), zones=self.zones_text(), selfie=self.selfie_text(),
                          join=((getattr(self, "join_hint", "") + NL)
                                if getattr(self, "join_hint", "") and
                                self.stat.get("輪數", 0) <= getattr(self, "_join_ask_until", 0) else ""),
                          quickdeath=(self.quickdeath_text() + NL if self.quickdeath_text() else ""),
                          offscreen=(self.offscreen_text() + NL if self.offscreen_text() else ""),
                          watching="、".join(getattr(self, "watching", []) or self.eyes.labels),
                          checkin=self.stat.get("登記", 0),
                          reject=self.stat.get("拒絕", 0), shift=sc.get("最高輪班", 0),
                          coin=sc.get("最高金幣", 0), instr=self.instr_text())
        # 一次性的提示送出去就清掉，不然會一直掛在提示上
        self.click_effect_hint = ""
        self.check_hint = ""
        self.reward_hint = self.punish_hint = self.retry_hint = self.task_hint = ""
        self.no_award_hint = ""
        self.propose_hint = ""
        self.train_hint = ""
        self.proc_hint = ""
        self.score_reset_hint = ""
        self.lesson_hint = ""
        self.study_hint = ""
        content = [{"type": "image_url", "image_url": {"url": uri}}]
        if getattr(self, "map", None) and getattr(self, "show_map", False):
            try:
                muri, _ = jpeg_data_uri(self.map_image(), 70)
                content.append({"type": "image_url", "image_url": {"url": muri}})
                text = ("（第一張是遊戲畫面，第二張是你自己走出來的**圖示版俯視地圖**：綠線是你走過的路"
                        "（＝可以走的地方，走廊的形狀就是這樣長出來的）、紅點是你撞到牆的位置、"
                        "青點是你記的地點、黃框是站位、橘箭頭是你自己與朝向。"
                        "用地形認自己在哪、怎麼走回去）" + NL + text)
            except Exception as e:  # noqa: BLE001
                print(f"   畫地圖失敗：{e}")
        if getattr(self, "zoom_img", None) is not None:
            try:
                zuri, _ = jpeg_data_uri(self.zoom_img, 82)
                content.append({"type": "image_url", "image_url": {"url": zuri}})
                text = (f"（最後一張是你上一輪要求放大的「{self.zoom_of}」，用它來看細節、比差異）" + NL + text)
            except Exception:  # noqa: BLE001
                pass
            self.zoom_img = None
        try:
            self.maybe_compile()          # 學習期：有散文步驟、沒程序 → 請大模型編譯一次
        except Exception as e:  # noqa: BLE001
            print(f"   自動編譯失敗：{e}")
        cx = self.context_prompt()
        if cx:
            text = cx + NL + NL + text
        _dup = self.macro_dup_text()
        if _dup:
            text = text + NL + NL + _dup
        _ck = self.check_text()
        if _ck:
            text = text + NL + _ck
        _chint = getattr(self, "check_hint", "")
        if _chint:
            text = _chint + NL + text
        _last = self.last_step_text()
        if _last:
            text = _last + NL + text          # 上一步做了什麼 → 放最前面
        _need = self.basic_check_text()
        if _need:
            text = _need + NL + text          # 場景剛換 → 放更前面
        self.follow_sample()
        _fol = self.follow_text()
        if _fol:
            text = _fol + NL + NL + text
        _ban = self.ban_text()
        if _ban:
            text = _ban + NL + NL + text      # 使用者的禁令放最前面（2026-09-27）
        _budh = getattr(self, "budget_hint", "")
        if _budh:
            text = _budh + NL + text
        _eff = getattr(self, "click_effect_hint", "")
        if _eff:
            text = _eff + NL + text
        _rep = self.repeat_alarm()
        if _rep:                      # 2026-09-18：重複警報放**最前面**，不要埋在兩萬字裡
            text = _rep + NL + NL + text
        _top = self.top_notes_text()
        if _top:                      # 2026-10-03 使用者：量比例提醒、他自己的檢討、視角矛盾——放最上面
            text = _top + NL + NL + text
        # 把反射看到的事實交給快思去決定要做什麼（反射本身已在截圖後跑過）
        if self._seen_reflex:
            text = (text + NL + NL
                    + "【你的眼睛剛才看到什麼】（這是不帶推理、只看畫面得到的事實，比你的印象可靠）"
                    + NL + "　" + self._seen_reflex + NL
                    + "　→ 這一輪的 s 就照這個寫，你的工作是決定 **a**（要做什麼）。")
        # 2026-09-17：短期記憶（最近幾輪的縮圖＋當時做了什麼）與他自己訂的死後改善
        try:
            content += self.short_mem_content()
        except Exception as e:  # noqa: BLE001
            print(f"   短期記憶組不起來：{e}")
        _imp = self.improve_text()
        if _imp:
            text = text + NL + NL + _imp
        _key = self.key_moves_text()
        if _key:
            text = text + NL + NL + _key
        _pt = self.points_text()
        if _pt:
            text = text + NL + NL + _pt
        _btn = self.btn_memo_text()
        if _btn:
            text = text + NL + NL + _btn
        _mac = self.macro_text()
        if _mac:
            text = _mac + NL + NL + text          # 大動作放最前面：他要先知道自己在做哪一步
        _doc = self.macro_doc()
        if _doc:
            text = text + NL + NL + "【這個大動作你自己寫的文檔】" + NL + _doc
        text = text + NL + NL + self.plan3_ask()      # 2026-09-29：快思要直接給三步的動作
        if self.review_status_text():
            text = text + NL + self.review_status_text()
        content.append({"type": "text", "text": text})
        self.dump_prompt(text)
        self.tick("組面板")
        self.stat["模型輪"] = self.stat.get("模型輪", 0) + 1
        # 思考強度由他自己決定（使用者 2026-09-16）：快節奏的遊戲可以自己降成反射，
        # 一輪從 40 幾秒縮到十幾秒；要想清楚的時候再自己調回快思。
        # 2026-09-29 使用者：**快思直接給前後三步的詳細動作；反射只檢查上一步有沒有正常執行。**
        # 快思那一輪回 步:[①②③]，①當場做；之後每輪反射看畫面判斷「上一步做成了嗎」——
        # 成了程式就做②、再做③；沒成就換快思重排。三步做完也回到快思。
        # 以前「每 4 輪快思一次、其餘反射自己決定動作」的節奏拿掉：反射不再決定動作。
        q = list(getattr(self, "_plan_q", []) or [])
        mode = 反射 if q else 快思
        if getattr(self, "_force_fast", False):     # 反射說上一步沒做成、或他自己喊了換快思
            self._force_fast = False
            self._plan_q = []
            mode = 快思
            print("   🧠 換快思：重新排前後三步")
        self._reflex_check = bool(q) and mode == 反射
        if mode == 快思 and not self.macro_steps() and self.macro_name():
            print(f"   🧠 「{self.macro_name()}」還沒有細步驟 → 這一輪請快思把它切開")
        # 2026-09-27：反射的 20 秒上限改成參數（微調 27B 比 Flash-Next 慢很多，20 秒答不完就變「沒動作」）
        dl = self.deadline if mode == 快思 else min(self.deadline, float(getattr(self.args, "reflex_deadline", 20.0) or 20.0))
        self.stat["快思輪" if mode == 快思 else "反射輪"] =             self.stat.get("快思輪" if mode == 快思 else "反射輪", 0) + 1
        # 2026-09-19 使用者更正：**反射輪要減少的是「思考量」，不是「提示」。**
        # 原本反射輪只送一份精簡提示（為了省 10 秒讀面板），代價是他那 3/4 的輪次
        # 看不到【目前視角】【標點】【按鈕簿】【死後改善】這些東西——
        # 他今天就因此「沒發現自己在抬頭」。現在反射輪一樣送整份面板，
        # 只是 effort=反射（不推理）＋token 少，該看的資訊一個都不少。
        # `--reflex-slim` 可以切回舊行為（純比速度時用）。
        if mode == 反射 and getattr(self.args, "reflex_slim", False):
            fast = ((self.repeat_alarm() + NL) if self.repeat_alarm() else ""
                    ) + ("看畫面，現在馬上做一件事。" + NL
                    + "【你在做什麼】" + str(getattr(self, "flow", "")) + NL
                    + "【右上角任務】" + (getattr(self, "cur_task", "") or "（沒讀到）") + NL
                    + "【眼睛看到】" + (getattr(self, "_seen_reflex", "") or "") + NL
                    + "【視角】" + self.gyro_text()[:200] + NL
                    + "【對正提示】" + self.align_hint() + NL
                    + "【左右牆距】" + self.path_center() + NL
                    + (("【提醒】" + NL + (getattr(self, "punish_hint", "") or "")
                        + (getattr(self, "lesson_hint", "") or "")
                        + (getattr(self, "proc_hint", "") or ""))[:600] + NL)
                    + "【你現在標的區】" + str(getattr(self, "cur_zone", "") or "（還沒標）") + NL
                    + (self.short_mem_text(3)[:600] + NL if self.short_mem_text(3) else "")
                    + (self.improve_text(2) + NL if self.improve_text(2) else "")
                    + '只輸出 JSON：{"s":"看到什麼(<=14字)","區":"你現在在哪一區(<=8字)",'
                      '"想":"為什麼做這個(<=20字)","a":[動作…最多2個],"task":"右上角任務原文"}')
            content_use = [{"type": "image_url", "image_url": {"url": uri}},
                           {"type": "text", "text": fast}]
            tokens_use = 400
        elif mode == 反射:
            # 2026-09-20：反射＝基礎操作的儀表＋現在這一步（不是整份面板、也不是一行提示）
            # 2026-09-29：有快思排好的佇列時，反射只做檢查（reflex_check_panel），不決定動作
            content_use = [{"type": "image_url", "image_url": {"url": uri}},
                           {"type": "text", "text": self.reflex_check_panel() if getattr(self, "_reflex_check", False) else self.reflex_panel()}]
            # 檢查輪只回 {"ok":true/false}（2026-09-30），上限壓小，答多了也不會拖時間
            # 2026-10-05：檢查輪可以多寫一句（30 字以內）→ 48 會把那一句切掉，放到 110；不寫的時候一樣只有十幾個 token
            tokens_use = 110 if getattr(self, "_reflex_check", False) else min(600, self.args.max_tokens)
        else:
            # 2026-10-05：快思開回推理，推理的 token 也算在上限裡，給到 1300 才不會「想完沒份寫 JSON」
            content_use, tokens_use = content, max(int(self.args.max_tokens), 1300)
        raw, trunc, ttft, _ = stream_json(self.endpoint, self.model, self.sys_prompt(mode),
                                          content_use,
                                          tokens_use, time.time() + dl, 0.2,
                                          # 2026-10-05 使用者：快思（每輪做決定的這一次）開回推理；其他小呼叫照舊不推理
                                          effort=("快思推理" if mode == 快思 else mode))
        self.tick("快思")
        self.log_panel(mode, content_use, raw)
        o = parse_json_loose(raw) or {}
        if getattr(self, "_reflex_check", False) and "ok" not in o:
            # 檢查輪只回 ok：他沒包成 JSON（只寫 true／false）也認得；兩個都沒有就當沒做成
            _r = str(raw or "").lower()
            if "true" in _r and "false" not in _r:
                o["ok"] = True
                o.setdefault("可以按", True)       # 整段只有 true、沒有 false＝兩個都是 true
            elif "false" in _r:
                o["ok"] = False
        try:
            o = self.plan3_apply(mode, o)      # 2026-09-29：快思的三步進佇列；反射檢查結果決定做下一步或換快思
            o = self.one_press_per_look(o)     # 2026-10-05：一次看畫面只按一個，其餘排到下一輪先確認再按
        except Exception as e:  # noqa: BLE001
            print(f"   三步佇列處理失敗：{e}")
        if not (o.get("a") or []):
            # 提示很長、又只有 5 秒，模型有時會來不及輸出動作 → 用精簡版重問一次
            short = ("看畫面，現在馬上做一件事。" + NL
                     + "【對正提示】" + self.align_hint() + NL
                     + "【左右牆距】" + self.path_center() + NL
                     + "只輸出 JSON：{\"s\":\"看到什麼(<=12字)\",\"a\":[動作...最多2個]}")
            raw2, _, _, _ = stream_json(self.endpoint, self.model, self.sys_prompt(),
                                        [{"type": "image_url", "image_url": {"url": uri}},
                                         {"type": "text", "text": short}], 400, time.time() + 10.0, 0.3,
                                        effort=反射)
            o2 = parse_json_loose(raw2) or {}
            self.tick("反射重問")
            if o2.get("a"):
                print("   ↻ 快思沒講完，用反射（不思考）重問後拿到動作")
                o["a"] = o2["a"]
                o.setdefault("s", o2.get("s"))
            else:
                # 2026-09-14：快思與反射都沒拿到動作＝**模型沒講完**，不是他不做事。
                # 以前這種輪次照樣罰「原地空轉」−100，一局 24 次就白扣 2400 分——
                # 那是我們自己的延遲造成的，不該記在他頭上。
                self._model_silent = True
                self.stat["模型沒回應"] = self.stat.get("模型沒回應", 0) + 1
                print(f"   🤐 快思與反射都沒拿到動作（第 {self.stat['模型沒回應']} 次）"
                      f"——這一輪不罰原地空轉，是模型沒講完不是他不動")
        stamp = datetime.now().strftime("%H:%M:%S")   # 後面寫 diff 與死亡紀錄都會用到（以前定義得太晚，他一死就 UnboundLocalError）
        seen = str(o.get("s") or "") or getattr(self, "_seen_reflex", "")
        self.last_seen = seen
        try:
            self.note_zone(o.get("區") or o.get("zone"))
            self._map_used = self.enforce_map(o, uri)
            self._last_think = str(o.get("想") or o.get("why") or "")
            self.vision_check()
        except Exception as e:  # noqa: BLE001
            print(f"   記區失敗：{e}")
        tk = str(o.get("task") or "").strip()[:20]
        if not getattr(self, "_reflex_check", False):     # 檢查輪不回 task，不算「沒回報任務」
            self.no_task_rounds = 0 if tk else getattr(self, "no_task_rounds", 0) + 1
        if tk and tk != getattr(self, "cur_task", ""):
            print(f"   🎯 右上角任務：{tk}")
            self.note_progress("任務換了")
            # 右上角任務換了＝上一件事完成了 → 把步驟的箭頭往前推一格
            # （他幾乎不會自己用「下一步」，箭頭永遠停在第 1 步，那個面板等於白給）
            d0 = self.steps.get(self.flow)
            if d0 and d0.get("steps") and d0.get("idx", 0) < len(d0["steps"]) - 1:
                d0["idx"] += 1
                self.save_steps()
                print(f"   📋 任務換了 → 步驟推進到第 {d0['idx'] + 1} 步：{d0['steps'][d0['idx']]}")
                self.history.append(f"[{stamp}] 任務換成「{tk}」，你的步驟推進到"
                                    f"第 {d0['idx'] + 1} 步：{d0['steps'][d0['idx']]}"
                                    f"（做完一步也可以自己用 下一步）")
            self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 右上角任務變成「{tk}」")
            self.add_note(f"（任務）右上角出現「{tk}」")
            try:
                self.task_log(getattr(self, "cur_task", "") or "", tk)   # 2026-10-12 使用者：任務完成要另外記錄
            except Exception as e:  # noqa: BLE001
                print(f"   任務紀錄寫不進去：{e}")
            self.cur_task = tk
        self.update_score(seen)
        if self.in_lobby() and self.flow != "開始上班" and "開始上班" in self.flows:
            print("   🏠 判定你在大廳 → 流程自動切成「開始上班」（先回醫院才有事做）")
            self.flow, self.step = "開始上班", 1
        cur_shift = self.score.get("目前輪班")
        if cur_shift is not None and cur_shift != getattr(self, "_shift_no", None):
            first = getattr(self, "_shift_no", None) is None
            self._shift_no = cur_shift
            self.shift_base_checkin = self.stat.get("登記", 0)
            self.no_patient = 0
            print(f"   🕐 進入輪班 {cur_shift}，這一班的報到數重新算")
            if not first:
                self.reset_scores(f"進入輪班 {cur_shift}")

        # 換流程 / 更新步驟（都是他自己回報的）
        # 理智危急：先提醒他物品欄有東西，真的快死了程式幫他用一次
        m = None
        try:
            import re as _re
            m = _re.search(r"理智\s*(\d{1,3})\s*%?", seen)
        except Exception:  # noqa: BLE001
            pass
        if m:
            self.san = int(m.group(1))
        if self.san is not None and self.san <= 0:
            self.dark_hint = ("☠ 理智已經 0% ——你死了。流程是：**esc 離開遊戲 → 回大廳 → 重新加入 → "
                              "確認自己在哪 → 繼續做事**，不要停在原地，也不要點自我復活（那要花錢）。"
                              + NL + (getattr(self, "dark_hint", "") or ""))
        elif self.san is not None and self.san <= 22:
            self.dark_hint = (f"⚠ 理智只剩 {self.san}%，快死了。畫面最下面那排格子是你的物品欄——"
                              f"如果裡面有咖啡杯，**按數字鍵 1 拿出來，再按左鍵喝掉**，這是最快回理智的方法。"
                              + NL + (getattr(self, "dark_hint", "") or ""))
        if self.san is not None and self.san <= 15 and time.time() - getattr(self, "_last_rescue", 0) > 60:
            self._last_rescue = time.time()
            print(f"   🚑 理智只剩 {self.san}%，程式幫你拿出物品欄第一格並使用一次")
            self.press_key("1", 0.05)
            time.sleep(0.4)
            self.execute([{"t": "click", "x": 0.5, "y": 0.5, "g": "使用手上的東西"}])

        # 手槍是陷阱：撿起來就彈購買視窗，他今天已經在這裡耗掉好幾段時間
        if any(w in seen for w in ("手槍", "gun", "槍")) and "購買" not in seen:
            self.item_hint = ("⚠ **地上那把手槍不要撿**：你一撿它就會跳出購買視窗，"
                              "然後你要花好幾輪才關得掉（今天已經發生很多次）。"
                              "那不是任務需要的東西——**繞過去，看你的步驟該做什麼**。" + NL
                              + (getattr(self, "item_hint", "") or ""))
        # 看到付費視窗的字：程式直接 esc，不要讓他在購買視窗前面耗時間
        # 2026-09-29 使用者：**拿掉「商店／解鎖／升級」**——大廳左側常駐按鈕就寫著「商店」，
        # 這條今天替他按了 160+ 次 esc，把 Roblox 選單打開，他整晚在「好友選單關不掉」裡打轉。
        # 只認真正付費視窗才會出現的字（跟 SHOP_WORDS 同一個原則）。
        if (any(k in seen.lower() for k in ("購買", "付費", "付款", "robux", "儲值", "確認購買", "立即購買", "buy now", "gamepass"))
                and time.time() - getattr(self, "_last_auto_esc", 0) > 30):
            self._last_auto_esc = time.time()
            self.buy_popups = getattr(self, "buy_popups", 0) + 1
            print(f"   🚫 偵測到付費視窗的字，程式直接按 esc（第 {self.buy_popups} 次）")   # 先印再按：【上一步】的「程式說」才對得上
            self.press_key("esc", 0.05)

        nf = str(o.get("flow") or "").strip()
        locks = getattr(self, "lock_flows", []) or ([self.lock_flow] if getattr(self, "lock_flow", "") else [])
        # 理智快沒了還把他鎖在報到／治療＝逼他死在櫃檯前；這種時候放行去補理智
        if locks and nf == "喝咖啡" and self.san is not None and self.san <= 20:
            print(f"   ☕ 理智只剩 {self.san}%，破例放行去補理智")
            locks = locks + ["喝咖啡"]
        if locks and nf and nf not in locks:
            print(f"   🔒 現在只做「{'、'.join(locks)}」，不准換成「{nf}」")
            self.flow_lock_hint = (f"⚠ 你想換去做「{nf}」——現在只做「{'、'.join(locks)}」，已經擋掉。"
                                   f"專心把 Check In 做出來，其他事之後再說。" + NL)
            nf = self.flow if self.flow in locks else locks[0]
        if (self.g("理智") and nf == "喝咖啡" and self.san is not None and self.san >= 20):
            print(f"   ⛔ 理智還有 {self.san}%（20% 以上都夠用），先做報到／治療")
            self.osc_hint = (f"⚠ 你想切去喝咖啡，但理智還有 {self.san}%——程式擋下來了。"
                             f"30% 以上一律先做報到或治療，補理智是理智低於 30% 才做的事。" + NL)
            nf = "報到"
        if nf and nf in self.flows and nf != self.flow:
            print(f"   📄 換流程：{self.flow} → {nf}")
            self.flow, self.step = nf, 1
            self.stat["換流程"] += 1
            self.flow_since = time.time()
        try:
            ns = int(o.get("step"))
            if 1 <= ns <= 20:
                self.step = ns
        except (TypeError, ValueError):
            pass
        self.save_progress()

        actions = o.get("a") if isinstance(o.get("a"), list) else []
        self._asked = [str(a.get("t")) for a in actions if isinstance(a, dict)]   # 2026-09-27：他這輪要了哪些工具（擋掉／轉換之前）
        self._asked_full = [dict(a) for a in actions if isinstance(a, dict)]      # 2026-09-29：工具執行完會被移出 actions，留一份給【上一步】摘要
        # 記位置／忘記位置：由他自己決定要記哪裡、叫什麼名字
        # 2026-09-27 使用者：「他不需要想怎麼走，只需要知道他在哪、要去哪，剩下直接用地圖計算」
        _dest = getattr(self, "_goto_place", "")
        self._routed = ""
        # 2026-09-29：反射檢查輪做的是快思排好的步驟，地圖帶路不要把它換掉
        # （22:41 實測：反射說①做成了→該做②「look 對準蓋印表格」，卻被地圖重問拿到的「要去」蓋掉，
        # 　改成 YOLO 閉環走 0.7 秒，那一步等於沒做，下一輪反射只能判「沒做成」）
        if _dest and getattr(self, "_reflex_check", False):
            print(f"   🗺⏸ 反射檢查輪：照快思排的步做，這輪不帶路去「{_dest}」")
            self._goto_place = ""
            _dest = ""
        try:
            if _dest and self.ui_open_hint():
                print(f"   🗺✋ 畫面被面板蓋著，這輪不帶路去「{_dest}」")
                _dest = ""
        except Exception:  # noqa: BLE001
            pass
        if _dest:
            self._goto_place = ""
            moves = [a for a in actions if isinstance(a, dict) and (
                (a.get("t") == "key" and str(a.get("k", "")).lower() in ("w", "a", "s", "d"))
                or (a.get("t") == "look" and a.get("dx")))]
            if moves:
                # 2026-10-04 使用者：「改掉」——地圖帶路不要蓋掉他自己的移動。
                # 10/04 農場遊戲 1200 輪裡拿掉 289 次：「要去」多半是地圖重問補的（開場空地／這裡旁邊），
                # 程式把他排好的 w 拿掉、再判「已經在旁邊了」→ 這輪變成沒動作 → 小步驟 −20 連扣。
                # 現在：他自己送了移動就照他的走；只有他沒送移動、只說「要去」時程式才帶路。
                print(f"   🗺⏸ 他自己送了 {len(moves)} 個移動動作，照他的走，這輪不帶路去「{_dest}」")
                self.stat["帶路讓給他"] = self.stat.get("帶路讓給他", 0) + 1
            else:
                self._routed = _dest
                print(f"   🗺➡ 他說要去「{_dest}」、自己沒送移動 → 用地圖算路線帶他走")
                try:
                    self.goto_remembered(_dest, why="他說要去")
                    self.stat["地圖帶路"] = self.stat.get("地圖帶路", 0) + 1
                except Exception as e:  # noqa: BLE001
                    print(f"   地圖帶路失敗：{e}")
        for a in list(actions):
            if not isinstance(a, dict):
                continue
            if a.get("t") == "鎖定":
                self.start_lock(str(a.get("target") or ""))
                actions.remove(a)
            elif a.get("t") == "追蹤":
                self.start_follow(str(a.get("target") or a.get("name") or ""))
                actions.remove(a)
            elif a.get("t") in ("停止追蹤", "解除追蹤"):
                print(f"   👁 停止追蹤「{getattr(self, 'follow_target', '')}」")
                self.follow_target, self.follow_hist = "", []
                actions.remove(a)
            elif a.get("t") == "解除鎖定":
                self.stop_lock("他自己解除")
                actions.remove(a)
            elif a.get("t") == "選":
                try:
                    self.pick(a)
                except Exception as e:  # noqa: BLE001
                    print(f"   選目標失敗：{e}")
                actions.remove(a)
            elif a.get("t") == "放大":
                try:
                    self.make_zoom(a)
                except Exception as e:  # noqa: BLE001
                    print(f"   放大失敗：{e}")
                actions.remove(a)
            elif a.get("t") == "走到":
                try:
                    self.goto_target(str(a.get("target") or ""))
                except Exception as e:  # noqa: BLE001
                    print(f"   走到失敗：{e}")
                actions.remove(a)
            elif a.get("t") == "找":
                labs = [str(x).strip()[:30].lower() for x in (a.get("labels") or []) if self.good_label(x)][:12]
                if labs:
                    # 他換偵測詞的時候，牆／櫃子／桌椅這些詞一定要留著：
                    # 以前直接整組換掉，YOLO 防撞與【前方障礙】就跟著失效了（他自己不會知道）。
                    keep = [x for x in self.OBSTACLE_KEEP if x not in labs]
                    self.eyes.watch(labs + keep)
                    self.watching = labs
                    self.custom_labels |= set(labs)
                    self.save_stage()
                    print(f"   👁 他叫眼睛改找：{'、'.join(labs)}")
                    self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 你把眼睛改成找：{'、'.join(labs)}")
                actions.remove(a)
            elif a.get("t") == "列步驟":
                sts = [str(x).strip()[:30] for x in (a.get("steps") or []) if str(x).strip()][:8]
                if sts:
                    self.steps[self.flow] = {"steps": sts, "idx": 0}
                    self.save_steps()
                    print(f"   📋 他替「{self.flow}」列了 {len(sts)} 步：" + "／".join(sts))
                actions.remove(a)
            elif a.get("t") == "下一步":
                d = self.steps.get(self.flow)
                if d and d["steps"]:
                    d["idx"] = min(d["idx"] + 1, len(d["steps"]) - 1)
                    self.save_steps()
                    print(f"   📋 進到第 {d['idx'] + 1} 步：{d['steps'][d['idx']]}")
                self.plan_advance()          # 慢想排的下三步也跟著往前推
                actions.remove(a)
            elif a.get("t") == "寫程序":
                try:
                    nm = str(a.get("名") or "").strip()[:16]
                    ok, bad = self.validate_steps(a.get("步驟") or [])
                    if nm and len(ok) >= 2:
                        old_p = self.procs.get(nm, {})
                        self.save_proc({"名": nm, "步驟": ok, "來源": "寫程序",
                                        "成功": int(old_p.get("成功", 0)), "失敗": int(old_p.get("失敗", 0))})
                        self.special_hint = ""
                        print(f"   🧩 他寫了程序「{nm}」{len(ok)} 步" + (f"，退回 {len(bad)} 步：{bad[0]}" if bad else ""))
                        self.proc_hint = (f"🧩 程序「{nm}」已存（{len(ok)} 步）。"
                                          + (f"被退回的：{'；'.join(bad[:3])}" if bad else "從下一輪起程式會照它做。") + NL)
                    else:
                        self.proc_hint = f"⛔ 程序沒存：{'；'.join(bad[:3]) or '至少要 2 步有效'}" + NL
                except Exception as e:  # noqa: BLE001
                    print(f"   寫程序失敗：{e}")
                actions.remove(a)
            elif a.get("t") == "編譯程序":
                try:
                    self.compile_proc(str(a.get("名") or (self.tree or {}).get("現在專注") or ""))
                except Exception as e:  # noqa: BLE001
                    print(f"   編譯程序失敗：{e}")
                actions.remove(a)
            elif a.get("t") == "執行程序":
                nm = str(a.get("名") or "").strip()
                if nm in self.procs:
                    self._proc_escalated_until = 0
                    self.proc_start(nm, why="他自己要求")
                    try:
                        self.proc["i"] = max(0, min(len(self.procs[nm]["步驟"]) - 1, int(a.get("從第幾步", 1)) - 1))
                    except (TypeError, ValueError):
                        pass
                    self.special_hint = ""
                else:
                    self.proc_hint = f"⛔ 沒有程序「{nm}」" + NL
                actions.remove(a)
            elif a.get("t") == "停止程序":
                self.proc_stop("他自己要求")
                self._proc_escalated_until = time.time() + 600
                actions.remove(a)
            elif a.get("t") == "教眼睛":
                try:
                    o = {"yolo更正": ([{"yolo": a.get("看到"), "其實是": a.get("其實是")}]
                                     if a.get("看到") and a.get("其實是") else []),
                         "沒框到": [a["新詞"]] if a.get("新詞") else []}
                    self.note_eye_corrections(o, self.yolo_rows())
                except Exception as e:  # noqa: BLE001
                    print(f"   教眼睛失敗：{e}")
                actions.remove(a)
            elif a.get("t") in ("包資料", "訓練眼睛"):
                try:
                    req = {"時間": datetime.now().strftime("%m/%d %H:%M:%S"), "張數": self.label_count(),
                           "更正": getattr(self, "eye_fix_n", 0), "誰要求": "他自己"}
                    with open(os.path.join(self.dir, "訓練請求.json"), "w", encoding="utf-8") as f:
                        json.dump(req, f, ensure_ascii=False)
                    print(f"   🧠 他要求訓練眼睛：現在有 {req['張數']} 張、更正 {req['更正']} 筆。"
                          f"收工時（GPU 空下來）會自動跑 train_eyes.py")
                    self.train_hint = ("🧠 收到，訓練眼睛會在**收工時**跑（現在跑會跟你搶 GPU）。"
                                       "在那之前多用 教眼睛 把認錯與漏掉的記下來，訓練資料才有料。" + NL)
                except Exception as e:  # noqa: BLE001
                    print(f"   訓練請求失敗：{e}")
                actions.remove(a)
            elif a.get("t") == "記問題":
                # 2026-09-27 使用者：他遇到問題要自己記下來（例如腦內地圖找不到自己在哪），使用者會定期看
                if str(a.get("問題") or a.get("內容") or "").strip():
                    self.record_problem(str(a.get("問題") or a.get("內容")), tried=str(a.get("試過") or ""),
                                        source="他自己")
                actions.remove(a)
            elif a.get("t") == "水平歸零":
                # 2026-09-26 使用者：他要知道怎麼修回水平、確保水平儀正確，每次登入優先調好。
                # 他自己看畫面確認已經水平之後，用這個把水平儀的上下讀數歸零（方位不動）。
                self.pitch_deg = 0.0
                self.pitch = 0
                self.pitch_hi = self.pitch_lo = 0.0
                print("   🧭 他確認畫面水平 → 水平儀上下歸零（方位不動）")
                self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 你確認畫面是水平的，"
                                    f"水平儀上下已歸零；之後的抬頭／低頭度數都從這裡算。")
                actions.remove(a)
            elif a.get("t") == "量刻度":
                try:
                    self.calibrate_axes("他自己要求")
                except Exception as e:  # noqa: BLE001
                    print(f"   量刻度失敗：{e}")
                actions.remove(a)
            elif a.get("t") in ("提案分數", "提案加分", "提案扣分"):
                try:
                    if a.get("t") == "提案扣分":
                        a["正負"] = "負"
                    self.propose_score(a)
                except Exception as e:  # noqa: BLE001
                    print(f"   提案分數失敗：{e}")
                actions.remove(a)
            elif a.get("t") in ("訂任務", "訂分數", "訂扣分"):
                try:
                    if a.get("t") == "訂扣分":
                        a["正負"] = "負"
                    self.set_task(a)
                except Exception as e:  # noqa: BLE001
                    print(f"   訂任務失敗：{e}")
                actions.remove(a)
            elif a.get("t") == "目標達成":
                self.goal_done(True)
                actions.remove(a)
            elif a.get("t") == "目標放棄":
                self.goal_done(False)
                actions.remove(a)
            elif a.get("t") == "拍地標":
                nm = str(a.get("name", "")).strip()[:24]
                im0 = getattr(self, "_frame", None)
                if nm and im0 is not None:
                    try:
                        self.snap_mark(nm, im0)
                        self.teach_eyes_landmark(nm, im0)     # 順便把這個地標教給眼睛
                    except Exception as e:  # noqa: BLE001
                        print(f"   拍地標失敗：{e}")
                else:
                    print("   拍地標要有 name（地名）")
                actions.remove(a)
            elif a.get("t") == "重想":
                print("   🐢 他自己要求重想：這一輪結束後馬上慢想一次")
                self._slow_due = True
                actions.remove(a)
            elif a.get("t") == "實驗":
                try:
                    self.run_experiment(a)
                except Exception as e:  # noqa: BLE001
                    print(f"   實驗失敗：{e}")
                actions.remove(a)
            elif a.get("t") == "設目標":
                nm = str(a.get("name") or "").strip()[:12]
                if nm in getattr(self, "map", {}):
                    self.goal = nm
                    print(f"   🚩 下一個目標設成「{nm}」")
                else:
                    print(f"   ⚠ 地圖上沒有「{nm}」（現有：{'、'.join(self.map)}）")
                actions.remove(a)
            elif a.get("t") == "記櫃檯":
                try:
                    self.record_desk(a)
                except Exception as e:  # noqa: BLE001
                    print(f"   記櫃檯失敗：{e}")
                actions.remove(a)
            elif a.get("t") == "掃視櫃檯":
                try:
                    self.scan_desk(a)
                except Exception as e:  # noqa: BLE001
                    print(f"   掃視櫃檯失敗：{e}")
                actions.remove(a)
            elif a.get("t") == "記因果":
                try:
                    self.record_cause(a)
                except Exception as e:  # noqa: BLE001
                    print(f"   記因果失敗：{e}")
                actions.remove(a)
            elif a.get("t") == "記流程":
                try:
                    self.record_chain(a)
                except Exception as e:  # noqa: BLE001
                    print(f"   記流程失敗：{e}")
                actions.remove(a)
            elif a.get("t") == "記遊戲":
                try:
                    self.record_game(a)
                except Exception as e:  # noqa: BLE001
                    print(f"   記遊戲失敗：{e}")
                actions.remove(a)
            elif a.get("t") in ("基礎確認", "確認狀態"):
                try:
                    self.basic_check(a)
                except Exception as e:  # noqa: BLE001
                    print(f"   基礎確認失敗：{e}")
                actions.remove(a)
            elif a.get("t") in ("併大動作", "合併大動作"):
                try:
                    self.merge_macros(a)
                except Exception as e:  # noqa: BLE001
                    print(f"   併大動作失敗：{e}")
                actions.remove(a)
            elif a.get("t") in ("排大動作", "排大動作序列"):
                try:
                    self.set_macros(a)
                except Exception as e:  # noqa: BLE001
                    print(f"   排大動作失敗：{e}")
                actions.remove(a)
            elif a.get("t") in ("寫步驟", "切步驟"):
                try:
                    self.write_macro_steps(a)
                except Exception as e:  # noqa: BLE001
                    print(f"   寫步驟失敗：{e}")
                actions.remove(a)
            elif a.get("t") in ("這步做完", "步驟完成"):
                self._step_done_round = self.stat.get("輪數", 0)   # 2026-09-27：照工作流程有進度時少排慢想
                try:
                    self.macro_step_done(a)
                except Exception as e:  # noqa: BLE001
                    print(f"   推進步驟失敗：{e}")
                actions.remove(a)
            elif a.get("t") in ("大動作完成", "換大動作"):
                try:
                    self.macro_done(a)
                except Exception as e:  # noqa: BLE001
                    print(f"   換大動作失敗：{e}")
                actions.remove(a)
            elif a.get("t") in ("換快思", "要想一下"):
                self._force_fast = True
                why = str(a.get("為什麼") or a.get("why") or "")[:40]
                try:
                    self.mark_step("喊了換快思" + (f"（{why}）" if why else ""))
                except Exception:  # noqa: BLE001
                    pass
                print(f"   🧠 反射說情況對不上，下一輪改用快思重新切步驟"
                      + (f"（{why}）" if why else ""))
                actions.remove(a)
            elif a.get("t") in ("記按鈕", "存按鈕"):
                try:
                    self.add_button_memo(a)
                except Exception as e:  # noqa: BLE001
                    print(f"   記按鈕失敗：{e}")
                actions.remove(a)
            elif a.get("t") in ("忘記按鈕", "刪按鈕"):
                try:
                    self.forget_button(a)
                except Exception as e:  # noqa: BLE001
                    print(f"   刪按鈕失敗：{e}")
                actions.remove(a)
            elif a.get("t") in ("標點", "留標點", "釘標點"):
                try:
                    self.add_point(a)
                except Exception as e:  # noqa: BLE001
                    print(f"   標點失敗：{e}")
                actions.remove(a)
            elif a.get("t") in ("量比例", "量深度比例"):
                try:
                    self.gmap_measure()
                except Exception as e:  # noqa: BLE001
                    print(f"   量比例失敗：{e}")
                actions.remove(a)
            elif a.get("t") in ("定比例", "定深度比例"):
                try:
                    self.gmap_set_scale(a)
                except Exception as e:  # noqa: BLE001
                    print(f"   定比例失敗：{e}")
                actions.remove(a)
            elif a.get("t") in ("記互動物", "記觸碰物"):
                try:
                    self.gmap_add(str(a.get("t"))[1:], a)
                except Exception as e:  # noqa: BLE001
                    print(f"   記物件失敗：{e}")
                actions.remove(a)
            elif a.get("t") in ("到標點", "對標點", "我在標點"):
                try:
                    self.hit_point(a)
                except Exception as e:  # noqa: BLE001
                    print(f"   對標點失敗：{e}")
                actions.remove(a)
            elif a.get("t") == "記站位":
                nm = str(a.get("name") or "").strip()[:12]
                if nm:
                    self.stands[nm] = {"x": round(self.pos[0], 2), "y": round(self.pos[1], 2),
                                       "yaw": round(getattr(self, "yaw", 0.0), 0)}
                    self.save_stands()
                    print(f"   🧍 他記下站位「{nm}」：x{self.pos[0]:+.1f} y{self.pos[1]:+.1f} 朝向 {self.yaw:.0f} 度")
                actions.remove(a)
            elif a.get("t") == "走到記過的":
                try:
                    self.goto_remembered(str(a.get("name") or ""))
                except Exception as e:  # noqa: BLE001
                    print(f"   走到記過的點失敗：{e}")
                actions.remove(a)
            elif a.get("t") in ("重新定位", "對齊視窗", "重新對齊"):
                try:
                    self.relocate_window()
                except Exception as e:  # noqa: BLE001
                    print(f"   重新定位失敗：{e}")
                actions.remove(a)
            elif a.get("t") in ("記自己", "記自己的樣子"):
                try:
                    self.record_self(a)
                except Exception as e:  # noqa: BLE001
                    print(f"   記自己失敗：{e}")
                actions.remove(a)
            elif a.get("t") in ("錄成程序", "錄程序"):
                try:
                    self.record_procedure(a)
                except Exception as e:  # noqa: BLE001
                    print(f"   錄成程序失敗：{e}")
                actions.remove(a)
            elif a.get("t") in ("點字", "點文字"):
                try:
                    self.click_text(a)
                except Exception as e:  # noqa: BLE001
                    print(f"   點字失敗：{e}")
                actions.remove(a)
            elif a.get("t") in ("記知識", "改知識", "刪知識", "升級知識"):
                try:
                    self.edit_knowledge(a)
                except Exception as e:  # noqa: BLE001
                    print(f"   改知識失敗：{e}")
                actions.remove(a)
            elif a.get("t") in ("改提示", "更新提示", "寫提示"):
                try:
                    self.edit_my_prompt(a)     # 2026-10-05 使用者：讓他自己去更新 Prompt
                except Exception as e:  # noqa: BLE001
                    print(f"   改提示失敗：{e}")
                actions.remove(a)
            elif a.get("t") in ("思考強度", "思考"):
                v = str(a.get("強度") or a.get("值") or a.get("v") or "").strip()
                fast = any(k in v for k in ("反射", "不思考", "快", "低", "fast"))
                slow = any(k in v for k in ("快思", "一般", "正常", "想", "normal"))
                if fast:
                    self.think_mode = 反射
                    print("   ⚡ 思考強度：**反射**（不思考，一輪最多 15 秒）——他自己降的")
                elif slow:
                    self.think_mode = 快思
                    print("   🧠 思考強度：**快思**（一輪最多 45 秒）——他自己調回來的")
                else:
                    print(f"   ⚠ 思考強度只能填「反射」或「快思」，收到：{v}")
                actions.remove(a)
            elif a.get("t") in ("切空間", "換空間", "新空間"):
                try:
                    self.switch_space(str(a.get("name") or a.get("名") or ""),
                                      str(a.get("為什麼") or a.get("why") or ""))
                except Exception as e:  # noqa: BLE001
                    print(f"   切空間失敗：{e}")
                actions.remove(a)
            elif a.get("t") == "我在":
                nm = str(a.get("name") or "").strip()[:12]
                if nm in getattr(self, "map", {}):
                    ox, oy = self.pos[0], self.pos[1]
                    self.pos = [float(self.map[nm][0]), float(self.map[nm][1])]
                    self.fix_count = getattr(self, "fix_count", 0) + 1
                    print(f"   🧭 位置校正：你說你在「{nm}」→ 座標從 ({ox:+.1f},{oy:+.1f}) "
                          f"改成 ({self.pos[0]:+.1f},{self.pos[1]:+.1f})")
                    self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] "
                                        f"你認出自己在「{nm}」，位置已校正（推算會漂，認得出來就要校正）")
                    self.note_progress("校正位置")
                else:
                    print(f"   ⚠ 地圖上沒有「{nm}」，先用 記位置 把它記起來")
                actions.remove(a)
            elif a.get("t") == "記位置":
                nm = str(a.get("name") or "").strip()[:12]
                if nm:
                    self.map[nm] = (round(self.pos[0], 2), round(self.pos[1], 2))
                    self.save_map()
                    print(f"   🗺 他把現在的位置記成「{nm}」")
                actions.remove(a)
            elif a.get("t") == "忘記位置":
                nm = str(a.get("name") or "").strip()
                if nm in self.map:
                    del self.map[nm]
                    self.save_map()
                    print(f"   🗺 他把「{nm}」從地圖刪掉了")
                actions.remove(a)
        self._has_turn = any(isinstance(a, dict) and a.get("t") in ("look", "aim") for a in actions)
        if not self._has_turn:
            self.turn_move_hint = ""
        self.two_part_hint = ""                 # 2026-10-12：拆兩段的提示只留一輪
        actions = [self.split_turn_move(a) for a in actions if isinstance(a, dict)]
        actions = [a for a in actions if a]
        actions = [self.snap_to_eyes(a) for a in actions if isinstance(a, dict)]
        actions = [self.tame_look(a) for a in actions if isinstance(a, dict)]
        actions = [a for a in actions if a]      # tame_look 回 None＝這個動作沒有意義，丟掉
        actions, blocked = self.guard(actions, 0)
        self._blocked_now = list(blocked)       # 2026-09-29：寫進【上一步】，讓他知道哪個動作被擋、為什麼
        self.stat["擋掉"] += len(blocked)
        for b in blocked:
            print(f"   🚫 擋掉 {b}")
        # 這一輪的做法先記著：成功的話要寫進成功紀錄（他最缺「上次是怎麼成功的」）
        aim = next((a for a in actions if a.get("t") in ("aim", "click")), None)
        if aim:
            try:
                self._last_aim = f"({float(aim.get('x', 0)):.2f},{float(aim.get('y', 0)):.2f})"
            except (TypeError, ValueError):
                self._last_aim = None
        mv = [a for a in actions if a.get("t") == "key" and str(a.get("k", "")).lower() in ("w", "a", "s", "d")]
        if mv:
            self._prev_move = "、".join(f"按 {a.get('k')} {float(a.get('d', 0)):.1f}s" for a in mv[:2])
        before = self.grab_still(320)
        # 追蹤目標：他這一輪講的東西，用來判斷「快到了要停」
        self._track = ""
        # 他這一輪有沒有講出「要去哪」——不限程式認得的標籤，他自己取的名字也算
        self._named = next((str(a.get("g") or "").strip() for a in actions
                            if str(a.get("g") or "").strip()), "")
        self._acts_now = list(actions)
        # 2026-09-13 這兩行以前沒有人叫：score_behaviour 整組行為扣分（原地空轉、
        # 大幅度轉頭、卡在選單、鏡頭頂到天花板／地板）從頭到尾都是死碼，
        # 所以懲罰明細裡永遠只有那三項。跟 09/09 「記櫃檯沒分派」是同一類錯。
        try:
            self.score_behaviour(actions)
            self.score_frozen()
        except Exception as e:  # noqa: BLE001
            print(f"   行為計分失敗：{e}")
        for a in actions:
            g = str(a.get("g") or "")
            lab = next((v for kk, v in self.SNAP.items() if kk in g), None)
            if lab:
                self._track = lab
                break
        rest = []
        self._his_exec = True                   # 2026-09-29：從這裡到 execute(rest) 動到遊戲的都是「他自己的動作」
        for a in actions:
            if a.get("t") == "middle":
                # 中鍵不是用來轉視角的——換成捲動視窗內容
                try:
                    dyv = float(a.get("dy", 0) or 0)
                except (TypeError, ValueError):
                    dyv = 0.0
                print("   ↕ 中鍵是捲動視窗內容，不是轉視角 → 改成捲動")
                self.execute([{"t": "scroll", "v": -3 if dyv > 0 else 3}])
                continue
            if a.get("t") == "key" and str(a.get("k", "")).lower() in ("w", "a", "s", "d"):
                kk = str(a["k"]).lower()
                if self.block_oscillation(kk):
                    continue
                if kk == "s":
                    self.press_key("s", float(a.get("d", 0.5) or 0.5))
                else:
                    _r0 = None
                    try:
                        if kk == "w" and self.gmap_on() and getattr(self, "gmap", None) is not None and not self.gmap.scale:
                            _r0 = self.gmap_look()
                    except Exception:  # noqa: BLE001
                        _r0 = None
                    _real, _ = self.walk_two_part(kk, a.get("d", 0.5), a)      # 2026-10-12：拆兩段、寧少勿多
                    a["實走"] = _real                # 2026-09-30：位置用實際走的秒數算（被叫停就沒走那麼遠）
                    try:
                        if _r0 is not None:
                            time.sleep(0.25)
                            self.gmap_sample(_r0, self.gmap_look(), _real)
                    except Exception as e:  # noqa: BLE001
                        print(f"   陣列地圖取樣失敗：{e}")
            elif a.get("t") == "look" and self.two_part_on():
                # 2026-10-12：轉頭也拆兩段。先過一遍跟 rest 一樣的防線（幅度、擋視角…），還是 look 才拆
                ga = self.guard_hold(self.guard_leave(self.guard_esc(self.guard_job(self.guard_pay_click(self.clamp_aim([a]))))))
                if ga and isinstance(ga[0], dict) and ga[0].get("t") == "look" and self.look_two_part(ga[0]):
                    continue
                rest.extend(ga)
            else:
                rest.append(a)
        self._busy = True
        rest = self.guard_hold(self.guard_leave(self.guard_esc(self.guard_job(self.guard_pay_click(self.clamp_aim(rest))))))
        pre_click = self.grab_still(320)
        try:
            self.execute(rest)
        finally:
            self._his_exec = False
        self.check_click(rest, pre_click)
        self._busy = False
        # 他自己提的操作加分：小幅度把歪掉的視角修回水平（用畫面判斷，不看推算角度）
        try:
            looks_all = [a for a in actions if a.get("t") == "look"]
            if looks_all and getattr(self, "_pitch_before", None) in (1, -1):
                small = all(abs(float(a.get("dx", 0) or 0)) < 100
                            and abs(float(a.get("dy", 0) or 0)) < 50 for a in looks_all)
                if small:
                    _, need_after = self.pitch_from_eyes()
                    if need_after == 0:
                        was = "看天花板" if self._pitch_before == 1 else "看地板"
                        self.op_award("精準的視角修正",
                                      f"你本來在{was}，用一次小幅度的 look（dx<100、dy<50）就修回水平了"
                                      f"——沒有一口氣甩過頭。")
            self._pitch_before = None
        except Exception:  # noqa: BLE001
            pass
        self.odometry(actions)          # 用他做的動作推算位置與朝向
        self.mark_trail()
        # 轉不動 = 已經頂到那一端：往下轉但畫面沒變 → 正對著地板；往上轉沒變 → 正對著天花板。
        looks = [a for a in actions if a.get("t") == "look" and int(float(a.get("dy", 0) or 0))]
        if looks:
            dy = int(float(looks[-1].get("dy", 0)))
            if frame_diff(before, self.grab_still(320)) < 3.0:
                self.pitch_hint = ("⚠ 你剛才往下轉但畫面完全沒變 → 鏡頭已經頂到最下面，你正對著地板；"
                                   "要看路請用負的 dy 往上轉。"
                                   if dy > 0 else
                                   "⚠ 你剛才往上轉但畫面完全沒變 → 鏡頭已經頂到最上面，你正對著天花板；"
                                   "要看路請用正的 dy 往下轉。")
                print("   🔁 " + self.pitch_hint)
                # 這是**量出來的事實**（轉了但畫面完全沒變＝已經頂到那一端），
                # 比 YOLO 推論的俯仰可靠得多：記下來，接下來擋掉同方向的 dy。
                if not (dy > 0 and (getattr(self, "desk_lock", 0) >= 2 or self.at_desk_surface())):
                    self.pitch_edge = "下" if dy > 0 else "上"
                    self.pitch_edge_at = time.time()
                # 「往下轉但畫面沒變」不一定是頂到最下面——櫃檯作業中鏡頭被鎖住、
                # 或畫面正對著桌面時也完全不會變。這種時候往回抬 90 就是把他推去看天花板。
                if dy > 0 and (getattr(self, "desk_lock", 0) >= 2 or self.at_desk_surface()):
                    self.pitch_hint = ("⚠ 你剛才往下轉但畫面沒變——你正在櫃檯作業（鏡頭被鎖住）或"
                                       "正對著桌面，這不是頂到下限。不要再動 dy，改用 look 左右轉或點畫面上的按鈕。")
                    print("   ⏸ 櫃檯作業／正對桌面：不做自動回轉（避免又被推去看天花板）")
                else:
                    self.raw_look(0, -60 if dy > 0 else 90)     # 程式先幫他往回轉一點（往上只轉 60）
                # 兩個方向都轉不動 = 鏡頭被夾在桌子/牆裡（不是頂到上下限）→ 拉遠鏡頭並退開
                self.no_turn = getattr(self, "no_turn", 0) + 1
                if self.no_turn >= 2:
                    self.no_turn = 0
                    self.zoom_out("上下都轉不動，鏡頭多半被夾在桌子或牆裡")
            else:
                self.pitch_hint = ""
                self.no_turn = 0
                # 轉得動了：如果是往頂端的反方向轉成功，就代表已經離開那一端
                edge = getattr(self, "pitch_edge", "")
                if (edge == "下" and dy < 0) or (edge == "上" and dy > 0):
                    self.pitch_edge = ""
        dfx = str(o.get("diff") or "").strip()
        if dfx:
            with open(os.path.join(self.dir, "差異筆記.md"), "a", encoding="utf-8") as f:
                f.write(f"- [{stamp}] {dfx}" + NL)
            self.diffs = (getattr(self, "diffs", []) + [dfx])[-6:]
            self.diff_count = getattr(self, "diff_count", 0) + 1
            print(f"   🔎 比對：{dfx}")
        if o.get("n"):
            self.add_note(str(o["n"]))

        # 有沒有真的做到報到（從他自己的描述抓）
        low = seen + " " + " ".join(str(a.get("g", "")) for a in actions)
        if any(k in low for k in ("Check In", "check in", "登記完成", "已登記")):
            self.stat["登記"] += 1
            print(f"   🎉 疑似完成 Check In（累計 {self.stat['登記']}）")
        if any(k in low for k in ("關快門", "快門關", "拒絕")):
            self.stat["拒絕"] += 1

        if any(k in seen for k in ("我死了", "死亡", "倒地", "理智歸零", "前往大廳")) \
                and time.time() - getattr(self, "last_death", 0) > 180:
            # 進場多久就死了（使用者 09/16：一開始就死＝局內沒時間想，要改成事後分析）
            born = getattr(self, "_enter_at", 0) or getattr(self, "_boot", 0)
            self._last_life = time.time() - born if born else None
            self._lives = (getattr(self, "_lives", None) or [])[-9:] + [self._last_life]
            self.last_death = time.time()
            self.pos, self.yaw = [0.0, 0.0], 0.0
            self.history.append(f"[{stamp}] 你死了，位置推算已歸零，地圖上的舊座標不準了，要重新記")
            self.death_count = getattr(self, "death_count", 0) + 1
            try:
                # 2026-09-23 使用者：死亡不扣分（只檢討）。原本 −10（09/19 前 −150）。
                print("   💀 你死了（不扣分，使用者 09/23 定；只做死亡檢討）")
                self.death_review()          # 會寫進 死亡檢討.md 並更新 self.death_note
                # 死亡檔會在背景寫完後由 _after_death_review 記筆記（2026-09-29 檢討背景化）
            except Exception as e:  # noqa: BLE001
                print(f"   死亡檢討失敗：{e}")

        self.osc_hint = ""
        self.look_up_hint = ""
        self.esc_hint = ""
        self.leave_hint = ""
        self.hold_block_hint = ""
        self.flow_lock_hint = ""
        # 2026-09-29 使用者：**有做事就不算沒動作，要鼓勵他調用工具**。
        # 工具（點字、這步做完、寫步驟、記知識…）執行完會被移出 actions，原本這裡就只剩「沒動作」，
        # 連「點字『是的』重新連線、畫面變化 25」都回灌成「做了『沒動作』」，他的檢討因此一直以為自己在空轉。
        summ_prev = "、".join(self.describe(a) for a in actions) or self.describe_tools() or "沒動作"
        # 重複動作偵測：一模一樣的動作連做好幾次都沒效果（整晚最常見的失敗模式）
        sig = json.dumps([{k: v for k, v in a.items() if k in ("t", "k", "g")} for a in actions],
                         ensure_ascii=False, sort_keys=True)
        if sig == getattr(self, "_last_sig", None) and sig != "[]":
            self.same_act = getattr(self, "same_act", 0) + 1
        else:
            self.same_act = 0
        self._last_sig = sig
        if self.same_act >= 2 and self.g("重複"):
            self.repeat_hint = (f"⚠ 你已經連續 {self.same_act + 1} 次做一模一樣的事（{summ_prev}），"
                                f"而且沒有任何進展。**同樣的做法不會突然有效**——這一輪換完全不同的做法："
                                f"換方向、換目標、或先站著把畫面看清楚。" + NL)
        else:
            self.repeat_hint = ""
        # 交替式空轉：他會在「按 w+a」與「轉頭」之間來回跳，讓「一模一樣」的計數永遠歸零。
        # 使用者指示：**不要擋、不要代打**，把事實攤給他看，讓他自己發現、自己改。
        self._sig_win = (getattr(self, "_sig_win", []) + [sig])[-10:]
        # 數字鍵是開關：一直按等於拿出來又收回去（他今天在喝咖啡卡了很多輪）
        try:
            digits = sum(1 for s in self._sig_win[-4:]
                         if '"k": "' in s and any(f'"k": "{d}"' in s for d in "123456789"))
            if digits >= 3 and self.frozen_secs() > 40:
                self.item_hint = ("⚠ **數字鍵 1~9 是開關**：按一次把東西拿到手上、**再按一次就收回去了**。"
                                  "你連續好幾輪都在按同一個數字鍵，等於一直拿出來又收回去。"
                                  "先看畫面下方的物品欄：那一格**亮起來／有框**就代表已經拿在手上——"
                                  "這時候**直接按左鍵或 E 使用**，不要再按數字鍵。" + NL)
        except Exception:  # noqa: BLE001
            pass

        if self.same_act >= 5:
            self.same_act = 0
            print("   🔁 連續 6 次同樣動作，程式幫你換方向走")
            self.raw_look(int(self.MAX_DX * 2), 0)
            self.press_key("w", 1.5)
            self.stuck_review("連續好幾輪重複同一個動作，完全沒有進展")

        # 原地打轉偵測：連續好幾輪只有轉頭／等待，沒有真的走動
        only_look = bool(actions) and all(a.get("t") in ("look", "wait", "aim") for a in actions)
        self.spin = (getattr(self, "spin", 0) + 1) if only_look else 0
        if self.spin >= 4 and self.g("打轉"):
            self.spin_hint = (f"⚠ 你已經原地轉了 {self.spin} 輪都沒有走動。轉頭是看不到新房間的——"
                              f"**這一輪請直接往你剛才看到最開闊的方向按 w 走過去**，走幾步再看。" + NL)
            print(f"   🌀 原地打轉 {self.spin} 輪，程式幫你往前走一段")
            self.press_key("w", 1.2)
            if self.spin >= 7:
                self.spin = 0
                self.stuck_review("一直原地轉圈找不到目標，沒有離開這個房間")
        else:
            self.spin_hint = ""
        summ = summ_prev
        dn = locals().get("d_now")
        if dn is None:
            try:
                dn = frame_diff(before, self.grab_still(320))
            except Exception:  # noqa: BLE001
                dn = -1.0
        eff = (f"畫面變化 {dn:.0f}" + ("（幾乎沒動）" if 0 <= dn < 3 else "")) if dn >= 0 else "效果不明"
        try:
            self.acc_note(summ, dn)        # 2026-10-05 使用者給的目標：先優先提高正確率 → 每一下有沒有反應都記下來給他看
        except Exception as e:  # noqa: BLE001
            print(f"   正確率記不進去：{e}")
        self.recent3 = (getattr(self, "recent3", []) + [f"做了「{summ}」→ {eff}"])[-3:]
        try:
            self.short_mem_push(seen, o.get("想") or o.get("why"), summ, eff)
        except Exception as e:  # noqa: BLE001
            print(f"   短期記憶寫不進去：{e}")
        try:
            self.macro_budget_tick()       # 2026-09-21：切幾步就要幾輪做完
        except Exception as e:  # noqa: BLE001
            print(f"   步數預算失敗：{e}")
        try:
            self.log_step(seen, summ, eff)
        except Exception as e:  # noqa: BLE001
            print(f"   小動作紀錄失敗：{e}")
        try:
            self.key_moves_tick(seen, summ, eff)
        except Exception as e:  # noqa: BLE001
            print(f"   關鍵動作追不下去：{e}")
        # 使用者 2026-09-17：把他的思考流程寫成文字紀錄（看到什麼→想做什麼→做了什麼）
        try:
            with open(os.path.join(self.dir, "思考流程.jsonl"), "a", encoding="utf-8") as _f:
                _f.write(json.dumps({
                    "時間": stamp, "輪": self.stat.get("輪數", 0),
                    "區": getattr(self, "cur_zone", ""),
                    "看到": seen[:120],
                    "想": str(o.get("想") or o.get("why") or "")[:80],
                    "推理": str(getattr(self, "_last_reasoning", "") or "")[:1200],   # 2026-09-29：模型內部推理
                    "做了": summ[:160],
                    "任務": getattr(self, "cur_task", ""),
                    "視角": f"{getattr(self, 'pitch_deg', 0):.0f}度/朝{getattr(self, 'yaw', 0) % 360:.0f}",
                }, ensure_ascii=False) + chr(10))
        except Exception:  # noqa: BLE001
            pass
        self.history.append(f"[{stamp}] {self.flow} 第{self.step}步｜看到：{seen} → {summ}")
        self._last_summ = summ
        self.history = self.history[-30:]
        self.tick("其他")
        print(f"[{stamp}] {self.flow} 第{self.step}步 {round(time.time() - t0, 1)}s｜{seen} → {summ}")
        tk = getattr(self, "_tk", {}) or {}
        if tk:
            print("   ⏱ " + "　".join(f"{k} {v:.1f}s" for k, v in tk.items() if v >= 0.05))
        self.log({"時間": stamp, "模式": f"流程:{self.flow}", "步驟": self.step, "看到": seen,
                  "動作": actions, "擋掉": blocked, "程式替你": (getattr(self, "_last_step_rec", {}) or {}).get("程式", ""),
                  "推理": str(getattr(self, "_last_reasoning", "") or "")[:1200],
                  "秒": round(time.time() - t0, 2),
                  "登記": self.stat["登記"],
                  # 2026-09-27 使用者：拆解每一步各花幾秒、用了幾個工具、有沒有用子代理（反射／快思／慢想這幾層模型呼叫）
                  "耗時": dict(tk), "要的工具": list(getattr(self, "_asked", []) or []),
                  "腦內地圖": getattr(self, "_map_used", ""),
                  "地圖帶路": getattr(self, "_routed", ""),
                  "模型": getattr(self, "model", "")})
        # 畫面長時間沒有變化＝卡住了，也要交報告
        d_now = frame_diff(before, self.grab_still(320))
        self.no_change = (getattr(self, "no_change", 0) + 1) if d_now < 3.0 else 0
        if self.no_change >= 8:
            self.no_change = 0
            self.same_screen = getattr(self, "same_screen", 0) + 1
            self.stuck_review(f"連續 16 輪畫面幾乎沒有變化（第 {self.same_screen} 次），做什麼都沒有反應")
            # 一直卡在同一個畫面 → 程式自己升級處理：關選單 → 脫困走位 → 離開重進
            if self.same_screen == 1:
                print("   🧰 卡在同一個畫面：先按 esc 關掉可能開著的視窗，再後退轉身")
                self.press_key("esc", 0.05)
                time.sleep(0.6)
                self.press_key("s", 0.8)
                self.raw_look(int(self.MAX_DX), 0)
            elif self.same_screen == 2:
                print("   🧰 還是同一個畫面：拉遠鏡頭 + 程式端脫困走位")
                self.zoom_out("一直卡在同一個畫面")
                self.force_respawn()
            elif self.same_screen >= 3 and time.time() - getattr(self, "last_rejoin", 0) > 600:
                print("   🧰 三次都卡在同一個畫面：離開遊戲重進")
                self.force_leave()
                self.same_screen = 0
        elif d_now > 8.0:
            self.same_screen = 0          # 畫面有在動就重新計數
            if self.stat.get("互動成功", 0) != getattr(self, "_last_ok", -1):
                self._last_ok = self.stat.get("互動成功", 0)
                self._stuck_since = None  # 有成功互動就當作脫離卡關
        # 沒有卡住跡象卻停在「卡住或死掉」流程 → 直接切回本業
        if (not getattr(self, "lock_flow", "") and self.flow == "卡住或死掉" and not near
                and (self.san is None or self.san > 5)
                and time.time() - (getattr(self, "_stuck_since", None) or 0) > 120):
            print("   ↩ 現在沒有卡住也沒死，切回「報到」")
            self.flow, self.step = "報到", 1
            self.flow_since = time.time()

        # 同一件事做太久沒有任何進展 → 強制換一件事做（避免整晚耗在同一個迴圈）
        if not hasattr(self, "flow_since"):
            self.flow_since = time.time()
            self.flow_base = (self.stat.get("登記", 0), self.stat.get("拒絕", 0))
        # 2026-09-19 使用者：**一個任務大約 5 分鐘**，五分鐘還沒達成就要他自己停下來檢討。
        # （原本是 8 分鐘才檢討一次，太鬆——他常常一個迴圈耗掉半小時才被點名。）
        if time.time() - self.flow_since > 300:
            no_progress = (self.stat.get("登記", 0), self.stat.get("拒絕", 0)) == getattr(self, "flow_base", (0, 0))
            if no_progress and not getattr(self, "lock_flow", ""):
                others = [f for f in self.flows if f != self.flow]
                # 使用者交代：地圖還沒摸熟之前，先探索而不是硬做報到
                if len(getattr(self, "map", {})) < 6 and "探索地圖" in others:
                    nxt = "探索地圖"
                else:
                    nxt = "報到" if "報到" in others else (others[0] if others else self.flow)
                print(f"   ⏱ 「{self.flow}」做了 5 分鐘還是沒進展，強制換成「{nxt}」")
                self.history.append(f"[{stamp}] 你在「{self.flow}」耗了 5 分鐘沒有任何進展，"
                                    f"程式幫你換成「{nxt}」——同樣的做法試不通就要換一件事做")
                self.stuck_review(f"在「{self.flow}」耗了 5 分鐘完全沒有進展（一個任務的預算就是 5 分鐘）")
                self.flow, self.step = nxt, 1
            self.flow_since = time.time()
            self.flow_base = (self.stat.get("登記", 0), self.stat.get("拒絕", 0))
        # 使用者要求：開始後先自己想辦法玩，**真的失敗了**才去看影片。
        # 所以不是照時間定期看，而是要先累積真實的失敗（死掉 2 次，或卡住檢討 5 次以上）。
        fails = getattr(self, "death_count", 0) - getattr(self, "_study_deaths", 0)
        stucks = getattr(self, "_stuck_count", 0) - getattr(self, "_study_stucks", 0)
        if ((fails >= 2 or stucks >= 5)
                and time.time() - getattr(self, "_last_study", 0) > 1800
                and time.time() - getattr(self, "_boot", 0) > 900):
            # 2026-09-16 使用者：**要不要查資料由他自己決定**，程式不再硬把他丟去查。
            # 舊版在這裡自動帶他查，查詢字串還寫死成別的遊戲，每次白丟三分半鐘。
            # 現在只提醒一次，他要查就自己送 wiki 動作，覺得查不到或沒用就跳過。
            self._last_study = time.time()
            self._study_deaths = getattr(self, "death_count", 0)
            self._study_stucks = getattr(self, "_stuck_count", 0)
            miss = int(getattr(self, "wiki_useless", 0) or 0)
            self.study_hint = (
                f"📖 你已經死 {fails} 次、卡住 {stucks} 次了。**要不要查資料你自己決定**："
                '想查就送 {"t":"wiki","q":"你自己想的關鍵字"}（會花三分半鐘）；'
                "覺得查不到、或查過沒用，就直接跳過，繼續自己試。"
                + (f"（你查過 {miss} 次都沒拿到東西）" if miss else "") + NL)
            print(f"   📖 提醒他可以查資料（死 {fails} 次、卡住 {stucks} 次）——查不查他自己決定")

        if self.stat["輪數"] % 10 == 0:
            self.check_stage()
        if self.stat["輪數"] % 20 == 0:
            self.save_stat()
        if time.time() - getattr(self, "_last_snap", 0) > 1800:
            self._last_snap = time.time()
            try:
                self.snapshot_save()
            except Exception as e:  # noqa: BLE001
                print(f"   存檔失敗：{e}")
        if time.time() - getattr(self, "_last_organise", 0) > 2400:
            self._last_organise = time.time()
            try:
                self.organise()
            except Exception as e:  # noqa: BLE001
                print(f"   整理失敗：{e}")
        if not ((getattr(self, "goal", None) or {}).get("目標")
                or (getattr(self, "goal", None) or {}).get("清單")) and self.stat.get("輪數", 0) % 6 == 0:
            try:
                self.set_goal()
            except Exception as e:  # noqa: BLE001
                print(f"   訂目標失敗：{e}")
        _periodic = self.stat.get("輪數", 0) % self.SLOW_EVERY == 0
        # 2026-09-27 使用者：學會的事拆成工作流程，快思照著走就知道上下步驟，**減少慢想**。
        # 現在的大動作有細步驟、而且最近 SLOW_EVERY 輪內有「這步做完」＝他正照流程往前走 → 例行慢想跳過；
        # 卡住（_slow_due）時照樣慢想。
        if _periodic and self.macro_steps() and                 self.stat.get("輪數", 0) - getattr(self, "_step_done_round", -999) <= self.SLOW_EVERY:
            _periodic = False
            self.stat["慢想跳過"] = self.stat.get("慢想跳過", 0) + 1
            print("   🐢⏭ 照工作流程有進度，這次例行慢想跳過")
        if _periodic or getattr(self, "_slow_due", False):
            self._slow_due = False
            try:
                self.slow_think()
            except Exception as e:  # noqa: BLE001
                print(f"   慢想失敗：{e}")
        try:
            self.do_pending_reviews()          # 欠的事後檢討，安全了就補
        except Exception as e:  # noqa: BLE001
            print(f"   事後檢討失敗：{e}")
        if time.time() - getattr(self, "_last_report", 0) > 1800:
            self._last_report = time.time()
            try:
                self.self_report()
            except Exception as e:  # noqa: BLE001
                print(f"   回報失敗：{e}")

    CATS = ["基礎操作", "遊戲模式", "地圖", "如何遊玩", "目標", "異常與危險", "資源與物品", "還不確定的"]

    ORGANISE = """【整理時間】把你到目前為止學到的東西，自己整理成一本「你自己的遊戲手冊」。
下面是你自己寫過的筆記（去重後、括號是重複學到幾次）、你自己實測的操作數值、你自己畫的地圖。
每條 <=30 字，只留你確定的；講同一件事的要合併成一條；不確定的放進「還不確定的」，之後再去驗證。

分成這幾個部分：
- 基礎操作：鍵盤滑鼠實際會造成什麼結果（你自己實測的數值，例如拖多少像素轉幾度、按 w 一秒走多遠）
- 遊戲模式：這個遊戲怎麼開始、怎麼結束、一回合是什麼、有哪些模式
- 地圖：醫院有哪些區域、什麼東西在哪裡、怎麼從一個地方到另一個地方
- 如何遊玩：一輪班從頭到尾要做什麼，順序是什麼
- 目標：你到底在追求什麼、錢怎麼來、成績怎麼算
- 異常與危險：什麼會害你死、怎麼判斷病人有沒有問題、遇到了怎麼處理
- 資源與物品：物品欄怎麼用、咖啡與道具在哪、怎麼補理智
- 還不確定的：你猜的、還沒驗證過的，寫下來之後去試

只輸出 JSON：{{"基礎操作":["..."],"遊戲模式":["..."],"地圖":["..."],"如何遊玩":["..."],"目標":["..."],"異常與危險":["..."],"資源與物品":["..."],"還不確定的":["..."]}}

你的筆記：
{notes}

你實測的操作數值：
{manual}

你的地圖：
{map}"""

    def organise(self):
        if not self._in_review_thread():
            return self.review_async("整理學習", self.organise)
        """讓他自己把學到的東西整理成一份分類文件（程式只負責觸發與保存）。"""
        print("   📚 整理時間：讓他自己把學到的東西分類整理")
        rows = sorted(self.mem.items(), key=lambda kv: -kv[1])[:40]
        notes = NL.join(f"- ({n}) {t}" for t, n in rows)
        raw, _, _, _ = stream_json(self.endpoint, self.model, self.sys_prompt(),
                                   [{"type": "text", "text": self.ORGANISE.format(
                                       notes=notes, manual=self.manual_text(), map=self.map_text())}],
                                   900, time.time() + 150, 0.3)
        o = parse_json_loose(raw) or {}
        if not o:
            print("   📚 整理失敗（沒解析到 JSON）")
            return
        p = os.path.join(self.dir, "學習整理.md")
        kdir = os.path.join(self.dir, "知識")
        os.makedirs(kdir, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(f"# 我自己的遊戲手冊（{datetime.now().strftime('%m/%d %H:%M')} 自己整理）" + NL + NL)
            for cat in self.CATS:
                items = [x.strip() for x in (o.get(cat) or []) if isinstance(x, str) and x.strip()]
                if not items:
                    continue
                f.write(f"## {cat}" + NL)
                for x in items:
                    f.write(f"- {x}" + NL)
                f.write(NL)
                # 每個部分也各存一份，方便單獨翻閱
                with open(os.path.join(kdir, f"{cat}.md"), "w", encoding="utf-8") as g:
                    g.write(f"# {cat}（我自己整理，{datetime.now().strftime('%m/%d %H:%M')}）" + NL + NL)
                    for x in items:
                        g.write(f"- {x}" + NL)
        self.organised = {k: [x for x in (o.get(k) or []) if isinstance(x, str)][:4]
                          for k in self.CATS}
        n = sum(len(v) for v in self.organised.values())
        print(f"   📚 整理完成，共 {n} 條 → {p}")
        self.log({"時間": datetime.now().strftime("%H:%M:%S"), "模式": "整理學習", "條數": n})

    def organised_text(self):
        d = getattr(self, "organised", None)
        if not d:
            return ""
        # 提示已經很長（6 秒截止內模型常常來不及輸出 JSON），手冊只留最相關的幾部分
        keep = ("如何遊玩", "目標", "異常與危險", "資源與物品")
        rows = [f"{k}：" + "／".join(v[:3]) for k, v in d.items() if v and any(x in k for x in keep)]
        return NL.join(rows)[:900]

    REPORT = """【每半小時的回報】用你自己的話跟使用者講：這半小時你在做什麼、學到什麼、卡在哪、接下來要怎麼做。
只輸出 JSON：{"做了什麼":"(<=60字)","學到什麼":"(<=40字)","卡在哪":"(<=40字)","接下來":"(<=40字)"}"""

    # ---------------- 慢想（不趕時間的那一層：等一下去哪、上一步、下三步）
    SLOW_EVERY = 12          # 每 12 個快思輪次慢想一次
    # 2026-09-14 實測：慢想（xhigh＋真實 2.8 萬 token 提示）純模型時間中位數 94 秒，
    # 最慢 102 秒——原本 120 秒的預算隨時會超時。改成 200 秒。
    SLOW_BUDGET = 150.0      # 2026-09-17 使用者：一分鐘；2026-10-01 慢想多讀整份面板＋medium 推理，100 秒仍寫不完（21:59 回了全空的 JSON）→ 150 秒

    SLOW_ASK = """【慢想時間】現在**不趕時間**，你有兩分鐘可以好好想。
快思那一層（每輪 10 秒）只負責移動和眼前的小判斷；**方向是這裡決定的**。

**先檢討，再排計畫。**（2026-09-22 使用者：慢想不是重排，是檢討）
上面【上一次慢想排的計畫】告訴你：上一個大動作叫什麼、切了幾步、已經花了幾輪、做到第幾步。
0. **它為什麼沒走完？** 卡在哪一步、那一步為什麼做不到（畫面沒變？點不到？被面板擋住？
   游標被鎖住？走不過去？）——用【這個大動作從頭到現在】那份紀錄回答，不要憑印象。
   然後決定：**沿用同一個名字、只改做不到的那一步**，還是目標本身錯了才換。
   **同一件事不准換名字**——名字一換，步驟和計輪就整個重來，你會忘記自己已經試過什麼。
   （你之前十次慢想給同一件事取了十個名字：「進場」「開始遊戲進場」「按開始遊戲進場」…）

看完畫面和你最近做過的事，再回答這幾件事：
1. 你現在在哪——**用剛才環顧的那四張圖判斷**，不要只憑推算的座標。
   四張分別是你面朝四個方位看到的樣子；哪一面有你認得的東西（櫃檯、走廊、拍照機、門），
   就用那一面來定位，再對照【目前視角】的 X／Y 跟【探索地圖】。
   如果四張都不認得，那就是你在沒去過的地方，老實說「不確定」，並且在下三步裡安排
   {"t":"拍地標","name":"地名"} 把這裡記下來。
2. **等一下要去哪**，為什麼要去那裡（要能推進你現在的流程，不是隨便走走）
3. 你**上一步**實際做了什麼、有沒有效
4. **現在這個大動作，完整的詳細步驟**（不是只排三步，是把這件事從頭到尾拆完）
5. **做完之後的下一個大動作**是什麼

「大動作」是一件有頭有尾、做完會看到結果的事；「詳細步驟」是它拆出來的每一個小動作。
　煮飯的例子：大動作＝**備料**，詳細步驟＝切青菜／切馬鈴薯／切蘿蔔／洗米；
　　　　　　　下一個大動作＝**煮菜**。
　這個遊戲的例子：大動作＝**報到這個病人**，詳細步驟＝走到櫃檯窗口／看病人四五秒／
　　　　　　　拍照等顯影／照片跟本人比對／看 CCTV 再比一次／正常就蓋章／電腦登記；
　　　　　　　下一個大動作＝**帶他去指定房間**。

規矩：
- **「現在的大動作」必須是【任務樹】裡「現在專注」的那個中任務，或它的一部分。**
  只有兩種例外可以先做別的：人還在大廳（先「開始上班」回醫院）、理智快沒了（先「喝咖啡」）。
  不准自己換成別的中任務——學會了程式會把 👉 移到下一個。
- 「下一個大動作」＝同一個中任務再做一次（還沒做穩），或任務樹裡的下一個中任務。
- 詳細步驟要**具體到可以直接做**（「轉到面朝東再直走兩秒到那塊踏板」可以，「找到踏板」不行）
- **上一次做不到的那一步，不准原樣再排一次**——要換做法（換路線、先關面板、先解游標、退出重進）
- 步驟數量看事情本身，三到八步都可以，**不要為了湊數硬拆**
- 「做到第幾步」要老實填：你現在卡在哪一步就填哪一步
- 下一個大動作只要名字＋一句為什麼，不用拆步驟（等這個做完再慢想一次）

只輸出 JSON：
{"上一個計畫失敗在":"(<=40字，哪一步、為什麼做不到)","沿用名字":true,
 "我在哪":"(<=30字)","怎麼看出來的":"(<=30字，講你在第幾張圖看到什麼)",
 "現在的大動作":"(<=16字，沿用就寫一樣的名字)","為什麼":"(<=40字)","上一步":"(<=40字)",
 "詳細步驟":["(<=30字)","(<=30字)","..."],"做到第幾步":1,
 "下一個大動作":"(<=16字)","下一個為什麼":"(<=30字)","要注意":"(<=40字)"}"""

    def look_around_shots(self):
        """慢想之前先環顧一圈：轉四次 90 度，每一面拍一張。

        四張一起看，他才判斷得出自己在地圖的哪裡——只看正前方那一張，
        任何一面白牆都長得一樣。轉完四次剛好回到原本的朝向，不會弄亂方位。
        """
        shots = []
        if self.dry or getattr(self, "desk_lock", 0) >= 2:
            return shots                      # 櫃檯作業中鏡頭是鎖住的，不要轉
        for _ in range(4):
            try:
                yaw = getattr(self, "yaw", 0.0) % 360
                im = self.grab_still(768)
                near, sc = "", 0.0
                for nm, rec in (getattr(self, "marks", {}) or {}).items():
                    v = self.sig_match(self.shot_sig(im), rec.get("sig"))
                    if v > sc:
                        near, sc = nm, v
                shots.append({"方位": self.compass(yaw), "度": round(yaw), "im": im,
                              "像": (f"（這一面跟你存的「{near}」有 {sc * 100:.0f}% 像）"
                                     if near and sc >= 0.55 else "")})
                self.turn_by(90)
                time.sleep(0.45)              # 等畫面轉完再拍下一張
            except Exception as e:  # noqa: BLE001
                print(f"   環顧失敗：{e}")
                break
        print(f"   🔄 環顧一圈拍了 {len(shots)} 張（"
              + "、".join(f"{x['方位']}{x['度']}度" for x in shots) + "）")
        return shots

    def panel_for_slow(self):
        """給慢想看的面板：快思那一輪的整份面板，但拿掉「情境說明」那一塊（那是核心文件，system 裡本來就有一份），
        免得 system＋面板＋四張環顧圖超過 65,536 的上下文。"""
        t = getattr(self, "_panel_text", "") or "（這一輪還沒組面板）"
        a = t.find("【現在這個情境才有的說明】")
        b = t.find("【程式確認：你現在看的是什麼視窗】")
        if 0 <= a < b:
            t = t[:a] + t[b:]
        return t[:60000]

    def slow_think(self):
        """慢想：不趕時間，重新決定方向與接下來三步。結果會回灌給每一個快思輪次。"""
        print("   🐢 慢想中（最多兩分鐘，不趕時間）…")
        try:
            shots = self.look_around_shots()
            content, faces = [], []
            for i, sh in enumerate(shots):
                u, _ = jpeg_data_uri(sh["im"], 66)
                content.append({"type": "image_url", "image_url": {"url": u}})
                faces.append(f"第 {i + 1} 張＝面朝{sh['方位']}（{sh['度']} 度）{sh['像']}")
            if not content:
                u, _ = jpeg_data_uri(self.draw_eyes(draw_grid(self.grab_still(1024))), 70)
                content.append({"type": "image_url", "image_url": {"url": u}})
                faces.append("（這次沒有環顧，只有正前方一張）")
            content.append({"type": "text",
                            "text": "【剛才環顧一圈拍的四面】" + NL + NL.join(faces)
                                    + NL + "先看完這四張，再判斷你到底在地圖的哪裡。"
                                    + NL + NL + "你最近做過的事：" + NL + NL.join(self.history[-15:])
                                    + NL + NL + "【目前視角】" + self.gyro_text()
                                    + NL + NL + "【畫面上的字】" + self.text_text()
                                    + NL + NL + "【探索地圖】" + self.explore_text()
                                    + ((NL + NL + "【任務樹】" + NL + self.task_tree_text())
                                       if self.task_tree_text() else "")
                                    + ((NL + NL + self.step_journal_text())
                                       if self.step_journal_text() else "")
                                    + NL + NL + self.last_plan_text()
                                    # 2026-10-01 使用者：「慢想要能看到完整的內容」——以前慢想只拿到環顧圖、歷史、視角、字、探索地圖、
                                    # 任務樹、步驟日誌、上一次計畫；禁令、被擋紀錄、改善規則、計分板、他自己的知識全都看不到，
                                    # 所以會排出被禁的步驟。現在把快思這一輪看到的整份面板原樣附上。
                                    + NL + NL + "【快思這一輪看到的完整面板】（禁令、上一步被擋了什麼、程式替你做了什麼、你的規則與知識都在裡面；排步驟前先讀）"
                                    + NL + self.panel_for_slow()
                                    + NL + NL + self.SLOW_ASK})
            _t0 = time.time()
            try:
                raw, _trunc, _, _ = stream_json(self.endpoint, self.model, self.sys_prompt(),
                                                content, 2500, time.time() + self.SLOW_BUDGET, 0.35,
                                                effort=中想)
            except Exception as e:  # noqa: BLE001
                # 整份面板＋四張環顧圖＋system 可能超過模型 65,536 的上下文（HTTP 400）：退一步，不附面板再試一次
                if "400" not in str(e):
                    raise
                print(f"   慢想太長被退回（{e}），改成不附整份面板再試一次")
                content[-1]["text"] = content[-1]["text"].split("【快思這一輪看到的完整面板】")[0] + self.SLOW_ASK
                raw, _trunc, _, _ = stream_json(self.endpoint, self.model, self.sys_prompt(),
                                                content, 2500, time.time() + self.SLOW_BUDGET, 0.35,
                                                effort=中想)
            # 2026-10-01：把慢想的原文與耗時留下來（之前空 JSON 查不到原因）
            try:
                with open(os.path.join(self.dir, "慢想原文.log"), "a", encoding="utf-8") as _f:
                    _f.write(f"## {datetime.now().strftime('%m/%d %H:%M:%S')} 第 {self.stat.get('輪數', 0)} 輪　{time.time() - _t0:.0f}s"
                             f"{'　⚠ 超時截斷' if _trunc else ''}　推理 {len(getattr(self, '_last_reasoning', '') or '')} 字" + NL + str(raw or "")[:4000] + NL + NL)
            except Exception:  # noqa: BLE001
                pass
            if _trunc:
                print(f"   ⚠ 慢想 {self.SLOW_BUDGET:.0f} 秒內沒寫完，被截斷（原文在 記憶/慢想原文.log）")
        except Exception as e:  # noqa: BLE001
            print(f"   慢想失敗：{e}")
            return
        o = parse_json_loose(raw) or {}
        if not o:
            print("   慢想沒解析出 JSON，這次跳過")
            return
        # 09/14 起排的是「大動作＋完整詳細步驟＋下一個大動作」；舊欄位也還讀得進來
        steps = [str(x)[:40] for x in (o.get("詳細步驟") or o.get("下三步") or []) if str(x).strip()][:10]
        big = str(o.get("現在的大動作") or o.get("要去哪") or "")[:30]
        try:
            at = max(0, min(len(steps) - 1, int(o.get("做到第幾步", 1) or 1) - 1))
        except (TypeError, ValueError):
            at = 0
        self.plan = {"我在哪": str(o.get("我在哪", ""))[:40],
                     "怎麼看出來的": str(o.get("怎麼看出來的", ""))[:40],
                     "現在的大動作": big,
                     "為什麼": str(o.get("為什麼", ""))[:60], "上一步": str(o.get("上一步", ""))[:60],
                     "詳細步驟": steps, "做到第幾步": at,
                     "下一個大動作": str(o.get("下一個大動作", ""))[:30],
                     "下一個為什麼": str(o.get("下一個為什麼", ""))[:40],
                     "要注意": str(o.get("要注意", ""))[:60],
                     "輪數": self.stat.get("輪數", 0),
                     "時間": datetime.now().strftime("%H:%M:%S")}
        # 2026-09-20 三層分工：慢想排出來的「大動作＋詳細步驟」就是反射輪要照著做的東西，
        # 所以直接同步進大動作簿（一份真相，不要兩套）。
        try:
            nxt = str(o.get("下一個大動作", ""))[:12]
            # 2026-09-22 使用者：慢想要檢討不是重排——同一件事沿用同一個名字。
            # 他說沿用、或新名字跟現在的大動作很像（>=0.6），就用原本的名字。
            cur = self.macro_name()
            keep = bool(o.get("沿用名字")) if "沿用名字" in o else False
            # 「同一家族」：現在的名字＋跟它很像的舊名字；新名字只要像家族裡任何一個就算同一件事
            family = {cur} | {k for _, k in self.macro_similar(cur, 6, 0.6)} if cur else set()
            alike = {k for _, k in self.macro_similar(big[:12], 8, 0.5)} | {big[:12]}
            if cur and (keep or (alike & family)):
                if big[:12] != cur:
                    print(f"   🧩 慢想想取新名字「{big[:12]}」，跟現在的「{cur}」是同一件事 → 沿用「{cur}」")
                big = cur
                fails = self.macro.setdefault("失敗次數", {})
                fails[cur] = int(fails.get(cur, 0) or 0) + 1
            why_fail = str(o.get("上一個計畫失敗在") or "")[:60]
            if why_fail:
                print(f"   🧩 慢想檢討：上一個計畫失敗在——{why_fail}")
                self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 你慢想時檢討出：上一個計畫失敗在 {why_fail}")
            seq = [x for x in (big[:12], nxt) if x]
            if seq:
                old_seq = self.macro.get("序列") or []
                if seq[0] not in old_seq:
                    self.macro["序列"] = seq
                    self.macro["第幾個"] = 0
                else:
                    self.macro["第幾個"] = old_seq.index(seq[0])
                    if len(seq) > 1 and seq[1] not in old_seq:
                        self.macro["序列"] = old_seq + [seq[1]]
                self.macro["主任務"] = (self.macro.get("主任務")
                                     or str(o.get("為什麼", ""))[:24])
                if steps:
                    old_steps = list((self.macro.get("步驟") or {}).get(seq[0], []))
                    self.macro.setdefault("步驟", {})[seq[0]] = steps
                    self.macro["第幾步"] = at
                    if steps != old_steps:
                        self.macro_budget_start("慢想重切了步驟")   # 真的改了步驟才重新計輪
                    _aim = str(o.get("為什麼", ""))[:40]
                    if _aim:
                        self.macro.setdefault("目的", {})[seq[0]] = _aim
                self.save_macros()
                print(f"   🧩 慢想 → 大動作簿：{seq[0]}"
                      + (f"（{len(steps)} 步，現在第 {at + 1} 步）" if steps else "（還沒切步驟）"))
        except Exception as e:  # noqa: BLE001
            print(f"   慢想同步大動作失敗：{e}")
        try:
            with open(os.path.join(self.dir, "慢想.json"), "w", encoding="utf-8") as f:
                json.dump(self.plan, f, ensure_ascii=False, indent=1)
            with open(os.path.join(self.dir, "慢想.md"), "a", encoding="utf-8") as f:
                f.write(f"## {datetime.now().strftime('%m/%d %H:%M')}（第 {self.plan['輪數']} 輪）" + NL
                        + f"- 我在哪：{self.plan['我在哪']}" + NL
                        + f"- **現在的大動作：{big}**（{self.plan['為什麼']}）" + NL
                        + f"- 上一步：{self.plan['上一步']}" + NL
                        + NL.join(f"  {i+1}. {x}" + ("　👈 做到這裡" if i == at else "")
                                  for i, x in enumerate(steps)) + NL
                        + f"- 下一個大動作：{self.plan['下一個大動作']}"
                        + (f"（{self.plan['下一個為什麼']}）" if self.plan['下一個為什麼'] else "") + NL
                        + f"- 要注意：{self.plan['要注意']}" + NL + NL)
        except Exception:  # noqa: BLE001
            pass
        print(f"   🐢 慢想結果：在「{self.plan['我在哪']}」"
              f"→ 大動作「{big}」共 {len(steps)} 步（現在第 {at + 1} 步）"
              + (f"→ 接著「{self.plan['下一個大動作']}」" if self.plan['下一個大動作'] else ""))
        for i, x in enumerate(steps):
            print(f"      {'👉' if i == at else '  '} {i + 1}. {x}")
        self.log({"時間": datetime.now().strftime("%H:%M:%S"), "模式": "慢想", **self.plan})

    def load_plan(self):
        self.plan = {}
        try:
            with open(os.path.join(self.dir, "慢想.json"), encoding="utf-8") as f:
                self.plan = json.load(f) or {}
        except Exception:  # noqa: BLE001
            self.plan = {}
        if self.plan.get("下三步"):
            print(f"[慢想] 接上次的計畫：要去「{self.plan.get('要去哪', '')}」，"
                  f"下三步 {'／'.join(self.plan['下三步'])}")

    # 2026-09-14 使用者定案的學習結構：
    #   大任務（推進輪班）→ 拆成中任務（報到、治療、喝咖啡…）→ 一個一個學會 → 合起來完成大任務。
    #   慢想排的「大動作」必須是現在專注的那個中任務（或擋在前面的前置）。
    TREE_ORDER = ["開始上班", "喝咖啡", "報到", "治療", "分配房間"]
    TREE_PRE = {"開始上班", "喝咖啡"}          # 前置：隨時可以做，不算「換目標」

    def task_tree(self):
        """算出每個中任務現在會不會。證據都是程式看得到的，不是他說的。"""
        if not self.mod("任務樹"):
            self.tree = {}
            return self.tree
        sc = getattr(self, "score", {}) or {}
        wins = getattr(self, "wins", []) or []
        rew = ((getattr(self, "reward", {}) or {}).get("明細") or {})
        course = getattr(self, "course", {}) or {}
        passed = course.get("通過") or {}
        coffee = sum(1 for w in wins if any(k in str(w.get("目標", "")) + str(w.get("任務", ""))
                                             for k in ("coffee", "咖啡")))
        # 成功紀錄的目標欄常是「?」，互動嘗試表（每個目標的成功次數）才是可靠證據
        try:
            tl = getattr(self, "tries_log", {}) or {}
            coffee = max(coffee, sum(int(v.get("成功", 0)) for k, v in tl.items()
                                     if any(x in str(k).lower() for x in ("coffee", "咖啡"))))
        except Exception:  # noqa: BLE001
            pass
        cured = int(rew.get("治好一個病人", 0))
        shifts = int(sc.get("最高輪班", 0) or 0)
        checkins = int(self.stat.get("登記", 0))
        mids = {
            "開始上班": ("會了" if shifts >= 2 else "學習中" if shifts >= 1 else "還沒學",
                        f"進過輪班 {shifts} 次"),
            "喝咖啡":   ("會了" if coffee >= 3 else "學習中" if coffee >= 1 else "還沒學",
                        f"成功喝到 {coffee} 次"),
            "報到":     ("會了" if "C6" in passed else "學習中",
                        f"課程 {len(passed)}/6 關、做成 {checkins} 次"),
            "治療":     ("會了" if cured >= 3 else "學習中" if cured >= 1 else "還沒學",
                        f"治好 {cured} 個"),
            "分配房間": ("還沒學", "還沒有做成過的證據"),
        }
        focus = next((n for n in self.TREE_ORDER if mids[n][0] != "會了" and n not in self.TREE_PRE), None) \
            or next((n for n in self.TREE_ORDER if mids[n][0] != "會了"), None) or "治療"
        ov = getattr(self, "_focus_override", None)
        if ov and time.time() < ov[1] and ov[0] in mids:
            focus = ov[0]                     # 快思小檢查說該換的，先照它（半小時內）
        tree = {"大任務": "推進輪班：撐完一整輪班，並在班上賺到錢",
                "中任務": [{"名": n, "狀態": mids[n][0], "證據": mids[n][1]} for n in self.TREE_ORDER],
                "現在專注": focus, "更新": datetime.now().strftime("%m/%d %H:%M")}
        self.tree = tree
        try:
            with open(os.path.join(self.dir, "任務樹.json"), "w", encoding="utf-8") as f:
                json.dump(tree, f, ensure_ascii=False, indent=1)
        except Exception:  # noqa: BLE001
            pass
        return tree

    def task_tree_text(self):
        # 任務樹的中任務清單是照這個遊戲寫死的，別的遊戲要他自己長出來 → 預設關掉。
        if not self.mod("任務樹"):
            return ""
        t = self.task_tree()
        mark = {"會了": "✅ 會了", "學習中": "🔶 學習中", "還沒學": "⬜ 還沒學"}
        rows = [f"■ 大任務：**{t['大任務']}**",
                "　拆成這幾個中任務，**一次只學一個，學會了才換下一個**，最後合起來完成大任務："]
        for m in t["中任務"]:
            pre = "（前置，隨時可以做）" if m["名"] in self.TREE_PRE else ""
            arrow = "　👉 " if m["名"] == t["現在專注"] else "　　 "
            rows.append(f"{arrow}{m['名']}{pre}：{mark[m['狀態']]}（{m['證據']}）")
        rows.append(f"■ **現在專注學的中任務：{t['現在專注']}**。"
                    f"慢想排的「大動作」要就是它、或是它的一部分；理智低或人在大廳時，先做前置。")
        return NL.join(rows)

    def plan_text(self):
        """把慢想的計畫回灌給每一個快思輪次——快思只管怎麼走，方向照這份走。"""
        pl = getattr(self, "plan", None) or {}
        # play_agent 的 think() 會把 self.plan 設成一個**字串**（計畫那句話），
        # 而慢想寫進來的是 dict——兩邊共用同一個屬性，字串進來這裡就會 AttributeError
        # （2026-09-14 開啟思考模式之後 think() 更常被觸發，這一輪就炸了好幾次）。
        if isinstance(pl, str):
            return f"（上次停下來想的結論）{pl[:180]}" if pl.strip() else                    "（還沒慢想過，下一次慢想會排出計畫；在那之前先做眼前最合理的事）"
        if not isinstance(pl, dict):
            pl = {}
        steps = pl.get("詳細步驟") or pl.get("下三步") or []      # 舊格式也讀得到
        big = pl.get("現在的大動作") or pl.get("要去哪")
        if not steps and not big:
            return "（還沒慢想過，下一次慢想會排出計畫；在那之前先做眼前最合理的事）"
        i = int(pl.get("做到第幾步", pl.get("第幾步", 0)) or 0)
        rows = [f"（{pl.get('時間', '')} 慢想排的。**這一輪只要把「現在這步」往前推一點就好**）",
                f"我在哪：{pl.get('我在哪', '（沒寫）')}"
                + (f"（怎麼看出來的：{pl['怎麼看出來的']}）" if pl.get("怎麼看出來的") else ""),
                "",
                f"■ **現在的大動作：{big or '（沒寫）'}**"
                + (f"（{pl['為什麼']}）" if pl.get("為什麼") else ""),
                f"　上一步：{pl.get('上一步', '（沒寫）')}"]
        for n, x in enumerate(steps):
            mark = "👉 現在做這步" if n == i else ("✓ 做過了" if n < i else "　")
            rows.append(f"　{mark}　第 {n + 1}/{len(steps)} 步：{x}")
        if pl.get("要注意"):
            rows.append(f"　⚠ 要注意：{pl['要注意']}")
        nxt = pl.get("下一個大動作")
        if nxt:
            rows.append("")
            rows.append(f"■ 這件做完之後，下一個大動作：**{nxt}**"
                        + (f"（{pl['下一個為什麼']}）" if pl.get("下一個為什麼") else ""))
            rows.append("　（下一個大動作先不用管細節，這件做完程式會讓你再慢想一次把它拆開）")
        rows.append("")
        rows.append("→ 做完一步就送 {\"t\":\"下一步\"}，箭頭會往下移。"
                    "整份計畫覺得不對就送 {\"t\":\"重想\"}，程式會馬上給你一次不限時的慢想。"
                    "**最後一步也做完了就送 重想**，好排下一個大動作的步驟。")
        return NL.join(rows)

    def plan_advance(self, done=True):
        pl = getattr(self, "plan", None) or {}
        if not isinstance(pl, dict):
            return
        steps = pl.get("詳細步驟") or pl.get("下三步")
        if not steps:
            return
        key = "做到第幾步" if "詳細步驟" in pl else "第幾步"
        i = int(pl.get(key, 0) or 0) + (1 if done else 0)
        pl[key] = i
        if i < len(steps):
            print(f"   ✅ 「{pl.get('現在的大動作', '計畫')}」推進到第 {i + 1}/{len(steps)} 步：{steps[i][:30]}")
        else:
            nxt = pl.get("下一個大動作")
            print(f"   ✅ 「{pl.get('現在的大動作', '計畫')}」整個做完了"
                  + (f"，下一個大動作是「{nxt}」——下一輪重新慢想把它拆開" if nxt else "，下一輪重新慢想"))
            self._slow_due = True
        try:
            with open(os.path.join(self.dir, "慢想.json"), "w", encoding="utf-8") as f:
                json.dump(pl, f, ensure_ascii=False, indent=1)
        except Exception:  # noqa: BLE001
            pass

    # ---------------- 變化偵測（這款遊戲的異變＝東西變了）
    HUE_NAMES = [(15, "紅"), (45, "橙"), (70, "黃"), (170, "綠"), (200, "青"),
                 (260, "藍"), (310, "紫"), (345, "粉紅"), (361, "紅")]
    YAW_BUCKET = 30          # 每 30 度算「同一個方位」
    HUE_MOVE = 25            # 色相差超過這麼多度＝變色
    SEEN_ENOUGH = 3          # 同一個方位看過這麼多次，才有資格說「以前沒有／不見了」

    def color_name(self, rgb):
        import colorsys
        r, g, b = [x / 255.0 for x in rgb]
        h, l, sat = colorsys.rgb_to_hls(r, g, b)
        if sat < 0.12:
            return "灰白" if l > 0.6 else ("深灰" if l > 0.25 else "黑")
        deg = h * 360
        for edge, nm in self.HUE_NAMES:
            if deg < edge:
                return ("淺" if l > 0.65 else ("深" if l < 0.3 else "")) + nm
        return "紅"

    def obj_color(self, im, d):
        """取這個框正中間 60% 的平均顏色（避開邊緣的背景）。"""
        import numpy as np
        W, H = im.size
        x0 = int(max(0, (d["cx"] - d["w"] * 0.3) * W)); x1 = int(min(W, (d["cx"] + d["w"] * 0.3) * W))
        y0 = int(max(0, (d["cy"] - d["h"] * 0.3) * H)); y1 = int(min(H, (d["cy"] + d["h"] * 0.3) * H))
        if x1 - x0 < 4 or y1 - y0 < 4:
            return None
        a = np.asarray(im.convert("RGB").crop((x0, y0, x1, y1)).resize((16, 16)), dtype=np.float32)
        return tuple(int(v) for v in a.reshape(-1, 3).mean(axis=0))

    def hue_sat(self, rgb):
        import colorsys
        h, l, sat = colorsys.rgb_to_hls(*[x / 255.0 for x in rgb])
        return h * 360, sat, l

    def load_appear(self):
        self.appear = {}
        try:
            with open(os.path.join(self.dir, "物品外觀.json"), encoding="utf-8") as f:
                self.appear = json.load(f) or {}
        except Exception:  # noqa: BLE001
            self.appear = {}
        print(f"[變化] 他記過 {len(self.appear)} 個「東西＋方位」的樣子（用來比對變色與新增）")

    def save_appear(self):
        try:
            with open(os.path.join(self.dir, "物品外觀.json"), "w", encoding="utf-8") as f:
                json.dump(self.appear, f, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            pass

    def scene_changes(self, im):
        """比對這一輪看到的東西跟以前的樣子：變色、新出現、不見了。

        這款遊戲的異變就是「同一個東西變得不一樣」，所以這件事程式一定要幫他量——
        顏色的細微改變他用看的看不出來，但數字比得出來。
        """
        dets, stamp = self.eyes.latest()
        if not stamp or time.time() - stamp > 2.0:
            return []
        yaw = getattr(self, "yaw", 0.0) % 360
        bucket = int(yaw // self.YAW_BUCKET)
        msgs, now_keys = [], set()
        for lab, arr in (dets or {}).items():
            for d in arr[:1]:
                if self.eyes.is_self(d) or d.get("score", 0) < 0.25:
                    continue
                rgb = self.obj_color(im, d)
                if not rgb:
                    continue
                key = f"{lab}@{bucket}"
                now_keys.add(key)
                rec = self.appear.get(key)
                h1, s1, _ = self.hue_sat(rgb)
                if not rec:
                    self.appear[key] = {"rgb": list(rgb), "n": 1, "缺席": 0,
                                        "首見": datetime.now().strftime("%H:%M")}
                    if any(v.get("n", 0) >= self.SEEN_ENOUGH for k, v in self.appear.items()
                           if k.endswith(f"@{bucket}")):
                        msgs.append(f"🆕 **場地多了東西**：面朝 {self.compass(yaw)}（{yaw:.0f} 度）"
                                    f"出現「{lab}」（{self.color_name(rgb)}），這個方位以前沒有它")
                    continue
                h0, s0, _ = self.hue_sat(rec["rgb"])
                dh = min(abs(h1 - h0), 360 - abs(h1 - h0))
                if s0 > 0.15 and s1 > 0.15 and dh > self.HUE_MOVE:
                    msgs.append(f"🎨 **變色了**：面朝 {self.compass(yaw)} 的「{lab}」"
                                f"從 {self.color_name(rec['rgb'])} 變成 {self.color_name(rgb)}"
                                f"（色相差 {dh:.0f} 度，看過 {rec['n']} 次都是原來的顏色）")
                    self.log({"時間": datetime.now().strftime("%H:%M:%S"), "模式": "變色",
                              "東西": lab, "方位": round(yaw), "舊": rec["rgb"], "新": list(rgb)})
                # 平均進去（慢慢跟著光線走，但變色會先被抓到）
                rec["rgb"] = [round((a * rec["n"] + b) / (rec["n"] + 1)) for a, b in zip(rec["rgb"], rgb)]
                rec["n"] = min(rec.get("n", 1) + 1, 30)
                rec["缺席"] = 0
        for key, rec in list(self.appear.items()):
            if not key.endswith(f"@{bucket}") or key in now_keys:
                continue
            if rec.get("n", 0) < self.SEEN_ENOUGH:
                continue
            rec["缺席"] = rec.get("缺席", 0) + 1
            if rec["缺席"] == 2:
                msgs.append(f"❓ **東西不見了**：面朝 {self.compass(yaw)} 本來有「{key.split('@')[0]}」"
                            f"（看過 {rec['n']} 次），現在看不到了")
        if msgs:
            for m in msgs:
                print("   " + m)
            try:
                with open(os.path.join(self.dir, "變化紀錄.md"), "a", encoding="utf-8") as f:
                    f.write(f"## {datetime.now().strftime('%m/%d %H:%M:%S')}（第 {self.stat.get('輪數', 0)} 輪）" + NL
                            + NL.join("- " + m for m in msgs) + NL + NL)
            except Exception:  # noqa: BLE001
                pass
        self.changes = msgs[-4:]
        if self.stat.get("輪數", 0) % 10 == 0:
            self.save_appear()
        return msgs

    def changes_text(self):
        ms = getattr(self, "changes", None) or []
        n = len(getattr(self, "appear", {}) or {})
        if not ms:
            return (f"（這一輪沒有量到變化。程式已經記住 {n} 個「東西＋方位」原本的顏色，"
                    f"一有變色、多出東西或東西不見，這裡會第一時間告訴你）")
        return (NL.join(ms) + NL
                + "→ **這就是異變的線索**。顏色的細微改變你用看的看不出來，這是程式量出來的，可信。"
                + "先確認是不是真的（走近一點再看一眼），確定了就照流程處理：拍照存證、必要時按快門通報。")

    # ---------------- 自動標註：邊玩邊存訓練資料
    LABEL_EVERY = 5          # 每 5 輪存一張
    LABEL_KEEP = 6000        # 最多留這麼多張

    def label_dirs(self):
        base = os.path.join(self.label_base())
        for sub in ("images", "labels", "meta"):
            os.makedirs(os.path.join(base, sub), exist_ok=True)
        return base

    def label_base(self):
        """訓練資料放在 專案/<遊戲>/素材/標註（不進記憶，太大）。"""
        b = project_dir(self.game, "素材", "標註")
        if os.path.isdir(os.path.dirname(b)) or not os.path.isdir(os.path.join(self.dir, "標註")):
            return b
        return os.path.join(self.dir, "標註")

    def load_classes(self):
        self.classes = []
        try:
            with open(os.path.join(self.label_base(), "類別表.json"), encoding="utf-8") as f:
                self.classes = list(json.load(f) or [])
        except Exception:  # noqa: BLE001
            self.classes = []
        self.label_n = 0
        print(f"[標註] 已累積 {self.label_count()} 張訓練用畫面、{len(self.classes)} 種類別")

    def class_id(self, lab):
        lab = str(lab).strip().lower()
        if lab not in self.classes:
            self.classes.append(lab)
            try:
                self.label_dirs()
                with open(os.path.join(self.label_base(), "類別表.json"), "w", encoding="utf-8") as f:
                    json.dump(self.classes, f, ensure_ascii=False)
            except Exception:  # noqa: BLE001
                pass
        return self.classes.index(lab)

    def save_label(self, im, name=None):
        """把這一輪的畫面＋眼睛框到的東西＋他自己講的一句話，存成 YOLO 格式的訓練資料。

        框是 YOLO-World 給的（座標可信），標籤名也是它給的；
        他自己那句「看到什麼」另外存在 meta，之後要蒸餾成專屬模型時當語意來源。
        """
        dets, stamp = self.eyes.latest()
        if not stamp or time.time() - stamp > 2.0 or not dets:
            return
        base = self.label_dirs()
        name = name or (datetime.now().strftime("%Y%m%d_%H%M%S_") + str(self.stat.get("輪數", 0)))
        rows = []
        for lab, arr in dets.items():
            for d in arr[:3]:
                if d.get("score", 0) < 0.25 or self.eyes.is_self(d):
                    continue
                rows.append("%d %.6f %.6f %.6f %.6f" % (self.class_id(lab), d["cx"], d["cy"], d["w"], d["h"]))
        if not rows:
            return
        try:
            im.convert("RGB").save(os.path.join(base, "images", name + ".jpg"), quality=80)
            with open(os.path.join(base, "labels", name + ".txt"), "w", encoding="utf-8") as f:
                f.write(NL.join(rows) + NL)
            with open(os.path.join(base, "meta", name + ".json"), "w", encoding="utf-8") as f:
                json.dump({"他說看到": str(getattr(self, "last_seen", ""))[:120],
                           "流程": self.flow, "步驟": self.step,
                           "位置": [round(self.pos[0], 2), round(self.pos[1], 2)],
                           "面朝": round(getattr(self, "yaw", 0.0) % 360),
                           "俯仰": round(getattr(self, "pitch_deg", 0.0)),
                           "框數": len(rows)}, f, ensure_ascii=False)
        except Exception as e:  # noqa: BLE001
            print(f"   存標註失敗：{e}")
            return
        self.label_n = getattr(self, "label_n", 0) + 1
        if self.label_n % 20 == 0:
            print(f"   🏷 自動標註累積 {self.label_count()} 張（類別 {len(self.classes)} 種）")
        self.prune_labels()

    TRAIN_EVERY = 300          # 沒人要求時，新累積這麼多張就自動訓練一次

    def maybe_train_eyes(self):
        """收工時（GPU 空下來）決定要不要跑 train_eyes.py：他有要求、或新資料夠多。

        使用者 09/15：訓練眼睛是**另一種權限**，由 設定.json 的「眼睛訓練」決定——
        他只能「提出」（訓練請求.json），要不要真的跑、多少張才跑，是程式與使用者定的。
        """
        try:
            cfg = (self.cfg.get("眼睛訓練") or {}) if isinstance(self.cfg.get("眼睛訓練"), dict) else {}
            if not cfg.get("允許", True):
                print("🧠 收工：設定.json 的「眼睛訓練.允許」是 false，不訓練（他的請求保留著）")
                return
            every = int(cfg.get("每N張", self.TRAIN_EVERY) or self.TRAIN_EVERY)
            req = os.path.exists(os.path.join(self.dir, "訓練請求.json"))
            if req and not cfg.get("接受他的請求", True):
                req = False
            last = {}
            try:
                last = json.load(open(os.path.join(self.dir, "訓練狀態.json"), encoding="utf-8")) or {}
            except Exception:  # noqa: BLE001
                pass
            fresh = self.label_count() - int(last.get("張數", 0) or 0)
            if not req and (not cfg.get("自動收工訓練", True) or fresh < every):
                return
            import subprocess
            cmd = [sys.executable, os.path.join(HERE, "train_eyes.py"), "--game", self.game]
            logf = open(os.path.join(self.dir, "訓練輸出.log"), "a", encoding="utf-8")
            flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
            subprocess.Popen(cmd, cwd=HERE, stdout=logf, stderr=subprocess.STDOUT, creationflags=flags)
            print(f"🧠 收工：{'他要求' if req else f'新資料 {fresh} 張'} → 已在背景啟動 train_eyes.py"
                  f"（輸出在 記憶/訓練輸出.log，完成後 weights/eyes_trained.pt 下次開機自動使用）")
        except Exception as e:  # noqa: BLE001
            print(f"   訓練啟動失敗：{e}")

    def label_count(self):
        try:
            return len(os.listdir(os.path.join(self.label_base(), "images")))
        except Exception:  # noqa: BLE001
            return 0

    def prune_labels(self):
        if getattr(self, "label_n", 0) % 100:
            return
        base = os.path.join(self.label_base())
        try:
            fs = sorted(os.listdir(os.path.join(base, "images")))
            for f in fs[:max(0, len(fs) - self.LABEL_KEEP)]:
                stem = f.rsplit(".", 1)[0]
                for sub, ext in (("images", ".jpg"), ("labels", ".txt"), ("meta", ".json")):
                    try:
                        os.remove(os.path.join(base, sub, stem + ext))
                    except OSError:
                        pass
        except Exception:  # noqa: BLE001
            pass

    # ---------------- 地標照片：用真的畫面印證腦中的地圖對不對
    LANDMARK_HIT = 0.62      # 相似度到這裡就算「真的在同一個地方」

    def shot_sig(self, im):
        """把畫面壓成一個小簽名：8x8 灰階結構 + 4x4x3 顏色。夠穩、又幾乎不吃資源。"""
        import numpy as np
        g = np.asarray(im.convert("L").resize((8, 8)), dtype=np.float32)
        g = (g - g.mean()) / (g.std() + 1e-5)
        c = np.asarray(im.convert("RGB").resize((4, 4)), dtype=np.float32) / 255.0
        return [round(float(v), 3) for v in g.flatten()] + [round(float(v), 3) for v in c.flatten()]

    def sig_match(self, a, b):
        """兩個簽名有多像（0~1）：結構相關性與顏色差各佔一半。"""
        import numpy as np
        if not a or not b or len(a) != len(b):
            return 0.0
        a = np.array(a, dtype=np.float32)
        b = np.array(b, dtype=np.float32)
        ga, gb = a[:64], b[:64]
        gs = float(np.dot(ga, gb) / (np.linalg.norm(ga) * np.linalg.norm(gb) + 1e-6))
        cs = 1.0 - float(np.abs(a[64:] - b[64:]).mean()) * 2.0
        return max(0.0, min(1.0, 0.5 * (gs + 1) / 2.0 + 0.5 * max(0.0, cs)))

    def load_marks(self):
        self.marks = {}
        try:
            with open(os.path.join(self.dir, "地標照片.json"), encoding="utf-8") as f:
                self.marks = json.load(f) or {}
        except Exception:  # noqa: BLE001
            self.marks = {}
        print(f"[印證] 他存過 {len(self.marks)} 個地標的實際畫面（用來檢查腦中地圖對不對）")

    def save_marks(self):
        try:
            with open(os.path.join(self.dir, "地標照片.json"), "w", encoding="utf-8") as f:
                json.dump(self.marks, f, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            pass

    def snap_mark(self, name, im):
        """把「這個地方長什麼樣」拍下來存檔：畫面簽名＋當時的座標與朝向。"""
        d = os.path.join(self.dir, "地標")
        os.makedirs(d, exist_ok=True)
        safe = "".join(ch for ch in str(name) if ch not in '\\/:*?"<>|')[:24] or "地標"
        try:
            im.convert("RGB").resize((320, 180)).save(os.path.join(d, safe + ".jpg"), quality=82)
        except Exception:  # noqa: BLE001
            pass
        self.marks[safe] = {"sig": self.shot_sig(im),
                            "x": round(self.pos[0], 2), "y": round(self.pos[1], 2),
                            "yaw": round(getattr(self, "yaw", 0.0) % 360),
                            "次數": (self.marks.get(safe, {}) or {}).get("次數", 0) + 1,
                            "時間": datetime.now().strftime("%m/%d %H:%M")}
        self.save_marks()
        try:
            self.award("拍下新地標", f"「{safe}」")
        except Exception:  # noqa: BLE001
            pass
        print(f"   📷 存下「{safe}」現在的樣子（x{self.pos[0]:+.1f} y{self.pos[1]:+.1f} 面朝 {self.marks[safe]['yaw']}度）")

    def verify_place(self, im):
        """拿現在的畫面比對他存過的地標照片——腦中的地圖對不對，這是唯一驗得出來的方法。"""
        self.verify_fix = ""
        if not getattr(self, "marks", None):
            self.verify = ("（還沒有任何地標照片可以比對。走到你認得的地方，用 "
                           + ACT_SNAP + " 把它現在的樣子存下來；"
                           "以後回到同一個地方，程式就能告訴你「你真的在那裡」還是「你只是以為」）")
            return
        import math
        sig = self.shot_sig(im)
        best, score = None, 0.0
        for nm, rec in self.marks.items():
            v = self.sig_match(sig, rec.get("sig"))
            if v > score:
                best, score = nm, v
        rec = self.marks.get(best) or {}
        gap = math.hypot(rec.get("x", 0) - self.pos[0], rec.get("y", 0) - self.pos[1])
        if score >= self.LANDMARK_HIT and gap > 3.0:
            self.verify = ("⚠ **腦中的地圖漂了**：現在的畫面跟你存的「%s」有 %.0f%% 像，"
                           "但你以為自己在 x%+.1f y%+.1f，跟「%s」的 x%s y%s 差了 %.1f 步。"
                           "→ 眼睛看到的才算數，送 %s 把座標校正回來。"
                           % (best, score * 100, self.pos[0], self.pos[1], best,
                              rec.get("x"), rec.get("y"), gap, ACT_HERE % best))
            self.verify_fix = best
        elif score >= self.LANDMARK_HIT:
            self.verify = ("✅ **印證過了**：畫面跟你存的「%s」有 %.0f%% 像，座標也只差 %.1f 步"
                           "——你腦中的地圖在這一塊是對的。" % (best, score * 100, gap))
            # 2026-09-14 補接：印證正確 +2 以前也沒人發過獎（同一地標 60 秒內只給一次）
            try:
                seen = getattr(self, "_verify_at", None) or {}
                if time.time() - seen.get(best, 0) > 60:
                    seen[best] = time.time()
                    self._verify_at = seen
                    self.award("腦中地圖印證正確",
                               f"畫面跟你記的「{best}」對得上，座標也只差 {gap:.1f} 步。")
            except Exception:  # noqa: BLE001
                pass
        else:
            self.verify = ("（現在的畫面跟你存過的地標都不像，最接近的是「%s」%.0f%%。"
                           "這代表你在一個還沒拍過的地方——值得用 %s 把它記下來）"
                           % (best, score * 100, ACT_SNAP))

    def verify_text(self):
        return getattr(self, "verify", "") or "（這一輪還沒比對）"

    # ---------------- 陣列地圖（2026-09-30 使用者定；細節在 grid_map.py 開頭）
    # 整張地圖用二維陣列存、出生點是原點；牆壁用視覺深度畫；三種元素：牆壁／互動物／觸碰物。
    # 只有第一次到一個地方才用視覺掃一圈，之後靠陣列地圖＋水平儀（程式自己送的轉頭量）推算；路線用 A* 在陣列上找。
    # 開關：遊戲.json 的 "陣列地圖": true。所有事件記在 記憶/陣列地圖紀錄.jsonl（掃描、量比例、撞牆、核對、路線、物件）。
    GMAP_SCAN_TURNS = 12         # 掃一圈轉幾次（每次 30 度：畫面寬約 70 度，每個方向會在兩三面裡出現）
    GMAP_SKIP_CENTER = 10.0      # 正中間 ±幾度的視線不用：第三人稱時那裡是自己的背影，不是牆
    GMAP_MAX_DIST = 10.0         # 幾步以外的深度不當牆（太遠不準）
    GMAP_DEFAULT_SCALE = 0.5     # 量不出來時的預設：深度 1 單位＝0.5 步（09/30 第一次實測量到 0.50）
    GMAP_CHECK_EVERY = 6         # 每幾輪用當下這一張畫面核對一次位置（不轉頭）

    def gmap_on(self):
        return bool(self.gcfg.get("陣列地圖")) and not getattr(self, "_gmap_dead", False)

    def gmap_file(self):
        # 2026-10-02 使用者：同一個場景要合成一張——檔名只看場景（空間）名，不再分 Roblox place／伺服器
        return self.space_file("陣列地圖.json")

    def gmap_log(self, kind, **kw):
        """陣列地圖的每一件事都記一筆，之後才比得出有沒有改善。"""
        try:
            rec = {"時間": datetime.now().strftime("%m/%d %H:%M:%S"), "輪": self.stat.get("輪數", 0), "事件": kind,
                   "位置": [round(self.pos[0], 2), round(self.pos[1], 2)], "朝向": round(getattr(self, "yaw", 0.0) % 360, 1)}
            rec.update(kw)
            with open(os.path.join(self.dir, "陣列地圖紀錄.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + NL)
        except OSError:
            pass

    def gmap_load(self):
        """載入現在這個場景（Roblox place）的陣列地圖；場景換了就換一張，並把出生點當原點。"""
        if not self.gmap_on():
            return
        try:
            import grid_map
            p = self.gmap_file()
            old = getattr(self, "gmap", None)
            try:
                pid = self.current_place()
            except Exception:  # noqa: BLE001
                pid = None
            moved = old is not None and pid != getattr(self, "_gmap_pid", pid)
            self._gmap_pid = pid
            if old is not None and old.path == p and not moved:
                return
            if old is not None:
                old.save()
                # 換場景／被傳送＝在新的地方出生：座標與朝向歸零，這張地圖的原點就是出生點
                self.pos, self.yaw = [0.0, 0.0], 0.0
                if old.path == p:
                    self.gmap_log("重新出生", 檔=os.path.basename(p))
                    return
            self.gmap = grid_map.GridMap.load(p)
            if not self.gmap.scale:
                m = (self.measures_load().get("深度比例") or {})
                key = self.measure_key()
                src = m.get(key) or m.get("預設")
                if src and src.get("值"):
                    self.gmap.scale = float(src["值"])
                    print(f"   🗺 陣列地圖：這張圖沒比例，沿用這支遊戲量測值裡的 {self.gmap.scale}（{'同場景' if key in m else '預設'}）")
                    self.gmap_log("比例沿用", 比例=self.gmap.scale, 來源=("同場景" if key in m else "預設"))
                    self.gmap_replay()
            c = self.gmap.counts()
            print(f"[陣列地圖] {os.path.basename(p)}：牆壁 {c[grid_map.WALL]} 格、空地 {c[grid_map.FREE]} 格、"
                  f"互動物／觸碰物 {len(self.gmap.objects)} 個、掃過 {len(self.gmap.scanned)} 處、深度比例 {self.gmap.scale}")
            self.gmap_log("載入", 檔=os.path.basename(p), 牆=c[grid_map.WALL], 空地=c[grid_map.FREE],
                          物件=len(self.gmap.objects), 換場景=old is not None)
        except Exception as e:  # noqa: BLE001
            print(f"   陣列地圖載入失敗：{e}")

    def gmap_save(self):
        try:
            if getattr(self, "gmap", None) is not None:
                self.gmap.save()
        except Exception as e:  # noqa: BLE001
            print(f"   陣列地圖存檔失敗：{e}")

    def gmap_eye(self):
        """本機深度模型（第一次用才載入）。載不起來就整個功能停用，不拖累代理。"""
        if getattr(self, "_depth_eye", None) is None:
            try:
                import depth_eye
                t0 = time.time()
                self._depth_eye = depth_eye.DepthEye()
                print(f"[陣列地圖] 深度模型載入完成（{time.time() - t0:.1f}s，{self._depth_eye.device}）")
            except Exception as e:  # noqa: BLE001
                print(f"[陣列地圖] 深度模型載不起來，這次開機停用陣列地圖的視覺部分：{e}")
                self._gmap_dead = True
                self.gmap_log("深度模型失敗", 原因=str(e)[:120])
                return None
        return self._depth_eye

    def gmap_look(self, im=None):
        """看一眼現在的畫面 → 各方向的深度。人（會走）不算牆；自己的背影用來量鏡頭離自己多遠。"""
        eye = self.gmap_eye()
        if eye is None:
            return None
        im = im or self.grab_still(640)
        skip, me = [], None
        try:
            dets, stamp = self.eyes.latest()
            if stamp and time.time() - stamp < 2.0:
                for lab, arr in dets.items():
                    for d in arr[:3]:
                        box = (d["cx"] - d["w"] / 2, d["cy"] - d["h"] / 2, d["cx"] + d["w"] / 2, d["cy"] + d["h"] / 2)
                        if Eyes.is_self(d):
                            me = box
                        elif lab in ("person", "people", "player", "character"):
                            skip.append((box[0], box[2]))
        except Exception:  # noqa: BLE001
            pass
        r = eye.rays(im, n=9, skip=skip, self_box=me)
        # 正中間那幾條視線會打到自己的背影（第三人稱），一律不用；掃一圈時每個方向都會在別面的側邊再出現
        r["rays"] = [(a, z) for a, z in r["rays"] if abs(a) >= self.GMAP_SKIP_CENTER]
        return r

    @staticmethod
    def gmap_front(rays):
        """正前方（±28 度內、已經去掉正中間自己的背影）的深度中位數。"""
        zs = sorted(z for a, z in rays if abs(a) <= 28) or sorted(z for a, z in rays)
        return zs[len(zs) // 2] if zs else None

    def gmap_sample(self, r0, r1, walked):
        """他自己往前走了一段：用走之前、走之後兩張畫面的深度差，量一次「深度 1 單位＝幾步」。

        每一條視線各自比，取中位數；不合理的（沒變近、或算出來離譜）丟掉。湊滿 5 個取中位數定案，定案後就不再變
        （同一張地圖從頭到尾要用同一把尺）。
        """
        gm = getattr(self, "gmap", None)
        if gm is None or gm.scale or not r0 or not r1 or walked < 0.6:
            return
        a1 = {round(a): z for a, z in r1["rays"]}
        diffs = sorted(z - a1[round(a)] for a, z in r0["rays"] if round(a) in a1 and z <= 14.0)
        if len(diffs) < 3:
            return
        dd = diffs[len(diffs) // 2]
        v = (walked / dd) if dd > 0.3 else None
        ok = v is not None and 0.08 <= v <= 1.5
        if ok:
            gm.samples = (gm.samples + [round(v, 3)])[-12:]      # 只當參考給他看；比例要他自己量（使用者 10/01）
            gm.save()
        self.gmap_log("量比例樣本", 走了=round(walked, 2), 各線差的中位數=round(dd, 3), 這次=None if v is None else round(v, 3),
                      採用=ok, 目前樣本數=len(gm.samples))

    GMAP_SCAN_STOPS = 4          # 2026-10-02 使用者：第一次到一個地方先不動、一次轉九十度，慢慢畫
    GMAP_SCAN_NUDGE = 25.0       # 每一面左右各偏一點再看：正中間是自己的背影，偏一下才看得到正前方

    def gmap_scan(self):
        """第一次到這個地方：站著不動，一次轉 90 度、四面各看一次（每面左右偏 25 度各看一眼），
        用視覺深度把四周的牆畫進陣列。比例還沒定就先把看到的存起來，量好比例再補畫。轉完回到原本的朝向。"""
        gm = self.gmap
        t0 = time.time()
        views = []
        first = self.grab_still(320)
        nd = self.GMAP_SCAN_NUDGE
        for i in range(self.GMAP_SCAN_STOPS):
            for k, off in enumerate((-nd, 2 * nd)):
                self.turn_by(off)
                time.sleep(0.4)
                if i == 0 and k == 0 and frame_diff(first, self.grab_still(320)) < 3.0:
                    # 轉了畫面卻沒變：有面板蓋著、游標沒鎖、或根本不在 3D 場景裡——這種畫面量出來的深度不能畫成牆
                    self.turn_by(-off)
                    print("   🗺 陣列地圖：想原地看四面，但轉頭畫面沒跟著動（面板擋住／不在場景裡），這次不看")
                    self.gmap_log("掃描放棄", 原因="轉頭畫面沒變")
                    self._gmap_retry_at = self.stat.get("輪數", 0) + 5
                    return False
                r = self.gmap_look()
                if r is None:
                    return False
                views.append((getattr(self, "yaw", 0.0) % 360, r["rays"], r["self"]))
            self.turn_by(-nd)                  # 回到這一面的正中間
            time.sleep(0.2)
            self.turn_by(360.0 / self.GMAP_SCAN_STOPS)
            time.sleep(0.5)
        x0, y0 = self.pos[0], self.pos[1]
        gm.note_scanned(x0, y0)
        if not gm.scale:
            for yaw, rays, me in views:        # 比例還沒定：先存起來，他量好比例再補畫（gmap_replay）
                gm.keep_view(x0, y0, yaw, rays, me)
            gm.save()
            print(f"   🗺 陣列地圖：在 ({x0:+.1f},{y0:+.1f}) 原地轉了四面（每次 90 度），比例還沒定，先存著 {len(views)} 面，量好比例就補畫")
            self.gmap_log("掃描", 在=[round(x0, 2), round(y0, 2)], 新牆=0, 秒=round(time.time() - t0, 1), 先存著=len(views))
            self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] ⚙ 程式替你站著不動、一次轉 90 度看了四面，已轉回原本的朝向；"
                                "深度比例還沒定，看到的先存著——送 量比例 之後會一次補畫成牆")
            return True
        new = 0
        for yaw, rays, me in views:
            new += gm.scan(x0, y0, yaw, rays, max_dist=self.GMAP_MAX_DIST, self_depth=me)
        gm.save()
        dt = time.time() - t0
        try:
            prof = [(self.gmap_front(v[1]) or 0) for v in views]
            h = len(prof) // 2
            a, b = prof[:h], prof[h:]
            if h >= 4 and sum(abs(x - y) for x, y in zip(a, b)) / max(1e-6, sum(a) + sum(b)) < 0.08 and max(prof) - min(prof) > 1.0:
                print("   ⚠ 陣列地圖：掃一圈的距離前後半圈幾乎一樣——轉頭刻度可能差兩倍（實際每次轉的角度是程式以為的兩倍），"
                      "下次開機量刻度會重量；這次掃的牆方向可能不準")
                self.gmap_log("刻度疑似差兩倍", 各面=[round(x, 1) for x in prof])
        except Exception:  # noqa: BLE001
            pass
        self.stat["陣列地圖掃描"] = self.stat.get("陣列地圖掃描", 0) + 1
        print(f"   🗺 陣列地圖：在 ({x0:+.1f},{y0:+.1f}) 掃了一圈，新畫 {new} 格牆（{dt:.1f}s）；這附近之後不用再掃")
        self.gmap_log("掃描", 在=[round(x0, 2), round(y0, 2)], 新牆=new, 秒=round(dt, 1), 鏡頭在後=round(gm.cam_back, 2),
                      各面正前方步數=[round((self.gmap_front(v[1]) or 0) * gm.scale, 1) for v in views])
        self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] ⚙ 程式替你站著不動、一次轉 90 度看了四面，用視覺深度把四周的牆畫進陣列地圖"
                            f"（新畫 {new} 格牆），已轉回原本的朝向")
        return True

    def gmap_check(self, rays_pack):
        """不轉頭，用當下這一張畫面核對：照地圖這個方向應該幾步有牆，實際量到幾步。"""
        gm = self.gmap
        if not gm.scale:
            return
        obs = []
        for a, z in rays_pack["rays"]:
            d = z * gm.scale / max(0.2, math.cos(math.radians(a))) - gm.cam_back
            if 0.5 <= d <= self.GMAP_MAX_DIST:
                obs.append((a, d))
        if len(obs) < 3:
            return
        r = gm.check_pose(self.pos[0], self.pos[1], getattr(self, "yaw", 0.0), obs)
        if r["誤差"] is None:
            return
        self.stat["陣列地圖核對"] = self.stat.get("陣列地圖核對", 0) + 1
        if r["符合"]:
            self.gmap_hint = f"👁 視覺核對：前方的牆跟陣列地圖對得上（平均差 {r['誤差']:.1f} 步）。"
        else:
            self.stat["陣列地圖核對不符"] = self.stat.get("陣列地圖核對不符", 0) + 1
            sug = r["建議位置"]
            self.gmap_hint = (f"👁 視覺核對：**對不上**——照陣列地圖你前方的牆跟實際量到的平均差 {r['誤差']:.1f} 步，位置可能漂了。"
                              + (f"比較像是在 ({sug[0]:+.0f},{sug[1]:+.0f})（差 {r['建議誤差']:.1f} 步）。認得出自己在哪就用「我在」校正。"
                                 if sug else "附近找不到對得上的位置，程式下一輪會重掃這裡。"))
            if not sug:
                gm.scanned = [p for p in gm.scanned if math.hypot(p[0] - self.pos[0], p[1] - self.pos[1]) > 4.0]
        self.gmap_log("核對", 誤差=round(r["誤差"], 2), 符合=r["符合"],
                      建議=[round(v, 1) for v in r["建議位置"]] if r["建議位置"] else None)
        return r["符合"]

    def gmap_check_objects(self, rays_pack):
        """2026-10-02 使用者：一直用視覺確認東西的相對位置。照地圖畫面裡應該看得到的互動物／觸碰物，
        量那個方向的深度，看距離對不對得上。"""
        import grid_map
        gm = self.gmap
        if not gm.scale or not gm.objects:
            return ""
        rays = rays_pack["rays"]
        out = []
        for o, ang, d in gm.objects_in_view(self.pos[0], self.pos[1], getattr(self, "yaw", 0.0)):
            near = [z for a, z in rays if abs(a - ang) <= 8]
            if not near:
                continue
            z = sorted(near)[len(near) // 2]
            seen = z * gm.scale / max(0.2, math.cos(math.radians(ang))) - gm.cam_back
            ok = abs(seen - d) <= max(1.5, d * 0.3)
            out.append(f"「{o['名稱']}」照地圖在你{grid_map._side(ang)} {d:.1f} 步，畫面量到那個方向 {seen:.1f} 步"
                       + ("（對得上）" if ok else ("（**更近的地方有東西擋著**）" if seen < d else "（**那裡可能沒有它，或你位置漂了**）")))
            self.gmap_log("物件核對", 名稱=o["名稱"], 地圖步數=round(d, 1), 量到=round(seen, 1), 符合=ok)
        return ("👁 東西的位置：" + "；".join(out)) if out else ""

    def gmap_tick(self):
        """每輪一次（截圖之後、組面板之前）。"""
        if not self.gmap_on():
            return
        try:
            self.gmap_load()
            gm = getattr(self, "gmap", None)
            if gm is None:
                return
            gm.mark_walked(self.pos[0], self.pos[1])
            rnd = self.stat.get("輪數", 0)
            busy = bool(getattr(self, "_plan_q", None)) or getattr(self, "desk_lock", 0) >= 2 or self.dry
            level = abs(getattr(self, "pitch_deg", 0.0)) <= 20
            # 比例要他自己量（10/01）；但沒比例也照樣原地看四面、平常也照樣看——看到的先存著，量好比例一次補畫（10/02）
            if (not busy and level and gm.need_scan(self.pos[0], self.pos[1])
                    and rnd >= getattr(self, "_gmap_retry_at", 0)):
                self.gmap_scan()
            elif level and rnd % self.GMAP_CHECK_EVERY == 0:
                # 2026-10-02 使用者：要一直用視覺確認牆壁的正確距離與東西的相對位置
                r = self.gmap_look()
                if r:
                    if not gm.scale:
                        gm.keep_view(self.pos[0], self.pos[1], getattr(self, "yaw", 0.0), r["rays"], r["self"])
                        gm.save()
                    else:
                        fit = self.gmap_check(r)
                        if fit is not False:     # 位置對得上（或前方還沒畫過牆）才拿這一面更新牆：畫錯的牆會被視線穿過扣掉
                            n = gm.scan(self.pos[0], self.pos[1], getattr(self, "yaw", 0.0), r["rays"],
                                        max_dist=self.GMAP_MAX_DIST, self_depth=r["self"])
                            gm.save()
                            self.gmap_log("視覺更新牆", 新牆=n)
                        self.gmap_obj_hint = self.gmap_check_objects(r)
        except Exception as e:  # noqa: BLE001
            print(f"   陣列地圖這一輪出錯（不影響他玩）：{e}")
            self.gmap_log("出錯", 原因=str(e)[:160])

    def gmap_text(self):
        gm = getattr(self, "gmap", None)
        if gm is None:
            return ""
        hint = NL.join(x for x in (getattr(self, "gmap_hint", ""), getattr(self, "gmap_obj_hint", "")) if x)
        if not gm.scale:
            ref = ("、".join(str(x) for x in gm.samples[-6:]) if gm.samples else "（還沒有）")
            hint = (hint + NL if hint else "") + (
                "📏 **要畫地圖，第一步就是量比例**（使用者 10/02）。深度比例還沒定，牆畫不出來——要你自己量"
                + (f"（程式已經替你原地看過、先存著 {len(gm.pending)} 面，量好就一次補畫）" if gm.pending else "")
                + "：走到正對一面牆、前方還有 3 步以上空間、沒有面板蓋著的地方，"
                '送 {"t":"量比例"}；程式會替你走 1.2 秒、比較前後的深度，算出「深度 1 單位＝幾步」。'
                '自己算好了也可以直接 {"t":"定比例","比例":0.5}。'
                f"程式在你走路時順便量到的參考值：{ref}（很散，只當參考）。")
        tail = ("這張地圖是**陣列**（同一個場景只有一張）：出生點是原點；第一次到一個地方，程式替你站著不動、一次轉 90 度看四面，用視覺深度畫牆；"
                "之後每幾輪用當下的畫面再核對牆的距離和東西的位置；你的位置用「走了幾秒＋轉了幾度」推算。"
                + NL + '要去某一格或某個記過的東西：{"t":"走到記過的","name":"名字"}，程式會照陣列找一條不穿牆的路帶你走。'
                + NL + '互動成功的東西程式會自動記成互動物；你自己也可以記：'
                + '{"t":"記互動物","name":"名字","功能":"按了會怎樣","用法":"怎麼按","前":1}、'
                + '{"t":"記觸碰物","name":"名字","功能":"站上去會怎樣","用法":"怎麼站"}（記在你腳下那一格）。')
        return NL.join(x for x in (gm.text(self.pos[0], self.pos[1], getattr(self, "yaw", 0.0), radius=8), hint, tail) if x)

    MEASURE_FILE = "量測值.json"

    def measure_key(self):
        """量測值依場景（空間名）分開記，跟陣列地圖同一把鑰匙（10/02 起不再用 Roblox place）。"""
        return str(getattr(self, "space", "") or self.SPACE0)

    def gmap_replay(self):
        """比例剛定好：把之前沒比例時存著的視野一次補畫成牆。"""
        gm = getattr(self, "gmap", None)
        if gm is None or not gm.scale or not gm.pending:
            return 0
        n = len(gm.pending)
        new = gm.replay_pending(max_dist=self.GMAP_MAX_DIST)
        gm.save()
        print(f"   🗺 陣列地圖：比例定了，把之前存著的 {n} 面視野補畫成牆，新畫 {new} 格")
        self.gmap_log("補畫", 視野=n, 新牆=new)
        self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] ⚙ 比例定了，程式把你之前轉頭看過的 {n} 面補畫進陣列地圖（新畫 {new} 格牆）")
        return new

    def measures_load(self):
        try:
            return json.load(open(os.path.join(self.dir, self.MEASURE_FILE), encoding="utf-8")) or {}
        except (OSError, json.JSONDecodeError):
            return {}

    def measures_save_scale(self, v, how):
        """2026-10-01 使用者：量過的數值記在**這支遊戲**的檔案裡（每支遊戲不同）。
        深度比例依 place 分開記，另存一個「預設」給同一支遊戲還沒量過的場景用；順便把視角刻度抄一份進來，一眼看全。"""
        try:
            d = self.measures_load()
            key = self.measure_key()
            stamp = datetime.now().strftime("%m/%d %H:%M")
            d.setdefault("深度比例", {})[key] = {"值": v, "方法": how, "時間": stamp}
            d["深度比例"]["預設"] = {"值": v, "來自": key, "時間": stamp}
            try:
                cal = json.load(open(os.path.join(self.dir, "視角校準.json"), encoding="utf-8")) or {}
                d["視角刻度"] = {"每度dx": (cal.get("水平") or {}).get("每度dx"), "一圈dx": (cal.get("水平") or {}).get("一圈dx"),
                               "每度dy": (cal.get("垂直") or {}).get("每度dy"), "時間": (cal.get("水平") or {}).get("時間")}
            except Exception:  # noqa: BLE001
                pass
            d["說明"] = "他自己量的數值，只對這支遊戲成立：深度比例（深度 1 單位＝幾步，依 place）、視角刻度（每度 dx／dy）"
            json.dump(d, open(os.path.join(self.dir, self.MEASURE_FILE), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            man = getattr(self, "manual", None)
            if isinstance(man, dict):
                man["深度比例"] = f"深度 1 單位＝{v} 步（{how}，{stamp}，場景 {key}）"
                self.save_manual()
        except Exception as e:  # noqa: BLE001
            print(f"   量測值存檔失敗：{e}")

    def gmap_measure(self):
        """他送「量比例」：程式替他走 1.2 秒、比較走前走後正前方的深度，算出「深度 1 單位＝幾步」並定案。
        要站在正對一面牆、前方還有幾步空間的地方量才準；量不出來會告訴他為什麼。"""
        gm = getattr(self, "gmap", None)
        if gm is None or not self.gmap_on():
            print("   ⚠ 陣列地圖沒開，量不了")
            return
        r0 = self.gmap_look()
        if not r0:
            return
        z0 = self.gmap_front(r0["rays"])
        self._prog_walk = True
        try:
            walked, why = self.walk_guarded("w", 1.2)
        finally:
            self._prog_walk = False
        th = math.radians(getattr(self, "yaw", 0.0))
        self.pos[0] += math.sin(th) * walked
        self.pos[1] += math.cos(th) * walked
        time.sleep(0.25)
        r1 = self.gmap_look()
        z1 = self.gmap_front(r1["rays"]) if r1 else None
        a1 = {round(a): z for a, z in (r1["rays"] if r1 else [])}
        diffs = sorted(z - a1[round(a)] for a, z in r0["rays"] if round(a) in a1 and z <= 14.0)
        dd = diffs[len(diffs) // 2] if diffs else None
        v = (walked / dd) if (dd and dd > 0.3 and walked >= 0.6) else None
        ok = v is not None and 0.08 <= v <= 1.5
        if ok:
            gm.scale = round(v, 3)
            gm.save()
            self.measures_save_scale(gm.scale, "量比例（程式替你走 1.2 秒量的）")
            self.gmap_replay()
            msg = f"量到：走 {walked:.1f} 秒，正前方深度 {z0:.2f}→{z1:.2f}（各線差的中位數 {dd:.2f}）→ 深度 1 單位＝{gm.scale} 步。已定案，之後開始畫牆"
        else:
            msg = (f"沒量成：走 {walked:.1f} 秒，正前方深度 {z0}→{z1}" + (f"（{why}）" if why else "")
                   + "。要正對一面牆、前方還有 3 步以上空間、沒有面板蓋著，再量一次")
        print(f"   🗺 {msg}")
        self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] ⚙ 你送了 量比例，程式替你走了 {walked:.1f} 秒：{msg}")
        self.gmap_hint = "📏 " + msg
        self.gmap_log("量比例", 走了=round(walked, 2), 深度前=z0, 深度後=z1, 各線差的中位數=dd, 比例=gm.scale if ok else None, 成功=ok, 來源="他自己")
        self.note_progress("量比例")

    def gmap_set_scale(self, a):
        """他送「定比例」：自己算好的「深度 1 單位＝幾步」。"""
        gm = getattr(self, "gmap", None)
        if gm is None:
            return
        try:
            v = float(a.get("比例") or a.get("v") or 0)
        except (TypeError, ValueError):
            v = 0.0
        if not (0.05 <= v <= 3.0):
            print(f'   ⚠ 定比例要給 0.05～3 之間的數字：{{"t":"定比例","比例":0.5}}（你給的是 {a.get("比例")}）')
            return
        gm.scale = round(v, 3)
        gm.save()
        self.measures_save_scale(gm.scale, "定比例（他自己填的）")
        self.gmap_replay()
        print(f"   🗺 他自己定了深度比例：1 單位＝{gm.scale} 步")
        self.gmap_log("量比例", 比例=gm.scale, 成功=True, 來源="他自己定")
        self.note_progress("定比例")

    def gmap_add(self, kind, a):
        """他自己記的互動物／觸碰物。互動物在他前面（不可穿透，所以不會是他站的那一格）；觸碰物預設在腳下。"""
        gm = getattr(self, "gmap", None)
        if gm is None:
            print("   ⚠ 陣列地圖沒開，記不了")
            return
        nm = str(a.get("name") or a.get("名稱") or "").strip()[:20]
        if not nm:
            print(f'   ⚠ 要寫 name：{{"t":"記{kind}","name":"名字","功能":"…","用法":"…"}}')
            return
        try:
            ahead = float(a.get("前", 1 if kind == "互動物" else 0) or 0)
        except (TypeError, ValueError):
            ahead = 1.0 if kind == "互動物" else 0.0
        if kind == "互動物":
            ahead = max(1.0, ahead)
        th = math.radians(getattr(self, "yaw", 0.0))
        x, y = self.pos[0] + math.sin(th) * ahead, self.pos[1] + math.cos(th) * ahead
        i = gm.add_object(kind, x, y, nm, str(a.get("功能") or ""), str(a.get("用法") or a.get("使用方式") or ""))
        gm.save()
        print(f"   🗺 陣列地圖記下{kind}「{nm}」：格 {gm.cell_of(x, y)}（一維陣列第 {i} 筆）")
        self.gmap_log("記物件", 種類=kind, 名稱=nm, 格=list(gm.cell_of(x, y)), 功能=str(a.get("功能") or "")[:40],
                      用法=str(a.get("用法") or "")[:40], 來源="他自己")
        self.note_progress(f"記{kind}")

    def gmap_walk(self, name, x, y, budget=6.0):
        """照陣列地圖走：A* 找一條不穿牆的路，換成「轉幾度、走幾秒」照做。回傳 True＝有照陣列帶路。"""
        gm = getattr(self, "gmap", None)
        if gm is None or not self.gmap_on():
            return False
        segs = gm.route((self.pos[0], self.pos[1]), getattr(self, "yaw", 0.0), (x, y))
        if not segs:
            self.gmap_log("路線", 去=name, 結果="找不到路")
            return False
        plan = [{"轉": s["轉"], "走": s["走"]} for s in segs]
        print(f"   🗺 照陣列地圖走去「{name}」：" + " → ".join(f"轉 {s['轉']:+.0f}° 走 {s['走']:.1f}s" for s in segs[:6]))
        used, done, why = 0.0, 0, ""
        for s in segs:
            if used >= budget:
                why = "這一輪的時間用完，下一輪接著走"
                break
            left = s["轉"]
            for _ in range(8):
                if abs(left) < 4:
                    break
                step = max(-1, min(1, left)) * min(abs(left), self.MAX_DX / (self.px_per_pct or 4.0) * 0.70)
                self.turn_by(step)
                left -= step
                time.sleep(0.12)
            need = min(s["走"], budget - used)
            while need > 0.15:
                # 比例還沒定案時，程式帶路走的這一段也拿來取樣（開了強制腦內地圖後，他自己很少直接按 w）
                _r0 = None
                try:
                    if not gm.scale:
                        _r0 = self.gmap_look()
                except Exception:  # noqa: BLE001
                    _r0 = None
                self._prog_walk = True
                try:
                    walked, r = self.walk_guarded("w", min(need, 3.0))
                finally:
                    self._prog_walk = False
                try:
                    if _r0 is not None:
                        time.sleep(0.25)
                        self.gmap_sample(_r0, self.gmap_look(), walked)
                except Exception as e:  # noqa: BLE001
                    print(f"   陣列地圖取樣失敗：{e}")
                th = math.radians(getattr(self, "yaw", 0.0))
                self.pos[0] += math.sin(th) * walked
                self.pos[1] += math.cos(th) * walked
                gm.mark_walked(self.pos[0], self.pos[1])
                used += walked
                need -= max(walked, 0.15)
                if r:
                    why = r
                    break
            if why:
                break
            done += 1
        dist = math.hypot(x - self.pos[0], y - self.pos[1])
        self.stat["陣列地圖帶路"] = self.stat.get("陣列地圖帶路", 0) + 1
        self.gmap_log("路線", 去=name, 計畫=plan[:8], 做完段數=done, 走了=round(used, 1), 停因=why[:40], 離目標=round(dist, 1))
        self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] ⚙ 程式照陣列地圖帶你走去「{name}」："
                            f"{len(segs)} 段走了 {done} 段、{used:.1f} 秒，離目標還有 {dist:.1f} 步" + (f"（停下來：{why[:30]}）" if why else ""))
        self.note_progress("照陣列地圖走")
        return True

    # ---------------- 探索地圖：走過的、牆、還沒去過的
    CELL = 2.0               # 一格 2 步
    EXPLORE_R = 9            # 地圖畫他周圍幾格

    def cell_of(self, x, y):
        import math
        return (int(math.floor(x / self.CELL)), int(math.floor(y / self.CELL)))

    def explore_grid(self):
        """把走過的路、撞牆點、地標各自壓成格子。這是他的探索狀態。"""
        walked, walls = set(), set()
        for x, y in list(getattr(self, "trail_pts", []) or []) + [tuple(self.pos)]:
            walked.add(self.cell_of(x, y))
        for x, y in list(getattr(self, "wall_pts", []) or []):
            walls.add(self.cell_of(x, y))
        walls -= walked                    # 走得過去的就不是牆
        named = {}
        for nm, v in (getattr(self, "map", {}) or {}).items():
            named.setdefault(self.cell_of(v[0], v[1]), nm)
        for nm, v in (getattr(self, "spots", {}) or {}).items():
            named.setdefault(self.cell_of(v.get("x", 0), v.get("y", 0)), nm)
        return walked, walls, named

    def frontier(self, walked, walls):
        """未探索的邊界：貼著走過的格子、但自己沒走過也不是牆——那就是還可以去的地方。"""
        out = set()
        for cx, cy in walked:
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                c = (cx + dx, cy + dy)
                if c not in walked and c not in walls:
                    out.add(c)
        return out

    def explore_text(self):
        """畫一張他看得懂的俯視圖：走過的、牆、沒去過的、他站的地方。

        使用者要的是「讓他更好理解」，所以牆與未探索區都要標出來——
        看不到邊界，他就永遠只在走過的那幾格裡繞。
        """
        import math
        if self.gmap_on() and getattr(self, "gmap", None) is not None:
            return self.gmap_text()         # 2026-09-30：開了陣列地圖就用它取代舊的探索格子
        walked, walls, named = self.explore_grid()
        front = self.frontier(walked, walls)
        me = self.cell_of(self.pos[0], self.pos[1])
        R = self.EXPLORE_R
        rows = []
        legend = []
        n = 0
        for gy in range(me[1] + R, me[1] - R - 1, -1):
            line = []
            for gx in range(me[0] - R, me[0] + R + 1):
                c = (gx, gy)
                if c == me:
                    line.append("我")
                elif c in named:
                    n += 1
                    if n <= 9:
                        legend.append(f"{n}={named[c]}")
                    line.append(str(n) if n <= 9 else "＊")
                elif c in walls:
                    line.append("█")
                elif c in walked:
                    line.append("·")
                elif c in front:
                    line.append("？")
                else:
                    line.append(" ")
            rows.append("".join(line))
        head = (f"（一格 2 步，上面是北。你在正中央那個「我」，"
                f"面朝 {self.compass(getattr(self, 'yaw', 0.0))}）")
        key = "· 走過　█ 撞過牆　？ 沒去過但走得到（探索的下一個目標）　空白 完全不知道"
        stat = (f"走過 {len(walked)} 格、撞過牆 {len(walls)} 格、"
                f"貼著你的未探索有 {len(front)} 格")
        tip = ""
        if front:
            best = min(front, key=lambda c: (c[0] - me[0]) ** 2 + (c[1] - me[1]) ** 2)
            vx, vy = (best[0] - me[0]) * self.CELL, (best[1] - me[1]) * self.CELL
            deg = (math.degrees(math.atan2(vx, vy))) % 360
            tip = (f"→ 最近的未探索在你的 {self.compass(deg)}（{deg:.0f} 度）、"
                   f"大約 {math.hypot(vx, vy):.0f} 步。要開新地方就往那邊走。")
        return NL.join([head, "```", *rows, "```", key, stat]
                       + (["、".join(legend)] if legend else []) + ([tip] if tip else []))

    # ---------------- 文字眼睛：畫面上的字
    KEY_WORDS = ("press", "hold", "[e]", " e ", "check", "shift", "anomaly", "report",
                 "exit", "continue", "close", "confirm", "accept", "decline", "print",
                 "room", "patient", "photo", "camera", "sanity", "shop", "buy",
                 "是的", "確定", "取消", "關閉", "繼續", "離開", "重生", "商店", "購買",
                 "報到", "登記", "異常", "正常", "病人", "房間", "拍照", "列印", "輪班")

    def zone(self, x, y):
        v = "上" if y < 0.34 else ("中" if y < 0.7 else "下")
        h = "左" if x < 0.34 else ("中" if x < 0.67 else "右")
        return v + h

    def text_lines(self):
        try:
            lines, stamp = self.text_eyes.latest()
        except Exception:  # noqa: BLE001
            return [], 0.0
        if not stamp or time.time() - stamp > 6.0:
            return [], stamp
        return lines, stamp

    def text_set(self):
        """現在畫面上讀到的字（集合，正規化過）——拿來比對點之前／點之後差在哪。"""
        try:
            lines, stamp = self.text_lines()
        except Exception:  # noqa: BLE001
            return set()
        if not stamp:
            return set()
        import zh_text as _z
        return {_z.norm(l.get("t", "")) for l in lines if str(l.get("t", "")).strip()}

    def watch_click(self, name, x=None, y=None):
        """2026-09-20 使用者：**按完每一顆 UI 都要看字**（反射輪也要）。

        點下去的當下先把畫面上的字記起來，下一輪 OCR 更新後自動比對：
        多出哪些字＝這顆按鈕打開了什麼；少了哪些字＝它關掉了什麼。
        結果會寫進按鈕簿的「效果」，慢慢長成一張 UI→功能 的對照表。
        """
        self._click_watch = {"名": str(name or "")[:16], "字": self.text_set(),
                             "時": time.time(), "x": x, "y": y}

    def check_click_effect(self):
        """每輪開頭呼叫：上一次點擊的效果現在看得出來了嗎？"""
        w = getattr(self, "_click_watch", None)
        if not w:
            return
        if time.time() - w["時"] < 0.9:        # OCR 每 0.8 秒一輪，等它更新完再比
            return
        self._click_watch = None
        now = self.text_set()
        if not now and not w["字"]:
            return
        try:
            lines, _ = self.text_lines()
            raw = {}
            import zh_text as _z
            for l in lines:
                raw[_z.norm(l.get("t", ""))] = str(l.get("t", ""))[:14]
        except Exception:  # noqa: BLE001
            raw = {}
        new = [raw.get(k, k) for k in (now - w["字"]) if k][:6]
        gone = [k for k in (w["字"] - now) if k][:6]
        nm = w["名"] or "那顆按鈕"
        if not new and not gone:
            self.click_effect_hint = (f"🔎 你剛才按了「{nm}」，**畫面上的字完全沒變**——"
                                      f"那一下沒有生效。不要再點同一個地方，換做法"
                                      f"（先拉遠解鎖滑鼠、先關掉擋住的東西、或走到觸發區站上去）。" + NL)
            print(f"   🔎 按「{nm}」之後字沒變＝沒生效")
            return
        bits = []
        if new:
            bits.append("**多出來的字**：" + "、".join(new))
        if gone:
            bits.append("不見的字：" + "、".join(gone[:4]))
        self.click_effect_hint = (f"🔎 你剛才按了「{nm}」→ " + "；".join(bits)
                                  + "。**先讀完這些新字再決定下一步**"
                                  + "（上面通常就寫著接下來要按什麼）。" + NL)
        print(f"   🔎 按「{nm}」之後：" + "；".join(bits))
        if new:
            try:      # 順便把「這顆 UI 會開出什麼」記進按鈕簿，長成 UI→功能 對照表
                b = self.btn_memo.get(nm)
                if b is not None and not b.get("他自己寫的"):
                    b["效果"] = ("會跳出：" + "、".join(new[:3]))[:40]
                    self.save_buttons_memo()
            except Exception:  # noqa: BLE001
                pass

    def text_text(self):
        """把畫面上讀到的字整理給他看：分區、標重點、標出跟上一輪不同的新字。"""
        lines, stamp = self.text_lines()
        if not stamp:
            return "（文字眼睛還在暖機，這一輪先靠畫面判斷）"
        if not lines:
            self._prev_text = set()
            return "（畫面上沒有讀到任何字。這通常代表你正對著牆、地板或天花板——沒有字就沒有線索）"
        prev = getattr(self, "_prev_text", set())
        now = {l["t"] for l in lines}
        rows = []
        for l in sorted(lines, key=lambda d: -d["h"])[:18]:
            low = l["t"].lower()
            hot = any(k in low for k in self.KEY_WORDS)
            tag = "⭐" if hot else "　"
            new = "（新出現）" if l["t"] not in prev else ""
            w = l.get("w")
            box = (f"　中心({l['x']:.2f},{l['y']:.2f})　框 {w:.2f}×{l['h']:.2f}"
                   if w is not None else f"　中心({l['x']:.2f},{l['y']:.2f})")
            rows.append(f"{tag}[{self.zone(l['x'], l['y'])}] {l['t']}{new}{box}")
        self._prev_text = now
        head = (f"（本機 OCR 讀的，比你用看的準；共 {len(lines)} 段，"
                f"下面按字的大小排，⭐ 是可能要你動作的字）")
        tail = ("→ **有字就先讀完再決定**。按鈕上寫什麼就照著做，不要用猜的；"
                "看到 E 或 Press 才代表按得動，沒有那個字按了也是白按。" + NL
                + '→ **要點某個字最穩的方法**：{"t":"點字","字":"那幾個字"}——'
                + "程式會用 OCR 當下量到的框中心去點，不用你自己猜座標，也不會點偏。")
        return NL.join([head] + rows + [tail])

    def has_text(self, *words):
        """畫面上有沒有這些字（給程式端的防線用，例如死亡畫面、離開確認）。"""
        lines, stamp = self.text_lines()
        if not stamp:
            return ""
        low = " ".join(l["t"].lower() for l in lines)
        for w in words:
            if str(w).lower() in low:
                return str(w)
        return ""

    # ---------------- 懲罰：做了不該做的事，要留下紀錄
    # 使用者 09/11 改制：程式不再擋他（花錢除外），原本會被擋的行為改成扣 1 分；
    # 真正會毀掉一整班的事，扣分拉到讓他不敢賭的程度。
    PENALTY = {
        # —— 嚴重：一次就抵掉好幾次成功 ——
        "碰到花錢的東西": 300,
        # 2026-09-19 使用者：**死亡的扣分降到 10**。死一次的真實代價是「要重跑一遍」，
        # 那本來就夠痛了；扣 150 只會讓他怕死到不敢進關卡、寧可待在大廳。
        "死掉": 0,             # 2026-09-23 使用者：死亡不扣分（程式也不再呼叫 punish("死掉")，只留死亡檢討）
        # 2026-09-19 使用者：**退出遊戲的扣分要降下來**。原本 120 分讓他不敢退，
        # 結果寧可對著一面牆耗一小時（那樣扣得更多）。退出重進本來就是他該用的解法。
        "沒事退出遊戲": 20,
        "給自己偏袒的評分": 80, "放棄自己訂的任務": 60,
        # —— 小毛病：只記錄，一次 1 分 ——
        # 2026-09-12 使用者：照他自己提的操作分數表調權重
        # （他把自己最常犯的三項全部主動加重，理由是照舊的 3/1/1 根本不痛不癢）
        "沒有目的地就亂走": 20, "走不到目的地": 3,
        # 2026-09-12 使用者：一直不動也要罰。比亂走更糟——亂走至少還在換位置
        "一直待在原地": 25,
        "面壁": 8, "空按E": 10, "卡在選單": 1,
        "大幅度轉頭": 1, "鏡頭頂到天花板": 15, "鏡頭頂到地板": 15,
        # 2026-09-24 使用者：避免他甩視角，用懲罰讓他自己避免。
        # 上下大幅轉（|dy|≥150）之後 60 秒內又往反方向大幅轉＝來回甩。
        "上下來回甩視角": 20,
        # 2026-09-13 使用者：都不做事（不移動、不互動、也不轉視角）＝ −100
        "重複同一個動作": 1, "原地空轉": 100, "esc連按": 1,
        # 2026-09-21 使用者：切成幾步就要在幾輪內做完，超過就扣分。
        # 同日下午改成 −3：規則照舊（第一次超支扣一次，之後每多拖 3 輪再扣一次），
        # 但一次只扣 3 分——他超支多半是步驟切得太細，不是偷懶，罰 30 分把扣分表洗版了
        # （09/21 一天扣了 832 次＝−24960，蓋掉其他扣分的訊號）。
        "步數超支": 3,
        # 2026-09-29 使用者：「有做事就不算，要鼓勵他調用工具，但是沒有直接完成小動作要處罰」
        # ——一個小步驟超過兩輪沒做完、或想了卻沒送動作，各 −20（工具本身不加分，免得刷工具）。
        "小步驟沒直接做完": 20, "想了卻沒做": 20,
    }
    SMALL = ("沒有目的地就亂走", "走不到目的地", "面壁", "空按E", "卡在選單", "大幅度轉頭",
             "鏡頭頂到天花板", "鏡頭頂到地板", "重複同一個動作", "原地空轉", "esc連按")
    # 「商店」「購買」是常駐按鈕的字，不是事件——放進來會把按鈕當成他跳出購買框（誤扣過 351 分）。
    # 只留真正的付費視窗才會出現的字。
    SHOP_WORDS = ("robux", "buy now", "get more robux", "儲值", "special offer",
                  "gamepass", "confirm purchase", "確認購買", "立即購買", "付款")

    def load_penalty(self):
        self.penalty = {"總分": 0, "明細": {}}
        try:
            with open(os.path.join(self.dir, "懲罰.json"), encoding="utf-8") as f:
                self.penalty = json.load(f) or self.penalty
        except Exception:  # noqa: BLE001
            pass
        self._shop_seen = 0.0
        print(f"[懲罰] 目前累積 {self.penalty.get('總分', 0)} 分："
              + ("、".join(f"{k}×{v}" for k, v in (self.penalty.get("明細") or {}).items()) or "（乾淨）"))

    def log_mistake(self, kind, n, why=""):
        """犯錯紀錄表（使用者 2026-09-13 要求）：每一次扣分都留一列，
        寫清楚**他當時在做什麼、送了哪些操作、前十五秒做過什麼**。

        以前只有「懲罰紀錄.md」寫了扣幾分，看不出他是怎麼走到那一步的；
        要看出「每次犯這個錯之前他都在做同一件蠢事」，就得把操作一起記下來。
        """
        try:
            acts = [a for a in (getattr(self, "_acts_now", None) or []) if isinstance(a, dict)]
            did = "、".join(self.describe(a) for a in acts[:3]) or "（這一輪沒送動作）"
            try:
                pdesc, _ = self.pitch_from_eyes()
            except Exception:  # noqa: BLE001
                pdesc = ""
            row = {
                "時間": datetime.now().strftime("%m/%d %H:%M:%S"),
                "犯了什麼": kind, "扣分": n, "為什麼扣": str(why)[:80],
                "在做什麼": f"{self.flow} 第{self.step}步",
                "任務": str(getattr(self, "cur_task", "") or "")[:20],
                "他說看到": str(getattr(self, "last_seen", "") or "")[:40],
                "他送的操作": did[:90],
                "視角": str(pdesc)[:30],
                "位置": [round(x, 1) for x in (getattr(self, "pos", None) or [0, 0])],
                "理智": getattr(self, "san", None),
                "輪": self.stat.get("輪數", 0),
                "前十五秒": [t.strip() for t in
                             self.trace_text(15.0, limit=6).split(NL) if t.strip()][-6:],
                "他自己訂的規則": (self.lessons.get(kind) or {}).get("規則", ""),
            }
            with open(os.path.join(self.dir, "犯錯紀錄.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + NL)
            with open(os.path.join(self.dir, "犯錯紀錄.md"), "a", encoding="utf-8") as f:
                f.write(f"| {row['時間']} | {kind} −{n} | {row['在做什麼']} | "
                        f"{row['他送的操作']} | {row['他說看到']} | {row['視角']} |" + NL)
            self.mistakes = (getattr(self, "mistakes", []) + [row])[-400:]
        except Exception as e:  # noqa: BLE001
            print(f"   犯錯紀錄寫入失敗：{e}")

    def mistakes_digest(self, kind, n=6):
        """同一種錯最近幾次，他每一次都在做什麼——檢討時攤給他看。"""
        rows = [r for r in (getattr(self, "mistakes", None) or []) if r.get("犯了什麼") == kind][-n:]
        if not rows:
            return "（還沒有紀錄）"
        out = []
        for r in rows:
            out.append(f"　{r['時間']}｜{r['在做什麼']}｜你送了：{r['他送的操作']}"
                       f"｜你說看到：{r['他說看到']}｜視角：{r['視角']}")
        return NL.join(out)

    def punish(self, kind, why=""):
        n = self.PENALTY.get(kind, 1)
        # 出事的時刻要當場記下來：檢討本身要花四十幾秒，
        # 等進了 learn_from_penalty 再用 time.time() 往前抓，抓到的全是檢討期間的空白。
        self._pen_at = getattr(self, "_pen_at", {})
        self._pen_at[kind] = time.time()
        try:
            self.trace_add(f"⛔ 被扣 {n} 分：{kind}", kind="扣分")
        except Exception:  # noqa: BLE001
            pass
        self.log_mistake(kind, n, why)
        self.penalty["總分"] = self.penalty.get("總分", 0) + n
        d = self.penalty.setdefault("明細", {})
        d[kind] = d.get(kind, 0) + 1
        self.penalty["最後"] = f"{datetime.now().strftime('%H:%M')} {kind}"
        try:
            with open(os.path.join(self.dir, "懲罰.json"), "w", encoding="utf-8") as fh:
                json.dump(self.penalty, fh, ensure_ascii=False)
            with open(os.path.join(self.dir, "懲罰紀錄.md"), "a", encoding="utf-8") as fh:
                fh.write(f"- {datetime.now().strftime('%m/%d %H:%M:%S')} **{kind}** −{n} 分"
                         + (f"（{why}）" if why else "") + NL)
        except Exception:  # noqa: BLE001
            pass
        print(f"   ⛔ 懲罰 −{n}：{kind}{('　' + why) if why else ''}"
              f"（累計 {self.penalty['總分']} 分）")
        if kind in self.SMALL:
            # 小毛病不特別跳提示，只累積在計分板上讓他自己看見
            self.punish_hint = getattr(self, "punish_hint", "") or ""
        else:
            self.punish_hint = (f"⛔⛔ **你被扣了 {n} 分：{kind}**。"
                                f"{why} 今天累計被扣 {self.penalty['總分']} 分。"
                                f"這種扣分一次就抵掉好幾次成功——**不要再賭這一次**。" + NL)
        self.log({"時間": datetime.now().strftime("%H:%M:%S"), "模式": "懲罰",
                  "項目": kind, "扣分": n, "累計": self.penalty["總分"]})
        try:
            self.maybe_review(kind, n)      # 使用者要求：被扣分就要自己學、自己改
        except Exception as e:  # noqa: BLE001
            print(f"   檢討排程失敗：{e}")

    def check_shop(self):
        """畫面上出現購買框＝扣分。這是使用者定的：他不該把時間花在會花錢的視窗上。"""
        lines, stamp = self.text_lines()
        if not stamp:
            return
        # 要「大字」才算真的彈出視窗；常駐的小按鈕不算
        big = {l["t"].lower() for l in lines if l.get("h", 0) >= 0.025}
        hit = ""
        for w in self.SHOP_WORDS:
            if any(w in t for t in big):
                hit = w
                break
        prev = getattr(self, "_shop_prev", set())
        self._shop_prev = big
        if not hit:
            return
        if hit in " ".join(prev):
            return                     # 上一輪就在了＝不是這一輪才跳出來的
        if time.time() - getattr(self, "_shop_seen", 0.0) < 600:
            return                     # 同一個框十分鐘內只扣一次
        self._shop_seen = time.time()
        self.punish("碰到花錢的東西", f"畫面上讀到「{hit}」。不准花任何錢，看到就馬上關掉（右上角叉叉，其次 esc）。")


    # ---------------- 他自己訂的目標
    GOAL_ASK = """【訂目標】使用者要你**自己訂一個目標，然後做到**。

不要訂「熟悉環境」「多練習」這種做不完也驗不出來的願望。
要訂一個**數得出來、今天之內驗得出成敗**的目標，例如
「完成 3 次 Check In」「把走廊五個房間門口都拍成地標」「一整輪不貼牆」。

**還有一件事：你要自己估這件事要花幾分鐘。**
你訂的任務跟報到治療一樣有倒數獎金：一開始值 200 分，**時間到之前做完拿滿分**，
超過你自己估的時間就開始每分鐘扣 10 分，一路扣到保底 50 分為止。
估太短會很快開始扣，估太長則是你自己拖自己——照你真正需要的時間估。

只輸出 JSON：{"目標":"(<=30字，要能用數字驗收)","為什麼選它":"(<=40字)","怎麼算達成":"(<=30字)","第一步":"(<=30字)","預計幾分鐘":10}"""

    def load_goal(self):
        self.goal = {}
        try:
            with open(os.path.join(self.dir, "目標.json"), encoding="utf-8") as f:
                self.goal = json.load(f) or {}
        except Exception:  # noqa: BLE001
            self.goal = {}
        items = self.goal.get("清單") or []
        if items:
            print("[目標] 使用者交代的三件事："
                  + "／".join(("✅" if x.get("達成") else "☐") + x["名"] for x in items))
        elif self.goal.get("目標"):
            print(f"[目標] 他自己訂的：{self.goal['目標']}（{self.goal.get('怎麼算達成', '')}）")

    def set_goal(self):
        """讓他自己訂今天的目標。訂完之後每一輪都會看到，直到他自己宣告達成。"""
        print("   🎯 讓他自己訂一個目標…")
        try:
            uri, _ = jpeg_data_uri(self.draw_eyes(draw_grid(self.grab_still(1024))), 70)
            raw, _, _, _ = stream_json(
                self.endpoint, self.model, self.sys_prompt(),
                [{"type": "image_url", "image_url": {"url": uri}},
                 {"type": "text", "text": "你最近做過的事：" + NL + NL.join(self.history[-12:])
                                          + NL + NL + "今天的成績：報到 "
                                          + str(self.stat.get("登記", 0)) + " 次、最高輪班 "
                                          + str(self.score.get("最高輪班", 0))
                                          + NL + NL + self.GOAL_ASK}],
                300, time.time() + 90, 0.4)
        except Exception as e:  # noqa: BLE001
            print(f"   訂目標失敗：{e}")
            return
        g = parse_json_loose(raw) or {}
        if not g.get("目標"):
            print("   目標沒解析出來，下次再訂")
            return
        self.goal = {"目標": str(g.get("目標", ""))[:40],
                     "為什麼選它": str(g.get("為什麼選它", ""))[:60],
                     "怎麼算達成": str(g.get("怎麼算達成", ""))[:40],
                     "第一步": str(g.get("第一步", ""))[:40],
                     "預計幾分鐘": max(1, min(120, int(float(g.get("預計幾分鐘", 10) or 10)))),
                     "開始於": time.time(),
                     "訂於": datetime.now().strftime("%m/%d %H:%M"),
                     "訂時輪數": self.stat.get("輪數", 0), "達成": False}
        try:
            with open(os.path.join(self.dir, "目標.json"), "w", encoding="utf-8") as f:
                json.dump(self.goal, f, ensure_ascii=False, indent=1)
            with open(os.path.join(self.dir, "目標紀錄.md"), "a", encoding="utf-8") as f:
                f.write(f"## {self.goal['訂於']}　{self.goal['目標']}" + NL
                        + f"- 為什麼：{self.goal['為什麼選它']}" + NL
                        + f"- 怎麼算達成：{self.goal['怎麼算達成']}" + NL
                        + f"- 第一步：{self.goal['第一步']}" + NL + NL)
        except Exception:  # noqa: BLE001
            pass
        print(f"   🎯 他訂的目標：{self.goal['目標']}（怎麼算達成：{self.goal['怎麼算達成']}）")
        self.log({"時間": datetime.now().strftime("%H:%M:%S"), "模式": "訂目標", **self.goal})

    def goal_done(self, ok=True):
        g = getattr(self, "goal", None) or {}
        items = g.get("清單") or []
        if items:
            for it in items:
                if not it.get("達成"):
                    if ok:
                        it["達成"] = True
                        it["何時"] = datetime.now().strftime("%m/%d %H:%M")
                        print(f"   🎯✅ 目標達成（他自己宣告）：{it['名']}")
                        if "治" in it["名"]:
                            self.award("治好一個病人", "他自己宣告治療完成。", value=self.clock_value())
                            self.clock_reset("治療完成，下一位重新計時")
                    else:
                        print(f"   🎯 他說做不到：{it['名']}——還是要做，這三件是使用者交代的")
                    break
            self.save_goal()
            return
        if not g.get("目標"):
            return
        self.goal["達成"] = bool(ok)
        self.goal["結束於"] = datetime.now().strftime("%m/%d %H:%M")
        r = self.task_clock()
        if ok and r:
            self.award("做成自己訂的任務", f"「{self.goal.get('目標', '')}」", value=r[0])
        elif not ok:
            self.punish("放棄自己訂的任務",
                        f"「{self.goal.get('目標', '')}」——訂了就要做到，"
                        f"做完再爛也有保底 50 分，放棄是 0 分。")
        n = self.stat.get("輪數", 0) - int(self.goal.get("訂時輪數", 0) or 0)
        try:
            with open(os.path.join(self.dir, "目標.json"), "w", encoding="utf-8") as f:
                json.dump(self.goal, f, ensure_ascii=False, indent=1)
            with open(os.path.join(self.dir, "目標紀錄.md"), "a", encoding="utf-8") as f:
                f.write(f"- {self.goal['結束於']} **{'達成' if ok else '放棄'}**"
                        f"（花了 {n} 輪）：{self.goal['目標']}" + NL + NL)
        except Exception:  # noqa: BLE001
            pass
        print(f"   🎯 目標{'達成' if ok else '放棄'}：{self.goal['目標']}（花了 {n} 輪）")
        self.goal = {}
        self._goal_due = True          # 下一輪重訂一個

    def goal_check(self):
        """清單型目標：能用成績自動判定的就自動打勾，不用等他自己宣告。"""
        g = getattr(self, "goal", None) or {}
        items = g.get("清單") or []
        if not items:
            return
        base = g.get("起點") or {}
        now = {"報到": self.stat.get("登記", 0),
               "輪班": self.score.get("最高輪班", 0)}
        changed = False
        for it in items:
            if it.get("達成"):
                continue
            key = it.get("看哪個數字")
            need = int(it.get("要幾次", 1) or 1)
            if key and now.get(key, 0) - int(base.get(key, 0) or 0) >= need:
                it["達成"] = True
                it["何時"] = datetime.now().strftime("%m/%d %H:%M")
                changed = True
                print(f"   🎯✅ 目標達成（程式自動判定）：{it['名']}")
                self.log({"時間": datetime.now().strftime("%H:%M:%S"), "模式": "目標達成",
                          "項目": it["名"], "判定": "自動"})
        if changed:
            self.save_goal()

    def save_goal(self):
        try:
            with open(os.path.join(self.dir, "目標.json"), "w", encoding="utf-8") as f:
                json.dump(self.goal, f, ensure_ascii=False, indent=1)
        except Exception:  # noqa: BLE001
            pass

    def goal_text(self):
        g = getattr(self, "goal", None) or {}
        items = g.get("清單") or []
        if items:
            self.goal_check()
            done = sum(1 for x in items if x.get("達成"))
            rows = [f"**這是使用者直接交代的三件事，做完為止（已完成 {done}/{len(items)}）**"]
            cur = None
            for i, it in enumerate(items):
                if it.get("達成"):
                    rows.append(f"　☑ {i + 1}. {it['名']}　（{it.get('何時', '')} 完成）")
                else:
                    mark = "👉 現在做這件" if cur is None else "之後"
                    if cur is None:
                        cur = it
                    rows.append(f"　☐ {i + 1}. **{it['名']}**（{mark}）")
                    if it.get("怎麼算達成"):
                        rows.append(f"　　　怎麼算達成：{it['怎麼算達成']}")
            if cur is None:
                rows.append("→ 三件都做完了。跟使用者回報，然後自己訂下一個目標。")
            else:
                rows.append("→ 一次只做一件，從上面那個 👉 開始。前面沒做完不要跳過去做後面的。")
                rows.append("　 前兩件程式看得到成績會自動打勾；「治好一個病人」要你自己送 "
                            + ACT_GOAL_OK + " 宣告，宣告前先確認畫面上真的顯示治療完成。")
            n = self.stat.get("輪數", 0) - int(g.get("訂時輪數", 0) or 0)
            if n > 500 and cur is not None:
                rows.append(f"⚠ 已經 {n} 輪了還沒把「{cur['名']}」做完——現在的做法不對，換一個做法。")
            return NL.join(rows)
        if not g.get("目標"):
            return "（還沒有目標。下一輪程式會讓你自己訂一個，訂了就要做到。）"
        n = self.stat.get("輪數", 0) - int(g.get("訂時輪數", 0) or 0)
        rows = [f"**{g['目標']}**（{g.get('訂於', '')} 你自己訂的，已經過了 {n} 輪）",
                f"怎麼算達成：{g.get('怎麼算達成', '')}",
                f"你當初說第一步是：{g.get('第一步', '')}"]
        if g.get("為什麼選它"):
            rows.append(f"為什麼選它：{g['為什麼選它']}")
        rows.append("→ 這是**你自己訂的**，不是別人叫你做的。做到了送 " + ACT_GOAL_OK
                    + "；真的做不到就送 " + ACT_GOAL_NO + " 並在下一次重訂一個做得到的。")
        if n > 400:
            rows.append(f"⚠ 你已經花了 {n} 輪還沒達成——現在的做法顯然不對，換一個做法，不要重複同一套。")
        return NL.join(rows)

    # ---------------- 獎勵：做成該做的事，分數要大到蓋過所有小事
    REWARD = {"完成一次 Check In": 100, "治好一個病人": 600, "撐完一整輪班": 150,
              "第一次做成某件事": 20, "拍下新地標": 3, "腦中地圖印證正確": 2,
              "發現異變": 10, "走到目標並互動成功": 10, "做成自己訂的任務": 200,
              # 2026-09-12 他自己提的操作加分（使用者指示照他的表做）
              "一次到位的移動": 15, "有效的橫移避障": 12,
              "精準的視角修正": 10, "互動前的對準": 8,
              # 2026-09-17 使用者：**成功進場也加分**。判準是客觀的——
              # Roblox log 的 job id 換了＝真的加入了一場新的伺服器（不是他自己說進去了）。
              "成功進場": 30}

    def load_reward(self):
        self.reward = {"總分": 0, "明細": {}}
        try:
            with open(os.path.join(self.dir, "獎勵.json"), encoding="utf-8") as f:
                self.reward = json.load(f) or self.reward
        except Exception:  # noqa: BLE001
            pass
        self._rw_base = None
        print(f"[獎勵] 目前累積 {self.reward.get('總分', 0)} 分："
              + ("、".join(f"{k}×{v}" for k, v in (self.reward.get("明細") or {}).items())
                 or "（還沒得過分）"))

    def reset_scores(self, why=""):
        """每一局（一輪班）分數歸零（使用者 2026-09-13 指示）。

        累計分數跨了好幾天之後已經沒有意義：淨分永遠是幾萬的負數，
        他今天做得比昨天好也看不出來。改成每一局重新算，
        上一局的成績整包存進 分數歷史.jsonl，可以一局一局比。
        """
        r, p = getattr(self, "reward", {}) or {}, getattr(self, "penalty", {}) or {}
        got, lost = int(r.get("總分", 0)), int(p.get("總分", 0))
        row = {"結束於": datetime.now().strftime("%m/%d %H:%M:%S"), "原因": why,
               "輪班": getattr(self, "_shift_no", None), "輪數": self.stat.get("輪數", 0),
               "得分": got, "扣分": lost, "淨分": got - lost,
               "得分明細": dict(r.get("明細") or {}), "扣分明細": dict(p.get("明細") or {}),
               "登記": self.stat.get("登記", 0)}
        try:
            with open(os.path.join(self.dir, "分數歷史.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + NL)
        except Exception:  # noqa: BLE001
            pass
        self.reward = {"總分": 0, "明細": {}}
        self.penalty = {"總分": 0, "明細": {}}
        for nm, obj in (("獎勵.json", self.reward), ("懲罰.json", self.penalty)):
            try:
                with open(os.path.join(self.dir, nm), "w", encoding="utf-8") as f:
                    json.dump(obj, f, ensure_ascii=False)
            except Exception:  # noqa: BLE001
                pass
        self.pen_since = {}
        print(f"   🔄 這一局結束（{why}）：得 {got}、扣 {lost}、淨 {got - lost}"
              f" → 已存進分數歷史，**新的一局分數歸零**")
        self.score_reset_hint = (f"🔄 **新的一局開始，分數歸零。** 上一局你得 {got} 分、"
                                 f"扣 {lost} 分、淨 {got - lost} 分。"
                                 f"這一局從 0 開始重新算——上一局做不好不會跟著你，"
                                 f"但同樣的錯這一局再犯一次還是照扣。" + NL)

    def award(self, kind, why="", value=None):
        n = int(round(value)) if value is not None else self.REWARD.get(kind, 1)
        if value is not None:
            top = int(self.clock_par()[0] if getattr(self, "flow", "") in self.CLOCK_FLOWS else self.CLOCK_TOP)
            why = (why + f" 倒數獎金：滿分 {top}，你拿到 {n}（少拿的 {top - n} 分是拖掉的）。").strip()
        self.reward["總分"] = self.reward.get("總分", 0) + n
        d = self.reward.setdefault("明細", {})
        d[kind] = d.get(kind, 0) + 1
        self.reward["最後"] = f"{datetime.now().strftime('%H:%M')} {kind}"
        try:
            with open(os.path.join(self.dir, "獎勵.json"), "w", encoding="utf-8") as fh:
                json.dump(self.reward, fh, ensure_ascii=False)
            with open(os.path.join(self.dir, "獎勵紀錄.md"), "a", encoding="utf-8") as fh:
                fh.write(f"- {datetime.now().strftime('%m/%d %H:%M:%S')} **{kind}** +{n} 分"
                         + (f"（{why}）" if why else "") + NL)
        except Exception:  # noqa: BLE001
            pass
        big = n >= 50
        print(("   🏆🏆🏆 " if big else "   🏆 ") + f"獎勵 +{n}：{kind}"
              + (f"　{why}" if why else "") + f"（累計 {self.reward['總分']} 分）")
        self.reward_hint = (("🏆🏆🏆 **你剛剛拿到 %d 分：%s**！這是今天最有價值的一件事，"
                             "把你剛才那一串做法完整記下來（成功紀錄程式會自動存），"
                             "下一次照著再做一次。" % (n, kind)) if big else
                            ("🏆 +%d 分：%s" % (n, kind))) + NL
        self.log({"時間": datetime.now().strftime("%H:%M:%S"), "模式": "獎勵",
                  "項目": kind, "加分": n, "累計": self.reward["總分"]})
        # 使用者 2026-09-15 要的：成功了就停下來，**自己回推剛才是怎麼做到的**。
        # 跟扣分檢討完全對稱——扣分要問「為什麼會發生」，成功要問「我做了什麼才成功」。
        # 2026-09-17 使用者：關鍵的加分項要記下「前後做了什麼」
        try:
            self.key_move_start(kind, n, why)
        except Exception as e:  # noqa: BLE001
            print(f"   關鍵動作記不起來：{e}")
        self._rw_at = getattr(self, "_rw_at", {})
        self._rw_at[kind] = time.time()
        try:
            self.maybe_success_review(kind, n)
        except Exception as e:  # noqa: BLE001
            print(f"   成功回推失敗：{e}")

    def reward_poll(self):
        """用成績的變化自動發獎：報到成功、輪班往上跳，都是他自己做到的。"""
        now = {"報到": self.stat.get("登記", 0),
               "輪班": self.score.get("最高輪班", 0),
               "互動": self.stat.get("互動成功", 0),
               "異變": int(self.score.get("拒絕異常次數", 0) or 0) + self.stat.get("拒絕", 0)}
        base = getattr(self, "_rw_base", None)
        if base is None:
            self._rw_base = now
            return
        if now["報到"] > base["報到"]:
            for _ in range(now["報到"] - base["報到"]):
                v = self.clock_value()
                self.award("完成一次 Check In",
                           "成績欄的報到數字往上跳了——這就是你一整天在追的東西。", value=v)
                self.clock_reset("報到成功，下一位重新計時")
        if now["輪班"] > base["輪班"]:
            self.award("撐完一整輪班", f"輪班從 {base['輪班']} 變成 {now['輪班']}。")
        # 2026-09-14 補接：「發現異變」以前只寫在 REWARD 表與提示裡，**沒有任何地方發過獎**。
        # 這是這個遊戲的核心玩法（認出異變病人並拒絕），提示卻一直跟他說「發現異變 +10」。
        if now.get("異變", 0) > base.get("異變", 0):
            for _ in range(min(3, now["異變"] - base["異變"])):
                self.award("發現異變", "你認出了一個異變、而且處理掉了——這就是這個遊戲在考的事。")
        # 使用者 09/12：單純按成功一個東西不加分。
        # 只有「走到你說要去的目標，並且在那裡互動成功」才給 +10（見 hold_interact）。
        self._rw_base = now

    def score_text(self):
        r = getattr(self, "reward", None) or {}
        p = getattr(self, "penalty", None) or {}
        got, lost = r.get("總分", 0), p.get("總分", 0)
        rows = [f"**得分 {got}　扣分 {lost}　淨分 {got - lost}**"]
        d = r.get("明細") or {}
        if d:
            rows.append("得分來源：" + "、".join(f"{k}×{v}" for k, v in
                                             sorted(d.items(), key=lambda kv: -self.REWARD.get(kv[0], 0))))
        e = p.get("明細") or {}
        if e:
            rows.append("扣分來源：" + "、".join(f"{k}×{v}" for k, v in sorted(e.items(), key=lambda kv: -kv[1])))
        if self.mod("輪班計畫"):
            rows.append("→ **治好一個病人 +450～600**——這是全場最值錢的一件事，做成一次就抵得過整局的扣分；報到成功一次 +200、撐完一整輪班 +150。"
                        "其他所有事情加起來都比不上這三件——貼牆才扣 1 分，繞路十分鐘也不扣分，"
                        "但一次成功的報到就是 100 分。**把時間花在把這三件做成，不要花在別的地方。**")
        return NL.join(rows)

    # ---------------- 倒數獎金：拖越久拿越少
    TASK_TOP = 200.0         # 他自己訂的任務：滿分
    TASK_DROP = 10.0         # 超時之後每分鐘掉這麼多
    TASK_FLOOR = 50.0        # 預設保底；他自己訂分數時保底＝原分數的四分之一

    def task_clock(self):
        """他自己訂的任務值幾分：估的時間內滿分，超時每分鐘 −10，保底 50。"""
        g = getattr(self, "goal", None) or {}
        if not g.get("目標") or g.get("清單"):
            return None            # 使用者指定的清單走的是另一套（報到／治療的秒級倒數）
        t0 = float(g.get("開始於") or 0)
        if not t0:
            return None
        mins = (time.time() - t0) / 60.0
        est = float(g.get("預計幾分鐘") or 10)
        over = max(0.0, mins - est)
        return max(self.TASK_FLOOR, self.TASK_TOP - self.TASK_DROP * over), mins, est, over

    def task_clock_text(self):
        r = self.task_clock()
        if not r:
            return ""
        v, mins, est, over = r
        bar_n = int(round((v - self.TASK_FLOOR) / (self.TASK_TOP - self.TASK_FLOOR) * 20))
        bar = "█" * bar_n + "░" * (20 - bar_n)
        rows = [f"**你這個任務現在值 {v:.0f} 分**"
                f"（你自己估 {est:.0f} 分鐘，已經做了 {mins:.0f} 分鐘）",
                f"　{bar}　200 →（超時每分鐘 −10）→ 保底 50"]
        if over <= 0:
            rows.append(f"　還在你估的時間內，現在做完拿**滿分 200**。"
                        f"剩 {est - mins:.0f} 分鐘，之後才開始扣。")
        elif v > self.TASK_FLOOR:
            rows.append(f"　已經超時 {over:.0f} 分鐘，掉了 {self.TASK_TOP - v:.0f} 分。"
                        f"再拖 {(v - self.TASK_FLOOR) / self.TASK_DROP:.0f} 分鐘就會掉到保底。")
        else:
            rows.append("　已經掉到保底 50 分——**但保底就是保底，做完還有 50 分，放棄是 0 分**。"
                        "做完它，然後訂一個估得準一點的下一個任務。")
        return NL.join(rows)

    # 2026-09-13 使用者：治療成功要是**一次失敗的三到四倍**（他的例子：成功 +40、失敗 −10）。
    # 比的是「單次事件」，不是整局的失敗總量。治療途中最嚴重的一次失敗是死掉 −150，
    # 所以成功給 600（4 倍）、拖久保底 450（3 倍）。
    # 對一般的小扣分（空按 E −10、亂走 −20、待在原地 −25）更是遠遠蓋過。
    CLOCK_TOP = 200.0        # 一開始值這麼多（報到沿用舊值）
    CLOCK_DROP = 2.0         # 每秒掉這麼多
    CLOCK_FLOOR = 50.0       # 掉到這裡就不再掉
    CLOCK_FLOWS = ("報到", "治療")
    CLOCK_BY_FLOW = {        # 各流程自己的 (滿分, 每秒掉, 保底)
        # 保底也要維持在三倍：不管拖多久，治好一個病人都是死一次的 3~4 倍。
        "治療": (600.0, 1.5, 450.0),
    }

    def clock_par(self):
        """這個流程的倒數獎金參數（滿分、每秒掉、保底）。"""
        return self.CLOCK_BY_FLOW.get(self.flow,
                                      (self.CLOCK_TOP, self.CLOCK_DROP, self.CLOCK_FLOOR))

    def clock_reset(self, why=""):
        self._clock_at = time.time()
        self._clock_flow = self.flow
        top, drop, floor = self.clock_par()
        print(f"   ⏱ 倒數獎金重新計時：{top:.0f} 分起跳，每秒 −{drop:.0f}，"
              f"最低 {floor:.0f}{('　' + why) if why else ''}")

    def clock_value(self):
        """現在把報到或治療做成，可以拿幾分。"""
        if self.flow not in self.CLOCK_FLOWS:
            return None
        if getattr(self, "_clock_flow", None) != self.flow or not getattr(self, "_clock_at", 0):
            self.clock_reset(f"換成「{self.flow}」流程")
        gone = time.time() - self._clock_at
        top, drop, floor = self.clock_par()
        return max(floor, top - drop * gone)

    def clock_text(self):
        v = self.clock_value()
        if v is None:
            own = self.task_clock_text()
            if own:
                return own
            if not self.mod("輪班計畫"):
                return ("（你自己訂的任務才有倒數獎金：訂任務的時候自己估幾分鐘，"
                        "在時間內做完拿滿分，超時每分鐘掉 10 分、保底 50。）")
            return ("（現在不是報到也不是治療，倒數獎金沒有在跑。"
                    "一旦開始做報到或治療，獎金就從 200 分開始每秒掉 2 分。）")
        gone = time.time() - self._clock_at
        top, _drop, floor = self.clock_par()
        lost = top - v
        bar_n = int(round((v - floor) / max(1.0, top - floor) * 20))
        bar = "█" * bar_n + "░" * (20 - bar_n)
        rows = [f"**現在把「{self.flow}」做成，可以拿 {v:.0f} 分**"
                f"（已經過 {gone:.0f} 秒，掉了 {lost:.0f} 分）",
                f"　{bar}　200 →（每秒 −2）→ 50"]
        if v <= self.CLOCK_FLOOR:
            rows.append("　已經掉到最低的 50 分了——但**還是要做完**，做完 50 分，沒做完 0 分。")
        else:
            rows.append(f"　再拖 {(v - self.CLOCK_FLOOR) / self.CLOCK_DROP:.0f} 秒就會掉到最低的 50 分。"
                        "**動作快一點比想得完美更值錢。**")
        return NL.join(rows)

    # ---------------- 任務分數制（他自己訂分，程式只擋偏袒）
    SCORE_CAP = 500          # 正面任務的上限
    SCORE_CAP_NEG = 1000     # 負面項目的上限
    BASIC_CAP = 10           # 基礎動作最多值這麼多
    FAMILIAR_CAP = 30        # 已經做成過很多次的事最多值這麼多
    FAMILIAR_N = 20          # 做成過幾次算「已經很熟」
    KEEP = 0.25              # 保底：不管拖多久，至少留下原分數的四分之一
    BASIC_WORDS = ("走路", "走一步", "移動", "前進", "後退", "轉頭", "轉視角", "看", "觀察",
                   "按鍵", "跳", "站", "對準", "瞄準", "開門", "關門", "撿", "打開選單",
                   "walk", "move", "turn", "look", "jump", "press")
    FATAL_WORDS = ("死", "失敗", "被抓", "理智歸零", "歸零", "被殺", "淘汰", "出局", "遊戲結束",
                   "game over", "die", "death", "fail")

    def load_tasks(self):
        self.tasks = {}
        try:
            with open(os.path.join(self.dir, "任務分數表.json"), encoding="utf-8") as f:
                self.tasks = json.load(f) or {}
        except Exception:  # noqa: BLE001
            self.tasks = {}
        pos = sum(1 for v in self.tasks.values() if v.get("正負") == "正")
        print(f"[任務分數] 他自己訂過 {len(self.tasks)} 項（正面 {pos}、負面 {len(self.tasks) - pos}）")
        # 他提案的分數表：只記錄、不生效，開機讀回來才不會每次重開又從零列一遍
        self.proposals = []
        try:
            with open(os.path.join(self.dir, "他提案的分數表.json"), encoding="utf-8") as f:
                self.proposals = json.load(f) or []
        except Exception:  # noqa: BLE001
            self.proposals = []
        if self.proposals:
            print(f"[提案] 他提過 {len(self.proposals)} 條分數提案（草案，不影響計分）")

    def save_tasks(self):
        try:
            with open(os.path.join(self.dir, "任務分數表.json"), "w", encoding="utf-8") as f:
                json.dump(self.tasks, f, ensure_ascii=False, indent=1)
        except Exception:  # noqa: BLE001
            pass

    def done_times(self, name):
        """這件事他已經做成過幾次（拿來判斷是不是『已經很熟的基本功』）。"""
        n = 0
        for w in (getattr(self, "wins", None) or []):
            if isinstance(w, dict) and str(name) in json.dumps(w, ensure_ascii=False):
                n += 1
        for k, v in (getattr(self, "spots", {}) or {}).items():
            if str(name) in str(k):
                n += int(v.get("成功次數", 0) or 0)
        return n

    def max_positive(self):
        vals = [int(v.get("分數", 0) or 0) for v in (getattr(self, "tasks", {}) or {}).values()
                if v.get("正負") == "正"]
        return max(vals) if vals else 0

    def audit_score(self, name, score, sign):
        """擋偏袒。回傳 (准不准, 理由)。程式不決定分數高低，只擋明顯不合理的評分。"""
        name = str(name).strip()
        low = name.lower()
        if score <= 0:
            return False, "分數要填正整數（是正面還是負面用「正負」那欄決定，不要用負號）"
        if sign == "正":
            if any(w in low for w in self.BASIC_WORDS) and score > self.BASIC_CAP:
                return False, (f"「{name}」是走路、轉頭這一類**每個玩家一開始就會做的基礎動作**，"
                               f"最多只能給 {self.BASIC_CAP} 分。你給了 {score} 分，"
                               f"這叫給自己灌水。基礎動作是手段，不是成果。")
            n = self.done_times(name)
            if n >= self.FAMILIAR_N and score > self.FAMILIAR_CAP:
                return False, (f"「{name}」你已經做成過 {n} 次了，對你來說早就不難，"
                               f"最多給 {self.FAMILIAR_CAP} 分。你給 {score} 分是在用熟練的事刷分。"
                               f"分數要留給**你還做不到的事**。")
            if score > self.SCORE_CAP:
                return False, f"正面任務上限 {self.SCORE_CAP} 分，你給了 {score} 分。"
        else:
            if any(w in low for w in self.FATAL_WORDS):
                need = max(self.max_positive(), 100)
                if score < need:
                    return False, (f"「{name}」是**會把你的進度整個清掉**的事。"
                                   f"你目前最高的正面任務值 {self.max_positive()} 分，"
                                   f"所以它至少要扣 {need} 分。你只想扣 {score} 分，"
                                   f"那等於死一次還划得來——**不准給自己打折**。")
            if score > self.SCORE_CAP_NEG:
                return False, f"負面項目上限 {self.SCORE_CAP_NEG} 分，你給了 {score} 分。"
        return True, ""

    def propose_score(self, a):
        """他自己提案的加扣分表——**只記錄，不生效**。

        2026-09-12 使用者要求：想看他針對自己現在要做的任務，會替哪些事加分、
        替哪些事扣分、各給多少。這跟 `訂分數` 不一樣：訂分數會真的進計分板，
        提案不會——不審偏袒、不駁回、不扣分，原原本本存下來給使用者看。
        """
        name = str(a.get("名") or a.get("name") or "").strip()[:30]
        if not name:
            print("   提案分數要有「名」")
            return
        sign = "負" if str(a.get("正負") or a.get("sign") or "正").startswith("負") else "正"
        try:
            score = int(float(a.get("分數") or a.get("score") or 0))
        except (TypeError, ValueError):
            score = 0
        row = {"項目": name, "正負": sign, "分數": score,
               "為什麼": str(a.get("為什麼") or a.get("why") or "")[:80],
               "怎麼檢查": str(a.get("怎麼檢查") or a.get("檢查") or "")[:80],
               "任務": str(a.get("任務") or self.flow or "")[:20],
               "時間": datetime.now().strftime("%m/%d %H:%M:%S"), "輪": self.stat.get("輪數", 0)}
        self.proposals = getattr(self, "proposals", [])
        self.proposals.append(row)
        try:
            with open(os.path.join(self.dir, "他提案的分數表.json"), "w", encoding="utf-8") as f:
                json.dump(self.proposals, f, ensure_ascii=False, indent=1)
            with open(os.path.join(self.dir, "他提案的分數表.md"), "a", encoding="utf-8") as f:
                f.write(f"- {row['時間']}　[{row['任務']}] **{name}**　{sign}{score} 分"
                        + (f"　理由：{row['為什麼']}" if row["為什麼"] else "")
                        + (f"　怎麼檢查：{row['怎麼檢查']}" if row["怎麼檢查"] else "") + NL)
        except Exception:  # noqa: BLE001
            pass
        print(f"   📝 他提案（不生效）：{name} {sign}{score} 分"
              + (f"　{row['為什麼'][:30]}" if row["為什麼"] else ""))
        self.propose_hint = (f"📝 收到你的提案「{name} {sign}{score} 分」——"
                             f"**這只是記下來給使用者看，不會影響你現在的分數**。"
                             f"繼續把表列完，列完就回去做事。" + NL)
        self.log({"時間": row["時間"], "模式": "提案分數", "名": name,
                  "分數": score, "正負": sign})

    def proposal_text(self):
        ps = getattr(self, "proposals", None) or []
        if not ps:
            return "（還沒提案過）"
        rows = [f"你已經提了 {len(ps)} 條（只是草案，不影響你的分數）："]
        for r in ps[-8:]:
            rows.append(f"　{r['正負']}{r['分數']}　{r['項目']}")
        return NL.join(rows)

    def set_task(self, a):
        name = str(a.get("名") or a.get("name") or "").strip()[:30]
        sign = "負" if str(a.get("正負") or a.get("sign") or "正").startswith("負") else "正"
        try:
            score = int(float(a.get("分數") or a.get("score") or 0))
        except (TypeError, ValueError):
            score = 0
        why = str(a.get("為什麼") or a.get("why") or "")[:60]
        est = a.get("預計幾分鐘") or a.get("est")
        if not name:
            print("   訂任務要有「名」")
            return
        ok, reason = self.audit_score(name, score, sign)
        if not ok:
            print(f"   ⚖ 駁回「{name}」{sign} {score} 分：{reason}")
            self.task_hint = (f"⚖ **你給「{name}」的 {score} 分被駁回了。**{reason}"
                              f" 重新評一個合理的分數再送一次。" + NL)
            self.punish("給自己偏袒的評分", f"「{name}」{score} 分——{reason[:40]}")
            return
        self.tasks[name] = {"分數": score, "正負": sign, "為什麼": why,
                            "預計幾分鐘": int(float(est)) if est else None,
                            "保底": int(round(score * self.KEEP)),
                            "訂於": datetime.now().strftime("%m/%d %H:%M"),
                            "達成次數": (self.tasks.get(name, {}) or {}).get("達成次數", 0)}
        self.save_tasks()
        try:
            with open(os.path.join(self.dir, "任務分數表.md"), "a", encoding="utf-8") as f:
                f.write(f"- {self.tasks[name]['訂於']} **{name}**　{sign} {score} 分"
                        f"（保底 {self.tasks[name]['保底']}）{('　' + why) if why else ''}" + NL)
        except Exception:  # noqa: BLE001
            pass
        print(f"   ⚖✅ 他訂了「{name}」：{sign} {score} 分，保底 {self.tasks[name]['保底']}")
        self.log({"時間": datetime.now().strftime("%H:%M:%S"), "模式": "訂任務分數",
                  "名": name, "分數": score, "正負": sign})

    def task_score_text(self):
        ts = getattr(self, "tasks", None) or {}
        if not ts:
            return ("（你還沒訂過任何任務的分數。**分數由你自己訂**：一件事對「把這個遊戲玩成」"
                    "有多大貢獻，你自己評。用 " + ACT_TASK + " 訂。）")
        pos = [(k, v) for k, v in ts.items() if v.get("正負") == "正"]
        neg = [(k, v) for k, v in ts.items() if v.get("正負") != "正"]
        rows = []
        if pos:
            rows.append("**要做到的（做成有分，拖越久越少，保底是原分的四分之一）**")
            for k, v in sorted(pos, key=lambda kv: -kv[1].get("分數", 0)):
                rows.append(f"　+{v['分數']}　{k}（保底 {v['保底']}"
                            + (f"、你估 {v['預計幾分鐘']} 分鐘" if v.get("預計幾分鐘") else "")
                            + f"、已做成 {v.get('達成次數', 0)} 次）")
        if neg:
            rows.append("**不能發生的（發生就扣，分數也是你自己評的嚴重程度）**")
            for k, v in sorted(neg, key=lambda kv: -kv[1].get("分數", 0)):
                rows.append(f"　−{v['分數']}　{k}")
        rows.append("→ 分數是你自己訂的，所以**做不到不能怪規則**。"
                    "訂的時候誠實一點：基礎動作不值錢，會清掉進度的事要扣得夠重。")
        return NL.join(rows)

    FROZEN_PEN_AFTER = 60.0     # 這麼久沒有任何進展就罰一次，之後每隔這麼久再罰

    def score_frozen(self):
        """一直待在原地＝扣分（使用者 09/12 指示）。

        原本只有「原地空轉」罰單輪什麼都沒做；但他真正的浪費是**連續好幾分鐘**
        在同一個地方轉頭、按沒反應的 E、走半步又退回來——每一輪看起來都有動作，
        所以一輪一輪看都罰不到。這裡看的是跨輪的事實：沒有任何一件事成功，
        而且人根本沒有離開原地。
        """
        try:
            fz = self.frozen_secs()
            if fz < self.FROZEN_PEN_AFTER:
                self._frozen_mark = None
                return
            now, pos = time.time(), list(getattr(self, "pos", [0.0, 0.0]))
            mark = getattr(self, "_frozen_mark", None)
            if not mark:
                self._frozen_mark = {"時間": now, "位置": pos}
                return
            if now - mark["時間"] < self.FROZEN_PEN_AFTER:
                return
            moved = ((pos[0] - mark["位置"][0]) ** 2 + (pos[1] - mark["位置"][1]) ** 2) ** 0.5
            self._frozen_mark = {"時間": now, "位置": pos}
            if moved < 2.0:
                self.punish("一直待在原地",
                            f"這一分鐘沒有任何一件事成功，而且"
                            f"只挪了 {moved:.1f} 公尺——等於待在原地。"
                            f"站著轉頭、按沒反應的 E、走半步又退回來，都不算在做事。"
                            f"**換一個看得到的目標，一次走過去。**")
        except Exception:  # noqa: BLE001
            pass

    def score_behaviour(self, actions):
        """程式不再擋他，但照樣看著：原本會被擋下來的行為，現在只記 1 分扣分。

        使用者 09/11 的指示——限制全部解除（花錢除外），讓他自己習慣走路的方式；
        做錯不會被攔，只會留在計分板上，由他自己決定要不要改。
        """
        acts = [a for a in (actions or []) if isinstance(a, dict)]
        for a in acts:
            if a.get("t") == "look":
                try:
                    if abs(float(a.get("dx", 0) or 0)) > self.MAX_DX:
                        self.punish("大幅度轉頭", "一次甩太多會把要找的東西掃過去，也會弄亂方位。")
                        break
                except (TypeError, ValueError):
                    pass
        # 2026-09-24 使用者：上下來回甩視角要扣分，讓他自己避免。
        for a in acts:
            if a.get("t") != "look":
                continue
            try:
                dy = float(a.get("dy", 0) or 0)
            except (TypeError, ValueError):
                continue
            if abs(dy) < 150:
                continue
            last = getattr(self, "_last_big_dy", None)
            now = time.time()
            self._last_big_dy = (dy, now)
            if last and now - last[1] < 60 and (last[0] > 0) != (dy > 0):
                self.punish("上下來回甩視角",
                            f"{int(now - last[1])} 秒前你才上下大轉 dy {last[0]:+.0f}，"
                            f"這一輪又往反方向大轉 dy {dy:+.0f}——來回甩，鏡頭一直停不下來。"
                            f"大轉一次之後，先看畫面和水平儀確認轉到哪裡了，再決定下一步。")
                break
        # 使用者 09/12：抬頭／低頭要用**畫面看出來的**判斷，不要用累積的轉動角度
        # （角度是推算的、會漂；眼睛框到的東西在畫面哪裡才是事實）。
        try:
            desc, need = self.pitch_from_eyes()
            self._pitch_before = need          # 修視角加分要比對修之前是不是歪的
            if getattr(self, "_pitch_src", "") == "dy":
                # 2026-09-14 晚：「這一分鐘淨往下轉了 600 ＝ 八成低頭」是**猜**的，不是看到的。
                # 他剛從天花板一口氣壓回水平，下一輪這裡就會因為那 600 罰他「頂到地板」——
                # 猜的方向只拿來給建議，扣分要有反射或 YOLO 的實據。
                need = None
            # 2026-09-14 使用者定案：扣分看**算出來的度數**，畫面只寫在理由裡當旁註
            pd = getattr(self, "pitch_deg", 0.0)
            aux = f"；畫面輔助：{str(desc)[:24]}" if desc else ""
            if pd >= 55:
                self.punish("鏡頭頂到天花板",
                            f"水平儀：抬頭 {pd:.0f} 度{aux}。上面沒有任何東西，"
                            f"櫃檯、病人、按鈕、E 的提示全在水平視線上。")
            elif pd <= -55 and not self.at_desk_surface():
                self.punish("鏡頭頂到地板", f"水平儀：低頭 {abs(pd):.0f} 度{aux}。低頭什麼都做不成。")
        except Exception:  # noqa: BLE001
            pass
        # 空按 E 不在這裡罰：hold_interact 用「真的按了沒反應」判，比看畫面準
        n_esc = sum(1 for a in acts if a.get("t") == "key" and str(a.get("k", "")).lower() == "esc")
        if n_esc >= 2:
            self.punish("esc連按", "esc 是開關，連按等於自己把選單開開關關。")
        # 這一輪等於什麼都沒做＝原地空轉
        # 2026-09-22 使用者：**點字要算動作**。之前這張清單漏了點字，他用 OCR 框中心點按鈕
        # （最準的點法）卻每次被記成「沒動作」再罰 −100 原地空轉——09/18～09/22 五天白扣 209,700 分，
        # 也讓他的檢討一直誤以為自己在空轉。
        real = [a for a in acts if a.get("t") in ("key", "hold", "click", "look", "aim", "走到",
                                                  "點字", "點文字")]
        if getattr(self, "_model_silent", False):
            self._model_silent = False          # 模型沒講完的輪次不罰（見 cycle 裡的說明）
            return
        if getattr(self, "_routed", ""):
            return                              # 2026-09-27：程式照地圖帶他走了，這輪不是空轉
        # 2026-09-27 使用者更正：「原地空轉不是指不做移動，是指什麼都沒有做——沒有去完成任務才屬於原地空轉」。
        # 所以：他這輪有要任何動作／工具（點字沒找到、寫步驟、記知識、基礎確認、換快思…都算在做事），
        # 或是有理由地等（站在框框等人數、等傳送、等載入），都不算空轉。什麼都沒要、也不是在等，才算。
        asked = list(getattr(self, "_asked", []) or [])
        think = getattr(self, "_last_think", "") or ""
        waiting = any(t in ("wait", "等") for t in asked) or any(
            w in think for w in ("等", "傳送", "載入", "配對", "人數", "倒數", "排隊"))
        if not asked and not waiting:
            self.punish("原地空轉", "這一輪你什麼都沒做，也不是在等任何事——沒有往任務前進。")
        # 2026-09-29 使用者：用工具算做事、要鼓勵；但**想了卻沒做**要罰（−20）。
        # 例：想法寫「直接走向右側框框」，卻只送「這步做完／換快思」、沒送任何會動到遊戲的動作。
        acting_tools = ("點字", "點文字", "走到記過的", "走到", "水平歸零", "wait", "等")
        acted_by_tool = any(t in acting_tools for t in asked)
        if asked and not real and not waiting and not acted_by_tool and not getattr(self, "_reflex_check", False):
            intent = re.search(r"走向|走過去|走到|往前走|前進|按\s*[EeWwAaSsDd](?![A-Za-z])|長按|按住|點擊|去點|點一下|"
                               r"站進|站上|站到|跳過去|靠近|轉向|轉過去", think)
            done = re.search(r"做完|已經到|已到|已完成|完成了", think)
            if intent and not done:
                self.punish("想了卻沒做",
                            f"你想的是「{think[:30]}」，這一輪卻只送了 {'、'.join(asked)[:30]}，"
                            f"沒有送任何會真的去做的動作。想做什麼，這一輪就把那個動作送出去。")
        # UI 蓋著還不處理＝卡在選單
        try:
            if self.ui_open_hint():
                self._ui_rounds = getattr(self, "_ui_rounds", 0) + 1
                if self._ui_rounds >= 2:
                    self._ui_rounds = 0
                    self.punish("卡在選單", "畫面上有東西蓋著，而且你連續好幾輪都沒把它關掉。"
                                            "UI 開著的時候按什麼走什麼都不會有反應——先關掉再做事。")
            else:
                self._ui_rounds = 0
        except Exception:  # noqa: BLE001
            pass
        sig = json.dumps(acts, ensure_ascii=False, sort_keys=True)
        if sig and sig == getattr(self, "_prev_sig", None):
            self._same_n = getattr(self, "_same_n", 0) + 1
            if self._same_n >= 1:
                self._same_n = 0
                self.punish("重複同一個動作", "同一個動作做了四次都沒有效果，該換做法了。")
        else:
            self._same_n = 0
        self._prev_sig = sig

    # ---------------- 扣分之後的自我學習
    REVIEW_SMALL_AFTER = 1     # 使用者 09/12：**每一次**被扣分都要重新想一遍問題在哪
    # 2026-09-17 使用者：**事後檢討可以花長時間**——快的是玩，不是想清楚。
    REVIEW_BUDGET = 180.0

    LEARN_ASK = """【檢討時間】你剛才因為「{kind}」被扣分了{times}。現在**不趕時間**，好好想。

被扣分不是重點，重點是**同樣的事不要再發生**。

⚠ **原因只能從下面那份「出事前三十秒實際做的事」裡面找**。
畫面上剛好有什麼東西**不等於**它是原因——同時發生不是因果。
如果那份紀錄裡看不出原因，就老實寫「看不出來」，然後訂一條「下次出事前要先記下什麼」的規則。

回答三件事：
1. 為什麼會發生？（指出是紀錄裡的哪一個動作造成的，不要講「我會注意」這種空話）
2. 你當時可以怎麼做才不會被扣？（具體到動作）
3. 訂一條**你之後每一輪都照著做**的規則（一句話，要具體到可以直接檢查自己有沒有做到）

規則的例子：
　好：「按 w 之前一定先在同一個動作裡填 g，寫出我要去的東西」
　壞：「我會更小心」「下次注意目的地」——這種規則沒辦法檢查，等於沒訂。

⚖ **訂之前先對【指令等級表】**（系統提示最前面那一份）：你訂的規則是第 3 級，
跟第 0～2 級任何一條抵觸就會被退件（例如高階說「卡住可以離開重進」，你就不能訂「看到離開視窗只准 esc」）。
先問自己「照這條做，會不會擋住上面某一條？」——會就換一條不打架的。

只輸出 JSON：{{"為什麼":"(<=50字)","當時該怎麼做":"(<=50字)","規則":"(<=40字，具體可檢查，不抵觸等級表)"}}"""

    WIN_ASK = """【回推時間】你剛才成功了：**{kind}**（+{n} 分）。現在**不趕時間**。

成功一次不算會了——**會了是「下次還做得出來」**。所以現在要做的是：
從下面那份「成功前四十秒實際做了什麼」裡面，**倒推回去**：到底是哪幾步讓它成功的。

⚠ 只能從那份紀錄裡找。你當時腦中在想什麼不算證據，**做了什麼才算**。
分不出哪一步是關鍵，就老實寫「分不出來」，然後寫下「下次要多記什麼才分得出來」。

回答四件事：
1. 你實際做了哪幾步？（照順序，用紀錄裡的動作講）
2. 哪一步是**關鍵**？（少了它就不會成功的那一步）
3. 下次要怎麼**重現**？（一句一步，具體到可以直接照做：先轉幾度、走幾秒、對準什麼、按幾秒）
4. 這次差點失敗在哪？（有的話）

只輸出 JSON：{{"做了什麼":"(<=60字)","關鍵一步":"(<=30字)","怎麼重現":"(<=80字，一步一句)","差點失敗在":"(<=30字)"}}"""

    def load_wins_how(self):
        self.wins_how = {}
        try:
            with open(os.path.join(self.dir, "成功做法.json"), encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                self.wins_how = d
        except (OSError, json.JSONDecodeError, TypeError):
            pass
        if self.wins_how:
            print(f"[成功回推] 他自己回推過 {len(self.wins_how)} 種成功做法")

    def save_wins_how(self):
        try:
            with open(os.path.join(self.dir, "成功做法.json"), "w", encoding="utf-8") as f:
                json.dump(self.wins_how, f, ensure_ascii=False, indent=1)
        except Exception:  # noqa: BLE001
            pass

    def maybe_success_review(self, kind, n):
        """成功之後讓他自己回推。小分（拍地標那種）不停，免得整輪都在想。"""
        if n < 10:
            return
        last = (getattr(self, "_win_review_at", None) or {}).get(kind, 0)
        if time.time() - last < 120:
            return
        self._win_review_at = getattr(self, "_win_review_at", {})
        self._win_review_at[kind] = time.time()
        if getattr(self, "think_mode", 快思) != 快思:
            self.defer_review(kind, "成功")
            return
        self.learn_from_win(kind, n)

    def learn_from_win(self, kind, n):
        if not self._in_review_thread():
            return self.review_async("成功回推", self.learn_from_win, kind, n)
        print(f"   🏆📖 因為「{kind}」成功了，停下來自己回推是怎麼做到的（不趕時間）…")
        old = (getattr(self, "wins_how", None) or {}).get(kind) or {}
        extra = (NL + f"你上次回推過同一件事，當時寫的重現步驟是：「{old.get('怎麼重現', '')}」。"
                      f"這次照那幾步做的嗎？有沒有哪一步其實不需要、或漏了什麼？") if old else ""
        try:
            uri, _ = jpeg_data_uri(self.draw_eyes(draw_grid(self.grab_still(1024))), 70)
            content = [{"type": "image_url", "image_url": {"url": uri}}]
            for ago, ruri in self.replay_shots(40.0, end=(getattr(self, "_rw_at", {}) or {}).get(kind)):
                content.append({"type": "text", "text": f"【成功前 {ago:.0f} 秒】"})
                content.append({"type": "image_url", "image_url": {"url": ruri}})
            # 2026-09-17 使用者：加分的回推也接上短期記憶。回推要的是「哪幾步讓它成功」，
            # 回放只看得到那幾秒的畫面，短期記憶才有**每一輪他當時想做什麼、做了什麼**。
            content += self.short_mem_content()
            content.append(
                {"type": "text", "text": "你最近做過的事：" + NL + NL.join(self.history[-10:])
                                          + NL + NL + "【成功前四十秒你實際做了什麼】（程式一筆一筆記的，不會漏）" + NL
                                          + self.trace_text(40.0, end=(getattr(self, "_rw_at", {}) or {}).get(kind),
                                                            label="成功")
                                          + (NL + NL + self.key_moves_text(2)
                                             if self.key_moves_text(2) else "")
                                          + NL + NL + self.WIN_ASK.format(kind=kind, n=n)
                                          + extra})
            raw, _, _, _ = stream_json(
                self.endpoint, self.model, self.sys_prompt(), content,
                1200, time.time() + self.REVIEW_BUDGET + 20, 0.35, effort=慢想)
        except Exception as e:  # noqa: BLE001
            print(f"   回推請求失敗：{e}")
            return
        d = parse_json_loose(raw) or {}
        how = str(d.get("怎麼重現", "")).strip()[:120]
        if not how:
            print("   回推沒解析出重現步驟，這次跳過")
            return
        if not hasattr(self, "wins_how"):
            self.load_wins_how()
        self.wins_how[kind] = {
            "區": getattr(self, "cur_zone", "") or "（沒標區）",
            "做了什麼": str(d.get("做了什麼", ""))[:80],
            "關鍵一步": str(d.get("關鍵一步", ""))[:40],
            "怎麼重現": how,
            "差點失敗在": str(d.get("差點失敗在", ""))[:40],
            "回推於": datetime.now().strftime("%m/%d %H:%M"),
            "成功幾次": int((old.get("成功幾次") or 0)) + 1,
        }
        self.save_wins_how()
        try:
            with open(os.path.join(self.dir, "成功檢討.md"), "a", encoding="utf-8") as f:
                w = self.wins_how[kind]
                f.write(f"## {w['回推於']}　{kind} +{n}（第 {w['成功幾次']} 次回推）" + NL
                        + f"- 做了什麼：{w['做了什麼']}" + NL
                        + f"- **關鍵一步：{w['關鍵一步']}**" + NL
                        + f"- 怎麼重現：{how}" + NL
                        + (f"- 差點失敗在：{w['差點失敗在']}" + NL if w["差點失敗在"] else "")
                        + "- 成功前四十秒實際做的事（拿來核對上面的歸因對不對）：" + NL
                        + self.trace_text(40.0, end=(getattr(self, "_rw_at", {}) or {}).get(kind), label="成功")
                        + NL + NL)
        except Exception:  # noqa: BLE001
            pass
        print(f"   🏆📖✅ 他自己回推出來的做法：{how[:60]}")
        self.lesson_hint = (getattr(self, "lesson_hint", "") or "") + (
            f"🏆 你剛才回推過「{kind}」是怎麼成功的，自己寫的重現步驟是：**{how}**"
            f"　下次遇到同樣的事就照這幾步做，不要重新亂試。" + NL)
        self.log({"時間": datetime.now().strftime("%H:%M:%S"), "模式": "成功回推",
                  "項目": kind, "怎麼重現": how})

    def wins_how_text(self):
        wh = getattr(self, "wins_how", None) or {}
        if not wh:
            return ("（還沒有。你每成功一次比較大的事，程式就會讓你停下來，"
                    "從「成功前四十秒實際做了什麼」自己倒推是哪幾步讓它成功的。）")
        rows = []
        for k, v in list(wh.items())[-4:]:
            rows.append(f"**{k}**（回推 {v.get('成功幾次', 1)} 次，{v.get('回推於', '')}"
                        + (f"，在「{v['區']}」" if v.get("區") else "") + "）" + NL
                        + f"　關鍵一步：{v.get('關鍵一步', '?')}" + NL
                        + f"　怎麼重現：{v.get('怎麼重現', '')}"
                        + (NL + f"　差點失敗在：{v['差點失敗在']}" if v.get("差點失敗在") else ""))
        # 高分的事（30 分以上）有沒有做成過、上次怎麼做到的——他最該記住的就是這幾件
        big = {k: n for k, n in self.REWARD.items() if n >= 30}
        det = ((getattr(self, "reward", {}) or {}).get("明細") or {})
        lines = []
        for k, n in sorted(big.items(), key=lambda kv: -kv[1]):
            got = int(det.get(k, 0))
            if got and k in wh:
                lines.append(f"　✅ **{k}（{n} 分）**做成 {got} 次｜你自己記的做法：{wh[k].get('怎麼重現', '')[:60]}")
            elif got:
                lines.append(f"　✅ **{k}（{n} 分）**做成 {got} 次，但**還沒回推過做法**——下次做成馬上回推。")
            else:
                lines.append(f"　⬜ {k}（{n} 分）**還沒做成過**")
        if lines:
            rows.append("【最值錢的那幾件事，你做成過沒有】" + NL + NL.join(lines))
        return NL.join(rows) + NL + "→ 這是**你自己**從紀錄倒推出來的，不是別人教的。照它做，做不出來就再回推一次。"

    def load_lessons(self):
        self.lessons = {}
        try:
            with open(os.path.join(self.dir, "扣分檢討.json"), encoding="utf-8") as f:
                self.lessons = json.load(f) or {}
        except Exception:  # noqa: BLE001
            self.lessons = {}
        self.pen_since = {}
        if self.lessons:
            print(f"[改善] 他自己訂過 {len(self.lessons)} 條改善規則："
                  + "、".join(self.lessons.keys()))

    def save_lessons(self):
        try:
            with open(os.path.join(self.dir, "扣分檢討.json"), "w", encoding="utf-8") as f:
                json.dump(self.lessons, f, ensure_ascii=False, indent=1)
        except Exception:  # noqa: BLE001
            pass

    REVIEW_AGAIN_AFTER = 10     # 使用者 09/13：每再犯超過十次就要重新檢討一次「為什麼會犯」

    def defer_review(self, kind, what):
        """快節奏時不要當場停下來檢討——先排隊，等安全了再補（使用者 2026-09-16）。

        他自己把思考強度調成反射，代表現在是「慢一秒就死」的狀況；
        這時候停 45 秒寫檢討，等於站在原地送死。所以改成**事後檢討**：
        記下來，等他調回快思（或回到大廳／局結束）再一次補完。
        """
        q = getattr(self, "pending_reviews", None)
        if q is None:
            q = self.pending_reviews = []
        if not any(x[0] == kind and x[1] == what for x in q):
            q.append((kind, what))
            print(f"   ⏸ 現在是反射模式（你自己調的），「{kind}」的檢討先排隊，等安全了再補"
                  f"（待補 {len(q)} 件）")

    def do_pending_reviews(self):
        """安全了就把欠的檢討補掉，一次補一件（免得整輪都在想）。"""
        q = getattr(self, "pending_reviews", None) or []
        if not q or getattr(self, "think_mode", 快思) != 快思:
            return
        kind, what = q.pop(0)
        print(f"   📖 事後檢討（當時在趕，現在補）：{kind}　還欠 {len(q)} 件")
        try:
            if what == "扣分":
                self.learn_from_penalty(kind, 1)
            else:
                self.learn_from_win(kind, self.REWARD.get(kind, 10))
        except Exception as e:  # noqa: BLE001
            print(f"   事後檢討失敗：{e}")

    def maybe_review(self, kind, n):
        """扣分之後要不要停下來檢討。

        2026-09-13 使用者：「真的換了再檢討」。
        原本每一次扣分都停 45 秒，結果他為同一件事重訂了一千多次規則、
        內容幾乎一模一樣（亂走 427 次、空按 E 261 次），等於一整天在重寫同一句話。
        現在：**已經訂過規則的項目不再停下來**，只把他自己訂的那條念給他聽；
        只有又犯 25 次（代表那條規則真的沒用）才重新檢討一次，而且會要求訂不一樣的。
        """
        self.pen_since[kind] = self.pen_since.get(kind, 0) + 1
        old_rule = (self.lessons.get(kind) or {}).get("規則", "")
        if old_rule:
            if self.pen_since[kind] < self.REVIEW_AGAIN_AFTER:
                self.lesson_hint = (f"⛔ 又一次「{kind}」。**你自己訂過的規則是：{old_rule}**"
                                    f"　這一輪就照它做——訂了不做等於沒訂。" + NL)
                return
            print(f"   📖 「{kind}」訂了規則之後又犯 {self.pen_since[kind]} 次 → 重新檢討")
        need = 1 if n >= 50 else self.REVIEW_SMALL_AFTER
        if self.pen_since[kind] < need:
            return
        if getattr(self, "think_mode", 快思) != 快思:
            self.defer_review(kind, "扣分")
            self.pen_since[kind] = 0
            return
        last = (getattr(self, "_review_at", None) or {}).get(kind, 0)
        if time.time() - last < 90:
            self.pen_since[kind] = 0
            return                      # 同一種十分鐘內只停下來檢討一次，不然整輪都在想
        self._review_at = getattr(self, "_review_at", {})
        self._review_at[kind] = time.time()
        cnt = self.pen_since[kind]
        self.pen_since[kind] = 0
        try:
            self.learn_from_penalty(kind, cnt)
        except Exception as e:  # noqa: BLE001
            print(f"   檢討失敗：{e}")

    def learn_from_penalty(self, kind, cnt):
        if not self._in_review_thread():
            return self.review_async("扣分檢討", self.learn_from_penalty, kind, cnt)
        """讓他自己檢討這一種扣分，並訂一條具體、可檢查的改善規則。"""
        times = f"（這是累積第 {cnt} 次）" if cnt > 1 else ""
        print(f"   📖 因為「{kind}」停下來檢討（不趕時間）…")
        old = (self.lessons.get(kind) or {}).get("規則", "")
        extra = (NL + f"你上次為了這件事訂過規則：「{old}」，但又犯了。"
                      f"這代表那條規則沒用或你沒照做——這次要訂一條**不一樣的、更具體的**。") if old else ""
        try:
            uri, _ = jpeg_data_uri(self.draw_eyes(draw_grid(self.grab_still(1024))), 70)
            content = [{"type": "image_url", "image_url": {"url": uri}}]
            # 通用檢討方式（使用者 09/16）：出事當下來不及看，事後把那幾十秒的畫面攤出來看。
            for ago, ruri in self.replay_shots(40.0, end=(getattr(self, "_pen_at", {}) or {}).get(kind)):
                content.append({"type": "text", "text": f"【出事前 {ago:.0f} 秒】"})
                content.append({"type": "image_url", "image_url": {"url": ruri}})
            # 2026-09-17 使用者：扣分檢討也接上短期記憶（回放是秒、短期記憶是輪，
            # 後者才看得出「這幾輪我一直在做同一件事」）。
            content += self.short_mem_content()
            content.append(
                {"type": "text", "text": "你最近做過的事：" + NL + NL.join(self.history[-10:])
                                          + NL + NL + f"【你最近幾次犯「{kind}」的時候各在做什麼】" + NL
                                          + self.mistakes_digest(kind)
                                          + NL + NL + "【被扣分前三十秒你實際做了什麼】（程式一筆一筆記的，不會漏）" + NL
                                          + self.trace_text(30.0, end=(getattr(self, "_pen_at", {}) or {}).get(kind))
                                          + NL + NL + self.LEARN_ASK.format(kind=kind, times=times)
                                          + extra})
            raw, _, _, _ = stream_json(
                self.endpoint, self.model, self.sys_prompt(), content,
                1200, time.time() + self.REVIEW_BUDGET + 20, 0.35, effort=慢想)
        except Exception as e:  # noqa: BLE001
            print(f"   檢討請求失敗：{e}")
            return
        d = parse_json_loose(raw) or {}
        rule = str(d.get("規則", "")).strip()[:50]
        if not rule:
            print("   檢討沒解析出規則，這次跳過")
            return
        prev = self.lessons.get(kind) or {}
        # 「真的換了」才算數：跟上一條實質相同的規則不收（他最愛換個說法再交一次）
        if prev.get("規則"):
            import difflib
            strip = lambda x: "".join(ch for ch in str(x) if ch.isalnum())
            same = difflib.SequenceMatcher(None, strip(prev["規則"]), strip(rule)).ratio()
            if same >= 0.40:       # 中文逐字比，換句話說大約落在 0.5~0.7；做法真的不同的只有 0~0.2
                prev["重複交件"] = int(prev.get("重複交件", 0)) + 1
                self.lessons[kind] = prev
                self.save_lessons()
                print(f"   📖⚠ 這條跟上次那條幾乎一樣（相似度 {same:.0%}），不收：{rule}")
                self.lesson_hint = (f"⚠ 你為「{kind}」交的新規則跟上次那條幾乎一樣"
                                    f"（「{prev['規則']}」）。**同一條規則重訂沒有意義**——"
                                    f"它已經證明沒用了。要嘛照它做，要嘛想一條做法完全不同的。" + NL)
                return
        # 2026-09-23 使用者定的等級表：他訂的規則（第 3 級）抵觸第 0～2 級就退件
        bad = self.rule_audit([("扣分檢討·" + kind, kind, rule)], "新規則")
        if bad:
            hi, reason = bad[0]
            self._audit_log([f"- {datetime.now():%m/%d %H:%M}（新規則退件）「{rule}」（扣分檢討·{kind}）——抵觸「{hi}」：{reason}"])
            print(f"   ⚖ 新規則抵觸高階規則，退件：{rule}　← {hi}（{reason}）")
            self.lesson_hint = (f"⚖ 你為「{kind}」訂的規則「{rule}」跟【指令等級表】的高階規則抵觸"
                                f"（{hi}：{reason}），**不收**。高階規則優先，重訂一條不打架的。" + NL)
            return
        self.lessons[kind] = {
            "規則": rule,
            "為什麼": str(d.get("為什麼", ""))[:60],
            "當時該怎麼做": str(d.get("當時該怎麼做", ""))[:60],
            "訂於": datetime.now().strftime("%m/%d %H:%M"),
            "訂時已犯": int((self.penalty.get("明細") or {}).get(kind, 0)),
            "改過幾次": int(prev.get("改過幾次", 0)) + 1,
        }
        self.save_lessons()
        try:
            with open(os.path.join(self.dir, "扣分檢討.md"), "a", encoding="utf-8") as f:
                f.write(f"## {self.lessons[kind]['訂於']}　{kind}（第 {self.lessons[kind]['改過幾次']} 次檢討）" + NL
                        + f"- 為什麼：{self.lessons[kind]['為什麼']}" + NL
                        + f"- 當時該怎麼做：{self.lessons[kind]['當時該怎麼做']}" + NL
                        + f"- **訂下的規則：{rule}**" + NL
                        + "- 扣分前三十秒實際做的事（拿來核對上面的歸因對不對）：" + NL
                        + self.trace_text(30.0, end=(getattr(self, "_pen_at", {}) or {}).get(kind)) + NL + NL)
        except Exception:  # noqa: BLE001
            pass
        try:
            self.add_note(f"（扣分檢討·{kind}）{rule}")
        except Exception:  # noqa: BLE001
            pass
        print(f"   📖✅ 他訂了改善規則：{rule}")
        self.lesson_hint = (f"📖 你剛才為了「{kind}」檢討過，自己訂的規則是：**{rule}**"
                            f"　從這一輪開始照著做，程式會盯著這條規則有沒有效。" + NL)
        self.log({"時間": datetime.now().strftime("%H:%M:%S"), "模式": "扣分檢討",
                  "項目": kind, "規則": rule})

    def lessons_text(self):
        ls = getattr(self, "lessons", None) or {}
        if not ls:
            return "（還沒有。被扣分累積到一定次數，程式會讓你停下來檢討、自己訂一條改善規則。）"
        det = (getattr(self, "penalty", {}) or {}).get("明細") or {}
        rows = ["（這些規則是**你自己訂的**，程式在盯著它們有沒有效）"]
        for k, v in ls.items():
            after = int(det.get(k, 0)) - int(v.get("訂時已犯", 0))
            if after <= 0:
                mark = "✅ 訂了規則之後沒再犯"
            elif after <= 2:
                mark = f"⚠ 訂了之後又犯 {after} 次"
            else:
                mark = f"❌ 訂了之後又犯 {after} 次——**這條規則沒用，換一條**"
            rows.append(f"　「{k}」→ {v['規則']}　（{v.get('訂於', '')} 訂，{mark}）")
        rows.append("→ 規則沒用就是你訂錯了，不是規則太難。犯第二次的時候重訂一條更具體的。")
        return NL.join(rows)

    def self_report(self):
        """他自己寫給使用者的回報，存進 自我回報.md，聊天網站看得到。"""
        uri, _ = jpeg_data_uri(self.draw_eyes(draw_grid(self.grab_still(1024))), 70)
        raw, _, _, _ = stream_json(self.endpoint, self.model, self.sys_prompt(),
                                   [{"type": "image_url", "image_url": {"url": uri}},
                                    {"type": "text",
                                     "text": "最近做過的事：\n" + "\n".join(self.history[-15:])
                                             + "\n\n" + self.REPORT}], 1200, time.time() + 150, 0.3)
        o = parse_json_loose(raw) or {}
        if not any(str(o.get(k, "")).strip() for k in ("做了什麼", "學到什麼", "卡在哪", "接下來")):
            # 2026-09-16：推理全開時 320 token 會「想完沒份寫 JSON」，寫出四欄全空的回報。
            # 解析不到內容就不要留空白紀錄，五分鐘後再試。
            print("   📣 回報沒解析出內容（模型可能被截斷），這次跳過")
            self._last_report = time.time() - 1500
            return
        stamp = datetime.now().strftime("%m/%d %H:%M")
        lines = [f"## {stamp} 半小時回報",
                 f"- 做了什麼：{o.get('做了什麼', '')}", f"- 學到什麼：{o.get('學到什麼', '')}",
                 f"- 卡在哪：{o.get('卡在哪', '')}", f"- 接下來：{o.get('接下來', '')}",
                 (f"- 成績：報到 {self.stat.get('登記', 0)}、拒絕 {self.stat.get('拒絕', 0)}、"
                  f"最高輪班 {self.score.get('最高輪班', 0)}、最高金幣 ${self.score.get('最高金幣', 0)}"
                  if self.mod("輪班計畫") else
                  f"- 成績：得分 {(self.reward or {}).get('總分', 0)}、扣分 {(self.penalty or {}).get('總分', 0)}、"
                  f"輪數 {self.stat.get('輪數', 0)}、空間 {len(getattr(self, 'spaces', {}) or {})} 個、"
                  f"互動點 {len(getattr(self, 'spots', {}) or {})} 個"), ""]
        with open(os.path.join(self.dir, "自我回報.md"), "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        print("\n📣 " + " ｜ ".join(l[2:] for l in lines[1:6]) + "\n")
        self.log({"時間": datetime.now().strftime("%H:%M:%S"), "模式": "半小時回報", **o})

    CALIB_STEP = 100

    def calibrate_axes(self, why="學習階段：基礎操作"):
        """量轉視角的刻度——**這是學習課程「基礎操作」的一部分**（使用者 09/14 定的）：
        一開始玩任何新遊戲，就先摸清楚這個遊戲拖 X／Y 幾像素會轉幾度、是第幾人稱。
        方法是機械的、不靠畫面內容：
          垂直：先頂到一端，往另一端每次拖 100，數「畫面還會變」的步數 → 全程 178 度需要幾個 dy
          水平：記住起點畫面，每次拖 100，畫面回到跟起點最像的時候＝轉了一圈 360 度
          視角：問反射層畫面裡看不看得到自己的背影（第三人稱）——圖片只用來輔助這一項
        結果寫進 視角校準.json 與他的操作手冊，水平儀從此用這組刻度累加。
        """
        if self.dry or not self.win.activate():
            print("   量刻度：視窗不在前景，略過")
            return False
        import numpy as np
        print(f"📐 量刻度（{why}）：上下掃全程、左右轉一圈，各拖 {self.CALIB_STEP} 一步")

        def grab():
            return np.asarray(self.grab_still(320).convert("L").resize((160, 90)), dtype=np.float32)

        def diff(a, b):
            return float(np.abs(a - b).mean())

        # 垂直：兩個方向各掃一次取平均
        totals = []
        for sign in (+1, -1):
            self.raw_look(0, -sign * 2600); time.sleep(0.6)
            steps, still = 0, 0
            for _ in range(40):
                a = grab(); self.raw_look(0, sign * self.CALIB_STEP); time.sleep(0.3); b = grab()
                if diff(a, b) < 1.5:
                    still += 1
                    if still >= 2:
                        break
                else:
                    still, steps = 0, steps + 1
            totals.append(steps * self.CALIB_STEP)
        v_total = sum(totals) / len(totals)
        # 2026-09-24 修：第二趟是 dy 負的（往上）掃，掃完停在**頂端**看天花板，
        # 以前這裡寫 -v_total/2 又往上推 → 鏡頭對著天花板、水平儀卻在這裡歸零成「完全水平」。
        self.raw_look(0, v_total / 2); time.sleep(0.4)           # 從頂端往下半程＝回到大約水平
        dy_per_deg = round(v_total / 178.0, 2) if v_total else None
        if dy_per_deg and not (1.5 <= dy_per_deg <= 25):
            # 2026-09-27：第三人稱或單調畫面會讓「畫面沒變就停」提早停，量到全程 dy 50 這種假值；不存、沿用舊的
            print(f"   ⚠ 垂直全程 dy {v_total:.0f} 不合理（每度 {dy_per_deg}），這次垂直刻度不採用")
            dy_per_deg = None

        # 水平：轉到畫面回到起點
        start = grab(); best = (1e9, 0); hist = []
        for i in range(1, 61):
            self.raw_look(self.CALIB_STEP, 0); time.sleep(0.25)
            d0 = diff(grab(), start); hist.append(d0)
            if i >= 6 and d0 < best[0]:
                best = (d0, i)
            # 畫面回到起點＝轉了一圈。2026-10-01 修：以前要 i>=12 才肯停，一圈不到 1200 px 的遊戲
            # 會轉到**第二圈**才停 → 每度 dx 剛好量成兩倍（陣列地圖掃一圈 12 面、每 6 面重複，就是這樣來的）。
            if i >= 6 and d0 < max(hist[:5]) * 0.35:
                best = (d0, i); break
        h_total = best[1] * self.CALIB_STEP if best[1] else 0
        dx_per_deg = round(h_total / 360.0, 2) if h_total else None

        # 視角：圖片只輔助這一項
        persp = ""
        try:
            uri, _ = jpeg_data_uri(self.grab_still(640), 60)
            raw, _, _, _ = stream_json(self.endpoint, self.model, self.SEE_SYS,
                                       [{"type": "image_url", "image_url": {"url": uri}},
                                        {"type": "text", "text": "畫面裡看得到自己角色的背影或身體嗎？"
                                         "看得到＝第三人稱，看不到（只有手或什麼都沒有）＝第一人稱。"
                                         '只輸出 JSON：{"視角":"第一人稱|第三人稱"}'}],
                                       60, time.time() + 12.0, 0.5, effort=反射)
            persp = str((parse_json_loose(raw) or {}).get("視角") or "")[:6]
        except Exception:  # noqa: BLE001
            pass

        try:
            path = os.path.join(self.dir, "視角校準.json")
            d = {}
            if os.path.exists(path):
                d = json.load(open(path, encoding="utf-8")) or {}
            stamp = datetime.now().strftime("%m/%d %H:%M")
            b0 = self.win.client_bbox() or {}
            if b0:
                # 2026-09-16：視窗大小一改，這組刻度就不準了（滑鼠位移是像素，畫面角度不是）。
                # 記下量的時候視窗多大，之後程式就能照實告訴他「現在跟當時差幾倍」。
                d["視窗"] = {"寬": b0.get("width"), "高": b0.get("height"), "時間": stamp}
            if dy_per_deg:
                d["垂直"] = {"全程dy": v_total, "每度dy": dy_per_deg, "方法": "機械掃描", "時間": stamp}
                self.dy_per_deg = dy_per_deg
            if dx_per_deg:
                d["水平"] = {"一圈dx": h_total, "每度dx": dx_per_deg, "方法": "轉到畫面回起點", "時間": stamp}
                self.dx_per_deg = dx_per_deg
                self.px_per_pct = max(1.5, min(15.0, round(dx_per_deg * self.DEG_PER_PCT, 1)))
            if persp:
                d["視角"] = persp
            json.dump(d, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        except Exception as e:  # noqa: BLE001
            print(f"   量刻度存檔失敗：{e}")
        # 寫進他自己的操作手冊（學習階段的驗收看這裡）
        man = getattr(self, "manual", None)
        if isinstance(man, dict):
            if dy_per_deg:
                man["滑鼠拖 dy 100"] = f"上下轉 {100 / dy_per_deg:.0f} 度（全程 178 度＝dy {v_total:.0f}）"
            if dx_per_deg:
                man["滑鼠拖 dx 100"] = f"左右轉 {100 / dx_per_deg:.0f} 度（一圈 360 度＝dx {h_total:.0f}）"
            if dy_per_deg and dx_per_deg:
                man["滑鼠拖 刻度"] = f"每度：dx {dx_per_deg}／dy {dy_per_deg}（機械量的，圖片只輔助）"
            try:
                self.save_manual()
            except Exception:  # noqa: BLE001
                pass
        if persp and isinstance(getattr(self, "game_id", None), dict) and not self.game_id.get("視角"):
            self.game_id["視角"] = persp
            try:
                self.save_stage()
            except Exception:  # noqa: BLE001
                pass
        self.pitch_deg, self.pitch_edge = 0.0, ""
        print(f"📐 量完：垂直全程 dy {v_total:.0f}（每度 {dy_per_deg}）｜"
              f"水平一圈 dx {h_total}（每度 {dx_per_deg}）｜視角 {persp or '?'}")
        self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 程式帶你量了轉視角的刻度："
                            f"dy 100≈{(100 / dy_per_deg) if dy_per_deg else 0:.0f} 度、"
                            f"dx 100≈{(100 / dx_per_deg) if dx_per_deg else 0:.0f} 度、{persp or '視角未知'}")
        self.log({"時間": datetime.now().strftime("%H:%M:%S"), "模式": "量刻度",
                  "垂直全程": v_total, "水平一圈": h_total, "視角": persp})
        return True

    def calibrate_all(self):
        """手冊還是空的時候，先把基本操作實測一輪（轉頭三種幅度、前後左右各一秒）。

        使用者要求「所有操作他都要大約推演過」——這一輪測完的數字會寫進他自己的操作手冊，
        之後每一輪都看得到，他也可以隨時用「實驗」動作再測。
        """
        try:
            cal = json.load(open(os.path.join(self.dir, "視角校準.json"), encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            cal = {}
        if not (cal.get("水平") and cal.get("垂直")):
            try:
                self.calibrate_axes("這個遊戲還沒量過刻度")
            except Exception as e:  # noqa: BLE001
                print(f"   量刻度失敗：{e}")
        if getattr(self, "manual", None):
            return
        print("🧪 操作手冊是空的，先做一輪基礎實測")
        plan = [{"t": "實驗", "k": "look", "dx": 50}, {"t": "實驗", "k": "look", "dx": 100},
                {"t": "實驗", "k": "look", "dx": 200}, {"t": "實驗", "k": "look", "dx": -100},
                {"t": "實驗", "k": "w", "d": 0.5}, {"t": "實驗", "k": "w", "d": 1.0},
                {"t": "實驗", "k": "w", "d": 2.0}, {"t": "實驗", "k": "s", "d": 1.0},
                {"t": "實驗", "k": "a", "d": 1.0}, {"t": "實驗", "k": "d", "d": 1.0}]
        for a in plan:
            if self.stop.is_set() or os.path.exists(os.path.join(HERE, "stop.txt")):
                return
            try:
                self.run_experiment(a)
            except Exception as e:  # noqa: BLE001
                print(f"   實驗失敗：{e}")
            time.sleep(0.3)
        self.level_camera(2)
        print(f"🧪 基礎實測完成，手冊有 {len(self.manual)} 條")

    def run(self):
        print(f"[flow_agent] 工作流程模式：程式只給文件與畫面，步驟由他自己走。"
              f"　大腦={self.model}　眼睛=本機 {self.eyes.__class__.__name__}（YOLO-World）")
        print(f"   目前流程：{self.flow} 第 {self.step} 步")
        try:
            self.calibrate_all()
            self.gyro_zero("開機，把現在的姿勢當成出生基準")
            super().run()
        finally:
            self.eyes.stop()
            try:
                self.text_eyes.stop()
            except Exception:  # noqa: BLE001
                pass
            self.save_map()
            self.save_appear()
            self.save_stat()
            print(f"[流程統計] {self.stat}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", default="動物醫院")
    ap.add_argument("--hours", type=float, default=8.0)
    # 2026-09-14 推理全開之後，一輪不可能在 10 秒內講完。
    # 實測：25 秒截止時 7 輪只有 2 輪產出動作（其餘思考還沒講完就被砍），
    # 成功的那幾輪落在 27 秒上下 → 放寬到 45 秒。
    # 代價是輪數大幅變少（原本 10 秒一輪），換到的是他每一輪真的想過一遍。
    # 2026-09-17 使用者（第二版）：**快思放到 30 秒讓它真的講得完，但比重降低、多用反射**。
    # 實測 10 秒時 83 輪有 82 輪講不完＝每輪固定浪費 10 秒再花 3.5 秒用反射補救。
    # 現在改成：多數輪次直接用反射（2~4 秒出動作），每 N 輪才用一次會思考的快思。
    ap.add_argument("--deadline", type=float, default=30.0)
    ap.add_argument("--reflex-deadline", type=float, default=20.0, help="反射輪最多等模型幾秒（慢的大腦要放寬）")
    ap.add_argument("--think-every", type=int, default=4,
                    help="每幾輪用一次快思（會思考），其他輪用反射。1＝每輪都思考")
    ap.add_argument("--short-mem", type=int, default=4,
                    help="短期記憶記最近幾輪（畫面＋做了什麼）。0＝關掉")
    ap.add_argument("--short-mem-img", type=int, default=3,
                    help="短期記憶裡最新幾輪要附畫面縮圖（其餘只留文字）")
    ap.add_argument("--reflex-slim", action="store_true",
                    help="反射輪改送精簡提示（舊行為，比較快但他看不到面板上的資訊）")
    ap.add_argument("--key-move-min", type=int, default=20,
                    help="加幾分以上要記成關鍵動作（前後各追幾輪都會留下來）")
    # 2026-09-14 推理全開之後 token 預算要一起放大：
    # 實測思考本身就吃掉 221（low）～400（medium）個 token，
    # 原本 260 的上限等於「思考完就沒額度寫 JSON」——當場出現「20 秒沒動作」。
    ap.add_argument("--max-tokens", type=int, default=900)
    ap.add_argument("--reflect-min", type=float, default=20.0)
    ap.add_argument("--vision-fps", type=float, default=5.0)
    ap.add_argument("--vision-size", type=int, default=480)
    ap.add_argument("--flow", default="", help="開場先用哪一份流程")
    ap.add_argument("--map", default="off",
                    help="on=程式畫地圖並算方位給他看；off=只留他自己記的地名，地圖樣子由他自己想")
    ap.add_argument("--prompt", default="模組",
                    help="模組＝從 提示/*.md 載入（預設）；off／內建＝用程式裡那一份完整 SYS")
    ap.add_argument("--lock-flow", default="", help="鎖定只做某一個流程（例如 報到）")
    ap.add_argument("--guards", default="",
                    help="防線開關：留空=全開；off=全關；或用逗號指定要保留的（抬頭,幅度,兩步,吸附,振盪,煞車,打轉,重複,脫困,理智,空轉aim,鏡頭復位）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    log_dir = project_dir(args.game, "記憶")
    if not os.path.isdir(log_dir) and os.path.isdir(os.path.join(HERE, "資料", args.game)):
        log_dir = os.path.join(HERE, "資料", args.game)      # 舊結構
    os.makedirs(log_dir, exist_ok=True)
    sys.stdout = Tee(os.path.join(log_dir, "agent.log"), sys.stdout)
    sys.stderr = Tee(os.path.join(log_dir, "agent.log"), sys.stderr)
    print(f"\n===== {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} 啟動（工作流程模式）=====")
    FlowAgent(args).run()


if __name__ == "__main__":
    main()
