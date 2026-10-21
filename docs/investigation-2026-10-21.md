# Investigation: recurring kernel panics on a MacBookPro16,1 (2026-10-21)

**Machine:** MacBook Pro 16" 2019 (MacBookPro16,1), 8-core i9, 16 GB DDR4-2667 (soldered, non-ECC),
1 TB Apple SSD AP1024N, Intel UHD 630 + AMD Radeon Pro 5500M, T2 chip (bridgeOS 10.3).
**OS:** macOS 26.3.2 (25D2140). System Integrity Protection on, Authenticated Root on, system volume sealed.
**History (owner's report):** kernel panics "all the time" for ~2 years, persisting across factory resets and
clean macOS installs. macOS had kept reports only back to 2026-10-14.

Identifiers (serial number etc.) are intentionally omitted. All data referenced here is in
`data/index.ndjson`; raw reports stay on the owner's Mac.

## Timeline of 2026-10-21 (EDT)

| Time | Event |
|---|---|
| 15:53 | **Kernel panic #1**: general protection fault (type 13) in `com.apple.iokit.IOAcceleratorFamily2`, process `com.apple.WebKit.GPU`. Uptime ~11.6 h, 23 hibernation exits. No third-party kexts (`roots installed: 0`) |
| 15:54 | Reboot. SMC "Previous shutdown cause: -20" |
| 15:54–15:59 | **60 × `apfs_announce_hash_mismatch`** on disk1s2 (sealed System volume), every one the same inode, same 16 KB block at offset 15,482,880, same expected hash, and **the same wrong hash** |
| 15:55–15:58 | 15 crashes with `SIGBUS` / `FS pagein error: 94 Bad message` (errno 94 = EBADMSG) or `KERN_MEMORY_ERROR` across suggestd, reversetemplated, IMDPersistenceAgent, corespotlightd, mds_stores. All faulted 15,490,688 bytes into a 26.7 MB mapped file used by `/usr/lib/liblangid.dylib`. That offset lies **inside** the flagged block (15,482,880–15,499,264). The file is `/usr/share/langid/langid.inv` |
| 15:57–16:01 | Claude.app's crash handler (`chrome_crashpad_handler`, SIGTRAP ×3) and two `node` segfaults inside V8's parser |
| 16:01 | Warm reset (cause 1) |
| 16:12 | **Kernel panic #2**: `kfree: addr 0x1000000000000, size 0 … not found in any zone` in `com.apple.filesystems.lifs`, process `kernel_task`. Uptime ~10.8 min, **no sleep since boot**, 0% memory pressure |
| 16:13 | Reboot, cause -20 |
| 16:33 | Apple Diagnostics: **ADP000, "No issues found"** |
| 16:55 | Re-read of `langid.inv`: OK, no new hash mismatches. After `sudo purge`, re-read of the single flagged block: OK. (Caveat: the file is memory-mapped by running processes, so the read may have been served from cache) |

## Earlier in the retained window

- 2026-10-14: `airportd` SIGBUS `KERN_MEMORY_ERROR` ×17 in 22 min at one address in its framework code.
- 2026-10-15: `CommCenter` killed 25× in 5 min with **SIGKILL (Code Signature Invalid)**, namespace CODESIGNING,
  indicator "Invalid Page", at the same address in the dyld shared cache. `mediaanalysisd` once.
- ~14 SIGSEGVs in unrelated code: JavaScriptCore GC, objc retain/release, Spotlight index, libunwind.
- 2026-10-18: 15 boots in ~13 h, each preceded by shutdown cause 5 ("Software initiated shutdown").

## Health checks that passed

| Check | Result |
|---|---|
| Apple Diagnostics | ADP000 (memory codes PPM001–016, storage codes VDH001–004 not reported) |
| SSD S.M.A.R.T. | Verified |
| NVMe `CRITICAL_WARNING` at each boot | 0x0 |
| SIP / Authenticated Root / volume seal | Enabled / enabled / sealed |
| Flagged block re-read | OK |

The "AppleNVMe Assert failed" pair in the kernel log appears at **every** boot, including the clean one after
Diagnostics, so it isn't counted as evidence.

## What the evidence supports

- **Code-signing kills and EBADMSG page-ins** mean the kernel read pages of Apple-signed, sealed system files whose
  bytes did not match their hashes. An unmodified, sealed install should not produce this.
- **Identical wrong bytes 60 times within one boot**, then correct bytes after reboot. The corruption was
  repeatable during that boot but was not reproduced afterwards.
- **Two panics in unrelated subsystems** (GPU acceleration, filesystem), one only 11 minutes after a fresh boot
  with no sleep: not explained by sleep/wake, memory pressure, or a third-party driver.
- **Every on-demand test passes.** Failures are intermittent and appear under normal use.

The logs point to the hardware data path (SSD/T2 storage controller, memory, or logic board) rather than a
specific macOS bug, but they **cannot isolate which component**. On this model RAM and SSD are soldered to the
logic board.

## Next steps

1. Boot macOS from an external drive for several days. If the crashes stop, the internal SSD path is implicated;
   if they continue, it isn't.
2. Apple Store / authorized service: bring this write-up; ask for testing under load.
3. Keep collecting: `scripts/update.sh` after every crash; `scripts/collect_system_info.sh` periodically.
