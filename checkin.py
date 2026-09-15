#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一体化每日签到脚本（WorkBuddy + Trae），仅依赖 Python 标准库。

设计目标：单个文件即可迁移到云端 / 青龙面板 / 其它设备，迁移时只需带上
`checkin.py` + `config.json`（Trae 凭证也统一存进 config.json，无需单独 auths 目录）。

支持的平台：
  - WorkBuddy：access_token 直连 API，无需浏览器（移植自上游 workbuddy_checkin.py v2.2.0 的接口）
  - Trae Work（字节，trae.cn 国内版）：移植自 https://github.com/xz0609/trae-work-checkin-ql
    · 凭证来自浏览器 OAuth 登录（`python checkin.py --init-trae`）。签到依赖本机真实设备指纹，
      因此同一账号每天只能在"做过登录的那台设备"上领取；换设备/云端需先在本机跑一次 `--init-trae`。

内置能力（原 notify.py / auto_checkin.py 已并入本文件并删除）：
  - 通知：Webhook（群机器人/Server酱/钉钉/飞书/Bark）+ 企业微信「应用」双通道，配置在 config.json 的 notify 段
  - 去重与限流：当日首次成功推一次；失败按每天上限 + 最小间隔限流
  - 幂等：两平台领取前都先查状态，已签到直接跳过

用法：
  python checkin.py                      # 跑所有已启用的平台
  python checkin.py --workbuddy-only     # 只跑 WorkBuddy
  python checkin.py --trae-only          # 只跑 Trae
  python checkin.py --status-only        # 只查状态不领取
  python checkin.py --dry-run            # 只校验配置
  python checkin.py --debug              # 打印脱敏响应
  python checkin.py --version

初始化（每个平台一次，用法与其它参数一致）：
  python checkin.py --init-workbuddy     # 导入本机 WorkBuddy 桌面端登录凭据（可配合 --auth-file）
  python checkin.py --init-trae          # Trae 浏览器 OAuth 登录
  python checkin.py --init               # 依次初始化两个平台
"""

from __future__ import annotations

import argparse
import base64
import binascii
import copy
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

VERSION = "1.7.0"
DEFAULT_TIMEOUT = 20
DEFAULT_RETRIES = 2
DEBUG = False

# 平台展示名与初始化命令（日志、通知、提示文案的统一口径）
PLATFORM_WORKBUDDY = "WorkBuddy"
PLATFORM_TRAE = "Trae"
INIT_CMD_WORKBUDDY = "python checkin.py --init-workbuddy"
INIT_CMD_TRAE = "python checkin.py --init-trae"
PLATFORM_LABELS = {"workbuddy": PLATFORM_WORKBUDDY, "trae": PLATFORM_TRAE}

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
STATE_FILE = HERE / ".checkin_state.json"
LOG_NAME = "AICreditPunch.log"

# 计划任务名 / 触发时刻 / 入口 bat —— 必须与 checkin.bat 顶部的同名常量保持一致，
# 改一边就得改另一边。这里只用于「检查」，Python 侧不注册任务。
TASK_DAILY = "AICreditPunch-Daily"
TASK_STARTUP = "AICreditPunch-Startup"
TASK_RESUME = "AICreditPunch-Resume"
CHECKIN_TIMES = ["08:45", "11:45", "14:45", "17:45", "20:45", "23:45"]
ENTRY_BAT = "checkin.bat"
ENTRY_PATH = HERE / ENTRY_BAT


def log_file_path() -> Path:
    """日志文件位置：直接放在 `%APPDATA%` 根下，即 `%APPDATA%\\AICreditPunch.log`。

    Windows 上 `%APPDATA%` = `C:\\Users\\<你>\\AppData\\Roaming`；
    其它平台回退到 `~/.config/AICreditPunch.log`；再失败则退回脚本同目录。
    """
    base = os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    try:
        d = Path(base)
        d.mkdir(parents=True, exist_ok=True)
        return d / LOG_NAME
    except OSError:
        return HERE / LOG_NAME


# 供 bat 复用（bat 自己不解析本行）
LOG_FILE = log_file_path()

# "请打开日志"标记：由 bat 调用时留文件而不是立刻弹记事本 —— bat 会把本次输出
# 前置到日志后再打开，记事本里才看得到最新一次运行（见 _request_open_log）。
OPEN_LOG_FLAG = LOG_FILE.parent / ".AICreditPunch.openlog"


# --------------------------------------------------------------------------- #
# 通用工具
# --------------------------------------------------------------------------- #
def log(message: str) -> None:
    # 空串 = 纯换行（用于块间留白），不输出时间戳前缀
    if not message:
        print("", flush=True)
        return
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode("utf-8", errors="replace").decode("utf-8", errors="replace"), flush=True)


def open_log_in_notepad() -> None:
    """用系统默认程序（Windows 上是记事本）打开日志文件；失败静默。"""
    try:
        if os.name == "nt":
            os.startfile(str(LOG_FILE))  # type: ignore[attr-defined]
        else:
            webbrowser.open(LOG_FILE.as_uri())
    except Exception:
        pass


def _request_open_log() -> None:
    """请求「打开日志」，按调用方决定时机。

    由 `checkin.bat` 调用时（环境变量 `ACP_LOG_OWNER=bat`）**不立刻打开**，只写一个
    标记文件；bat 把本次输出前置到日志顶端后再弹记事本，否则记事本显示的是本次运行
    之前的旧内容。直接 `python checkin.py` 时没有 bat 兜底，就立即打开。
    """
    if os.environ.get("ACP_LOG_OWNER") == "bat":
        try:
            OPEN_LOG_FLAG.parent.mkdir(parents=True, exist_ok=True)
            OPEN_LOG_FLAG.write_text("open\n", encoding="ascii")
            return
        except OSError:
            pass
    open_log_in_notepad()


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "y"}


def env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def clean_text(value: Any) -> str:
    return str(value or "").strip()


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("***" if any(m in str(k).lower() for m in ("token", "auth", "secret", "cookie", "key")) else redact(v)) for k, v in value.items()}
    if isinstance(value, str) and len(value) > 120:
        return value[:24] + "***" + value[-8:]
    return value


def _mask_url(url: str) -> str:
    try:
        parts = urllib.parse.urlparse(url)
        if parts.query:
            q = urllib.parse.parse_qs(parts.query)
            q = {k: ("***" if "key" in k.lower() or "token" in k.lower() else v) for k, v in q.items()}
            return parts._replace(query=urllib.parse.urlencode(q, doseq=True)).geturl()
    except Exception:
        pass
    return url


# --------------------------------------------------------------------------- #
# 两平台共用的文案构造
# --------------------------------------------------------------------------- #
def _fmt_credit(today: Optional[int] = None, streak: Optional[int] = None,
                balance: Optional[int] = None) -> str:
    """积分明细文本（中文）；字段缺失自动省略，保证两个平台写法一致。

    - `balance` 在 WorkBuddy 是 `total_credits`（当前总积分）；
    - `balance` 在 Trae 是 `usage_summary.total_amount - consumed_amount`（当前可用积分）。
    两者日志文本统一为「当前积分余额 X」。
    """
    items = []
    if today is not None:
        items.append(f"本次 +{today}")
    if streak is not None:
        items.append(f"连续 {streak} 天")
    if balance is not None:
        items.append(f"当前积分余额 {balance}")
    return "，".join(items)


def _line(state: str, detail: str = "") -> str:
    """日志正文：`{状态}；{明细}`。

    平台名不重复 —— 每个平台块已有 `===== 平台 =====` 标题，调用处统一写
    `log(f"[{name}] {_line(状态, 明细)}")`，于是每行是「账号 + 状态 +（可选）明细」。
    """
    return state + (f"；{detail}" if detail else "")


def _result(platform: str, name: str, state: str, detail: str = "") -> str:
    """对外结果文案（通知正文 / 汇总）：`{账号名}（{平台}）：{状态}；{明细}`。"""
    return f"{name}（{platform}）：{state}" + (f"；{detail}" if detail else "")


# --------------------------------------------------------------------------- #
# 两平台共用的响应解读
# --------------------------------------------------------------------------- #
def _code_of(payload: Optional[Dict[str, Any]]) -> Any:
    """报文里的业务码；非对象一律视为「没有码」。"""
    return payload.get("code") if isinstance(payload, dict) else None


def _msg_of(payload: Optional[Dict[str, Any]]) -> str:
    """报文里的提示文案：`message` / `msg` / `data.message`，取第一个非空。"""
    if not isinstance(payload, dict):
        return ""
    data = payload.get("data")
    nested = data.get("message") if isinstance(data, dict) else ""
    return clean_text(payload.get("message") or payload.get("msg") or nested)


def _business_ok(payload: Optional[Dict[str, Any]]) -> bool:
    """业务是否成功：`code` 缺省 / 0 / 200，且没有显式 `success: false`。"""
    if not isinstance(payload, dict):
        return False
    if payload.get("code") not in (None, 0, 200, "0", "200"):
        return False
    return payload.get("success") is not False


# --------------------------------------------------------------------------- #
# 通用 HTTP（POST + 重试）
# --------------------------------------------------------------------------- #
def http_post(url: str, headers: Dict[str, str], body: Any, timeout: int, retries: int) -> Tuple[Optional[int], Optional[Dict[str, Any]], Optional[str]]:
    data = json.dumps(body).encode("utf-8") if body is not None else b"{}"
    base_headers = {"Content-Type": "application/json"}
    for k, v in headers.items():
        base_headers.setdefault(k, v)
    base_headers.setdefault("Content-Length", str(len(data)))
    last_err = "请求重试结束"
    for attempt in range(retries + 1):
        request = urllib.request.Request(url, data=data, headers=base_headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
                payload = json.loads(raw) if raw else {}
                if not isinstance(payload, dict):
                    return int(response.status), None, "响应 JSON 顶层不是对象"
                if DEBUG:
                    log("调试响应: " + json.dumps(redact(payload), ensure_ascii=False)[:1500])
                return int(response.status), payload, None
        except urllib.error.HTTPError as exc:
            raw = ""
            try:
                raw = exc.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            payload = None
            if raw:
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict):
                        payload = parsed
                except json.JSONDecodeError:
                    pass
            if DEBUG and payload is not None:
                log("调试错误响应: " + json.dumps(redact(payload), ensure_ascii=False)[:1500])
            if exc.code not in {429, 500, 502, 503, 504} or attempt >= retries:
                return int(exc.code), payload, f"HTTP {exc.code} {exc.reason}"
            wait = min(8, 2 ** attempt)
            log(f"遇到 HTTP {exc.code}，{wait}s 后重试（{attempt + 1}/{retries}）")
            time.sleep(wait)
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            if attempt >= retries:
                return None, None, f"网络请求失败: {exc}"
            wait = min(8, 2 ** attempt)
            log(f"网络波动，{wait}s 后重试（{attempt + 1}/{retries}）")
            time.sleep(wait)
        except Exception as exc:
            return None, None, f"请求异常: {type(exc).__name__}: {exc}"
    return None, None, last_err


def http_get(url: str, headers: Dict[str, str], timeout: int) -> Tuple[Optional[int], Optional[Dict[str, Any]], Optional[str]]:
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                payload = None
            return int(response.status), payload if isinstance(payload, dict) else None, None
    except Exception as exc:
        return None, None, f"GET 失败: {exc}"


# --------------------------------------------------------------------------- #
# 配置加载
# --------------------------------------------------------------------------- #
def load_config() -> Dict[str, Any]:
    if not CONFIG_PATH.is_file():
        raise RuntimeError(f"未找到配置文件：{CONFIG_PATH}；请先运行 {INIT_CMD_WORKBUDDY} 与/或 {INIT_CMD_TRAE} 完成初始化")
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"config.json 读取失败：{exc}") from exc


def workbuddy_accounts(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    data = cfg.get("accounts")
    if data is None:
        # 兼容顶层为数组的旧格式
        if isinstance(cfg, list):
            data = cfg
        elif isinstance(cfg, dict) and any(k in cfg for k in ("access_token", "accessToken", "token")):
            data = [cfg]
    if not isinstance(data, list):
        return []
    return [a for a in data if isinstance(a, dict) and a.get("enabled", True) is not False]


def trae_accounts(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    data = cfg.get("trae_accounts")
    if not isinstance(data, list):
        return []
    return [a for a in data if isinstance(a, dict) and a.get("enabled", True) is not False]


# =========================================================================== #
# WorkBuddy 平台（独立实现）
#
# 本段为**独立实现**：只依据公开的接口行为（请求路径、鉴权头、报文字段）编写，
# 不复制上游脚本的代码结构、命名与错误处理方式。协议要点：
#
#   POST {base}/v2/billing/meter/checkin-activity-status   查询当日签到状态
#   POST {base}/v2/billing/meter/daily-checkin             提交签到
#   鉴权  Authorization: Bearer <access_token>
#         可选头 X-User-Id / X-Domain / X-Enterprise-Id / X-Tenant-Id
#   报文  code（缺省 / 0 / 200 视为业务成功）、message | msg、data.*
#   「已签到」有两种表达：专用 code 10001，或文案里的「已签到」字样，两者都要认。
# =========================================================================== #
WB_BASE_URL = "https://www.codebuddy.cn"
WB_ROUTE_STATUS = "/v2/billing/meter/checkin-activity-status"
WB_ROUTE_CLAIM = "/v2/billing/meter/daily-checkin"

# api_base 只允许这些域名：token 是 Bearer 裸传，绝不能发到第三方地址
WB_ALLOWED_HOSTS = frozenset({"codebuddy.cn", "workbuddy.cn", "copilot.tencent.com"})
WB_ALLOWED_SUFFIXES = (".codebuddy.cn", ".workbuddy.cn")

WB_ALREADY_CODE = 10001
WB_ALREADY_WORDS = ("已签到", "已经签到", "already checked", "already claimed")
WB_CHECKED_FLAGS = ("today_checked_in", "checked_in", "checkedIn", "claimed")
WB_TODAY_CREDIT_KEYS = ("daily_credit", "today_credit")
WB_STREAK_KEY = "streak_days"
WB_BALANCE_KEY = "total_credits"
WB_ACCOUNT_LABEL = "WorkBuddy账号"


def wb_validated_base(value: Any) -> str:
    """校验并规范化 api_base；不在白名单的地址直接拒绝，避免 token 外泄。"""
    raw = clean_text(value) or WB_BASE_URL
    candidate = raw if "://" in raw else "https://" + raw
    parts = urllib.parse.urlparse(candidate)
    host = (parts.hostname or "").lower()
    allowed = host in WB_ALLOWED_HOSTS or host.endswith(WB_ALLOWED_SUFFIXES)
    if parts.scheme != "https" or not allowed:
        raise ValueError(f"WorkBuddy API 地址校验失败: {raw}")
    port = f":{parts.port}" if parts.port else ""
    return f"https://{host}{port}{parts.path.rstrip('/')}"


class WbReply:
    """一次调用的「HTTP 码 + 报文」的语义化解读。

    调用方只读属性、不再自己做字段探测，于是判定规则集中在一处，好改也好测。
    """

    __slots__ = ("http", "payload", "transport_error")

    def __init__(self, http: Optional[int], payload: Any, transport_error: Optional[str]) -> None:
        self.http = http
        self.payload = payload if isinstance(payload, dict) else None
        self.transport_error = transport_error

    def _scope(self) -> Dict[str, Any]:
        """字段查找范围：优先 data 子对象，没有就退回顶层报文。"""
        data = (self.payload or {}).get("data")
        return data if isinstance(data, dict) else (self.payload or {})

    @property
    def code(self) -> Any:
        return (self.payload or {}).get("code")

    @property
    def message(self) -> str:
        return _msg_of(self.payload)

    @property
    def accepted(self) -> bool:
        """业务是否成功：code 缺省 / 0 / 200，且没有显式 `success: false`。"""
        if self.payload is None:
            return False
        if self.code not in (None, 0, 200, "0", "200"):
            return False
        return self.payload.get("success") is not False

    @property
    def already_checked(self) -> bool:
        """今日是否已签到：专用 code、文案关键字、报文标记，命中任一即可。"""
        if self.payload is None:
            return False
        if str(self.code) == str(WB_ALREADY_CODE):
            return True
        text = self.message.lower()
        if any(word in text for word in WB_ALREADY_WORDS):
            return True
        scope = self._scope()
        for flag in WB_CHECKED_FLAGS:
            if flag not in scope:
                continue
            value = scope[flag]
            if isinstance(value, str):
                if value.lower() in {"true", "1"}:
                    return True
            elif value:
                return True
        return False

    @property
    def credits(self) -> Tuple[Optional[int], Optional[int], Optional[int]]:
        """(本次积分, 连续天数, 当前总积分)；缺失一律 None。"""
        scope = self._scope()
        today = next((scope[key] for key in WB_TODAY_CREDIT_KEYS if scope.get(key) is not None), None)
        streak = scope.get(WB_STREAK_KEY)
        balance = scope.get(WB_BALANCE_KEY)
        return (
            today if isinstance(today, (int, float)) else None,
            streak if isinstance(streak, (int, float)) and streak else None,
            balance if isinstance(balance, (int, float)) else None,
        )

    @property
    def credit_text(self) -> str:
        return _fmt_credit(*self.credits)

    @property
    def reason(self) -> str:
        """失败原因：优先报文里的 message，其次网络层错误。"""
        return self.message or self.transport_error or "未知错误"


class WorkBuddyClient:
    """一个账号的一次签到会话：发请求、把响应翻译成日志行与结果文案。"""

    def __init__(self, record: Dict[str, Any], timeout: int, retries: int) -> None:
        self.record = record
        self.timeout = timeout
        self.retries = retries
        self.name = clean_text(record.get("name")) or WB_ACCOUNT_LABEL
        self.base = wb_validated_base(record.get("api_base"))
        self._headers = self._build_headers()

    def _build_headers(self) -> Dict[str, str]:
        headers = {
            "Authorization": f"Bearer {clean_text(self.record.get('access_token'))}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": f"WorkBuddy-Checkin/{VERSION}",
        }
        uid = clean_text(self.record.get("uid"))
        if uid:
            headers["X-User-Id"] = uid
        domain = clean_text(self.record.get("domain"))
        if domain:
            headers["X-Domain"] = domain
        enterprise = clean_text(self.record.get("enterprise_id"))
        if enterprise:
            headers["X-Enterprise-Id"] = enterprise
            headers["X-Tenant-Id"] = enterprise
        return headers

    def _call(self, route: str) -> WbReply:
        status, payload, error = http_post(self.base + route, self._headers, {},
                                           self.timeout, self.retries)
        return WbReply(status, payload, error)

    def _say(self, state: str, detail: str = "") -> None:
        log(f"[{self.name}] {_line(state, detail)}")

    @staticmethod
    def _outcome(ok: bool, name: str, state: str, detail: str = "") -> Tuple[bool, str]:
        return ok, _result(PLATFORM_WORKBUDDY, name, state, detail)

    def check_in(self, status_only: bool) -> Tuple[bool, str]:
        self._say("查询签到状态", f"API={self.base}")
        status = self._call(WB_ROUTE_STATUS)

        if status.already_checked:
            return self._settle("今日已签到，本次无需签到", status)

        if status.accepted:
            self._say("今日未签到", status.credit_text)
        else:
            self._say("状态查询异常", f"HTTP={status.http} code={status.code} {status.reason}")
            if status.http in (401, 403):
                return self._outcome(False, self.name, "凭证失效或权限不足")
            if status_only:
                return self._outcome(False, self.name, "状态查询失败")

        if status_only:
            return self._outcome(True, self.name, "待签到")
        self._say("提交签到")
        return self._claim()

    def _claim(self) -> Tuple[bool, str]:
        claim = self._call(WB_ROUTE_CLAIM)
        if claim.already_checked:
            return self._settle("今日已签到，本次无需签到", claim)
        if not claim.accepted:
            self._say("签到失败", f"HTTP={claim.http} code={claim.code} {claim.reason}")
            return self._outcome(False, self.name, "签到失败", claim.reason[:80])

        # 接口受理不等于到账：回查一次状态才算真的签到成功。
        self._say("签到已受理，回查确认")
        verify = self._call(WB_ROUTE_STATUS)
        if verify.already_checked:
            return self._settle("签到成功", verify)
        return self._outcome(False, self.name, "领取后回查未确认签到")

    def _settle(self, state: str, reply: WbReply) -> Tuple[bool, str]:
        """已签到 / 签到成功：日志与结果文案都带上积分明细。"""
        self._say(state, reply.credit_text)
        return self._outcome(True, self.name, state, reply.credit_text)


def run_workbuddy(acc: Dict[str, Any], status_only: bool, timeout: int, retries: int) -> Tuple[bool, str]:
    """单账号签到入口（对外签名保持不变）。api_base 非法时只跳过该账号，不拖垮整次运行。"""
    name = clean_text(acc.get("name")) or WB_ACCOUNT_LABEL
    try:
        client = WorkBuddyClient(acc, timeout, retries)
    except ValueError as exc:
        log(f"[{name}] {_line('跳过', str(exc))}")
        return False, _result(PLATFORM_WORKBUDDY, name, "配置错误", "api_base 不在白名单，已跳过")
    return client.check_in(status_only)


# =========================================================================== #
# WorkBuddy 初始化（独立实现：读取桌面端 auth 快照并合并进 config.json）
#
# 桌面端把登录态存在 `Data/Public/auth/workbuddy-desktop*.info` 快照里，
# 结构为 `{"auth": {"accessToken": ...}, "accounts": [...]}`。本段只做三件事：
# 找快照 → 解析成账号记录 → 按 uid 合并进 config.json 的 accounts 段（幂等）。
# =========================================================================== #
WB_AUTH_ENV = "WORKBUDDY_AUTH_FILE"
WB_AUTH_FILENAME = "workbuddy-desktop*.info"
WB_PRODUCT_DIRS = ("CodeBuddyExtension", "WorkBuddy")
WB_AUTH_TAIL = ("Data", "Public", "auth")
WB_DEFAULT_DOMAIN = "www.workbuddy.cn"


def _dedupe_paths(paths: Any) -> List[Path]:
    """按大小写不敏感的完整路径去重，保持原有顺序。"""
    unique: List[Path] = []
    seen = set()
    for path in paths:
        key = os.path.normcase(str(path))
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def wb_auth_dirs() -> List[Path]:
    """桌面端 auth 目录候选（Windows / macOS），有界、非递归、去重。"""
    home = Path.home()
    local = clean_text(os.environ.get("LOCALAPPDATA"))
    roots: List[Path] = [Path(local) if local else home / "AppData" / "Local"]
    roaming = clean_text(os.environ.get("APPDATA"))
    if roaming:
        roots.append(Path(roaming))
    suspects = [root.joinpath(product, *WB_AUTH_TAIL) for root in roots for product in WB_PRODUCT_DIRS]
    suspects += [home.joinpath("Library", "Application Support", product, *WB_AUTH_TAIL)
                 for product in WB_PRODUCT_DIRS]
    return _dedupe_paths(suspects)


class WbAuthSnapshot:
    """一个 `workbuddy-desktop*.info` 快照：解析出一条账号记录，绝不回显敏感值。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.record: Optional[Dict[str, Any]] = None
        self.expires_at: Optional[float] = None

    @property
    def expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= time.time()

    def load(self) -> bool:
        """解析快照；不可用时打一行跳过原因并返回 False。"""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return self._skip("读取或 JSON 解析失败")
        auth = data.get("auth") if isinstance(data, dict) else None
        if not isinstance(auth, dict):
            return self._skip("缺少 auth 对象")
        token = clean_text(auth.get("accessToken") or auth.get("access_token"))
        if not token:
            return self._skip("缺少 accessToken")
        owner = self._pick_owner(data, token)
        uid = clean_text(owner.get("uid") or owner.get("user_id")) or (_jwt_subject(token) or "")
        self.record = {
            "enabled": True,
            "name": self._display_name(owner, uid),
            "access_token": token,
            "uid": uid,
            "domain": clean_text(auth.get("domain") or owner.get("domain")) or WB_DEFAULT_DOMAIN,
            "enterprise_id": self._enterprise_id(owner, auth),
            "api_base": WB_BASE_URL,
        }
        self.expires_at = self._expires_at(auth)
        return True

    def _skip(self, why: str) -> bool:
        log(f"跳过认证文件：{self.path}（{why}）")
        return False

    def _pick_owner(self, data: Dict[str, Any], token: str) -> Dict[str, Any]:
        """挑出账号对象：先按 token 主体里的 uid 精确匹配，再退到「上次登录」标记。"""
        owners: List[Dict[str, Any]] = []
        for key in ("account", "accounts", "allAccounts"):
            value = data.get(key)
            if isinstance(value, dict):
                owners.append(value)
            elif isinstance(value, list):
                owners.extend(item for item in value if isinstance(item, dict))
        subject = _jwt_subject(token)
        if subject:
            matched = next((o for o in owners
                            if clean_text(o.get("uid") or o.get("user_id")) == subject), None)
            if matched:
                return matched
        flagged = next((o for o in owners
                        if o.get("lastLogin") is True
                        or clean_text(o.get("lastLogin")).lower() in {"true", "1", "yes"}), None)
        return flagged or (owners[0] if owners else {})

    def _display_name(self, owner: Dict[str, Any], uid: str) -> str:
        name = clean_text(owner.get("nickname") or owner.get("name") or owner.get("displayName")
                          or owner.get("username") or owner.get("uin"))
        if name:
            return name
        return f"账号-{uid[-8:]}" if uid else self.path.stem

    @staticmethod
    def _enterprise_id(owner: Dict[str, Any], auth: Dict[str, Any]) -> str:
        for source in (owner, auth):
            for key in ("enterpriseId", "enterprise_id", "tenantId", "tenant_id"):
                value = clean_text(source.get(key))
                if value:
                    return value
        return ""

    @staticmethod
    def _expires_at(auth: Dict[str, Any]) -> Optional[float]:
        """过期时间归一到秒；桌面端可能给毫秒时间戳。"""
        raw = auth.get("expiresAt") or auth.get("expires_at")
        if raw is None:
            return None
        try:
            value = float(str(raw))
        except (TypeError, ValueError):
            return None
        return value / 1000.0 if value > 100_000_000_000 else value


def _requested_auth_paths(explicit: Optional[str]) -> List[str]:
    """显式参数优先，其次环境变量（多个用系统路径分隔符）；都没有就返回空表示自动发现。"""
    if clean_text(explicit):
        return [clean_text(explicit)]
    from_env = clean_text(os.environ.get(WB_AUTH_ENV))
    if not from_env:
        return []
    return [item.strip() for item in from_env.split(os.pathsep) if item.strip()]


def wb_auth_snapshots(explicit: Optional[str] = None) -> List[WbAuthSnapshot]:
    """收集待解析的凭据快照（只列文件，不读内容）。"""
    requested = _requested_auth_paths(explicit)
    candidates: List[Path] = []
    if requested:
        for value in requested:
            path = Path(value).expanduser()
            if path.is_dir():
                candidates.extend(sorted(path.glob(WB_AUTH_FILENAME)))
            elif path.is_file():
                candidates.append(path)
            else:
                log(f"未找到认证文件：{path}")
    else:
        for directory in wb_auth_dirs():
            if directory.is_dir():
                candidates.extend(sorted(directory.glob(WB_AUTH_FILENAME)))
    resolved: List[Path] = []
    for path in candidates:
        try:
            resolved.append(path.resolve())
        except OSError:
            resolved.append(path.absolute())
    return [WbAuthSnapshot(path) for path in _dedupe_paths(resolved)]


def _record_token(record: Dict[str, Any]) -> str:
    return clean_text(record.get("access_token") or record.get("accessToken") or record.get("token"))


class WbAccountStore:
    """config.json 的 `accounts` 段：合并导入记录，重复运行保持幂等。"""

    def __init__(self, records: Any) -> None:
        source = records if isinstance(records, list) else []
        self.records: List[Dict[str, Any]] = [copy.deepcopy(item) for item in source
                                              if isinstance(item, dict)]

    @staticmethod
    def identity(record: Dict[str, Any]) -> Tuple[str, str]:
        """账号身份：优先 uid，缺失时退回 token（用于判重与定位）。"""
        uid = clean_text(record.get("uid") or record.get("user_id"))
        token = _record_token(record)
        if not uid:
            uid = _jwt_subject(token) or ""
        return uid, token

    def merge(self, candidates: List[Dict[str, Any]]) -> Tuple[int, int]:
        """就地合并，返回 (更新数, 新增数)。"""
        by_uid: Dict[str, int] = {}
        by_token: Dict[str, int] = {}
        for position, record in enumerate(self.records):
            uid, token = self.identity(record)
            if uid:
                by_uid.setdefault(uid, position)
            if token:
                by_token.setdefault(token, position)

        updated = added = 0
        for candidate in candidates:
            uid, token = self.identity(candidate)
            position = by_uid.get(uid) if uid else None
            if position is None and not uid:
                position = by_token.get(token)
            if position is None:
                self.records.append(self._adopt(candidate, None))
                position = len(self.records) - 1
                added += 1
            else:
                self.records[position] = self._adopt(candidate, self.records[position])
                updated += 1
            if uid:
                by_uid[uid] = position
            if token:
                by_token[token] = position

        self.records = self._dedupe()
        return updated, added

    @staticmethod
    def _adopt(candidate: Dict[str, Any], existing: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """生成入库记录：保留既有自定义字段，凭据一律以本次导入为准。"""
        merged = copy.deepcopy(existing) if isinstance(existing, dict) else {}
        for stale in ("accessToken", "token"):
            merged.pop(stale, None)
        merged.setdefault("enabled", True)
        if not clean_text(merged.get("name")):
            merged["name"] = candidate["name"]
        merged["access_token"] = candidate["access_token"]
        if candidate.get("uid"):
            merged["uid"] = candidate["uid"]
        merged["domain"] = (candidate["domain"] or clean_text(merged.get("domain"))
                            or WB_DEFAULT_DOMAIN)
        # 快照里没有企业信息时保留用户手填的值，不要用空串覆盖
        merged["enterprise_id"] = candidate["enterprise_id"] or clean_text(merged.get("enterprise_id"))
        if not clean_text(merged.get("api_base")):
            merged["api_base"] = WB_BASE_URL
        return merged

    def _dedupe(self) -> List[Dict[str, Any]]:
        kept: List[Dict[str, Any]] = []
        seen = set()
        for record in self.records:
            uid, token = self.identity(record)
            key = f"uid:{uid}" if uid else (f"token:{token}" if token else f"pos:{len(kept)}")
            if key in seen:
                continue
            seen.add(key)
            kept.append(record)
        return kept


def _config_for_write() -> Optional[Dict[str, Any]]:
    """读取待改写的 config.json；文件不存在给空配置，已损坏则中止以免覆盖用户数据。"""
    if not CONFIG_PATH.is_file():
        return {}
    try:
        return load_config()
    except Exception as exc:
        log(f"config.json 解析失败（{type(exc).__name__}），为避免覆盖已有配置已中止；"
            f"请先修复该文件")
        return None


def _masked_uid(uid: str) -> str:
    return ("***" + uid[-8:]) if len(uid) > 8 else (uid or "未提供")


def setup_workbuddy(auth_file: Optional[str] = None) -> int:
    """WorkBuddy 初始化：把本机桌面端登录凭据导入 config.json 的 `accounts` 段。"""
    if clean_text(auth_file):
        explicit = Path(auth_file).expanduser()
        if not explicit.exists():
            log(f"指定认证路径不存在：{explicit}")
            return 1

    snapshots = [snap for snap in wb_auth_snapshots(auth_file) if snap.load()]
    if not snapshots:
        log("未发现本机 WorkBuddy 登录凭据（workbuddy-desktop.info）。")
        log("请先在本机登录 WorkBuddy 桌面端后重试；或从已登录电脑复制 config.json 到本目录；")
        log(f"也可用 `{INIT_CMD_WORKBUDDY} --auth-file <路径>` 或环境变量 {WB_AUTH_ENV} 指定凭据文件。")
        return 1

    cfg = _config_for_write()
    if cfg is None:
        return 1

    imported = [snap.record for snap in snapshots if snap.record]
    store = WbAccountStore(cfg.get("accounts"))
    updated, added = store.merge(imported)

    log(f"发现可用账号：{len(imported)} 个")
    for snap in snapshots:
        record = snap.record or {}
        log(f"- {record.get('name')}；UID={_masked_uid(record.get('uid') or '')}；"
            f"token_length={len(record.get('access_token') or '')}；来源={snap.path.name}"
            f"{'；已过期' if snap.expired else ''}")
    log(f"配置合并：更新 {updated} 个，新增 {added} 个，最终保留 {len(store.records)} 个账号")

    cfg["accounts"] = store.records
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log(f"配置已写入：{CONFIG_PATH}（notify / trae_accounts / 注释字段完整保留）")
    log(f"{PLATFORM_WORKBUDDY} 初始化完成：已写入 config.json 的 accounts（{len(store.records)} 个账号）；"
        f"直接运行 `python checkin.py` 即可签到")
    return 0


# =========================================================================== #
# Trae 平台（移植自 xz0609/trae-work-checkin-ql，凭证统一进 config.json）
# =========================================================================== #
TRAE_CLIENT_ID = "en1oxy7wnw8j9n"
TRAE_IDE_VERSION = "0.1.43"
TRAE_API_BASE = "https://api.trae.cn"
TRAE_EXCHANGE_HOST = "https://api.trae.com.cn"
TRAE_LOGIN_HOST = "https://www.trae.cn"
TRAE_EXCHANGE_URL = TRAE_EXCHANGE_HOST + "/cloudide/api/v3/trae/oauth/ExchangeToken"
TRAE_STATUS_URL = TRAE_API_BASE + "/trae/api/v2/ug/checkin_credits/status"
TRAE_CLAIM_URL = TRAE_API_BASE + "/trae/api/v2/ug/checkin_credits/claim"
TRAE_CREDITS_URL = TRAE_API_BASE + "/trae/api/v2/pay/ide_user_ent_usage"
TRAE_REFRESH_MARGIN = 24 * 3600
TRAE_CLAIM_RETRIES = 3

# 「今日是否已签到」的判读口径。实测 status 响应（未签到时）：
#   {"checked_in": false, "code": 0, "credits": 150, "did_checked_in": false,
#    "enable": true, "extra_credits": 50, "message": "success"}
# 注意 `code=0` 只代表「查询成功」——未签到时同样是 0。所以是否已签到必须
# 只看明确标记与明确文案，**绝不能用业务码兜底**（v1.7.0 及更早正是这么错的，
# 结果每次运行都判成「今日已签到」，claim 一次都没执行过）。
TRAE_CHECKED_FLAGS = ("checked_in", "did_checked_in", "today_checked_in", "is_checked_in")
TRAE_ALREADY_WORDS = ("已签到", "已经签到", "已领取", "今日已领取", "明日再来",
                      "already checked", "already claimed")
TRAE_CREDIT_KEYS = ("credits", "today_credit", "daily_credit", "sign_credit", "reward_credit",
                    "credit", "reward", "score", "points",
                    "obtain_credit", "get_credit", "add_credit", "grant_credit")
# 领奖后回查确认的等待节奏（0 = 立即回查一次，再等 4s 回查一次）
TRAE_VERIFY_WAITS = (0, 4)


def _trae_headers(acc: Dict[str, Any], device_id: str) -> Dict[str, str]:
    fp = acc.get("fingerprint") or {}
    brand = clean_text(fp.get("brand")) or "Apple"
    dev_type = clean_text(fp.get("type")) or "mac"
    os_version = clean_text(fp.get("os")) or "macOS"
    return {
        "Content-Type": "application/json",
        "Authorization": f"Cloud-IDE-JWT {acc['accessToken']}",
        "x-device-id": device_id,
        "X-Device-Id": device_id,
        "X-User-Region": "CN",
        "x-device-brand": brand,
        "x-device-type": dev_type,
        "x-os-version": os_version,
        "x-app-version": TRAE_IDE_VERSION,
        "User-Agent": f"Trae/{TRAE_IDE_VERSION}",
    }


def _trae_device_pool() -> List[str]:
    """扫描本机已安装 Trae 客户端的真实设备池（与参考仓库一致）。

    设备配置文件固定位于 <TraeAppDir>/ahanet/tt_net_config.config（或 <TraeAppDir>/tt_net_config.config），
    直接按已知路径读取，**不递归扫描**——否则对含大缓存的 Trae 目录做 rglob 每次运行会慢 8s+。
    """
    home = Path.home()
    app = os.environ.get("APPDATA")
    names = ("TRAE SOLO CN", "Trae CN", "Trae", "TRAE")
    dirs: List[Path] = []
    if app:
        base = Path(app)
        for n in names:
            dirs.append(base / n)
    for n in names:
        dirs.append(home / "AppData" / "Roaming" / n)
        dirs.append(home / "Library" / "Application Support" / n)
    found: List[str] = []
    rels = ("ahanet/tt_net_config.config", "tt_net_config.config")
    for d in dirs:
        for rel in rels:
            cfg = d / rel
            if not cfg.is_file():
                continue
            try:
                text = cfg.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for m in re.findall(r"device_id&#\*([^@$]*)@\$", text):
                if m and m not in found:
                    found.append(m)
    return found


def _resolve_trae_device_id(stored: str) -> str:
    for cand in (
        Path.home() / "AppData" / "Roaming" / "TRAE SOLO CN" / "machineid",
        Path.home() / "AppData" / "Roaming" / "Trae CN" / "machineid",
        Path.home() / "Library" / "Application Support" / "TRAE SOLO CN" / "machineid",
        Path.home() / "Library" / "Application Support" / "Trae CN" / "machineid",
    ):
        try:
            val = cand.read_text(encoding="utf-8", errors="replace").strip()
            if val:
                return val
        except OSError:
            continue
    return clean_text(stored) or secrets.token_hex(16)


def _trae_fingerprint() -> Tuple[str, str, str]:
    if sys.platform == "darwin":
        try:
            brand = os.popen("sysctl -n hw.model").read().strip() or "Apple"
        except Exception:
            brand = "Apple"
        dev_type = "mac"
        try:
            os_version = "macOS " + os.popen("sw_vers -productVersion").read().strip()
        except Exception:
            os_version = "macOS"
    elif sys.platform == "win32":
        brand = "PC"
        dev_type = "PC"
        os_version = "Windows"
    else:
        brand, dev_type, os_version = "Unknown", "linux", platform_platform()
    return brand, dev_type, os_version


def platform_platform() -> str:
    return clean_text(__import__("platform").platform()) or "Unknown"


def _trae_pick_device(acc: Dict[str, Any], pool: List[str]) -> str:
    cid = clean_text(acc.get("claimDeviceId"))
    if cid:
        return cid
    if pool:
        return pool[0]
    return _resolve_trae_device_id(acc.get("deviceId", ""))


def _trae_refresh(acc: Dict[str, Any]) -> bool:
    name = acc.get("screenName") or acc.get("userId") or "Trae账号"
    body = {
        "ClientID": TRAE_CLIENT_ID,
        "RefreshToken": acc["refreshToken"],
        "ClientSecret": "-",
        "UserID": acc.get("userId") or "",
    }
    status, payload, err = http_post(TRAE_EXCHANGE_URL, {"Content-Type": "application/json"}, body, DEFAULT_TIMEOUT, DEFAULT_RETRIES)
    if payload is None:
        log(f"[{name}] {_line('token 刷新失败', str(err))}")
        return False
    data = payload.get("Result") if isinstance(payload.get("Result"), dict) else (payload.get("data") if isinstance(payload.get("data"), dict) else payload)
    tok = data.get("Token") or data.get("AccessToken") or data.get("access_token")
    if not tok:
        log(f"[{name}] {_line('token 刷新响应缺少 Token', str(_msg_of(payload)))}")
        return False
    acc["accessToken"] = tok
    if data.get("RefreshToken") or data.get("refreshToken"):
        acc["refreshToken"] = data.get("RefreshToken") or data.get("refresh_token")
    exp = data.get("TokenExpireAt") or data.get("expiresAt")
    if exp:
        try:
            exp_v = float(exp)
            if exp_v > 1e12:
                exp_v /= 1000.0
            acc["expiresAt"] = exp_v
        except (TypeError, ValueError):
            pass
    return True


def _trae_flag(payload: Optional[Dict[str, Any]], keys: Tuple[str, ...]) -> Optional[bool]:
    """在 payload / payload.data 里按 keys 顺序找布尔标记；字段不存在返回 None。

    字符串 "true"/"1" 视为真、"false"/"0" 视为假，兼容把布尔序列化成字符串的接口。
    keys 的先后就是优先级：先命中的那个字段说了算。
    """
    if not isinstance(payload, dict):
        return None
    data = payload.get("data") if isinstance(payload.get("data"), dict) else None
    for node in (payload, data):
        if not isinstance(node, dict):
            continue
        for key in keys:
            if key not in node:
                continue
            value = node[key]
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                text = value.strip().lower()
                if text in ("true", "1", "yes"):
                    return True
                if text in ("false", "0", "no"):
                    return False
    return None


def _trae_status_checked(payload: Optional[Dict[str, Any]]) -> bool:
    """状态查询是否表示「今日已签到」。

    只认**明确标记**（checked_in 等）与**明确文案**；业务码一概不看。
    Trae 的 status 在「未签到」时同样返回 code=0 / message=success，
    拿业务码兜底就会把「每次查询成功」当成「今天已经签过」。
    """
    flag = _trae_flag(payload, TRAE_CHECKED_FLAGS)
    if flag is not None:
        return flag
    msg = _msg_of(payload).lower()
    return any(word in msg for word in TRAE_ALREADY_WORDS)


def _trae_claim_ok(payload: Optional[Dict[str, Any]]) -> bool:
    """领奖调用是否成功：业务码成功，或明确说明今天已经领过。

    与 status 不同，claim 的 code=0/200 就是「领奖成功」本身，所以这里可以用。
    """
    if _business_ok(payload):
        return True
    return _trae_status_checked(payload)


def _trae_pick_int(payload: Optional[Dict[str, Any]], *keys: str) -> Optional[int]:
    """从响应（支持 data 嵌套）里取第一个匹配 key 的整数字段。"""
    if not isinstance(payload, dict):
        return None
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    for d in (payload, data):
        if not isinstance(d, dict):
            continue
        for k in keys:
            v = d.get(k)
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float)):
                return int(v)
            if isinstance(v, str) and v.isdigit():
                return int(v)
    return None


def _trae_today_earned(payload: Optional[Dict[str, Any]]) -> Optional[int]:
    """今日可得 / 已得的积分 = credits + extra_credits；两个字段都缺则 None。"""
    base = _trae_pick_int(payload, *TRAE_CREDIT_KEYS)
    extra = _trae_pick_int(payload, "extra_credits", "extra_credit")
    if base is None and extra is None:
        return None
    return (base or 0) + (extra or 0)


def _trae_confirm(headers: Dict[str, str], timeout: int, retries: int) -> Optional[Dict[str, Any]]:
    """领奖后回查状态：确认到账返回状态报文，否则 None。

    服务端落账可能有几秒延迟，所以按 TRAE_VERIFY_WAITS 的节奏多查几次。
    """
    for wait in TRAE_VERIFY_WAITS:
        if wait:
            time.sleep(wait)
        status = http_post(TRAE_STATUS_URL, headers, {}, timeout, retries)
        if _trae_status_checked(status[1]):
            return status[1]
    return None


def _trae_query_credits(acc: Dict[str, Any], device_id: str, timeout: int, retries: int) -> Optional[int]:
    """查询 Trae 当前积分余额（来自 ide_user_ent_usage 接口），返回当前可用积分或 None。

    真实响应结构（实测）：usage_summary.total_amount=总额、consumed_amount=已用；
    余额展示统一为"当前积分余额 X"，因此返回 `总额 - 已用`（可用积分）。
    若无法取得已用量，则回退到总额；再无法取得则回退到套餐额度求和。
    """
    st, pl, err = http_post(TRAE_CREDITS_URL, _trae_headers(acc, device_id), {}, timeout, retries)
    if pl is None:
        log(f"[{acc.get('screenName') or acc.get('userId') or 'Trae账号'}] "
            f"{_line('积分查询失败', str(err))}")
        return None
    data = pl.get("data") if isinstance(pl.get("data"), dict) else pl
    usage = data.get("usage_summary") if isinstance(data.get("usage_summary"), dict) else None
    if usage and usage.get("total_amount") is not None:
        total = _to_int(usage.get("total_amount"), 0)
        consumed = _to_int(usage.get("consumed_amount"), 0)
        left = total - consumed
        return left if consumed else total
    packs = data.get("user_entitlement_pack_list") if isinstance(data.get("user_entitlement_pack_list"), list) else None
    if packs:
        total = 0
        for p in packs:
            info = p.get("entitlement_base_info") if isinstance(p.get("entitlement_base_info"), dict) else {}
            quota = info.get("quota") if isinstance(info.get("quota"), dict) else {}
            c = quota.get("credits_limit")
            if isinstance(c, (int, float)):
                total += int(c)
        return total
    return None


def run_trae(acc: Dict[str, Any], status_only: bool, timeout: int, retries: int, cfg: Dict[str, Any]) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    name = acc.get("screenName") or acc.get("userId") or "Trae账号"
    if not acc.get("accessToken") or not acc.get("refreshToken"):
        return False, _result(PLATFORM_TRAE, name, f"缺少凭证，请先运行 `{INIT_CMD_TRAE}` 完成初始化"), None
    pool = _trae_device_pool()
    device_id = _trae_pick_device(acc, pool)

    # token 临期前 24h 自动刷新
    expires = acc.get("expiresAt")
    if expires is not None and time.time() - 0 > (expires - TRAE_REFRESH_MARGIN):
        log(f"[{name}] {_line('token 临期，自动刷新')}")
        if _trae_refresh(acc):
            _persist_trae(cfg, acc)

    headers = _trae_headers(acc, device_id)
    log(f"[{name}] {_line('查询签到状态', f'device={device_id[:8]}***')}")
    status = http_post(TRAE_STATUS_URL, headers, {}, timeout, retries)
    if _trae_status_checked(status[1]):
        bal = _trae_query_credits(acc, device_id, timeout, retries)
        txt = _fmt_credit(today=_trae_today_earned(status[1]), balance=bal)
        log(f"[{name}] {_line('今日已签到，本次无需签到', txt)}")
        return True, _result(PLATFORM_TRAE, name, '今日已签到，本次无需签到', txt), None
    if status_only:
        if status[1] is None:
            return False, _result(PLATFORM_TRAE, name, '状态查询失败', clean_text(status[2])[:80]), None
        return True, _result(PLATFORM_TRAE, name, '待签到'), None
    if status[1] is None:
        log(f"[{name}] {_line('状态查询失败，仍尝试领取', clean_text(status[2])[:60])}")

    log(f"[{name}] {_line('提交签到')}")
    for attempt in range(TRAE_CLAIM_RETRIES):
        claim = http_post(TRAE_CLAIM_URL, headers, {}, timeout, retries)
        c = _code_of(claim[1])
        if _trae_claim_ok(claim[1]):
            # 受理 ≠ 到账：必须回查确认（与 WorkBuddy 同一口径）。少了这一步，
            # 就会出现「日志写签到成功、平台上其实没签上」的假绿。
            log(f"[{name}] {_line('签到已受理，回查确认')}")
            settled = _trae_confirm(headers, timeout, retries)
            if settled is not None:
                bal = _trae_query_credits(acc, device_id, timeout, retries)
                earned = _trae_today_earned(claim[1]) or _trae_today_earned(settled)
                txt = _fmt_credit(today=earned, balance=bal)
                log(f"[{name}] {_line('签到成功', txt)}")
                return True, _result(PLATFORM_TRAE, name, '签到成功', txt), acc if acc.get("accessToken") != _orig_token(acc) else None
            log(f"[{name}] {_line('回查未确认签到', '状态接口仍显示未签到，稍后重试')}")
            continue
        if c == 1001:
            log(f"[{name}] {_line('认证失败（1001），刷新 token 重试')}")
            if _trae_refresh(acc):
                _persist_trae(cfg, acc)
                headers = _trae_headers(acc, device_id)
                continue
            return False, _result(PLATFORM_TRAE, name, 'token 刷新失败'), acc
        if c == 9074:
            wait = 20 * (attempt + 1)
            log(f"[{name}] {_line(f'设备指纹校验失败（9074），{wait}s 后重试并轮换设备')}")
            if pool and len(pool) > 1:
                headers = _trae_headers(acc, pool[(pool.index(device_id) + 1) % len(pool)] if device_id in pool else pool[0])
            time.sleep(wait)
            continue
        if c == 9095:
            log(f"[{name}] {_line('该设备今日已签过其它账号（9095），跳过')}")
            return False, _result(PLATFORM_TRAE, name, '设备已被占用（9095）'), None
        detail = _msg_of(claim[1]) or claim[2] or "未知错误"
        log(f"[{name}] {_line('签到失败', f'HTTP={claim[0]} code={c} {detail}')}")
        return False, _result(PLATFORM_TRAE, name, '签到失败', detail[:80]), acc if acc.get("accessToken") != _orig_token(acc) else None
    return False, _result(PLATFORM_TRAE, name, '签到重试后仍失败'), acc if acc.get("accessToken") != _orig_token(acc) else None


def _orig_token(acc: Dict[str, Any]) -> str:
    return acc.get("_orig_access_token", acc.get("accessToken", ""))


def _persist_trae(cfg: Dict[str, Any], acc: Dict[str, Any]) -> None:
    """把刷新后的 Trae 凭证写回 config.json（只更新 trae_accounts 段）。"""
    accounts = cfg.get("trae_accounts")
    if not isinstance(accounts, list):
        return
    for item in accounts:
        if item.get("userId") == acc.get("userId") and isinstance(item, dict):
            for k in ("accessToken", "refreshToken", "expiresAt"):
                if k in acc:
                    item[k] = acc[k]
    try:
        CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        log(f"[{acc.get('screenName') or acc.get('userId') or 'Trae账号'}] "
            f"{_line('凭证已写回 config.json')}")
    except OSError as exc:
        log(f"{_line('凭证写回失败', str(exc))}")


# --------------------------------------------------------------------------- #
# Trae 登录（浏览器 OAuth，移植参考仓库流程）
# --------------------------------------------------------------------------- #
def _jwt_subject(token: str) -> Optional[str]:
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)).decode("utf-8"))
    except Exception:
        return None
    return clean_text(payload.get("sub")) if isinstance(payload, dict) else None


def _parse_json_param(raw: Optional[str]) -> Optional[Dict[str, Any]]:
    """解析回调 URL 中可能是 JSON 字符串编码的参数（如 userInfo、userJwt）。"""
    if not raw:
        return None
    for val in (raw, urllib.parse.unquote(raw)):
        try:
            obj = json.loads(val)
            if isinstance(obj, dict):
                return obj
        except Exception:
            continue
    return None


def _find_free_port() -> int:
    for port in range(18080, 18100):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
            return port
        except OSError:
            continue
    raise RuntimeError("找不到可用回调端口（18080-18099）")


def trae_login() -> int:
    """浏览器 OAuth 登录 Trae（参考 Maquer/trae-signin 的 login.sh 流程）。"""
    captured: Dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path.rstrip("/") in ("/authorize", "/callback", ""):
                q = urllib.parse.parse_qs(parsed.query)
                for k, v in q.items():
                    captured[k] = v[0] if v else ""
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write("<html><body><h3>Trae 登录成功，可关闭此页面</h3></body></html>".encode("utf-8"))
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args):
            pass

    port = _find_free_port()
    server = HTTPServer(("127.0.0.1", port), Handler)
    machine_id = _resolve_trae_device_id("")
    device_id = secrets.token_hex(16)
    trace_id = secrets.token_hex(8)
    brand, dev_type, os_version = _trae_fingerprint()

    params = {
        "login_version": "1",
        "auth_from": "solo",
        "login_channel": "native_ide",
        "plugin_version": "2.3.62834",
        "auth_type": "local",
        "client_id": TRAE_CLIENT_ID,
        "redirect": "0",
        "login_trace_id": trace_id,
        "auth_callback_url": f"http://127.0.0.1:{port}/authorize",
        "machine_id": machine_id,
        "device_id": device_id,
        "x_device_id": device_id,
        "x_machine_id": machine_id,
        "x_device_brand": brand,
        "x_device_type": dev_type,
        "x_os_version": os_version,
        "x_app_version": TRAE_IDE_VERSION,
        "x_app_type": "stable",
    }
    auth_url = f"{TRAE_LOGIN_HOST}/authorization?" + urllib.parse.urlencode(params)

    log("=" * 60)
    log(f"  {PLATFORM_TRAE} 初始化（浏览器 OAuth 登录）")
    log("=" * 60)
    log("步骤：")
    log("  1. 在浏览器打开下面链接，用手机号/验证码或第三方登录")
    log("  2. 登录成功后浏览器会跳转到 127.0.0.1 并显示成功提示")
    log("  3. 回到这里查看结果")
    log("")
    log(f"登录链接：{auth_url}")
    log("")
    try:
        webbrowser.open(auth_url)
    except Exception:
        pass

    server.timeout = 1
    deadline = time.time() + 300
    while time.time() < deadline and not captured:
        server.handle_request()

    if not captured:
        log("登录超时或未捕获回调参数；请检查浏览器是否已完成授权")
        return 1

    # 回调参数：refreshToken、userInfo(JSON)、userJwt(JSON)
    refresh_token = captured.get("refreshToken") or captured.get("refresh_token")
    user_info = _parse_json_param(captured.get("userInfo")) or {}
    user_jwt = _parse_json_param(captured.get("userJwt")) or {}
    user_id = clean_text(user_info.get("UserID"))
    screen_name = clean_text(user_info.get("ScreenName"))

    jwt_token = clean_text(user_jwt.get("Token"))
    jwt_refresh = clean_text(user_jwt.get("RefreshToken"))
    if not refresh_token:
        refresh_token = jwt_refresh

    access_token = ""
    new_refresh = refresh_token
    expires_at = 0
    ua = f"Trae/{TRAE_IDE_VERSION}"

    if refresh_token:
        log("使用 refreshToken 换取 accessToken ...")
        body = {
            "ClientID": TRAE_CLIENT_ID,
            "RefreshToken": refresh_token,
            "ClientSecret": "-",
            "UserID": user_id,
        }
        exch = http_post(TRAE_EXCHANGE_URL, {"Content-Type": "application/json", "User-Agent": ua}, body, DEFAULT_TIMEOUT, DEFAULT_RETRIES)
        if exch[1] is None:
            log(f"ExchangeToken 失败：{exch[2]}")
            return 1
        result = exch[1].get("Result") if isinstance(exch[1].get("Result"), dict) else exch[1]
        access_token = clean_text(result.get("Token"))
        new_refresh = clean_text(result.get("RefreshToken")) or refresh_token
        expires_at = _to_int(result.get("TokenExpireAt"), 0)
        if expires_at > 10 ** 12:
            expires_at //= 1000
        if not access_token:
            log(f"ExchangeToken 响应缺少 Token：{json.dumps(redact(exch[1]), ensure_ascii=False)[:300]}")
            return 1
    else:
        access_token = jwt_token
        expires_at = _to_int(user_jwt.get("TokenExpireAt"), 0)
        if expires_at > 10 ** 12:
            expires_at //= 1000
        if not access_token:
            log("回调链接缺少 refreshToken/userJwt.Token，无法继续")
            return 1

    # 兜底：用 GetUserInfo 补全 uid/nickname
    if not user_id or not screen_name:
        try:
            ui = http_post(
                TRAE_EXCHANGE_HOST + "/cloudide/api/v3/trae/GetUserInfo",
                {"Content-Type": "application/json", "x-cloudide-token": access_token, "User-Agent": ua},
                {"ReqSource": "IDE", "IDEVersion": TRAE_IDE_VERSION},
                DEFAULT_TIMEOUT,
                DEFAULT_RETRIES,
            )
            u = ui[1].get("Result") if isinstance(ui[1].get("Result"), dict) else (ui[1] or {})
            if u.get("UserID"):
                user_id = clean_text(u.get("UserID")) or user_id
                screen_name = clean_text(u.get("ScreenName")) or screen_name
        except Exception as exc:
            log(f"GetUserInfo 失败：{exc}")

    if not user_id:
        user_id = _jwt_subject(access_token)
    if not user_id:
        log("无法从 token/回调解析 userId，请检查回调参数")
        return 1

    pool = _trae_device_pool()
    claim_device = pool[0] if pool else device_id
    cred = {
        "userId": user_id,
        "screenName": screen_name or user_id,
        "accessToken": access_token,
        "refreshToken": new_refresh,
        "expiresAt": expires_at or int(time.time()) + 1209600,
        "deviceId": device_id,
        "machineId": machine_id,
        "claimDeviceId": claim_device,
        "fingerprint": {"brand": brand, "type": dev_type, "os": os_version},
        "enabled": True,
    }
    cfg = load_config() if CONFIG_PATH.is_file() else {}
    accounts = cfg.get("trae_accounts")
    if not isinstance(accounts, list):
        accounts = []
    accounts = [a for a in accounts if a.get("userId") != user_id]
    accounts.append(cred)
    cfg["trae_accounts"] = accounts
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log(f"{PLATFORM_TRAE} 初始化完成：已写入 config.json 的 trae_accounts（userId={user_id}，"
        f"claimDevice={claim_device[:8]}***）；直接运行 `python checkin.py` 即可签到")
    return 0


# =========================================================================== #
# 通知（内置，移植 notify.py：Webhook + 企业微信应用，双通道）
# =========================================================================== #
def _webhook_mask(url: str) -> str:
    return _mask_url(url)


def _send_wecom_app(notify: Dict[str, Any], title: str, content: str) -> bool:
    wecom = notify.get("wecom") or {}
    corpid = clean_text(wecom.get("corpid"))
    corpsecret = clean_text(wecom.get("corpsecret"))
    agentid = clean_text(wecom.get("agentid"))
    touser = clean_text(wecom.get("touser")) or "@all"
    if not (corpid and corpsecret and agentid):
        return False
    tok, _, err = http_get(
        f"https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid={corpid}&corpsecret={corpsecret}",
        {}, DEFAULT_TIMEOUT,
    )
    if tok is None or not isinstance(tok, dict) or tok.get("errcode") not in (0, None):
        log(f"企业微信获取 token 失败：{tok.get('errmsg') if isinstance(tok, dict) else err}")
        return False
    access = tok.get("access_token")
    body = {"touser": touser, "msgtype": "text", "agentid": int(agentid) if agentid.isdigit() else agentid,
            "text": {"content": f"{title}\n\n{content}"}}
    send_url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={access}"
    st, pl, er = http_post(send_url, {}, body, DEFAULT_TIMEOUT, DEFAULT_RETRIES)
    if st == 200 and isinstance(pl, dict) and pl.get("errcode") == 0:
        return True
    log(f"企业微信推送失败：{pl.get('errmsg') if isinstance(pl, dict) else er}")
    return False


def _send_webhook(url: str, title: str, content: str) -> bool:
    url = clean_text(url)
    if not url:
        return False
    text = f"{title}\n\n{content}"
    if "qyapi.weixin.qq.com" in url and "webhook" in url:
        body = {"msgtype": "markdown", "markdown": {"content": text.replace("\n", "\n\n")}}
    elif "sctapi.ftqq.com" in url or "sc.ftqq.com" in url:
        body = {"title": title, "desp": content}
    elif "dingtalk.com" in url:
        body = {"msgtype": "text", "text": {"content": text}}
    elif "feishu.cn" in url or "larksuite.com" in url:
        body = {"msg_type": "text", "content": {"text": text}}
    elif "api.day.app" in url:
        body = {"title": title, "body": content, "device_key": ""}
    else:
        body = {"msgtype": "text", "text": {"content": text}}
    st, pl, er = http_post(url, {}, body, DEFAULT_TIMEOUT, DEFAULT_RETRIES)
    if st == 200:
        return True
    log(f"Webhook 推送失败：HTTP={st} {er}")
    return False


def send_notify(notify: Dict[str, Any], title: str, content: str) -> bool:
    if not isinstance(notify, dict):
        return False
    ok = False
    webhook = clean_text((notify.get("webhook") or {}).get("url"))
    if webhook:
        ok = _send_webhook(webhook, title, content) or ok
    if _send_wecom_app(notify, title, content):
        ok = True
    if not ok:
        log("通知：未配置有效渠道或推送失败（不影响签到）")
    return ok


# =========================================================================== #
# 编排：去重 + 失败限流（移植 auto_checkin.py）
# =========================================================================== #
MAX_FAIL_ALERTS = env_int("AICREDIT_MAX_FAIL_ALERTS", 3, 0, 100)
MIN_FAIL_INTERVAL = env_int("AICREDIT_FAIL_ALERT_INTERVAL", 60, 1, 100000) * 60


def load_state() -> Dict[str, Any]:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: Dict[str, Any]) -> None:
    try:
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        log(f"状态写入失败（不影响签到）：{exc}")


def orchestrate(platform: str, results: List[Tuple[bool, str]], notify: Dict[str, Any],
                cfg: Dict[str, Any]) -> Tuple[int, bool]:
    """推送编排（去重 + 失败限流），返回 `(退出码, 本次是否为当日首次成功推送)`。"""
    today = date.today().isoformat()
    state = load_state()
    key_ok = f"success_date_{platform}"
    key_fail = f"fail_date_{platform}"
    label = PLATFORM_LABELS.get(platform, platform)
    success = all(ok for ok, _ in results)
    summary = "\n".join(t for _, t in results)
    first_today = False

    if success:
        if state.get(key_ok) == today:
            log(f"{label} 今日已推送成功通知，本次静默跳过")
        else:
            send_notify(notify, f"{label} 签到成功", summary)
            state[key_ok] = today
            first_today = True
        if state.get(key_fail) == today:
            state["fail_count"] = 0
    else:
        if state.get(key_fail) != today:
            state[key_fail] = today
            state["fail_count"] = 0
            state["last_fail_ts"] = 0.0
        count = int(state.get("fail_count", 0))
        last = float(state.get("last_fail_ts", 0.0))
        now = time.time()
        if count < MAX_FAIL_ALERTS and (now - last) >= MIN_FAIL_INTERVAL:
            send_notify(notify, f"{label} 签到失败", summary)
            state["fail_count"] = count + 1
            state["last_fail_ts"] = now
        else:
            log(f"{label} 失败，但今日已推送 {count} 条（上限 {MAX_FAIL_ALERTS}）或间隔不足，跳过推送")
    save_state(state)
    return (0 if success else 1), first_today


# =========================================================================== #
# 计划任务检查（只读） / 日常查看（只读，纯本地）
# =========================================================================== #
_WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
_TASK_RESULTS = {0: "成功", 0x41301: "正在运行", 0x41303: "从未运行"}


def _ps(script: str) -> Optional[str]:
    """执行一段 PowerShell 并返回 stdout；找不到 PowerShell 或执行失败返回 None。"""
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        return None
    try:
        proc = subprocess.run(
            [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=40,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    return (proc.stdout or "").strip()


def _task_snapshot() -> Optional[Dict[str, Dict[str, Any]]]:
    """查询三个计划任务：是否存在 / 动作 / 下次运行 / 上次运行与结果。

    属性名一律用英文（`name` / `action` / `state` / `next` / `last` / `result`），
    避开 `schtasks /fo LIST /v` 那种「字段名随系统语言变化」的解析坑。
    PowerShell 不可用时返回 None，调用方退化为「只判存在」。
    """
    names = ",".join("'%s'" % n for n in (TASK_DAILY, TASK_STARTUP, TASK_RESUME))
    script = (
        "$o=@();"
        "foreach($n in @(__NAMES__)){"
        "$t=Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue;"
        "if($t){"
        "$i=Get-ScheduledTaskInfo -TaskName $n;"
        "$a=@();foreach($x in $t.Actions){$a+=($x.Execute+' '+$x.Arguments)};"
        "$r=if($null -eq $i.LastTaskResult){''}else{'0x{0:X}' -f $i.LastTaskResult};"
        "$o+=[pscustomobject]@{name=$n;found=$true;state=[string]$t.State;"
        "action=($a -join ' | ');"
        "next=if($i.NextRunTime){$i.NextRunTime.ToString('yyyy-MM-dd HH:mm')}else{''};"
        "last=if($i.LastRunTime -and $i.LastRunTime.Year -gt 2000){$i.LastRunTime.ToString('yyyy-MM-dd HH:mm')}else{''};"
        "result=$r}"
        "}else{"
        "$o+=[pscustomobject]@{name=$n;found=$false;state='';action='';next='';last='';result=''}"
        "}}"
        "$o|ConvertTo-Json -Compress"
    ).replace("__NAMES__", names)
    raw = _ps(script)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return None
    snapshot: Dict[str, Dict[str, Any]] = {}
    for item in data:
        if isinstance(item, dict) and item.get("name"):
            snapshot[str(item["name"])] = item
    return snapshot or None


def _task_exists_via_schtasks(name: str) -> bool:
    """退化的存在性判断（PowerShell 不可用时兜底）。"""
    try:
        return subprocess.run(["schtasks", "/query", "/tn", name],
                              capture_output=True, timeout=20).returncode == 0
    except Exception:
        return False


def _task_result_text(result: Any) -> str:
    raw = str(result or "").strip()
    if not raw:
        return "未知"
    try:
        code = int(raw, 16)
    except ValueError:
        return raw
    return f"{_TASK_RESULTS.get(code, '异常')}（{raw}）"


def show_tasks() -> int:
    """检查本脚本的三个计划任务（只读，不做任何修改）。

    返回 0 = 三个任务都正常；1 = 缺失 / 指向别处 / 查不到。
    """
    log("===== 计划任务检查 =====")
    if os.name != "nt":
        log("计划任务检查只适用于 Windows；Linux / macOS 请用 crontab（见 README §3.5）。")
        return 0

    entry = str(ENTRY_PATH)
    entry_lc = entry.lower()
    items = (
        (TASK_DAILY, f"每日 {len(CHECKIN_TIMES)} 次（{'、'.join(CHECKIN_TIMES)}）"),
        (TASK_STARTUP, "用户登录时触发"),
        (TASK_RESUME, "从睡眠 / 休眠恢复时触发"),
    )
    log(f"入口脚本：{entry}")
    if not ENTRY_PATH.exists():
        log(_line("异常", f"当前目录下找不到 {ENTRY_BAT}，请确认脚本是否被移动或删除"))
    log("")

    snapshot = _task_snapshot()
    problems = 0
    for name, trigger in items:
        info = (snapshot or {}).get(name)
        if snapshot is None:
            # PowerShell 不可用：只判存在
            exists = _task_exists_via_schtasks(name)
            problems += 0 if exists else 1
            log(f"[{name}] " + _line("已注册" if exists else "缺失",
                                     f"{trigger}；读不到详情（PowerShell 不可用）"))
            continue
        if not info or not info.get("found"):
            problems += 1
            log(f"[{name}] " + _line("缺失", f"未找到同名计划任务；{trigger}"))
            continue

        detail = [trigger]
        action = str(info.get("action") or "")
        if entry_lc and entry_lc in action.lower():
            status = "正常"
        else:
            problems += 1
            status = "异常"
            detail.append(f"动作指向 {action or '未知'}，不是当前目录的 {ENTRY_BAT}")
        if info.get("next"):
            detail.append(f"下次 {info['next']}")
        if info.get("last"):
            detail.append(f"上次 {info['last']}")
        detail.append("上次结果 " + _task_result_text(info.get("result")))
        log(f"[{name}] " + _line(status, "；".join(detail)))

    total = len(items)
    log("")
    if problems == 0:
        log(f"汇总：{total}/{total} 个计划任务正常，均指向 {entry}")
        log("要强制重装或修复：`checkin.bat --install`；直接跑一次 `checkin.bat` 也会自动补齐。")
        return 0
    log(f"汇总：{total - problems}/{total} 个计划任务正常，需要修复")
    log("修复：运行 `checkin.bat --install` 重新注册三个任务（普通账户权限即可，无需管理员）。")
    return 1


def _read_log_head(limit: int = 200_000) -> str:
    """读取日志开头 —— 每次运行都被前置到最顶端，所以最新内容在文件头部。"""
    try:
        return LOG_FILE.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def _log_blocks(text: str) -> List[List[str]]:
    """把日志切成运行块（块与块之间用空行分隔），第 0 块就是最新一次运行。"""
    blocks: List[List[str]] = []
    current: List[str] = []
    for raw in text.splitlines():
        if raw.strip():
            current.append(raw)
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    return blocks


def show_today() -> int:
    """日常查看：今日签到状态 + 计划任务下一班次 + 最近一次运行摘要。

    **纯本地只读，不发送任何网络请求**，随时可跑。返回 0 = 今日各平台都已签到，1 = 有待办。
    """
    today = date.today()
    today_iso = today.isoformat()
    state = load_state()
    text = _read_log_head()
    blocks = _log_blocks(text)

    log("===== 日常查看 =====")
    log(f"日期：{today_iso}（{_WEEKDAYS[today.weekday()]}）")

    try:
        cfg = load_config()
    except Exception:
        cfg = {}
    accounts = {
        "workbuddy": workbuddy_accounts(cfg) if cfg.get("accounts") else [],
        "trae": trae_accounts(cfg) if cfg.get("trae_accounts") else [],
    }

    pending = 0
    for key in ("workbuddy", "trae"):
        label = PLATFORM_LABELS[key]
        init_cmd = INIT_CMD_WORKBUDDY if key == "workbuddy" else INIT_CMD_TRAE
        if not accounts[key]:
            log(f"[{label}] " + _line("未初始化", f"请先运行 `{init_cmd}`"))
            continue
        count = f"{len(accounts[key])} 个账号"
        done_date = state.get(f"success_date_{key}")
        if done_date == today_iso:
            log(f"[{label}] " + _line("今日已签到", f"{count}；成功记录 {done_date}"))
        else:
            pending += 1
            log(f"[{label}] " + _line("今日尚未签到", f"{count}；上次成功 {done_date or '无记录'}"))
        if state.get(f"fail_date_{key}") == today_iso:
            log(f"[{label}] " + _line("今日有失败记录",
                                     f"已推送失败告警 {_to_int(state.get('fail_count'))} 条（上限 {MAX_FAIL_ALERTS}）"))

    runs_today = sum(1 for block in blocks for line in block
                     if line.startswith(f"[{today_iso}") and "启动" in line)
    log(f"[运行记录] " + _line(f"日志中今日 {runs_today} 次",
                              "含计划任务触发与手动运行" if runs_today else "今天还没有运行记录"))

    if os.name == "nt":
        snapshot = _task_snapshot()
        if snapshot is not None:
            daily = snapshot.get(TASK_DAILY) or {}
            if daily.get("found"):
                log(f"[计划任务] " + _line("已注册",
                                          f"下次自动运行 {daily.get('next') or '待定'}；"
                                          f"上次 {daily.get('last') or '从未运行'}"))
            else:
                pending += 1
                log(f"[计划任务] " + _line("缺失", "自动签到不会触发，运行 `checkin.bat --install` 修复"))
            resume = snapshot.get(TASK_RESUME) or {}
            if resume.get("found"):
                log(f"[计划任务] " + _line("唤醒触发已启用",
                                          f"从睡眠 / 休眠恢复时补签；上次 {resume.get('last') or '从未运行'}"))
            else:
                pending += 1
                log(f"[计划任务] " + _line("唤醒触发缺失",
                                          "睡眠恢复后不会补签，运行 `checkin.bat --install` 修复"))

    log(f"[日志文件] " + _line("最新在前", str(LOG_FILE)))

    log("")
    if blocks:
        log("最近一次运行（日志顶部）：")
        for line in blocks[0][:40]:
            log(f"  {line}")
        if len(blocks[0]) > 40:
            log(f"  ...本块另有 {len(blocks[0]) - 40} 行，完整内容见日志文件")
    else:
        log("最近一次运行：日志为空或尚未生成（日志由 checkin.bat 写入）")

    log("")
    configured = sum(1 for key in accounts if accounts[key])
    if not configured:
        log("提示：两个平台都还没初始化，先跑 `python checkin.py --init` 完成初始化（见 README §2.2）。")
        return 1
    if pending:
        log("提示：仍有待办项。手动补一次签到用 `checkin.bat`；在线复查余额用 "
            "`python checkin.py --status-only`。")
        return 1
    log("提示：今日两个平台都已完成签到，无需处理。")
    return 0


# =========================================================================== #
# 主流程
# =========================================================================== #
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="一体化每日签到（WorkBuddy + Trae）",
        epilog="初始化：--init-workbuddy（导入本机凭据）/ --init-trae（浏览器登录）/ --init（两者依次）；"
               "查看：--today（今日状态，纯本地）/ --tasks（计划任务检查）",
    )
    p.add_argument("--init", action="store_true", help="依次初始化 WorkBuddy 与 Trae")
    p.add_argument("--init-workbuddy", action="store_true",
                   help="初始化 WorkBuddy：导入本机桌面端登录凭据（可配合 --auth-file）")
    p.add_argument("--init-trae", action="store_true",
                   help="初始化 Trae：浏览器 OAuth 登录")
    p.add_argument("--auth-file", help="【--init-workbuddy】指定 workbuddy-desktop.info 路径（默认自动发现）")
    p.add_argument("--workbuddy-only", action="store_true", help="只跑 WorkBuddy 平台")
    p.add_argument("--trae-only", action="store_true", help="只跑 Trae 平台")
    p.add_argument("--status-only", action="store_true", help="只查询签到状态，不尝试领取")
    p.add_argument("--today", "--view", action="store_true", dest="today",
                   help="日常查看：今日签到状态、计划任务下一班次、最近一次运行摘要（纯本地，不联网）")
    p.add_argument("--tasks", action="store_true", dest="tasks",
                   help="检查本脚本的三个计划任务是否注册、是否指向当前目录（只读）")
    p.add_argument("--dry-run", action="store_true", help="只加载并校验配置，不发送网络请求")
    p.add_argument("--debug", action="store_true", help="打印脱敏的原始响应，用于排错")
    p.add_argument("--version", action="version", version=VERSION)
    return p.parse_args()


def _print_init_hint() -> None:
    """未初始化 / 配置缺失时，打印平台化的初始化指引（与 --init-* 命令一一对应）。"""
    log("尚未初始化，请先执行下面至少一条初始化命令：")
    log(f"  {INIT_CMD_WORKBUDDY}     # WorkBuddy：导入本机桌面端登录凭据（自动发现）")
    log(f"  {INIT_CMD_TRAE}          # Trae：打开浏览器完成 OAuth 登录")
    log("  python checkin.py --init               # 两个平台依次初始化")
    log("初始化完成后直接运行 `python checkin.py` 即可签到；详见 README §2.2。")


def main() -> int:
    global DEBUG
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = parse_args()
    DEBUG = bool(args.debug or env_bool("WORKBUDDY_DEBUG", False))

    # 只读的查看类命令：不加载/不校验账号，也不发任何网络请求
    if args.tasks:
        log(f"一体化每日签到脚本 v{VERSION} 启动（计划任务检查）")
        rc = show_tasks()
        log("本次脚本执行完毕。")
        log("")
        return rc

    if args.today:
        log(f"一体化每日签到脚本 v{VERSION} 启动（日常查看）")
        rc = show_today()
        log("本次脚本执行完毕。")
        log("")
        return rc

    if args.init:
        log(f"一体化每日签到脚本 v{VERSION} 启动（初始化模式）")
        rc = setup_workbuddy(args.auth_file)
        rc |= trae_login()
        log("本次脚本执行完毕。")
        log("")
        return rc

    if args.init_workbuddy or args.init_trae:
        log(f"一体化每日签到脚本 v{VERSION} 启动（初始化模式）")
        rc = 0
        if args.init_workbuddy:
            rc |= setup_workbuddy(args.auth_file)
        if args.init_trae:
            rc |= trae_login()
        log("本次脚本执行完毕。")
        log("")
        return rc

    log(f"一体化每日签到脚本 v{VERSION} 启动")

    timeout = env_int("WORKBUDDY_TIMEOUT", DEFAULT_TIMEOUT, 5, 120)
    retries = env_int("WORKBUDDY_RETRIES", DEFAULT_RETRIES, 0, 5)

    try:
        cfg = load_config()
    except Exception as exc:
        log(f"配置错误：{exc}")
        _print_init_hint()
        log("本次脚本执行完毕。")
        log("")
        return 1

    wb = workbuddy_accounts(cfg) if cfg.get("accounts") else []
    trae = trae_accounts(cfg) if cfg.get("trae_accounts") else []
    notify = cfg.get("notify") or {}

    # 标记 _orig_access_token 用于判断 refresh 后是否需回写
    for a in trae:
        a["_orig_access_token"] = a.get("accessToken", "")

    if args.dry_run:
        log(f"Dry-run：{PLATFORM_WORKBUDDY} 账号 {len(wb)} 个；{PLATFORM_TRAE} 账号 {len(trae)} 个")
        log("本次脚本执行完毕。")
        log("")
        return 0

    rc = 0
    first_today = False
    if not args.trae_only and wb:
        log(f"===== {PLATFORM_WORKBUDDY} =====")
        results = [run_workbuddy(a, args.status_only, timeout, retries) for a in wb]
        trc, first = orchestrate("workbuddy", results, notify, cfg)
        rc |= trc
        first_today = first_today or first
    elif not args.trae_only and not wb:
        log(f"{PLATFORM_WORKBUDDY}：尚未初始化，请先运行 `{INIT_CMD_WORKBUDDY}`（导入本机登录凭据）")

    if not args.workbuddy_only and trae:
        log(f"===== {PLATFORM_TRAE} =====")
        results: List[Tuple[bool, str]] = []
        for a in trae:
            ok, msg, updated = run_trae(a, args.status_only, timeout, retries, cfg)
            if updated:
                _persist_trae(cfg, updated)
            results.append((ok, msg))
        trc, first = orchestrate("trae", results, notify, cfg)
        rc |= trc
        first_today = first_today or first
    elif not args.workbuddy_only and not trae:
        log(f"{PLATFORM_TRAE}：尚未初始化，请先运行 `{INIT_CMD_TRAE}`（打开浏览器完成登录）")

    if not wb and not trae:
        _print_init_hint()
        log("本次脚本执行完毕。")
        log("")
        return 1

    log("本次脚本执行完毕。")
    log("")
    if first_today:
        log(f"当日首次签到，正在用记事本打开日志：{LOG_FILE}")
        _request_open_log()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
