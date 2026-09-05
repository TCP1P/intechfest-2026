import hashlib
import unittest
from unittest.mock import patch

from run import (
    AdContext,
    BANNER,
    CRITICAL_FILE_WHITELIST,
    check_kernel_base_and_code,
    IMMUTABLE_CODE_CHECKS,
    INIT_SHA256,
    KERNEL_LINK_COMMIT,
    KERNEL_LINK_MEMORY_IDENTITY,
    KERNEL_LINK_INIT_TASK,
    KERNEL_LINK_PREPARE,
    KERNEL_LINK_TEXT,
    MEMORY_IDENTITY,
    SMOKING_MESSAGE_BYTES,
    SMOKING_MESSAGE_SHA256,
    digest_flag_image,
    _line,
)


class CheckerHelpersTest(unittest.TestCase):
    def test_flag_image_digest_matches_fixed_block_contract(self):
        flag = "rsctf{local_test}"
        expected = hashlib.sha256(
            (flag + "\n").encode().ljust(4096, b"\0")
        ).hexdigest()
        self.assertEqual(digest_flag_image(flag), expected)

    def test_line_requires_one_ascii_newline(self):
        self.assertEqual(_line(b"PONG\n"), "PONG")
        with self.assertRaises(Exception):
            _line(b"PONG")

    def test_banner_is_ascii_and_newline_terminated(self):
        self.assertEqual(
            BANNER,
            (
                b"         (  )   (   )\n"
                b"       (      )       )\n"
                b"     (  /\\__/\\   (  )\n"
                b"      ( o . o )    )\n"
                b"       \\  ^  /---~~\n"
                b"      /      \\\n"
                b"\n"
                b"                 YANINEKO\n"
                b"       +-------------------------------+\n"
                b"       | EAT | PUKE | SMOKING | Mrrph  |\n"
                b"       +-------------------------------+\n"
                b"  YANINEKO EAT <length>\n"
                b"  YANINEKO PUKE <length> <hex>\n"
                b"  YANINEKO SMOKING\n"
                b"  YANINEKO SMOKING <address>\n"
                b"  YANINEKO Mrrph\n"
            ),
        )

    def test_critical_file_whitelist_has_fixed_kernel_entries(self):
        self.assertEqual(set(CRITICAL_FILE_WHITELIST), {
            "/proc/sys/vm/mmap_min_addr",
            "/proc/sys/kernel/randomize_va_space",
            "/etc/yani/contract",
            "/proc/yani",
            "/init",
        })
        self.assertEqual(len(MEMORY_IDENTITY), 38)
        self.assertEqual(INIT_SHA256, "8708a6dd6d7070d8dbd197b4d5848c1870fbaa938d36227d8cee1a215e0787e9")

    def test_kernel_code_whitelist_is_relocatable_and_bounded(self):
        self.assertEqual(KERNEL_LINK_TEXT, 0xFFFFFFFF81000000)
        self.assertEqual(KERNEL_LINK_MEMORY_IDENTITY, 0xFFFFFFFF825756A0)
        self.assertEqual(KERNEL_LINK_PREPARE, 0xFFFFFFFF812CDEB0)
        self.assertEqual(KERNEL_LINK_COMMIT, 0xFFFFFFFF812CDC00)
        self.assertEqual(KERNEL_LINK_INIT_TASK, 0xFFFFFFFF82C0CA00)
        self.assertEqual(
            IMMUTABLE_CODE_CHECKS,
            (
                (0xFFFFFFFF81000000, 128, "d97893ed7a4e12bf79dd0ac7ef9bdc76b3526e2c2bcbbd3a2d6d890d3bdd480c"),
                (0xFFFFFFFF82063DE0, 128, "95e717fde1b6579767cbd15eaff01150fd258a3914398a8818632c6788bea0e0"),
                (0xFFFFFFFF82063FB0, 128, "7e5b6b279d05c89fee654a17ae419275178e352854472365be030796fdc9e969"),
            ),
        )
        for _, length, digest in IMMUTABLE_CODE_CHECKS:
            self.assertGreater(length, 0)
            self.assertLessEqual(length, 4096)
            self.assertEqual(len(digest), 64)

    def test_kernel_base_check_relocates_addresses_from_info_and_eat(self):
        slide = 0x25600000
        info = (
            "INFO kernel=7.2.3 port=31337 netfilter=ipv4 smep=0 smap=0 "
            f"mem_start=0x{KERNEL_LINK_MEMORY_IDENTITY + slide:x} mem_length=38"
        )
        leaked = bytearray(96)
        leaked[64:72] = (KERNEL_LINK_PREPARE + slide).to_bytes(8, "little")
        leaked[72:80] = (KERNEL_LINK_COMMIT + slide).to_bytes(8, "little")
        leaked[80:88] = (KERNEL_LINK_INIT_TASK + slide).to_bytes(8, "little")
        responses = iter([
            info,
            f"LEAK {leaked.hex()}",
            *(f"MEMCHECK_OK {digest}" for _, _, digest in IMMUTABLE_CODE_CHECKS),
        ])
        context = AdContext("127.0.0.1", 31337, 1, 1, 1, "rsctf{test}")
        with patch("run.request_tcp", side_effect=lambda _, __: next(responses)):
            check_kernel_base_and_code(context)

    def test_smoking_message_digest_is_pinned(self):
        self.assertEqual(SMOKING_MESSAGE_BYTES, 361)
        self.assertEqual(
            SMOKING_MESSAGE_SHA256,
            "99b4249080b3d7d74717786ae4c044bf97348142eabf482f09a671770f77dc94",
        )

    def test_safe_byte_probe_covers_one_byte_values(self):
        self.assertEqual(tuple(f"{value:02x}" for value in range(256))[0], "00")
        self.assertEqual(tuple(f"{value:02x}" for value in range(256))[-1], "ff")

if __name__ == "__main__":
    unittest.main()
