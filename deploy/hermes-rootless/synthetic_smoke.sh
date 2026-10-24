#!/bin/sh
# Bounded synthetic storage-boundary probe. No network, actual task or secrets.
set -eu
test "$(id -u)" = "1000"
test -f /workspace/hermes-home/config.yaml
test -L /workspace/.hermes/config.yaml
test ! -f /workspace/.hermes/.env
test ! -f /workspace/hermes-home/.env
test "$(cat /research/knowledge/marker.txt)" = "SYNTHETIC_ONLY"
grep -q '^NoNewPrivs:[[:space:]]*1$' /proc/self/status
grep -q '^CapEff:[[:space:]]*0000000000000000$' /proc/self/status
if ( printf X > /research/knowledge/protected-marker.txt ) 2>/dev/null; then
    echo SYNTHETIC_RO_KNOWLEDGE_WRITE_ALLOWED >&2
    exit 43
fi
if ( printf X > /etc/rootless-write-forbidden ) 2>/dev/null; then
    echo SYNTHETIC_ROOTFS_WRITE_ALLOWED >&2
    exit 44
fi
for denied in /media/scott/SSD_4TB /media/scott/NAS5 /root/knowledge /home/scott/knowledge; do
    if test -e "$denied"; then
        echo SYNTHETIC_PROTECTED_PATH_VISIBLE >&2
        exit 45
    fi
done
printf "PRIVATE_SCOPE_WRITE_OK\n" > /workspace/synthetic-result.txt
test "$(cat /workspace/synthetic-result.txt)" = "PRIVATE_SCOPE_WRITE_OK"
echo ROOTLESS_COMPOSE_STORAGE_ISOLATION_PASS
