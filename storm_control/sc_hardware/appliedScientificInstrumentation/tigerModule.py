#!/usr/bin/env python
"""
HAL module for controlling a Tiger controller.

Hazen 05/18
"""
import math
from PyQt5 import QtCore

import storm_control.sc_library.halExceptions as halExceptions
import storm_control.sc_library.parameters as params

import storm_control.hal4000.halLib.halMessage as halMessage

import storm_control.sc_hardware.baseClasses.amplitudeModule as amplitudeModule
import storm_control.sc_hardware.baseClasses.hardwareModule as hardwareModule
import storm_control.sc_hardware.baseClasses.stageModule as stageModule
import storm_control.sc_hardware.baseClasses.stageZModule as stageZModule
import storm_control.sc_hardware.baseClasses.voltageZModule as voltageZModule

import storm_control.sc_hardware.appliedScientificInstrumentation.tiger as tiger
from storm_control.sc_hardware.appliedScientificInstrumentation.tigerEncoderScan import (
    EncoderScanError, EncoderScanSettings, TigerEncoderScan)


class TigerLEDFunctionality(amplitudeModule.AmplitudeFunctionalityBuffered):
    def __init__(self, address = None, channel = None, ttl_mode = None, led = None, **kwds):
        """
        ttl_mode is the TTL control mode to use when filming. Usually this is mode 22, which
        requires firmware 3.30 and above. Note also that due to how this mode is implemented
        the power will only get updated when the shutter line goes high, so for always on you
        should include short pulses so that the power updates.
        """
        super().__init__(**kwds)
        self.address = address
        self.channel = channel
        self.cur_power = 0
        self.led = led
        self.on = False
        self.ttl_mode = ttl_mode

        # Make sure we are mode 0.
        self.mustRun(task = self.led.setTTLMode,
                     args = [self.address, 0])

    def onOff(self, power, state):
        self.mustRun(task = self.led.setLED,
                     args = [self.address, self.channel, power])
        self.on = state
    
    def output(self, power):
        if self.on:
            self.maybeRun(task = self.led.setLED,
                          args = [self.address, self.channel, power])

    def setFilmPower(self):
        #
        # Bypass the queue because we need to be sure that this
        # gets done before the film starts.
        #
        self.device_mutex.lock()
        self.led.setLED(self.address, self.channel, self.cur_power)
        self.device_mutex.unlock()

    def setFilmTTLMode(self, filming):
        #
        # Bypass the queue because we need to be sure that this
        # gets done before the film starts.
        #
        if (self.ttl_mode > 0):
            self.device_mutex.lock()
            if filming:
                self.led.setTTLMode(self.address, self.ttl_mode)
            else:
                self.led.setTTLMode(self.address, 0)
            self.device_mutex.unlock()
                    
    def startFilm(self, power):
        self.cur_power = power

import numpy as np
class TigerStageFunctionality(stageModule.StageFunctionalityNF):
    """
    According to the documentation, this stage has a maximum velocity of 7.5mm / second.
    """
    def __init__(self, velocity = None, **kwds):
        super().__init__(**kwds)
        self.max_velocity = 1.0e+3 * velocity # Maximum velocity in um/s

        self.mustRun(task = self.stage.setVelocity,
                     args = [velocity, velocity])
        
#        self.stage.setVelocity(velocity, velocity)

    def calculateMoveTime(self, dx, dy):
        vx,vy = self.stage.getVelocity()
        print("Velocity estimation: ",vx*1000,vy*1000)
        print("Distance estimation: ",dx,dy)
        #time_estimate = math.sqrt(dx*dx + dy*dy)/self.max_velocity/0.75 + 1.0
        vx,vy=vx*1000.,vy*1000.
        
        time_estimate = max(np.abs(dx)/vx,np.abs(dy)/vy)+2.5
        #print("> stage move time estimate is {0:.3f} seconds".format(time_estimate))
        return time_estimate


class TigerVoltageZFunctionality(voltageZModule.VoltageZFunctionality):
    """
    External voltage control of piezo Z stage.
    """
    def __init__(self, **kwds):
        super().__init__(**kwds)
    
class TigerZStageFunctionality(stageZModule.ZStageFunctionalityBuffered):
    """
    The z sign convention of this stage is the opposite from the expected
    so we have to adjust.
    """
    def __init__(self, update_interval = None, velocity = None, **kwds):
        super().__init__(**kwds)
        self.encoder_scan_active = False

        self.maximum = self.getParameter("maximum")
        self.minimum = self.getParameter("minimum")
        print("Zstage_max:",self.maximum,self.minimum)
        # Set initial z velocity.
        self.mustRun(task = self.z_stage.zSetVelocity,
                     args = [velocity])
        
        # This timer to restarts the update timer after a move. It appears
        # that if you query the position during a move the stage will stop
        # moving.
        self.restart_timer = QtCore.QTimer()
        self.restart_timer.setInterval(2000)
        self.restart_timer.timeout.connect(self.handleRestartTimer)
        self.restart_timer.setSingleShot(True)

        # Each time this timer fires we'll query the z stage position. We need
        # to do this as the user might use the controller to directly change
        # the stage z position.
        self.update_timer = QtCore.QTimer()
        self.update_timer.setInterval(update_interval)
        self.update_timer.timeout.connect(self.handleUpdateTimer)
        self.update_timer.start()
        
    def goAbsolute(self, z_pos):
        # We have to stop the update timer because if it goes off during the
        # move it will stop the move.
        self.update_timer.stop()
        super().goAbsolute(z_pos)
        self.restart_timer.start()

    def goRelative(self, z_delta):
        z_pos = -1.0*self.z_position + z_delta
        self.goAbsolute(z_pos)        

    def handleRestartTimer(self):
        if not self.encoder_scan_active:
            self.update_timer.start()
        
    def handleUpdateTimer(self):
        if not self.encoder_scan_active:
            self.mustRun(task = self.position,
                         ret_signal = self.zStagePosition)

    def position(self):
        # A polling job may already be queued when filming starts.
        if not self.encoder_scan_active:
            self.z_position = self.z_stage.zPosition()["z"]
        return -1.0*self.z_position

    def zero(self):
        self.mustRun(task = self.z_stage.zZero)
        self.zStagePosition.emit(0.0)
    
    def zMoveTo(self, z_pos):
        return -1.0*super().zMoveTo(-z_pos)
    
        
#
# Inherit from stageModule.StageModule instead of the base class so we don't
# have to duplicate most of the stage stuff, particularly the TCP control.
#
class TigerController(stageModule.StageModule):

    def __init__(self, module_params = None, qt_settings = None, **kwds):
        super().__init__(**kwds)
        self.controller_mutex = QtCore.QMutex()
        self.functionalities = {}
        self.z_stage_fn = None
        self.encoder_scan = None
        self.encoder_scan_for_film = False
        self.encoder_scan_triggered = False
        self.encoder_reuse_setup = False
        self.encoder_polling_timers = []
        self.encoder_cameras = {}
        self.encoder_trigger_sources = set()
        self.encoder_parameters = params.StormXMLObject()
        scan_p = self.encoder_parameters.addSubSection("encoder_scan")
        scan_p.add(params.ParameterSetBoolean(name = "enabled", value = False))
        for name, value in (("step_size_um", 0.5), ("travel_distance_um", 20.0),
                            ("fps", 40.0), ("start_offset_um", 3.0)):
            scan_p.add(params.ParameterFloat(name = name, value = value))

        # These are used for the Z piezo stage.
        self.z_piezo_configuration = None
        self.z_piezo_functionality = None

        configuration = module_params.get("configuration")
        self.encoder_scan_supported = configuration.get("encoder_scan_supported", False)
        self.controller = tiger.Tiger(baudrate = configuration.get("baudrate"),
                                      fast_serial = configuration.get("fast_serial", False),
                                      serial_response_timeout = configuration.get("serial_response_timeout", 1.0),
                                      port = configuration.get("port"))
        
        if self.controller.getStatus():
            if self.encoder_scan_supported:
                self.encoder_scan = TigerEncoderScan(self.controller)
                # Initialize once, before creating any position-polling timers.
                self.encoder_scan.initialize()

            # Note: We are not checking whether the devices that the user requested
            #       are actually available, we're just assuming that they know what
            #       they are doing.
            #
            devices = configuration.get("devices")
            for dev_name in devices.getAttrs():

                # XY stage.
                if (dev_name == "xy_stage"):
                    settings = devices.get(dev_name)

                    # We do this so that the superclass works correctly.
                    self.stage = self.controller

                    self.stage_functionality = TigerStageFunctionality(device_mutex = self.controller_mutex,
                                                                       stage = self.stage,
                                                                       update_interval = 500,
                                                                       velocity = settings.get("velocity", 7.5))
                    self.functionalities[self.module_name + "." + dev_name] = self.stage_functionality

                elif (dev_name == "z_piezo"):
                    self.z_piezo_configuration = devices.get(dev_name)

                elif (dev_name == "z_stage"):
                    settings = devices.get(dev_name)
                    z_stage_fn = TigerZStageFunctionality(device_mutex = self.controller_mutex,
                                                          parameters = settings,
                                                          update_interval = 500,
                                                          velocity = settings.get("velocity", 1.0),
                                                          z_stage = self.controller)
                    
                    self.functionalities[self.module_name + "." + dev_name] = z_stage_fn
                    self.z_stage_fn = z_stage_fn
                elif (dev_name.startswith("led")):
                    settings = devices.get(dev_name)
                    led_fn = TigerLEDFunctionality(address = settings.get("address"),
                                                   channel = settings.get("channel"),
                                                   device_mutex = self.controller_mutex,
                                                   maximum = 100,
                                                   ttl_mode = configuration.get("ttl_mode", -1),
                                                   led = self.controller)
                    self.functionalities[self.module_name + "." + dev_name] = led_fn

                else:
                    raise halExceptions.HardwareException("Unknown device " + str(dev_name))

        else:
            self.controller = None
    
    def cleanUp(self, qt_settings):
        if self.controller is not None:
            #if self.z_piezo_functionality is not None:
            #    self.z_piezo_functionality.goAbsolute(
            #        self.z_piezo_functionality.getMinimum())
            
            for fn in self.functionalities.values():
                if hasattr(fn, "wait"):
                    fn.wait()
            if self.encoder_scan is not None:
                self.runEncoderTask(self.encoder_scan.stop_scan)
                self.runEncoderTask(self.encoder_scan.restore_speed)
            self.controller.shutDown()

    def runEncoderTask(self, task):
        self.controller_mutex.lock()
        try:
            return task()
        finally:
            self.controller_mutex.unlock()

    def updateEncoderParameters(self, message, settings):
        if self.encoder_scan is not None:
            self.runEncoderTask(lambda: self.encoder_scan.apply_settings(settings))
        for name in settings.__dataclass_fields__:
            self.encoder_parameters.setv("encoder_scan." + name, getattr(settings, name))
        message.addResponse(halMessage.HalMessageResponse(
            source = self.module_name,
            data = {"new parameters" : self.encoder_parameters.copy()}))

    def pauseEncoderPolling(self):
        self.encoder_polling_timers = []
        # Keep all controller position polling quiet during the scan.
        for fn in (self.stage_functionality, self.z_stage_fn):
            if fn is not None:
                if fn is self.z_stage_fn:
                    fn.encoder_scan_active = True
                for name in ("update_timer", "restart_timer"):
                    timer = getattr(fn, name, None)
                    if timer is not None:
                        self.encoder_polling_timers.append((timer, timer.isActive()))
                        timer.stop()

    def resumeEncoderPolling(self):
        if self.z_stage_fn is not None:
            self.z_stage_fn.encoder_scan_active = False
        for timer, was_active in self.encoder_polling_timers:
            if was_active:
                timer.start()
        self.encoder_polling_timers = []

    def prepareEncoderFilm(self, run_shutters):
        if self.stage_functionality is not None:
            # StageModule.startFilm normally disables X, Y AND Z motors.
            self.runEncoderTask(self.encoder_scan.prepare_film)
        if run_shutters:
            self.startLED()

    def processEncoderMessage(self, message):
        """Return True for messages handled completely by the encoder integration."""
        if message.isType("configuration"):
            properties = message.getData()["properties"]
            if properties.get("is camera", False):
                self.encoder_cameras[message.getSourceName()] = properties["is master"]
            elif properties.get("is master", False):
                self.encoder_trigger_sources.add(message.getSourceName())

        elif message.isType("configure1"):
            if self.encoder_scan_supported:
                self.sendMessage(halMessage.HalMessage(
                    m_type = "initial parameters",
                    data = {"parameters" : self.encoder_parameters.copy()}))

        elif message.isType("current parameters"):
            if self.encoder_scan_supported:
                message.addResponse(halMessage.HalMessageResponse(
                    source = self.module_name,
                    data = {"parameters" : self.encoder_parameters.copy()}))
                return True

        elif message.isType("new parameters"):
            if self.encoder_scan_for_film:
                raise EncoderScanError("Cannot change encoder parameters during a film")
            root = message.getData()["parameters"]
            module_p = root.get(self.module_name) if root.has(self.module_name) else None
            scan_p = module_p.get("encoder_scan") if module_p is not None and module_p.has("encoder_scan") else None
            if not self.encoder_scan_supported and scan_p is None:
                return False
            message.addResponse(halMessage.HalMessageResponse(
                source = self.module_name,
                data = {"old parameters" : self.encoder_parameters.copy()}))
            defaults = EncoderScanSettings()
            settings = EncoderScanSettings(**{
                name: scan_p.get(name, getattr(defaults, name)) if scan_p is not None
                else getattr(defaults, name) for name in defaults.__dataclass_fields__})
            if settings.enabled:
                if self.encoder_scan is None:
                    raise EncoderScanError("Set encoder_scan_supported=True in the Tiger hardware configuration")
                focus_p = root.get("focuslock") if root.has("focuslock") else None
                if focus_p is not None and focus_p.get("hardware_z_scan.z_offsets", "").strip():
                    raise EncoderScanError("Clear focuslock.hardware_z_scan.z_offsets for encoder scans")
                timing_p = root.get("timing") if root.has("timing") else None
                if timing_p is not None and timing_p.get("time_base", "") == "hardware_timing":
                    raise EncoderScanError("Use a camera time_base, not the free-running hardware_timing counter")
            hardwareModule.runHardwareTask(
                self, message, lambda: self.updateEncoderParameters(message, settings))
            return True

        elif message.isType("start camera"):
            if self.encoder_scan_for_film and message.getData()["master"]:
                if not self.encoder_scan_triggered:
                    self.encoder_scan_triggered = True
                    hardwareModule.runHardwareTask(
                        self, message, lambda: self.runEncoderTask(
                            lambda: self.encoder_scan.start_scan(reuse_setup=self.encoder_reuse_setup)))
                return True

        elif message.isType("stop camera"):
            if self.encoder_scan_for_film and message.getData()["master"]:
                hardwareModule.runHardwareTask(
                    self, message, lambda: self.runEncoderTask(self.encoder_scan.stop_scan))
                return True

        elif message.isType("stop film"):
            if self.encoder_scan_supported:
                message.addResponse(halMessage.HalMessageResponse(
                    source = self.module_name,
                    data = {"parameters" : self.encoder_parameters.copy()}))
            # Continue into the ordinary stage/LED stop-film handling.
        return False

    def getFunctionality(self, message):
        if message.getData()["name"] in self.functionalities:
            fn = self.functionalities[message.getData()["name"]]
            message.addResponse(halMessage.HalMessageResponse(source = self.module_name,
                                                              data = {"functionality" : fn}))

    def handleResponse(self, message, response):
        if message.isType("get functionality"):
            if (message.getData()["extra data"] == "z_piezo"):
                self.z_piezo_functionality = TigerVoltageZFunctionality(
                    ao_fn = response.getData()["functionality"],
                    parameters = self.z_piezo_configuration.get("parameters"),
                    microns_to_volts = self.z_piezo_configuration.get("microns_to_volts"))
                
                # Configure controller for voltage Z control.
                self.controller_mutex.lock()
                axis = self.z_piezo_configuration.get("axis")
                mode = self.z_piezo_configuration.get("mode")
                self.controller.zConfigurePiezo(axis, mode)
                self.controller_mutex.unlock()
        
                # Add to dictionary of available functionalities.
                self.functionalities[self.module_name + ".z_piezo"] = self.z_piezo_functionality
            
    def processMessage(self, message):
        if self.processEncoderMessage(message):
            return
        if message.isType("configure1"):
            if self.z_piezo_configuration is not None:
                self.sendMessage(halMessage.HalMessage(
                    m_type = "get functionality",
                    data = {"name" : self.z_piezo_configuration.get("ao_fn_name"),
                            "extra data" : "z_piezo"}))
            
        elif message.isType("get functionality"):
            self.getFunctionality(message)
            
        #
        # The rest of the message are only relevant if we actually have a XY stage.
        #
        if self.stage_functionality is None:
            # A Z-only controller still participates in film start/stop.
            if message.isType("start film"):
                self.startFilm(message)
            elif message.isType("stop film"):
                self.stopFilm(message)
            return

        if message.isType("configuration"):
            if message.sourceIs("tcp_control"):
                self.tcpConnection(message.getData()["properties"]["connected"])

            elif message.sourceIs("mosaic"):
                self.pixelSize(message.getData()["properties"]["pixel_size"])

        elif message.isType("start film"):
            self.startFilm(message)

        elif message.isType("stop film"):
            self.stopFilm(message)
            
        elif message.isType("tcp message"):
            self.tcpMessage(message)

    def startFilm(self, message):
        if self.encoder_parameters.get("encoder_scan.enabled"):
            if any(self.encoder_cameras.values()):
                raise EncoderScanError("All acquisition cameras must have master=False for encoder scans")
            if self.encoder_trigger_sources:
                raise EncoderScanError("Remove independent trigger modules for encoder scans: " +
                                       ", ".join(sorted(self.encoder_trigger_sources)))
            self.encoder_scan_for_film = True
            self.encoder_scan_triggered = False
            self.encoder_reuse_setup = message.getData()["film settings"].reuseEncoderScan()
            self.pauseEncoderPolling()
            run_shutters = message.getData()["film settings"].runShutters()
            hardwareModule.runHardwareTask(
                self, message, lambda: self.prepareEncoderFilm(run_shutters))
            return
        if self.stage_functionality is not None:
            super().startFilm(message)
        #
        # Need to use runHardwareTask() here so that we can be sure that the
        # Tiger LED controller will be in the correct state before we start
        # filming.
        #
        if (message.getData()["film settings"].runShutters()):
            hardwareModule.runHardwareTask(self, message, self.startLED)

    def startLED(self):
        #
        # Set TTL mode for one functionality sets the mode for all functionalities,
        # assuming there is only a single LED driver card.
        #
        set_ttl = False
        for fn_name in self.functionalities:
            if ("led" in fn_name):
                if not set_ttl:
                    self.functionalities[fn_name].setFilmTTLMode(True)
                    set_ttl = True
                self.functionalities[fn_name].setFilmPower()
                    

    def stopFilm(self, message):
        # Save this before resetting the encoder flags.
        was_encoder_film = self.encoder_scan_for_film

        if was_encoder_film:
            if self.encoder_scan.scan_started:
                raise EncoderScanError(
                    "ASI scan is not idle; position polling remains suspended"
                )

            self.resumeEncoderPolling()
            self.encoder_scan_for_film = False
            self.encoder_scan_triggered = False
            self.encoder_reuse_setup = False

        if self.stage_functionality is not None:
            if was_encoder_film:
                # Save XY position without restoring joystick/motor controls.
                pos = self.stage_functionality.getCurrentPosition()
                pos_string = "{0:.2f},{1:.2f}".format(pos["x"], pos["y"])

                pos_param = params.ParameterCustom(
                    name="stage_position",
                    value=pos_string
                )

                message.addResponse(
                    halMessage.HalMessageResponse(
                        source=self.module_name,
                        data={"acquisition": [pos_param]}
                    )
                )
            else:
                # Preserve the original behavior for non-encoder movies.
                super().stopFilm(message)

        hardwareModule.runHardwareTask(self, message, self.stopLED)
    def stopLED(self):
        for fn_name in self.functionalities:
            if ("led" in fn_name):
                self.functionalities[fn_name].setFilmTTLMode(False)
                break
