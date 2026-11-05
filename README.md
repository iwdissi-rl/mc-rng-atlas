# Minecraft RNG Atlas

A map of every source of randomness in Minecraft: Java Edition. For each one it records where it is
seeded, which generator it draws from, what else shares that generator, and whether a player can
observe or predict it.

Most existing work covers one source in depth (seed cracking, enchanting, fishing), or lists call
sites for one old version ([KillTheRNG](https://github.com/MinecraftTAS/KillTheRNG), 1.12.2). The
atlas aims to cover every source, across versions, confirmed against the running game.

> **Scope:** this is documentation and research. The repo ships the catalog, the data and analysis
> tools. It does **not** ship client mods for cracking seeds or predicting outcomes on multiplayer
> servers. No Mojang code, jars or decompiled source are committed, only names and our own analysis.

## Status

| Stage | What | Status |
|---|---|---|
| 1 | Static bytecode scanner + interprocedural root resolver | **26.3 done** |
| 1b | Same for 1.16.1 (Mojang mappings) and 1.12.2 (community mappings) | todo |
| 2 | Runtime logger (Java agent) + scripted headless scenarios | todo |
| 3 | Confirmation engine: predict next N outputs from logged state, compare | todo |
| 4 | Correlation CLI: shared-generator graph, crackability scoring, anomaly search | todo |

**26.3 snapshot:** 11,383 classes, 16 RNG types, 51 generator fields, 2,492 draw sites,
resolving to about 96% concrete root generators. The remainder is lambdas called through
functional interfaces, and a few cycles, flagged for review.

## How it works

1. **Scan** (`atlas scan`) walks every method's bytecode. It finds RNG types through the jar's own
   class hierarchy (anything implementing `RandomSource`, `java.util.Random`, `RandomGenerator`, …),
   every generator field, and every call site:
   - `draw`: `nextInt`, `nextFloat`, …, including method references like `random::nextFloat`
   - `construct`, `reseed`, `fork`, `derive`: where generators are created or reseeded, with hints
     about the seed source (e.g. `get EnchantmentMenu.enchantmentSeed`)
   - `passthrough`: a generator handed to another method, mapped to the parameter it lands in
   - `global`: unseeded process-wide randomness: `Math.random`, `UUID.randomUUID`,
     `Collections.shuffle(list)`
2. **Resolve** (`atlas resolve`) traces each draw back to its root generator. It follows parameters
   through every caller (with virtual dispatch over the class hierarchy), getters through what they
   return, lambda captures into the lambda bodies, and inherited fields to their declaring class.
   Roots look like:
   - `field:net/minecraft/world/entity/Entity.random`: one generator per entity
   - `field:net/minecraft/world/level/Level.random`: one shared generator per dimension
   - `factory:RandomSource.create@EnderDragonFight.spawnExitPortal`: a fresh generator built at that call
   - `at(forkPositional(...))`: positional generators derived from a parent

## Usage

```sh
cargo build --release --manifest-path crates/atlas/Cargo.toml
B=crates/atlas/target/release/atlas

$B scan    --version 26.3 --jar path/to/26.3-client.jar
$B resolve --version 26.3
$B export  --out data/parquet          # sorted, zstd-compressed Parquet

# Which generators feed the most draw sites?
$B query "SELECT root, count(*) sites, count(DISTINCT class) classes
          FROM draw_roots WHERE kind='draw' GROUP BY root ORDER BY sites DESC LIMIT 20"

# Every gameplay outcome that uses unseeded global randomness
$B query "SELECT class, method, target_owner||'.'||target_name FROM call_sites WHERE kind='global'"
```

## Data model

| Table | One row per |
|---|---|
| `rng_types` | class that is an RNG type, with the root interface it descends from |
| `rng_fields` | field holding a generator (`is_static` = process-wide) |
| `call_sites` | RNG-related instruction: kind, target, generator identity, seed hints |
| `draw_roots` | (call site, possible root generator) edge |
| `classes`, `methods` | hierarchy used for dispatch resolution |

## Early findings (26.3)

- `Entity.random` (976 draw sites) and `Level.random` (827) carry most gameplay randomness.
- Enchanting: `EnchantmentMenu.random` is reseeded from `Player.enchantmentSeed`, which is
  re-rolled from the player's own `Entity.random` in `Player.onEnchantmentPerformed`. This is the
  known chain behind enchantment cracking, found by the scanner with no prior hints.
- Mob AI in `InsideBrownianWalk` and `LongJumpToRandomPos` uses `Collections.shuffle` with the JDK's
  global unseeded generator, so those outcomes can't be predicted from any game seed.

## Credits / prior work

KillTheRNG (MinecraftTAS), Earthcomputer's clientcommands and RNG research, SeedcrackerX, the
seedfinding community and Minecraft@Home. This project builds on what they found.
