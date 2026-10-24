"""Render SYNTHETIC invoice images for testing Gemini extraction.

Usage (from the repo root):
    python -m data.make_samples

Writes PNGs to data/samples/. All businesses and emails are fictional.
One invoice deliberately has line items that don't add up to the total,
so the verification check has something to catch.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = Path(__file__).parent / "samples"
FONT_DIR = Path("/System/Library/Fonts/Supplemental")

SAMPLES = [
    {
        "file": "supplier_clean.png",  # payable, clean
        "issuer": "Eastside Dairy Co", "issuer_lines": ["Unit 4, Fish Island, London E3", "billing@eastsidedairy.example.com"],
        "to": "GAIA Coffee", "number": "EDC-7802", "issued": "17 Oct 2026", "due": "07 Nov 2026",
        "items": [("Whole milk, 2L", 30, 1.85), ("Oat milk, 1L", 48, 2.10), ("Single cream, 1L", 6, 3.40)],
        "total": None,
    },
    {
        "file": "gaia_overdue.png",  # receivable, overdue
        "issuer": "GAIA Coffee", "issuer_lines": ["Mobile coffee, East London", "hello@gaiacoffee.example.com"],
        "to": "Hackney Hub Coworking (accounts@hackneyhub.example.com)", "number": "GAIA-1050",
        "issued": "26 Sep 2026", "due": "10 Oct 2026",
        "items": [("Office coffee cart, 1 day", 1, 180.00), ("Pastry platter", 1, 45.00)],
        "total": None,
    },
    {
        "file": "supplier_messy.png",  # payable, total does NOT match line items, blurred
        "issuer": "Bow Lane Roasters", "issuer_lines": ["Arch 12, Bow, London E3", "orders@bowlaneroasters.example.com"],
        "to": "GAIA Coffee", "number": "BLR-2231", "issued": "20 Oct 2026", "due": "30 Oct 2026",
        "items": [("House espresso blend, 1kg", 10, 19.50), ("Decaf, 1kg", 2, 21.00)],
        "total": 263.00,  # real sum is 237.00
        "blur": 1.1,
    },
]


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(str(FONT_DIR / name), size)
    except OSError:
        return ImageFont.load_default(size=size)


def render(s: dict) -> Image.Image:
    img = Image.new("RGB", (900, 1100), "white")
    d = ImageDraw.Draw(img)
    big, bold, reg, small = _font("Arial Bold.ttf", 40), _font("Arial Bold.ttf", 22), _font("Arial.ttf", 22), _font("Arial.ttf", 16)

    d.text((60, 50), s["issuer"], font=big, fill="black")
    for i, line in enumerate(s["issuer_lines"]):
        d.text((60, 105 + i * 28), line, font=reg, fill="#444")
    d.text((620, 55), "INVOICE", font=big, fill="#1f4e79")

    y = 210
    for label, value in [("Invoice no.", s["number"]), ("Issue date", s["issued"]), ("Due date", s["due"]), ("Bill to", s["to"])]:
        d.text((60, y), label, font=bold, fill="black")
        d.text((240, y), value, font=reg, fill="black")
        y += 36

    y += 30
    d.rectangle([60, y, 840, y + 40], fill="#e8eef5")
    for x, h in [(70, "Description"), (520, "Qty"), (620, "Unit £"), (740, "Amount £")]:
        d.text((x, y + 8), h, font=bold, fill="black")
    y += 50
    total = 0.0
    for desc, qty, unit in s["items"]:
        amount = qty * unit
        total += amount
        d.text((70, y), desc, font=reg, fill="black")
        d.text((520, y), f"{qty:g}", font=reg, fill="black")
        d.text((620, y), f"{unit:.2f}", font=reg, fill="black")
        d.text((740, y), f"{amount:.2f}", font=reg, fill="black")
        y += 38
    d.line([60, y + 10, 840, y + 10], fill="black", width=2)
    shown_total = s["total"] if s["total"] is not None else total
    d.text((560, y + 25), "TOTAL DUE", font=bold, fill="black")
    d.text((740, y + 25), f"£{shown_total:.2f}", font=bold, fill="black")

    d.text((60, 1040), "SYNTHETIC DEMO DATA. Not a real invoice.", font=small, fill="#999")
    if s.get("blur"):
        img = img.rotate(1.5, expand=False, fillcolor="white").filter(ImageFilter.GaussianBlur(s["blur"]))
    return img


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for s in SAMPLES:
        render(s).save(OUT / s["file"])
        print(f"wrote data/samples/{s['file']}")


if __name__ == "__main__":
    main()
