import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../../context/authContext.jsx";
import kotaroImage from "../../assets/kotaro.png";
import "./taskBoard.css";

const API_BASE_URL =
    import.meta.env.VITE_API_BASE_URL ||
    import.meta.env.VITE_API_BASE ||
    "";

const EMPTY_BOARD_FORM = {
    name: "",
    description: "",
};

const EMPTY_CARD_FORM = {
    title: "",
    description: "",
    columnId: "",
    priority: "none",
    dueAt: "",
    allDay: false,
    labels: "",
    assigneeIds: [],
};

const EMPTY_PRESET_FORM = {
    title: "",
    description: "",
    priority: "none",
    labels: "",
};

function readStoredPresets() {
    try {
        const value = JSON.parse(localStorage.getItem("nowlTaskBoardPresets") || "[]");
        return Array.isArray(value) ? value : [];
    } catch {
        return [];
    }
}

async function readJsonResponse(response) {
    const contentType = response.headers.get("content-type") || "";
    if (!contentType.includes("application/json")) {
        throw new Error(`The server returned an unexpected response (${response.status}).`);
    }
    return response.json();
}

function formatDate(value, allDay = false) {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "";

    return allDay
        ? date.toLocaleDateString("en-SG", {
              day: "numeric",
              month: "short",
              year: "numeric",
          })
        : date.toLocaleString("en-SG", {
              day: "numeric",
              month: "short",
              hour: "numeric",
              minute: "2-digit",
          });
}

function toDatetimeLocal(value) {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
    return local.toISOString().slice(0, 16);
}

function labelsFromText(value) {
    return String(value || "")
        .split(",")
        .map((label) => label.trim())
        .filter(Boolean)
        .slice(0, 20);
}

function nextPosition(cards, columnId) {
    const positions = cards
        .filter((card) => card.columnId === columnId)
        .map((card) => Number(card.position) || 0);

    return positions.length ? Math.max(...positions) + 1 : 0;
}

function avatarText(name) {
    return String(name || "U").trim().charAt(0).toUpperCase() || "U";
}

function TaskBoardPage() {
    const navigate = useNavigate();
    const { currentUser } = useAuth();

    const [boards, setBoards] = useState([]);
    const [activeBoardId, setActiveBoardId] = useState("");
    const [activeBoard, setActiveBoard] = useState(null);
    const [cards, setCards] = useState([]);
    const [calendarTasks, setCalendarTasks] = useState([]);
    const [presets, setPresets] = useState(readStoredPresets);

    const [pageStatus, setPageStatus] = useState("loading");
    const [pageError, setPageError] = useState("");
    const [notice, setNotice] = useState("");

    const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false);
    const [libraryTab, setLibraryTab] = useState("calendar");
    const [librarySearch, setLibrarySearch] = useState("");

    const [boardModalOpen, setBoardModalOpen] = useState(false);
    const [boardForm, setBoardForm] = useState(EMPTY_BOARD_FORM);

    const [cardModalOpen, setCardModalOpen] = useState(false);
    const [editingCard, setEditingCard] = useState(null);
    const [cardForm, setCardForm] = useState(EMPTY_CARD_FORM);

    const [presetModalOpen, setPresetModalOpen] = useState(false);
    const [presetForm, setPresetForm] = useState(EMPTY_PRESET_FORM);

    const [columnModalOpen, setColumnModalOpen] = useState(false);
    const [columnName, setColumnName] = useState("");

    const [draggedItem, setDraggedItem] = useState(null);
    const [saving, setSaving] = useState(false);

    const getAuthHeaders = useCallback(
        async (includeJson = false) => {
            if (!currentUser) throw new Error("You must be signed in.");
            const token = await currentUser.getIdToken();

            return {
                Authorization: `Bearer ${token}`,
                ...(includeJson ? { "Content-Type": "application/json" } : {}),
            };
        },
        [currentUser],
    );

    const apiRequest = useCallback(
        async (path, options = {}) => {
            const headers = await getAuthHeaders(Boolean(options.body));
            const response = await fetch(`${API_BASE_URL}${path}`, {
                cache: "no-store",
                ...options,
                headers: {
                    ...headers,
                    ...(options.headers || {}),
                },
            });
            const data = await readJsonResponse(response);
            if (!response.ok) {
                throw new Error(data.error || "The request could not be completed.");
            }
            return data;
        },
        [getAuthHeaders],
    );

    const loadBoard = useCallback(
        async (boardId) => {
            if (!boardId) {
                setActiveBoard(null);
                setCards([]);
                return;
            }

            const data = await apiRequest(`/api/task-boards/${boardId}`);
            setActiveBoard(data.board || null);
            setCards(Array.isArray(data.cards) ? data.cards : []);
        },
        [apiRequest],
    );

    const loadBoards = useCallback(async () => {
        const data = await apiRequest("/api/task-boards");
        const nextBoards = Array.isArray(data.boards) ? data.boards : [];
        setBoards(nextBoards);

        setActiveBoardId((current) => {
            if (current && nextBoards.some((board) => board.id === current)) {
                return current;
            }
            return nextBoards[0]?.id || "";
        });

        return nextBoards;
    }, [apiRequest]);

    const loadCalendarTasks = useCallback(async () => {
        const data = await apiRequest(
            "/api/calendar/items?type=task",
        );

        const items = Array.isArray(data.items) ? data.items : [];
        setCalendarTasks(
            items
                .filter((item) => item.status !== "completed")
                .sort((a, b) => {
                    const first = new Date(a.dueAt || 0).getTime();
                    const second = new Date(b.dueAt || 0).getTime();
                    return first - second;
                }),
        );
    }, [apiRequest]);

    useEffect(() => {
        if (!currentUser?.uid) return undefined;

        let cancelled = false;
        setPageStatus("loading");
        setPageError("");

        Promise.all([loadBoards(), loadCalendarTasks()])
            .then(() => {
                if (!cancelled) setPageStatus("success");
            })
            .catch((error) => {
                if (cancelled) return;
                console.error("Unable to load task boards:", error);
                setPageError(error.message || "Could not load task boards.");
                setPageStatus("error");
            });

        return () => {
            cancelled = true;
        };
    }, [currentUser?.uid, loadBoards, loadCalendarTasks]);

    useEffect(() => {
        if (!activeBoardId) {
            setActiveBoard(null);
            setCards([]);
            return;
        }

        loadBoard(activeBoardId).catch((error) => {
            console.error("Unable to load board:", error);
            setPageError(error.message || "Could not load this board.");
        });
    }, [activeBoardId, loadBoard]);

    useEffect(() => {
        localStorage.setItem("nowlTaskBoardPresets", JSON.stringify(presets));
    }, [presets]);

    useEffect(() => {
        if (!notice) return undefined;
        const timer = window.setTimeout(() => setNotice(""), 3200);
        return () => window.clearTimeout(timer);
    }, [notice]);

    const canEdit = activeBoard?.currentUserRole === "owner" ||
        activeBoard?.currentUserRole === "editor";
    const isOwner = activeBoard?.currentUserRole === "owner";

    const boardMembers = useMemo(() => {
        if (!activeBoard) return [];

        const owner = {
            uid: activeBoard.ownerId,
            displayName: activeBoard.ownerDisplayName || "Owner",
            profilePicLink: activeBoard.ownerProfilePicLink || "",
            role: "owner",
        };

        return [
            owner,
            ...Object.values(activeBoard.members || {}),
        ];
    }, [activeBoard]);

    const filteredCalendarTasks = useMemo(() => {
        const query = librarySearch.trim().toLowerCase();
        if (!query) return calendarTasks;

        return calendarTasks.filter((task) =>
            `${task.title} ${task.description || ""}`.toLowerCase().includes(query),
        );
    }, [calendarTasks, librarySearch]);

    const filteredPresets = useMemo(() => {
        const query = librarySearch.trim().toLowerCase();
        if (!query) return presets;

        return presets.filter((preset) =>
            `${preset.title} ${preset.description || ""}`.toLowerCase().includes(query),
        );
    }, [presets, librarySearch]);

    function openCreateCard(columnId = "") {
        setEditingCard(null);
        setCardForm({
            ...EMPTY_CARD_FORM,
            columnId: columnId || activeBoard?.columns?.[0]?.id || "",
        });
        setCardModalOpen(true);
    }

    function openEditCard(card) {
        setEditingCard(card);
        setCardForm({
            title: card.title || "",
            description: card.description || "",
            columnId: card.columnId || "",
            priority: card.priority || "none",
            dueAt: toDatetimeLocal(card.dueAt),
            allDay: Boolean(card.allDay),
            labels: (card.labels || []).join(", "),
            assigneeIds: card.assigneeIds || [],
        });
        setCardModalOpen(true);
    }

    async function handleCreateBoard(event) {
        event.preventDefault();
        setSaving(true);
        setPageError("");

        try {
            const data = await apiRequest("/api/task-boards", {
                method: "POST",
                body: JSON.stringify(boardForm),
            });

            const board = data.board;
            setBoards((current) => [board, ...current]);
            setActiveBoardId(board.id);
            setBoardForm(EMPTY_BOARD_FORM);
            setBoardModalOpen(false);
            setNotice("Board created.");
        } catch (error) {
            setPageError(error.message);
        } finally {
            setSaving(false);
        }
    }

    async function handleSaveCard(event) {
        event.preventDefault();
        if (!activeBoard) return;

        setSaving(true);
        setPageError("");

        const payload = {
            title: cardForm.title,
            description: cardForm.description,
            columnId: cardForm.columnId,
            priority: cardForm.priority,
            dueAt: cardForm.dueAt
                ? new Date(cardForm.dueAt).toISOString()
                : null,
            allDay: cardForm.allDay,
            labels: labelsFromText(cardForm.labels),
            assigneeIds: cardForm.assigneeIds,
        };

        try {
            if (editingCard) {
                const data = await apiRequest(
                    `/api/task-boards/${activeBoard.id}/cards/${editingCard.id}`,
                    {
                        method: "PATCH",
                        body: JSON.stringify(payload),
                    },
                );

                setCards((current) =>
                    current.map((card) =>
                        card.id === editingCard.id ? data.card : card,
                    ),
                );
                setNotice("Card updated.");
            } else {
                payload.position = nextPosition(cards, payload.columnId);
                const data = await apiRequest(
                    `/api/task-boards/${activeBoard.id}/cards`,
                    {
                        method: "POST",
                        body: JSON.stringify(payload),
                    },
                );
                setCards((current) => [...current, data.card]);
                setNotice("Card created.");
            }

            setCardModalOpen(false);
            setEditingCard(null);
            setCardForm(EMPTY_CARD_FORM);
        } catch (error) {
            setPageError(error.message);
        } finally {
            setSaving(false);
        }
    }

    async function handleDeleteCard(card) {
        if (!activeBoard || !window.confirm(`Delete "${card.title}"?`)) return;

        try {
            await apiRequest(
                `/api/task-boards/${activeBoard.id}/cards/${card.id}`,
                { method: "DELETE" },
            );
            setCards((current) => current.filter((item) => item.id !== card.id));
            setCardModalOpen(false);
            setNotice("Card deleted.");
        } catch (error) {
            setPageError(error.message);
        }
    }

    async function moveCard(cardId, columnId) {
        if (!activeBoard || !canEdit) return;

        const card = cards.find((item) => item.id === cardId);
        if (!card || card.columnId === columnId) return;

        const previousCards = cards;
        const position = nextPosition(cards, columnId);

        setCards((current) =>
            current.map((item) =>
                item.id === cardId ? { ...item, columnId, position } : item,
            ),
        );

        try {
            const data = await apiRequest(
                `/api/task-boards/${activeBoard.id}/cards/${cardId}`,
                {
                    method: "PATCH",
                    body: JSON.stringify({ columnId, position }),
                },
            );
            setCards((current) =>
                current.map((item) => (item.id === cardId ? data.card : item)),
            );
        } catch (error) {
            setCards(previousCards);
            setPageError(error.message);
        }
    }

    async function importCalendarTask(taskId, columnId) {
        if (!activeBoard || !canEdit) return;

        try {
            const data = await apiRequest(
                `/api/task-boards/${activeBoard.id}/import-calendar`,
                {
                    method: "POST",
                    body: JSON.stringify({
                        calendarItemIds: [taskId],
                        columnId,
                    }),
                },
            );

            if (data.cards?.length) {
                setCards((current) => [...current, ...data.cards]);
                setNotice("Calendar task added to the board.");
            } else {
                const reason = data.skipped?.[0]?.reason;
                setNotice(
                    reason === "already_imported"
                        ? "That calendar task is already on this board."
                        : "The task could not be imported.",
                );
            }
        } catch (error) {
            setPageError(error.message);
        }
    }

    async function createCardFromPreset(presetId, columnId) {
        const preset = presets.find((item) => item.id === presetId);
        if (!preset || !activeBoard) return;

        try {
            const data = await apiRequest(
                `/api/task-boards/${activeBoard.id}/cards`,
                {
                    method: "POST",
                    body: JSON.stringify({
                        title: preset.title,
                        description: preset.description || "",
                        columnId,
                        priority: preset.priority || "none",
                        labels: preset.labels || [],
                        position: nextPosition(cards, columnId),
                    }),
                },
            );
            setCards((current) => [...current, data.card]);
            setNotice("Preset card added.");
        } catch (error) {
            setPageError(error.message);
        }
    }

    function handleDragStart(event, item) {
        setDraggedItem(item);
        event.dataTransfer.effectAllowed =
            item.kind === "card" ? "move" : "copy";
        event.dataTransfer.setData("text/plain", JSON.stringify(item));
    }

    function handleColumnDrop(event, columnId) {
        event.preventDefault();

        let item = draggedItem;
        if (!item) {
            try {
                item = JSON.parse(event.dataTransfer.getData("text/plain"));
            } catch {
                return;
            }
        }

        if (item.kind === "card") {
            moveCard(item.id, columnId);
        } else if (item.kind === "calendar") {
            importCalendarTask(item.id, columnId);
        } else if (item.kind === "preset") {
            createCardFromPreset(item.id, columnId);
        }

        setDraggedItem(null);
    }

    async function handleAddColumn(event) {
        event.preventDefault();
        if (!activeBoard || !isOwner) return;

        const name = columnName.trim();
        if (!name) return;

        const baseId = name
            .toLowerCase()
            .replace(/[^a-z0-9_-]+/g, "_")
            .replace(/^_+|_+$/g, "") || `column_${Date.now()}`;

        let id = baseId;
        let counter = 2;
        const existingIds = new Set(activeBoard.columns.map((column) => column.id));
        while (existingIds.has(id)) {
            id = `${baseId}_${counter}`;
            counter += 1;
        }

        const columns = [
            ...activeBoard.columns,
            {
                id,
                name,
                position: activeBoard.columns.length,
            },
        ];

        setSaving(true);
        try {
            const data = await apiRequest(
                `/api/task-boards/${activeBoard.id}`,
                {
                    method: "PATCH",
                    body: JSON.stringify({ columns }),
                },
            );
            setActiveBoard((current) => ({
                ...data.board,
                currentUserRole: current.currentUserRole,
            }));
            setBoards((current) =>
                current.map((board) =>
                    board.id === data.board.id
                        ? { ...board, ...data.board }
                        : board,
                ),
            );
            setColumnModalOpen(false);
            setColumnName("");
            setNotice("Column added.");
        } catch (error) {
            setPageError(error.message);
        } finally {
            setSaving(false);
        }
    }


    async function handleDeleteColumn(column) {
        if (!activeBoard || !isOwner) return;

        const columnCards = cards.filter((card) => card.columnId === column.id);

        if (columnCards.length > 0) {
            setPageError(
                `Move or delete the ${columnCards.length} card${columnCards.length === 1 ? "" : "s"} in "${column.name}" before deleting this column.`,
            );
            return;
        }

        if (activeBoard.columns.length <= 1) {
            setPageError("A board must contain at least one column.");
            return;
        }

        if (!window.confirm(`Delete the "${column.name}" column?`)) return;

        setSaving(true);
        setPageError("");

        try {
            const data = await apiRequest(
                `/api/task-boards/${activeBoard.id}/columns/${column.id}`,
                { method: "DELETE" },
            );

            const nextColumns = Array.isArray(data.columns)
                ? data.columns
                : activeBoard.columns.filter((item) => item.id !== column.id);

            setActiveBoard((current) => ({
                ...current,
                columns: nextColumns,
            }));

            setBoards((current) =>
                current.map((board) =>
                    board.id === activeBoard.id
                        ? { ...board, columns: nextColumns }
                        : board,
                ),
            );

            setNotice("Column deleted.");
        } catch (error) {
            setPageError(error.message);
        } finally {
            setSaving(false);
        }
    }

    async function handleDeleteBoard() {
        if (
            !activeBoard ||
            !isOwner ||
            !window.confirm(`Delete "${activeBoard.name}" and all its cards?`)
        ) {
            return;
        }

        try {
            await apiRequest(`/api/task-boards/${activeBoard.id}`, {
                method: "DELETE",
            });
            const remaining = boards.filter((board) => board.id !== activeBoard.id);
            setBoards(remaining);
            setActiveBoardId(remaining[0]?.id || "");
            setNotice("Board deleted.");
        } catch (error) {
            setPageError(error.message);
        }
    }

    function handleSavePreset(event) {
        event.preventDefault();
        const title = presetForm.title.trim();
        if (!title) return;

        setPresets((current) => [
            ...current,
            {
                id: crypto.randomUUID?.() || `preset_${Date.now()}`,
                title,
                description: presetForm.description.trim(),
                priority: presetForm.priority,
                labels: labelsFromText(presetForm.labels),
            },
        ]);
        setPresetForm(EMPTY_PRESET_FORM);
        setPresetModalOpen(false);
        setNotice("Preset saved in this browser.");
    }

    const columns = activeBoard?.columns || [];

    return (
        <main
            className={`task-board-page ${
                isSidebarCollapsed ? "task-board-page--sidebar-collapsed" : ""
            }`}
        >
            <aside
                className={`task-board-sidebar ${
                    isSidebarCollapsed ? "task-board-sidebar--collapsed" : ""
                }`}
            >
                <div className="task-board-sidebar__topbar">
                    <button
                        type="button"
                        className="task-board-sidebar__brand"
                        onClick={() => navigate("/")}
                    >
                        <img src={kotaroImage} alt="" />
                        <span className="task-board-sidebar__label">The Nowl</span>
                    </button>
                    <button
                        type="button"
                        className="task-board-sidebar__toggle"
                        onClick={() => setIsSidebarCollapsed((value) => !value)}
                        aria-label={
                            isSidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"
                        }
                    >
                        {isSidebarCollapsed ? "›" : "‹"}
                    </button>
                </div>

                <nav className="task-board-sidebar__nav">
                    <button type="button" onClick={() => navigate("/")}>
                        <span aria-hidden="true">⌂</span>
                        <span className="task-board-sidebar__label">Homepage</span>
                    </button>
                    <button type="button" onClick={() => navigate("/calendar")}>
                        <span aria-hidden="true">▣</span>
                        <span className="task-board-sidebar__label">Calendar</span>
                    </button>
                    <button type="button" className="is-active">
                        <span aria-hidden="true">☷</span>
                        <span className="task-board-sidebar__label">Task boards</span>
                    </button>
                </nav>

                <div className="task-board-sidebar__section">
                    <div className="task-board-sidebar__section-title">
                        <span className="task-board-sidebar__label">Your boards</span>
                        <button
                            type="button"
                            onClick={() => setBoardModalOpen(true)}
                            aria-label="Create board"
                        >
                            +
                        </button>
                    </div>

                    <div className="task-board-sidebar__boards">
                        {boards.map((board) => (
                            <button
                                type="button"
                                key={board.id}
                                className={
                                    board.id === activeBoardId ? "is-active" : ""
                                }
                                onClick={() => setActiveBoardId(board.id)}
                                title={board.name}
                            >
                                <span aria-hidden="true">▦</span>
                                <span className="task-board-sidebar__label">
                                    {board.name}
                                </span>
                            </button>
                        ))}
                    </div>
                </div>
            </aside>

            <div className="task-board-shell">
                <header className="task-board-topbar">
                    <div>
                        <p className="task-board-eyebrow">Shared workspace</p>
                        <h1>{activeBoard?.name || "Task boards"}</h1>
                        <p>
                            {activeBoard?.description ||
                                "Plan work, import calendar tasks, and move cards as progress changes."}
                        </p>
                    </div>

                    <div className="task-board-topbar__actions">
                        {activeBoard && isOwner && (
                            <button
                                type="button"
                                className="task-board-button task-board-button--danger-soft"
                                onClick={handleDeleteBoard}
                            >
                                Delete board
                            </button>
                        )}
                        <button
                            type="button"
                            className="task-board-button task-board-button--ghost"
                            onClick={() => setPresetModalOpen(true)}
                        >
                            New preset
                        </button>
                        <button
                            type="button"
                            className="task-board-button task-board-button--primary"
                            onClick={() => openCreateCard()}
                            disabled={!activeBoard || !canEdit}
                        >
                            + New card
                        </button>
                    </div>
                </header>

                {notice && <div className="task-board-notice">{notice}</div>}
                {pageError && (
                    <div className="task-board-error">
                        <span>{pageError}</span>
                        <button type="button" onClick={() => setPageError("")}>
                            ×
                        </button>
                    </div>
                )}

                {pageStatus === "loading" && (
                    <div className="task-board-state">Loading your task boards…</div>
                )}

                {pageStatus !== "loading" && boards.length === 0 && (
                    <section className="task-board-empty">
                        <div className="task-board-empty__icon">☷</div>
                        <p className="task-board-eyebrow">Start organising</p>
                        <h2>Create your first board</h2>
                        <p>
                            Each board starts with Backlog, In Progress, Completed,
                            and Reminders columns.
                        </p>
                        <button
                            type="button"
                            className="task-board-button task-board-button--primary"
                            onClick={() => setBoardModalOpen(true)}
                        >
                            Create a board
                        </button>
                    </section>
                )}

                {activeBoard && (
                    <>
                        <section className="task-board-toolbar">
                            <div className="task-board-members">
                                <span>Members</span>
                                <div className="task-board-members__stack">
                                    {boardMembers.slice(0, 5).map((member) => (
                                        <span
                                            className="task-board-avatar"
                                            key={member.uid}
                                            title={`${member.displayName} · ${member.role}`}
                                        >
                                            {member.profilePicLink ? (
                                                <img
                                                    src={member.profilePicLink}
                                                    alt={member.displayName}
                                                />
                                            ) : (
                                                avatarText(member.displayName)
                                            )}
                                        </span>
                                    ))}
                                    {boardMembers.length > 5 && (
                                        <span className="task-board-avatar">
                                            +{boardMembers.length - 5}
                                        </span>
                                    )}
                                </div>
                            </div>

                            <div className="task-board-toolbar__right">
                                <span className="task-board-role">
                                    {activeBoard.currentUserRole}
                                </span>
                                {isOwner && (
                                    <button
                                        type="button"
                                        className="task-board-button task-board-button--ghost"
                                        onClick={() => setColumnModalOpen(true)}
                                    >
                                        + Add column
                                    </button>
                                )}
                            </div>
                        </section>

                        <div className="task-board-workspace">
                            <aside className="task-card-library">
                                <div className="task-card-library__header">
                                    <div>
                                        <p className="task-board-eyebrow">Card list</p>
                                        <h2>Add to board</h2>
                                    </div>
                                    <button
                                        type="button"
                                        onClick={() =>
                                            libraryTab === "calendar"
                                                ? loadCalendarTasks()
                                                : setPresetModalOpen(true)
                                        }
                                    >
                                        {libraryTab === "calendar" ? "↻" : "+"}
                                    </button>
                                </div>

                                <div className="task-card-library__tabs">
                                    <button
                                        type="button"
                                        className={
                                            libraryTab === "calendar" ? "is-active" : ""
                                        }
                                        onClick={() => setLibraryTab("calendar")}
                                    >
                                        Calendar
                                    </button>
                                    <button
                                        type="button"
                                        className={
                                            libraryTab === "presets" ? "is-active" : ""
                                        }
                                        onClick={() => setLibraryTab("presets")}
                                    >
                                        Presets
                                    </button>
                                </div>

                                <input
                                    className="task-card-library__search"
                                    type="search"
                                    placeholder="Search cards…"
                                    value={librarySearch}
                                    onChange={(event) =>
                                        setLibrarySearch(event.target.value)
                                    }
                                />

                                <p className="task-card-library__hint">
                                    Drag a card into any board column.
                                </p>

                                <div className="task-card-library__list">
                                    {libraryTab === "calendar" &&
                                        filteredCalendarTasks.map((task) => (
                                            <article
                                                className="library-card library-card--calendar"
                                                key={task.id}
                                                draggable={canEdit}
                                                onDragStart={(event) =>
                                                    handleDragStart(event, {
                                                        kind: "calendar",
                                                        id: task.id,
                                                    })
                                                }
                                            >
                                                <span className="library-card__type">
                                                    Calendar task
                                                </span>
                                                <strong>{task.title}</strong>
                                                {task.dueAt && (
                                                    <small>
                                                        Due {formatDate(task.dueAt, task.allDay)}
                                                    </small>
                                                )}
                                            </article>
                                        ))}

                                    {libraryTab === "presets" &&
                                        filteredPresets.map((preset) => (
                                            <article
                                                className="library-card library-card--preset"
                                                key={preset.id}
                                                draggable={canEdit}
                                                onDragStart={(event) =>
                                                    handleDragStart(event, {
                                                        kind: "preset",
                                                        id: preset.id,
                                                    })
                                                }
                                            >
                                                <div className="library-card__topline">
                                                    <span className="library-card__type">
                                                        Preset
                                                    </span>
                                                    <button
                                                        type="button"
                                                        onClick={() =>
                                                            setPresets((current) =>
                                                                current.filter(
                                                                    (item) =>
                                                                        item.id !== preset.id,
                                                                ),
                                                            )
                                                        }
                                                        aria-label={`Delete ${preset.title}`}
                                                    >
                                                        ×
                                                    </button>
                                                </div>
                                                <strong>{preset.title}</strong>
                                                {preset.description && (
                                                    <small>{preset.description}</small>
                                                )}
                                            </article>
                                        ))}

                                    {((libraryTab === "calendar" &&
                                        filteredCalendarTasks.length === 0) ||
                                        (libraryTab === "presets" &&
                                            filteredPresets.length === 0)) && (
                                        <div className="task-card-library__empty">
                                            {libraryTab === "calendar"
                                                ? "No available calendar tasks."
                                                : "No presets yet."}
                                        </div>
                                    )}
                                </div>
                            </aside>

                            <section className="task-board-canvas">
                                {columns.map((column) => {
                                    const columnCards = cards
                                        .filter((card) => card.columnId === column.id)
                                        .sort(
                                            (a, b) =>
                                                Number(a.position || 0) -
                                                Number(b.position || 0),
                                        );

                                    return (
                                        <article
                                            className="task-board-column"
                                            key={column.id}
                                            onDragOver={(event) => {
                                                if (canEdit) {
                                                    event.preventDefault();
                                                    event.dataTransfer.dropEffect =
                                                        draggedItem?.kind === "card"
                                                            ? "move"
                                                            : "copy";
                                                }
                                            }}
                                            onDrop={(event) =>
                                                handleColumnDrop(event, column.id)
                                            }
                                        >
                                            <header className="task-board-column__header">
                                                <div>
                                                    <span
                                                        className={`task-board-column__dot task-board-column__dot--${column.id}`}
                                                    />
                                                    <h2>{column.name}</h2>
                                                    <span>{columnCards.length}</span>
                                                </div>
                                                <div className="task-board-column__actions">
                                                    {canEdit && (
                                                        <button
                                                            type="button"
                                                            onClick={() =>
                                                                openCreateCard(column.id)
                                                            }
                                                            aria-label={`Add card to ${column.name}`}
                                                            title="Add card"
                                                        >
                                                            +
                                                        </button>
                                                    )}
                                                    {isOwner && (
                                                        <button
                                                            type="button"
                                                            className="task-board-column__delete"
                                                            onClick={() => handleDeleteColumn(column)}
                                                            aria-label={`Delete ${column.name} column`}
                                                            title={
                                                                columnCards.length > 0
                                                                    ? "Move or delete all cards first"
                                                                    : "Delete column"
                                                            }
                                                            disabled={saving}
                                                        >
                                                            ×
                                                        </button>
                                                    )}
                                                </div>
                                            </header>

                                            <div className="task-board-column__cards">
                                                {columnCards.map((card) => (
                                                    <article
                                                        className={`task-board-card task-board-card--${card.priority}`}
                                                        key={card.id}
                                                        draggable={canEdit}
                                                        onDragStart={(event) =>
                                                            handleDragStart(event, {
                                                                kind: "card",
                                                                id: card.id,
                                                            })
                                                        }
                                                        onDragEnd={() =>
                                                            setDraggedItem(null)
                                                        }
                                                        onClick={() =>
                                                            canEdit && openEditCard(card)
                                                        }
                                                    >
                                                        {(card.labels || []).length > 0 && (
                                                            <div className="task-board-card__labels">
                                                                {card.labels
                                                                    .slice(0, 3)
                                                                    .map((label) => (
                                                                        <span key={label}>
                                                                            {label}
                                                                        </span>
                                                                    ))}
                                                            </div>
                                                        )}

                                                        <h3>{card.title}</h3>
                                                        {card.description && (
                                                            <p>{card.description}</p>
                                                        )}

                                                        <footer className="task-board-card__footer">
                                                            <div>
                                                                {card.dueAt && (
                                                                    <span
                                                                        className={
                                                                            new Date(card.dueAt) <
                                                                            new Date()
                                                                                ? "is-overdue"
                                                                                : ""
                                                                        }
                                                                    >
                                                                        ◷{" "}
                                                                        {formatDate(
                                                                            card.dueAt,
                                                                            card.allDay,
                                                                        )}
                                                                    </span>
                                                                )}
                                                                {card.calendarItemId && (
                                                                    <span title="Imported from calendar">
                                                                        ▣
                                                                    </span>
                                                                )}
                                                            </div>

                                                            {(card.assignees || []).length >
                                                                0 && (
                                                                <div className="task-board-card__assignees">
                                                                    {card.assignees
                                                                        .slice(0, 3)
                                                                        .map((assignee) => (
                                                                            <span
                                                                                key={assignee.uid}
                                                                                title={
                                                                                    assignee.displayName
                                                                                }
                                                                            >
                                                                                {assignee.profilePicLink ? (
                                                                                    <img
                                                                                        src={
                                                                                            assignee.profilePicLink
                                                                                        }
                                                                                        alt=""
                                                                                    />
                                                                                ) : (
                                                                                    avatarText(
                                                                                        assignee.displayName,
                                                                                    )
                                                                                )}
                                                                            </span>
                                                                        ))}
                                                                </div>
                                                            )}
                                                        </footer>
                                                    </article>
                                                ))}

                                                {columnCards.length === 0 && (
                                                    <div className="task-board-column__dropzone">
                                                        Drop cards here
                                                    </div>
                                                )}
                                            </div>
                                        </article>
                                    );
                                })}

                                {isOwner && (
                                    <button
                                        type="button"
                                        className="task-board-add-column"
                                        onClick={() => setColumnModalOpen(true)}
                                    >
                                        <span>+</span>
                                        Add another column
                                    </button>
                                )}
                            </section>
                        </div>
                    </>
                )}
            </div>

            {boardModalOpen && (
                <div className="task-board-modal-backdrop">
                    <form className="task-board-modal" onSubmit={handleCreateBoard}>
                        <header>
                            <div>
                                <p className="task-board-eyebrow">New workspace</p>
                                <h2>Create a board</h2>
                            </div>
                            <button
                                type="button"
                                onClick={() => setBoardModalOpen(false)}
                            >
                                ×
                            </button>
                        </header>

                        <label>
                            Board name
                            <input
                                required
                                maxLength={120}
                                value={boardForm.name}
                                onChange={(event) =>
                                    setBoardForm((current) => ({
                                        ...current,
                                        name: event.target.value,
                                    }))
                                }
                                placeholder="Family plans"
                            />
                        </label>

                        <label>
                            Description
                            <textarea
                                rows={4}
                                maxLength={2000}
                                value={boardForm.description}
                                onChange={(event) =>
                                    setBoardForm((current) => ({
                                        ...current,
                                        description: event.target.value,
                                    }))
                                }
                                placeholder="What will this board be used for?"
                            />
                        </label>

                        <footer>
                            <button
                                type="button"
                                className="task-board-button task-board-button--ghost"
                                onClick={() => setBoardModalOpen(false)}
                            >
                                Cancel
                            </button>
                            <button
                                className="task-board-button task-board-button--primary"
                                disabled={saving}
                            >
                                {saving ? "Creating…" : "Create board"}
                            </button>
                        </footer>
                    </form>
                </div>
            )}

            {cardModalOpen && activeBoard && (
                <div className="task-board-modal-backdrop">
                    <form
                        className="task-board-modal task-board-modal--wide"
                        onSubmit={handleSaveCard}
                    >
                        <header>
                            <div>
                                <p className="task-board-eyebrow">
                                    {editingCard ? "Card details" : "New work item"}
                                </p>
                                <h2>{editingCard ? "Edit card" : "Create a card"}</h2>
                            </div>
                            <button
                                type="button"
                                onClick={() => setCardModalOpen(false)}
                            >
                                ×
                            </button>
                        </header>

                        <div className="task-board-form-grid">
                            <label className="task-board-form-grid__full">
                                Title
                                <input
                                    required
                                    maxLength={200}
                                    value={cardForm.title}
                                    onChange={(event) =>
                                        setCardForm((current) => ({
                                            ...current,
                                            title: event.target.value,
                                        }))
                                    }
                                />
                            </label>

                            <label className="task-board-form-grid__full">
                                Description
                                <textarea
                                    rows={5}
                                    maxLength={5000}
                                    value={cardForm.description}
                                    onChange={(event) =>
                                        setCardForm((current) => ({
                                            ...current,
                                            description: event.target.value,
                                        }))
                                    }
                                />
                            </label>

                            <label>
                                Column
                                <select
                                    value={cardForm.columnId}
                                    onChange={(event) =>
                                        setCardForm((current) => ({
                                            ...current,
                                            columnId: event.target.value,
                                        }))
                                    }
                                >
                                    {columns.map((column) => (
                                        <option value={column.id} key={column.id}>
                                            {column.name}
                                        </option>
                                    ))}
                                </select>
                            </label>

                            <label>
                                Priority
                                <select
                                    value={cardForm.priority}
                                    onChange={(event) =>
                                        setCardForm((current) => ({
                                            ...current,
                                            priority: event.target.value,
                                        }))
                                    }
                                >
                                    <option value="none">No priority</option>
                                    <option value="low">Low</option>
                                    <option value="medium">Medium</option>
                                    <option value="high">High</option>
                                    <option value="urgent">Urgent</option>
                                </select>
                            </label>

                            <label>
                                Due date and time
                                <input
                                    type="datetime-local"
                                    value={cardForm.dueAt}
                                    onChange={(event) =>
                                        setCardForm((current) => ({
                                            ...current,
                                            dueAt: event.target.value,
                                        }))
                                    }
                                />
                            </label>

                            <label className="task-board-checkbox">
                                <input
                                    type="checkbox"
                                    checked={cardForm.allDay}
                                    onChange={(event) =>
                                        setCardForm((current) => ({
                                            ...current,
                                            allDay: event.target.checked,
                                        }))
                                    }
                                />
                                All-day deadline
                            </label>

                            <label className="task-board-form-grid__full">
                                Labels
                                <input
                                    value={cardForm.labels}
                                    onChange={(event) =>
                                        setCardForm((current) => ({
                                            ...current,
                                            labels: event.target.value,
                                        }))
                                    }
                                    placeholder="Home, Important, Shopping"
                                />
                                <small>Separate labels with commas.</small>
                            </label>

                            <fieldset className="task-board-form-grid__full">
                                <legend>Assignees</legend>
                                <div className="task-board-assignee-picker">
                                    {boardMembers.map((member) => {
                                        const checked =
                                            cardForm.assigneeIds.includes(member.uid);
                                        return (
                                            <label key={member.uid}>
                                                <input
                                                    type="checkbox"
                                                    checked={checked}
                                                    onChange={() =>
                                                        setCardForm((current) => ({
                                                            ...current,
                                                            assigneeIds: checked
                                                                ? current.assigneeIds.filter(
                                                                      (uid) =>
                                                                          uid !== member.uid,
                                                                  )
                                                                : [
                                                                      ...current.assigneeIds,
                                                                      member.uid,
                                                                  ],
                                                        }))
                                                    }
                                                />
                                                <span className="task-board-avatar">
                                                    {member.profilePicLink ? (
                                                        <img
                                                            src={member.profilePicLink}
                                                            alt=""
                                                        />
                                                    ) : (
                                                        avatarText(member.displayName)
                                                    )}
                                                </span>
                                                {member.displayName}
                                            </label>
                                        );
                                    })}
                                </div>
                            </fieldset>
                        </div>

                        <footer>
                            {editingCard && (
                                <button
                                    type="button"
                                    className="task-board-button task-board-button--danger-soft"
                                    onClick={() => handleDeleteCard(editingCard)}
                                >
                                    Delete
                                </button>
                            )}
                            <span className="task-board-modal__spacer" />
                            <button
                                type="button"
                                className="task-board-button task-board-button--ghost"
                                onClick={() => setCardModalOpen(false)}
                            >
                                Cancel
                            </button>
                            <button
                                className="task-board-button task-board-button--primary"
                                disabled={saving}
                            >
                                {saving ? "Saving…" : "Save card"}
                            </button>
                        </footer>
                    </form>
                </div>
            )}

            {presetModalOpen && (
                <div className="task-board-modal-backdrop">
                    <form className="task-board-modal" onSubmit={handleSavePreset}>
                        <header>
                            <div>
                                <p className="task-board-eyebrow">Reusable card</p>
                                <h2>Create a preset</h2>
                            </div>
                            <button
                                type="button"
                                onClick={() => setPresetModalOpen(false)}
                            >
                                ×
                            </button>
                        </header>

                        <label>
                            Title
                            <input
                                required
                                value={presetForm.title}
                                onChange={(event) =>
                                    setPresetForm((current) => ({
                                        ...current,
                                        title: event.target.value,
                                    }))
                                }
                                placeholder="Take out the bins"
                            />
                        </label>

                        <label>
                            Description
                            <textarea
                                rows={4}
                                value={presetForm.description}
                                onChange={(event) =>
                                    setPresetForm((current) => ({
                                        ...current,
                                        description: event.target.value,
                                    }))
                                }
                            />
                        </label>

                        <label>
                            Priority
                            <select
                                value={presetForm.priority}
                                onChange={(event) =>
                                    setPresetForm((current) => ({
                                        ...current,
                                        priority: event.target.value,
                                    }))
                                }
                            >
                                <option value="none">No priority</option>
                                <option value="low">Low</option>
                                <option value="medium">Medium</option>
                                <option value="high">High</option>
                                <option value="urgent">Urgent</option>
                            </select>
                        </label>

                        <label>
                            Labels
                            <input
                                value={presetForm.labels}
                                onChange={(event) =>
                                    setPresetForm((current) => ({
                                        ...current,
                                        labels: event.target.value,
                                    }))
                                }
                                placeholder="Routine, Weekly"
                            />
                        </label>

                        <p className="task-board-modal__note">
                            Presets are stored in this browser. Your current backend
                            does not yet include shared preset routes.
                        </p>

                        <footer>
                            <button
                                type="button"
                                className="task-board-button task-board-button--ghost"
                                onClick={() => setPresetModalOpen(false)}
                            >
                                Cancel
                            </button>
                            <button className="task-board-button task-board-button--primary">
                                Save preset
                            </button>
                        </footer>
                    </form>
                </div>
            )}

            {columnModalOpen && (
                <div className="task-board-modal-backdrop">
                    <form className="task-board-modal" onSubmit={handleAddColumn}>
                        <header>
                            <div>
                                <p className="task-board-eyebrow">Board structure</p>
                                <h2>Add a column</h2>
                            </div>
                            <button
                                type="button"
                                onClick={() => setColumnModalOpen(false)}
                            >
                                ×
                            </button>
                        </header>

                        <label>
                            Column name
                            <input
                                required
                                maxLength={80}
                                value={columnName}
                                onChange={(event) => setColumnName(event.target.value)}
                                placeholder="Waiting for"
                            />
                        </label>

                        <footer>
                            <button
                                type="button"
                                className="task-board-button task-board-button--ghost"
                                onClick={() => setColumnModalOpen(false)}
                            >
                                Cancel
                            </button>
                            <button
                                className="task-board-button task-board-button--primary"
                                disabled={saving}
                            >
                                {saving ? "Adding…" : "Add column"}
                            </button>
                        </footer>
                    </form>
                </div>
            )}
        </main>
    );
}

export default TaskBoardPage;