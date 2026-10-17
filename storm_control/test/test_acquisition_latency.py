"""Request counts and exception behavior for acquisition latency changes."""

from contextlib import redirect_stdout
import io
import unittest
from unittest import mock

from storm_control.sc_hardware.lumencor import celesta
from storm_control.sc_library import acquisitionTiming


class LatencyTests(unittest.TestCase):
    def laser(self):
        laser = celesta.LumencorLaser.__new__(celesta.LumencorLaser)
        laser.ip = "test"
        laser.laser_id = "0"
        laser.pmax = 100
        laser.message = {"message": "A OK"}
        laser.on = False
        return laser

    def test_power_update_wakes_once_and_retains_intensity_and_enable(self):
        laser = self.laser()
        with mock.patch.object(celesta, "lumencor_httpcommand", return_value={"message": "A OK"}) as send:
            with redirect_stdout(io.StringIO()):
                self.assertTrue(laser.setPower(25))
        self.assertEqual([call.kwargs["command"] for call in send.call_args_list],
                         ["WAKEUP", "SET CHINT 0 25", "SET CH 0 1"])
        self.assertTrue(laser.on)

    def test_zero_power_still_writes_zero_then_enables_as_before(self):
        laser = self.laser()
        with mock.patch.object(celesta, "lumencor_httpcommand", return_value={"message": "A OK"}) as send:
            with redirect_stdout(io.StringIO()):
                laser.setPower(0)
        self.assertEqual([call.kwargs["command"] for call in send.call_args_list],
                         ["WAKEUP", "SET CHINT 0 0", "SET CH 0 1"])

    def test_direct_enable_keeps_wakeup_and_direct_disable_stays_off(self):
        laser = self.laser()
        with mock.patch.object(celesta, "lumencor_httpcommand", return_value={"message": "A OK"}) as send:
            with redirect_stdout(io.StringIO()):
                laser.setLaserOnOff(True)
                laser.setLaserOnOff(False)
        self.assertEqual([call.kwargs["command"] for call in send.call_args_list],
                         ["WAKEUP", "SET CH 0 1", "SET CH 0 0"])
        self.assertFalse(laser.on)

    def test_timing_does_not_suppress_hardware_errors(self):
        output = io.StringIO()
        with mock.patch.dict("os.environ", {"HAL_ACQUISITION_TIMING": "1"}):
            with redirect_stdout(output):
                with self.assertRaisesRegex(RuntimeError, "hardware failed"):
                    with acquisitionTiming.span("test operation", camera="camera1"):
                        raise RuntimeError("hardware failed")
        self.assertIn("test operation begin", output.getvalue())
        self.assertIn("test operation end", output.getvalue())
        self.assertIn("elapsed_ms=", output.getvalue())

    def test_timing_is_quiet_when_disabled(self):
        output = io.StringIO()
        with mock.patch.dict("os.environ", {"HAL_ACQUISITION_TIMING": "0"}):
            with redirect_stdout(output):
                with acquisitionTiming.span("test"):
                    acquisitionTiming.event("inside")
        self.assertEqual(output.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
