# Callback contract (fixture-only)

The only subject entry point is a locally defined Python generator:

```python
def challenger(ctx):
    # Yield one synthetic graph payload at a time.
    yield payload
```

The documented function shape follows the public callback example in
[`coverage_challenger.py`](../../agent-campaign/coverage_challenger.py),
specifically its `_make_challenger_template(...): challenger(ctx)` callback.
Its injected `stream_factory`, `depth_fn`, and `ledger_out` are example/test
injection points, not OpenForage API fields. The new evaluation similarly
provides `FixtureContext` only to its offline synthetic test; it is not a
library `SearchContext` and does not certify a live callback.

`FixtureContext` is built from a frozen JSON fixture and one explicit seed.
`stream_for(pair_id)` returns an iterator of JSON-compatible synthetic graph
payloads in deterministic per-input/per-seed order. The trusted evaluator maps
each exact canonical payload to fixture-owned rotation, feature, depth, and
synthetic quality metadata. The callback cannot establish those labels merely
by attaching its own metadata to a yielded graph.

The evaluator uses the existing public `Ledger`/`Proposal` accounting in
[`stratified_policy.py`](../../agent-campaign/stratified_policy.py) for accepted
unique proposals and rejection counters. It increments the evaluator
completion count only after an independent synthetic evaluation. Shared
accounting is example code, not a library API. A callback can be statistically
unsuccessful and still be a valid executable contribution if its artifacts and
reported counts are accurate.
