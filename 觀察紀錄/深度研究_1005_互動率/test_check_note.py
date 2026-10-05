# 離線測「檢查輪可以簡單寫一句」（不碰遊戲）；最後一段會真的問 DGX 上的大腦一次，看他會不會寫、要幾秒
import os, sys, types, json, shutil, tempfile, builtins, io, time, base64, urllib.request
os.chdir(r"D:\Documents\ThorWorkSpace\gamer AI")
sys.path.insert(0, os.getcwd())
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import flow_agent as fa
import play_agent as pa
F = fa.FlowAgent
fa.print = lambda *a, **k: None
P = builtins.print
fails = []


def check(name, cond, extra=""):
    P(("✅" if cond else "❌"), name, extra)
    if not cond:
        fails.append(name)


tmp = tempfile.mkdtemp()
W = {"t": "key", "k": "w", "d": 2.0}
E = {"t": "hold", "k": "e", "d": 1.5}


def me():
    m = types.SimpleNamespace(stat={}, history=[], _plan_q=[{"做": "②按E", "a": [E], "成": ""}], _plan_cur={"做": "①走", "a": [W], "成": ""},
                              _force_fast=False, _reflex_check=True, PLAN3_ACTS=F.PLAN3_ACTS, PRESS_ACTS=F.PRESS_ACTS, dir=tmp,
                              CHECK_NOTE_MAX=F.CHECK_NOTE_MAX, CHECK_NOTE_KEEP=F.CHECK_NOTE_KEEP, CHECK_NOTE_SHOW_SECS=F.CHECK_NOTE_SHOW_SECS)
    m.press_desc = lambda acts: F.press_desc(m, acts)
    m.mark_step = lambda what: None
    m.check_note = lambda o: F.check_note(m, o)
    m.check_notes_text = lambda: F.check_notes_text(m)
    return m


m = me()
o = F.plan3_apply(m, pa.反射, {"ok": True, "可以按": True, "寫": "畫面跳出 收成 3 支"})
check("有寫：照樣做下一步", o["a"] == [E])
check("有寫：這一輪的『看到』變成他自己寫的那一句", "收成 3 支" in str(o.get("s")) and "檢查輪自己寫的" in str(o.get("s")), str(o.get("s")))
check("有寫：留給快思的那一行有這一句", "收成 3 支" in F.check_notes_text(m), F.check_notes_text(m)[:80])
check("有寫：進歷史、存檔", any("檢查輪你自己寫" in h for h in m.history) and os.path.exists(os.path.join(tmp, "檢查輪寫的.jsonl")))
check("有寫：計數 1", m.stat.get("檢查輪寫了") == 1)

m = me()
o = F.plan3_apply(m, pa.反射, {"ok": True, "可以按": True})
check("沒寫：行為跟原本一樣、沒有多出東西", o["a"] == [E] and not getattr(m, "check_notes", None) and "s" not in o and F.check_notes_text(m) == "")

m = me()
o = F.plan3_apply(m, pa.反射, {"ok": False, "可以按": False, "寫": "面板還開著 選擇種子"})
check("ok false 也收下那一句，照樣換快思", o["a"] == [{"t": "換快思"}] and "選擇種子" in F.check_notes_text(m))

m = me()
o = F.plan3_apply(m, pa.反射, {"ok": True, "可以按": False, "寫": "沒看到E提示 只有 未解鎖"})
check("可以按 false 也收下那一句，不按", o["a"] == [{"t": "換快思"}] and "未解鎖" in F.check_notes_text(m))

m = me()
F.check_note(m, {"寫": "字" * 200})
check("一句最多 30 字", len(m.check_notes[-1]["寫"]) == 30)
for i in range(10):
    F.check_note(m, {"寫": f"第{i}句"})
check("只留最近 6 句、面板放最近 4 句", len(m.check_notes) == 6 and F.check_notes_text(m).count("「") == 4)
m.check_notes[-1]["t"] -= 3600
m.check_notes = m.check_notes[-1:]
check("超過十分鐘的不再放上面板", F.check_notes_text(m) == "")
m = me(); F.check_note(m, {"寫": "無"})
check("寫『無』不算", not getattr(m, "check_notes", None))

# 面板文字
m = me(); m.ban_text = lambda: ""; m.last_step_text = lambda: ""; m._seen_reflex = ""; m.gyro_text = lambda: ""; m.text_text = lambda: ""
m.cur_task = ""; m.short_mem_text = lambda n=None: ""; m.describe = lambda a: str(a.get("t"))
txt = F.reflex_check_panel(m)
check("檢查輪的提示有講可以多寫一句", "可以多寫一句" in txt and '"寫"' in txt and "不要寫別的" not in txt, txt[-150:])
shutil.rmtree(tmp, ignore_errors=True)

# 真的問一次大腦（同一張有 E 提示的圖），量時間
try:
    img = r"D:\Documents\ThorWorkSpace\gamer AI\專案\農場遊戲\記憶\關鍵動作\1004_084034_第一次做成某件事.jpg"
    b64 = base64.b64encode(open(img, "rb").read()).decode()
    body = "【快思交代的上一步】做「一次走到位」，做完應該看到：「田區變大」\n【接下來要按的】做「看E提示才按」：長按 E 互動「目標」\n【眼睛看到】框到 person（中中）；字：695｜鏡頭在看水平\n" + txt[txt.find("**你只做兩件事"):]
    for k in range(3):
        payload = {"model": "gamer-27b-ft-run1", "max_tokens": 110, "temperature": 0.65, "top_p": 0.85, "top_k": 20,
                   "chat_template_kwargs": {"enable_thinking": False},
                   "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}},
                                                             {"type": "text", "text": body}]}]}
        t0 = time.time()
        r = json.loads(urllib.request.urlopen(urllib.request.Request("http://192.168.0.126:8888/v1/chat/completions",
                       data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers={"Content-Type": "application/json"}), timeout=60).read().decode("utf-8"))
        P(f"   大腦回（{time.time()-t0:.1f}s）：", r["choices"][0]["message"]["content"].strip().replace("\n", " ")[:200])
except Exception as e:  # noqa: BLE001
    P("   （大腦那一段沒測到：", e, "）")

P()
P("全部通過" if not fails else f"沒過：{fails}")
sys.exit(1 if fails else 0)
