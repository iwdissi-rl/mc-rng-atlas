package atlas.hook;

import java.lang.reflect.*;
import java.nio.file.*;
import java.util.*;

/**
 * Scripted fake players for headless servers. Runs on the server thread at the start of every tick (called from
 * the instrumented MinecraftServer.tickServer). Everything goes through reflection on the unobfuscated 26.3 names,
 * so the driver has no compile-time dependency on Minecraft.
 *
 * Script (-Datlas.driver=FILE): one action per line, "TICK action args...", TICK counted from the first server
 * tick. Actions:
 *   player NAME X Y Z            spawn a fake player (survival) in the overworld
 *   cmd COMMAND...               run a console command (e.g. "give NAME minecraft:fishing_rod")
 *   look NAME YAW PITCH          set view rotation
 *   slot NAME N                  select hotbar slot N
 *   use NAME                     use the held item (eat, throw, cast, start drawing a bow ...)
 *   release NAME                 release a held use (fire a bow / trident after drawing it for some ticks)
 *   useon NAME X Y Z             use the held item on a block (top face)
 *   break NAME X Y Z             mine a block as the player (tool, enchantments and durability apply)
 *   attack NAME [RADIUS]         attack the nearest non-player living entity
 *   interact NAME [RADIUS]       interact with the nearest non-player entity
 *   drop NAME                    drop the held stack (e.g. gold for piglin bartering)
 *   enchant NAME X Y Z BUTTON    open the enchanting table at X Y Z, enchant the held item with lapis, take it back
 *   trade NAME [RADIUS]          open the nearest villager/trader and buy every offer once (needs payment items)
 *   fish NAME CATCHES            keep casting and reel in on every bite until CATCHES catches (stateful)
 *   insomnia NAME TICKS          set the player's time-since-rest statistic (phantom spawning)
 *   stop                         halt the server
 */
public final class Driver {
    private static final List<String[]> SCRIPT = new ArrayList<>();
    private static int cursor, startTick = -1;
    private static Object server;
    private static ClassLoader cl;
    private static final Map<String, Object> PLAYERS = new LinkedHashMap<>();
    private static final Map<String, int[]> FISHING = new HashMap<>(); // name -> {target, caught, cooldown}
    private static boolean enabled;

    static {
        String f = System.getProperty("atlas.driver");
        if (f != null) {
            try {
                for (String line : Files.readAllLines(Path.of(f))) {
                    line = line.strip();
                    if (line.isEmpty() || line.startsWith("#")) continue;
                    SCRIPT.add(line.split("\\s+"));
                }
                SCRIPT.sort(Comparator.comparingInt(a -> Integer.parseInt(a[0])));
                enabled = true;
                log("loaded " + SCRIPT.size() + " actions from " + f);
            } catch (Exception e) {
                throw new RuntimeException(e);
            }
        }
    }

    static void log(String s) {
        System.err.println("[atlas-driver] " + s);
    }

    /** Called from Hook.tick on the server thread. */
    static void tick(Object srv, int tick) {
        if (!enabled) return;
        if (server == null) {
            server = srv;
            cl = srv.getClass().getClassLoader();
        }
        if (startTick < 0) startTick = tick;
        int t = tick - startTick;
        for (Object p : PLAYERS.values()) {
            try {
                call(p, "doTick");
            } catch (Throwable e) {
                log("doTick failed: " + root(e));
            }
        }
        for (var e : FISHING.entrySet()) fishTick(e.getKey(), e.getValue());
        while (cursor < SCRIPT.size() && Integer.parseInt(SCRIPT.get(cursor)[0]) <= t) {
            String[] a = SCRIPT.get(cursor++);
            try {
                run(a);
            } catch (Throwable e) {
                log("tick " + t + " '" + String.join(" ", a) + "' failed: " + root(e));
            }
        }
    }

    private static void run(String[] a) throws Throwable {
        String act = a[1];
        switch (act) {
            case "player" -> spawn(a[2], d(a[3]), d(a[4]), d(a[5]));
            case "cmd" -> command(String.join(" ", Arrays.copyOfRange(a, 2, a.length)));
            case "look" -> {
                Object p = p(a[2]);
                call(p, "setYRot", f(a[3]));
                call(p, "setXRot", f(a[4]));
            }
            case "slot" -> call(call(p(a[2]), "getInventory"), "setSelectedSlot", Integer.parseInt(a[3]));
            case "use" -> {
                Object p = p(a[2]);
                Object gm = field(p, "gameMode");
                call(gm, "useItem", p, call(p, "level"), call(p, "getMainHandItem"), mainHand());
            }
            case "useon" -> {
                Object p = p(a[2]);
                Object pos = blockPos(a[3], a[4], a[5]);
                call(field(p, "gameMode"), "useItemOn", p, call(p, "level"), call(p, "getMainHandItem"), mainHand(), hit(pos));
            }
            case "release" -> call(p(a[2]), "releaseUsingItem"); // fire a charged bow/crossbow/trident
            case "break" -> call(field(p(a[2]), "gameMode"), "destroyBlock", blockPos(a[3], a[4], a[5]));
            case "attack" -> {
                Object p = p(a[2]);
                Object target = nearest(p, a.length > 3 ? d(a[3]) : 4.0, true);
                if (target != null) {
                    call(p, "attack", target);
                    call(p, "resetAttackStrengthTicker");
                }
            }
            case "interact" -> {
                Object p = p(a[2]);
                Object target = nearest(p, a.length > 3 ? d(a[3]) : 4.0, false);
                if (target != null) call(p, "interactOn", target, mainHand(), call(target, "position"));
            }
            case "drop" -> call(p(a[2]), "drop", true);
            case "enchant" -> enchant(p(a[2]), blockPos(a[3], a[4], a[5]), Integer.parseInt(a[6]));
            case "trade" -> trade(p(a[2]), a.length > 3 ? d(a[3]) : 4.0);
            case "fish" -> FISHING.put(a[2], new int[] {Integer.parseInt(a[3]), 0, 0, 0});
            case "insomnia" -> { // set time-since-rest (phantoms spawn for players awake 3+ in-game days)
                Object p = p(a[2]);
                Object stat = call(staticField("net.minecraft.stats.Stats", "CUSTOM"), "get", staticField("net.minecraft.stats.Stats", "TIME_SINCE_REST"));
                call(call(p, "getStats"), "setValue", p, stat, Integer.parseInt(a[3]));
            }
            case "stop" -> call(server, "halt", false);
            default -> log("unknown action " + act);
        }
    }

    // ---- actions ----

    private static void spawn(String name, double x, double y, double z) throws Throwable {
        Object level = call(server, "overworld");
        Object profile = cls("com.mojang.authlib.GameProfile").getConstructor(UUID.class, String.class)
                .newInstance(UUID.nameUUIDFromBytes(("atlas:" + name).getBytes()), name);
        Object cookie = cls("net.minecraft.server.network.CommonListenerCookie").getMethod("createInitial", profile.getClass(), boolean.class)
                .invoke(null, profile, false);
        Object info = call(cookie, "clientInformation");
        Class<?> sp = cls("net.minecraft.server.level.ServerPlayer");
        Object player = sp.getConstructors()[0].getParameterCount() == 4
                ? sp.getConstructors()[0].newInstance(server, level, profile, info)
                : ctor(sp, 4).newInstance(server, level, profile, info);
        Class<?> flow = cls("net.minecraft.network.protocol.PacketFlow");
        Object conn = cls("net.minecraft.network.Connection").getConstructor(flow).newInstance(enumConst(flow, "SERVERBOUND"));
        Class<?> handler = cls("io.netty.channel.ChannelHandler");
        Object handlers = Array.newInstance(handler, 1);
        Array.set(handlers, 0, conn);
        cls("io.netty.channel.embedded.EmbeddedChannel").getConstructor(handlers.getClass()).newInstance(handlers);
        call(call(server, "getPlayerList"), "placeNewPlayer", conn, player, cookie);
        call(player, "snapTo", x, y, z, 0f, 0f);
        PLAYERS.put(name, player);
        command("gamemode survival " + name);
        log("spawned fake player " + name + " at " + x + " " + y + " " + z);
    }

    private static void command(String c) throws Throwable {
        Object src = call(server, "createCommandSourceStack");
        call(call(server, "getCommands"), "performPrefixedCommand", src, c);
    }

    private static void enchant(Object p, Object tablePos, int button) throws Throwable {
        Object held = call(p, "getMainHandItem");
        call(field(p, "gameMode"), "useItemOn", p, call(p, "level"), held, mainHand(), hit(tablePos));
        Object menu = field(p, "containerMenu");
        if (!menu.getClass().getSimpleName().equals("EnchantmentMenu")) {
            log("enchant: table did not open (" + menu.getClass().getSimpleName() + ")");
            return;
        }
        Object item = call(held, "copyAndClear");
        Object lapis = cls("net.minecraft.world.item.ItemStack").getConstructor(cls("net.minecraft.world.level.ItemLike"), int.class)
                .newInstance(staticField("net.minecraft.world.item.Items", "LAPIS_LAZULI"), 3);
        call(call(menu, "getSlot", 0), "set", item);
        call(call(menu, "getSlot", 1), "set", lapis);
        call(menu, "clickMenuButton", p, button);
        call(menu, "quickMoveStack", p, 0); // enchanted item back to inventory
        call(menu, "quickMoveStack", p, 1);
        call(p, "closeContainer");
    }

    private static void trade(Object p, double r) throws Throwable {
        Object v = nearestOf(p, r, "AbstractVillager");
        if (v == null) {
            log("trade: no villager in range");
            return;
        }
        call(p, "interactOn", v, mainHand(), call(v, "position"));
        Object menu = field(p, "containerMenu");
        if (!menu.getClass().getSimpleName().equals("MerchantMenu")) {
            log("trade: merchant menu did not open (" + menu.getClass().getSimpleName() + ")");
            return;
        }
        int n = (int) call(call(menu, "getOffers"), "size");
        for (int i = 0; i < n; i++) {
            call(menu, "setSelectionHint", i);
            call(menu, "tryMoveItems", i);
            call(menu, "quickMoveStack", p, 2); // take the result: pays, uses the offer, gives villager XP
        }
        call(p, "closeContainer");
    }

    /** st = {targetCatches, caught, cooldown, ticks} */
    private static void fishTick(String name, int[] st) {
        try {
            Object p = p(name);
            if (st[1] >= st[0]) return;
            st[3]++;
            Object hook = field(p, "fishing");
            boolean debug = Boolean.getBoolean("atlas.debugFishing") && st[3] % 40 == 0;
            if (debug) {
                log(name + " t=" + st[3] + " held=" + call(p, "getMainHandItem") + (hook == null ? " hook=null" : " state=" + field(hook, "currentState")
                        + " nibble=" + field(hook, "nibble") + " lured=" + field(hook, "timeUntilLured") + " hooked=" + field(hook, "timeUntilHooked")
                        + " pos=" + call(hook, "position") + " removed=" + call(hook, "isRemoved")));
            }
            if (st[2] > 0) {
                st[2]--;
                return;
            }
            Object gm = field(p, "gameMode");
            if (hook == null) {
                Object r = call(gm, "useItem", p, call(p, "level"), call(p, "getMainHandItem"), mainHand()); // cast
                if (debug || st[3] < 20) log(name + " cast -> " + r);
                st[2] = 10;
                return;
            }
            if ((int) field(hook, "nibble") > 0) {
                call(gm, "useItem", p, call(p, "level"), call(p, "getMainHandItem"), mainHand()); // reel in the catch
                st[1]++;
                st[2] = 10;
                if (st[1] % 25 == 0 || st[1] == st[0]) log(name + " caught " + st[1] + "/" + st[0]);
            }
        } catch (Throwable e) {
            log("fish " + name + " failed: " + root(e));
            st[1] = st[0];
        }
    }

    // ---- helpers ----

    private static Object p(String name) {
        Object p = PLAYERS.get(name);
        if (p == null) throw new IllegalArgumentException("no fake player " + name);
        return p;
    }

    private static Object nearest(Object p, double r, boolean livingOnly) throws Throwable {
        return nearestOf(p, r, livingOnly ? "LivingEntity" : "Entity");
    }

    /** Nearest entity within r that is an instance of a class with the given simple name (and not a player). */
    private static Object nearestOf(Object p, double r, String type) throws Throwable {
        Object box = call(call(p, "getBoundingBox"), "inflate", r);
        List<?> es = (List<?>) call(call(p, "level"), "getEntities", p, box);
        Object best = null;
        double bd = Double.MAX_VALUE;
        for (Object e : es) {
            if (isA(e, "Player") || !isA(e, type)) continue;
            double dd = (double) call(p, "distanceToSqr", e);
            if (dd < bd) {
                bd = dd;
                best = e;
            }
        }
        return best;
    }

    private static boolean isA(Object o, String simple) {
        for (Class<?> c = o.getClass(); c != null; c = c.getSuperclass()) if (c.getSimpleName().equals(simple)) return true;
        return false;
    }

    private static Object blockPos(String x, String y, String z) throws Throwable {
        return cls("net.minecraft.core.BlockPos").getConstructor(int.class, int.class, int.class)
                .newInstance(Integer.parseInt(x), Integer.parseInt(y), Integer.parseInt(z));
    }

    private static Object hit(Object pos) throws Throwable {
        Class<?> vec3 = cls("net.minecraft.world.phys.Vec3");
        Object center = vec3.getMethod("atCenterOf", cls("net.minecraft.core.Vec3i")).invoke(null, pos);
        Class<?> dir = cls("net.minecraft.core.Direction");
        return cls("net.minecraft.world.phys.BlockHitResult").getConstructor(vec3, dir, pos.getClass(), boolean.class)
                .newInstance(center, enumConst(dir, "UP"), pos, false);
    }

    private static Object mainHand() throws Throwable {
        return enumConst(cls("net.minecraft.world.InteractionHand"), "MAIN_HAND");
    }

    private static Class<?> cls(String n) throws ClassNotFoundException {
        return Class.forName(n, true, cl);
    }

    private static Object enumConst(Class<?> c, String n) {
        for (Object o : c.getEnumConstants()) if (((Enum<?>) o).name().equals(n)) return o;
        throw new IllegalArgumentException(c + "." + n);
    }

    private static Object staticField(String c, String f) throws Throwable {
        return cls(c).getField(f).get(null);
    }

    private static Constructor<?> ctor(Class<?> c, int n) {
        for (Constructor<?> k : c.getConstructors()) if (k.getParameterCount() == n) return k;
        throw new IllegalArgumentException("no " + n + "-arg constructor on " + c);
    }

    private static final Map<String, Method> METHODS = new HashMap<>();

    /** Calls a public method by name and argument count (first match whose parameter types accept the args). */
    static Object call(Object target, String name, Object... args) throws Throwable {
        Class<?> c = target.getClass();
        String key = c.getName() + "#" + name + "/" + args.length;
        Method m = METHODS.get(key);
        if (m == null) {
            outer:
            for (Method k : c.getMethods()) {
                if (!k.getName().equals(name) || k.getParameterCount() != args.length) continue;
                Class<?>[] pt = k.getParameterTypes();
                for (int i = 0; i < pt.length; i++) if (args[i] != null && !wrap(pt[i]).isInstance(args[i])) continue outer;
                m = k;
                break;
            }
            if (m == null) throw new NoSuchMethodException(c.getName() + "." + name + "/" + args.length);
            m.setAccessible(true);
            METHODS.put(key, m);
        }
        try {
            return m.invoke(target, args);
        } catch (InvocationTargetException e) {
            throw e.getCause();
        }
    }

    static Object field(Object target, String name) throws Throwable {
        for (Class<?> c = target.getClass(); c != null; c = c.getSuperclass()) {
            try {
                Field f = c.getDeclaredField(name);
                f.setAccessible(true);
                return f.get(target);
            } catch (NoSuchFieldException ignored) {
            }
        }
        throw new NoSuchFieldException(name);
    }

    private static Class<?> wrap(Class<?> c) {
        if (!c.isPrimitive()) return c;
        return switch (c.getName()) {
            case "int" -> Integer.class;
            case "long" -> Long.class;
            case "double" -> Double.class;
            case "float" -> Float.class;
            case "boolean" -> Boolean.class;
            case "byte" -> Byte.class;
            case "short" -> Short.class;
            default -> Character.class;
        };
    }

    private static double d(String s) {
        return Double.parseDouble(s);
    }

    private static float f(String s) {
        return Float.parseFloat(s);
    }

    private static String root(Throwable e) {
        while (e.getCause() != null) e = e.getCause();
        return e.toString();
    }
}
