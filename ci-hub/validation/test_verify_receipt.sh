#!/usr/bin/env bash
# INERT both-sided bracket of the merge gate's LABEL-EVIDENCE CONSUMER.
#
# `verify_receipt.sh` is what stands between a `locally-validated` label and a
# merge-gate acceptance. In the authoritative merge-gate-v4 job it is the ONLY
# way `local_pass` becomes true (merge-gate.yml:806-817); a label the verifier
# cannot dereference leaves `local_pass=false`, which falls through to NO_RESULT
# rather than an acceptance. The label is a cache; the receipt chain is truth.
#
# Everything here runs against fixture receipts in a temp dir via
# --fixture-receipts, so no PR is read, no label is touched, no merge is armed,
# and no GitHub call is made. Planting an artifact that IS an authorization is
# never done against live state (#243).
#
# Both legs are required:
#   NEGATIVE  a bare label, an impersonated or mis-shaped evidence comment, a
#             receipt for another head/repo, and every tampered envelope or
#             ledger field are REFUSED (the guard fires), and
#   POSITIVE  a genuinely backed exact-head receipt is still ACCEPTED, including
#             when buried among junk comments (the guard is not refuse-all).
# Both tallies print with their denominators.
#
# Exit contract: 0 accept, 1 no qualifying receipt, 2 usage/deploy defect
# (unreadable or malformed qualifying-receipt predicate) -- a deploy defect must
# stay loud and distinguishable from an honest refusal.
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
verifier=$script_dir/verify_receipt.sh
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT

sha=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
receipt_commit=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
mkdir -p "$tmp/receipts/$receipt_commit"

# --- PRODUCER DEFINITION BINDING (task bind_receipt_to_producer) -------------
# The verifier reads the registered producer definition from the immutable
# parent commit. This bracket points it at a FIXTURE registry so the cases are
# stable against real blob churn on hermit main -- registering real blobs here
# would make the bracket fail every time validate.sh legitimately changes.
REG_VALIDATE=1111111111111111111111111111111111111111   # stand-in registration
REG_PORTABLE=2222222222222222222222222222222222222222
STALE_VALIDATE=9a9c31ce24abaa764089af7c4cafc820709c4c77 # a REAL older validate.sh blob
cat >"$tmp/producer-registry.json" <<REG
{"registered": {"validate.sh": "$REG_VALIDATE",
                ".github/workflows/ci-portable.yml": "$REG_PORTABLE"}}
REG
export PRODUCER_DEFINITION_REGISTRY=$tmp/producer-registry.json

# Assert a mutation actually changed the receipt before its refusal is believed.
# A mutation harness whose expression silently no-ops reports that the code is
# robust when nothing was tested -- the anchor must be shown to have matched.
mutation_anchor_failures=0
assert_mutated() { # assert_mutated <base> <mutant> <label>
    if cmp -s "$1" "$2"; then
        printf 'BAD  ANCHOR    mutation did not change the receipt: %s\n' "$3" >&2
        mutation_anchor_failures=$((mutation_anchor_failures + 1))
        bracket_fail=1
    fi
}

neg_refused=0
neg_total=0
pos_accepted=0
pos_total=0
bracket_fail=0

make_receipt() { make_receipt_at "$sha" "$1" "$2"; }

make_receipt_at() {
    local sha=$1 executed=$2 output=$3
    jq -cnS --arg sha "$sha" --argjson executed "$executed" \
            --arg reg_validate "$REG_VALIDATE" --arg reg_portable "$REG_PORTABLE" '{
      schema_version: 1,
      repository: "rrnewton/hermit",
      commit: $sha,
      run_id: ($sha + "@2026-08-04T12:00:00Z@test-host"),
      source_log_file: "/tmp/validate.log",
      durable_log_file: "/durable/validate.log",
      log_sha256: ("c" * 64),
      producer: {
        resolved_from: "/fixture/worktree",
        definition: {
          "validate.sh": $reg_validate,
          ".github/workflows/ci-portable.yml": $reg_portable
        }
      },
      ledger_record: {
        schema_version: 1,
        started_at: "2026-08-04T12:00:00Z",
        finished_at: "2026-08-04T12:01:00Z",
        host: "test-host",
        commit: $sha,
        profile: "full",
        selection_mode: "full",
        commit_anchored: true,
        tree_dirty: false,
        result: "pass",
        checks: 5,
        failures: 0,
        executed_tests: $executed,
        filtered_tests: 0,
        log_file: "/tmp/validate.log"
      }
    }' >"$output"
}

write_comments() {
    local path=$1 digest=$2
    jq -cn --arg commit "$receipt_commit" --arg path "$path" --arg digest "$digest" '{
      user: {login: "rrnewton"},
      body: ("[impl agent, ci-hub]\n\n<!-- locally-validated-receipt commit=" + $commit + " path=" + $path + " sha256=" + $digest + " -->")
    } | [[.]]' >"$tmp/comments.json"
}

# One evidence comment with a caller-chosen author login and body prefix, so the
# authenticity clauses (`.user.login == owner`, the `[impl agent, ci-hub]`
# prefix) can be bracketed rather than assumed.
write_comment_as() {
    local login=$1 prefix=$2 path=$3 digest=$4
    jq -cn --arg login "$login" --arg prefix "$prefix" --arg commit "$receipt_commit" \
           --arg path "$path" --arg digest "$digest" '{
      user: {login: $login},
      body: ($prefix + "<!-- locally-validated-receipt commit=" + $commit + " path=" + $path + " sha256=" + $digest + " -->")
    } | [[.]]' >"$tmp/comments.json"
}

# Run the verifier over whatever $tmp/comments.json currently holds and score it.
# expected_rc: an exact code, or "nonzero" when only refusal matters.
run_case() {
    local side=$1 expected_rc=$2 name=$3 query_sha=${4:-$sha}
    local status=0 verdict=OK
    "$verifier" --sha "$query_sha" --comments "$tmp/comments.json" \
        --fixture-receipts "$tmp/receipts" >/dev/null 2>&1 || status=$?
    if [[ $expected_rc == nonzero ]]; then
        [[ $status -ne 0 ]] || verdict=BAD
    else
        [[ $status -eq $expected_rc ]] || verdict=BAD
    fi
    # An acceptance is exit 0; a refusal that somehow exits 0 is not a refusal.
    if [[ $side == NEG && $status -eq 0 ]]; then verdict=BAD; fi
    if [[ $side == POS && $status -ne 0 ]]; then verdict=BAD; fi
    if [[ $side == NEG ]]; then
        neg_total=$((neg_total + 1))
        [[ $verdict == OK ]] && neg_refused=$((neg_refused + 1))
    else
        pos_total=$((pos_total + 1))
        [[ $verdict == OK ]] && pos_accepted=$((pos_accepted + 1))
    fi
    printf '%-4s %-4s rc=%-2s %s\n' "$verdict" "$side" "$status" "$name"
    [[ $verdict == BAD ]] && bracket_fail=1
    return 0
}

# Install $1 as a fixture receipt at $2 and point a well-formed owner comment at
# it, so only the field under test differs from a legitimate receipt.
plant_at() {
    local file=$1 path=$2 digest
    digest=$(sha256sum "$file" | awk '{print $1}')
    mkdir -p "$tmp/receipts/$receipt_commit/$(dirname "$path")"
    cp "$file" "$tmp/receipts/$receipt_commit/$path"
    write_comments "$path" "$digest"
}

# Plant $1 as the receipt for exact head $2 at its own digest path.
plant_for_head() {
    local file=$1 head=$2 digest
    digest=$(sha256sum "$file" | awk '{print $1}')
    plant_at "$file" "validation-receipts/rrnewton/hermit/$head/$digest.json"
}

echo "== NEGATIVE leg: no label, no impersonation, and no tampered receipt authorizes =="

# --- THE BARE LABEL: the named hole. A PR carries locally-validated and its
#     comment thread contains no evidence comment at all. Nothing to dereference.
jq -cn '[[{user: {login: "rrnewton"}, body: "LGTM, landing this."},
          {user: {login: "someone"},  body: "ci please"}]]' >"$tmp/comments.json"
run_case NEG 1 "BARE LABEL: ordinary comments, no receipt marker at all"
jq -cn '[[]]' >"$tmp/comments.json"
run_case NEG 1 "BARE LABEL: empty comment thread"

# --- Well-shaped marker pointing at a receipt that does not exist.
forged_digest=$(printf 'd%.0s' {1..64})
forged_path="validation-receipts/rrnewton/hermit/$sha/$forged_digest.json"
write_comments "$forged_path" "$forged_digest"
run_case NEG 1 "forged: well-shaped marker, nonexistent receipt blob"

# Build one genuine receipt; every authenticity case below reuses it so that the
# ONLY difference from an accepted landing is the clause under test.
make_receipt 12 "$tmp/receipt.json"
digest=$(sha256sum "$tmp/receipt.json" | awk '{print $1}')
path="validation-receipts/rrnewton/hermit/$sha/$digest.json"
mkdir -p "$tmp/receipts/$receipt_commit/$(dirname "$path")"
cp "$tmp/receipt.json" "$tmp/receipts/$receipt_commit/$path"

# --- Comment AUTHENTICITY: a real, resolvable, digest-matching receipt is still
#     not evidence when the comment carrying it is not the owner's ci-hub post.
write_comment_as "attacker" "[impl agent, ci-hub]"$'\n\n' "$path" "$digest"
run_case NEG 1 "IMPERSONATION: valid receipt announced by a non-owner login"
write_comment_as "rrnewton" "" "$path" "$digest"
run_case NEG 1 "no [impl agent, ci-hub] role prefix on the evidence comment"
write_comment_as "rrnewton" "[coordinator, opus-5]"$'\n\n' "$path" "$digest"
run_case NEG 1 "wrong role tag on the evidence comment"

# --- Receipt PATH binding: the path must name this repo and this exact head,
#     and its filename must be the digest it claims.
cross_repo_path="validation-receipts/rrnewton/reverie/$sha/$digest.json"
mkdir -p "$tmp/receipts/$receipt_commit/$(dirname "$cross_repo_path")"
cp "$tmp/receipt.json" "$tmp/receipts/$receipt_commit/$cross_repo_path"
write_comments "$cross_repo_path" "$digest"
run_case NEG 1 "receipt path belongs to a different repository"
misnamed_path="validation-receipts/rrnewton/hermit/$sha/notthedigest.json"
mkdir -p "$tmp/receipts/$receipt_commit/$(dirname "$misnamed_path")"
cp "$tmp/receipt.json" "$tmp/receipts/$receipt_commit/$misnamed_path"
write_comments "$misnamed_path" "$digest"
run_case NEG 1 "receipt filename is not <digest>.json"

# --- The same legitimate receipt must not authorize a different (rebased) head.
write_comments "$path" "$digest"
run_case NEG 1 "STALE HEAD: receipt for the prior head cannot authorize a rebase" \
    ffffffffffffffffffffffffffffffffffffffff

# --- Envelope tampering: byte-level, then field-level.
printf '\n' >>"$tmp/receipts/$receipt_commit/$path"
run_case NEG 1 "tampered receipt body (digest no longer matches)"
cp "$tmp/receipt.json" "$tmp/receipts/$receipt_commit/$path"   # restore

while IFS='|' read -r label expr; do
    [ -n "$label" ] || continue
    jq -cS "$expr" "$tmp/receipt.json" >"$tmp/mut.json"
    assert_mutated "$tmp/receipt.json" "$tmp/mut.json" "ENVELOPE $label"
    verify_digest=$(sha256sum "$tmp/mut.json" | awk '{print $1}')
    plant_at "$tmp/mut.json" "validation-receipts/rrnewton/hermit/$sha/$verify_digest.json"
    run_case NEG 1 "ENVELOPE $label"
done <<'CASES'
wrapper schema_version != 1|.schema_version = 2
repository field mismatch|.repository = "rrnewton/reverie"
receipt .commit != queried head|.commit = "1111111111111111111111111111111111111111"
run_id sha segment forged|.run_id = ("2222222222222222222222222222222222222222@" + .ledger_record.started_at + "@test-host")
run_id started_at segment forged|.run_id = (.commit + "@1999-01-01T00:00:00Z@test-host")
log_sha256 malformed|.log_sha256 = "not-a-digest"
durable_log_file is relative|.durable_log_file = "relative/validate.log"
source_log_file != ledger log_file|.source_log_file = "/tmp/other.log"
ledger commit != receipt commit|.ledger_record.commit = "3333333333333333333333333333333333333333"
ledger profile is not full|.ledger_record.profile = "fast"
ledger selection_mode is not full|.ledger_record.selection_mode = "affected"
ledger result is not pass|.ledger_record.result = "fail"
ledger commit_anchored false|.ledger_record.commit_anchored = false
ledger tree_dirty true|.ledger_record.tree_dirty = true
ledger failures above max|.ledger_record.failures = 1
ledger executed_tests = 0|.ledger_record.executed_tests = 0
ledger counts absent entirely|del(.ledger_record.executed_tests) | del(.ledger_record.filtered_tests)
ledger host absent|del(.ledger_record.host)
run_id host segment disagrees|.run_id = (.commit + "@" + .ledger_record.started_at + "@other-host")
CASES

# --- Count-capable receipts additionally bind the per-node coverage obligation.
make_receipt 12 "$tmp/schema5-base.json"
jq '.ledger_record.schema_version = 5' "$tmp/schema5-base.json" >"$tmp/schema5-missing.json"
plant_for_head "$tmp/schema5-missing.json" "$sha"
run_case NEG 1 "COVERAGE schema5 receipt carries no coverage block"
while IFS='|' read -r label expr; do
    [ -n "$label" ] || continue
    jq -cS "$expr" "$tmp/schema5-missing.json" >"$tmp/cov.json"
    assert_mutated "$tmp/schema5-missing.json" "$tmp/cov.json" "COVERAGE $label"
    plant_for_head "$tmp/cov.json" "$sha"
    run_case NEG 1 "COVERAGE $label"
done <<'CASES'
schema5 zero planned nodes|.ledger_record.coverage = {planned_test_nodes: 0, executed_test_nodes: 0, zero_executed_nodes: [], absent_nodes: []}
schema5 absent node|.ledger_record.coverage = {planned_test_nodes: 2, executed_test_nodes: 1, zero_executed_nodes: [], absent_nodes: ["test.missing"]}
schema5 inert (zero-executed) node|.ledger_record.coverage = {planned_test_nodes: 2, executed_test_nodes: 2, zero_executed_nodes: ["detcore"], absent_nodes: []}
CASES

# The count-capable positive control lives at a SECOND exact head, so the two
# accepted controls are two distinct legitimate landing authorizations rather
# than one row parsed twice. Build it AT that head -- a receipt minted for one
# commit cannot be re-pointed at another (that is the stale-head case above).
sha2=eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee
make_receipt_at "$sha2" 12 "$tmp/schema5-head2-base.json"
jq '.ledger_record.schema_version = 5
    | .ledger_record.coverage = {
        planned_test_nodes: 2, executed_test_nodes: 2,
        zero_executed_nodes: [], absent_nodes: []
      }' "$tmp/schema5-head2-base.json" >"$tmp/schema5-valid.json"

# --- PRODUCER DEFINITION BINDING: a receipt minted by a different/older check
#     definition cannot authorize a landing, even though every other clause --
#     exact head, counts, coverage, host identity, digest -- is impeccable.
#     This is the residual #1579 left open: that check binds the GATE FILE at the
#     run's own sha; nothing bound the PRODUCER that minted the receipt.
while IFS='|' read -r label expr; do
    [ -n "$label" ] || continue
    jq -cS "$expr" "$tmp/receipt.json" >"$tmp/prod.json"
    assert_mutated "$tmp/receipt.json" "$tmp/prod.json" "PRODUCER $label"
    plant_for_head "$tmp/prod.json" "$sha"
    run_case NEG 1 "PRODUCER $label"
done <<CASES
STALE: receipt minted by an older validate.sh|.producer.definition["validate.sh"] = "$STALE_VALIDATE"
STALE: older ci-portable.yml|.producer.definition[".github/workflows/ci-portable.yml"] = "$STALE_VALIDATE"
ABSENT: no producer block at all (a pre-binding receipt)|del(.producer)
ABSENT: producer present but definition missing|.producer = {resolved_from: "/fixture/worktree"}
EMPTY: definition is an empty object|.producer.definition = {}
OMITTED FILE: validate.sh dropped to dodge comparison|del(.producer.definition["validate.sh"])
OMITTED FILE: ci-portable.yml dropped|del(.producer.definition[".github/workflows/ci-portable.yml"])
EXTRA FILE: superset of the registered definition|.producer.definition["extra.sh"] = "$REG_VALIDATE"
NULL: definition explicitly null|.producer.definition = null
TYPE: definition is a string, not a map|.producer.definition = "$REG_VALIDATE"
CASES

# --- Registry deploy defects must stay LOUD (exit 2), and an UNBOUND producer
#     registration must fail closed rather than vacuously accept every producer.
plant_at "$tmp/receipt.json" "$path"
PRODUCER_DEFINITION_REGISTRY=$tmp/no-such-registry.json \
    run_case NEG 2 "DEPLOY DEFECT: producer registry unreadable"
printf 'not json at all\n' >"$tmp/prod-broken.json"
PRODUCER_DEFINITION_REGISTRY=$tmp/prod-broken.json \
    run_case NEG 2 "DEPLOY DEFECT: producer registry is not JSON"
printf '{"registered": {}}\n' >"$tmp/prod-empty.json"
PRODUCER_DEFINITION_REGISTRY=$tmp/prod-empty.json \
    run_case NEG 2 "UNBOUND: producer registry registers no files (must not accept-all)"
printf '{"note": "no registered key"}\n' >"$tmp/prod-nokey.json"
PRODUCER_DEFINITION_REGISTRY=$tmp/prod-nokey.json \
    run_case NEG 2 "UNBOUND: producer registry has no .registered"
printf '{"registered": {"validate.sh": "not-a-blob"}}\n' >"$tmp/prod-badblob.json"
PRODUCER_DEFINITION_REGISTRY=$tmp/prod-badblob.json \
    run_case NEG 2 "DEPLOY DEFECT: registered blob is not 40-hex"

# --- Deploy defect must stay LOUD (exit 2), never a silent lenient fallback and
#     never confused with an honest refusal.
make_receipt 12 "$tmp/good-for-pred.json"
good_pred_digest=$(sha256sum "$tmp/good-for-pred.json" | awk '{print $1}')
plant_at "$tmp/good-for-pred.json" "validation-receipts/rrnewton/hermit/$sha/$good_pred_digest.json"
QUALIFYING_RECEIPT_PREDICATE=$tmp/does-not-exist.json \
    run_case NEG 2 "DEPLOY DEFECT: qualifying-receipt predicate unreadable"
printf '{"counts_schema": 5}\n' >"$tmp/partial-pred.json"
QUALIFYING_RECEIPT_PREDICATE=$tmp/partial-pred.json \
    run_case NEG 2 "DEPLOY DEFECT: qualifying-receipt predicate is partial"
printf 'not json at all\n' >"$tmp/broken-pred.json"
QUALIFYING_RECEIPT_PREDICATE=$tmp/broken-pred.json \
    run_case NEG 2 "DEPLOY DEFECT: qualifying-receipt predicate is not JSON"

echo
echo "== POSITIVE leg: a genuinely backed exact-head receipt is still ACCEPTED =="

# Legacy control 1: one legitimate counted receipt at the exact head.
legacy_accepted=0
plant_at "$tmp/receipt.json" "$path"
run_case POS 0 "legitimate counted receipt at the exact head"
[[ $pos_accepted -eq 1 ]] && legacy_accepted=$((legacy_accepted + 1))

# The guard must survive noise: junk comments and an impersonated marker around
# the genuine one must not stop the real evidence being found.
jq -cn --arg commit "$receipt_commit" --arg path "$path" --arg digest "$digest" '
  [[ {user: {login: "rrnewton"}, body: "LGTM"},
     {user: {login: "attacker"},
      body: ("[impl agent, ci-hub]\n\n<!-- locally-validated-receipt commit=" + $commit + " path=" + $path + " sha256=" + ("e" * 64) + " -->")},
     {user: {login: "rrnewton"},
      body: ("[impl agent, ci-hub]\n\n<!-- locally-validated-receipt commit=" + $commit + " path=" + $path + " sha256=" + $digest + " -->")},
     {user: {login: "bot"}, body: "rerun ci"} ]]' >"$tmp/comments.json"
run_case POS 0 "genuine receipt found among junk and impersonated comments"

# Legacy control 2: the count-capable, complete-coverage receipt at the SECOND
# exact head (built above, at that head).
before_b=$pos_accepted
plant_for_head "$tmp/schema5-valid.json" "$sha2"
run_case POS 0 "schema5 complete-coverage receipt at a second exact head" "$sha2"
[[ $pos_accepted -eq $((before_b + 1)) ]] && legacy_accepted=$((legacy_accepted + 1))

# --- PRODUCER positive leg: the registered definition is still accepted, and
#     the acceptance TRACKS THE REGISTRY rather than being hardcoded. Without
#     the rotation case a check that ignored the registry entirely would still
#     look green here.
plant_at "$tmp/receipt.json" "$path"
run_case POS 0 "PRODUCER receipt carrying the registered current definition"

rot_validate=3333333333333333333333333333333333333333
cat >"$tmp/producer-rotated.json" <<REG
{"registered": {"validate.sh": "$rot_validate",
                ".github/workflows/ci-portable.yml": "$REG_PORTABLE"}}
REG
jq -cS --arg v "$rot_validate" '.producer.definition["validate.sh"] = $v' \
    "$tmp/receipt.json" >"$tmp/rotated-receipt.json"
assert_mutated "$tmp/receipt.json" "$tmp/rotated-receipt.json" "PRODUCER rotation"
plant_for_head "$tmp/rotated-receipt.json" "$sha"
PRODUCER_DEFINITION_REGISTRY=$tmp/producer-rotated.json \
    run_case POS 0 "PRODUCER rotation: receipt matching a NEWLY registered definition is accepted"
# ...and the previously-good receipt is refused under the rotated registration,
# which is the same fact from the other side: the binding is to the CURRENT
# definition, not to any definition that was ever valid.
plant_at "$tmp/receipt.json" "$path"
PRODUCER_DEFINITION_REGISTRY=$tmp/producer-rotated.json \
    run_case NEG 1 "PRODUCER rotation: yesterday's registered definition no longer authorizes"

plant_root=$tmp
rm -rf -- "$plant_root"
if [[ -e $plant_root ]]; then
    echo "FAIL: receipt fixture plant was not deleted cleanly: $plant_root" >&2
    exit 1
fi
trap - EXIT

echo
# Legacy summary line, kept verbatim (same two exact-head landing controls it
# always counted) so existing consumers keep working.
printf 'PASS: %d/2 legitimate exact-head landing receipts accepted; stale-head, forged, tampered, zero-executed, host-mismatch, host-absent, and three incomplete schema5 controls refused; fixture plant deleted cleanly\n' \
    "$legacy_accepted"
printf 'NEGATIVE refusals: %d/%d   POSITIVE acceptances: %d/%d\n' \
    "$neg_refused" "$neg_total" "$pos_accepted" "$pos_total"
# Every mutant must be shown to have actually changed the receipt. A silently
# no-op mutation would otherwise be scored as "the guard refused it", i.e. the
# harness would report robustness it never tested.
printf 'MUTATION ANCHORS: %s\n' \
    "$([[ $mutation_anchor_failures -eq 0 ]] && echo 'all mutants differed from their base' \
       || echo "$mutation_anchor_failures MUTANT(S) DID NOT DIFFER -- results not believable")"
if [[ $bracket_fail -ne 0 ]] || [[ $neg_refused -ne $neg_total ]] ||
   [[ $pos_accepted -ne $pos_total ]] || [[ $legacy_accepted -ne 2 ]] ||
   [[ $mutation_anchor_failures -ne 0 ]]; then
    echo "FAIL: receipt-consumer bracket" >&2
    exit 1
fi
echo "PASS: a bare label, an impersonated or mis-shaped comment, a foreign/stale/tampered receipt, and every broken envelope or ledger clause are all refused; genuine exact-head receipts are still accepted"
