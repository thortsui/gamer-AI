# -*- coding: utf-8 -*-
"""
grid_map.py — 陣列地圖（2026-09-30 使用者定）

整張地圖用**二維陣列**存，**出生點是原點 (0,0)**。每一格是下面其中一種：

    0 未知　1 空地（走過或看過是空的）
    2 牆壁　　不可互動、不可穿透
    3 互動物　可以互動、不可穿透（功能與使用方式另外存在一維陣列 objects 裡）
    4 觸碰物　可以互動，但要用玩家自己走上去／碰到它才會生效（可以站上去）

怎麼長出來：
  - 走過的格子 → 空地（odometry：走幾秒＝幾步；朝向用程式自己送出去的轉頭量累加＝水平儀）。
  - 視覺深度 → 牆壁：在一個**沒掃過的地方**，程式自己原地轉一圈，每一面用深度模型量
    「這個方向多遠有東西擋住」，把那一格標成牆、中間標成空地（scan）。
  - **只有第一次到一個地方才需要用視覺掃**；之後靠陣列地圖＋水平儀推算就好（need_scan）。
    真的撞到地圖上沒有的牆（bump）或視覺核對對不上（check_pose）才重掃。
  - 互動成功／站上去有反應 → 互動物／觸碰物（add_object）。
路線：find_path 用 A* 在陣列上找路（不穿牆、不穿互動物），route 把它換成「轉幾度、走幾秒」。

單位：x 往東為正、y 往北為正，出生時面朝北（yaw 0，往右轉變大）。1 步＝走 1 秒。
"""
import heapq
import json
import math
import os

UNKNOWN, FREE, WALL, INTERACT, TOUCH = 0, 1, 2, 3, 4
NAMES = {UNKNOWN: "未知", FREE: "空地", WALL: "牆壁", INTERACT: "互動物", TOUCH: "觸碰物"}
GLYPH = {UNKNOWN: "　", FREE: "·", WALL: "█", INTERACT: "◆", TOUCH: "◎"}
BLOCKING = (WALL, INTERACT)            # 不可穿透
KIND = {"牆壁": WALL, "互動物": INTERACT, "觸碰物": TOUCH}


class GridMap:
    def __init__(self, cell=1.0, size=41):
        self.cell = float(cell)                # 一格幾步
        self.size = int(size) | 1              # 陣列邊長（奇數，原點在正中央）
        self.grid = [[UNKNOWN] * self.size for _ in range(self.size)]     # 二維陣列：grid[列][欄]
        self.hits = {}                         # (gx,gy) → 被深度量到幾次（次數夠了才算牆）
        self.walked = set()                    # 真的走過的格子（不會被改成牆）
        self.scanned = []                      # 掃過的位置 [(x, y)]
        self.objects = []                      # 一維陣列：互動物／觸碰物的功能與使用方式
        self.scale = None                      # 深度模型的 1 單位＝幾步（定案後不再變）
        self.samples = []                      # 還沒定案前量到的樣本
        self.cam_back = 0.0                    # 第三人稱：鏡頭在自己後面幾步
        self.pending = []                      # 比例還沒定時看到的視野 [x, y, yaw, rays, self]；定了比例再補畫成牆
        self.path = ""

    # ---------------- 座標 ↔ 陣列
    def cell_of(self, x, y):
        return (int(math.floor(x / self.cell + 0.5)), int(math.floor(y / self.cell + 0.5)))

    def center_of(self, gx, gy):
        return (gx * self.cell, gy * self.cell)

    def _idx(self, gx, gy):
        """格子座標（原點＝出生點）→ 陣列的 (列, 欄)；超出範圍就把陣列長大。"""
        half = self.size // 2
        while abs(gx) > half or abs(gy) > half:
            self._grow()
            half = self.size // 2
        return (half - gy, gx + half)          # 列 0 在最北邊

    def _grow(self):
        pad = 10
        n = self.size + 2 * pad
        g = [[UNKNOWN] * n for _ in range(n)]
        for r in range(self.size):
            g[r + pad][pad:pad + self.size] = self.grid[r]
        self.grid, self.size = g, n

    def get(self, gx, gy):
        half = self.size // 2
        if abs(gx) > half or abs(gy) > half:
            return UNKNOWN
        r, c = self._idx(gx, gy)
        return self.grid[r][c]

    def set(self, gx, gy, v):
        r, c = self._idx(gx, gy)
        self.grid[r][c] = v

    # ---------------- 長地圖
    def mark_walked(self, x, y):
        """走過＝空地（觸碰物站上去也算走過，但格子的種類不變）。"""
        c = self.cell_of(x, y)
        self.walked.add(c)
        if self.get(*c) in (UNKNOWN, WALL):
            self.set(c[0], c[1], FREE)
            self.hits.pop(c, None)

    def mark_bump(self, x, y, yaw):
        """往前走走不動＝前面那一格是牆。"""
        th = math.radians(yaw)
        c = self.cell_of(x + math.sin(th) * self.cell, y + math.cos(th) * self.cell)
        if c not in self.walked and self.get(*c) in (UNKNOWN, FREE):
            self.set(c[0], c[1], WALL)
            self.hits[c] = 99                   # 撞到的是硬證據，之後視線「穿過」它也不退
            return c
        return None

    def mark_ray(self, x, y, yaw, dist, hit=True, confirm=2):
        """從 (x,y) 朝 yaw 看出去，dist 步的地方有東西擋住：中間是空地、那一格是牆。

        hit=False 代表「看了 dist 步都沒東西」（只標空地）。
        深度量測有雜訊，所以同一格要被量到 confirm 次才真的變成牆。
        """
        th = math.radians(yaw)
        sx, sy = math.sin(th), math.cos(th)
        n = int(dist / (self.cell * 0.5))
        # 量到的是擋路的東西的「表面」，它本身在表面再往裡一點——往裡推 0.4 格再取格子，
        # 不然表面剛好落在兩格交界時，雜訊會讓牆一半畫在這格、一半畫在前一格（牆變厚、往自己這邊長）。
        deep = dist + self.cell * 0.4
        end = self.cell_of(x + sx * deep, y + sy * deep)
        for i in range(1, n):
            c = self.cell_of(x + sx * i * self.cell * 0.5, y + sy * i * self.cell * 0.5)
            if c == end:
                break
            if self.get(*c) == UNKNOWN:
                self.set(c[0], c[1], FREE)
            elif self.get(*c) == WALL and self.hits.get(c, 0) < 50:
                # 視線穿過一格「牆」＝那格其實沒東西（以前量錯、或東西被搬走）：扣一次，扣完就退回空地
                self.hits[c] = self.hits.get(c, 0) - 1
                if self.hits[c] <= 0:
                    self.set(c[0], c[1], FREE)
                    self.hits.pop(c, None)
        if not hit or end in self.walked or self.get(*end) in (INTERACT, TOUCH):
            return None
        self.hits[end] = self.hits.get(end, 0) + 1
        if self.hits[end] >= confirm and self.get(*end) in (UNKNOWN, FREE):
            self.set(end[0], end[1], WALL)
            return end
        return None

    def scan(self, x, y, yaw, rays, max_dist=10.0, self_depth=None):
        """一面畫面的深度 → 地圖。rays＝[(偏離正前方的角度, 沿鏡頭軸的深度)…]（DepthEye.rays 的輸出）。

        深度換成步數用 self.scale；第三人稱時鏡頭在自己後面 cam_back 步，視線從鏡頭位置出發。
        回傳這一面新增了幾格牆。
        """
        if not self.scale:
            return 0
        if self_depth:
            self.cam_back = 0.7 * self.cam_back + 0.3 * self_depth * self.scale if self.cam_back else self_depth * self.scale
        th = math.radians(yaw)
        cx, cy = x - math.sin(th) * self.cam_back, y - math.cos(th) * self.cam_back
        new = 0
        for ang, z in rays:
            rng = z * self.scale / max(0.2, math.cos(math.radians(ang)))     # 沿視線的距離
            rng -= 0.0
            hit = rng <= max_dist + self.cam_back
            d = min(rng, max_dist + self.cam_back)
            if d <= self.cam_back + 0.3:
                continue                                                    # 擋在鏡頭跟自己之間，不可信
            if self.mark_ray(cx, cy, (yaw + ang) % 360, d, hit=hit):
                new += 1
        return new

    def keep_view(self, x, y, yaw, rays, self_depth=None):
        """比例還沒定：先把這一面的深度存起來，等他量好比例再一次補畫（2026-10-02 使用者：第一次先不動、轉九十度慢慢畫）。"""
        self.pending = (self.pending + [[round(x, 2), round(y, 2), round(yaw % 360, 1),
                                          [[round(a, 1), round(z, 3)] for a, z in rays], self_depth]])[-80:]

    def replay_pending(self, max_dist=10.0):
        """比例定了：把之前存著的視野全部補畫成牆。回傳新畫幾格。"""
        if not self.scale or not self.pending:
            return 0
        new = 0
        for x, y, yaw, rays, me in self.pending:
            new += self.scan(x, y, yaw, [tuple(r) for r in rays], max_dist=max_dist, self_depth=me)
        self.pending = []
        return new

    def note_scanned(self, x, y):
        self.scanned.append((round(x, 1), round(y, 1)))

    def need_scan(self, x, y, radius=4.0):
        """這附近掃過了嗎？沒掃過才需要用視覺（使用者：只有第一次需要用視覺判斷）。"""
        return not any(math.hypot(x - a, y - b) <= radius for a, b in self.scanned)

    def calibrate(self, depth_before, depth_after, walked_steps):
        """量比例：往前走 walked_steps 步，正前方的深度從 before 變成 after → 深度 1 單位＝幾步。"""
        dd = depth_before - depth_after
        if walked_steps <= 0 or dd <= 0.05:
            return None
        s = walked_steps / dd
        if not (0.05 <= s <= 20):
            return None
        self.scale = s if not self.scale else 0.6 * self.scale + 0.4 * s
        return self.scale

    # ---------------- 互動物／觸碰物（一維陣列）
    def add_object(self, kind, x, y, name, func="", how=""):
        """kind：'互動物' 或 '觸碰物'。同一格同一種再記一次＝更新功能、使用方式、成功次數。"""
        k = KIND.get(kind)
        if k not in (INTERACT, TOUCH):
            return None
        c = self.cell_of(x, y)
        for i, o in enumerate(self.objects):
            if tuple(o["格"]) == c and o["種類"] == kind:
                o["名稱"] = name or o["名稱"]
                o["功能"] = func or o["功能"]
                o["使用方式"] = how or o["使用方式"]
                o["成功次數"] = o.get("成功次數", 0) + 1
                return i
        self.objects.append({"編號": len(self.objects), "種類": kind, "格": [c[0], c[1]],
                             "名稱": str(name)[:20], "功能": str(func)[:40], "使用方式": str(how)[:40],
                             "成功次數": 1})
        self.set(c[0], c[1], k)
        self.hits.pop(c, None)
        return len(self.objects) - 1

    def object_at(self, gx, gy):
        for o in self.objects:
            if tuple(o["格"]) == (gx, gy):
                return o
        return None

    def find_object(self, name):
        for o in self.objects:
            if o["名稱"] == name:
                return o
        return None

    def objects_in_view(self, x, y, yaw, half_fov=35.0, max_dist=10.0):
        """照地圖，現在畫面裡應該看得到哪些互動物／觸碰物：[(物件, 偏離角度, 幾步)]。"""
        out = []
        for o in self.objects:
            ox, oy = self.center_of(*o["格"])
            d = math.hypot(ox - x, oy - y)
            ang = ((math.degrees(math.atan2(ox - x, oy - y)) - yaw) + 180) % 360 - 180
            if 0.5 <= d <= max_dist and abs(ang) <= half_fov:
                out.append((o, ang, d))
        return out

    # ---------------- 視覺核對：我真的在地圖說的位置嗎
    def expected(self, x, y, yaw, max_dist=10.0):
        """照地圖，從 (x,y) 朝 yaw 看出去，幾步之後會碰到不可穿透的東西（沒有就回 None）。"""
        th = math.radians(yaw)
        sx, sy = math.sin(th), math.cos(th)
        start = self.cell_of(x, y)
        d = self.cell * 0.5
        while d <= max_dist:
            c = self.cell_of(x + sx * d, y + sy * d)
            if c != start and self.get(*c) in BLOCKING:
                return d
            d += self.cell * 0.25
        return None

    def check_pose(self, x, y, yaw, rays, tol=1.5, search=2):
        """拿現在量到的各方向距離，跟地圖上「應該看到的」比。

        rays＝[(偏離角度, 幾步)]（已經換成步數、從自己算起）。
        回傳 {"誤差", "符合", "建議位置", "建議誤差"}：差太多時在附近 ±search 格找一個最對得上的位置。
        """
        def err(px, py):
            es = []
            for ang, d in rays:
                e = self.expected(px, py, (yaw + ang) % 360)
                if e is not None and d is not None:
                    es.append(abs(e - d))
            return (sum(es) / len(es)) if es else None
        e0 = err(x, y)
        out = {"誤差": e0, "符合": e0 is not None and e0 <= tol, "建議位置": None, "建議誤差": None}
        if e0 is None or e0 <= tol:
            return out
        best = (e0, (x, y))
        for dx in range(-search, search + 1):
            for dy in range(-search, search + 1):
                px, py = x + dx * self.cell, y + dy * self.cell
                if self.get(*self.cell_of(px, py)) in BLOCKING:
                    continue
                e = err(px, py)
                if e is not None and e < best[0] - 0.3:
                    best = (e, (px, py))
        if best[1] != (x, y):
            out["建議位置"], out["建議誤差"] = best[1], best[0]
        return out

    # ---------------- 路線
    def passable(self, gx, gy, allow_unknown=True):
        v = self.get(gx, gy)
        if v in BLOCKING:
            return False
        return allow_unknown or v != UNKNOWN

    def find_path(self, start, goal, allow_unknown=True, limit=6000):
        """A*。start／goal 是 (x, y) 步座標。goal 是互動物就走到它旁邊。回傳格子清單（含起點），找不到回 []。"""
        s, g = self.cell_of(*start), self.cell_of(*goal)
        goals = {g}
        if self.get(*g) in BLOCKING:
            goals = {(g[0] + dx, g[1] + dy) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
                     if self.passable(g[0] + dx, g[1] + dy, allow_unknown)}
            if not goals:
                return []
        if s in goals:
            return [s]
        h = lambda c: min(abs(c[0] - t[0]) + abs(c[1] - t[1]) for t in goals)
        openq = [(h(s), 0.0, s)]
        came, cost = {s: None}, {s: 0.0}
        n = 0
        while openq and n < limit:
            _, gc, cur = heapq.heappop(openq)
            n += 1
            if cur in goals:
                path = []
                while cur is not None:
                    path.append(cur)
                    cur = came[cur]
                return path[::-1]
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
                nx = (cur[0] + dx, cur[1] + dy)
                if not self.passable(nx[0], nx[1], allow_unknown):
                    continue
                if dx and dy and not (self.passable(cur[0] + dx, cur[1], allow_unknown)
                                      and self.passable(cur[0], cur[1] + dy, allow_unknown)):
                    continue                                   # 不切牆角
                step = math.hypot(dx, dy) * (1.0 if self.get(*nx) != UNKNOWN else 2.5)   # 沒看過的地方貴一點
                nc = gc + step
                if nc < cost.get(nx, 1e18):
                    cost[nx], came[nx] = nc, cur
                    heapq.heappush(openq, (nc + h(nx), nc, nx))
        return []

    def route(self, start, yaw, goal, allow_unknown=True):
        """路線換成動作：[{"轉": 度（正＝往右）, "走": 秒, "到": (x, y)}…]。同方向的格子併成一段。"""
        cells = self.find_path(start, goal, allow_unknown)
        if len(cells) < 2:
            return []
        segs, cur_dir, run = [], None, 0
        pts = [self.center_of(*c) for c in cells]
        pts[0] = tuple(start)
        legs = []
        for a, b in zip(pts, pts[1:]):
            d = (round(b[0] - a[0], 3), round(b[1] - a[1], 3))
            head = round(math.degrees(math.atan2(d[0], d[1])) % 360, 1)
            if legs and abs(((head - legs[-1]["朝"]) + 180) % 360 - 180) < 1.0:
                legs[-1]["走"] += math.hypot(*d)
                legs[-1]["到"] = b
            else:
                legs.append({"朝": head, "走": math.hypot(*d), "到": b})
        facing = yaw
        for lg in legs:
            turn = ((lg["朝"] - facing) + 180) % 360 - 180
            segs.append({"轉": round(turn, 1), "走": round(lg["走"] * 1.0, 2), "到": (round(lg["到"][0], 2), round(lg["到"][1], 2))})
            facing = lg["朝"]
        return segs

    # ---------------- 給他看的文字
    def counts(self):
        c = {k: 0 for k in NAMES}
        for row in self.grid:
            for v in row:
                c[v] += 1
        return c

    def text(self, x, y, yaw, radius=9):
        """以他為中心的俯視圖（上面是北）＋一維陣列裡的互動物／觸碰物清單。"""
        me = self.cell_of(x, y)
        arrow = "↑↗→↘↓↙←↖"[int(((yaw % 360) + 22.5) // 45) % 8]
        rows = []
        for gy in range(me[1] + radius, me[1] - radius - 1, -1):
            line = []
            for gx in range(me[0] - radius, me[0] + radius + 1):
                if (gx, gy) == me:
                    line.append(arrow)
                elif (gx, gy) == (0, 0):
                    line.append("原")
                else:
                    line.append(GLYPH[self.get(gx, gy)])
            rows.append("".join(line))
        c = self.counts()
        head = (f"（一格 {self.cell:g} 步，上面是北。出生點是原點「原」(0,0)；你在 ({x:+.1f},{y:+.1f})＝格 {me}，"
                f"箭頭是你面朝的方向 {yaw:.0f}°）")
        key = "█ 牆壁（不能過）　◆ 互動物（不能過，走到旁邊用）　◎ 觸碰物（走上去才生效）　· 空地　空白 還不知道"
        stat = f"牆壁 {c[WALL]} 格、空地 {c[FREE]} 格、互動物 {c[INTERACT]} 個、觸碰物 {c[TOUCH]} 個"
        objs = []
        for o in self.objects:
            ox, oy = self.center_of(*o["格"])
            d = math.hypot(ox - x, oy - y)
            ang = ((math.degrees(math.atan2(ox - x, oy - y)) - yaw) + 180) % 360 - 180
            objs.append(f"　[{o['編號']}] {o['種類']}「{o['名稱']}」格 ({o['格'][0]},{o['格'][1]})，在你{_side(ang)} {d:.1f} 步"
                        f"｜功能：{o['功能'] or '（還沒寫）'}｜用法：{o['使用方式'] or '（還沒寫）'}｜成功 {o.get('成功次數', 0)} 次")
        return "\n".join([head, "```", *rows, "```", key, stat] + (["互動物／觸碰物："] + objs if objs else []))

    # ---------------- 存檔
    def to_dict(self):
        return {"說明": "陣列地圖：0未知 1空地 2牆壁 3互動物 4觸碰物；出生點＝原點，陣列正中央；列 0 在最北邊",
                "一格幾步": self.cell, "邊長": self.size,
                "陣列": ["".join(str(v) for v in row) for row in self.grid],
                "物件": self.objects,
                "量到次數": [[k[0], k[1], v] for k, v in self.hits.items()],
                "走過": [list(c) for c in sorted(self.walked)],
                "掃過的位置": [list(p) for p in self.scanned],
                "深度比例": self.scale, "比例樣本": self.samples, "鏡頭在後面幾步": self.cam_back,
                "待畫的視野": self.pending}

    def save(self, path=None):
        path = path or self.path
        if not path:
            return
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)

    @classmethod
    def load(cls, path, cell=1.0):
        m = cls(cell=cell)
        m.path = path
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, json.JSONDecodeError):
            return m
        try:
            m.cell = float(d.get("一格幾步", cell))
            rows = d.get("陣列") or []
            if rows:
                m.size = len(rows)
                m.grid = [[int(ch) for ch in row] for row in rows]
            m.objects = list(d.get("物件") or [])
            m.hits = {(a, b): v for a, b, v in d.get("量到次數") or []}
            m.walked = {tuple(c) for c in d.get("走過") or []}
            m.scanned = [tuple(p) for p in d.get("掃過的位置") or []]
            m.scale = d.get("深度比例")
            m.samples = list(d.get("比例樣本") or [])
            m.cam_back = float(d.get("鏡頭在後面幾步") or 0.0)
            m.pending = list(d.get("待畫的視野") or [])
        except (TypeError, ValueError):
            pass
        return m

    @classmethod
    def merge(cls, maps, path=""):
        """同一個場景的好幾張圖合成一張（2026-10-02 使用者：要合成）。都以出生點為原點，直接疊：
        走過的一定是空地；互動物／觸碰物 > 牆 > 空地 > 未知；量到次數相加；物件同格同種只留一個。"""
        out = cls()
        out.path = path
        rank = {UNKNOWN: 0, FREE: 1, WALL: 2, TOUCH: 3, INTERACT: 3}
        for m in maps:
            half = m.size // 2
            for r, row in enumerate(m.grid):
                for c, v in enumerate(row):
                    if v == UNKNOWN:
                        continue
                    gx, gy = c - half, half - r
                    if rank[v] > rank[out.get(gx, gy)]:
                        out.set(gx, gy, v)
            for k, v in m.hits.items():
                out.hits[k] = out.hits.get(k, 0) + v
            out.walked |= m.walked
            out.scanned += [p for p in m.scanned if p not in out.scanned]
            out.samples += m.samples
            out.pending += m.pending
            out.scale = out.scale or m.scale
            out.cam_back = out.cam_back or m.cam_back
            for o in m.objects:
                if not any(tuple(p["格"]) == tuple(o["格"]) and p["種類"] == o["種類"] for p in out.objects):
                    out.objects.append(dict(o, 編號=len(out.objects)))
        for c in out.walked:
            if out.get(*c) == WALL:
                out.set(c[0], c[1], FREE)
                out.hits.pop(c, None)
        out.samples = out.samples[-12:]
        out.pending = out.pending[-80:]
        return out


def _side(ang):
    a = abs(ang)
    return ("正前方" if a <= 20 else ("右前方" if ang > 0 else "左前方") if a <= 70 else
            ("正右邊" if ang > 0 else "正左邊") if a <= 110 else
            ("右後方" if ang > 0 else "左後方") if a <= 160 else "正後方")
