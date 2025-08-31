
# app.py - Minimal Dynatrace Grail DQL proxy for Grafana (Infinity) 
# FastAPI + httpx, in-memory TTL cache, simple bearer auth (optional)
import os, time, hashlib, json, asyncio
from datetime import datetime, timezone
from typing import Optional, Dict, Any
import httpx
from fastapi import FastAPI, HTTPException, Query, Header, Request
from fastapi.responses import JSONResponse, PlainTextResponse

DT_URL = os.getenv("DT_URL", "").rstrip("/")
DT_TOKEN = os.getenv("DT_TOKEN", "")
# Optional org-level bearer token to protect the proxy (Authorization: Bearer <ORG_TOKEN>)
ORG_BEARER = os.getenv("ORG_BEARER", "")

# Basic sanity checks (won't block startup: allows docker healthcheck before env is set)
if not DT_URL:
    print("[WARN] DT_URL not set")
if not DT_TOKEN:
    print("[WARN] DT_TOKEN not set")

app = FastAPI(title="Dynatrace Grail DQL Proxy", version="0.1.0")

# --- Tiny TTL cache in memory ---
class TTLCache:
    def __init__(self, max_items: int = 256):
        self.max_items = max_items
        self.store: Dict[str, Any] = {}

    def _evict_if_needed(self):
        if len(self.store) <= self.max_items:
            return
        # evict oldest
        oldest_key = min(self.store, key=lambda k: self.store[k]["ts"])
        self.store.pop(oldest_key, None)

    def get(self, key: str, ttl: int):
        item = self.store.get(key)
        if not item:
            return None
        if time.time() - item["ts"] > ttl:
            self.store.pop(key, None)
            return None
        return item["value"]

    def set(self, key: str, value: Any):
        self.store[key] = {"value": value, "ts": time.time()}
        self._evict_if_needed()

cache = TTLCache(max_items=512)

def _require_auth(auth_header: Optional[str]):
    if ORG_BEARER:
        if not auth_header or not auth_header.startswith("Bearer "):
            raise HTTPException(401, "Missing or invalid Authorization header")
        token = auth_header.split(" ", 1)[1].strip()
        if token != ORG_BEARER:
            raise HTTPException(403, "Forbidden")

def _hash_key(*parts: str) -> str:
    s = "|".join(parts)
    return hashlib.sha1(s.encode("utf-8")).hexdigest()

async def grail_query(dql: str, _from: str, _to: str, timeout_sec: int = 30) -> dict:
    if not DT_URL or not DT_TOKEN:
        raise HTTPException(500, "DT_URL/DT_TOKEN not configured")
    payload = {"query": dql, "from": _from, "to": _to}
    headers = {"Authorization": f"Api-Token {DT_TOKEN}"}
    async with httpx.AsyncClient(timeout=timeout_sec) as c:
        r = await c.post(f"{DT_URL}/api/v2/query:execute", json=payload, headers=headers)
        if r.status_code >= 400:
            raise HTTPException(r.status_code, r.text)
        job = r.json().get("jobId")
        if not job:
            raise HTTPException(502, "Grail did not return a jobId")
        start = time.time()
        backoff = 0.25
        while True:
            p = await c.get(f"{DT_URL}/api/v2/query:poll", params={"jobId": job}, headers=headers)
            if p.status_code >= 400:
                raise HTTPException(p.status_code, p.text)
            j = p.json()
            status = j.get("status")
            if status == "SUCCEEDED":
                return j
            if status == "FAILED":
                # surface details to the caller
                raise HTTPException(502, json.dumps(j))
            if time.time() - start > timeout_sec:
                raise HTTPException(504, "Grail timeout")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 1.0)

def _to_epoch_ms(ts):
    # Accept ISO 8601 or epoch (s/ms)
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        # if seconds, convert to ms
        return int(ts * 1000) if ts < 10**12 else int(ts)
    if isinstance(ts, str):
        # remove Z -> +00:00 for fromisoformat
        iso = ts.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(iso)
        except ValueError:
            # fallback: try without timezone
            dt = datetime.fromisoformat(iso.split(".")[0])
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    return None

def to_grafana_table(j: dict) -> dict:
    # Expect j["result"]["records"] as list of dicts
    recs = j.get("result", {}).get("records", [])
    cols = []
    seen = set()
    # preserve a reasonable ordering: timestamp-ish first
    def sort_key(k: str):
        kl = k.lower()
        if "time" in kl or k == "timestamp":
            return (0, k)
        return (1, k)
    keys = sorted({k for r in recs for k in r.keys()}, key=sort_key)
    for k in keys:
        cols.append({"text": k})
    rows = []
    for r in recs:
        rows.append([r.get(k) for k in keys])
    return {"schema": "grafana-table", "columns": cols, "rows": rows}

def to_grafana_timeseries(j: dict, timecol: str, value: str, label: Optional[str]) -> dict:
    recs = j.get("result", {}).get("records", [])
    series: Dict[str, list] = {}
    for r in recs:
        key = str(r.get(label)) if label else "series"
        ts = _to_epoch_ms(r.get(timecol))
        val = r.get(value)
        if ts is None or val is None:
            # skip incomplete rows
            continue
        series.setdefault(key, []).append((ts, val))
    frames = []
    for k, pts in series.items():
        pts.sort(key=lambda x: x[0])
        frames.append({
            "name": k,
            "fields": [
                {"name": "Time", "type": "time", "values": [t for t, _ in pts]},
                {"name": "Value", "type": "number", "values": [v for _, v in pts]},
            ]
        })
    return {"schema": "grafana-timeseries", "frames": frames}

@app.get("/health")
async def health():
    return {"status": "ok", "dt_url_set": bool(DT_URL), "org_bearer_set": bool(ORG_BEARER)}

@app.post("/query")
async def raw_query(request: Request, authorization: Optional[str] = Header(default=None)):
    _require_auth(authorization)
    body = await request.json()
    dql = body.get("dql")
    _from = body.get("from", "now()-1h")
    _to = body.get("to", "now()")
    if not dql:
        raise HTTPException(400, "Missing 'dql'")
    cache_key = _hash_key("raw", dql, _from, _to)
    hit = cache.get(cache_key, ttl=int(os.getenv("RAW_TTL", "15")))
    if hit is not None:
        return JSONResponse(hit)
    j = await grail_query(dql, _from, _to, timeout_sec=int(os.getenv("QUERY_TIMEOUT", "30")))
    cache.set(cache_key, j)
    return JSONResponse(j)

@app.get("/table")
async def table(
    dql: str = Query(...),
    from_: str = Query(..., alias="from"),
    to_: str = Query(..., alias="to"),
    authorization: Optional[str] = Header(default=None),
    ttl: int = Query(30, description="Cache TTL seconds")
):
    _require_auth(authorization)
    cache_key = _hash_key("table", dql, from_, to_)
    hit = cache.get(cache_key, ttl=ttl)
    if hit is not None:
        return JSONResponse(hit)
    j = await grail_query(dql, from_, to_, timeout_sec=int(os.getenv("QUERY_TIMEOUT", "30")))
    out = to_grafana_table(j)
    cache.set(cache_key, out)
    return JSONResponse(out)

@app.get("/timeseries")
async def timeseries(
    dql: str = Query(...),
    from_: str = Query(..., alias="from"),
    to_: str = Query(..., alias="to"),
    value: str = Query(..., description="Numeric column name"),
    timecol: str = Query("timestamp", description="Time column name"),
    label: Optional[str] = Query(None, description="Series label column"),
    authorization: Optional[str] = Header(default=None),
    ttl: int = Query(30, description="Cache TTL seconds")
):
    _require_auth(authorization)
    cache_key = _hash_key("ts", dql, from_, to_, value, timecol, label or "")
    hit = cache.get(cache_key, ttl=ttl)
    if hit is not None:
        return JSONResponse(hit)
    j = await grail_query(dql, from_, to_, timeout_sec=int(os.getenv("QUERY_TIMEOUT", "30")))
    out = to_grafana_timeseries(j, timecol, value, label)
    cache.set(cache_key, out)
    return JSONResponse(out)

@app.get("/metrics")
async def metrics():
    # Simple text exposition for Prometheus scraping (extend as needed)
    # (Here just a placeholder so you can wire Prometheus quickly)
    content = textwrap.dedent(f"""
    # HELP dql_proxy_up 1 if proxy is running
    # TYPE dql_proxy_up gauge
    dql_proxy_up 1
    """).strip()+"\n"
    return PlainTextResponse(content)
