"""HTTP routes for this feature area."""

from flask import Blueprint

from core import *  # noqa: F403 - shared legacy helpers during migration

bp = Blueprint("calendar", __name__)


@bp.post("/api/calendar/items")
def create_calendar_item():
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error

    identity, error = _authenticated_identity()
    if error:
        return error

    user = _get_or_create_user(
        identity["uid"],
        identity["email"],
        identity["name"],
    )
    payload = request.get_json(silent=True) or {}

    item_type = str(payload.get("itemType") or "").strip().lower()
    title = str(payload.get("title") or "").strip()
    description = str(payload.get("description") or "").strip()
    visibility = str(payload.get("visibility") or "personal").strip().lower()
    all_day = bool(payload.get("allDay", False))

    if item_type not in CALENDAR_ITEM_TYPES:
        return jsonify(
            {"error": "itemType must be event, task, or reminder"}
        ), 400

    if not title:
        return jsonify({"error": "Title is required"}), 400

    if len(title) > 200:
        return jsonify(
            {"error": "Title cannot exceed 200 characters"}
        ), 400

    if len(description) > 2000:
        return jsonify(
            {"error": "Description cannot exceed 2000 characters"}
        ), 400

    if visibility not in CALENDAR_VISIBILITIES:
        return jsonify(
            {"error": "visibility must be personal, family, or all"}
        ), 400

    family_name = _normalize_family_name(user.get("familyName"))
    if visibility == "family" and not family_name:
        return jsonify(
            {"error": "Your user document does not have a familyName"}
        ), 400

    requested_tagged_ids = _normalize_string_list(
        payload.get("taggedUserIds")
    )
    if len(requested_tagged_ids) > 50:
        return jsonify(
            {"error": "You can tag at most 50 users on one item"}
        ), 400

    tagged_user_ids, tagged_users, missing_ids = _resolve_tagged_users(
        requested_tagged_ids,
        identity["uid"],
    )
    if missing_ids:
        return jsonify(
            {
                "error": "One or more tagged users do not exist",
                "missingUserIds": missing_ids,
            }
        ), 400

    start_at = None
    end_at = None
    due_at = None
    now_datetime = datetime.now(SINGAPORE_TZ)
    today = now_datetime.date()

    if item_type == "event":
        start_at, datetime_error = _parse_calendar_datetime(
            payload.get("startAt"),
            "startAt",
        )
        if datetime_error:
            return jsonify({"error": datetime_error}), 400

        end_at, datetime_error = _parse_calendar_datetime(
            payload.get("endAt"),
            "endAt",
        )
        if datetime_error:
            return jsonify({"error": datetime_error}), 400

        if end_at < start_at:
            return jsonify(
                {"error": "endAt must be the same as or later than startAt"}
            ), 400

        if all_day:
            if start_at.date() < today:
                return jsonify(
                    {"error": "Calendar items cannot be created in the past"}
                ), 400
        elif start_at < now_datetime:
            return jsonify(
                {"error": "Calendar items cannot be created in the past"}
            ), 400
    elif item_type == "task":
        start_at, datetime_error = _parse_calendar_datetime(
            payload.get("startAt"),
            "startAt",
        )
        if datetime_error:
            return jsonify({"error": datetime_error}), 400

        due_at, datetime_error = _parse_calendar_datetime(
            payload.get("dueAt"),
            "dueAt",
        )
        if datetime_error:
            return jsonify({"error": datetime_error}), 400

        if due_at < start_at:
            return jsonify(
                {"error": "dueAt must be the same as or later than startAt"}
            ), 400

        if all_day:
            if start_at.date() < today:
                return jsonify(
                    {"error": "Calendar items cannot be created in the past"}
                ), 400
        elif start_at < now_datetime:
            return jsonify(
                {"error": "Calendar items cannot be created in the past"}
            ), 400

    else:
        due_at, datetime_error = _parse_calendar_datetime(
            payload.get("dueAt"),
            "dueAt",
        )
        if datetime_error:
            return jsonify({"error": datetime_error}), 400

        if all_day:
            if due_at.date() < today:
                return jsonify(
                    {"error": "Calendar items cannot be created in the past"}
                ), 400
        elif due_at < now_datetime:
            return jsonify(
                {"error": "Calendar items cannot be created in the past"}
            ), 400

    recurrence, recurrence_error = _normalize_recurrence(
        payload.get("recurrence"),
        anchor=(start_at if item_type == "event" else due_at),
    )
    if recurrence_error:
        return jsonify({"error": recurrence_error}), 400

    status = "pending" if item_type == "task" else None
    now = _now_iso()
    item_ref = FIRESTORE_DB.collection("calendarItems").document()

    item = {
        "itemType": item_type,
        "title": title,
        "description": description,
        "startAt": start_at.isoformat() if start_at else None,
        "endAt": end_at.isoformat() if end_at else None,
        "dueAt": due_at.isoformat() if due_at else None,
        "allDay": all_day,
        "status": status,
        "completedAt": None,
        "visibility": visibility,
        "ownerId": identity["uid"],
        "ownerEmail": identity["email"],
        "ownerDisplayName": (
            str(user.get("displayName") or "").strip()
            or identity["name"]
            or (
                identity["email"].split("@")[0]
                if identity["email"]
                else "User"
            )
        ),
        "familyName": family_name if visibility == "family" else None,
        "taggedUserIds": tagged_user_ids,
        "taggedUsers": tagged_users,
        "createdAt": now,
        "updatedAt": now,
        "recurrence": recurrence,
        "completedOccurrenceKeys": [],
        "skippedOccurrenceKeys": [],
    }

    try:
        item_ref.set(item)
    except Exception as database_error:
        current_app.logger.exception(
            "Could not create calendar item: %s",
            database_error,
        )
        return jsonify({"error": "Could not create calendar item"}), 500

    return jsonify(
        {
            "message": f"Calendar {item_type} created",
            "item": _calendar_item_response(item_ref.id, item),
        }
    ), 201



@bp.patch("/api/calendar/items/<item_id>")
def update_calendar_item(item_id: str):
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error

    identity, error = _authenticated_identity()
    if error:
        return error

    normalized_item_id, _ = _split_occurrence_id(str(item_id or "").strip())
    if not normalized_item_id:
        return jsonify(
            {"error": "Calendar item ID is required"}
        ), 400

    item_ref = _document(
        "calendarItems",
        normalized_item_id,
    )

    try:
        snapshot = item_ref.get()
    except Exception as database_error:
        current_app.logger.exception(
            "Could not retrieve calendar item for editing: %s",
            database_error,
        )
        return jsonify(
            {"error": "Could not retrieve calendar item"}
        ), 500

    if not snapshot.exists:
        return jsonify(
            {"error": "Calendar item not found"}
        ), 404

    existing_item = snapshot.to_dict() or {}

    if str(existing_item.get("ownerId") or "") != identity["uid"]:
        return jsonify(
            {
                "error": (
                    "Only the user who created this item can edit it"
                )
            }
        ), 403

    user = _get_or_create_user(
        identity["uid"],
        identity["email"],
        identity["name"],
    )

    payload = request.get_json(silent=True) or {}

    item_type = str(
        payload.get(
            "itemType",
            existing_item.get("itemType"),
        )
        or ""
    ).strip().lower()

    title = str(
        payload.get(
            "title",
            existing_item.get("title"),
        )
        or ""
    ).strip()

    description = str(
        payload.get(
            "description",
            existing_item.get("description"),
        )
        or ""
    ).strip()

    visibility = str(
        payload.get(
            "visibility",
            existing_item.get("visibility", "personal"),
        )
        or ""
    ).strip().lower()

    all_day = bool(
        payload.get(
            "allDay",
            existing_item.get("allDay", False),
        )
    )

    if item_type not in CALENDAR_ITEM_TYPES:
        return jsonify(
            {"error": "itemType must be event, task, or reminder"}
        ), 400

    if not title:
        return jsonify(
            {"error": "Title is required"}
        ), 400

    if len(title) > 200:
        return jsonify(
            {"error": "Title cannot exceed 200 characters"}
        ), 400

    if len(description) > 2000:
        return jsonify(
            {"error": "Description cannot exceed 2000 characters"}
        ), 400

    if visibility not in CALENDAR_VISIBILITIES:
        return jsonify(
            {
                "error": (
                    "visibility must be personal, family, or all"
                )
            }
        ), 400

    family_name = _normalize_family_name(
        user.get("familyName")
    )

    if visibility == "family" and not family_name:
        return jsonify(
            {
                "error": (
                    "Your user document does not have a familyName"
                )
            }
        ), 400

    requested_tagged_ids = _normalize_string_list(
        payload.get(
            "taggedUserIds",
            existing_item.get("taggedUserIds", []),
        )
    )

    if len(requested_tagged_ids) > 50:
        return jsonify(
            {
                "error": (
                    "You can tag at most 50 users on one item"
                )
            }
        ), 400

    tagged_user_ids, tagged_users, missing_ids = (
        _resolve_tagged_users(
            requested_tagged_ids,
            identity["uid"],
        )
    )

    if missing_ids:
        return jsonify(
            {
                "error": "One or more tagged users do not exist",
                "missingUserIds": missing_ids,
            }
        ), 400

    start_at = None
    end_at = None
    due_at = None

    now_datetime = datetime.now(SINGAPORE_TZ)
    today = now_datetime.date()

    if item_type == "event":
        start_at, datetime_error = _parse_calendar_datetime(
            payload.get(
                "startAt",
                existing_item.get("startAt"),
            ),
            "startAt",
        )

        if datetime_error:
            return jsonify(
                {"error": datetime_error}
            ), 400

        end_at, datetime_error = _parse_calendar_datetime(
            payload.get(
                "endAt",
                existing_item.get("endAt"),
            ),
            "endAt",
        )

        if datetime_error:
            return jsonify(
                {"error": datetime_error}
            ), 400

        if end_at < start_at:
            return jsonify(
                {
                    "error": (
                        "endAt must be the same as or later "
                        "than startAt"
                    )
                }
            ), 400

        if all_day:
            if start_at.date() < today:
                return jsonify(
                    {
                        "error": (
                            "Calendar items cannot be moved "
                            "into the past"
                        )
                    }
                ), 400
        elif start_at < now_datetime:
            return jsonify(
                {
                    "error": (
                        "Calendar items cannot be moved "
                        "into the past"
                    )
                }
            ), 400

    elif item_type == "task":
        start_at, datetime_error = _parse_calendar_datetime(
            payload.get(
                "startAt",
                existing_item.get("startAt"),
            ),
            "startAt",
        )

        if datetime_error:
            return jsonify(
                {"error": datetime_error}
            ), 400

        due_at, datetime_error = _parse_calendar_datetime(
            payload.get(
                "dueAt",
                existing_item.get("dueAt"),
            ),
            "dueAt",
        )

        if datetime_error:
            return jsonify(
                {"error": datetime_error}
            ), 400

        if due_at < start_at:
            return jsonify(
                {
                    "error": (
                        "dueAt must be the same as or later "
                        "than startAt"
                    )
                }
            ), 400

        if all_day:
            if start_at.date() < today:
                return jsonify(
                    {
                        "error": (
                            "Calendar items cannot be moved "
                            "into the past"
                        )
                    }
                ), 400
        elif start_at < now_datetime:
            return jsonify(
                {
                    "error": (
                        "Calendar items cannot be moved "
                        "into the past"
                    )
                }
            ), 400

    else:
        due_at, datetime_error = _parse_calendar_datetime(
            payload.get(
                "dueAt",
                existing_item.get("dueAt"),
            ),
            "dueAt",
        )

        if datetime_error:
            return jsonify(
                {"error": datetime_error}
            ), 400

        if all_day:
            if due_at.date() < today:
                return jsonify(
                    {
                        "error": (
                            "Calendar items cannot be moved "
                            "into the past"
                        )
                    }
                ), 400
        elif due_at < now_datetime:
            return jsonify(
                {
                    "error": (
                        "Calendar items cannot be moved "
                        "into the past"
                    )
                }
            ), 400

    recurrence, recurrence_error = _normalize_recurrence(
        payload.get("recurrence", existing_item.get("recurrence")),
        anchor=(start_at if item_type == "event" else due_at),
    )
    if recurrence_error:
        return jsonify({"error": recurrence_error}), 400

    existing_status = str(
        existing_item.get("status") or "pending"
    ).strip().lower()

    status = (
        existing_status
        if item_type == "task"
        else None
    )

    if item_type == "task":
        requested_status = str(
            payload.get("status") or status
        ).strip().lower()

        if requested_status not in CALENDAR_TASK_STATUSES:
            return jsonify(
                {
                    "error": (
                        "status must be pending, in_progress, "
                        "or completed"
                    )
                }
            ), 400

        status = requested_status

    updates = {
        "itemType": item_type,
        "title": title,
        "description": description,
        "startAt": (
            start_at.isoformat()
            if start_at
            else None
        ),
        "endAt": (
            end_at.isoformat()
            if end_at
            else None
        ),
        "dueAt": (
            due_at.isoformat()
            if due_at
            else None
        ),
        "allDay": all_day,
        "status": status,
        "completedAt": (
            existing_item.get("completedAt")
            if status == "completed"
            else None
        ),
        "visibility": visibility,
        "familyName": (
            family_name
            if visibility == "family"
            else None
        ),
        "taggedUserIds": tagged_user_ids,
        "taggedUsers": tagged_users,
        "updatedAt": _now_iso(),
        "recurrence": recurrence,
    }

    if (
        item_type == "task"
        and status == "completed"
        and not updates["completedAt"]
    ):
        updates["completedAt"] = _now_iso()

    try:
        item_ref.set(updates, merge=True)
    except Exception as database_error:
        current_app.logger.exception(
            "Could not update calendar item: %s",
            database_error,
        )
        return jsonify(
            {"error": "Could not update calendar item"}
        ), 500

    updated_item = {
        **existing_item,
        **updates,
    }

    return jsonify(
        {
            "message": "Calendar item updated successfully",
            "item": _calendar_item_response(
                normalized_item_id,
                updated_item,
            ),
        }
    ), 200



@bp.delete("/api/calendar/items/<item_id>")
def delete_calendar_item(item_id: str):
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error

    identity, error = _authenticated_identity()
    if error:
        return error

    normalized_item_id, _ = _split_occurrence_id(str(item_id or "").strip())
    if not normalized_item_id:
        return jsonify(
            {"error": "Calendar item ID is required"}
        ), 400

    item_ref = _document(
        "calendarItems",
        normalized_item_id,
    )

    try:
        snapshot = item_ref.get()
    except Exception as database_error:
        current_app.logger.exception(
            "Could not retrieve calendar item for deletion: %s",
            database_error,
        )
        return jsonify(
            {"error": "Could not retrieve calendar item"}
        ), 500

    if not snapshot.exists:
        return jsonify(
            {"error": "Calendar item not found"}
        ), 404

    item = snapshot.to_dict() or {}
    owner_id = str(item.get("ownerId") or "").strip()

    if owner_id != identity["uid"]:
        return jsonify(
            {
                "error": (
                    "Only the user who created this item can delete it"
                )
            }
        ), 403

    try:
        item_ref.delete()
    except Exception as database_error:
        current_app.logger.exception(
            "Could not delete calendar item: %s",
            database_error,
        )
        return jsonify(
            {"error": "Could not delete calendar item"}
        ), 500

    return jsonify(
        {
            "message": "Calendar item deleted successfully",
            "deletedItem": {
                "id": normalized_item_id,
                "itemType": item.get("itemType"),
                "title": str(item.get("title") or ""),
            },
        }
    ), 200



@bp.get("/api/calendar/items")
def get_visible_calendar_items():
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error

    identity, error = _authenticated_identity()
    if error:
        return error

    user = _get_or_create_user(
        identity["uid"],
        identity["email"],
        identity["name"],
    )
    family_name = _normalize_family_name(user.get("familyName"))
    item_type = str(request.args.get("type") or "").strip().lower() or None

    if item_type and item_type not in CALENDAR_ITEM_TYPES:
        return jsonify(
            {"error": "type must be event, task, or reminder"}
        ), 400

    range_start, range_end, range_error = _parse_calendar_range()
    if range_error:
        return jsonify({"error": range_error}), 400

    try:
        items = _load_calendar_items(
            uid=identity["uid"],
            family_name=family_name,
            visibility_scope="visible",
            item_type=item_type,
            range_start=range_start,
            range_end=range_end,
        )
    except Exception as database_error:
        current_app.logger.exception(
            "Could not load calendar items: %s",
            database_error,
        )
        return jsonify({"error": "Could not load calendar items"}), 500

    return jsonify(
        {
            "items": items,
            "count": len(items),
            "familyName": family_name or None,
        }
    )



@bp.get("/api/calendar/items/own")
def get_own_calendar_items():
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error

    identity, error = _authenticated_identity()
    if error:
        return error

    item_type = str(request.args.get("type") or "").strip().lower() or None
    if item_type and item_type not in CALENDAR_ITEM_TYPES:
        return jsonify({"error": "Invalid calendar item type"}), 400

    items = _load_calendar_items(
        uid=identity["uid"],
        family_name="",
        visibility_scope="own",
        item_type=item_type,
    )
    return jsonify({"items": items, "count": len(items)})



@bp.get("/api/calendar/items/tagged")
def get_tagged_calendar_items():
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error

    identity, error = _authenticated_identity()
    if error:
        return error

    items = _load_calendar_items(
        uid=identity["uid"],
        family_name="",
        visibility_scope="tagged",
    )
    return jsonify({"items": items, "count": len(items)})



@bp.get("/api/calendar/items/upcoming")
def get_upcoming_calendar_items():
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error

    identity, error = _authenticated_identity()
    if error:
        return error

    user = _get_or_create_user(
        identity["uid"],
        identity["email"],
        identity["name"],
    )
    family_name = _normalize_family_name(user.get("familyName"))
    limit = request.args.get("limit", default=5, type=int)
    limit = max(1, min(limit, 50))
    item_type = str(request.args.get("type") or "").strip().lower() or None

    if item_type and item_type not in CALENDAR_ITEM_TYPES:
        return jsonify(
            {"error": "type must be event, task, or reminder"}
        ), 400

    now = datetime.now(SINGAPORE_TZ)
    visible_items = _load_calendar_items(
        uid=identity["uid"],
        family_name=family_name,
        visibility_scope="visible",
        item_type=item_type,
        range_start=now,
        range_end=now + timedelta(days=365 * 5),
    )

    upcoming: list[tuple[datetime, dict[str, Any]]] = []
    for item in visible_items:
        raw_datetime = _calendar_item_datetime(item)
        if not raw_datetime:
            continue

        parsed_datetime, parse_error = _parse_calendar_datetime(
            raw_datetime,
            "calendar datetime",
        )
        if parse_error or parsed_datetime is None:
            continue

        if parsed_datetime >= now:
            upcoming.append((parsed_datetime, item))

    upcoming.sort(key=lambda entry: entry[0])
    selected_items = [entry[1] for entry in upcoming[:limit]]

    return jsonify(
        {
            "items": selected_items,
            "count": len(selected_items),
            "limit": limit,
        }
    )



@bp.patch("/api/calendar/tasks/<item_id>/status")
def update_calendar_task_status(item_id: str):
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error

    identity, error = _authenticated_identity()
    if error:
        return error

    series_id, occurrence_key = _split_occurrence_id(
        str(item_id or "").strip())
    item_ref = _document("calendarItems", series_id)
    snapshot = item_ref.get()
    if not snapshot.exists:
        return jsonify({"error": "Task not found"}), 404

    item = snapshot.to_dict() or {}
    if item.get("itemType") != "task":
        return jsonify({"error": "Calendar item is not a task"}), 400

    if str(item.get("ownerId") or "") != identity["uid"]:
        return jsonify({"error": "Only the task owner can change its status"}), 403

    payload = request.get_json(silent=True) or {}
    status = str(payload.get("status") or "").strip().lower()
    if status not in CALENDAR_TASK_STATUSES:
        return jsonify(
            {"error": "status must be pending, in_progress, or completed"}
        ), 400

    recurrence = item.get("recurrence") if isinstance(
        item.get("recurrence"), dict) else {}
    is_recurring = str(recurrence.get("frequency") or "none") != "none"

    if is_recurring and occurrence_key:
        completed_keys = set(_normalize_string_list(
            item.get("completedOccurrenceKeys")))
        if status == "completed":
            completed_keys.add(occurrence_key)
        else:
            completed_keys.discard(occurrence_key)
        updates = {
            "completedOccurrenceKeys": sorted(completed_keys),
            "updatedAt": _now_iso(),
        }
        item_ref.set(updates, merge=True)
        item.update(updates)
        occurrence_dt = _parse_stored_datetime(occurrence_key)
        if occurrence_dt is None:
            return jsonify({"error": "Invalid recurring occurrence ID"}), 400
        expanded = _expand_calendar_item(
            series_id,
            item,
            occurrence_dt - timedelta(seconds=1),
            occurrence_dt + timedelta(seconds=1),
        )
        response_item = expanded[0] if expanded else _calendar_item_response(
            item_id, item)
    else:
        updates = {
            "status": status,
            "completedAt": _now_iso() if status == "completed" else None,
            "updatedAt": _now_iso(),
        }
        item_ref.set(updates, merge=True)
        item.update(updates)
        response_item = _calendar_item_response(series_id, item)

    return jsonify({"message": "Task status updated", "item": response_item})

