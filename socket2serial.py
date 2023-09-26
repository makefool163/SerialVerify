# -*- coding: utf-8 -*-
from __future__ import absolute_import, print_function

import asyncio

# 1    控制命令
# 1.1  连接服务器
# 1.2      
# 2    负载数据
#      XX XX YY YY DD DD ...
#      XX XX 服务器端口号
#      YY YY 客户端端口号
class Socket_Forward_Serial_Base:
    def __init__ (self, serial, ip, port, gui_debug=False):
        self.gui_debug = gui_debug
        self.serial = serial
        self.ip = ip
        self.port = port
    def Start(self):
        pass
    def Stop(self):
        pass
    async def net_recv(self, sock):
        while True:
            try:
                d = sock.recv(8*1024)
            except Exception as e:
                break
            if len(d) > 0:
                self.serial.write(d)
                # 这个是阻塞的
            else:
                # 对端已断开，需要打扫现场
                pass
    async def net_send(self):
        while True:
            # 这个是阻塞的
            com_ret = self.serial.read()
            # 先分析一下数据




if __name__ == "__main__":
