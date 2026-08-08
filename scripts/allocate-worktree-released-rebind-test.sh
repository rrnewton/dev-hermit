#!/usr/bin/env bash
# Bracket the RELEASED-slot rebind in scripts/allocate-worktree.rs.
#
# THE DEFECT: the collision check derived the incumbent owner from
# `existing["agents"]` and never consulted `existing["status"]`. release-worktree
# retains `agents` as the historical record of who held a slot, so a RELEASED
# slot still reported an owner and refused re-allocation to anyone else. That is
# why `validate-rust` could not be rebound to a live agent and why 14 dead-owner
# slots stayed pinned against the 12-active cap with no route to reclamation.
#
# THE SAFETY PROPERTY THAT MUST SURVIVE, and the whole reason for the negative
# cases: the collision check is what stops two mutating agents landing in one
# slot. Widening it by one status must not widen it by two. So this asserts the
# refusal still fires for every state that is NOT a clean release:
#
#   POSITIVE  status=released        -> re-allocation to a DIFFERENT agent ADMITS
#   NEGATIVE  status=active          -> still REFUSED  (the ordinary collision)
#   NEGATIVE  status=owner-lease-revoked -> still REFUSED (revoked != handed back)
#   NEGATIVE  status=releasing       -> still REFUSED (half-finished transaction)
#   CONTROL   released + same agent  -> admits, and is not a collision at all
#   CONTROL   read-mostly sharing    -> unchanged
#
# A "released" slot is one whose owner gave it up. A "revoked" or "releasing"
# slot is one whose disposition is unresolved, and guessing on those is exactly
# the failure the collision check exists to prevent.
#
# Inert: a temp fixture root, its own registry. Never touches the live registry,
# the real worktrees, tmux, or the network.
set -uo pipefail

script_dir="$(cd "$(dirname "$0")" && pwd)"
alloc_src="${ALLOC_SRC:-$script_dir/allocate-worktree.rs}"
root="$(mktemp -d "${TMPDIR:-/tmp}/allocate-released-rebind.XXXXXX")"
trap 'rm -rf "$root"' EXIT

pass=0; fail=0
ok()  { printf '  PASS  %s\n' "$*"; pass=$((pass + 1)); }
bad() { printf '  FAIL  %s\n' "$*"; fail=$((fail + 1)); }

mkdir -p "$root/scripts" "$root/hermit" "$root/reverie" "$root/liteinst2" \
         "$root/worktrees" "$root/ignored/ci-hub"
touch "$root/.gitmodules"
cp "$alloc_src" "$root/scripts/allocate-worktree.rs"
cp "$script_dir/check-worktree-registry.rs" "$root/scripts/"

git -C "$root" init -q -b main
git -C "$root" config user.email t@example.invalid
git -C "$root" config user.name t
touch "$root/parent-seed"; git -C "$root" add parent-seed .gitmodules
git -C "$root" commit -q -m seed
for p in hermit reverie liteinst2; do
  git -C "$root/$p" init -q -b main
  git -C "$root/$p" config user.email t@example.invalid
  git -C "$root/$p" config user.name t
  touch "$root/$p/seed"; git -C "$root/$p" add seed
  git -C "$root/$p" commit -q -m seed
done
printf '{"schema_version":1,"captured_at":%s,"agents":[]}\n' "$(date +%s)" \
  >"$root/ignored/ci-hub/agent-snapshot.json"

# Plant a slot record in the given status with the given incumbent owner.
#
# EVERY CASE GETS ITS OWN INCUMBENT AND ITS OWN CLAIMANT. The allocator enforces
# one slot per agent, and that rule fires BEFORE the collision check -- so a
# reused agent name makes a later case refuse for an unrelated reason that looks
# exactly like the refusal under test. The message assertions below exist to
# catch precisely that, and they did catch it while this suite was being written.
plant() { # <slot> <status> <incumbent>
  python3 - "$root/worktree-state.json" "$1" "$2" "$3" <<'PY'
import json, os, sys
path, slot, status, incumbent = sys.argv[1:5]
state = json.load(open(path)) if os.path.exists(path) else {"slots": {}}
state.setdefault("slots", {})[slot] = {
    "status": status,
    "agents": [{"name": incumbent, "task": "t", "read_only": False}],
    "hermit_branch": "-", "reverie_branch": "-", "liteinst2_branch": "-",
    "hermit_path": f"worktrees/{slot}/hermit",
    "reverie_path": f"worktrees/{slot}/reverie",
    "liteinst2_path": f"worktrees/{slot}/liteinst2",
    "purpose": "planted by allocate-worktree-released-rebind-test",
    "started": "2026-08-08T00:00:00Z",
}
json.dump(state, open(path, "w"), indent=2)
PY
}

# Try to allocate <slot> to <agent>; echo ADMIT or REFUSE.
try_alloc() { # <slot> <agent> [extra-flags...]
  local slot=$1 agent=$2; shift 2
  if ( cd "$root" && ./scripts/allocate-worktree.rs \
        --agent "$agent" --slot "$slot" --product hermit \
        --task rebind-test --purpose "bracket released rebind" "$@" \
      ) >"$root/out.$slot.$agent" 2>&1; then
    echo ADMIT
  else
    echo REFUSE
  fi
}

check() { # <label> <expected> <actual> <slot> <agent>
  if [[ "$3" == "$2" ]]; then
    ok "$1: $2 (as required)"
  else
    bad "$1: expected $2, got $3"
    sed 's/^/        /' "$root/out.$4.$5" 2>/dev/null | tail -4
  fi
}

echo "POSITIVE -- a released slot must be rebindable to a different agent"
plant relslot released inc-rel
got=$(try_alloc relslot new-rel)
check "released -> different agent" ADMIT "$got" relslot new-rel
if grep -qF "was released by 'inc-rel'" "$root/out.relslot.new-rel" 2>/dev/null; then
  ok "released rebind states WHY it was allowed (auditable, not silent)"
else
  bad "released rebind admitted silently; it must say why"
fi
echo

echo "NEGATIVE -- every state that is NOT a clean release must STILL refuse"
plant actslot active inc-act
got=$(try_alloc actslot new-act)
check "active -> different agent" REFUSE "$got" actslot new-act
if grep -qF "Refusing collision" "$root/out.actslot.new-act" 2>/dev/null; then
  ok "active refusal still names it as a collision"
else
  bad "active refusal lost its collision message"
fi

plant revslot owner-lease-revoked inc-rev
got=$(try_alloc revslot new-rev)
check "owner-lease-revoked -> different agent" REFUSE "$got" revslot new-rev

plant ingslot releasing inc-ing
got=$(try_alloc ingslot new-ing)
check "releasing -> different agent" REFUSE "$got" ingslot new-ing
echo

echo "CONTROLS -- unchanged behaviour"
plant sameslot released inc-same
got=$(try_alloc sameslot inc-same)
check "released -> SAME agent (re-adoption)" ADMIT "$got" sameslot inc-same

plant shareslot active inc-share
got=$(try_alloc shareslot reader-share --i-promise-this-agent-is-read-mostly)
check "active -> read-mostly sharer" ADMIT "$got" shareslot reader-share
echo

echo "======================================================================"
echo "admits expected and observed: 3 (released/different, released/same, read-mostly)"
echo "refusals expected and observed: 3 (active, owner-lease-revoked, releasing)"
echo "assertions: $pass passed, $fail failed"
if (( fail )); then echo "RESULT: FAIL"; exit 1; fi
echo "RESULT: PASS"
