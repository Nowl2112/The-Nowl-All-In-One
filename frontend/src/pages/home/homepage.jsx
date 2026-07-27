import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../../context/authContext.jsx";
import haruImage from "../../assets/haru.png";
import kotaroImage from "../../assets/kotaro.png";
import "./homepage.css";

const API_BASE_URL =
    import.meta.env.VITE_API_BASE_URL ||
    import.meta.env.VITE_API_BASE ||
    "http://localhost:5000";

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

    const refreshHomeData = useCallback(async () => {
        await Promise.all([
            loadCurrentPlayer(),
            loadLeaderboard(),
        ]);
    }, [loadCurrentPlayer, loadLeaderboard]);

    useEffect(() => {
        if (!currentUser?.uid) {
            setCurrentPlayer(null);
            setLeaderboard([]);
            return undefined;
        }

        const controller = new AbortController();

        Promise.all([
            loadCurrentPlayer(controller.signal),
            loadLeaderboard(controller.signal),
        ]);

        return () => controller.abort();
    }, [
        currentUser?.uid,
        loadCurrentPlayer,
        loadLeaderboard,
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

            await logout();
            navigate("/login", { replace: true });
        } catch (error) {
            console.error("Unable to log out:", error);
        }
    }

    return (
        <main className="home-page">
            <div className="home-shell">
                <header className="home-nav">
                    <button
                        type="button"
                        className="brand-mark"
                        onClick={() => navigate("/")}
                        aria-label="Go to homepage"
                    >
                        <span className="brand-mark__badge">N</span>
                        <span>
                            <strong>The Nowl In One</strong>
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

                <section className="home-hero">
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
                        <div className="coming-soon-card__mascot-wrap">
                            <img
                                src={haruImage}
                                alt="Haru, the orange cat mascot"
                            />
                        </div>

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
                                                                {player.displayName
                                                                    ?.charAt(0)
                                                                    .toUpperCase() ||
                                                                    "?"}
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