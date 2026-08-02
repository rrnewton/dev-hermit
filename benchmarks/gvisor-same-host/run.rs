#!/usr/bin/env rust-script
//! Provision runsc and run the same-host gVisor application benchmark matrix.
//!
//! ```cargo
//! [dependencies]
//! libc = "0.2"
//! serde = { version = "1", features = ["derive"] }
//! serde_json = "1"
//! sha2 = "0.10"
//! ```

use serde::{Deserialize, Serialize};
use serde_json::json;
use sha2::{Digest, Sha256, Sha512};
use std::collections::{BTreeMap, BTreeSet};
use std::env;
use std::fs::{self, File};
use std::io::{Read, Write};
use std::os::unix::fs::PermissionsExt;
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{exit, Command, ExitStatus, Stdio};
use std::thread;
use std::time::{Duration, Instant};

const RUNSC_RELEASE: &str = "20260727.0";
const RUNSC_URL: &str =
    "https://storage.googleapis.com/gvisor/releases/release/20260727.0/x86_64/runsc";
const RUNSC_SHA512: &str = "ab99ea1b0e2d169ec95473ea6c44abdac9b6b63d9c483f898487fd2b3c32d63bfa9ea104a3d5eed217b90cfc880ceb7a1130a9f7daef6e50656b6e028a8f52e3";
const GETPID_ITERATIONS: u64 = 1_000_000;

const USAGE: &str = r#"Usage: benchmarks/gvisor-same-host/run.rs [OPTIONS]

Options:
  --platforms LIST              systrap,kvm,ptrace (default: all)
  --workloads LIST              getpid,redis,ffmpeg,absl,tensorflow (default: all)
  --repetitions N               measured getpid/redis/ffmpeg samples (default: 3)
  --warmups N                   getpid/redis/ffmpeg warmups (default: 1)
  --expensive-repetitions N     measured ABSL/TensorFlow samples (default: 1)
  --expensive-warmups N         ABSL/TensorFlow warmups (default: 0)
  --timeout-seconds N           per-sample wall timeout (default: 900)
  --output PATH                 tracked text result directory
  --provision-only              fetch/verify runsc, images, roots, and getpid guest
  --skip-provision              require already-provisioned artifacts
  -h, --help                    show this help

Invoke with `with-proxy` when provisioning may access external registries.
"#;

const PLATFORMS: [&str; 3] = ["systrap", "kvm", "ptrace"];
const WORKLOADS: [&str; 5] = ["getpid", "redis", "ffmpeg", "absl", "tensorflow"];

#[derive(Clone, Copy)]
struct ImageSpec {
    name: &'static str,
    reference: &'static str,
}

#[derive(Clone, Copy)]
struct DownloadSpec {
    filename: &'static str,
    url: &'static str,
    sha256: &'static str,
}

const IMAGES: [ImageSpec; 4] = [
    ImageSpec {
        name: "redis",
        reference: "us-central1-docker.pkg.dev/gvisor-presubmit/gvisor-presubmit-images/benchmarks/redis_x86_64@sha256:7f3745cd93de68d0cff864d37f409b1ff6abaa18b051f1b3248bfd530b35cb7e",
    },
    ImageSpec {
        name: "ffmpeg",
        reference: "us-central1-docker.pkg.dev/gvisor-presubmit/gvisor-presubmit-images/benchmarks/ffmpeg_x86_64@sha256:14ddfe863d28ac20b27aceb4eaf3a2ed5a0dc9269fb3952d6995e4de3c99df46",
    },
    ImageSpec {
        name: "absl",
        reference: "us-central1-docker.pkg.dev/gvisor-presubmit/gvisor-presubmit-images/benchmarks/absl_x86_64@sha256:5f3c919fd4d1bef9c220345cb577efd3bfa6c3656420c82f63d6b3108ffe05a0",
    },
    ImageSpec {
        name: "tensorflow",
        reference: "us-central1-docker.pkg.dev/gvisor-presubmit/gvisor-presubmit-images/benchmarks/tensorflow_x86_64@sha256:7accf29be680e86d4cf0abe88fcc711cd6dac1d07b488466fcd73b21c54345f7",
    },
];

const ABSL_DOWNLOADS: [DownloadSpec; 7] = [
    DownloadSpec {
        filename: "googletest-1.15.2.tar.gz",
        url: "https://github.com/google/googletest/releases/download/v1.15.2/googletest-1.15.2.tar.gz",
        sha256: "7b42b4d6ed48810c5362c265a17faebe90dc2373c885e5216439d37927f02926",
    },
    DownloadSpec {
        filename: "re2-2024-07-02.tar.gz",
        url: "https://github.com/google/re2/releases/download/2024-07-02/re2-2024-07-02.tar.gz",
        sha256: "eb2df807c781601c14a260a507a5bb4509be1ee626024cb45acbd57cb9d4032b",
    },
    DownloadSpec {
        filename: "v1.8.3.tar.gz",
        url: "https://github.com/google/benchmark/archive/refs/tags/v1.8.3.tar.gz",
        sha256: "6bc180a57d23d4d9515519f92b0c83d61b05b5bab188961f36ac7b06b0d9e9ce",
    },
    DownloadSpec {
        filename: "bazel-skylib-1.5.0.tar.gz",
        url: "https://github.com/bazelbuild/bazel-skylib/releases/download/1.5.0/bazel-skylib-1.5.0.tar.gz",
        sha256: "cd55a062e763b9349921f0f5db8c3933288dc8ba4f76dd9416aac68acee3cb94",
    },
    DownloadSpec {
        filename: "platforms-0.0.10.tar.gz",
        url: "https://mirror.bazel.build/github.com/bazelbuild/platforms/releases/download/0.0.10/platforms-0.0.10.tar.gz",
        sha256: "218efe8ee736d26a3572663b374a253c012b716d8af0c07e842e82f238a0a7ee",
    },
    DownloadSpec {
        filename: "rules_cc-0.0.9.tar.gz",
        url: "https://github.com/bazelbuild/rules_cc/releases/download/0.0.9/rules_cc-0.0.9.tar.gz",
        sha256: "2037875b9a4456dce4a79d112a8ae885bbc4aad968e6587dca6e64f3a0900cdf",
    },
    DownloadSpec {
        filename: "rules_python-0.24.0.tar.gz",
        url: "https://github.com/bazelbuild/rules_python/releases/download/0.24.0/rules_python-0.24.0.tar.gz",
        sha256: "0a8003b044294d7840ac7d9d73eef05d6ceb682d7516781a4ec62eeb34702578",
    },
];

#[derive(Default)]
struct Args {
    platforms: Vec<String>,
    workloads: Vec<String>,
    repetitions: usize,
    warmups: usize,
    expensive_repetitions: usize,
    expensive_warmups: usize,
    timeout: Duration,
    output: Option<PathBuf>,
    provision_only: bool,
    skip_provision: bool,
}

#[derive(Clone)]
struct Workload {
    name: &'static str,
    image: Option<ImageSpec>,
    script: Option<&'static str>,
    needs_network: bool,
    expensive: bool,
}

#[derive(Serialize, Deserialize, Clone)]
struct Sample {
    workload: String,
    engine: String,
    repetition: usize,
    elapsed_ms: f64,
    metric_value: f64,
    metric_unit: String,
    stdout_sha256: String,
    status: String,
    reason: String,
}

struct RunOutput {
    status: ExitStatus,
    elapsed: Duration,
    stdout: String,
    stderr: String,
}

fn die(message: &str) -> ! {
    eprintln!("gvisor-same-host: {message}");
    exit(2);
}

fn take_value<I: Iterator<Item = String>>(flag: &str, args: &mut I) -> String {
    args.next()
        .unwrap_or_else(|| die(&format!("{flag} requires a value")))
}

fn parse_positive(flag: &str, value: String) -> usize {
    value
        .parse::<usize>()
        .ok()
        .filter(|value| *value > 0)
        .unwrap_or_else(|| die(&format!("{flag} must be positive")))
}

fn parse_nonnegative(flag: &str, value: String) -> usize {
    value
        .parse::<usize>()
        .unwrap_or_else(|_| die(&format!("{flag} must be nonnegative")))
}

fn parse_list(raw: String, allowed: &[&str], flag: &str) -> Vec<String> {
    let values: Vec<String> = raw
        .split(',')
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
        .collect();
    let unique: BTreeSet<&str> = values.iter().map(String::as_str).collect();
    if values.is_empty()
        || unique.len() != values.len()
        || values
            .iter()
            .any(|value| !allowed.contains(&value.as_str()))
    {
        die(&format!(
            "{flag} must be a unique comma-separated subset of {}",
            allowed.join(",")
        ));
    }
    values
}

fn parse_args() -> Args {
    let mut parsed = Args {
        platforms: PLATFORMS.iter().map(|value| value.to_string()).collect(),
        workloads: WORKLOADS.iter().map(|value| value.to_string()).collect(),
        repetitions: 3,
        warmups: 1,
        expensive_repetitions: 1,
        expensive_warmups: 0,
        timeout: Duration::from_secs(900),
        ..Args::default()
    };
    let mut args = env::args().skip(1);
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--platforms" => {
                parsed.platforms = parse_list(take_value(&arg, &mut args), &PLATFORMS, &arg)
            }
            "--workloads" => {
                parsed.workloads = parse_list(take_value(&arg, &mut args), &WORKLOADS, &arg)
            }
            "--repetitions" => {
                parsed.repetitions = parse_positive(&arg, take_value(&arg, &mut args))
            }
            "--warmups" => parsed.warmups = parse_nonnegative(&arg, take_value(&arg, &mut args)),
            "--expensive-repetitions" => {
                parsed.expensive_repetitions = parse_positive(&arg, take_value(&arg, &mut args))
            }
            "--expensive-warmups" => {
                parsed.expensive_warmups = parse_nonnegative(&arg, take_value(&arg, &mut args))
            }
            "--timeout-seconds" => {
                parsed.timeout =
                    Duration::from_secs(parse_positive(&arg, take_value(&arg, &mut args)) as u64)
            }
            "--output" => parsed.output = Some(take_value(&arg, &mut args).into()),
            "--provision-only" => parsed.provision_only = true,
            "--skip-provision" => parsed.skip_provision = true,
            "-h" | "--help" => {
                print!("{USAGE}");
                exit(0);
            }
            _ => die(&format!("unknown argument: {arg}\n\n{USAGE}")),
        }
    }
    if parsed.provision_only && parsed.skip_provision {
        die("--provision-only and --skip-provision are mutually exclusive");
    }
    parsed
}

fn repository_root() -> PathBuf {
    let mut path = env::current_dir().unwrap_or_else(|error| die(&error.to_string()));
    loop {
        if path.join(".gitmodules").is_file() && path.join("AGENTS.md").is_file() {
            return path;
        }
        if !path.pop() {
            die("could not locate dev-hermit repository root");
        }
    }
}

fn timestamp() -> String {
    let output = Command::new("date")
        .args(["-u", "+%Y%m%dT%H%M%SZ"])
        .output()
        .unwrap_or_else(|error| die(&format!("date: {error}")));
    if !output.status.success() {
        die("date failed");
    }
    String::from_utf8_lossy(&output.stdout).trim().to_owned()
}

fn short_hostname(value: &str) -> String {
    let hostname = value.trim().split('.').next().unwrap_or_default();
    if hostname.is_empty() {
        die("hostname is empty");
    }
    hostname.to_owned()
}

fn hostname() -> String {
    let output = Command::new("hostname")
        .output()
        .unwrap_or_else(|error| die(&format!("hostname: {error}")));
    short_hostname(&String::from_utf8_lossy(&output.stdout))
}

fn repository_sha(root: &Path) -> String {
    let output = Command::new("git")
        .arg("-C")
        .arg(root)
        .args(["rev-parse", "HEAD"])
        .output()
        .unwrap_or_else(|error| die(&format!("git rev-parse HEAD: {error}")));
    if !output.status.success() {
        die("git rev-parse HEAD failed");
    }
    String::from_utf8_lossy(&output.stdout).trim().to_owned()
}

fn hex_digest(bytes: impl AsRef<[u8]>) -> String {
    bytes
        .as_ref()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

fn file_sha256(path: &Path) -> String {
    let mut file =
        File::open(path).unwrap_or_else(|error| die(&format!("open {}: {error}", path.display())));
    let mut digest = Sha256::new();
    let mut buffer = [0_u8; 1024 * 1024];
    loop {
        let count = file
            .read(&mut buffer)
            .unwrap_or_else(|error| die(&format!("read {}: {error}", path.display())));
        if count == 0 {
            break;
        }
        digest.update(&buffer[..count]);
    }
    hex_digest(digest.finalize())
}

fn file_sha512(path: &Path) -> String {
    let mut file =
        File::open(path).unwrap_or_else(|error| die(&format!("open {}: {error}", path.display())));
    let mut digest = Sha512::new();
    let mut buffer = [0_u8; 1024 * 1024];
    loop {
        let count = file
            .read(&mut buffer)
            .unwrap_or_else(|error| die(&format!("read {}: {error}", path.display())));
        if count == 0 {
            break;
        }
        digest.update(&buffer[..count]);
    }
    hex_digest(digest.finalize())
}

fn command_text(command: &Command) -> String {
    format!("{command:?}")
}

fn run_checked(command: &mut Command, description: &str) -> String {
    eprintln!("+ {}", command_text(command));
    let output = command
        .output()
        .unwrap_or_else(|error| die(&format!("{description}: {error}")));
    if !output.status.success() {
        die(&format!(
            "{description} exited {}\nstdout:\n{}\nstderr:\n{}",
            output.status,
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        ));
    }
    String::from_utf8_lossy(&output.stdout).trim().to_owned()
}

fn ensure_runsc(artifact_root: &Path) -> PathBuf {
    let bin = artifact_root
        .join("bin")
        .join(format!("runsc-release-{RUNSC_RELEASE}"));
    fs::create_dir_all(bin.parent().unwrap())
        .unwrap_or_else(|error| die(&format!("create runsc directory: {error}")));
    if bin.is_file() && file_sha512(&bin) == RUNSC_SHA512 {
        return bin;
    }
    if bin.exists() {
        die(&format!(
            "existing {} has the wrong SHA-512; remove it explicitly",
            bin.display()
        ));
    }
    let partial = bin.with_extension("partial");
    let mut curl = Command::new("curl");
    curl.args(["-fL", "--retry", "3", "--output"])
        .arg(&partial)
        .arg(RUNSC_URL);
    run_checked(&mut curl, "download pinned runsc");
    let actual = file_sha512(&partial);
    if actual != RUNSC_SHA512 {
        die(&format!(
            "runsc SHA-512 mismatch: expected {RUNSC_SHA512}, got {actual}"
        ));
    }
    let mut permissions = fs::metadata(&partial).unwrap().permissions();
    permissions.set_mode(0o755);
    fs::set_permissions(&partial, permissions).unwrap();
    fs::rename(&partial, &bin).unwrap();
    bin
}

fn image_root(artifact_root: &Path, image: ImageSpec) -> PathBuf {
    artifact_root.join("rootfs").join(image.name)
}

fn ensure_image_root(artifact_root: &Path, image: ImageSpec) {
    let root = image_root(artifact_root, image);
    let sentinel = root.join(".dev-hermit-image-reference");
    if sentinel.is_file() {
        let found = fs::read_to_string(&sentinel).unwrap();
        if found.trim() == image.reference {
            return;
        }
        die(&format!(
            "{} contains a different image; remove it explicitly before reprovisioning",
            root.display()
        ));
    }
    if root.exists() {
        die(&format!(
            "partial root {} exists without a sentinel; remove it explicitly",
            root.display()
        ));
    }

    let mut pull = Command::new("podman");
    pull.args(["pull", image.reference]);
    run_checked(&mut pull, &format!("pull {} image", image.name));

    fs::create_dir_all(&root).unwrap();
    let container_name = format!(
        "dev-hermit-gvisor-export-{}-{}",
        image.name,
        std::process::id()
    );
    let mut create = Command::new("podman");
    create
        .args([
            "create",
            "--name",
            &container_name,
            "--entrypoint",
            "/bin/true",
        ])
        .arg(image.reference);
    let container_id = run_checked(&mut create, "create export container");
    let tar_path = artifact_root.join(format!("{}.rootfs.tar", image.name));
    let mut export = Command::new("podman");
    export
        .args(["export", "--output"])
        .arg(&tar_path)
        .arg(&container_id);
    run_checked(&mut export, "export image root");
    let mut remove = Command::new("podman");
    remove.args(["rm", &container_id]);
    run_checked(&mut remove, "remove export container");
    let mut extract = Command::new("tar");
    extract.args(["-xf"]).arg(&tar_path).arg("-C").arg(&root);
    run_checked(&mut extract, "extract image root");
    fs::remove_file(&tar_path).unwrap();
    fs::write(sentinel, format!("{}\n", image.reference)).unwrap();
}

fn ensure_absl_dist(artifact_root: &Path, allow_download: bool) {
    let dist = artifact_root.join("downloads/absl");
    fs::create_dir_all(&dist).unwrap();
    for artifact in ABSL_DOWNLOADS {
        let path = dist.join(artifact.filename);
        if path.is_file() && file_sha256(&path) == artifact.sha256 {
            continue;
        }
        if path.exists() {
            die(&format!(
                "existing {} has the wrong SHA-256; remove it explicitly",
                path.display()
            ));
        }
        if !allow_download {
            die(&format!(
                "missing pinned ABSL dependency {}; run without --skip-provision",
                path.display()
            ));
        }
        let partial = path.with_extension("partial");
        let mut curl = Command::new("curl");
        curl.args(["-fL", "--retry", "3", "--output"])
            .arg(&partial)
            .arg(artifact.url);
        run_checked(&mut curl, &format!("download {}", artifact.filename));
        let actual = file_sha256(&partial);
        if actual != artifact.sha256 {
            die(&format!(
                "{} SHA-256 mismatch: expected {}, got {actual}",
                artifact.filename, artifact.sha256
            ));
        }
        fs::rename(partial, path).unwrap();
    }

    let guest_dist = artifact_root.join("rootfs/absl/bench-dist");
    fs::create_dir_all(&guest_dist).unwrap();
    for artifact in ABSL_DOWNLOADS {
        let source = dist.join(artifact.filename);
        let destination = guest_dist.join(artifact.filename);
        if !destination.is_file() || file_sha256(&destination) != artifact.sha256 {
            fs::copy(&source, &destination).unwrap();
        }
    }
}

fn ensure_getpid_guest(root: &Path, artifact_root: &Path) -> PathBuf {
    let source = root.join("benchmarks/gvisor-same-host/getpid-loop.c");
    let output = artifact_root.join("bin/getpid-loop");
    if output.is_file()
        && output.metadata().unwrap().modified().unwrap()
            >= source.metadata().unwrap().modified().unwrap()
    {
        return output;
    }
    fs::create_dir_all(output.parent().unwrap()).unwrap();
    let mut cc = Command::new(env::var("CC").unwrap_or_else(|_| "cc".to_owned()));
    cc.args(["-O2", "-Wall", "-Wextra", "-Werror"])
        .arg(&source)
        .arg("-o")
        .arg(&output);
    run_checked(&mut cc, "compile static getpid guest");
    output
}

fn workloads() -> Vec<Workload> {
    let image = |name| *IMAGES.iter().find(|image| image.name == name).unwrap();
    vec![
        Workload {
            name: "getpid",
            image: None,
            script: None,
            needs_network: false,
            expensive: false,
        },
        Workload {
            name: "redis",
            image: Some(image("redis")),
            script: Some(
                r#"set -euo pipefail
port=26379
redis-server --bind 127.0.0.1 --port "$port" --save '' --appendonly no --protected-mode no --daemonize yes --dir /tmp
trap 'redis-cli -h 127.0.0.1 -p "$port" shutdown nosave >/dev/null 2>&1 || true' EXIT
for i in $(seq 1 200); do redis-cli -h 127.0.0.1 -p "$port" ping 2>/dev/null | grep -qx PONG && break; sleep 0.05; done
redis-benchmark --csv -t set -h 127.0.0.1 -p "$port" -n 250000 -c 5
redis-cli -h 127.0.0.1 -p "$port" shutdown nosave >/dev/null
trap - EXIT"#,
            ),
            needs_network: false,
            expensive: false,
        },
        Workload {
            name: "ffmpeg",
            image: Some(image("ffmpeg")),
            script: Some(
                "set -euo pipefail; cd /media; rm -f /tmp/output.mp4; ffmpeg -nostdin -hide_banner -loglevel error -y -i video.mp4 -c:v libx264 -preset veryslow /tmp/output.mp4; stat -c 'OUTPUT_BYTES=%s' /tmp/output.mp4",
            ),
            needs_network: false,
            expensive: false,
        },
        Workload {
            name: "absl",
            image: Some(image("absl")),
            script: Some(
                "set -euo pipefail; cd /abseil-cpp; bazel --batch build --jobs=16 --loading_phase_threads=16 --enable_bzlmod=false --distdir=/bench-dist //...; echo BUILD_OK",
            ),
            needs_network: false,
            expensive: true,
        },
        Workload {
            name: "tensorflow",
            image: Some(image("tensorflow")),
            script: Some(
                r#"set -euo pipefail
export PYTHONPATH="${PYTHONPATH:-}:/TensorFlow-Examples/examples"
cd /TensorFlow-Examples/examples
for f in \
  2_BasicModels/gradient_boosted_decision_tree.py \
  2_BasicModels/kmeans.py \
  2_BasicModels/logistic_regression.py \
  2_BasicModels/nearest_neighbor.py \
  2_BasicModels/random_forest.py \
  3_NeuralNetworks/convolutional_network.py \
  3_NeuralNetworks/multilayer_perceptron.py \
  3_NeuralNetworks/neural_network.py; do
  python "$f"
done
echo TF_OK"#,
            ),
            needs_network: false,
            expensive: true,
        },
    ]
}

fn run_bounded(
    command: &mut Command,
    timeout: Duration,
    stdout_path: &Path,
    stderr_path: &Path,
) -> RunOutput {
    let stdout_file = File::create(stdout_path).unwrap();
    let stderr_file = File::create(stderr_path).unwrap();
    command
        .stdout(Stdio::from(stdout_file))
        .stderr(Stdio::from(stderr_file))
        .process_group(0);
    eprintln!("+ {}", command_text(command));
    let started = Instant::now();
    let mut child = command
        .spawn()
        .unwrap_or_else(|error| die(&error.to_string()));
    let status = loop {
        if let Some(status) = child.try_wait().unwrap() {
            break status;
        }
        if started.elapsed() >= timeout {
            unsafe {
                libc::kill(-(child.id() as i32), libc::SIGKILL);
            }
            let _ = child.wait();
            let timeout_status = Command::new("sh")
                .args(["-c", "exit 124"])
                .status()
                .unwrap();
            break timeout_status;
        }
        thread::sleep(Duration::from_millis(50));
    };
    let stdout = fs::read_to_string(stdout_path).unwrap_or_default();
    let stderr = fs::read_to_string(stderr_path).unwrap_or_default();
    RunOutput {
        status,
        elapsed: started.elapsed(),
        stdout,
        stderr,
    }
}

fn command_for(
    root: &Path,
    artifact_root: &Path,
    runsc: &Path,
    getpid: &Path,
    workload: &Workload,
    engine: &str,
    slug: &str,
) -> Command {
    if workload.name == "getpid" && engine == "native" {
        let mut command = Command::new(getpid);
        command.arg(GETPID_ITERATIONS.to_string());
        return command;
    }
    if engine == "native" {
        let image = workload.image.unwrap();
        let mut command = Command::new("podman");
        command.arg("run").arg("--rm");
        if !workload.needs_network {
            command.arg("--network=none");
        }
        if workload.name == "absl" {
            command.arg("--volume").arg(format!(
                "{}:/bench-dist:ro",
                artifact_root.join("downloads/absl").display()
            ));
        }
        for variable in [
            "http_proxy",
            "https_proxy",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "no_proxy",
            "NO_PROXY",
        ] {
            if env::var_os(variable).is_some() {
                command.arg("--env").arg(variable);
            }
        }
        command
            .args(["--entrypoint", "/bin/bash"])
            .arg(image.reference)
            .args(["-lc", workload.script.unwrap()]);
        return command;
    }

    let state = artifact_root.join("state").join(slug);
    fs::create_dir_all(&state).unwrap();
    let mut command = Command::new(runsc);
    command
        .arg(format!("--root={}", state.display()))
        .arg("--rootless")
        .arg(format!(
            "--network={}",
            if workload.needs_network {
                "sandbox"
            } else {
                "none"
            }
        ))
        .arg(format!("--platform={engine}"))
        .arg("do")
        .arg("--quiet");
    if workload.name == "getpid" {
        command
            .args(["--root", "/", "--"])
            .arg(getpid)
            .arg(GETPID_ITERATIONS.to_string());
    } else {
        let image = workload.image.unwrap();
        command
            .arg("--root")
            .arg(image_root(artifact_root, image))
            .args(["--", "/bin/bash", "-lc", workload.script.unwrap()]);
    }
    command.current_dir(root);
    command
}

fn parse_metric(workload: &Workload, output: &RunOutput) -> Result<(f64, &'static str), String> {
    if !output.status.success() {
        return Err(format!(
            "exit {}: {}",
            output.status,
            output
                .stderr
                .lines()
                .rev()
                .take(8)
                .collect::<Vec<_>>()
                .into_iter()
                .rev()
                .collect::<Vec<_>>()
                .join(" | ")
        ));
    }
    match workload.name {
        "getpid" => {
            if !output
                .stdout
                .contains(&format!("iterations={GETPID_ITERATIONS} checksum="))
            {
                return Err("missing getpid checksum marker".to_owned());
            }
            Ok((
                output.elapsed.as_secs_f64() * 1e9 / GETPID_ITERATIONS as f64,
                "ns/call",
            ))
        }
        "redis" => {
            let line = output
                .stdout
                .lines()
                .find(|line| line.contains("\"SET\""))
                .ok_or_else(|| "missing Redis SET CSV row".to_owned())?;
            let qps = line
                .split(',')
                .nth(1)
                .ok_or_else(|| "malformed Redis SET row".to_owned())?
                .trim_matches('"')
                .parse::<f64>()
                .map_err(|error| format!("parse Redis QPS: {error}"))?;
            if qps <= 0.0 {
                return Err("Redis reported nonpositive QPS".to_owned());
            }
            Ok((250_000_000.0 / qps, "ms"))
        }
        "ffmpeg" => {
            let bytes = output
                .stdout
                .lines()
                .find_map(|line| line.strip_prefix("OUTPUT_BYTES="))
                .ok_or_else(|| "missing ffmpeg output-size marker".to_owned())?
                .parse::<u64>()
                .map_err(|error| format!("parse ffmpeg output size: {error}"))?;
            if bytes < 1_000_000 {
                return Err(format!("ffmpeg output too small: {bytes}"));
            }
            Ok((output.elapsed.as_secs_f64() * 1000.0, "ms"))
        }
        "absl" => {
            if !output.stdout.contains("BUILD_OK") {
                return Err("missing ABSL build marker".to_owned());
            }
            Ok((output.elapsed.as_secs_f64() * 1000.0, "ms"))
        }
        "tensorflow" => {
            if !output.stdout.contains("TF_OK") {
                return Err("missing TensorFlow completion marker".to_owned());
            }
            Ok((output.elapsed.as_secs_f64() * 1000.0, "ms"))
        }
        _ => Err("unknown workload".to_owned()),
    }
}

fn median(values: &mut [f64]) -> f64 {
    values.sort_by(f64::total_cmp);
    if values.len() % 2 == 1 {
        values[values.len() / 2]
    } else {
        (values[values.len() / 2 - 1] + values[values.len() / 2]) / 2.0
    }
}

fn tsv_to_markdown(path: &Path, title: &str) -> String {
    let Ok(text) = fs::read_to_string(path) else {
        return format!(
            "## {title}\n\nReference input missing: `{}`.\n\n",
            path.display()
        );
    };
    let mut lines = text.lines();
    let Some(header) = lines.next() else {
        return String::new();
    };
    let columns: Vec<&str> = header.split('\t').collect();
    let retained: Vec<usize> = columns
        .iter()
        .enumerate()
        .filter(|(_, column)| !column.to_ascii_lowercase().contains("blog"))
        .map(|(index, _)| index)
        .collect();
    let visible_columns: Vec<&str> = retained.iter().map(|index| columns[*index]).collect();
    let mut output = format!(
        "## {title}\n\n| {} |\n| {} |\n",
        visible_columns.join(" | "),
        visible_columns
            .iter()
            .map(|_| "---")
            .collect::<Vec<_>>()
            .join(" | ")
    );
    for line in lines {
        let fields: Vec<&str> = line.split('\t').collect();
        output.push_str(&format!(
            "| {} |\n",
            retained
                .iter()
                .filter_map(|index| fields.get(*index).copied())
                .collect::<Vec<_>>()
                .join(" | ")
        ));
    }
    output.push('\n');
    output
}

fn write_results(
    root: &Path,
    output: &Path,
    run_id: &str,
    args: &Args,
    runsc: &Path,
    samples: &[Sample],
    start_load: (f64, f64, f64),
) {
    fs::create_dir_all(output).unwrap();
    let mut raw = File::create(output.join("samples.tsv")).unwrap();
    writeln!(raw, "workload\tengine\trepetition\telapsed_ms\tmetric_value\tmetric_unit\tstdout_sha256\tstatus\treason").unwrap();
    for sample in samples {
        let reason = sample.reason.replace(['\t', '\n'], " ");
        writeln!(
            raw,
            "{}\t{}\t{}\t{:.3}\t{:.6}\t{}\t{}\t{}\t{}",
            sample.workload,
            sample.engine,
            sample.repetition,
            sample.elapsed_ms,
            sample.metric_value,
            sample.metric_unit,
            sample.stdout_sha256,
            sample.status,
            if reason.is_empty() { "-" } else { &reason }
        )
        .unwrap();
    }

    let mut groups: BTreeMap<(String, String, String), Vec<f64>> = BTreeMap::new();
    for sample in samples.iter().filter(|sample| sample.status == "ok") {
        groups
            .entry((
                sample.workload.clone(),
                sample.engine.clone(),
                sample.metric_unit.clone(),
            ))
            .or_default()
            .push(sample.metric_value);
    }
    let mut medians: BTreeMap<(String, String), (f64, String, usize)> = BTreeMap::new();
    for ((workload, engine, unit), mut values) in groups {
        let count = values.len();
        medians.insert((workload, engine), (median(&mut values), unit, count));
    }
    let mut summary = File::create(output.join("summary.tsv")).unwrap();
    writeln!(
        summary,
        "workload\tengine\trepetitions\tmedian\tunit\tratio_vs_native"
    )
    .unwrap();
    for ((workload, engine), (value, unit, count)) in &medians {
        let native = medians
            .get(&(workload.clone(), "native".to_owned()))
            .map(|row| row.0);
        let ratio = native.map(|native| *value / native);
        writeln!(
            summary,
            "{workload}\t{engine}\t{count}\t{value:.6}\t{unit}\t{}",
            ratio
                .map(|ratio| format!("{ratio:.6}"))
                .unwrap_or_else(|| "NA".to_owned())
        )
        .unwrap();
    }

    let reference_metadata =
        root.join("experiments/gvisor-systrap-benchmark-repro-20260802/metadata.json");
    let reference_host = fs::read_to_string(&reference_metadata)
        .ok()
        .and_then(|text| serde_json::from_str::<serde_json::Value>(&text).ok())
        .and_then(|value| value["host"].as_str().map(short_hostname));
    let current_host = hostname();
    let same_reference_host = reference_host.as_deref() == Some(current_host.as_str());

    let metadata = json!({
        "schema": 1,
        "run_id": run_id,
        "repository_sha": repository_sha(root),
        "host": current_host,
        "kernel": String::from_utf8_lossy(&Command::new("uname").arg("-r").output().unwrap().stdout).trim(),
        "start_load_average": [start_load.0, start_load.1, start_load.2],
        "end_load_average": fs::read_to_string("/proc/loadavg").unwrap_or_default(),
        "runsc_release": RUNSC_RELEASE,
        "runsc_url": RUNSC_URL,
        "runsc_sha512": RUNSC_SHA512,
        "runsc_version": run_checked(Command::new(runsc).arg("--version"), "runsc version"),
        "script_sha256": file_sha256(&root.join("benchmarks/gvisor-same-host/run.rs")),
        "getpid_source_sha256": file_sha256(&root.join("benchmarks/gvisor-same-host/getpid-loop.c")),
        "images": IMAGES.iter().map(|image| (image.name, image.reference)).collect::<BTreeMap<_, _>>(),
        "platforms": args.platforms,
        "workloads": args.workloads,
        "repetitions": args.repetitions,
        "warmups": args.warmups,
        "expensive_repetitions": args.expensive_repetitions,
        "expensive_warmups": args.expensive_warmups,
        "timeout_seconds": args.timeout.as_secs(),
        "existing_backend_reference_host": reference_host,
        "existing_backend_reference_is_same_host": same_reference_host,
    });
    fs::write(
        output.join("metadata.json"),
        serde_json::to_string_pretty(&metadata).unwrap() + "\n",
    )
    .unwrap();

    let mut report = format!(
        "# Same-host gVisor runsc results\n\nRun `{run_id}` on `{}` with runsc `{RUNSC_RELEASE}`. Blog numbers are not used for ranking.\n\n| Workload | Engine | Median | Unit | Native ratio | Samples |\n| --- | --- | ---: | --- | ---: | ---: |\n",
        hostname()
    );
    for ((workload, engine), (value, unit, count)) in &medians {
        let native = medians
            .get(&(workload.clone(), "native".to_owned()))
            .map(|row| row.0);
        let ratio = native.map(|native| *value / native);
        report.push_str(&format!(
            "| {workload} | {engine} | {value:.3} | {unit} | {} | {count} |\n",
            ratio
                .map(|ratio| format!("{ratio:.3}x"))
                .unwrap_or_else(|| "n/a".to_owned())
        ));
    }
    report.push_str(
        "\nFailed cells remain in `samples.tsv`; they are never treated as fast samples.\n\n",
    );
    if same_reference_host {
        report.push_str("The following Hermit/Reverie reference inputs were measured on the same host and are included for comparison. They were collected earlier, so host load and time still differ.\n\n");
        report.push_str(&tsv_to_markdown(
            &root
                .join("experiments/gvisor-systrap-benchmark-repro-20260802/raw/getpid-summary.tsv"),
            "Hermit/Reverie getpid reference",
        ));
        report.push_str(&tsv_to_markdown(
            &root.join("experiments/gvisor-systrap-benchmark-repro-20260802/raw/redis-medians.tsv"),
            "Hermit/Reverie Redis reference",
        ));
        report.push_str(&tsv_to_markdown(
            &root.join(
                "experiments/gvisor-systrap-benchmark-repro-20260802/raw/application-summary.tsv",
            ),
            "Hermit/Reverie application reference",
        ));
    } else {
        report.push_str("Hermit/Reverie reference data was not included because its host identity differs from this run. Re-run those backends locally before comparing.\n");
    }
    fs::write(output.join("REPORT.md"), report).unwrap();
}

fn main() {
    let args = parse_args();
    let root = repository_root();
    let artifact_root = root.join("ignored/gvisor-runsc-same-host");
    fs::create_dir_all(&artifact_root).unwrap();

    let runsc = if args.skip_provision {
        artifact_root
            .join("bin")
            .join(format!("runsc-release-{RUNSC_RELEASE}"))
    } else {
        ensure_runsc(&artifact_root)
    };
    if !runsc.is_file() || file_sha512(&runsc) != RUNSC_SHA512 {
        die("runsc is missing or does not match the pinned SHA-512");
    }
    if !args.skip_provision {
        for image in IMAGES {
            ensure_image_root(&artifact_root, image);
        }
    }
    ensure_absl_dist(&artifact_root, !args.skip_provision);
    let getpid = ensure_getpid_guest(&root, &artifact_root);
    if args.provision_only {
        println!(
            "Provisioned runsc and benchmark roots under {}",
            artifact_root.display()
        );
        return;
    }

    let run_id = timestamp();
    let output = args.output.clone().unwrap_or_else(|| {
        root.join("benchmarks/gvisor-same-host/results")
            .join(&run_id)
    });
    if output.exists() {
        die(&format!("output already exists: {}", output.display()));
    }
    fs::create_dir_all(&output).unwrap();
    let raw_logs = artifact_root.join("logs").join(&run_id);
    fs::create_dir_all(&raw_logs).unwrap();
    let start_load = {
        let text = fs::read_to_string("/proc/loadavg").unwrap();
        let mut fields = text
            .split_whitespace()
            .take(3)
            .map(|value| value.parse::<f64>().unwrap());
        (
            fields.next().unwrap(),
            fields.next().unwrap(),
            fields.next().unwrap(),
        )
    };

    let selected: Vec<Workload> = workloads()
        .into_iter()
        .filter(|workload| args.workloads.iter().any(|name| name == workload.name))
        .collect();
    let mut samples = Vec::new();
    for workload in &selected {
        let warmups = if workload.expensive {
            args.expensive_warmups
        } else {
            args.warmups
        };
        let repetitions = if workload.expensive {
            args.expensive_repetitions
        } else {
            args.repetitions
        };
        let mut engines = vec!["native".to_owned()];
        engines.extend(args.platforms.iter().cloned());
        for phase in 0..(warmups + repetitions) {
            let measured = phase >= warmups;
            let repetition = if measured { phase - warmups + 1 } else { 0 };
            let offset = phase % engines.len();
            let order: Vec<String> = engines
                .iter()
                .cycle()
                .skip(offset)
                .take(engines.len())
                .cloned()
                .collect();
            for engine in order {
                let kind = if measured {
                    format!("rep{repetition}")
                } else {
                    format!("warmup{}", phase + 1)
                };
                let slug = format!("{}-{}-{}", workload.name, engine, kind);
                let stdout_path = raw_logs.join(format!("{slug}.stdout"));
                let stderr_path = raw_logs.join(format!("{slug}.stderr"));
                let mut command = command_for(
                    &root,
                    &artifact_root,
                    &runsc,
                    &getpid,
                    workload,
                    &engine,
                    &format!("{run_id}-{slug}"),
                );
                let result = run_bounded(&mut command, args.timeout, &stdout_path, &stderr_path);
                let metric = parse_metric(workload, &result);
                println!(
                    "{} {} {}: {:.3}s {}",
                    workload.name,
                    engine,
                    kind,
                    result.elapsed.as_secs_f64(),
                    metric
                        .as_ref()
                        .map(|(value, unit)| format!("{value:.3} {unit}"))
                        .unwrap_or_else(|reason| format!("FAIL {reason}"))
                );
                if measured {
                    let (metric_value, metric_unit, status, reason) = match metric {
                        Ok((value, unit)) => {
                            (value, unit.to_owned(), "ok".to_owned(), String::new())
                        }
                        Err(reason) => (0.0, "n/a".to_owned(), "failed".to_owned(), reason),
                    };
                    samples.push(Sample {
                        workload: workload.name.to_owned(),
                        engine: engine.clone(),
                        repetition,
                        elapsed_ms: result.elapsed.as_secs_f64() * 1000.0,
                        metric_value,
                        metric_unit,
                        stdout_sha256: hex_digest(Sha256::digest(result.stdout.as_bytes())),
                        status,
                        reason,
                    });
                } else if metric.is_err() {
                    eprintln!(
                        "warmup failed; measured samples will retain the failure if it repeats"
                    );
                }
            }
        }
    }
    write_results(&root, &output, &run_id, &args, &runsc, &samples, start_load);
    println!("Results: {}", output.display());
}
