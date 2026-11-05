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
| 1 | Static bytecode scanner + interprocedural root resolver | **done: 26.3, 1.16.1, 1.12.2** |
| 1c | Cross-check 1.12.2 against KillTheRNG's call-site list | todo |
| 2 | Runtime logger (Java agent) + scripted headless scenarios | todo |
| 3 | Confirmation engine: predict next N outputs from logged state, compare | todo |
| 4 | Correlation CLI: shared-generator graph, crackability scoring, anomaly search | todo |

| Version | Names from | Classes | RNG types | Generator fields | Draw sites | Global (`Math.random` etc.) |
|---|---|---|---|---|---|---|
| 26.3 | unobfuscated jar | 11,383 | 16 | 51 | 2,492 | 26 |
| 1.16.1 | Mojang official mappings | 5,463 | 8 | 47 | 2,280 | 85 |
| 1.12.2 | MCP `stable_39` (Forge names) | 3,310 | 7 | 41 | 2,003 | 84 |

Each draw is traced to a root generator. Unresolved edges, mostly lambdas called through functional
interfaces and a few cycles, are kept and flagged for review. Static edges are *possible* flows:
virtual dispatch makes them over-approximate, and stage 2 confirms which ones actually happen.

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

## Getting readable jars

26.3+ ships unobfuscated. Older versions are remapped with `tools/remap/Remap.java` (tiny-remapper + mapping-io + ASM):

```sh
CP=$(ls work/tools/*.jar | tr '\n' ':')
java -cp "$CP" tools/remap/Remap.java proguard 1.16.1-client.jar 1.16.1-named.jar 1.16.1-client_mappings.txt
java -cp "$CP" tools/remap/Remap.java mcp 1.12.2-client.jar 1.12.2-named.jar joined.srg fields.csv methods.csv
```

Jars come from Mojang's version manifest. Mojang mappings come from the version JSON. MCP comes from
`maven.minecraftforge.net` (`de.oceanlabs.mcp:mcp:1.12.2:srg`, `mcp_stable:39-1.12`). None are committed.

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

## Early findings (older versions)

- **1.16.1 has a process-wide `static Item.random`** shared by every item. `BoneMealItem` passes it
  into feature placement, so bone-meal growth draws from one global generator. Flows into specific
  feature types are over-approximated by dispatch and still need runtime confirmation.
- **Use of `Math.random` dropped over time.** Outside client code: 29 sites in 1.12.2 (XP orb and
  item-drop motion, TNT fuse direction, `EntityLivingBase.<init>`, mob spawning), 16 in 1.16.1 and 2
  in 26.3. In old versions those outcomes come from the JVM's single unseeded global generator.

## Credits / prior work

KillTheRNG (MinecraftTAS), Earthcomputer's clientcommands and RNG research, SeedcrackerX, the
seedfinding community and Minecraft@Home. This project builds on what they found.
