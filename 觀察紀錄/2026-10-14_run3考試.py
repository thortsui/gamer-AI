# -*- coding: utf-8 -*-
"""一輪考試：拿 10/13 08:02:32 那一輪真正送進模型的 system＋user 面板＋截圖，補上 10/14 新加的速記段，送給 run3。"""
import base64, json, os, sys, time
ROOT = r"D:\Documents\ThorWorkSpace\gamer AI"
SP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
import play_agent as pa  # noqa: E402
import shorthand  # noqa: E402

M = os.path.join(ROOT, "專案", "動物醫院", "記憶")
rec = json.load(open(os.path.join(SP, "last_fast.json"), encoding="utf-8"))
system = open(os.path.join(M, "面板", "system_2498a5f5e302.txt"), encoding="utf-8").read()
user = rec["user"]

SH_DOC = """  **速記（省字，2026-10-14）**：a 裡可以直接寫字串「名字(量)」，不用包成 dict，舊寫法也照樣可以。
      走：前進(0.6) 後退(0.5) 左移(0.5) 右移(0.5)（括號＝秒，不寫＝0.5）　跳
      轉：視角左(100) 視角右(100) 視角上(40) 視角下(40)（括號＝像素，跟 dx／dy 同刻度、同上限）
      按：按(e) 按(e,1.5) 長按(e) 點(0.5,0.4) 點字(開始遊戲) 等(1) 捲(-3)
      工具：沒參數就只寫名字（水平歸零、這步做完、量比例…）；一個名字參數就放括號（走到記過的(門口)、鎖定(cat)、換快思(為什麼)）
      例：{"a":["前進(0.6)","視角左(100)"]}
"""
HINT = "a 可用速記省字：前進(0.6)、視角左(100)、長按(e)、點字(字)。\n"
anchor = "動作（一次最多 2 個，座標 0~1）：\n"
assert system.count(anchor) == 1
system = system.replace(anchor, anchor + SH_DOC, 1)
anchor2 = "a 就是「動作」那一欄，照表執行。\n"
assert user.count(anchor2) == 1, user.count(anchor2)
user = user.replace(anchor2, anchor2 + HINT, 1)

img = open(os.path.join(M, "畫面", "080225_定時.jpg"), "rb").read()
uri = "data:image/jpeg;base64," + base64.b64encode(img).decode()
content = [{"type": "image_url", "image_url": {"url": uri}}, {"type": "text", "text": user}]

endpoint = "http://192.168.0.126:8888/v1"
model = "gamer-27b-ft-run3"
t0 = time.time()
raw, trunc, ttft, _ = pa.stream_json(endpoint, model, system, content, 1300, time.time() + 150, 0.2, effort="快思推理")
dt = time.time() - t0
reason = pa.LAST_REASONING.get("text", "")
o = pa.parse_json_loose(raw) or {}
lg = shorthand.expand_reply(o)

out = []
out.append(f"耗時 {dt:.1f}s  首字 {ttft}  截斷 {trunc}  system {len(system)} 字  user {len(user)} 字  推理 {len(reason)} 字  輸出 {len(raw)} 字")
out.append("\n===== 他的內部推理（reasoning_content）=====\n" + reason)
out.append("\n===== 他的原始輸出 =====\n" + raw)
out.append("\n===== 解析後 =====\n" + json.dumps(o, ensure_ascii=False, indent=1))
out.append("\n===== 速記翻譯 =====\n" + (shorthand.describe(lg) if lg else "（他這輪沒用速記）"))
out.append("\n===== 10/13 當時那一輪他的答案（對照）=====\n" + rec["輸出"].strip())
out.append("\n===== 10/13 當時的推理（對照）=====\n" + (rec.get("推理") or ""))
open(os.path.join(SP, "exam_out.md"), "w", encoding="utf-8").write("\n".join(out))
open(os.path.join(SP, "exam_user.txt"), "w", encoding="utf-8").write(user)
open(os.path.join(SP, "exam_system.txt"), "w", encoding="utf-8").write(system)
print("done", f"{dt:.1f}s")
