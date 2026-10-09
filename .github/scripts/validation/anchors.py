"""Relative link and internal anchor validation for Markdown files."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

from . import common
from .common import GitIgnoreError, git_ignored_paths, iter_files, read_text, target_safety

LINK_PATTERN = re.compile(r"\[[^\]]*\]\(([^)]*)\)")
HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
HTML_ANCHOR_PATTERN = re.compile(r"<a\s+(?:id|name)=[\"']([^\"']+)[\"']", re.IGNORECASE)
_TITLE_PATTERN = re.compile(r'''(?:"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|\((?:[^()\\]|\\.)*\))''')
_BAD_ESCAPE_PATTERN = re.compile(r"%(?![0-9A-Fa-f]{2})")
_EXTERNAL_SCHEMES = {"http", "https", "mailto", "tel"}


def github_slug(heading: str) -> str:
    """Approximate GitHub's heading anchor algorithm."""
    text = heading.strip()
    text = re.sub(r"`", "", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[*_~]", "", text)
    text = text.lower()
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"\s+", "-", text)
    return text


def collect_anchors(path: Path) -> set[str]:
    anchors: set[str] = set()
    text = read_text(path)
    for line in text.splitlines():
        heading = HEADING_PATTERN.match(line)
        if heading:
            slug = github_slug(heading.group(2))
            if slug:
                anchors.add(slug)
        for explicit in HTML_ANCHOR_PATTERN.findall(line):
            anchors.add(explicit)
    return anchors


def _destination(value: str) -> tuple[str | None, str | None]:
    """Split an inline link's destination and optional title conservatively."""
    inside = value.strip()
    if not inside:
        return "", None
    if inside.startswith("<"):
        close = inside.find(">")
        if close < 0:
            return None, "unsupported or ambiguous Markdown destination syntax"
        destination = inside[1:close]
        remainder = inside[close + 1 :].strip()
        if "<" in destination or ">" in destination or (remainder and not _TITLE_PATTERN.fullmatch(remainder)):
            return None, "unsupported or ambiguous Markdown destination syntax"
        return destination, None
    if "<" in inside or ">" in inside:
        return None, "unsupported or ambiguous Markdown destination syntax"
    pieces = inside.split(None, 1)
    destination = pieces[0]
    if len(pieces) == 2 and not _TITLE_PATTERN.fullmatch(pieces[1].strip()):
        return None, "unsupported or ambiguous Markdown destination syntax"
    return destination, None


def _local_parts(raw: str) -> tuple[str | None, str, str | None]:
    """Return (decoded path, decoded fragment, problem); external URIs are separate."""
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return None, "", "unsupported or ambiguous Markdown destination syntax"
    if parsed.scheme.casefold() in _EXTERNAL_SCHEMES or (not parsed.scheme and parsed.netloc):
        return None, "", "external"
    if parsed.scheme:
        return None, "", "unsupported or ambiguous Markdown destination syntax"
    if _BAD_ESCAPE_PATTERN.search(parsed.path) or _BAD_ESCAPE_PATTERN.search(parsed.fragment):
        return None, "", "unsupported or ambiguous Markdown destination syntax"
    try:
        path = unquote(parsed.path, errors="strict")
        fragment = unquote(parsed.fragment, errors="strict").split("?", 1)[0]
    except (UnicodeError, ValueError):
        return None, "", "unsupported or ambiguous Markdown destination syntax"
    if "\\" in path or any(ord(char) < 32 or ord(char) == 127 for char in path):
        return None, "", "unsupported or ambiguous Markdown destination syntax"
    if "\x00" in path:
        return None, "", "unsupported or ambiguous Markdown destination syntax"
    return path, fragment, None


def check_links(report, root: Path) -> None:
    root = Path(root).resolve()
    if iter_files is common.iter_files:
        path_inventory = common.repo_path_inventory(root)
        file_inventory = [
            root / relative
            for relative in path_inventory
            if not (root / relative).is_symlink() and (root / relative).is_file()
        ]
        inventory = set(path_inventory)
    else:
        # The completion checker deliberately replaces this iterator with its
        # disclosed-input view; keep that scope and derive its ancestor dirs.
        file_inventory = list(iter_files(root))
        inventory = set()
        for path in file_inventory:
            relative = path.relative_to(root).as_posix()
            inventory.add(relative)
            parent = Path(relative).parent
            while parent != Path("."):
                inventory.add(parent.as_posix())
                parent = parent.parent
    try:
        ignored = git_ignored_paths(root, inventory)
    except GitIgnoreError as exc:
        report.fail(f"links: {exc}")
        return

    markdown_files = []
    for path in file_inventory:
        relative = path.relative_to(root).as_posix()
        if relative not in ignored and path.suffix.casefold() == ".md":
            markdown_files.append((relative, path))

    for relative, path in markdown_files:
        text = read_text(path)
        for line_no, line in enumerate(text.splitlines(), start=1):
            for match in LINK_PATTERN.finditer(line):
                destination, problem = _destination(match.group(1))
                if problem:
                    report.fail(f"{relative}:{line_no}: {problem}")
                    continue
                raw = destination or ""
                decoded, fragment, problem = _local_parts(raw)
                if problem == "external":
                    continue
                if problem:
                    report.fail(f"{relative}:{line_no}: {problem}")
                    continue
                if decoded is None:
                    continue
                if not decoded:
                    candidate = path
                elif decoded.startswith("/"):
                    candidate = root / decoded.lstrip("/")
                else:
                    candidate = path.parent / decoded

                problem, safe_candidate = target_safety(root, candidate, ignored)
                if problem:
                    report.fail(f"{relative}:{line_no}: {problem}")
                    continue
                if fragment and safe_candidate is not None and safe_candidate.suffix.casefold() == ".md":
                    anchors = collect_anchors(safe_candidate)
                    if fragment not in anchors:
                        report.fail(
                            f"{relative}:{line_no}: internal anchor missing in a portable Markdown target: {fragment}"
                        )
