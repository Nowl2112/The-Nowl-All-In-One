"""HTTP routes for this feature area."""

from flask import Blueprint

from core import *  # noqa: F403 - shared legacy helpers during migration

bp = Blueprint("task_boards", __name__)


@bp.delete("/api/task-boards/<board_id>/columns/<column_id>")
def delete_task_board_column(board_id: str, column_id: str):
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error

    identity, error = _authenticated_identity()
    if error:
        return error

    board_ref, board, access_error = _require_task_board_access(
        board_id,
        identity["uid"],
        minimum_role="owner",
    )
    if access_error:
        return access_error

    columns = board.get("columns") or []

    existing = next(
        (column for column in columns if column["id"] == column_id),
        None,
    )

    if existing is None:
        return jsonify({"error": "Column not found"}), 404

    if len(columns) == 1:
        return jsonify({
            "error": "A board must contain at least one column"
        }), 400

    # Check for cards still inside this column
    cards = _load_task_board_cards(board_id)
    cards_in_column = [
        card for card in cards
        if card.get("columnId") == column_id
    ]

    if cards_in_column:
        return jsonify({
            "error": "Column still contains cards",
            "cardCount": len(cards_in_column),
            "cardIds": [card["id"] for card in cards_in_column],
        }), 409

    new_columns = [
        column
        for column in columns
        if column["id"] != column_id
    ]

    # Re-number positions
    for index, column in enumerate(new_columns):
        column["position"] = index

    try:
        board_ref.set({
            "columns": new_columns,
            "updatedAt": _now_iso(),
        }, merge=True)

    except Exception as database_error:
        current_app.logger.exception(
            "Could not delete board column: %s",
            database_error,
        )
        return jsonify({
            "error": "Could not delete board column"
        }), 500

    return jsonify({
        "message": "Column deleted",
        "deletedColumn": existing,
        "columns": new_columns,
    }), 200



@bp.post("/api/task-boards")
def create_task_board():
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error
    identity, error = _authenticated_identity()
    if error:
        return error
    user = _get_or_create_user(
        identity["uid"], identity["email"], identity["name"])
    payload = request.get_json(silent=True) or {}
    name = str(payload.get("name") or "").strip()
    description = str(payload.get("description") or "").strip()
    if not name:
        return jsonify({"error": "Board name is required"}), 400
    if len(name) > 120:
        return jsonify({"error": "Board name cannot exceed 120 characters"}), 400
    if len(description) > 2000:
        return jsonify({"error": "Board description cannot exceed 2000 characters"}), 400
    columns, columns_error = _normalize_board_columns(payload.get("columns"))
    if columns_error:
        return jsonify({"error": columns_error}), 400
    members, members_error = _resolve_initial_board_members(
        payload.get("members"), identity["uid"]
    )
    if members_error:
        return jsonify({"error": members_error}), 400
    now = _now_iso()
    board_ref = FIRESTORE_DB.collection("taskBoards").document()
    board = {
        "name": name,
        "description": description,
        "ownerId": identity["uid"],
        "ownerDisplayName": str(user.get("displayName") or identity["name"] or "User").strip(),
        "ownerProfilePicLink": _profile_picture_link(user),
        "members": members,
        "columns": columns,
        "createdAt": now,
        "updatedAt": now,
    }
    try:
        board_ref.set(board)
    except Exception as database_error:
        current_app.logger.exception("Could not create task board: %s", database_error)
        return jsonify({"error": "Could not create task board"}), 500
    return jsonify({"message": "Task board created", "board": _task_board_response(board_ref.id, board)}), 201



@bp.get("/api/task-boards")
def get_task_boards():
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error
    identity, error = _authenticated_identity()
    if error:
        return error
    boards = []
    try:
        for snapshot in FIRESTORE_DB.collection("taskBoards").stream():
            board = snapshot.to_dict() or {}
            role = _task_board_user_role(board, identity["uid"])
            if role is None:
                continue
            response_board = _task_board_response(snapshot.id, board)
            response_board["currentUserRole"] = role
            boards.append(response_board)
    except Exception as database_error:
        current_app.logger.exception("Could not load task boards: %s", database_error)
        return jsonify({"error": "Could not load task boards"}), 500
    boards.sort(key=lambda board: str(
        board.get("updatedAt") or ""), reverse=True)
    return jsonify({"boards": boards, "count": len(boards)})



@bp.get("/api/task-boards/<board_id>")
def get_task_board(board_id: str):
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error
    identity, error = _authenticated_identity()
    if error:
        return error
    _, board, access_error = _require_task_board_access(
        board_id, identity["uid"], minimum_role="viewer")
    if access_error:
        return access_error
    try:
        cards = _load_task_board_cards(board_id)
    except Exception as database_error:
        current_app.logger.exception(
            "Could not load task board cards: %s", database_error)
        return jsonify({"error": "Could not load task board cards"}), 500
    response_board = _task_board_response(board_id, board)
    response_board["currentUserRole"] = _task_board_user_role(
        board, identity["uid"])
    return jsonify({"board": response_board, "cards": cards, "cardCount": len(cards)})



@bp.patch("/api/task-boards/<board_id>")
def update_task_board(board_id: str):
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error
    identity, error = _authenticated_identity()
    if error:
        return error
    board_ref, board, access_error = _require_task_board_access(
        board_id, identity["uid"], minimum_role="owner")
    if access_error:
        return access_error
    payload = request.get_json(silent=True) or {}
    updates = {}
    if "name" in payload:
        name = str(payload.get("name") or "").strip()
        if not name:
            return jsonify({"error": "Board name cannot be empty"}), 400
        if len(name) > 120:
            return jsonify({"error": "Board name cannot exceed 120 characters"}), 400
        updates["name"] = name
    if "description" in payload:
        description = str(payload.get("description") or "").strip()
        if len(description) > 2000:
            return jsonify({"error": "Board description cannot exceed 2000 characters"}), 400
        updates["description"] = description
    if "columns" in payload:
        columns, columns_error = _normalize_board_columns(
            payload.get("columns"))
        if columns_error:
            return jsonify({"error": columns_error}), 400
        removed = _board_column_ids(
            board) - {column["id"] for column in columns}
        if removed:
            used = sorted({card["columnId"] for card in _load_task_board_cards(
                board_id) if card.get("columnId") in removed})
            if used:
                return jsonify({"error": "Move or delete cards from removed columns first", "columnsInUse": used}), 409
        updates["columns"] = columns
    if not updates:
        return jsonify({"error": "No board changes were provided"}), 400
    updates["updatedAt"] = _now_iso()
    try:
        board_ref.set(updates, merge=True)
    except Exception as database_error:
        current_app.logger.exception("Could not update task board: %s", database_error)
        return jsonify({"error": "Could not update task board"}), 500
    return jsonify({"message": "Task board updated", "board": _task_board_response(board_id, {**board, **updates})})



@bp.delete("/api/task-boards/<board_id>")
def delete_task_board(board_id: str):
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error
    identity, error = _authenticated_identity()
    if error:
        return error
    board_ref, board, access_error = _require_task_board_access(
        board_id, identity["uid"], minimum_role="owner")
    if access_error:
        return access_error
    try:
        card_snapshots = list(FIRESTORE_DB.collection(
            "taskBoardCards").where("boardId", "==", board_id).stream())
        for start in range(0, len(card_snapshots), 450):
            batch = FIRESTORE_DB.batch()
            for snapshot in card_snapshots[start:start + 450]:
                batch.delete(snapshot.reference)
            batch.commit()
        board_ref.delete()
    except Exception as database_error:
        current_app.logger.exception("Could not delete task board: %s", database_error)
        return jsonify({"error": "Could not delete task board"}), 500
    return jsonify({"message": "Task board deleted", "deletedBoard": {"id": board_id, "name": board.get("name"), "deletedCardCount": len(card_snapshots)}})



@bp.post("/api/task-boards/<board_id>/members")
def add_task_board_member(board_id: str):
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error
    identity, error = _authenticated_identity()
    if error:
        return error
    board_ref, board, access_error = _require_task_board_access(
        board_id, identity["uid"], minimum_role="owner")
    if access_error:
        return access_error
    payload = request.get_json(silent=True) or {}
    member_uid = str(payload.get("uid") or "").strip()
    role = str(payload.get("role") or "editor").strip().lower()
    if not member_uid:
        return jsonify({"error": "uid is required"}), 400
    if member_uid == identity["uid"]:
        return jsonify({"error": "The board owner cannot be added as a member"}), 400
    if role not in TASK_BOARD_MEMBER_ROLES:
        return jsonify({"error": "role must be viewer or editor"}), 400
    member_user = _read_document("users", member_uid)
    if not member_user:
        return jsonify({"error": "User not found"}), 404
    members = dict(board.get("members") if isinstance(
        board.get("members"), dict) else {})
    members[member_uid] = {
        "uid": member_uid,
        "role": role,
        "displayName": str(member_user.get("displayName") or "User").strip(),
        "email": _normalize_email(member_user.get("email")),
        "profilePicLink": _profile_picture_link(member_user),
        "addedAt": members.get(member_uid, {}).get("addedAt") or _now_iso(),
        "updatedAt": _now_iso(),
    }
    try:
        board_ref.set(
            {"members": members, "updatedAt": _now_iso()}, merge=True)
    except Exception as database_error:
        current_app.logger.exception("Could not add board member: %s", database_error)
        return jsonify({"error": "Could not add board member"}), 500
    return jsonify({"message": "Board member saved", "member": members[member_uid]})



@bp.delete("/api/task-boards/<board_id>/members/<member_uid>")
def remove_task_board_member(board_id: str, member_uid: str):
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error
    identity, error = _authenticated_identity()
    if error:
        return error
    board_ref, board, access_error = _require_task_board_access(
        board_id, identity["uid"], minimum_role="owner")
    if access_error:
        return access_error
    members = dict(board.get("members") if isinstance(
        board.get("members"), dict) else {})
    if member_uid not in members:
        return jsonify({"error": "Board member not found"}), 404
    removed_member = members.pop(member_uid)
    try:
        board_ref.set(
            {"members": members, "updatedAt": _now_iso()}, merge=True)
    except Exception as database_error:
        current_app.logger.exception(
            "Could not remove board member: %s", database_error)
        return jsonify({"error": "Could not remove board member"}), 500
    return jsonify({"message": "Board member removed", "removedMember": removed_member})



@bp.post("/api/task-boards/<board_id>/cards")
def create_task_board_card(board_id: str):
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error
    identity, error = _authenticated_identity()
    if error:
        return error
    board_ref, board, access_error = _require_task_board_access(
        board_id, identity["uid"], minimum_role="editor")
    if access_error:
        return access_error
    payload = request.get_json(silent=True) or {}
    title = str(payload.get("title") or "").strip()
    description = str(payload.get("description") or "").strip()
    column_id = str(payload.get("columnId") or "").strip()
    priority = str(payload.get("priority") or "none").strip().lower()
    if not title:
        return jsonify({"error": "Card title is required"}), 400
    if len(title) > 200:
        return jsonify({"error": "Card title cannot exceed 200 characters"}), 400
    if len(description) > 5000:
        return jsonify({"error": "Card description cannot exceed 5000 characters"}), 400
    column_ids = _board_column_ids(board)
    if not column_id:
        columns = board.get("columns") or []
        column_id = str(columns[0].get("id") or "") if columns else ""
    if column_id not in column_ids:
        return jsonify({"error": "columnId is not part of this board"}), 400
    if priority not in TASK_BOARD_CARD_PRIORITIES:
        return jsonify({"error": "priority must be none, low, medium, high, or urgent"}), 400
    due_at = None
    if payload.get("dueAt") not in (None, ""):
        due_at, due_error = _parse_calendar_datetime(
            payload.get("dueAt"), "dueAt")
        if due_error:
            return jsonify({"error": due_error}), 400
    assignee_ids, assignees, invalid_ids = _resolve_board_assignees(
        _normalize_string_list(payload.get("assigneeIds")), board)
    if invalid_ids:
        return jsonify({"error": "Assignees must be members of the board", "invalidAssigneeIds": invalid_ids}), 400
    checklist, checklist_error = _normalize_board_checklist(
        payload.get("checklist"))
    if checklist_error:
        return jsonify({"error": checklist_error}), 400
    try:
        position = float(payload.get("position", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "position must be a number"}), 400
    user = _read_document("users", identity["uid"]) or {}
    now = _now_iso()
    card_ref = FIRESTORE_DB.collection("taskBoardCards").document()
    card = {
        "boardId": board_id,
        "title": title,
        "description": description,
        "columnId": column_id,
        "position": position,
        "priority": priority,
        "dueAt": due_at.isoformat() if due_at else None,
        "allDay": bool(payload.get("allDay", False)),
        "assigneeIds": assignee_ids,
        "assignees": assignees,
        "labels": _normalize_string_list(payload.get("labels"))[:20],
        "checklist": checklist,
        "calendarItemId": None,
        "calendarOccurrenceKey": None,
        "createdBy": identity["uid"],
        "createdByDisplayName": str(user.get("displayName") or "User").strip(),
        "completedAt": now if column_id == "completed" else None,
        "createdAt": now,
        "updatedAt": now,
    }
    try:
        card_ref.set(card)
        board_ref.set({"updatedAt": now}, merge=True)
    except Exception as database_error:
        current_app.logger.exception("Could not create board card: %s", database_error)
        return jsonify({"error": "Could not create board card"}), 500
    return jsonify({"message": "Task board card created", "card": _task_board_card_response(card_ref.id, card)}), 201



@bp.patch("/api/task-boards/<board_id>/cards/<card_id>")
def update_task_board_card(board_id: str, card_id: str):
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error
    identity, error = _authenticated_identity()
    if error:
        return error
    board_ref, board, access_error = _require_task_board_access(
        board_id, identity["uid"], minimum_role="editor")
    if access_error:
        return access_error
    card_ref = _document("taskBoardCards", card_id)
    snapshot = card_ref.get()
    if not snapshot.exists:
        return jsonify({"error": "Task board card not found"}), 404
    card = snapshot.to_dict() or {}
    if str(card.get("boardId") or "") != board_id:
        return jsonify({"error": "Card does not belong to this board"}), 400
    payload = request.get_json(silent=True) or {}
    updates = {}
    if "title" in payload:
        title = str(payload.get("title") or "").strip()
        if not title:
            return jsonify({"error": "Card title cannot be empty"}), 400
        if len(title) > 200:
            return jsonify({"error": "Card title cannot exceed 200 characters"}), 400
        updates["title"] = title
    if "description" in payload:
        description = str(payload.get("description") or "").strip()
        if len(description) > 5000:
            return jsonify({"error": "Card description cannot exceed 5000 characters"}), 400
        updates["description"] = description
    if "columnId" in payload:
        column_id = str(payload.get("columnId") or "").strip()
        if column_id not in _board_column_ids(board):
            return jsonify({"error": "columnId is not part of this board"}), 400
        previous_column = str(card.get("columnId") or "")
        updates["columnId"] = column_id
        if column_id == "completed" and previous_column != "completed":
            updates["completedAt"] = _now_iso()
        elif previous_column == "completed" and column_id != "completed":
            updates["completedAt"] = None
    if "position" in payload:
        try:
            updates["position"] = float(payload.get("position"))
        except (TypeError, ValueError):
            return jsonify({"error": "position must be a number"}), 400
    if "priority" in payload:
        priority = str(payload.get("priority") or "none").strip().lower()
        if priority not in TASK_BOARD_CARD_PRIORITIES:
            return jsonify({"error": "priority must be none, low, medium, high, or urgent"}), 400
        updates["priority"] = priority
    if "dueAt" in payload:
        if payload.get("dueAt") in (None, ""):
            updates["dueAt"] = None
        else:
            due_at, due_error = _parse_calendar_datetime(
                payload.get("dueAt"), "dueAt")
            if due_error:
                return jsonify({"error": due_error}), 400
            updates["dueAt"] = due_at.isoformat()
    if "allDay" in payload:
        updates["allDay"] = bool(payload.get("allDay"))
    if "assigneeIds" in payload:
        assignee_ids, assignees, invalid_ids = _resolve_board_assignees(
            _normalize_string_list(payload.get("assigneeIds")), board)
        if invalid_ids:
            return jsonify({"error": "Assignees must be members of the board", "invalidAssigneeIds": invalid_ids}), 400
        updates["assigneeIds"] = assignee_ids
        updates["assignees"] = assignees
    if "labels" in payload:
        updates["labels"] = _normalize_string_list(payload.get("labels"))[:20]
    if "checklist" in payload:
        checklist, checklist_error = _normalize_board_checklist(
            payload.get("checklist"))
        if checklist_error:
            return jsonify({"error": checklist_error}), 400
        updates["checklist"] = checklist
    if not updates:
        return jsonify({"error": "No card changes were provided"}), 400
    updates["updatedAt"] = _now_iso()
    try:
        card_ref.set(updates, merge=True)
        board_ref.set({"updatedAt": updates["updatedAt"]}, merge=True)
    except Exception as database_error:
        current_app.logger.exception("Could not update board card: %s", database_error)
        return jsonify({"error": "Could not update board card"}), 500
    return jsonify({"message": "Task board card updated", "card": _task_board_card_response(card_id, {**card, **updates})})



@bp.delete("/api/task-boards/<board_id>/cards/<card_id>")
def delete_task_board_card(board_id: str, card_id: str):
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error
    identity, error = _authenticated_identity()
    if error:
        return error
    board_ref, _, access_error = _require_task_board_access(
        board_id, identity["uid"], minimum_role="editor")
    if access_error:
        return access_error
    card_ref = _document("taskBoardCards", card_id)
    snapshot = card_ref.get()
    if not snapshot.exists:
        return jsonify({"error": "Task board card not found"}), 404
    card = snapshot.to_dict() or {}
    if str(card.get("boardId") or "") != board_id:
        return jsonify({"error": "Card does not belong to this board"}), 400
    try:
        card_ref.delete()
        board_ref.set({"updatedAt": _now_iso()}, merge=True)
    except Exception as database_error:
        current_app.logger.exception("Could not delete board card: %s", database_error)
        return jsonify({"error": "Could not delete board card"}), 500
    return jsonify({"message": "Task board card deleted", "deletedCard": {"id": card_id, "title": card.get("title"), "boardId": board_id}})



@bp.post("/api/task-boards/<board_id>/import-calendar")
def import_calendar_items_to_task_board(board_id: str):
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error
    identity, error = _authenticated_identity()
    if error:
        return error
    board_ref, board, access_error = _require_task_board_access(
        board_id, identity["uid"], minimum_role="editor")
    if access_error:
        return access_error
    payload = request.get_json(silent=True) or {}
    calendar_item_ids = _normalize_string_list(payload.get("calendarItemIds"))
    column_id = str(payload.get("columnId") or "").strip()
    if not calendar_item_ids:
        return jsonify({"error": "calendarItemIds is required"}), 400
    if len(calendar_item_ids) > 100:
        return jsonify({"error": "You can import at most 100 calendar items at once"}), 400
    if not column_id:
        columns = board.get("columns") or []
        column_id = str(columns[0].get("id") or "") if columns else ""
    if column_id not in _board_column_ids(board):
        return jsonify({"error": "columnId is not part of this board"}), 400
    user = _get_or_create_user(
        identity["uid"], identity["email"], identity["name"])
    family_name = _normalize_family_name(user.get("familyName"))
    existing_cards = _load_task_board_cards(board_id)
    existing_links = {
        (str(card.get("calendarItemId") or ""), str(
            card.get("calendarOccurrenceKey") or ""))
        for card in existing_cards if card.get("calendarItemId")
    }
    imported_cards, skipped_items = [], []
    for requested_id in calendar_item_ids:
        series_id, occurrence_key = _split_occurrence_id(requested_id)
        calendar_item = _read_document("calendarItems", series_id)
        if not calendar_item:
            skipped_items.append({"id": requested_id, "reason": "not_found"})
            continue
        if not _calendar_item_is_visible_to_user(calendar_item, identity["uid"], family_name):
            skipped_items.append(
                {"id": requested_id, "reason": "not_accessible"})
            continue
        link_key = (series_id, occurrence_key or "")
        if link_key in existing_links:
            skipped_items.append(
                {"id": requested_id, "reason": "already_imported"})
            continue
        imported_item = dict(calendar_item)
        if occurrence_key:
            occurrence_datetime = _parse_stored_datetime(occurrence_key)
            if occurrence_datetime is None:
                skipped_items.append(
                    {"id": requested_id, "reason": "invalid_occurrence"})
                continue
            expanded = _expand_calendar_item(
                series_id,
                calendar_item,
                occurrence_datetime - timedelta(seconds=1),
                occurrence_datetime + timedelta(seconds=1),
            )
            if not expanded:
                skipped_items.append(
                    {"id": requested_id, "reason": "occurrence_not_found"})
                continue
            imported_item = expanded[0]
        item_type = str(imported_item.get("itemType") or "").strip().lower()
        due_at = imported_item.get(
            "startAt") if item_type == "event" else imported_item.get("dueAt")
        card_column_id = column_id
        if item_type == "task" and imported_item.get("status") == "completed" and "completed" in _board_column_ids(board):
            card_column_id = "completed"
        now = _now_iso()
        card_ref = FIRESTORE_DB.collection("taskBoardCards").document()
        card = {
            "boardId": board_id,
            "title": str(imported_item.get("title") or "Untitled calendar item"),
            "description": str(imported_item.get("description") or ""),
            "columnId": card_column_id,
            "position": len(existing_cards) + len(imported_cards),
            "priority": "none",
            "dueAt": due_at,
            "allDay": bool(imported_item.get("allDay", False)),
            "assigneeIds": [],
            "assignees": [],
            "labels": ["Calendar", item_type.capitalize()],
            "checklist": [],
            "calendarItemId": series_id,
            "calendarOccurrenceKey": occurrence_key,
            "createdBy": identity["uid"],
            "createdByDisplayName": str(user.get("displayName") or "User").strip(),
            "completedAt": now if card_column_id == "completed" else None,
            "createdAt": now,
            "updatedAt": now,
        }
        try:
            card_ref.set(card)
        except Exception as database_error:
            current_app.logger.exception(
                "Could not import calendar item %s: %s", requested_id, database_error)
            skipped_items.append(
                {"id": requested_id, "reason": "database_error"})
            continue
        imported_cards.append(_task_board_card_response(card_ref.id, card))
        existing_links.add(link_key)
    if imported_cards:
        board_ref.set({"updatedAt": _now_iso()}, merge=True)
    return jsonify({
        "message": f"Imported {len(imported_cards)} calendar item(s)",
        "cards": imported_cards,
        "importedCount": len(imported_cards),
        "skipped": skipped_items,
        "skippedCount": len(skipped_items),
    }), 201 if imported_cards else 200

