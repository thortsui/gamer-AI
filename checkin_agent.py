#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════════╗
║  ⚠ 舊架構，已淘汰（2026-09-11 標記）——保留作為紀錄，不要再改也不要再跑  ║
╚══════════════════════════════════════════════════════════════════════╝

現在的主力是 `flow_agent.py`（工作流程模式）。這個檔案是它的前身，留著是為了
保存當時的做法與踩過的坑，**不是**現在跑的東西。

為什麼被取代：專門做 Check In 的單一用途代理，寫死的步驟撐不住實際狀況變化。現在報到是 flow_agent 的一條工作流程（工作流程/20_報到.md），內容由他自己累積。

要跑代理請用：
    python flow_agent.py --game 動物醫院 --hours 12 --deadline 10         --lock-flow 報到,治療,開始上班,找人一起玩 --map off
原本的說明如下
------------------------------------------------------------

checkin_agent.py — 只做「櫃檯 Check In 處理」的專用代理

和 play_agent.py 的差別：
  play_agent    = 教他玩整個遊戲（模型自己想目標、自己找路），結果 Check In 次數 0、卡住 30%
  checkin_agent = 程式主導的櫃檯報到流程，模型只回答「這一格畫面上的一個小問題」

狀態機（程式決定順序，模型只做判斷）：
  找櫃檯 → 印資料 → 核對(四層) → 登記 或 拒絕 → 等下一位 → 印資料 …
  例外狀態：喝咖啡（理智太低）、死亡處理（倒地／理智歸零）

每輪只送一張 1024 寬的清楚畫面（不送影片）：櫃檯作業是靜態畫面，這樣每輪快 1~2 秒。

用法：
  python checkin_agent.py --game 動物醫院 --hours 8
  python checkin_agent.py --game 動物醫院 --dry-run      # 只判斷不動作
停止：專案夾放 stop.txt / Ctrl+F12 / 滑鼠甩左上角
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from play_agent import (Agent, Tee, HERE, stream_json, parse_json_loose,  # noqa: E402
                        draw_grid, jpeg_data_uri)

# ------------------------------------------------------------------ 提示詞
SYS = """你是 gamer AI，在 Roblox 遊戲「{game}」（Animal Anomaly）裡擔任醫院櫃檯的報到員。
你唯一的工作是：幫窗口的病人做 Check In —— 印資料 → 比對照片 → 看 CCTV → 正常就登記、異常就關快門。
現在程式會一步一步問你問題，**你這一輪只要回答眼前這一個問題**，不要規劃整個遊戲、不要想別的事、不要提議去做別的。

畫面上淡黃色的格線與 0.1~0.9 的小數字是程式疊上去的座標尺（左上角 0,0、右下角 1,1），遊戲裡沒有這東西，
不會遮住任何東西、不用理它，你報位置時用它當尺就好。

鐵律：不准花任何錢（Robux、金幣、購買、解鎖、升級、自我復活都不准點）。

只輸出一個 JSON，不要 markdown、不要多餘文字、不要換行。"""

# 每個提示都要回報的共同欄位
COMMON = ('"see":"畫面重點(<=20字)","san":理智(Sanity)百分比數字，看不到就 null,'
          '"dead":0或1(角色倒地／理智歸零／出現死亡或「前往大廳」畫面=1)')

P_DESK = """【問題】我現在是不是已經站在櫃檯前，可以開始做報到了？
站在櫃檯前＝畫面上看得到接待窗口、窗口對面的病人、櫃檯上的印表機／文件，或 Check In 介面。
如果還沒到櫃檯，請指出「櫃檯」或「黃色箭頭指的方向」在畫面上的位置（黃色箭頭最優先，跟著它一定對）。
只輸出 JSON：{"at":0或1,"x":0.00或null,"y":0.00或null,"far":"遠"或"中"或"近",%s}""" % COMMON

P_PRINT = """【問題】這位病人的資料印出來了嗎？
- 病歷／文件／資料卡已經印出來、在櫃檯上或在你手上 → done=1
- 還沒印 → done=0，並指出「印表機」或畫面上「按住 E」提示的位置
- 窗口根本沒有病人（沒人來、快門關著、櫃檯空的）→ none=1
只輸出 JSON：{"done":0或1,"none":0或1,"x":0.00或null,"y":0.00或null,%s}""" % COMMON

P_INSPECT = """【問題】窗口這位病人是「正常」還是「異常」？請做四層核對：
① 本人：臉、眼睛、身形、姿勢有沒有不對（抽搐、扭曲、空洞的眼睛、直視你）
② 照片：印出來的資料上的照片和本人是不是同一個
③ CCTV：監視器畫面有沒有變形、黑屏、多出東西、直視鏡頭
④ 文件：資料上的名字、日期、症狀有沒有矛盾
只要任何一層明顯不對就算「異常」。四層都看過而且都沒問題才算「正常」。
還有哪一層沒看清楚，就回 "看不清"，並在 look 寫要看什麼、x/y 指出它在畫面哪裡。
（注意：Stalker 不要一直盯著看；判斷完就移開視線。）
只輸出 JSON：{"verdict":"正常"或"異常"或"看不清","why":"理由(<=20字)","look":"照片"或"CCTV"或"文件"或"病人"或"","x":0.00或null,"y":0.00或null,%s}""" % COMMON

P_ACCEPT = """【問題】「Check In」（登記／接受病人）按鈕在畫面哪裡？
- 已經登記完成（病人進來了、按鈕消失、出現下一步提示）→ done=1
- 還沒登記 → done=0，並指出 Check In 按鈕的正中央位置
只輸出 JSON：{"done":0或1,"f":0或1(找不到按鈕=0),"x":0.00或null,"y":0.00或null,%s}""" % COMMON

P_REJECT = """【問題】要拒絕這位異常病人，「關快門」（Shutter／拉下鐵捲門）的按鈕、拉桿或提示在畫面哪裡？
- 快門已經關上（窗口被擋住、病人不見了）→ done=1
- 還沒關 → done=0，並指出快門開關的位置；若畫面提示「按住 E」，hold=1
只輸出 JSON：{"done":0或1,"hold":0或1,"f":0或1(找不到=0),"x":0.00或null,"y":0.00或null,%s}""" % COMMON

P_NEXT = """【問題】櫃檯窗口現在有沒有下一位病人？
- 有人站在窗口等 → patient=1
- 出現輪班報告／結算畫面 → report=1（順便把畫面上的輪班數字與金錢寫進 see，例如「輪班 2 $150」）
- 你已經不在櫃檯前（看不到窗口）→ away=1
只輸出 JSON：{"patient":0或1,"report":0或1,"away":0或1,%s}""" % COMMON

P_COFFEE = """【問題】咖啡機在畫面哪裡？（你的理智太低了，要去喝咖啡；咖啡是免費的）
- 已經拿到咖啡或畫面提示可以喝 → ready=1
- 看得到咖啡機 → 指出位置；完全看不到 → f=0
只輸出 JSON：{"ready":0或1,"f":0或1,"x":0.00或null,"y":0.00或null,"far":"遠"或"中"或"近",%s}""" % COMMON

P_DEAD = """【問題】你死了（或這一回合結束了）。畫面上「前往大廳」或「立即開始」的按鈕在哪裡？
⚠「自我復活」要花錢，絕對不准點。
- 畫面已經回到正常遊戲（沒有死亡畫面）→ back=1
只輸出 JSON：{"back":0或1,"btn":"前往大廳"或"立即開始"或"沒有","x":0.00或null,"y":0.00或null,%s}""" % COMMON

FAR_SEC = {"遠": 2.5, "中": 1.2, "近": 0.6}


class CheckinAgent(Agent):
    """櫃檯報到專用：程式跑流程，模型只做視覺判斷。"""

    def __init__(self, args):
        super().__init__(args)
        self.state = "找櫃檯"
        self.tries = 0
        self.san = None
        self.stat_path = os.path.join(self.dir, "櫃檯統計.json")
        self.stat = {"登記": 0, "拒絕": 0, "印資料": 0, "看不清放棄": 0, "回退找櫃檯": 0,
                     "喝咖啡": 0, "死亡": 0, "輪數": 0}
        try:
            with open(self.stat_path, encoding="utf-8") as f:
                self.stat.update({k: v for k, v in json.load(f).items() if isinstance(v, int)})
        except (OSError, json.JSONDecodeError):
            pass
        self.stat["本次開始"] = datetime.now().strftime("%m/%d %H:%M:%S")

    # ---------------- 基本工具
    def sys_prompt(self):
        return SYS.format(game=self.game)

    def ask(self, prompt, max_tokens=160, secs=8.0):
        """送一張清楚畫面 + 一個小問題，回 dict。"""
        uri, _ = jpeg_data_uri(draw_grid(self.grab_still(1024)), 72)
        instr = self._read_instr()
        extra = ("【使用者臨時指示】\n" + instr[-400:] + "\n\n") if instr.startswith("【櫃檯") else ""
        raw, trunc, ttft, _ = stream_json(self.endpoint, self.model, self.sys_prompt(),
                                          [{"type": "image_url", "image_url": {"url": uri}},
                                           {"type": "text", "text": extra + prompt}],
                                          max_tokens, time.time() + secs, 0.1)
        obj = parse_json_loose(raw) or {}
        obj["_截斷"] = trunc
        obj["_首字"] = round(ttft, 2) if ttft else None
        return obj

    def do(self, actions):
        """經過硬規則過濾後執行（每次最多 2 個動作，和 play_agent 一致）。"""
        for i in range(0, len(actions), 2):
            ok, blocked = self.guard(actions[i:i + 2], 0)
            for b in blocked:
                print(f"   🚫 擋掉 {b}")
            self.execute(ok)

    def num(self, v, lo=0.0, hi=1.0):
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return f if lo <= f <= hi else None

    def save_stat(self):
        self.stat["更新"] = datetime.now().strftime("%m/%d %H:%M:%S")
        try:
            with open(self.stat_path, "w", encoding="utf-8") as f:
                json.dump(self.stat, f, ensure_ascii=False, indent=2)
        except OSError:
            pass
        # 讓成績.json 也看得到（儀表板與日報都讀它）
        self.score["CheckIn次數"] = self.stat["登記"]
        self.score["拒絕異常次數"] = self.stat["拒絕"]
        self.score["更新"] = datetime.now().strftime("%m/%d %H:%M:%S")
        try:
            with open(os.path.join(self.dir, "成績.json"), "w", encoding="utf-8") as f:
                json.dump(self.score, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def goto(self, x, y, far, name):
        """對準目標再直走一段（距離由 far 決定）。"""
        self.do([{"t": "aim", "x": x, "y": y, "g": name},
                 {"t": "key", "k": "w", "d": FAR_SEC.get(far, 1.2)}])

    def switch(self, state, why=""):
        if state != self.state:
            print(f"   ➡ {self.state} → {state}" + (f"（{why}）" if why else ""))
            self.state = state
            self.tries = 0

    # ---------------- 共同檢查：死亡、理智
    def common(self, o):
        """回傳 True 代表這一輪已被接管（切到死亡或咖啡狀態）。"""
        self.update_score(str(o.get("see") or ""))
        try:
            if o.get("san") is not None:
                self.san = float(o["san"])
        except (TypeError, ValueError):
            pass
        if o.get("dead") == 1 and self.state != "死亡處理":
            self.stat["死亡"] += 1
            self.switch("死亡處理", "偵測到死亡畫面")
            if time.time() - getattr(self, "last_death", 0) > 180:
                self.last_death = time.time()
                self.death_count = getattr(self, "death_count", 0) + 1
                try:
                    self.death_review()
                except Exception as e:  # noqa: BLE001
                    print(f"   死亡檢討失敗：{e}")
            return True
        if self.san is not None and self.san <= 40 and self.state not in ("喝咖啡", "死亡處理"):
            self.switch("喝咖啡", f"理智只剩 {self.san:.0f}%")
            return True
        return False

    # ---------------- 各狀態
    def s_找櫃檯(self):
        o = self.ask(P_DESK)
        if self.common(o):
            return o
        if o.get("at") == 1:
            self.switch("印資料", "已經在櫃檯前")
            return o
        near, tex = self.wall_score()
        if near:
            self.turn_around(f"畫面紋理只有 {tex:.0f}，臉貼牆")
            return o
        x, y = self.num(o.get("x")), self.num(o.get("y"))
        if x is None or y is None:
            self.do([{"t": "look", "dx": 220, "dy": 0}])     # 掃視找櫃檯／黃色箭頭
        else:
            self.goto(x, y, o.get("far"), "櫃檯")
        self.tries += 1
        if self.tries == 8:
            self.emergency_think("找了很久還沒走到櫃檯，我可能卡住或方向錯了")
        if self.tries >= 12:
            self.force_respawn()      # 程式端脫困走位
            self.tries = 0
        return o

    def s_印資料(self):
        o = self.ask(P_PRINT)
        if self.common(o):
            return o
        if o.get("none") == 1:
            self.switch("等下一位", "窗口沒有病人")
            return o
        if o.get("done") == 1:
            self.stat["印資料"] += 1
            self.switch("核對", "資料已印出")
            return o
        x, y = self.num(o.get("x")), self.num(o.get("y"))
        if x is not None and y is not None:
            self.do([{"t": "aim", "x": x, "y": y, "g": "印表機"}])
        self.do([{"t": "hold", "k": "e", "g": "印表機"}])
        self.tries += 1
        if self.tries >= 5:
            self.stat["回退找櫃檯"] += 1
            self.switch("找櫃檯", "印不出來，重新走位到櫃檯")
        return o

    def s_核對(self):
        o = self.ask(P_INSPECT, max_tokens=200, secs=10.0)
        if self.common(o):
            return o
        v = str(o.get("verdict") or "")
        why = str(o.get("why") or "")
        if "異常" in v:
            print(f"   ❗判定異常：{why}")
            self.add_note(f"（核對）判定異常：{why}")
            self.switch("拒絕", why)
            return o
        if "正常" in v:
            print(f"   ✅判定正常：{why}")
            self.switch("登記", why)
            return o
        # 看不清：把視線移到還沒看的那一層
        x, y = self.num(o.get("x")), self.num(o.get("y"))
        look = str(o.get("look") or "")
        if x is not None and y is not None and look != "病人":
            self.do([{"t": "aim", "x": x, "y": y, "g": look or "要看的東西"}])
        else:
            self.do([{"t": "wait", "d": 0.6}])
        self.tries += 1
        if self.tries >= 4:
            self.stat["看不清放棄"] += 1
            print("   ⚠ 四層核對看不清楚，保守處理：關快門拒絕（寧可少賺，不要放異常進來）")
            self.switch("拒絕", "看不清，保守拒絕")
        return o

    def s_登記(self):
        o = self.ask(P_ACCEPT)
        if self.common(o):
            return o
        if o.get("done") == 1:
            self.stat["登記"] += 1
            self.save_stat()
            print(f"   🎉 完成第 {self.stat['登記']} 次 Check In")
            self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 完成 Check In（累計 {self.stat['登記']} 次）")
            self.switch("等下一位", "登記完成")
            return o
        x, y = self.num(o.get("x")), self.num(o.get("y"))
        if o.get("f") == 0 or x is None or y is None:
            loc = self.locate("Check In 登記按鈕")
            if loc:
                x, y = loc
        if x is not None and y is not None:
            self.do([{"t": "click", "x": x, "y": y, "g": "Check In 登記按鈕"}])
        else:
            self.do([{"t": "hold", "k": "e", "g": "櫃檯登記"}])
        self.tries += 1
        if self.tries >= 5:
            self.stat["回退找櫃檯"] += 1
            self.switch("找櫃檯", "按不到 Check In")
        return o

    def s_拒絕(self):
        o = self.ask(P_REJECT)
        if self.common(o):
            return o
        if o.get("done") == 1:
            self.stat["拒絕"] += 1
            self.save_stat()
            print(f"   🛑 已關快門拒絕異常（累計 {self.stat['拒絕']} 次）")
            self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 關快門拒絕異常病人（累計 {self.stat['拒絕']} 次）")
            self.switch("等下一位", "已關快門")
            return o
        x, y = self.num(o.get("x")), self.num(o.get("y"))
        if o.get("f") == 0 or x is None or y is None:
            loc = self.locate("關快門的開關或拉桿（Shutter）")
            if loc:
                x, y = loc
        if x is not None and y is not None:
            self.do([{"t": "aim", "x": x, "y": y, "g": "快門開關"}])
        if o.get("hold") == 1 or x is None:
            self.do([{"t": "hold", "k": "e", "g": "關快門"}])
        else:
            self.do([{"t": "click", "x": x, "y": y, "g": "快門開關"}])
        self.tries += 1
        if self.tries >= 5:
            self.stat["回退找櫃檯"] += 1
            self.switch("找櫃檯", "關不到快門")
        return o

    def s_等下一位(self):
        o = self.ask(P_NEXT)
        if self.common(o):
            return o
        if o.get("report") == 1:
            print(f"   📋 輪班報告：{o.get('see')}")
            self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 輪班報告：{o.get('see')}")
            self.switch("找櫃檯", "輪班結束，回櫃檯")
            return o
        if o.get("patient") == 1:
            self.switch("印資料", "下一位病人到了")
            return o
        if o.get("away") == 1:
            self.switch("找櫃檯", "已經不在櫃檯前")
            return o
        self.do([{"t": "wait", "d": 1.0}])
        self.tries += 1
        if self.tries >= 15:
            self.switch("找櫃檯", "等太久，回頭確認位置")
        return o

    def s_喝咖啡(self):
        o = self.ask(P_COFFEE)
        if o.get("dead") == 1:
            self.common(o)
            return o
        self.update_score(str(o.get("see") or ""))
        try:
            if o.get("san") is not None:
                self.san = float(o["san"])
        except (TypeError, ValueError):
            pass
        if self.san is not None and self.san >= 66:
            self.stat["喝咖啡"] += 1
            self.switch("找櫃檯", f"理智回到 {self.san:.0f}%")
            return o
        if o.get("ready") == 1:
            self.do([{"t": "hold", "k": "e", "g": "喝咖啡"}])
            self.tries += 1
            return o
        x, y = self.num(o.get("x")), self.num(o.get("y"))
        if o.get("f") == 0 or x is None or y is None:
            self.do([{"t": "look", "dx": 220, "dy": 0}])
        else:
            self.goto(x, y, o.get("far"), "咖啡機")
            self.do([{"t": "hold", "k": "e", "g": "咖啡機"}])
        self.tries += 1
        if self.tries >= 12:
            self.stat["喝咖啡"] += 1
            self.switch("找櫃檯", "喝不到咖啡，先回去做登記")
        return o

    def s_死亡處理(self):
        o = self.ask(P_DEAD)
        self.update_score(str(o.get("see") or ""))
        if o.get("back") == 1 or (o.get("dead") == 0 and o.get("btn") == "沒有"):
            self.san = None
            self.switch("找櫃檯", "回到遊戲")
            return o
        x, y = self.num(o.get("x")), self.num(o.get("y"))
        btn = str(o.get("btn") or "")
        if x is not None and y is not None and btn in ("前往大廳", "立即開始"):
            self.do([{"t": "click", "x": x, "y": y, "g": btn}])
        else:
            self.do([{"t": "wait", "d": 1.0}])
        self.tries += 1
        if self.tries >= 10:
            self.force_leave()      # 離開重進
            self.switch("找櫃檯", "死亡畫面卡住，離開重進")
        return o

    # ---------------- 一輪
    def cycle(self):
        t0 = time.time()
        handler = getattr(self, "s_" + self.state, None)
        if handler is None:
            self.state = "找櫃檯"
            handler = self.s_找櫃檯
        state_before = self.state
        o = handler() or {}
        self.stat["輪數"] += 1
        rec = {"時間": datetime.now().strftime("%H:%M:%S"), "模式": f"櫃檯:{state_before}",
               "看到": o.get("see"), "理智": self.san, "判定": o.get("verdict"),
               "下一狀態": self.state, "嘗試": self.tries,
               "登記": self.stat["登記"], "拒絕": self.stat["拒絕"],
               "截斷": o.get("_截斷"), "首字秒": o.get("_首字"), "秒": round(time.time() - t0, 2)}
        self.log(rec)
        print(f"[{rec['時間']}] {state_before} {rec['秒']}s · 看到：{o.get('see')}"
              + (f" · 理智 {self.san:.0f}%" if self.san is not None else "")
              + f" · 登記 {self.stat['登記']}／拒絕 {self.stat['拒絕']}")
        self.history.append(f"[{rec['時間']}] {state_before}：{o.get('see')} → {self.state}")
        self.history = self.history[-40:]
        if self.stat["輪數"] % 20 == 0:
            self.save_stat()

    def run(self):
        print(f"[checkin_agent] 只做櫃檯 Check In。遊戲「{self.game}」 模型 {self.model} "
              f"起始累計：登記 {self.stat['登記']}、拒絕 {self.stat['拒絕']}")
        try:
            super().run()
        finally:
            self.save_stat()
            print(f"[櫃檯統計] 登記 {self.stat['登記']}、拒絕 {self.stat['拒絕']}、印資料 {self.stat['印資料']}、"
                  f"看不清放棄 {self.stat['看不清放棄']}、回退 {self.stat['回退找櫃檯']}、死亡 {self.stat['死亡']}"
                  f" → {self.stat_path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", default="動物醫院")
    ap.add_argument("--hours", type=float, default=8.0)
    ap.add_argument("--deadline", type=float, default=10.0)
    ap.add_argument("--max-tokens", type=int, default=180)
    ap.add_argument("--reflect-min", type=float, default=15.0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    log_dir = os.path.join(HERE, "資料", args.game)
    os.makedirs(log_dir, exist_ok=True)
    sys.stdout = Tee(os.path.join(log_dir, "agent.log"), sys.stdout)
    sys.stderr = Tee(os.path.join(log_dir, "agent.log"), sys.stderr)
    print(f"\n===== {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} 啟動（櫃檯 Check In 模式）=====")
    CheckinAgent(args).run()


if __name__ == "__main__":
    main()
