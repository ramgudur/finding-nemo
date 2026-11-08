# Demo runbook

How to bring the platform up, run the two demo apps, check the public page on a phone, and what to do when something fails live.

## One-time setup

```bash
cd backend && python -m venv .venv && .venv/Scripts/pip install -r requirements.txt   # macOS/Linux: .venv/bin/pip
cd ../frontend && npm install
```

On Windows, run the scripts from Git Bash.

**Environment.** Copy `backend/.env.example` to `backend/.env`. It is gitignored, so never commit it.

- **The core demo needs no keys.** The map, hotspots, overrides, spoof flags, error chart and sim controls are all deterministic code.
- **Agents need keys.** Set `NVIDIA_API_KEY` for the Critic and Compiler, and `OPENROUTER_API_KEY` for the Lightning models. Without them, agents skip their runs and write a `rate budget` / skipped entry to the trace.
- **Don't set `DEMO_MODE` in `.env`.** `tools/demo` turns it on for its own backend.
- **Never paste keys into the board, a PR or a screenshot.**

## Start everything (one command)

```bash
tools/demo campus-alert          # or: tools/demo flyway
tools/demo campus-alert --fresh  # empty demo DB and new keys
tools/demo flyway --no-sim       # bring it up without running the simulator
```

What `tools/demo` does:
1. Starts the backend on :8000 with `DEMO_MODE=1` and its own DB in `data/demo.db`.
2. Starts the frontend on :5173, also bound to your LAN IP for phones.
3. Creates and activates the app from `specs/<app>.json`. Activation is the recorded dev confirmation.
4. Stores the keys in `data/demo-keys-<app>.json` (gitignored). The platform shows them only once, at activation.
5. Prints the console URL and the authority key.
6. Runs the simulator with `--follow-clock`, so the console's Pause/Speed controls drive it.

Ctrl-C stops everything. Logs go to `data/demo-backend.log` and `data/demo-frontend.log`.

Once it's up:
1. Open `http://localhost:5173/apps/<app>`.
2. Paste the authority key, and tick "remember on this device" if it's your machine.
3. You'll see the live map, layer toggles, hotspots, weights, verification scores and agent trace. In demo mode you also get the **Simulation** controls and the **Error vs ground truth** chart.
4. Without the key, the same page shows exactly what a public caller gets.

## Demo 1: Campus Alert (about 4 min at 1x)

Public safety on a generic site (a fairground). All times are sim seconds; the console shows wall time.

| t | What happens | What to show |
|---|---|---|
| 0–15 s | 48 people press panic and flee from a moving threat | The score heat map lights up around the threat. The `panic.flow_source` layer points back at it; the error chart's orange line (point estimate) drops to about 10–30 m. |
| 5–30 s | Three groups hide in rooms (stationary pings) | Dwell and density show the groups. They don't drag the flow estimate. |
| 60 s | Room alert button (locked authority) | An override disk at the room: the score is 1.0 there, whatever the crowd says (rule 6). |
| 100 s | Spoof burst: 10 accounts fake a straight, uniform escape from a decoy, and 6 new accounts report the exact same coordinates | A second hotspot near the decoy. Point out it's suspicious; with the Critic running (#92), trust drops and the error chart gets a marker. |
| 150 s | Police marker (locked authority) | A second override at the threat's last position. |
| 180 s | Scripted news article (`[SCRIPTED DEMO]`) on the local RSS feed at :8765 | Once the connector engine (#80) is in, the stiff news layer appears. Until then the feed is served but not ingested. |
| 240 s | End of script | The map keeps ticking and layers decay. |

- **Pacing:** use **Pause** to talk over a moment and **Speed** (0.5–8x) to skip ahead. The sim and the engine move together.
- **Public vs authority:** remove the key ("Forget key"). Campus Alert is authority-only, so the public map is empty. Public end users get instructions only (rule 8).

## Demo 2: Flyway (75 min of tag data in about 4 min at the default 20x)

Wildlife around White Rock Lake, Dallas: 12 tagged birds dwell at three shore stopovers and fly between them (`sim/replays/flyway_short.jsonl`, deterministic).

- **Dwell:** `tags.dwell` heat builds at the stopovers as birds linger, and hotspots sit on them (the e2e test checks within 150 m).
- **Plausibility:** fast, straight, steady flight is normal for birds. The spec's `plausibility` turns those spoof checks off, so no bird is flagged.
- **Visibility:** `score` is authority-only because it derives from private tag layers. Only `wind.field` is public.
- **Wind:** a pull input (Open-Meteo preset). It needs the connector engine (#80); until then the wind layer is empty.
- **Live compile:** compiling the same GPS input into a different operator needs the Compiler (#81). Show the hand-written spec until then.

## Phone over a tunnel (test before judging)

The end-user page must work on a phone over a tunnel.

1. Start the stack with `tools/demo campus-alert`.
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
| Agent runs show `skipped: rate budget` in the trace | NVIDIA (about 40 RPM shared) or OpenRouter limits | Keep going: map, hotspots, overrides and spoof flags don't need the LLM. **OpenRouter free models cap at 50 requests/day** (unless the account has ever bought $10+ of credit), and failed requests count. Don't restart agents in a loop before the demo. |
| Console says "polling" instead of "live" | WebSocket blocked (tunnel, proxy, Wi-Fi) | Nothing. The console falls back to REST every 2 s and keeps retrying the WS (backoff up to 15 s). |
| Console shows `500 Internal Server Error for /apps/...` | Backend down; the frontend proxy answers 500 | Check `data/demo-backend.log`; restart `tools/demo`. |
| `demo: ... is active but its keys aren't in data/...` | The demo DB was reused but its keys file was deleted | `tools/demo <app> --fresh` (new keys). |
| Sim prints `ingest key rejected` or `app or input not found` | Wrong or rotated key, or the app isn't active | Use `tools/demo`, which wires the key in, or pass `--ingest-key` from `data/demo-keys-<app>.json`. |
| Sim stalls about 2 s per event on Windows | `localhost` resolving to IPv6 first | Already handled (the sim uses 127.0.0.1); use `--api http://127.0.0.1:8000` for custom runs. |
| Pause/Speed do nothing to the sim | The sim was started without `--follow-clock` | `tools/demo` always passes it; add it for manual runs. |
| Map tiles are blank | No internet (OpenStreetMap tiles) | The heat layers still render on the grey background; bring a hotspot or tether. |
| Ticks feel slow | Tick work grows with grid size and inputs | Measured about 10–15 ms per tick for campus-alert (80×80). Not a risk at demo sizes. |

## Known gaps (as of this runbook)
- **End-user instructions page:** step 6, #94/#95.
- **Connector engine (RSS news, Open-Meteo wind):** #80.
- **Critic agent and triggers:** #92, in review.
- **Compiler and live compile:** #81.

None of these blocks the core map demo above.
