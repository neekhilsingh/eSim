#!/usr/bin/env python3
"""
esim_preflight.py -- eSim 2.5 / Ubuntu 25.04 pre-flight compatibility checker.

Probes the host for every incompatibility documented in docs/BUGS.md and reports
which ones actually affect THIS machine. Nothing is assumed: every finding is
derived from a live probe, and anything that cannot be probed is reported as
UNKNOWN rather than guessed.

Part of: esim-plucky-fix (FOSSEE eSim Screening Task 4, Autumn 2026)
Author : Neekhil Kumar Singh -- https://github.com/neekhilsingh

Requires: Python 3.8+, standard library only. No pip install, no root.

Usage:
    python3 scripts/esim_preflight.py              # human-readable table
    python3 scripts/esim_preflight.py --json       # machine-readable
    python3 scripts/esim_preflight.py --verbose    # include OK / N-A rows

Exit codes:
    0  no blocking issue found
    1  at least one BLOCKER affects this host
    2  only non-blocking issues found
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import platform
import re
import shutil
import subprocess
import sys

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

# Launchpad PPAs (KiCad, OpenModelica, ...) publish builds per Ubuntu series.
# In practice they target LTS releases plus the current development series.
# 25.04 "plucky" is an interim release and is routinely absent.
SERIES_USUALLY_PUBLISHED = {"focal", "jammy", "noble"}

# Verified live against numpy 2.2.6 -- see docs/BUGS.md ISSUE-07.
NUMPY2_REMOVED = [
    "float_", "NaN", "Inf", "alltrue", "unicode_", "bool8", "int0",
    "sometrue", "cumproduct", "product", "round_", "string_", "complex_",
]

# Verified live against matplotlib 3.10.9 -- see docs/BUGS.md ISSUE-11.
MPL_REMOVED = ["register_cmap"]

BLOCKER, MAJOR, MINOR = "BLOCKER", "MAJOR", "MINOR"

AFFECTED, OK, UNKNOWN, NA = "AFFECTED", "OK", "UNKNOWN", "N/A"

COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
_C = {
    AFFECTED: "\033[1;31m", OK: "\033[1;32m",
    UNKNOWN: "\033[1;33m", NA: "\033[1;90m",
    "dim": "\033[2m", "bold": "\033[1m", "off": "\033[0m",
}


def c(key: str, text: str) -> str:
    """Colourise text when attached to a terminal."""
    return f"{_C.get(key, '')}{text}{_C['off']}" if COLOR else text


# --------------------------------------------------------------------------- #
# Probe helpers
# --------------------------------------------------------------------------- #

def run(cmd, timeout=10):
    """Run a command, return (rc, combined_output). Never raises."""
    try:
        p = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=timeout, text=True, errors="replace",
        )
        return p.returncode, p.stdout.strip()
    except FileNotFoundError:
        return 127, ""
    except subprocess.TimeoutExpired:
        return 124, ""
    except OSError as exc:  # permission, exec format, ...
        return 126, str(exc)


def os_release() -> dict:
    """Parse /etc/os-release into a dict."""
    data = {}
    try:
        with open("/etc/os-release", encoding="utf-8") as fh:
            for line in fh:
                if "=" in line:
                    k, _, v = line.strip().partition("=")
                    data[k] = v.strip('"')
    except OSError:
        pass
    return data


def tool_version(binary: str, *args) -> "str | None":
    """Return the first line of `binary --version`, or None if unavailable."""
    if not shutil.which(binary):
        return None
    rc, out = run([binary, *(args or ["--version"])])
    if rc != 0 and not out:
        return None
    return out.splitlines()[0].strip() if out else None


def first_int(text: str) -> "int | None":
    """First standalone integer in a string (used for major versions)."""
    m = re.search(r"(\d+)", text or "")
    return int(m.group(1)) if m else None


def py_import(module: str):
    """Import a module in a *subprocess* so a broken lib cannot kill us.

    Returns (ok, version_or_error).
    """
    code = (
        "import importlib,sys\n"
        f"m=importlib.import_module({module!r})\n"
        "print(getattr(m,'__version__','present'))\n"
    )
    rc, out = run([sys.executable, "-c", code], timeout=60)
    return (rc == 0, out.splitlines()[-1] if out else "import failed")


# --------------------------------------------------------------------------- #
# Finding model
# --------------------------------------------------------------------------- #

class Finding:
    """One documented issue, plus the outcome of probing for it."""

    def __init__(self, ident, title, severity, component,
                 status, detail, fix=""):
        self.id = ident
        self.title = title
        self.severity = severity
        self.component = component
        self.status = status
        self.detail = detail
        self.fix = fix

    def as_dict(self) -> dict:
        return {
            "id": self.id, "title": self.title, "severity": self.severity,
            "component": self.component, "status": self.status,
            "detail": self.detail, "fix": self.fix,
        }


# --------------------------------------------------------------------------- #
# The checks
# --------------------------------------------------------------------------- #

def check_pep668(_ctx) -> Finding:
    """ISSUE-01: PEP 668 marker makes system pip installs fail."""
    markers = glob.glob("/usr/lib/python3*/EXTERNALLY-MANAGED")
    markers += glob.glob("/usr/lib/python3/EXTERNALLY-MANAGED")
    if markers:
        return Finding(
            "ISSUE-01", "pip refuses system installs (PEP 668)",
            BLOCKER, "installer / Python deps", AFFECTED,
            f"marker present: {markers[0]}",
            "install deps via apt, or use a venv with --system-site-packages",
        )
    return Finding(
        "ISSUE-01", "pip refuses system installs (PEP 668)",
        BLOCKER, "installer / Python deps", OK,
        "no EXTERNALLY-MANAGED marker on this host",
    )


def check_kicad_ppa(ctx) -> Finding:
    """ISSUE-02: pinned KiCad PPA has no build for this Ubuntu series."""
    codename = ctx["codename"]
    # Look for an already-configured KiCad PPA in either apt format.
    entries = []
    for pat in ("/etc/apt/sources.list.d/*.list",
                "/etc/apt/sources.list.d/*.sources"):
        for path in glob.glob(pat):
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    body = fh.read()
            except OSError:
                continue
            if "kicad" in body.lower():
                entries.append(os.path.basename(path))

    if not codename:
        return Finding("ISSUE-02", "KiCad PPA has no build for this series",
                       BLOCKER, "KiCad (schematic + layout)", UNKNOWN,
                       "could not read VERSION_CODENAME from /etc/os-release")

    detail = f"series={codename}"
    if entries:
        detail += f"; kicad apt entries found: {', '.join(sorted(set(entries)))}"

    if codename not in SERIES_USUALLY_PUBLISHED:
        return Finding(
            "ISSUE-02", "KiCad PPA has no build for this series",
            BLOCKER, "KiCad (schematic + layout)", AFFECTED,
            detail + " -- interim series, PPA usually publishes LTS only",
            "drop the PPA and install `kicad` from the Ubuntu archive",
        )
    return Finding("ISSUE-02", "KiCad PPA has no build for this series",
                   BLOCKER, "KiCad (schematic + layout)", OK,
                   detail + " -- series is normally published by the PPA")


def check_gcc14(_ctx) -> Finding:
    """ISSUE-03: GCC >= 14 turns legacy C warnings into hard errors."""
    ver = tool_version("gcc")
    if ver is None:
        return Finding("ISSUE-03", "GCC >= 14 rejects legacy C (ngspice build)",
                       BLOCKER, "NGHDL / ngspice compile", UNKNOWN,
                       "gcc not installed yet -- re-run after build-essential")
    m = re.search(r"\b(\d+)\.\d+\.\d+\b", ver)
    major = int(m.group(1)) if m else None
    if major is None:
        return Finding("ISSUE-03", "GCC >= 14 rejects legacy C (ngspice build)",
                       BLOCKER, "NGHDL / ngspice compile", UNKNOWN,
                       f"unparsed version string: {ver}")
    if major >= 14:
        return Finding(
            "ISSUE-03", "GCC >= 14 rejects legacy C (ngspice build)",
            BLOCKER, "NGHDL / ngspice compile", AFFECTED,
            f"gcc {major} -- implicit-function-declaration, int-conversion "
            "and incompatible-pointer-types are errors by default",
            "export CFLAGS with -std=gnu17 and the matching -Wno-error=* flags",
        )
    return Finding("ISSUE-03", "GCC >= 14 rejects legacy C (ngspice build)",
                   BLOCKER, "NGHDL / ngspice compile", OK,
                   f"gcc {major} still treats these as warnings")


def check_apt_key(_ctx) -> Finding:
    """ISSUE-04: apt-key was removed, so key imports fail outright."""
    if shutil.which("apt-key"):
        return Finding("ISSUE-04", "apt-key removed from apt 3.x",
                       MAJOR, "3rd-party apt repos", OK,
                       "apt-key still present (deprecated but working)")
    return Finding(
        "ISSUE-04", "apt-key removed from apt 3.x",
        MAJOR, "3rd-party apt repos", AFFECTED,
        "apt-key not on PATH -- any `apt-key add` in a script aborts",
        "pipe the key through `gpg --dearmor` into /etc/apt/keyrings "
        "and reference it with signed-by=",
    )


def check_distutils(ctx) -> Finding:
    """ISSUE-05: distutils removed in Python 3.12; apt package gone too."""
    ok, _ = py_import("distutils")
    pyver = ctx["python_version"]
    rc, _ = run(["apt-cache", "show", "python3-distutils"], timeout=20)
    pkg_exists = (rc == 0)
    if ok and pkg_exists:
        return Finding("ISSUE-05", "distutils removed in Python 3.12+",
                       MAJOR, "Python build deps", OK,
                       f"python {pyver}: distutils importable, apt package exists")
    bits = []
    if not ok:
        bits.append("`import distutils` fails")
    if not pkg_exists:
        bits.append("apt package python3-distutils not available")
    return Finding(
        "ISSUE-05", "distutils removed in Python 3.12+",
        MAJOR, "Python build deps", AFFECTED,
        f"python {pyver}: " + "; ".join(bits),
        "drop python3-distutils from the apt list; rely on python3-setuptools",
    )


def check_ghdl(_ctx) -> Finding:
    """ISSUE-06: the `ghdl` metapackage pulls the LLVM backend (huge)."""
    rc_m, _ = run(["dpkg-query", "-W", "-f=${Status}", "ghdl-mcode"], timeout=15)
    rc_l, _ = run(["dpkg-query", "-W", "-f=${Status}", "ghdl-llvm"], timeout=15)
    have_mcode, have_llvm = rc_m == 0, rc_l == 0
    ver = tool_version("ghdl")

    if have_mcode:
        return Finding("ISSUE-06", "`ghdl` pulls the LLVM backend (~1 GB)",
                       MAJOR, "NGHDL (VHDL co-simulation)", OK,
                       f"ghdl-mcode installed ({ver or 'version unknown'})")
    if have_llvm:
        return Finding(
            "ISSUE-06", "`ghdl` pulls the LLVM backend (~1 GB)",
            MAJOR, "NGHDL (VHDL co-simulation)", AFFECTED,
            f"ghdl-llvm installed ({ver or 'version unknown'}) -- this is the "
            "variant whose LLVM dependency chain makes the installer look hung",
            "prefer `apt-get install -y ghdl-mcode` on amd64",
        )
    return Finding(
        "ISSUE-06", "`ghdl` pulls the LLVM backend (~1 GB)",
        MAJOR, "NGHDL (VHDL co-simulation)", AFFECTED,
        "no ghdl backend installed -- a bare `apt-get install ghdl` will "
        "resolve to ghdl-llvm and drag in the LLVM toolchain",
        "install ghdl-mcode explicitly before the installer runs",
    )


def check_numpy2(_ctx) -> Finding:
    """ISSUE-07: NumPy 2.0 deleted long-standing aliases."""
    ok, ver = py_import("numpy")
    if not ok:
        return Finding("ISSUE-07", "NumPy 2.x removed legacy aliases",
                       MAJOR, "eSim plotting / model code", UNKNOWN,
                       f"numpy not importable ({ver})")
    major = first_int(ver)
    if major is not None and major < 2:
        return Finding("ISSUE-07", "NumPy 2.x removed legacy aliases",
                       MAJOR, "eSim plotting / model code", OK,
                       f"numpy {ver} still provides the legacy aliases")
    code = (
        "import numpy as np,json\n"
        f"names={NUMPY2_REMOVED!r}\n"
        "print(json.dumps([n for n in names if not hasattr(np,n)]))\n"
    )
    rc, out = run([sys.executable, "-c", code], timeout=60)
    gone = []
    if rc == 0 and out:
        try:
            gone = json.loads(out.splitlines()[-1])
        except (ValueError, IndexError):
            gone = []
    return Finding(
        "ISSUE-07", "NumPy 2.x removed legacy aliases",
        MAJOR, "eSim plotting / model code", AFFECTED,
        f"numpy {ver}; removed here: " + (", ".join("np." + g for g in gone) or "none"),
        "run scripts/patch_esim_sources.py --scan to find real call sites",
    )


def check_matplotlib(_ctx) -> Finding:
    """ISSUE-11: matplotlib 3.9/3.10 removed some cm helpers."""
    ok, ver = py_import("matplotlib")
    if not ok:
        return Finding("ISSUE-11", "matplotlib removed cm.register_cmap",
                       MINOR, "eSim plotting", UNKNOWN,
                       f"matplotlib not importable ({ver})")
    code = (
        "import matplotlib.cm as cm,json\n"
        f"print(json.dumps([n for n in {MPL_REMOVED!r} if not hasattr(cm,n)]))\n"
    )
    rc, out = run([sys.executable, "-c", code], timeout=60)
    gone = []
    if rc == 0 and out:
        try:
            gone = json.loads(out.splitlines()[-1])
        except (ValueError, IndexError):
            gone = []
    if gone:
        return Finding(
            "ISSUE-11", "matplotlib removed cm.register_cmap",
            MINOR, "eSim plotting", AFFECTED,
            f"matplotlib {ver}; missing: " + ", ".join("cm." + g for g in gone),
            "use matplotlib.colormaps.register() instead",
        )
    return Finding("ISSUE-11", "matplotlib removed cm.register_cmap",
                   MINOR, "eSim plotting", OK,
                   f"matplotlib {ver} still exposes {', '.join(MPL_REMOVED)}")


def check_qt(_ctx) -> Finding:
    """ISSUE-08: Qt5 needs PyQt5 + QScintilla + the xcb platform libs."""
    problems, notes = [], []

    ok_qt, qt_ver = py_import("PyQt5.QtWidgets")
    if ok_qt:
        notes.append("PyQt5 present")
    else:
        problems.append("PyQt5 not importable")

    ok_sci, _ = py_import("PyQt5.Qsci")
    if ok_sci:
        notes.append("QScintilla present")
    else:
        problems.append("PyQt5.Qsci (python3-pyqt5.qsci) missing")

    rc, out = run(["ldconfig", "-p"], timeout=20)
    if rc == 0 and out:
        if "libxcb-cursor.so" not in out:
            problems.append("libxcb-cursor0 missing (Qt xcb plugin will fail)")
        else:
            notes.append("libxcb-cursor present")
    else:
        notes.append("ldconfig unavailable, xcb libs unverified")

    session = os.environ.get("XDG_SESSION_TYPE", "")
    if session:
        notes.append(f"session={session}")
        if session == "wayland" and not os.environ.get("QT_QPA_PLATFORM"):
            notes.append("QT_QPA_PLATFORM unset under Wayland")

    detail = "; ".join(problems + notes) or "no Qt information available"
    if problems:
        return Finding(
            "ISSUE-08", "Qt5 platform plugin / QScintilla missing",
            BLOCKER, "eSim main GUI", AFFECTED, detail,
            "apt-get install python3-pyqt5 python3-pyqt5.qsci "
            "python3-pyqt5.qtsvg libxcb-cursor0 qtwayland5",
        )
    return Finding("ISSUE-08", "Qt5 platform plugin / QScintilla missing",
                   BLOCKER, "eSim main GUI", OK, detail)


def check_sudo_home(_ctx) -> Finding:
    """ISSUE-09: running under sudo puts $HOME at /root."""
    sudo_user = os.environ.get("SUDO_USER")
    home = os.path.expanduser("~")
    if sudo_user and home == "/root":
        return Finding(
            "ISSUE-09", "sudo makes $HOME=/root, launcher lands in /root",
            MAJOR, "desktop launcher + user config", AFFECTED,
            f"SUDO_USER={sudo_user} but HOME={home}",
            "resolve the real home via getent passwd \"$SUDO_USER\"",
        )
    if os.geteuid() == 0 and not sudo_user:
        return Finding("ISSUE-09", "sudo makes $HOME=/root, launcher lands in /root",
                       MAJOR, "desktop launcher + user config", AFFECTED,
                       "running as real root -- eSim will be installed for root only",
                       "run the installer as a normal user with sudo rights")
    return Finding("ISSUE-09", "sudo makes $HOME=/root, launcher lands in /root",
                   MAJOR, "desktop launcher + user config", OK,
                   f"HOME={home}, euid={os.geteuid()}")


def check_verilator(_ctx) -> Finding:
    """ISSUE-10: NgVeri targets Verilator 4.x; 25.04 ships 5.x."""
    ver = tool_version("verilator")
    if ver is None:
        return Finding("ISSUE-10", "Verilator 5.x vs NgVeri (expects 4.x)",
                       MINOR, "NgVeri (Verilog import)", NA,
                       "verilator not installed -- only needed for NgVeri")
    m = re.search(r"(\d+)\.(\d+)", ver)
    major = int(m.group(1)) if m else None
    if major is not None and major >= 5:
        return Finding(
            "ISSUE-10", "Verilator 5.x vs NgVeri (expects 4.x)",
            MINOR, "NgVeri (Verilog import)", AFFECTED,
            f"{ver} -- Verilator 5 changed --cc output and timing defaults",
            "pass --no-timing -Wno-fatal, or pin Verilator 4.228 in ~/.esim",
        )
    return Finding("ISSUE-10", "Verilator 5.x vs NgVeri (expects 4.x)",
                   MINOR, "NgVeri (Verilog import)", OK, ver)


def check_installer_hygiene(ctx) -> Finding:
    """ISSUE-12: upstream install-eSim.sh has no set -e and no resume."""
    path = ctx.get("installer_path")
    if not path or not os.path.isfile(path):
        return Finding("ISSUE-12", "installer lacks set -e / resume support",
                       MINOR, "install-eSim.sh", UNKNOWN,
                       "install-eSim.sh not found -- pass --installer <path>")
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            body = fh.read()
    except OSError as exc:
        return Finding("ISSUE-12", "installer lacks set -e / resume support",
                       MINOR, "install-eSim.sh", UNKNOWN, str(exc))

    problems = []
    if not re.search(r"^\s*set\s+-[eu]", body, re.M):
        problems.append("no `set -e` (failures are silently ignored)")
    if "PLUCKY-FIX" not in body:
        problems.append("not yet patched by patch-installer.sh")
    pips = len(re.findall(r"\bpip3?\s+install\b", body))
    if pips:
        problems.append(f"{pips} unguarded pip install call(s)")

    if problems:
        return Finding("ISSUE-12", "installer lacks set -e / resume support",
                       MINOR, "install-eSim.sh", AFFECTED,
                       "; ".join(problems),
                       "apply scripts/patch-installer.sh")
    return Finding("ISSUE-12", "installer lacks set -e / resume support",
                   MINOR, "install-eSim.sh", OK, "already hardened")


CHECKS = [
    check_pep668, check_kicad_ppa, check_gcc14, check_apt_key,
    check_distutils, check_ghdl, check_numpy2, check_qt,
    check_sudo_home, check_verilator, check_matplotlib,
    check_installer_hygiene,
]


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #

def build_context(args) -> dict:
    rel = os_release()
    return {
        "distro": rel.get("PRETTY_NAME", platform.platform()),
        "version_id": rel.get("VERSION_ID", "?"),
        "codename": rel.get("VERSION_CODENAME", ""),
        "arch": platform.machine(),
        "kernel": platform.release(),
        "python_version": platform.python_version(),
        "installer_path": args.installer,
    }


def print_header(ctx) -> None:
    print()
    print(c("bold", "  eSim 2.5 pre-flight check  --  Ubuntu 25.04 compatibility"))
    print("  " + "-" * 62)
    for label, key in (("Distro", "distro"), ("Series", "codename"),
                       ("Kernel", "kernel"), ("Arch", "arch"),
                       ("Python", "python_version")):
        print(f"  {label:<8}: {ctx[key] or '(unknown)'}")
    gcc = tool_version("gcc") or "not installed"
    print(f"  {'GCC':<8}: {gcc}")
    print()


def print_table(findings, verbose) -> None:
    shown = [f for f in findings
             if verbose or f.status in (AFFECTED, UNKNOWN)]
    if not shown:
        print("  Nothing to report -- this host looks clean.\n")
        return

    print(f"  {'ID':<10} {'STATUS':<9} {'SEV':<8} ISSUE")
    print("  " + "-" * 62)
    for f in shown:
        # Pad first, colourise second -- escape codes must not count as width.
        status = c(f.status, f"{f.status:<9}")
        print(f"  {f.id:<10} {status}{f.severity:<8} {f.title}")
        print(f"             {c('dim', f.component + ' | ' + f.detail)}")
        if f.fix and f.status == AFFECTED:
            print(f"             {c('dim', 'fix: ' + f.fix)}")
        print()


def print_summary(findings) -> None:
    aff = [f for f in findings if f.status == AFFECTED]
    blockers = [f for f in aff if f.severity == BLOCKER]
    unknown = [f for f in findings if f.status == UNKNOWN]

    print("  " + "-" * 62)
    print(f"  checked {len(findings)}  |  "
          f"affected {len(aff)}  |  blockers {len(blockers)}  |  "
          f"unknown {len(unknown)}")
    if blockers:
        print("  " + c(AFFECTED, "Blocking: " + ", ".join(f.id for f in blockers)))
        print("  Next: sudo ./install-eSim-plucky.sh --install")
    elif aff:
        print("  " + c(UNKNOWN, "Non-blocking issues only -- install should complete."))
    else:
        print("  " + c(OK, "No known Ubuntu 25.04 issue detected on this host."))
    print()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Pre-flight compatibility check for eSim 2.5 on Ubuntu 25.04.")
    ap.add_argument("--json", action="store_true",
                    help="emit JSON instead of a table")
    ap.add_argument("--verbose", "-v", action="store_true",
                    help="also show OK and N/A rows")
    ap.add_argument("--installer", default="install-eSim.sh",
                    help="path to upstream install-eSim.sh (default: ./install-eSim.sh)")
    args = ap.parse_args(argv)

    ctx = build_context(args)

    findings = []
    for check in CHECKS:
        try:
            findings.append(check(ctx))
        except Exception as exc:  # a probe must never crash the report
            findings.append(Finding(
                getattr(check, "__name__", "check"), "probe raised",
                MINOR, "preflight", UNKNOWN, f"{type(exc).__name__}: {exc}"))

    findings.sort(key=lambda f: ([BLOCKER, MAJOR, MINOR].index(f.severity)
                                 if f.severity in (BLOCKER, MAJOR, MINOR) else 9,
                                 f.id))

    if args.json:
        print(json.dumps({"host": ctx,
                          "findings": [f.as_dict() for f in findings]}, indent=2))
    else:
        print_header(ctx)
        print_table(findings, args.verbose)
        print_summary(findings)

    aff = [f for f in findings if f.status == AFFECTED]
    if any(f.severity == BLOCKER for f in aff):
        return 1
    return 2 if aff else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        sys.exit(130)
