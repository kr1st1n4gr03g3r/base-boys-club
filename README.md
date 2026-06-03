# ⚾ Base Boys Club

A Python project that pulls completed MLB game data from the MLB Stats API and generates baseball game reports for analysis.

The project creates:

- 📄 Markdown reports for saving, archiving, and feeding into AI tools later
- 🌎 HTML previews for easier browser-based visual review
- 📊 Pitch-by-pitch breakdowns, lineups, pitching totals, weather, park info, scoring plays, and more

## What This Project Does

`base-boys-club` uses the public MLB Stats API to fetch completed game data and generate a detailed report.

The report currently includes:

- Game date and time
- Teams and final score
- Weather
- Ballpark information
- Team logos
- Lineups
- Pitching totals
- Pitching decisions
- At-bat results
- Extra runner results
- Pitch-by-pitch plate appearance data
- Batted-ball data such as exit velocity, distance, launch angle, and trajectory

## Project Structure

```text
base-boys-club/
├── import_data.py
├── unit_formatters.py
├── html_report.py
├── templates/
│   └── report_template.html
├── styles/
│   └── report.css
├── output/
├── requirements.txt
├── requirements-dev.txt
├── .gitignore
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

Recommended `requirements.txt`:

```txt
requests
markdown
```

Recommended `requirements-dev.txt`:

```txt
ruff
```

## Run the Program

From the project root:

```bash
python3 import_data.py
```

You will be prompted for:

```text
Game date (YYYY-MM-DD):
Home team, e.g. Orioles:
Away team, e.g. Blue Jays:
```

Example:

```text
Game date (YYYY-MM-DD): 2026-05-31
Home team, e.g. Orioles: orioles
Away team, e.g. Blue Jays: jays
```

The program then generates a report in the `output/` folder.

## Output Options

The CLI can ask what kind of output you want:

```text
Would you like:
a) 📁 A markdown file saved to the /output folder?
b) 🌎 The browser to open the report?
c) 🎉 Both, please
```

Recommended use:

- Choose `a` when you want to save a Markdown report for later AI analysis
- Choose `b` when you are testing the browser preview
- Choose `c` when you want both

## Markdown vs HTML

The project separates the report into two useful formats.

### 📄 Markdown

The Markdown report is the main archive format.

It is useful for:

- Saving completed game reports
- Reading the raw report text
- Feeding the report into AI later
- Keeping the data portable and simple

### 🌎 HTML

The HTML report is for visual inspection.

It is useful for:

- Browser preview
- Styling with CSS
- Better readability while analyzing games
- Testing layout ideas

The HTML preview is generated from the Markdown report using `html_report.py`.

## Useful Testing Shortcut

You can create a file called `test_input.txt`:

```text
2026-05-31
orioles
jays
b
```

Then run:

```bash
python3 import_data.py < test_input.txt
```

This feeds the prompts automatically while testing.

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

## Git Ignore Suggestions

The project should ignore generated files, local environment files, and Python clutter:

```gitignore
output/
.DS_Store
__pycache__/
*.pyc
.venv/
```

## Notes

This project is designed for completed MLB games, not live games.

The Markdown output is intended to remain useful for analysis, while the HTML output can become more visual and styled over time.
