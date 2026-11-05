mod db;
mod resolve;
mod runtime;
mod scan;
mod seeds;

use anyhow::Result;
use clap::{Parser, Subcommand};

#[derive(Parser)]
#[command(name = "atlas", about = "Minecraft RNG Atlas: map, confirm and correlate every RNG source")]
struct Cli {
    /// DuckDB database file
    #[arg(long, global = true, default_value = "data/atlas.duckdb")]
    db: String,
    #[command(subcommand)]
    cmd: Cmd,
}

#[derive(Subcommand)]
enum Cmd {
    /// Statically scan a jar and store its RNG types, fields and call sites
    Scan {
        #[arg(long)]
        version: String,
        #[arg(long)]
        jar: String,
    },
    /// Trace every draw back to its root generator(s) (builds the draw_roots table)
    Resolve {
        #[arg(long)]
        version: String,
    },
    /// Scan several versions in parallel: --jar 26.3=path.jar --jar 1.16.1=path.jar ...
    ScanAll {
        #[arg(long = "jar", required = true)]
        jars: Vec<String>,
    },
    /// Classify every seed source and build the generator_roots view
    Seeds {
        #[arg(long)]
        version: String,
    },
    /// Ingest one agent run directory, replay every generator to confirm it, and store per-site results
    Ingest {
        /// run directory produced by tools/run_scenario.py
        dir: String,
        #[arg(long, default_value = "26.3")]
        version: String,
        /// run name (defaults to the directory name)
        #[arg(long)]
        name: Option<String>,
        #[arg(long, default_value = "data/events")]
        events: String,
    },
    /// Runtime coverage of the static catalog: confirmed sites, runtime-only sites, never-exercised sites
    Coverage {
        #[arg(long, default_value = "26.3")]
        version: String,
    },
    /// Export all tables to sorted, zstd-compressed Parquet
    Export {
        #[arg(long, default_value = "data/parquet")]
        out: String,
    },
    /// Run an SQL query against the atlas and print the result
    Query { sql: String },
}

fn main() -> Result<()> {
    let cli = Cli::parse();
    if let Some(p) = std::path::Path::new(&cli.db).parent() {
        std::fs::create_dir_all(p)?;
    }
    let mut conn = db::open(&cli.db)?;
    match cli.cmd {
        Cmd::Scan { version, jar } => {
            let t = std::time::Instant::now();
            let r = scan::scan_jar(&jar)?;
            db::store(&mut conn, &version, &r)?;
            println!(
                "{version}: {} classes ({} parse errors), {} RNG types, {} RNG fields, {} call sites in {:.2?}",
                r.classes_scanned,
                r.parse_errors,
                r.types.len(),
                r.fields.len(),
                r.sites.len(),
                t.elapsed()
            );
        }
        Cmd::Resolve { version } => {
            let t = std::time::Instant::now();
            let (sites, edges) = resolve::run(&mut conn, &version)?;
            println!("{version}: resolved {sites} sites into {edges} site->root edges in {:.2?}", t.elapsed());
        }
        Cmd::ScanAll { jars } => {
            let t = std::time::Instant::now();
            // Each version is scanned on its own thread (each scan is itself rayon-parallel over classes).
            let results: Vec<(String, Result<scan::ScanResult>)> = std::thread::scope(|s| {
                let hs: Vec<_> = jars
                    .iter()
                    .map(|vj| {
                        let (v, j) = vj.split_once('=').expect("--jar takes VERSION=PATH");
                        s.spawn(move || (v.to_string(), scan::scan_jar(j)))
                    })
                    .collect();
                hs.into_iter().map(|h| h.join().unwrap()).collect()
            });
            for (v, r) in results {
                let r = r?;
                db::store(&mut conn, &v, &r)?;
                let (sites, _) = resolve::run(&mut conn, &v)?;
                let seeds = seeds::run(&mut conn, &v)?;
                println!("{v}: {} classes, {} call sites, {sites} resolved draw sites, {seeds} seed sources", r.classes_scanned, r.sites.len());
            }
            println!("all versions in {:.2?}", t.elapsed());
        }
        Cmd::Seeds { version } => {
            let n = seeds::run(&mut conn, &version)?;
            println!("{version}: classified {n} seed sources");
        }
        Cmd::Ingest { dir, version, name, events } => {
            let p = std::path::Path::new(&dir);
            let run = name.unwrap_or_else(|| p.file_name().unwrap().to_string_lossy().into_owned());
            println!("{}", runtime::ingest(&mut conn, p, &run, &version, std::path::Path::new(&events))?);
        }
        Cmd::Coverage { version } => {
            conn.execute_batch(&format!(
                "CREATE OR REPLACE TEMP VIEW rt AS
                   SELECT class, method, bci, sum(events) events, sum(confirmed) confirmed, sum(mismatched) mismatched,
                          count(DISTINCT run) runs
                   FROM rt_sites WHERE version = '{version}' GROUP BY ALL;
                 CREATE OR REPLACE TEMP VIEW st AS
                   SELECT class, method, bci, any_value(kind) kind FROM call_sites
                   WHERE version = '{version}' AND kind IN ('draw','construct','reseed','fork','derive') GROUP BY ALL;"
            ))?;
            println!("== static sites exercised at runtime (by kind)");
            print_query(&conn, "SELECT kind, count(*) static_sites, count(rt.class) exercised,
                round(100.0 * count(rt.class) / count(*), 1) pct, sum(rt.confirmed) confirmed_draws, sum(rt.mismatched) mismatched
                FROM st LEFT JOIN rt USING (class, method, bci) GROUP BY kind ORDER BY static_sites DESC")?;
            println!("\n== runtime sites missing from the static table");
            print_query(&conn, "SELECT rt.class, rt.method, rt.bci, rt.events FROM rt LEFT JOIN st USING (class, method, bci)
                WHERE st.class IS NULL ORDER BY events DESC LIMIT 20")?;
            println!("\n== packages with the most never-exercised draw sites");
            print_query(&conn, "SELECT regexp_extract(class, '^(.*)/[^/]+$', 1) package, count(*) unexercised
                FROM st LEFT JOIN rt USING (class, method, bci) WHERE rt.class IS NULL AND kind = 'draw'
                GROUP BY ALL ORDER BY unexercised DESC LIMIT 15")?;
        }
        Cmd::Export { out } => db::export_parquet(&conn, &out)?,
        Cmd::Query { sql } => {
            print_query(&conn, &sql)?;
        }
    }
    Ok(())
}

/// Prints any query as tab-separated rows with a header.
fn print_query(conn: &duckdb::Connection, sql: &str) -> Result<()> {
    use duckdb::types::ValueRef;
    let mut stmt = conn.prepare(sql)?;
    let mut rows = stmt.query([])?;
    let mut header_done = false;
    while let Some(row) = rows.next()? {
        let st = row.as_ref();
        let n = st.column_count();
        if !header_done {
            let names: Vec<String> = (0..n).map(|i| st.column_name(i).map(|s| s.to_string()).unwrap_or_default()).collect();
            println!("{}", names.join("\t"));
            header_done = true;
        }
        let vals: Vec<String> = (0..n)
            .map(|i| match row.get_ref(i) {
                Ok(ValueRef::Null) => "NULL".into(),
                Ok(ValueRef::Text(t)) => String::from_utf8_lossy(t).into_owned(),
                Ok(v) => format!("{:?}", v.to_owned()).split_once('(').map(|(_, r)| r.trim_end_matches(')').to_string()).unwrap_or_default(),
                Err(e) => format!("<{e}>"),
            })
            .collect();
        println!("{}", vals.join("\t"));
    }
    Ok(())
}
