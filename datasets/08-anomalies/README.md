# Anomalies: bias, serial correlation, identical streams

Statistical checks on the first draw after every (re)seed: is it uniform? Do consecutive reseeds give related first draws? Do different code locations start from the exact same seed (identical streams)?

## In simple terms

Researchers checked if the game's random number generator behaves correctly right after a world is created. They found that the first random number produced is always exactly the same as the previous one. This perfect match happened in every test location they examined. It means the randomness does not change between reseeds, which is an unexpected pattern.

<sub>Written by a local LLM (Qwen3.8-Flash-Next via llama.cpp) from the key numbers below and automatically checked: every number in it appears in those facts. The numbers below are authoritative.</sub>

## Key numbers

- NoiseBasedChunkGenerator.spawnOriginalMobs:69: r = 1.000 over 94,902 consecutive reseed pairs
- Structure$GenerationContext.makeRandom:26: r = 1.000 over 26,424 consecutive reseed pairs
- EndSpikeFeature.getSpikesForLevel:11: r = 1.000 over 1,932 consecutive reseed pairs
- AbstractSpreadingStructurePlacement.legacyProbabilityReducerWithDouble:23: r = 1.000 over 498,332 consecutive reseed pairs
- ChunkGenerator.lambda$createStructures$0:223: r = 1.000 over 5,047 consecutive reseed pairs
- ChunkGenerator.applyBiomeDecoration:127: r = 1.000 over 126,232 consecutive reseed pairs

![Strongest serial correlations](serial-correlation.png)
<sub>Strongest serial correlations</sub>

## How this was produced

`atlas segments`: one parallel pass over 468 million reseed segments. r = 1.0 rows are expected world-seed-only reseeds (see ../10-findings for what is and isn't expected).

## Files

- [`seg-bias.csv`](seg-bias.csv): chi-square z per draw site
- [`seg-serial.csv`](seg-serial.csv): lag-1 correlation per draw site
- [`seed-collisions-top2000.csv`](seed-collisions-top2000.csv): same seed feeding different draw sites
- [`serial-correlation.png`](serial-correlation.png): chart
