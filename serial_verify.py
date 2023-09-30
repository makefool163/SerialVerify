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

def print_hex(buf, f="-"):
    # 把输入的 bytes 打印成 16进制形式
    hex = ''
    for b in buf:
        hex += f + format(b, '02x')
    return hex

crc32_func = crcmod.mkCrcFun(0x104c11db7, initCrc=0, xorOut=0xFFFFFFFF)

async def async_sleep():
    await asyncio.sleep(0.1)

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
        self.recv_Queue = asyncio.queues.Queue()
        self.Stop_Sign = False
        self.write_lock = asyncio.Lock()
        self.read_lock = asyncio.Lock()
        self.watch_dog = time.time()

    def Start(self):
        self.watch_dog_task = asyncio.create_task(self.Watch_Dog())
        self.com_write_task = asyncio.create_task(self.Com_Write())
        self.com_read_task  = asyncio.create_task(self.Com_Read())

    def Stop(self):
        try:
            self.watch_dog_task.cancel()
            self.com_write_task.cancel()
            self.com_read_task.cancel()
            self.com.close()
        except Exception as e:
            print ("Stop Error ", e)

    def __del__(self):
        self.Stop()
        self.com.close()

    async def Watch_Dog(self):
        """
        发现串口有一端时间（40s）没有收到正确的数据，则重启串口
        (1024 + 12)*10 /600 = 17.266666666666666        
        """
        dog_sleep_time = 40
        while True:
            await asyncio.sleep(dog_sleep_time)
            if time.time() - self.watch_dog > dog_sleep_time:
                print ("WATCH DOG ! WATCH DOG !!!")
                self.com.close()
                self.com.open()

    async def write(self, buf):
        """
        写串口调用
        如果串口正忙的话，该调用会被阻塞直到允许数据发送
        """
        async with self.write_lock:
            #print (self.call_name, "serial_verify class write", buf)
            while len(self.send_bufs) > 0:
                # 前面的数据还没有处理完成，就不执行下面的，保持阻塞状态
                await asyncio.sleep(0.1)
                #asyncio.run(async_sleep())
            # 把 输入 组合成 待发送的 数据帧
            idx = 0
            buf_len = len(buf)
            while len(buf) > 0:
                if idx == 0:
                    self.send_bufs[idx] = [buf[:1024 - 3], 0]
                    # 第一帧的 第  1 个字节为总帧数
                    # 第一帧的 第2、3个字节为总帧数
                    buf = buf[1024 - 3:]
                else:
                    self.send_bufs[idx] = [buf[:1024], 0]
                    # 前一个元素是 数据 本身，后一个 用来标识 是否已发送过
                    buf = buf[1024:]
                idx += 1
            self.send_bufs_len = idx
            self.confirm_timeout_const = 100 * idx * 1024 * 10.0 / self.baud_rate
            #self.confirm_timeout_const = 0.8
            f_head = struct.pack("=BH", idx, buf_len)
            print (self.call_name, "serial_verify class write", idx, buf_len)
            self.send_bufs[0][0] = f_head +self.send_bufs[0][0]
            #print ("serial_verify write F_idx", idx)
            #print ("sver class write", buf)

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
        # 2、补发数据帧
        # 3、正常数据帧
        #print (self.call_name, "Com_Write Enter.")
        #await asyncio.sleep(0.1)
        while True:
            if self.Stop_Sign:
                break
            # 把cpu 还给事件循环
            await asyncio.sleep(0)

            # 1、接受确认帧
            #print ("Com_Write confirm write")
            while True:
                try:
                    buf = self.confirm_Queue.get(block=False)
                    self.com.write(buf)
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
                print (self.call_name, "Send Missing Frame re_send com.write", i, l, len(re_send))
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
                    print (self.call_name, "Send Missing Frame timeout com.write", i, l, len(k))
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
                print (self.call_name, "Send Normal Frame com.write", ids[0], l)
                #print_hex(oStr, "-")
                time_out = self.confirm_timeout_const + time.time()
                self.send_bufs[ids[0]][1] = time_out # 超时的时间点
            # 只发一个正常的数据帧

    async def Com_Read(self):
        """
        内置 的读串口方法
        需要用 task 来启动
        # 55 55 55 L1 L2 ZZ ... C1 C2 C3 C4 AA AA
        """
        d = b""
        while True:
            if self.Stop_Sign:
                break
            while self.com.in_waiting == 0:
                # 没有数据缓冲，可以切换出去了
                await asyncio.sleep(0)
            in_buf = self.com.read(self.com.in_waiting)
            #print (self.call_name, "Com_Read from Buf", end="")
            #print_hex (in_buf, "-")
            d += in_buf
            #print (self.call_name, " Com_Work_Read d +++++++++++++ ", print_hex (d[-20:]))
            # 找 第一个 55 AA 帧同步字
            # 55 55 55 L1 L2 ZZ ... C1 C2 C3 C4 AA AA
            match = re.search(b"\x55\x55\x55", d)
            if match and len(d) >= match.start() +8:
                self.watch_dog = time.time()
                # 喂狗                
                i = match.start()
                frame_head = d[i:i +6]
                f_len = 0
                f_len, f_idx = struct.unpack ("=xxxHB", frame_head)
                #print (self.call_name, "f_len, f_idx ", f_len, f_idx, len(d[i:]), i, print_hex (d[i:i +8]))
                #print_hex (d[i:i +8])

                if f_len == 0xAAAA \
                and len(d) >= i +8 \
                and d[i +6: i +8] == b"\xAA\xAA":
                    # 接收确认帧
                    # 55 55 55 AA AA ZZ AA AA
                    # 确认帧如果出错的话，通信就岌岌可危了
                    print (self.call_name, "Recv Confirm Frame ", f_idx)
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
                            print (self.call_name, "Recv Right ", f_idx)
                            # 此处要检查一下 self.recv_bufs 是不是全部接收完了
                            # 若已接收完成，则要准备收下一组数据，否则下一组数据就会
                            # 把已接收的数据冲毁
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
                                    await self.recv_Queue.put(oStr)
                        d = d[i +12 +f_len: ]
                    else:
                        print (self.call_name, "Recv Fail  ", f_idx)
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

    C1.Start()
    C2.Start()

    b1 = os.urandom(8*1024)
    #b1 = os.urandom(64)
    print_hex (b1[:10], "-")
    await C1.write(b1)
    b2 = await C2.read()
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
    C1.Start()
    await C1.write(buf)
    b1 = await C1.read()
    print ("C1", len(b1), b1[:30])
    stop_sign.value = stop_sign.value +1

    while stop_sign.value < 2:
        await asyncio.sleep(1)

    C1.Stop_Sign = True
    await asyncio.sleep(2)
    return b1

def C1(baud_rate, com_port, stop_sign, buf, ret_Q):
    ret = asyncio.run(main1(baud_rate, com_port, stop_sign, buf))
    ret_Q.put(("C1", ret))
    
async def main2(baud_rate, com_port, stop_sign, buf):
    C2 = serial_verify(com_port, baud_rate, "BB")
    C2.Start()
    await C2.write(buf)
    b2 = await C2.read()
    print ("C2", len(b2), b2[:30])
    stop_sign.value = stop_sign.value +1

    while stop_sign.value < 2:
        await asyncio.sleep(1)

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
    #baud_rate = 115200
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
    #multiP_main()
    N = 1
    #t = timeit.timeit(multiP_main, number=N)
    t = timeit.timeit(asyncio.run(main()), number=N)
    print ("sum_time", t, t/N)