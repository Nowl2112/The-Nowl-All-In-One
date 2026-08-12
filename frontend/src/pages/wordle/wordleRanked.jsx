import React, {
    useCallback,
    useEffect,
    useMemo,
    useState,
} from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../../context/authContext.jsx";
import kotaroImage from "../../assets/kotaro.png";
import "./wordleRanked.css";

const WORD_LENGTH = 5;
const MAX_ATTEMPTS = 6;
const API_BASE_URL =
    import.meta.env.VITE_API_BASE_URL ||
    import.meta.env.VITE_API_BASE ||
    "";

const KEYBOARD_ROWS = [
    ["Q", "W", "E", "R", "T", "Y", "U", "I", "O", "P"],
    ["A", "S", "D", "F", "G", "H", "J", "K", "L"],
    ["ENTER", "Z", "X", "C", "V", "B", "N", "M", "BACKSPACE"],
];

const EMPTY_STATS = {
    rankScore: 0,
    combo: 0,
    wins: 0,
    gamesPlayed: 0,
    bestCombo: 0,
};

const REVEAL_STEP_MS = 150;
const REVEAL_DURATION_MS = 600;
const INVALID_SHAKE_MS = 450;

function wait(milliseconds) {
    return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

function WordleRanked() {
    const navigate = useNavigate();
    const { currentUser, authLoading } = useAuth();

    const [guesses, setGuesses] = useState([]);
    const [evaluations, setEvaluations] = useState([]);
    const [currentGuess, setCurrentGuess] = useState("");
    const [message, setMessage] = useState("");
    const [gameStatus, setGameStatus] = useState("loading");
    const [stats, setStats] = useState(EMPTY_STATS);
    const [answer, setAnswer] = useState("");
    const [isSubmitting, setIsSubmitting] = useState(false);
    const [revealingRow, setRevealingRow] = useState(-1);
    const [shakingRow, setShakingRow] = useState(-1);
    const [pressedKey, setPressedKey] = useState("");
    const [winningRow, setWinningRow] = useState(-1);
    const [showConfetti, setShowConfetti] = useState(false);
    const [statsPulse, setStatsPulse] = useState(0);
    const [visibleGuessCount, setVisibleGuessCount] = useState(0);

    useEffect(() => {
        if (authLoading) {
            return undefined;
        }

        if (!currentUser) {
            setGameStatus("error");
            setMessage("Please log in before playing ranked Wordle.");
            navigate("/login", { replace: true });
            return undefined;
        }

        const controller = new AbortController();

        async function loadGame() {
            setGameStatus("loading");
            setMessage("");

            try {
                const token = await currentUser.getIdToken();

                const response = await fetch(
                    `${API_BASE_URL}/api/games/wordle`,
                    {
                        method: "GET",
                        headers: {
                            Authorization: `Bearer ${token}`,
                        },
                        signal: controller.signal,
                        cache: "no-store",
                    },
                );

                const data = await response.json();

                if (!response.ok) {
                    throw new Error(data.error || "Could not load Wordle.");
                }

                setGuesses(Array.isArray(data.guesses) ? data.guesses : []);
                setVisibleGuessCount(
                    Array.isArray(data.guesses) ? data.guesses.length : 0,
                );
                setEvaluations(
                    Array.isArray(data.evaluations) ? data.evaluations : [],
                );
                setStats(data.stats || EMPTY_STATS);
                setGameStatus(data.status || "playing");
                setAnswer(data.answer || "");

                if (data.status === "won") {
                    setMessage("You already completed today's Wordle.");
                } else if (data.status === "lost") {
                    setMessage(`Today's word was ${data.answer}.`);
                } else {
                    setMessage("");
                }
            } catch (error) {
                if (error?.name === "AbortError") {
                    return;
                }

                console.error("Unable to load Wordle:", error);
                setGameStatus("error");
                setMessage(error?.message || "Could not load Wordle.");
            }
        }

        void loadGame();

        return () => controller.abort();
    }, [authLoading, currentUser, navigate]);

    const keyboardStatuses = useMemo(() => {
        const statuses = {};
        const priority = {
            absent: 1,
            present: 2,
            correct: 3,
        };

        guesses.slice(0, visibleGuessCount).forEach((guess, guessIndex) => {
            const result = evaluations[guessIndex] || [];

            guess.split("").forEach((letter, letterIndex) => {
                const nextStatus = result[letterIndex];
                const currentStatus = statuses[letter];

                if (
                    nextStatus &&
                    (!currentStatus ||
                        priority[nextStatus] > priority[currentStatus])
                ) {
                    statuses[letter] = nextStatus;
                }
            });
        });

        return statuses;
    }, [evaluations, guesses, visibleGuessCount]);

    const submitGuess = useCallback(async () => {
        if (
            gameStatus !== "playing" ||
            isSubmitting ||
            !currentUser
        ) {
            return;
        }

        if (currentGuess.length !== WORD_LENGTH) {
            setMessage("Enter a five-letter word.");
            return;
        }

        setIsSubmitting(true);
        setMessage("");

        try {
            const token = await currentUser.getIdToken();

            const response = await fetch(
                `${API_BASE_URL}/api/games/wordle/guess`,
                {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        Authorization: `Bearer ${token}`,
                    },
                    body: JSON.stringify({
                        guess: currentGuess,
                    }),
                    cache: "no-store",
                },
            );

            const data = await response.json();

            if (!response.ok) {
                setMessage(data.error || "Could not submit guess.");

                // Invalid dictionary words do not consume an attempt.
                // Shake first, then clear the row for the next word.
                if (data.code === "invalid_word") {
                    setShakingRow(guesses.length);
                    await wait(INVALID_SHAKE_MS);
                    setShakingRow(-1);
                    setCurrentGuess("");
                }
                return;
            }

            const completedRow = guesses.length;
            setGuesses((previous) => [...previous, data.guess]);
            setEvaluations((previous) => [
                ...previous,
                data.evaluation,
            ]);
            setCurrentGuess("");
            setRevealingRow(completedRow);

            await wait(
                REVEAL_DURATION_MS +
                    (WORD_LENGTH - 1) * REVEAL_STEP_MS,
            );

            setRevealingRow(-1);
            setVisibleGuessCount(completedRow + 1);
            setStats(data.stats);
            setGameStatus(data.status);
            setAnswer(data.answer || "");
            setStatsPulse((previous) => previous + 1);

            if (data.status === "won") {
                setWinningRow(completedRow);
                setShowConfetti(true);
                setMessage(
                    `Correct! You gained ${data.pointsGained} ranked points.`,
                );
                window.setTimeout(() => setShowConfetti(false), 2400);
            } else if (data.status === "lost") {
                setMessage(`The word was ${data.answer}. Your combo was reset.`);
            }
        } catch (error) {
            console.error(error);
            setMessage("Could not reach the game server.");
        } finally {
            setIsSubmitting(false);
        }
    }, [currentGuess, currentUser, gameStatus, guesses.length, isSubmitting]);

    const handleKey = useCallback(
        (key) => {
            if (gameStatus !== "playing" || isSubmitting) {
                return;
            }

            setPressedKey(key);
            window.setTimeout(() => {
                setPressedKey((current) => (current === key ? "" : current));
            }, 140);

            if (key === "ENTER") {
                void submitGuess();
                return;
            }

            if (key === "BACKSPACE") {
                setCurrentGuess((previous) => previous.slice(0, -1));
                setMessage("");
                return;
            }

            if (/^[A-Z]$/.test(key) && currentGuess.length < WORD_LENGTH) {
                setCurrentGuess((previous) => previous + key);
                setMessage("");
            }
        },
        [currentGuess.length, gameStatus, isSubmitting, submitGuess],
    );

    useEffect(() => {
        function handlePhysicalKeyboard(event) {
            const key = event.key.toUpperCase();

            if (key === "ENTER") {
                handleKey("ENTER");
            } else if (key === "BACKSPACE") {
                handleKey("BACKSPACE");
            } else if (/^[A-Z]$/.test(key)) {
                handleKey(key);
            }
        }

        window.addEventListener("keydown", handlePhysicalKeyboard);
        return () => window.removeEventListener("keydown", handlePhysicalKeyboard);
    }, [handleKey]);

    function renderBoardRow(rowIndex) {
        const submittedGuess = guesses[rowIndex];
        const isCurrentRow =
            rowIndex === guesses.length && gameStatus === "playing";

        const rowValue = submittedGuess
            ? submittedGuess
            : isCurrentRow
              ? currentGuess.padEnd(WORD_LENGTH, " ")
              : " ".repeat(WORD_LENGTH);

        const statuses = submittedGuess
            ? evaluations[rowIndex] || Array(WORD_LENGTH).fill("")
            : Array(WORD_LENGTH).fill("");

        return (
            <div
                className={[
                    "wordle-row",
                    shakingRow === rowIndex ? "wordle-row--shake" : "",
                    winningRow === rowIndex ? "wordle-row--win" : "",
                ]
                    .filter(Boolean)
                    .join(" ")}
                key={rowIndex}
            >
                {rowValue.split("").map((letter, columnIndex) => {
                    const status = statuses[columnIndex];

                    return (
                        <div
                            className={[
                                "wordle-tile",
                                status ? `wordle-tile--${status}` : "",
                                letter.trim() && !status
                                    ? "wordle-tile--filled"
                                    : "",
                                revealingRow === rowIndex && status
                                    ? "wordle-tile--revealing"
                                    : "",
                            ]
                                .filter(Boolean)
                                .join(" ")}
                            key={`${rowIndex}-${columnIndex}`}
                            style={
                                revealingRow === rowIndex
                                    ? {
                                          animationDelay: `${columnIndex * REVEAL_STEP_MS}ms`,
                                      }
                                    : undefined
                            }
                        >
                            {letter}
                        </div>
                    );
                })}
            </div>
        );
    }

    const winRate =
        stats.gamesPlayed > 0
            ? Math.round((stats.wins / stats.gamesPlayed) * 100)
            : 0;

    return (
        <main className="wordle-page">
            <header className="wordle-topbar">
                <button
                    type="button"
                    className="wordle-back-button"
                    onClick={() => navigate("/")}
                    aria-label="Return to homepage"
                >
                    ←
                </button>

                <div className="wordle-title-group">
                    <h1>Wordle Ranked</h1>
                    <p>Daily five-letter challenge</p>
                </div>

                <div className="wordle-rank-badge">
                    {stats.rankScore} pts
                </div>
            </header>

            <div className="wordle-layout">
                <section className="wordle-game-card">
                    {showConfetti && (
                        <div className="wordle-confetti" aria-hidden="true">
                            {Array.from({ length: 24 }, (_, index) => (
                                <i
                                    key={index}
                                    style={{
                                        "--confetti-index": index,
                                        "--confetti-delay": `${(index % 8) * 45}ms`,
                                        "--confetti-left": `${4 + (index % 12) * 8}%`,
                                        "--confetti-drift": `${(index % 5) * 13 - 26}px`,
                                    }}
                                />
                            ))}
                        </div>
                    )}
                    <div className="wordle-game-header">
                        <div>
                            <p className="wordle-small-label">Ranked score</p>
                            <strong
                                key={`score-${statsPulse}`}
                                className={statsPulse ? "wordle-stat--pulse" : ""}
                            >
                                {stats.rankScore}
                            </strong>
                        </div>

                        <div className="wordle-attempt-counter">
                            Attempt{" "}
                            {Math.min(guesses.length + 1, MAX_ATTEMPTS)} of{" "}
                            {MAX_ATTEMPTS}
                        </div>
                    </div>

                    <div className="wordle-board" aria-label="Wordle game board">
                        {Array.from(
                            { length: MAX_ATTEMPTS },
                            (_, index) => renderBoardRow(index),
                        )}
                    </div>

                    <div
                        className={`wordle-message ${
                            gameStatus !== "playing"
                                ? "wordle-message--result"
                                : ""
                        }`}
                        role="status"
                    >
                        {message ||
                            (gameStatus === "loading"
                                ? "Loading today's Wordle..."
                                : "Guess the daily five-letter word.")}
                    </div>

                    <div className="wordle-keyboard">
                        {KEYBOARD_ROWS.map((row, rowIndex) => (
                            <div
                                className="wordle-keyboard-row"
                                key={rowIndex}
                            >
                                {row.map((key) => {
                                    const status = keyboardStatuses[key];

                                    return (
                                        <button
                                            type="button"
                                            key={key}
                                            onClick={() => handleKey(key)}
                                            className={[
                                                "wordle-key",
                                                key.length > 1
                                                    ? "wordle-key--wide"
                                                    : "",
                                                status
                                                    ? `wordle-key--${status}`
                                                    : "",
                                                pressedKey === key
                                                    ? "wordle-key--pressed"
                                                    : "",
                                            ]
                                                .filter(Boolean)
                                                .join(" ")}
                                            disabled={
                                                gameStatus !== "playing" ||
                                                isSubmitting
                                            }
                                        >
                                            {key === "BACKSPACE" ? "⌫" : key}
                                        </button>
                                    );
                                })}
                            </div>
                        ))}
                    </div>

                    {gameStatus !== "playing" &&
                        gameStatus !== "loading" && (
                            <button
                                type="button"
                                className="wordle-home-button"
                                onClick={() => navigate("/")}
                            >
                                Return home
                            </button>
                        )}
                </section>

                <aside className="wordle-stats-card">
                    <div className="wordle-mascot-banner">
                        <div>
                            <p className="wordle-small-label">Kotaro says</p>
                            <strong>Don't be a dumbass you have 6 tries</strong>
                        </div>
                        <img src={kotaroImage} alt="Kotaro, the white cat mascot" />
                    </div>

                    <div className="wordle-stats-heading">
                        <div>
                            <p className="wordle-small-label">Ranked profile</p>
                            <h2>{stats.rankScore} points</h2>
                        </div>

                        <div
                            key={`combo-${statsPulse}`}
                            className={`wordle-rating-circle ${
                                statsPulse ? "wordle-rating-circle--pulse" : ""
                            }`}
                        >
                            ×{stats.combo}
                        </div>
                    </div>

                    <div className="wordle-stats-grid">
                        <div>
                            <strong>{stats.gamesPlayed}</strong>
                            <span>Played</span>
                        </div>

                        <div>
                            <strong>{winRate}%</strong>
                            <span>Win rate</span>
                        </div>

                        <div>
                            <strong>{stats.combo}</strong>
                            <span>Current combo</span>
                        </div>

                        <div>
                            <strong>{stats.bestCombo}</strong>
                            <span>Best combo</span>
                        </div>
                    </div>

                    <div className="wordle-ranking-note">
                        <strong>Daily scoring</strong>
                        <p>
                            Points gained = (6 − attempts used) × combo.
                            Missing a day or losing resets your combo.
                        </p>
                        {answer && <p>Today's answer: {answer}</p>}
                    </div>
                </aside>
            </div>
        </main>
    );
}

export default WordleRanked;
