# Original LiteInst C++: architecture and PLDI faithfulness review

**Review date:** 2026-08-01

**Scope:** the original `iu-parfunc/liteinst` C++ artifact, reviewed independently
against the PLDI 2016 and PLDI 2017 papers

**Source revision:** [`b264b0a0e61cfd94d2a05378956173b2b6b1a124`](https://github.com/iu-parfunc/liteinst/tree/b264b0a0e61cfd94d2a05378956173b2b6b1a124)
(`v0.4-7-gb264b0a`, 2018-04-11)

**Bottom line:** the repository contains recognizable implementations of
instruction punning, trampoline coalescing, signal rerouting, and WordPatch++.
It is not a faithful, complete, or currently demonstrated implementation of all
the behavior described as LiteInst in the PLDI 2017 paper. The missing fallback
and basic-block decisions are central to the paper's "any instruction" claim,
and several reachable correctness defects make the public artifact unsuitable
as a drop-in instrumentation engine without substantial repair.

## Evidence and interpretation

The papers are treated as the specification for this review:

1. Buddhika Chamith, Luke Dalessandro, Bo Joel Svensson, and Ryan R. Newton,
   ["Living on the Edge: Rapid-Toggling Probes with Cross-Modification on x86"](https://pldi16.sigplan.org/details/pldi-2016-papers/28/Living-on-the-edge-Rapid-toggling-probes-with-cross-modification-on-x86),
   PLDI 2016. This defines WordPatch and the bounded-staleness argument used by
   the later artifact's patch layer.
2. Buddhika Chamith, Luke Dalessandro, Bo Joel Svensson, and Ryan R. Newton,
   ["Instruction Punning: Lightweight Instrumentation for x86-64"](https://doi.org/10.1145/3062341.3062344),
   PLDI 2017, pp. 320-332. The
   [conference program record](https://pldi17.sigplan.org/details/pldi-2017-papers/22/Instruction-Punning-Lightweight-Instrumentation-for-x86-64)
   is an accessible bibliographic link.

Local review copies had SHA-256 digests
`c74cd6d099beb490d3dc4571f250c64cb626dee32c7c35de3222b351045668ca`
(PLDI 2016) and
`15f6aa84e8cc1f9f236500176eece8a6000302282358a4bbe9f249ea72857ddd`
(PLDI 2017). Findings below are tied to the public source revision, not to an
unversioned local checkout. `PRESENT`, `PARTIAL`, `MISSING`, and `CONTRADICTED`
describe that revision only.

The PLDI 2017 paper sometimes calls INT3's handler `SIGINT`; on Linux, INT3 is
delivered as `SIGTRAP`. This report uses `SIGTRAP` except when describing that
paper typo.

## Architecture of the public artifact

### End-to-end control flow

```text
LD_PRELOAD client DSO
  |
  +-- initGlobalProbeController(premain callback)
  |     |
  |     +-- constructor patches the first 14 bytes of main
  |     +-- temporary RWX stub saves registers and flags
  |     +-- initializeLiteprobes restores main, installs SIGILL, calls premain
  |
  +-- registerInstrumentation(entry function, exit function or raw bytes)
  +-- registerProbes(Coordinates, InstrumentationProvider)
        |
        +-- read /proc/self/exe ELF .symtab/.strtab
        +-- disassemble selected functions with diStorm
        +-- turn function/BB/address coordinates into probe addresses
        +-- sort, group, and coalesce nearby probe sites
        +-- choose arena or fixed trampoline allocation
        +-- relocate displaced instructions and emit (super-)trampoline
        +-- patch a five-byte E9 pun with WordPatch++
        +-- route SIGILL from overwritten instruction heads to relocations
  |
  +-- activate/deactivate individual probes
        +-- toggle a two-byte trampoline short circuit
        +-- first activation installs the pun; last deactivation restores bytes
```

### Components and responsibilities

| Component | What the source does | Faithfulness |
|---|---|---|
| Public API | Defines instrumentation providers, coordinates, registrations, and a controller in [`include/liteinst.hpp`](https://github.com/iu-parfunc/liteinst/blob/b264b0a0e61cfd94d2a05378956173b2b6b1a124/include/liteinst.hpp#L200-L515). The built factory accepts only `LITEPROBES` in [`probe_provider.cpp`](https://github.com/iu-parfunc/liteinst/blob/b264b0a0e61cfd94d2a05378956173b2b6b1a124/libliteinst/src/liteprobes/probe_provider.cpp#L12-L39). | `PARTIAL` |
| Pre-main bootstrap | A constructor locates `main`, overwrites 14 bytes, runs a register-saving stub, restores the bytes, then calls the client callback in [`boostrap.cpp`](https://github.com/iu-parfunc/liteinst/blob/b264b0a0e61cfd94d2a05378956173b2b6b1a124/libliteinst/src/liteprobes/boostrap.cpp#L120-L249). | `PRESENT`, but narrowly implemented |
| Program discovery | Reads only the main executable's static ELF symbol table and selects `STT_FUNC` symbols in [`process_analyzer.cpp`](https://github.com/iu-parfunc/liteinst/blob/b264b0a0e61cfd94d2a05378956173b2b6b1a124/utils/src/process_analyzer.cpp#L85-L124). diStorm supplies linear decoding and function-local control-flow facts. | `PARTIAL` |
| Coordinate expansion | Function entry/exit/boundary, basic-block entry/exit/boundary, and address entry have code paths in [`liteprobe_provider.cpp`](https://github.com/iu-parfunc/liteinst/blob/b264b0a0e61cfd94d2a05378956173b2b6b1a124/libliteinst/src/liteprobes/liteprobe_provider.cpp#L183-L328). Group generation and explicit unsupported cases appear later in the same file ([lines 330-477](https://github.com/iu-parfunc/liteinst/blob/b264b0a0e61cfd94d2a05378956173b2b6b1a124/libliteinst/src/liteprobes/liteprobe_provider.cpp#L330-L477)). | `PARTIAL` |
| Punning and grouping | Decodes at least five bytes, classifies a one-instruction span as arena-compatible, otherwise constrains every downstream instruction head to an illegal opcode in [`liteprobe_injector.cpp`](https://github.com/iu-parfunc/liteinst/blob/b264b0a0e61cfd94d2a05378956173b2b6b1a124/libliteinst/src/liteprobes/liteprobe_injector.cpp#L40-L132). Nearby sites and old springboards are coalesced ([lines 264-359](https://github.com/iu-parfunc/liteinst/blob/b264b0a0e61cfd94d2a05378956173b2b6b1a124/libliteinst/src/liteprobes/liteprobe_injector.cpp#L264-L359)). | `PARTIAL` |
| Trampoline allocation | Arena allocation handles nominally unconstrained layouts; fixed allocation searches illegal-opcode combinations for constrained layouts. The constraint type has only `UNCONSTRAINED` and `ILLOP` in [`alloc.hpp`](https://github.com/iu-parfunc/liteinst/blob/b264b0a0e61cfd94d2a05378956173b2b6b1a124/utils/include/alloc.hpp#L19-L23). | `PARTIAL` |
| Relocation and trampolines | Emits a short circuit, context save/restore, client callout, relocated instructions, and a jump back; coalesced sites receive super-trampolines in [`code_jitter.cpp`](https://github.com/iu-parfunc/liteinst/blob/b264b0a0e61cfd94d2a05378956173b2b6b1a124/libliteinst/src/liteprobes/code_jitter.cpp#L177-L331). | `PRESENT`, with correctness defects |
| Signal routing | Rewrites `RIP` after `SIGILL` using per-springboard relocation offsets in [`control_flow_router.cpp`](https://github.com/iu-parfunc/liteinst/blob/b264b0a0e61cfd94d2a05378956173b2b6b1a124/libliteinst/src/liteprobes/control_flow_router.cpp#L11-L118). | `PRESENT`, illegal-opcode path only |
| Activation | Individual activation patches the pun on the first active probe and toggles its short circuit; last deactivation restores the original bytes in [`liteprobe_provider.cpp`](https://github.com/iu-parfunc/liteinst/blob/b264b0a0e61cfd94d2a05378956173b2b6b1a124/libliteinst/src/liteprobes/liteprobe_provider.cpp#L680-L793). | `PARTIAL` |
| Cross-modification | `patch_64_plus` uses a simple eight-byte write for non-straddlers and the INT3/wait/back/wait/front protocol for straddlers in [`patcher.c`](https://github.com/iu-parfunc/liteinst/blob/b264b0a0e61cfd94d2a05378956173b2b6b1a124/libpointpatch/src/patcher.c#L342-L435). | `PARTIAL` WordPatch++ |

The repository also contains stale or unfinished alternative-provider files, but
the source Makefile builds the `liteprobes` provider. They should not be counted
as additional implemented backends.

## Paper-required patch-site decision tree

This is the complete PLDI 2017 decision space, reconstructed from Sections 2.2,
2.4, and 5.2. Capital letters are instruction heads; lowercase letters are
continuation bytes. `A` is always the probed instruction head.

### 1. Classify all 16 five-byte layouts

```text
Abcde  AbcdE  AbcDe  AbcDE
AbCde  AbCdE  AbCDe  AbCDE
ABcde  ABcdE  ABcDe  ABcDE
ABCde  ABCdE  ABCDe  ABCDE
```

The layout does not itself choose arena versus fixed allocation. It defines the
legal values of each `bcde` displacement byte:

1. A non-head byte belonging to instruction `A` is free.
2. At each head `B`, `C`, or `D`, recursively choose one of:
   - write `INT3`; every continuation byte of that instruction becomes free;
   - write a microarchitecture-valid illegal opcode; every continuation byte
     becomes free; or
   - preserve the original head and the entire original instruction.
3. At head `E`, choose an illegal opcode or preserve the complete original
   instruction. The paper excludes `INT3` at `E` because `0xCC` makes the signed
   high displacement byte negative in the usual address layout.
4. A continuation byte of `B`, `C`, or `D` is free only when that instruction's
   head was replaced by a trap or illegal opcode. Otherwise it is preserved.
5. Reject a candidate displacement if its target is unavailable or unsuitable;
   continue through the bounded candidate order.

This recursion covers all layouts. For example:

| Layout | Full paper search space |
|---|---|
| `Abcde` | `bcde` are all free: arena candidate with a full 32-bit displacement range. |
| `ABcde` | At `B`, choose trap, illegal, or preserve all of `Bcde`; freed suffix bytes depend on that choice. |
| `AbCde` | `b` is free. At `C`, choose trap, illegal, or preserve all of `Cde`. |
| `ABCde` | Make independent choices at `B` and `C`, except preserving a multi-byte `B` also fixes bytes that would otherwise be interpreted as `C`. Only structurally consistent choices survive. |
| `ABCDE` | Every tail byte is a head. `B/C/D` each choose trap, illegal, or original; `E` chooses illegal or original. This is maximally constrained. |

### 2. Choose a direct site or an upstream site

```text
Does the five-byte pun remain inside the probe's basic block?
  yes
    -> Search a direct pun using the 16-layout rules.
       Is a suitable direct target found within the bounded search?
         yes -> allocate/initialize trampoline, then install the pun.
         no  -> client policy chooses trap-only or failure.
  no
    -> Walk backward only inside this same basic block.
       Is there enough preceding code and a suitable upstream site?
         yes -> put the jump pun at that upstream site;
                relocate/emulate the full upstream-to-target span;
                put a just-in-case trap at the original target.
         no  -> install a trap-only probe at the original target.
                Do not search predecessor basic blocks.
```

An upstream site has two useful paper cases: a single instruction of at least
five bytes, or a shorter sequence with a good legal pun. In both cases the
trampoline covers every bypassed instruction. The extra target trap protects
against concurrent insertion and incorrectly discovered control-flow entries.

### 3. Choose allocation and fallback

```text
Are enough low displacement bytes free for the arena policy?
  yes -> bump-allocate in the arena for the probe's 2^32-byte code region.
  no  -> fixed allocator:
           iterate a fixed order of D/E illegal-opcode pairs (up to 196);
           for an existing/new candidate page, search B/C placement values;
           preserve, trap, or invalidate heads according to the layout;
           stop on the first suitable trampoline slot.
           exhausted?
             -> client-selected one-byte trap-only probe, or give up.
```

### 4. Handle collisions and toggling

```text
Does a new site's displaced span overlap another probe/springboard?
  no  -> emit a normal trampoline.
  yes -> coalesce the span and emit a super-trampoline with one independently
         togglable short circuit per logical probe.

Activate one logical probe:
  enable its short circuit;
  if it is the first active member, atomically install the shared pun.

Deactivate one logical probe:
  disable its short circuit;
  if it was the last active member, atomically restore original site bytes.
```

For a patch that straddles an eight-byte atomic-write boundary, PLDI 2017's
WordPatch++ generalization requires INT3 protection for every instruction head
in the front word, a bounded `T_max` wait, the back write, another wait, and the
front write. PLDI 2016 supplies the cross-modification and bounded-staleness
argument underlying that protocol.

## Decision tree actually implemented

The public code makes a materially smaller set of decisions:

```text
Resolve requested address to a discovered function.
  not found / function smaller than 5 bytes -> fail registration for the site.
  found -> decode whole instructions until span >= 5 bytes.

Does the first instruction alone cover >= 5 bytes?
  yes -> mark all four displacement bytes UNCONSTRAINED; use arena allocator.
  no  -> mark every subsequent instruction head in the span ILLOP;
         use fixed allocator with the hard-coded 14 illegal opcodes.

Before allocation, sort requested addresses and coalesce:
  targets <= 5 bytes apart -> merge;
  span < 5 near function start -> extend downstream until >= 5;
  span < 5 elsewhere -> walk backward by whole instructions until >= 5;
  overlap an existing springboard -> merge it into the new span.

Allocate and emit one springboard for the resulting group.
  allocation or pun search fails -> fail the whole group; no alternate site,
                                    trap fallback, or rollback/retry policy.
  succeeds -> relocate the span, emit one or more callouts, patch the E9 pun,
              and replace downstream logical probe heads with opcode 0x62.
```

### Outcome matrix for every site/upstream class

| Situation | Paper behavior | Public source behavior |
|---|---|---|
| Direct site, first instruction is at least five bytes | Arena-compatible direct pun. | `PRESENT`, arena selected. |
| Direct site has multiple instructions but stays in one basic block | Search trap/illegal/original combinations, then fixed allocation. | `PARTIAL`: every downstream head must become illegal; original and INT3 choices are absent. |
| Direct fixed search exhausts candidate mappings | Bounded trap-only fallback or client-selected failure. | `MISSING`: group fails; no trap-only representation or client policy. |
| Five-byte span crosses into a successor basic block and an upstream five-byte instruction exists in the same block | Move pun upstream, trap original target, emulate entire span. | `MISSING`: coalescing does not consult basic-block boundaries. It may use the original cross-boundary span or geometric backtracking. |
| Same crossing, upstream multi-instruction site has a good pun | Same upstream-plus-target-trap behavior. | `MISSING`: no paper search over alternative upstream puns. |
| Same crossing, no sufficient same-block upstream site | Trap-only at target; never search predecessor blocks. | `CONTRADICTED`: may extend downstream near function start, backtrack without a BB gate, or fail. |
| Two new probes are less than five bytes apart | Composite super-trampoline, independently togglable. | `PRESENT`: new sites are grouped and callouts have independent short circuits. |
| New span overlaps an installed springboard | Replace with a composite springboard and preserve logical probes. | `PRESENT` in design: overlapping springboards are subsumed and rerouted. |
| Control enters a displaced downstream instruction head | Preserve the instruction, or take SIGTRAP/SIGILL and reroute to its relocation. | `PARTIAL`: only hard-coded SIGILL opcode replacement is used. |
| Exact duplicate registration address | Paper does not require failure; it describes probe sets and super-trampolines. | Immediate failure in [`injectProbes`](https://github.com/iu-parfunc/liteinst/blob/b264b0a0e61cfd94d2a05378956173b2b6b1a124/libliteinst/src/liteprobes/liteprobe_injector.cpp#L510-L544). |
| A group fails after probe metadata was inserted | No stale failed registration is implied. | `DEFECT`: entries are inserted before allocation and not removed by the failure path, so a retry can be rejected as a duplicate ([lines 531-544 and 587-592](https://github.com/iu-parfunc/liteinst/blob/b264b0a0e61cfd94d2a05378956173b2b6b1a124/libliteinst/src/liteprobes/liteprobe_injector.cpp#L531-L592)). |

## Missing or contradicted paper behavior

### Correctness-critical

1. **`MISSING`: one-byte trap fallback.** The paper makes trap-only fallback the
   bounded escape from failed fixed placement and from insufficient same-basic-
   block space. The source has no trap-probe representation or SIGTRAP probe
   execution path. Allocation failure simply rejects the group.
2. **`MISSING`: basic-block-safe direct/upstream selection.** The source computes
   basic blocks for coordinate selection, but injection/coalescing never uses a
   basic-block boundary to constrain a five-byte span or backward walk. The
   paper's explicit "do not search predecessor blocks" rule is therefore absent.
3. **`MISSING`: paper's complete pun search.** The allocator constraint model can
   say only free or illegal. It cannot preserve a complete downstream original
   instruction or choose INT3 at `B/C/D`. This removes a large portion of the
   16-layout search space described in Section 2.2.
4. **`MISSING`: target trap for an upstream insertion.** The paper requires the
   just-in-case trap at the original site. Source coalescing may move the group
   start backward and later invalidate logical probe heads with `0x62`, but this
   is not the paper's pre-install target-trap protocol and has no SIGTRAP path.
5. **`DEFECT`: the bulk API shown in the paper is empty.**
   `activate(ProbeRegistration)` and `deactivate(ProbeRegistration)` have empty
   bodies and no return in [`liteprobe_provider.cpp`](https://github.com/iu-parfunc/liteinst/blob/b264b0a0e61cfd94d2a05378956173b2b6b1a124/libliteinst/src/liteprobes/liteprobe_provider.cpp#L822-L828).
   The paper's primary `controller->activate(probes)` workflow is not implemented.
6. **`DEFECT`: function-callout trampoline sizing is reversed.** Emission chooses
   a full saved-context callout when `getInstrumentation() == NULL`, but the size
   calculation accounts for that callout when it is non-null in
   [`code_jitter.cpp`](https://github.com/iu-parfunc/liteinst/blob/b264b0a0e61cfd94d2a05378956173b2b6b1a124/libliteinst/src/liteprobes/code_jitter.cpp#L244-L289)
   and [lines 339-369](https://github.com/iu-parfunc/liteinst/blob/b264b0a0e61cfd94d2a05378956173b2b6b1a124/libliteinst/src/liteprobes/code_jitter.cpp#L339-L369).
   Function-pointer providers therefore reserve too little space before emitting
   the ordinary callout.
7. **`DEFECT`: a reachable fixed-allocator loop decrements from zero.** For the
   `B=ILLOP, C=ILLOP` case, the loop is `i--` while testing
   `i < invalid_opcodes.size()`, causing unsigned underflow/out-of-bounds access
   in [`fixed_alloc.cpp`](https://github.com/iu-parfunc/liteinst/blob/b264b0a0e61cfd94d2a05378956173b2b6b1a124/utils/src/fixed_alloc.cpp#L287-L304).
   Maximally constrained layouts can reach this case.
8. **`DEFECT`: a control-transfer relocation path uses an uninitialized result.**
   `ret.size` is consumed after the relocation call is skipped when
   `is_end_a_control_transfer` is true in
   [`code_jitter.cpp`](https://github.com/iu-parfunc/liteinst/blob/b264b0a0e61cfd94d2a05378956173b2b6b1a124/libliteinst/src/liteprobes/code_jitter.cpp#L292-L326).
9. **`PARTIAL/DEFECT`: WordPatch++ has unsafe fallthroughs.** The normal build's
   `WRITE` is a plain store, and a straddling `patch_64_plus` falls through to an
   unconditional eight-byte write if its front-word CAS fails
   ([configuration lines 92-109](https://github.com/iu-parfunc/liteinst/blob/b264b0a0e61cfd94d2a05378956173b2b6b1a124/libpointpatch/src/patcher.c#L92-L109),
   [patch lines 342-435](https://github.com/iu-parfunc/liteinst/blob/b264b0a0e61cfd94d2a05378956173b2b6b1a124/libpointpatch/src/patcher.c#L342-L435)).
   Internal locks reduce same-library races but do not establish the paper's
   advertised behavior against an independent writer.

### Coverage and operational gaps

1. **`PARTIAL`: coordinate support.** Function and basic-block placements exist;
   loop placement is explicitly unimplemented, instruction/offset selection is
   unimplemented, and address exit/boundary placement is unimplemented. Module
   matching is exposed by the API but is not a meaningful multi-module discovery
   path in the provider.
2. **`PARTIAL`: arbitrary-binary claim.** Discovery requires `.symtab/.strtab`
   function symbols in `/proc/self/exe`. It does not discover stripped binaries,
   shared-library functions, or a general runtime module set. PIE/load-bias
   handling is not evident in this path.
3. **`DEFECT`: arena region calculation.** The allocator keys by `addr >> 32` but
   also uses that shifted value as the address base instead of shifting it back,
   in [`arena_alloc.cpp`](https://github.com/iu-parfunc/liteinst/blob/b264b0a0e61cfd94d2a05378956173b2b6b1a124/utils/src/arena_alloc.cpp#L41-L75).
   Low non-PIE executables can hide this; high ASLR/module addresses cannot.
4. **`DEFECT`: process range identification uses inverted string comparisons.**
   Heap and stack branches are taken for non-equal map names in
   [`process.cpp`](https://github.com/iu-parfunc/liteinst/blob/b264b0a0e61cfd94d2a05378956173b2b6b1a124/utils/src/process.cpp#L47-L71),
   corrupting the coarse exclusion ranges used during placement.
5. **`MISSING`: microarchitecture installation check.** The paper requires a
   library version matched to the microarchitecture because formerly illegal
   opcodes may become valid. Source hard-codes the paper's 14-byte list and does
   not identify the CPU or validate the opcodes. `0x62`, for example, is also the
   EVEX prefix on AVX-512-era processors, so future-decoding concerns are no
   longer hypothetical.
6. **`PARTIAL`: transparent signal coexistence.** The SIGILL route lookup asserts
   when an address is not owned and the handler uses C++ maps/locks rather than
   async-signal-safe primitives. It does not visibly chain an application's
   prior SIGILL handler.
7. **`PARTIAL`: memory protection.** Bootstrap and trampoline pages are writable
   and executable together; there is no W^X transition policy.
8. **`DEFECT`: failed allocation rollback passes a zero length** (`end - end`) in
   [`fixed_alloc.cpp`](https://github.com/iu-parfunc/liteinst/blob/b264b0a0e61cfd94d2a05378956173b2b6b1a124/utils/src/fixed_alloc.cpp#L512-L533).
9. **`PARTIAL`: bounded search is narrower than the paper.** A fixed D/E search
   exists, but allocation failure has neither the paper's trap fallback nor a
   retry at a different upstream location. A source comment identifies such
   backtracking as future work in
   [`liteprobe_injector.cpp`](https://github.com/iu-parfunc/liteinst/blob/b264b0a0e61cfd94d2a05378956173b2b6b1a124/libliteinst/src/liteprobes/liteprobe_injector.cpp#L580-L596).

## What is genuinely present

The gaps should not obscure the substantive artifact:

1. The core instruction-punning idea is implemented: a five-byte `E9 rel32` is
   also interpreted as valid original bytes or illegal instructions at alternate
   entries.
2. The fixed allocator searches candidate pages using the 14 illegal opcodes
   listed in the paper, and the arena allocator represents the unconstrained
   fast path.
3. Displaced RIP-relative operations, calls, jumps, and short branches receive
   relocation logic rather than blind byte copying.
4. Nearby probes are coalesced into a composite trampoline, with independently
   togglable two-byte short circuits.
5. SIGILL routing maps overwritten instruction entries to their corresponding
   relocated instruction offsets.
6. Individual probes have the intended zero-invocation-path-overhead inactive
   state: the original bytes are restored after the last logical probe is
   deactivated.
7. The straddling patch path implements the recognizable front-trap, wait, back,
   wait, front shape of WordPatch++.

These are useful design references. They do not compensate for the missing
fallback decisions because those decisions are what close the difficult patch
sites in the paper's completeness argument.

## Reproduction and validation

All network access used `with-proxy`. Validation was performed in an isolated
scratch clone at the pinned source revision on x86-64 Linux 6.18 with GCC/G++
11.5.0.

| Command | Result |
|---|---|
| `git clone https://github.com/iu-parfunc/liteinst.git` and `git checkout b264...` | Source revision reproduced. |
| Initialize pinned `distorm`, `elph`, `doctest`, and `asmjit` submodules | Completed after translating legacy SSH GitHub URLs to HTTPS. |
| `make lib` | Exit 0. Numerous warnings remain, including missing returns and the point-patcher's `NONATOMIC WRITE` warning. |
| `make quicktest` | Exit 0: 16 doctest cases, 39 assertions. Inspection shows this target tests utilities, not LiteInst injection. |
| `make test` in `libliteinst/tests` | Does not run as shipped: the test Makefile omits the diStorm include path. |
| `make EXTRA_WARNS=-I../../deps/distorm/include test` | Builds the test DSOs/apps, then exits 127 because the runner invokes unavailable `python` rather than `python3`. |
| Directly run all six built preload cases: `reg`, `deact`, and `act`, each against O0 and O3 test apps | Every case exits 139 with `SIGSEGV`. No end-to-end case passed. Each run was bounded by a 15-second timeout. |

The shipped Python runner calls child processes with `os.system` and does not
aggregate their exit status, so fixing only the Python executable name would
still not make the target a reliable pass/fail gate. The build result proves
that much of the source remains compilable; it does not validate the PLDI 2017
correctness or coverage claims.

## Reuse decision

Treat the original C++ repository as an architectural and historical reference,
not as a proven library dependency.

Before a production reuse, the minimum technical gate is:

1. Implement the full layout constraint model, including original-instruction
   preservation and a real trap-only probe path.
2. Make the direct/upstream decision explicitly basic-block-aware and test every
   branch of the decision tree above.
3. Repair the fixed allocator, arena math, trampoline sizing, control-transfer
   relocation, activation APIs, failure rollback, and patch CAS behavior.
4. Replace static-symbol-only discovery with a declared, tested binary/module
   envelope.
5. Establish signal-handler coexistence and async-signal-safety rules.
6. Select/verify illegal opcodes per supported microarchitecture.
7. Add a test oracle that fails on child crashes, plus exhaustive synthetic
   coverage of all 16 layouts, allocation exhaustion, every upstream outcome,
   collisions, straddlers, and concurrent activation/deactivation.

Until those gates are met, claims of "paper-faithful LiteInst" should be limited
to the implemented mechanisms listed above, not the paper's complete arbitrary-
instruction placement and fallback behavior.
