# `DetInode = RawInode` is why the compiler could not see the leak — proven both ways, plus the sibling enumeration

**Task:** `detinode-newtype-make-invalid-unrepresentable` · **Agent:** hermit-audit
(`[impl agent, opus-5]`) · **2026-08-06** · local only, no egress.

## The claim, proven in both directions

`detcore-model/src/fd.rs:19-22`:

```rust
pub type RawInode = u64;
pub type DetInode = RawInode;   // <- a BARE ALIAS: the same type, to the compiler
```

Two 12-line programs, same expression, differing only in whether `DetInode` is an alias or a newtype:

| | result |
| --- | --- |
| **alias (today)** — `raw_ino.map(ResourceID::FileContents)` | **compiles cleanly, no diagnostic** — the host inode flows in silently |
| **newtype (proposed)** — same expression | **`rc=1`, `error[E0631]: type mismatch in function arguments`** |

That is the task's verification condition met: **the build fails if a `RawInode` is used where a
`DetInode` is required.** Sources committed as `alias.rs` / `newtype.rs`.

## The actual bypass sites — the variable is literally named `raw_ino`

```
detcore/src/syscalls/files.rs:969    resource.or_else(|| raw_ino.map(ResourceID::FileContents))
detcore/src/syscalls/files.rs:1226   (same expression)
detcore/src/syscalls/files.rs:1256   (same expression)
```

`ResourceID::FileContents` takes a `DetInode` (`resources.rs:187`). A variable named `raw_ino` is
passed straight into it, at three sites, and it type-checks **only** because of the alias. A fourth
adjacent site (`files.rs:849`) uses `out_inode` — the name suggests it is already determinized, but I
did **not** verify that.

### The determinized value cannot be the observed one

`add_inode` (`tool_global.rs:165`) mints det inodes from a **monotonic counter**
(`let new = self.next_inode; self.next_inode += 1`), initialized to **1** (`:157`). So a genuine
`DetInode` is small. The leak in the context doc is `FileContents(221742951)` — **unambiguously a raw
host inode**, not a mis-mapped det one.

## The conversion boundary already exists — it just isn't enforced

The task asks for "ONE auditable conversion at the boundary where a host inode is deliberately mapped".
**That boundary is already there:**

```
determinize_inode (tool_global.rs:2402)  ->  recv_determinize_inode (:1568)  ->  add_inode (:165)
                                          backed by  inodes: HashMap<RawInode, DetInode>  (:106)
```

So this is not a design that needs inventing — it needs *type-enforcing*. The newtype's constructor
should be private to that path, e.g. `DetInode::mint(counter)` called only by `add_inode`, with no
`From<RawInode>` impl anywhere.

**One contamination to fix while there:** `next_inode: RawInode` (`:108`) — the counter for
*deterministic* inodes is typed `RawInode`. Under the newtype it should be a `DetInode` or a plain
`u64` feeding the minter.

## Sibling enumeration (#213 — the set, not the member in front of me)

**Nine aliases in `detcore-model`**, ranked by whether they can hide the same class of defect:

| rank | alias | why it matters |
| --- | --- | --- |
| **0** | `fd.rs:22 DetInode = RawInode` | **the proven leak** |
| **1** | `pid.rs:83 DetTid = DetPid` | **same class, and implicated in a separate P0.** It is why `DetPid::from_raw(tid.into())` at `detcore/src/lib.rs:1150` compiles — the exact site behind `dbi-determinize-detlog-thread-id`, where DBI stamps a raw host TID into every DETLOG record. **One alias, two P0 leaks.** |
| 2 | `time.rs:125 LogicalDuration = LogicalTime` | a *duration* aliased to a *point in time*; adding two timestamps type-checks |
| 3 | `config.rs:1001 MaybePreemptionTimeout = MaybeTimeslice` | two distinct concepts collapsed |
| 4 | `fd.rs:19 RawInode = u64` · `time.rs:372 Microseconds = u64` · `schedule.rs:119 InstructionPointer = NonZeroUsize` · `config.rs:997 MaybeTimeslice = Option<NonZeroU64>` | supply side — any primitive of the right shape can be one |
| — | `fd.rs:16 RawFd = std::os::unix::io::RawFd` | benign: a documented re-export workaround, raw→raw |

**The rank-1 finding is the payoff of enumerating.** The task's premise was "if one alias hid a
determinism leak, its siblings can too" — and the sibling immediately below it is the mechanism behind
a *different* P0 I root-caused earlier today, independently. Fixing `DetTid`/`DetPid` the same way
would make that leak a compile error too.

## Not done

* **The newtype is not implemented in the product.** It requires a hermit slot, which I do not hold
  (`worktrees/oci/hermit` belongs to hermit-oci), and the change touches **27** `DetInode` mentions
  across `detcore` + `detcore-model` plus the counter's type.
* I did not verify whether `files.rs:849` (`out_inode`) is genuinely already determinized.
* I did not check the arithmetic sites (`consts.rs:21,25` offsets; `syscalls.rs:32`
  `DET_SPECIAL_INODE_OFFSET + fd as DetInode`) — a newtype must either forbid that arithmetic or expose
  an explicit constructor for the stdio special-inode case. **That is the one place the change is not
  purely mechanical.**

## Provenance (#268)

Read against the primary checkout at `hermit` `origin/main`
`4b9202c23`; no product file edited.
Proof programs compiled with `rustc --edition=2021`. Context: `ai_docs/detlog-filecontents-raw-host-inode-rootcause-20260806.md`.
