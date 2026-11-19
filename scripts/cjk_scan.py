#!/usr/bin/env python3
"""List likely untranslated CJK strings in ShellBot's UI source.

This is deliberately a small, dependency-free inventory tool for the i18n work.
It scans visible web source and likely UI/API Python source, while skipping
comments, docstrings, tests, and lines that already use the known i18n helpers.
It reports source occurrences (not deduplicated English/Chinese keys) so a later
run can be compared with the baseline.
"""

from __future__ import annotations

import argparse
import io
import re
import tokenize
from pathlib import Path


CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]+")
WEB_SUFFIXES = {".html", ".js"}
PYTHON_NAMES = {
    "agent_cursor.py",
    "agent_group.py",
    "agent_grok.py",
    "agent_link.py",
    "agent_model.py",
    "agent_status.py",
    "bot_names.py",
    "bot_skills.py",
    "guide.py",
    "link_usage.py",
    "main.py",
    "sched_view.py",
    "sf_lang.py",
    "usage_probe.py",
}


def runs(text: str) -> list[str]:
    return [part for part in CJK.findall(text) if part]


def comment_only(line: str) -> bool:
    stripped = line.lstrip()
    return stripped.startswith(("//", "/*", "*", "<!--", "-->")) or stripped.startswith("#")


def existing_i18n(line: str) -> bool:
    """Skip obvious bilingual calls/entries already handled by the app."""
    return bool(
        re.search(r"\b(?:t|T|pick)\s*\(", line)
        or "sf_lang.pick(" in line
        or "data-i18n" in line
        or re.search(r"\b(?:zh|zh-TW)\s*:", line)
        or re.search(r"['\"][A-Za-z][^'\"]*['\"]\s*,\s*['\"][\u3400-\u9fff]", line)
        or (CJK.search(line) and re.search(r"['\"][^'\"]*[A-Za-z][^'\"]*['\"]\s*,", line))
        or (line.count("=>") >= 2 and "[" in line)
        or re.search(r"\b(?:zh|isZh)\s*\?", line)
    )


def strip_comments(lines: list[str]) -> list[str]:
    """Remove JS/HTML comments but keep strings and line numbers intact."""
    out: list[str] = []
    block = ""
    for line in lines:
        chars: list[str] = []
        i = 0
        while i < len(line):
            if block:
                end = line.find(block, i)
                if end < 0:
                    i = len(line)
                    continue
                i = end + 2
                block = ""
                continue
            if line.startswith("<!--", i):
                block = "-->"
                i += 4
                continue
            if line.startswith("/*", i):
                block = "*/"
                i += 2
                continue
            if line.startswith("//", i):
                break
            chars.append(line[i])
            i += 1
        out.append("".join(chars))
    return out


def i18n_lines(lines: list[str]) -> set[int]:
    """Return lines belonging to a multi-line t/T/pick call."""
    marked: set[int] = set()
    active = False
    depth = 0
    for lineno, line in enumerate(lines, 1):
        if not active and re.search(r"\b(?:t|T|pick)\s*\(", line):
            active = True
            depth = 0
        if active:
            marked.add(lineno)
            depth += line.count("(") - line.count(")")
            if depth <= 0 and ("," in line or ")" in line):
                active = False
    return marked


def table_lines(lines: list[str]) -> set[int]:
    """Return lines inside the legacy index.html I18N table."""
    marked: set[int] = set()
    active = False
    for lineno, line in enumerate(lines, 1):
        if not active and re.search(r"\bconst\s+I18N\s*=\s*\{", line):
            active = True
        if active:
            marked.add(lineno)
            if line.strip() == "};":
                active = False
    return marked


def external_locale_lines(lines: list[str]) -> set[int]:
    """Return the zh-TW data block in the extracted locale table."""
    marked: set[int] = set()
    active = False
    depth = 0
    for lineno, line in enumerate(lines, 1):
        if not active and re.search(r"['\"]zh-TW['\"]\s*:\s*\{", line):
            active = True
            depth = 0
        if active:
            marked.add(lineno)
            depth += line.count("{") - line.count("}")
            if depth <= 0:
                active = False
    return marked


def helper_lines(lines: list[str]) -> set[int]:
    """Return lines belonging to a multi-line sf_lang.pick / _(en, zh) call."""
    marked: set[int] = set()
    active = False
    depth = 0
    for lineno, line in enumerate(lines, 1):
        if not active and ("sf_lang.pick(" in line or re.search(r"\b_\s*\(", line)):
            active = True
            depth = 0
        if active:
            marked.add(lineno)
            depth += line.count("(") - line.count(")")
            if depth <= 0 and ")" in line:
                active = False
    return marked


def web_records(root: Path) -> list[tuple[str, int, str]]:
    out: list[tuple[str, int, str]] = []
    web = root / "web"
    for path in sorted(web.rglob("*")):
        if not path.is_file() or path.suffix not in WEB_SUFFIXES:
            continue
        rel = path.relative_to(root).as_posix()
        lines = strip_comments(path.read_text(encoding="utf-8", errors="replace").splitlines())
        translated = i18n_lines(lines) | table_lines(lines)
        if rel == "web/i18n.js":
            translated |= external_locale_lines(lines)
        for lineno, line in enumerate(lines, 1):
            if not runs(line) or comment_only(line) or existing_i18n(line) or lineno in translated:
                continue
            for value in runs(line):
                out.append((rel, lineno, value))
    return out


def python_records(root: Path) -> list[tuple[str, int, str]]:
    out: list[tuple[str, int, str]] = []
    def included(path: Path) -> bool:
        if path.name.startswith("test") or path.parts[-2:-1] == ("scripts",):
            return False
        return path.name == "main.py" or path.name.startswith("api_") or path.name in PYTHON_NAMES

    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if rel.startswith(".venv/") or not included(path):
            continue
        try:
            source = path.read_text(encoding="utf-8")
            translated = helper_lines(source.splitlines())
            tokens = tokenize.generate_tokens(io.StringIO(source).readline)
            for token in tokens:
                if token.type != tokenize.STRING or token.start[0] in translated or not runs(token.string):
                    continue
                raw = token.string
                if raw.lstrip().startswith(("'''", '\"\"\"')):
                    continue
                line = source.splitlines()[token.start[0] - 1]
                if existing_i18n(line):
                    continue
                for value in runs(raw):
                    out.append((rel, token.start[0], value))
        except (OSError, UnicodeError, tokenize.TokenError, IndentationError):
            continue
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = args.root.resolve()
    records = [("web", *row) for row in web_records(root)] + [("python", *row) for row in python_records(root)]
    records.sort(key=lambda row: (row[1], row[2], row[3]))
    print("source\tfile:line\tstring")
    for source, path, lineno, value in records:
        print(f"{source}\t{path}:{lineno}\t{value}")
    print(f"TOTAL\t{len(records)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
