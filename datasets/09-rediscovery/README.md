# Rediscovery: does the atlas find what the community already knows?

A sanity check of the whole pipeline: known facts about Minecraft randomness (slime chunks, structure salts, enchantment seeds, player RNG, bedrock patterns, …), each checked automatically against the atlas data, which was produced without being told about any of them.

## In simple terms

The atlas was tested to see if it could independently find randomness facts the Minecraft community already knew. It successfully rediscovered all 18 of these known facts without being told about them beforehand. This includes details on slime chunks, structure salts, enchantment seeds, and player randomness. The test also confirmed patterns for bedrock, end spikes, and various other game mechanics.

<sub>Written by a local LLM (Qwen3.8-Flash-Next via llama.cpp) from the key numbers below and automatically checked: every number in it appears in those facts. The numbers below are authoritative.</sub>

## Key numbers

- 18 of 18 community-known facts rediscovered independently by the atlas
- slime-chunks: rediscovered
- structure-salts: rediscovered
- enchant-seed: rediscovered
- enchant-reroll: rediscovered
- enchant-reroll-runtime: rediscovered
- level-random-shared: rediscovered
- entity-random: rediscovered
- item-drop-player-rng: rediscovered
- fishing-hook-rng: rediscovered
- random-ticks: rediscovered
- stronghold-rings: rediscovered
- end-spikes: rediscovered
- bedrock-positional: rediscovered
- decoration-seed: rediscovered
- tnt-math-random-old: rediscovered
- goat-horn-uuid: rediscovered
- piglin-dance-gametime: rediscovered
- hunger-no-rng: rediscovered

## How this was produced

`tools/rediscovery.py`: one SQL query per fact over the static catalog, seed sources, runtime graph and segment statistics.

## Files

- [`rediscovery.csv`](rediscovery.csv): one row per check with the evidence found

| Check | Known fact | Known from | Atlas result |
|---|---|---|---|
| ✅ `slime-chunks` | Slime chunks are decided by a Random seeded from world seed + chunk coordinates with salt 987234911 | Minecraft Wiki (Slime), slime-chunk finders | 1.16.1:Slime.checkSlimeSpawnRules, 26.3:Slime.checkSlimeSpawnRules, 1.12.2:EntitySlime.getCanSpawnHere, 26.1.2:Slime.checkSlimeSpawnRules, 1 |
| ✅ `structure-salts` | Structure placement seeds chunks with x*341873128712 + z*132897987541 + worldSeed + salt | Seedfinding community (structure seed cracking) | 26.3:WorldgenRandom.setLargeFeatureWithSalt, 1.12.2:ChunkGeneratorOverworld.generateChunk, 1.12.2:ChunkGeneratorEnd.generateChunk, 1.12.2:Ch |
| ✅ `enchant-seed` | The enchanting table is reseeded from the player's enchantment seed before rolling offers | Earthcomputer's enchantment cracking (clientcommands) | 26.3, 1.21.11, 26.2, 26.1.2, 1.16.1 |
| ✅ `enchant-reroll` | After enchanting, the enchantment seed is re-rolled from the player's own entity RNG | Earthcomputer's enchantment cracking | field:net/minecraft/world/entity/Entity.random |
| ✅ `enchant-reroll-runtime` | ...and that re-roll actually happens at runtime when a player enchants | Earthcomputer's enchantment cracking | player-enchanting-1337, drv-test, player-enchanting-8675309, player-enchanting--4172144997902289642, player-enchanting-42 |
| ✅ `level-random-shared` | One shared RNG per dimension (Level.random) drives many unrelated mechanics | RNG manipulation (technical community) | consumer classes of Level.<init> generators: 230 |
| ✅ `entity-random` | Each entity (including the player) has its own RNG used for its drops, sounds and movement | Player RNG cracking (clientcommands) | consumer classes of Entity.<init> generators: 168 |
| ✅ `item-drop-player-rng` | Items a player throws use the player's own RNG for their motion (used to crack player RNG) | clientcommands player-RNG cracking | LivingEntity.createItemStackToDrop |
| ✅ `fishing-hook-rng` | Fishing bite timing and catches come from the fishing hook entity's own RNG | Fishing manipulation research | retrieve, catchingFish |
| ✅ `random-ticks` | Random ticks pick positions with the dimension RNG (randomTickSpeed per section per tick) | Minecraft Wiki (Tick) | field:net/minecraft/world/level/Level.random |
| ✅ `stronghold-rings` | Stronghold ring positions are seeded from the world seed (create() then setSeed) | Stronghold finders / seedfinding | world_seed |
| ✅ `end-spikes` | End pillar layout is derived from the world seed | End pillar seed cracking | world_seed @ getSpikesForLevel |
| ✅ `bedrock-positional` | Bedrock floor/roof patterns come from a positional RNG (nearby positions are correlated) | Bedrock-pattern seed cracking | VerticalGradientCondition serial r=0.119 |
| ✅ `decoration-seed` | Features in a chunk are placed with a per-chunk decoration seed derived from the world seed | Dungeon / feature seed cracking | setLargeFeatureSeed, setFeatureSeed, setDecorationSeed, setLargeFeatureWithSalt |
| ✅ `tnt-math-random-old` | Old versions used Math.random for TNT priming and dropped-item spread (unseeded, unpredictable) | Technical Minecraft (TNT direction randomness) | EntityItem.<init>, EntityTNTPrimed.<init> |
| ✅ `goat-horn-uuid` | A goat's horn variant is seeded by its UUID hash | Minecraft Wiki talk page (Goat Horn) | entity_identity,string_hash |
| ✅ `piglin-dance-gametime` | Piglin victory dances are rolled with an RNG seeded by game time | Mentioned in a third-party reimplementation PR (consequence not documented) | game_time |
| ✅ `hunger-no-rng` | Hunger (food level, saturation, the 80-tick heal/starve timer) uses no randomness at all | Minecraft Wiki (Hunger): deterministic tick timer | RNG call sites in FoodData across all versions: 0 |
