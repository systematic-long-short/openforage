# Paper-backed research contribution evaluation

This optional fresh-agent fixture requires a completed, executable challenger
contribution: a locally authored Python callback, a typed research/result
artifact, and a machine report whose development claims are independently
recomputed by the grader. It is an instruction-isolated public fixture, not a
blind evaluation or an operating-system security boundary.

The native subject trace has a focused access audit. It permits the task,
research README and schemas, callback contract, cited excerpt and provenance,
the selected series manifest, authored outputs, bounded hashes of those paths,
native writes to the declared contribution files, and the exact development
evaluator command. Direct subject reads or shell inspection/execution of the
reserved `fixture.json`, `research_grader.py`, `research_selfcheck.py`, or
`v2/grader.py` fail; `runner.py` is invoked only by that exact development
command. The evaluator's trusted internal reads are not counted as subject file
actions. Other evaluator implementation source files are outside the subject
read allowlist. Unsupported or incomplete native traces remain unknown and
cannot establish reserved-input isolation. This is instruction-level evidence,
not an adversarial operating-system sandbox.

## Entrypoint-first route for new native trials

The frozen task protocol requires completed content reads of root `README.md`
and `AGENTS.md` as the first content sources. Next, the subject must either
successfully run the read-only `python scripts/start_here.py --task research`
selector or read `docs/agents/start-here.md`; then it must read this research
README and `task.md` before editing. The grader records each event sequence and
native action identity. Injected task text, filename listings, and glob
discovery do not satisfy content-read evidence. The `start_here.py` trial form
accepts one documented `--task` selector plus optional `--format text|json` or
a bounded `--harness` name; generation flags are not admitted.

A structured `read` counts toward the route only with observed nonempty output
and a requested range covering the existing source from its beginning through
its end. Empty output, offsets beyond EOF, and partial ranges are not whole-file
reads. `grep` is a content search, not a whole-source read; listings/globs and
partial `head`, `tail`, or `sed` reads also do not establish the route. A
successful bounded `cat` of an existing nonempty workspace file is a
whole-source shell read. Searches and partial reads can still show that another
content source was consulted before the root entries; they never satisfy a
required whole-source read.

The observed OpenCode `read` offset is **1-based**: an omitted offset and
`offset=1` start at line 1, and `offset=2` starts at line 2. A local boundary
probe observed `offset=0` returning line 1 as well; the grader accepts 0 and 1
as first-line spellings while retaining the supplied typed arguments in the
hash-bound native record. For successful compound shell actions, the route
evidence preserves the `&&` segment and operand positions, so ordered root
reads, selector, and task reads within one native action can be checked in
sequence. Separate native actions require recorded completion/start ordering.
These positions do not create internal timestamps, independent native
identities, or per-segment outcomes; a nonzero chain does not supply successful
content reads. Searches such as `grep` still count as prior source consultation
when ordered before the root entries, but never as full-source reads.

The actual `research` start-here packet links these allowed public route files:
`docs/agents/research-opportunities.json`,
`docs/agents/research-campaign.md`, `docs/agents/research-security.md`,
`docs/agents/references/compatibility.json`,
`src/openforage/skills/search_research/SKILL.md`,
`src/openforage/skills/agent_runtime_integration/references/harnesses.json`,
`src/openforage/skills/agent_runtime_integration/references/unsupported.md`,
and `examples/agent-campaign/campaign.json`. Their inclusion permits public
route research; it does not permit reading evaluation implementation or
reserved fixture contents.

OpenCode `read`, `grep`, `list`, and `glob` actions retain typed bounded
arguments and their digest, native call identity, event sequence, and explicit
completion. `read`/`grep` access content; `list`/`glob` expose filenames only
and never count as a source read. Missing, malformed, conflicting, redacted,
escaping, symlinked, or incomplete arguments stay unknown/refused. The shared
shell parser is capped at 8192 UTF-8 command bytes, eight `&&` segments, and a
Codex wrapper depth of two; unrecognized or mutating forms remain unknown.

The synthetic evaluator compares the callback with a separately implemented
uniform selector over the fixture's eligible pairs. Both arms receive paired
seeds and the same completed-evaluator-call target. This reference is named
`synthetic_uniform_pair_selector_v1`; it is not OpenForage's `genetic`
algorithm, a measured library result, or evidence of live performance. The
separate `genetic` target remains 100,000 completed simulations and is
unmeasured here.

## Stable callback fixture interface

The contribution file is `trial-output/challenger.py` and must define a local
`def challenger(ctx)` generator that yields one graph payload per `yield`.
`ctx` is the explicitly test-only `FixtureContext` in `contract.py`, not an
OpenForage `SearchContext` or a library API. Its fields and method are:

- `seed`: paired deterministic fixture seed;
- `depth`: synthetic fixture depth cap, always 6;
- `scope`: synthetic `era_id` and `config_id` for this input;
- `eligible_pairs`: loaded, eligible `rotation|feature` identifiers in this
  fixture only;
- `excluded_pairs`: the fixture's excluded or unloaded identifiers;
- `pair_descriptors`: supplied fixture metadata, not candidate introspection;
- `stream_for(pair_id)`: the shared finite test stream for that pair, yielding
  synthetic JSON graph payloads. Calling it again returns the same stream
  cursor.

The fixture deliberately includes repeated graph identities, unknown payloads,
excluded/unloaded pairs, and graphs whose supplied synthetic depth exceeds 6.
The trusted evaluator maps exact canonical fixture payloads to descriptors; it
does not trust a callback's claimed feature, rotation, depth, or quality.
Only a unique, known, loaded, eligible payload at depth 1–6 that the evaluator
actually evaluates increments the completed-call counter. Proposal, duplicate,
invalid, excluded, unloaded, and over-depth attempts do not.

The paper attribution reuses the existing archived excerpt at
[`../fixtures/challenger-research/trial-inputs/primary-source-excerpt.md`](../fixtures/challenger-research/trial-inputs/primary-source-excerpt.md).
The short note in `source-provenance.json` is a paraphrase of Mouret and Clune's
MAP-Elites description, not a quotation or a superiority claim. Do not extend
the archived quotation.

## Offline use

Run from the checkout root. These commands make no harness/model calls and need
no package install, network, credentials, wallet, or live search:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv-docs/bin/python examples/fresh-agent-evals/research/runner.py --selfcheck
```

After reviewing the task, templates, grader, fixtures, provenance, and helper
closure, freeze a new series exactly once. The output path must not already
exist; every run and grader rechecks the frozen digests:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv-docs/bin/python examples/fresh-agent-evals/research/runner.py \
  --freeze --series-id <series-id> \
  --output examples/fresh-agent-evals/research/series/<series-id>.json
PYTHONDONTWRITEBYTECODE=1 .venv-docs/bin/python examples/fresh-agent-evals/research/runner.py \
  --check-freeze --series-manifest examples/fresh-agent-evals/research/series/<series-id>.json
```

The native action is explicit and should be run only by the parent after the
implementation session and any callback workers have exited. Supply a caller-
chosen model and harness selector; there is no model default. Run one harness
first, then pin the other to the first trial's `snapshot_sha256` if comparing
harness subjects:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv-docs/bin/python examples/fresh-agent-evals/research/runner.py \
  --native-run --harness opencode --case paper-backed-coverage-challenger \
  --series-manifest examples/fresh-agent-evals/research/series/<series-id>.json \
  --model <provider/model> --variant <caller-selected-variant> --run-id opencode-research-1

PYTHONDONTWRITEBYTECODE=1 .venv-docs/bin/python examples/fresh-agent-evals/research/runner.py \
  --native-run --harness codex --case paper-backed-coverage-challenger \
  --series-manifest examples/fresh-agent-evals/research/series/<series-id>.json \
  --model <provider/model> --reasoning-effort <caller-selected-effort> \
  --expected-snapshot-sha256 <snapshot-sha256-from-first-trial> --run-id codex-research-1
```

The child first writes `trial-output/challenger.py`, then runs the exact
development-only evaluator command shown in its task prompt to create
`trial-output/comparison-report.json`, then writes
`trial-output/research-result.json` with source, implementation, report, and
series hashes. The grader checks that a completed native command with the
recorded full command digest and unique lifecycle identity occurred between
those writes; a self-reported command result is not evidence.

Current research verdicts contain a shared structured `diagnostics` array and
the measured `entry_route`. Diagnostics attribute failures to a native event
sequence/hashed action identity or a workspace-relative artifact when available,
separate permission denial, unsupported evidence, artifact/schema errors,
protocol violations, comparison outcomes, and runtime unavailability, and give
a bounded next step. Negative, mixed, or incomplete honest synthetic results
are informational research outcomes, not protocol failures. Historical verdicts
remain unchanged and may omit these fields.

For Codex, the retained `command` evidence is the full safely normalized
`bash -lc`/`sh -lc` wrapper, optionally with one nested login-shell wrapper,
when it is within the shared 8192-byte UTF-8 parser bound. Chains are limited to
eight `&&` segments. Its digest and byte count cover the complete safely
normalized wrapper; the normalized inner command is never a substitute for it. Missing,
conflicting, redacted, truncated, or over-bound command text remains unknown
and cannot establish the evaluation.
The separate `command_preview` is for display only; private absolute paths are
redacted from the authoritative text rather than retained as previews.

The development-only command, also usable for a local callback check after a
series is frozen, is:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv-docs/bin/python examples/fresh-agent-evals/research/runner.py \
  --evaluate-development --implementation trial-output/challenger.py \
  --report-out trial-output/comparison-report.json \
  --series-manifest examples/fresh-agent-evals/research/series/<series-id>.json
```

For a retained native run, the runner independently executes the callback on
each development input/seed and a reserved held-out input/seed, then writes a
sanitized verdict with the full recomputed coverage/comparison report under
`.tmp/fresh-agent-research/runs/<series-id>/<run-id>/`. The held-out input is
not part of the development report and should not be used while developing a
native subject. All fixture material is public, so this remains instruction-
isolated rather than perfectly blind. Raw native streams remain in ignored
`.tmp` scratch; records, patches, hashes, and verdicts omit private host paths
and can be exported after review.

A retained run can be independently graded again without launching a harness.
The runner resolves its local throwaway workspace from the recorded `run_id`;
an explicit `--workspace` may be supplied if the workspace was moved:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv-docs/bin/python examples/fresh-agent-evals/research/runner.py \
  --grade \
  --trial .tmp/fresh-agent-research/runs/<series-id>/<run-id>/trial.json \
  --series-manifest examples/fresh-agent-evals/research/series/<series-id>.json \
  --output .tmp/fresh-agent-research/runs/<series-id>/<run-id>/independent-regrade.json
```

The report labels every quality bit synthetic, scopes the eligible denominator
to the fixture, and leaves live performance, out-of-sample return correlation,
and server acceptance `null` with status `unknown`. If either arm exhausts
before the target, it reports `incomplete_comparison` with the observed counts;
it does not pad counts. Negative and inconclusive outcomes are valid, and no
challenger superiority is required. Construction coverage is not return
correlation.

An unavailable optional OpenCode or Codex runtime produces a clear
`unavailable` record rather than a guessed command result. The offline
`--selfcheck` remains available everywhere and never launches a harness.
