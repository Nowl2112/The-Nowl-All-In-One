from __future__ import annotations

import argparse

from app import create_app
from services.firebase import get_db
from services.riddle_service import (
    current_month_key,
    now_iso,
    player_combo,
)


def derived_totals(player):
    wins = 0
    games_played = 0
    lifetime_points = 0
    for game in (player.get("daily") or {}).values():
        status = game.get("status")
        if status not in {"won", "lost"}:
            continue
        games_played += 1
        if status == "won":
            wins += 1
            lifetime_points += int(game.get("pointsGained", 0))
    return wins, games_played, lifetime_points


def migrate(*, apply_changes=False, rebuild_totals=False):
    app = create_app()
    with app.app_context():
        db = get_db()
        if db is None:
            raise RuntimeError("Firestore is unavailable")

        changes = []
        snapshots = list(db.collection("riddleUsers").stream())
        for snapshot in snapshots:
            player = snapshot.to_dict() or {}
            update = {
                "combo": player_combo(player),
                "statsMonth": current_month_key(),
                "updatedAt": now_iso(),
            }
            if rebuild_totals:
                wins, games_played, lifetime_points = derived_totals(player)
                update.update(
                    {
                        "wins": wins,
                        "gamesPlayed": games_played,
                        "lifetimePoints": lifetime_points,
                    }
                )
            if any(player.get(key) != value for key, value in update.items() if key != "updatedAt"):
                changes.append((snapshot.reference, update))

        print(
            f"Scanned {len(snapshots)} riddle users; {len(changes)} need updates.")
        if not apply_changes:
            print("Dry run only. Re-run with --apply to write these changes.")
            return

        batch = db.batch()
        batch_size = 0
        for reference, update in changes:
            batch.set(reference, update, merge=True)
            batch_size += 1
            if batch_size == 450:
                batch.commit()
                batch = db.batch()
                batch_size = 0
        if batch_size:
            batch.commit()
        print(f"Updated {len(changes)} riddle users.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="write changes to Firestore")
    parser.add_argument(
        "--rebuild-totals",
        action="store_true",
        help="also rebuild lifetime wins, games, and points from daily records",
    )
    args = parser.parse_args()
    migrate(apply_changes=args.apply, rebuild_totals=args.rebuild_totals)
