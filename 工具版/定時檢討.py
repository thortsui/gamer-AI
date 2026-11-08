"""跑代理的中間，到指定時間就叫他做一次大檢討（big_review.py），代理不停。
11/09 使用者「中間要自己進行檢討」。
用法：python -u 工具版/定時檢討.py --game 下樓逃殭屍 --at "2026-11-09 15:00" "2026-11-10 03:00" ...
看最新的 YYYY-MM-DD.jsonl 當 --date（run_until 中途重開時會換新檔）。stop.txt 出現就結束。
"""
import argparse, datetime as dt, glob, os, re, subprocess, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def log(game, msg):
    p = os.path.join(ROOT, "專案", game, "記憶", "工具版", "定時檢討.log")
    with open(p, "a", encoding="utf-8") as f:
        f.write(f"{dt.datetime.now():%m/%d %H:%M:%S} {msg}\n")


def latest_date(game):
    files = [f for f in glob.glob(os.path.join(ROOT, "專案", game, "記憶", "工具版", "*.jsonl"))
             if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.jsonl", os.path.basename(f))]
    return os.path.basename(max(files, key=os.path.getmtime))[:10] if files else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", required=True)
    ap.add_argument("--at", nargs="+", required=True)
    a = ap.parse_args()
    times = sorted(dt.datetime.strptime(t, "%Y-%m-%d %H:%M") for t in a.at)
    log(a.game, "排好：" + "、".join(f"{t:%m/%d %H:%M}" for t in times))
    for t in times:
        while dt.datetime.now() < t:
            if os.path.exists(os.path.join(ROOT, "stop.txt")):
                log(a.game, "看到 stop.txt，結束")
                return
            time.sleep(30)
        d = latest_date(a.game)
        log(a.game, f"開始大檢討 --date {d}")
        rc = subprocess.call([sys.executable, "-u", os.path.join(ROOT, "工具版", "big_review.py"),
                              "--game", a.game, "--date", d], cwd=ROOT)
        log(a.game, f"大檢討結束 rc {rc}")


if __name__ == "__main__":
    main()
