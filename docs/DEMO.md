# Demo runbook

How to bring the platform up, build the demo app live in the Builder, run the simulator against it, check the public page on a phone, and what to do when something fails live.

The demo app is **built live** on camera: type the intent and inputs in the Builder, the Compiler (Nemotron) proposes a spec, you review it, confirm, and the keys are shown once. The simulator then stands in for the dev's own app and its users. `specs/*.json` are test fixtures and the emergency fallback only.

## One-time setup

```bash
cd backend && python -m venv .venv && .venv/Scripts/pip install -r requirements.txt   # macOS/Linux: .venv/bin/pip
cd ../frontend && npm install
```

On Windows, run the scripts from Git Bash.

**Environment.** Copy `backend/.env.example` to `backend/.env`. It is gitignored, so never commit it.

- **The live build needs `NVIDIA_API_KEY`.** The Compiler and every agent run on build.nvidia.com, so it is the only key needed (`OPENROUTER_API_KEY` is optional). Copy the `*_MODEL` lines from `backend/.env.example`; the old Super ID returns 404. Without a key, compile fails and agents skip their runs (a `rate budget` / skipped entry in the trace).
- **The map itself needs no keys.** Map, hotspots, overrides, spoof flags, error chart and sim controls are deterministic code, so the `--from-spec` fallback runs with no key at all.
- **Don't set `DEMO_MODE` in `.env`.** `tools/demo` turns it on for its own backend.
- **Never paste keys into the board, a PR or a screenshot.**

## Start everything (one command)

```bash
tools/demo                                   # Builder-first: servers up, then asks for the app id and keys
tools/demo --fresh                           # same, from an empty demo DB
tools/demo --app campus-alert --ingest-key <key> --authority-key <key>   # keys on the command line, no prompt
tools/demo --no-sim                          # servers only (build and browse, no simulator)
tools/demo --from-spec campus-alert          # emergency fallback: hand-written spec, no live compile
```

What `tools/demo` does (default mode):
1. Starts the backend on :8000 with `DEMO_MODE=1` and its own DB in `data/demo.db` (`--fresh` wipes it).
2. Starts the frontend on :5173, also bound to your LAN IP for phones.
3. Prints the Builder URL, `http://localhost:5173/`.
4. Waits for the app you build there: it takes `--app`, `--ingest-key` and optionally `--authority-key` from the command line, or asks for them on the terminal (app id defaults to `campus-alert`; press Enter to skip the authority key). The keys aren't stored anywhere.
5. Runs the Campus Alert simulator with `--follow-clock`, so the console's Pause/Speed controls drive it. With `--authority-key`, the sim reads the app's spec first, stops with a list of any inputs it needs that the app doesn't have, and drops event fields the app didn't declare. The authority key also lets `tools/demo` resume the server clock at 1x.

Options passed through to the sim: `--input-map panic=<id>,room_button=<id>,police_marker=<id>` if you named the inputs differently in the Builder (default: the same ids).

`--from-spec <campus-alert|flyway>` is the old path: it creates and activates the app from `specs/<app>.json`, stores the keys in `data/demo-keys-<app>.json` (gitignored), prints them, and runs the matching sim.

Ctrl-C stops everything. Logs go to `data/demo-backend.log` and `data/demo-frontend.log`.

## Demo: Campus Alert, built live

Public safety on a generic site (a fairground at Fair Park, Dallas).

### 1. Build it in the Builder

Run `tools/demo` (add `--fresh` if `campus-alert` already exists in the demo DB) and open `http://localhost:5173/`. Type exactly this:

**App id:** `campus-alert`

**What should the map tell you?**

> During an emergency on a large site, find where the danger is from panic-button phones, room alert buttons, police markers and local news, and tell people who are able to leave the safest way out.

**Area:** pan and zoom the map to Fair Park, Dallas, so the view covers the fairground, then press **Use this view as the area**. The sim's events fall in this box:

| | lat | lon |
|---|---|---|
| south-west | 32.777703 | -96.762137 |
| north-east | 32.781296 | -96.757863 |

That box is about 400 x 400 m; a view a little larger is fine (events outside the area are dropped, so don't cut it off). Set **Cell size (m)** to `5`. The grid readout must stay under 250,000 cells (the fairground box at 5 m is 80 x 80); if it's over, zoom in and press the button again.

**Inputs** (fill the first row, press "Add input" for each next one; ids must match exactly, because the sim posts to them):

| id | kind | shape | fields | meaning |
|---|---|---|---|---|
| `panic` | push | track | `press_id:string?` | When someone presses the panic button, their phone starts streaming its GPS position every couple of seconds. People who can, run away from the danger; people who can't, hide where they are. |
| `room_button` | push | fixed_point | `room:string, cleared:boolean?` | Staff pressed the alert button in their room: danger is confirmed at or near this room. The alert stays until it is cleared. |
| `police_marker` | push | fixed_point | `cleared:boolean?` | Police mark the threat's last known position. The marker stays until police clear it. |
| `local_news` | pull | document | (none) | Local news reports about incidents on or near the site, from the RSS feed at http://localhost:8765/campus-news.xml (served by the simulator). |

The Builder has no connector or URL field, so the `local_news` meaning must name the feed: that is how the Compiler knows to configure the RSS connector. The simulator serves that feed only while it runs; `DEMO_MODE` allows the localhost URL.

### 2. Compile and review

Press **Compile**. It calls Nemotron live (`NVIDIA_API_KEY`) and takes about 30 to 100 s, so talk over it. Then check the proposed spec:

- **panic:** tier `flexible`, with a `flow_source` operator (density alongside is fine) and plausibility limits (max speed, constant-speed and straight-line flags). This is what finds the threat and flags the spoof burst.
- **room_button** and **police_marker:** tier `locked` with an `override` operator. That's why their score is 1.0 whatever the crowd says (rule 6).
- **local_news:** an `rss` connector with `http://localhost:8765/campus-news.xml`. If it doesn't come back that way, the news beat is skipped; the rest of the demo is unaffected.
- **Labels:** a label set along the lines of threat origin, gathering point, shelter-in-place group (sensitive), suspected spoof cluster (sensitive).
- **Routing:** on, with destinations, so the end-user page can show the safest way out.
- **Visibility:** layers authority-only (no warning that score is public while other layers are private).

If something is off, edit the text and press **Compile again**.

### 3. Confirm and start the sim

Press **Confirm and activate**. The ingest key and authority key are shown **once**: copy both (Copy buttons). Then, in the terminal running `tools/demo`, paste them at the prompts, or restart it with:

```bash
tools/demo --app campus-alert --ingest-key <ingest key> --authority-key <authority key>
```

(Don't pass `--fresh` on that restart, or the app you just built is wiped.)

If the sim stops saying an input is missing, it lists what the app needs: add that input in a rebuilt app, or point the sim at your ids with `--input-map panic=<id>,room_button=<id>,police_marker=<id>`.

### 4. Show it

1. Open the console, `http://localhost:5173/apps/campus-alert` (the "Open the console" link after confirm), and paste the authority key. Tick "remember on this device" only on your own machine.
2. You'll see the live map, layer toggles, hotspots, weights, verification scores and agent trace. In demo mode you also get the **Simulation** controls and the **Error vs ground truth** chart.
3. Then open the end-user page, `http://localhost:5173/apps/campus-alert/go`. Without the key, it shows exactly what a public caller gets.

### Timeline (about 4 min at 1x)

All times are sim seconds from when the sim starts; the console shows wall time.

| t | What happens | What to show |
|---|---|---|
| before | Builder: intent, area, inputs, compile (30–100 s), review, confirm, keys | The dev's own words become a reviewed spec; keys shown once. |
| 0–15 s | 48 people press panic and flee from a moving threat | The score heat map lights up around the threat. The `panic.flow_source` layer points back at it; the error chart's orange line (point estimate) drops to about 10–30 m. |
| 5–30 s | Three groups hide in rooms (stationary pings) | Dwell and density show the groups. They don't drag the flow estimate. |
| 60 s | Room alert button (locked authority) | An override disk at the room: the score is 1.0 there, whatever the crowd says (rule 6). |
| 100 s | Spoof burst: 10 accounts fake a straight, uniform escape from a decoy, and 6 new accounts report the exact same coordinates | A second hotspot near the decoy. Point out it's suspicious; with the Critic running, trust drops and the error chart gets a marker. |
| 150 s | Police marker (locked authority) | A second override at the threat's last position. |
| 180 s | Scripted news article (`[SCRIPTED DEMO]`) on the sim's RSS feed at :8765 | If the Compiler set up the RSS connector, the stiff news layer appears. |
| 240 s | End of script | The map keeps ticking and layers decay. |

- **Pacing:** use **Pause** to talk over a moment and **Speed** (0.5–8x) to skip ahead. The sim and the engine move together.
- **Public vs authority:** remove the key ("Forget key"). Campus Alert is authority-only, so the public map is empty. Public end users get instructions only (rule 8).

`specs/flyway.json` and `sim/flyway.py` remain as a test fixture (`tools/demo --from-spec flyway` still runs it); they are not part of the demo.

## Phone over a tunnel (test before judging)

The end-user page must work on a phone over a tunnel.

1. Start the stack with `tools/demo` and build `campus-alert` in the Builder (or `tools/demo --from-spec campus-alert` for a quick check).
2. Expose the frontend:
   ```bash
   cloudflared tunnel --url http://localhost:5173     # prints https://<random>.trycloudflare.com; proxies WebSockets
   npx localtunnel --port 5173                        # fallback; if the console says "polling", the WS didn't pass
   ```
   The frontend proxies `/v1` (REST and WS) to the backend, so only port 5173 needs a tunnel.
3. On the phone, open `https://<tunnel>/apps/campus-alert/go` (the public page) and `/apps/campus-alert` (the console).
4. From the laptop, take a true 390×844 layout screenshot (written to `data/phone-check/`):
   ```bash
   backend/.venv/Scripts/python tools/phone_check.py https://<tunnel>/apps/campus-alert/go https://<tunnel>/apps/campus-alert
   ```
   A plain headless screenshot at 390 px is misleading: browsers clamp the window to about 480 px and crop. The script renders the page in a 390 px iframe instead.
5. Check for:
   - no sideways scroll
   - text readable without zoom
   - the key field and buttons reachable with a thumb
   - the "live" label: if it says "polling", the WebSocket didn't connect through the tunnel; REST polling every 2 s still works

**Checked so far:** the console at 390 px. Header, key form and hint wrap cleanly, and the map fits the width. The end-user page is still a placeholder until routing and instructions land (step 6, @kida-k:a's #94/#95). Re-run the check once it does.

## If something fails live

| Symptom | Likely cause | What to do |
|---|---|---|
| Compile takes much longer than 100 s, or fails | NVIDIA is slow or rate-limited, or the model returned an invalid spec | Press **Compile again** once. If it still fails, Ctrl-C and fall back to `tools/demo --from-spec campus-alert --fresh` (hand-written spec, keys printed). |
| Compile fails straight away | No `NVIDIA_API_KEY` in `backend/.env` | Add it and restart `tools/demo`, or use `--from-spec campus-alert`. |
| Builder says the grid is too big | The view is too large for 5 m cells (limit 250,000 cells) | Zoom in on the fairground and press "Use this view as the area" again. |
| Confirm says the app already exists | `campus-alert` is in the demo DB from an earlier run | Restart with `tools/demo --fresh`, or use another app id and pass it with `--app`. |
| Sim says an input is missing | An input id or field in the Builder doesn't match what the sim posts | It lists what to type: rebuild with those ids, or map yours with `--input-map panic=<id>,room_button=<id>,police_marker=<id>`. |
| No news layer at t=180 s | The Compiler didn't set up `local_news` as an rss connector with the feed URL | Skip the beat; the rest of the demo is unaffected. Next time make sure the meaning names the feed URL. |
| Agent runs show `skipped: rate budget` in the trace | NVIDIA's limit (about 40 requests/min, shared by every agent) | Keep going: map, hotspots, overrides and spoof flags don't need the LLM. The Critic and POI Labeler share one rate budget; don't restart agents in a loop before the demo. If OpenRouter is configured as an optional extra, its free models cap at 50 requests/day and failed requests count. |
| Console says "polling" instead of "live" | WebSocket blocked (tunnel, proxy, Wi-Fi) | Nothing. The console falls back to REST every 2 s and keeps retrying the WS (backoff up to 15 s). |
| Console shows `500 Internal Server Error for /apps/...` | Backend down; the frontend proxy answers 500 | Check `data/demo-backend.log`; restart `tools/demo` (without `--fresh`, so the app and keys survive). |
| `demo: ... is active but its keys aren't in data/...` | `--from-spec` reused the demo DB but its keys file was deleted | `tools/demo --from-spec <app> --fresh` (new keys). |
| Sim prints `ingest key rejected` or `app or input not found` | Wrong key, wrong app id, or the app isn't confirmed yet | Re-paste the ingest key from the Builder and check `--app`; with `--from-spec` the key is in `data/demo-keys-<app>.json`. |
| Lost the keys | They're shown only once at confirm | Rebuild with `tools/demo --fresh`, or fall back to `--from-spec campus-alert`. |
| Sim stalls about 2 s per event on Windows | `localhost` resolving to IPv6 first | Already handled (the sim uses 127.0.0.1); use `--api http://127.0.0.1:8000` for custom runs. |
| Pause/Speed do nothing to the sim | The sim was started without `--follow-clock` | `tools/demo` always passes it; add it for manual runs. |
| Map tiles are blank | No internet (OpenStreetMap tiles) | The heat layers still render on the grey background; bring a hotspot or tether. Without tiles, finding Fair Park in the Builder is hard, so use `--from-spec campus-alert`. |
| Ticks feel slow | Tick work grows with grid size and inputs | Measured about 10–15 ms per tick for campus-alert (80×80). Not a risk at demo sizes. |

## Known gaps (as of this runbook)
- **End-user instructions page:** step 6, #94/#95.
- **Connector engine (RSS news):** #80.
- **Critic agent and triggers:** #92, in review.

None of these blocks the core map demo above.
