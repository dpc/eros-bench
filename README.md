# eros-bench

A reproducible, artificial Rust error-handling benchmark: **81,152 generated
business-body lines per variant**, comparing `anyhow`, erased and typed `eros`,
and concrete `thiserror` enums. The main question is developer iteration cost,
especially `cargo check`, not a universal ranking of error libraries.

The checked-in experiment ran on **October 9, 2026 UTC** (October 8 locally),
on an AMD Ryzen 9 7950X3D, with **Rust 1.98.1**, Cargo 1.98.0, and **8 Cargo jobs**.
Sources, dependency locks, toolchain environment, raw observations, rebuild
audits, and behavioral validation are preserved or reproducible.

## Results

**The largest check difference appeared in fresh-target checks, not ordinary edits.**
Erased eros took 3.91× anyhow's cold-target check time, but only 1.03× its
warm-dependency median, with overlapping ranges. All five leaf-edit medians were
between 0.217 and 0.252 seconds. Typed eros added a modest warm-project check
cost in this workload; turning its diagnostic features off did not give a clear
check-time win.

Seconds, **median [minimum–maximum]**:

| Mode | anyhow | eros-erased | eros-typed | eros-typed-lean | thiserror |
|---|---|---|---|---|---|
| Cold-target check, n=5 | 0.911 [0.905–1.099] | 3.562 [3.514–4.041] | 3.823 [3.601–4.266] | 3.855 [3.598–5.197] | 2.941 [2.788–3.499] |
| Warm-dependency check, n=5 | 0.613 [0.592–0.657] | 0.630 [0.594–0.685] | 0.702 [0.682–0.746] | 0.686 [0.674–0.772] | 0.675 [0.647–0.681] |
| No-op check, n=10 | 0.022 [0.020–0.024] | 0.024 [0.024–0.029] | 0.025 [0.024–0.030] | 0.024 [0.023–0.035] | 0.024 [0.023–0.026] |
| Leaf-body edit check, n=5 | 0.217 [0.192–0.219] | 0.219 [0.199–0.343] | 0.220 [0.205–0.452] | 0.219 [0.202–0.490] | 0.252 [0.223–0.258] |
| Shared-body edit check, n=5 | 0.245 [0.228–0.252] | 0.247 [0.236–0.308] | 0.246 [0.237–0.255] | 0.258 [0.247–0.263] | 0.276 [0.264–0.280] |
| Cold-target dev build, n=3 | 2.153 [2.146–2.378] | 6.937 [4.649–7.288] | 5.175 [4.827–13.747] | 5.273 [4.750–5.707] | 3.931 [3.886–4.210] |
| Cold-target release build, n=3 | 3.567 [3.411–3.583] | 6.181 [6.170–10.547] | 6.601 [6.273–7.103] | 6.552 [6.231–10.924] | 5.323 [4.991–5.904] |

See [the automatically generated table](results/2026-10-09/table.md) and
[machine-readable summary](results/2026-10-09/summary.json). All times include
Cargo startup; cleaning, source edits, downloading, generation, and validation
are outside the timers. The summary reports medians and observed ranges, not
confidence intervals.

### Interpreting the check results

* **Erased eros versus anyhow:** cold medians differ by 2.651 seconds
  (3.562 / 0.911 = **3.91×**), versus only 17 milliseconds with warm dependencies
  (0.630 / 0.613 = **1.03×**). The latter ranges overlap substantially. Erased
  eros's proc-macro dependency graph is part of the cold workload, whereas anyhow
  has no such graph in these locks. This supports separating dependency setup
  from project checking, not attributing the entire cold difference to `?`.
* **Typed versus erased eros:** warm medians are 0.702 / 0.630 = **1.11×**,
  or 72 milliseconds more for typed errors. The observed ranges barely overlap;
  this is a modest local observation, not a precise general trait-solving tax.
  Their leaf/shared edit medians are almost identical.
* **Default versus lean typed eros:** warm medians are 0.702 / 0.686 =
  **1.02×**, with overlapping ranges. Cold and edit measurements do not establish
  a consistent feature-removal advantage.
* **Lean typed eros versus thiserror:** warm medians are 0.686 / 0.675 =
  **1.02×**, again inconclusive. Thiserror's edit medians are higher by roughly
  18–33 milliseconds, but this is small in absolute terms and some ranges
  overlap. No-op results mostly show 20–35 milliseconds of Cargo overhead.

In short, the choice mattered much more to a fresh target directory than to
these small incremental body edits. This does not predict edits to public
signatures, large generic definitions, or macro-heavy application code.
The warm-dependency mode removes local package artifacts/incremental state;
the edit modes retain them. The rebuild audit proves that Cargo invokes rustc
for a package, **not** that rustc repeats every type-checking query. This
distinction matters when interpreting the relatively cheap shared-helper edit.

### Builds and binary sizes

Cold build medians favor anyhow here, but **build measurements are noisy**:
typed eros's dev samples span 4.827–13.747 seconds, and two eros release variants
have samples above ten seconds. All outliers are retained. Three samples do not
support fine-grained ranking among the eros modes. The recorded pre-trial
one-minute load averages span 10.2–37.2, including preceding benchmark activity
and any other host activity; those averages do not isolate the cause of a slow
sample.

File sizes were identical across all three repetitions of each variant/profile.
MiB means 1,048,576 bytes. The stripped column uses a separate release copy.

| Variant | Dev file, MiB | Release file, MiB | Stripped release, MiB | Stripped release, bytes |
|---|---:|---:|---:|---:|
| anyhow | 17.306 | 1.840 | 1.505 | 1,577,784 |
| eros-erased | 14.870 | 2.019 | 1.712 | 1,794,872 |
| eros-typed | 17.235 | 2.019 | 1.712 | 1,794,872 |
| eros-typed-lean | 16.139 | 2.630 | 2.356 | 2,470,712 |
| thiserror | 14.551 | 1.498 | 1.337 | 1,401,656 |

Default erased and typed eros have the same stripped **size**, 13.8% larger than
anyhow. Thiserror is the smallest here, with the important advantage/limitation
that all underlying error payloads are zero-sized.

**Unexpectedly, disabling eros's default features made the stripped release
file 37.7% larger, not smaller.** A bounded ELF inspection explains where the
extra bytes reside, without establishing a compiler-level cause:

| ELF section, bytes | Typed default | Typed lean |
|---|---:|---:|
| Executable `.text` | 1,545,813 | 1,338,533 |
| `.rela.dyn` relocations | 55,104 | 584,592 |
| `.data.rel.ro` | 29,064 | 320,648 |
| `.eh_frame` | 66,992 | 123,064 |
| `.eh_frame_hdr` | 11,740 | 36,100 |

Lean actually has **less executable `.text`**, but much larger relocation and
read-only-after-relocation data sections, plus more unwind metadata. There are
24,291 `R_X86_64_RELATIVE` relocations versus 2,227 with defaults. GNU `size`'s
aggregate “text” number includes more than executable `.text`; treating it as
instruction bytes would give the wrong explanation.

Symbol inspection also finds 1,024 `SendSyncError::as_any` adapter definitions
in lean versus 64 with defaults. For one concrete error type,
`domain5::errors::Error0`, that is 16 definitions versus one. Release uses 16
codegen units. Duplicated adapter/vtable-related structures across codegen units
are therefore a plausible explanation for some static-data/relocation growth,
but the relationship is not a controlled causal finding. We did not vary
codegen units, LTO, inlining, or the linker, and cannot account for every extra
byte. This result does not imply lean errors inherently require more memory or
instructions.

[Raw sizes](results/2026-10-09/sizes.jsonl) and
[ELF analysis](results/2026-10-09/elf-analysis.json) preserve commands, section
sizes, relocation/adapter counts, and binary hashes.

## Reproduce

On x86-64 Linux with Nix:

```sh
# Fetch the pinned compiler/tools, generate the sources, and fetch locked crates.
nix develop path:. -c python3 bench.py prepare

# Fast harness regression tests.
nix develop path:. -c python3 -m unittest -v

# Full-size regeneration comparison, Clippy, debug/release builds, and oracle tests.
# Output directories must not already exist.
nix develop path:. -c python3 bench.py verify --output scratch/my-verification

# Serial scored matrix: 5 samples/check mode, 10 no-ops, 3 samples/build mode.
nix develop path:. -c python3 bench.py measure --output results/my-machine

# Optional section/relocation analysis of the retained final release artifacts.
nix develop path:. -c python3 analyze_sizes.py --output results/my-machine

# Recompute tables from a complete raw dataset without rebuilding.
nix develop path:. -c python3 bench.py summarize --output results/my-machine
```

`prepare` copies the committed per-variant locks into generated workspaces and
uses `cargo fetch --locked`. It never updates existing dependency locks.
`generate` regenerates sources offline without fetching anything. Generated
workspaces are under `generated/VARIANT/`; each is an ordinary, standalone Cargo
workspace. They are deliberately not committed: the small generator and source
hash manifests reproduce the large inputs.

Without Nix, use Python 3.11+, a Rust compiler supporting edition 2024, Cargo,
Clippy, GNU `time`, GNU binutils (`size`, `strip`, `ld`), a C linker, and `lscpu`.
Run the same Python commands directly. This is supported as a **new experiment**,
not an exact reproduction of the pinned compiler. The runner currently targets
Linux (`/proc`, `fcntl`, GNU tools); it is not a portable benchmark service.

Keep other compilation and heavy workloads idle during measurements. The local
harness lock prevents two harness commands in this checkout from overlapping,
but does not control other processes. Allow several GiB for target directories.
Delete `targets/` to reclaim build products; registry downloads remain cached.
Changing repetition counts is supported with `--check-trials`, `--noop-trials`,
and `--build-trials`; fewer samples weaken the comparison.

## What is being compared?

| Variant | API representation | Dependency and features |
|---|---|---|
| `anyhow` | Erased `anyhow::Result<T>` | `anyhow = "=1.0.104"`, defaults |
| `eros-erased` | Erased `eros::Result<T>` | `eros = "=0.8.1"`, defaults |
| `eros-typed` | Precise `eros::Result<T, (E1, E2, …)>` | `eros = "=0.8.1"`, defaults |
| `eros-typed-lean` | Exactly the same Rust source as `eros-typed` | `eros = "=0.8.1"`, `default-features = false` |
| `thiserror` | Concrete, flat, transparent enums | `thiserror = "=2.0.21"`, defaults |

These are **not five identical error representations**. `thiserror` derives
standard error implementations and conversions; it is not an erased error
container. Eros supports both typed and erased errors. Its defaults include
context and backtrace support; the lean variant removes those features.
No variant adds user-written context frames. `RUST_BACKTRACE=0` and
`RUST_LIB_BACKTRACE=0` disable runtime backtrace capture, which is not the same
as removing compiled support. Eros's macros dependency remains in the lean
dependency graph.

Useful comparisons are:

* **Erased eros / anyhow:** two erased propagation implementations.
* **Typed eros / erased eros:** precision cost within the same library.
* **Typed default / typed lean eros:** diagnostic-feature cost with identical source.
* **Typed lean eros / thiserror:** typed propagation, but different representations.

The lockfiles also preserve transitive dependencies. In this run eros uses
`syn` 2.0.119 while thiserror uses `syn` 3.0.6. Cold-target comparisons therefore
include different dependency and proc-macro compilation costs; warm-dependency
measurements are important.

Primary API references:
[eros 0.8.1](https://docs.rs/eros/0.8.1/eros/),
[anyhow 1.0.104](https://docs.rs/anyhow/1.0.104/anyhow/),
[thiserror 2.0.21](https://docs.rs/thiserror/2.0.21/thiserror/),
[Cargo check](https://doc.rust-lang.org/cargo/commands/cargo-check.html), and
[Cargo profiles](https://doc.rust-lang.org/cargo/reference/profiles.html).
These exact registry releases were resolved for this experiment; the benchmark
does not track moving upstream branches.

## Generated application

Each variant has the same ten-crate dependency topology:

* One shared `model` library.
* Eight independent domain libraries depending on `model` and their error library.
* One application depending on all domains and the shared model.

Each domain contains 32 workflow modules, for **256 workflows** overall. Every
workflow has eight primitive field transforms, four pair stages, two four-field
stages, and an eight-field stage. The application dispatches every workflow;
all successful outputs contribute to a printed checksum.

Selected call path, following one branch:

```text
app::main(seed, failure_selector):
    domain0::run(workflow_index, request):
        workflow_000::stage_6(request):          // eight-error set
            stage_4(request):                  // four-error set
                stage_0(request):              // two-error set
                    leaf_0(request)            // one concrete error
                    leaf_1(request)
                stage_1(request)
            stage_5(request)
    // Repeat for all workflows in all eight domains.
```

The transforms format runtime data as hex, scan bytes, classify digits, calculate
bounds/budgets, and combine record fingerprints and item counts. These are
synthetic in-memory request/accounting operations, **not a real parser or real
I/O**. Errors are deliberately injected by category. Runtime CLI seeds prevent
the application from becoming a constant expression; no blanket `inline(never)`
or `black_box` directives distort the optimizer.

There are **64 manually implemented leaf error types**, eight per domain, reused
across its workflows. Rotating field order produces 17 distinct sets per domain:
eight two-error sets, eight four-error sets, and one eight-error set. Typed
variants therefore have **136 shared aliases/enums**, not one bespoke error
type per function. Eros uses `.union()?` to enter a set and `.widen()?` to expand
it. Thiserror uses generated, shared, flat `From<SmallerSet>` conversions;
each leaf category remains observable without nested wrapper categories.
Common leaf errors do not derive thiserror, so they do not introduce its
dependency into the other variants.

| Source count | anyhow / erased eros | typed eros / typed lean | thiserror |
|---|---:|---:|---:|
| Business function-body lines | 81,152 | 81,152 | 81,152 |
| All Rust nonblank/noncomment lines | 90,568 | 90,968 | 93,544 |
| Workflow functions | 3,840 | 3,840 | 3,840 |
| `?` propagation sites | 3,584 | 3,584 | 3,584 |

The strict business count includes nonblank, noncomment function-body lines
(including braces), not imports, signatures, error declarations/conversions,
the model, application dispatcher, tests, dependencies, or blank padding. Bodies
have identical operations, differing only in error-adapter syntax. The generator
rejects a full-size variant with 60,000 or fewer such lines. Extra thiserror
boilerplate is counted separately rather than padded away in other variants.

**All leaf errors are zero-sized structs.** The thiserror enums can therefore
be compact discriminants, while erased/union types have different storage and
diagnostic machinery. This is especially important when interpreting binary
sizes: payload-rich application errors could behave differently.

## Measurement protocol

Timed check/build invocations use `--workspace --locked --offline --jobs 8` and an explicit
manifest/target directory. Registry crates are fetched first. Variants never
share target artifacts, and each measurement mode has its own target directory.

The runner clears inherited `RUST*` and `CARGO_*` settings, including compiler
wrappers and injected flags. It uses a separate Cargo home with only a symlink
to the downloaded registry cache. Commands run in a fresh directory under
`/tmp`, not the caller's directory; ancestor and isolated-home Cargo configs are
rejected. Nix's compiler/linker environment is retained and recorded. `prepare`
alone permits the caller's registry authentication environment for downloading.

Profiles are explicit:

* Dev/check: opt-level 0, debug info 2, incremental on, unwind.
* Release: opt-level 3, debug info 0, incremental off, LTO false,
  16 codegen units, unwind, no stripping during the build.

| Mode | State immediately before the timed command |
|---|---|
| `check-cold-target` | Entire target directory removed |
| `check-warm-deps` | Completed check, then `cargo clean -p` for all ten local packages; external dependency artifacts retained |
| `check-noop` | Completed check, no source or artifact changes |
| `check-edit-leaf` | Incremental baseline; toggle one literal inside domain0/workflow0/leaf0 |
| `check-edit-shared` | Incremental baseline; toggle one literal inside the shared helper body |
| `build-dev-cold-target` | Entire dedicated target directory removed; `cargo build` |
| `build-release-cold-target` | Entire dedicated target directory removed; `cargo build --release` |

“Cold” means **cold target artifacts**, not a cold machine, cold filesystem, or
empty registry. OS page caches are not dropped. Edits change actual bytes,
preserve source length and signatures, and alternate states every trial. An
untimed changed-state check primes each edit mode. Original source bytes are
restored on ordinary exceptions too. A forced kill or power loss cannot run
cleanup; use `generate` before restarting after such an interruption. The shared
edit is a **body change, not an API change**.

Untimed verbose audits assert the exact set of rebuilt crates:

* Warm dependencies: all ten local packages, no external packages.
* Leaf edit: domain0 and the application.
* Shared edit: model, all eight domains, and the application.

Trials run serially in blocks. Variant order rotates one position each block;
five check blocks balance all five positions. Three build blocks are rotated but
not fully position-balanced. Modes themselves run in a fixed order, so compare
variants within a mode rather than treating mode-to-mode ratios as controlled
causal experiments. No-op timings mainly measure Cargo freshness/startup.

The timer uses Python's monotonic clock around GNU `time` plus Cargo. GNU time
also records user/system CPU seconds and max RSS. **Its max RSS is not aggregate
peak memory of simultaneous compiler processes**; this project makes no
whole-build peak-memory claim. Binary-size records include raw dev/release file
bytes, `size` text/data/bss, and a separate release copy processed with
`strip --strip-all`. The originals are not stripped.

## Validation and audit trail

The independent Python oracle reimplements the checksum arithmetic. Every
debug/release application is tested with four successful seeds, including
`u64::MAX`, and each of eight failure selectors. Each invocation dispatches
all 256 workflows; failures are caught separately so one failure cannot hide
later workflows. Per-domain category counters verify all 64 concrete error
categories. Error display categories are compared, not library-specific Debug
output or diagnostic chains.

`verify` also regenerates every file into a temporary directory and compares
SHA-256 manifests, runs Clippy with warnings denied, and builds both profiles.
Scored build artifacts run through the same oracle outside the timer.
Python unit tests cover generation shape, minimum size, precise sets, mutation
round trips, subprocess working-directory isolation, rebuild auditing, and
rejection of failed/incomplete summaries.

The results directory contains:

* `plan.json`: expected ordered measurement cells, written before timing.
* `raw.jsonl`: every timed command, order, UTC timestamp, elapsed/resources,
  pre-run load average, return code, and log paths.
* `environment.json`: compiler/tool versions and paths, OS/kernel/CPU/memory,
  controlled environment, flake and generator hashes.
* `source-manifest.json`: exact generated-source and lock hashes/counts.
* `bench.py.snapshot`: exact harness used for the run.
* `logs/`, `setup.log`, `audit.json`: scored logs, preparation, rebuild assertions,
  and per-build behavioral output.
* `sizes.jsonl`, `elf-analysis.json`, `summary.json`, `table.md`: raw sizes,
  bounded ELF inspection, and derived summaries.
* `complete.json`: successful completion marker.

A failed command aborts the run. Summary generation requires the exact complete
planned cell sequence; it cannot silently average failures, missing trials, or
unequal sample counts. Output directories are never reused automatically.
[Full verification evidence](results/verification/validation.json) is retained
separately from scored observations.

## Limits

This is one deterministic workload, one machine, and one compiler configuration.
Large LoC alone does not make it a representative Rust application: it has
repeated, straightforward functions, few dependencies, no async, no complex
business generics, no procedural macros in its business logic, no I/O, no
payload-bearing errors, and no added context. Shared aliases/conversions avoid
an intentionally combinatorial type-set stress test.

The machine was not reserved or CPU-pinned. Fixed eight-job parallelism uses
only part of its 32 logical CPUs; power management, heterogeneous cache layout,
filesystem cache state, and background load can influence small differences.
Observed ranges and small sample counts do not justify significance claims.
The runtime executions validate semantics; **they are not runtime latency or
allocation benchmarks**.

Cargo check omits final target code generation/linking, so release-size
differences do not explain check costs by themselves. Proc-macro dependencies,
trait solving, monomorphization, enum conversions, and diagnostic machinery are
possible mechanisms, not isolated causal findings of this experiment. Useful
follow-ups include payload-rich errors, matched added-context workloads, more
irregular generic/async code, larger error-set diversity, more compilers, and
other hardware.

## AI usage disclosure

[I use LLMs when working on my projects.](https://dpc.pw/posts/he-went-to-the-slop-side/)
