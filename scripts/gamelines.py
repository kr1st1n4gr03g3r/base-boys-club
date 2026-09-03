"""FanDuel Ontario MLB moneyline odds tracker.

Captures moneyline odds at checkpoints relative to each game's first pitch
(baseline / post-lineup / post-start) plus a next-day result check, and
appends rows to a CSV for later fair-price / vig analysis.

Usage:
    python scripts/gamelines.py tick

Meant to be invoked every ~5 minutes by cron or launchd. Each invocation:
  1. Ensures today's per-game checkpoint schedule exists (computed once,
     shortly after midnight ET, from the MLB Stats API).
  2. Fires any due, not-yet-captured checkpoints for today and yesterday.

Checkpoint offsets (BASELINE_TIME_ET, POST_LINEUP_OFFSET, POST_START_OFFSET,
RESULT_TIME_ET below) are best-guess defaults — tune them once you've seen
how FanDuel actually moves lines around your games.
"""
from __future__ import annotations

import csv
import json
import logging
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import statsapi

# --- Config ------------------------------------------------------------

ET = ZoneInfo("America/New_York")

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = REPO_ROOT / "output" / "gamelines"
CSV_PATH = OUTPUT_DIR / "gamelines.csv"
CHECKPOINTS_DIR = OUTPUT_DIR / "checkpoints"

CSV_COLUMNS = ["game_id", "time", "team", "vs_team", "gameline", "result"]

# Checkpoint timing, relative to each game's first pitch (ET) unless noted.
BASELINE_TIME_ET = (2, 0)  # fixed 2:00 AM ET same day (pre-lineup, pre-news)
POST_LINEUP_OFFSET = timedelta(hours=-3, minutes=-30)  # ~3.5h before first pitch
POST_START_OFFSET = timedelta(minutes=20)  # 20 min after first pitch
RESULT_TIME_ET = (1, 0)  # 1:00 AM ET, the day AFTER the game

RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SEC = 15

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("gamelines")


# --- Data model ----------------------------------------------------------

@dataclass
class Checkpoint:
    game_id: int
    kind: str  # "baseline" | "post_lineup" | "post_start" | "result"
    scheduled_for: str  # ISO datetime, ET
    home_team: str
    away_team: str
    done: bool = False


# --- MLB schedule / checkpoint planning -----------------------------------

def _checkpoint_file(day: date) -> Path:
    return CHECKPOINTS_DIR / f"{day.isoformat()}.json"


def plan_day(day: date) -> list[Checkpoint]:
    """Compute checkpoints for every MLB game scheduled on `day` (ET)."""
    games = statsapi.schedule(date=day.strftime("%Y-%m-%d"))
    checkpoints: list[Checkpoint] = []

    for g in games:
        game_id = g["game_id"]
        raw_dt = g.get("game_datetime")
        if not raw_dt:
            log.warning("Game %s missing game_datetime, skipping", game_id)
            continue
        first_pitch = datetime.fromisoformat(raw_dt.replace("Z", "+00:00")).astimezone(ET)

        baseline = datetime(day.year, day.month, day.day, *BASELINE_TIME_ET, tzinfo=ET)
        post_lineup = first_pitch + POST_LINEUP_OFFSET
        post_start = first_pitch + POST_START_OFFSET
        result_day = day + timedelta(days=1)
        result_time = datetime(
            result_day.year, result_day.month, result_day.day, *RESULT_TIME_ET, tzinfo=ET
        )

        for kind, when in [
            ("baseline", baseline),
            ("post_lineup", post_lineup),
            ("post_start", post_start),
            ("result", result_time),
        ]:
            checkpoints.append(
                Checkpoint(
                    game_id=game_id,
                    kind=kind,
                    scheduled_for=when.isoformat(),
                    home_team=g["home_name"],
                    away_team=g["away_name"],
                )
            )

    return checkpoints


def load_or_plan_day(day: date) -> list[Checkpoint]:
    path = _checkpoint_file(day)
    if path.exists():
        raw = json.loads(path.read_text())
        return [Checkpoint(**c) for c in raw]

    checkpoints = plan_day(day)
    save_checkpoints(day, checkpoints)
    log.info("Planned %d checkpoints for %s", len(checkpoints), day)
    return checkpoints


def save_checkpoints(day: date, checkpoints: list[Checkpoint]) -> None:
    CHECKPOINTS_DIR.mkdir(parents=True, exist_ok=True)
    _checkpoint_file(day).write_text(json.dumps([asdict(c) for c in checkpoints], indent=2))


# --- Odds scraping (STUB - fill in with real selectors) -------------------

def scrape_moneyline_odds() -> list[dict]:
    """Load the FanDuel Ontario MLB page and extract moneyline odds.

    TODO(kristina): this is a stub. Real selectors depend on FanDuel's
    actual rendered DOM, which hasn't been inspected against the live page.

    Suggested approach:
      1. `playwright codegen https://on.sportsbook.fanduel.ca/` locally,
         click into the MLB section, and see what selectors it records.
      2. Or: open devtools, find one moneyline row, copy its outerHTML,
         and use that to write selectors against real markup.

    Should return a list of dicts like:
        [{"home_team": "Toronto Blue Jays", "away_team": "Baltimore Orioles",
          "home_odds": -400, "away_odds": 320}, ...]
    """
    from playwright.sync_api import sync_playwright

    results: list[dict] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("https://on.sportsbook.fanduel.ca/", wait_until="networkidle")

        # TODO: replace with real selectors once known, e.g.:
        # page.wait_for_selector('[data-testid="mlb-moneyline-row"]')
        # for row in page.query_selector_all('[data-testid="mlb-moneyline-row"]'):
        #     ...
        raise NotImplementedError(
            "scrape_moneyline_odds() is a stub - fill in real selectors "
            "after inspecting the live page (see docstring above)."
        )

        browser.close()

    return results


def fetch_game_result(game_id: int) -> dict | None:
    """Fetch the final score for a completed game via the MLB Stats API."""
    try:
        data = statsapi.schedule(game_id=game_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("fetch_game_result(%s) failed: %s", game_id, exc)
        return None

    if not data:
        return None
    g = data[0]
    if g.get("status") != "Final":
        return None  # not finished yet, will retry on a later tick

    return {
        "home_team": g["home_name"],
        "away_team": g["away_name"],
        "home_score": g.get("home_score"),
        "away_score": g.get("away_score"),
    }


# --- Retry wrapper ---------------------------------------------------------

def with_retries(fn, *args, attempts=RETRY_ATTEMPTS, backoff=RETRY_BACKOFF_SEC, **kwargs):
    last_exc = None
    for attempt in range(1, attempts + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            log.warning("%s failed (attempt %d/%d): %s", fn.__name__, attempt, attempts, exc)
            if attempt < attempts:
                time.sleep(backoff)
    log.error("%s gave up after %d attempts: %s", fn.__name__, attempts, last_exc)
    return None


# --- CSV output --------------------------------------------------------------

def append_rows(rows: list[dict]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    is_new = not CSV_PATH.exists()
    with CSV_PATH.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        if is_new:
            writer.writeheader()
        writer.writerows(rows)


def fire_odds_checkpoint(cp: Checkpoint) -> bool:
    """Capture odds for one game at one checkpoint and append to CSV.

    TODO(kristina): once scrape_moneyline_odds() returns real data, match
    its entries to this checkpoint's game by team name and fill in the
    real gameline values below (currently left blank).
    """
    odds = with_retries(scrape_moneyline_odds)
    if odds is None:
        return False

    now = datetime.now(ET).strftime("%Y-%m-%d %H:%M")
    rows = [
        {
            "game_id": cp.game_id,
            "time": now,
            "team": cp.home_team,
            "vs_team": cp.away_team,
            "gameline": "",
            "result": "",
        },
        {
            "game_id": cp.game_id,
            "time": now,
            "team": cp.away_team,
            "vs_team": cp.home_team,
            "gameline": "",
            "result": "",
        },
    ]
    append_rows(rows)
    return True


def fire_result_checkpoint(cp: Checkpoint) -> bool:
    result = fetch_game_result(cp.game_id)
    if result is None:
        return False  # not final yet, will retry on a later tick

    now = datetime.now(ET).strftime("%Y-%m-%d %H:%M")
    rows = [
        {
            "game_id": cp.game_id,
            "time": now,
            "team": result["home_team"],
            "vs_team": result["away_team"],
            "gameline": "",
            "result": "Win" if result["home_score"] > result["away_score"] else "Loss",
        },
        {
            "game_id": cp.game_id,
            "time": now,
            "team": result["away_team"],
            "vs_team": result["home_team"],
            "gameline": "",
            "result": "Win" if result["away_score"] > result["home_score"] else "Loss",
        },
    ]
    append_rows(rows)
    return True


# --- Tick loop -----------------------------------------------------------------

def tick() -> None:
    now = datetime.now(ET)
    today = now.date()
    yesterday = today - timedelta(days=1)

    for day in (yesterday, today):
        checkpoints = load_or_plan_day(day)
        changed = False
        for cp in checkpoints:
            if cp.done:
                continue
            scheduled = datetime.fromisoformat(cp.scheduled_for)
            if now < scheduled:
                continue

            log.info("Firing %s checkpoint for game %s", cp.kind, cp.game_id)
            ok = fire_result_checkpoint(cp) if cp.kind == "result" else fire_odds_checkpoint(cp)

            if ok:
                cp.done = True
                changed = True
            # if not ok, leave done=False so it retries next tick

        if changed:
            save_checkpoints(day, checkpoints)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "tick":
        tick()
    else:
        print("Usage: python scripts/gamelines.py tick")
        sys.exit(1)
