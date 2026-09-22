"""procedure.py — 程序化執行（2026-09-15，使用者定的方向）

目標：**學習期**讓大語言模型把大步驟、小步驟都寫成「程式能直接執行、能自己驗證」的步驟；
**執行期**只靠腦內地圖＋水平儀＋視覺模型純算——這一輪要轉幾度、走幾秒、對準什麼、按什麼，
不呼叫大語言模型。只有「特殊狀況」（一步連續失敗、狀態不認得）才回去問它，並讓它修程序。

步驟語言（DSL）——每一種都有程式看得到的成功判準：
  {"做":"走到地點","地點":"櫃檯工作位"}                 用腦內地圖：轉向→走幾秒；到了＝距離 < 0.8
  {"做":"走近","物件":"counter desk"}                    用 YOLO 閉環走過去；到了＝框高 ≥ 0.45
  {"做":"走向字","字":"AH1|ANIMAL","秒":1.5}              OCR 讀到那些字（任一）就把它轉到畫面中央、直走；到了＝字變大或消失在前方
  {"做":"面向","方位":130}                               水平儀：轉到那個方位；到了＝差 < 8 度
  {"做":"對準","物件":"computer monitor"}                YOLO：把框轉到畫面中央；到了＝|cx-0.5| < 0.08
  {"做":"修水平"}                                        水平儀：dy = 俯仰 × 每度dy；到了＝|俯仰| < 10
  {"做":"互動","鍵":"e","秒":1.5,"需要提示":true}         有 E 提示才按；成功＝遊戲有反應
  {"做":"點","位置":[0.91,0.21],"文字":"蓋印表格"}        畫面上有那個字才點；成功＝畫面有變化
  {"做":"按鍵","鍵":"w","秒":1.0}                         純按，不驗
  {"做":"等待","條件":"任務字變|E提示|畫面變","秒":8}      輪詢；時間內出現＝成功
  {"做":"檢查","條件":"E提示|任務字含:xxx|框到:cat"}      當下檢查，不等
  {"做":"我在","地點":"櫃檯工作位"}                       用已知點校正推算座標
  {"做":"呼叫","程序":"喝咖啡"}                            子程序（中任務串成大任務）
每一步可加 "重試": 次數（預設 2）、"備註"、"除非": 條件（已成立就略過這步）、"可略過": true（前提不在就略過不算失敗）。
程序存在 記憶/程序/<名>.json。
"""
import json
import math
import os
import time
from datetime import datetime

NL = "\n"
STEP_KINDS = ("走到地點", "走近", "走向字", "面向", "對準", "修水平", "互動", "點", "按鍵", "等待", "檢查", "我在", "呼叫")


class ProcedureMixin:
    PROC_MAX_RETRY = 2          # 一步最多重試幾次，超過就算特殊狀況交給大模型
    PROC_ESCALATE_ROUNDS = 6    # 交給大模型之後至少讓它接手幾輪
    PROC_STEP_PAUSE = 0.4

    # ---------------- 存取
    def proc_dir(self):
        d = os.path.join(self.dir, "程序")
        os.makedirs(d, exist_ok=True)
        return d

    def load_procs(self):
        self.procs = {}
        try:
            for f in sorted(os.listdir(self.proc_dir())):
                if f.endswith(".json"):
                    with open(os.path.join(self.proc_dir(), f), encoding="utf-8") as fh:
                        d = json.load(fh)
                    if isinstance(d, dict) and d.get("步驟"):
                        self.procs[d.get("名") or f[:-5]] = d
        except Exception as e:  # noqa: BLE001
            print(f"   讀程序失敗：{e}")
        self.proc = None
        self._proc_escalated_until = 0      # 計數器（程式輪／模型輪／特殊狀況）都用 stat.get 帶預設，這裡不碰 stat
        if self.procs:
            print("[程序] 已有程式化步驟：" + "、".join(
                f"{k}({len(v['步驟'])}步 ✓{v.get('成功', 0)}/✗{v.get('失敗', 0)})" for k, v in self.procs.items()))
        else:
            print("[程序] 還沒有程式化步驟——學習期會請大模型把步驟編譯成程序")

    def save_proc(self, d):
        d["更新"] = datetime.now().strftime("%m/%d %H:%M")
        self.procs[d["名"]] = d
        with open(os.path.join(self.proc_dir(), d["名"] + ".json"), "w", encoding="utf-8") as fh:
            json.dump(d, fh, ensure_ascii=False, indent=1)

    def validate_steps(self, steps):
        """只收程式做得到、驗得了的步驟。回 (清理過的步驟, 被退回的原因列表)。"""
        ok, bad = [], []
        for i, st in enumerate(steps or []):
            if not isinstance(st, dict):
                bad.append(f"第{i + 1}步不是物件"); continue
            k = str(st.get("做") or st.get("t") or "").strip()
            if k not in STEP_KINDS:
                bad.append(f"第{i + 1}步「{k}」不是程式認得的動作（只認：{'、'.join(STEP_KINDS)}）"); continue
            need = {"走到地點": "地點", "走近": "物件", "走向字": "字", "面向": "方位", "對準": "物件", "互動": None,
                    "點": "位置", "按鍵": "鍵", "等待": "條件", "檢查": "條件", "我在": "地點", "呼叫": "程序",
                    "修水平": None}[k]
            if need and st.get(need) in (None, ""):
                bad.append(f"第{i + 1}步「{k}」缺「{need}」"); continue
            if k == "走到地點" and st["地點"] not in (getattr(self, "map", {}) or {}) \
                    and st["地點"] not in (getattr(self, "spots", {}) or {}) \
                    and st["地點"] not in (getattr(self, "stands", {}) or {}):
                bad.append(f"第{i + 1}步「走到地點」的「{st['地點']}」不在腦中地圖／站位／互動點裡"); continue
            if k == "點":
                try:
                    x, y = float(st["位置"][0]), float(st["位置"][1])
                    st["位置"] = [round(x, 3), round(y, 3)]
                except Exception:  # noqa: BLE001
                    bad.append(f"第{i + 1}步「點」的位置要是 [x, y]"); continue
            st["做"] = k
            ok.append(st)
        return ok, bad

    # ---------------- 什麼時候用程式跑
    def exec_mode_ready(self):
        if time.time() < getattr(self, "_proc_escalated_until", 0):
            return False
        if getattr(self, "desk_lock", 0) >= 2:
            return False                       # 櫃檯作業中鏡頭鎖住，程序還沒設計這種狀態
        try:
            if self.in_lobby() and "開始上班" not in self.procs:
                return False
        except Exception:  # noqa: BLE001
            pass
        if self.proc:
            return True
        name = self.proc_pick()
        if not name:
            return False
        self.proc_start(name, why="現在專注的中任務有程序")
        return True

    def proc_pick(self):
        """挑現在該跑的程序：在大廳→開始上班；理智低→喝咖啡；否則任務樹現在專注的中任務。"""
        try:
            if self.in_lobby() and "開始上班" in self.procs:
                return "開始上班"
        except Exception:  # noqa: BLE001
            pass
        san = getattr(self, "san", None)
        if san is not None and san <= 20 and "喝咖啡" in self.procs:
            return "喝咖啡"
        focus = (getattr(self, "tree", None) or {}).get("現在專注") or ""
        return focus if focus in self.procs else None

    def proc_start(self, name, why=""):
        self.proc = {"名": name, "i": 0, "重試": 0, "開始": time.time(), "步數": len(self.procs[name]["步驟"])}
        print(f"🤖 程式接手「{name}」（{self.proc['步數']} 步）{('：' + why) if why else ''}——這段不呼叫大模型")

    def proc_stop(self, why=""):
        if self.proc:
            print(f"🤖 程式放下「{self.proc['名']}」{('：' + why) if why else ''}")
        self.proc = None

    # ---------------- 一輪：做一步、驗一步
    def exec_round(self):
        p = self.proc
        d = self.procs.get(p["名"])
        if not d:
            self.proc_stop("程序不見了"); return
        if p["i"] >= len(d["步驟"]):
            d["成功"] = int(d.get("成功", 0)) + 1
            self.save_proc(d)
            print(f"🤖✅ 「{p['名']}」全部 {p['步數']} 步做完（累計成功 {d['成功']} 次）")
            try:
                self.award("做成自己訂的任務", f"程式照你寫的程序把「{p['名']}」從頭做到尾。")
            except Exception:  # noqa: BLE001
                pass
            self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 程式照程序做完「{p['名']}」")
            self.proc = None
            return
        st = d["步驟"][p["i"]]
        self.stat["程式輪"] = self.stat.get("程式輪", 0) + 1
        t0 = time.time()
        try:
            unless = st.get("除非")
            if unless:
                from play_agent import frame_diff
                if self.check_cond(str(unless), str(getattr(self, "cur_task", "") or ""), None, frame_diff):
                    ok, why = True, f"「{unless}」已成立，略過"
                else:
                    ok, why = self.exec_step(st)
            else:
                ok, why = self.exec_step(st)
        except Exception as e:  # noqa: BLE001
            ok, why = False, f"程式出錯 {type(e).__name__}: {e}"
        line = f"[{datetime.now().strftime('%H:%M:%S')}] 🤖 {p['名']} 第{p['i'] + 1}/{p['步數']}步 {self.step_text(st)} → {'✓' if ok else '✗'} {why}（{time.time() - t0:.1f}s）"
        print(line)
        self.proc_note(f"第{p['i'] + 1}步 {self.step_text(st)} → {'✓' if ok else '✗'} {why[:30]}")
        self.log({"時間": datetime.now().strftime("%H:%M:%S"), "模式": "程式執行", "程序": p["名"],
                  "步": p["i"] + 1, "動作": st, "成功": ok, "原因": why})
        if ok:
            p["i"] += 1; p["重試"] = 0
            try:
                self.note_progress("程式做成一步")
            except Exception:  # noqa: BLE001
                pass
        else:
            p["重試"] += 1
            if p["重試"] > int(st.get("重試", self.PROC_MAX_RETRY)):
                self.proc_escalate(st, why)
        try:
            self.reward_poll()
        except Exception:  # noqa: BLE001
            pass
        time.sleep(self.PROC_STEP_PAUSE)

    def proc_escalate(self, st, why):
        """特殊狀況：這一步試不過，交給大模型幾輪，並把失敗現場攤給它，讓它修程序。"""
        p = self.proc
        d = self.procs.get(p["名"], {})
        d["失敗"] = int(d.get("失敗", 0)) + 1
        d.setdefault("卡過", []).append({"步": p["i"] + 1, "原因": why[:80],
                                        "時間": datetime.now().strftime("%m/%d %H:%M")})
        d["卡過"] = d["卡過"][-10:]
        self.save_proc(d)
        self.stat["特殊狀況"] = self.stat.get("特殊狀況", 0) + 1
        self._proc_escalated_until = time.time() + self.PROC_ESCALATE_ROUNDS * max(10.0, self.deadline)
        self.special_hint = (
            f"🚨 **特殊狀況（程式交回給你）**：程序「{p['名']}」第 {p['i'] + 1}/{p['步數']} 步"
            f"「{self.step_text(st)}」連續 {p['重試']} 次失敗：{why}。" + NL
            + "接下來幾輪由你接手。你有兩個選擇：(1) 自己把這一步做過去，做到了送 "
            + f'{{"t":"執行程序","名":"{p["名"]}","從第幾步":{p["i"] + 2}}} 讓程式從下一步繼續；'
            + "(2) 這一步本來就寫錯了 → 用 寫程序 改掉它（同名覆蓋）。" + NL)
        print(f"🚨 特殊狀況：{p['名']} 第 {p['i'] + 1} 步失敗 {p['重試']} 次（{why}）→ 交給大模型 {self.PROC_ESCALATE_ROUNDS} 輪")
        self._proc_resume = {"名": p["名"], "i": p["i"]}
        self.proc = None

    # ---------------- 眼睛輔助：確保詞在詞表裡、多候選詞、YOLO 認不出時問反射方向
    SCAN_DWELL = 0.7

    def ensure_watch(self, labels):
        """目標詞不在眼睛正在盯的詞表裡就加進去（YOLO-World 只找有給的詞）。"""
        try:
            cur = list(getattr(self.eyes, "labels", []) or [])
            add = [l for l in labels if l and l not in cur]
            if add:
                self.eyes.watch(cur + add)
                self.watching = list(self.eyes.labels)
                time.sleep(0.5)               # 讓它至少跑過一個偵測週期
        except Exception:  # noqa: BLE001
            pass

    def best_any(self, labels, max_age=1.5):
        for l in labels:
            d = self.eyes.best(l, max_age=max_age)
            if d:
                return l, d
        return None, None

    def reflex_direction(self, what):
        """YOLO 認不出的東西，問反射（不思考、3 秒）它在畫面哪一邊。回 左/右/前/後/看不到。"""
        from play_agent import stream_json, parse_json_loose, jpeg_data_uri, 反射
        try:
            uri, _ = jpeg_data_uri(self.grab_still(640), 60)
            raw, _, _, _ = stream_json(self.endpoint, self.model, self.SEE_SYS,
                                       [{"type": "image_url", "image_url": {"url": uri}},
                                        {"type": "text", "text": f"畫面裡「{what}」在哪一邊？只看得到的才算。"
                                         '只輸出 JSON：{"方向":"左|右|前|看不到","距離":"近|中|遠"}'}],
                                       60, time.time() + 10.0, 0.5, effort=反射)
            o = parse_json_loose(raw) or {}
            return str(o.get("方向", "看不到")).strip(), str(o.get("距離", "中")).strip()
        except Exception:  # noqa: BLE001
            return "看不到", "中"

    # ---------------- 每一種步驟怎麼做、怎麼驗
    def step_text(self, st):
        k = st.get("做", "?")
        arg = st.get("地點") or st.get("物件") or st.get("方位") or st.get("條件") or st.get("程序") \
            or st.get("文字") or st.get("鍵") or st.get("位置") or ""
        return f"{k}{('「' + str(arg) + '」') if arg != '' else ''}"

    def exec_step(self, st):
        k = st["做"]
        if k == "修水平":
            p = float(getattr(self, "pitch_deg", 0.0))
            if abs(p) < 10:
                return True, f"已水平（{p:+.0f}°）"
            self.raw_look(0, int(round(p * self.dy_per_deg_v())))
            time.sleep(0.3)
            p2 = float(getattr(self, "pitch_deg", 0.0))
            return abs(p2) < 12, f"俯仰 {p:+.0f}° → {p2:+.0f}°"
        if k == "面向":
            want = float(st["方位"]) % 360
            cur = float(getattr(self, "yaw", 0.0)) % 360
            delta = (want - cur + 540) % 360 - 180
            if abs(delta) < 8:
                return True, f"已面向 {want:.0f}°"
            self.turn_by(delta)
            time.sleep(0.3)
            cur2 = float(getattr(self, "yaw", 0.0)) % 360
            d2 = abs((want - cur2 + 540) % 360 - 180)
            return d2 < 10, f"轉 {delta:+.0f}° → 剩 {d2:.0f}°"
        if k == "走到地點":
            nm = st["地點"]
            tgt = None
            if nm in (getattr(self, "stands", {}) or {}):
                v = self.stands[nm]; tgt = (float(v["x"]), float(v["y"]), v.get("yaw"))
            elif nm in (getattr(self, "spots", {}) or {}):
                v = self.spots[nm]; tgt = (float(v["x"]), float(v["y"]), v.get("朝向"))
            elif nm in (getattr(self, "map", {}) or {}):
                x, y = self.map[nm]; tgt = (float(x), float(y), None)
            if not tgt:
                return False, f"腦中地圖沒有「{nm}」"
            dist = math.hypot(tgt[0] - self.pos[0], tgt[1] - self.pos[1])
            if dist < 0.8:
                if tgt[2] is not None:
                    self.turn_by(((float(tgt[2]) - self.yaw + 540) % 360) - 180)
                return True, f"已在「{nm}」旁（{dist:.1f}）"
            self.goto_remembered(nm, why="程序")
            dist2 = math.hypot(tgt[0] - self.pos[0], tgt[1] - self.pos[1])
            return dist2 < 1.2 or dist2 < dist * 0.5, f"距離 {dist:.1f} → {dist2:.1f}"
        if k == "走近":
            labels = [x.strip() for x in str(st["物件"]).split("|") if x.strip()]
            self.ensure_watch(labels)
            lab, d = self.best_any(labels, max_age=2.0)
            if not d:
                # 看不到就掃一圈找（每面停久一點，讓 YOLO 跑到）
                for _ in range(4):
                    self.turn_by(90); time.sleep(self.SCAN_DWELL)
                    lab, d = self.best_any(labels, max_age=1.5)
                    if d:
                        break
            if not d:
                # YOLO 認不出 → 問反射它在哪一邊（視覺層，不是大模型推理）
                what = st.get("備註") or labels[0]
                side, dist = self.reflex_direction(what)
                if side in ("左", "右"):
                    self.turn_by(-45 if side == "左" else 45); time.sleep(0.4)
                    self.press_key("w", 1.5 if dist == "遠" else 1.0)
                    return False, f"YOLO 看不到，反射說在{side}邊（{dist}）→ 轉過去走一步再試"
                if side == "前":
                    self.press_key("w", 2.0 if dist == "遠" else 1.2)
                    return False, f"YOLO 看不到，反射說在前方（{dist}）→ 直走一步再試"
                if st.get("可略過"):
                    return True, f"看不到「{'/'.join(labels)}」，這步可略過"
                return False, f"轉了一圈 YOLO 看不到「{'/'.join(labels)}」，反射也看不到"
            self.goto_target(lab, budget=float(st.get("秒", 8.0)))
            _, d2 = self.best_any([lab] + labels, max_age=2.0)
            near = bool(d2) and (d2["h"] >= self.GOAL_NEAR_H or d2["cy"] + d2["h"] / 2 > self.GOAL_NEAR_BOTTOM)
            why = (f"「{lab}」框高 {d2['h']:.2f}" if d2 else "走完看不到了")
            if not near and st.get("可略過") and (d2 is None or d2["h"] < 0.12):
                # 只框到遠處一個小東西、走了也沒變近＝這個詞不是眼前的路標；標了可略過就放行往下走
                return True, why + "（太遠、走不近，這步可略過）"
            return near, why
        if k == "走向字":
            # 用畫面上的字當路標：招牌、門牌、公路牌都算。OCR 行有 x（中心）與 h（字高）。
            words = [x.strip().lower() for x in str(st["字"]).split("|") if x.strip()]
            lines, stamp = self.text_lines()
            hit = None
            for l in lines or []:
                t = str(l.get("t", "")).lower()
                if any(w in t for w in words):
                    hit = l; break
            if not hit:
                for _ in range(8):                                  # 掃一圈找字：八面各 45°，OCR 1.2 秒一次要多等
                    self.turn_by(45); time.sleep(1.6)
                    lines, _ = self.text_lines()
                    for l in lines or []:
                        if any(w in str(l.get("t", "")).lower() for w in words):
                            hit = l; break
                    if hit:
                        break
            if not hit:
                saw = "、".join(str(l.get("t", ""))[:10] for l in (lines or [])[:6]) or "（OCR 什麼都沒讀到）"
                if st.get("可略過"):
                    return True, f"讀不到「{'/'.join(words)}」，這步可略過"
                return False, f"轉了一圈都讀不到「{'/'.join(words)}」；OCR 當下讀到：{saw}"
            off = float(hit.get("x", 0.5)) - 0.5
            h0 = float(hit.get("h", 0.0))
            if abs(off) > 0.08:
                self.turn_by(off * 70.0 * 0.9); time.sleep(0.4)
            self.press_key("w", float(st.get("秒", 1.5)))
            time.sleep(1.3)
            lines, _ = self.text_lines()
            again = next((l for l in (lines or []) if any(w in str(l.get("t", "")).lower() for w in words)), None)
            if again is None:
                return True, f"「{hit.get('t', '')[:12]}」已經不在畫面上＝走到它底下了"
            h1 = float(again.get("h", 0.0))
            return h1 >= max(0.06, h0 * 1.25), f"字高 {h0:.3f} → {h1:.3f}（偏 {off:+.2f}）"
        if k == "對準":
            labels = [x.strip() for x in str(st["物件"]).split("|") if x.strip()]
            self.ensure_watch(labels)
            for i in range(3):
                lab, d = self.best_any(labels, max_age=1.5)
                if not d:
                    return False, f"看不到「{'/'.join(labels)}」"
                off = d["cx"] - 0.5
                if abs(off) < 0.08:
                    return True, f"「{lab}」在中央（偏 {off:+.2f}）"
                self.turn_by(off * 70.0 * 0.9)
                time.sleep(0.3)
            lab, d = self.best_any(labels, max_age=1.5)
            return bool(d) and abs(d["cx"] - 0.5) < 0.1, (f"偏 {d['cx'] - 0.5:+.2f}" if d else "對準後看不到")
        if k == "互動":
            key = str(st.get("鍵", "e")).lower()
            if st.get("需要提示", True) and not self.e_prompt(max_age=1.5):
                if st.get("可略過"):
                    return True, "沒有互動提示，這步可略過"
                return False, "畫面上沒有互動提示"
            ok, held = self.hold_interact(key, max_hold=float(st.get("秒", 1.5)))
            return bool(ok), f"按了 {held:.1f}s{'，有反應' if ok else '，沒反應'}"
        if k == "點":
            txt = st.get("文字")
            if txt and not self.has_text(str(txt)):
                if st.get("可略過"):
                    return True, f"畫面上沒有「{txt}」，這步可略過"
                return False, f"畫面上沒有「{txt}」"
            before = self.grab_still(320)
            x, y = st["位置"]
            self.execute([{"t": "click", "x": x, "y": y, "g": str(txt or "程序")}])
            time.sleep(0.6)
            from play_agent import frame_diff
            dv = frame_diff(before, self.grab_still(320))
            return dv >= 3.0, f"畫面變化 {dv:.0f}"
        if k == "按鍵":
            self.press_key(str(st["鍵"]).lower(), float(st.get("秒", 0.5)))
            return True, "按了"
        if k == "等待":
            cond = str(st["條件"]); limit = float(st.get("秒", 8.0)); t0 = time.time()
            base_task = str(getattr(self, "cur_task", "") or "")
            before = self.grab_still(320)
            from play_agent import frame_diff
            while time.time() - t0 < limit:
                if self.check_cond(cond, base_task, before, frame_diff):
                    return True, f"「{cond}」出現（{time.time() - t0:.1f}s）"
                time.sleep(0.5)
            if st.get("可略過"):
                return True, f"等了 {limit:.0f}s 沒出現「{cond}」，這步可略過"
            return False, f"等了 {limit:.0f}s 沒出現「{cond}」"
        if k == "檢查":
            cond = str(st["條件"])
            from play_agent import frame_diff
            ok = self.check_cond(cond, str(getattr(self, "cur_task", "") or ""), None, frame_diff)
            if not ok and st.get("可略過"):
                return True, f"「{cond}」不成立，這步可略過"
            return ok, f"「{cond}」{'成立' if ok else '不成立'}"
        if k == "我在":
            nm = st["地點"]
            src = (getattr(self, "stands", {}) or {}).get(nm) or (getattr(self, "spots", {}) or {}).get(nm)
            if src:
                self.pos = [float(src["x"]), float(src["y"])]
            elif nm in (getattr(self, "map", {}) or {}):
                self.pos = [float(self.map[nm][0]), float(self.map[nm][1])]
            else:
                return False, f"沒有「{nm}」"
            return True, "座標已校正"
        if k == "呼叫":
            sub = st["程序"]
            if sub not in self.procs:
                if st.get("可略過"):
                    return True, f"還沒有程序「{sub}」，這步可略過"
                return False, f"沒有程序「{sub}」"
            # 用內嵌方式：把子程序的步驟展開插進來
            p = self.proc; d = self.procs[p["名"]]
            d["步驟"][p["i"]:p["i"] + 1] = list(self.procs[sub]["步驟"])
            p["步數"] = len(d["步驟"])
            return True, f"展開子程序「{sub}」（{len(self.procs[sub]['步驟'])} 步）"
        return False, f"不認得「{k}」"

    def check_cond(self, cond, base_task, before, frame_diff):
        if cond == "E提示":
            return bool(self.e_prompt(max_age=1.5))
        if cond == "任務字變":
            try:
                lines, _ = self.text_lines()
                now = self.read_task_text() if hasattr(self, "read_task_text") else str(getattr(self, "cur_task", ""))
            except Exception:  # noqa: BLE001
                now = str(getattr(self, "cur_task", ""))
            return bool(now) and now != base_task
        if cond == "畫面變":
            return before is not None and frame_diff(before, self.grab_still(320)) >= 3.0
        if cond.startswith("任務字含:"):
            return cond.split(":", 1)[1] in str(getattr(self, "cur_task", "")) or self.has_text(cond.split(":", 1)[1])
        if cond.startswith("字:"):
            return self.has_text(cond.split(":", 1)[1])
        if cond.startswith("框到:"):
            return bool(self.eyes.best(cond.split(":", 1)[1], max_age=1.5))
        if cond == "在醫院":                       # 用 Roblox log 的 placeid 判，比讀字可靠（傳送有讀取畫面）
            try:
                return not self.in_lobby()
            except Exception:  # noqa: BLE001
                return False
        if cond == "在大廳":
            try:
                return bool(self.in_lobby())
            except Exception:  # noqa: BLE001
                return False
        return False

    # ---------------- 路線錄製：大廳出生點固定，走到門口的那一趟錄下來，之後純重播
    ROUTE_NAME = "大廳路線"

    def place_watch(self):
        """每輪看 placeid 有沒有換。進大廳＝水平儀歸零＋開始錄；進醫院＝把錄到的存成程序。"""
        try:
            pid = self.current_place()
        except Exception:  # noqa: BLE001
            return
        prev = getattr(self, "_prev_place", None)
        if pid == prev or pid is None:
            return
        self._prev_place = pid
        # 2026-09-16：記下「這條命是什麼時候開始的」——用來判斷他是不是一進場就死
        self._enter_at = time.time()
        if pid == self.LOBBY_PLACE:
            self.yaw, self.pitch_deg, self.pos = 0.0, 0.0, [0.0, 0.0]
            self._route_rec = []
            self._route_since = time.time()
            print("   🗺 進到大廳（出生點）：水平儀歸零、開始錄走到門口的路線")
        elif pid in self.GAME_PLACES and prev == self.LOBBY_PLACE:
            self.save_route()

    def route_note(self, k, secs):
        """press_key 會叫：只在大廳、只錄 wasd。"""
        if getattr(self, "_prev_place", None) != self.LOBBY_PLACE or k not in ("w", "a", "s", "d"):
            return
        rec = getattr(self, "_route_rec", None)
        if rec is None:
            rec = self._route_rec = []
        rec.append((round(getattr(self, "yaw", 0.0) % 360), k, float(secs)))
        if len(rec) > 200:
            del rec[:len(rec) - 200]

    def save_route(self):
        rec = list(getattr(self, "_route_rec", []) or [])
        total = sum(x[2] for x in rec)
        if len(rec) < 2 or not (2.0 <= total <= 120.0):
            print(f"   🗺 進醫院了，但這趟路線不值得存（{len(rec)} 段、共 {total:.1f} 秒）")
            return
        # 同方向、同鍵的連續段合併
        segs = []
        for yaw, k, secs in rec:
            if segs and segs[-1][1] == k and abs((yaw - segs[-1][0] + 540) % 360 - 180) < 12:
                segs[-1][2] += secs
            else:
                segs.append([yaw, k, secs])
        steps = [{"做": "按鍵", "鍵": "space", "秒": 0.15}]
        for yaw, k, secs in segs[:30]:
            steps.append({"做": "面向", "方位": int(yaw)})
            steps.append({"做": "按鍵", "鍵": k, "秒": round(min(secs, 6.0), 1)})
        steps.append({"做": "等待", "條件": "在醫院", "秒": 45, "重試": 1})
        old = self.procs.get(self.ROUTE_NAME, {})
        self.save_proc({"名": self.ROUTE_NAME, "步驟": steps, "來源": "錄製（大廳出生點→醫院門）",
                        "成功": int(old.get("成功", 0)), "失敗": int(old.get("失敗", 0)),
                        "錄於": datetime.now().strftime("%m/%d %H:%M"), "原始段數": len(rec), "總秒": round(total, 1)})
        print(f"   🗺✅ 進醫院了！這趟路線存成程序「{self.ROUTE_NAME}」：{len(segs)} 段、共 {total:.1f} 秒。"
              f"下次在大廳，開始上班會先重播它")
        self.history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 程式把你從大廳走到醫院門的路線錄成程序（{len(segs)} 段）")

    # ---------------- 學習期：讓大模型把步驟編譯成程序
    COMPILE_ASK = """【編譯程序】把你已經學會的做法，寫成**程式可以直接執行、自己驗證**的步驟。

這件事的目的：之後這個中任務**不用再問你**，程式照這份程序自己做——用腦中地圖轉向、用水平儀修視角、
用 YOLO 對準、看到 E 提示才按。你只有在程式卡住時才會被叫回來。

只能用下面這幾種動作（其他的程式不認得，會被退回）：
{dsl}

你手上的素材：
- 你自己列的步驟：{steps}
- 你成功過的做法：{wins}
- 腦中地圖的地點：{places}
- 站位：{stands}
- 互動點（有座標與朝向）：{spots}

規矩：
- 每一步都要是上面那幾種之一，參數要填程式看得到的名字（地點要用腦中地圖裡有的名字；物件用 YOLO 的英文詞）。
- 先「修水平」、再「走到地點」或「走近」、再「對準」、有提示才「互動」、之後「等待 任務字變」確認。
- 不確定的步驟不要編，寧可少幾步。
只輸出 JSON：{{"名":"{name}","步驟":[{{"做":"修水平"}},{{"做":"走到地點","地點":"..."}},...]}}"""

    def compile_proc(self, name):
        """學習期：請大模型（慢想檔）把散文步驟＋成功紀錄編成 DSL；程式驗證後才存。"""
        from play_agent import stream_json, parse_json_loose, 慢想
        steps = (getattr(self, "steps", {}) or {}).get(name, {}).get("steps") or []
        chain = (getattr(self, "chains", {}) or {}).get(name) or []
        wins = [w for w in (getattr(self, "wins", []) or []) if name in str(w.get("任務", "")) or True][-8:]
        dsl = NL.join(x.strip() for x in __doc__.split("步驟語言（DSL）")[1].split("每一步可加")[0].split(NL) if x.strip())
        ask = self.COMPILE_ASK.format(
            dsl=dsl, name=name,
            steps="／".join(steps or chain) or "（沒有）",
            wins="；".join(f"{w.get('做法')} {w.get('目標')} {w.get('秒數')}s 前一步{w.get('前一步')}" for w in wins) or "（沒有）",
            places="、".join(list((getattr(self, "map", {}) or {}).keys())[:20]) or "（沒有）",
            stands="、".join(f"{k}(朝{v.get('yaw', 0):.0f}°)" for k, v in (getattr(self, "stands", {}) or {}).items()) or "（沒有）",
            spots="、".join(f"{k}(朝{v.get('朝向', 0)}° {v.get('怎麼用', '')})" for k, v in list((getattr(self, "spots", {}) or {}).items())[:12]) or "（沒有）")
        print(f"🧩 請大模型把「{name}」編譯成程序（不趕時間）…")
        try:
            raw, _, _, _ = stream_json(self.endpoint, self.model, self.sys_prompt(),
                                       [{"type": "text", "text": ask}], 2500, time.time() + 200.0, 0.3, effort=慢想)
        except Exception as e:  # noqa: BLE001
            print(f"   編譯請求失敗：{e}"); return False
        o = parse_json_loose(raw) or {}
        ok, bad = self.validate_steps(o.get("步驟") or [])
        for b in bad[:6]:
            print(f"   ⛔ 退回：{b}")
        if len(ok) < 2:
            print(f"   🧩 「{name}」編不出可用的程序（只有 {len(ok)} 步有效）")
            return False
        old = self.procs.get(name, {})
        self.save_proc({"名": name, "步驟": ok, "來源": "編譯", "成功": int(old.get("成功", 0)),
                        "失敗": int(old.get("失敗", 0)), "退回": bad[:6]})
        print(f"🧩✅ 「{name}」編譯成 {len(ok)} 步程序" + (f"（退回 {len(bad)} 步）" if bad else ""))
        for i, st in enumerate(ok):
            print(f"      {i + 1}. {self.step_text(st)}")
        return True

    def maybe_compile(self):
        """學習期自動觸發：現在專注的中任務有散文步驟、卻還沒有程序 → 編譯一次。"""
        focus = (getattr(self, "tree", None) or {}).get("現在專注") or ""
        if not focus or focus in self.procs:
            return
        if focus not in (getattr(self, "steps", {}) or {}) and focus not in (getattr(self, "chains", {}) or {}):
            return
        last = getattr(self, "_compile_at", {}).get(focus, 0)
        if time.time() - last < 1800:
            return
        self._compile_at = getattr(self, "_compile_at", {}); self._compile_at[focus] = time.time()
        self.compile_proc(focus)

    # ---------------- 執行期的監督：大模型用快思定期看一眼方向對不對
    SUPERVISE_EVERY_ROUNDS = 8
    SUPERVISE_EVERY_SECS = 120.0
    SUPERVISE_ASK = """【小檢查】程式正在照程序自己做事，你只要花十秒看一眼**方向對不對**。

現在：{tree}
程式在跑：{proc}
最近幾步：{recent}
眼睛看到：{seen}

回答三件事：
1. 方向對不對——程式做的這件事，是不是任務樹 👉 那個中任務、或它必要的前置（人在大廳先回醫院、理智低先補）？
2. 接下來該怎樣：**繼續**（沒問題）／**換成:某程序或中任務**（該做別的了，例如窗口沒人該去治療）／**接手**（情況不對，你要自己來幾輪）
3. 一句理由
只輸出 JSON：{{"方向":"對|不對","接下來":"繼續|換成:名字|接手","理由":"(<=40字)"}}"""

    def proc_note(self, line):
        self._proc_log = (getattr(self, "_proc_log", []) + [line])[-8:]

    def proc_supervise(self, force=False):
        """每 8 個程式輪或 120 秒，用快思做一次小檢查。回 True 表示這輪呼叫了大模型。"""
        n = self.stat.get("程式輪", 0)
        last_n, last_t = getattr(self, "_sup_at", (0, 0.0))
        if not force and (n - last_n < self.SUPERVISE_EVERY_ROUNDS or time.time() - last_t < self.SUPERVISE_EVERY_SECS):
            return False
        self._sup_at = (n, time.time())
        from play_agent import stream_json, parse_json_loose, jpeg_data_uri, 快思
        try:
            im = self.grab_still(640)
            uri, _ = jpeg_data_uri(im, 60)
            seen = self.reflex_see(uri) or "（反射沒回）"
        except Exception:  # noqa: BLE001
            uri, seen = None, "（拿不到畫面）"
        p = self.proc
        ptxt = (f"「{p['名']}」第 {p['i'] + 1}/{p['步數']} 步" if p else "（沒有程序在跑）")
        ask = self.SUPERVISE_ASK.format(tree=self.task_tree_text(), proc=ptxt,
                                        recent=NL.join("　" + x for x in getattr(self, "_proc_log", [])[-6:]) or "（還沒有）",
                                        seen=seen)
        content = ([{"type": "image_url", "image_url": {"url": uri}}] if uri else []) + [{"type": "text", "text": ask}]
        self.stat["模型輪"] = self.stat.get("模型輪", 0) + 1
        self.stat["監督"] = self.stat.get("監督", 0) + 1
        try:
            raw, _, _, _ = stream_json(self.endpoint, self.model, self.sys_prompt(), content,
                                       400, time.time() + 45.0, 0.3, effort=快思)
        except Exception as e:  # noqa: BLE001
            print(f"   監督請求失敗：{e}"); return True
        o = parse_json_loose(raw) or {}
        ok = str(o.get("方向", "")).strip()
        nxt = str(o.get("接下來", "繼續")).strip()
        why = str(o.get("理由", ""))[:60]
        rec = {"時間": datetime.now().strftime("%m/%d %H:%M:%S"), "程序": ptxt, "方向": ok, "接下來": nxt, "理由": why}
        try:
            with open(os.path.join(self.dir, "監督.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + NL)
        except Exception:  # noqa: BLE001
            pass
        print(f"   🔎 快思小檢查：方向{ok or '?'}｜{nxt}｜{why}")
        self.history.append(f"[{rec['時間'][-8:]}] 快思小檢查：方向{ok}，{nxt}（{why}）")
        if nxt.startswith("換成"):
            target = nxt.split(":", 1)[1].strip() if ":" in nxt else ""
            if target in self.procs:
                self.proc_stop(f"小檢查說該換成「{target}」")
                self.proc_start(target, why=why)
            elif target:
                self._focus_override = (target, time.time() + 1800)
                self.proc_stop(f"小檢查說該換成「{target}」，但沒有它的程序 → 交回你來做／編譯")
                self._proc_escalated_until = time.time() + self.PROC_ESCALATE_ROUNDS * max(10.0, self.deadline)
                self.special_hint = (f"🔎 你自己在小檢查時說該換成「{target}」（{why}），但它還沒有程序——"
                                     f"接下來幾輪你來做；做得順就用 寫程序 把它寫下來。" + NL)
        elif nxt == "接手" or ok == "不對":
            self.proc_stop("小檢查說情況不對")
            self._proc_escalated_until = time.time() + self.PROC_ESCALATE_ROUNDS * max(10.0, self.deadline)
            self.special_hint = f"🔎 你自己在小檢查時說方向不對（{why}），接下來幾輪你來。" + NL
        return True

    # ---------------- 給提示的面板
    def proc_text(self):
        rows = [f"（程式輪 {self.stat.get('程式輪', 0)}／模型輪 {self.stat.get('模型輪', 0)}／特殊狀況 {self.stat.get('特殊狀況', 0)}）"]
        if self.proc:
            rows.append(f"程式正在跑「{self.proc['名']}」第 {self.proc['i'] + 1}/{self.proc['步數']} 步")
        if not self.procs:
            rows.append("還沒有任何程序。**學會一個中任務之後，用 寫程序 把它寫成程式步驟**，之後那件事就不用問你了。")
        else:
            for k, v in self.procs.items():
                rows.append(f"　{k}：{len(v['步驟'])} 步（成功 {v.get('成功', 0)}／失敗 {v.get('失敗', 0)}）"
                            + (f"　最近卡在第 {v['卡過'][-1]['步']} 步：{v['卡過'][-1]['原因'][:30]}" if v.get("卡過") else ""))
        if getattr(self, "special_hint", ""):
            rows.append(self.special_hint)
        return NL.join(rows)
