#!/usr/bin/env python3
"""Cross-version check: run the full analysis per runtime version and compare the key results.

For each version: `atlas analyze` and `atlas segments` restricted to that version's runs, the rediscovery checklist,
and a row of key numbers (coverage, replay confirmation, headline findings). 26.3 runs last so the main analysis
tables end on the latest release. Output: datasets/12-cross-version/.
"""
import csv, subprocess
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
ATLAS = str(ROOT / "crates/atlas/target/release/atlas")
OUT = ROOT / "datasets/12-cross-version"
VERSIONS = [("26.1.2", "v26.1.2-", r"/events/v26\.1\.2-[^/]*/part-"), ("26.2", "v26.2-", r"/events/v26\.2-[^/]*/part-"),
            ("26.3", "", r"/events/[^v/][^/]*/part-")]
GAMEPLAY = """method NOT SIMILAR TO '.*(animateTick|[Pp]article|tickClient|clientTick|handleEntityEvent|playIdleSounds|emit.*|addEating.*|makeDrown.*|makePoof.*|spawnBubbles|spawnTamingParticles|animationTick|onFlap|playGallopSound|playChestEquipsSound).*'
              AND class NOT LIKE 'net/minecraft/client/%' AND class NOT LIKE 'com/mojang/%'"""


def sh(*a):
    r = subprocess.run(a, cwd=ROOT, capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"{a[:3]} failed: {r.stderr[-500:]}")
    return r.stdout


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for v, prefix, filt in VERSIONS:
        print(f"== {v}", flush=True)
        # per-version directory of symlinks so each pass reads only that version's files
        vdir = ROOT / "data/events-by-version" / v
        vdir.mkdir(parents=True, exist_ok=True)
        for run in (ROOT / "data/events").iterdir():
            is_v = run.name.startswith("v") and run.name[1:2].isdigit()
            if (prefix and run.name.startswith(prefix)) or (not prefix and not is_v):
                link = vdir / run.name
                if not link.exists():
                    link.symlink_to(run.resolve())
        print(sh(ATLAS, "analyze", "--events", str(vdir / "*/*.parquet"), "--run-filter", ".").strip().splitlines()[-1])
        print(sh(ATLAS, "segments", "--events", str(vdir), "--prefix", prefix).strip().splitlines()[-1])
        print(sh(str(ROOT / ".venv/bin/python"), "tools/rediscovery.py", "--version", v, "--out", str(OUT / f"rediscovery-{v}.csv")).strip().splitlines()[-1])
        c = duckdb.connect(str(ROOT / "data/atlas.duckdb"), read_only=True)
        q = lambda s: c.execute(s).fetchone()
        runs = q(f"SELECT count(*), sum(events), sum(confirmed), sum(mismatched) FROM rt_runs WHERE version='{v}'")
        cov = q(f"""WITH rt AS (SELECT DISTINCT class, method, bci FROM rt_sites WHERE version='{v}'),
                    st AS (SELECT DISTINCT class, method, bci FROM call_sites WHERE version='{v}' AND kind='draw' AND {GAMEPLAY})
                    SELECT count(*), count(rt.class) FROM st LEFT JOIN rt USING (class, method, bci)""")
        ench = q("SELECT r, pairs FROM an_seg_serial WHERE site_key LIKE '%EnchantmentHelper.selectEnchantment%' ORDER BY pairs DESC LIMIT 1") or (None, 0)
        pal = q("SELECT r, pairs FROM an_seg_serial WHERE site_key LIKE '%StructurePlaceSettings%' ORDER BY pairs DESC LIMIT 1") or (None, 0)
        lvl = q("SELECT max(n) FROM (SELECT count(DISTINCT regexp_extract(draw_site, '^(.*)\\.[^.]+:', 1)) n FROM an_edges WHERE origin LIKE '%/Level.<init>%' GROUP BY origin)")
        piglin = q(f"SELECT count(*) FROM seed_sources WHERE version='{v}' AND class LIKE '%PiglinAi' AND categories LIKE '%game_time%'")
        ench_formula = q(f"SELECT count(*) FROM call_sites WHERE version='{v}' AND class LIKE '%EnchantmentMenu' AND kind='reseed' AND hints LIKE '%enchantmentSeed%'")
        red = list(csv.DictReader(open(OUT / f"rediscovery-{v}.csv")))
        rows.append({
            "version": v, "runs": runs[0], "events": runs[1], "draws_confirmed": runs[2], "mismatches": runs[3],
            "gameplay_draw_sites": cov[0], "gameplay_sites_exercised": cov[1], "coverage_pct": round(100 * cov[1] / cov[0], 1),
            "rediscovered": sum(r["status"] == "rediscovered" for r in red), "checks": len(red),
            "piglin_dance_seeded_by_game_time": piglin[0] > 0, "enchant_seed_plus_slot_reseed": ench_formula[0] > 0,
            "enchant_first_draw_serial_r": round(ench[0], 3) if ench[0] is not None else None, "enchant_pairs": ench[1],
            "structure_palette_serial_r": round(pal[0], 3) if pal[0] is not None else None,
            "level_random_consumer_classes": lvl[0],
        })
        c.close()
        print(rows[-1], flush=True)
    with open(OUT / "cross-version.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    main()
