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
| 1b | Seed provenance: classify and rank what every generator is seeded from (`atlas seeds`) | **done** (interprocedural seed tracing: todo) |
| 1c | Cross-check 1.12.2 against KillTheRNG's call-site list | todo |
| 2 | Runtime logger (Java agent) + scripted headless scenarios | **done for 26.3** (server side) |
| 3 | Confirmation engine: replay every generator's state machine, match sites to static table | **done** (chain replay); scenario coverage: in progress |
| 4 | Correlation CLI: shared-generator graph, crackability scoring, anomaly search | **done**, see [docs/stage4-findings.md](docs/stage4-findings.md) |

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

# All versions in parallel: scan + resolve + seed classification (about 1.3 s for all three)
$B scan-all --jar 26.3=26.3-client.jar --jar 1.16.1=1.16.1-named.jar --jar 1.12.2=1.12.2-named.jar

# Or one step at a time
$B scan    --version 26.3 --jar path/to/26.3-client.jar
$B resolve --version 26.3
$B seeds   --version 26.3
$B export  --out data/parquet          # sorted, zstd-compressed Parquet

# Which generators feed the most draw sites?
$B query "SELECT root, count(*) sites, count(DISTINCT class) classes
          FROM draw_roots WHERE kind='draw' GROUP BY root ORDER BY sites DESC LIMIT 20"

# Every gameplay outcome that uses unseeded global randomness
$B query "SELECT class, method, target_owner||'.'||target_name FROM call_sites WHERE kind='global'"
```

## Runtime confirmation (stage 2)

`agent/` is a Java agent for the 26.3 server. It hooks the generator primitives
(`LegacyRandomSource`/`SingleThreadedRandomSource`/`ThreadSafeLegacyRandomSource.next(bits)`,
`setSeed`, `Xoroshiro128PlusPlus.nextLong` and its constructors) and tags every call on an RNG type
with a site id at class-load time. Each event records generator id, global sequence, tick and
pre-state. `atlas ingest` replays every generator: a draw is **confirmed** when its logged pre-state
equals the state predicted from that generator's previous event, so any unlogged change shows up as
a mismatch.

```sh
agent/build.sh
python3 tools/gen_scenarios.py                       # scenarios from the jar's own data
python3 tools/run_many.py --jobs 24 --cpus 4         # parallel headless servers + ingest
crates/atlas/target/release/atlas coverage           # what the static catalog has confirmed
```

**Coverage so far** (124 runs on up to 32 parallel servers):

| | |
|---|---|
| RNG events logged | 5,908,701,550 |
| Generators observed | 323,265,227 |
| Draws confirmed by replay | 5,423,670,161 |
| Mismatches | **0** |
| Server gameplay draw sites exercised | **1,108 / 1,650 (67.2%)** |
| Client-side methods in shared classes (`animateTick`, particles, `handleEntityEvent`, …) | 36 / 370, need a client |
| Client packages | 0 / 468, need a client |

Scenario families: world generation (3 dimensions × 4 seeds), every configured feature and structure,
every loot table, every mob type, random ticks for every block, dispensers and droppers, and
fake-player families. **Fake players** (`agent/src/hook/atlas/hook/Driver.java`) are real `ServerPlayer`s
placed through an embedded connection, the way Mojang's gametest framework does it, and driven by
timed scripts. They fish (reeling on each bite), enchant at tables with 0 to 15 bookshelves, trade
with every profession and level, mine with fortune, silk touch and unbreaking tools, fight melee and
ranged (bows, crossbows, tridents), throw and eat items, barter with piglins, trigger raids, trial
spawners and vaults, breed and tame animals, fight bosses, and survive hazards (drowning, lightning,
fire, zombie sieges, breaking tools, brushing suspicious blocks), plus natural spawning with time and
weather running.

```sh
python3 tools/gen_scenarios.py && python3 tools/gen_player_scenarios.py
python3 tools/run_many.py --manifest scenarios/gen/manifest.tsv        --jobs 24 --cpus 4
python3 tools/run_many.py --manifest scenarios/gen/manifest-player.tsv --jobs 32 --cpus 4 --xmx 5G
```

Runtime sites that join with the static table: every tagged site. The only runtime-only sites are
**functional-interface draws**: a generator method passed as a function (`random::nextInt` handed to
`AbstractHorse.generateJumpStrength`, `XoroshiroRandomSource::new` stored in
`WorldgenRandom$Algorithm`). The static table lists those at the method-reference creation site,
and runtime sees them where the function is invoked.

## Correlation analysis (stage 4)

```sh
atlas analyze    # per-generator table, shared-generator graph, origins + tiers (~1 min, all cores)
atlas segments   # one parallel pass over every reseed segment: bias, serial correlation, seed collisions
atlas graph [--origin EnderDragonFight]   # which draw sites share generators from an origin
atlas crack      # crackability tiers per generator origin
atlas anomalies  # identical-stream collisions, first-draw bias, correlated consecutive reseeds
```

Headline candidates (details, numbers and caveats in [docs/stage4-findings.md](docs/stage4-findings.md)):
- **Piglin victory dances come in time windows.** They're seeded with consecutive game times, so "dance"
  ticks cluster into windows up to 51 s long with gaps up to 23 min. Still 10% overall, but not independent per kill.
- **Enchanting-table slots share their first level modifier.** Seeds `s, s+1, s+2` give the same first
  modifier on all three slots 99.5% of the time for enchantability 12–15 items (6.2% if independent).
- **Structure template palettes and processors are spatially correlated** (r = 0.47 between consecutive placements).

## Data model

| Table | One row per |
|---|---|
| `rng_types` | class that is an RNG type, with the root interface it descends from |
| `rng_fields` | field holding a generator (`is_static` = process-wide) |
| `call_sites` | RNG-related instruction: kind, target, generator identity, seed hints |
| `draw_roots` | (call site, possible root generator) edge |
| `seed_sources` | seed-taking site with seed categories (`game_time`, `entity_identity`, `position`, `world_seed`, `constant`, `wall_clock`, …) and an unusualness score |
| `generator_roots` (view) | root generator with draw/reseed/fork counts and whether it is seeded, unseeded or process-global |
| `classes`, `methods` | hierarchy used for dispatch resolution |
| `rng_invokes` | every call on an RNG type with its per-method ordinal and bytecode offset (joins runtime tags to static sites) |
| `rt_runs`, `rt_sites` | per runtime run / per runtime call site: events, confirmed, mismatched, generators |
| `data/events/<run>/*.parquet` | every runtime event (`gen, seq, tick, type, a, b, bits, site, verdict, output`), not committed |

## Early findings (26.3)

- `Entity.random` (976 draw sites) and `Level.random` (827) carry most gameplay randomness.
- Enchanting: `EnchantmentMenu.random` is reseeded from `Player.enchantmentSeed`, which is
  re-rolled from the player's own `Entity.random` in `Player.onEnchantmentPerformed`. This is the
  known chain behind enchantment cracking, found by the scanner with no prior hints.
- Piglin victory dances (`PiglinAi.wantsToDance`) are seeded with the world's **game time**, not the
  piglin's own generator. Every piglin that decides in the same tick gets the same roll, so on a
  given tick either all eligible piglins dance or none do. Anyone who knows the game time can predict it.
- `FishingHook.syncronizedRandom` is reseeded every tick with `UUID.getLeastSignificantBits() ^ gameTime`.
  The client knows both values, so it can reproduce the generator (it keeps the bobber's motion in sync).
  The catch itself uses the hook's own `random`, which is not reseeded.
- Goat horn variants are seeded with the goat's `UUID.hashCode()`, so they're fixed per goat.
- Mob AI in `InsideBrownianWalk` and `LongJumpToRandomPos` uses `Collections.shuffle` with the JDK's
  global unseeded generator, so those outcomes can't be predicted from any game seed.

## Early findings (older versions)

- **1.16.1 has a process-wide `static Item.random`** shared by every item. Bytecode check:
  underwater bone meal (`BoneMealItem.growWaterPlant`: seagrass and coral placement) draws from
  it directly, while land bone meal (`growCrop`) uses `Level.random`. Other flows the resolver
  reports into world features are over-approximated by dispatch and still need runtime confirmation.
- **Use of `Math.random` dropped over time.** Outside client code: 29 sites in 1.12.2 (XP orb and
  item-drop motion, TNT fuse direction, `EntityLivingBase.<init>`, mob spawning), 16 in 1.16.1 and 2
  in 26.3. In old versions those outcomes come from the JVM's single unseeded global generator.

## Credits / prior work

KillTheRNG (MinecraftTAS), Earthcomputer's clientcommands and RNG research, SeedcrackerX, the
seedfinding community and Minecraft@Home. This project builds on what they found.
