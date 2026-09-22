#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════════╗
║  ⚠ 舊架構，已淘汰（2026-09-11 標記）——保留作為紀錄，不要再改也不要再跑  ║
╚══════════════════════════════════════════════════════════════════════╝

現在的主力是 `flow_agent.py`（工作流程模式）。這個檔案是它的前身，留著是為了
保存當時的做法與踩過的坑，**不是**現在跑的東西。

為什麼被取代：只被 skill_agent.py 使用；技能狀態機淘汰後跟著退役。裡面的閉環控制概念（眼睛回饋、走到目標前停下）已經搬進 flow_agent 的 goto_target。

要跑代理請用：
    python flow_agent.py --game 動物醫院 --hours 12 --deadline 10         --lock-flow 報到,治療,開始上班,找人一起玩 --map off
原本的說明如下
------------------------------------------------------------

skills.py — 技能庫：把「走到人旁邊」「報到」「喝咖啡」「治療」各自包成一個技能。

設計原則
  1. 大腦（DGX 大模型）只看得到技能的「名字」和「一句話用途」，**看不到步驟細節**。
     它只要決定「現在用哪一個技能、對哪個目標」，剩下的由技能自己完成。
  2. 技能內部是程式寫死的閉環，眼睛（本機 Grounding DINO，每秒 4~5 次）提供即時位置：
     對準 → 前進 → 目標變夠大就停。不用每一步都去問大腦，也就不會走到牆裡。
  3. 只有「語意判斷」才問大腦（例如：這個病人正常還是異常、畫面上的按鈕在哪）。

技能介面：
    s.name      技能名（大腦用這個字串選它）
    s.purpose   一句話用途（大腦唯一看得到的說明）
    s.begin(target)   開始（可帶目標）
    s.tick()    推進一小步，回傳 None(還在做) / "完成" / "失敗:原因"
"""
import time

# 走到不同東西前面時，「目標在畫面上要多高」才算走到了（畫面高度比例）
STOP_H = {
    "a person": 0.55,
    "a counter desk": 0.50,
    "a coffee machine": 0.45,
    "a printer": 0.40,
    "a door": 0.70,
}
DEFAULT_STOP_H = 0.45


class Skill:
    name = "技能"
    purpose = ""
    watch = []          # 這個技能要眼睛盯著的東西

    def __init__(self, ag):
        self.ag = ag
        self.t0 = 0
        self.ticks = 0
        self.target = ""
        self.note = ""

    # ---- 生命週期
    def begin(self, target=""):
        self.t0 = time.time()
        self.ticks = 0
        self.target = target or ""
        self.note = ""
        if self.watch:
            self.ag.eyes.watch(self.watch)
        self.on_begin()

    def on_begin(self):
        pass

    def tick(self):
        raise NotImplementedError

    # ---- 小工具
    @property
    def age(self):
        return time.time() - self.t0

    def say(self, msg):
        print(f"   [{self.name}] {msg}")
        self.note = msg


class 走到(Skill):
    """移動：方向由大腦（DGX）指，眼睛（本機 5 fps）負責盯住目標、微調對準、在正確距離停下。

    分工：
      大腦 → 「去哪個目標、往哪個方向」（begin 時給、看不到目標時再問一次）
      眼睛 → 每 0.2 秒回報目標在畫面哪裡、多大 → 程式對準、前進、到位就停
    """

    name = "走到"
    purpose = "往某個方向或某個東西移動過去，並在正確的距離停下來"
    watch = ["a person", "a counter desk", "a door", "a coffee machine"]

    P_DIR = """【只回答這一題】我要去「{target}」，但現在畫面上看不到它。我該往哪個方向轉、往哪走？
（有黃色箭頭就跟著黃色箭頭指的方向）
只輸出 JSON：{{"dir":"左"或"右"或"前"或"後","x":0.00或null,"y":0.00或null,"see":"畫面重點(<=20字)"}}"""

    def on_begin(self):
        t = self.target or "a person"
        self.ag.level_camera()          # 先把鏡頭轉回水平，不然看到的是地板或天花板
        self.ag.eyes.watch(list(dict.fromkeys([self.ag.en(t)] + self.watch)))
        self.stop_h = STOP_H.get(self.ag.en(t), DEFAULT_STOP_H)
        self.lost = 0
        self.asks = 0
        self.last_h = 0.0
        self.no_progress = 0
        # 大腦在選技能時就指了方向／位置，先照它說的轉過去
        hint = self.ag.hint or {}
        x, y = self.ag.xy(hint)
        if x is not None:
            self.ag.aim_to(x, y, t)
        elif hint.get("dir"):
            self.turn(hint["dir"])
        self.say(f"目標「{t}」，方向由大腦指，走到它高度佔畫面 {self.stop_h:.0%} 就停")

    def turn(self, d):
        r = self.ag.ppp()
        if d == "左":
            self.ag.look(int(-r * 25), 0)
        elif d == "右":
            self.ag.look(int(r * 25), 0)
        elif d == "後":
            self.ag.look(int(r * 50), 0)
        else:
            self.ag.press("w", 0.5)

    def tick(self):
        self.ticks += 1
        if self.age > 90:
            return "失敗:走太久還沒到"
        d = self.ag.eyes.best(self.target or "a person")
        if d is None:
            self.lost += 1
            if self.lost % 6 == 0:          # 眼睛看不到 → 回頭問大腦方向
                self.asks += 1
                if self.asks > 5:
                    return "失敗:大腦指了方向還是找不到目標"
                o = self.ag.brain(self.P_DIR.format(target=self.target))
                x, y = self.ag.xy(o)
                if x is not None:
                    self.ag.aim_to(x, y, self.target)
                    self.ag.press("w", 0.6)
                else:
                    self.turn(str(o.get("dir") or "右"))
                    self.ag.press("w", 0.5)
            else:
                if self.lost % 3 == 0:
                    self.ag.level_camera(1)
                self.ag.look(int(self.ag.ppp() * 15), 0)
            time.sleep(0.1)
            return None
        self.lost = 0
        # 眼睛看得到了 → 由眼睛閉環：對準、前進、到位就停
        ex, ey = d["cx"] - 0.5, d["cy"] - 0.5
        if abs(ex) > 0.06:
            self.ag.look(int(ex * 100 * self.ag.ppp() * 0.8), 0)
            return None
        if abs(ey) > 0.22:
            self.ag.look(0, int(ey * 100 * self.ag.ppp() * 0.5))
            return None
        bottom = d["cy"] + d["h"] / 2
        if d["h"] >= self.stop_h or bottom > 0.92:
            self.say(f"到了，停下（高度 {d['h']:.2f}，走了 {self.age:.1f} 秒）")
            return "完成"
        self.ag.press("w", 0.30)
        if d["h"] <= self.last_h + 0.004:
            self.no_progress += 1
        else:
            self.no_progress = 0
        self.last_h = d["h"]
        if self.no_progress >= 6:
            self.say("走不動，被東西擋住，側移繞開")
            self.ag.press("d" if self.ticks % 2 else "a", 0.45)
            self.ag.press("w", 0.35)
            self.no_progress = 0
        return None


class 看看四周(Skill):
    """原地轉一圈，用眼睛記下看到什麼（給大腦當判斷素材）。"""

    name = "看看四周"
    purpose = "不知道自己在哪、找不到東西時，先看一圈"
    watch = ["a person", "a counter desk", "a door", "a coffee machine", "a printer"]

    def on_begin(self):
        self.ag.level_camera()
        self.found = {}
        self.turns = 0

    def tick(self):
        self.ticks += 1
        dets, _ = self.ag.eyes.latest()
        for k, v in dets.items():
            if v and (k not in self.found or v[0]["score"] > self.found[k]["score"]):
                self.found[k] = v[0]
        if self.turns >= 8:
            names = "、".join(f"{k}({v['cx']:.2f},{v['cy']:.2f} 大小{v['h']:.2f})" for k, v in self.found.items())
            self.say(f"看完一圈：{names or '什麼都沒看到'}")
            self.ag.last_scan = self.found
            return "完成"
        self.ag.look(int(self.ag.ppp() * 12), 0)     # 每次轉約 12% 畫面
        self.turns += 1
        time.sleep(0.25)
        return None


class 報到(Skill):
    """櫃檯報到：印資料 → 問大腦四層核對 → 正常登記／異常關快門。

    位置由眼睛顧、判斷由大腦做，按鈕位置問大腦（偵測器對文字按鈕不準）。
    """

    name = "報到"
    purpose = "處理櫃檯窗口的病人（這是賺錢的本業）"
    watch = ["a person", "a counter desk", "a printer"]

    P_PRINT = """【只回答這一題】窗口這位病人的資料印出來了嗎？
- 文件／病歷／資料卡已經在櫃檯上或你手上 → done=1
- 還沒印 → done=0，並指出印表機或「按住 E」提示的位置
- 窗口沒有人 → none=1
只輸出 JSON：{"done":0或1,"none":0或1,"x":0.00或null,"y":0.00或null,"see":"畫面重點(<=20字)"}"""

    P_JUDGE = """【只回答這一題】窗口這位病人是「正常」還是「異常」？四層核對：
① 本人的臉、眼睛、身形、姿勢有沒有不對 ② 資料上的照片和本人是不是同一個
③ CCTV 有沒有變形、黑屏、直視鏡頭 ④ 文件的名字、日期、症狀有沒有矛盾
任何一層明顯不對就是異常；四層都看過都沒問題才是正常；還沒看清楚就回「看不清」。
只輸出 JSON：{"verdict":"正常"或"異常"或"看不清","why":"理由(<=20字)","see":"畫面重點(<=20字)"}"""

    P_BUTTON = """【只回答這一題】畫面上「{what}」在哪個位置？
只輸出 JSON：{{"x":0.00,"y":0.00,"f":0或1(找不到=0),"done":0或1(這件事已經完成=1),"see":"畫面重點(<=20字)"}}"""

    def on_begin(self):
        # 還沒站到櫃檯前就先自己走過去（眼睛閉環），大腦不用管這一步
        d = self.ag.eyes.best("a counter desk")
        if d is None or d["h"] < STOP_H["a counter desk"] * 0.9:
            self.walk = 走到(self.ag)
            self.walk.begin("a counter desk")
            self.phase = "走到櫃檯"
        else:
            self.walk = None
            self.phase = "印資料"
        self.fail = 0

    def tick(self):
        self.ticks += 1
        if self.age > 150:
            return "失敗:報到拖太久"
        if self.phase == "走到櫃檯":
            r = self.walk.tick()
            if r == "完成":
                self.say("站到櫃檯前了，開始處理病人")
                self.phase = "印資料"
            elif r and r.startswith("失敗"):
                return f"失敗:走不到櫃檯（{r[3:]}）"
            return None
        if self.phase == "印資料":
            o = self.ag.brain(self.P_PRINT)
            if o.get("none") == 1:
                return "失敗:這裡沒有可以報到的窗口（可能還在醫院外面，要先開始上班）"
            if o.get("done") == 1:
                self.say("資料印好了，開始核對")
                self.phase = "核對"
                return None
            x, y = self.ag.xy(o)
            if x is not None:
                self.ag.aim_to(x, y, "印表機")
            self.ag.hold_e("印表機")
            self.fail += 1
            return None if self.fail < 5 else "失敗:印表機按不動（可能不是真的櫃檯，先開始上班）"
        if self.phase == "核對":
            o = self.ag.brain(self.P_JUDGE, max_tokens=180, secs=10)
            v = str(o.get("verdict") or "")
            why = str(o.get("why") or "")
            if "異常" in v:
                self.say(f"判定異常：{why}")
                self.ag.note(f"（核對）異常：{why}")
                self.phase = "關快門"
                self.fail = 0
                return None
            if "正常" in v:
                self.say(f"判定正常：{why}")
                self.phase = "登記"
                self.fail = 0
                return None
            self.fail += 1
            if self.fail >= 4:
                self.say("看不清楚，保守起見關快門拒絕")
                self.phase = "關快門"
                self.fail = 0
            return None
        what = "Check In（登記）按鈕" if self.phase == "登記" else "關快門（Shutter）的開關或拉桿"
        o = self.ag.brain(self.P_BUTTON.format(what=what))
        if o.get("done") == 1:
            if self.phase == "登記":
                self.ag.stat_add("登記")
                self.say(f"完成 Check In（累計 {self.ag.stat['登記']} 次）")
            else:
                self.ag.stat_add("拒絕")
                self.say(f"已關快門拒絕異常（累計 {self.ag.stat['拒絕']} 次）")
            return "完成"
        x, y = self.ag.xy(o)
        if x is None:
            loc = self.ag.locate(what)
            x, y = loc if loc else (None, None)
        if x is not None:
            if self.phase == "登記":
                self.ag.click(x, y, "Check In 登記按鈕")
            else:
                self.ag.aim_to(x, y, "快門開關")
                self.ag.hold_e("關快門")
        else:
            self.ag.hold_e("櫃檯互動")
        self.fail += 1
        return None if self.fail < 6 else f"失敗:找不到{what}"


class 喝咖啡(Skill):
    """理智低時去咖啡機補理智：走過去（眼睛閉環）→ 長按 E 煮 → 長按 E 喝。"""

    name = "喝咖啡"
    purpose = "理智太低的時候補理智"
    watch = ["a coffee machine"]

    P = """【只回答這一題】現在的理智（Sanity）是多少百分比？咖啡好了嗎？
只輸出 JSON：{"san":數字或null,"ready":0或1(有可以喝的咖啡或提示=1),"see":"畫面重點(<=20字)"}"""

    def on_begin(self):
        self.walk = 走到(self.ag)
        self.walk.begin("a coffee machine")
        self.phase = "走過去"
        self.brews = 0

    def tick(self):
        self.ticks += 1
        if self.age > 120:
            return "失敗:找不到咖啡機"
        if self.phase == "走過去":
            r = self.walk.tick()
            if r == "完成":
                self.phase = "煮"
            elif r and r.startswith("失敗"):
                return r
            return None
        ok, _ = self.ag.hold_e_raw("咖啡機")
        self.brews += 1
        o = self.ag.brain(self.P)
        san = o.get("san")
        if san is not None and float(san) >= 66:
            self.ag.san = float(san)
            self.ag.stat_add("喝咖啡")
            self.say(f"理智回到 {san}%")
            return "完成"
        if san is not None:
            self.ag.san = float(san)
        if self.brews >= 8:
            return "失敗:喝了幾次理智沒回來"
        return None


class 治療(Skill):
    """治療已登記的病人：問大腦要用什麼道具 → 走到道具 → 拿 → 走到病人 → 用上去。"""

    name = "治療"
    purpose = "處理已經登記進來的病人的症狀"
    watch = ["a person"]

    P_WHAT = """【只回答這一題】這位病人的症狀是什麼？該用哪一樣東西處理？
可用的東西：草藥、眼藥水、咖啡、滅火器、楓糖漿、電擊器、掃描儀。
只輸出 JSON：{"symptom":"症狀(<=12字)","item":"要用的東西(<=6字)","x":0.00或null,"y":0.00或null,"see":"畫面重點(<=20字)"}"""
    P_DONE = """【只回答這一題】這位病人處理完了嗎？（已離開、症狀消失、出現結算）
只輸出 JSON：{"done":0或1,"x":0.00或null,"y":0.00或null,"see":"畫面重點(<=20字)"}"""

    def on_begin(self):
        self.phase = "看症狀"
        self.item = ""
        self.fail = 0

    def tick(self):
        self.ticks += 1
        if self.age > 180:
            return "失敗:治療拖太久"
        if self.phase == "看症狀":
            o = self.ag.brain(self.P_WHAT, max_tokens=160)
            self.item = str(o.get("item") or "")
            self.say(f"症狀「{o.get('symptom')}」→ 用「{self.item}」")
            self.ag.note(f"（治療）症狀 {o.get('symptom')} 用 {self.item}")
            self.phase = "拿道具"
            return None
        if self.phase == "拿道具":
            x, y = self.ag.xy(self.ag.brain(self.P_DONE))     # 順便確認是不是已經好了
            loc = self.ag.locate(self.item or "治療用的道具")
            if loc:
                self.ag.aim_to(loc[0], loc[1], self.item)
                self.ag.press("w", 0.4)
                self.ag.hold_e(self.item)
                self.phase = "用上去"
            else:
                self.fail += 1
                self.ag.look(int(self.ag.ppp() * 12), 0)
                if self.fail >= 8:
                    return "失敗:找不到道具"
            return None
        d = self.ag.eyes.best("a person")
        if d:
            self.ag.aim_to(d["cx"], d["cy"], "病人")
            self.ag.click(d["cx"], d["cy"], "把道具用在病人身上")
        o = self.ag.brain(self.P_DONE)
        if o.get("done") == 1:
            self.ag.stat_add("治療")
            self.say("病人處理完了")
            return "完成"
        self.fail += 1
        return None if self.fail < 10 else "失敗:用不上去"


class 脫困(Skill):
    """臉貼牆、卡住時：後退、轉身、換方向走。純程式，不問大腦。"""

    name = "脫困"
    purpose = "卡住不能動的時候用"
    watch = ["a door", "a counter desk"]

    def on_begin(self):
        self.step = 0

    def tick(self):
        self.ticks += 1
        near, tex = self.ag.wall_score()
        if self.step > 0 and not near:
            self.say(f"脫困了（紋理 {tex:.0f}）")
            return "完成"
        if self.step >= 4:
            return "失敗:脫不了困"
        self.ag.press("s", 0.7)
        self.ag.look(int(self.ag.ppp() * 50), 0)
        self.ag.press("w", 0.4)
        self.step += 1
        return None


class 處理死亡(Skill):
    """死掉或回合結束：只點「前往大廳」「立即開始」，絕不點要花錢的復活。"""

    name = "處理死亡"
    purpose = "角色倒下或這一回合結束時，重新開始"
    watch = ["a button"]

    P = """【只回答這一題】畫面上「前往大廳」或「立即開始」按鈕在哪？（「自我復活」要花錢，不准點）
- 已經回到正常遊戲畫面 → back=1
只輸出 JSON：{"back":0或1,"btn":"前往大廳"或"立即開始"或"沒有","x":0.00或null,"y":0.00或null,"see":"畫面重點(<=20字)"}"""

    def on_begin(self):
        self.fail = 0

    def tick(self):
        self.ticks += 1
        o = self.ag.brain(self.P)
        if o.get("back") == 1:
            self.ag.san = None
            self.say("回到遊戲了")
            return "完成"
        x, y = self.ag.xy(o)
        btn = str(o.get("btn") or "")
        if x is not None and btn in ("前往大廳", "立即開始"):
            self.ag.click(x, y, btn)
        else:
            time.sleep(0.8)
        self.fail += 1
        if self.fail >= 10:
            self.ag.force_leave()
            return "完成"
        return None


class 進遊戲(Skill):
    """人不在遊戲裡（Roblox 首頁／大廳／載入畫面）時，自己點回「動物醫院」開始玩。"""

    name = "進遊戲"
    purpose = "現在不在遊戲裡的時候，回到遊戲裡開始玩"
    watch = ["a button"]

    P = """【只回答這一題】現在畫面是在遊戲裡，還是在 Roblox 的首頁／大廳／載入畫面？
- 已經在遊戲裡（看得到角色、醫院場景、遊戲介面）→ in=1
- 還在首頁／大廳 → in=0，並指出要點的東西在哪：
  先找「動物醫院／Animal Anomaly」的遊戲圖示（最近玩過、繼續遊玩、搜尋結果都算），
  已經在遊戲頁面就找綠色的播放（Play／▶）按鈕。
⚠ 任何要花錢、加值、會員的按鈕都不准點。
只輸出 JSON：{"in":0或1,"what":"遊戲圖示"或"播放按鈕"或"載入中"或"沒有","x":0.00或null,"y":0.00或null,"see":"畫面重點(<=20字)"}"""

    def on_begin(self):
        self.fail = 0
        self.clicked = 0

    def tick(self):
        self.ticks += 1
        if self.age > 240:
            return "失敗:進不了遊戲"
        o = self.ag.brain(self.P, max_tokens=140, secs=9)
        if o.get("in") == 1:
            self.say("已經在遊戲裡了")
            return "完成"
        what = str(o.get("what") or "")
        if what == "載入中":
            time.sleep(3.0)
            return None
        x, y = self.ag.xy(o)
        if x is None:
            self.fail += 1
            time.sleep(1.5)
            return None if self.fail < 8 else "失敗:找不到遊戲圖示"
        self.ag.click(x, y, what or "遊戲圖示")
        self.clicked += 1
        self.say(f"點了「{what}」（第 {self.clicked} 次）")
        time.sleep(3.0)
        return None


class 開始上班(Skill):
    """人還在醫院外面的大廳時，進到醫院裡開始這一輪班（有櫃檯可以報到才算開始）。"""

    name = "開始上班"
    purpose = "還沒開始這一輪班的時候，進到醫院裡開工"
    watch = ["a door", "a counter desk", "a person"]

    P = """【只回答這一題】我現在是在醫院裡上班，還是在外面的大廳？
- 已經在醫院裡上班（看得到接待櫃檯、窗口、輪班倒數、病人排隊）→ working=1
- 畫面上開著選單面板（職業／商店／日誌／等級那種一大塊的視窗）→ menu=1（程式會幫你關掉）
- 還在外面（ANIMAL HOSPITAL 建築外觀、街道、左邊那排商店／邀請／職業／日誌按鈕）→ working=0，
  指出「進醫院上班」要往哪走或點什麼：醫院大門、發光或黃色的地板區域、電梯、黃色箭頭、Solo／開始／Start。
⚠ 左邊那排「商店／邀請／職業／日誌」都不是開工的按鈕，不要點；任何要花錢、解鎖、升級的更不准點。
只輸出 JSON：{"working":0或1,"menu":0或1,"what":"門"或"按鈕"或"箭頭"或"地板區域"或"沒有","x":0.00或null,"y":0.00或null,"see":"畫面重點(<=20字)"}"""

    def on_begin(self):
        self.fail = 0
        self.walk = None

    def tick(self):
        self.ticks += 1
        if self.age > 180:
            return "失敗:進不去醫院開工"
        if self.walk is not None:                   # 正在走去門口
            r = self.walk.tick()
            if r == "完成":
                self.walk = None
                self.ag.hold_e("醫院入口")
                self.ag.press("w", 1.0)
            elif r and r.startswith("失敗"):
                self.walk = None
                self.fail += 1
            return None
        o = self.ag.brain(self.P, max_tokens=150, secs=9)
        if o.get("working") == 1:
            self.say("已經在醫院裡了，可以開始接病人")
            return "完成"
        if o.get("menu") == 1:                       # 面板擋住畫面 → 先關掉
            self.say("有選單面板開著，先按 esc 關掉")
            self.ag.press("esc", 0.05)
            time.sleep(0.8)
            return None
        what = str(o.get("what") or "")
        x, y = self.ag.xy(o)
        if what == "按鈕" and x is not None:
            self.ag.click(x, y, "開始輪班按鈕")
            self.say("點了開始輪班的按鈕")
            time.sleep(2.0)
            return None
        if x is not None:
            self.ag.aim_to(x, y, what or "入口")
        self.walk = 走到(self.ag)                    # 交給眼睛閉環走過去
        self.walk.begin("a door")
        self.fail += 1
        return None if self.fail < 8 else "失敗:找不到醫院入口"


class 想一想(Skill):
    """停下來自己想：整理現在的狀況、決定接下來要怎麼做，把計畫記下來。

    這是留給千問「自主思考」的空間——它可以自己選這個技能，程式也會在卡住或死亡後強制觸發。
    """

    name = "想一想"
    purpose = "停下來整理現況、想清楚接下來要怎麼做（想完的計畫會記住）"
    watch = ["a person", "a counter desk"]

    P_Q = """【只回答這一題】你現在最需要想清楚的問題是什麼？（一句話，之後你會有比較長的時間好好想）
只輸出 JSON：{"q":"問題(<=25字)","see":"畫面重點(<=20字)"}"""

    def on_begin(self):
        self.asked = False

    def tick(self):
        self.ticks += 1
        if not self.asked:
            self.asked = True
            o = self.ag.brain(self.P_Q, max_tokens=90, secs=7)
            q = str(o.get("q") or "").strip() or "我現在該做什麼才能多做幾次報到？"
            self.say(f"想：{q}")
            try:
                self.ag.think(q)            # 用比較長的時間思考，結果寫進計畫.md 並記住
            except Exception as e:          # noqa: BLE001
                return f"失敗:想不出來（{type(e).__name__}）"
            plan = (getattr(self.ag, "plan", "") or "").strip()
            if plan:
                self.say(f"計畫：{plan[:60]}")
            return "完成"
        return "完成"


class 自己來(Skill):
    """不照任何流程，千問自己決定要按什麼、點哪裡。

    技能只是「大概的工作流程」，不是硬規定。碰到流程沒涵蓋的狀況（奇怪的視窗、新的機關、
    臨時事件），它可以選這個技能自己動手，程式只負責擋掉會花錢的動作。
    """

    name = "自己來"
    purpose = "現有的做法都不合適時，自己決定要按什麼、點哪裡"
    watch = ["a person", "a counter desk", "a door"]

    P = """【現在由你自己動手】看畫面，決定接下來要做的 1~2 個動作。
可用動作（座標是畫面比例 0~1）：
  {"t":"click","x":0.5,"y":0.4,"g":"點的東西"}   {"t":"hold","k":"e","g":"要互動的東西"}
  {"t":"aim","x":0.5,"y":0.5,"g":"對準的東西"}   {"t":"key","k":"w","d":1.0}
  {"t":"look","dx":200,"dy":0}                   {"t":"wait","d":1.0}
⚠ 任何要花錢、購買、解鎖、升級、復活的都不准點。
做完你想做的事就把 done 設成 1，程式會讓你重新決定下一件事。
只輸出 JSON：{"a":[動作...最多2個],"done":0或1,"see":"畫面重點(<=20字)","n":"學到的事(<=25字)或空"}"""

    def on_begin(self):
        self.rounds = 0

    def tick(self):
        self.ticks += 1
        o = self.ag.brain(self.P, max_tokens=220, secs=10)
        acts = o.get("a") or []
        if isinstance(acts, list) and acts:
            self.ag.do(acts)
            self.say("、".join(self.ag.describe(a) for a in acts if isinstance(a, dict)))
        if o.get("n"):
            self.ag.note(str(o["n"]))
        self.rounds += 1
        if o.get("done") == 1:
            return "完成"
        if self.rounds >= 8:
            return "完成"
        if self.age > 90:
            return "失敗:自己來做太久"
        return None


ALL = [走到, 看看四周, 開始上班, 報到, 喝咖啡, 治療, 想一想, 自己來, 脫困, 處理死亡, 進遊戲]
