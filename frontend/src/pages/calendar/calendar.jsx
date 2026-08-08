import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../../context/authContext.jsx";
import "./calendar.css";
import seckotaroImage from "../../assets/seckotaro.png";
import kotaroImage from "../../assets/kotaro.png";
const API_BASE_URL =
    import.meta.env.VITE_API_BASE_URL ||
    import.meta.env.VITE_API_BASE ||
    "";

const ITEM_TYPES = ["event", "task", "reminder"];
const VISIBILITIES = ["personal", "family", "all"];
const RECURRENCE_FREQUENCIES = ["none", "daily", "weekly", "monthly", "yearly"];

const EMPTY_FORM = {
    itemType: "event",
    title: "",
    description: "",
    startDate: "",
    startTime: "09:00",
    endDate: "",
    endTime: "10:00",
    dueDate: "",
    dueTime: "17:00",
    allDay: false,
    visibility: "personal",
    taggedUserIds: [],
    status: "pending",
    recurrenceFrequency: "none",
    recurrenceInterval: 1,
    recurrenceEndType: "never",
    recurrenceEndDate: "",
    recurrenceCount: 10,
};

function pad(value) {
    return String(value).padStart(2, "0");
}

function localDateKey(date) {
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

function todayDateKey() {
    return localDateKey(new Date());
}

function isPastDate(date) {
    const candidate = new Date(
        date.getFullYear(),
        date.getMonth(),
        date.getDate(),
    );
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    return candidate < today;
}

function isPastDateTime(date, time, allDay = false) {
    if (!date) return false;

    const todayKey = todayDateKey();
    if (date < todayKey) return true;
    if (allDay || date > todayKey) return false;

    const selected = new Date(
        `${date}T${time || "00:00"}:00+08:00`,
    );

    return selected.getTime() < Date.now();
}

function formStartDateTime(form) {
    if (form.itemType === "reminder") {
        return buildIso(form.dueDate, form.dueTime, form.allDay);
    }

    return buildIso(form.startDate, form.startTime, form.allDay);
}

function formEndDateTime(form) {
    if (form.itemType === "event") {
        return buildIso(form.endDate, form.endTime, form.allDay);
    }

    if (form.itemType === "task") {
        return buildIso(form.dueDate, form.dueTime, form.allDay);
    }

    return formStartDateTime(form);
}

function parseDateOnly(value) {
    if (!value) return null;
    const [year, month, day] = value.slice(0, 10).split("-").map(Number);
    if (!year || !month || !day) return null;
    return new Date(year, month - 1, day);
}

function startOfMonth(date) {
    return new Date(date.getFullYear(), date.getMonth(), 1);
}

function addMonths(date, amount) {
    return new Date(date.getFullYear(), date.getMonth() + amount, 1);
}

function addDays(date, amount) {
    const next = new Date(date);
    next.setDate(next.getDate() + amount);
    return next;
}

function recurrenceLabel(item) {
    const recurrence = item?.recurrence || {};
    const frequency = recurrence.frequency || "none";
    if (frequency === "none") return "";

    const interval = Number(recurrence.interval) || 1;
    const unit = frequency === "daily"
        ? "day"
        : frequency === "weekly"
          ? "week"
          : frequency === "monthly"
            ? "month"
            : "year";

    return interval === 1
        ? `Repeats ${frequency}`
        : `Repeats every ${interval} ${unit}s`;
}

function formatMonth(date) {
    return new Intl.DateTimeFormat("en-SG", {
        month: "long",
        year: "numeric",
    }).format(date);
}

function formatFriendlyDate(value) {
    if (!value) return "No date";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;

    return new Intl.DateTimeFormat("en-SG", {
        weekday: "short",
        day: "numeric",
        month: "short",
        hour: "numeric",
        minute: "2-digit",
    }).format(date);
}

function buildIso(date, time, allDay = false) {
    if (!date) return null;
    if (allDay) return `${date}T00:00:00+08:00`;
    return `${date}T${time || "09:00"}:00+08:00`;
}

function itemDateKey(item) {
    const raw = item.startAt || item.dueAt || item.endAt;
    if (!raw) return "";
    return raw.slice(0, 10);
}

function itemRange(item) {
    let startRaw = item.startAt || item.dueAt;
    let endRaw = startRaw;

    if (item.itemType === "event") {
        endRaw = item.endAt || startRaw;
    } else if (item.itemType === "task") {
        endRaw = item.dueAt || startRaw;
    }

    const start = parseDateOnly(startRaw);
    const end = parseDateOnly(endRaw);

    if (!start || !end) return null;

    return end < start
        ? { start, end: start }
        : { start, end };
}

function daysBetween(first, second) {
    const millisecondsPerDay = 24 * 60 * 60 * 1000;
    const firstUtc = Date.UTC(
        first.getFullYear(),
        first.getMonth(),
        first.getDate(),
    );
    const secondUtc = Date.UTC(
        second.getFullYear(),
        second.getMonth(),
        second.getDate(),
    );

    return Math.round((secondUtc - firstUtc) / millisecondsPerDay);
}

function buildWeekSegments(weekCells, items) {
    if (!weekCells.length) return [];

    const weekStart = weekCells[0].date;
    const weekEnd = weekCells[weekCells.length - 1].date;

    const candidates = items
        .map((item) => {
            const range = itemRange(item);
            if (!range || range.end < weekStart || range.start > weekEnd) {
                return null;
            }

            const visibleStart = range.start < weekStart
                ? weekStart
                : range.start;
            const visibleEnd = range.end > weekEnd
                ? weekEnd
                : range.end;

            return {
                item,
                range,
                startColumn: daysBetween(weekStart, visibleStart) + 1,
                endColumn: daysBetween(weekStart, visibleEnd) + 2,
                continuesBefore: range.start < weekStart,
                continuesAfter: range.end > weekEnd,
            };
        })
        .filter(Boolean)
        .sort((first, second) => {
            if (first.startColumn !== second.startColumn) {
                return first.startColumn - second.startColumn;
            }

            return second.endColumn - first.endColumn;
        });

    const laneEndColumns = [];

    return candidates.map((segment) => {
        let lane = laneEndColumns.findIndex(
            (endColumn) => endColumn <= segment.startColumn,
        );

        if (lane === -1) {
            lane = laneEndColumns.length;
        }

        laneEndColumns[lane] = segment.endColumn;

        return {
            ...segment,
            lane,
        };
    });
}

function itemTimeLabel(item) {
    if (item.allDay) return "All day";

    const raw = item.startAt || item.dueAt;
    if (!raw) return "";

    const date = new Date(raw);
    if (Number.isNaN(date.getTime())) return "";

    return new Intl.DateTimeFormat("en-SG", {
        hour: "numeric",
        minute: "2-digit",
    }).format(date);
}

function readJsonResponse(response) {
    const contentType = response.headers.get("content-type") || "";

    if (!contentType.includes("application/json")) {
        throw new Error(
            `The server returned an unexpected response (${response.status}).`,
        );
    }

    return response.json();
}

function getKotaroMessage(upcomingItems, status) {
    if (status === "loading") {
        return {
            title: "Let me check your schedule…",
            detail: "I’m gathering your upcoming plans.",
        };
    }

    if (status === "error") {
        return {
            title: "I couldn’t read the calendar.",
            detail: "Try refreshing the page in a moment.",
        };
    }

    if (upcomingItems.length === 0) {
        return {
            title: "Your schedule is clear!",
            detail: "Really so free ah",
        };
    }

    const nextItem = upcomingItems[0];
    const nextDate = nextItem.startAt || nextItem.dueAt;

    if (upcomingItems.length === 1) {
        return {
            title: "You have one thing coming up.",
            detail: `${nextItem.title} · ${formatFriendlyDate(nextDate)}`,
        };
    }

    if (upcomingItems.length >= 5) {
        return {
            title: `Busy days ahead — ${upcomingItems.length} items coming up.`,
            detail: `Next: ${nextItem.title} · ${formatFriendlyDate(nextDate)}`,
        };
    }

    return {
        title: `You have ${upcomingItems.length} items coming up.`,
        detail: `Next: ${nextItem.title} · ${formatFriendlyDate(nextDate)}`,
    };
}

function CalendarPage() {
    const navigate = useNavigate();
    const { currentUser } = useAuth();

    const [visibleMonth, setVisibleMonth] = useState(startOfMonth(new Date()));
    const [items, setItems] = useState([]);
    const [upcomingItems, setUpcomingItems] = useState([]);
    const [status, setStatus] = useState("loading");
    const [error, setError] = useState("");
    const [isMainUser, setIsMainUser] = useState(false);

    const [modalOpen, setModalOpen] = useState(false);
    const [editingItem, setEditingItem] = useState(null);
    const [form, setForm] = useState(EMPTY_FORM);
    const [saving, setSaving] = useState(false);
    const [deleting, setDeleting] = useState(false);
    const [formError, setFormError] = useState("");

    const [userQuery, setUserQuery] = useState("");
    const [userResults, setUserResults] = useState([]);
    const [selectedUsers, setSelectedUsers] = useState([]);
    const [searchingUsers, setSearchingUsers] = useState(false);

    const [telegramStatus, setTelegramStatus] = useState("loading");
    const [telegramSubscription, setTelegramSubscription] = useState({
        connected: false,
        active: false,
    });
    const [telegramMessage, setTelegramMessage] = useState("");
    const [telegramBusy, setTelegramBusy] = useState(false);

    const getAuthHeaders = useCallback(
        async (includeJson = false) => {
            if (!currentUser) throw new Error("You must be signed in.");

            const token = await currentUser.getIdToken();

            return {
                Authorization: `Bearer ${token}`,
                ...(includeJson
                    ? { "Content-Type": "application/json" }
                    : {}),
            };
        },
        [currentUser],
    );

    const loadTelegramSubscription = useCallback(async () => {
        if (!currentUser) return;

        setTelegramStatus("loading");

        try {
            const headers = await getAuthHeaders();
            const response = await fetch(
                `${API_BASE_URL}/api/telegram/subscription`,
                {
                    method: "GET",
                    headers,
                    cache: "no-store",
                },
            );

            const data = await readJsonResponse(response);

            if (!response.ok) {
                throw new Error(
                    data.error || "Could not load Telegram connection status.",
                );
            }

            setTelegramSubscription({
                connected: Boolean(data.connected),
                active: Boolean(data.active),
                telegramUsername: data.telegramUsername || "",
                telegramFirstName: data.telegramFirstName || "",
                linkedAt: data.linkedAt || null,
                reminderHour: data.reminderHour ?? 8,
                timezone: data.timezone || "Asia/Singapore",
            });
            setTelegramStatus("success");
        } catch (telegramError) {
            console.error("Unable to load Telegram subscription:", telegramError);
            setTelegramStatus("error");
        }
    }, [currentUser, getAuthHeaders]);

async function handleConnectTelegram() {
    setTelegramBusy(true);
    setTelegramMessage("");

    try {
        const headers = await getAuthHeaders(true);

        const response = await fetch(
            `${API_BASE_URL}/api/telegram/link`,
            {
                method: "POST",
                headers,
                body: JSON.stringify({}),
            }
        );

        const data = await readJsonResponse(response);

        if (!response.ok) {
            throw new Error(
                data.error || "Could not create a Telegram connection link."
            );
        }

        if (!data.telegramUrl) {
            throw new Error("The server did not return a Telegram link.");
        }

        const opened = window.open(
            data.telegramUrl,
            "_blank",
            "noopener,noreferrer"
        );

        if (!opened) {
            setTelegramMessage(
                "Your browser blocked the new tab. Please allow pop-ups for this site and try again."
            );
            return;
        }

        setTelegramMessage(
            "Telegram has been opened in a new tab. Press Start in the bot, then return here and click Refresh status."
        );
    } catch (telegramError) {
        console.error("Unable to connect Telegram:", telegramError);
        setTelegramMessage(
            telegramError.message || "Could not connect Telegram."
        );
    } finally {
        setTelegramBusy(false);
    }
}
    async function handleRefreshTelegram() {
        setTelegramMessage("");
        await loadTelegramSubscription();
    }

    async function handleTelegramTest() {
        setTelegramBusy(true);
        setTelegramMessage("");

        try {
            const headers = await getAuthHeaders(true);
            const response = await fetch(
                `${API_BASE_URL}/api/telegram/test`,
                {
                    method: "POST",
                    headers,
                    body: JSON.stringify({}),
                },
            );

            const data = await readJsonResponse(response);

            if (!response.ok) {
                throw new Error(data.error || "Could not send a test reminder.");
            }

            setTelegramMessage("Test reminder sent. Check Telegram.");
        } catch (telegramError) {
            console.error("Unable to send Telegram test:", telegramError);
            setTelegramMessage(
                telegramError.message || "Could not send a test reminder.",
            );
        } finally {
            setTelegramBusy(false);
        }
    }

    async function handleDisconnectTelegram() {
        const confirmed = window.confirm(
            "Disable Telegram reminders for this account?",
        );

        if (!confirmed) return;

        setTelegramBusy(true);
        setTelegramMessage("");

        try {
            const headers = await getAuthHeaders();
            const response = await fetch(
                `${API_BASE_URL}/api/telegram/subscription`,
                {
                    method: "DELETE",
                    headers,
                },
            );

            const data = await readJsonResponse(response);

            if (!response.ok) {
                throw new Error(
                    data.error || "Could not disable Telegram reminders.",
                );
            }

            setTelegramMessage("Telegram reminders have been disabled.");
            await loadTelegramSubscription();
        } catch (telegramError) {
            console.error("Unable to disconnect Telegram:", telegramError);
            setTelegramMessage(
                telegramError.message || "Could not disable Telegram reminders.",
            );
        } finally {
            setTelegramBusy(false);
        }
    }

    const loadCalendar = useCallback(async () => {
        if (!currentUser) return;

        setStatus("loading");
        setError("");

        try {
            const headers = await getAuthHeaders();

            const rangeStart = addDays(startOfMonth(visibleMonth), -7);
            const rangeEnd = addDays(addMonths(startOfMonth(visibleMonth), 1), 7);
            const rangeQuery = new URLSearchParams({
                start: buildIso(localDateKey(rangeStart), "00:00", true),
                end: buildIso(localDateKey(rangeEnd), "00:00", true),
            });

            const [itemsResponse, upcomingResponse] = await Promise.all([
                fetch(`${API_BASE_URL}/api/calendar/items?${rangeQuery.toString()}`, {
                    method: "GET",
                    headers,
                    cache: "no-store",
                }),
                fetch(`${API_BASE_URL}/api/calendar/items/upcoming?limit=6`, {
                    method: "GET",
                    headers,
                    cache: "no-store",
                }),
            ]);

            const [itemsData, upcomingData] = await Promise.all([
                readJsonResponse(itemsResponse),
                readJsonResponse(upcomingResponse),
            ]);

            if (!itemsResponse.ok) {
                throw new Error(itemsData.error || "Could not load calendar items.");
            }

            if (!upcomingResponse.ok) {
                throw new Error(
                    upcomingData.error || "Could not load upcoming items.",
                );
            }

            setItems(Array.isArray(itemsData.items) ? itemsData.items : []);
            setIsMainUser(Boolean(itemsData.isMainUser));
            setUpcomingItems(
                Array.isArray(upcomingData.items) ? upcomingData.items : [],
            );
            setStatus("success");
        } catch (loadError) {
            console.error("Unable to load calendar:", loadError);
            setError(loadError.message || "Could not load the calendar.");
            setStatus("error");
        }
    }, [currentUser, getAuthHeaders, visibleMonth]);

    useEffect(() => {
        loadCalendar();
        loadTelegramSubscription();
    }, [loadCalendar, loadTelegramSubscription]);

    const monthCells = useMemo(() => {
        const first = startOfMonth(visibleMonth);
        const mondayIndex = (first.getDay() + 6) % 7;
        const gridStart = new Date(
            first.getFullYear(),
            first.getMonth(),
            1 - mondayIndex,
        );

        return Array.from({ length: 42 }, (_, index) => {
            const date = new Date(gridStart);
            date.setDate(gridStart.getDate() + index);

            return {
                date,
                key: localDateKey(date),
                isCurrentMonth: date.getMonth() === visibleMonth.getMonth(),
                isToday: localDateKey(date) === localDateKey(new Date()),
                isPast: isPastDate(date),
            };
        });
    }, [visibleMonth]);

    const monthWeeks = useMemo(
        () =>
            Array.from({ length: 6 }, (_, weekIndex) => {
                const cells = monthCells.slice(
                    weekIndex * 7,
                    weekIndex * 7 + 7,
                );
                const segments = buildWeekSegments(cells, items);
                const laneCount = segments.reduce(
                    (highest, segment) =>
                        Math.max(highest, segment.lane + 1),
                    0,
                );

                return {
                    key: cells[0]?.key || `week-${weekIndex}`,
                    cells,
                    segments,
                    laneCount,
                };
            }),
        [monthCells, items],
    );

    const kotaroMessage = useMemo(
        () => getKotaroMessage(upcomingItems, status),
        [upcomingItems, status],
    );

    function openCreateModal(date = new Date()) {
        if (isPastDate(date)) {
            return;
        }

        const dateKey = localDateKey(date);

        setEditingItem(null);
        setForm({
            ...EMPTY_FORM,
            startDate: dateKey,
            endDate: dateKey,
            dueDate: dateKey,
        });
        setSelectedUsers([]);
        setUserQuery("");
        setUserResults([]);
        setFormError("");
        setModalOpen(true);
    }

    function openEditModal(item) {
        const rawStart = item.startAt || "";
        const rawEnd = item.endAt || "";
        const rawDue = item.dueAt || "";
        const fallbackDate = itemDateKey(item) || todayDateKey();

        setEditingItem(item);
        setForm({
            itemType: item.itemType || "event",
            title: item.title || "",
            description: item.description || "",
            startDate: rawStart.slice(0, 10) || fallbackDate,
            startTime: rawStart.slice(11, 16) || "09:00",
            endDate: rawEnd.slice(0, 10) || fallbackDate,
            endTime: rawEnd.slice(11, 16) || "10:00",
            dueDate: rawDue.slice(0, 10) || fallbackDate,
            dueTime: rawDue.slice(11, 16) || "17:00",
            allDay: Boolean(item.allDay),
            visibility: isMainUser ? (item.visibility || "personal") : "personal",
            taggedUserIds: Array.isArray(item.taggedUserIds)
                ? item.taggedUserIds
                : [],
            status: item.status || "pending",
            recurrenceFrequency: item.recurrence?.frequency || "none",
            recurrenceInterval: item.recurrence?.interval || 1,
            recurrenceEndType: item.recurrence?.count
                ? "count"
                : item.recurrence?.endAt
                  ? "date"
                  : "never",
            recurrenceEndDate: item.recurrence?.endAt?.slice(0, 10) || "",
            recurrenceCount: item.recurrence?.count || 10,
        });
        setSelectedUsers(
            Array.isArray(item.taggedUsers) ? item.taggedUsers : [],
        );
        setUserQuery("");
        setUserResults([]);
        setFormError("");
        setModalOpen(true);
    }

    function closeModal() {
        if (saving || deleting) return;
        setModalOpen(false);
        setEditingItem(null);
        setForm(EMPTY_FORM);
        setSelectedUsers([]);
        setFormError("");
    }

    function updateForm(field, value) {
        setForm((current) => ({
            ...current,
            [field]: value,
        }));
    }

    useEffect(() => {
        if (!modalOpen || userQuery.trim().length < 2) {
            setUserResults([]);
            return undefined;
        }

        const timeout = window.setTimeout(async () => {
            setSearchingUsers(true);

            try {
                const headers = await getAuthHeaders();
                const response = await fetch(
                    `${API_BASE_URL}/api/users/search?q=${encodeURIComponent(
                        userQuery.trim(),
                    )}`,
                    {
                        method: "GET",
                        headers,
                        cache: "no-store",
                    },
                );

                const data = await readJsonResponse(response);

                if (!response.ok) {
                    throw new Error(data.error || "Could not search users.");
                }

                const selectedIds = new Set(
                    selectedUsers.map((user) => user.uid),
                );

                setUserResults(
                    (Array.isArray(data.users) ? data.users : []).filter(
                        (user) =>
                            user.uid !== currentUser?.uid &&
                            !selectedIds.has(user.uid),
                    ),
                );
            } catch (searchError) {
                console.error("Unable to search users:", searchError);
                setUserResults([]);
            } finally {
                setSearchingUsers(false);
            }
        }, 350);

        return () => window.clearTimeout(timeout);
    }, [
        modalOpen,
        userQuery,
        selectedUsers,
        currentUser?.uid,
        getAuthHeaders,
    ]);

    function addTaggedUser(user) {
        setSelectedUsers((current) => [...current, user]);
        setUserQuery("");
        setUserResults([]);
    }

    function removeTaggedUser(uid) {
        setSelectedUsers((current) =>
            current.filter((user) => user.uid !== uid),
        );
    }

    function makePayload() {
        const taggedUserIds = selectedUsers.map((user) => user.uid);

        const recurrence = {
            frequency: form.recurrenceFrequency,
            interval: Number(form.recurrenceInterval) || 1,
            endAt:
                form.recurrenceFrequency !== "none" &&
                form.recurrenceEndType === "date" &&
                form.recurrenceEndDate
                    ? buildIso(form.recurrenceEndDate, "23:59", false)
                    : null,
            count:
                form.recurrenceFrequency !== "none" &&
                form.recurrenceEndType === "count"
                    ? Number(form.recurrenceCount) || 1
                    : null,
        };

        const payload = {
            itemType: form.itemType,
            title: form.title.trim(),
            description: form.description.trim(),
            visibility: form.visibility,
            taggedUserIds,
            allDay: form.allDay,
            recurrence,
        };

        if (form.itemType === "event") {
            payload.startAt = buildIso(
                form.startDate,
                form.startTime,
                form.allDay,
            );
            payload.endAt = buildIso(
                form.endDate,
                form.endTime,
                form.allDay,
            );
        } else if (form.itemType === "task") {
            payload.startAt = buildIso(
                form.startDate,
                form.startTime,
                form.allDay,
            );
            payload.dueAt = buildIso(
                form.dueDate,
                form.dueTime,
                form.allDay,
            );
            payload.status = form.status;
        } else {
            payload.dueAt = buildIso(
                form.dueDate,
                form.dueTime,
                form.allDay,
            );
        }

        return payload;
    }

    async function handleSubmit(event) {
        event.preventDefault();

        if (!form.title.trim()) {
            setFormError("Give this item a title.");
            return;
        }

        if (form.itemType === "event") {
            if (!form.startDate || !form.endDate) {
                setFormError("Choose both a start date and an end date.");
                return;
            }
        } else if (form.itemType === "task") {
            if (!form.startDate || !form.dueDate) {
                setFormError("Choose both a start date and a deadline date.");
                return;
            }
        } else if (!form.dueDate) {
            setFormError("Choose a reminder date.");
            return;
        }

        const startValue = formStartDateTime(form);
        const endValue = formEndDateTime(form);

        if (form.recurrenceFrequency !== "none") {
            const interval = Number(form.recurrenceInterval);
            if (!Number.isInteger(interval) || interval < 1 || interval > 365) {
                setFormError("Repeat interval must be between 1 and 365.");
                return;
            }

            if (form.recurrenceEndType === "date") {
                if (!form.recurrenceEndDate) {
                    setFormError("Choose when the recurring series should end.");
                    return;
                }

                const firstDate = form.itemType === "reminder"
                    ? form.dueDate
                    : form.startDate;
                if (form.recurrenceEndDate < firstDate) {
                    setFormError("The repeat end date cannot be before the first item.");
                    return;
                }
            }

            if (form.recurrenceEndType === "count") {
                const count = Number(form.recurrenceCount);
                if (!Number.isInteger(count) || count < 1 || count > 1000) {
                    setFormError("Number of occurrences must be between 1 and 1000.");
                    return;
                }
            }
        }

        if (!editingItem) {
            const firstDate = form.itemType === "reminder"
                ? form.dueDate
                : form.startDate;
            const firstTime = form.itemType === "reminder"
                ? form.dueTime
                : form.startTime;

            if (isPastDateTime(firstDate, firstTime, form.allDay)) {
                setFormError(
                    form.allDay
                        ? "Choose today or a future date."
                        : "Choose a start time that has not already passed.",
                );
                return;
            }
        }

        if (startValue && endValue && endValue < startValue) {
            setFormError(
                form.itemType === "task"
                    ? "The deadline cannot be earlier than the task start."
                    : "The event end cannot be earlier than the event start.",
            );
            return;
        }

        setSaving(true);
        setFormError("");

        try {
            const headers = await getAuthHeaders(true);
            const isEditing = Boolean(editingItem?.id);
            const url = isEditing
                ? `${API_BASE_URL}/api/calendar/items/${editingItem.id}`
                : `${API_BASE_URL}/api/calendar/items`;

            const response = await fetch(url, {
                method: isEditing ? "PATCH" : "POST",
                headers,
                body: JSON.stringify(makePayload()),
            });

            const data = await readJsonResponse(response);

            if (!response.ok) {
                throw new Error(
                    data.error ||
                        `Could not ${isEditing ? "update" : "create"} this item.`,
                );
            }

            closeModal();
            await loadCalendar();
        } catch (saveError) {
            console.error("Unable to save calendar item:", saveError);
            setFormError(
                saveError.message || "Could not save this calendar item.",
            );
        } finally {
            setSaving(false);
        }
    }

    async function handleDelete() {
        if (!editingItem?.id || editingItem.ownerId !== currentUser?.uid) {
            return;
        }

        const confirmed = window.confirm(
            editingItem.isRecurringOccurrence
                ? `Delete the entire recurring series for "${editingItem.title}"? This cannot be undone.`
                : `Delete "${editingItem.title}"? This cannot be undone.`,
        );

        if (!confirmed) {
            return;
        }

        setDeleting(true);
        setFormError("");

        try {
            const headers = await getAuthHeaders();
            const response = await fetch(
                `${API_BASE_URL}/api/calendar/items/${editingItem.id}`,
                {
                    method: "DELETE",
                    headers,
                },
            );

            const data = await readJsonResponse(response);

            if (!response.ok) {
                throw new Error(data.error || "Could not delete this item.");
            }

            setModalOpen(false);
            setEditingItem(null);
            setForm(EMPTY_FORM);
            setSelectedUsers([]);
            await loadCalendar();
        } catch (deleteError) {
            console.error("Unable to delete calendar item:", deleteError);
            setFormError(
                deleteError.message || "Could not delete this calendar item.",
            );
        } finally {
            setDeleting(false);
        }
    }

    return (
        <main className="calendar-page">
            <div className="calendar-shell">
                <header className="calendar-nav">
                                    <button
                                            type="button"
                                            className="brand-mark"
                                            onClick={() => navigate("/")}
                                            aria-label="Go to homepage"
                                        >
                                            <img
                                                src={kotaroImage}
                                                alt="Kotaro"
                                                className="brand-logo"
                                                style={{ width: "auto", height: "80px" }}
                                            />
                    
                                            <span>
                                                <h1>The Nowl In One</h1>
                                            </span>
                                        </button>

                    <div className="calendar-nav__actions">
                        <button
                            type="button"
                            className="calendar-nav-button"
                            onClick={() => navigate("/")}
                        >
                            Home
                        </button>
                        <button
                            type="button"
                            className="calendar-nav-button calendar-nav-button--blue"
                            onClick={() => openCreateModal(new Date())}
                        >
                            + New item
                        </button>
                    </div>
                </header>

                <section className="calendar-hero">
                    <div>
                        <p className="calendar-eyebrow">Shared calendar</p>
                        <h1>Yall really believe yall can manage a calendar? Be my guests!!!</h1>
                        <p>
                            Manage events
                            without losing sight of what is coming next.
                        </p>
                    </div>
                    <div className="kotaro-assistant">
                        <div className="kotaro-chat" role="status" aria-live="polite">
                            <strong>{kotaroMessage.title}</strong>
                            <span>{kotaroMessage.detail}</span>
                        </div>

                        <img
                            src={seckotaroImage}
                            alt="Kotaro the calendar assistant"
                            className="kotaro"
                        />
                    </div>
                </section>

                <section className="calendar-layout">
                    <article className="calendar-card">
                        <div className="calendar-toolbar">
                            <div className="calendar-toolbar__navigation">
                                <button
                                    type="button"
                                    onClick={() =>
                                        setVisibleMonth((month) =>
                                            addMonths(month, -1),
                                        )
                                    }
                                    aria-label="Previous month"
                                >
                                    ←
                                </button>
                                <button
                                    type="button"
                                    onClick={() =>
                                        setVisibleMonth(startOfMonth(new Date()))
                                    }
                                >
                                    Today
                                </button>
                                <button
                                    type="button"
                                    onClick={() =>
                                        setVisibleMonth((month) =>
                                            addMonths(month, 1),
                                        )
                                    }
                                    aria-label="Next month"
                                >
                                    →
                                </button>
                            </div>

                            <h2>{formatMonth(visibleMonth)}</h2>

                            <button
                                type="button"
                                className="calendar-refresh-button"
                                onClick={loadCalendar}
                                disabled={status === "loading"}
                            >
                                {status === "loading" ? "Loading…" : "Refresh"}
                            </button>
                        </div>

                        {error && (
                            <div className="calendar-error-state">{error}</div>
                        )}

                        <div className="calendar-weekdays" aria-hidden="true">
                            {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map(
                                (day) => (
                                    <span key={day}>{day}</span>
                                ),
                            )}
                        </div>

                        <div
                            className="calendar-grid"
                            aria-label={`${formatMonth(visibleMonth)} calendar`}
                        >
                            {monthWeeks.map((week) => (
                                <div
                                    className="calendar-week-row"
                                    key={week.key}
                                    style={{
                                        "--calendar-lanes": Math.max(
                                            week.laneCount,
                                            1,
                                        ),
                                    }}
                                >
                                    <div className="calendar-week-days">
                                        {week.cells.map((cell) => (
                                            <div
                                                key={cell.key}
                                                className={[
                                                    "calendar-day",
                                                    !cell.isCurrentMonth
                                                        ? "calendar-day--muted"
                                                        : "",
                                                    cell.isToday
                                                        ? "calendar-day--today"
                                                        : "",
                                                    cell.isPast
                                                        ? "calendar-day--past"
                                                        : "",
                                                ]
                                                    .filter(Boolean)
                                                    .join(" ")}
                                                onClick={() => {
                                                    if (!cell.isPast) {
                                                        openCreateModal(cell.date);
                                                    }
                                                }}
                                                onKeyDown={(event) => {
                                                    if (
                                                        !cell.isPast &&
                                                        (event.key === "Enter" ||
                                                            event.key === " ")
                                                    ) {
                                                        openCreateModal(cell.date);
                                                    }
                                                }}
                                                role="button"
                                                tabIndex={cell.isPast ? -1 : 0}
                                                aria-disabled={cell.isPast}
                                            >
                                                <div className="calendar-day__header">
                                                    <span>{cell.date.getDate()}</span>
                                                    {!cell.isPast && (
                                                        <span className="calendar-day__plus">
                                                            +
                                                        </span>
                                                    )}
                                                </div>
                                            </div>
                                        ))}
                                    </div>

                                    <div className="calendar-week-items">
                                        {week.segments.map((segment) => (
                                            <button
                                                type="button"
                                                key={`${segment.item.id}-${week.key}`}
                                                className={[
                                                    "calendar-item",
                                                    "calendar-item--range",
                                                    `calendar-item--${segment.item.itemType}`,
                                                    segment.continuesBefore
                                                        ? "calendar-item--continues-before"
                                                        : "",
                                                    segment.continuesAfter
                                                        ? "calendar-item--continues-after"
                                                        : "",
                                                ]
                                                    .filter(Boolean)
                                                    .join(" ")}
                                                style={{
                                                    gridColumn: `${segment.startColumn} / ${segment.endColumn}`,
                                                    gridRow: segment.lane + 1,
                                                }}
                                                onClick={(event) => {
                                                    event.stopPropagation();
                                                    openEditModal(segment.item);
                                                }}
                                                title={segment.item.title}
                                            >
                                                {!segment.continuesBefore && (
                                                    <span className="calendar-item__time">
                                                        {itemTimeLabel(segment.item)}
                                                    </span>
                                                )}
                                                <span className="calendar-item__title">
                                                    {segment.item.title}
                                                </span>
                                                {segment.item.recurrence?.frequency !== "none" && (
                                                    <span
                                                        className="calendar-item__repeat"
                                                        title={recurrenceLabel(segment.item)}
                                                        aria-label={recurrenceLabel(segment.item)}
                                                    >
                                                        ↻
                                                    </span>
                                                )}
                                                {segment.item.taggedUserIds?.length >
                                                    0 && (
                                                    <span
                                                        className="calendar-item__tag"
                                                        aria-label="Tagged users"
                                                    >
                                                        @
                                                    </span>
                                                )}
                                            </button>
                                        ))}
                                    </div>
                                </div>
                            ))}
                        </div>
                    </article>

                    <aside className="calendar-sidebar">
                        <section className="upcoming-card">
                            <div className="upcoming-card__title">
                                <div>
                                    <h2>Coming up</h2>
                                </div>
                                <span className="upcoming-card__count">
                                    {upcomingItems.length}
                                </span>
                            </div>

                            {status === "loading" && (
                                <p className="upcoming-empty">Loading upcoming items…</p>
                            )}

                            {status !== "loading" && upcomingItems.length === 0 && (
                                <div className="upcoming-empty-state">
                                    <strong>Your schedule is clear.</strong>
                                    <p>Create an item to start planning ahead.</p>
                                    <button
                                        type="button"
                                        className="calendar-secondary-button"
                                        onClick={() => openCreateModal(new Date())}
                                    >
                                        Add an item
                                    </button>
                                </div>
                            )}

                            <div className="upcoming-list">
                                {upcomingItems.map((item) => (
                                    <button
                                        type="button"
                                        className="upcoming-item"
                                        key={item.id}
                                        onClick={() => openEditModal(item)}
                                    >
                                        <span
                                            className={`upcoming-item__icon upcoming-item__icon--${item.itemType}`}
                                        >
                                            {item.itemType === "event"
                                                ? "E"
                                                : item.itemType === "task"
                                                  ? "T"
                                                  : "R"}
                                        </span>
                                        <span>
                                            <strong>{item.title}</strong>
                                            <small>
                                                {formatFriendlyDate(
                                                    item.startAt || item.dueAt,
                                                )}
                                                {item.recurrence?.frequency !== "none"
                                                    ? ` · ${recurrenceLabel(item)}`
                                                    : ""}
                                            </small>
                                        </span>
                                    </button>
                                ))}
                            </div>
                        </section>

                        <section className="telegram-card">
                            <div className="telegram-card__header">
                                <div>
                                    <p className="calendar-section-label">
                                        Notifications
                                    </p>
                                    <h2>Telegram reminders</h2>
                                </div>

                                <span
                                    className={[
                                        "telegram-status-pill",
                                        telegramSubscription.active
                                            ? "telegram-status-pill--active"
                                            : "",
                                    ]
                                        .filter(Boolean)
                                        .join(" ")}
                                >
                                    {telegramStatus === "loading"
                                        ? "Checking"
                                        : telegramSubscription.active
                                          ? "Active"
                                          : "Not connected"}
                                </span>
                            </div>

                            <p className="telegram-card__description">
                                Receive a daily summary of your upcoming events,
                                tasks, and reminders in Telegram.
                            </p>

                            {telegramSubscription.active ? (
                                <div className="telegram-connected-box">
                                    <strong>
                                        Connected
                                        {telegramSubscription.telegramFirstName
                                            ? ` as ${telegramSubscription.telegramFirstName}`
                                            : ""}
                                    </strong>
                                    <span>
                                        Daily reminder at{" "}
                                        {telegramSubscription.reminderHour}:00 AM
                                        Singapore time
                                    </span>
                                </div>
                            ) : (
                                <div className="telegram-how-it-works">
                                    <span>1</span>
                                    <p>Open Telegram using the button below.</p>
                                    <span>2</span>
                                    <p>Press Start in the bot chat.</p>
                                    <span>3</span>
                                    <p>Return here and refresh the status.</p>
                                </div>
                            )}

                            {telegramMessage && (
                                <div className="telegram-card__message">
                                    {telegramMessage}
                                </div>
                            )}

                            <div className="telegram-card__actions">
                                {!telegramSubscription.active ? (
                                    <>
                                        <button
                                            type="button"
                                            className="telegram-primary-button"
                                            onClick={handleConnectTelegram}
                                            disabled={telegramBusy}
                                        >
                                            {telegramBusy
                                                ? "Opening…"
                                                : "Connect Telegram"}
                                        </button>
                                        <button
                                            type="button"
                                            className="telegram-secondary-button"
                                            onClick={handleRefreshTelegram}
                                            disabled={
                                                telegramBusy ||
                                                telegramStatus === "loading"
                                            }
                                        >
                                            Refresh status
                                        </button>
                                    </>
                                ) : (
                                    <>
                                        <button
                                            type="button"
                                            className="telegram-primary-button"
                                            onClick={handleTelegramTest}
                                            disabled={telegramBusy}
                                        >
                                            {telegramBusy
                                                ? "Sending…"
                                                : "Send test"}
                                        </button>
                                        <button
                                            type="button"
                                            className="telegram-secondary-button"
                                            onClick={handleRefreshTelegram}
                                            disabled={
                                                telegramBusy ||
                                                telegramStatus === "loading"
                                            }
                                        >
                                            Refresh
                                        </button>
                                        <button
                                            type="button"
                                            className="telegram-danger-button"
                                            onClick={handleDisconnectTelegram}
                                            disabled={telegramBusy}
                                        >
                                            Disconnect
                                        </button>
                                    </>
                                )}
                            </div>
                        </section>

                        <section className="calendar-legend">
                            <p className="calendar-section-label">Item types</p>
                            <div>
                                <span>
                                    <i className="legend-dot legend-dot--event" />
                                    Events
                                </span>
                                <span>
                                    <i className="legend-dot legend-dot--task" />
                                    Tasks
                                </span>
                                <span>
                                    <i className="legend-dot legend-dot--reminder" />
                                    Reminders
                                </span>
                            </div>
                        </section>
                    </aside>
                </section>
            </div>

            {modalOpen && (
                <div
                    className="calendar-modal-backdrop"
                    onMouseDown={(event) => {
                        if (event.target === event.currentTarget) closeModal();
                    }}
                >
                    <section
                        className="calendar-modal"
                        role="dialog"
                        aria-modal="true"
                        aria-labelledby="calendar-modal-title"
                    >
                        <div className="calendar-modal__header">
                            <div>
                                <p className="calendar-section-label">
                                    {editingItem ? "Update plan" : "New plan"}
                                </p>
                                <h2 id="calendar-modal-title">
                                    {editingItem?.isRecurringOccurrence
                                        ? "Edit recurring series"
                                        : editingItem
                                          ? "Edit calendar item"
                                          : "Create calendar item"}
                                </h2>
                            </div>

                            <button
                                type="button"
                                className="calendar-modal__close"
                                onClick={closeModal}
                                aria-label="Close"
                                disabled={saving || deleting}
                            >
                                ×
                            </button>
                        </div>

                        <form
                            className="calendar-form"
                            onSubmit={handleSubmit}
                        >
                            <div className="calendar-type-picker">
                                {ITEM_TYPES.map((type) => (
                                    <button
                                        key={type}
                                        type="button"
                                        className={
                                            form.itemType === type
                                                ? "is-selected"
                                                : ""
                                        }
                                        onClick={() =>
                                            updateForm("itemType", type)
                                        }
                                    >
                                        {type}
                                    </button>
                                ))}
                            </div>

                            <label className="calendar-field calendar-field--full">
                                <span>Title</span>
                                <input
                                    type="text"
                                    value={form.title}
                                    onChange={(event) =>
                                        updateForm("title", event.target.value)
                                    }
                                    placeholder="What is happening?"
                                    maxLength={120}
                                    autoFocus
                                />
                            </label>

                            <label className="calendar-field calendar-field--full">
                                <span>Description</span>
                                <textarea
                                    value={form.description}
                                    onChange={(event) =>
                                        updateForm(
                                            "description",
                                            event.target.value,
                                        )
                                    }
                                    placeholder="Add useful details"
                                    rows={3}
                                    maxLength={2000}
                                />
                            </label>

                            <div className="calendar-schedule-section">
                                <div className="calendar-schedule-section__header">
                                    <strong>
                                        {form.itemType === "event"
                                            ? "Event duration"
                                            : form.itemType === "task"
                                              ? "Task timeline"
                                              : "Reminder time"}
                                    </strong>

                                    <label className="calendar-check-field">
                                        <input
                                            type="checkbox"
                                            checked={form.allDay}
                                            onChange={(event) =>
                                                updateForm(
                                                    "allDay",
                                                    event.target.checked,
                                                )
                                            }
                                        />
                                        <span>All day</span>
                                    </label>
                                </div>

                                {form.itemType === "event" && (
                                    <>
                                        <div className="calendar-form__row">
                                            <label className="calendar-field">
                                                <span>Start date</span>
                                                <input
                                                    type="date"
                                                    value={form.startDate}
                                                    min={editingItem ? undefined : todayDateKey()}
                                                    onChange={(event) => {
                                                        const value = event.target.value;
                                                        updateForm("startDate", value);
                                                        if (!form.endDate || form.endDate < value) {
                                                            updateForm("endDate", value);
                                                        }
                                                    }}
                                                />
                                            </label>

                                            <label className="calendar-field">
                                                <span>End date</span>
                                                <input
                                                    type="date"
                                                    value={form.endDate}
                                                    min={form.startDate || todayDateKey()}
                                                    onChange={(event) =>
                                                        updateForm("endDate", event.target.value)
                                                    }
                                                />
                                            </label>
                                        </div>

                                        {!form.allDay && (
                                            <div className="calendar-form__row">
                                                <label className="calendar-field">
                                                    <span>Start time</span>
                                                    <input
                                                        type="time"
                                                        value={form.startTime}
                                                        onChange={(event) =>
                                                            updateForm("startTime", event.target.value)
                                                        }
                                                    />
                                                </label>

                                                <label className="calendar-field">
                                                    <span>End time</span>
                                                    <input
                                                        type="time"
                                                        value={form.endTime}
                                                        onChange={(event) =>
                                                            updateForm("endTime", event.target.value)
                                                        }
                                                    />
                                                </label>
                                            </div>
                                        )}
                                    </>
                                )}

                                {form.itemType === "task" && (
                                    <>
                                        <div className="calendar-form__row">
                                            <label className="calendar-field">
                                                <span>Start date</span>
                                                <input
                                                    type="date"
                                                    value={form.startDate}
                                                    min={editingItem ? undefined : todayDateKey()}
                                                    onChange={(event) => {
                                                        const value = event.target.value;
                                                        updateForm("startDate", value);
                                                        if (!form.dueDate || form.dueDate < value) {
                                                            updateForm("dueDate", value);
                                                        }
                                                    }}
                                                />
                                            </label>

                                            <label className="calendar-field">
                                                <span>Deadline date</span>
                                                <input
                                                    type="date"
                                                    value={form.dueDate}
                                                    min={form.startDate || todayDateKey()}
                                                    onChange={(event) =>
                                                        updateForm("dueDate", event.target.value)
                                                    }
                                                />
                                            </label>
                                        </div>

                                        {!form.allDay && (
                                            <div className="calendar-form__row">
                                                <label className="calendar-field">
                                                    <span>Start time</span>
                                                    <input
                                                        type="time"
                                                        value={form.startTime}
                                                        onChange={(event) =>
                                                            updateForm("startTime", event.target.value)
                                                        }
                                                    />
                                                </label>

                                                <label className="calendar-field">
                                                    <span>Deadline time</span>
                                                    <input
                                                        type="time"
                                                        value={form.dueTime}
                                                        onChange={(event) =>
                                                            updateForm("dueTime", event.target.value)
                                                        }
                                                    />
                                                </label>
                                            </div>
                                        )}
                                    </>
                                )}

                                {form.itemType === "reminder" && (
                                    <div className="calendar-form__row">
                                        <label className="calendar-field">
                                            <span>Reminder date</span>
                                            <input
                                                type="date"
                                                value={form.dueDate}
                                                min={editingItem ? undefined : todayDateKey()}
                                                onChange={(event) =>
                                                    updateForm("dueDate", event.target.value)
                                                }
                                            />
                                        </label>

                                        {!form.allDay && (
                                            <label className="calendar-field">
                                                <span>Reminder time</span>
                                                <input
                                                    type="time"
                                                    value={form.dueTime}
                                                    onChange={(event) =>
                                                        updateForm("dueTime", event.target.value)
                                                    }
                                                />
                                            </label>
                                        )}
                                    </div>
                                )}
                            </div>

                            <div className="calendar-recurrence-section">
                                <div className="calendar-recurrence-section__header">
                                    <div>
                                        <strong>Repeat</strong>
                                        <span>Automatically place future occurrences on the calendar.</span>
                                    </div>
                                    {form.recurrenceFrequency !== "none" && (
                                        <span className="calendar-repeat-pill">Recurring</span>
                                    )}
                                </div>

                                <div className="calendar-form__row">
                                    <label className="calendar-field">
                                        <span>Frequency</span>
                                        <select
                                            value={form.recurrenceFrequency}
                                            onChange={(event) =>
                                                updateForm("recurrenceFrequency", event.target.value)
                                            }
                                        >
                                            {RECURRENCE_FREQUENCIES.map((frequency) => (
                                                <option key={frequency} value={frequency}>
                                                    {frequency === "none"
                                                        ? "Does not repeat"
                                                        : frequency[0].toUpperCase() + frequency.slice(1)}
                                                </option>
                                            ))}
                                        </select>
                                    </label>

                                    {form.recurrenceFrequency !== "none" && (
                                        <label className="calendar-field">
                                            <span>Repeat every</span>
                                            <div className="calendar-repeat-interval">
                                                <input
                                                    type="number"
                                                    min="1"
                                                    max="365"
                                                    value={form.recurrenceInterval}
                                                    onChange={(event) =>
                                                        updateForm("recurrenceInterval", event.target.value)
                                                    }
                                                />
                                                <span>
                                                    {form.recurrenceFrequency === "daily"
                                                        ? "day(s)"
                                                        : form.recurrenceFrequency === "weekly"
                                                          ? "week(s)"
                                                          : form.recurrenceFrequency === "monthly"
                                                            ? "month(s)"
                                                            : "year(s)"}
                                                </span>
                                            </div>
                                        </label>
                                    )}
                                </div>

                                {form.recurrenceFrequency !== "none" && (
                                    <>
                                        <label className="calendar-field">
                                            <span>Ends</span>
                                            <select
                                                value={form.recurrenceEndType}
                                                onChange={(event) =>
                                                    updateForm("recurrenceEndType", event.target.value)
                                                }
                                            >
                                                <option value="never">Never</option>
                                                <option value="date">On a date</option>
                                                <option value="count">After a number of occurrences</option>
                                            </select>
                                        </label>

                                        {form.recurrenceEndType === "date" && (
                                            <label className="calendar-field">
                                                <span>Last occurrence date</span>
                                                <input
                                                    type="date"
                                                    value={form.recurrenceEndDate}
                                                    min={
                                                        form.itemType === "reminder"
                                                            ? form.dueDate
                                                            : form.startDate
                                                    }
                                                    onChange={(event) =>
                                                        updateForm("recurrenceEndDate", event.target.value)
                                                    }
                                                />
                                            </label>
                                        )}

                                        {form.recurrenceEndType === "count" && (
                                            <label className="calendar-field">
                                                <span>Number of occurrences</span>
                                                <input
                                                    type="number"
                                                    min="1"
                                                    max="1000"
                                                    value={form.recurrenceCount}
                                                    onChange={(event) =>
                                                        updateForm("recurrenceCount", event.target.value)
                                                    }
                                                />
                                            </label>
                                        )}
                                    </>
                                )}

                                {editingItem?.isRecurringOccurrence && (
                                    <p className="calendar-recurring-note">
                                        This is one occurrence in a recurring series. Saving or deleting it updates the whole series.
                                    </p>
                                )}
                            </div>

                            <div className="calendar-form__row">
                                <label className="calendar-field">
                                    <span>Visibility</span>
                                    <select
                                        value={form.visibility}
                                        onChange={(event) =>
                                            updateForm(
                                                "visibility",
                                                event.target.value,
                                            )
                                        }
                                    >
                                        {(isMainUser ? VISIBILITIES : ["personal"]).map((visibility) => (
                                            <option
                                                key={visibility}
                                                value={visibility}
                                            >
                                                {visibility === "all"
                                                    ? "All users (main group)"
                                                    : visibility[0].toUpperCase() +
                                                      visibility.slice(1)}
                                            </option>
                                        ))}
                                    </select>
                                </label>

                                {form.itemType === "task" && (
                                    <label className="calendar-field">
                                        <span>Status</span>
                                        <select
                                            value={form.status}
                                            onChange={(event) =>
                                                updateForm(
                                                    "status",
                                                    event.target.value,
                                                )
                                            }
                                        >
                                            <option value="pending">
                                                Pending
                                            </option>
                                            <option value="in_progress">
                                                In progress
                                            </option>
                                            <option value="completed">
                                                Completed
                                            </option>
                                        </select>
                                    </label>
                                )}
                            </div>

                            <div className="calendar-field calendar-field--full">
                                <span>Tag users</span>

                                <div className="tagged-user-list">
                                    {selectedUsers.map((user) => (
                                        <button
                                            type="button"
                                            key={user.uid}
                                            className="tagged-user-chip"
                                            onClick={() =>
                                                removeTaggedUser(user.uid)
                                            }
                                        >
                                            {user.displayName || user.email}
                                            <span>×</span>
                                        </button>
                                    ))}
                                </div>

                                <div className="calendar-user-search">
                                    <input
                                        type="search"
                                        value={userQuery}
                                        onChange={(event) =>
                                            setUserQuery(event.target.value)
                                        }
                                        placeholder="Search by name or email"
                                    />

                                    {(searchingUsers ||
                                        userResults.length > 0) && (
                                        <div className="calendar-user-results">
                                            {searchingUsers && (
                                                <span>Searching…</span>
                                            )}

                                            {!searchingUsers &&
                                                userResults.map((user) => (
                                                    <button
                                                        type="button"
                                                        key={user.uid}
                                                        onClick={() =>
                                                            addTaggedUser(user)
                                                        }
                                                    >
                                                        <strong>
                                                            {user.displayName ||
                                                                "Unnamed user"}
                                                        </strong>
                                                        <small>
                                                            {user.email}
                                                        </small>
                                                    </button>
                                                ))}
                                        </div>
                                    )}
                                </div>
                            </div>

                            {formError && (
                                <div className="calendar-form-error">
                                    {formError}
                                </div>
                            )}

                            <div className="calendar-form__actions">
                                {editingItem?.ownerId === currentUser?.uid && (
                                    <button
                                        type="button"
                                        className="calendar-delete-button"
                                        onClick={handleDelete}
                                        disabled={saving || deleting}
                                    >
                                        {deleting ? "Deleting…" : "Delete item"}
                                    </button>
                                )}

                                <div className="calendar-form__actions-main">
                                    <button
                                        type="button"
                                        className="calendar-secondary-button"
                                        onClick={closeModal}
                                        disabled={saving || deleting}
                                    >
                                        Cancel
                                    </button>
                                    <button
                                        type="submit"
                                        className="calendar-primary-button"
                                        disabled={saving || deleting}
                                    >
                                        {saving
                                            ? "Saving…"
                                            : editingItem
                                              ? "Save changes"
                                              : "Create item"}
                                    </button>
                                </div>
                            </div>
                        </form>
                    </section>
                </div>
            )}
        </main>
    );
}

export default CalendarPage;
