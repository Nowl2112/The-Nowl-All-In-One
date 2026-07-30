import React, { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../../context/authContext.jsx";
import kotaroImage from "../../assets/kotaro.png";
import haruDetectiveImage from "../../assets/haru-detective.png";
import "./riddlespage.css";

const API_BASE_URL =
    import.meta.env.VITE_API_BASE_URL ||
    import.meta.env.VITE_API_BASE ||
    "";

async function readJson(response) {
    const contentType = response.headers.get("content-type") || "";
    if (!contentType.includes("application/json")) {
        throw new Error(`Unexpected server response (${response.status}).`);
    }
    return response.json();
}

function RiddlesPage() {
    const navigate = useNavigate();
    const { currentUser } = useAuth();
    const [daily, setDaily] = useState(null);
    const [playerStats, setPlayerStats] = useState(null);
    const [savedRiddles, setSavedRiddles] = useState([]);
    const [guess, setGuess] = useState("");
    const [status, setStatus] = useState("loading");
    const [savedStatus, setSavedStatus] = useState("loading");
    const [message, setMessage] = useState("");
    const [error, setError] = useState("");
    const [isSubmitting, setIsSubmitting] = useState(false);

    const getHeaders = useCallback(
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

    const loadToday = useCallback(async (signal) => {
        setStatus("loading");
        setError("");
        try {
            const response = await fetch(
                `${API_BASE_URL}/api/games/riddles/today`,
                { headers: await getHeaders(), signal, cache: "no-store" },
            );
            const data = await readJson(response);
            if (!response.ok) throw new Error(data.error || "Could not load today's riddle.");
            setDaily(data);
            setStatus("success");
        } catch (requestError) {
            if (requestError?.name === "AbortError") return;
            setError(requestError?.message || "Could not load today's riddle.");
            setStatus("error");
        }
    }, [getHeaders]);

    const loadSaved = useCallback(async (signal) => {
        setSavedStatus("loading");
        try {
            const response = await fetch(
                `${API_BASE_URL}/api/games/riddles/saved`,
                { headers: await getHeaders(), signal, cache: "no-store" },
            );
            const data = await readJson(response);
            if (!response.ok) throw new Error(data.error || "Could not load saved riddles.");
            setSavedRiddles(Array.isArray(data.riddles) ? data.riddles : []);
            setSavedStatus("success");
        } catch (requestError) {
            if (requestError?.name === "AbortError") return;
            setSavedStatus("error");
        }
    }, [getHeaders]);

    const loadPlayerStats = useCallback(async (signal) => {
        try {
            const response = await fetch(
                `${API_BASE_URL}/api/games/riddles/me`,
                { headers: await getHeaders(), signal, cache: "no-store" },
            );
            const data = await readJson(response);
            if (response.ok) setPlayerStats(data.player || null);
        } catch (requestError) {
            if (requestError?.name !== "AbortError") {
                console.error("Unable to load riddle statistics:", requestError);
            }
        }
    }, [getHeaders]);

    useEffect(() => {
        if (!currentUser?.uid) return undefined;
        const controller = new AbortController();
        loadToday(controller.signal);
        loadSaved(controller.signal);
        loadPlayerStats(controller.signal);
        return () => controller.abort();
    }, [currentUser?.uid, loadToday, loadSaved, loadPlayerStats]);

    async function submitGuess(event) {
        event.preventDefault();
        if (!guess.trim() || isSubmitting || daily?.game?.status !== "playing") return;

        setIsSubmitting(true);
        setMessage("");
        setError("");
        try {
            const response = await fetch(`${API_BASE_URL}/api/games/riddles/guess`, {
                method: "POST",
                headers: await getHeaders(true),
                body: JSON.stringify({ guess: guess.trim() }),
            });
            const data = await readJson(response);
            if (!response.ok) throw new Error(data.error || "Could not submit your answer.");

            setGuess("");
            setMessage(
                data.correct
                    ? `Correct! You earned ${data.pointsGained} points.`
                    : data.status === "lost"
                      ? `The answer was “${data.answer}”.`
                      : `Not quite. ${data.attemptsRemaining} attempts remaining.`,
            );
            await loadToday();
            await loadPlayerStats();
        } catch (requestError) {
            setError(requestError?.message || "Could not submit your answer.");
        } finally {
            setIsSubmitting(false);
        }
    }

    async function revealHint() {
        setMessage("");
        setError("");
        try {
            const response = await fetch(`${API_BASE_URL}/api/games/riddles/hint`, {
                method: "POST",
                headers: await getHeaders(true),
                body: "{}",
            });
            const data = await readJson(response);
            if (!response.ok) throw new Error(data.error || "Could not reveal a hint.");
            setDaily((current) => ({
                ...current,
                game: { ...current.game, hint: data.hint, hintsUsed: data.hintsUsed },
            }));
        } catch (requestError) {
            setError(requestError?.message || "Could not reveal a hint.");
        }
    }

    async function toggleSaved() {
        const riddle = daily?.riddle;
        if (!riddle?.id) return;
        setError("");
        try {
            const isSaved = Boolean(daily.game?.saved);
            const response = await fetch(
                `${API_BASE_URL}/api/games/riddles/saved${isSaved ? `/${encodeURIComponent(riddle.id)}` : ""}`,
                {
                    method: isSaved ? "DELETE" : "POST",
                    headers: await getHeaders(!isSaved),
                    ...(!isSaved ? { body: JSON.stringify({ riddleId: riddle.id }) } : {}),
                },
            );
            if (!response.ok) {
                const data = await readJson(response);
                throw new Error(data.error || "Could not update saved riddles.");
            }
            setDaily((current) => ({
                ...current,
                game: { ...current.game, saved: !isSaved },
            }));
            await loadSaved();
        } catch (requestError) {
            setError(requestError?.message || "Could not update saved riddles.");
        }
    }

    async function removeSaved(riddleId) {
        try {
            const response = await fetch(
                `${API_BASE_URL}/api/games/riddles/saved/${encodeURIComponent(riddleId)}`,
                { method: "DELETE", headers: await getHeaders() },
            );
            if (!response.ok) throw new Error("Could not remove saved riddle.");
            setSavedRiddles((current) => current.filter((item) => item.id !== riddleId));
            if (daily?.riddle?.id === riddleId) {
                setDaily((current) => ({
                    ...current,
                    game: { ...current.game, saved: false },
                }));
            }
        } catch (requestError) {
            setError(requestError?.message || "Could not remove saved riddle.");
        }
    }

    const game = daily?.game;
    const riddle = daily?.riddle;
    const finished = game?.status === "won" || game?.status === "lost";

    return (
        <main className="riddles-page">
            <div className="riddles-shell">
                <header className="riddles-nav">
                    <button type="button" className="riddles-brand" onClick={() => navigate("/")}>
                        <img src={kotaroImage} alt="Kotaro" />
                        <span>The Nowl In One</span>
                    </button>
                    <button type="button" className="riddles-back" onClick={() => navigate("/")}>
                        ← Home
                    </button>
                </header>

                <section className="riddles-hero">
                    <div className="riddles-hero__copy">
                        <p className="riddles-eyebrow">One new puzzle every day</p>
                        <h1>Daily Riddle</h1>
                        <p>Detective Haru has a new mystery for you. Think carefully, use a hint if you need one, and climb both leaderboards.</p>
                    </div>
                    <div className="riddles-hero__mascot" aria-label="Detective Haru">
                        <img src={haruDetectiveImage} alt="Haru dressed as a detective" />
                        <span>Can you crack today&apos;s riddle?</span>
                    </div>
                    <div className="riddles-score">
                        <span>Your all-time score</span>
                        <strong>{playerStats?.allTimeScore ?? playerStats?.lifetimePoints ?? 0}</strong>
                        <span className="riddles-score__monthly">
                            30-day score: {playerStats?.monthlyScore ?? playerStats?.rollingScore ?? 0}
                        </span>
                        <small>{playerStats?.wins ?? daily?.stats?.wins ?? 0} riddles solved</small>
                    </div>
                </section>

                {status === "loading" && <div className="riddle-state">Loading today&apos;s riddle...</div>}
                {status === "error" && <div className="riddle-state riddle-state--error">{error}</div>}

                {status === "success" && riddle && (
                    <section className="riddle-card">
                        <div className="riddle-card__topline">
                            <div>
                                <span className="riddle-chip">{riddle.difficulty || "Daily"}</span>
                                <span className="riddle-date">{riddle.date}</span>
                            </div>
                            <button type="button" className="riddle-save" onClick={toggleSaved}>
                                {game?.saved ? "★ Saved" : "☆ Save riddle"}
                            </button>
                        </div>

                        <p className="riddle-question">{riddle.question}</p>

                        {game?.hint && <div className="riddle-hint"><strong>Hint:</strong> {game.hint}</div>}
                        {finished && riddle.answer && (
                            <div className={`riddle-result riddle-result--${game.status}`}>
                                <strong>{game.status === "won" ? "Solved!" : "Answer"}</strong>
                                <span>{riddle.answer}</span>
                                {game.status === "won" && <small>+{game.pointsGained} points</small>}
                            </div>
                        )}

                        {!finished && (
                            <form className="riddle-answer-form" onSubmit={submitGuess}>
                                <label htmlFor="riddle-answer">Your answer</label>
                                <div>
                                    <input
                                        id="riddle-answer"
                                        value={guess}
                                        onChange={(event) => setGuess(event.target.value)}
                                        maxLength={120}
                                        placeholder="Type your answer..."
                                        autoComplete="off"
                                    />
                                    <button type="submit" disabled={!guess.trim() || isSubmitting}>
                                        {isSubmitting ? "Checking..." : "Submit"}
                                    </button>
                                </div>
                            </form>
                        )}

                        <div className="riddle-card__footer">
                            <span>{game?.attemptsRemaining ?? 0} attempts remaining</span>
                            {!finished && !game?.hint && (
                                <button type="button" onClick={revealHint}>Reveal hint (−15 points)</button>
                            )}
                        </div>
                        {message && <p className="riddle-message">{message}</p>}
                        {error && status !== "error" && <p className="riddle-message riddle-message--error">{error}</p>}
                    </section>
                )}

                <section className="saved-riddles">
                    <div className="saved-riddles__header">
                        <div>
                            <p className="riddles-eyebrow">Your collection</p>
                            <h2>Saved riddles</h2>
                        </div>
                        <span>{savedRiddles.length} saved</span>
                    </div>

                    {savedStatus === "loading" && <div className="riddle-state">Loading saved riddles...</div>}
                    {savedStatus === "error" && <div className="riddle-state">Saved riddles could not be loaded.</div>}
                    {savedStatus === "success" && savedRiddles.length === 0 && (
                        <div className="saved-riddles__empty">Save a riddle you enjoy and it will appear here.</div>
                    )}
                    {savedStatus === "success" && savedRiddles.length > 0 && (
                        <div className="saved-riddles__grid">
                            {savedRiddles.map((item) => (
                                <article key={item.id} className="saved-riddle-card">
                                    <span>{item.date}</span>
                                    <h3>{item.question}</h3>
                                    <p><strong>Answer:</strong> {item.answer}</p>
                                    <button type="button" onClick={() => removeSaved(item.id)}>Remove</button>
                                </article>
                            ))}
                        </div>
                    )}
                </section>
            </div>
        </main>
    );
}

export default RiddlesPage;