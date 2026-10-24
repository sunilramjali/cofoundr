"""Card payments (owner: Sunil, MVP2): EVO CSV upload, Square + SumUp read-only connectors, storage with card data
stripped, real-data guard. Fake HTTP, no network. Own file so it never conflicts with test_edges/test_middle."""
import csv
import io
import re
from datetime import date, datetime, timezone
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from backend import payments, sales
from backend.ai import forecast
from backend.db import schema
from backend.db.client import RealDataRefused
from backend.inputs import evo, fetch, square, sumup
from backend.inputs.pos.client import PosClient
from data import make_evo_sample

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
CARD = "535522******1234"
SECRETS = (CARD, "GB0000000999999", "1111111N", "EXAMPLE TRADING LTD", "A1B2C3", "ARN99887766", "BR")


def _evo_csv(rows=None, merchant="EXAMPLE TRADING LTD") -> bytes:
    """An EVO export in the real column layout, with sensitive values the importer must never keep."""
    rows = rows or [{}]
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=make_evo_sample.COLUMNS)
    w.writeheader()
    for n, over in enumerate(rows):
        w.writerow({**{c: "" for c in make_evo_sample.COLUMNS}, "TransactionID": f"1234567N{n:014d}", "OutletID": "GB0000000999999",
                    "TerminalID": "1111111N", "Date": "17/10/2026", "Amount": "4.00", "MSCAmount": "0.04", "CardNumber": CARD,
                    "UniqueTransactionReference": "ARN99887766", "CardBrandDesc": "Visa", "ChargeDesc": "Sale Transaction",
                    "CardType": "Consumer", "Product": "Debit", "Region": "Domestic", "Merchant Name": merchant,
                    "Card Issuer Country": "BR", "Transaction currency": "GBP", "Transaction authorization code": "A1B2C3", **over})
    return out.getvalue().encode()


@pytest.fixture
def salt(monkeypatch):
    monkeypatch.setenv("PAYMENT_ID_SALT", "test-salt")


# --- EVO CSV

def test_evo_keeps_only_allow_listed_fields_and_nothing_sensitive_is_stored(db, salt):
    parsed = evo.parse(_evo_csv([{}, {"Region": "Inter", "Product": "Credit", "CardType": "Corporate"}]))
    assert not parsed.synthetic and parsed.rows == 2 and parsed.errors == []
    result = payments.record_payments(parsed.payments, origin="test", synthetic=False, now=NOW)
    assert result.new == 2 and result.revenue == 8.0 and result.fees == 0.08
    stored = list(db[schema.PAYMENTS].find())
    blob = repr(stored)
    assert not any(s in blob for s in SECRETS) and "1234567N" not in blob  # raw transaction id: hashed only
    assert stored[1]["region"] == "international" and stored[1]["card_product"] == "credit" and stored[1]["card_category"] == "corporate"
    assert stored[0]["date"] == "2026-10-17" and stored[0]["paid_at"] is None and stored[0]["synthetic"] is False


def test_reimporting_the_same_file_stores_nothing_twice(db, salt):
    parsed = evo.parse(_evo_csv([{}, {}]))
    payments.record_payments(parsed.payments, origin="test", synthetic=False, now=NOW)
    again = payments.record_payments(parsed.payments, origin="test", synthetic=False, now=NOW)
    assert again.new == 0 and again.duplicates == 2 and db[schema.PAYMENTS].count_documents({}) == 2


def test_bad_rows_are_reported_and_the_rest_imported(db, salt):
    parsed = evo.parse(_evo_csv([{}, {"Date": "2026-10-17"}, {"Amount": "four"}, {"Transaction currency": "EUR"}, {"Date": "01/01/2030"}]))
    assert len(parsed.errors) == 2  # unreadable date and amount: left out by the reader
    result = payments.record_payments(parsed.payments, origin="test", synthetic=False, now=NOW)
    assert result.new == 1 and len(result.errors) == 2  # EUR and a future date rejected by the middle
    assert any("only GBP" in e for e in result.errors) and any("future" in e for e in result.errors)


def test_refunds_count_against_takings_and_other_charges_are_skipped(db, salt):
    parsed = evo.parse(_evo_csv([{"Amount": "10.00"}, {"ChargeDesc": "Refund Transaction", "Amount": "-4.00"},
                                 {"ChargeDesc": "Chargeback"}]))
    result = payments.record_payments(parsed.payments, origin="test", synthetic=False, now=NOW)
    assert result.new == 2 and result.skipped == 1 and result.revenue == 6.0
    takings = payments.daily_takings(db[schema.PAYMENTS].find())
    assert list(takings.values()) == [{"revenue": 6.0, "orders": 1}]


def test_a_file_that_isnt_an_evo_export_is_rejected_clearly():
    with pytest.raises(ValueError, match="doesn't look like an EVO transactions export"):
        evo.parse(b"date,amount\n2026-10-17,4.00\n")


def test_the_committed_sample_is_synthetic_and_imports_cleanly(db):
    parsed = evo.parse(make_evo_sample.OUT.read_bytes())
    assert parsed.synthetic and parsed.errors == [] and parsed.rows > 500
    assert payments.record_payments(parsed.payments, origin="test", synthetic=True, now=NOW).new == parsed.rows


def test_one_unmarked_row_makes_the_whole_file_real(db):
    data = _evo_csv([{"Merchant Name": "GAIA COFFEE (SYNTHETIC)"}, {}], merchant="GAIA COFFEE (SYNTHETIC)")
    assert evo.parse(data).synthetic
    mixed = _evo_csv([{"Merchant Name": "GAIA COFFEE (SYNTHETIC)"}, {"Merchant Name": "EXAMPLE TRADING LTD"}])
    assert not evo.parse(mixed).synthetic


# --- privacy guards

def test_real_data_is_refused_in_the_demo_database(db, salt, monkeypatch):
    monkeypatch.setenv("MONGODB_DB", schema.DB_NAME)
    with pytest.raises(RealDataRefused):
        payments.record_payments(evo.parse(_evo_csv()).payments, origin="test", synthetic=False, now=NOW)
    with pytest.raises(RealDataRefused):
        sales.record_orders([], origin="test", synthetic=False)


def test_real_payment_ids_need_a_secret_salt(db, monkeypatch):
    monkeypatch.delenv("PAYMENT_ID_SALT", raising=False)
    with pytest.raises(ValueError, match="PAYMENT_ID_SALT isn't set"):
        payments.record_payments(evo.parse(_evo_csv()).payments, origin="test", synthetic=False, now=NOW)
    assert db[schema.PAYMENTS].count_documents({}) == 0


def test_unknown_input_fields_are_never_stored(db, salt):
    p = {"source": "sumup", "source_id": "T1", "paid_at": "2026-10-17T09:15:00Z", "amount_gbp": 3.5, "kind": "sale",
         "card_number": CARD, "cardholder": "Jane Doe", "email": "jane@example.com"}
    payments.record_payments([p], origin="test", synthetic=False, now=NOW)
    blob = repr(db[schema.PAYMENTS].find_one())
    assert CARD not in blob and "Jane" not in blob and "example.com" not in blob


# --- dashboard upload

def _web():
    from bridge.main import app
    return TestClient(app, raise_server_exceptions=False)


def test_dashboard_upload_takes_the_synthetic_sample(db):
    r = _web().post("/import/evo", files={"file": ("evo.csv", make_evo_sample.OUT.read_bytes(), "text/csv")},
                    data={"back": "/sales"}, follow_redirects=False)
    assert r.status_code == 303 and "1030 new card payment" in unquote(r.headers["location"]) and db[schema.PAYMENTS].count_documents({}) > 500


def test_dashboard_upload_refuses_a_real_export_on_the_demo_database(db, salt, monkeypatch):
    monkeypatch.setenv("MONGODB_DB", schema.DB_NAME)
    r = _web().post("/import/evo", files={"file": ("evo.csv", _evo_csv(), "text/csv")}, data={"back": "/sales"}, follow_redirects=False)
    assert "real EVO export" in unquote(r.headers["location"]) and db[schema.PAYMENTS].count_documents({}) == 0


def test_dashboard_upload_explains_a_wrong_file(db):
    r = _web().post("/import/evo", files={"file": ("x.csv", b"a,b\n1,2\n", "text/csv")}, data={"back": "/sales"}, follow_redirects=False)
    assert "EVO" in r.headers["location"] and db[schema.PAYMENTS].count_documents({}) == 0


# --- forecast from card payments when there's no till

def test_forecast_uses_card_payments_when_there_are_no_orders(db):
    payments.record_payments(evo.parse(make_evo_sample.OUT.read_bytes()).payments, origin="test", synthetic=True, now=NOW)
    out = forecast.run(now=NOW, use_gemini=False)
    trading = [f["weekday"] for f in out if f["trading"]]
    assert trading == ["Saturday", "Sunday", "Thursday", "Friday"] and out[0]["products"] == []
    assert out[0]["expected_revenue"] > out[3]["expected_revenue"]  # Saturdays busier than Thursdays


# --- Square (read-only, items)

SQ_ORDER = {"id": "ORD1", "location_id": "L1", "closed_at": "2026-10-17T09:30:00Z", "state": "COMPLETED", "customer_id": "CUST-9",
            "line_items": [{"name": "Flat White", "catalog_object_id": "VAR1", "variation_name": "Oat", "quantity": "2",
                            "base_price_money": {"amount": 350, "currency": "GBP"},
                            "modifiers": [{"name": "Extra shot", "base_price_money": {"amount": 50, "currency": "GBP"}}],
                            "gross_sales_money": {"amount": 800, "currency": "GBP"}}],
            "total_discount_money": {"amount": 100, "currency": "GBP"},
            "fulfillments": [{"pickup_details": {"recipient": {"display_name": "Jane Doe", "phone_number": "+447700900123"}}}],
            "tenders": [{"type": "CARD", "card_details": {"card": {"last_4": "1234", "fingerprint": "fp-abc", "card_brand": "VISA"}}}]}


def test_square_order_maps_to_the_pos_format_without_personal_data(db):
    mapped = square.to_pos_order(SQ_ORDER)
    blob = repr(mapped)
    assert not any(s in blob for s in ("Jane", "+44", "CUST-9", "1234", "fp-abc"))
    assert mapped["payment"]["method"] == "card" and mapped["total_gbp"] == 7.0 and mapped["items"][0]["variant"] == "Oat"
    result = sales.record_orders([mapped], origin="test", now=NOW)
    assert result.new == 1 and result.errors == [] and db[schema.SALES].find_one()["qty"] == 2


def test_square_reads_every_page_read_only_with_the_token(db):
    calls = []

    def fake(url, params, source, body=None, headers=None, **_):
        calls.append((url, body, headers))
        if url.endswith("/v2/locations"):
            return {"locations": [{"id": "L1", "status": "ACTIVE"}]}
        return {"orders": [SQ_ORDER], "cursor": "next"} if not body.get("cursor") else {"orders": [{**SQ_ORDER, "id": "ORD2"}]}

    client = square.SquareClient(token="sq-test", env="sandbox", get=fake)
    result, errors, orders = square.sync(client, days=7, now=NOW)
    assert result.new == 2 and errors == [] and client.synthetic
    assert all(h["Authorization"] == "Bearer sq-test" for _, _, h in calls)
    assert {u.rsplit("/v2/", 1)[1] for u, _, _ in calls} == {"locations", "orders/search"}  # nothing that writes


def test_square_needs_a_token_and_reports_non_gbp_orders(monkeypatch):
    monkeypatch.delenv("SQUARE_ACCESS_TOKEN", raising=False)
    with pytest.raises(fetch.FetchError, match="SQUARE_ACCESS_TOKEN"):
        square.SquareClient()
    with pytest.raises(ValueError, match="only GBP"):
        square.to_pos_order({**SQ_ORDER, "total_discount_money": {"amount": 1, "currency": "USD"}})


def test_square_production_data_is_real_and_refused_in_the_demo_database(db, monkeypatch):
    monkeypatch.setenv("MONGODB_DB", schema.DB_NAME)
    client = square.SquareClient(token="t", env="production", location_id="L1", get=lambda *a, **k: {"orders": [SQ_ORDER]})
    with pytest.raises(RealDataRefused):
        square.sync(client, now=NOW)


# --- SumUp (read-only, payments)

SU_ITEMS = [{"id": "TX1", "transaction_code": "TEENSK4W2K", "amount": 3.5, "currency": "GBP", "timestamp": "2026-10-17T08:05:00Z",
             "status": "SUCCESSFUL", "type": "PAYMENT", "card_type": "VISA", "entry_mode": "contactless", "user": "staff@gaia.example.com",
             "product_summary": "Flat White"},
            {"id": "TX2", "amount": 4.0, "currency": "GBP", "timestamp": "2026-10-17T08:10:00Z", "status": "FAILED", "type": "PAYMENT"},
            {"id": "TX3", "amount": 3.5, "currency": "GBP", "timestamp": "2026-10-17T09:00:00Z", "status": "SUCCESSFUL", "type": "REFUND"}]


def test_sumup_pages_through_history_and_drops_personal_fields(db):
    pages = [{"items": SU_ITEMS[:2], "links": [{"rel": "next", "href": "limit=100&order=ascending&oldest_ref=TX2"}]},
             {"items": SU_ITEMS[2:], "links": []}]
    seen = []

    def fake(url, params, source, headers=None, **_):
        seen.append(dict(params))
        return pages.pop(0)

    result, mapped = sumup.sync(sumup.SumUpClient(token="su-test", env="sandbox", get=fake), now=NOW)
    assert result.new == 2 and result.skipped == 1 and result.revenue == 0.0  # sale then refunded
    assert seen[1]["oldest_ref"] == "TX2"
    blob = repr(list(db[schema.PAYMENTS].find()))
    assert "staff@" not in blob and "TEENSK4W2K" not in blob and "Flat White" not in blob
    first = db[schema.PAYMENTS].find_one({"kind": "sale"})
    assert first["entry_mode"] == "contactless" and first["brand"] == "Visa" and first["paid_at"] is not None


def test_no_card_number_shaped_text_survives_any_connector(db, salt):
    payments.record_payments(evo.parse(_evo_csv()).payments, origin="test", synthetic=False, now=NOW)
    payments.record_payments([sumup.to_payment(SU_ITEMS[0])], origin="test", synthetic=False, now=NOW)
    sales.record_orders([square.to_pos_order(SQ_ORDER)], origin="test", now=NOW)
    everything = repr([list(db[c].find()) for c in (schema.PAYMENTS, schema.ORDERS, schema.SALES, schema.AGENT_LOG)])
    assert not re.search(r"\d{6}\*+\d{4}", everything)


# --- post-mortem regressions (Sat 24 Oct, ~23:30)

def test_changing_the_salt_is_refused_instead_of_double_counting(db, salt, monkeypatch):
    parsed = evo.parse(_evo_csv([{}, {}]))
    payments.record_payments(parsed.payments, origin="test", synthetic=False, now=NOW)
    monkeypatch.setenv("PAYMENT_ID_SALT", "another-salt")
    with pytest.raises(ValueError, match="has changed"):
        payments.record_payments(parsed.payments, origin="test", synthetic=False, now=NOW)
    assert db[schema.PAYMENTS].count_documents({}) == 2
    assert "test-salt" not in repr(list(db[schema.COUNTERS].find()))  # only a fingerprint is kept


@pytest.mark.parametrize("data, says", [
    (",".join(make_evo_sample.COLUMNS).encode() + b"\n" + b"x" * 200_000 + b"\n", "doesn't look like an EVO"),  # was a 500
    (bytes(range(256)) * 4, "doesn't look like an EVO"),  # was a raw decode error
    (",".join(make_evo_sample.COLUMNS).encode() + b"\n", "no payments"),  # was "That's a real EVO export"
])
def test_bad_uploads_get_a_plain_message_never_a_crash(db, data, says):
    r = _web().post("/import/evo", files={"file": ("f.csv", data, "text/csv")}, follow_redirects=False)
    assert r.status_code == 303 and says in unquote(r.headers["location"]) and db[schema.PAYMENTS].count_documents({}) == 0


def test_amounts_with_a_pound_sign_or_thousands_comma_are_read():
    parsed = evo.parse(_evo_csv([{"Amount": "£4.00"}, {"Amount": "1,234.50", "MSCAmount": "£3.70"}]))
    assert parsed.errors == [] and [p["amount_gbp"] for p in parsed.payments] == [4.0, 1234.5] and parsed.payments[1]["fee_gbp"] == 3.7


def test_a_negative_sumup_refund_is_stored_as_a_refund(db):
    result = payments.record_payments([sumup.to_payment({**SU_ITEMS[2], "amount": -3.5})], origin="test", synthetic=True, now=NOW)
    assert result.new == 1 and result.revenue == -3.5


def test_a_square_line_without_gross_sales_is_totalled_from_its_price():
    line = {k: v for k, v in SQ_ORDER["line_items"][0].items() if k != "gross_sales_money"}
    mapped = square.to_pos_order({**SQ_ORDER, "line_items": [line]})
    assert mapped["items"][0]["line_total_gbp"] == 8.0 and mapped["total_gbp"] == 7.0


def test_endless_pagination_stops_with_an_error():
    endless = lambda *a, **k: {"items": [], "links": [{"rel": "next", "href": "oldest_ref=same"}]}  # noqa: E731
    with pytest.raises(fetch.FetchError, match="same next page"):
        list(sumup.SumUpClient(token="t", env="sandbox", get=endless).iter_transactions(NOW, NOW))
    loc_calls = []

    def sq(url, *a, **k):
        if url.endswith("/locations"):
            loc_calls.append(url)
            return {"locations": [{"id": "L1"}]}
        return {"orders": [], "cursor": "same"}
    with pytest.raises(fetch.FetchError, match="same cursor"):
        list(square.SquareClient(token="t", env="sandbox", get=sq).iter_orders(NOW, NOW))
    assert len(loc_calls) == 1  # looked up once, not per page
    with pytest.raises(fetch.FetchError, match="no active locations"):
        square.SquareClient(token="t", env="sandbox", get=lambda *a, **k: {"locations": []}).locations()


def test_a_refund_on_a_closed_day_does_not_make_it_a_trading_day():
    days = payments.daily_takings([{"date": "2026-10-20", "kind": "refund", "amount": 4.0},
                                   {"date": "2026-10-24", "kind": "sale", "amount": 5.0}])
    assert list(days) == [date(2026, 10, 24)]


def test_forecast_prefers_months_of_card_payments_over_a_till_connected_yesterday(db):
    payments.record_payments(evo.parse(make_evo_sample.OUT.read_bytes()).payments, origin="test", synthetic=True, now=NOW)
    sales.record_orders(PosClient(now="2026-12-01").iter_sales(since="2026-10-23", until="2026-10-24"), origin="test", now=NOW)
    out = forecast.run(now=NOW, use_gemini=False)
    assert [f["weekday"] for f in out if f["trading"]] == ["Saturday", "Sunday", "Thursday", "Friday"]
    log = db[schema.AGENT_LOG].find_one({"kind": "forecast_updated"})
    assert "card payments" in log["summary"]


def test_synthetic_data_hashes_the_same_whatever_the_local_salt(db, monkeypatch):
    sample = evo.parse(make_evo_sample.OUT.read_bytes()).payments[:3]
    payments.record_payments(sample, origin="Fly, no salt", synthetic=True, now=NOW)
    monkeypatch.setenv("PAYMENT_ID_SALT", "sunils-real-salt")
    again = payments.record_payments(sample, origin="laptop with a salt", synthetic=True, now=NOW)
    assert again.duplicates == 3 and again.new == 0
