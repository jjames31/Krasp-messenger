"""Kaonic messenger wire protocol."""

import json
import time
import uuid

PROTOCOL_NAME = "kaonic"
PROTOCOL_VERSION = 1
MAX_DATAGRAM_BYTES = 8192
MAX_BODY_BYTES = 4096


class ProtocolError(ValueError):
    """Raised when a Kaonic packet is invalid."""


def create_chat(sender, recipient, body):
    """Create a Kaonic chat packet."""
    packet = {
        "protocol": PROTOCOL_NAME,
        "version": PROTOCOL_VERSION,
        "type": "chat",
        "id": str(uuid.uuid4()),
        "sent_at": int(time.time()),
        "sender": sender,
        "recipient": recipient,
        "body": body,
    }
    validate_packet(packet)
    return packet


def create_ack(sender, message_id):
    """Create an acknowledgement for a chat packet."""
    packet = {
        "protocol": PROTOCOL_NAME,
        "version": PROTOCOL_VERSION,
        "type": "ack",
        "message_id": message_id,
        "sender": sender,
    }
    validate_packet(packet)
    return packet


def encode_packet(packet):
    """Validate and encode a packet for UDP transport."""
    validate_packet(packet)
    data = json.dumps(packet, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(data) > MAX_DATAGRAM_BYTES:
        raise ProtocolError(f"packet exceeds {MAX_DATAGRAM_BYTES} bytes")
    return data


def decode_packet(data):
    """Decode and validate a UDP datagram."""
    if len(data) > MAX_DATAGRAM_BYTES:
        raise ProtocolError(f"packet exceeds {MAX_DATAGRAM_BYTES} bytes")

    try:
        packet = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProtocolError("packet is not valid UTF-8 JSON") from error

    validate_packet(packet)
    return packet


def validate_packet(packet):
    """Validate a decoded Kaonic packet."""
    if not isinstance(packet, dict):
        raise ProtocolError("packet must be a JSON object")
    if packet.get("protocol") != PROTOCOL_NAME:
        raise ProtocolError("packet is not a Kaonic message")
    if packet.get("version") != PROTOCOL_VERSION:
        raise ProtocolError(f"unsupported protocol version: {packet.get('version')!r}")

    packet_type = packet.get("type")
    if packet_type == "chat":
        _require_text(packet, "id")
        _require_text(packet, "sender")
        _require_text(packet, "recipient")
        body = _require_text(packet, "body", allow_empty=True)
        if len(body.encode("utf-8")) > MAX_BODY_BYTES:
            raise ProtocolError(f"message body exceeds {MAX_BODY_BYTES} bytes")
        sent_at = packet.get("sent_at")
        if not isinstance(sent_at, int) or isinstance(sent_at, bool):
            raise ProtocolError("chat field 'sent_at' must be an integer")
    elif packet_type == "ack":
        _require_text(packet, "message_id")
        _require_text(packet, "sender")
    else:
        raise ProtocolError(f"unsupported packet type: {packet_type!r}")


def _require_text(packet, field, allow_empty=False):
    value = packet.get(field)
    if not isinstance(value, str) or (not allow_empty and not value):
        raise ProtocolError(f"field {field!r} must be a non-empty string")
    return value
