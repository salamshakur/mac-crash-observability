#!/usr/bin/env python3
"""
push_to_loki.py: send events from data/index.ndjson to Loki's HTTP push API.

  python3 scripts/push_to_loki.py             push events not sent before
  python3 scripts/push_to_loki.py --all       push everything again (use on a fresh/empty Loki;
                                              on an existing one it can create duplicates)
  python3 scripts/push_to_loki.py --dry-run   print the request instead of sending it

Config comes from .env: LOKI_URL (required), LOKI_USERNAME / LOKI_PASSWORD (optional).
Already-sent event IDs are remembered in data/.loki_pushed (gitignored).

Why push instead of running an agent? This Mac crashes; a tiny one-shot script that runs
after each reboot is simpler than keeping a collector alive on it. See observability/README.md
for the Grafana Alloy alternative.
"""
import base64
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(ROOT, "data", "index.ndjson")
STATE = os.path.join(ROOT, "data", ".loki_pushed")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from build_index import load_env  # noqa: E402

BATCH = 500
# Labels must stay low-cardinality (few distinct values): every unique label set is a separate
# stream in Loki. Everything else stays inside the JSON line and is parsed at query time with `| json`.
LABEL_KEYS = ("category", "source")


def to_ns(iso):
    t = dt.datetime.strptime(iso.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S.%f%z")
    return str(int(t.timestamp() * 1_000_000) * 1000)


def main():
    env = load_env()
    url = (env.get("LOKI_URL") or "").rstrip("/")
    dry = "--dry-run" in sys.argv
    if not url and not dry:
        sys.exit("LOKI_URL is empty in .env")
    if not os.path.exists(INDEX):
        sys.exit("No data/index.ndjson. Run scripts/update.sh first.")

    sent = set()
    if os.path.exists(STATE) and "--all" not in sys.argv:
        sent = set(open(STATE).read().split())

    events = [json.loads(l) for l in open(INDEX) if l.strip()]
    todo = [e for e in events if e.get("ts") and e["event_id"] not in sent]
    if not todo:
        print("Nothing new to push.")
        return

    # group into streams (one per label set); Loki wants each stream's entries in time order
    streams = {}
    for e in sorted(todo, key=lambda e: e["ts"]):
        labels = {"job": "mac-crash", "host": e.get("host", "mac")}
        labels.update({k: str(e[k]) for k in LABEL_KEYS if e.get(k)})
        streams.setdefault(tuple(sorted(labels.items())), []).append(e)

    headers = {"Content-Type": "application/json"}
    if env.get("LOKI_USERNAME"):
        tok = base64.b64encode(("%s:%s" % (env["LOKI_USERNAME"], env.get("LOKI_PASSWORD", ""))).encode()).decode()
        headers["Authorization"] = "Basic " + tok

    pushed = 0
    for key, evs in streams.items():
        for i in range(0, len(evs), BATCH):
            chunk = evs[i:i + BATCH]
            body = {"streams": [{"stream": dict(key),
                                 "values": [[to_ns(e["ts"]), json.dumps(e, sort_keys=True)] for e in chunk]}]}
            if dry:
                print(json.dumps(body)[:1500])
                continue
            req = urllib.request.Request(url + "/loki/api/v1/push", data=json.dumps(body).encode(), headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    ok = r.status in (200, 204)
            except urllib.error.HTTPError as err:
                print("Loki rejected %s: HTTP %s %s" % (dict(key), err.code, err.read().decode()[:300]), file=sys.stderr)
                ok = False
            except urllib.error.URLError as err:
                sys.exit("Can't reach Loki at %s (%s). Is the stack running?" % (url, err.reason))
            if ok:
                pushed += len(chunk)
                with open(STATE, "a") as f:
                    f.write("".join(e["event_id"] + "\n" for e in chunk))
    print("%s %d events in %d streams to %s" % ("Would push" if dry else "Pushed", len(todo) if dry else pushed,
                                               len(streams), url or "(dry run)"))


if __name__ == "__main__":
    main()
