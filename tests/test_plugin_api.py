"""Parse / status / cache honesty for SMF Overwatch pane."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))

import plugin_api as api

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _json(name: str):
    return json.loads(_read(name))


NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


def _getter_from_map(mapping):
    def getter(url, headers=None):
        if url not in mapping:
            raise OSError(f"unexpected url {url}")
        spec = mapping[url]
        if isinstance(spec, Exception):
            raise spec
        status, body = spec
        if status >= 400:
            raise OSError(
                api.describe_layer_http_error(
                    status, api._layer_id_for_url(url), body[:180] if body else None
                )
            )
        return status, body, {}

    return getter


def _all_ok_map():
    return {
        api.USGS_URL: (200, _read("usgs.geojson")),
        api.EONET_URL: (200, _read("eonet.json")),
        api.OPENSKY_URL: (200, _read("opensky-states.json")),
        api.NWS_URL: (200, _read("nws-alerts.json")),
        api.FIRMS_CSV_URL: (200, _read("firms-viirs.csv")),
    }


def _all_empty_map():
    return {
        api.USGS_URL: (200, '{"type":"FeatureCollection","features":[]}'),
        api.EONET_URL: (200, '{"events":[]}'),
        api.OPENSKY_URL: (200, '{"time":1,"states":[]}'),
        api.NWS_URL: (200, '{"features":[]}'),
        api.FIRMS_CSV_URL: (200, "latitude,longitude,frp\n"),
    }


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def test_classify_status_matches_hud_honesty():
    now = NOW
    assert api.classify_status(enabled=False, updated_at=None, error=None, now=now) == "off"
    assert api.classify_status(enabled=True, updated_at=None, error="HTTP 403", now=now) == "err"
    assert api.classify_status(
        enabled=True, updated_at="2026-09-20T12:00:00Z", error="HTTP 403", now=now
    ) == "stale"
    assert api.classify_status(
        enabled=True, updated_at="2026-09-20T12:00:00Z", error=None, now=now
    ) == "live"
    assert api.classify_status(
        enabled=True,
        updated_at="2026-09-20T11:40:00Z",
        error=None,
        now=now,
    ) == "stale"
    assert api.classify_status(enabled=True, updated_at=None, error=None, now=now) == "err"


def test_opensky_http_errors_are_honest():
    msg401 = api.describe_layer_http_error(401, "opensky")
    assert "401" in msg401
    assert "invented" in msg401.lower()
    msg429 = api.describe_layer_http_error(429, "opensky")
    assert "429" in msg429
    assert "invented" in msg429.lower()


# ---------------------------------------------------------------------------
# Parsers — never invent coordinates
# ---------------------------------------------------------------------------

def test_parse_usgs_keeps_valid_quakes_drops_bad_geometry():
    points = api.parse_usgs(_json("usgs.geojson"))
    assert [p["id"] for p in points] == ["eq-us7000abcd", "eq-us7000efgh"]
    assert points[0]["kind"] == "quake"
    assert points[0]["lat"] == -8.2
    assert points[0]["lng"] == 120.1
    assert points[0]["label"].startswith("M4.5")
    assert "Example City" in points[0]["label"]
    assert points[1]["mag"] == pytest.approx(6.2)


def test_parse_usgs_does_not_invent_from_malformed():
    assert api.parse_usgs(None) == []
    assert api.parse_usgs({}) == []
    assert api.parse_usgs({"features": "nope"}) == []
    assert api.parse_usgs({"features": [{"id": "x"}]}) == []


def test_parse_eonet_uses_last_geometry_skips_unlocated():
    points = api.parse_eonet(_json("eonet.json"))
    assert len(points) == 2
    assert points[0]["id"] == "eonet-EONET_111"
    assert points[0]["lat"] == pytest.approx(36.7)
    assert points[0]["lng"] == pytest.approx(-120.1)
    assert points[0]["label"] == "Wildfire - Example Ridge"
    assert points[1]["lat"] == pytest.approx(12.5)
    assert points[1]["lng"] == pytest.approx(45.1)


def test_parse_opensky_samples_and_drops_invalid_coords():
    points = api.parse_opensky(_json("opensky-states.json"), stride=1, limit=90)
    assert [p["id"] for p in points] == ["ac-abc123", "ac-skip1", "ac-skip2"]
    assert points[0]["label"] == "BAW123"
    assert points[0]["lat"] == 51.47
    assert points[0]["lng"] == -0.45
    assert points[0]["extra"] == "United Kingdom"
    assert points[0]["kind"] == "aircraft"


def test_parse_opensky_does_not_invent_aircraft():
    assert api.parse_opensky(None) == []
    assert api.parse_opensky({}) == []
    assert api.parse_opensky({"states": "nope"}) == []


def test_parse_nws_centroids_skip_null_geometry():
    points = api.parse_nws(_json("nws-alerts.json"))
    assert len(points) == 2
    assert points[0]["label"] == "Tornado Warning"
    assert points[0]["lat"] == pytest.approx(35.4, abs=0.05)
    assert points[0]["lng"] == pytest.approx(-89.6, abs=0.05)
    assert points[0]["extra"] == "Demo County"
    assert points[1]["label"] == "Special Weather Statement"
    assert points[1]["lat"] == pytest.approx(25.77)
    ids = [p["id"] for p in points]
    assert all("nogeom" not in i for i in ids)


def test_parse_ais_snapshot_skips_bogus_coordinates():
    points = api.parse_ais_snapshot(_json("ais-snapshot.json"))
    assert len(points) == 2
    assert points[0]["id"] == "ais-367719770"
    assert points[0]["label"] == "EXAMPLE STAR"
    assert points[0]["lat"] == pytest.approx(25.77)
    assert points[1]["label"] == "MMSI 211476060"


def test_extract_ais_vessel_does_not_fabricate_a_ship():
    messages = _json("ais-messages.json")["messages"]
    from_meta = api.extract_ais_vessel(messages[0])
    assert from_meta["mmsi"] == "368207620"
    assert from_meta["name"] == "DEMO SHIP"
    assert from_meta["lat"] == pytest.approx(33.72)
    assert api.extract_ais_vessel(messages[2]) is None
    points = api.parse_ais_snapshot(_json("ais-messages.json"))
    assert len(points) == 2
    assert api.parse_ais_snapshot({"vessels": []}) == []
    assert api.parse_ais_snapshot({"error": "no key"}) == []


def test_parse_firms_csv_ranks_by_frp_skips_invalid_rows():
    points = api.parse_firms_csv(_read("firms-viirs.csv"), limit=10)
    assert len(points) == 3
    assert points[0]["kind"] == "fire"
    assert points[0]["lat"] == pytest.approx(-23.5)
    assert "FRP 88" in (points[0].get("extra") or "")


def test_parse_firms_csv_rejects_html_and_missing_columns():
    with pytest.raises(ValueError, match="HTML"):
        api.parse_firms_csv(_read("firms-invalid.html"))
    with pytest.raises(ValueError, match="latitude"):
        api.parse_firms_csv("foo,bar\n1,2")
    with pytest.raises(ValueError, match="MAP_KEY"):
        api.parse_firms_csv("invalid map key")
    assert api.parse_firms_csv("") == []


def test_normalize_point_requires_real_coordinates():
    assert api.normalize_point({"lat": 91, "lng": 0, "label": "x", "kind": "event"}) is None
    assert api.normalize_point({"lat": 10, "lng": 200, "label": "x", "kind": "event"}) is None
    assert api.normalize_point({"label": "no coords", "kind": "event"}) is None
    ok = api.normalize_point({"lat": 1.5, "lng": 2.5, "label": "ok", "kind": "event", "id": "p1"})
    assert ok["lat"] == 1.5
    assert ok["id"] == "p1"


def test_host_allowed_is_osint_allowlist_only():
    assert api.host_allowed(api.USGS_URL)
    assert api.host_allowed(api.OPENSKY_URL)
    assert not api.host_allowed("https://example.com/secret")
    assert not api.host_allowed("http://127.0.0.1:22/")
    assert api.hud_url_allowed(api.HUD_PREVIEW_URL)
    assert api.hud_url_allowed(api.HUD_DEV_URL)
    assert not api.hud_url_allowed("http://127.0.0.1:22/")
    assert not api.hud_url_allowed("https://earthquake.usgs.gov/")


# ---------------------------------------------------------------------------
# Collect — empty vs error vs stale vs AIS off
# ---------------------------------------------------------------------------

def test_empty_successful_read_is_live_not_error(tmp_path: Path):
    payload = api.collect_layers(
        getter=_getter_from_map(_all_empty_map()),
        root=tmp_path,
        now=NOW,
        ttl=300,
        refresh=True,
        probe=False,
    )
    assert payload["ok"] is True
    by_id = {row["id"]: row for row in payload["layers"]}
    for layer_id in ("earthquakes", "eonet", "opensky", "nws", "firms"):
        row = by_id[layer_id]
        assert row["status"] == "live"
        assert row["count"] == 0
        assert row["sample"] == []
        assert row["error"] is None
    assert by_id["ais"]["status"] == "off"
    assert by_id["ais"]["count"] == 0
    assert by_id["ais"]["sample"] == []
    assert payload["summary"]["live"] == 5
    assert payload["summary"]["off"] == 1
    assert payload["hud"]["cases_key"] == api.CASES_KEY
    assert "does not read or sync" in payload["hud"]["cases_note"]


def test_collect_layers_parses_fixtures_without_inventing(tmp_path: Path):
    payload = api.collect_layers(
        getter=_getter_from_map(_all_ok_map()),
        root=tmp_path,
        now=NOW,
        refresh=True,
        probe=False,
    )
    assert payload["ok"] is True
    by_id = {row["id"]: row for row in payload["layers"]}
    assert by_id["earthquakes"]["status"] == "live"
    assert by_id["earthquakes"]["count"] == 2
    assert by_id["earthquakes"]["sample"][0]["label"].startswith("M4.5")
    assert by_id["eonet"]["count"] == 2
    assert by_id["opensky"]["count"] >= 1
    assert by_id["nws"]["count"] == 2
    assert by_id["firms"]["count"] == 3
    assert by_id["ais"]["status"] == "off"
    assert by_id["ais"]["sample"] == []
    titles = [pt["label"] for row in payload["layers"] for pt in row["sample"]]
    assert "Invented" not in titles
    assert payload["hud"]["preview_url"] == "http://127.0.0.1:4173/"
    assert payload["hud"]["dev_url"] == "http://127.0.0.1:5173/"
    assert payload["hud"]["github_url"] == api.GITHUB_URL
    assert payload["hud"]["identified"] is None
    assert payload["hud"]["reachable"] is None
    assert payload["hud"]["aigc_studio_url"] == "http://127.0.0.1:5174/"


def test_ais_is_off_even_when_getter_could_return_vessels(tmp_path: Path):
    mapping = _all_empty_map()

    def greedy(url, headers=None):
        if "ais" in url.lower() or "stream.aisstream" in url:
            raise AssertionError(f"AIS must not be fetched, got {url}")
        return _getter_from_map(mapping)(url, headers)

    payload = api.collect_layers(
        getter=greedy,
        root=tmp_path,
        now=NOW,
        refresh=True,
        probe=False,
    )
    ais = next(row for row in payload["layers"] if row["id"] == "ais")
    assert ais["status"] == "off"
    assert ais["enabled"] is False
    assert ais["count"] == 0
    assert ais["sample"] == []
    assert ais["error"] is None


def test_required_layer_failure_without_cache_is_err(tmp_path: Path):
    mapping = {
        api.USGS_URL: OSError("down"),
        api.EONET_URL: OSError("down"),
        api.OPENSKY_URL: OSError(
            "HTTP 401 unauthorized — OpenSky rejected this client. "
            "Anonymous access is often blocked. No aircraft were invented."
        ),
        api.NWS_URL: OSError("down"),
        api.FIRMS_CSV_URL: OSError("down"),
    }
    payload = api.collect_layers(
        getter=_getter_from_map(mapping),
        root=tmp_path,
        now=NOW,
        refresh=True,
        probe=False,
    )
    assert payload["ok"] is True
    by_id = {row["id"]: row for row in payload["layers"]}
    for layer_id in ("earthquakes", "eonet", "opensky", "nws", "firms"):
        assert by_id[layer_id]["status"] == "err"
        assert by_id[layer_id]["count"] == 0
        assert by_id[layer_id]["sample"] == []
        assert by_id[layer_id]["error"]
    assert by_id["ais"]["status"] == "off"
    assert payload["summary"]["err"] == 5
    assert payload["from_cache"] is False
    assert "Invented" not in str(payload)


def test_opensky_401_is_err_not_fake_tracks(tmp_path: Path):
    mapping = _all_empty_map()
    mapping[api.OPENSKY_URL] = (401, '{"error":"unauthorized"}')
    payload = api.collect_layers(
        getter=_getter_from_map(mapping),
        root=tmp_path,
        now=NOW,
        refresh=True,
        probe=False,
    )
    ads = next(row for row in payload["layers"] if row["id"] == "opensky")
    assert ads["status"] == "err"
    assert ads["count"] == 0
    assert ads["sample"] == []
    assert "401" in ads["error"]
    assert "invented" in ads["error"].lower()


def test_cache_stale_flag_on_refresh_failure(tmp_path: Path):
    now = NOW
    fresh = api.collect_layers(
        getter=_getter_from_map(_all_ok_map()),
        root=tmp_path,
        now=now,
        ttl=300,
        refresh=True,
        probe=False,
    )
    usgs = next(row for row in fresh["layers"] if row["id"] == "earthquakes")
    assert usgs["status"] == "live"
    cached_labels = [pt["label"] for pt in usgs["sample"]]

    fail = {url: OSError("timeout") for url in _all_ok_map()}
    later = now + timedelta(hours=1)
    stale = api.collect_layers(
        getter=_getter_from_map(fail),
        root=tmp_path,
        now=later,
        ttl=300,
        refresh=True,
        probe=False,
    )
    usgs2 = next(row for row in stale["layers"] if row["id"] == "earthquakes")
    assert usgs2["status"] == "stale"
    assert usgs2["from_cache"] is True
    assert usgs2["cache_age_seconds"] == pytest.approx(3600, abs=1)
    assert [pt["label"] for pt in usgs2["sample"]] == cached_labels
    assert usgs2["error"]
    assert "Invented" not in {pt["label"] for pt in usgs2["sample"]}
    ais = next(row for row in stale["layers"] if row["id"] == "ais")
    assert ais["status"] == "off"


def test_ttl_serves_cache_without_network(tmp_path: Path):
    first = api.collect_layers(
        getter=_getter_from_map(_all_ok_map()),
        root=tmp_path,
        now=NOW,
        ttl=300,
        refresh=True,
        probe=False,
    )
    usgs = next(row for row in first["layers"] if row["id"] == "earthquakes")
    assert usgs["from_cache"] is False

    def boom(url, headers=None):
        raise AssertionError(f"network should not run for {url}")

    second = api.collect_layers(
        getter=boom,
        root=tmp_path,
        now=NOW + timedelta(minutes=2),
        ttl=300,
        refresh=False,
        probe=False,
    )
    usgs2 = next(row for row in second["layers"] if row["id"] == "earthquakes")
    assert usgs2["from_cache"] is True
    assert usgs2["status"] == "live"
    assert usgs2["sample"][0]["label"] == usgs["sample"][0]["label"]


def test_malformed_json_is_err_not_empty_live(tmp_path: Path):
    mapping = _all_empty_map()
    mapping[api.USGS_URL] = (200, "<html>nope</html>")
    payload = api.collect_layers(
        getter=_getter_from_map(mapping),
        root=tmp_path,
        now=NOW,
        refresh=True,
        probe=False,
    )
    usgs = next(row for row in payload["layers"] if row["id"] == "earthquakes")
    assert usgs["status"] == "err"
    assert usgs["count"] == 0
    assert usgs["sample"] == []
    assert "JSON" in usgs["error"] or "HTML" in usgs["error"]


def test_firms_html_block_is_err(tmp_path: Path):
    mapping = _all_empty_map()
    mapping[api.FIRMS_CSV_URL] = (200, _read("firms-invalid.html"))
    payload = api.collect_layers(
        getter=_getter_from_map(mapping),
        root=tmp_path,
        now=NOW,
        refresh=True,
        probe=False,
    )
    firms = next(row for row in payload["layers"] if row["id"] == "firms")
    assert firms["status"] == "err"
    assert firms["sample"] == []
    assert "HTML" in firms["error"]


def test_partial_failure_keeps_other_layers_live(tmp_path: Path):
    mapping = _all_ok_map()
    mapping[api.OPENSKY_URL] = OSError("HTTP 429 rate limited")
    payload = api.collect_layers(
        getter=_getter_from_map(mapping),
        root=tmp_path,
        now=NOW,
        refresh=True,
        probe=False,
    )
    by_id = {row["id"]: row for row in payload["layers"]}
    assert by_id["earthquakes"]["status"] == "live"
    assert by_id["earthquakes"]["count"] == 2
    assert by_id["opensky"]["status"] == "err"
    assert by_id["opensky"]["count"] == 0
    assert payload["summary"]["live"] >= 3
    assert payload["summary"]["err"] == 1


OVERWATCH_HTML = (
    "<!doctype html><html><head><title>Overwatch OSINT for Omarchy</title>"
    '<meta name="description" content="Overwatch OSINT for Omarchy — a configurable OSINT workbench.">'
    "</head><body><div id=\"root\"></div></body></html>"
)
PACK_BUILDER_HTML = (
    "<!doctype html><html><head>"
    "<title>AIGC Production Flow Pack Builder · SMF Works</title>"
    "</head><body>vite</body></html>"
)
STUDIO_HTML = (
    "<!doctype html><html><head><title>AIGC Studio Spine</title></head><body></body></html>"
)


def test_html_identifies_overwatch_title_not_other_vite():
    assert api.html_identifies_as_overwatch(OVERWATCH_HTML)
    assert api.html_identifies_as_overwatch(
        "<html><head><title>  OVERWATCH   OSINT  </title></head></html>"
    )
    assert not api.html_identifies_as_overwatch(PACK_BUILDER_HTML)
    assert not api.html_identifies_as_overwatch(STUDIO_HTML)
    assert not api.html_identifies_as_overwatch("<html><title>sparkDash</title><body>overwatch</body></html>")
    assert not api.html_identifies_as_overwatch("<html>hud</html>")
    assert not api.html_identifies_as_overwatch("")
    # Title wins: a pack-builder title is not Overwatch even if the body mentions it.
    assert not api.html_identifies_as_overwatch(
        "<html><head><title>AIGC Production Flow Pack Builder</title>"
        '<meta name="description" content="Overwatch OSINT"></head></html>'
    )
    assert api.html_identifies_as_overwatch(
        '<html><head><meta name="description" content="Overwatch OSINT for Omarchy"></head></html>'
    )


def test_hud_probe_only_hits_known_ports(tmp_path: Path):
    seen = []

    def getter(url, headers=None):
        seen.append(url)
        if url == api.HUD_PREVIEW_URL:
            return 200, OVERWATCH_HTML, {}
        if url in _all_empty_map():
            return _getter_from_map(_all_empty_map())(url, headers)
        raise OSError(f"unexpected url {url}")

    payload = api.collect_layers(
        getter=getter,
        root=tmp_path,
        now=NOW,
        refresh=True,
        probe=True,
    )
    assert payload["hud"]["reachable"] is True
    assert payload["hud"]["identified"] is True
    assert payload["hud"]["reachable_url"] == api.HUD_PREVIEW_URL
    assert payload["hud"]["aigc_studio_url"] == api.AIGC_STUDIO_URL
    assert "status pane" in payload["hud"]["note"]
    allowed_hosts = {
        "earthquake.usgs.gov",
        "eonet.gsfc.nasa.gov",
        "opensky-network.org",
        "api.weather.gov",
        "firms.modaps.eosdis.nasa.gov",
        "127.0.0.1",
    }
    for url in seen:
        host = (urlparse(url).hostname or "").lower()
        assert host in allowed_hosts
        if host == "127.0.0.1":
            assert urlparse(url).port in {4173, 5173}
    assert api.HUD_DEV_URL not in seen


def test_hud_probe_down_is_honest(tmp_path: Path):
    mapping = _all_empty_map()

    def getter(url, headers=None):
        if url in (api.HUD_PREVIEW_URL, api.HUD_DEV_URL):
            raise OSError("connection refused")
        return _getter_from_map(mapping)(url, headers)

    payload = api.collect_layers(
        getter=getter,
        root=tmp_path,
        now=NOW,
        refresh=True,
        probe=True,
    )
    assert payload["hud"]["reachable"] is False
    assert payload["hud"]["identified"] is False
    assert payload["hud"]["reachable_url"] is None
    assert payload["ok"] is True


def test_hud_probe_rejects_pack_builder_on_5173(tmp_path: Path):
    seen = []

    def getter(url, headers=None):
        seen.append(url)
        if url == api.HUD_PREVIEW_URL:
            raise OSError("connection refused")
        if url == api.HUD_DEV_URL:
            return 200, PACK_BUILDER_HTML, {}
        if url == api.AIGC_STUDIO_URL:
            raise AssertionError("AIGC Studio :5174 must not be probed")
        if url in _all_empty_map():
            return _getter_from_map(_all_empty_map())(url, headers)
        raise OSError(f"unexpected url {url}")

    payload = api.collect_layers(
        getter=getter,
        root=tmp_path,
        now=NOW,
        refresh=True,
        probe=True,
    )
    assert payload["hud"]["reachable"] is False
    assert payload["hud"]["identified"] is False
    assert payload["hud"]["reachable_url"] is None
    assert api.HUD_DEV_URL in seen
    assert api.AIGC_STUDIO_URL not in seen


def test_hud_probe_skips_wrong_4173_and_accepts_overwatch_5173(tmp_path: Path):
    def getter(url, headers=None):
        if url == api.HUD_PREVIEW_URL:
            return 200, PACK_BUILDER_HTML, {}
        if url == api.HUD_DEV_URL:
            return 200, OVERWATCH_HTML, {}
        if url in _all_empty_map():
            return _getter_from_map(_all_empty_map())(url, headers)
        raise OSError(f"unexpected url {url}")

    payload = api.collect_layers(
        getter=getter,
        root=tmp_path,
        now=NOW,
        refresh=True,
        probe=True,
    )
    assert payload["hud"]["reachable"] is True
    assert payload["hud"]["identified"] is True
    assert payload["hud"]["reachable_url"] == api.HUD_DEV_URL


def test_hud_probe_prefers_identified_4173_over_5173(tmp_path: Path):
    seen = []

    def getter(url, headers=None):
        seen.append(url)
        if url == api.HUD_PREVIEW_URL:
            return 200, OVERWATCH_HTML, {}
        if url == api.HUD_DEV_URL:
            return 200, OVERWATCH_HTML, {}
        if url in _all_empty_map():
            return _getter_from_map(_all_empty_map())(url, headers)
        raise OSError(f"unexpected url {url}")

    payload = api.collect_layers(
        getter=getter,
        root=tmp_path,
        now=NOW,
        refresh=True,
        probe=True,
    )
    assert payload["hud"]["reachable_url"] == api.HUD_PREVIEW_URL
    assert api.HUD_DEV_URL not in seen


def test_hud_probe_studio_title_on_either_port_is_not_the_hud(tmp_path: Path):
    def getter(url, headers=None):
        if url in (api.HUD_PREVIEW_URL, api.HUD_DEV_URL):
            return 200, STUDIO_HTML, {}
        if url in _all_empty_map():
            return _getter_from_map(_all_empty_map())(url, headers)
        raise OSError(f"unexpected url {url}")

    payload = api.collect_layers(
        getter=getter,
        root=tmp_path,
        now=NOW,
        refresh=True,
        probe=True,
    )
    assert payload["hud"]["reachable"] is False
    assert payload["hud"]["reachable_url"] is None
    assert payload["layers"]


def test_layer_defs_cover_requested_feeds():
    labels = [row["label"] for row in api.LAYER_DEFS]
    assert labels == ["USGS", "EONET", "ADS-B", "NWS", "AIS", "FIRMS"]


def test_cases_are_not_in_layer_payload(tmp_path: Path):
    payload = api.collect_layers(
        getter=_getter_from_map(_all_empty_map()),
        root=tmp_path,
        now=NOW,
        refresh=True,
        probe=False,
    )
    blob = json.dumps(payload)
    assert "omarchy-overwatch.cases.v1" in blob
    assert '"cases":' not in blob
    assert "pins" not in blob


def test_desktop_plugin_registers_palette_and_right_pane():
    js = (ROOT / "desktop" / "plugin.js").read_text(encoding="utf-8")
    register = js.split("register(ctx)", 1)[1]
    assert register.count("area: PANES_AREA") == 1
    assert "id: 'keep'" not in js
    assert "placement: 'floating'" not in js
    assert "Second pane so Close" not in js
    assert "PANES_AREA" in js
    assert "placement: 'right'" in js
    assert "width: '760px'" in js
    assert "Open Overwatch HUD" in js
    assert "Open Overwatch pane" in js
    assert "http://127.0.0.1:4173/" in js
    assert "http://127.0.0.1:5173/" in js
    assert "http://127.0.0.1:5174/" in js
    assert "AIGC Studio" in js
    assert "overwatch osint" in js
    assert "Backend not reachable" in js
    assert "Quit Hermes Desktop" in js
    assert "Reload desktop plugins" in js
    assert "profiles/<name>/plugins" in js
    assert "reachableUrl || preview" not in js
    assert "identified" in js
    assert "omarchy-overwatch.cases.v1" in js
    yaml = (ROOT / "plugin.yaml").read_text(encoding="utf-8")
    assert "name: smf-overwatch-pane" in yaml
    assert "author: SMF Works" in yaml
    assert "kind: standalone" in yaml


def test_desktop_plugin_iframes_identified_hud_without_plugin_api():
    """The pane embeds the HUD the way AIGC Studio iframes a local URL.

    /layers failure stays a badge. The iframe src is only a title-verified
    :4173 or :5173 URL — never a raw port, and never the unmounted ErrorState.
    """
    js = (ROOT / "desktop" / "plugin.js").read_text(encoding="utf-8")
    assert "function HudFrame" in js
    assert "id: 'smf-overwatch-pane-frame'" in js
    assert "sandbox: IFRAME_SANDBOX" in js
    assert (
        "allow-scripts allow-same-origin allow-forms allow-popups allow-downloads allow-modals"
        in js
    )
    assert "src: url" in js
    assert "src: HUD_PREVIEW" not in js
    assert "src: HUD_DEV" not in js
    assert "enabled: !apiChecked" in js
    assert "verifiedHudUrl(hud)" in js
    assert "function BackendBadge" in js
    assert "The HUD iframe does not need that API" in js
    pane = js.split("function OverwatchPane", 1)[1].split("function StatusChipHost", 1)[0]
    assert "jsx(HudFrame" in pane
    assert "jsx(BackendBadge" in pane
    assert "ErrorState" not in pane
    assert "Loading Overwatch layers" not in js
    # Identity gate stays in front of the frame. A pack-builder title must not match.
    assert "function htmlIdentifiesAsOverwatch" in js
    assert "if (url !== HUD_PREVIEW && url !== HUD_DEV) return null" in js
