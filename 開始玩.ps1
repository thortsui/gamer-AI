# 開始玩.ps1 — 一鍵啟動 gamer AI（大腦檢查 → Roblox → 代理＋眼睛（YOLO／OCR 在代理裡面）→ 儀表板）
#
#   .\開始玩.ps1                       # 玩「新遊戲」，玩到今天/明天 21:00
#   .\開始玩.ps1 -Game 新遊戲 -Until 21:00
#   .\開始玩.ps1 -Game 動物醫院 -Until 18:30
#
# 停止：在專案根目錄建 stop.txt（他會自己收工、寫回記憶），之後再刪掉 stop.txt。
param(
    [string]$Game = "新遊戲",
    [string]$Until = "21:00"
)
$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ROOT   = Split-Path -Parent $MyInvocation.MyCommand.Path
$PY     = "C:\Python313\python.exe"
if (-not (Test-Path $PY)) { $PY = (Get-Command python).Source }
$MEM    = Join-Path $ROOT "專案\$Game\記憶"
$DGX    = "http://192.168.0.126:8888/v1/models"

function Say($s) { Write-Host ("[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $s) }

# 0. 專案存在？
if (-not (Test-Path (Join-Path $ROOT "專案\$Game\遊戲.json"))) {
    Say "❌ 找不到 專案\$Game\遊戲.json，遊戲名字打錯了？"; exit 1
}
New-Item -ItemType Directory -Force $MEM | Out-Null

# 1. 收工時間 → 小時數
$end = [datetime]::ParseExact($Until, "HH:mm", $null)
$now = Get-Date
$end = Get-Date -Hour $end.Hour -Minute $end.Minute -Second 0
if ($end -le $now) { $end = $end.AddDays(1) }
$hours = [math]::Round(($end - $now).TotalHours, 2)
Say ("玩「{0}」到 {1:MM/dd HH:mm}（{2} 小時）" -f $Game, $end, $hours)

# 2. DGX 大腦活著？
try {
    $r = Invoke-WebRequest -Uri $DGX -TimeoutSec 8 -UseBasicParsing
    $model = (($r.Content | ConvertFrom-Json).data | Select-Object -First 1).id
    Say "🧠 DGX 大腦在線：$model"
} catch {
    Say "❌ DGX 8888 沒回應，代理沒有大腦跑不動。照 DGX連線說明.md 先把 vllm-fn-tp1 開起來。"; exit 1
}

# 3. 舊的代理／訓練還在跑？先清掉
$old = Get-CimInstance Win32_Process -Filter "name='python.exe'" |
       Where-Object { $_.CommandLine -like "*flow_agent.py*" -or $_.CommandLine -like "*play_agent.py*" }
foreach ($p in $old) { Say "🧹 關掉舊代理 PID $($p.ProcessId)"; Stop-Process -Id $p.ProcessId -Force -Confirm:$false }
$train = Get-CimInstance Win32_Process -Filter "name='python.exe'" | Where-Object { $_.CommandLine -like "*train_eyes.py*" }
if ($train) {
    Say "⏳ 眼睛還在訓練（train_eyes.py），等它做完再啟動（最多等 10 分鐘）…"
    $t0 = Get-Date
    while ((Get-CimInstance Win32_Process -Filter "name='python.exe'" | Where-Object { $_.CommandLine -like "*train_eyes.py*" }) -and ((Get-Date) - $t0).TotalMinutes -lt 10) { Start-Sleep 10 }
}
foreach ($f in @("stop.txt", "停止.txt")) {
    $p = Join-Path $ROOT $f
    if (Test-Path $p) { Remove-Item $p -Force; Say "🧹 移除 $f" }
}

# 4. Roblox 開著嗎？沒有就用深層連結開到這個遊戲的大廳
$rbx = Get-Process RobloxPlayerBeta -ErrorAction SilentlyContinue
if (-not $rbx) {
    $place = (Get-Content (Join-Path $ROOT "專案\$Game\遊戲.json") -Raw -Encoding UTF8 | ConvertFrom-Json)."大廳place"
    Say "🎮 Roblox 沒開，用深層連結開 place $place（等 40 秒載入）"
    Start-Process "roblox://experiences/start?placeId=$place"
    Start-Sleep 40
} else {
    Say "🎮 Roblox 已在跑（PID $($rbx.Id)），視窗請保持最大化、電腦不要鎖定"
}

# 5. 啟動代理（眼睛 YOLO-World、文字 OCR 都是代理裡的執行緒，會一起起來）
$out = Join-Path $MEM "stdout.log"; $err = Join-Path $MEM "stderr.log"
$proc = Start-Process -FilePath $PY -WorkingDirectory $ROOT -WindowStyle Hidden -PassThru `
    -ArgumentList '-u','flow_agent.py','--game',$Game,'--hours',$hours `
    -RedirectStandardOutput $out -RedirectStandardError $err
Say "🚀 代理已啟動 PID $($proc.Id)（隱藏視窗，輸出在 專案\$Game\記憶\stdout.log）"

# 6. 儀表板（8300）沒開就開
if (-not (Get-NetTCPConnection -LocalPort 8300 -State Listen -ErrorAction SilentlyContinue)) {
    Start-Process -FilePath $PY -WorkingDirectory (Join-Path $ROOT "網站") -WindowStyle Hidden `
        -ArgumentList '-u','dashboard_server.py','--port','8300','--game',$Game | Out-Null
    Say "📊 儀表板已啟動 http://localhost:8300（/ 總覽、/think 想什麼、/flow 看到→想做→做了、/chat 對話）"
} else {
    Say "📊 儀表板已經在 8300"
}

# 7. 等 45 秒確認真的動起來（眼睛載入約 20～30 秒）
Start-Sleep 45
if ($proc.HasExited) {
    Say "❌ 代理 45 秒內就結束了，stderr 最後幾行："
    Get-Content $err -Tail 15 -ErrorAction SilentlyContinue
    exit 1
}
Say "✅ 代理活著。stdout 最後幾行："
Get-Content $out -Tail 6 -Encoding UTF8 -ErrorAction SilentlyContinue
