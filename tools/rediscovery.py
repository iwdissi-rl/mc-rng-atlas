#!/usr/bin/env python3
"""Rediscovery checklist: did the atlas independently find what the Minecraft community already knows?

Each check pairs a known fact (with where it's known from) with a query over the atlas. A check passes when the
atlas data shows the same mechanism without being told about it. Writes datasets/09-rediscovery/rediscovery.csv.

    rediscovery.py [--db data/atlas.duckdb]
"""
import argparse, csv
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent

# (id, fact, known from, sql returning (value, evidence text), pass condition on value)
CHECKS = [
    ("slime-chunks", "Slime chunks are decided by a Random seeded from world seed + chunk coordinates with salt 987234911",
     "Minecraft Wiki (Slime), slime-chunk finders",
     "SELECT count(DISTINCT version), string_agg(DISTINCT version || ':' || regexp_extract(class,'[^/]+$') || '.' || method, ', ') "
     "FROM call_sites WHERE hints LIKE '%987234911%'", lambda v: v >= 5),
    ("structure-salts", "Structure placement seeds chunks with x*341873128712 + z*132897987541 + worldSeed + salt",
     "Seedfinding community (structure seed cracking)",
     "SELECT count(DISTINCT version), string_agg(DISTINCT version || ':' || regexp_extract(class,'[^/]+$') || '.' || method, ', ') "
     "FROM call_sites WHERE hints LIKE '%341873128712%' AND hints LIKE '%132897987541%'", lambda v: v >= 5),
    ("enchant-seed", "The enchanting table is reseeded from the player's enchantment seed before rolling offers",
     "Earthcomputer's enchantment cracking (clientcommands)",
     "SELECT count(DISTINCT version), string_agg(DISTINCT version, ', ') FROM call_sites "
     "WHERE class LIKE '%EnchantmentMenu' AND kind = 'reseed' AND hints LIKE '%enchantmentSeed%'", lambda v: v >= 4),
    ("enchant-reroll", "After enchanting, the enchantment seed is re-rolled from the player's own entity RNG",
     "Earthcomputer's enchantment cracking",
     "SELECT count(*), string_agg(DISTINCT root, ', ') FROM draw_roots WHERE version='{v}' "
     "AND class LIKE '%/Player' AND method = 'onEnchantmentPerformed' AND root LIKE 'field:%Entity.random'", lambda v: v >= 1),
    ("enchant-reroll-runtime", "...and that re-roll actually happens at runtime when a player enchants",
     "Earthcomputer's enchantment cracking",
     "SELECT coalesce(sum(events),0), string_agg(DISTINCT run, ', ') FROM rt_sites WHERE version='{v}' "
     "AND class LIKE '%/Player' AND method = 'onEnchantmentPerformed'", lambda v: v >= 1),
    ("level-random-shared", "One shared RNG per dimension (Level.random) drives many unrelated mechanics",
     "RNG manipulation (technical community)",
     "SELECT max(n), 'consumer classes of Level.<init> generators: ' || max(n) FROM ("
     "SELECT count(DISTINCT regexp_extract(draw_site, '^(.*)\\.[^.]+:', 1)) n FROM an_edges WHERE origin LIKE '%/Level.<init>%' GROUP BY origin)",
     lambda v: v >= 50),
    ("entity-random", "Each entity (including the player) has its own RNG used for its drops, sounds and movement",
     "Player RNG cracking (clientcommands)",
     "SELECT max(n), 'consumer classes of Entity.<init> generators: ' || max(n) FROM ("
     "SELECT count(DISTINCT regexp_extract(draw_site, '^(.*)\\.[^.]+:', 1)) n FROM an_edges WHERE origin LIKE '%/Entity.<init>%' GROUP BY origin)",
     lambda v: v >= 50),
    ("item-drop-player-rng", "Items a player throws use the player's own RNG for their motion (used to crack player RNG)",
     "clientcommands player-RNG cracking",
     "SELECT count(*), string_agg(DISTINCT regexp_extract(class,'[^/]+$') || '.' || method, ', ') FROM draw_roots WHERE version='{v}' "
     "AND (class LIKE '%/Player' OR class LIKE '%/LivingEntity') AND method ILIKE '%drop%' AND root LIKE 'field:%Entity.random'",
     lambda v: v >= 1),
    ("fishing-hook-rng", "Fishing bite timing and catches come from the fishing hook entity's own RNG",
     "Fishing manipulation research",
     "SELECT count(*), string_agg(DISTINCT method, ', ') FROM draw_roots WHERE version='{v}' "
     "AND class LIKE '%/FishingHook' AND method IN ('catchingFish', 'retrieve') AND root LIKE 'field:%Entity.random'", lambda v: v >= 1),
    ("random-ticks", "Random ticks pick positions with the dimension RNG (randomTickSpeed per section per tick)",
     "Minecraft Wiki (Tick)",
     "SELECT count(*), string_agg(DISTINCT root, ', ') FROM draw_roots WHERE version='{v}' "
     "AND class LIKE '%/ServerLevel' AND method = 'tickChunk' AND root LIKE '%Level.random'", lambda v: v >= 1),
    ("stronghold-rings", "Stronghold ring positions are seeded from the world seed (create() then setSeed)",
     "Stronghold finders / seedfinding",
     "SELECT count(*), string_agg(DISTINCT categories, ', ') FROM seed_sources WHERE version='{v}' "
     "AND method = 'generateRingPositions' AND categories LIKE '%world_seed%'", lambda v: v >= 1),
    ("end-spikes", "End pillar layout is derived from the world seed",
     "End pillar seed cracking",
     "SELECT count(*), string_agg(DISTINCT categories || ' @ ' || method, ', ') FROM seed_sources WHERE version='{v}' "
     "AND class LIKE '%EndSpikeFeature' AND (categories LIKE '%world_seed%' OR hints LIKE '%getSeed%' OR hints LIKE '%Seed%')", lambda v: v >= 1),
    ("bedrock-positional", "Bedrock floor/roof patterns come from a positional RNG (nearby positions are correlated)",
     "Bedrock-pattern seed cracking",
     "SELECT count(*), 'VerticalGradientCondition serial r=' || round(max(r),3) FROM an_seg_serial "
     "WHERE site_key LIKE '%VerticalGradientCondition%' AND r > 0.05", lambda v: v >= 1),
    ("decoration-seed", "Features in a chunk are placed with a per-chunk decoration seed derived from the world seed",
     "Dungeon / feature seed cracking",
     "SELECT count(*), string_agg(DISTINCT method, ', ') FROM call_sites WHERE version='{v}' "
     "AND class LIKE '%WorldgenRandom' AND method IN ('setDecorationSeed','setFeatureSeed','setLargeFeatureSeed','setLargeFeatureWithSalt')",
     lambda v: v >= 3),
    ("tnt-math-random-old", "Old versions used Math.random for TNT priming and dropped-item spread (unseeded, unpredictable)",
     "Technical Minecraft (TNT direction randomness)",
     "SELECT count(*), string_agg(DISTINCT regexp_extract(class,'[^/]+$') || '.' || method, ', ') FROM call_sites "
     "WHERE version='1.12.2' AND kind='global' AND target_name='random' AND (class LIKE '%TNT%' OR class LIKE '%EntityItem%')",
     lambda v: v >= 1),
    ("goat-horn-uuid", "A goat's horn variant is seeded by its UUID hash",
     "Minecraft Wiki talk page (Goat Horn)",
     "SELECT count(*), string_agg(DISTINCT categories, ', ') FROM seed_sources WHERE version='{v}' "
     "AND class LIKE '%/Goat' AND categories LIKE '%entity_identity%'", lambda v: v >= 1),
    ("piglin-dance-gametime", "Piglin victory dances are rolled with an RNG seeded by game time",
     "Mentioned in a third-party reimplementation PR (consequence not documented)",
     "SELECT count(*), string_agg(DISTINCT categories, ', ') FROM seed_sources WHERE version='{v}' "
     "AND class LIKE '%PiglinAi' AND categories LIKE '%game_time%'", lambda v: v >= 1),
    ("hunger-no-rng", "Hunger (food level, saturation, the 80-tick heal/starve timer) uses no randomness at all",
     "Minecraft Wiki (Hunger): deterministic tick timer",
     "SELECT count(*), 'RNG call sites in FoodData across all versions: ' || count(*) FROM call_sites WHERE class LIKE '%FoodData%' OR class LIKE '%FoodStats%'",
     lambda v: v == 0),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data/atlas.duckdb"))
    ap.add_argument("--out", default=str(ROOT / "datasets/09-rediscovery/rediscovery.csv"))
    ap.add_argument("--version", default="26.3", help="version for runtime/static checks that are version-specific")
    a = ap.parse_args()
    c = duckdb.connect(a.db, read_only=True)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for cid, fact, source, sql, ok in CHECKS:
        try:
            v, ev = c.execute(sql.replace("{v}", a.version)).fetchone()
            v = v or 0
            status = "rediscovered" if ok(v) else "not found"
        except Exception as e:  # a missing table (e.g. analysis not run) is a failed check, not a crash
            v, ev, status = 0, f"query failed: {e}".splitlines()[0][:200], "not found"
        rows.append({"id": cid, "status": status, "fact": fact, "known_from": source, "value": v, "evidence": (ev or "")[:400]})
        print(f"{status:<13} {cid:<24} {str(v)[:12]:>12}  {(ev or '')[:90]}")
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"{sum(r['status'] == 'rediscovered' for r in rows)}/{len(rows)} rediscovered -> {out}")


if __name__ == "__main__":
    main()
