#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
calibrate_look.py — 量出「滑鼠拖曳多少像素 = 畫面轉多少」，寫成代理能懂的換算表

做法：在 Roblox 視窗裡對每個 dx（水平拖曳像素）做一次右鍵拖曳，
比較拖曳前後的畫面，用相位相關（phase correlation）算出畫面實際平移了幾個像素，
再換算成「畫面寬度的百分之幾」與「估計轉了幾度」（以水平視野 70 度估算）。

輸出：資料/<遊戲>/視角校準.json（含給模型看的「提示」字串）與 視角校準.md（人看的表）

用法：
    python calibrate_look.py                 # 預設 動物醫院，量 dx=60/120/200/300/400 與 dy=100/200/300
    python calibrate_look.py --repeat 3       # 每個值重複 3 次取中位數
需要 Roblox 視窗開著且在遊戲中（會實際轉動你的視角，約 1 分鐘）。
"""
import argparse
import json
import os
import statistics
import sys
import time

import numpy as np
import cv2
import mss
import pyautogui
import pydirectinput
from PIL import Image

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from play_agent import RobloxWindow  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
pyautogui.FAILSAFE = True
pydirectinput.PAUSE = 0.02
FOV_H = 70.0   # Roblox 預設水平視野約 70 度（估算用）


def grab(region):
    with (getattr(mss, "MSS", None) or mss.mss)() as sct:
        shot = sct.grab(region)
        im = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    g = np.asarray(im.convert("L"), dtype=np.float32)
    return g


def shift_of(a, b):
    """b 相對 a 的平移（像素）：用 ORB 特徵匹配取中位數位移；暗場景比相位相關可靠。
    回傳 (dx, dy, 信心=可用匹配點數/50)。"""
    ia = np.clip(a, 0, 255).astype(np.uint8)
    ib = np.clip(b, 0, 255).astype(np.uint8)
    orb = cv2.ORB_create(1200)
    ka, da = orb.detectAndCompute(ia, None)
    kb, db = orb.detectAndCompute(ib, None)
    if da is None or db is None or len(ka) < 8 or len(kb) < 8:
        return 0.0, 0.0, 0.0
    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    matches = sorted(bf.match(da, db), key=lambda m: m.distance)[:120]
    if len(matches) < 8:
        return 0.0, 0.0, 0.0
    dxs = [kb[m.trainIdx].pt[0] - ka[m.queryIdx].pt[0] for m in matches]
    dys = [kb[m.trainIdx].pt[1] - ka[m.queryIdx].pt[1] for m in matches]
    mx, my = statistics.median(dxs), statistics.median(dys)
    # 只留靠近中位數的點，過濾錯配
    good = [(x, y) for x, y in zip(dxs, dys) if abs(x - mx) < 60 and abs(y - my) < 60]
    if len(good) < 6:
        return 0.0, 0.0, 0.0
    return (statistics.median([g[0] for g in good]), statistics.median([g[1] for g in good]),
            min(1.0, len(good) / 50.0))


def center_mouse(region):
    pydirectinput.moveTo(region["left"] + region["width"] // 2, region["top"] + region["height"] // 2)
    time.sleep(0.1)


def drag(dx, dy, steps=8, region=None):
    if region:
        center_mouse(region)
    pydirectinput.mouseDown(button="right")
    for _ in range(steps):
        pydirectinput.moveRel(int(dx / steps), int(dy / steps), relative=True)
        time.sleep(0.02)
    pydirectinput.mouseUp(button="right")
    time.sleep(0.35)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", default="動物醫院")
    ap.add_argument("--repeat", type=int, default=2)
    ap.add_argument("--dx", default="60,120,200,300,400")
    ap.add_argument("--dy", default="100,200,300")
    a = ap.parse_args()

    win = RobloxWindow()
    if not win.find():
        print("找不到 Roblox 視窗，請先開遊戲"); return
    if not win.activate():
        print("無法把 Roblox 帶到前景"); return
    region = win.client_bbox()
    print(f"Roblox 視窗客戶區：{region}  前景={win.is_foreground()}")
    center_mouse(region)
    W, H = region["width"], region["height"]
    time.sleep(0.8)

    rows = []
    for axis, values in (("dx", [int(v) for v in a.dx.split(",") if v]), ("dy", [int(v) for v in a.dy.split(",") if v])):
        for v in values:
            moves = []
            for _ in range(a.repeat):
                before = grab(region)
                drag(v if axis == "dx" else 0, 0 if axis == "dx" else v, region=region)
                after = grab(region)
                sx, sy, resp = shift_of(before, after)
                moves.append((-sx if axis == "dx" else -sy, resp))
                # 轉回去，避免視角越轉越偏
                drag(-v if axis == "dx" else 0, 0 if axis == "dx" else -v, region=region)
                time.sleep(0.2)
            good = [m for m, r in moves if r >= 0.12]
            px = statistics.median(good) if good else 0.0
            span = W if axis == "dx" else H
            pct = 100 * px / span
            deg = pct / 100 * FOV_H if axis == "dx" else pct / 100 * (FOV_H * H / W)
            rows.append({"軸": axis, "拖曳像素": v, "畫面平移像素": round(px, 1),
                         "佔畫面": round(pct, 1), "估計角度": round(deg, 1),
                         "信心": round(statistics.median([r for _, r in moves]), 3)})
            print(f"  {axis}={v:>4} → 畫面移動 {px:7.1f}px（{pct:5.1f}% 寬/高，約 {deg:5.1f}°）")

    dxr = [r for r in rows if r["軸"] == "dx" and r["信心"] > 0.05]
    ratio = statistics.median([r["畫面平移像素"] / r["拖曳像素"] for r in dxr]) if dxr else None
    hint_parts = []
    for r in rows:
        if r["信心"] >= 0.12:
            hint_parts.append(f"{r['軸']}={r['拖曳像素']}→畫面轉{r['佔畫面']:.0f}%（約{r['估計角度']:.0f}度）")
    if ratio:
        quarter = int(round(0.25 * W / ratio / 10) * 10)
        half = int(round(0.5 * W / ratio / 10) * 10)
        full = int(round(1.0 * W / ratio / 10) * 10)
        hint_parts.append(f"換算：轉四分之一個畫面≈dx {quarter}、半個畫面≈dx {half}、整個畫面≈dx {full}；想微調就用 dx 60~120")
    hint = "；".join(hint_parts)

    d = os.path.join(HERE, "資料", a.game)
    os.makedirs(d, exist_ok=True)
    json.dump({"時間": time.strftime("%Y-%m-%d %H:%M"), "視窗": region, "量測": rows,
               "每像素畫面位移": round(ratio, 3) if ratio else None, "提示": hint},
              open(os.path.join(d, "視角校準.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    md = ["# 視角校準（滑鼠拖曳 → 畫面轉多少）", "",
          f"> {time.strftime('%Y-%m-%d %H:%M')} 實測，Roblox 視窗 {W}×{H}。畫面平移用相位相關算出。", "",
          "| 軸 | 拖曳像素 | 畫面平移(px) | 佔畫面 | 估計角度 | 信心 |", "|---|---|---|---|---|---|"]
    md += [f"| {r['軸']} | {r['拖曳像素']} | {r['畫面平移像素']} | {r['佔畫面']}% | {r['估計角度']}° | {r['信心']} |" for r in rows]
    md += ["", "## 給模型的提示", "", hint]
    open(os.path.join(d, "視角校準.md"), "w", encoding="utf-8").write("\n".join(md) + "\n")
    print("\n" + hint)
    print(f"→ 寫入 {d}\\視角校準.json / .md")


if __name__ == "__main__":
    main()
