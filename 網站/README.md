# 網站清單

| 網站 | 位置 | 開法 |
|---|---|---|
| 動物醫院 AI 代理實錄（研究報告） | `網站/研究網站/動物醫院AI代理實錄.html`（原稿 `資料/研究/`；線上版 https://claude.ai/code/artifact/0b6bac3a-506c-4ad0-8e7a-c3358187b696 ） | 直接雙擊 html，或開線上連結 |
| 動物醫院代理即時台（看畫面＋思考流程） | `網站/dashboard_server.py` ＋ `網站/dashboard.html` | `python 網站/dashboard_server.py` 後開 http://localhost:8300 ；同區網手機開 http://<筆電IP>:8300 |

**看它在想什麼**：http://localhost:8300/think —— 每輪一張卡片：看到 → 想做 → 實際做 → 被擋 → 反射 → 學到，可篩選「只看有動作／只看被擋／只看事件」，也可暫停更新。

即時台每 1 秒更新螢幕、每 3 秒更新思考流程（看到 → 做 → 擋 → 記）、計畫、死亡檢討、玩法摘要、筆記。
