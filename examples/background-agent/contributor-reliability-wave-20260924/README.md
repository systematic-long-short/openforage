# Background reliability evidence archive — 2026-09-24

This archive retains the four sanitized, parent-selected proof outputs and their
attempt indexes byte-for-byte. It records bounded Linux observations for the
external background controller, not full native-product conformance. Raw native
event streams, credentials, and private host/authentication data are not included.

## Attempt outcomes

| Proof | Installed product version / profile | Result |
|---|---|---|
| [OpenCode attempt 1](background-opencode-1.json) | `1.18.31`, Linux, `isolated` (`--pure`) | **Failed** after the optional-state normalizer bug; its original outcome and source bundle 1 remain preserved. SHA-256 `6dbc25b6ab887d898e96e07e607d86347e7f4cc72fb0d0324af6895deda595b5`. |
| [OpenCode attempt 2](background-opencode-2.json) | `1.18.31`, Linux, `isolated` (`--pure`) | **External-controller edges passed; overall conformance partial.** Ten external-controller lifecycle edges and cleanup passed. All nine typed native primitive outcomes remain unknown. SHA-256 `6ca3e39beae1bf5b8ee1fdbc0298b625ff6009a31615bf39b1b70cfacb36ad43`. |
| [Codex attempt 1](background-codex-1.json) | `codex-cli 0.156.1`, Linux, `isolated` (`--ignore-user-config`, `features.goals=true`, `--sandbox workspace-write`) | **External-controller edges passed; overall conformance partial.** Ten external-controller lifecycle edges and cleanup passed. All nine typed native primitive outcomes remain unknown. SHA-256 `a09c09cd4f5f0e7ec48118e1cdaa782202698612cb2b5184fbe050c086daa1e8`. |
| [Claude Code attempt 1](background-claude-code-1.json) | `2.1.281 (Claude Code)`, Linux, isolated fixture with native permissions inherited | **Failed.** An inherited native `Write` denial blocked the fixture marker; the child did not provide the required structured acknowledgement. The CLI exited 0, but the task/proof did not succeed. Cleanup was confirmed; no capability was promoted and no permission bypass or retry was attempted. SHA-256 `166e345fcfe6fd63fedd5382880a939514080c0b07f20dadaca55bcb2df926a3`. |

The OpenCode request used `openrouter/openai/gpt-6-luna` with variant `max`;
Codex used `gpt-6-astra` with reasoning effort `medium`; Claude Code used
`sonnet` with effort `medium`. These are different requested model selectors,
not a harness-only comparison or independent evidence of backend-served models.
The selectors and exact proof scope remain in the immutable records.

## Canonical scope and unknowns

Only the successful OpenCode 2 and Codex 1 v2 proof artifacts are cited by new
scoped canonical records in `harnesses.json`; each record is tied to its exact
artifact, installed version, Linux OS, isolated profile, mechanism, and
lifecycle. External controller pause/stop, exact-child continuation, detached
invocation, and external due-time recurrence remain distinct mechanisms and
claims. The passing controller edges do not establish native harness pause,
stop, scheduling, or full product conformance.

For each successful run, the nine typed outcomes—goal declaration, task
creation, task-list write, durable task reread, task-state update, memory write,
durable memory reread, native scheduler creation, and native scheduler
delivery—are all `unknown`. In particular, the task-list operation is not a
durable task store, an external due-time delivery is not a native scheduler,
and exact-session history is not global memory. Neither proof started an
OpenForage worker; checkpoint and worker-stop behavior remain unknown. Earlier
capability records remain tied to their historical proof artifacts and source
evidence; this archive does not rewrite or broaden them.

The read-only adapter probe record is also retained
([read-only-adapter-probes.json](read-only-adapter-probes.json)); its version/help
results launched no sessions, inspected no authentication state, and changed
no configuration. Attempt indexes are retained in
[opencode-attempts.json](opencode-attempts.json),
[codex-attempt.json](codex-attempt.json), and
[claude-attempt-diagnosis.json](claude-attempt-diagnosis.json).

## Tested source snapshots

The immutable `source_file_digests` maps in the proof outputs describe their
pre-integration tested inputs. The source archives and exact provenance
manifests are retained under [`source-snapshots/`](source-snapshots/):

| Snapshot | Applies to | Bundle SHA-256 |
|---|---|---|
| [`prior-background-runtime`](source-snapshots/prior-background-runtime/) | Prior inert source bundle and its two historical proof records; no raw native streams | `0f69012ddede8067bdadb70b51ee2e4c9d3fbe3f698f67b0698f89e9cc7f878c` |
| [`background-runtime-1`](source-snapshots/background-runtime-1/) | Failed OpenCode attempt 1; 25 tested source/input files | `5c5a8c3b967b6c2ac049849e9d9ea76f45833e66dc8e7f538ebaa9f5e744b69e` |
| [`background-runtime-2`](source-snapshots/background-runtime-2/) | OpenCode attempt 2, Codex attempt 1, and Claude attempt 1; 25 tested source/input files | `19d9cd0f6d44024683ed6dd010b9f47e06bf211c60e38c8ebea01753e661333b` |

The v2 proofs hashed the pre-integration registry at SHA-256
`4b28c0dc26fd1dba684a44bc229de4d5083dc75c0956fcd273bc8faafe884efb`. The
canonical registry necessarily changes when it cites the new proof artifacts;
the retained bundles are immutable input snapshots, not the current projection.
No proof claims to have hashed a registry that already contains itself.

## Limits

The records do not establish host-reboot or terminal-hangup survival, native
goal/task/memory/scheduler operation, OpenForage worker checkpoint/stop, normal
user plugin/config compatibility, live search, registration, campaign or server
acceptance, evaluation, out-of-sample correlation/usefulness, or a successful
`genetic` 100,000-simulation run. The reference count remains a target, not a
measured result.
