"""Narrow fixture-correction contracts; no English keyword grading."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from urllib.parse import urlsplit

from paths import safe_relative
from schema import read_json

FENCE_OPEN = re.compile(r"^ {0,3}(`{3,}|~{3,})([^\r\n]*)\s*(?:\r?\n)?$")
LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


class SemanticCheckError(RuntimeError):
    """The finished page no longer satisfies its bounded fixture contract."""


def _markdown_parts(text: str) -> tuple[list[dict], list[tuple[str, object]]]:
    lines = text.splitlines(keepends=True)
    blocks: list[dict] = []
    outside: list[tuple[str, object]] = []
    index = 0
    while index < len(lines):
        opening = FENCE_OPEN.match(lines[index])
        if not opening:
            outside.append(("line", lines[index]))
            index += 1
            continue
        fence = opening.group(1)
        info = opening.group(2).strip()
        body: list[str] = []
        index += 1
        close_index = None
        close_re = re.compile(rf"^ {{0,3}}{re.escape(fence)}[ \t]*(?:\r?\n)?$")
        while index < len(lines):
            if close_re.match(lines[index]):
                close_index = index
                break
            body.append(lines[index])
            index += 1
        if close_index is None:
            raise SemanticCheckError("unclosed Markdown code fence")
        block_index = len(blocks)
        blocks.append({"fence": fence, "info": info, "body": "".join(body)})
        outside.append(("block", (fence, info, block_index)))
        index = close_index + 1
    return blocks, outside


def _is_subsequence(needle: list[tuple[str, object]], haystack: list[tuple[str, object]]) -> bool:
    cursor = 0
    for item in haystack:
        if cursor < len(needle) and needle[cursor] == item:
            cursor += 1
    return cursor == len(needle)


def _ast_signature(source: str):
    return ast.dump(ast.parse(source), annotate_fields=True, include_attributes=False)


def _language(info: str) -> str:
    parts = info.split(maxsplit=1)
    return parts[0].casefold() if parts else ""


def canonical_primary_seed_source(text: str, fixture: dict) -> str | None:
    """Reconstruct the canonical clean seed in an offline selfcheck copy only."""
    find, replace = fixture.get("find"), fixture.get("replace")
    if not isinstance(find, str) or not isinstance(replace, str):
        return None
    if text.count(find) == 1:
        return text
    if text.count(replace) == 1:
        return text.replace(replace, find, 1)
    lines = text.splitlines(keepends=True)
    byte_offsets = [0]
    for line in lines:
        byte_offsets.append(byte_offsets[-1] + len(line.encode("utf-8")))
    matches = []
    index = 0
    while index < len(lines):
        opening = FENCE_OPEN.match(lines[index])
        if not opening:
            index += 1
            continue
        fence, info = opening.group(1), opening.group(2).strip()
        body_start = index + 1
        closing = re.compile(rf"^ {{0,3}}{re.escape(fence)}[ \t]*(?:\r?\n)?$")
        body_end = body_start
        while body_end < len(lines) and not closing.match(lines[body_end]):
            body_end += 1
        if body_end >= len(lines):
            return None
        body = "".join(lines[body_start:body_end])
        if _language(info) in {"python", "py"}:
            try:
                tree = ast.parse(body)
            except SyntaxError:
                tree = None
            if tree is not None:
                body_lines = body.splitlines(keepends=True)
                body_offsets = [0]
                for body_line in body_lines:
                    body_offsets.append(body_offsets[-1] + len(body_line.encode("utf-8")))
                body_bytes = body.encode("utf-8")
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                        continue
                    if not isinstance(node.func.value, ast.Name) or node.func.value.id != "openforage" or node.func.attr != "register":
                        continue
                    invite = next((item.value for item in node.keywords if item.arg == "invite_code"), None)
                    if not isinstance(invite, ast.Constant) or invite.value != "<CODE>" or node.end_lineno is None:
                        continue
                    call_start = body_offsets[node.lineno - 1] + node.col_offset
                    call_end = body_offsets[node.end_lineno - 1] + node.end_col_offset
                    canonical_body = body_bytes[:call_start] + find.encode("utf-8") + body_bytes[call_end:]
                    matches.append((byte_offsets[body_start], byte_offsets[body_end], canonical_body))
        index = body_end + 1
    if len(matches) != 1:
        return None
    start_offset, end_offset, body_bytes = matches[0]
    whole = text.encode("utf-8")
    return (whole[:start_offset] + body_bytes + whole[end_offset:]).decode("utf-8")


def _python_example_problem(clean: str, final: str, fixture: dict) -> str | None:
    clean_blocks, clean_outside = _markdown_parts(clean)
    final_blocks, final_outside = _markdown_parts(final)
    if len(clean_blocks) != len(final_blocks):
        return "Python example/fence inventory changed"
    markers = [
        index for index, block in enumerate(clean_blocks)
        if _language(block["info"]) in {"python", "py"}
        and block["body"].count(fixture["find"]) == 1
    ]
    if len(markers) != 1:
        return "clean source does not identify exactly one seeded Python example"
    target_index = markers[0]
    for index, (before, after) in enumerate(zip(clean_blocks, final_blocks)):
        if before["fence"] != after["fence"] or before["info"] != after["info"]:
            return "Markdown code-fence structure changed"
        if index == target_index:
            try:
                if _ast_signature(before["body"]) != _ast_signature(after["body"]):
                    return "corrected Python example does not preserve the clean source semantics"
            except SyntaxError:
                return "corrected Python example is not syntactically valid"
        elif before["body"] != after["body"]:
            return "an unrelated code example changed"
    if not _is_subsequence(clean_outside, final_outside):
        return "surrounding documentation was removed or rewritten"
    return None


def _link_problem(clean: str, final: str, fixture: dict, workspace: Path) -> str | None:
    fragment = fixture.get("find")
    if not isinstance(fragment, str) or clean.count(fragment) != 1:
        return "clean source does not identify one held-out link context"
    source_link = LINK_RE.search(fragment)
    if not source_link:
        return "held-out fixture does not contain a Markdown link"
    prefix, suffix = fragment[:source_link.start()], fragment[source_link.end():]
    label, canonical_href = source_link.groups()
    pattern = re.compile(re.escape(prefix) + r"\[" + re.escape(label) + r"\]\(([^)\s]+)\)" + re.escape(suffix))
    matches = list(pattern.finditer(final))
    if len(matches) != 1:
        return "held-out documentation link was removed or its source context changed"
    href = matches[0].group(1)
    parsed = urlsplit(href)
    if parsed.scheme or parsed.netloc or parsed.path.startswith("/") or "\\" in parsed.path or ".." in Path(parsed.path).parts:
        return "held-out replacement link is not a safe relative link"
    source_path = safe_relative(fixture["path"])
    candidate = (workspace / source_path).parent.joinpath(parsed.path).resolve()
    if not candidate.is_relative_to(workspace.resolve()) or not candidate.is_file():
        return "held-out replacement link target does not exist inside the workspace"
    canonical = (workspace / source_path).parent.joinpath(urlsplit(canonical_href).path).resolve()
    if candidate != canonical:
        return "held-out replacement link no longer identifies the canonical compatibility record"
    clean_normalized = clean.replace(fragment, "<HELDOUT-LINK>", 1)
    final_normalized = final[:matches[0].start()] + "<HELDOUT-LINK>" + final[matches[0].end():]
    clean_blocks, clean_outside = _markdown_parts(clean_normalized)
    final_blocks, final_outside = _markdown_parts(final_normalized)
    if clean_blocks != final_blocks or not _is_subsequence(clean_outside, final_outside):
        return "held-out page changed outside the single bounded link repair"
    return None


def fixture_correction_problem(repo: Path, workspace: Path, case_id: str) -> str | None:
    """Accept semantically equivalent primary code and bounded held-out link fixes."""
    oracle = read_json(repo / "examples/fresh-agent-evals/v2/oracle.json")
    fixture = oracle["cases"][case_id]["fixture"]
    relative = safe_relative(fixture["path"])
    source, final = repo / relative, workspace / relative
    if source.is_symlink() or final.is_symlink() or not source.is_file() or not final.is_file():
        return "corrected documentation source is missing or unsafe"
    try:
        clean_text = source.read_text(encoding="utf-8")
        final_text = final.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return "corrected documentation source is not readable UTF-8"
    if case_id == "first-finished-contribution-primary":
        return _python_example_problem(clean_text, final_text, fixture)
    if case_id == "first-finished-contribution-heldout":
        return _link_problem(clean_text, final_text, fixture, workspace)
    return "unknown fixture correctness contract"
