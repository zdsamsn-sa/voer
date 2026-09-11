#!/usr/bin/env python3
"""
Voer.host 免费服务器会话续期 + 自动开机（Playwright 版）

功能：
1. 检测服务器是否关机/停止 → 自动点击开机
2. 若开机或续期需要看激励广告 → 模拟观看（默认 3 个）
3. 会话续期（每次 +4h，每日/每会话最多 4 次）
4. 可选 Telegram 文字通知 + 截图

优先环境变量：
    VOER_SERVER_ID / VOER_TOKEN          （必须）
    TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID（可选）
    VOER_ADS_PER_EXTENSION               （可选，默认 3）
    VOER_AD_DURATION_SEC                 （可选，默认 32）

运行：
    xvfb-run -a python3 voer_renew.py            # 开机(如需) + 续期
    xvfb-run -a python3 voer_renew.py --status   # 只查状态
    xvfb-run -a python3 voer_renew.py --power-on # 只尝试开机（仍可能看广告）
"""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = pathlib.Path(__file__).resolve().parent
CONFIG_PATH = BASE / "config.json"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36"
)

DEFAULT_CONFIG = {
    "server_id": "在这里填服务器 UUID（面板地址 /panel/server/ 后面那串）",
    "token": "在这里填浏览器 Cookie 里 voer.host 的 token 值（JWT）",
    "ads_per_extension": 3,
    "ad_duration_sec": 45,
    "headless": False,
    "use_system_chrome": False,
    "telegram_bot_token": "",
    "telegram_chat_id": "",
}

# 关机/停止类状态（小写比较）
STOPPED_STATUSES = {
    "stopped",
    "stop",
    "offline",
    "off",
    "shutdown",
    "shut down",
    "exited",
    "exit",
    "poweroff",
    "power off",
    "halted",
    "inactive",
    "down",
}

# 运行中
RUNNING_STATUSES = {
    "running",
    "run",
    "online",
    "on",
    "active",
    "up",
    "started",
}

POWER_ON_LABELS = [
    "开机",
    "啟動",
    "启动",
    "開機",
    "Start",
    "START",
    "Power on",
    "Power On",
    "Power-On",
    "Turn on",
    "Turn On",
    "Boot",
    "启动服务器",
    "開啟",
    "开启",
]

EXTEND_LABELS = [
    "延伸",
    "+ 延伸",
    "延长",
    "延長",
    "续期",
    "續期",
    "擴展",
    "扩展",
    "Extend",
    "Extend session",
    "Extend Session",
    "Renew",
    "Watch ads",
    "Watch Ads",
]

WATCH_CONFIRM_LABELS = [
    "觀看廣告",
    "观看广告",
    "Watch ad",
    "Watch Ad",
    "Watch ads",
    "Watch Ads",
    "Watch",
    "开始",
    "開始",
]

WATCH_AD_LABELS = ["Watch ad", "觀看廣告", "观看广告", "Watch Ad"]
CLOSE_AD_LABELS = ["Close", "關閉", "关闭", "Close ad", "Skip"]

ACCEPT_LABELS = ["Accept", "Accept all", "同意", "接受", "I agree", "OK", "Got it"]

from playwright.sync_api import sync_playwright


def log(*a):
    print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------
def _tg_enabled(cfg) -> bool:
    return bool(cfg.get("telegram_bot_token") and cfg.get("telegram_chat_id"))


def tg_send_message(cfg, text: str) -> bool:
    if not _tg_enabled(cfg):
        return False
    token = cfg["telegram_bot_token"]
    chat_id = cfg["telegram_chat_id"]
    api = f"https://api.telegram.org/bot{token}/sendMessage"
    body = urllib.parse.urlencode(
        {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }
    ).encode()
    req = urllib.request.Request(
        api,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": UA},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode())
            if data.get("ok"):
                log("Telegram 文本通知已发送")
                return True
            log(f"Telegram 发送失败: {data}")
            return False
    except Exception as e:
        log(f"Telegram 发送异常: {e}")
        return False


def tg_send_photo(cfg, photo_path: pathlib.Path, caption: str = "") -> bool:
    if not _tg_enabled(cfg):
        return False
    if not photo_path.exists():
        log(f"截图不存在，跳过发图: {photo_path}")
        return False
    token = cfg["telegram_bot_token"]
    chat_id = str(cfg["telegram_chat_id"])
    api = f"https://api.telegram.org/bot{token}/sendPhoto"
    boundary = f"----VoerBoundary{int(time.time())}"
    filename = photo_path.name
    file_data = photo_path.read_bytes()
    mime = mimetypes.guess_type(filename)[0] or "image/png"
    parts = []
    parts.append(
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="chat_id"\r\n\r\n'
        f"{chat_id}\r\n".encode()
    )
    if caption:
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="caption"\r\n\r\n'
            f"{caption}\r\n".encode()
        )
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="parse_mode"\r\n\r\n'
            f"HTML\r\n".encode()
        )
    parts.append(
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="photo"; filename="{filename}"\r\n'
        f"Content-Type: {mime}\r\n\r\n".encode()
        + file_data
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    req = urllib.request.Request(
        api,
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": UA,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read().decode())
            if data.get("ok"):
                log("Telegram 截图已发送")
                return True
            log(f"Telegram 发图失败: {data}")
            return False
    except Exception as e:
        log(f"Telegram 发图异常: {e}")
        return False


def notify(cfg, title: str, lines: list, photo: pathlib.Path | None = None):
    text = f"<b>{title}</b>\n" + "\n".join(lines)
    log("通知内容:\n" + text.replace("<b>", "").replace("</b>", "").replace("<code>", "").replace("</code>", ""))
    if not _tg_enabled(cfg):
        log("未配置 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID，跳过 TG 通知")
        return
    if photo and photo.exists():
        if len(text) <= 1000:
            tg_send_photo(cfg, photo, caption=text)
        else:
            tg_send_photo(cfg, photo, caption=title)
            tg_send_message(cfg, text)
    else:
        tg_send_message(cfg, text)


# ---------------------------------------------------------------------------
# Config / API
# ---------------------------------------------------------------------------


def _should_skip_extend_for_now(state: dict) -> bool:
    """若仍在 running 且距离到期超过 lead+缓冲，则跳过续期。"""
    import datetime
    if not is_running(state.get("status")):
        return False
    exp_s = state.get("sessionExpiresAt")
    if not exp_s:
        return False
    try:
        lead = int(os.environ.get("VOER_NEXT_RUN_LEAD_MINUTES", "45"))
    except Exception:
        lead = 45
    # 额外缓冲 15 分钟：只在到期前 lead+15 分钟内才真正看广告续期
    window = lead + 15
    try:
        s = str(exp_s).strip().replace("Z", "+00:00")
        exp = datetime.datetime.fromisoformat(s)
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=datetime.timezone.utc)
        now = datetime.datetime.now(datetime.timezone.utc)
        left_min = (exp.astimezone(datetime.timezone.utc) - now).total_seconds() / 60.0
        log(f"距离到期约 {left_min:.0f} 分钟（窗口={window} 分钟内才续期）")
        return left_min > window
    except Exception as e:
        log(f"解析到期时间失败，不跳过: {e}")
        return False


def write_next_run(session_expires_at: str | None, lead_minutes: int = 45) -> str | None:
    """
    根据会话到期时间，计算下次应运行的 UTC 时间（默认提前 45 分钟）。
    写入 next_run.txt，并打印 NEXT_RUN_UTC=... 供 workflow 解析。
    返回 ISO 时间字符串或 None。
    """
    import datetime

    if not session_expires_at:
        log("无 sessionExpiresAt，无法计算下次运行时间")
        return None
    try:
        s = session_expires_at.strip().replace("Z", "+00:00")
        exp = datetime.datetime.fromisoformat(s)
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=datetime.timezone.utc)
        exp_utc = exp.astimezone(datetime.timezone.utc)
    except Exception as e:
        log(f"解析到期时间失败: {e}")
        return None

    now = datetime.datetime.now(datetime.timezone.utc)
    next_run = exp_utc - datetime.timedelta(minutes=lead_minutes)
    # 若已经过了「提前量」，则改为 10 分钟后跑（避免立刻重复）
    if next_run <= now:
        next_run = now + datetime.timedelta(minutes=10)

    # 对齐到整分钟
    next_run = next_run.replace(second=0, microsecond=0)
    iso = next_run.strftime("%Y-%m-%dT%H:%M:%SZ")
    # cron: 分 时 日 月 *
    cron = f"{next_run.minute} {next_run.hour} {next_run.day} {next_run.month} *"

    line = f"NEXT_RUN_UTC={iso}"
    print(line, flush=True)
    log(f"下次运行时间 (UTC): {iso}  | cron: {cron}")
    log(f"会话到期 (UTC): {exp_utc.strftime('%Y-%m-%dT%H:%M:%SZ')}  | 提前 {lead_minutes} 分钟")

    out = pathlib.Path("next_run.txt")
    content = (
        f"NEXT_RUN_UTC={iso}\n"
        f"NEXT_RUN_CRON={cron}\n"
        f"SESSION_EXPIRES_AT={exp_utc.strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
        f"LEAD_MINUTES={lead_minutes}\n"
    )
    out.write_text(content, encoding="utf-8")
    log(f"已写入 {out.resolve()}")
    return iso


def _jwt_hint(token: str) -> str:
    t = (token or "").strip()
    if not t:
        return "空"
    parts = t.split(".")
    hint = f"长度={len(t)}, 段数={len(parts)}, 开头={t[:8]}..., 结尾=...{t[-6:]}"
    if len(parts) != 3:
        hint += "  【警告：标准 JWT 应有 3 段】"
    if not t.startswith("eyJ"):
        hint += "  【警告：正常 JWT 一般以 eyJ 开头】"
    try:
        if len(parts) >= 2:
            pad = parts[1] + "=" * (-len(parts[1]) % 4)
            payload = json.loads(base64.urlsafe_b64decode(pad))
            exp = payload.get("exp")
            if exp:
                import datetime

                exp_dt = datetime.datetime.utcfromtimestamp(exp)
                now = datetime.datetime.utcnow()
                if exp_dt < now:
                    hint += f"  【已过期！UTC {exp_dt.isoformat()}Z】"
                else:
                    hours = int((exp_dt - now).total_seconds() // 3600)
                    hint += f"  【未过期，剩余约 {hours} 小时】"
    except Exception:
        pass
    return hint


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except Exception as e:
            log(f"读取 config.json 失败: {e}")

    env_sid = os.environ.get("VOER_SERVER_ID", "").strip().strip('"').strip("'")
    env_token = os.environ.get("VOER_TOKEN", "").strip().strip('"').strip("'")
    if env_sid:
        cfg["server_id"] = env_sid
    if env_token:
        cfg["token"] = env_token

    env_tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip().strip('"').strip("'")
    env_tg_chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip().strip('"').strip("'")
    if env_tg_token:
        cfg["telegram_bot_token"] = env_tg_token
    if env_tg_chat:
        cfg["telegram_chat_id"] = env_tg_chat

    if os.environ.get("VOER_ADS_PER_EXTENSION"):
        cfg["ads_per_extension"] = int(os.environ["VOER_ADS_PER_EXTENSION"])
    if os.environ.get("VOER_AD_DURATION_SEC"):
        cfg["ad_duration_sec"] = int(os.environ["VOER_AD_DURATION_SEC"])

    sid = cfg.get("server_id", "")
    token = cfg.get("token", "")
    if not sid or "在这里填" in sid or not token or "在这里填" in token:
        log("缺少 VOER_SERVER_ID / VOER_TOKEN，请先配置")
        sys.exit(1)

    log(f"server_id 长度={len(sid)}, 开头={sid[:8]}...")
    log(f"token 诊断: {_jwt_hint(token)}")
    raw_tg_t = os.environ.get("TELEGRAM_BOT_TOKEN")
    raw_tg_c = os.environ.get("TELEGRAM_CHAT_ID")
    log(
        f"环境变量探测: TELEGRAM_BOT_TOKEN="
        f"{'已设置 len=' + str(len(raw_tg_t)) if raw_tg_t else '空/未传入'}, "
        f"TELEGRAM_CHAT_ID="
        f"{'已设置 len=' + str(len(raw_tg_c)) if raw_tg_c else '空/未传入'}"
    )
    if _tg_enabled(cfg):
        log("Telegram 通知: 已启用")
    else:
        log("Telegram 通知: 未配置（需同时设置 TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID）")
    return cfg


def api_state(cfg):
    url = f"https://voer.host/api/servers/{cfg['server_id']}"
    req = urllib.request.Request(
        url,
        headers={
            "Cookie": f"token={cfg['token']}",
            "User-Agent": UA,
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode())
            if "server" not in data:
                raise RuntimeError(f"API 返回格式异常: {list(data.keys())}")
            return data["server"]
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode(errors="replace")[:300]
        except Exception:
            pass
        log(f"API 失败: HTTP {e.code} {e.reason}  body={body}")
        if e.code in (401, 403):
            log(f"token 诊断: {_jwt_hint(cfg['token'])}")
            log("请重新从浏览器复制 VOER_TOKEN")
        raise SystemExit(1) from e
    except urllib.error.URLError as e:
        log(f"网络错误: {e.reason}")
        raise SystemExit(1) from e


def normalize_status(s) -> str:
    return str(s or "").strip().lower()


def is_stopped(status: str) -> bool:
    st = normalize_status(status)
    if st in STOPPED_STATUSES:
        return True
    # 模糊匹配
    for k in ("stop", "off", "halt", "exit", "down", "shut"):
        if k in st:
            return True
    return False


def is_running(status: str) -> bool:
    st = normalize_status(status)
    if st in RUNNING_STATUSES:
        return True
    for k in ("run", "online", "active", "up", "start"):
        if k in st and "stop" not in st and "restart" not in st:
            return True
    return False


# ---------------------------------------------------------------------------
# Browser helpers
# ---------------------------------------------------------------------------
def click_anywhere(page, texts, timeout_ms, exact=True, force=False):
    """在所有 frame 里找文本并点击；兼容 button / div / 跨域 iframe。"""
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        frames = list(page.frames)
        for frame in frames:
            for t in texts:
                selectors = []
                if exact:
                    selectors.extend(
                        [
                            lambda t=t, f=frame: f.get_by_role("button", name=t, exact=True).first,
                            lambda t=t, f=frame: f.get_by_text(t, exact=True).first,
                            lambda t=t, f=frame: f.locator(f"button:text-is('{t}')").first,
                            lambda t=t, f=frame: f.locator(f"text={t}").first,
                        ]
                    )
                selectors.extend(
                    [
                        lambda t=t, f=frame: f.get_by_role("button", name=t, exact=False).first,
                        lambda t=t, f=frame: f.get_by_text(t, exact=False).first,
                        lambda t=t, f=frame: f.locator(f"button:has-text('{t}')").first,
                        lambda t=t, f=frame: f.locator(f"[role=button]:has-text('{t}')").first,
                        lambda t=t, f=frame: f.locator(f"div:has-text('{t}')").first,
                        lambda t=t, f=frame: f.locator(f"span:has-text('{t}')").first,
                        lambda t=t, f=frame: f.locator(f"a:has-text('{t}')").first,
                    ]
                )
                for maker in selectors:
                    try:
                        loc = maker()
                        n = loc.count()
                        if not n:
                            continue
                        # 可见则点；不可见但 force 时也试
                        visible = False
                        try:
                            visible = loc.is_visible()
                        except Exception:
                            visible = False
                        if visible or force:
                            try:
                                loc.click(timeout=4000, force=force)
                            except Exception:
                                # 退回 JS 点击
                                try:
                                    loc.evaluate("el => el.click()")
                                except Exception:
                                    continue
                            return f"{t}@{frame.url[:70]}"
                    except Exception:
                        pass
        time.sleep(1.0)
    return None


def wait_for_any_text(page, texts, timeout_ms) -> str | None:
    """等待任意文案出现在任意 frame，返回命中文本。"""
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        for frame in page.frames:
            for t in texts:
                try:
                    loc = frame.get_by_text(t, exact=False).first
                    if loc.count() and loc.is_visible():
                        return t
                except Exception:
                    pass
                try:
                    loc = frame.locator(f"text={t}").first
                    if loc.count() and loc.is_visible():
                        return t
                except Exception:
                    pass
        time.sleep(1.0)
    return None



def _is_ad_frame_url(url: str) -> bool:
    u = (url or "").lower()
    keys = (
        "wormies",
        "doubleclick",
        "googleads",
        "googlesyndication",
        "pagead",
        "adservice",
        "voer-ads",
        "about:blank",
    )
    return any(k in u for k in keys)


def click_close_ad_only(page, timeout_ms=60000):
    """只在广告相关 iframe 里点 Close，避免关掉主面板的「Watch 3 ads」弹窗。"""
    labels = CLOSE_AD_LABELS
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        for frame in page.frames:
            if not _is_ad_frame_url(frame.url):
                # 主站 panel 上的 Close 很危险（会关整个广告流程弹窗）
                if "voer.host" in (frame.url or "").lower():
                    continue
            for t in labels:
                for maker in (
                    lambda t=t, f=frame: f.get_by_role("button", name=t, exact=False).first,
                    lambda t=t, f=frame: f.get_by_text(t, exact=False).first,
                    lambda t=t, f=frame: f.locator(f"button:has-text('{t}')").first,
                ):
                    try:
                        loc = maker()
                        if loc.count() and loc.is_visible():
                            loc.click(timeout=3000)
                            return f"{t}@{frame.url[:70]}"
                    except Exception:
                        pass
        time.sleep(1.0)
    return None



def click_watch_ad_prefer_iframe(page, timeout_ms=90000):
    """优先在 wormies / 广告 iframe 里点 Watch ad（续期核销关键），再回退到主页面。"""
    labels = WATCH_AD_LABELS
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        frames = list(page.frames)
        # 1) 优先广告帧
        ordered = sorted(
            frames,
            key=lambda f: (0 if _is_ad_frame_url(f.url) and "voer.host/panel" not in (f.url or "") else 1),
        )
        for frame in ordered:
            for t in labels:
                makers = [
                    lambda t=t, f=frame: f.get_by_role("button", name=t, exact=False).first,
                    lambda t=t, f=frame: f.get_by_text(t, exact=False).first,
                    lambda t=t, f=frame: f.locator(f"button:has-text('{t}')").first,
                    lambda t=t, f=frame: f.locator(f"[role=button]:has-text('{t}')").first,
                ]
                for maker in makers:
                    try:
                        loc = maker()
                        if not loc.count():
                            continue
                        try:
                            visible = loc.is_visible()
                        except Exception:
                            visible = False
                        if not visible:
                            continue
                        loc.click(timeout=4000)
                        return f"{t}@{frame.url[:80]}"
                    except Exception:
                        try:
                            loc.evaluate("el => el.click()")
                            return f"{t}@js:{frame.url[:60]}"
                        except Exception:
                            pass
        time.sleep(1.0)
    return None


def read_ad_progress(page) -> str | None:
    """读取弹窗进度文案，如 '1 / 3'、'2/3'。"""
    patterns = ["1 / 3", "2 / 3", "3 / 3", "1/3", "2/3", "3/3", "0 / 3", "0/3"]
    for frame in page.frames:
        for t in patterns:
            try:
                loc = frame.get_by_text(t, exact=False).first
                if loc.count() and loc.is_visible():
                    return t.replace(" ", "")
            except Exception:
                pass
    return None


def dismiss_banners(page):
    for t in ACCEPT_LABELS:
        hit = click_anywhere(page, [t], 2500)
        if hit:
            log(f"已关闭弹窗: {hit}")
            page.wait_for_timeout(800)


def take_screenshot(page, name="screenshot.png") -> pathlib.Path:
    path = pathlib.Path(name)
    try:
        page.screenshot(path=str(path), full_page=True)
        log(f"截图已保存: {path.resolve()}")
    except Exception as e:
        log(f"截图失败: {e}")
    return path


def dump_page_debug(page, tag="debug"):
    log(f"----- 页面诊断 ({tag}) -----")
    log(f"URL: {page.url}")
    try:
        log(f"Title: {page.title()}")
    except Exception:
        pass
    texts = []
    try:
        for frame in page.frames:
            for role in ("button", "link"):
                try:
                    for loc in frame.get_by_role(role).all()[:50]:
                        try:
                            if loc.is_visible():
                                t = (loc.inner_text(timeout=500) or "").strip()
                                if t and t not in texts:
                                    texts.append(t)
                        except Exception:
                            pass
                except Exception:
                    pass
    except Exception as e:
        log(f"收集按钮失败: {e}")
    if texts:
        log("可见按钮/链接文字:")
        for t in texts[:60]:
            log(f"  - {t!r}")
    else:
        log("未收集到可见按钮文字")
    take_screenshot(page, "debug_screenshot.png")
    log("----- 诊断结束 -----")


def open_panel(page, url: str):
    log(f"打开页面: {url}")
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    page.wait_for_timeout(4000)
    dismiss_banners(page)


def watch_reward_ads(page, cfg, reason: str = "广告") -> int:
    """
    观看激励广告。以进度条 1/3→2/3→3/3 为准，未涨进度不算成功。
    优先在 wormies iframe 点击 Watch ad（与开机成功路径一致）。
    """
    total = int(cfg["ads_per_extension"])
    duration = int(cfg["ad_duration_sec"])
    if "续期" in reason:
        duration = max(duration, 45)

    ready = wait_for_any_text(
        page,
        ["Ad ready", "Watch ad", "Watch 3 ads", "Rewarded ad", "Watch Ads to Extend"],
        45000,
    )
    log(f"[{reason}] 广告界面: {ready!r}")

    def progress_num() -> int:
        prog = read_ad_progress(page)
        if not prog:
            return -1
        # "1/3" / "2 / 3"
        try:
            return int(prog.replace(" ", "").split("/")[0])
        except Exception:
            return -1

    watched = 0
    log(f"[{reason}] 起始进度: {read_ad_progress(page) or '未知'}")

    for i in range(1, total + 1):
        before_p = progress_num()
        # 等 Ad ready
        wait_for_any_text(page, ["Ad ready", "Watch ad", "Rewarded ad"], 60000)

        # 最多重试 2 次直到进度从 before 涨到 >= i
        slot_ok = False
        for attempt in range(1, 3):
            hit = click_watch_ad_prefer_iframe(page, timeout_ms=60000)
            if not hit:
                # 主站点一次，唤起 wormies，再优先 iframe
                log(f"[{reason}] 第{i}个 尝试{attempt}: iframe 无按钮，点主站 Watch ad 唤起…")
                click_anywhere(page, WATCH_AD_LABELS, 15000, exact=False, force=True)
                page.wait_for_timeout(3000)
                hit = click_watch_ad_prefer_iframe(page, timeout_ms=45000)
            if not hit:
                log(f"[{reason}] 第{i}个 尝试{attempt}: 仍未找到 Watch ad")
                try:
                    for fr in page.frames[:12]:
                        log(f"  frame: {(fr.url or '')[:100]}")
                except Exception:
                    pass
                continue

            log(f"[{reason}] 第{i}/{total} 尝试{attempt} 点击: {hit}")
            # 等广告帧
            saw = False
            for _ in range(25):
                for fr in page.frames:
                    u = fr.url or ""
                    if any(k in u for k in ("doubleclick", "googleads", "pagead", "googlesyndication", "wormies")):
                        if "voer.host/panel" not in u:
                            log(f"[{reason}] 广告帧: {u[:90]}")
                            saw = True
                            break
                if saw:
                    break
                time.sleep(1)
            if not saw:
                log(f"[{reason}] 警告: 未出现广告帧")

            log(f"[{reason}] 播放等待 {duration}s…")
            page.wait_for_timeout(duration * 1000)

            closed = click_close_ad_only(page, timeout_ms=50000)
            log(
                f"[{reason}] Close: "
                + (closed if closed else "无（可能自动关）")
            )

            # 等进度上涨
            page.wait_for_timeout(4000)
            advanced = False
            for _ in range(25):
                pnow = progress_num()
                if pnow >= i:
                    advanced = True
                    log(f"[{reason}] 进度已到 {pnow}/3（目标>={i}）")
                    break
                if pnow > before_p and before_p >= 0:
                    advanced = True
                    log(f"[{reason}] 进度 {before_p} → {pnow}")
                    break
                time.sleep(1)
            if advanced:
                slot_ok = True
                watched = max(watched, progress_num() if progress_num() >= 0 else i)
                break
            log(f"[{reason}] 第{i}个 尝试{attempt}: 进度未涨（仍为 {progress_num()}），重试")
            page.wait_for_timeout(2000)

        if not slot_ok:
            log(f"[{reason}] 第 {i} 个广告未能核销，停止后续")
            break

        page.wait_for_timeout(3000)

    final_p = progress_num()
    log(f"[{reason}] 结束进度: {read_ad_progress(page) or '未知'} | 计为完成格数≈{watched}")
    if final_p >= total:
        return total
    return max(0, final_p) if final_p >= 0 else watched



def wait_until_running(cfg, timeout_sec=120) -> dict | None:
    """轮询 API 直到状态变为 running，或超时。"""
    end = time.time() + timeout_sec
    last = None
    while time.time() < end:
        try:
            last = api_state(cfg)
            st = last.get("status")
            log(f"等待开机中… status={st}")
            if is_running(st):
                return last
        except SystemExit:
            raise
        except Exception as e:
            log(f"轮询状态失败: {e}")
        time.sleep(8)
    return last


def try_power_on(page, cfg) -> tuple[bool, dict | None]:
    """
    尝试开机。免费档关机后点 Start 会弹出：
      "Watch 3 ads to start your free server" → Watch ad × 3
    返回 (是否已变为 running, 最新 state)。
    """
    state = api_state(cfg)
    st = state.get("status")
    log(f"开机检查: status={st}")

    if is_running(st):
        log("服务器已在运行，无需开机")
        return True, state

    if not is_stopped(st):
        log(f"状态既非 running 也非明确 stopped（{st}），仍尝试寻找开机按钮")

    log("正在寻找开机按钮…")
    # RESUME 有时也会出现在会话恢复场景
    labels = POWER_ON_LABELS + ["RESUME", "Resume", "恢复"]
    hit = click_anywhere(page, labels, 40000)
    if not hit:
        hit = click_anywhere(page, labels, 20000, exact=False, force=True)
    if not hit:
        log("未找到开机按钮")
        dump_page_debug(page, "找不到开机按钮")
        return False, state

    log(f"已点击开机: {hit}")
    page.wait_for_timeout(4000)

    # 开机几乎总会要求看 3 个广告（截图已确认 UI）
    log("开始处理开机广告（Watch 3 ads to start）…")
    watched = watch_reward_ads(page, cfg, reason="开机广告")
    if watched < 1:
        log("开机广告一个都没点到，尝试再点一次 Start 后重试")
        click_anywhere(page, labels, 10000, exact=False, force=True)
        page.wait_for_timeout(3000)
        watched = watch_reward_ads(page, cfg, reason="开机广告-重试")

    need = int(cfg["ads_per_extension"])
    if watched < need:
        log(f"警告: 仅完成 {watched}/{need} 个开机广告，尝试补看剩余…")
        extra = watch_reward_ads(page, cfg, reason="开机广告-补看")
        watched += extra
        log(f"补看后合计: {watched}/{need}")

    new_state = wait_until_running(cfg, timeout_sec=240)
    if new_state and is_running(new_state.get("status")):
        log(f"开机成功 → status={new_state.get('status')}")
        return True, new_state

    log("开机后状态仍未变为 running")
    return False, new_state or state


def try_extend_session(page, cfg, before: dict) -> tuple[bool, dict | None]:
    """点击续期入口并看广告，轮询 session 是否延长。"""
    log("正在寻找「续期/延伸」按钮…")
    hit = click_anywhere(page, EXTEND_LABELS, 45000)
    if not hit:
        page.wait_for_timeout(4000)
        hit = click_anywhere(page, EXTEND_LABELS, 25000, exact=False)
    if not hit:
        log("未找到续期入口按钮")
        dump_page_debug(page, "找不到延伸按钮")
        return False, None

    log(f"已点击续期入口: {hit}")
    page.wait_for_timeout(2500)

    n = watch_reward_ads(page, cfg, reason="续期广告")
    if n < int(cfg["ads_per_extension"]):
        log(f"续期广告未满 {cfg['ads_per_extension']} 个（完成约 {n}），仍等待接口是否生效…")

    end = time.time() + 240
    now = None
    before_exp = before.get("sessionExpiresAt")
    before_ext = int(before.get("sessionExtensions") or 0)
    while time.time() < end:
        try:
            now = api_state(cfg)
        except Exception:
            now = None
        if now:
            exp = now.get("sessionExpiresAt")
            ext = int(now.get("sessionExtensions") or 0)
            # 到期时间变了，或累计续期次数增加
            if (exp and exp != before_exp) or ext > before_ext:
                log(
                    "续期成功 → 新到期:",
                    exp,
                    "| 累计:",
                    ext,
                    "| 今日:",
                    now.get("sessionExtensionsToday"),
                )
                return True, now
            log(f"等待续期生效… 到期仍为 {exp} | 累计 {ext}")
        time.sleep(10)

    log("未检测到续期生效（广告可能未核销，或面板/API 限制）")
    return False, now


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    cfg = load_config()
    server_id = cfg["server_id"]
    url = f"https://voer.host/panel/server/{server_id}"
    short_id = server_id[:8] + "…"
    only_status = "--status" in sys.argv
    only_power = "--power-on" in sys.argv

    if only_status:
        s = api_state(cfg)
        for k in (
            "status",
            "sessionExpiresAt",
            "sessionExtensions",
            "sessionExtensionsToday",
            "sessionDuration",
            "adsWatched",
        ):
            print(f"{k} = {s.get(k)}")
        if os.environ.get("TG_NOTIFY_STATUS") == "1":
            notify(
                cfg,
                "📊 Voer 状态查询",
                [
                    f"服务器: <code>{short_id}</code>",
                    f"状态: {s.get('status')}",
                    f"到期: {s.get('sessionExpiresAt')}",
                    f"累计续期: {s.get('sessionExtensions')}",
                    f"今日续期: {s.get('sessionExtensionsToday')}",
                ],
            )
        return

    power_ok = None  # None=未尝试, True/False
    extend_ok = None
    before = {}
    after = {}
    shot = pathlib.Path("renew_screenshot.png")

    with sync_playwright() as p:
        launch = dict(
            headless=cfg["headless"],
            args=[
                "--disable-blink-features=AutomationControlled",
                "--window-size=1400,1000",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        if cfg["use_system_chrome"]:
            launch["channel"] = "chrome"
        browser = p.chromium.launch(**launch)
        ctx = browser.new_context(viewport={"width": 1400, "height": 1000})
        ctx.add_cookies(
            [
                {
                    "name": "token",
                    "value": cfg["token"],
                    "domain": "voer.host",
                    "path": "/",
                    "secure": True,
                }
            ]
        )
        page = ctx.new_page()
        try:
            open_panel(page, url)
            before = api_state(cfg)
            log(
                "当前状态:",
                before.get("status"),
                "| 到期:",
                before.get("sessionExpiresAt"),
                "| 已续期:",
                before.get("sessionExtensions"),
                "| 今日:",
                before.get("sessionExtensionsToday"),
            )

            # 1) 关机 → 开机（可能含广告）
            if is_stopped(before.get("status")) or only_power:
                power_ok, after = try_power_on(page, cfg)
                if only_power:
                    shot = take_screenshot(page, "renew_screenshot.png")
                    if power_ok:
                        exp = (after or before).get("sessionExpiresAt")
                        try:
                            lead = int(os.environ.get("VOER_NEXT_RUN_LEAD_MINUTES", "45"))
                        except Exception:
                            lead = 45
                        write_next_run(exp, lead_minutes=lead)
                        notify(
                            cfg,
                            "✅ Voer 开机成功",
                            [
                                f"服务器: <code>{short_id}</code>",
                                f"状态: {after.get('status') if after else '?'}",
                                f"到期: {after.get('sessionExpiresAt') if after else before.get('sessionExpiresAt')}",
                            ],
                            photo=shot,
                        )
                    else:
                        notify(
                            cfg,
                            "⚠️ Voer 开机未确认成功",
                            [
                                f"服务器: <code>{short_id}</code>",
                                f"原状态: {before.get('status')}",
                                f"现状态: {(after or before).get('status')}",
                                "请查看截图或日志",
                            ],
                            photo=shot if shot.exists() else pathlib.Path("debug_screenshot.png"),
                        )
                        raise SystemExit(4)
                    return
            else:
                log("服务器非关机状态，跳过开机步骤")

            # 2) 会话续期
            # 注意：API 的 sessionExtensionsToday 可能与面板「今天的擴展 x/4」不一致
            # 因此不以 API 字段硬跳过；能点到「延伸」就尝试，以续期是否生效为准
            skip_extend = os.environ.get("VOER_SKIP_EXTEND", "").strip() in ("1", "true", "yes")
            try:
                before = api_state(cfg)
            except Exception:
                pass
            today_ext = int(before.get("sessionExtensionsToday") or 0)
            if today_ext >= 4:
                log(
                    f"提示: API sessionExtensionsToday={today_ext} "
                    f"（面板可能仍显示 0/4，将继续尝试点「延伸」）"
                )
            if skip_extend:
                log("已设置 VOER_SKIP_EXTEND，跳过续期")
            elif power_ok is False and is_stopped(before.get("status")):
                log("开机未成功且仍为 stopped，跳过续期")
            elif _should_skip_extend_for_now(before):
                log("距离到期仍较远，跳过本次续期（避免无意义看广告）")
                extend_ok = None
                after = before
                try:
                    lead = int(os.environ.get("VOER_NEXT_RUN_LEAD_MINUTES", "45"))
                except Exception:
                    lead = 45
                write_next_run(before.get("sessionExpiresAt"), lead_minutes=lead)
            else:
                extend_ok, after = try_extend_session(page, cfg, before)

            shot = take_screenshot(page, "renew_screenshot.png")

        except SystemExit:
            raise
        except Exception as e:
            log(f"运行异常: {e}")
            try:
                dump_page_debug(page, "异常")
            except Exception:
                pass
            notify(
                cfg,
                "❌ Voer 任务异常",
                [f"服务器: <code>{short_id}</code>", f"错误: <code>{e}</code>"],
                photo=pathlib.Path("debug_screenshot.png"),
            )
            raise
        finally:
            try:
                page.wait_for_timeout(1000)
            except Exception:
                pass
            browser.close()

    # 汇总通知
    final = after or before
    lines = [
        f"服务器: <code>{short_id}</code>",
        f"状态: {final.get('status')}",
        f"到期: {final.get('sessionExpiresAt')}",
        f"累计续期: {final.get('sessionExtensions')} | 今日: {final.get('sessionExtensionsToday')}",
    ]
    if power_ok is True:
        lines.insert(1, "开机: 成功")
    elif power_ok is False:
        lines.insert(1, "开机: 未确认成功")

    if extend_ok is True:
        try:
            lead = int(os.environ.get("VOER_NEXT_RUN_LEAD_MINUTES", "45"))
        except Exception:
            lead = 45
        write_next_run(final.get("sessionExpiresAt"), lead_minutes=lead)
        notify(cfg, "✅ Voer 续期成功", lines, photo=shot)
    elif extend_ok is False:
        notify(
            cfg,
            "⚠️ Voer 续期未生效",
            lines
            + [
                "可能: 广告未完整核销 / 面板显示已满 / 需更长广告时长",
                "可把 VOER_AD_DURATION_SEC 调到 45 后重试",
            ],
            photo=shot if shot.exists() else None,
        )
        raise SystemExit(3)
    else:
        # 跳过续期：若已在跑且有到期时间，仍写入下次运行
        if final.get("sessionExpiresAt") and is_running(final.get("status")):
            try:
                lead = int(os.environ.get("VOER_NEXT_RUN_LEAD_MINUTES", "45"))
            except Exception:
                lead = 45
            write_next_run(final.get("sessionExpiresAt"), lead_minutes=lead)
        notify(cfg, "ℹ️ Voer 任务完成", lines, photo=shot if shot.exists() else None)


if __name__ == "__main__":
    main()
