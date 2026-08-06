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
# The required set is DERIVED FROM THE VERIFIER ITSELF, never hardcoded here.
# That is what makes this a ratchet: when the verifier gains a registry (e.g.
# producer-definition.json from bind_receipt_to_producer), this bracket starts
# failing until the workflow fetches it too, instead of the gate exiting 2 in
# production. A hardcoded list would have to be remembered, which is exactly
# the thing that was not remembered.
#
# BRACKETS:
#   POSITIVE  the real provisioning step supplies the full closure, the
#             verifier runs to a VERDICT (not exit 2), and it still REFUSES a
#             receipt-less input (rc 1) -- so the fix did not turn the gate
#             into an always-pass.
#   NEGATIVE  a lone-file provisioning step (the pre-fix shape) is REFUSED by
#             this bracket. Without this leg, a green here would prove nothing.
#
# Nothing dials GitHub. `gh` is stubbed and serves blobs from a local fixture,
# so no pinned commit needs to exist and no token is used. A gate-satisfying
# artifact is itself an authorization, so none is ever planted on live state
# (#243).
#
# Exit 0 = PASS, 1 = FAIL, 3 = UNAVAILABLE (gate definition not present; the
# parent's own CI checks out no submodules, so there the honest verdict is
# UNAVAILABLE, not PASS). Mirrors test_gate_definition_binding.sh.
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "$script_dir/../.." && pwd)
workflow=${MERGE_GATE_WORKFLOW:-$repo_root/hermit/.github/workflows/merge-gate.yml}
verifier_src=${VERIFY_RECEIPT_SRC:-$repo_root/ci-hub/validation/verify_receipt.sh}

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

# ---------------------------------------------------------------- required set
# Derive the registries the verifier reads from its own source. Matches
# "$script_dir/../validate/<name>" regardless of surrounding quoting.
mapfile -t required < <(
    grep -oE '\$script_dir/\.\./validate/[A-Za-z0-9._-]+' "$verifier_src" |
        sed 's#.*/##' | sort -u
)
if ((${#required[@]} == 0)); then
    printf 'UNAVAILABLE: derived an EMPTY registry set from %s; the grep no longer matches the verifier and this bracket would be vacuous\n' \
        "$verifier_src" >&2
    exit 3
fi
note "Registries the verifier actually reads: ${required[*]}"

# ------------------------------------------------------------ extract the step
# Pull the `run:` body of the first "Fetch the trusted receipt verifier" step.
extract_step() {
    awk '
        /^      - name: Fetch the trusted receipt verifier$/ { found = 1; next }
        found && /^        run: \|$/ { inbody = 1; next }
        inbody && /^      - name: / { exit }
        inbody { sub(/^          /, ""); print }
    ' "$1"
}

step_script=$(extract_step "$workflow")
if [[ -z ${step_script//[[:space:]]/} ]]; then
    printf 'UNAVAILABLE: could not extract the verifier-provisioning step from %s\n' "$workflow" >&2
    exit 3
fi

# The gate has more than one copy of this step; they must not drift apart,
# because a fix applied to only one of them leaves the other exiting 2.
copies=$(grep -c '^      - name: Fetch the trusted receipt verifier$' "$workflow")
note "Provisioning step copies in the gate definition: $copies"

# ------------------------------------------------------------------- harness
# Run one provisioning script under a stubbed `gh` and report whether the
# verifier it produced can reach a verdict. Echoes the RECEIPT_VERIFIER path.
run_provisioning() {
    local body=$1 sandbox=$2
    mkdir -p "$sandbox/bin" "$sandbox/temp" "$sandbox/blobs/ci-hub/validation" \
        "$sandbox/blobs/ci-hub/validate"

    # Fixture blobs: the real verifier, plus a stand-in for every registry it
    # reads. Content only has to be resolvable and well-formed enough that the
    # verifier gets past "unreadable" and reaches its own logic.
    cp "$verifier_src" "$sandbox/blobs/ci-hub/validation/verify_receipt.sh"
    local name
    for name in "${required[@]}"; do
        if [[ -r $repo_root/ci-hub/validate/$name ]]; then
            cp "$repo_root/ci-hub/validate/$name" "$sandbox/blobs/ci-hub/validate/$name"
        else
            printf '{}\n' >"$sandbox/blobs/ci-hub/validate/$name"
        fi
    done

    # Stubbed gh: serves `contents/<path>?ref=...` from the fixture as base64,
    # ignoring the ref entirely, so no commit needs to exist.
    cat >"$sandbox/bin/gh" <<STUB
#!/usr/bin/env bash
set -euo pipefail
# args: api <endpoint> --jq .content
endpoint=\$2
path=\${endpoint#repos/rrnewton/dev-hermit/contents/}
path=\${path%%\\?*}
blob="$sandbox/blobs/\$path"
[[ -r \$blob ]] || { printf 'stub gh: no fixture blob for %s\n' "\$path" >&2; exit 1; }
base64 -w0 <"\$blob"
STUB
    chmod +x "$sandbox/bin/gh"

    # The step's digest checks must be satisfied against the FIXTURE bytes, or
    # every run would fail for the wrong reason. Rewrite the pinned digests to
    # the fixture's actual ones -- this bracket tests dependency CLOSURE, while
    # digest correctness against the real pin is a separate concern.
    local rewritten=$sandbox/step.sh
    printf '%s\n' "$body" >"$rewritten.orig"
    python3 - "$rewritten.orig" "$rewritten" "$sandbox/blobs" <<'PY'
import hashlib, pathlib, re, sys
src, dst, blobs = sys.argv[1], sys.argv[2], pathlib.Path(sys.argv[3])
text = pathlib.Path(src).read_text()
digest_of = {}
for f in blobs.rglob('*'):
    if f.is_file():
        digest_of[f.name] = hashlib.sha256(f.read_bytes()).hexdigest()
# Replace every 64-hex literal with the digest of the blob named nearby.
lines = text.splitlines()
for i, line in enumerate(lines):
    m = re.search(r'\b[0-9a-f]{64}\b', line)
    if not m:
        continue
    window = ' '.join(lines[max(0, i - 2):i + 3])
    for name, d in digest_of.items():
        if name in window:
            lines[i] = line.replace(m.group(0), d)
            break
pathlib.Path(dst).write_text('\n'.join(lines) + '\n')
PY

    (
        export PATH="$sandbox/bin:$PATH"
        export RUNNER_TEMP="$sandbox/temp"
        export GITHUB_ENV="$sandbox/github_env"
        : >"$GITHUB_ENV"
        bash "$rewritten"
    ) >"$sandbox/out" 2>&1
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

# ------------------------------------------------------- POSITIVE: the real step
positive=$(mktemp -d); trap 'rm -rf -- "$positive" "${negative:-}"' EXIT
if ! run_provisioning "$step_script" "$positive"; then
    bad "the real provisioning step did not complete: $(tail -3 "$positive/out")"
else
    v=$(receipt_verifier_from "$positive")
    if [[ -z $v || ! -x $v ]]; then
        bad "the real provisioning step exported no executable RECEIPT_VERIFIER"
    else
        missing=()
        vdir=$(cd -- "$(dirname -- "$v")" && pwd)
        for name in "${required[@]}"; do
            [[ -r $vdir/../validate/$name ]] || missing+=("$name")
        done
        if ((${#missing[@]})); then
            bad "provisioned closure is incomplete; unresolvable registries: ${missing[*]}"
        else
            note "PASS positive: all ${#required[@]} registr(y|ies) resolve beside the verifier"
        fi
        rc=$(probe_verdict "$v" "$positive")
        if [[ $rc == 2 ]]; then
            bad "provisioned verifier still exits 2 (deploy error, not a verdict): $(head -2 "$positive/probe.out")"
        elif [[ $rc == 1 ]]; then
            note "PASS positive: verifier reached a verdict AND refused a receipt-less input (rc 1)"
        elif [[ $rc == 0 ]]; then
            bad "verifier ACCEPTED a receipt-less input (rc 0) -- the always-pass direction (#323)"
        else
            bad "verifier exited $rc, which is neither a verdict nor the known deploy error"
        fi
    fi
fi

# ------------------- NEGATIVE: the pre-fix lone-file shape must be REFUSED
negative=$(mktemp -d)
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
    for name in "${required[@]}"; do
        [[ -r $vdir/../validate/$name ]] || detected=1
    done
    rc=$(probe_verdict "$v" "$negative")
    if ((detected == 1)) && [[ $rc == 2 ]]; then
        note "PASS negative: lone-file provisioning is detected (missing closure) and exits 2 as expected"
    else
        bad "lone-file provisioning was NOT detected (missing=$detected rc=$rc) -- this bracket is inert"
    fi
fi

if ((fail)); then
    printf 'verifier-dep-closure bracket: FAILED\n' >&2
    exit 1
fi
printf 'verifier-dep-closure bracket: PASSED (%d registr(y|ies), %d step copies)\n' \
    "${#required[@]}" "$copies"
