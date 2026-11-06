# Database sample

A small slice of the full ~500 GB database (Parquet): 2.56 M RNG events from 29 runs on 26.3 (complete
generator streams, one run per scenario family) plus the lookup and analysis tables.

`events/*.parquet`: `gen` generator, `seq` order, `tick`, `type` (1 LCG draw, 2 setSeed, 3 Xoroshiro draw,
4 Xoroshiro init), `a`/`b` state before, `bits`, `site` (→ `sites.parquet`), `verdict` (1 = replay-confirmed), `output`.

```sh
duckdb -c "SELECT s.class, s.method, count(*) FROM 'events/*.parquet' e JOIN 'sites.parquet' s ON s.run = e.run AND s.site_id = e.site GROUP BY ALL ORDER BY 3 DESC LIMIT 20"
```
