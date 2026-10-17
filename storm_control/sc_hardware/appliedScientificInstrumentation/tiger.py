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


class Tiger(RS232.RS232):
    """
    Tiger controller interface class.
    """
    # Only known single-line command families use the faster reader. WHO,
    # BU/build reports, help and other multiline replies keep the old reader.
    single_line_command = re.compile(
        r"\s*(?:\d+)?(?:W|J|MC|S|M|R|VE|H|PM|TTL|LED|UM|SCAN|SCANR|SCANV)\b",
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
        self.commWithResp("M X={0:.1f} Y={1:.1f}".format(x * self.um_to_unit, y * self.um_to_unit))

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
        self.commWithResp("M Z={0:.2f}".format(z * self.um_to_unit))

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
        self.commWithResp("S Z={0:.2f}".format(z_vel))
        
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


