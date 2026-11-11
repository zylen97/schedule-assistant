#!/usr/bin/env python3
"""早/中/晚简报：日程 + 未处理的重要邮件 → 推到手机，并存一份到 data/briefs/。

  brief.py            # 按当前时间自动判断早/中/晚
  brief.py --dry      # 只打印不推送
"""
import os
import sys
import time
from datetime import timedelta

sys.path.insert(0, os.path.dirname(__file__))
from common import DATA, config, db, now, push  # noqa: E402
from sched import bucket  # noqa: E402


def build():
    conn = db()
    cfg = config()
    t = now()
    today = t.date()
    slot = "早报 ☀️" if t.hour < 11 else ("午报 🍱" if t.hour < 17 else "晚报 🌙")
    items = conn.execute("SELECT * FROM items WHERE status='todo' ORDER BY COALESCE(due,'9999'), id").fetchall()
    groups = {"逾期": [], "今天": [], "7 天内": []}
    for r in items:
        b = bucket(r["due"], today)
        if b in groups:
            groups[b].append(r)
    since = (int(time.time()) - 86400) * 1000
    mails = conn.execute(
        "SELECT * FROM mail_seen WHERE level='important' AND handled=0 AND received>=? ORDER BY received DESC",
        (since,)).fetchall()
    done_today = conn.execute(
        "SELECT COUNT(*) FROM items WHERE status='done' AND done_at LIKE ?", (today.isoformat() + "%",)).fetchone()[0]

    lines = []
    if groups["逾期"]:
        lines.append(f"⚠️ 逾期 {len(groups['逾期'])}：" + "；".join(r["title"] for r in groups["逾期"][:3]))
    if groups["今天"]:
        lines.append(f"📌 今天 {len(groups['今天'])}：" + "；".join(
            r["title"] + (f" {r['due'][11:]}" if len(r["due"] or "") > 10 else "") for r in groups["今天"][:5]))
    if t.hour < 11 and groups["7 天内"]:
        soon = [r for r in groups["7 天内"] if r["due"][:10] <= (today + timedelta(days=3)).isoformat()]
        if soon:
            lines.append("🔜 3 天内：" + "；".join(f"{r['title']}({r['due'][5:10]})" for r in soon[:4]))
    if mails:
        labels = {a["address"]: a["label"] for a in cfg["accounts"]}
        lines.append(f"📧 待处理邮件 {len(mails)}：" + "；".join(
            f"[{labels.get(m['account'], m['account'])}] {(m['subject'] or '（无主题）')[:24]}" for m in mails[:3]))
    if t.hour >= 17:
        lines.append(f"✅ 今天完成 {done_today} 件")
    if not lines:
        lines.append("今天没有待办，也没有要紧邮件 🎉")
    title = f"{slot} {t.strftime('%m/%d')}"
    return title, "\n".join(lines)


def main():
    title, body = build()
    if "--dry" in sys.argv:
        print(title + "\n" + body)
        return
    ch = push(title, body, group="简报")
    out = DATA / "briefs" / f"{now().strftime('%Y-%m-%d_%H%M')}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(f"# {title}\n\n{body}\n\n（推送渠道：{ch}）\n", encoding="utf-8")
    print(f"已推送（{ch}）：{title}")


if __name__ == "__main__":
    main()
