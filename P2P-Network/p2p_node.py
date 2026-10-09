"""
Core P2P networking layer.

Each P2PNode acts as:
1. TCP server - accepts incoming peer connections.
2. TCP client - initiates outgoing peer connections.

Protocol:
    Framed JSON control messages:
        [4-byte unsigned big-endian length][JSON bytes]

For a file:
    1. Framed JSON metadata message.
    2. Exactly filesize raw bytes, sent in chunks.
"""

import hashlib
import json
import os
import socket
import struct
import threading
import uuid
from pathlib import Path


HOST = "0.0.0.0"
DOWNLOAD_DIR = Path(__file__).resolve().parent / "downloads"
CHUNK_SIZE = 64 * 1024
MAX_JSON_MESSAGE = 4 * 1024 * 1024


class PeerConnection:
    """Stores one active TCP connection."""

    def __init__(self, sock, address, peer_info=None, outgoing=False):
        self.sock = sock
        self.address = address
        self.peer_info = peer_info or {}
        self.send_lock = threading.Lock()
        self.closed = False
        self.outgoing = outgoing

    def close(self):
        if self.closed:
            return

        self.closed = True
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

        try:
            self.sock.close()
        except OSError:
            pass


class P2PNode:
    def __init__(self, peer_name, listen_port, event_callback):
        self.peer_name = peer_name
        self.listen_port = listen_port
        self.peer_id = uuid.uuid4().hex[:8]
        self.event_callback = event_callback

        self.server_socket = None
        self.running = False
        self.accept_thread = None

        self.connections = {}
        self.connections_lock = threading.Lock()

        DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    # =========================================================
    # Public lifecycle
    # =========================================================

    def start(self):
        if self.running:
            return

        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        # 0.0.0.0 is intentional here: it allows peers on the same
        # LAN to connect through this computer's LAN IPv4 address.
        server.bind((HOST, self.listen_port))
        server.listen(10)
        server.settimeout(1.0)

        self.server_socket = server
        self.running = True

        self.accept_thread = threading.Thread(
            target=self._accept_loop,
            name="P2P-Accept-Thread",
            daemon=True,
        )
        self.accept_thread.start()

        self._emit("status", f"Listening on TCP port {self.listen_port}")
        self._emit("local_ip", self.get_local_ip())

    def stop(self):
        self.running = False

        if self.server_socket:
            try:
                self.server_socket.close()
            except OSError:
                pass
            self.server_socket = None

        with self.connections_lock:
            connections = list(self.connections.values())
            self.connections.clear()

        for connection in connections:
            connection.close()

        self._emit("peers", [])

    # =========================================================
    # TCP server
    # =========================================================

    def _accept_loop(self):
        while self.running and self.server_socket:
            try:
                client_socket, address = self.server_socket.accept()
            except socket.timeout:
                continue
            except OSError:
                break

            connection = PeerConnection(client_socket, address)

            self._emit(
                "log",
                f"[CONNECTION] Incoming connection from {address[0]}:{address[1]}",
            )

            thread = threading.Thread(
                target=self._handle_connection,
                args=(connection,),
                name=f"Peer-{address[0]}",
                daemon=True,
            )
            thread.start()

    # =========================================================
    # TCP client
    # =========================================================

    def connect_to_peer(self, ip, port):
        thread = threading.Thread(
            target=self._connect_worker,
            args=(ip, port),
            daemon=True,
        )
        thread.start()

    def _connect_worker(self, ip, port):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(8)

        try:
            sock.connect((ip, port))
            sock.settimeout(None)

            connection = PeerConnection(sock, (ip, port), outgoing=True)

            self._emit("log", f"[CONNECT] Connected to {ip}:{port}")

            # The outgoing side sends HELLO immediately.
            self._send_json(
                connection,
                {
                    "type": "hello",
                    "peer_id": self.peer_id,
                    "peer_name": self.peer_name,
                    "port": self.listen_port,
                },
            )

            self._start_connection_handler(connection)

        except socket.timeout:
            self._emit("log", f"[ERROR] Connection timed out: {ip}:{port}")
            try:
                sock.close()
            except OSError:
                pass
        except ConnectionRefusedError:
            self._emit("log", f"[ERROR] Connection refused: {ip}:{port}")
            try:
                sock.close()
            except OSError:
                pass
        except OSError as exc:
            self._emit("log", f"[ERROR] Could not connect to {ip}:{port}: {exc}")
            try:
                sock.close()
            except OSError:
                pass

    # =========================================================
    # Connection handler
    # =========================================================

    def _start_connection_handler(self, connection):
        thread = threading.Thread(
            target=self._handle_connection,
            args=(connection,),
            daemon=True,
        )
        thread.start()

    def _handle_connection(self, connection):
        peer_id = None

        try:
            # Incoming connections receive HELLO; outgoing connections receive
            # HELLO_ACK because they already sent HELLO in _connect_worker.
            message = self._recv_json(connection.sock)
            expected = "hello_ack" if connection.outgoing else "hello"
            if not message or message.get("type") != expected:
                raise ValueError(f"Expected {expected.upper()} during handshake.")

            peer_id = str(message.get("peer_id", "")).strip()
            peer_name = str(message.get("peer_name", "")).strip()
            peer_port = int(message.get("port", connection.address[1]))

            if not peer_id or not peer_name:
                raise ValueError("Invalid HELLO information.")

            # Prevent accidentally replacing our own identity.
            if peer_id == self.peer_id:
                raise ValueError("Peer ID matches local peer ID.")

            peer_info = {
                "id": peer_id,
                "name": peer_name,
                "ip": connection.address[0],
                "port": peer_port,
            }
            connection.peer_info = peer_info

            with self.connections_lock:
                old = self.connections.get(peer_id)
                self.connections[peer_id] = connection

            if old and old is not connection:
                old.close()

            self._emit(
                "log",
                f"[HANDSHAKE] Connected with {peer_name} [{peer_id}] "
                f"at {connection.address[0]}:{peer_port}",
            )
            self._emit_peer_list()

            # Only the incoming side sends HELLO_ACK. The outgoing side
            # has already received that acknowledgement above.
            if not connection.outgoing:
                self._send_json(
                    connection,
                    {
                        "type": "hello_ack",
                        "peer_id": self.peer_id,
                        "peer_name": self.peer_name,
                        "port": self.listen_port,
                    },
                )

            while self.running and not connection.closed:
                message = self._recv_json(connection.sock)

                if message is None:
                    break

                message_type = message.get("type")

                if message_type == "hello_ack":
                    self._handle_hello_ack(connection, message)

                elif message_type == "text":
                    self._handle_text(connection, message)

                elif message_type == "file":
                    self._handle_file(connection, message)

                else:
                    self._emit(
                        "log",
                        f"[ERROR] Unknown message type from {peer_name}: {message_type}",
                    )

        except (ConnectionResetError, BrokenPipeError):
            self._emit(
                "log",
                f"[DISCONNECT] {connection.peer_info.get('name', connection.address[0])} disconnected."
            )
        except (OSError, ValueError, struct.error, json.JSONDecodeError) as exc:
            if self.running:
                self._emit(
                    "log",
                    f"[ERROR] Connection error from {connection.address[0]}: {exc}",
                )
        finally:
            connection.close()

            if peer_id:
                with self.connections_lock:
                    if self.connections.get(peer_id) is connection:
                        del self.connections[peer_id]

                self._emit_peer_list()

    def _handle_hello_ack(self, connection, message):
        peer_id = str(message.get("peer_id", "")).strip()
        peer_name = str(message.get("peer_name", "")).strip()

        if not peer_id or not peer_name:
            raise ValueError("Invalid HELLO_ACK.")

        peer_port = int(message.get("port", connection.address[1]))

        peer_info = {
            "id": peer_id,
            "name": peer_name,
            "ip": connection.address[0],
            "port": peer_port,
        }

        with self.connections_lock:
            old = self.connections.get(peer_id)

            # If this is an existing connection, don't replace the same socket.
            if old is None or old is connection:
                self.connections[peer_id] = connection
                connection.peer_info = peer_info

        self._emit(
            "log",
            f"[HANDSHAKE] HELLO_ACK received from {peer_name} [{peer_id}]",
        )
        self._emit_peer_list()

    def _handle_text(self, connection, message):
        sender_name = message.get("sender_name", connection.peer_info.get("name", "Unknown"))
        text = message.get("message", "")

        self._emit("log", f"[MESSAGE] {sender_name}: {text}")

    def _handle_file(self, connection, message):
        filename = os.path.basename(str(message.get("filename", "")).strip())

        try:
            filesize = int(message.get("filesize", -1))
        except (TypeError, ValueError):
            raise ValueError("Invalid file size.")

        if not filename:
            raise ValueError("Invalid filename.")

        if filesize < 0:
            raise ValueError("Invalid file size.")

        if filesize > 20 * 1024 * 1024 * 1024:
            raise ValueError("File is too large for this educational application.")

        sender_name = message.get(
            "sender_name",
            connection.peer_info.get("name", "Unknown"),
        )

        self._emit(
            "log",
            f"[FILE] Receiving '{filename}' ({filesize} bytes) from {sender_name}...",
        )

        destination = self._unique_download_path(filename)

        remaining = filesize
        try:
            with open(destination, "wb") as output:
                while remaining > 0:
                    chunk = connection.sock.recv(min(CHUNK_SIZE, remaining))
                    if not chunk:
                        raise ConnectionError(
                            "Peer disconnected before file transfer completed."
                        )

                    output.write(chunk)
                    remaining -= len(chunk)

        except Exception:
            try:
                if destination.exists():
                    destination.unlink()
            except OSError:
                pass
            raise

        self._emit(
            "log",
            f"[FILE RECEIVED] {filename} saved to downloads/{destination.name}",
        )

    # =========================================================
    # Sending
    # =========================================================

    def send_text(self, peer_id, message):
        connection = self._get_connection(peer_id)

        if not connection:
            self._emit("log", "[ERROR] Selected peer is no longer connected.")
            self._emit_peer_list()
            return False

        try:
            self._send_json(
                connection,
                {
                    "type": "text",
                    "sender_id": self.peer_id,
                    "sender_name": self.peer_name,
                    "message": message,
                },
            )

            self._emit(
                "log",
                f"[YOU -> {connection.peer_info.get('name', peer_id)}] {message}",
            )
            return True

        except OSError as exc:
            self._emit("log", f"[ERROR] Failed to send message: {exc}")
            self._remove_connection(peer_id, connection)
            return False

    def send_file(self, peer_id, file_path):
        connection = self._get_connection(peer_id)

        if not connection:
            self._emit("log", "[ERROR] Selected peer is no longer connected.")
            self._emit_peer_list()
            return False

        path = Path(file_path)

        if not path.is_file():
            self._emit("log", "[ERROR] File does not exist.")
            return False

        try:
            filesize = path.stat().st_size
        except OSError as exc:
            self._emit("log", f"[ERROR] Cannot read file information: {exc}")
            return False

        thread = threading.Thread(
            target=self._send_file_worker,
            args=(connection, path, filesize),
            daemon=True,
        )
        thread.start()
        return True

    def _send_file_worker(self, connection, path, filesize):
        filename = path.name
        peer_name = connection.peer_info.get("name", "peer")

        try:
            # Keep the metadata and all file bytes together so another
            # message cannot be inserted into the middle of a file transfer.
            with connection.send_lock:
                metadata = {
                    "type": "file",
                    "sender_id": self.peer_id,
                    "sender_name": self.peer_name,
                    "filename": filename,
                    "filesize": filesize,
                }
                self._send_json_unlocked(connection, metadata)

                with open(path, "rb") as source:
                    while True:
                        chunk = source.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        connection.sock.sendall(chunk)

            self._emit(
                "log",
                f"[FILE SENT] {filename} ({filesize} bytes) -> {peer_name}",
            )

        except (OSError, ConnectionError) as exc:
            self._emit("log", f"[ERROR] File transfer failed: {exc}")
            self._remove_connection(connection.peer_info.get("id"), connection)

    # =========================================================
    # Framing
    # =========================================================

    def _send_json(self, connection, message):
        with connection.send_lock:
            self._send_json_unlocked(connection, message)

    @staticmethod
    def _send_json_unlocked(connection, message):
        payload = json.dumps(message, separators=(",", ":")).encode("utf-8")

        if len(payload) > MAX_JSON_MESSAGE:
            raise ValueError("JSON message is too large.")

        header = struct.pack("!I", len(payload))
        connection.sock.sendall(header)
        connection.sock.sendall(payload)

    @staticmethod
    def _recv_exact(sock, amount):
        data = bytearray()

        while len(data) < amount:
            chunk = sock.recv(amount - len(data))
            if not chunk:
                return None
            data.extend(chunk)

        return bytes(data)

    def _recv_json(self, sock):
        header = self._recv_exact(sock, 4)

        if header is None:
            return None

        payload_length = struct.unpack("!I", header)[0]

        if payload_length <= 0 or payload_length > MAX_JSON_MESSAGE:
            raise ValueError("Invalid JSON message length.")

        payload = self._recv_exact(sock, payload_length)

        if payload is None:
            return None

        return json.loads(payload.decode("utf-8"))

    # =========================================================
    # Peer management
    # =========================================================

    def _get_connection(self, peer_id):
        with self.connections_lock:
            return self.connections.get(peer_id)

    def _remove_connection(self, peer_id, connection):
        if peer_id:
            with self.connections_lock:
                if self.connections.get(peer_id) is connection:
                    del self.connections[peer_id]

        connection.close()
        self._emit_peer_list()

    def _emit_peer_list(self):
        with self.connections_lock:
            peers = [dict(connection.peer_info) for connection in self.connections.values()]

        peers.sort(key=lambda peer: peer.get("name", "").lower())
        self._emit("peers", peers)

    def _emit(self, event_type, *data):
        try:
            self.event_callback((event_type, *data))
        except Exception:
            # The networking layer should not crash because the UI callback failed.
            pass

    @staticmethod
    def _unique_download_path(filename):
        destination = DOWNLOAD_DIR / filename

        if not destination.exists():
            return destination

        stem = destination.stem
        suffix = destination.suffix

        counter = 1
        while True:
            candidate = DOWNLOAD_DIR / f"{stem}_{counter}{suffix}"
            if not candidate.exists():
                return candidate
            counter += 1

    @staticmethod
    def get_local_ip():
        """Best-effort LAN IPv4 discovery without sending application data."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        try:
            # This does not need to successfully send a packet. The OS uses
            # routing information to select the local interface.
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
        except OSError:
            return "127.0.0.1"
        finally:
            sock.close()
