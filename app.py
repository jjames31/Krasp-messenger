"""Kaonic UDP messenger for Raspberry Pi and other Linux devices."""

import argparse
from collections import deque
import json
import logging
from pathlib import Path
import re
import signal
import socket
import threading

from protocol import (
    MAX_DATAGRAM_BYTES,
    ProtocolError,
    create_ack,
    create_chat,
    decode_packet,
    encode_packet,
)
from udp import UdpTransport

DEFAULT_PORT = 6969
DEFAULT_CONTACTS = Path(__file__).with_name("contacts.json")
LOGGER = logging.getLogger("kaonic")


def validate_callsign(callsign):
    """Validate a callsign used to identify a Kaonic node."""
    if not isinstance(callsign, str) or not callsign.strip():
        raise ValueError("callsign cannot be empty")
    callsign = callsign.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,31}", callsign):
        raise ValueError(
            "callsign must be 1-32 letters, numbers, dots, underscores, or dashes"
        )
    return callsign


def parse_peer(peer_text):
    """Parse a peer in the form NAME=HOST or NAME=HOST:PORT."""
    name, separator, address = peer_text.partition("=")
    if not separator or not name or not address:
        raise ValueError("peer must use the format NAME=HOST or NAME=HOST:PORT")

    if ":" in address:
        host, port_text = address.rsplit(":", 1)
        if not host:
            raise ValueError("peer host cannot be empty")
        try:
            port = int(port_text)
        except ValueError as error:
            raise ValueError("peer port must be a number") from error
        validate_port(port)
    else:
        host = address
        port = None

    return validate_callsign(name), host, port


def validate_port(port):
    """Validate a UDP port."""
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535")


def load_contacts(path, default_port):
    """Load peers from a Kaonic contacts JSON file."""
    if not path.exists():
        LOGGER.warning("contacts file does not exist: %s", path)
        return {}

    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"could not read contacts file {path}: {error}") from error

    if not isinstance(document, dict) or not isinstance(document.get("peers"), dict):
        raise ValueError("contacts file must contain a JSON object named 'peers'")

    peers = {}
    for name, contact in document["peers"].items():
        try:
            name = validate_callsign(name)
        except ValueError as error:
            raise ValueError(f"invalid contact callsign {name!r}: {error}") from error
        if not isinstance(contact, dict):
            raise ValueError(f"contact {name!r} must be a JSON object")
        host = contact.get("host")
        if not isinstance(host, str) or not host:
            raise ValueError(f"contact {name!r} must contain a non-empty string 'host'")

        port = contact.get("port", default_port)
        try:
            validate_port(port)
        except ValueError as error:
            raise ValueError(f"contact {name!r} has invalid port: {error}") from error
        peers[name] = (host, port)

    return peers


class KaonicChat:  # pylint: disable=too-many-instance-attributes
    """A single Kaonic node that sends and receives UDP messages."""

    def __init__(self, name, listen_ip, port, peers):
        self.name = validate_callsign(name)
        self.peers = peers
        self.active_receiver = None
        self.interactive = False
        self.transport = UdpTransport(listen_ip, port, MAX_DATAGRAM_BYTES + 1)
        self.running = threading.Event()
        self.running.set()
        self.ack_condition = threading.Condition()
        self.acknowledged = deque(maxlen=256)

    @property
    def port(self):
        """Return the actual bound UDP port."""
        return self.transport.local_address[1]

    def make_message(self, target, message):
        """Create a Kaonic chat packet."""
        return create_chat(self.name, target, message)

    def listen_loop(self):
        """Receive packets until the node is stopped."""
        LOGGER.info("%s listening on UDP %s:%s", self.name, *self.transport.local_address)

        while self.running.is_set():
            try:
                data, address = self.transport.receive()
            except socket.timeout:
                continue
            except OSError:
                break

            try:
                packet = decode_packet(data)
            except ProtocolError as error:
                LOGGER.warning("rejected packet from %s:%s: %s", *address, error)
                continue

            if packet["type"] == "chat":
                self._receive_chat(packet, address)
            elif packet["type"] == "ack":
                self._receive_ack(packet)

    def _receive_chat(self, packet, address):
        if packet["recipient"] not in (self.name, "*"):
            LOGGER.debug("ignored message addressed to %s", packet["recipient"])
            return

        if self.interactive:
            print(f"\n[{packet['sender']} -> {self.name}] {packet['body']}")
            print(self.terminal_prompt(), end="", flush=True)
        else:
            LOGGER.info("message from %s: %s", packet["sender"], packet["body"])
        ack = create_ack(self.name, packet["id"])
        try:
            self.transport.send(encode_packet(ack), address)
        except OSError as error:
            LOGGER.warning("could not acknowledge %s: %s", packet["id"], error)

    def _receive_ack(self, packet):
        message_id = packet["message_id"]
        if self.interactive:
            print(f"\n[delivered to {packet['sender']}]")
            print(self.terminal_prompt(), end="", flush=True)
        else:
            LOGGER.info("%s acknowledged message %s", packet["sender"], message_id)
        with self.ack_condition:
            self.acknowledged.append(message_id)
            self.ack_condition.notify_all()

    def send_to(self, target, message):
        """Send a message and return its ID, or return None on failure."""
        if target not in self.peers:
            LOGGER.error("unknown peer %s; known peers: %s", target, self.peer_names())
            return None

        packet = self.make_message(target, message)
        try:
            self.transport.send(encode_packet(packet), self.peers[target])
        except (OSError, ProtocolError) as error:
            LOGGER.error("could not send to %s: %s", target, error)
            return None

        if not self.interactive:
            LOGGER.info("sent message %s to %s", packet["id"], target)
        return packet["id"]

    def wait_for_ack(self, message_id, timeout):
        """Wait for an acknowledgement from a peer."""
        with self.ack_condition:
            received = self.ack_condition.wait_for(
                lambda: message_id in self.acknowledged,
                timeout=timeout,
            )
            if received:
                self.acknowledged.remove(message_id)
            return received

    def peer_names(self):
        """Return configured peer names for display."""
        return ", ".join(sorted(self.peers)) or "(none)"

    def define_receiver(self, peer_text, default_port):
        """Define a receiver from NAME=HOST[:PORT] and select it."""
        name, host, port = parse_peer(peer_text)
        self.peers[name] = (host, port or default_port)
        self.active_receiver = name
        return name

    def select_receiver(self, name):
        """Select an existing receiver."""
        if name not in self.peers:
            return False
        self.active_receiver = name
        return True

    def terminal_prompt(self):
        """Return the interactive chat prompt."""
        return f"{self.name} -> {self.active_receiver or 'no-receiver'} > "

    def close(self):
        """Stop the node and close its socket."""
        self.running.clear()
        self.transport.close()

    def input_loop(self, default_port, initial_receiver=None):
        """Run the interactive callsign-to-callsign terminal."""
        self.interactive = True
        print()
        print("KAONIC TERMINAL")
        print(f"Sender callsign: {self.name}")
        print(f"Listening on UDP port {self.port}")
        print("Type /help for commands.")
        print()

        if initial_receiver:
            self._set_receiver_from_input(initial_receiver, default_port)
        elif self.peers:
            print(f"Known receivers: {self.peer_names()}")
            receiver = self._read_line("Select receiver: ")
            if receiver:
                self._set_receiver_from_input(receiver, default_port)
        else:
            print("No receivers are configured.")
            receiver = self._read_line("Define receiver as CALLSIGN=HOST[:PORT]: ")
            if receiver:
                self._set_receiver_from_input(receiver, default_port)

        while self.running.is_set():
            line = self._read_line(self.terminal_prompt())

            if line in (None, "/quit"):
                break
            if not line:
                continue
            if line.startswith("/"):
                self._run_terminal_command(line, default_port)
            elif self.active_receiver:
                self.send_to(self.active_receiver, line)
            else:
                print("Select a receiver first with /receiver CALLSIGN=HOST[:PORT]")

        self.interactive = False

    @staticmethod
    def _read_line(prompt):
        try:
            return input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            return None

    def _set_receiver_from_input(self, receiver, default_port):
        if receiver.startswith("/receiver "):
            receiver = receiver.removeprefix("/receiver ").strip()
        try:
            if "=" in receiver:
                name = self.define_receiver(receiver, default_port)
                host, port = self.peers[name]
                print(f"Receiver defined: {name} at {host}:{port}")
            elif self.select_receiver(receiver):
                print(f"Receiver selected: {receiver}")
            else:
                print(f"Unknown receiver: {receiver}")
                print("Define it with /receiver CALLSIGN=HOST[:PORT]")
        except ValueError as error:
            print(f"Invalid receiver: {error}")

    def _run_terminal_command(self, line, default_port):  # pylint: disable=too-many-branches
        command, _, argument = line.partition(" ")
        argument = argument.strip()

        if command in ("/help", "/?"):
            print("  Type a message              send to the active receiver")
            print("  /receiver CALLSIGN          select a known receiver")
            print("  /receiver CALLSIGN=HOST     define and select a receiver")
            print("  /to CALLSIGN message        send one message to another receiver")
            print("  /callsign CALLSIGN          change your sender callsign")
            print("  /contacts                   list known receivers")
            print("  /status                     show sender and receiver")
            print("  /quit                       close the terminal")
        elif command == "/receiver":
            if argument:
                self._set_receiver_from_input(argument, default_port)
            else:
                print("Usage: /receiver CALLSIGN or /receiver CALLSIGN=HOST[:PORT]")
        elif command == "/to":
            target, separator, message = argument.partition(" ")
            if separator and message:
                self.send_to(target, message)
            else:
                print("Usage: /to CALLSIGN message")
        elif command in ("/contacts", "/peers"):
            for name, (host, port) in sorted(self.peers.items()):
                selected = " *" if name == self.active_receiver else ""
                print(f"{name}: {host}:{port}{selected}")
            if not self.peers:
                print("(no receivers defined)")
        elif command == "/callsign":
            try:
                self.name = validate_callsign(argument)
                print(f"Sender callsign changed to {self.name}")
            except ValueError as error:
                print(f"Invalid callsign: {error}")
        elif command == "/status":
            print(f"Sender: {self.name}")
            print(f"Receiver: {self.active_receiver or '(not selected)'}")
            print(f"UDP port: {self.port}")
        else:
            print("Unknown command. Type /help for commands.")


def build_parser():
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--callsign",
        "--name",
        dest="callsign",
        help="sender callsign; defaults to the Pi hostname",
    )
    parser.add_argument(
        "--receiver",
        help="initial receiver CALLSIGN or CALLSIGN=HOST[:PORT]",
    )
    parser.add_argument("--listen-ip", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--contacts", type=Path, default=DEFAULT_CONTACTS)
    parser.add_argument(
        "--peer",
        action="append",
        default=[],
        help="override/add peer in format NAME=HOST or NAME=HOST:PORT",
    )

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--daemon", action="store_true", help="run headless until stopped")
    mode.add_argument(
        "--send",
        nargs=2,
        metavar=("NAME", "MESSAGE"),
        help="send one message and wait for acknowledgement",
    )
    parser.add_argument("--ack-timeout", type=float, default=3.0)
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
    )
    return parser


def configure_logging(level):
    """Configure console logging suitable for journald."""
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def prompt_for_callsign(default):
    """Prompt an interactive user to create a sender callsign."""
    while True:
        try:
            callsign = input(f"Create sender callsign [{default}]: ").strip() or default
        except (EOFError, KeyboardInterrupt):
            return default
        try:
            return validate_callsign(callsign)
        except ValueError as error:
            print(f"Invalid callsign: {error}")


def run_daemon(chat):
    """Wait for SIGINT or SIGTERM while the listener runs."""
    stopped = threading.Event()

    def stop(_signal_number, _frame):
        stopped.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    LOGGER.info("Kaonic daemon ready; peers: %s", chat.peer_names())
    stopped.wait()


def main():
    """Run the Kaonic messenger."""
    parser = build_parser()
    args = parser.parse_args()
    configure_logging(args.log_level)

    try:
        validate_port(args.port)
        peers = load_contacts(args.contacts, args.port)
        for peer_text in args.peer:
            name, host, port = parse_peer(peer_text)
            peers[name] = (host, port or args.port)
    except ValueError as error:
        parser.error(str(error))

    default_callsign = socket.gethostname()
    callsign = args.callsign or default_callsign
    if not args.daemon and not args.send and not args.callsign:
        callsign = prompt_for_callsign(default_callsign)

    try:
        listen_port = 0 if args.send else args.port
        chat = KaonicChat(callsign, args.listen_ip, listen_port, peers)
    except ValueError as error:
        parser.error(str(error))
    except OSError as error:
        parser.error(f"could not bind UDP port: {error}")

    listener = threading.Thread(target=chat.listen_loop, name="kaonic-udp", daemon=True)
    listener.start()

    exit_code = 0
    try:
        if args.daemon:
            run_daemon(chat)
        elif args.send:
            message_id = chat.send_to(*args.send)
            if not message_id or not chat.wait_for_ack(message_id, args.ack_timeout):
                LOGGER.error("message was not acknowledged within %.1f seconds", args.ack_timeout)
                exit_code = 1
        else:
            chat.input_loop(args.port, args.receiver)
    finally:
        chat.close()
        listener.join(timeout=1)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
