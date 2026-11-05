//! DuckDB storage. One database holds every version; rows are keyed by `version`.

use crate::scan::ScanResult;
use anyhow::Result;
use duckdb::{params, Connection};

const SCHEMA: &str = r#"
CREATE TABLE IF NOT EXISTS rng_types (
    version VARCHAR, class VARCHAR, root VARCHAR, is_interface BOOLEAN
);
CREATE TABLE IF NOT EXISTS rng_fields (
    version VARCHAR, owner VARCHAR, name VARCHAR, descriptor VARCHAR,
    is_static BOOLEAN, is_final BOOLEAN
);
CREATE TABLE IF NOT EXISTS classes (
    version VARCHAR, class VARCHAR, super_class VARCHAR, interfaces VARCHAR, is_interface BOOLEAN
);
CREATE TABLE IF NOT EXISTS methods (
    version VARCHAR, class VARCHAR, name VARCHAR, descr VARCHAR, is_static BOOLEAN, is_abstract BOOLEAN
);
CREATE TABLE IF NOT EXISTS call_sites (
    version VARCHAR, class VARCHAR, method VARCHAR, method_desc VARCHAR, bci UINTEGER,
    kind VARCHAR, target_owner VARCHAR, target_name VARCHAR, target_desc VARCHAR,
    receiver VARCHAR, hints VARCHAR, source_file VARCHAR
);
"#;

pub fn open(path: &str) -> Result<Connection> {
    let c = Connection::open(path)?;
    c.execute_batch(SCHEMA)?;
    Ok(c)
}

pub fn store(conn: &mut Connection, version: &str, r: &ScanResult) -> Result<()> {
    let tx = conn.transaction()?;
    for t in ["rng_types", "rng_fields", "call_sites", "classes", "methods"] {
        tx.execute(&format!("DELETE FROM {t} WHERE version = ?"), params![version])?;
    }
    {
        let mut a = tx.appender("rng_types")?;
        for t in &r.types {
            a.append_row(params![version, t.class, t.root, t.is_interface])?;
        }
        let mut a = tx.appender("rng_fields")?;
        for f in &r.fields {
            a.append_row(params![version, f.owner, f.name, f.descriptor, f.is_static, f.is_final])?;
        }
        let mut a = tx.appender("classes")?;
        for c in &r.classes {
            // interfaces stored comma-joined
            a.append_row(params![version, c.class, c.super_class, c.interfaces.join(","), c.is_interface])?;
        }
        let mut a = tx.appender("methods")?;
        for m in &r.methods {
            a.append_row(params![version, m.class, m.name, m.desc, m.is_static, m.is_abstract])?;
        }
        let mut a = tx.appender("call_sites")?;
        for s in &r.sites {
            a.append_row(params![
                version, s.class, s.method, s.method_desc, s.bci, s.kind, s.target_owner,
                s.target_name, s.target_desc, s.receiver, s.hints, s.source_file
            ])?;
        }
    }
    tx.commit()?;
    Ok(())
}

/// Exports each table to Parquet, sorted for fast range scans by version/class.
pub fn export_parquet(conn: &Connection, dir: &str) -> Result<()> {
    std::fs::create_dir_all(dir)?;
    for (t, order) in [
        ("rng_types", "version, class"),
        ("rng_fields", "version, owner, name"),
        ("call_sites", "version, kind, class, method, bci"),
        ("classes", "version, class"),
        ("methods", "version, class, name"),
        ("draw_roots", "version, root, class, method, bci"),
    ] {
        let exists: i64 = conn.query_row(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = ?",
            [t],
            |r| r.get(0),
        )?;
        if exists == 0 {
            continue;
        }
        conn.execute_batch(&format!(
            "COPY (SELECT * FROM {t} ORDER BY {order}) TO '{dir}/{t}.parquet' (FORMAT PARQUET, COMPRESSION ZSTD)"
        ))?;
    }
    Ok(())
}
