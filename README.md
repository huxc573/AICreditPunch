# AICreditPunch · 本地自动签到

> **当前版本：v1.8**（`VERSION` = `1.8.0`　·　2026-09-15 09:55）
> 变更记录见 [CHANGELOG.md](CHANGELOG.md)

WorkBuddy + Trae 一体化自动签到、状态与积分查询工具（支持多账号）。

本仓库两个平台的实现都是在**上游脚本的基础上重写**而来，上游脚本文件已全部删除，
只保留「接口协议」这一层公开事实（请求路径、鉴权头、报文字段）。来源与许可状况
（**动手开源前请先看 [§8](#8-来源声明与致谢)**）：

| 模块 | 上游来源（仅作溯源） | 上游许可 | 本仓库当前状态 |
|---|---|---|---|
| WorkBuddy | [`tianxing226/AICreditPunch`](https://github.com/tianxing226/AICreditPunch) · `workbuddy_checkin.py` v2.2.0 | ⚠️ **未附任何许可证** | ✅ **已独立重写**：只保留公开接口事实，不含其代码 |
| Trae | [`xz0609/trae-work-checkin-ql`](https://github.com/xz0609/trae-work-checkin-ql) · `trae_work_checkin.py` | ✅ MIT · `Copyright (c) 2026 Yiran` | 上游 MIT 代码的衍生版本，保留其声明 |
| Trae 登录流程（参考） | [`Maquer/trae-signin`](https://github.com/Maquer/trae-signin) | ✅ MIT · `Copyright (c) 2026` | 仅参考其 OAuth 流程 |

> **WorkBuddy 段的重写说明**：`v1.6.0` 及更早的版本曾直接移植上游实现；
> 自 `v1.7.0` 起，该段已按接口行为**独立重写**（不含上游代码结构与命名），
> 详见 [§8.1](#81-本项目的构成与来源) 与 [§8.2](#82-许可license)。

在上游实现之外，本仓库**自有**的部分是：**通知编排与失败限流**、**Windows 计划任务封装与自管理**、
**`--today` / `--tasks` 只读查看命令**、**`checkin.bat` 入口与日志前置**，以及**安全加固**。
WorkBuddy 段已按接口行为独立重写，整仓以 **MIT** 许可公开发布（见 [§8](#8-来源声明与致谢)）。

> ✅ 远程 `origin` → [`github.com/huxc573/AICreditPunch`](https://github.com/huxc573/AICreditPunch)（公开）。
> ⚠️ 凭据文件全部被 `.gitignore` 屏蔽，推送前请用 `git status --short` 确认，**不要** `git add -f`。
>
> 📌 本文档内的路径、账号名、设备号**均为占位示例**，不含任何真实凭据。

---

## 1. 目录结构与文件清单

```
AICreditPunch/
├── README.md              本文件：总览 / 快速开始 / 定时任务 / 通知 / 安全 / 排错 / 版本管理 / 来源与致谢
├── CHANGELOG.md           版本变更记录
├── VERSION                当前版本号（唯一来源）
├── LICENSE                MIT 许可全文（版权人 huxc573）
├── THIRD-PARTY-NOTICES.md 第三方组件声明（上游 MIT 版权行 + 许可全文）
│
├── checkin.py             唯一脚本：签到 + 初始化 + 通知（WorkBuddy + Trae，单文件零依赖）
├── checkin.bat            Windows 计划任务入口（ASCII + CRLF，网络等待 + 任务自管理）
├── run-hidden.vbs         计划任务的隐藏窗口包装器（wscript.exe 执行，触发时不弹 cmd 黑框）
├── scheduled-task.resume.xml  唤醒触发任务的 XML 模板（事件触发器，由 checkin.bat 注册）
├── config.json.example    脱敏配置模板（可入库）
├── .gitignore             凭据与运行产物防护
│
├── config.json            🔴 真实配置（账号凭据 + 推送）· 已忽略 + 权限收紧
└── .checkin_state.json    通知去重状态 · 已忽略
```

运行日志不在仓库内：`%APPDATA%\AICreditPunch.log`（见 §2.5）。

| 文件 | 用途 | 含敏感信息 |
|---|---|---|
| `checkin.py` | 唯一脚本：签到 + 初始化（`--init-*`）+ 内置通知 | 否 |
| `checkin.bat` | 计划任务入口；子命令 `--install` / `--uninstall` / `--tasks` / `--today` / `--help`，其它 `--xxx` 转发给 `checkin.py` | 否 |
| `run-hidden.vbs` | 计划任务动作的包装器：`wscript.exe` 以隐藏窗口启动 cmd，触发时不弹黑框 | 否 |
| `scheduled-task.resume.xml` | `AICreditPunch-Resume` 的 XML 模板：`Kernel-Power` 事件 ID 107（睡眠/休眠恢复）触发器 | 否 |
| `config.json.example` | 脱敏模板 | 否 |
| `LICENSE` | MIT 许可全文 | 否 |
| `THIRD-PARTY-NOTICES.md` | 第三方组件声明（上游 MIT 版权行与全文） | 否 |
| `config.json` | 统一配置：账号凭据 + 推送配置（含 `_readme` 注释） | 🔴 是，已加锁且 gitignore |
| `.checkin_state.json` | 通知去重状态 | 否，已 gitignore |
| `%APPDATA%\AICreditPunch.log` | 运行日志（仓库外，最新在前） | 🟡 含账号名 |

> 三个上游产物的文件都已删除，功能全部吸收进 `checkin.py`：
> `workbuddy_checkin.py`（上游 v2.2.0，来源见 §8.1）、`auto_checkin.py` 与 `notify.py`。
> 细化到版本：v1.1.0 吸收通知与编排，v1.2.0 吸收上游 `--setup`（今 `--init-workbuddy`），
> v1.3.0 起初始化命令统一为 `--init-workbuddy` / `--init-trae`。
> **迁移到其他设备只需带上 `checkin.py` + `config.json`**（或在新设备重跑 `--init-*`）。

**为什么脚本都放在根目录？**
Windows 计划任务里写的是 `checkin.bat` 的绝对路径，为不破坏已注册的定时任务，可执行文件保持扁平结构。
`checkin.bat` 内部用 `%~dp0` 解析自己的位置，所以**把整个目录改名或搬到别处也不会失效**
（任务里存的旧路径会在下次运行时自动纠正，见 §3.1）。

---

## 2. 快速开始

### 2.1 环境要求

结论先行：**没有 `requirements.txt`，也不需要**。

`checkin.py` 只 `import` 标准库（`argparse / json / urllib / pathlib` 等），
不依赖 `requests`、不依赖任何第三方包，所以不需要 `pip install`、不需要虚拟环境，
**Python ≥ 3.8** 即可运行。

```bash
python --version                # 需 >= 3.8
python checkin.py --version     # 能打印版本号即环境正常
```

Windows 上若 `python` 不在 PATH，直接用绝对路径（`checkin.bat` 里的 `PYEXE` 变量就填这个）：

```bat
set "PYEXE=D:\Dev\Python\Env\Python312\python.exe"
```

> **建议**：计划任务用系统 Python。WorkBuddy 自带的托管 Python 会随 WorkBuddy 升级被替换，可能导致任务失效。

### 2.2 初始化 WorkBuddy（`--init-workbuddy`）

在**已登录 WorkBuddy 桌面端**的机器上执行：

```bash
python checkin.py --init-workbuddy
```

脚本会自动扫描下列位置（**有界、不递归**），找到 `workbuddy-desktop*.info` 后导入并
按 uid（无 uid 时按 token）去重合并：

```
%LOCALAPPDATA%\CodeBuddyExtension\Data\Public\auth\
%LOCALAPPDATA%\WorkBuddy\Data\Public\auth\
%APPDATA%\CodeBuddyExtension\Data\Public\auth\
%APPDATA%\WorkBuddy\Data\Public\auth\
~/Library/Application Support/<上两个产品>/Data/Public/auth/    (macOS)
~/AppData/Local/<上两个产品>/Data/Public/auth/                  (无 %LOCALAPPDATA% 时的回退)
```

目录里没有 `.info` 文件时**不报错**，只提示未找到（也会打印扫描日志）。

指定文件或环境变量：

```bash
python checkin.py --init-workbuddy --auth-file C:\path\to\workbuddy-desktop.info
# 或先 set "WORKBUDDY_AUTH_FILE=<路径>" 再重跑 --init-workbuddy（多个用 ; 分隔）
```

**刷新 token 只替换 `accounts`**：`notify`、`trae_accounts` 与 `_` 前缀注释字段原样保留（已实测结构对比一致）。
日志出现 `401` / `凭证失效或权限不足` 时，重跑本命令即可。

### 2.3 初始化 Trae（`--init-trae`）

Trae 凭证无法从本地文件导入，需要走浏览器 OAuth：

```bash
python checkin.py --init-trae
```

1. 脚本自动打开浏览器访问授权页 `https://www.trae.cn/authorization?...`
   （参数含 `client_id`、`login_version=1`、`auth_type=local`、`redirect=0`、
   `login_trace_id`、`auth_callback_url` 及 `x_*` 设备指纹等）。
2. 在页面完成手机号/验证码或第三方登录。
3. 登录成功后浏览器跳转到本地回调
   `http://127.0.0.1:<端口>/authorize?refreshToken=...&userInfo=...&userJwt=...`
   （**端口从 18080 起在 18080–18099 之间自动挑一个空闲的**，所以不一定是 18080；
   终端会打印本次实际使用的回调地址），脚本用 `refreshToken` 调
   `api.trae.com.cn/cloudide/api/v3/trae/oauth/ExchangeToken` 换取 `accessToken`，
   写入 `config.json` 的 `trae_accounts` 段（含设备指纹）。
4. 浏览器没自动开就手动复制终端打印的链接；回调由本地端口监听自动接收，无需手工粘贴。

登录实现参考 [Maquer/trae-signin](https://github.com/Maquer/trae-signin)。

> ⚠️ **设备指纹限制**：Trae 签到对设备指纹有校验。
> **同一个账号每天只能在"做过 `--init-trae` 的那台设备"上领取**；换机器或放云端服务器会报
> `9074`（设备指纹校验失败），这是平台限制，不是脚本 bug。
> 状态码含义：`9074` 设备指纹失败（脚本会自动重试并轮换设备池）、`1001` 认证失败（自动刷新 token 重试）、
> `9095` 该设备今日已签过别的账号。

### 2.4 运行

```bash
python checkin.py                      # 跑所有已启用平台（推荐入口，含通知去重）
python checkin.py --workbuddy-only     # 只跑 WorkBuddy
python checkin.py --trae-only          # 只跑 Trae
python checkin.py --status-only        # 只查状态，不领取（可反复执行）
python checkin.py --dry-run            # 只校验配置，不发网络请求
python checkin.py --debug              # 打印脱敏的原始响应，排错用
python checkin.py --today              # 日常查看：今日状态 + 任务下一班次 + 最近一次运行（纯本地）
python checkin.py --tasks              # 检查本脚本的三个计划任务（只读，不修改）

# 初始化（每个平台一次，用法与其它参数一致）
python checkin.py --init-workbuddy     # 导入 WorkBuddy 本机凭据（可加 --auth-file <路径>）
python checkin.py --init-trae          # Trae 浏览器 OAuth 登录
python checkin.py --init               # 两个平台依次初始化
```

> 初始化只有 `--init-workbuddy` / `--init-trae` / `--init` 三种写法；旧的位置参数写法
> （`checkin.py setup-workbuddy` / `setup-trae` / `login`）**已移除**，传入会直接报参数错误（退出码 `2`）。

**幂等说明**：`python checkin.py` 会先查询每个平台的今日签到状态；若已签到，
只打印状态与余额，**不会重复调用领取接口**，并在输出里标注「本次无需签到」。

### 2.5 输出与日志

输出结构（两平台完全一致，v1.4.0 起首尾加起止标记）：

```text
一体化每日签到脚本 v1.8.0 启动
===== WorkBuddy =====
[示例账号] 查询签到状态；API=https://www.codebuddy.cn
[示例账号] 今日已签到，本次无需签到；本次 +100，连续 1 天，当前积分余额 2,242.18
[示例账号] 积分构成；套餐基础 142.18，平台奖励 2,100，购买积分 0
WorkBuddy 今日已推送成功通知，本次静默跳过
===== Trae =====
[示例账号] 查询签到状态；device=xxxxxxxx***
[示例账号] 今日已签到，本次无需签到；本次 +150，当前积分余额 184
本次脚本执行完毕。
```

- **首行 / 末行**是整次运行的起止标记（`一体化每日签到脚本 vX.Y.Z 启动`、`本次脚本执行完毕。`），
  所有退出路径（成功 / 失败 / 配置错误 / dry-run / 初始化）都会输出；末行后面额外留一个空行，
  方便和上一次运行的日志块分隔。
- 中间按平台分块（`===== 平台 =====`），块标题已表明平台，块内每行统一为
  `[账号名] 状态；明细`，**不重复平台名**；与账号无关的平台级提示才写成 `平台 状态；明细`。
  通知决策类日志（`WorkBuddy 今日已推送成功通知，本次静默跳过`、
  `Trae 失败，但今日已推送 3 条（上限 3）或间隔不足，跳过推送`）属于**平台级**，不带账号名。
- 推送到手机的结果文案：`账号名（平台）：状态；明细`，例 `示例账号（Trae）：签到成功；本次 +150，当前积分余额 184`。
- `当前积分余额` 是**账号真实可用积分**，与桌面端「设置 - 套餐与积分」的总剩余一致；
  WorkBuddy 还会多打一行 `积分构成`（套餐基础 / 平台奖励 / 购买积分）。口径见 §2.8。

**日志位置**：`%APPDATA%\AICreditPunch.log`（**直接放根目录，不建子文件夹**；
资源管理器地址栏输入 `%APPDATA%` 就能看到）。

| 行为 | 说明 |
|---|---|
| **最新在前** | `checkin.bat` 把本次输出与旧日志做前置拼接，**最新一次运行永远在文件最顶端** |
| **手动打开** | `checkin.bat --logs` 随时用系统记事本打开该文件；文件还不存在时给出提示并返回退出码 `1` |
| **首次签到弹窗** | 当天首次成功签到时，运行结束自动用【系统记事本】打开该文件；同日后续运行不再弹 |
| **注册失败弹窗** | 计划任务注册失败时同样在运行结束后打开日志，第一条就是 `WARN`，不用自己找文件 |
| 回退路径 | `%APPDATA%` 不可用时依次回退 `$XDG_CONFIG_HOME` → 用户主目录 → 脚本同目录 |

> 两个弹窗都发生在**本次输出已置顶之后**：`checkin.py` 只留一个标记文件，
> 由 `checkin.bat` 完成前置拼接再打开，所以记事本里第一眼就是**本次**运行，而不是上一次的旧内容。
>
> 直接从 `python checkin.py` 运行**不会**写日志文件（只有 stdout），日志落盘由 `checkin.bat` 负责；
> 这种情况下没有 `checkin.bat` 兜底，首次签到时会由 `checkin.py` 自己立刻打开日志（可能少一块刚写的内容）。

### 2.6 未初始化时怎么办

在**没初始化过**的机器上直接运行 `python checkin.py`（或 `checkin.bat`），
脚本会检测到缺少账号并打印初始化指引，退出码 `1`，且**不发送任何网络请求**：

```text
一体化每日签到脚本 v1.8.0 启动
配置错误：未找到配置文件：...\config.json；请先运行 ... 完成初始化
尚未初始化，请先执行下面至少一条初始化命令：
  python checkin.py --init-workbuddy     # WorkBuddy：导入本机桌面端登录凭据（自动发现）
  python checkin.py --init-trae          # Trae：打开浏览器完成 OAuth 登录
  python checkin.py --init               # 两个平台依次初始化
初始化完成后直接运行 `python checkin.py` 即可签到；详见 README §2.2。
本次脚本执行完毕。
```

只缺单平台时，提示相应变成
`WorkBuddy：尚未初始化，请先运行 `python checkin.py --init-workbuddy`（导入本机登录凭据）`。

### 2.7 日常查看 与 计划任务检查

两个**只读**命令：不写配置、不动计划任务、不发网络请求，随时可跑。

```bash
python checkin.py --today     # 等价：checkin.bat --today
python checkin.py --tasks     # 等价：checkin.bat --tasks
```

#### `--today`（日常查看）

一眼看完今天的状态，不用翻日志：

```text
一体化每日签到脚本 v1.8.0 启动（日常查看）
===== 日常查看 =====
日期：2026-09-14（周一）
[WorkBuddy] 今日已签到；1 个账号；成功记录 2026-09-14
[Trae] 今日已签到；1 个账号；成功记录 2026-09-14
[运行记录] 日志中今日 3 次；含计划任务触发与手动运行
[计划任务] 已注册；下次自动运行 2026-09-14 23:45；上次 2026-09-14 20:45
[计划任务] 唤醒触发已启用；从睡眠 / 休眠恢复时补签；上次 从未运行
[日志文件] 最新在前；C:\Users\<你>\AppData\Roaming\AICreditPunch.log

最近一次运行（日志顶部）：
  [2026-09-14 20:45:03] 一体化每日签到脚本 v1.8.0 启动
  [2026-09-14 20:45:03] ===== WorkBuddy =====
  [2026-09-14 20:45:04] [示例账号] 今日已签到，本次无需签到；本次 +100，连续 1 天，当前积分余额 2,242.18
  ...
本次脚本执行完毕。
```

| 行 | 含义 |
|---|---|
| `日期` | 本机当天日期与星期 |
| `[平台] 今日已签到 / 今日尚未签到` | 读 `.checkin_state.json` 的当日成功记录，**纯本地判断** |
| `[平台] 今日有失败记录` | 当天出现过失败（记在 `.checkin_state.json` 里）；明细是已推送的告警条数 |
| `[运行记录]` | 日志里当天出现过几次「…启动」（含计划任务触发与手动运行） |
| `[计划任务]` | 主任务（`-Daily`）的下次触发时间；`唤醒触发已启用 / 缺失` 指睡眠恢复任务（`-Resume`）；缺失时直接给出修复命令 |
| `最近一次运行` | 把日志最顶端那一块原样贴出来，最多 40 行 |

退出码：`0` = 今日各平台都已签到；`1` = 还有未签到、平台未初始化、或计划任务缺失等待办项。

> `--today` 只回答「今天签没签」，**不查实时余额**；要看余额用 `--status-only`（会联网查询，但不领取）。

#### `--tasks`（计划任务检查）

核对三个任务是否注册、是否指向**当前目录**的 `checkin.bat`：

```text
一体化每日签到脚本 v1.8.0 启动（计划任务检查）
===== 计划任务检查 =====
入口脚本：D:\Dev\Workspaces\WorkBuddy\AICreditPunch\checkin.bat

[AICreditPunch-Daily] 正常；每日 6 次（08:45、11:45、14:45、17:45、20:45、23:45）；下次 2026-09-14 23:45；上次 2026-09-14 20:45；上次结果 成功（0x0）
[AICreditPunch-Startup] 正常；用户登录时触发；上次结果 从未运行（0x41303）
[AICreditPunch-Resume] 正常；从睡眠 / 休眠恢复时触发；上次结果 从未运行（0x41303）

汇总：3/3 个计划任务正常，均指向 D:\Dev\Workspaces\WorkBuddy\AICreditPunch\checkin.bat
要强制重装或修复：`checkin.bat --install`；直接跑一次 `checkin.bat` 也会自动补齐。
```

- 常见「上次结果」：`成功（0x0）`、`正在运行（0x41301）`、`从未运行（0x41303）`，其余非零码按异常显示。
- 任务**缺失**或**动作指向别处**（例如目录改名后）会标成 `缺失` / `异常`，并提示修复命令；退出码 `1`。
- 实现上走 PowerShell `Get-ScheduledTask`，把字段名换成英文再解析，
  避开 `schtasks /fo LIST /v` 那种「字段名随系统语言变化」的坑；
  PowerShell 不可用时退化为「只判存在」，仍会给出结论。

---

### 2.8 积分口径（余额从哪来）

日志里的三个数字口径不同，别混：

| 文案 | 含义 | 来源 |
|---|---|---|
| `本次 +N` | **本次签到**拿到的积分 | WorkBuddy：活动报文的 `daily_credit`；Trae：签到前后余额差**实测** |
| `当前积分余额 N` | 账号**真实可用积分** | WorkBuddy：`get-user-resource-summary`；Trae：`usage_summary.total_amount - consumed_amount` |
| `积分构成；…` | 余额由哪几块构成 | WorkBuddy 的三条资源包接口（与桌面端同源） |

**WorkBuddy**：余额与构成走桌面端同一组接口 —— 先取
`GET 语义的 POST /billing/meter/get-user-resource-summary`（各资源包周期总额 / 剩余），
再取免费包 `…/get-user-resource-free-packages` 与付费包 `…/get-user-resource-paid-packages` 明细：

- **总剩余积分** = 汇总接口各包 `CycleRemainCapacity` 之和（= 桌面端「总剩余积分」大数字）
- **平台奖励积分** = 赠送包（`SubProductCode` 含 `bonus_pack`）剩余之和
- **购买积分** = 付费包剩余之和
- **套餐基础积分** = 总剩余 − 平台奖励 − 购买

> 这三条路由**不带 `/v2` 前缀**，与签到那两条（`/v2/billing/meter/…`）不同，别拼错。
>
> ⚠️ 签到报文里的 `total_credits` **不是**余额，而是「活动期内累计获得」
> （= 每日额度 × 活动期内签到天数，实测每日 100、签到 2 天 → `200`，而账号真实余额两千多）。
> `v1.8.0` 起已不再把它当余额显示。

**Trae**：`本次 +N` 用**实测**——签到前后各查一次余额取差值（`v1.8.0` 起）。
status 报文里声明的 `credits + extra_credits` 会虚报：实测声明 `150 + 50 = 200`，
而余额只涨 `150`，权益包清单里也只有一笔 `credits_limit=150` 的「签到奖励」，
`extra_credits` 并未形成权益包、实际未到账。所以只取 `credits` 作回退，**不再相加**。

**失败降级**：余额 / 构成查询失败只少打一行（`当前积分余额 …` / `积分构成；…`），
不影响签到结果，也**不会**把「查不到」写成 `0`。

---

## 3. 定时任务（Windows）

### 3.1 自动安装 / 自动修复

```cmd
checkin.bat               :: 跑签到；任务缺失或路径过期时自动注册
checkin.bat --install     :: 强制重装三个任务
checkin.bat --uninstall   :: 卸载三个任务
checkin.bat --logs        :: 用记事本打开本地日志（见 §2.5）
checkin.bat --tasks       :: 检查三个任务是否正常（只读）
checkin.bat --today       :: 日常查看，见 §2.7
checkin.bat --help        :: 查看用法
```

> 子命令一律以 `--` 开头。**只有帮助类例外**：`--help`、`-h`、`/?`、裸 `help` 都能打印用法。
> 裸的 `install` / `uninstall` / `logs` 已**不再接受**，会提示 `Unknown argument: xxx` 并返回退出码 `2`。
> 其它任何 `--xxx` 参数都会**原样转发**给 `checkin.py`，例如 `checkin.bat --status-only`、`checkin.bat --init-trae`、`checkin.bat --version`；
> 这类转发是**只读交互**（输出直接打到控制台、不写日志），所以适合随手查看。

`checkin.bat` 每次运行都会检查三个任务是否**存在**且**指向当前目录**：

- 缺失，或任务里存的还是**旧路径**（比如项目目录改过名）→ 自动重注册，结果写进日志；
- 已存在且指向正确 → 跳过，不做任何改动；
- 注册失败（权限不足等）**不影响本次签到**：日志里留两条 `WARN`，并在本次运行结束后
  **自动用记事本打开日志**，失败原因直接摆在眼前（见 §2.5）。

因此**换台电脑或改名后**，把目录拷过去跑一次 `checkin.bat` 就能自动接上定时，无需手工敲 `schtasks`。

注册通过 PowerShell 的 `Register-ScheduledTask` 完成（**普通账户权限即可**，不需要管理员），
好处是能带上「**错过触发后尽快补跑**」（`StartWhenAvailable`）等设置。
注意 `schtasks /create /sc onlogon` 建登录任务需要管理员权限，所以脚本内部走的是 PowerShell 那条路。

`-Daily` / `-Startup` 用 `New-ScheduledTaskAction` 等 cmdlet 组装；`-Resume` 要的是**事件触发器**，
cmdlet 表达不了，于是读 [`scheduled-task.resume.xml`](scheduled-task.resume.xml) 后走
`Register-ScheduledTask -Xml` 注册。该模板**不能出现 `<?xml?>` 声明**：`-Xml` 收的是字符串，
带声明会被直接拒（`The task XML is malformed. (1,40) 错误: 无法切换编码`），哪怕 XML 本身完全合法。

### 3.2 卸载

```cmd
checkin.bat --uninstall
```

输出示例：

```text
Removing AICreditPunch scheduled tasks ...

  [ OK ] AICreditPunch-Daily - removed
  [ OK ] AICreditPunch-Startup - removed
  [ OK ] AICreditPunch-Resume - removed

Done. The log file was kept: "<%APPDATA% 展开后的完整路径>\AICreditPunch.log"
Run this file without arguments to install the tasks again.
```

> 上面是**示意**：`checkin.bat` 打印的是 `%APPDATA%` **展开后**的真实路径。
> 任务本来就不存在时该行会变成 `[skip] AICreditPunch-Daily - not installed`（不算失败，退出码仍为 0）。

卸载只删计划任务，**不动脚本、配置与日志**；哪天想恢复，运行一次不带参数的 `checkin.bat` 即可。

### 3.3 任务清单

| 任务名 | 触发 | 说明 |
|---|---|---|
| `AICreditPunch-Daily` | 每天 08:45 / 11:45 / 14:45 / 17:45 / 20:45 / 23:45（6 个触发器） | 主签到任务，开启 `StartWhenAvailable`（错过补跑） |
| `AICreditPunch-Startup` | 用户登录时 | 登录补跑，防止当天错过 |
| `AICreditPunch-Resume` | **从睡眠 / 休眠恢复时**（`Kernel-Power` 事件 ID 107） | 唤醒补签；事件触发器只能走 XML（见 §3.1） |

动作均为 `wscript.exe "<项目目录>\run-hidden.vbs" "<项目目录>\checkin.bat --auto"`，以**当前用户 + 交互式令牌**运行（无需存密码）。

> **为什么有一层 vbs**：交互式令牌下，直接用 `cmd.exe /c` 做任务动作，每次触发都会在屏幕上弹一个
> 空的 cmd 黑框。`wscript.exe` 是 GUI 子系统宿主（自身没有控制台），`run-hidden.vbs` 再以**窗口样式 0**
> （隐藏）启动 cmd，整条链路不再出现任何窗口。vbs 会**等待 bat 结束并透传退出码**，
> 所以「上次运行时间 / 上次结果」依然准确，10 分钟执行时限照常生效。
>
> **手动与自动的差别**：任务固定带 `--auto`（vbs 保证只带一个），该开关下**不打印、不暂停**，
> 全程无窗口。手动运行不带它，本次结果会**实时打印在窗口里**，同时照样写进日志 —— 两处是
> 同一份文本：`checkin.py` 打到控制台，并按 `ACP_RUN_LOG` 追加一份临时副本，bat 结束后
> 把它前置到日志顶端（不再切代码页）。**双击**时结束后停留等待按键；从已打开的终端里
> 运行则不暂停。想让一次手动运行也不出声，就显式加 `--auto`：`checkin.bat --auto`。

常用管理命令：

```cmd
schtasks /query /tn "AICreditPunch-Daily" /fo LIST /v    :: 查看详情
schtasks /run   /tn "AICreditPunch-Daily"                :: 立即触发测试
schtasks /end   /tn "AICreditPunch-Daily"                :: 终止
schtasks /change /tn "AICreditPunch-Daily" /disable      :: 临时停用
```

手动创建（一般不必，bat 会自动注册）：

```cmd
schtasks /create /tn "AICreditPunch-Daily" ^
  /tr "wscript.exe \"<项目目录>\run-hidden.vbs\" \"<项目目录>\checkin.bat --auto\"" /sc daily /st 08:45 /f
```

> **关于「开机时自动执行」的取舍**：用的是 `-AtLogOn`（用户登录时触发），而不是真正的 `-AtStartup`。
> 后者在**用户尚未登录**时就运行，必须勾选「不管用户是否登录都要运行」——会要求输入并保存账户密码，
> 且要放开 `config.json` 权限给 SYSTEM，凭据暴露面变大。`-AtLogOn` 无需管理员、无需存密码，够用。
>
> **为什么唤醒要单独一个事件触发器**：锁屏后解锁、以及休眠 / 睡眠恢复，都**不算新登录** ——
> 会话是被原地恢复的（`SessionId` 与访问令牌都不变），`-AtLogOn` / `-AtStartup` 一概不响应，
> 所以只能订阅系统事件。实测本机恢复只记 `Microsoft-Windows-Kernel-Power` **事件 ID 107**，
> 订阅因此以 107 为主，另把 `Microsoft-Windows-Power-Troubleshooter` 事件 ID 1 一起 `OR` 进去兼容
> 别的机器（匹配不到也无害）。想确认自己这台记哪些事件：
>
> ```powershell
> Get-WinEvent -FilterHashtable @{LogName='System'; Id=107,507} -MaxEvents 20 |
>   Select-Object TimeCreated, Id, ProviderName
> ```
>
> 用**现代待机（S0ix）**的机器可能记的是别的 ID，先跑一次上面这条再决定订阅内容。

### 3.4 为什么一天跑 6 次是安全的

- 两个平台**领取前都先查状态**，今日已签到会直接跳过，不会重复领取；
- 唤醒触发同理：恢复后若当天已经签过，只会在日志多一条 `今日已签到，本次无需签到`，不会重复领；
- `checkin.py` 内置通知逻辑保证**同日只推送一次成功通知**，失败推送按「每天 ≤3 条、间隔 ≥60 分钟」限流。

所以高频运行只增加一点点请求，不会重复领积分、不会刷屏。这也让「关机 / 断网导致某次没跑」的漏签风险降到很低。

### 3.5 Linux / macOS（crontab）

```bash
crontab -e
# 每天 08:30 签到
30 8 * * * cd /opt/AICreditPunch && /usr/bin/python3 checkin.py >> checkin.log 2>&1
```

要点：

- `cd <目录> &&` —— 先切工作目录，保证相对路径与日志落点正确
- Python 用**绝对路径**（`which python3` 查），cron 的 PATH 极简
- `>> checkin.log 2>&1` —— 追加写入并合并 stderr；用 `>>` 而非 `>` 保留历史

免编辑 crontab 的安装方式：

```bash
(crontab -l 2>/dev/null; echo '30 8 * * * cd /opt/AICreditPunch && /usr/bin/python3 checkin.py >> checkin.log 2>&1') | crontab -
crontab -l | grep AICreditPunch    # 确认已写入
```

> Linux / macOS 上 `checkin.bat` 用不了，日志「最新在前」与「首次签到弹记事本」是 Windows 侧特性。
> Trae 部分依赖本机设备指纹，放服务器上跑大概率报 `9074`（见 §2.3）。

---

## 4. 通知

通知由 `checkin.py` **内置**统一决策，是否推送、推几条都由本脚本自己判断（见 §4.1）。

> **历史说明**：上游脚本的 `WORKBUDDY_NOTIFY=true` 开关**已随上游脚本一并删除**，
> 本脚本**不再读取**这个变量。设置它不会有任何效果，也不会像以前那样每次运行都推一条。

### 4.1 推送时机

| 场景 | 是否推送 |
|---|---|
| 当日首次签到成功 | ✅ 推送一次 |
| 同日后续运行（已签到） | ❌ 静默，仅写日志 |
| 签到失败 | ✅ 推送，但限流：默认每天最多 3 条、间隔 ≥60 分钟 |

限流是必要的：token 过期时若不限流，6 次运行会连刷 6 条消息。
可用 `AICREDIT_MAX_FAIL_ALERTS`（条数）、`AICREDIT_FAIL_ALERT_INTERVAL`（分钟）调整。

### 4.2 配置位置：`config.json` 的 `notify` 段

推送配置与账号凭据统一放在同一个 `config.json`：

```json
{
  "_readme": ["...文件说明..."],
  "accounts": [{ "name": "示例账号", "access_token": "【敏感】" }],
  "notify": {
    "_comment": ["...推送时机说明..."],
    "webhook": { "url": "" },
    "wecom":   { "corpid": "", "corpsecret": "", "agentid": "", "touser": "@all" }
  }
}
```

> JSON 不支持 `//` 或 `#` 注释，说明文字一律写在 `_` 开头的字段里，脚本会忽略它们。
> 填完保存即生效，**无需重启计划任务**。两类渠道可同时启用；任一留空则静默跳过，不影响另一个。

### 4.3 渠道 A：Webhook（群机器人等）

填 `notify.webhook.url`，按 URL 自动识别：

| 渠道 | 地址格式 |
|---|---|
| 企业微信群机器人（直达微信群） | `https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx` |
| Server酱（推送到个人微信服务号） | `https://sctapi.ftqq.com/XXX.send` |
| 钉钉 / 飞书 / Bark | 见 `checkin.py` 中 `_send_webhook` 的说明 |

### 4.4 渠道 B：企业微信「应用」推送

与群机器人是**两套 API**：先 `GET /cgi-bin/gettoken` 换 `access_token`，再 `POST /cgi-bin/message/send`。
优点是可指定接收人、不依赖某个具体的群。填 `notify.wecom` 四个字段：

| 字段 | 取值位置（企业微信管理后台） |
|---|---|
| `corpid` | 我的企业 → 企业信息 → 页面最下方「企业ID」 |
| `corpsecret` | 应用管理 → 自建应用 → Secret（点「查看」会推送到你的企业微信） |
| `agentid` | 应用管理 → 自建应用 → AgentId |
| `touser` | 接收人；`@all` = 应用可见范围所有人，多个用 `\|` 分隔 |

> 通知配置**只从 `config.json` 读**，不支持环境变量覆盖：上游脚本的 `WORKBUDDY_WEBHOOK`、
> `WORKBUDDY_WECOM_CORPID` / `_SECRET` / `_AGENTID` / `_TOUSER` **本仓库都没有实现**。
> `touser` 留空时按 `@all` 处理。

### 4.5 测试

```bash
python checkin.py --status-only   # 只查不领取，用来验证配置
```

日志中的 webhook 地址会自动脱敏（`key=abc1***yz`），`corpsecret` / `access_token` 在异常信息里替换为 `***`。
**通知失败只记日志，绝不影响签到主流程。**

---

## 5. 安全

### 5.1 凭据保存与加载

**唯一来源是脚本同目录的 `config.json`**——新版已不存在"用环境变量指定配置文件"（`WORKBUDDY_CONFIG`）
或"用环境变量直接灌账号"（`WORKBUDDY_ACCOUNTS` / `WORKBUDDY_ACCESS_TOKEN`）这类写法：

```text
<脚本目录>/config.json
├── accounts        WorkBuddy 账号（由 --init-workbuddy 写入）
├── trae_accounts   Trae 账号（由 --init-trae 写入，含设备指纹）
└── notify          Webhook / 企业微信应用推送
```

> 这两个环境变量是**上游脚本的能力，本仓库没有实现**。
> 因此迁移到青龙 / Docker / CI 时，必须**把 `config.json` 一起带上**（见 §1 末尾），
> 或者在新环境里重跑一次 `--init-*`。

推荐做法（安全等级从高到低）：

1. **`config.json` + 文件权限收紧**（本机 / 自建服务器首选，见 §5.3）
2. **放到不进版本控制的位置**——`config.json` 已被 `.gitignore` 屏蔽，不要 `git add -f`
3. **不要在命令行里回显 token**——`config.json` 之外别的地方（shell history、聊天记录）都不要留

### 5.2 字段与敏感级别

| 字段 | 必填 | 敏感级别 | 说明 |
|---|:--:|:--:|---|
| `access_token` | ✅ | 🔴 **最高** | 等价于完整登录态。脚本也接受 `accessToken` / `token` 别名 |
| `refreshToken`（Trae） | ✅ | 🔴 **最高** | 用于换取新 accessToken |
| `notify.webhook.url` / `wecom.corpsecret` |  | 🔴 最高 | 推送密钥，泄露即可被冒充推送 |
| `uid` / `userId` / `deviceId` / `claimDeviceId` |  | 🟡 中 | 用户与设备标识；`uid` 留空时按 token 去重 |
| `enterprise_id` |  | 🟡 中 | 企业版账号标识，个人版留 `""` |
| `domain` / `api_base` / `name` / `enabled` |  | 🟢 低 | `api_base` 受白名单校验：仅允许 `*.codebuddy.cn` / `*.workbuddy.cn` / `copilot.tencent.com` 的 https 地址 |

### 5.3 文件权限加固

```bat
:: Windows（等价于 Linux chmod 600）：只给当前用户读写，断开继承
icacls config.json /inheritance:r
icacls config.json /grant:r "%USERNAME%:(R,W)"
icacls config.json        :: 复核，应只看到一行授权
```

```bash
# Linux / macOS
chmod 600 config.json
```

`config.json` 权限被收紧为**仅本人可读写**（复核输出只有一行 `config.json <本机用户>:(R,W)`）。

### 5.4 `.gitignore`

```gitignore
# ===== 凭据与真实配置：绝不入库 =====
config.json
config.json.*
*.bak
*.token
!config.json.example

# ===== 运行产物 =====
checkin.log
*.log
.checkin_state.json

# ===== 工作区记忆（本地私有，含会话与自动化记录）=====
.workbuddy/

# ===== Python =====
__pycache__/
*.pyc
*.pyo
.venv/
venv/

# ===== 编辑器 / 操作系统 =====
.vscode/
.idea/
.DS_Store
Thumbs.db
```

`git status` 里**不应出现**上述任何文件；`git add -A` 也不会把本地记忆带进提交。

### 5.5 其它

| 措施 | 说明 |
|---|---|
| 远程仓库 | `origin` → `github.com/huxc573/AICreditPunch`（**公开**）；凭据文件全部被 `.gitignore` 屏蔽，推送前先 `git status --short` 复核 |
| 日志脱敏 | webhook `key=abc1***yz`；`corpsecret` / `access_token` 输出为 `***` |
| 无第三方依赖 | 只有标准库，避免供应链风险 |

若 token 泄露或失效：`python checkin.py --init-workbuddy` 覆盖刷新即可（Trae 用 `--init-trae`）。

---

## 6. 排错

### 6.1 分级测试（从安全到真实）

```bash
python checkin.py --today          # ⓪ 今天签没签、任务正不正常（纯本地，先看这个）
python checkin.py --tasks          # ⓪ 计划任务检查（只读）
python checkin.py --version        # ① 版本/环境
python checkin.py --dry-run        # ② 只校验配置，不发网络请求（改完 config 先跑这个）
python checkin.py --status-only    # ③ 只查状态，不领取
python checkin.py                  # ④ 完整签到（先查状态，已签则跳过领取）
python checkin.py --workbuddy-only # ⑤ 只跑 WorkBuddy
python checkin.py --trae-only      # ⑥ 只跑 Trae
python checkin.py --debug          # ⑦ 打印脱敏后的响应结构
```

计划任务触发测试：

```cmd
checkin.bat --tasks                :: ① 确认任务存在且指向当前目录
schtasks /run /tn "AICreditPunch-Daily"
schtasks /run /tn "AICreditPunch-Resume"   :: 唤醒任务也可按需试跑（真验证要真的睡一次）
timeout /t 6 /nobreak >nul
notepad "%APPDATA%\AICreditPunch.log"
```

### 6.2 日志排查表

| 日志/报错 | 含义 | 处理 |
|---|---|---|
| `一体化每日签到脚本 vX.Y.Z 启动` | 运行起点标记 | 正常；往下看本次结果 |
| `本次脚本执行完毕。` | 运行终点标记 | 正常；该行之前是本次全部输出 |
| `今日已签到，本次无需签到；...` | 当日已领过，幂等 | 无需处理 |
| `今日已推送成功通知，本次静默跳过` | 同日第二次运行 | 正常 |
| `未找到配置文件` / `配置错误` | 配置缺失/路径不对 | 按提示重跑 `--init-workbuddy` |
| `凭证失效或权限不足` / HTTP `401` `403` | token 过期或失效 | 重跑 `python checkin.py --init-workbuddy` |
| HTTP `429` | 限流 | 调大 `WORKBUDDY_RETRIES`，或错开执行时间 |
| Trae `9074` | 设备指纹校验失败 | 换回做过 `--init-trae` 的那台设备；云服务器无解 |
| Trae `9095` | 该设备今日已签过别的账号 | 一天一台设备一个账号，属平台限制 |
| `领取后回查未确认签到` | 领取已受理但状态未更新 | 稍后用 `--status-only` 复查，一般无需重跑 |
| `ERROR: python not found` | `.bat` 里 `PYEXE` 路径失效 | 编辑 `checkin.bat` 改 `PYEXE` |
| `WARN: task registration failed - opening the log in notepad.` | 计划任务注册失败（本机注册不成功） | 用有权管理任务的账户跑 `checkin.bat --install`；本次运行结束会自动弹记事本，第一条就是失败原因 |
| `Unknown argument: xxx` | `checkin.bat` 不认识这个参数，或用了已取消的裸写法（`install` / `uninstall` / `logs`） | 用 `checkin.bat --help` 看子命令；子命令一律 `--` 开头，唯有帮助类可用 `help` / `-h` / `/?` |
| `[平台] 今日尚未签到`（`--today`） | 本地状态里没有今日成功记录 | 跑一次 `checkin.bat` 补签；若刚初始化过属正常 |
| `[平台] 未初始化`（`--today`） | 该平台没有配置账号 | 按提示跑 `--init-workbuddy` / `--init-trae` |
| `[AICreditPunch-Daily] 缺失`（`--tasks`） | 计划任务不存在 | 跑 `checkin.bat --install` 重装 |
| `[AICreditPunch-Daily] 异常；动作指向 …`（`--tasks`） | 任务在，但动作指向的不是**当前目录**的 `checkin.bat`（目录改名/搬移后常见） | 跑一次 `checkin.bat` 会自动重注册；也可 `checkin.bat --install` |
| `[AICreditPunch-Resume] 缺失`（`--tasks`） | 唤醒触发任务不在（旧版本装的，或 `scheduled-task.resume.xml` 被删） | 跑 `checkin.bat --install`，它会读 XML 模板重新注册 |
| 计划任务触发时弹空的 cmd 黑框 | 旧版本动作直接是 `cmd.exe /c checkin.bat`（v1.8.0 及更早） | 跑一次 `checkin.bat --install`，动作会改为经 `run-hidden.vbs` 隐藏运行 |
| `The task XML is malformed. (1,40) 错误: 无法切换编码` | `scheduled-task.resume.xml` 里出现了 `<?xml?>` 声明 —— `Register-ScheduledTask -Xml` 收字符串时不允许 | 删掉模板首行声明，重新 `checkin.bat --install` |
| 睡眠 / 休眠唤醒后没有补签 | 事件触发器没匹配上；用现代待机（S0ix）的机器常记别的 ID | 按 §3.3 的命令看本机实际记哪个 ID，改 `scheduled-task.resume.xml` 的订阅后 `--install` |
| Trae 每次都提示 `今日已签到，本次无需签到`，平台上其实没签 | 判定 bug：status 返回的 `code=0 / message=success` 被误当成「已签到」（v1.7.0 及更早） | 升级本脚本；修好后日志会先出现 `提交签到` → `签到已受理，回查确认` → `签到成功` |
| 计划任务「上次结果 0x1」 | bat 执行失败 | 检查 `PYEXE` 路径、编码；手动跑一次 bat |
| 计划任务「上次结果 0x41301」 | 任务正在运行 | 正常，等待完成 |
| 日志中文乱码 | 代码页 / 编码问题 | ① 确认 `.bat` 里有 `PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8`；② `.bat` **自己写**的日志行必须纯 ASCII——v1.5.0 起已杜绝 `%date%`/`%time%`（中文字符页会输出「周一」这类本地化文本，落进 UTF-8 日志即乱码）。日志里出现 `\xd6\xdc\xd2\xbb` 字节就是这条问题复发 |
| 日志为空 | 重定向未生效或任务未跑 | 确认用 `.bat` 包装；`schtasks /query` 看「上次运行时间」 |

### 6.3 可调环境变量

脚本**实际会读**的变量只有下面这些：

| 变量 | 默认 | 说明 |
|---|---|---|
| `WORKBUDDY_TIMEOUT` | `20` | 单次请求超时秒数（夹在 5–120 之间） |
| `WORKBUDDY_RETRIES` | `2` | 重试次数（夹在 0–5 之间） |
| `WORKBUDDY_DEBUG` | `false` | 输出脱敏后的响应结构（等价于 `--debug`） |
| `WORKBUDDY_AUTH_FILE` | 空 | 仅供 `--init-workbuddy`：指定桌面端 auth 文件，多个用 `;` 分隔 |
| `AICREDIT_MAX_FAIL_ALERTS` | `3` | 失败通知每天上限条数 |
| `AICREDIT_FAIL_ALERT_INTERVAL` | `60` | 失败通知最小间隔（分钟） |

下面三个是**运行环境**变量，平时不用手动设，但知道它们有助于排错：

| 变量 | 作用 |
|---|---|
| `APPDATA` | 决定日志落点 `%APPDATA%\AICreditPunch.log` 与凭据扫描范围；缺失时依次回退 `$XDG_CONFIG_HOME` → 用户主目录 → 脚本同目录 |
| `LOCALAPPDATA` | `--init-workbuddy` 的扫描起点（`…\AppData\Local`）；缺失时回退到 `~/AppData/Local` |
| `ACP_LOG_OWNER` | `checkin.bat` 会设为 `bat`：让 `checkin.py` 只留标记文件，由 bat 把本次输出**置顶到日志之后**再打开记事本 |

> ⚠️ **已失效的变量（不要再用）**：`WORKBUDDY_NOTIFY`、`WORKBUDDY_CONFIG`、`WORKBUDDY_ACCOUNTS`、
> `WORKBUDDY_ACCESS_TOKEN`、`WORKBUDDY_WEBHOOK`、`WORKBUDDY_WECOM_*`。
> 这些都是上游脚本的能力，本仓库未实现，设置它们不会有任何效果。

---

## 7. 版本管理

### 7.1 仓库状态

- 远程：`origin` → `github.com/huxc573/AICreditPunch`（**公开仓库**），主分支 `main` 跟踪 `origin/main`。
- 许可：整仓 **MIT**（[LICENSE](LICENSE)），第三方声明见 [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)。
- **公开历史从 `v1.7.0` 起**：上游脚本文件在 `v1.1.0`–`v1.2.0` 期间已被删除，`v1.7.0` 又完成了
  WorkBuddy 段的独立重写；因此公开发布时只发布了 `v1.7.0` 这一个初始提交 ——
  公开历史中**不含**任何上游源码、发行包或移植实现。
- **完整开发历史留在本地**：`v1.0`–`v1.7` 的 40 个提交与全部旧标签保存在本地分支
  `archive/full-history`，**不推送**（仅作溯源，见 [§8](#8-来源声明与致谢)）。
- 推送前**务必**先 `git status --short` 确认无凭据文件（`.gitignore` 已拦住，别用 `git add -f` 绕过）。
- 推标签时**只推** `v1.7.0` 及以上，**不要**用 `git push --tags` ——
  本地仍留有指向旧提交的 `v1.0`–`v1.6` 标签，整批推送会把上游代码带回公开库。

```bash
git remote -v                                    # origin  https://github.com/huxc573/AICreditPunch.git
git branch -vv                                   # * main [origin/main]；archive/full-history 为本地溯源分支
git log --oneline archive/full-history | head    # 本地完整历史（40 个提交）
```

### 7.2 版本号规则（语义化版本 2.0.0）

格式 `MAJOR.MINOR.PATCH`：

| 段位 | 何时 +1 | 本项目示例 |
|---|---|---|
| `MAJOR` | 不兼容变更：配置结构破坏性调整、目录重构、运行方式改变 | `config.json` 改为必须的对象结构 |
| `MINOR` | 向后兼容的新功能 | 新增企业微信应用推送、新增 `--init-*` 命令 |
| `PATCH` | 向后兼容的修复 | 修 bat 编码乱码、改超时、安全加固 |

- **版本号的唯一来源是根目录 `VERSION`**（单行，如 `1.8.0`），脚本与文档都以它为准。
- Git 标签命名 `v<MAJOR>.<MINOR>.<PATCH>`，对外简称可写 **v1.8**。
- **迭代时间约定**：`CHANGELOG.md` 每个版本段标题除日期外必须带 **24 小时制、到分钟**的时间
  （格式 `YYYY-MM-DD HH:MM`），`README.md` 顶部版本号同样带该时间，便于精确回溯。

### 7.3 版本信息存放位置

| 文件 | 作用 | 是否入库 |
|---|---|---|
| `VERSION` | 当前版本号（唯一来源） | ✅ |
| `CHANGELOG.md` | 每个版本的 Added / Changed / Fixed / Security | ✅ |
| `README.md`（本文档 §7） | 版本管理规则 | ✅ |
| Git tag `vX.Y.Z` | 与提交一一对应的不可变锚点 | ✅（本地） |

### 7.4 日常变更流程

```bash
# 1. 改动（个人仓库，直接改 main 即可）
# 2. 确认没有凭据入库
git status --short      # 不应出现 config.json / *.log / .checkin_state.json / .workbuddy
# 3. 提交
git add -A
git commit -m "fix(bat): 修复开机时网络未就绪导致漏签"
# 4. 需要发版时：更新 VERSION → CHANGELOG 顶部追加 → 提交 → 打标签
git add VERSION CHANGELOG.md
git commit -m "chore(release): v1.8.0"
git tag -a v1.8.0 -m "1.8.0: 一句话概要"
```

> ✅ 本仓库已以 **MIT** 公开发布：[`huxc573/AICreditPunch`](https://github.com/huxc573/AICreditPunch)，
> 远程 `origin` 已配置。推送前先 `git status --short` 确认暂存区里没有凭据文件。
> 发布新版本后记得同步标签：`git push origin main --follow-tags`。

### 7.5 提交信息约定（Conventional Commits 精简版）

```
<type>(<scope>): <一句话描述>
```

| type | 含义 | 对应版本段位 |
|---|---|---|
| `feat` | 新功能 | MINOR |
| `fix` | 修 bug | PATCH |
| `perf` | 性能/重试策略优化 | PATCH |
| `refactor` | 重构，不改变外部行为 | PATCH |
| `docs` | 仅文档 | PATCH（或跳过发版） |
| `chore` | 构建/任务配置/依赖 | 视情况 |
| `security` | 安全加固（凭据、权限、脱敏） | PATCH |

示例：`feat(notify): 新增企业微信应用推送渠道`、`fix(bat): 修复开机时网络未就绪导致漏签`。

### 7.6 发版检查清单

- [ ] `python checkin.py --dry-run` 通过
- [ ] 手动跑一次 `python checkin.py`，日志出现 `今日已签到` 或 `签到成功`
- [ ] `git status --short` 中**没有**凭据或运行产物
- [ ] `VERSION` 已更新
- [ ] `CHANGELOG.md` 已追加新版本段落（含日期 + 24 小时制时间）
- [ ] `README.md` 顶部版本号同步
- [ ] 已提交并 `git tag -a vX.Y.Z`
- [ ] Windows 计划任务仍指向正确的 `checkin.bat`（改动过目录结构必须复核；
      也可直接跑一次 `checkin.bat`，它会自动纠正过期路径）

### 7.7 回滚

```bash
git log --oneline --decorate      # 找到目标 tag
git show v1.8.0                   # 查看该版本内容
git checkout v1.8.0 -- <文件>      # 只回滚单个文件
git checkout -b hotfix/x v1.8.0   # 从旧版本拉修复分支
```

回滚后记得同步修正 `VERSION` 与 `CHANGELOG.md`，避免版本号与实际代码不符。

---

## 8. 来源声明与致谢

### 8.1 本项目的构成与来源

本仓库**不含任何上游脚本文件**：上游文件在 v1.1.0–v1.2.0 期间被逐个删除，功能全部收敛到
自行维护的 `checkin.py` + `checkin.bat`。下表说明**与上游的关系**，以及哪些部分已改为独立实现：

| 模块 | 来源仓库 / 文件 | 与上游的关系 | 上游许可 |
|---|---|---|---|
| WorkBuddy 平台 | [`tianxing226/AICreditPunch`](https://github.com/tianxing226/AICreditPunch) · `workbuddy_checkin.py`（v2.2.0，2026-09-02 发布） | ✅ **已独立重写**：只保留公开接口事实 —— `POST /v2/billing/meter/checkin-activity-status`、`POST /v2/billing/meter/daily-checkin` 两条路径、Bearer 鉴权头与可选 `X-*` 头、`api_base` 域名白名单规则、桌面端 auth 快照的字段约定；函数划分、命名、错误处理均为本仓库自行设计。`v1.6.0` 及更早版本曾直接移植其实现 | ⚠️ **未附任何许可证** |
| Trae 平台 | [`xz0609/trae-work-checkin-ql`](https://github.com/xz0609/trae-work-checkin-ql) · `trae_work_checkin.py` | 上游 MIT 代码的衍生版本：登录 / 刷新 token / 签到 / 查积分四条链路，`checkin_credits/{status,claim}`、`ide_user_ent_usage`、`ExchangeToken`；`9074` 设备指纹重试 + 设备池轮换、`9095` 设备占用跳过 | ✅ **MIT** · `Copyright (c) 2026 Yiran` |
| Trae 登录流程（参考） | [`Maquer/trae-signin`](https://github.com/Maquer/trae-signin) | 仅参考其 `login.sh` 的 OAuth 流程：生成授权链接 → 本地端口收 `refreshToken` / `userInfo` / `userJwt` → `ExchangeToken` 换 accessToken。**未复制其 Go / shell 代码** | ✅ **MIT** · `Copyright (c) 2026` |

> 上游链再往前一层：`xz0609/trae-work-checkin-ql` 在其 README 致谢里写明 Fork 自
> [`yang89520/auto-checkin-hub`](https://github.com/yang89520/auto-checkin-hub)（同项目也覆盖
> Trae Work + CodeBuddy/WorkBuddy），该仓库同为 **MIT**。整条 Trae 链路是干净的。

> ⚠️ **"独立重写"的口径要说清楚**：严格意义的 clean-room（一人只写规格、另一人只看规格写代码）
> 在这里做不到 —— 重写者此前已读过上游实现。所依据的是**接口行为**（路径、请求头、字段名、
> 状态码语义），这类协议事实不受著作权保护；代码结构、命名与错误处理已全部重做。
> 因此这**不是**教科书式的干净房实现，但已不再是上游表达形式的复制。
> 若要彻底消除争议，最省事的仍是**顺手找上游作者补一句授权**（见 §8.2）。

**本仓库自有的部分**（不来自任何上游）：

- 两平台合一的编排、幂等与退出码语义（`orchestrate`）
- 通知决策与失败限流（当日首次推一次；失败按每天 ≤3 条 / 间隔 ≥60 分钟限流）
- Windows 计划任务的注册、卸载、路径自纠正与"错过补跑"
- 只读查看命令 `--today` / `--tasks`，日志"最新在前"、首次签到弹记事本
- `checkin.bat` 入口（`--` 子命令、参数转发、`--logs`）与全部文档

### 8.2 许可（License）

**结论：许可障碍已解除，整仓已以 MIT 公开发布（`v1.7.0`）。** 依据与已完成的动作如下：

| 部分 | 结论 | 依据 |
|---|---|---|
| 本仓库自有代码（编排 / 通知 / 计划任务 / `--today` / `--tasks` / 文档） | ✅ 已 MIT | 完全自有 |
| WorkBuddy 段 | ✅ 已纳入 MIT | 已按接口行为**独立重写**；协议事实（URL、请求头名、字段名、状态码）不受著作权保护 |
| Trae 段 | ✅ 已随 MIT 发布，**保留上游声明** | 上游 `xz0609/trae-work-checkin-ql` 与 `yang89520/auto-checkin-hub` 均为 **MIT**，MIT 允许再分发与改许可，但**必须保留版权与许可声明**；已写入 `THIRD-PARTY-NOTICES.md` |

开源动作清单（**均已完成**）：

1. ✅ `LICENSE`：MIT 全文，版权人 `huxc573`，年份 2026；
2. ✅ `THIRD-PARTY-NOTICES.md`：保留 `Copyright (c) 2026 Yiran` 与 `Copyright (c) 2026`
   两条上游 MIT 版权行及 MIT 全文，并逐条注明其对应的 Trae 链路；
3. ✅ 公开仓库 [`huxc573/AICreditPunch`](https://github.com/huxc573/AICreditPunch) 已创建，
   远程 `origin` 已接回并推送；§5.5 / §7.1 / §7.4 里"没有远程 / 不要 push"的表述已同步改掉。

> 💡 **保险动作（建议顺手做）**：即使已独立重写，给上游作者留一句沟通成本几乎为零 ——
> 提个 Issue 或发封邮件说明"WorkBuddy 段已独立重写并以 MIT 发布，仍保留来源声明"，
> 对方回复一句"没问题"就能把最后一点争议也消掉。这不是必须项。
>
> 无论是否发布，**本文档顶部与 §8.1 的来源表格都要保留**。

### 8.3 免责声明

- 本项目是**第三方逆向脚本**，与 WorkBuddy / Trae 官方无关，可能违反相关产品的服务条款；
  接口随时可能变更或失效，**仅供个人学习研究，请自行评估风险后使用**。
- Trae 平台限制"**同一账号每天只能在登录它的那台设备上领取**"，换设备或放云服务器报 `9074` 属预期。
- 请勿把 `config.json`（含 `access_token` / `refreshToken` / 推送密钥）提交或上传到任何公开位置。
