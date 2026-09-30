"""Generate PWA icons into src/web/static (dev-only tool, needs Pillow).

  python tools/make_icons.py
apple-touch-icon.png 180 (iOS home screen, opaque, iOS rounds the corners itself), icons/icon-192.png,
icons/icon-512.png, icons/maskable-512.png (content inside the 80 % safe zone), favicon.png 64.
"""
import os

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "src", "web", "static")
BG = (15, 17, 23)
GREEN = (20, 241, 149)
PURPLE = (153, 69, 255)


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def draw(size: int, scale: float = 1.0) -> Image.Image:
    s = size * 4                                  # supersample for smooth edges
    img = Image.new("RGB", (s, s), BG)
    d = ImageDraw.Draw(img)
    c, r = s / 2, s * 0.36 * scale
    w = max(4, int(s * 0.055 * scale))
    # gradient ring (purple -> green) drawn as arcs
    steps = 120
    for i in range(steps):
        a0, a1 = 360 * i / steps - 90, 360 * (i + 1) / steps - 90 + 0.8
        d.arc([c - r, c - r, c + r, c + r], a0, a1, fill=lerp(PURPLE, GREEN, i / steps), width=w)
    # crosshair ticks
    for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
        x0, y0 = c + dx * r * 0.62, c + dy * r * 0.62
        x1, y1 = c + dx * r * 1.18, c + dy * r * 1.18
        d.line([x0, y0, x1, y1], fill=GREEN, width=int(w * 0.8))
    # rising signal line inside
    pts = [(c - r * 0.55, c + r * 0.30), (c - r * 0.18, c + r * 0.05), (c + r * 0.05, c + r * 0.22),
           (c + r * 0.52, c - r * 0.38)]
    d.line(pts, fill=GREEN, width=int(w * 0.9), joint="curve")
    tip = pts[-1]
    d.ellipse([tip[0] - w, tip[1] - w, tip[0] + w, tip[1] + w], fill=GREEN)
    return img.resize((size, size), Image.LANCZOS)


def main():
    os.makedirs(os.path.join(OUT, "icons"), exist_ok=True)
    draw(180).save(os.path.join(OUT, "apple-touch-icon.png"))
    draw(64).save(os.path.join(OUT, "favicon.png"))
    draw(192).save(os.path.join(OUT, "icons", "icon-192.png"))
    draw(512).save(os.path.join(OUT, "icons", "icon-512.png"))
    draw(512, scale=0.78).save(os.path.join(OUT, "icons", "maskable-512.png"))
    print("icons written to", OUT)


if __name__ == "__main__":
    main()
