use object::{
    Object, ObjectSection, ObjectSymbol, RelocationKind, RelocationTarget, SectionFlags, SymbolKind,
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

#[derive(Debug)]
struct Options {
    source: PathBuf,
    linker_script: PathBuf,
    output: PathBuf,
    object: PathBuf,
    elf: PathBuf,
    manifest: PathBuf,
    rustc: String,
}

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
    ] {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
    }

    compile_object(&options)?;
    let input_bytes = fs::read(&options.object)?;
    let input = object::File::parse(input_bytes.as_slice())?;
    audit_input_object(&input)?;

    let rust_lld = rust_lld_path(&options.rustc)?;
    link_closure(&options, &rust_lld)?;
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
    let rustc_version = command_output(Command::new(&options.rustc).arg("-vV"), "rustc -vV")?;
    let mut manifest = format!(
        "format=reverie-pod-v2\nsource={}\nobject={}\nelf={}\nimage={}\nimage_len={}\ncode_len={}\ncode_sha256={}\nartifact_sha256={}\nstate_file_len={}\npayload_len={}\nrust_lld={}\n",
        options.source.display(),
        options.object.display(),
        options.elf.display(),
        options.output.display(),
        image.len(),
        pod_bytes.len(),
        hex(&code_hash),
        hex(&image_hash),
        header.state_file_len,
        header.payload_len,
        rust_lld.display(),
    );
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

fn compile_object(options: &Options) -> Result<(), Box<dyn Error>> {
    let emit = format!("obj={}", options.object.display());
    let output = Command::new(&options.rustc)
        .arg(&options.source)
        .args([
            "--crate-name",
            "reverie_pod_v2_code",
            "--edition=2024",
            "--crate-type=lib",
            "--emit",
            &emit,
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
        ])
        .output()?;
    if !output.status.success() {
        return Err(format!(
            "rustc failed:\n{}{}",
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
            if alloc && section.size() != 0 {
                if section.index() != pod_index || writable || !executable {
                    return Err(format!(
                        "forbidden allocated section {:?}: write={writable} exec={executable}",
                        section.name().unwrap_or("<unnamed>")
                    )
                    .into());
                }
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
        if !target_is_inside_pod(elf, pod_index, relocation.target())? {
            return Err(format!(
                "relocation at .pod+0x{offset:x} targets outside immutable pod code"
            )
            .into());
        }
    }
    Ok((pod_index, pod.data()?))
}

fn target_is_inside_pod(
    elf: &object::File<'_>,
    pod_index: object::SectionIndex,
    target: RelocationTarget,
) -> Result<bool, Box<dyn Error>> {
    Ok(match target {
        RelocationTarget::Symbol(index) => {
            elf.symbol_by_index(index)?.section_index() == Some(pod_index)
        }
        RelocationTarget::Section(index) => index == pod_index,
        _ => false,
    })
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

fn parse_options() -> Result<Options, Box<dyn Error>> {
    let mut source = None;
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
                    "usage: pod-v2-compiler --source FILE --linker-script FILE --output FILE --object FILE --elf FILE --manifest FILE [--rustc PATH]"
                );
                std::process::exit(0);
            }
            _ => return Err(format!("unknown argument {argument:?}").into()),
        }
    }
    Ok(Options {
        source: required(source, "--source")?,
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
