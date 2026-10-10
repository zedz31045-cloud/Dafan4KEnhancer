"""Generate assets/icon.ico (and a PNG) with Pillow - no external artwork needed."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parents[1] / "assets"


def draw(size: int) -> Image.Image:
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    r = size // 5
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=r, fill=(7, 11, 20, 255),
                        outline=(34, 211, 238, 255), width=max(2, size // 32))
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", int(size * 0.46))
    except OSError:
        font = ImageFont.load_default()
    text = "4K"
    box = d.textbbox((0, 0), text, font=font)
    d.text(((size - (box[2] - box[0])) / 2 - box[0], (size - (box[3] - box[1])) / 2 - box[1]),
           text, font=font, fill=(34, 211, 238, 255))
    return im


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    big = draw(256)
    big.save(OUT / "icon.png")
    big.save(OUT / "icon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("icon written to", OUT)
