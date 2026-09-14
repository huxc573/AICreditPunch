# 第三方组件声明（Third-Party Notices）

本文件列明本仓库（`huxc573/AICreditPunch`）所使用的第三方成果、其许可状态，
以及为满足「保留版权与许可声明」要求而必须随分发保留的原文。

本仓库整体以 **MIT** 许可发布，全文见 [LICENSE](LICENSE)。
下方各组件的许可均为 MIT，与本仓库许可**兼容**。

---

## 1. 本仓库自有部分

以下部分为本仓库自行设计实现，版权归 `huxc573` 所有，适用根目录 [LICENSE](LICENSE)：

- 通知编排与失败限流
- Windows 计划任务封装与自管理（`--install` / `--uninstall` / `--tasks`）
- 只读查看命令（`--today`）
- `checkin.bat` 入口、日志前置与子命令分派
- 安全加固（凭据权限收紧、日志脱敏、`api_base` 域名白名单校验）
- WorkBuddy 平台实现（**接口行为独立重写**，见 §4）
- 文档（`README.md` / `CHANGELOG.md`）

---

## 2. Trae 平台实现

> 来源：[`xz0609/trae-work-checkin-ql`](https://github.com/xz0609/trae-work-checkin-ql)
> （其自身 Fork 自 [`yang89520/auto-checkin-hub`](https://github.com/yang89520/auto-checkin-hub)）
> 许可：**MIT** · `Copyright (c) 2026 Yiran`
> 本仓库用法：本仓库的 Trae 链路（登录 / 刷新 token / 签到 / 查询积分）为该项目的衍生实现。

**必须保留的版权声明：**

```
Copyright (c) 2026 Yiran
```

---

## 3. Trae 登录流程（参考实现）

> 来源：[`Maquer/trae-signin`](https://github.com/Maquer/trae-signin)
> 许可：**MIT** · `Copyright (c) 2026`
> 本仓库用法：仅参考其 OAuth 登录流程（生成授权链接 → 本地端口回调收取
> `refreshToken` / `userInfo` / `userJwt` → 换取 accessToken）。
> **未复制其 Go / shell 代码**，本仓库的对应实现为自行编写。

**必须保留的版权声明：**

```
Copyright (c) 2026
```

---

## 4. WorkBuddy 平台实现（来源说明）

> 来源：[`tianxing226/AICreditPunch`](https://github.com/tianxing226/AICreditPunch)
> （`workbuddy_checkin.py` v2.2.0）
> 许可：**该仓库未附任何许可证文件**，依《伯尔尼公约》默认「保留所有权利」。
> 本仓库用法：**自 `v1.7.0` 起，WorkBuddy 段已按接口行为独立重写**，不含其代码。

说明：

1. 该上游**没有** LICENSE，也未在 README 中给出任何授权声明，因此其源码不可再分发。
2. 本仓库 `v1.6.0` 及更早的版本曾直接移植其实现；自 `v1.7.0` 起该部分已整体重写。
3. 重写后保留的仅为**不受著作权保护的接口协议事实**（请求路径、鉴权头名、报文字段名、
   状态码语义），代码结构、函数划分、命名与错误处理均为本仓库自行设计。
4. 口径边界：本次重写**不属于严格意义的 clean-room**
   （重写者此前已阅读过上游实现），本仓库据此如实标注为「独立重写」而非「clean-room 重写」。

> 另：`v1.1.0`–`v1.6.0` 期间，本仓库曾删除上游脚本文件并吸收其功能。
> 如需使用这些历史版本，请注意其许可状态与当前 HEAD 不同。

---

## 5. MIT 许可全文

以下全文适用于本文件 §2、§3 所述的第三方组件；
本仓库自身的 [LICENSE](LICENSE) 为同一许可的独立副本（版权人不同）。

```
MIT License

Copyright (c) 2026 Yiran
Copyright (c) 2026

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
