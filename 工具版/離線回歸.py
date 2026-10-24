"""每次改工具後跑：python 工具版/離線回歸.py（dry-run，不碰遊戲）。任何一行 FAIL 就不要重啟代理。"""
import os, sys, math, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
import tool_agent as T
from PIL import Image
cat = T.load_catalog("動物醫院"); t = T.Tools("動物醫院", cat, live=False, image_path=None)
ok = True
def check(name, cond, info=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (" | " + str(info)[:120] if info else ""))
    ok = ok and bool(cond)
check("V_FOV", getattr(t, "V_FOV", None) == 70.0)
r = t.look_turn(30); check("轉(30)", "轉了 30" in r, r)
r = t.look_turn(120); check("轉(120) 截 90", "只轉 +90" in r, r)
r = t.look_at(0.75, 0.45); check("視角移動到", "左右 +34" in r, r)
r = t.look_level(); check("修水平 dry-run", "dry-run" in r, r)
r = t.key_hold("e", 1.5); check("長按", "dry-run" in r or "按" in r, r)
r = t.see_around(); check("看四周", "拍了 4 張" in r or "離線" in r, r)
r = t.fix_panel(); check("關掉", "esc" in r, r)
check("run 等", "等了" in t.run("等", {"秒": "1"}))
n, p = T._parse_call("檢討(長按E、等2秒,有反應,沒反應,太短,下次長按)", cat); check("parse 「、」", p.get("原因") == "長按E、等2秒", p)
n, p = T._parse_call("記地點(候診區,藍椅子一排、電腦櫃檯)", cat); check("parse 選填", p.get("樣子") == "藍椅子一排、電腦櫃檯", p)
raw = '{"看到":"x","下兩步":[{"做":"走(0.8)","結果應該是:往前"}]}'
from play_agent import parse_json_loose
check("repair_json", "下兩步" in parse_json_loose(T.repair_json(raw)))
shot = os.path.join(HERE, "..", "專案", "動物醫院", "記憶", "畫面")
im = None
for f in sorted(os.listdir(shot))[-3:] if os.path.isdir(shot) else []:
    try:
        im = Image.open(os.path.join(shot, f)).convert("RGB"); break
    except Exception:
        pass
if im is not None:
    W, H = im.size; th = math.tan(math.radians(70 / 2)) * W / H
    px = int(W * math.tan(math.radians(20)) / (2 * th)); after = Image.new("RGB", (W, H)); after.paste(im, (-px, 0))
    got, txt = t._landmark_turn(im, after, 1, 20); check("標點 20 度", got is not None and 12 <= got <= 26, txt)
    z = T.Tools.zoom_factor(im, im.crop((W // 8, H // 8, W - W // 8, H - H // 8)).resize((W, H))); check("zoom 1.33", z and 1.2 <= z <= 1.5, z)
print("ALL PASS" if ok else "SOME FAIL"); sys.exit(0 if ok else 1)
