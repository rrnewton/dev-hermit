# DetTrace TensorFlow deterministic demo — recovery & assessment

Task: `find-dettrace-tensorflow-deterministic-demo` (research). Owner recollection:
"dettrace had a tensorflow demo that ran deterministically (whereas native was
nondeterministic)." Flagged as a recollection → establish existence FIRST.

**Verdict: CONFIRMED. The demo is real and the recollection is accurate.** It is
a published evaluation result in the DetTrace ASPLOS 2020 paper (§7.6), backed by
enabling commits in the dettrace source. One important nuance below: the demo
wrapped the *stock* TensorFlow model tutorials — there is no bespoke "TF demo
script" checked into the dettrace repo to dig up; the recoverable artifacts are
(a) dettrace itself and (b) the external TF tutorial programs it ran.

## 1. Does it exist — evidence (links/SHAs)

- **Paper**: "Reproducible Containers", Navarro Leija, Shiptoski, Scott, Wang,
  Renner, Dickerson, Devietti — ASPLOS 2020. §7.6 is titled **"TensorFlow"** and
  §6.1 motivates ML reproducibility. (PDF: gatowololo.github.io /
  krs85.github.io `dettrace.pdf`.)
- **Source repo**: `github.com/dettrace/dettrace` (mirror of `upenn-acg/detTrace`).
  Local checkout: `ignored/dettrace`.
  - master HEAD `657ff07bbfad5bac5ce680c412d66acb1008ae3b`
  - paper-submission tag `ASPLOS2020Submission` = `4038b0edd5fb5210d6a20bffdfd89f79de58caa3`
- **Enabling commits (the "we actually ran TF" trail):**
  - `546ae0848a1e9ed6fc28cadd2b298aec7fb040b3` — Devietti, 2019-08-15,
    *"model accumulation of nanosec/microsec realistically in gettimeofday and
    clock_gettime, needed for TensorFlow"* (the smoking gun — a fix made
    specifically to run TF).
  - `abacb3e` — `--clock-step` CLI (tunable logical-time advance).
  - `53ad57b` / `646b831` — deterministic getrandom / /dev/urandom / /dev/random
    seeding (cmdline PRNG seed).
  - `futex-epoll` branch (merged) — futex/epoll + threading support, required for
    TF's thread pool.

The literal string "Tensor" appears in **no tracked file content** across all
history (only in the one commit *message*). So the demo was never a committed
example/script; it was an experiment wrapping external TF programs.

## 2. What it actually demonstrated (§7.6, verbatim points)

- Workload: **TensorFlow v1.14**, the **alexnet** and **cifar10** programs from
  the `tensorflow/models` image tutorials (paper ref [36] =
  `github.com/tensorflow/models/tree/master/tutorials/image`), doing **model
  creation, training, and inference**. **CPU-only** (no GPU).
- Reproducibility metric: **the value of the loss function at each training
  step**.
- Three configs: (1) native parallel, (2) native but TF forced to a **single
  thread**, (3) under DetTrace.
- Result (quote): *"these values are irreproducible when running natively, **even
  with serialized TensorFlow** … due to, e.g., randomization of the training set.
  DetTrace renders these workloads reproducible **without any code changes**."*
- Performance: DetTrace serializes threads, so vs **native parallel (16 cores)**
  it is **17.49× slower on alexnet, 11.94× on cifar10**. But vs **serialized
  native** (apples-to-apples, single-thread) only **1.51× (alexnet)** and
  **1.08× (cifar10)** — "a small performance price for non-threaded
  compute-bound workloads."
- Hardware: 2× Intel Xeon E5-2618Lv3 (Haswell), 16 cores total, 128GB, Ubuntu
  18.10.

So it was **not** full distributed/GPU training — it was CPU training+inference
of two small classic CNN tutorials, with per-step loss as the determinism
witness. That is exactly the compelling "native drifts, determinized runtime
reproduces" shape.

## 3. What made NATIVE nondeterministic, and what DetTrace controlled

The paper's key subtlety: **single-threading TF was NOT enough** — native stayed
irreproducible even serialized. So the sources were plural:

| Source of native nondeterminism | Present? | DetTrace control |
|---|---|---|
| **RNG / training-set shuffle** (dataset sampling, weight init) | **Yes — the dominant one; survives single-threading** | Deterministic getrandom//dev/*random seeding (`53ad57b`, `646b831`) |
| **Thread scheduling / float reduction order** (OpenMP thread pool → nondeterministic parallel sum order) | Yes (in parallel config) | Serialized deterministic scheduler (single logical CPU; `futex-epoll`) |
| **Time** (clock_gettime/gettimeofday feeding into ops/seeds/logging) | Yes | Virtual logical clock (`546ae08` made it sec+nsec realistic; `--clock-step`) |
| Filesystem starting state / file mtimes | Yes (build/artifact repro) | chroot + empty /tmp + fixed mtimes + CPUID interception |

The evidence that DetTrace's **RNG** determinization (not just serialization) was
essential: native single-thread TF was still irreproducible "due to
randomization of the training set," and DetTrace fixed it with no code changes —
which only follows if it seeded the RNG deterministically. This is the honest,
non-glossed answer to "which of those did dettrace actually control": **all four
— and RNG was the one that serialization alone could not fix.**

## 4. Does the source still build?

- **dettrace itself**: builds via its pinned Docker toolchain — Ubuntu 18.04,
  **clang-6.0**, `libseccomp-dev`, `libarchive-dev` (see `Dockerfile`;
  `docker build -t dettrace .`). A native build on a modern host is *not*
  guaranteed (2018-era clang-6 + ptrace/seccomp assumptions); Docker is the
  supported path. No prebuilt binary is in the checkout. Not verified-built this
  session (would need the old-toolchain container).
- **The TF demo workload is the hard part to revive, not dettrace.** It needs
  **TF v1.14** (2019; EOL, requires Python 3.6/3.7 + old numpy/protobuf) and the
  **`tensorflow/models` image tutorials**, whose `tutorials/image` path has since
  been **removed from modern `tensorflow/models`** (recoverable only from that
  repo's git history). So "digging up the source" resolves to: dettrace @
  `ASPLOS2020Submission`/master + a pinned old TF 1.14 + the alexnet/cifar10
  tutorial scripts from `tensorflow/models` history. There is no single bundled
  artifact.

## 5. Is a Hermit equivalent realistic? (hard parts NAMED)

**Realistic as a demo, with caveats — and arguably easier to make *compelling*
than to make *fast*.** The demo's value is the shape (native loss drifts;
determinized loss is bitwise-stable), which Hermit's model directly supports
(deterministic scheduler + RNG + virtual time — the same four levers dettrace
used). Concretely:

- **Easy / already covered**: RNG determinization, virtual time, deterministic
  serialized scheduling, filesystem starting state — Hermit does all of these
  today. The per-step-loss witness is trivial to capture.
- **Hard part 1 — TF's threading model.** TF v1.14 uses an OpenMP/Eigen thread
  pool; Hermit (like dettrace) serializes it. Expect a **~12–17× slowdown vs
  native-parallel** but only **~1.0–1.5× vs single-thread native** (dettrace's
  measured numbers). For a small tutorial that's fine; for anything real it's
  slow. Name it, don't hide it.
- **Hard part 2 — getting TF to run at all under the sandbox.** This is a
  syscall-coverage problem: TF pulls in a large native stack (Eigen, possibly
  MKL/oneDNN, protobuf, gRPC even when local). Whatever syscalls that stack
  issues must be determinized/handled. Modern TF 2.x is heavier than the 1.14
  the paper used; **pinning old TF 1.14 is the pragmatic choice** but fights
  Python/pip EOL. This is where the real effort goes.
- **Hard part 3 — BLAS reduction determinism.** Even serialized, some BLAS
  kernels choose code paths by CPU feature / buffer alignment. dettrace masked
  CPUID; Hermit must ensure the same, or the "no code changes" claim weakens.
- **Out of scope (keep it that way)**: GPU. The dettrace demo was CPU-only;
  a Hermit demo should be too. GPU determinism is a separate, much larger problem
  (nondeterministic cuDNN kernels) and is not needed to show the value prop.

**Recommendation**: a Hermit "native-nondeterministic vs Hermit-reproducible
training loss" demo on a *small* CPU model is a strong, achievable addition to
the demo set / compat corpus. Scope it to a tiny model (even a from-scratch
NumPy/tiny-TF MLP on a fixed dataset) first to prove the shape cheaply, then
attempt TF 1.14 alexnet/cifar10 to match the dettrace result. Budget the effort
against Hard Part 2 (TF-under-sandbox syscall coverage), which dominates. Do not
promise full/modern/GPU TF.

## Reproduction of this investigation
- `ignored/dettrace` @ master `657ff07`; `git show 546ae08`; `git log --all -i
  --grep=tensor`.
- Paper: `with-proxy curl -sL <dettrace.pdf>`; §7.6 "TensorFlow", §6.1 "Machine
  Learning". (Text extracted via zlib stream decode; poppler/pdftotext absent on
  host.)
