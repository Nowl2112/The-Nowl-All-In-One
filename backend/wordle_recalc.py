from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app import create_app
from services.firebase import get_db


SGT = ZoneInfo("Asia/Singapore")
MAX_ATTEMPTS = 6
MAX_COMBO = 10


def current_month_key():
    return datetime.now(SGT).strftime("%Y-%m")


def today_sgt():
    return datetime.now(SGT).date()


def recalculate_wordle_scores():
    db = get_db()

    if db is None:
        raise RuntimeError(
            "Firestore is unavailable. "
            "Ensure create_app() initializes Firebase."
        )

    active_month = current_month_key()
    today = today_sgt()

    print(
        f"Recalculating Wordle data for active month {active_month}...",
        flush=True,
    )

    processed = 0
    batch = db.batch()
    batch_size = 0

    snapshots = db.collection("wordleUsers").stream()

    for snapshot in snapshots:
        print(f"Processing {snapshot.id}...", flush=True)

        stats = snapshot.to_dict() or {}
        daily = stats.get("daily") or {}

        monthly_scores = defaultdict(int)
        daily_updates = {}

        total_wins = 0
        total_games_played = 0

        monthly_combo = 0
        current_month_combo = 0
        best_combo = 0

        current_streak = 0
        best_streak = 0

        previous_win_date = None
        last_win_date = None
        last_played_date = None

        for date_key in sorted(daily):
            game = daily.get(date_key)

            if not isinstance(game, dict):
                continue

            try:
                game_date = datetime.strptime(
                    date_key,
                    "%Y-%m-%d",
                ).date()
            except ValueError:
                print(
                    f"Skipping invalid date: "
                    f"{snapshot.id} {date_key}",
                    flush=True,
                )
                continue

            month_key = game_date.strftime("%Y-%m")
            status = game.get("status")

            if status not in {"won", "lost"}:
                continue

            total_games_played += 1
            last_played_date = game_date

            if status == "lost":
                monthly_combo = 0
                current_streak = 0
                previous_win_date = None

                daily_updates[
                    f"daily.{date_key}.pointsGained"
                ] = 0
                daily_updates[
                    f"daily.{date_key}.basePoints"
                ] = 0
                daily_updates[
                    f"daily.{date_key}.scoringCombo"
                ] = 0

                if month_key == active_month:
                    current_month_combo = 0

                continue

            guesses = game.get("guesses") or []
            attempts_used = len(guesses)

            if not 1 <= attempts_used <= MAX_ATTEMPTS:
                print(
                    f"Skipping invalid won game: "
                    f"{snapshot.id} {date_key} "
                    f"(attempts={attempts_used})",
                    flush=True,
                )

                monthly_combo = 0
                current_streak = 0
                previous_win_date = None
                continue

            total_wins += 1

            is_consecutive_win = (
                previous_win_date is not None
                and game_date - previous_win_date
                == timedelta(days=1)
            )

            is_same_month_as_previous_win = (
                previous_win_date is not None
                and previous_win_date.year == game_date.year
                and previous_win_date.month == game_date.month
            )

            # Overall streak may continue between months.
            if is_consecutive_win:
                current_streak += 1
            else:
                current_streak = 1

            best_streak = max(best_streak, current_streak)

            # Scoring combo resets after a missed day, loss,
            # or month change.
            if (
                is_consecutive_win
                and is_same_month_as_previous_win
            ):
                monthly_combo = min(
                    monthly_combo + 1,
                    MAX_COMBO,
                )
            else:
                monthly_combo = 1

            best_combo = max(best_combo, monthly_combo)

            base_points = (
                MAX_ATTEMPTS + 1
            ) - attempts_used

            corrected_points = (
                base_points * monthly_combo
            )

            monthly_scores[month_key] += corrected_points

            daily_updates[
                f"daily.{date_key}.pointsGained"
            ] = corrected_points
            daily_updates[
                f"daily.{date_key}.basePoints"
            ] = base_points
            daily_updates[
                f"daily.{date_key}.scoringCombo"
            ] = monthly_combo

            if month_key == active_month:
                current_month_combo = monthly_combo

            previous_win_date = game_date
            last_win_date = game_date

        # If the last win was before yesterday, the currently
        # displayed combo and streak are no longer active.
        if (
            last_win_date is None
            or (today - last_win_date).days > 1
        ):
            current_month_combo = 0
            current_streak = 0

        # Never carry a previous month's combo into this month.
        if (
            last_win_date is None
            or last_win_date.strftime("%Y-%m") != active_month
        ):
            current_month_combo = 0

        lifetime_score = sum(monthly_scores.values())

        update = {
            **daily_updates,
            "monthlyRankScores": dict(monthly_scores),
            "rankScore": monthly_scores.get(active_month, 0),
            "rankScoreMonth": active_month,
            "lifetimeRankScore": lifetime_score,
            "wins": total_wins,
            "gamesPlayed": total_games_played,
            "combo": current_month_combo,
            "bestCombo": best_combo,
            "currentStreak": current_streak,
            "bestStreak": best_streak,
            "lastWinDate": (
                last_win_date.isoformat()
                if last_win_date
                else None
            ),
            "lastPlayedDate": (
                last_played_date.isoformat()
                if last_played_date
                else None
            ),
            "updatedAt": datetime.now(SGT).isoformat(),
        }

        batch.update(snapshot.reference, update)
        batch_size += 1
        processed += 1

        print(
            f"{snapshot.id}: "
            f"wins={total_wins}, "
            f"gamesPlayed={total_games_played}, "
            f"combo={current_month_combo}, "
            f"currentStreak={current_streak}, "
            f"rankScore={monthly_scores.get(active_month, 0)}",
            flush=True,
        )

        if batch_size >= 450:
            print("Committing batch...", flush=True)
            batch.commit()
            batch = db.batch()
            batch_size = 0

    if batch_size:
        print("Committing final batch...", flush=True)
        batch.commit()

    print(
        f"Successfully recalculated {processed} Wordle users.",
        flush=True,
    )


if __name__ == "__main__":
    print("1. Recalculation script started", flush=True)
    print("2. Creating Flask app...", flush=True)

    app = create_app()

    print("3. Flask app created", flush=True)

    with app.app_context():
        print("4. Starting recalculation...", flush=True)
        recalculate_wordle_scores()

    print("5. Recalculation completed", flush=True)
