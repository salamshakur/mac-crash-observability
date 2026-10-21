#!/usr/bin/env python3
"""
build_index.py: turn macOS diagnostic reports into one normalized event per line (NDJSON).

Reads (all paths relative to the repo root):
  data/raw/system/   copies of /Library/Logs/DiagnosticReports
  data/raw/user/     copies of ~/Library/Logs/DiagnosticReports
  data/raw/bridge/   copies of /Library/Logs/DiagnosticReports/ProxiedDevice-Bridge (T2 chip)
  data/system-info/  output of scripts/collect_system_info.sh (kernel log, pmset log)

Writes:
  data/index.ndjson  one JSON event per line, identifiers scrubbed (safe to publish)
  data/index.csv     the same events, flattened, for spreadsheets

Every machine identifier listed in .env (serial, hardware UUID, username, hostnames)
is replaced with a placeholder such as <SERIAL> before anything is written.
Standard library only; works with the python3 that ships with Xcode Command Line Tools (3.9+).
"""
import csv
import datetime as dt
import glob
import hashlib
import json
import os
import re
import sys

try:
    from zoneinfo import ZoneInfo
except ImportError:  # Python < 3.9
    ZoneInfo = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
RAW = os.path.join(DATA, "raw")
SYSINFO = os.path.join(DATA, "system-info")

# ----------------------------------------------------------------------------- .env + scrubbing

def load_env(path=os.path.join(ROOT, ".env")):
    env = {}
    if not os.path.exists(path):
        return env
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


ENV = load_env()

# .env key -> placeholder written into the index
SCRUB_KEYS = [
    ("MAC_SERIAL", "<SERIAL>"),
    ("MAC_HW_UUID", "<HW_UUID>"),
    ("MAC_COMPUTER_NAME", "<COMPUTER_NAME>"),
    ("MAC_LOCAL_HOSTNAME", "<HOSTNAME>"),
    ("MAC_HOSTNAME", "<HOSTNAME>"),
    ("MAC_USERNAME", "<USER>"),
]


def _build_scrub_rules():
    rules = []
    for key, placeholder in SCRUB_KEYS:
        val = ENV.get(key, "")
        if len(val) < 3:
            continue
        variants = {val}
        if key == "MAC_COMPUTER_NAME":
            # macOS turns "Jane's MacBook Pro" into "Janes-MacBook-Pro" in report file names
            variants.add(re.sub(r"[^A-Za-z0-9-]", "", val.replace(" ", "-")))
        if key == "MAC_HOSTNAME":
            variants.add(val.split(".")[0])
        for v in variants:
            if len(v) < 3:
                continue
            if key == "MAC_USERNAME":
                # only replace the username where it is clearly a path or account name,
                # so a short username doesn't clobber unrelated words
                rules.append((re.compile(r"(/Users/)" + re.escape(v) + r"\b"), r"\1" + placeholder))
                rules.append((re.compile(r"\b" + re.escape(v) + r"\b", re.I), placeholder))
            else:
                rules.append((re.compile(re.escape(v), re.I), placeholder))
    # longest patterns first so "Janes-MacBook-Pro" wins over "Janes"
    rules.sort(key=lambda r: -len(r[0].pattern))
    return rules


SCRUB_RULES = _build_scrub_rules()


def scrub(obj):
    if isinstance(obj, str):
        for rx, rep in SCRUB_RULES:
            obj = rx.sub(rep, obj)
        return obj
    if isinstance(obj, dict):
        return {k: scrub(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub(v) for v in obj]
    return obj

# ----------------------------------------------------------------------------- time helpers

TS_RX = re.compile(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})(\.\d+)?\s*([+-]\d{2}:?\d{2}|Z)?")


def local_tz():
    name = ENV.get("MAC_TIMEZONE") or "UTC"
    if ZoneInfo:
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    return dt.timezone.utc


def parse_ts(text, assume_local=False):
    """Parse the timestamp formats Apple uses; return ISO-8601 UTC ('...Z') or None."""
    if not text:
        return None
    m = TS_RX.search(str(text))
    if not m:
        return None
    date, clock, frac, off = m.groups()
    frac = (frac or "")[:7]  # datetime handles up to microseconds
    base = dt.datetime.strptime(date + " " + clock + (frac if frac else ""),
                                "%Y-%m-%d %H:%M:%S" + (".%f" if frac else ""))
    if off and off != "Z":
        off = off.replace(":", "")
        sign = 1 if off[0] == "+" else -1
        tz = dt.timezone(sign * dt.timedelta(hours=int(off[1:3]), minutes=int(off[3:5])))
        base = base.replace(tzinfo=tz)
    elif off == "Z":
        base = base.replace(tzinfo=dt.timezone.utc)
    else:
        base = base.replace(tzinfo=local_tz() if assume_local else dt.timezone.utc)
    return base.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def ts_from_filename(fn):
    """Report file names carry local time, e.g. shutdown_stall_2026-10-15-184429_host or 2026-10-21-155518_kernel."""
    m = re.search(r"(\d{4}-\d{2}-\d{2})-(\d{2})(\d{2})(\d{2})", fn)
    if not m:
        return None
    return parse_ts("%s %s:%s:%s" % m.groups(), assume_local=True)


def ts_from_mtime(path):
    return dt.datetime.fromtimestamp(os.path.getmtime(path), dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

# ----------------------------------------------------------------------------- small utils

def rel(path):
    return os.path.relpath(path, ROOT)


def event_id(*parts):
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def read_text(path):
    with open(path, errors="replace") as f:
        return f.read()


def split_ips(text):
    """.ips files are a one-line JSON header followed by a JSON body."""
    head, _, body = text.partition("\n")
    try:
        h = json.loads(head)
    except ValueError:
        h = {}
    try:
        b = json.loads(body) if body.strip() else {}
    except ValueError:
        b = {}
    return h, b


def norm_hex(s):
    return re.sub(r"0x[0-9a-fA-F]+", "0x…", s) if s else s

# ----------------------------------------------------------------------------- parsers per report type

def parse_panic_text(panic):
    """Fields from a macOS kernel panic string."""
    out = {}
    first = panic.strip().splitlines()[0] if panic.strip() else ""
    m = re.match(r"panic\(cpu (\d+) caller 0x[0-9a-f]+\):\s*(.*)", first)
    if m:
        out["panic_cpu"] = int(m.group(1))
        out["panic_reason"] = m.group(2).strip()
    else:
        out["panic_reason"] = first.strip()
    out["panic_reason_normalized"] = norm_hex(out.get("panic_reason"))
    m = re.search(r"type = (\d+=[\w ]+)", panic)
    if m:
        out["exception"] = m.group(1).strip()
    m = re.search(r"Panicked task .*?pid (\d+): ([^\s]+)", panic)
    if m:
        out["pid"] = int(m.group(1))
        out["process"] = m.group(2)
    m = re.search(r"Kernel Extensions in backtrace:\s*\n\s*([\w.]+)\(", panic)
    out["panic_subsystem"] = m.group(1) if m else None
    m = re.search(r"System uptime in nanoseconds: (\d+)", panic)
    if m:
        out["uptime_s"] = round(int(m.group(1)) / 1e9)
    m = re.search(r"Hibernation exit count: (\d+)", panic)
    if m:
        out["hibernation_exit_count"] = int(m.group(1))
    m = re.search(r"System model name: (\S+)", panic)
    if m:
        out["model"] = m.group(1)
    m = re.search(r"Mac OS version:\s*\n\s*(\S+)", panic)
    if m:
        out["os_build"] = m.group(1)
    m = re.search(r"Compressor Info: (.*)", panic)
    if m:
        out["compressor_info"] = m.group(1).strip()
    m = re.search(r"last started kext at \d+: (\S+)", panic)
    if m:
        out["last_started_kext"] = m.group(1)
    out["third_party_roots_installed"] = int(m.group(1)) if (m := re.search(r"roots installed: (\d+)", panic)) else None
    return out


def kernel_panic(path):
    text = read_text(path)
    h, _ = split_ips(text)
    panic = h.get("panic_string") if isinstance(h, dict) and h.get("panic_string") else text
    ev = {"category": "kernel_panic", "source": "system"}
    ev.update(parse_panic_text(panic))
    ev["ts"] = ts_from_filename(os.path.basename(path)) or ts_from_mtime(path)
    ev["process"] = ev.get("process") or "kernel"
    return ev


def bridge_panic(path):
    h, b = split_ips(read_text(path))
    if str(h.get("bug_type")) != "210":
        return None
    panic = b.get("panicString", "") or ""
    return {
        "category": "t2_panic_copy",
        "source": "bridge",
        "ts": parse_ts(h.get("timestamp")) or ts_from_mtime(path),
        "process": "bridgeOS",
        "caused_by": h.get("caused_by"),
        "bridge_os": h.get("os_version"),
        "os_build": (re.search(r"\((\w+)\)", h.get("macos_version", "")) or [None, None])[1],
        "panic_reason": panic.strip().splitlines()[0] if panic.strip() else None,
        "note": "T2 chip's copy of a macOS panic; count category=kernel_panic for panic totals",
    }


def app_crash(path, source):
    h, b = split_ips(read_text(path))
    bug = str(h.get("bug_type"))
    if bug == "298":  # jetsam (low-memory kill)
        return {
            "category": "jetsam",
            "source": source,
            "ts": parse_ts(h.get("timestamp")) or ts_from_mtime(path),
            "process": "kernel",
            "largest_process": b.get("largestProcess"),
            "os_build": (re.search(r"\((\w+)\)", h.get("os_version", "")) or [None, None])[1],
        }
    if bug == "210":
        return bridge_panic(path)
    if bug not in ("309", "109"):  # 309 = crash report; others (stackshots etc.) skipped
        return None
    ex = b.get("exception") or {}
    term = b.get("termination") or {}
    ev = {
        "category": "app_crash",
        "source": source,
        "ts": parse_ts(h.get("timestamp")) or ts_from_mtime(path),
        "process": b.get("procName") or h.get("app_name") or h.get("name"),
        "process_path": b.get("procPath"),
        "bundle_id": (b.get("bundleInfo") or {}).get("CFBundleIdentifier"),
        "app_version": h.get("app_version") or None,
        "parent": b.get("parentProc"),
        "responsible": b.get("responsibleProc"),
        "exception_type": ex.get("type"),
        "signal": ex.get("signal"),
        "subtype": (ex.get("subtype") or "").strip() or None,
        "subtype_normalized": norm_hex((ex.get("subtype") or "").strip()) or None,
        "termination": term.get("indicator"),
        "termination_namespace": term.get("namespace"),
        "os_build": (re.search(r"\((\w+)\)", h.get("os_version", "")) or [None, None])[1],
    }
    sub = ev["subtype"] or ""
    ev["fs_pagein_error"] = "FS pagein error" in sub
    ev["codesign_invalid"] = term.get("namespace") == "CODESIGNING" or "Code Signature Invalid" in (ev["signal"] or "")
    # faulting frame
    imgs, threads, ft = b.get("usedImages", []), b.get("threads", []), b.get("faultingThread")
    if ft is not None and ft < len(threads):
        for fr in threads[ft].get("frames", [])[:4]:
            ii = fr.get("imageIndex")
            if ii is not None and ii < len(imgs):
                ev["faulting_image"] = imgs[ii].get("name")
                ev["top_frame"] = "%s`%s" % (imgs[ii].get("name"), fr.get("symbol", "?"))
                break
    vm = b.get("vmRegionInfo") or ""
    m = re.search(r"--->\s+(.*)", vm)
    if m:
        ev["fault_region"] = re.sub(r"\s+", " ", m.group(1)).strip()
    m = re.search(r"bytes after start: (\d+)", vm)
    if m:
        ev["fault_offset_in_region"] = int(m.group(1))
    return ev


def resource_report(path):
    text = read_text(path)
    def field(name):
        m = re.search(r"^%s:\s*(.+)$" % re.escape(name), text, re.M)
        return m.group(1).strip() if m else None
    event = field("Event")
    detail = field("Writes") or field("CPU") or field("Wakeups")
    return {
        "category": "resource_report",
        "source": "system",
        "ts": parse_ts(field("Date/Time")) or ts_from_mtime(path),
        "ts_end": parse_ts(field("End time")),
        "process": field("Command"),
        "process_path": field("Path"),
        "resource": event,
        "action_taken": field("Action taken"),
        "detail": detail,
        "os_build": (re.search(r"Build (\w+)", field("OS Version") or "") or [None, None])[1],
    }


def shutdown_stall(path):
    return {
        "category": "shutdown_stall",
        "source": "system",
        "ts": ts_from_filename(os.path.basename(path)) or ts_from_mtime(path),
        "process": "shutdown",
        "note": "binary spindump; view with: spindump -i <file>",
    }

# ----------------------------------------------------------------------------- unified-log derived events

LOG_LINE_RX = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+[+-]\d{4})\s+\S+\s+(\S+?)\[\d+\]:\s*(.*)$")


def log_events():
    """Events extracted from saved `log show` / `pmset -g log` output in data/system-info/**."""
    events = {}
    cause_text = {}
    pmset_files = glob.glob(os.path.join(SYSINFO, "**", "pmset_shutdowns*.txt"), recursive=True)
    for p in pmset_files:
        for line in read_text(p).splitlines():
            m = re.search(r"SMC shutdown cause: (-?\d+):\s*(.*?)\s*$", line)
            if m and m.group(2):
                cause_text[m.group(1)] = m.group(2)

    for p in sorted(glob.glob(os.path.join(SYSINFO, "**", "*.txt"), recursive=True)):
        for line in read_text(p).splitlines():
            m = LOG_LINE_RX.match(line)
            if not m:
                continue
            ts_raw, proc, msg = m.groups()
            ev = None
            if "apfs_announce_hash_mismatch" in msg:
                hm = re.search(r"(disk\w+) Data hash mismatch for (\d+) bytes at offset (\d+) in .*? of inode (\d+): expected (\w+), got (\w+)", msg)
                ev = {"category": "apfs_hash_mismatch", "source": "kernel_log", "process": proc}
                if hm:
                    ev.update({"volume": hm.group(1), "bytes": int(hm.group(2)), "offset": int(hm.group(3)),
                               "inode": hm.group(4), "expected_hash": hm.group(5), "got_hash": hm.group(6)})
            elif "Previous shutdown cause:" in msg:
                code = msg.rsplit(":", 1)[1].strip()
                ev = {"category": "shutdown_cause", "source": "kernel_log", "process": proc,
                      "shutdown_cause": int(code) if re.match(r"-?\d+$", code) else code,
                      "shutdown_cause_text": cause_text.get(code)}  # only what macOS itself printed
            if ev:
                ev["ts"] = parse_ts(ts_raw)
                ev["message"] = msg[:500]
                ev["file"] = rel(p)
                ev["event_id"] = event_id("log", ts_raw, msg)
                events[ev["event_id"]] = ev  # dedupe lines seen in overlapping log exports
    return list(events.values())

# ----------------------------------------------------------------------------- main

SKIP = re.compile(r"(deviceMetadata\.proxy|ResetCounter-|\.DS_Store)$")


def collect():
    events = []
    for source in ("system", "user", "bridge"):
        for path in sorted(glob.glob(os.path.join(RAW, source, "*"))):
            fn = os.path.basename(path)
            if not os.path.isfile(path) or SKIP.search(fn):
                continue
            try:
                if fn.endswith("_kernel.panic") or fn.endswith(".panic"):
                    ev = kernel_panic(path)
                elif fn.endswith(".shutdownStall"):
                    ev = shutdown_stall(path)
                elif fn.endswith(".diag"):
                    ev = resource_report(path)
                elif fn.endswith(".ips"):
                    ev = bridge_panic(path) if source == "bridge" else app_crash(path, source)
                else:
                    ev = None
            except Exception as e:  # keep going; record the failure as an event
                ev = {"category": "parse_error", "source": source, "ts": ts_from_mtime(path), "error": repr(e)}
            if ev:
                ev["file"] = rel(path)
                ev["event_id"] = event_id(ev["file"])
                events.append(ev)
    events.extend(log_events())
    host_label = ENV.get("HOST_LABEL") or "mac"
    for ev in events:
        ev["host"] = host_label
        ev.setdefault("model", ENV.get("MAC_MODEL") or None)
    events = [scrub(e) for e in events]
    events.sort(key=lambda e: (e.get("ts") or "", e.get("event_id")))
    return events


CSV_COLS = ["ts", "category", "source", "process", "exception", "exception_type", "signal", "subtype_normalized",
            "panic_subsystem", "panic_reason_normalized", "top_frame", "fault_region", "resource", "detail",
            "shutdown_cause", "shutdown_cause_text", "volume", "offset", "inode", "host", "os_build", "file", "event_id"]


def main():
    if not ENV:
        print("warning: no .env found. Run scripts/setup_env.sh first so identifiers get scrubbed.", file=sys.stderr)
    events = collect()
    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(DATA, "index.ndjson"), "w") as f:
        for e in events:
            f.write(json.dumps({k: v for k, v in e.items() if v is not None}, sort_keys=True) + "\n")
    with open(os.path.join(DATA, "index.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(events)
    counts = {}
    for e in events:
        counts[e["category"]] = counts.get(e["category"], 0) + 1
    print("%d events -> data/index.ndjson  (%s)" % (len(events), ", ".join("%s=%d" % kv for kv in sorted(counts.items()))))


if __name__ == "__main__":
    main()
