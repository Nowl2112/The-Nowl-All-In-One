import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../../context/authContext.jsx";
import haruImage from "../../assets/haru.png";
import kotaroImage from "../../assets/kotaro.png";
import "./homepage.css";

const API_BASE_URL =
    import.meta.env.VITE_API_BASE_URL ||
    import.meta.env.VITE_API_BASE ||
    "";


function getItemDate(item) {
    const rawDate =
        item?.itemType === "event"
            ? item?.startAt
            : item?.dueAt;

    if (!rawDate) return null;

    const parsedDate = new Date(rawDate);
    return Number.isNaN(parsedDate.getTime()) ? null : parsedDate;
}

function isWithinNextSevenDays(item) {
    const itemDate = getItemDate(item);
    if (!itemDate) return false;

    const now = new Date();
    const sevenDaysFromNow = new Date(now);
    sevenDaysFromNow.setDate(now.getDate() + 7);

    return itemDate >= now && itemDate < sevenDaysFromNow;
}

function formatUpcomingDate(item) {
    const itemDate = getItemDate(item);
    if (!itemDate) return "Date unavailable";

    if (item?.allDay) {
        return itemDate.toLocaleDateString("en-SG", {
            weekday: "short",
            day: "numeric",
            month: "short",
        });
    }

    return itemDate.toLocaleString("en-SG", {
        weekday: "short",
        day: "numeric",
        month: "short",
        hour: "numeric",
        minute: "2-digit",
    });
}

function getUpcomingDay(item) {
    const itemDate = getItemDate(item);
    if (!itemDate) return "--";

    return itemDate.toLocaleDateString("en-SG", {
        day: "2-digit",
    });
}

function getUpcomingMonth(item) {
    const itemDate = getItemDate(item);
    if (!itemDate) return "---";

    return itemDate.toLocaleDateString("en-SG", {
        month: "short",
    });
}

async function readJsonResponse(response) {
    const contentType = response.headers.get("content-type") || "";

    if (!contentType.includes("application/json")) {
        throw new Error(
            `The server returned an unexpected response (${response.status}).`,
        );
    }

    return response.json();
}

function HomePage() {
    const navigate = useNavigate();
    const { logout, currentUser } = useAuth();

    const [currentPlayer, setCurrentPlayer] = useState(null);
    const [playerStatus, setPlayerStatus] = useState("idle");
    const [playerError, setPlayerError] = useState("");

    const [leaderboard, setLeaderboard] = useState([]);
    const [leaderboardStatus, setLeaderboardStatus] = useState("idle");
    const [leaderboardError, setLeaderboardError] = useState("");

    const [upcomingItems, setUpcomingItems] = useState([]);
    const [upcomingStatus, setUpcomingStatus] = useState("idle");
    const [upcomingError, setUpcomingError] = useState("");

    const [activeToolIndex, setActiveToolIndex] = useState(0);
    const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false);

    const usefulTools = useMemo(
        () => [
            { id: "calendar", label: "Calendar" },
            { id: "headlines", label: "Today’s headlines" },
            { id: "task-boards", label: "Task boards" },
        ],
        [],
    );

    const showPreviousTool = () => {
        setActiveToolIndex((currentIndex) =>
            currentIndex === 0 ? usefulTools.length - 1 : currentIndex - 1,
        );
    };

    const showNextTool = () => {
        setActiveToolIndex((currentIndex) =>
            currentIndex === usefulTools.length - 1 ? 0 : currentIndex + 1,
        );
    };

    const getAuthHeaders = useCallback(async () => {
        if (!currentUser) {
            throw new Error("You must be signed in.");
        }

        const token = await currentUser.getIdToken();

        return {
            Authorization: `Bearer ${token}`,
        };
    }, [currentUser]);

    const loadCurrentPlayer = useCallback(
        async (signal) => {
            if (!currentUser) {
                setCurrentPlayer(null);
                setPlayerStatus("idle");
                return;
            }

            setPlayerStatus("loading");
            setPlayerError("");

            try {
                const headers = await getAuthHeaders();
                const response = await fetch(
                    `${API_BASE_URL}/api/games/wordle/me`,
                    {
                        method: "GET",
                        headers,
                        signal,
                        cache: "no-store",
                    },
                );

                const data = await readJsonResponse(response);

                if (!response.ok) {
                    throw new Error(
                        data.error || "Could not load your player statistics.",
                    );
                }

                setCurrentPlayer(data.player || null);
                setPlayerStatus("success");
            } catch (error) {
                if (error?.name === "AbortError") return;

                console.error("Unable to load player statistics:", error);
                setCurrentPlayer(null);
                setPlayerError(
                    error?.message || "Could not load your player statistics.",
                );
                setPlayerStatus("error");
            }
        },
        [currentUser, getAuthHeaders],
    );

    const loadLeaderboard = useCallback(
        async (signal) => {
            if (!currentUser) {
                setLeaderboard([]);
                setLeaderboardStatus("idle");
                return;
            }

            setLeaderboardStatus("loading");
            setLeaderboardError("");

            try {
                const headers = await getAuthHeaders();
                const response = await fetch(
                    `${API_BASE_URL}/api/games/wordle/leaderboard?limit=100`,
                    {
                        method: "GET",
                        headers,
                        signal,
                        cache: "no-store",
                    },
                );

                const data = await readJsonResponse(response);

                if (!response.ok) {
                    throw new Error(
                        data.error || "Could not load the leaderboard.",
                    );
                }

                setLeaderboard(
                    Array.isArray(data.leaderboard)
                        ? data.leaderboard
                        : [],
                );
                setLeaderboardStatus("success");
            } catch (error) {
                if (error?.name === "AbortError") return;

                console.error("Unable to load leaderboard:", error);
                setLeaderboard([]);
                setLeaderboardError(
                    error?.message || "Could not load the leaderboard.",
                );
                setLeaderboardStatus("error");
            }
        },
        [currentUser, getAuthHeaders],
    );


    const loadUpcomingItems = useCallback(
        async (signal) => {
            if (!currentUser) {
                setUpcomingItems([]);
                setUpcomingStatus("idle");
                return;
            }

            setUpcomingStatus("loading");
            setUpcomingError("");

            try {
                const headers = await getAuthHeaders();
                const response = await fetch(
                    `${API_BASE_URL}/api/calendar/items/upcoming?limit=50`,
                    {
                        method: "GET",
                        headers,
                        signal,
                        cache: "no-store",
                    },
                );

                const data = await readJsonResponse(response);

                if (!response.ok) {
                    throw new Error(
                        data.error || "Could not load upcoming calendar items.",
                    );
                }

                const nextSevenDays = (
                    Array.isArray(data.items) ? data.items : []
                )
                    .filter(isWithinNextSevenDays)
                    .filter(
                        (item) =>
                            !(
                                item.itemType === "task" &&
                                item.status === "completed"
                            ),
                    )
                    .sort(
                        (firstItem, secondItem) =>
                            getItemDate(firstItem) - getItemDate(secondItem),
                    );

                setUpcomingItems(nextSevenDays);
                setUpcomingStatus("success");
            } catch (error) {
                if (error?.name === "AbortError") return;

                console.error("Unable to load upcoming items:", error);
                setUpcomingItems([]);
                setUpcomingError(
                    error?.message || "Could not load upcoming calendar items.",
                );
                setUpcomingStatus("error");
            }
        },
        [currentUser, getAuthHeaders],
    );

    const refreshHomeData = useCallback(async () => {
        await Promise.all([
            loadCurrentPlayer(),
            loadLeaderboard(),
            loadUpcomingItems(),
        ]);
    }, [loadCurrentPlayer, loadLeaderboard, loadUpcomingItems]);

    useEffect(() => {
        if (!currentUser?.uid) {
            setCurrentPlayer(null);
            setLeaderboard([]);
            setUpcomingItems([]);
            return undefined;
        }

        const controller = new AbortController();

        Promise.all([
            loadCurrentPlayer(controller.signal),
            loadLeaderboard(controller.signal),
            loadUpcomingItems(controller.signal),
        ]);

        return () => controller.abort();
    }, [
        currentUser?.uid,
        loadCurrentPlayer,
        loadLeaderboard,
        loadUpcomingItems,
    ]);

    const leaderboardPlayer = useMemo(
        () =>
            leaderboard.find(
                (player) =>
                    (player.userId || player.uid || player.id) ===
                    currentUser?.uid,
            ) || null,
        [leaderboard, currentUser?.uid],
    );

    const displayedPlayer = useMemo(
        () => ({
            ...(currentPlayer || {}),
            rank:
                leaderboardPlayer?.rank ??
                currentPlayer?.rank ??
                null,
        }),
        [currentPlayer, leaderboardPlayer],
    );

    const firstName =
        currentUser?.displayName?.trim() ||
        currentUser?.email?.split("@")[0] ||
        "player";

    async function handleLogout() {
        try {
            setCurrentPlayer(null);
            setLeaderboard([]);
            setPlayerError("");
            setLeaderboardError("");
            setUpcomingItems([]);
            setUpcomingError("");

            await logout();
            navigate("/login", { replace: true });
        } catch (error) {
            console.error("Unable to log out:", error);
        }
    }

    return (
        <main className="home-page">
            <aside
                className={`home-sidebar ${isSidebarCollapsed ? "home-sidebar--collapsed" : ""}`}
                aria-label="Main navigation"
            >
                <div className="home-sidebar__topbar">
                    <button
                        type="button"
                        className="home-sidebar__brand"
                        onClick={() => navigate("/")}
                        aria-label="Go to homepage"
                    >
                        <img src={kotaroImage} alt="" />
                        <span className="home-sidebar__label">The Nowl</span>
                    </button>

                    <button
                        type="button"
                        className="home-sidebar__toggle"
                        onClick={() => setIsSidebarCollapsed((current) => !current)}
                        aria-label={isSidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
                        aria-expanded={!isSidebarCollapsed}
                    >
                        <span aria-hidden="true">{isSidebarCollapsed ? "›" : "‹"}</span>
                    </button>
                </div>

                <nav className="home-sidebar__nav">
                    <a href="#overview" title="Overview">
                        <span className="home-sidebar__icon" aria-hidden="true">⌂</span>
                        <span className="home-sidebar__label">Overview</span>
                    </a>
                    <a href="#useful-tools" title="Useful tools">
                        <span className="home-sidebar__icon" aria-hidden="true">▦</span>
                        <span className="home-sidebar__label">Useful tools</span>
                    </a>
                    <a href="#games" title="Games">
                        <span className="home-sidebar__icon" aria-hidden="true">◆</span>
                        <span className="home-sidebar__label">Games</span>
                    </a>
                    <a href="#leaderboard" title="Leaderboard">
                        <span className="home-sidebar__icon" aria-hidden="true">★</span>
                        <span className="home-sidebar__label">Leaderboard</span>
                    </a>
                </nav>

                <div className="home-sidebar__quick-links">
                    <p className="home-sidebar__label">Quick access</p>
                    <button type="button" onClick={() => navigate("/calendar")} title="Calendar">
                        <span className="home-sidebar__icon" aria-hidden="true">▣</span>
                        <span className="home-sidebar__label">Calendar</span>
                        <span className="home-sidebar__label" aria-hidden="true">→</span>
                    </button>
                    <button type="button" onClick={() => navigate("/task-boards")} title="Task boards">
                        <span className="home-sidebar__icon" aria-hidden="true">☷</span>
                        <span className="home-sidebar__label">Task boards</span>
                        <span className="home-sidebar__label" aria-hidden="true">→</span>
                    </button>
                </div>
            </aside>

            <div className={`home-shell ${isSidebarCollapsed ? "home-shell--sidebar-collapsed" : ""}`} id="overview">
                <header className="home-nav">
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

                    <button
                        type="button"
                        onClick={handleLogout}
                        className="home-logout-button"
                    >
                        Log out
                    </button>
                </header>


                <section className="useful-tools-section" id="useful-tools">
                    <div className="useful-tools-section__header">
                        <div>
                            <p className="home-section-label">At a glance</p>
                            <h2>Useful tools</h2>
                            <p>
                                Check what matters now, then jump straight into
                                the full tool when you need more detail.
                            </p>
                        </div>

                        <div className="tool-carousel__controls">
                            <button
                                type="button"
                                onClick={showPreviousTool}
                                aria-label="Show previous tool"
                            >
                                ←
                            </button>
                            <button
                                type="button"
                                onClick={showNextTool}
                                aria-label="Show next tool"
                            >
                                →
                            </button>
                        </div>
                    </div>

                    <div className="tool-carousel__tabs" role="tablist" aria-label="Useful tools">
                        {usefulTools.map((tool, index) => (
                            <button
                                type="button"
                                role="tab"
                                aria-selected={activeToolIndex === index}
                                className={activeToolIndex === index ? "is-active" : ""}
                                key={tool.id}
                                onClick={() => setActiveToolIndex(index)}
                            >
                                {tool.label}
                            </button>
                        ))}
                    </div>

                    <div className="tool-carousel" aria-live="polite">
                        {activeToolIndex === 0 && (
                            <article className="tool-slide tool-slide--calendar">
                                <div className="tool-slide__topline">
                                    <div>
                                        <span className="tool-slide__icon" aria-hidden="true">▦</span>
                                        <div>
                                            <p>Calendar</p>
                                            <h3>Your next seven days</h3>
                                        </div>
                                    </div>

                                    <div className="tool-slide__actions">
                                        <button
                                            type="button"
                                            className="button button--ghost"
                                            onClick={() => loadUpcomingItems()}
                                            disabled={upcomingStatus === "loading"}
                                        >
                                            {upcomingStatus === "loading" ? "Loading..." : "Refresh"}
                                        </button>
                                        <button
                                            type="button"
                                            className="button button--primary"
                                            onClick={() => navigate("/calendar")}
                                        >
                                            Open calendar
                                        </button>
                                    </div>
                                </div>

                                {upcomingStatus === "loading" && (
                                    <div className="upcoming-state">Loading your week...</div>
                                )}

                                {upcomingStatus === "error" && (
                                    <div className="upcoming-state upcoming-state--error">
                                        {upcomingError}
                                    </div>
                                )}

                                {upcomingStatus === "success" && upcomingItems.length === 0 && (
                                    <div className="upcoming-empty">
                                        <div className="upcoming-empty__icon" aria-hidden="true">✓</div>
                                        <div>
                                            <strong>Your week is clear.</strong>
                                            <span>Add an event, task, or reminder from the calendar.</span>
                                        </div>
                                    </div>
                                )}

                                {upcomingStatus === "success" && upcomingItems.length > 0 && (
                                    <div className="tool-calendar-list">
                                        {upcomingItems.slice(0, 4).map((item) => (
                                            <button
                                                type="button"
                                                className={`upcoming-item upcoming-item--${item.itemType}`}
                                                key={item.id}
                                                onClick={() => navigate("/calendar")}
                                            >
                                                <span className="upcoming-item__date-block" aria-hidden="true">
                                                    <strong>{getUpcomingDay(item)}</strong>
                                                    <span>{getUpcomingMonth(item)}</span>
                                                </span>
                                                <span className="upcoming-item__content">
                                                    <span className="upcoming-item__topline">
                                                        <strong>{item.title}</strong>
                                                        <span className={`upcoming-item__type upcoming-item__type--${item.itemType}`}>
                                                            {item.itemType}
                                                        </span>
                                                    </span>
                                                    <span className="upcoming-item__date">
                                                        {formatUpcomingDate(item)}
                                                    </span>
                                                </span>
                                                <span className="upcoming-item__arrow" aria-hidden="true">→</span>
                                            </button>
                                        ))}
                                    </div>
                                )}
                            </article>
                        )}

                        {/*{activeToolIndex === 1 && (
                            <article className="tool-slide tool-slide--headlines">
                                <div className="tool-slide__topline">
                                    <div>
                                        <span className="tool-slide__icon" aria-hidden="true">◫</span>
                                        <div>
                                            <p>Today’s headlines</p>
                                            <h3>Your daily news briefing</h3>
                                        </div>
                                    </div>
                                    <span className="tool-status-pill">Coming soon</span>
                                </div>

                                <div className="headline-preview-list">
                                    {[
                                        "Top stories selected for the family",
                                        "Short summaries without the clutter",
                                        "Open the full article when something matters",
                                    ].map((headline, index) => (
                                        <div className="headline-preview" key={headline}>
                                            <span>{String(index + 1).padStart(2, "0")}</span>
                                            <strong>{headline}</strong>
                                        </div>
                                    ))}
                                </div>
                            </article>
                        )}

                        {activeToolIndex === 2 && (
                            <article className="tool-slide tool-slide--boards">
                                <div className="tool-slide__topline">
                                    <div>
                                        <span className="tool-slide__icon" aria-hidden="true">▤</span>
                                        <div>
                                            <p>Task boards</p>
                                            <h3>Keep shared work moving</h3>
                                        </div>
                                    </div>
                                    <button
                                        type="button"
                                        className="button button--primary"
                                        onClick={() => navigate("/task-boards")}
                                    >
                                        Go to task boards
                                    </button>
                                </div>

                                <div className="board-preview">
                                    {[
                                        ["Backlog", "Plan birthday dinner", "3 cards"],
                                        ["In progress", "Book family trip", "2 cards"],
                                        ["Completed", "Update shared calendar", "5 cards"],
                                    ].map(([title, task, count]) => (
                                        <div className="board-preview__column" key={title}>
                                            <div>
                                                <strong>{title}</strong>
                                                <span>{count}</span>
                                            </div>
                                            <p>{task}</p>
                                        </div>
                                    ))}
                                </div>
                            </article>
                        )}*/}
                    </div>

                    <div className="tool-carousel__dots" aria-label="Choose useful tool">
                        {usefulTools.map((tool, index) => (
                            <button
                                type="button"
                                key={tool.id}
                                className={activeToolIndex === index ? "is-active" : ""}
                                aria-label={`Show ${tool.label}`}
                                onClick={() => setActiveToolIndex(index)}
                            />
                        ))}
                    </div>
                </section>

                <section className="home-hero" id="games">
                    <div className="home-hero__copy">
                        <p className="home-eyebrow">Game corner</p>
                        <h1>Welcome back, {firstName}.</h1>
                        <p className="home-hero__text">
                            Take on today&apos;s challenge and keep your combo alive.
                        </p>

                        <div className="home-hero__actions">
                            <button
                                type="button"
                                className="button button--primary"
                                onClick={() => navigate("/wordle-ranked")}
                            >
                                Play today&apos;s Wordle
                            </button>

                            <a
                                className="button button--ghost"
                                href="#leaderboard"
                            >
                                View leaderboard
                            </a>
                        </div>
                    </div>

                    <div
                        className="home-hero__mascots"
                        aria-hidden="true"
                    >
                        <span className="mascot-bubble mascot-bubble--left">
                            It&apos;s all water bro
                        </span>
                        <img
                            className="home-mascot home-mascot--haru"
                            src={haruImage}
                            alt=""
                        />
                        <img
                            className="home-mascot home-mascot--kotaro"
                            src={kotaroImage}
                            alt=""
                        />
                        <span className="mascot-bubble mascot-bubble--right">
                            Nah ur fat ass shi
                        </span>
                    </div>
                </section>

                {playerStatus === "error" && (
                    <div className="leaderboard-state leaderboard-state--error">
                        {playerError}
                    </div>
                )}

                <section
                    className="home-summary-grid"
                    aria-busy={playerStatus === "loading"}
                >
                    <article className="summary-card summary-card--score">
                        <span>Ranked score</span>
                        <strong>
                            {playerStatus === "loading"
                                ? "…"
                                : displayedPlayer.rankScore ?? 0}
                        </strong>
                        <small>Keep playing to earn more points.</small>
                    </article>

                    <article className="summary-card">
                        <span>Your rank</span>
                        <strong>
                            {playerStatus === "loading"
                                ? "…"
                                : displayedPlayer.rank
                                  ? `#${displayedPlayer.rank}`
                                  : "—"}
                        </strong>
                        <small>
                            {displayedPlayer.rank
                                ? "Among all players"
                                : "Play once to be ranked"}
                        </small>
                    </article>

                    <article className="summary-card">
                        <span>Current combo</span>
                        <strong>
                            {playerStatus === "loading"
                                ? "…"
                                : `×${displayedPlayer.combo ?? 0}`}
                        </strong>
                        <small>Win daily to build your streak.</small>
                    </article>

                    <article className="summary-card">
                        <span>Total wins</span>
                        <strong>
                            {playerStatus === "loading"
                                ? "…"
                                : displayedPlayer.wins ?? 0}
                        </strong>
                        <small>Every correct word counts.</small>
                    </article>
                </section>

                <section className="home-content-grid">
                    <article className="game-card">
                        <div className="game-card__topline">
                            <span className="pill">Available now</span>
                            <span className="game-card__difficulty">
                                Daily challenge
                            </span>
                        </div>

                        <div className="game-card__body">
                            <div>
                                <p className="home-section-label">
                                    Featured game
                                </p>
                                <h2>Wordle Ranked</h2>
                                <p>
                                    Guess the five-letter word in six attempts.
                                    Faster solves earn more ranked points.
                                </p>
                            </div>

                            <div className="mini-wordle" aria-hidden="true">
                                {["N", "O", "W", "L", "S"].map(
                                    (letter, index) => (
                                        <span
                                            key={`${letter}-${index}`}
                                            className={`mini-wordle__tile mini-wordle__tile--${index + 1}`}
                                        >
                                            {letter}
                                        </span>
                                    ),
                                )}
                            </div>
                        </div>

                        <button
                            type="button"
                            className="button button--primary game-card__button"
                            onClick={() => navigate("/wordle-ranked")}
                        >
                            Start game <span aria-hidden="true">→</span>
                        </button>
                    </article>

                    <aside className="coming-soon-card">
                            <img
                                className="coming-soon-mascot"
                                src={haruImage}
                                alt="Haru, the orange cat mascot"
                            />

                        <div>
                            <p className="home-section-label">
                                More on the way
                            </p>
                            <h2>New games soon</h2>
                            <p>
                                Haru&apos;s fat ass is blocking the way, so
                                gimme some time to move him.
                            </p>
                        </div>
                    </aside>
                </section>

                <section
                    className="leaderboard-card"
                    id="leaderboard"
                >
                    <div className="leaderboard-header">
                        <div>
                            <p className="home-section-label">
                                Wordle Ranked
                            </p>
                            <h2>Leaderboard</h2>
                        </div>

                        <button
                            type="button"
                            className="leaderboard-refresh-button"
                            onClick={refreshHomeData}
                            disabled={
                                leaderboardStatus === "loading" ||
                                playerStatus === "loading"
                            }
                        >
                            {leaderboardStatus === "loading"
                                ? "Loading..."
                                : "Refresh"}
                        </button>
                    </div>

                    {leaderboardStatus === "loading" && (
                        <div className="leaderboard-state">
                            Loading leaderboard...
                        </div>
                    )}

                    {leaderboardStatus === "error" && (
                        <div className="leaderboard-state leaderboard-state--error">
                            {leaderboardError}
                        </div>
                    )}

                    {leaderboardStatus === "success" &&
                        leaderboard.length === 0 && (
                            <div className="leaderboard-state">
                                No ranked players yet.
                            </div>
                        )}

                    {leaderboardStatus === "success" &&
                        leaderboard.length > 0 && (
                            <div className="leaderboard-table-wrapper">
                                <table className="leaderboard-table">
                                    <thead>
                                        <tr>
                                            <th>Rank</th>
                                            <th>Player</th>
                                            <th>Points</th>
                                            <th>Wins</th>
                                            <th>Win rate</th>
                                        </tr>
                                    </thead>

                                    <tbody>
                                        {leaderboard.map((player) => {
                                            const playerUid =
                                                player.userId ||
                                                player.uid ||
                                                player.id;

                                            const isCurrentPlayer =
                                                Boolean(currentUser?.uid) &&
                                                playerUid === currentUser.uid;

                                            return (
                                                <tr
                                                    key={playerUid}
                                                    className={
                                                        isCurrentPlayer
                                                            ? "leaderboard-row--current"
                                                            : ""
                                                    }
                                                >
                                                    <td>
                                                        <span className="leaderboard-rank">
                                                            {player.rank <= 3
                                                                ? ["🥇", "🥈", "🥉"][
                                                                      player.rank - 1
                                                                  ]
                                                                : `#${player.rank}`}
                                                        </span>
                                                    </td>

                                                    <td>
                                                        <div className="leaderboard-player">
                                                            <span className="leaderboard-avatar">
                                                                <span
                                                                    className="leaderboard-avatar__fallback"
                                                                    aria-hidden="true"
                                                                >
                                                                    {player.displayName
                                                                        ?.charAt(0)
                                                                        .toUpperCase() ||
                                                                        "?"}
                                                                </span>

                                                                {player.profilePicLink && (
                                                                    <img
                                                                        src={player.profilePicLink}
                                                                        alt={`${player.displayName || "Player"} profile`}
                                                                        className="leaderboard-avatar__image"
                                                                        loading="lazy"
                                                                        referrerPolicy="no-referrer"
                                                                        onError={(event) => {
                                                                            event.currentTarget.style.display = "none";
                                                                        }}
                                                                    />
                                                                )}
                                                            </span>

                                                            <div>
                                                                <strong>
                                                                    {
                                                                        player.displayName
                                                                    }
                                                                </strong>
                                                                {isCurrentPlayer && (
                                                                    <span>
                                                                        You
                                                                    </span>
                                                                )}
                                                            </div>
                                                        </div>
                                                    </td>

                                                    <td>
                                                        <strong>
                                                            {player.rankScore}
                                                        </strong>
                                                    </td>
                                                    <td>{player.wins}</td>
                                                    <td>{player.winRate}%</td>
                                                </tr>
                                            );
                                        })}
                                    </tbody>
                                </table>
                            </div>
                        )}
                </section>
            </div>
        </main>
    );
}

export default HomePage;