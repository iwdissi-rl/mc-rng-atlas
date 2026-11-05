//! Interprocedural resolver: traces every draw back to the root generator(s) it can come from.
//!
//! Roots are: `field:Declaring.name`, `static:Declaring.name`, `new:Type@site`, `factory:Owner.name@site`,
//! `fork(<root>)`, `this:<RngImpl>`, `entry:Class.method#param` (no caller found — lambda/reflection/external),
//! `call:<external>` and `unknown`.

use anyhow::Result;
use duckdb::{params, Connection};
use std::collections::{BTreeSet, HashMap, HashSet};

type Ctx = (String, String, String); // class, method, desc
type Roots = BTreeSet<String>;
const MAX_ROOTS: usize = 64;
const MAX_DEPTH: usize = 48;

struct Site {
    class: String,
    method: String,
    desc: String,
    bci: u32,
    kind: String,
    t_owner: String,
    t_name: String,
    t_desc: String,
    receiver: String,
}

pub struct Resolver {
    supers: HashMap<String, Vec<String>>,
    defines: HashMap<(String, String), Vec<String>>, // (name, desc) -> classes with a concrete body
    by_name_rng_ret: HashMap<String, Vec<(String, String)>>, // name -> (class, desc) of methods with return sites
    param_callers: HashMap<(Ctx, usize), Vec<(String, Ctx, u32)>>,
    returns: HashMap<Ctx, Vec<String>>,
    rng_fields: HashSet<(String, String)>,
    ancestors_cache: HashMap<String, Vec<String>>,
    memo: HashMap<(Ctx, String), Roots>,
    in_progress: HashSet<(Ctx, String)>,
}

fn split_member(s: &str) -> (&str, &str) {
    s.rsplit_once('.').unwrap_or((s, ""))
}

impl Resolver {
    fn ancestors(&mut self, c: &str) -> Vec<String> {
        if let Some(a) = self.ancestors_cache.get(c) {
            return a.clone();
        }
        let mut out = vec![c.to_string()];
        let mut i = 0;
        let mut seen: HashSet<String> = HashSet::from([c.to_string()]);
        while i < out.len() {
            if let Some(ss) = self.supers.get(&out[i]).cloned() {
                for s in ss {
                    if seen.insert(s.clone()) {
                        out.push(s);
                    }
                }
            }
            i += 1;
        }
        self.ancestors_cache.insert(c.to_string(), out.clone());
        out
    }

    /// Every concrete method a call to owner.name(desc) can land in.
    fn dispatch(&mut self, owner: &str, name: &str, desc: &str, exact: bool) -> Vec<Ctx> {
        let key = (name.to_string(), desc.to_string());
        let defs = self.defines.get(&key).cloned().unwrap_or_default();
        let mut out = Vec::new();
        // nearest definition in owner or its ancestors (inherited implementation)
        for a in self.ancestors(owner) {
            if defs.contains(&a) {
                out.push((a, name.to_string(), desc.to_string()));
                break;
            }
        }
        if !exact {
            for c in defs {
                if c != owner && self.ancestors(&c).iter().any(|a| a == owner) {
                    out.push((c, name.to_string(), desc.to_string()));
                }
            }
        }
        out.sort();
        out.dedup();
        out
    }

    fn declaring_field(&mut self, owner: &str, name: &str) -> String {
        for a in self.ancestors(owner) {
            if self.rng_fields.contains(&(a.clone(), name.to_string())) {
                return a;
            }
        }
        owner.to_string()
    }

    pub fn resolve(&mut self, src: &str, ctx: &Ctx, depth: usize) -> Roots {
        let key = (ctx.clone(), src.to_string());
        if let Some(r) = self.memo.get(&key) {
            return r.clone();
        }
        if depth > MAX_DEPTH || !self.in_progress.insert(key.clone()) {
            return Roots::new(); // cycle or too deep: contributes nothing on this path
        }
        let mut out = Roots::new();
        if let Some(i) = src.strip_prefix("param:") {
            let i: usize = i.parse().unwrap_or(usize::MAX);
            let callers = self.param_callers.get(&(ctx.clone(), i)).cloned().unwrap_or_default();
            if callers.is_empty() {
                out.insert(format!("entry:{}.{}#{}", ctx.0, ctx.1, i));
            }
            for (csrc, cctx, _) in callers {
                out.extend(self.resolve(&csrc, &cctx, depth + 1));
                if out.len() > MAX_ROOTS {
                    break;
                }
            }
        } else if let Some((base, m)) = src.rsplit_once("<-") {
            // value returned by calling a method on another generator, e.g. random.fork()
            let (_, mname) = split_member(m);
            for r in self.resolve(base, ctx, depth + 1) {
                out.insert(format!("{mname}({r})"));
            }
        } else if let Some(m) = src.strip_prefix("call:") {
            let (owner, name) = split_member(m);
            let cands: Vec<(String, String)> = self.by_name_rng_ret.get(name).cloned().unwrap_or_default();
            let mut targets = Vec::new();
            for (c, d) in cands {
                for t in self.dispatch(owner, name, &d, false) {
                    if t.0 == c {
                        targets.push(t);
                    }
                }
            }
            targets.sort();
            targets.dedup();
            let mut sub = Roots::new();
            for t in &targets {
                for r in self.returns.get(t).cloned().unwrap_or_default() {
                    sub.extend(self.resolve(&r, t, depth + 1));
                }
            }
            if sub.is_empty() {
                out.insert(format!("call:{owner}.{name}"));
            } else if sub.iter().all(|r| r.starts_with("new:") || r.starts_with("factory:")) {
                // the callee builds a fresh generator each time: the identity is this call site
                out.insert(format!("factory:{}.{}@{}.{}", short(owner), name, short(&ctx.0), ctx.1));
            } else {
                out.extend(sub);
            }
        } else if let Some(f) = src.strip_prefix("field:").or_else(|| src.strip_prefix("static:")) {
            let (owner, name) = split_member(f);
            let decl = self.declaring_field(owner, name);
            let kind = if src.starts_with("static:") { "static" } else { "field" };
            out.insert(format!("{kind}:{decl}.{name}"));
        } else if src == "this" {
            out.insert(format!("this:{}", ctx.0));
        } else if src.starts_with("new:") {
            out.insert(src.to_string());
        } else {
            out.insert(src.to_string());
        }
        if out.len() > MAX_ROOTS {
            let keep: Roots = out.iter().take(MAX_ROOTS).cloned().collect();
            out = keep;
            out.insert("…truncated".into());
        }
        self.in_progress.remove(&key);
        self.memo.insert(key, out.clone());
        out
    }
}

fn short(c: &str) -> &str {
    c.rsplit('/').next().unwrap_or(c)
}

pub fn run(conn: &mut Connection, version: &str) -> Result<(usize, usize)> {
    let mut supers: HashMap<String, Vec<String>> = HashMap::new();
    {
        let mut st = conn.prepare("SELECT class, super_class, interfaces FROM classes WHERE version = ?")?;
        let rows = st.query_map(params![version], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?, r.get::<_, String>(2)?)))?;
        for row in rows {
            let (c, s, i) = row?;
            let mut v: Vec<String> = i.split(',').filter(|x| !x.is_empty()).map(String::from).collect();
            if !s.is_empty() {
                v.insert(0, s);
            }
            supers.insert(c, v);
        }
    }
    let mut defines: HashMap<(String, String), Vec<String>> = HashMap::new();
    {
        let mut st = conn.prepare("SELECT class, name, descr FROM methods WHERE version = ? AND NOT is_abstract")?;
        let rows = st.query_map(params![version], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?, r.get::<_, String>(2)?)))?;
        for row in rows {
            let (c, n, d) = row?;
            defines.entry((n, d)).or_default().push(c);
        }
    }
    let mut rng_fields = HashSet::new();
    {
        let mut st = conn.prepare("SELECT owner, name FROM rng_fields WHERE version = ?")?;
        for row in st.query_map(params![version], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)))? {
            rng_fields.insert(row?);
        }
    }
    let mut sites = Vec::new();
    {
        let mut st = conn.prepare(
            "SELECT class, method, method_desc, bci, kind, target_owner, target_name, target_desc, receiver FROM call_sites WHERE version = ?",
        )?;
        for row in st.query_map(params![version], |r| {
            Ok(Site {
                class: r.get(0)?,
                method: r.get(1)?,
                desc: r.get(2)?,
                bci: r.get(3)?,
                kind: r.get(4)?,
                t_owner: r.get(5)?,
                t_name: r.get(6)?,
                t_desc: r.get(7)?,
                receiver: r.get(8)?,
            })
        })? {
            sites.push(row?);
        }
    }

    let mut rs = Resolver {
        supers,
        defines,
        by_name_rng_ret: HashMap::new(),
        param_callers: HashMap::new(),
        returns: HashMap::new(),
        rng_fields,
        ancestors_cache: HashMap::new(),
        memo: HashMap::new(),
        in_progress: HashSet::new(),
    };

    for s in &sites {
        let ctx: Ctx = (s.class.clone(), s.method.clone(), s.desc.clone());
        match s.kind.as_str() {
            "return" => {
                rs.returns.entry(ctx.clone()).or_default().push(s.receiver.clone());
                let e = rs.by_name_rng_ret.entry(s.method.clone()).or_default();
                if !e.contains(&(s.class.clone(), s.desc.clone())) {
                    e.push((s.class.clone(), s.desc.clone()));
                }
            }
            "passthrough" | "construct" => {
                // construct receivers look like new:Type[0=src;...] when the constructor takes a generator
                let mapping = if s.kind == "construct" {
                    match s.receiver.split_once('[') {
                        Some((_, m)) => m.trim_end_matches(']').to_string(),
                        None => continue,
                    }
                } else {
                    s.receiver.clone()
                };
                let exact = s.t_name == "<init>";
                let targets = rs.dispatch(&s.t_owner, &s.t_name, &s.t_desc, exact);
                for part in mapping.split(';').filter(|p| !p.is_empty()) {
                    let Some((i, src)) = part.split_once('=') else { continue };
                    let Ok(i) = i.parse::<usize>() else { continue };
                    for t in &targets {
                        rs.param_callers.entry((t.clone(), i)).or_default().push((src.to_string(), ctx.clone(), s.bci));
                    }
                }
            }
            _ => {}
        }
    }

    let mut out: Vec<(usize, String)> = Vec::new();
    for (idx, s) in sites.iter().enumerate() {
        if !matches!(s.kind.as_str(), "draw" | "reseed" | "fork" | "derive") {
            continue;
        }
        let ctx: Ctx = (s.class.clone(), s.method.clone(), s.desc.clone());
        let roots = rs.resolve(&s.receiver, &ctx, 0);
        if roots.is_empty() {
            out.push((idx, "unresolved-cycle".into()));
        }
        for r in roots {
            out.push((idx, r));
        }
    }

    let tx = conn.transaction()?;
    tx.execute_batch(
        "CREATE TABLE IF NOT EXISTS draw_roots (version VARCHAR, class VARCHAR, method VARCHAR, method_desc VARCHAR, bci UINTEGER, kind VARCHAR, target VARCHAR, root VARCHAR)",
    )?;
    tx.execute("DELETE FROM draw_roots WHERE version = ?", params![version])?;
    {
        let mut a = tx.appender("draw_roots")?;
        for (i, r) in &out {
            let s = &sites[*i];
            a.append_row(params![version, s.class, s.method, s.desc, s.bci, s.kind, format!("{}.{}", short(&s.t_owner), s.t_name), r])?;
        }
    }
    tx.commit()?;
    let n_sites = out.iter().map(|x| x.0).collect::<HashSet<_>>().len();
    Ok((n_sites, out.len()))
}
