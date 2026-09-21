# SMF Overwatch pane — agent notes

For Hermes/OpenClaw installing this repo on a user's machine.

## Do

1. Clone to `$HOME/.hermes/plugins/smf-overwatch-pane` (or `hermes plugins install smfworks/smf-overwatch-pane --enable`).
2. Run `bash install.sh` from that tree. It enables the plugin on `$HOME/.hermes` **and** every `profiles/*/plugins` home, then copies `desktop/plugin.js` to `$HOME/.hermes/desktop-plugins/smf-overwatch-pane/`.
3. Tell the user the HUD iframe works as soon as desktop JS reloads, when `:4173` or verified `:5173` titles as **Overwatch OSINT**. Quit Hermes Desktop and relaunch from the menu only so `plugin_api.py` mounts and `/layers` can fill the secondary strip. For a named profile, that Python file is the one under `HERMES_HOME/profiles/<name>/plugins/smf-overwatch-pane` (for example `~/.hermes/profiles/james/plugins`). The iframe does not read that directory.

## Do not

- Do not treat ⌘K → Reload desktop plugins as a backend remount. That is JS only. **Backend not reachable** means `/layers` is unread. Show that as a small badge on the HUD iframe. Do not replace the iframe with a full-pane error, and do not invent layer points to fill the gap. When the HUD is not identified, still offer GitHub, Retry, and the identity note.
- Do not run `hermes desktop` to relaunch if a packaged Electron binary already exists (`…/linux-unpacked/Hermes --no-sandbox`). `hermes desktop` rewrites the `.desktop` `Exec=` and can prompt for `chrome-sandbox` sudo.
- Do not `hermes serve --stop` (kills every serve on the box). Do not kill this chat's backend from inside the same Desktop window unless the user asked for a relaunch.
- Do not invent earthquakes, events, aircraft, alerts, vessels, or fires. A failed refresh must serve disk cache with `status: stale` / age, or `status: err` + empty `sample` if there is no cache. Empty successful reads stay `live` with `count: 0`.
- Do not fetch AISStream, open extra ports, or scan. AIS is **OFF** in this pane (no public REST). HUD probe is only `127.0.0.1:4173` then `:5173`, and a port counts only when the HTML title identifies as **Overwatch OSINT**. Do not treat the AIGC pack builder, sparkDash, or any other Vite on those ports as the HUD. AIGC Studio is `127.0.0.1:5174` and is not probed.
- Do not read or sync Overwatch case notes (`localStorage` key `omarchy-overwatch.cases.v1`). Point the user at the HUD.
- Do not modify `smfworks/omarchy-overwatch`. Do not `git reset --hard` an existing plugin checkout.

## After relaunch

Sidebar **Overwatch**, the right-of-chat pane, or ⌘K → Open Overwatch pane. When the HUD title matches, that column **is** the Overwatch HUD in an iframe (AIGC Studio’s local-URL pattern). It is not AIGC Studio (`:5174`) and it is not the pack builder. ⌘K → Open Overwatch HUD opens preview `http://127.0.0.1:4173/` when that page identifies as Overwatch OSINT, otherwise dev `http://127.0.0.1:5173/` only with the same identity. Optional status-bar chip shows ERR/STALE counts only when `/layers` actually returned those statuses — never a guessed number.
