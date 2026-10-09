# Extensive context-macro experiment

This is a separate comparison: eros 0.8.1 defaults are enabled in **all six**
variants. `none` means no attached messages, not disabled context support.
The original five-variant experiment and its archived results are unchanged.

## Results: measurable iteration cost at high density

On October 9, 2026 UTC, with the same pinned Rust 1.98.1 toolchain and host,
**1,800 lazy attributes added about 0.10 seconds to a leaf edit and
0.12 seconds to a shared-body edit**. This was a 49–57% increase over the fast
no-context controls. No-op checks remained around 24 ms. Cold dev builds grew
about 15%, cold release builds about 32%, and stripped release size about 47%.
These are compile/build/size costs; **runtime overhead remains unmeasured**.

All figures below use within-run matched controls, not the original run.
Seconds are medians; percentages are calculated before rounding.

| Mode | Erased: none → macro | Change | Typed: none → macro | Change |
|---|---:|---:|---:|---:|
| Cold-target check, n=6 | 3.485 → 3.744 | +259 ms, +7.4% | 3.759 → 3.839 | +80 ms, +2.1% |
| Warm-dependency check, n=6 | 0.651 → 0.910 | +259 ms, +39.7% | 0.820 → 1.005 | +185 ms, +22.6% |
| Leaf-body edit, n=6 | 0.175 → 0.274 | +99 ms, +56.8% | 0.182 → 0.280 | +98 ms, +54.0% |
| Shared-body edit, n=6 | 0.238 → 0.354 | +116 ms, +48.7% | 0.243 → 0.364 | +121 ms, +49.7% |
| Cold dev build, n=3 | 4.629 → 5.319 | +690 ms, +14.9% | 4.769 → 5.480 | +712 ms, +14.9% |
| Cold release build, n=3 | 6.126 → 8.055 | +1,929 ms, +31.5% | 6.231 → 8.215 | +1,985 ms, +31.9% |

**The edit results are clearer than the cold/warm check rankings.** Erased
leaf-edit ranges were 0.171–0.183 s without context versus 0.266–0.282 s with
attributes; typed ranges were 0.177–0.192 versus 0.276–0.287 s. Shared-edit ranges
were also separated. Cold/warm checks had substantial outliers and overlapping
ranges: for example erased cold manual checks ranged 3.621–8.806 s and typed
no-context cold checks 3.554–6.931 s. Do not treat their small median differences
as reliable rankings. No samples were discarded.

The six-way [full table, including every range](results/context-2026-10-09/table.md),
[summary with MAD](results/context-2026-10-09/summary.json),
[raw samples](results/context-2026-10-09/raw.jsonl), and
[declared run order](results/context-2026-10-09/plan.json) preserve all 252
observations. One-minute load averages ranged 2.92–19.40; this was not a dedicated
or CPU-pinned host.

### Attribute syntax versus equivalent handwritten wrappers

| Mode | Erased: manual → macro | Macro/manual | Typed: manual → macro | Macro/manual |
|---|---:|---:|---:|---:|
| Warm-dependency check | 0.796 → 0.910 s | 1.143×, +114 ms | 0.904 → 1.005 s | 1.112×, +102 ms |
| Leaf-body edit | 0.203 → 0.274 s | 1.351×, +71 ms | 0.210 → 0.280 s | 1.336×, +70 ms |
| Shared-body edit | 0.278 → 0.354 s | 1.272×, +76 ms | 0.284 → 0.364 s | 1.282×, +80 ms |
| Cold dev build | 5.229 → 5.319 s | 1.017×, +89 ms | 5.401 → 5.480 s | 1.015×, +79 ms |
| Cold release build | 8.005 → 8.055 s | 1.006×, +50 ms | 8.120 → 8.215 s | 1.012×, +96 ms |

Most added cold-build cost also appeared with manual wrappers; the attribute
syntax added a smaller further difference. On leaf edits the manual machinery
added about 28 ms over none, while the attributes added a further 70–71 ms.
That is an observed end-to-end difference, not isolated proc-macro CPU time.
Incremental check behavior need not scale linearly with the number of sites.

### Binary sizes

Bytes; every size was identical across the three repetitions of its cell:

| Variant | Dev binary (debug info) | Release binary | Release stripped |
|---|---:|---:|---:|
| erased-none | 15,592,336 | 2,117,336 | 1,794,872 |
| erased-macro | 22,607,856 | 3,532,864 | 2,646,840 |
| erased-manual | 22,622,296 | 3,532,864 | 2,646,840 |
| typed-none | 18,072,328 | 2,117,408 | 1,794,872 |
| typed-macro | 25,796,584 | 3,630,520 | 2,642,744 |
| typed-manual | 25,811,024 | 3,630,520 | 2,642,744 |

Stripped growth is **832 KiB / 47.5% erased** and **828 KiB / 47.2% typed**.
Macro and manual have identical release sizes within each family, but not
identical binary hashes; do not infer byte-identical code. Manual dev binaries
are 14,440 bytes larger than macro in both families.

[Bounded ELF inspection](results/context-2026-10-09/elf-analysis.json) shows
`.text` growing from 1,545,813 bytes to 2,121,141 erased / 2,117,189 typed and
`.rodata` from 37,356 to 117,724 bytes. `.data.rel.ro` remains 29,064 bytes and
`.rela.dyn` increases only 192 bytes. Unlike the original feature-disabled size
surprise, this growth is principally executable/read-only content rather than a
large relocation-table increase. This is not a symbol-level attribution to
individual wrappers or message formatting.

## Design

| Family | No added context | Lazy attribute | Matching manual expansion |
|---|---|---|---|
| Erased | `erased-none` | `erased-macro` | `erased-manual` |
| Typed unions | `typed-none` | `typed-macro` | `typed-manual` |

## Questions and controls

Compare within each family:

- **macro / none:** total adoption cost, including wrappers, formatting types,
  diagnostics, and attribute processing.
- **manual / none:** equivalent context machinery without the 1,800 attributes.
- **macro / manual:** observed cost of expressing that machinery with an
  attribute. This is **not** an exact isolated measurement of macro CPU time:
  spans, hygiene, debug metadata, caching, and host noise can differ.

All variants already depend on `eros-macros`; none versus macro does not add a
new dependency compilation. The new controls are rerun together, not compared
to old baseline timings as though paired.

The unchanged 81,152 business-body lines form 256 workflows in eight domains.
Attributes cover all seven composing stages per workflow and eight domain
dispatchers: **1,800 sites**. Concrete leaf errors and leaf functions stay
unchanged. A failing path attaches exactly four messages, in order:
pair, quad, octet, domain. Successful full traversal visits all 1,800 sites.
This is deliberately dense usage, not a recommendation to decorate everything.

Nonblank/noncomment production Rust source lines are 90,568 / 92,368 / 101,368
for erased none/macro/manual and 90,968 / 92,768 / 101,768 for typed variants.
The business-body count stays fixed; manual source exposes functions that macro
source hides. Test source is counted separately in the
[source manifest](results/context-2026-10-09/source-manifest.json).

Messages have a unique static site plus runtime seed and failure selector:

```rust
#[eros::context("domain=0 workflow=0 stage=2 seed={} fail={}", input.seed, input.fail)]
```

Domain messages also format the selected workflow index. Tests inspect every
frame for every category/workflow/domain at three seeds, checking exact text,
count and attachment order; separate small counter fixtures verify laziness.
These are integration-test targets, not part of the measured application.
The scored app is identical within/across context conditions and still checks
root categories; its output also passes the independent Python oracle.

### Version-specific manual control

`context_workload.attach` follows the synchronous explicit-format expansion in
**eros-macros 0.8.1 `src/lib.rs`, `context_impl`, lines 309–370**.
The full source file's SHA-256 is
`43407cf09140fbeea1c9cf9afb06d3d8553f268a005600bacc8e4703e7b8aec9`.

The macro emits sibling functions, not a closure around the original body:

```rust
#[doc(hidden)]
#[track_caller]
fn __run_internal(index: usize, input: Request) -> eros::Result<Record> {
    // Original body, unchanged.
}
/// Original doc comment stays on the outer function.
pub fn run(index: usize, input: Request) -> eros::Result<Record> {
    use eros::Context as _;
    __run_internal(index, input).with_context(||
        eros::__private::format!("domain=0 workflow={} seed={} fail={}",
                                 index, input.seed, input.fail))
}
```

The inner function is private, with the original signature and body; the outer
keeps visibility and docs. `Request` is Copy, so captures need no ownership
workaround. The private formatting path deliberately matches the pinned
expansion; it is **not recommended application API usage**. A regression fixture
checks this structure against the inspected expansion. Macro and manual each
add 1,800 expanded functions, although only manual exposes them in source counts.

## Protocol and reproduction

Same pinned Nix toolchain, jobs=8, explicit profiles, sanitized environment,
isolated Cargo cwd/home, backtraces off, and offline/locked scored commands as
the primary experiment. Fresh independent `generated/context` and
`targets/context` directories prevent target/source reuse across experiments.
Locks are copies of the original default-eros locks; no dependencies changed.

```sh
nix develop path:. -c python3 -m unittest -v
nix develop path:. -c python3 bench.py prepare --experiment context
nix develop path:. -c python3 bench.py verify --experiment context \
  --output results/context-verification
nix develop path:. -c python3 bench.py measure --experiment context \
  --output results/context-2026-10-09
nix develop path:. -c python3 analyze_sizes.py --experiment context \
  --output results/context-2026-10-09
```

Use a new output directory when rerunning. Defaults are six repetitions of each
ordinary check mode, twelve no-ops, and three builds of each profile: 252 scored
commands. Six-way rotating blocks balance check/no-op positions; three build
blocks are not fully position-balanced. Modes retain fixed order. Cold-target,
warm-dependency, no-op, leaf edit and shared edit cache states follow the original
protocol, including rebuild audits and source restoration. Record all samples,
including outliers. Full heavy macro checks/builds and behavior tests are piloted
outside the scored run before committing measurement resources.

Raw wall/CPU/RSS samples, source hashes, runner/generator snapshots, compiler and
host metadata, rebuild audits, binary sizes, and oracle outputs are archived.
`uncontextualized_workflow_sha256` proves identical input workflow sources within
each family; model and application hashes also match across conditions.
No new expensive verification is included inside timed invocations.
The [verification archive](results/context-verification) records all-target
Clippy, dev/release application oracles, both-profile context/laziness tests,
and 15 Python harness regressions, including exact original source hashes.

## Limits

This measures **compile/iteration cost and binary size, not runtime throughput or
latency**. Lazy messages avoid formatting on success, but that does not establish
zero runtime cost. Error allocations/formatting and success wrapper overhead are
unmeasured. Automatic parameter formatting, eager contexts, async functions,
locations, deep Debug objects, and per-call context placement are outside scope.
The underlying workload still uses synthetic zero-sized errors and injected
failures. Host load, allocator/code layout, and build noise limit small rankings.
