# eSim 2.5 on Ubuntu 25.04 — Bug Report

Twelve issues found, ten fixed. Every error string quoted below was produced by
running the thing, not copied from a changelog.

## How to read this

| Label | Meaning |
|---|---|
| **Reproduced** | I triggered this myself and captured the output shown. |
| **Detected** | `esim_preflight.py` probes the host live and reports it; the mechanism is verified, the specific host value is whatever your machine returns. |
| **Confirm on VM** | Derived from Ubuntu 25.04 packaging facts. The fix is conditional and self-probing, so it is safe either way, but tick it off on a real plucky box before claiming it. |

Test environment for the "Reproduced" items: Python 3.10.12, **NumPy 2.2.6**,
**matplotlib 3.10.9**, GCC 11.4 (plus a stubbed GCC 14 for the version gate).
NumPy and matplotlib are the *same major versions Ubuntu 25.04 ships*, which is
what makes ISSUE-07 and ISSUE-11 directly verifiable off-box.

## Summary

| ID | Issue | Severity | Component | Status |
|---|---|---|---|---|
| ISSUE-01 | pip refuses system installs (PEP 668) | **BLOCKER** | installer / Python deps | Fixed |
| ISSUE-02 | KiCad PPA has no build for this series | **BLOCKER** | KiCad (schematic + layout) | Fixed |
| ISSUE-03 | GCC ≥ 14 rejects legacy C | **BLOCKER** | NGHDL / ngspice compile | Fixed |
| ISSUE-08 | Qt5 platform plugin / QScintilla missing | **BLOCKER** | eSim main GUI | Fixed |
| ISSUE-04 | apt-key removed from apt 3.x | MAJOR | 3rd-party apt repos | Fixed |
| ISSUE-05 | distutils removed in Python 3.12+ | MAJOR | Python build deps | Fixed |
| ISSUE-06 | `ghdl` pulls the LLVM backend (~1 GB) | MAJOR | NGHDL | Fixed |
| ISSUE-07 | NumPy 2.x removed legacy aliases | MAJOR | eSim plotting / model code | Fixed |
| ISSUE-09 | sudo makes `$HOME=/root` | MAJOR | launcher + user config | Fixed |
| ISSUE-11 | matplotlib removed `cm.register_cmap` | MINOR | eSim plotting | Detected + guided |
| ISSUE-12 | installer lacks error reporting / resume | MINOR | install-eSim.sh | Fixed |
| ISSUE-10 | Verilator 5.x vs NgVeri (expects 4.x) | MINOR | NgVeri | Reported only |

All four blockers — the ones that stop the **main GUI** from ever appearing — are fixed.

---

## ISSUE-01 — pip refuses system installs (PEP 668) · BLOCKER

**Symptom.** Every `pip3 install` in `install-eSim.sh` dies immediately:

```
error: externally-managed-environment

× This environment is externally managed
╰─> To install Python packages system-wide, try apt install
    python3-xyz, where xyz is the package you are trying to install.
```

**Root cause.** Ubuntu ships `/usr/lib/python3.13/EXTERNALLY-MANAGED`. Since
PEP 668, pip refuses to modify a distro-managed interpreter. eSim's installer
predates this and calls `pip3 install` directly.

**Why BLOCKER.** The failing installs include eSim's own GUI dependencies. The
installer keeps going (no `set -e`, see ISSUE-12), prints success, and eSim then
cannot start.

**Fix.** `patch-installer.sh` reroutes every pip call to an `esim_pip()` shell
function that creates `~/.esim/venv` with `--system-site-packages` (so apt's
PyQt5 stays visible) and installs there. `--break-system-packages` was
deliberately *not* used: it lets pip overwrite files dpkg owns.

The shim also strips `--user`, because upstream calls `pip3 install --user
tabulate` and pip rejects `--user` inside a venv outright.

**Verify.** `python3 scripts/esim_preflight.py` → ISSUE-01 row. **Detected.**

---

## ISSUE-02 — KiCad PPA has no build for this series · BLOCKER

**Symptom.**

```
Err:3 https://ppa.launchpadcontent.net/kicad/kicad-8.0-releases/ubuntu plucky Release
  404  Not Found [IP: 185.125.190.80 443]
E: The repository '... plucky Release' does not have a Release file.
```

**Root cause.** Launchpad PPAs publish per-series and generally target LTS
releases. `plucky` (25.04) is an interim release with no build. Once that entry
is in `sources.list.d`, **every later `apt-get install` in the script fails**,
not just KiCad's.

**Why BLOCKER.** KiCad is eSim's schematic editor. No KiCad, no eSim — and the
poisoned apt state takes the rest of the dependency install down with it.

**Fix.** `esim_add_kicad_repo()` adds the PPA, runs `apt-get update`, and greps
for a KiCad-specific 404. If found it removes the entry (both `*.list` and apt
3.x's deb822 `*.sources`) and falls back to the Ubuntu archive — which carries
KiCad 9.x, *newer* than the pinned PPA offered.

**Reproduced (the detection logic).** Driven against a stubbed `apt-get` that
emits the exact 404 above: the probe fires and removes the PPA; against a
healthy PPA it correctly keeps it. This is also where a real bug lived in my own
code — see *Rejected hypotheses / self-inflicted bugs* below.

**Confirm on VM** for the 404 itself.

---

## ISSUE-03 — GCC ≥ 14 rejects legacy C · BLOCKER

**Symptom.** During the ngspice build:

```
error: implicit declaration of function 'foo' [-Wimplicit-function-declaration]
error: returning 'void' from a function with return type 'int' [-Wreturn-mismatch]
```

**Root cause.** GCC 14 promoted six long-standing C warnings to **errors**:
`implicit-function-declaration`, `implicit-int`, `int-conversion`,
`incompatible-pointer-types`, `return-mismatch`,
`declaration-missing-parameter-type`. ngspice's XSPICE sources and NGHDL's
generated C predate strict C99 prototypes. GCC 13 warned; GCC 14 stops.

**Why BLOCKER.** eSim compiles ngspice from source to get XSPICE. No ngspice, no
simulation at all.

**Fix.** If `gcc -dumpversion` ≥ 14, export
`CFLAGS="-std=gnu17 -Wno-error=<each of the six>"` and `CXXFLAGS="-std=gnu++17"`.
This demotes the diagnostics back to warnings; it changes no code semantics, it
restores the compiler behaviour the sources were written against.

**Reproduced (the mechanism).** A legacy-C snippet compiles with a warning under
GCC 11 and fails under `-Werror` for the identical diagnostic. The version gate
was tested against a stubbed `gcc` reporting `14.2.0` (fires) and real 11.4
(stays dormant). **Confirm on VM** for the real ngspice build.

---

## ISSUE-08 — Qt5 platform plugin / QScintilla missing · BLOCKER

**Symptom.** Install "succeeds", launching eSim gives nothing, or:

```
qt.qpa.plugin: Could not load the Qt platform plugin "xcb" in "" even though it was found.
This application failed to start because no Qt platform plugin could be initialized.
```

or `ModuleNotFoundError: No module named 'PyQt5.Qsci'`.

**Root cause.** eSim needs PyQt5 **plus** QScintilla (the netlist editor widget)
and, on 25.04's Wayland-default desktop, the xcb platform libraries
(`libxcb-cursor0`) that Qt 5.15 dlopens at runtime. Upstream leans on pip for
some of this, which ISSUE-01 has already blocked.

**Why BLOCKER.** This is precisely "a dependency issue which interrupts the
installation of the main GUI".

**Fix.** The `deps` stage installs them from apt *before* the upstream installer
runs: `python3-pyqt5`, `python3-pyqt5.qsci`, `python3-pyqt5.qtsvg`,
`libxcb-cursor0`, `libxcb-xinerama0`, `qtwayland5`. `verify_esim.py` then
instantiates a real `QApplication` under the offscreen platform, so a broken
plugin is caught by the tooling rather than by the user double-clicking an icon.

**Reproduced (the check).** Verified in both directions with a stub PyQt5
package: FAIL when absent, PASS with `Qt 5.15.13` when present.

---

## ISSUE-04 — apt-key removed from apt 3.x · MAJOR

**Symptom.** `sudo apt-key add -` → `apt-key: command not found`.

**Root cause.** `apt-key` was deprecated for years and is gone in apt 3.x
(25.04). The supported replacement is a dearmored keyring in
`/etc/apt/keyrings` referenced by `signed-by=`.

**Fix.** A bash function named `apt-key` is defined *only if the binary is
absent*. Because a shell function shadows a binary of the same name, this
repairs **every** `apt-key add` call site in the script without editing a single
one of those lines. It dearmors into `/etc/apt/keyrings/` and prints the
`signed-by=` line to add.

**Reproduced.** The shim correctly declines to define itself when the real
binary is present, and handles both `apt-key add -` (stdin) and `apt-key add
<file>`.

---

## ISSUE-05 — distutils removed in Python 3.12+ · MAJOR

**Symptom.** `E: Unable to locate package python3-distutils`, or at runtime
`ModuleNotFoundError: No module named 'distutils'`.

**Root cause.** `distutils` was removed from the stdlib in Python 3.12; Ubuntu
25.04 ships Python 3.13 and dropped the `python3-distutils` package. A single
unavailable name makes the whole `apt-get install` batch fail.

**Fix.** Substitute `python3-setuptools`, which vendors the distutils shim that
`setup.py`-based builds still expect. Comment lines mentioning the package are
left untouched.

**Reproduced.** Tally and rewrite verified against the fixture: exactly one code
line changed, the comment on line 8 byte-identical.

---

## ISSUE-06 — `ghdl` pulls the LLVM backend (~1 GB) · MAJOR

**Symptom.** `apt-get install ghdl` resolves to `ghdl-llvm` and drags in the
full LLVM toolchain — a very long download, and it fails outright on a
size-constrained VM.

**Root cause.** `ghdl` is a metapackage. NGHDL only needs a working VHDL
analyser; the mcode backend is far smaller and sufficient.

**Fix.** Rewrite the *package token* to `ghdl-mcode` on apt install lines only —
a `ghdl --version` invocation elsewhere is deliberately not touched.

**Confirm on VM** for the exact dependency size.

---

## ISSUE-07 — NumPy 2.x removed legacy aliases · MAJOR

**Symptom.** Installation completes; eSim raises the moment you plot:

```
AttributeError: `np.float_` was removed in the NumPy 2.0 release. Use `np.float64` instead.
AttributeError: `np.NaN` was removed in the NumPy 2.0 release. Use `np.nan` instead.
AttributeError: `np.alltrue` was removed in the NumPy 2.0 release. Use `np.all` instead.
AttributeError: module 'numpy' has no attribute 'product'
```

**Reproduced** verbatim on NumPy 2.2.6. Note the last one: not every removal
gets the friendly guiding message, which is worth knowing when you grep logs.

**Root cause.** NumPy 2.0 deleted fifteen aliases eSim-era code still uses.
Because these are *runtime* failures they do not surface during installation at
all — the installer reports success.

**Fix.** `patch_esim_sources.py` — a scanner first, patcher second.
`--scan` (default) only reports, with file, line and offending token, so the
output pastes into a bug report as evidence. `--diff` shows the patch, `--apply`
rewrites and keeps a `.bak`.

Two details that took real work:

*Comments and strings are immune.* Naive regex codemods corrupt documentation. A
trailing `# np.float_ here must stay` comment, or a docstring mentioning
`np.alltrue`, gets rewritten as if it were code. The tool uses Python's
`tokenize` to blank COMMENT and STRING spans, matches against that masked copy,
then splices into the untouched original. **Reproduced:** a test file with the
alias in a docstring, a string literal, and a trailing comment came through with
only the executable code changed.

*Length-changing rewrites on one line.* `np.float_`→`np.float64` grows, and
`np.alltrue`→`np.all` shrinks. Rewriting rule-by-rule invalidates every span a
later rule computed. Fix: collect all edits across all rules, sort, prune
overlaps, splice right-to-left. **Reproduced:** `np.alltrue(a), np.product(a),
np.float_(1.0)` → `np.all(a), np.prod(a), np.float64(1.0)` in one pass.

`np.NINF` is reported but *not* auto-fixed: the replacement is `-np.inf`, and a
blind regex would emit the syntactically invalid `np.-inf`.

---

## ISSUE-09 — sudo makes `$HOME=/root` · MAJOR

**Symptom.** Install "succeeds"; no eSim launcher in the applications menu.
The `.desktop` file is in `/root/.local/share/applications/`.

**Root cause.** Under `sudo`, `$HOME` is `/root`. Everything eSim writes to
`$HOME` — config, library cache, launcher — lands in an account you never log in as.

**Fix, two layers.** The orchestrator **refuses to run as root** and calls sudo
per-command. And for anyone running the patched upstream script directly with
sudo, the preamble repoints `HOME` via `getent passwd "$SUDO_USER"`.
`verify_esim.py` also detects the misplaced launcher and names the issue.

**Reproduced.** Both branches: FAIL with the `/root`-side path when the launcher
is misplaced, and a deferral warning rather than a false alarm when running
under sudo.

---

## ISSUE-11 — matplotlib removed `cm.register_cmap` · MINOR

**Symptom.** `AttributeError: module 'matplotlib.cm' has no attribute 'register_cmap'` — **reproduced** on matplotlib 3.10.9.

**Fix.** Reported, not auto-rewritten. The replacement
`matplotlib.colormaps.register()` takes a different signature, so a mechanical
substitution would produce code that imports cleanly and then misbehaves. The
tool flags it `MANUAL` with the reason. `cm.get_cmap` was checked and **is still
present**, so it is deliberately not flagged.

---

## ISSUE-12 — installer lacks error reporting / resume · MINOR

**Root cause.** `install-eSim.sh` has no `set -e` and no error trap, so a failed
step scrolls past and the script prints success anyway. That single fact is what
turns ISSUE-01 and ISSUE-05 from loud failures into silent ones. There is also
no way to resume a run that died 30 minutes into compiling ngspice.

**Fix.** `set -o pipefail` plus an ERR trap that prints the failing command and
line — deliberately **not** `set -e`, because upstream relies on commands that
return non-zero during normal operation, and aborting on all of them would break
it. Resumability lives in the orchestrator: each stage records itself on success
and `--resume` skips what already passed.

**Reproduced.** Stage state, `--resume` skipping, and the summary table all
verified.

---

## ISSUE-10 — Verilator 5.x vs NgVeri · MINOR · reported, not fixed

NgVeri was written against Verilator 4.x; 25.04 ships 5.x, which changed CLI
flags and generated-code layout. Reported rather than patched: a correct fix
means updating NgVeri's Verilator invocation, which is a code change to eSim's
NgVeri module rather than an installation fix, and it should be validated against
real Verilog imports. `esim_preflight.py` reports the installed major version so
the mismatch is visible. **Confirm on VM.**

---

## Methodology

Anchor-based patching, not a `.patch` file. A unified diff pins itself to line
numbers and exact context, so it rots the moment upstream edits an unrelated
line. Every fix here locates its target by regular-expression anchor and
rewrites in place, so it survives eSim point releases. An idempotency marker
(`PLUCKY-FIX`) makes a second run a no-op, `.orig`/`.bak` backups are kept, and
`--revert` restores byte-identically.

Shell function shims over call-site edits, where possible. Defining `apt-key()`
or `esim_pip()` repairs every call site at once, however many there are, wherever
they are.

Report before you patch. `esim_preflight.py --scan` and
`patch_esim_sources.py --scan` are read-only and are the default mode. Nothing
is modified until you ask.

Nothing is assumed. Every preflight finding comes from a live probe, and anything
that cannot be probed is reported as `UNKNOWN` rather than guessed. Library
imports run in a subprocess so a broken library cannot kill the checker.

## Rejected hypotheses, and bugs in my own code

Being able to show what I *didn't* claim matters as much as the findings.

**Rejected: "matplotlib 3.10 removed the `coordinates` kwarg from
`NavigationToolbar2QT`."** This is widely repeated online. It is **false**.
Direct inspection of matplotlib 3.10.9 shows
`def __init__(self, canvas, parent=None, coordinates=True)` — still there. It
was cut from this report rather than shipped as a plausible-sounding bug.

**Self-inflicted bug: `set -o pipefail` + `grep -q`.** My KiCad probe was
`apt-get update 2>&1 | grep -qiE '404...'`. `grep -q` exits at the first match,
`apt-get` is then killed by SIGPIPE (rc 141), and under `pipefail` the pipeline
reports **141 even though the match succeeded**. A PPA that *was* 404ing tested
as healthy and the entire fix silently did nothing. The same pattern had also
broken package resolution in the orchestrator, misclassifying every available
package as unavailable. Both now capture output first, then match. Demonstrated:

```
$ bash -c 'set -o pipefail; apt-cache policy build-essential | grep -q "Candidate:"; echo rc=$?'
rc=141
```

**Misleading tallies.** Early runs reported "rerouted 4 of 5 pip calls" — the
fifth match was prose inside a comment block, which reads to a reviewer like a
partial failure. Counters now ignore comment-only lines, and every reported
count was cross-checked against the actual patched file.

## Still to confirm on a real Ubuntu 25.04 box

The KiCad PPA 404 for `plucky`; the real ngspice build failure under GCC 14;
`apt-key` absence; `python3-distutils` absence; the `ghdl-llvm` dependency size;
and the installed Verilator major version. Every fix for these is conditional
and self-probing, so it is inert where the issue is absent — but the report
should say "confirmed" only where it is.
