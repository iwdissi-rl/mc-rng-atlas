#!/usr/bin/env python3
"""In-game screenshots of scenarios: runs a family's driver script at normal speed while the headless client
(`iwdissi`, spectator) flies to the stations and takes screenshots. Several families run in parallel, each with
its own Xvfb display, client game directory and server port.

    showcase.py --families fishing,enchanting --jobs 4
    showcase.py --scene scenes/piglin-dance.txt          # a hand-written console scene instead of a family
"""
import argparse, os, re, shutil, socket, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GEN = ROOT / "scenarios/gen"
OUT = ROOT / "runs/showcase"


def free_port():
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def ensure_display(n):
    if not Path(f"/tmp/.X11-unix/X{n}").exists():
        subprocess.Popen(["Xvfb", f":{n}", "-screen", "0", "1280x720x24", "-nolisten", "tcp"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            if Path(f"/tmp/.X11-unix/X{n}").exists():
                break
            time.sleep(0.1)
    return f":{n}"


# most interesting stations per family (index into the family's fake players); default: first and last
PICK = {"enchanting": [7, 4], "fishing": [3, 7], "combat": [0, 3], "ranged": [0, 3], "trials": [0, 5], "bosses": [0, 2],
        "mobs2": [0, 1], "blocks2": [1, 6], "hazards": [2, 4], "itemuse": [5, 4]}


def cameras(drv_text, fam, limit):
    """Station centres from the driver's `player NAME x y z` lines."""
    out = []
    for m in re.finditer(r"^\d+ player (\S+) (\S+) (\S+) (\S+)$", drv_text, re.M):
        out.append((m.group(1), float(m.group(2)), float(m.group(3)), float(m.group(4))))
    if not out:  # no fake players (e.g. worldgen2): point the camera at what the script places
        for m in re.finditer(r"^\d+ cmd place (?:feature|structure) \S+ (-?\d+) (-?\d+) (-?\d+)$", drv_text, re.M):
            out.append((None, float(m.group(1)), float(m.group(2)), float(m.group(3))))
        out = out[::7] or out
    pick = PICK.get(fam, [0, len(out) - 1])
    return [out[i] for i in dict.fromkeys(pick) if -len(out) <= i < len(out)][:limit]


def family_scene(fam, seed, slot, ticks, stations):
    drv = (GEN / f"player-{fam}-{seed}.drv").read_text()
    work = OUT / fam
    work.mkdir(parents=True, exist_ok=True)
    # normal speed (no sprint), stop handled by the console script, keep only the first `ticks` ticks
    keep = [l for l in drv.splitlines() if "tick sprint" not in l and not l.endswith(" stop") and int(l.split()[0]) <= ticks]
    (work / "showcase.drv").write_text("\n".join(keep) + "\n")
    lines = [f"#exec python3 {ROOT}/tools/client.py launch --version 26.3 --server localhost:{{port}} "
             f"--display {{display}} --game-dir {{gamedir}} >/dev/null",
             "#await iwdissi joined the game", "gamemode spectator iwdissi", "effect give iwdissi minecraft:night_vision infinite 0 true",
             "#sleep 3", "#exec xdotool key --window $(xwininfo -root -tree | grep -oE '0x[0-9a-f]+ \"Minecraft [0-9]' | head -1 | cut -d' ' -f1) F1"]
    for k, (name, x, y, z) in enumerate(cameras(drv, fam, stations)):
        # stand back and above, looking at the station (HUD hidden with F1)
        if name is None:
            lines += [f"tp iwdissi {x} {y + 14} {z - 22} 0 30", "#sleep 30", f"#shot {fam}-{k}-a",
                      f"tp iwdissi {x + 12} {y + 20} {z - 18} -30 40", "#sleep 15", f"#shot {fam}-{k}-b"]
            continue
        lines += [f"execute as {name} at @s run tp iwdissi ~ ~3 ~-5 0 28", "#sleep 25", f"#shot {fam}-{k}-a",
                  f"execute as {name} at @s run tp iwdissi ~4 ~4 ~-6 -30 32", "#sleep 15", f"#shot {fam}-{k}-b"]
    (work / "scene.txt").write_text("\n".join(lines) + "\n")
    return work / "scene.txt", work / "showcase.drv"


def run_one(scene_txt, driver, name, slot, seed):
    display = ensure_display(147 + slot)
    gamedir = ROOT / f"work/client/game-{slot}"
    port = free_port()
    txt = Path(scene_txt).read_text().replace("{port}", str(port)).replace("{display}", display).replace("{gamedir}", str(gamedir))
    run_dir = OUT / name
    run_dir.mkdir(parents=True, exist_ok=True)
    final = run_dir / "scene.final.txt"
    final.write_text(txt)
    env = dict(os.environ, ATLAS_DISPLAY=display, ATLAS_CLIENT_GAMEDIR=str(gamedir))
    cmd = [sys.executable, str(ROOT / "tools/run_scenario.py"), "--scenario", str(final), "--seed", str(seed),
           "--out", str(run_dir / "server"), "--port", str(port), "--cpus", "6", "--timeout", "1500"]
    if driver:
        cmd += ["--driver", str(driver)]
    t = time.time()
    r = subprocess.run(cmd, env=env, capture_output=True, text=True)
    subprocess.run(["pkill", "-f", f"game-{slot} "], capture_output=True)  # the client for this slot
    subprocess.run(["pkill", "-f", f"gameDir {gamedir}"], capture_output=True)
    shots = sorted((run_dir / "server" / "shots").glob("*.png")) if (run_dir / "server" / "shots").exists() else []
    dest = OUT / "shots"
    dest.mkdir(exist_ok=True)
    for s in shots:
        shutil.copy(s, dest / s.name)
    return f"{name}: rc={r.returncode} {len(shots)} shots in {time.time() - t:.0f}s {r.stderr.strip()[-200:] if r.returncode else ''}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--families", default="")
    ap.add_argument("--scene", action="append", default=[], help="hand-written console scene (.txt), repeatable")
    ap.add_argument("--seed", default="42")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--ticks", type=int, default=1500)
    ap.add_argument("--stations", type=int, default=2)
    a = ap.parse_args()
    jobs = []
    for fam in [f for f in a.families.split(",") if f]:
        scene, drv = family_scene(fam, a.seed, 0, a.ticks, a.stations)
        jobs.append((scene, drv, fam))
    for sc in a.scene:
        jobs.append((sc, None, Path(sc).stem))
    with ThreadPoolExecutor(a.jobs) as ex:
        futs = [ex.submit(run_one, sc, drv, name, i % a.jobs, a.seed) for i, (sc, drv, name) in enumerate(jobs)]
        for f in futs:
            print(f.result(), flush=True)


if __name__ == "__main__":
    main()
