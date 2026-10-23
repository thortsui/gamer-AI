# -*- coding: utf-8 -*-
"""10/23 使用者：「給他派發任務 給我網站(手機連線)列出他正在想了甚麼 內心想了甚麼 動用什麼工具」
「網頁上需要有出現 他使用的tool和他預計tool的輸出」

區網小網站（手機連同一個 Wi-Fi）：
  - 正在想：即時.json（代理每 1.5 秒寫：這一輪第幾秒、串流中的推理、正在做的工具＋預計＋回傳）
  - 最近幾輪：看到／想／內心推理／每一步工具＋他預計的結果＋程式實際回傳＋下一輪他填的符合
  - 派發任務：寫進 派發任務.json，代理面板最上面【使用者派發的任務】（只顯示，不替他做）
  - 問Claude 的問與答
用法：python 工具版/直播.py [--port 8765]   網址印在畫面上（含通行碼 k=…，沒有通行碼看不到也派不了）
"""
import argparse
import json
import os
import re
import secrets
import socket
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
MDIR = os.path.join(os.path.dirname(HERE), "專案", "動物醫院", "記憶", "工具版")
TOKEN_PATH = os.path.join(os.path.expanduser("~"), ".gamer_ai_live_token")


def token():
    try:
        t = open(TOKEN_PATH, encoding="utf-8").read().strip()
        if t:
            return t
    except OSError:
        pass
    t = secrets.token_urlsafe(9)
    open(TOKEN_PATH, "w", encoding="utf-8").write(t)
    return t


TOKEN = token()


def jload(name, default):
    try:
        return json.load(open(os.path.join(MDIR, name), encoding="utf-8"))
    except (OSError, ValueError):
        return default


def jlines(name):
    out = []
    try:
        for ln in open(os.path.join(MDIR, name), encoding="utf-8"):
            try:
                out.append(json.loads(ln))
            except ValueError:
                pass
    except OSError:
        pass
    return out


def tail_rounds(n=12):
    days = sorted(f for f in os.listdir(MDIR) if re.fullmatch(r"\d{4}-\d\d-\d\d\.jsonl", f))
    if not days:
        return []
    path = os.path.join(MDIR, days[-1])
    with open(path, "rb") as f:                     # 只讀檔尾，檔案再大也快
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - 600_000))
        lines = f.read().decode("utf-8", "replace").splitlines()[1:]
    recs = []
    for ln in lines:
        try:
            recs.append(json.loads(ln))
        except ValueError:
            pass
    recs = recs[-(n + 1):]
    out = []
    for i, r in enumerate(recs):
        nxt = recs[i + 1] if i + 1 < len(recs) else None
        match = nxt.get("符合") if nxt else None
        steps = []
        for j, st in enumerate(r.get("下三步") or []):
            m = match[j] if isinstance(match, list) and j < len(match) else None
            steps.append({"做": st.get("做"), "預計": st.get("結果應該是", ""), "回傳": str(st.get("回傳", ""))[:600], "符合": m})
        out.append({"時間": r.get("時間"), "輪": r.get("輪"), "秒": r.get("秒"), "看到": r.get("看到"), "想": r.get("想"),
                    "推理": str(r.get("推理") or "")[:5000], "先記": r.get("先記") or [], "步": steps,
                    "回傳": [str(x)[:400] for x in (r.get("回傳") or [])], "上一輪實際": r.get("上一輪實際"),
                    "任務字": [w[0] for w in (r.get("右上字") or []) if isinstance(w, list) and w]})
    return out[-n:][::-1]


def state():
    live = jload("即時.json", {})
    try:
        age = time.time() - os.path.getmtime(os.path.join(MDIR, "即時.json"))
    except OSError:
        age = 1e9
    stop = os.path.exists(os.path.join(os.path.dirname(HERE), "stop.txt"))
    small = jload("目前大任務.json", {})
    qs = jlines("問Claude.jsonl")
    ans = {a["id"]: a for a in jlines("問Claude_回答.jsonl")}
    qa = [{"id": a["id"], "問題": a.get("問題") or "（Claude 主動告訴他）", "回答": a.get("回答", ""), "時間": a.get("時間")}
          for a in sorted(ans.values(), key=lambda a: a["id"])]
    qa += [{"id": q["id"], "問題": q["問題"], "回答": "", "時間": q.get("時間")} for q in qs if q["id"] not in ans]
    sm = small.get("小任務") if isinstance(small, dict) else None
    return {"現在": datetime.now().strftime("%H:%M:%S"), "在跑": age < 20, "即時": live, "即時多久前": round(age, 1),
            "小任務": (sm or {}).get("名稱") if isinstance(sm, dict) else None,
            "派發": jload("派發任務.json", []), "問答": qa[-10:][::-1], "輪": tail_rounds(), "停": stop}


def save_tasks(tasks):
    p = os.path.join(MDIR, "派發任務.json")
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)
    with open(os.path.join(MDIR, "派發任務紀錄.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"時間": datetime.now().strftime("%m/%d %H:%M:%S"), "全部": tasks}, ensure_ascii=False) + "\n")


PAGE = r"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>代理直播</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#16181d;--mute:#69707d;--line:#e3e6eb;--acc:#2f6fed;--ok:#1a8f4c;--bad:#c93b3b;--warn:#b7791f;--code:#f0f2f5}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#171a20;--ink:#e8eaee;--mute:#99a1ad;--line:#2a2f38;--acc:#6c9bff;--ok:#4cc38a;--bad:#ff7a7a;--warn:#e0b050;--code:#1f232b}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 -apple-system,"PingFang TC","Noto Sans TC","Microsoft JhengHei",sans-serif}
header{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--line);padding:10px 16px;display:flex;gap:10px;align-items:center}
header h1{font-size:17px;margin:0;flex:1}.dot{width:10px;height:10px;border-radius:50%;background:var(--mute);display:inline-block}.dot.on{background:var(--ok)}
main{max-width:760px;margin:0 auto;padding:12px 16px 60px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px;margin:0 0 12px}
.card h2{font-size:14px;margin:0 0 8px;color:var(--mute);font-weight:600;letter-spacing:.02em}
.big{font-size:16px}.mute{color:var(--mute);font-size:13px}
.think{white-space:pre-wrap;background:var(--code);border-radius:8px;padding:8px 10px;max-height:46vh;overflow:auto;font-size:13.5px}
.step{border-left:3px solid var(--line);padding:4px 0 4px 10px;margin:8px 0}
.step.ok{border-color:var(--ok)}.step.bad{border-color:var(--bad)}.step.run{border-color:var(--acc)}
.tool{font-weight:600;word-break:break-all}.lab{display:inline-block;min-width:3.2em;color:var(--mute);font-size:12.5px}
.ret{white-space:pre-wrap;font-size:13px;word-break:break-word}
details summary{cursor:pointer;color:var(--acc);font-size:13.5px;margin-top:6px}
.tag{display:inline-block;font-size:12px;padding:1px 7px;border-radius:99px;border:1px solid var(--line);margin-right:4px}
.tag.ok{color:var(--ok);border-color:var(--ok)}.tag.bad{color:var(--bad);border-color:var(--bad)}
form{display:flex;gap:8px}input[type=text]{flex:1;min-width:0;font:inherit;padding:9px 10px;border-radius:8px;border:1px solid var(--line);background:var(--bg);color:var(--ink)}
button{font:inherit;padding:8px 12px;border-radius:8px;border:1px solid var(--acc);background:var(--acc);color:#fff}
button.ghost{background:transparent;color:var(--acc);padding:3px 9px;font-size:13px}
.task{display:flex;gap:6px;align-items:center;padding:6px 0;border-top:1px solid var(--line)}.task span{flex:1}
.done{text-decoration:line-through;color:var(--mute)}
</style></head><body>
<header><span id="dot" class="dot"></span><h1>代理直播</h1><span id="clock" class="mute"></span></header>
<main>
<section class="card"><h2>派發任務給他</h2>
<form id="f"><input id="t" type="text" placeholder="例：走到大廳正前方的配對踏板開始配對" maxlength="200"><button>派發</button></form>
<div id="tasks"></div></section>
<section class="card" id="now"><h2>正在想／正在做</h2><div id="nowbody" class="mute">讀取中…</div></section>
<section id="rounds"></section>
<section class="card"><h2>問 Claude</h2><div id="qa" class="mute">（沒有）</div></section>
</main>
<script>
const K=new URLSearchParams(location.search).get('k')||'';
const $=s=>document.querySelector(s);
function el(tag,cls,txt){const e=document.createElement(tag);if(cls)e.className=cls;if(txt!=null)e.textContent=txt;return e}
function stepEl(s,running){
  const d=el('div','step '+(s.符合===true?'ok':s.符合===false?'bad':running?'run':''));
  const t=el('div','tool',typeof s.做==='string'?s.做:(s.工具+(s.參數&&Object.keys(s.參數).length?'('+Object.values(s.參數).join(',')+')':'')));d.append(t);
  const p=el('div');p.append(el('span','lab','預計'),document.createTextNode(s.預計||'（沒寫）'));d.append(p);
  const r=el('div','ret');r.append(el('span','lab','回傳'),document.createTextNode(s.回傳||(running?'執行中…':'')));d.append(r);
  if(s.符合===true||s.符合===false){const m=el('div');m.append(el('span','tag '+(s.符合?'ok':'bad'),s.符合?'他判斷：符合':'他判斷：不符'));d.append(m)}
  return d}
let openSet=new Set();
function render(S){
  $('#dot').className='dot'+(S.在跑?' on':'');$('#clock').textContent=(S.在跑?'在跑':'停著')+' · '+S.現在;
  // 任務
  const tb=$('#tasks');tb.replaceChildren();
  (S.派發||[]).slice().reverse().forEach(t=>{const row=el('div','task');const sp=el('span',t.狀態!=='進行中'?'done':'',t.任務);
    row.append(sp,el('span','mute',t.時間+' · '+t.狀態));
    if(t.狀態==='進行中'){const b1=el('button','ghost','完成');b1.onclick=()=>upd(t.id,'完成');const b2=el('button','ghost','取消');b2.onclick=()=>upd(t.id,'取消');row.append(b1,b2)}
    tb.append(row)});
  // 即時
  const L=S.即時||{},nb=$('#nowbody');nb.replaceChildren();nb.className='';
  if(!S.在跑){nb.append(el('div','mute','代理停著（即時檔 '+Math.round(S.即時多久前)+' 秒沒更新）'))}
  else{
    nb.append(el('div','big','第 '+L.輪+' 輪 · '+(L.階段==='想'?'思考中':'執行工具')+' · '+L.已經秒+' 秒'+(L.思考強度?' · '+L.思考強度:'')));
    if(S.小任務)nb.append(el('div','mute','現在的小任務：'+S.小任務));
    if(L.階段==='想'){nb.append(el('h2',null,'內心推理（即時）'));const th=el('div','think',L.推理||'（還沒開始）');nb.append(th);th.scrollTop=th.scrollHeight;
      if(L.輸出){nb.append(el('h2',null,'輸出（即時）'));nb.append(el('div','think',L.輸出))}}
    else{nb.append(el('h2',null,'這一輪的工具'));(L.做||[]).forEach((s,i)=>nb.append(stepEl(s,i===(L.做.length-1)&&!s.回傳)))}
  }
  // 各輪
  const rs=$('#rounds');rs.replaceChildren();
  (S.輪||[]).forEach(r=>{const c=el('section','card');
    c.append(el('h2',null,'第 '+r.輪+' 輪 · '+r.時間+' · '+r.秒+' 秒'));
    if(r.任務字&&r.任務字.length)c.append(el('div','mute','右上任務字：'+r.任務字.join('、')));
    const s1=el('div');s1.append(el('span','lab','看到'),document.createTextNode(r.看到||''));c.append(s1);
    const s2=el('div','big');s2.append(el('span','lab','想'),document.createTextNode(r.想||''));c.append(s2);
    if(r.上一輪實際){const s3=el('div','mute');s3.textContent='上一輪實際：'+r.上一輪實際;c.append(s3)}
    (r.先記||[]).forEach(x=>{const p=el('div','step');p.append(el('div','tool','先記 '+x));c.append(p)});
    (r.步||[]).forEach(s=>c.append(stepEl(s,false)));
    const dt=el('details');const key='r'+r.輪;if(openSet.has(key))dt.open=true;dt.ontoggle=()=>{dt.open?openSet.add(key):openSet.delete(key)};
    dt.append(el('summary',null,'內心推理'),el('div','think',r.推理||'（沒有）'));c.append(dt);
    const dr=el('details');const k2='x'+r.輪;if(openSet.has(k2))dr.open=true;dr.ontoggle=()=>{dr.open?openSet.add(k2):openSet.delete(k2)};
    dr.append(el('summary',null,'全部工具回傳（含先記）'));(r.回傳||[]).forEach(x=>dr.append(el('div','ret',x)));c.append(dr);
    rs.append(c)});
  // 問答
  const qa=$('#qa');qa.replaceChildren();qa.className='';
  if(!(S.問答||[]).length)qa.append(el('div','mute','（他還沒問過）'));
  (S.問答||[]).forEach(q=>{const d=el('div','step');d.append(el('div','tool',q.問題),el('div','ret',q.回答||'（等 Claude 回答）'),el('div','mute',q.時間||''));qa.append(d)});
}
async function load(){try{const r=await fetch('/api/state?k='+encodeURIComponent(K));if(r.ok)render(await r.json());else $('#nowbody').textContent='通行碼不對'}catch(e){$('#dot').className='dot'}}
async function post(path,body){await fetch(path+'?k='+encodeURIComponent(K),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});load()}
function upd(id,st){post('/api/task/update',{id,狀態:st})}
$('#f').onsubmit=e=>{e.preventDefault();const v=$('#t').value.trim();if(!v)return;post('/api/task',{任務:v});$('#t').value=''};
load();setInterval(load,3000);
</script></body></html>"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _ok(self):
        k = parse_qs(urlparse(self.path).query).get("k", [""])[0]
        if not secrets.compare_digest(k, TOKEN):
            self.send_response(403)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("需要通行碼（網址後面的 ?k=…）".encode("utf-8"))
            return False
        return True

    def _send(self, body, ctype):
        b = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if not self._ok():
            return
        p = urlparse(self.path).path
        if p == "/":
            self._send(PAGE, "text/html; charset=utf-8")
        elif p == "/api/state":
            self._send(json.dumps(state(), ensure_ascii=False, default=str), "application/json; charset=utf-8")
        else:
            self.send_error(404)

    def do_POST(self):
        if not self._ok():
            return
        p = urlparse(self.path).path
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(min(n, 20000)).decode("utf-8") or "{}")
        except ValueError:
            body = {}
        tasks = jload("派發任務.json", [])
        now = datetime.now().strftime("%m/%d %H:%M")
        if p == "/api/task":
            txt = str(body.get("任務", "")).strip()[:200]
            if txt:
                tasks.append({"id": max([t["id"] for t in tasks] + [0]) + 1, "時間": now, "任務": txt, "狀態": "進行中"})
                save_tasks(tasks)
        elif p == "/api/task/update":
            for t in tasks:
                if t["id"] == body.get("id") and body.get("狀態") in ("完成", "取消"):
                    t["狀態"], t["更新"] = body["狀態"], now
            save_tasks(tasks)
        else:
            self.send_error(404)
            return
        self._send('{"ok":true}', "application/json")


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.168.0.1", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    print(f"直播網站：http://{lan_ip()}:{a.port}/?k={TOKEN}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", a.port), H).serve_forever()
