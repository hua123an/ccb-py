#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Lightweight and fast interactive terminal client for ccb-py.
Starts in milliseconds. Forwards keyboard input and handles window resizing over Unix Sockets.
"""
import socket
import sys
import select
import tty
import termios
import struct
import fcntl
import signal
import os

MSG_STDIN = 0x01
MSG_STDOUT = 0x02
MSG_STDERR = 0x03
MSG_RESIZE = 0x04
MSG_SIGNAL = 0x05

def send_resize(sock):
    """Obtains the current terminal window dimensions and forwards them to the daemon."""
    try:
        # Get terminal size using standard ioctl
        s = struct.pack("HHHH", 0, 0, 0, 0)
        size = fcntl.ioctl(sys.stdout.fileno(), termios.TIOCGWINSZ, s)
        rows, cols, _, _ = struct.unpack("HHHH", size)
        payload = struct.pack("!HH", rows, cols)
        header = struct.pack("!BI", MSG_RESIZE, len(payload))
        sock.sendall(header + payload)
    except Exception:
        pass

def run_client(socket_path=None):
    if socket_path is None:
        socket_path = os.path.expanduser("~/.ccb/ccb_ipc.sock")
    
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(socket_path)
    except Exception as e:
        print(f"Error: CCB Daemon is not running or socket is inaccessible ({e}).")
        print("Please start the daemon server first using 'ccb serve'")
        sys.exit(1)

    # Save original TTY settings
    fd = sys.stdin.fileno()
    try:
        old_settings = termios.tcgetattr(fd)
        tty.setraw(fd)
        is_tty = True
    except termios.error:
        is_tty = False

    # Define SIGWINCH signal handler for terminal resizing
    def sigwinch_handler(signum, frame):
        send_resize(sock)

    if is_tty:
        send_resize(sock)
        signal.signal(signal.SIGWINCH, sigwinch_handler)

    try:
        while True:
            # Multi-channel low-latency non-blocking read
            readers = [sock]
            if is_tty:
                readers.append(sys.stdin)
            
            r, _, _ = select.select(readers, [], [])
            
            if sock in r:
                header = sock.recv(5)
                if not header or len(header) < 5:
                    break
                msg_type, length = struct.unpack("!BI", header)
                # Keep receiving payload until it reaches the length
                payload = b""
                while len(payload) < length:
                    chunk = sock.recv(length - len(payload))
                    if not chunk:
                        break
                    payload += chunk
                
                if msg_type in (MSG_STDOUT, MSG_STDERR):
                    sys.stdout.buffer.write(payload)
                    sys.stdout.flush()

            if is_tty and sys.stdin in r:
                # Use raw os.read to capture complex keyboard sequences and escapes
                data = os.read(fd, 4096)
                if not data:
                    break
                header = struct.pack("!BI", MSG_STDIN, len(data))
                sock.sendall(header + data)
    except KeyboardInterrupt:
        # Forward SIGINT
        payload = struct.pack("!I", signal.SIGINT)
        sock.sendall(struct.pack("!BI", MSG_SIGNAL, len(payload)) + payload)
    except Exception as e:
        pass
    finally:
        # Restore TTY settings
        if is_tty:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        sock.close()

if __name__ == "__main__":
    run_client()
