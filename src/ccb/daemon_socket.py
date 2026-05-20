# -*- coding: utf-8 -*-
"""
UNIX Domain Socket Server for ccb-py.
Manages persistent agent sessions and exposes an asynchronous PTY channel for thin clients.
"""
import asyncio
import os
import struct
import sys
import pty
import tty
import termios
import fcntl
import traceback
from typing import Tuple, Dict, Any

# Protocol Constants
MSG_STDIN = 0x01
MSG_STDOUT = 0x02
MSG_STDERR = 0x03
MSG_RESIZE = 0x04
MSG_SIGNAL = 0x05
MSG_CONTROL = 0x06

class BinaryIPCProtocol:
    @staticmethod
    def pack(msg_type: int, payload: bytes) -> bytes:
        return struct.pack("!BI", msg_type, len(payload)) + payload

    @staticmethod
    async def read_frame(reader: asyncio.StreamReader) -> Tuple[int, bytes]:
        header = await reader.readexactly(5)
        msg_type, length = struct.unpack("!BI", header)
        payload = await reader.readexactly(length)
        return msg_type, payload


class DaemonSocketServer:
    def __init__(self, socket_path: str = None):
        if socket_path is None:
            self.socket_path = os.path.expanduser("~/.ccb/ccb_ipc.sock")
        else:
            self.socket_path = socket_path
        self.server = None

    async def start(self):
        # 确保套接字所在目录存在
        os.makedirs(os.path.dirname(self.socket_path), exist_ok=True)
        if os.path.exists(self.socket_path):
            try:
                os.unlink(self.socket_path)
            except OSError:
                pass

        self.server = await asyncio.start_unix_server(self.handle_client, self.socket_path)
        print(f"[Daemon Socket] Listening on {self.socket_path}")

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """
        处理瘦客户端连结，为其创建 PTY，并驱动后台交互式会话。
        """
        print("[Daemon Socket] Client connected.")
        
        # 创建伪终端
        master_fd, slave_fd = pty.openpty()
        
        # 定义内部管道数据流转发
        async def read_client_loop():
            try:
                while True:
                    msg_type, payload = await BinaryIPCProtocol.read_frame(reader)
                    if msg_type == MSG_STDIN:
                        os.write(master_fd, payload)
                    elif msg_type == MSG_RESIZE:
                        if len(payload) == 4:
                            rows, cols = struct.unpack("!HH", payload)
                            # 设置伪终端窗口尺寸
                            try:
                                fcntl.ioctl(master_fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
                            except IOError:
                                pass
                    elif msg_type == MSG_SIGNAL:
                        if len(payload) == 4:
                            sig_num = struct.unpack("!I", payload)[0]
                            # 向伪终端所连进程发送信号
                            os.kill(0, sig_num)
            except asyncio.IncompleteReadError:
                pass
            except Exception as e:
                print(f"[Daemon Socket] Error reading from client: {e}")
            finally:
                try:
                    os.close(master_fd)
                except OSError:
                    pass

        async def read_pty_loop():
            loop = asyncio.get_running_loop()
            try:
                while True:
                    # 在 executor 里非阻塞读取 PTY
                    data = await loop.run_in_executor(None, os.read, master_fd, 4096)
                    if not data:
                        break
                    frame = BinaryIPCProtocol.pack(MSG_STDOUT, data)
                    writer.write(frame)
                    await writer.drain()
            except Exception:
                pass
            finally:
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass

        # 启动后台主 REPL 执行（将标准 IO 挂载到 slave PTY）
        # 这里为简便起见，我们在后台单独派生出 ccb 真实的异步执行流
        async def run_repl_subprocess():
            # 我们直接复制当前进程状态或者调用子命令启动真正的本地 REPL
            # 将其标准 I/O 绑定到 slave_fd 上
            # 这是一个极具鲁棒性的设计，可以把 slave_fd 作为新进程的 stdin/stdout/stderr
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "ccb", "--classic",
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                preexec_fn=os.setsid # 创建新进程组
            )
            # 守护进程中等待子进程退出
            await process.wait()
            # 退出后关闭 slave 描述符
            try:
                os.close(slave_fd)
            except OSError:
                pass

        # 并发驱动
        await asyncio.gather(
            read_client_loop(),
            read_pty_loop(),
            run_repl_subprocess(),
            return_exceptions=True
        )
        print("[Daemon Socket] Client disconnected and resources cleared.")


async def main():
    server = DaemonSocketServer()
    await server.start()
    try:
        await asyncio.Event().wait() # 保持运行
    except KeyboardInterrupt:
        print("[Daemon Socket] Exiting...")

if __name__ == "__main__":
    asyncio.run(main())
