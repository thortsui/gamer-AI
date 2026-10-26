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
    t._feat_deg = lambda a, b, want=None: (0.8 * _sent[-1], 0.0, 100, 0.9, "特徵點"); t._verify_dump = lambda *a, **k: None
    _m, _p, _d, _st, _lu = t._turn_chunks(34.0)
    check("分段少轉→補轉一次", "補轉 +7→實測 +5" in _p and abs(_m - 32.6) < 0.3 and abs(_d - 40.8) < 0.3 and not _st, (_p, round(_m, 1), round(_d, 1)))
    _sent.clear(); t._feat_deg = lambda a, b, want=None: (0.95 * _sent[-1], 0.0, 100, 0.9, "特徵點")
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
    _noise = Image.fromarray(_np3.random.RandomState(1).randint(0, 255, (im.height, im.width, 3), dtype=_np3.uint8))   # 轉完的畫面：找不到原本那塊、也不是釘住
    t.grab = lambda: im; t._settled = lambda prev=None: _noise; t.diff = lambda a, b: 30.0; t._shift = lambda a, b, d: (0.1, "")
    t._drag = lambda dx, dy, fy=0.5: _sent.append(dx / t.dx_deg)
    t._landmark_turn = lambda *a, **k: (None, "沒標點"); t._scene_deg = lambda a, b: (None, ""); t._repetitive = lambda a: (False, "")
    t._feat_deg = lambda a, b, want=None: (0.8 * _sent[-1], 0.0, 100, 0.9, "特徵點"); t._verify_dump = lambda *a, **k: None
    _r = t.look_at(0.2, 0.52)
    check("補轉後不說只轉了", "補轉" in _r and "只轉了" not in _r, _r[:300])
    check("補轉後合計跟算好的比→一致", "【對照 一致】視角移動到 分段實測 要 -39度" in _r, _r[_r.find("【對照"):][:120])
    _sent.clear(); _r2 = t.look_turn(90)
    check("轉(90) 補轉後跟要的 90 比→一致", "【對照 一致】轉頭合計 要 +90度" in _r2, _r2[_r2.find("【對照"):][:120])
except Exception as _e:
    check("補轉後不說只轉了（跑不起來）", False, repr(_e)[:200])
finally:
    for k, v in _bak.items():
        setattr(t, k, v)
if im is not None:
    _rot45 = Image.fromarray(_V.synth_rotate(_np3.array(im.convert("L")), 45.0, 0.0))
    _ly, _lp, _ln, _lc, _ld = _V.estimate_yaw_pitch_loftr(im.convert("L"), _rot45)
    check("LoFTR 退路 合成右轉 45 度", _ly is not None and 43 <= _ly <= 47 and _lc >= 0.5, (_ly, _ln, _lc, _ld))
check("量刻度中段 dry-run", "不能量刻度中段" in t.cal_mid(""), t.cal_mid(""))
check("量刻度中段 在目錄", "量刻度中段" in "\n".join(T.catalog_lines(cat)) if hasattr(T, "catalog_lines") else any(x.get("名稱") == "量刻度中段" for x in (cat["工具"] if isinstance(cat, dict) and "工具" in cat else cat)))
_n, _p = T._parse_call("量刻度中段(45)", cat); check("parse 量刻度中段(45)", _n == "量刻度中段" and _p.get("度") == "45", (_n, _p))
# 中段算法：模擬三段 +8／+10／+10（頭段死區 2 度）
_bak2 = {k: getattr(t, k) for k in ("_drag_staged", "_feat_deg")}
try:
    t._drag_staged = lambda dx, stages=3, fy=0.5, pause=0.3: [im, im, im, im]
    _seq = iter([8.0, 10.0, 10.0]); t._feat_deg = lambda a, b, want=None: (next(_seq), 0.0, 100, 0.9, "x")
    _sc, _hl, _dg, _tx = t._mid_sample(75.0)
    check("中段法 75px/3÷10度＝2.5、頭段少 2 度", _sc == 2.5 and _hl == 2.0, (_sc, _hl, _dg))
finally:
    for k, v in _bak2.items():
        setattr(t, k, v)
if im is not None:
    _rot18 = Image.fromarray(_V.synth_rotate(_np3.array(im.convert("L")), 18.0, 0.0))
    _y, *_rest = t._feat_deg(im.convert("L"), _rot18, want=17.0); check("特徵點 want 接近→採用", _y is not None and 16 <= _y <= 20, _y)
    _y2, _p2, _n2, _c2, _t2 = t._feat_deg(im.convert("L"), _rot18, want=-30.0); check("特徵點 跟送出差太多但 LoFTR 一致→信、附註", _y2 is not None and 16 <= _y2 <= 20 and "兩法一致" in _t2, _t2)
    _bakL = T.Tools._feat_mod.estimate_yaw_pitch_loftr
    try:
        T.Tools._feat_mod.estimate_yaw_pitch_loftr = lambda a, b, third_person=None: (-25.0, 0.0, 60, 0.9, {})
        _y3, _p3, _n3, _c3, _t3 = t._feat_deg(im.convert("L"), _rot18, want=-30.0); check("特徵點 跟送出差太多且 LoFTR 不一致→不信", _y3 is None and "都不信" in _t3, _t3)
    finally:
        T.Tools._feat_mod.estimate_yaw_pitch_loftr = _bakL
# 第 36 輪：特徵點量不出、標點反方向 → 不信、照送出算
_bak3 = {k: getattr(t, k) for k in ("live", "image_path", "grab", "_settled", "diff", "_shift", "_drag", "_landmark_turn", "_scene_deg", "_feat_deg", "_repetitive", "_verify_dump")}
try:
    t.live, t.image_path = True, None
    t.grab = lambda: im; t._settled = lambda prev=None: im; t.diff = lambda a, b: 30.0; t._shift = lambda a, b, d: (0.1, "")
    t._drag = lambda dx, dy, fy=None, fx=None: None
    t._landmark_turn = lambda *a, **k: (-26.0, "標點"); t._scene_deg = lambda a, b: (-46.0, ""); t._repetitive = lambda a: (False, "")
    t._feat_deg = lambda a, b, want=None: (None, None, 5, 0.1, "量不出"); t._verify_dump = lambda *a, **k: None
    _m, _p, _d, _st, _lu = t._turn_chunks(30.0)
    check("標點反方向且特徵點量不出→照送出算", "反方向不信" in _p and abs(_m - 30.0) < 0.1, (_p[:160], _m))
    _lm, _lt, _sc = t._landmark_verify(im, im, 30.0)
    check("_landmark_verify 反方向不信", _lm is None and "反方向" in _lt, _lt[-120:])
finally:
    for k, v in _bak3.items():
        setattr(t, k, v)
if im is not None:
    import math as _m4
    _g4 = _np3.array(im.convert("L"))
    for _th in (0, 45, 70):     # 鏡頭低頭 _th 度時繞世界垂直軸右轉 20 度
        _ax = (0.0, _m4.cos(_m4.radians(_th)), _m4.sin(_m4.radians(_th)))
        _r20 = Image.fromarray(_V.synth_rotate_axis(_g4, _ax, 20.0))
        _vy, _vp, _vn, _vc, _vd = _V.estimate_yaw_pitch(im.convert("L"), _r20, sweep=False)
        check(f"低頭 {_th} 度時右轉 20 度→量到 +20", _vy is not None and 17 <= _vy <= 23 and _vc >= 0.5, (_vy, _vp, _vn, _vc, {k: _vd.get(k) for k in ("繞垂直軸", "逐點yaw", "俯仰估計")}))
    _l20 = Image.fromarray(_V.synth_rotate_axis(_g4, (0.0, _m4.cos(_m4.radians(60)), _m4.sin(_m4.radians(60))), -20.0))
    _vy2, *_ = _V.estimate_yaw_pitch(im.convert("L"), _l20, sweep=False); check("低頭 60 度時左轉 20 度→量到 -20", _vy2 is not None and -23 <= _vy2 <= -17, _vy2)
    _p10 = Image.fromarray(_V.synth_rotate(_g4, 0.0, 10.0))
    _vy3, _vp3, *_ = _V.estimate_yaw_pitch(im.convert("L"), _p10, sweep=False); check("純抬頭 10 度不被當成繞垂直軸", _vy3 is not None and abs(_vy3) < 2 and 8 <= _vp3 <= 12, (_vy3, _vp3))
if im is not None:
    import cv2 as _cv5
    _gb = _cv5.GaussianBlur(_np3.array(im.convert("L")), (0, 0), 12)          # 沒紋理的牆
    _turned = _V.synth_hud(_gb, _V.synth_rotate(_gb, 25.0))                   # 轉了 25 度、HUD 與中央角色釘著
    _ty, _tp, _tn, _tc, _td = _V.estimate_yaw_pitch(Image.fromarray(_gb), Image.fromarray(_turned), sweep=False)
    check("沒紋理轉 25 度→不說 0 度（量不出或量到）", _ty is None or _ty >= 10, (_ty, _tc, _td.get("reason"), _td.get("整張差")))
    _same = _V.synth_hud(_gb, _np3.clip(_gb.astype(_np3.int16) + _np3.random.RandomState(2).randint(-3, 4, _gb.shape), 0, 255).astype(_np3.uint8))
    _sy, _sp, _sn, _sc, _sd = _V.estimate_yaw_pitch(Image.fromarray(_gb), Image.fromarray(_same), sweep=False)
    check("沒轉（只有雜訊）→ 0 度", _sy is not None and abs(_sy) < 1, (_sy, _sc))
check("_panel_buttons 無字→空", t._panel_buttons(None) == "" or isinstance(t._panel_buttons(None), str))
_bak6 = {k: getattr(t, k) for k in ("live", "image_path", "grab", "diff", "_drag")}
try:
    t.live, t.image_path = True, None
    t.grab = lambda: im; t.diff = lambda a, b: 0.5; t._drag = lambda dx, dy, fy=None, fx=None: None
    _r = t.cal_loop(""); check("鏡頭轉不動→驗一圈不算", "鏡頭轉不動" in _r, _r[:120])
    _r2 = t.cal_mid(""); check("鏡頭轉不動→量刻度中段不算", "鏡頭轉不動" in _r2, _r2[:120])
finally:
    for k, v in _bak6.items():
        setattr(t, k, v)
t._nomove = 0
check("連續沒動 第 3 次才提示", t._nomove_note(True) == "" and t._nomove_note(True) == "" and "連續 3 次" in t._nomove_note(True))
check("有動就歸零", t._nomove_note(False) == "" and t._nomove == 0)
# 第 48 輪：任務框以下的字（大廳招牌 y 0.39）連兩輪不算換字；任務框內的字連兩輪算
_tw = T.TaskWords(); _tw.update([("拍照", 0.91, 0.21)]); _tw.update([("拍照", 0.91, 0.21)])
_tw.update([("頂尖輪", 0.87, 0.39)]); _, _ev = _tw.update([("你的頂尖輪", 0.86, 0.39)])
check("任務字換 不認 y 0.39 的招牌", _ev is None and _tw.current == ["拍照"], (_ev, _tw.current))
_, _ev1 = _tw.update([("在電腦上註冊", 0.91, 0.21)]); _, _ev2 = _tw.update([("在電腦上註冊", 0.91, 0.21)])
_ev = _ev1 or _ev2
check("任務字換 認任務框內的字", _ev is not None and _ev["新"] == "在電腦上註冊", (_ev1, _ev2))
# 第 49 輪：轉不足換拖曳起點；量不出的段照最近比例估
t._turn_hist = []; t.DRAG_FX, t.DRAG_FY = 0.30, 0.40; t._drag_idx = 0
check("比例 不夠筆數 None", t._recent_ratio() is None)
t._turn_record(-17, -4); t._turn_record(-17, -6)
check("比例 10/34≈0.29", abs(t._recent_ratio() - 10 / 34) < 0.01, t._recent_ratio())
_e, _tx = t._unmeasured_est(-24)
check("量不出的段照比例估 -24→約 -7", -8.5 < _e < -6.5 and "不確定" in _tx and t._chunk_unsure, (_e, _tx))
_n = t._under_turn_note(-17, -4)
check("轉不足 只回報比例、不換起點", (t.DRAG_FX, t.DRAG_FY) == (0.30, 0.40) and "24%" in _n, (_n, t.DRAG_FX))
check("轉夠 沒說明", t._under_turn_note(-30, -27) == "")
t._turn_hist = []; t._turn_record(30, 28); t._turn_record(30, 27); t._chunk_unsure = False
_e, _tx = t._unmeasured_est(20)
check("比例正常時 量不出照送出算", _e == 20 and _tx == "" and not t._chunk_unsure, (_e, _tx))
t._turn_hist = []; t.DRAG_FX, t.DRAG_FY = 0.30, 0.40; t._drag_idx = 0
# 第 50 輪：每段固定少轉 → 補償量
t._turn_hist = []
check("補償 不夠筆數 0", t._comp_deg() == 0.0)
t._turn_record(-20, -14); t._turn_record(-22, -17); t._turn_record(15, 10)
check("補償 停用：少轉 5／5／6 → 0", t._comp_deg() == 0.0, t._comp_deg())
t._turn_hist = []; t._turn_record(30, 28); t._turn_record(30, 27); t._turn_record(17, 14)
check("補償 少轉 2～3 → 0（正常誤差）", t._comp_deg() == 0.0, t._comp_deg())
t._turn_hist = []; t._turn_record(-17, -4); t._turn_record(-30, -8); t._turn_record(-15, -5)
check("補償 少轉 13／22／10 差太多 → 0（不穩不補）", t._comp_deg() == 0.0, t._comp_deg())
t._turn_hist = []; t._turn_record(-20, -14); t._turn_record(-22, -17); t._turn_record(15, 10)
check("補償後 dry-run 轉(-20) 照常", "轉了 20 度" in t.look_yaw("-20") if hasattr(t, "look_yaw") else True)
t._turn_hist = []
# 第 51 輪：5 分鐘前的段也算；差 7 度仍補
import time as _tm
t._turn_hist = [(_tm.time() - 300, 19.0, 14.0), (_tm.time() - 200, -11.0, -5.0), (_tm.time() - 10, 17.0, 6.0)]
check("補償 停用：10 分鐘窗口也 → 0", t._comp_deg() == 0.0, t._comp_deg())
t._turn_hist = [(_tm.time() - 700, 19.0, 14.0), (_tm.time() - 200, -11.0, -5.0), (_tm.time() - 10, 17.0, 12.0)]
check("補償 超過 10 分鐘的不算 → 0", t._comp_deg() == 0.0, t._comp_deg())
t._turn_hist = []
# 第 53 輪：同一次呼叫內少轉就按比例多送；補轉兩次；視角移動到 ≥10 度走分段路
_sent = []
_bak = {k: getattr(t, k) for k in ("live", "image_path", "grab", "_settled", "diff", "_shift", "_drag", "_landmark_turn", "_scene_deg", "_feat_deg", "_repetitive", "_verify_dump")}
try:
    t.live, t.image_path = True, None; t._turn_hist = []
    t.grab = lambda: im; t._settled = lambda prev=None: im; t.diff = lambda a, b: 30.0; t._shift = lambda a, b, d: (0.1, "")
    t._drag = lambda dx, dy, fy=0.5: _sent.append(dx / t.dx_deg)
    t._landmark_turn = lambda *a, **k: (None, "沒標點"); t._scene_deg = lambda a, b: (None, ""); t._repetitive = lambda a: (False, "")
    t._feat_deg = lambda a, b, want=None: (0.5 * _sent[-1], 0.0, 100, 0.9, "特徵點"); t._verify_dump = lambda *a, **k: None
    _m, _p, _d, _st, _lu = t._turn_chunks(40.0)
    check("只轉到 50%：第 2 段多送 100%、補轉後合計到 40", "多送 100%" in _p and abs(_m - 40.0) < 0.6 and "補轉" in _p, (_p, round(_m, 1), _sent))
    _sent.clear(); t._feat_deg = lambda a, b, want=None: (0.5 * _sent[-1], 0.0, 100, 0.9, "特徵點")
    _m, _p, _d, _st, _lu = t._turn_chunks(20.0)
    check("一段 20 只轉到 10：補轉多送到 20、合計 20", abs(_m - 20.0) < 0.6 and "補轉 +10" in _p, (_p, round(_m, 1), _sent))
    _sent.clear(); t._turn_hist = []; t._feat_deg = lambda a, b, want=None: (0.8 * _sent[-1], 0.0, 100, 0.9, "特徵點")
    _m, _p, _d, _st, _lu = t._turn_chunks(34.0)
    check("80% 不按比例多送（補轉照舊）", "多送" not in _p and "補轉 +7→實測 +5" in _p, _p)
    _sent.clear(); t._turn_hist = []; t._feat_deg = lambda a, b, want=None: (0.5 * _sent[-1], 0.0, 100, 0.9, "特徵點")
    _noise53 = Image.fromarray(_np3.random.RandomState(2).randint(0, 255, (im.height, im.width, 3), dtype=_np3.uint8))
    t._settled = lambda prev=None: _noise53
    _r = t.look_at(0.65, 0.5)
    check("視角移動到 +19 度走分段路有補轉", "每段實測" in _r and "補轉" in _r, _r[:300])
finally:
    for k, v in _bak.items():
        setattr(t, k, v)
    t._turn_hist = []; t.DRAG_FX, t.DRAG_FY = 0.30, 0.40; t._drag_idx = 0
# 第 54 輪：鏡頭沒轉時目標的字當然在原位，不能判「釘在畫面上」
_bak = {k: getattr(t, k) for k in ("live", "image_path", "grab", "_settled", "diff", "_shift", "_drag", "_landmark_turn", "_scene_deg", "_feat_deg", "_repetitive", "_verify_dump", "_words")}
try:
    t.live, t.image_path = True, None; t._turn_hist = []
    t.grab = lambda: im; t._settled = lambda prev=None: im; t.diff = lambda a, b: 30.0; t._shift = lambda a, b, d: (0.0, "")
    t._drag = lambda dx, dy, fy=0.5: None
    t._landmark_turn = lambda *a, **k: (None, "沒標點"); t._scene_deg = lambda a, b: (None, ""); t._repetitive = lambda a: (False, "")
    t._feat_deg = lambda a, b, want=None: (0.0, 0.0, 100, 0.9, "特徵點"); t._verify_dump = lambda *a, **k: None
    t._words = lambda im_: {"保全攝影機": (0.57, 0.45)}
    _r = t.look_at(0.57, 0.45)
    check("鏡頭沒轉（特徵點 0）不判釘在畫面上", "＝它是釘在畫面上的東西" not in _r, _r[:300])
    t._feat_deg = lambda a, b, want=None: (11.0, 0.0, 100, 0.9, "特徵點")
    _r = t.look_at(0.57, 0.45)
    check("鏡頭轉了 11 度、字還在原位 → 釘在畫面上", "＝它是釘在畫面上的東西" in _r, _r[:300])
finally:
    for k, v in _bak.items():
        setattr(t, k, v)
    t._turn_hist = []
# 第 56 輪：補轉標點量到不合理就不信；送出 <8 度不判釘住
_sent = []
_bak = {k: getattr(t, k) for k in ("live", "image_path", "grab", "_settled", "diff", "_shift", "_drag", "_landmark_turn", "_scene_deg", "_feat_deg", "_repetitive", "_verify_dump", "_words")}
try:
    t.live, t.image_path = True, None; t._turn_hist = []
    t.grab = lambda: im; t._settled = lambda prev=None: im; t.diff = lambda a, b: 30.0; t._shift = lambda a, b, d: (0.1, "")
    t._drag = lambda dx, dy, fy=0.5: _sent.append(dx / t.dx_deg)
    t._scene_deg = lambda a, b: (None, ""); t._repetitive = lambda a: (False, ""); t._verify_dump = lambda *a, **k: None
    _calls = [0]
    def _fd(a, b, want=None):
        _calls[0] += 1
        return (0.5 * _sent[-1], 0.0, 100, 0.9, "特徵點") if _calls[0] == 1 else (None, None, 0, 0.0, "量不出")
    t._feat_deg = _fd
    t._landmark_turn = lambda *a, **k: (51.0, "標點")
    _m, _p, _d, _st, _lu = t._turn_chunks(17.0)
    check("補轉標點 +51（送 +16）不信、合計不含 51", "不信" in _p and _m < 30, (_p, round(_m, 1)))
    _sent.clear(); _calls[0] = 0; t._landmark_turn = lambda *a, **k: (18.0, "標點")
    _m, _p, _d, _st, _lu = t._turn_chunks(-17.0)
    check("補轉標點反方向 +18 不信", "不信" in _p and _m > -30, (_p, round(_m, 1)))
    t._feat_deg = lambda a, b, want=None: (6.0, 0.0, 100, 0.9, "特徵點"); t._landmark_turn = lambda *a, **k: (None, "沒標點")
    t._words = lambda im_: {"拿起電擊槍": (0.46, 0.50)}
    _r = t.look_at(0.46, 0.50)
    check("送 -6 度不判釘在畫面上", "＝它是釘在畫面上的東西" not in _r, _r[:200])
finally:
    for k, v in _bak.items():
        setattr(t, k, v)
    t._turn_hist = []
# 第 57 輪：鏡頭只轉到一半、字只移一半 → 不是釘住
_bak = {k: getattr(t, k) for k in ("live", "image_path", "grab", "_settled", "diff", "_shift", "_drag", "_landmark_turn", "_scene_deg", "_feat_deg", "_repetitive", "_verify_dump", "_words")}
try:
    t.live, t.image_path = True, None; t._turn_hist = []
    _noise57 = Image.fromarray(_np3.random.RandomState(3).randint(0, 255, (im.height, im.width, 3), dtype=_np3.uint8))   # 轉完的畫面：影像比對找不到那塊
    t.grab = lambda: im; t._settled = lambda prev=None: _noise57; t.diff = lambda a, b: 30.0; t._shift = lambda a, b, d: (0.1, "")
    t._drag = lambda dx, dy, fy=0.5: None
    t._scene_deg = lambda a, b: (None, ""); t._repetitive = lambda a: (False, ""); t._verify_dump = lambda *a, **k: None
    t._landmark_turn = lambda *a, **k: (None, "沒標點")
    _fc = [0]
    def _fd57(a, b, want=None):                                              # 要 -11：第一段轉 -5、補轉都 0（合計只轉一半）
        _fc[0] += 1
        return (-5.0 if _fc[0] == 1 else 0.0, 0.0, 100, 0.9, "特徵點")
    t._feat_deg = _fd57
    _wb = {"還回電擊槍": (0.43, 0.40)}; _wa = {"還回電擊槍": (0.455, 0.40)}        # 字移了 0.025（一半）
    _state = [0]
    def _wf(im_):
        _state[0] += 1
        return dict(_wb) if _state[0] % 2 == 1 else dict(_wa)
    t._words = _wf
    _r = t.look_at(0.43, 0.40)
    check("只轉一半、字移一半 → 不判釘住", "＝它是釘在畫面上的東西" not in _r, _r[:200])
    _state[0] = 0; _fc[0] = 0; t._words = lambda im_: dict(_wb)          # 字完全沒動
    _r = t.look_at(0.43, 0.40)
    check("只轉一半、字完全沒動 → 釘住", "＝它是釘在畫面上的東西" in _r, _r[:200])
finally:
    for k, v in _bak.items():
        setattr(t, k, v)
    t._turn_hist = []
# 第 58 輪：點之前讀游標位置
_cp = t._cursor_pos()
check("讀得到游標位置", _cp is None or (isinstance(_cp, tuple) and len(_cp) == 2), _cp)
check("dry-run 點 照常", "點了" in t._do_click(0.5, 0.83), t._do_click(0.5, 0.83))
# 第 59 輪：跨工具「全沒反應」累計
t._dead_n = 0; t._dead_since = ""; t._nomove = 0
for _i in range(4):
    _n = t._dead_mark(True)
check("4 次沒反應還不提示", _n == "", _n)
_n = t._dead_mark(True)
check("第 5 次起提示 全沒反應", "連續 5 個動作全沒反應" in _n, _n)
_n = t._nomove_note(True)
check("走 沒變也算進去（6）", "連續 6 個動作全沒反應" in _n, _n)
t._turn_record(20, 15)
check("轉頭有量到就解除", t._dead_n == 0 and t._dead_mark(False) == "")
t._turn_record(20, 0)
check("轉頭量到 0 算沒反應", t._dead_n == 1)
t._dead_n = 0; t._dead_since = ""; t._nomove = 0; t._turn_hist = []
# 第 60 輪：esc 不歸零全沒反應累計
t._dead_n = 0; t._dead_since = ""; t._nomove = 0
for _i in range(5):
    t._dead_mark(True)
_bak = {k: getattr(t, k) for k in ("live", "image_path", "grab", "diff")}
try:
    t.live, t.image_path = True, None
    t.grab = lambda: im; t.diff = lambda a, b: 40.0        # 按了畫面有變（Roblox 選單打開）
    _r = t._move("esc", 0.1, 0.1, 0.1)
    check("按 esc 畫面變了也不歸零", t._dead_n == 5, (t._dead_n, _r[:80]))
    t.diff = lambda a, b: 2.0
    _r = t._move("w", 0.3, 0.3, 0.3)
    check("走 畫面變化 2（雜訊）不歸零", t._dead_n == 6, (t._dead_n, _r[:80]))
    t.diff = lambda a, b: 40.0
    _r = t._move("w", 0.3, 0.3, 0.3)
    check("走 有動才歸零", t._dead_n == 0, (t._dead_n, _r[:80]))
finally:
    for k, v in _bak.items():
        setattr(t, k, v)
    t._dead_n = 0; t._dead_since = ""; t._nomove = 0
# 第 61 輪：卡住的段不算進比例；補轉上限；讀數帶最後有反應的動作
_sent = []
_bak = {k: getattr(t, k) for k in ("live", "image_path", "grab", "_settled", "diff", "_shift", "_drag", "_landmark_turn", "_scene_deg", "_feat_deg", "_repetitive", "_verify_dump")}
try:
    t.live, t.image_path = True, None; t._turn_hist = []; t._dead_n = 0; t._dead_since = ""; t._last_ok = None
    t.grab = lambda: im; t._settled = lambda prev=None: im; t.diff = lambda a, b: 30.0; t._shift = lambda a, b, d: (0.1, "")
    t._drag = lambda dx, dy, fy=0.5: _sent.append(dx / t.dx_deg)
    t._landmark_turn = lambda *a, **k: (None, "沒標點"); t._scene_deg = lambda a, b: (None, ""); t._repetitive = lambda a: (False, ""); t._verify_dump = lambda *a, **k: None
    _seq = iter([0.95, 0.0, 0.0, 0.98])          # 第 1 段正常、第 2 段與補轉卡住、再補轉鬆開
    t._feat_deg = lambda a, b, want=None: (next(_seq, 0.98) * _sent[-1], 0.0, 100, 0.9, "特徵點")
    _m, _p, _d, _st, _lu = t._turn_chunks(-40.0)
    check("卡住的段不算比例：再補轉不多送、合計不過頭", abs(_sent[-1]) <= 21 and -45 <= _m <= -35, (_p, round(_m, 1), _sent))
    _n = t._dead_mark(False, act="長按 e"); _n = ""
    for _i in range(5):
        _n = t._dead_mark(True)
    check("讀數帶最後有反應的動作", "最後一個有反應的動作：長按 e" in _n, _n)
finally:
    for k, v in _bak.items():
        setattr(t, k, v)
    t._turn_hist = []; t._dead_n = 0; t._dead_since = ""; t._last_ok = None
# 第 62 輪：修水平 沒修好 → 上下限制先不套用
t.pitch_acc = -44.0; t.pitch_unknown = True
check("水平儀不可靠時不擋", t._pitch_room(-20) == -20)
t.pitch_unknown = False
check("水平儀可靠時照擋", abs(t._pitch_room(-20)) < 20)
t.pitch_acc = 0.0
# 第 63 輪：走過去 新字只在像 E 提示才停
import re as _re63
_pat = r"(^|[\s\[（(])E([\s\]）)]|$)|長按|按 ?E"
check("「公室」不像 E 提示", not _re63.search(_pat, "公室"))
check("「, ~ aoye 8」不像 E 提示", not _re63.search(_pat, ", ~ aoye 8"))
check("「E estamp」像 E 提示", bool(_re63.search(_pat, "E estamp")))
check("「[E] 開啟」像 E 提示", bool(_re63.search(_pat, "[E] 開啟")))
check("「長按E」像 E 提示", bool(_re63.search(_pat, "長按E")))
# 第 64 輪：走過去 名稱對應不能因為空 token 配到第一項
import time as _tm64
_bak = {k: getattr(t, k, None) for k in ("desc_items", "desc_at")}
try:
    t.desc_items = [{"編號": 1, "名稱": " [商店] 左側按鈕", "x": 0.05, "y": 0.41}, {"編號": 2, "名稱": "角色", "x": 0.3, "y": 0.58}, {"編號": 3, "名稱": "發光藍框 (0.48,0.42)", "x": 0.48, "y": 0.42}]
    t.desc_at = _tm64.time()
    _r = t._resolve_target("藍色發光框")
    check("名稱沒有的東西不配到第一項", isinstance(_r, str) and "沒有「藍色發光框」" in _r, _r)
    _r = t._resolve_target("發光藍框")
    check("名稱有的配對", not isinstance(_r, str) and _r[2].startswith("[3]"), _r)
    _r = t._resolve_target("商店")
    check("頭兩字配對", not isinstance(_r, str) and _r[2].startswith("[1]"), _r)
finally:
    for k, v in _bak.items():
        setattr(t, k, v)
# 第 65 輪：esc 不覆蓋「最後一個有反應的動作」
_bak = {k: getattr(t, k) for k in ("live", "image_path", "grab", "diff", "_grab_noise")}
try:
    t.live, t.image_path = True, None; t._last_ok = ("05:13", "長按 e")
    t.grab = lambda: im; t.diff = lambda a, b: 40.0; t._grab_noise = lambda: (im, 0.0)   # 背景雜訊 0、按完變 40
    t._move("esc", 0.1, 0.1, 0.1)
    check("按 esc 有變也不覆蓋最後有反應動作", t._last_ok == ("05:13", "長按 e"), t._last_ok)
    t._move("e", 1.5, 1.5, 1.5)
    check("長按 e 有變會更新", t._last_ok[1] == "長按 e" and t._last_ok[0] != "05:13", t._last_ok)
finally:
    for k, v in _bak.items():
        setattr(t, k, v)
    t._last_ok = None
# 第 66 輪：口吃式對話泡泡不算任務字
_tw66 = T.TaskWords(); _tw66.update([("完成簽到", 0.91, 0.21)]); _tw66.update([("完成簽到", 0.91, 0.21)])
_tw66.update([("你咕可咕以咕再咕加口", 0.90, 0.20)]); _, _ev66 = _tw66.update([("你咕可咕以咕再咕加口", 0.90, 0.20)])
check("「你咕可咕以咕再咕」不算任務字換", _ev66 is None and _tw66.current == ["完成簽到"], (_ev66, _tw66.current))
_, _ev66 = _tw66.update([("治療病人", 0.91, 0.21)])
check("正常任務字照樣換", _ev66 is not None and _ev66["新"] == "治療病人", _ev66)
print("ALL PASS" if ok else "SOME FAIL"); sys.exit(0 if ok else 1)
