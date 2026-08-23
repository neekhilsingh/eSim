#!/usr/bin/env bash
#===============================================================================
#         FILE: install-eSim-plucky.sh
#      PROJECT: esim-plucky-fix -- eSim 2.5 on Ubuntu 25.04 (plucky)
#       AUTHOR: Neekhil Kumar Singh -- https://github.com/neekhilsingh
#
#  One command that takes a stock Ubuntu 25.04 machine to a working eSim 2.5.
#  It does not replace FOSSEE's install-eSim.sh -- it wraps it:
#
#      preflight -> deps -> patch -> install -> sources -> verify
#
#  DESIGN NOTES
#
#  * Resumable. Installing eSim takes a long time, mostly compiling ngspice. If
#    stage 4 dies you should not have to redo stages 1-3. Each stage records
#    itself in a state file on success, and --resume skips whatever already
#    completed. This is what makes the script usable while you are still
#    diagnosing a failure.
#
#  * Tolerant apt. `apt-get install a b c` is all-or-nothing: one package name
#    that does not exist on this release aborts the entire batch, taking the
#    other twenty with it. Package names do move between releases, so this
#    script resolves the list against apt-cache first and reports what is
#    genuinely unavailable instead of failing opaquely.
#
#  * Never run under sudo. If you sudo the whole script, $HOME becomes /root and
#    eSim installs itself for a user you will never log in as (ISSUE-09). This
#    script refuses to start as root and calls sudo per-command instead.
#
#  * Every stage is separately runnable (--only) and every stage is a no-op the
#    second time, so it is safe to re-run after fixing something by hand.
#
#  USAGE
#     ./install-eSim-plucky.sh --list                  # show the stages
#     ./install-eSim-plucky.sh --dry-run               # print, execute nothing
#     ./install-eSim-plucky.sh                         # full run
#     ./install-eSim-plucky.sh --resume                # skip completed stages
#     ./install-eSim-plucky.sh --only verify           # one stage
#     ./install-eSim-plucky.sh --from install          # this stage onwards
#     ./install-eSim-plucky.sh --esim-root ~/eSim      # where install-eSim.sh is
#     ./install-eSim-plucky.sh --reset                 # forget recorded progress
#
#  EXIT CODES
#     0  all requested stages succeeded
#     1  a stage failed (the state file keeps the ones that passed)
#     2  bad usage / wrong environment
#===============================================================================

set -uo pipefail

VERSION="1.0.0"
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS="$HERE/scripts"

ESIM_ROOT="${ESIM_ROOT:-$HOME/eSim}"
STATE_DIR="$HOME/.esim"
STATE_FILE="$STATE_DIR/plucky-state"
LOG_DIR="$STATE_DIR/logs"

DRY_RUN=0
RESUME=0
ASSUME_YES=0
ONLY=""
FROM=""

ALL_STAGES=(preflight deps patch install sources verify)

declare -A STAGE_DESC=(
    [preflight]="profile the host and list every issue it is exposed to"
    [deps]="install the Ubuntu 25.04 packages eSim needs (GUI, build, HDL)"
    [patch]="rewrite install-eSim.sh with the plucky compatibility shims"
    [install]="run the patched FOSSEE installer"
    [sources]="fix NumPy 2.x / matplotlib API removals in the eSim tree"
    [verify]="prove eSim actually starts and its blocks are usable"
)

# -----------------------------------------------------------------------------
# Output
# -----------------------------------------------------------------------------
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    R=$'\033[1;31m'; G=$'\033[1;32m'; Y=$'\033[1;33m'
    C=$'\033[1;36m'; B=$'\033[1m'; D=$'\033[2m'; O=$'\033[0m'
else
    R=""; G=""; Y=""; C=""; B=""; D=""; O=""
fi

info() { printf '  %s\n' "$*"; }
ok()   { printf '  %s+%s %s\n' "$G" "$O" "$*"; }
warn() { printf '  %s!%s %s\n' "$Y" "$O" "$*"; }
err()  { printf '  %sx%s %s\n' "$R" "$O" "$*" >&2; }
note() { printf '    %s%s%s\n' "$D" "$*" "$O"; }
die()  { err "$*"; exit "${2:-2}"; }

banner() {
    printf '\n  %s==============================================================%s\n' "$C" "$O"
    printf '  %s  %s%s\n' "$C" "$*" "$O"
    printf '  %s==============================================================%s\n\n' "$C" "$O"
}

usage() {
    sed -n '/^#  USAGE/,/^#====/p' "$0" | sed 's/^#  \{0,1\}//;$d'
    exit "${1:-2}"
}

# Print a command, then run it unless this is a dry run.
runcmd() {
    printf '    %s$ %s%s\n' "$D" "$*" "$O"
    [ "$DRY_RUN" -eq 1 ] && return 0
    "$@"
}

confirm() {
    [ "$ASSUME_YES" -eq 1 ] && return 0
    [ "$DRY_RUN" -eq 1 ] && return 0
    [ -t 0 ] || return 0          # non-interactive (CI, piped) -- proceed
    local reply
    printf '  %s?%s %s [y/N] ' "$Y" "$O" "$*"
    read -r reply
    case "$reply" in [yY]*) return 0 ;; *) return 1 ;; esac
}

# -----------------------------------------------------------------------------
# Arguments
# -----------------------------------------------------------------------------
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run|-n)  DRY_RUN=1 ;;
        --resume)      RESUME=1 ;;
        --yes|-y)      ASSUME_YES=1 ;;
        --only)        shift; [ $# -gt 0 ] || usage; ONLY="$1" ;;
        --from)        shift; [ $# -gt 0 ] || usage; FROM="$1" ;;
        --esim-root)   shift; [ $# -gt 0 ] || usage; ESIM_ROOT="$1" ;;
        --list)
            printf '\n  %sstages%s\n\n' "$B" "$O"
            for s in "${ALL_STAGES[@]}"; do
                printf '    %-10s %s\n' "$s" "${STAGE_DESC[$s]}"
            done
            printf '\n'
            exit 0 ;;
        --reset)
            rm -f -- "$STATE_FILE" && ok "progress reset" || warn "no state file"
            exit 0 ;;
        --version)     printf 'install-eSim-plucky.sh %s\n' "$VERSION"; exit 0 ;;
        --help|-h)     usage 0 ;;
        *)             err "unknown option: $1"; usage ;;
    esac
    shift
done

is_stage() {
    local want="$1" s
    for s in "${ALL_STAGES[@]}"; do [ "$s" = "$want" ] && return 0; done
    return 1
}
[ -z "$ONLY" ] || is_stage "$ONLY" || die "no such stage: $ONLY (try --list)"
[ -z "$FROM" ] || is_stage "$FROM" || die "no such stage: $FROM (try --list)"

# -----------------------------------------------------------------------------
# Environment sanity
# -----------------------------------------------------------------------------
[ "$(id -u)" -ne 0 ] || die "do not run this as root or under sudo.
    Under sudo \$HOME is /root, so eSim would install itself for a user you
    never log in as -- that is ISSUE-09, the very bug this repo fixes.
    Run it as your normal user; it will call sudo only where it must."

command -v sudo >/dev/null 2>&1 || die "sudo is required but not installed"
command -v apt-get >/dev/null 2>&1 || die "this script targets Debian/Ubuntu (apt-get not found)"

if [ -r /etc/os-release ]; then
    # shellcheck disable=SC1091
    SERIES="$(. /etc/os-release && printf '%s' "${VERSION_CODENAME:-unknown}")"
    PRETTY="$(. /etc/os-release && printf '%s' "${PRETTY_NAME:-unknown}")"
else
    SERIES="unknown"; PRETTY="unknown"
fi

mkdir -p -- "$STATE_DIR" "$LOG_DIR" 2>/dev/null || true
LOG_FILE="$LOG_DIR/install-$(date +%Y%m%d-%H%M%S).log"

# Mirror everything into a log file, because the useful half of a failed eSim
# install is the 400 lines of compiler output that scrolled past. Colour is kept
# on the terminal but stripped from the file, so the log can be pasted straight
# into a bug report. The `wait` in the exit trap gives tee time to flush --
# without it the last few lines can be lost when the script exits.
if [ "$DRY_RUN" -eq 0 ] && [ -z "${ESIM_PLUCKY_LOG_ACTIVE:-}" ]; then
    if ( : > "$LOG_FILE" ) 2>/dev/null; then
        export ESIM_PLUCKY_LOG_ACTIVE=1
        exec > >(tee >(sed -u 's/\x1b\[[0-9;]*[mK]//g' >> "$LOG_FILE")) 2>&1
        trap 'exec 1>&- 2>&-; wait' EXIT
    else
        LOG_FILE="(not writable -- logging disabled)"
    fi
fi

# -----------------------------------------------------------------------------
# State
# -----------------------------------------------------------------------------
stage_done() { [ -f "$STATE_FILE" ] && grep -qx -- "$1" "$STATE_FILE"; }
mark_done()  {
    [ "$DRY_RUN" -eq 1 ] && return 0
    stage_done "$1" || printf '%s\n' "$1" >> "$STATE_FILE"
}

# -----------------------------------------------------------------------------
# apt helpers
# -----------------------------------------------------------------------------

# Packages eSim needs on 25.04. Split by purpose so a failure is legible.
#
# GUI: eSim's own installer leans on pip for some of these, which PEP 668 now
# blocks (ISSUE-01). Taking them from apt sidesteps the problem entirely and
# gives Qt bindings built against this release's Qt.
PKG_GUI=(python3-pyqt5 python3-pyqt5.qsci python3-pyqt5.qtsvg
         libxcb-cursor0 libxcb-xinerama0 qtwayland5)
# Python: python3-venv is what makes the esim_pip shim possible; setuptools
# replaces the deleted python3-distutils (ISSUE-05).
PKG_PY=(python3-dev python3-venv python3-setuptools python3-numpy
        python3-matplotlib)
# Build: ngspice is compiled from source with XSPICE, which needs these.
PKG_BUILD=(build-essential automake autoconf libtool bison flex
           libxaw7-dev libx11-dev libreadline-dev libfl-dev gnupg)
# HDL: ghdl-mcode instead of the ghdl metapackage (ISSUE-06).
PKG_HDL=(ghdl-mcode verilator)

# Split a package list into installable and unavailable. Package names move
# between releases; resolving first turns "apt failed" into "package X does not
# exist on plucky", which is the sentence you actually need in a bug report.
#
# CAUTION -- do not "simplify" this into `apt-cache policy "$p" | grep -q ...`.
# `grep -q` exits at the first match, apt-cache then dies of SIGPIPE (rc 141),
# and because this script runs with `set -o pipefail` the pipeline reports 141
# even though the match succeeded. Every package would be misclassified as
# unavailable. Capture the output first, then match it.
resolve_pkgs() {
    AVAILABLE=(); MISSING=()
    local p pol line cand
    for p in "$@"; do
        pol="$(apt-cache policy "$p" 2>/dev/null)"
        cand=""
        # Parsed in pure bash -- no pipe, so there is no SIGPIPE to trip over.
        while IFS= read -r line; do
            case "$line" in
                *Candidate:*)
                    cand="${line#*Candidate:}"
                    cand="${cand//[[:space:]]/}"
                    break ;;
            esac
        done <<< "$pol"
        if [ -n "$cand" ] && [ "$cand" != "(none)" ]; then
            AVAILABLE+=("$p")
        else
            MISSING+=("$p")
        fi
    done
}

apt_install_group() {
    local label="$1"; shift
    resolve_pkgs "$@"
    info "$label: ${#AVAILABLE[@]} available, ${#MISSING[@]} unavailable"
    if [ "${#MISSING[@]}" -gt 0 ]; then
        warn "not in the $SERIES archive: ${MISSING[*]}"
        note "recorded, not fatal -- see docs/BUGS.md for substitutions"
    fi
    [ "${#AVAILABLE[@]}" -gt 0 ] || return 0
    runcmd sudo apt-get install -y --no-install-recommends "${AVAILABLE[@]}"
}

# -----------------------------------------------------------------------------
# Stages
# -----------------------------------------------------------------------------

stage_preflight() {
    [ -f "$SCRIPTS/esim_preflight.py" ] || { err "missing $SCRIPTS/esim_preflight.py"; return 1; }
    # Exit 1 means "blockers found", which is the expected result on a stock
    # 25.04 box and is exactly why we are here. Only a crash is a real failure.
    runcmd python3 "$SCRIPTS/esim_preflight.py"
    local rc=$?
    # In dry-run nothing executed, so rc says nothing about the host. Reporting
    # "host is already clean" here would be a lie.
    [ "$DRY_RUN" -eq 1 ] && { note "(dry-run: preflight not executed)"; return 0; }
    case "$rc" in
        0) ok "host is already clean" ;;
        1) warn "blocking issues found -- the later stages address them" ;;
        2) info "only non-blocking issues found" ;;
        *) err "preflight crashed (rc=$rc)"; return 1 ;;
    esac
    return 0
}

stage_deps() {
    runcmd sudo apt-get update || warn "apt-get update reported errors (continuing)"
    apt_install_group "GUI / Qt"        "${PKG_GUI[@]}"   || return 1
    apt_install_group "Python runtime"  "${PKG_PY[@]}"    || return 1
    apt_install_group "build toolchain" "${PKG_BUILD[@]}" || return 1
    apt_install_group "HDL toolchain"   "${PKG_HDL[@]}"   || return 1
    return 0
}

stage_patch() {
    local target="$ESIM_ROOT/install-eSim.sh"
    if [ ! -f "$target" ]; then
        err "install-eSim.sh not found at $target"
        note "clone it first:"
        note "  git clone -b installer https://github.com/neekhilsingh/eSim.git $ESIM_ROOT"
        note "or point at an existing clone with --esim-root <path>"
        return 1
    fi
    [ -x "$SCRIPTS/patch-installer.sh" ] || chmod +x "$SCRIPTS/patch-installer.sh" 2>/dev/null
    # Always show the diff before touching anything -- you should be able to
    # read every change this repo makes to FOSSEE's script.
    runcmd bash "$SCRIPTS/patch-installer.sh" --file "$target" --dry-run || return 1
    if ! confirm "apply the patch shown above to $target?"; then
        warn "declined -- stage not marked complete"
        return 1
    fi
    runcmd bash "$SCRIPTS/patch-installer.sh" --file "$target" || return 1
    runcmd bash -n "$target" || { err "patched script is not valid bash"; return 1; }
    ok "patched installer parses cleanly"
    return 0
}

stage_install() {
    local target="$ESIM_ROOT/install-eSim.sh"
    [ -f "$target" ] || { err "no installer at $target -- run the patch stage first"; return 1; }
    grep -q 'PLUCKY-FIX' -- "$target" || {
        err "$target has not been patched -- run: $0 --only patch"; return 1; }

    warn "this stage compiles ngspice and can take 20-40 minutes"
    confirm "run the patched FOSSEE installer now?" || { warn "declined"; return 1; }

    # Prime sudo now so the long build does not stall on a password prompt
    # halfway through and eventually time out.
    [ "$DRY_RUN" -eq 1 ] || sudo -v || return 1

    ( cd -- "$ESIM_ROOT" && runcmd bash ./install-eSim.sh --install )
    local rc=$?
    [ "$rc" -eq 0 ] || { err "the installer exited with rc=$rc -- see $LOG_FILE"; return 1; }
    return 0
}

stage_sources() {
    [ -d "$ESIM_ROOT" ] || { err "no eSim tree at $ESIM_ROOT"; return 1; }
    [ -f "$SCRIPTS/patch_esim_sources.py" ] || { err "missing patch_esim_sources.py"; return 1; }
    # --diff prints the findings report *and* the proposed rewrite in one pass,
    # and exits 1 when there is anything to fix. Running --scan first as well
    # would only print the same report a second time.
    runcmd python3 "$SCRIPTS/patch_esim_sources.py" --root "$ESIM_ROOT" --diff
    local rc=$?
    if [ "$DRY_RUN" -eq 1 ]; then
        note "(dry-run: scan not executed, so nothing is claimed about the tree)"
        return 0
    fi
    if [ "$rc" -eq 0 ]; then
        ok "no removed NumPy/matplotlib name is used in this tree"
        return 0
    fi
    confirm "apply the rewrites shown above (a .bak is kept per file)?" || {
        warn "declined -- eSim will still install, but plotting may raise AttributeError"
        return 0
    }
    runcmd python3 "$SCRIPTS/patch_esim_sources.py" --root "$ESIM_ROOT" --apply || return 1
    return 0
}

stage_verify() {
    [ -f "$SCRIPTS/verify_esim.py" ] || { err "missing verify_esim.py"; return 1; }
    runcmd python3 "$SCRIPTS/verify_esim.py"
    local rc=$?
    [ "$rc" -eq 0 ] || { err "verification failed -- eSim will not start correctly"; return 1; }
    return 0
}

# -----------------------------------------------------------------------------
# Driver
# -----------------------------------------------------------------------------
banner "esim-plucky-fix $VERSION  ::  eSim 2.5 on Ubuntu 25.04"
info "host      : $PRETTY (codename: $SERIES)"
info "eSim root : $ESIM_ROOT"
info "log       : $LOG_FILE"
info "mode      : $([ "$DRY_RUN" -eq 1 ] && echo 'dry-run (nothing will be executed)' || echo 'execute')"
if [ "$SERIES" != "plucky" ] && [ "$SERIES" != "unknown" ]; then
    warn "this repo targets Ubuntu 25.04 'plucky'; you are on '$SERIES'"
    note "the fixes are conditional and should be harmless, but they are"
    note "only validated on plucky"
fi
printf '\n'

# Work out which stages to run.
SELECTED=()
if [ -n "$ONLY" ]; then
    SELECTED=("$ONLY")
else
    started=0
    for s in "${ALL_STAGES[@]}"; do
        [ -n "$FROM" ] && [ "$s" != "$FROM" ] && [ "$started" -eq 0 ] && continue
        started=1
        SELECTED+=("$s")
    done
fi

declare -A RESULT=()
FAILED_STAGE=""
START_ALL=$SECONDS

for s in "${SELECTED[@]}"; do
    if [ "$RESUME" -eq 1 ] && stage_done "$s"; then
        ok "stage '$s' already completed -- skipping (--reset to forget)"
        RESULT[$s]="skipped"
        continue
    fi

    banner "stage: $s  --  ${STAGE_DESC[$s]}"
    t0=$SECONDS
    if "stage_$s"; then
        RESULT[$s]="ok ($((SECONDS - t0))s)"
        mark_done "$s"
        printf '\n'; ok "stage '$s' completed"
    else
        RESULT[$s]="FAILED"
        FAILED_STAGE="$s"
        printf '\n'; err "stage '$s' failed"
        break
    fi
done

# -----------------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------------
banner "summary  (total $((SECONDS - START_ALL))s)"
for s in "${SELECTED[@]}"; do
    r="${RESULT[$s]:-not reached}"
    case "$r" in
        FAILED)  printf '    %-10s %s%s%s\n' "$s" "$R" "$r" "$O" ;;
        skipped) printf '    %-10s %s%s%s\n' "$s" "$D" "$r" "$O" ;;
        ok*)     printf '    %-10s %s%s%s\n' "$s" "$G" "$r" "$O" ;;
        *)       printf '    %-10s %s%s%s\n' "$s" "$D" "$r" "$O" ;;
    esac
done
printf '\n'

if [ -n "$FAILED_STAGE" ]; then
    err "stopped at stage '$FAILED_STAGE'"
    note "everything before it was recorded; pick up where you left off with:"
    note "  $0 --resume"
    note "or re-run just that stage:"
    note "  $0 --only $FAILED_STAGE"
    printf '\n'
    exit 1
fi

if [ "$DRY_RUN" -eq 1 ]; then
    warn "dry-run complete -- nothing was executed"
else
    ok "all requested stages completed"
    note "launch eSim with:  cd $ESIM_ROOT && python3 esim.py"
    note "re-check any time: python3 $SCRIPTS/verify_esim.py"
fi
printf '\n'
exit 0
