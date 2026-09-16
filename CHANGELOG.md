# 变更日志（Changelog）

本项目所有版本记录遵循 [语义化版本 2.0.0](https://semver.org/lang/zh-CN/)：`MAJOR.MINOR.PATCH`。
版本号唯一来源为根目录 `VERSION` 文件，Git 标签为 `v<MAJOR>.<MINOR>.<PATCH>`。
发布流程与版本规则见 [README.md](README.md) §7。

---

## [未发布] - 2026-09-16 08:15

### 新增

- **计划任务隐藏窗口运行（去掉触发时的空 cmd 黑框）**：三个任务的动作由
  `cmd.exe /c "...\checkin.bat"` 改为 `wscript.exe "...\run-hidden.vbs" "...\checkin.bat"`。
  交互式令牌下旧动作每次触发都会在屏幕上弹一个空 cmd 窗口（用户反馈）；`wscript.exe` 是
  GUI 子系统宿主（自身无控制台），vbs 以窗口样式 0 启动 cmd，整条链路不再出现任何窗口。
  vbs **等待 bat 结束并透传退出码**（`WScript.Quit rc`），「上次运行时间 / 上次结果」与
  10 分钟执行时限不受影响；`--install` 重新注册即生效，手动运行 `checkin.bat` 仍照常显示输出。
- 新增 `run-hidden.vbs`（纯 ASCII + CRLF，与 `checkin.bat` 同约束，勿改编码）。

### 验证

- `--install` 后 `--tasks` 汇总 `3/3 正常`，动作校验仍通过（参数里含 `checkin.bat` 全路径）；
  任务侧「要运行的任务」显示 `wscript.exe "...\run-hidden.vbs" "...\checkin.bat"`。
- `schtasks /run /tn AICreditPunch-Resume` 触发 → 日志新增完整签到块（08:12:30，
  两平台「今日已签到，无需签到」，当日已签故静默跳过通知），链路 `wscript → vbs → cmd → bat` 全通。
- `py_compile` + AST 未定义名扫描干净；`checkin.bat` / `run-hidden.vbs` 纯 ASCII + CRLF。

## [v1.8.0] - 2026-09-15 09:55

按 `MINOR` 发版：本次含一处**严重缺陷修复**（Trae 判定误判，签到请求实际从未发出）、
两处积分口径修正（WorkBuddy 改用真实可用余额、Trae 本次积分改为实测），
并新增**睡眠 / 休眠恢复自动补签**任务。

### 修复

- **Trae「今日已签到」误判（严重，签到实际从未发出）**：`_trae_already()` 末尾用业务码兜底
  （`_code_of(payload) in (0, 200)` → 已签到），但 status 在**未签到**时同样返回
  `code=0` / `message=success`，于是每次运行都判定「今日已签到」，`提交签到` 一次都没执行过。
  实测报文：`{"checked_in": false, "code": 0, "credits": 150, "did_checked_in": false,
  "enable": true, "extra_credits": 50, "message": "success"}`。
  现在拆成 `_trae_status_checked()`（只认明确标记 `checked_in` 等与明确文案，**不看业务码**）
  与 `_trae_claim_ok()`（claim 的 `code=0/200` 才算领奖成功），字段读取统一走 `_trae_flag()`。
- **领奖后回查确认**（与 WorkBuddy 同口径）：`提交签到` → `签到已受理，回查确认`
  （按 `TRAE_VERIFY_WAITS = (0, 4)` 回查两次）→ 确认到账才写 `签到成功`；
  回查仍未确认则记 `回查未确认签到` 并进入重试，不再出现「日志绿、平台没签」的假成功。
- **Trae「本次 +N」虚报**：原按平台声明的 `credits + extra_credits` 相加，实测**多报 50**
  （声明 `150 + 50 = 200`，余额却只涨 `150`）。改用**签到前后余额差实测**
  （`_trae_gain()`，领取前先查一次余额）；实测不到时只回退到 `credits`（`_trae_declared_credit()`），
  **不再与 `extra_credits` 相加** —— 该字段并未形成权益包（`ide_user_ent_usage` 里只有一笔
  `credits_limit=150` 的「签到奖励」），实际从未到账。
- **WorkBuddy「当前积分余额」口径错误**：原取签到报文的 `total_credits` 当余额，
  但它其实是**活动期内累计获得**（= 每日额度 × 活动期内签到天数）。实测每日 100、
  签到 2 天 → `200`，而账号真实余额是 `2,246.68`，**差一个数量级**。
  现在改由资源包接口取真实可用积分（见「新增」），`total_credits` 不再当余额显示。
- **`--status-only` 不再把查询失败说成待签到**：status 请求失败时返回 `状态查询失败`。

### 新增

- **WorkBuddy 真实可用积分与构成查询**（与桌面端「设置 - 套餐与积分」同源、同一套 Bearer 凭据）：
  · `POST /billing/meter/get-user-resource-summary` → 各资源包周期总额 / 剩余；
  · `POST /billing/meter/get-user-resource-free-packages` → 赠送包明细；
  · `POST /billing/meter/get-user-resource-paid-packages` → 付费包明细。
  于是日志多出「真实余额」并附一行构成：
  `今日已签到，本次无需签到；本次 +100，连续 2 天，当前积分余额 2,242.18` +
  `积分构成；套餐基础 142.18，平台奖励 2,100，购买积分 0`（与桌面端三行对齐）。
  总剩余 = 汇总接口各包 `CycleRemainCapacity` 之和；平台奖励 = 赠送包
  （`SubProductCode` 含 `bonus_pack`）剩余之和；购买积分 = 付费包剩余之和；
  套餐基础 = 总剩余 − 平台奖励 − 购买。**这三条路由不带 `/v2` 前缀**，与签到那两条不同。
- **唤醒补签任务 `AICreditPunch-Resume`**：订阅 `Microsoft-Windows-Kernel-Power` **事件 ID 107**
  （睡眠 / 休眠恢复），恢复后 15s 触发完整签到（`Delay=PT15S`）；另把
  `Microsoft-Windows-Power-Troubleshooter` 事件 ID 1 一起 `OR` 进订阅以兼容其它机器
  （本机不记该事件，匹配不到也无害）。以当前用户 + 交互式令牌运行（**不存密码**），
  `MultipleInstancesPolicy=IgnoreNew`、`ExecutionTimeLimit=PT10M`、`RunLevel=Limited`。
- **`scheduled-task.resume.xml`**：该任务的 XML 模板 —— `New-ScheduledTaskTrigger` 无法表达
  事件触发器，只能走 XML 注册；占位符 `__CHECKIN_BAT__` / `__USER__` 由 `checkin.bat` 替换。
- `checkin.py` 的 `TASK_RESUME` 常量、`--tasks`、`--today` 与 `checkin.bat` 的
  `:register_tasks` / `:tasks_ok` / `--uninstall` / 帮助文案全部覆盖第三个任务。

### 验证

- Trae 真机（09:09）：`checked_in=false` → `提交签到` → 回查 `checked_in=true` →
  `签到成功；本次 +200，当前积分余额 184`；修复前 `.checkin_state.json` 里的
  `success_date_trae` 是**假**成功记录，现已名副其实。
- 积分口径真机（09:32 / 09:33）：Trae 由「本次 +200」改为「本次 +150」
  （余额 34 → 184，与 `credits=150` 及权益包 `credits_limit=150` 三方吻合）；
  WorkBuddy 余额由 `200` 改为 `2,242.18`，构成 `套餐基础 142.18 + 平台奖励 2,100 + 购买积分 0`，
  与桌面端「套餐与积分」页的 `2,249.66 = 149.66 + 2,100 + 0` 逐项对齐（差额为期间正常消耗）。
- 完整运行 / `--status-only` / `--dry-run` / `--today` / `--tasks` 全部回归通过；
  余额接口失败只少打一行，不影响签到结果。
- 计划任务：`checkin.bat --install` 注册三个任务，`--tasks` 汇总 `3/3 正常`；
  `schtasks /run /tn "AICreditPunch-Resume"` 按需触发一次，日志新增一整块完整签到。
- 真实唤醒验证待做：睡眠 → 唤醒后看 `--tasks` 的「上次运行」是否更新。

### 已知与限制

- `Register-ScheduledTask -Xml` **不接受带 `<?xml?>` 声明的字符串**
  （`The task XML is malformed. (1,40) 错误: 无法切换编码`），模板已去掉声明并在文件头注明原因。
- 本机 System 日志只有 `Kernel-Power` 107（无 `Power-Troubleshooter`）；**现代待机（S0ix）**
  的机器可能记其它 ID，换机器按 README §3.3 的命令先确认再改订阅。
- 积分构成里的「平台奖励 / 购买」靠 `SubProductCode` 标记与付费包接口区分，
  「套餐基础」由总剩余相减得出；若后端改了包标记，构成行会在口径对不上时**自动省略**，
  只保留总剩余（宁可少一行也不报错数）。

## [v1.7.0] - 2026-09-14 23:07

`checkin.py` 的 WorkBuddy 段（约 400 行）**按接口行为独立重写**，不再包含上游代码 ——
配合开源方案 B（见 README §8.2）。日志文案、退出码、`config.json` 结构**均未改变**。

### 重构

- **签到链路改为客户端对象**：`WorkBuddyClient`（一个账号的一次会话）+ `WbReply`
  （把 HTTP 码与报文收敛成 `accepted` / `already_checked` / `credits` / `reason`）。
  原先散落的字段探测（`_wb_business_ok` / `_wb_already` / `_wb_extract_credit` /
  `_wb_credit_text`）集中进 `WbReply`，判定规则只在一处。
- **凭据导入重写**：`WbAuthSnapshot`（单个 `workbuddy-desktop*.info` 快照）+
  `WbAccountStore`（`accounts` 段的合并与判重）取代原来的
  `_wb_auth_directories` / `_wb_auth_files` / `_wb_parse_auth_file` / `_wb_record_*` /
  `_wb_candidate_record` / `_wb_merge_setup` 一整套；目录发现走 `wb_auth_dirs()` /
  `wb_auth_snapshots()`，地址校验走 `wb_validated_base()`。
- **共用部分上移公共区**：文案构造 `_fmt_credit` / `_line` / `_result` 与响应解读
  `_code_of` / `_msg_of` / `_business_ok`（Trae 段也在用），从 WorkBuddy 段提到公共工具区。
- 代码里不再有 `_wb_` 前缀，注释里的"移植自上游"改为"独立实现"并记明协议依据。

### 修复

- **`api_base` 非法不再拖垮整次运行**：配置写错域名（或写成 `http://`）时原来会抛
  `ValueError` 直接中断全部签到（Trae 也被一起跳过）；现在只跳过该账号并记一行
  `跳过；WorkBuddy API 地址校验失败: ...`，其余账号照常执行。
- **重新初始化不再清空手填的 `enterprise_id`**：快照里没有企业信息时，保留
  `config.json` 中已有的值（原先会被空串覆盖）。

### 验证

- 真机：`--status-only`、完整签到、`--init-workbuddy`（幂等：更新 1 / 新增 0 / 保留 1）、
  `--dry-run`、`--today`、`--tasks`、`checkin.bat` 四条路径全部通过，
  日志文案与 v1.6.0 逐字一致；日志 UTF-8 坏行 0。
- `api_base` 白名单边界：`evil.example.com`、`http://`、`codebuddy.cn.evil.com`、
  `xcodebuddy.cn`、`ftp://` 全部拒绝；`*.codebuddy.cn` / `*.workbuddy.cn` /
  `copilot.tencent.com` 通过，路径尾斜杠已归一。
- 快照解析六种情况：正常 / `lastLogin` 回退 / 毫秒时间戳 / 坏 JSON / 缺 `auth` / 缺 token。
- 合并逻辑：uid 与 token 两条判重路径、自定义字段保留、重复导入幂等。

### 文档

- README 顶部与 §8 同步：WorkBuddy 段标注为「**已独立重写**」；§8.2 从"四种可选方案"
  改为**发布前置动作清单**（补 `LICENSE` + `THIRD-PARTY-NOTICES` + 接回远程），
  并写明"独立重写"的口径边界 —— 非严格 clean-room（重写者此前已读过上游实现），
  但代码结构、命名与错误处理已全部重做，保留的只是不受著作权保护的接口协议事实。

### 许可

- **整仓以 MIT 公开发布**：新增根目录 [`LICENSE`](LICENSE)（MIT，版权人 `huxc573`，2026），
  并新增 [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md)：保留 Trae 链路两个上游
  （`xz0609/trae-work-checkin-ql` · `Copyright (c) 2026 Yiran`、`Maquer/trae-signin` ·
  `Copyright (c) 2026`）的 MIT 版权行与许可全文；对 WorkBuddy 上游
  （`tianxing226/AICreditPunch`，**未附任何许可证**）写明 `v1.7.0` 起已独立重写及其口径边界。
- **接回公开远程**：`origin` → [`huxc573/AICreditPunch`](https://github.com/huxc573/AICreditPunch)；
  README §5.5 / §7.1 / §7.4 里"没有远程 / 不要 `git push`"的表述一并改掉。

---

## [v1.6.0] - 2026-09-14 22:51

`checkin.bat` 子命令统一为 `--` 前缀并支持参数转发；新增两个只读命令 `--tasks`（计划任务检查）
与 `--today`（日常查看）；计划任务注册失败时自动用记事本打开日志。

### 新增

- **`--today`（日常查看）**：纯本地只读汇总，不联网、不改任何文件。等价入口 `checkin.bat --today`。
  - 今日各平台签到状态（读 `.checkin_state.json`）、当天失败告警条数
  - 日志里今天出现过几次运行、主任务的下次触发时间、日志文件路径
  - 直接把日志最顶端（即最新一次运行）那一块贴出来，最多 40 行
  - 退出码：`0` = 今日各平台都已签到；`1` = 有未签到 / 计划任务缺失等待办项
- **`--tasks`（计划任务检查）**：核对两个计划任务是否注册、是否指向**当前目录**的 `checkin.bat`，
  并显示「下次运行 / 上次运行 / 上次结果」。缺失或指向别处时直接给出修复命令，退出码 `1`。
  实现走 PowerShell `Get-ScheduledTask`，把字段名换成英文再解析，避开
  `schtasks /fo LIST /v` 那种「字段名随系统语言变化」的坑；PowerShell 不可用时退化为「只判存在」。
- **`checkin.bat --logs`**：随时用系统记事本打开本地日志；文件还不存在时给出提示并返回退出码 `1`。
  这是 `checkin.bat` 自己实现的本地命令，**不转发**给 `checkin.py`。

### 变更

- **`checkin.bat` 子命令统一 `--` 前缀**：`--install` / `--uninstall` / `--logs` / `--help`。
  **裸写法已移除**：`install` / `uninstall` / `logs` 一律拒绝，提示 `Unknown argument: xxx`，退出码 `2`。
  唯一例外是帮助类 —— `--help` / `-h` / `/?` / 裸 `help` 都能打印用法。
- **`checkin.py` 移除旧的位置参数写法**：`setup-workbuddy` / `setup-trae` / `login` 不再被识别，
  初始化只剩 `--init-workbuddy` / `--init-trae` / `--init`（传入旧写法由 argparse 直接报错，退出码 `2`）。
- **`checkin.bat` 支持参数转发**：其它任何 `--xxx` 原样传给 `checkin.py`
  （如 `checkin.bat --status-only`、`--init-trae`、`--version`），输出直接打到控制台、不写日志。
- **`checkin.bat --install` 失败时退出码改为 `1`**（成功仍为 `0`），方便脚本判定。

### 修复

- **弹记事本的时机**：`checkin.py` 不再自己立刻打开日志，改为写标记文件
  （`%APPDATA%\.AICreditPunch.openlog`，仅当环境变量 `ACP_LOG_OWNER=bat` 时），
  由 `checkin.bat` 在把本次输出**前置到日志顶端之后**再打开 ——
  之前弹出的记事本看到的其实是本次运行**之前**的旧内容（首次签到场景）。
- **计划任务注册失败会自动弹记事本**：失败时日志里留两条 `WARN`，运行结束即打开日志展示原因。
- **`checkin.bat --install` 的日志块补一个空行**，避免与上一次运行的块黏连
  （否则 `--today` 的「最近一次运行」会把两块当成一块显示）。
- **「从未运行」不再显示 `1999-11-30 00:00`**：`LastRunTime` 的哨兵值按空处理，
  只保留「上次结果 从未运行（0x41303）」。
- **打开日志不再拖住调用方**：改用 `Start-Process` 拉起记事本（独立进程）。
  原先的 `start "" notepad` 会让记事本**继承本脚本的 stdout/stderr**，
  任何用管道或重定向调用 `checkin.bat` 的场景（包括计划任务）都要等用户关掉记事本才等到 EOF。

### 文档

- **README 新增 §8「来源声明与致谢」**：逐条列明两个上游脚本的**来源仓库、移植范围与许可状态**，
  并给出开源发布前的许可处置方案（WorkBuddy 上游 `tianxing226/AICreditPunch` **未附任何许可证**，
  默认保留所有权利，不能直接整仓挂 MIT 发布）。
- **更正 README 中与实现不符的说明**（多为早期从上游文档沿袭、移植后未同步的部分）：
  - 删掉"支持 `WORKBUDDY_ACCOUNTS` / `WORKBUDDY_ACCESS_TOKEN` / `WORKBUDDY_CONFIG` 环境变量注入"
    的说法 —— 新版**只从脚本同目录 `config.json` 读配置**（§5.1 重写、§6.3 重写）。
  - 删掉"通知可用 `WORKBUDDY_WEBHOOK` / `WORKBUDDY_WECOM_*` 环境变量临时覆盖"的说法 —— 未实现（§4.4）。
  - `WORKBUDDY_NOTIFY` 改为历史说明：上游开关已随上游脚本删除，本脚本**不再读取**（§4 开头）。
  - `--init-workbuddy` 的扫描路径按实现更正：匹配 `workbuddy-desktop*.info` 通配，
    无 `%LOCALAPPDATA%` 时回退 `~/AppData/Local`（原文写的 `$XDG_DATA_HOME` 并不存在）。
  - Trae 登录回调地址更正为"从 18080 起在 **18080–18099** 自动挑空闲端口"，不是固定 18080（§2.3）。
  - `--today` / `--tasks` 的示例与说明对齐真实输出（平台级通知行不带账号名、`异常` 状态、
    卸载的 `[skip]` 行、`%APPDATA%` 展开后的真实路径）。
  - 排错表更正"日志中文乱码"的成因：bat 自写行里的本地化 `%date%` / `%time%`（v1.5.0 已根治），
    并给出 `\xd6\xdc\xd2\xbb` 字节这一判别依据。
  - §5.4 的 `.gitignore` 清单与真实文件对齐（补齐编辑器 / Python / 系统忽略项）。
- **修正历史记述**：v1.0 / v1.1.0 段里的入口脚本当时名为 `run_checkin.bat`（v1.5.0 起改名）；
  v1.0 段的"开源脚本"改为"上游脚本"，并补上上游仓库地址与许可状态。
- **README 顶部**改为直接给出两个来源仓库及其许可状态（其中 WorkBuddy 上游缺许可证已醒目标注）。

---

## [v1.5.0] - 2026-09-14 21:47

`checkin.bat`（原 `run_checkin.bat`）增加卸载能力，计划任务注册改走 PowerShell（无需管理员 + 错过补跑）；
README 合并部署手册与版本规则，删除 `docs/`，全量脱敏；修复 bat 自写日志的乱码。

### 新增

- **`checkin.bat` 支持三个动作**：
  - `checkin.bat` —— 跑签到；发现任务缺失或**指向旧路径**时自动重注册
  - `checkin.bat install` —— 强制重装两个计划任务
  - `checkin.bat uninstall` —— 卸载两个计划任务（不动脚本、配置与日志，重跑无参即恢复）
  - `checkin.bat help` —— 用法说明
- **计划任务注册改用 PowerShell `Register-ScheduledTask`**：普通账户权限即可完成，
  且能设置 `StartWhenAvailable`（错过触发后尽快补跑）。原方案用 `schtasks /sc onlogon`
  建登录任务需要管理员权限，实际上会静默失败。
- **目录改名/搬移免疫**：bat 用 `%~dp0` 解析自身位置（不再硬编码目录），
  并按「任务是否存在 + 动作是否指向当前目录」判断是否需要重注册。

### 变更

- **收尾空行改为纯换行**：`log("")` 不再输出时间戳前缀，运行块末尾留白干净。
- **bat 自写日志改用 ASCII 时间戳**：原 `%date% %time%` 在中文字符页下写成
  `2026/09/14 周一 ...`，在 UTF-8 日志里显示为乱码；现改用
  `Get-Date -Format s`（ISO 格式 `2026-09-14T21:44:12`，转换后与 Python 侧一致）。
- **README 成为唯一文档**：`docs/DEPLOY.md`（依赖 / 配置 / 定时任务 / 测试 / 排错 / 通知）
  与 `docs/VERSIONING.md`（版本规则 / 流程 / 清单 / 回滚）的有效内容全部并入 `README.md`，
  `docs/` 目录删除。
- **文档全量脱敏**：账号显示名统一为 `示例账号`，本机用户名与主机名、用户目录绝对路径、
  设备号、上游仓库地址全部替换为占位符；示例输出明确标注为不含真实凭据。

### 修复

- **bat 语法错误导致脚本完全不执行**：`if errorlevel 1 ( ... )` 块内的 `echo` 文本里带了圆括号，
  被 cmd 当成块结束符，报「此时不应有 .」并以 255 退出（触发路径：计划任务注册）。
- **`install` 模式日志未落盘**：发布逻辑抽为 `:publish_block` 子程序，默认路径与 `install` 共用。
- **项目目录改名后任务失效**：两个计划任务仍指向 `tianxing226_AICreditPunch` 旧路径，
  已在本机自动重注册修正。

### 已验证

- `checkin.bat` 各模式实跑：默认（自动注册 + 签到，RC 0）、`install`、`uninstall`
  （两个任务均删除）、`help` 均符合预期。
- 任务 XML 复核：`AICreditPunch-Daily` 6 个日历触发器（08:45 / 11:45 / 14:45 / 17:45 / 20:45 / 23:45）、
  `StartWhenAvailable=true`、动作指向当前目录；`AICreditPunch-Startup` 1 个登录触发器、路径一致。
- 日志：本次运行块位于文件最顶端；结束行后为纯空行；bat 自写行均为 ASCII 时间戳。
- `.bat` 纯 ASCII + CRLF（219 行全 CRLF）；`py_compile` 通过；`git status` 无凭据文件。

### 修正（v1.5.0 内，不单独发版）

- **入口 bat 改名 `run_checkin.bat` → `checkin.bat`**（与 `checkin.py` 成对，命令更直观）。
  同步范围：任务注册脚本、任务路径校验（`findstr` 匹配串）、`help` 文本、README / CHANGELOG 全文。
  本机两个计划任务已自动重注册到新文件名（bat 每次运行会校验「任务是否存在 + 动作是否指向
  当前目录与当前文件名」，不符即重建）。
- **日志乱码根治**：`:now` 取时间失败的兜底分支原先退回 `%date% %time%`，在中文代码页下写出
  `2026/09/14 周一 21:44:12.28`，落入 UTF-8 日志即乱码（21:44 那次运行实测触发）。现在三层防护：
  1. 取时间改用 `Get-Date -Format s`（ISO 可排序格式，**无需内层引号**，减少 `for /f` 解析风险）；
  2. 取不到时自动重试一次；
  3. 仍取不到则**整行不打时间戳**（bat 自写行统一走新增的 `:say` 子程序），
     彻底杜绝任何本地化文本进入 UTF-8 日志。
  已用「把取时间命令改成不存在程序」的副本实测：输出为无时间戳的纯 ASCII 行，日志无坏字节。

---

## [v1.4.0] - 2026-09-14 20:42

运行输出加上起止标记并去掉重复平台名；日志迁到 `%APPDATA%`，最新在前，当日首次签到自动弹记事本。

### 新增

- **运行起止标记**：每次运行首行打印 `一体化每日签到脚本 vX.Y.Z 启动`，末行打印
  `本次脚本执行完毕。`（成功、失败、配置错误、dry-run、初始化等所有路径都会收尾）。
- **日志迁至 `%APPDATA%\AICreditPunch.log`**（`log_file_path()`/`LOG_FILE`）：
  - **直接放 `%APPDATA%` 根下，不建子文件夹**，打开 Roaming 就能看到。
  - `%APPDATA%` 不可用时回退 `$XDG_CONFIG_HOME` → 用户主目录 → 脚本同目录。
- **`checkin.bat` 日志前置写入**：本次输出先写临时文件，再与旧日志做二进制前置拼接
  （`copy /b 新 + 旧` 后 `move` 覆盖），因此**最新一次运行的日志块永远在文件最顶端**；
  改动是全量覆盖，不再用 `>>` 追加。
- **当日首次签到自动打开日志**：`orchestrate()` 改为返回 `(退出码, 是否当日首次成功推送)`，
  主流程汇总各平台后，若本次是当日首次 → `open_log_in_notepad()` 用系统默认程序
  （Windows 记事本）打开日志文件；同日后续运行静默不弹。

### 变更

- **块内行不再重复平台名**：块标题为 `===== WorkBuddy =====` / `===== Trae =====` 已表明平台，
  故行内只保留账号与状态，统一为 `[账号名] 状态；明细`，输出左右对齐更整齐。
  典型输出：
  ```text
  一体化每日签到脚本 v1.4.0 启动
  ===== WorkBuddy =====
  [示例账号] 查询签到状态；API=https://www.codebuddy.cn
  [示例账号] 今日已签到，本次无需签到；本次 +100，连续 1 天，当前积分余额 100
  ===== Trae =====
  [示例账号] 查询签到状态；device=xxxxxxxx***
  [示例账号] 今日已签到，本次无需签到；本次 +200，当前积分余额 34
  本次脚本执行完毕。
  ```
- 仓库内旧 `checkin.log` 不再写入（仍保留在 `.gitignore` 中以防误加）。

### 已验证

- `--status-only` / 默认执行：起止行、块内对齐、末尾总结行均正确。
- 真实 `checkin.bat` 端到端两次：日志落到
  `%APPDATA%\AICreditPunch.log`；第二次运行后文件**首行即本次启动行**
  （前置拼接生效）；`notepad.exe` 进程确认被拉起（首次签到路径），并已关闭测试窗口。
- `.bat` 仍为纯 ASCII + CRLF。

### 修正（同日小修，不单独发版）

- 日志路径去掉 `\AICreditPunch\` 子目录 → 直接 `%APPDATA%\AICreditPunch.log`。
- 移除启动时 `AICREDITPUNCH_LOG_FILE=<路径>` 输出行（不再需要）。
- 去掉块内行重复的平台名（`_line()` 首个 `platform` 参数移除，共 23 处调用点同步）。
- 「本次脚本执行完毕。」后追加一个空行，与下一次运行的日志块之间留白，便于区分。
- **`checkin.bat` 自动注册计划任务**：本机不存在 `AICreditPunch-Daily` 时，
  自动按既有配置注册 `AICreditPunch-Daily`（08:45/11:45/14:45/17:45/20:45/23:45）
  与 `AICreditPunch-Startup`（登录触发），`/f` 幂等、结果写入日志；已存在则完全跳过。
  用于「换台电脑拷过去跑一次 bat 即可接上定时」，注册失败只记 WARN 不影响签到。
- **未初始化时打印完整指引**：新增 `_print_init_hint()`，在「无 config.json」与
  「账号为空」两条路径下统一输出三条 `--init-*` 命令及说明；单平台缺失时的提示也改为
  `` `python checkin.py --init-workbuddy`（导入本机登录凭据） `` 这种带用途说明的写法。

---

## [v1.3.0] - 2026-09-14 20:25

初始化命令改为与其它参数同风格的 `--init-平台` flag；WorkBuddy 与 Trae 的运行输出文案彻底统一。

### 变更

- **初始化命令改为 `--init-*` flag**（与 `--workbuddy-only` / `--trae-only` / `--status-only` 用法一致）：
  | 平台 | v1.2.0 写法 | v1.3.0 写法 |
  |---|---|---|
  | WorkBuddy | `python checkin.py setup-workbuddy` | `python checkin.py --init-workbuddy` |
  | Trae | `python checkin.py setup-trae`（别名 `login`） | `python checkin.py --init-trae` |
  | 两个平台 | — | `python checkin.py --init`（依次执行） |
  - 旧的 positional 写法（`setup-workbuddy` / `setup-trae` / `login`）仍被兼容识别，不会报错。
  - `--auth-file` 依旧配合 `--init-workbuddy` 使用；`--help` 的 epilog 会提示三条初始化命令。
- **输出文案统一**：新增 `_line()`（日志）与 `_result()`（通知）两个统一构造函数，
  两平台的每一行都是同一结构：
  - 日志：`[账号名] 平台 状态；明细`
    （如 `[示例账号] Trae 今日已签到，本次无需签到；本次 +200，当前积分余额 34`，
    与 WorkBuddy 行逐字对齐）
  - 通知/汇总：`账号名（平台）：状态；明细`
    （如 `示例账号（Trae）：签到成功；本次 +200，当前积分余额 234`）
  - 平台名与初始化命令收敛为常量 `PLATFORM_WORKBUDDY` / `PLATFORM_TRAE` /
    `INIT_CMD_WORKBUDDY` / `INIT_CMD_TRAE`，提示文案不再散落硬编码。
  - 未初始化、token 刷新、凭证回写、Dry-run、通知标题等副文案也一并统一了口径；
    通知正文由 `; ` 拼接改为换行，多账号时更易读。

### 已验证

- `checkin.py --help`：新增 `--init` / `--init-workbuddy` / `--init-trae`，positional 参数隐藏。
- 未初始化场景（临时目录空配置）：分别提示 `--init-workbuddy` / `--init-trae`；未知命令 `foo` 返回 2 并列出可用 flag。
- 真实目录 `--status-only` 与默认执行：两平台输出结构完全一致，幂等跳过未重复领取。

---

## [v1.2.0] - 2026-09-14 19:55

上游脚本完成历史使命：`--setup`（WorkBuddy 凭据导入）并入 `checkin.py`，初始化命令规范化，未初始化时给出明确指引。

### 新增

- **`python checkin.py setup-workbuddy`** —— 移植上游 `--setup`：自动扫描本机
  WorkBuddy 桌面端认证目录（`CodeBuddyExtension` / `WorkBuddy` 的 `Data/Public/auth`），
  解析 `workbuddy-desktop*.info` 并按 uid 合并进 `config.json` 的 `accounts` 段；
  支持 `--auth-file` 与环境变量 `WORKBUDDY_AUTH_FILE` 指定凭据文件。
  写回只替换 `accounts`，`notify` / `trae_accounts` / `_` 注释字段完整保留（已实测结构对比一致）。
- **未初始化提示**：`python checkin.py` 在某平台未配置时，明确提示执行
  `python checkin.py setup-workbuddy` / `python checkin.py setup-trae` 完成初始化（退出码 1）。

### 变更

- **初始化命令规范化**（原来分散在两个脚本、命名不一致）：
  | 平台 | 旧命令 | 新命令 |
  |---|---|---|
  | WorkBuddy | `python workbuddy_checkin.py --setup` | `python checkin.py setup-workbuddy` |
  | Trae | `python checkin.py login` | `python checkin.py setup-trae`（`login` 仍为别名） |
- **删除 `workbuddy_checkin.py`**：上游脚本（v2.2.0）的签到接口与 `--setup` 均已并入
  `checkin.py`，项目收敛为**唯一脚本** + `checkin.bat`；迁移到云端只需
  `checkin.py` + `config.json` 两个文件。

### 已验证

- `setup-workbuddy` 真实执行：发现 1 个账号、更新 1 个；写前后 `config.json` 顶层结构、
  `notify` / `trae_accounts` / `_readme` 完全一致。
- 导入的新 token 实跑 `--workbuddy-only` 签到成功；全量 `python checkin.py` 双平台正常。
- 未初始化模拟（空配置注入）：提示文案与退出码符合预期。
- Windows 计划任务复核（安全中心放行 `schtasks.exe` 后）：`AICreditPunch-Daily` 6 个触发器
  全部启用，上次运行 17:45 结果 0；`AICreditPunch-Startup` 登录触发，上次运行 14:48 结果 0。

---

## [v1.1.0] - 2026-09-14 17:55

新增 Trae Work 签到平台，并将 WorkBuddy / Trae / 通知 / 去重限流合并为**单一零依赖脚本** `checkin.py`，便于整体迁移到云端或青龙面板。

### 新增

- **`checkin.py`（单文件、纯标准库）** —— 一体化入口，替代原先分散的 `workbuddy_checkin.py` + `auto_checkin.py` + `notify.py`：
  - **WorkBuddy 平台**：移植上游 `workbuddy_checkin.py` v2.2.0 的接口（`/v2/billing/meter/checkin-activity-status` + `/daily-checkin`，Bearer 鉴权），登录前查状态、幂等跳过。
  - **Trae Work 平台**：移植自 [xz0609/trae-work-checkin-ql](https://github.com/xz0609/trae-work-checkin-ql)（MIT），
    含 `login`（浏览器 OAuth 登录）、`refresh`（刷新 token，临期前 24h 自动刷新并回写）、
    `checkin`/`claim`（9074 设备指纹校验失败重试并轮换设备、1001 刷新重试、9095 设备占用跳过）、`query_credits`。
  - **通知内置**：Webhook（群机器人/Server酱/钉钉/飞书/Bark）+ 企业微信「应用」双通道，配置在 `config.json` 的 `notify` 段；
    密钥与 webhook 地址自动脱敏（`key=***`、`corpsecret`/`access_token` 替换为 `***`）。
  - **去重与限流内置**：当日首次成功推一次；失败按每天上限（默认 3）+ 最小间隔（默认 60 分钟）限流，状态存 `.checkin_state.json`。
- **`config.json` 增加 `trae_accounts` 段**：Trae 凭证（统一存进 `config.json`，不再需要单独的 `auths/` 目录）。模板见 `config.json.example`。
- **CLI**：`python checkin.py`（全平台）/ `--trae-only` / `--workbuddy-only` / `login` / `--status-only` / `--dry-run` / `--debug` / `--version`。

### 变更

- 入口 bat（当时名为 `run_checkin.bat`，v1.5.0 起改名 `checkin.bat`）改为调用 `checkin.py`
  （原先 `auto_checkin.py`），计划任务路径不变。
- WorkBuddy 账号配置仍由上游 `workbuddy_checkin.py --setup` 生成 / 刷新（本脚本只读取 `accounts` 段，不重复实现登录抓取）。
- `config.json.example` 重构为对象结构，含 `accounts` / `trae_accounts` / `notify` 三段与 `_` 前缀注释。

### 注意事项（Trae 平台）

- Trae 签到依赖**本机真实设备指纹**：同一账号每天只能在做过 `login` 的那台设备上领取，换设备 / 云端需先在本机跑一次 `python checkin.py login`（且最好本机装过 Trae 客户端以拿到真实 `deviceId`）。
- 未配置 Trae 账号时脚本照常只跑 WorkBuddy，互不干扰。

---

## [v1.0] - 2026-09-14

本项目首个基线版本。在上游脚本 `workbuddy_checkin.py`
（来源 [`tianxing226/AICreditPunch`](https://github.com/tianxing226/AICreditPunch) v2.2.0；
⚠️ 该上游**未附任何许可证**，见 README §8.2）之上完成了本地化部署、通知编排与安全加固，
并将仓库从 GitHub 解绑转为本地版本管理。

### 新增

- **`auto_checkin.py`** —— 智能签到编排器
  - 以子进程方式调用上游脚本，按**退出码**判定成败，取代上游无条件的通知钩子
  - 当日首次成功才推送微信；同日后续运行（已签到）静默，仅写日志
  - 失败告警限流：每天最多 3 条、间隔 ≥60 分钟（`AICREDIT_MAX_FAIL_ALERTS` / `AICREDIT_FAIL_ALERT_INTERVAL` 可调）
  - 状态持久化到 `.checkin_state.json`，跨运行去重
- **`notify.py`** —— 通知插件（纯标准库）
  - 渠道 A：Webhook，按 URL 自动识别企业微信群机器人 / Server酱 / 钉钉 / 飞书 / Bark
  - 渠道 B：企业微信「应用」推送（`gettoken` + `message/send`），可指定接收人
  - 配置统一读取 `config.json` 的 `notify` 段，支持环境变量覆盖
  - 日志自动脱敏：`key=abc1***yz`，`corpsecret` / `access_token` 替换为 `***`
- **`run_checkin.bat`**（v1.5.0 起改名 `checkin.bat`）—— Windows 计划任务入口
  - 纯 ASCII + CRLF，设置 `PYTHONUTF8=1` 避免中文日志乱码
  - 执行前用 PowerShell 探测 `www.workbuddy.cn:443`，最长等待约 120 秒，
    解决开机/登录后 WiFi 未就绪导致当天漏签的问题
- **`config.json.example`** —— 脱敏配置模板，可安全入库
- **`DEPLOY.md`** —— 部署与运维手册（依赖、配置、定时任务、测试、排错表、通知配置）
  （v1.5.0 起已并入 `README.md`，`docs/` 目录删除）
- **`.gitignore`** —— 凭据与运行产物防护

### 变更

- `config.json` 由数组结构升级为对象结构，账号凭据与推送配置合并到同一文件：
  `{"_readme": [...], "accounts": [...], "notify": {...}}`
  - JSON 不支持注释，说明文字统一放在 `_` 前缀字段中，脚本会忽略
  - 已验证 `--setup` 刷新 token 时只替换 `accounts`，`notify` 与注释字段完整保留
- 签到频率由「每天 1 次」调整为「08:45 起每 3 小时一次、0 点前收尾」：
  08:45 / 11:45 / 14:45 / 17:45 / 20:45 / 23:45，共 6 次
  - 上游脚本幂等（已签到直接返回成功，不重复领取），高频重跑安全
- 原 `webhook.txt` / `wecom.txt` 等零散配置文件已删除，配置入口收敛为 `config.json`
- 仓库移除 GitHub 远程 `origin`（`https://github.com/tianxing226/AICreditPunch.git`），转为纯本地版本管理，
  彻底消除误推送到公开仓库的风险
- 清理克隆时遗留的上游标签 `v2.2.0`（指向上游提交 `7d72c84`，该提交仍在历史中），
  避免与本仓库从 `v1.0` 开始的版本序列混淆

### 安全

- `config.json` 使用 `icacls` 收紧为仅当前用户可读写（等价于 Linux `chmod 600`），断开继承
- `.gitignore` 覆盖 `config.json` / `*.bak` / `config.json.*` / `*.log` / `.checkin_state.json` / `.workbuddy/`
- 迁移期产生的 `config.json.bak`（含明文 token）已在本版本整理中删除

### 已验证

| 项目 | 结果 |
|---|---|
| 手动签到 | ✅ 成功 1/1（账号：示例账号） |
| 计划任务 `AICreditPunch-Daily` | ✅ 6 个每日触发器，实跑 `lastresult=0` |
| 计划任务 `AICreditPunch-Startup` | ✅ 登录补跑，实跑 `lastresult=0` |
| 通知限流与脱敏 | ✅ 生效 |
| 提交内容 | ✅ 无任何凭据文件 |
