//! Seed provenance: classifies what every generator is seeded from and ranks how unusual it is.

use anyhow::Result;
use duckdb::{params, Connection};

/// (category, unusualness score, substrings that identify it in the statement-local hints)
const RULES: &[(&str, u8, &[&str])] = &[
    ("game_time", 5, &["getGameTime", "getDayTime", "gameTime", "getOverworldClockTime"]),
    ("entity_identity", 4, &["getUUID", "UUID.hashCode", "getId", "UUID.getMostSignificantBits", "UUID.getLeastSignificantBits", "uuid"]),
    ("wall_clock", 3, &["nanoTime", "currentTimeMillis", "Instant", "Util.getMillis", "Util.getNanos"]),
    ("player_state", 4, &["enchantmentSeed", "getEnchantmentSeed", "experienceLevel", "Player."]),
    ("position", 2, &["BlockPos", "ChunkPos", "SectionPos", "asLong", "getX", "getZ", "getY", "chunkX", "chunkZ", ".x", ".z"]),
    ("other_rng", 2, &["nextLong", "nextInt", "RandomSource.", "Random."]),
    ("string_hash", 3, &["hashCode", "fromHashOf", "\""]),
    ("world_seed", 1, &["getSeed", "seed", "Seed", "WorldOptions", "concentricRingsSeed"]),
];

pub fn classify(hints: &str) -> (Vec<&'static str>, u8) {
    let mut cats = Vec::new();
    let mut score = 0u8;
    for (cat, s, keys) in RULES {
        if keys.iter().any(|k| hints.contains(k)) {
            cats.push(*cat);
            score = score.max(*s);
        }
    }
    if cats.is_empty() {
        if hints.contains("const") {
            cats.push("constant");
            score = 3; // a fixed seed is fully predictable by anyone
        } else {
            cats.push("unknown");
            score = 3;
        }
    }
    if cats.len() > 1 {
        score = score.saturating_add(1); // mixed sources are rarer and worth a look
    }
    (cats, score)
}

pub fn run(conn: &mut Connection, version: &str) -> Result<usize> {
    // Seed-taking sites: reseeds, seeded factories, constructors taking a primitive seed, positional derivations.
    let mut st = conn.prepare(
        "SELECT class, method, bci, kind, target_owner || '.' || target_name, target_desc, receiver, hints
         FROM call_sites
         WHERE version = ?
           AND (kind IN ('reseed', 'derive')
                OR (kind = 'construct' AND (target_desc LIKE '(J%' OR target_desc LIKE '(I%')))",
    )?;
    let rows: Vec<(String, String, u32, String, String, String, String, String)> = st
        .query_map(params![version], |r| {
            Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?, r.get(5)?, r.get(6)?, r.get(7)?))
        })?
        .collect::<Result<_, _>>()?;
    drop(st);

    let tx = conn.transaction()?;
    tx.execute_batch(
        "CREATE TABLE IF NOT EXISTS seed_sources (version VARCHAR, class VARCHAR, method VARCHAR, bci UINTEGER,
            kind VARCHAR, target VARCHAR, target_desc VARCHAR, receiver VARCHAR, categories VARCHAR, score UTINYINT, hints VARCHAR)",
    )?;
    tx.execute("DELETE FROM seed_sources WHERE version = ?", params![version])?;
    {
        let mut a = tx.appender("seed_sources")?;
        for (c, m, b, k, t, td, r, h) in &rows {
            let (cats, score) = classify(h);
            a.append_row(params![version, c, m, b, k, t, td, r, cats.join(","), score, h])?;
        }
    }
    // Root generators with how they are used; `reseeded` catches create()-then-setSeed() patterns.
    tx.execute_batch(
        "CREATE OR REPLACE VIEW generator_roots AS
         SELECT version, root,
                count(*) FILTER (kind = 'draw')   AS draw_sites,
                count(*) FILTER (kind = 'reseed') AS reseed_sites,
                count(*) FILTER (kind IN ('fork','derive')) AS fork_sites,
                count(DISTINCT class) AS classes,
                bool_or(kind = 'reseed') AS reseeded,
                CASE
                  WHEN root LIKE 'factory:%(seeded)%' OR bool_or(kind = 'reseed') THEN 'seeded'
                  WHEN root LIKE 'factory:%' OR root LIKE 'new:%' THEN 'unseeded'
                  WHEN root LIKE 'static:%' THEN 'process-global'
                  WHEN root LIKE 'field:%' THEN 'owned-by-object'
                  ELSE 'other'
                END AS seeding
         FROM draw_roots GROUP BY version, root",
    )?;
    tx.commit()?;
    Ok(rows.len())
}
