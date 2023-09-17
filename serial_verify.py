# -*- coding: utf-8 -*-
from __future__ import absolute_import, print_function

import threading
import serial
import asyncio
import struct
import re
import eventlet

crc16_table = \
[   0x0000,0xa001,0xe003,0x4002,0x6007,0xc006,0x8004,0x2005,0xc00e,0x600f,0x200d,0x800c,0xa009,0x8,0x400a,0xe00b,
    0x201d,0x801c,0xc01e,0x601f,0x401a,0xe01b,0xa019,0x18,0xe013,0x4012,0x10,0xa011,0x8014,0x2015,0x6017,0xc016,
    0x403a,0xe03b,0xa039,0x0038,0x203d,0x803c,0xc03e,0x603f,0x8034,0x2035,0x6037,0xc036,0xe033,0x4032,0x30,0xa031,
    0x6027,0xc026,0x8024,0x2025,0x0020,0xa021,0xe023,0x4022,0xa029,0x28,0x402a,0xe02b,0xc02e,0x602f,0x202d,0x802c,
    0x8074,0x2075,0x6077,0xc076,0xe073,0x4072,0x0070,0xa071,0x407a,0xe07b,0xa079,0x78,0x207d,0x807c,0xc07e,0x607f,
    0xa069,0x0068,0x406a,0xe06b,0xc06e,0x606f,0x206d,0x806c,0x6067,0xc066,0x8064,0x2065,0x60,0xa061,0xe063,0x4062,
    0xc04e,0x604f,0x204d,0x804c,0xa049,0x0048,0x404a,0xe04b,0x40,0xa041,0xe043,0x4042,0x6047,0xc046,0x8044,0x2045,
    0xe053,0x4052,0x0050,0xa051,0x8054,0x2055,0x6057,0xc056,0x205d,0x805c,0xc05e,0x605f,0x405a,0xe05b,0xa059,0x58,
    0xa0e9,0x00e8,0x40ea,0xe0eb,0xc0ee,0x60ef,0x20ed,0x80ec,0x60e7,0xc0e6,0x80e4,0x20e5,0xe0,0xa0e1,0xe0e3,0x40e2,
    0x80f4,0x20f5,0x60f7,0xc0f6,0xe0f3,0x40f2,0x00f0,0xa0f1,0x40fa,0xe0fb,0xa0f9,0xf8,0x20fd,0x80fc,0xc0fe,0x60ff,
    0xe0d3,0x40d2,0x00d0,0xa0d1,0x80d4,0x20d5,0x60d7,0xc0d6,0x20dd,0x80dc,0xc0de,0x60df,0x40da,0xe0db,0xa0d9,0xd8,
    0xc0ce,0x60cf,0x20cd,0x80cc,0xa0c9,0x00c8,0x40ca,0xe0cb,0xc0,0xa0c1,0xe0c3,0x40c2,0x60c7,0xc0c6,0x80c4,0x20c5,
    0x209d,0x809c,0xc09e,0x609f,0x409a,0xe09b,0xa099,0x98,0xe093,0x4092,0x90,0xa091,0x8094,0x2095,0x6097,0xc096,
    0x0080,0xa081,0xe083,0x4082,0x6087,0xc086,0x8084,0x2085,0xc08e,0x608f,0x208d,0x808c,0xa089,0x88,0x408a,0xe08b,
    0x60a7,0xc0a6,0x80a4,0x20a5,0x00a0,0xa0a1,0xe0a3,0x40a2,0xa0a9,0x00a8,0x40aa,0xe0ab,0xc0ae,0x60af,0x20ad,0x80ac,
    0x40ba,0xe0bb,0xa0b9,0x00b8,0x20bd,0x80bc,0xc0be,0x60bf,0x80b4,0x20b5,0x60b7,0xc0b6,0xe0b3,0x40b2,0xb0,0xa0b1]

def cacl_crc16(binStr):
    crc = 0xFFFF  
    for b in binStr:  
        crc = crc16_table[(crc ^ b) & 0xFF]  
    return crc & 0xFFFF

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

async def async_sleep():
    await asyncio.sleep(0.1)

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
        self.write_bufs = {}
        self.read_bufs  = {}
    def write(self, source, target, buf):
        while (source, target) in self.write_bufs:
            # 前面的数据还没有处理完成，就不执行下面的，保持阻塞状态
            asyncio.run(async_sleep())
        self.write_bufs[(source,target)] = buf
    def read(self, target, block=False):
        if block:
            while target not in self.read_bufs:
                # 没有数据，就进行阻塞
                asyncio.run(async_sleep())
        if target in self.read_bufs:
            return self.read_bufs[target]
        else:
            return None
    async def Com_Read(self):
        d = ""
        while True:
            in_buf = self.com.read_all()
            if len(in_buf) == 0:
                # 缓冲都读完了，就可以切换出去了
                await asyncio.sleep(0.1)
            d += in_buf
            # 找 第一个 55 AA 帧同步字
            match = re.search(b"\x55\xAA", d)
            if match:
                i = match.start()
                frame_head = d[i:i+7]
                f_len = 0
                f_len1, f_src, f_trg, f_len2 = struct.unpack ("xxBBBB", frame_head)
                if f_len1 != f_len2:
                    c1 = cacl_crc16 (d[i:i+f_len1])
                    c2 = cacl_crc16 (d[i:i+f_len2])
                    cc1, = struct.unpack("H", d[i+f_len1:i+f_len1+2])
                    cc2, = struct.unpack("H", d[i+f_len2:i+f_len2+2])
                    if c1 == cc1:
                        f_len = f_len1
                    if c2 == cc2:
                        f_len = f_len2
                else:
                    c1 = cacl_crc16 (d[i:i+f_len1])
                    cc1, = struct.unpack("H", d[i+f_len1:i+f_len1+2])
                    if c1 == cc1:
                        f_len = f_len1
                if f_len != 0:
                    # crc校验成功，写输出数据
                    self.read_bufs[f_trg] = [f_src, d[i+7:i+f_len]]
                    d = d[i +7 +f_len +3:]
                    # 返回 确认帧
                    pass
                else:
                    # 没有找到，只好跳过这个帧头，继续找下一帧了
                    d = d[i+7:]

if __name__ == "__main__":
    pass