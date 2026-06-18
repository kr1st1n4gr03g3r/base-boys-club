import webbrowser
from pathlib import Path

import requests

response = requests.get("https://statsapi.mlb.com/api/v1/eventTypes", timeout=30)
response.raise_for_status()

rows = ""
for item in response.json():
    code = item.get("code", "")
    abbreviation = item.get("shortDescription", "")
    description = item.get("description", "")
    rows += f"<tr><td>{code}</td><td>{abbreviation}</td><td>{description}</td></tr>\n"

html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <title>MLB Event Types</title>
    <style>
        body {{ font-family: monospace; padding: 2rem; }}
        table {{ border-collapse: collapse; width: 100%; }}
        th, td {{ border: 1px solid #ccc; padding: 0.4rem 0.8rem; text-align: left; }}
        th {{ background: #f0f0f0; }}
    </style>
</head>
<body>
    <h1>MLB Event Types</h1>
    <table>
        <thead>
            <tr><th>Code</th><th>Abbreviation</th><th>Description</th></tr>
        </thead>
        <tbody>
            {rows}
        </tbody>
    </table>
</body>
</html>"""

output = Path("reference/event-types.html")
output.write_text(html, encoding="utf-8")
webbrowser.open(output.resolve().as_uri())
print(f"Opened: {output}")
