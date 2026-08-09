import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import triviaMomo from "../../assets/trivia-momo.png";
import kotaroImage from "../../assets/kotaro.png";
import { useAuth } from "../../context/authContext.jsx";
import "./triviaquest.css";

const API_BASE_URL =
    import.meta.env.VITE_API_BASE_URL ||
    import.meta.env.VITE_API_BASE ||
    "";

async function readJson(response) {
    const contentType = response.headers.get("content-type") || "";
    if (!contentType.includes("application/json")) {
        throw new Error(`The server returned an unexpected response (${response.status}).`);
    }
    return response.json();
}

function healthPercent(value, maximum) {
    if (!maximum) return 0;
    return Math.max(0, Math.min(100, (Number(value || 0) / Number(maximum)) * 100));
}

function initials(name) {
    return String(name || "?")
        .split(/\s+/)
        .slice(0, 2)
        .map((part) => part[0]?.toUpperCase())
        .join("");
}

function PlayerAvatar({ player }) {
    const imageUrl = player?.avatarUrl || player?.profilePicLink || "";
    return (
        <span className="quest-avatar" aria-hidden="true">
            <span>{initials(player?.displayName)}</span>
            {imageUrl && (
                <img
                    src={imageUrl}
                    alt=""
                    referrerPolicy="no-referrer"
                    onError={(event) => {
                        event.currentTarget.style.display = "none";
                    }}
                />
            )}
        </span>
    );
}

function TriviaQuest() {
    const navigate = useNavigate();
    const [searchParams, setSearchParams] = useSearchParams();
    const { currentUser } = useAuth();
    const inviteToken = searchParams.get("invite") || "";

    const [team, setTeam] = useState(null);
    const [battle, setBattle] = useState(null);
    const [leaderboard, setLeaderboard] = useState([]);
    const [invitePreview, setInvitePreview] = useState(null);
    const [inviteDetails, setInviteDetails] = useState(null);
    const [teamName, setTeamName] = useState("");
    const [difficulty, setDifficulty] = useState("medium");
    const [turn, setTurn] = useState(null);
    const [answerResult, setAnswerResult] = useState(null);
    const [selectedAnswer, setSelectedAnswer] = useState("");
    const [healTarget, setHealTarget] = useState("");
    const [status, setStatus] = useState("loading");
    const [busy, setBusy] = useState("");
    const [message, setMessage] = useState("");
    const [error, setError] = useState("");

    const authHeaders = useCallback(async (json = false) => {
        if (!currentUser) throw new Error("Please sign in to play Trivia Quest.");
        const token = await currentUser.getIdToken();
        return {
            ...(json ? { "Content-Type": "application/json" } : {}),
            Authorization: `Bearer ${token}`,
        };
    }, [currentUser]);

    const apiRequest = useCallback(async (path, options = {}) => {
        const response = await fetch(`${API_BASE_URL}${path}`, {
            cache: "no-store",
            ...options,
            headers: {
                ...(await authHeaders(Boolean(options.body))),
                ...(options.headers || {}),
            },
        });
        const data = await readJson(response);
        if (!response.ok) throw new Error(data.error || "Something went wrong.");
        return data;
    }, [authHeaders]);

    const loadBattle = useCallback(async () => {
        const data = await apiRequest("/api/games/trivia-quest/battle");
        setBattle(data.battle || null);
        return data.battle || null;
    }, [apiRequest]);

    const loadActiveTurn = useCallback(async () => {
        const data = await apiRequest("/api/games/trivia-quest/battle/turn");
        const activeTurn = data.turn || null;
        setTurn(activeTurn);
        setAnswerResult(activeTurn?.answerResult || null);
        setSelectedAnswer("");
        return activeTurn;
    }, [apiRequest]);

    const loadLeaderboard = useCallback(async () => {
        const data = await apiRequest("/api/games/trivia-quest/leaderboard");
        setLeaderboard(Array.isArray(data.leaderboard) ? data.leaderboard : []);
    }, [apiRequest]);

    const loadTeam = useCallback(async () => {
        const data = await apiRequest("/api/games/trivia-quest/teams/me");
        setTeam(data.team || null);
        return data.team || null;
    }, [apiRequest]);

    const refreshGame = useCallback(async () => {
        setError("");
        try {
            const nextTeam = await loadTeam();
            if (nextTeam?.status === "active") {
                await loadBattle();
                await loadActiveTurn();
            } else {
                setBattle(null);
                setTurn(null);
                setAnswerResult(null);
            }
            await loadLeaderboard();
            setStatus("ready");
        } catch (requestError) {
            setError(requestError?.message || "Could not load Trivia Quest.");
            setStatus("error");
        }
    }, [loadActiveTurn, loadBattle, loadLeaderboard, loadTeam]);

    useEffect(() => {
        if (!currentUser) return;
        void refreshGame();
    }, [currentUser, refreshGame]);

    useEffect(() => {
        if (!inviteToken) {
            setInvitePreview(null);
            return;
        }
        const controller = new AbortController();
        async function preview() {
            try {
                const response = await fetch(
                    `${API_BASE_URL}/api/games/trivia-quest/invites/${encodeURIComponent(inviteToken)}`,
                    { signal: controller.signal, cache: "no-store" },
                );
                const data = await readJson(response);
                if (!response.ok) throw new Error(data.error || "This invite is unavailable.");
                setInvitePreview(data);
            } catch (requestError) {
                if (requestError?.name !== "AbortError") {
                    setError(requestError?.message || "Could not open this invite.");
                }
            }
        }
        void preview();
        return () => controller.abort();
    }, [inviteToken]);

    useEffect(() => {
        if (battle?.status !== "active" || turn) return undefined;
        const timer = window.setInterval(() => {
            void Promise.all([loadBattle(), loadTeam()]).catch(() => undefined);
        }, 10000);
        return () => window.clearInterval(timer);
    }, [battle?.status, loadBattle, loadTeam, turn]);

    const isLeader = team?.leaderId === currentUser?.uid;
    const currentPlayer = battle?.members?.find((member) => member.uid === currentUser?.uid);
    const healTargets = useMemo(
        () => (battle?.members || []).filter((member) => member.health < member.maxHealth),
        [battle?.members],
    );

    async function runAction(actionName, callback) {
        setBusy(actionName);
        setError("");
        setMessage("");
        try {
            await callback();
        } catch (requestError) {
            setError(requestError?.message || "Something went wrong.");
        } finally {
            setBusy("");
        }
    }

    function createTeam(event) {
        event.preventDefault();
        if (!teamName.trim()) return;
        void runAction("create-team", async () => {
            const data = await apiRequest("/api/games/trivia-quest/teams", {
                method: "POST",
                body: JSON.stringify({ name: teamName.trim() }),
            });
            setTeam(data.team);
            setTeamName("");
            setMessage("Your team is ready. Invite up to four teammates.");
        });
    }

    function acceptInvite() {
        void runAction("accept-invite", async () => {
            const data = await apiRequest(
                `/api/games/trivia-quest/invites/${encodeURIComponent(inviteToken)}/accept`,
                { method: "POST", body: "{}" },
            );
            setTeam(data.team);
            setInvitePreview(null);
            setSearchParams({}, { replace: true });
            setMessage(`You joined ${data.team.name}.`);
            if (data.team.status === "active") {
                await loadBattle();
                await loadActiveTurn();
            }
            await loadLeaderboard();
        });
    }

    function createInvite() {
        void runAction("create-invite", async () => {
            const data = await apiRequest(
                `/api/games/trivia-quest/teams/${encodeURIComponent(team.id)}/invites`,
                { method: "POST", body: "{}" },
            );
            setInviteDetails(data);
            setMessage("Invite created. Share it before the weekly reset.");
        });
    }

    function removeMember(member) {
        if (!member?.uid || member.uid === currentUser?.uid) return;
        if (!window.confirm(`Remove ${member.displayName} from this team?`)) return;

        void runAction(`remove-member-${member.uid}`, async () => {
            const data = await apiRequest(
                `/api/games/trivia-quest/teams/${encodeURIComponent(team.id)}/members/${encodeURIComponent(member.uid)}`,
                { method: "DELETE" },
            );
            setTeam(data.team);
            setInviteDetails(null);
            setMessage(`${member.displayName} was removed from the team.`);
        });
    }

async function copyInvite() {
    const inviteUrl = inviteDetails?.inviteUrl;
    if (!inviteUrl) {
        setError("There is no invite link to copy.");
        return;
    }

    try {
        if (navigator.clipboard && window.isSecureContext) {
            await navigator.clipboard.writeText(inviteUrl);
            setMessage("Invite link copied.");
            setError("");
            return;
        }

        const textarea = document.createElement("textarea");
        textarea.value = inviteUrl;

        textarea.style.position = "fixed";
        textarea.style.left = "-9999px";
        textarea.style.top = "-9999px";

        document.body.appendChild(textarea);

        textarea.focus();
        textarea.select();

        const successful = document.execCommand("copy");

        document.body.removeChild(textarea);

        if (!successful) {
            throw new Error("Copy command failed.");
        }

        setMessage("Invite link copied.");
        setError("");
    } catch (copyError) {
        console.error("Failed to copy invite link:", copyError);

        setError(
            "Could not copy the invite link automatically. Select the link and copy it manually."
        );
    }
}

    function startBattle() {
        void runAction("start-battle", async () => {
            const data = await apiRequest(
                `/api/games/trivia-quest/teams/${encodeURIComponent(team.id)}/start`,
                { method: "POST", body: "{}" },
            );
            setBattle(data.battle);
            setTeam((current) => ({ ...current, status: "active" }));
            setMessage("The battle has begun. Choose your first question.");
        });
    }

    function requestQuestion() {
        void runAction("question", async () => {
            const data = await apiRequest("/api/games/trivia-quest/battle/questions", {
                method: "POST",
                body: JSON.stringify({ difficulty }),
            });
            setTurn(data);
            setSelectedAnswer("");
            setAnswerResult(null);
            setHealTarget("");
        });
    }

    function submitAnswer() {
        if (!turn?.turnId || !selectedAnswer) return;
        void runAction("answer", async () => {
            const data = await apiRequest(
                `/api/games/trivia-quest/battle/questions/${encodeURIComponent(turn.turnId)}/answer`,
                { method: "POST", body: JSON.stringify({ answer: selectedAnswer }) },
            );
            setAnswerResult(data);
            if (!data.correct) {
                setTurn(null);
                setSelectedAnswer("");
                await loadBattle();
                await loadLeaderboard();
            }
        });
    }

    function chooseAction(action) {
        if (!turn?.turnId) return;
        if (action === "heal" && !healTarget) {
            setError("Choose a teammate to heal.");
            return;
        }
        void runAction(action, async () => {
            const data = await apiRequest(
                `/api/games/trivia-quest/battle/questions/${encodeURIComponent(turn.turnId)}/action`,
                {
                    method: "POST",
                    body: JSON.stringify({
                        action,
                        ...(action === "heal" ? { targetUserId: healTarget } : {}),
                    }),
                },
            );
            setMessage(
                action === "attack"
                    ? `You dealt ${data.amount} damage to the boss!`
                    : `You restored ${data.amount} health.`,
            );
            setTurn(null);
            setAnswerResult(null);
            setSelectedAnswer("");
            setHealTarget("");
            await loadBattle();
            await loadLeaderboard();
        });
    }

    function renderInviteCard() {
        if (!inviteToken || !invitePreview || team) return null;
        return (
            <section className="quest-panel quest-invite-panel">
                <div className="quest-invite-mark">✦</div>
                <div>
                    <p className="quest-eyebrow">You have been summoned</p>
                    <h2>Join {invitePreview.team?.name}</h2>
                    <p>
                        {invitePreview.team?.memberCount || 0} of {invitePreview.team?.maxMembers || 5} adventurers are ready.
                    </p>
                </div>
                <button type="button" className="quest-button quest-button--primary" onClick={acceptInvite} disabled={Boolean(busy)}>
                    {busy === "accept-invite" ? "Joining..." : "Accept invite"}
                </button>
            </section>
        );
    }

    function renderTeamSetup() {
        if (team) return null;
        return (
            <section className="quest-panel quest-create-panel">
                <div>
                    <p className="quest-eyebrow">Begin this week&apos;s quest</p>
                    <h2>Form your party</h2>
                    <p>Create a team, then invite up to four friends using a secure link.</p>
                </div>
                <form onSubmit={createTeam}>
                    <label htmlFor="quest-team-name">Team name</label>
                    <div>
                        <input
                            id="quest-team-name"
                            value={teamName}
                            onChange={(event) => setTeamName(event.target.value)}
                            minLength={2}
                            maxLength={40}
                            placeholder="The Night Owls"
                        />
                        <button className="quest-button quest-button--primary" disabled={Boolean(busy) || teamName.trim().length < 2}>
                            {busy === "create-team" ? "Creating..." : "Create team"}
                        </button>
                    </div>
                </form>
            </section>
        );
    }

    function renderLobby() {
        if (!team || team.status !== "forming") return null;
        return (
            <section className="quest-panel quest-lobby">
                <div className="quest-section-heading">
                    <div>
                        <p className="quest-eyebrow">Party lobby · {team.weekKey}</p>
                        <h2>{team.name}</h2>
                    </div>
                    <span className="quest-count">{team.memberCount}/{team.maxMembers}</span>
                </div>

                <div className="quest-roster">
                    {team.members?.map((member) => (
                        <article key={member.uid}>
                            <PlayerAvatar player={member} />
                            <div>
                                <strong>{member.displayName}</strong>
                                <span>{member.uid === team.leaderId ? "Party leader" : "Adventurer"}</span>
                            </div>
                            {isLeader && member.uid !== team.leaderId && (
                                <button
                                    type="button"
                                    className="quest-roster__remove"
                                    onClick={() => removeMember(member)}
                                    disabled={Boolean(busy)}
                                    aria-label={`Remove ${member.displayName} from the team`}
                                >
                                    {busy === `remove-member-${member.uid}` ? "Removing..." : "Remove"}
                                </button>
                            )}
                        </article>
                    ))}
                    {Array.from({ length: Math.max(0, team.maxMembers - team.memberCount) }, (_, index) => (
                        <article className="quest-roster__empty" key={`empty-${index}`}>
                            <span>+</span><div><strong>Open place</strong><span>Invite a friend</span></div>
                        </article>
                    ))}
                </div>

                {isLeader ? (
                    <div className="quest-lobby-actions">
                        <button type="button" className="quest-button" onClick={createInvite} disabled={Boolean(busy) || team.memberCount >= team.maxMembers}>
                            {busy === "create-invite" ? "Creating link..." : "Create invite link"}
                        </button>
                        <button type="button" className="quest-button quest-button--primary" onClick={startBattle} disabled={Boolean(busy)}>
                            {busy === "start-battle" ? "Opening the gate..." : "Start battle"}
                        </button>
                    </div>
                ) : (
                    <p className="quest-waiting">Waiting for the party leader to start the battle.</p>
                )}

                {inviteDetails && (
                    <div className="quest-share-box">
                        <label htmlFor="quest-invite-url">Share this invite</label>
                        <div>
                            <input id="quest-invite-url" readOnly value={inviteDetails.inviteUrl} onFocus={(event) => event.target.select()} />
                            <button type="button" onClick={copyInvite}>Copy</button>
                            <a href={inviteDetails.telegramShareUrl} target="_blank" rel="noreferrer">Telegram</a>
                        </div>
                    </div>
                )}
            </section>
        );
    }

    function renderBattle() {
        if (!battle) return null;
        const bossPercent = healthPercent(battle.boss?.health, battle.boss?.maxHealth);
        const damageLeaders = [...(battle.members || [])].sort(
            (first, second) => Number(second.damageDealt || 0) - Number(first.damageDealt || 0),
        );
        const totalDamage = Math.max(
            0,
            Number(battle.boss?.maxHealth || 0) - Number(battle.boss?.health || 0),
        );
        return (
            <>
                <section
                    className={`quest-battle-stage quest-battle-stage--${battle.status} ${battle.boss?.battlefieldImageUrl ? "has-battlefield-art" : ""}`}
                    style={
                        battle.boss?.battlefieldImageUrl
                            ? {
                                backgroundImage: `url("${battle.boss.battlefieldImageUrl}")`,
                            }
                            : undefined
                    }
                >
                    <div className="quest-battle-hud">
                        <div>
                            <p className="quest-eyebrow">Weekly boss · {battle.weekKey}</p>
                            <h2>{battle.boss?.name}</h2>
                        </div>
                        <div className="quest-boss-health">
                            <div className="quest-health-label">
                                <strong>Boss HP</strong>
                                <span>{battle.boss?.health?.toLocaleString()} / {battle.boss?.maxHealth?.toLocaleString()}</span>
                            </div>
                            <div className="quest-health-track quest-health-track--boss">
                                <span style={{ width: `${bossPercent}%` }} />
                            </div>
                        </div>
                    </div>

                    <div className="quest-battlefield">
                        <div className="quest-enemy-side">
                            <div className={`quest-boss ${battle.boss?.imageUrl ? "quest-boss--asset" : ""}`} aria-label={battle.boss?.name || "Weekly boss"}>
                                {battle.boss?.imageUrl ? (
                                    <img src={battle.boss.imageUrl} alt={battle.boss?.name || "Weekly boss"} />
                                ) : (
                                    <>
                                        <span className="quest-boss__ear quest-boss__ear--left" />
                                        <span className="quest-boss__ear quest-boss__ear--right" />
                                        <div className="quest-boss__face"><span>●</span><b>⌄</b><span>●</span></div>
                                    </>
                                )}
                            </div>
                            <span className="quest-combat-label">Boss</span>
                        </div>

                        <div className="quest-versus" aria-hidden="true">VS</div>

                        <div className="quest-party-formation" aria-label="Your party on the battlefield">
                            {battle.members?.map((member, index) => (
                                <article
                                    key={member.uid}
                                    className={`quest-combatant quest-combatant--${index + 1} ${member.health <= 0 ? "is-down" : ""}`}
                                >
                                    <PlayerAvatar player={member} />
                                    <strong>{member.displayName}</strong>
                                    <div className="quest-health-track">
                                        <span style={{ width: `${healthPercent(member.health, member.maxHealth)}%` }} />
                                    </div>
                                    <small>{member.health}/{member.maxHealth} HP</small>
                                </article>
                            ))}
                        </div>

                    </div>

                    {battle.status === "victory" && <div className="quest-result-banner"><strong>Victory!</strong><span>Your party defeated the weekly boss.</span></div>}
                    {battle.status === "party_defeated" && <div className="quest-result-banner quest-result-banner--lost"><strong>Party defeated</strong><span>Return stronger next week.</span></div>}
                </section>

                <section className="quest-panel quest-command-panel">
                    <div className="quest-command-panel__label">
                        <span>Command phase</span>
                        <small>{currentPlayer?.health || 0} HP · {Number(currentPlayer?.damageDealt || 0).toLocaleString()} damage dealt</small>
                    </div>
                        {battle.status !== "active" ? (
                            <div className="quest-turn-state"><span>✦</span><h2>This quest is complete</h2><p>See where your party placed in the weekly rankings.</p></div>
                        ) : currentPlayer?.health <= 0 ? (
                            <div className="quest-turn-state"><span>♡</span><h2>You are down</h2><p>A teammate must heal you before you can answer another question.</p></div>
                        ) : !turn ? (
                            <div className="quest-question-picker">
                                {answerResult && !answerResult.correct && (
                                    <div className="quest-answer-feedback" role="status">
                                        <span>Incorrect answer</span>
                                        <strong>The correct answer was {answerResult.correctAnswer}.</strong>
                                        <small>You took {answerResult.damageTaken} damage.</small>
                                    </div>
                                )}
                                <p className="quest-eyebrow">Your next move</p>
                                <h2>Choose a challenge</h2>
                                <p>Harder questions deal more damage, but wrong answers hurt more too.</p>
                                <div className="quest-difficulties">
                                    {[
                                        ["easy", "30", "15", "8"],
                                        ["medium", "50", "25", "16"],
                                        ["hard", "80", "40", "30"],
                                    ].map(([level, damage, healing, penalty]) => (
                                        <button type="button" key={level} className={difficulty === level ? "is-selected" : ""} onClick={() => setDifficulty(level)}>
                                            <strong>{level}</strong><span>{damage} attack · {healing} heal</span><small>{penalty} damage if wrong</small>
                                        </button>
                                    ))}
                                </div>
                                <button type="button" className="quest-button quest-button--primary quest-button--wide" onClick={requestQuestion} disabled={Boolean(busy)}>
                                    {busy === "question" ? "Finding a question..." : "Draw question"}
                                </button>
                            </div>
                        ) : !answerResult?.correct ? (
                            <div className="quest-question">
                                <div className="quest-question__meta"><span>{turn.question?.difficulty}</span><span>{turn.question?.category || "Trivia"}</span></div>
                                <h2>{turn.question?.text}</h2>
                                <div className="quest-answers">
                                    {turn.question?.answers?.map((answer) => (
                                        <button type="button" key={answer} className={selectedAnswer === answer ? "is-selected" : ""} onClick={() => setSelectedAnswer(answer)}>{answer}</button>
                                    ))}
                                </div>
                                <button type="button" className="quest-button quest-button--primary quest-button--wide" onClick={submitAnswer} disabled={!selectedAnswer || Boolean(busy)}>
                                    {busy === "answer" ? "Checking..." : "Lock in answer"}
                                </button>
                            </div>
                        ) : (
                            <div className="quest-actions">
                                <p className="quest-eyebrow">Correct answer!</p>
                                <h2>Choose your move</h2>
                                <div className="quest-action-cards">
                                    <button type="button" onClick={() => chooseAction("attack")} disabled={Boolean(busy)}>
                                        <span>⚔</span><strong>Attack</strong><small>Deal {answerResult.actions?.attack} damage</small>
                                    </button>
                                    <div className="quest-heal-card">
                                        <span>♡</span><strong>Heal</strong><small>Restore {answerResult.actions?.heal} HP</small>
                                        <select value={healTarget} onChange={(event) => setHealTarget(event.target.value)}>
                                            <option value="">Choose who to heal</option>
                                            {healTargets.map((member) => (
                                                <option value={member.uid} key={member.uid}>
                                                    {member.uid === currentUser?.uid ? "Yourself" : member.displayName} · {member.health}/{member.maxHealth}
                                                </option>
                                            ))}
                                        </select>
                                        <button type="button" onClick={() => chooseAction("heal")} disabled={!healTarget || Boolean(busy)}>Use heal</button>
                                    </div>
                                </div>
                            </div>
                        )}
                </section>

                {isLeader && battle.status === "active" && (battle.members?.length || 0) < team?.maxMembers && (
                    <section className="quest-panel quest-battle-invite">
                        <div>
                            <p className="quest-eyebrow">Party reinforcements</p>
                            <h2>Invite another adventurer</h2>
                            <p>The boss gains more maximum and remaining HP when a new player joins.</p>
                        </div>
                        <button type="button" className="quest-button" onClick={createInvite} disabled={Boolean(busy)}>
                            {busy === "create-invite" ? "Creating link..." : "Create battle invite"}
                        </button>
                        {inviteDetails && (
                            <div className="quest-share-box">
                                <label htmlFor="quest-active-invite-url">Share this invite</label>
                                <div>
                                    <input id="quest-active-invite-url" readOnly value={inviteDetails.inviteUrl} onFocus={(event) => event.target.select()} />
                                    <button type="button" onClick={copyInvite}>Copy</button>
                                    <a href={inviteDetails.telegramShareUrl} target="_blank" rel="noreferrer">Telegram</a>
                                </div>
                            </div>
                        )}
                    </section>
                )}

                <section className="quest-panel quest-contribution-panel">
                    <div className="quest-section-heading">
                        <div>
                            <p className="quest-eyebrow">Individual contributions</p>
                            <h2>Damage leaderboard</h2>
                        </div>
                        <div className="quest-contribution-total">
                            <span>Party damage</span>
                            <strong>{totalDamage.toLocaleString()}</strong>
                        </div>
                    </div>
                    <div className="quest-contribution-list">
                        {damageLeaders.map((member, index) => (
                            <article key={member.uid} className={member.uid === currentUser?.uid ? "is-you" : ""}>
                                <span className="quest-damage-rank">#{index + 1}</span>
                                <PlayerAvatar player={member} />
                                <div>
                                    <strong>{member.displayName}</strong>
                                    <small>{member.questionsAnswered || 0} questions · {member.correctAnswers || 0} correct</small>
                                </div>
                                <div><span>Damage</span><strong>{Number(member.damageDealt || 0).toLocaleString()}</strong></div>
                                <div><span>Healing</span><strong>{Number(member.healingDone || 0).toLocaleString()}</strong></div>
                            </article>
                        ))}
                    </div>
                </section>
            </>
        );
    }

    return (
        <main className={`trivia-quest-page ${battle ? "is-battle-active" : ""}`}>
            <div className="quest-shell">
                <header className="quest-nav">
                    <button type="button" className="quest-brand" onClick={() => navigate("/")}>
                        <img src={kotaroImage} alt="Kotaro" /><span>The Nowl In One</span>
                    </button>
                    <button type="button" className="quest-button" onClick={() => navigate("/")}>← Home</button>
                </header>

                <section className="quest-hero">
                    <div>
                        <p className="quest-eyebrow">A new adventure every week</p>
                        <h1>Trivia Quest</h1>
                        <p>Gather your party, answer trivia, and bring down the Weekly Nowl together.</p>
                    </div>
                    <div className="quest-hero__art">
                        <img src={triviaMomo} alt="Witch Momo" />
                        <div className="quest-hero__seal"><span>5</span><small>players max</small></div>
                    </div>
                </section>

                {message && <div className="quest-notice" role="status">{message}</div>}
                {error && <div className="quest-notice quest-notice--error" role="alert">{error}</div>}
                {status === "loading" && <div className="quest-loading">Preparing this week&apos;s quest...</div>}

                {status !== "loading" && renderInviteCard()}
                {status !== "loading" && !invitePreview && renderTeamSetup()}
                {status !== "loading" && renderLobby()}
                {status !== "loading" && renderBattle()}

                <section className="quest-panel quest-leaderboard">
                    <div className="quest-section-heading">
                        <div><p className="quest-eyebrow">Weekly standings</p><h2>Damage leaderboard</h2></div>
                    </div>
                    {leaderboard.length === 0 ? (
                        <div className="quest-empty">No party has entered the arena yet.</div>
                    ) : (
                        <div className="quest-ranking-list">
                            {leaderboard.map((entry) => (
                                <article key={`${entry.teamId}-${entry.uid}`} className={entry.uid === currentUser?.uid ? "is-current" : ""}>
                                    <strong className="quest-rank">#{entry.rank}</strong>
                                    <PlayerAvatar player={entry} />
                                    <div>
                                        <strong>{entry.displayName}{entry.winner ? " · Winner" : ""}</strong>
                                        <span>{entry.teamName} · {entry.questionsAnswered} questions</span>
                                    </div>
                                    <div className="quest-ranking-progress"><span style={{ width: `${entry.damagePercent}%` }} /></div>
                                    <strong>{Number(entry.damageDealt || entry.damage || 0).toLocaleString()} damage</strong>
                                </article>
                            ))}
                        </div>
                    )}
                </section>
            </div>
        </main>
    );
}

export default TriviaQuest;
