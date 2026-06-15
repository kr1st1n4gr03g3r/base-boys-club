import os
from pathlib import Path

import markdown
from jinja2 import Environment, FileSystemLoader

TEMPLATE_FILE = Path("templates/report_template.html")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")

env = Environment(
    loader=FileSystemLoader(TEMPLATE_DIR),
    trim_blocks=True,  # Removes the first newline after a block
    lstrip_blocks=True,  # Strips leading spaces/tabs from a block line
)


def write_jinja_html_report(context, html_file):
    template = env.get_template("scorecard.html")
    html = template.render(context)
    html_path = Path(html_file)
    html_path.write_text(html, encoding="utf-8")
    return html_path


def get_html_output_file(markdown_file):
    markdown_path = Path(markdown_file)
    return markdown_path.with_suffix(".html")


def convert_markdown_to_html(markdown_text):
    return markdown.markdown(
        markdown_text,
        extensions=["tables", "fenced_code"],
    )


def write_html_preview(markdown_file, html_file=None, title="Baseball Report"):
    markdown_path = Path(markdown_file)

    if html_file is None:
        html_path = get_html_output_file(markdown_path)
    else:
        html_path = Path(html_file)

    markdown_text = markdown_path.read_text(encoding="utf-8")
    report_body = convert_markdown_to_html(markdown_text)

    template_text = TEMPLATE_FILE.read_text(encoding="utf-8")

    html_page = template_text.replace("{{ title }}", title).replace(
        "{{ report_body }}", report_body
    )

    html_path.write_text(html_page, encoding="utf-8")

    return html_path
