#!/usr/bin/env python3
"""
patch_esim_sources.py -- fix NumPy 2.x / matplotlib API removals in eSim's tree.

The installer-level fixes get eSim *installed*; these fix it at *runtime*. Ubuntu
25.04 ships NumPy 2.x and matplotlib 3.10, both of which deleted long-deprecated
names that eSim-era code still calls. The failures are AttributeErrors raised the
moment a waveform is plotted, so they do not show up during installation at all.

This is a scanner first and a patcher second. `--scan` (the default) only reports
what it found, with file, line number and the offending token, so the output can
be pasted into a bug report as evidence. Nothing is modified until `--apply`.

Part of: esim-plucky-fix (FOSSEE eSim Screening Task 4, Autumn 2026)
Author : Neekhil Kumar Singh -- https://github.com/neekhilsingh

Requires: Python 3.8+, standard library only.

Usage:
    python3 scripts/patch_esim_sources.py --scan            # report only
    python3 scripts/patch_esim_sources.py --scan --json
    python3 scripts/patch_esim_sources.py --diff            # show the patch
    python3 scripts/patch_esim_sources.py --apply           # rewrite, with .bak
    python3 scripts/patch_esim_sources.py --apply --root ~/eSim

Exit codes:
    0  clean (nothing to fix) or --apply succeeded
    1  occurrences found in --scan/--diff mode (so CI can gate on it)
    2  bad usage / root not found
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys

# --------------------------------------------------------------------------- #
# Rules
#
# Every entry was confirmed against a live NumPy 2.2.6 / matplotlib 3.10.9
# install -- the same major versions Ubuntu 25.04 ships. `hasattr` was used to
# check each name rather than trusting the release notes. See docs/BUGS.md.
# --------------------------------------------------------------------------- #

class Rule:
    def __init__(self, issue, pattern, replacement, why, auto=True):
        self.issue = issue
        self.regex = re.compile(pattern)
        self.replacement = replacement
        self.why = why
        self.auto = auto          # False => report only, needs a human


# NumPy 2.0 deleted these aliases outright (ISSUE-07).
_NUMPY_ALIASES = {
    "float_":     "float64",
    "complex_":   "complex128",
    "unicode_":   "str_",
    "string_":    "bytes_",
    "bool8":      "bool_",
    "int0":       "intp",
    "uint0":      "uintp",
    "NaN":        "nan",
    "Inf":        "inf",
    "Infinity":   "inf",
    "alltrue":    "all",
    "sometrue":   "any",
    "cumproduct": "cumprod",
    "product":    "prod",
    "round_":     "round",
}

RULES = [
    Rule("ISSUE-07",
         r"\b(np|numpy)\.%s\b" % re.escape(old),
         r"\1.%s" % new,
         "NumPy 2.0 removed np.%s; use np.%s" % (old, new))
    for old, new in _NUMPY_ALIASES.items()
]

RULES += [
    # np.NINF is also gone, but the replacement is `-np.inf` -- a sign change
    # that a blind regex would turn into the syntactically invalid `np.-inf`.
    # Report it and let a human move the minus sign.
    Rule("ISSUE-07",
         r"\b(?:np|numpy)\.NINF\b",
         "", "NumPy 2.0 removed np.NINF; use -np.inf (note the leading minus)",
         auto=False),

    # `from numpy import float_` style. Reported, not rewritten: the fix depends
    # on how the name is used further down the file.
    Rule("ISSUE-07",
         r"^\s*from\s+numpy\s+import\s+.*\b(%s)\b" % "|".join(
             re.escape(k) for k in _NUMPY_ALIASES),
         "", "direct `from numpy import <removed alias>` -- needs manual review",
         auto=False),

    # matplotlib removed cm.register_cmap (ISSUE-11). The replacement,
    # matplotlib.colormaps.register(), takes a different signature, so this is
    # flagged for a human rather than blindly rewritten.
    Rule("ISSUE-11",
         r"\b(?:matplotlib\.cm|cm|mpl\.cm)\.register_cmap\s*\(",
         "", "matplotlib 3.9 removed cm.register_cmap; use "
             "matplotlib.colormaps.register() -- signature differs",
         auto=False),
]

SKIP_DIRS = {".git", "__pycache__", "venv", ".venv", "build", "dist",
             "node_modules", ".tox", ".mypy_cache", "site-packages"}

COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if COLOR else text


# --------------------------------------------------------------------------- #
# Scanning
# --------------------------------------------------------------------------- #

def is_comment(line: str) -> bool:
    """True for comment-only lines (fallback path only)."""
    return line.lstrip().startswith("#")


NUL = "\x00"


def protected_mask(text: str):
    """Return a copy of `text` with every comment and string literal blanked.

    Regex codemods that ignore this detail corrupt documentation: a trailing
    `# np.float_ is gone` comment, or a doctest inside a docstring, gets
    rewritten as if it were code. Python's own tokenizer tells us exactly which
    byte ranges are comments or string literals, so we run the match against a
    masked copy and then splice the replacement into the untouched original.

    Falls back to None if the file does not tokenize (Python 2 syntax, partial
    file, ...), in which case the caller uses the comment-only heuristic.
    """
    import io
    import tokenize

    lines = text.splitlines(keepends=True)
    masked = list(lines)
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError, ValueError):
        return None

    for tok in tokens:
        if tok.type not in (tokenize.COMMENT, tokenize.STRING):
            continue
        (srow, scol), (erow, ecol) = tok.start, tok.end
        for row in range(srow, erow + 1):
            idx = row - 1
            if idx < 0 or idx >= len(masked):
                continue
            line = masked[idx]
            begin = scol if row == srow else 0
            end = ecol if row == erow else len(line)
            end = min(end, len(line))
            if begin >= end:
                continue
            masked[idx] = line[:begin] + NUL * (end - begin) + line[end:]
    return masked


def iter_py_files(root: str):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in sorted(filenames):
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


class Hit:
    def __init__(self, path, lineno, rule, text):
        self.path, self.lineno, self.rule, self.text = path, lineno, rule, text

    def as_dict(self):
        return {"file": self.path, "line": self.lineno,
                "issue": self.rule.issue, "auto_fixable": self.rule.auto,
                "why": self.rule.why, "source": self.text.strip()}


def scan_text(path: str, text: str):
    """Return (hits, patched_text). Comments and string literals are immune."""
    lines = text.splitlines(keepends=True)
    masked = protected_mask(text)
    hits, out = [], []

    for lineno, line in enumerate(lines, start=1):
        if masked is not None:
            probe = masked[lineno - 1]
        else:
            # Tokeniser bailed out: fall back to skipping comment-only lines.
            if is_comment(line):
                out.append(line)
                continue
            probe = line

        new_line = line
        # Collect every auto-fixable match across ALL rules *before* touching
        # the line. Rewriting rule-by-rule would be wrong: np.float_ -> np.float64
        # lengthens the line, so any span a later rule computed from the
        # original text would then point at the wrong offset.
        edits = []
        for rule in RULES:
            spans = [m.span() for m in rule.regex.finditer(probe)]
            if not spans:
                continue
            hits.append(Hit(path, lineno, rule, line))
            if rule.auto:
                edits.extend((s, e, rule) for s, e in spans)

        # Drop overlaps (keep the leftmost/longest), then splice right-to-left.
        edits.sort(key=lambda t: (t[0], -(t[1] - t[0])))
        pruned, last_end = [], -1
        for start, end, rule in edits:
            if start >= last_end:
                pruned.append((start, end, rule))
                last_end = end
        for start, end, rule in reversed(pruned):
            original = new_line[start:end]
            new_line = (new_line[:start]
                        + rule.regex.sub(rule.replacement, original)
                        + new_line[end:])
        out.append(new_line)

    return hits, "".join(out)


def scan_tree(root: str):
    """Return (all_hits, {path: (original, patched)})."""
    all_hits, changed = [], {}
    for path in iter_py_files(root):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                original = fh.read()
        except OSError:
            continue
        hits, patched = scan_text(path, original)
        if hits:
            all_hits.extend(hits)
        if patched != original:
            changed[path] = (original, patched)
    return all_hits, changed


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #

def report(root, hits, changed, n_files):
    print()
    print(c("1", "  eSim source scan  --  NumPy 2.x / matplotlib API removals"))
    print("  " + "-" * 64)
    print(f"  root        : {root}")
    print(f"  .py scanned : {n_files}")
    print(f"  occurrences : {len(hits)}")
    print(f"  files to fix: {len(changed)}")
    print()

    if not hits:
        print("  " + c("1;32", "Clean -- no removed NumPy/matplotlib name is used here."))
        print("  (This is a legitimate result, not a failed scan. Record it as")
        print("   'not reproducible on this tree' in your report.)")
        print()
        return

    by_issue = {}
    for h in hits:
        by_issue.setdefault(h.rule.issue, []).append(h)

    for issue in sorted(by_issue):
        group = by_issue[issue]
        auto = sum(1 for h in group if h.rule.auto)
        print(f"  {c('1', issue)}  --  {len(group)} occurrence(s), "
              f"{auto} auto-fixable, {len(group) - auto} manual")
        for h in group:
            rel = os.path.relpath(h.path, root)
            tag = c("1;32", "auto") if h.rule.auto else c("1;33", "MANUAL")
            print(f"    {rel}:{h.lineno}  [{tag}]")
            print(f"      {c('2', h.text.strip()[:96])}")
            if not h.rule.auto:
                print(f"      {c('2', '-> ' + h.rule.why)}")
        print()


def show_diff(root, changed):
    if not changed:
        print("  No automatic rewrite is needed.\n")
        return
    for path, (old, new) in sorted(changed.items()):
        rel = os.path.relpath(path, root)
        diff = difflib.unified_diff(
            old.splitlines(keepends=True), new.splitlines(keepends=True),
            fromfile=f"a/{rel}", tofile=f"b/{rel}")
        sys.stdout.writelines(diff)
    print()


def apply_changes(root, changed, make_backup=True):
    for path, (old, new) in sorted(changed.items()):
        rel = os.path.relpath(path, root)
        if make_backup:
            bak = path + ".bak"
            if not os.path.exists(bak):
                try:
                    with open(bak, "w", encoding="utf-8") as fh:
                        fh.write(old)
                except OSError as exc:
                    print(f"  ! backup failed for {rel}: {exc}")
                    continue
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(new)
            print(f"  {c('1;32', '+')} patched {rel}")
        except OSError as exc:
            print(f"  {c('1;31', 'x')} write failed {rel}: {exc}")
    print()


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Scan/patch eSim sources for NumPy 2.x and matplotlib "
                    "API removals (Ubuntu 25.04).")
    ap.add_argument("--root", default=".",
                    help="root of the eSim source tree (default: .)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--scan", action="store_true",
                      help="report occurrences only (default)")
    mode.add_argument("--diff", action="store_true",
                      help="print the unified diff that --apply would write")
    mode.add_argument("--apply", action="store_true",
                      help="rewrite files in place (a .bak is kept)")
    ap.add_argument("--json", action="store_true", help="machine-readable scan")
    ap.add_argument("--no-backup", action="store_true",
                    help="skip .bak files when applying")
    args = ap.parse_args(argv)

    root = os.path.abspath(os.path.expanduser(args.root))
    if not os.path.isdir(root):
        print(f"error: not a directory: {root}", file=sys.stderr)
        return 2

    n_files = sum(1 for _ in iter_py_files(root))
    hits, changed = scan_tree(root)

    if args.json:
        print(json.dumps({
            "root": root, "files_scanned": n_files,
            "occurrences": [h.as_dict() for h in hits],
            "files_to_patch": sorted(os.path.relpath(p, root) for p in changed),
        }, indent=2))
        return 1 if hits else 0

    if args.apply:
        report(root, hits, changed, n_files)
        if changed:
            apply_changes(root, changed, make_backup=not args.no_backup)
            print("  Re-run with --scan to confirm the tree is clean.\n")
        return 0

    report(root, hits, changed, n_files)
    if args.diff:
        show_diff(root, changed)
    elif hits:
        print("  Next: --diff to review, then --apply to rewrite.\n")
    return 1 if hits else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        sys.exit(130)
