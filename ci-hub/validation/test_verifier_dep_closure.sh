#!/usr/bin/env bash
# INERT both-sided bracket of the merge gate's VERIFIER PROVISIONING step.
#
# The defect this closes (task merge_gate_fetches_the): the gate fetched
# ci-hub/validation/verify_receipt.sh as a LONE FILE into $RUNNER_TEMP. The
# verifier resolves its registries relative to its own location
# ("$script_dir/../validate/*.json"), so in a bare temp directory those paths
# do not exist and the verifier exits 2 on EVERY call. merge-gate.yml reads a
# non-zero exit as "evidence invalid", publishes a failing exact-head check and
# removes the locally-validated label -- so every legitimate receipt silently
# becomes a refusal. Fail-closed, but indistinguishable from mass evidence loss.
#
# The property under test is NOT "two specific files are fetched". It is:
#
#   every registry the PINNED VERIFIER ACTUALLY READS is provisioned beside it,
#   and the provisioned tree lets the verifier run to a verdict.
#
# The direct required set is DERIVED FROM THE VERIFIER ITSELF, never hardcoded
# here. Transitive runtime dependencies are then exercised by running the real
# semantic verifier suite through the provisioned tree. That is what makes this
# a ratchet: a newly read registry, helper, or helper module must be provisioned
# before a legitimate receipt can still be accepted.
#
# BRACKETS:
#   POSITIVE  EVERY real provisioning-step copy supplies the full closure and
#             the provisioned verifier accepts legitimate counted receipts.
#             The same semantic suite plants missing and tampered local-only
#             fixtures and requires their refusal.
#   NEGATIVE  a lone-file provisioning step (the pre-fix shape) is REFUSED by
#             this bracket. Without this leg, a green here would prove nothing.
#
# Nothing dials GitHub. `gh` is stubbed and serves blobs from a local fixture,
# so no pinned commit needs to exist and no token is used. The semantic suite
# plants qualifying evidence only inside its isolated temporary fixture and
# deletes it before returning; no authorization artifact reaches live state
# (#243).
#
# Exit 0 = PASS, 1 = FAIL, 3 = UNAVAILABLE (gate definition not present). The
# parent CI materializes the pinned Hermit checkout before running this bracket;
# any other caller without that dependency receives an honest UNAVAILABLE, not
# PASS. Mirrors test_gate_definition_binding.sh.
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "$script_dir/../.." && pwd)
workflow=${MERGE_GATE_WORKFLOW:-$repo_root/hermit/.github/workflows/merge-gate.yml}
verifier_src=${VERIFY_RECEIPT_SRC:-$repo_root/ci-hub/validation/verify_receipt.sh}
semantic_suite=$repo_root/ci-hub/validation/test_verify_receipt.sh

fail=0
note() { printf '%s\n' "$*"; }
bad() { printf 'FAIL: %s\n' "$*" >&2; fail=1; }

if [[ ! -r $workflow ]]; then
    printf 'UNAVAILABLE: gate definition not readable: %s\n' "$workflow" >&2
    exit 3
fi
if [[ ! -r $verifier_src ]]; then
    printf 'UNAVAILABLE: verifier not readable: %s\n' "$verifier_src" >&2
    exit 3
fi
if [[ ! -x $semantic_suite ]]; then
    printf 'UNAVAILABLE: semantic verifier suite not executable: %s\n' \
        "$semantic_suite" >&2
    exit 3
fi

# ---------------------------------------------------------------- required set
# Derive every direct path the verifier resolves from its own directory. The
# real semantic suite below catches transitive dependencies (for example the
# Rust modules loaded by ../ci-hub) that a source grep cannot safely infer.
# The pattern intentionally matches literal shell source.
# shellcheck disable=SC2016
mapfile -t required < <(
    grep -oE '\$script_dir/\.\./[A-Za-z0-9._/-]+' "$verifier_src" |
        sed 's#^\$script_dir/\.\./##' | sort -u
)
if ((${#required[@]} == 0)); then
    printf 'UNAVAILABLE: derived an EMPTY direct dependency set from %s; the grep no longer matches the verifier and this bracket would be vacuous\n' \
        "$verifier_src" >&2
    exit 3
fi
note "Direct paths the verifier actually resolves: ${required[*]}"

# ------------------------------------------------------------ extract the step
# Pull the `run:` body of one numbered verifier-provisioning step. Every copy is
# executed below; merely counting copies would let the untested copy drift.
extract_step() {
    local workflow_file=$1 wanted=$2
    awk -v wanted="$wanted" '
        /^      - name: Fetch the trusted receipt verifier$/ {
            seen++
            found = (seen == wanted)
            next
        }
        found && /^        run: \|$/ { inbody = 1; next }
        inbody && /^      - name: / { exit }
        inbody { sub(/^          /, ""); print }
    ' "$workflow_file"
}

# The gate has more than one copy of this step; they must not drift apart,
# because a fix applied to only one of them leaves the other exiting 2.
copies=$(grep -c '^      - name: Fetch the trusted receipt verifier$' "$workflow")
if ((copies == 0)); then
    printf 'UNAVAILABLE: no verifier-provisioning step in %s\n' "$workflow" >&2
    exit 3
fi
note "Provisioning step copies in the gate definition: $copies"

# ------------------------------------------------------------------- harness
# Run one provisioning script under a stubbed `gh`. The caller then drives the
# complete receipt semantic suite through the exported verifier tree.
run_provisioning() {
    local body=$1 sandbox=$2
    mkdir -p "$sandbox/bin" "$sandbox/temp"

    # Stubbed gh: serves requested parent-repository paths from this exact local
    # fixture. No token, network, label, status, or live receipt is touched.
    cat >"$sandbox/bin/gh" <<STUB
#!/usr/bin/env bash
set -euo pipefail
# args: api <endpoint> --jq .content
endpoint=\$2
path=\${endpoint#repos/rrnewton/dev-hermit/contents/}
path=\${path%%\\?*}
case \$path in /* | *..*) printf 'stub gh: unsafe fixture path %s\n' "\$path" >&2; exit 1 ;; esac
if [[ \$path == ci-hub/validation/verify_receipt.sh ]]; then
    blob="$verifier_src"
else
    blob="$repo_root/\$path"
fi
[[ -r \$blob ]] || { printf 'stub gh: no fixture blob for %s\n' "\$path" >&2; exit 1; }
base64 -w0 <"\$blob"
STUB
    chmod +x "$sandbox/bin/gh"

    # The step's digest checks must be satisfied against the FIXTURE bytes, or
    # every run would fail for the wrong reason. Rewrite the pinned digests to
    # the fixture's actual ones -- this bracket tests dependency CLOSURE, while
    # digest correctness against the real immutable pin is a separate check.
    local rewritten=$sandbox/step.sh
    printf '%s\n' "$body" >"$rewritten.orig"
    python3 - "$rewritten.orig" "$rewritten" "$repo_root" "$verifier_src" <<'PY'
import hashlib, pathlib, re, sys
src, dst = map(pathlib.Path, sys.argv[1:3])
repo, verifier = pathlib.Path(sys.argv[3]), pathlib.Path(sys.argv[4])
text = pathlib.Path(src).read_text()
lines = text.splitlines()
for i, line in enumerate(lines):
    m = re.search(r'\b[0-9a-f]{64}\b', line)
    if not m:
        continue
    # The fetched repository path precedes its digest in both supported step
    # shapes. Search nearest-first so destination names cannot steal the bind.
    for nearby in reversed(lines[max(0, i - 5):i + 1]):
        paths = re.findall(r'ci-hub/[A-Za-z0-9._/-]+', nearby)
        if paths:
            rel = paths[-1]
            blob = verifier if rel == 'ci-hub/validation/verify_receipt.sh' else repo / rel
            if blob.is_file():
                digest = hashlib.sha256(blob.read_bytes()).hexdigest()
                lines[i] = line.replace(m.group(0), digest)
            break
pathlib.Path(dst).write_text('\n'.join(lines) + '\n')
PY

    : >"$sandbox/github_env"
    PATH="$sandbox/bin:$PATH" RUNNER_TEMP="$sandbox/temp" \
        GITHUB_ENV="$sandbox/github_env" bash "$rewritten" \
        >"$sandbox/out" 2>&1
}

receipt_verifier_from() {
    sed -n 's/^RECEIPT_VERIFIER=//p' "$1/github_env" | tail -1
}

# Does the provisioned verifier reach a VERDICT rather than a deploy error?
# rc 2 == it could not resolve something -> the defect. rc 0/1 == a verdict.
probe_verdict() {
    local v=$1 sandbox=$2 rc
    printf '[]\n' >"$sandbox/comments.json"
    set +e
    "$v" --repo rrnewton/hermit \
        --sha 0000000000000000000000000000000000000000 \
        --comments "$sandbox/comments.json" >"$sandbox/probe.out" 2>&1
    rc=$?
    set -e
    printf '%s' "$rc"
}

run_semantic_suite() {
    local v=$1 sandbox=$2 vdir digest rc
    vdir=$(cd -- "$(dirname -- "$v")" && pwd)
    digest=$vdir/../ci-hub
    set +e
    PATH="$sandbox/bin:$PATH" VERIFY_RECEIPT="$v" \
        RECEIPT_DIGEST="$digest" "$semantic_suite" \
        >"$sandbox/semantic.out" 2>&1
    rc=$?
    set -e
    if ((rc != 0)); then
        return 1
    fi
    grep -F '2/2 legitimate exact-head landing receipts accepted' \
        "$sandbox/semantic.out" >/dev/null &&
        grep -F 'forged' "$sandbox/semantic.out" >/dev/null &&
        grep -F 'tampered' "$sandbox/semantic.out" >/dev/null &&
        grep -F 'fixture plant deleted cleanly' "$sandbox/semantic.out" >/dev/null
}

# ------------------------------------------------------- POSITIVE: every real step
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT
for ((copy = 1; copy <= copies; copy++)); do
    step_script=$(extract_step "$workflow" "$copy")
    positive=$tmp/positive-$copy
    if [[ -z ${step_script//[[:space:]]/} ]]; then
        bad "could not extract verifier-provisioning step copy $copy from $workflow"
        continue
    fi
    if ! run_provisioning "$step_script" "$positive"; then
        bad "provisioning step copy $copy did not complete: $(tail -3 "$positive/out")"
        continue
    fi
    v=$(receipt_verifier_from "$positive")
    if [[ -z $v || ! -x $v ]]; then
        bad "provisioning step copy $copy exported no executable RECEIPT_VERIFIER"
        continue
    fi

    missing=()
    vdir=$(cd -- "$(dirname -- "$v")" && pwd)
    for relative in "${required[@]}"; do
        [[ -r $vdir/../$relative ]] || missing+=("$relative")
    done
    if ((${#missing[@]})); then
        bad "provisioning step copy $copy has incomplete direct closure: ${missing[*]}"
        continue
    fi
    note "PASS positive copy $copy: all ${#required[@]} direct dependencies resolve"

    if run_semantic_suite "$v" "$positive"; then
        note "PASS positive copy $copy: legitimate receipts accepted; missing/tampered fixtures refused"
    else
        bad "provisioned verifier copy $copy failed the real semantic suite: $(tail -4 "$positive/semantic.out")"
    fi
done

# ------------------- NEGATIVE: the pre-fix lone-file shape must be REFUSED
negative=$tmp/negative
# This is intentionally literal planted shell source.
# shellcheck disable=SC2016
lone_step='set -euo pipefail
verifier="$RUNNER_TEMP/verify-local-validation-receipt.sh"
gh api "repos/rrnewton/dev-hermit/contents/ci-hub/validation/verify_receipt.sh?ref=deadbeef" --jq .content | tr -d "\n" | base64 --decode >"$verifier"
chmod +x "$verifier"
echo "RECEIPT_VERIFIER=$verifier" >>"$GITHUB_ENV"'

if ! run_provisioning "$lone_step" "$negative"; then
    bad "the lone-file control could not run at all; the negative leg proves nothing"
else
    v=$(receipt_verifier_from "$negative")
    detected=0
    vdir=$(cd -- "$(dirname -- "$v")" && pwd)
    for relative in "${required[@]}"; do
        [[ -r $vdir/../$relative ]] || detected=1
    done
    rc=$(probe_verdict "$v" "$negative")
    if ((detected == 1)) && [[ $rc == 2 ]] && \
       ! run_semantic_suite "$v" "$negative"; then
        note "PASS negative: lone-file provisioning is detected and cannot pass the real receipt suite"
    else
        bad "lone-file provisioning was NOT detected (missing=$detected rc=$rc) -- this bracket is inert"
    fi
fi

if ((fail)); then
    printf 'verifier-dep-closure bracket: FAILED\n' >&2
    exit 1
fi
printf 'verifier-dep-closure bracket: PASSED (%d direct dependencies, %d step copies)\n' \
    "${#required[@]}" "$copies"
