"""Lyra — remote control of OBS Studio (OBS 28 or newer has the WebSocket server built in).

Lyra does not capture the screen itself: its Record button, the voice commands and the keyboard shortcuts start / stop /
pause OBS, which records with its own capture and encoder. In OBS: Tools -> WebSocket Server Settings -> tick "Enable
WebSocket server" (port 4455; if it asks for a password, type the same one in Lyra's Settings -> Recordings).

Only the standard library is used (a minimal WebSocket client, protocol "obswebsocket.json", v5): nothing to install.
Everything stays on this PC (localhost) unless the person points Lyra at another machine.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct
import threading
import uuid

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"          # RFC 6455


class ObsError(Exception):
    """Something Lyra can say out loud: OBS is not running, wrong password, a request OBS refused."""


class ObsRefused(ObsError):
    """OBS is fine but said no to this request (nothing to reconnect for)."""


def auth_string(password: str, salt: str, challenge: str) -> str:
    """OBS WebSocket 5 authentication: base64(sha256(base64(sha256(password + salt)) + challenge))."""
    secret = base64.b64encode(hashlib.sha256((password + salt).encode()).digest()).decode()
    return base64.b64encode(hashlib.sha256((secret + challenge).encode()).digest()).decode()


class _WS:
    """Just enough of a WebSocket client: text frames, ping/pong, close."""

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET / HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
                           f"Sec-WebSocket-Protocol: obswebsocket.json\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ObsError("OBS closed the connection")
            head += chunk
            if len(head) > 16384:
                raise ObsError("that is not an OBS WebSocket server")
        text, _, self._buf = head.partition(b"\r\n\r\n")
        lines = text.decode("latin-1").split("\r\n")
        accept = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
        if " 101 " not in lines[0] or accept.lower() not in text.decode("latin-1").lower():
            raise ObsError("that is not an OBS WebSocket server")

    def _read(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ObsError("OBS closed the connection")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def send(self, text: str | bytes, opcode: int = 1) -> None:
        data = text.encode() if isinstance(text, str) else text
        n = len(data)
        head = bytes([0x80 | opcode])
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", n)
        mask = os.urandom(4)
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self) -> str:
        """The next complete text message (answers pings, raises ObsError when the server closes)."""
        message = b""
        while True:
            b1, b2 = self._read(2)
            fin, opcode, n = b1 & 0x80, b1 & 0x0F, b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            data = self._read(n)
            if opcode == 8:
                code = struct.unpack(">H", data[:2])[0] if len(data) >= 2 else 0
                raise ObsError("wrong password" if code == 4009 else f"OBS closed the connection ({code})")
            if opcode == 9:
                self.send(data, 0xA)                      # answer a ping with a pong carrying the same bytes
                continue
            if opcode == 0xA:
                continue
            message += data
            if fin:
                return message.decode("utf-8", "replace")

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


class ObsClient:
    """One connection to OBS, opened when first needed and again after it drops. Thread-safe."""

    def __init__(self, host: str = "127.0.0.1", port: int = 4455, password: str = "", timeout: float = 3.0) -> None:
        self.host, self.port, self.password, self.timeout = host or "127.0.0.1", int(port or 4455), password or "", timeout
        self._ws: _WS | None = None
        self._lock = threading.Lock()

    def same_as(self, host: str, port: int, password: str) -> bool:
        return (self.host, self.port, self.password) == (host or "127.0.0.1", int(port or 4455), password or "")

    def close(self) -> None:
        with self._lock:
            if self._ws is not None:
                self._ws.close()
                self._ws = None

    def _open(self) -> _WS:
        try:
            ws = _WS(self.host, self.port, self.timeout)
        except (ConnectionRefusedError, socket.timeout, TimeoutError, OSError) as e:
            raise ObsError("OBS isn't running, or its WebSocket server is switched off "
                           "(OBS: Tools → WebSocket Server Settings → Enable)") from e
        try:
            hello = json.loads(ws.recv())
            if hello.get("op") != 0:
                raise ObsError("OBS did not say hello")
            ident: dict = {"rpcVersion": 1, "eventSubscriptions": 0}          # no events: Lyra only asks questions
            need = hello["d"].get("authentication")
            if need:
                if not self.password:
                    raise ObsError("OBS asks for a password: type it in Lyra's Settings → Recordings")
                ident["authentication"] = auth_string(self.password, need["salt"], need["challenge"])
            ws.send(json.dumps({"op": 1, "d": ident}))
            reply = json.loads(ws.recv())
            if reply.get("op") != 2:
                raise ObsError("OBS did not accept the connection")
        except (OSError, ValueError, KeyError) as e:
            ws.close()
            raise ObsError("OBS did not answer properly") from e
        except ObsError:
            ws.close()
            raise
        return ws

    def request(self, kind: str, data: dict | None = None) -> dict:
        """One OBS request -> its responseData. Raises ObsError with a sentence Lyra can say."""
        with self._lock:
            for attempt in (1, 2):
                had_connection = self._ws is not None
                try:
                    if self._ws is None:
                        self._ws = self._open()
                    rid = uuid.uuid4().hex
                    self._ws.send(json.dumps({"op": 6, "d": {"requestType": kind, "requestId": rid, "requestData": data or {}}}))
                    while True:
                        msg = json.loads(self._ws.recv())
                        if msg.get("op") == 7 and msg["d"].get("requestId") == rid:
                            break
                    st = msg["d"].get("requestStatus", {})
                    if not st.get("result"):
                        raise ObsRefused(str(st.get("comment") or f"OBS refused {kind} (code {st.get('code')})"))
                    return msg["d"].get("responseData") or {}
                except ObsRefused:
                    raise
                except (ObsError, OSError, ValueError) as e:
                    if self._ws is not None:
                        self._ws.close()
                    self._ws = None
                    if had_connection and attempt == 1:       # a connection that had gone stale: once more with a fresh one
                        continue
                    raise e if isinstance(e, ObsError) else ObsError("lost the connection to OBS")
        raise ObsError("OBS did not answer")
