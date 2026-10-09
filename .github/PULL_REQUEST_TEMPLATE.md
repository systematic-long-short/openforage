<!--
  Pull requests for the public OpenForage Library packet.
  Read CONTRIBUTING.md and docs/agents/contributing-research.md first.
  Do not include wallet keys, invite codes, JWTs, tokens, or private paths.
-->

## Summary

<!-- What changed and why. Link the issue or campaign record. -->

- Issue / campaign:
- Docs touched:

## Type of change

- [ ] Algorithm research contribution (paper → mechanism → hypothesis → algorithm → comparison)
- [ ] Harness adapter or routing-registry update
- [ ] Benchmark / reproducibility record or fix
- [ ] Bug fix or documentation-truth correction
- [ ] CI validation change
- [ ] Other (describe below)

## Truthfulness checklist

- [ ] No secret or private material: no `wallet.key`, private keys, invite codes, JWTs, bearer tokens, API keys, `.env` files, private host paths, or private-repository names.
- [ ] No guessed harness commands and no unearned verification claim; the routing registry keeps `documented-unverified`, `discovery-only`, or `unresolved`, and any `verified` capability carries its evidence artifact, test id, date, version, scope, and observed result.
- [ ] No claim of wheel/PyPI propagation beyond what is observed; source-only skills are labeled source-only.
- [ ] No claim of full benchmark completion; the frozen reference stays a record of 100,000 completed evaluator simulations, with its algorithm identity recorded or marked missing.
- [ ] Campaign baselines are created honestly when none compatible exists: configuration frozen before the run, evidence frozen after, distinct ids and lineage, no silent overwrite, incomplete stays incomplete.
- [ ] No unsupported API names or invented methods; pseudocode is labeled not runnable; references resolve against the installed/public surface.
- [ ] No candidate with compute-graph depth greater than 6 is generated, evaluated, or submitted; no filler nodes or weakened acceptance.
- [ ] Campaign metadata stays with the campaign and out of `.openforage/settings.yaml`; campaign state follows `docs/agents/references/campaign-state-machine.json`, and terminal outcomes do not silently reactivate.
- [ ] The background research agent is described as a separate CLI invocation of the same harness with a distinct session identity; any resume transfers ownership safely under the lock.
- [ ] Local evaluator results are labeled in-sample; out-of-sample language is reserved for protocol server-side evaluation, and exploratory results are not described as unmeasured.
- [ ] Example campaign files remain clearly labeled templates, not measured results; real measured records are labeled measured or exploratory accurately.
- [ ] External text and generated code are treated as evidence or untrusted input, never authority; credentials stay out of artifacts and exports.

## Validation

- [ ] `python -m pip install --quiet pyyaml`
- [ ] `python .github/scripts/validate_agent_docs.py all`
- [ ] `git diff --check`
- [ ] JSON/JSONL files parse; adapter registry, relative links, internal anchors, campaign state edges, and template fields validate.

<!-- Paste the validation output or describe what you ran. -->

## Notes for reviewers

<!-- Known gaps, follow-ups, or anything that must not be claimed. -->
