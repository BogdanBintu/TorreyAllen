"""Encoder-clocked, motorized Z scans for ASI Tiger card 2.

The HAL module owns threading and the controller mutex. All methods here
must be called with that mutex held. No serial connection is opened here.
"""

from dataclasses import dataclass
import math
import re
import time


class EncoderScanError(RuntimeError):
    pass


@dataclass(frozen=True)
class EncoderScanSettings:
    enabled: bool = False
    step_size_um: float = 0.5
    travel_distance_um: float = 20.0
    fps: float = 40.0
    start_offset_um: float = 3.0

    def __post_init__(self):
        for name in ("step_size_um", "travel_distance_um", "fps"):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise EncoderScanError(name + " must be finite and greater than zero")
        if not math.isfinite(self.start_offset_um):
            raise EncoderScanError("start_offset_um must be finite")

    @property
    def speed_mm_per_s(self):
        return self.step_size_um * self.fps / 1000.0

    @property
    def encoder_divider(self):
        # Match np.round(), including ties to even. Encoder resolution is 10 nm.
        return max(1, int(round(self.step_size_um / 0.01)))


class TigerEncoderScan:
    def __init__(self, controller):
        self.controller = controller
        self.settings = EncoderScanSettings()
        self.units_per_mm = None
        self.original_speed = None
        self.scan_started = False
        self.prepared_settings = None

    def command(self, command):
        response = self.controller.commWithResp(command)
        if not response or ":N" in response:
            raise EncoderScanError("ASI command {!r} failed: {!r}".format(command, response))
        return response.strip()

    @staticmethod
    def axis_value(response):
        match = re.search(r"Z\s*=\s*([-+0-9.eE]+)", response)
        if match is None:
            raise EncoderScanError("Cannot parse ASI Z response: " + response)
        value = float(match.group(1))
        if not math.isfinite(value):
            raise EncoderScanError("Non-finite ASI Z response: " + response)
        return value

    def initialize(self):
        if self.units_per_mm is not None:
            return
        self.command("2SCAN Y=0 Z=9 F=0")
        self.command("2SCANV X=0 Y=0 Z=1 F=10 T=0")
        units = self.axis_value(self.command("UM Z?"))
        if units == 0:
            raise EncoderScanError("UM Z? returned zero units per millimeter")
        self.units_per_mm = units

    def apply_settings(self, settings):
        if self.scan_started:
            raise EncoderScanError("Cannot change encoder settings during a scan")
        if settings != self.settings:
            self.prepared_settings = None
        if settings.enabled:
            if self.units_per_mm is None:
                raise EncoderScanError("Encoder scan hardware has not been initialized")
            if self.original_speed is None:
                self.original_speed = self.axis_value(self.command("S Z?"))
            self.command("S Z={:.6f}".format(settings.speed_mm_per_s))
        elif self.settings.enabled:
            self.restore_speed()
        self.settings = settings

    def prepare_film(self):
        # Match normal HAL XY behavior while leaving the scanned Z motor on.
        self.command("J X- Y-")
        self.command("MC X- Y- Z+")

    def state(self):
        response = self.command("2SCAN X?")
        # Prefer X=I over the X in ':A X=I'; also accept the bare ':A I' form.
        match = re.search(r"\bX\s*=\s*([A-Za-z])\b", response)
        if match is None:
            match = re.search(r":A\s+([A-Za-z])\s*$", response)
        if match is None:
            raise EncoderScanError("Cannot parse ASI scan state: " + response)
        return match.group(1)

    def start_scan(self, reuse_setup=False):
        """Start a scan, optionally reusing the last absolute SCANR range.

        Reuse skips W Z and SCANR only. It never skips the enabled/idle
        checks. A first scan or changed settings always require preparation.
        The caller must request fresh preparation when the desired Z range
        changes, even if the XY fields are close together.
        """
        if not self.settings.enabled:
            raise EncoderScanError("Encoder scan is disabled")
        if self.scan_started:# or self.state() != "I":
            raise EncoderScanError("ASI scan is already active; refusing to toggle SCAN")
        if not reuse_setup or self.prepared_settings != self.settings:
            # Invalidate first so a failed preparation cannot reuse stale Z.
            self.prepared_settings = None
            response = self.command("W Z")
            if "=" in response:
                z_units = self.axis_value(response)
            else:
                try:
                    z_units = float(response.split()[1])
                except (ValueError, IndexError):
                    raise EncoderScanError("Cannot parse ASI Z position: " + response)
            if not math.isfinite(z_units):
                raise EncoderScanError("Non-finite ASI Z position")
            z0_mm = z_units / self.units_per_mm
            start_mm = z0_mm + self.settings.start_offset_um / 1000.0
            end_mm = start_mm + self.settings.travel_distance_um / 1000.0
            command = "2SCANR X={:.6f} Y={:.6f} Z={}".format(
                start_mm, end_mm, self.settings.encoder_divider)
            print("Encoder Z scan:", command)
            self.command(command)
            self.prepared_settings = self.settings
        else:
            print("Encoder Z scan: reusing previous SCANR range")
        # Mark before sending: even a missing acknowledgment can mean motion began.
        self.scan_started = True
        self.command("2SCAN")

    def stop_scan(self, timeout=5.0):
        if not self.scan_started:
            return
        if self.state() != "I":
            # Explicit stop state P=80. Bare SCAN is a toggle and could restart.
            self.command("2SCAN X=80")
            deadline = time.monotonic() + timeout
            while self.state() != "I":
                if time.monotonic() >= deadline:
                    raise EncoderScanError("ASI scan did not reach idle after stop")
                time.sleep(0.02)
        self.scan_started = False

    def restore_speed(self):
        if self.original_speed is not None:
            self.command("S Z={:.6f}".format(self.original_speed))
            self.original_speed = None
