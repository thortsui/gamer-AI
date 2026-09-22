#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════════╗
║  ⚠ 舊架構，已淘汰（2026-09-11 標記）——保留作為紀錄，不要再改也不要再跑  ║
╚══════════════════════════════════════════════════════════════════════╝

現在的主力是 `flow_agent.py`（工作流程模式）。這個檔案是它的前身，留著是為了
保存當時的做法與踩過的坑，**不是**現在跑的東西。

為什麼被取代：技能狀態機（走到／報到／喝咖啡…各包成一個技能）在實戰中太僵硬：遊戲的狀況比技能的前提多，常常卡在「沒有任何技能適用」。flow_agent 改成只給他文件與畫面、步驟由他自己走，彈性好很多。

要跑代理請用：
    python flow_agent.py --game 動物醫院 --hours 12 --deadline 10         --lock-flow 報到,治療,開始上班,找人一起玩 --map off
原本的說明如下
------------------------------------------------------------

skill_agent.py — 三層代理：大腦（DGX）／眼睛（本機）／技能（程式）

  大腦 = DGX 上的 Qwen3.8-Flash-Next
         負責「現在做哪一件事、去哪個目標、往哪個方向」，以及所有語意判斷
         （這個病人正不正常、按鈕在哪、症狀是什麼）。
         ⚠ 它只看得到技能的名字和一句用途，看不到任何步驟細節。
  眼睛 = 本機 RTX 4060 上的 Grounding DINO tiny，每秒 4~5 次
         只回答「東西在畫面哪裡、多大」，讓「走到人旁邊並停下來」變成即時閉環，
         不必每一步都問大腦，也就不會一直卡牆。
  技能 = skills.py 裡一個一個獨立的小程式（走到／報到／喝咖啡／治療／脫困／處理死亡）。

用法：
  python skill_agent.py --game 動物醫院 --hours 8
  python skill_agent.py --game 動物醫院 --dry-run          # 只想不動
  python skill_agent.py --game 動物醫院 --skill 報到        # 直接指定第一個技能
停止：專案夾放 stop.txt / Ctrl+F12 / 滑鼠甩左上角
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pydirectinput  # noqa: E402
from play_agent import (Agent, Tee, HERE, stream_json, parse_json_loose,  # noqa: E402
                        draw_grid, jpeg_data_uri, MAX_KEY_HOLD)
from local_vision import Eyes, to_en  # noqa: E402
import skills as SK  # noqa: E402

SYS = """你是 gamer AI，在玩 Roblox 遊戲「{game}」（Animal Anomaly，一間會出現異常病人的動物醫院）。
你的工作是幫櫃檯窗口的病人做報到（Check In）賺錢。

你不用自己想「怎麼操作鍵盤滑鼠」。你只要決定「現在做哪一件事、對哪個目標、往哪個方向」，
程式會用你選的方法替你完成，完成或失敗都會回報給你。

畫面上淡黃色格線與 0.1~0.9 的數字是程式疊的座標尺（左上 0,0、右下 1,1），報位置用它。
鐵律：不准花任何錢（Robux、金幣、購買、解鎖、升級、自我復活都不准點）。
只輸出一個 JSON，不要 markdown、不要多餘文字。"""

KEEP = """【只回答這一題】你現在正在做的是「{skill}」（已經做了 {secs} 秒）。看畫面，這件事還值得繼續嗎？
- 還在正軌上 → keep=1
- 情況變了、卡住了、有更該做的事（例如出現病人、快死了、跑錯地方）→ keep=0
只輸出 JSON：{{"keep":0或1,"why":"理由(<=15字)","see":"畫面重點(<=20字)"}}"""

SELECT = """【現在要決定的事】接下來做哪一件？

你會做的事（選一個。這些只是「大概的工作流程」，幫你知道自己在幹嘛，不是硬規定；
覺得都不合適，就選「自己來」自己動手，或選「想一想」先想清楚）：
{skills}

可以指定的目標：a person（人／病人）、a counter desk（櫃檯）、a coffee machine（咖啡機）、
a printer（印表機）、a door（門）。

【剛才發生什麼】
{last}

【你的眼睛現在看到】（本機視覺每秒看 5 次，括號是畫面位置與大小）
{eyes}

【你自己上次想出來的計畫】
{plan}

【使用者交代】（隨時可能更新，優先照做）
{instr}

【成績】完成報到 {checkin} 次、拒絕異常 {reject} 次、治療 {treat} 次

只輸出 JSON：{{"skill":"技能名","target":"目標或空字串","dir":"左"或"右"或"前"或"後"或空字串,"x":0.00或null,"y":0.00或null,"why":"為什麼(<=15字)"}}
（x,y 是你要去或要看的方向在畫面上的位置；不知道就填 null）"""


class SkillAgent(Agent):
    def __init__(self, args):
        super().__init__(args)
        self.eyes = Eyes(bbox_fn=self.win.client_bbox, fps=args.vision_fps, short_edge=args.vision_size)
        self.eyes.watch(["a person", "a counter desk", "a door"])
        self.eyes.start()
        self.skills = {}
        for cls in SK.ALL:
            s = cls(self)
            self.skills[s.name] = s
        self.load_sensitivity()
        self.cur = None
        self.hint = {}
        self.cooldown = {}      # 技能名 -> 冷卻到什麼時候（連續失敗就先擱著，避免空轉）
        self.fail_streak = {}
        self.san = None
        self.last_result = "（還沒開始）"
        self.last_scan = {}
        self.stat_path = os.path.join(self.dir, "技能統計.json")
        self.stat = {"登記": 0, "拒絕": 0, "治療": 0, "喝咖啡": 0, "技能完成": 0, "技能失敗": 0}
        try:
            with open(self.stat_path, encoding="utf-8") as f:
                self.stat.update({k: v for k, v in json.load(f).items() if isinstance(v, int)})
        except (OSError, json.JSONDecodeError):
            pass
        self.per_skill = {}

    # ---------------- 給技能用的小工具
    def en(self, label):
        return to_en(label)

    def ppp(self):
        """轉視角靈敏度：拖幾個像素 ≈ 畫面轉 1%。名字不能叫 px_per_pct（會蓋掉父類別的屬性）。"""
        return getattr(self, "px_per_pct", None) or 26.0

    def load_sensitivity(self):
        """從視角校準.json 的實測樣本算中位靈敏度。程式原本的預設值 12 太小，對準都會不夠。"""
        import statistics as st
        try:
            with open(os.path.join(self.dir, "視角校準.json"), encoding="utf-8") as f:
                rs = [x["dx"] / x["畫面轉%"] for x in json.load(f).get("樣本", []) if x.get("畫面轉%", 0) >= 3]
            if len(rs) >= 5:
                self.px_per_pct = round(st.median(rs), 1)
                print(f"[校準] 實測靈敏度：拖 {self.px_per_pct} 像素 ≈ 畫面轉 1%（{len(rs)} 個樣本）")
                return
        except (OSError, json.JSONDecodeError, ZeroDivisionError, KeyError):
            pass
        self.px_per_pct = 26.0
        print("[校準] 沒有可用樣本，用預設 26 像素/1%")

    def xy(self, o):
        try:
            x, y = float(o.get("x")), float(o.get("y"))
        except (TypeError, ValueError, AttributeError):
            return None, None
        return (x, y) if 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0 else (None, None)

    def look(self, dx, dy=0):
        if self.dry or not self.win.activate():
            return
        self.raw_look(int(max(-800, min(800, dx))), int(max(-200, min(400, dy))), steps=6)
        self.pitch = getattr(self, "pitch", 0) + int(dy)

    def press(self, k, d=0.3):
        if self.dry or not self.win.activate():
            return
        d = max(0.05, min(MAX_KEY_HOLD, float(d)))
        pydirectinput.keyDown(k)
        time.sleep(d)
        pydirectinput.keyUp(k)

    def level_camera(self, tries=3):
        """把鏡頭轉回水平：看地板或看天花板時，遊戲裡什麼都判斷不了。"""
        for _ in range(tries):
            v = self.view_state()
            if "偏上" in v:
                self.look(0, 160)
            elif "偏下" in v:
                self.look(0, -140)
            else:
                self.pitch = 0
                return True
            time.sleep(0.2)
        self.pitch = 0
        return False

    def aim_to(self, x, y, g=""):
        if self.dry:
            return
        self.execute([{"t": "aim", "x": x, "y": y, "g": g}])

    def click(self, x, y, g=""):
        ok, blocked = self.guard([{"t": "click", "x": x, "y": y, "g": g}], 0)
        for b in blocked:
            print(f"   🚫 擋掉 {b}")
        self.execute(ok)

    def do(self, actions):
        """執行一串動作（先過硬規則）。給「自己來」這種自由發揮的技能用。"""
        for i in range(0, len(actions), 2):
            ok, blocked = self.guard(actions[i:i + 2], 0)
            for b in blocked:
                print(f"   🚫 擋掉 {b}")
            self.execute(ok)

    def hold_e(self, g=""):
        self.execute([{"t": "hold", "k": "e", "g": g}])

    def hold_e_raw(self, g=""):
        if self.dry or not self.win.activate():
            return False, 0.0
        return self.hold_interact("e")

    def note(self, text):
        self.add_note(text)

    def stat_add(self, key, n=1):
        self.stat[key] = self.stat.get(key, 0) + n
        self.save_stat()

    def save_stat(self):
        try:
            with open(self.stat_path, "w", encoding="utf-8") as f:
                json.dump({**self.stat, "更新": datetime.now().strftime("%m/%d %H:%M:%S"),
                           "各技能": self.per_skill}, f, ensure_ascii=False, indent=2)
        except OSError:
            pass
        self.score["CheckIn次數"] = self.stat.get("登記", 0)
        self.score["拒絕異常次數"] = self.stat.get("拒絕", 0)
        try:
            with open(os.path.join(self.dir, "成績.json"), "w", encoding="utf-8") as f:
                json.dump(self.score, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    # ---------------- 問大腦（DGX）
    def sys_prompt(self):
        return SYS.format(game=self.game)

    def draw_eyes(self, im):
        """把本機偵測器框到的東西畫在圖上（青色框＋標籤），當作千問的輔助標註。
        千問還是自己看畫面做判斷，偵測器只是幫他每秒看好幾次、把東西圈出來。"""
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
                d.rectangle((x0, y0, x1, y1), outline=(0, 255, 255), width=3)
                d.text((x0 + 4, max(0, y0 - 12)), f"{lab} {det['score']:.2f}", fill=(0, 255, 255))
        return im

    def brain(self, prompt, max_tokens=140, secs=8.0):
        """送一張目前畫面（含偵測器框出來的輔助標註）+ 一個小問題給 DGX，回 dict。"""
        im = self.draw_eyes(draw_grid(self.grab_still(1024)))
        eyes_line = self.eyes_text()
        if eyes_line and "沒看到" not in eyes_line and "暖機" not in eyes_line:
            prompt = ("【輔助標註】本機偵測器每秒看 5 次，圖上青色框就是它框到的東西："
                      + eyes_line + "。框只是提示，最後還是你自己看畫面判斷。\n\n" + prompt)
        uri, _ = jpeg_data_uri(im, 72)
        raw, trunc, ttft, _ = stream_json(self.endpoint, self.model, self.sys_prompt(),
                                          [{"type": "image_url", "image_url": {"url": uri}},
                                           {"type": "text", "text": prompt}],
                                          max_tokens, time.time() + secs, 0.1)
        o = parse_json_loose(raw) or {}
        see = str(o.get("see") or "")
        if see:
            self.update_score(see)
            if any(k in see for k in ("我死了", "死亡", "倒地", "理智0", "理智 0", "前往大廳")):
                self._death_flag = True
        return o

    def eyes_text(self):
        dets, stamp = self.eyes.latest()
        if not dets:
            return "（什麼都沒看到）" if stamp else "（眼睛還在暖機）"
        return "、".join(f"{k}在({v[0]['cx']:.2f},{v[0]['cy']:.2f})大小{v[0]['h']:.2f}"
                         for k, v in dets.items() if v) or "（什麼都沒看到）"

    def choose(self):
        """讓大腦選一個技能（它只看得到名字和一句用途）。"""
        lines = "\n".join(f"- {s.name}：{s.purpose}" for s in self.skills.values())
        instr = (self._read_instr() or "").strip()
        instr = instr[-300:] if instr else "（沒有特別交代）"
        plan = (getattr(self, "plan", "") or "（還沒有）").strip()
        o = self.brain(SELECT.format(skills=lines, last=self.last_result, eyes=self.eyes_text(),
                                     instr=instr, plan=plan,
                                     checkin=self.stat.get("登記", 0), reject=self.stat.get("拒絕", 0),
                                     treat=self.stat.get("治療", 0)),
                       max_tokens=160, secs=9.0)
        name = str(o.get("skill") or "").strip()
        if name not in self.skills:
            name = next((k for k in self.skills if k and k in str(o.get("skill") or "")), "看看四周")
        if time.time() < self.cooldown.get(name, 0):
            alt = "走到" if name != "走到" else "看看四周"
            print(f"   ⏳「{name}」剛連續失敗還在冷卻，改用「{alt}」")
            name = alt
        self.hint = {k: o.get(k) for k in ("dir", "x", "y")}
        target = str(o.get("target") or "").strip()
        why = str(o.get("why") or "")
        print(f"\n🧠 大腦選：{name}" + (f"（{target}）" if target else "") + f" — {why}")
        s = self.skills[name]
        s.begin(target)
        self.cur = s
        self.log({"時間": datetime.now().strftime("%H:%M:%S"), "模式": "選技能", "技能": name,
                  "目標": target, "方向": o.get("dir"), "理由": why, "眼睛": self.eyes_text()})

    # ---------------- 一輪
    def cycle(self):
        # 程式端的兩個保險：死亡、貼牆
        if getattr(self, "_death_flag", False) and (not self.cur or self.cur.name != "處理死亡"):
            self._death_flag = False
            print("   💀 偵測到死亡畫面，切到「處理死亡」")
            if time.time() - getattr(self, "last_death", 0) > 180:
                self.last_death = time.time()
                self.death_count = getattr(self, "death_count", 0) + 1
                try:
                    self.death_review()
                except Exception as e:  # noqa: BLE001
                    print(f"   死亡檢討失敗：{e}")
            self.cur = self.skills["處理死亡"]
            self.cur.begin("")
        if self.cur is None:
            near, tex = self.wall_score()
            if near:
                print(f"   🧱 紋理只有 {tex:.0f}，臉貼牆 → 先脫困")
                self.cur = self.skills["脫困"]
                self.cur.begin("")
            else:
                self.choose()
            return
        t0 = time.time()
        # （2026-09-07：拿掉「做到一半問要不要繼續」的中止機制——它會一直打斷自己，什麼都做不完）
        try:
            r = self.cur.tick()
        except Exception as e:  # noqa: BLE001
            import traceback
            print(f"   技能出錯：{type(e).__name__}: {e}")
            print(traceback.format_exc())
            r = f"失敗:程式錯誤 {type(e).__name__}"
        if r:
            name = self.cur.name
            ok = r == "完成"
            self.stat["技能完成" if ok else "技能失敗"] += 1
            force_think = False
            if ok:
                self.fail_streak[name] = 0
            else:
                self.fail_streak[name] = self.fail_streak.get(name, 0) + 1
                if self.fail_streak[name] >= 2:
                    self.cooldown[name] = time.time() + 45
                    print(f"   ⏳「{name}」連續失敗 {self.fail_streak[name]} 次，45 秒內不再選它")
                force_think = self.fail_streak[name] >= 3 and name != "想一想"
            d = self.per_skill.setdefault(name, {"完成": 0, "失敗": 0})
            d["完成" if ok else "失敗"] += 1
            secs = round(self.cur.age, 1)
            self.last_result = f"你剛用「{name}」{'成功了' if ok else r}（花了 {secs} 秒）。{self.cur.note}"
            print(f"   {'✅' if ok else '❌'} {name}：{r}（{secs} 秒）")
            self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] {name} → {r}")
            self.history = self.history[-30:]
            self.log({"時間": datetime.now().strftime("%H:%M:%S"), "模式": f"技能:{name}",
                      "結果": r, "秒": secs, "備註": self.cur.note,
                      "登記": self.stat.get("登記", 0), "拒絕": self.stat.get("拒絕", 0)})
            self.save_stat()
            self.cur = None
            if force_think:                     # 一直失敗 → 強制它自己停下來想
                self.fail_streak[name] = 0
                print("   🤔 連續失敗，強制停下來想一想")
                self.cur = self.skills["想一想"]
                self.cur.begin("")
        else:
            time.sleep(max(0.0, 0.20 - (time.time() - t0)))     # 跟眼睛的節奏對齊

    def wait_for_roblox(self):
        """Roblox 視窗還沒出現就先等（例如遊戲當掉、還沒開）。每 30 秒報一次。"""
        if self.win.client_bbox():
            return
        print("⏸ 找不到 Roblox 視窗（遊戲沒開或當掉了）。等它出現就會自己開始……")
        n = 0
        while not self.stop.is_set() and not os.path.exists(os.path.join(HERE, "stop.txt")):
            time.sleep(5)
            self.win.hwnd = None
            if self.win.client_bbox():
                print(f"▶ 看到 Roblox 視窗了（等了 {n * 5} 秒），開始工作")
                time.sleep(2)
                return
            n += 1
            if n % 6 == 0:
                print(f"   還在等 Roblox…（{n * 5} 秒）")

    def run(self):
        print(f"[skill_agent] 大腦={self.model}（DGX）　眼睛=本機 Grounding DINO　"
              f"技能={'、'.join(self.skills)}")
        print(f"   起始累計：登記 {self.stat.get('登記', 0)}、拒絕 {self.stat.get('拒絕', 0)}")
        self.wait_for_roblox()
        if self.args.skill and self.args.skill in self.skills:
            self.cur = self.skills[self.args.skill]
            self.cur.begin(self.args.target or "")
            print(f"   指定第一個技能：{self.args.skill}")
        try:
            super().run()
        finally:
            self.eyes.stop()
            self.save_stat()
            print(f"[技能統計] 登記 {self.stat.get('登記', 0)}、拒絕 {self.stat.get('拒絕', 0)}、"
                  f"治療 {self.stat.get('治療', 0)}、技能完成 {self.stat.get('技能完成', 0)}／"
                  f"失敗 {self.stat.get('技能失敗', 0)} → {self.stat_path}")
            for k, v in self.per_skill.items():
                print(f"   {k}: 完成 {v['完成']}／失敗 {v['失敗']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", default="動物醫院")
    ap.add_argument("--hours", type=float, default=8.0)
    ap.add_argument("--deadline", type=float, default=10.0)
    ap.add_argument("--max-tokens", type=int, default=180)
    ap.add_argument("--reflect-min", type=float, default=20.0)
    ap.add_argument("--vision-fps", type=float, default=5.0)
    ap.add_argument("--vision-size", type=int, default=480, help="丟給偵測器的短邊像素（越小越快）")
    ap.add_argument("--skill", default="", help="直接指定第一個技能")
    ap.add_argument("--target", default="")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    log_dir = os.path.join(HERE, "資料", args.game)
    os.makedirs(log_dir, exist_ok=True)
    sys.stdout = Tee(os.path.join(log_dir, "agent.log"), sys.stdout)
    sys.stderr = Tee(os.path.join(log_dir, "agent.log"), sys.stderr)
    print(f"\n===== {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} 啟動（技能模式：大腦選技能＋本機眼睛閉環）=====")
    SkillAgent(args).run()


if __name__ == "__main__":
    main()
