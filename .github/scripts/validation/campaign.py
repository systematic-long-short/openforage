"""Campaign validation orchestration.

The rule set is split into coherent modules:

- :mod:`state_machine` — state machine definition check and append-only history;
- :mod:`protocol` — preregistration, statistics, provenance, security,
  usefulness metrics, and completion/adapter honesty;
- :mod:`baselines` — frozen-reference and compatible-baseline availability;
- :mod:`resources` — execution limits, lifecycle observations, accounting,
  coverage, and submissions;
- :mod:`records` — record shape, trial inventory, benchmark records, and the
  strict example templates.

This module keeps the public entry points used by the validator and the
self-tests: ``check_records``, ``check_examples``,
``check_state_machine_definition``, and the shared placeholder helper.
"""

from __future__ import annotations

from .baselines import (  # noqa: F401  (re-exported for callers)
    check_benchmark_record,
    check_compatible_baseline,
    check_frozen_baseline,
)
from .common import is_placeholder  # noqa: F401  (re-exported for callers)
from .records import (  # noqa: F401  (re-exported for callers)
    CAMPAIGN_REQUIRED_KEYS,
    EXPERIMENT_REQUIRED_KEYS,
    RECORD_CLASSIFICATIONS,
    TRIAL_STATUSES,
    check_campaign_record,
    check_coverage_records,
    check_examples,
    check_experiment_record,
    check_records,
    check_trial_inventory,
)
from .resources import (  # noqa: F401  (re-exported for callers)
    RESOURCE_KINDS,
    check_resources,
)
from .state_machine import (  # noqa: F401  (re-exported for callers)
    ACTIVE_STATES,
    HOLD_STATES,
    STATES,
    TERMINAL_STATES,
    check_history,
    check_state_machine_definition,
    load_state_machine,
    machine_index,
)
