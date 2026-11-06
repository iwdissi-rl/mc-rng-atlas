# Comparison charts

| Chart | What it compares |
|---|---|
| ![](coverage-by-version.png) | Share of server gameplay RNG code exercised at runtime, 26.1.2 vs 26.2 vs 26.3 |
| ![](draw-sites-by-version.png) | RNG draw sites in the bytecode, 1.12.2 → 26.3 |
| ![](math-random-by-version.png) | Unseeded `Math.random()` calls outside client code, per version |
| ![](shared-generators.png) | Generators shared by the most different parts of the game (26.3) |
| ![](serial-correlation.png) | Strongest "nearby seeds → nearby outputs" correlations (26.3) |
| ![](tiers.png) | How predictable each generator origin is (26.3) |
| ![](seed-categories-26.3.png) | What generators are seeded from (26.3) |
| ![](coverage.png) | Server vs client RNG code exercised (26.3) |

Numbers per version: [cross-version.csv](cross-version.csv).
