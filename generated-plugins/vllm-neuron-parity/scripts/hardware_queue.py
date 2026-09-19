#!/usr/bin/env python3
"""hardware_queue.py - the single writer of vllm-neuron-parity lease records.

A lease reserves named non-shareable pools on a host from the roster's declared
capacity, never the whole host. Two grains, one noun: a CAMPAIGN lease reserves
nothing and binds a campaign to a host's verified identity; a JOB lease names the
pools and amounts one job takes and is granted only when they fit the remaining
capacity (roster pools minus every open job lease, derived from the records,
never stored). A job that takes no pool holds no job lease, and
this tool refuses to record one. A campaign that already holds an open campaign
lease on a host is refused a second one, with the standing lease's reference,
so one campaign never holds two. Records are one JSON file per event under
<root>/campaigns/<campaign>/attempts/leases/, keyed by host in name and body so
every campaign leasing a host can read them. The grant runs under a per-host OS
lock (<root>/run/hardware-queue/<host>.lock, fcntl.flock - released with the
process) so two jobs never both get the last unit. --job-record names the file
the job writes only when it ends; a grant whose job record has closed (exists,
parses as JSON, carries an "outcome" or "closed_at" key) without a release is
released by the tool on its next invocation (reason "reaped"). --roster is the
run state file (its instance_roster list, frozen by the lead at intake) or a
JSON {"hosts": {<host>: {"pools": {...}}}}. The lead writes no lease record.

A known pool class (neuron_devices, compile_memory_gib, cpu_cores,
compile_cache_write_slot) the roster does not size is indivisible: a job that
declares a need for it reserves the host whole, so a roster that sizes no pools
behaves as one job per host. An unknown pool name is a request defect.

Exit codes: 0 done; 2 request defect (unknown host or pool, oversize, empty job
reservation, a second campaign lease, nothing to release); 3 wait (capacity
exists but is busy).

  grant   --root R --roster ROSTER --host H --campaign C
          [--job J --pool NAME=AMOUNT ... [--job-record PATH] [--wait SECONDS]]
          [--markers-verified a,b] [--markers-unavailable c] [--deltas-explained TEXT]
  release --root R --host H --campaign C [--job J | --lease-id ID] [--all]
  amend   --root R --host H --boot-identifier B
  status  --root R --roster ROSTER [--host H]
"""
from __future__ import annotations

import argparse
import fcntl
import glob
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")


def load_roster(path: str) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict) and "instance_roster" in data:  # run-state.json
        data = data["instance_roster"]
    if isinstance(data, list):
        return {h["instance"]: h for h in data if isinstance(h, dict) and h.get("instance")}
    return data.get("hosts", data)


def lease_dir(root: Path, campaign: str) -> Path:
    return root / "campaigns" / campaign / "attempts" / "leases"


def write_event(root: Path, campaign: str, event: dict) -> Path:
    d = lease_dir(root, campaign)
    d.mkdir(parents=True, exist_ok=True)
    name = f"{event['at']}-{event['host']}-{event['event']}-{event.get('kind', 'x')}-{event.get('lease_id', '')[:8]}.json"
    p = d / name
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(event, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, p)
    return p


def read_events(root: Path, host: str) -> list[dict]:
    out = []
    for f in sorted(glob.glob(str(root / "campaigns" / "*" / "attempts" / "leases" / "*.json"))):
        try:
            e = json.loads(Path(f).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if e.get("host") == host:
            out.append(e)
    return out


def open_leases(events: list[dict]) -> list[dict]:
    released = {e["lease_id"] for e in events if e.get("event") == "release"}
    return [e for e in events if e.get("event") == "grant" and e["lease_id"] not in released]


def job_record_closed(path: str | None) -> bool:
    """True only when the job's record exists, parses as JSON, and carries a terminal key."""
    if not path or not Path(path).exists():
        return False
    try:
        rec = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(rec, dict) and ("outcome" in rec or "closed_at" in rec)


def reap(root: Path, host: str, events: list[dict]) -> list[dict]:
    """Release every open job lease whose job record has closed on disk."""
    reaped = []
    for lease in open_leases(events):
        if lease.get("kind") != "job":
            continue
        if job_record_closed(lease.get("job_record")):
            ev = {"event": "release", "lease_id": lease["lease_id"], "host": host,
                  "campaign": lease["campaign"], "job": lease.get("job"), "kind": "job",
                  "at": now(), "reason": "reaped: job record closed without a release"}
            write_event(root, lease["campaign"], ev)
            events.append(ev)
            reaped.append(ev)
    return reaped


def remaining(pools: dict, open_jobs: list[dict]) -> dict:
    rem = {}
    whole = any(l.get("whole_host") for l in open_jobs)
    for name, size in pools.items():
        if size is None:
            rem[name] = 0 if open_jobs else 1
        else:
            used = sum(int(l.get("pools", {}).get(name, 0)) for l in open_jobs)
            rem[name] = 0 if whole else max(0, int(size) - used)
    return rem


class Lock:
    def __init__(self, root: Path, host: str):
        d = root / "run" / "hardware-queue"
        d.mkdir(parents=True, exist_ok=True)
        self.path = d / f"{host}.lock"

    def __enter__(self):
        self.fh = open(self.path, "a+")
        fcntl.flock(self.fh, fcntl.LOCK_EX)
        return self

    def __exit__(self, *a):
        fcntl.flock(self.fh, fcntl.LOCK_UN)
        self.fh.close()


def emit(obj: dict, code: int) -> int:
    print(json.dumps(obj, sort_keys=True))
    return code


def parse_pools(items: list[str]) -> dict:
    pools = {}
    for it in items or []:
        name, _, amount = it.partition("=")
        if not name or not amount.isdigit():
            raise ValueError(f"pool must be NAME=AMOUNT, got {it!r}")
        pools[name] = int(amount)
    return pools


KNOWN_POOLS = ("neuron_devices", "compile_memory_gib", "cpu_cores", "compile_cache_write_slot")


def try_grant_job(root: Path, roster: dict, args, pools: dict) -> tuple[int, dict]:
    host_pools = dict(roster[args.host].get("pools", {}) or {})
    for name in pools:
        if name not in host_pools:
            if name in KNOWN_POOLS:
                host_pools[name] = None  # the roster leaves it unsized: indivisible
            else:
                return 2, {"status": "defect", "reason": f"pool {name!r} is not a known pool class and the roster does not size it for {args.host}"}
    for name, amount in pools.items():
        size = host_pools[name]
        if size is not None and amount > int(size):
            return 2, {"status": "defect", "reason": f"oversize: {name}={amount} exceeds the roster size {size}"}
        if size is None and amount != 1:
            return 2, {"status": "defect", "reason": f"pool {name!r} is unsized (indivisible); request it as {name}=1"}
    whole = any(host_pools[n] is None for n in pools)
    with Lock(root, args.host):
        events = read_events(root, args.host)
        reap(root, args.host, events)
        jobs = [l for l in open_leases(events) if l.get("kind") == "job"]
        rem = remaining(host_pools, jobs)
        if whole and jobs:
            return 3, {"status": "wait", "reason": "indivisible pool requested while job leases are open", "remaining": rem}
        short = {n: (a, rem[n]) for n, a in pools.items() if a > rem[n]}
        if short:
            return 3, {"status": "wait", "reason": "capacity busy", "short": short, "remaining": rem}
        lease_id = uuid.uuid4().hex
        ev = {"event": "grant", "kind": "job", "lease_id": lease_id, "host": args.host,
              "campaign": args.campaign, "job": args.job, "pools": pools, "whole_host": whole,
              "job_record": args.job_record, "pid": os.getpid(), "at": now()}
        p = write_event(root, args.campaign, ev)
        jobs.append(ev)
        return 0, {"status": "granted", "kind": "job", "lease_id": lease_id, "record": str(p),
                   "remaining": remaining(host_pools, jobs)}


def cmd_grant(args) -> int:
    root = Path(args.root)
    roster = load_roster(args.roster)
    if args.host not in roster:
        return emit({"status": "defect", "reason": f"host {args.host!r} is not on the roster"}, 2)
    if args.job is None:
        with Lock(root, args.host):
            events = read_events(root, args.host)
            reap(root, args.host, events)
            standing = [l for l in open_leases(events)
                        if l.get("kind") == "campaign" and l.get("campaign") == args.campaign]
            if standing:
                return emit({"status": "defect", "kind": "campaign", "lease_id": standing[0]["lease_id"],
                             "reason": "this campaign already holds a campaign lease on this host; "
                                       "one campaign never holds two - release it or reuse it"}, 2)
            lease_id = uuid.uuid4().hex
            ev = {"event": "grant", "kind": "campaign", "lease_id": lease_id, "host": args.host,
                  "campaign": args.campaign, "grant_reference": lease_id, "at": now(),
                  "markers_verified": [m for m in (args.markers_verified or "").split(",") if m],
                  "markers_unavailable": [m for m in (args.markers_unavailable or "").split(",") if m],
                  "deltas_explained": args.deltas_explained or ""}
            p = write_event(root, args.campaign, ev)
        return emit({"status": "granted", "kind": "campaign", "lease_id": lease_id, "record": str(p)}, 0)
    try:
        pools = parse_pools(args.pool)
    except ValueError as exc:
        return emit({"status": "defect", "reason": str(exc)}, 2)
    if not pools:
        return emit({"status": "defect", "reason": "a job that takes no pool holds no job lease; run it under the campaign lease"}, 2)
    deadline = time.monotonic() + (args.wait or 0)
    while True:
        code, obj = try_grant_job(root, roster, args, pools)
        if code != 3 or time.monotonic() >= deadline:
            return emit(obj, code)
        time.sleep(min(5.0, max(0.2, deadline - time.monotonic())))


def cmd_release(args) -> int:
    root = Path(args.root)
    with Lock(root, args.host):
        events = read_events(root, args.host)
        reap(root, args.host, events)
        mine = [l for l in open_leases(events) if l["campaign"] == args.campaign]
        if args.lease_id:
            targets = [l for l in mine if l["lease_id"] == args.lease_id]
        elif args.all:
            targets = mine
        elif args.job is not None:
            targets = [l for l in mine if l.get("kind") == "job" and l.get("job") == args.job]
        else:
            targets = [l for l in mine if l.get("kind") == "campaign"]
        if not targets:
            return emit({"status": "defect", "reason": "no open lease matches the request"}, 2)
        written = []
        for l in targets:
            ev = {"event": "release", "lease_id": l["lease_id"], "host": args.host, "campaign": args.campaign,
                  "job": l.get("job"), "kind": l.get("kind"), "at": now(), "reason": "released"}
            written.append(str(write_event(root, args.campaign, ev)))
    return emit({"status": "released", "count": len(written), "records": written}, 0)


def cmd_amend(args) -> int:
    root = Path(args.root)
    with Lock(root, args.host):
        events = read_events(root, args.host)
        camps = [l for l in open_leases(events) if l.get("kind") == "campaign"]
        if not camps:
            return emit({"status": "defect", "reason": "no open campaign lease names this host"}, 2)
        ev = {"event": "amend", "kind": "campaign", "lease_id": camps[0]["lease_id"], "host": args.host,
              "campaign": camps[0]["campaign"], "boot_identifier": args.boot_identifier, "at": now(),
              "applies_to": [{"campaign": l["campaign"], "lease_id": l["lease_id"]} for l in camps]}
        p = write_event(root, camps[0]["campaign"], ev)
    return emit({"status": "amended", "applies_to": ev["applies_to"], "record": str(p)}, 0)


def cmd_status(args) -> int:
    root = Path(args.root)
    roster = load_roster(args.roster)
    hosts = [args.host] if args.host else sorted(roster)
    out = {}
    for h in hosts:
        if h not in roster:
            return emit({"status": "defect", "reason": f"host {h!r} is not on the roster"}, 2)
        with Lock(root, h):
            events = read_events(root, h)
            reaped = reap(root, h, events)
            ol = open_leases(events)
        jobs = [l for l in ol if l.get("kind") == "job"]
        pools = dict(roster[h].get("pools", {}) or {})
        for l in jobs:
            for name in l.get("pools", {}):
                pools.setdefault(name, None)
        out[h] = {"pools": pools, "remaining": remaining(pools, jobs),
                  "open_job_leases": [{"campaign": l["campaign"], "job": l.get("job"), "pools": l.get("pools"), "lease_id": l["lease_id"]} for l in jobs],
                  "open_campaign_leases": [{"campaign": l["campaign"], "lease_id": l["lease_id"]} for l in ol if l.get("kind") == "campaign"],
                  "reaped_now": len(reaped)}
    return emit({"status": "ok", "hosts": out}, 0)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("grant"); g.set_defaults(fn=cmd_grant)
    g.add_argument("--root", required=True); g.add_argument("--roster", required=True)
    g.add_argument("--host", required=True); g.add_argument("--campaign", required=True)
    g.add_argument("--job"); g.add_argument("--pool", action="append", default=[])
    g.add_argument("--job-record"); g.add_argument("--wait", type=float, default=0.0)
    g.add_argument("--markers-verified"); g.add_argument("--markers-unavailable"); g.add_argument("--deltas-explained")
    r = sub.add_parser("release"); r.set_defaults(fn=cmd_release)
    r.add_argument("--root", required=True); r.add_argument("--host", required=True); r.add_argument("--campaign", required=True)
    r.add_argument("--job"); r.add_argument("--lease-id"); r.add_argument("--all", action="store_true")
    a = sub.add_parser("amend"); a.set_defaults(fn=cmd_amend)
    a.add_argument("--root", required=True); a.add_argument("--host", required=True); a.add_argument("--boot-identifier", required=True)
    s = sub.add_parser("status"); s.set_defaults(fn=cmd_status)
    s.add_argument("--root", required=True); s.add_argument("--roster", required=True); s.add_argument("--host")
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
