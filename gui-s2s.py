# -*- coding: utf-8 -*-
from __future__ import absolute_import, print_function

# Powered by pygubu-designer
# pygubu-designer
# pip install pygubu-designer

import socket2serial
import serial_verify_v2
import serial.tools.list_ports as port_list
import importlib
import os
import socket
import asyncio

#os.environ['PYTHONASYNCIODEBUG'] = '1'

#!/usr/bin/python3
import tkinter as tk
import tkinter.ttk as ttk
from tkinter.scrolledtext import ScrolledText

def get_pc_ip_addresses():
    ip_addresses = ["localhost"]
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # doesn't even have to be reachable
        s.connect(('10.255.255.255', 1))
        ip_addresses.append(s.getsockname()[0])
    except Exception:
        pass
    finally:
        s.close()
    return ip_addresses

class GuiS2SApp:
    def __init__(self, master=None):
        # build ui
        self.baseToplevel = tk.Tk() if master is None else tk.Toplevel(master)
        self.baseToplevel.configure(height=480, width=640)
        self.baseToplevel.resizable(False, False)
        self.baseToplevel.title("gui-socket2serial")
        self.cmbox_bdrate = ttk.Combobox(self.baseToplevel)
        self.cmbox_bdrate.configure(
            takefocus=False,
            values='9600 19200 38400 56000 115200 128000 230400 460800 921600 1500000 2000000')
        self.cmbox_bdrate.grid(column=1, row=3)
        self.cmbox_COM = ttk.Combobox(self.baseToplevel)
        self.cmbox_COM.grid(column=0, row=3)
        self.btServer = ttk.Button(self.baseToplevel)
        self.btServer.configure(text='Run as Server')
        self.btServer.grid(column=0, row=0)
        self.btServer.bind("<ButtonPress>", self.runS_click, add="")
        self.txtSend = ScrolledText(self.baseToplevel)
        self.txtSend.configure(height=40, width=40)
        self.txtSend.grid(column=0, columnspan=2, row=5)
        self.txtRecv = ScrolledText(self.baseToplevel)
        self.txtRecv.configure(height=40, width=40)
        self.txtRecv.grid(column=2, columnspan=2, row=5)
        self.label1 = ttk.Label(self.baseToplevel)
        self.label1.configure(text='socket Send')
        self.label1.grid(column=0, columnspan=2, row=4)
        self.label2 = ttk.Label(self.baseToplevel)
        self.label2.configure(text='socket Recv')
        self.label2.grid(column=2, columnspan=2, row=4)
        self.label5 = ttk.Label(self.baseToplevel)
        self.label5.configure(text='COM')
        self.label5.grid(column=0, row=2)
        self.label6 = ttk.Label(self.baseToplevel)
        self.label6.configure(text='BaudRate')
        self.label6.grid(column=1, row=2)
        self.btClient = ttk.Button(self.baseToplevel)
        self.btClient.configure(text='Run as Client')
        self.btClient.grid(column=0, row=1)
        self.btClient.bind("<ButtonPress>", self.runC_click, add="")
        self.etPS = ttk.Entry(self.baseToplevel)
        self.intPortS = tk.IntVar(value=22)
        self.etPS.configure(textvariable=self.intPortS)
        _text_ = '22'
        self.etPS.delete("0", "end")
        self.etPS.insert("0", _text_)
        self.etPS.grid(column=3, row=0)
        self.etPC = ttk.Entry(self.baseToplevel)
        self.intPortC = tk.IntVar(value=10022)
        self.etPC.configure(textvariable=self.intPortC)
        _text_ = '10022'
        self.etPC.delete("0", "end")
        self.etPC.insert("0", _text_)
        self.etPC.grid(column=3, row=1)
        self.label7 = ttk.Label(self.baseToplevel)
        self.label7.configure(text='Port Forward')
        self.label7.grid(column=2, row=0)
        self.label8 = ttk.Label(self.baseToplevel)
        self.label8.configure(text='Port Listen')
        self.label8.grid(column=2, row=1)
        self.entry1 = ttk.Entry(self.baseToplevel)
        self.strIP_S = tk.StringVar(value='localhost')
        #self.entry1.configure(textvariable=self.strIP_S)
        #_text_ = 'localhost'
        self.entry1.delete("0", "end")
        self.entry1.insert("0", _text_)
        self.entry1.grid(column=1, row=0)
        self.cmbox_IP = ttk.Combobox(self.baseToplevel)
        self.cmbox_IP.grid(column=1, row=0)

        # Main widget
        self.mainwindow = self.baseToplevel

    def run(self):
        self.mainwindow.mainloop(n=5)

    def runS_click(self, event=None):
        print ("Run as Server")
        if self.btServer['text'] == "Run as Server":
            self.btServer['text'] = "Stop Server"
            self.btClient.config(state=tk.DISABLED)
            #self.btClient['state'] = "disable"
            self.txtRecv.delete("1.0","end")
            self.txtSend.delete("1.0","end")
            self.txtRecv_idx = 0
            self.txtSend_idx = 0
            COM_Name = self.com_ports[self.cmbox_COM.get()]
            print (COM_Name)
            log_file = "d:/stk/s2s_server.log"
            log_file = None
            self.com_server = serial_verify_v2.serial_verify\
                (com_port = COM_Name, \
                baud_rate = int(self.cmbox_bdrate.get()),\
                call_name= "Server"
                )
            self.ss = socket2serial.Socket_Forward_Serial_Base\
                (serial=self.com_server, \
                gui_debug=self.gui_debug \
                )
            self.ss.Start()
        else:
            self.btServer['text'] = "Run as Server"
            self.btClient.config(state= tk.NORMAL)
            #self.btClient['state'] = "normal"
            self.ss.Stop()
            del self.com_server
            del self.ss
            importlib.reload(serial_verify_v2)
            importlib.reload(socket2serial)
      
    def runC_click(self, event=None):
        print ("Run as Client")
        if self.btClient['text'] == "Run as Client":
            self.btClient['text'] = "Stop Client"
            self.btServer.config(state=tk.DISABLED)
            #self.btServer['state'] = "disable"
            self.txtRecv_idx = 0
            self.txtSend_idx = 0
            self.txtRecv.delete("1.0","end")
            self.txtSend.delete("1.0","end")
            COM_Name = self.com_ports[self.cmbox_COM.get()]
            print (COM_Name)
            log_file = "d:/stk/s2s_client.log"
            log_file = None
            self.com_client = serial_verify_v2.serial_verify\
                (com_port = COM_Name, \
                baud_rate = int(self.cmbox_bdrate.get()),\
                call_name= "Client"
                )
            self.ss = socket2serial.Socket_Forward_Serial_Client\
                (serial=self.com_client, \
                ports=[self.intPortC.get()], \
                port_offset=10000, \
                gui_debug=self.gui_debug \
                )
            self.ss.Start()
        else:
            self.btClient['text'] = "Run as Client"
            self.btServer.config(state=tk.NORMAL)
            #self.btServer['state'] = "normal"
            self.ss.Stop()
            del self.com_client
            del self.ss
            importlib.reload(serial_verify_v2)
            importlib.reload(socket2serial)

    def gui_debug(self, SC, inStr):
        if SC == "r":
            self.txtRecv_idx +=1 
            inStr = str(self.txtRecv_idx) + "\t" + inStr
            self.txtRecv.insert(tk.END, inStr+"\n")
            self.txtRecv.see(tk.END)
            self.txtRecv.update()
        else:
            self.txtSend_idx +=1
            inStr = str(self.txtSend_idx) + "\t" + inStr
            self.txtSend.insert(tk.END, inStr+"\n")
            self.txtSend.see(tk.END)
            self.txtSend.update()

    def on_closing(self):
        print ("on_closing")
        self.mainwindow.quit()        
        self.mainwindow.destroy()
        print ("self.mainwindow.destroy()")
        os._exit(0)

    def run_First(self):
        self.mainwindow.protocol("WM_DELETE_WINDOW", self.on_closing)
        ips = get_pc_ip_addresses()
        self.cmbox_IP["values"] = " ".join(ips)
        self.cmbox_IP.current(0)
        com_ports = list(port_list.comports())
        com_ports.reverse()
        com_devices = [i.device for i in com_ports]
        com_description = [i.description for i in com_ports]        
        self.com_ports = dict(zip(com_description, com_devices))
        self.cmbox_COM['value'] = com_description
        self.cmbox_COM.current(0)
        self.cmbox_bdrate.current(4)        
        self.txtRecv_idx = 0
        self.txtSend_idx = 0

    async def coroutine_mainloop(self):
        while True:
            self.mainwindow.update_idletasks()
            self.mainwindow.update()
            await asyncio.sleep(0)
            #eventlet.sleep(n)

if __name__ == "__main__":
    app = GuiS2SApp()
    app.run_First()
    #app.run()
    #app.coroutine_mainloop()
    """
    loop = asyncio.get_event_loop() 
    loop.run_in_executor(None, app.coroutine_mainloop)
    """
    asyncio.run(app.coroutine_mainloop())
