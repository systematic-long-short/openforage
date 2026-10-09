"""Dependency-free typed checks for research artifacts and portable records."""

from __future__ import annotations

import re
from pathlib import Path

SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
RESULT_FIELDS = {
    "schema_version", "task_id", "source", "research", "machine_claims", "human_review",
}
SOURCE_FIELDS = {
    "source_excerpt_path", "source_excerpt_sha256", "title", "authors", "year",
    "identifier", "url", "paraphrase",
}
RESEARCH_FIELDS = {"mechanism_explanation", "hypothesis", "falsifier"}
CLAIM_FIELDS = {
    "evaluation_scope", "comparison_report_path", "comparison_report_sha256",
    "implementation_path", "implementation_sha256", "series_id", "series_manifest_path",
    "series_manifest_sha256", "reference_identity", "synthetic_uniform_is_genetic",
    "synthetic_quality_scope", "synthetic_comparison_interpretation",
    "live_performance", "out_of_sample_return_correlation", "server_acceptance",
    "genetic_reference_target",
}
HUMAN_REVIEW_FIELDS = {"summary", "rationale"}


def _exact_object(value: object, fields: set[str], where: str, problems: list[str]) -> bool:
    if not isinstance(value, dict):
        problems.append(f"{where} must be an object")
        return False
    if set(value) != fields:
        problems.append(f"{where} fields do not match the typed schema")
        return False
    return True


def _nonempty_text(value: object, where: str, problems: list[str], *, limit: int = 4000) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        problems.append(f"{where} must be non-empty bounded text")


def _sha(value: object, where: str, problems: list[str]) -> None:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        problems.append(f"{where} must be a lowercase SHA-256 digest")


def _safe_relative(value: object, where: str, problems: list[str]) -> None:
    if not isinstance(value, str):
        problems.append(f"{where} must be a workspace-relative path")
        return
    path = Path(value)
    if (
        not value or path.is_absolute() or "\\" in value
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.as_posix() != value
    ):
        problems.append(f"{where} must be a safe workspace-relative path")


def validate_research_result(value: object) -> list[str]:
    """Validate shape and primitive types; prose is never quality-graded."""
    problems: list[str] = []
    if not _exact_object(value, RESULT_FIELDS, "research result", problems):
        return problems
    if type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        problems.append("schema_version must be integer 1")
    if value.get("task_id") != "paper-backed-coverage-challenger-v1":
        problems.append("task_id does not match the research contribution task")

    source = value.get("source")
    if _exact_object(source, SOURCE_FIELDS, "source", problems):
        _safe_relative(source["source_excerpt_path"], "source.source_excerpt_path", problems)
        _sha(source["source_excerpt_sha256"], "source.source_excerpt_sha256", problems)
        for key in ("title", "identifier", "url"):
            _nonempty_text(source[key], f"source.{key}", problems, limit=512)
        authors = source["authors"]
        if not isinstance(authors, list) or not authors or any(not isinstance(item, str) or not item.strip() for item in authors):
            problems.append("source.authors must be a non-empty string array")
        if type(source["year"]) is not int or not 1900 <= source["year"] <= 2100:
            problems.append("source.year must be an integer year")
        _nonempty_text(source["paraphrase"], "source.paraphrase", problems, limit=500)

    research = value.get("research")
    if _exact_object(research, RESEARCH_FIELDS, "research", problems):
        for key in sorted(RESEARCH_FIELDS):
            _nonempty_text(research[key], f"research.{key}", problems, limit=2000)

    claims = value.get("machine_claims")
    if _exact_object(claims, CLAIM_FIELDS, "machine_claims", problems):
        for key in (
            "comparison_report_path", "implementation_path", "series_manifest_path",
        ):
            _safe_relative(claims[key], f"machine_claims.{key}", problems)
        if not isinstance(claims["series_id"], str) or re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,63}", claims["series_id"]) is None:
            problems.append("machine_claims.series_id must be a frozen safe series identity")
        for key in (
            "comparison_report_sha256", "implementation_sha256", "series_manifest_sha256",
        ):
            _sha(claims[key], f"machine_claims.{key}", problems)
        if claims["evaluation_scope"] != "synthetic_fixture_only":
            problems.append("machine_claims.evaluation_scope must remain synthetic_fixture_only")
        if claims["reference_identity"] != "synthetic_uniform_pair_selector_v1":
            problems.append("machine_claims.reference_identity must name the synthetic uniform fixture")
        if type(claims["synthetic_uniform_is_genetic"]) is not bool or claims["synthetic_uniform_is_genetic"] is not False:
            problems.append("the synthetic uniform reference must not be claimed as genetic")
        if claims["synthetic_quality_scope"] != "fixture_only":
            problems.append("synthetic_quality_scope must be fixture_only")
        if claims["synthetic_comparison_interpretation"] != "descriptive_only":
            problems.append("synthetic_comparison_interpretation must be descriptive_only")
        for key in ("live_performance", "out_of_sample_return_correlation", "server_acceptance"):
            observation = claims[key]
            if (
                not isinstance(observation, dict)
                or set(observation) != {"status", "value"}
                or observation.get("status") != "unknown"
                or observation.get("value") is not None
            ):
                problems.append(f"machine_claims.{key} must be null with status unknown")
        target = claims["genetic_reference_target"]
        if (
            not isinstance(target, dict)
            or set(target) != {
                "algorithm", "target_completed_simulations", "observed_completed_simulations", "status",
            }
            or target.get("algorithm") != "genetic"
            or type(target.get("target_completed_simulations")) is not int
            or target.get("target_completed_simulations") != 100_000
            or target.get("observed_completed_simulations") is not None
            or target.get("status") != "unmeasured_target"
        ):
            problems.append("genetic_reference_target must preserve the separate unmeasured 100000 target")

    review = value.get("human_review")
    if _exact_object(review, HUMAN_REVIEW_FIELDS, "human_review", problems):
        _nonempty_text(review["summary"], "human_review.summary", problems, limit=2000)
        _nonempty_text(review["rationale"], "human_review.rationale", problems, limit=4000)
    return problems


def validate_trial_record(value: object) -> list[str]:
    """Validate the portable native record's top-level identity and digests."""
    problems: list[str] = []
    required = {
        "record_schema_version", "series_id", "series_manifest_path",
        "series_manifest_sha256", "case_id", "harness", "execution_mode",
        "fresh_native_invocation", "run_id", "workspace_identity_sha256",
        "snapshot_sha256", "initial_file_sha256", "native_exit_code",
        "native_timed_out", "process_group_remaining", "native_session_id_sha256",
        "native_trace_complete", "native_actions", "raw_events_path",
        "raw_events_sha256", "raw_events_bytes", "raw_stderr_path",
        "raw_stderr_sha256", "raw_stderr_bytes", "artifact_sha256",
        "expected_snapshot_sha256",
    }
    if not _exact_object(value, required, "trial record", problems):
        return problems
    if type(value.get("record_schema_version")) is not int or value["record_schema_version"] != 1:
        problems.append("record_schema_version must be integer 1")
    if value.get("case_id") != "paper-backed-coverage-challenger":
        problems.append("case_id is not the research challenger case")
    if value.get("harness") not in {"opencode", "codex"}:
        problems.append("harness is not an admitted explicit native adapter")
    if value.get("execution_mode") != "fresh-native-run" or value.get("fresh_native_invocation") is not True:
        problems.append("trial record does not identify a fresh native invocation")
    for key in (
        "workspace_identity_sha256", "snapshot_sha256", "series_manifest_sha256",
        "raw_events_sha256", "raw_stderr_sha256",
    ):
        _sha(value[key], f"trial record.{key}", problems)
    for key in ("series_manifest_path", "raw_events_path", "raw_stderr_path"):
        _safe_relative(value[key], f"trial record.{key}", problems)
    if not isinstance(value["initial_file_sha256"], dict):
        problems.append("initial_file_sha256 must be an object")
    if not isinstance(value["native_actions"], list):
        problems.append("native_actions must be an array")
    for key in ("native_timed_out", "process_group_remaining", "native_trace_complete"):
        if type(value[key]) is not bool:
            problems.append(f"trial record.{key} must be a boolean")
    for key in ("raw_events_bytes", "raw_stderr_bytes"):
        if type(value[key]) is not int or value[key] < 0:
            problems.append(f"trial record.{key} must be a non-negative integer")
    if value["native_exit_code"] is not None and type(value["native_exit_code"]) is not int:
        problems.append("native_exit_code must be an integer or null")
    if value["native_session_id_sha256"] is not None:
        _sha(value["native_session_id_sha256"], "native_session_id_sha256", problems)
    if value["expected_snapshot_sha256"] is not None:
        _sha(value["expected_snapshot_sha256"], "expected_snapshot_sha256", problems)
    artifacts = value["artifact_sha256"]
    if not isinstance(artifacts, dict) or any(
        not isinstance(path, str) or not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None
        for path, digest in artifacts.items()
    ):
        problems.append("artifact_sha256 must map paths to SHA-256 digests")
    return problems
