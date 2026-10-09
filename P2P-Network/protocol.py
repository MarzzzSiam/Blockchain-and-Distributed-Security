"""
Application-level protocol definitions.

Control messages are JSON objects framed as:
    [4-byte big-endian payload length][JSON payload]

Supported message types:
    hello
    hello_ack
    text
    file
"""

HELLO = "hello"
HELLO_ACK = "hello_ack"
TEXT = "text"
FILE = "file"

SUPPORTED_MESSAGE_TYPES = {
    HELLO,
    HELLO_ACK,
    TEXT,
    FILE,
}
