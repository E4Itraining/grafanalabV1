from fastapi import FastAPI, HTTPException, Query
import httpx, os, time, hashlib, json
from datetime import datetime
import asyncio

DT_URL = os.environ["DT_URL"].rstrip("/")
DT_TOKEN = os.environ["DT_TOKEN"]

app = FastAPI()

async def grail_query(dql:str, _from:str, _to:str, timeout:int=30):
    payload = {"query": dql, "from": _from, "to": _to}
    headers = {"Authorization": f"Api-Token {DT_TOKEN}"}
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.post(f"{DT_URL}/api/v2/query:execute", json=payload, headers=headers)
        r.raise_for_status()
        job = r.json()["jobId"]
        start = time.time()
        backoff = 0.25
        while True:
            p = await c.get(f"{DT_URL}/api/v2/query:poll?jobId={job}", headers=headers)
            p.raise_for_status()
            j = p.json()
            if j["status"] == "SUCCEEDED":
                return j
            if j["status"] == "FAILED":
                raise HTTPException(502, detail=j)
            if time.time() - start > timeout:
                raise HTTPException(504, "Grail timeout")
            await asyncio.sleep(backoff)
            backoff = min(backoff*2, 1.0)

def to_grafana_table(j):
    # j["result"]["records"] -> list of dicts
    records = j.get("result", {}).get("records", [])
    cols = sorted({k for r in records for k in r.keys()})
    rows = [[r.get(c) for c in cols] for r in records]
    return {"schema":"grafana-table",
            "columns":[{"text":c} for c in cols],
            "rows":rows}

def to_grafana_timeseries(j, timecol, value, label=None, fill=None):
    records = j.get("result", {}).get("records", [])
    series = {}
    for r in records:
        key = str(r.get(label)) if label else "series"
        ts = r.get(timecol)
        # normaliser timestamp → epoch ms
        if isinstance(ts, str):  # ISO8601
            ts = int(datetime.fromisoformat(ts.replace("Z","+00:00")).timestamp()*1000)
        elif isinstance(ts, (int, float)) and ts < 10**12:  # sec→ms
            ts = int(ts*1000)
        val = r.get(value)
        series.setdefault(key, []).append((ts, val))
    frames = []
    for k, pts in series.items():
        pts.sort(key=lambda x: x[0])
        frames.append({"name":k,"fields":[
            {"name":"Time","type":"time","values":[t for t,_ in pts]},
            {"name":"Value","type":"number","values":[v for _,v in pts]}
        ]})
    return {"schema":"grafana-timeseries","frames":frames}

@app.get("/table")
async def table(dql:str=Query(...), _from:str=Query(..., alias="from"), _to:str=Query(..., alias="to")):
    j = await grail_query(dql, _from, _to)
    return to_grafana_table(j)

@app.get("/timeseries")
async def timeseries(dql:str=Query(...), _from:str=Query(..., alias="from"), _to:str=Query(..., alias="to"),
                     timecol:str="timestamp", value:str=Query(...), label:str|None=None, fill:str|None=None):
    j = await grail_query(dql, _from, _to)
    return to_grafana_timeseries(j, timecol, value, label, fill)
