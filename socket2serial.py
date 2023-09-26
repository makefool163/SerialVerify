# -*- coding: utf-8 -*-
from __future__ import absolute_import, print_function

import asyncio
import struct

# 1    控制命令
# 1.1  连接服务器
#      01 XX XX YY YY
# 1.2  网络已断开
#      02 XX XX YY YY
# 1.3  服务器连接失败
#      03 XX XX YY YY
# 2    负载数据
#      00 XX XX YY YY DD DD ...
#      XX XX 服务器端口号
#      YY YY 客户端端口号
class Socket_Forward_Serial_Base:
    def __init__ (self, serial, ip, port, gui_debug=False):
        self.gui_debug = gui_debug
        self.serial = serial
        self.ip = ip
        self.port = port
        self.sock_dir = {}
    def Start(self):
        pass
    def Stop(self):
        pass
    async def net_recv(self, r, w):
        svr_addr = w.get_extra_info('peername')
        cit_addr = r.get_extra_info('sockname')
        svr_addr, svr_port = sock.getpeername()
        cit_addr, cit_port = sock.getsockname()
        sc_pack = struct.pack("=II", svr_port, cit_port)
        while True:
            try:
                d = sock.recv(8*1024)
            except Exception as e:
                break
            if len(d) > 0:
                self.serial.write(b"\x00" +sc_pack +d)
                # 这个是阻塞的
            else:
                # 对端已断开，需要打扫现场
                sock.close()
                self.serial.write(b"\x02" + sc_pack)
    async def com_recv(self):
        while True:
            # 这个是阻塞的
            buf = self.serial.read()
            idx, svr_port, cit_port = struct.unpack("=BII", buf[:3])
            if idx == 0:
                self.sock_dir[(svr_port, cit_port)].send (buf[3:])
            elif idx == 2:
                self.sock_dir[(svr_port, cit_port)].close()
                del self.sock_dir[(svr_port, cit_port)]
            elif idx == 1:
                reader, writer = await asyncio.open_connection('127.0.0.1', svr_port)





if __name__ == "__main__":
