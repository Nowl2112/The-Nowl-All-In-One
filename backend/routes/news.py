"""HTTP routes for this feature area."""

from flask import Blueprint

from core import *  # noqa: F403 - shared legacy helpers during migration

bp = Blueprint("news", __name__)


@bp.get("/api/news")
def get_news_articles():
    """
    Query parameters:
      region=singapore|global|world|asia|latest
      sort=time|importance
      limit=1..100
      offset=0..n
      q=free text search
      keywords=comma,separated,keywords
    """
    region = _normalize_news_region(request.args.get("region"))
    sort_method = str(
        request.args.get("sort") or "time"
    ).strip().lower()
    limit = request.args.get("limit", default=50, type=int)
    offset = request.args.get("offset", default=0, type=int)
    query = str(request.args.get("q") or "").strip().lower()
    keywords = [
        keyword.strip().lower()
        for keyword in str(
            request.args.get("keywords") or ""
        ).split(",")
        if keyword.strip()
    ]

    if region not in CNA_RSS_FEEDS:
        return jsonify({
            "error": "region must be singapore, global, world, asia, or latest"
        }), 400

    if sort_method not in NEWS_SORT_METHODS:
        return jsonify({
            "error": "sort must be time or importance"
        }), 400

    limit = max(1, min(limit, NEWS_MAX_LIMIT))
    offset = max(0, offset or 0)

    try:
        articles = _deduplicate_news_articles(
            _fetch_cna_feed(region)
        )
    except requests.RequestException as error:
        current_app.logger.exception("Could not retrieve CNA RSS: %s", error)
        return jsonify({
            "error": "The news provider is temporarily unavailable"
        }), 502
    except Exception as error:
        current_app.logger.exception("Could not prepare news feed: %s", error)
        return jsonify({"error": "Could not load news articles"}), 500

    if query or keywords:
        filtered: list[dict[str, Any]] = []

        for article in articles:
            searchable = " ".join([
                str(article.get("title") or ""),
                str(article.get("summary") or ""),
                " ".join(article.get("importanceReasons") or []),
            ]).lower()

            if query and query not in searchable:
                continue

            if keywords and not all(
                keyword in searchable
                for keyword in keywords
            ):
                continue

            filtered.append(article)

        articles = filtered

    sorted_articles = _sort_news_articles(articles, sort_method)
    total_count = len(sorted_articles)
    page_articles = sorted_articles[offset:offset + limit]
    next_offset = offset + len(page_articles)
    has_more = next_offset < total_count

    return jsonify({
        "articles": page_articles,
        "count": len(page_articles),
        "totalCount": total_count,
        "offset": offset,
        "limit": limit,
        "nextOffset": next_offset if has_more else None,
        "hasMore": has_more,
        "region": "global" if region == "world" else region,
        "sort": sort_method,
        "query": query,
        "keywords": keywords,
        "source": "CNA RSS",
        "refreshedAt": _now_iso(),
    })



@bp.get("/api/news/headlines")
def get_news_headlines():
    region = _normalize_news_region(
        request.args.get("region") or "singapore"
    )
    sort_method = str(
        request.args.get("sort") or "importance"
    ).strip().lower()
    limit = request.args.get("limit", default=5, type=int)

    if region not in CNA_RSS_FEEDS:
        return jsonify({"error": "Invalid news region"}), 400
    if sort_method not in NEWS_SORT_METHODS:
        return jsonify({"error": "Invalid news sort method"}), 400

    try:
        articles = _sort_news_articles(
            _deduplicate_news_articles(_fetch_cna_feed(region)),
            sort_method,
        )[:max(1, min(limit, 20))]
    except Exception as error:
        current_app.logger.exception("Could not load headline preview: %s", error)
        return jsonify({"error": "Could not load headlines"}), 502

    return jsonify({
        "articles": articles,
        "count": len(articles),
        "region": "global" if region == "world" else region,
    })



@bp.post("/api/news/summary")
def generate_news_summary():
    """
    Generate one TLDR for every article from the past 24 hours.

    JSON body:
    {
        "scope": "singapore" | "global",
        "maxArticles": 100,
        "articles": [...]  # optional; otherwise CNA RSS is fetched
    }
    """
    identity, auth_error = _authenticated_identity()
    if auth_error:
        return auth_error

    huggingface_error = _require_huggingface_configuration()
    if huggingface_error:
        return huggingface_error

    payload = request.get_json(silent=True) or {}
    scope = str(payload.get("scope") or "singapore").strip().lower()

    if scope not in {"singapore", "global"}:
        return jsonify({
            "error": "scope must be singapore or global"
        }), 400

    try:
        max_articles = int(
            payload.get("maxArticles", NEWS_SUMMARY_MAX_ARTICLES)
        )
    except (TypeError, ValueError):
        return jsonify({
            "error": "maxArticles must be a whole number"
        }), 400

    max_articles = max(
        NEWS_SUMMARY_MIN_ARTICLES,
        min(max_articles, NEWS_SUMMARY_MAX_ARTICLES),
    )

    feed_region = "singapore" if scope == "singapore" else "world"
    generated_at = datetime.now(SINGAPORE_TZ)
    window_started_at = generated_at - timedelta(
        hours=NEWS_SUMMARY_WINDOW_HOURS
    )

    client_articles = _normalize_client_news_articles(
        payload.get("articles"),
        default_region=feed_region,
    )

    try:
        if client_articles:
            articles = client_articles
            article_source = "frontend payload"
        else:
            articles = _deduplicate_news_articles(
                _fetch_cna_feed(feed_region)
            )
            article_source = "CNA RSS"

        recent_articles = _articles_from_last_hours(
            articles,
            NEWS_SUMMARY_WINDOW_HOURS,
        )

        # Keep undated client articles because the frontend may omit dates.
        if client_articles:
            recent_articles = _deduplicate_news_articles(
                recent_articles
                + [
                    article
                    for article in articles
                    if not article.get("publishedAt")
                ]
            )

        # Newest first. Every selected article is summarised, not merely the
        # highest-scoring stories.
        selected_articles = _sort_news_articles(
            recent_articles,
            "time",
        )[:max_articles]

    except requests.RequestException as error:
        current_app.logger.exception(
            "Could not retrieve articles for AI summary: %s",
            error,
        )
        return jsonify({
            "error": "The news provider is temporarily unavailable"
        }), 502
    except Exception as error:
        current_app.logger.exception(
            "Could not prepare articles for AI summary: %s",
            error,
        )
        return jsonify({
            "error": "Could not prepare the news summary"
        }), 500

    if not selected_articles:
        return jsonify({
            "error": "No articles from the past 24 hours were available",
            "scope": scope,
            "windowStartedAt": window_started_at.isoformat(),
            "windowEndedAt": generated_at.isoformat(),
        }), 404

    ai_articles = _prepare_articles_for_ai(selected_articles)

    try:
        tldrs, usage, batch_count = _generate_all_article_tldrs(
            ai_articles,
            scope,
        )
        summary = _article_tldrs_to_summary(
            selected_articles,
            tldrs,
            scope,
        )

    except PermissionError as error:
        current_app.logger.error("Hugging Face authentication failed: %s", error)
        return jsonify({"error": str(error)}), 502
    except requests.Timeout:
        return jsonify({
            "error": (
                "The AI summary took too long to generate. "
                "Please try again."
            )
        }), 504
    except requests.RequestException as error:
        current_app.logger.exception("Could not contact Hugging Face: %s", error)
        return jsonify({
            "error": "The AI summary service is unavailable"
        }), 502
    except RuntimeError as error:
        current_app.logger.exception("Could not generate AI news TLDRs: %s", error)
        return jsonify({"error": str(error)}), 502
    except Exception as error:
        current_app.logger.exception("Unexpected news summary failure: %s", error)
        return jsonify({
            "error": "Could not generate the news summary"
        }), 500

    return jsonify({
        "summary": summary,
        "scope": scope,
        "mode": "all_articles",
        "source": article_source,
        "model": HF_NEWS_MODEL,
        "articleCount": len(selected_articles),
        "summarisedArticleCount": len(summary["events"]),
        "batchCount": batch_count,
        "batchSize": NEWS_SUMMARY_BATCH_SIZE,
        "articlesConsidered": ai_articles,
        "windowHours": NEWS_SUMMARY_WINDOW_HOURS,
        "windowStartedAt": window_started_at.isoformat(),
        "windowEndedAt": generated_at.isoformat(),
        "generatedAt": generated_at.isoformat(),
        "usage": usage,
        "generatedForUser": identity["uid"],
    }), 200



@bp.get("/api/news/saved")
def get_saved_news_articles():
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error

    identity, error = _authenticated_identity()
    if error:
        return error

    results: list[dict[str, Any]] = []

    try:
        snapshots = (
            FIRESTORE_DB.collection("newsSavedArticles")
            .where("ownerId", "==", identity["uid"])
            .stream()
        )

        for snapshot in snapshots:
            saved = snapshot.to_dict() or {}
            results.append(
                _saved_news_response(snapshot.id, saved)
            )
    except Exception as database_error:
        current_app.logger.exception(
            "Could not load saved news articles: %s",
            database_error,
        )
        return jsonify({
            "error": "Could not load saved articles"
        }), 500

    results.sort(
        key=lambda item: str(item.get("updatedAt") or ""),
        reverse=True,
    )

    return jsonify({
        "articles": results,
        "count": len(results),
    })



@bp.put("/api/news/saved/<article_id>")
def save_news_article(article_id: str):
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error

    identity, error = _authenticated_identity()
    if error:
        return error

    payload = request.get_json(silent=True) or {}
    article_payload = payload.get("article")
    if not isinstance(article_payload, dict):
        article_payload = payload

    article_payload = {
        **article_payload,
        "id": article_id,
    }

    try:
        article = _news_article_snapshot(article_payload)
    except ValueError as validation_error:
        return jsonify({"error": str(validation_error)}), 400

    tags = _normalize_news_tags(payload.get("tags"))
    document_id = _news_saved_article_id(
        identity["uid"],
        article_id,
    )
    saved_ref = _document("newsSavedArticles", document_id)
    existing_snapshot = saved_ref.get()
    existing = (
        existing_snapshot.to_dict() or {}
        if existing_snapshot.exists
        else {}
    )
    now = _now_iso()

    saved = {
        "ownerId": identity["uid"],
        "articleId": article_id,
        "article": article,
        "tags": tags,
        "savedAt": existing.get("savedAt") or now,
        "updatedAt": now,
    }

    try:
        saved_ref.set(saved)
    except Exception as database_error:
        current_app.logger.exception(
            "Could not save news article: %s",
            database_error,
        )
        return jsonify({"error": "Could not save article"}), 500

    return jsonify({
        "message": "Article saved",
        "savedArticle": _saved_news_response(document_id, saved),
    }), 200 if existing else 201



@bp.patch("/api/news/saved/<article_id>")
def update_saved_news_article(article_id: str):
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error

    identity, error = _authenticated_identity()
    if error:
        return error

    document_id = _news_saved_article_id(
        identity["uid"],
        article_id,
    )
    saved_ref = _document("newsSavedArticles", document_id)
    snapshot = saved_ref.get()

    if not snapshot.exists:
        return jsonify({"error": "Saved article not found"}), 404

    payload = request.get_json(silent=True) or {}
    tags = _normalize_news_tags(payload.get("tags"))
    updates = {
        "tags": tags,
        "updatedAt": _now_iso(),
    }

    saved_ref.set(updates, merge=True)
    saved = {**(snapshot.to_dict() or {}), **updates}

    return jsonify({
        "message": "Saved article updated",
        "savedArticle": _saved_news_response(document_id, saved),
    })



@bp.delete("/api/news/saved/<article_id>")
def delete_saved_news_article(article_id: str):
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error

    identity, error = _authenticated_identity()
    if error:
        return error

    document_id = _news_saved_article_id(
        identity["uid"],
        article_id,
    )
    saved_ref = _document("newsSavedArticles", document_id)
    snapshot = saved_ref.get()

    if not snapshot.exists:
        return jsonify({"error": "Saved article not found"}), 404

    try:
        saved_ref.delete()
    except Exception as database_error:
        current_app.logger.exception(
            "Could not remove saved news article: %s",
            database_error,
        )
        return jsonify({
            "error": "Could not remove saved article"
        }), 500

    return jsonify({
        "message": "Article removed from saved items",
        "articleId": article_id,
    })



@bp.get("/api/news/<article_id>/comments")
def get_news_comments(article_id: str):
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error

    identity, error = _authenticated_identity()
    if error:
        return error

    comments: list[dict[str, Any]] = []

    try:
        snapshots = (
            FIRESTORE_DB.collection("newsComments")
            .where("articleId", "==", article_id)
            .stream()
        )

        for snapshot in snapshots:
            comment = snapshot.to_dict() or {}
            is_owner = (
                str(comment.get("ownerId") or "")
                == identity["uid"]
            )
            is_public = (
                str(comment.get("visibility") or "")
                == "public"
            )

            if is_owner or is_public:
                comments.append(
                    _news_comment_response(
                        snapshot.id,
                        comment,
                        identity["uid"],
                    )
                )
    except Exception as database_error:
        current_app.logger.exception(
            "Could not load news comments: %s",
            database_error,
        )
        return jsonify({"error": "Could not load comments"}), 500

    comments.sort(
        key=lambda comment: str(comment.get("createdAt") or "")
    )

    return jsonify({
        "comments": comments,
        "count": len(comments),
    })



@bp.post("/api/news/<article_id>/comments")
def create_news_comment(article_id: str):
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error

    identity, error = _authenticated_identity()
    if error:
        return error

    payload = request.get_json(silent=True) or {}
    text = str(payload.get("text") or "").strip()
    visibility = str(
        payload.get("visibility") or "private"
    ).strip().lower()

    if not text:
        return jsonify({"error": "Comment text is required"}), 400
    if len(text) > NEWS_MAX_COMMENT_LENGTH:
        return jsonify({
            "error": (
                f"Comment cannot exceed "
                f"{NEWS_MAX_COMMENT_LENGTH} characters"
            )
        }), 400
    if visibility not in NEWS_COMMENT_VISIBILITIES:
        return jsonify({
            "error": "visibility must be private or public"
        }), 400

    user = _get_or_create_user(
        identity["uid"],
        identity["email"],
        identity["name"],
    )
    now = _now_iso()
    comment_ref = FIRESTORE_DB.collection(
        "newsComments"
    ).document()

    comment = {
        "articleId": article_id,
        "text": text,
        "visibility": visibility,
        "ownerId": identity["uid"],
        "ownerDisplayName": str(
            user.get("displayName")
            or identity["name"]
            or "User"
        ).strip(),
        "ownerProfilePicLink": _profile_picture_link(user),
        "createdAt": now,
        "updatedAt": now,
    }

    try:
        comment_ref.set(comment)
    except Exception as database_error:
        current_app.logger.exception(
            "Could not create news comment: %s",
            database_error,
        )
        return jsonify({"error": "Could not add comment"}), 500

    return jsonify({
        "message": "Comment added",
        "comment": _news_comment_response(
            comment_ref.id,
            comment,
            identity["uid"],
        ),
    }), 201



@bp.patch("/api/news/<article_id>/comments/<comment_id>")
def update_news_comment(article_id: str, comment_id: str):
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error

    identity, error = _authenticated_identity()
    if error:
        return error

    comment_ref = _document("newsComments", comment_id)
    snapshot = comment_ref.get()

    if not snapshot.exists:
        return jsonify({"error": "Comment not found"}), 404

    comment = snapshot.to_dict() or {}

    if str(comment.get("articleId") or "") != article_id:
        return jsonify({
            "error": "Comment does not belong to this article"
        }), 400

    if str(comment.get("ownerId") or "") != identity["uid"]:
        return jsonify({
            "error": "You can only edit your own comments"
        }), 403

    payload = request.get_json(silent=True) or {}
    updates: dict[str, Any] = {}

    if "text" in payload:
        text = str(payload.get("text") or "").strip()
        if not text:
            return jsonify({
                "error": "Comment text cannot be empty"
            }), 400
        if len(text) > NEWS_MAX_COMMENT_LENGTH:
            return jsonify({
                "error": "Comment is too long"
            }), 400
        updates["text"] = text

    if "visibility" in payload:
        visibility = str(
            payload.get("visibility") or ""
        ).strip().lower()
        if visibility not in NEWS_COMMENT_VISIBILITIES:
            return jsonify({
                "error": "visibility must be private or public"
            }), 400
        updates["visibility"] = visibility

    if not updates:
        return jsonify({
            "error": "No comment changes were provided"
        }), 400

    updates["updatedAt"] = _now_iso()
    comment_ref.set(updates, merge=True)
    comment.update(updates)

    return jsonify({
        "message": "Comment updated",
        "comment": _news_comment_response(
            comment_id,
            comment,
            identity["uid"],
        ),
    })



@bp.delete("/api/news/<article_id>/comments/<comment_id>")
def delete_news_comment(article_id: str, comment_id: str):
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error

    identity, error = _authenticated_identity()
    if error:
        return error

    comment_ref = _document("newsComments", comment_id)
    snapshot = comment_ref.get()

    if not snapshot.exists:
        return jsonify({"error": "Comment not found"}), 404

    comment = snapshot.to_dict() or {}

    if str(comment.get("articleId") or "") != article_id:
        return jsonify({
            "error": "Comment does not belong to this article"
        }), 400

    if str(comment.get("ownerId") or "") != identity["uid"]:
        return jsonify({
            "error": "You can only delete your own comments"
        }), 403

    comment_ref.delete()

    return jsonify({
        "message": "Comment deleted",
        "commentId": comment_id,
    })

