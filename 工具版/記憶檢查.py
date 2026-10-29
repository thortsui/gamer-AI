# -*- coding: utf-8 -*-
"""10/29 使用者：記憶檢查也做成收工（結尾）的步驟之一。
代理停了之後跑：python 工具版/記憶檢查.py [--game 動物醫院]
跟遊戲中定期的記憶檢查同一套（Tools.mem_check）：全部編號列給大腦、分三批、他決定哪些混亂／互斥／重複、程式照他寫的改（先備份）。"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tool_agent as T  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--game", default="動物醫院")
a = ap.parse_args()
import subprocess
r = subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | ? { $_.CommandLine -like '*tool_agent.py*' } | Measure-Object | % Count"],
                   capture_output=True, text=True)
if (r.stdout.strip() or "0") != "0":
    sys.exit("代理還在跑（遊戲中它自己會定期做記憶檢查）；收工要先 stop.txt 等它停了再跑這支")
tools = T.Tools(a.game, T.load_catalog(a.game), live=False, image_path=None)
print(tools.mem_check() or "沒有記憶可檢查")
