# -*- coding: utf-8 -*-
from __future__ import absolute_import, print_function

import asyncio
import struct
import argparse
import socket

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
    def __init__ (self, serial, gui_debug=None):
        self.gui_debug = gui_debug
        self.serial = serial
        print (serial)
        self.readers = {}
        self.writers = {}
    def Start(self):
        asyncio.create_task(self.com_recv())
    def Stop(self):
        pass
    async def net_recv(self, svr_port, clt_port):
        # 此协程是有新的连接时，才启动
        print ("net_recv ", svr_port, clt_port)
        sc_pack = struct.pack("=II", svr_port, clt_port)
        while True:
            try:
                d = await self.readers[(svr_port, clt_port)].read(8*1024)
            except Exception as e:
                break
            if len(d) > 0:
                print ("recv ", len(d))
                if self.gui_debug != None:
                    self.gui_debug ('r', str(clt_port)+"\t"+str(len(d)))
                await self.serial.write(b"\x00" +sc_pack +d)
                # 这个是阻塞的
            else:
                # 网络已断开，需要打扫现场，并告知对端
                self.writers[(svr_port, clt_port)].close()
                del self.writers[(svr_port, clt_port)]
                del self.readers[(svr_port, clt_port)]
                await self.serial.write(b"\x02" +sc_pack)
    async def com_recv(self):
        while True:
            # 这个是阻塞的
            buf = await self.serial.read()
            print ("com_recv ", len(buf))
            idx, svr_port, clt_port = struct.unpack("=BII", buf[:5])
            sc_pack = buf[1:5]
            if idx == 0:
                if self.gui_debug != None:
                    self.gui_debug ('w', str(clt_port) + "\t" +str(len(buf[5:])))
                await self.writers[(svr_port, clt_port)].write (buf[5:])
            elif idx == 2:
                # 不论是服务器、还是客户端，都可能从com收到要求断开信号
                self.writers[(svr_port, clt_port)].close()
                del self.writers[(svr_port, clt_port)]
                del self.readers[(svr_port, clt_port)]
            elif idx == 1:
                # 只有服务器端，才可能从com收到请求连接的信号
                print ("com_recv idx==1", svr_port, clt_port)
                try:
                    reader, writer = await asyncio.open_connection('localhost', svr_port)
                    self.writers[(svr_port, clt_port)] = writer
                    self.readers[(svr_port, clt_port)] = reader
                    asyncio.create_task(self.net_recv(svr_port, clt_port))
                except Exception as e:
                    # 不能连接服务，告诉对方网络已断开
                    await self.serial.write (b"\x02" + sc_pack)

class Socket_Forward_Serial_Client(Socket_Forward_Serial_Base):
    def __init__(self, serial, ports, port_offset=40000, gui_debug=False):
        super().__init__(serial, gui_debug)
        self.ports = ports
        self.port_offset = port_offset
        self.servers = {}
    async def server_listen(self, reader, writer):
        _, svr_port = writer.get_extra_info('sockname')
        _, clt_port = writer.get_extra_info('peername')
        svr_port = svr_port - self.port_offset
        self.readers[(svr_port, clt_port)] = reader
        self.writers[(svr_port, clt_port)] = writer
        # 通知对端，新连接来了
        sc_pack = struct.pack("=II", svr_port, clt_port)
        self.serial.write(b"\x01" +sc_pack)
        asyncio.create_task(self.net_recv(svr_port, clt_port))        
        print ("had server_listen ", svr_port, clt_port)
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
            self.servers[port] = await asyncio.start_server(self.server_listen, 
                                                            sock=server_sock)
    def Start(self):
        super().Start()
        # 由于 Start 必须马上返回，所以不能是协程
        # 服务器的启动只好放到另一个协程中去实现了
        asyncio.create_task(self.Start_Server())
    def Stop(self):
        for k in self.writers:
            self.writers[k].close()

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