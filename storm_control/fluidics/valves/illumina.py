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

class ViciValve(AbstractValve):
    
    def __init__(self, com_port=2, verbose= False):

        self.com_port = com_port
        self.verbose = verbose
        self.read_length = 64

        self.serial = serial.Serial(port = self.com_port, 
                baudrate=9600,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                bytesize=serial.EIGHTBITS,
                timeout=0.5)
        #give the arduino time to initialize
        time.sleep(1)
        self.port_count = self.getPortCount()
        #time.sleep(0.5)
        self.updated =True
        self.current_position, self.moving = -1,False
        self.updateValveStatus()
        #
        
    def getPortCount(self):
        #self.write('N?')
        #return int(self.read().strip(string.ascii_letters))
        return 37
    def get_single_valve_position(self,id_='0'):
        time.sleep(0.5)
        self.write(id_+'CP')
        time.sleep(1)
        response = self.read()
        if len(response):
            try:
                return int(response[-2:])
            except:
                return 0
        else:
            return 0
    def updateValveStatus(self):
        print("Status update BB")
        
        if self.updated:
        
            p1 = self.get_single_valve_position(id_='0')
            p2 = self.get_single_valve_position(id_='1')
            if p1<24:
                self.current_position = p1
            else:
                self.current_position = p2
                if p2>0:
                    self.current_position += 20
            self.moving = False    
            
            if self.current_position==38: self.current_position=20
            if self.current_position==39: self.current_position=29
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
        # special commands
        if port_ID==19:
            self.write('0GO24')
            time.sleep(0.5)
            self.write('1GO18')
            time.sleep(0.5)
        elif port_ID==28:
            self.write('0GO24')
            time.sleep(0.5)
            self.write('1GO19')
            time.sleep(0.5)
        else:
            if port_ID>=20:
                port_ = str(port_ID+1-20).zfill(2)
                self.write('0GO24')
                time.sleep(0.5)
                self.write('1GO'+port_)
                time.sleep(0.5)
            else:
                port_ = str(port_ID+1).zfill(2)
                self.write('0GO'+port_)
        #self.write('P ' + str(port_ID+1))
        
    def howManyValves(self):
        return 1

    def close(self):
        self.serial.close()

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

    def write(self, message):
        appendedMessage = message + '\r\n'
        self.serial.write(appendedMessage.encode())

    def read(self):
        time.sleep(0.25)
        output = b''
        while self.serial.inWaiting()>0:
            bit = self.serial.read(1)
            output+=bit
        inMessage = output.decode().rstrip()
        return inMessage
        
