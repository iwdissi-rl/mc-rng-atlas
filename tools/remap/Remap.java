// Remaps an obfuscated Minecraft jar to readable names so the atlas scanner sees real class/method names.
//
//   java -cp <tiny-remapper, mapping-io, asm*> Remap.java proguard <in.jar> <out.jar> <client_mappings.txt>
//   java -cp <...>                             Remap.java mcp      <in.jar> <out.jar> <joined.srg> <fields.csv> <methods.csv>
//
// proguard: Mojang's official mappings (named -> obf; switched here to obf -> named).
// mcp:      MCP SRG (obf -> searge) with the MCP CSVs (searge -> human names), as used by Forge 1.12.

import java.nio.file.*;
import java.util.*;

import net.fabricmc.mappingio.MappingReader;
import net.fabricmc.mappingio.adapter.MappingSourceNsSwitch;
import net.fabricmc.mappingio.format.MappingFormat;
import net.fabricmc.mappingio.tree.MappingTree;
import net.fabricmc.mappingio.tree.MemoryMappingTree;
import net.fabricmc.tinyremapper.IMappingProvider;
import net.fabricmc.tinyremapper.OutputConsumerPath;
import net.fabricmc.tinyremapper.TinyRemapper;
import org.objectweb.asm.ClassReader;
import org.objectweb.asm.ClassVisitor;
import org.objectweb.asm.FieldVisitor;
import org.objectweb.asm.Opcodes;

public class Remap {
    public static void main(String[] a) throws Exception {
        String mode = a[0];
        Path in = Path.of(a[1]), out = Path.of(a[2]);
        MemoryMappingTree tree = new MemoryMappingTree();
        Map<String, String> csv = new HashMap<>();
        if (mode.equals("proguard")) {
            MemoryMappingTree raw = new MemoryMappingTree();
            MappingReader.read(Path.of(a[3]), MappingFormat.PROGUARD_FILE, raw);
            // ProGuard files map named -> obf; make obf the source namespace.
            raw.accept(new MappingSourceNsSwitch(tree, raw.getDstNamespaces().get(0)));
        } else if (mode.equals("mcp")) {
            MappingReader.read(Path.of(a[3]), MappingFormat.SRG_FILE, tree);
            for (int i = 4; i < a.length; i++) {
                List<String> lines = Files.readAllLines(Path.of(a[i]));
                for (String line : lines.subList(1, lines.size())) { // skip CSV header
                    String[] p = line.split(",", 3);
                    if (p.length >= 2) csv.put(p[0], p[1]); // searge -> name
                }
            }
        } else throw new IllegalArgumentException(mode);

        // SRG lists fields without descriptors; read them from the jar (owner/name -> desc).
        Map<String, String> fieldDesc = new HashMap<>();
        try (FileSystem fs = FileSystems.newFileSystem(in)) {
            try (var walk = Files.walk(fs.getPath("/"))) {
                for (Path p : (Iterable<Path>) walk.filter(x -> x.toString().endsWith(".class"))::iterator) {
                    new ClassReader(Files.readAllBytes(p)).accept(new ClassVisitor(Opcodes.ASM9) {
                        String owner;
                        @Override public void visit(int v, int acc, String name, String sig, String sup, String[] itf) { owner = name; }
                        @Override public FieldVisitor visitField(int acc, String name, String desc, String sig, Object val) {
                            fieldDesc.put(owner + "/" + name, desc);
                            return null;
                        }
                    }, ClassReader.SKIP_CODE);
                }
            }
        }

        int[] counts = new int[3];
        IMappingProvider provider = acceptor -> {
            for (MappingTree.ClassMapping c : tree.getClasses()) {
                String dst = c.getDstName(0);
                if (dst != null) { acceptor.acceptClass(c.getSrcName(), dst); counts[0]++; }
                for (MappingTree.FieldMapping f : c.getFields()) {
                    String n = f.getDstName(0);
                    String desc = f.getSrcDesc() != null ? f.getSrcDesc() : fieldDesc.get(c.getSrcName() + "/" + f.getSrcName());
                    if (n == null || desc == null) continue;
                    acceptor.acceptField(new IMappingProvider.Member(c.getSrcName(), f.getSrcName(), desc), csv.getOrDefault(n, n));
                    counts[1]++;
                }
                for (MappingTree.MethodMapping m : c.getMethods()) {
                    String n = m.getDstName(0);
                    if (n == null) continue;
                    acceptor.acceptMethod(new IMappingProvider.Member(c.getSrcName(), m.getSrcName(), m.getSrcDesc()), csv.getOrDefault(n, n));
                    counts[2]++;
                }
            }
        };

        TinyRemapper r = TinyRemapper.newRemapper().withMappings(provider).renameInvalidLocals(false).build();
        Files.deleteIfExists(out);
        try (OutputConsumerPath oc = new OutputConsumerPath.Builder(out).build()) {
            r.readInputs(in);
            r.apply(oc);
        } finally {
            r.finish();
        }
        System.out.printf("remapped %s -> %s (%d classes, %d fields, %d methods mapped)%n", in, out, counts[0], counts[1], counts[2]);
    }
}
