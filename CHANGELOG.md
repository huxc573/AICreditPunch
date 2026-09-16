# 变更日志（Changelog）

遵循 [语义化版本](https://semver.org/lang/zh-CN/)（`MAJOR.MINOR.PATCH`）；版本号唯一来源 `VERSION`，标签 `v<X.Y.Z>`。
发布流程见 [README.md](README.md) §7。**口径：一个变更一行，只写「变了什么 + 为什么」**（过程性验证看 `git log`）。

---

## [v1.8.2] - 2026-09-16 20:55

### 变更

- **WorkBuddy 日志补上活动期口径**：新增一行 `本期活动；高校新生攻略 第9期 09-16~09-29（剩13天，进行中），本期连签1天，已签1天`
  （取 `activity_name` / `season` / `start_time` / `end_time` / `active` / `streak_days` / `checkin_dates`）；
  「连签」从积分明细挪进这一行 —— 它是当期活动字段，与服务端窗口同源，不会再被当成跨期连续。
  写法紧凑：数字两侧不留空格（`剩13天` / `本期连签1天，已签1天`），窗口连写为 `09-16~09-29`。
- **本次积分补上连签奖励**：`本次 +100（含连签奖励 +50）`，取 `streak_bonus_credit`（为 `0` 时不加括号）；
  此前只显示 `daily_credit`，里程碑日会少报。`today_credit` 是否已含奖励待第 3 天实测确认。
- **异常行带 `requestId`**：`状态查询异常` / `签到失败` 尾部追加报文 `requestId`，便于自查与向官方反馈。
- **可单独测试推送**：新增 `--test-notify`，只发一条测试消息、逐渠道回报结果，
  不签到也不改「当日已推送」去重记录；企业微信失败时附错误码解释（`40001` / `60020` 可信 IP /
  `81013` 可见范围 / `60011`）。为拿到原因，`_send_webhook` 与 `_send_wecom_app` 改为返回 `(bool, 原因)`。

### 修复

- **「今日已推送成功」标记不再虚写**：`orchestrate` 丢弃 `send_notify` 的返回值、无条件写
  `success_date_*`，于是渠道没配或推送失败也打印「今日已推送成功通知，本次静默跳过」——
  日志谎报，且当天配好渠道后不会再补推。改为签到成功记 `success_date_*`、**通知真正送达才记
  `notify_date_*`**；未送达时日志写「本次签到通知未送达」，下次运行（含手动）补推，
  送达则记一行「已推送签到通知」。失败告警同口径：只有送达才占用当日配额。
- **企业微信应用渠道此前从未生效**：`_send_wecom_app` 把 `http_get` 的返回值接成 `tok, _, err`，
  而它实际是 `(status, payload, err)` —— `tok` 拿到的是状态码 `200`，`isinstance(tok, dict)` 恒为假，
  于是每次调用都走「获取 token 失败」分支（日志只留一行失败），签到通知根本没发出去；
  改为 `st, tok, err`，失败原因同时带上 `HTTP=<code>`。仅配企业微信应用、未配 webhook 的用户受影响。
- 未签到时显示的「连续 N 天」是**签到前**的旧值（比实际少一天）：改为查询阶段只打活动窗口，
  连续与已签天数留到签到结算后用当次报文打印。

## [v1.8.1] - 2026-09-16 12:30

### 变更

- **等网络改为「最长约 5 分钟、通了立刻走」**：`ping -n 1 -w 1000 www.workbuddy.cn` 快探测（通后约 1 秒即继续），
  不通每 2 秒一次、最多 100 轮，再做一次 3 秒硬超时的 443 握手复核（防 ICMP 被拦）。放弃后以短超时续跑
  （`WORKBUDDY_TIMEOUT=10`、`WORKBUDDY_RETRIES=0`），避免「吞包不回」的链路把整轮拖过任务的 10 分钟执行时限
  ——被强杀时 `:publish_block` 还没执行，那次运行不会出现在日志里。替换旧实现（无超时的 `TcpClient.Connect`，实测 21s/次）。
- **控制台与日志双输出**：`checkin.py` 实时打到控制台并按 `ACP_RUN_LOG` 追加副本，bat 前置到日志顶端；
  不再用 `chcp 65001` + `type`（真实 conhost 下 `chcp` 会清屏，回显块和已打印内容一起消失，管道下看不出来）。
- **计划任务静默开关 `--auto`**：任务固定带它（vbs 幂等，缺才补），该开关下不打印、不暂停；手动运行实时打印、双击才暂停。
- **隐藏窗口**：任务动作改为 `wscript.exe "run-hidden.vbs" "checkin.bat --auto"`，去掉触发时的空 cmd 黑框。

### 修复

- 手动运行窗口只剩一行 `Checking the network ...`（回显被 `chcp` 清掉）。
- 任务被 `pause` 卡死：vbs 对已带 `--auto` 的参数又追加一次，第二个落到转发分支触发 pause（Daily 挂起 0x41301）。
- `--install` 注册失败被误报成功（改为先看注册命令退出码）。
- `-Resume` 注册失败：XML 注释写了占位符全名，替换把 `--` 注入注释（`--` 在 XML 注释里非法 → `malformed (8,60)`）。

### 文档

- README 930→329 行、CHANGELOG 545→67 行、项目记忆 67→40 行（原文归档 `archive/`）；
  代码删死函数 `_webhook_mask`、20 处超长 docstring 收敛为首行摘要。

## [v1.8.0] - 2026-09-15 09:55

Trae 判定误判修复 + 两处积分口径修正 + 唤醒补签任务。

- **修复 Trae「今日已签到」误判**（严重，签到请求从未发出）：status 未签到时同样返回 `code=0/message=success`，
  旧判定按业务码兜底 → 每次都判成已签到、`提交签到` 从未执行。改为只认 `checked_in`（`_trae_status_checked()`），
  领奖成功另看 claim 业务码（`_trae_claim_ok()`），领奖后按 `(0, 4)` 秒回查确认再写成功。
- **修复 Trae「本次 +N」虚报**：声明的 `credits + extra_credits` 多报 50（权益包只有一笔 `credits_limit=150`），
  改为签到前后余额差**实测**，实测不到才回退 `credits`。
- **修复 WorkBuddy 余额口径**：签到报文的 `total_credits` 是「活动期内累计获得」（实测 200 vs 真余额两千多），
  改用资源包接口；`--status-only` 查询失败不再谎报「待签到」。
- **新增真实余额与构成**：`get-user-resource-summary` / `-free-packages` / `-paid-packages`（**不带 `/v2`**）；
  总剩余 = 各包 `CycleRemainCapacity` 之和，平台奖励 = `SubProductCode` 含 `bonus_pack` 的包，套餐基础 = 相减。
- **新增 `AICreditPunch-Resume`**：订阅 `Kernel-Power` ID 107（+ `Power-Troubleshooter` 1 兜底），延迟 15s；
  配套 `scheduled-task.resume.xml`——事件触发器只能 XML 注册，且**不能带 `<?xml?>` 声明**。

## [v1.7.0] - 2026-09-14 23:07

WorkBuddy 段（约 400 行）按接口行为**独立重写**，公开仓库自本版本起（MIT）；日志文案、退出码、`config.json` 结构未变。

- 重构：`WorkBuddyClient` + `WbReply`（HTTP 码与报文收敛为 `accepted` / `already_checked` / `credits` / `reason`）；
  `WbAuthSnapshot` + `WbAccountStore` 取代一整套 `_wb_*` 函数；文案与报文解读上移公共区。
- 修复：`api_base` 非法只跳过该账号、不再中断整轮；重新初始化不再清空手填的 `enterprise_id`。
- 许可：新增 `LICENSE`（MIT · huxc573）与 `THIRD-PARTY-NOTICES.md`，接回公开远程。

## v1.0 – v1.6（2026-09-14 本地迭代）

- `v1.6.0` 子命令统一 `--` 前缀 + 参数转发；只读命令 `--tasks` / `--today`；注册失败自动弹日志。
- `v1.5.0` 卸载能力；任务注册改造；`--logs`；根除日志乱码（`%date%` / `%time%` 全部移除）。
- `v1.4.0` 通知编排与失败限流（同日只推一次 / 失败每天 ≤3 条、间隔 ≥60 分钟）。
- `v1.3.0` 初始化命令统一为 `--init-workbuddy` / `--init-trae` / `--init`。
- `v1.2.0` 吸收上游 `--setup`；`v1.1.0` 合并 Trae 签到为单文件 `checkin.py`；`v1.0` 首个可用版本。

> 完整细节在本地分支 `archive/full-history`（40 个提交，不推送）。
