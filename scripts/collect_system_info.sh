#!/bin/zsh
# collect_system_info.sh: capture evidence that isn't in the crash-report folders.
# Read-only; changes nothing on the Mac. Output goes to data/system-info/<timestamp>/ (gitignored).
#
#   scripts/collect_system_info.sh          kernel log for the last 14 days
#   scripts/collect_system_info.sh 30d      ... or any `log show --last` window
#
# The shutdown-cause query scans the whole unified log and can take several minutes.
set -u
ROOT="${0:A:h:h}"
WINDOW="${1:-14d}"
OUT="$ROOT/data/system-info/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$OUT"

# zsh has its own builtin called `log`, so always call macOS's by full path.
LOG=/usr/bin/log

echo "Hardware / SSD / GPU…"
system_profiler SPHardwareDataType SPMemoryDataType SPNVMeDataType SPStorageDataType SPDisplaysDataType > "$OUT/system_profiler.txt" 2>&1
sw_vers > "$OUT/sw_vers.txt"
{ csrutil status; csrutil authenticated-root status; } > "$OUT/csrutil.txt" 2>&1
diskutil info / > "$OUT/diskutil_root.txt"
diskutil apfs list > "$OUT/apfs_list.txt"

echo "Shutdown causes (whole retained log, slow)…"
$LOG show --style syslog --predicate 'eventMessage CONTAINS "Previous shutdown cause"' > "$OUT/shutdown_causes.txt" 2>&1

echo "Kernel storage / APFS / GPU messages, last $WINDOW…"
$LOG show --last "$WINDOW" --style syslog --predicate 'process == "kernel" AND (eventMessage CONTAINS[c] "hash mismatch" OR eventMessage CONTAINS[c] "EBADMSG" OR eventMessage CONTAINS[c] "checksum" OR eventMessage CONTAINS[c] "nvme" OR eventMessage CONTAINS[c] "IOAccelerator" OR eventMessage CONTAINS[c] "apfs_vnop")' > "$OUT/kernel_storage_gpu_errors.txt" 2>&1

echo "pmset shutdown history…"
pmset -g log | grep -iE "shutdown cause|panic" > "$OUT/pmset_shutdowns.txt" 2>&1

echo "Done → ${OUT#$ROOT/}"
echo "Note: system_profiler.txt contains your serial number. data/system-info is gitignored; keep it that way."
python3 "$ROOT/scripts/build_index.py"
