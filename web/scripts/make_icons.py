"""Render the Chop Desk blade mark to PNG app icons (no image libraries needed).

Usage: uv run python web/scripts/make_icons.py web/public
"""

import struct
import sys
import zlib

import numpy as np

BLUE = np.array([42, 120, 214])
WHITE = np.array([255, 255, 255])


def render(size: int, maskable: bool) -> np.ndarray:
    ss = 4  # supersampling for smooth edges
    n = size * ss
    y, x = (np.mgrid[0:n, 0:n] + 0.5) / n  # 0..1
    # Maskable icons must keep content inside the central 80% safe zone and fill the square.
    pad = 0.0 if maskable else 0.06
    r = 0.0 if maskable else 0.22
    lo, hi = pad, 1 - pad
    cx = np.clip(x, lo + r, hi - r)
    cy = np.clip(y, lo + r, hi - r)
    bg = (
        ((x - cx) ** 2 + (y - cy) ** 2 <= r * r + 1e-12)
        & (x >= lo)
        & (x <= hi)
        & (y >= lo)
        & (y <= hi)
    )
    # Logo geometry from the 32x32 SVG (badge spans units 2..30): blade M9 9h14l-14 7z, bar
    # x9 y20 w14 h3. Regular: badge = the rounded square; maskable: badge = the 80% safe zone.
    box_lo, box_hi = (0.1, 0.9) if maskable else (lo, hi)
    s = (box_hi - box_lo) / 28
    ox = oy = box_lo - 2 * s
    X, Y = (x - ox) / s, (y - oy) / s
    blade = (Y >= 9) & (X >= 9) & (X <= 23) & (Y <= 9 + 7 * (23 - X) / 14)
    bar = (X >= 9) & (X <= 23) & (Y >= 20) & (Y <= 23)
    img = np.zeros((n, n, 4))
    img[bg, :3] = BLUE
    img[bg, 3] = 255
    for mask, alpha in ((blade, 0.95), (bar, 0.7)):
        m = mask & bg
        img[m, :3] = img[m, :3] * (1 - alpha) + WHITE * alpha
    return img.reshape(size, ss, size, ss, 4).mean(axis=(1, 3)).round().astype(np.uint8)


def write_png(path: str, rgba: np.ndarray) -> None:
    h, w, _ = rgba.shape
    raw = b"".join(b"\x00" + rgba[i].tobytes() for i in range(h))

    def chunk(tag, data):
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)


out = sys.argv[1]
for size in (192, 512):
    write_png(f"{out}/icon-{size}.png", render(size, maskable=False))
write_png(f"{out}/icon-maskable-512.png", render(512, maskable=True))
write_png(f"{out}/apple-touch-icon.png", render(180, maskable=True))
print("ok")
