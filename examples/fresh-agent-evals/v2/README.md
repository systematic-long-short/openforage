# Finished-contribution trials v2

This is the preferred current first-contribution suite. It tests a **finished,
reviewable documentation correction**, not a plan: the subject discovers the
repository route, runs a baseline check, edits a seeded fixture defect, records
typed facts in `trial-output/completion.json`, and runs a relevant validator
again. The human summary and rationale are retained separately and are never
graded for research quality or magic wording.

## Cases and disclosure

`cases.json` defines one primary task and one distinct held-out task. The
primary fixture is shared unchanged by OpenCode and Codex; the held-out fixture
is reserved for an independent later check and is not used by native model-trial
subjects unless explicitly selected with `--include-heldout`; mechanical
selfchecks and preflight may exercise the fixture. The task gives the output
path, full JSON Schema, and isolation boundaries, but not the defective page,
validator command, expected fact values, or repair recipe.

The public checkout contains `oracle.json`, grader code, cases, and fixtures.
Prepared workspaces retain the full public validation closure so an authentic
`all` check does not fail because eval sources were removed. The task forbids
direct inspection of case/oracle/answer/grader material; validator execution
may read its own test sources. The access audit checks recorded native events
and commands. This is instruction-isolated, not a pristine blind evaluation or
an OS sandbox, and an incomplete trace leaves access and action-count metrics
unknown.

## Frozen named series

The checked-in `series.json` is the **historical, pre-native series-1 draft**;
it has no native model trials and is preserved byte-for-byte. Ordinary `all`
validation and selfchecks do not replay that archive. An explicit
`--archived-series1-drift-check` reports whether its old input closure still
matches; it is not a current run selection.

```bash
python examples/fresh-agent-evals/v2/freeze.py --archived-series1-drift-check
```

After final review, create one new named manifest for the selected series using
a caller-chosen NEW UNIQUE `<series-id>` and a new output path. `--create` needs
that unused path; `--check` and a runner's `--series-manifest` may select an
existing manifest, including an archived series. The manifest freezes only the
case/schema/oracle definitions, v2 grading and runner helpers (including the
shared shell parser, structured native-argument evidence, and diagnostic
contract), reused v1 outcome/trace helpers, canonical route facts,
the transitive `start_here.py` onboarding helper modules and route/data inputs
(including capability-registry evidence artifacts), the dependent validator
modules, and the fixture source pages. It does not freeze the whole checkout.
Every runner and grader requires an explicit selected manifest and refuses drift in
that closure. Per-trial input file maps and snapshot hashes identify the
prepared public source snapshot, including unrelated documentation at that
time; these do not make ordinary repository selfchecks fail after later edits.
Selected manifest bytes are recorded separately by their own hash.

```bash
PYTHONDONTWRITEBYTECODE=1 .venv-docs/bin/python examples/fresh-agent-evals/v2/freeze.py \
  --create --series-id <series-id> \
  --output examples/fresh-agent-evals/v2/series/<series-id>.json
PYTHONDONTWRITEBYTECODE=1 .venv-docs/bin/python examples/fresh-agent-evals/v2/freeze.py \
  --check --series-manifest examples/fresh-agent-evals/v2/series/<series-id>.json
```

Manifest creation refuses an existing path. Grader changes require a new named
series; a regrade uses `--regrade-label` and a separate verdict path. Trial
records, raw streams, and verdicts are exclusive-create artifacts; use a new
`--run-id` for each invocation.

## Explicit native runs

Run the same primary case in separate fresh workspaces, one native subject at
a time, after the implementation session exits. Supply a caller-selected model
and a harness-specific variant/reasoning effort; the public runner has no model
default. Run OpenCode first; use its recorded `snapshot_sha256` as Codex's
`--expected-snapshot-sha256` pin. The CLI gates on the locally observed OpenCode `1.18.31` or Codex
`0.156.1` version before launching.

```bash
PYTHONDONTWRITEBYTECODE=1 .venv-docs/bin/python examples/fresh-agent-evals/v2/runner.py \
  --harness opencode --case first-finished-contribution-primary \
  --series-manifest examples/fresh-agent-evals/v2/series/<series-id>.json \
  --model <provider/model> --variant <caller-selected-variant> --run-id opencode-primary-1

PYTHONDONTWRITEBYTECODE=1 .venv-docs/bin/python examples/fresh-agent-evals/v2/runner.py \
  --harness codex --case first-finished-contribution-primary \
  --series-manifest examples/fresh-agent-evals/v2/series/<series-id>.json \
  --model <provider/model> --reasoning-effort <caller-selected-effort> \
  --expected-snapshot-sha256 <snapshot-sha256-from-opencode-trial> --run-id codex-primary-1
```

The runner records each workspace's complete initial file map and snapshot
digest. After both successful primary trials, compare them without launching a
harness:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv-docs/bin/python examples/fresh-agent-evals/v2/comparison.py \
  --opencode-trial .tmp/native-contribution-wave/runs/opencode-primary-1/trial.json \
  --codex-trial .tmp/native-contribution-wave/runs/codex-primary-1/trial.json
```

The comparison refuses different selected manifests, source identities,
prepared file maps, or prepared snapshot digests, so different source inputs
cannot be attributed to a harness difference. It also reports sanitized
requested model/variant/reasoning selectors side by side; these are argv
requests, not proof of the backend-served model.

OpenCode uses `run --pure --dir W --model M --variant V --agent build --format
json`; the runner injects a wildcard-deny profile, allows local read/edit/shell
and `todowrite`, explicitly denies external-directory, web/network, nested
`task`, interactive `question`, and mode-switching `plan_enter`/`plan_exit`
actions, and checks the exact effective named-agent matrix before launch. Local
`todowrite` remains available for todo planning. The question and mode-switching
restrictions apply only to this single-turn contribution fixture; normal
background adapters continue inheriting native configuration. Clarification
observations therefore describe this restricted fixture and are not an estimate
for unrestricted sessions. The runner also checks that project `.opencode` and
`node_modules` trees remain absent. Shell is available, so these permission and
trace checks do not prove that the host is unable to reach a network through
every possible command.

Codex uses `--no-daemon --ask-for-approval never --model M --cd W --sandbox
workspace-write -c model_reasoning_effort=E exec --json --ignore-user-config`.
The runner inherits the configured auth/rules environment without recording
its paths or contents; it does not use `--ignore-rules`, `--last`, or a sandbox
bypass. `workspace-write` is the native selected sandbox boundary; trace checks
are additional observations, not a claim about host-wide isolation.

The runner records sanitized allowlisted actions, event identities, explicit
native outcomes, file digests, and a sanitized verdict. It retains the
caller-requested model selector and variant/reasoning value only when their
shape is safe, labels them as requests (the backend-served model is not
observed), and stores a digest plus unknown reason otherwise. Raw argv,
auth/provider environment, private configuration, raw streams, and absolute
workspace paths are not archived; raw stdout/stderr stay in ignored
`.tmp/native-contribution-wave/` scratch.
Shell validation uses the shared bounded parser in `shell_parser.py` through
`commands.py`, `evidence.py`, and the grader. OpenCode native commands are
parsed as direct shell scripts. Codex native commands are admitted only as one
exact `bash -lc SCRIPT` or `sh -lc SCRIPT` wrapper, optionally containing one
exact nested login-shell wrapper (maximum wrapper depth **2**); completion
claims use the complete inner command. The normalized action's `command` field
retains the full safely normalized native command up to `MAX_COMMAND_BYTES`
(**8192 UTF-8 bytes**); its digest and byte count cover the complete command.
The separate `command_preview` is display-only. Over-bound text and text
containing an unrecognized absolute host path are marked incomplete/redacted
and are not stored as authoritative command text. `normalized_command` is a
convenience value only and cannot replace a missing, truncated, conflicting,
or redacted native command; `command_redaction_status` identifies the private-
path case without copying the path. The quote-aware grammar admits at most
**8** simple argv segments joined only by `&&`, plus the exact harmless
`PYTHONDONTWRITEBYTECODE=1` prefix (also as an `env` assignment). Each chain
segment must be classified by the local command allowlist. Quoted operators
remain argument text; operators are split only outside quotes. Arbitrary
assignments, `PYTHONPATH`/`PYTHONHOME`, `env -i`, substitutions, `;`, `||`,
pipelines, redirection, backgrounding, `eval`, deeper/unclassified shells,
malformed commands, redacted commands, and truncated commands are unadmitted or
unknown. The exact
`PYTHONDONTWRITEBYTECODE=1 python -m json.tool
trial-output/completion.json` helper is recognized as a local artifact read,
not validator or contribution evidence.

Structured OpenCode `read`, `grep`, `list`, and `glob` calls retain a typed
`native_arguments` object, its SHA-256/byte count, argument event sequence,
native call identity, and terminal status. Path/pattern fields are bounded to
2048 UTF-8 bytes, the canonical argument object to 8192 bytes, read offsets to
`0..100000000`, read limits to `1..10000`, and list ignore patterns to 32.
Missing/malformed/unknown fields, conflicting updates, redaction, escaping or
symlink paths, and missing completion remain unknown/refused. `read` and
`grep` are content access; `list` and `glob` are filename discovery only. A
bounded discovery result can identify public inputs but does not count as a
source read or as protected-content access. Content reads/searches of protected
sources still fail the access audit.

For authoritative-source and first-read route credit, a structured `read` also
needs observed nonempty output and a requested range that starts at the source
beginning and reaches its actual end. Empty results, offsets beyond EOF, and
partial ranges do not count as whole-source reads. `grep` remains a content
search, never a whole-source read; filename discovery and partial shell reads
such as `head`, `tail`, or `sed` likewise do not satisfy the route. A successful
bounded `cat` of an existing nonempty workspace file is a whole-source shell
read. Searches and partial reads can still establish that another content source
was consulted before the root entries; they cannot satisfy a required whole-
source read. These distinctions do not change the separate access-audit
treatment of searching protected content.

The observed OpenCode `read` offset is **1-based**: omitted offset and
`offset=1` start at line 1, while `offset=2` starts at line 2. A local boundary
probe also observed `offset=0` returning line 1, so the range check accepts 0
and 1 as first-line spellings while preserving the supplied typed argument in
its hash-bound native record. Offset 2 or later is not a full-source start.
For compound successful shell actions, content observations retain the
`&&` segment index and the operand index within that segment. Those local
positions establish order inside the one native action; across separate
actions, route phases require the recorded completion/start sequence to
establish order. Positions do not create extra native identities, timestamps,
or per-segment outcomes, and a nonzero `&&` chain supplies no successful
content-read credit. Searches, including `grep`, affect first-content-source
ordering but never satisfy a whole-source read.

## Entrypoint-first evidence and diagnostics

Every new contribution trial must show, in native action order: completed
content reads of root `README.md` and `AGENTS.md` as the first content sources;
then either a successful bounded `python scripts/start_here.py --task
contribute` selector or a completed read of `docs/agents/start-here.md`; then a
completed read of `docs/agents/first-contribution.md`. The measurable
`entry_route` verdict field records event sequences and native action identity.
Injected task/protocol text, directory listings, and filename discovery do not
satisfy these reads. The helper's allowed trial arguments are one documented
`--task` selector, with optional `--format text|json` and bounded `--harness`
name; generation and other helper flags are not admitted.

Current verdicts add a `diagnostics` array conforming to
[`diagnostic.schema.json`](diagnostic.schema.json); the field is optional in
the research verdict schema so historical records remain readable. Each item
has a stable code/category, event sequence and hashed native identity or
workspace-relative artifact path when available, observed/missing evidence,
and a bounded next step. Categories distinguish native permission denial,
unsupported evidence, artifact/schema errors, protocol violations, synthetic
research comparison outcomes, and runtime unavailability. Negative, mixed, and
incomplete honest research results remain comparison outcomes, not protocol
failures. CLI summaries include diagnostics or point to the retained verdict;
private paths and credential-shaped text are redacted. Permission-denial
categories require an observed native `denied` status. A native action is
attached only when the failure site establishes one unique sequence and hashed
identity; otherwise `native_action` is null and the missing identity is stated.

## Evidence and offline checks

The grader correlates the baseline to the latest exact validator invocation
completed strictly before the first corrected-page write. It correlates the
final result strictly after the last corrected-page write and, when the trace
exposes it, strictly before the completed write of the final `completion.json`.
Correlation retains native call/item identity, the full command digest, the
complete parsed segment/environment/operator sequence, exact modes, explicit
native exit, and strict ordering. A standalone validator's explicit nonzero
exit is a known failure. For a fully admitted `&&` chain with one or more
validator segments, explicit native exit zero proves every executed segment
passed; the verdict records ordered validator segment indices, flattened modes,
compound-shell exit, and inference basis. A nonzero chain exit does not locate
which validator failed, so no validator is reported as failed and the outcome
stays unknown. Literal `false` segments also make a
contradictory chain exit zero unknown. Repeated checks are resolved by their
observed phase boundary; a missing, denied, conflicting, truncated, or
out-of-order boundary result stays unknown or fails, and the grader never
chooses an exit code to fit a claim. A later recheck after the completion
artifact was written does not revise an earlier accurate claim. The grader then
checks final artifacts against workspace path/digest diffs. It independently
runs the unchanged
`truthfulness` and `links` validators against the final workspace. The
primary correction is checked by AST equivalence of the seeded Python example,
preserved surrounding source, and the real validator; harmless quote/spacing
changes and additive explanation are accepted, while deleting the example or
changing unrelated code is not. The held-out link contract is bounded to its
source context and an existing canonical target; no held-out subject run is
automatic. The
baseline failure and final success are separate observed outcomes. Missing or
unclassifiable events keep setup/clarification totals unknown. Contribution
completion time is reported only as elapsed-at-verification upper bound unless
an exact completion time is observable; first meaningful action has its own
metric. Structured fact mismatches count as unsupported claims. Prose is shown
only in a labeled human-review section. Structured command fields are
workspace-relative; absolute host paths are rejected.

`route.authoritative_sources` lists only canonical files actually consulted
through completed, recorded read operations. Automatically injected
instructions may inform the work, but they are not independently proven read
events for this field; list the files actually read without redundantly listing
every entry file. An unsupported source declaration is a provenance mismatch,
not a finding that the agent invented the source content.

The local audit recognizes exactly `python -m json.tool
trial-output/completion.json` (or the `python3` spelling), with the harmless
bytecode environment prefix, as inspection of the disclosed JSON artifact,
not as repository validation or contribution-success evidence. It also
recognizes exactly `git branch --show-current` as a read-only repository-state
observation; other branch arguments and branch mutations remain unadmitted.
The bounded read-only shell classifications preserve ordinary `cat`, simple
`sed` address/range reads, `rg` searches without `--pre`, and Git `status`,
`diff`, `show`, `log`, `rev-parse`, and `ls-files` inspections. `awk`,
interactive `less`/`more`, `rg --pre`, and `sort --compress-program` forms stay
unclassified because their program or command-execution behavior is not bounded
by this parser.
This narrow classification is not an OS sandbox: arbitrary `python -c`,
unclassified quoted/compound commands, redacted or truncated commands, and
other unknown commands remain unknown and can still fail the strict access-
audit gate. A content read/search of an oracle path or reserved case/grader
source remains direct-suite access even when another segment is a valid
validator; a filename-only `list`/`glob` observation is separately classified.

```bash
PYTHONDONTWRITEBYTECODE=1 .venv-docs/bin/python examples/fresh-agent-evals/v2/runner.py --selfcheck
# After creating the caller-selected series manifest shown above:
PYTHONDONTWRITEBYTECODE=1 .venv-docs/bin/python examples/fresh-agent-evals/v2/freeze.py \
  --check --series-manifest examples/fresh-agent-evals/v2/series/<series-id>.json
```

The v2 selfcheck is explicitly synthetic; it checks schema typing, anti-forgery,
phase-bound repeated validation results, structured native arguments and
discovery/content separation, root-first entry routing, the exact former
multi-validator chain, nested Codex wrappers, quote-aware shell bounds,
zero/nonzero `&&` outcome inference, actionable diagnostic categories,
denied/missing results, manifest drift, no-overwrite evidence, alternate prose,
path safety, snapshot isolation, and an independent validator run. It launches
no harness or model. These regressions do not count
as real trials. The original v1 cases, grader, records, and verdicts remain
preserved as historical evidence under the parent directory.
