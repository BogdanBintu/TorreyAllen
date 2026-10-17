"""Encoder command sequencing, with no serial connection or microscope."""

from dataclasses import replace
import unittest
from unittest import mock

from storm_control.sc_hardware.appliedScientificInstrumentation.tigerEncoderScan import (
    EncoderScanError, EncoderScanSettings, TigerEncoderScan)


class FakeTiger:
    def __init__(self):
        self.commands = []
        self.scan_state = "I"
        self.z_units = 10000.0
        self.fail_prefix = None

    def getStatus(self):
        return True

    def commWithResp(self, command):
        self.commands.append(command)
        if self.fail_prefix and command.startswith(self.fail_prefix):
            return ":N 1"
        if command == "UM Z?":
            return ":A Z=10000"
        if command == "S Z?":
            return ":A Z=1.92"
        if command == "2SCAN X?":
            return ":A X=" + self.scan_state
        if command == "W Z":
            return ":A " + str(self.z_units)
        if command == "2SCAN":
            self.scan_state = "S"
        elif command == "2SCAN X=80":
            self.scan_state = "I"
        return ":A"


class EncoderScanTests(unittest.TestCase):
    def setUp(self):
        self.controller = FakeTiger()
        self.scan = TigerEncoderScan(self.controller)
        self.scan.initialize()
        self.settings = EncoderScanSettings(
            enabled=True, step_size_um=0.3, travel_distance_um=12.0)
        self.scan.apply_settings(self.settings)
        self.controller.commands.clear()

    def finish_scan(self):
        self.controller.scan_state = "I"
        self.scan.stop_scan()
        self.controller.commands.clear()

    def test_default_recomputes_absolute_range_for_each_movie(self):
        self.scan.start_scan()
        self.assertEqual(self.controller.commands, [
            "2SCAN X?", "W Z", "2SCANR X=1.003000 Y=1.015000 Z=30", "2SCAN"])
        self.finish_scan()
        self.controller.z_units = 20000.0
        self.scan.start_scan()
        self.assertEqual(self.controller.commands, [
            "2SCAN X?", "W Z", "2SCANR X=2.003000 Y=2.015000 Z=30", "2SCAN"])

    def test_reuse_keeps_original_absolute_range(self):
        self.scan.start_scan()
        self.finish_scan()
        self.controller.z_units = 20000.0
        self.scan.start_scan(reuse_setup=True)
        self.assertEqual(self.controller.commands, ["2SCAN X?", "2SCAN"])

    def test_cold_reuse_prepares_automatically(self):
        self.scan.start_scan(reuse_setup=True)
        self.assertIn("W Z", self.controller.commands)
        self.assertIn("2SCANR X=1.003000 Y=1.015000 Z=30", self.controller.commands)

    def test_every_setting_change_invalidates_preparation(self):
        for name, value in (("step_size_um", 0.5), ("travel_distance_um", 15.0),
                            ("fps", 20.0), ("start_offset_um", 5.0)):
            with self.subTest(setting=name):
                self.scan.start_scan()
                self.finish_scan()
                changed = replace(self.scan.settings, **{name: value})
                self.scan.apply_settings(changed)
                self.controller.commands.clear()
                self.scan.start_scan(reuse_setup=True)
                self.assertIn("W Z", self.controller.commands)
                self.assertTrue(any(c.startswith("2SCANR ") for c in self.controller.commands))
                self.finish_scan()

    def test_reapplying_identical_settings_preserves_preparation(self):
        self.scan.start_scan()
        self.finish_scan()
        self.scan.apply_settings(replace(self.settings))
        self.controller.commands.clear()
        self.scan.start_scan(reuse_setup=True)
        self.assertEqual(self.controller.commands, ["2SCAN X?", "2SCAN"])

    def test_disable_then_reenable_requires_fresh_preparation(self):
        self.scan.start_scan()
        self.finish_scan()
        self.scan.apply_settings(replace(self.settings, enabled=False))
        self.scan.apply_settings(self.settings)
        self.controller.commands.clear()
        self.scan.start_scan(reuse_setup=True)
        self.assertIn("W Z", self.controller.commands)

    def test_failed_preparation_cannot_reuse_old_range(self):
        for prefix in ("W Z", "2SCANR "):
            with self.subTest(command=prefix):
                self.scan.start_scan()
                self.finish_scan()
                self.controller.fail_prefix = prefix
                with self.assertRaises(EncoderScanError):
                    self.scan.start_scan()
                self.assertFalse(self.scan.scan_started)
                self.controller.fail_prefix = None
                self.controller.commands.clear()
                self.scan.start_scan(reuse_setup=True)
                self.assertIn("W Z", self.controller.commands)
                self.assertTrue(any(c.startswith("2SCANR ") for c in self.controller.commands))
                self.finish_scan()

    def test_reuse_refuses_disabled_scan(self):
        self.scan.apply_settings(replace(self.settings, enabled=False))
        self.controller.commands.clear()
        with self.assertRaises(EncoderScanError):
            self.scan.start_scan(reuse_setup=True)
        self.assertEqual(self.controller.commands, [])

    def test_reuse_refuses_nonidle_hardware(self):
        self.scan.start_scan()
        self.finish_scan()
        self.controller.scan_state = "S"
        with self.assertRaises(EncoderScanError):
            self.scan.start_scan(reuse_setup=True)
        self.assertEqual(self.controller.commands, ["2SCAN X?"])

    def test_reuse_cannot_toggle_a_started_scan(self):
        self.scan.start_scan()
        self.controller.commands.clear()
        with self.assertRaises(EncoderScanError):
            self.scan.start_scan(reuse_setup=True)
        self.assertEqual(self.controller.commands, [])

    def test_missing_start_acknowledgment_still_requires_stop(self):
        # Prepare first, then lose only the bare start command's acknowledgment.
        self.scan.start_scan()
        self.finish_scan()
        original = self.controller.commWithResp

        def lost_ack(command):
            response = original(command)
            return None if command == "2SCAN" else response

        self.controller.commWithResp = lost_ack
        with self.assertRaises(EncoderScanError):
            self.scan.start_scan(reuse_setup=True)
        self.assertTrue(self.scan.scan_started)
        self.scan.stop_scan()
        self.assertIn("2SCAN X=80", self.controller.commands)
        self.assertFalse(self.scan.scan_started)

    def test_both_idle_response_formats(self):
        for response in (":A X=I", ":A I"):
            with self.subTest(response=response):
                with mock.patch.object(self.controller, "commWithResp", return_value=response):
                    self.assertEqual(self.scan.state(), "I")

    def test_stop_uses_explicit_stop_before_reuse(self):
        self.scan.start_scan()
        self.controller.commands.clear()
        self.scan.stop_scan()
        self.assertEqual(self.controller.commands, ["2SCAN X?", "2SCAN X=80", "2SCAN X?"])
        self.controller.commands.clear()
        self.scan.start_scan(reuse_setup=True)
        self.assertEqual(self.controller.commands, ["2SCAN X?", "2SCAN"])


if __name__ == "__main__":
    unittest.main()
