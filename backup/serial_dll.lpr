library serial_dll;

{$mode objfpc}{$H+}

uses
  Classes
  { you can add units after this };
  Windows;

// 初始化函数
function InitSerialPort: integer; export;
// 启动函数
procedure StartSerialThreads;
// 停止函数
procedure StopSerialThreads;
// 输出函数1
procedure Output1; cdecl; export;
// 输出函数2
procedure Output2; cdecl; export;

implementation

var
  hSerial: THandle;            // 串口句柄
  ovRead, ovWrite: TOverlapped; // 读写事件
  bConnected: boolean;         // 串口是否已连接

// 读取线程函数
function ReadThread(lpParameter: pointer): DWORD; stdcall;
var
  pData: ^integer;
  dwEventMask: DWORD;
  szBuf: array[0..1023] of char;
  nBytesRead: DWORD;
begin
  pData := lpParameter;
  ovRead.hEvent := CreateEvent(nil, true, false, nil);   // 创建事件对象

  // 监听串口事件
  SetCommMask(hSerial, EV_RXCHAR);

  while bConnected do
  begin
    // 等待事件
    if WaitCommEvent(hSerial, dwEventMask, @ovRead) then
    begin
      // 读取数据
      if ReadFile(hSerial, szBuf, sizeof(szBuf), nBytesRead, @ovRead) then
      begin
        szBuf[nBytesRead] := #0;
        WriteLn(Format('线程 %d 收到数据： %s', [pData^, szBuf]));
      end;
    end;
  end;

  CloseHandle(ovRead.hEvent);

  Result := 0;
end;

// 写入线程函数
function WriteThread(lpParameter: pointer): DWORD; stdcall;
var
  pData: ^integer;
  szBuf: array[0..1023] of char;
  nBytesWritten: DWORD;
begin
  pData := lpParameter;

  while bConnected do
  begin
    // 获取输入
    Write(Format('线程 %d 请输入要发送的数据： ', [pData^]));
    ReadLn(szBuf, sizeof(szBuf));

    // 写入数据
    if WriteFile(hSerial, szBuf, Length(szBuf), nBytesWritten, @ovWrite) then
    begin
      WriteLn(Format('线程 %d 发送数据成功： %s', [pData^, szBuf]));
    end
    else
    begin
      WriteLn(Format('线程 %d 发送数据失败', [pData^]));
    end;
  end;

  Result := 0;
end;

// 初始化函数
function InitSerialPort: integer; export;
var
  dcbSerialParams: TDCB;
begin
  // 打开串口
  hSerial := CreateFile('COM1', GENERIC_READ or GENERIC_WRITE, 0, nil, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, 0);
  if hSerial = INVALID_HANDLE_VALUE then
  begin
    WriteLn('无法打开串口');
    Result := 1;
    Exit;
  end;

  // 初始化 DCB 结构体
  FillChar(dcbSerialParams, SizeOf(dcbSerialParams), 0);
  dcbSerialParams.DCBlength := SizeOf(dcbSerialParams);
  if not GetCommState(hSerial, dcbSerialParams) then
  begin
    WriteLn('无法获取串口配置');
    CloseHandle(hSerial);
    Result := 1;
    Exit;
  end;

  // 配置串口
  dcbSerialParams.BaudRate := 9600;
  dcbSerialParams.ByteSize := 8;
  dcbSerialParams.Parity := NOPARITY;
  dcbSerialParams.StopBits := ONESTOPBIT;
  if not SetCommState(hSerial, dcbSerialParams) then
  begin
    WriteLn('无法配置串口');
    CloseHandle(hSerial);
    Result := 1;
    Exit;
  end;

  // 初始化事件对象
  FillChar(ovRead, SizeOf(ovRead), 0);
  FillChar(ovWrite, SizeOf(ovWrite), 0);
  ovRead.hEvent := CreateEvent(nil, true, false, nil);
  ovWrite.hEvent := CreateEvent(nil, true, false, nil);

  Result := 0;
end;

// 启动函数
procedure StartSerialThreads;
begin
  // 创建读写线程
  bConnected := true;
  var data1, data2: integer;
  data1 := 1;
  data2 := 2;
  CreateThread(nil, 0, @ReadThread, @data1, 0, nil);
  CreateThread(nil, 0, @WriteThread, @data2, 0, nil);
end;

// 停止函数
procedure StopSerialThreads;
begin
  // 关闭线程和串口
  bConnected := false;
  WaitForSingleObject(hSerial, INFINITE);
  CloseHandle(hSerial);
end;

// 输出函数1
procedure Output1; cdecl; export;
begin
  WriteLn('输出函数1');
end;

// 输出函数2
procedure Output2; cdecl; export;
begin
  WriteLn('输出函数2');
end;

begin
end.
