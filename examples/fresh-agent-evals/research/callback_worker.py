#!/usr/bin/env python3
"""Bounded JSON transport for one callback execution; never a native runner."""

from __future__ import annotations

import argparse
import ast
import contextlib
import hashlib
import importlib.util
import inspect
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from contract import FixtureContext  # noqa: E402

MAX_CALLBACK_YIELDS = 64
MAX_PAYLOAD_BYTES = 4_096


def _subject_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _local_callback_source(path: Path) -> tuple[ast.Module, str]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("callback source is missing or a symlink")
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=path.name)
    functions = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "challenger"
    ]
    if len(functions) != 1 or not isinstance(functions[0], ast.FunctionDef):
        raise ValueError("callback must define one local synchronous challenger(ctx) function")
    callback = functions[0]
    if (
        len(callback.args.posonlyargs) != 0
        or len(callback.args.args) != 1
        or callback.args.vararg is not None
        or callback.args.kwarg is not None
        or callback.args.kwonlyargs
        or callback.args.defaults
        or callback.args.kw_defaults
        or callback.args.args[0].arg != "ctx"
    ):
        raise ValueError("challenger must have exactly one required positional parameter named ctx")
    if any(
        (
            isinstance(node, ast.Import)
            and any(alias.name.split(".", 1)[0] == "coverage_challenger" for alias in node.names)
        )
        or (
            isinstance(node, ast.ImportFrom)
            and isinstance(node.module, str)
            and node.module.split(".", 1)[0] == "coverage_challenger"
        )
        for node in ast.walk(tree)
    ):
        raise ValueError("the bundled coverage_challenger example cannot be imported as the contribution")
    return tree, source


def _json_transport(value: object) -> dict:
    try:
        raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, RecursionError, UnicodeEncodeError):
        return {"transport_status": "not_json_compatible"}
    if len(raw) > MAX_PAYLOAD_BYTES:
        return {"transport_status": "payload_too_large"}
    try:
        copied = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"transport_status": "invalid_json_encoding"}
    return {"transport_status": "ok", "payload": copied}


def _worker_result(subject: Path, fixture: dict, input_id: str, seed: int) -> dict:
    try:
        _tree, _source = _local_callback_source(subject)
        before = _subject_digest(subject)
        spec = importlib.util.spec_from_file_location("fresh_research_subject", subject)
        if spec is None or spec.loader is None:
            raise ValueError("callback module could not be loaded")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        # The callback sees only its context through the declared function call;
        # normal Python execution is still instruction-isolated, not an OS sandbox.
        original_argv = sys.argv
        sys.argv = [subject.name]
        stdout_fd, stderr_fd = os.dup(1), os.dup(2)
        try:
            with open(os.devnull, "w", encoding="utf-8") as sink:
                os.dup2(sink.fileno(), 1)
                os.dup2(sink.fileno(), 2)
                with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                    spec.loader.exec_module(module)
                    callback = getattr(module, "challenger", None)
                    if not callable(callback):
                        raise ValueError("local challenger function is not callable")
                    if not inspect.isgeneratorfunction(callback):
                        raise ValueError("challenger(ctx) must be a generator function that yields payloads")
                    context = FixtureContext(fixture, seed)
                    generated = callback(context)
                    if not inspect.isgenerator(generated):
                        raise ValueError("challenger(ctx) did not return an iterator of graph payloads")
                    payloads = []
                    exhausted = False
                    limit_reached = False
                    for index in range(MAX_CALLBACK_YIELDS + 1):
                        try:
                            value = next(generated)
                        except StopIteration:
                            exhausted = True
                            break
                        if index == MAX_CALLBACK_YIELDS:
                            limit_reached = True
                            break
                        payloads.append(_json_transport(value))
        finally:
            os.dup2(stdout_fd, 1)
            os.dup2(stderr_fd, 2)
            os.close(stdout_fd)
            os.close(stderr_fd)
            sys.argv = original_argv
        after = _subject_digest(subject)
        if before != after:
            raise ValueError("callback source changed during execution")
        return {
            "worker_schema_version": 1,
            "execution_status": "completed",
            "subject_sha256": before,
            "payloads": payloads,
            "captured_yield_count": len(payloads),
            "yield_stream_exhausted": exhausted,
            "yield_limit_reached": limit_reached,
            "yield_count_is_lower_bound": limit_reached,
            "yield_count_lower_bound": MAX_CALLBACK_YIELDS + 1 if limit_reached else len(payloads),
        }
    except BaseException as exc:  # noqa: BLE001 - report only safe exception type
        return {
            "worker_schema_version": 1,
            "execution_status": "callback_error",
            "error_type": type(exc).__name__,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subject", required=True)
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--input-id", required=True)
    parser.add_argument("--seed", required=True, type=int)
    args = parser.parse_args(argv)
    subject = Path(args.subject)
    fixture_path = Path(args.fixture)
    try:
        if subject.is_symlink() or fixture_path.is_symlink():
            raise ValueError("subject and fixture paths may not be symlinks")
        fixture_data = json.loads(fixture_path.read_text(encoding="utf-8"))
        selected = next(
            item for item in fixture_data["inputs"]
            if item.get("input_id") == args.input_id
        )
        if args.seed not in selected["paired_seeds"]:
            raise ValueError("seed is not declared for the selected fixture input")
        payload = _worker_result(subject, selected, args.input_id, args.seed)
    except BaseException as exc:  # noqa: BLE001 - keep transport diagnostics sanitized
        payload = {
            "worker_schema_version": 1,
            "execution_status": "worker_error",
            "error_type": type(exc).__name__,
        }
    sys.stdout.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
