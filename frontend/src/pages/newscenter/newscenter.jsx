import React, {
    useCallback,
    useEffect,
    useMemo,
    useRef,
    useState,
} from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../../context/authContext.jsx";
import kotaroImage from "../../assets/kotaro.png";
import kotaroNewsImage from "../../assets/kotaro-news.png";
import "./newscenter.css";

const API_BASE_URL =
    import.meta.env.VITE_API_BASE_URL ||
    import.meta.env.VITE_API_BASE ||
    "";

const NEWS_PAGE_SIZE = 20;

function formatPublishedAt(value) {
    if (!value) return "Publication time unavailable";

    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
        return "Publication time unavailable";
    }

    return date.toLocaleString("en-SG", {
        day: "numeric",
        month: "short",
        year: "numeric",
        hour: "numeric",
        minute: "2-digit",
    });
}

function articleReadingTime(summary) {
    const words = String(summary || "")
        .trim()
        .split(/\s+/)
        .filter(Boolean).length;

    return Math.max(1, Math.ceil(words / 180));
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

function NewsCenter() {
    const navigate = useNavigate();
    const { currentUser } = useAuth();

    const [articles, setArticles] = useState([]);
    const [savedArticles, setSavedArticles] = useState([]);
    const [status, setStatus] = useState("idle");
    const [error, setError] = useState("");
    const [hasMoreArticles, setHasMoreArticles] = useState(true);
    const [loadingMoreArticles, setLoadingMoreArticles] = useState(false);

    const articleOffsetRef = useRef(0);
    const loadMoreSentinelRef = useRef(null);
    const loadingMoreRef = useRef(false);
    const resetRequestIdRef = useRef(0);

    const [region, setRegion] = useState("singapore");
    const [sortBy, setSortBy] = useState("time");
    const [searchText, setSearchText] = useState("");
    const [keywordText, setKeywordText] = useState("");
    const [activeKeywords, setActiveKeywords] = useState([]);
    const [showSavedOnly, setShowSavedOnly] = useState(false);

    const [selectedArticle, setSelectedArticle] = useState(null);
    const [tagDraft, setTagDraft] = useState("");
    const [comments, setComments] = useState([]);
    const [commentText, setCommentText] = useState("");
    const [commentVisibility, setCommentVisibility] =
        useState("private");
    const [commentStatus, setCommentStatus] = useState("idle");
    const [panelError, setPanelError] = useState("");

    const [summaryScope, setSummaryScope] = useState("singapore");
    const [newsSummary, setNewsSummary] = useState(null);
    const [summaryStatus, setSummaryStatus] = useState("idle");
    const [summaryError, setSummaryError] = useState("");
    const [showSummary, setShowSummary] = useState(false);

    const getAuthHeaders = useCallback(async (json = false) => {
        if (!currentUser) {
            throw new Error("You must be signed in.");
        }

        const token = await currentUser.getIdToken();

        return {
            Authorization: `Bearer ${token}`,
            ...(json ? { "Content-Type": "application/json" } : {}),
        };
    }, [currentUser]);

    const savedByArticleId = useMemo(
        () =>
            new Map(
                savedArticles.map((saved) => [
                    saved.articleId,
                    saved,
                ]),
            ),
        [savedArticles],
    );

    const loadArticles = useCallback(
        async ({ reset = false, signal } = {}) => {
            if (!reset && loadingMoreRef.current) return;

            const requestId = reset
                ? resetRequestIdRef.current + 1
                : resetRequestIdRef.current;

            if (reset) {
                resetRequestIdRef.current = requestId;
                articleOffsetRef.current = 0;
                setArticles([]);
                setStatus("loading");
                setError("");
                setHasMoreArticles(true);
            } else {
                loadingMoreRef.current = true;
                setLoadingMoreArticles(true);
                setError("");
            }

            const offset = reset ? 0 : articleOffsetRef.current;

            try {
                const params = new URLSearchParams({
                    region,
                    sort: sortBy,
                    limit: String(NEWS_PAGE_SIZE),
                    offset: String(offset),
                });

                const response = await fetch(
                    `${API_BASE_URL}/api/news?${params.toString()}`,
                    {
                        signal,
                        cache: "no-store",
                        headers: { Accept: "application/json" },
                    },
                );
                const data = await readJsonResponse(response);

                if (!response.ok) {
                    throw new Error(
                        data.error || "Could not load news articles.",
                    );
                }

                if (
                    reset &&
                    requestId !== resetRequestIdRef.current
                ) {
                    return;
                }

                const incoming = Array.isArray(data.articles)
                    ? data.articles
                    : [];

                setArticles((current) => {
                    if (reset) return incoming;

                    const articleKey = (article) =>
                        article.id || article.url;

                    const byId = new Map(
                        current.map((article) => [
                            articleKey(article),
                            article,
                        ]),
                    );

                    incoming.forEach((article) => {
                        const key = articleKey(article);
                        if (key) byId.set(key, article);
                    });

                    return Array.from(byId.values());
                });

                const nextOffset = Number.isFinite(
                    Number(data.nextOffset),
                )
                    ? Number(data.nextOffset)
                    : offset + incoming.length;

                articleOffsetRef.current = nextOffset;

                const serverHasMore =
                    typeof data.hasMore === "boolean"
                        ? data.hasMore
                        : incoming.length === NEWS_PAGE_SIZE;

                setHasMoreArticles(
                    incoming.length > 0 && serverHasMore,
                );
                setStatus("success");
            } catch (loadError) {
                if (loadError?.name === "AbortError") return;

                console.error("Unable to load news:", loadError);

                if (reset) {
                    setArticles([]);
                    setStatus("error");
                }

                setError(
                    loadError?.message ||
                        "Could not load news articles.",
                );
            } finally {
                if (!reset) {
                    loadingMoreRef.current = false;
                    setLoadingMoreArticles(false);
                }
            }
        },
        [region, sortBy],
    );

    const loadSavedArticles = useCallback(async (signal) => {
        if (!currentUser) {
            setSavedArticles([]);
            return;
        }

        try {
            const headers = await getAuthHeaders();
            const response = await fetch(
                `${API_BASE_URL}/api/news/saved`,
                {
                    headers,
                    signal,
                    cache: "no-store",
                },
            );
            const data = await readJsonResponse(response);

            if (!response.ok) {
                throw new Error(
                    data.error || "Could not load saved articles.",
                );
            }

            setSavedArticles(
                Array.isArray(data.articles) ? data.articles : [],
            );
        } catch (savedError) {
            if (savedError?.name === "AbortError") return;
            console.error("Unable to load saved articles:", savedError);
        }
    }, [currentUser, getAuthHeaders]);

    useEffect(() => {
        const controller = new AbortController();

        loadArticles({
            reset: true,
            signal: controller.signal,
        });
        loadSavedArticles(controller.signal);

        return () => controller.abort();
    }, [loadArticles, loadSavedArticles]);

    useEffect(() => {
        const sentinel = loadMoreSentinelRef.current;

        if (
            !sentinel ||
            showSavedOnly ||
            !hasMoreArticles ||
            status !== "success"
        ) {
            return undefined;
        }

        const observer = new IntersectionObserver(
            ([entry]) => {
                if (
                    entry?.isIntersecting &&
                    !loadingMoreRef.current
                ) {
                    loadArticles({ reset: false });
                }
            },
            {
                root: null,
                rootMargin: "400px 0px",
                threshold: 0.01,
            },
        );

        observer.observe(sentinel);

        return () => observer.disconnect();
    }, [
        hasMoreArticles,
        loadArticles,
        showSavedOnly,
        status,
    ]);

    const displayedArticles = useMemo(() => {
        const sourceArticles = showSavedOnly
            ? savedArticles.map((saved) => ({
                  ...saved.article,
                  id: saved.articleId,
              }))
            : articles;

        const normalizedSearch = searchText.trim().toLowerCase();

        return sourceArticles.filter((article) => {
            const searchable = [
                article.title,
                article.summary,
                article.source,
            ]
                .join(" ")
                .toLowerCase();

            const matchesSearch =
                !normalizedSearch ||
                searchable.includes(normalizedSearch);

            const matchesKeywords =
                activeKeywords.length === 0 ||
                activeKeywords.every((keyword) =>
                    searchable.includes(keyword.toLowerCase()),
                );

            return matchesSearch && matchesKeywords;
        });
    }, [
        articles,
        savedArticles,
        showSavedOnly,
        searchText,
        activeKeywords,
    ]);

    function addKeyword() {
        const keyword = keywordText.trim();

        if (
            !keyword ||
            activeKeywords.some(
                (item) => item.toLowerCase() === keyword.toLowerCase(),
            )
        ) {
            setKeywordText("");
            return;
        }

        setActiveKeywords((current) => [...current, keyword]);
        setKeywordText("");
    }

    function handleKeywordKeyDown(event) {
        if (event.key === "Enter" || event.key === ",") {
            event.preventDefault();
            addKeyword();
        }
    }

    async function saveArticle(article) {
        try {
            const headers = await getAuthHeaders(true);
            const response = await fetch(
                `${API_BASE_URL}/api/news/saved/${article.id}`,
                {
                    method: "PUT",
                    headers,
                    body: JSON.stringify({
                        article,
                        tags:
                            savedByArticleId.get(article.id)?.tags || [],
                    }),
                },
            );
            const data = await readJsonResponse(response);

            if (!response.ok) {
                throw new Error(data.error || "Could not save article.");
            }

            setSavedArticles((current) => [
                data.savedArticle,
                ...current.filter(
                    (item) => item.articleId !== article.id,
                ),
            ]);
        } catch (saveError) {
            setError(saveError?.message || "Could not save article.");
        }
    }

    async function removeSavedArticle(articleId) {
        try {
            const headers = await getAuthHeaders();
            const response = await fetch(
                `${API_BASE_URL}/api/news/saved/${articleId}`,
                {
                    method: "DELETE",
                    headers,
                },
            );
            const data = await readJsonResponse(response);

            if (!response.ok) {
                throw new Error(
                    data.error || "Could not remove saved article.",
                );
            }

            setSavedArticles((current) =>
                current.filter(
                    (item) => item.articleId !== articleId,
                ),
            );
        } catch (removeError) {
            setError(
                removeError?.message ||
                    "Could not remove saved article.",
            );
        }
    }

    async function updateTags(articleId, tags) {
        try {
            const headers = await getAuthHeaders(true);
            const response = await fetch(
                `${API_BASE_URL}/api/news/saved/${articleId}`,
                {
                    method: "PATCH",
                    headers,
                    body: JSON.stringify({ tags }),
                },
            );
            const data = await readJsonResponse(response);

            if (!response.ok) {
                throw new Error(data.error || "Could not update tags.");
            }

            setSavedArticles((current) =>
                current.map((item) =>
                    item.articleId === articleId
                        ? data.savedArticle
                        : item,
                ),
            );
        } catch (tagError) {
            setPanelError(tagError?.message || "Could not update tags.");
        }
    }

    async function loadComments(articleId) {
        setCommentStatus("loading");
        setPanelError("");

        try {
            const headers = await getAuthHeaders();
            const response = await fetch(
                `${API_BASE_URL}/api/news/${articleId}/comments`,
                {
                    headers,
                    cache: "no-store",
                },
            );
            const data = await readJsonResponse(response);

            if (!response.ok) {
                throw new Error(data.error || "Could not load notes.");
            }

            setComments(
                Array.isArray(data.comments) ? data.comments : [],
            );
            setCommentStatus("success");
        } catch (commentError) {
            setComments([]);
            setPanelError(
                commentError?.message || "Could not load notes.",
            );
            setCommentStatus("error");
        }
    }

    function openArticlePanel(article) {
        setSelectedArticle(article);
        setTagDraft("");
        setCommentText("");
        setPanelError("");
        loadComments(article.id);
    }

    async function addTagToSelected() {
        if (!selectedArticle) return;

        const tag = tagDraft.trim();
        if (!tag) return;

        let saved = savedByArticleId.get(selectedArticle.id);

        if (!saved) {
            await saveArticle(selectedArticle);
            saved = {
                articleId: selectedArticle.id,
                tags: [],
            };
        }

        const existingTags =
            savedByArticleId.get(selectedArticle.id)?.tags ||
            saved.tags ||
            [];
        const nextTags = Array.from(
            new Map(
                [...existingTags, tag].map((item) => [
                    item.toLowerCase(),
                    item,
                ]),
            ).values(),
        );

        await updateTags(selectedArticle.id, nextTags);
        setTagDraft("");
    }

    async function submitComment(event) {
        event.preventDefault();

        if (!selectedArticle || !commentText.trim()) return;

        setCommentStatus("saving");
        setPanelError("");

        try {
            const headers = await getAuthHeaders(true);
            const response = await fetch(
                `${API_BASE_URL}/api/news/${selectedArticle.id}/comments`,
                {
                    method: "POST",
                    headers,
                    body: JSON.stringify({
                        text: commentText.trim(),
                        visibility: commentVisibility,
                    }),
                },
            );
            const data = await readJsonResponse(response);

            if (!response.ok) {
                throw new Error(
                    data.error || "Could not add your note.",
                );
            }

            setComments((current) => [...current, data.comment]);
            setCommentText("");
            setCommentStatus("success");
        } catch (commentError) {
            setPanelError(
                commentError?.message || "Could not add your note.",
            );
            setCommentStatus("error");
        }
    }

    async function deleteComment(commentId) {
        if (!selectedArticle) return;

        try {
            const headers = await getAuthHeaders();
            const response = await fetch(
                `${API_BASE_URL}/api/news/${selectedArticle.id}/comments/${commentId}`,
                {
                    method: "DELETE",
                    headers,
                },
            );
            const data = await readJsonResponse(response);

            if (!response.ok) {
                throw new Error(
                    data.error || "Could not delete note.",
                );
            }

            setComments((current) =>
                current.filter((comment) => comment.id !== commentId),
            );
        } catch (deleteError) {
            setPanelError(
                deleteError?.message || "Could not delete note.",
            );
        }
    }

    async function generateNewsSummary() {
        setSummaryStatus("loading");
        setSummaryError("");
        setNewsSummary(null);
        setShowSummary(true);

        try {
            const headers = await getAuthHeaders(true);
            const response = await fetch(
                `${API_BASE_URL}/api/news/summary`,
                {
                    method: "POST",
                    headers,
                    body: JSON.stringify({
                        scope: summaryScope,
                        maxArticles: 100,
                    }),
                },
            );
            const data = await readJsonResponse(response);

            if (!response.ok) {
                throw new Error(
                    data.error || "Could not generate the news summary.",
                );
            }

            setNewsSummary(data.summary || data);
            setSummaryStatus("success");
        } catch (summaryGenerationError) {
            console.error(
                "Unable to generate news summary:",
                summaryGenerationError,
            );
            setSummaryError(
                summaryGenerationError?.message ||
                    "Could not generate the news summary.",
            );
            setSummaryStatus("error");
        }
    }

    const selectedSaved = selectedArticle
        ? savedByArticleId.get(selectedArticle.id)
        : null;

    return (
        <main className="news-page">
            <div className="news-shell">
                <header className="news-nav">
                    <button
                        type="button"
                        className="news-brand"
                        onClick={() => navigate("/")}
                    >
                        <img src={kotaroImage} alt="Kotaro" />
                        <span>
                            <strong>The Nowl In One</strong>
                            <small>News Center</small>
                        </span>
                    </button>

                    <button
                        type="button"
                        className="news-back-button"
                        onClick={() => navigate("/")}
                    >
                        ← Home
                    </button>
                </header>

                <section className="news-hero">
                    <div className="news-hero__copy">
                        <p className="news-eyebrow">Kotaro&apos;s news desk</p>
                        <h1>Find the stories that matter.</h1>
                        <p>
                            Search Singapore and global headlines, save useful
                            articles, organise them with tags, and discuss them
                            with the people using The Nowl.
                        </p>
                    </div>

                    <div className="news-hero__assistant">
                        <img
                            src={kotaroNewsImage}
                            alt="Kotaro searching through news articles"
                            className="news-hero__image"
                        />

                        <div className="news-summary-launcher">
                            <p>Kotaro can read the headlines for you.</p>

                            <div
                                className="news-summary-launcher__scope"
                                aria-label="Summary region"
                            >
                                {[
                                    ["singapore", "Singapore"],
                                    ["global", "Global"],
                                ].map(([value, label]) => (
                                    <button
                                        type="button"
                                        key={value}
                                        className={
                                            summaryScope === value
                                                ? "is-active"
                                                : ""
                                        }
                                        onClick={() =>
                                            setSummaryScope(value)
                                        }
                                        disabled={
                                            summaryStatus === "loading"
                                        }
                                    >
                                        {label}
                                    </button>
                                ))}
                            </div>

                            <button
                                type="button"
                                className="news-summary-launcher__button"
                                onClick={generateNewsSummary}
                                disabled={summaryStatus === "loading"}
                            >
                                <span aria-hidden="true">✦</span>
                                {summaryStatus === "loading"
                                    ? "Kotaro is summarising…"
                                    : "Generate AI summary"}
                            </button>
                        </div>
                    </div>
                </section>

                <section className="news-controls" aria-label="News filters">
                    <label className="news-search">
                        <span aria-hidden="true">⌕</span>
                        <input
                            type="search"
                            value={searchText}
                            onChange={(event) =>
                                setSearchText(event.target.value)
                            }
                            placeholder="Search headlines and summaries"
                        />
                    </label>

                    <div className="news-control-group">
                        <span>Region</span>
                        <div className="news-segmented">
                            {[
                                ["singapore", "Singapore"],
                                ["global", "Global"],
                            ].map(([value, label]) => (
                                <button
                                    type="button"
                                    key={value}
                                    className={
                                        region === value
                                            ? "is-active"
                                            : ""
                                    }
                                    onClick={() => {
                                        setRegion(value);
                                        setShowSavedOnly(false);
                                    }}
                                >
                                    {label}
                                </button>
                            ))}
                        </div>
                    </div>

                    <label className="news-select">
                        <span>Sort by</span>
                        <select
                            value={sortBy}
                            onChange={(event) =>
                                setSortBy(event.target.value)
                            }
                            disabled={showSavedOnly}
                        >
                            <option value="time">Newest first</option>
                            <option value="importance">
                                Most important
                            </option>
                        </select>
                    </label>

                    <button
                        type="button"
                        className={`news-saved-toggle ${
                            showSavedOnly ? "is-active" : ""
                        }`}
                        onClick={() =>
                            setShowSavedOnly((current) => !current)
                        }
                    >
                        ★ Saved ({savedArticles.length})
                    </button>
                </section>

                <section className="news-keywords">
                    <div className="news-keywords__entry">
                        <input
                            value={keywordText}
                            onChange={(event) =>
                                setKeywordText(event.target.value)
                            }
                            onKeyDown={handleKeywordKeyDown}
                            placeholder="Filter by keyword"
                        />
                        <button type="button" onClick={addKeyword}>
                            Add
                        </button>
                    </div>

                    <div className="news-keywords__chips">
                        {activeKeywords.length === 0 ? (
                            <span className="news-keywords__hint">
                                Add keywords such as MRT, AI, housing, or
                                elections.
                            </span>
                        ) : (
                            activeKeywords.map((keyword) => (
                                <button
                                    type="button"
                                    key={keyword}
                                    onClick={() =>
                                        setActiveKeywords((current) =>
                                            current.filter(
                                                (item) =>
                                                    item !== keyword,
                                            ),
                                        )
                                    }
                                >
                                    {keyword} ×
                                </button>
                            ))
                        )}
                    </div>
                </section>

                <div className="news-results-header">
                    <div>
                        <p className="news-eyebrow">
                            {showSavedOnly
                                ? "Your reading list"
                                : region === "singapore"
                                  ? "Singapore"
                                  : "Global"}
                        </p>
                        <h2>
                            {showSavedOnly
                                ? "Saved articles"
                                : "Latest headlines"}
                        </h2>
                    </div>

                    <div className="news-results-header__meta">
                        <span>
                            {displayedArticles.length} article
                            {displayedArticles.length === 1 ? "" : "s"}
                        </span>
                        <button
                            type="button"
                            onClick={() => {
                                loadArticles({ reset: true });
                                loadSavedArticles();
                            }}
                            disabled={status === "loading"}
                        >
                            {status === "loading" ? "Refreshing…" : "Refresh"}
                        </button>
                    </div>
                </div>

                {status === "loading" && !showSavedOnly && (
                    <div className="news-state">
                        Kotaro is collecting today&apos;s articles…
                    </div>
                )}

                {status === "error" && !showSavedOnly && (
                    <div className="news-state news-state--error">
                        {error}
                    </div>
                )}

                {displayedArticles.length === 0 &&
                    (status === "success" || showSavedOnly) && (
                        <div className="news-empty">
                            <img src={kotaroNewsImage} alt="" />
                            <div>
                                <h3>No matching articles.</h3>
                                <p>
                                    Try removing a keyword, changing your
                                    search, or switching region.
                                </p>
                            </div>
                        </div>
                    )}

                <section className="news-grid">
                    {displayedArticles.map((article) => {
                        const saved = savedByArticleId.get(article.id);

                        return (
                            <article className="news-card" key={article.id}>
                                <button
                                    type="button"
                                    className="news-card__image-button"
                                    onClick={() => openArticlePanel(article)}
                                >
                                    {article.imageUrl ? (
                                        <img
                                            src={article.imageUrl}
                                            alt=""
                                            loading="lazy"
                                            referrerPolicy="no-referrer"
                                        />
                                    ) : (
                                        <span>📰</span>
                                    )}
                                </button>

                                <div className="news-card__body">
                                    <div className="news-card__meta">
                                        <span>{article.source || "CNA"}</span>
                                        <span>
                                            {formatPublishedAt(
                                                article.publishedAt,
                                            )}
                                        </span>
                                    </div>

                                    <button
                                        type="button"
                                        className="news-card__title"
                                        onClick={() => openArticlePanel(article)}
                                    >
                                        {article.title}
                                    </button>

                                    <p>{article.summary}</p>

                                    <div className="news-card__footer">
                                        <span>
                                            {articleReadingTime(
                                                article.summary,
                                            )}{" "}
                                            min preview
                                        </span>

                                        <div>
                                            <button
                                                type="button"
                                                className={
                                                    saved
                                                        ? "is-saved"
                                                        : ""
                                                }
                                                onClick={() =>
                                                    saved
                                                        ? removeSavedArticle(
                                                            article.id,
                                                        )
                                                        : saveArticle(article)
                                                }
                                                aria-label={
                                                    saved
                                                        ? "Remove saved article"
                                                        : "Save article"
                                                }
                                            >
                                                {saved ? "★ Saved" : "☆ Save"}
                                            </button>
                                            <button
                                                type="button"
                                                onClick={() =>
                                                    openArticlePanel(article)
                                                }
                                            >
                                                Notes
                                            </button>
                                            <a
                                                href={article.url}
                                                target="_blank"
                                                rel="noopener noreferrer"
                                            >
                                                Read article
                                            </a>
                                        </div>
                                    </div>

                                    {saved?.tags?.length > 0 && (
                                        <div className="news-card__tags">
                                            {saved.tags.map((tag) => (
                                                <span key={tag}>{tag}</span>
                                            ))}
                                        </div>
                                    )}
                                </div>
                            </article>
                        );
                    })}
                </section>

                {!showSavedOnly && (
                    <div
                        ref={loadMoreSentinelRef}
                        className="news-load-more"
                        aria-live="polite"
                    >
                        {loadingMoreArticles && (
                            <span>Loading more articles…</span>
                        )}

                        {!hasMoreArticles &&
                            status === "success" &&
                            articles.length > 0 && (
                                <span>

                                </span>
                            )}

                        {error &&
                            status === "success" &&
                            !loadingMoreArticles && (
                                <button
                                    type="button"
                                    onClick={() => loadArticles()}
                                >
                                    Could not load more. Try again
                                </button>
                            )}
                    </div>
                )}
            </div>


            {showSummary && (
                <div
                    className="news-summary-backdrop"
                    role="presentation"
                    onMouseDown={(event) => {
                        if (event.target === event.currentTarget) {
                            setShowSummary(false);
                        }
                    }}
                >
                    <section
                        className="news-summary-modal"
                        role="dialog"
                        aria-modal="true"
                        aria-label={`${
                            summaryScope === "singapore"
                                ? "Singapore"
                                : "Global"
                        } news summary`}
                    >
                        <div className="news-summary-modal__header">
                            <div>
                                <p className="news-eyebrow">Kotaro&apos;s TLDR</p>
                                <h2>
                                    {summaryScope === "singapore"
                                        ? "Singapore news summary"
                                        : "Global news summary"}
                                </h2>
                            </div>
                            <button
                                type="button"
                                onClick={() => setShowSummary(false)}
                                aria-label="Close news summary"
                            >
                                ×
                            </button>
                        </div>

                        {summaryStatus === "loading" && (
                            <div className="news-summary-modal__loading">
                                <img src={kotaroNewsImage} alt="" />
                                <div>
                                    <strong>Kotaro is reading the news…</strong>
                                    <p>
                                        Picking out the most important events
                                        from today&apos;s headlines.
                                    </p>
                                </div>
                            </div>
                        )}

                        {summaryStatus === "error" && (
                            <div className="news-summary-modal__error">
                                <strong>Summary unavailable</strong>
                                <p>{summaryError}</p>
                                <button
                                    type="button"
                                    onClick={generateNewsSummary}
                                >
                                    Try again
                                </button>
                            </div>
                        )}

                        {summaryStatus === "success" && newsSummary && (
                            <div className="news-summary-content">
                                <div className="news-summary-content__intro">
                                    <span>AI-generated briefing</span>
                                    <h3>
                                        {newsSummary.headline ||
                                            "What matters today"}
                                    </h3>
                                    {newsSummary.overview && (
                                        <p>{newsSummary.overview}</p>
                                    )}
                                </div>

                                {Array.isArray(newsSummary.events) &&
                                    newsSummary.events.length > 0 && (
                                        <div className="news-summary-events">
                                            {newsSummary.events.map(
                                                (event, index) => (
                                                    <article
                                                        key={`${
                                                            event.title ||
                                                            "event"
                                                        }-${index}`}
                                                    >
                                                        <span>
                                                            {String(
                                                                index + 1,
                                                            ).padStart(
                                                                2,
                                                                "0",
                                                            )}
                                                        </span>
                                                        <div>
                                                            <h4>
                                                                {event.title ||
                                                                    event.headline ||
                                                                    "Important event"}
                                                            </h4>
                                                            <p>
                                                                {event.summary ||
                                                                    event.description ||
                                                                    event.whyItMatters}
                                                            </p>
                                                            {event.whyItMatters &&
                                                                event.whyItMatters !==
                                                                    event.summary && (
                                                                    <small>
                                                                        Why it
                                                                        matters: {
                                                                            event.whyItMatters
                                                                        }
                                                                    </small>
                                                                )}
                                                        </div>
                                                    </article>
                                                ),
                                            )}
                                        </div>
                                    )}

                                {Array.isArray(
                                    newsSummary.developingStories,
                                ) &&
                                    newsSummary.developingStories.length >
                                        0 && (
                                        <div className="news-summary-developing">
                                            <h3>Keep an eye on</h3>
                                            <ul>
                                                {newsSummary.developingStories.map(
                                                    (story, index) => (
                                                        <li key={index}>
                                                            {typeof story ===
                                                            "string"
                                                                ? story
                                                                : story.title ||
                                                                  story.summary}
                                                        </li>
                                                    ),
                                                )}
                                            </ul>
                                        </div>
                                    )}

                                <p className="news-summary-content__disclaimer">
                                    This briefing is generated from the news
                                    headlines and previews currently available
                                    in The Nowl. Check the original articles for
                                    full context.
                                </p>
                            </div>
                        )}
                    </section>
                </div>
            )}

            {selectedArticle && (
                <div
                    className="news-panel-backdrop"
                    role="presentation"
                    onMouseDown={(event) => {
                        if (event.target === event.currentTarget) {
                            setSelectedArticle(null);
                        }
                    }}
                >
                    <aside
                        className="news-panel"
                        role="dialog"
                        aria-modal="true"
                        aria-label={`Notes for ${selectedArticle.title}`}
                    >
                        <div className="news-panel__header">
                            <div>
                                <p className="news-eyebrow">Article workspace</p>
                                <h2>{selectedArticle.title}</h2>
                            </div>
                            <button
                                type="button"
                                onClick={() => setSelectedArticle(null)}
                                aria-label="Close article panel"
                            >
                                ×
                            </button>
                        </div>

                        <div className="news-panel__article-meta">
                            <span>{selectedArticle.source || "CNA"}</span>
                            <span>
                                {formatPublishedAt(
                                    selectedArticle.publishedAt,
                                )}
                            </span>
                            <a
                                href={selectedArticle.url}
                                target="_blank"
                                rel="noopener noreferrer"
                            >
                                Read full article ↗
                            </a>
                        </div>

                        <p className="news-panel__summary">
                            {selectedArticle.summary}
                        </p>

                        <section className="news-panel__section">
                            <div className="news-panel__section-title">
                                <h3>Saved tags</h3>
                                {!selectedSaved && (
                                    <button
                                        type="button"
                                        onClick={() =>
                                            saveArticle(selectedArticle)
                                        }
                                    >
                                        Save article first
                                    </button>
                                )}
                            </div>

                            <div className="news-tag-editor">
                                <input
                                    value={tagDraft}
                                    onChange={(event) =>
                                        setTagDraft(event.target.value)
                                    }
                                    onKeyDown={(event) => {
                                        if (event.key === "Enter") {
                                            event.preventDefault();
                                            addTagToSelected();
                                        }
                                    }}
                                    placeholder="Add a tag"
                                />
                                <button
                                    type="button"
                                    onClick={addTagToSelected}
                                >
                                    Add tag
                                </button>
                            </div>

                            <div className="news-panel__tags">
                                {selectedSaved?.tags?.length ? (
                                    selectedSaved.tags.map((tag) => (
                                        <button
                                            type="button"
                                            key={tag}
                                            onClick={() =>
                                                updateTags(
                                                    selectedArticle.id,
                                                    selectedSaved.tags.filter(
                                                        (item) => item !== tag,
                                                    ),
                                                )
                                            }
                                        >
                                            {tag} ×
                                        </button>
                                    ))
                                ) : (
                                    <span>No tags yet.</span>
                                )}
                            </div>
                        </section>

                        <section className="news-panel__section">
                            <h3>Notes and discussion</h3>

                            <form
                                className="news-comment-form"
                                onSubmit={submitComment}
                            >
                                <textarea
                                    value={commentText}
                                    onChange={(event) =>
                                        setCommentText(event.target.value)
                                    }
                                    placeholder="Write a note or comment about this article…"
                                    maxLength={4000}
                                />

                                <div>
                                    <label>
                                        Visibility
                                        <select
                                            value={commentVisibility}
                                            onChange={(event) =>
                                                setCommentVisibility(
                                                    event.target.value,
                                                )
                                            }
                                        >
                                            <option value="private">
                                                Private note
                                            </option>
                                            <option value="public">
                                                Public comment
                                            </option>
                                        </select>
                                    </label>

                                    <button
                                        type="submit"
                                        disabled={
                                            !commentText.trim() ||
                                            commentStatus === "saving"
                                        }
                                    >
                                        {commentStatus === "saving"
                                            ? "Adding…"
                                            : "Add note"}
                                    </button>
                                </div>
                            </form>

                            {panelError && (
                                <div className="news-panel__error">
                                    {panelError}
                                </div>
                            )}

                            {commentStatus === "loading" ? (
                                <div className="news-panel__loading">
                                    Loading notes…
                                </div>
                            ) : (
                                <div className="news-comments">
                                    {comments.length === 0 ? (
                                        <p className="news-comments__empty">
                                            No notes or public comments yet.
                                        </p>
                                    ) : (
                                        comments.map((comment) => (
                                            <article
                                                className="news-comment"
                                                key={comment.id}
                                            >
                                                <div className="news-comment__topline">
                                                    <div>
                                                        <strong>
                                                            {
                                                                comment.ownerDisplayName
                                                            }
                                                        </strong>
                                                        <span>
                                                            {comment.visibility ===
                                                            "private"
                                                                ? "Private note"
                                                                : "Public comment"}
                                                        </span>
                                                    </div>

                                                    {comment.isOwner && (
                                                        <button
                                                            type="button"
                                                            onClick={() =>
                                                                deleteComment(
                                                                    comment.id,
                                                                )
                                                            }
                                                        >
                                                            Delete
                                                        </button>
                                                    )}
                                                </div>

                                                <p>{comment.text}</p>
                                                <time>
                                                    {formatPublishedAt(
                                                        comment.createdAt,
                                                    )}
                                                </time>
                                            </article>
                                        ))
                                    )}
                                </div>
                            )}
                        </section>
                    </aside>
                </div>
            )}
        </main>
    );
}

export default NewsCenter;