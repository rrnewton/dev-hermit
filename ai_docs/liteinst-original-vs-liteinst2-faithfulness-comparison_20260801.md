# Original LiteInst vs LiteInst2: independent faithfulness comparison

**Review date:** 2026-08-01

**Reviewer lane:** independent comparator, faithfulness review 3/3

**Original source:** [`iu-parfunc/liteinst@b264b0a0`](https://github.com/iu-parfunc/liteinst/tree/b264b0a0e61cfd94d2a05378956173b2b6b1a124)

**LiteInst2 source:** [`rrnewton/liteinst2@9ffde283`](https://github.com/rrnewton/liteinst2/tree/9ffde2830a637eb64de0f77c00e8e28f137cb14b)

**Input reports:** [original C++ review](https://github.com/rrnewton/dev-hermit/blob/12ff614975ce4b99513d68aa9a6bec83e1289373/ai_docs/liteinst-original-cpp-faithfulness-review_20260801.md) and [LiteInst2 review](https://github.com/rrnewton/dev-hermit/blob/2dc990167b4b7b82332753d5a34e41e9717d4339/ai_docs/liteinst2-paper-faithfulness-architecture_20260801.md)

**Specification:** [PLDI 2016 WordPatch](https://doi.org/10.1145/2908080.2908084) and [PLDI 2017 Instruction Punning](https://doi.org/10.1145/3062341.3062344)

## Verdict

LiteInst2 is not a faithful port of the original artifact, and neither artifact
is a complete faithful implementation of the PLDI 2017 design.

The port is not simply "original LiteInst rewritten in Rust." It keeps the
five-byte jump, live publication, relocation, and trampoline ideas, but replaces
most instruction-punning coverage with a generic relocation path and explicit
caller safety obligations. That trade produces a smaller, better tested, more
fail-closed core, but it removes mechanisms central to the paper's
"probe-anywhere" argument and introduces a serious in-flight-PC proof gap.

The highest-impact differences are:

1. The original implements a partial constrained-pun search with illegal
   opcodes and SIGILL rerouting. LiteInst2 implements only the ideal `Abcde`
   natural-pun leaf, and only when the *unchanged* four tail bytes already point
   to free memory. All other sites use ordinary forward relocation or fail.
2. The original groups nearby probes and rebuilds overlapping springboards as
   independently togglable super-trampolines. LiteInst2 rejects overlapping
   patch envelopes.
3. The original has a controller, preload bootstrap, symbol/coordinate
   discovery, partial group APIs, asynchronous PointPatch, and a separate
   CallPatch library. LiteInst2 is deliberately only a caller-driven patching
   core and has none of those product surfaces.
4. LiteInst2 adds exact snapshot revalidation, direct-target checks, typed
   failure, overlap reservation, safer mappings, much fuller architectural
   state preservation, and meaningful stress tests. The original artifact's
   shipped end-to-end cases all crashed in the parallel review.
5. Both miss correct same-basic-block upstream selection, a steady-state trap
   fallback, the full 16-layout search, robust signal coexistence, calibrated
   publication timing, and a PC/unwind translation contract.

## Correction to the two input reports

The reports conflict about WordPatch++ and only the LiteInst2 report is correct
on this point. PLDI 2017 says the generalized protocol writes INT3 traps to
**"all bytes before the split"**, specifically so the patcher need not parse the
instruction layout. The [original review instead says every instruction head](https://github.com/rrnewton/dev-hermit/blob/12ff614975ce4b99513d68aa9a6bec83e1289373/ai_docs/liteinst-original-cpp-faithfulness-review_20260801.md#L186-L194).

This correction does not vindicate either implementation. The original
[`patch_64_plus`](https://github.com/iu-parfunc/liteinst/blob/b264b0a0e61cfd94d2a05378956173b2b6b1a124/libpointpatch/src/patcher.c#L342-L435)
decodes the window and marks only instruction heads. LiteInst2 likewise builds
a [decoded-head guard mask](https://github.com/rrnewton/liteinst2/blob/9ffde2830a637eb64de0f77c00e8e28f137cb14b/src/patcher.rs#L420-L449).
Both therefore implement a head-only variant not proved by the paper. The
LiteInst2 report correctly flags this as a new protocol/proof obligation; the
original report missed the same divergence in the original code.

The original report also omits two real repository components relevant to a
port comparison: [`async_patch_64`](https://github.com/iu-parfunc/liteinst/blob/b264b0a0e61cfd94d2a05378956173b2b6b1a124/libpointpatch/src/patcher.c#L742-L869)
and the separate split-specific [`libcallpatch`](https://github.com/iu-parfunc/liteinst/blob/b264b0a0e61cfd94d2a05378956173b2b6b1a124/libcallpatch/src/wait_free.c#L78-L109).
They are rough, and the LiteProbes provider does not use the asynchronous path,
but they exist and LiteInst2 has no equivalents. They must not be treated as
production-ready features: the async non-straddler branch passes a 64-bit value
to a 32-bit write and `finish_patch_64` immediately asserts; CallPatch admits
that split positions beyond its four special cases use an "iffy" raw write.

## Complete capability and behavior crosswalk

| Dimension | Original LiteInst C++ | LiteInst2 | Discrepancy |
|---|---|---|---|
| Product boundary | LD_PRELOAD library with controller, provider, discovery, registration, and patching | Standalone Rust patch/trampoline core | Most high-level original behavior is absent by design in LiteInst2 |
| Bootstrap | Rewrites 14 bytes of `main`, runs an RWX save/restore stub, then calls premain | None | Client owns startup and injection |
| Discovery | Reads `/proc/self/exe` static `.symtab` functions; no general DSO/stripped/JIT support | Caller supplies a code region and executable/writable aliases | Original is partial and brittle; LiteInst2 delegates all discovery |
| Coordinates | Partial function, basic-block, and address placements; several placements unimplemented | None | Original API surface is not ported |
| Decode | diStorm over discovered functions | iced-x86 over an exact caller-supplied snapshot | LiteInst2 is stricter and more explicit |
| Stale-code defense | No complete plan/install byte-snapshot revalidation | Rescans and compares snapshots; bind verifies live bytes | LiteInst2 fail-closes a race the original does not |
| CFG knowledge | Analysis computes some BB facts, but injection ignores BB boundaries | Linear scan only; caller must prove hidden entries absent | Neither closes the paper's CFG obligation |
| Direct branch targets | No equivalent injection-time interior-target rejection | Rejects direct near branches into the displaced interior visible in the supplied scan | LiteInst2 catches a useful subset; indirect/external targets remain caller proof |
| Hook semantics | Entry/exit callouts or raw instrumentation bytes | `Observing` and `ReplaceFirst` callbacks with mutable `HookContext` | Replace-first is a LiteInst2 extension; raw blobs/providers are not ported |
| Ideal `Abcde` pun | Tail bytes are free: arena may choose and write any rel32 displacement | Tail bytes must remain unchanged and already encode one exact available mapping | LiteInst2's rapid path is much narrower than both paper and original |
| Other 15 layouts | Partial: every downstream head must be one of 14 hard-coded illegal opcodes | No constrained-pun representation; rapid mode rejects them | Major coverage loss in LiteInst2 |
| Original-instruction pun choice | Missing | Missing | Both omit a major part of the paper search |
| INT3 pun choice at B/C/D | Missing | Missing | Both omit a major part of the paper search |
| Illegal-opcode route | Persistent SIGILL router maps displaced heads to trampoline offsets | None; SIGTRAP router is only for temporary publication guards | LiteInst2 loses the original's valid-interior-PC mechanism |
| Illegal-opcode qualification | Hard-coded 2017 list, including `0x62`, with no CPU validation | Avoids steady-state illegal opcodes | Original mechanism is unsafe on newer decoding; LiteInst2 avoids that risk by dropping the feature |
| First instruction >= 5 bytes | Direct arena pun | Natural pun if exact target maps; otherwise generic relocation | Original remains instruction-pun based; LiteInst2 changes strategy |
| Multi-instruction direct site | Fixed illegal-opcode pun and displaced-PC routing | Unconstrained E9 relocation, requiring a caller no-interior-entry proof | LiteInst2 may overwrite a thread's still-live interior PC |
| Cross-BB site | May extend forward or backtrack geometrically without a BB gate | Always collects forward from A, or returns `TrapRequired` | Both diverge from paper in different ways |
| Upstream site | Accidental/geometric backward coalescing, not paper policy | No backward path | Original can move; LiteInst2 never does; neither is correct paper backtracking |
| Original-target insertion trap | Missing | Missing | Both omit the concurrency/CFG guard required for a non-overlapping upstream site |
| Trap fallback | None; allocation/group failure aborts registration | `TrapRequired` diagnostic delegated to client | LiteInst2 names the requirement but does not implement it |
| Rapid allocation collision | Arena/fixed search chooses another representable candidate | Exact natural mapping fails; client may choose carried relocation fallback | LiteInst2 gives up rapid toggling after one address |
| Generic trampoline allocation | Arena and fixed allocators; intended page reuse, but multiple allocator bugs | Fresh near memfd mapping or 4096-byte arena slot, `MAP_FIXED_NOREPLACE` style placement | LiteInst2 is safer but far less memory dense |
| Collision handling | Groups nearby probes and subsumes installed springboards into super-trampolines | Reservation registry rejects overlapping atomic envelopes | LiteInst2 loses independently togglable coalesced probes |
| Duplicate site | Immediate rejection; failed metadata can make retries look duplicate | Overlap rejection through process-lifetime reservations | Both reject duplicates, but original can poison retries after failure |
| Relocation | Custom relocation for RIP-relative data, calls, jumps, and branches; confirmed defects | iced-x86 block encoder with broader defensive handling | LiteInst2 is materially stronger here |
| Context preservation | Pushes flags and integer registers only | Protects red zone; saves flags, GPRs, x87/SIMD through AVX-512, and PKRU | LiteInst2 closes major transparency holes |
| Return from trampoline | Direct E9 rel32 | `jmp qword ptr [rip+0]` indirect absolute transfer | LiteInst2 adds a CET-IBT incompatibility absent from the original direct return |
| Fault/unwind PC translation | None | None | Both expose generated PCs for relocated faults, signals, profiling, and unwinding |
| Initial state | Registration installs an active springboard (`active_probes = n_probes`) | Installation is initially inactive | Observable lifecycle difference |
| Single-probe toggle | First/last activity installs/restores pun; super-trampoline callouts use two-byte short circuits | Rapid probe toggles one opcode byte; generic hook applies/reverts one eight-byte window | Different hot paths and state machines |
| Concurrent transition policy | Per-springboard spinlock serializes activation | Atomic state returns `TransitionInProgress` to a competing caller | LiteInst2 exposes contention rather than waiting |
| Group toggling | Probe-group loop exists; registration-wide overloads are empty/undefined | No group abstraction | Original is partial and one bulk API is defective; LiteInst2 omits it |
| Same-line publication | Configurable `WRITE`, normal build reports non-atomic plain store | Expected-byte checked eight-byte publication | LiteInst2 is more defensive |
| Cross-line publication | Head guards, wait, back, wait, front; CAS-failure fallthrough can do unsafe full write | Head guards, caller TSC budget, back, wait, front; registry and conflict errors | Same unproved head-only idea; original has an additional confirmed failure path |
| Timing calibration | Default 2000 empty-loop iterations, environment override, optional compile-time RDTSC variants | Explicit `StalenessBudget`; public example hard-codes 3000 ticks | Neither ships CPU/topology qualification; LiteInst2 makes the obligation clearer |
| Asynchronous patch | `async_patch_64` plus patch-list progress/try-finish path; not used by LiteProbes and contains 32-bit-write/finish defects | Missing | Mechanism was not ported, but original code is not a sound implementation to reuse |
| CallPatch | Separate wait-free split handlers for 1/4, 2/3, 3/2, 4/1 call layouts; other cases take an admitted raw-write shortcut | Missing | Mechanism was not ported; original requires its own qualification |
| Memory permissions | RWX bootstrap/trampoline behavior | Separate RW/RX aliases; arena leaves an RW alias to executable bytes | LiteInst2 improves exposure but is not strict W^X |
| Mapping lifetime | Implicit globals and process-lifetime allocations | Explicit unsafe process-lifetime contract; reservations intentionally persist | LiteInst2 documents rather than hides the obligation |
| Signal coexistence | Installs SIGTRAP and SIGILL handlers, does not transparently chain, and uses locks/maps/asserts in SIGILL | Captures/chains the prior SIGTRAP disposition for unrelated traps, but a later handler replaces it | LiteInst2 improves coexistence but does not solve ownership |
| Evidence | Utility tests pass; all six repaired direct preload cases SIGSEGV | 61 unit tests, stress target, 1M rapid stores, 10K generic cycles, 30K signals, green CI | LiteInst2 has much stronger evidence for its narrower envelope |

## Reconciled patch-site decision tree

The tree below compares actual implementation choices, not intended paper
behavior.

```text
Requested instruction head A
|
+-- Original C++
|   |
|   +-- address resolves to discovered function and function >= 5 bytes?
|   |   +-- no  -> group fails (metadata may already be inserted)
|   |   `-- yes -> sort/group requested sites
|   |
|   +-- site/group span < 5 bytes?
|   |   +-- near function start -> extend forward
|   |   `-- otherwise           -> walk backward by whole instructions
|   |                              (no basic-block gate)
|   |
|   +-- first instruction alone covers 5 bytes?
|   |   +-- yes -> arena chooses a rel32 target; write arbitrary free tail bytes
|   |   `-- no  -> require every later head to be one of 14 illegal opcodes;
|   |              fixed allocator searches candidate pages
|   |
|   +-- allocation/search succeeds?
|   |   +-- no  -> fail whole group; no alternate upstream site or trap
|   |   `-- yes -> emit relocation/callouts and install shared E9 pun
|   |
|   `-- overlaps nearby/new/installed probe?
|       +-- yes -> coalesce/rebuild super-trampoline with per-probe circuits
|       `-- no  -> ordinary springboard
|
`-- LiteInst2
    |
    +-- supplied scan decodes, matches snapshot, and contains A?
    |   +-- no  -> TrapRequired/error (client owns fallback)
    |   `-- yes -> continue
    |
    +-- ReplaceFirst?
    |   +-- yes -> generic forward relocation
    |   `-- no  -> try natural rapid plan
    |              +-- no interior head/direct target, A != E9, and unchanged
    |              |   suffix encodes an exact available target -> one-byte pun
    |              `-- otherwise -> generic forward relocation
    |
    +-- generic path covers >= 5 bytes with complete forward instructions,
    |   no known interior direct target, rel32 trampoline, and caller proofs?
    |   +-- no  -> TrapRequired/error
    |   `-- yes -> bind initially inactive eight-byte live patch
    |
    `-- any registered atomic envelope overlaps?
        +-- yes -> reject
        `-- no  -> reserve for process lifetime
```

### Scenario-by-scenario divergence

| Scenario | PLDI 2017 | Original C++ | LiteInst2 |
|---|---|---|---|
| `Abcde`, arbitrary free target | Choose arena displacement | Supported | Unsupported by rapid path unless original suffix already names target; may relocate |
| Tail contains heads | Search original/INT3/illegal combinations | Illegal-only subset | No constrained pun; relocate under caller proof |
| Pun crosses successor BB | Backtrack within same BB | Forward extension or ungated geometric backtrack | Forward relocation only |
| Good five-byte upstream instruction | Patch upstream and trap target during insertion | May reach it geometrically, without policy/target trap | Never considered |
| Good multi-instruction upstream pun | Patch upstream and trap target | No complete pun search or target trap | Never considered |
| No usable same-BB upstream | Steady-state trap probe | Fail or cross boundary | `TrapRequired` returned to client |
| Fixed search exhausts | Trap or client-selected failure | Fail group, possible stale registration | Relocation or delegated trap |
| Nearby new probes | Coalesce | Coalesce | Reject overlap |
| New probe overlaps installed site | Rebuild super-trampoline | Attempts subsumption/rerouting | Reject overlap |
| Thread resumes at displaced interior head | Preserve or route through trap/illegal head | SIGILL route for constrained pun | Unprotected on generic path |
| Cross-line front fragment | Trap every byte before split | Trap decoded heads only | Trap decoded heads only |

## LiteInst2 potential bugs: comparison against the original

This section carries forward all six risks flagged by the LiteInst2 report and
states whether the original avoids, shares, or worsens each one.

| LiteInst2 finding | Original comparison | Result |
|---|---|---|
| 1. In-flight interior PC can resume in rel32 data during generic relocation | Original constrained puns make downstream heads illegal and its SIGILL router maps them to relocated offsets. This is the mechanism LiteInst2 removed. Original still has unrelated router and relocation defects. | Primarily a LiteInst2 regression in the generic path |
| 2. Head-only WordPatch++ guard lacks the paper's proof | Original `patch_64_plus` also decodes and guards heads, contradicting the paper's all-byte protocol. It additionally falls through to a full write after failed CAS. | Shared proof gap; original implementation is worse |
| 3. Example hard-codes a 3000-cycle `Tmax` | Original defaults to 2000 empty-loop iterations, accepts an environment override, and has optional build-time wait variants, with no per-machine qualification. | Shared portability bug; LiteInst2's typed budget is clearer but example remains unsafe |
| 4. Later SIGTRAP installation disables guards | Original installs its handler once too, and also replaces rather than transparently composes with a prior handler. Its separate SIGILL path uses non-signal-safe maps/locks and asserts unknown PCs. | Shared ownership bug; original is worse |
| 5. Indirect trampoline return violates CET IBT | Original emits a direct E9 rel32 return to the application continuation. | LiteInst2-specific regression; original avoids this exact transfer problem |
| 6. Faults/signals/unwinding observe trampoline PCs | Original also runs relocated instructions from generated addresses and has no reverse PC/unwind metadata. | Shared gap |

## Confirmed original defects not carried forward as LiteInst2 defects

The original review found concrete implementation failures. LiteInst2 omits the
affected feature or uses a different implementation in each case:

| Original defect | LiteInst2 status |
|---|---|
| Registration-wide `activate`/`deactivate` have empty bodies and no return | No registration/group API; individual hook transitions are implemented and tested |
| Function-callout trampoline sizing tests the provider mode backwards | Different typed trampoline emitter; not applicable |
| Fixed allocator's `B=ILLOP,C=ILLOP` loop decrements unsigned `i` from zero | No fixed illegal-opcode allocator |
| Control-transfer-ended trampoline consumes uninitialized `ret.size` | Different iced-x86 emission path |
| Failed front CAS in `patch_64_plus` falls through to unconditional full write | Publication returns contention/conflict errors |
| Probe metadata is inserted before allocation and retained after group failure | Installation objects are returned only after successful bind; failed mappings are discarded |
| Arena region key is used as an address without shifting it back | Different near mapping/arena implementation |
| `/proc/self/maps` heap/stack identification uses inverted string comparisons | Different Rust maps parser; no matching reported defect |
| Failed fixed allocation rolls back with `end - end` zero length | Different RAII-like error cleanup |
| Hard-coded illegal opcode set is not qualified for the CPU (`0x62` is now EVEX) | No steady-state illegal-opcode use |
| SIGILL route can assert or index past its relocation table and uses locks/maps in a handler | No steady-state SIGILL route; temporary SIGTRAP router has a narrower role |
| Async non-straddler path writes through a `uint32_t *`, truncating its 64-bit patch, while `finish_patch_64` begins with `assert(false)` | No asynchronous API |
| All six repaired preload examples crash with SIGSEGV | LiteInst2's audited unit/stress/CI suite passes |

## Additional comparator finding: original context corruption risk

The original review did not flag a major trampoline difference visible in the
source. Its [context save/restore](https://github.com/iu-parfunc/liteinst/blob/b264b0a0e61cfd94d2a05378956173b2b6b1a124/libliteinst/src/liteprobes/code_jitter.cpp#L27-L92)
pushes flags and integer registers only. Those pushes consume 120 bytes below
the application stack pointer, overwriting most of the System V 128-byte red
zone. It also does not save x87, XMM/YMM/ZMM, or PKRU state before calling
instrumentation. A leaf function using red-zone locals, or a callback that
clobbers vector/FPU state, can therefore change application state.

LiteInst2 explicitly protects the red zone and saves/restores flags, integer
registers, x87/SIMD state through AVX-512, and PKRU in its
[trampoline emitter](https://github.com/rrnewton/liteinst2/blob/9ffde2830a637eb64de0f77c00e8e28f137cb14b/src/trampoline.rs#L359-L579),
with dedicated red-zone and vector-state tests. This is a real correctness
improvement, not just a Rust rewrite.

## Missing or unproved in both

1. Complete 16-layout constrained search: the original is illegal-only and
   LiteInst2 has no constrained representation.
2. Basic-block-aware direct/upstream choice, including same-block backtracking
   and the just-in-case original-target trap.
3. An implemented steady-state one-byte trap fallback.
4. The paper's all-front-byte WordPatch++ protocol, or a replacement proof for
   head-only guarding across every cache-line split.
5. A machine/topology-specific staleness calibrator and qualification gate.
6. Transparent, composable signal-handler ownership.
7. Reverse PC mapping and unwind metadata for faults/signals in relocated code.
8. Strict W^X for all executable storage throughout its lifetime.
9. Complete stripped-binary, DSO, JIT, fork, unload, and remap lifecycle.
10. Exhaustive adversarial tests over all layouts, interior PCs, split points,
    allocation collisions, upstream outcomes, and concurrent toggles.

## Porting priorities

For LiteInst2 to become a faithful and safer successor, priority should be:

1. **P0:** close the in-flight interior-PC hole. Implement constrained
   preserve/trap/illegal choices with safe rerouting, or prohibit live generic
   multi-instruction relocation until an equivalent mechanism exists.
2. **P0:** add real CFG-aware same-block backtracking, original-target insertion
   guards, and an implemented trap fallback.
3. **P0:** add collision coalescing/super-trampolines or narrow the public claim
   to non-overlapping preselected sites.
4. **P1:** replace head-only WordPatch++ with the paper protocol or supply a
   hardware model and split-complete proof/test suite; ship calibration.
5. **P1:** replace the CET-hostile indirect continuation jump with a compatible
   direct transfer when possible and test under IBT/shadow-stack enforcement.
6. **P1:** define signal ownership and reverse-PC/unwind behavior.
7. **P2:** port asynchronous PointPatch and CallPatch only after the coverage
   and correctness mechanisms above. Their presence in the original does not
   make its rough implementations suitable for direct reuse.
8. **P2:** build the product layer (discovery, coordinates, providers, groups,
   module/JIT lifecycle) outside or above the core with explicit cross-crate
   evidence.

## Evidence and limits

This comparison read both reports in full, checked both decision trees, and
inspected the cited source at the exact revisions above. The PLDI 2016/2017
review copies matched the SHA-256 digests recorded in the input reports. The
WordPatch++ wording was resolved directly from PLDI 2017, not inferred from
either implementation.

No new product code or runtime experiment was needed: this task compares
architecture and documented evidence. The validation results remain those of
the two independent lanes: original utility tests pass but repaired end-to-end
preload cases crash; LiteInst2's narrower unit, stress, and CI envelope passes.

Safe claim language is therefore:

> LiteInst2 is a tested, policy-free live-patching and trampoline core inspired
> by LiteInst. It improves validation, relocation, state preservation, mapping,
> and test quality, but it is not a faithful port: it lacks most constrained
> instruction puns, displaced-PC routing, CFG-aware upstream placement,
> trap fallback, collision coalescing, and the original product/API layer.
