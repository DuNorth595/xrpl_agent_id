# xrpl_agent_id — Live Dashboard

> Real-time view of agent identity activity on XRPL testnet.

The dashboard is a stdlib-only HTTP server that reads from a SQLite DB
fed by a websocket subscriber. It displays identity events (DIDSet /
DIDDelete), credential events (CredentialCreate / Accept / Delete), and
the state of any agent addresses you add to the watchlist.

## Quick start

```bash
# 1. (Optional) Backfill the DB with historical data so the UI shows
#    something immediately.
python -m xrpl_agent_id.dashboard.backfill

# 2. Start the dashboard server (defaults to http://127.0.0.1:8768).
python -m xrpl_agent_id.dashboard.server

# 3. In a separate terminal, start the monitor so live testnet traffic
#    gets ingested into the same DB.
python -m xrpl_agent_id.dashboard.monitor
```

Open <http://127.0.0.1:8768> in your browser.

## Architecture

```
┌─────────────────────┐         ┌─────────────────────┐
│ XRPL Testnet (wss)  │ ◀────── │ monitor.py          │
│ transactions stream │         │  filters:           │
└─────────────────────┘         │   - CredentialCreate│
                                │   - CredentialAccept│
                                │   - CredentialDelete│
                                │   - DIDSet          │
                                │   - DIDDelete       │
                                └──────────┬──────────┘
                                           │ INSERT
                                           ▼
                                ┌─────────────────────┐
                                │ SQLite WAL          │
                                │ xrpl_agent_id_      │
                                │ dashboard.db        │
                                └──────────┬──────────┘
                                           │ SELECT
                                           ▼
┌─────────────────────┐         ┌─────────────────────┐
│ Browser             │ ◀────── │ server.py           │
│ index.html +        │ poll /  │  /api/summary       │
│ dashboard.js        │ 2s      │  /api/credentials   │
└─────────────────────┘         │  /api/identities    │
                                │  /api/watchlist     │
                                │  /api/agent_state   │
                                │  /api/health        │
                                └─────────────────────┘
```

## What the panels show

- **Summary** — total counts + last update time
- **Tracked agents** — one card per watched address, showing its DID
  status and the credential events involving it (with color-coded
  pills: teal `Create`, purple `Accept`, red `Delete`)
- **Connection health** — recent monitor events (connect, disconnect,
  subscribe_ack, error)
- **Recent credentials** — last 25 credential events with full
  (issuer, subject, type, URI, tx hash linked to bithomp)
- **Recent identity events** — last 25 DIDSet / DIDDelete events
- **Watchlist** — addresses the monitor tracks with role + label

## API endpoints

| Endpoint | Returns |
|---|---|
| `GET /api/summary` | Counts, last events, watchlist size, last monitor event |
| `GET /api/credentials?limit=N` | Most recent N credential events |
| `GET /api/identities?limit=N` | Most recent N identity events |
| `GET /api/watchlist` | All watched addresses |
| `GET /api/agent_state` | Per-agent: DID status + all credential events involving them |
| `GET /api/health?limit=N` | Last N monitor events (connect/disconnect/error) |

All return JSON.

## Managing the watchlist

The `watchlist` table is the source of truth. Insert addresses manually
with `sqlite3` or via your own scripts:

```sql
INSERT INTO watchlist (address, role, label, added_at)
VALUES ('rPNGAy...', 'agent', 'My Agent', strftime('%s', 'now'));
```

Roles: `agent`, `issuer`, `observer`.

## Network

Default is **XRPL testnet** (`wss://s.altnet.rippletest.net:51233`).
Override with `--network {mainnet,devnet,testnet}`. Mainnet is supported
but currently not used by default.

## Files

- `xrpl_agent_id/dashboard/db.py` — schema + insert helpers
- `xrpl_agent_id/dashboard/monitor.py` — websocket subscriber
- `xrpl_agent_id/dashboard/server.py` — HTTP server + JSON API
- `xrpl_agent_id/dashboard/backfill.py` — seed DB from results/
- `xrpl_agent_id/dashboard/templates/index.html` — UI markup
- `xrpl_agent_id/dashboard/templates/dashboard.js` — UI polling logic
- `xrpl_agent_id_dashboard.db` — SQLite (gitignored)

## Testing

The dashboard ships with a smoke test that exercises the db layer and
the HTTP handler end-to-end:

```bash
PYTHONPATH=. /usr/bin/python3 tests/test_dashboard.py
```

It verifies:
- Schema applies cleanly
- Insert helpers dedupe on `tx_hash`
- `/api/summary`, `/api/credentials`, `/api/identities`,
  `/api/watchlist`, `/api/agent_state`, `/api/health` all return 200
- Watchlist add/remove works

The full project test suite (`pytest tests/ --ignore=tests/test_integration_ledger_live.py`)
runs 45 offline tests, including the 2 dashboard tests.

## Limitations

- **In-memory agent_state computation**: `api_agent_state` reads all
  credential events involving a watched address on every request. Fine
  for hundreds of events; for production scale, add an index on
  `(subject, issuer)` and cache results.
- **No authentication**: the dashboard binds to `127.0.0.1` by default.
  If you change to `0.0.0.0`, anyone on your LAN can view agent
  activity. Add a reverse proxy + auth before exposing publicly.
- **Trust registry cache is unbounded**: this is a known issue carried
  over from the trust library (CHANGELOG §Known issues).
