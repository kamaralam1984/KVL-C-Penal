"""Small, honest load probe for the API (not a capacity benchmark). Usage: loadtest.py [base_url] [requests] [concurrency]
Logs in once with the dev owner account (backend/.dev-credentials) and measures latency percentiles."""
import asyncio
import re
import statistics
import sys
import time
from pathlib import Path

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
N = int(sys.argv[2]) if len(sys.argv) > 2 else 300
C = int(sys.argv[3]) if len(sys.argv) > 3 else 20
creds = (Path(__file__).resolve().parents[1] / "backend/.dev-credentials").read_text()
password = re.search(r"password:\s*(\S+)", creds).group(1)
email = re.search(r"super_admin\s+(\S+)", creds).group(1)


async def hammer(client: httpx.AsyncClient, path: str) -> dict:
    lat, errors = [], 0
    sem = asyncio.Semaphore(C)

    async def one():
        nonlocal errors
        async with sem:
            t = time.perf_counter()
            try:
                r = await client.get(path)
                if r.status_code != 200:
                    errors += 1
            except httpx.HTTPError:
                errors += 1
            lat.append((time.perf_counter() - t) * 1000)

    t0 = time.perf_counter()
    await asyncio.gather(*(one() for _ in range(N)))
    wall = time.perf_counter() - t0
    lat.sort()
    return {"path": path, "n": N, "conc": C, "rps": round(N / wall, 1), "p50_ms": round(statistics.median(lat), 1), "p95_ms": round(lat[int(len(lat) * 0.95) - 1], 1), "max_ms": round(lat[-1], 1), "errors": errors}


async def main():
    async with httpx.AsyncClient(base_url=BASE, timeout=30) as client:
        r = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
        r.raise_for_status()
        for path in ("/healthz", "/api/v1/auth/me", "/api/v1/stats/dashboard", "/api/v1/tenants?limit=25"):
            print(await hammer(client, path))


asyncio.run(main())
