# schedule-assistant

**用自然语言管日程和多个邮箱的个人助手，跑在 Claude Code 里，提醒推到 iPhone。**

不需要滴答清单之类的待办 App，也不需要额外订阅 AI 助手。在 Claude Code 里（电脑上，或手机远程连上电脑）说一句话：

> "周四前把张三的论文审完，提前 5 天、1 天各提醒我一次"

Claude 会把它记进本地日程本，在 iPhone「提醒事项」里排好多级提醒；后台还会监听你所有邮箱，重要邮件立刻推到手机，早、午、晚各推一份简报。

## 框架

```
                    你（自然语言，电脑 / 手机远程）
                               │
                 ┌─────────────▼─────────────┐
                 │  Claude Code + CLAUDE.md   │  理解、拆解、确认、起草回复
                 └──────┬──────────────┬──────┘
                        │ 调用 CLI      │
          ┌─────────────▼───┐   ┌──────▼──────────────┐
          │ bin/sched.py     │   │ bin/mailwatch.py     │
          │ 日程本 + 多级提醒 │   │ 多邮箱 IMAP 只读收信  │
          └──────┬──────────┘   └──────┬──────────────┘
                 │      data/schedule.db (SQLite)      │
                 │  items · alerts · mail_seen · mail_state
                 │                                     │
   ┌─────────────▼───────────┐   ┌──────────────────────▼───────────┐
   │ Apple 提醒事项（iCloud） │   │ launchd 定时任务                  │
   │ 每级提醒一条，到点由      │   │ · tick.py   每分钟：补推提醒 +     │
   │ iPhone 自己响            │   │             每 5 分钟收信、分类    │
   │ （Mac 睡着也会响）        │   │ · brief.py  08:00 / 12:30 / 21:00 │
   └─────────────────────────┘   └──────────────────┬────────────────┘
                                                    │
                                          📱 Bark 推送（简报 · 重要邮件）
```

| 模块 | 做什么 |
|---|---|
| `bin/sched.py` | 日程本 CLI：增删改查、看板、多级提醒的生成与撤销 |
| `bin/mailwatch.py` | IMAP 只读收信：`SELECT readonly` + `BODY.PEEK`，不改已读状态、不删不发 |
| `bin/tick.py` | 每分钟：到点提醒兜底推送；每 5 分钟收一次信，按规则分成 重要 / 普通 / 忽略，重要的立即推 |
| `bin/brief.py` | 早报 / 午报 / 晚报：逾期、今天、3 天内的事项 + 未处理的重要邮件 |
| `bin/common.py` | 数据库、配置、凭证读取、推送（Bark，未配置时退回 macOS 通知） |
| `CLAUDE.md` | 给 Claude 的操作手册：怎么拆口语、什么时候要确认、邮件只读等规则 |
| `install.sh` | 生成并加载 launchd 任务 |

## 技术栈

- **Python 3 标准库**：`sqlite3`、`imaplib`、`email`、`urllib`，没有第三方依赖
- **SQLite**：本地单文件数据库，数据完全在自己手里
- **AppleScript（`osascript`）**：写入 Apple「提醒事项」，经 iCloud 同步到 iPhone
- **launchd**：macOS 原生定时任务
- **IMAP**：Gmail（应用专用密码）、网易 163 / 网易企业邮（授权码）等任意 IMAP 邮箱；对网易邮箱自动发送 IMAP `ID` 命令
- **Bark**：免费开源的 iOS 推送 App
- **Claude Code**：自然语言入口；配合 Seedmux 等终端可以在手机上远程使用

## 多级提醒

每个事项可以挂多条提醒，每条在 iPhone 提醒事项里单独一条，标题自带距离截止的时间：

```
○ 【5天后截止】审张三论文
○ 【明天截止】审张三论文
○ 【今天截止】审张三论文
○ 【1小时后 14:00】组会
```

- 默认策略写在 `config.json → reminder_policy`，可以按清单分别设置：

  ```json
  "reminder_policy": {
    "date_anchor": "09:00",
    "date":  ["1d", "0d"],
    "timed": ["1d", "1h"],
    "by_list": { "论文": ["5d", "1d", "0d"], "基金": ["5d", "1d", "0d"] }
  }
  ```
- 提前量写法：`5d` = 5 天、`12h` = 12 小时、`30m` = 30 分钟、`0d` = 当天。只有日期的截止以当天 `date_anchor` 为基准，比如截止 11-20 时，`1d` 就是 11-19 09:00。
- 单个事项可以覆盖默认：`--before 5d,1d,12h`、`--at "2026-11-18 10:00"`、`--no-remind`。
- 标记完成或取消时，还没响的提醒会一并撤掉；改截止日会重建提醒。

## 安装

要求：macOS、Python 3.9+、Claude Code；推送需要 iPhone 装 Bark。

```bash
git clone https://github.com/zylen97/schedule-assistant.git
cd schedule-assistant
cp config.example.json config.json          # 改成你的邮箱、规则、简报时间
mkdir -p ~/.config/schedule-assistant        # 或在 config.json 里改 secrets_dir
```

在 `secrets_dir` 下放凭证文件（文件名、变量名与 `config.json → accounts` 对应）：

```bash
# ~/.config/schedule-assistant/mail.env
GMAIL_USER="you@gmail.com"
GMAIL_APP_PASSWORD="xxxx xxxx xxxx xxxx"   # Google 账号 → 安全性 → 应用专用密码
MAIL163_USER="you@163.com"
MAIL163_AUTH_CODE="XXXXXXXXXXXXXXXX"       # 163 网页版 → 设置 → POP3/SMTP/IMAP → 授权码

# ~/.config/schedule-assistant/bark.env
BARK_KEY=你的BarkKey
```

```bash
chmod 600 ~/.config/schedule-assistant/*.env
./install.sh "$(command -v python3)"        # 装定时任务；卸载：./install.sh --uninstall
python3 bin/tick.py --mail-now              # 第一次收信，只把最近 3 天记为基线，不推送
```

第一次写入提醒事项时，macOS 会询问是否允许终端 / Python 控制「提醒事项」，点允许。

然后在项目目录里启动 Claude Code，直接说话就行。可选：建一个 `CLAUDE.local.md`（已在 `.gitignore` 里），写常用人名、解释器路径等个人信息。

## 手动用法

```bash
bin/sched.py add "审张三论文" --list 论文 --due 2026-11-20
bin/sched.py add "组会" --list 学生 --due "2026-11-12 14:00" --before 1d,1h
bin/sched.py ls                 # 看板：逾期 / 今天 / 7 天内 / 以后 / 无日期
bin/sched.py alerts             # 所有待响提醒
bin/sched.py done 3
bin/sched.py mail               # 未处理的重要邮件
bin/brief.py --dry              # 预览简报
```

## 隐私

- 日程、邮件索引、日志都在本地 `data/`、`logs/`，不上传任何服务器。邮件只读，不发信、不删信、不改已读状态。
- 凭证放在仓库外的 `secrets_dir`；`config.json`、`data/`、`logs/`、`*.env` 都在 `.gitignore` 里。
- 推送内容（简报、邮件主题）会经过 Bark 服务器；介意的话可以自建 Bark 服务端，或开启 Bark 的加密推送。

## License

MIT
