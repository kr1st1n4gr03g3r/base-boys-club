"""
Live dev server for a single game.

Prompts once, exactly like `python3 import_data.py` (game date/number, clear
previous cache). From then on:

  - Saving a template, styles/*.css, or import_data.py/html_report.py/
    unit_formatters.py re-renders the page IMMEDIATELY from whatever game
    data is already in memory -- no network call.
  - In the background, independent of saves, fresh data is pulled from the
    network on its own schedule (see constants below) and the page is
    re-rendered whenever that lands.

The two background refreshes run on different cadences on purpose:
  - LIVE_FEED_REFRESH_SECONDS: game feed + venue. Cheap (1-2 requests),
    and the score/plays genuinely change during a live game, so this
    defaults to every 60 seconds.
  - STATCAST_REFRESH_SECONDS: the 5 Baseball Savant leaderboard calls.
    These are season-long aggregates that update at most a few times a
    day, not minute to minute, so refetching them as often as the live
    feed would just be repeated load for data that hasn't moved. Defaults
    to every 20 minutes -- change the constant if you want it locked to
    the same 60s cadence as the live feed instead.

Usage:
    python3 scripts/dev_server.py
"""

import functools
import importlib
import sys
import threading
import time
import webbrowser
from pathlib import Path

print = functools.partial(print, flush=True)  # noqa: A001 -- see progress output immediately

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from livereload import Server  # noqa: E402

import html_report  # noqa: E402
import import_data  # noqa: E402

OUTPUT_DIR = PROJECT_ROOT / "output"

LIVE_FEED_REFRESH_SECONDS = 60
STATCAST_REFRESH_SECONDS = 20 * 60

state = {
    "feed": None,
    "venue_details": {},
    "base_name": None,
    "statcast_by_player_id": {},
    "last_statcast_refresh": 0.0,
}
state_lock = threading.Lock()


def apply_cached_statcast(feed):
    with state_lock:
        metrics = state["statcast_by_player_id"]

    players = import_data.safe_get(feed, "gameData", "players", default={})
    for player_data in players.values():
        player_data["statcast"] = metrics.get(player_data.get("id"), {})


def render():
    """Re-render both HTML pages from whatever's currently in memory. No network calls."""
    importlib.reload(html_report)

    with state_lock:
        feed = state["feed"]
        venue_details = state["venue_details"]
        base_name = state["base_name"]

    if feed is None:
        return

    context = import_data.build_game_context(feed, venue_details)
    context["scorecard"] = import_data.player_scorecard(feed)
    context["scorebug"] = import_data.build_scorebug_context(feed)

    home_file = OUTPUT_DIR / f"{base_name}.html"
    scorecard_file = OUTPUT_DIR / f"{base_name}_scorecard.html"
    context["scorecard_filename"] = scorecard_file.name
    context["home_filename"] = home_file.name

    html_report.write_jinja_html_report(context, scorecard_file, template_name="scorecard.html")
    html_report.write_jinja_html_report(context, home_file, template_name="home.html")
    print(f"Rendered {base_name}")


def render_on_save():
    """Triggered by template/logic file saves -- reload fresh code, render from cached data."""
    importlib.reload(import_data)
    render()


def refresh_live_feed(game_pk):
    """Real network call: game feed + venue. Runs on LIVE_FEED_REFRESH_SECONDS."""
    feed = import_data.get_game_feed(game_pk)
    venue_id = import_data.safe_get(feed, "gameData", "venue", "id")
    venue_details = import_data.get_venue_details(venue_id) if venue_id else {}

    apply_cached_statcast(feed)

    with state_lock:
        state["feed"] = feed
        state["venue_details"] = venue_details
        state["base_name"] = import_data.get_output_filename(feed)

    print("Refreshed live game feed")
    render()


def refresh_statcast():
    """Real network calls: 5 Baseball Savant leaderboard fetches. Runs on STATCAST_REFRESH_SECONDS."""
    with state_lock:
        feed = state["feed"]

    if feed is None:
        return

    season = import_data.safe_get(feed, "gameData", "game", "season")
    metrics = import_data.get_statcast_metrics(season) if season else {}

    with state_lock:
        state["statcast_by_player_id"] = metrics
        state["last_statcast_refresh"] = time.time()
        feed = state["feed"]

    apply_cached_statcast(feed)
    print("Refreshed Statcast leaderboards")
    render()


def background_refresh_loop(game_pk):
    while True:
        time.sleep(LIVE_FEED_REFRESH_SECONDS)
        try:
            refresh_live_feed(game_pk)

            with state_lock:
                statcast_due = (time.time() - state["last_statcast_refresh"]) >= STATCAST_REFRESH_SECONDS

            if statcast_due:
                refresh_statcast()
        except Exception as exc:
            print(f"Background refresh failed: {exc}")


def main():
    game_pk, _clear_cache = import_data.prompt_for_game_selection()
    if game_pk is None:
        return

    print("Fetching initial game data...")
    feed = import_data.get_game_feed(game_pk)
    venue_id = import_data.safe_get(feed, "gameData", "venue", "id")
    venue_details = import_data.get_venue_details(venue_id) if venue_id else {}
    season = import_data.safe_get(feed, "gameData", "game", "season")
    statcast_by_player_id = import_data.get_statcast_metrics(season) if season else {}

    OUTPUT_DIR.mkdir(exist_ok=True)
    with state_lock:
        state["feed"] = feed
        state["venue_details"] = venue_details
        state["base_name"] = import_data.get_output_filename(feed)
        state["statcast_by_player_id"] = statcast_by_player_id
        state["last_statcast_refresh"] = time.time()

    apply_cached_statcast(feed)
    render()

    threading.Thread(target=background_refresh_loop, args=(game_pk,), daemon=True).start()

    server = Server()
    server.watch(str(PROJECT_ROOT / "templates" / "**" / "*.html"), render_on_save)
    server.watch(str(PROJECT_ROOT / "styles" / "*.css"))
    server.watch(str(PROJECT_ROOT / "import_data.py"), render_on_save)
    server.watch(str(PROJECT_ROOT / "html_report.py"), render_on_save)
    server.watch(str(PROJECT_ROOT / "unit_formatters.py"), render_on_save)
    server.watch(str(OUTPUT_DIR / "*.html"))  # catches background-thread renders too

    with state_lock:
        base_name = state["base_name"]

    port = 5502
    url = f"http://127.0.0.1:{port}/output/{base_name}.html"
    print(f"Serving {url}")
    print(f"Live feed refresh: every {LIVE_FEED_REFRESH_SECONDS}s")
    print(f"Statcast refresh: every {STATCAST_REFRESH_SECONDS}s")
    webbrowser.open(url)

    server.serve(root=str(PROJECT_ROOT), port=port, open_url_delay=None)


if __name__ == "__main__":
    main()
