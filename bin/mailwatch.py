"""IMAP 直连收信（只读：SELECT readonly + BODY.PEEK，不改已读状态，不删不发）。"""
import email
import imaplib
import os
import re
import socket
import time
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime
from html import unescape

from common import read_env

imaplib.Commands.setdefault("ID", ("NONAUTH", "AUTH", "SELECTED"))
MAX_FULL = 2_000_000  # 超过 2MB 的邮件只取头部
socket.setdefaulttimeout(30)


def _dec(v):
    try:
        return str(make_header(decode_header(v or "")))
    except Exception:
        return v or ""


def _text(msg):
    plain, html = None, None
    for part in msg.walk() if msg.is_multipart() else [msg]:
        ct = part.get_content_type()
        if part.get_content_disposition() == "attachment":
            continue
        if ct in ("text/plain", "text/html"):
            try:
                s = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
            except Exception:
                continue
            if ct == "text/plain" and plain is None:
                plain = s
            elif ct == "text/html" and html is None:
                html = s
    s = plain or re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(style|script).*?</\1>", " ", html or ""))
    return re.sub(r"\s+", " ", unescape(s)).strip()


def fetch_new(acc, last_uid, baseline_days):
    """返回 (uidvalidity, uidnext, [dict...])。last_uid=None 表示首次：取最近 baseline_days 天。"""
    env = read_env(acc["env_file"])
    user, pwd = env.get(acc["user_var"]), env.get(acc["pass_var"])
    if not user or not pwd:
        raise RuntimeError(f"缺少凭证 {acc['env_file']}:{acc['user_var']}/{acc['pass_var']}")
    M = imaplib.IMAP4_SSL(acc["host"], 993)
    try:
        M.login(user, pwd)
        if "163" in acc["host"]:  # 网易要求客户端先报 ID，否则报 Unsafe Login
            M._simple_command("ID", '("name" "schedule-assistant" "version" "1.0")')
        typ, _ = M.select("INBOX", readonly=True)
        if typ != "OK":
            raise RuntimeError("选择收件箱失败")
        uv = M.untagged_responses.get("UIDVALIDITY", [b"0"])[0].decode()
        if "UIDNEXT" in M.untagged_responses:
            uidnext = int(M.untagged_responses["UIDNEXT"][0])
        else:  # 163 等服务器不返回 UIDNEXT：用现有最大 UID 推算
            _, alld = M.uid("SEARCH", None, "ALL")
            uidnext = max([int(u) for u in (alld[0] or b"").split()] + [0]) + 1
        if last_uid is None:
            since = time.strftime("%d-%b-%Y", time.gmtime(time.time() - baseline_days * 86400))
            typ, data = M.uid("SEARCH", None, f"SINCE {since}")
        else:
            typ, data = M.uid("SEARCH", None, f"UID {last_uid + 1}:*")
        uids = [int(u) for u in (data[0] or b"").split() if last_uid is None or int(u) > last_uid]
        out = []
        for uid in uids:
            typ, d = M.uid("FETCH", str(uid), "(RFC822.SIZE)")
            size = int(re.search(rb"RFC822\.SIZE (\d+)", d[0]).group(1)) if d and d[0] else 0
            part = "BODY.PEEK[]" if size < MAX_FULL else "BODY.PEEK[HEADER]"
            typ, d = M.uid("FETCH", str(uid), f"({part})")
            raw = next((x[1] for x in d if isinstance(x, tuple)), b"")
            msg = email.message_from_bytes(raw)
            name, addr = parseaddr(_dec(msg.get("From")))
            try:
                ts = int(parsedate_to_datetime(msg.get("Date")).timestamp() * 1000)
            except Exception:
                ts = int(time.time() * 1000)
            out.append({"uid": uid, "received": ts, "addr": addr.lower(), "name": name or addr,
                        "subject": _dec(msg.get("Subject")), "summary": _text(msg)[:500]})
        return uv, uidnext, out
    finally:
        try:
            M.logout()
        except Exception:
            pass
