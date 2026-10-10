"""Draws the Forge mark (dark rounded square, orange inner square) as a 1024px PNG.
Run: uv run --with pillow python desktop/scripts/make-icon.py desktop/assets/icon.png"""
import sys
from PIL import Image, ImageDraw

S = 4  # supersample for smooth corners
size = 1024 * S
img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
# macOS icon grid: 824px body centered on a 1024 canvas.
m = 100 * S
body = size - 2 * m
d.rounded_rectangle((m, m, size - m, size - m), radius=int(body * 0.225), fill="#16150f")
inner = int(body * 14 / 32)
o = (size - inner) // 2
d.rounded_rectangle((o, o, o + inner, o + inner), radius=int(body * 3 / 32), fill="#e2570b")
img.resize((1024, 1024), Image.LANCZOS).save(sys.argv[1])
