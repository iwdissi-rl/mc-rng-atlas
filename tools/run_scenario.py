#!/usr/bin/env python3
"""Run one headless Minecraft server with the atlas agent and a scripted command scenario.

Scenario file: one server command per line. Directives:
    #sleep SECONDS       pause (lets ticks run)
    #await TEXT          wait until TEXT appears in the server log
    # anything else      comment
The server is stopped with `stop` at the end so the agent flushes its logs.

    run_scenario.py --scenario scenarios/smoke.txt --seed 42 --out runs/smoke-42
"""
import argparse, os, shutil, subprocess, sys, threading, time, zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JAVA = os.environ.get("ATLAS_JAVA", "/usr/lib/jvm/java-25-openjdk-amd64/bin/java")
# RNG type list from the static scan; the agent tags every call on these types. Regenerated if missing.
RNG_TYPES = ROOT / "agent/rng-types-26.3.txt"


def ensure_rng_types():
    if not RNG_TYPES.exists():
        q = subprocess.run([str(ROOT / "crates/atlas/target/release/atlas"), "query",
                            "SELECT class FROM rng_types WHERE version='26.3' ORDER BY class"],
                           cwd=ROOT, capture_output=True, text=True, check=True)
        RNG_TYPES.write_text("\n".join(q.stdout.splitlines()[1:]) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--seed", default="42")
    ap.add_argument("--out", required=True, help="run directory (server files + atlas logs)")
    ap.add_argument("--jar", default=str(ROOT / "work/versions/26.3-server.jar"))
    ap.add_argument("--port", type=int, default=0, help="0 = derive from seed")
    ap.add_argument("--xmx", default="4G")
    ap.add_argument("--cpus", type=int, default=0, help="limit the JVM's visible cores (sizes worker pools); 0 = all")
    ap.add_argument("--timeout", type=int, default=1800)
    a = ap.parse_args()

    ensure_rng_types()
    out = Path(a.out).resolve()
    if out.exists():
        shutil.rmtree(out)
    srv = out / "server"
    srv.mkdir(parents=True)
    port = a.port or 30000 + (zlib.crc32(f"{a.seed}{a.scenario}{out}".encode()) % 20000)
    (srv / "eula.txt").write_text("eula=true\n")
    (srv / "server.properties").write_text(
        f"level-seed={a.seed}\nserver-port={port}\nonline-mode=false\nenable-query=false\n"
        "enable-rcon=false\nspawn-protection=0\nsync-chunk-writes=false\nmax-tick-time=-1\n"
        "view-distance=4\nsimulation-distance=4\n"
    )
    cmd = [
        JAVA, f"-Xmx{a.xmx}", "-XX:+UseParallelGC",
        *([f"-XX:ActiveProcessorCount={a.cpus}"] if a.cpus else []),
        f"-javaagent:{ROOT}/agent/build/atlas-agent.jar={ROOT}/agent/build/atlas-hook.jar",
        f"-Datlas.out={out / 'atlas'}",
        f"-Datlas.rngTypes={RNG_TYPES}",
        "-jar", a.jar, "nogui",
    ]
    log = open(out / "server.log", "w")
    p = subprocess.Popen(cmd, cwd=srv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    lines, cond = [], threading.Condition()

    def reader():
        for line in p.stdout:
            log.write(line)
            log.flush()
            with cond:
                lines.append(line)
                cond.notify_all()

    threading.Thread(target=reader, daemon=True).start()
    deadline = time.time() + a.timeout

    def await_text(text):
        with cond:
            while not any(text in l for l in lines):
                if p.poll() is not None or time.time() > deadline:
                    raise SystemExit(f"server exited or timed out waiting for {text!r}; see {out/'server.log'}")
                cond.wait(1)

    def send(c):
        p.stdin.write(c + "\n")
        p.stdin.flush()

    t0 = time.time()
    await_text("Done (")
    for raw in Path(a.scenario).read_text().splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#sleep"):
            time.sleep(float(line.split()[1]))
        elif line.startswith("#await"):
            await_text(line.split(None, 1)[1])
        elif line.startswith("#"):
            continue
        else:
            send(line)
    send("stop")
    p.wait(timeout=max(60, deadline - time.time()))
    meta = (out / "atlas" / "meta.txt")
    print(f"{out.name}: exit={p.returncode} in {time.time()-t0:.1f}s; " + (meta.read_text().replace("\n", " ") if meta.exists() else "no atlas meta"))
    sys.exit(p.returncode)


if __name__ == "__main__":
    main()
