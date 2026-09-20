# SMF Overwatch Pane — Hermes Desktop Plugin

A [Hermes Agent](https://github.com/NousResearch/hermes-agent) desktop plugin that puts **Omarchy Overwatch** live-layer status in a wide column to the right of chat. It complements the [Overwatch web HUD](https://github.com/smfworks/omarchy-overwatch) — it does not replace the globe, and it does not invent tracks.

Public OSINT only. No scanning, no credential stuffing, no case-note sync.

## What it does

- **Right pane** — Overwatch, docked to the right of the workspace (`400px`)
- **Sidebar + palette** — Overwatch, plus ⌘K → **Open Overwatch pane**. ⌘K → **Open Overwatch** launches the HUD at `http://127.0.0.1:4173/`
- **Layer status** — USGS, EONET, ADS-B, NWS, AIS, FIRMS as **LIVE** / **STALE** / **ERR** / **OFF**, with sampled counts and source labels when the public endpoint actually returned them
- **Open Overwatch HUD** — `http://127.0.0.1:4173/` (Vite preview). Dev server is `http://127.0.0.1:5173`
- **GitHub** — link to the Overwatch app repo
- **Cases tip** — investigation notes live in the HUD browser `localStorage` key `omarchy-overwatch.cases.v1`. This pane does not read or sync them into Hermes
- **Status bar** — optional chip when any layer is ERR or STALE
- **Honesty** — empty successful reads stay LIVE with count 0. Fetch failures serve disk cache as STALE with age, or ERR with `sample: []` when there is nothing cached. Points are never invented

## Install

One shot (covers profiles + Desktop JS + enable):

```bash
git clone https://github.com/smfworks/smf-overwatch-pane.git ~/.hermes/plugins/smf-overwatch-pane
bash ~/.hermes/plugins/smf-overwatch-pane/install.sh
```

Or, if Hermes is already on PATH:

```bash
hermes plugins install smfworks/smf-overwatch-pane --enable
bash "${HERMES_HOME:-$HOME/.hermes}/plugins/smf-overwatch-pane/install.sh"
```

`install.sh` enables the plugin on `$HOME/.hermes` **and** every `profiles/*/plugins` home Desktop may spawn, copies `desktop/plugin.js` into `$HOME/.hermes/desktop-plugins/smf-overwatch-pane/` (what packaged Electron actually loads), and tells you to **quit and relaunch Desktop**.

**⌘K → Reload desktop plugins is JS only.** It does not mount `plugin_api.py`. If Overwatch says **Backend not reachable**, the serve process started before enable — quit Desktop and launch it again.

Do **not** run `hermes desktop` to relaunch if you already have the packaged Linux binary. That command rewrites the `.desktop` `Exec=` and can prompt for `chrome-sandbox` sudo. Use the menu entry / `…/linux-unpacked/Hermes --no-sandbox`.

Then:

1. Settings → Plugins → Overwatch → on
2. The **Overwatch** pane on the right of chat, or Sidebar → **Overwatch**, or ⌘K → Open Overwatch pane

### Give this to a Hermes agent

```
Install SMF Overwatch pane from https://github.com/smfworks/smf-overwatch-pane
Run bash ~/.hermes/plugins/smf-overwatch-pane/install.sh (clone first if missing).
Enable on $HOME/.hermes and every profiles/*/ that already has a plugins dir.
Copy desktop/plugin.js to $HOME/.hermes/desktop-plugins/smf-overwatch-pane/.
Do not run hermes desktop. Do not kill this chat from inside it.
Tell me to quit Hermes Desktop and relaunch from the menu so plugin_api.py mounts.
```

## Usage

- Watch layer chips. **LIVE** means the public endpoint responded recently. **STALE** is cached points after a failed refresh (age shown). **ERR** is a failed fetch with nothing cached. **OFF** is not fetched (AIS here).
- **Refresh** forces a network pull per public layer. Failures keep the last cached sample on that row — they do not invent a replacement.
- **Open Overwatch HUD** opens `http://127.0.0.1:4173/` (preview). If you are on `npm run dev`, use `http://127.0.0.1:5173`.
- Case notes stay in the HUD. Do not expect them in this pane.

## Architecture

```
smf-overwatch-pane/
├── install.sh
├── AGENTS.md
├── plugin.yaml
├── __init__.py
├── dashboard/
│   ├── manifest.json        # api: plugin_api.py
│   └── plugin_api.py        # GET /layers  GET /health
├── desktop/
│   └── plugin.js            # copy to ~/.hermes/desktop-plugins/smf-overwatch-pane/
└── tests/
    ├── fixtures/
    └── test_plugin_api.py
```

| Route | What it does |
|-------|----------------|
| `GET /layers` | Probe public feeds independently. Disk cache under the Hermes home (`cache/smf-overwatch-pane/`) with TTL. `?refresh=1` bypasses TTL. Per-layer failure → cached sample + `status: stale` + age, or `status: err` + empty sample. AIS is `off` (no public REST). |
| `GET /health` | `{ status: ok, plugin }` |

Layers (implemented):

| Layer | Source | This pane |
|--------|--------|-----------|
| USGS | `earthquake.usgs.gov` GeoJSON (M2.5+ day) | public GET |
| EONET | NASA EONET open events | public GET |
| ADS-B | `opensky-network.org` `/api/states/all` | public GET; 401/429 are ERR |
| NWS | `api.weather.gov` active alerts | public GET; alerts without geometry are skipped |
| AIS | AISStream WebSocket snapshot | **OFF** — no public REST; HUD needs `AISSTREAM_API_KEY` |
| FIRMS | NASA FIRMS VIIRS 24h CSV | public GET; HTML/blocked responses are ERR |

Optional keys for the **HUD** (`AISSTREAM_API_KEY`, OpenSky OAuth, `FIRMS_MAP_KEY`) stay in the Overwatch Vite process. This plugin does not collect them and does not sync `omarchy-overwatch.cases.v1`.

Normalized layer row:

```
{ id, label, kind, enabled, status, count, updated_at, error, note, url, sample[], from_cache, cache_age_seconds, stale }
```

`sample` is a short list of points the source actually returned (id, lat, lng, label, kind, extra?). Count is the sampled cap used by the HUD parsers, not a fabricated world total.

## Tests

```bash
python3 -m pytest tests/ -q
```

Network is not required. Parsers, status, cache, and HUD-probe allowlisting run against fixtures.

## License

MIT — SMF Works
