# -*- coding: utf-8 -*-
from __future__ import absolute_import, print_function

import threading
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

if __name__ == "__main__":
    pass