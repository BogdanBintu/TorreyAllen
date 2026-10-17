#!/usr/bin/env python
"""
RS-232 interface to an ASI tiger controller.

Hazen 05/18
"""
import traceback
import math
import re
import time

import storm_control.sc_hardware.serial.RS232 as RS232
import storm_control.sc_library.hdebug as hdebug
from storm_control.sc_library import acquisitionTiming


class TigerSerialError(RuntimeError):
    pass


class TigerMoveError(RuntimeError):
    pass


class Tiger(RS232.RS232):
    """
    Tiger controller interface class.
    """
    # Only known single-line command families use the faster reader. WHO,
    # BU/build reports, help and other multiline replies keep the old reader.
    single_line_command = re.compile(
        r"\s*(?:\d+)?(?:W|J|MC|S|M|R|RS|RDSTAT|VE|H|PM|TTL|LED|UM|SCAN|SCANR|SCANV)\b",
        re.IGNORECASE)

    def __init__(self, fast_serial=False, serial_response_timeout=1.0, **kwds):
        """
        Connect to the tiger controller at the specified port.
        """
        self.live = True
        self.fast_serial = fast_serial
        self.serial_response_timeout = float(serial_response_timeout)
        if not math.isfinite(self.serial_response_timeout) or self.serial_response_timeout <= 0:
            raise ValueError("serial_response_timeout must be finite and positive")
        self.serial_failed = False
        self.unit_to_um = 0.1
        self.um_to_unit = 1.0/self.unit_to_um

        # FIXME: Why are we storing the position?
        self.x = 0
        self.y = 0
        self.z = 0

        # Try and connect to the controller.
        try:
            super().__init__(**kwds)
            assert not (self.commWithResp("WHO") == None)
            self.joystickOnOff(True)#BB edit

        except (AttributeError, AssertionError):
            print(traceback.format_exc())
            self.live = False
            print("Tiger controller is not connected? Controller is not on?")
            print("Failed to connect to the tiger controller at port", kwds["port"])

    def commWithResp(self, command):
        with acquisitionTiming.span("Tiger serial", command=command):
            if self.serial_failed:
                raise TigerSerialError("Tiger reply stream is unsynchronized; restart HAL before sending commands")
            if self.fast_serial and self.single_line_command.match(command):
                return self.fastCommWithResp(command)
            return super().commWithResp(command)

    def fastCommWithResp(self, command):
        """Read a whole CR-terminated reply without the fixed 100 ms sleep.

        ASI normally uses CR LF and can be configured for CR only. Returning
        at CR supports both; a leftover LF is harmless whitespace at the
        beginning of the next reply. Read timeouts may split a packet, so keep
        reading until CR or the overall deadline, never return a partial reply.
        The caller still owns the normal controller mutex.
        """
        self.sendCommand(command)
        deadline = time.monotonic() + self.serial_response_timeout
        response = bytearray()
        while time.monotonic() < deadline:
            chunk = self.tty.read_until(expected=b"\r")
            if chunk:
                response.extend(chunk)
                if chunk.endswith(b"\r"):
                    return response.decode(self.encoding)
        # Do not let a delayed reply become the next command's acknowledgment.
        # No fallback or automatic re-send: SCAN is a motion toggle.
        self.serial_failed = True
        raise TigerSerialError("Timed out waiting for complete Tiger reply to {!r}: {!r}; restart HAL".format(
            command, bytes(response)))

    def goAbsolute(self, x, y):
        return self.commWithResp("M X={0:.1f} Y={1:.1f}".format(x * self.um_to_unit, y * self.um_to_unit))

    def moveXYAndWait(self, x, y, poll_interval=0.020, timeout=10.0,
                      settle_time=0.0, tolerance=0.5):
        """Move XY without commanding or querying Z, in hardware microns."""
        return self._moveAxesAndWait({"X": x, "Y": y}, poll_interval, timeout,
                                    settle_time, tolerance)

    def moveXYZAndWait(self, x, y, z, poll_interval=0.020, timeout=10.0,
                       settle_time=0.0, tolerance=0.5):
        """Move XY and motorized Z, then verify all three hardware positions."""
        return self._moveAxesAndWait({"X": x, "Y": y, "Z": z}, poll_interval,
                                    timeout, settle_time, tolerance)

    @staticmethod
    def checkMoveAck(reply, description):
        # Tiger may acknowledge a successful write with just a terminator.
        if reply is None or reply.strip() not in ("", ":A"):
            raise TigerMoveError("Tiger rejected {}: {!r}".format(description, reply))

    def _moveAxesAndWait(self, targets, poll_interval, timeout, settle_time, tolerance):
        """Caller holds the controller mutex for this entire worker task.

        RS axis? requires Tiger 2.8+. Do not query position during motion, or
        use global '/' status, which also waits for unrelated axes.
        """
        axes = tuple(targets)
        label = "".join(axes)
        values = tuple(targets.values()) + (poll_interval, timeout, settle_time, tolerance)
        if not all(math.isfinite(value) for value in values):
            raise ValueError(label + " move settings must be finite")
        if poll_interval <= 0 or timeout <= 0 or settle_time < 0 or tolerance < 0:
            raise ValueError("Invalid " + label + " move polling, timeout, settling or tolerance")

        with acquisitionTiming.span(label + " move and wait", **{axis.lower(): value for axis, value in targets.items()}):
            deadline = time.monotonic() + timeout
            self.checkMoveAck(self.goAbsolute(targets["X"], targets["Y"]), "XY move")
            if "Z" in targets:
                # Separate acknowledged commands work across Tiger cards. Z
                # starts before status polling, so it overlaps the XY move.
                self.checkMoveAck(self.zMoveTo(targets["Z"]), "motorized Z move")
            idle_since = None
            while time.monotonic() < deadline:
                # Materialize every query: all() on a generator could skip Y/Z.
                busy = [self.axisIsBusy(axis) for axis in axes]
                now = time.monotonic()
                if now >= deadline:
                    break
                if not any(busy):
                    if idle_since is None:
                        idle_since = now
                    if now - idle_since >= settle_time:
                        pos = self.axesPositionStrict(axes)
                        if time.monotonic() >= deadline:
                            break
                        for axis, target in targets.items():
                            if abs(pos[axis.lower()] - target) > tolerance:
                                raise TigerMoveError(
                                    "{} stopped outside {:.3f} um tolerance on {}: target {:.3f}, "
                                    "actual {:.3f}".format(label, tolerance, axis, target, pos[axis.lower()]))
                        acquisitionTiming.event(label + " stage ready", **pos)
                        return pos
                else:
                    idle_since = None
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    time.sleep(min(poll_interval, remaining))
            raise TigerMoveError(label + " move did not complete within {:.3f} seconds".format(timeout))

    def axisIsBusy(self, axis):
        if axis not in ("X", "Y", "Z"):
            raise ValueError("Stage busy query requires X, Y or Z")
        reply = self.commWithResp("RS {}?".format(axis))
        match = re.fullmatch(r"(?:\s*:A)?\s*(?:" + axis + r"\s*=\s*)?([BN])\s*", reply or "")
        if match is None:
            raise TigerMoveError("Invalid {} busy reply: {!r}".format(axis, reply))
        return match.group(1) == "B"

    def xyPositionStrict(self):
        return self.axesPositionStrict(("X", "Y"))

    def axesPositionStrict(self, axes):
        """Unlike position(), never substitute zero for a failed query.

        Query axes separately so hardware axis order cannot swap X and Y.
        """
        pos = {}
        for axis in axes:
            if axis not in ("X", "Y", "Z"):
                raise ValueError("Stage position query requires X, Y or Z")
            reply = self.commWithResp("W " + axis)
            text = (reply or "").strip()
            if text.startswith(":A"):
                text = text[2:].strip()
            if text.startswith(axis + "="):
                text = text[2:].strip()
            try:
                value = float(text) * self.unit_to_um
                if not math.isfinite(value):
                    raise ValueError("Nonfinite position")
            except ValueError:
                raise TigerMoveError("Invalid {} position reply: {!r}".format(axis, reply))
            pos[axis.lower()] = value
        for axis, value in pos.items():
            setattr(self, axis, value)
        return pos

    def goRelative(self, x, y):
        self.commWithResp("R X={0:.1f} Y={1:.1f}".format(x * self.um_to_unit, y * self.um_to_unit))

    def jog(self, x_speed, y_speed):
        # Stage velocities are in units of mm/sec.
        vx = x_speed * self.um_to_unit * 1.0e-3
        vy = y_speed * self.um_to_unit * 1.0e-3
        self.commWithResp("VE X={0:.3f} Y={1:.3f}".format(vx, vy))

    def joystickOnOff(self, on):
        # This also turns off the stage motors to disable position
        # feedback control during movies.
        if on:
            try:
                self.commWithResp("J X+ Y+")
                self.commWithResp("MC X+ Y+ Z+")
            except:
                print("Failed communication...")
        else:
            try:
                self.commWithResp("J X- Y-")
                self.commWithResp("MC X- Y- Z-")
            except:
                print("Failed communication...")

    def position(self):
        try:
            [self.x, self.y] = map(lambda x: float(x)*self.unit_to_um, 
                                   self.commWithResp("W X Y").split(" ")[1:3])
        except:
            [self.x, self.y] = [0,0]#BBedit
        return {"x" : self.x,
                "y" : self.y}

    def setLED(self, address, channel, power):
        self.commWithResp(address + "LED " + channel + "={0:0d}".format(int(power)))

    def setTTLMode(self, address, mode):
        self.commWithResp(address + "TTL X={0:0d}".format(int(mode)))

    def setVelocity(self, x_vel, y_vel):
        """
        Set the maximum X/Y speed in mm/sec.
        """
        self.commWithResp("S X={0:.2f} Y={1:.2f}".format(x_vel, y_vel))
        self.vx,self.vy = x_vel, y_vel
        print("Velocity was set to:",x_vel,y_vel)
    def getVelocity(self):
        maxvel = 1.92
        if not hasattr(self,'vx'):
            self.vx,self.vy=maxvel,maxvel
        self.vx = min(maxvel,self.vx)
        self.vy = min(maxvel,self.vy)
        
        return self.vx,self.vy
        
    def zero(self):
        self.commWithResp("H X Y")

    def zConfigurePiezo(self, axis, mode):
        self.commWithResp("PM " + str(axis) + "=" + str(mode))
        
    def zMoveTo(self, z):
        """
        Move the z stage to the specified position (in microns).
        """
        return self.commWithResp("M Z={0:.2f}".format(z * self.um_to_unit))

    def zPosition(self):
        """
        Query for current z position in microns.
        """
        new_z = self.z
        try:
            temp = self.commWithResp("W Z")
            new_z = float(temp.split(" ")[1])*self.unit_to_um
        except ValueError:
            print("Tiger.zPosition(): could not parse -", temp, "-")            
        self.z = new_z
        return {"z" : self.z}

    def zSetVelocity(self, z_vel):
        """
        Set the maximum Z speed in mm/sec.
        """
        return self.commWithResp("S Z={0:.6f}".format(z_vel))
        
    def zZero(self):
        self.commWithResp("H Z")
    def goZRelative(self, z):
        self.commWithResp("R Z={0:.1f}".format(z * self.um_to_unit))

if (__name__ == "__main__"):
    import time
    
    stage = Tiger(port = "COM4", baudrate = 115200)
    print(stage.position())

    stage.goRelative(100,0)
    time.sleep(0.5)
    print(stage.position())

    stage.goAbsolute(0,0)
    time.sleep(0.5)
    print(stage.position())
    
    stage.shutDown()


