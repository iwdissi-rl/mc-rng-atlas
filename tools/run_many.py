#!/usr/bin/env python3
"""Run every scenario in a manifest on parallel headless servers, then ingest + confirm each run.

    run_many.py --manifest scenarios/gen/manifest.tsv --jobs 24 --cpus 4

Servers run concurrently (each limited to --cpus cores so worker pools stay small). Ingestion writes to
one DuckDB file, so it happens in this process, one run at a time, as each server finishes.
"""
import argparse, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ATLAS = ROOT / "crates/atlas/target/release/atlas"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default=str(ROOT / "scenarios/gen/manifest.tsv"))
    ap.add_argument("--jobs", type=int, default=24)
    ap.add_argument("--cpus", type=int, default=4)
    ap.add_argument("--xmx", default="4G")
    ap.add_argument("--runs", default=str(ROOT / "runs"))
    ap.add_argument("--only", default="", help="substring filter on scenario names")
    ap.add_argument("--keep-raw", action="store_true", help="keep raw .bin logs after ingest")
    a = ap.parse_args()

    todo = []
    for line in Path(a.manifest).read_text().splitlines():
        scen, seed = line.split("\t")
        if a.only in Path(scen).stem:
            todo.append((Path(scen), seed))
    print(f"{len(todo)} scenarios, {a.jobs} parallel servers x {a.cpus} cores", flush=True)

    def run(scen, seed):
        out = Path(a.runs) / scen.stem
        t = time.time()
        r = subprocess.run([sys.executable, str(ROOT / "tools/run_scenario.py"), "--scenario", str(scen), "--seed", seed,
                            "--out", str(out), "--cpus", str(a.cpus), "--xmx", a.xmx], capture_output=True, text=True)
        return scen.stem, out, r.returncode, (r.stdout + r.stderr).strip().splitlines()[-1:] or [""], time.time() - t

    t0, ok, failed = time.time(), 0, []
    with ThreadPoolExecutor(a.jobs) as ex:
        futs = [ex.submit(run, s, seed) for s, seed in todo]
        for f in as_completed(futs):
            name, out, rc, last, dt = f.result()
            if rc != 0:
                failed.append(name)
                print(f"FAIL {name} ({dt:.0f}s): {last[0]}", flush=True)
                continue
            ing = subprocess.run([str(ATLAS), "ingest", str(out), "--name", name], cwd=ROOT, capture_output=True, text=True)
            if ing.returncode != 0:
                failed.append(name)
                print(f"INGEST FAIL {name}: {ing.stderr.strip()[-300:]}", flush=True)
                continue
            ok += 1
            if not a.keep_raw:
                for b in (out / "atlas").glob("*.bin"):
                    b.unlink()
            print(f"[{ok + len(failed)}/{len(todo)}] {dt:5.0f}s  {ing.stdout.strip()}", flush=True)
    print(f"done: {ok} ok, {len(failed)} failed in {time.time() - t0:.0f}s {failed if failed else ''}")


if __name__ == "__main__":
    main()
