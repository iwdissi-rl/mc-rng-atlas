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


ROLE = {"fish": "fish", "ench": "ench", "fight": "fight", "archer": "archer", "trader": "trader", "miner": "miner",
        "user": "user", "barter": "barter", "raider": "raider", "trial": "trial", "breeder": "breed", "boss": "boss",
        "wanderer": "wander", "hazard": "hazard", "hero": "hero", "keeper": "keeper", "tamer": "tamer",
        "builder": "build", "camper": "camp"}


def rename_players(text):
    """Fake players are named iwdissi_<role><n> (Minecraft names: max 16 chars, [A-Za-z0-9_])."""
    import re
    def sub(m):
        name = f"iwdissi_{ROLE[m.group(1)]}{m.group(2)}"
        assert len(name) <= 16, name
        return name
    return re.sub(r"\b(" + "|".join(sorted(ROLE, key=len, reverse=True)) + r")(\d)\b", sub, text)


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
        text = "\n".join(f"{t} {a}" for t, a in sorted(self.lines, key=lambda x: x[0])) + "\n"
        return rename_players(text)


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


def breeding(s):
    pairs = [("cow", "wheat"), ("sheep", "wheat"), ("pig", "carrot"), ("chicken", "wheat_seeds"), ("wolf", "bone"),
             ("cat", "cod"), ("horse", "golden_carrot"), ("llama", "hay_block"), ("fox", "sweet_berries"),
             ("panda", "bamboo"), ("rabbit", "dandelion"), ("turtle", "seagrass"), ("goat", "wheat"), ("axolotl", "tropical_fish_bucket"),
             ("frog", "slime_ball"), ("bee", "dandelion"), ("camel", "cactus"), ("armadillo", "spider_eye"), ("sniffer", "torchflower_seeds"),
             ("strider", "warped_fungus"), ("parrot", "wheat_seeds"), ("ocelot", "cod"), ("donkey", "golden_carrot"), ("mule", "golden_carrot")]
    for i in range(8):
        p = f"breeder{i}"
        x, z = station(s, i, p, floor="minecraft:grass_block", r=14)
        s.cmd(9, f"fill {x - 9} {Y} {z - 9} {x + 9} {Y + 3} {z + 9} minecraft:oak_fence hollow")
        s.cmd(9, f"fill {x - 8} {Y} {z - 8} {x + 8} {Y + 3} {z + 8} minecraft:air")
        s.cmd(9, f"fill {x - 8} {Y - 1} {z + 6} {x - 6} {Y - 1} {z + 8} minecraft:water")  # turtles/axolotls/frogs
        mine = pairs[i * 3:(i + 1) * 3]
        for t in range(20, DURATION - 60, 600):
            for j, (mob, food) in enumerate(mine):
                for k in range(2):  # two adults with AI so they can find each other and breed
                    s.cmd(t, f"summon minecraft:{mob} {x - 4 + j * 4} {Y} {z + k * 2 - 1} {{Age:0,PersistenceRequired:1b}}")
        for t in range(40, DURATION - 40, 20):
            mob, food = mine[(t // 20) % len(mine)]
            hand(s, t, p, f"minecraft:{food} 64")
            s.at(t + 1, "interact", p, 9)  # feed (love mode / taming attempts / horse mounting)
        for t in range(1200, DURATION - 40, 1200):
            s.cmd(t, f"kill @e[type=!minecraft:player,x={x},y={Y},z={z},distance=..12]")


def bosses(s):
    fights = [("wither", ench("netherite_sword", sharpness=5, smite=5)), ("ender_dragon", ench("netherite_sword", sharpness=5)),
              ("ravager", ench("netherite_axe", sharpness=5)), ("evoker", "minecraft:netherite_sword"),
              ("elder_guardian", ench("trident", impaling=5)), ("warden", ench("netherite_sword", sharpness=5)),
              ("piglin_brute", "minecraft:netherite_sword"), ("vindicator", ench("netherite_sword", sweeping_edge=3))]
    s.cmd(1, "difficulty hard")
    for i, (boss, weapon) in enumerate(fights):
        p = f"boss{i}"
        x, z = station(s, i, p, r=20)
        hand(s, 9, p, weapon)
        s.cmd(9, f"item replace entity {p} armor.chest with {ench('netherite_chestplate', protection=4)}")
        s.cmd(9, f"effect give {p} minecraft:resistance infinite 255 true")
        if boss == "elder_guardian":
            s.cmd(9, f"fill {x - 10} {Y - 6} {z - 10} {x + 10} {Y + 6} {z + 10} minecraft:water replace minecraft:air")
        for t in range(20, DURATION - 20, 900):
            s.cmd(t, f"execute positioned {x} {Y} {z} unless entity @e[type=minecraft:{boss},distance=..40] run "
                     f"summon minecraft:{boss} {x} {Y + (6 if boss in ('wither', 'ender_dragon') else 0)} {z + 6} {{PersistenceRequired:1b}}")
        for t in range(30, DURATION - 20, 12):
            s.at(t, "attack", p, 16)


def natural(s):
    """Natural spawning with time and weather running: patrols, wandering traders, lightning, mob spawns."""
    s.cmd(1, "difficulty hard")
    for i in range(8):
        p = f"wanderer{i}"
        x, z = i * 512, -3000
        s.cmd(3, f"forceload add {x - 64} {z - 64} {x + 63} {z + 63}")
        s.at(6, "player", p, x + 0.5, 140, z + 0.5)
        s.cmd(8, f"effect give {p} minecraft:resistance infinite 255 true")
        s.cmd(8, f"execute as {p} at @s positioned over motion_blocking run tp @s ~ ~ ~")
        if i >= 4:
            s.cmd(9, f"effect give {p} minecraft:bad_omen 120000 0")
    s.cmd(10, "gamerule spawn_mobs true")
    s.cmd(10, "gamerule advance_time true")
    s.cmd(10, "gamerule advance_weather true")
    s.cmd(10, "gamerule spawn_patrols true")
    s.cmd(10, "gamerule spawn_wandering_traders true")
    s.cmd(10, "gamerule spawn_phantoms true")
    for t in range(100, DURATION, 1000):
        s.cmd(t, "weather thunder")
        s.cmd(t + 500, "time add 12000")


def hazards(s):
    s.cmd(1, "difficulty hard")
    for i in range(8):
        p = f"hazard{i}"
        x, z = station(s, i, p, r=14)
        if i == 0:  # drowning
            s.cmd(9, f"fill {x - 3} {Y} {z - 3} {x + 3} {Y + 4} {z + 3} minecraft:water")
            s.cmd(9, f"effect give {p} minecraft:resistance infinite 255 true")
        elif i == 1:  # tools on their last durability point
            for t in range(20, DURATION - 20, 6):
                hand(s, t, p, "minecraft:diamond_pickaxe[minecraft:damage=1560,minecraft:enchantments={\"minecraft:unbreaking\":3}]")
                s.cmd(t + 1, f"setblock {x} {Y} {z + 2} minecraft:stone")
                s.at(t + 2, "break", p, x, Y, z + 2)
        elif i == 2:  # lightning on copper, villagers, pigs, creepers, turtles
            for t in range(20, DURATION - 20, 60):
                s.cmd(t, f"setblock {x + 4} {Y} {z} minecraft:lightning_rod")
                s.cmd(t, f"setblock {x + 4} {Y - 1} {z} minecraft:copper_block")
                s.cmd(t, f"summon minecraft:{['villager', 'pig', 'creeper', 'turtle', 'mooshroom'][(t // 60) % 5]} {x + 4} {Y + 1} {z + 1}")
                s.cmd(t + 1, f"summon minecraft:lightning_bolt {x + 4} {Y + 1} {z}")
        elif i == 3:  # zombie sieges: zombies vs villagers (infection), reinforcements on hit
            for t in range(20, DURATION - 20, 300):
                s.cmd(t, f"summon minecraft:villager {x + 2} {Y} {z + 2}")
                s.cmd(t, f"summon minecraft:zombie {x - 2} {Y} {z + 2}")
                s.cmd(t, f"summon minecraft:drowned {x - 2} {Y} {z - 2}")
            hand(s, 9, p, "minecraft:wooden_sword")
            for t in range(30, DURATION - 20, 15):
                s.at(t, "attack", p, 8)
        elif i == 4:  # fire spread and burning entities
            s.cmd(9, f"fill {x - 5} {Y} {z + 2} {x + 5} {Y} {z + 6} minecraft:oak_planks")
            for t in range(20, DURATION - 20, 200):
                s.cmd(t, f"fill {x - 5} {Y} {z + 2} {x + 5} {Y} {z + 6} minecraft:oak_planks replace minecraft:air")
                s.cmd(t + 1, f"setblock {x} {Y + 1} {z + 4} minecraft:fire")
                s.cmd(t + 1, f"summon minecraft:cow {x + 1} {Y + 1} {z + 4} {{Fire:200s}}")
        elif i == 5:  # player damage, armor durability, totem, death drops
            s.cmd(9, f"item replace entity {p} armor.chest with minecraft:diamond_chestplate[minecraft:enchantments={{\"minecraft:thorns\":3}}]")
            for t in range(20, DURATION - 20, 40):
                s.cmd(t, f"summon minecraft:{['zombie', 'skeleton', 'spider', 'husk', 'vindicator'][(t // 40) % 5]} {x + 2} {Y} {z} {{PersistenceRequired:1b}}")
        elif i == 6:  # elytra, riptide, ender pearl, chorus teleport
            for t in range(20, DURATION - 20, 50):
                hand(s, t, p, "minecraft:chorus_fruit")
                s.at(t + 1, "use", p)
                hand(s, t + 40, p, "minecraft:ender_pearl")
                s.at(t + 41, "look", p, (t * 7) % 360, -30)
                s.at(t + 42, "use", p)
        else:  # suspicious sand/gravel brushing and decorated pots
            for t in range(20, DURATION - 20, 120):
                s.cmd(t, f"setblock {x} {Y} {z + 2} minecraft:suspicious_sand{{LootTable:\"minecraft:archaeology/desert_pyramid\"}}")
                s.cmd(t, f"setblock {x + 2} {Y} {z + 2} minecraft:suspicious_gravel{{LootTable:\"minecraft:archaeology/trail_ruins_rare\"}}")
                hand(s, t + 1, p, "minecraft:brush")
                for k in range(0, 100, 2):
                    s.at(t + 2 + k, "useon", p, x if k < 50 else x + 2, Y, z + 2)


# ---------------- wave 4: targeted at the sites earlier waves never reached ----------------

def villages(s):
    """Villager life with time running: breeding, farming, gossip, gifts to a hero, bells, sieges, raid wins."""
    s.cmd(1, "difficulty hard")
    for i, v in enumerate(["village_plains", "village_desert", "village_savanna", "village_taiga", "village_snowy",
                           "village_plains", "village_taiga", "village_desert"]):
        p = f"hero{i}"
        x, z = i * 256, 4000
        s.cmd(3, f"forceload add {x - 80} {z - 80} {x + 79} {z + 79}")
        s.cmd(5, f"place structure minecraft:{v} {x} 64 {z}")
        s.at(8, "player", p, x + 0.5, 120, z + 0.5)
        s.cmd(9, f"effect give {p} minecraft:resistance infinite 255 true")
        s.cmd(10, f"execute as {p} at @s positioned over motion_blocking run tp @s ~ ~ ~")
        s.cmd(12, f"effect give {p} minecraft:hero_of_the_village 120000 2")
        for k in range(10):  # extra villagers and beds' worth of activity; mobs pick up loot nearby
            s.cmd(14, f"execute at {p} run summon minecraft:villager ~{k - 5} ~ ~3")
        for k in range(4):
            s.cmd(14, f"execute at {p} run summon minecraft:zombie ~{k * 3} ~ ~12 {{CanPickUpLoot:1b,PersistenceRequired:1b}}")
            s.cmd(14, f"execute at {p} run summon minecraft:item ~{k * 3} ~ ~12 {{Item:{{id:\"minecraft:iron_sword\",count:1}}}}")
        hand(s, 15, p, "minecraft:bread 64")
        for t in range(40, DURATION - 40, 40):
            s.at(t, "interact", p, 6)        # feed / trade attempts
        for t in range(300, DURATION - 40, 300):
            s.at(t, "attack", p, 5)          # hitting villagers creates gossip; iron golems react
        if i >= 4:  # raids that get won: the omen starts one, killing every raider each wave ends it
            s.cmd(20, f"effect give {p} minecraft:bad_omen 6000 2")
            for t in range(700, DURATION - 40, 250):
                s.cmd(t, f"execute at {p} run kill @e[type=#minecraft:raiders,distance=..96]")
    s.cmd(10, "gamerule advance_time true")
    s.cmd(10, "time set 23000")


def breeding2(s):
    """Breeding forced with InLove, owners for tameables, plus panda personalities and turtles laying eggs."""
    sets = [["horse", "donkey", "llama"], ["cat", "wolf", "parrot"], ["fox", "rabbit", "axolotl"],
            ["mooshroom", "panda", "goat"], ["villager", "sheep", "pig"], ["turtle", "frog", "sniffer"],
            ["camel", "armadillo", "bee"], ["mule", "trader_llama", "ocelot"]]
    for i, mobs in enumerate(sets):
        p = f"keeper{i}"
        x, z = station(s, i, p, floor="minecraft:grass_block", r=16)
        s.cmd(9, f"fill {x - 10} {Y} {z - 10} {x + 10} {Y + 2} {z + 10} minecraft:oak_fence hollow")
        s.cmd(9, f"fill {x - 9} {Y} {z - 9} {x + 9} {Y + 2} {z + 9} minecraft:air")
        s.cmd(9, f"fill {x - 9} {Y - 3} {z + 5} {x - 3} {Y - 1} {z + 9} minecraft:water")
        s.cmd(9, f"fill {x + 3} {Y - 1} {z + 5} {x + 9} {Y - 1} {z + 9} minecraft:sand")
        s.cmd(9, f"fill {x - 8} {Y} {z - 8} {x - 4} {Y} {z - 4} minecraft:bamboo")
        s.cmd(9, f"setblock {x + 6} {Y} {z - 6} minecraft:beehive")
        s.cmd(9, f"fill {x + 2} {Y} {z - 9} {x + 8} {Y} {z - 9} minecraft:poppy")
        for t in range(20, DURATION - 100, 900):
            for j, mob in enumerate(mobs):
                for k in range(3):
                    extra = "Tame:1b," if mob in ("horse", "donkey", "llama", "mule", "trader_llama", "camel") else ""
                    if mob == "panda":
                        extra = f"MainGene:\"{['lazy', 'worried', 'playful', 'aggressive', 'weak', 'brown'][(t // 900 + k) % 6]}\",HiddenGene:\"{['weak', 'brown', 'normal'][k]}\","
                    s.cmd(t, f"summon minecraft:{mob} {x - 6 + j * 6} {Y} {z + k * 2 - 2} {{{extra}Age:0,PersistenceRequired:1b,Tags:[\"atlas{i}\"]}}")
            s.cmd(t + 2, f"execute as @e[tag=atlas{i},type=#minecraft:can_breathe_under_water] run data merge entity @s {{}}")
            s.cmd(t + 2, f"execute as @e[tag=atlas{i}] run data modify entity @s Owner set from entity {p} UUID")
            for k in range(0, 800, 100):
                s.cmd(t + 5 + k, f"execute as @e[tag=atlas{i}] run data merge entity @s {{InLove:600,Age:0}}")
            s.cmd(t + 850, f"kill @e[tag=atlas{i}]")
            s.cmd(t + 851, f"kill @e[type=minecraft:item,x={x},y={Y},z={z},distance=..14]")
        hand(s, 12, p, "minecraft:golden_apple 64")
        for t in range(30, DURATION - 40, 25):
            s.at(t, "interact", p, 10)  # taming attempts, mounting horses, feeding


def mobs2(s):
    """Specific mob behaviours: shields vs ravagers/axes, slimes splitting, phantoms, wololo, cures, conversions."""
    s.cmd(1, "difficulty hard")
    plans = ["shield", "slimes", "phantoms", "evoker", "cure", "water", "nether", "misc"]
    for i, plan in enumerate(plans):
        p = f"tamer{i}"
        x, z = station(s, i, p, r=18)
        s.cmd(9, f"effect give {p} minecraft:resistance infinite 255 true")
        if plan == "shield":   # ravager stun + axe disabling shields + vindicators
            for t in range(20, DURATION - 40, 200):
                s.cmd(t, f"execute positioned {x} {Y} {z} unless entity @e[type=minecraft:ravager,distance=..16] run summon minecraft:ravager {x} {Y} {z + 5}")
                s.cmd(t, f"summon minecraft:vindicator {x + 3} {Y} {z + 3}")
                hand(s, t + 1, p, "minecraft:shield")
                s.at(t + 2, "use", p)
                s.at(t + 150, "release", p)
        elif plan == "slimes":  # big slimes / magma cubes split when killed; oozing / infested / weaving effects
            hand(s, 9, p, ench("netherite_sword", sharpness=5))
            for t in range(20, DURATION - 40, 120):
                s.cmd(t, f"summon minecraft:slime {x} {Y} {z + 3} {{Size:3}}")
                s.cmd(t, f"summon minecraft:magma_cube {x + 2} {Y} {z + 3} {{Size:3}}")
                s.cmd(t + 1, f"effect give @e[type=minecraft:zombie,distance=..20,x={x},y={Y},z={z}] minecraft:oozing 600 0")
                s.cmd(t + 1, f"summon minecraft:zombie {x - 3} {Y} {z + 3} {{active_effects:[{{id:\"minecraft:infested\",duration:600}},{{id:\"minecraft:wind_charged\",duration:600}},{{id:\"minecraft:oozing\",duration:600}}]}}")
            for t in range(25, DURATION - 40, 6):
                s.at(t, "attack", p, 6)
        elif plan == "phantoms":
            s.at(9, "insomnia", p, 200000)
            for t in range(20, DURATION - 40, 400):
                s.cmd(t, f"summon minecraft:phantom {x} {Y + 20} {z} {{Size:{(t // 400) % 4}}}")
        elif plan == "evoker":  # wololo needs blue sheep; illusioner and evoker fangs
            for t in range(20, DURATION - 40, 400):
                s.cmd(t, f"summon minecraft:sheep {x + 4} {Y} {z + 4} {{Color:11}}")
                s.cmd(t, f"summon minecraft:sheep {x + 5} {Y} {z + 4} {{Color:11}}")
                s.cmd(t, f"execute positioned {x} {Y} {z} unless entity @e[type=minecraft:evoker,distance=..16] run summon minecraft:evoker {x} {Y} {z + 8}")
                s.cmd(t, f"execute positioned {x} {Y} {z} unless entity @e[type=minecraft:illusioner,distance=..16] run summon minecraft:illusioner {x - 6} {Y} {z + 8}")
        elif plan == "cure":    # zombie villager curing: weakness + golden apple; zombies converting villagers
            for t in range(20, DURATION - 40, 300):
                s.cmd(t, f"summon minecraft:zombie_villager {x} {Y} {z + 2} {{PersistenceRequired:1b}}")
                s.cmd(t + 1, f"effect give @e[type=minecraft:zombie_villager,distance=..6,x={x},y={Y},z={z}] minecraft:weakness 600 0")
                hand(s, t + 2, p, "minecraft:golden_apple")
                s.at(t + 3, "interact", p, 4)
                s.cmd(t + 5, f"summon minecraft:villager {x + 6} {Y} {z + 6}")
                s.cmd(t + 5, f"summon minecraft:zombie {x + 6} {Y} {z + 9}")
                s.cmd(t + 5, f"summon minecraft:creeper {x - 6} {Y} {z + 2} {{NoAI:1b}}")
                hand(s, t + 6, p, "minecraft:flint_and_steel")
                s.cmd(t + 6, f"tp {p} {x - 5} {Y} {z + 2}")
                s.at(t + 7, "interact", p, 3)  # ignite the creeper
                s.cmd(t + 60, f"tp {p} {x + 0.5} {Y} {z + 0.5}")
        elif plan == "water":   # guardians, dolphins (treasure, swim with player), drowned, axolotls, glow squid
            s.cmd(9, f"fill {x - 15} {Y - 10} {z - 15} {x + 15} {Y + 2} {z + 15} minecraft:water")
            s.cmd(9, f"fill {x - 1} {Y - 10} {z - 1} {x + 1} {Y - 1} {z + 1} minecraft:prismarine")
            for t in range(20, DURATION - 40, 400):
                for m in ("guardian", "dolphin", "drowned", "glow_squid", "pufferfish", "tropical_fish", "salmon"):
                    s.cmd(t, f"summon minecraft:{m} {x + 4} {Y - 4} {z + 4}")
                s.cmd(t + 1, f"summon minecraft:item {x + 4} {Y - 3} {z + 4} {{Item:{{id:\"minecraft:cod\",count:1}}}}")
        elif plan == "nether":  # blaze, ghast, enderman, endermite, wither (needs targets), shulker bullets
            for t in range(20, DURATION - 40, 500):
                for k, m in enumerate(("blaze", "enderman", "endermite", "shulker", "ghast", "silverfish")):
                    s.cmd(t, f"summon minecraft:{m} {x - 8 + k * 3} {Y + (8 if m == 'ghast' else 0)} {z + 8}")
                s.cmd(t, f"execute positioned {x} {Y} {z} unless entity @e[type=minecraft:wither,distance=..40] run summon minecraft:wither {x} {Y + 4} {z - 10} {{Invul:0}}")
                for k in range(4):
                    s.cmd(t + 1, f"summon minecraft:cow {x - 6 + k * 4} {Y} {z - 6}")
            s.cmd(10, f"fill {x + 10} {Y} {z - 4} {x + 12} {Y + 1} {z + 4} minecraft:infested_stone")
            hand(s, 11, p, "minecraft:diamond_sword")
            for t in range(30, DURATION - 40, 20):
                s.at(t, "attack", p, 5)
        else:   # creaking at night in a pale garden, mobs picking up gear, witches, copper golems
            s.cmd(9, f"fillbiome {x - 16} {Y - 4} {z - 16} {x + 16} {Y + 16} {z + 16} minecraft:pale_garden")
            s.cmd(9, f"setblock {x + 6} {Y} {z + 6} minecraft:creaking_heart[natural=true,creaking_heart_state=awake]")
            s.cmd(9, f"setblock {x + 6} {Y + 1} {z + 6} minecraft:pale_oak_log")
            s.cmd(9, f"setblock {x + 6} {Y - 1} {z + 6} minecraft:pale_oak_log")
            for t in range(20, DURATION - 40, 600):
                s.cmd(t, f"summon minecraft:witch {x - 6} {Y} {z + 6}")
                s.cmd(t, f"summon minecraft:copper_golem {x - 3} {Y} {z - 6}")
                s.cmd(t, f"summon minecraft:skeleton {x + 3} {Y} {z - 6} {{CanPickUpLoot:1b}}")
                s.cmd(t, f"summon minecraft:item {x + 3} {Y} {z - 6} {{Item:{{id:\"minecraft:diamond_helmet\",count:1}}}}")
            hand(s, 11, p, "minecraft:iron_axe")
            for t in range(30, DURATION - 40, 30):
                s.at(t, "attack", p, 8)
    s.cmd(10, "time set 18000")


def blocks2(s):
    """Player-driven block interactions and block entities."""
    plans = ["composter", "eggs", "bonemeal2", "use", "loot", "explode", "vehicles", "projectile"]
    for i, plan in enumerate(plans):
        p = f"builder{i}"
        x, z = station(s, i, p, floor="minecraft:grass_block", r=16)
        bx, bz = x, z + 2
        if plan == "composter":
            items = ["wheat_seeds", "oak_leaves", "melon_slice", "pumpkin", "cake", "carrot", "rotten_flesh", "kelp", "moss_block"]
            for t in range(20, DURATION - 40, 4):
                if t % 400 == 20:
                    s.cmd(t, f"setblock {bx} {Y} {bz} minecraft:composter")
                hand(s, t, p, f"minecraft:{items[(t // 4) % len(items)]}")
                s.at(t + 1, "useon", p, bx, Y, bz)
        elif plan == "eggs":   # dragon egg teleport, turtle eggs trampled/hatching, sniffer/frog spawn hatching, chorus
            s.cmd(9, f"fill {x + 4} {Y - 1} {z - 4} {x + 8} {Y - 1} {z + 4} minecraft:sand")
            s.cmd(9, f"fill {x - 8} {Y - 1} {z - 4} {x - 4} {Y - 1} {z + 4} minecraft:water")
            s.cmd(9, f"fill {x - 2} {Y - 1} {z - 8} {x + 2} {Y - 1} {z - 6} minecraft:end_stone")
            s.cmd(9, "gamerule random_tick_speed 300")
            for t in range(20, DURATION - 40, 100):
                s.cmd(t, f"setblock {bx} {Y} {bz} minecraft:dragon_egg")
                s.at(t + 1, "useon", p, bx, Y, bz)
                s.cmd(t + 2, f"setblock {x + 6} {Y} {z} minecraft:turtle_egg[eggs=4]")
                s.cmd(t + 2, f"setblock {x - 6} {Y} {z} minecraft:frogspawn")
                s.cmd(t + 2, f"setblock {x + 6} {Y} {z + 3} minecraft:sniffer_egg[hatch=2]")
                s.cmd(t + 2, f"setblock {x} {Y} {z - 7} minecraft:chorus_flower")
                s.cmd(t + 3, f"summon minecraft:zombie {x + 6} {Y} {z + 1} {{NoAI:0b}}")
                s.cmd(t + 60, f"kill @e[type=minecraft:zombie,x={x},y={Y},z={z},distance=..14]")
        elif plan == "bonemeal2":
            targets = [("grass_block", None), ("netherrack", "crimson_nylium"), ("crimson_nylium", None), ("warped_nylium", None),
                       ("sea_pickle[waterlogged=true]", "brain_coral_block"), ("weeping_vines", "netherrack"),
                       ("twisting_vines", "netherrack"), ("crimson_fungus", "crimson_nylium"), ("warped_fungus", "warped_nylium"),
                       ("pale_moss_block", None), ("moss_block", None), ("mangrove_leaves", None), ("big_dripleaf", "clay"),
                       ("glow_lichen[down=true]", "stone"), ("rooted_dirt", None), ("short_dry_grass", "sand"), ("firefly_bush", None)]
            for t in range(20, DURATION - 40, 6):
                blk, nb = targets[(t // 6) % len(targets)]
                if nb:
                    s.cmd(t, f"setblock {bx + 1} {Y} {bz} minecraft:{nb}")
                if blk.startswith("weeping"):
                    s.cmd(t, f"setblock {bx} {Y + 1} {bz} minecraft:netherrack")
                s.cmd(t, f"setblock {bx} {Y} {bz} minecraft:{blk}")
                hand(s, t + 1, p, "minecraft:bone_meal 64")
                s.at(t + 2, "useon", p, bx, Y, bz)
                s.at(t + 3, "useon", p, bx, Y, bz)
        elif plan == "use":   # fence gates, trapdoors, note blocks, pumpkins + shears, berries, cauldrons, honey, powder snow
            seq = [("oak_fence_gate", "minecraft:air"), ("oak_trapdoor", "minecraft:air"), ("note_block", "minecraft:air"),
                   ("pumpkin", "minecraft:shears"), ("sweet_berry_bush[age=3]", "minecraft:air"), ("cauldron", "minecraft:water_bucket"),
                   ("dirt", "minecraft:potion[minecraft:potion_contents={potion:\"minecraft:water\"}]"), ("obsidian", "minecraft:flint_and_steel"),
                   ("campfire[lit=false]", "minecraft:fire_charge"), ("bell", "minecraft:air"), ("respawn_anchor", "minecraft:glowstone"),
                   ("pale_moss_block", "minecraft:pale_moss_carpet"), ("chiseled_bookshelf", "minecraft:book")]
            s.cmd(9, f"fill {x + 4} {Y} {z + 4} {x + 4} {Y + 3} {z + 4} minecraft:honey_block")
            s.cmd(9, f"setblock {x - 4} {Y} {z + 4} minecraft:powder_snow")
            for t in range(20, DURATION - 40, 5):
                blk, it = seq[(t // 5) % len(seq)]
                s.cmd(t, f"setblock {bx} {Y} {bz} minecraft:{blk}")
                hand(s, t + 1, p, it)
                s.at(t + 2, "useon", p, bx, Y, bz)
            for t in range(40, DURATION - 40, 100):
                s.cmd(t, f"summon minecraft:pig {x + 3.5} {Y + 3} {z + 4.5}")
                s.cmd(t, f"summon minecraft:zombie {x - 4} {Y + 1} {z + 4}")
        elif plan == "loot":  # player-opened loot chests/barrels (random sequences), shulkers, ender chests, item frames
            tables = ["chests/simple_dungeon", "chests/end_city_treasure", "chests/buried_treasure", "chests/desert_pyramid",
                      "chests/stronghold_library", "chests/ancient_city", "chests/bastion_treasure", "chests/shipwreck_treasure",
                      "chests/village/village_weaponsmith", "chests/trial_chambers/reward_ominous"]
            for t in range(20, DURATION - 40, 6):
                kind = ["chest", "barrel", "shulker_box", "ender_chest"][(t // 6) % 4]
                tb = tables[(t // 24) % len(tables)]
                s.cmd(t, f"setblock {bx} {Y} {bz} minecraft:air")
                s.cmd(t + 1, f"setblock {bx} {Y} {bz} minecraft:{kind}" + ("" if kind == "ender_chest" else f"{{LootTable:\"minecraft:{tb}\"}}"))
                hand(s, t + 1, p, "minecraft:air")
                s.at(t + 2, "useon", p, bx, Y, bz)
                s.at(t + 4, "look", p, 0, 0)
            for t in range(30, DURATION - 40, 50):
                s.cmd(t, f"summon minecraft:item_frame {x - 3} {Y + 1} {z} {{Facing:4b,Item:{{id:\"minecraft:diamond\",count:1}},ItemDropChance:0.5f}}")
                s.cmd(t + 5, f"kill @e[type=minecraft:item_frame,x={x},y={Y},z={z},distance=..8]")
        elif plan == "explode":  # fire explosions, TNT minecarts, falling anvils on mobs, furnace XP
            for t in range(20, DURATION - 40, 60):
                s.cmd(t, f"summon minecraft:fireball {x + 5} {Y + 6} {z + 8} {{ExplosionPower:2b,Motion:[0.0,-1.0,0.0]}}")
                s.cmd(t + 10, f"summon minecraft:tnt_minecart {x - 6} {Y} {z + 8} {{TNTFuse:1}}")
                s.cmd(t + 20, f"summon minecraft:cow {x} {Y} {z - 6}")
                s.cmd(t + 21, f"summon minecraft:falling_block {x} {Y + 6} {z - 6} {{BlockState:{{Name:\"minecraft:anvil\"}},HurtEntities:1b,FallHurtAmount:4.0f,FallHurtMax:40}}")
                s.cmd(t + 30, f"fill {x - 10} {Y - 1} {z - 10} {x + 10} {Y - 1} {z + 12} minecraft:grass_block replace minecraft:air")
            s.cmd(9, f"setblock {x + 8} {Y} {z} minecraft:furnace[lit=true]{{Items:[{{Slot:0b,id:\"minecraft:raw_iron\",count:64}},{{Slot:1b,id:\"minecraft:coal_block\",count:64}}]}}")
            for t in range(400, DURATION - 40, 400):
                s.at(t, "useon", p, x + 8, Y, z)
        elif plan == "vehicles":  # boats over bubble columns, furnace minecarts, boosted pigs/striders
            s.cmd(9, f"fill {x - 6} {Y - 6} {z + 3} {x - 2} {Y - 1} {z + 7} minecraft:water")
            s.cmd(9, f"fill {x - 6} {Y - 7} {z + 3} {x - 2} {Y - 7} {z + 7} minecraft:soul_sand")
            s.cmd(9, f"fill {x + 2} {Y} {z - 8} {x + 2} {Y} {z + 8} minecraft:rail")
            for t in range(20, DURATION - 40, 200):
                s.cmd(t, f"summon minecraft:oak_boat {x - 4} {Y} {z + 5}")
                s.cmd(t, f"summon minecraft:furnace_minecart {x + 2} {Y} {z - 7} {{Fuel:3600s}}")
                s.cmd(t, f"summon minecraft:pig {x + 5} {Y} {z} {{saddle:1b}}")
                hand(s, t + 1, p, "minecraft:carrot_on_a_stick")
                s.at(t + 2, "interact", p, 8)
                s.at(t + 10, "use", p)
        else:  # projectiles hitting amethyst, pearls (endermites), fireworks, experience orbs collected
            s.cmd(9, f"fill {x - 3} {Y} {z + 6} {x + 3} {Y + 3} {z + 6} minecraft:amethyst_block")
            for t in range(20, DURATION - 40, 8):
                it = ["ender_pearl", "snowball", "firework_rocket[minecraft:fireworks={explosions:[{shape:\"large_ball\",colors:[I;16711680]}],flight_duration:1}]", "experience_bottle", "egg"][(t // 8) % 5]
                hand(s, t, p, f"minecraft:{it}")
                s.at(t + 1, "look", p, 0, (t * 13) % 60 - 30)
                s.at(t + 2, "use", p)
                if it == "ender_pearl":
                    s.cmd(t + 6, f"tp {p} {x + 0.5} {Y} {z + 0.5}")


def spawning(s):
    """Natural spawners with long game time: phantoms, patrols, wandering traders, sieges, biome-specific spawns."""
    s.cmd(1, "difficulty hard")
    biomes = ["swamp", "jungle", "deep_frozen_ocean", "desert", "mangrove_swamp", "snowy_plains", "mushroom_fields", "basalt_deltas"]
    for i, b in enumerate(biomes):
        p = f"camper{i}"
        x, z = i * 400, -6000
        s.cmd(3, f"forceload add {x - 64} {z - 64} {x + 63} {z + 63}")
        s.cmd(4, f"fillbiome {x - 64} -64 {z - 64} {x + 63} 319 {z + 63} minecraft:{b}")
        s.at(6, "player", p, x + 0.5, 140, z + 0.5)
        s.cmd(8, f"effect give {p} minecraft:resistance infinite 255 true")
        s.cmd(8, f"effect give {p} minecraft:regeneration infinite 4 true")
        s.cmd(9, f"execute as {p} at @s positioned over motion_blocking run tp @s ~ ~ ~")
        s.at(10, "insomnia", p, 300000)
        if i % 2 == 0:
            s.cmd(12, f"execute at {p} run place structure minecraft:{'village_plains' if i != 2 else 'monument'} ~ 64 ~20")
    for g in ("spawn_mobs", "advance_time", "advance_weather", "spawn_patrols", "spawn_wandering_traders", "spawn_phantoms"):
        s.cmd(10, f"gamerule {g} true")
    s.cmd(10, "time set 113000")  # day 5+ so patrols are allowed, starting at night (sieges, phantoms)
    for t in range(200, DURATION, 1500):
        s.cmd(t, "weather thunder")


def worldgen2(s):
    """Features and structures that failed to place earlier: give them the terrain they need."""
    feats = ["iceberg_packed", "iceberg_blue", "ice_spike", "blue_ice", "coral_claw", "coral_tree", "coral_mushroom",
             "oak_bees_005", "birch_bees_005", "warm_ocean_vegetation", "sea_pickle", "kelp", "seagrass_tall"]
    for i in range(4):  # ocean basins: water over sand for icebergs/corals/kelp
        x, z = i * 128, 8000
        s.cmd(3, f"forceload add {x - 48} {z - 48} {x + 47} {z + 47}")
        s.cmd(4, f"fill {x - 40} 50 {z - 40} {x + 40} 50 {z + 40} minecraft:sand")
        s.cmd(5, f"fill {x - 40} 51 {z - 40} {x + 40} 62 {z + 40} minecraft:water")
        s.cmd(5, f"fillbiome {x - 40} 40 {z - 40} {x + 40} 80 {z + 40} minecraft:{['frozen_ocean', 'warm_ocean', 'deep_frozen_ocean', 'lukewarm_ocean'][i]}")
        for j in range(60):
            f = feats[(i * 60 + j) % len(feats)]
            fx, fz = x - 36 + (j % 8) * 10, z - 36 + (j // 8) * 10
            ylvl = 63 if f.startswith("iceberg") else (51 if "coral" in f or f in ("warm_ocean_vegetation", "sea_pickle", "kelp", "seagrass_tall") else 100)
            if ylvl == 100:
                s.cmd(20 + j, f"fill {fx - 2} 99 {fz - 2} {fx + 2} 99 {fz + 2} minecraft:grass_block")
                s.cmd(20 + j, f"fill {fx - 2} 99 {fz - 2} {fx + 2} 99 {fz + 2} minecraft:{'snow_block' if 'ice' in f else 'grass_block'}")
            s.cmd(21 + j, f"place feature minecraft:{f} {fx} {ylvl} {fz}")
    structs = ["desert_pyramid", "mansion", "stronghold", "monument", "ruined_portal", "ruined_portal_desert",
               "ruined_portal_mountain", "ruined_portal_ocean", "ruined_portal_jungle", "ruined_portal_swamp", "jungle_pyramid", "igloo"]
    for j, st in enumerate(structs * 2):
        x, z = j * 300, 12000 + (j % 2) * 300
        s.cmd(100 + j * 5, f"forceload add {x - 80} {z - 80} {x + 79} {z + 79}")
        s.cmd(102 + j * 5, f"place structure minecraft:{st} {x} 64 {z}")


# natural spawners (patrols, traders, sieges, phantoms) work on day cycles: give them longer
LONG = {"spawning": 30000, "villages": 24000}

FAMILIES = {"fishing": fishing, "enchanting": enchanting, "combat": combat, "ranged": ranged, "trading": trading,
            "mining": mining, "itemuse": itemuse, "bartering": bartering, "raids": raids, "trials": trials,
            "breeding": breeding, "bosses": bosses, "natural": natural, "hazards": hazards,
            "villages": villages, "breeding2": breeding2, "mobs2": mobs2, "blocks2": blocks2, "spawning": spawning,
            "worldgen2": worldgen2}


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
    ap.add_argument("--families", default="", help="comma list: write only these to manifest-wave.tsv")
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
            p.write_text(s.text(LONG.get(fam, DURATION)))
            manifest.append((p, seed))
    for name, text in blocks_scenarios(a.jar, out):
        for seed in SEEDS[:2]:
            p = out / f"{name}-{seed}.drv"
            p.write_text(text)
            manifest.append((p, seed))
    if a.families:
        want = set(a.families.split(","))
        wave = [(p, sd) for p, sd in manifest if p.stem.split("-")[1] in want]
        (out / "manifest-wave.tsv").write_text("".join(f"{Path(p).resolve().relative_to(ROOT)}\t{s}\n" for p, s in wave))
        print(f"wave manifest: {len(wave)} scenarios")
    (out / "manifest-player.tsv").write_text("".join(f"{Path(p).resolve().relative_to(ROOT)}\t{s}\n" for p, s in manifest))
    print(f"{len(manifest)} driver scenarios ({len(FAMILIES)} player families x {len(SEEDS)} seeds + block/redstone shards)")


if __name__ == "__main__":
    main()
