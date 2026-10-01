# 排程啟動.ps1 — 給 Windows 工作排程器用（2026-10-02 使用者：小心電腦閃退）
# 對話關掉或電腦重開，Claude 的排程就沒了；這支由 Windows 自己在時間到時執行。
# 用 27B 的時限設定啟動動物醫院，玩到 21:00。代理已經在跑就不重複啟動。
Set-Location "D:\Documents\ThorWorkSpace\gamer AI"
$running = Get-CimInstance Win32_Process -Filter "name='python.exe'" | Where-Object { $_.CommandLine -like "*flow_agent.py*" }
if ($running) { exit 0 }
$env:GAMER_DEADLINE = "90"; $env:GAMER_REFLEX_DEADLINE = "40"; $env:GAMER_MAP = "on"
& ".\開始玩.ps1" -Game 動物醫院 -Until 21:00 *> "專案\動物醫院\記憶\排程啟動.log"
