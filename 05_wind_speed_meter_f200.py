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
file_report_period = 5
## HW wiring (-1 means - not connected)
a2d_chan_speed = 1
a2d_chan_direction = 2
a2d_chan_vcc = -1
## HW parameters
wind_sensor_radius = 0.1 # meters
## begin: main parameters override
###################################

class dir_range():
    '''Range maximal length is 5V/8Notches'''
    def __init__(self, dir_v, max_v = 5.0):
        self.dir = dir_v
        self.max_v = max_v
        self._rg_len = round(max_v/8,1)
        #print("rln " + str(self._rg_len))
        self._rg_high = self.dir + round(self._rg_len/2,1)
        #print("rh " + str(self._rg_high))
        if(self._rg_high > 5):
            self._rg_high = round(self._rg_high - 5, 1)
            #print("rh " + str(self._rg_high))
            
        self._rg_low = round(self._rg_high - self._rg_len, 1)
        #print("rl " + str(self._rg_low))
        
        if(self._rg_low < 0):
            self._rg_low = self.max_v + self._rg_low
            #print("rl " + str(self._rg_low))
        #print("added {}, ({},{})".format(self.dir, self._rg_low, self._rg_high))

    def is_in(self, val):
        if(self._rg_low < self._rg_high):
            if(val >= self._rg_low and val <= self._rg_high):
                return True
        else:
            if(val >= self._rg_low and (val - self._rg_low) <= self._rg_len):
               return True
            if(val <= self._rg_high and (self._rg_high - val)<=self._rg_len):
               return True
        return False

class sample():
    def __init__(self):
        self.sample_time_stamp = None
        self.sample_time_span = 0
        self.voltage_reads = 0
        self.sample_average_voltage = 0
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

        self._thread_sampling_obj = None
        self._thread_reporting_obj = None
        self._stop_requested = False

        self._dirs_stream = list() #stream of directions searched for callibration samples
        
        #calibrated directions
        self._dirs = list()
        self._dirs_volts = list()
        self._dirs_names = ("NN", "NE", "EE", "SE", "SS", "SW", "WW", "NW")


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
        self._thread_sampling_obj = threading.Thread(target=self._thread_sampling)
        self._thread_sampling_obj.start()
        self._thread_reporting_obj = threading.Thread(target=self._thread_reporting)
        self._thread_reporting_obj.start()

    def _dir_calibration(self, smpl):
        if(len(self._dirs_stream) == 0):
            self._dirs_stream.append((smpl.direction_voltage,0))
        else:
            delta_from_prev = 0
            smpl_last = self._dirs_stream[len(self._dirs_stream)-1]
            dir_last = smpl_last[0]

            #calculate delta drom previous direction sensor read
            if(smpl.direction_voltage > dir_last):
                delta_from_prev = smpl.direction_voltage - dir_last
            elif(smpl.direction_voltage > 0) and (smpl.direction_voltage <1) and (dir_last > 4):
                delta_from_prev = smpl.direction_voltage + (5.0 - dir_last)

            #append only changing directions 
            if(dir_last != smpl.direction_voltage):
                self._dirs_stream.append((smpl.direction_voltage,round(delta_from_prev,1)))
            
            
        if (len(self._dirs_stream) == 5):
            self._dirs_stream.pop(0)

        #check if callibration is activated
        print("dirs: " + str(self._dirs_stream))
        calibration_matches = 0
        for dir in self._dirs_stream:
            if(dir[1]) >= 1 and (dir[1])<1.6:
                    calibration_matches+=1
                    
        if(calibration_matches == 4):
            matches = 0
            print("Direction callibration is completed:")
            print("Four consequent directions are cptured for N/E/S/W")
            self._dirs = self._dirs_stream
            self._dirs_stream = list() #reset the stream of directions searched for callibration samples
            self._dirs_volts = list()

            #fill calibration lists
            north = self._dirs[0][0]
            self._dirs_volts.append(dir_range(north))
            dir = self._dirs_volts[len(self._dirs_volts)-1]
            print("{}: {} ({},{})".format(self._dirs_names[0], dir.dir, dir._rg_low, dir._rg_high))
            for i in range(1,len(self._dirs)):
                v_direction_prev = self._dirs[i-1][0]
                v_direction = self._dirs[i][0]
                d_direction = self._dirs[i][1] #delta from previos v_direction
                middle_dir = v_direction_prev + round(d_direction/2,1)
                if(middle_dir > 5):
                    middle_dir -= 5
                self._dirs_volts.append(dir_range(middle_dir))
                idx = len(self._dirs_volts)-1
                dir = self._dirs_volts[idx]
                print("{}: {} ({},{})".format(self._dirs_names[idx], dir.dir, dir._rg_low, dir._rg_high))
                self._dirs_volts.append(dir_range(v_direction))
                idx = len(self._dirs_volts)-1
                dir = self._dirs_volts[idx]
                print("{}: {} ({},{})".format(self._dirs_names[idx], dir.dir, dir._rg_low, dir._rg_high))
        
    def _thread_reporting(self):
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
        self._dirs_stream = list()
        while(self._stop_requested != True):
            time_now = time.time()

            if(time_now - time_last_report < self._file_report_period) or (time_now < time_last_report):
                time.sleep(0.2)
                continue

            if(len(self._samples) > 0):
                self._samples_lock.acquire()
                smpl = self._samples.pop(0)
                self._samples_lock.release()
                if(len(self._dirs_volts)>0):
                    for i in range(0, len(self._dirs_volts)):
                        if self._dirs_volts[i].is_in(smpl.direction_voltage):
                            smpl.direction_code = self._dirs_names[i]
                            break
                print("speed={}m/s Vspeed_avg={}V ({} reads) Vdir={}V DIr={}".format(smpl.speed_m_per_sec, smpl.sample_average_voltage, smpl.voltage_reads, smpl.direction_voltage, smpl.direction_code))

                self._dir_calibration(smpl)
                
            time_last_report = time_now

    def _thread_sampling(self):
        print("sampling thread started")
        speed_reads_counter = 0
        last_sample_timestamp = 0
        time_now = time.time()
        time_last_sampling = time_now
        speed_voltage_sum = 0.0
        while(self._stop_requested != True):
            time_now = time.time()

            # output voltage is proportional to the wind speed at the voltage read moment
            #read voltage from speed sensor
            speed_voltage_sum += self._adc.read_voltage(self._a2d_chan_speed)
            speed_reads_counter += 1

            if(time_now - time_last_sampling < self._file_report_period) or (time_now < time_last_sampling):
                time.sleep(0.01)
                continue

            smpl = sample()
            smpl.sample_time_stamp = time_now
            smpl.sample_time_span = time_now - time_last_sampling
            smpl.voltage_reads = speed_reads_counter
            smpl.sample_average_voltage = round(speed_voltage_sum/smpl.voltage_reads,2)

            #Vout may vary from 0 to 5V which linearry relates to 0.5 to 50M/s
            # Thus each 1V relates to 10M/s or 1 mV -> 0.01 M/s:  1mV means 1cm/s
            # smpl.sample_average_voltage is in volts
            # smpl.sample_average_voltage*1000 gives the value in milli-volts
            # from here smpl.speed_m_per_sec = (smpl.sample_average_voltage*1000)mV*0.01 (Ms/ per mV)
            # or smpl.speed_m_per_sec = smpl.sample_average_voltage*10
            smpl.speed_m_per_sec = round(smpl.sample_average_voltage*10, 2)

            if(self._a2d_chan_vcc != -1):
                vcc = self._adc.read_voltage(self._a2d_chan_vcc)

            if(a2d_chan_direction != -1):    
                smpl.direction_voltage = round(self._adc.read_voltage(self._a2d_chan_direction),1)
                smpl.direction_code = ""
                            
            self._samples_lock.acquire()
            self._samples.append(smpl)
            print("speed={}m/s Vspeed_avg={}V ({} reads) Vdir={}V List_len={}".format(smpl.speed_m_per_sec, smpl.sample_average_voltage, smpl.voltage_reads, smpl.direction_voltage, len(self._samples)))
            self._samples_lock.release()

            speed_reads_counter = 0
            speed_voltage_sum = 0.0
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
