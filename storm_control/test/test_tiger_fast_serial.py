"""Packet fragmentation, reply framing, and no-retry behavior for Tiger."""

from collections import deque
import unittest
from unittest import mock

from storm_control.sc_hardware.appliedScientificInstrumentation import tiger


class ReplyPort:
    def __init__(self, chunks):
        self.chunks = deque(chunks)
        self.writes = []

    def flush(self):
        pass

    def write(self, data):
        self.writes.append(data)

    def read_until(self, expected):
        assert expected == b"\r"
        return self.chunks.popleft() if self.chunks else b""


class FastSerialTests(unittest.TestCase):
    def controller(self, chunks):
        obj = tiger.Tiger.__new__(tiger.Tiger)
        obj.tty = ReplyPort(chunks)
        obj.encoding = "utf-8"
        obj.end_of_line = "\r"
        obj.fast_serial = True
        obj.serial_response_timeout = 1.0
        obj.serial_failed = False
        return obj

    def test_fragmented_reply_waits_for_terminator(self):
        obj = self.controller([b":", b"", b"A X=", b"I", b"\r"])
        self.assertEqual(obj.commWithResp("2SCAN X?"), ":A X=I\r")
        self.assertEqual(obj.tty.writes, [b"2SCAN X?\r"])

    def test_crlf_tail_does_not_steal_next_reply(self):
        obj = self.controller([b":A\r", b"\n", b":A Z=10", b"000\r"])
        self.assertEqual(obj.commWithResp("J X- Y-"), ":A\r")
        self.assertEqual(obj.commWithResp("W Z").strip(), ":A Z=10000")
        self.assertEqual(obj.tty.writes, [b"J X- Y-\r", b"W Z\r"])

    def test_tiger_blank_acknowledgment_and_cr_only(self):
        obj = self.controller([b"\r", b"\n", b"Z=100\r"])
        self.assertEqual(obj.commWithResp("MC X- Y- Z+"), "\r")
        self.assertEqual(obj.commWithResp("W Z").strip(), "Z=100")

    def test_error_reply_is_returned_for_caller_to_handle(self):
        obj = self.controller([b":N-1\r"])
        self.assertEqual(obj.commWithResp("2SCAN"), ":N-1\r")

    def test_fast_scan_does_not_use_fixed_sleep(self):
        obj = self.controller([b":A\r"])
        with mock.patch.object(tiger.time, "sleep", side_effect=AssertionError("fixed sleep")):
            self.assertEqual(obj.commWithResp("2SCAN"), ":A\r")

    def test_multiline_and_unknown_commands_keep_legacy_reader(self):
        for command in ("WHO", "2BU X", "HELP", "2Z2B"):
            with self.subTest(command=command):
                obj = self.controller([])
                with mock.patch.object(tiger.RS232.RS232, "commWithResp", return_value="multiline\r\nreport") as legacy:
                    self.assertEqual(obj.commWithResp(command), "multiline\r\nreport")
                    legacy.assert_called_once_with(command)

    def test_disabled_fast_mode_keeps_legacy_reader(self):
        obj = self.controller([])
        obj.fast_serial = False
        with mock.patch.object(tiger.RS232.RS232, "commWithResp", return_value=":A\r\n") as legacy:
            self.assertEqual(obj.commWithResp("2SCAN"), ":A\r\n")
            legacy.assert_called_once_with("2SCAN")

    def test_timeout_never_retries_motion_or_uses_partial_reply(self):
        obj = self.controller([b":A"])
        with mock.patch.object(tiger.time, "monotonic", side_effect=[0.0, 0.0, 2.0]):
            with self.assertRaisesRegex(tiger.TigerSerialError, "complete Tiger reply"):
                obj.commWithResp("2SCAN")
        self.assertEqual(obj.tty.writes, [b"2SCAN\r"])
        self.assertTrue(obj.serial_failed)
        with self.assertRaisesRegex(tiger.TigerSerialError, "unsynchronized"):
            obj.commWithResp("2SCAN")
        self.assertEqual(obj.tty.writes, [b"2SCAN\r"])


if __name__ == "__main__":
    unittest.main()
