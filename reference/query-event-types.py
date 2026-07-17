"""
Scans MLB Stats API play-by-play data from the last 30 days across all teams
and appends any newly observed (event, eventType) pairs to eventTypes.md.

The window is always "today minus 30 days", so re-running later never
re-scans stale dates -- only pairs not already in eventTypes.md get added.
"""

import re
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests
from tqdm import tqdm

MLB_API_BASE = "https://statsapi.mlb.com/api"
EVENT_TYPES_FILE = Path(__file__).parent / "eventTypes.md"
RATE_LIMIT_DELAY = 0.3  # seconds between requests; be polite to the public API


def get_game_pks(start_date, end_date):
    url = f"{MLB_API_BASE}/v1/schedule"
    params = {"sportId": 1, "startDate": start_date, "endDate": end_date}
    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    data = response.json()

    game_pks = []
    for date_block in data.get("dates", []):
        for game in date_block.get("games", []):
            game_pks.append(game["gamePk"])
    return game_pks


def get_event_pairs(game_pk):
    """Pulls (event, eventType) pairs from result, playEvents, and runners."""
    url = f"{MLB_API_BASE}/v1/game/{game_pk}/playByPlay"
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    data = response.json()

    pairs = set()
    for play in data.get("allPlays", []):
        sources = [play.get("result", {})]
        sources += [pe.get("details", {}) for pe in play.get("playEvents", [])]
        sources += [r.get("details", {}) for r in play.get("runners", [])]

        for source in sources:
            event = source.get("event")
            event_type = source.get("eventType")
            if event and event_type:
                pairs.add((event, event_type))

    return pairs


def load_existing_pairs():
    if not EVENT_TYPES_FILE.exists():
        return []

    pairs = []
    for line in EVENT_TYPES_FILE.read_text(encoding="utf-8").splitlines():
        match = re.match(r"\|\s*(.+?)\s*\|\s*(.+?)\s*\|$", line.strip())
        if match and match.group(1) not in ("event", "---"):
            pairs.append((match.group(1), match.group(2)))
    return pairs


def write_pairs(pairs):
    lines = ["# Extracted Event Types", "", "| event | eventType |", "| --- | --- |"]
    lines += [f"| {event} | {event_type} |" for event, event_type in pairs]
    EVENT_TYPES_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    end_date = datetime.now()
    start_date = end_date - timedelta(days=30)
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")

    print(f"Fetching schedule {start_str} to {end_str} (all teams)...")
    game_pks = get_game_pks(start_str, end_str)
    print(f"Found {len(game_pks)} games.")

    existing_pairs = load_existing_pairs()
    existing_set = set(existing_pairs)
    new_pairs = set()

    for game_pk in tqdm(game_pks, desc="Scanning games"):
        try:
            pairs = get_event_pairs(game_pk)
        except requests.RequestException as e:
            print(f"  Skipping game {game_pk}: {e}")
            continue
        new_pairs |= pairs - existing_set
        time.sleep(RATE_LIMIT_DELAY)

    if not new_pairs:
        print("No new event/eventType pairs found.")
        return

    all_pairs = existing_pairs + sorted(new_pairs)
    write_pairs(all_pairs)

    print(f"Added {len(new_pairs)} new pair(s) to {EVENT_TYPES_FILE}")
    for event, event_type in sorted(new_pairs):
        print(f"  {event} -> {event_type}")


if __name__ == "__main__":
    main()
