"""Transport (owner: Sunil, built for MVP2): TfL fetcher (inputs) + which disruptions hit our stations (middle) +
storage. Fake HTTP and real-shaped TfL data, no network. Own file so it never conflicts with test_edges/test_middle."""
from datetime import date, datetime, timezone

import pytest

from backend import conditions, transport
from backend.db import schema
from backend.inputs import fetch
from backend.inputs import transport as tfl

WINDOW = ("2026-10-24", "2026-10-30")
# Real Windrush pieces as TfL returns them (shortened): they share junction stations
WINDRUSH = [["Highbury & Islington Rail Station", "Dalston Junction Rail Station", "Haggerston Rail Station",
             "Hoxton Rail Station", "Shoreditch High Street Rail Station", "Whitechapel Rail Station", "Surrey Quays Rail Station"],
            ["Surrey Quays Rail Station", "New Cross Gate Rail Station", "Brockley Rail Station", "Sydenham Rail Station"],
            ["Sydenham Rail Station", "Penge West Rail Station", "Norwood Junction Rail Station", "West Croydon Rail Station"],
            ["Sydenham Rail Station", "Crystal Palace Rail Station"]]
STATIONS = [{"station_id": "910GLONFLDS", "name": "London Fields Rail Station", "distance_m": 616, "lines": ["weaver"], "modes": ["overground"]},
            {"station_id": "940GZZLUBLG", "name": "Bethnal Green Underground Station", "distance_m": 1048, "lines": ["central"], "modes": ["tube"]},
            {"station_id": "910GHAGGERS", "name": "Haggerston Rail Station", "distance_m": 1059, "lines": ["windrush"], "modes": ["overground"]},
            {"station_id": "910GHOXTON", "name": "Hoxton Rail Station", "distance_m": 1132, "lines": ["windrush"], "modes": ["overground"]}]


def _status(line="windrush", status="Part Closure", reason="", periods=None, category="PlannedWork"):
    return {"line_id": line, "line_name": line.capitalize(), "status": status, "severity": 3, "category": category,
            "reason": reason, "periods": periods if periods is not None else [{"from": "2026-10-25T04:30:00Z", "to": "2026-10-26T01:29:00Z"}],
            "source": "tfl", "fetched_at": "2026-10-24T17:00:00Z"}


def _assess(*statuses):
    return transport.assess({"broadway-market": {"stations": STATIONS, "statuses": list(statuses), "window": WINDOW,
                                                 "sequences": {"windrush": WINDRUSH}}})


# --- which disruptions hit our stations (middle)

def test_a_closure_far_down_the_line_is_ignored():
    found, ignored = _assess(_status(reason="WINDRUSH LINE: Sunday 25 October, no service between Sydenham and Crystal Palace."))
    assert found == [] and "doesn't include our stations" in ignored[0]["why"]


def test_a_closure_through_our_stations_hits_exactly_those_stations():
    found, _ = _assess(_status(reason="Sunday 25 October, no service between Dalston Junction and Whitechapel."))
    assert found[0]["impact"] == "closed" and found[0]["stations"] == ["Haggerston", "Hoxton"] and found[0]["nearest_m"] == 1059
    assert found[0]["dates"] == ["2026-10-25"] and found[0]["category"] == "planned"


def test_a_section_spanning_two_tfl_pieces_is_followed_across_the_junction():
    far, _ = _assess(_status(reason="no service between New Cross Gate and West Croydon / Crystal Palace."))
    assert far == []  # 6 stops south of us, across two pieces of the line map
    near, _ = _assess(_status(reason="no service between Highbury & Islington and New Cross Gate."))
    assert near[0]["stations"] == ["Haggerston", "Hoxton"]


def test_a_station_named_in_the_text_is_hit_even_without_a_section():
    found, _ = _assess(_status(line="weaver", status="Part Closure", reason="London Fields station is closed for works.",
                               periods=[{"from": "2026-10-25T08:00:00Z", "to": "2026-10-25T16:00:00Z"}]))
    assert found[0]["stations"] == ["London Fields"] and found[0]["impact"] == "closed"


def test_whole_line_problems_hit_every_nearby_station_on_it():
    found, _ = _assess(_status(line="central", status="Severe Delays", category="RealTime",
                               reason="Central Line: Severe delays due to train cancellations.",
                               periods=[{"from": "2026-10-24T12:36:00Z", "to": "2026-10-25T01:29:00Z"}]))
    assert found[0]["impact"] == "delays" and found[0]["stations"] == ["Bethnal Green"] and found[0]["category"] == "live"


def test_a_section_we_cant_place_is_uncertain_not_guessed():
    found, _ = _assess(_status(reason="no service between Narnia and Atlantis."))
    assert found[0]["impact"] == "uncertain" and found[0]["stations"] == ["Haggerston", "Hoxton"]
    vague, _ = _assess(_status(reason="Part closure this weekend, check before you travel."))
    assert vague[0]["impact"] == "uncertain"


def test_night_only_works_and_non_disruptions_are_ignored():
    night = [{"from": f"2026-10-2{d}T22:30:00Z", "to": f"2026-10-2{d + 1}T00:29:00Z"} for d in (6, 7)]  # 23:30 London
    found, ignored = _assess(_status(reason="no service between Dalston Junction and Whitechapel after 2330.", periods=night),
                             _status(line="weaver", status="Special Service", reason="https://example"),
                             _status(line="greater-anglia", status="Severe Delays", reason="Delays."))
    assert found == []
    assert [i["why"][:12] for i in ignored] == ["only outside", "'Special Ser", "none of our "]


def test_names_with_and_in_them_are_split_correctly():
    line = [["Kennington Rail Station", "Elephant and Castle Rail Station", "Borough Rail Station", "London Bridge Rail Station"]]
    assert transport._section("Elephant and Castle and London Bridge", line) == {"elephant and castle", "borough", "london bridge"}
    assert transport._section("Elephant and Atlantis", line) is None


def test_trading_days_follow_london_time_and_the_window():
    first, last = date(2026, 10, 24), date(2026, 10, 30)
    assert transport._trading_days([{"from": "2026-10-23T06:00:00Z", "to": "2026-11-05T18:00:00Z"}], first, last) == \
        [f"2026-10-{d}" for d in range(24, 31)]  # clipped to the window
    # Clocks go back on Sun 25 Oct: 17:30Z is 17:30 GMT (still trading) that day, 18:30 BST (closed) the day before
    assert transport._trading_days([{"from": "2026-10-25T17:30:00Z", "to": "2026-10-25T22:00:00Z"}], first, last) == ["2026-10-25"]
    assert transport._trading_days([{"from": "2026-10-24T17:30:00Z", "to": "2026-10-24T22:00:00Z"}], first, last) == []
    assert transport._trading_days([{"from": "bad", "to": "worse"}], first, last) == []


# --- storage (middle)

def test_disruptions_are_stored_once_and_resolved_when_tfl_stops_listing_them(db):
    found, _ = _assess(_status(reason="no service between Dalston Junction and Whitechapel."),
                       _status(line="central", status="Severe Delays", reason="Central Line: Severe delays.", category="RealTime",
                               periods=[{"from": "2026-10-26T09:00:00Z", "to": "2026-10-26T12:00:00Z"}]))
    first = conditions.record_transport(found, covered=[("broadway-market", *WINDOW)])
    assert first.new == 2 and db[schema.TRANSPORT].count_documents({"state": "active"}) == 2
    assert conditions.record_transport(found, covered=[("broadway-market", *WINDOW)]).unchanged == 2
    gone = conditions.record_transport(found[:1], covered=[("broadway-market", *WINDOW)])  # delays cleared
    assert gone.removed == 1 and db[schema.TRANSPORT].count_documents({}) == 2  # resolved, not deleted
    back = conditions.record_transport(found, covered=[("broadway-market", *WINDOW)])
    assert back.updated == 1 and db[schema.TRANSPORT].count_documents({"state": "active"}) == 2
    assert db[schema.AGENT_LOG].count_documents({"kind": "transport_synced"}) == 3  # not on the unchanged run


def test_bad_disruptions_are_rejected_and_the_forecast_reads_the_rest(db):
    good, _ = _assess(_status(reason="no service between Dalston Junction and Whitechapel."))
    r = conditions.record_transport(good + [{**good[0], "source_id": "x1", "impact": "bad"},
                                            {**good[0], "source_id": "x2", "dates": []},
                                            {**good[0], "source_id": "x3", "stations": []}, "junk"])
    assert r.new == 1 and len(r.errors) == 4
    got = conditions.conditions_for(["2026-10-25", "2026-10-26"])
    assert [t["stations"] for t in got["2026-10-25"]["transport"]] == [["Haggerston", "Hoxton"]]
    assert got["2026-10-26"]["transport"] == []
    stored = db[schema.TRANSPORT].find_one({})
    assert stored["starts_at"] == datetime(2026, 10, 25, 4, 30, tzinfo=timezone.utc)  # TfL time read as UTC


def test_sync_survives_tfl_being_down_and_keeps_what_is_stored(db, monkeypatch):
    def down(*_a, **_k):
        raise fetch.FetchError("TfL is unreachable (timed out)")
    monkeypatch.setattr(fetch, "get_json", down)
    assert "unreachable" in conditions.sync_transport()
    assert db[schema.AGENT_LOG].find_one({"kind": "transport_sync_failed"})


# --- fetcher (inputs), fake HTTP

@pytest.fixture
def fake_tfl(monkeypatch):
    calls = []

    def get_json(url, params, source, timeout=fetch.TIMEOUT, expect=dict):
        calls.append((url, params))
        if "/StopPoint" in url:
            return {"stopPoints": [
                {"id": "910GLONFLDS", "commonName": "London Fields Rail Station", "distance": 616.4,
                 "lines": [{"id": "weaver"}], "modes": ["overground", "bus"]},
                {"id": "BAD"},  # malformed: skipped, not fatal
                {"id": "940GZZLUBLG", "commonName": "Bethnal Green Underground Station", "distance": 1048.2,
                 "lines": [{"id": "central"}], "modes": ["tube"]}]}
        if "/Status/" in url:
            assert expect is list
            return [{"id": "central", "name": "Central", "lineStatuses": [
                        {"statusSeverityDescription": "Severe Delays", "statusSeverity": 6, "reason": "Severe delays.",
                         "validityPeriods": [{"fromDate": "2026-10-24T12:36:00Z", "toDate": "2026-10-25T01:29:00Z"}],
                         "disruption": {"category": "RealTime"}}]},
                    {"id": "weaver", "name": "Weaver", "lineStatuses": [{"statusSeverityDescription": "Good Service"}]}]
        raise AssertionError(url)
    monkeypatch.setattr(fetch, "get_json", get_json)
    return calls


def test_fetcher_reads_stations_and_only_real_problems(fake_tfl, monkeypatch):
    monkeypatch.delenv("TFL_APP_KEY", raising=False)
    near = tfl.stations()
    assert [s["name"] for s in near] == ["London Fields Rail Station", "Bethnal Green Underground Station"]
    assert near[0]["distance_m"] == 616 and near[0]["modes"] == ["overground"]
    found = tfl.statuses(["weaver", "central"], date(2026, 10, 24), date(2026, 10, 30))
    assert [(s["line_id"], s["status"], s["category"]) for s in found] == [("central", "Severe Delays", "RealTime")]
    assert "/Line/central,weaver/Status/2026-10-24/to/2026-10-30" in fake_tfl[-1][0] and "app_key" not in fake_tfl[-1][1]


def test_fetcher_sends_the_optional_app_key_and_fetch_masks_it(monkeypatch):
    monkeypatch.setenv("TFL_APP_KEY", "s3cret")
    assert tfl._params()["app_key"] == "s3cret"
    assert "app_key" in fetch.SECRET_PARAMS


def test_fetcher_skips_the_line_map_when_no_section_is_named(fake_tfl):
    snap = tfl.snapshot(days=7, today=date(2026, 10, 24))
    assert snap["broadway-market"]["sequences"] == {} and snap["broadway-market"]["window"] == WINDOW
    assert not any("/Route/Sequence" in url for url, _ in fake_tfl)  # 2 calls, not 2 + one per line


def test_a_list_where_an_object_was_expected_is_a_clear_error(monkeypatch):
    import io
    import urllib.request

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_k: Resp(b"[1, 2]"))
    with pytest.raises(fetch.FetchError, match="isn't an object"):
        fetch.get_json("https://x", {}, "TfL")
    assert fetch.get_json("https://x", {}, "TfL", expect=list) == [1, 2]


def test_a_one_day_window_asks_tfl_for_a_valid_range(monkeypatch):
    urls = []

    def get_json(url, *_a, **_k):
        urls.append(url)
        return []
    monkeypatch.setattr(fetch, "get_json", get_json)
    tfl.statuses(["weaver"], date(2026, 10, 24), date(2026, 10, 24))  # TfL answers 400 when EndDate == StartDate
    assert urls[0].endswith("/Status/2026-10-24/to/2026-10-25")
