import os
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")

env = Environment(
    loader=FileSystemLoader(TEMPLATE_DIR),
    trim_blocks=True,
    lstrip_blocks=True,
)


def write_jinja_html_report(context, html_file, template_name="scorecard.html"):
    template = env.get_template(template_name)
    html = template.render(context)
    html_path = Path(html_file)
    html_path.write_text(html, encoding="utf-8")
    return html_path
