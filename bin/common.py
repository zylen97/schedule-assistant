"""共享：路径、数据库、推送。"""
import json
import os
import re
import sqlite3
import subprocess
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DB = DATA / "schedule.db"
CONFIG = ROOT / "config.json"
TZ = ZoneInfo("Asia/Shanghai")

SCHEMA = """
CREATE TABLE IF NOT EXISTS items(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  list TEXT NOT NULL DEFAULT '其他',
  due TEXT,            -- 'YYYY-MM-DD' 或 'YYYY-MM-DD HH:MM'
  status TEXT NOT NULL DEFAULT 'todo',   -- todo | done | cancelled
  people TEXT,
  notes TEXT,
  source TEXT,         -- 例如 'mail:<account>:<localid>'
  created_at TEXT NOT NULL,
  done_at TEXT
);
CREATE TABLE IF NOT EXISTS alerts(  -- 多级提醒：一个事项可有多条
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id INTEGER NOT NULL,
  at TEXT NOT NULL,    -- 'YYYY-MM-DD HH:MM'
  label TEXT NOT NULL, -- 如【5天后截止】审张三论文
  apple_id TEXT,       -- Apple 提醒事项 id；为空则到点由 Bark 推送
  fired INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS mail_seen(
  account TEXT NOT NULL,
  local_id INTEGER NOT NULL,
  received INTEGER,
  sender TEXT,
  subject TEXT,
  summary TEXT,
  level TEXT,          -- important | normal | ignore
  pushed INTEGER NOT NULL DEFAULT 0,
  handled INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(account, local_id)
);
CREATE TABLE IF NOT EXISTS mail_state(
  account TEXT PRIMARY KEY,
  uidvalidity TEXT,
  last_uid INTEGER,
  last_check INTEGER,
  last_error TEXT
);
"""


def now():
    return datetime.now(TZ)


def now_str():
    return now().strftime("%Y-%m-%d %H:%M")


def db():
    DATA.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(items)")}
    for old in ("remind_at", "reminded", "apple_id"):  # v1 单提醒字段，已由 alerts 表取代
        if old in cols:
            conn.execute(f"ALTER TABLE items DROP COLUMN {old}")
    return conn


def config():
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def secrets_dir():
    """凭证目录（config.json 的 secrets_dir），默认 ~/.config/schedule-assistant。"""
    return Path(os.path.expanduser(config().get("secrets_dir", "~/.config/schedule-assistant")))


def read_env(name):
    """读取 secrets_dir 下 KEY=value 形式的 env/sh 文件。"""
    vals, path = {}, secrets_dir() / name
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
            if m:
                vals[m.group(1)] = m.group(2).strip().strip("\"'")
    return vals


def _bark_key():
    return os.environ.get("BARK_KEY") or read_env("bark.env").get("BARK_KEY")


def push(title, body, group="日程", level="active", url=None):
    """推到手机（Bark）；没有 Bark key 时退回 Mac 系统通知。返回渠道名。"""
    key = _bark_key()
    if key:
        payload = {"device_key": key, "title": title, "body": body,
                   "group": group, "level": level}
        if url:
            payload["url"] = url
        req = urllib.request.Request(
            "https://api.day.app/push",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json; charset=utf-8"})
        try:
            urllib.request.urlopen(req, timeout=15).read()
            return "bark"
        except Exception as e:  # 网络失败时退回本机通知
            print(f"[push] bark 失败：{e}")
    q = lambda s: json.dumps(s, ensure_ascii=False)  # AppleScript 不认 \uXXXX
    script = f'display notification {q(body)} with title {q(title)}'
    subprocess.run(["osascript", "-e", script], check=False)
    return "mac"
