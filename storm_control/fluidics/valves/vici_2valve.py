'''
A class for serial interface to VICI Illumina valve syste with 24 valves.
This assumes that there are 2 valves connected in seris on port 24 valve 1 ->input valve 2
This assumes that valve 2 position 1 is total position 21.
This assumes that valve 1 has been configured with id 0  and valve 2 has been given id 1.

Bogdan Bintu
7/9/2022
'''
import serial
import string
import time

from storm_control.fluidics.valves.valve import AbstractValve

class ViciValve2(AbstractValve):
    
    def __init__(self, com_port=2, verbose= False):

        self.com_ports = str(com_port).split(',')
        self.verbose = verbose
        
        self.serials = [serial.Serial(port = port, 
                baudrate=9600,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                bytesize=serial.EIGHTBITS,
                timeout=0.5) for port in self.com_ports]
        #give the arduino time to initialize
        time.sleep(1)
        self.port_count = self.getPortCount()
        #time.sleep(0.5)
        self.updated =True
        self.current_position, self.moving = -1,False
        self.updateValveStatus()
        #
        
    def getPortCount(self):
        return 47
    def get_single_valve_position(self,serial):
        time.sleep(0.5)
        self.write('CP',serial)
        time.sleep(1)
        response = self.read(serial)
        if len(response):
            try:
                return int(response[-2:])
            except:
                return 0
        else:
            return 0
    def updateValveStatus(self):
        #print("Status update BB")
        
        if self.updated:
            serial1,serial2 = self.serials[0],self.serials[-1]
            p1 = self.get_single_valve_position(serial1)
            p2 = self.get_single_valve_position(serial2)
            if p1<24:
                self.current_position = p1
            else:
                self.current_position = p2
                if p2>0:
                    self.current_position += 24-1
            self.moving = False    
            self.updated = False
        return self.current_position, self.moving

    '''
    Ignores the direction, always moves in the direction to minimize the 
    move distance. ValveID is also ignored.
    '''
    def changePort(self, valve_ID, port_ID, direction = 0):
        self.updated=True
        if not self.isValidPort(port_ID):
            return False
        if True:
            serial1,serial2 = self.serials[0],self.serials[-1]
            if port_ID>=23:
                port_ = str(port_ID+1-23).zfill(2)
                self.write('GO24',serial1)
                time.sleep(0.25)
                self.write('GO'+port_,serial2)
                time.sleep(0.5)
            else:
                port_ = str(port_ID+1).zfill(2)
                self.write('GO'+port_,serial1)
        #self.write('P ' + str(port_ID+1))
        
    def howManyValves(self):
        return 1

    def close(self):
        [serial.close() for serial in self.serials]

    def getDefaultPortNames(self, valve_ID):
        return ['Port ' + str(portID + 1) for portID in range(self.port_count)]

    def howIsValveConfigured(self, valve_ID):
        return str(self.port_count) + ' ports'

    def getStatus(self, valve_ID):
        position, moving = self.updateValveStatus()
        return ('Port ' + str(position), moving)

    def resetChain(self):
        pass

    def getRotationDirections(self, valve_ID):
        return ['Least distance']

    def isValidPort(self, port_ID):
        return port_ID < self.port_count

    def write(self, message,serial):
        appendedMessage = message + '\r\n'
        serial.write(appendedMessage.encode())

    def read(self,serial):
        time.sleep(0.25)
        output = b''
        while serial.inWaiting()>0:
            bit = serial.read(1)
            output+=bit
        inMessage = output.decode().rstrip()
        return inMessage
        
