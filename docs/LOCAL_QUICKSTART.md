# AQROS — Local Quickstart

The platform is a monorepo of 17 microservices. This is the shortest path from a
clean clone to a running stack you can poke.

## Prerequisites

- Docker Desktop (or Docker Engine + Compose v2)
- `uv` — https://docs.astral.sh/uv/getting-started/installation/
- Python 3.12+ (only needed for `make check` / tests; the stack runs in Docker)

## First run

```bash
make install     # create .venv and install the workspace (for tests + tooling)
make up          # build images, start databases, migrate, then start services
make status      # per-service health, with a hint when something needs fixing
```

`make up` is deliberately ordered **databases → migrate → services**. Several
services seed rows during startup and cannot boot against an empty schema, so
starting everything at once would crash-loop them. If you already have the
images, `make up` is quick on subsequent runs.

When it's healthy you should see roughly:

```
Gateway aggregate: degraded  (15/16 services healthy)
```

`parity-monitor` is expected to be down: it belongs to the `live` profile and is
only meaningful once real orders exist.

## Things to try

```bash
# What exists, and which services the public edge will proxy to
curl localhost:8000/v1/topology | python3 -m json.tool

# Health of every service, aggregated
curl localhost:8000/v1/health/platform | python3 -m json.tool

# Prometheus metrics (every service exposes these)
curl localhost:8000/metrics | head

# Ingest real market data (uses yfinance; no API key needed)
curl -X POST localhost:8000/v1/market-data/v1/ingestion \
  -H 'Content-Type: application/json' \
  -d '{"symbol":"AAPL","start":"2024-01-02","end":"2024-01-31","interval":"1d"}'

# Read it back — note event_time and knowledge_time (bitemporal)
curl "localhost:8000/v1/market-data/v1/instruments/AAPL/bars?start=2024-01-02&end=2024-01-10"

# Compute features from those bars
curl -X POST localhost:8000/v1/feature-store/v1/pipeline/compute \
  -H 'Content-Type: application/json' \
  -d '{"symbol":"AAPL","start":"2024-01-02","end":"2024-01-31","feature_names":["sma_20","rsi_14"]}'
```

## Proving the safety properties work

These are the interesting ones. Each should behave exactly as shown.

```bash
# The money path is NOT reachable through the public gateway (404, not 403 —
# a 403 would confirm the service exists and allow enumeration)
for s in risk-engine oms portfolio live-trading-engine audit-ledger; do
  printf '%-22s ' "$s"; curl -s -o /dev/null -w '%{http_code}\n' "localhost:8000/v1/$s/v1/anything"
done
# every line should print 404
```

### Four-eyes approval (auth on :8001)

Dev bootstrap creates `admin` and `committee` users. A request can never be
approved by the person who made it, and `admin` cannot approve at all.

```bash
# Two distinct committee members, because one person is not four-eyes
TOK=$(curl -s -X POST localhost:8001/v1/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"committee","password":"committee-dev-password-1234"}' | jq -r .access_token)

curl -s -X POST localhost:8001/v1/users -H "Authorization: Bearer $ADMIN" \
  -H 'Content-Type: application/json' \
  -d '{"username":"committee2","display_name":"C2","password":"committee2-dev-password-1234","roles":["committee"]}'

RID=$(curl -s -X POST localhost:8001/v1/approvals -H "Authorization: Bearer $TOK" \
  -H 'Content-Type: application/json' \
  -d '{"action":"promote_model","resource":"momentum_v3"}' | jq -r .request_id)

# Self-approval -> 403 separation of duties
curl -s -X POST "localhost:8001/v1/approvals/$RID/approve" \
  -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' -d '{}'

# A distinct committee member -> 200 APPROVED
curl -s -X POST "localhost:8001/v1/approvals/$RID/approve" \
  -H "Authorization: Bearer $TOK2" -H 'Content-Type: application/json' -d '{"reason":"reviewed"}'
```

### Audit ledger tamper-evidence (audit-ledger on :8007, loopback only)

The ledger is append-only: there is no update or delete endpoint, and a database
trigger rejects mutation outright.

```bash
curl -s -X POST localhost:8007/v1/audit/events -H 'Content-Type: application/json' \
  -d '{"event_type":"test","actor_id":"alice","action":"verify","resource":"chain","outcome":"ALLOWED"}' | jq

curl -s localhost:8007/v1/audit/verify | jq
# status VERIFIED, entries_checked increasing

curl -s -X DELETE localhost:8007/v1/audit/events/whatever   # 405/404 — no such thing
```

## Everyday commands

| Command | What it does |
|---|---|
| `make up` | Build, start databases, migrate, start services |
| `make status` | Per-service health + what to do about failures |
| `make down` | Stop the stack (add `-v` to delete volumes) |
| `make check` | Full quality gate: ruff, black, mypy --strict, pytest |
| `make migrate` | Apply migrations to running services |
| `make migrate-down` | **Destructive:** roll every database back to base |

## Profiles: the trust ladder

Services are grouped so you only run what you are working on.

| Profile | Services |
|---|---|
| *(default)* | gateway, auth, market-data, feature-store, dataset-builder, training-pipeline, model-registry, inference-service, strategy-engine |
| `backtest` | backtesting-engine |
| `audit` | audit-ledger |
| `paper` | paper-trading-engine |
| `live` | parity-monitor |
| `trading` | risk-engine, portfolio, oms, live-trading-engine (+ `paper`) |
| `objectstore` | MinIO (off by default — see below) |

```bash
docker compose --profile trading --profile backtest --profile audit up -d
```

## Troubleshooting

**`make up` fails with "port already allocated".** Something else on your
machine holds one of the service ports (`8000`–`8021`). Find it with
`lsof -nP -iTCP:<port> -sTCP:LISTEN`. Database ports are *not* published, so
these are application ports only.

**A service reports 503 with a failing `schema` check.** Migrations have not
been applied. Run `make migrate`.

**`dependency <x> failed to start`.** The dependency is not healthy yet —
`docker compose logs <x>` will say why. Usually it needs `make migrate` first.

**A service crash-loops with `No module named aqros_x.main`.** The image predates
the entrypoint fix. `docker compose build --no-cache <service>`.

**Reset everything.** `docker compose down -v` then `make up`.

## Known gaps

Honest list of what is *not* finished:

- **MinIO is off by default.** `minio/minio` was removed from Docker Hub and
  quay.io requires auth in this environment, so the image reference could not be
  verified. Artifacts persist to named Docker volumes, which has no redundancy.
  Enable with `--profile objectstore` after setting a resolvable `MINIO_IMAGE`.
- **CI has not been exercised on these commits.** Locally: 1386 tests, ruff,
  black, and mypy --strict all clean. The supply-chain and migration jobs in CI
  have not run yet.
- **Auth and audit-ledger Postgres adapters** now have round-trip integration
  tests, but `paper-trading-engine` still has no schema (no migrations, and its
  DB is unused today).
- **Live/paper still define their own order dataclasses** beyond the shared
  `OrderSide`/`OrderType` enums, so the rest of Hard Rule §7.1 (one codebase for
  backtest/paper/live) is not yet fully satisfied.
