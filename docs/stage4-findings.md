# Stage 4 findings (26.3)

Source: 124 headless runs, 5,908,701,550 logged RNG events, 323,165,976 generators, 467,994,315 reseed
segments. Every draw was confirmed by state-machine replay (0 mismatches). Reproduce with:

```sh
atlas analyze && atlas segments && atlas graph && atlas crack && atlas anomalies
```

`analyze` takes about 61 s and `segments` about 2.5 min (192 threads).

**Status labels.** *Known* = documented by others or the basis of existing tools. *Expected* = follows
directly from the design and is not exploitable beyond what's known. *Candidate* = confirmed in our data
and bytecode, and we could not find it written up. "Could not find" only covers web search; Discord and
source-comment knowledge isn't searchable, so check with the seedfinding / technical communities
before calling anything new.

## Candidates

All four candidates were re-measured on 26.1.2, 26.2 and 26.3 with the same results (see
[datasets/12-cross-version](../datasets/12-cross-version/)).

### 1. Piglin victory dances come in time windows, not as independent 10% rolls
`PiglinAi.wantsToDance` uses `RandomSource.createThreadLocalInstance(level.getGameTime()).nextFloat() < 0.1f`.
The LCG's first output after `setSeed(t)` changes by only about 2^-13.5 of its range per tick, so
consecutive ticks give nearly the same roll. Replaying the exact arithmetic over 40 in-game days: 10.00%
of ticks are "dance" ticks, but they form windows up to **1,024 ticks (51 s)**, separated by gaps up to
**27,408 ticks (23 min)**. Every hoglin kill in a window makes all eligible piglins dance; outside it
none do. Anyone who knows the game time (`/time query gametime`) can predict it.
(The game-time seed itself is mentioned in a third-party reimplementation PR; the windowing consequence
was not found anywhere.)

### 2. The three enchanting-table offers share their first level modifier
`EnchantmentMenu.getEnchantmentList` seeds `setSeed(enchantmentSeed + slot)` for slots 0, 1, 2. The first
draw in `EnchantmentHelper.selectEnchantment` is `nextInt(enchantability / 4 + 1)`. For power-of-two
bounds, Java's `nextInt` takes the high bits, which barely move between seeds s, s+1, s+2. Runtime data:
lag-1 correlation of that first draw across consecutive reseeds **r = 0.917** (519k pairs). Exact
arithmetic over 200k random enchantment seeds:

| enchantability (bound) | P(all 3 slots get the same first modifier) | if independent |
|---|---|---|
| 12–15 (bound 4): iron/netherite/wood tools, leather/chain/netherite armor | **0.995** | 0.062 |
| 4–7 (bound 2) | ~1.0 | 0.25 |
| 10 (bound 3): diamond | 0.210 | 0.111 |
| 22 (bound 6): gold tools | **0.000** (never all equal) | 0.028 |

Enchantability values are the long-standing vanilla ones, not re-read from 26.3.

### 3. Structure template palettes and processors are spatially correlated
`StructurePlaceSettings.getRandomPalette` is fed from `getRandom(pos)` = `RandomSource.create(Mth.getSeed(pos))`
when no random is set. First draw after seeding: chi-square **z = 6,168** over 6.4M segments
(strongly non-uniform), and lag-1 correlation **r = 0.47** between consecutive placements.
`RuleProcessor.processBlock` reuses the same seeds (identical-stream collision). Palette and
processor choices for nearby template placements are therefore not independent.

### 4. The same chunk seeds feed several unrelated generation steps
Identical seed values are consumed by `CaveWorldCarver.isStartChunk`, `MineshaftStructure.findGenerationPoint`,
`RuinedPortalStructure.findGenerationPoint`, `NaturalSpawner.spawnMobsForChunkGeneration`, `Util.getRandom` and
legacy structure probability reducers (`an_seg_collisions`: up to 8 different draw sites from one
seed value). Their first draws are therefore identical, so outcomes are coupled (e.g. cave start vs.
mineshaft placement in the same chunk). This is probably familiar to seedfinders; listed as a
candidate only because we found no write-up.

## Expected / known (filtered out of the candidate list)

- **World-seed-only reseeds** (`NoiseBasedChunkGenerator.generateCarvers`, `applyBiomeDecoration`,
  `spawnOriginalMobs`, `Structure$GenerationContext.makeRandom`): `setSeed(worldSeed)` then `nextLong`, so
  the first draw is constant per world (4 to 6 histogram buckets, r = 1.0). That's the design of large-feature seeding.
- **Structure spacing** (`RandomSpreadType.evaluate`, r = 0.14 to 0.52) and **positional rules**
  (`VerticalGradientCondition` r = 0.18 over 239M pairs, `OreVeinRule` r = 0.20, aquifers r = 0.12):
  neighbouring chunks or positions give correlated first draws. This is what structure and bedrock seed
  cracking rely on (known).
- **`Level.random`** (one generator per dimension) feeds 434 draw sites in 196 classes, and **`Entity.random`**
  417 sites in 138 classes. Both are 48-bit LCGs with long streams (tier B), the basis of known RNG
  manipulation.

## Method notes and limits

- **Crackability tiers** are heuristics: A = seeded from public inputs (game time, entity identity,
  position, constants); B = 48-bit LCG with multi-draw streams; B- = 48-bit LCG, mostly single-draw
  streams; C = 128-bit Xoroshiro. They do not model what a player can actually observe.
- **Bias** = chi-square of the top byte of the first draw after each (re)seed, per consuming site
  (Wilson–Hilferty z, df 255). **Serial correlation** = Pearson r between first draws of consecutive
  segments at the same site, ordered by global event sequence.
- Coverage: 67.2% of server gameplay draw sites were exercised. Unexercised sites aren't analysed.
