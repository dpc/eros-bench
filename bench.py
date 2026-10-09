#!/usr/bin/env python3
"""Generate, validate, measure, and summarize comparable synthetic Rust projects."""

import argparse
from dataclasses import dataclass
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import statistics
import subprocess
import tempfile
import time
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
VARIANTS = ("anyhow", "eros-erased", "eros-typed", "eros-typed-lean", "thiserror")
DOMAINS = 8
MODULES = 32
JOBS = 8
COMMAND_DIRECTORY = None


@dataclass(frozen=True)
class Experiment:
    """Keep independent experiments out of each other's sources and targets."""

    name: str
    variants: tuple

    @property
    def generated(self):
        return ROOT / "generated" / ("context" if self.name == "context" else "")

    @property
    def targets(self):
        return ROOT / "targets" / ("context" if self.name == "context" else "")


PRIMARY = Experiment("primary", VARIANTS)
CONTEXT = Experiment(
    "context",
    tuple(
        f"{family}-{kind}"
        for family in ("erased", "typed")
        for kind in ("none", "macro", "manual")
    ),
)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def lines_count(text):
    return sum(
        bool(line.strip()) and not line.lstrip().startswith("//")
        for line in text.splitlines()
    )


def set_name(ids):
    return "Set" + "".join(map(str, sorted(ids)))


def groups(rotation):
    order = [(rotation + i) % 8 for i in range(8)]
    return [order[i : i + width] for width in (2, 4, 8) for i in range(0, 8, width)]


def result_type(variant, ids):
    if variant == "anyhow":
        return "anyhow::Result<Record>"
    if variant == "eros-erased":
        return "eros::Result<Record>"
    if variant.startswith("eros-typed"):
        return f"eros::Result<Record, {set_name(ids)}>"
    return f"Result<Record, {set_name(ids)}>"


def errors_source(variant, domain):
    text = "use std::fmt;\n"
    for e in range(8):
        text += f"""
/// Domain {domain} validation category {e}.
#[derive(Debug)]
pub struct Error{e};
impl fmt::Display for Error{e} {{
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {{
        f.write_str("D{domain}E{e}")
    }}
}}
impl std::error::Error for Error{e} {{}}
"""
    sets = {tuple(sorted(g)) for r in range(8) for g in groups(r)}
    if variant.startswith("eros-typed") or variant == "thiserror":
        for ids in sorted(sets):
            name = set_name(ids)
            text += "/// Shared precise error set at a workflow boundary.\n"
            if variant.startswith("eros"):
                text += f"pub type {name} = ({', '.join(f'Error{i}' for i in ids)},);\n"
            else:
                text += f"#[derive(Debug, thiserror::Error)]\npub enum {name} {{\n"
                for i in ids:
                    text += f"    /// Validation category {i}.\n    #[error(transparent)]\n    E{i}(#[from] Error{i}),\n"
                text += "}\n"
    if variant == "thiserror":
        conversions = set()
        for r in range(8):
            gs = groups(r)
            for i in range(4):
                conversions.add((tuple(sorted(gs[i])), tuple(sorted(gs[4 + i // 2]))))
            for i in (4, 5):
                conversions.add((tuple(sorted(gs[i])), tuple(range(8))))
        for src, dst in sorted(conversions):
            text += f"impl From<{set_name(src)}> for {set_name(dst)} {{\n"
            text += f"    fn from(error: {set_name(src)}) -> Self {{\n        match error {{\n"
            for i in src:
                text += f"            {set_name(src)}::E{i}(e) => Self::E{i}(e),\n"
            text += "        }\n    }\n}\n"
    return text


def workflow_source(variant, domain, module):
    text = "use model::{Record, Request, adjust};\nuse crate::errors::*;\n"
    if variant.startswith("eros-typed"):
        text += "use eros::{IntoUnion, ReshapeUnion};\n"
    business = 0
    for e in range(8):
        body = f"""    if input.fail == {e} {{
        return Err(Error{e});
    }}
    let salt = {1 + domain * 257 + module * 17 + e}u64;
    let key = input.seed.wrapping_add(salt);
    let encoded = format!("{{key:016x}}");
    let mut checksum = salt;
    let mut digits = 0u64;
    let mut letters = 0u64;
    for byte in encoded.bytes() {{
        checksum = checksum.rotate_left(5) ^ u64::from(byte);
        if byte.is_ascii_digit() {{
            digits += 1;
        }} else {{
            letters += 1;
        }}
    }}
    let capacity = (key & 255).wrapping_add(1);
    let requested = checksum & 511;
    let accepted = requested.min(capacity);
    let remaining = capacity - accepted;
    let weighted = accepted.wrapping_mul(digits + 1);
    let normalized = weighted.wrapping_add(letters);
    let balance = adjust(normalized ^ remaining);
    let fingerprint = checksum.wrapping_add(balance);
    let record = Record {{
        value: fingerprint ^ key,
        count: accepted + 1,
    }};
    Ok(record)
"""
        business += lines_count(body)
        text += f"/// Parse and validate one request field.\nfn leaf_{e}(input: Request) -> Result<Record, Error{e}> {{\n{body}}}\n"
    gs = groups(module % 8)
    for idx, ids in enumerate(gs):
        if idx < 4:
            children = [f"leaf_{i}" for i in ids]
            suffix = ".union()?" if variant.startswith("eros-typed") else "?"
        else:
            children = [
                f"stage_{i}"
                for i in ([2 * (idx - 4), 2 * (idx - 4) + 1] if idx < 6 else [4, 5])
            ]
            suffix = ".widen()?" if variant.startswith("eros-typed") else "?"
        body = f"""    let left = {children[0]}(input){suffix};
    let right = {children[1]}(input){suffix};
    let count = left.count.wrapping_add(right.count);
    let mixed = left.value.rotate_left({idx + 3}) ^ right.value;
    let budget = count.wrapping_mul({module + idx + 1});
    let value = mixed.wrapping_add(budget);
    let value = value ^ input.seed.rotate_right({idx + 1});
    Ok(Record {{
        value,
        count,
    }})
"""
        business += lines_count(body)
        visibility = "pub " if idx == 6 else ""
        text += f"/// Compose validated records at layer {len(ids)}.\n{visibility}fn stage_{idx}(input: Request) -> {result_type(variant, ids)} {{\n{body}}}\n"
    return text, business


MODEL = """/// Runtime request shared by every workflow.
#[derive(Clone, Copy)]
pub struct Request {
    /// Caller-controlled seed prevents constant folding.
    pub seed: u64,
    /// Error category to inject, or 255 for success.
    pub fail: u8,
}
/// Normalized business output.
pub struct Record {
    /// Deterministic content fingerprint.
    pub value: u64,
    /// Accepted item count.
    pub count: u64,
}
/// Apply the shared accounting rule.
pub fn adjust(value: u64) -> u64 {
    value.wrapping_mul(3) // BENCH_EDIT_SHARED
}
"""


def generate(destination, modules=MODULES, enforce_size=True, experiment=PRIMARY):
    """Write deterministic sources; dependency resolution is a separate operation."""
    if experiment == CONTEXT:
        import context_workload

        return context_workload.generate(destination, modules, enforce_size)
    if modules < 1:
        raise ValueError("modules must be positive")
    metadata = {}
    for variant in VARIANTS:
        root = destination / variant
        if root.exists():
            shutil.rmtree(root)
        members = ["model", "app"] + [f"domain{d}" for d in range(DOMAINS)]
        manifest = (
            '[workspace]\nresolver = "2"\nmembers = ' + json.dumps(members) + "\n"
        )
        manifest += """
[profile.dev]
opt-level = 0
debug = 2
incremental = true
panic = "unwind"

[profile.release]
opt-level = 3
debug = 0
incremental = false
lto = false
codegen-units = 16
panic = "unwind"
strip = "none"
"""
        write(root / "Cargo.toml", manifest)
        dep = {
            "anyhow": 'anyhow = "=1.0.104"',
            "eros-erased": 'eros = "=0.8.1"',
            "eros-typed": 'eros = "=0.8.1"',
            "eros-typed-lean": 'eros = { version = "=0.8.1", default-features = false }',
            "thiserror": 'thiserror = "=2.0.21"',
        }[variant]
        for member in members:
            manifest = f'[package]\nname = "{member}"\nversion = "0.1.0"\nedition = "2024"\npublish = false\n'
            if member.startswith("domain"):
                manifest += (
                    f'\n[dependencies]\nmodel = {{ path = "../model" }}\n{dep}\n'
                )
            if member == "app":
                manifest += '\n[dependencies]\nmodel = { path = "../model" }\n'
                manifest += "".join(
                    f'domain{d} = {{ path = "../domain{d}" }}\n' for d in range(DOMAINS)
                )
            write(root / member / "Cargo.toml", manifest)
        write(root / "model/src/lib.rs", MODEL)
        business = 0
        for d in range(DOMAINS):
            write(root / f"domain{d}/src/errors.rs", errors_source(variant, d))
            lib = "mod errors;\nuse model::{Record, Request};\n"
            for m in range(modules):
                lib += f"mod workflow_{m:03};\n"
                source, count = workflow_source(variant, d, m)
                if d == 0 and m == 0:
                    source = source.replace(
                        "let salt = 1u64;", "let salt = 1u64; // BENCH_EDIT_LEAF", 1
                    )
                business += count
                write(root / f"domain{d}/src/workflow_{m:03}.rs", source)
            lib += f"/// Dispatch every workflow through the domain boundary.\npub fn run(index: usize, input: Request) -> {result_type(variant, range(8))} {{\n"
            # Import only the boundary alias; erased variants deliberately have no alias.
            if variant.startswith("eros-typed") or variant == "thiserror":
                lib = "use errors::Set01234567;\n" + lib
            lib += "    match index {\n"
            lib += "".join(
                f"        {m} => workflow_{m:03}::stage_6(input),\n"
                for m in range(modules)
            )
            lib += '        _ => panic!("workflow index out of range"),\n    }\n}\n'
            write(root / f"domain{d}/src/lib.rs", lib)
        app = """use model::Request;

fn main() {
    let mut args = std::env::args().skip(1);
    let seed: u64 = args.next().expect("seed").parse().expect("numeric seed");
    let fail: u8 = args.next().expect("failure selector").parse().expect("numeric selector");
    let input = Request { seed, fail };
    let mut checksum = 0u64;
    let mut successes = 0u64;
    let mut failures = 0u64;
    let mut categories = [0u64; 64];
"""
        for d in range(DOMAINS):
            app += f"""    for index in 0..{modules} {{
        match domain{d}::run(index, input) {{
            Ok(record) => {{
                checksum = checksum.wrapping_add(record.value ^ record.count);
                successes += 1;
            }}
            Err(error) => {{
                let category = error.to_string();
                assert_eq!(category, format!("D{d}E{{fail}}"));
                categories[{f"{d * 8} + " if d else ""}usize::from(fail)] += 1;
                failures += 1;
            }}
        }}
    }}
"""
        app += '    println!("{checksum} {successes} {failures} {categories:?}");\n}\n'
        write(root / "app/src/main.rs", app)
        lock = ROOT / "locks" / f"{variant}.lock"
        if lock.exists():
            shutil.copyfile(lock, root / "Cargo.lock")
        files = sorted(p for p in root.rglob("*") if p.is_file())
        hashes = {str(p.relative_to(root)): digest(p.read_bytes()) for p in files}
        total = sum(lines_count(p.read_text()) for p in files if p.suffix == ".rs")
        if enforce_size and business <= 60000:
            raise ValueError(f"insufficient business source: {business}")
        metadata[variant] = {
            "business_body_lines": business,
            "rust_code_lines": total,
            "crates": 10,
            "workflows": DOMAINS * modules,
            "leaf_error_types": 64,
            "precise_sets": 136
            if variant in ("eros-typed", "eros-typed-lean", "thiserror")
            else 0,
            "workflow_functions": DOMAINS * modules * 15,
            "propagation_sites": DOMAINS * modules * 14,
            "files": hashes,
        }
    write(destination / "manifest.json", json.dumps(metadata, indent=2) + "\n")
    return metadata


def environment():
    env = os.environ.copy()
    for key in list(env):
        if key.startswith(("CARGO_", "RUST")):
            del env[key]
    home = ROOT / "scratch/cargo-home"
    home.mkdir(parents=True, exist_ok=True)
    registry = home / "registry"
    if not registry.exists():
        original = Path(os.environ.get("CARGO_HOME", Path.home() / ".cargo")).resolve()
        registry.symlink_to(original / "registry", target_is_directory=True)
    env.update(
        {
            "CARGO_TERM_COLOR": "never",
            "RUST_BACKTRACE": "0",
            "RUST_LIB_BACKTRACE": "0",
            "CARGO_HOME": str(home),
            "CARGO_TERM_PROGRESS_WHEN": "never",
        }
    )
    return env


def command(variant, target, mode="check", verbose=False, experiment=PRIMARY):
    args = [
        "cargo",
        mode,
        "--manifest-path",
        str(experiment.generated / variant / "Cargo.toml"),
        "--workspace",
        "--locked",
        "--offline",
        "--jobs",
        str(JOBS),
        "--target-dir",
        str(target),
    ]
    if verbose:
        args.append("-v")
    return args


def execute(args, env=None, **kwargs):
    return subprocess.run(
        args,
        env=env or environment(),
        check=True,
        text=True,
        cwd=command_directory(),
        **kwargs,
    )


def command_directory():
    """Keep Cargo's ancestor config search outside the checkout and user home."""
    global COMMAND_DIRECTORY
    if COMMAND_DIRECTORY is None:
        COMMAND_DIRECTORY = tempfile.TemporaryDirectory(
            prefix="eros-bench-cwd-", dir="/tmp"
        )
    return Path(COMMAND_DIRECTORY.name)


def prepare(experiment=PRIMARY):
    if experiment == CONTEXT:
        # Reuse the published pinned graph; context adds no dependencies.
        generate(experiment.generated, experiment=experiment)
        for variant in experiment.variants:
            execute(
                [
                    "cargo",
                    "fetch",
                    "--locked",
                    "--manifest-path",
                    str(experiment.generated / variant / "Cargo.toml"),
                ],
                env=os.environ.copy(),
            )
        return
    generate(ROOT / "generated")
    for variant in VARIANTS:
        manifest = ROOT / "generated" / variant / "Cargo.toml"
        args = ["cargo", "fetch", "--manifest-path", str(manifest)]
        if (manifest.parent / "Cargo.lock").exists():
            args.append("--locked")
        # Fetching is not timed; allow the user's registry authentication here only.
        execute(args, env=os.environ.copy())
        lock = ROOT / "locks" / f"{variant}.lock"
        lock.parent.mkdir(exist_ok=True)
        shutil.copyfile(manifest.parent / "Cargo.lock", lock)
    generate(ROOT / "generated")


@contextmanager
def exclusive():
    """Prevent two local harness commands from racing source edits or timings."""
    (ROOT / "scratch").mkdir(exist_ok=True)
    with (ROOT / "scratch/runner.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def source_manifest(experiment=PRIMARY):
    """Verify sources and lockfiles still match the recorded generation."""
    path = experiment.generated / "manifest.json"
    metadata = json.loads(path.read_text())
    for variant, entry in metadata.items():
        for name, expected in entry["files"].items():
            actual = digest((experiment.generated / variant / name).read_bytes())
            if actual != expected:
                raise ValueError(f"modified generated input: {variant}/{name}")
    return metadata


def inspect_environment():
    # Cargo searches ancestors even with an isolated CARGO_HOME. Reject instead
    # of silently allowing local target/profile/linker configuration to leak in.
    directory = command_directory()
    configs = [
        p / ".cargo" / name
        for p in (directory, *directory.parents)
        for name in ("config", "config.toml")
    ]
    configs.extend(
        Path(environment()["CARGO_HOME"]) / name for name in ("config", "config.toml")
    )
    if any(p.exists() for p in configs):
        raise ValueError(
            f"unexpected Cargo config: {[str(p) for p in configs if p.exists()]}"
        )
    result = {
        "timestamp_utc": utc_now(),
        "platform": platform.platform(),
        "workdir": str(ROOT),
        "jobs": JOBS,
        "cpu_count": os.cpu_count(),
        "environment": {
            k: v
            for k, v in environment().items()
            if k.startswith(("CARGO_", "RUST", "NIX_CFLAGS", "NIX_LDFLAGS"))
            or k in ("CC", "CXX", "AR", "LD")
        },
        "generator_sha256": digest(Path(__file__).read_bytes()),
        "flake_lock_sha256": digest((ROOT / "flake.lock").read_bytes()),
        "loadavg": Path("/proc/loadavg").read_text(),
        "meminfo": Path("/proc/meminfo").read_text(),
        "os_release": Path("/etc/os-release").read_text(),
    }
    for args in (
        ["rustc", "-Vv"],
        ["cargo", "-V"],
        ["lscpu"],
        ["uname", "-a"],
        ["cc", "--version"],
        ["ld", "--version"],
        ["strip", "--version"],
        ["size", "--version"],
        ["time", "--version"],
        ["df", "-T", str(ROOT)],
    ):
        result[" ".join(args)] = execute(args, capture_output=True).stdout
    result["tools"] = {
        tool: str(Path(shutil.which(tool)).resolve())
        for tool in ("rustc", "cargo", "cc", "ld", "strip", "size", "time")
    }
    return result


def oracle(seed, fail, modules=MODULES):
    """Independent integer model for the successful checksum and failure counts."""
    mask = (1 << 64) - 1

    def rol(x, n):
        return ((x << n) | (x >> (64 - n))) & mask

    def ror(x, n):
        return ((x >> n) | (x << (64 - n))) & mask

    checksum = 0
    categories = [0] * 64
    if fail < 8:
        for d in range(DOMAINS):
            categories[d * 8 + fail] = modules
        return f"0 0 {DOMAINS * modules} {categories}\n"
    for d in range(DOMAINS):
        for m in range(modules):
            records = {}
            for e in range(8):
                salt = 1 + d * 257 + m * 17 + e
                key = (seed + salt) & mask
                text = f"{key:016x}"
                hashed = salt
                for byte in text.encode():
                    hashed = rol(hashed, 5) ^ byte
                digits = sum(c.isdigit() for c in text)
                capacity = (key & 255) + 1
                accepted = min(hashed & 511, capacity)
                normalized = accepted * (digits + 1) + (16 - digits)
                balance = ((normalized ^ (capacity - accepted)) * 3) & mask
                records[e] = (((hashed + balance) & mask) ^ key, accepted + 1)
            stages = []
            for idx, ids in enumerate(groups(m % 8)):
                if idx < 4:
                    left, right = (records[e] for e in ids)
                elif idx < 6:
                    left, right = stages[2 * (idx - 4) : 2 * (idx - 4) + 2]
                else:
                    left, right = stages[4:6]
                count = (left[1] + right[1]) & mask
                value = (
                    (rol(left[0], idx + 3) ^ right[0]) + count * (m + idx + 1)
                ) & mask
                stages.append((value ^ ror(seed, idx + 1), count))
            checksum = (checksum + (stages[6][0] ^ stages[6][1])) & mask
    return f"{checksum} {DOMAINS * modules} 0 {categories}\n"


def validate_binary(binary):
    cases = []
    for seed, fail in [(s, 255) for s in (0, 1, 123456789, (1 << 64) - 1)] + [
        (37, e) for e in range(8)
    ]:
        output = execute(
            [str(binary), str(seed), str(fail)], capture_output=True
        ).stdout
        expected = oracle(seed, fail)
        if output != expected:
            raise AssertionError(
                f"behavior differs: {binary}, {seed}, {fail}: {output!r} != {expected!r}"
            )
        cases.append({"seed": seed, "fail": fail, "stdout": output})
    return cases


def verify(output, experiment=PRIMARY):
    """Full-size behavior, determinism, and lint gates, outside scored trials."""
    output.mkdir(parents=True, exist_ok=False)
    metadata = source_manifest(experiment)
    with tempfile.TemporaryDirectory(dir=ROOT / "scratch") as temporary:
        regenerated = generate(Path(temporary), experiment=experiment)
        if metadata != regenerated:
            raise AssertionError("generation is not deterministic")
    results = {}
    for variant in experiment.variants:
        target = experiment.targets / "verify" / variant
        with (output / f"{variant}.log").open("w") as log:
            execute(
                command(variant, target, "clippy", experiment=experiment)
                + (["--all-targets"] if experiment == CONTEXT else [])
                + ["--", "-D", "warnings"],
                stdout=log,
                stderr=log,
            )
            results[variant] = {}
            for release in (False, True):
                args = command(variant, target, "build", experiment=experiment) + (
                    ["--release"] if release else []
                )
                execute(args, stdout=log, stderr=log)
                profile = "release" if release else "debug"
                results[variant][profile] = validate_binary(target / profile / "app")
                if experiment == CONTEXT:
                    execute(
                        command(variant, target, "test", experiment=experiment)
                        + ["--tests"]
                        + (["--release"] if release else []),
                        stdout=log,
                        stderr=log,
                    )
            if experiment == CONTEXT:
                results[variant]["context_tests"] = "passed in debug and release"
    write(output / "validation.json", json.dumps(results, indent=2) + "\n")
    write(output / "source-manifest.json", json.dumps(metadata, indent=2) + "\n")


def mutation_path(variant, shared=False, experiment=PRIMARY):
    return (
        experiment.generated
        / variant
        / ("model/src/lib.rs" if shared else "domain0/src/workflow_000.rs")
    )


def mutate(path, state, shared=False):
    before, after = (
        ("wrapping_mul(3) // BENCH_EDIT_SHARED", "wrapping_mul(5) // BENCH_EDIT_SHARED")
        if shared
        else (
            "let salt = 1u64; // BENCH_EDIT_LEAF",
            "let salt = 2u64; // BENCH_EDIT_LEAF",
        )
    )
    text = path.read_text()
    old, new = (before, after) if state else (after, before)
    if text.count(old) != 1 or new in text:
        raise ValueError(f"mutation state mismatch: {path}")
    changed = text.replace(old, new)
    assert len(changed) == len(text)
    path.write_text(changed)


def clean_local(variant, target, log, experiment=PRIMARY):
    args = [
        "cargo",
        "clean",
        "--manifest-path",
        str(experiment.generated / variant / "Cargo.toml"),
        "--target-dir",
        str(target),
    ]
    for package in ["model", "app"] + [f"domain{d}" for d in range(DOMAINS)]:
        args.extend(["-p", package])
    execute(args, stdout=log, stderr=log)


def audit_rebuild(log, expected):
    """Verbose rustc command lines prove the intended package rebuild boundary."""
    rebuilt = set()
    for line in log.read_text().splitlines():
        if "Running `" in line and "--crate-name " in line:
            rebuilt.add(line.split("--crate-name ", 1)[1].split()[0])
    if rebuilt != set(expected):
        raise AssertionError(f"{log}: rebuilt {rebuilt}, expected {set(expected)}")
    return sorted(rebuilt)


def summarize(output, experiment=PRIMARY):
    records = [
        json.loads(line) for line in (output / "raw.jsonl").read_text().splitlines()
    ]
    if any(row["returncode"] for row in records):
        raise ValueError("cannot summarize a run containing failed commands")
    plan = json.loads((output / "plan.json").read_text())

    def key(row):
        return row["mode"], row["variant"], row["trial"], row["position"]

    expected = [key(row) for row in plan["cells"]]
    actual = [key(row) for row in records]
    if actual != expected:
        raise ValueError(
            "incomplete, duplicated, or out-of-order measurements; no summary produced"
        )
    summary = {}
    for mode in dict.fromkeys(row["mode"] for row in records):
        summary[mode] = {}
        for variant in experiment.variants:
            rows = [r for r in records if r["mode"] == mode and r["variant"] == variant]
            if not rows:
                raise ValueError(f"incomplete mode: {mode}/{variant}")
            values = [r["elapsed_seconds"] for r in rows]
            median = statistics.median(values)
            summary[mode][variant] = {
                "n": len(values),
                "median_seconds": median,
                "min_seconds": min(values),
                "max_seconds": max(values),
                "mad_seconds": statistics.median(abs(x - median) for x in values),
                "median_user_seconds": statistics.median(
                    r["user_seconds"] for r in rows
                ),
                "median_system_seconds": statistics.median(
                    r["system_seconds"] for r in rows
                ),
                "median_maxrss_kib": statistics.median(r["maxrss_kib"] for r in rows),
            }
    write(output / "summary.json", json.dumps(summary, indent=2) + "\n")
    table = (
        "| Mode (seconds: median [min–max]) | "
        + " | ".join(experiment.variants)
        + " |\n"
    )
    table += "|---|" + "---|" * len(experiment.variants) + "\n"
    for mode, entries in summary.items():
        table += f"| {mode} (n={entries[experiment.variants[0]]['n']}) |"
        for entry in entries.values():
            table += f" {entry['median_seconds']:.3f} [{entry['min_seconds']:.3f}–{entry['max_seconds']:.3f}] |"
        table += "\n"
    write(output / "table.md", table)
    return summary


def binary_sizes(binary, output, variant, profile, trial):
    entry = {
        "variant": variant,
        "profile": profile,
        "trial": trial,
        "bytes": binary.stat().st_size,
        "sha256": digest(binary.read_bytes()),
    }
    entry["size_output"] = execute(["size", str(binary)], capture_output=True).stdout
    if profile == "release":
        with tempfile.TemporaryDirectory(dir=ROOT / "scratch") as temporary:
            stripped = Path(temporary) / "app"
            shutil.copyfile(binary, stripped)
            execute(["strip", "--strip-all", str(stripped)])
            entry["stripped_bytes"] = stripped.stat().st_size
    with (output / "sizes.jsonl").open("a") as stream:
        stream.write(json.dumps(entry) + "\n")


def measurement_plan(experiment, check_trials, build_trials, noop_trials):
    """Predeclare every scored command, including its order within each block."""
    variants = experiment.variants
    modes = (
        "check-cold-target",
        "check-warm-deps",
        "check-noop",
        "check-edit-leaf",
        "check-edit-shared",
        "build-dev-cold-target",
        "build-release-cold-target",
    )
    if min(check_trials, build_trials, noop_trials) < 1:
        raise ValueError("trial counts must be positive")
    cells = []
    for mode in modes:
        trials = (
            build_trials
            if mode.startswith("build")
            else noop_trials
            if mode == "check-noop"
            else check_trials
        )
        for trial in range(trials):
            order = (
                variants[trial % len(variants) :] + variants[: trial % len(variants)]
            )
            cells.extend(
                {"mode": mode, "variant": variant, "trial": trial, "position": position}
                for position, variant in enumerate(order)
            )
    return {"experiment": experiment.name, "variants": variants, "cells": cells}


def measure(output, check_trials=5, build_trials=3, noop_trials=10, experiment=PRIMARY):
    plan = measurement_plan(experiment, check_trials, build_trials, noop_trials)
    modes = list(dict.fromkeys(cell["mode"] for cell in plan["cells"]))
    variants = experiment.variants
    metadata = source_manifest(experiment)
    output.mkdir(parents=True, exist_ok=False)
    (output / "logs").mkdir()
    environment_metadata = inspect_environment()
    environment_metadata["experiment"] = experiment.name
    if experiment == CONTEXT:
        environment_metadata["context_generator_sha256"] = digest(
            (ROOT / "context_workload.py").read_bytes()
        )
    write(
        output / "environment.json", json.dumps(environment_metadata, indent=2) + "\n"
    )
    write(output / "source-manifest.json", json.dumps(metadata, indent=2) + "\n")
    shutil.copyfile(Path(__file__), output / "bench.py.snapshot")
    if experiment == CONTEXT:
        shutil.copyfile(
            ROOT / "context_workload.py", output / "context_workload.py.snapshot"
        )
    write(
        output / "plan.json",
        json.dumps(plan, indent=2) + "\n",
    )
    audit = {}
    originals = {
        mutation_path(v, shared, experiment): mutation_path(
            v, shared, experiment
        ).read_bytes()
        for v in variants
        for shared in (False, True)
    }
    states = {}
    try:
        with (output / "setup.log").open("w") as setup:
            for mode in modes:
                trials = (
                    build_trials
                    if mode.startswith("build")
                    else noop_trials
                    if mode == "check-noop"
                    else check_trials
                )
                if trials < 1:
                    raise ValueError("trial counts must be positive")
                for variant in variants:
                    target = experiment.targets / "measure" / mode / variant
                    if target.exists():
                        shutil.rmtree(target)
                    if "cold-target" not in mode:
                        execute(
                            command(variant, target, experiment=experiment),
                            stdout=setup,
                            stderr=setup,
                        )
                    if mode in (
                        "check-warm-deps",
                        "check-edit-leaf",
                        "check-edit-shared",
                    ):
                        if mode == "check-warm-deps":
                            clean_local(variant, target, setup, experiment)
                            expected = ["model", "app"] + [
                                f"domain{d}" for d in range(DOMAINS)
                            ]
                        else:
                            shared = mode == "check-edit-shared"
                            mutate(
                                mutation_path(variant, shared, experiment), True, shared
                            )
                            states[variant] = True
                            expected = (
                                ["model", "app"]
                                + [f"domain{d}" for d in range(DOMAINS)]
                                if shared
                                else ["domain0", "app"]
                            )
                        logpath = output / "logs" / f"audit-{mode}-{variant}.log"
                        with logpath.open("w") as log:
                            execute(
                                command(
                                    variant, target, verbose=True, experiment=experiment
                                ),
                                stdout=log,
                                stderr=log,
                            )
                        audit[f"{mode}/{variant}"] = audit_rebuild(logpath, expected)
                for trial in range(trials):
                    # Complete cycles balance positions; exact order is recorded.
                    order = (
                        variants[trial % len(variants) :]
                        + variants[: trial % len(variants)]
                    )
                    for position, variant in enumerate(order):
                        target = experiment.targets / "measure" / mode / variant
                        if "cold-target" in mode and target.exists():
                            shutil.rmtree(target)
                        if mode == "check-warm-deps":
                            clean_local(variant, target, setup, experiment)
                        if mode.startswith("check-edit"):
                            shared = mode == "check-edit-shared"
                            states[variant] = not states[variant]
                            mutate(
                                mutation_path(variant, shared, experiment),
                                states[variant],
                                shared,
                            )
                        args = command(
                            variant,
                            target,
                            "build" if mode.startswith("build") else "check",
                            experiment=experiment,
                        )
                        if mode == "build-release-cold-target":
                            args.append("--release")
                        stem = f"{mode}-{trial:02}-{variant}"
                        stdout_path = output / "logs" / f"{stem}.stdout"
                        stderr_path = output / "logs" / f"{stem}.stderr"
                        resource_path = output / "logs" / f"{stem}.resources"
                        timed = [
                            shutil.which("time"),
                            "-f",
                            "%U %S %M",
                            "-o",
                            str(resource_path),
                            *args,
                        ]
                        record = {
                            "mode": mode,
                            "variant": variant,
                            "trial": trial,
                            "position": position,
                            "command": args,
                            "timestamp_utc": utc_now(),
                            "stdout": str(stdout_path.relative_to(output)),
                            "stderr": str(stderr_path.relative_to(output)),
                            "resources": str(resource_path.relative_to(output)),
                            "loadavg_before": list(os.getloadavg()),
                        }
                        print(f"{mode} {trial + 1}/{trials} {variant}", flush=True)
                        env = environment()
                        with (
                            stdout_path.open("w") as stdout,
                            stderr_path.open("w") as stderr,
                        ):
                            start = time.monotonic()
                            proc = subprocess.run(
                                timed,
                                env=env,
                                cwd=command_directory(),
                                stdout=stdout,
                                stderr=stderr,
                            )
                            record["elapsed_seconds"] = time.monotonic() - start
                        record["returncode"] = proc.returncode
                        values = resource_path.read_text().splitlines()[-1].split()
                        record.update(
                            user_seconds=float(values[0]),
                            system_seconds=float(values[1]),
                            maxrss_kib=int(values[2]),
                        )
                        with (output / "raw.jsonl").open("a") as stream:
                            stream.write(json.dumps(record) + "\n")
                            stream.flush()
                            os.fsync(stream.fileno())
                        if proc.returncode:
                            raise RuntimeError(
                                f"scored command failed; see {stderr_path}"
                            )
                        if mode.startswith("build"):
                            profile = "release" if "release" in mode else "debug"
                            binary = target / profile / "app"
                            binary_sizes(binary, output, variant, profile, trial)
                            validation = validate_binary(binary)
                            write(
                                output / "logs" / f"{stem}.validation.json",
                                json.dumps(validation, indent=2) + "\n",
                            )
                if mode.startswith("check-edit"):
                    for variant in variants:
                        path = mutation_path(
                            variant, mode == "check-edit-shared", experiment
                        )
                        path.write_bytes(originals[path])
    finally:
        for path, content in originals.items():
            if path.read_bytes() != content:
                path.write_bytes(content)
    write(output / "audit.json", json.dumps(audit, indent=2) + "\n")
    source_manifest(experiment)
    summarize(output, experiment)
    write(
        output / "complete.json",
        json.dumps(
            {
                "timestamp_utc": utc_now(),
                "check_trials": check_trials,
                "build_trials": build_trials,
                "noop_trials": noop_trials,
            },
            indent=2,
        )
        + "\n",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=["generate", "prepare", "verify", "measure", "summarize"]
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--experiment", choices=["primary", "context"], default="primary"
    )
    parser.add_argument("--check-trials", type=int)
    parser.add_argument("--build-trials", type=int, default=3)
    parser.add_argument("--noop-trials", type=int)
    args = parser.parse_args()
    experiment = CONTEXT if args.experiment == "context" else PRIMARY
    with exclusive():
        if args.action == "generate":
            generate(experiment.generated, experiment=experiment)
        elif args.action == "prepare":
            prepare(experiment)
        else:
            if args.output is None:
                parser.error("--output is required")
            output = args.output.resolve()
            if args.action == "verify":
                verify(output, experiment)
            elif args.action == "measure":
                measure(
                    output,
                    args.check_trials
                    if args.check_trials is not None
                    else (6 if experiment == CONTEXT else 5),
                    args.build_trials,
                    args.noop_trials
                    if args.noop_trials is not None
                    else (12 if experiment == CONTEXT else 10),
                    experiment,
                )
            else:
                summarize(output, experiment)


if __name__ == "__main__":
    main()
