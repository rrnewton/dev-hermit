use object::{Object, ObjectSection, ObjectSymbol, SymbolKind};
use pod_api::{IMAGE_PAGE_SIZE, METHOD_SYMBOLS, PodImageHeader, PodMethod, align_up};
use std::env;
use std::error::Error;
use std::fs;
use std::io;
use std::path::PathBuf;
use std::process::Command;

#[derive(Debug)]
struct Options {
    source: PathBuf,
    output: PathBuf,
    object: PathBuf,
    manifest: PathBuf,
    rustc: String,
}

fn main() {
    if let Err(error) = run() {
        eprintln!("pod-compiler: {error}");
        std::process::exit(1);
    }
}

fn run() -> Result<(), Box<dyn Error>> {
    let options = parse_options()?;
    for path in [&options.output, &options.object, &options.manifest] {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
    }

    compile_object(&options)?;
    let object_bytes = fs::read(&options.object)?;
    let object = object::File::parse(object_bytes.as_slice())?;

    if object.architecture() != object::Architecture::X86_64 {
        return Err(format!(
            "unsupported object architecture: {:?}",
            object.architecture()
        )
        .into());
    }
    if object.format() != object::BinaryFormat::Elf
        || object.kind() != object::ObjectKind::Relocatable
    {
        return Err(format!(
            "expected a relocatable ELF object, got {:?} {:?}",
            object.format(),
            object.kind()
        )
        .into());
    }

    let mut image = vec![0_u8; core::mem::size_of::<PodImageHeader>()];
    let mut methods = [PodMethod::default(); pod_api::METHOD_COUNT];
    let mut manifest_lines = Vec::new();

    for (method_index, symbol_name) in METHOD_SYMBOLS.iter().enumerate() {
        let symbol = object
            .symbols()
            .find(|symbol| symbol.name().ok() == Some(*symbol_name))
            .ok_or_else(|| format!("required symbol {symbol_name:?} is absent"))?;
        if symbol.kind() != SymbolKind::Text || !symbol.is_global() || symbol.size() == 0 {
            return Err(
                format!("symbol {symbol_name:?} is not a non-empty global text symbol").into(),
            );
        }

        let section_index = symbol
            .section_index()
            .ok_or_else(|| format!("symbol {symbol_name:?} has no concrete section"))?;
        let section = object.section_by_index(section_index)?;
        let section_name = section.name()?;
        if !section_name.starts_with(".text") {
            return Err(format!(
                "symbol {symbol_name:?} is in unexpected section {section_name:?}"
            )
            .into());
        }

        let section_data = section.data()?;
        let symbol_start = symbol
            .address()
            .checked_sub(section.address())
            .ok_or_else(|| format!("invalid address for symbol {symbol_name:?}"))?
            as usize;
        let symbol_end = symbol_start
            .checked_add(symbol.size() as usize)
            .ok_or("symbol range overflow")?;
        if symbol_end > section_data.len() {
            return Err(format!("symbol {symbol_name:?} extends beyond its section").into());
        }
        if symbol_start != 0 || symbol_end != section_data.len() {
            return Err(format!(
                "symbol {symbol_name:?} does not occupy its complete dedicated section"
            )
            .into());
        }

        for (offset, relocation) in section.relocations() {
            let offset = offset as usize;
            if (symbol_start..symbol_end).contains(&offset) {
                return Err(format!(
                    "symbol {symbol_name:?} contains a relocation at +0x{:x} targeting {:?}",
                    offset - symbol_start,
                    relocation.target()
                )
                .into());
            }
        }

        let image_offset = align_up(image.len(), 16);
        image.resize(image_offset, 0x90);
        image.extend_from_slice(&section_data[symbol_start..symbol_end]);
        let method_size = u32::try_from(symbol.size())
            .map_err(|_| format!("symbol {symbol_name:?} is too large for the image ABI"))?;
        methods[method_index] = PodMethod {
            offset: image_offset as u64,
            size: method_size,
            reserved: 0,
        };
        manifest_lines.push(format!(
            "method.{method_index}.name={symbol_name}\nmethod.{method_index}.offset=0x{image_offset:x}\nmethod.{method_index}.size={}",
            symbol.size()
        ));
    }

    let mut header = PodImageHeader::empty();
    header.image_len = image.len() as u64;
    header.state_offset = align_up(image.len(), IMAGE_PAGE_SIZE as usize) as u64;
    header.state_len = align_up(
        core::mem::size_of::<pod_api::PodState>(),
        IMAGE_PAGE_SIZE as usize,
    ) as u64;
    header.methods = methods;
    let header_bytes = unsafe {
        core::slice::from_raw_parts(
            (&header as *const PodImageHeader).cast::<u8>(),
            core::mem::size_of::<PodImageHeader>(),
        )
    };
    image[..header_bytes.len()].copy_from_slice(header_bytes);
    fs::write(&options.output, &image)?;

    let rustc_version = Command::new(&options.rustc).arg("-vV").output()?;
    if !rustc_version.status.success() {
        return Err("rustc -vV failed after compilation".into());
    }
    let manifest = format!(
        "format=reverie-pod-v1\nsource={}\nobject={}\nimage={}\nimage_len={}\nstate_offset={}\nstate_len={}\nrustflags=-Copt-level=3 -Cpanic=abort -Crelocation-model=pic -Ccode-model=small -Ccodegen-units=1 -Coverflow-checks=no -Cdebug-assertions=no -Cforce-unwind-tables=no -Ctarget-cpu=x86-64 -Cembed-bitcode=no\n{}\nrustc:\n{}",
        options.source.display(),
        options.object.display(),
        options.output.display(),
        header.image_len,
        header.state_offset,
        header.state_len,
        manifest_lines.join("\n"),
        String::from_utf8_lossy(&rustc_version.stdout),
    );
    fs::write(&options.manifest, manifest)?;

    println!(
        "emitted {} bytes of relocation-free x86_64 code image to {}",
        image.len(),
        options.output.display()
    );
    Ok(())
}

fn compile_object(options: &Options) -> Result<(), Box<dyn Error>> {
    let emit = format!("obj={}", options.object.display());
    let output = Command::new(&options.rustc)
        .arg(&options.source)
        .args([
            "--crate-name",
            "reverie_pod_code",
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

fn parse_options() -> Result<Options, Box<dyn Error>> {
    let mut source = None;
    let mut output = None;
    let mut object = None;
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
            "--output" => output = Some(value(&mut args, "--output")?),
            "--object" => object = Some(value(&mut args, "--object")?),
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
                    "usage: pod-compiler --source FILE --output FILE --object FILE --manifest FILE [--rustc PATH]"
                );
                std::process::exit(0);
            }
            _ => return Err(format!("unknown argument {argument:?}").into()),
        }
    }

    Ok(Options {
        source: required(source, "--source")?,
        output: required(output, "--output")?,
        object: required(object, "--object")?,
        manifest: required(manifest, "--manifest")?,
        rustc,
    })
}

fn required(value: Option<PathBuf>, name: &str) -> Result<PathBuf, Box<dyn Error>> {
    value.ok_or_else(|| format!("missing required argument {name}").into())
}
