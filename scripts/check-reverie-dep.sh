#!/usr/bin/env bash
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.
#
# check-reverie-dep.sh
# --------------------
# PARENT-REPO (dev-hermit) invariant lint.
#
# The dev-hermit workspace pins `reverie` as a git submodule. The `hermit`
# submodule's crates depend on reverie via git dependencies in their
# Cargo.toml files. Those two references must agree: whatever SHA the
# reverie submodule is pinned to should be the SHA that hermit's Cargo.toml
# builds against.
#
# This script:
#   1. Reads the reverie submodule pin recorded in the parent repo tree
#      (`git rev-parse HEAD:reverie`).
#   2. Parses every reverie git dependency in the hermit submodule's
#      Cargo.toml files, extracting `rev = "..."` or `branch = "..."`.
#   3. Compares:
#        - `rev = "<sha>"` deps must equal the submodule pin (hard error).
#        - `branch = "..."` deps are floating; reported as a warning, and
#          (when network is available) resolved via `git ls-remote` to show
#          whether the submodule pin currently matches the branch tip.
#        - All reverie deps must reference the pin consistently.
#   4. Advisory: a hermit checkout on a `speculative` branch should track a
#      speculative reverie, not `main`.
#
# Exit codes:
#   0  consistent (warnings allowed unless --strict)
#   1  mismatch / inconsistency detected (or a warning under --strict)
#   2  usage / environment error

set -uo pipefail

readonly REVERIE_GIT_URL_RE='github\.com[:/]facebookexperimental/reverie(\.git)?'
readonly MIN_SHA_MATCH=7

STRICT=0
QUIET=0
NO_NET=0
REPO_ROOT=""

warnings=0
errors=0

# --- output helpers ----------------------------------------------------------

is_tty() { [[ -t 1 ]]; }
if is_tty; then
    C_RED=$'\033[31m'; C_YEL=$'\033[33m'; C_GRN=$'\033[32m'; C_DIM=$'\033[2m'; C_RST=$'\033[0m'
else
    C_RED=""; C_YEL=""; C_GRN=""; C_DIM=""; C_RST=""
fi

info()  { ((QUIET)) || echo "${C_DIM}info:${C_RST} $*"; }
ok()    { ((QUIET)) || echo "${C_GRN}ok:${C_RST}   $*"; }
warn()  { echo "${C_YEL}warn:${C_RST} $*" >&2; ((warnings++)); }
err()   { echo "${C_RED}error:${C_RST} $*" >&2; ((errors++)); }

usage() {
    cat <<'EOF'
Usage: scripts/check-reverie-dep.sh [OPTIONS]

Verify that the hermit submodule's reverie git dependency (Cargo.toml)
matches the reverie submodule pin recorded in the dev-hermit parent repo.

Options:
  --repo-root PATH   Path to the dev-hermit repo (default: auto-detected).
  --strict           Treat warnings (floating branches, advisories) as errors.
  --no-net           Skip network resolution of branch tips via git ls-remote.
  --install-hook     Install this check as a parent-repo pre-commit hook and exit.
  -q, --quiet        Only print warnings and errors.
  -h, --help         Show this help.

Exit codes: 0 = consistent, 1 = mismatch, 2 = usage/environment error.
EOF
}

# --- git plumbing ------------------------------------------------------------

# Run git, optionally through `with-proxy` for network operations.
git_net() {
    if ((NO_NET)); then
        return 3
    fi
    if command -v with-proxy >/dev/null 2>&1; then
        with-proxy git "$@"
    else
        git "$@"
    fi
}

# True if two SHAs match (allowing a short SHA to prefix a long one).
sha_match() {
    local a=$1 b=$2 short long
    [[ -z $a || -z $b ]] && return 1
    if ((${#a} <= ${#b})); then short=$a; long=$b; else short=$b; long=$a; fi
    ((${#short} >= MIN_SHA_MATCH)) || return 1
    [[ $long == "$short"* ]]
}

# --- argument parsing --------------------------------------------------------

INSTALL_HOOK=0
while (($#)); do
    case $1 in
        --repo-root) REPO_ROOT=${2:-}; shift 2 || { usage >&2; exit 2; } ;;
        --repo-root=*) REPO_ROOT=${1#*=}; shift ;;
        --strict) STRICT=1; shift ;;
        --no-net) NO_NET=1; shift ;;
        --install-hook) INSTALL_HOOK=1; shift ;;
        -q|--quiet) QUIET=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

# --- locate the parent repo --------------------------------------------------

script_dir() { cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd; }

if [[ -z $REPO_ROOT ]]; then
    # scripts/check-reverie-dep.sh lives at <repo>/scripts/, so the repo root
    # is the parent of this script's directory.
    REPO_ROOT="$(cd -- "$(script_dir)/.." && pwd)"
fi

if ! git -C "$REPO_ROOT" rev-parse --show-toplevel >/dev/null 2>&1; then
    err "not a git repo: $REPO_ROOT"
    exit 2
fi
REPO_ROOT="$(git -C "$REPO_ROOT" rev-parse --show-toplevel)"

# --- optional hook installation ----------------------------------------------

if ((INSTALL_HOOK)); then
    hook_dir="$(git -C "$REPO_ROOT" rev-parse --git-path hooks)"
    hook="$hook_dir/pre-commit"
    mkdir -p "$hook_dir"
    if [[ -e $hook ]] && ! grep -q "check-reverie-dep.sh" "$hook" 2>/dev/null; then
        err "a pre-commit hook already exists and does not call check-reverie-dep.sh: $hook"
        err "merge it manually to avoid clobbering existing hooks."
        exit 2
    fi
    cat >"$hook" <<'HOOK'
#!/usr/bin/env bash
# Auto-installed by scripts/check-reverie-dep.sh --install-hook
# Verify the reverie submodule pin agrees with hermit's Cargo.toml deps.
# Blocks the commit only on a hard mismatch (a rev= pin that disagrees with the
# submodule); floating-branch deps remain non-fatal warnings. Add --strict below
# if you want floating branches to block commits too.
repo_root="$(git rev-parse --show-toplevel)"
"$repo_root/scripts/check-reverie-dep.sh" || {
    echo "pre-commit: reverie dependency check failed (run scripts/check-reverie-dep.sh)" >&2
    exit 1
}
HOOK
    chmod +x "$hook"
    ok "installed pre-commit hook: $hook"
    exit 0
fi

# --- 1. reverie submodule pin ------------------------------------------------

if ! git -C "$REPO_ROOT" config --file "$REPO_ROOT/.gitmodules" --get submodule.reverie.path >/dev/null 2>&1; then
    err "no 'reverie' submodule configured in $REPO_ROOT/.gitmodules"
    exit 2
fi
reverie_path="$(git -C "$REPO_ROOT" config --file "$REPO_ROOT/.gitmodules" --get submodule.reverie.path)"

if ! submodule_pin="$(git -C "$REPO_ROOT" rev-parse --verify --quiet "HEAD:$reverie_path")" \
        || [[ ! $submodule_pin =~ ^[0-9a-fA-F]{40}$ ]]; then
    err "could not read reverie submodule pin (git rev-parse HEAD:$reverie_path)"
    err "  is '$reverie_path' committed as a submodule gitlink in HEAD?"
    exit 2
fi
info "reverie submodule pin (parent repo): $submodule_pin"

# Also report the currently checked-out reverie SHA, which may differ from the
# committed pin (shown as a leading '+' in `git submodule status`).
checked_out=""
if [[ -d $REPO_ROOT/$reverie_path/.git || -f $REPO_ROOT/$reverie_path/.git ]]; then
    checked_out="$(git -C "$REPO_ROOT/$reverie_path" rev-parse HEAD 2>/dev/null || true)"
    if [[ -n $checked_out ]] && ! sha_match "$checked_out" "$submodule_pin"; then
        warn "reverie working tree ($checked_out) differs from committed pin ($submodule_pin);"
        warn "  run 'git submodule update' or commit the new pin."
    fi
fi

# --- 2. parse reverie deps from hermit Cargo.toml ----------------------------

hermit_path="$(git -C "$REPO_ROOT" config --file "$REPO_ROOT/.gitmodules" --get submodule.hermit.path 2>/dev/null || echo hermit)"
hermit_dir="$REPO_ROOT/$hermit_path"
if [[ ! -d $hermit_dir ]]; then
    err "hermit submodule directory not found: $hermit_dir"
    exit 2
fi

# Find workspace-member Cargo.toml files, skipping nested worktrees, build
# output and vendored third-party trees.
mapfile -t cargo_files < <(
    find "$hermit_dir" \
        \( -path '*/worktrees/*' -o -path '*/target/*' -o -path '*/third-party/*' -o -path '*/.git/*' \) -prune \
        -o -name Cargo.toml -print | sort
)

declare -A revs_seen=()     # rev sha -> "file:line, file:line"
declare -A branches_seen=() # branch  -> "file:line, file:line"
dep_count=0

for f in "${cargo_files[@]}"; do
    # Each dep is normally a single line: <name> = { ... reverie.git ... }
    while IFS= read -r match; do
        lineno=${match%%:*}
        content=${match#*:}
        # Only lines that are reverie git deps.
        [[ $content =~ $REVERIE_GIT_URL_RE ]] || continue
        # Ignore commented-out lines.
        trimmed=${content#"${content%%[![:space:]]*}"}
        [[ $trimmed == \#* ]] && continue
        ((dep_count++))
        rel="${f#"$REPO_ROOT"/}"
        if [[ $content =~ rev[[:space:]]*=[[:space:]]*\"([0-9a-fA-F]+)\" ]]; then
            sha="${BASH_REMATCH[1]}"
            revs_seen[$sha]+="${rel}:${lineno} "
        elif [[ $content =~ branch[[:space:]]*=[[:space:]]*\"([^\"]+)\" ]]; then
            br="${BASH_REMATCH[1]}"
            branches_seen[$br]+="${rel}:${lineno} "
        elif [[ $content =~ tag[[:space:]]*=[[:space:]]*\"([^\"]+)\" ]]; then
            tg="${BASH_REMATCH[1]}"
            branches_seen["tag:$tg"]+="${rel}:${lineno} "
        else
            warn "reverie dep with neither rev/branch/tag: $rel:$lineno"
        fi
    done < <(grep -nE "$REVERIE_GIT_URL_RE" "$f" 2>/dev/null)
done

if ((dep_count == 0)); then
    err "found no reverie git dependencies under $hermit_dir"
    exit 1
fi
info "found $dep_count reverie git dependency reference(s) across ${#cargo_files[@]} Cargo.toml file(s)"

# --- 3. compare --------------------------------------------------------------

# Pinned (rev=) deps must equal the submodule pin.
for sha in "${!revs_seen[@]}"; do
    locs="${revs_seen[$sha]}"
    if sha_match "$sha" "$submodule_pin"; then
        ok "rev pin matches submodule: $sha"
    else
        err "reverie rev '$sha' does NOT match submodule pin '$submodule_pin'"
        err "  at: ${locs}"
    fi
done

# Branch (floating) deps: warn, and try to resolve the tip for context.
for br in "${!branches_seen[@]}"; do
    locs="${branches_seen[$br]}"
    if [[ $br == tag:* ]]; then
        warn "reverie dep pinned to tag '${br#tag:}' (cannot compare to submodule SHA) at: ${locs}"
        continue
    fi
    warn "reverie dep tracks floating branch '$br' (not a fixed SHA) at: ${locs}"
    url="https://github.com/facebookexperimental/reverie.git"
    if tip_line="$(git_net ls-remote "$url" "refs/heads/$br" 2>/dev/null)" && [[ -n $tip_line ]]; then
        tip="${tip_line%%$'\t'*}"
        tip="${tip%% *}"
        if sha_match "$tip" "$submodule_pin"; then
            info "  branch '$br' tip ($tip) currently equals the submodule pin"
        else
            warn "  branch '$br' tip ($tip) differs from submodule pin ($submodule_pin);"
            warn "  the git build would resolve to the tip, not the pin — consider pinning rev=\"$submodule_pin\""
        fi
    else
        info "  (could not resolve branch tip; offline or --no-net)"
    fi
done

# --- 4. advisory: speculative hermit should track speculative reverie --------

hermit_head_branch=""
if [[ -d $hermit_dir/.git || -f $hermit_dir/.git ]]; then
    # Prefer an actual branch name; fall back to any branch containing HEAD.
    hermit_head_branch="$(git -C "$hermit_dir" symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
    if [[ -z $hermit_head_branch ]]; then
        hermit_head_branch="$(git -C "$hermit_dir" branch -a --contains HEAD 2>/dev/null \
            | grep -oiE 'speculative' | head -n1 || true)"
    fi
fi
if [[ $hermit_head_branch == *speculative* ]]; then
    speculative_tracked=0
    for br in "${!branches_seen[@]}"; do
        [[ $br == *speculative* ]] && speculative_tracked=1
    done
    if ((speculative_tracked == 0)) && ((${#branches_seen[@]} > 0)); then
        warn "hermit is on a speculative branch ('$hermit_head_branch') but reverie deps track: ${!branches_seen[*]}"
        warn "  a speculative hermit should build against the latest green speculative reverie."
    fi
fi

# --- summary -----------------------------------------------------------------

echo
if ((errors > 0)); then
    err "reverie dependency check FAILED: $errors error(s), $warnings warning(s)"
    exit 1
fi
if ((warnings > 0)); then
    if ((STRICT)); then
        err "reverie dependency check failed under --strict: $warnings warning(s)"
        exit 1
    fi
    ok "reverie rev pins consistent; $warnings warning(s) (floating branch/advisory)"
    exit 0
fi
ok "reverie dependency check passed: Cargo.toml pins match submodule pin $submodule_pin"
exit 0
