#!/usr/bin/env bash
# Builds build/atlas-hook.jar (bootstrap logger) and build/atlas-agent.jar (ASM transformer, ASM bundled).
set -euo pipefail
cd "$(dirname "$0")"
ASM_DIR=${ASM_DIR:-../work/tools}
rm -rf build && mkdir -p build/hook build/agent
javac --release 21 -d build/hook $(find src/hook -name '*.java')
jar --create --file build/atlas-hook.jar -C build/hook .
javac --release 21 -cp "$ASM_DIR/asm.jar:build/hook" -d build/agent $(find src/agent -name '*.java')
(cd build/agent && jar xf "../../$ASM_DIR/asm.jar" && rm -rf META-INF module-info.class)
printf 'Premain-Class: atlas.agent.Agent\n' > build/manifest.txt
jar --create --file build/atlas-agent.jar --manifest build/manifest.txt -C build/agent .
echo "built build/atlas-hook.jar build/atlas-agent.jar"
