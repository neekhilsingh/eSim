#!/usr/bin/env python3
"""
verify_esim.py -- prove that eSim actually works after installation.

`install-eSim.sh` printing "eSim installed successfully" means the *script*
reached its last line, not that eSim runs. On Ubuntu 25.04 the two come apart
routinely: the installer completes while the Qt platform plugin is unloadable,
or QScintilla is missing, or the launcher was written into /root. This script
checks the things a user would otherwise discover by double-clicking the icon
and getting nothing.

Every probe is independent and none of them need eSim to be running. The Qt
check uses the offscreen platform so it works over SSH and in CI.

Part of: esim-plucky-fix (FOSSEE eSim Screening Task 4, Autumn 2026)
Author : Neekhil Kumar Singh -- https://github.com/neekhilsingh

Requires: Python 3.8+, standard library only.

Usage:
    python3 scripts/verify_esim.py
    python3 scripts/verify_esim.py --json
    python3 scripts/verify_esim.py --venv ~/.esim/venv   # check inside the venv

Exit codes:
    0  every required check passed
    1  at least one required check failed
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import platform
import shutil
import subprocess
import sys

PASS, FAIL, SKIP, WARN = "PASS", "FAIL", "SKIP", "WARN"

COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
_C = {PASS: "\033[1;32m", FAIL: "\033[1;31m",
      SKIP: "\033[1;90m", WARN: "\033[1;33m",
      "b": "\033[1m", "d": "\033[2m", "o": "\033[0m"}


def c(key, text):
    return f"{_C.get(key, '')}{text}{_C['o']}" if COLOR else text


def run(cmd, timeout=25, env=None):
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=timeout,
                           text=True, errors="replace", env=env)
        return p.returncode, p.stdout.strip()
    except FileNotFoundError:
        return 127, "not found"
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    except OSError as exc:
        return 126, str(exc)


class Check:
    def __init__(self, name, status, detail, required=True):
        self.name, self.status = name, status
        self.detail, self.required = detail, required

    def as_dict(self):
        return {"check": self.name, "status": self.status,
                "detail": self.detail, "required": self.required}


# --------------------------------------------------------------------------- #
# Python-side checks
# --------------------------------------------------------------------------- #

def python_exe(args):
    """Prefer the eSim venv interpreter when one exists -- that is what eSim uses."""
    if args.venv:
        cand = os.path.join(os.path.expanduser(args.venv), "bin", "python")
        if os.path.isfile(cand):
            return cand
    default = os.path.expanduser("~/.esim/venv/bin/python")
    return default if os.path.isfile(default) else sys.executable


def check_import(exe, module, label, required=True):
    code = (f"import {module} as _m\n"
            "print(getattr(_m,'__version__','present'))\n")
    rc, out = run([exe, "-c", code], timeout=90)
    if rc == 0:
        return Check(label, PASS, (out.splitlines() or ["present"])[-1], required)
    first = (out.splitlines() or ["import failed"])[-1]
    return Check(label, FAIL, first[:140], required)


def check_qt_offscreen(exe):
    """Instantiate a real QApplication -- catches broken platform plugins."""
    code = (
        "import os\n"
        "os.environ.setdefault('QT_QPA_PLATFORM','offscreen')\n"
        "from PyQt5.QtWidgets import QApplication\n"
        "from PyQt5.QtCore import QT_VERSION_STR\n"
        "a=QApplication([])\n"
        "print('Qt', QT_VERSION_STR)\n"
    )
    rc, out = run([exe, "-c", code], timeout=90)
    if rc == 0:
        return Check("Qt platform plugin loads", PASS,
                     (out.splitlines() or ["ok"])[-1])
    return Check("Qt platform plugin loads", FAIL,
                 (out.splitlines() or ["failed"])[-1][:140])


def check_numpy_aliases(exe):
    """eSim-era code calls names NumPy 2 deleted -- report which are absent."""
    names = ["float_", "NaN", "Inf", "alltrue", "product", "round_"]
    code = ("import numpy as np, json\n"
            f"print(json.dumps([n for n in {names!r} if not hasattr(np,n)]))\n")
    rc, out = run([exe, "-c", code], timeout=60)
    if rc != 0:
        return Check("NumPy legacy aliases", SKIP, "numpy unavailable", False)
    try:
        gone = json.loads((out.splitlines() or ["[]"])[-1])
    except ValueError:
        return Check("NumPy legacy aliases", SKIP, "unparsed probe output", False)
    if not gone:
        return Check("NumPy legacy aliases", PASS, "all still present", False)
    return Check("NumPy legacy aliases", WARN,
                 f"{len(gone)} removed ({', '.join('np.' + g for g in gone)}) "
                 "-- run patch_esim_sources.py --scan", False)


# --------------------------------------------------------------------------- #
# Binary / filesystem checks
# --------------------------------------------------------------------------- #

def check_binary(binary, label, version_args=("--version",), required=True):
    if not shutil.which(binary):
        return Check(label, FAIL if required else SKIP, "not on PATH", required)
    rc, out = run([binary, *version_args])
    line = (out.splitlines() or [""])[0][:90] if out else ""
    if rc == 0 or line:
        return Check(label, PASS, line or "present", required)
    return Check(label, FAIL if required else WARN, f"rc={rc}", required)


def check_ngspice():
    """ngspice needs XSPICE (code models) for eSim's NGHDL/NgVeri blocks."""
    if not shutil.which("ngspice"):
        return Check("ngspice + XSPICE", FAIL, "ngspice not on PATH")
    rc, out = run(["ngspice", "-v"], timeout=25)
    if rc != 0 and not out:
        rc, out = run(["ngspice", "--version"], timeout=25)
    low = (out or "").lower()
    ver = next((l for l in (out or "").splitlines() if l.strip()), "")[:70]
    if "xspice" in low:
        return Check("ngspice + XSPICE", PASS, f"{ver} (XSPICE enabled)")
    if ver:
        return Check("ngspice + XSPICE", WARN,
                     f"{ver} -- XSPICE not reported; NGHDL/NgVeri models may not load")
    return Check("ngspice + XSPICE", FAIL, "no version output")


def check_launcher():
    """The .desktop entry must exist in the *invoking user's* home (ISSUE-09)."""
    home = os.path.expanduser("~")
    if os.environ.get("SUDO_USER") and home == "/root":
        return Check("desktop launcher", WARN,
                     "running under sudo; re-run as your normal user to test this",
                     False)
    patterns = [
        os.path.join(home, ".local/share/applications/*esim*.desktop"),
        "/usr/share/applications/*esim*.desktop",
    ]
    found = [p for pat in patterns for p in glob.glob(pat)]
    if found:
        return Check("desktop launcher", PASS, found[0], False)
    # ESIM_VERIFY_ROOT_HOME exists so this branch is testable without being
    # root, and so the check still works if root's home is not literally /root.
    root_home = os.environ.get("ESIM_VERIFY_ROOT_HOME", "/root")
    root_side = glob.glob(
        os.path.join(root_home, ".local/share/applications/*esim*.desktop"))
    if root_side:
        return Check("desktop launcher", FAIL,
                     f"installed into root's home instead: {root_side[0]} "
                     "-- this is ISSUE-09", False)
    return Check("desktop launcher", WARN, "no *esim*.desktop found", False)


def check_venv(args):
    path = os.path.expanduser(args.venv or "~/.esim/venv")
    pip = os.path.join(path, "bin", "pip")
    if os.path.isfile(pip):
        return Check("eSim venv (ISSUE-01 workaround)", PASS, path, False)
    return Check("eSim venv (ISSUE-01 workaround)", SKIP,
                 f"none at {path} -- fine if deps came from apt", False)


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #

def collect(args):
    exe = python_exe(args)
    checks = [
        Check("python interpreter", PASS,
              f"{exe} ({platform.python_version()})", False),
        check_import(exe, "PyQt5.QtWidgets", "PyQt5 (GUI toolkit)"),
        check_import(exe, "PyQt5.Qsci", "QScintilla (netlist editor)"),
        check_import(exe, "PyQt5.QtSvg", "PyQt5 SVG (symbol rendering)"),
        check_qt_offscreen(exe),
        check_import(exe, "matplotlib", "matplotlib (waveform plots)"),
        check_import(exe, "numpy", "numpy"),
        check_numpy_aliases(exe),
        check_ngspice(),
        check_binary("kicad", "KiCad", ("--version",)),
        check_binary("ghdl", "GHDL (NGHDL)", ("--version",), required=False),
        check_binary("verilator", "Verilator (NgVeri)", ("--version",),
                     required=False),
        check_venv(args),
        check_launcher(),
    ]
    return exe, checks


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Post-install verification for eSim on Ubuntu 25.04.")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--venv", default="", help="path to the eSim venv")
    args = ap.parse_args(argv)

    exe, checks = collect(args)

    if args.json:
        print(json.dumps({"interpreter": exe,
                          "checks": [ch.as_dict() for ch in checks]}, indent=2))
    else:
        print()
        print(c("b", "  eSim post-install verification"))
        print("  " + "-" * 64)
        width = max(len(ch.name) for ch in checks)
        for ch in checks:
            tag = c(ch.status, f"{ch.status:<4}")
            star = "" if ch.required else c("d", " (optional)")
            print(f"  [{tag}] {ch.name:<{width}}  {ch.detail}{star}")
        print("  " + "-" * 64)

    failed = [ch for ch in checks if ch.status == FAIL and ch.required]
    warned = [ch for ch in checks if ch.status == WARN]

    if not args.json:
        n_pass = sum(1 for ch in checks if ch.status == PASS)
        print(f"  {n_pass}/{len(checks)} passed, "
              f"{len(failed)} required failure(s), {len(warned)} warning(s)")
        if failed:
            print("  " + c(FAIL, "eSim will NOT start correctly:"))
            for ch in failed:
                print(f"    - {ch.name}: {ch.detail}")
        elif warned:
            print("  " + c(WARN, "eSim should start; some blocks may misbehave."))
        else:
            print("  " + c(PASS, "eSim is fully functional on this host."))
        print()

    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        sys.exit(130)
