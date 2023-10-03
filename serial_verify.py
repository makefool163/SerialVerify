# -*- coding: utf-8 -*-
from __future__ import absolute_import, print_function

import asyncio
import struct
import re
import queue
import asyncio
import serial
import serial_asyncio
import time
import os

import crcmod
import multiprocessing

os.environ['PYTHONASYNCIODEBUG'] = '1'

def print_hex(buf, f="-"):
    # 把输入的 bytes 打印成 16进制形式
    hex = ''
    for b in buf:
        hex += f + format(b, '02x')
    return hex

crc32_func = crcmod.mkCrcFun(0x104c11db7, initCrc=0, xorOut=0xFFFFFFFF)

class serial_verify:
    """
    Com_Write, Com_Read 两个方法是 async 协程
    必须使用 create_task 启动，串口读写机制才能工作
    类里已经提供了Start方法用于进行协程启动

    Write、Read 两个方法 提供外部访问本对象
    注意调用 Write、Read 的方法必须使用 await 调用
    因为 Write、Read 两个方法都有 协程阻塞的特性
    """
    def __init__(self, com_port, baud_rate, call_name = "default"):
        self.confirm_timeout_const = 200 * (10.0 / baud_rate)
        self.One_Frame_time_out = 0xAA * 10.0 / baud_rate
        # 在write 方法中，会把全部数据发完的时间 重新设置为 超时时间
        self.call_name = call_name
        self.baud_rate = baud_rate
        self.com_port = com_port

        self.recv_bufs  = {}
        #self.confirm_Queue = queue.Queue()
        # 确认帧，不可能重发，如果确认帧都不能保证正确，通信效果就岌岌可危了
        self.send_Queue = queue.Queue()
        self.recv_Queue = asyncio.queues.Queue()
        self.Stop_Sign = False
        self.write_lock = asyncio.Lock()
        self.watch_dog = time.time()

    async def OpenComm(self):
        return await \
            serial_asyncio.open_serial_connection(url=self.com_port,
                                                  baudrate=self.baud_rate,
                                                  rtscts=True,
                                                  parity=serial.PARITY_EVEN,
                                                  bytesize=serial.EIGHTBITS
                                                  )

    async def Start(self):
        self.com_reader, self.com_writer = await self.OpenComm()
        self.watch_dog_task = asyncio.create_task(self.Watch_Dog())
        self.com_read_task  = asyncio.create_task(self.Com_Read())
        self.com_write_task = asyncio.create_task(self.Com_Write())

    def Stop(self):
        try:
            self.com_write_task.cancel()
            self.com_read_task.cancel()
            self.watch_dog_task.cancel()
            #self.com_reader.close()
            self.com_writer.close()
        except Exception as e:
            print ("Stop Error ", e)

    def __del__(self):
        self.Stop()
        #self.com_reader.close()
        self.com_writer.close()

    async def Watch_Dog(self):
        """
        发现串口有一端时间（40s）没有收到正确的数据，则重启串口
        (1024 + 12)*10 /600 = 17.266666666666666        
        """
        dog_sleep_time = 120
        while True:
            await asyncio.sleep(dog_sleep_time)
            if time.time() - self.watch_dog > dog_sleep_time:
                print ("WATCH DOG ! WATCH DOG !!!")
                self.com_reader.close()
                self.com_reader, self.com_writer = await self.OpenComm()

    async def write(self, buf):
        """
        写串口调用
        如果串口正忙的话，该调用会被阻塞直到允许数据发送
        """
        await self.write_lock.acquire()
        # 把 输入 组合成 待发送的 数据帧
        idx = 0
        buf_len = len(buf)
        send_bufs = []
        #print (self.call_name, "serial_verify class write")
        while len(buf) > 0:
            if idx == 0:
                send_bufs.append([buf[:1024 - 3], idx, 0])
                # 第一帧的 第  1 个字节为 帧数
                # 第一帧的 第2、3个字节为 总长度
                buf = buf[1024 - 3:]
            else:
                send_bufs.append([buf[:1024    ], idx, 0])
                # 前一个元素是 数据 本身，后一个 用来标识 是否已发送过
                buf = buf[1024:]
            idx += 1
        self.confirm_timeout_const = 10 * 1024 * 12.0 / self.baud_rate
        f_head = struct.pack("=BH", idx, buf_len)
        send_bufs[0][0] = f_head +send_bufs[0][0]

        for s in send_bufs:
            self.send_Queue.put(s)
            # 队列的元素： 原始数据,帧序号,超时时间
            # 超时时间，用来延时，使接收方能有时间完成接收动作，并返回确认信号
        #print (self.call_name, "serial_verify class write", idx, buf_len)

    async def read(self):
        """
        读串口调用
        将阻塞直至有返回值
        """
        return (await self.recv_Queue.get())

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
            #print (self.call_name, "asm frame", f_len, i)
            oStr += buf
            cc = crc32_func(oStr)
            oStr += struct.pack("=I", cc)
            oStr += b"\xAA\xAA"
            return oStr
        # 总体的发送优先级
        # 1、接受确认帧
        # 2、数据帧（正常、补发），从send_Queue队列中取
        print (self.call_name, "Com_Write Enter.")
        #await asyncio.sleep(0)
        while True:
            #print (self.call_name,"w",end=" ",flush=True)
            if self.Stop_Sign:
                break

            # 1、接受确认帧
            """
            print ("Com_Write confirm write", self.confirm_Queue.qsize())
            while True:
                try:
                    buf = self.confirm_Queue.get(block=False)
                    self.com_writer.write(buf)
                    await self.com_writer.drain()
                    print ("Send Confirm Frame", hex(buf[5]))
                except queue.Empty:
                    break
            """

            # 2、发送数据帧（按队列顺序）
            #print (self.call_name, "Send Normal Frame com.write")
            try:
                buf, idx, time_out = self.send_Queue.get(block=False)
                time_out = self.confirm_timeout_const
                self.send_Queue.put([buf, idx, time_out])
                oStr = assemble_Frame(idx, buf)
                self.com_writer.write(oStr)
                await asyncio.sleep(time_out)
                await self.com_writer.drain()

                #print (self.call_name, "Send Frame", idx, len(buf),
                #       print_hex(buf[:10]),print_hex(oStr[-4:]))
            except queue.Empty:
                await asyncio.sleep(0)

    async def Com_Read(self):
        """
        内置 的读串口方法
        需要用 task 来启动
        # 55 55 55 L1 L2 ZZ ... C1 C2 C3 C4 AA AA
        """
        print (self.call_name, "Com_Read Enter.")
        d = b""
        while True:
            #print (self.call_name,"r1", end=" ",flush=True)
            if self.Stop_Sign:
                break
            in_buf = await self.com_reader.readuntil(separator=b"\xAA\xAA")
            
            #print (self.call_name, "Com_Read from Buf", end="")
            #print_hex (in_buf, "-")
            d += in_buf
            #print (self.call_name, " Com_Work_Read d +++++++++++++ ", len(d), end=" ")
            #print (print_hex (d[:10]), print_hex (d[-10:]))
            
            # 找 第一个 55 AA 帧同步字
            # 55 55 55 L1 L2 ZZ ... C1 C2 C3 C4 AA AA
            #print (self.call_name,"r1-2", end=" ",flush=True)
            match = re.search(b"\x55\x55\x55", d)
            if match and len(d) >= match.start() +8:
                #print (self.call_name,"r2", end=" ",flush=True)
                #print (f"=={self.call_name[0]}==", end="",flush=True)
                self.watch_dog = time.time()
                # 喂狗
                i = match.start()
                frame_head = d[i:i +6]
                f_len, f_idx = struct.unpack ("=xxxHB", frame_head)
                #print (self.call_name, "f_len, f_idx ", f_len, f_idx, len(d[i:]), i, print_hex (d[i:i +8]))
                #print_hex (d[i:i +8])

                if d[i +3: i +5] == b"\xAA\xAA" \
                and len(d) >= i +8 \
                and d[i +6: i +8] == b"\xAA\xAA":
                    # 接收确认帧
                    # 55 55 55 AA AA ZZ AA AA
                    # 确认帧如果出错的话，通信就岌岌可危了
                    # 接收确认帧 无误，处理发送缓冲
                    """
                    # 这一段使老代码，用字典方式处理写缓冲
                    if f_idx in self.send_bufs:
                        del self.send_bufs[f_idx]
                    """
                    #print (self.call_name,"r3", end=" ",flush=True)
                    # 新的，用asyncio队列方式处理写缓存
                    send_list = list(self.send_Queue.queue)
                    new_list = [s for s in send_list if s[1] != f_idx]
                    self.send_Queue.queue.clear()
                    for s in new_list:
                        self.send_Queue.put(s)
                    if self.send_Queue.empty():
                        # 全部接收完成了，解开 写入 锁，开始接收新的输入
                        self.write_lock.release()
                    #print (self.call_name, "Recv Confirm Frame ", f_idx, len(self.recv_bufs))
                    d = d[i +8:]

                elif f_len +12 <= len(d[i:]) :
                    #print (self.call_name,"r4", end=" ",flush=True)
                    # 55 55 55 L1 L2 ZZ ... C1 C2 C3 C4 AA AA
                    c1 = crc32_func (d[i: i +6 +f_len])
                    #print (f_len, print_hex(d[i +6 +f_len:i +6 +f_len +4],"-"))
                    cc1, = struct.unpack("=I", d[i +6 +f_len:i +6 +f_len +4])
                    #print (self.call_name, "Test CRC")
                    # 接受 数据帧
                    if c1 == cc1:
                        #print (self.call_name, "Test CRC Success")
                        # crc校验成功，写输出数据
                        # 返回 确认帧
                        # 55 55 55 AA AA ZZ AA AA
                        confirm_Str = b"\x55\x55\x55\xAA\xAA" 
                        confirm_Str += struct.pack("=B", f_idx) + b"\xAA\xAA"
                        self.com_writer.write(confirm_Str)
                        await self.com_writer.drain()
                        
                        #print (self.call_name, "Recv Right ", f_idx)
                        if f_idx not in self.recv_bufs:
                            # 已经接收过了，就不再重写了，对付 不必要的 补发帧
                            self.recv_bufs[f_idx] = d[i +6: i +6 +f_len]
                        # 此处要检查一下 self.recv_bufs 是不是全部接收完了
                        # 若已接收完成，则要准备收下一组数据，否则下一组数据就会
                        # 把已接收的数据冲毁
                        if 0 in self.recv_bufs:
                            f_idx_len, f_buf_len = \
                                struct.unpack("=BH",self.recv_bufs[0][:3])
                            if len(self.recv_bufs) == f_idx_len:                                
                                ids = list(self.recv_bufs.keys())
                                ids.sort()
                                oStr = b""
                                for i in ids:
                                    if i == 0:
                                        oStr = self.recv_bufs[i][3:]
                                    else:
                                        oStr += self.recv_bufs[i]
                                    del self.recv_bufs[i]
                                #print (self.call_name, "Read All off", len(oStr), f_buf_len, print_hex(oStr[:5]), oStr[:40])
                                await self.recv_Queue.put(oStr)
                                #print (self.call_name, "recv_Queue.put", len(oStr))
                        d = d[i +12 +f_len:]
                    else:
                        #print (self.call_name,"r5", end=" ",flush=True)
                        #print (self.call_name, "Recv Fail  ", f_idx)
                        # 没有找到，只好跳过这个帧头，继续找下一帧了
                        d = d[i +6:]

import os

async def main():
    #baud_rate = 600
    baud_rate = 115200
    #baud_rate = 921600
    #baud_rate = 6000000
    C1 = serial_verify("COM5", baud_rate, "AA")
    C2 = serial_verify("COM6", baud_rate, "BB")

    await C1.Start()
    await C2.Start()

    b1 = os.urandom(8*1024)
    #b1 = os.urandom(64)
    print_hex (b1[:10], "-")
    await C1.write(b1)
    b2 = await C2.read()
    await asyncio.sleep(10)
    C1.Stop_Sign = True
    C2.Stop_Sign = True
    print (b1[:50])
    print (b2[:50])
    if b1 == b2:
        print ("OK") 
    else:
        print ("Fail")
    await asyncio.sleep(1)

async def main1(baud_rate, com_port, stop_sign, buf):
    C1 = serial_verify(com_port, baud_rate, "AA")
    await C1.Start()
    await C1.write(buf)
    await C1.write(buf)
    b1 = await C1.read()
    b1 = await C1.read()
    print ("C1", len(b1), b1[:30])
    stop_sign.value = stop_sign.value +1

    while stop_sign.value < 2:
        await asyncio.sleep(0)

    C1.Stop_Sign = True
    await asyncio.sleep(2)
    return b1

def C1(baud_rate, com_port, stop_sign, buf, ret_Q):
    ret = asyncio.run(main1(baud_rate, com_port, stop_sign, buf))
    ret_Q.put(("C1", ret))
    
async def main2(baud_rate, com_port, stop_sign, buf):
    C2 = serial_verify(com_port, baud_rate, "BB")
    await C2.Start()
    await C2.write(buf)
    await C2.write(buf)
    b2 = await C2.read()
    b2 = await C2.read()
    print ("C2", len(b2), b2[:30])
    stop_sign.value = stop_sign.value +1

    while stop_sign.value < 2:
        await asyncio.sleep(0)

    C2.Stop_Sign = True
    await asyncio.sleep(2)
    return b2

def C2(baud_rate, com_port, stop_sign, buf, ret_Q):
    ret = asyncio.run(main2(baud_rate, com_port, stop_sign, buf))
    print (ret[:30])
    ret_Q.put(("C2",ret))

def multiP_main():
    baud_rate = 1200
    baud_rate = 9600
    baud_rate = 115200
    #baud_rate = 460800
    #baud_rate = 1000000
    #baud_rate = 6000000
    stop_sign = multiprocessing.Manager().Value("i", 0)
    #stop_sign = multiprocessing.RLock()
    ret_Q = multiprocessing.Queue()
    #b1 = os.urandom(8*1024)
    b1 = os.urandom(1000*8)
    b2 = os.urandom(2000*4)

    C1_proc = multiprocessing.Process(target=C1, \
                    args=(baud_rate, "COM5", stop_sign, b1, ret_Q),daemon=True)
    C2_proc = multiprocessing.Process(target=C2, \
                    args=(baud_rate, "COM6", stop_sign, b2, ret_Q),daemon=True)
    C1_proc.start()
    C2_proc.start()
    #recv_proc.join()
    #send_proc.join()

    z1, d1 = ret_Q.get()
    z2, d2 = ret_Q.get()
    if z1 == "C1":
        d1, d2 = d2, d1
    if b1 == d1 and b2 == d2:
        print ("OK") 
    else:
        print ("Fail")

if __name__ == "__main__":
    #asyncio.run(main())
    multiP_main()
    """
    N = 1
    t = timeit.timeit(multiP_main, number=N)
    #t = timeit.timeit(asyncio.run(main()), number=N)
    print ("sum_time", t, t/N)
    """