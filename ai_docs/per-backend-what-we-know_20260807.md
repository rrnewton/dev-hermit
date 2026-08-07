# Per backend: what is MEASURED, what is BLOCKED, what is ASSUMED

**Date:** 2026-08-07 · **Author:** hermit-w10 · **Task:** `per-backend-what-do-we-actually-know-summary`

Synthesis over the session's measurements. **Every line below is either sourced to an artifact
or listed as ASSUMED.** Nothing was re-derived that already had a source; three things were
re-measured because I could not source them, and one dispatch claim turned out to be stale.

**Read the ASSUMED section first if you are about to quote a number.** That section is where the
next false claim comes from, and it is longer than anyone would like.

## How to read the denominators

Two figures in this document look like the same measurement and are not:

- ptrace stack **31 records, 0 differing**, guest `/bin/true`
- SaBRe stack **104 records, 104 differing**, guest `/bin/true`, same hermit binary

and the dispatch that commissioned this document quotes SaBRe as **121/121**. Both are true; they
are different guests. **A stack-record count is a property of the guest and the backend jointly,
so quoting one without naming the guest is not a claim about anything.** Same trap as the e9patch
corpus figures below.

---

## ptrace — the golden reference

**MEASURED**
- Stack self-determinism: **31 records/run, 0/31 differing**, n=2. Guest `/bin/true`,
  `--detlog-stack --base-env minimal`, hermit `gf89c69766371-dirty`.
  Artifact: `scratch/w10-sabre/ptrue-{1,2}.{log,err}` (measured for this document).
- Also **18 records, 0 differing** on a deep-recursion static guest
  (`ci-hub/parity/guests/stack_deep_recursion.c`), n=2, same conditions.

**BLOCKED** — nothing.

**ASSUMED** — see the golden-reference entry in the ASSUMED section. Self-consistency is measured;
*correctness* is not, and "golden" asserts the latter.

---

## KVM

**MEASURED**
- Heap parity vs ptrace on a malloc/fragment/reuse guest: **ptrace 24 hashes/run, KVM 23/run**,
  21 distinct each, each backend stable across n=3. Cause decomposed: raw digests diverge at
  record 1 because measured *domains* differ (ptrace hashes page-rounded VMAs, KVM hashes exact
  brk ranges); first control-flow divergence is syscall #23 `brk`, where KVM refuses at a fixed
  `brk_limit` 0x5bd000. Net count delta is two ptrace-only shrink records minus one KVM-only mmap
  record. Source: task `kvm-heap-parity-fails-differing-hash-counts-and-sequences`, Hermit
  `590fcc9e`, Reverie `6144323c`. I independently reproduced the ptrace arm (24 hashes,
  `heap-sum=6358`) while building `ci-hub/parity/guests/heap_fragment_reuse.c`.
- Stack self-determinism fix: **reverie#403 is OPEN, unmerged** (checked 2026-08-07). I reviewed it
  at head `a6aa8bc4` and independently red-planted its subscription guard: removing the 3-line
  guard makes `static_elf_unsubscribed_rdtscp_remains_guest_exception` FAIL with
  `TimestampLog { calls: [(Pid(1), Tscp)] }`; restoring it passes. Full suite 229 passed/0 failed.
  Both authoritative gates SUCCESS at that head.
  Source: https://github.com/rrnewton/reverie/pull/403#issuecomment-5213970063
- **reverie#402 is OPEN, unmerged** (checked 2026-08-07).

**BLOCKED**
- #403 and #402 both unlanded. #403 additionally sits behind the *draft* merge-gate pathology:
  a draft PR's `merge-gate-v2` cancels and `refire-on-ci-completion` is SKIPPED, so it can hold two
  green authoritative gates and a CANCELLED gate indefinitely. Marking ready refires it.
- Trigger-3 dual review: `passed-review-claude` applied; **Codex re-review at `a6aa8bc4` outstanding.**

**ASSUMED** — the "21/31 ordinals" baseline figure and the #402 "record 20 → >139" improvement;
neither sourced here. See ASSUMED.

---

## DBI

**MEASURED — and this is the one dispatch claim that is now STALE**
- The dispatch says DBI detlog is "measurable **once pinned to** reverie#394". **#394 MERGED at
  2026-08-07T05:50:33Z**, and its merge commit is **`038e993926e4`** — which **is** the reverie pin
  on hermit `origin/main` (verified by `merge-base --is-ancestor`). So the DynamoRIO dead-stack
  residue scrub is **active at hermit main today**; this is no longer a pending dependency.
- Root cause on record: only **32 of 135168** `[stack]` bytes differed, all below the guest's
  deepest write; payload was DR's randomized mapping addresses plus the raw host pid.
  The shipped fix is **two scrubs with two different selection rules** (positional at the first
  application instruction; DR-ownership on clone re-arm). Source: reverie#394.

**BLOCKED** — nothing named that I could source.

**ASSUMED** — `Detcore Tool active` witness emission; the "detlog measurable" claim itself is now
about a landed change but I did not re-measure detlog after the pin advanced.

---

## SaBRe

**MEASURED**
- Stack self-determinism: **104 records/run, 104/104 DIFFERING**, n=2, guest `/bin/true`, same
  hermit binary and flags as the ptrace row above. Range and record count stable across runs;
  only contents move. Artifact: `scratch/w10-sabre/true-{1,2}.{log,err}` (measured for this task
  `sabre-stack-baseline-needs-a-reverie-side-change`).
- **Maps byte-identical** across runs — so this is NOT the LiteInst maps-inode cause.
- The env scrub WORKS: a sabre guest cat'ing `/proc/self/environ` shows **0 occurrences** of the
  random socket path (ptrace control also 0). `take_private_env`
  (`reverie/experimental/reverie-sabre/src/paths.rs:63-95`) is effective; the live environ is clean.
- The staged-program PID path is **still gated** to filenames starting with `ld`
  (`hermit/hermit-cli/src/lib.rs:816-820`), so it did not fire for these guests.
- **0 parity verdicts across 7 scorecard rows** — confirmed independently: in
  `compat-envelope/scorecard.csv`, sabre has 7 rows and **all 7 have an empty `parity` column**,
  while kvm/liteinst/dbi all carry 0s and 1s. Present in the table, informing nothing.
- SaBRe **SEGFAULTS (rc=139)** on a static deep-recursion guest that ptrace runs cleanly.

**BLOCKED**
- Stack column cannot become scorable until the baseline reproduces.
- The decisive experiment (pin the RPC socket path, re-measure) needs a one-line edit:
  `hermit-cli/src/lib.rs:1018-1021` generates the tempdir unconditionally and `:1039` overwrites
  the env var, so there is no override to set read-only.

**ASSUMED (leading hypothesis, NOT confirmed)** — dead-stack residue of the random socket-path
suffix, left by a `sockaddr_un` stack copy during `UnixStream::connect`
(`reverie/experimental/reverie-sabre/src/rpc.rs:96,102,110`). Consistent with every measured fact
and with the DBI precedent, but **unconfirmed**.

---

## LiteInst

**MEASURED**
- **hermit#1847 is OPEN, unmerged** (checked 2026-08-07).

**BLOCKED** — #1847 unlanded.

**ASSUMED** — the 303/413 stack baseline, the 410/410 post-fix prediction, the detlog 0/1245 figure,
and the four in-guest gaps (CPUID/RDTSC/RDRAND/RDSEED). None sourced today.

---

## e9patch

**MEASURED**
- Corpus split: **20/20 on the dedicated corpus vs 4/137 on the shared one.** These are different
  populations; **neither number means anything without naming its corpus.** Source: memory record
  `e9patch-reach-is-ten-mnemonics-and-two-corpus-populations`, and the reach surface is **ten
  mnemonics**, not just `syscall`.
- Same-position content REPLACE is real divergence, not windowable: the measuring agent's heap
  count **DECREASED 14 → 12**, and a purely additive preprocessing story cannot remove records.
  Now filed as https://github.com/rrnewton/hermit/issues/1888.
- Architecturally **not a backend**: e9patch is binary-rewriting preprocessing used *with* the
  ptrace backend (`hermit/CLAUDE.md`, Backend Definition).

**BLOCKED** — nothing named beyond the REPLACE investigation.

**ASSUMED** — that the TSC-cleanliness is INHERITED from the attached ptracer rather than earned,
and that DETLOG routes via the ptrace host. Both are highly plausible given the architecture note
above, but I did not source a measurement for either today.

---

# ASSUMED — the most valuable section

Every item here is currently **stated somewhere as if it were fact** and could not be sourced to an
artifact by me today. Each is a place a false claim could originate.

1. **"ptrace is the golden reference."** What is measured is *self-consistency* (0/31, 0/18
   differing). Nothing measures that ptrace is *correct*. Every cross-backend parity number in this
   repo is expressed as agreement-with-ptrace, so if ptrace is wrong about something, that error is
   invisible to the entire parity apparatus and will read as five backends agreeing. This is the
   single largest unexamined assumption in the program.
2. **ptrace detlog `0/141 differing`** — not sourced.
3. **Falsifiability `8/8 old → 8/8 strict, DROP=0`** — not sourced. Note this sits in tension with
   the separately-recorded re-baseline of **1,837 raw → 0 qualified**
   (https://github.com/rrnewton/hermit/issues/1885); the two are over different populations, and
   quoting them together without saying so would be badly misleading.
4. **KVM stack baseline `21/31 ordinals`** — not sourced by me; only the *fix* was reviewed.
5. **KVM #402 "prefix-parity record 20 → >139"** — not sourced.
6. **KVM strace-litmus PASS with no engagement witness** — the *gap* is filed
   (https://github.com/rrnewton/hermit/issues/1889); the PASS itself I did not reproduce.
7. **DBI emits `Detcore Tool active`** — not sourced.
8. **The detlog-clean figures: SaBRe `0/368` and LiteInst `0/1245`.** I could not source either.
   Flagging SaBRe's specifically: *detlog-clean* alongside *stack-nondeterministic at 104/104* is a
   surprising combination. It is not impossible — the two hash different things, and the stack
   residue hypothesis would not touch the detlog record stream — but a figure that surprising should
   carry a source before it is repeated.
9. **LiteInst 303/413 → predicted 410/410** — not sourced; the prediction especially should never
   be quoted as a result.
10. **e9patch TSC-cleanliness is inherited, not earned** — architecturally likely, unmeasured.
11. **SaBRe dead-stack-residue cause** — leading hypothesis only; the confirming experiment is
    blocked on a one-line edit.

## Cross-cutting, and sourced

- **The cross-backend `parity` column does not gate `outcome`.** Of 2312 scorecard rows, 1697 carry
  an executed parity comparison and 354 recorded a MISMATCH — **105 of those are published as
  `outcome=pass`**. `outcome` is assigned from the verify exit code at
  `compat-envelope/collect-fullcorpus.sh:177-179` and never revised; `parity` is computed after at
  `:180-184` and is read by nothing but the row emission. **Cells whose outcome could ever be
  changed by the parity result: 0 / 2312.** So every per-backend parity figure in this document is
  a *recorded observation*, never a gate.
- **Empty `parity` means UNMEASURED by design**, not missing data (`:158`, "never a false match").
- **`--detlog-stack` output goes to STDERR under SaBRe**, not `--log-file`. Reading only the log
  file yields 0 records, which reads as "no data" rather than "wrong stream". I hit this and
  briefly measured 0/0.
