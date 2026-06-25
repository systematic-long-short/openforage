---
name: search-settings
description: Use this when configuring OpenForage search defaults, choosing genetic versus random_weighted, setting n_jobs, or writing a settings.yaml file for reproducible genetic search and feature-rotation runs.
---

# Search Settings

`openforage.search_settings` is the canonical settings module for library search defaults. The same `settings.yaml` also carries contributor feature-rotation settings when it lives at `.openforage/settings.yaml`. Use `settings.yaml` when a run needs explicit, reviewable knobs; omit the file to use documented defaults.

## Defaults

- `default_algorithm`: `genetic`
- `n_jobs`: all cores from the current CPU count
- `batch_size`: `100`
- `process_timeout_seconds`: `120`
- `process_priority`: `normal`
- `seed`: `20260613`
- `restart_threshold`: no-improvement window before fresh diversity injection
- `elite_fraction`: fraction of structurally distinct prior candidates used for breeding
- `diversity_weight`: penalty strength for redundant, high-correlation candidates
- `parsimony_weight`: penalty strength for larger expression trees
- `operator_rates`: relative rates for `crossover`, `point_mutation`, `subtree_mutation`, and `hoist_mutation`

`random_weighted` remains explicitly selectable by CLI, Python API, or settings file:

```yaml
default_algorithm: random_weighted
```

## Settings File

The library ships a starter `settings.yaml` at `src/openforage/settings.yaml`. Keep it non-secret. On init, OpenForage creates `.openforage/settings.yaml` from that library default. When the file already exists, init performs an intelligent merge: new fields from the library default are added, removed fields are removed locally, and all other existing user values are preserved. Runtime data belongs under `.openforage/data/`.

For a local run, pass the generated settings path to the search API or CLI so search settings and feature-rotation settings are read from one file:

```bash
mkdir -p .openforage/data
openforage start --data-dir .openforage/data \
                 --settings-path .openforage/settings.yaml \
                 --json
```

```python
import openforage

openforage.configure(data_dir=".openforage/data")
handle = openforage.search(settings_path=".openforage/settings.yaml")
```

The starter file includes search keys (`default_algorithm`, `n_jobs`, `batch_size`, `process_timeout_seconds`, `process_priority`, `seed`, `restart_threshold`, `elite_fraction`, `diversity_weight`, `parsimony_weight`, `operator_rates`) and feature-rotation keys (`algorithm`, `feature_budget_gb`, `market`, `named_universes`, `rotation_frequency`, `rotation_seed`, `contributor_stats_upload_opt_out`). Edit the file deliberately; do not split these knobs across multiple local files.

## Safety Rules

Malformed YAML, duplicate keys, unknown keys, invalid algorithms, non-positive `n_jobs`, non-positive `batch_size`, non-positive `process_timeout_seconds`, invalid `process_priority`, non-positive `restart_threshold`, invalid `elite_fraction`, negative weights, empty `operator_rates`, non-positive `feature_budget_gb`, empty `named_universes`, invalid `rotation_frequency`, and invalid `contributor_stats_upload_opt_out` fail loudly.

Use a smaller `n_jobs` only when memory, CPU contention, or a test needs a bounded run. Set `process_priority` to `normal`, `below_normal`, or `idle`; the default `normal` preserves current worker behavior. Raise `process_timeout_seconds` only when real candidate simulations exceed the default budget and the overall run timeout is still bounded. Use `feature_budget_gb` to bound local artifact footprint; use `contributor_stats_upload_opt_out: true` only when the operator intentionally disables contributor statistics upload for that run. Without the starter file, the default worker count is all cores.
