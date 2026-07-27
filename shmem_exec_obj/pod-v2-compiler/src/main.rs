use object::{
    Object, ObjectSection, ObjectSymbol, RelocationEncoding, RelocationKind, RelocationTarget,
    SectionFlags, SymbolKind,
};
use pod_v2_api::{
    FLAG_OFFSET_ARENA, HEADER_SIZE, ImageHeader, METHOD_SPECS, MethodEntry, TARGET_ARCH_X86_64,
};
use sha2::{Digest, Sha256};
use std::env;
use std::error::Error;
use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::{collections::BTreeSet, ffi::OsStr};

#[derive(Debug)]
struct Options {
    source: PathBuf,
    sdk_manifest: PathBuf,
    sdk_source: PathBuf,
    sdk_rlib: PathBuf,
    linker_script: PathBuf,
    output: PathBuf,
    object: PathBuf,
    elf: PathBuf,
    manifest: PathBuf,
    rustc: String,
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct BuildInput {
    path: PathBuf,
    digest: [u8; 32],
}

#[derive(Debug)]
struct SdkMetadata {
    root: PathBuf,
    package: String,
    version: String,
    edition: String,
    manifest: BuildInput,
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct DependencySnapshot {
    inputs: Vec<BuildInput>,
    inputs_digest: [u8; 32],
}

const COMMON_CODEGEN_ARGS: &[&str] = &[
    "-Copt-level=3",
    "-Cpanic=abort",
    "-Crelocation-model=pic",
    "-Ccode-model=small",
    "-Ccodegen-units=1",
    "-Coverflow-checks=no",
    "-Cdebug-assertions=no",
    "-Cforce-unwind-tables=no",
    "-Ctarget-cpu=x86-64",
    "-Cembed-bitcode=no",
];

fn main() {
    if let Err(error) = run() {
        eprintln!("pod-v2-compiler: {error}");
        std::process::exit(1);
    }
}

fn run() -> Result<(), Box<dyn Error>> {
    let options = parse_options()?;
    for path in [
        &options.output,
        &options.object,
        &options.elf,
        &options.manifest,
        &options.sdk_rlib,
    ] {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
    }

    let sdk = inspect_sdk(&options)?;
    let rust_lld = rust_lld_path(&options.rustc)?;
    let rustc_binary = rustc_binary_path(&options.rustc)?;
    let rustc_launcher_input = hash_file(&resolve_executable(&options.rustc)?)?;
    let rustc_input = hash_file(&rustc_binary)?;
    let rust_lld_input = hash_file(&rust_lld)?;
    let linker_script_input = hash_file(&options.linker_script)?;

    let sdk_probe_dep_info = sidecar_path(&options.sdk_rlib, "probe.d")?;
    let sdk_dep_info = sidecar_path(&options.sdk_rlib, "d")?;
    emit_sdk_dep_info(&options, &sdk, &sdk_probe_dep_info)?;
    let sdk_dependencies = read_dependency_snapshot(&sdk_probe_dep_info)?;
    let sdk_source_input =
        required_dependency(&sdk_dependencies, &options.sdk_source, "SDK crate root")?;
    compile_sdk(&options, &sdk, &sdk_dep_info)?;
    verify_dependency_snapshot(&sdk_dependencies, &sdk_dep_info, "SDK")?;
    let sdk_rlib_input = hash_file(&options.sdk_rlib)?;

    let pod_probe_dep_info = sidecar_path(&options.object, "probe.d")?;
    let pod_dep_info = sidecar_path(&options.object, "d")?;
    emit_pod_dep_info(&options, &pod_probe_dep_info)?;
    let pod_dependencies = read_dependency_snapshot(&pod_probe_dep_info)?;
    let pod_source_input =
        required_dependency(&pod_dependencies, &options.source, "pod crate root")?;
    compile_object(&options, &pod_dep_info)?;
    verify_dependency_snapshot(&pod_dependencies, &pod_dep_info, "pod")?;
    verify_sdk_unchanged(&options, &sdk)?;
    let object_input = hash_file(&options.object)?;
    let input_bytes = fs::read(&options.object)?;
    let input = object::File::parse(input_bytes.as_slice())?;
    audit_input_object(&input)?;

    link_closure(&options, &rust_lld)?;
    verify_dependency_files(&sdk_dependencies, "SDK")?;
    verify_dependency_files(&pod_dependencies, "pod")?;
    verify_file_unchanged(&sdk.manifest, "SDK manifest")?;
    verify_file_unchanged(&linker_script_input, "linker script")?;
    verify_file_unchanged(&rustc_launcher_input, "rustc launcher")?;
    verify_file_unchanged(&rustc_input, "rustc binary")?;
    verify_file_unchanged(&rust_lld_input, "rust-lld binary")?;
    verify_file_unchanged(&sdk_rlib_input, "SDK rlib")?;
    verify_file_unchanged(&object_input, "pod object")?;
    let elf_bytes = fs::read(&options.elf)?;
    let elf = object::File::parse(elf_bytes.as_slice())?;
    let (pod_index, pod_bytes) = audit_linked_elf(&elf)?;

    let code_hash: [u8; 32] = Sha256::digest(pod_bytes).into();
    let mut header = ImageHeader::new(pod_bytes.len(), code_hash)?;
    header.flags = FLAG_OFFSET_ARENA;
    let pod_section = elf.section_by_index(pod_index)?;
    for (index, (name, signature)) in METHOD_SPECS.iter().enumerate() {
        let symbol = elf
            .symbols()
            .find(|symbol| symbol.name().ok() == Some(*name))
            .ok_or_else(|| format!("required method symbol {name:?} is absent"))?;
        if symbol.kind() != SymbolKind::Text
            || !symbol.is_global()
            || symbol.size() == 0
            || symbol.section_index() != Some(pod_index)
        {
            return Err(format!("method {name:?} is not global text in .pod").into());
        }
        let relative = symbol
            .address()
            .checked_sub(pod_section.address())
            .ok_or_else(|| format!("method {name:?} address underflow"))?;
        let size =
            u32::try_from(symbol.size()).map_err(|_| format!("method {name:?} is too large"))?;
        header.methods[index] = MethodEntry {
            offset: HEADER_SIZE as u64 + relative,
            size,
            signature: *signature as u16,
            reserved: 0,
        };
    }
    let encoded = header.encode()?;
    let mut image = Vec::with_capacity(encoded.len() + pod_bytes.len());
    image.extend_from_slice(&encoded);
    image.extend_from_slice(pod_bytes);
    if image.len() as u64 != header.image_len {
        return Err("constructed image length does not match header".into());
    }
    fs::write(&options.output, &image)?;

    let image_hash: [u8; 32] = Sha256::digest(&image).into();
    let elf_hash: [u8; 32] = Sha256::digest(&elf_bytes).into();
    let sdk_dep_info_input = hash_file(&sdk_dep_info)?;
    let pod_dep_info_input = hash_file(&pod_dep_info)?;
    let rustc_version = command_output(Command::new(&options.rustc).arg("-vV"), "rustc -vV")?;
    let mut manifest = format!(
        "format=reverie-pod-v2\nprovenance.scope=rustc-dep-info-plus-explicit-link-inputs\nsource={}\nsource_sha256={}\nobject={}\nobject_sha256={}\nelf={}\nelf_sha256={}\nimage={}\nimage_len={}\ncode_len={}\ncode_sha256={}\nartifact_sha256={}\nstate_file_len={}\npayload_len={}\nlinker_script={}\nlinker_script_sha256={}\nrustc_invocation={}\nrustc_launcher={}\nrustc_launcher_sha256={}\nrustc_binary={}\nrustc_binary_sha256={}\nrust_lld={}\nrust_lld_sha256={}\nsdk.package={}\nsdk.version={}\nsdk.edition={}\nsdk.root={}\nsdk.manifest={}\nsdk.manifest_sha256={}\nsdk.crate_root={}\nsdk.crate_root_sha256={}\nsdk.rlib={}\nsdk.rlib_sha256={}\nsdk.default_features=false\nsdk.features=none\nsdk.dep_info={}\nsdk.dep_info_sha256={}\nsdk.dependencies_sha256={}\npod.dep_info={}\npod.dep_info_sha256={}\npod.dependencies_sha256={}\nsdk.rustc_args={}\npod.rustc_args={}\nlink.inputs={},{},{}\n",
        pod_source_input.path.display(),
        hex(&pod_source_input.digest),
        options.object.display(),
        hex(&object_input.digest),
        options.elf.display(),
        hex(&elf_hash),
        options.output.display(),
        image.len(),
        pod_bytes.len(),
        hex(&code_hash),
        hex(&image_hash),
        header.state_file_len,
        header.payload_len,
        linker_script_input.path.display(),
        hex(&linker_script_input.digest),
        options.rustc,
        rustc_launcher_input.path.display(),
        hex(&rustc_launcher_input.digest),
        rustc_input.path.display(),
        hex(&rustc_input.digest),
        rust_lld_input.path.display(),
        hex(&rust_lld_input.digest),
        sdk.package,
        sdk.version,
        sdk.edition,
        sdk.root.display(),
        sdk.manifest.path.display(),
        hex(&sdk.manifest.digest),
        sdk_source_input.path.display(),
        hex(&sdk_source_input.digest),
        options.sdk_rlib.display(),
        hex(&sdk_rlib_input.digest),
        sdk_dep_info_input.path.display(),
        hex(&sdk_dep_info_input.digest),
        hex(&sdk_dependencies.inputs_digest),
        pod_dep_info_input.path.display(),
        hex(&pod_dep_info_input.digest),
        hex(&pod_dependencies.inputs_digest),
        sdk_rustc_args(&sdk),
        pod_rustc_args(&options),
        options.object.display(),
        options.sdk_rlib.display(),
        options.linker_script.display(),
    );
    append_build_inputs(&mut manifest, "sdk.dependency", &sdk_dependencies.inputs);
    append_build_inputs(&mut manifest, "pod.dependency", &pod_dependencies.inputs);
    for (index, (name, signature)) in METHOD_SPECS.iter().enumerate() {
        let method = header.methods[index];
        manifest.push_str(&format!(
            "method.{index}.name={name}\nmethod.{index}.offset=0x{:x}\nmethod.{index}.size={}\nmethod.{index}.signature={:?}\n",
            method.offset, method.size, signature
        ));
    }
    manifest.push_str("rustc:\n");
    manifest.push_str(&rustc_version);
    fs::write(&options.manifest, manifest)?;

    println!(
        "emitted {}-byte V2 image ({} linked code bytes, sha256 {})",
        image.len(),
        pod_bytes.len(),
        hex(&image_hash)
    );
    Ok(())
}

fn compile_sdk(
    options: &Options,
    sdk: &SdkMetadata,
    dep_info: &Path,
) -> Result<(), Box<dyn Error>> {
    let emit = format!(
        "link={},dep-info={}",
        options.sdk_rlib.display(),
        dep_info.display()
    );
    let mut command = sdk_rustc_command(options, sdk);
    command.args(["--emit", &emit]);
    run_rustc(&mut command, "compiling no-default-feature SDK")?;
    if !options.sdk_rlib.is_file() {
        return Err(format!(
            "rustc did not emit SDK rlib at {}",
            options.sdk_rlib.display()
        )
        .into());
    }
    Ok(())
}

fn emit_sdk_dep_info(
    options: &Options,
    sdk: &SdkMetadata,
    dep_info: &Path,
) -> Result<(), Box<dyn Error>> {
    let emit = format!("dep-info={}", dep_info.display());
    let mut command = sdk_rustc_command(options, sdk);
    command.args(["--emit", &emit]);
    run_rustc(&mut command, "discovering SDK dependencies")
}

fn sdk_rustc_command(options: &Options, sdk: &SdkMetadata) -> Command {
    let mut command = Command::new(&options.rustc);
    command
        .arg(&options.sdk_source)
        .args(["--crate-name", "shmem_pod"])
        .arg(format!("--edition={}", sdk.edition))
        .arg("--crate-type=rlib")
        .args(COMMON_CODEGEN_ARGS);
    command
}

fn compile_object(options: &Options, dep_info: &Path) -> Result<(), Box<dyn Error>> {
    let emit = format!(
        "obj={},dep-info={}",
        options.object.display(),
        dep_info.display()
    );
    let mut command = pod_rustc_command(options);
    command.args(["--emit", &emit]);
    run_rustc(&mut command, "compiling pod object")
}

fn emit_pod_dep_info(options: &Options, dep_info: &Path) -> Result<(), Box<dyn Error>> {
    let emit = format!("dep-info={}", dep_info.display());
    let mut command = pod_rustc_command(options);
    command.args(["--emit", &emit]);
    run_rustc(&mut command, "discovering pod dependencies")
}

fn pod_rustc_command(options: &Options) -> Command {
    let sdk_extern = format!("shmem_pod={}", options.sdk_rlib.display());
    let mut command = Command::new(&options.rustc);
    command
        .arg(&options.source)
        .args([
            "--crate-name",
            "reverie_pod_v2_code",
            "--edition=2024",
            "--crate-type=lib",
        ])
        .args(["--extern", &sdk_extern])
        .args(COMMON_CODEGEN_ARGS);
    command
}

fn run_rustc(command: &mut Command, operation: &str) -> Result<(), Box<dyn Error>> {
    let output = command.output()?;
    if !output.status.success() {
        return Err(format!(
            "rustc failed while {operation}:\n{}{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        )
        .into());
    }
    Ok(())
}

fn audit_input_object(object: &object::File<'_>) -> Result<(), Box<dyn Error>> {
    if object.format() != object::BinaryFormat::Elf
        || object.kind() != object::ObjectKind::Relocatable
        || object.architecture() != object::Architecture::X86_64
    {
        return Err("rustc did not emit a relocatable x86-64 ELF object".into());
    }
    for section in object.sections() {
        for (offset, relocation) in section.relocations() {
            if relocation.kind() == RelocationKind::Absolute {
                return Err(format!(
                    "input contains forbidden absolute relocation in {:?} at 0x{offset:x}",
                    section.name().unwrap_or("<unnamed>")
                )
                .into());
            }
        }
    }
    Ok(())
}

fn link_closure(options: &Options, rust_lld: &Path) -> Result<(), Box<dyn Error>> {
    let output = Command::new(rust_lld)
        .args([
            "-flavor",
            "gnu",
            "-static",
            "--gc-sections",
            "--no-undefined",
        ])
        .args(["--emit-relocs", "--build-id=none", "-z", "noexecstack"])
        .arg("--fatal-warnings")
        .arg("-T")
        .arg(&options.linker_script)
        .arg("-o")
        .arg(&options.elf)
        .arg(&options.object)
        .arg(&options.sdk_rlib)
        .output()?;
    if !output.status.success() {
        return Err(format!(
            "rust-lld failed:\n{}{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        )
        .into());
    }
    Ok(())
}

fn audit_linked_elf<'a>(
    elf: &'a object::File<'a>,
) -> Result<(object::SectionIndex, &'a [u8]), Box<dyn Error>> {
    if elf.format() != object::BinaryFormat::Elf
        || elf.kind() != object::ObjectKind::Executable
        || elf.architecture() != object::Architecture::X86_64
    {
        return Err("linker did not emit an executable x86-64 ELF".into());
    }
    let pod = elf
        .section_by_name(".pod")
        .ok_or("linked ELF has no .pod section")?;
    let pod_index = pod.index();
    if pod.address() != 0 || pod.size() == 0 {
        return Err(".pod must be nonempty and linked at VMA zero".into());
    }

    for section in elf.sections() {
        if let SectionFlags::Elf { sh_flags } = section.flags() {
            let alloc = sh_flags & object::elf::SHF_ALLOC as u64 != 0;
            let writable = sh_flags & object::elf::SHF_WRITE as u64 != 0;
            let executable = sh_flags & object::elf::SHF_EXECINSTR as u64 != 0;
            if alloc
                && section.size() != 0
                && (section.index() != pod_index || writable || !executable)
            {
                return Err(format!(
                    "forbidden allocated section {:?}: write={writable} exec={executable}",
                    section.name().unwrap_or("<unnamed>")
                )
                .into());
            }
        }
    }
    for symbol in elf.symbols() {
        if symbol.is_undefined() && !symbol.is_weak() {
            return Err(format!(
                "linked pod retains undefined symbol {:?}",
                symbol.name().unwrap_or("<unnamed>")
            )
            .into());
        }
    }
    for (offset, relocation) in pod.relocations() {
        if !matches!(
            relocation.kind(),
            RelocationKind::Relative | RelocationKind::PltRelative
        ) || relocation.size() != 32
        {
            return Err(format!(
                "forbidden applied relocation {:?}/{} at .pod+0x{offset:x}",
                relocation.kind(),
                relocation.size()
            )
            .into());
        }
        if !matches!(
            relocation.encoding(),
            RelocationEncoding::Generic
                | RelocationEncoding::X86Branch
                | RelocationEncoding::X86RipRelative
        ) {
            return Err(format!(
                "forbidden relocation encoding {:?} at .pod+0x{offset:x}",
                relocation.encoding()
            )
            .into());
        }
        validate_pod_relocation(elf, &pod, pod_index, offset, &relocation)?;
    }
    Ok((pod_index, pod.data()?))
}

fn validate_pod_relocation(
    elf: &object::File<'_>,
    pod: &object::Section<'_, '_>,
    pod_index: object::SectionIndex,
    offset: u64,
    relocation: &object::Relocation,
) -> Result<(), Box<dyn Error>> {
    if relocation.has_implicit_addend() {
        return Err(
            format!("relocation at .pod+0x{offset:x} has an unsupported implicit addend").into(),
        );
    }
    let (target_address, target_is_pod) =
        relocation_target_address(elf, pod_index, relocation.target())?;
    if !target_is_pod {
        return Err(
            format!("relocation at .pod+0x{offset:x} targets outside immutable pod code").into(),
        );
    }
    validate_effective_reference(
        pod.address(),
        pod.size(),
        offset,
        target_address,
        relocation.addend(),
    )
    .map_err(|error| format!("relocation at .pod+0x{offset:x}: {error}").into())
}

fn relocation_target_address(
    elf: &object::File<'_>,
    pod_index: object::SectionIndex,
    target: RelocationTarget,
) -> Result<(u64, bool), Box<dyn Error>> {
    Ok(match target {
        RelocationTarget::Symbol(index) => {
            let symbol = elf.symbol_by_index(index)?;
            (symbol.address(), symbol.section_index() == Some(pod_index))
        }
        RelocationTarget::Section(index) => {
            (elf.section_by_index(index)?.address(), index == pod_index)
        }
        _ => return Err("relocation has an unsupported target kind".into()),
    })
}

fn validate_effective_reference(
    pod_address: u64,
    pod_size: u64,
    offset: u64,
    target_address: u64,
    addend: i64,
) -> Result<(), String> {
    let relocation_end = offset
        .checked_add(4)
        .ok_or_else(|| "relocation storage extent overflow".to_owned())?;
    if relocation_end > pod_size {
        return Err("relocation storage lies outside .pod".to_owned());
    }
    let pod_end = pod_address
        .checked_add(pod_size)
        .ok_or_else(|| ".pod address extent overflow".to_owned())?;
    let place = pod_address
        .checked_add(offset)
        .ok_or_else(|| "relocation place address overflow".to_owned())?;

    // object defines Relative as S + A - P. x86_64 executes a 32-bit PC-relative
    // reference from RIP=P+4, so its actual target is S+A+4.
    let relocation_width = 4_i128;
    let effective = i128::from(target_address) + i128::from(addend) + relocation_width;
    if effective < 0 || effective > i128::from(u64::MAX) {
        return Err("effective target address overflow".to_owned());
    }
    let effective = effective as u64;
    if effective < pod_address || effective >= pod_end {
        return Err(format!(
            "effective target 0x{effective:x} is outside .pod [0x{pod_address:x}, 0x{pod_end:x})"
        ));
    }

    let place_after = i128::from(place) + relocation_width;
    let displacement = i128::from(effective) - place_after;
    if displacement < i128::from(i32::MIN) || displacement > i128::from(i32::MAX) {
        return Err("PC-relative displacement does not fit signed 32 bits".to_owned());
    }
    Ok(())
}

fn rust_lld_path(rustc: &str) -> Result<PathBuf, Box<dyn Error>> {
    let sysroot = command_output(
        Command::new(rustc).args(["--print", "sysroot"]),
        "rustc sysroot",
    )?;
    let path =
        PathBuf::from(sysroot.trim()).join("lib/rustlib/x86_64-unknown-linux-gnu/bin/rust-lld");
    if !path.is_file() {
        return Err(format!("rust-lld is absent at {}", path.display()).into());
    }
    Ok(path)
}

fn rustc_binary_path(rustc: &str) -> Result<PathBuf, Box<dyn Error>> {
    let sysroot = command_output(
        Command::new(rustc).args(["--print", "sysroot"]),
        "rustc sysroot",
    )?;
    let path = PathBuf::from(sysroot.trim()).join("bin/rustc");
    if !path.is_file() {
        return Err(format!("selected rustc binary is absent at {}", path.display()).into());
    }
    Ok(path.canonicalize()?)
}

fn resolve_executable(command: &str) -> Result<PathBuf, Box<dyn Error>> {
    let path = Path::new(command);
    if path.components().count() > 1 {
        return Ok(path.canonicalize()?);
    }
    let search = env::var_os("PATH").ok_or("PATH is not set")?;
    for directory in env::split_paths(&search) {
        let candidate = directory.join(command);
        if candidate.is_file() {
            return Ok(candidate.canonicalize()?);
        }
    }
    Err(format!("cannot resolve executable {command:?} through PATH").into())
}

fn sidecar_path(path: &Path, suffix: &str) -> Result<PathBuf, Box<dyn Error>> {
    let mut name = path
        .file_name()
        .ok_or_else(|| format!("path {} has no file name", path.display()))?
        .to_os_string();
    name.push(OsStr::new("."));
    name.push(OsStr::new(suffix));
    Ok(path.with_file_name(name))
}

fn hash_file(path: &Path) -> Result<BuildInput, Box<dyn Error>> {
    let path = path.canonicalize()?;
    if !path.is_file() {
        return Err(format!("build input {} is not a regular file", path.display()).into());
    }
    Ok(BuildInput {
        digest: Sha256::digest(fs::read(&path)?).into(),
        path,
    })
}

fn read_dependency_snapshot(path: &Path) -> Result<DependencySnapshot, Box<dyn Error>> {
    let text = fs::read_to_string(path)?;
    let parsed = depfile::parse(&text).map_err(|offset| {
        format!(
            "failed to parse rustc dep-info {} at byte {offset}",
            path.display()
        )
    })?;
    let current = env::current_dir()?;
    let mut paths = BTreeSet::new();
    for (_, dependencies) in parsed.iter() {
        for dependency in dependencies {
            let dependency = PathBuf::from(dependency.as_ref());
            let dependency = if dependency.is_absolute() {
                dependency
            } else {
                current.join(dependency)
            };
            paths.insert(dependency.canonicalize()?);
        }
    }
    if paths.is_empty() {
        return Err(format!("rustc dep-info {} has no dependencies", path.display()).into());
    }
    let inputs = paths
        .iter()
        .map(|dependency| hash_file(dependency))
        .collect::<Result<Vec<_>, _>>()?;
    Ok(DependencySnapshot {
        inputs_digest: aggregate_input_digest(&inputs),
        inputs,
    })
}

fn aggregate_input_digest(inputs: &[BuildInput]) -> [u8; 32] {
    let mut aggregate = Sha256::new();
    for input in inputs {
        let name = input.path.to_string_lossy();
        aggregate.update((name.len() as u64).to_le_bytes());
        aggregate.update(name.as_bytes());
        aggregate.update(input.digest);
    }
    aggregate.finalize().into()
}

fn required_dependency<'a>(
    snapshot: &'a DependencySnapshot,
    expected: &Path,
    label: &str,
) -> Result<&'a BuildInput, Box<dyn Error>> {
    let expected = expected.canonicalize()?;
    snapshot
        .inputs
        .iter()
        .find(|input| input.path == expected)
        .ok_or_else(|| {
            format!(
                "rustc dep-info does not include {label} {}",
                expected.display()
            )
            .into()
        })
}

fn verify_dependency_snapshot(
    expected: &DependencySnapshot,
    dep_info: &Path,
    label: &str,
) -> Result<(), Box<dyn Error>> {
    let actual = read_dependency_snapshot(dep_info)?;
    if &actual != expected {
        return Err(format!(
            "{label} dependency closure changed while the executable image was being compiled"
        )
        .into());
    }
    Ok(())
}

fn verify_dependency_files(
    snapshot: &DependencySnapshot,
    label: &str,
) -> Result<(), Box<dyn Error>> {
    for input in &snapshot.inputs {
        verify_file_unchanged(input, &format!("{label} dependency"))?;
    }
    Ok(())
}

fn verify_file_unchanged(input: &BuildInput, label: &str) -> Result<(), Box<dyn Error>> {
    if hash_file(&input.path)? != *input {
        return Err(format!("{label} {} changed during build", input.path.display()).into());
    }
    Ok(())
}

fn command_output(command: &mut Command, label: &str) -> Result<String, Box<dyn Error>> {
    let output = command.output()?;
    if !output.status.success() {
        return Err(format!(
            "{label} failed:\n{}{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        )
        .into());
    }
    Ok(String::from_utf8(output.stdout)?)
}

fn hex(bytes: &[u8]) -> String {
    let mut output = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        use std::fmt::Write;
        write!(output, "{byte:02x}").expect("writing to String cannot fail");
    }
    output
}

fn append_build_inputs(manifest: &mut String, prefix: &str, inputs: &[BuildInput]) {
    for (index, input) in inputs.iter().enumerate() {
        manifest.push_str(&format!(
            "{prefix}.{index}.path={}\n{prefix}.{index}.sha256={}\n",
            input.path.display(),
            hex(&input.digest),
        ));
    }
}

fn inspect_sdk(options: &Options) -> Result<SdkMetadata, Box<dyn Error>> {
    let manifest_text = fs::read_to_string(&options.sdk_manifest)?;
    let manifest: toml::Value = toml::from_str(&manifest_text)?;
    let package_table = manifest
        .get("package")
        .and_then(toml::Value::as_table)
        .ok_or("SDK manifest lacks a [package] table")?;
    let package_value = |name: &str| -> Result<String, Box<dyn Error>> {
        package_table
            .get(name)
            .and_then(toml::Value::as_str)
            .map(str::to_owned)
            .ok_or_else(|| format!("SDK package field {name:?} must be a string").into())
    };
    let package = package_value("name")?;
    if package != "shmem-pod" {
        return Err(
            format!("SDK manifest package must be \"shmem-pod\", found {package:?}").into(),
        );
    }
    let version = package_value("version")?;
    let edition = package_value("edition")?;
    let root = options
        .sdk_manifest
        .parent()
        .ok_or("SDK manifest has no parent directory")?
        .canonicalize()?;
    let source = options.sdk_source.canonicalize()?;
    let expected_source = root.join("src/lib.rs").canonicalize()?;
    if source != expected_source {
        return Err(format!(
            "SDK crate root must be {}, got {}",
            expected_source.display(),
            source.display()
        )
        .into());
    }
    let source_text = fs::read_to_string(&source)?;
    if !source_text.lines().any(|line| line.trim() == "#![no_std]") {
        return Err("SDK crate root does not declare #![no_std]".into());
    }

    Ok(SdkMetadata {
        root,
        package,
        version,
        edition,
        manifest: hash_file(&options.sdk_manifest)?,
    })
}

fn verify_sdk_unchanged(options: &Options, expected: &SdkMetadata) -> Result<(), Box<dyn Error>> {
    let actual = inspect_sdk(options)?;
    if actual.root != expected.root
        || actual.package != expected.package
        || actual.version != expected.version
        || actual.edition != expected.edition
        || actual.manifest != expected.manifest
    {
        return Err("SDK inputs changed while the executable image was being compiled".into());
    }
    Ok(())
}

fn sdk_rustc_args(sdk: &SdkMetadata) -> String {
    format!(
        "--crate-name shmem_pod --edition={} --crate-type=rlib --emit=link {}",
        sdk.edition,
        COMMON_CODEGEN_ARGS.join(" ")
    )
}

fn pod_rustc_args(options: &Options) -> String {
    format!(
        "--crate-name reverie_pod_v2_code --edition=2024 --crate-type=lib --emit=obj --extern shmem_pod={} {}",
        options.sdk_rlib.display(),
        COMMON_CODEGEN_ARGS.join(" ")
    )
}

fn parse_options() -> Result<Options, Box<dyn Error>> {
    let mut source = None;
    let mut sdk_manifest = None;
    let mut sdk_source = None;
    let mut sdk_rlib = None;
    let mut linker_script = None;
    let mut output = None;
    let mut object = None;
    let mut elf = None;
    let mut manifest = None;
    let mut rustc = String::from("rustc");
    let mut args = env::args_os().skip(1);
    while let Some(argument) = args.next() {
        let argument = argument
            .into_string()
            .map_err(|_| "arguments must be valid UTF-8")?;
        let value = |args: &mut std::iter::Skip<std::env::ArgsOs>, name: &str| {
            args.next()
                .ok_or_else(|| {
                    io::Error::new(
                        io::ErrorKind::InvalidInput,
                        format!("{name} requires a value"),
                    )
                })
                .map(PathBuf::from)
        };
        match argument.as_str() {
            "--source" => source = Some(value(&mut args, "--source")?),
            "--sdk-manifest" => sdk_manifest = Some(value(&mut args, "--sdk-manifest")?),
            "--sdk-source" => sdk_source = Some(value(&mut args, "--sdk-source")?),
            "--sdk-rlib" => sdk_rlib = Some(value(&mut args, "--sdk-rlib")?),
            "--linker-script" => linker_script = Some(value(&mut args, "--linker-script")?),
            "--output" => output = Some(value(&mut args, "--output")?),
            "--object" => object = Some(value(&mut args, "--object")?),
            "--elf" => elf = Some(value(&mut args, "--elf")?),
            "--manifest" => manifest = Some(value(&mut args, "--manifest")?),
            "--rustc" => {
                rustc = args
                    .next()
                    .ok_or("--rustc requires a value")?
                    .into_string()
                    .map_err(|_| "--rustc must be valid UTF-8")?;
            }
            "-h" | "--help" => {
                println!(
                    "usage: pod-v2-compiler --source FILE --sdk-manifest FILE --sdk-source FILE --sdk-rlib FILE --linker-script FILE --output FILE --object FILE --elf FILE --manifest FILE [--rustc PATH]"
                );
                std::process::exit(0);
            }
            _ => return Err(format!("unknown argument {argument:?}").into()),
        }
    }
    Ok(Options {
        source: required(source, "--source")?,
        sdk_manifest: required(sdk_manifest, "--sdk-manifest")?,
        sdk_source: required(sdk_source, "--sdk-source")?,
        sdk_rlib: required(sdk_rlib, "--sdk-rlib")?,
        linker_script: required(linker_script, "--linker-script")?,
        output: required(output, "--output")?,
        object: required(object, "--object")?,
        elf: required(elf, "--elf")?,
        manifest: required(manifest, "--manifest")?,
        rustc,
    })
}

fn required(value: Option<PathBuf>, name: &str) -> Result<PathBuf, Box<dyn Error>> {
    value.ok_or_else(|| format!("missing required argument {name}").into())
}

const _: u16 = TARGET_ARCH_X86_64;

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};

    static TEMP_ID: AtomicU64 = AtomicU64::new(0);

    #[test]
    fn accepts_in_bounds_signed_relative_reference() {
        validate_effective_reference(0x1000, 0x200, 0x40, 0x1100, -4).unwrap();
    }

    #[test]
    fn rejects_section_target_with_outside_addend() {
        let error = validate_effective_reference(0, 0x2000, 0x40, 0, 0x20_000).unwrap_err();
        assert!(error.contains("effective target"));
        assert!(error.contains("outside .pod"));
    }

    #[test]
    fn rejects_effective_target_arithmetic_and_displacement_overflow() {
        assert!(
            validate_effective_reference(0, 16, 0, u64::MAX, 1)
                .unwrap_err()
                .contains("address overflow")
        );
        assert!(
            validate_effective_reference(0, 16, 0, 0, -5)
                .unwrap_err()
                .contains("address overflow")
        );
        assert!(
            validate_effective_reference(0, (i32::MAX as u64) + 16, 0, i32::MAX as u64 + 1, 0)
                .unwrap_err()
                .contains("signed 32 bits")
        );
    }

    #[test]
    fn rejects_relocation_storage_outside_pod() {
        assert!(
            validate_effective_reference(0, 4, 1, 0, 0)
                .unwrap_err()
                .contains("storage lies outside")
        );
    }

    #[test]
    fn dep_info_snapshot_parses_escaped_paths_and_detects_changes() {
        let id = TEMP_ID.fetch_add(1, Ordering::Relaxed);
        let directory =
            env::temp_dir().join(format!("pod-v2-dep-info-{}-{id}", std::process::id()));
        fs::create_dir_all(&directory).unwrap();
        let source = directory.join("crate root.rs");
        let included = directory.join("included data.txt");
        let dep_info = directory.join("crate.d");
        fs::write(&source, "source").unwrap();
        fs::write(&included, "included").unwrap();
        fs::write(
            &dep_info,
            format!(
                "output: {} {}\n",
                depfile::escape(&source.to_string_lossy()),
                depfile::escape(&included.to_string_lossy())
            ),
        )
        .unwrap();

        let snapshot = read_dependency_snapshot(&dep_info).unwrap();
        assert_eq!(snapshot.inputs.len(), 2);
        verify_dependency_files(&snapshot, "test").unwrap();
        fs::write(&included, "changed").unwrap();
        assert!(verify_dependency_files(&snapshot, "test").is_err());
        fs::remove_dir_all(directory).unwrap();
    }
}
