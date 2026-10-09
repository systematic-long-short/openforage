"""Timeout-bounded subprocess helpers that only signal runner-owned groups."""

from __future__ import annotations

import os
import signal
import subprocess
from dataclasses import dataclass


@dataclass
class ProcessResult:
    argv: list[str]
    returncode: int | None
    stdout: bytes
    stderr: bytes
    timed_out: bool
    process_group_remaining: bool
    process_group_id: int
    cleanup_attempted: bool


def group_alive(process_group: int) -> bool:
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def terminate_owned_group(process_group: int, grace_seconds: float = 2.0) -> bool:
    """Terminate only a process group created by this runner, then report liveness."""
    if not group_alive(process_group):
        return False
    try:
        os.killpg(process_group, signal.SIGTERM)
    except ProcessLookupError:
        return False
    try:
        import time
        deadline = time.monotonic() + grace_seconds
        while time.monotonic() < deadline and group_alive(process_group):
            time.sleep(0.05)
    finally:
        if group_alive(process_group):
            try:
                os.killpg(process_group, signal.SIGKILL)
            except ProcessLookupError:
                pass
    return group_alive(process_group)


def run_bounded(argv: list[str], *, cwd, env=None, timeout: float) -> ProcessResult:
    """Run argv without a shell; timeout cleanup is scoped to its new session."""
    process = subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    timed_out = False
    cleanup_attempted = False
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as first_timeout:
        timed_out = True
        cleanup_attempted = True
        terminate_owned_group(process.pid, grace_seconds=2.0)
        try:
            stdout, stderr = process.communicate(timeout=2.0)
        except subprocess.TimeoutExpired as second_timeout:
            # The group was created by this call; only its leader is reaped here.
            terminate_owned_group(process.pid, grace_seconds=0.0)
            stdout = second_timeout.output or first_timeout.output or b""
            stderr = second_timeout.stderr or first_timeout.stderr or b""
    if process.poll() is not None and group_alive(process.pid):
        cleanup_attempted = True
        terminate_owned_group(process.pid, grace_seconds=1.0)
    return ProcessResult(
        argv=list(argv),
        returncode=process.poll(),
        stdout=stdout or b"",
        stderr=stderr or b"",
        timed_out=timed_out,
        process_group_remaining=group_alive(process.pid),
        process_group_id=process.pid,
        cleanup_attempted=cleanup_attempted,
    )
