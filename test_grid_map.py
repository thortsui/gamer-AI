# -*- coding: utf-8 -*-
"""
test_grid_map.py — 陣列地圖的離線測試（不用開遊戲）。

做一個假的場地（兩個房間、中間一道牆只開一扇門），假的「深度眼睛」照真實的牆回報距離（加雜訊），
看地圖能不能：把出生點當原點、用視覺畫出牆、只在第一次掃、用陣列找路（要繞過牆走門）、
存互動物／觸碰物、發現位置漂掉、存檔讀回來一樣。

  python test_grid_map.py
"""
import math
import os
import random
import sys
import tempfile

from grid_map import GridMap, WALL, FREE, INTERACT, TOUCH, UNKNOWN

sys.stdout.reconfigure(encoding="utf-8")
random.seed(7)

# ---- 假場地：x -6..6、y -4..10 的外牆；y=4 有一道橫牆，只在 x=3 開門
TRUE_WALLS = set()
for x in range(-6, 7):
    TRUE_WALLS |= {(x, -4), (x, 10)}
for y in range(-4, 11):
    TRUE_WALLS |= {(-6, y), (6, y)}
for x in range(-6, 7):
    if x != 3:
        TRUE_WALLS.add((x, 4))
COMPUTER = (-3, 3)          # 互動物：不可穿透
PAD = (0, 7)                # 觸碰物：可以站上去
SOLID = TRUE_WALLS | {COMPUTER}
REAL_SCALE = 0.5            # 假深度 1 單位＝0.5 步（地圖要自己量出來）


def true_range(x, y, yaw, max_d=30.0):
    th = math.radians(yaw)
    d = 0.05
    while d < max_d:
        c = (int(math.floor(x + math.sin(th) * d + 0.5)), int(math.floor(y + math.cos(th) * d + 0.5)))
        if c in SOLID:
            return d
        d += 0.05
    return max_d


def fake_rays(x, y, yaw, n=9, noise=0.06):
    """模擬 DepthEye.rays：回 (偏離角度, 沿鏡頭軸的深度)，單位是假的「公尺」。"""
    out = []
    for i in range(n):
        cx = (i + 0.5) / n
        ang = math.degrees(math.atan((cx - 0.5) * 2 * math.tan(math.radians(35))))
        rng = true_range(x, y, yaw + ang)
        z = rng * math.cos(math.radians(ang)) / REAL_SCALE
        out.append((ang, z * (1 + random.uniform(-noise, noise))))
    return out


def scan_here(m, x, y):
    for k in range(12):                       # 原地轉一圈，每次 30 度、各看兩次
        for _ in range(2):
            m.scan(x, y, k * 30.0, fake_rays(x, y, k * 30.0))
    m.note_scanned(x, y)


results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("✅" if ok else "❌"), name, detail)


m = GridMap(cell=1.0)

# 1. 出生點是原點、在陣列正中央
r, c = m._idx(0, 0)
check("出生點＝原點，在陣列正中央", m.cell_of(0.0, 0.0) == (0, 0) and r == c == m.size // 2, f"陣列 {m.size}×{m.size}，原點在 [{r}][{c}]")

# 2. 自己量深度比例：往前走 2 步，正前方深度的變化
z0 = true_range(0, 0, 0) / REAL_SCALE
z1 = true_range(0, 2, 0) / REAL_SCALE
s = m.calibrate(z0, z1, 2.0)
check("深度比例自己量得出來", s is not None and abs(s - REAL_SCALE) < 0.05, f"量到 {s:.3f}（真值 {REAL_SCALE}）")

# 3. 第一次到：需要掃；掃完：不需要
check("沒掃過的地方 need_scan＝要", m.need_scan(0, 0))
m.mark_walked(0, 0)
scan_here(m, 0, 0)
check("掃過之後 need_scan＝不用", not m.need_scan(0.5, 1.0))
check("走到遠處的新地方 need_scan＝要", m.need_scan(0, 8))

# 4. 視覺畫出來的牆對不對（只看第一個房間裡 8 步內看得到的牆）
found = {(gx, gy) for gx in range(-8, 9) for gy in range(-6, 12) if m.get(gx, gy) == WALL}
visible = {w for w in SOLID if w[1] <= 4 and math.hypot(*w) <= 8}
hit = len(found & SOLID)
near_miss = sum(1 for f in found - SOLID if any(abs(f[0] - w[0]) <= 1 and abs(f[1] - w[1]) <= 1 for w in SOLID))
recall = len({w for w in visible if any(abs(f[0] - w[0]) <= 1 and abs(f[1] - w[1]) <= 1 for f in found)}) / len(visible)
far_off = len(found) - hit - near_miss
check("視覺畫出的牆：看得到的牆有八成以上被畫到（容許差一格）", recall >= 0.8, f"涵蓋 {recall * 100:.0f}%")
check("視覺畫出的牆：沒有離真牆兩格以上的假牆", far_off == 0, f"畫了 {len(found)} 格：正中 {hit}、差一格 {near_miss}、離譜 {far_off}")
check("走過的格子不會被畫成牆", m.get(0, 0) == FREE)

# 5. 三種元素：互動物（不可穿透＋功能／用法存在一維陣列）、觸碰物（可以站上去）
i = m.add_object("互動物", COMPUTER[0], COMPUTER[1], "電腦", "按了跳出登記畫面", "走到旁邊長按 E 1.5 秒")
j = m.add_object("觸碰物", PAD[0], PAD[1], "發光踏板", "站上去跳出面板", "走上去站 1 秒")
check("互動物存進一維陣列、格子不可穿透", m.objects[i]["功能"] == "按了跳出登記畫面" and m.get(*COMPUTER) == INTERACT and not m.passable(*COMPUTER))
check("觸碰物存進一維陣列、格子可以站上去", m.objects[j]["種類"] == "觸碰物" and m.get(*PAD) == TOUCH and m.passable(*PAD))
m.add_object("互動物", COMPUTER[0], COMPUTER[1], "電腦", "", "")
check("同一個互動物再成功一次＝次數加一，不會重複存", len(m.objects) == 2 and m.objects[i]["成功次數"] == 2)

# 6. 到第二個房間也掃一次（穿過門走過去），再用陣列找路
for p in [(1, 0), (2, 1), (3, 2), (3, 3), (3, 4), (3, 5), (3, 6)]:
    m.mark_walked(*p)
scan_here(m, 3, 6)
path = m.find_path((0, 0), PAD)
through_wall = [c for c in path if c in SOLID]
check("找路：從原點到牆另一邊的觸碰物，找得到", bool(path) and path[-1] == PAD, f"{len(path)} 格")
check("找路：路線不穿牆、不穿互動物", not through_wall, f"穿過 {through_wall}" if through_wall else "")
check("找路：有經過那扇門 (3,4)", (3, 4) in path)
p2 = m.find_path((0, 0), COMPUTER)
check("找路：目標是互動物時停在它旁邊", bool(p2) and p2[-1] != COMPUTER and abs(p2[-1][0] - COMPUTER[0]) + abs(p2[-1][1] - COMPUTER[1]) == 1, f"停在 {p2[-1] if p2 else None}")

# 7. 路線換成「轉幾度、走幾秒」，照著走真的會到（用水平儀的角度，不用視覺）
x, y, yaw = 0.0, 0.0, 0.0
for seg in m.route((0.0, 0.0), 0.0, PAD):
    yaw = (yaw + seg["轉"]) % 360
    x += math.sin(math.radians(yaw)) * seg["走"]
    y += math.cos(math.radians(yaw)) * seg["走"]
check("路線換成轉頭＋走路，照做會到目的地", math.hypot(x - PAD[0], y - PAD[1]) < 0.6, f"到 ({x:.2f},{y:.2f})，目標 {PAD}")

# 8. 視覺核對：位置對的時候符合；推算漂了兩格時抓得出來、建議的位置比較接近真的
def steps_rays(px, py, yw):
    return [(a, z * REAL_SCALE / math.cos(math.radians(a))) for a, z in fake_rays(px, py, yw, noise=0.03)]
ok_pose = m.check_pose(0, 0, 0, steps_rays(0, 0, 0))
bad = m.check_pose(2, -2, 0, steps_rays(0, 0, 0))          # 真的在 (0,0)，地圖以為在 (2,-2)
check("視覺核對：位置對的時候說符合", ok_pose["符合"], f"誤差 {ok_pose['誤差']:.2f} 步")
sug = bad["建議位置"]
check("視覺核對：位置漂了抓得出來，建議的位置比較接近真的", (not bad["符合"]) and sug is not None and math.hypot(*sug) < math.hypot(2, -2),
      f"誤差 {bad['誤差']:.2f} → 建議 {sug}")

# 9. 撞到地圖上沒有的牆
m2 = GridMap()
m2.mark_walked(0, 0)
c = m2.mark_bump(0, 0, 90)
check("撞牆：前面那一格變成牆", c == (1, 0) and m2.get(1, 0) == WALL)

# 10. 陣列會自己長大
m2.mark_walked(60, -45)
check("走出陣列範圍會自己長大", m2.get(60, -45) == FREE and m2.cell_of(0, 0) == (0, 0) and m2.get(0, 0) == FREE, f"陣列變成 {m2.size}×{m2.size}")

# 11. 存檔讀回來一樣
p = os.path.join(tempfile.gettempdir(), "gridmap_test.json")
m.save(p)
k = GridMap.load(p)
same = (k.grid == m.grid and k.objects == m.objects and k.walked == m.walked and abs(k.scale - m.scale) < 1e-9
        and [tuple(a) for a in k.scanned] == [tuple(a) for a in m.scanned])
check("存檔讀回來一模一樣", same, f"{os.path.getsize(p)} bytes")

print()
print(m.text(3, 6, 0, radius=9))
print()
print(f"通過 {sum(results)}／{len(results)}")
sys.exit(0 if all(results) else 1)
