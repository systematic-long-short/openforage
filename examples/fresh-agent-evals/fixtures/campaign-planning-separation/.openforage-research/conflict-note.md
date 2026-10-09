# Ownership conflict fixture

The parent onboarding session (`parent-fixture-01`) wrote the ownership
claim before exiting. The registry then recorded the handoff to
`child-fixture-01`, which the status fixture confirms is live and already
consuming `improvement_prompt` events.

Exactly one live child may own a campaign id or a data directory. The
claim in `ownership.json` was never transferred, so it still names the
exited parent and carries the pre-handoff fencing token.

Decide the correct single owner from the registry and status, then
repair the claim. Campaign metadata lives with the campaign; do not write
anything into `.openforage/settings.yaml`.
