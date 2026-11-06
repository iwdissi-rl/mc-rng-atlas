package atlas.agent;

import java.lang.instrument.ClassFileTransformer;
import java.lang.instrument.Instrumentation;
import java.nio.file.Path;
import java.security.ProtectionDomain;
import java.util.Map;
import java.util.jar.JarFile;

import org.objectweb.asm.*;

/**
 * Instruments Minecraft's RNG implementations at load time so every primitive draw and reseed is logged by
 * {@code atlas.hook.Hook}. Usage: {@code java -javaagent:atlas-agent.jar=/path/atlas-hook.jar -Datlas.out=DIR -jar server.jar}
 */
public final class Agent {
    private static final String HOOK = "atlas/hook/Hook";
    private static final String ID = "__atlasId";
    private static final String LEVELGEN = "net/minecraft/world/level/levelgen/";

    /** LCG classes -> (cls code, how their seed is stored). */
    private static final Map<String, Integer> LCG = Map.of(
            LEVELGEN + "LegacyRandomSource", 0,
            LEVELGEN + "SingleThreadedRandomSource", 1,
            LEVELGEN + "ThreadSafeLegacyRandomSource", 2);
    private static final String XORO = LEVELGEN + "Xoroshiro128PlusPlus";
    private static final String SERVER = "net/minecraft/server/MinecraftServer";

    /** Every RNG type from the static scan (atlas.rngTypes = file with one internal class name per line). */
    static final java.util.Set<String> RNG_TYPES = new java.util.HashSet<>();
    static final java.util.concurrent.atomic.AtomicInteger NEXT_TAG = new java.util.concurrent.atomic.AtomicInteger(1);
    static final java.util.concurrent.atomic.AtomicLong TAGGED_SITES = new java.util.concurrent.atomic.AtomicLong();
    /** tag \t class \t method \t desc \t ordinal-of-RNG-call-in-method \t called RNG method */
    static final java.util.concurrent.ConcurrentLinkedQueue<String> TAGS = new java.util.concurrent.ConcurrentLinkedQueue<>();

    public static void premain(String args, Instrumentation inst) throws Exception {
        inst.appendToBootstrapClassLoaderSearch(new JarFile(Path.of(args).toFile()));
        String types = System.getProperty("atlas.rngTypes");
        if (types != null) RNG_TYPES.addAll(java.nio.file.Files.readAllLines(Path.of(types)));
        Path out = Path.of(System.getProperty("atlas.out", "atlas-run"));
        Runtime.getRuntime().addShutdownHook(new Thread(() -> {
            try {
                java.nio.file.Files.createDirectories(out);
                java.nio.file.Files.write(out.resolve("tags.tsv"), TAGS);
            } catch (java.io.IOException e) {
                e.printStackTrace();
            }
        }, "atlas-tags"));
        inst.addTransformer(new ClassFileTransformer() {
            @Override
            public byte[] transform(ClassLoader l, String name, Class<?> c, ProtectionDomain pd, byte[] bytes) {
                if (name == null) return null;
                try {
                    if (LCG.containsKey(name) || name.equals(XORO) || name.equals(SERVER)) {
                        byte[] out = instrument(name, bytes);
                        System.err.println("[atlas] instrumented " + name);
                        return out;
                    }
                    if ((name.startsWith("net/minecraft/") || name.startsWith("com/mojang/")) && !RNG_TYPES.contains(name)) {
                        return tagCallSites(bytes);
                    }
                } catch (Throwable t) {
                    System.err.println("[atlas] FAILED to instrument " + name + ": " + t);
                    t.printStackTrace();
                }
                return null;
            }
        });
        System.err.println("[atlas] agent loaded, logging to " + System.getProperty("atlas.out", "atlas-run"));
    }

    static byte[] instrument(String owner, byte[] bytes) {
        ClassReader cr = new ClassReader(bytes);
        ClassWriter cw = new ClassWriter(cr, ClassWriter.COMPUTE_MAXS);
        boolean isGen = !owner.equals(SERVER);
        cr.accept(new ClassVisitor(Opcodes.ASM9, cw) {
            @Override
            public void visitEnd() {
                if (isGen) cv.visitField(Opcodes.ACC_PUBLIC | Opcodes.ACC_TRANSIENT, ID, "J", null, null).visitEnd();
                super.visitEnd();
            }

            @Override
            public MethodVisitor visitMethod(int acc, String name, String desc, String sig, String[] ex) {
                MethodVisitor mv = super.visitMethod(acc, name, desc, sig, ex);
                if (owner.equals(SERVER)) {
                    if (name.equals("tickServer")) return new Entry(mv, () -> {
                        mv.visitVarInsn(Opcodes.ALOAD, 0);
                        mv.visitMethodInsn(Opcodes.INVOKESTATIC, HOOK, "tick", "(Ljava/lang/Object;)V", false);
                    });
                    return mv;
                }
                if (name.equals("<init>")) return new Ctor(mv, owner, desc);
                if (LCG.containsKey(owner)) {
                    int cls = LCG.get(owner);
                    if (name.equals("next") && desc.equals("(I)I")) {
                        return new Entry(mv, () -> {
                            pushId(mv, owner);
                            pushInt(mv, cls);
                            pushLcgSeed(mv, owner, cls);
                            mv.visitVarInsn(Opcodes.ILOAD, 1);
                            mv.visitMethodInsn(Opcodes.INVOKESTATIC, HOOK, "lcgNext", "(JIJI)V", false);
                        });
                    }
                    if (name.equals("setSeed") && desc.equals("(J)V")) {
                        return new Entry(mv, () -> {
                            pushId(mv, owner);
                            pushInt(mv, cls);
                            mv.visitVarInsn(Opcodes.LLOAD, 1);
                            mv.visitMethodInsn(Opcodes.INVOKESTATIC, HOOK, "lcgSetSeed", "(JIJ)V", false);
                        });
                    }
                }
                if (owner.equals(XORO) && name.equals("nextLong") && desc.equals("()J")) {
                    return new Entry(mv, () -> {
                        pushId(mv, owner);
                        pushXoroState(mv);
                        mv.visitMethodInsn(Opcodes.INVOKESTATIC, HOOK, "xoroNext", "(JJJ)V", false);
                    });
                }
                return mv;
            }
        }, 0);
        return cw.toByteArray();
    }

    /**
     * Wraps every call on an RNG type with Hook.at(tag) / Hook.at(0) so the logger knows the call site without a
     * stack walk. Returns null (class unchanged) when the class makes no RNG calls.
     */
    static byte[] tagCallSites(byte[] bytes) {
        if (RNG_TYPES.isEmpty()) return null;
        ClassReader cr = new ClassReader(bytes);
        boolean[] changed = {false};
        ClassWriter cw = new ClassWriter(cr, ClassWriter.COMPUTE_MAXS);
        cr.accept(new ClassVisitor(Opcodes.ASM9, cw) {
            @Override
            public MethodVisitor visitMethod(int acc, String mname, String mdesc, String sig, String[] ex) {
                return new MethodVisitor(Opcodes.ASM9, super.visitMethod(acc, mname, mdesc, sig, ex)) {
                    int ordinal;

                    @Override
                    public void visitMethodInsn(int op, String o, String n, String d, boolean itf) {
                        if (!RNG_TYPES.contains(o)) {
                            super.visitMethodInsn(op, o, n, d, itf);
                            return;
                        }
                        changed[0] = true;
                        TAGGED_SITES.incrementAndGet();
                        int tag = NEXT_TAG.getAndIncrement();
                        TAGS.add(tag + "\t" + cr.getClassName() + "\t" + mname + "\t" + mdesc + "\t" + (ordinal++) + "\t" + n);
                        mv.visitLdcInsn(tag);
                        mv.visitMethodInsn(Opcodes.INVOKESTATIC, HOOK, "at", "(I)V", false);
                        super.visitMethodInsn(op, o, n, d, itf);
                        mv.visitInsn(Opcodes.ICONST_0);
                        mv.visitMethodInsn(Opcodes.INVOKESTATIC, HOOK, "at", "(I)V", false);
                    }
                };
            }
        }, 0);
        return changed[0] ? cw.toByteArray() : null;
    }

    static void pushId(MethodVisitor mv, String owner) {
        mv.visitVarInsn(Opcodes.ALOAD, 0);
        mv.visitFieldInsn(Opcodes.GETFIELD, owner, ID, "J");
    }

    static void pushInt(MethodVisitor mv, int v) {
        mv.visitLdcInsn(v);
    }

    static void pushLcgSeed(MethodVisitor mv, String owner, int cls) {
        mv.visitVarInsn(Opcodes.ALOAD, 0);
        if (cls == 1) {
            mv.visitFieldInsn(Opcodes.GETFIELD, owner, "seed", "J");
        } else {
            mv.visitFieldInsn(Opcodes.GETFIELD, owner, "seed", "Ljava/util/concurrent/atomic/AtomicLong;");
            mv.visitMethodInsn(Opcodes.INVOKEVIRTUAL, "java/util/concurrent/atomic/AtomicLong", "get", "()J", false);
        }
    }

    static void pushXoroState(MethodVisitor mv) {
        mv.visitVarInsn(Opcodes.ALOAD, 0);
        mv.visitFieldInsn(Opcodes.GETFIELD, XORO, "seedLo", "J");
        mv.visitVarInsn(Opcodes.ALOAD, 0);
        mv.visitFieldInsn(Opcodes.GETFIELD, XORO, "seedHi", "J");
    }

    /** Runs {@code body} at method entry. */
    static final class Entry extends MethodVisitor {
        final Runnable body;

        Entry(MethodVisitor mv, Runnable body) {
            super(Opcodes.ASM9, mv);
            this.body = body;
        }

        @Override
        public void visitCode() {
            super.visitCode();
            body.run();
        }
    }

    /**
     * Assigns the generator id right after the super-constructor call (before the constructor body can call
     * setSeed). For Xoroshiro, also logs the initial state before every return.
     */
    static final class Ctor extends MethodVisitor {
        final String owner, desc;
        boolean idDone;

        Ctor(MethodVisitor mv, String owner, String desc) {
            super(Opcodes.ASM9, mv);
            this.owner = owner;
            this.desc = desc;
        }

        @Override
        public void visitMethodInsn(int op, String o, String n, String d, boolean itf) {
            super.visitMethodInsn(op, o, n, d, itf);
            // The first invokespecial <init> on java/lang/Object is the super() call. A this(...) delegation
            // targets our own class, whose constructor assigns the id itself.
            if (!idDone && op == Opcodes.INVOKESPECIAL && n.equals("<init>") && o.equals("java/lang/Object")) {
                idDone = true;
                mv.visitVarInsn(Opcodes.ALOAD, 0);
                mv.visitMethodInsn(Opcodes.INVOKESTATIC, HOOK, "newId", "()J", false);
                mv.visitFieldInsn(Opcodes.PUTFIELD, owner, ID, "J");
            }
        }

        @Override
        public void visitInsn(int op) {
            if (op == Opcodes.RETURN && owner.equals(XORO) && idDone) {
                pushId(mv, owner);
                pushXoroState(mv);
                mv.visitMethodInsn(Opcodes.INVOKESTATIC, HOOK, "xoroInit", "(JJJ)V", false);
            }
            super.visitInsn(op);
        }
    }
}
