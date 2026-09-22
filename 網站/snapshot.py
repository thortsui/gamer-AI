#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""snapshot.py — 把「它在想什麼」做成一個不需伺服器的離線 HTML（可雙擊、可傳給別人）

用法：python 網站/snapshot.py            # 輸出 網站/快照_YYYYMMDD_HHMM.html
      python 網站/snapshot.py --open     # 產生後直接開啟
"""
import argparse
import base64
import io
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import dashboard_server as ds  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", default="動物醫院")
    ap.add_argument("--open", action="store_true")
    a = ap.parse_args()
    ds.GAME = a.game
    st = ds.state()
    try:
        shot = "data:image/jpeg;base64," + base64.b64encode(ds.grab_jpeg(900, 55)).decode()
    except Exception:  # noqa: BLE001
        shot = ""
    page = open(os.path.join(HERE, "thinking.html"), encoding="utf-8").read()
    # 內嵌資料，改寫 fetch 與截圖更新
    inject = ("<script>window.__SNAPSHOT__=" + json.dumps(st, ensure_ascii=False) +
              ";window.__SHOT__=" + json.dumps(shot) + ";</script>")
    page = page.replace("<script>\n(function(){", inject + "\n<script>\n(function(){", 1)
    page = page.replace('setInterval(function(){ if(paused) return; var im=new Image();',
                        'if(window.__SHOT__) document.getElementById("shot").src=window.__SHOT__;\n  if(false) setInterval(function(){ var im=new Image();', 1)
    page = page.replace('function tick(){ if(paused) return; fetch("/api/state?t="+Date.now()).then(function(r){return r.json();}).then(render).catch(function(){',
                        'function tick(){ render(window.__SNAPSHOT__); if(false) fetch("/api/state").then(function(r){return r.json();}).then(render).catch(function(){', 1)
    page = page.replace("tick(); setInterval(tick,3000);", "tick();", 1)
    page = page.replace("<title>它在想什麼</title>", f"<title>它在想什麼（{time.strftime('%m/%d %H:%M')} 快照）</title>", 1)
    out = os.path.join(HERE, f"快照_{time.strftime('%Y%m%d_%H%M')}.html")
    open(out, "w", encoding="utf-8").write(page)
    print(f"→ {out}（{os.path.getsize(out)//1024} KB，含 {len(st.get('最近', []))} 輪與一張畫面）")
    if a.open:
        os.startfile(out)  # noqa: S606


if __name__ == "__main__":
    main()
