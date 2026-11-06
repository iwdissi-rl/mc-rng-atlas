# Finding: one chunk seed feeds unrelated generation steps

Several world-generation decisions in the same chunk start from the exact same seed value, so their first random numbers are identical and their outcomes are coupled.

## In simple terms

Several world-generation decisions within the same chunk start from the exact same seed value. This causes their first random numbers to be identical, which links their outcomes together. For example, cave carving, mineshaft placement, ruined portal location, and mob spawning can all share this starting point. The current status of this finding is a candidate that is probably familiar to seedfinders.

<sub>Written by a local LLM (Qwen3.8-Flash-Next via llama.cpp) from the key numbers below and automatically checked: every number in it appears in those facts. The numbers below are authoritative.</sub>

## Key numbers

- 200 distinct seed values feed several unrelated draw sites, up to 23 sites per seed
- examples: CaveWorldCarver.isStartChunk, MineshaftStructure.findGenerationPoint, RuinedPortalStructure.findGenerationPoint, NaturalSpawner.spawnMobsForChunkGeneration
- status: candidate, probably familiar to seedfinders

## How this was produced

`an_seg_collisions`: identical seed values reaching different draw sites across all runs.

## Files

- [`collisions.csv`](collisions.csv): seed values and the draw sites they feed
