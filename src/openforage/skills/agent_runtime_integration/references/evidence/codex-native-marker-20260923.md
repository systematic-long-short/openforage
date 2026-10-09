# Codex native marker observation — 2026-09-23

**Outcome:** the bounded fixture passed. The Codex harness remains
**documented-unverified** overall; this is not adapter certification.

## Recorded run

- CLI: `codex-cli 0.156.1` on Linux.
- Caller-selected model/settings: Astra, medium reasoning effort.
- UTC interval: 2026-09-23 12:42:47–12:43:30.
- Five CLI calls had explicit exit code 0; cleanup confirmed no owned process
  remained.
- Fresh parent and child thread IDs were distinct. The parent exited before
  the child began.
- Three later `exec resume` calls targeted the exact recorded child ID and
  matched the marker retained in thread history.
- Six scoped results passed: separate CLI invocation, distinct thread
  identity, parent-exit-before-child-continuation, exact-ID history
  continuation, history-marker recall, and the finite external-driver check.
- Three results remain unknown: native goal/task operation, global memory, and
  active-process pause/stop. Native goal/task tool/event evidence was absent;
  enabling the goals feature and final prose do not establish an operation.

## Scope and limits

One due event was dispatched by a finite **external** driver. Its bounded
paused/stopped windows prevented later driver invocations; they do not prove a
native scheduler or pause/stop of active Codex work. The native schedule remains
unverified. Sequential parent exit does not test a concurrent daemon,
parent-death/hangup/reboot, or crash recovery. The run establishes no global
memory, OpenForage callback, signal search, simulation, or submission result.

## Provenance

The sanitized JSON record was copied byte-for-byte from the completed
`.tmp/codex-lifecycle/run01.json`. The retained record contains five raw-trace
hashes and four source hashes; these were verified against retained ignored
scratch files. The public evidence contains no raw provider outputs or private
host-path strings.
