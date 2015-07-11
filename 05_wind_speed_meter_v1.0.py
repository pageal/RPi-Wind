from ABE_ADCPi import ADCPi
from ABE_helpers import ABEHelpers
import time
import threading
import os
import commands
import re
import struct
import ctypes
import socket
import math

#this SW is for prototype model using sensors F200-201/2

###################################
## begin: main parameters override
file_report_period = 10
## HW wiring (-1 means - not connected)
a2d_chan_speed = 1
a2d_chan_direction = -1
a2d_chan_vcc = -1
## HW parameters
wind_sensor_radius = 0.1 # meters
## begin: main parameters override
###################################

class sample():
    def __init__(self):
        self.sample_time_stamp = None
        self.sample_time_span = 0
        self.speed_pulses = 0
        self.speed_m_per_sec = 0.0
        self.direction_voltage = 0.0
        self.direction_code = ""


class wind_speed_meter():
    def __init__(self):
        self._a2d_chan_speed = a2d_chan_speed
        self._a2d_chan_direction = a2d_chan_direction
        self._a2d_chan_vcc = a2d_chan_vcc
        
        self._file_report_period = file_report_period

        self._i2c_helper = ABEHelpers()
        self._bus = self._i2c_helper.get_smbus()
        self._adc = ADCPi(self._bus, 0x68, 0x69, 12)

        self._CtrlPort = 8200
        self._IPAddr = self.get_local_ip()
        self._samples = list()
        self._samples_lock = threading.Lock()        

        self._thread_sampling = None
        self._thread_reporting = None
        self._stop_requested = False


    def get_local_ip(self):
        ifconfig_cmd = commands.getoutput("ifconfig")
        patt = re.compile(r'inet\s*\w*\S*:\s*(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})')
        addr_list = patt.findall(ifconfig_cmd)
        for addr in addr_list:
            if addr == "127.0.0.1":
                continue
##            if(self._nettype == NETTYPE.CELL):
##                if(addr.find("192.168.") == 0):
##                    continue
            if(addr.find('.')>0):
                return addr
        return "127.0.0.1"

    def start(self):
        self._stop_requested = False
        self._thread_sampling = threading.Thread(target=self.thread_sampling)
        self._thread_sampling.start()
        self._thread_reporting = threading.Thread(target=self.thread_reporting)
        self._thread_reporting.start()
        
    def thread_reporting(self):
        print("reporting thread started")
        mc_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        #mc_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        mc_socket.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 100)
        mc_socket.bind((self._IPAddr, 0))
        #mreq = struct.pack('4sl', socket.inet_aton("224.0.150.150"), socket.INADDR_ANY)
        mreq = struct.pack('4sl', socket.inet_aton("224.0.1.200"), socket.INADDR_ANY)
        mc_socket.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        #mc_socket.sendto("init", ("224.0.150.150", 8100))

        time_last_report = time.time()
        while(self._stop_requested != True):
            time_now = time.time()

            if(time_now - time_last_report < self._file_report_period) or (time_now < time_last_report):
                time.sleep(0.2)
                continue

            if(len(self._samples) > 0):
                self._samples_lock.acquire()
                smpl = self._samples.pop(0)
                print("extracted speed: {} ({}). list len: {}".format(str(smpl.speed_m_per_sec), smpl.speed_pulses, len(self._samples)))
                self._samples_lock.release()
            time_last_report = time_now

    def thread_sampling(self):
        print("sampling thread started")
        speed_voltage_last = 0
        speed_pulses_counter = 0
        last_sample_timestamp = 0
        time_now = time.time()
        time_last_sampling = time_now
        while(self._stop_requested != True):
            time_now = time.time()

            #read voltage from speed sensor
            speed_voltage = self._adc.read_voltage(self._a2d_chan_speed)
            if( (speed_voltage > 0) and (speed_voltage_last == 0)):
                #count only transitions from low to high signal
                speed_pulses_counter += 1
                #print("pulses: " + str(speed_pulses_counter))
            speed_voltage_last = speed_voltage

            if(time_now - time_last_sampling < self._file_report_period) or (time_now < time_last_sampling):
  #              time.sleep(0.001)
                continue

            smpl = sample()
            smpl.sample_time_stamp = time_now
            smpl.sample_time_span = time_now - time_last_sampling
            smpl.speed_pulses = speed_pulses_counter
            smpl.speed_m_per_sec = (speed_pulses_counter*2*math.pi*wind_sensor_radius)/smpl.sample_time_span

            if(self._a2d_chan_vcc != -1):
                vcc = self._adc.read_voltage(self._a2d_chan_vcc)

            if(a2d_chan_direction != -1):    
                smpl.direction_voltage = self._adc.read_voltage(self._dir_channel)
                smpl.direction_code = ""

            self._samples_lock.acquire()
            self._samples.append(smpl)
            print("speed: {} ({}). list len: {}".format(str(smpl.speed_m_per_sec), smpl.speed_pulses, len(self._samples)))
            self._samples_lock.release()

            speed_pulses_counter = 0
            time_last_sampling = time.time()


    def Stop(self):
        if(self._thread == None):
            return
        self._stop_requested = True
        if(self._thread.isAlive()):
            self._thread.join();
        self._thread = None

sm = wind_speed_meter()
sm.start()
