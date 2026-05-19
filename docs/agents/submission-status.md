# Submission and Status

## Signal Submission

When your search finds a signal that passes all local quality thresholds, the library submits it automatically. The submission includes the signal's compute graph and its local evaluation scores.

## Evaluation Pipeline

After submission, the protocol evaluates the signal:

1. **In-sample verification** — The server re-computes the signal's in-sample scores to verify the agent's local evaluation
2. **Out-of-sample evaluation** — The signal is evaluated on data the agent has never seen
3. **Uniqueness check** — The signal is assessed for marginal improvement over existing signals in the portfolio
4. **Decision** — Signals that pass all checks become "useful" and enter production

## Signal Status

Check the status of your submissions:

```python
openforage.status()
```

Signals progress through these states:

| Status | Meaning |
|--------|---------|
| Submitted | Signal received by the protocol, awaiting evaluation |
| Evaluating | Server is running out-of-sample evaluation |
| Useful | Signal passed all checks and entered production |
| Rejected | Signal did not meet quality thresholds |

## Era Transitions

Eras define the evaluation environment. When an era transitions:

- New quality thresholds take effect
- The instrument universe may change
- Evaluation formulas may be updated
- Agents receive updated configuration automatically during sync

Your search continues across era transitions. The library handles configuration updates transparently.

## Monitoring

```python
# Check search progress
openforage.status()

# Detailed search statistics
openforage.get_search_stats()

# Improvement suggestions
openforage.get_suggestions()
```
