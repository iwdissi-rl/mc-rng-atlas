package atlas.hook;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;

/**
 * Runtime RNG logger. Loaded on the bootstrap class path so instrumented Minecraft classes can call it
 * regardless of class loader. Depends on nothing but java.base.
 *
 * Record layout (43 bytes, big-endian):
 *   u8 type | u8 cls | u64 gen | u64 seq | u32 tick | i64 a | i64 b | u8 bits | u32 site
 * type: 1 = LCG next(bits)   a = seed before          (b unused)
 *       2 = LCG setSeed       a = raw setSeed argument
 *       3 = Xoroshiro next    a = seedLo before, b = seedHi before
 *       4 = Xoroshiro init    a = seedLo, b = seedHi
 * cls:  0 LegacyRandomSource, 1 SingleThreadedRandomSource, 2 ThreadSafeLegacyRandomSource, 3 Xoroshiro128PlusPlus
 */
public final class Hook {
    public static final int RECORD = 43;
    private static final AtomicLong IDS = new AtomicLong(1);
    private static final AtomicLong SEQ = new AtomicLong();
    /** Untagged sites (found by stack walk) get ids from here; tagged ids come from the agent and stay below it. */
    private static final int UNTAGGED_BASE = 1 << 30;
    private static final AtomicInteger SITE_IDS = new AtomicInteger(UNTAGGED_BASE);
    private static final ConcurrentHashMap<String, Integer> SITES = new ConcurrentHashMap<>();
    /** Tagged site id -> resolved "class.method:bci|rngMethod|caller" (resolved by one stack walk on first use). */
    private static final ConcurrentHashMap<Integer, String> TAGGED = new ConcurrentHashMap<>();
    private static final List<Buf> ALL = Collections.synchronizedList(new ArrayList<>());
    private static final StackWalker WALKER = StackWalker.getInstance();
    public static volatile int tick;
    private static final Path OUT;
    private static volatile boolean closed;

    static {
        OUT = Path.of(System.getProperty("atlas.out", "atlas-run"));
        try {
            Files.createDirectories(OUT);
        } catch (IOException e) {
            throw new RuntimeException(e);
        }
        Runtime.getRuntime().addShutdownHook(new Thread(Hook::close, "atlas-flush"));
    }

    private static final class Buf {
        final ByteBuffer b = ByteBuffer.allocateDirect(RECORD * 100_000);
        final FileChannel ch;
        boolean busy; // re-entrancy guard (site lookup must never log itself)
        int site;     // call-site tag set by instrumented code around each RNG call (0 = untagged)

        Buf(String thread) {
            try {
                String safe = thread.replaceAll("[^A-Za-z0-9_.-]", "_");
                ch = FileChannel.open(OUT.resolve("t-" + Thread.currentThread().threadId() + "-" + safe + ".bin"),
                        StandardOpenOption.CREATE, StandardOpenOption.WRITE, StandardOpenOption.TRUNCATE_EXISTING);
            } catch (IOException e) {
                throw new RuntimeException(e);
            }
        }

        synchronized void flush() {
            b.flip();
            try {
                while (b.hasRemaining()) ch.write(b);
            } catch (IOException e) {
                throw new RuntimeException(e);
            }
            b.clear();
        }
    }

    private static final ThreadLocal<Buf> BUF = ThreadLocal.withInitial(() -> {
        Buf b = new Buf(Thread.currentThread().getName());
        ALL.add(b);
        return b;
    });

    public static long newId() {
        return IDS.getAndIncrement();
    }

    /** Called by instrumented code immediately before (id) and after (0) every call on an RNG object. */
    public static void at(int id) {
        BUF.get().site = id;
    }

    public static void tick() {
        tick++;
    }

    public static void lcgNext(long gen, int cls, long seed, int bits) {
        record(1, cls, gen, seed, 0, bits);
    }

    public static void lcgSetSeed(long gen, int cls, long seed) {
        record(2, cls, gen, seed, 0, 0);
    }

    public static void xoroNext(long gen, long lo, long hi) {
        record(3, 3, gen, lo, hi, 0);
    }

    public static void xoroInit(long gen, long lo, long hi) {
        record(4, 3, gen, lo, hi, 0);
    }

    private static void record(int type, int cls, long gen, long a, long b, int bits) {
        if (closed) return;
        Buf buf = BUF.get();
        if (buf.busy) return;
        buf.busy = true;
        try {
            int tag = buf.site;
            int site;
            if (tag != 0) {
                site = tag;
                if (!TAGGED.containsKey(tag)) TAGGED.putIfAbsent(tag, siteKey());
            } else {
                site = site();
            }
            synchronized (buf) {
                if (buf.b.remaining() < RECORD) buf.flush();
                buf.b.put((byte) type).put((byte) cls).putLong(gen).putLong(SEQ.getAndIncrement()).putInt(tick)
                        .putLong(a).putLong(b).put((byte) bits).putInt(site);
            }
        } finally {
            buf.busy = false;
        }
    }

    static boolean isRng(String c) {
        return c.startsWith("atlas.") || c.contains("RandomSource") || c.endsWith("Xoroshiro128PlusPlus")
                || c.endsWith("MarsagliaPolarGaussian") || c.endsWith("WorldgenRandom") || c.contains("RandomFactory")
                || c.endsWith("RandomSupport");
    }

    /** "site|rngMethod|caller": the first frame outside RNG classes, the RNG method it called, and its caller. */
    private static int site() {
        String key = siteKey();
        Integer id = SITES.get(key);
        if (id == null) id = SITES.computeIfAbsent(key, k -> SITE_IDS.getAndIncrement());
        return id;
    }

    private static String siteKey() {
        return WALKER.walk(s -> {
            String rngMethod = "?";
            String site = null;
            Iterator<StackWalker.StackFrame> it = s.iterator();
            while (it.hasNext()) {
                StackWalker.StackFrame f = it.next();
                if (isRng(f.getClassName())) {
                    if (!f.getClassName().startsWith("atlas.")) rngMethod = f.getMethodName();
                    continue;
                }
                if (site == null) {
                    site = fmt(f);
                    String caller = it.hasNext() ? fmt(it.next()) : "";
                    return site + "|" + rngMethod + "|" + caller;
                }
            }
            return "?|" + rngMethod + "|";
        });
    }

    private static String fmt(StackWalker.StackFrame f) {
        return f.getClassName().replace('.', '/') + "." + f.getMethodName() + ":" + f.getByteCodeIndex();
    }

    private static synchronized void close() {
        if (closed) return;
        closed = true;
        synchronized (ALL) {
            for (Buf b : ALL) {
                b.flush();
                try {
                    b.ch.close();
                } catch (IOException ignored) {
                }
            }
        }
        StringBuilder sb = new StringBuilder();
        SITES.forEach((k, v) -> sb.append(v).append('\t').append(k).append('\n'));
        TAGGED.forEach((k, v) -> sb.append(k).append('\t').append(v).append('\n'));
        try {
            Files.writeString(OUT.resolve("sites.tsv"), sb.toString());
            Files.writeString(OUT.resolve("meta.txt"), "events=" + SEQ.get() + "\nticks=" + tick + "\ngenerators=" + (IDS.get() - 1) + "\n");
        } catch (IOException e) {
            throw new RuntimeException(e);
        }
    }
}
