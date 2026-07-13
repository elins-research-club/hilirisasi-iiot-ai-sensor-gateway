#!/usr/bin/env python3
"""Enqueue a remote laptop job into jobs/inbox for the Windows agent."""
from __future__ import annotations
import argparse, json, time, uuid
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--command", required=True, help="cmd.exe command to run on laptop")
    p.add_argument("--job-id", default="")
    p.add_argument("--workdir", default="")
    args = p.parse_args()
    job_id = args.job_id or time.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    inbox = ROOT / "jobs" / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "iiot.remote_job.v1",
        "job_id": job_id,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "command": args.command,
        "workdir": args.workdir or str(ROOT),
    }
    path = inbox / f"{job_id}.job.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    print(path)
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
