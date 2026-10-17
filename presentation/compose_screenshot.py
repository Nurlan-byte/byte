"""Crop repeated guidance from a real screenshot; preserve its text pixels."""
from pathlib import Path
from PIL import Image

root = Path(__file__).resolve().parent
source = Image.open(root / "screenshots/cgz-six-confirmed.png").convert("RGB")
assert source.size == (2448, 1912)
columns = [(0, 340), (350, 790), (790, 875), (1940, 2070)]
starts = [92, 404, 700, 996, 1292, 1602]
card_width = sum(b - a for a, b in columns)
card_height = 170
gap = 38
out = Image.new("RGB", (2 * card_width + gap, 3 * card_height), (255, 254, 250))

def copy_band(top, height, output_left, output_top):
    x = output_left
    for index, (left, right) in enumerate(columns):
        if index == 2:
            out.paste(source.crop((left, top + 22, right, top + 75)), (x, output_top + 22))
        elif index == 3:
            out.paste(source.crop((left, top + 68, right, top + 145)), (x, output_top + 38))
        else:
            out.paste(source.crop((left, top, right, top + height)), (x, output_top))
        x += right - left

for index, top in enumerate(starts):
    left = 0 if index < 3 else card_width + gap
    y = (index % 3) * card_height
    copy_band(top, card_height, left, y)
out.save(root / "screenshots/cgz-six-compact.png")
