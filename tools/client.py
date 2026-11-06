#!/usr/bin/env python3
"""Install and launch the vanilla Minecraft client headlessly (Xvfb) as offline player `iwdissi`, connected to a
local offline-mode atlas server, to take in-game screenshots.

    client.py install --version 26.3
    client.py launch  --version 26.3 --server localhost:PORT --display :99

This is how mod development environments run the client (offline name, no account token). It only ever
connects to the local test server started by run_scenario.py (online-mode=false).
"""
import argparse, hashlib, json, os, subprocess, sys, urllib.request, zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / "work/client"
JAVA = os.environ.get("ATLAS_JAVA", "/usr/lib/jvm/java-25-openjdk-amd64/bin/java")
MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"


def fetch(url, dest, sha1=None):
    dest = Path(dest)
    if dest.exists() and (sha1 is None or hashlib.sha1(dest.read_bytes()).hexdigest() == sha1):
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(6):
        try:
            data = urllib.request.urlopen(url, timeout=120).read()
            break
        except Exception:
            if attempt == 5:
                raise
            import time
            time.sleep(2 ** attempt)
    if sha1 and hashlib.sha1(data).hexdigest() != sha1:
        raise RuntimeError(f"sha1 mismatch for {url}")
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(data)
    tmp.replace(dest)
    return dest


def allowed(rules):
    if not rules:
        return True
    ok = False
    for r in rules:
        os_ok = "os" not in r or r["os"].get("name") == "linux"
        if "features" in r:
            continue
        if os_ok:
            ok = r["action"] == "allow"
    return ok


def version_json(version):
    p = BASE / version / "version.json"
    if not p.exists():
        m = json.load(urllib.request.urlopen(MANIFEST))
        v = next(x for x in m["versions"] if x["id"] == version)
        fetch(v["url"], p, v["sha1"])
    return json.loads(p.read_text())


def install(version):
    vj = version_json(version)
    vd = BASE / version
    jobs = [(vj["downloads"]["client"]["url"], vd / "client.jar", vj["downloads"]["client"]["sha1"])]
    cp = [vd / "client.jar"]
    for lib in vj["libraries"]:
        if not allowed(lib.get("rules")):
            continue
        art = lib.get("downloads", {}).get("artifact")
        if not art:
            continue
        if ":natives-" in lib["name"] and not lib["name"].endswith(":natives-linux"):
            continue
        dest = BASE / "libraries" / art["path"]
        jobs.append((art["url"], dest, art["sha1"]))
        cp.append(dest)
    idx = vj["assetIndex"]
    idx_path = BASE / "assets/indexes" / f"{idx['id']}.json"
    fetch(idx["url"], idx_path, idx["sha1"])
    for name, o in json.loads(idx_path.read_text())["objects"].items():
        h = o["hash"]
        jobs.append((f"https://resources.download.minecraft.net/{h[:2]}/{h}", BASE / "assets/objects" / h[:2] / h, h))
    with ThreadPoolExecutor(16) as ex:
        list(ex.map(lambda j: fetch(*j), jobs))
    (vd / "classpath.txt").write_text(":".join(str(c) for c in cp))
    print(f"installed {version}: {len(cp)} classpath entries, {len(jobs)} files")


def launch(version, server, display, game_dir, width, height):
    vj = version_json(version)
    vd = BASE / version
    cp = (vd / "classpath.txt").read_text()
    natives = vd / "natives"
    natives.mkdir(exist_ok=True)
    for jar in cp.split(":"):
        if "natives-linux" in jar:
            with zipfile.ZipFile(jar) as z:
                for n in z.namelist():
                    if n.endswith(".so"):
                        (natives / Path(n).name).write_bytes(z.read(n))
    gd = Path(game_dir)
    gd.mkdir(parents=True, exist_ok=True)
    # quiet, fast, low-res settings suitable for software rendering
    (gd / "options.txt").write_text(
        f"renderDistance:6\nsimulationDistance:6\nmaxFps:30\nguiScale:2\nonboardAccessibility:false\n"
        f"skipMultiplayerWarning:true\njoinedFirstServer:true\ntutorialStep:none\npauseOnLostFocus:false\n"
        f"soundCategory_master:0.0\nnarrator:0\nfullscreen:false\noverrideWidth:{width}\noverrideHeight:{height}\n")
    # Never touch the user's real desktop: drop Wayland, force SDL onto the virtual X display, render with
    # Mesa (GLX, software) so no window can appear anywhere but Xvfb.
    env = {k: v for k, v in os.environ.items() if k not in ("WAYLAND_DISPLAY", "WAYLAND_SOCKET", "XDG_SESSION_TYPE")}
    env.update(DISPLAY=display, SDL_VIDEODRIVER="x11", SDL_VIDEO_X11_FORCE_EGL="0", SDL_VIDEO_FORCE_EGL="0",
               LIBGL_ALWAYS_SOFTWARE="1", __GLX_VENDOR_LIBRARY_NAME="mesa", VK_ICD_FILENAMES="/nonexistent")
    if not display.startswith(":") or display in (":0", ":1"):
        raise SystemExit(f"refusing to use display {display}: pass the Xvfb display, e.g. :99")
    cmd = [JAVA, "-Xmx4G", f"-Djava.library.path={natives}", f"-Dorg.lwjgl.librarypath={natives}", "-cp", cp,
           vj["mainClass"], "--username", "iwdissi", "--version", version, "--gameDir", str(gd),
           "--assetsDir", str(BASE / "assets"), "--assetIndex", vj["assetIndex"]["id"],
           "--uuid", "00000000000000000000000000000000", "--accessToken", "0", "--userType", "legacy",
           "--versionType", "release", "--width", str(width), "--height", str(height),
           "--quickPlayMultiplayer", server]
    return subprocess.Popen(cmd, env=env, stdout=open(gd / "client.log", "w"), stderr=subprocess.STDOUT)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("install")
    i.add_argument("--version", default="26.3")
    l = sub.add_parser("launch")
    l.add_argument("--version", default="26.3")
    l.add_argument("--server", required=True)
    l.add_argument("--display", default=":147")
    l.add_argument("--game-dir", default=str(BASE / "game"))
    l.add_argument("--width", type=int, default=1280)
    l.add_argument("--height", type=int, default=720)
    a = ap.parse_args()
    if a.cmd == "install":
        install(a.version)
    else:
        p = launch(a.version, a.server, a.display, a.game_dir, a.width, a.height)
        print(p.pid)


if __name__ == "__main__":
    main()
