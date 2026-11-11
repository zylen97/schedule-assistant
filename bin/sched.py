#!/usr/bin/env python3
"""日程本 CLI（给 Claude 调用，也可手动用）。

  sched.py add "审张三论文" --list 论文 --due 2026-11-13                 # 按清单默认策略生成多级提醒
  sched.py add "组会" --due "2026-11-12 14:00" --before 1d,1h            # 自定义：提前 1 天、1 小时
  sched.py add "交材料" --due 2026-11-20 --at "2026-11-18 10:00"         # 额外加一个绝对时间提醒
  sched.py add "想想精力分配" --no-remind                                 # 不提醒
  sched.py ls [--all] [--list 教学]
  sched.py done 3 5 / cancel 4
  sched.py edit 3 --due 2026-11-14 [--before 5d,1d,0d]                   # 改截止日会按策略重建提醒
  sched.py alerts [3]                                                     # 查看提醒时间表
  sched.py mail / mail-done 12

提前量写法：5d=5 天、12h=12 小时、30m=30 分钟、0d=当天。
只有日期的截止日，以当天 09:00 为基准（0d=当天 09:00，1d=前一天 09:00，12h=前一天 21:00）。
"""
import argparse
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
from common import config, db, now, now_str  # noqa: E402

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}( \d{2}:\d{2})?$")
OFF_RE = re.compile(r"^(\d+)([dhm])$")
APPLE_LIST = "日程助手"


def check_dt(v, name):
    if v and not DATE_RE.match(v):
        sys.exit(f"{name} 格式应为 YYYY-MM-DD 或 'YYYY-MM-DD HH:MM'：{v}")
    return v


def parse_dt(v):
    return datetime.strptime(v, "%Y-%m-%d %H:%M" if len(v) > 10 else "%Y-%m-%d")


# ---------- 多级提醒 ----------
def anchor(due):
    cfg = config().get("reminder_policy", {})
    if len(due) > 10:
        return parse_dt(due)
    h, m = map(int, cfg.get("date_anchor", "09:00").split(":"))
    return parse_dt(due).replace(hour=h, minute=m)


def offset(s):
    m = OFF_RE.match(s.strip())
    if not m:
        sys.exit(f"提前量写法不对：{s}（例：5d、12h、30m、0d）")
    n, u = int(m.group(1)), m.group(2)
    return timedelta(days=n) if u == "d" else timedelta(hours=n) if u == "h" else timedelta(minutes=n)


def stage_label(due, off):
    """提醒标题前缀，按"提醒时刻 → 截止时刻"的实际距离写，如【5天后截止】【明天 14:00】【1小时后 14:00】。"""
    days = (parse_dt(due[:10]).date() - (anchor(due) - off).date()).days
    word = "今天" if days == 0 else "明天" if days == 1 else "后天" if days == 2 else f"{days}天后"
    if len(due) <= 10:
        return f"【{word}截止】"
    if days == 0:
        mins = int(off.total_seconds() // 60)
        if mins == 0:
            return f"【现在 {due[11:]}】"
        return f"【{mins // 60}小时后 {due[11:]}】" if mins % 60 == 0 else f"【{mins}分钟后 {due[11:]}】"
    return f"【{word} {due[11:]}】"


def policy_for(lst, due):
    cfg = config().get("reminder_policy", {})
    by_list = cfg.get("by_list", {})
    if lst in by_list:
        return by_list[lst]
    return cfg.get("timed" if len(due) > 10 else "date", ["1d", "0d"])


def plan_alerts(title, due, before, extra_at):
    """返回 [(时间 'YYYY-MM-DD HH:MM', 标题)]，已过去的跳过，按时间排序去重。"""
    out = {}
    if due and before:
        for s in before:
            off = offset(s)
            at = anchor(due) - off
            out[at.strftime("%Y-%m-%d %H:%M")] = stage_label(due, off) + title
    for a in extra_at or []:
        out[a] = "【提醒】" + title
    cur = now_str()
    return sorted((k, v) for k, v in out.items() if k > cur)


# ---------- Apple 提醒事项（每级一条，只当闹钟用；Mac 睡着 iPhone 也会响） ----------
def _osa(script):
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return r.returncode == 0, (r.stdout.strip() or r.stderr.strip())


def apple_add(name, at):
    dt = parse_dt(at)
    esc = name.replace("\\", "\\\\").replace('"', '\\"')
    ok, out = _osa(f'''
tell application "Reminders"
  if not (exists list "{APPLE_LIST}") then make new list with properties {{name:"{APPLE_LIST}"}}
  set d to current date
  set day of d to 1
  set year of d to {dt.year}
  set month of d to {dt.month}
  set day of d to {dt.day}
  set time of d to {dt.hour * 3600 + dt.minute * 60}
  set r to make new reminder at end of list "{APPLE_LIST}" with properties {{name:"{esc}", remind me date:d}}
  return id of r
end tell''')
    return out if ok else None


def apple_finish(rids, delete):
    for rid in filter(None, rids):
        _osa(f'tell application "Reminders" to delete reminder id "{rid}"' if delete else
             f'tell application "Reminders" to set completed of reminder id "{rid}" to true')


def create_alerts(conn, item_id, plans):
    synced = 0
    for at, name in plans:
        rid = apple_add(name, at)
        synced += bool(rid)
        conn.execute("INSERT INTO alerts(item_id,at,label,apple_id) VALUES(?,?,?,?)", (item_id, at, name, rid))
    return synced


def drop_alerts(conn, item_id, delete=True):
    rows = conn.execute("SELECT apple_id FROM alerts WHERE item_id=? AND fired=0", (item_id,)).fetchall()
    apple_finish([r[0] for r in rows], delete)
    conn.execute("DELETE FROM alerts WHERE item_id=? AND fired=0", (item_id,))


def describe(plans, synced):
    if not plans:
        return "（不提醒）"
    times = "、".join(at[5:] for at, _ in plans)
    tail = "，已同步 iPhone 提醒事项" if synced == len(plans) else f"，iPhone 同步 {synced}/{len(plans)}，其余用推送"
    return f"（提醒 {len(plans)} 次：{times}{tail}）"


def resolve_before(a, lst, due):
    if getattr(a, "no_remind", False):
        return []
    if a.before:
        return [s for s in a.before.split(",") if s.strip()]
    return policy_for(lst, due) if due else []


# ---------- 命令 ----------
def cmd_add(a):
    conn = db()
    check_dt(a.due, "due")
    for x in a.at or []:
        check_dt(x, "at")
    cur = conn.execute(
        "INSERT INTO items(title,list,due,people,notes,source,created_at) VALUES(?,?,?,?,?,?,?)",
        (a.title, a.list, a.due, a.people, a.notes, a.source, now_str()))
    plans = plan_alerts(a.title, a.due, resolve_before(a, a.list, a.due), a.at)
    synced = create_alerts(conn, cur.lastrowid, plans)
    conn.commit()
    print(f"已添加 #{cur.lastrowid} [{a.list}] {a.title}" + (f" 截止 {a.due}" if a.due else "") + describe(plans, synced))


def cmd_edit(a):
    conn = db()
    row = conn.execute("SELECT * FROM items WHERE id=?", (a.id,)).fetchone()
    if not row:
        sys.exit(f"没有 #{a.id}")
    fields = {k: getattr(a, k) for k in ("title", "list", "due", "people", "notes") if getattr(a, k) is not None}
    check_dt(a.due, "due")
    if not fields and not (a.before or a.at or a.no_remind):
        sys.exit("没有要改的字段")
    if fields:
        conn.execute(f"UPDATE items SET {','.join(k + '=?' for k in fields)} WHERE id=?", (*fields.values(), a.id))
    msg = f"已更新 #{a.id}：{', '.join(fields) or '提醒'}"
    if {"title", "due", "list"} & fields.keys() or a.before or a.at or a.no_remind:
        title, lst, due = fields.get("title", row["title"]), fields.get("list", row["list"]), fields.get("due", row["due"])
        drop_alerts(conn, a.id)
        plans = plan_alerts(title, due, resolve_before(a, lst, due), a.at)
        msg += " " + describe(plans, create_alerts(conn, a.id, plans))
    conn.commit()
    print(msg)


def _close(ids, status):
    conn = db()
    for i in ids:
        row = conn.execute("SELECT * FROM items WHERE id=?", (i,)).fetchone()
        if not row:
            print(f"没有 #{i}")
            continue
        conn.execute("UPDATE items SET status=?, done_at=? WHERE id=?", (status, now_str(), i))
        drop_alerts(conn, i, delete=True)  # 没响的提醒都撤掉
        print(f"#{i} {row['title']} → {'已完成' if status == 'done' else '已取消'}")
    conn.commit()


def bucket(due, today):
    if not due:
        return "无日期"
    d = parse_dt(due[:10]).date()
    if d < today:
        return "逾期"
    if d == today:
        return "今天"
    if d <= today + timedelta(days=7):
        return "7 天内"
    return "以后"


ORDER = ["逾期", "今天", "7 天内", "以后", "无日期"]


def board(include_done=False, only_list=None):
    conn = db()
    q = "SELECT * FROM items WHERE status='todo'" if not include_done else "SELECT * FROM items WHERE status!='cancelled'"
    rows = conn.execute(q + " ORDER BY COALESCE(due,'9999'), id").fetchall()
    if only_list:
        rows = [r for r in rows if r["list"] == only_list]
    nxt = dict(conn.execute("SELECT item_id, MIN(at) FROM alerts WHERE fired=0 GROUP BY item_id").fetchall())
    today = now().date()
    groups = {k: [] for k in ORDER + ["已完成"]}
    for r in rows:
        groups["已完成" if r["status"] == "done" else bucket(r["due"], today)].append(r)
    out = []
    for k, rs in groups.items():
        if not rs:
            continue
        out.append(f"\n## {k}（{len(rs)}）")
        for r in rs:
            extra = " · ".join(x for x in [
                r["due"] and f"截止 {r['due']}",
                nxt.get(r["id"]) and f"⏰ 下次 {nxt[r['id']][5:]}",
                r["people"] and f"👤 {r['people']}"] if x)
            out.append(f"- #{r['id']} [{r['list']}] {r['title']}" + (f"  （{extra}）" if extra else ""))
    return "\n".join(out).strip() or "目前没有待办 🎉"


def cmd_ls(a):
    print(board(a.all, a.list))


def cmd_alerts(a):
    conn = db()
    q = "SELECT * FROM alerts WHERE fired=0" + (" AND item_id=?" if a.id else "") + " ORDER BY at"
    rows = conn.execute(q, (a.id,) if a.id else ()).fetchall()
    for r in rows:
        print(f"- {r['at']}  #{r['item_id']} {r['label']}" + ("" if r["apple_id"] else "  (推送)"))
    if not rows:
        print("没有待响的提醒")


def cmd_mail(a):
    conn = db()
    labels = {x["address"]: x["label"] for x in config()["accounts"]}
    rows = conn.execute(
        "SELECT rowid,* FROM mail_seen WHERE level='important' AND handled=0 ORDER BY received DESC LIMIT ?",
        (a.n,)).fetchall()
    if not rows:
        print("没有未处理的重要邮件")
    for r in rows:
        t = datetime.fromtimestamp(r["received"] / 1000).strftime("%m-%d %H:%M")
        print(f"- m{r['rowid']} [{labels.get(r['account'], r['account'])}] {t} {r['sender']}：{r['subject']}\n"
              f"    {(r['summary'] or '')[:120]}")


def cmd_mail_done(a):
    conn = db()
    conn.executemany("UPDATE mail_seen SET handled=1 WHERE rowid=?", [(i,) for i in a.ids])
    conn.commit()
    print(f"已标记处理：{', '.join('m' + str(i) for i in a.ids)}")


def main():
    p = argparse.ArgumentParser()
    s = p.add_subparsers(dest="cmd", required=True)

    def remind_args(x):
        x.add_argument("--before", help="提前量，逗号分隔，如 5d,1d,12h")
        x.add_argument("--at", action="append", help="额外的绝对提醒时间，可多次")
        x.add_argument("--no-remind", action="store_true")

    x = s.add_parser("add")
    x.add_argument("title")
    x.add_argument("--list", default="其他")
    for k in ("due", "people", "notes", "source"):
        x.add_argument(f"--{k}")
    remind_args(x)
    x.set_defaults(f=cmd_add)
    x = s.add_parser("edit")
    x.add_argument("id", type=int)
    for k in ("title", "list", "due", "people", "notes"):
        x.add_argument(f"--{k}")
    remind_args(x)
    x.set_defaults(f=cmd_edit)
    x = s.add_parser("done")
    x.add_argument("ids", type=int, nargs="+")
    x.set_defaults(f=lambda a: _close(a.ids, "done"))
    x = s.add_parser("cancel")
    x.add_argument("ids", type=int, nargs="+")
    x.set_defaults(f=lambda a: _close(a.ids, "cancelled"))
    x = s.add_parser("ls")
    x.add_argument("--all", action="store_true")
    x.add_argument("--list")
    x.set_defaults(f=cmd_ls)
    x = s.add_parser("alerts")
    x.add_argument("id", type=int, nargs="?")
    x.set_defaults(f=cmd_alerts)
    x = s.add_parser("mail")
    x.add_argument("-n", type=int, default=20)
    x.set_defaults(f=cmd_mail)
    x = s.add_parser("mail-done")
    x.add_argument("ids", type=int, nargs="+")
    x.set_defaults(f=cmd_mail_done)
    a = p.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
