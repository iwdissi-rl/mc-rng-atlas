#!/usr/bin/env python3
"""Generate fake-player driver scenarios (.drv) for mechanics that need a player, plus block/redstone coverage.

Each scenario is one server: a family of mechanics laid out as up to 8 stations 96 blocks apart, each with its
own fake player and variant (enchantments, mob type, profession, tool, ...). Every family is generated for
several world seeds. Appends to scenarios/gen/manifest-player.tsv.

    gen_player_scenarios.py --jar work/versions/26.3-client.jar
"""
import argparse, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEEDS = [42, 1337, -4172144997902289642, 8675309]
GAP = 96          # blocks between stations
Y = 100           # floor level
DURATION = 6000   # ticks per scenario (sprinted)


class Scn:
    """Collects timed driver actions for one server."""

    def __init__(self):
        self.lines = []

    def at(self, t, *a):
        self.lines.append((t, " ".join(str(x) for x in a)))

    def cmd(self, t, c):
        self.at(t, "cmd", c)

    def text(self, duration=DURATION):
        self.cmd(1, "gamerule spawn_mobs false")
        self.cmd(1, "gamerule advance_time false")
        self.cmd(1, "gamerule advance_weather false")
        self.cmd(2, f"tick sprint {duration + 100}")
        self.at(duration, "stop")
        return "\n".join(f"{t} {a}" for t, a in sorted(self.lines, key=lambda x: x[0])) + "\n"


def station(s, i, name, floor="minecraft:stone", r=12):
    """Forceload, floor and a fake player at the station centre. Returns (x, z) of the centre."""
    x, z = i * GAP, 0
    s.cmd(3, f"forceload add {x - 32} {z - 32} {x + 31} {z + 31}")
    s.cmd(4, f"fill {x - r} {Y - 1} {z - r} {x + r} {Y - 1} {z + r} {floor}")
    s.cmd(4, f"fill {x - r} {Y} {z - r} {x + r} {Y + 4} {z + r} minecraft:air")
    s.at(6, "player", name, x + 0.5, Y, z + 0.5)
    s.cmd(8, f"effect give {name} minecraft:resistance infinite 4 true")
    s.cmd(8, f"effect give {name} minecraft:regeneration infinite 4 true")
    s.cmd(8, f"effect give {name} minecraft:saturation infinite 0 true")
    return x, z


def hand(s, t, p, item):
    s.cmd(t, f"item replace entity {p} weapon.mainhand with {item}")


def ench(item, **lv):
    if not lv:
        return f"minecraft:{item}"
    return f"minecraft:{item}[minecraft:enchantments={{" + ",".join(f'"minecraft:{k}":{v}' for k, v in lv.items()) + "}]"


# ---------------- families ----------------

def fishing(s):
    rods = [{}, {"lure": 3}, {"luck_of_the_sea": 3}, {"lure": 3, "luck_of_the_sea": 3}, {"unbreaking": 3},
            {"lure": 1}, {"luck_of_the_sea": 1}, {}]
    for i, e in enumerate(rods):
        p = f"fish{i}"
        x, z = station(s, i, p)
        s.cmd(5, f"fill {x - 16} {Y - 5} {z - 16} {x + 16} {Y - 1} {z + 16} minecraft:water")
        s.cmd(5, f"fill {x} {Y - 5} {z} {x} {Y - 1} {z} minecraft:stone")
        if i == 7:  # open-sky vs rain/thunder affects bite time; also jungle-style "open water" check
            s.cmd(9, "weather thunder")
        hand(s, 9, p, ench("fishing_rod", **e))
        s.at(10, "look", p, (i * 45) % 360, 35)
        s.at(12, "fish", p, 10000)


def enchanting(s):
    items = ["diamond_sword", "diamond_pickaxe", "bow", "book", "diamond_chestplate", "trident", "fishing_rod",
             "crossbow", "mace", "golden_helmet", "iron_axe", "shears", "netherite_boots", "brush", "golden_sword"]
    ring = [(dx, dz) for dx in range(-2, 3) for dz in range(-2, 3) if max(abs(dx), abs(dz)) == 2]
    for i, shelves in enumerate([0, 1, 3, 5, 8, 11, 15, 15]):
        p = f"ench{i}"
        x, z = station(s, i, p)
        tx, tz = x, z + 3
        s.cmd(9, f"setblock {tx} {Y} {tz} minecraft:enchanting_table")
        placed = 0
        for layer in (0, 1):
            for dx, dz in ring:
                if placed >= shelves:
                    break
                s.cmd(9, f"setblock {tx + dx} {Y + layer} {tz + dz} minecraft:bookshelf")
                placed += 1
        s.cmd(10, f"xp add {p} 100000 levels")
        t, k = 20, 0
        while t < DURATION - 20:
            hand(s, t, p, f"minecraft:{items[k % len(items)]}")
            s.at(t + 1, "enchant", p, tx, Y, tz, k % 3)
            s.cmd(t + 2, f"clear {p}")
            t, k = t + 4, k + 1


def combat(s):
    setups = [("zombie", ench("diamond_sword", sharpness=5, sweeping_edge=3, looting=3)),
              ("skeleton", ench("iron_axe", fire_aspect=2)),
              ("sheep", ench("diamond_sword", knockback=2)),
              ("creeper", "minecraft:mace"),
              ("spider", ench("netherite_sword", looting=3)),
              ("witch", ench("trident", impaling=5)),
              ("enderman", ench("diamond_sword", looting=3)),
              ("pig", "minecraft:wooden_sword")]
    for i, (mob, weapon) in enumerate(setups):
        p = f"fight{i}"
        x, z = station(s, i, p)
        s.cmd(9, f"fill {x - 6} {Y} {z - 6} {x + 6} {Y + 3} {z + 6} minecraft:glass hollow")
        s.cmd(9, f"fill {x - 5} {Y} {z - 5} {x + 5} {Y + 3} {z + 5} minecraft:air")
        s.cmd(9, f"fill {x - 6} {Y + 3} {z - 6} {x + 6} {Y + 3} {z + 6} minecraft:glass")
        hand(s, 10, p, weapon)
        s.cmd(10, f"item replace entity {p} armor.chest with {ench('diamond_chestplate', thorns=3, protection=4)}")
        s.cmd(10, f"item replace entity {p} armor.head with {ench('iron_helmet', unbreaking=3)}")
        for t in range(20, DURATION - 20, 15):
            if t % 150 == 20:
                s.cmd(t, f"execute positioned {x} {Y} {z} unless entity @e[type=minecraft:{mob},distance=..8] run "
                         f"summon minecraft:{mob} {x + 2} {Y} {z} {{PersistenceRequired:1b}}")
            s.at(t + 1, "attack", p, 6)
            if mob == "pig" and t % 300 == 21:  # mace smash needs a fall: jump the player with a teleport
                s.cmd(t, f"tp {p} {x + 0.5} {Y + 2} {z + 0.5}")


def ranged(s):
    # bows, crossbows, tridents fired by the player; skeletons/pillagers/blazes/ghasts shooting back
    weapons = [ench("bow", power=5, punch=2, flame=1), ench("bow", infinity=1), "minecraft:bow",
               ench("crossbow", multishot=1), ench("crossbow", piercing=4, quick_charge=3),
               ench("trident", loyalty=3), ench("trident", riptide=3), "minecraft:crossbow"]
    shooters = ["skeleton", "stray", "pillager", "blaze", "bogged", "piglin", "drowned", "breeze"]
    for i, w in enumerate(weapons):
        p = f"archer{i}"
        x, z = station(s, i, p, r=20)
        hand(s, 9, p, w)
        s.cmd(9, f"give {p} minecraft:arrow 640")
        s.at(10, "look", p, 0, 0)
        for t in range(20, DURATION - 40, 40):
            if t % 400 == 20:
                s.cmd(t, f"execute positioned {x} {Y} {z} unless entity @e[type=minecraft:{shooters[i]},distance=..24] run "
                         f"summon minecraft:{shooters[i]} {x} {Y} {z + 12} {{PersistenceRequired:1b}}")
                s.cmd(t, f"execute positioned {x} {Y} {z} unless entity @e[type=minecraft:husk,distance=..24] run "
                         f"summon minecraft:husk {x} {Y} {z + 6} {{NoAI:1b,PersistenceRequired:1b}}")
            s.at(t, "use", p)
            s.at(t + 25, "release", p)


def trading(s):
    profs = ["librarian", "armorer", "weaponsmith", "toolsmith", "cleric", "farmer", "fletcher", "cartographer",
             "butcher", "fisherman", "leatherworker", "mason", "shepherd"]
    pay = ["emerald 64", "paper 64", "book 64", "wheat 64", "coal 64", "string 64", "iron_ingot 64", "rotten_flesh 64",
           "gold_ingot 64", "redstone 64", "lapis_lazuli 64", "glass_pane 64", "stick 64", "flint 64", "feather 64",
           "chicken 64", "porkchop 64", "rabbit 64", "cod 64", "salmon 64", "leather 64", "clay_ball 64", "white_wool 64",
           "pumpkin 64", "melon 64", "carrot 64", "potato 64", "beetroot 64", "diamond 64", "glass_bottle 64",
           "compass 16", "ink_sac 64", "quartz 64", "tripwire_hook 64", "dried_kelp_block 64", "sweet_berries 64",
           "rabbit_hide 64", "turtle_scute 16", "tropical_fish 64", "pufferfish 64", "nether_wart 64", "stone 64",
           "andesite 64", "granite 64", "diorite 64", "black_dye 64", "white_dye 64"]
    for i in range(8):
        p = f"trader{i}"
        x, z = station(s, i, p)
        for t in range(10, DURATION - 30, 300):
            prof = profs[(i + t // 300) % len(profs)]
            level = 1 + (t // 300) % 5
            # fresh villager every 300 ticks: new offer generation each time; trades raise its level (more offers)
            s.cmd(t, f"kill @e[type=minecraft:villager,distance=..6,x={x},y={Y},z={z}]")
            s.cmd(t + 1, f"summon minecraft:villager {x} {Y} {z + 2} {{NoAI:1b,PersistenceRequired:1b,"
                         f"VillagerData:{{profession:\"minecraft:{prof}\",level:{level},type:\"minecraft:plains\"}}}}")
            s.cmd(t + 2, f"clear {p}")
            for j, it in enumerate(pay):
                s.cmd(t + 2, f"give {p} minecraft:{it}")
            for k in range(5):
                s.at(t + 10 + k * 50, "trade", p)
        if i == 7:  # wandering trader
            for t in range(150, DURATION - 30, 300):
                s.cmd(t, f"summon minecraft:wandering_trader {x - 2} {Y} {z} {{NoAI:1b}}")


def mining(s):
    blocks = ["coal_ore", "iron_ore", "copper_ore", "gold_ore", "redstone_ore", "lapis_ore", "diamond_ore", "emerald_ore",
              "deepslate_diamond_ore", "nether_quartz_ore", "nether_gold_ore", "gravel", "oak_leaves", "jungle_leaves",
              "dark_oak_leaves", "cherry_leaves", "mangrove_leaves", "pale_oak_leaves", "azalea_leaves", "glowstone",
              "melon", "wheat[age=7]", "carrots[age=7]", "potatoes[age=7]", "beetroots[age=3]", "nether_wart[age=3]",
              "short_grass", "tall_grass", "fern", "dead_bush", "amethyst_cluster", "sea_lantern", "clay",
              "snow_block", "gilded_blackstone", "budding_amethyst", "sweet_berry_bush[age=3]", "cave_vines[berries=true]",
              "pointed_dripstone", "sculk", "sculk_catalyst", "suspicious_sand", "decorated_pot", "bee_nest", "cobweb",
              "vine", "seagrass", "kelp", "torchflower_crop[age=1]", "pitcher_crop[age=4]", "infested_stone", "spawner"]
    tools = [ench("diamond_pickaxe", fortune=3), ench("diamond_pickaxe", silk_touch=1), ench("iron_pickaxe", unbreaking=3),
             "minecraft:wooden_pickaxe", ench("diamond_shovel", fortune=3), ench("netherite_hoe", fortune=3),
             "minecraft:shears", ench("diamond_axe", fortune=2, unbreaking=1)]
    for i, tool in enumerate(tools):
        p = f"miner{i}"
        x, z = station(s, i, p)
        s.cmd(9, f"fill {x - 3} {Y - 1} {z + 1} {x + 3} {Y - 1} {z + 3} minecraft:farmland")
        hand(s, 9, p, tool)
        t, k = 20, 0
        while t < DURATION - 20:
            b = blocks[k % len(blocks)]
            bx, bz = x + (k % 3) - 1, z + 2
            if t % 400 == 20:
                hand(s, t, p, tool)  # fresh tool before it breaks
            s.cmd(t, f"setblock {bx} {Y} {bz} minecraft:{b}")
            s.at(t + 1, "break", p, bx, Y, bz)
            t, k = t + 3, k + 1
        s.cmd(DURATION - 30, f"kill @e[type=minecraft:item,x={x},y={Y},z={z},distance=..20]")


def itemuse(s):
    stations = [
        # (items cycled into hand, action pattern)
        (["egg", "snowball", "ender_pearl", "experience_bottle", "wind_charge"], "throw"),
        (["ender_eye"], "throw"),
        (["chorus_fruit", "golden_apple", "suspicious_stew", "honey_bottle", "rotten_flesh", "pufferfish", "potion"], "eat"),
        (["splash_potion[minecraft:potion_contents={potion:\"minecraft:healing\"}]",
          "lingering_potion[minecraft:potion_contents={potion:\"minecraft:poison\"}]", "firework_rocket"], "throw"),
        (["bone_meal"], "bonemeal"),
        (["wheat", "carrot", "wheat_seeds", "bone", "cod", "golden_carrot"], "animals"),
        (["shears", "bucket", "bowl", "lead", "name_tag", "brush"], "animals2"),
        (["flint_and_steel", "fire_charge", "wooden_hoe", "golden_shovel"], "useon"),
    ]
    animals = ["cow", "sheep", "pig", "chicken", "wolf", "cat", "horse", "rabbit", "fox", "llama", "panda", "turtle",
               "goat", "axolotl", "parrot", "mooshroom", "camel", "armadillo", "sniffer", "frog"]
    for i, (items, mode) in enumerate(stations):
        p = f"user{i}"
        x, z = station(s, i, p, floor="minecraft:grass_block", r=16)
        if mode == "bonemeal":
            s.cmd(9, f"fill {x - 3} {Y - 1} {z + 1} {x + 3} {Y - 1} {z + 3} minecraft:farmland")
        t, k = 20, 0
        while t < DURATION - 40:
            it = items[k % len(items)]
            hand(s, t, p, f"minecraft:{it}")
            if mode == "throw":
                s.at(t + 1, "look", p, (k * 37) % 360, -20)
                s.at(t + 2, "use", p)
                t += 6
            elif mode == "eat":
                s.at(t + 1, "use", p)
                t += 45
            elif mode == "bonemeal":
                plants = ["wheat", "carrots", "oak_sapling", "birch_sapling", "short_grass", "red_mushroom", "bamboo_sapling",
                          "sweet_berry_bush", "cocoa", "sea_pickle", "kelp", "pink_petals", "moss_block", "pitcher_crop",
                          "torchflower_crop", "cherry_sapling", "mangrove_propagule", "pale_oak_sapling", "azalea", "dandelion"]
                pl = plants[k % len(plants)]
                below = "minecraft:farmland" if pl in ("wheat", "carrots", "pitcher_crop", "torchflower_crop") else "minecraft:grass_block"
                s.cmd(t, f"setblock {x} {Y - 1} {z + 2} {below}")
                s.cmd(t, f"setblock {x} {Y} {z + 2} minecraft:{pl}")
                for j in range(3):
                    s.at(t + 1 + j, "useon", p, x, Y, z + 2)
                t += 6
            elif mode in ("animals", "animals2"):
                if t % 300 == 20:
                    s.cmd(t, f"kill @e[type=!minecraft:player,x={x},y={Y},z={z},distance=..14]")
                    for j, a in enumerate(animals[:10] if mode == "animals" else animals[10:]):
                        s.cmd(t + 1, f"summon minecraft:{a} {x + (j % 5) - 2} {Y} {z + 2 + j // 5} {{NoAI:1b}}")
                s.at(t + 2, "interact", p, 6)
                t += 5
            elif mode == "useon":
                s.cmd(t, f"setblock {x} {Y} {z + 2} minecraft:{'tnt' if k % 2 else 'grass_block'}")
                s.at(t + 1, "useon", p, x, Y if k % 2 else Y, z + 2)
                t += 8
            k += 1


def bartering(s):
    for i in range(8):
        p = f"barter{i}"
        x, z = station(s, i, p)
        # one pen around the player and the piglins, so dropped gold always lands where they can reach it
        s.cmd(9, f"fill {x - 4} {Y} {z - 2} {x + 4} {Y + 3} {z + 6} minecraft:glass hollow")
        s.cmd(9, f"fill {x - 3} {Y} {z - 1} {x + 3} {Y + 2} {z + 5} minecraft:air")
        for j in range(4):
            s.cmd(10, f"summon minecraft:piglin {x - 2 + j + 0.5} {Y} {z + 3.5} {{IsImmuneToZombification:1b,PersistenceRequired:1b}}")
        s.at(11, "look", p, 0, 10)
        for t in range(30, DURATION - 20, 25):
            hand(s, t, p, "minecraft:gold_ingot")
            s.at(t + 1, "drop", p)
        s.cmd(DURATION - 40, f"kill @e[type=minecraft:item,x={x},y={Y},z={z},distance=..20]")


def raids(s):
    villages = ["village_plains", "village_desert", "village_savanna", "village_snowy", "village_taiga",
                "village_plains", "village_desert", "village_taiga"]
    for i, v in enumerate(villages):
        p = f"raider{i}"
        x, z = i * 256, 2000
        s.cmd(3, f"forceload add {x - 80} {z - 80} {x + 79} {z + 79}")
        s.cmd(5, f"place structure minecraft:{v} {x} 64 {z}")
        s.at(8, "player", p, x + 0.5, 120, z + 0.5)
        s.cmd(9, f"effect give {p} minecraft:resistance infinite 4 true")
        s.cmd(9, f"effect give {p} minecraft:regeneration infinite 4 true")
        s.cmd(10, f"execute as {p} at @s run tp @s ~ ~ ~")
        s.cmd(12, f"execute as {p} at @s positioned over motion_blocking run tp @s ~ ~ ~")
        s.cmd(14, f"effect give {p} minecraft:bad_omen 6000 {i % 5}")
        hand(s, 15, p, ench("diamond_sword", sharpness=5))
        for t in range(40, DURATION - 20, 15):
            s.at(t, "attack", p, 5)


def trials(s):
    kinds = ["melee/zombie", "melee/husk", "ranged/skeleton", "small_melee/baby_zombie", "breeze", "melee/spider",
             "ranged/stray", "small_melee/slime"]

    def cfg(kind):
        return (f'{{normal_config:"minecraft:trial_chamber/{kind}/normal",'
                f'ominous_config:"minecraft:trial_chamber/{kind}/ominous"}}')

    for i in range(8):
        p = f"trial{i}"
        x, z = station(s, i, p, r=14)
        ominous = i >= 4
        s.cmd(9, f"setblock {x} {Y} {z + 4} minecraft:trial_spawner[ominous={'true' if ominous else 'false'}]" + cfg(kinds[i]))

        s.cmd(9, f"setblock {x + 4} {Y} {z} minecraft:vault[ominous={'true' if ominous else 'false'}]")
        hand(s, 10, p, ench("netherite_sword", sharpness=5))
        if ominous:
            s.cmd(10, f"effect give {p} minecraft:trial_omen 6000 0")
        for t in range(40, DURATION - 20, 10):
            s.at(t, "attack", p, 8)
        key = "ominous_trial_key" if ominous else "trial_key"
        for t in range(60, DURATION - 20, 200):
            hand(s, t, p, f"minecraft:{key}")
            s.at(t + 1, "useon", p, x + 4, Y, z)
            hand(s, t + 2, p, ench("netherite_sword", sharpness=5))
        for t in range(300, DURATION - 20, 1200):  # cooldown passes faster than real time only via ticks; respawn
            s.cmd(t, f"setblock {x} {Y} {z + 4} minecraft:air")
            s.cmd(t + 1, f"setblock {x} {Y} {z + 4} minecraft:trial_spawner[ominous={'true' if ominous else 'false'}]" + cfg(kinds[i]))


FAMILIES = {"fishing": fishing, "enchanting": enchanting, "combat": combat, "ranged": ranged, "trading": trading,
            "mining": mining, "itemuse": itemuse, "bartering": bartering, "raids": raids, "trials": trials}


def blocks_scenarios(jar, out):
    """No player: every block placed in a grid under extreme random tick speed; dispensers firing projectiles."""
    names = zipfile.ZipFile(jar).namelist()
    blocks = sorted(n[len("data/minecraft/loot_table/blocks/"):-5] for n in names
                    if n.startswith("data/minecraft/loot_table/blocks/") and n.endswith(".json"))
    shards = [blocks[i:i + 160] for i in range(0, len(blocks), 160)]
    res = []
    for si, part in enumerate(shards):
        s = Scn()
        # stone floor (only the test cells tick) and a high but bounded random tick speed
        s.cmd(3, "forceload add 0 0 255 111")
        s.cmd(4, "fill 0 99 0 255 99 111 minecraft:stone")
        s.cmd(5, "gamerule random_tick_speed 256")
        for j, b in enumerate(part):
            bx, bz = (j % 20) * 12 + 4, (j // 20) * 12 + 4
            # surroundings that let most blocks survive and grow: water + farmland + a log to climb
            s.cmd(6, f"setblock {bx + 2} 99 {bz} minecraft:water")
            s.cmd(6, f"setblock {bx} 99 {bz} minecraft:farmland")
            s.cmd(7, f"setblock {bx} 100 {bz} minecraft:{b}")
            s.cmd(7, f"setblock {bx - 2} 100 {bz} minecraft:{b}")
            s.cmd(7, f"setblock {bx - 2} 99 {bz} minecraft:dirt")
        res.append((f"blocks-{si}", s.text(1500)))
    # dispensers / droppers on a redstone clock, each loaded with a different projectile or item
    s = Scn()
    s.cmd(3, "forceload add 0 0 127 127")
    s.cmd(4, "fill 0 99 0 127 99 127 minecraft:stone")
    loads = ["arrow", "spectral_arrow", "tipped_arrow", "fire_charge", "egg", "snowball", "splash_potion", "experience_bottle",
             "firework_rocket", "bone_meal", "shears", "flint_and_steel", "trident", "wind_charge", "tnt", "dirt"]
    for j, it in enumerate(loads):
        bx = 4 + j * 6
        for kind, dz in (("dispenser", 4), ("dropper", 20)):
            s.cmd(6, f"setblock {bx} 100 {dz} minecraft:{kind}[facing=south]")
            s.cmd(7, f"item replace block {bx} 100 {dz} container.0 with minecraft:{it} 64")
            s.cmd(7, f"item replace block {bx} 100 {dz} container.4 with minecraft:{it} 64")
            s.cmd(7, f"item replace block {bx} 100 {dz} container.8 with minecraft:dirt 64")
        if it == "shears":
            s.cmd(8, f"summon minecraft:sheep {bx} 100 5 {{NoAI:1b}}")
    for t in range(20, 3000, 4):
        s.cmd(t, "fill 4 101 4 100 101 20 minecraft:redstone_block replace minecraft:air" if t % 8 == 4 else
              "fill 4 101 4 100 101 20 minecraft:air replace minecraft:redstone_block")
    for t in range(20, 3000, 200):
        for j, it in enumerate(loads):
            s.cmd(t, f"item replace block {4 + j * 6} 100 4 container.0 with minecraft:{it} 64")
    s.cmd(2990, "kill @e[type=!minecraft:player]")
    res.append(("redstone-0", s.text(3000)))
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jar", default=str(ROOT / "work/versions/26.3-client.jar"))
    ap.add_argument("--out", default=str(ROOT / "scenarios/gen"))
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for f in out.glob("*.drv"):
        f.unlink()
    manifest = []
    for fam, fn in FAMILIES.items():
        for seed in SEEDS:
            s = Scn()
            fn(s)
            p = out / f"player-{fam}-{seed}.drv"
            p.write_text(s.text())
            manifest.append((p, seed))
    for name, text in blocks_scenarios(a.jar, out):
        for seed in SEEDS[:2]:
            p = out / f"{name}-{seed}.drv"
            p.write_text(text)
            manifest.append((p, seed))
    (out / "manifest-player.tsv").write_text("".join(f"{p}\t{s}\n" for p, s in manifest))
    print(f"{len(manifest)} driver scenarios ({len(FAMILIES)} player families x {len(SEEDS)} seeds + block/redstone shards)")


if __name__ == "__main__":
    main()
