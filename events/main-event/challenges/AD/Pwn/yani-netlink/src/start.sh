#!/bin/sh
set -eu

ROOT=${YANI_ROOT:-/opt/yani}
RUNTIME=${YANI_RUNTIME:-/run/yani}
FLAG_SOURCE=${RSCTF_FLAG_FILE:-/run/rsctf/flag}
FLAG_IMAGE=$RUNTIME/flag.img
SERIAL_SOCKET=$RUNTIME/serial.sock
HOST_PORT=${YANI_HOST_PORT:-31337}

mkdir -p "$RUNTIME"
truncate -s 4096 "$FLAG_IMAGE"

sync_flag() {
	STAGED=$RUNTIME/.flag.staged
	python3 - "$FLAG_SOURCE" "$STAGED" <<'PY'
import os
import sys

source, target = sys.argv[1:]
try:
    with open(source, "rb") as stream:
        value = stream.read(255).replace(b"\r", b"").replace(b"\n", b"")
except OSError:
    raise SystemExit(f"flag source is unavailable: {source}")
payload = (value + b"\n")[:4096].ljust(4096, b"\0")
with open(target, "wb") as stream:
    stream.write(payload)
    stream.flush()
    os.fsync(stream.fileno())
PY
	dd if="$STAGED" of="$FLAG_IMAGE" bs=4096 conv=notrunc status=none
	unlink "$STAGED"
}

sync_flag
(
	while :; do
		sync_flag
		sleep 0.25
	done
) &
SYNC_PID=$!
cleanup() {
	kill "$SYNC_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

rm -f "$SERIAL_SOCKET"
qemu-system-x86_64 \
	-cpu qemu64,-smep,-smap,-umip \
	-m 256M \
	-smp 2 \
	-kernel "$ROOT/bzImage" \
	-initrd "$ROOT/initramfs.cpio.gz" \
	-nographic \
	-monitor none \
	-chardev socket,id=yaniserial,path="$SERIAL_SOCKET",server=on,wait=off \
	-serial chardev:yaniserial \
	-no-reboot \
	-netdev user,id=net0,hostfwd=tcp:0.0.0.0:${HOST_PORT}-:31337,hostfwd=udp:0.0.0.0:${HOST_PORT}-:31337 \
	-device virtio-net-pci,netdev=net0 \
	-drive file="$FLAG_IMAGE",format=raw,if=none,id=flag,readonly=on,cache=none \
	-device virtio-blk-pci,drive=flag \
	-append "console=ttyS0 rdinit=/init loglevel=4 panic=1"
