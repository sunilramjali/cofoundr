"""Write a SYNTHETIC EVO transactions export for the demo upload: data/samples/evo_transactions_synthetic.csv.

Usage (from the repo root):
    python -m data.make_evo_sample

Same columns as EVO's real "Transactions" CSV, so the importer is tested on the real layout. Everything is fake:
card numbers are 000000******0000, ids are random, and the merchant name ends with "(SYNTHETIC)", which is how
the importer (and the demo server) tell it apart from a real export. Shape only loosely follows a Thu-Sun truck.
Fees follow data/samples/card_fees_synthetic.png, except every OVERCHARGE_EVERY-th payment (planted for the audit).
"""
import csv
import random
from datetime import date, timedelta
from pathlib import Path

OUT = Path(__file__).parent / "samples" / "evo_transactions_synthetic.csv"
COLUMNS = ["TransactionID", "OutletID", "TerminalID", "Date", "Type", "Amount", "MSCAmount", "Scheme Fees", "Margin",
           "CardNumber", "UniqueTransactionReference", "CardBrand", "CardBrandDesc", "ChargeType", "TypeId", "ChargeDesc",
           "CardType", "Product", "Region", "Security", "TransactionReferenceNumber", "Merchant Name",
           "Batch End Date and Time", "DCC transaction", "Card Issuer Country", "Transaction currency",
           "Transaction authorization code", "Deferred Auth", ""]
PAYMENTS_PER_DAY = {3: 30, 4: 40, 5: 90, 6: 80}  # Thu, Fri, Sat, Sun
PRICES = [3.0, 3.2, 3.5, 3.8, 4.0, 4.2, 6.5, 7.0, 7.8, 9.5]
RATES = {("Consumer", "Debit"): 0.003, ("Consumer", "Credit"): 0.006, ("Corporate", "Debit"): 0.012, ("Corporate", "Credit"): 0.012}


OVERCHARGE_EVERY = 250  # every 250th payment is charged 15p too much: the fee audit (backend/ai/fees.py) must find them
OVERCHARGE = 0.15


def rows(start: date = date(2026, 9, 24), end: date = date(2026, 10, 23), seed: int = 7):
    rng = random.Random(seed)
    day, n = start, 0
    while day <= end:
        for _ in range(PAYMENTS_PER_DAY.get(day.weekday(), 0)):
            n += 1
            amount = rng.choice(PRICES)
            card_type = "Corporate" if rng.random() < 0.02 else "Consumer"
            product = "Credit" if rng.random() < 0.15 else "Debit"
            region = rng.choices(["Domestic", "Intra", "Inter"], [88, 6, 6])[0]
            brand = rng.choice(["Visa", "MasterCard"])
            tid = f"{rng.randrange(10**6, 10**7)}N{rng.randrange(10**13, 10**14)}"
            yield {"TransactionID": tid, "OutletID": "GB0000000000000", "TerminalID": "0000000X", "Date": day.strftime("%d/%m/%Y"),
                   "Type": "00", "Amount": f"{amount:.2f}", "MSCAmount": f"{amount * RATES[(card_type, product)] + 0.03 + (OVERCHARGE if n % OVERCHARGE_EVERY == 0 else 0):.2f}",
                   "Scheme Fees": "", "Margin": "", "CardNumber": "000000******0000", "UniqueTransactionReference": f"SY{tid}",
                   "CardBrand": "2" if brand == "Visa" else "3", "CardBrandDesc": brand, "ChargeType": "200", "TypeId": "00",
                   "ChargeDesc": "Sale Transaction", "CardType": card_type, "Product": product, "Region": region,
                   "Security": "Secure", "TransactionReferenceNumber": tid, "Merchant Name": "GAIA COFFEE (SYNTHETIC)",
                   "Batch End Date and Time": day.strftime("%d/%m/%Y"), "DCC transaction": "NO", "Card Issuer Country": "GB",
                   "Transaction currency": "GBP", "Transaction authorization code": "000000", "Deferred Auth": "False", "": ""}
        day += timedelta(days=1)


def main() -> None:
    OUT.parent.mkdir(exist_ok=True)
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows())
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
