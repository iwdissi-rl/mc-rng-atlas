//! Stage 4: correlation analysis over every runtime event (DuckDB, all cores, straight from Parquet).
//!
//! Builds:
//!   an_gens      one row per generator per run: origin site (where its first event happened), class,
//!                event/draw/seed counts, initial state, first draw output
//!   an_edges     origin site -> draw site, with events and generators: the runtime shared-generator graph
//!   an_origins   per origin (class.method:bci): generator statistics + seed categories + crackability tier
//!   an_collisions  identical initial states reached from different origins (identical streams)
//!   an_bias      first-draw uniformity per origin (chi-square over the top output byte)
//!   an_serial    lag-1 correlation of first draws between consecutive generators of the same origin

use anyhow::Result;
use duckdb::Connection;

pub fn run(conn: &Connection, events_glob: &str, threads: usize, memory: &str, temp: &str) -> Result<()> {
    conn.execute_batch(&format!(
        "SET threads = {threads}; SET memory_limit = '{memory}'; SET temp_directory = '{temp}';
         SET preserve_insertion_order = false;"
    ))?;
    let step = |name: &str, sql: &str| -> Result<()> {
        let t = std::time::Instant::now();
        conn.execute_batch(sql)?;
        let n: i64 = conn.query_row(&format!("SELECT count(*) FROM {name}"), [], |r| r.get(0))?;
        println!("{name:<14} {n:>12} rows  {:.1?}", t.elapsed());
        Ok(())
    };

    // Runtime sites resolved to static identities (class.method:bci), per run.
    step(
        "an_sitemap",
        "CREATE OR REPLACE TABLE an_sitemap AS
         SELECT run, site_id, class, method, bci, class || '.' || method || ':' || bci AS site_key
         FROM rt_sites",
    )?;

    // 1. Per-generator summary. The run name is the events directory name.
    step(
        "an_gens",
        &format!(
            "CREATE OR REPLACE TABLE an_gens AS
             SELECT regexp_extract(filename, '/([^/]+)/part-[0-9]+\\.parquet$', 1) AS run, gen,
                    any_value(cls) cls,
                    arg_min(site, seq) origin_site,
                    arg_min(type, seq) first_type,
                    arg_min(a, seq) first_a,
                    arg_min(b, seq) first_b,
                    min(seq) first_seq,
                    count(*) events,
                    count(*) FILTER (type IN (1, 3)) draws,
                    count(*) FILTER (type IN (2, 4)) seeds,
                    arg_min(output, seq) FILTER (type IN (1, 3)) first_out,
                    arg_min(bits, seq) FILTER (type IN (1, 3)) first_bits
             FROM read_parquet('{events_glob}', filename = true)
             GROUP BY ALL"
        ),
    )?;

    // 2. Shared-generator graph: which draw sites consume generators created at each origin.
    step(
        "an_edges",
        &format!(
            "CREATE OR REPLACE TABLE an_edges AS
             WITH e AS (
               SELECT regexp_extract(filename, '/([^/]+)/part-[0-9]+\\.parquet$', 1) AS run, gen, site
               FROM read_parquet('{events_glob}', filename = true) WHERE type IN (1, 3))
             SELECT o.site_key AS origin, d.site_key AS draw_site, count(*) events,
                    approx_count_distinct(e.run || ':' || e.gen) generators, count(DISTINCT e.run) runs
             FROM e JOIN an_gens g USING (run, gen)
             JOIN an_sitemap o ON o.run = g.run AND o.site_id = g.origin_site
             JOIN an_sitemap d ON d.run = e.run AND d.site_id = e.site
             GROUP BY ALL"
        ),
    )?;

    // 3. Per-origin statistics + seed provenance + crackability tier.
    step(
        "an_origins",
        "CREATE OR REPLACE TABLE an_origins AS
         WITH g AS (
           SELECT o.site_key origin, o.class, o.method, o.bci, g.*
           FROM an_gens g JOIN an_sitemap o ON o.run = g.run AND o.site_id = g.origin_site),
         agg AS (
           SELECT origin, any_value(class) class, any_value(method) method, any_value(bci) bci,
                  CASE WHEN bool_and(cls = 3) THEN 'xoroshiro128++' WHEN bool_or(cls = 3) THEN 'mixed' ELSE 'lcg48' END algo,
                  count(*) generators, sum(draws) draws, avg(draws) avg_draws, max(draws) max_draws,
                  avg(seeds) avg_seeds, count(DISTINCT run) runs,
                  approx_count_distinct(first_a::VARCHAR || ':' || first_b::VARCHAR) distinct_initial_states
           FROM g GROUP BY origin),
         consumers AS (SELECT origin, count(*) consumer_sites, sum(events) consumed FROM an_edges GROUP BY origin),
         seeds AS (
           SELECT class, method, bci, string_agg(DISTINCT categories, ',') seed_categories, max(score) seed_score
           FROM seed_sources WHERE version = '26.3' GROUP BY ALL)
         SELECT agg.*, coalesce(consumers.consumer_sites, 0) consumer_sites,
                coalesce(seeds.seed_categories, 'unseeded/inherited') seed_categories,
                CASE algo WHEN 'lcg48' THEN 48 ELSE 128 END state_bits,
                CASE
                  WHEN seeds.seed_categories SIMILAR TO '.*(game_time|entity_identity|constant|position|player_state).*'
                       THEN 'A: predictable from public inputs'
                  WHEN seeds.seed_categories LIKE '%world_seed%' AND distinct_initial_states <= 2
                       THEN 'A: fixed per world seed'
                  WHEN algo = 'lcg48' AND avg_draws >= 2
                       THEN 'B: crackable from a few observed outputs (48-bit LCG)'
                  WHEN algo = 'lcg48'
                       THEN 'B-: 48-bit LCG, short-lived streams'
                  ELSE 'C: 128-bit state, needs many observations'
                END AS tier
         FROM agg
         LEFT JOIN consumers USING (origin)
         LEFT JOIN seeds ON seeds.class = agg.class AND seeds.method = agg.method AND seeds.bci = agg.bci",
    )?;

    // 4. Identical initial states reached from different origins: different mechanics replaying the same stream.
    step(
        "an_collisions",
        "CREATE OR REPLACE TABLE an_collisions AS
         SELECT g.cls, g.first_type, g.first_a, g.first_b,
                count(DISTINCT o.site_key) origins, count(*) generators, count(DISTINCT g.run) runs,
                list(DISTINCT o.site_key ORDER BY o.site_key)[1:8] example_origins
         FROM an_gens g JOIN an_sitemap o ON o.run = g.run AND o.site_id = g.origin_site
         WHERE g.first_type IN (2, 4) AND g.draws > 0
         GROUP BY ALL HAVING count(DISTINCT o.site_key) > 1",
    )?;

    // 5. First-draw bias per origin: chi-square of the top byte of the first output after seeding.
    step(
        "an_bias",
        "CREATE OR REPLACE TABLE an_bias AS
         WITH f AS (
           SELECT o.site_key origin,
                  CASE WHEN g.cls = 3 THEN (g.first_out >> 56) & 255
                       WHEN g.first_bits >= 32 THEN (g.first_out >> 24) & 255
                       WHEN g.first_bits >= 8 THEN (g.first_out >> (g.first_bits - 8)) & 255
                       ELSE NULL END AS bucket
           FROM an_gens g JOIN an_sitemap o ON o.run = g.run AND o.site_id = g.origin_site
           WHERE g.first_out IS NOT NULL),
         h AS (SELECT origin, bucket, count(*) c FROM f WHERE bucket IS NOT NULL GROUP BY ALL),
         n AS (SELECT origin, sum(c) total, count(*) used_buckets FROM h GROUP BY origin HAVING sum(c) >= 2560),
         part AS (
           SELECT n.origin, any_value(n.total) total, any_value(n.used_buckets) used_buckets,
                  sum((h.c - n.total / 256.0) ^ 2 / (n.total / 256.0)) s
           FROM n JOIN h USING (origin) GROUP BY n.origin),
         -- empty buckets each contribute their expected count
         chi AS (SELECT origin, total, used_buckets, s + (256 - used_buckets) * (total / 256.0) AS chi2 FROM part)
         SELECT origin, total samples, used_buckets, chi2,
                -- Wilson-Hilferty normal approximation for chi-square with 255 degrees of freedom
                ((chi2 / 255) ^ (1.0 / 3) - (1 - 2.0 / (9 * 255))) / sqrt(2.0 / (9 * 255)) AS z
         FROM chi",
    )?;

    // 6. Serial correlation of first draws between consecutive generators from the same origin (same run).
    step(
        "an_serial",
        "CREATE OR REPLACE TABLE an_serial AS
         WITH f AS (
           SELECT o.site_key origin, g.run, g.first_seq,
                  CASE WHEN g.cls = 3 THEN ((g.first_out >> 11) & 9007199254740991) / 9007199254740992.0
                       WHEN g.first_bits >= 32 THEN ((g.first_out & 4294967295)) / 4294967296.0
                       ELSE g.first_out / pow(2, g.first_bits) END AS u
           FROM an_gens g JOIN an_sitemap o ON o.run = g.run AND o.site_id = g.origin_site
           WHERE g.first_out IS NOT NULL AND g.first_bits > 0),
         l AS (SELECT origin, u, lag(u) OVER (PARTITION BY origin, run ORDER BY first_seq) prev FROM f)
         SELECT origin, count(*) pairs, corr(u, prev) r, corr(u, prev) * sqrt(count(*)) AS z
         FROM l WHERE prev IS NOT NULL GROUP BY origin HAVING count(*) >= 1000",
    )?;
    Ok(())
}
