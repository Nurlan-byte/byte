"""Build the self-contained offline presentation from local, public assets."""

from base64 import b64encode
from pathlib import Path

ROOT = Path(__file__).resolve().parent
template = (ROOT / "index.template.html").read_text(encoding="utf-8")
assets = {
    "__LOGO_DATA_URI__": ("image/svg+xml", ROOT / "assets/BakePlan-v2-logo.svg"),
    "__CONFIRMED_SHOT_URI__": (
        "image/png",
        ROOT / "screenshots/cgz-confirmed-1920x1080.png",
    ),
}
for placeholder, (mime, path) in assets.items():
    assert template.count(placeholder) == 1, placeholder
    uri = f"data:{mime};base64,{b64encode(path.read_bytes()).decode('ascii')}"
    template = template.replace(placeholder, uri)
assert "__" not in template
(ROOT / "index.html").write_text(template, encoding="utf-8")
print(ROOT / "index.html")
