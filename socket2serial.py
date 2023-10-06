# -*- coding: utf-8 -*-
from __future__ import absolute_import, print_function

import asyncio
import struct
import argparse
import socket
import serial_verify_v2
import time

# 1    控制命令
# 1.1  连接服务器
#      01 XX XX YY YY
# 1.2  网络已断开
#      02 XX XX YY YY
# 2    负载数据
#      00 XX XX YY YY DD DD ...
#      XX XX 服务器端口号
#      YY YY 客户端端口号
class Socket_Forward_Serial_Base:
    def __init__ (self, serial: serial_verify_v2.serial_verify, gui_debug=None):
        self.gui_debug = gui_debug
        self.serial = serial
        print (serial)
        self.readers = {}
        self.writers = {}
    def Start(self):
        asyncio.create_task(self.serial.Start())
        self.com_recv_task = asyncio.create_task(self.com_recv())
    def Stop(self):
        self.serial.Stop()
        self.com_recv_task.cancel()
        self.writers = {}
        self.readers = {}
    async def net_recv(self, reader:asyncio.StreamReader, svr_port, clt_port):
        # 此协程是有新的连接时，才启动
        print ("net_recv enter", svr_port, clt_port)
        connect_timeout = time.time()
        #reader = self.readers[(svr_port, clt_port)]
        sc_pack = struct.pack("=HH", svr_port, clt_port)
        while True:
            try:
                d = await reader.read(8*1024)
                # 这个在winscp的重连接时，似乎无效，连接建立起来后，read结果一直是 0                
                if len(d) > 0:
                    print ("net_recv ", len(d), end=" ")
                    if self.gui_debug != None:
                        self.gui_debug ('r', str(clt_port)+"\t"+str(len(d)))
                    await self.serial.write(b"\x00" +sc_pack +d)
                    connect_timeout = time.time()
                    print ("!")
                    # 这个是阻塞的
                else:
                    #print ("Z", end="", flush=True)
                    if time.time() - connect_timeout > 25:
                        print ('close net_recv 0 byte by timeout', svr_port, clt_port)
                        # 网络已断开，需要打扫现场，并告知对端
                        if (svr_port, clt_port) in self.writers:
                            writer = self.writers[(svr_port, clt_port)]
                            del self.writers[(svr_port, clt_port)]
                            del self.readers[(svr_port, clt_port)]
                            writer.close()
                            await writer.wait_closed()
                        await self.serial.write(b"\x02" +sc_pack)
                        break
            except asyncio.exceptions.IncompleteReadError as e:
                if e.args and e.args[-1] == b'':
                    # 服务器端已经断开连接
                    print("Server has closed the connection.")
                    break
            except ConnectionRefusedError as e:
                print("ConnectionRefusedError.", e)
                break
            except Exception as Error:
                print ('close net_recv', Error, svr_port, clt_port)
                # 网络已断开，需要打扫现场，并告知对端
                if (svr_port, clt_port) in self.writers:
                    writer = self.writers[(svr_port, clt_port)]
                    del self.writers[(svr_port, clt_port)]
                    del self.readers[(svr_port, clt_port)]
                    writer.close()
                    await writer.wait_closed()
                await self.serial.write(b"\x02" +sc_pack)
                break
    async def com_recv(self):
        while True:
            # 这个是阻塞的
            #print ("serial.read...",end=" ", flush=True)
            buf = await self.serial.read()
            idx, svr_port, clt_port = struct.unpack("=BHH", buf[:5])
            print ("com_recv ", svr_port, clt_port, len(buf))
            sc_pack = buf[1:5]
            if idx == 0:
                print (f"c0_{len(buf[5:])}",end=" ",flush=True)
                if self.gui_debug != None:
                    self.gui_debug ('w', str(clt_port) + "\t" +str(len(buf[5:])))
                if (svr_port, clt_port) in self.writers:
                    self.writers[(svr_port, clt_port)].write (buf[5:])
                    await self.writers[(svr_port, clt_port)].drain()
                    print ("@", end="", flush=True)
                """
                print ("net_send try write", 
                       svr_port, clt_port, 
                       self.writers[(svr_port, clt_port)],
                       len(buf[5:]))
                """
            elif idx == 2:
                print (f"c2_{len(buf[5:])}",end=" ",flush=True)
                # 不论是服务器、还是客户端，都可能从com收到要求断开信号
                print ("com_recv idx==2", svr_port, clt_port)
                if (svr_port, clt_port) in self.writers:
                    writer = self.writers[(svr_port, clt_port)]
                    del self.writers[(svr_port, clt_port)]
                    del self.readers[(svr_port, clt_port)]
                    writer.close()
                    await writer.wait_closed()
                print ("com_recv idx==2 done")
            elif idx == 1:
                print (f"c1_{len(buf[5:])}",end=" ",flush=True)
                # 只有服务器端，才可能从com收到请求连接的信号
                print ("com_recv idx==1", svr_port, clt_port)
                try:
                    print ("openconnect 0", svr_port, clt_port)
                    if (svr_port, clt_port) not in self.writers:
                        print ("openconnect 1", svr_port, clt_port)
                        reader, writer = await asyncio.open_connection('localhost', svr_port)
                        self.writers[(svr_port, clt_port)] = writer
                        self.readers[(svr_port, clt_port)] = reader
                        print ("openconnect 2", svr_port, clt_port)
                        asyncio.create_task(self.net_recv(reader, svr_port, clt_port))
                        print ("openconnect 3", svr_port, clt_port)
                except Exception as e:
                    # 不能连接服务，告诉对方网络已断开
                    print ("openconnect Error", svr_port, clt_port, e)
                    await self.serial.write (b"\x02" + sc_pack)

class Socket_Forward_Serial_Client(Socket_Forward_Serial_Base):
    def __init__(self, serial, ports, port_offset=40000, gui_debug=False):
        super().__init__(serial, gui_debug)
        self.ports = ports
        self.port_offset = port_offset
        self.servers = {}
        self.net_recv_proc = {}
    async def server_listen(self, reader, writer):
        _, svr_port = writer.get_extra_info('sockname')
        _, clt_port = writer.get_extra_info('peername')
        svr_port = svr_port - self.port_offset
        self.readers[(svr_port, clt_port)] = reader
        self.writers[(svr_port, clt_port)] = writer
        # 通知对端，新连接来了
        print ("had server_listen ", svr_port, clt_port)
        sc_pack = struct.pack("=HH", svr_port, clt_port)
        await self.serial.write(b"\x01" +sc_pack) #　新连接在这里卡住了？
        self.net_recv_proc[(svr_port, clt_port)] = \
            asyncio.create_task(self.net_recv(reader, svr_port, clt_port))
        print ("prepare from new listen in")
    async def Start_Server(self):
        for port in self.ports:
            server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            # 启动alive探测
            server_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 20)
            # 空闲时间
            server_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 3)
            # 探测间隔
            server_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
            # 探测次数
            server_sock.bind (("localhost", port))
            server_sock.listen()
            self.servers[port] = [await asyncio.start_server(self.server_listen, 
                                                            sock=server_sock),
                                                            server_sock]
    def Start(self):
        super().Start()
        # 由于 Start 必须马上返回，所以不能是协程
        # 服务器的启动只好放到另一个协程中去实现了
        asyncio.create_task(self.Start_Server())
    def Stop(self):
        for k in self.net_recv_proc:
            self.net_recv_proc[k].cancel()
        for k in self.writers:
            self.writers[k].close()
            asyncio.create_task(self.writers[k].wait_closed())
        for k in self.servers:
            #self.servers[k][0].close()
            self.servers[k][1].setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.servers[k][1].close()
        print ('Client Stop 1')
        super().Stop()
        self.servers = {}
        print ('Client Stop 2')
        
async def main():
    parser = argparse.ArgumentParser(description="Forward socket service to another computer via a serial port.")
    parser.add_argument('Action', choices=['S', 'C'], type=str, help='act as forwarding Server or Client')
    parser.add_argument('-ip', default='localhost', type=str, help="Connect to Server IP when act as Source, default is localhost")
    parser.add_argument('-port', default=22, type=int, nargs=1, required=True, help='Connect to Server port when act as source/Listen port when act as target, default is 22')
    parser.add_argument('-com', type=str, nargs=1, required=True, help="Serial Port")
    parser.add_argument('-baudrate', type=int, default=115200, help="Serial Port baudrate")
    parser.add_argument('-d', '--debug', choices=[0,1,2,3], type=int, default=0, help="set debug out")
    parser.add_argument('-b', "--backdoor", action="store_true", help="set backdoor debug")
    args = parser.parse_args()

if __name__ == "__main__":
    asyncio.run(main())