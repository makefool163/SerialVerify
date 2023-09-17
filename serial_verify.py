# -*- coding: utf-8 -*-
from __future__ import absolute_import, print_function

import threading
import serial
import eventlet

class pipe_Str:
    def __init__(self):
        self.buf = b""
        self.lock = threading.Semaphore(1)
    def put(self, inPut):
        self.lock.acquire()
        self.buf += inPut
        self.lock.release
    def get(self, l):
        self.lock.acquire()
        ret = self.buf[-l:]
        self.buf = self.buf[:-l]
        self.lock.release
        return ret
    def qsize(self):
        return len(self.buf)        

class serial_verify:
    def __init__(self, com_port, baud_rate):
        self.write_enable = False
        self.com = serial.Serial(port=com_port, 
                                timeout = 0, 
                                # non-blocking mode, return immediately in any case, 
                                # returning zero or more, up to the requested number of bytes
                                baudrate=baud_rate,
                                rtscts=True,
                                dsrdtr=True,
                                # We use hardware stream control here
                                parity=serial.PARITY_EVEN,
                                bytesize=serial.EIGHTBITS)
        
    def write(self, buf):

    def read(self, block=False):

if __name__ == "__main__":
    pass