# SMF Overwatch Pane — Hermes Desktop Plugin

A [Hermes Agent](https://github.com/NousResearch/hermes-agent) desktop plugin that embeds the [Overwatch HUD](https://github.com/smfworks/omarchy-overwatch) in a column to the right of chat, the same way AIGC Studio iframes a local URL. Layer status is a secondary strip. The iframe does not need `plugin_api.py`, and it does not invent tracks.

Public OSINT only. No scanning, no credential stuffing, no case-note sync.

## What it does

- **Right pane** — Overwatch HUD iframe, docked to the right of the workspace (`760px`), after the page title identifies as Overwatch OSINT
- **Sidebar + palette** — Overwatch, plus ⌘K → **Open Overwatch pane**. ⌘K → **Open Overwatch HUD** opens the HUD only after the page title identifies as Overwatch OSINT (`http://127.0.0.1:4173/` preferred, then `http://127.0.0.1:5173/`)
- **Works without `plugin_api.py`** — the desktop JS probes `:4173` then `:5173` itself and iframes the HUD when the HTML `<title>` contains **Overwatch OSINT**. A failed or unread `GET /layers` does not replace that iframe with a full-pane error. The quit-and-relaunch note stays a small **Backend not reachable** badge
- **Layer status** — secondary strip (USGS, EONET, ADS-B, NWS, AIS, FIRMS as **LIVE** / **STALE** / **ERR** / **OFF**) when the Python API is mounted for the profile Hermes is actually serving
- **Open Overwatch HUD** — preview `http://127.0.0.1:4173/`, or dev `http://127.0.0.1:5173/`, only when that response’s HTML title contains **Overwatch OSINT**. Another Vite app on those ports is not opened or iframed as the HUD. **AIGC Studio** is a different app at `http://127.0.0.1:5174/`
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

**⌘K → Reload desktop plugins is JS only.** It does not mount `plugin_api.py`. The HUD iframe does not need that mount: once `:4173` or `:5173` titles as Overwatch OSINT, the pane embeds it. **Backend not reachable** is a badge on that embed when `/layers` is unread. Quit Desktop and launch it again only if you want the layer strip.

`GET /layers` is served by whichever profile Desktop spawned. A profile such as `james` reads `HERMES_HOME/profiles/james/plugins` (usually `~/.hermes/profiles/james/plugins`). That folder must contain `smf-overwatch-pane` or the profile’s serve never mounts the Python API. `install.sh` enables the plugin on `$HOME/.hermes` and every `profiles/*/plugins` home it can see. The iframe path does not read that folder.

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
The HUD iframe works without plugin_api.py when :4173 or :5173 titles as Overwatch OSINT.
Quit and relaunch only so /layers mounts. For a named profile, plugins must live in HERMES_HOME/profiles/<name>/plugins.
```

## Usage

- The pane’s main view is the HUD iframe (sandbox `allow-scripts allow-same-origin allow-forms allow-popups allow-downloads allow-modals`). **Layers** opens the status strip under the chrome.
- Watch layer chips when `/layers` is mounted. **LIVE** means the public endpoint responded recently. **STALE** is cached points after a failed refresh (age shown). **ERR** is a failed fetch with nothing cached. **OFF** is not fetched (AIS here).
- **Refresh** forces a network pull per public layer. Failures keep the last cached sample on that row — they do not invent a replacement.
- **Open** launches the same verified HUD URL outside the pane. Preview `:4173` wins when its HTML title is Overwatch OSINT. Dev `:5173` is used only when that page identifies as Overwatch — a pack builder or sparkDash on `:5173` is left alone. AIGC Studio (`http://127.0.0.1:5174/`) is a different app.
- Case notes stay in the HUD origin’s `localStorage`. This pane does not read or sync them.

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
| `GET /layers` | Probe public feeds independently. Disk cache under the Hermes home (`cache/smf-overwatch-pane/`) with TTL. `?refresh=1` bypasses TTL. Per-layer failure → cached sample + `status: stale` + age, or `status: err` + empty sample. AIS is `off` (no public REST). HUD probe is `127.0.0.1:4173` then `:5173`, and `reachable_url` is set only when the HTML title identifies as Overwatch OSINT (`identified: true`). |
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

Network is not required. Parsers, status, cache, HUD identity checks, and probe allowlisting run against fixtures.

## License

MIT — SMF Works
