# Path: tools/rcon_client.py
# Purpose: Minimal stdlib-only RCON client for sending console commands to a running Factorio server.

from __future__ import annotations

import argparse
import socket
import struct
import sys

SERVERDATA_AUTH = 3
SERVERDATA_AUTH_RESPONSE = 2
SERVERDATA_EXECCOMMAND = 2
SERVERDATA_RESPONSE_VALUE = 0


class RconError(RuntimeError):
    pass


class RconClient:
    def __init__(self, host: str, port: int, password: str, timeout: float = 10.0):
        self._sock = socket.create_connection((host, port), timeout=timeout)
        self._next_id = 1
        self._auth(password)

    def _send_packet(self, packet_type: int, body: str) -> int:
        packet_id = self._next_id
        self._next_id += 1
        payload = struct.pack("<ii", packet_id, packet_type) + body.encode("utf-8") + b"\x00\x00"
        self._sock.sendall(struct.pack("<i", len(payload)) + payload)
        return packet_id

    def _recv_exact(self, count: int) -> bytes:
        chunks = []
        while count > 0:
            chunk = self._sock.recv(count)
            if not chunk:
                raise RconError("Connection closed by server")
            chunks.append(chunk)
            count -= len(chunk)
        return b"".join(chunks)

    def _recv_packet(self) -> tuple[int, int, str]:
        (size,) = struct.unpack("<i", self._recv_exact(4))
        data = self._recv_exact(size)
        packet_id, packet_type = struct.unpack("<ii", data[:8])
        body = data[8:-2].decode("utf-8", errors="replace")
        return packet_id, packet_type, body

    def _auth(self, password: str) -> None:
        sent_id = self._send_packet(SERVERDATA_AUTH, password)
        packet_id, packet_type, _ = self._recv_packet()
        # Some servers send an empty RESPONSE_VALUE before the auth response.
        if packet_type == SERVERDATA_RESPONSE_VALUE:
            packet_id, packet_type, _ = self._recv_packet()
        if packet_type != SERVERDATA_AUTH_RESPONSE or packet_id != sent_id:
            raise RconError("RCON authentication failed")

    def command(self, text: str) -> str:
        sent_id = self._send_packet(SERVERDATA_EXECCOMMAND, text)
        while True:
            packet_id, packet_type, body = self._recv_packet()
            if packet_id != sent_id:
                # A long prior response can leave another packet queued.  It
                # belongs to that request, never to the command just sent.
                continue
            if packet_type != SERVERDATA_RESPONSE_VALUE:
                raise RconError(
                    f"Unexpected RCON response type {packet_type} for request {sent_id}"
                )
            return body

    def close(self) -> None:
        self._sock.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Send console commands to a Factorio server via RCON.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=27015)
    parser.add_argument("--password", required=True)
    parser.add_argument("commands", nargs="+", help="Console commands to send, in order (e.g. /snapshot)")
    args = parser.parse_args()

    client = RconClient(args.host, args.port, args.password)
    try:
        for command in args.commands:
            response = client.command(command)
            if response.strip():
                print(response.strip())
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
