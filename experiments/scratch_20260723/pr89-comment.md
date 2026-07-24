## Review analysis: register determinism vs. what this PR does

Picking up the defense-in-depth concern (hermit must force determinism even for a *badly-behaved* guest that reads registers it "shouldn't"). The concern is legitimate and worth a design note, but it targets a **different register path** than this PR, and the concrete remedy needs one ABI correction. Findings below are code-grounded (reverie-ptrace + detcore).

### 1. This PR is entry-side and comparison-only; the concern is return-side

- **#89** changes only the replay **desync detector**: `syscalls_match()` zeroes argument registers ≥ arity purely to *compare* recorded-vs-replayed **outgoing** syscall arguments (`replayer/mod.rs::expect_syscall`). It is a pure predicate — it never writes guest state.
- **The concern** is about **return-side** register *content the guest can read* after hermit emulates a syscall.

These are complementary, not the same change. Under #89 alone, a pathological guest that reads an unused register after `statfs` would *still* observe nondeterminism — #89 makes the **detector** tolerant, not the **guest** deterministic. So the concern is a real, *separate* hardening, and #89 is not the place to implement it.

### 2. x86-64 Linux syscall-return ABI (the ground truth)

After `syscall`: `rax` = return value; **`rcx` and `r11` are clobbered** (they carry RIP and RFLAGS for `SYSRET`); **every other GP register is ABI-preserved** (`rdi, rsi, rdx, r10, r8, r9, rbx, rbp, rsp, r12–r15`). glibc's syscall wrappers depend on exactly this (clobber list = `rcx, r11, cc, memory`).

Consequence: on the return path there are essentially **no "unused" registers to zero except `rcx`/`r11`**. Every other register has an ABI-defined value — its input value, preserved.

### 3. What hermit does today (so we know what actually needs fixing)

- reverie's emulated-return path already **preserves** registers: `set_ret` (`reverie-ptrace/src/task.rs:534`) is read-modify-write — `getregs` → set only `rax` → `setregs`. It does **not** inject host/kernel garbage into the preserved registers.
- detcore produces only an `i64` (→ `rax`). The `reverie::Guest` trait exposes `regs()` **read-only** and no register-writer, so detcore literally cannot set anything but the return value today.
- So the strongest form of the worry — "hermit dumps host state into registers on return" — is **not** happening on the ordinary emulated path.

**The one genuine leak** is the **injected/rewritten-syscall path** (`open`→`openat`, `vfork`→`clone`, and reverie's own injections). Those run a real `SYSCALL` from reverie's **private trampoline page** (`cp/mmap.rs`), so hardware sets `rcx = trampoline_page+2` and `r11 = RFLAGS-at-trampoline`. `restore_context` (`task.rs:587`) restores `rip`/args/`orig_rax` but **deliberately leaves `rcx`/`r11`** (comment at `task.rs:610-612`: "not required to restore … the syscall is finished"). Net: after an injected syscall the guest's `rcx` holds a **reverie-internal address**, not the guest's own post-syscall RIP. That is exactly the kind of nondeterministic/internal state a misbehaving guest could read — the concern, made concrete.

(The `statfs` symptom that motivated #89 is *not* this leak — it's the detector reading a non-semantic **entry** register. #89 fixes that correctly.)

### 4. Layer: the determinism *policy* goes in detcore — but reverie must first expose the *mechanism*

Agreed that the determinism decision belongs in **detcore**, not reverie: "which registers must be forced, and to what value, so even a misbehaving guest is deterministic" is a determinism judgment, and reverie is the generic, determinism-agnostic instrumentation layer.

The catch is purely mechanical: **detcore cannot do this today** because the `Guest`/`Tool` interface gives a tool no way to write registers (only an `i64` return). So the split should be:

- **reverie (generic mechanism):** add a determinism-agnostic capability for a Tool to set the guest register file on syscall return (e.g. extend `Guest` with a `set_regs`/`set_return_regs`, or a post-syscall register hook applied just before resume — after any injection). This is a generic instrumentation feature, not determinism logic.
- **detcore (determinism policy):** in `handle_syscall_event`, after computing the return value, use that capability to force the non-semantic registers to canonical deterministic values. Because detcore writes them explicitly on every return, this *also* overrides the trampoline `rcx`/`r11` from §3 — the injection leak is closed as a side effect of the policy.

### 5. *What* detcore should force (the ABI correction to "zero unused registers")

- **`rcx`/`r11` (ABI-clobbered):** force to a deterministic value. Best is the faithful post-`SYSRET` values — `rcx` = the guest's post-syscall RIP, `r11` = the guest's RFLAGS (both are already in the saved `user_regs_struct` as `ip`/`eflags`, so it's exact and cheap). Zeroing them is an acceptable fallback since a correct program can't rely on clobbered registers anyway.
- **`rdi, rsi, rdx, r10, r8, r9`, callee-saved (ABI-preserved):** **do not zero these.** Linux preserves them, so zeroing would (a) diverge from real Linux and (b) break well-behaved programs that legally keep a live value across a syscall (e.g. a value the compiler parked in `rdx`). detcore's deterministic policy for these should be **preserve the guest's own value** — which is already deterministic and is what reverie does.

In other words: "zero the unused registers" is right in spirit but, on x86-64, the only registers that are actually unused/clobbered on return are `rcx`/`r11`; the rest must be preserved, not zeroed.

### 6. Performance

Negligible. The ptrace backend already does a `getregs`/`setregs` around each intercepted syscall; forcing `rcx`/`r11` is two field writes folded into that existing `setregs`, with no extra ptrace round-trips. (Worth keeping in mind for future fast-paths — seccomp-notify / in-guest / KVM — where the same contract must be met by construction, and where a per-syscall extra `setregs` would not be free. A shared cross-backend conformance test would be the right guardrail.)

### 7. Verdict on #89

**Keep it as-is.** An arity-aware desync detector is correct hygiene regardless of the return-path work: comparing non-semantic entry registers is inherently fragile, and #89's fail-closed default (arity 6 for unknown syscalls) is the right call. The register-determinism hardening is a **separate** piece of work (a generic reverie register-write capability + a detcore policy that canonicalizes `rcx`/`r11`), best tracked as its own task rather than folded into this PR.
