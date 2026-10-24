"""Edges (owner: Jade): web app routes, Saint endpoints, owner-facing messages. Fast, no MongoDB/Gemini needed."""
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from bridge.main import app


def test_health_and_token_guard():
    client = TestClient(app)
    assert client.get("/health").json() == {"ok": True}
    assert client.get("/brief").status_code == 403
    assert client.post("/approve", json={"text": "approve 1"}).status_code == 403
    assert client.get("/updates").status_code == 403


def test_update_messages_are_whatsapp_ready():
    from backend.outputs.updates import approval_message, invoice_message
    ok = invoice_message({"invoice_number": "EDC-1", "counterparty": "Eastside Dairy Co", "amount": 176.7,
                          "due_date": datetime(2026, 11, 7, tzinfo=timezone.utc), "needs_review": False})
    assert "EDC-1" in ok and "£176.70" in ok and "07 Nov" in ok and "✓" in ok
    bad = invoice_message({"invoice_number": "BLR-1", "counterparty": "Bow Lane", "amount": 263.0, "needs_review": True,
                           "verification_issues": ["Line items add up to 237.00 but the printed total is 263.00."]})
    assert bad.startswith("⚠️") and "237.00" in bad
    msg = approval_message({"ref": 4, "title": "Chase Hackney Hub Coworking: £450.00, 3 days overdue"})
    assert "approve 4" in msg and "reject 4" in msg


# ---------------------------------------------------------------- scenarios (in-memory DB, fake Gemini)
from urllib.parse import unquote  # noqa: E402

import pytest  # noqa: E402

from backend.ai import overdue  # noqa: E402
from backend.db import client as dbclient, schema  # noqa: E402
from bridge.main import parse_reply  # noqa: E402
from tests.conftest import TOKEN, make_invoice  # noqa: E402

web = TestClient(app)


def _flash(r):
    return unquote(r.headers.get("location", "").split("msg=")[-1])


# --- WhatsApp reply parsing: only an unambiguous single command can move money
@pytest.mark.parametrize("text,expected", [
    ("approve 1", ("approve", 1)), ("Approve #2!", ("approve", 2)), ("  yes 3. ", ("approve", 3)), ("OK 4", ("approve", 4)),
    ("approved 5", ("approve", 5)), ("reject 2", ("reject", 2)), ("No 7", ("reject", 7)), ("REJECTED #8", ("reject", 8)),
])
def test_reply_parser_accepts_clear_commands(text, expected):
    assert parse_reply(text) == expected


@pytest.mark.parametrize("text", [
    "don't approve 1", "do not approve 1", "approve 1 and 2", "approve the £450 one", "approve", "1", "",
    "approve -1", "approve 1.5", "yes please approve 3", "approve 1234567", "nope 3",
])
def test_reply_parser_refuses_negated_or_ambiguous_messages(text):
    assert parse_reply(text) is None


# --- /approve (Saint relays the owner's WhatsApp reply)
def test_whatsapp_approve_flow_and_errors(db, gemini_down, sent):
    overdue.run()
    post = lambda body, **kw: web.post("/approve", params={"token": TOKEN}, json=body, **kw)  # noqa: E731
    assert post({"text": "don't approve 1"}).status_code == 400
    assert db[schema.APPROVALS].find_one({"ref": 1})["status"] == "pending"  # the negation moved nothing
    assert post({"text": "approve 1 and 2"}).status_code == 400
    assert post({"ref": "abc", "decision": "approve"}).status_code == 400
    assert post({"ref": True, "decision": "approve"}).status_code == 400
    assert post({"ref": 1, "decision": "edit"}).status_code == 400
    assert post({"ref": 999, "decision": "approve"}).status_code == 404
    r = post({"text": "Approve #1"})
    assert r.status_code == 200 and r.json()["status"] == "approved" and r.json()["reply"].startswith("✅ #1")
    assert post({"text": "approve 1"}).status_code == 409  # already decided
    r = post({"ref": "2", "decision": "reject"})
    assert r.status_code == 200 and r.json()["reply"].startswith("❌ #2")


def test_token_guard_handles_odd_tokens(db):
    for bad in ["", "wrong", "tést-ünïcode", TOKEN + "x"]:
        assert web.get("/brief", params={"token": bad}).status_code == 403
    assert web.get("/brief", headers={"x-token": TOKEN}).status_code == 200


# --- dashboard
def test_dashboard_renders_with_real_data(db, gemini_down):
    overdue.run()
    r = web.get("/", params={"msg": "<b>hi</b>"})
    assert r.status_code == 200 and "Waiting for your approval" in r.text
    assert "<b>hi</b>" not in r.text and "&lt;b&gt;hi&lt;/b&gt;" in r.text  # flash messages are escaped


def test_dashboard_decision_with_a_bad_id_does_not_crash(db):
    r = web.post("/approvals/not-an-id/decide", data={"decision": "approve"}, follow_redirects=False)
    assert r.status_code == 303 and _flash(r) == "Approval not found."


def test_dashboard_edit_and_approve(db, gemini_down, sent):
    overdue.run()
    a = db[schema.APPROVALS].find_one({"ref": 4})
    r = web.post(f"/approvals/{a['_id']}/decide", data={"decision": "edit", "draft": "Edited text"}, follow_redirects=False)
    assert "#4 edited" in _flash(r) and sent[-1]["draft"] == "Edited text"


def test_upload_route(db, fake_extraction):
    fake_extraction(make_invoice(invoice_number="EDC-5555"))
    r = web.post("/upload", files={"file": ("inv.png", b"img", "image/png")}, follow_redirects=False)
    assert "Added EDC-5555" in _flash(r)
    r = web.post("/upload", files={"file": ("notes.txt", b"hello", "text/plain")}, follow_redirects=False)
    assert "Can't read" in _flash(r)


# --- /brief
def test_brief_escapes_untrusted_names_in_html(db):
    from bson import ObjectId
    evil = db[schema.INVOICES].find_one({"invoice_number": "EDC-7781"})
    evil.update(_id=ObjectId(), invoice_number="X-1", counterparty="<script>alert(1)</script>", counterparty_key="evil",
                needs_review=True, verification_issues=["odd"])
    db[schema.INVOICES].insert_one(evil)  # e.g. a name read from an emailed invoice
    r = web.get("/brief", params={"token": TOKEN, "format": "html"})
    assert "<script>alert(1)</script>" not in r.text and "&lt;script&gt;" in r.text


def test_brief_reply_hint_uses_a_real_ref(db, gemini_down):
    dbclient.next_refs(db, "gaia-coffee", 4)  # refs 1-4 already used earlier today
    overdue.run()
    text = web.get("/brief", params={"token": TOKEN}).text
    assert 'Reply "approve 5"' in text and 'approve 1"' not in text


# --- /updates (Saint's outbox feed)
def test_updates_feed_delivers_each_message_once(db, fake_extraction, sent):
    fake_extraction(make_invoice(issuer_name="GAIA Coffee", recipient_name="Test Cafe", invoice_number="GAIA-4000",
                                 due_date="2026-10-01"))
    web.post("/upload", files={"file": ("inv.png", b"img", "image/png")})
    get = lambda **kw: web.get("/updates", params={"token": TOKEN, **kw}).json()  # noqa: E731
    first = get()
    kinds = [u["kind"] for u in first["updates"]]
    assert "invoice_extracted" in kinds and "approval_needed" in kinds
    assert any("GAIA-4000" in u["message"] for u in first["updates"])
    assert get()["count"] == 0  # delivered once
    assert web.get("/updates", params={"token": TOKEN, "format": "text"}).text == ""  # nothing to send

    ref = next(u["ref"] for u in first["updates"] if u["kind"] == "approval_needed")
    web.post("/approve", params={"token": TOKEN}, json={"text": f"approve {ref}"})  # WhatsApp: owner already got a reply
    assert get()["count"] == 0
    a = db[schema.APPROVALS].find_one({"status": "pending"})
    web.post(f"/approvals/{a['_id']}/decide", data={"decision": "reject"})  # dashboard: tell them on WhatsApp
    msgs = get()["updates"]
    assert len(msgs) == 1 and msgs[0]["message"].startswith("❌")


def test_updates_peek_and_bad_cursor(db, gemini_down):
    overdue.run()
    peek = web.get("/updates", params={"token": TOKEN, "peek": "true"}).json()
    assert peek["count"] == 4 and web.get("/updates", params={"token": TOKEN}).json()["count"] == 4  # peek didn't consume
    assert web.get("/updates", params={"token": TOKEN, "since": "nope"}).status_code == 400


# ---------------------------------------------------------------- POS connector (synthetic till files, no network)
from backend.inputs.pos.client import LONDON, PosClient  # noqa: E402

ALL_DATA = PosClient(now="2027-01-01")  # a clock after every file, so everything is visible


def test_pos_reads_every_synthetic_order_and_count():
    orders = list(ALL_DATA.iter_sales())
    assert len(orders) == 1319 + 1389 + 1261
    assert len({o["order_id"] for o in orders}) == len(orders)
    assert [o["placed_at"] for o in orders] == sorted(o["placed_at"] for o in orders)
    assert len(ALL_DATA.list_stock_counts()) == 6
    assert ALL_DATA.list_stock()["count_id"] == "GAIA-COUNT-CLOSING-20261130"


def test_pos_never_returns_the_future():
    till = PosClient(now=datetime(2026, 10, 24, 12, 0, tzinfo=LONDON))
    orders = list(till.iter_sales())
    assert orders and max(o["placed_at"] for o in orders) < "2026-10-24T12:00"
    assert list(till.iter_sales(since="2026-11-01")) == []
    assert till.list_stock()["count_id"] == "GAIA-COUNT-OPENING-20261002"  # not the 31 Oct or November counts
    assert PosClient(now="2026-09-01").list_stock() is None


def test_pos_date_filter_uses_london_days_and_until_is_exclusive():
    day = list(ALL_DATA.iter_sales(since="2026-10-03", until="2026-10-04"))
    assert day and all(o["placed_at"].startswith("2026-10-03") for o in day)
    assert list(ALL_DATA.iter_sales(since="2026-10-04", until="2026-10-05")) == []  # Sunday: not a trading day


def test_pos_cursor_pages_cover_everything_once():
    seen, cursor = [], None
    while True:
        page = ALL_DATA.list_sales(since="2026-09-01", until="2026-10-01", limit=50, cursor=cursor)
        assert len(page["orders"]) <= 50
        seen += [o["order_id"] for o in page["orders"]]
        cursor = page["cursor"]
        if cursor is None:
            break
    assert len(seen) == len(set(seen)) == 1319


@pytest.mark.parametrize("kwargs", [{"limit": 0}, {"limit": 201}, {"cursor": "abc"}, {"cursor": "-5"}])
def test_pos_rejects_bad_paging(kwargs):
    with pytest.raises(ValueError):
        ALL_DATA.list_sales(**kwargs)


def test_pos_results_are_copies():
    first = ALL_DATA.list_sales(limit=1)["orders"][0]
    first["total_gbp"] = 9999
    assert ALL_DATA.list_sales(limit=1)["orders"][0]["total_gbp"] != 9999


def test_pos_refuses_files_not_labelled_synthetic(tmp_path):
    (tmp_path / "real.json").write_text('{"orders": []}')
    with pytest.raises(ValueError, match="synthetic"):
        PosClient(tmp_path).list_sales()


def test_pos_empty_folder_is_an_empty_till(tmp_path):
    till = PosClient(tmp_path)
    assert till.list_sales() == {"orders": [], "cursor": None}
    assert till.list_stock() is None


# --- till sync: connector → backend.sales (in-memory DB)
import json  # noqa: E402
from datetime import timedelta  # noqa: E402

from backend.inputs.pos import sync as pos_sync  # noqa: E402


def test_pos_backfill_records_everything_once(db):
    first = pos_sync.sync(ALL_DATA, days=None)
    assert first.ok and first.orders.new == 3969 and first.stock_new == 6
    assert db[schema.ORDERS].count_documents({}) == 3969
    again = pos_sync.sync(ALL_DATA, days=None)
    assert again.ok and again.orders.new == 0 and again.orders.duplicates == 3969 and again.stock_new == 0
    assert "0 new order(s)" in again.message and "0 new stock count(s)" in again.message


def test_pos_sync_reads_only_the_lookback_window_and_never_the_future(db):
    now = datetime(2026, 10, 24, 12, 0, tzinfo=LONDON)
    r = pos_sync.sync(PosClient(now=now), days=7)
    assert r.ok and r.orders.new > 0 and r.stock_new == 0  # the 2 Oct count is outside the window
    stored = [o["placed_at"] for o in db[schema.ORDERS].find()]
    assert min(stored) >= now - timedelta(days=7) and max(stored) < now
    assert db[schema.ORDERS].count_documents({}) == r.orders.new


def test_pos_sync_reports_a_broken_till_and_records_nothing(db, tmp_path):
    (tmp_path / "real.json").write_text('{"orders": []}')
    r = pos_sync.sync(PosClient(tmp_path), days=None)
    assert not r.ok and "synthetic" in r.message and r.message.startswith("Till sync failed")
    assert db[schema.ORDERS].count_documents({}) == 0


def test_pos_sync_reports_a_bad_stock_count_but_keeps_going(db, tmp_path):
    good = {"data_type": "synthetic_mock_data", "record_type": "physical_inventory_count", "count_id": "GAIA-COUNT-OPENING-1",
            "counted_at": "2026-10-02T07:10:00+01:00", "items": [{"sku": "INV-001", "unit": "kg", "counted_quantity": 5}]}
    bad = {**good, "count_id": "", "counted_at": "2026-10-03T07:10:00+01:00"}
    (tmp_path / "good.json").write_text(json.dumps(good))
    (tmp_path / "bad.json").write_text(json.dumps(bad))
    r = pos_sync.sync(PosClient(tmp_path, now="2026-12-01"), days=None)
    assert r.stock_new == 1 and len(r.stock_errors) == 1 and r.stock_errors[0].startswith("?:") and not r.ok


def test_pos_sync_rejects_a_zero_day_window():
    with pytest.raises(ValueError):
        pos_sync.sync(ALL_DATA, days=0)


def test_pos_sync_command_line(db, capsys):
    pos_sync.main(["--all", "--now", "2026-10-03"])
    out = capsys.readouterr().out
    assert out.startswith("Till sync:") and "new stock count(s)" in out
    assert db[schema.ORDERS].count_documents({}) > 0


# ---------------------------------------------------------------- weather + events inputs (fake HTTP, no network)
import io  # noqa: E402
import urllib.error  # noqa: E402
import urllib.request  # noqa: E402

from backend.inputs import events, fetch, weather  # noqa: E402
from backend.inputs.pitches import PITCHES  # noqa: E402

PITCH = PITCHES[0]


@pytest.fixture(autouse=True)
def no_real_http(monkeypatch):
    """Edge tests never reach Open-Meteo or Ticketmaster: patch fetch.get_json (or urlopen) to fake them."""
    def blocked(*_a, **_k):
        raise RuntimeError("Tests must not make real HTTP calls: use fake_http.")
    monkeypatch.setattr(urllib.request, "urlopen", blocked)


@pytest.fixture
def fake_http(monkeypatch):
    """fake_http(*responses): each fetch.get_json call returns the next response (or raises it); calls are recorded."""
    calls = []

    def use(*responses):
        queue = list(responses)

        def get_json(url, params, source, timeout=fetch.TIMEOUT):
            calls.append({"url": url, "params": params, "source": source})
            r = queue.pop(0) if len(queue) > 1 else queue[0]
            if isinstance(r, Exception):
                raise r
            return r
        monkeypatch.setattr(fetch, "get_json", get_json)
        return calls
    return use


def _meteo(**overrides):
    daily = {"time": ["2026-10-24", "2026-10-25"], "weather_code": [63, 0], "temperature_2m_max": [15.5, 18.0],
             "temperature_2m_min": [7.4, 9.0], "precipitation_sum": [6.2, 0.0], "precipitation_probability_max": [100, 3],
             "wind_speed_10m_max": [24.1, 10.0]}
    return {"timezone": "Europe/London", "daily": {**daily, **overrides}}


def test_weather_reads_open_meteo_days_in_london_time(fake_http):
    calls = fake_http(_meteo())
    days = weather.forecast(PITCH, days=2)
    assert [d["date"] for d in days] == ["2026-10-24", "2026-10-25"]
    assert days[0]["summary"] == "Rain" and days[0]["precip_chance_pct"] == 100 and days[1]["summary"] == "Clear"
    assert all(d["pitch_id"] == PITCH["id"] and d["source"] == "open-meteo" for d in days)
    p = calls[0]["params"]
    assert p["timezone"] == "Europe/London" and p["forecast_days"] == 2 and (p["latitude"], p["longitude"]) == (PITCH["lat"], PITCH["lon"])


def test_weather_keeps_a_missing_value_as_none(fake_http):
    fake_http(_meteo(weather_code=[None, 99], precipitation_probability_max=[None, 40]))
    first, second = weather.forecast(PITCH, days=2)
    assert first["summary"] == "No data" and first["precip_chance_pct"] is None and first["temp_max_c"] == 15.5
    assert second["summary"] == "Thunderstorm with hail"
    assert weather.describe(42) == "Unknown (code 42)"


@pytest.mark.parametrize("payload,expected", [
    ({"error": True}, "without a daily forecast"),
    (_meteo(temperature_2m_max=[15.5]), "temperature_2m_max"),          # wrong length
    (_meteo(precipitation_sum=None), "precipitation_sum"),             # missing column
    (_meteo(temperature_2m_min=["cold", 9.0]), "non-number"),
    (_meteo(time=["24/10/2026", "2026-10-25"]), "bad date"),
])
def test_weather_refuses_a_broken_forecast(fake_http, payload, expected):
    fake_http(payload)
    with pytest.raises(fetch.FetchError, match=expected):
        weather.forecast(PITCH, days=2)


@pytest.mark.parametrize("days", [0, 17])
def test_weather_rejects_days_outside_the_horizon(days):
    with pytest.raises(ValueError):
        weather.forecast(PITCH, days=days)


NOW = datetime(2026, 10, 24, 9, 30, 15, 123456, tzinfo=timezone.utc)


def _tm_event(eid, day="2026-10-25", status="onsale", **extra):
    return {"id": eid, "name": f"Event {eid}", "url": f"https://example.com/{eid}",
            "dates": {"start": {"localDate": day, "dateTime": f"{day}T18:30:00Z"}, "status": {"code": status}},
            "classifications": [{"primary": True, "segment": {"name": "Music"}, "genre": {"name": "Rock"}}],
            "_embedded": {"venues": [{"name": "Hackney Venue", "location": {"latitude": "51.5450", "longitude": "-0.0553"}}]},
            **extra}


def _tm_page(events_, number=0, total_pages=1):
    body = {"page": {"size": 100, "number": number, "totalPages": total_pages, "totalElements": len(events_)}}
    if events_:
        body["_embedded"] = {"events": events_}
    return body


def test_events_need_a_ticketmaster_key(monkeypatch):
    monkeypatch.delenv("TICKETMASTER_API_KEY", raising=False)
    with pytest.raises(fetch.FetchError, match="TICKETMASTER_API_KEY"):
        events.nearby(PITCH)


def test_events_reads_nearby_events_and_the_request_window(fake_http):
    calls = fake_http(_tm_page([_tm_event("b", day="2026-10-26"), _tm_event("a")]))
    found = events.nearby(PITCH, days=7, now=NOW, api_key="k")
    assert [e["source_id"] for e in found] == ["a", "b"]  # soonest first
    a = found[0]
    assert a["category"] == "Music" and a["genre"] == "Rock" and a["venue"] == "Hackney Venue" and a["pitch_id"] == PITCH["id"]
    assert 0.9 < a["distance_km"] < 1.3  # ~1.1 km from Broadway Market, computed from the venue
    p = calls[0]["params"]
    assert p["startDateTime"] == "2026-10-24T09:30:15Z" and p["endDateTime"] == "2026-10-31T09:30:15Z"
    assert p["latlong"] == f"{PITCH['lat']},{PITCH['lon']}" and p["unit"] == "km" and p["radius"] == events.RADIUS_KM


def test_events_leave_out_cancelled_and_broken_ones(fake_http):
    tba = _tm_event("tba")
    tba["dates"]["start"] = {"localDate": "2026-10-27"}  # time to be announced
    undefined = _tm_event("u", classifications=[{"primary": True, "segment": {"name": "Undefined"}, "genre": {"name": "Undefined"}}])
    no_venue = _tm_event("nv", distance=2.5)
    no_venue["_embedded"] = {}
    fake_http(_tm_page([_tm_event("x", status="cancelled"), {"name": "no id"}, _tm_event("bad", day="soon"), "junk",
                        tba, undefined, no_venue]))
    found = {e["source_id"]: e for e in events.nearby(PITCH, now=NOW, api_key="k")}
    assert set(found) == {"tba", "u", "nv"}
    assert found["tba"]["starts_at"] is None and found["u"]["category"] is None and found["u"]["genre"] is None
    assert found["nv"]["venue"] is None and found["nv"]["distance_km"] == 2.5  # falls back to Ticketmaster's distance


def test_events_follow_pages_dedupe_and_stop(fake_http):
    calls = fake_http(_tm_page([_tm_event("a")], 0, 2), _tm_page([_tm_event("a"), _tm_event("b")], 1, 2))
    assert [e["source_id"] for e in events.nearby(PITCH, now=NOW, api_key="k")] == ["a", "b"]
    assert [c["params"]["page"] for c in calls] == [0, 1]
    calls = fake_http(_tm_page([_tm_event("a")], 0, 50))  # a huge result set stops at MAX_PAGES
    events.nearby(PITCH, now=NOW, api_key="k")
    assert len(calls) == 2 + events.MAX_PAGES


def test_events_quiet_area_is_an_empty_list(fake_http):
    fake_http(_tm_page([]))
    assert events.nearby(PITCH, now=NOW, api_key="k") == []


@pytest.mark.parametrize("kwargs", [{"days": 0}, {"days": 32}, {"radius_km": 0}, {"radius_km": 51}])
def test_events_reject_bad_windows(kwargs):
    with pytest.raises(ValueError):
        events.nearby(PITCH, api_key="k", **kwargs)


def test_fetch_errors_never_leak_the_api_key(monkeypatch):
    def unauthorized(request, timeout):
        body = io.BytesIO(b'{"fault": {"faultstring": "Invalid ApiKey for given resource"}}')
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, body)
    monkeypatch.setattr(urllib.request, "urlopen", unauthorized)
    with pytest.raises(fetch.FetchError) as err:
        fetch.get_json(events.TICKETMASTER, {"apikey": "SECRET123", "size": 1}, source="Ticketmaster")
    assert "SECRET123" not in str(err.value) and "HTTP 401" in str(err.value) and "Invalid ApiKey" in str(err.value)
    assert err.value.status == 401


@pytest.mark.parametrize("failure,expected", [
    (urllib.error.URLError("Name or service not known"), r"unreachable \(Name or service not known\)"),
    (TimeoutError("timed out"), "unreachable"),
])
def test_fetch_reports_an_unreachable_service(monkeypatch, failure, expected):
    def down(*_a, **_k):
        raise failure
    monkeypatch.setattr(urllib.request, "urlopen", down)
    with pytest.raises(fetch.FetchError, match=expected):
        fetch.get_json(weather.OPEN_METEO, {}, source="Open-Meteo")


@pytest.mark.parametrize("body,expected", [(b"<html>busy</html>", "isn't JSON"), (b"[1, 2]", "isn't an object")])
def test_fetch_refuses_non_object_answers(monkeypatch, body, expected):
    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_k: Response(body))
    with pytest.raises(fetch.FetchError, match=expected):
        fetch.get_json(weather.OPEN_METEO, {}, source="Open-Meteo")


def test_weather_and_events_commands_print_a_clear_error(fake_http, monkeypatch, capsys):
    fake_http(fetch.FetchError("Open-Meteo is unreachable (timed out)"))
    with pytest.raises(SystemExit, match="unreachable"):
        weather.main([])
    monkeypatch.delenv("TICKETMASTER_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="TICKETMASTER_API_KEY"):
        events.main([])
