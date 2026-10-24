"""Render a SYNTHETIC card fee summary (the layout UK card providers must send, Payment Systems Regulator style).

Usage (from the repo root):
    python -m data.make_fee_sample

Writes data/samples/card_fees_synthetic.png. The provider, contract and every number are made up, and the image is
marked SYNTHETIC; it matches data/samples/evo_transactions_synthetic.csv, where a few payments are deliberately
overcharged so the fee audit has something to catch.
"""
from pathlib import Path

from PIL import Image, ImageDraw

from data.make_samples import _font

OUT = Path(__file__).parent / "samples" / "card_fees_synthetic.png"
RATES = [("Debit", "0.30% + £0.03", "0.70% + £0.03"), ("Credit", "0.60% + £0.03", "1.00% + £0.03"),
         ("Business", "1.20% + £0.03", "1.60% + £0.03")]


def render() -> Image.Image:
    img = Image.new("RGB", (1000, 900), "white")
    d = ImageDraw.Draw(img)
    big, bold, reg, small = _font("Arial Bold.ttf", 34), _font("Arial Bold.ttf", 20), _font("Arial.ttf", 19), _font("Arial.ttf", 15)
    d.rectangle([0, 0, 1000, 44], fill="#b3261e")
    d.text((24, 10), "SYNTHETIC DEMO DOCUMENT: not a real contract", font=bold, fill="white")
    d.text((40, 70), "Your card payments summary", font=big, fill="black")
    d.text((40, 118), "Card provider: EXAMPLE PAYMENTS (SYNTHETIC)   ·   Merchant: GAIA COFFEE (SYNTHETIC)", font=reg, fill="#333")
    d.text((40, 148), "Your contract ends on 30 June 2027   ·   Merchant category code 5812", font=reg, fill="#333")
    d.text((40, 200), "Your costs for accepting common individual card payments (UK-issued cards)", font=bold, fill="black")
    y = 240
    for col, x in (("Card", 40), ("In person", 300), ("Online / phone", 560)):
        d.text((x, y), col, font=bold, fill="black")
    for card, person, online in RATES:
        y += 36
        d.line([40, y - 6, 960, y - 6], fill="#ddd")
        for text, x in ((card, 40), (person, 300), (online, 560)):
            d.text((x, y), text, font=reg, fill="black")
    y += 60
    d.text((40, y), "Additional charges per transaction", font=bold, fill="black")
    d.text((40, y + 32), "Authorisation fee: 3p per transaction", font=reg, fill="black")
    d.text((40, y + 62), "Chargeback fee: £30.00", font=reg, fill="black")
    d.text((40, y + 92), "Minimum monthly service charge: £10.00", font=reg, fill="black")
    y += 150
    d.text((40, y), "Example", font=bold, fill="black")
    d.text((40, y + 32), "A customer pays £10.00 with a personal debit card in person:", font=reg, fill="black")
    d.text((40, y + 62), "0.30% of £10.00 + £0.03 = 6p, plus 3p authorisation fee = 9p", font=reg, fill="black")
    d.text((40, y + 92), "A customer pays £10.00 with a personal credit card online:", font=reg, fill="black")
    d.text((40, y + 122), "1.00% of £10.00 + £0.03 = 13p, plus 3p authorisation fee = 16p", font=reg, fill="black")
    d.text((40, 860), "Settlement: transaction date + 1 working day. All figures are invented for a demo.", font=small, fill="#666")
    return img


def main() -> None:
    OUT.parent.mkdir(exist_ok=True)
    render().save(OUT)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
