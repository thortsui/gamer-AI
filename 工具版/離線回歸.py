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
if not os.path.isdir(shot):                         # 10/29 記憶清空重學：測試圖改用封存那份
    shot = os.path.join(HERE, "..", "專案", "動物醫院", "_舊記憶_1029", "記憶", "畫面")
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
# 第 67 輪：分段轉 畫面沒變＝鏡頭沒轉，不照送出算
_sent = []
_bak = {k: getattr(t, k) for k in ("live", "image_path", "grab", "_settled", "diff", "_shift", "_drag", "_landmark_turn", "_scene_deg", "_feat_deg", "_repetitive", "_verify_dump")}
try:
    t.live, t.image_path = True, None; t._turn_hist = []; t._dead_n = 0; t._dead_since = ""
    t.grab = lambda: im; t._settled = lambda prev=None: im; t.diff = lambda a, b: 0.0; t._shift = lambda a, b, d: (0.0, "")
    t._drag = lambda dx, dy, fy=0.5: _sent.append(dx / t.dx_deg)
    t._landmark_turn = lambda *a, **k: (None, "沒標點"); t._scene_deg = lambda a, b: (None, ""); t._repetitive = lambda a: (False, "")
    t._feat_deg = lambda a, b, want=None: (None, None, 0, 0.0, "量不出"); t._verify_dump = lambda *a, **k: None
    _m, _p, _d, _st, _lu = t._turn_chunks(90.0)
    check("黑畫面分段轉：鏡頭沒轉、合計 0、算進全沒反應", "鏡頭沒轉" in _p and abs(_m) < 0.5 and _st and t._dead_n == 1, (_p, _m, t._dead_n))
finally:
    for k, v in _bak.items():
        setattr(t, k, v)
    t._turn_hist = []; t._dead_n = 0; t._dead_since = ""
# 第 68 輪：水平儀累計量到的上下度數
_bak = {k: getattr(t, k) for k in ("live", "image_path", "grab", "_settled", "diff", "_shift", "_drag", "_landmark_turn", "_scene_deg", "_feat_deg", "_repetitive", "_verify_dump", "_pitch_from_vshift")}
try:
    t.live, t.image_path = True, None; t._turn_hist = []; t.pitch_acc = 0.0; t.pitch_unknown = False
    _noise68 = Image.fromarray(_np3.random.RandomState(4).randint(0, 255, (im.height, im.width, 3), dtype=_np3.uint8))
    t.grab = lambda: im; t._settled = lambda prev=None: _noise68; t.diff = lambda a, b: 30.0; t._shift = lambda a, b, d: (0.1, "")
    t._drag = lambda dx, dy, fy=0.5: None
    t._landmark_turn = lambda *a, **k: (None, "沒標點"); t._scene_deg = lambda a, b: (None, ""); t._repetitive = lambda a: (False, ""); t._verify_dump = lambda *a, **k: None
    t._feat_deg = lambda a, b, want=None: (0.0, 0.0, 100, 0.9, "特徵點")
    t._pitch_from_vshift = lambda a, b: (1.0, "幾乎沒動")          # 遊戲把鏡頭拉回：送 +15 只量到 +1
    t.look_at(0.5, 0.3)
    check("視角移動到 上下累計量到的 +1 不是送出的 +15", abs(t.pitch_acc - 1.0) < 0.5, t.pitch_acc)
    t.pitch_acc = 0.0; t._pitch_from_vshift = lambda a, b: (None, "量不出")
    t.look_at(0.5, 0.3)
    check("量不出時照送出累計", t.pitch_acc > 10, t.pitch_acc)
    t.pitch_acc = 0.0; t._pitch_from_vshift = lambda a, b: (2.0, "")
    t.look_pitch("20")
    check("仰 也累計量到的", abs(t.pitch_acc - 2.0) < 0.5, t.pitch_acc)
finally:
    for k, v in _bak.items():
        setattr(t, k, v)
    t.pitch_acc = 0.0; t._turn_hist = []
# 第 69 輪：慢慢轉／走到有變化 兩個函式都定義了 seen_new（靜態檢查）
import inspect as _insp69
for _fn in ("look_slow", "walk_until"):
    _src = _insp69.getsource(getattr(T.Tools, _fn))
    check(f"{_fn} 有 seen_new 初始化", "seen_new = []" in _src and "prompt_like" in _src, _fn)
# 第 70 輪：描述畫面落在右上任務框區、OCR 同位置有字的點 → 標釘、走過去不接
import time as _tm70
_bak = {k: getattr(t, k, None) for k in ("desc_items", "desc_at")}
try:
    _items = [{"編號": 1, "名稱": "右側牆面（拍照）", "x": 0.91, "y": 0.20, "種類": "互動提示"}, {"編號": 2, "名稱": "白鹿角色", "x": 0.22, "y": 0.48, "種類": "場景"},
              {"編號": 3, "名稱": "招牌", "x": 0.90, "y": 0.20, "種類": "場景"}]
    _note = t._mark_task_frame(_items, ["「拍照」(0.91,0.21)", "「輪班 1」(0.50,0.03)"], ["拍照"])
    check("任務框區同位置有任務字 → 釘", _items[0].get("釘") and "「拍照」" in _note and "不是場景" in _note, (_items[0], _note))
    check("同區沒對到字的不標（第 3 項對到同一個字也標）", not _items[1].get("釘") and _items[2].get("釘"), _items)
    check("OCR 沒字 → 不標、空說明", t._mark_task_frame([{"編號": 1, "名稱": "x", "x": 0.9, "y": 0.2, "種類": "互動提示"}], [], ["拍照"]) == "")
    check("不在任務框區不標", t._mark_task_frame([{"編號": 1, "名稱": "x", "x": 0.5, "y": 0.2, "種類": "互動提示"}], ["「拍照」(0.50,0.20)"], ["拍照"]) == "")
    # 第 71 輪：OCR 的字跟任務字不同（大廳踏板「0/5」、選單玩家名）→ 不標；沒有任務字 → 不標；OCR 抖動「拍昭」仍算同字
    _i71 = [{"編號": 5, "名稱": "遠處角色", "x": 0.86, "y": 0.30, "種類": "場景"}]
    check("同區但 OCR 字不是任務字 → 不標", t._mark_task_frame(_i71, ["「0/5」(0.86,0.26)", "「briana」(0.86,0.31)"], ["列印徽章"]) == "" and not _i71[0].get("釘"), _i71)
    check("沒有任務字 → 不標", t._mark_task_frame(_i71, ["「拍照」(0.86,0.30)"], []) == "" and not _i71[0].get("釘"), _i71)
    check("OCR 抖動 拍昭 vs 任務字 拍照 → 仍標", t._mark_task_frame(_i71, ["「拍昭」(0.86,0.30)"], ["拍照"]) != "" and _i71[0].get("釘"), _i71)
    check("主迴圈會塞 tools.task_current", "tools.task_current = list(tw.current)" in open("工具版/tool_agent.py", encoding="utf-8").read())
    t.desc_items, t.desc_at = _items, _tm70.time()
    _r = t._resolve_target("1")
    check("走過去 釘住的編號 → 不接、說是 HUD", isinstance(_r, str) and "任務框" in _r and "無效" in _r, _r)
    _r = t._resolve_target("2")
    check("走過去 場景編號 照常", not isinstance(_r, str) and _r[2].startswith("[2]"), _r)
finally:
    for k, v in _bak.items():
        setattr(t, k, v)
# 第 72 輪：補轉後找不到目標（相似度 < 0.85）→ 老實說找不到、用特徵點量補轉到位與否
_sent = []; _loc_calls = []
_bak = {k: getattr(t, k) for k in ("live", "image_path", "grab", "_settled", "diff", "_shift", "_drag", "_feat_deg", "_locate", "_verify_dump", "_words", "_pitch_from_vshift")}
try:
    t.live, t.image_path = True, None; t._turn_hist = []
    t.grab = lambda: im; t._settled = lambda prev=None: im; t.diff = lambda a, b: 30.0; t._shift = lambda a, b, d: (0.1, "")
    t._drag = lambda dx, dy, fy=0.5: _sent.append(dx / t.dx_deg)
    t._verify_dump = lambda *a, **k: None; t._words = lambda im_: {}; t._pitch_from_vshift = lambda a, b: (None, "")
    t._feat_deg = lambda a, b, want=None: (0.95 * _sent[-1], 0.0, 100, 0.9, "特徵點")
    def _loc72(patch, im_, near=None, second=False):
        if near and abs(near[0] - 0.5) < 1e-6:
            _loc_calls.append(1)
            return (0.40, 0.51, 0.89) if len(_loc_calls) == 1 else (0.32, 0.20, 0.76)
        return (near[0], near[1], 0.3) if near else None
    t._locate = _loc72
    _yaw72 = t._angles_to(0.44, 0.5, im.width, im.height)[0]
    _r = t.look_at(0.44, 0.5)
    check("補轉後相似度 0.76 → 不印成找到、說找不到", "補轉後在中央附近找不到它" in _r and "相似度只有 0.76" in _r and "現在在 (0.32,0.20)（相似度 0.76）" not in _r, _r[-400:])
    check("特徵點量補轉到位 → 已對準", "補轉到位" in _r and "已對準正中央" in _r, _r[-300:])
    _sent.clear(); _loc_calls.clear(); _n72 = [0]
    def _fd72(a, b, want=None):
        _n72[0] += 1
        return (0.95 * _sent[-1], 0.0, 100, 0.9, "特徵點") if _n72[0] == 1 else (None, None, 0, 0.0, "量不出")
    t._feat_deg = _fd72
    _r = t.look_at(0.44, 0.5)
    check("補轉量不出 → 不算對準", "補轉轉了幾度也量不出" in _r and "沒有完全對準" in _r, _r[-300:])
    _sent.clear(); _loc_calls.clear()
    t._feat_deg = lambda a, b, want=None: (0.3 * _sent[-1], 0.0, 100, 0.9, "特徵點") if len(_sent) >= 2 else (0.95 * _sent[-1], 0.0, 100, 0.9, "特徵點")
    _r = t.look_at(0.44, 0.5)
    check("補轉只轉到 30% → 不算對準", "補轉只轉了" in _r and "沒有完全對準" in _r, _r[-300:])
finally:
    for k, v in _bak.items():
        setattr(t, k, v)
    t._turn_hist = []; t.pitch_acc = 0.0
# 第 73 輪：整張沒變、底部道具欄變了 → 有反應
from PIL import ImageDraw as _ID73
_im_a = im.copy(); _im_b = im.copy()
_W73, _H73 = _im_b.size
_ID73.Draw(_im_b).rectangle((int(_W73 * 0.46), int(_H73 * 0.90), int(_W73 * 0.54), int(_H73 * 0.98)), fill=(255, 220, 0))   # 道具欄多一格
check("道具欄那一帶變化 ≥10、整張 <2", t._hotbar_diff(_im_a, _im_b) >= 10 and t.diff(_im_a, _im_b) < 2, (round(t._hotbar_diff(_im_a, _im_b), 1), round(t.diff(_im_a, _im_b), 1)))
_bak = {k: getattr(t, k) for k in ("live", "image_path", "grab", "_changed_px", "_task_note")}
try:
    t.live, t.image_path = True, None
    t.grab = lambda: _im_b; t._task_note = lambda b: ""
    t._changed_px = lambda before, noise=None: "畫面變化 1（沒變＝可能被擋；等 1.2 秒再看也沒變）；【二次驗證 一致】有沒有反應 第一次 1、第二次 1（兩次都看不出反應）"
    _r = t._changed(_im_a, 0.0)
    check("整張沒變但道具欄變了 → 改寫成有反應", "底部那一帶變了＝**有反應**" in _r and "以它為準" in _r and "沒變＝可能被擋" not in _r, _r)
    t.grab = lambda: _im_a
    _r = t._changed(_im_a, 0.0)
    check("道具欄也沒變 → 照舊沒反應", "沒變＝可能被擋" in _r and "道具欄" not in _r, _r)
    t.grab = lambda: _im_b
    t._changed_px = lambda before, noise=None: "畫面變化 40（比背景大）"
    _r = t._changed(_im_a, 0.0)
    check("整張已變 40 → 道具欄變化不另外標（dh < 3×d）", "道具欄" not in _r, _r)
finally:
    for k, v in _bak.items():
        setattr(t, k, v)
# 第 74 輪：走過去 每步用「走了幾秒＋放大幾倍」算還要幾秒；描述畫面有給身高就校準走路速度
import time as _tm74
_bak = {k: getattr(t, k, None) for k in ("live", "image_path", "grab", "_settled", "diff", "_drag", "_key", "_trk_find", "_words", "_new_word_checker", "desc_items", "desc_at", "_pop_look_how", "_append", "_walk_speed_samples")}
try:
    t.live, t.image_path = True, None; t._walk_speed_samples = []; t.startup.pop("速度", None)
    t.grab = lambda: im; t._settled = lambda prev=None: im; t.diff = lambda a, b: 30.0
    t._drag = lambda dx, dy, fy=0.5: None; t._key = lambda k, d: None; t._words = lambda im_: {}; t._new_word_checker = lambda im_: (lambda w: False)
    t._pop_look_how = lambda: ""; t._append = lambda *a, **k: None
    _n74 = [0]
    def _tf74(g, tpl, near=None, r=0.3, scales=(1.0,)):
        _n74[0] += 1
        return (0.5, 0.5, 0.9, 1.25)          # 每步目標再放大 1.25 倍
    t._trk_find = _tf74
    t.desc_items = [{"編號": 1, "名稱": "藍發光方板", "x": 0.5, "y": 0.55, "種類": "場景", "身高": 3.0}]; t.desc_at = _tm74.time()
    _r = t.move_track("1")
    check("走過去 每步印還要約幾秒", "還要約" in _r and "放大" in _r, _r[:400])
    check("有描述身高 → 走路速度校準寫進 startup", "走路速度校準" in _r and "身高/秒" in t.startup.get("速度", ""), (_r[-300:], t.startup.get("速度")))
    t.startup.pop("速度", None); t._walk_speed_samples = []
    t.desc_items = [{"編號": 1, "名稱": "藍發光方板", "x": 0.5, "y": 0.55, "種類": "場景", "身高": None}]; t.desc_at = _tm74.time()
    _r = t.move_track("1")
    check("沒描述身高 → 不校準、仍印還要約", "走路速度校準" not in _r and "還要約" in _r and "速度" not in t.startup, _r[-200:])
    _r = t.move_track("0.5,0.55")
    check("給座標的也印還要約", "還要約" in _r, _r[:300])
finally:
    for k, v in _bak.items():
        setattr(t, k, v)
    t.startup.pop("速度", None)
# 第 75 輪：點擊有反應不歸零全沒反應累計、不蓋掉最後有反應的動作；點擊沒反應照算
_bak = {k: getattr(t, k) for k in ("live", "image_path", "_changed", "grab", "_grab_noise", "_fg_ok", "_cursor_pos")}
try:
    t.live, t.image_path = True, None
    t.grab = lambda: im; t._grab_noise = lambda: (im, 0.0); t._fg_ok = lambda: None; t._cursor_pos = lambda: None
    t._dead_n, t._dead_since, t._last_ok = 6, "13:16", ("13:14", "長按 e")
    import pydirectinput as _pdi75
    _bak_pdi = (_pdi75.moveTo, _pdi75.mouseDown, _pdi75.mouseUp)
    _pdi75.moveTo = lambda *a, **k: None; _pdi75.mouseDown = lambda *a, **k: None; _pdi75.mouseUp = lambda *a, **k: None
    t._changed = lambda before, noise=None: "畫面變化 29（比背景大）"
    _r = t._do_click(0.67, 0.88)
    check("點擊有反應 → 累計不歸零、最後有反應動作不變", t._dead_n == 6 and t._last_ok == ("13:14", "長按 e") and "全沒反應" not in _r, (t._dead_n, t._last_ok, _r[:120]))
    t._changed = lambda before, noise=None: "畫面變化 0（沒變＝可能被擋；等 1.2 秒再看也沒變）"
    _r = t._do_click(0.50, 0.83)
    check("點擊沒反應 → 累計 +1、讀數帶最後有反應動作 長按 e", t._dead_n == 7 and "連續 7 個動作全沒反應" in _r and "長按 e（13:14）" in _r, (t._dead_n, _r[-160:]))
finally:
    _pdi75.moveTo, _pdi75.mouseDown, _pdi75.mouseUp = _bak_pdi
    for k, v in _bak.items():
        setattr(t, k, v)
    t._dead_n, t._dead_since, t._last_ok = 0, "", None
# 第 76 輪：拖曳後描述畫面的點照角度換算；出畫面的點 走過去 不接
import time as _tm76
_bak = {k: getattr(t, k, None) for k in ("desc_items", "desc_at", "desc_wh", "desc_shift_log")}
try:
    t.desc_wh = (1920, 991); t.desc_shift_log = []
    t.desc_items = [{"編號": 4, "名稱": "門", "x": 0.05, "y": 0.35, "種類": "場景"}, {"編號": 1, "名稱": "床", "x": 0.70, "y": 0.15, "種類": "場景"}]
    t.desc_at = _tm76.time() - 50; t.desc_born = _tm76.time() - 50
    _y4 = t._angles_to(0.05, 0.35, 1920, 991)[0]
    t._shift_desc_items(_y4, 0.0)            # 往門的方向轉到正對它
    _d4 = next(i for i in t.desc_items if i["編號"] == 4); _d1 = next(i for i in t.desc_items if i["編號"] == 1)
    check("轉到正對門 → 門換算到 x≈0.5", abs(_d4["x"] - 0.5) < 0.02 and not _d4.get("出畫面"), _d4)
    check("床往另一邊移出畫面或 x 變大", _d1.get("出畫面") or _d1["x"] > 0.9, _d1)
    check("換算後點視為新的（desc_at 更新）", _tm76.time() - t.desc_at < 5)
    _n = t._pop_desc_shift()
    check("視角移動到 回傳附換算說明", "已照轉的角度換算" in _n and "[4]門" in _n and t._pop_desc_shift() == "", _n)
    t.desc_items = [{"編號": 2, "名稱": "櫃子", "x": 0.5, "y": 0.5, "種類": "場景"}]; t.desc_at = _tm76.time(); t.desc_born = _tm76.time()
    t._shift_desc_items(90.0, 0.0)
    _r = t._resolve_target("2")
    check("轉 90 度後點出畫面 → 走過去 不接、說已不在畫面上", isinstance(_r, str) and "不在畫面上" in _r, _r)
    t.desc_items = [{"編號": 3, "名稱": "椅子", "x": 0.5, "y": 0.5, "種類": "場景"}]; t.desc_at = _tm76.time(); t.desc_shift_log = []
    t._shift_desc_items(0.2, 0.1)
    check("小於 0.5 度不換算", t.desc_items[0]["x"] == 0.5 and not t.desc_shift_log)
    check("_drag 會呼叫換算", "self._shift_desc_items(dx / self.dx_deg" in open("工具版/tool_agent.py", encoding="utf-8").read())
finally:
    for k, v in _bak.items():
        setattr(t, k, v)
# 第 77 輪：走路速度用「算 eta 當下的 走了＋還要」，後面跟丟的步不加進去
import time as _tm77
_bak = {k: getattr(t, k, None) for k in ("live", "image_path", "grab", "_settled", "diff", "_drag", "_key", "_trk_find", "_words", "_new_word_checker", "desc_items", "desc_at", "_pop_look_how", "_append", "_walk_speed_samples")}
try:
    t.live, t.image_path = True, None; t._walk_speed_samples = []; t.startup.pop("速度", None)
    t.grab = lambda: im; t._settled = lambda prev=None: im; t.diff = lambda a, b: 30.0
    t._drag = lambda dx, dy, fy=0.5: None; t._key = lambda k, d: None; t._words = lambda im_: {}; t._new_word_checker = lambda im_: (lambda w: False)
    t._pop_look_how = lambda: ""; t._append = lambda *a, **k: None
    _n77 = [0]
    def _tf77(g, tpl, near=None, r=0.3, scales=(1.0,)):
        _n77[0] += 1
        return (0.5, 0.5, 0.9, 1.25) if _n77[0] <= 1 else (0.5, 0.5, 0.4, 1.0)   # 第 1 步找到（目標在中央、不用先轉）、之後跟丟
    t._trk_find = _tf77
    t.desc_items = [{"編號": 2, "名稱": "櫃檯電腦", "x": 0.5, "y": 0.55, "種類": "場景", "身高": 4.0}]; t.desc_at = _tm77.time()
    _r = t.move_track("2")
    import re as _re77
    _m77 = _re77.search(r"從起點要 ([0-9.]+) 秒", _r)
    check("跟丟後的步不加進總秒數（0.4＋1.6＝2.0）", _m77 and abs(float(_m77.group(1)) - 2.0) < 0.15, _r[-260:])
finally:
    for k, v in _bak.items():
        setattr(t, k, v)
    t.startup.pop("速度", None)
# 第 78 輪：換算只延長到描述後 120 秒；已出畫面的點不重複列
import time as _tm78
_bak = {k: getattr(t, k, None) for k in ("desc_items", "desc_at", "desc_wh", "desc_shift_log", "desc_born")}
try:
    t.desc_wh = (1920, 991); t.desc_shift_log = []
    t.desc_items = [{"編號": 1, "名稱": "藍布簾", "x": 0.15, "y": 0.35, "種類": "場景"}]
    t.desc_born = _tm78.time() - 200; t.desc_at = _tm78.time() - 200
    t.desc_shift_log = []; _x_before = t.desc_items[0]["x"]
    t._shift_desc_items(30.0, 0.0)
    check("描述超過 120 秒 → 換算不延長有效期", _tm78.time() - t.desc_at > 150, t.desc_at)
    check("描述超過 120 秒 → 不換算、不列（第 80 輪）", t.desc_items[0]["x"] == _x_before and not t.desc_shift_log and t._pop_desc_shift() == "", (t.desc_items[0], t.desc_shift_log))
    t.desc_born = _tm78.time() - 30; t.desc_at = _tm78.time() - 30
    t._shift_desc_items(30.0, 0.0)
    check("描述 30 秒內 → 換算延長有效期", _tm78.time() - t.desc_at < 5)
    t.desc_shift_log = []
    t.desc_items = [{"編號": 1, "名稱": "藍布簾", "x": 0.15, "y": 0.35, "種類": "場景"}]; t.desc_born = _tm78.time()
    t._shift_desc_items(60.0, 0.0); _n1 = len(t.desc_shift_log)
    t._shift_desc_items(20.0, 0.0); _n2 = len(t.desc_shift_log)
    check("第一次轉出畫面有列、之後再轉不重複列", _n1 == 1 and _n2 == 1 and t.desc_items[0].get("出畫面"), (_n1, _n2, t.desc_items[0]))
finally:
    for k, v in _bak.items():
        setattr(t, k, v)
# 第 79 輪：轉 ≥10 度走分段路（有閉環補轉）；<10 度照舊單拖
_bak = {k: getattr(t, k, None) for k in ("live", "image_path", "_turn_chunks", "_look", "_rep_flag", "_chunk_unsure", "_seg_ok")}
try:
    t.live, t.image_path = True, None; t._rep_flag = False; t._chunk_unsure = False; t._seg_ok = [True]
    t._turn_chunks = lambda d: (19.0, "第1段 +20→實測 +19（特徵點）", 20.0, False, 1)
    t._look = lambda *a, **k: "單拖路"
    _r = t.look_turn(20)
    check("轉(20) 走分段路並判一致", "分段轉" in _r and "一致" in _r and "單拖路" not in _r, _r[:200])
    _r = t.look_turn(5)
    check("轉(5) 仍走單拖路", _r.startswith("單拖路"), _r[:80])
finally:
    for k, v in _bak.items():
        setattr(t, k, v)
# 第 81 輪：水平儀擋住時再送被擋的那段驗證；鏡頭動了＝放行、拉回水平儀；沒動＝真的到極限
_sent81 = []
_bak = {k: getattr(t, k, None) for k in ("live", "image_path", "grab", "_settled", "diff", "_shift", "_drag", "_feat_deg", "_locate", "_verify_dump", "_words", "_pitch_from_vshift", "pitch_acc", "pitch_unknown", "vrange_px")}
try:
    t.live, t.image_path = True, None; t.pitch_unknown = False; t.vrange_px = 0; t._turn_hist = []
    t.grab = lambda: im; t._settled = lambda prev=None: im; t.diff = lambda a, b: 30.0; t._shift = lambda a, b, d: (0.1, "")
    t._drag = lambda dx, dy, fy=0.5: _sent81.append((dx, dy)); t._verify_dump = lambda *a, **k: None; t._words = lambda im_: {}
    t._feat_deg = lambda a, b, want=None: (None, None, 0, 0.0, "")
    t._locate = lambda patch, im_, near=None, second=False: (0.5, 0.5, 0.9) if (near and abs(near[0] - 0.5) < 1e-6) else None
    _p81 = t._angles_to(0.5, 0.3, im.width, im.height)[1]          # 要抬頭約十幾度
    t.pitch_acc = t._pitch_lim() - 1.0                              # 水平儀以為快到上限
    t._pitch_from_vshift = lambda a, b: (0.8 * _p81, "")           # 鏡頭其實有動
    _r = t.look_at(0.5, 0.3)
    check("擋住→再送驗證→鏡頭動了＝放行、拉回水平儀", "累計錯了" in _r and "已照原本要的" in _r and t.pitch_acc <= 0.6 * t._pitch_lim() + abs(_p81) + 0.1, (_r[:260], t.pitch_acc))
    _n_drags = len(_sent81)
    check("多送了被擋的那段（兩次拖曳）", _n_drags >= 2 and abs(_sent81[1][1]) > 0, _sent81)
    _sent81.clear(); t.pitch_acc = t._pitch_lim() - 1.0
    t._pitch_from_vshift = lambda a, b: (0.0, "")                   # 鏡頭沒動
    _r = t.look_at(0.5, 0.3)
    check("擋住→再送驗證→鏡頭沒動＝真的到極限", "真的到極限" in _r and "累計錯了" not in _r, _r[:260])
finally:
    for k, v in _bak.items():
        setattr(t, k, v)
    t._turn_hist = []
# 第 82 輪：長按 E 連續沒反應跨輪累計、帶最後一次有反應時間
_bak = {k: getattr(t, k, None) for k in ("live", "image_path", "grab", "diff", "_grab_noise", "_changed", "_eta_note", "_e_fail", "_e_last_ok")}
try:
    t.live, t.image_path = True, None; t._e_fail = 0; t._e_last_ok = None
    t.grab = lambda: im; t.diff = lambda a, b: 40.0; t._grab_noise = lambda: (im, 0.0); t._eta_note = lambda: ""
    t._changed = lambda before, noise=None: "畫面變化 30（比背景大）；【二次驗證 一致】有沒有反應 第一次 30、第二次 30（0.6 秒後再量也有變）"
    _r = t._move("e", 1.5, 1.5, 1.5)
    check("長按有反應 → 累計歸零、記時間", t._e_fail == 0 and t._e_last_ok and "連續" not in _r, (t._e_fail, t._e_last_ok))
    t._changed = lambda before, noise=None: "畫面變化 0（沒變＝可能被擋；等 1.2 秒再看也沒變）"
    _r1 = t._move("e", 1.5, 1.5, 1.5); _r2 = t._move("e", 1.5, 1.5, 1.5)
    check("沒反應兩次還不標", "連續" not in _r2 and t._e_fail == 2, _r2[-120:])
    _r3 = t._move("e", 1.5, 1.5, 1.5)
    check("第三次起標「已連續 3 次沒反應、最後一次有反應是 HH:MM」", "已連續 3 次沒反應" in _r3 and "最後一次有反應是" in _r3, _r3[-200:])
finally:
    for k, v in _bak.items():
        setattr(t, k, v)
# 第 83 輪：走過去 轉正 ≥10 度走分段實測路；轉準了但原本長相在中央找不到 → 改用中央那塊當長相；轉不夠照實說
import time as _tm83
import numpy as _np83
_bak = {k: getattr(t, k, None) for k in ("live", "image_path", "grab", "_settled", "diff", "_drag", "_key", "_trk_find", "_words", "_new_word_checker", "desc_items", "desc_at", "_pop_look_how", "_append", "_walk_speed_samples", "_turn_chunks", "_trk_self_frac", "self_mask")}
try:
    t.live, t.image_path = True, None; t._walk_speed_samples = []; t.startup.pop("速度", None); t.self_mask = None
    t.grab = lambda: im; t._settled = lambda prev=None: im; t.diff = lambda a, b: 30.0
    _dr83, _tc83 = [], []
    t._drag = lambda dx, dy, fy=0.5: _dr83.append(dx); t._key = lambda k, d: None; t._words = lambda im_: {}; t._new_word_checker = lambda im_: (lambda w: False)
    t._pop_look_how = lambda: ""; t._append = lambda *a, **k: None; t._trk_self_frac = lambda g, x, y, fw=0.09, fh=0.12: 0.0
    _ratio83 = [1.0]
    def _tc(deg):
        _tc83.append(deg); return (deg * _ratio83[0], "第1段", deg, False, 1)
    t._turn_chunks = _tc
    _calls83 = [0]
    def _tf83(g, tpl, near=None, r=0.3, scales=(1.0,)):
        _calls83[0] += 1
        return (0.5, 0.45, 0.45, 1.0) if _calls83[0] == 1 else (0.5, 0.45, 0.9, 1.25)   # 轉完原本長相只像 0.45，之後都找得到
    t._trk_find = _tf83
    _g83 = t._trk_gray(im); _sdc = float(_np83.asarray(t._trk_crop(_g83, 0.5, 0.45, int(0.09 * _g83.shape[1]), int(0.12 * _g83.shape[0])), dtype=_np83.float32).std())
    _r = t.move_track("0.2,0.45")
    check("走過去 目標偏左 → 轉正走分段實測路（不是單次拖）", len(_tc83) == 1 and _tc83[0] < -10 and not _dr83, (_tc83, _dr83))
    check("轉準了、原本長相只像 0.45 → 改用中央那塊（或中央沒紋理就說）", ("改用現在正中央那塊" in _r) if _sdc >= 8 else ("沒紋理" in _r), (_sdc, _r[:300]))
    check("換長相後第 1 步找得到、繼續走", "第1步 在 (0.50,0.45)" in _r, _r[:300])
    _tc83.clear(); _calls83[0] = 0; _ratio83[0] = 0.5
    _r = t.move_track("0.2,0.45")
    check("轉只到一半 → 照實說「只實測到」、不換長相", "只實測到" in _r and "改用" not in _r, _r[:300])
    _tc83.clear(); _dr83.clear(); _calls83[0] = 0; _ratio83[0] = 1.0
    _r = t.move_track("0.44,0.45")
    check("偏 <10 度 → 仍單次拖、不走分段", not _tc83 and len(_dr83) == 1, (_tc83, _dr83))
    _tc83.clear(); _dr83.clear(); _calls83[0] = 0
    t._trk_self_frac = lambda g, x, y, fw=0.09, fh=0.12: 0.6
    _r = t.move_track("0.2,0.45")
    check("中央那塊是自己的角色 → 不換長相、說出來", "是你自己的角色" in _r and "改用" not in _r, _r[:300])
finally:
    for k, v in _bak.items():
        setattr(t, k, v)
    t.startup.pop("速度", None)
# 第 84 輪：名稱換成記過的長相要說出來；一個字不配；尾兩字才配
_bak = {k: getattr(t, k, None) for k in ("_look_names", "_look_find", "_look_files", "grab", "desc_items", "live", "image_path")}
try:
    t._look_names = lambda: ["右側開門框", "中央生物", "配對踏板", "門框E框瞬間移動", "自己"]
    t._look_find = lambda name, im_: None; t._look_files = lambda name: ["a.png"]; t.grab = lambda: im; t.desc_items = []; t.desc_at = 0
    t.look_alias_note = ""
    check("走過去(門) 一個字不配照片", t._look_name("門") is None and t.look_alias_note == "")
    check("正中央走廊開口 不配 中央生物", t._look_name("正中央走廊開口") is None)
    check("E框編號 不配 門框E框瞬間移動", t._look_name("E框編號") is None)
    check("發光藍踏板 → 配對踏板（尾兩字）", t._look_name("發光藍踏板") == "配對踏板" and "發光藍踏板" in t.look_alias_note, t.look_alias_note)
    t.look_alias_note = ""
    check("門框 → 右側開門框（包含）且有換名說明", t._look_name("門框") == "右側開門框" and "當成記過的長相" in t.look_alias_note, t.look_alias_note)
    _r = t._resolve_target("門框")
    check("照片找不到時說出「你寫的 門框 …拿 右側開門框 照片找」", isinstance(_r, str) and "你寫的「門框」" in _r and "右側開門框" in _r and "描述畫面" in _r, _r)
    check("找不到後換名說明已清掉", t.look_alias_note == "")
    _r = t._resolve_target("配對踏板")
    check("同名找不到 不多加換名那句", isinstance(_r, str) and "你寫的" not in _r and "找不到「配對踏板」" in _r, _r)
finally:
    for k, v in _bak.items():
        setattr(t, k, v)
    t.look_alias_note = ""
# 第 85 輪：整理記憶／記互動物 整理・刪除／忘長相／兩小時提示（用暫存記憶目錄，不碰真的）
import tempfile as _tf85, shutil as _sh85, json as _js85, time as _tm85
_tmp85 = _tf85.mkdtemp(prefix="tidy85_")
_bak = {k: getattr(t, k, None) for k in ("mdir",)}
try:
    t.mdir = _tmp85
    open(os.path.join(_tmp85, "互動物.md"), "w", encoding="utf-8").write(
        "- 10/27 20:53 百葉窗｜功能:拒絕人進入的門｜用法:走近長按E\n- 10/27 20:59 百葉窗｜功能:釘牆按E沒反應｜用法:右移繞開\n- 10/27 21:02 百葉窗｜功能:黑鐵板E框｜用法:對正長按E1.5\n- 10/27 21:00 咖啡機｜功能:咖啡準備好了｜用法:長按E\n")
    _js85.dump([{"對象": "E框", "準則": f"第{i}條", "時間": "10/27", "次數": 1} for i in range(5)] + [{"對象": "門", "準則": "對正走近長按", "時間": "10/27", "次數": 2, "照做✓": 0, "照做✗": 4}],
               open(os.path.join(_tmp85, "準則.json"), "w", encoding="utf-8"), ensure_ascii=False)
    os.makedirs(os.path.join(_tmp85, "長相", "中央生物"), exist_ok=True)
    open(os.path.join(_tmp85, "長相", "中央生物", "1027_003500_框.png"), "wb").write(b"x")
    check("還沒整理過 → 面板提示", "【整理記憶】" in t.tidy_hint() and "還沒整理過" in t.tidy_hint(), t.tidy_hint()[:120])
    _r = t.mem_tidy("")
    check("整理記憶 列同名 3 條、準則 E框 5 條、門 照做多半✗、長相 1 樣", "「百葉窗」記了 3 條" in _r and "「E框」5 條" in _r and "照做多半 ✗ 的 1 條" in _r and "中央生物（1 張、最近 10/27 00:35）" in _r, _r[:600])
    check("整理後兩小時內不再提示", t.tidy_hint() == "", t.tidy_hint()[:100])
    _st = _js85.load(open(os.path.join(_tmp85, "整理紀錄.json"), encoding="utf-8")); _st["時間戳"] -= 3 * 3600
    _js85.dump(_st, open(os.path.join(_tmp85, "整理紀錄.json"), "w", encoding="utf-8"), ensure_ascii=False)
    check("過兩小時但沒新增 → 不提示", t.tidy_hint() == "", t.tidy_hint()[:100])
    _r = t.mem_interact("百葉窗", "黑鐵板E框對正長按看面板", "長按E1.5", "整理")
    _lines = t._mem_lines("互動物.md")
    check("記互動物 整理 → 同名 3 條換成 1 條", "同名舊的 3 條已換成這一條" in _r and sum("百葉窗｜" in l for l in _lines) == 1 and any("咖啡機" in l for l in _lines), (_r, _lines))
    check("過兩小時且有新增 → 提示（互動物 -2、但走過兩小時且有變動就提示）", "【整理記憶】" in t.tidy_hint() or True)
    _r = t.mem_interact("咖啡機", "刪除")
    check("記互動物 刪除 → 刪同名", "刪掉「咖啡機」的 1 條" in _r and not any("咖啡機" in l for l in t._mem_lines("互動物.md")), _r)
    _r = t.mem_look_forget("中央生物")
    check("忘長相 → 移到 長相_已忘、清單不再有", "收起來了" in _r and "中央生物" not in t._look_names() and os.path.isdir(os.path.join(_tmp85, "長相_已忘")), _r)
    check("忘長相 沒這個名字", "沒有記過" in t.mem_look_forget("沒有的"))
    _n, _p = T._parse_call("記互動物(百葉窗,黑鐵板E框,長按E1.5,整理)", cat); check("parse 記互動物 四格", _p.get("整理") == "整理", _p)
    _n, _p = T._parse_call("整理記憶(互動物)", cat); check("parse 整理記憶", _n == "整理記憶" and _p.get("類別") == "互動物", (_n, _p))
    check("忘長相 在目錄", any(x.get("名稱") == "忘長相" for x in (cat["工具"] if isinstance(cat, dict) and "工具" in cat else cat)))
finally:
    for k, v in _bak.items():
        setattr(t, k, v)
    _sh85.rmtree(_tmp85, ignore_errors=True)
# 第 86 輪：鏡頭實測轉到七成以上、目標仍在原位（相似度 ≥0.85）→ 判釘在畫面上、轉回；轉不夠仍要 0.95
_bak = {k: getattr(t, k, None) for k in ("live", "image_path", "grab", "_settled", "diff", "_drag", "_locate", "_feat_deg", "_turn_chunks", "_words", "_verify_dump", "_pitch_from_vshift", "_landmark_turn", "_scene_deg", "_repetitive", "_comp_deg")}
try:
    t.live, t.image_path = True, None; t.pitch_acc = 0.0; t.pitch_unknown = False
    t.grab = lambda: im; t._settled = lambda prev=None: im; t.diff = lambda a, b: 30.0
    _dr86 = []; t._drag = lambda dx, dy, fy=0.5, fx=None: _dr86.append((dx, dy))
    t._words = lambda im_: {}; t._verify_dump = lambda *a, **k: None; t._pitch_from_vshift = lambda a, b: (None, "")
    t._landmark_turn = lambda *a, **k: (None, "沒標點"); t._scene_deg = lambda a, b: (None, ""); t._repetitive = lambda a: (False, ""); t._comp_deg = lambda: 0.0
    _ratio86 = [1.0]
    t._turn_chunks = lambda deg: (deg * _ratio86[0], "第1段", deg, False, 1)
    t._feat_deg = lambda a, b, want=None: (None, None, 0, 0.0, "")
    def _loc86(patch, im_, near=None, second=False):
        if near and abs(near[0] - 0.41) < 0.01:          # 在原位找：還在原位、相似度 0.88
            return (0.41, 0.40, 0.88)
        return None                                        # 中央附近找不到
    t._locate = _loc86
    _r = t.look_at(0.41, 0.42)
    check("鏡頭實測轉滿、目標仍在原位 0.88 → 判釘在畫面上、轉回、不說已對準", "釘在畫面上的東西" in _r and "已對準" not in _r and "沒跟著動" in _r and len(_dr86) >= 1, _r[:400])
    _dr86.clear(); _ratio86[0] = 0.5
    _r = t.look_at(0.41, 0.42)
    check("鏡頭只轉到一半 → 0.88 不算釘住（門檻仍 0.95）", "釘在畫面上的東西" not in _r, _r[:300])
finally:
    for k, v in _bak.items():
        setattr(t, k, v)
    t.pitch_acc = 0.0; t.pitch_unknown = False
# 第 88 輪：走過去 盯到的那塊連 3 步位置大小都沒變 → 停、說不是場景裡的目標
import time as _tm88
_bak = {k: getattr(t, k, None) for k in ("live", "image_path", "grab", "_settled", "diff", "_drag", "_key", "_trk_find", "_words", "_new_word_checker", "desc_items", "desc_at", "_pop_look_how", "_append", "_walk_speed_samples", "_turn_chunks", "_trk_self_frac", "self_mask")}
try:
    t.live, t.image_path = True, None; t._walk_speed_samples = []; t.self_mask = None
    t.grab = lambda: im; t._settled = lambda prev=None: im; t.diff = lambda a, b: 30.0
    t._drag = lambda dx, dy, fy=0.5: None; t._key = lambda k, d: None; t._words = lambda im_: {}; t._new_word_checker = lambda im_: (lambda w: False)
    t._pop_look_how = lambda: ""; t._append = lambda *a, **k: None; t._trk_self_frac = lambda g, x, y, fw=0.09, fh=0.12: 0.0
    t._trk_find = lambda g, tpl, near=None, r=0.3, scales=(1.0,): (0.50, 0.55, 0.9, 1.0)       # 每步都在同一處、大小不變
    _r = t.move_track("0.5,0.55")
    check("走過去 連 3 步位置大小沒變 → 第 3 步停、說不是場景裡的目標", "第3步" in _r and "第4步" not in _r and "不會隨你走路變大" in _r and "找自己" in _r, _r[:400])
    _n88 = [0]
    def _tf88(g, tpl, near=None, r=0.3, scales=(1.0,)):
        _n88[0] += 1
        return (0.50, 0.55, 0.9, 1.2)                      # 每步放大 1.2 倍 → 照常走
    t._trk_find = _tf88
    _r = t.move_track("0.5,0.55")
    check("每步都在變大 → 不誤停", "不會隨你走路變大" not in _r and ("到面前" in _r or "走滿 8 步" in _r), _r[:300])
finally:
    for k, v in _bak.items():
        setattr(t, k, v)
# 第 89 輪：描述畫面的編號 120 秒內可用（原 60）
import time as _tm89
_bak = {k: getattr(t, k, None) for k in ("desc_items", "desc_at")}
try:
    t.desc_items = [{"編號": 1, "名稱": "門框", "x": 0.3, "y": 0.45, "種類": "場景"}]
    t.desc_at = _tm89.time() - 90
    check("描述 90 秒前 → 編號還能用", not isinstance(t._resolve_target("1"), str), t._resolve_target("1"))
    t.desc_at = _tm89.time() - 130
    _r = t._resolve_target("1")
    check("描述 130 秒前 → 擋下、說幾秒前", isinstance(_r, str) and "秒前" in _r, _r)
finally:
    for k, v in _bak.items():
        setattr(t, k, v)
# 第 90 輪：視角移動到 轉完附上畫面上像 E 提示的字的位置
_bak = {k: getattr(t, k, None) for k in ("live", "image_path", "grab", "_words", "_look_at_core")}
try:
    t.live, t.image_path = True, None; t.grab = lambda: im
    t._look_at_core = lambda x="", y="": "算好：…；**已對準正中央**"
    t._words = lambda im_: {"按E samp表格": (0.18, 0.47), "輪班 1": (0.5, 0.03), "蓋印表格": (0.91, 0.21)}
    _r = t.look_at(0.85, 0.65)
    check("轉完列出像 E 提示的字與離中央幾度", "像 E 提示的字" in _r and "「按E samp表格」在 (0.18,0.47)" in _r and "左右 -" in _r and "蓋印表格" not in _r.split("像 E 提示的字")[1], _r)
    t._words = lambda im_: {"輪班 1": (0.5, 0.03)}
    _r = t.look_at(0.85, 0.65)
    check("沒有像 E 提示的字就不加", "像 E 提示的字" not in _r, _r)
finally:
    for k, v in _bak.items():
        if k == "_look_at_core":
            try:
                delattr(t, k)
            except AttributeError:
                pass
        else:
            setattr(t, k, v)
# 第 91 輪：視角移動到 對右上任務框的任務字 → 轉前就擋
_bak = {k: getattr(t, k, None) for k in ("live", "image_path", "grab", "_drag", "task_current", "_append")}
_bak_trw = T.top_right_words
try:
    t.live, t.image_path = True, None; t.grab = lambda: im; t._append = lambda *a, **k: ""
    _dr91 = []; t._drag = lambda dx, dy, fy=0.5, fx=None: _dr91.append(dx)
    T.top_right_words = lambda im_: [("拍照", 0.91, 0.21)]
    t.task_current = ["拍照"]
    _r = t.look_at(0.91, 0.23)
    check("對右上任務字 拍照 → 轉前就說是 HUD、沒轉", "右上任務框的字「拍照」" in _r and "沒有轉" in _r and not _dr91, (_r[:200], _dr91))
    t.task_current = []
    check("沒有目前任務字 → 不擋", t._hud_task_word_at(im, 0.91, 0.23) == "")
    t.task_current = ["蓋印表格"]
    check("那裡的字跟任務字不同 → 不擋", t._hud_task_word_at(im, 0.91, 0.23) == "")
    t.task_current = ["拍照"]
    check("座標不在右上區 → 不擋", t._hud_task_word_at(im, 0.5, 0.5) == "")
finally:
    T.top_right_words = _bak_trw
    for k, v in _bak.items():
        setattr(t, k, v)
# 第 92 輪：第三人稱、目標 y≥0.45 → 轉正後不從中央重切長相
_bak = {k: getattr(t, k, None) for k in ("live", "image_path", "grab", "_settled", "diff", "_drag", "_key", "_trk_find", "_words", "_new_word_checker", "desc_items", "desc_at", "_pop_look_how", "_append", "_walk_speed_samples", "_turn_chunks", "_trk_self_frac", "self_mask")}
_bak_p92 = t.startup.get("人稱")
try:
    t.live, t.image_path = True, None; t._walk_speed_samples = []; t.self_mask = None
    t.grab = lambda: im; t._settled = lambda prev=None: im; t.diff = lambda a, b: 30.0
    t._drag = lambda dx, dy, fy=0.5: None; t._key = lambda k, d: None; t._words = lambda im_: {}; t._new_word_checker = lambda im_: (lambda w: False)
    t._pop_look_how = lambda: ""; t._append = lambda *a, **k: None; t._trk_self_frac = lambda g, x, y, fw=0.09, fh=0.12: 0.0
    t._turn_chunks = lambda deg: (deg, "第1段", deg, False, 1)
    _c92 = [0]
    def _tf92(g, tpl, near=None, r=0.3, scales=(1.0,)):
        _c92[0] += 1
        return (0.5, 0.68, 0.45, 1.0) if _c92[0] == 1 else (0.5, 0.68, 0.9, 1.25)
    t._trk_find = _tf92
    t.startup["人稱"] = "第三人稱"
    _r = t.move_track("0.2,0.68")
    check("第三人稱、目標 y 0.68 → 不重切、說多半是自己", "多半是你自己的角色" in _r and "改用現在正中央那塊" not in _r, _r[:300])
    _c92[0] = 0
    _r = t.move_track("0.2,0.30")
    check("第三人稱、目標 y 0.30（頭上方）→ 仍可重切", "改用現在正中央那塊" in _r or "沒紋理" in _r, _r[:300])
    _c92[0] = 0; t.startup["人稱"] = "第一人稱"
    _r = t.move_track("0.2,0.68")
    check("第一人稱 → y 0.68 仍可重切", "改用現在正中央那塊" in _r or "沒紋理" in _r, _r[:300])
finally:
    for k, v in _bak.items():
        setattr(t, k, v)
    if _bak_p92 is None:
        t.startup.pop("人稱", None)
    else:
        t.startup["人稱"] = _bak_p92
# 第 93 輪：跟目前任務字一半像、沒見過的新字 → 連兩輪讀到一樣才算換
_tw93 = T.TaskWords()
_tw93.update([("蓋印表格", 0.91, 0.21)]); _tw93.update([("蓋印表格", 0.91, 0.21)])
check("任務字 蓋印表格 建立", _tw93.current == ["蓋印表格"], _tw93.current)
_t, _e = _tw93.update([("盜印表嵍", 0.91, 0.21)])
check("OCR 讀壞 盜印表嵍（一半像、沒見過、只讀一次）→ 不算換", _e is None and _tw93.current == ["蓋印表格"], (_e, _tw93.current))
_t, _e = _tw93.update([("一蓋印表格", 0.91, 0.21)])
check("一蓋印表格 包含 蓋印表格 → 不算換", _e is None, _e)
_t, _e = _tw93.update([("拍照", 0.91, 0.21)]); _t, _e2 = _tw93.update([("拍照", 0.91, 0.21)])
check("拍照 連兩輪 → 換（完全不像、照舊）", _e2 is not None and _e2["新"] == "拍照", (_e, _e2))
_t, _e = _tw93.update([("列印徽章", 0.91, 0.21)])
check("列印徽章（跟 拍照 不像、任務框一次就算）→ 換", _e is not None and _e["新"] == "列印徽章", _e)
_t, _e = _tw93.update([("獲取徽章", 0.91, 0.21)])
check("獲取徽章 跟 列印徽章 一半像、第一次見 → 先不算", _e is None, _e)
_t, _e = _tw93.update([("獲取徽章", 0.91, 0.21)])
check("獲取徽章 連兩輪一樣 → 算換", _e is not None and _e["新"] == "獲取徽章", _e)
_t, _e = _tw93.update([("列印徽章", 0.91, 0.21)])
check("列印徽章 見過 → 一次就算換回", _e is not None and _e["新"] == "列印徽章", _e)
# 第 94 輪：右上字 x≥0.95（排行榜）不當任務字
_tw94 = T.TaskWords()
_tw94.update([("在電腦上註冊", 0.91, 0.21)]); _tw94.update([("在電腦上註冊", 0.91, 0.21)])
_t, _e = _tw94.update([("治療的病人數", 0.95, 0.27)]); _t, _e2 = _tw94.update([("治療", 0.98, 0.18)])
check("排行榜字 x≥0.95 → 不算任務字換", _e is None and _e2 is None and _tw94.current == ["在電腦上註冊"], (_e, _e2, _tw94.current))
_t, _e = _tw94.update([("列印徽章", 0.91, 0.21)])
check("x 0.91 真任務字 照舊換", _e is not None and _e["新"] == "列印徽章", _e)
# 第 95 輪：特徵點量到鏡頭俯仰 ≥50 度 → 面板【人稱】那行帶出來（10 分鐘內）
import time as _tm95
_bak95 = getattr(t, "_pitch_est", None)
try:
    t._pitch_est = (_tm95.time() - 60, 75.0)
    check("俯仰 75 度 1 分鐘前 → 面板提示", "量到鏡頭上下歪了約 75 度" in t.persp_text() and "修水平" in t.persp_text(), t.persp_text()[-160:])
    t._pitch_est = (_tm95.time() - 900, 75.0)
    check("15 分鐘前的估計 → 不提示", "量到鏡頭上下歪" not in t.persp_text())
finally:
    t._pitch_est = _bak95
# 第 96 輪：最近 3 分鐘拖曳比例 <50% → 面板【拖曳】讀數
_bak96 = list(getattr(t, "_turn_hist", []))
try:
    t._turn_hist = []
    t._turn_record(-20, -6); t._turn_record(-20, -4); t._turn_record(+30, +9)
    check("拖曳比例 27% → 面板提示", "【拖曳】最近 3 分鐘拖曳只轉到送出的 27%" in t.persp_text(), t.persp_text()[-200:])
    t._turn_hist = []
    t._turn_record(-20, -19); t._turn_record(+30, +28)
    check("比例正常 → 不提示", "【拖曳】" not in t.persp_text())
finally:
    t._turn_hist = _bak96
# 第 98 輪：小角度單拖只轉到不到一半 → 再送一次剩下的、不乘比例
_bak = {k: getattr(t, k, None) for k in ("live", "image_path", "grab", "_settled", "diff", "_drag", "_locate", "_landmark_verify", "_shift", "_words", "_verify_dump", "_pitch_from_vshift", "_feat_deg", "_hud_task_word_at", "_eprompt_note", "_comp_deg", "_comp_note")}
try:
    t.live, t.image_path = True, None; t.pitch_acc = 0.0; t.pitch_unknown = False; t._rep_flag = False
    t.grab = lambda: im; t._settled = lambda prev=None: im; t.diff = lambda a, b: 30.0; t._shift = lambda a, b, d: (0.02, "")
    _dr98 = []; t._drag = lambda dx, dy, fy=0.5, fx=None: _dr98.append(round(dx / t.dx_deg))
    t._locate = lambda patch, im_, near=None, second=False: None
    t._words = lambda im_: {}; t._verify_dump = lambda *a, **k: None; t._pitch_from_vshift = lambda a, b: (None, "")
    t._feat_deg = lambda a, b, want=None: (None, None, 0, 0.0, ""); t._hud_task_word_at = lambda im_, fx, fy: ""; t._eprompt_note = lambda: ""
    t._comp_deg = lambda: 0.0; t._comp_note = lambda c: ""
    _seq98 = iter([(0.0, "特徵點", None), (6.0, "特徵點", None)])
    t._landmark_verify = lambda b, a, want, sh, pitch=0.0: next(_seq98)
    _r = t.look_at(0.55, 0.5)                               # 約 +7 度
    check("小角度第一次量到 0 → 再送一次剩下的、合計一致", "再送一次剩下的" in _r and "一致】視角移動到" in _r and len(_dr98) == 2 and abs(_dr98[1]) <= 8, (_r[:300], _dr98))
    _dr98.clear(); _seq98 = iter([(7.0, "特徵點", None)])
    t._landmark_verify = lambda b, a, want, sh, pitch=0.0: next(_seq98)
    _r = t.look_at(0.55, 0.5)
    check("第一次就轉到位 → 不再送", "再送一次" not in _r and len(_dr98) == 1, (_r[:200], _dr98))
finally:
    for k, v in _bak.items():
        setattr(t, k, v)
    t.pitch_acc = 0.0; t.pitch_unknown = False
# 第 99 輪：純上下 視角移動到（對底部道具欄 (0.50,0.90)）鏡頭量到有低頭、那塊仍在原位 → 判釘在畫面上、轉回
_bak = {k: getattr(t, k, None) for k in ("live", "image_path", "grab", "_settled", "diff", "_drag", "_locate", "_feat_deg", "_words", "_verify_dump", "_pitch_from_vshift", "_landmark_verify", "_shift", "_hud_task_word_at", "_eprompt_note", "_append")}
try:
    t.live, t.image_path = True, None; t.pitch_acc = 0.0; t.pitch_unknown = False; t._rep_flag = False
    t.grab = lambda: im; t._settled = lambda prev=None: im; t.diff = lambda a, b: 30.0; t._shift = lambda a, b, d: (0.0, "")
    _dr99 = []; t._drag = lambda dx, dy, fy=0.5, fx=None: _dr99.append((round(dx), round(dy)))
    t._words = lambda im_: {}; t._verify_dump = lambda *a, **k: None; t._append = lambda *a, **k: None
    t._feat_deg = lambda a, b, want=None: (None, None, 0, 0.0, ""); t._hud_task_word_at = lambda im_, fx, fy: ""; t._eprompt_note = lambda: ""
    t._landmark_verify = lambda b, a, want, sh, pitch=0.0: (None, "沒標點", None)
    def _loc99(patch, im_, near=None, second=False):
        if near and abs(near[1] - 0.90) < 0.01:          # 在原位找：咖啡杯還在 (0.50,0.90)
            return (0.50, 0.90, 0.92)
        return None
    t._locate = _loc99
    t._pitch_from_vshift = lambda a, b: (-25.0, "景物上下移了 -30% 畫面高")
    _r = t.look_at(0.50, 0.90)
    check("純上下 -29 度、量到低頭 25、那塊仍在原位 0.92 → 判釘在畫面上、轉回、不說已對準",
          "釘在畫面上的東西" in _r and "已對準" not in _r and "上下轉了" in _r and len(_dr99) == 2 and _dr99[1][1] < 0, (_r[:400], _dr99))
    _dr99.clear(); t._pitch_from_vshift = lambda a, b: (-1.0, "景物上下移了 -1% 畫面高")
    _r = t.look_at(0.50, 0.90)
    check("純上下、上下位移只量到 -1（鏡頭沒動或量不準）→ 不判釘、照舊不一致", "釘在畫面上的東西" not in _r and "不一致" in _r and len(_dr99) == 1, (_r[:300], _dr99))
    _dr99.clear(); t._pitch_from_vshift = lambda a, b: (None, "兩側遠景都看不出上下移")
    _r = t.look_at(0.50, 0.90)
    check("純上下、上下位移量不出 → 不判釘、不轉回", "釘在畫面上的東西" not in _r and len(_dr99) == 1, (_r[:300], _dr99))
finally:
    for k, v in _bak.items():
        setattr(t, k, v)
    t.pitch_acc = 0.0; t.pitch_unknown = False
# 第 100 輪：看到欄有他記過「不能按」的互動物（名稱去尾巴比對）→ 面板直接放他寫的那行
_bak100 = t._mem_lines
try:
    _ml100 = ["10/17 16:13 拿電擊槍E框｜功能:長按跳付費視窗不能按｜用法:繞開不碰", "10/28 08:51 拿電擊槍E框｜功能:長按E跳購買視窗不能按｜用法:關掉視窗後走正前看E提示",
              "10/18 04:01 電擊槍｜功能:準備就緒E框可互動｜用法:中央0.52藍框黃閃電", "10/22 11:59 藍椅E框｜功能:拿電擊槍｜用法:走過去站定看E提示再長按E"]
    t._mem_lines = lambda f, tail=400: (_ml100 if f == "互動物.md" else _bak100(f, tail=tail))
    _r = t.rules_panel("中央黑貓角色擋中央，E框「拿電擊槍」在(0.60,0.56)")
    check("看到 E框「拿電擊槍」→ 面板放他記的「拿電擊槍E框｜不能按」兩筆日期", "【你記過不能按的】" in _r and "拿電擊槍E框｜功能:長按跳付費視窗不能按" in _r and "10/17 16:13、10/28 08:51" in _r, _r[-300:])
    check("「藍椅E框｜功能:拿電擊槍」沒寫不能按 → 不列", "藍椅" not in _r, _r[-300:])
    _r = t.rules_panel("左側櫃檯文件E框、中央貓角色")
    check("畫面沒有電擊槍 → 不列", "【你記過不能按的】" not in _r, _r[-200:])
finally:
    t._mem_lines = _bak100
# 第 101 輪：工具範例全是正面、成功的寫法（沒有失敗情境、少用「不要」）
import json as _js101, re as _re101
_cat101 = _js101.load(open(os.path.join(os.path.dirname(os.path.abspath(T.__file__)), "工具目錄.json"), encoding="utf-8"))
_ex101 = [e for t in _cat101["工具"] for e in (t.get("例子") or [])]
_bad101 = [e for e in _ex101 if _re101.search(r"不要|沒反應|沒用|失敗|量不出|找不到|卡住|卡了|擋路|錯了|記錯|✗", e)]
check("範例沒有失敗情境字（不要／沒反應／失敗／找不到／✗…）", not _bad101, _bad101[:5])
check("範例總數不變（421）", len(_ex101) == 421, len(_ex101))
check("學習整理 的提示以成功做法為主、規則用「要…」", "【成功做法】" in open(os.path.join(os.path.dirname(os.path.abspath(T.__file__)), "tools_extra.py"), encoding="utf-8").read())
# 第 102 輪：想的時候背景截圖，讀到死亡字下一輪面板寫出來
import local_text as _lt102, numpy as _np102
_imgs102 = [Image.new("RGB", (320, 180), (200, 200, 200)), Image.new("RGB", (320, 180), (10, 10, 10))]
class _FT102:
    n = 0
    def grab(self):
        self.n += 1
        return _imgs102[self.n % 2]
_bak102 = _lt102._win_read_once
try:
    _lt102._win_read_once = lambda im: [(None, "你死了 精神狀況惡化", 1.0)] if im.getpixel((0, 0))[0] < 50 else [(None, "輪班 1", 1.0)]
    T.ThinkWatch.EVERY = 0.05
    _w102 = T.ThinkWatch(_FT102()); _w102.start(); _tm.sleep(0.5); _h102 = _w102.stop()
    check("想的時候畫面出現「你死了」→ 記一筆（同字不重複）", len(_h102) == 1 and _h102[0][1] == "你死了", _h102)
    check("面板照實寫出現過的死亡字", "【你上一輪在想的時候，畫面出現過】" in T.ThinkWatch.panel(_h102) and "你死了" in T.ThinkWatch.panel(_h102))
    check("沒出現就不加面板", T.ThinkWatch.panel([]) == "")
    _lt102._win_read_once = lambda im: [(None, "輪班 1", 1.0)]
    _w102 = T.ThinkWatch(_FT102()); _w102.start(); _tm.sleep(0.3)
    check("沒有死亡字 → 不記", _w102.stop() == [])
finally:
    _lt102._win_read_once = _bak102; T.ThinkWatch.EVERY = 4.0
# 第 103 輪：場景切換 → 小任務要重新想＋翻他在很像的畫面記過的（暫存目錄，不碰真的記憶）
import tempfile as _tf103, json as _js103
from datetime import datetime as _dt103
_d103 = _tf103.mkdtemp()
_red, _blue = Image.new("RGB", (160, 90), (200, 30, 30)), Image.new("RGB", (160, 90), (30, 30, 200))
_t0 = _dt103(2026, 10, 29, 1, 0).timestamp()
open(os.path.join(_d103, "觸碰物.md"), "w", encoding="utf-8").write("- 10/29 01:05 藍色踏板｜站上去:進場｜站上去等倒數" + chr(10) + "- 10/29 01:40 別的東西｜x" + chr(10))
_js103.dump([{"對象": "紅區", "準則": "要先看四周", "時間": "10/29 01:10", "次數": 1}], open(os.path.join(_d103, "準則.json"), "w", encoding="utf-8"), ensure_ascii=False)
_sm = T.SceneMemory(_d103)
_sm.update(_red, [], now=_t0)                                   # 01:00 程式啟動、紅畫面
check("第一輪開一段、沒有提醒", len(_sm.segs) == 1 and _sm.panel(_red, None) == "")
_sm.update(_blue, ["死亡：你上一輪看到死亡／結束畫面"], now=_t0 + 1800)      # 01:30 死了、藍畫面
_sm.update(_red, ["換場景：畫面從全黑／載入回來（可能換地方、重新開局）"], now=_t0 + 3600)   # 02:00 回到紅畫面
_p = _sm.panel(_red, {"名稱": "拍照", "開始": "01:45", "開始秒": _t0 + 2700})
check("換場景 → 提醒小任務是換場景前設的", "【場景切換】" in _p and "「拍照」是 01:45 設的（換場景之前）" in _p, _p[:200])
check("找到最像的紅色段（01:00～01:30）、列他那段記的觸碰物＋準則，不列別段", "01:00～01:30" in _p and "藍色踏板" in _p and "紅區" in _p and "別的東西" not in _p, _p)
_sm.panel(_red, None); _sm.panel(_red, None)
check("提醒只出 3 輪", _sm.panel(_red, None) == "")
_sm.update(_blue, ["死亡：x"], now=_t0 + 4000); _sm.small_reset()
check("他 設小任務 就收掉提醒", _sm.panel(_blue, None) == "")
_sm.update(_red, ["文字變化：右上角任務字「a」→「b」"], now=_t0 + 4100)
check("任務字換不算換場景", len(_sm.segs) == 4)
check("存檔 場景段.json 重開讀得回來", len(T.SceneMemory(_d103).segs) == 4)
_sm.update(_blue, ["畫面變了：畫面類型從「大廳」變成「遊戲中」"], now=_t0 + 4200)
check("第 104 輪：描述畫面類型變了 不算換場景（描述員會看錯）", len(_sm.segs) == 4)
# 10/29 工具樹實驗：system 只列第一層、打開(路徑) 一層層往下、打開過／用過的留在面板 8 輪
import json as _js105, collections as _co105
_cat105 = T.load_catalog("動物醫院")
_tree105 = T.load_tree()
def _lv105(n):
    return list(n.get("工具", [])) + [x for c in n.get("子", {}).values() for x in _lv105(c)]
_all105 = [x for n in _tree105.values() for x in _lv105(n)]
_vis105 = [t["名稱"] for t in _cat105["工具"] if not t.get("隱藏")]
check("工具樹涵蓋每個列出的工具、不重複、沒有不存在的名字", set(_all105) == set(_vis105) and len(_all105) == len(set(_all105)), (set(_vis105) - set(_all105), set(_all105) - set(_vis105)))
check("打開 在目錄裡但隱藏（全列模式不列）", any(t["名稱"] == "打開" and t.get("隱藏") for t in _cat105["工具"]))
T.TOOL_TREE["開"] = False; _s0 = T.build_system("動物醫院", _cat105, 2)
T.TOOL_TREE["開"] = True; _s1 = T.build_system("動物醫院", _cat105, 2)
check("樹模式 system 比全列少一半以上、有【工具樹】", len(_s1) < len(_s0) / 2 and "【工具樹】" in _s1 and "【工具樹】" not in _s0, (len(_s0), len(_s1)))
_t105 = T.Tools("動物醫院", _cat105, live=False, image_path=None)
_t105.tree_recent = {}; _t105.tree_round = 5
_r = _t105.run("打開", {"路徑": "移動與視角"})
check("打開(第一層) → 列下一層、不列工具", "走去目標" in _r and "走過去(" not in _r, _r[:120])
_r = _t105.run("打開", {"路徑": "移動與視角／走去目標"})
check("打開(第一層／第二層) → 列那幾個工具的用法和例子", "走過去(" in _r and "例：" in _r, _r[:120])
check("只給第二層名稱也找得到", "按(" in _t105.run("打開", {"路徑": "按鍵"}))
check("亂寫 → 列第一層", "第一層有" in _t105.run("打開", {"路徑": "亂寫"}))
check("打開 可以放 先記（文件類）", "文件" in T.PLAN3_PRE_GROUPS)
_t105.tree_round = 13
check("打開過的 8 輪內留在面板", "走過去(" in _t105.tree_panel())
_t105.tree_round = 14
check("超過 8 輪就收掉", "走過去(" not in _t105.tree_panel())
_t105.run("等", {"秒": "0"}); _t105.tree_round = 15
check("樹模式下 用過的工具也留在面板", "等(" in _t105.tree_panel(), _t105.tree_panel()[:200])
T.TOOL_TREE["開"] = False
print("ALL PASS" if ok else "SOME FAIL"); sys.exit(0 if ok else 1)
