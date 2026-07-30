"""HTTP routes for this feature area."""

from flask import Blueprint

from core import *  # noqa: F403 - shared legacy helpers during migration

bp = Blueprint("telegram", __name__)


@bp.post("/api/telegram/link")
def create_telegram_link():
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error

    telegram_error = _require_telegram_configuration()
    if telegram_error:
        return telegram_error

    identity, error = _authenticated_identity()
    if error:
        return error

    user = _get_or_create_user(
        identity["uid"],
        identity["email"],
        identity["name"],
    )

    raw_token = secrets.token_urlsafe(32)
    token_hash = _telegram_link_token_hash(raw_token)

    now = datetime.now(SINGAPORE_TZ)
    expires_at = now + timedelta(
        minutes=TELEGRAM_LINK_TOKEN_LIFETIME_MINUTES
    )

    _write_document(
        "telegramLinkTokens",
        token_hash,
        {
            "uid": identity["uid"],
            "email": identity["email"],
            "displayName": str(
                user.get("displayName") or ""
            ),
            "createdAt": now.isoformat(),
            "expiresAt": expires_at.isoformat(),
            "usedAt": None,
        },
    )

    telegram_url = (
        f"https://t.me/{TELEGRAM_BOT_USERNAME}"
        f"?start={raw_token}"
    )

    return jsonify(
        {
            "telegramUrl": telegram_url,
            "expiresAt": expires_at.isoformat(),
            "expiresInMinutes": (
                TELEGRAM_LINK_TOKEN_LIFETIME_MINUTES
            ),
        }
    )



@bp.post("/api/telegram/webhook")
def telegram_webhook():
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error

    if not TELEGRAM_WEBHOOK_SECRET:
        return jsonify(
            {
                "error": (
                    "TELEGRAM_WEBHOOK_SECRET is not configured"
                )
            }
        ), 503

    received_secret = request.headers.get(
        "X-Telegram-Bot-Api-Secret-Token",
        "",
    ).strip()

    if not secrets.compare_digest(
        received_secret,
        TELEGRAM_WEBHOOK_SECRET,
    ):
        return jsonify(
            {"error": "Invalid webhook secret"}
        ), 403

    update = request.get_json(silent=True) or {}
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    telegram_user = message.get("from") or {}
    text_value = str(message.get("text") or "").strip()

    chat_id = chat.get("id")
    chat_type = str(chat.get("type") or "")

    if not chat_id or not text_value:
        return jsonify({"ok": True})

    if chat_type != "private":
        return jsonify({"ok": True})

    command_parts = text_value.split(maxsplit=1)
    command = command_parts[0].split("@", 1)[0].lower()
    command_argument = (
        command_parts[1].strip()
        if len(command_parts) > 1
        else ""
    )

    try:
        if command == "/start":
            first_name = escape(
                str(telegram_user.get("first_name") or "there")
            )

            # Always send an immediate greeting so the user can confirm
            # that the Telegram bot and webhook are working.
            _send_telegram_message(
                chat_id,
                (
                    f"Hello, <b>{first_name}</b>! \n\n"
                    "Kotaro here, I'll be here to remind you of upcoming events from now on."
                ),
            )

            if not command_argument:
                _send_telegram_message(
                    chat_id,
                    (
                        "To connect your account, open The Nowl In One "
                        "website and press <b>Connect Telegram</b>.\n\n"
                        "Once connected, I can send your upcoming "
                        "events, tasks, and reminders."
                    ),
                )
                return jsonify({"ok": True})

            token_hash = _telegram_link_token_hash(
                command_argument
            )
            token_ref = _document(
                "telegramLinkTokens",
                token_hash,
            )
            token_snapshot = token_ref.get()

            if not token_snapshot.exists:
                _send_telegram_message(
                    chat_id,
                    (
                        "This connection link is invalid or "
                        "has already been used. Please generate "
                        "a new link from The Nowl."
                    ),
                )
                return jsonify({"ok": True})

            token_data = token_snapshot.to_dict() or {}
            expires_at = _parse_stored_datetime(
                token_data.get("expiresAt")
            )

            if (
                token_data.get("usedAt")
                or expires_at is None
                or expires_at < datetime.now(SINGAPORE_TZ)
            ):
                token_ref.delete()
                _send_telegram_message(
                    chat_id,
                    (
                        "This connection link has expired. "
                        "Please generate a new one from The Nowl."
                    ),
                )
                return jsonify({"ok": True})

            uid = str(token_data.get("uid") or "").strip()
            if not uid:
                token_ref.delete()
                _send_telegram_message(
                    chat_id,
                    (
                        "This connection link could not be processed. "
                        "Please generate a new one from The Nowl."
                    ),
                )
                return jsonify({"ok": True})

            existing_uid, existing_subscription = (
                _find_subscription_by_chat_id(
                    str(chat_id)
                )
            )

            if (
                existing_uid
                and existing_uid != uid
                and existing_subscription
            ):
                _deactivate_telegram_subscription(
                    existing_uid,
                    reason="telegram_chat_relinked",
                )

            _set_telegram_subscription_active(
                uid,
                chat_id=str(chat_id),
                telegram_user=telegram_user,
            )
            token_ref.delete()

            display_name = escape(
                str(
                    token_data.get("displayName")
                    or telegram_user.get("first_name")
                    or "there"
                )
            )

            _send_telegram_message(
                chat_id,
                (
                    f"Connected successfully, "
                    f"<b>{display_name}</b>! ✅\n\n"
                    "You will receive a daily summary of your "
                    "events, tasks, and reminders for the next "
                    "seven days.\n\n"
                    "Commands:\n"
                    "/upcoming — View your week ahead\n"
                    "/status — Check your subscription\n"
                    "/unsubscribe — Stop daily reminders"
                ),
            )

        elif command == "/upcoming":
            uid, subscription = (
                _find_subscription_by_chat_id(
                    str(chat_id)
                )
            )

            if (
                not uid
                or not subscription
                or not subscription.get("active")
            ):
                _send_telegram_message(
                    chat_id,
                    (
                        "Your Telegram account is not currently "
                        "connected to The Nowl."
                    ),
                )
                return jsonify({"ok": True})

            user = _read_document("users", uid) or {}
            items = _get_user_week_ahead_items(uid)

            _send_telegram_message(
                chat_id,
                _build_week_ahead_message(
                    str(
                        user.get("displayName")
                        or telegram_user.get("first_name")
                        or "there"
                    ),
                    items,
                ),
            )

        elif command == "/status":
            uid, subscription = (
                _find_subscription_by_chat_id(
                    str(chat_id)
                )
            )

            if (
                uid
                and subscription
                and subscription.get("active")
            ):
                _send_telegram_message(
                    chat_id,
                    (
                        "Your daily Nowl reminders are "
                        "<b>active</b>. ✅\n\n"
                        "They are currently scheduled for "
                        "8:00 AM Singapore time."
                    ),
                )
            else:
                _send_telegram_message(
                    chat_id,
                    (
                        "Your Nowl reminder subscription is "
                        "<b>not active</b>."
                    ),
                )

        elif command in {"/unsubscribe", "/stop"}:
            uid, subscription = (
                _find_subscription_by_chat_id(
                    str(chat_id)
                )
            )

            if uid and subscription:
                _deactivate_telegram_subscription(
                    uid,
                    reason="telegram_command",
                )

            _send_telegram_message(
                chat_id,
                (
                    "Daily reminders have been disabled. "
                    "You can reconnect from The Nowl In One at any time."
                ),
            )

        elif command == "/help":
            _send_telegram_message(
                chat_id,
                (
                    "<b>The Kotaro Reminders Bot</b>\n\n"
                    "/upcoming — View the next seven days\n"
                    "/status — Check reminder status\n"
                    "/unsubscribe — Stop reminders\n"
                    "/help — Show this message"
                ),
            )

    except requests.RequestException as telegram_error:
        current_app.logger.exception(
            "Telegram request failed: %s",
            telegram_error,
        )
    except Exception as webhook_error:
        current_app.logger.exception(
            "Telegram webhook processing failed: %s",
            webhook_error,
        )

    return jsonify({"ok": True})



@bp.get("/api/telegram/subscription")
def get_telegram_subscription():
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error

    identity, error = _authenticated_identity()
    if error:
        return error

    subscription = _read_document(
        "telegramSubscriptions",
        identity["uid"],
    )

    if not subscription:
        return jsonify(
            {
                "connected": False,
                "active": False,
            }
        )

    return jsonify(
        {
            "connected": bool(subscription.get("chatId")),
            "active": bool(subscription.get("active")),
            "telegramUsername": (
                subscription.get("telegramUsername")
                or None
            ),
            "telegramFirstName": (
                subscription.get("telegramFirstName")
                or None
            ),
            "linkedAt": subscription.get("linkedAt"),
            "reminderHour": subscription.get(
                "reminderHour",
                8,
            ),
            "timezone": subscription.get(
                "timezone",
                "Asia/Singapore",
            ),
        }
    )



@bp.delete("/api/telegram/subscription")
def delete_telegram_subscription():
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error

    identity, error = _authenticated_identity()
    if error:
        return error

    subscription = _read_document(
        "telegramSubscriptions",
        identity["uid"],
    )

    if not subscription:
        return jsonify(
            {
                "message": (
                    "Telegram subscription was already inactive"
                )
            }
        )

    _deactivate_telegram_subscription(
        identity["uid"],
        reason="nowl_frontend",
    )

    chat_id = subscription.get("chatId")
    if chat_id:
        try:
            _send_telegram_message(
                chat_id,
                (
                    "Your daily Nowl reminders have been "
                    "disabled from the website."
                ),
            )
        except Exception as telegram_error:
            current_app.logger.warning(
                "Could not send Telegram unsubscribe message: %s",
                telegram_error,
            )

    return jsonify(
        {
            "message": (
                "Telegram reminders disabled successfully"
            )
        }
    )



@bp.post("/api/telegram/test")
def send_telegram_test_message():
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error

    telegram_error = _require_telegram_configuration()
    if telegram_error:
        return telegram_error

    identity, error = _authenticated_identity()
    if error:
        return error

    subscription = _read_document(
        "telegramSubscriptions",
        identity["uid"],
    )

    if (
        not subscription
        or not subscription.get("active")
        or not subscription.get("chatId")
    ):
        return jsonify(
            {
                "error": (
                    "Connect Telegram before sending a test message"
                )
            }
        ), 409

    try:
        _send_telegram_message(
            subscription["chatId"],
            (
                "<b>The Nowl In One test reminder</b> 🦉\n\n"
                "Your Telegram subscription is working correctly."
            ),
        )
    except requests.RequestException as telegram_error:
        current_app.logger.exception(
            "Could not send Telegram test message: %s",
            telegram_error,
        )
        return jsonify(
            {"error": "Could not send Telegram message"}
        ), 502

    return jsonify(
        {"message": "Test reminder sent"}
    )



@bp.post("/api/internal/telegram/send-daily-reminders")
def send_daily_telegram_reminders():
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error

    telegram_error = _require_telegram_configuration()
    if telegram_error:
        return telegram_error

    if not _verify_cron_secret():
        return jsonify(
            {"error": "Invalid cron secret"}
        ), 403

    today_key = datetime.now(
        SINGAPORE_TZ
    ).date().isoformat()

    sent_count = 0
    failed_count = 0
    skipped_count = 0
    failures: list[dict[str, str]] = []

    snapshots = (
        FIRESTORE_DB
        .collection("telegramSubscriptions")
        .where("active", "==", True)
        .stream()
    )

    for snapshot in snapshots:
        uid = snapshot.id
        subscription = snapshot.to_dict() or {}
        chat_id = str(
            subscription.get("chatId") or ""
        ).strip()

        if not chat_id:
            skipped_count += 1
            continue

        try:
            user = _read_document("users", uid) or {}
            items = _get_user_week_ahead_items(uid)

            _send_telegram_message(
                chat_id,
                _build_week_ahead_message(
                    str(
                        user.get("displayName")
                        or subscription.get(
                            "telegramFirstName"
                        )
                        or "there"
                    ),
                    items,
                ),
            )

            # Do not persist delivery state. Each cron invocation sends
            # the current reminder independently.
            sent_count += 1

        except Exception as delivery_error:
            failed_count += 1
            error_message = str(delivery_error)[:300]
            failures.append(
                {
                    "uid": uid,
                    "error": error_message,
                }
            )

            current_app.logger.exception(
                "Telegram reminder failed for user %s: %s",
                uid,
                delivery_error,
            )

    return jsonify(
        {
            "message": "Daily Telegram reminder run completed",
            "date": today_key,
            "sent": sent_count,
            "failed": failed_count,
            "skipped": skipped_count,
            "failures": failures,
        }
    )



@bp.post("/api/internal/telegram/send-daily-news")
def send_daily_telegram_news():
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error

    telegram_error = _require_telegram_configuration()
    if telegram_error:
        return telegram_error

    huggingface_error = _require_huggingface_configuration()
    if huggingface_error:
        return huggingface_error

    if not _verify_cron_secret():
        return jsonify(
            {"error": "Invalid cron secret"}
        ), 403

    today_key = datetime.now(
        SINGAPORE_TZ
    ).date().isoformat()

    # Generate only once, regardless of subscriber count.
    try:
        singapore_summary = (
            _generate_scheduled_news_summary(
                "singapore"
            )
        )

        global_summary = (
            _generate_scheduled_news_summary(
                "global"
            )
        )

        singapore_message = (
            _build_telegram_news_message(
                singapore_summary,
                "singapore",
            )
        )

        global_message = (
            _build_telegram_news_message(
                global_summary,
                "global",
            )
        )

    except requests.Timeout:
        current_app.logger.exception(
            "Scheduled news summary timed out"
        )
        return jsonify({
            "error": (
                "The scheduled news summary took too long "
                "to generate"
            )
        }), 504

    except Exception as summary_error:
        current_app.logger.exception(
            "Could not generate scheduled news summaries: %s",
            summary_error,
        )
        return jsonify({
            "error": (
                "Could not generate scheduled news summaries"
            ),
            "details": str(summary_error)[:300],
        }), 502

    sent_count = 0
    failed_count = 0
    skipped_count = 0
    failures: list[dict[str, str]] = []

    snapshots = (
        FIRESTORE_DB
        .collection("telegramSubscriptions")
        .where("active", "==", True)
        .stream()
    )

    for snapshot in snapshots:
        uid = snapshot.id
        subscription = snapshot.to_dict() or {}

        chat_id = str(
            subscription.get("chatId") or ""
        ).strip()

        if not chat_id:
            skipped_count += 1
            continue

        # Prevent duplicate news digests on the same date.
        if (
            subscription.get(
                "lastNewsSummarySentDate"
            )
            == today_key
        ):
            skipped_count += 1
            continue

        try:
            _send_telegram_message(
                chat_id,
                singapore_message,
            )

            _send_telegram_message(
                chat_id,
                global_message,
            )

            _write_document(
                "telegramSubscriptions",
                uid,
                {
                    "lastNewsSummarySentAt": _now_iso(),
                    "lastNewsSummarySentDate": today_key,
                    "lastNewsSummaryStatus": "sent",
                    "lastNewsSummarySingaporeEventCount": len(
                        singapore_summary.get(
                            "events",
                            [],
                        )
                    ),
                    "lastNewsSummaryGlobalEventCount": len(
                        global_summary.get(
                            "events",
                            [],
                        )
                    ),
                    "updatedAt": _now_iso(),
                },
                merge=True,
            )

            sent_count += 1

        except Exception as delivery_error:
            failed_count += 1
            error_message = str(
                delivery_error
            )[:300]

            failures.append({
                "uid": uid,
                "error": error_message,
            })

            _write_document(
                "telegramSubscriptions",
                uid,
                {
                    "lastNewsSummaryStatus": "failed",
                    "lastNewsSummaryError": error_message,
                    "lastNewsSummaryAttemptAt": _now_iso(),
                    "updatedAt": _now_iso(),
                },
                merge=True,
            )

            current_app.logger.exception(
                "News summary delivery failed for user %s: %s",
                uid,
                delivery_error,
            )

    return jsonify({
        "message": (
            "Daily Telegram news summary run completed"
        ),
        "date": today_key,
        "sent": sent_count,
        "failed": failed_count,
        "skipped": skipped_count,
        "singaporeEventCount": len(
            singapore_summary.get("events", [])
        ),
        "globalEventCount": len(
            global_summary.get("events", [])
        ),
        "failures": failures,
    }), 200

