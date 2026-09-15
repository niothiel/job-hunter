#!/usr/bin/env python3
"""Replay OTLP span files to Phoenix.

Reads ``spans-*.otlp`` files from the fallback directory and POSTs them to
the Phoenix OTLP HTTP endpoint. Successfully sent files are deleted.

Usage::

    python -m pipeline.helpers.replay_spans [--dir <path>] [--host <url>]

Defaults:
    --dir  : logs/exports (relative to CWD)
    --host : $PHOENIX_HOST or http://localhost:6006

Exits 0 if all files were sent (or none existed). Exits 1 if any file failed
to send — failed files are left on disk for the next run.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import requests


def replay(fallback_dir: Path, phoenix_host: str) -> int:
    endpoint = phoenix_host.rstrip("/")
    if not endpoint.endswith("/v1/traces"):
        endpoint = f"{endpoint}/v1/traces"

    span_files = sorted(fallback_dir.glob("spans-*.otlp"))
    if not span_files:
        print(f"No .otlp files found in {fallback_dir}")
        return 0

    print(f"Replaying {len(span_files)} span file(s) to {endpoint}")
    failures = 0
    for f in span_files:
        data = f.read_bytes()
        try:
            resp = requests.post(
                endpoint,
                data=data,
                headers={"Content-Type": "application/x-protobuf"},
                timeout=30,
            )
            if resp.ok:
                f.unlink()
                print(f"  {f.name} -> OK ({len(data)} bytes)")
            else:
                print(f"  {f.name} -> FAIL (HTTP {resp.status_code}: {resp.reason})")
                failures += 1
        except requests.RequestException as exc:
            print(f"  {f.name} -> FAIL ({exc})")
            failures += 1

    if failures:
        print(f"\n{failures} file(s) failed — left on disk for retry.")
        return 1
    print(f"\nAll {len(span_files)} file(s) sent successfully.")
    return 0


def main() -> None:
    import os

    parser = argparse.ArgumentParser(description="Replay OTLP span files to Phoenix.")
    parser.add_argument(
        "--dir", type=Path, default=Path.cwd() / "logs" / "exports",
        help="Directory containing spans-*.otlp files (default: logs/exports)",
    )
    parser.add_argument(
        "--host", type=str, default=os.environ.get("PHOENIX_HOST", "http://localhost:6006"),
        help="Phoenix server URL (default: $PHOENIX_HOST or http://localhost:6006)",
    )
    args = parser.parse_args()

    if not args.dir.exists():
        print(f"Directory does not exist: {args.dir}")
        return

    sys.exit(replay(args.dir, args.host))


if __name__ == "__main__":
    main()
