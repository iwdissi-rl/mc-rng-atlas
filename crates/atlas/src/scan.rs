//! Static bytecode scanner: finds every RNG type, generator field and RNG call site in a jar.

use anyhow::{Context, Result};
use cafebabe::attributes::AttributeData;
use cafebabe::bytecode::Opcode;
use cafebabe::constant_pool::{Loadable, LiteralConstant, MemberRef};
use cafebabe::descriptors::{FieldDescriptor, FieldType};
use std::collections::{HashMap, VecDeque};
use std::io::Read;

/// Root RNG types. Anything in the jar that extends/implements one of these is also an RNG type.
pub const RNG_ROOTS: &[&str] = &[
    "java/util/Random",
    "java/util/SplittableRandom",
    "java/security/SecureRandom",
    "java/util/concurrent/ThreadLocalRandom",
    "java/util/random/RandomGenerator",
    "net/minecraft/util/RandomSource",
    "net/minecraft/world/level/levelgen/PositionalRandomFactory",
];

#[derive(Debug, Clone)]
pub struct RngType {
    pub class: String,
    pub root: String,
    pub is_interface: bool,
}

#[derive(Debug, Clone)]
pub struct RngField {
    pub owner: String,
    pub name: String,
    pub descriptor: String,
    pub is_static: bool,
    pub is_final: bool,
}

#[derive(Debug, Clone)]
pub struct CallSite {
    pub class: String,
    pub method: String,
    pub method_desc: String,
    pub bci: u32,
    /// draw | construct | reseed | fork | derive | passthrough | capture | global
    pub kind: &'static str,
    pub target_owner: String,
    pub target_name: String,
    pub target_desc: String,
    /// Best-effort identity of the generator used (field:/call:/param:/new:/this/unknown).
    pub receiver: String,
    /// Recent instructions before the site; for construct/reseed/derive this hints at the seed source.
    pub hints: String,
    pub source_file: String,
}

#[derive(Debug, Clone)]
pub struct ClassInfo {
    pub class: String,
    pub super_class: String,
    pub interfaces: Vec<String>,
    pub is_interface: bool,
}

#[derive(Debug, Clone)]
pub struct MethodInfo {
    pub class: String,
    pub name: String,
    pub desc: String,
    pub is_static: bool,
    pub is_abstract: bool,
}

#[derive(Default)]
pub struct ScanResult {
    pub classes: Vec<ClassInfo>,
    pub methods: Vec<MethodInfo>,
    pub types: Vec<RngType>,
    pub fields: Vec<RngField>,
    pub sites: Vec<CallSite>,
    pub classes_scanned: usize,
    pub parse_errors: usize,
}

struct ClassBytes {
    name: String,
    bytes: Vec<u8>,
}

fn read_jar(path: &str) -> Result<Vec<ClassBytes>> {
    let f = std::fs::File::open(path).with_context(|| format!("open {path}"))?;
    let mut zip = zip::ZipArchive::new(f)?;
    let mut out = Vec::new();
    for i in 0..zip.len() {
        let mut e = zip.by_index(i)?;
        let n = e.name()?.to_string();
        if !n.ends_with(".class") || n.starts_with("META-INF/") {
            continue;
        }
        let mut b = Vec::with_capacity(e.size() as usize);
        e.read_to_end(&mut b)?;
        out.push(ClassBytes { name: n.trim_end_matches(".class").to_string(), bytes: b });
    }
    Ok(out)
}

/// Resolves which classes are RNG types via the jar's own hierarchy.
fn rng_hierarchy(classes: &[ClassBytes]) -> (HashMap<String, String>, Vec<RngType>) {
    let mut supers: HashMap<String, (Vec<String>, bool)> = HashMap::new();
    for c in classes {
        if let Ok(cf) = cafebabe::parse_class_with_options(&c.bytes, &no_code()) {
            let mut s: Vec<String> = cf.interfaces.iter().map(|i| i.to_string()).collect();
            if let Some(sc) = &cf.super_class {
                s.push(sc.to_string());
            }
            let iface = cf.access_flags.contains(cafebabe::ClassAccessFlags::INTERFACE);
            supers.insert(cf.this_class.to_string(), (s, iface));
        }
    }
    let mut root_of: HashMap<String, String> =
        RNG_ROOTS.iter().map(|r| (r.to_string(), r.to_string())).collect();
    loop {
        let mut changed = false;
        for (cls, (ss, _)) in &supers {
            if root_of.contains_key(cls) {
                continue;
            }
            if let Some(r) = ss.iter().find_map(|s| root_of.get(s)) {
                root_of.insert(cls.clone(), r.clone());
                changed = true;
            }
        }
        if !changed {
            break;
        }
    }
    let mut types: Vec<RngType> = root_of
        .iter()
        .map(|(c, r)| RngType {
            class: c.clone(),
            root: r.clone(),
            is_interface: supers.get(c).map(|x| x.1).unwrap_or(false),
        })
        .collect();
    types.sort_by(|a, b| a.class.cmp(&b.class));
    (root_of, types)
}

fn no_code() -> cafebabe::ParseOptions {
    let mut o = cafebabe::ParseOptions::default();
    o.parse_bytecode(false);
    o
}

fn obj_name<'a>(fd: &'a FieldDescriptor) -> Option<&'a str> {
    match (&fd.field_type, fd.dimensions) {
        (FieldType::Object(n), 0) => Some(n),
        _ => None,
    }
}

/// Parses a raw method descriptor string like "(JLnet/x/Y;)V" into (param object types or None, return object type).
fn parse_desc(d: &str) -> (Vec<(Option<String>, bool)>, Option<String>) {
    // returns params as (object class if non-array object, is_wide)
    let b = d.as_bytes();
    let mut i = 1;
    let mut params = Vec::new();
    let read = |i: &mut usize| -> (Option<String>, bool) {
        let mut dims = 0;
        while b[*i] == b'[' {
            dims += 1;
            *i += 1;
        }
        let c = b[*i];
        *i += 1;
        match c {
            b'L' => {
                let s = *i;
                while b[*i] != b';' {
                    *i += 1;
                }
                let n = &d[s..*i];
                *i += 1;
                (if dims == 0 { Some(n.to_string()) } else { None }, false)
            }
            b'J' | b'D' if dims == 0 => (None, true),
            _ => (None, false),
        }
    };
    while b[i] != b')' {
        params.push(read(&mut i));
    }
    i += 1;
    let ret = if b[i] == b'V' { None } else { read(&mut i).0 };
    (params, ret)
}

fn classify(name: &str) -> &'static str {
    match name {
        "setSeed" => "reseed",
        "fork" | "forkPositional" | "split" | "splits" => "fork",
        "at" | "fromHashOf" | "fromSeed" | "positional" => "derive",
        "<init>" => "construct",
        _ => "draw",
    }
}

const GLOBAL_STATICS: &[(&str, &str)] = &[
    ("java/lang/Math", "random"),
    ("java/lang/StrictMath", "random"),
    ("java/util/UUID", "randomUUID"),
];

pub fn scan_jar(path: &str) -> Result<ScanResult> {
    let classes = read_jar(path)?;
    let (rng, types) = rng_hierarchy(&classes);
    let is_rng = |c: &str| rng.contains_key(c);

    let per_class: Vec<(Vec<RngField>, Vec<CallSite>, Option<(ClassInfo, Vec<MethodInfo>)>)> = {
        use rayon::prelude::*;
        classes
            .par_iter()
            .map(|c| match cafebabe::parse_class(&c.bytes) {
                Ok(cf) => {
                    let (f, s) = scan_class(&cf, &is_rng);
                    (f, s, Some(hierarchy(&cf)))
                }
                Err(e) => {
                    eprintln!("parse error in {}: {e}", c.name);
                    (vec![], vec![], None)
                }
            })
            .collect()
    };

    let mut res = ScanResult { types, classes_scanned: classes.len(), ..Default::default() };
    for (f, s, h) in per_class {
        res.fields.extend(f);
        res.sites.extend(s);
        match h {
            Some((c, ms)) => {
                res.classes.push(c);
                res.methods.extend(ms);
            }
            None => res.parse_errors += 1,
        }
    }
    Ok(res)
}

fn hierarchy(cf: &cafebabe::ClassFile) -> (ClassInfo, Vec<MethodInfo>) {
    let class = cf.this_class.to_string();
    let ci = ClassInfo {
        class: class.clone(),
        super_class: cf.super_class.as_ref().map(|c| c.to_string()).unwrap_or_default(),
        interfaces: cf.interfaces.iter().map(|i| i.to_string()).collect(),
        is_interface: cf.access_flags.contains(cafebabe::ClassAccessFlags::INTERFACE),
    };
    let ms = cf
        .methods
        .iter()
        .map(|m| MethodInfo {
            class: class.clone(),
            name: m.name.to_string(),
            desc: m.descriptor.to_string(),
            is_static: m.access_flags.contains(cafebabe::MethodAccessFlags::STATIC),
            is_abstract: m.access_flags.contains(cafebabe::MethodAccessFlags::ABSTRACT),
        })
        .collect();
    (ci, ms)
}

fn member(m: &MemberRef) -> String {
    format!("{}.{}", short(&m.class_name), m.name_and_type.name)
}

fn short(c: &str) -> &str {
    c.rsplit('/').next().unwrap_or(c)
}

fn scan_class(cf: &cafebabe::ClassFile, is_rng: &dyn Fn(&str) -> bool) -> (Vec<RngField>, Vec<CallSite>) {
    let class = cf.this_class.to_string();
    let source_file = cf
        .attributes
        .iter()
        .find_map(|a| match &a.data {
            AttributeData::SourceFile(s) => Some(s.to_string()),
            _ => None,
        })
        .unwrap_or_default();
    let class_is_rng = is_rng(&class);
    let bootstraps: Vec<&cafebabe::attributes::BootstrapMethodEntry> = cf
        .attributes
        .iter()
        .find_map(|a| match &a.data {
            AttributeData::BootstrapMethods(b) => Some(b.iter().collect()),
            _ => None,
        })
        .unwrap_or_default();

    let mut fields = Vec::new();
    for f in &cf.fields {
        if let Some(n) = obj_name(&f.descriptor) {
            if is_rng(n) {
                fields.push(RngField {
                    owner: class.clone(),
                    name: f.name.to_string(),
                    descriptor: f.descriptor.to_string(),
                    is_static: f.access_flags.contains(cafebabe::FieldAccessFlags::STATIC),
                    is_final: f.access_flags.contains(cafebabe::FieldAccessFlags::FINAL),
                });
            }
        }
    }

    let mut sites = Vec::new();
    for m in &cf.methods {
        let Some(code) = m.attributes.iter().find_map(|a| match &a.data {
            AttributeData::Code(c) => Some(c),
            _ => None,
        }) else {
            continue;
        };
        let Some(bc) = &code.bytecode else { continue };
        let is_static = m.access_flags.contains(cafebabe::MethodAccessFlags::STATIC);

        // local slot -> generator identity
        let mut locals: HashMap<u16, String> = HashMap::new();
        let mut slot: u16 = 0;
        if !is_static {
            if class_is_rng {
                locals.insert(0, "this".into());
            }
            slot = 1;
        }
        for (i, p) in m.descriptor.parameters.iter().enumerate() {
            if let Some(n) = obj_name(p) {
                if is_rng(n) {
                    locals.insert(slot, format!("param:{i}"));
                }
            }
            slot += match (&p.field_type, p.dimensions) {
                (FieldType::Long | FieldType::Double, 0) => 2,
                _ => 1,
            };
        }

        let mut stack: Vec<String> = Vec::new(); // RNG values only
        let mut last_pushed = false;
        let mut hints: VecDeque<String> = VecDeque::new();
        let push_hint = |h: &mut VecDeque<String>, s: String| {
            if h.len() == 8 {
                h.pop_front();
            }
            h.push_back(s);
        };
        let mdesc = m.descriptor.to_string();

        for (off, op) in &bc.opcodes {
            let mut pushed = false;
            let mut emit = |kind: &'static str, t: &MemberRef, recv: String, hints: &VecDeque<String>| {
                sites.push(CallSite {
                    class: class.clone(),
                    method: m.name.to_string(),
                    method_desc: mdesc.clone(),
                    bci: *off as u32,
                    kind,
                    target_owner: t.class_name.to_string(),
                    target_name: t.name_and_type.name.to_string(),
                    target_desc: t.name_and_type.descriptor.to_string(),
                    receiver: recv,
                    hints: hints.iter().cloned().collect::<Vec<_>>().join(" | "),
                    source_file: source_file.clone(),
                });
            };
            match op {
                Opcode::Aload(n) => {
                    if let Some(s) = locals.get(n) {
                        stack.push(s.clone());
                        pushed = true;
                    }
                }
                Opcode::Astore(n) => {
                    if last_pushed {
                        let s = stack.pop().unwrap_or_else(|| "unknown".into());
                        locals.insert(*n, s);
                    } else {
                        locals.remove(n);
                    }
                }
                Opcode::Dup if last_pushed => {
                    if let Some(t) = stack.last().cloned() {
                        stack.push(t);
                        pushed = true;
                    }
                }
                Opcode::Checkcast(_) => pushed = last_pushed,
                Opcode::Getfield(f) | Opcode::Getstatic(f) => {
                    let ty = f.name_and_type.descriptor.as_ref();
                    if ty.starts_with('L') && is_rng(&ty[1..ty.len() - 1]) {
                        let st = if matches!(op, Opcode::Getstatic(_)) { "static:" } else { "field:" };
                        stack.push(format!("{st}{}.{}", f.class_name, f.name_and_type.name));
                        pushed = true;
                    } else {
                        push_hint(&mut hints, format!("get {}", member(f)));
                    }
                }
                Opcode::Ldc(l) | Opcode::LdcW(l) | Opcode::Ldc2W(l) => {
                    if let Loadable::LiteralConstant(c) = l {
                        let s = match c {
                            LiteralConstant::Integer(v) => format!("const {v}"),
                            LiteralConstant::Long(v) => format!("const {v}L"),
                            LiteralConstant::Float(v) => format!("const {v}f"),
                            LiteralConstant::Double(v) => format!("const {v}d"),
                            LiteralConstant::String(v) => format!("const \"{v}\""),
                            _ => "const ?".into(),
                        };
                        push_hint(&mut hints, s);
                    }
                }
                Opcode::Lxor | Opcode::Ixor => push_hint(&mut hints, "xor".into()),
                Opcode::Lmul | Opcode::Imul => push_hint(&mut hints, "mul".into()),
                Opcode::Invokedynamic(indy) => {
                    let (params, _) = parse_desc(&indy.name_and_type.descriptor);
                    let n = params.iter().filter(|p| p.0.as_deref().is_some_and(is_rng)).count();
                    if n > 0 {
                        let mut recv = Vec::new();
                        for _ in 0..n {
                            recv.push(stack.pop().unwrap_or_else(|| "unknown".into()));
                        }
                        recv.reverse();
                        // Link the capture to the lambda / method-reference implementation it calls.
                        let implh = bootstraps.get(indy.attr_index as usize).and_then(|b| {
                            b.arguments.iter().find_map(|a| match a {
                                cafebabe::constant_pool::BootstrapArgument::MethodHandle(h)
                                    if !matches!(
                                        h.kind,
                                        cafebabe::constant_pool::ReferenceKind::GetField
                                            | cafebabe::constant_pool::ReferenceKind::GetStatic
                                            | cafebabe::constant_pool::ReferenceKind::PutField
                                            | cafebabe::constant_pool::ReferenceKind::PutStatic
                                    ) =>
                                {
                                    Some(h)
                                }
                                _ => None,
                            })
                        });
                        let rng_pos: Vec<usize> = params
                            .iter()
                            .enumerate()
                            .filter(|(_, p)| p.0.as_deref().is_some_and(is_rng))
                            .map(|(i, _)| i)
                            .collect();
                        match implh {
                            Some(h) => {
                                use cafebabe::constant_pool::ReferenceKind as K;
                                let shift = matches!(h.kind, K::InvokeVirtual | K::InvokeInterface | K::InvokeSpecial) as usize;
                                let t = MemberRef { class_name: h.class_name.clone(), name_and_type: h.member_ref.clone() };
                                let mut mapped = Vec::new();
                                for (pos, src) in rng_pos.iter().zip(&recv) {
                                    if *pos < shift {
                                        // captured generator is the receiver: a method reference like random::nextFloat
                                        if is_rng(&h.class_name) {
                                            emit(classify(&h.member_ref.name), &t, src.clone(), &hints);
                                        }
                                    } else {
                                        mapped.push(format!("{}={}", pos - shift, src));
                                    }
                                }
                                if !mapped.is_empty() {
                                    emit("passthrough", &t, mapped.join(";"), &hints);
                                }
                            }
                            None => {
                                let t = MemberRef { class_name: "<indy>".into(), name_and_type: indy.name_and_type.clone() };
                                emit("capture", &t, recv.join(","), &hints);
                            }
                        }
                    }
                }
                Opcode::Invokevirtual(t)
                | Opcode::Invokeinterface(t, _)
                | Opcode::Invokestatic(t)
                | Opcode::Invokespecial(t) => {
                    let owner = t.class_name.as_ref();
                    let name = t.name_and_type.name.as_ref();
                    let (params, ret) = parse_desc(&t.name_and_type.descriptor);
                    let static_call = matches!(op, Opcode::Invokestatic(_));
                    let owner_rng = is_rng(owner);
                    let n_rng_params = params.iter().filter(|p| p.0.as_deref().is_some_and(is_rng)).count();

                    let rng_idx: Vec<usize> = params
                        .iter()
                        .enumerate()
                        .filter(|(_, p)| p.0.as_deref().is_some_and(is_rng))
                        .map(|(i, _)| i)
                        .collect();
                    let mut args = Vec::new();
                    for _ in 0..n_rng_params {
                        args.push(stack.pop().unwrap_or_else(|| "unknown".into()));
                    }
                    args.reverse();
                    // "argIndex=source;..." so the resolver knows which callee parameter receives what
                    let mapped: Vec<String> =
                        rng_idx.iter().zip(&args).map(|(i, a)| format!("{i}={a}")).collect();
                    let ret_rng = ret.as_deref().is_some_and(is_rng);

                    if owner_rng && name == "<init>" {
                        emit("construct", t, if mapped.is_empty() { format!("new:{owner}") } else { format!("new:{owner}[{}]", mapped.join(";")) }, &hints);
                        stack.push(format!("new:{}@{}.{}:{}", short(owner), short(&class), m.name, off));
                        pushed = true;
                    } else if owner_rng && !static_call {
                        let recv = stack.pop().unwrap_or_else(|| "unknown".into());
                        let kind = classify(name);
                        emit(kind, t, recv.clone(), &hints);
                        if ret_rng {
                            stack.push(format!("{}<-{}.{}", recv, short(owner), name));
                            pushed = true;
                        }
                    } else if owner_rng && static_call {
                        // RandomSource.create(...), ThreadLocalRandom.current(), etc.
                        let kind = if ret_rng { "construct" } else if n_rng_params > 0 { "passthrough" } else { "draw" };
                        emit(kind, t, if args.is_empty() { format!("static:{owner}") } else { mapped.join(";") }, &hints);
                        if ret_rng {
                            // factories taking arguments (a seed) are seeded; "()" factories are not
                            let seeded = if t.name_and_type.descriptor.starts_with("()") { "" } else { "(seeded)" };
                            stack.push(format!("call:{}.{}{}", owner, name, seeded));
                            pushed = true;
                        }
                    } else {
                        if GLOBAL_STATICS.contains(&(owner, name))
                            || (owner == "java/util/Collections" && name == "shuffle" && params.len() == 1)
                        {
                            emit("global", t, "global".into(), &hints);
                        } else if n_rng_params > 0 {
                            emit("passthrough", t, mapped.join(";"), &hints);
                        }
                        if ret_rng {
                            stack.push(format!("call:{}.{}", owner, name));
                            pushed = true;
                        }
                        push_hint(&mut hints, format!("call {}", member(t)));
                    }
                }
                Opcode::Areturn if last_pushed => {
                    let recv = stack.pop().unwrap_or_else(|| "unknown".into());
                    let t = MemberRef {
                        class_name: class.clone().into(),
                        name_and_type: cafebabe::constant_pool::NameAndType {
                            name: m.name.to_string().into(),
                            descriptor: mdesc.clone().into(),
                        },
                    };
                    emit("return", &t, recv, &hints);
                    stack.clear();
                }
                Opcode::Return | Opcode::Areturn | Opcode::Athrow | Opcode::Goto(_) => {
                    stack.clear();
                }
                _ => {}
            }
            last_pushed = pushed;
        }
    }
    (fields, sites)
}
