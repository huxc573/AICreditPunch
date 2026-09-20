#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一体化每日签到脚本（WorkBuddy + Trae），仅依赖 Python 标准库。"""

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
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

VERSION = "1.9.0"
DEFAULT_TIMEOUT = 20
DEFAULT_RETRIES = 2
DEFAULT_CONCURRENCY = 4      # 并发跑账号的线程数上限；1 = 串行
STREAM_TICK = 0.2            # 并发收集的轮询间隔（秒）：活动行备好就出，不必等账号跑完
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
# 日志行前缀的时间戳：本轮第一行（运行头）是 `[26.09.17 09:33:06]`，之后只留 `[09:33:08]` ——
# 一次运行都在同一天，日期重复十几遍纯属占宽。bat 的 `:now` 仍写完整格式（bat 自己只写少数几行）。
LOG_DAY_FMT = "%y.%m.%d"
LOG_TIME_FMT = "%H:%M:%S"
LOG_TS_FMT = LOG_DAY_FMT + " " + LOG_TIME_FMT

# 计划任务名 / 触发时刻 / 入口 bat —— 必须与 checkin.bat 顶部的同名常量保持一致，
# 改一边就得改另一边。这里只用于「检查」，Python 侧不注册任务。
TASK_DAILY = "AICreditPunch-Daily"
TASK_STARTUP = "AICreditPunch-Startup"
TASK_RESUME = "AICreditPunch-Resume"
CHECKIN_TIMES = ["08:45", "11:45", "14:45", "17:45", "20:45", "23:45"]
ENTRY_BAT = "checkin.bat"
ENTRY_VBS = "run-hidden.vbs"  # 计划任务动作的隐藏窗口包装器（wscript.exe 执行）
ENTRY_PATH = HERE / ENTRY_BAT


def log_file_path() -> Path:
    """日志文件位置：直接放在 `%APPDATA%` 根下，即 `%APPDATA%\\AICreditPunch.log`。"""
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

# 本次运行的「输出副本」：由 checkin.bat 用环境变量 ACP_RUN_LOG 指定一个临时文件。
# bat 要的是「控制台 + 日志」双份输出：Python 把每行实时打到控制台（--auto 隐藏运行时
# 输出进的是隐藏控制台，等于静默），同时向该文件追加一份；bat 结束后把它前置到正式日志顶端。
RUN_LOG_FILE = os.environ.get("ACP_RUN_LOG", "").strip()

# 由 checkin.bat 在 --auto（计划任务触发）时设置：关掉控制台输出。隐藏窗口本来也看
# 不到，但显式关掉更稳 —— 万一任务动作被改回可见的 cmd，也不会弹出一堆输出。
CONSOLE_OUTPUT = os.environ.get("ACP_QUIET", "").strip().lower() not in ("1", "true", "yes", "on")


def _append_run_log(line: str) -> None:
    """把一行同时写进 bat 指定的运行副本文件；未指定或写失败都不影响主流程。"""
    if not RUN_LOG_FILE:
        return
    try:
        with open(RUN_LOG_FILE, "a", encoding="utf-8", newline="\r\n") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# 通用工具
# --------------------------------------------------------------------------- #
# 本轮是否已经打过带日期的运行头（正文行只留时分秒，见 LOG_TIME_FMT）。
_HEADER_LOGGED = False

# 并发跑账号时，各线程先把输出行攒进自己的线程缓冲，该账号一跑完就由主线程整块打出来
# （见 `_map_accounts` / `_flush_block`）：签完一个出一个，不必等整平台跑完。
_LOG_BUFFER = threading.local()


def log(message: str) -> None:
    """落一行输出。并发跑账号时先攒进线程缓冲，等该账号跑完由主线程整块打（见 `_capture`）。"""
    buffered = getattr(_LOG_BUFFER, "lines", None)
    if buffered is not None:
        buffered.append(message)    # 只攒内容：时刻统一盖在打印那一下，免得块内时间倒序
        return
    _emit(datetime.now(), message)


def _emit(when: datetime, message: str) -> None:
    """真正输出一行：`[时间] 内容`（时刻由调用方给）；空串 = 纯换行（块间留白），不带时间戳、不消耗运行头。"""
    global _HEADER_LOGGED
    if not message:
        if CONSOLE_OUTPUT:
            print("", flush=True)
        _append_run_log("")
        return
    fmt = LOG_TIME_FMT if _HEADER_LOGGED else LOG_TS_FMT
    _HEADER_LOGGED = True
    line = f"[{when.strftime(fmt)}] {message}"
    if CONSOLE_OUTPUT:
        try:
            print(line, flush=True)
        except UnicodeEncodeError:
            print(line.encode("utf-8", errors="replace").decode("utf-8", errors="replace"), flush=True)
    # 控制台已经实时显示；再写一份给 bat 前置到正式日志
    _append_run_log(line)


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
    """请求「打开日志」，按调用方决定时机。"""
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
def _num_of(value: Any) -> Optional[float]:
    """把报文里的数值字段转成 float：兼容数字与 `"146.6800001"` 这类字符串。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = clean_text(value)
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _fmt_num(value: Any) -> str:
    """积分数字文案：整数值不带小数（`2,100`），非整数保留两位（`146.68`）。"""
    num = _num_of(value)
    if num is None:
        return str(value)
    num = round(num, 2)
    if num == int(num):
        return f"{int(num):,}"
    return f"{num:,.2f}"


def _int_of(value: Any) -> Optional[int]:
    """整数取值：bool / 非数值一律 None（`0` 原样返回，是否省略交给调用方）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _md_of(value: Any) -> str:
    """报文时间戳取月日：`2026-09-16 00:00:00` → `09-16`；识别不了返回空串。"""
    text = clean_text(value)
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[5:10]
    return ""


def _date_of(value: Any) -> Optional[date]:
    """报文时间戳取日期；识别不了返回 None。"""
    try:
        return datetime.strptime(clean_text(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _window_note(active: Any, end: Any, today: Optional[date] = None) -> str:
    """活动窗口补充说明：`（剩13天，进行中）` / `（今天截止）` / `（已结束）`；无可用信息返回空串。"""
    notes: List[str] = []
    stop = _date_of(end)
    if stop is not None:
        left = (stop - (today or date.today())).days
        if left > 0:
            notes.append(f"剩{left}天")
        elif left == 0:
            notes.append("今天截止")
    if isinstance(active, bool):
        notes.append("进行中" if active else "已结束")
    return f"（{'，'.join(notes)}）" if notes else ""


def _fmt_credit(today: Optional[int] = None, balance: Any = None) -> str:
    """积分明细文本（中文）；字段缺失自动省略，保证两个平台写法一致。"""
    items = []
    if today is not None:
        items.append(f"本次 +{_fmt_num(today)}")
    if balance is not None:
        items.append(f"余额{_fmt_num(balance)}")
    return "，".join(items)


def _line(state: str, detail: str = "") -> str:
    """日志正文：`{状态}；{明细}`。"""
    return state + (f"；{detail}" if detail else "")


def _result(platform: str, name: str, state: str, detail: str = "") -> str:
    """对外结果文案（通知正文）：`[{平台}] {账号名} {状态}；{明细}`，一个账号一行。"""
    return f"[{platform}] {name} {state}" + (f"；{detail}" if detail else "")


def _credit_of(text: str) -> Optional[int]:
    """结果行里的「本次 +N」数字；失败行没有这一项，返回 None。

    认的就是 `_fmt_credit` 那一种写法（自己刚拼出来的文案），别处不要复用这个口径。
    """
    matched = re.search(r"本次 \+([\d,]+)", text or "")
    return int(matched.group(1).replace(",", "")) if matched else None


def _run_summary(results: List[Tuple[bool, str]]) -> str:
    """收尾一句：`合计：3 个账号全部成功，本次 +350`；本轮没有账号参战则返回空串。"""
    if not results:
        return ""
    total = len(results)
    ok = sum(1 for flag, _ in results if flag)
    head = (f"合计：{total} 个账号全部成功" if ok == total
            else f"合计：{total} 个账号，{ok} 成功 {total - ok} 失败")
    got = sum(c for c in (_credit_of(msg) for _, msg in results) if c)
    return head + (f"，本次 +{_fmt_num(got)}" if got else "")


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
WB_STREAK_BONUS_KEY = "streak_bonus_credit"
WB_DATES_KEY = "checkin_dates"
WB_ACTIVITY_NAME_KEY = "activity_name"
WB_SEASON_KEY = "season"
WB_START_KEY = "start_time"
WB_END_KEY = "end_time"
WB_ACTIVE_KEY = "active"
WB_REQUEST_ID_KEY = "requestId"
WB_ACCOUNT_LABEL = "WorkBuddy账号"

# 账号真实可用积分（与桌面端「设置 - 套餐与积分」同源）。
# 注意这三条路由**不带 `/v2`** 前缀，与签到那两条不同；网关前缀差异见客户端注释。
WB_ROUTE_RESOURCE_SUMMARY = "/billing/meter/get-user-resource-summary"
WB_ROUTE_FREE_PACKAGES = "/billing/meter/get-user-resource-free-packages"
WB_ROUTE_PAID_PACKAGES = "/billing/meter/get-user-resource-paid-packages"
WB_PACKAGE_STATUS = [0, 3]          # 0=有效、3=已用尽（与桌面端请求体一致）
WB_PACKAGE_PAGE_SIZE = 100
# 「平台奖励积分」的标识：赠送包（`sp_tcaca_codebuddyide_bonus_pack`）。
# 免费包列表里既有赠送包也有套餐本体（如 `sp_tcaca_codebuddy_ide` = 个人体验版），
# 不区分就会把套餐基础积分错算进平台奖励（实测踩过）。
WB_BONUS_MARK = "bonus_pack"


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
    """一次调用的「HTTP 码 + 报文」的语义化解读。"""

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
    def paid_in(self) -> Tuple[Optional[int], Optional[int]]:
        """(本次基础积分, 连签奖励积分)；缺失一律 None。"""
        scope = self._scope()
        today = next((scope[key] for key in WB_TODAY_CREDIT_KEYS if scope.get(key) is not None), None)
        return _int_of(today), _int_of(scope.get(WB_STREAK_BONUS_KEY))

    @property
    def credit_text(self) -> str:
        """`本次 +100（含连签奖励 +50），余额…`；奖励为 0 或缺字段时不加括号。"""
        today, bonus = self.paid_in
        text = _fmt_credit(today=today)
        if bonus:
            text += f"（含连签奖励 +{_fmt_num(bonus)}）"
        return text

    @property
    def streak(self) -> Optional[int]:
        """本期连续签到天数（口径见 README §2.8：换期归零，跨期不累计）。"""
        return _int_of(self._scope().get(WB_STREAK_KEY))

    @property
    def checkin_days(self) -> Optional[int]:
        """本期已签到天数（`checkin_dates` 长度）；字段缺失 None。"""
        dates = self._scope().get(WB_DATES_KEY)
        return len(dates) if isinstance(dates, list) else None

    @property
    def activity_text(self) -> str:
        """本期活动：`高校新生攻略 第9期 09-16~09-29（剩13天，进行中）`；字段缺失逐项省略。"""
        scope = self._scope()
        title = clean_text(scope.get(WB_ACTIVITY_NAME_KEY))
        season = _int_of(scope.get(WB_SEASON_KEY))
        head = " ".join(p for p in (title, f"第{season}期" if season else "") if p)
        start, end = _md_of(scope.get(WB_START_KEY)), _md_of(scope.get(WB_END_KEY))
        window = f"{start}~{end}" if start and end else (end or start)
        note = _window_note(scope.get(WB_ACTIVE_KEY), scope.get(WB_END_KEY))
        return " ".join(p for p in (head, window) if p) + note

    @property
    def streak_text(self) -> str:
        """账号级的 `连签2/已签2`；字段缺失逐项省略（只在签到结算后用，见 `_settle`）。"""
        streak, days = self.streak, self.checkin_days
        return "/".join(p for p in [f"连签{streak}" if streak else "",
                                    f"已签{days}" if days else ""] if p)

    @property
    def request_id(self) -> str:
        """报文 requestId（顶层字段）：出错时便于自查与反馈；缺失返回空串。"""
        return clean_text((self.payload or {}).get(WB_REQUEST_ID_KEY))

    @property
    def reason(self) -> str:
        """失败原因：优先报文里的 message，其次网络层错误。"""
        return self.message or self.transport_error or "未知错误"


# 平台级活动行只打一次：同平台的账号看到的是同一期活动（同一份 `activity_name` / `season`），
# 每个账号再打一遍纯属重复。进程就是一次运行，所以这个标记不需要重置。
_ACTIVITY_SHOWN: List[str] = []
# 备好、待打印的活动行：并发跑时由主线程的收集循环随时打掉（要排在账号行前面）。
_ACTIVITY_PENDING: List[str] = []
# `_ACTIVITY_SHOWN` 的「查过没」与「记下来」要原子：并发下几个账号可能同时查到状态，
# 不锁的话这行会重复打。
_ACTIVITY_LOCK = threading.Lock()


class WorkBuddyClient:
    """一个账号的一次签到会话：发请求、把响应翻译成日志行与结果文案。"""

    def __init__(self, record: Dict[str, Any], timeout: int, retries: int) -> None:
        self.record = record
        self.timeout = timeout
        self.retries = retries
        self.name = clean_text(record.get("name")) or WB_ACCOUNT_LABEL
        self.base = wb_validated_base(record.get("api_base"))
        self._headers = self._build_headers()
        self._status: Optional[WbReply] = None      # 首次状态查询报文，供活动期字段兜底

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
        http, payload, error = self._raw(route, {})
        return WbReply(http, payload, error)

    def _raw(self, route: str, body: Dict[str, Any]) -> Tuple[Optional[int], Any, Optional[str]]:
        """原样发一次 POST，返回 (HTTP 码, 报文, 传输层错误)。"""
        return http_post(self.base + route, self._headers, body, self.timeout, self.retries)

    def _packages_remain(self, route: str, codes: List[str], extra: Dict[str, Any],
                         sub_mark: str = "") -> Optional[float]:
        """某个资源包接口下各包剩余额度之和；取不到返回 None（不猜 0）。"""
        body: Dict[str, Any] = {
            "PageNumber": 1,
            "PageSize": WB_PACKAGE_PAGE_SIZE,
            "PackageCodes": codes,
            "Status": list(WB_PACKAGE_STATUS),
        }
        body.update(extra)
        _, payload, _ = self._raw(route, body)
        data = (payload or {}).get("data") if isinstance(payload, dict) else None
        accounts = data.get("Accounts") if isinstance(data, dict) else None
        if not isinstance(accounts, list):
            return None
        total = 0.0
        for item in accounts:
            if not isinstance(item, dict):
                continue
            if sub_mark and sub_mark not in (clean_text(item.get("SubProductCode")) or "").lower():
                continue
            num = _num_of(item.get("CycleCapacityRemainPrecise"))
            if num is None:
                num = _num_of(item.get("CycleCapacityRemain"))
            if num is not None:
                total += num
        return total

    def balance(self) -> Tuple[Optional[str], Optional[str]]:
        """账号真实可用积分，返回 (余额文本, 构成文本)；查不到一律 (None, None)。"""
        _, payload, _ = self._raw(WB_ROUTE_RESOURCE_SUMMARY, {})
        data = (payload or {}).get("data") if isinstance(payload, dict) else None
        packages = data.get("Packages") if isinstance(data, dict) else None
        if not isinstance(packages, list) or not packages:
            return None, None
        remains = [_num_of(p.get("CycleRemainCapacity")) for p in packages if isinstance(p, dict)]
        remains = [r for r in remains if r is not None]
        if not remains:
            return None, None
        total = sum(remains)
        codes = [clean_text(p.get("PackageCode")) for p in packages if isinstance(p, dict)]
        codes = [c for c in codes if c]
        text = _fmt_num(total)
        if not codes:
            return text, None

        reward = self._packages_remain(WB_ROUTE_FREE_PACKAGES, codes, {}, WB_BONUS_MARK)
        paid = self._packages_remain(WB_ROUTE_PAID_PACKAGES, codes, {"NeedRenewInfo": True})
        if reward is None or paid is None:
            return text, None
        base = total - reward - paid
        if base < 0:                      # 口径对不上就不报构成，避免误导
            return text, None
        parts = [("套餐", base), ("奖励", reward), ("购买", paid)]
        return text, ("+".join(f"{k}{_fmt_num(v)}" for k, v in parts if v) or None)

    def _say(self, state: str, detail: str = "") -> None:
        log(f"[{self.name}] {_line(state, detail)}")

    @staticmethod
    def _outcome(ok: bool, name: str, state: str, detail: str = "") -> Tuple[bool, str]:
        return ok, _result(PLATFORM_WORKBUDDY, name, state, detail)

    def check_in(self, status_only: bool) -> Tuple[bool, str]:
        status = self._call(WB_ROUTE_STATUS)
        self._status = status
        # 平台级活动行打在每个平台的第一行位置；整轮只打一次（见 `_ACTIVITY_SHOWN`）。
        self._say_activity(status)

        if status.already_checked:
            return self._settle("不重签", status)

        if status.accepted:
            # 正常流程不报「未签到」：紧接着的结算行（`签到成功；连签…；本次 +100`）本来就说清了，
            # 多一行只是噪声；只查不领没有结算行，那一句才有用。
            if status_only:
                self._say("今日未签到", status.credit_text)
        else:
            self._say("状态查询异常", self._err_text(status))
            if status.http in (401, 403):
                return self._outcome(False, self.name, "凭证失效或权限不足")
            if status_only:
                return self._outcome(False, self.name, "状态查询失败")

        if status_only:
            # 只查不领：活动行已在上面打过，连续天数此时还是签到前的旧值（会少一天），不打
            return self._outcome(True, self.name, "待签到")
        return self._claim()

    def _claim(self) -> Tuple[bool, str]:
        claim = self._call(WB_ROUTE_CLAIM)
        if claim.already_checked:
            return self._settle("不重签", claim)
        if not claim.accepted:
            self._say("签到失败", self._err_text(claim))
            return self._outcome(False, self.name, "签到失败", claim.reason[:80])

        # 接口受理不等于到账：回查一次状态才算真的签到成功。
        verify = self._call(WB_ROUTE_STATUS)
        if verify.already_checked:
            return self._settle("签到成功", verify)
        return self._outcome(False, self.name, "领取后回查未确认签到")

    @staticmethod
    def _say_activity(reply: WbReply) -> None:
        """平台级活动行（`WorkBuddy 本期活动；…`），整轮只打一次；字段全缺则不打这行。

        这行是平台级的、要排在各账号行前面：并发跑时只备好文案，由主线程的收集循环
        （`_map_accounts` 里的 `_flush_pending_activity`）先打掉；串行跑就直接打，位置天然正确。
        """
        detail = reply.activity_text
        with _ACTIVITY_LOCK:
            if not detail or _ACTIVITY_SHOWN:
                return
            _ACTIVITY_SHOWN.append(detail)
            text = f"{PLATFORM_WORKBUDDY} " + _line("本期活动", detail)
            if getattr(_LOG_BUFFER, "lines", None) is not None:
                _ACTIVITY_PENDING.append(text)
            else:
                log(text)

    @staticmethod
    def _err_text(reply: WbReply) -> str:
        """异常行：HTTP 码 + 业务码 + 原因（+ requestId，便于自查与向官方反馈）。"""
        text = f"HTTP={reply.http} code={reply.code} {reply.reason}"
        return f"{text} requestId={reply.request_id}" if reply.request_id else text

    def _settle(self, state: str, reply: WbReply) -> Tuple[bool, str]:
        """已签到 / 签到成功：打本账号的状态、连签天数与积分明细（活动行已在 `check_in` 打过）。"""
        balance, compose = self.balance()
        parts = [reply.credit_text]
        if balance:
            parts.append(f"余额{balance}")
        detail = "，".join(p for p in parts if p)
        # 日志：连签（账号级）与构成都接在同一行（连签自成一段，用 `；` 与明细隔开）；
        # 通知只要明细 —— 连签是当期活动口径，余额拆解也不该塞进推送。
        log_detail = "；".join(p for p in [reply.streak_text, detail] if p)
        if compose:
            log_detail += f"（{compose}）"
        self._say(state, log_detail)
        return self._outcome(True, self.name, state, detail)


def _map_accounts(items: List[Any], work: Callable[[Any], Any],
                  workers: int) -> List[Tuple[Any, List[str]]]:
    """并发跑各账号：**谁先跑完谁先打**，返回值仍按输入顺序 `[(返回值, 该账号的输出行), ...]`。

    并发只吃网络等待：每个线程的输出先攒进自己的线程缓冲（见 `log`），该账号一跑完就由主线程
    整块打出来 —— 所以是「签完一个出一个」，不必等整平台跑完。
    返回值顺序仍是输入顺序：汇总、通知与退出码照旧按账号顺序。
    `workers <= 1` 或只有一个账号时退化成串行（边跑边打，缓冲为空，`_flush_block` 不会被调）。
    """
    if workers <= 1 or len(items) <= 1:
        return [(work(item), []) for item in items]
    with ThreadPoolExecutor(max_workers=min(workers, len(items))) as pool:
        futures = [pool.submit(_capture, work, item) for item in items]
        pending = set(futures)
        while pending:
            finished, pending = wait(pending, timeout=STREAM_TICK, return_when=FIRST_COMPLETED)
            _flush_pending_activity()       # 活动行备好就出，不占账号行的位置
            for future in finished:
                _flush_block(future.result()[1])
        _flush_pending_activity()           # 兜底：最后一刻才备好的活动行，别漏到下一个平台
        return [future.result() for future in futures]


def _capture(work: Callable[[Any], Any], item: Any) -> Tuple[Any, List[str]]:
    """在线程缓冲下跑一个账号：返回它的返回值与攒下的输出行（主线程拿去按完成顺序打）。"""
    lines: List[str] = []
    _LOG_BUFFER.lines = lines
    try:
        return work(item), lines
    finally:
        _LOG_BUFFER.lines = None


def _flush_block(lines: List[str]) -> None:
    """打一个账号攒下的输出（它跑完时由主线程调用）：块内时间戳就是打完那一刻。"""
    for message in lines:
        _emit(datetime.now(), message)


def _flush_pending_activity() -> None:
    """把备好的平台级活动行打掉（只备一次、只打一次）：它要排在账号行前面。"""
    if not _ACTIVITY_PENDING:
        return
    with _ACTIVITY_LOCK:
        pending = list(_ACTIVITY_PENDING)
        _ACTIVITY_PENDING.clear()
    for text in pending:
        _emit(datetime.now(), text)


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
    """扫描本机已安装 Trae 客户端的真实设备池（与参考仓库一致）。"""
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
    """在 payload / payload.data 里按 keys 顺序找布尔标记；字段不存在返回 None。"""
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
    """状态查询是否表示「今日已签到」。"""
    flag = _trae_flag(payload, TRAE_CHECKED_FLAGS)
    if flag is not None:
        return flag
    msg = _msg_of(payload).lower()
    return any(word in msg for word in TRAE_ALREADY_WORDS)


def _trae_claim_ok(payload: Optional[Dict[str, Any]]) -> bool:
    """领奖调用是否成功：业务码成功，或明确说明今天已经领过。"""
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


def _trae_declared_credit(payload: Optional[Dict[str, Any]]) -> Optional[int]:
    """平台声明的「本次签到积分」= `credits`；没有该字段则 None。"""
    return _trae_pick_int(payload, *TRAE_CREDIT_KEYS)


def _trae_gain(before: Optional[int], after: Optional[int]) -> Optional[int]:
    """本次实际到账 = 签到前后余额差；任一为空或差额非正时返回 None（交回退）。"""
    if before is None or after is None:
        return None
    gain = after - before
    return gain if gain > 0 else None


def _trae_confirm(headers: Dict[str, str], timeout: int, retries: int) -> Optional[Dict[str, Any]]:
    """领奖后回查状态：确认到账返回状态报文，否则 None。"""
    for wait in TRAE_VERIFY_WAITS:
        if wait:
            time.sleep(wait)
        status = http_post(TRAE_STATUS_URL, headers, {}, timeout, retries)
        if _trae_status_checked(status[1]):
            return status[1]
    return None


def _trae_query_credits(acc: Dict[str, Any], device_id: str, timeout: int, retries: int) -> Optional[int]:
    """查询 Trae 当前积分余额（来自 ide_user_ent_usage 接口），返回当前可用积分或 None。"""
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
    status = http_post(TRAE_STATUS_URL, headers, {}, timeout, retries)
    if _trae_status_checked(status[1]):
        bal = _trae_query_credits(acc, device_id, timeout, retries)
        txt = _fmt_credit(today=_trae_declared_credit(status[1]), balance=bal)
        log(f"[{name}] {_line('不重签', txt)}")
        return True, _result(PLATFORM_TRAE, name, '不重签', txt), None
    if status_only:
        if status[1] is None:
            return False, _result(PLATFORM_TRAE, name, '状态查询失败', clean_text(status[2])[:80]), None
        # 与 WorkBuddy 同一口径：只查不领没有结算行，这里得自己把状态打出来
        log(f"[{name}] {_line('今日未签到', _fmt_credit(today=_trae_declared_credit(status[1])))}")
        return True, _result(PLATFORM_TRAE, name, '待签到'), None
    if status[1] is None:
        log(f"[{name}] {_line('状态查询失败，仍尝试领取', clean_text(status[2])[:60])}")

    # 记下领取前的余额：本次实际到账只能靠前后差实测（平台声明的值会虚报）。
    balance_before = _trae_query_credits(acc, device_id, timeout, retries)

    for attempt in range(TRAE_CLAIM_RETRIES):
        claim = http_post(TRAE_CLAIM_URL, headers, {}, timeout, retries)
        c = _code_of(claim[1])
        if _trae_claim_ok(claim[1]):
            # 受理 ≠ 到账：必须回查确认（与 WorkBuddy 同一口径）。少了这一步，
            # 就会出现「日志写签到成功、平台上其实没签上」的假绿。
            settled = _trae_confirm(headers, timeout, retries)
            if settled is not None:
                balance_after = _trae_query_credits(acc, device_id, timeout, retries)
                earned = _trae_gain(balance_before, balance_after)
                if earned is None:
                    earned = _trae_declared_credit(claim[1]) or _trae_declared_credit(settled)
                txt = _fmt_credit(today=earned, balance=balance_after)
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


def _wecom_hint(code: Any) -> str:
    """企业微信错误码 → 最可能的原因，只列高频几条。"""
    return {40001: "corpsecret 不对（别把「应用 Secret」和「通讯录 Secret」弄混）",
            40013: "corpid 不对",
            60011: "agentid 与该 Secret 不匹配，或应用未授权",
            60020: "调用方 IP 不在可信 IP 白名单（企业微信后台 → 应用 → 企业可信 IP）",
            81013: "touser 中的成员不在应用可见范围内（要填 UserID，不是手机号或姓名）",
            82001: "touser 不能为空"}.get(_to_int(code, -1), "")


def _send_wecom_app(notify: Dict[str, Any], title: str, content: str) -> Tuple[bool, str]:
    """推送企业微信应用消息，返回 `(是否成功, 失败原因)`；渠道未配置时原因为空串。"""
    wecom = notify.get("wecom") or {}
    corpid = clean_text(wecom.get("corpid"))
    corpsecret = clean_text(wecom.get("corpsecret"))
    agentid = clean_text(wecom.get("agentid"))
    touser = clean_text(wecom.get("touser")) or "@all"
    if not (corpid and corpsecret and agentid):
        return False, ""
    st, tok, err = http_get(
        f"https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid={corpid}&corpsecret={corpsecret}",
        {}, DEFAULT_TIMEOUT,
    )
    if not isinstance(tok, dict) or tok.get("errcode") not in (0, None):
        reason = clean_text(tok.get("errmsg")) if isinstance(tok, dict) else (clean_text(err) or "HTTP=" + str(st))
        hint = _wecom_hint(tok.get("errcode") if isinstance(tok, dict) else None)
        return False, "gettoken 失败：" + (reason or "无响应") + ("（" + hint + "）" if hint else "")
    access = tok.get("access_token")
    body = {"touser": touser, "msgtype": "text", "agentid": int(agentid) if agentid.isdigit() else agentid,
            "text": {"content": f"{title}\n{content}"}}
    send_url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={access}"
    st, pl, er = http_post(send_url, {}, body, DEFAULT_TIMEOUT, DEFAULT_RETRIES)
    if st == 200 and isinstance(pl, dict) and pl.get("errcode") == 0:
        return True, ""
    reason = clean_text(pl.get("errmsg")) if isinstance(pl, dict) else clean_text(er)
    hint = _wecom_hint(pl.get("errcode") if isinstance(pl, dict) else None)
    return False, "message/send 失败：" + (reason or ("HTTP=" + str(st))) + ("（" + hint + "）" if hint else "")


def _send_webhook(url: str, title: str, content: str) -> Tuple[bool, str]:
    """推送任意 webhook，返回 `(是否成功, 失败原因)`；url 为空时原因为空串。"""
    url = clean_text(url)
    if not url:
        return False, ""
    text = f"{title}\n{content}"
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
        return True, ""
    return False, (("HTTP=" + str(st) + " ") if st else "") + (clean_text(er) or "无响应")


def send_notify(notify: Dict[str, Any], title: str, content: str) -> bool:
    if not isinstance(notify, dict):
        return False
    ok = False
    webhook = clean_text((notify.get("webhook") or {}).get("url"))
    if webhook:
        good, detail = _send_webhook(webhook, title, content)
        if not good:
            log(f"Webhook 推送失败：{detail}")
        ok = good or ok
    good, detail = _send_wecom_app(notify, title, content)
    if not good and detail:
        log(f"企业微信推送失败：{detail}")
    ok = ok or good
    if not ok:
        log("通知：未配置有效渠道或推送失败（不影响签到）")
    return ok


def test_notify(notify: Dict[str, Any]) -> int:
    """单独发一条测试通知，验证两个渠道；不签到、不改动当日去重记录。"""
    notify = notify if isinstance(notify, dict) else {}
    webhook = clean_text((notify.get("webhook") or {}).get("url"))
    wecom = notify.get("wecom") or {}
    corpid = clean_text(wecom.get("corpid"))
    corpsecret = clean_text(wecom.get("corpsecret"))
    agentid = clean_text(wecom.get("agentid"))
    touser = clean_text(wecom.get("touser")) or "@all"
    missing = [k for k, v in (("corpid", corpid), ("corpsecret", corpsecret), ("agentid", agentid)) if not v]
    has_wecom = not missing            # 三个字段齐了才发得出去
    touched_wecom = len(missing) < 3   # 填了至少一个字段

    log("通知测试：只发测试消息，不签到、不改动当日去重记录。")
    log("渠道 A Webhook：" + (_mask_url(webhook) if webhook else "未配置（notify.webhook.url 为空）"))
    if touched_wecom:
        head = corpid if len(corpid) <= 6 else corpid[:6] + "***"
        log("渠道 B 企业微信应用：corpid=" + (head or "(空)") + " agentid=" + (agentid or "(空)")
            + " corpsecret=" + (("已填 " + str(len(corpsecret)) + " 位") if corpsecret else "(空)")
            + " touser=" + touser + ("" if has_wecom else "（配置不完整，缺 " + "/".join(missing) + "）"))
    else:
        log("渠道 B 企业微信应用：未配置（notify.wecom 的 corpid / corpsecret / agentid 全为空）")
    if not webhook and not touched_wecom:
        log("两个渠道都未配置，没有可测试的对象；填好 config.json 的 notify 段再重跑本命令（见 README §4）。")
        log("本次脚本执行完毕。")
        log("")
        return 1

    title = "【测试】每日签到通知渠道"
    content = ("这是一条由 checkin.py --test-notify 发出的测试消息。\n"
               "收到即说明该渠道可用；真实签到通知只在当日首次签到成功后推送一次。")
    rc = 0
    if webhook:
        good, detail = _send_webhook(webhook, title, content)
        log("渠道 A Webhook：" + ("推送成功" if good else "推送失败：" + detail))
        rc |= 0 if good else 1
    if touched_wecom:
        if has_wecom:
            good, detail = _send_wecom_app(notify, title, content)
            log("渠道 B 企业微信应用：" + ("推送成功" if good else "推送失败：" + detail))
            rc |= 0 if good else 1
        else:
            log("渠道 B 企业微信应用：未发送 —— 配置不完整，缺 " + "/".join(missing))
            rc = 1
    log("请在接收端确认是否收到这条消息。" if not rc else
        "有未完成的渠道，原因见上面每行；企业微信最常见的是可信 IP 白名单与 touser 可见范围。")
    log("本次脚本执行完毕。")
    log("")
    return rc


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


def orchestrate(platform: str, results: List[Tuple[bool, str]],
                plan: List[Dict[str, Any]],
                skipped: List[str]) -> Tuple[int, bool]:
    """更新签到状态，并把「本轮要发的通知」追加进 `plan`；不在这里发送。

    两个平台的 `plan` 由 `flush_notify` 合并成一条消息发出，避免一天收到两条；今天已推送过的平台记进
    `skipped`，由 `main` 在收尾处并成一句（一条通知只报一句，不再夹在平台块中间）。
    `success_date_*` 记「当日签到成功」（`--today` 判读与当日首次弹窗都用它）；
    `notify_date_*` 记「当日通知已送达」—— 发送与状态分开是为了**只有真送达才算推送过**：
    旧实现丢弃 `send_notify` 返回值、无条件写标记，渠道没配或推送失败也打印
    「今日已推送成功通知」，而且当天配好渠道后不会再补推。
    """
    today = date.today().isoformat()
    state = load_state()
    key_ok = f"success_date_{platform}"
    key_push = f"notify_date_{platform}"
    key_fail = f"fail_date_{platform}"
    label = PLATFORM_LABELS.get(platform, platform)
    success = all(ok for ok, _ in results)
    summary = "\n".join(t for _, t in results)

    if success:
        first_today = state.get(key_ok) != today
        state[key_ok] = today
        if state.get(key_push) == today:
            # 通知只有一条（两平台合并），它的去向也只报一句：交给收尾处的 `main` 统一打。
            # 原先按平台各打一行，WorkBuddy 那句会被下一个 `===== Trae =====` 劈在两半。
            skipped.append(label)
        else:
            plan.append({"platform": platform, "ok": True, "text": summary})
        if state.get(key_fail) == today:
            state["fail_count"] = 0
    else:
        first_today = False
        if state.get(key_fail) != today:
            state[key_fail] = today
            state["fail_count"] = 0
            state["last_fail_ts"] = 0.0
        count = int(state.get("fail_count", 0))
        last = float(state.get("last_fail_ts", 0.0))
        now = time.time()
        if count < MAX_FAIL_ALERTS and (now - last) >= MIN_FAIL_INTERVAL:
            plan.append({"platform": platform, "ok": False, "text": summary})
            state["last_fail_ts"] = now          # 成败都推进节流窗口
        else:
            log(f"{label} 失败，但今日已推送 {count} 条（上限 {MAX_FAIL_ALERTS}）或间隔不足，跳过推送")
    save_state(state)
    return (0 if success else 1), first_today


def flush_notify(notify: Dict[str, Any], plan: List[Dict[str, Any]], summary: str = "") -> bool:
    """把两个平台的待发内容合并成**一条**消息发出，返回是否送达。

    送达后才写标记：成功平台记 `notify_date_*`（同日不再重复推），失败平台占一条当日配额。
    未送达则一个标记都不写 —— 下次运行（含手动）会重试。
    `summary` 是收尾那句合计（`_run_summary`），接在正文末尾，与日志末尾同一句。
    """
    if not plan:
        return False
    today = date.today().isoformat()
    all_ok = all(item["ok"] for item in plan)
    body = "\n".join(item["text"] for item in plan)
    if summary:
        body += "\n" + summary
    stamp = datetime.now().strftime(LOG_TS_FMT)
    title = f"一体化每日签到脚本 v{VERSION} {stamp}" + ("" if all_ok else "（未全部成功）")
    sent = send_notify(notify, title, body)
    state = load_state()
    if sent:
        for item in plan:
            if item["ok"]:
                state[f"notify_date_{item['platform']}"] = today
            else:
                state["fail_count"] = int(state.get("fail_count", 0) or 0) + 1
        # 不报「合并 N 条：平台A，平台B」——几个平台、哪些账号就在上面的平台块里，
        # 条数与来源一眼可见；这一行只说明「发出去了」。
        log("已推送签到通知")
    else:
        log("本次签到通知未送达（原因见上），下次运行会重试")
    save_state(state)
    return sent


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
    """查询三个计划任务：是否存在 / 动作 / 下次运行 / 上次运行与结果。"""
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
    """检查本脚本的三个计划任务（只读，不做任何修改）。"""
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
    log(f"计划任务动作：wscript.exe -> {ENTRY_VBS} -> {ENTRY_BAT} --auto，全程隐藏窗口、不回显、不弹 cmd 黑框")
    if not ENTRY_PATH.exists():
        log(_line("异常", f"当前目录下找不到 {ENTRY_BAT}，请确认脚本是否被移动或删除"))
    elif not (ENTRY_PATH.parent / ENTRY_VBS).exists():
        log(_line("异常", f"当前目录下找不到 {ENTRY_VBS}，计划任务将无法启动，请恢复该文件后重跑 `checkin.bat --install`"))
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
    """日常查看：今日签到状态 + 计划任务下一班次 + 最近一次运行摘要。"""
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

    day_tag = today.strftime(LOG_DAY_FMT)
    runs_today = sum(1 for block in blocks for line in block
                     if line.startswith(f"[{day_tag} ") and "启动" in line)
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
               "查看：--today（今日状态，纯本地）/ --tasks（计划任务检查）/ --test-notify（发一条测试推送）",
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
    p.add_argument("--test-notify", "--notify-test", action="store_true", dest="test_notify",
                   help="只发一条测试通知，验证 webhook / 企业微信应用是否可用（不签到、不改去重状态）")
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
    workers = env_int("AICREDIT_CONCURRENCY", DEFAULT_CONCURRENCY, 1, 8)

    try:
        cfg = load_config()
    except Exception as exc:
        log(f"配置错误：{exc}")
        _print_init_hint()
        log("本次脚本执行完毕。")
        log("")
        return 1

    if args.test_notify:
        return test_notify(cfg.get("notify") or {})

    wb = workbuddy_accounts(cfg) if cfg.get("accounts") else []
    trae = trae_accounts(cfg) if cfg.get("trae_accounts") else []
    notify = cfg.get("notify") or {}

    # 标记 _orig_access_token 用于判断 refresh 后是否需回写
    for a in trae:
        a["_orig_access_token"] = a.get("accessToken", "")

    if args.dry_run:
        log(f"Dry-run：{PLATFORM_WORKBUDDY} 账号 {len(wb)} 个；{PLATFORM_TRAE} 账号 {len(trae)} 个"
            f"；并发 {workers}")
        log("本次脚本执行完毕。")
        log("")
        return 0

    rc = 0
    first_today = False
    plan: List[Dict[str, Any]] = []          # 待推送内容；两平台跑完由 flush_notify 合并成一条
    notify_skips: List[str] = []             # 今天已推送过、本轮不再发的平台（收尾处并成一句）
    done: List[Tuple[bool, str]] = []        # 本轮走完签到流程的账号结果，收尾处汇成一句合计
    if not args.trae_only and wb:
        log(f"===== {PLATFORM_WORKBUDDY} =====")
        captured = _map_accounts(
            wb, lambda a: run_workbuddy(a, args.status_only, timeout, retries), workers)
        results = [value for value, _ in captured]
        if args.status_only:
            rc |= 0 if all(ok for ok, _ in results) else 1    # 只查询：不写状态、不推送
        else:
            trc, first = orchestrate("workbuddy", results, plan, notify_skips)
            rc |= trc
            first_today = first_today or first
            done.extend(results)
    elif not args.trae_only and not wb:
        log(f"{PLATFORM_WORKBUDDY}：尚未初始化，请先运行 `{INIT_CMD_WORKBUDDY}`（导入本机登录凭据）")

    if not args.workbuddy_only and trae:
        log(f"===== {PLATFORM_TRAE} =====")
        captured = _map_accounts(
            trae, lambda a: run_trae(a, args.status_only, timeout, retries, cfg), workers)
        results: List[Tuple[bool, str]] = []
        for value, _ in captured:
            ok, msg, updated = value
            if updated:
                _persist_trae(cfg, updated)     # 写 config.json 只能串行，放回主线程做
            results.append((ok, msg))
        if args.status_only:
            rc |= 0 if all(ok for ok, _ in results) else 1    # 只查询：不写状态、不推送
        else:
            trc, first = orchestrate("trae", results, plan, notify_skips)
            rc |= trc
            first_today = first_today or first
            done.extend(results)
    elif not args.workbuddy_only and not trae:
        log(f"{PLATFORM_TRAE}：尚未初始化，请先运行 `{INIT_CMD_TRAE}`（打开浏览器完成登录）")

    if not wb and not trae:
        _print_init_hint()
        log("本次脚本执行完毕。")
        log("")
        return 1

    summary = _run_summary(done)
    if plan:
        flush_notify(notify, plan, summary)     # 通知正文末尾也带这一句
    elif notify_skips:
        # 两个平台今天都已推送过 —— 本条通知没有要发的内容，本轮也不发。
        log("今日通知已推送，跳过")
    if summary:
        log(summary)
    log("本次脚本执行完毕。")
    if first_today:
        log(f"当日首次签到，正在用记事本打开日志：{LOG_FILE}")
        _request_open_log()
    log("")                                 # 运行块之间的分隔：必须压在本轮所有输出之后
    return rc


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException:
        # 未捕获异常也要进运行副本，否则 bat 侧的日志会缺这一段（控制台仍由 Python 自己打印）
        log("未捕获异常：" + traceback.format_exc())
        raise
