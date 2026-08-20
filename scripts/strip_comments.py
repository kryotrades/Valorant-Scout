from __future__ import annotations

import ast
import io
import os
import sys
import tokenize

_BODY_FIELDS = ("body", "orelse", "finalbody")


def _is_bare_string(stmt: ast.stmt) -> bool:
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
    )


def _string_statement_spans(tree: ast.AST) -> list[tuple[int, int, bool, int]]:
    spans = []
    for node in ast.walk(tree):
        for field in _BODY_FIELDS:
            body = getattr(node, field, None)
            if not isinstance(body, list) or not body:
                continue
            strings = [s for s in body if _is_bare_string(s)]
            if not strings:
                continue
            empties = len(strings) == len(body) and not isinstance(node, ast.Module)
            for i, stmt in enumerate(strings):
                spans.append((stmt.lineno, stmt.end_lineno, empties and i == 0, stmt.col_offset))
    return spans


def _comment_spans(src: str) -> dict[int, int]:
    out = {}
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            out[tok.start[0]] = tok.start[1]
    return out


def strip_source(src: str) -> str:
    tree = ast.parse(src)
    docs = _string_statement_spans(tree)
    comments = _comment_spans(src)
    lines = src.splitlines()

    drop = set()
    replace: dict[int, str] = {}
    for start, end, needs_pass, indent in docs:
        if lines[start - 1][:indent].strip():
            continue
        for ln in range(start, end + 1):
            drop.add(ln)
        if needs_pass:
            replace[start] = " " * indent + "pass"

    out = []
    for i, line in enumerate(lines, start=1):
        if i in replace:
            out.append(replace[i])
            continue
        if i in drop:
            continue
        if i in comments:
            kept = line[: comments[i]].rstrip()
            if not kept:
                continue
            out.append(kept)
            continue
        out.append(line.rstrip())

    cleaned, blanks = [], 0
    for line in out:
        if not line.strip():
            blanks += 1
            if blanks > 2:
                continue
        else:
            blanks = 0
        cleaned.append(line)
    return "\n".join(cleaned).strip("\n") + "\n"


def strip_file(path: str, check: bool = False) -> tuple[int, int]:
    with open(path, encoding="utf-8") as fh:
        original = fh.read()
    stripped = strip_source(original)
    compile(stripped, path, "exec")
    if check:
        if stripped != original:
            raise ValueError("comments or docstrings remain")
        return len(original), len(original)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(stripped)
    return len(original), len(stripped)


def main(root: str, check: bool = False) -> int:
    before = after = files = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in sorted(filenames):
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            try:
                b, a = strip_file(path, check=check)
            except Exception as exc:
                print(f"FAIL {os.path.relpath(path, root)}: {exc}", file=sys.stderr)
                return 1
            before, after, files = before + b, after + a, files + 1
    if check:
        print(f"verified {files} Python files contain no comments or docstrings")
    else:
        saved = 100 * (before - after) / before if before else 0
        print(
            f"stripped {files} python files: {before:,} -> {after:,} bytes ({saved:.1f}% smaller)"
        )
    return 0


if __name__ == "__main__":
    check_mode = "--check" in sys.argv[1:]
    args = [arg for arg in sys.argv[1:] if arg != "--check"]
    sys.exit(main(args[0] if args else ".", check=check_mode))
