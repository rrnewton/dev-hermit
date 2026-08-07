# Parent-main reconcile: 6 receipt/merge-gate conflicts needing their owners

Produced by `hermit-w12` while trying to publish parent main (task
`fix-orc-hermit-msg-delivery-ack-and-post-1.0-pane-detection`). NOT resolved —
every conflict is two independent hardenings of the same authority, and picking
a side silently drops one of them. That is a fake-green vector, so it needs the
owning agents.

## Reproduce

```
git worktree add --detach <path> main
git -C <path> merge --no-commit --no-ff origin/main
```

## Shape

| file | hunks | lines | ours | theirs |
|---|---|---|---|---|
| `ci-hub/lib/qualifying_receipt.rs` | 2 | 37 | 724302f | cb646b8 |
| `ci-hub/validation/publish_receipt.py` | 2 | 79 | 291a7a2 | cb646b8 |
| `ci-hub/validation/test_publish_receipt.py` | 3 | 83 | 291a7a2 | cb646b8 |
| `ci-hub/validation/test_verify_receipt.sh` | 6 | 470 | 291a7a2 | cb646b8 |
| `ci-hub/validation/verify_receipt.sh` | 1 | 60 | 291a7a2 | cb646b8 |
| `scripts/test_primary_checkout.py` | 3 | 42 | a203f41 | 7f1a8ce |

`theirs` is dominated by `cb646b8` — "coordinator: unify skills, validation
receipts, and worktree safety (#38)", already on origin/main.
`ours` is dominated by `291a7a2` — "ci-hub: bind the validate receipt to its
producer definition", which per task `merge_gate_fetches_the` also lives on the
unlanded branch `origin/staging/bind-receipt-to-producer`.

## The semantic question, per file

- **`verify_receipt.sh`** — ours adds PRODUCER BINDING
  (`.producer.definition == $registered_producer`, exact map equality). Theirs
  adds a structural row validator (`current_structural_row`: schema >= 4, gates,
  counts, timestamps). Both are additive hardenings of the SAME jq predicate.
  Correct resolution is a UNION; dropping either weakens the gate.
- **`qualifying_receipt.rs`** — ours changed the return type from `bool` to a
  typed `Qualification` enum carrying refusal reasons (commit `1f68f16`, "make
  the coverage verdict typed and fail-closed"). Theirs kept `bool`
  (`value_qualifies`). A type-signature divergence: theirs' logic must be
  ported INTO the typed enum, not either side taken whole.
- **`test_primary_checkout.py`** — two independent fixture refactors
  (`_ParentWorkspaceFixture` vs `_PrimaryFixture`); ours adds helpers
  (`dirty_hermit_worktree`, `drift_hermit_commit`) theirs lacks. Union the
  helpers into the surviving fixture.
- **`publish_receipt.py` / `test_publish_receipt.py` / `test_verify_receipt.sh`**
  — the mint side and the brackets for both of the above. They must be resolved
  consistently with the two decisions above, then re-run.

## Why this blocks publication

Parent main is 33 ahead / 46 behind. These 6 files are the only thing between
local main and a push. Measured non-destructively with `git merge-tree`:

- `a203f41` (hermit-w9's reconcile base) vs current origin/main -> **7** conflicts
- `9e577f8` (current main tip)          vs current origin/main -> **6** conflicts

So there is no cheap base to reconcile from — w9's staged merge shows 0 conflicts
only because it targets `6ed3002c`, which is 5 commits behind current origin/main.
Whoever reconciles must resolve these 6 either way.

## Full conflict hunks


### `ci-hub/lib/qualifying_receipt.rs`
```
<<<<<<< HEAD
    if count_capable {
        if !executed_ok {
            return Qualification::Refused("executed_tests below executed_tests_min");
        }
        // Coverage is not required when the predicate turns it off or the row
        // predates it; otherwise it must be present AND satisfied. `None` here
        // is a writer defect, and is refused rather than skipped.
        if !pred.coverage.per_node || schema < pred.coverage.applies_at_schema_min {
            return Qualification::FullCoverage;
        }
        match row.coverage.as_ref().map(coverage_verdict) {
            Some(CoverageVerdict::Satisfied) => Qualification::FullCoverage,
            Some(CoverageVerdict::Unsatisfied) => {
                Qualification::Refused("coverage reported inert or absent nodes")
            }
            Some(CoverageVerdict::Unavailable(why)) => Qualification::Refused(why),
            None => Qualification::Refused(
                "count-capable receipt carries no coverage object; unavailable coverage \
                 is refused, never accepted",
            ),
        }
=======
    let value_qualifies = if count_capable {
        let coverage_ok = !pred.coverage.per_node
            || schema < pred.coverage.applies_at_schema_min
            || row.coverage.as_ref().is_some_and(coverage_satisfied);
        executed_ok && coverage_ok
>>>>>>> origin/main
<<<<<<< HEAD
        Qualification::Refused("no counts: receipt proves nothing")
=======
        false
    };
    if !value_qualifies {
        return false;
>>>>>>> origin/main
```

### `ci-hub/validation/verify_receipt.sh`
```
<<<<<<< HEAD
        --argjson gate_filtered "$p_gate_filtered" \
        --argjson registered_producer "$p_producer" '
        .schema_version == 1
        and .repository == $repo
        and .commit == $sha
        # PRODUCER BINDING: the receipt must name the check definition that
        # produced it, and that definition must be the registered current one.
        # EXACT equality of the whole map -- not a subset check -- so a receipt
        # cannot drop a file to escape the comparison, and not "compare only
        # what is present", which would make an absent block a free pass.
        and ((.producer.definition // null) == $registered_producer)
        # Host-in-identity (Req2): the run_id binds sha + started_at + producing
        # host, so the ledger host cannot be swapped without breaking identity.
        and (.ledger_record.host | (type == "string") and (length > 0))
=======
        --argjson gate_filtered "$p_gate_filtered" '
        def integer:
          if type == "number" then . == floor else false end;
        def nonempty_string:
          if type == "string" then test("\\S") else false end;
        def selected_identity_valid:
          .digest_algorithm == "sha256"
          and .canonicalization == "serde_json::to_vec(HistoryRow)-v1"
          and (.digest | test("^[0-9a-f]{64}$"));
        def current_structural_row($sha; $repo):
          .ledger_record as $row
          | $repo == "rrnewton/hermit"
          and ($row.schema_version | integer and . >= 4)
          and (
            $row.repo == "hermit"
            or $row.repo == "rrnewton/hermit"
            or ($row.schema_version == 4 and $row.repo == null)
          )
          and $row.commit == $sha
          and ($row.tree | type == "string" and test("^[0-9A-Fa-f]{40}$"))
          and $row.raw_result == "pass"
          and $row.exit_code == 0
          and ($row.checks | integer and . > 0)
          and ($row.gates_run | integer and . >= $row.gates_expected)
          and ($row.gates_expected | integer and . > 0)
          and $row.checks == $row.gates_run
          and ($row.gates | type == "array" and length == $row.gates_run)
          and all($row.gates[];
            (.name | nonempty_string)
            and .result == "pass"
            and .exit_code == 0
          )
          and ($row.executed_tests | integer)
          and ($row.filtered_tests | integer and . >= 0)
          and ($row.started_at | nonempty_string)
          and ($row.finished_at | nonempty_string)
          and ($row.host | nonempty_string)
          and ($row.slot | nonempty_string)
          and ($row.log_file | nonempty_string);
        .schema_version == 1
        and .repository == $repo
        and .commit == $sha
        and (.ledger_record.host | nonempty_string)
>>>>>>> origin/main
```

### `ci-hub/validation/publish_receipt.py`
```
<<<<<<< HEAD
def producer_registry_path() -> Path:
    override = os.environ.get("PRODUCER_DEFINITION_REGISTRY")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[1] / "validate" / "producer-definition.json"


def registered_producer() -> dict[str, str]:
    """The registered current producer definition (file -> git blob)."""
    path = producer_registry_path()
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot read producer-definition registry {path}: {error}")
    registered = value.get("registered")
    if not isinstance(registered, dict) or not registered:
        fail(f"malformed producer-definition registry (no non-empty .registered): {path}")
    return registered


def producer_definition(row: dict[str, Any], sha: str) -> dict[str, Any]:
    """Identify the check definition that produced this row.

    Derived from the VALIDATED COMMIT (`git rev-parse <sha>:<path>`), not
    self-reported by the producer: the blobs are a property of the commit that
    was validated, so a producer cannot claim a definition it did not run
    without also changing the commit under test.  Failure to resolve is fatal --
    a receipt that cannot name its producer must not be minted at all, because a
    producer-less receipt is exactly what the consumer now refuses.
    """
    checkout = row.get("cwd")
    if not checkout or not Path(checkout).is_dir():
        fail(
            "ledger row has no usable `cwd`, so the producing check definition "
            f"cannot be resolved for {sha}"
        )
    definition: dict[str, str] = {}
    for relative in sorted(registered_producer()):
        result = subprocess.run(
            ["git", "-C", str(checkout), "rev-parse", f"{sha}:{relative}"],
            text=True,
            capture_output=True,
        )
        blob = result.stdout.strip()
        if result.returncode != 0 or len(blob) != 40:
            fail(
                f"cannot resolve producer blob for {relative} at {sha} in {checkout}: "
                f"{result.stderr.strip() or 'no output'}"
            )
        definition[relative] = blob
    return {"resolved_from": str(checkout), "definition": definition}


def build_receipt(repo: str, sha: str, row: dict[str, Any], durable_log: Path) -> tuple[dict[str, Any], bytes, str]:
=======
def build_receipt(
    repo: str,
    sha: str,
    row: dict[str, Any],
    durable_log: Path,
    *,
    selected_digest: str,
    canonicalization: str,
) -> tuple[dict[str, Any], bytes, str]:
>>>>>>> origin/main
<<<<<<< HEAD
        # Producer binding: WHICH check definition produced this receipt, at
        # which head. The consumer requires this to equal the registered
        # current definition, so a receipt minted by an older/foreign
        # validate.sh cannot authorize a landing (task bind_receipt_to_producer).
        "producer": producer_definition(row, sha),
=======
        "selected_receipt_identity": {
            "digest_algorithm": "sha256",
            "canonicalization": canonicalization,
            "digest": selected_digest,
        },
>>>>>>> origin/main
```

### `scripts/test_primary_checkout.py`
```
<<<<<<< HEAD
import unittest.mock
=======
from unittest.mock import patch
>>>>>>> origin/main
<<<<<<< HEAD
class _ParentWorkspaceFixture(unittest.TestCase):
    """A miniature dev-hermit parent: three product gitlinks, one Reverie pin."""
=======
class _PrimaryFixture(unittest.TestCase):
    """Shared parent+products fixture. Holds no tests of its own so that the
    suites below do not re-run each other's cases."""
>>>>>>> origin/main
<<<<<<< HEAD
    def dirty_hermit_worktree(self) -> None:
        """Leave someone else's uncommitted work under hermit/, pin files included.

        Covers all three shapes seen in the field: a rewritten manifest rev, a
        tracked pin file missing from the working tree, and untracked scratch.
        """
        reverie_head = git(self.root / "reverie", "rev-parse", "HEAD")
        manifest = self.root / "hermit" / "Cargo.toml"
        manifest.write_text(manifest.read_text().replace(reverie_head, "b" * 40))
        lock = self.root / "hermit" / "Cargo.lock"
        lock.write_text(lock.read_text().replace(reverie_head, "0" * 40))
        (self.root / "hermit" / "liteinst-runtime-build" / "Cargo.lock").unlink()
        (self.root / "hermit" / "scratch-note.md").write_text("someone else's work\n")

    def drift_hermit_commit(self) -> str:
        """Commit a real Reverie pin drift IN the Hermit submodule. Returns its SHA."""
        lock = self.root / "hermit" / "Cargo.lock"
        reverie_head = git(self.root / "reverie", "rev-parse", "HEAD")
        lock.write_text(lock.read_text().replace(reverie_head, "0" * 40))
        git(self.root / "hermit", "add", "Cargo.lock")
        git(self.root / "hermit", "commit", "-m", "drift the lock")
        return git(self.root / "hermit", "rev-parse", "HEAD")


class PrimaryCheckoutTests(_ParentWorkspaceFixture):
=======
class PrimaryCheckoutTests(_PrimaryFixture):
>>>>>>> origin/main
```

### `ci-hub/validation/test_publish_receipt.py`
```
<<<<<<< HEAD
            repo, sha = make_producer_checkout(Path(directory))
            row = self.row(Path(directory))
            row["commit"] = sha
            row["cwd"] = str(repo)
            selected = MODULE.qualifying_row([row], sha)
            durable = MODULE.preserve_log(Path(directory) / "ledger.jsonl", sha, selected)
            receipt, body, digest = MODULE.build_receipt(
                "rrnewton/hermit", sha, selected, durable
            )
            self.assertEqual(receipt["ledger_record"]["executed_tests"], 12)
            self.assertEqual(receipt["commit"], sha)
=======
            root = Path(directory)
            row = self.row(root)
            canonical_row = self.canonical_row(row)
            digest = hashlib.sha256(canonical_row).hexdigest()
            selected = MODULE.selected_record(
                canonical_row,
                sha=self.SHA,
                expected_digest=digest,
                canonicalization=MODULE.RECEIPT_CANONICALIZATION,
            )
            durable = MODULE.preserve_log(root / "ledger.jsonl", self.SHA, selected)
            receipt, body, artifact_digest = MODULE.build_receipt(
                "rrnewton/hermit",
                self.SHA,
                selected,
                durable,
                selected_digest=digest,
                canonicalization=MODULE.RECEIPT_CANONICALIZATION,
            )
            self.assertEqual(receipt["ledger_record"]["executed_tests"], 12)
            self.assertEqual(receipt["commit"], self.SHA)
            self.assertEqual(receipt["selected_receipt_identity"]["digest"], digest)
>>>>>>> origin/main
<<<<<<< HEAD
            row_a = self.row(root)
            row_a["commit"] = sha
            row_a["cwd"] = str(repo)
            row_a["host"] = "host-a"
            row_b = dict(row_a)
            row_b["host"] = "host-b"
            self.assertEqual(row_a["started_at"], row_b["started_at"])
            receipt_a, _, _ = MODULE.build_receipt("rrnewton/hermit", sha, row_a, durable)
            receipt_b, _, _ = MODULE.build_receipt("rrnewton/hermit", sha, row_b, durable)
=======
            row_a = self.row(root, host="host-a")
            row_b = dict(row_a)
            row_b["host"] = "host-b"
            self.assertEqual(row_a["started_at"], row_b["started_at"])
            digest_a = hashlib.sha256(self.canonical_row(row_a)).hexdigest()
            digest_b = hashlib.sha256(self.canonical_row(row_b)).hexdigest()
            receipt_a, _, _ = MODULE.build_receipt(
                "rrnewton/hermit",
                self.SHA,
                row_a,
                durable,
                selected_digest=digest_a,
                canonicalization=MODULE.RECEIPT_CANONICALIZATION,
            )
            receipt_b, _, _ = MODULE.build_receipt(
                "rrnewton/hermit",
                self.SHA,
                row_b,
                durable,
                selected_digest=digest_b,
                canonicalization=MODULE.RECEIPT_CANONICALIZATION,
            )
>>>>>>> origin/main
<<<<<<< HEAD
            # PRE-EXISTING BREAKAGE, fixed in passing: 19a219f moved the
            # count-schema boundary into the shared qualifying-receipt predicate
            # and deleted publish_receipt.COUNTS_SCHEMA, but left this reference
            # behind, so this test had been erroring (not failing) ever since.
            # Read it from the one canonical source, as that refactor intended.
            row["schema_version"] = MODULE.qualifying_receipt.active()["counts_schema"]
=======
            row["schema_version"] = MODULE.qualifying_receipt.active()[
                "counts_schema"
            ]
            canonical_row = self.canonical_row(row)
>>>>>>> origin/main
```

### `ci-hub/validation/test_verify_receipt.sh`
```
<<<<<<< HEAD
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
=======
make_receipt() {
    local executed=$1 output=$2
    local raw=$tmp/receipt-build.json selected
    jq -cnS --arg sha "$sha" --argjson executed "$executed" '{
>>>>>>> origin/main
<<<<<<< HEAD
      producer: {
        resolved_from: "/fixture/worktree",
        definition: {
          "validate.sh": $reg_validate,
          ".github/workflows/ci-portable.yml": $reg_portable
        }
=======
      selected_receipt_identity: {
        digest_algorithm: "sha256",
        canonicalization: "serde_json::to_vec(HistoryRow)-v1",
        digest: "pending"
>>>>>>> origin/main
<<<<<<< HEAD
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
=======
verify_file() {
    local file=$1 expected=$2 label=$3 role_tag=${4:-'[impl agent, ci-hub]'}
    local file_digest file_path status=0
    file_digest=$(sha256sum "$file" | awk '{print $1}')
    file_path="validation-receipts/rrnewton/hermit/$sha/$file_digest.json"
    mkdir -p "$tmp/receipts/$receipt_commit/$(dirname "$file_path")"
    cp "$file" "$tmp/receipts/$receipt_commit/$file_path"
    write_comments "$file_path" "$file_digest" "$role_tag"
    "$verifier" --sha "$sha" --comments "$tmp/comments.json" \
        --fixture-receipts "$tmp/receipts" >/dev/null 2>&1 || status=$?
    if [[ $expected == pass && $status != 0 ]] || [[ $expected == fail && $status == 0 ]]; then
        printf 'FAIL: %s expected %s, verifier exit=%s\n' "$label" "$expected" "$status" >&2
        exit 1
    fi
>>>>>>> origin/main
<<<<<<< HEAD
# --- Envelope tampering: byte-level, then field-level.
=======
# The wrapper's digest-addressed path is not permission to forge the selected
# row identity. Change only that inner digest; verify_file recomputes the outer
# artifact digest/path, and the final verifier must still refuse it.
jq '.selected_receipt_identity.digest = ("f" * 64)' \
    "$tmp/receipt.json" >"$tmp/tampered-selected-digest.json"
verify_file "$tmp/tampered-selected-digest.json" fail \
    "tampered selected digest with recomputed outer artifact identity" \
    '[coordinator, gpt-5.6-sol]'

# Only the explicitly documented historical service-actor tag may consume an
# older artifact that predates the canonical selected-row identity.
jq '
  del(.selected_receipt_identity)
  | .ledger_record.schema_version = 1
  | del(
      .ledger_record.slot,
      .ledger_record.repo,
      .ledger_record.tree,
      .ledger_record.raw_result,
      .ledger_record.exit_code,
      .ledger_record.gates_run,
      .ledger_record.gates_expected,
      .ledger_record.gates
    )
' "$tmp/receipt.json" >"$tmp/historical.json"
verify_file "$tmp/historical.json" pass "legacy service artifact without selected identity"
verify_file "$tmp/historical.json" fail "current role artifact without selected identity" \
    '[coordinator, gpt-5.6-sol]'
jq '.selected_receipt_identity = false' "$tmp/receipt.json" >"$tmp/malformed-identity.json"
verify_file "$tmp/malformed-identity.json" fail "legacy artifact with malformed selected identity"
jq '
  .ledger_record.checks = 0
  | .ledger_record.gates_run = 0
  | .ledger_record.gates = []
' "$tmp/receipt.json" >"$tmp/current-weak-row-raw.json"
refresh_selected_identity "$tmp/current-weak-row-raw.json" "$tmp/current-weak-row.json"
verify_file "$tmp/current-weak-row.json" fail "current role artifact with weak selected row" \
    '[coordinator, gpt-5.6-sol]'

# Preserve the historical automated service tag and accept each current
# AGENTS.md role-tag form. Tags outside those exact forms remain inert.
valid_role_tags=(
    '[impl agent, ci-hub]'
    '[impl agent, gpt-5.6-sol]'
    '[adversarial-reviewer agent, gpt-5.6-sol]'
    '[coordinator, gpt-5.6-sol]'
    '[Human]'
)
for role_tag in "${valid_role_tags[@]}"; do
    write_comments "$path" "$digest" "$role_tag"
    if ! "$verifier" --sha "$sha" --comments "$tmp/comments.json" \
        --fixture-receipts "$tmp/receipts" >/dev/null 2>&1; then
        printf 'FAIL: valid receipt role tag was refused: %s\n' "$role_tag" >&2
        exit 1
    fi
done

invalid_role_tags=(
    '[assistant, gpt-5.6-sol]'
    '[coordinator, ]'
    '[Human, gpt-5.6-sol]'
    'prefix [coordinator, gpt-5.6-sol]'
)
for role_tag in "${invalid_role_tags[@]}"; do
    write_comments "$path" "$digest" "$role_tag"
    if "$verifier" --sha "$sha" --comments "$tmp/comments.json" \
        --fixture-receipts "$tmp/receipts" >/dev/null 2>&1; then
        printf 'FAIL: malformed receipt role tag was accepted: %s\n' "$role_tag" >&2
        exit 1
    fi
done
write_comments "$path" "$digest" '[coordinator, gpt-5.6-sol]'

# The same legitimate receipt must not authorize a different (rebased) head.
stale_sha=ffffffffffffffffffffffffffffffffffffffff
if "$verifier" --sha "$stale_sha" --comments "$tmp/comments.json" \
    --fixture-receipts "$tmp/receipts" >/dev/null 2>&1; then
    echo "FAIL: receipt for the prior head authorized a rebased head" >&2
    exit 1
fi

# A tampered body and a real zero-executed receipt are both refused.
>>>>>>> origin/main
<<<<<<< HEAD
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
=======
# Host provenance binds the wrapper identity for legacy and current roles.
make_receipt 12 "$tmp/host-good.json"
jq -cS '.run_id = (.commit + "@" + .ledger_record.started_at + "@other-host")' \
    "$tmp/host-good.json" >"$tmp/host-mismatch.json"
verify_file "$tmp/host-mismatch.json" fail "run_id host disagrees with ledger host"
jq -cS 'del(.ledger_record.host)' "$tmp/host-good.json" >"$tmp/host-absent-raw.json"
refresh_selected_identity "$tmp/host-absent-raw.json" "$tmp/host-absent.json"
verify_file "$tmp/host-absent.json" fail "ledger host absent"

# End-to-end current producer contract: one strong verifier-selected row is
# passed as exact bytes with its canonical digest, the mechanical publisher
# emits an artifact-SHA-addressed body, and a current role-tagged marker
# dereferences through the landing verifier.
sha=dddddddddddddddddddddddddddddddddddddddd
strong_log=$tmp/strong-validate.log
printf 'running 12 tests\ntest result: ok. 12 passed; 0 failed\n' >"$strong_log"
jq -cn --arg sha "$sha" --arg log "$strong_log" '{
  schema_version: 4,
  started_at: "2026-08-04T13:00:00Z",
  finished_at: "2026-08-04T13:02:00Z",
  host: "fixture-host",
  slot: "fixture-slot",
  profile: "full",
  selection_mode: "full",
  commit: $sha,
  tree: ("f" * 40),
  commit_anchored: true,
  tree_dirty: false,
  result: "pass",
  raw_result: "pass",
  exit_code: 0,
  executed_tests: 12,
  filtered_tests: 3,
  checks: 2,
  gates_run: 2,
  gates_expected: 2,
  failures: 0,
  log_file: $log,
  gates: [
    {name: "fmt", result: "pass", exit_code: 0},
    {name: "test", result: "pass", exit_code: 0}
  ]
}' | tr -d '\n' >"$tmp/strong-row.json"
"$receipt_digest" receipt-digest --sha "$sha" --canonical-row \
    <"$tmp/strong-row.json" >"$tmp/strong-canonical-row.json"
selected_digest=$(sha256sum "$tmp/strong-canonical-row.json" | awk '{print $1}')
strong_report=$(python3 "$publisher" \
    --repo rrnewton/hermit \
    --sha "$sha" \
    --ledger "$tmp/strong-ledger.jsonl" \
    --selected-receipt-sha256 "$selected_digest" \
    --canonicalization 'serde_json::to_vec(HistoryRow)-v1' \
    --dry-run <"$tmp/strong-canonical-row.json")
artifact_digest=$(jq -r '.artifact_sha256' <<<"$strong_report")
artifact_path=$(jq -r '.path' <<<"$strong_report")
jq -jr '.artifact_body' <<<"$strong_report" >"$tmp/strong-artifact.json"
if [[ $(sha256sum "$tmp/strong-artifact.json" | awk '{print $1}') != "$artifact_digest" ]] || \
   [[ $artifact_path != "validation-receipts/rrnewton/hermit/$sha/$artifact_digest.json" ]]; then
    echo "FAIL: publisher did not bind exact artifact bytes to its digest-addressed path" >&2
    exit 1
fi
if ! jq -e --arg selected "$selected_digest" '
    .selected_receipt_identity.digest_algorithm == "sha256"
    and .selected_receipt_identity.canonicalization == "serde_json::to_vec(HistoryRow)-v1"
    and .selected_receipt_identity.digest == $selected
    and .ledger_record.checks == 2
    and .ledger_record.gates_run == 2
    and (.ledger_record.gates | length) == 2
' "$tmp/strong-artifact.json" >/dev/null; then
    echo "FAIL: artifact lost the verifier-selected strong row identity" >&2
    exit 1
fi
mkdir -p "$tmp/receipts/$receipt_commit/$(dirname "$artifact_path")"
cp "$tmp/strong-artifact.json" "$tmp/receipts/$receipt_commit/$artifact_path"
write_comments "$artifact_path" "$artifact_digest" '[coordinator, gpt-5.6-sol]'
if ! "$verifier" --sha "$sha" --comments "$tmp/comments.json" \
    --fixture-receipts "$tmp/receipts" >/dev/null 2>&1; then
    echo "FAIL: strong-row -> artifact-digest -> marker chain was refused" >&2
    exit 1
fi

# Count-capable receipts additionally bind the per-node coverage obligation.
# Use a second exact head so the two positive controls represent two distinct
# legitimate landing authorizations rather than repeated parsing of one row.
sha=eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee
make_receipt 12 "$tmp/schema5-base.json"
jq '.ledger_record.schema_version = 5' "$tmp/schema5-base.json" >"$tmp/schema5-missing-raw.json"
refresh_selected_identity "$tmp/schema5-missing-raw.json" "$tmp/schema5-missing.json"
verify_file "$tmp/schema5-missing.json" fail "schema5 missing coverage"
jq '.ledger_record.coverage = {
      planned_test_nodes: 0, executed_test_nodes: 0,
      zero_executed_nodes: [], absent_nodes: []
    }' "$tmp/schema5-missing.json" >"$tmp/schema5-zero-planned-raw.json"
refresh_selected_identity "$tmp/schema5-zero-planned-raw.json" "$tmp/schema5-zero-planned.json"
verify_file "$tmp/schema5-zero-planned.json" fail "schema5 zero planned nodes"
jq '.ledger_record.coverage = {
      planned_test_nodes: 2, executed_test_nodes: 1,
      zero_executed_nodes: [], absent_nodes: ["test.missing"]
    }' "$tmp/schema5-missing.json" >"$tmp/schema5-absent-raw.json"
refresh_selected_identity "$tmp/schema5-absent-raw.json" "$tmp/schema5-absent.json"
verify_file "$tmp/schema5-absent.json" fail "schema5 absent node"
jq '.ledger_record.coverage = {
      planned_test_nodes: 2, executed_test_nodes: 2,
      zero_executed_nodes: [], absent_nodes: []
    }' "$tmp/schema5-missing.json" >"$tmp/schema5-valid-raw.json"
refresh_selected_identity "$tmp/schema5-valid-raw.json" "$tmp/schema5-valid.json"
verify_file "$tmp/schema5-valid.json" pass "schema5 complete coverage"
>>>>>>> origin/main
<<<<<<< HEAD
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
=======
echo "PASS: 2/2 legitimate exact-head landing receipts accepted; 2/2 additional identity/compatibility receipts and 5/5 role tags accepted; current-tagged identity omission, malformed legacy identity, tampered selected-row digest after outer rehash, current-tagged weak row, 4/4 malformed role tags, stale-head, forged, tampered, zero-executed, host-mismatch, host-absent, and three incomplete schema5 controls refused; fixture plant deleted cleanly"
>>>>>>> origin/main
```
