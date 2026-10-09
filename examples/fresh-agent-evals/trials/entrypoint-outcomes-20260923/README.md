# Discovery entry-point trial archive — 2026-09-23

This offline archive records six native observations: three quickstart-entry
attempts, two pasted-entry attempts, and one contribution cold-start attempt.
Every native CLI exit code was `0`, and every record reports
`owned_process_remaining=false`; the original grader outcomes below are kept
separate from those execution facts. The archive preserves earlier records,
verdicts, and artifacts. The two latest entry attempts also have derived,
same-native-run rubric regrades; neither regrade is a new model run or erases
its original failed verdict.

## Quickstart-entry history — original verdicts

### `discovery-quickstart-entry-luna` (initial)

Original verdict: **FAILED, 3,663 failures**. Of these, 3,658 concerned
unintended `.opencode` runtime-dependency writes; five were route/wording
failures. The retained record reports 25 file-tool actions.

- [Original trial record](discovery-quickstart-entry-luna.trial.json)
- [Original verdict](discovery-quickstart-entry-luna.trial.verdict.json)
- [Substantive artifact](artifacts/quickstart-research.json)

### `discovery-quickstart-entry-luna-rerun`

Original verdict: **FAILED, 6 failures**. Two were permission-key versus
normalized `apply_patch` event-label mismatches; four duplicated two exact
source-URL and algorithm-identifier schema mismatches. The historical
explanation and rubric changes are retained in
[the earlier closeout](closeout-20260923.md); this verdict remains unchanged.

- [Original trial record](discovery-quickstart-entry-luna-rerun.trial.json)
- [Original verdict](discovery-quickstart-entry-luna-rerun.trial.verdict.json)
- [Substantive artifact](artifacts/discovery-quickstart-entry-luna-rerun/quickstart-research.json)

### `discovery-quickstart-entry-luna-canonical-rerun` (latest original verdict)

Original verdict: **FAILED, 3 rubric failures**. The actual artifact said the
100,000 figure was a target, not an observed completion; described a
“separately started CLI process”; and recorded campaign access as `unknown`
because access was unchecked. The original grader required the literal phrase
“target only,” did not recognize “separately started,” and required access to be
`unavailable`. It had no isolation or forbidden-action violations.

- [Original trial record](discovery-quickstart-entry-luna-canonical-rerun.trial.json)
- [Original, unchanged FAIL-3 verdict](discovery-quickstart-entry-luna-canonical-rerun.trial.verdict.json)
- [Substantive artifact](artifacts/discovery-quickstart-entry-luna-canonical-rerun/quickstart-research.json)
- [Derived rubric regrade](discovery-quickstart-entry-luna-canonical-rerun.trial.verdict.regrade.json)
- [Regrade provenance](discovery-quickstart-entry-luna-canonical-rerun.trial.verdict.regrade.provenance.json)

The derived regrade passes with **0 remaining grading failures** on the same
workspace, trial record, and retained raw-trace corroboration. The rubric now
accepts explicit target-versus-unobserved-result wording, while still rejecting
a claimed measured GA benchmark or completed simulation count; recognizes the
“separately started CLI process” wording while requiring same-harness identity
and prohibiting main-session fallback; and permits campaign access to remain
`unknown` when unchecked while still rejecting an unsupported `available`
claim. The original FAIL-3 verdict remains part of the evidence.

## Pasted-entry history — original verdicts

### `discovery-pasted-entry-luna` (initial)

Original verdict: **FAILED, 5 failures**. Two were `apply_patch` event-label
mismatches. One was a genuine attempted outside-workspace read and remains a
real failure. The remaining two were rubric assumptions: the selected
canonical onboarding JSON target was rejected, and a literal `README` token was
required despite the correct authorization/session semantics. Details remain
in [the earlier closeout](closeout-20260923.md); no failure is removed here.

- [Original trial record](discovery-pasted-entry-luna.trial.json)
- [Original verdict](discovery-pasted-entry-luna.trial.verdict.json)
- [Substantive artifact](artifacts/discovery-pasted-entry-luna/pasted-entry-contribution.json)

### `discovery-pasted-entry-luna-rerun` (latest original verdict)

Original verdict: **FAILED, 1 rubric failure**. The artifact says no live route
is currently authorized or needed for the contribution, then explicitly
requires user authorization for future live registration/search, a separate
same-harness CLI process with a distinct session identity, and no main-session
fallback. The grader mistook the current-state sentence for a waiver of future
authorization. There were no outside-workspace actions.

- [Original trial record](discovery-pasted-entry-luna-rerun.trial.json)
- [Original, unchanged FAIL-1 verdict](discovery-pasted-entry-luna-rerun.trial.verdict.json)
- [Substantive artifact](artifacts/discovery-pasted-entry-luna-rerun/pasted-entry-contribution.json)
- [Derived rubric regrade](discovery-pasted-entry-luna-rerun.trial.verdict.regrade.json)
- [Regrade provenance](discovery-pasted-entry-luna-rerun.trial.verdict.regrade.provenance.json)

The derived regrade passes with **0 remaining grading failures** on the same
native observation and retained raw-trace corroboration. The corrected check
accepts the artifact's explicit future authorization requirement, while
regressions continue to reject claims that authorization is unnecessary and
instructions to run without it. The original FAIL-1 verdict remains preserved.

## Contribution cold-start — trial pass, validator failure

- [Trial record](discovery-contribution-cold-start-outcomes-luna.trial.json)
- [Original verdict](discovery-contribution-cold-start-outcomes-luna.trial.verdict.json)
- [Substantive artifact](artifacts/discovery-contribution-cold-start-outcomes-luna/contribution-discovery.json)
- Native trial outcome: **PASSED, 0 failures**; CLI exit code `0` and
  `owned_process_remaining=false`.
- The artifact honestly records its standalone pre-edit link-validator exit
  code as `1` with `baseline_result="fail"`. That validator failure is distinct
  from the passing trial outcome and is not reported as a validator pass.

## Related scoped Codex evidence

The separate [Codex native marker record](../../../../src/openforage/skills/agent_runtime_integration/references/evidence/codex-native-marker-20260923.json)
and [scope/provenance note](../../../../src/openforage/skills/agent_runtime_integration/references/evidence/codex-native-marker-20260923.md)
report a bounded fixture pass. Three items remain unknown—native goal/task
operation, global memory, and active-process pause/stop—and Codex remains
**documented-unverified overall**.

## Copy and regrade boundaries

The two latest sanitized trial records and their original verdicts were copied
byte-for-byte from ignored scratch, as were their substantive JSON artifacts.
Each derived regrade and provenance file is separately retained in scratch and
here. Raw JSONL/provider logs, prepared workspaces, `node_modules`, and
prepare-metadata files were not copied. No native run was repeated for either
regrade.
