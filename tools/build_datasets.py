#!/usr/bin/env python3
"""Build datasets/: one folder per dataset with data files, charts, in-game screenshots and a README.

READMEs have two parts:
  * a deterministic part generated from the data (what it is, how it was made, columns, exact key numbers)
  * "In simple terms": written by a local LLM (OpenAI-compatible endpoint, default the user's llama-server on
    127.0.0.1:8181) from the key numbers only, then fact-checked: every number in the text must appear in the
    facts, otherwise it is regenerated; after 4 failures the section is omitted.

    build_datasets.py [--db data/atlas.duckdb] [--llm http://127.0.0.1:8181] [--only 10-findings]
"""
import argparse, json, re, shutil, textwrap, urllib.request
from pathlib import Path

import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent
DS = ROOT / "datasets"
SHOTS = ROOT / "runs/showcase/shots"
VERSIONS = ["1.12.2", "1.16.1", "1.21.11", "26.1.2", "26.2", "26.3"]

# reference palette (light): surface, text, categorical slots in fixed order, sequential blue
SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a"]
plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE, "axes.edgecolor": GRID,
    "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2, "text.color": INK, "axes.grid": True,
    "grid.color": GRID, "grid.linewidth": 0.8, "axes.spines.top": False, "axes.spines.right": False,
    "axes.spines.left": False, "font.size": 10, "axes.titlesize": 12, "axes.titleweight": "bold",
    "axes.titlelocation": "left", "lines.linewidth": 2, "figure.dpi": 100,
})

LLM_URL = "http://127.0.0.1:8181"
LLM_STATS = {"ok": 0, "retried": 0, "omitted": 0}


# ---------------- LLM plain-language summaries (fact-checked) ----------------

def numbers_in(text):
    return {n.replace(",", "") for n in re.findall(r"\d[\d,]*\.?\d*", text)}


def llm_summary(title, facts, context):
    fact_text = "\n".join(f"- {f}" for f in facts)
    allowed = numbers_in(fact_text + " " + context)
    prompt = (
        "You explain Minecraft technical research to ordinary players. Write 3 to 5 short, plain sentences "
        "explaining the dataset below to someone who plays Minecraft but doesn't program. Use ONLY the facts given; "
        "do not add numbers that are not in the facts; do not invent mechanics; no headings, no bullet points, "
        "no hype. If something is uncertain in the facts, say so. If the facts contain a 'status:' line, state that "
        "status accurately in your own words (for example, do not call something known if the status says it was not "
        "found written up).\n\n"
        f"Dataset: {title}\nContext: {context}\nFacts:\n{fact_text}\n")
    for attempt in range(4):
        body = json.dumps({"messages": [{"role": "user", "content": prompt}], "max_tokens": 320, "temperature": 0.2 + 0.1 * attempt,
                           "chat_template_kwargs": {"enable_thinking": False}}).encode()
        try:
            req = urllib.request.Request(f"{LLM_URL}/v1/chat/completions", data=body, headers={"Content-Type": "application/json"})
            text = json.load(urllib.request.urlopen(req, timeout=180))["choices"][0]["message"]["content"].strip()
        except Exception as e:
            print(f"  LLM error: {e}")
            continue
        bad = {n for n in numbers_in(text) if n not in allowed and not (n.isdigit() and int(n) <= 10)}
        if text and not bad:
            LLM_STATS["ok"] += 1
            LLM_STATS["retried"] += attempt > 0
            return text
        prompt += f"\n(Your previous answer used numbers not in the facts: {sorted(bad)[:6]}. Use only numbers from the facts.)"
    LLM_STATS["omitted"] += 1
    return None


# ---------------- README writer ----------------

def write_readme(folder, title, what, how, facts, files, images=(), context="", extra=""):
    folder.mkdir(parents=True, exist_ok=True)
    simple = llm_summary(title, facts, context or what) if LLM_URL else None
    md = [f"# {title}", "", what.strip(), ""]
    if simple:
        md += ["## In simple terms", "", simple, "",
               "<sub>Written by a local LLM (Qwen3.8-Flash-Next via llama.cpp) from the key numbers below and "
               "automatically checked: every number in it appears in those facts. The numbers below are authoritative.</sub>", ""]
    md += ["## Key numbers", ""] + [f"- {f}" for f in facts] + [""]
    for img, cap in images:
        md += [f"![{cap}]({img})", f"<sub>{cap}</sub>", ""]
    md += ["## How this was produced", "", how.strip(), "", "## Files", ""]
    md += [f"- [`{f}`]({f}): {d}" for f, d in files] + [""]
    if extra:
        md += [extra.strip(), ""]
    (folder / "README.md").write_text("\n".join(md))


def save(fig, path):
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def copy_shot(name, folder, new=None):
    src = SHOTS / name
    if src.exists():
        (folder / "screenshots").mkdir(parents=True, exist_ok=True)
        from PIL import Image
        dst = folder / "screenshots" / Path(new or name).with_suffix(".jpg").name
        Image.open(src).convert("RGB").save(dst, "JPEG", quality=85, optimize=True)
        return f"screenshots/{dst.name}"
    return None


def fmt(n):
    return f"{int(n):,}" if float(n).is_integer() else f"{n:,.3f}"


# ---------------- datasets ----------------

def ds_static(c):
    f = DS / "01-static-catalog"
    f.mkdir(parents=True, exist_ok=True)
    kinds = c.execute("""SELECT version, kind, count(*) n FROM call_sites GROUP BY ALL ORDER BY version, kind""").df()
    kinds.to_csv(f / "sites-by-kind.csv", index=False)
    summary = c.execute("""SELECT v.version,
          (SELECT count(*) FROM classes c WHERE c.version=v.version) classes,
          (SELECT count(*) FROM rng_types t WHERE t.version=v.version) rng_types,
          (SELECT count(*) FROM rng_fields t WHERE t.version=v.version) rng_fields,
          (SELECT count(*) FROM call_sites s WHERE s.version=v.version AND kind='draw') draw_sites,
          (SELECT count(*) FROM call_sites s WHERE s.version=v.version AND kind='global') global_sites
        FROM (SELECT DISTINCT version FROM call_sites) v""").df()
    summary["order"] = summary.version.map({v: i for i, v in enumerate(VERSIONS)})
    summary = summary.sort_values("order").drop(columns="order")
    summary.to_csv(f / "summary.csv", index=False)
    c.execute("""COPY (SELECT version, class, method, bci, kind, target_owner || '.' || target_name AS target, receiver
                 FROM call_sites WHERE kind IN ('draw','construct','reseed','fork','derive','global') ORDER BY version, class, method, bci)
                 TO '%s' (HEADER)""" % (f / "call-sites.csv"))
    fig, ax = plt.subplots(figsize=(8, 3.6))
    ax.bar(summary.version, summary.draw_sites, color=SERIES[0], width=0.55)
    for x, y in zip(summary.version, summary.draw_sites):
        ax.text(x, y, f"{y:,}", ha="center", va="bottom", color=INK2, fontsize=9)
    ax.set_title("RNG draw sites found in the game's bytecode, per version")
    ax.set_ylabel("draw call sites")
    save(fig, f / "draw-sites-by-version.png")
    facts = [f"{r.version}: {r.classes:,} classes, {r.rng_types} RNG types, {r.rng_fields} generator fields, "
             f"{r.draw_sites:,} draw sites, {r.global_sites} unseeded global calls" for r in summary.itertuples()]
    write_readme(f, "Static catalog: every RNG call site in six versions",
                 "Every place in Minecraft: Java Edition's code that draws a random number, creates or reseeds a random "
                 "generator, or uses unseeded global randomness (`Math.random`, `UUID.randomUUID`, `Collections.shuffle`).",
                 "`atlas scan-all` parses every class file of the client jar (26.x are unobfuscated; 1.21.11 and 1.16.1 "
                 "remapped with Mojang's mappings, 1.12.2 with MCP `stable_39`). No game code is included, only "
                 "class/method names, bytecode offsets and classifications.",
                 facts,
                 [("summary.csv", "per-version totals"), ("sites-by-kind.csv", "call sites per kind per version"),
                  ("call-sites.csv", "every draw/construct/reseed/fork/derive/global call site (version, class, method, bytecode offset, target, generator identity)"),
                  ("draw-sites-by-version.png", "chart")],
                 [("draw-sites-by-version.png", "Draw call sites per version")],
                 context="A static scan reads the game's compiled code without running it.")


def ds_seeds(c):
    f = DS / "02-seed-sources"
    f.mkdir(parents=True, exist_ok=True)
    c.execute("COPY (SELECT version, class, method, bci, kind, target, categories, score, hints FROM seed_sources ORDER BY version, score DESC) TO '%s' (HEADER)" % (f / "seed-sources.csv"))
    cats = c.execute("""SELECT version, cat, count(*) n FROM (SELECT version, unnest(string_split(categories, ',')) cat FROM seed_sources) GROUP BY ALL""").df()
    top = c.execute("""SELECT regexp_extract(class,'[^/]+$') || '.' || method site, categories, score FROM seed_sources
                       WHERE version='26.3' AND class NOT LIKE '%/client/%' ORDER BY score DESC, site LIMIT 8""").df()
    t26 = cats[cats.version == "26.3"].sort_values("n")
    fig, ax = plt.subplots(figsize=(8, 3.8))
    ax.barh(t26.cat, t26.n, color=SERIES[0], height=0.55)
    ax.set_title("What generators are seeded from (26.3)")
    ax.set_xlabel("seed-taking call sites")
    save(fig, f / "seed-categories-26.3.png")
    facts = [f"26.3 has {int(t26.n.sum())} seed-category assignments across {len(c.execute(chr(83)+'ELECT 1 FROM seed_sources WHERE version=' + chr(39) + '26.3' + chr(39)).fetchall())} seed-taking call sites"]
    facts += [f"26.3 seeds from {r.cat}: {r.n} sites" for r in t26.sort_values("n", ascending=False).itertuples()]
    facts += [f"most unusual: {r.site} seeded from {r.categories} (score {r.score})" for r in top.head(5).itertuples()]
    write_readme(f, "Seed sources: what every generator is seeded from",
                 "Every place a generator is created with a seed, reseeded, or derived from a position, classified by "
                 "what feeds the seed (world seed, game time, entity UUID, position, constants, the clock, …) and scored "
                 "for how unusual that is.",
                 "`atlas seeds` reads the instructions that compute the seed argument (scoped to the statement and "
                 "followed through local variables) and classifies them by keyword. `unknown` mostly means the seed "
                 "comes in as a method argument; interprocedural seed tracing is future work.",
                 facts, [("seed-sources.csv", "all versions, highest score first"), ("seed-categories-26.3.png", "chart")],
                 [("seed-categories-26.3.png", "Seed categories in 26.3")])


def ds_versions(c):
    f = DS / "03-version-diffs"
    f.mkdir(parents=True, exist_ok=True)
    # compare by (class, method, target): bytecode offsets shift between versions
    rows = []
    pairs = list(zip(VERSIONS[1:], VERSIONS[2:]))
    for a, b in pairs:
        r = c.execute(f"""
          WITH A AS (SELECT DISTINCT class, method, target_owner || '.' || target_name t FROM call_sites WHERE version='{a}' AND kind='draw'),
               B AS (SELECT DISTINCT class, method, target_owner || '.' || target_name t FROM call_sites WHERE version='{b}' AND kind='draw')
          SELECT (SELECT count(*) FROM B EXCEPT SELECT count(*) FROM A WHERE false) total_b,
                 (SELECT count(*) FROM (SELECT * FROM B EXCEPT SELECT * FROM A)) added,
                 (SELECT count(*) FROM (SELECT * FROM A EXCEPT SELECT * FROM B)) removed""").fetchone()
        rows.append((a, b, *r))
        c.execute(f"""COPY (SELECT '{b}' version, 'added' change, * FROM (
                 SELECT DISTINCT class, method, target_owner || '.' || target_name target FROM call_sites WHERE version='{b}' AND kind='draw'
                 EXCEPT SELECT DISTINCT class, method, target_owner || '.' || target_name FROM call_sites WHERE version='{a}' AND kind='draw')
               UNION ALL SELECT '{b}', 'removed', * FROM (
                 SELECT DISTINCT class, method, target_owner || '.' || target_name FROM call_sites WHERE version='{a}' AND kind='draw'
                 EXCEPT SELECT DISTINCT class, method, target_owner || '.' || target_name FROM call_sites WHERE version='{b}' AND kind='draw')
               ORDER BY change, class, method) TO '{f}/diff-{a}-to-{b}.csv' (HEADER)""")
    glob = c.execute("SELECT version, count(*) FROM call_sites WHERE kind='global' AND target_name='random' AND class NOT LIKE '%/client/%' AND class NOT LIKE 'com/mojang/%' GROUP BY 1").fetchall()
    g = dict(glob)
    fig, ax = plt.subplots(figsize=(8, 3.4))
    xs = [v for v in VERSIONS if v in g]
    ax.plot(xs, [g[v] for v in xs], color=SERIES[0], marker="o", markersize=8)
    for x in xs:
        ax.annotate(str(g[x]), (x, g[x]), textcoords="offset points", xytext=(0, 8), ha="center", color=INK2)
    ax.set_title("Math.random() calls outside client code, per version")
    ax.set_ylim(0, max(g.values()) * 1.25)
    save(fig, f / "math-random-by-version.png")
    facts = [f"{a} -> {b}: {added} draw sites added, {removed} removed (compared by class, method and called RNG method)" for a, b, _, added, removed in rows]
    facts += [f"Math.random outside client code: " + ", ".join(f"{v} {g[v]}" for v in xs)]
    write_readme(f, "Version differences", "How RNG usage changed between versions: draw sites added and removed, and "
                 "the move away from unseeded `Math.random`.",
                 "Draw sites are compared by (class, method, called RNG method), because bytecode offsets shift between "
                 "versions. Renames between mapping sets (MCP for 1.12.2) make the older comparisons noisier.",
                 facts, [(f"diff-{a}-to-{b}.csv", f"added/removed draw sites {a} -> {b}") for a, b in pairs] +
                 [("math-random-by-version.png", "chart")], [("math-random-by-version.png", "Math.random calls per version")])


def ds_runtime(c):
    f = DS / "04-runtime-runs"
    f.mkdir(parents=True, exist_ok=True)
    runs = c.execute("SELECT run, version, events, generators, confirmed, mismatched FROM rt_runs ORDER BY version, run").df()
    runs.to_csv(f / "runs.csv", index=False)
    runs["family"] = runs.run.str.replace(r"^v[0-9.]+-", "", regex=True).str.replace(r"-(-?\d+)$", "", regex=True).str.replace(r"^(player-[a-z0-9]+).*", r"\1", regex=True).str.replace(r"^(worldgen|features|structures|loot|mobs|blocks|redstone)-.*", r"\1", regex=True)
    fam = runs[runs.version == "26.3"].groupby("family").events.sum().sort_values()
    fig, ax = plt.subplots(figsize=(8, 6))
    ax.barh(fam.index, fam.values / 1e6, color=SERIES[0], height=0.6)
    ax.set_title("RNG events logged per scenario family (26.3)")
    ax.set_xlabel("million events")
    save(fig, f / "events-by-family.png")
    by_v = runs.groupby("version").agg(runs=("run", "count"), events=("events", "sum"), confirmed=("confirmed", "sum"), mismatched=("mismatched", "sum"))
    facts = [f"{v}: {r.runs} runs, {fmt(r.events)} events, {fmt(r.confirmed)} draws confirmed by replay, {fmt(r.mismatched)} mismatches"
             for v, r in by_v.iterrows()]
    write_readme(f, "Runtime runs: every RNG event, replayed and confirmed",
                 "Headless servers ran scripted scenarios (world generation, every feature/structure/loot table/mob, and "
                 "fake players named `iwdissi_*` fishing, enchanting, trading, fighting, breeding, …) with a Java agent "
                 "logging every random draw. Each generator's log was replayed: a draw is *confirmed* when its state "
                 "matches the state predicted from the previous draw.",
                 "`tools/run_many.py` (parallel servers) + `atlas ingest`. Raw events (~43 bytes each) are kept as Parquet "
                 "locally and not committed (hundreds of GB); this folder has the per-run summary.",
                 facts, [("runs.csv", "one row per run"), ("events-by-family.png", "chart")],
                 [("events-by-family.png", "Events per scenario family")])


def ds_coverage(c):
    f = DS / "05-coverage"
    f.mkdir(parents=True, exist_ok=True)
    df = c.execute("""
      WITH rt AS (SELECT DISTINCT class, method, bci FROM rt_sites WHERE version='26.3'),
      st AS (SELECT DISTINCT class, method, bci,
               CASE WHEN class LIKE 'net/minecraft/client/%' OR class LIKE 'com/mojang/%' THEN 'client package'
                    WHEN method SIMILAR TO '.*(animateTick|[Pp]article|tickClient|clientTick|handleEntityEvent|playIdleSounds|emit.*|addEating.*|makeDrown.*|makePoof.*|spawnBubbles|spawnTamingParticles|animationTick|onFlap|playGallopSound|playChestEquipsSound).*' THEN 'client-side method'
                    ELSE 'server gameplay' END scope
             FROM call_sites WHERE version='26.3' AND kind='draw')
      SELECT st.*, rt.class IS NOT NULL exercised FROM st LEFT JOIN rt USING (class, method, bci)""").df()
    df.to_csv(f / "draw-site-coverage-26.3.csv", index=False)
    s = df.groupby("scope").exercised.agg(["count", "sum"])
    s["pct"] = (100 * s["sum"] / s["count"]).round(1)
    fig, ax = plt.subplots(figsize=(8, 2.8))
    order = ["server gameplay", "client-side method", "client package"]
    s = s.reindex(order)
    ax.barh(order[::-1], s["count"][::-1], color=GRID, height=0.55)
    ax.barh(order[::-1], s["sum"][::-1], color=SERIES[0], height=0.55)
    for i, sc in enumerate(order[::-1]):
        ax.text(s.loc[sc, "count"], i, f"  {int(s.loc[sc, 'sum'])}/{int(s.loc[sc, 'count'])} ({s.loc[sc, 'pct']}%)", va="center", color=INK2)
    ax.set_title("Static draw sites exercised at runtime (26.3)")
    ax.set_xlim(0, s["count"].max() * 1.35)
    save(fig, f / "coverage.png")
    facts = [f"{sc}: {int(s.loc[sc, 'sum'])} of {int(s.loc[sc, 'count'])} draw sites exercised ({s.loc[sc, 'pct']}%)" for sc in order]
    write_readme(f, "Coverage: which RNG code actually ran", "Which of the statically found draw sites were exercised by "
                 "the runtime scenarios. Client code (particles, rendering, ambient sounds) only runs in a game client.",
                 "Join of the static `call_sites` table with runtime `rt_sites` on (class, method, original bytecode offset).",
                 facts, [("draw-site-coverage-26.3.csv", "every 26.3 draw site with scope and whether it ran"), ("coverage.png", "chart")],
                 [("coverage.png", "Exercised vs total draw sites")])


def ds_graph(c):
    f = DS / "06-generator-graph"
    f.mkdir(parents=True, exist_ok=True)
    c.execute("COPY (SELECT * FROM an_edges ORDER BY origin, events DESC) TO '%s' (HEADER)" % (f / "edges.csv"))
    top = c.execute("""SELECT regexp_replace(origin, '^net/minecraft/', '') origin, count(*) sites,
                        count(DISTINCT regexp_extract(draw_site, '^(.*)\\.[^.]+:', 1)) classes, sum(events) draws
                       FROM an_edges GROUP BY origin ORDER BY classes DESC LIMIT 10""").df()
    fig, ax = plt.subplots(figsize=(9, 4))
    t = top.iloc[::-1]
    ax.barh([o.split("/")[-1][:48] for o in t.origin], t.classes, color=SERIES[0], height=0.6)
    ax.set_title("Generators shared by the most different classes (runtime)")
    ax.set_xlabel("distinct classes drawing from generators created here")
    save(fig, f / "shared-generators.png")
    facts = [f"{r.origin}: generators created here feed {r.sites} draw sites in {r.classes} classes ({fmt(r.draws)} draws)" for r in top.head(6).itertuples()]
    write_readme(f, "Shared-generator graph", "Which random generators are shared between which parts of the game, measured "
                 "at runtime: each generator's creation site (origin) is linked to every draw site that consumed it. "
                 "Outcomes that draw from the same generator are correlated.",
                 "`atlas analyze`: per-generator first event = origin; edges = (origin, draw site) with event and generator counts.",
                 facts, [("edges.csv", "origin -> draw site edges"), ("shared-generators.png", "chart")],
                 [("shared-generators.png", "Most widely shared generator origins")])


def ds_crack(c):
    f = DS / "07-crackability"
    f.mkdir(parents=True, exist_ok=True)
    c.execute("COPY (SELECT * FROM an_origins ORDER BY tier, origin) TO '%s' (HEADER)" % (f / "origins.csv"))
    t = c.execute("SELECT tier, count(*) origins, sum(generators) generators, sum(draws) draws FROM an_origins GROUP BY tier ORDER BY tier").df()
    t.to_csv(f / "tiers.csv", index=False)
    fig, ax = plt.subplots(figsize=(8, 3))
    ax.barh(t.tier[::-1], t.origins[::-1], color=SERIES[0], height=0.55)
    ax.set_title("Generator origins per crackability tier")
    ax.set_xlabel("origins")
    save(fig, f / "tiers.png")
    facts = [f"{r.tier}: {r.origins} origins, {fmt(r.generators)} generators, {fmt(r.draws)} draws" for r in t.itertuples()]
    write_readme(f, "Crackability tiers", "A rough tier per generator origin: how hard it would be to predict its outputs.",
                 "Heuristic from state size (48-bit LCG vs 128-bit Xoroshiro), seed source, and stream length. It does "
                 "not model what a player can actually observe.",
                 facts, [("origins.csv", "per-origin statistics, seed categories and tier"), ("tiers.csv", "tier totals"), ("tiers.png", "chart")],
                 [("tiers.png", "Origins per tier")])


def ds_anomalies(c):
    f = DS / "08-anomalies"
    f.mkdir(parents=True, exist_ok=True)
    for t in ("an_seg_bias", "an_seg_serial"):
        c.execute(f"COPY (SELECT * FROM {t} ORDER BY z DESC) TO '{f}/{t[3:].replace('_', '-')}.csv' (HEADER)")
    c.execute(f"COPY (SELECT * FROM an_seg_collisions ORDER BY sites DESC, segments DESC LIMIT 2000) TO '{f}/seed-collisions-top2000.csv' (HEADER)")
    s = c.execute("SELECT regexp_extract(site_key, '([^/]+)$', 1) s, pairs, r FROM an_seg_serial ORDER BY abs(r) DESC LIMIT 12").df()
    fig, ax = plt.subplots(figsize=(9, 4.2))
    t = s.iloc[::-1]
    ax.barh([x[:52] for x in t.s], t.r, color=SERIES[0], height=0.6)
    ax.set_xlim(0, 1.05)
    ax.set_title("Consecutive reseeds -> correlated first draws (Pearson r)")
    save(fig, f / "serial-correlation.png")
    facts = [f"{r.s}: r = {r.r:.3f} over {fmt(r.pairs)} consecutive reseed pairs" for r in s.head(6).itertuples()]
    write_readme(f, "Anomalies: bias, serial correlation, identical streams",
                 "Statistical checks on the first draw after every (re)seed: is it uniform? Do consecutive reseeds give "
                 "related first draws? Do different code locations start from the exact same seed (identical streams)?",
                 "`atlas segments`: one parallel pass over 468 million reseed segments. r = 1.0 rows are expected "
                 "world-seed-only reseeds (see ../10-findings for what is and isn't expected).",
                 facts, [("seg-bias.csv", "chi-square z per draw site"), ("seg-serial.csv", "lag-1 correlation per draw site"),
                         ("seed-collisions-top2000.csv", "same seed feeding different draw sites"), ("serial-correlation.png", "chart")],
                 [("serial-correlation.png", "Strongest serial correlations")])


def ds_rediscovery(c):
    import csv
    f = DS / "09-rediscovery"
    rows = list(csv.DictReader(open(f / "rediscovery.csv")))
    n = sum(r["status"] == "rediscovered" for r in rows)
    table = ["| Check | Known fact | Known from | Atlas result |", "|---|---|---|---|"]
    for r in rows:
        mark = "✅" if r["status"] == "rediscovered" else "❌"
        table.append(f"| {mark} `{r['id']}` | {r['fact']} | {r['known_from']} | {r['evidence'][:140].replace('|', '/')} |")
    facts = [f"{n} of {len(rows)} community-known facts rediscovered independently by the atlas"]
    facts += [f"{r['id']}: {r['status']}" for r in rows]
    write_readme(f, "Rediscovery: does the atlas find what the community already knows?",
                 "A sanity check of the whole pipeline: known facts about Minecraft randomness (slime chunks, structure "
                 "salts, enchantment seeds, player RNG, bedrock patterns, …), each checked automatically against the "
                 "atlas data, which was produced without being told about any of them.",
                 "`tools/rediscovery.py`: one SQL query per fact over the static catalog, seed sources, runtime "
                 "graph and segment statistics.",
                 facts, [("rediscovery.csv", "one row per check with the evidence found")], extra="\n".join(table))


def ds_findings(c):
    base = DS / "10-findings"
    # 1. piglin dance windows: exact game arithmetic over 40 in-game days
    f = base / "piglin-dance-windows"
    f.mkdir(parents=True, exist_ok=True)
    M, A, MASK = 0x5DEECE66D, 0xB, (1 << 48) - 1
    N = 24000 * 40
    dance = []
    for t in range(N):
        s = ((t ^ M) & MASK) * M + A & MASK
        dance.append((s >> 24) / (1 << 24) < 0.1)
    runs, cur, val, start = [], 1, dance[0], 0
    for i in range(1, N):
        if dance[i] == val:
            cur += 1
        else:
            runs.append((start, val, cur)); start, val, cur = i, dance[i], 1
    runs.append((start, val, cur))
    with open(f / "dance-windows.csv", "w") as o:
        o.write("start_tick,dance,length_ticks\n" + "".join(f"{s},{int(v)},{n}\n" for s, v, n in runs))
    on = [n for _, v, n in runs if v]
    off = [n for _, v, n in runs if not v]
    fig, ax = plt.subplots(figsize=(10, 2.6))
    day = 24000 * 2
    for s, v, n in runs:
        if v and s < day:
            ax.axvspan(s / 1200, (s + n) / 1200, color=SERIES[1], lw=0)
    ax.set_xlim(0, day / 1200)
    ax.set_yticks([])
    ax.grid(False)
    ax.set_xlabel("minutes of game time (first 2 in-game days)")
    ax.set_title("When a hoglin kill makes piglins dance (orange = dance window)")
    save(fig, f / "dance-timeline.png")
    shot = copy_shot("piglin-scene.png", f) if (SHOTS / "piglin-scene.png").exists() else None
    facts = [f"code rule: dance if RandomSource.createThreadLocalInstance(gameTime).nextFloat() < 0.1",
             f"fraction of game ticks that are dance ticks over 40 in-game days: {sum(dance) / N:.4f}",
             f"dance windows: {len(on)}, mean {sum(on) / len(on):.0f} ticks, longest {max(on)} ticks ({max(on) / 20:.0f} seconds)",
             f"gaps without dancing: mean {sum(off) / len(off):.0f} ticks, longest {max(off)} ticks ({max(off) / 1200:.0f} minutes)",
             "status: candidate (seed source mentioned in a third-party PR; the windowing consequence not found documented)"]
    write_readme(f, "Finding: piglin victory dances come in time windows",
                 "Piglins that kill a hoglin dance 10% of the time, but the roll is seeded with the world's game time. "
                 "Consecutive game times give nearly the same first random number, so dance ticks cluster into windows.",
                 "Bytecode of `PiglinAi.wantsToDance` (26.3) + replaying the game's exact LCG arithmetic "
                 "(`SingleThreadedRandomSource`: setSeed scramble, `next(24)`) for every tick of 40 in-game days.",
                 facts, [("dance-windows.csv", "every dance / no-dance window (start tick, length)"), ("dance-timeline.png", "chart")],
                 [("dance-timeline.png", "Dance windows over two in-game days")] + ([(shot, "Test scene: piglin and hoglin (in-game, client `iwdissi`)")] if shot else []),
                 context="A hoglin kill only makes piglins dance during these windows; anyone who knows the game time can predict it.")

    # 2. enchanting slots
    f = base / "enchanting-slot-correlation"
    f.mkdir(parents=True, exist_ok=True)
    import random
    rnd = random.Random(1)

    def first_nextint(seed, bound):
        s = (seed ^ M) & MASK
        while True:
            s = (s * M + A) & MASK
            r = s >> 17
            if bound & (bound - 1) == 0:
                return (bound * r) >> 31
            v = r % bound
            if r - v + (bound - 1) < (1 << 31):
                return v
    rows = []
    for ench in range(1, 26):
        bound = ench // 4 + 1
        same = sum(len({first_nextint(s + i, bound) for i in range(3)}) == 1 for s in (rnd.randrange(-2**31, 2**31) for _ in range(20000)))
        rows.append((ench, bound, same / 20000, 1 / bound ** 2))
    with open(f / "same-modifier-probability.csv", "w") as o:
        o.write("enchantability,bound,p_all_three_slots_same,p_if_independent\n" + "".join(f"{a},{b},{p:.4f},{q:.4f}\n" for a, b, p, q in rows))
    fig, ax = plt.subplots(figsize=(9, 3.6))
    ax.plot([r[0] for r in rows], [r[2] for r in rows], color=SERIES[0], marker="o", markersize=8, label="measured (game arithmetic)")
    ax.plot([r[0] for r in rows], [r[3] for r in rows], color=SERIES[1], marker="o", markersize=8, label="if the 3 slots were independent")
    ax.set_xlabel("item enchantability")
    ax.set_ylabel("P(all 3 slots same first modifier)")
    ax.set_title("Enchanting-table slots share their first level modifier")
    ax.legend(frameon=False)
    save(fig, f / "same-modifier.png")
    serial = c.execute("SELECT r, pairs FROM an_seg_serial WHERE site_key LIKE '%EnchantmentHelper.selectEnchantment%' ORDER BY pairs DESC LIMIT 1").fetchone()
    shot = copy_shot("enchanting-0-a.png", f)
    b4 = [r for r in rows if r[1] == 4][0]
    facts = ["code: EnchantmentMenu.getEnchantmentList calls setSeed(enchantmentSeed + slot) for slots 0, 1, 2",
             "first draw in selectEnchantment: nextInt(enchantability / 4 + 1)",
             f"runtime: lag-1 correlation of that first draw across consecutive reseeds r = {serial[0]:.3f} over {serial[1]:,} pairs" if serial else "runtime correlation: n/a",
             f"enchantability 12-15 (bound 4): all three slots share the first modifier {b4[2]:.3f} of the time vs {b4[3]:.4f} if independent",
             f"enchantability 22-23 (bound 6): {[r for r in rows if r[0] == 22][0][2]:.3f} vs {1 / 36:.4f} if independent",
             "status: candidate (seed + slot formula is known to enchantment crackers; this consequence not found written up)"]
    write_readme(f, "Finding: the three enchanting-table offers share their first level modifier",
                 "The table seeds each offered slot with the player's enchantment seed plus 0, 1 or 2. Java's random "
                 "generator gives almost the same first number for neighbouring seeds, and for power-of-two ranges "
                 "`nextInt` uses exactly those high bits.",
                 "Bytecode of `EnchantmentMenu.getEnchantmentList` / `EnchantmentHelper.selectEnchantment` (26.3), runtime "
                 "segment statistics from fake player `iwdissi_ench*` enchanting at tables with 0–15 bookshelves, and the "
                 "exact game arithmetic over 20,000 random enchantment seeds per enchantability.",
                 facts, [("same-modifier-probability.csv", "probability per enchantability"), ("same-modifier.png", "chart")],
                 [("same-modifier.png", "Probability that all three slots share the first modifier")] + ([(shot, "Fake player `iwdissi_ench7` at a 15-bookshelf table (in-game)")] if shot else []),
                 context="Enchantability values used are the long-standing vanilla ones; not re-read from 26.3.")

    # 3. structure palettes and 4. shared chunk seeds (runtime statistics)
    f = base / "structure-palette-correlation"
    f.mkdir(parents=True, exist_ok=True)
    pal = c.execute("SELECT site_key, pairs, r FROM an_seg_serial WHERE site_key LIKE '%StructurePlaceSettings%' ORDER BY pairs DESC").df()
    bias = c.execute("SELECT site_key, segments, z FROM an_seg_bias WHERE site_key LIKE '%StructurePlaceSettings%' ORDER BY segments DESC").df()
    pal.to_csv(f / "serial.csv", index=False)
    bias.to_csv(f / "bias.csv", index=False)
    shot = copy_shot("worldgen2-1-a.png", f)
    facts = [f"{r.site_key.split('/')[-1]}: r = {r.r:.3f} over {r.pairs:,} consecutive placements" for r in pal.head(2).itertuples()]
    facts += [f"{r.site_key.split('/')[-1]}: first-draw bias z = {r.z:.0f} over {r.segments:,} placements" for r in bias.head(2).itertuples()]
    facts += ["seed: RandomSource.create(Mth.getSeed(pos)) when no random is set (position hash)", "status: candidate"]
    write_readme(f, "Finding: structure template palettes are spatially correlated",
                 "When a structure template is placed without its own random, its palette/processor random is seeded "
                 "from a hash of the block position, so nearby placements make related choices.",
                 "Runtime segment statistics over all structure placements in the scenario runs.",
                 facts, [("serial.csv", "lag-1 correlation"), ("bias.csv", "first-draw uniformity")],
                 [(shot, "Placed structures (in-game)")] if shot else [])

    f = base / "shared-chunk-seeds"
    f.mkdir(parents=True, exist_ok=True)
    col = c.execute("""SELECT sites, segments, runs, array_to_string(example_sites, ' ; ') examples FROM an_seg_collisions
                       WHERE list_has_any(example_sites, ['net/minecraft/world/level/levelgen/carver/CaveWorldCarver.isStartChunk:1']) OR array_to_string(example_sites, ' ') LIKE '%Mineshaft%'
                       ORDER BY sites DESC, segments DESC LIMIT 200""").df()
    col.to_csv(f / "collisions.csv", index=False)
    facts = [f"{len(col)} distinct seed values feed several unrelated draw sites, up to {int(col.sites.max()) if len(col) else 0} sites per seed",
             "examples: CaveWorldCarver.isStartChunk, MineshaftStructure.findGenerationPoint, RuinedPortalStructure.findGenerationPoint, NaturalSpawner.spawnMobsForChunkGeneration",
             "status: candidate, probably familiar to seedfinders"]
    write_readme(f, "Finding: one chunk seed feeds unrelated generation steps",
                 "Several world-generation decisions in the same chunk start from the exact same seed value, so their "
                 "first random numbers are identical and their outcomes are coupled.",
                 "`an_seg_collisions`: identical seed values reaching different draw sites across all runs.",
                 facts, [("collisions.csv", "seed values and the draw sites they feed")])


def ds_mechanics(c):
    import gen_player_scenarios as g
    base = DS / "11-mechanics"
    for fam in g.FAMILIES:
        f = base / fam
        f.mkdir(parents=True, exist_ok=True)
        sites = c.execute(f"""SELECT regexp_extract(class, '[^/]+$') cls, method, sum(events) events FROM rt_sites
                              WHERE version='26.3' AND run LIKE 'player-{fam}-%' AND class NOT LIKE '%RandomSource%'
                              GROUP BY ALL ORDER BY events DESC""").df()
        runs = c.execute(f"SELECT count(*), coalesce(sum(events),0) FROM rt_runs WHERE version='26.3' AND run LIKE 'player-{fam}-%'").fetchone()
        if not runs[0]:
            continue
        sites.to_csv(f / "draw-sites.csv", index=False)
        doc = (getattr(g, fam).__doc__ or "").strip()
        imgs = [(p, f"In-game: station {i}") for i, p in enumerate(filter(None, [copy_shot(f"{fam}-0-a.png", f), copy_shot(f"{fam}-1-a.png", f)]))]
        top = sites.head(8)
        facts = [f"{runs[0]} runs (4 world seeds), {fmt(runs[1])} RNG events, {len(sites)} distinct draw sites"]
        facts += [f"{r.cls}.{r.method}: {fmt(r.events)} draws" for r in top.itertuples()]
        write_readme(f, f"Mechanic: {fam}", doc or f"Scenario family `{fam}`: fake players `iwdissi_*` exercising this mechanic.",
                     f"Driver script `scenarios/gen/player-{fam}-<seed>.drv` (generated by `tools/gen_player_scenarios.py`), "
                     "run on 26.3 for 4 world seeds; fake players are real server players named `iwdissi_<role><n>`.",
                     facts, [("draw-sites.csv", "every draw site this family exercised, with draw counts")], imgs)


def ds_cross(c):
    import csv
    f = DS / "12-cross-version"
    if not (f / "cross-version.csv").exists():
        return
    rows = list(csv.DictReader(open(f / "cross-version.csv")))
    fig, ax = plt.subplots(figsize=(8, 3.2))
    vs = [r["version"] for r in rows]
    cov = [float(r["coverage_pct"]) for r in rows]
    ax.bar(vs, cov, color=SERIES[0], width=0.5)
    for x, y in zip(vs, cov):
        ax.text(x, y, f"{y}%", ha="center", va="bottom", color=INK2)
    ax.set_ylim(0, 100)
    ax.set_title("Server gameplay draw sites exercised at runtime, per version")
    save(fig, f / "coverage-by-version.png")
    facts = []
    for r in rows:
        facts.append(f"{r['version']}: {r['runs']} runs, {int(float(r['events'])):,} events, {int(float(r['mismatches']))} mismatches, "
                     f"coverage {r['coverage_pct']}%, rediscovered {r['rediscovered']} of {r['checks']} known facts")
        facts.append(f"{r['version']}: piglin dance seeded by game time = {r['piglin_dance_seeded_by_game_time']}, "
                     f"enchant slots reseeded seed+slot = {r['enchant_seed_plus_slot_reseed']}, enchant first-draw r = {r['enchant_first_draw_serial_r']}, "
                     f"structure palette r = {r['structure_palette_serial_r']}, Level.random shared by {r['level_random_consumer_classes']} classes")
    table = ["| Version | Runs | Events | Mismatches | Coverage | Rediscovered | Piglin game-time seed | Enchant r | Palette r |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        table.append(f"| {r['version']} | {r['runs']} | {int(float(r['events'])):,} | {int(float(r['mismatches']))} | {r['coverage_pct']}% | "
                     f"{r['rediscovered']}/{r['checks']} | {r['piglin_dance_seeded_by_game_time']} | {r['enchant_first_draw_serial_r']} | {r['structure_palette_serial_r']} |")
    write_readme(f, "Cross-version check: 26.1.2, 26.2 and 26.3",
                 "The whole runtime process (every scenario family, the replay check, the correlation analysis and the "
                 "rediscovery checklist) repeated on the three most recent releases, to see whether the findings hold.",
                 "`tools/run_many.py --version <v>` for each version, then `tools/cross_version.py` (analysis restricted "
                 "to that version's runs). Static scans of 1.21.11, 1.16.1 and 1.12.2 are in ../01-static-catalog and ../03-version-diffs.",
                 facts, [("cross-version.csv", "key numbers per version")] +
                 [(f"rediscovery-{r['version']}.csv", f"rediscovery checklist for {r['version']}") for r in rows] +
                 [("coverage-by-version.png", "chart")],
                 [("coverage-by-version.png", "Coverage per version")], extra="\n".join(table))


DATASETS = {"01-static-catalog": ds_static, "02-seed-sources": ds_seeds, "03-version-diffs": ds_versions,
            "04-runtime-runs": ds_runtime, "05-coverage": ds_coverage, "06-generator-graph": ds_graph,
            "07-crackability": ds_crack, "08-anomalies": ds_anomalies, "09-rediscovery": ds_rediscovery,
            "10-findings": ds_findings, "11-mechanics": ds_mechanics, "12-cross-version": ds_cross}


def index():
    lines = ["# Datasets", "", "Every dataset has its own folder with the data files, charts, in-game screenshots where "
             "useful, and a README: exact numbers generated from the data plus a short plain-language explanation.", ""]
    for d in sorted(DS.iterdir()):
        if d.is_dir():
            subs = sorted(p for p in d.iterdir() if p.is_dir() and (p / "README.md").exists())
            title = (d / "README.md").read_text().splitlines()[0][2:] if (d / "README.md").exists() else d.name
            lines.append(f"- [{d.name}]({d.name}/): {title}")
            for s in subs:
                lines.append(f"  - [{s.name}]({d.name}/{s.name}/): {(s / 'README.md').read_text().splitlines()[0][2:]}")
    (DS / "README.md").write_text("\n".join(lines) + "\n")


def main():
    global LLM_URL
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data/atlas.duckdb"))
    ap.add_argument("--llm", default=LLM_URL, help="OpenAI-compatible endpoint; empty to skip summaries")
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    LLM_URL = a.llm
    import sys
    sys.path.insert(0, str(ROOT / "tools"))
    c = duckdb.connect(a.db, read_only=True)
    for name, fn in DATASETS.items():
        if a.only and a.only not in name:
            continue
        print(f"building {name}", flush=True)
        fn(c)
    index()
    print(f"LLM summaries: {LLM_STATS}")


if __name__ == "__main__":
    main()
