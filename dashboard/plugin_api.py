"""SMF Overwatch pane — public live-layer status for Hermes Desktop.

Probes the same public OSINT endpoints Omarchy Overwatch visualizes
(USGS, EONET, OpenSky ADS-B, NWS, NASA FIRMS). AISStream has no public
REST snapshot, so AIS is ``off`` here — never invented vessels.

Never invents points, counts, or headlines. Disk cache under the Hermes
home with TTL. A failed refresh serves that layer's cache with
``status: stale`` and age, or ``status: err`` when there is nothing cached.
Empty successful reads stay ``live`` with ``count: 0``.

``GET /layers`` query params:

* ``refresh`` — ``1`` / ``true`` bypasses TTL and hits the network.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

try:
    from fastapi import APIRouter
    from fastapi.responses import JSONResponse

    router = APIRouter()
except ImportError:  # tests / hosts without FastAPI still import the helpers
    APIRouter = None  # type: ignore[misc, assignment]
    JSONResponse = None  # type: ignore[misc, assignment]
    router = None

PLUGIN = "smf-overwatch-pane"
USER_AGENT = (
    "SMF-Overwatch-Pane/1.0 (+https://github.com/smfworks/smf-overwatch-pane)"
)
LAYER_TTL_SECONDS = 5 * 60
STALE_SECONDS = 15 * 60
HTTP_TIMEOUT = 20
FIRMS_TIMEOUT = 25
HUD_PROBE_TIMEOUT = 2
POINT_CAP = 80
SAMPLE_CAP = 4
OPENSKY_STRIDE = 18

HUD_PREVIEW_URL = "http://127.0.0.1:4173/"
HUD_DEV_URL = "http://127.0.0.1:5173/"
GITHUB_URL = "https://github.com/smfworks/omarchy-overwatch"
CASES_KEY = "omarchy-overwatch.cases.v1"
CASES_NOTE = (
    "Cases live in the Overwatch HUD browser localStorage key "
    "omarchy-overwatch.cases.v1. This pane does not read or sync them."
)

USGS_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.geojson"
EONET_URL = "https://eonet.gsfc.nasa.gov/api/v3/events?limit=40&status=open"
OPENSKY_URL = "https://opensky-network.org/api/states/all"
NWS_URL = "https://api.weather.gov/alerts/active?status=actual&limit=50"
FIRMS_CSV_URL = (
    "https://firms.modaps.eosdis.nasa.gov/data/active_fire/"
    "suomi-npp-viirs-c2/csv/SUOMI_VIIRS_C2_Global_24h.csv"
)

ALLOWED_FETCH_HOSTS = frozenset({
    "earthquake.usgs.gov",
    "eonet.gsfc.nasa.gov",
    "opensky-network.org",
    "api.weather.gov",
    "firms.modaps.eosdis.nasa.gov",
})

LAYER_DEFS: Tuple[Dict[str, Any], ...] = (
    {
        "id": "earthquakes",
        "label": "USGS",
        "kind": "quake",
        "url": USGS_URL,
        "note": "USGS earthquakes M2.5+ past 24h via earthquake.usgs.gov (no key).",
        "public": True,
    },
    {
        "id": "eonet",
        "label": "EONET",
        "kind": "event",
        "url": EONET_URL,
        "note": "NASA EONET open natural events (fires, storms, volcanoes).",
        "public": True,
    },
    {
        "id": "opensky",
        "label": "ADS-B",
        "kind": "aircraft",
        "url": OPENSKY_URL,
        "note": (
            "OpenSky sampled aircraft states. Anonymous REST is often blocked; "
            "401/429 are ERR, not fake tracks."
        ),
        "public": True,
    },
    {
        "id": "nws",
        "label": "NWS",
        "kind": "alert",
        "url": NWS_URL,
        "note": "api.weather.gov active alerts. US-only, no key. Alerts without geometry are skipped.",
        "public": True,
    },
    {
        "id": "ais",
        "label": "AIS",
        "kind": "vessel",
        "url": None,
        "note": (
            "AISStream maritime snapshot. No public REST; the HUD needs "
            "AISSTREAM_API_KEY. This pane does not open a WebSocket and will not invent vessels."
        ),
        "public": False,
    },
    {
        "id": "firms",
        "label": "FIRMS",
        "kind": "fire",
        "url": FIRMS_CSV_URL,
        "note": "NASA FIRMS VIIRS detections (public 24h CSV). Sampled by FRP; no invented fires.",
        "public": True,
    },
)

HttpGetter = Callable[[str, Optional[Dict[str, str]]], Tuple[int, str, Dict[str, str]]]


# ---------------------------------------------------------------------------
# Time / numbers / hosts
# ---------------------------------------------------------------------------

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_datetime(value: Any) -> Optional[str]:
    """Return an ISO-8601 UTC timestamp, or None. Never guesses a date."""
    dt = parse_datetime_obj(value)
    return iso_utc(dt) if dt is not None else None


def parse_datetime_obj(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 1e12:
            ts /= 1000.0
        if ts <= 0:
            return None
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def as_finite_number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        n = float(value)
        return n if n == n and abs(n) != float("inf") else None
    if isinstance(value, str) and value.strip():
        try:
            n = float(value.strip())
        except ValueError:
            return None
        if n == n and abs(n) != float("inf"):
            return n
    return None


def valid_lat_lng(lat: Optional[float], lng: Optional[float]) -> bool:
    if lat is None or lng is None:
        return False
    return -90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0


def host_allowed(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return host in ALLOWED_FETCH_HOSTS


def hud_url_allowed(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    if host not in {"127.0.0.1", "localhost"}:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    port = parsed.port
    return port in {4173, 5173}


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def classify_status(
    *,
    enabled: bool,
    updated_at: Optional[str],
    error: Optional[str],
    now: Optional[datetime] = None,
    stale_seconds: int = STALE_SECONDS,
) -> str:
    """LIVE / STALE / ERR / OFF — same honesty rules as the Overwatch HUD."""
    if not enabled:
        return "off"
    dt = parse_datetime_obj(updated_at)
    if error and dt is None:
        return "err"
    if dt is None:
        return "err"
    as_of = now.astimezone(timezone.utc) if now is not None else utcnow()
    age = (as_of - dt).total_seconds()
    if error:
        return "stale"
    if age > stale_seconds:
        return "stale"
    return "live"


def describe_layer_http_error(status: int, layer: Optional[str] = None, detail: Optional[str] = None) -> str:
    hint = (detail or "").strip()
    if layer == "opensky" and status == 401:
        canned = (
            "HTTP 401 unauthorized — OpenSky rejected this client. Anonymous access is often blocked. "
            "No aircraft were invented."
        )
        return f"{canned} ({hint})" if hint and hint not in canned else canned
    if layer == "opensky" and status == 403:
        canned = "HTTP 403 forbidden — OpenSky denied this request. No aircraft were invented."
        return f"{canned} ({hint})" if hint and hint not in canned else canned
    if layer == "opensky" and status == 429:
        canned = (
            "HTTP 429 rate limited — OpenSky quota exhausted. Wait, or authenticate in the HUD. "
            "No aircraft were invented."
        )
        return f"{canned} ({hint})" if hint and hint not in canned else canned
    if layer == "firms" and status in {401, 403}:
        canned = (
            f"HTTP {status} — FIRMS public CSV blocked. The HUD can retry with FIRMS_MAP_KEY. "
            "No fires were invented."
        )
        return f"{canned} ({hint})" if hint and hint not in canned else canned
    if status == 401:
        return hint if hint.startswith("HTTP 401") else (hint or "HTTP 401 unauthorized")
    if status == 403:
        return hint if hint.startswith("HTTP 403") else (hint or "HTTP 403 forbidden")
    if status == 429:
        return hint if hint.startswith("HTTP 429") else (hint or "HTTP 429 rate limited")
    if hint:
        return hint if hint.startswith("HTTP ") else f"HTTP {status} — {hint}"
    return f"HTTP {status}"


def empty_layer(spec: Dict[str, Any], **overrides: Any) -> Dict[str, Any]:
    row = {
        "id": spec["id"],
        "label": spec["label"],
        "kind": spec["kind"],
        "enabled": bool(spec.get("public")),
        "status": "off",
        "count": 0,
        "updated_at": None,
        "error": None,
        "note": spec.get("note") or "",
        "url": spec.get("url"),
        "sample": [],
        "from_cache": False,
        "cache_age_seconds": None,
        "stale": False,
    }
    row.update(overrides)
    row["status"] = classify_status(
        enabled=bool(row["enabled"]),
        updated_at=row.get("updated_at"),
        error=row.get("error"),
        now=overrides.get("_now"),
    )
    row.pop("_now", None)
    row["stale"] = row["status"] == "stale"
    row["count"] = int(row.get("count") or 0)
    if not isinstance(row.get("sample"), list):
        row["sample"] = []
    return row


def normalize_point(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    lat = as_finite_number(raw.get("lat"))
    lng = as_finite_number(raw.get("lng"))
    if not valid_lat_lng(lat, lng):
        return None
    label = str(raw.get("label") or "").strip()
    kind = str(raw.get("kind") or "").strip() or "event"
    ident = str(raw.get("id") or "").strip()
    if not ident:
        ident = f"{kind}-{lat:.3f}-{lng:.3f}"
    extra = raw.get("extra")
    if extra is not None:
        extra = str(extra).strip() or None
    point: Dict[str, Any] = {
        "id": ident[:160],
        "lat": lat,
        "lng": lng,
        "label": label or kind,
        "kind": kind,
    }
    if extra:
        point["extra"] = extra
    mag = as_finite_number(raw.get("mag"))
    if mag is not None:
        point["mag"] = mag
    return point


def points_payload(points: Sequence[Dict[str, Any]]) -> Tuple[int, List[Dict[str, Any]]]:
    kept: List[Dict[str, Any]] = []
    for raw in points:
        item = normalize_point(raw) if isinstance(raw, dict) else None
        if item:
            kept.append(item)
        if len(kept) >= POINT_CAP:
            break
    return len(kept), kept[:SAMPLE_CAP]


# ---------------------------------------------------------------------------
# Parsers — copy source fields, never invent geodata
# ---------------------------------------------------------------------------

def parse_json_body(body: str) -> Any:
    text = (body or "").strip()
    if not text:
        raise ValueError("empty payload")
    if text[:1] in "<":
        raise ValueError("payload was HTML, not JSON")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("payload was not JSON") from exc


def parse_usgs(payload: Any) -> List[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    features = payload.get("features")
    if not isinstance(features, list):
        return []
    points: List[Dict[str, Any]] = []
    for i, feat in enumerate(features):
        if not isinstance(feat, dict):
            continue
        geom = feat.get("geometry") if isinstance(feat.get("geometry"), dict) else {}
        coords = geom.get("coordinates") if isinstance(geom, dict) else None
        if not isinstance(coords, list) or len(coords) < 2:
            continue
        lng = as_finite_number(coords[0])
        lat = as_finite_number(coords[1])
        if not valid_lat_lng(lat, lng):
            continue
        props = feat.get("properties") if isinstance(feat.get("properties"), dict) else {}
        mag = as_finite_number(props.get("mag")) if isinstance(props, dict) else None
        place = ""
        if isinstance(props, dict) and isinstance(props.get("place"), str):
            place = props["place"].strip()
        mag_label = f"{mag:.1f}" if mag is not None else "?"
        ident = str(feat.get("id") or i)
        extra = None
        if isinstance(props, dict) and props.get("time") is not None:
            extra = parse_datetime(props.get("time"))
        raw = {
            "id": f"eq-{ident}",
            "lat": lat,
            "lng": lng,
            "mag": mag,
            "label": f"M{mag_label} {place or 'earthquake'}",
            "kind": "quake",
            "extra": extra,
        }
        point = normalize_point(raw)
        if point:
            points.append(point)
        if len(points) >= POINT_CAP:
            break
    return points


def _last_eonet_coords(geometry: Any) -> Optional[Tuple[float, float]]:
    if not isinstance(geometry, list) or not geometry:
        return None
    last = geometry[-1]
    if not isinstance(last, dict):
        return None
    coords = last.get("coordinates")
    if isinstance(coords, list) and len(coords) >= 2 and isinstance(coords[0], (int, float)):
        lng = as_finite_number(coords[0])
        lat = as_finite_number(coords[1])
        if valid_lat_lng(lat, lng):
            return lat, lng  # type: ignore[return-value]
    if isinstance(coords, list) and coords and isinstance(coords[0], list):
        pair = coords[0]
        if isinstance(pair, list) and len(pair) >= 2:
            lng = as_finite_number(pair[0])
            lat = as_finite_number(pair[1])
            if valid_lat_lng(lat, lng):
                return lat, lng  # type: ignore[return-value]
    return None


def parse_eonet(payload: Any) -> List[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    events = payload.get("events")
    if not isinstance(events, list):
        return []
    points: List[Dict[str, Any]] = []
    for i, ev in enumerate(events):
        if not isinstance(ev, dict):
            continue
        pair = _last_eonet_coords(ev.get("geometry"))
        if not pair:
            continue
        lat, lng = pair
        title = ""
        if isinstance(ev.get("title"), str):
            title = ev["title"].strip()
        extra = None
        geom = ev.get("geometry")
        if isinstance(geom, list) and geom and isinstance(geom[-1], dict):
            extra = geom[-1].get("date")
            if extra is not None:
                extra = str(extra)
        ident = str(ev.get("id") or i)
        point = normalize_point({
            "id": f"eonet-{ident}",
            "lat": lat,
            "lng": lng,
            "label": title or "EONET event",
            "kind": "event",
            "extra": extra,
        })
        if point:
            points.append(point)
        if len(points) >= POINT_CAP:
            break
    return points


def parse_opensky(payload: Any, *, stride: int = OPENSKY_STRIDE, limit: int = POINT_CAP) -> List[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    states = payload.get("states")
    if not isinstance(states, list):
        return []
    sampled = [row for i, row in enumerate(states) if i % max(1, stride) == 0]
    points: List[Dict[str, Any]] = []
    for i, row in enumerate(sampled):
        if not isinstance(row, list) or len(row) < 7:
            continue
        lng = as_finite_number(row[5])
        lat = as_finite_number(row[6])
        if not valid_lat_lng(lat, lng):
            continue
        callsign = row[1].strip() if isinstance(row[1], str) else ""
        icao = row[0].strip() if isinstance(row[0], str) else ""
        extra = row[2].strip() if isinstance(row[2], str) else ""
        point = normalize_point({
            "id": f"ac-{icao or i}",
            "lat": lat,
            "lng": lng,
            "label": callsign or icao or "aircraft",
            "kind": "aircraft",
            "extra": extra or None,
        })
        if point:
            points.append(point)
        if len(points) >= limit:
            break
    return points


def _walk_coords(value: Any) -> List[List[float]]:
    if not isinstance(value, list):
        return []
    if len(value) >= 2 and isinstance(value[0], (int, float)) and isinstance(value[1], (int, float)):
        lng = as_finite_number(value[0])
        lat = as_finite_number(value[1])
        if valid_lat_lng(lat, lng):
            return [[lng, lat]]  # type: ignore[list-item]
        return []
    out: List[List[float]] = []
    for item in value:
        out.extend(_walk_coords(item))
    return out


def centroid(geom: Any) -> Optional[Tuple[float, float]]:
    if not isinstance(geom, dict):
        return None
    pts = _walk_coords(geom.get("coordinates"))
    if not pts:
        return None
    lon = sum(p[0] for p in pts) / len(pts)
    lat = sum(p[1] for p in pts) / len(pts)
    if not valid_lat_lng(lat, lon):
        return None
    return lat, lon


def parse_nws(payload: Any) -> List[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    features = payload.get("features")
    if not isinstance(features, list):
        return []
    points: List[Dict[str, Any]] = []
    for i, feat in enumerate(features):
        if not isinstance(feat, dict):
            continue
        pair = centroid(feat.get("geometry"))
        if not pair:
            continue
        lat, lng = pair
        props = feat.get("properties") if isinstance(feat.get("properties"), dict) else {}
        event = ""
        area = None
        if isinstance(props, dict):
            if isinstance(props.get("event"), str):
                event = props["event"].strip()
            if isinstance(props.get("areaDesc"), str):
                area = props["areaDesc"].strip() or None
        ident = str(feat.get("id") or i)
        point = normalize_point({
            "id": f"nws-{ident}",
            "lat": lat,
            "lng": lng,
            "label": event or "NWS alert",
            "kind": "alert",
            "extra": area,
        })
        if point:
            points.append(point)
        if len(points) >= 60:
            break
    return points


def extract_ais_vessel(message: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(message, dict):
        return None
    if isinstance(message.get("error"), str) and message["error"].strip():
        return None
    meta = message.get("MetaData") or message.get("Metadata") or {}
    if not isinstance(meta, dict):
        meta = {}
    msg_type = message.get("MessageType") if isinstance(message.get("MessageType"), str) else ""
    body = None
    nested = message.get("Message")
    if msg_type and isinstance(nested, dict):
        candidate = nested.get(msg_type)
        if isinstance(candidate, dict):
            body = candidate
    body = body or {}
    lat = as_finite_number(meta.get("Latitude", meta.get("latitude", body.get("Latitude", body.get("latitude")))))
    lng = as_finite_number(meta.get("Longitude", meta.get("longitude", body.get("Longitude", body.get("longitude")))))
    if not valid_lat_lng(lat, lng):
        return None
    mmsi_raw = meta.get("MMSI", meta.get("mmsi", body.get("UserID", body.get("UserId"))))
    mmsi = str(mmsi_raw).strip() if mmsi_raw is not None else ""
    name_raw = meta.get("ShipName", meta.get("shipName", meta.get("Ship_Name")))
    name = name_raw.strip() if isinstance(name_raw, str) else ""
    extra = " · ".join(p for p in [msg_type or None, f"MMSI {mmsi}" if mmsi else None] if p)
    return {
        "mmsi": mmsi or None,
        "name": name or None,
        "lat": lat,
        "lng": lng,
        "extra": extra or None,
    }


def parse_ais_snapshot(payload: Any, limit: int = POINT_CAP) -> List[Dict[str, Any]]:
    """Parse an AIS snapshot if one is supplied. The pane never fetches AIS."""
    if not isinstance(payload, dict):
        return []
    if isinstance(payload.get("error"), str) and payload["error"].strip():
        if not payload.get("vessels") and not payload.get("messages"):
            return []
    records: List[Dict[str, Any]] = []
    if isinstance(payload.get("vessels"), list):
        for row in payload["vessels"]:
            if not isinstance(row, dict):
                continue
            lat = as_finite_number(row.get("lat"))
            lng = as_finite_number(row.get("lng"))
            if not valid_lat_lng(lat, lng):
                continue
            name = row.get("name")
            records.append({
                "mmsi": str(row["mmsi"]).strip() if row.get("mmsi") is not None else None,
                "name": name.strip() if isinstance(name, str) else None,
                "lat": lat,
                "lng": lng,
                "extra": row.get("extra") if isinstance(row.get("extra"), str) else None,
            })
    elif isinstance(payload.get("messages"), list):
        for message in payload["messages"]:
            vessel = extract_ais_vessel(message)
            if vessel:
                records.append(vessel)
    seen = set()
    points: List[Dict[str, Any]] = []
    for rec in records:
        key = rec.get("mmsi") or f"{rec['lat']:.4f},{rec['lng']:.4f}"
        if key in seen:
            continue
        seen.add(key)
        name = rec.get("name") or ""
        mmsi = rec.get("mmsi") or ""
        point = normalize_point({
            "id": f"ais-{mmsi or f'{rec['lat']:.3f}-{rec['lng']:.3f}'}",
            "lat": rec["lat"],
            "lng": rec["lng"],
            "label": name or (f"MMSI {mmsi}" if mmsi else "vessel"),
            "kind": "vessel",
            "extra": rec.get("extra"),
        })
        if point:
            points.append(point)
        if len(points) >= limit:
            break
    return points


def parse_firms_csv(text: str, limit: int = POINT_CAP) -> List[Dict[str, Any]]:
    trimmed = (text or "").strip()
    if not trimmed:
        return []
    if re.search(r"invalid map[_ ]?key", trimmed, re.I):
        raise ValueError("FIRMS rejected MAP_KEY")
    if trimmed.startswith("<"):
        raise ValueError("FIRMS returned HTML instead of CSV")
    header_line = trimmed.splitlines()[0].lower() if trimmed else ""
    if "latitude" not in header_line or "longitude" not in header_line:
        raise ValueError("FIRMS CSV missing latitude/longitude columns")
    reader = csv.DictReader(io.StringIO(trimmed))
    scored: List[Tuple[float, Dict[str, Any]]] = []
    for i, row in enumerate(reader):
        if not row:
            continue
        lower = {str(k).strip().lower(): (v or "").strip() for k, v in row.items() if k}
        lat = as_finite_number(lower.get("latitude"))
        lng = as_finite_number(lower.get("longitude"))
        if not valid_lat_lng(lat, lng):
            continue
        frp = as_finite_number(lower.get("frp")) or 0.0
        sat = lower.get("satellite") or lower.get("instrument") or "VIIRS"
        conf = lower.get("confidence") or ""
        when = " ".join(p for p in [lower.get("acq_date"), lower.get("acq_time")] if p)
        extra = " · ".join(
            p for p in [
                sat,
                f"conf {conf}" if conf else None,
                f"FRP {frp}" if frp else None,
                when or None,
            ] if p
        )
        point = normalize_point({
            "id": f"fire-{lower.get('acq_date') or 'd'}-{lower.get('acq_time') or i}-{lat:.3f}-{lng:.3f}",
            "lat": lat,
            "lng": lng,
            "label": f"Fire {lat:.2f}°, {lng:.2f}°",
            "kind": "fire",
            "extra": extra or None,
        })
        if point:
            scored.append((frp, point))
    scored.sort(key=lambda item: item[0], reverse=True)
    seen = set()
    points: List[Dict[str, Any]] = []
    for _frp, point in scored:
        key = f"{point['lat']:.3f},{point['lng']:.3f}"
        if key in seen:
            continue
        seen.add(key)
        points.append(point)
        if len(points) >= limit:
            break
    return points


# ---------------------------------------------------------------------------
# HTTP + cache
# ---------------------------------------------------------------------------

def hermes_home(root: Optional[Path] = None) -> Path:
    if root is not None:
        return Path(root)
    env = os.environ.get("HERMES_HOME")
    if env:
        return Path(env)
    return Path.home() / ".hermes"


def cache_dir(root: Optional[Path] = None) -> Path:
    d = hermes_home(root) / "cache" / PLUGIN
    d.mkdir(parents=True, exist_ok=True)
    return d


def layer_cache_path(layer_id: str, root: Optional[Path] = None) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", layer_id)[:80]
    return cache_dir(root) / f"{safe}.json"


def layers_cache_path(root: Optional[Path] = None) -> Path:
    return cache_dir(root) / "layers.json"


def load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def save_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def default_http_get(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: Optional[float] = None,
) -> Tuple[int, str, Dict[str, str]]:
    hdrs = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if headers:
        hdrs.update(headers)
    req = Request(url, headers=hdrs, method="GET")
    wait = HTTP_TIMEOUT if timeout is None else timeout
    if timeout is None and "firms.modaps.eosdis.nasa.gov" in url:
        wait = FIRMS_TIMEOUT
    try:
        with urlopen(req, timeout=wait) as resp:
            body = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
            text = body.decode(charset, errors="replace")
            info = {k.lower(): v for k, v in resp.headers.items()}
            return int(getattr(resp, "status", 200) or 200), text, info
    except HTTPError as exc:
        try:
            err_body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            err_body = ""
        raise OSError(describe_layer_http_error(int(exc.code), _layer_id_for_url(url), err_body[:180])) from exc
    except URLError as exc:
        raise OSError(f"network error for {url}: {exc.reason}") from exc


def _layer_id_for_url(url: str) -> Optional[str]:
    host = (urlparse(url).hostname or "").lower()
    if "usgs" in host:
        return "earthquakes"
    if "eonet" in host:
        return "eonet"
    if "opensky" in host:
        return "opensky"
    if "weather.gov" in host:
        return "nws"
    if "firms" in host:
        return "firms"
    return None


def _http(
    getter: HttpGetter,
    url: str,
    headers: Optional[Dict[str, str]] = None,
    *,
    layer: Optional[str] = None,
) -> str:
    if not host_allowed(url):
        raise OSError(f"host not allowlisted: {url}")
    status, body, _ = getter(url, headers)
    if status >= 400:
        raise OSError(describe_layer_http_error(int(status), layer, (body or "")[:180]))
    return body


def _cache_age(cached: Optional[Dict[str, Any]], as_of: datetime) -> Optional[float]:
    if not cached:
        return None
    dt = parse_datetime_obj(cached.get("updated_at") or cached.get("fetched_at"))
    if dt is None:
        return None
    return (as_of - dt).total_seconds()


def _layer_from_cache(spec: Dict[str, Any], cached: Dict[str, Any], *, as_of: datetime, error: Optional[str] = None) -> Dict[str, Any]:
    age = _cache_age(cached, as_of)
    sample = cached.get("sample") if isinstance(cached.get("sample"), list) else []
    count = cached.get("count")
    if not isinstance(count, int):
        count = len(sample)
    err = error if error is not None else cached.get("error")
    row = empty_layer(
        spec,
        enabled=True,
        updated_at=cached.get("updated_at"),
        error=err,
        count=count,
        sample=[p for p in sample if isinstance(p, dict)][:SAMPLE_CAP],
        from_cache=True,
        cache_age_seconds=int(age) if age is not None else None,
        _now=as_of,
    )
    return row


def fetch_public_layer(
    spec: Dict[str, Any],
    getter: HttpGetter,
    *,
    as_of: datetime,
) -> Dict[str, Any]:
    layer_id = spec["id"]
    url = spec["url"]
    if layer_id == "earthquakes":
        body = _http(getter, url, {"Accept": "application/geo+json, application/json"}, layer=layer_id)
        points = parse_usgs(parse_json_body(body))
    elif layer_id == "eonet":
        body = _http(getter, url, {"Accept": "application/json"}, layer=layer_id)
        points = parse_eonet(parse_json_body(body))
    elif layer_id == "opensky":
        body = _http(getter, url, {"Accept": "application/json"}, layer=layer_id)
        points = parse_opensky(parse_json_body(body))
    elif layer_id == "nws":
        body = _http(
            getter,
            url,
            {"Accept": "application/geo+json, application/json"},
            layer=layer_id,
        )
        points = parse_nws(parse_json_body(body))
    elif layer_id == "firms":
        body = _http(getter, url, {"Accept": "text/csv, text/plain, */*"}, layer=layer_id)
        points = parse_firms_csv(body)
    else:
        raise OSError(f"unknown public layer {layer_id}")
    count, sample = points_payload(points)
    return empty_layer(
        spec,
        enabled=True,
        updated_at=iso_utc(as_of),
        error=None,
        count=count,
        sample=sample,
        from_cache=False,
        cache_age_seconds=0,
        _now=as_of,
    )


def collect_ais_layer(spec: Dict[str, Any], *, as_of: datetime) -> Dict[str, Any]:
    """AIS is not a public REST feed. OFF, with an empty sample — never invented."""
    return empty_layer(
        spec,
        enabled=False,
        updated_at=None,
        error=None,
        count=0,
        sample=[],
        from_cache=False,
        cache_age_seconds=None,
        _now=as_of,
    )


def collect_layer(
    spec: Dict[str, Any],
    *,
    getter: HttpGetter,
    root: Optional[Path],
    as_of: datetime,
    ttl: int,
    refresh: bool,
) -> Dict[str, Any]:
    if not spec.get("public"):
        return collect_ais_layer(spec, as_of=as_of)

    path = layer_cache_path(spec["id"], root)
    cached = load_json(path)
    age = _cache_age(cached, as_of)
    if (
        not refresh
        and cached
        and age is not None
        and 0 <= age < ttl
        and cached.get("updated_at")
        and not cached.get("error")
    ):
        return _layer_from_cache(spec, cached, as_of=as_of)

    try:
        row = fetch_public_layer(spec, getter, as_of=as_of)
        save_json(path, {
            "id": row["id"],
            "updated_at": row["updated_at"],
            "count": row["count"],
            "sample": row["sample"],
            "error": None,
            "fetched_at": iso_utc(as_of),
        })
        return row
    except Exception as exc:
        if cached and cached.get("updated_at"):
            return _layer_from_cache(spec, cached, as_of=as_of, error=str(exc))
        return empty_layer(
            spec,
            enabled=True,
            updated_at=None,
            error=str(exc),
            count=0,
            sample=[],
            from_cache=False,
            cache_age_seconds=None,
            _now=as_of,
        )


def default_hud(*, reachable: Optional[bool] = None, reachable_url: Optional[str] = None) -> Dict[str, Any]:
    return {
        "preview_url": HUD_PREVIEW_URL,
        "dev_url": HUD_DEV_URL,
        "github_url": GITHUB_URL,
        "reachable": reachable,
        "reachable_url": reachable_url,
        "cases_key": CASES_KEY,
        "cases_note": CASES_NOTE,
    }


def probe_hud(getter: HttpGetter) -> Dict[str, Any]:
    """Check the known Overwatch preview/dev ports only. No port scan."""
    for url in (HUD_PREVIEW_URL, HUD_DEV_URL):
        if not hud_url_allowed(url):
            continue
        try:
            status, _body, _ = getter(url, {"Accept": "text/html, */*"})
            if 200 <= int(status) < 500:
                return default_hud(reachable=True, reachable_url=url)
        except Exception:
            continue
    return default_hud(reachable=False, reachable_url=None)


def collect_layers(
    *,
    getter: Optional[HttpGetter] = None,
    root: Optional[Path] = None,
    now: Optional[datetime] = None,
    ttl: int = LAYER_TTL_SECONDS,
    refresh: bool = False,
    probe: bool = False,
) -> Dict[str, Any]:
    getter = getter or default_http_get
    as_of = now.astimezone(timezone.utc) if now is not None else utcnow()
    layers = [
        collect_layer(spec, getter=getter, root=root, as_of=as_of, ttl=ttl, refresh=refresh)
        for spec in LAYER_DEFS
    ]
    errors: List[Dict[str, Any]] = []
    for layer in layers:
        if layer.get("status") == "err" and layer.get("error"):
            errors.append({
                "kind": layer["id"],
                "path": layer.get("url"),
                "error": layer["error"],
                "required": False,
            })
        elif layer.get("status") == "stale" and layer.get("error"):
            errors.append({
                "kind": layer["id"],
                "path": layer.get("url"),
                "error": layer["error"],
                "required": False,
            })

    live = sum(1 for layer in layers if layer.get("status") == "live")
    stale = sum(1 for layer in layers if layer.get("status") == "stale")
    err = sum(1 for layer in layers if layer.get("status") == "err")
    off = sum(1 for layer in layers if layer.get("status") == "off")
    enabled_layers = [layer for layer in layers if layer.get("enabled")]
    hud = probe_hud(getter) if probe else default_hud()
    payload = {
        "ok": True,
        "plugin": PLUGIN,
        "stale": stale > 0 and err == 0 and live == 0,
        "read_status": "ok",
        "empty": live == 0 and stale == 0 and all(layer.get("count", 0) == 0 for layer in layers),
        "fetched_at": iso_utc(as_of),
        "layers": layers,
        "summary": {"live": live, "stale": stale, "err": err, "off": off, "total": len(layers)},
        "hud": hud,
        "errors": errors,
        "warnings": [],
        "from_cache": bool(enabled_layers) and all(layer.get("from_cache") for layer in enabled_layers),
    }
    save_json(layers_cache_path(root), {
        "fetched_at": payload["fetched_at"],
        "summary": payload["summary"],
    })
    return payload


def _json(payload: Dict[str, Any], status: int = 200):
    if JSONResponse is None:
        return payload
    return JSONResponse(payload, status_code=status)


def _truthy(value: Optional[str]) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "refresh"}


if router is not None:

    @router.get("/layers")
    def layers(refresh: Optional[str] = None):
        try:
            payload = collect_layers(refresh=_truthy(refresh), probe=True)
        except Exception as exc:  # pragma: no cover - defensive mount
            return _json({
                "ok": False,
                "plugin": PLUGIN,
                "stale": False,
                "read_status": "unread",
                "empty": False,
                "layers": [],
                "summary": {"live": 0, "stale": 0, "err": 0, "off": 0, "total": 0},
                "hud": default_hud(),
                "errors": [{"kind": "layers", "path": None, "error": str(exc), "required": True}],
                "warnings": [],
                "fetched_at": parse_datetime(utcnow()),
                "from_cache": False,
            }, status=200)
        return _json(payload)

    @router.get("/health")
    def health() -> dict:
        return {"status": "ok", "plugin": PLUGIN}
