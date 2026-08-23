#!/usr/bin/env bash
#===============================================================================
#         FILE: patch-installer.sh
#      PROJECT: esim-plucky-fix -- eSim 2.5 on Ubuntu 25.04 (plucky)
#       AUTHOR: Neekhil Kumar Singh -- https://github.com/neekhilsingh
#
#  Patches FOSSEE's upstream install-eSim.sh so that it completes on Ubuntu
#  25.04. See docs/BUGS.md for the reasoning behind every individual fix.
#
#  DESIGN NOTE -- why anchors and not a .patch file:
#  A unified diff pins itself to line numbers and exact context, so it rots the
#  moment upstream touches an unrelated line. This script instead locates each
#  defect by regular-expression anchor (`pip install`, `ppa:kicad/...`,
#  `python3-distutils`, ...) and rewrites it in place. That means it keeps
#  working across eSim point releases, and it is safe to run twice: an
#  idempotency marker makes a second run a no-op.
#
#  Several fixes are delivered as *shell function shims* in an injected
#  preamble rather than as edits to call sites. A bash function shadows a
#  binary of the same name, so defining `apt-key()` transparently repairs every
#  `apt-key add` in the file -- however many there are, wherever they are --
#  without touching a single one of those lines.
#
#  USAGE
#     ./patch-installer.sh --dry-run              # show the diff, change nothing
#     ./patch-installer.sh                        # patch ./install-eSim.sh
#     ./patch-installer.sh --file /path/to/install-eSim.sh
#     ./patch-installer.sh --revert               # restore the .orig backup
#
#  EXIT CODES
#     0  patched (or already patched, or dry-run completed)
#     1  target missing / unreadable
#     2  bad usage
#===============================================================================

set -uo pipefail

MARKER="PLUCKY-FIX"
TARGET="install-eSim.sh"
DRY_RUN=0
REVERT=0
FIXES=()

# -----------------------------------------------------------------------------
# Output helpers
# -----------------------------------------------------------------------------
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    R=$'\033[1;31m'; G=$'\033[1;32m'; Y=$'\033[1;33m'
    B=$'\033[1m';    D=$'\033[2m';    O=$'\033[0m'
else
    R=""; G=""; Y=""; B=""; D=""; O=""
fi

info()  { printf '  %s\n' "$*"; }
ok()    { printf '  %s+%s %s\n' "$G" "$O" "$*"; }
warn()  { printf '  %s!%s %s\n' "$Y" "$O" "$*"; }
die()   { printf '  %sx%s %s\n' "$R" "$O" "$*" >&2; exit "${2:-1}"; }
note()  { printf '    %s%s%s\n' "$D" "$*" "$O"; }

usage() {
    sed -n '/^#  USAGE/,/^#====/p' "$0" | sed 's/^#  \{0,1\}//;$d'
    exit "${1:-2}"
}

# -----------------------------------------------------------------------------
# Argument parsing
# -----------------------------------------------------------------------------
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run|-n) DRY_RUN=1 ;;
        --revert)     REVERT=1 ;;
        --file|-f)    shift; [ $# -gt 0 ] || usage; TARGET="$1" ;;
        --help|-h)    usage 0 ;;
        *)            printf 'unknown option: %s\n' "$1" >&2; usage ;;
    esac
    shift
done

[ -f "$TARGET" ] || die "target not found: $TARGET
    Run this from the root of your eSim clone, or pass --file <path>."
[ -r "$TARGET" ] || die "target not readable: $TARGET"

BACKUP="${TARGET}.orig"

# -----------------------------------------------------------------------------
# Revert mode
# -----------------------------------------------------------------------------
if [ "$REVERT" -eq 1 ]; then
    [ -f "$BACKUP" ] || die "no backup at $BACKUP -- nothing to revert"
    cp -- "$BACKUP" "$TARGET" || die "restore failed"
    ok "restored $TARGET from $BACKUP"
    exit 0
fi

# -----------------------------------------------------------------------------
# Already patched?
# -----------------------------------------------------------------------------
if grep -q "$MARKER" -- "$TARGET" 2>/dev/null; then
    ok "$TARGET already carries the $MARKER preamble -- nothing to do"
    note "use --revert to undo, then re-run to re-apply"
    exit 0
fi

# -----------------------------------------------------------------------------
# The injected preamble: runtime shims for the environment-level defects.
# -----------------------------------------------------------------------------
write_preamble() {
cat <<'PREAMBLE_EOF'

# ============================ PLUCKY-FIX BEGIN ==============================
# Injected by esim-plucky-fix -- https://github.com/neekhilsingh/eSim
# Ubuntu 25.04 (plucky) compatibility shims. Full rationale: docs/BUGS.md
# Remove this block (or run `patch-installer.sh --revert`) to get upstream back.

# --- ISSUE-12: surface the failing command instead of ploughing on -----------
# Deliberately NOT `set -e`: upstream install-eSim.sh relies on commands that
# return non-zero during normal operation, so aborting on every failure would
# break it. An ERR trap gives us the diagnostics without changing control flow.
set -o pipefail
trap '_pf_rc=$?; printf "[plucky-fix] command failed (rc=%s) at line %s: %s\n" \
      "$_pf_rc" "$LINENO" "$BASH_COMMAND" >&2' ERR

_pf_say() { printf '[plucky-fix] %s\n' "$*"; }

# --- ISSUE-09: under sudo, $HOME is /root, so eSim installs for the wrong user
# Everything eSim writes to "$HOME" (config, library cache, .desktop launcher)
# would land in /root and be invisible to the desktop user. Repoint $HOME at
# the account that actually invoked sudo.
if [ -n "${SUDO_USER:-}" ] && [ "${SUDO_USER}" != "root" ]; then
    _pf_home="$(getent passwd "$SUDO_USER" 2>/dev/null | cut -d: -f6)"
    if [ -n "${_pf_home:-}" ] && [ -d "$_pf_home" ] && [ "${HOME:-}" != "$_pf_home" ]; then
        export HOME="$_pf_home"
        export ESIM_REAL_USER="$SUDO_USER"
        _pf_say "HOME repointed to $HOME (SUDO_USER=$SUDO_USER)"
    fi
fi

# --- ISSUE-03: GCC >= 14 promotes legacy C diagnostics to hard errors --------
# ngspice's XSPICE sources (and the C that NGHDL generates) predate strict C99
# prototype rules. GCC 13 warned; GCC 14 errors out and the build dies. Demote
# exactly those six diagnostics back to warnings -- this changes no semantics,
# it only restores the compiler behaviour the code was written against.
_pf_gccmaj="$(gcc -dumpversion 2>/dev/null | cut -d. -f1)"
if [ -n "${_pf_gccmaj:-}" ] && [ "$_pf_gccmaj" -ge 14 ] 2>/dev/null; then
    _pf_relax="-std=gnu17"
    for _pf_w in implicit-function-declaration implicit-int int-conversion \
                 incompatible-pointer-types return-mismatch \
                 declaration-missing-parameter-type; do
        _pf_relax="$_pf_relax -Wno-error=$_pf_w"
    done
    export CFLAGS="${CFLAGS:-} $_pf_relax"
    export CXXFLAGS="${CXXFLAGS:-} -std=gnu++17"
    unset _pf_w _pf_relax
    _pf_say "gcc $_pf_gccmaj detected -- relaxed CFLAGS exported for ngspice"
fi

# --- ISSUE-04: apt-key no longer exists on apt 3.x --------------------------
# A bash function shadows a missing binary, so this single shim repairs every
# `apt-key add` call site in the script without editing any of them. Keys are
# dearmored into /etc/apt/keyrings, which is the supported replacement.
if ! command -v apt-key >/dev/null 2>&1; then
    apt-key() {
        case "${1:-}" in
            add)
                shift
                install -d -m 0755 /etc/apt/keyrings 2>/dev/null || true
                _pf_kr="/etc/apt/keyrings/esim-imported-$$-${RANDOM}.gpg"
                if [ $# -eq 0 ] || [ "${1:-}" = "-" ]; then
                    gpg --dearmor > "$_pf_kr" 2>/dev/null
                else
                    gpg --dearmor < "$1" > "$_pf_kr" 2>/dev/null
                fi
                chmod 0644 "$_pf_kr" 2>/dev/null || true
                _pf_say "key written to $_pf_kr"
                _pf_say "  add  signed-by=$_pf_kr  to the matching sources entry"
                ;;
            *)  _pf_say "ignoring unsupported call: apt-key $*" ;;
        esac
        return 0
    }
    _pf_say "apt-key absent -- installed gpg --dearmor shim"
fi

# --- ISSUE-01: PEP 668 makes pip refuse to touch the system interpreter -----
# Ubuntu ships /usr/lib/python3.13/EXTERNALLY-MANAGED, so `pip3 install X`
# fails with "error: externally-managed-environment". Rather than force the
# issue with --break-system-packages (which lets pip fight dpkg over the same
# files), route eSim's pure-Python extras into a dedicated venv that still sees
# the apt-installed PyQt5 via --system-site-packages.
ESIM_VENV="${ESIM_VENV:-$HOME/.esim/venv}"
esim_pip() {
    if [ ! -x "$ESIM_VENV/bin/pip" ]; then
        _pf_say "creating venv at $ESIM_VENV"
        python3 -m venv --system-site-packages "$ESIM_VENV" 2>/dev/null \
            || python3 -m venv "$ESIM_VENV" \
            || { _pf_say "venv creation failed -- is python3-venv installed?"; return 1; }
        "$ESIM_VENV/bin/python" -m pip install --quiet --upgrade \
            pip setuptools wheel 2>/dev/null || true
        if [ -n "${ESIM_REAL_USER:-}" ]; then
            chown -R "$ESIM_REAL_USER" "$ESIM_VENV" 2>/dev/null || true
        fi
    fi

    # Upstream calls `pip3 install --user ...`. Inside a venv pip *rejects*
    # --user outright ("Can not perform a '--user' install. User site-packages
    # are not visible in this virtualenv."), so silently drop the flag: the
    # venv already provides the per-user isolation --user was asking for.
    _pf_args=()
    for _pf_a in "$@"; do
        case "$_pf_a" in
            --user) _pf_say "dropping --user (meaningless inside a venv)" ;;
            *)      _pf_args+=("$_pf_a") ;;
        esac
    done

    "$ESIM_VENV/bin/pip" "${_pf_args[@]}"
    _pf_rc=$?
    if [ -n "${ESIM_REAL_USER:-}" ]; then
        chown -R "$ESIM_REAL_USER" "$ESIM_VENV" 2>/dev/null || true
    fi
    return $_pf_rc
}

# --- ISSUE-02: the pinned KiCad PPA publishes nothing for interim series ----
# Launchpad PPAs target LTS releases. On 25.04 "plucky" the Release file 404s,
# which poisons every later `apt-get install` in the script. Probe the PPA; if
# it cannot serve this series, remove it and take KiCad from the Ubuntu
# archive (25.04 carries KiCad 9.x, which is newer than the PPA offered).
esim_add_kicad_repo() {
    local ppa="${1:-}"
    local series; series="$(. /etc/os-release 2>/dev/null && printf '%s' "${VERSION_CODENAME:-}")"
    [ -n "$ppa" ] || { _pf_say "no PPA argument; using Ubuntu archive KiCad"; return 0; }

    _pf_say "probing $ppa for series '${series:-unknown}'"
    if ! add-apt-repository -y "$ppa" >/dev/null 2>&1; then
        _pf_say "add-apt-repository refused $ppa -- falling back to the archive"
        esim_drop_kicad_repo "$ppa"; return 0
    fi
    # Capture apt's output before matching it. Piping straight into `grep -q`
    # looks tidier but is broken here: grep -q exits at the first match, apt-get
    # is then killed by SIGPIPE (rc 141), and with `set -o pipefail` in force the
    # pipeline reports 141 -- so a PPA that *is* 404ing would test as healthy and
    # this entire fix would silently do nothing.
    _pf_out="$(apt-get update -o Acquire::Retries=1 2>&1)"
    if printf '%s\n' "$_pf_out" \
         | grep -iE 'kicad.*(does not have a Release file|404 +Not Found)' >/dev/null; then
        _pf_say "$ppa has no build for '$series' -- removing it"
        esim_drop_kicad_repo "$ppa"; return 0
    fi
    _pf_say "$ppa is usable on '$series'"
    return 0
}

esim_drop_kicad_repo() {
    add-apt-repository -y -r "${1:-}" >/dev/null 2>&1 || true
    # apt 3.x writes deb822 *.sources, older apt writes *.list -- clear both.
    rm -f /etc/apt/sources.list.d/*kicad*.list \
          /etc/apt/sources.list.d/*kicad*.sources 2>/dev/null || true
    apt-get update -qq 2>/dev/null || true
    _pf_say "KiCad will be installed from the Ubuntu archive"
}
# ============================= PLUCKY-FIX END ===============================
PREAMBLE_EOF
}

# -----------------------------------------------------------------------------
# Individual text rewrites. Each returns the number of lines it changed.
# -----------------------------------------------------------------------------

# Count lines matching an extended regex. `grep -c` prints 0 *and* exits 1 when
# there is no match, so the count must be captured before the || fires.
count_re() {
    local n
    n="$(grep -cE -- "$1" "$2" 2>/dev/null)" || n=0
    printf '%s' "${n:-0}"
}

# Count matching lines, ignoring comment-only lines. Without this, a regex like
# `pip install` also matches prose inside a comment block, and the tally then
# reads like a partial failure ("rerouted 4 of 5") when nothing is actually wrong.
count_code_re() {
    local n
    n="$(grep -vE '^[[:space:]]*#' -- "$2" | grep -cE -- "$1")" || n=0
    printf '%s' "${n:-0}"
}

# Count lines that mention every one of the given lowercase substrings,
# ignoring comment-only lines. Used where a single regex would be unreadable
# (e.g. add-apt-repository + kicad, which upstream may write as
# `add-apt-repository -y "$kicadPPA"`).
count_all() {
    local f="$1"; shift
    awk -v pats="$*" '
        BEGIN { n = split(pats, p, " ") }
        /^[[:space:]]*#/ { next }
        { line = tolower($0); hit = 1
          for (i = 1; i <= n; i++) if (index(line, p[i]) == 0) { hit = 0; break }
          if (hit) c++ }
        END { print c + 0 }' "$f" 2>/dev/null || printf '0'
}

# FIX A -- inject the preamble immediately after the shebang.
fix_preamble() {
    local f="$1" tmp first
    tmp="$(mktemp)" || return 1
    write_preamble > "${tmp}.pre"
    # Read the first line directly rather than `head -n1 | grep -q`: under
    # pipefail, grep -q's early exit can leave the pipeline reporting SIGPIPE.
    IFS= read -r first < "$f" || first=""
    case "$first" in
        '#!'*) { printf '%s\n' "$first"; cat "${tmp}.pre"; tail -n +2 -- "$f"; } > "$tmp" ;;
        *)     { cat "${tmp}.pre"; cat -- "$f"; } > "$tmp" ;;
    esac
    mv -- "$tmp" "$f"; rm -f -- "${tmp}.pre"
    FIXES+=("preamble  : injected runtime shims (ISSUE-01/02/03/04/09/12)")
}

# FIX B -- ISSUE-01: route every pip invocation through the venv helper.
fix_pip() {
    local f="$1" before after
    before="$(count_code_re '(^|[^[:alnum:]_])(pip3?|python3?[[:space:]]+-m[[:space:]]+pip)[[:space:]]+install' "$f")"
    [ "$before" -gt 0 ] || { note "ISSUE-01: no pip install lines found"; return 0; }
    # python3 -m pip install ...  ->  esim_pip install ...
    sed -i -E '/^[[:space:]]*#/! s/(^|[[:space:]])python3?[[:space:]]+-m[[:space:]]+pip[[:space:]]+install/\1esim_pip install/g' -- "$f"
    # pip3 install / pip install  ->  esim_pip install   (skip our own helper)
    sed -i -E '/esim_pip/! { /^[[:space:]]*#/! s/(^|[[:space:]])(sudo[[:space:]]+)?pip3?[[:space:]]+install/\1esim_pip install/g }' -- "$f"
    after="$(count_re 'esim_pip[[:space:]]+install' "$f")"
    FIXES+=("ISSUE-01  : rerouted $after of $before pip call(s) into the eSim venv")
}

# FIX C -- ISSUE-02: guard the KiCad PPA behind the probing helper.
# Upstream stores the PPA in a shell variable (`add-apt-repository -y "$kicadPPA"`),
# so anchoring on a literal `ppa:kicad/...` string finds nothing. Match any
# add-apt-repository line that mentions kicad in *any* form -- literal or
# variable -- and keep whatever the argument was, so the runtime expansion of
# "$kicadPPA" still reaches our helper untouched.
fix_kicad_ppa() {
    local f="$1" n
    n="$(count_all "$f" add-apt-repository kicad)"
    if [ "$n" -eq 0 ]; then
        note "ISSUE-02: no KiCad add-apt-repository line found"
        return 0
    fi
    sed -i -E '/kicad/I {
        s@(^[[:space:]]*)(sudo[[:space:]]+)?add-apt-repository[[:space:]]+((-y|--yes|-n|--no-update)[[:space:]]+)*@\1esim_add_kicad_repo @
    }' -- "$f"
    FIXES+=("ISSUE-02  : guarded $n KiCad PPA line(s) with series probing")
}

# FIX D -- ISSUE-05: python3-distutils no longer exists in the archive.
fix_distutils() {
    local f="$1" n
    n="$(count_code_re 'python3-distutils' "$f")"
    [ "$n" -gt 0 ] || { note "ISSUE-05: python3-distutils not referenced"; return 0; }
    # Drop the package token; keep python3-setuptools, which now provides the
    # distutils shim that setup.py-based builds still expect.
    sed -i -E '/^[[:space:]]*#/! s/[[:space:]]*python3-distutils(-extra)?\>/ python3-setuptools/g' -- "$f"
    FIXES+=("ISSUE-05  : replaced python3-distutils on $n line(s)")
}

# FIX E -- ISSUE-06: `ghdl` resolves to ghdl-llvm and pulls in ~1 GB of LLVM.
fix_ghdl() {
    local f="$1" n
    # Only rewrite the *package token* on apt install lines; never touch a
    # `ghdl --version` style invocation.
    n="$(count_code_re '(apt-get|apt|aptitude)[^#]*install[^#]*\<ghdl\>' "$f")"
    [ "$n" -gt 0 ] || { note "ISSUE-06: no apt line installs ghdl"; return 0; }
    sed -i -E '/^[[:space:]]*#/! { /(apt-get|apt|aptitude)[^#]*install/ s/\<ghdl\>(-mcode|-llvm|-gcc)?/ghdl-mcode/g }' -- "$f"
    FIXES+=("ISSUE-06  : pinned ghdl-mcode on $n apt line(s), avoiding LLVM")
}

# -----------------------------------------------------------------------------
# Drive it
# -----------------------------------------------------------------------------
printf '\n  %sesim-plucky-fix  ::  install-eSim.sh patcher%s\n' "$B" "$O"
printf '  %s\n' "------------------------------------------------------------"
info "target : $TARGET"
info "mode   : $([ "$DRY_RUN" -eq 1 ] && echo 'dry-run (no writes)' || echo 'apply')"
printf '\n'

WORK="$(mktemp)" || die "cannot create temp file"
trap 'rm -f -- "$WORK" "${WORK}.pre"' EXIT
cp -- "$TARGET" "$WORK" || die "cannot copy target"

# Order matters: text rewrites first, preamble prepended last. If the preamble
# went in first, later sed passes would rewrite the shim definitions inside it
# (the venv helper legitimately contains the text `pip install`).
fix_pip       "$WORK"
fix_kicad_ppa "$WORK"
fix_distutils "$WORK"
fix_ghdl      "$WORK"
fix_preamble  "$WORK"

printf '\n  %sfixes staged%s\n' "$B" "$O"
for entry in "${FIXES[@]}"; do ok "$entry"; done

if [ "$DRY_RUN" -eq 1 ]; then
    printf '\n  %sunified diff (upstream -> patched)%s\n\n' "$B" "$O"
    diff -u --label "a/$TARGET" --label "b/$TARGET" -- "$TARGET" "$WORK" || true
    printf '\n'
    warn "dry-run: $TARGET was NOT modified"
    exit 0
fi

if [ ! -f "$BACKUP" ]; then
    cp -- "$TARGET" "$BACKUP" || die "backup failed -- refusing to patch"
    ok "backup written to $BACKUP"
else
    warn "backup already exists at $BACKUP -- left untouched"
fi

cat -- "$WORK" > "$TARGET" || die "write failed"
chmod +x -- "$TARGET" 2>/dev/null || true

printf '\n'
ok "patched $TARGET ($(wc -l < "$TARGET") lines)"
note "verify with : bash -n $TARGET"
note "review with : diff -u $BACKUP $TARGET"
note "undo with   : $0 --file $TARGET --revert"
printf '\n'
