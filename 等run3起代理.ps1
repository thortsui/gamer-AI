# 等run3起代理.ps1（2026-10-14）：每 60 秒看 DGX 8888 有沒有 gamer-27b-ft-run3，
# 有了就用 10/12 的參數啟動代理玩動物醫院（run3 實戰 A/B）。獨立程序，不靠 Claude Code 活著。
# ⚠ -Until 是「HH:mm」：還沒過就算今天（21:35 啟動、Until 23:00 → 只跑 1.4 小時）；要跑到隔天就填一個已經過了的時刻（例如 20:00）。
#   Start-Process powershell -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','等run3起代理.ps1' -WindowStyle Hidden
param(
    [string]$Game  = "動物醫院",
    [string]$Until = "23:00",
    [int]$MaxMinutes = 240
)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path
$LOG  = Join-Path $ROOT "專案\$Game\記憶\等run3.log"
function Log($s) { $line = "[{0}] {1}" -f (Get-Date -Format "MM/dd HH:mm:ss"), $s; Add-Content -Path $LOG -Value $line -Encoding UTF8 }

Log "開始等 run3（最多 $MaxMinutes 分鐘）"
$t0 = Get-Date
$model = ""
while (((Get-Date) - $t0).TotalMinutes -lt $MaxMinutes) {
    try {
        $r = Invoke-WebRequest -Uri "http://192.168.0.126:8888/v1/models" -TimeoutSec 8 -UseBasicParsing
        $model = (($r.Content | ConvertFrom-Json).data | Select-Object -First 1).id
    } catch { $model = "" }
    if ($model -like "*run3*") { break }
    Start-Sleep 60
}
if ($model -notlike "*run3*") { Log "逾時：8888 還沒有 run3（最後看到「$model」），沒啟動代理"; exit 1 }
Log "8888 上線：$model，等 90 秒讓 vLLM 暖機"
Start-Sleep 90

$env:GAMER_PLAN_STEPS = "1"
$env:GAMER_DEADLINE = "90"
$env:GAMER_REFLEX_DEADLINE = "40"
$env:GAMER_MAP = "on"
$env:GAMER_TWO_PART = "on"
Log "啟動代理：開始玩.ps1 -Game $Game -Until $Until（PLAN_STEPS=1 DEADLINE=90 REFLEX=40 MAP=on TWO_PART=on）"
$out = & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $ROOT "開始玩.ps1") -Game $Game -Until $Until 2>&1
$out | ForEach-Object { Log ("  " + $_) }
Log "開始玩.ps1 結束 exit=$LASTEXITCODE"
