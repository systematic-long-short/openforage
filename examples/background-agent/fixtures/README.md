# Inert background-adapter fixtures

Every fixture in this directory is synthetic. The Claude Code JSONL file uses
the documented `system/init`, `stream_event`/`content_block_start`, and final
`result` shapes to check the adapter parser; it is not a captured Claude Code
session. The lifecycle facts fixture feeds the reusable edge checker and is
not a controller run or a capability observation. The proof-envelope fixture
checks only the typed v2 shape. None of the fixtures enters
`harnesses.json` or establishes product behavior.
