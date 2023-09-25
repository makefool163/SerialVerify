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

crc32_func = crcmod.mkCrcFun(0x104c11db7, initCrc=0, xorOut=0xFFFFFFFF)

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
        self.com_port = com_port
        self.com = serial.Serial(port=self.com_port, 
                                timeout = 0, 
                                # non-blocking mode, return immediately in any case, 
                                # returning zero or more, up to the requested number of bytes
                                baudrate=self.baud_rate,
                                rtscts=True,
                                #dsrdtr=True,
                                # We use hardware stream control here
                                parity=serial.PARITY_EVEN,
                                bytesize=serial.EIGHTBITS)
        self.confirm_Queue = queue.Queue()
        # 确认帧，不可能重发，如果确认帧都不能保证正确，通信效果就岌岌可危了
        self.send_bufs = {}
        self.send_bufs_len = 0
        self.recv_bufs  = {}
        self.Stop_Sign = False

    def Start(self):
        asyncio.create_task(self.Com_Write())
        asyncio.create_task(self.Com_Read())

    def __del__(self):
        self.com.close()

    async def write(self, buf):
        """
        写串口调用
        如果串口正忙的话，该调用会被阻塞直到允许数据发送
        """
        while len(self.send_bufs) > 0:
            # 前面的数据还没有处理完成，就不执行下面的，保持阻塞状态
            await asyncio.sleep(0.1)
            #asyncio.run(async_sleep())
        # 把 输入 组合成 待发送的 数据帧
        idx = 0
        buf_len = len(buf)
        while len(buf) > 0:
            if idx == 0:
                self.send_bufs[idx] = [buf[:0x1024 - 3], 0]
                # 第一帧的 第  1 个字节为总帧数
                # 第一帧的 第2、3个字节为总帧数
                buf = buf[0x1024 - 3:]
            else:
                self.send_bufs[idx] = [buf[:0x1024], 0]
                # 前一个元素是 数据 本身，后一个 用来标识 是否已发送过
                buf = buf[0x1024:]
            idx += 1
        self.send_bufs_len = idx
        self.confirm_timeout_const = 100 * idx * 1024 * 10.0 / self.baud_rate
        #self.confirm_timeout_const = 0.8
        f_head = struct.pack("=BH", idx, buf_len)
        self.send_bufs[0][0] = f_head +self.send_bufs[0][0]
        #print ("serial_verify write F_idx", idx)

    async def read(self, block=True):
        """
        读串口调用
        选阻塞模式时，将阻塞直至有返回值
        非阻塞模式时，若返回值，则返回None
        返回值是((tagA, tagB), ret_data) 这样的形式
        如果串口已经收到了多组数据，本调用仅返回其中一组数据
        """
        def read_sub(self):
            #print ("self.recv_bufs", self.recv_bufs)
            #print ("                     self.recv_idx", self.recv_idx, len(self.recv_bufs[k]))
            if 0 in self.recv_bufs:
                f_idx_len, f_buf_len = \
                    struct.unpack("=BH",self.recv_bufs[0][:3])
                if len(self.recv_bufs) == f_idx_len:
                    #print ("serial_verify read already")
                    ids = list(self.recv_bufs.keys())
                    ids.sort()
                    oStr = b""
                    for i in ids:
                        if i == 0:
                            oStr = self.recv_bufs[i][3:]
                        else:
                            oStr += self.recv_bufs[i]
                    del self.recv_bufs[i]
                    return (oStr)
            return None
        ret = read_sub(self)
        while type(ret) == type(None) and block:
            ret = read_sub(self)
            await asyncio.sleep(0.1)
            # 用事件循环 阻塞一下
        return ret

    async def Com_Write(self):
        """
        内置 的写串口方法
        需要用 task 来启动
        pyserial 的write不是异步的，
        此处的异步是在等待输入数据（即有Write方法写入数据）时进行异步
        """
        def assemble_Frame(i, buf):
            # 55 55 55 L1 L2 ZZ ... C1 C2 C3 C4 AA AA
            f_len = len(buf)
            oStr  = b"\x55\x55\x55"
            oStr += struct.pack("=HB", f_len, i)
            oStr += buf
            cc = crc32_func(oStr)
            oStr += struct.pack("=I", cc)
            oStr += b"\xAA\xAA"
            return oStr
        # 总体的发送优先级
        # 1、接受确认帧
        # 2、补发数据帧
        # 3、正常数据帧
        #print (self.call_name, "Com_Write Enter.")
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
            not_send_ends = list(self.send_bufs.keys())
            had_sended =  list(set(range(self.send_bufs_len)) - set(not_send_ends))            
            re_send = [i for i in not_send_ends if any(i < j for j in had_sended)]
            for i in re_send:
                # 也许在 补发 的过程中，已经收到 确认帧了
                if i not in self.send_bufs.keys():
                    continue
                buf = self.send_bufs[i][0]
                oStr = assemble_Frame(i, buf)
                l = self.com.write(oStr)
                while self.com.out_waiting > 0:
                    await asyncio.sleep(0.01)
                # 也许在 补发 的过程中，已经收到 确认帧了
                if i not in self.send_bufs.keys():
                    continue
                time_out = self.confirm_timeout_const + time.time()
                self.send_bufs[i][1] = time_out
                print (self.call_name, "Send Missing Frame com.write", i, l, len(ids))
                #await asyncio.sleep(self.One_Frame_time_out *100)

            # 3、补发 超时帧
            # 选择出 所有已发的 数据帧
            k = list(self.send_bufs.keys())
            k.sort()
            # 因为是 协程，可能在 循环遍历 self.send_bufs.keys() 过程中，
            # send_bufs 可能就发送变化了，所以不能用 keys() 作为遍历对象
            for i in k:
                # 也许在 补发 的过程中，已经收到 确认帧了
                if i not in self.send_bufs.keys():
                    continue
                if self.send_bufs[i][1] == 0:
                    continue
                # 超时是一种重发的 启动信号，由于 串口缓冲区的存在，
                # 超时只能是很严重的情况下才能成立
                # 而且这样的超时，应该是 硬件链路 有着严重误码，才可能出现
                # 既然 误码 程度如此之大，其通信 机制就不是靠 这样的数据帧 设计结构 来解决了
                # 帧长 设定为 0xAA，有不必要的开销，如果能更长一些，当然开销要小很多
                # 不过 对误码的 处理就更复杂了
                if time.time() > self.send_bufs[i][1] :
                    buf = self.send_bufs[i][0]
                    oStr = assemble_Frame(i, buf)
                    l = self.com.write(oStr)
                    while self.com.out_waiting > 0:
                        await asyncio.sleep(0.01)
                    # Windows 10 系统分配的串口发送缓冲区大小为 4KB
                    # 表面上 write 是阻塞方式，实际上只是写缓冲区

                    # 也许在 补发 的过程中，已经收到 确认帧了
                    if i not in self.send_bufs.keys():
                        continue
                    time_out = self.confirm_timeout_const + time.time()
                    self.send_bufs[i][1] = time_out
                    #print ("Send Missing Frame com.write", l, end="")
                    print (self.call_name, "Send Missing Frame com.write", i, l, len(ids))
                    #print_hex(oStr, "-")

            # 4、发送一个未发的数据帧
            ids = list(self.send_bufs.keys())
            #print (ids)
            ids = [i for i in ids if self.send_bufs[i][1] == False]
            # 把未发送的数据找出来
            if len(ids) > 0:
                ids.sort()
                buf = self.send_bufs[ids[0]][0]
                oStr = assemble_Frame(ids[0], buf)
                l = self.com.write(oStr)
                while self.com.out_waiting > 0:
                    await asyncio.sleep(0.01)
                #print (self.call_name, "Send Normal Frame com.write", ids[0], l)
                #print_hex(oStr, "-")
                time_out = self.confirm_timeout_const + time.time()
                self.send_bufs[ids[0]][1] = time_out # 超时的时间点
            # 只发一个正常的数据帧
            await asyncio.sleep(0.1)
            # 把cpu 还给事件循环

    async def Com_Read(self):
        """
        内置 的读串口方法
        需要用 task 来启动
        # 55 55 55 L1 L2 ZZ ... C1 C2 C3 C4 AA AA
        """
        #print (self.call_name, "Com_Read Enter.")
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
            # 55 55 55 L1 L2 ZZ ... C1 C2 C3 C4 AA AA
            match = re.search(b"\x55\x55\x55", d)
            if match and len(d) >= match.start() +8:
                i = match.start()
                frame_head = d[i:i +6]
                f_len = 0
                f_len, f_idx = struct.unpack ("=xxxHB", frame_head)
                #print ("f_len, f_idx ", f_len, f_idx, len(d[i:]))

                if f_len == 0xAAAA \
                    and len(d) >= i +8 and d[i +6: i +8] != b"\xAA\xAA":
                    # 接收确认帧
                    # 55 55 55 AA AA ZZ AA AA
                    # 确认帧如果出错的话，通信就岌岌可危了
                    #print (self.call_name, "Recv Confirm Frame Test ", f_idx)
                    # 接收确认帧 无误，处理发送缓冲
                    if f_idx in self.send_bufs:
                        del self.send_bufs[f_idx]
                    d = d[i +8]
                elif f_len +12 <= len(d[i:]) :
                    # 55 55 55 L1 L2 ZZ ... C1 C2 C3 C4 AA AA
                    c1 = crc32_func (d[i: i +6 +f_len])
                    cc1, = struct.unpack("=I", d[i +6 +f_len:i +6 +f_len +4])
                    # 接受 数据帧
                    if c1 == cc1:
                        # crc校验成功，写输出数据
                        if f_idx not in self.recv_bufs:
                            # 已经接收过了，就不再重写了，对付 不必要的 补发帧
                            self.recv_bufs[f_idx] = d[i +6: i +6 +f_len]
                            # 返回 确认帧
                            # 55 55 55 AA AA ZZ AA AA
                            confirm_Str = b"\x55\x55\x55\xAA\xAA" 
                            confirm_Str += struct.pack("=B", f_idx) + b"\xAA\xAA"
                            self.confirm_Queue.put (confirm_Str)
                            #self.confirm_Queue.put (confirm_Str)
                            #print (self.call_name, "Recv Right ", tgA, tgB, f_idx)
                        d = d[i +12 +f_len: ]
                        # 确认帧 要连发两次
                    else:
                        print (self.call_name, "Recv Fail  ", f_idx)
                        # 没有找到，只好跳过这个帧头，继续找下一帧了
                        d = d[i +6:]

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
    await C1.write(b1)
    b2 = await C2.read(block=True)
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
    await C1.write(buf)
    while stop_sign.value == 0:
        await asyncio.sleep(1)
    C1.Stop_Sign = True
    await asyncio.sleep(2)

def send(baud_rate, com_port, stop_sign, buf):
    asyncio.run(send_main(baud_rate, com_port, stop_sign, buf))
    
async def recv_main(baud_rate, com_port, stop_sign):
    C2 = serial_verify(com_port, baud_rate, "BB")
    C2.Start()
    b2 = await C2.read(block=True)
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
    baud_rate = 9600
    baud_rate = 1200
    #baud_rate = 115200
    #baud_rate = 460800
    #baud_rate = 2000000
    #baud_rate = 6000000
    stop_sign = multiprocessing.Manager().Value("i", 0)
    #stop_sign = multiprocessing.RLock()
    ret_Q = multiprocessing.Queue()
    b1 = os.urandom(8*1024)

    send_proc = multiprocessing.Process(target=send, \
                    args=(baud_rate, "COM3", stop_sign, b1,),daemon=True)
    recv_proc = multiprocessing.Process(target=recv, \
                    args=(baud_rate, "COM4", stop_sign,ret_Q),daemon=True)
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
    #multiP_main()
    N = 1
    t = timeit.timeit(multiP_main, number=N)
    print ("sum_time", t, t/N)