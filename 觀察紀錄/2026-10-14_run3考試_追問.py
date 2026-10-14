# -*- coding: utf-8 -*-
"""接著剛才那輪考試問他：為什麼不用轉頭（look）而用 a/s/d/w 移動？多輪對話、非串流，直接打 /chat/completions。"""
import base64, json, os, sys, time, urllib.request
ROOT = r"D:\Documents\ThorWorkSpace\gamer AI"
SP = os.path.dirname(os.path.abspath(__file__))
M = os.path.join(ROOT, "專案", "動物醫院", "記憶")
system = open(os.path.join(SP, "exam_system.txt"), encoding="utf-8").read()
user = open(os.path.join(SP, "exam_user.txt"), encoding="utf-8").read()
img = open(os.path.join(M, "畫面", "080225_定時.jpg"), "rb").read()
uri = "data:image/jpeg;base64," + base64.b64encode(img).decode()
answer = ('{"s": "正前灰牆貼近，右側植物葉影，無E提示", "區": "蓋印表格區", "task": "從房間2中的病人身上採集樣本", '
          '"想": "因為:正前牆距8貼牆且E提示未現｜所以要:離正前牆找開闊處｜動作:a0.5往左橫移離牆｜結果應該是:正前牆離開、看到左側開闊", '
          '"a": [{"t": "key", "k": "a", "d": 0.5, "g": "左側開闊處"}]}')
question = ("使用者問你：你剛才為什麼不選擇用轉頭（look，轉視角）去找目標，而是選擇用 a／s／d／w 移動？"
            "請用你自己的話誠實說明你當時的判斷依據：你看到了什麼、哪一條規則或哪一個數字讓你決定橫移而不是轉頭、"
            "你覺得轉頭在這個情況會有什麼問題、什麼情況下你才會選轉頭。用繁體中文、300 字以內、直接說，不用 JSON。")
payload = {
    "model": "gamer-27b-ft-run3",
    "messages": [
        {"role": "system", "content": system},
        {"role": "user", "content": [{"type": "image_url", "image_url": {"url": uri}}, {"type": "text", "text": user}]},
        {"role": "assistant", "content": answer},
        {"role": "user", "content": question},
    ],
    "max_tokens": 1200, "stream": False,
    "chat_template_kwargs": {"enable_thinking": True}, "reasoning_effort": "low",
    "temperature": 0.65, "top_p": 0.85, "top_k": 20,
}
req = urllib.request.Request("http://192.168.0.126:8888/v1/chat/completions",
                             data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                             headers={"Content-Type": "application/json"})
t0 = time.time()
with urllib.request.urlopen(req, timeout=240) as r:
    res = json.loads(r.read().decode("utf-8"))
dt = time.time() - t0
msg = res["choices"][0]["message"]
out = [f"耗時 {dt:.1f}s  usage {res.get('usage')}",
       "\n===== 他的內部推理 =====\n" + str(msg.get("reasoning_content") or msg.get("reasoning") or ""),
       "\n===== 他的回答 =====\n" + str(msg.get("content") or "")]
open(os.path.join(SP, "exam_ask_out.md"), "w", encoding="utf-8").write("\n".join(out))
print("done", f"{dt:.1f}s")
