# 離線測 GAMER_PLAN_STEPS（不碰遊戲）
import os, sys, types, io, builtins, importlib
os.chdir(r"D:\Documents\ThorWorkSpace\gamer AI"); sys.path.insert(0, os.getcwd())
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
P = builtins.print; fails = []
def check(n, c, e=""):
    P(("✅" if c else "❌"), n, e)
    if not c: fails.append(n)
W = {"t": "key", "k": "w", "d": 2.0}; E = {"t": "hold", "k": "e", "d": 1.5}; L = {"t": "look", "dx": 40, "dy": 0}
def me(F):
    m = types.SimpleNamespace(stat={}, history=[], _plan_q=[], _plan_cur=None, _force_fast=False, _reflex_check=False,
                              PLAN3_ACTS=F.PLAN3_ACTS, PRESS_ACTS=F.PRESS_ACTS, PLAN_STEPS=F.PLAN_STEPS)
    m.press_desc = lambda acts: F.press_desc(m, acts); m.mark_step = lambda w: None
    return m
for val in ("1", "3", ""):
    if val: os.environ["GAMER_PLAN_STEPS"] = val
    else: os.environ.pop("GAMER_PLAN_STEPS", None)
    for k in [k for k in sys.modules if k in ("flow_agent",)]: del sys.modules[k]
    import flow_agent as fa, play_agent as pa
    fa.print = lambda *a, **k: None
    F = fa.FlowAgent
    P(f"--- GAMER_PLAN_STEPS={val or '（沒設）'} → PLAN_STEPS={F.PLAN_STEPS}")
    m = me(F)
    o = F.plan3_apply(m, pa.快思, {"想": "x", "a": [], "步": [{"做": "①", "a": [L], "成": ""}, {"做": "②", "a": [W], "成": ""}, {"做": "③", "a": [E], "成": ""}]})
    txt = F.plan3_ask(m)
    if val == "1":
        check("設 1：他還是寫了三步 → 只做①，不排佇列（下一輪還是快思）", o["a"] == [L] and m._plan_q == [])
        check("設 1：面板改成『這一輪只決定現在這一步』", "只決定現在這一步" in txt and "不用寫 步" in txt and "不要閒置太久" in txt)
        m = me(F); o = F.plan3_apply(m, pa.快思, {"想": "x", "a": [W]})
        check("設 1：只給 a、沒寫步 → 照 a 做，佇列空", o["a"] == [W] and m._plan_q == [])
        m = me(F); o = F.one_press_per_look(m, F.plan3_apply(m, pa.快思, {"想": "x", "a": [L, W]}))
        check("設 1：同一輪送兩個按鍵 → 先做第一個，第二個排給檢查輪", o["a"] == [L] and len(m._plan_q) == 1 and m._plan_q[0]["a"] == [W])
    else:
        check("沒設／設 3：跟原本一樣排兩步進佇列", o["a"] == [L] and len(m._plan_q) == 2)
        check("沒設／設 3：面板還是三步的說明", "快思要直接給三步的動作" in txt)
P(); P("全部通過" if not fails else f"沒過：{fails}"); sys.exit(1 if fails else 0)
