#!/usr/bin/env bash
# Creates a NEW eight-validator test chain. Never starts the research chain.
set -euo pipefail
LAB_ROOT="$(cd "$(dirname "$0")/../data/anchor-integration" && pwd)/network"
SOURCE_ROOT=/mnt/d/ANCHOR_V21_RUNTIME_20261001/bmacp_chain_8node
if [[ -e "$LAB_ROOT" ]]; then
  if [[ -f "$LAB_ROOT/INITIALIZED" ]]; then
    echo 'Existing lab preserved; use its explicit lifecycle commands.'
    exit 0
  fi
  echo 'Incomplete lab preserved; operator recovery required.' >&2
  exit 3
fi
for port in $(seq 9645 9652) $(seq 22400 22407) $(seq 32400 32407); do
  if ss -ltnH | awk '{print $4}' | grep -qE ":${port}$"; then
    echo 'Lab port unavailable; refusing to alter another service.' >&2
    exit 2
  fi
done
mkdir -p "$LAB_ROOT"
chmod 700 "$LAB_ROOT"
cp "$SOURCE_ROOT/build_chain.sh" "$LAB_ROOT/build_chain.sh"
cp "$SOURCE_ROOT/nodes/127.0.0.1/fisco-bcos" "$LAB_ROOT/fisco-bcos"
cd "$LAB_ROOT"
chmod u+x fisco-bcos
# The explicit local binary avoids unpinned downloads. Fresh keys/genesis only.
bash build_chain.sh -l 127.0.0.1:8 -p 32400,22400,9645 -C 101 -v 2.9.1 -e "$LAB_ROOT/fisco-bcos" -o nodes
sed -i 's/channel_listen_ip=0.0.0.0/channel_listen_ip=127.0.0.1/; s/    listen_ip=0.0.0.0/    listen_ip=127.0.0.1/' nodes/127.0.0.1/node*/config.ini
chmod -R go-rwx "$LAB_ROOT"
cd nodes/127.0.0.1
bash start_all.sh
touch "$LAB_ROOT/INITIALIZED"
echo 'TRUSTA_NEW_FISCO_TEST_CHAIN: chain=101 group=1 nodes=8 rpc=9645..9652'
