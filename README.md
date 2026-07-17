# ⚾ Base Boys Club

A Python project that pulls MLB game data from the MLB Stats API and renders it as a per-game player scorecard in the browser.

## What This Project Does

Run `import_data.py`, give it a game (by date + teams, or directly by `gamePk`), and it:

1. Pulls the live game feed from the **MLB Stats API** (`statsapi.mlb.com`) - lineups, play-by-play, boxscore.
2. Pulls venue details (timezone, field dimensions, roof type).
3. Attaches Statcast season metrics per player from **Baseball Savant** (`baseballsavant.mlb.com`) - xBA, xSLG, xwOBA, xERA, barrel%, hard-hit%, exit velocity, sprint speed, plate discipline. (Collected into the JSON; not yet shown on the scorecard itself - see Future Improvements.)
4. Writes the full game JSON to `output/`.
5. Renders a scorecard as HTML (via `templates/scorecard.html`) and opens it in your browser.

### The Scorecard

For each lineup slot and inning, the scorecard shows:

- **Ball/strike count** - filled/empty squares for the count at the end of that plate appearance.
- **Diamond icon** - which base the batter reached (`B1` for now; `B2`/`B3`/`HOMERUN` icons exist in `templates/icons/` but aren't wired into the logic yet).
- **Result shorthand** - a compact scorecard-style code for how the at-bat ended:

| Code | Meaning |
|---|---|
| `K` | Strikeout swinging |
| `ꓘ` | Strikeout looking |
| `HR` | Home run |
| `DP` / `TP` | Double play / triple play |
| `G`, `F`, `L`, `P` | Groundout, flyout, lineout, pop out (followed by the fielding sequence, e.g. `G: 6-3`) |
| `B1`, `B2`, `B3` | Single, double, triple (followed by the fielder, e.g. `B1: 7`) |
| `HBP` | Hit by pitch |
| `BB` | Walk |
| `IW` | Intentional Walk |
| `Err` | Reached on a fielding error or fielder's choice |
| `WILD` | Reached first on a dropped third strike / wild pitch |

Not yet handled: catcher's interference, fielder obstruction, and a batted ball striking a runner or umpire (all still reach base, but aren't classified yet - see `get_diamond_icon` in `import_data.py`).

## Project Structure

```text
base-boys-club/
├── import_data.py          # main entry point - fetch, enrich, render, open
├── enrichment.py           # per-game enrichment blocks (WIP, disabled by default - see below)
├── html_report.py          # Jinja2 HTML scorecard renderer
├── unit_formatters.py       # distance/temperature/wind display formatting
├── templates/
│   ├── scorecard.html      # the only template currently rendered
│   └── icons/               # B1/B2/B3/HOMERUN/RUN/DEFAULT.svg diamond icons
├── styles/
│   └── main.css
├── reference/
│   ├── query-event-types.py # scans real games to keep eventTypes.md up to date
│   └── eventTypes.md        # event/eventType pairs observed in real game feeds
├── output/                  # generated .json and .html files (gitignored)
├── .cache/                  # disk cache for enrichment API responses (gitignored)
├── notes.md                 # enrichment roadmap (gitignored, local only)
├── requirements.txt
├── requirements-dev.txt
└── README.md
```

## Setup

Create a virtual environment:

```bash
python3 -m venv .venv
```

Activate it:

```bash
source .venv/bin/activate
```

Install dependencies:

```bash
python3 -m pip install -r requirements.txt
```

Optional, install development tools:

```bash
python3 -m pip install -r requirements-dev.txt
```

## Dependencies

`requirements.txt`:

```txt
requests
jinja2
pybaseball
tqdm
```

`requirements-dev.txt`:

```txt
ruff
```

## Run the Program

From the project root:

```bash
python3 import_data.py
```

You'll be asked whether to clear the API response cache, then for a game date or a direct MLB game number (`gamePk`):

```text
Clear previous cache? (Y/N):
Game date (YYYY-MM-DD) or baseball game number:
```

**By game number** (fastest):

```text
Game date (YYYY-MM-DD) or baseball game number: 824750
```

**By date** (prompts for home and away team):

```text
Game date (YYYY-MM-DD) or baseball game number: 2026-05-31
Home team, e.g. Orioles: Orioles
Away team, e.g. Blue Jays: Blue Jays
```

The program writes two files to `output/`:

- `<date>_<away>_at_<home>.json` - full game data (feed + Statcast metrics)
- `<date>_<away>_at_<home>.html` - the scorecard, opened automatically in the browser

## Reference / Tooling

`reference/query-event-types.py` scans every MLB game from the last 30 days (all teams) and appends any newly observed `event`/`eventType` pairs to `reference/eventTypes.md` - the pairs are what drive the shorthand-code logic in `import_data.py` (`get_result_type_shorthand`, `get_diamond_icon`). It only adds pairs not already recorded, so re-running it later is cheap and never duplicates entries.

```bash
python3 reference/query-event-types.py
```

## Development Tools

Format and lint with Ruff:

```bash
ruff check import_data.py
ruff format import_data.py
```

To check for Python syntax errors:

```bash
python3 -m py_compile import_data.py
```

## Future Improvements & Experiments

Everything below exists in the codebase but isn't part of the scorecard yet - either disabled, unwired, or unused placeholders for later work.

- **Per-game enrichment (`enrichment.py`)** - pulls platoon splits, recent form, batter-vs-pitcher history, pitcher workload, and bullpen usage into the game JSON. Currently **disabled by default** via `SKIP_ENRICHMENT = True` in `import_data.py`. See `notes.md` for the full block-by-block roadmap (pitch arsenal, catcher framing, park factors, FanGraphs stats via `pybaseball`, and more - none of it implemented yet).
- **Statcast metrics** - already attached to every player in the output JSON on every run, but not displayed anywhere on the scorecard yet.
- **`templates/play-by-play.html`** - an empty placeholder template for a future play-by-play view; not rendered by anything.
- **`styles/team-colours.json`** - MLB team hex color reference; not wired into any styling yet.
