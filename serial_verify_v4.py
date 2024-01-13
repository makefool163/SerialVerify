# -*- coding: utf-8 -*-
from __future__ import absolute_import, print_function

import asyncio
import serial
import serial_asyncio
import struct
import re
import queue
#import eventlet
#import signal
import time
import timeit
import datetime
#from eventlet import backdoor

if "eventlet" in globals():
    from eventlet.green import socket
else:
    import socket

import os
#os.environ['EVENTLET_DEBUG'] = 'True'

import crcmod
import multiprocessing
import eventlet.lock

"""
使用本模块，有一个强假设，通信的两端程序是同时发起的，同时发起前通道上没有任何数据。
同时发起后，两端完成握手后才能开始传输数据，这样可以避免数据无法同步。
保证了通信数据的完整性，避免上一级调用的数据丢失。
"""
class CircularInt:
    def __init__(self, length=256, value=0):        
        self.length = length
        self.value = value

    def inc(self, v=1):
        self.value = (self.value + v) % self.length

    def dec(self, v=1):
        self.value = (self.value - v) % self.length

    def distant(self, other):
        if other.__class__.__name__ != "CircularInt":
            raise "distant must CircularInt"
        diff = self.value - other.value
        if abs(diff) > self.length / 2:
            diff = (diff + self.length) % self.length - self.length
        return diff

    def compare(self, other):
        if other.__class__.__name__ != "CircularInt":
            raise "compare must CircularInt"
        if self.length != other.length:
            raise "compare CircularInt must same Length"
        return self.distant(other)

    def __str__(self):
        return f"CircularInt({self.length}, {self.value})"
    
    # 加减法 是 加减 某个常数，结果 是 CircularInt对象
    def __add__(self, other):
        ret = (self.value + other) % self.length
        return CircularInt(self.length, ret)

    def __sub__(self, other):
        ret = (self.value - other) % self.length
        return CircularInt(self.length, ret)

    # copy 、比较 都是 CircluarInt 的对象之间
    def copy(self):
        return CircularInt(self.length, self.value)
    
    def __eq__(self, other):
        if self.compare(other) == 0:
            return True
        else:
            return False
    
    def __gt__(self, other):
        if self.compare(other) > 0:
            return True
        else:
            return False
    
    def __lt__(self, other):
        if self.compare(other) < 0:
            return True
        else:
            return False
    
    def __ge__(self, other):
        if self.compare(other) >= 0:
            return True
        else:
            return False
        
    def __le__(self, other):
        if self.compare(other) <= 0:
            return True
        else:
            return False
        

class serial_extend_eventlet(serial.Serial):
    """
    因为异步化了 , 所以不再有timeout的定义
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._read_in_buf = b""
    async def read_until(self, separator = b"\n") -> bytes:
        """
        不能一个个的字符读取，否则太慢了，要有缓冲
        """
        while separator not in self._read_in_buf:
            while self.in_waiting == 0:
                #eventlet.sleep(0.02)
                await asyncio.sleep(0.02)
            self._read_in_buf += super().read(self.in_waiting)
        i = self._read_in_buf.index(separator)
        ret = self._read_in_buf[:i+len(separator)]
        self._read_in_buf = self._read_in_buf[i+len(separator):]
        return ret
    async def read(self, size: int = 1) -> bytes:
        while (len(self._read_in_buf)) < size:
            while self.in_waiting == 0:
                #eventlet.sleep(0.02)
                await asyncio.sleep(0.02)
            self._read_in_buf += super().read(self.in_waiting)
        ret = self._read_in_buf[:size]
        self._read_in_buf = self._read_in_buf[size:]
        return ret

def print_hex(buf, f="-"):
    # 把输入的 bytes 打印成 16进制形式
    hex = ''
    for b in buf:
        hex += f + format(b, '02x')
    return hex

# 定义一个256个元素的全0数组
reversal_crc32_table = [0 for x in range(0,256)]

def reversal_init_crc32_table():
    for i in range(256):
        c = i
        for _ in range(8):
            if (c & 0x00000001):
                c = (c >> 1) ^ 0xEDB88320
            else:
                c = c >> 1
        reversal_crc32_table[i] = c & 0xffffffff

def crc32_func(bytes_arr):
    length = len(bytes_arr)

    if bytes_arr != None:
        crc = 0xffffffff
        for i in range(0, length):
            crc = (crc >> 8) ^ reversal_crc32_table[ (bytes_arr[i] ^ crc) & 0xff ]
    else:
        crc = 0xffffffff

    crc = crc ^ 0xffffffff
    return crc

#crc32_func = crcmod.mkCrcFun(0x104c11db7, initCrc=0, xorOut=0xFFFFFFFF)

"""
校验重发机制：
1、发送端将数据包分出若干个数据帧。
2、按顺序发出所有的数据帧, 如果发送过程中, 没有收到错误提示, 则继续发。
   接收方收到所有数据帧后, 发送一个确认信号, 发送方即丢弃掉缓冲中的对应数据帧。
3、接收方发现有错误的数据帧 (CRC错误, 或者跳帧), 发送错误信号, 指明错误数据帧
   发送方根据错误信号, 重发对应数据帧, 重发的数据帧是否正确, 还是由接收方考虑
   接收方在发出错误信号后, 间隔了三个数据帧的接收后(或者对应的接收超时时间), 还没有看到重发帧, 就继续再发错误信号
   send:  0  1 /2  3  4  5  2
                    |         ?
   recv:  0  1     3  4  5  2 
   接收方要对每个数据帧的接收计时
4、极端情况, 数据大范围错误
   要告知上一级调用对象: 有大问题了, 你得自己看着办了
"""
class serial_verify:
    """
    Com_Write, Com_Read 两个方法是 协程
    必须使用 create_task 启动，串口读写机制才能工作
    类里已经提供了Start方法用于进行协程启动

    Write、Read 两个方法 提供外部访问本对象
    Write、Read 两个方法都有 协程阻塞的特性
    """
    def __init__(self, com_port, baud_rate, 
                 write_buf=3, 
                 stop_callback = None,
                 log_level=0, 
                 call_name = "default"):
        self.write_buf = write_buf
        self.stop_callback = stop_callback
        self.log_level = log_level
        self.call_name = call_name
        self.baud_rate = baud_rate
        self.com_port = com_port
        self.Base_Frame_Len = 1024
        self.One_Frame_recv_Time = self.Base_Frame_Len * 12.0 / self.baud_rate
        self.Find_recv_Omission_Proc_Start = False
        self.recv_Omission_Frame_Count = 0
        # 接收方：目前有多少个漏发帧

        self.read_out_Queue = asyncio.Queue()
        # 输出数据包队列，没有数据包，get方法就阻塞直至有数据包可读
        self.oMission_idxs_list = []
        self.oMission_notice_Queue = queue.Queue() # 通知信号
        self.oMission_Data_Queue = queue.Queue()   # 丢帧发送数据
        # 需纠错idx队列
        self.pkg_Done_Queue = queue.Queue()
        # 数据包正确确认队列

        self.write_semaphore = asyncio.Semaphore(self.write_buf)
        # 最多能容许 3 个数据包进来 写

        self.send_bufs = {}
        self.recv_bufs = {}
        #self.send_bufs[self.send_idx_bottom.value] = [pkg_idx, f_idx, len_f_idx, s]
        # 数据存在 send_bufs 中, 包序号[环形数], 帧序号, 总帧数, 负载数据
        
        self.send_idx_top = CircularInt(256)
        self.send_idx_bottom = CircularInt(256)
        # 发送缓冲的头尾位置
        self.recv_idx_top = CircularInt(256)
        # recv 无需尾位置
        # recv 的 top 是用来移除

        self.send_idx_now = CircularInt(256)
        self.recv_idx_now = CircularInt(256)
        # 发送、接收的当前位置

        self.pkg_idx = CircularInt(12)
        self.__isHandShake = False

    def __empty_queue(self, queue: asyncio.Queue):
        while not queue.empty():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    @property
    def isConnect (self):
        return self.__isHandShake
    
    def print_log (self,  *args, **kwargs):
        if self.log_level > 2:
            print (*args, **kwargs)
    
    async def __OpenComm(self):
        return await\
            serial_asyncio.open_serial_connection(url=self.com_port,
                                                  baudrate=self.baud_rate,
                                                  rtscts=True,
                                                  parity=serial.PARITY_NONE,
                                                  bytesize=serial.EIGHTBITS
                                                  )

    async def Start(self):
        """
        因为有握手过程，Start是阻塞方式的
        """
        self.com_reader, self.com_writer = await self.__OpenComm()
        #self.com = self.__OpenComm()
        print (self.call_name, "COM Open, Waitting Handshake", datetime.datetime.now())

        # 先等待 握手成功
        self.print_log (self.call_name, " Start Handshake ...")
        await self.__Handshake()
        #await self.com_reader.read(8*1024)
        # 握手成功后，先走完握手环节的，
        # 要等待后走完握手环节的2秒，以保证通道上的已经没有数据
        await asyncio.sleep(0.5)
        self.print_log (self.call_name, " Handshake Off ...")

        for i in range(256):
            self.send_bufs[i] = None
            self.recv_bufs[i] = None

        self.__empty_queue(self.read_out_Queue)
        self.gl_Com_Read  = asyncio.create_task(self.__Com_Read())
        self.gl_Com_Write = asyncio.create_task(self.__Com_Write())
        self.gl_Com_Notice_oMission = asyncio.create_task(self.__Notice_recv_Omission_Frame())

    async def __Handshake(self):
        d = b""
        while True:
            self.com_writer.write(b"\xAA\xAA\xAA\x00\x00\x00")
            d += await self.com_reader.read(8*1024) # 尽可能地读
            self.print_log (self.call_name, print_hex(d))
            if b"\xAA\xAA\xAA\x00\x00\x00" in d:
                # 找 同步符号
                for _ in range(10):
                    self.com_writer.write(b"\xAA\xAA\xAA\x00\x00\x00")
                    await asyncio.sleep(0.02)
                break
        # 写 握手确认
        self.com_writer.write(b"\xA5\xA5\xA5\x00\x00\x00")

        while True:
            d += await self.com_reader.read(8*1024) # 尽可能地读
            if b"\xA5\xA5\xA5\x00\x00\x00" in d:
                break
        print (self.call_name, "Recv Handshake.", datetime.datetime.now())
        self.__isHandShake = True

    async def Stop(self):
        if not self.__isHandShake:
            return
        self.__isHandShake = False

        self.gl_Com_Notice_oMission.cancel()
        self.gl_Com_Write.cancel()
        self.gl_Com_Read.cancel()

        self.__empty_queue(self.read_out_Queue)
        # 向外部调用的 读出 队列发一个空字符，说明串口已经失效，中断串口的使用
        await self.read_out_Queue.put(b"")
        # 向 对方发一个 串口停止的信号，要求对方也停止（因为重连要做handshake）
        # 55 55 55 A5 00 A5 00 AA AA
        self.com_writer.write(b"\x55\x55\x55\xA5\x00\xA5\x00\xAA\xAA")

        await asyncio.sleep(0.5)
        self.com_writer.close()

        if self.stop_callback is not None:
            self.stop_callback()
        print (self.call_name, "COM Close")

    #def __del__(self):
    #    await self.Stop()

    async def write(self, buf):
        """
        写串口调用
        如果串口正忙的话，该调用会被阻塞直到允许数据发送
        """
        if not self.__isHandShake:
            print ("Error:", self.com_port, "is not connnect, handshake not success")
            return
        await self.write_semaphore.acquire()
        # 如果数据包 pkg 没有被确认接收的话，就不能允许新的数据包写入，
        # 这也是保证纠错帧正常进行的办法

        # 把 输入 组合成 待发送的 数据帧
        bufs = []
        while True:
            bufs.append(buf[:self.Base_Frame_Len])
            # 前一个元素是 数据 本身，后一个 用来标识 是否已发送过
            buf = buf[self.Base_Frame_Len:]
            if len(buf) == 0:
                break

        for f_idx, s in enumerate(bufs):
            # 数据存在 send_bufs 中
            self.send_bufs[self.send_idx_bottom.value] = \
                [self.pkg_idx.value, len(bufs)+1, f_idx, s]
            # send_bufs 存储正常的发送时序，
            # 使接收方能识别出哪些帧收漏收了，可以通过发送时序要求重发
            self.send_idx_bottom.inc()
        # 最后加上一个空白buf帧, 万一有缺帧，接收方可以通过接收到的空白帧，向前扫描
        self.send_bufs[self.send_idx_bottom.value] = \
            [self.pkg_idx.value, len(bufs)+1, f_idx+1, b""]
        self.send_idx_bottom.inc()
        
        self.pkg_idx.inc()
        # pkg_idx 决定数据包的顺序，使乱序收包后能顺序输出
        #self.print_log (self.call_name, "serial_verify class write", len(bufs))

    async def read(self):
        """
        读串口调用
        将阻塞直至有返回值
        """
        return await self.read_out_Queue.get()

    #self.send_bufs[self.send_idx_bottom.value] = [self.pkg_idx, len(send_bufs), f_idx, s]
    def __assemble_Data_Frame(self, send_idx, pkg_idx, len_f_idx, f_idx, buf):
        # 55 55 55 L1 L2 MM NN LL OO ... C1 C2 C3 C4 AA AA
        fHead = struct.pack("=HBBBB", len(buf), send_idx, pkg_idx, len_f_idx, f_idx)
        oStr  = b"\x55\x55\x55" + fHead + buf

        cc = crc32_func(oStr)
        fTail = struct.pack("=I", cc)
        oStr += fTail + b"\xAA\xAA"
        return oStr
    
    def __assemble_Omission_Frame(self, i: int):
        i = i.to_bytes(1, byteorder="big")
        return b"\x55\x55\x55" + b"\xA5\xA5" + i + i + b"\xAA\xAA"
    
    def __assemble_pkg_Done_Frame(self, i: bytes):
        return b"\x55\x55\x55" + b"\xF0\xF0" + i + i + b"\xAA\xAA"

    async def __Com_Write(self):
        """
        内置 的写串口方法
        1、正常数据帧的发出
        2、响应纠错信号, 重发数据帧
        3、发数据包确认信号、纠错信号(如果有)
        """
        self.print_log (self.call_name, "Com_Write Enter.")
        while True:
            #self.print_log (self.call_name,"while_W", end="\t", flush=True)
            while True:
                # 发送 纠错信号
                # 一次循环 纠错信号只发一次，不要重复发
                oMission_idxs = []
                try:
                    r = self.oMission_notice_Queue.get(block=False)
                    if r not in oMission_idxs:
                        self.com_writer.write(self.__assemble_Omission_Frame(r))
                        #await self.com_writer.drain()
                        self.print_log (self.call_name, "   cW", r, end="\t", flush=True)
                    oMission_idxs.append(r)                    
                except queue.Empty:
                    break
            while True:
                # 发送 数据包正确确认信号
                try:
                    r = self.pkg_Done_Queue.get(block=False)
                    self.com_writer.write(self.__assemble_pkg_Done_Frame(r))
                    #await self.com_writer.drain()
                    self.print_log (self.call_name, "   aW", int.from_bytes(r, byteorder='big'))
                except queue.Empty:
                    break
            while True:
                try:
                    r = self.oMission_Data_Queue.get(block=False)
                    if self.send_bufs[r] != None:
                        pkg_idx, len_f_idx, f_idx, buf = self.send_bufs[r]
                        oStr = self.__assemble_Data_Frame(r, pkg_idx, len_f_idx, f_idx, buf)

                        self.com_writer.write(oStr)
                        #await self.com_writer.drain()
                        self.print_log (self.call_name,"  eW", r, len(oStr),end=" ",flush=True)
                except queue.Empty:
                    break
            #self.send_bufs[self.send_idx_bottom.value] = [self.pkg_idx, len(send_bufs), f_idx, s]
            if self.send_bufs[self.send_idx_now.value] != None:
                pkg_idx, len_f_idx, f_idx, buf = self.send_bufs[self.send_idx_now.value]
                oStr = self.__assemble_Data_Frame(self.send_idx_now.value, pkg_idx, len_f_idx, f_idx, buf)

                #self.print_log (self.call_name,"   w",self.send_idx_now.value, len(oStr))#,end=" ",flush=True)
                self.send_idx_now.inc()
                # 发送完成后, self.send_idx_now指向下一个待发帧

                self.com_writer.write(oStr)
                #await self.com_writer.drain()
            await asyncio.sleep(0.02)

    async def __Notice_recv_Omission_Frame(self):
        while True:
            self.recv_Omission_Frame_Count = len(self.oMission_idxs_list)
            for r in self.oMission_idxs_list:
                self.oMission_notice_Queue.put(r)

            await asyncio.sleep(self.One_Frame_recv_Time *3)
            # 用延时，让 通知 坏帧不要发送太频繁

    async def __Find_recv_Ready_pkg(self):
        # 扫描有无接收完整的pkg, 若已具备，则外发
        #self.send_bufs[self.send_idx_bottom.value] = [self.pkg_idx, len(send_bufs), f_idx, s]
        pkg_fidx_Count = -100
        last_pkg_idx = -100
        j = self.recv_idx_top.copy()
        while j <= self.recv_idx_now:
            # 使用 小于等于 即便是 刚刚收到的 正确帧，也可以纳入处理范围
            # 极端情况可能是，当前接收的帧就是 一个完整数据包的最后一帧
            # 处理完成后 recv_idx_top 指向 recv_idx_now 的后一个位置
            if self.recv_bufs[j.value] is None:
                # 遇到一个残帧，就不扫描了
                # 即便后面又完整的数据包，为保证数据包的正确顺序，也不能往后扫描
                break
            else:
                pkg_idx, sum_fidx, fidx, s = self.recv_bufs[j.value]
                if last_pkg_idx != pkg_idx and fidx == 0:
                    pkg_fidx_Count = 1
                    oStr = s
                elif last_pkg_idx == pkg_idx and fidx > 0 and pkg_fidx_Count > 0:
                    pkg_fidx_Count += 1
                    oStr += s
                if pkg_fidx_Count == sum_fidx and last_pkg_idx == pkg_idx :
                    self.print_log (self.call_name, "Find pkg", pkg_idx)
                    # 数完了一整个合格的, 放入输出队列                    
                    await self.read_out_Queue.put(oStr)
                    # 发送 数据包 接收确认帧给 发送方
                    self.pkg_Done_Queue.put(int(pkg_idx).to_bytes(1, byteorder='big'))
                    pkg_fidx_Count = -100
                    # 把 read_bufs 对应位置清空
                    for i in range(sum_fidx):
                        self.recv_bufs[(j - i).value] = None
                    # 移动 self.recv_idx_top 指针
                    self.recv_idx_top.inc(sum_fidx)
                last_pkg_idx = pkg_idx
            j.inc()

    async def __Com_Read(self):
        """
        内置 的读串口方法
        1、作为发送方
           响应处理 pkg接收确认信号、要求补发 send_idx信号
        2、作为接收方
           检查接收的环形缓冲, 发现有接收完成的数据包, 准备数据包确认信号, 为read准备外读的数据包
           检查接收的环形缓冲, 有缺失的, 准备纠错信号
        """
        self.print_log (self.call_name, "Com_Read Enter.")
        d = b""
        # cInt_Next_recv_idx 指向期待的接受的下一个序号，用来检测 跳帧
        cInt_Next_recv_idx = CircularInt(256, 0)
        while True:
            #self.print_log (self.call_name,"while_R", end="\t", flush=True)
            #self.print_log (self.call_name,"rr", end=" ",flush=True)
            #last_len_d = len(d)
            d += await self.com_reader.readuntil(separator=b"\xAA\xAA")
            #self.print_log (self.call_name, "Com_Read ", print_hex(d))
            #if len(d) == last_len_d:
            #    continue
            while True:
                # 这个while循环 目的就是 不停的 找帧头

                #if len(d) == 0:
                #    break
                # vvvvvvvvvvvv111111111111
                # 此处往下，没有协程切换点
                match = re.search(b"\x55\x55\x55", d)
                if match is None:
                    #print ("MmM",end=" ",flush=True)
                    break

                i = match.start()
                #print (self.call_name,"rr", i, len(d), print_hex(d[:9]), print_hex(d[-9:]))#, end=" ", flush=True)

                if len(d[i:]) < 9:
                    break

                frame_head = d[i:i +9]
                f_len, recv_idx, pkg_idx, sum_fidx, fidx = \
                    struct.unpack ("=xxxHBBBB", frame_head)
                #print (self.call_name, "f_len, f_idx ", f_len, f_idx, len(d[i:]), i, print_hex (d[i:i +8]))
                #print_hex (d[i:i +8])

                if d[i +3: i +5] == b"\xF0\xF0" \
                and d[i +7: i +9] == b"\xAA\xAA":
                    # pkg 接收确认信号
                    # 数据确认信号 只可能 在recv_idx_top，也就是send_idx_top
                    # 55 55 55 F0 F0 ZZ ZZ AA AA
                    if d[i +5] == d[i +6]:
                        while self.send_bufs[self.send_idx_top.value] is not None:
                            if self.send_bufs[self.send_idx_top.value][0] == pkg_idx:
                                self.send_bufs[self.send_idx_top.value] = None
                            else:
                                break
                            self.send_idx_top.inc()
                        self.write_semaphore.release()
                    self.print_log (self.call_name, "Ready", d[i +5])
                    d = d[i +9:]

                elif d[i +3: i +5] == b"\xA5\xA5" \
                and d[i +7: i +9] == b"\xAA\xAA":
                    # 收到要求补发 self.send_idx
                    # 55 55 55 A5 A5 ZZ ZZ AA AA
                    if d[i +5] == d[i +6]:                        
                        self.oMission_Data_Queue.put(d[i +5])
                    d = d[i +9:]
                    #print ("RrR",end=" ",flush=True)

                elif d[i: i +9] == b"\x55\x55\x55\xA5\x00\xA5\x00\xAA\xAA":
                    # 收到串口退出信号，开始调用 Stop方法了
                    # 因为 Stop 方法会 kill 本协程，所以放到另一个协程里去运行
                    asyncio.create_task(self.Stop())
                    print (self.call_name, "Server_Verify recv Stop.")
                    d = d[i+9: ]

                elif len(d[i:]) >= f_len +15:
                    # 55 55 55 L1 L2 MM NN LL OO ... C1 C2 C3 C4 AA AA
                    c1 = crc32_func (d[i: i +9 +f_len])
                    #print (f_len, print_hex(d[i +6 +f_len:i +6 +f_len +4],"-"))
                    cc1, = struct.unpack("=I", d[i +9 +f_len:i +9 +f_len +4])
                    #print (self.call_name, "Test CRC")
                    # 接受 数据帧
                    if c1 != cc1:
                        # 没有找到，只好跳过这个帧同步，继续找下一帧同步头
                        d = d[3:]
                        self.print_log ("EeE",end=" ",flush=True)
                    else:
                        # 收到 一个 好 的数据帧
                        s = d[i +9: i +9 +f_len]
                        self.recv_bufs[recv_idx] = [pkg_idx, sum_fidx, fidx, s]
                        cInt_recv_idx = CircularInt(256, recv_idx)
                        """
                        self.print_log (self.call_name, "R",
                               recv_idx, pkg_idx, sum_fidx, fidx,
                               len(d[i: i +15 +f_len]),
                               end = "--")
                        """
                        # 扫描 漏掉的 帧序号 ?? 接收的几个序号逻辑关系要核对
                        # 把 没有正确接收的帧序号 通知出去
                        # 通知的动作是定时任务，与Com_Read 过程 解耦
                        r_distant = cInt_recv_idx.distant(cInt_Next_recv_idx)
                        """
                        self.print_log (r_distant,
                               cInt_Next_recv_idx.value, end = "--")
                        """
                        for _ in range(r_distant):
                            if cInt_Next_recv_idx.value not in self.oMission_idxs_list:
                                self.oMission_idxs_list.append(cInt_Next_recv_idx.value)
                            cInt_Next_recv_idx.inc()                                
                            self.print_log (cInt_Next_recv_idx.value, end = "==", flush=True)

                        if self.recv_idx_now < cInt_recv_idx:
                            # 必须使用 环形对象的 比较方法, 
                            # 虽然 在自然条件下会有 120 > 100
                            # 但是环回后，1 > 200 的情况
                            self.recv_idx_now = cInt_recv_idx.copy()
                            cInt_Next_recv_idx.inc()

                        if recv_idx in self.oMission_idxs_list:
                            self.oMission_idxs_list.remove(recv_idx)
                        # -----没有协程切换点，可以放心保证变量的使用、修改唯一性-----

                        # 此处往上没有协程切换点
                        # vvvvvvvvvvvv111111111111

                        # 扫描有无接收完整的pkg, 若已具备，则外发
                        await self.__Find_recv_Ready_pkg()

                        d = d[i + f_len + 15:]
                else:
                    #d = d[3:]
                    # 此处有两种情况，一种是读入的数据不够长，另一种是读入的数据有错
                    if len(d[i:]) < self.Base_Frame_Len +15:
                        self.print_log ("Com Read Fial 0", len(d), i, len(d[i:]))
                        break
                    else:
                        self.print_log ("Com Read Fial 1", len(d), i, len(d[i:]))
                        d = d[i +3:]

def D1(baud_rate, com_port, buf, ret_Q):
    C1 = serial_verify(com_port, baud_rate, "AA")
    c1_Start = eventlet.spawn(C1.Start)
    c1_Start.wait()
    C1.write(buf)
    C1.write(buf)
    b1 = C1.read()
    b1 = C1.read()
    time.sleep(2)
    C1.Stop()
    ret_Q.put(("C1", b1))
    print ("C1", len(b1), b1[:30])

def D2(baud_rate, com_port, buf, ret_Q):
    C2 = serial_verify(com_port, baud_rate, "BB")
    c2_Start = eventlet.spawn(C2.Start)
    c2_Start.wait()
    C2.write(buf)
    C2.write(buf)
    b2 = C2.read()
    b2 = C2.read()
    time.sleep(2)
    C2.Stop()
    ret_Q.put(("C2", b2))
    print ("C2", len(b2), b2[:30])

def multiP_main():
    baud_rate = 1200
    baud_rate = 9600
    baud_rate = 115200
    baud_rate = 921600
    #baud_rate = 460800
    #baud_rate = 1000000
    baud_rate = 6000000
    mpM = multiprocessing.Manager()
    ret_Q = mpM.Queue()
    #b1 = os.urandom(8*1024)
    b1 = os.urandom(1000*8)
    b2 = os.urandom(2000*4)

    if socket.gethostname() == "x4deep":
        comA = "COM1"
        comB = "COM2"
    elif socket.gethostname() == "Q6600":
        comA = "COM1"
        comB = "COM2"
    else:
        return

    D1_proc = multiprocessing.Process(target=D1, \
                    args=(baud_rate, comA, b1, ret_Q),daemon=True)
    D2_proc = multiprocessing.Process(target=D2, \
                    args=(baud_rate, comB, b2, ret_Q),daemon=True)
    D2_proc.start()
    D1_proc.start()
    D1_proc.join()
    D2_proc.join()
    #recv_proc.join()
    #send_proc.join()
    print ("proc end.")

    z1, d1 = ret_Q.get()
    z2, d2 = ret_Q.get()
    if z1 == "C1":
        d1, d2 = d2, d1
    if b1 == d1 and b2 == d2:
        print ("OK") 
    else:
        print ("Fail")

def pause_signal(signum, frame):
    pdb.set_trace()

async def smain():
    async def C1_write(C1 :serial_verify, b1):
        for b in b1:
            await C1.write(b)
    async def C2_read (C2 :serial_verify, b_count, b2):
        for i in range(b_count):
            b2.append(await C2.read())
            #print ("Ready", i)
    #baud_rate = 600
    #baud_rate = 38400
    #baud_rate = 115200
    baud_rate = 921600
    #baud_rate = 1500000
    #baud_rate = 2000000
    #baud_rate = 4000000
    
    if socket.gethostname() == "x4deep":
        C1 = serial_verify("COM1", baud_rate, log_level=2, call_name="AA")
        C2 = serial_verify("COM2", baud_rate, log_level=2, call_name="BB")
    elif socket.gethostname() == "Q6600":
        C1 = serial_verify("COM1", baud_rate, call_name="AA")
        C2 = serial_verify("COM2", baud_rate, call_name="BB")
    else:
        return
    
    b_count = 150
    b1 = [os.urandom(11*1000) for _ in range(b_count)]

    c1_start = asyncio.create_task(C1.Start())
    c2_start = asyncio.create_task(C2.Start())
    #eventlet.sleep(10)
    await c2_start
    await c1_start

    asyncio.create_task(C1_write(C1, b1))
    b2 = []
    await C2_read(C2, b_count, b2)

    ret = True
    for i, b in enumerate(b1):
        if b != b2[i]:
            ret = False

    if ret:
        print ("OK") 
    else:
        print ("Fail")

def run_main():
    asyncio.run(smain())

if __name__ == "__main__":
    reversal_init_crc32_table()
    #asyncio.run(smain())
    #multiP_main()
    
    N = 1
    #t = timeit.timeit(multiP_main, number=N)
    t = timeit.timeit(run_main, number=N)
    print ("sum_time", t, t/N)
