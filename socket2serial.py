# -*- coding: utf-8 -*-
from __future__ import absolute_import, print_function

import asyncio
import struct

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
    def __init__ (self, serial, gui_debug=False):
        self.gui_debug = gui_debug
        self.serial = serial
        self.readers = {}
        self.writers = {}
    def Start(self):
        asyncio.create_task(self.com_recv)
    def Stop(self):
        pass
    async def net_recv(self, svr_port, clt_port):
        # 此协程是有新的连接时，才启动
        sc_pack = struct.pack("=II", svr_port, clt_port)
        while True:
            try:
                d = self.readers[(svr_port, clt_port)].read(8*1024)
            except Exception as e:
                break
            if len(d) > 0:
                self.serial.write(b"\x00" +sc_pack +d)
                # 这个是阻塞的
            else:
                # 网络已断开，需要打扫现场，并告知对端
                self.writers[(svr_port, clt_port)].close()
                del self.writers[(svr_port, clt_port)]
                del self.readers[(svr_port, clt_port)]
                self.serial.write(b"\x02" +sc_pack)
    async def com_recv(self):
        while True:
            # 这个是阻塞的
            buf = self.serial.read()
            idx, svr_port, clt_port = struct.unpack("=BII", buf[:3])
            sc_pack = buf[1:3]
            if idx == 0:
                self.writers[(svr_port, clt_port)].write (buf[3:])
            elif idx == 2:
                self.writers[(svr_port, clt_port)].close()
                del self.writers[(svr_port, clt_port)]
                del self.readers[(svr_port, clt_port)]
            elif idx == 1:
                try:
                    reader, writer = await asyncio.open_connection('localhost', svr_port)
                    self.writers[(svr_port, clt_port)] = writer
                    self.readers[(svr_port, clt_port)] = reader
                    asyncio.create_task(self.net_recv(svr_port, clt_port))
                except Exception as e:
                    # 不能连接服务，告诉对方网络已断开
                    self.serial.write (b"\x02" + sc_pack)

class Socket_Forward_Serial_Client(Socket_Forward_Serial_Base):
    def __init__(self, serial, ports, gui_debug=False):
        super().__init__(serial, gui_debug)
        self.ports = ports
        self.severs = {}
    async def server_listen(self, reader, writer):
        client_info = writer.get_extra_info('peername')
        clt_port = client_info[1]
        svr_port = reader.get_extra_info('server_port')
        self.readers[(svr_port, clt_port)] = reader
        self.writers[(svr_port, clt_port)] = writer
    async def Start(self):
        super().Start()
        for port in self.ports:
            self.servers[port] = await asyncio.start_server(self.server_listen, 'localhost', port)

async def main():
    
if __name__ == "__main__":
    asyncio.run(main())