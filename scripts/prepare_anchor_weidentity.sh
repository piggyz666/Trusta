#!/usr/bin/env bash
set -euo pipefail
TASK_WORKSPACE="$(cd "$(dirname "$0")/.." && pwd)"
TASK_TOOLCHAIN="$TASK_WORKSPACE/data/anchor-integration/toolchain"
TASK_HOST=$(ip route show default | awk '{print $3; exit}')
cat > "$TASK_TOOLCHAIN/maven-settings.xml" <<EOF
<settings><localRepository>$TASK_TOOLCHAIN/maven-repository</localRepository><mirrors><mirror><id>trusta-public-maven-relay</id><mirrorOf>*</mirrorOf><url>http://$TASK_HOST:18890</url></mirror></mirrors></settings>
EOF
chmod u+x "$TASK_TOOLCHAIN/apache-maven-3.9.9/bin/mvn"
cd "$TASK_WORKSPACE"
"$TASK_TOOLCHAIN/apache-maven-3.9.9/bin/mvn" -s "$TASK_TOOLCHAIN/maven-settings.xml" -f integrations/weidentity/pom.xml dependency:copy-dependencies -DoutputDirectory="$TASK_TOOLCHAIN/weid-lib" > data/anchor-integration/weid-dependencies.log 2>&1
