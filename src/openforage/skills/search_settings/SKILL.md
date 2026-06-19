---
name: search-settings
description: Use this when configuring OpenForage search defaults, choosing genetic versus random_weighted, setting n_jobs, or writing a settings.yaml file for reproducible genetic search runs.
---

# Search Settings

`openforage.search_settings` is the canonical settings module for library search defaults. Use `settings.yaml` when a run needs explicit, reviewable knobs; omit the file to use documented defaults.

## Defaults

- `default_algorithm`: `genetic`
- `n_jobs`: all cores from the current CPU count
- `batch_size`: `100`
- `process_timeout_seconds`: `120`
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

Place settings in `settings.yaml` or pass the path explicitly through the Python API:

```python
import openforage

handle = openforage.search(settings_path="settings.yaml")
```

Example:

```yaml
default_algorithm: genetic
n_jobs: 2
batch_size: 100
process_timeout_seconds: 120
seed: 20260613
restart_threshold: 5
elite_fraction: 0.20
diversity_weight: 1.50
parsimony_weight: 0.05
operator_rates:
  crossover: 0.45
  point_mutation: 0.25
  subtree_mutation: 0.20
  hoist_mutation: 0.10
```

## Safety Rules

Malformed YAML, unknown keys, invalid algorithms, non-positive `n_jobs`,
non-positive `batch_size`, non-positive `process_timeout_seconds`, non-positive
`restart_threshold`, invalid `elite_fraction`, negative weights, and empty
`operator_rates` fail loudly with `SearchSettingsError`.

Use a smaller `n_jobs` only when memory, CPU contention, or a test needs a
bounded run. Raise `process_timeout_seconds` only when real candidate
simulations exceed the default budget and the overall run timeout is still
bounded. The default worker count is all cores.
