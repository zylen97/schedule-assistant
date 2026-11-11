#!/usr/bin/env python3
"""每分钟跑一次（launchd）：① 到点提醒 ② 每 mail_interval_min 分钟收一次信，重要的立即推送。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from common import config, db, now_str, push  # noqa: E402
from mailwatch import fetch_new  # noqa: E402

BASELINE_DAYS = 3  # 某账号第一次被监听时，只把最近 3 天的邮件记入（不推送）


def check_reminders(conn):
    rows = conn.execute(
        "SELECT a.*, i.status FROM alerts a JOIN items i ON i.id=a.item_id WHERE a.fired=0 AND a.at<=?",
        (now_str(),)).fetchall()
    for r in rows:
        # 已镜像到 Apple 提醒事项的由 iPhone 自己响，不重复推
        if not r["apple_id"] and r["status"] == "todo":
            push("⏰ 提醒", r["label"], group="提醒", level="timeSensitive")
        conn.execute("UPDATE alerts SET fired=1 WHERE id=?", (r["id"],))
    conn.commit()


def classify(cfg, addr, subject, summary):
    text = f"{subject} {summary}".lower()
    if any(k in subject for k in cfg.get("ignore_subjects", [])):
        return "ignore"
    if any(s in addr for s in cfg["important_senders"]):
        return "important"
    if any(s in addr for s in cfg["ignore_senders"]):
        return "ignore"
    if any(k.lower() in text for k in cfg["important_keywords"]):
        return "important"
    return "normal"


def check_mail(conn, force=False):
    cfg = config()
    interval = cfg.get("mail_interval_min", 5) * 60
    for acc in cfg["accounts"]:
        name = acc["address"]
        st = conn.execute("SELECT * FROM mail_state WHERE account=?", (name,)).fetchone()
        if st and not force and time.time() - (st["last_check"] or 0) < interval:
            continue
        last_uid = st["last_uid"] if st else None
        try:
            uv, uidnext, mails = fetch_new(acc, last_uid, BASELINE_DAYS)
        except Exception as e:
            print(f"[mail] {name} 失败：{type(e).__name__}: {e}")
            conn.execute("INSERT INTO mail_state(account,last_check,last_error) VALUES(?,?,?) "
                         "ON CONFLICT(account) DO UPDATE SET last_check=excluded.last_check, last_error=excluded.last_error",
                         (name, int(time.time()), str(e)[:300]))
            conn.commit()
            continue
        if st and st["uidvalidity"] and st["uidvalidity"] != uv:  # 服务器重置了 UID，重新建基线
            uv, uidnext, mails = fetch_new(acc, None, BASELINE_DAYS)
            last_uid = None
        first_run = last_uid is None
        for m in mails:
            level = classify(cfg, m["addr"], m["subject"], m["summary"])
            conn.execute(
                "INSERT OR IGNORE INTO mail_seen(account,local_id,received,sender,subject,summary,level,pushed)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (name, m["uid"], m["received"], m["name"], m["subject"], m["summary"], level, int(first_run)))
            if level == "important" and not first_run:
                push(f"📧 {acc['label']} · {m['name']}", m["subject"] or "（无主题）", group="邮件")
                conn.execute("UPDATE mail_seen SET pushed=1 WHERE account=? AND local_id=?", (name, m["uid"]))
        # 首次以服务器 UIDNEXT 定基线，避免空收件箱时下次从 UID 1 全量拉取
        new_last = max([m["uid"] for m in mails] + [last_uid or 0] + ([uidnext - 1] if first_run else []))
        conn.execute("INSERT INTO mail_state(account,uidvalidity,last_uid,last_check,last_error) VALUES(?,?,?,?,NULL) "
                     "ON CONFLICT(account) DO UPDATE SET uidvalidity=excluded.uidvalidity, last_uid=excluded.last_uid, "
                     "last_check=excluded.last_check, last_error=NULL",
                     (name, uv, new_last, int(time.time())))
        conn.commit()


def main():
    conn = db()
    check_reminders(conn)
    check_mail(conn, force="--mail-now" in sys.argv)


if __name__ == "__main__":
    main()
