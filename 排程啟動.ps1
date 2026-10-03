# 排程啟動.ps1 — 給 Windows 工作排程器用（2026-10-02 使用者：小心電腦閃退）
# 對話關掉或電腦重開，Claude 的排程就沒了；這支由 Windows 自己在時間到時執行。
# 用 27B 的時限設定啟動動物醫院，玩到 23:00（10/03 使用者：繼續到晚上十一點）。代理已經在跑就不重複啟動；22:30 以後不再啟動。
Set-Location "D:\Documents\ThorWorkSpace\gamer AI"
$running = Get-CimInstance Win32_Process -Filter "name='python.exe'" | Where-Object { $_.CommandLine -like "*flow_agent.py*" }
if ($running) {
    # 10/03 使用者：Roblox 被關掉也要重開（代理在跑時它自己會重開；這裡是保險）
    if (-not (Get-Process RobloxPlayerBeta -ErrorAction SilentlyContinue)) { Start-Process "roblox://experiences/start?placeId=78515283254292" }
    exit 0
}
if ((Get-Date).Hour -ge 23 -or ((Get-Date).Hour -eq 22 -and (Get-Date).Minute -ge 30)) { exit 0 }
$env:GAMER_DEADLINE = "90"; $env:GAMER_REFLEX_DEADLINE = "40"; $env:GAMER_MAP = "on"
& ".\開始玩.ps1" -Game 動物醫院 -Until 23:00 *> "專案\動物醫院\記憶\排程啟動.log"
