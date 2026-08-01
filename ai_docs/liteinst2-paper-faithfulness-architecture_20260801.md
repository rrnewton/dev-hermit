# LiteInst2 paper-faithfulness and architecture review

**Review date:** 2026-08-01  
**Reviewer lane:** independent faithfulness review 2/3  
**Audited code:** [`rrnewton/liteinst2@9ffde2830a637eb64de0f77c00e8e28f137cb14b`](https://github.com/rrnewton/liteinst2/tree/9ffde2830a637eb64de0f77c00e8e28f137cb14b)  
**Verdict:** LiteInst2 is a useful, defensive Rust patching core, but it is **not
yet a faithful port of the complete PLDI'17 LiteInst design**. It faithfully
implements one narrow instruction-pun case, a recognizable WordPatch++
publication protocol, and relocation-aware trampolines. Most of the mechanism
that made the paper "probe anywhere" is absent or delegated to the client.

This review was performed from the two papers and the LiteInst2 source. It did
not use or coordinate conclusions with the parallel original-LiteInst review.

## Sources and scope

Primary sources:

- Chamith et al., ["Living on the Edge: Rapid-Toggling Probes with
  Cross-Modification on x86"](https://doi.org/10.1145/2908080.2908084),
  PLDI 2016 ([author-hosted PDF](https://svenssonjoel.github.io/writing/pldi16-crossmod.pdf)).
- Chamith et al., ["Instruction Punning: Lightweight Instrumentation for
  x86-64"](https://doi.org/10.1145/3062341.3062344), PLDI 2017
  ([indexed PDF](https://static.aminer.org/pdf/20170130/pdfs/pldi/wk5pe0zdtjp7ixuywdm82qrszbvqaym1.pdf)).
- The paper artifact, [`iu-parfunc/liteinst@b264b0a`](https://github.com/iu-parfunc/liteinst/tree/b264b0a0e61cfd94d2a05378956173b2b6b1a124),
  was used only as a secondary check on terminology and component boundaries.

The audited LiteInst2 revision is remote `main`, one commit newer than the
parent's pinned submodule. This report evaluates the standalone crate. Hermit
and Reverie policy, syscall semantics, process lifecycle, and site discovery
are outside the crate by design, as its
[README says](https://github.com/rrnewton/liteinst2/blob/9ffde2830a637eb64de0f77c00e8e28f137cb14b/README.md#L3-L16).

## Bottom line

The most accurate name for the current implementation is:

> a policy-free, fail-closed x86-64 live-patching and trampoline core with a
> natural-pun fast path, not a full LiteInst instruction-punning port.

That distinction matters:

1. **Natural punning is real.** If the original four bytes after an instruction
   opcode already encode an available rel32 trampoline address, LiteInst2 maps
   that exact address and toggles only the opcode byte. This matches the ideal
   `Abcde` case in PLDI'17.
2. **The generic path is relocation, not paper-style punning.** LiteInst2
   overwrites a forward window with an unconstrained five-byte jump, relocates
   complete instructions, and requires the caller to prove there are no
   interior control-flow entries.
3. **The paper's coverage mechanism is missing.** PLDI'17 preserves or reroutes
   every valid PC in all 16 five-byte instruction-head layouts, searches legal
   pun offsets, backtracks within a basic block when needed, and coalesces
   collisions. LiteInst2 implements none of those mechanisms.
4. **The README's disclaimer is correct and important.** It explicitly says
   the tests do not establish arbitrary-binary or probe-anywhere support and
   places interior-entry, mapping, lifetime, and fallback obligations on the
   client ([lines 65-73](https://github.com/rrnewton/liteinst2/blob/9ffde2830a637eb64de0f77c00e8e28f137cb14b/README.md#L65-L73)).

## Current architecture

### 1. Decode and bind a byte snapshot

`InstructionScanner` linearly decodes a caller-supplied, code-only region with
iced-x86. It rejects invalid or truncated input, records every decoded head,
classifies instruction/cache-line geometry, and retains the exact bytes
([scanner](https://github.com/rrnewton/liteinst2/blob/9ffde2830a637eb64de0f77c00e8e28f137cb14b/src/scanner.rs#L319-L442)).
Every later planner rescans and compares that snapshot. This is stricter than
the paper artifact and is a good fail-closed addition.

This is not CFG recovery. A linear scan sees only the stream the client gives
it. External direct branches, indirect targets, alternate instruction streams,
function boundaries, and mapping lifetime remain client proofs.

### 2. Select one of three strategies

`plan_hook` returns
[`RapidPreferred`, `Relocated`, or `TrapRequired`](https://github.com/rrnewton/liteinst2/blob/9ffde2830a637eb64de0f77c00e8e28f137cb14b/src/planner.rs#L52-L135):

- `RapidPreferred` is attempted only for observing hooks.
- `Relocated` is used for a generic observing hook or a replace-first hook.
- `TrapRequired` is a request to the client. LiteInst2 does not supply the
  steady-state trap implementation.

The planner carries a relocation fallback beside a rapid plan, but there is no
single public installer that consumes `PunPlan` and performs the fallback. The
client must wire that policy correctly.

### 3. Natural-pun fast path

`RapidTogglePlan` requires the complete five-byte window, rejects every decoded
instruction head or discovered direct branch target in bytes 1 through 4, and
interprets the unchanged four-byte suffix as a rel32 displacement
([planning](https://github.com/rrnewton/liteinst2/blob/9ffde2830a637eb64de0f77c00e8e28f137cb14b/src/rapid.rs#L196-L315)).
`RapidProbe::install` verifies live bytes, reserves the patch site, and maps a
fresh trampoline at the one exact implied address with
`MAP_FIXED_NOREPLACE`. Activation and deactivation are one atomic opcode store
([installation and toggle](https://github.com/rrnewton/liteinst2/blob/9ffde2830a637eb64de0f77c00e8e28f137cb14b/src/rapid.rs#L356-L476)).

This is faithful to PLDI'17's best `Abcde` case. It is deliberately much
narrower than the paper's instruction-pun search.

### 4. Generic forward relocation

The generic plan collects consecutive complete instructions starting at the
requested address until their total length is at least five bytes. It rejects
direct branch targets discovered inside that displaced window
([jump planning](https://github.com/rrnewton/liteinst2/blob/9ffde2830a637eb64de0f77c00e8e28f137cb14b/src/patcher.rs#L282-L369)).

`TrampolinePlan` uses iced-x86's block encoder to relocate those instructions,
or omits the first instruction for `ReplaceFirst`. Generated code:

1. protects the System V red zone;
2. saves flags, all integer registers, and x87/SIMD state through AVX-512 plus
   PKRU;
3. calls the client hook with a mutable `HookContext`;
4. restores state;
5. executes relocated instructions; and
6. transfers to the first application instruction after the displaced window.

See the [plan and emission path](https://github.com/rrnewton/liteinst2/blob/9ffde2830a637eb64de0f77c00e8e28f137cb14b/src/trampoline.rs#L359-L579).
This is a substantial and useful engineering extension beyond the paper's
minimal trampoline description.

### 5. Allocate executable storage

The normal path scans `/proc/self/maps`, creates a memfd, and attempts a fresh
near RX mapping without replacing live VMAs. The temporary RW alias is removed
before publication. Arena mode preallocates separate RW and RX aliases and
reserves one **4096-byte slot per trampoline**
([arena](https://github.com/rrnewton/liteinst2/blob/9ffde2830a637eb64de0f77c00e8e28f137cb14b/src/trampoline.rs#L596-L692),
[mapping](https://github.com/rrnewton/liteinst2/blob/9ffde2830a637eb64de0f77c00e8e28f137cb14b/src/trampoline.rs#L1025-L1209)).

The separate aliases avoid a single RWX VMA. Arena mode is still not strict
W^X, because executable bytes remain writable through the RW alias.

### 6. Publish and toggle

For an eight-byte patch wholly within a cache line, LiteInst2 performs one
eight-byte MOV. For a cross-line word it:

1. atomically replaces selected front-line instruction heads with `INT3`;
2. waits a caller-supplied `StalenessBudget` in TSC ticks;
3. writes the aligned back word;
4. waits again; and
5. publishes the aligned front word.

See [`publish_cross_line`](https://github.com/rrnewton/liteinst2/blob/9ffde2830a637eb64de0f77c00e8e28f137cb14b/src/patcher.rs#L706-L772).
A process-wide SIGTRAP router spins while the site is in the writing phase and
retries the trapped PC afterward
([router](https://github.com/rrnewton/liteinst2/blob/9ffde2830a637eb64de0f77c00e8e28f137cb14b/src/trap.rs#L273-L343)).

This follows the shape of PLDI'16 WordPatch and PLDI'17 WordPatch++, but changes
an important detail: the paper's WordPatch++ writes traps to **all bytes in the
front fragment**, while LiteInst2 guards only decoded instruction heads.

## Exhaustive patch-site decision tree

The following tree describes current behavior first, then the paper-only
upstream path that would be required for full faithfulness.

```text
Requested instruction head A
|
+-- Is the supplied region fully decodable and byte-for-byte current?
|   +-- no  -> TrapRequired (client owns trap behavior)
|   `-- yes
|
+-- Hook semantics = ReplaceFirst?
|   +-- yes -> skip rapid pun; try generic forward relocation
|   `-- no  -> try natural rapid pun
|       |
|       +-- Are five bytes available?
|       +-- Is A not already E9?
|       +-- Are bytes A+1..A+4 free of decoded instruction heads?
|       +-- Does the supplied scan contain no direct branch into A+1..A+4?
|       `-- Do original bytes A+1..A+4 encode a representable rel32 target?
|           +-- all yes -> RapidPreferred
|           |   |
|           |   +-- exact destination pages are unmapped
|           |   |   `-- map trampoline; toggle A <-> E9 with one-byte stores
|           |   `-- exact mapping collides
|           |       `-- client may use carried relocation fallback
|           `-- any no -> try generic forward relocation
|
+-- Generic forward relocation
|   |
|   +-- Can complete consecutive instructions from A cover >= 5 bytes?
|   +-- Are at least 8 readable/writable alias bytes available?
|   +-- Does the supplied scan contain no direct branch into the interior?
|   +-- Can the client prove no hidden direct, indirect, or external entry?
|   +-- Can a trampoline mapping be placed within signed rel32 reach?
|   `-- Can the client guarantee the code mapping and alias live forever?
|       +-- all yes -> bind InstalledHook
|       |   |
|       |   +-- [A,A+8) is within one cache line
|       |   |   `-- one 8-byte publication store
|       |   `-- [A,A+8) crosses a cache line
|       |       `-- guards -> wait -> back word -> wait -> front word
|       `-- any no -> TrapRequired
|
`-- PLDI'17 upstream/backtracking path (MISSING in LiteInst2)
    |
    +-- Does a five-byte pun cross a basic-block/function boundary, collide
    |   with another probe, or leave an unsafe valid interior PC?
    |   +-- no  -> run full 16-layout constrained-pun search
    |   `-- yes -> scan backward within the same basic block
    |       |
    |       +-- find a 5-byte instruction or good upstream pun
    |       |   +-- patch upstream site
    |       |   +-- if it does not overlap the requested instruction, also
    |       |   |   guard the requested site with a trap during insertion
    |       |   `-- trampoline emulates the whole bypassed span
    |       `-- no usable upstream site in this basic block
    |           `-- steady-state trap probe (or decline activation)
    |
    `-- New site overlaps an already installed probe
        +-- paper: build a super-trampoline with independently toggled
        |   short-circuit branches
        `-- LiteInst2: overlapping atomic envelopes are rejected
```

### All 16 five-byte layouts

PLDI'17 names a layout by whether each byte is an instruction head. `A` is
always a head; each of `B`, `C`, `D`, and `E` may or may not be one, yielding
16 layouts from `Abcde` to `ABCDE`.

| Tail condition | PLDI'17 possibility | LiteInst2 behavior |
|---|---|---|
| Byte belongs to instruction `A`, not a head | Free pun byte | Accepted only as its original value in rapid mode; freely replaced by generic relocation |
| `B`, `C`, or `D` is a head | Leave its complete instruction unchanged, replace the head with `INT3`, or replace it with a microarchitecture-checked illegal opcode | Rapid mode rejects the layout; relocation requires the client to rule out every interior entry |
| `E` is a head | Leave its complete instruction or use an illegal opcode; the paper excludes `INT3` here because `0xCC` in the high displacement byte normally makes the target negative | Rapid mode rejects the layout |
| Tail byte belongs to an instruction headed at `B`, `C`, or `D` | Free only if that head was converted to trap/illegal; otherwise retain the full instruction | No constrained-pun representation |
| Candidate pun targets an occupied/invalid page | Try the next deterministic illegal-op combination, reuse an existing LiteInst page when possible, then bounded fallback | Exact natural target fails; client may relocate or trap |
| Multiple probes want nearby/overlapping ranges | Coalesce into a super-trampoline with per-probe short circuits | Overlap rejected |

Thus LiteInst2 implements the one `Abcde` leaf whose **unchanged** `bcde`
already names free trampoline storage. It does not implement the general
constraint problem described in PLDI'17 Section 2.

## Faithfulness matrix

| Paper mechanism | Status in LiteInst2 | Assessment |
|---|---|---|
| PLDI'16 one-store, same-line WordPatch | Implemented | Recognizable and directly tested |
| PLDI'16/17 cross-line guard/wait/back/wait/front publication | Partial | Same shape, but head-only guards differ from WordPatch++'s all-front-byte guards |
| Calibrated per-machine `Tmax` | API hook only | Caller supplies ticks; no calibration tool or hardware qualification |
| Asynchronous `start_patch` / `finish_patch` | Missing | Current public patch is synchronous |
| Specialized wait-free CallPatch split cases | Missing | No `1|4`, `2|3`, `3|2`, `4|1` call toggler |
| Ideal `Abcde` natural pun | Implemented | Exact mapping plus one-byte E9 toggle |
| Remaining 15 constrained layouts | Missing | No illegal-opcode search or SIGILL path |
| Fixed allocator with bounded deterministic pun search | Missing | Exact mapping only; otherwise relocate/trap |
| Trampoline page reuse and paper-level packing | Missing | Arena consumes 4096 bytes per trampoline |
| Relocation of displaced instructions | Implemented differently | iced-x86 implementation is broader and more defensive |
| Valid-PC preservation inside the five-byte pun | Missing on relocation path | Replaced with a caller no-entry proof |
| Basic-block/function boundary awareness | Missing | Scanner is linear and boundary-agnostic |
| Upstream/backtracking probe selection | Missing | Only forward displacement from requested head |
| Requested-site trap during non-overlapping upstream insertion | Missing | No upstream insertion path |
| Collision coalescing / super-trampolines | Missing | Registered patch envelopes reject overlap |
| Illegal-instruction control-flow rerouting | Missing | Internal router handles only temporary SIGTRAP guards |
| Trap-only steady-state fallback | Client responsibility | `TrapRequired` contains diagnostics only |
| High-level discovery, coordinates, providers, group toggles | Missing by design | Standalone core, not the paper's full LiteInst library |
| Full arbitrary-binary / probe-anywhere evidence | Missing | README explicitly disclaims it |

## MISSING-vs-paper, prioritized

1. **P0 coverage mechanism:** implement constrained puns and preserve/reroute
   every valid interior PC. Without this, "probe anywhere" is false.
2. **P0 site selection:** add CFG/function boundary knowledge, same-block
   backtracking, requested-site insertion guards, and a real trap fallback.
3. **P0 collision semantics:** coalesce nearby probes or formally restrict the
   API; rejecting overlapping eight/sixteen-byte envelopes loses common sites.
4. **P1 cross-modification proof:** either match WordPatch++ exactly or document
   and validate a proof for head-only guarding across all splits and
   multi-instruction windows.
5. **P1 platform calibration:** ship a `Tmax` qualifier/calibrator and bind its
   result to CPU topology/microarchitecture. A raw integer is not a safe default.
6. **P1 signal routing:** add the PLDI'17 SIGILL/INT3 displaced-PC router and
   robust signal-disposition ownership.
7. **P1 memory layout:** reuse trampoline pages and use sub-page slots. The
   current 4 KiB slot makes the paper's 0.09% memory result inapplicable.
8. **P2 omitted PLDI'16 APIs:** asynchronous WordPatch and split-specific
   CallPatch matter for latency/throughput parity, though not initial coverage.
9. **P2 product layer:** discovery, registration groups, instrumentation
   providers, mapping lifecycle, fork/dlopen/JIT handling, and policy remain in
   downstream crates. Cross-repository evidence is required before claiming the
   paper's full system behavior.

## POTENTIAL BUGS and proof gaps

These are not all confirmed crashes. They are concrete hazards that the current
tests and safety contracts do not close.

### POTENTIAL BUG 1: live generic relocation can invalidate in-flight interior PCs

**Severity:** high. **Confidence:** high design gap; adversarial reproducer still
needed.

For a short first instruction, LiteInst2 overwrites the following instruction
heads with arbitrary rel32 bytes. Rejecting known incoming branches and asking
the caller to prove there is no interior entry does not address a thread that
entered at `A` before publication and was preempted at a following instruction.
When it resumes, that old valid PC can decode displacement bytes as code.

The paper's constrained puns retain those instructions or turn their heads into
traps/illegal instructions that reroute to the analogous trampoline offset.
LiteInst2's generic relocation path has no such rerouter. Its concurrent generic
tests and stress fixtures begin with a single five-byte `mov eax, imm32`, so
they contain no displaced interior instruction head. Multi-instruction and
replace-first fixtures are tested functionally, but not with a thread paused at
each interior PC. See the
[generic concurrent fixture](https://github.com/rrnewton/liteinst2/blob/9ffde2830a637eb64de0f77c00e8e28f137cb14b/src/patcher.rs#L1188-L1232)
and [stress image](https://github.com/rrnewton/liteinst2/blob/9ffde2830a637eb64de0f77c00e8e28f137cb14b/tests/stress.rs#L282-L301).

**Required test:** for every cache-line split and every 2-4 byte first
instruction, repeatedly park/resume threads at every displaced interior head
while applying and reverting the patch. Require original-or-hook semantics and
no signal, bad decode, missed callback, or wrong result.

### POTENTIAL BUG 2: head-only WordPatch++ guarding lacks the paper's proof

**Severity:** high if the generic live path is relied on. **Confidence:** medium.

PLDI'17 says its generalized WordPatch++ writes `INT3` to all bytes in the
front fragment. LiteInst2 derives a mask of decoded front-line heads only
([mask construction](https://github.com/rrnewton/liteinst2/blob/9ffde2830a637eb64de0f77c00e8e28f137cb14b/src/patcher.rs#L420-L449)).
That optimization may be valid under stronger fetch/decode assumptions, but
the papers do not prove it and the code provides no replacement proof. It also
does not guard an interior head that falls in the back cache line before that
word is changed.

Treat this as a new protocol requiring its own hardware model, split-complete
stress suite, and microarchitecture qualification, not as established
WordPatch++ faithfulness.

### POTENTIAL BUG 3: the example turns one machine's `Tmax` into a magic number

**Severity:** high portability hazard. **Confidence:** high.

The core correctly warns that Intel does not architecturally guarantee the
protocol and requires a calibrated budget
([contract](https://github.com/rrnewton/liteinst2/blob/9ffde2830a637eb64de0f77c00e8e28f137cb14b/src/patcher.rs#L1-L11)).
PLDI'16 measured different failure thresholds across machines and selected 3000
ticks only after testing its dual-socket host. The public `replace_first`
example nevertheless hard-codes `StalenessBudget::new(3_000)`
([example](https://github.com/rrnewton/liteinst2/blob/9ffde2830a637eb64de0f77c00e8e28f137cb14b/examples/replace_first.rs#L68-L79)).
Copying it onto another CPU can make correctness probabilistic.

### POTENTIAL BUG 4: a later SIGTRAP installation silently disables guards

**Severity:** high integration hazard. **Confidence:** high.

The router captures the previous disposition once, installs itself once, and
never interposes on or detects later `sigaction(SIGTRAP, ...)` calls. An
application/runtime that installs its own handler afterward replaces the
LiteInst2 router; subsequent guarded publication can then execute through
partially published code. The crate needs explicit signal ownership or a
composable dispatcher contract, plus a health check before publication. The
one-time installation is visible in
[`ensure_installed`](https://github.com/rrnewton/liteinst2/blob/9ffde2830a637eb64de0f77c00e8e28f137cb14b/src/trap.rs#L269-L301).

### POTENTIAL BUG 5: generated return transfer is incompatible with CET IBT

**Severity:** medium-high on CET-enforced processes. **Confidence:** high from
instruction form; runtime confirmation needed on enabled hardware.

The trampoline returns with `jmp qword ptr [rip+0]` to an arbitrary instruction
after the displaced window
([absolute jump](https://github.com/rrnewton/liteinst2/blob/9ffde2830a637eb64de0f77c00e8e28f137cb14b/src/trampoline.rs#L589-L593)).
Under Intel CET Indirect Branch Tracking, an indirect jump target must normally
start with `ENDBR64`; an arbitrary continuation does not. Because the trampoline
is already constrained to rel32 reach, a direct relative return jump should be
investigated. Add IBT-enabled CI and shadow-stack tests before claiming modern
x86-64 transparency.

### POTENTIAL BUG 6: faults, signals, and unwinding observe trampoline PCs

**Severity:** medium. **Confidence:** medium-high.

Relocated loads, branches, calls, and other faulting instructions execute from
the trampoline. There is no PC translation/unwind metadata layer. A synchronous
fault, signal frame, restartable operation, profiler, or unwinder can therefore
observe a generated address instead of the application PC. The papers focus on
profiling probes and displaced-code execution but do not make this downstream
problem disappear. Arbitrary-instruction support needs an explicit exception,
signal, and unwind contract. The emitted order is directly visible in
[`TrampolinePlan::emit_at`](https://github.com/rrnewton/liteinst2/blob/9ffde2830a637eb64de0f77c00e8e28f137cb14b/src/trampoline.rs#L475-L523).

## Evidence at the audited SHA

Local commands were run from a clean archive of `9ffde283...`, not from the
one-commit-stale primary checkout:

```text
cargo test --all-targets --all-features --locked
  PASS: 61 unit tests + 1 stress-target test; 3 long/benchmark tests ignored

cargo test --release --test stress live_probe_stress_matrix -- --ignored --exact --nocapture
  PASS: 128 rapid probes, 1,000,000 rapid stores, 16 general hooks,
        10,000 general cycles, 30,000 delivered signals,
        2,581,138 executed calls, 117,194 callbacks
```

GitHub checks for the same SHA were green:

- [Linux x86_64 tests](https://github.com/rrnewton/liteinst2/actions/runs/30720893324/job/91424393061)
- [Format and lint](https://github.com/rrnewton/liteinst2/actions/runs/30720893324/job/91424393005)

These results support the implemented narrow paths. They do not cover the
missing paper paths or discharge the potential bugs above.

## Recommended claim language

Safe current wording:

> LiteInst2 implements a tested natural-pun fast path, relocation-aware
> trampolines, and an experimental WordPatch++-style publisher for Linux
> x86-64. It is a standalone core that requires client-owned discovery,
> control-flow proofs, mapping lifetime, machine calibration, and trap fallback.

Avoid until the missing mechanisms and adversarial tests land:

- "faithful LiteInst port"
- "probe any instruction"
- "arbitrary binaries"
- "safe on x86-64" without a qualified CPU/topology and signal/CET contract
- paper memory/performance numbers applied to the 4 KiB-per-slot arena
