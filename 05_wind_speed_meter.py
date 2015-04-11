from ABE_ADCPi import ADCPi
from ABE_helpers import ABEHelpers
import time
import threading
import os
import glob
import commands
import re
import struct
import ctypes
import socket
import SocketServer
import BaseHTTPServer

the_report_period = 10
timezone = 7200

PAGE_WEATHER = \
"""
<!DOCTYPE html>
<html>
<body>
    <h1> Haifa local time: {} </h1>
    <h1> Local weather data: </h1>
    <h3> - Wind Speed (meters in sec/sensor pulses): [{}] / [{}] </h3>
    <h3> - Wind Direction (decoded/raw): [{}] / [{}]</h3>
    <h3> - Wind Dynamo Average Voltage (Volts): {}</h3>
    <h3> - Temperature sensor Voltage (Volts): {}</h3>
    <h3> - Ambient Temperature (Celsius/Fahrenheit): [{}] / [{}]</h3>

    <h1>Anemometer satus:</h1>
    <h3>Vcc (Volts): {}</h3>
    
</body>
</html>
"""

globals()["g_wind_speed"] = 0.0
globals()["g_wind_speed_pulses"] = 0.0
globals()["g_wind_direction"] = 0.0
globals()["g_wind_v"] = 0.0
globals()["g_vcc"] = 0.0
globals()["g_temp_v"] = 0.0
globals()["g_temp_c"] = 0.0
globals()["g_temp_f"] = 0.0

class temperature_resolver(object):
    def __init__(self):
        #initialize device
        os.system("modprobe w1-gpio")
        os.system("modprobe w1-therm")
        self.dev_dir = '/sys/bus/w1/devices/'

        try:
            self.ds18b20_folder = glob.glob(self.dev_dir + '28*')[0]
            self.ds18b20_data_file = self.ds18b20_folder + '/w1_slave'
        except:
            print("temperature_resolver initialization failed")
            return

    def temp_read(self):
        try:
            f = open(self.ds18b20_data_file, "r")
            lines = f.readlines()
            f.close()
            t_pos = lines[1].find('t=')
            if(t_pos != -1):
                self.raw_temp_str = lines[1][t_pos+2:]
                self.temp_c = float(self.raw_temp_str)/1000.0
                self.temp_f = self.temp_c * 9.0/5.0 + 32.0
        except:
            self.temp_c = 0.0
            self.temp_f = 0.0
        return self.temp_c, self.temp_f

class direction_resolver(object):
    def __init__(self):
        self.north = 1.29
        self.ne1 = 1.34
        self.ne2 = 0.37
        self.east = 0.59
        self.se1 = 0.92
        self.se2 = 1.13
        self.south = 1.65
        self.sw1 = 1.57
        self.sw2 = 1.99
        self.west = 2.01
        self.nw1 =  1.80
        self.nw2 =  1.96

        self._amp_to_dir_name = {self.north:"N", self.ne1:"NE", self.ne2:"NE",
                            self.east:"E", self.se1:"SE", self.se2:"SE",
                            self.south :"S",  self.sw1:"SW", self.sw2:"SW",
                            self.west:"W", self.nw1:"NW", self.nw2:"NW"}

    def resolve(self, sig_amp):
        sig_amp_str = "%0.2f"%sig_amp
        try:
            dir_name = self._amp_to_dir_name[float(sig_amp_str)]
            return dir_name
        except Exception:
            return " "

class HTTPHandler(BaseHTTPServer.BaseHTTPRequestHandler):
    _preview = False
    _camera = None
    stop_streaming = False
    _dr = direction_resolver()

    def requestline(self):
        return 1

    def request_version(self):
        return 'HTTP/5.0'

    def handle(self):

        print("handle request request")
        the_page = PAGE_WEATHER.format(time.ctime(int(time.time()+timezone)),
                    globals()["g_wind_speed"],
                    globals()["g_wind_speed_pulses"],
                    self._dr.resolve(globals()["g_wind_direction"]),
                    str(globals()["g_wind_direction"]),
                    str(globals()["g_wind_v"]),
                    str(globals()["g_temp_v"]),
                    globals()["g_temp_c"],
                    globals()["g_temp_f"],
                    globals()["g_vcc"])

        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(the_page)))
        self.send_header("refresh", str(the_report_period))
        self.request.send(the_page)
        self.end_headers()

        
        
class NETTYPE:
    LAN = 1
    WLAN = 2
    CELL = 3

class DataServerHTTP :
    _stop_server = False
    _main_thread = None
    _nettype = NETTYPE.LAN

    def GetLocalIP(self):
        ifconfig_cmd = commands.getoutput("ifconfig")
        patt = re.compile(r'inet\s*\w*\S*:\s*(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})')
        addr_list = patt.findall(ifconfig_cmd)
        for addr in addr_list:
            if addr == "127.0.0.1":
                continue
            if(self._nettype == NETTYPE.CELL):
                if(addr.find("192.168.") == 0):
                    continue
            if(addr.find('.')>0):
                return addr
        return "127.0.0.1"


    def _HTTPThread(self):
        IP = self.GetLocalIP()
        print("http server started at " + IP + ":8090")
        while (self._stop_server == False):
            self._http_srv = BaseHTTPServer.HTTPServer((IP, 8090),HTTPHandler)
            self._http_srv.rbufsize = -1
            self._http_srv.wbufsize = 100000000
            try:
                self._http_srv.handle_request()
            except Exception as e:
                pass
            self._http_srv.socket.close()
        print("http server finished")

    def Run(self):
        self._main_thread = threading.Thread(target=self._HTTPThread)
        self._main_thread.start()

    def Stop(self):
        self._stop_server=True
        self._http_srv.socket.close()
        

class WindSpeedMeter():
    _channel = 0
    _report_period = 5
    _pulses_counter = 0
    _last_sample_pulses = 0
    _last_sample_time = 0

    _thread = None
    _stop_requested = False

    _IPAddr = "127.0.0.1"
    _CtrlPort = 8200

    _tr = temperature_resolver()


    def GetLocalIP(self):
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

    def __init__(self, channel = 1, dir_channel = 2, wind_power_channel = 3, report_period = the_report_period):
        self._channel = channel
        self._dir_channel = dir_channel
        self._wind_power_channel = wind_power_channel
        self._temp_channel = 4
        self._report_period = report_period
        self._i2c_helper = ABEHelpers()
        self._bus = self._i2c_helper.get_smbus()
        self._adc = ADCPi(self._bus, 0x68, 0x69, 12)

        self._IPAddr = self.GetLocalIP()


    def Start(self):
        self._stop_requested = False
        self._thread = threading.Thread(target=self.SamplingThread)
        self._thread.start()
        print("sampling thread initialized")

    def SendMCStatus(self, msg):
        mc_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        #mc_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        mc_socket.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 100)
        mc_socket.bind((self._IPAddr, 8100))
        #mreq = struct.pack('4sl', socket.inet_aton("224.0.0.1"), socket.INADDR_ANY)
        mreq = struct.pack('4sl', socket.inet_aton("224.0.0.1"), socket.INADDR_ANY)
        mc_socket.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        #mc_socket.sendto("init", ("224.0.0.1", 8100))

        mc_socket.sendto(msg, ("224.0.0.1", 8200))
        mc_socket.close()
        
    def SamplingThread(self):
        print("sampling thread started")

        last_voltage = 0
        time_last_report = time.time()
        self._last_sample_pulses = 0
        wind_v = 0
        while(self._stop_requested != True):
            time_now = time.time()
            voltage = self._adc.read_voltage(self._channel)
            if( (voltage > 0) and (last_voltage == 0)):
                self._pulses_counter += 1
                #print("pulses: " + str(self._pulses_counter))

                curr_wind_v = self._adc.read_voltage(self._wind_power_channel)
                wind_v += curr_wind_v

            last_voltage = voltage


            if(time_now > time_last_report):
                if(time_now - time_last_report >= self._report_period):
                    pulses = (self._pulses_counter - self._last_sample_pulses)
                    speed = (pulses * 3.14159 * 2 * 0.09)/(time_now - time_last_report)
                    vcc = self._adc.read_voltage(8)
                    direction = self._adc.read_voltage(self._dir_channel)
                    if(pulses != 0):
                        wind_v = wind_v/pulses
                    temp_v = self._adc.read_voltage(self._temp_channel)
                    temp_c, temp_f = self._tr.temp_read()
                    try:
                        msg = "VCC: %02f, WS: %0.3f(%03d), WD: %0.3f WndV: %0.3f TmpV: %0.3f Tmp: %0.3f" % \
                        (vcc, speed, pulses, direction, wind_v, temp_v, temp_c)
                        print(msg)
                        self.SendMCStatus(msg)
                    except Exception:
                        pass
                    self._last_sample_pulses = self._pulses_counter

                    globals()["g_wind_speed"] = speed
                    globals()["g_wind_speed_pulses"] = pulses
                    globals()["g_wind_direction"] = direction
                    globals()["g_wind_v"] = wind_v
                    globals()["g_vcc"] = vcc
                    globals()["g_temp_v"] = temp_v
                    globals()["g_temp_c"] = temp_c
                    globals()["g_temp_f"] = temp_f
                    time_last_report = time_now
                    wind_v = 0
            else:
                time_last_report = time_now
            time.sleep(0.01)

    def Stop(self):
        if(self._thread == None):
            return
        self._stop_requested = True
        if(self._thread.isAlive()):
            self._thread.join();
        self._thread = None

sm = WindSpeedMeter()
sm.Start()

srv = DataServerHTTP()
srv.Run()
