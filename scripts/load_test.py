#!/usr/bin/env python3
"""Lightweight concurrency smoke test against a running API.

Usage:
  python scripts/load_test.py --base http://localhost --n 20 --url https://www.youtube.com/watch?v=jNQXAC9IVRw
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
import time
import urllib.error
import urllib.request


def post_job(base: str, url: str) -> tuple[int, str]:
    payload = json.dumps({"url": url, "captcha_token": None}).encode("utf-8")
    req = urllib.request.Request(
        f"{base.rstrip('/')}/api/jobs",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, body
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")
    except Exception as exc:
        return 0, str(exc)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://localhost")
    parser.add_argument("--n", type=int, default=20)
    parser.add_argument(
        "--url",
        default="https://www.youtube.com/watch?v=jNQXAC9IVRw",
        help="Short public video URL for smoke testing",
    )
    args = parser.parse_args()

    # Health first
    with urllib.request.urlopen(f"{args.base.rstrip('/')}/api/health", timeout=10) as resp:
        health = json.loads(resp.read().decode("utf-8"))
        print("health:", health)

    started = time.time()
    ok = 0
    limited = 0
    failed = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(args.n, 32)) as pool:
        futures = [pool.submit(post_job, args.base, args.url) for _ in range(args.n)]
        for fut in concurrent.futures.as_completed(futures):
            status, body = fut.result()
            if status in (200, 201):
                ok += 1
            elif status in (429, 503):
                limited += 1
            else:
                failed += 1
                print(f"fail {status}: {body[:200]}")

    elapsed = round(time.time() - started, 2)
    print(f"done in {elapsed}s — ok={ok} rate_limited={limited} failed={failed}")
    # Expect many 429s under default per-IP caps; that still validates control planes.
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
