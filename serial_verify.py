# -*- coding: utf-8 -*-
from __future__ import absolute_import, print_function

import threading
import eventlet
from eventlet.green import serial

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
        self.buf += inPut
        self.lock.release

if __name__ == "__main__":
    pass