#!/usr/bin/env python3
import os, requests
from collections import defaultdict

# Variables à définir (ou via env vars)
GRAFANA_URL = os.getenv("GRAFANA_URL", "https://grafana.xxxx.com")
API_KEY     = os.getenv("GRAFANA_API_KEY", "xxxxx")

HEADERS = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

def get(path, params=None):
    url = f"{GRAFANA_URL}{path}"
    r = requests.get(url, headers=HEADERS, params=params, timeout=30, verify=True)
    r.raise_for_status()
    return r.json()

def main():
    # 1) Lister tous les dashboards
    dashboards = get("/api/search", params={"type":"dash-db"})
    print(f"Found {len(dashboards)} dashboards")

    ds_usage_dash = defaultdict(set)   # datasource → set(dashboard UID)
    ds_usage_panels = defaultdict(int) # datasource → count panels/targets

    for d in dashboards:
        uid = d.get("uid")
        title = d.get("title")
        dash = get(f"/api/dashboards/uid/{uid}")
        panels = dash["dashboard"].get("panels", [])
        # on gère aussi les panels imbriqués (rows)
        stack = panels[:]
        while stack:
            panel = stack.pop()
            if "panels" in panel:   # row panel
                stack.extend(panel["panels"])
            targets = panel.get("targets", [])
            for t in targets:
                ds = t.get("datasource") or panel.get("datasource") or "<default>"
                if isinstance(ds, dict):
                    ds = ds.get("name") or ds.get("uid") or "<dict>"
                ds_usage_dash[ds].add(uid)
                ds_usage_panels[ds] += 1

    print("\n=== Datasource usage ===")
    for ds, dashes in ds_usage_dash.items():
        print(f"- {ds:25s} → {len(dashes)} dashboards, {ds_usage_panels[ds]} panels/queries")

if __name__ == "__main__":
    main()
