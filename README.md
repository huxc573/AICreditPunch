# AICreditPunch · 本地自动签到

> **v1.9**（`VERSION` = `1.9.1` · 2026-09-20 13:05）· 变更见 [CHANGELOG.md](CHANGELOG.md) ·
> 远程 [`huxc573/AICreditPunch`](https://github.com/huxc573/AICreditPunch)（公开，MIT）
>
> WorkBuddy + Trae 一体化签到 / 状态 / 积分查询，多账号，**单文件零依赖**（Python ≥ 3.8，无需 pip install）。

| 模块 | 来源（仅溯源） | 上游许可 | 本仓库状态 |
|---|---|---|---|
| WorkBuddy | [`tianxing226/AICreditPunch`](https://github.com/tianxing226/AICreditPunch) · `workbuddy_checkin.py` v2.2.0 | ⚠️ 未附许可证 | ✅ **已独立重写**（`v1.7.0` 起），只保留公开接口事实 |
| Trae | [`xz0609/trae-work-checkin-ql`](https://github.com/xz0609/trae-work-checkin-ql) | ✅ MIT · `Copyright (c) 2026 Yiran` | 上游 MIT 代码的衍生版，保留其声明 |
| Trae 登录（参考） | [`Maquer/trae-signin`](https://github.com/Maquer/trae-signin) | ✅ MIT · `Copyright (c) 2026` | 仅参考 OAuth 流程 |

自有部分：编排与幂等、通知与失败限流、计划任务封装与自管理、`--today` / `--tasks`、`checkin.bat` 入口。
**动手开源前先看 [§8](#8-来源声明与致谢)**。本文档内路径 / 账号 / 设备号均为占位示例。

## 1. 文件清单

| 文件 | 用途 | 敏感 |
|---|---|---|
| `checkin.py` | 唯一脚本：签到 + 初始化 + 通知 | 否 |
| `checkin.bat` | 入口；子命令 `--install` / `--uninstall` / `--tasks` / `--today` / `--logs` / `--help`，其它 `--xxx` 转发给 `checkin.py` | 否 |
| `run-hidden.vbs` | 任务动作的包装器：`wscript.exe` 以隐藏窗口启动 cmd，触发时不弹黑框 | 否 |
| `scheduled-task.resume.xml` | `-Resume` 任务的 XML 模板（`Kernel-Power` ID 107） | 否 |
| `config.json` | 账号凭据 + 推送配置（`config.json.example` 是脱敏模板） | 🔴 已加锁 + gitignore |
| `%APPDATA%\AICreditPunch.log` | 运行日志（仓库外，最新在前） | 🟡 含账号名 |

- 上游三个文件（`workbuddy_checkin.py` / `auto_checkin.py` / `notify.py`）已删除，功能全部进 `checkin.py`。
- **迁移设备只需带 `checkin.py` + `config.json`**（或在新设备重跑 `--init-*`）。
- 可执行文件保持扁平：任务里存 `checkin.bat` 绝对路径，但 bat 用 `%~dp0` 定位自身，
  **目录改名 / 搬移会自动纠正**（见 §3.1）。

## 2. 快速开始

### 2.1 环境要求

**没有 `requirements.txt`，也不需要**：只用标准库，Python ≥ 3.8。
Windows 上 `python` 不在 PATH 时，改 `checkin.bat` 里的
`set "PYEXE=D:\Dev\Python\Env\Python312\python.exe"`；**计划任务用系统 Python**
（WorkBuddy 自带托管 Python 会随其升级被替换）。

### 2.2 初始化 WorkBuddy（`--init-workbuddy`）

在**已登录 WorkBuddy 桌面端**的机器上 `python checkin.py --init-workbuddy`。
脚本**有界、不递归**扫描 `%LOCALAPPDATA%` / `%APPDATA%` 下的
`{CodeBuddyExtension,WorkBuddy}\Data\Public\auth\`（macOS 见 `~/Library/Application Support/…`），
找到 `workbuddy-desktop*.info` 后按 uid（无 uid 时按 token）去重合并；找不到**不报错**。

```bash
python checkin.py --init-workbuddy --auth-file C:\path\to\workbuddy-desktop.info  # 也可指定文件
# 或 set "WORKBUDDY_AUTH_FILE=<路径>"（多个用 ; 分隔）后重跑
```

刷新 token **只替换 `accounts`**，`notify` / `trae_accounts` / `_` 注释字段保留。
日志出现 `401` / `凭证失效或权限不足` 就重跑本命令。

### 2.3 初始化 Trae（`--init-trae`）

Trae 凭证无法从本地文件导入，走浏览器 OAuth：`python checkin.py --init-trae`

1. 自动打开 `https://www.trae.cn/authorization?...`（含 `client_id`、`login_version=1`、`x_*` 指纹等）。
2. 页面完成登录。
3. 浏览器跳回 `http://127.0.0.1:<端口>/authorize?refreshToken=…&userInfo=…&userJwt=…`
   （端口在 **18080–18099** 间自动挑，终端会打印实际地址）；脚本用 `refreshToken` 调
   `ExchangeToken` 换 `accessToken` 写入 `trae_accounts`。浏览器没自动开就复制终端链接，回调自动接收。

> ⚠️ **设备指纹**：Trae 签到校验设备，**同一账号每天只能在"做过 `--init-trae` 的那台设备"上领取**；
> 换机器 / 云服务器报 `9074`（平台限制，非脚本 bug）。`1001` 认证失败会自动刷新 token 重试，
> `9095` 该设备今日已签过别的账号。

### 2.4 运行

```bash
python checkin.py                  # 跑所有已启用平台（推荐入口，含通知去重）
python checkin.py --workbuddy-only | --trae-only     # 单平台
python checkin.py --status-only    # 只查状态不领取     python checkin.py --dry-run   # 只校验配置
python checkin.py --debug          # 打印脱敏响应       python checkin.py --today     # 日常查看（纯本地）
python checkin.py --tasks          # 检查三个计划任务（只读）
python checkin.py --test-notify    # 只发一条测试通知，验证推送配置（不签到）
python checkin.py --init-workbuddy | --init-trae | --init
```

- 初始化只有这三种写法；旧的位置参数（`setup-workbuddy` / `setup-trae` / `login`）**已移除**（退出码 `2`）。
- **幂等**：先查今日状态，已签到只打印状态与余额，**不重复领取**，标注 `不重签`。

### 2.5 输出与日志

```text
[26.09.17 09:33:06] 一体化每日签到脚本 v1.9.1 启动
[09:33:06] ===== WorkBuddy =====
[09:33:07] WorkBuddy 本期活动；高校新生攻略 第9期 09-16~09-29（剩12天，进行中）
[09:33:08] [示例账号] 不重签；连签1/已签1；本次 +100，余额2,242.18（套餐142.18+奖励2,100）
[09:33:08] ===== Trae =====
[09:33:08] [示例账号] 不重签；本次 +150，余额484
[09:33:08] 今日通知已推送，跳过
[09:33:08] 合计：2 个账号全部成功，本次 +250
[09:33:08] 本次脚本执行完毕。
```

- 时间戳：**本轮第一行**（运行头，`[26.09.17 09:33:06]`）带完整日期，之后的行只留 `[09:33:08]` ——
  一次运行都在同一天，往上找一行就是日期。`checkin.bat` 自己写的那几行（网络告警等）仍是完整格式。
  **首行 / 末行**是运行起止标记（所有退出路径都有）；中间按 `===== 平台 =====` 分块，块内每行
  `[账号名] 状态；明细`，与账号无关的平台级提示才写成 `平台 状态；明细`。
- **账号并行**：一轮里同平台的账号同时跑（默认 4 路，`AICREDIT_CONCURRENCY` 调 1–8，`1` = 串行），
  但输出仍按账号顺序落盘（各线程先攒行、回主线程按序打）—— 同一块里几行的时间戳看着一样是正常的，
  它们本来就是同时出的结果。
- **只打结果行**：`查询签到状态` / `提交签到` / `签到已受理，回查确认` 这类进度行不落盘 —— 每行都有时间戳，
  卡在哪一步看时间差即可，出事由 `状态查询异常` / `签到失败` 行的 `HTTP/code/reason/requestId` 定位。
- 状态词只有四个：`不重签`（查询时今天已签，本轮不重复领取）/ `签到成功` / `签到失败` / `状态查询异常`。
  首次签到不再单打一行「未签到」—— 结算行（`签到成功；连签…；本次 +100`）已经说明；只有 `--status-only`
  没有结算行，才打 `今日未签到；本次 +100`。
- 通知只有一条（两平台合并，§4），所以它的去向也只在**收尾处**报一句：发出去了是 `已推送签到通知`，
  今天早已推过是 `今日通知已推送，跳过` —— 平台块里不再有逐平台的推送行。
- 收尾一句**合计**（`--status-only` 不打，它不算签到）：`合计：2 个账号全部成功，本次 +250`；
  有失败写 `合计：3 个账号，2 成功 1 失败，本次 +350`。账号数与成功数来自本轮实际结果，
  `本次` 是各账号声明值之和（口径同账号行，§2.8）。日志与通知正文用**同一句**，都落在末尾。
- `本期活动` 是**平台级**一行（`WorkBuddy 本期活动；…`）：同平台各账号同一期活动，整轮只打一次，
  位置在该平台第一行（每个账号查完状态就打，`_ACTIVITY_SHOWN` 按进程去重）；连签与已签天数属于账号，
  接在各自的状态行里（`连签1/已签1`）。
- `余额N` 是**真实可用积分**，括号里是它的构成（§2.8）；推送文案 `[平台] 账号名 状态；明细`，不带构成。
- `本期活动` 里的名/期号/窗口是**当期口径**（换期归零，§2.8）；「已签」是本期内累计、「连续」要求连着签，
  漏签后两者会分叉。

**日志**：`%APPDATA%\AICreditPunch.log`（根目录，不建子文件夹），**最新在前**（bat 做前置拼接）。

| 行为 | 说明 |
|---|---|
| 手动打开 | `checkin.bat --logs`（文件不存在时提示并返回 `1`） |
| 首次签到弹窗 | 当天首次成功签到后自动用记事本打开日志，同日后续不再弹 |
| 注册失败弹窗 | 任务注册失败同样打开日志，第一条就是 `WARN` |
| 回退路径 | `%APPDATA%` 不可用时依次回退 `$XDG_CONFIG_HOME` → 用户主目录 → 脚本同目录 |

> 弹窗都在**本次输出已置顶之后**（`checkin.py` 只留标记文件，bat 完成拼接再打开）。
> 直接 `python checkin.py` **不写日志**（只有 stdout），落盘由 `checkin.bat` 负责。

### 2.6 未初始化

会打印指引、退出码 `1`，且**不发任何网络请求**：
`配置错误：未找到配置文件…` + `--init-workbuddy` / `--init-trae` / `--init` 三条命令提示；
只缺单平台时提示对应那一条。

### 2.7 日常查看（`--today` / `--tasks`，只读）

```text
===== 日常查看 =====
日期：2026-09-14（周一）
[WorkBuddy] 今日已签到；1 个账号；成功记录 2026-09-14
[运行记录] 日志中今日 3 次；含计划任务触发与手动运行
[计划任务] 已注册；下次自动运行 …；上次 …；唤醒触发已启用；上次 从未运行
最近一次运行（日志顶部）：…最多 40 行…

===== 计划任务检查 =====
[AICreditPunch-Daily] 正常；每日 6 次（08:45…23:45）；上次结果 成功（0x0）
汇总：3/3 个计划任务正常，均指向 <当前目录>\checkin.bat
```

- `今日已签到 / 尚未签到` 读 `.checkin_state.json`，**纯本地判断**；`--today` **不查实时余额**（要看余额用 `--status-only`）。
- 退出码：`0` = 今日都已签到；`1` = 还有未签到 / 未初始化 / 任务缺失。
- 「上次结果」常见值：`成功（0x0）`、`正在运行（0x41301）`、`从未运行（0x41303）`；任务缺失或动作指向别处会标 `缺失` / `异常` 并给出修复命令。
- 实现走 PowerShell `Get-ScheduledTask` 并把字段名换成英文再解析，避开 `schtasks /fo LIST /v`「字段名随语言变化」的坑。

### 2.8 积分口径

| 文案 | 含义 | 来源 |
|---|---|---|
| `本次 +N` | 本次签到拿到 | WorkBuddy：`daily_credit`；Trae：签到前后余额差**实测** |
| `本次 +N（含连签奖励 +M）` | M 只在本期连签达标当天有值 | WorkBuddy：`streak_bonus_credit`（为 `0` 时不显示括号） |
| `本期活动；…` | **平台级**一行：活动名/期号/窗口 | WorkBuddy：`activity_name` / `season` / `start_time` / `end_time` / `active` |
| `连签N/已签M` | **账号级**的当期连签与已签天数 | WorkBuddy：`streak_days` / `checkin_dates` |
| `余额N` | 账号真实可用积分 | WorkBuddy：`get-user-resource-summary`；Trae：`total_amount - consumed_amount` |
| `余额N（套餐A+奖励B+购买C）` | 余额构成，括号挂在结算行余额后面（为 0 的分项省略） | WorkBuddy 的三条资源包接口 |

- WorkBuddy：三条路由 **不带 `/v2`**（签到的两条带 `/v2`，别拼错）。总剩余 = 各包 `CycleRemainCapacity` 之和；
  平台奖励 = 赠送包（`SubProductCode` 含 `bonus_pack`）之和；购买积分 = 付费包之和；套餐基础 = 相减。
- ⚠️ 签到报文的 `total_credits` 是「活动期内累计获得」，**不是余额**（`v1.8.0` 起不再这样显示）。
- ⚠️ `streak_days` / `checkin_dates` / `week_checkin_days` 都是**当期活动口径**，换期（如第 8 期 → 第 9 期）
  归零；跨期连续天数服务端不提供。所以「连签1天」不等于「只签了 1 天」。
- 连签与已签天数只在**签到结算后**用当次报文打印（`--status-only` 只打平台级活动窗口、不打天数）——
  那一刻的 `streak_days` 还是签到前的旧值，会少一天。
- 异常行（`状态查询异常` / `签到失败`）尾部会带报文 `requestId`，便于自查与向官方反馈。
- Trae：声明的 `credits + extra_credits` 会虚报（实测声明 200、只到账 150），故用余额差实测，回退只取 `credits`。
- 余额 / 构成查询失败只少打一行，不影响签到结果，也**不会**把「查不到」写成 `0`。

## 3. 定时任务（Windows）

### 3.1 安装 / 卸载 / 管理

```cmd
checkin.bat               :: 跑签到；任务缺失或路径过期时自动注册
checkin.bat --install     :: 强制重装三个任务         checkin.bat --uninstall   :: 卸载三个任务
checkin.bat --logs / --tasks / --today / --help
schtasks /run /tn "AICreditPunch-Daily"    :: 立即触发测试（-Resume 也可按需试跑）
```

- 子命令一律 `--` 开头，**只有帮助类例外**（`--help`、`-h`、`/?`、裸 `help`）；其它 `--xxx` **原样转发**给
  `checkin.py`（只读交互，不写日志）。
- 每次运行都检查任务是否**存在**且**指向当前目录**：缺失 / 路径过期 → 自动重注册并写日志；
  注册失败不影响签到，只留 `WARN` 并在运行结束后打开日志。**换电脑或改名后跑一次 bat 即可接上定时。**
- 注册走 PowerShell `Register-ScheduledTask`（**无需管理员**）以带上 `StartWhenAvailable`；
  `-Resume` 的事件触发器 cmdlet 表达不了，故读 `scheduled-task.resume.xml` 走 `-Xml` 注册
  ——该模板**不能出现 `<?xml?>` 声明**，否则报 `malformed (1,40) 无法切换编码`。

### 3.2 任务清单

| 任务 | 触发 | 说明 |
|---|---|---|
| `AICreditPunch-Daily` | 每日 08:45 / 11:45 / 14:45 / 17:45 / 20:45 / 23:45 | 主任务，`StartWhenAvailable`（错过补跑） |
| `AICreditPunch-Startup` | 用户登录时 | 登录补跑 |
| `AICreditPunch-Resume` | 从睡眠 / 休眠恢复（`Kernel-Power` ID 107，延迟 15s） | 唤醒补签 |

动作均为 `wscript.exe "<目录>\run-hidden.vbs" "<目录>\checkin.bat --auto"`，当前用户 + 交互式令牌
（不存密码），`ExecutionTimeLimit = 10 分钟`，`MultipleInstancesPolicy = IgnoreNew`。

- **为什么有 vbs**：交互式令牌下用 `cmd.exe /c` 做动作会弹空 cmd 黑框；`wscript.exe` 是 GUI 宿主
  （自身无控制台），vbs 以**窗口样式 0** 启动 cmd，全链路无窗口，并透传退出码。
- **手动 vs 自动**：任务固定带 `--auto`（vbs 幂等，只带一个），该开关下不打印、不暂停；
  手动运行**实时双写**——控制台与日志是同一份文本（`checkin.py` 打印并按 `ACP_RUN_LOG` 追加副本，
  bat 结束前置到日志顶端）；**双击**才在结束后暂停。
- **等网络最长约 5 分钟**：签到前 `ping -n 1 -w 1000 www.workbuddy.cn`（通了立刻走，正常约 1 秒；
  不通每 2 秒一次，最多 100 轮），再做一次 3 秒硬超时的 443 握手复核（防 ICMP 被拦）。
  放弃后以短超时续跑（`WORKBUDDY_TIMEOUT=10`、`WORKBUDDY_RETRIES=0`），
  避免「吞包不回」的链路把整轮拖过 10 分钟时限（被强杀时日志块还没写）。改上限调 `waitnet` 段的 `100`。
- **登录触发为何用 `-AtLogOn`**：`-AtStartup` 要存账户密码、还得把 `config.json` 放开给 SYSTEM，暴露面更大。
- **唤醒为何单独一个事件**：锁屏解锁 / 睡眠恢复**不算新登录**（会话原地恢复），登录触发器不响应；
  本机实测只记 `Kernel-Power` **107**（另 `OR` 上 `Power-Troubleshooter` 1 兼容别的机器）。
  现代待机（S0ix）机器可能记别的 ID，先确认：

```powershell
Get-WinEvent -FilterHashtable @{LogName='System'; Id=107,507} -MaxEvents 20 | Select TimeCreated, Id, ProviderName
```

### 3.3 一天跑 6 次安全吗

安全：两平台**领取前都先查状态**，已签到直接跳过；通知同日只推一次（**只有真正送达才计入**，
未送达下次运行补推）、失败限流（每天 ≤3 条已送达、间隔 ≥60 分钟）。
高频运行只多几次请求，把「关机 / 断网漏签」的风险压到很低。
**同一时刻只跑一个实例**：并发运行会抢日志（`:publish_block` 重写整个文件，另一个实例可能读到写了一半的
行 → 日志出现截断的乱码行或重复行），所以 bat 用锁目录做互斥，拿不到锁的实例直接退出 ——
另一个正在做同样的签到，不会漏签。超过 15 分钟的锁视为被强杀的残留，自动清掉后重试一次。

### 3.4 Linux / macOS

```bash
30 8 * * * cd /opt/AICreditPunch && /usr/bin/python3 checkin.py >> checkin.log 2>&1   # crontab -e
```

要点：先 `cd` 保证相对路径与日志落点；Python 用绝对路径（cron 的 PATH 极简）；`>>` 追加并合并 stderr。
bat 相关特性（最新在前、弹记事本）不适用；Trae 依赖设备指纹，服务器上大概率报 `9074`。

## 4. 通知

内置统一决策（上游的 `WORKBUDDY_NOTIFY` 已删除，本脚本不读）：**当日首次签到成功推一次**；
同日已签到**静默**；失败推送限流（`AICREDIT_MAX_FAIL_ALERTS` 条数 / `AICREDIT_FAIL_ALERT_INTERVAL` 分钟）。
两个平台**合并成一条消息**：标题与启动横幅同款 —— `一体化每日签到脚本 v1.9.1 26.09.17 10:19:36`
（脚本名 + 版本 + 时间到秒；任一平台失败时尾部加「（未全部成功）」），**标题与正文之间不留空行**；
正文一个账号一行、行首用 `[]` 包裹平台名，如 `[WorkBuddy] 熊猫川 签到成功；本次 +100，余额1,534.12`，
末行是那句合计（`合计：2 个账号全部成功，本次 +250`，见 §2.5）。
不会一天收到两条 —— 即使其中只有一个平台需要通知，也只发这一条。
去重记在 `.checkin_state.json` 的 `notify_date_<平台>` —— **只有渠道真正收下才写入**，
所以渠道没配或推送失败时日志会写「未送达」，并在下一次运行（含手动）补推。

配置在 `config.json` 的 `notify` 段（说明文字写 `_` 开头字段，脚本忽略；改完即生效，无需重启任务）：

```json
"notify": { "webhook": { "url": "" },
            "wecom":   { "corpid": "", "corpsecret": "", "agentid": "", "touser": "@all" } }
```

- 渠道 A `webhook.url` 按 URL 自动识别：企业微信群机器人（`qyapi.weixin.qq.com/cgi-bin/webhook/send?key=…`）、
  Server酱（`sctapi.ftqq.com/XXX.send`）、钉钉 / 飞书 / Bark（见 `checkin.py` 的 `_send_webhook`）。
- 渠道 B `wecom`：`gettoken` 换 `access_token` 再 `message/send`，可指定接收人。字段取值：
  `corpid` 企业ID、`corpsecret` 自建应用 Secret、`agentid` AgentId、`touser` 接收人（多个用 `|`）。
  ⚠️ `@all` 是**应用可见范围内的所有人** —— 企业里有同事就会一并收到，只想发自己就填 **UserID**
  （管理后台 → 通讯录能看到；`--test-notify` 不打印 UserID，可在后台「应用 → 可见范围」里核对）。
- 两类可同时启用，任一留空则跳过；**只从 `config.json` 读**（环境变量覆盖未实现）。
- 日志自动脱敏（`key=abc1***yz`；`corpsecret` / `access_token` → `***`）；通知失败只记日志，不影响签到。

**验证配置：`checkin.bat --test-notify`** —— 只发一条测试消息，不签到、不改「当日已推送」去重记录，
逐渠道回报成功或失败原因。企业微信高频失败码：`40001` secret 不对、
`60020` 调用方 IP 不在**可信 IP** 白名单（后台 → 应用 → 企业可信 IP）、
`81013` `touser` 不在应用**可见范围**（填 UserID，不是手机号或姓名）、`60011` agentid 与 Secret 不匹配。

## 5. 安全

- **凭据唯一来源是脚本同目录的 `config.json`**（`accounts` / `trae_accounts` / `notify`）。
  上游那套「环境变量指定配置或灌账号」（`WORKBUDDY_CONFIG` / `WORKBUDDY_ACCOUNTS` / `WORKBUDDY_ACCESS_TOKEN`）
  本仓库未实现 → 迁移到青龙 / Docker / CI 必须带上 `config.json` 或重跑 `--init-*`。
- 敏感级别：`access_token` / `refreshToken` / 推送密钥 🔴 最高；`uid` / `deviceId` / `enterprise_id` 🟡 中；
  `name` / `domain` / `api_base` 🟢 低（`api_base` 有白名单：仅 `*.codebuddy.cn`、`*.workbuddy.cn`、
  `copilot.tencent.com` 的 https）。
- 权限收紧：`icacls config.json /inheritance:r` + `/grant:r "%USERNAME%:(R,W)"`（Linux `chmod 600`）。
- `.gitignore` 已屏蔽 `config.json*`、`*.log`、`.checkin_state.json`、`.workbuddy/`、`__pycache__/`、
  `.venv/`、编辑器与系统文件（保留 `config.json.example`）；推送前先 `git status --short`，别 `git add -f`。
- token 泄露 / 失效：重跑 `--init-workbuddy`（Trae 用 `--init-trae`）。

## 6. 排错

```bash
python checkin.py --today        # ⓪ 今天签没签、任务正不正常（纯本地，先看这个）
python checkin.py --tasks        # ⓪ 计划任务检查（只读）
python checkin.py --dry-run      # ② 只校验配置（改完 config 先跑）   --status-only  # ③ 只查不领
python checkin.py                # ④ 完整签到          --debug  # ⑦ 打印脱敏响应结构
```

| 日志 / 现象 | 含义 / 处理 |
|---|---|
| `不重签；…` / `今日通知已推送，跳过` | 正常（幂等 / 同日第二次） |
| `未找到配置文件` / `配置错误` | 按提示重跑 `--init-workbuddy` |
| `凭证失效或权限不足` / HTTP `401` `403` | token 过期 → `--init-workbuddy`；`429` = 限流，调大 `WORKBUDDY_RETRIES` |
| Trae `9074` / `9095` | 设备指纹失败 / 该设备今日已签别的账号（回原设备；云服务器无解） |
| `领取后回查未确认签到` | 领取已受理但状态未更新，稍后 `--status-only` 复查 |
| `ERROR: python not found` | 改 `checkin.bat` 的 `PYEXE` |
| `WARN: task registration failed …` | 用能管理任务的账户跑 `--install`；运行结束会自动弹记事本 |
| `Unknown argument: xxx` | 参数不认识或用了裸写法 → 看 `--help` |
| `[任务] 缺失 / 异常；动作指向 …` | 跑一次 `checkin.bat` 自动重注册，或 `--install` |
| 触发任务时弹空 cmd 黑框 | 旧版动作是 `cmd.exe /c checkin.bat` → `--install` 改为经 vbs |
| `malformed (1,40) 错误: 无法切换编码` | `resume.xml` 里出现了 `<?xml?>` 声明 → 删掉后 `--install` |
| 唤醒后没补签 | 事件订阅没匹配上（现代待机常记别的 ID）→ 按 §3.2 命令确认后改订阅 |
| 「Checking the network ...」停留较久 | 网络没起来正在等（最长约 5 分钟，见 §3.2） |
| 计划任务「上次结果 0x1」/ `0x41301` | bat 执行失败（查 `PYEXE`、编码）/ 任务正在运行（正常） |
| 日志某行被截断成乱码 / 内容重复 | 两个实例并发写日志（v1.8.3 起 bat 用锁目录互斥，见 §3.3）；
旧日志里的坏行可手工删掉 |
| 日志里出现旧版才有的 `%date%` 之类文本 | `.bat` 自己写的行必须纯 ASCII（v1.5.0 起已无 `%date%`/`%time%`） |
| Trae 每次都报「今日已签到」但其实没签 | v1.7.0 及更早的判定 bug → 升级 |

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `WORKBUDDY_TIMEOUT` / `WORKBUDDY_RETRIES` | `20` / `2` | 单次请求超时（5–120）/ 重试次数（0–5） |
| `AICREDIT_CONCURRENCY` | `4` | 账号并发路数（1–8）；`1` = 串行，逐个跑 |
| `WORKBUDDY_DEBUG` | `false` | 等价 `--debug` |
| `WORKBUDDY_AUTH_FILE` | 空 | 仅供 `--init-workbuddy`（多个用 `;`） |
| `AICREDIT_MAX_FAIL_ALERTS` / `AICREDIT_FAIL_ALERT_INTERVAL` | `3` / `60` | 失败通知每天上限 / 最小间隔（分钟） |
| `APPDATA` / `LOCALAPPDATA` | 系统 | 决定日志落点与凭据扫描起点（缺失时有回退） |

> bat 在**网络探测放弃后**会当次临时覆盖 `WORKBUDDY_TIMEOUT=10`、`WORKBUDDY_RETRIES=0`（不改系统变量）。
> ⚠️ **已失效变量**（上游能力）：`WORKBUDDY_NOTIFY`、`WORKBUDDY_CONFIG`、`WORKBUDDY_ACCOUNTS`、
> `WORKBUDDY_ACCESS_TOKEN`、`WORKBUDDY_WEBHOOK`、`WORKBUDDY_WECOM_*`。

## 7. 版本管理

- 远程 `origin` → `huxc573/AICreditPunch`（公开，MIT）；**公开历史从 `v1.7.0` 起**，
  `v1.0`–`v1.6` 的 40 个提交在本地分支 `archive/full-history`，**不推送**。
- 推送前 `git status --short` 确认无凭据；推标签**只推 `v1.7.0` 及以上**，**不要** `git push --tags`。
- 语义化版本：`MAJOR` 不兼容变更 / `MINOR` 新功能 / `PATCH` 修复。**版本号唯一来源是 `VERSION`**，
  标签 `v<X.Y.Z>`；`CHANGELOG.md` 段标题与 README 顶部都要带 **24 小时制到分钟**的时间。

```bash
git status --short && git add -A && git commit -m "fix(bat): 一句话"
git tag -a v1.9.1 -m "1.9.1: 一句话概要"
git push origin main v1.9.1      # 远程有 TLS 中间人时加 -c http.sslVerify=false（一次性，别写全局）
```

提交信息 `<type>(<scope>): <描述>`，type 取 `feat` / `fix` / `perf` / `refactor` / `docs` / `chore` / `security`。
发版检查：`--dry-run` 通过 + 手动跑一次能签到 → 无凭据入库 → `VERSION` / `CHANGELOG` / README 三处同步 →
提交打标签按名推送 → 跑一次 `checkin.bat` 自动纠正任务路径。

回滚：`git checkout v1.9.1 -- <文件>`（单文件）或 `git checkout -b hotfix/x v1.9.1`，回滚后同步 `VERSION` 与 CHANGELOG。

## 8. 来源声明与致谢

本仓库**不含任何上游脚本文件**（上游文件在 v1.1.0–v1.2.0 期间删除，功能收敛到自维护的 `checkin.py` + `checkin.bat`）：

| 模块 | 与上游的关系 | 上游许可 |
|---|---|---|
| WorkBuddy | ✅ **已独立重写**：只保留公开接口事实（`/v2/billing/meter/checkin-activity-status`、`/v2/billing/meter/daily-checkin` 两条路径、Bearer 头、`api_base` 白名单规则、auth 快照字段约定）；函数划分、命名、错误处理自行设计（`v1.6.0` 及更早曾直接移植） | ⚠️ `tianxing226/AICreditPunch` 未附任何许可证 |
| Trae | 上游 MIT 代码的衍生版：登录 / 刷新 token / 签到 / 查积分四条链路，`checkin_credits/{status,claim}`、`ide_user_ent_usage`、`ExchangeToken`；`9074` 重试 + 设备池轮换、`9095` 跳过 | ✅ `xz0609/trae-work-checkin-ql` · `Copyright (c) 2026 Yiran` |
| Trae 登录（参考） | 仅参考 OAuth 流程（授权链接 → 本地端口收 `refreshToken` → `ExchangeToken`），**未复制代码** | ✅ `Maquer/trae-signin` · `Copyright (c) 2026` |

> 上游链再往前一层：`xz0609/trae-work-checkin-ql` 自述 Fork 自
> [`yang89520/auto-checkin-hub`](https://github.com/yang89520/auto-checkin-hub)（MIT）。
>
> **「独立重写」的口径**：严格 clean-room 做不到（重写者此前读过上游实现）；依据的是**接口行为**
> 这类不受著作权保护的协议事实，代码结构、命名与错误处理已重做。不是教科书式干净房，但已不是原样复制。

**许可**：整仓 MIT（自 `v1.7.0`）——自有代码完全自有；WorkBuddy 段按接口行为独立重写后纳入 MIT；
Trae 段沿用上游 MIT，**保留版权与许可声明**（见 [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md)）。
> 本文档顶部与本节来源表格**必须保留**。

**免责**：第三方逆向脚本，与官方无关，可能违反相关服务条款，接口随时可能失效，**仅供个人学习研究**；
Trae「同账号每天只能在登录它的那台设备领取」属平台限制；请勿把 `config.json` 提交或上传到公开位置。
