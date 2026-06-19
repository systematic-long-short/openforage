# First Search in 3 Lines

## The Three Lines

```python
import openforage
openforage.register()
openforage.search()
```

## What Happens

### Line 1: Import

Loads the library. All dependencies are included.

### Line 2: Register

Creates a wallet, authenticates, and syncs data. After the first call, subsequent sessions reuse existing credentials.

### Line 3: Search

The `search()` function starts the signal discovery loop:

1. **Template selection** — `genetic` is the default search template; `random_weighted` remains available as an explicit baseline and fork source
2. **Signal composition** — The template composes signals by combining features and transformations into compute graphs
3. **Local evaluation** — Each composed signal is evaluated locally against the current era's quality thresholds
4. **Submission** — Signals that pass all local thresholds are submitted to the protocol
5. **Iteration** — The search continues, composing and evaluating new signals

## Monitoring Progress

```python
openforage.status()
```

Check the status of your search without blocking. The status object reports:

- How many signals have been evaluated
- How many passed local thresholds
- How many were submitted
- Current sync state

## Search Templates

The library ships with built-in templates. `genetic` is the default first-run template; `random_weighted` is a good baseline and comparison run. As you learn the system, you can:

- Try other built-in templates
- Customize template parameters
- Write your own search algorithms using the SearchContext API

## What Makes a Good Signal

A signal is a compute graph that takes features as input and outputs directional forecasts. Good signals:

- Pass in-sample quality thresholds (set by the current era)
- Demonstrate predictive power that holds out-of-sample
- Are sufficiently different from existing signals in the portfolio
