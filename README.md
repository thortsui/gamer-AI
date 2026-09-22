# gamer AI — 會教你玩 Roblox 的千問大腦

> 用家裡 DGX Spark 上的 **Qwen3.8-Flash-Next**（vLLM，支援看圖）當大腦，
> 餵它一份《Roblox 玩家入門說明書》，讓它當「教小朋友從安裝到進遊戲」的教練。
> 之後會放上 GitHub；`.gitignore` 已排除金鑰與資料。

## 資料夾

| 路徑 | 是什麼 |
|---|---|
| `roblox_ai.py` | 命令列聊天客戶端：連 DGX 模型、把說明書塞進 system prompt、可附截圖 |
| `screen_watch.py` | **即時看螢幕**：5 fps 擷取 → 最近 1 秒 5 張包成短影片送模型 → 5 秒硬截止 → 印出「畫面／下一步」並寫到 `資料/最新解讀.json` |
| `設定.json` | 端點、模型名（留空＝自動偵測）、溫度、思考模式等；第一次執行會自動產生 |
| `知識庫/Roblox玩家入門說明書.md` | 給模型讀的知識（安裝 → 帳號 → 找遊戲 → 進遊戲 → 操作 → 安全） |
| `知識庫/Roblox玩家入門說明書.docx` | 同一份說明書的 Word 版（給人看、可列印） |
| `DGX連線說明.md` | 怎麼連 DGX、怎麼啟停這顆模型、埠與坑 |
| `DGX部署/` | DGX 那邊的部署紀錄與指令備份 |
| `工作日誌/` | 每天做了什麼（`YYYY-MM-DD.md`） |

## 快速開始

```bash
# 1. 確認 DGX 上模型活著（沒有的話看 DGX連線說明.md 第 3 節）
python roblox_ai.py --check

# 2. 開始聊
python roblox_ai.py
# 或單問單答
python roblox_ai.py "我按 Play 沒反應怎麼辦"
```

聊天中：`/img 截圖路徑` 讓它看圖、`/think` 開關思考模式、`/clear` 清歷史、`/quit` 離開。

## 即時看螢幕

```bash
python screen_watch.py                   # 全螢幕，連續解讀，Ctrl+C 停
python screen_watch.py --window Roblox   # 只看標題含 Roblox 的視窗
python screen_watch.py --once            # 解讀一次
python screen_watch.py --ask "我該按哪裡？"   # 換問題
python screen_watch.py --bench           # 量延遲：video vs images 送法
```

每輪流程：背景 5 fps 擷取（縮到 640 寬）→ 取最近 1 秒 5 張 → OpenCV 包成 mp4（約 45 KB）→ 串流送模型（思考關、80 tokens 上限）→ 從擷取到拿到答案 **硬截止 5 秒**，超時就切斷、用已收到的字。

實測（2026-09-07，DGX 閒置、單人）：

| 設定 | 首字 | 一輪總時間 | prompt tokens |
|---|---|---|---|
| 影片送法、濃縮重點 prompt（**預設**） | 0.5～0.8 s | **1.5～1.8 s** | ~600～900 |
| 影片送法、夾整份說明書 | 1.8～2.8 s | 2.8～3.9 s | ~5,100 |
| 多圖送法（5 張 JPEG）、夾整份說明書 | 2.4～3.2 s | 3.2～4.7 s | ~5,800 |

所以模型「看」的是 5 fps 的連續畫面，每 1.5～2 秒給一次判斷。要更快可把 `畫面寬` 調 512、`看畫面max_tokens` 調 50。

輸出：終端機兩行（畫面／下一步）＋ `資料/最新解讀.json`（最新一筆）＋ `資料/螢幕解讀.jsonl`（歷史）。

## 模型端點

| 項目 | 值 |
|---|---|
| 正式 | `http://192.168.0.126:8888/v1`，模型名 `qwen3.8-flash-next` |
| 備援 | `http://192.168.0.126:8101/v1`（舊的 Qwen3.8-27B，兩者不能同時開） |

程式不寫死模型名，啟動時打 `/v1/models` 取第一個。

## 檔案現況（2026-09-11）

**現在的主力**

| 檔案 | 做什麼 |
|---|---|
| `flow_agent.py` | 主代理（工作流程模式）。大腦在 DGX，眼睛與文字眼睛在本機。 |
| `play_agent.py` | 基底類別：視窗、截圖、執行動作、與模型溝通。flow_agent 繼承它。 |
| `local_vision.py` | 眼睛：YOLO-World 開放詞彙偵測，背景執行緒。 |
| `local_text.py` | 文字眼睛：本機 OCR，每 1.2 秒讀一次畫面上的字。 |
| `網站/dashboard_server.py` | 即時台（port 8300）：畫面、思考、對話、完整提示。 |

**舊架構，保留作紀錄，不要再跑**

| 檔案 | 為什麼退役 |
|---|---|
| `skill_agent.py` | 技能狀態機太僵硬，常卡在「沒有任何技能適用」。 |
| `skills.py` | 只被 skill_agent 使用，跟著退役。閉環控制已搬進 flow_agent 的 goto_target。 |
| `checkin_agent.py` | 寫死步驟的單一用途代理，撐不住狀況變化。報到現在是 flow_agent 的一條工作流程。 |

這三個檔案的開頭都有標記橫幅。

**工具**

| 檔案 | 做什麼 |
|---|---|
| `calibrate_look.py` | 量視角靈敏度，結果寫進 `視角校準.json`，flow_agent 開機會讀。 |
| `check_agent.py` | 快速健康檢查：代理活著嗎、卡住了嗎、最近在做什麼。 |
| `daily_report.py` | 產生當天的進步日誌。 |
| `talk.py` | 直接跟代理對話。 |
| `screen_watch.py` | 螢幕擷取，被 play_agent 使用。 |
| `roblox_ai.py` | 最早的客戶端，專案起點，現在不在主線上。 |
