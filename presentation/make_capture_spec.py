"""Create a short real-UI screenshot run from the public synthetic demo."""
from pathlib import Path

root = Path(__file__).resolve().parent.parent
source = (root / "video/cgz-capture.spec.ts").read_text(encoding="utf-8")
source = source.replace("viewport: { width: 1440, height: 1000 }", "viewport: { width: 1920, height: 1250 }")
source = source.replace("deviceScaleFactor: 1", "deviceScaleFactor: 2")
source = source.replace("if (remaining > 0) await page.waitForTimeout(remaining);", "if (remaining > 0) await page.waitForTimeout(1);")
source = source.replace(
    "    scene('six proposed orders');",
    "    await page.getByTestId('procurement-table').screenshot({ path: 'presentation/screenshots/cgz-six-proposed.png' });\n"
    "    scene('six proposed orders');",
)
source = source.replace(
    "    scene('confirmed order');",
    "    await page.getByTestId('procurement-table').screenshot({ path: 'presentation/screenshots/cgz-six-confirmed.png' });\n"
    "    return;\n"
    "    scene('confirmed order');",
)
(root / "video/cgz-pitch-still.spec.ts").write_text(source, encoding="utf-8")
