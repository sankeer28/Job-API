"""
Run this after editing any HTML file in api/public/:
    python build_templates.py
It regenerates api/templates.py so Vercel can bundle the HTML.
"""
import os

HTML_DIR = os.path.join("api", "public")
OUT_FILE = os.path.join("api", "templates.py")
FILES = ["index", "docs", "playground"]

lines = ["# AUTO-GENERATED — do not edit by hand.", "# Run: python build_templates.py\n"]

for name in FILES:
    path = os.path.join(HTML_DIR, f"{name}.html")
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    lines.append(f"{name.upper()}_HTML = {repr(content)}\n")

with open(OUT_FILE, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))

print(f"✓ Generated {OUT_FILE} ({os.path.getsize(OUT_FILE):,} bytes)")
