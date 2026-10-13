"""
CTFd Telegram Notify
----------------------
Sends Telegram messages for:
  * Every challenge solve ("<name> solved by <user>!")
  * Halfway through the competition (CTFd's start/end config)
  * When the scoreboard freeze time is reached (CTFd's freeze config)

Settings (bot token, chat id, optional topic/thread id, per-event
toggles) are configured on an admin page at /admin/telegram-notify and
stored via CTFd's normal get_config/set_config key-value store -- no
extra settings table needed.
"""
import json
import logging
import urllib.error
import urllib.request
from datetime import datetime

from flask import Blueprint, g, redirect, render_template, request, session, url_for
from sqlalchemy import event

from CTFd.models import Challenges, Solves, db
from CTFd.utils import get_config, set_config
from CTFd.utils.dates import ctf_started
from CTFd.utils.decorators import admins_only

log = logging.getLogger("ctfd_telegram_notify")

CONFIG_KEYS = [
    "telegram_bot_token",
    "telegram_chat_id",
    "telegram_thread_id",
    "telegram_api_base",
    "telegram_notify_solves",
    "telegram_notify_halftime",
    "telegram_notify_freeze",
]


def _settings():
    return {k: get_config(k) for k in CONFIG_KEYS}


def _is_enabled(value):
    # CTFd's get_config() returns numeric-looking strings as actual ints
    # (e.g. stored "0" comes back as the int 0, not "0"), and booleans as
    # actual bools -- so comparisons must normalize type first, not just
    # check membership against a tuple of string sentinels.
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() not in ("", "0", "false", "none")


def _send_telegram_message(text):
    s = _settings()
    token = s.get("telegram_bot_token")
    chat_id = s.get("telegram_chat_id")
    if not token or not chat_id:
        log.warning("Telegram notify skipped: bot token or chat id not configured")
        return False

    base = s.get("telegram_api_base") or "https://api.telegram.org"
    url = f"{base.rstrip('/')}/bot{token}/sendMessage"

    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    thread_id = s.get("telegram_thread_id")
    if thread_id:
        try:
            payload["message_thread_id"] = int(thread_id)
        except ValueError:
            pass

    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            if not body.get("ok"):
                log.warning("Telegram API returned non-ok response: %s", body)
                return False
            return True
    except urllib.error.URLError:
        log.exception("Failed to send Telegram notification")
        return False
    except Exception:
        log.exception("Unexpected error sending Telegram notification")
        return False


def _notify_solve(solve_id):
    if not _is_enabled(get_config("telegram_notify_solves")):
        return

    solve = Solves.query.get(solve_id)
    if not solve:
        return

    challenge = Challenges.query.get(solve.challenge_id)
    if not challenge:
        return

    name = solve.account.name if solve.account else "someone"
    text = f"\U0001F6A9 <b>{challenge.name}</b> solved by <b>{name}</b>!"
    _send_telegram_message(text)


def _queue_solve_notification(mapper, connection, target):
    # Runs during the SQLAlchemy flush, before the request's commit.
    # Only buffer the id here (cheap, no DB/network access) -- the actual
    # notification is sent from an after_request hook once the solve is
    # safely committed.
    pending = g.setdefault("_telegram_pending_solves", [])
    pending.append(target.id)


event.listen(Solves, "after_insert", _queue_solve_notification)


def _format_hms(seconds):
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, _ = divmod(rem, 60)
    return f"{hours}h {minutes}m"


def _check_time_based_notifications():
    now = datetime.utcnow().timestamp()

    start = get_config("start")
    end = get_config("end")
    if (
        start
        and end
        and _is_enabled(get_config("telegram_notify_halftime"))
        and not _is_enabled(get_config("telegram_halftime_sent"))
    ):
        start, end = int(start), int(end)
        if end > start:
            midpoint = start + (end - start) / 2
            if now >= midpoint:
                remaining = _format_hms(end - now)
                _send_telegram_message(
                    f"⏰ <b>Halfway there!</b> Roughly {remaining} left until the CTF ends."
                )
                set_config("telegram_halftime_sent", "1")

    freeze = get_config("freeze")
    if (
        freeze
        and _is_enabled(get_config("telegram_notify_freeze"))
        and not _is_enabled(get_config("telegram_freeze_sent"))
    ):
        if now >= int(freeze):
            _send_telegram_message(
                "\U0001F9CA <b>Scoreboard frozen.</b> Submissions still count, "
                "but rankings won't update publicly until the event ends."
            )
            set_config("telegram_freeze_sent", "1")


telegram_bp = Blueprint("telegram_notify", __name__, template_folder="templates")


@telegram_bp.route("/admin/telegram-notify", methods=["GET"])
@admins_only
def view_settings():
    settings = _settings()
    toggles = {
        k: _is_enabled(settings[k])
        for k in ("telegram_notify_solves", "telegram_notify_halftime", "telegram_notify_freeze")
    }
    return render_template(
        "telegram_settings.html",
        settings=settings,
        toggles=toggles,
        nonce=session.get("nonce"),
    )


@telegram_bp.route("/admin/telegram-notify", methods=["POST"])
@admins_only
def save_settings():
    for key in CONFIG_KEYS:
        if key.startswith("telegram_notify_"):
            set_config(key, "1" if request.form.get(key) else "0")
        else:
            set_config(key, request.form.get(key, "").strip())
    return redirect(url_for("telegram_notify.view_settings", saved=1))


@telegram_bp.route("/admin/telegram-notify/test", methods=["POST"])
@admins_only
def send_test():
    ok = _send_telegram_message("✅ Test message from CTFd Telegram Notify plugin.")
    return redirect(
        url_for("telegram_notify.view_settings", test_ok=1 if ok else 0)
    )


@telegram_bp.route("/admin/telegram-notify/reset-flags", methods=["POST"])
@admins_only
def reset_flags():
    set_config("telegram_halftime_sent", "")
    set_config("telegram_freeze_sent", "")
    return redirect(url_for("telegram_notify.view_settings", reset=1))


def load(app):
    app.db.create_all()

    @app.after_request
    def _flush_pending_solve_notifications(response):
        pending = getattr(g, "_telegram_pending_solves", None)
        if pending:
            for solve_id in pending:
                try:
                    _notify_solve(solve_id)
                except Exception:
                    log.exception("Error notifying solve id=%s", solve_id)
        return response

    @app.before_request
    def _check_telegram_time_notifications():
        if ctf_started():
            _check_time_based_notifications()

    app.register_blueprint(telegram_bp)

    try:
        from CTFd.plugins import register_admin_plugin_menu_bar

        register_admin_plugin_menu_bar(
            title="Telegram Notify", route="/admin/telegram-notify"
        )
    except ImportError:
        pass
