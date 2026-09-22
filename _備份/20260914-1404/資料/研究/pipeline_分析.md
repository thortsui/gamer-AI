# gamer AI 技術文件：讓 DGX 上的 Qwen3.8-Flash-Next 自己玩 Roblox

> 本文根據專案原始碼（`play_agent.py`、`screen_watch.py`、`check_agent.py`、`設定.json`）、說明文件（`README.md`、`DGX連線說明.md`、`DGX部署/部署紀錄_Qwen3.8-Flash-Next.md`）與工作日誌（`2026-09-06.md`、`2026-09-07.md`）整理而成。
> 所有數字、門檻、函式名與行號都直接引自檔案；檔案裡找不到的一律標「未記錄」。行號以 2026-09-07 11:22 版的 `play_agent.py` 與 2026-09-07 00:40 版的 `screen_watch.py` 為準。
> 另外從 `資料/動物醫院/行動日誌.jsonl`（01:06:47～11:29:31，共 7,532 筆）統計了幾個實測值，會標「日誌統計」。

---

## 0. 一句話講完

筆電上的 `play_agent.py` 每一輪做五件事——**看**（把最近 1 秒的螢幕包成 mp4，加一張 1024 寬的格線清楚圖）→ **想**（送到區網另一台 DGX Spark 上的 vLLM，讓 Qwen3.8-Flash-Next 回一個很短的 JSON）→ **守**（程式端硬性規則：任何花錢的動作一律擋、付費視窗只准 Esc、可疑按鈕點前先看放大圖確認）→ **做**（用 `pydirectinput` 把鍵盤滑鼠送進 Roblox 視窗）→ **記**（行動日誌、研究筆記、玩法摘要、截圖）。整輪從擷取到拿到決策有 **5 秒硬截止**。

---

## 1. 整體架構

### 1.1 兩台機器、一條區網

```
┌──────────────────────────── 筆電（Windows 11，實體螢幕 1920×1080）────────────────────────────┐
│                                                                                              │
│  Roblox 視窗（前景）                                                                          │
│     ▲ 鍵盤／滑鼠（pydirectinput / pyautogui / keyboard）        螢幕像素（mss）                │
│     │                                                              │                          │
│  ┌──┴──────────────────────── play_agent.py ─────────────────────▼──────────────────────┐   │
│  │  ScreenGrabber 執行緒（screen_watch.py）5 fps → 640 寬 → deque(15)                     │   │
│  │  Agent.cycle()：frame_diff → mp4_data_uri / jpeg_data_uri(draw_grid) → 組 prompt        │   │
│  │  stream_json()：HTTP POST /v1/chat/completions（stream=True，5 秒硬截止）               │   │
│  │  parse_json_loose → guard → verify_click / locate → execute                             │   │
│  │  記錄：資料/<遊戲>/行動日誌.jsonl、研究筆記.md、玩法摘要.md、畫面/*.jpg、agent.log       │   │
│  │  週期：reflect()（每 10 分鐘）、snapshot("定時")（每 300 秒）、stuck 偵測、YouTube 模式  │   │
│  └───────────────────────────────────┬───────────────────────────────────────────────────┘   │
│                                      │ JSON（base64 mp4 + base64 JPEG + 文字）                 │
└──────────────────────────────────────┼───────────────────────────────────────────────────────┘
                                       │ 區網 WiFi/LAN  →  http://192.168.0.126:8888/v1
                                       ▼
┌──────────────────────── DGX Spark（hostname spark-7879，GB10，~119 GiB 統一記憶體，aarch64）─────┐
│  Docker 容器 vllm-fn-tp1（映像 vllm/vllm-openai:qwen38-flash-next）                            │
│  vLLM OpenAI 相容 API，port 8888，模型名 qwen3.8-flash-next                                    │
│  Qwen3.8-Flash-Next（NVFP4 權重 99 GB，PLE 表 27 GB 卸載到 CPU 並 mmap）                       │
│  262k context、看圖／看影片、MTP 投機解碼（3 tokens）、FP8 KV cache、MAX_NUM_SEQS 4            │
│  回：SSE 串流 data: {...delta.content...}  →  最後 usage（prompt_tokens）                       │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 資料在哪台機器上流動

| 資料 | 產生在 | 流向 | 落地在 |
|---|---|---|---|
| 螢幕像素（BGRA 1920×1080） | 筆電 `mss` | 筆電記憶體（縮成 640 寬 PIL 影像） | 不落地（環形緩衝 15 張） |
| mp4 短片（5 張 @5 fps）、1024 寬格線 JPEG | 筆電 `mp4_data_uri` / `jpeg_data_uri` | base64 data URI 塞進 HTTP JSON → 區網 → DGX | DGX 只在記憶體解碼，不存 |
| Prompt 文字（system + 指示 + 歷史 + 筆記） | 筆電 `Agent.cycle` | 同一個 HTTP 請求 | — |
| 模型輸出（精簡 JSON） | DGX vLLM | SSE 串流逐行回筆電 | 筆電 `行動日誌.jsonl` |
| 鍵盤滑鼠事件 | 筆電 `execute` | Windows SendInput → Roblox 視窗 | — |
| 筆記／摘要／截圖／log | 筆電 | 本機檔案 | `資料/<遊戲>/` |

重點：**DGX 只負責推論**，不存任何畫面；所有安全守門、鍵鼠操作、記錄都在筆電端。模型端沒有為這個專案改任何東西（`2026-09-07.md` ①：「模型端不用改：Qwen3.8-Flash-Next 本來就吃 `video_url`」）。

### 1.3 設定來源

`設定.json`（由 `screen_watch.load_config()` 第 86～91 行讀入，覆蓋 `DEFAULTS` 第 51～65 行）：

| 鍵 | 值 | 用在哪 |
|---|---|---|
| `端點` | `http://192.168.0.126:8888/v1` | `Agent.__init__` 第 305 行 |
| `備援端點` | `http://192.168.0.126:8101/v1` | `play_agent.py` 未使用（只有 `roblox_ai.py` 用） |
| `模型名` | `""`（空＝自動偵測） | `_detect_model` 第 343～345 行打 `/models` 取第一個 |
| `螢幕fps` | 5 | `ScreenGrabber` 擷取頻率、mp4 fps |
| `每輪張數` | 5 | `grabber.latest(5)` |
| `畫面寬` | 640 | 擷取縮圖寬度 |
| `jpeg品質` | 60 | 只有 `screen_watch.py` 多圖送法用；`play_agent.py` 自己寫死 70／75／85 |
| `送法` | `video` | `screen_watch.py` 用；`play_agent.py` 固定用影片 |
| `截止秒` | 5.0 | `screen_watch.py` 用；`play_agent.py` 用 `--deadline` 參數（預設也是 5.0，第 904 行） |
| `看畫面max_tokens` | 80 | `screen_watch.py` 用；`play_agent.py` 用 `--max-tokens`（預設 80，第 905 行） |
| `看畫面溫度` | 0.2 | `screen_watch.py` 用；`play_agent.py` 的 `stream_json` 預設 `temperature=0.2`（第 242 行） |
| `帶入說明書` | false | `screen_watch.build_system`；`play_agent.py` 不用說明書 |

命令列參數（`main()` 第 900～908 行）：`--game`（預設 `動物醫院`）、`--hours`（16.0）、`--deadline`（5.0）、`--max-tokens`（80）、`--reflect-min`（10.0）、`--dry-run`。

---

## 2. 一輪（cycle）的完整流程

主迴圈 `Agent.run()`（第 829～880 行）不斷呼叫 `Agent.cycle()`（第 383～473 行）。下面把一輪從螢幕截圖到寫日誌逐步拆開。

### 2a. 背景擷取執行緒：`ScreenGrabber`（`screen_watch.py` 第 114～177 行）

**輸入**：實體螢幕（`sct.monitors[1]`，主螢幕，實體 1920×1080——`play_agent.py` 第 37 行先 `import pyautogui`，它會把行程設成 DPI aware，所以之後所有座標都是實體像素）。

**處理**：

| 步驟 | 程式位置 | 說明 |
|---|---|---|
| 建立 | `Agent.__init__` 第 335～336 行 | `ScreenGrabber(self.cfg["螢幕fps"], self.cfg["畫面寬"], None)` → 5 fps、640 寬、不指定視窗（全螢幕）；`daemon=True`（第 118 行） |
| 環形緩衝 | 第 122 行 | `deque(maxlen=keep)`，`keep=15` → 只留最近 15 張（3 秒），舊的自動被擠掉 |
| 定時 | `run()` 第 142、168～173 行 | `interval = 1/fps = 0.2 秒`；用**絕對時間表** `next_t += interval` 排程，每張抓完算 `sleep = next_t - time.time()`，有剩才 `time.sleep` |
| 掉幀處理 | 第 172～173 行 | 若 `sleep <= 0`（這張抓太慢、已經落後），**不追趕**，直接 `next_t = time.time()` 重新對齊；也就是「掉幀就掉，不補抓」 |
| 抓圖 | 第 155～156 行 | `sct.grab(region)` → `Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")` |
| 縮圖 | 第 157～159 行 | 若寬 > 640 就 `resize((640, 640×h/w), Image.BILINEAR)` → 1920×1080 變 **640×360** |
| 寫入 | 第 160～161 行 | `with self.lock: self.frames.append((now, img))`——存 `(時間戳, PIL.Image)` |
| 錯誤 | 第 162～163 行 | 擷取失敗只印 `[擷取錯誤]`，不中斷執行緒 |
| 實際 fps | 第 165～167 行 | 每 2 秒算一次 `actual_fps = count / 經過秒數`（只給 `screen_watch.py` 結束時印） |
| 視窗模式 | `_find_window_bbox` 第 128～139 行 | 若有 `window_title`，每 2 秒用 `pygetwindow` 重新找視窗座標；`play_agent.py` 傳 `None`，所以永遠抓全螢幕 |

**輸出**：`grabber.latest(n)`（第 175～177 行）在鎖內回傳 `list(self.frames)[-n:]`，即最近 n 張 `(timestamp, PIL.Image)`。

日誌統計：`2026-09-07.md` 11:25 節記「視覺 5 幀/秒」。

### 2b. 取最近 5 張 → 畫面差異 → 分兩種模式（`cycle()` 第 384～392 行）

**輸入**：`frames = self.grabber.latest(self.cfg["每輪張數"])`（第 386 行，5 張）；`self.last_frame`（上一輪最後一張）。

**處理**：

1. 第 384～385 行：`t0 = time.time()`；`deadline_at = t0 + self.deadline`——**硬截止的起點就是這一輪開始的瞬間**，不是送出請求的時候。
2. 第 387～389 行：不到 2 張就 `sleep(0.2)` 直接返回（剛啟動時緩衝還沒滿）。
3. 第 390～391 行：`cur = frames[-1][1]`；`diff = frame_diff(self.last_frame, cur)`。
4. `frame_diff(a, b)`（第 194～201 行）：
   - 任一為 `None` 回 0.0。
   - 兩張都 `.convert("L")`（灰階）再 `.resize((160, 90))`。
   - `tobytes()` 後逐 byte 取 `abs(x - y)`，總和除以像素數（14,400）→ **0～255 的灰階平均差**。
5. 第 392 行：`big = self.last_frame is not None and diff > BIG_CHANGE_THRESHOLD`，`BIG_CHANGE_THRESHOLD = 28.0`（第 71 行）。

**輸出**：`diff`（寫進日誌欄位「差異」）與布林 `big`，決定走哪一條路：

| 模式 | 條件 | 做什麼 |
|---|---|---|
| 「看清楚」 | `diff > 28` | 第 395～415 行：只描述畫面，**不做任何動作**（唯一例外：模型回 `r=1` 就強制 Esc） |
| 「決策」 | 其他 | 第 417～473 行：完整的想→守→做→記 |

日誌統計：7,532 筆裡「決策」4,817、「看清楚」2,602、「脫困走位」97、「強制離開重進」12、「強制重生」8（最後一種是舊版模式名，目前程式碼已不產生）。

### 2c. 影片編碼與格線單張圖

**輸入**：5 張 640×360 PIL 影像；另外抓一張全解析度。

**處理（影片）**：

- 第 393 行：`video_uri, _ = mp4_data_uri([draw_grid(im) for _, im in frames], self.cfg["螢幕fps"])`——注意**每一格影片也先疊了格線**（YouTube 模式第 736 行則沒疊）。
- `mp4_data_uri(frames, fps)`（`screen_watch.py` 第 187～209 行）：
  1. 取第一張尺寸，`w -= w % 2; h -= h % 2`（mp4 要偶數邊）→ 640×360 本來就是偶數。
  2. `tempfile.mkstemp(suffix=".mp4")` 開暫存檔。
  3. `cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))`——**mp4v 編碼、5 fps**。
  4. 每張 `np.asarray(im.convert("RGB"))[:h, :w, ::-1]`（RGB → BGR 給 OpenCV）後 `write`。
  5. `release()`、讀回 bytes、`os.remove` 暫存檔。
  6. 回傳 `"data:video/mp4;base64," + base64` 與位元組數。
- 大小：`README.md` 與 `2026-09-07.md` 記「約 45 KB」。
- 模型端成本：`2026-09-07.md` ①：「README 說 16 幀只要 376 prompt tokens」。

**處理（單張清楚圖）**：

- `grab_still(1024)`（第 367～372 行）：另開一個 `mss` 直接抓全解析度 → 縮到 **1024 寬**（1024×576），`BILINEAR`。
- `draw_grid(img)`（第 146～158 行）：複製影像，用 `ImageDraw.Draw(im, "RGBA")` 在每 0.1（`range(1, 10)`）畫**黃色半透明線** `(255, 255, 0, 70)` 寬 1，並在頂邊、左邊寫座標標籤 `0.1～0.9`（`(255, 255, 0, 200)`）。
- `jpeg_data_uri(draw_grid(still), 70)`（第 430 行；`screen_watch.py` 第 181～184 行）→ JPEG 品質 70 的 data URI。

**為什麼需要格線圖**（`2026-09-07.md` 踩坑 3）：「影片的空間解析度太低，座標會偏 0.05～0.1（一直點到隔壁的『邀請』）→ 決策時多附一張 1024 寬的格線單張圖，座標以它為準；同一目標連點沒效果時再用 `locate()` 重新定位。修完第一輪就正確點開『職業』。」格線把畫面比例座標（0～1）直接畫在圖上，模型報座標時有參考尺，不必自己估比例。

**輸出**：`video_uri`（`video_url` content-part）、`still_uri` 或 `hi_uri`（`image_url` content-part）。

### 2d. Prompt 組成（決策模式，第 418～433 行）

**輸入**：`SYSTEM_PROMPT`、`資料/<遊戲>/任務指示.txt`、`self.history`、`self.summary`、`self.notes`。

**處理**——最終送出的 `messages` 只有兩則（Qwen3.8 模板限制：system 只能一則放最前面，`DGX連線說明.md` 第 4 節第 5 條）：

1. **system**：`SYSTEM_PROMPT.format(game=self.game)`（第 73～109 行），內容分四塊：
   - 身分與輸入說明（第 73～75 行）。
   - **鐵律 5 條**（第 77～82 行）：1 絕對禁止花任何錢（Robux、真錢、遊戲幣）；2 畫面大變化先看清楚；3 不改帳號、不離開 Roblox、不聊天，唯一例外是 youtube 動作（每 15 分鐘最多一次、每次最多 4 分鐘）；4 不確定就小步嘗試；5 邊玩邊學、記筆記。
   - **遊戲通則 11 條**（第 85～96 行）：叉叉／返回／esc 關視窗、有價格就離開、上鎖＝未解鎖、任務面板、NPC 驚嘆號按 e、教學浮字、載入等待、卡牆 esc 重置、商店只看、排行榜不是玩法、同鈕點兩次沒反應就換。
   - **動作 JSON 格式**（第 98～109 行）：輸出 `{"s":看到什麼(<=15字),"r":0或1,"a":[動作...最多3個],"n":筆記(<=30字)}`；動作型別 `click / move / key / look / scroll / type / wait / youtube`，座標為畫面比例 0～1；`r=1 時 a 只能放 esc 或點取消`。
2. **user**（多段 content）：
   - `video_url`：`video_uri`。
   - `image_url`：`still_uri`（1024 寬格線圖）。
   - `text`（第 433 行）＝固定前言「（影片＝最近 1 秒動態；單張圖＝現在的清楚畫面，含黃色座標格線，報座標以它為準）」＋ `user_text`，而 `user_text`（第 427～428 行）＝
     - `instr_text`（第 420～426 行）：若 `任務指示.txt` 存在且非空 → `【來自使用者的任務指示，優先照做】\n{instr}\n\n`。這個檔案每輪重新讀，所以使用者隨時改、下一輪就生效（`2026-09-07.md`：「用來叫它退出進錯的遊戲」）。
     - `最近幾輪：\n{hist}`：`hist = "\n".join(self.history[-8:])`（第 418 行）——程式碼是**最近 8 條** history（工作日誌 11:25 寫「最近 5 輪」，兩者不一致，以程式碼為準；每條 history 一輪一條，但脫困走位、YouTube 也會各加一條）。
     - `研究筆記摘要：\n{notes}`（第 419 行）：`【玩法摘要】`（`self.summary` 全部，最多 10 條）＋ `【最近筆記】`（`self.notes[-3:]`，最近 3 條原始筆記；沒有就「（還沒有）」）。
     - 結尾：「這是最近 1 秒的畫面。決定下一步，只輸出 JSON。」
   - **卡住提醒**不是獨立段落，而是夾在 history 那一行末尾（第 470～471 行）：同一組動作連做 `repeat >= 2` 次（即第 3 次以上）時，該輪 history 附上 `⚠ 這個動作已連續做了 N 次都沒效果，請換不同的做法（換位置、換按鍵、或先觀察）`，下一輪自然被帶進 prompt。

**Token 數**：程式沒有本地估算，直接用 vLLM 回的 `usage.prompt_tokens` 寫進日誌欄位 `prompt_tokens`（第 444 行）。工作日誌 11:25 記「prompt 約 3,000 tokens」；日誌統計 4,815 筆決策的 `prompt_tokens` 最小 1,054、中位 **2,983**、最大 3,431。

**輸出**：`content` list 與 system 字串，交給 `stream_json`。

### 2e. 串流呼叫與 5 秒硬截止：`stream_json`（第 242～297 行）

**輸入**：`endpoint`、`model`、`system_text`、`content`、`max_tokens`（決策 = `args.max_tokens` = 80；看清楚 = 120）、`deadline_at`（= 該輪 `t0 + 5.0`）、`temperature`（預設 0.2）。

**處理**：

| 步驟 | 行號 | 說明 |
|---|---|---|
| 組 payload | 243～249 | OpenAI 相容格式：`messages`（system + user）、`temperature`、`max_tokens`、`"stream": True`、`"stream_options": {"include_usage": True}`（要 vLLM 在最後一個 chunk 帶 `usage`）、`"chat_template_kwargs": {"enable_thinking": False}`（**關思考**；不關的話答案會跑到 `reasoning` 欄位、`content` 空掉——`DGX連線說明.md` 第 4 節第 1～2 條） |
| 建請求 | 250～252 | `urllib.request.Request(endpoint + "/chat/completions", data=json(ensure_ascii=False).encode("utf-8"))`，只用標準函式庫 |
| 先檢查剩餘時間 | 253～255 | `remaining = deadline_at - time.time()`；若 `<= 0.2` 秒，根本不送，直接回 `("", True, None, None)` |
| 連線逾時 | 258 | `urlopen(req, timeout=remaining)`——連線與第一個回應的上限就是剩餘秒數 |
| 逐行讀 SSE | 260～291 | `while True:` 每圈重算 `remaining`；`<= 0` → `truncated = True; break` |
| **socket 逾時逐行重設** | 266～267 | `resp.fp.raw._sock.settimeout(max(remaining, 0.05))`——每讀一行前把底層 socket 的 timeout 設成「離截止還剩幾秒」，所以 `readline()` 最多只會卡到截止線 |
| 超時就截斷 | 269～271 | `readline()` 丟 `TimeoutError`／`OSError` → `truncated = True; break`，**用已收到的片段**繼續往下走 |
| 解析 chunk | 274～291 | 只看 `data:` 開頭的行；`[DONE]` 結束；`json.loads` 失敗就跳過；有 `usage` 就記；`choices[].delta.content` 累加到 `text`；第一個字到達時記 `ttft`（首字秒） |
| 收尾 | 292～296 | `finally: resp.close()`——截斷時直接關連線，vLLM 端那個請求會被取消 |

**輸出**：`("".join(text), truncated, ttft, usage)`。

實測：日誌統計 4,815 筆決策的「決策秒」中位 **3.36 秒**、最大 4.5 秒、超過 5 秒 **0** 次、「截斷」**0** 次；「首字秒」例：1.56～1.8 秒；含動作執行的「總秒」中位 5.27、最大 9.72 秒（動作本身如 `wait d=5` 會拉長）。`2026-09-07.md` 11:25 記「模型決策中位 3.4 秒、0 次超過 5 秒；判斷平均每 4.6 秒一次」。

`screen_watch.py` 的 `stream_chat`（第 227～287 行）是同一套邏輯的原型，差別只在參數從 `cfg` 讀、回傳前多 `.strip()`。

### 2f. 解析截斷的 JSON：`parse_json_loose`（第 170～191 行）

**輸入**：模型回的原始字串 `raw`（可能被 `max_tokens=80` 或截止線截斷）。

**處理**（逐層救援）：

1. 第 172～173 行：`strip()`；用 `re.sub(r"^```(?:json)?|```$", "", s, flags=re.M)` 去掉 markdown 圍欄。
2. 第 174～180 行：直接 `json.loads`。若拿到 list（模型偶爾回陣列）→ 取第一個 dict；不是 dict → 包成 `{"s": str(obj)[:30], "r": 0, "a": []}`。
3. 第 181～184 行：失敗就找第一個 `{`，沒有就回 `None`。
4. 第 185～189 行：從 `{` 起的片段依序補七種尾巴 `""`、`"}`、`"]}`、`}]}`、`"}]}`、`]}`、`}}` 試 `json.loads`，哪個先成功就用哪個——這是「在字串中間斷掉」「在動作陣列中間斷掉」「在巢狀 dict 中間斷掉」的常見情形。
5. 第 190～191 行：都失敗就用 regex 撈 `"s":"..."` 當「看到」，`r` 用 `'"r":1' in frag.replace(" ", "")` 判斷（**寧可把付費旗標判成 1**），`a` 給空陣列。

**輸出**：dict 或 `None`；呼叫端第 436 行再用 `or {"s": "(無法解析)", "r": 0, "a": []}` 兜底。

### 2g. 守門：`guard(actions, robux_flag)`（第 516～543 行）

**輸入**：模型回的動作陣列 `obj["a"]`（第 437～440 行先確認是 list）；`robux_flag = (obj.get("r") == 1)`。

**處理**（每個動作依序過關，任何一關擋下就附「原因」丟進 `blocked`）：

| 順序 | 行號 | 規則 | 細節 |
|---|---|---|---|
| 0 | 518 | 最多 3 個 | `actions[:MAX_ACTIONS_PER_CYCLE]`，`MAX_ACTIONS_PER_CYCLE = 3`（第 70 行）；非 dict 跳過 |
| 1 | 522～524 | **目標含花錢字眼** | `click` 看 `g`；`type` 看 `g + x`。`forbidden()`（第 165～167 行）把文字轉小寫後比對 `FORBIDDEN_WORDS`（第 59～64 行）：`robux, r$, premium, gamepass, game pass, 通行證, 儲值, 加值, 訂閱, subscribe, 信用卡, credit card, 禮物卡, gift card, 結帳, checkout, 購買, 買, buy, purchase, 付費, 付款, pay, price, 價格, 花費, spend, 兌換, redeem, 升級, upgrade, vip, 會員, 解鎖, unlock`——共 36 個，涵蓋 Robux、真錢與遊戲幣 |
| 2 | 525～527 | **付費視窗旗標時只准 esc／取消** | `robux_flag` 為真時，只放行 `key esc` 或 `click` 且 `g` 含 `取消／關閉／cancel／close／x` |
| 3 | 528～531 | `type` 限制 | 文字 > 20 字、含 `http`、目標含 `聊天`／`chat` 都擋 |
| 4 | 532～535 | `youtube` 頻率 | 距上次 `last_youtube` 不到 `15 * 60` 秒就擋；否則直接放行（不再過下一關） |
| 5 | 536～537 | **鍵盤白名單** | `key` 的 `k` 小寫後必須在 `ALLOWED_KEYS`（第 65～68 行）：`w a s d space e q r f shift esc tab enter backspace 1-9 0 \` up down left right i o` |
| 收尾 | 539～542 | 統計與兜底 | `self.blocked += len(blocked)`；若 `robux_flag` 且一個動作都沒剩 → 強制塞一個 `{"t":"key","k":"esc","d":0.05,"強制":"付費視窗→Esc"}` |

**輸出**：`(ok, blocked)`；`blocked` 原樣寫進日誌欄位「擋掉」。日誌統計：全程擋掉 157 個動作、模型標記付費視窗（`robux=1`）264 輪。

看清楚模式另有一條獨立防線（第 408～410 行）：模型回 `r=1` → 不經 guard 直接 `execute([esc])`。

### 2h. 點擊前看圖確認：`needs_verify` / `verify_click`（第 546～584 行）

**觸發**（`needs_verify(a)` 第 546～548 行）：`click` 的目標 `g` 為空，**或** 小寫後含任一 `CONFIRM_WORDS`（第 114～116 行）：`確認, 確定, ok, yes, 是, 接受, accept, 繼續, continue, submit, 送出, confirm, 同意, agree, 領取, claim, get, 取得, 免費, free, 開啟, open, 解鎖, unlock, 購買, 買, buy, purchase, 付費, vip, upgrade, 升級, 會員, 商店, shop, 價格, price`。（其中後半段的字其實已在 guard 被擋，留著是雙保險。）

**輸入**：動作 `a`（含比例座標 `x, y`）。

**處理**（`verify_click`）：

1. 第 553～554 行：比例座標 clamp 到 0～1 後乘 `self.screen_w / screen_h`（`pyautogui.size()`，第 325 行）→ 實體像素。
2. 第 555～558 行：另開 `mss` 抓**全解析度**畫面；`fx, fy` 換算到擷取尺寸。
3. 第 559～560 行：以點擊點為中心裁 **±320 × ±200 像素**（640×400）的局部 `crop`。
4. 第 561～564 行：整張畫面複本畫**紅框** `(fx-40, fy-25, fx+40, fy+25)` 寬 6，再縮到 1024 寬。
5. 第 565～567 行：局部圖也畫紅框（寬 4）——放大圖不縮，等於原生像素的局部放大。
6. 第 568～572 行：整張 JPEG 品質 70、局部 JPEG 品質 85，兩張 `image_url` 加 `VERIFY_PROMPT`（第 118～120 行）：「那個按鈕/物件上寫什麼？點下去會不會花任何錢——Robux、真錢、或遊戲幣…只輸出 JSON：`{"b":"按鈕上的文字","p":0或1(會花任何錢=1，不確定也算1)}`」。
7. 第 573～574 行：`stream_json(..., max_tokens=60, deadline=time.time() + 4.0, temperature=0.0)`——**另外給 4 秒**，溫度 0。
8. 第 576～577 行：`btn = obj.get("b")`；`pay = obj.get("p", 1)`——**沒回 `p` 就當 1**。
9. 第 578～579 行：局部圖存到 `畫面/HHMMSS_確認_擋.jpg` 或 `_確認_放行.jpg`。
10. 第 580～584 行：判定順序——截斷且完全解析不出 → `(False, "確認看圖逾時，保守不點")`；`pay == 1` 或 `forbidden(btn)` → `(False, "看圖確認：按鈕「…」可能花錢")`；否則 `(True, ...)`。

**輸出**：`(可以點?, 原因)`；`execute` 第 643～649 行把原因寫進 `a["確認"]`，不可點就 `self.blocked += 1` 並跳過該動作。

### 2i. 重新定位：`locate(target)`（第 586～603 行）

**觸發**（`execute` 第 638～642 行）：動作是 `click`、有 `g`、且 `self.repeat >= 1`（上一輪已做過同一組動作）。

**輸入**：目標名稱 `g`。

**處理**：抓全解析度 → 縮 1024 寬 → `draw_grid` → JPEG 品質 75 → `LOCATE_PROMPT`（第 161～162 行）「請找出『{target}』在畫面上的正中央位置。只輸出 JSON：`{"x":0.00,"y":0.00,"f":0或1(找不到=0)}`」→ `stream_json(max_tokens=40, deadline=+4.0 秒, temperature=0.0)`。

**輸出**：`(x, y)` 或 `None`（`f == 0` 或欄位缺／型別錯）。找到就覆寫 `a["x"], a["y"]` 並印 `📍 重新定位`。

### 2j. 執行：`execute(actions)`（第 626～701 行）

**輸入**：過完 guard 的動作清單。

**前置**：

- 第 627～631 行：空清單直接回；`--dry-run` 只印「（dry-run，不執行）」。
- 第 632～634 行：`self.win.activate()` 失敗 → 印「Roblox 視窗不在前景，這輪不動作」並整輪放棄。
- `RobloxWindow`（第 204～238 行）：`find()` 用 `win32gui.EnumWindows` 找可見、標題等於 `Roblox` 或以 `Roblox` 開頭且不含 `Studio` 的視窗；`is_foreground()` 比對 `GetForegroundWindow()`；`activate()` 先按放一次 `alt`（第 233 行，「ALT 小技巧讓 SetForegroundWindow 在多數情況下生效」）再 `SetForegroundWindow`，`sleep(0.25)` 後再確認。

**各動作處理**（每個動作獨立 try，失敗只印 `動作失敗`；`FailSafeException` 往上拋）：

| 動作 | 行號 | 做法 |
|---|---|---|
| `click` | 650～661 | 比例→實體像素；`moveTo(x, y)` → `sleep(0.12)` → `moveRel(1, 0)` → `sleep(0.05)` → `mouseDown(left)` → **`sleep(0.08)`** → `mouseUp(left)` → `sleep(0.15)`。註解：「Roblox 的 UI 對『瞬間按放』常常沒反應：先移過去停一下，按住 80ms 再放」 |
| `move` | 662～666 | `moveTo` 後 `sleep(0.1)` |
| `key` | 667～670 | `k` 小寫；`d = clamp(0.05, 3.0)`（`MAX_KEY_HOLD = 3.0`，第 69 行）；`keyDown → sleep(d) → keyUp` |
| `look` | 671～679 | `dx` clamp ±400、`dy` clamp **-100～400**（往上看限制小，「避免鏡頭一直被推到天花板」）；按住**右鍵**，分 8 步 `moveRel(dx//8, dy//8)` 每步 `sleep(0.02)`，再放開右鍵 |
| `scroll` | 680～681 | `pyautogui.scroll(clamp(v, ±10) * 120)`（120 = 一格滾輪） |
| `type` | 682～693 | ASCII → `pydirectinput.typewrite(interval=0.03)`；非 ASCII（中文）→ `keyboard.write(txt, delay=0.03)`（Unicode SendInput）；再失敗 → `pyperclip` 複製後 Ctrl+V |
| `wait` | 694～695 | `sleep(clamp(d, 0.1, 3.0))`——注意上限 3 秒，即使模型要求 `d=5` |
| `youtube` | 696～697 | 進 `youtube_session(q)`（見 §3.3） |

`pydirectinput.PAUSE = 0.02`（第 53 行）：每個 pydirectinput 呼叫後自動停 20 ms。

**輸出**：實際鍵鼠事件送進前景的 Roblox 視窗；`self.blocked` 可能增加。

### 2k. 記錄

| 檔案 | 寫入處 | 內容 |
|---|---|---|
| `資料/<遊戲>/行動日誌.jsonl` | `Agent.log()` 第 363～365 行，一行一個 JSON | 見下表 |
| `資料/<遊戲>/研究筆記.md` | `add_note()` 第 353～361 行 | 第一次寫時加標題 `# 「{game}」研究筆記（gamer AI 自己寫的）`；每條 `- {note}  (MM/DD HH:MM)`；`note` 空、少於 4 字或已存在（`in self.notes`）就不寫。來源：每輪 `obj["n"]`（第 446～447 行）、`reflect()`、YouTube 學習（前綴「（YouTube）」） |
| `資料/<遊戲>/玩法摘要.md` | `reflect()` 第 808～810 行，**整檔覆寫** | 標題 `# 「{game}」玩法摘要（MM/DD HH:MM 自動濃縮）` ＋ 最多 10 條 `- ...`；啟動時第 316～320 行讀回 `self.summary` |
| `資料/<遊戲>/畫面/HHMMSS_{tag}.jpg` | `snapshot(tag)` 第 374～380 行（存 grabber 最新一張 640 寬，JPEG 70）；`verify_click` 第 578～579 行（局部圖，JPEG 80） | `tag` 有：`大變化`（看清楚模式，第 404 行）、`定時`（每 300 秒，第 857～858 行）、`確認_擋`／`確認_放行`。日誌統計：目前 3,203 張 |
| `資料/<遊戲>/agent.log` | `Tee` 類別第 883～897 行 | `main()` 把 `sys.stdout / stderr` 都換成 `Tee`，終端機印的每一行同時 append 進去；啟動時寫 `===== YYYY-MM-DD HH:MM:SS 啟動 =====` |

**`行動日誌.jsonl` 欄位**（決策模式，第 441～444、464 行；看清楚模式，第 405～407 行）：

| 欄位 | 意義 | 哪些模式有 |
|---|---|---|
| `時間` | `HH:MM:SS` | 全部 |
| `模式` | `決策`／`看清楚`／`脫困走位`／`強制離開重進`／`youtube開始`／`youtube`／`youtube結束` | 全部 |
| `差異` | `frame_diff` 結果（四捨五入 1 位） | 決策、看清楚 |
| `看到` | 模型 `s` | 決策、看清楚、youtube |
| `robux` | 模型 `r`（1 = 有付費視窗） | 決策、看清楚 |
| `動作` | 實際要執行的動作陣列（含 `確認` 原因、脫困時被替換成 `{"t":"脫困走位"...}`） | 決策、看清楚、youtube |
| `擋掉` | guard 擋下的動作（含 `原因`） | 決策、youtube |
| `筆記` | 模型 `n` | 決策、youtube |
| `截斷` | `stream_json` 的 `truncated` | 決策、看清楚 |
| `首字秒` | ttft | 決策 |
| `決策秒` | 從 `t0` 到解析完模型回覆 | 決策 |
| `總秒` | 從 `t0` 到動作執行完 | 決策 |
| `秒` | 看清楚模式的整輪耗時 | 看清楚 |
| `prompt_tokens` | vLLM `usage.prompt_tokens` | 決策 |
| `圖` | 大變化截圖路徑 | 看清楚 |
| `查詢`／`新筆記`／`結束` | YouTube 模式專用 | youtube* |

`check_agent.py`（健康檢查，獨立執行）就是讀這些檔：用 PowerShell `Get-CimInstance Win32_Process` 找 `play_agent.py` 程序（第 18～25 行）；讀 `行動日誌.jsonl` 最後 60 筆（第 28～37 行）；最後一筆超過 3 分鐘沒動就警告（第 41～44 行）；最近 20 輪同一「看到」≥ 12 次判「疑似卡住」、同一動作 ≥ 8 次判「重複動作」（第 46～54 行）；統計擋掉／付費視窗／YouTube／決策超過 5 秒（第 56～60 行）；從 `agent.log` 尾 200 行撈 `[錯誤]`、`Traceback`、`[FAILSAFE]` 等（第 68～75 行）；印筆記數與最新一條（第 77～83 行）。

---

## 3. 週期性機制

### 3.1 反思 `reflect()`（第 788～811 行，每 `--reflect-min` 分鐘＝預設 10 分鐘）

觸發：`run()` 第 851～856 行，`time.time() - self.last_reflect > reflect_min * 60`。

**第一段：整理筆記**
- 輸入：`REFLECT_PROMPT`（第 135～142 行）帶 `self.notes[-15:]`（已有筆記）與 `self.history[-25:]`（最近紀錄）。
- 處理：純文字請求，system 改成「你是正在研究一款 Roblox 遊戲的玩家，只輸出 JSON。」，`max_tokens=150`、`temperature=0.1`、截止 `self.deadline` 秒。要求「最多 5 條、每條 ≤30 字、不重複、不確定的不要寫」。
- 輸出：`obj["notes"][:5]` 逐條 `add_note`。

**第二段：濃縮玩法摘要**
- 輸入：`uniq = list(dict.fromkeys(n.split("  (")[0] for n in self.notes))[-150:]`（第 800 行）——去掉時間戳、去重、取最後 150 條。
- 處理：`SUMMARY_PROMPT`（第 128～133 行）「最多 10 條、每條 ≤25 字，只留怎麼玩／目標／東西在哪／什麼不能做；卡牆／鏡頭／選單類最多留 1 條」，`max_tokens=130`、`temperature=0.1`。
- 輸出：`summ[:10]` → `self.summary` 並覆寫 `玩法摘要.md`。

為什麼要第二段（`2026-09-07.md` 11:25）：「回灌的『最近幾條筆記』幾乎全是『卡牆怎麼脫困』的重複句，真正玩法知識被擠掉」；當時筆記已 858 條、整理 50 次。目前 `研究筆記.md` 有 871 行。

### 3.2 卡住偵測與脫困（`cycle()` 第 448～462 行）

**關鍵字計數 `stuck_hits`**（第 448～449 行）：模型回的 `s` 含任一 `天花板, 卡牆, 仰視, 卡住, 動不了, 重生, 貼牆, 朝下, 地磚, 地板` → `stuck_hits += 1`；否則 `-1`（最低 0）。

**升級流程**（第 450～460 行）：

```
stuck_hits >= 3 且 距上次脫困 > 120 秒
  ├─ unstuck_count += 1
  ├─ unstuck_count >= 3 且 距上次強制離開 > 900 秒 → force_leave()；unstuck_count = 0
  └─ 否則 → force_respawn()
  之後 stuck_hits = 0、本輪模型動作清空（actions = []）
```
第 461～462 行：`stuck_hits` 歸零且距上次脫困 > 600 秒 → `unstuck_count = 0`（「十分鐘沒再卡就歸零」）。

**`force_respawn()`（第 475～497 行）**——註解：「這款遊戲的 R 重生被停用」，所以用走位代替：`s` 按住 2.0 秒（後退）→ `d` 1.0 秒（側移）→ 滾輪 `-10 * 120` 三次（鏡頭拉到最遠）→ 按住右鍵往上拖 8×25 像素再往下拖 5×20（把極端仰角拉回水平）→ `w` 1.0 秒。加一條 history 告訴模型「程式幫你做了脫困走位…R 重生在這款遊戲不能用，別再點」，日誌 `模式: 脫困走位`。日誌統計：97 次。

**`force_leave()`（第 499～513 行）**：`esc` → 等 1.0 → `l` → 等 1.2 → `enter` → 等 3.0（Roblox 選單的離開遊戲快捷）；history 加「請從 Roblox 首頁的『最近玩過／繼續』找到『動物醫院』點進去按 Play 重新進入」，日誌 `模式: 強制離開重進`。日誌統計：12 次；最後一筆（11:28:11）正是這條，接著 11:28:19 看清楚模式看到「Roblox 遊戲頁面…藍色播放按鈕」、11:28:22 點 `Play按鈕`。

**同動作重複偵測 `repeat`**（第 466～470 行）：把本輪動作只留 `t / k / g` 三個鍵序列化成 `key`，與 `last_key` 相同且非空 → `repeat += 1`，否則歸零。`repeat >= 1` 觸發 `locate` 重新定位；`repeat >= 2` 在 history 加警告。

**`same_count`**：第 332 行有初始化 `self.same_count = 0`，但目前程式碼沒有任何地方使用。工作日誌 01:40 記的「連續 6 輪畫面幾乎沒變（灰階差 <8）→ prompt 加『你已在同一畫面約 N 秒』」在現行版本找不到對應實作——何時移除：未記錄。

### 3.3 YouTube 學習模式 `youtube_session(query, max_seconds=240)`（第 721～785 行）

**進入條件**：模型回 `{"t":"youtube","q":"..."}` 且通過 guard 的 15 分鐘限制（第 532～535 行）；`execute` 第 696～697 行呼叫，`q` 空就用 `f"{game} roblox 玩法"`。

**流程**：
1. 第 723～727 行：記 `last_youtube`；日誌 `youtube開始`；`webbrowser.open("https://www.youtube.com/results?search_query=" + quote(query))`；等 5 秒。
2. 第 729～777 行迴圈，條件：未滿 240 秒、`stop` 事件未設、沒有 `停止.txt`／`stop.txt`：
   - 取 5 張包 mp4（**不疊格線**，第 736 行）＋ 1024 寬格線單張圖。
   - `LEARN_PROMPT`（第 122～126 行）＋「目前筆記」`self.notes[-6:]`；規則：不准登入、訂閱、點廣告、加入會員、買東西；「略過」廣告可以點；可做 click／scroll／wait／space／esc；輸出 `{"s","a"(最多 2 個),"n"(<=40字),"d"(結束=1)}`。
   - `stream_json(max_tokens=100, deadline=t0 + self.deadline)`。
   - `n` 非空 → `add_note("（YouTube）" + n)`，`learned += 1`。
   - 動作先篩 `t in (click, scroll, wait, key, move)` 再過 `guard(…, False)`，`key` 再限 `space / esc / down / up`（第 747～749 行）。
   - `activate_title("YouTube")`（第 704～719 行，找標題含 YouTube 的視窗切到前景）失敗就結束。
   - 執行：click 一樣 `needs_verify → verify_click`，按住 0.08 秒；wait clamp 0.5～3.0；key 用 `press`。
   - `d == 1` 結束。
3. 第 779～785 行：切回 YouTube 視窗按 `Ctrl+W` 關分頁 → `self.win.activate()` 回 Roblox → history 加「去 YouTube 看了『{query}』，新增 N 條筆記，已回到遊戲」→ 日誌 `youtube結束`。

日誌統計：到 11:29 為止 `youtube*` 模式 **0** 筆，模型尚未主動用過這個動作。

### 3.4 定時截圖

`run()` 第 857～858 行：每 300 秒 `snapshot("定時")`。

---

## 4. 安全開關

| 開關 | 實作 | 行號 |
|---|---|---|
| **FAILSAFE 左上角** | `pyautogui.FAILSAFE = True`、`pydirectinput.FAILSAFE = True`；滑鼠甩到螢幕左上角時任何 pyautogui/pydirectinput 呼叫丟 `FailSafeException`；`execute` 第 698～699 行、`run` 第 843～844 行都 `raise` 不吞，最外層第 859～860 行印 `[FAILSAFE] 滑鼠在左上角，緊急停止` | 51～52、698、843、859 |
| **stop.txt／停止.txt** | `STOP_FILE = HERE/停止.txt`；主迴圈每輪開頭檢查 `os.path.exists(STOP_FILE) or os.path.exists(HERE/stop.txt)`；YouTube 迴圈也檢查 | 56、839～840、731 |
| **Ctrl+F12** | `keyboard.add_hotkey("ctrl+f12", lambda: self.stop.set())`（需要 `keyboard` 套件，抓不到就靜默略過）；`run()` 的 `while not self.stop.is_set()` | 337～341、836 |
| **時限** | `t_end = time.time() + args.hours * 3600`，預設 16 小時；到時印 `[時間到] 結束` | 830、837～838 |
| **Ctrl+C** | `KeyboardInterrupt` → `[Ctrl+C] 結束` | 861～862 |
| **只在 Roblox 前景時動** | `execute` 先 `win.activate()`，失敗整輪不動 | 632～634 |
| **收尾放開按鍵** | `finally`：`keyUp` w a s d shift space、`mouseUp(right)`、停擷取執行緒、印總結 | 867～880 |

註：檔頭 docstring（第 17 行）與 `start_agent.bat` 寫「按 F12」，實際程式碼綁的是 **Ctrl+F12**（第 339 行、第 833 行的提示訊息也是 Ctrl+F12）。

`start_agent.bat`：`set PYTHONIOENCODING=utf-8` 後 `start "gamer-AI agent" /min cmd /k python -u play_agent.py --hours 16`。

---

## 5. 模型端：DGX Spark 上的 Qwen3.8-Flash-Next

### 5.1 硬體與部署（`DGX連線說明.md` §1、§3；`部署紀錄` 表格）

| 項目 | 值 |
|---|---|
| 機器 | NVIDIA DGX Spark，hostname `spark-7879`，GB10，~119 GiB 統一記憶體（CPU/GPU 共用），ARM aarch64，磁碟 3.7 TB |
| 區網 | `192.168.0.126`，帳號 `ethan`，`ssh -i ~/.ssh/dgx_claude` |
| 原則 | 所有模型跑 Docker，主機 Python 不裝東西 |
| 來源 | GitHub `MiaAI-Lab/Qwen3.8-Flash-Next-Single-DGX-Spark`（AGPL-3.0，commit `ef1af5f`）；權重 HF `Mia-AiLab/Qwen3.8-Flash-Next-NVFP4`（99 GB，51 個檔） |
| 容器／映像 | `vllm-fn-tp1` / `vllm/vllm-openai:qwen38-flash-next`（含 PLE／QSA 補丁的 vLLM） |
| API | `http://192.168.0.126:8888/v1`，OpenAI 相容，模型名 `qwen3.8-flash-next`，`--reasoning-parser qwen3` |
| 啟動 | `./start.sh`；第一次多建 27 GB PLE 表（49 秒），之後每次約 10～12 分鐘到 `/health`（實測 23:44 → 23:56） |

### 5.2 `.env` 關鍵值（`部署紀錄` 「.env 重點」）

| 鍵 | 值 | 意思 |
|---|---|---|
| `MAX_MODEL_LEN` | 262144 | 原生 262k context（`YARN=0`；改 1 可到 512k，但「沒人在這台測過」） |
| `MTP_NUM_SPECULATIVE_TOKENS` | 3 | MTP 投機解碼；0 可省 1.5 GiB |
| `KV_CACHE_DTYPE` | fp8 | KV 池約 1.85 倍容量 |
| `KV_TARGET_GIB` | 20 | 願望值，實際被主機保留量壓到約 16.7 GiB |
| `HOST_RESERVE_GIB` | 26 | 給主機保命的，「別調低」 |
| `MAX_NUM_SEQS` | 4 | 同時請求數 |
| `PORT` | 8888 | |

實際預算（`2026-09-06.md` 驗證）：GMU 0.782 = 93.6 GiB、容器上限 98 GiB；上線後主機 MemAvailable 22 GiB、驅動持有 89 GiB。`start.sh` 要求啟動時 MemAvailable ≥ 103 GiB，所以**其他大模型容器（寵物 8102、Minecraft 8101）必須先停**，兩者不能同時開。

### 5.3 為什麼選它（從文件推得）

1. **看圖＋看影片**：直接吃 OpenAI 的 `image_url` 與 `video_url` content-part，筆電端不用自己做視覺前處理；16 幀影片只要 376 prompt tokens（`2026-09-07.md` ①）。
2. **262k context**：每輪 ~3,000 tokens 的 prompt（system 鐵律＋通則＋歷史＋摘要）遠在上限內，之後要加更多歷史或說明書都有空間。
3. **FP8 KV cache**：KV 池 1.85 倍，讓 99 GB 權重塞進單台後還有 ~16.7 GiB 給 KV。
4. **MTP 投機解碼**：文字生成單流 README 量到 48.7 tok/s、我們中文＋emoji 約 22～24 tok/s；注意「看圖時 MTP 會退回一般解碼，變慢是正常的」（`DGX連線說明.md` §4 第 4 條）。
5. **單台 DGX Spark 就能跑**：靠 PLE 表卸載到 CPU 並 mmap，不需要多卡 TP（容器名 `tp1`）。
6. **預設會思考，但可以關**：`chat_template_kwargs.enable_thinking=false`，配合 `max_tokens 80` 才能在 5 秒內回 JSON。

### 5.4 呼叫端必須知道的坑（`DGX連線說明.md` §4）

1. 不關思考時 `max_tokens` 至少 400，不然 `content` 空（答案在 `reasoning`）。
2. 別寫死模型名，打 `/v1/models` 取（程式第 343～345 行）。
3. system 訊息只能一則、放最前面，否則 400。
4. 8888 埠可能被 `comfy-h3.service` 搶（目前該服務不存在）。
5. 多模態請求**吃不到 prefix cache**：5k tokens 的說明書放 system prompt 每輪多 1.3～2.2 秒首字，所以 prompt 要精簡（`2026-09-07.md` 解決問題 1）。

---

## 6. 演進與踩坑清單（時間順序）

以下依 `工作日誌/2026-09-06.md` 與 `2026-09-07.md` 整理，時間未標明者以「未記錄」表示。

| # | 日期時間 | 問題／需求 | 做法 |
|---|---|---|---|
| 1 | 09-06（時間未記錄） | 找不到 DGX 連線方式 | 翻鄰近專案找到 `DGX連線與使用手冊.md`；`ssh -i ~/.ssh/dgx_claude ethan@192.168.0.126` 實測成功 |
| 2 | 09-06 | 部署 Qwen3.8-Flash-Next | 照 GitHub 四步：clone、`cp .env.sample .env`（不改值）、`download.sh` 背景抓 99 GB（30～58 MB/s）、`docker pull` 映像並行 |
| 3 | 09-06 | **幽靈記憶體**：停掉寵物容器後 `free` 仍 used 30 GiB、驅動持有約 27 GiB，起小 CUDA 程式再退出也沒用；`ethan` 無 sudo 不能遠端重開 | 請使用者手動重開 DGX（23:22 回來），重開後 used 3 GiB。判斷法：`MemTotal − MemFree − Buffers − Cached − AnonPages − Slab − PageTables − KernelStack`，正常閒置約 2 GiB |
| 4 | 09-06 23:22 後 | 重開機後 `vllm-qwen3.8-27b-nvfp4`（restart policy `on-failure`）自動復活；下載與映像拉取被中斷 | 再 `docker stop` 一次；下載與 pull 重跑（可續傳） |
| 5 | 09-06 | `pgrep -f download.sh` 永遠回「還在跑」（自己的 ssh 指令列含該字串） | 改 `pgrep -f "[s]napshot_download"` |
| 6 | 09-06 | docx-js 編號清單跨段落連號；本機沒 LibreOffice；docx 模組在 npm 全域 require 不到 | 每段清單給不同 `instance`；用 Word COM 匯 PDF、PyMuPDF 轉圖檢查；`NODE_PATH=$(npm root -g)` |
| 7 | 09-06 23:44～23:56 | 啟動驗證 | `start.sh --no-launch` 建 PLE 表 49 秒；正式啟動 12 分鐘到 `/health`；sanity test 思考 261 tokens 在 `reasoning` 欄位 |
| 8 | 09-06 | Windows 終端機 cp950 印 emoji `UnicodeEncodeError` | 程式開頭 `sys.stdout/stderr.reconfigure(encoding="utf-8")`（`play_agent.py` 第 45～49 行同樣做法） |
| 9 | 09-07 | 使用者：「每秒 5 fps…思考時間嚴格在 5 秒內」 | 新寫 `screen_watch.py`：背景 5 fps、640 寬、環形緩衝、mp4 送法、串流＋硬截止 |
| 10 | 09-07 | **多模態請求吃不到 prefix cache**：整份說明書 5,160 tokens 在 system prompt，首字 1.9～2.8 秒，第二輪也沒變快 | 改濃縮重點 prompt（917 tokens）：首字 0.7 秒、一輪 1.55 秒 |
| 11 | 09-07 | 送法比較：影片 vs 5 張 JPEG | 影片比多圖快約 1 秒、token 少一半 → 預設 `video` |
| 12 | 09-07 | Windows `timeout` 殺程式後 stdout 空（管線緩衝沒 flush） | 改看 `資料/螢幕解讀.jsonl` |
| 13 | 09-07 | `mss.mss` 在 10.2 版標 deprecated | `(getattr(mss, "MSS", None) or mss.mss)()`（`screen_watch.py` 第 143 行，`play_agent.py` 第 369、555、588 行） |
| 14 | 09-07（追加） | 使用者：「開啟動物醫院…不要給他線索…絕對禁止購買 Robux…16 個小時」 | 新寫 `play_agent.py`：看→想→守→做→記，三道防線（關鍵字擋、付費旗標只准 Esc、點前看圖確認），大變化只看不動，每 10 分鐘整理筆記，`任務指示.txt` |
| 15 | 09-07 | `pydirectinput.typewrite` 打不出中文 | 非 ASCII 改 `keyboard.write`（Unicode SendInput），再失敗走剪貼簿（第 682～693 行） |
| 16 | 09-07 | Roblox UI 對「瞬間按放」的點擊常沒反應 | 移過去停 0.12 秒、按住 80 ms 再放（第 654～661 行） |
| 17 | 09-07 | **影片空間解析度太低，座標偏 0.05～0.1**，一直點到隔壁「邀請」 | 決策時多附 1024 寬格線單張圖、座標以它為準；同目標連點沒效果用 `locate()` 重新定位；修完第一輪就正確點開「職業」 |
| 18 | 09-07 | 第一次搜尋進錯遊戲（DUELS 射擊；日誌 01:06:53 看到「標題『對手』…紅色角色射擊場景」） | 用 `任務指示.txt` 叫它 Esc → 離開 → 重新搜尋，成功進 ANIMAL HOSPITAL |
| 19 | 09-07 | 進錯遊戲時寫的筆記（好友選單、大廳）會誤導後面 | 手動清掉（留有 `資料/動物醫院/_進錯遊戲的筆記_備份.md`） |
| 20 | 09-07 | bash heredoc 寫 Python 補丁時 `\n` 被展開成真換行（發生兩次） | 之後補丁一律用 Edit 工具或 `chr(10)`（這就是第 419、433、741、809～810 行到處是 `chr(10)` 的原因） |
| 21 | 09-07 | 啟動代理的指令被 Claude Code 自動模式擋下（長時間操作鍵鼠） | 使用者授權後改用 PowerShell `Start-Process`；另有 `start_agent.bat` |
| 22 | 09-07 01:26 | 驗證防線 2 | 代理點開「商店」看到「高級禮包 1980 Robux／金幣儲值」→ 標記付費視窗 → Esc／關閉，沒有購買 |
| 23 | 09-07 01:40 | 使用者：「卡在同一頁太久就跟他說按叉叉可以退出，遊戲通則也都教他」 | System prompt 加 11 條遊戲通則；加卡住偵測（連續 6 輪灰階差 <8 → 提示；此機制現行程式碼已無） |
| 24 | 09-07 01:40 | **重要誤擋**：上一輪 43 次攔截全是「購買護士職業（20 金幣）」——遊戲幣被「購買／buy」硬擋，卡住無法推進 | 硬擋只留 Robux 專屬字眼，「購買／商店／價格／VIP／升級」改走點前看圖確認；修完成功用金幣裝備「護士」 |
| 25 | 09-07 01:45 | 使用者：「金幣也不准花」 | 規則升級為「任何貨幣都不准花」：硬擋字眼加回購買／買／buy／price／解鎖／升級／兌換／VIP（即現在第 59～64 行的 36 個字）；鐵律 1、通則、`VERIFY_PROMPT` 都改成「會不會花任何錢（含金幣）」；清掉 8 條「購買職業需 20 金幣」筆記。註：改之前已用金幣裝備護士一次，無法退回 |
| 26 | 09-07 01:52 | 使用者：「真的不行他能去看 YouTube 影片學怎麼玩，但不要花掉任何資源」 | 新增 `youtube` 動作與 `youtube_session`：最多 4 分鐘、每 15 分鐘一次、只能看／滾／點結果／space、學到的筆記加「（YouTube）」前綴、結束 Ctrl+W 回 Roblox；同樣走 guard 與看圖確認 |
| 27 | 09-07 10:10 | 角色理智歸零死亡，反覆點付費「自我復活」全被擋（付費視窗 14 次） | `任務指示.txt` 指示改點免費「前往大廳」後脫離；玩法摘要也寫進「死亡畫面只點『前往大廳』」 |
| 28 | 09-07 11:25 | 使用者：「確定他都有正常記憶就好 繼續訓練」；檢查發現回灌的最近筆記幾乎全是「卡牆脫困」重複句，真正玩法被擠掉 | 新增「玩法摘要」：每 10 分鐘把去重後最多 150 條筆記濃縮成 ≤10 條存 `玩法摘要.md`，每輪回灌「摘要＋最近 3 條原始筆記」；先人工寫第一版當起點 |
| 29 | 09-07 10:49～11:19 | 速度實測（400 輪） | 視覺 5 幀/秒；判斷平均每 4.6 秒一次；決策中位 3.4 秒、0 次超過 5 秒；prompt 約 3,000 tokens |
| 30 | 未記錄 | 卡牆脫困升級：`stuck_hits` 關鍵字計數、`force_respawn` 脫困走位（97 次）、`force_leave` 強制離開重進（12 次）、`look dy` 往上看限制 -100 | 現行程式碼第 448～513、673 行有實作，但工作日誌未記錄加入時間與原因（日誌中「強制重生」8 筆是更早的版本名） |

---

## 附錄 A：一輪的時間軸（以日誌 11:28:22 那一筆為例）

```
t0 = 0.00 s   cycle() 開始，deadline_at = 5.00 s
              latest(5) → frame_diff = 11.4（< 28 → 決策模式）
              mp4_data_uri(5 張 640×360 + 格線) 、grab_still(1024) + draw_grid + JPEG 70
              讀 任務指示.txt、組 history[-8:] + 玩法摘要 + notes[-3:]
              stream_json(max_tokens=80, enable_thinking=false) → POST 192.168.0.126:8888
t = 1.80 s    首字到（首字秒 1.8）
t = 3.37 s    串流結束（決策秒 3.37，prompt_tokens 2932，截斷 false）
              parse_json_loose → {"s":"Roblox遊戲頁面，準備進入","r":0,"a":[click Play按鈕(0.45,0.28), wait 5.0]}
              guard：兩個動作都放行（擋掉 []）
              execute：activate Roblox → click（按住 80 ms）→ wait（clamp 到 3.0 s）
t = 6.86 s    總秒 6.86，寫 行動日誌.jsonl、print_rec
```

## 附錄 B：程式呼叫模型的六種情境一覽

| 情境 | 函式／行號 | 影像輸入 | max_tokens | 截止 | 溫度 |
|---|---|---|---|---|---|
| 看清楚 | `cycle` 401～402 | 影片（格線）＋ 1024 格線圖 | 120 | 該輪 5 s | 0.2 |
| 決策 | `cycle` 434～435 | 影片（格線）＋ 1024 格線圖 | 80（`--max-tokens`） | 該輪 5 s | 0.2 |
| 點前確認 | `verify_click` 573～574 | 1024 紅框全圖 ＋ 640×400 局部 | 60 | +4 s | 0.0 |
| 重新定位 | `locate` 593～596 | 1024 格線圖 | 40 | +4 s | 0.0 |
| YouTube 學習 | `youtube_session` 742～743 | 影片（無格線）＋ 1024 格線圖 | 100 | 該輪 5 s | 0.2 |
| 反思／摘要 | `reflect` 793～794、801～803 | 無（純文字） | 150 / 130 | 5 s | 0.1 |
