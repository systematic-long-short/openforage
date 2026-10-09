#!/usr/bin/env python3
"""Compatibility drift probe for the public packet.

Reads the machine-readable expectations in
``docs/agents/references/compatibility.json``, probes the installed
``openforage`` surface without credentials or network side effects, and
records the exact package/Python/platform/date plus every difference.

Evidence classes are recorded separately and never conflated. Successful
callable observations are informational ``contract_evidence``; only genuine
drift (missing/non-callable/opaque where not allowed, signature or doc-token
drift, CLI additions/removals/choice drift, failed required probes, version
or platform mismatch) is reported in ``differences``, so a workflow can never
count informational rows as drift.

  * ``cli``          actual no-credential ``--help`` output under the same
                     Python interpreter (``python -m openforage.cli``; the
                     bare ``python -m openforage`` entry point is recorded as
                     observed separately).
  * ``introspection`` actual import + ``callable()`` + ``inspect.signature``.
  * ``doc``          the callable's own docstring signature line only. A doc
                     token proves a documented signature, never the
                     operational call contract.
  * ``runtime``      process-level example checks (offline smoke/live-check).

A missing, failed, or timed-out *required* probe is a failure, never a pass.
An opaque proxy signature (``(*args, **kwargs)``) is recorded as
``contract_evidence: doc-only`` and can never establish the operational
contract. A harness version mismatch yields ``needs_reverification``; a
harness that is not installed is an explicit ``not-applicable`` and is never
required for normal CI (no agent credentials are ever needed).

Modes:
  --check      (default) fail on unsupported version, missing/drifted
               required callables, CLI command/choice drift, or a failed
               required runtime probe.
  --observe    record whatever is installed as an observation only; never a
               support promise and never an edit to the expectations file.
  --selfcheck  deterministic probe-engine regression, including CLI
               additions/removal/default discrepancies, opaque callable
               limitations, timeout=failure, and harness mismatch handling.

This tool never registers, evaluates, submits, or calls production.
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import json
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_DEFAULT = HERE.parents[1]
EXPECTATIONS_REL = "docs/agents/references/compatibility.json"
CLI_TIMEOUT = 45
EXAMPLE_TIMEOUT = 180
HARNESS_TIMEOUT = 20
ALGORITHM_CHOICES_RE = re.compile(r"--algorithm\s+\{([^}]*)\}")
DEFAULT_RE = re.compile(r"--algorithm\s+[^\n(]*\(default:\s*([^)]+)\)")


class ProbeError(RuntimeError):
    pass


def load_expectations(repo: Path) -> dict:
    path = repo / EXPECTATIONS_REL
    if not path.is_file():
        raise ProbeError(f"missing expectations file: {EXPECTATIONS_REL}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data.get("expected_surface"), dict):
        raise ProbeError(f"{EXPECTATIONS_REL}: expected_surface must be an object")
    return data


# ---------------------------------------------------------------------------
# probes
# ---------------------------------------------------------------------------

def _installed_version() -> str:
    module = importlib.import_module("openforage")
    return str(getattr(module, "__version__", "unknown"))


def probe_environment() -> dict:
    return {
        "python": platform.python_version(),
        "python_minor": ".".join(platform.python_version().split(".")[:2]),
        "platform": f"{platform.system().lower()}-{platform.machine()}",
        "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def _signature_of(obj) -> tuple[str, bool]:
    """Return (signature, opaque). An opaque proxy signature exposes no contract."""
    try:
        signature = str(inspect.signature(obj))
    except (TypeError, ValueError):
        return "<no signature exposed>", True
    compact = signature.replace(" ", "")
    return signature, compact in {"(*args,**kwargs)", "(*args, **kwargs)"} or (
        compact.startswith("(*args,**kwargs)")
    )


def probe_callable(dotted: str) -> dict:
    parts = dotted.split(".")
    for cut in range(len(parts) - 1, 0, -1):
        module_name = ".".join(parts[:cut])
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        current = module
        for part in parts[cut:]:
            if not hasattr(current, part):
                break
            current = getattr(current, part)
        else:
            signature, opaque = _signature_of(current)
            doc = (inspect.getdoc(current) or "").strip().splitlines()
            return {
                "call": dotted,
                "present": True,
                "callable": callable(current),
                "signature": signature,
                "opaque_proxy": opaque,
                "doc_signature": doc[0] if doc else "",
                "introspection_evidence": "callable+signature" if callable(current) else "attribute-only",
            }
    return {"call": dotted, "present": False, "callable": False, "error": "no importable module prefix resolves this call"}


def probe_cli(expectations: dict) -> dict:
    """Run actual no-credential CLI help under the same interpreter."""
    cli_spec = expectations["expected_surface"].get("cli") or {}
    invocation = cli_spec.get("invocation") or ["-m", "openforage.cli"]
    result: dict = {
        "invocation": [sys.executable, *invocation],
        "commands": None,
        "start_algorithm_choices": None,
        "observed_default_algorithm": None,
        "probes": {},
    }

    def run(extra: list[str]) -> dict:
        argv = [sys.executable, *invocation, *extra]
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=CLI_TIMEOUT)
        except subprocess.TimeoutExpired:
            return {"exit_code": None, "timed_out": True, "stdout": "", "stderr": "timeout"}
        except OSError as exc:
            return {"exit_code": None, "timed_out": False, "stdout": "", "stderr": str(exc)}
        return {"exit_code": proc.returncode, "timed_out": False, "stdout": proc.stdout, "stderr": proc.stderr}

    top = run(["--help"])
    result["probes"]["--help"] = top
    if top["exit_code"] == 0:
        match = re.search(r"\{([a-z_,]+)\}", top["stdout"])
        if match:
            result["commands"] = [item.strip() for item in match.group(1).split(",") if item.strip()]

    start = run(["start", "--help"])
    result["probes"]["start --help"] = start
    if start["exit_code"] == 0:
        match = ALGORITHM_CHOICES_RE.search(start["stdout"])
        if match:
            result["start_algorithm_choices"] = [
                item.strip() for item in match.group(1).split(",") if item.strip()
            ]
        default = DEFAULT_RE.search(start["stdout"])
        if default:
            result["observed_default_algorithm"] = default.group(1).strip()

    bare = [sys.executable, "-m", "openforage", "--help"]
    try:
        proc = subprocess.run(bare, capture_output=True, text=True, timeout=CLI_TIMEOUT)
        result["bare_m_openforage"] = {
            "argv": bare,
            "exit_code": proc.returncode,
            "available": proc.returncode == 0,
            "stderr_tail": (proc.stderr or "").strip().splitlines()[-1:] or [""],
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        result["bare_m_openforage"] = {"argv": bare, "exit_code": None, "available": False, "error": str(exc)}
    return result


def probe_harness_versions(expectations: dict) -> dict:
    result = {}
    for product, entry in (expectations.get("harness_versions") or {}).items():
        binary = shutil.which(product)
        if not binary:
            result[product] = {
                "available": False,
                "status": "not-applicable",
                "registry_status": entry.get("registry_status"),
                "note": "Harness is not installed in this environment. Normal CI never requires agent credentials; this is not a failure.",
            }
            continue
        try:
            proc = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=HARNESS_TIMEOUT)
            if proc.returncode != 0:
                result[product] = {
                    "available": True,
                    "status": "needs-reverification",
                    "registry_status": entry.get("registry_status"),
                    "error": f"--version exited {proc.returncode}",
                }
                continue
            observed = (proc.stdout.strip() or proc.stderr.strip())
        except (OSError, subprocess.TimeoutExpired) as exc:
            result[product] = {
                "available": True,
                "status": "needs-reverification",
                "registry_status": entry.get("registry_status"),
                "error": str(exc),
            }
            continue
        expected = entry.get("locally_observed")
        result[product] = {
            "available": True,
            "observed_version_output": observed,
            "expected_local_observation": expected,
            "status": "observed-unverified" if observed == expected else "needs-reverification",
            "note": "A local version observation never upgrades a registry entry; re-verify against the harness evidence standard.",
        }
    return result


def probe_example(repo: Path) -> dict:
    example = repo / "examples/agent-campaign/coverage_challenger.py"
    if not example.is_file():
        return {"required": True, "present": False, "status": "failed", "error": "example file missing"}
    try:
        proc = subprocess.run(
            [sys.executable, str(example), "--live-check"],
            capture_output=True,
            text=True,
            timeout=EXAMPLE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return {"required": True, "present": True, "exit_code": None, "timed_out": True, "status": "failed",
                "error": f"--live-check timed out after {EXAMPLE_TIMEOUT}s"}
    except OSError as exc:
        return {"required": True, "present": True, "exit_code": None, "timed_out": False, "status": "failed",
                "error": str(exc)}
    status = "ok" if proc.returncode == 0 else "failed"
    return {
        "required": True,
        "present": True,
        "exit_code": proc.returncode,
        "timed_out": False,
        "status": status,
        "tail": (proc.stdout or proc.stderr)[-300:],
    }


# ---------------------------------------------------------------------------
# comparison engine (pure functions; exercised by --selfcheck)
# ---------------------------------------------------------------------------

def compare_callables(expected_surface: dict, observed: dict) -> tuple[list[dict], list[dict], list[str]]:
    """Return (differences, contract_evidence, missing_required).

    ``differences`` carries only genuine drift. Successful per-callable
    contract observations are informational evidence and are returned
    separately so a workflow can never count ``kind=contract_evidence`` rows
    as drift.
    """
    differences: list[dict] = []
    contract_evidence: list[dict] = []
    missing_required: list[str] = []

    for dotted, spec in (expected_surface.get("required_callables") or {}).items():
        item = observed.get(dotted) or {}
        if not item.get("present"):
            missing_required.append(dotted)
            differences.append({"call": dotted, "kind": "missing_required", "detail": item})
            continue
        if not item.get("callable"):
            missing_required.append(dotted)
            differences.append(
                {"call": dotted, "kind": "required_not_callable", "detail": item,
                 "note": "hasattr() alone is not a callable contract; callable() is required"}
            )
            continue
        signature = item.get("signature", "")
        doc = item.get("doc_signature", "")
        token = spec.get("signature_token")
        doc_token = spec.get("doc_token")
        entry = {
            "call": dotted,
            "kind": "contract_evidence",
            "introspection": item.get("introspection_evidence"),
            "signature": signature,
            "opaque_proxy": item.get("opaque_proxy", False),
            "callable": True,
            "contract_evidence": None,
        }
        if token and token in signature:
            entry["contract_evidence"] = "introspection"
            if doc_token and doc_token not in doc:
                differences.append({"call": dotted, "kind": "doc_token_missing", "expected_doc_token": doc_token,
                                    "observed_doc_signature": doc})
        elif token and token in doc and spec.get("allow_doc_only"):
            entry["contract_evidence"] = "doc-only"
        elif token:
            entry["contract_evidence"] = "missing"
            differences.append(
                {"call": dotted, "kind": "signature_drift", "expected_token": token,
                 "observed_signature": signature, "observed_doc_signature": doc}
            )
        if item.get("opaque_proxy") and not spec.get("allow_opaque"):
            entry["contract_evidence"] = entry["contract_evidence"] or "opaque-unavailable"
            differences.append(
                {"call": dotted, "kind": "opaque_callable_limitation",
                 "observed_signature": signature,
                 "note": "Opaque proxy exposes no operational call contract; cannot establish one."}
            )
        contract_evidence.append(entry)

    for dotted, spec in (expected_surface.get("observed_only_callables") or {}).items():
        item = observed.get(dotted) or {}
        if not item.get("present"):
            differences.append({"call": dotted, "kind": "observed_only_unavailable", "detail": item})
            continue
        if not item.get("callable"):
            differences.append({"call": dotted, "kind": "observed_only_not_callable", "detail": item})
            continue
        if spec.get("signature") and spec["signature"] != item.get("signature"):
            differences.append(
                {"call": dotted, "kind": "observed_signature_drift",
                 "expected_signature": spec["signature"], "observed_signature": item.get("signature")}
            )
        contract_evidence.append(
            {"call": dotted, "kind": "observed_only_present", "signature": item.get("signature"),
             "contract": spec.get("contract"), "opaque_proxy": item.get("opaque_proxy", False)}
        )
    return differences, contract_evidence, missing_required


def compare_cli(cli_spec: dict, observed_cli: dict) -> str | None:
    """Return 'mismatch', 'needs_reverification', or None."""
    expected_commands = set(cli_spec.get("commands") or [])
    observed_commands = set(observed_cli.get("commands") or [])
    expected_choices = list(cli_spec.get("start_algorithm_choices") or [])
    observed_choices = list(observed_cli.get("start_algorithm_choices") or [])
    problems: list[dict] = []

    if not observed_cli.get("probes", {}).get("--help", {}).get("exit_code") == 0:
        problems.append({"kind": "cli_probe_failed", "detail": observed_cli.get("probes", {}).get("--help"),
                         "severity": "mismatch"})
    else:
        removed = sorted(expected_commands - observed_commands)
        added = sorted(observed_commands - expected_commands)
        if removed:
            problems.append({"kind": "cli_commands_removed", "commands": removed, "severity": "mismatch"})
        if added:
            problems.append({"kind": "cli_commands_added", "commands": added, "severity": "needs_reverification"})

    if not observed_cli.get("probes", {}).get("start --help", {}).get("exit_code") == 0:
        problems.append({"kind": "cli_start_probe_failed",
                         "detail": observed_cli.get("probes", {}).get("start --help"), "severity": "mismatch"})
    else:
        if not set(expected_choices) <= set(observed_choices):
            problems.append({"kind": "algorithm_choices_drift", "expected": expected_choices,
                             "observed": observed_choices, "severity": "mismatch"})
        elif set(observed_choices) != set(expected_choices):
            problems.append({"kind": "algorithm_choices_added", "expected": expected_choices,
                             "observed": observed_choices, "severity": "needs_reverification"})
        documented_default = (cli_spec.get("documented_default_algorithm") or {}).get("value")
        observed_default = observed_cli.get("observed_default_algorithm")
        if documented_default is not None and observed_default is not None and documented_default != observed_default:
            problems.append({"kind": "documented_cli_default_discrepancy", "documented_default": documented_default,
                             "observed_cli_default": observed_default, "severity": "needs_reverification"})

    severities = {problem["severity"] for problem in problems}
    if "mismatch" in severities:
        observed_cli["problems"] = problems
        return "mismatch"
    if "needs_reverification" in severities:
        observed_cli["problems"] = problems
        return "needs_reverification"
    observed_cli["problems"] = []
    return None


def compare_surface(expected_surface, observed, installed, environment, supported) -> dict:
    callables = observed.get("callables", observed)
    callable_differences, contract_evidence, missing_required = compare_callables(expected_surface, callables)
    cli_result = compare_cli(expected_surface.get("cli") or {}, observed.get("cli") or {})

    runtime_differences: list[dict] = []
    if not observed.get("example", {}).get("status") == "ok":
        runtime_differences.append({"kind": "required_runtime_probe_failed", "detail": observed.get("example")})
    if observed.get("harness_versions"):
        for product, entry in observed["harness_versions"].items():
            if entry.get("status") == "needs-reverification":
                runtime_differences.append({"kind": "harness_version_mismatch", "harness": product, "detail": entry})

    contract_differences = list(callable_differences)
    if cli_result == "mismatch":
        contract_differences.extend(
            problem for problem in (observed.get("cli") or {}).get("problems", [])
        )
    runtime_only = [problem for problem in (observed.get("cli") or {}).get("problems", [])
                    if problem.get("severity") == "needs_reverification"]

    version_supported = installed.get("version") == supported.get("version")
    version_differences: list[dict] = []
    if not version_supported:
        version_differences.append(
            {"kind": "version_not_supported", "installed": installed.get("version"),
             "supported": supported.get("version"),
             "note": "Run --observe to record the drift; do not edit expectations without evidence."}
        )
    if environment.get("python_minor") not in supported.get("python_minors", []):
        version_differences.append({"kind": "python_minor_not_supported", "observed": environment.get("python_minor"),
                                    "supported": supported.get("python_minors", [])})
    if environment.get("platform") not in supported.get("platforms", []):
        version_differences.append({"kind": "platform_not_supported", "observed": environment.get("platform"),
                                    "supported": supported.get("platforms", [])})

    if missing_required or any(item["kind"] == "cli_probe_failed" for item in contract_differences):
        contract_result = "mismatch"
    elif any(item["kind"] in {"signature_drift", "required_not_callable", "observed_only_not_callable",
                              "algorithm_choices_drift", "cli_commands_removed", "observed_signature_drift",
                              "doc_token_missing"} for item in contract_differences):
        contract_result = "mismatch" if any(
            item["kind"] in {"signature_drift", "required_not_callable", "observed_only_not_callable",
                             "algorithm_choices_drift", "cli_commands_removed", "observed_signature_drift"}
            for item in contract_differences
        ) else "needs_reverification"
    elif contract_differences:
        contract_result = "ok"
    else:
        contract_result = "ok"

    if runtime_differences or runtime_only or version_differences:
        runtime_result = "needs_reverification"
    elif not version_supported:
        runtime_result = "needs_reverification"
    else:
        runtime_result = "ok"

    if contract_result == "mismatch":
        result = "mismatch"
    elif contract_result == "needs_reverification" or runtime_result == "needs_reverification":
        result = "needs_reverification"
    else:
        result = "ok"

    return {
        "result": result,
        "contract_compatibility": {"result": contract_result, "differences": contract_differences,
                                   "evidence": contract_evidence, "missing_required": missing_required},
        "runtime_verification": {"result": runtime_result, "differences": runtime_differences + runtime_only
                                 + version_differences},
        "contract_evidence": contract_evidence,
        "supported_version": supported.get("version"),
        "differences": contract_differences + runtime_differences + runtime_only + version_differences,
        "missing_required": missing_required,
    }


def run_probe(repo: Path, mode: str) -> tuple[int, dict]:
    expectations = load_expectations(repo)
    supported = expectations["supported"]["openforage"]
    environment = probe_environment()
    report: dict = {
        "mode": mode,
        "expectations_file": EXPECTATIONS_REL,
        "environment": environment,
        "supported": supported,
        "differences": [],
        "result": "unknown",
    }

    try:
        version = _installed_version()
        importlib.import_module("openforage")
    except ImportError as exc:
        report["result"] = "not_installed"
        report["error"] = f"openforage is not importable under this interpreter: {exc}"
        return 2, report

    report["installed_version"] = version
    surface = expectations["expected_surface"]
    callables = {**surface.get("required_callables", {}), **surface.get("observed_only_callables", {})}
    observed = {
        "callables": {dotted: probe_callable(dotted) for dotted in callables},
        "cli": probe_cli(expectations),
        "example": probe_example(repo),
        "harness_versions": probe_harness_versions(expectations),
    }
    report["observed"] = observed

    comparison = compare_surface(
        surface,
        observed,
        {"version": version},
        environment,
        supported,
    )
    report.update(comparison)

    if mode == "observe":
        report["promise"] = "observation-only"
        report["note"] = "Observation only. It does not change the supported version or any verified harness entry."
        return 0, report

    if report["result"] == "ok":
        return 0, report
    if report["result"] == "mismatch":
        return 2, report
    return 3, report


# ---------------------------------------------------------------------------
# deterministic engine selfcheck
# ---------------------------------------------------------------------------

def _selfcheck_cli(commands, choices, default=None, exit_code=0):
    return {
        "commands": commands,
        "start_algorithm_choices": choices,
        "observed_default_algorithm": default,
        "probes": {"--help": {"exit_code": exit_code}, "start --help": {"exit_code": exit_code}},
    }


def selfcheck() -> int:
    failures: list[str] = []
    expected_surface = {
        "cli": {
            "commands": ["init", "start"],
            "start_algorithm_choices": ["random_weighted"],
            "documented_default_algorithm": {"value": "genetic"},
        },
        "required_callables": {
            "pkg.thing": {"signature_token": "n_jobs", "doc_token": "n_jobs"},
            "pkg.opaque": {"allow_opaque": True, "signature_token": "opaque_token", "allow_doc_only": True},
            "pkg.mustbeopaque": {"signature_token": "ctx"},
        },
        "observed_only_callables": {"pkg.helper": {"contract": "signature-observed-only",
                                                    "signature": "(prior, batch_size=100)"}},
    }
    good_env = {"python_minor": "3.12", "platform": "linux-x86_64"}
    supported = {"version": "1.0.0", "python_minors": ["3.12"], "platforms": ["linux-x86_64"]}
    good_observed = {
        "callables": {
            "pkg.thing": {"present": True, "callable": True, "signature": "(*, n_jobs=None)",
                          "opaque_proxy": False, "doc_signature": "thing(*, n_jobs=None)",
                          "introspection_evidence": "callable+signature"},
            "pkg.opaque": {"present": True, "callable": True, "signature": "(*args, ctx=None, **kwargs)",
                           "opaque_proxy": True, "doc_signature": "opaque(opaque_token)",
                           "introspection_evidence": "callable+signature"},
            "pkg.mustbeopaque": {"present": True, "callable": True, "signature": "(*args, ctx=None, **kwargs)",
                                 "opaque_proxy": True, "doc_signature": "mustbeopaque(ctx)",
                                 "introspection_evidence": "callable+signature"},
            "pkg.helper": {"present": True, "callable": True, "signature": "(prior, batch_size=100)",
                           "opaque_proxy": False, "introspection_evidence": "callable+signature"},
        },
        "cli": _selfcheck_cli(["init", "start"], ["random_weighted"], default="genetic"),
        "example": {"status": "ok"},
        "harness_versions": {"hx": {"status": "observed-unverified"}},
    }

    result = compare_surface(expected_surface, good_observed, {"version": "1.0.0"}, good_env, supported)
    if result["result"] != "ok":
        failures.append(f"matching surface did not pass: {result['differences']}")
    if not result["contract_evidence"]:
        failures.append("matching surface recorded no informational contract evidence")
    if any(item.get("kind") == "contract_evidence" for item in result["differences"]):
        failures.append("informational contract_evidence rows leaked into genuine differences")

    missing = json.loads(json.dumps(good_observed))
    missing["callables"]["pkg.thing"] = {"present": False, "callable": False}
    result = compare_surface(expected_surface, missing, {"version": "1.0.0"}, good_env, supported)
    if result["result"] != "mismatch" or "pkg.thing" not in result["missing_required"]:
        failures.append("missing required callable was not a mismatch")

    not_callable = json.loads(json.dumps(good_observed))
    not_callable["callables"]["pkg.thing"] = {"present": True, "callable": False, "signature": "", "introspection_evidence": "attribute-only"}
    result = compare_surface(expected_surface, not_callable, {"version": "1.0.0"}, good_env, supported)
    if result["result"] != "mismatch" or not any(item["kind"] == "required_not_callable" for item in result["differences"]):
        failures.append("non-callable required attribute was not a mismatch")

    opaque = json.loads(json.dumps(good_observed))
    opaque["callables"]["pkg.mustbeopaque"] = {"present": True, "callable": True, "signature": "(*args, **kwargs)",
                                               "opaque_proxy": True, "doc_signature": "mustbeopaque(ctx)",
                                               "introspection_evidence": "callable+signature"}
    result = compare_surface(expected_surface, opaque, {"version": "1.0.0"}, good_env, supported)
    if result["result"] != "mismatch" or not any(item["kind"] == "opaque_callable_limitation" for item in result["differences"]):
        failures.append("opaque callable without an allowed doc-only contract was not a mismatch")
    allowed_checks = [item for item in result["contract_evidence"] if item.get("call") == "pkg.opaque"]
    if not any(item.get("contract_evidence") == "doc-only" for item in allowed_checks):
        failures.append("doc-only contract evidence was not recorded separately for an allowed opaque callable")

    additions = json.loads(json.dumps(good_observed))
    additions["cli"] = _selfcheck_cli(["init", "start", "earnings"], ["random_weighted"], default="genetic")
    result = compare_surface(expected_surface, additions, {"version": "1.0.0"}, good_env, supported)
    if result["result"] != "needs_reverification" or not any(item["kind"] == "cli_commands_added" for item in result["differences"]):
        failures.append("CLI command addition was not reported for re-verification")

    removals = json.loads(json.dumps(good_observed))
    removals["cli"] = _selfcheck_cli(["init"], ["random_weighted"], default="genetic")
    result = compare_surface(expected_surface, removals, {"version": "1.0.0"}, good_env, supported)
    if result["result"] != "mismatch" or not any(item["kind"] == "cli_commands_removed" for item in result["differences"]):
        failures.append("CLI command removal was not a mismatch")

    choices = json.loads(json.dumps(good_observed))
    choices["cli"] = _selfcheck_cli(["init", "start"], ["random_weighted", "genetic"], default="genetic")
    result = compare_surface(expected_surface, choices, {"version": "1.0.0"}, good_env, supported)
    if result["result"] != "needs_reverification" or not any(item["kind"] == "algorithm_choices_added" for item in result["differences"]):
        failures.append("algorithm choice addition was not reported for re-verification")

    default_drift = json.loads(json.dumps(good_observed))
    default_drift["cli"] = _selfcheck_cli(["init", "start"], ["random_weighted"], default="random_weighted")
    result = compare_surface(expected_surface, default_drift, {"version": "1.0.0"}, good_env, supported)
    if result["result"] != "needs_reverification" or not any(
        item["kind"] == "documented_cli_default_discrepancy" for item in result["differences"]
    ):
        failures.append("documented-versus-CLI default discrepancy was not reported")

    cli_failed = json.loads(json.dumps(good_observed))
    cli_failed["cli"] = _selfcheck_cli(None, None, exit_code=2)
    result = compare_surface(expected_surface, cli_failed, {"version": "1.0.0"}, good_env, supported)
    if result["result"] != "mismatch":
        failures.append("failed CLI probe was not a mismatch")

    probe_failed = json.loads(json.dumps(good_observed))
    probe_failed["example"] = {"status": "failed", "timed_out": True}
    result = compare_surface(expected_surface, probe_failed, {"version": "1.0.0"}, good_env, supported)
    if result["result"] != "needs_reverification" or not any(
        item["kind"] == "required_runtime_probe_failed" for item in result["differences"]
    ):
        failures.append("timed-out required runtime probe was not a failure")

    harness_drift = json.loads(json.dumps(good_observed))
    harness_drift["harness_versions"] = {"hx": {"status": "needs-reverification", "available": True}}
    result = compare_surface(expected_surface, harness_drift, {"version": "1.0.0"}, good_env, supported)
    if result["result"] != "needs_reverification" or not any(
        item["kind"] == "harness_version_mismatch" for item in result["differences"]
    ):
        failures.append("harness version mismatch did not yield needs_reverification")

    harness_absent = json.loads(json.dumps(good_observed))
    harness_absent["harness_versions"] = {"hx": {"status": "not-applicable", "available": False}}
    result = compare_surface(expected_surface, harness_absent, {"version": "1.0.0"}, good_env, supported)
    if result["result"] != "ok":
        failures.append("an absent harness (not-applicable) incorrectly affected the result")

    drifted = json.loads(json.dumps(good_observed))
    drifted["callables"]["pkg.helper"] = {"present": True, "callable": True, "signature": "(prior)",
                                          "opaque_proxy": False, "introspection_evidence": "callable+signature"}
    result = compare_surface(expected_surface, drifted, {"version": "1.0.0"}, good_env, supported)
    if not any(item["kind"] == "observed_signature_drift" for item in result["differences"]):
        failures.append("observed-only signature drift was not recorded")

    version_drift = compare_surface(expected_surface, good_observed, {"version": "9.9.9"}, good_env, supported)
    if version_drift["result"] != "needs_reverification" or not any(
        item["kind"] == "version_not_supported" for item in version_drift["differences"]
    ):
        failures.append("unsupported version was not reported for re-verification")

    if failures:
        for failure in failures:
            print(f"FAIL: selfcheck: {failure}")
        print(f"\n{len(failures)} selfcheck failure(s)")
        return 1
    print("note: selfcheck: 15 compatibility-engine checks behaved as expected")
    print("ok")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=str(REPO_DEFAULT))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="strict comparison to the tested-supported version")
    mode.add_argument("--observe", action="store_true", help="record the installed surface as an observation only")
    mode.add_argument("--selfcheck", action="store_true", help="deterministic probe-engine regression")
    parser.add_argument("--json-out", default=None)
    args = parser.parse_args(argv)

    if args.selfcheck:
        return selfcheck()

    repo = Path(args.repo).resolve()
    mode_name = "observe" if args.observe else "check"
    try:
        status, report = run_probe(repo, mode_name)
    except ProbeError as exc:
        print(f"FAIL: {exc}")
        return 2
    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return status


if __name__ == "__main__":
    sys.exit(main())
