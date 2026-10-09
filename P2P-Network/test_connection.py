"""Automated localhost smoke test: handshake, bidirectional messages, file transfer."""
import tempfile
import time
from pathlib import Path
from p2p_node import P2PNode, DOWNLOAD_DIR

A, B = [], []
a = P2PNode('Alice', 0, A.append)
b = P2PNode('Bob', 0, B.append)

def wait_until(condition, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return
        time.sleep(.03)
    raise AssertionError('Timed out: ' + repr((A[-6:], B[-6:])))

try:
    a.start(); b.start()
    # Port 0 lets OS choose available ports; update advertised ports.
    a.listen_port = a.server_socket.getsockname()[1]
    b.listen_port = b.server_socket.getsockname()[1]
    a.connect_to_peer('127.0.0.1', b.listen_port)
    wait_until(lambda: len(a.connections) == len(b.connections) == 1)
    assert a.send_text(b.peer_id, 'hello from Alice')
    assert b.send_text(a.peer_id, 'hello from Bob')
    wait_until(lambda: any('hello from Alice' in str(x) for x in B) and any('hello from Bob' in str(x) for x in A))
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'smoke_test_payload.bin'
        payload = b'abc123' * 20000
        path.write_bytes(payload)
        assert a.send_file(b.peer_id, str(path))
        wait_until(lambda: any('FILE RECEIVED' in str(x) and 'smoke_test_payload.bin' in str(x) for x in B))
        received = sorted(DOWNLOAD_DIR.glob('smoke_test_payload*.bin'), key=lambda x: x.stat().st_mtime)[-1]
        assert received.read_bytes() == payload
        received.unlink()
    print('PASS: bidirectional handshake, two-way messaging, and binary file transfer')
finally:
    a.stop(); b.stop()
