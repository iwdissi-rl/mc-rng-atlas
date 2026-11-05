#!/usr/bin/env python3
"""Generate scenario shards that exercise as much of the game's RNG as possible without a player.

Content lists (features, structures, loot tables, mobs) come from the version jar's own data, so the
scenarios track the game automatically. Writes scenarios/gen/<family>-<n>.txt and scenarios/gen/manifest.tsv
(scenario path, world seed).

    gen_scenarios.py --jar work/versions/26.3-client.jar
"""
import argparse, zipfile, zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEEDS = [42, 1337, -4172144997902289642, 8675309]
SPRINT = "#await Sprint completed"


def ids(names, prefix):
    out = []
    for n in names:
        if n.startswith(prefix) and n.endswith(".json"):
            out.append(n[len(prefix):-5])
    return sorted(out)


def chunks(xs, n):
    return [xs[i:i + n] for i in range(0, len(xs), n)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jar", default=str(ROOT / "work/versions/26.3-client.jar"))
    ap.add_argument("--out", default=str(ROOT / "scenarios/gen"))
    a = ap.parse_args()
    names = zipfile.ZipFile(a.jar).namelist()
    features = ids(names, "data/minecraft/worldgen/feature/")
    structures = ids(names, "data/minecraft/worldgen/structure/")
    loot = [t for t in ids(names, "data/minecraft/loot_table/")]
    mobs = [m for m in ids(names, "data/minecraft/loot_table/entities/") if "/" not in m and m not in ("player", "mannequin")]

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for f in out.glob("*.txt"):
        f.unlink()
    manifest = []

    def write(name, lines, seed=42):
        p = out / f"{name}.txt"
        p.write_text("\n".join(lines) + "\n")
        manifest.append((p, seed))

    base = ["gamerule spawn_mobs false"]  # keep each scenario focused; mobs get their own family

    # 1. World generation in each dimension, several seeds: 16x16 chunks each.
    for seed in SEEDS:
        for dim in ("overworld", "the_nether", "the_end"):
            write(f"worldgen-{dim}-{seed}", base + [
                f"execute in minecraft:{dim} run forceload add -128 -128 127 127",
                "tick sprint 400", SPRINT,
            ], seed)

    # 2. Configured features placed on the surface, 30 per shard, each in its own chunk.
    for i, part in enumerate(chunks(features, 30)):
        lines = base + ["forceload add 0 0 1023 31"]
        for j, f in enumerate(part):
            x = 16 + j * 32
            lines.append(f"execute positioned {x} 0 16 positioned over world_surface run place feature minecraft:{f}")
        lines += ["tick sprint 100", SPRINT]
        write(f"features-{i}", lines)

    # 3. Structures, 6 per shard (they are large), far apart.
    for i, part in enumerate(chunks(structures, 6)):
        lines = list(base)
        for j, s in enumerate(part):
            x = j * 256  # 8x8 forceloaded chunks per structure (forceload caps at 256 chunks per command)
            lines.append(f"forceload add {x - 64} -64 {x + 63} 63")
            lines.append(f"place structure minecraft:{s} {x} 64 0")
        lines += ["tick sprint 100", SPRINT]
        write(f"structures-{i}", lines)

    # 4. Loot tables, 120 per shard, rolled 3 times each.
    for i, part in enumerate(chunks(loot, 120)):
        lines = base + ["forceload add 0 0 15 15", "fill 0 -64 0 15 -60 15 minecraft:stone"]
        for t in part:
            for _ in range(3):
                lines.append(f"loot spawn 8 -59 8 loot minecraft:{t}")
        lines += ["kill @e[type=item]", "tick sprint 20", SPRINT]
        write(f"loot-{i}", lines)

    # 5. Mobs: summon each type 3x in a walled arena, let AI run, then kill (death loot, XP).
    for i, part in enumerate(chunks(mobs, 25)):
        lines = base + [
            "forceload add -32 -32 31 31",
            "fill -30 99 -30 30 99 30 minecraft:grass_block",
            "fill -31 100 -31 31 104 31 minecraft:glass hollow",
            "fill -30 100 -30 30 104 30 minecraft:air",
        ]
        for m in part:
            for k in range(3):
                lines.append(f"summon minecraft:{m} {(k - 1) * 8} 101 {(zlib.crc32(m.encode()) % 40) - 20}")
        lines += ["tick sprint 1200", SPRINT, "kill @e[type=!item,type=!experience_orb]", "tick sprint 40", SPRINT]
        write(f"mobs-{i}", lines)

    # 6. Misc systems: random ticks, weather/lightning, /random, spreadplayers.
    write("misc-0", base + [
        "forceload add -64 -64 63 63",
        "gamerule random_tick_speed 2000",
        "weather thunder",
        *[f"random roll 1..{n}" for n in (2, 6, 20, 100, 1000)],
        *[f"random value 1..{n} minecraft:atlas/seq{n}" for n in (2, 6, 20)],
        "summon minecraft:pig 0 100 0", "summon minecraft:pig 4 100 0", "summon minecraft:pig 8 100 0",
        "spreadplayers 0 0 5 40 false @e[type=pig]",
        "tick sprint 1200", SPRINT,
    ])

    (out / "manifest.tsv").write_text("".join(f"{p}\t{s}\n" for p, s in manifest))
    fam = {}
    for p, _ in manifest:
        fam[p.stem.split("-")[0]] = fam.get(p.stem.split("-")[0], 0) + 1
    print(f"{len(manifest)} scenarios: {fam}; {len(features)} features, {len(structures)} structures, {len(loot)} loot tables, {len(mobs)} mobs")


if __name__ == "__main__":
    main()
