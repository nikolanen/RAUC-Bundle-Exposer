"""Generates the icons. Requires Pillow.

app.ico        icon of the .exe file (Windows only, used by the build)
app_<n>.png    window icon in several sizes (all platforms) and app_96.png for the About dialog
"""

from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
S = 1024  # draw large, then downscale for smooth edges


def render() -> Image.Image:
    # Rounded square with a vertical blue gradient
    grad = Image.new("RGBA", (S, S))
    gd = ImageDraw.Draw(grad)
    top, bottom = (59, 130, 246), (30, 64, 175)
    for y in range(S):
        t = y / (S - 1)
        gd.line([(0, y), (S, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([32, 32, S - 32, S - 32], radius=230, fill=255)
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    img.paste(grad, (0, 0), mask)

    d = ImageDraw.Draw(img)
    white = (255, 255, 255, 255)
    cx = S // 2

    # Signal arcs above the arrow (network / broadcast)
    for r, width in ((150, 52), (250, 52)):
        d.arc([cx - r, 330 - r, cx + r, 330 + r], start=215, end=325, fill=white, width=width)

    # Down arrow
    d.rounded_rectangle([cx - 52, 300, cx + 52, 600], radius=26, fill=white)
    d.polygon([(cx - 175, 540), (cx + 175, 540), (cx, 730)], fill=white)

    # Tray
    lw, x0, x1, y0, y1 = 72, 220, S - 220, 640, 820
    d.rounded_rectangle([x0, y0, x0 + lw, y1], radius=lw // 2, fill=white)
    d.rounded_rectangle([x1 - lw, y0, x1, y1], radius=lw // 2, fill=white)
    d.rounded_rectangle([x0, y1 - lw, x1, y1], radius=lw // 2, fill=white)
    return img


# Window icon sizes (title bar, taskbar, Alt+Tab / docks) + 96 px for the About dialog
PNG_SIZES = (16, 32, 48, 96, 256)


def main():
    img = render()
    big = img.resize((256, 256), Image.LANCZOS)
    # .ico is only used for the .exe file icon (PyInstaller --icon)
    big.save(HERE / "app.ico", sizes=[(s, s) for s in (16, 20, 24, 32, 40, 48, 64, 128, 256)])
    for size in PNG_SIZES:
        img.resize((size, size), Image.LANCZOS).save(HERE / f"app_{size}.png")
    print("Icons written to", HERE)


if __name__ == "__main__":
    main()
