# Native background-adapter proof archive

This archive retains sanitized native proof outputs for the Linux-scoped
background controller. It records a bounded external-controller contract, not
a native harness scheduler or the broader OpenForage background-worker
contract. The detailed JSON records include the tested source-file SHA-256
manifest, requested model selectors, lifecycle observations, and explicit
unknowns. Raw provider/event streams remain in ignored `.tmp` scratch and are
not copied here.

## Observations

| Harness | Requested model and selector | Result | Observed scope |
|---|---|---|---|
| OpenCode `1.18.31` | `openrouter/openai/gpt-6-luna`, variant `max` | Passed bounded proof | Distinct parent/child session and ownership acknowledgement; exact-child history recall; two acknowledged external-due marker turns; active pause, reconciliation, recovery, and terminal stop; paused/stopped dispatch suppression; owned process groups gone and isolated workspace removed. Native `todowrite` success was observed. |
| Codex `0.156.1` | `gpt-6-astra`, reasoning `medium` | Passed bounded proof | Same bounded separation, acknowledgement, recurrence, lifecycle, and cleanup observations. Native goal/task/global-memory operations remain unknown. |

These are different requested models, and requested selectors do not
independently establish the backend-served model. Trial differences must not be
attributed solely to harness. Codex CLI selection explicitly chose the
installed `0.156.1` binary after an earlier ambiguous-on-PATH prelaunch
failure; no local executable path is published.

Both successful proofs used the evidence-specific `isolated` profile. The
normal background adapter defaults to `native` and inherits the installed
harness configuration; the isolated fixture does not prove that any user's
plugins, providers, or configuration work. The proofs are Linux-scoped and
show the external controller delivering to one exact child session. They do
not establish a native scheduler, host-reboot or terminal-hangup survival,
global memory, or OpenForage worker checkpoint/stop state. OpenForage worker
state remains unknown; neither proof registered, searched, evaluated,
simulated, or submitted OpenForage work.

OpenCode's observed successful `todowrite` events support only that native task
surface in this trace. Goals and global memory remain unknown. Codex native
goal, task, and global-memory operations remain unknown. A reported task
completion is not independent application-worker validation.

## Preserved attempts

- [`opencode-prelaunch-proof.json`](opencode-prelaunch-proof.json) is the original failed
  prelaunch proof record. Its conservative cleanup-unverified fields are
  unchanged. Separately, the parent diagnosed a missing `build_argv` import
  before `Popen` and confirmed that no raw parent or controller directory was
  created. That parent diagnosis does not upgrade the proof JSON.
- [`opencode-proof.json`](opencode-proof.json) and
  [`codex-proof.json`](codex-proof.json) are distinct later successful proof
  attempts, not regrades of the prelaunch failures.
- [Prelaunch provenance](../../fresh-agent-evals/trials/native-contribution-wave-20260924/prelaunch-attempts.json)
  includes the wrapper-only Codex background failure and the contribution
  preflight failure. They did not reach a native model session and are not
  native trial verdicts.

The proof driver is documented in the
[background adapter guide](../README.md). A read-only next-action proposal is
available through `scripts/research_next.py`; it neither starts this controller
nor schedules work. The controller launches a separate same-harness session,
with manual delivery by default or one explicitly selected external interval
owner. Finished-contribution evaluation is a separate versioned route; see the
[native contribution evaluation archive](../../fresh-agent-evals/trials/native-contribution-wave-20260924/README.md).
