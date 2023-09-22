# -*- coding: utf-8 -*-
from __future__ import absolute_import, print_function

import serial
import asyncio
import struct
import re
import queue
import time
import os

import timeit
import crcmod
import multiprocessing

#os.environ['PYTHONASYNCIODEBUG'] = '1'

def print_hex(buf, f):
    # 把输入的 bytes 打印成 16进制形式
    hex = ''
    for b in buf:
        hex += f + format(b, '02x')
    print (hex)

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

def calc_crc16(data):
    # 生成 CRC16 函数，使用 modbus 格式
    crc16_func = crcmod.predefined.Crc('modbus')

    # 更新 CRC16 计算器
    crc16_func.update(data)

    # 获取计算出来的 CRC16 值
    crc16_value = crc16_func.crcValue

    return crc16_value

def calc2_crc16(binStr):
    crc = 0xFFFF  
    for b in binStr:
        crc = crc16_table[(crc ^ b) & 0xFF]
    return crc & 0xFFFF

async def async_sleep():
    await asyncio.sleep(0.1)

class serial_verify:
    """
    Com_Write, Com_Read 两个方法是 async 协程
    必须使用 create_task 启动，串口读写机制才能工作

    Write、Read 两个方法 提供外部访问本对象
    注意调用 Write、Read 的方法必须使用 await 调用
    因为 Write、Read 两个方法都有 协程阻塞的特性
    """
    def __init__(self, com_port, baud_rate, call_name = "default"):
        self.confirm_timeout_const = 200 * (10.0 / baud_rate)
        self.One_Frame_time_out = 0xAA * 10.0 / baud_rate
        # 在write 方法中，会把全部数据发完的时间 重新设置为 超时时间
        self.call_name = call_name
        self.write_enable = False
        self.baud_rate = baud_rate
        self.com = serial.Serial(port=com_port, 
                                timeout = 0, 
                                # non-blocking mode, return immediately in any case, 
                                # returning zero or more, up to the requested number of bytes
                                baudrate=baud_rate,
                                rtscts=True,
                                #dsrdtr=True,
                                # We use hardware stream control here
                                parity=serial.PARITY_EVEN,
                                bytesize=serial.EIGHTBITS)
        self.confirm_Queue = queue.Queue()
        # 确认帧，不可能重发，如果确认帧都不能保证正确，通信效果就岌岌可危了
        self.send_bufs = {}
        self.left_bufs = {}
        self.recv_bufs  = {}
        self.recv_idx = {}
        self.Stop_Sign = False

    def Start(self):
        asyncio.create_task(self.Com_Write())
        asyncio.create_task(self.Com_Read())

    def __del__(self):
        self.com.close()

    async def write(self, tagA, tagB, buf):
        """
        写串口调用
        如果串口正忙的话，该调用会被阻塞直到允许数据发送
        通过指定 tagA,tagB （即发送、接受双方的编号），这样可以将串口通道进行 复用
        tagA,tagB 信息会传送到对侧
        若不复用，可以把 tagA,tagB 指定成固定值
        特别注意， 为了避免与同步字相同 tagA, tagB 取值不能是 0x55 0xAA
        """
        while (tagA, tagB) in self.send_bufs:
            # 前面的数据还没有处理完成，就不执行下面的，保持阻塞状态
            await asyncio.sleep(0.1)
            #asyncio.run(async_sleep())
        # 把 输入 组合成 待发送的 数据帧
        self.send_bufs[(tagA, tagB)] = {}
        self.left_bufs[(tagA, tagB)] = {}
        idx = 0
        buf_len = len(buf)        
        while len(buf) > 0:
            if idx == 0:
                self.send_bufs[(tagA, tagB)][idx] = [buf[:0x9F], 0]
                # 第一帧的 第  1 个字节为总帧数
                # 第一帧的 第2、3个字节为总帧数
                buf = buf[0x9F:]
            else:
                self.send_bufs[(tagA, tagB)][idx] = [buf[:0xA2], 0]
                # 前一个元素是 数据 本身，后一个 用来标识 是否已发送过
                buf = buf[0xA2:]
            idx += 1
            if idx == 0x55 or idx == 0xAA:
                idx += 1
            # 输入 8k 的长度，idx 不会超过 51个
        self.confirm_timeout_const = 200 * idx * 0xAA * 10.0 / self.baud_rate
        #self.confirm_timeout_const = 0.8
        f_head = struct.pack("=BH", idx, buf_len)
        self.send_bufs[(tagA, tagB)][0][0] = f_head +self.send_bufs[(tagA, tagB)][0][0]
        #print ("serial_verify write F_idx", idx)

    async def read(self, block=False):
        """
        读串口调用
        选阻塞模式时，将阻塞直至有返回值
        非阻塞模式时，若返回值，则返回None
        返回值是((tagA, tagB), ret_data) 这样的形式
        如果串口已经收到了多组数据，本调用仅返回其中一组数据        
        """
        def read_sub(self):
            #print ("self.recv_bufs", self.recv_bufs)
            for k in self.recv_idx:
                #print ("                     self.recv_idx", self.recv_idx, len(self.recv_bufs[k]))
                f_idx_len, f_buf_len = self.recv_idx[k]
                if len(self.recv_bufs[k]) == f_idx_len:
                    #print ("serial_verify read already")
                    ids = list(self.recv_bufs[k].keys())
                    ids.sort()
                    oStr = b""
                    for i in ids:
                        oStr += self.recv_bufs[k][i]
                    del self.recv_bufs[k]
                    del self.recv_idx[k]
                    return (k, oStr)
            return None
        ret = read_sub(self)
        while type(ret) == type(None) and block:
            ret = read_sub(self)
            await asyncio.sleep(0.1)
            #asyncio.run(async_sleep())
            # 用事件循环 阻塞一下
        return ret

    async def Com_Write(self):
        """
        内置 的写串口方法
        需要用 task 来启动
        pyserial 的write不是异步的，
        此处的异步是在等待输入数据（即有Write方法写入数据）时进行异步
        """
        def assemble_Frame(k, i, buf):
            # 55 AA LL XX YY ZZ LL ... CC CC AA
            f_len = len(buf)
            tgA, tgB = k
            oStr  = b"\x55\xAA"
            oStr += struct.pack("=BBBBB", f_len, tgA, tgB, i, f_len)
            oStr += buf
            cc = calc_crc16(oStr)
            oStr += struct.pack("=H", cc)
            oStr += b"\xAA"
            return oStr
        # 总体的发送优先级
        # 1、接受确认帧
        # 2、补发数据帧
        # 3、正常数据帧
        print (self.call_name, "Com_Write Enter.")
        #await asyncio.sleep(0.1)
        while True:
            if self.Stop_Sign:
                break
            # 1、接受确认帧
            #print ("Com_Write confirm write")
            while True:
                try:
                    buf = self.confirm_Queue.get(block=False)
                    #print_hex(buf, "-")
                    self.com.write(buf)
                    #print (self.call_name, "confirm_Frame com.write", end="")
                    #print_hex (buf, "-")
                except queue.Empty:
                    break
            # 2、补发数据帧
            #print ("Com_Write Missing write")
            # 补发 跳帧
            for k in self.left_bufs:
                ids = list(self.left_bufs[k].keys())
                ids.sort()
                for i in ids:
                    if i not in self.left_bufs[k].keys():
                        continue
                    buf = self.send_bufs[k][i][0]
                    oStr = assemble_Frame(k, i, buf)
                    l = self.com.write(oStr)
                    # Windows 10 系统分配的串口发送缓冲区大小为 4KB
                    # 表面上 write 是阻塞方式，实际上只是写缓冲区
                    time_out = self.confirm_timeout_const + time.time()
                    self.send_bufs[k][i][1] = time_out
                    print (self.call_name, "Send Missing Frame com.write", i, l, len(ids))
                    await asyncio.sleep(self.One_Frame_time_out *100)
            """
            # 补发 超时帧
            for k in self.send_bufs:
                # 选择出 所有已发的 数据帧
                ids = list(self.send_bufs[k].keys())
                ids.sort()
                for i in ids:
                    if i not in self.send_bufs[k].keys():
                        continue
                    if self.send_bufs[k][i][1] == 0:
                        continue
                    # 超时是一种重发的 启动信号，由于 串口缓冲区的存在，
                    # 超时只能是很严重的情况下才能成立
                    # 而且这样的超时，应该是 硬件链路 有着严重误码，才可能出现
                    # 既然 误码 程度如此之大，其通信 机制就不是靠 这样的数据帧 设计结构 来解决了
                    # 帧长 设定为 0xAA，有不必要的开销，如果能更长一些，当然开销要小很多
                    # 不过 对误码的 处理就更复杂了
                    if time.time() > self.send_bufs[k][i][1] :
                    #or (i != ids[-1] and (i +1 not in self.send_bufs[k].keys())):
                        buf = self.send_bufs[k][i][0]
                        oStr = assemble_Frame(k, i, buf)
                        l = self.com.write(oStr)
                        # Windows 10 系统分配的串口发送缓冲区大小为 4KB
                        # 表面上 write 是阻塞方式，实际上只是写缓冲区
                        time_out = self.confirm_timeout_const + time.time()
                        self.send_bufs[k][i][1] = time_out
                        #print ("Send Missing Frame com.write", l, end="")
                        print (self.call_name, "Send Missing Frame com.write", i, l, len(ids))
                        #print_hex(oStr, "-")
                        # 若有重发帧，才等待 一下，以使 Com_read 有机会读数据
                        await asyncio.sleep(self.One_Frame_time_out *100)
                    break
                #break
            """
            # 3、发送一个未发的数据帧
            for k in self.send_bufs:
                ids = list(self.send_bufs[k].keys())
                #print (ids)
                ids = [i for i in ids if self.send_bufs[k][i][1] == False]
                # 把未发送的数据找出来
                if len(ids) > 0:
                    ids.sort()
                    buf = self.send_bufs[k][ids[0]][0]
                    oStr = assemble_Frame(k, ids[0], buf)
                    l = self.com.write(oStr)
                    #print (self.call_name, "Send Normal Frame com.write", ids[0], l)
                    #print_hex(oStr, "-")
                    time_out = self.confirm_timeout_const + time.time()
                    self.send_bufs[k][ids[0]][1] = time_out # 超时的时间点
                break
                # 只发一个正常的数据帧
            await asyncio.sleep(0.1)
            # 把cpu 还给事件循环

    async def Com_Read(self):
        """
        内置 的读串口方法
        需要用 task 来启动
        """
        print (self.call_name, "Com_Read Enter.")
        #await asyncio.sleep(0.1)
        d = b""
        while True:
            if self.Stop_Sign:
                break
            while self.com.in_waiting == 0:
                # 没有数据缓冲，可以切换出去了
                await asyncio.sleep(0.1)
            in_buf = self.com.read(self.com.in_waiting)
            #print (self.call_name, "Com_Read from Buf", end="")
            #print_hex (in_buf, "-")
            d += in_buf
            #print (self.call_name, " Com_Work_Read d +++++++++++++ ", end="")
            #print_hex (d, "-")            
            # 找 第一个 55 AA 帧同步字
            # 55 AA LL XX YY ZZ LL ... CC CC AA
            match = re.search(b"\x55\xAA", d)
            if match and len(d) >= match.start()+7:
                i = match.start()
                frame_head = d[i:i +7]
                f_len = 0
                f_len1, tgA, tgB, f_idx, f_len2 = struct.unpack ("=xxBBBBB", frame_head)
                #print ("f_len1, tgA, tgB, f_idx, f_len2", f_len1, tgA, tgB, f_idx, f_len2, len(d[i:]))

                if f_len1 == 0xAA and f_len2 == 0xAA:
                    # 接收确认帧
                    # 55 AA AA XX YY ZZ AA CC CC AA
                    # 确认帧如果出错的话，通信就岌岌可危了
                    c1 = calc_crc16 (d[i: i +7])
                    cc1, = struct.unpack("=H", d[i +7:i +7+2])
                    #print (self.call_name, "Recv Confirm Frame Test ", tgA, tgB, f_idx)
                    if c1 == cc1:
                        # 接收确认帧 无误，处理发送缓冲
                        if (tgA,tgB) in self.send_bufs:
                            if f_idx in self.send_bufs[(tgA,tgB)]:
                                del self.send_bufs[(tgA,tgB)][f_idx]
                                #print (self.call_name, "Recv Confirm Frame", tgA, tgB, f_idx)
                                # 找出 未收到的帧序号，小于 确认帧 序号的，放到补发帧里
                                if f_idx in self.left_bufs[(tgA,tgB)]:
                                    del self.left_bufs[(tgA,tgB)][f_idx]
                                left_idxs = list(self.send_bufs[(tgA,tgB)].keys())
                                left_idxs = [i for i in left_idxs if i < f_idx]
                                for idx in left_idxs:
                                    self.left_bufs[(tgA,tgB)][idx] = self.send_bufs[(tgA,tgB)][idx]
                    d = d[i +7 +3:]
                elif f_len1 +10 <= len(d[i:]) and f_len2 +10 <= len(d[i:]):
                    if f_len1 != f_len2:
                        # 有一种特殊情况，LL 帧长度出现误码怎么办？
                        # 解决方案，发 两次 帧长， 万一 两次帧长不一致，先通过校验码确定哪个帧长是正确的
                        # 如果都对不上，就 放弃掉这个企图，再通过 55 AA 的同步字，找下一帧。                    
                        c1 = calc_crc16 (d[i:i +f_len1 +7])
                        c2 = calc_crc16 (d[i:i +f_len2 +7])
                        cc1, = struct.unpack("=H", d[i +7 +f_len1:i +7 +f_len1+2])
                        cc2, = struct.unpack("=H", d[i +7 +f_len2:i +7 +f_len2+2])
                        if c1 == cc1:
                            f_len = f_len1
                        if c2 == cc2:
                            f_len = f_len2
                    else:
                        c1 = calc_crc16 (d[i:i +7 +f_len1])
                        cc1, = struct.unpack("=H", d[i +7 +f_len1:i +7 +f_len1+2])
                        if c1 == cc1:
                            f_len = f_len1
                    #print (self.call_name, "CRC confirm", f_len)
                    if f_len > 0 and f_len != 0xAA:
                        # crc校验成功，写输出数据
                        if (tgA,tgB) not in self.recv_bufs:
                            self.recv_bufs[(tgA,tgB)] = {}
                        if f_idx not in self.recv_bufs[(tgA,tgB)]:
                            # 已经接收过了，就不再重写了，对付 不必要的 补发帧
                            if f_idx == 0:
                                # 先要 知道 这个数据包，包括了多少个数据帧，
                                # 这样在收到了足够的帧后，才可以通过Read调用把整个数据包交出去
                                f_idx_len, f_buf_len, = struct.unpack("=BH", d[i +7: i +7 +3])
                                self.recv_idx[(tgA,tgB)] = [f_idx_len, f_buf_len]
                                #print ("f_idx_len, f_buf_len ", f_idx_len, f_buf_len)
                                self.recv_bufs[(tgA,tgB)][f_idx] = d[i +7 +3: i +7 +f_len]
                            else:
                                self.recv_bufs[(tgA,tgB)][f_idx] = d[i +7: i +7 +f_len]
                            # 返回 确认帧
                            # 55 AA AA XX YY ZZ AA CC CC AA
                            confirm_Str = b"\x55\xAA\xAA" 
                            confirm_Str += struct.pack("=BBB", tgA, tgB, f_idx) + b"\xAA"
                            cc1 = calc_crc16(confirm_Str)
                            confirm_Str += struct.pack("=H", cc1) + b"\xAA"
                            self.confirm_Queue.put (confirm_Str)
                            #self.confirm_Queue.put (confirm_Str)
                            #print (self.call_name, "Recv Right ", tgA, tgB, f_idx)
                        d = d[i +7 +f_len +3:]
                        # 确认帧 要连发两次
                    else:
                        print (self.call_name, "Recv Fail  ", tgA, tgB, f_idx)
                        # 没有找到，只好跳过这个帧头，继续找下一帧了
                        d = d[i +7:]
            # await asyncio.sleep(0.1)
            # 让出协程

import os

async def main():
    #baud_rate = 600
    #baud_rate = 115200
    baud_rate = 921600
    baud_rate = 6000000
    C1 = serial_verify("COM4", baud_rate, "AA")
    C2 = serial_verify("COM3", baud_rate, "BB")

    C1.Start()
    C2.Start()

    b1 = os.urandom(8*1024)
    #b1 = os.urandom(64)
    print_hex (b1[:10], "-")
    await C1.write(0, 0, b1)
    k, b2 = await C2.read(block=True)
    C1.Stop_Sign = True
    C2.Stop_Sign = True
    print (b1[:50])
    print (b2[:50])
    if b1 == b2:
        print ("OK") 
    else:
        print ("Fail")
    await asyncio.sleep(1)

async def send_main(baud_rate, com_port, stop_sign, buf):
    C1 = serial_verify(com_port, baud_rate, "AA")
    C1.Start()
    await C1.write(0, 0, buf)
    while stop_sign.value == 0:
        await asyncio.sleep(1)
    C1.Stop_Sign = True
    await asyncio.sleep(2)

def send(baud_rate, com_port, stop_sign, buf):
    asyncio.run(send_main(baud_rate, com_port, stop_sign, buf))
    
async def recv_main(baud_rate, com_port, stop_sign):
    C2 = serial_verify(com_port, baud_rate, "BB")
    C2.Start()
    k, b2 = await C2.read(block=True)
    print (b2[:30])
    stop_sign.value = 1
    C2.Stop_Sign = True
    await asyncio.sleep(2)
    return b2

def recv(baud_rate, com_port, stop_sign, ret_Q):
    ret = asyncio.run(recv_main(baud_rate, com_port, stop_sign))
    print (ret[:30])
    ret_Q.put(ret)

def multiP_main():
    #baud_rate = 115200
    #baud_rate = 460800
    baud_rate = 2000000
    #baud_rate = 6000000
    stop_sign = multiprocessing.Manager().Value("i", 0)
    #stop_sign = multiprocessing.RLock()
    ret_Q = multiprocessing.Queue()
    b1 = os.urandom(8*1024)

    send_proc = multiprocessing.Process(target=send, \
                    args=(baud_rate, "COM3", stop_sign, b1,),daemon=True)
    recv_proc = multiprocessing.Process(target=recv, \
                    args=(baud_rate, "COM8", stop_sign,ret_Q),daemon=True)
    recv_proc.start()
    send_proc.start()
    #recv_proc.join()
    #send_proc.join()

    b2 = ret_Q.get(block=True)
    if b1 == b2:
        print ("OK") 
    else:
        print ("Fail")

if __name__ == "__main__":
    #asyncio.run(main())
    multiP_main()
    #t = timeit.timeit(multiP_main, number=5)
    #print ("sum_time", t, t/5.0)