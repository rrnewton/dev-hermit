# Original LiteInst upstream relocation

## Bottom line

The optimization is in the original `iu-parfunc/liteinst` C++ artifact. The
best stable reference is the PLDI release tag `v0.4`, commit
[`ce70c4625e10a0f685a8ca4140f3ccb6f6709521`](https://github.com/iu-parfunc/liteinst/commit/ce70c4625e10a0f685a8ca4140f3ccb6f6709521).

Its live implementation is split across:

- [`LiteProbeInjector::coalesceProbes`](https://github.com/iu-parfunc/liteinst/blob/ce70c4625e10a0f685a8ca4140f3ccb6f6709521/libliteinst/src/liteprobes/liteprobe_injector.cpp#L264-L312), which enlarges a too-short probe range over complete instructions and normally moves its start upstream;
- [`punAddress`](https://github.com/iu-parfunc/liteinst/blob/ce70c4625e10a0f685a8ca4140f3ccb6f6709521/libliteinst/src/liteprobes/liteprobe_injector.cpp#L50-L132), which records instruction heads inside the five-byte jump word as constraints on a valid pun/trampoline address; and
- [`CodeJitter::emitSpringboard`](https://github.com/iu-parfunc/liteinst/blob/ce70c4625e10a0f685a8ca4140f3ccb6f6709521/libliteinst/src/liteprobes/code_jitter.cpp#L173-L321), which relocates the enlarged multi-instruction range and inserts the logical probe at its original address.

## Concrete algorithm

1. `injectProbes` disassembles the containing function once and passes its
   decoded instruction sequence to `coalesceProbes`
   ([source](https://github.com/iu-parfunc/liteinst/blob/ce70c4625e10a0f685a8ca4140f3ccb6f6709521/libliteinst/src/liteprobes/liteprobe_injector.cpp#L399-L431)).
2. Nearby requested probes no more than five bytes apart are first coalesced.
   The candidate displaced range initially runs from the earliest probe to the
   end of the last probe instruction.
3. If that range is shorter than the five bytes needed by an x86-64 `e9 rel32`
   jump, the code grows it by whole decoded instructions. Normally it decrements
   the instruction index and grows backward until the range is at least five
   bytes. Within five bytes of function entry it instead grows forward, so it
   never scans before the function. The new patch address is `range.start`.
4. `punAddress` examines the instructions beginning at the new patch address.
   A single displaced instruction permits normal arena allocation. Multiple
   instructions cause every interior instruction-head byte in the five-byte
   jump offset to receive an `ILLOP` constraint, and the fixed-address allocator
   searches for a trampoline whose relative address encodes those constraints.
5. `emitSpringboard` disassembles the entire enlarged range, records every
   instruction offset, and sets `n_relocated` to the decoded instruction count.
   It relocates the code from the upstream start to the requested probe, emits
   the instrumentation callout there, relocates the remainder, then jumps back
   to `range.end`. The relocator copies position-independent instructions and
   rewrites position-dependent control flow, including expansion of short
   branches that no longer reach
   ([source](https://github.com/iu-parfunc/liteinst/blob/ce70c4625e10a0f685a8ca4140f3ccb6f6709521/libliteinst/src/liteprobes/relocator.cpp#L58-L129)).
6. Injection publishes the pun at the upstream springboard base. Probe heads
   beyond the five-byte patch window are replaced with the one-byte illegal
   opcode `0x62`; the SIGILL router maps an entry at any displaced instruction
   head to its relocated counterpart
   ([injection](https://github.com/iu-parfunc/liteinst/blob/ce70c4625e10a0f685a8ca4140f3ccb6f6709521/libliteinst/src/liteprobes/liteprobe_injector.cpp#L453-L549),
   [routing](https://github.com/iu-parfunc/liteinst/blob/ce70c4625e10a0f685a8ca4140f3ccb6f6709521/libliteinst/src/liteprobes/control_flow_router.cpp#L103-L118)).

Example: for `I0(2 bytes), I1(2), RET(1)` with a logical probe on `RET`, the
five-byte jump can be placed at `I0`. The trampoline executes relocated `I0`,
then `I1`, invokes the logical probe at the `RET` position, and executes the
relocated `RET`. The patch therefore gets enough room without requiring a NOP
sled or a five-byte instruction at the requested site.

There are two important scope qualifications:

- The release code performs one immediate whole-instruction range extension;
  it does not implement the paper's full search over alternative upstream
  candidates or an explicit CFG/basic-block-boundary check. The more ambitious
  `Backtrack until punnable instruction is met` logic remains a comment at the
  end of `injectProbes`
  ([source](https://github.com/iu-parfunc/liteinst/blob/ce70c4625e10a0f685a8ca4140f3ccb6f6709521/libliteinst/src/liteprobes/liteprobe_injector.cpp#L566-L579)).
- A pun straddling a *basic-block boundary* and a patch straddling a *cache-line
  boundary* are separate problems. Site movement and relocation address the
  former. `patch_64_plus`/WordPatch++ addresses atomic publication of the
  latter.

## Paper coverage

Yes, PLDI 2017 discusses the optimization directly. In
[*Instruction Punning: Lightweight Instrumentation for x86-64*](https://doi.org/10.1145/3062341.3062344),
Section 5.2, **Probe Site Selection**, the subsection **Backtracking: moving the
probe site** says LiteInst searches upstream when a five-byte pun would cross a
basic-block or function boundary. It describes selecting an upstream five-byte
instruction or good pun, placing a trap at the original site when the new pun
does not overlap it, and using a trampoline to emulate the bypassed span.
Section 5.3, **Trampoline Construction and Coalescing**, describes relocation of
the displaced instructions, short-branch expansion, and super-trampolines for
overlapping/nearby probes. A publicly readable copy is
[here](https://static.aminer.org/pdf/20170130/pdfs/pldi/wk5pe0zdtjp7ixuywdm82qrszbvqaym1.pdf).

PLDI 2016 does not contain the upstream-selection optimization. In
[*Living on the Edge: Rapid-Toggling Probes with Cross-Modification on x86*](https://doi.org/10.1145/2908080.2908084),
Section 4, **Programming Interface Overview** (under **Scalable Probes**), the
inactive site must already contain a relocatable sequence of at least five
bytes; it only notes that a trampoline executes displaced instructions.
Section 5, **Word Patching**, is the cache-line-straddler-safe publication
protocol. The author-hosted paper is
[here](https://svenssonjoel.github.io/writing/pldi16-crossmod.pdf).

## Porting guidance

For LiteInst2, the reusable idea is not merely "collect five bytes after the
requested PC." Plan a displaced interval on decoded instruction boundaries,
allow its start to precede the logical hook, relocate the complete interval,
and emit the hook at the logical PC inside that relocated stream. The missing
proof obligation is control-flow entry: every direct or indirect entry into the
displaced interval must either be rejected or routed to the corresponding
relocated instruction. Cache-line-safe patch publication remains an independent
requirement.
