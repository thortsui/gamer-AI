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
    got2, txt2, sc = t._landmark_verify(im, after, 20, None); check("標點驗證 20 度（視角移動到用）", got2 is not None and 12 <= got2 <= 26, txt2)
    got3, txt3, sc3 = t._landmark_verify(im, after, 20, -0.9); check("標點驗證 位移 ≥0.45 不換算", got3 is not None and sc3 is None, txt3)
    got4, txt4, sc4 = t._landmark_verify(im, after, 20, -0.3); check("標點驗證 整張位移錯但標點跟送出接近→信標點", got4 is not None and sc4 is not None, txt4)
    _orig = t._scene_deg; t._scene_deg = lambda b, a: (60.0, "假的")
    got5, txt5, sc5 = t._landmark_verify(im, after, -20, -0.3); check("標點驗證 跟位移、送出都差太多→否決", got5 is None, txt5)
    t._scene_deg = _orig
    z = T.Tools.zoom_factor(im, im.crop((W // 8, H // 8, W - W // 8, H - H // 8)).resize((W, H))); check("zoom 1.33", z and 1.2 <= z <= 1.5, z)
check("釘住判斷 小角度跟著景物移→不是釘住", not T.Tools._pin_ok(0.03, 0.02, 0.04, 0.02))
check("釘住判斷 大角度幾乎沒動→釘住", T.Tools._pin_ok(0.005, 0.0, 0.41, 0.2))
check("釘住判斷 OCR 抖 0.01 仍算釘住", T.Tools._pin_ok(0.01, 0.008, 0.3, 0.0))
v, dflt = t.walk_speed(); check("走路速度 預設 3 身高/秒", v == 3.0 and dflt, (v, dflt))
t.startup["速度"] = "約 2.5 身高/秒"; v2, d2 = t.walk_speed(); check("走路速度 讀校準值", v2 == 2.5 and not d2, (v2, d2))
r = t._heights_to_sec("前方：約 5 個身高 牆；場景：門 (0.3,0.5) 約 2.5 個身高遠；自己 1 個身高"); check("身高→秒走路", r == "前方：約 2.0 秒走路 牆；場景：門 (0.3,0.5) 約 1.0 秒走路遠；自己約 0.4 秒走路", r)
t.startup.pop("速度", None)
import numpy as _np
_tile = _np.tile(_np.kron(_np.array([[0, 255], [255, 0]], dtype=_np.uint8), _np.ones((12, 12), dtype=_np.uint8)), (6, 12))
rep, per = T.Tools._repetitive(Image.fromarray(_tile).convert("RGB")); check("重複花紋 方磚→是", rep, (rep, per))
_rng = _np.random.default_rng(0); rep2, _ = T.Tools._repetitive(Image.fromarray(_rng.integers(0, 255, (124, 240), dtype=_np.uint8)).convert("RGB")); check("重複花紋 雜訊→否", not rep2)
if im is not None:
    rep3, per3 = T.Tools._repetitive(im); check("重複花紋 真截圖→否", not rep3, (rep3, per3))
n, p = T._parse_call("檢討(轉90要到90；實測只有40；仍看天花板；刻度在磚紋上量的；先修水平再量)", cat); check("parse 「；」分五格", p.get("下次") == "先修水平再量", p)
_tim = Image.fromarray(_tile).convert("RGB").resize((1920, 991)); _taf = Image.new("RGB", (1920, 991)); _taf.paste(_tim, (-int(1920 * 0.06), 0))
_g, _txt = t._landmark_turn(_tim, _taf, 1, 20); check("標點 方磚平移→判重複花紋不當標點", _g is None and getattr(t, "_lm_ambiguous", False), _txt)
def _rot(img, deg):
    """合成針孔純左右轉 deg 度（正＝右轉）：每個輸出欄從 tan(a'+θ) 取樣"""
    import numpy as _np, math as _m
    a = _np.asarray(img.convert("RGB")); Hh, Ww = a.shape[:2]; th_ = _m.tan(_m.radians(35)) * Ww / Hh
    xs = (_np.arange(Ww) + 0.5) / Ww
    src = 0.5 + _np.tan(_np.arctan((xs - 0.5) * 2 * th_) + _m.radians(deg)) / (2 * th_)
    idx = _np.clip((src * Ww).astype(int), 0, Ww - 1); valid = (src >= 0) & (src < 1)
    out = a[:, idx]; out[:, ~valid] = 0
    return Image.fromarray(out)
if im is not None:
    for dg in (10, 20, 30):
        got, note = t._scene_deg(im, _rot(im, dg)); check(f"整張位移兩帶換算 {dg} 度", got is not None and abs(got - dg) <= 3.5, (round(got, 1) if got is not None else None, note))
    got, note = t._scene_deg(im, _rot(im, -20)); check("整張位移兩帶換算 -20 度", got is not None and abs(got + 20) <= 3.5, (round(got, 1) if got is not None else None, note))
yw, pt = t._angles_to(0.9, 0.2, 1920, 991); check("_angles_to pitch 修正 (0.9,0.2)≈15.9", abs(pt - 15.9) < 0.6, (round(yw, 1), round(pt, 1)))
if im is not None:
    import math as _m2
    _dy = int(im.size[1] * _m2.tan(_m2.radians(10)) / (2 * _m2.tan(_m2.radians(35))))   # 抬頭 10 度：景物往下移（用截圖自己的高）
    _af = Image.new("RGB", im.size); _af.paste(_rot(im, 15), (0, _dy))
    g_np, _ = t._landmark_turn(im, _af, 1, 15, pitch=0); g_p, txt_p = t._landmark_turn(im, _af, 1, 15, pitch=10)
    check("標點 轉15＋抬頭10：帶 pitch 找得到", g_p is not None and 11 <= g_p <= 19, (g_np, g_p, txt_p[:80]))
r0 = t.look_at(0.5, 0.5); check("視角移動到(0.5,0.5) 不轉", "已經在畫面正中央" in r0, r0)
if im is not None:
    _dy10 = int(im.size[1] * _m2.tan(_m2.radians(10)) / (2 * _m2.tan(_m2.radians(35))))
    _up = Image.new("RGB", im.size); _up.paste(im, (0, _dy10))
    pm, pm_txt = t._pitch_from_vshift(im, _up); check("上下位移換算 抬頭 10 度", pm is not None and 7 <= pm <= 13, (round(pm, 1) if pm is not None else None, pm_txt))
    _dn = Image.new("RGB", im.size); _dn.paste(im, (0, -_dy10))
    pm2, _ = t._pitch_from_vshift(im, _dn); check("上下位移換算 低頭 10 度", pm2 is not None and -13 <= pm2 <= -7, pm2)
if im is not None:
    _zm = im.crop((W // 8, H // 8, W - W // 8, H - H // 8)).resize((W, H))
    _pz = t._patch_zoom(im, _zm, 0.5); check("走 旁邊東西放大（_patch_zoom 1.33）", bool(_pz) and all(1.15 <= x["r"] <= 1.55 for x in _pz), [(x["名稱"][:6], x["r"]) for x in _pz])
if im is not None:
    T.TOOLFIX = True
    _z, _n = t._walk_zoom(im, _zm, 0.5, 30.0); check("走 量測鏈 _walk_zoom 1.33", _z is not None and 1.15 <= _z <= 1.55, (_z, _n))
_rs = '我想一下……先走。 {"看到":"門在中央","想":"因為x｜所以要y｜兩步:z","先記":[],"下兩步":[{"做":"走(1.0)","結果應該是":"到門前"}]} {"做":"視角移動到(0.85,0.55)","結果應該是":"對正"}'
_o = T.json_from_reason(_rs); check("JSON 寫在推理裡→撈出", _o.get("看到") == "門在中央" and len(_o.get("下兩步", [])) == 1, _o)
check("推理沒 JSON→空", T.json_from_reason("只有文字沒有物件") == {})
_ui = T.Tools._ui_words(["辦理入住", "出口", "輪班 4", "$ 271", "理智", "保安", "557", ") 561", "（辦理入住）", "E"]); check("面板按鈕字（非 HUD、去 OCR 雜訊）", _ui == ["辦理入住", "出口"], _ui)
check("最近兩輪有互動（前一輪）", T._acted_last([{"用": ["走(0.5)", "長按(e,1.5)"]}, {"用": ["走(0.5)"]}]) == "長按")
check("最近兩輪沒互動", T._acted_last([{"用": ["走(0.5)", "視角移動到(0.7,0.4)"]}, {"用": ["轉(30)"]}]) == "")
check("三輪前的互動不算", T._acted_last([{"用": ["長按(e,1.5)"]}, {"用": ["走(0.5)"]}, {"用": ["走(0.5)"]}]) == "")
check("第一輪不提醒", T._acted_last([]) == "？")
n, p = T._parse_call("檢討(對正拍照字,視角移動到(0.91,0.21)對到中央,視角移動到(0,9)截斷沒執行,工具名寫錯,照清單寫視角移動到(x,y))", cat)
check("parse 括號內逗號不切", p.get("下次") == "照清單寫視角移動到(x,y)" and p.get("預期") == "視角移動到(0.91,0.21)對到中央", p)
n2, p2 = T._parse_call("視角移動到(0.91,0.21)", cat); check("parse 視角移動到 兩參數", p2.get("x") == "0.91" and p2.get("y") == "0.21", p2)
if im is not None:
    import numpy as _np3, sys as _sys3
    _sys3.path.insert(0, "工具版")
    import 獨立驗證 as _V
    _g = _np3.array(im.convert("L"))
    _rot = Image.fromarray(_V.synth_rotate(_g, 18.0, 0.0))
    _fy, _fp, _fn, _fc, _ft = t._feat_deg(im.convert("L"), _rot)
    check("特徵點法 合成右轉 18 度", _fy is not None and 16 <= _fy <= 20 and _fc >= 0.5, (_fy, _fn, _fc, _ft))
    _lm, _lt, _sc = t._landmark_verify(im.convert("L"), _rot, 20.0)
    check("_landmark_verify 特徵點優先", getattr(t, "_feat_used", False) and _lm is not None and 16 <= _lm <= 20 and "特徵點" in _lt, (_lm, _lt[:120]))
    _fy0, *_ = t._feat_deg(im.convert("L"), im.convert("L"))
    check("特徵點法 沒轉→0", _fy0 is not None and abs(_fy0) < 1, _fy0)
import time as _tm
t._eta = (_tm.time() - 30, "中央那個東西還要走約 8.3 秒才到"); _en = t._eta_note()
check("長按沒反應帶上上次走的距離", "8.3 秒" in _en and "30 秒前" in _en, _en)
t._eta = (_tm.time() - 400, "x"); check("超過 3 分鐘不帶", t._eta_note() == "")
# 第 30 輪：分段少轉就補轉一次（模擬每次拖曳只轉到 80%）
_sent = []
_bak = {k: getattr(t, k) for k in ("live", "image_path", "grab", "_settled", "diff", "_shift", "_drag", "_landmark_turn", "_scene_deg", "_feat_deg", "_repetitive", "_verify_dump")}
try:
    t.live, t.image_path = True, None
    t.grab = lambda: im; t._settled = lambda prev=None: im; t.diff = lambda a, b: 30.0; t._shift = lambda a, b, d: (0.1, "")
    t._drag = lambda dx, dy, fy=0.5: _sent.append(dx / t.dx_deg)
    t._landmark_turn = lambda *a, **k: (None, "沒標點"); t._scene_deg = lambda a, b: (None, ""); t._repetitive = lambda a: (False, "")
    t._feat_deg = lambda a, b: (0.8 * _sent[-1], 0.0, 100, 0.9, "特徵點"); t._verify_dump = lambda *a, **k: None
    _m, _p, _d, _st, _lu = t._turn_chunks(34.0)
    check("分段少轉→補轉一次", "補轉 +7→實測 +5" in _p and abs(_m - 32.6) < 0.3 and abs(_d - 40.8) < 0.3 and not _st, (_p, round(_m, 1), round(_d, 1)))
    _sent.clear(); t._feat_deg = lambda a, b: (0.95 * _sent[-1], 0.0, 100, 0.9, "特徵點")
    _m, _p, _d, _st, _lu = t._turn_chunks(34.0)
    check("差在容許內不補轉", "補轉" not in _p and abs(_d - 34.0) < 0.1, _p)
finally:
    for k, v in _bak.items():
        setattr(t, k, v)
# 02:0x：補轉後 look_at 不能說「只轉了」
_bak = {k: getattr(t, k) for k in ("live", "image_path", "grab", "_settled", "diff", "_shift", "_drag", "_landmark_turn", "_scene_deg", "_feat_deg", "_repetitive", "_verify_dump")}
try:
    _sent = []
    t.live, t.image_path = True, None
    t.grab = lambda: im; t._settled = lambda prev=None: im; t.diff = lambda a, b: 30.0; t._shift = lambda a, b, d: (0.1, "")
    t._drag = lambda dx, dy, fy=0.5: _sent.append(dx / t.dx_deg)
    t._landmark_turn = lambda *a, **k: (None, "沒標點"); t._scene_deg = lambda a, b: (None, ""); t._repetitive = lambda a: (False, "")
    t._feat_deg = lambda a, b: (0.8 * _sent[-1], 0.0, 100, 0.9, "特徵點"); t._verify_dump = lambda *a, **k: None
    _r = t.look_at(0.2, 0.52)
    check("補轉後不說只轉了", "補轉" in _r and "只轉了" not in _r, _r[:300])
except Exception as _e:
    check("補轉後不說只轉了（跑不起來）", False, repr(_e)[:200])
finally:
    for k, v in _bak.items():
        setattr(t, k, v)
if im is not None:
    _rot45 = Image.fromarray(_V.synth_rotate(_np3.array(im.convert("L")), 45.0, 0.0))
    _ly, _lp, _ln, _lc, _ld = _V.estimate_yaw_pitch_loftr(im.convert("L"), _rot45)
    check("LoFTR 退路 合成右轉 45 度", _ly is not None and 43 <= _ly <= 47 and _lc >= 0.5, (_ly, _ln, _lc, _ld))
print("ALL PASS" if ok else "SOME FAIL"); sys.exit(0 if ok else 1)
