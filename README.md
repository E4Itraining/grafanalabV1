
# Dynatrace Grail DQL Proxy (Minimal)

A minimal FastAPI proxy that executes Dynatrace **Grail DQL** queries via the async API (`query:execute`/`query:poll`) and returns Grafana‑friendly **timeseries** or **table** JSON. Includes a tiny in‑memory TTL cache.

## Why
- The official Grafana ↔ Dynatrace plugin does not run DQL/Grail.
- This proxy exposes a simple, stable interface for Grafana (Infinity plugin or any HTTP panel).

## Endpoints
- `GET /health` → quick status
- `POST /query` (body: `{ "dql": "...", "from": "now()-1h", "to": "now()" }`) → raw Grail response (debug)
- `GET /table?dql=...&from=...&to=...` → `{schema:"grafana-table",columns,rows}`
- `GET /timeseries?dql=...&from=...&to=...&value=count&timecol=timestamp&label=host` → `{schema:"grafana-timeseries",frames:[...]}`
- `GET /metrics` → basic Prometheus metric (placeholder)

> **Auth**: if `ORG_BEARER` is set, requests must include `Authorization: Bearer <ORG_BEARER>`.

## Quick start

```bash
docker compose up --build -d
# or:
# docker build -t dql-proxy:mini .
# docker run -e DT_URL=... -e DT_TOKEN=... -p 8080:8080 dql-proxy:mini
```

### Health
```
curl http://localhost:8080/health
```

### Timeseries example
```bash
DQL='fetch logs
| filter loglevel == "ERROR"
| summarize count(), by: [bin(timestamp, 1m), dt.entity.host]
| sort timestamp asc'

curl -G "http://localhost:8080/timeseries" \
  --data-urlencode "dql=$DQL" \
  --data-urlencode "from=now()-2h" \
  --data-urlencode "to=now()" \
  --data-urlencode "value=count" \
  --data-urlencode "timecol=timestamp" \
  --data-urlencode "label=dt.entity.host" \
  -H "Authorization: Bearer ${ORG_BEARER:-CHANGE_ME}"
```

### Table example
```bash
DQL='fetch logs
| filter service.name == "api-gateway"
| summarize p95=percentile(duration_ms,95), by: endpoint
| sort p95 desc
| limit 50'

curl -G "http://localhost:8080/table" \
  --data-urlencode "dql=$DQL" \
  --data-urlencode "from=now()-24h" \
  --data-urlencode "to=now()" \
  -H "Authorization: Bearer ${ORG_BEARER:-CHANGE_ME}"
```

## Grafana (Infinity) setup

1. Create a **Infinity** data source (Type: JSON/URL).
2. For a **time series** panel:
   - URL: `http://<proxy>/timeseries?dql=<urlenc>&from=${__from:date}&to=${__to:date}&value=count&timecol=timestamp&label=dt.entity.host`
   - Response format: JSON  
   - Root: `$.frames[*]`  
   - Name: `$.name`  
   - Time values: `$.fields[?(@.type=="time")].values`  
   - Number values: `$.fields[?(@.type=="number")].values`
3. For a **table** panel:
   - URL: `http://<proxy>/table?...`
   - Root: `$.rows[*]` and use `columns[*].text` as headers.

> Tip: Add `${__from:date}` / `${__to:date}` to bind Grafana time picker.

## Env vars

- `DT_URL` — e.g. `https://<tenant>.live.dynatrace.com/e/<env>`
- `DT_TOKEN` — Dynatrace API token (must allow Grail query APIs)
- `ORG_BEARER` — optional bearer for incoming requests to protect the proxy
- `QUERY_TIMEOUT` — default 30s
- `RAW_TTL` — TTL for `/query` cache (seconds)

## Notes
- This is a **minimal** implementation: for production, add mTLS, Redis cache, rate limiting, structured logs, tracing, etc.
- The proxy avoids hitting Dynatrace multiple times for identical panels via a TTL cache key:
  `sha1(dql|from|to|value|timecol|label)`.

## License
MIT
