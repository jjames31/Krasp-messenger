"""Krasp UDP messenger for Linux devices."""

import argparse
from collections import deque
from datetime import datetime
import ipaddress
import json
import logging
from pathlib import Path
import re
import signal
import socket
import threading
import time

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
LOGGER = logging.getLogger("krasp")
ANSI_RESET = "\033[0m"
ANSI_BOLD = "\033[1m"
ANSI_DIM = "\033[90m"
ANSI_GREEN = "\033[32m"
ANSI_RED = "\033[31m"


def _fmt_time(unix_ts):
    try:
        return datetime.fromtimestamp(int(unix_ts)).strftime("%H:%M")
    except (OSError, OverflowError, TypeError, ValueError):
        return "--:--"


def _warn(message):
    print(f"{ANSI_RED}! {message}{ANSI_RESET}")


def _info(message):
    print(f"{ANSI_DIM}  {message}{ANSI_RESET}")


def validate_callsign(callsign):
    if not isinstance(callsign, str) or not callsign.strip():
        raise ValueError("callsign cannot be empty")
    callsign = callsign.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,31}", callsign):
        raise ValueError(
            "callsign must be 1-32 letters, numbers, dots, underscores, or dashes"
        )
    return callsign


def parse_peer(peer_text):
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
    # bool is an int subclass, but True/False should not be accepted as ports.
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535")


def load_contacts(path, default_port, warn_missing=True):
    if not path:
        return {}
    path = Path(path)
    if not path.exists():
        if warn_missing:
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


def save_peer(path, name, host, port):
    path = Path(path)
    peers = load_contacts(path, port, warn_missing=False)
    peers[name] = (host, port)
    document = {
        "peers": {
            peer_name: {"host": peer_host, "port": peer_port}
            for peer_name, (peer_host, peer_port) in sorted(peers.items())
        }
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def append_inbox(path, packet, address):
    if not path:
        return

    entry = {
        "received_at": int(time.time()),
        "sent_at": packet.get("sent_at"),
        "id": packet.get("id"),
        "sender": packet.get("sender"),
        "recipient": packet.get("recipient"),
        "body": packet.get("body"),
        "source": f"{address[0]}:{address[1]}",
    }

    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as inbox_file:
            inbox_file.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as error:
        LOGGER.warning("could not write inbox entry to %s: %s", path, error)


def show_inbox(paths, limit):
    entries = []
    for path in paths:
        path = Path(path)
        if not path.exists():
            continue
        try:
            with path.open(encoding="utf-8") as inbox_file:
                for line in inbox_file:
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    entry["_path"] = str(path)
                    entries.append(entry)
        except OSError as error:
            _warn(f"Could not read inbox {path}: {error}")

    if not entries:
        _info("No inbox messages found")
        return 0

    entries.sort(key=lambda item: item.get("received_at") or item.get("sent_at") or 0)
    for entry in entries[-limit:]:
        ts = _fmt_time(entry.get("received_at") or entry.get("sent_at"))
        sender = entry.get("sender") or "unknown"
        body = str(entry.get("body") or "").replace("\n", " ")
        print(f"{ts}  {sender}: {body}")
    return 0


def is_ipv4_address(host):
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


class KaonicChat:
    def __init__(
        self,
        name,
        listen_ip,
        port,
        peers,
        contacts_path=None,
        contact_save_path=None,
        inbox_path=None,
        default_port=DEFAULT_PORT,
        resolve_timeout=3.0,
    ):
        self.name = validate_callsign(name)
        self.peers = peers
        self.contacts_path = Path(contacts_path) if contacts_path else None
        self.contact_save_path = Path(contact_save_path) if contact_save_path else None
        self.inbox_path = Path(inbox_path) if inbox_path else None
        self.default_port = default_port
        self.resolve_timeout = max(0.1, float(resolve_timeout))
        self.peer_lock = threading.RLock()
        self.active_receiver = None
        self.interactive = False
        self.transport = UdpTransport(listen_ip, port, MAX_DATAGRAM_BYTES + 1)
        self.running = threading.Event()
        self.running.set()
        self.ack_condition = threading.Condition()
        self.acknowledged = deque(maxlen=256)

    @property
    def port(self):
        return self.transport.local_address[1]

    def make_message(self, target, message):
        return create_chat(self.name, target, message)

    def listen_loop(self):
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

    def reload_contacts(self):
        if not self.contacts_path:
            LOGGER.warning("no contacts file configured for reload")
            return False

        try:
            peers = load_contacts(self.contacts_path, self.default_port, warn_missing=False)
        except ValueError as error:
            LOGGER.error("could not reload contacts: %s", error)
            return False

        with self.peer_lock:
            self.peers = peers
        LOGGER.info("reloaded %d contacts from %s", len(peers), self.contacts_path)
        if self.interactive:
            _info(f"Reloaded contacts from {self.contacts_path}")
        return True

    def _receive_chat(self, packet, address):
        if packet["recipient"] not in (self.name, "*"):
            LOGGER.debug("ignored message addressed to %s", packet["recipient"])
            return

        append_inbox(self.inbox_path, packet, address)

        if self.interactive:
            sent_at = _fmt_time(packet.get("sent_at"))
            print(
                f"\n{ANSI_GREEN}◀ {packet['sender']}{ANSI_RESET} "
                f"({sent_at}): {packet['body']}"
            )
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
            print(f"\n{ANSI_DIM}✓ Delivered to {packet['sender']}{ANSI_RESET}")
            print(self.terminal_prompt(), end="", flush=True)
        else:
            LOGGER.info("%s acknowledged message %s", packet["sender"], message_id)
        with self.ack_condition:
            self.acknowledged.append(message_id)
            self.ack_condition.notify_all()

    def send_to(self, target, message):
        with self.peer_lock:
            peer = self.peers.get(target)

        if not peer:
            LOGGER.error("unknown peer %s; known peers: %s", target, self.peer_names())
            if self.interactive:
                _warn(f"Unknown receiver: {target}")
                _info("Use /receiver CALLSIGN=HOST[:PORT] to add it.")
            return None

        address = self._resolve_peer(target, peer)
        if address is None:
            return None

        packet = self.make_message(target, message)
        try:
            self.transport.send(encode_packet(packet), address)
        except (OSError, ProtocolError) as error:
            LOGGER.error("could not send to %s: %s", target, error)
            if self.interactive:
                _warn(f"Could not send to {target}: {error}")
            return None

        if not self.interactive:
            LOGGER.info("sent message %s to %s", packet["id"], target)
        return packet["id"]

    def _resolve_peer(self, target, peer):
        host, port = peer
        if is_ipv4_address(host):
            return host, port

        if self.interactive and host.endswith(".local"):
            _info(f"Resolving {host} with mDNS...")

        result = {}
        finished = threading.Event()

        def worker():
            try:
                info = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_DGRAM)
                result["address"] = info[0][4]
            except OSError as error:
                result["error"] = error
            finally:
                finished.set()

        thread = threading.Thread(target=worker, name="krasp-resolve", daemon=True)
        thread.start()

        if not finished.wait(self.resolve_timeout):
            LOGGER.error("timed out resolving %s for %s", host, target)
            if self.interactive:
                _warn(f"Timed out resolving {host}")
                if host.endswith(".local"):
                    _info("Check mDNS/Avahi, multicast filtering, or use a fixed IP address.")
            return None

        if "error" in result:
            LOGGER.error("could not resolve %s for %s: %s", host, target, result["error"])
            if self.interactive:
                _warn(f"Could not resolve {host}: {result['error']}")
                if host.endswith(".local"):
                    _info("Check mDNS/Avahi, multicast filtering, or use a fixed IP address.")
            return None

        return result["address"]

    def wait_for_ack(self, message_id, timeout):
        with self.ack_condition:
            received = self.ack_condition.wait_for(
                lambda: message_id in self.acknowledged,
                timeout=timeout,
            )
            if received:
                self.acknowledged.remove(message_id)
            return received

    def peer_names(self):
        with self.peer_lock:
            return ", ".join(sorted(self.peers)) or "(none)"

    def define_receiver(self, peer_text, default_port):
        name, host, port = parse_peer(peer_text)
        port = port or default_port
        with self.peer_lock:
            self.peers[name] = (host, port)
        self.active_receiver = name

        if self.contact_save_path:
            try:
                save_peer(self.contact_save_path, name, host, port)
            except (OSError, ValueError) as error:
                LOGGER.warning("could not save receiver %s to %s: %s", name, self.contact_save_path, error)
                if self.interactive:
                    _warn(f"Receiver added for this session, but could not save it: {error}")
            else:
                if self.interactive:
                    _info(f"Saved receiver in {self.contact_save_path}")
        return name

    def select_receiver(self, name):
        with self.peer_lock:
            if name not in self.peers:
                return False
        self.active_receiver = name
        return True

    def terminal_prompt(self):
        if self.active_receiver:
            receiver_part = f"{ANSI_BOLD}{self.active_receiver}{ANSI_RESET}"
        else:
            receiver_part = f"{ANSI_DIM}no receiver{ANSI_RESET}"
        return f"{self.name} → {receiver_part} › "

    def close(self):
        self.running.clear()
        self.transport.close()

    def input_loop(self, default_port, initial_receiver=None):
        self.interactive = True
        print("\n" + "─" * 40)
        print("  Krasp  |  terminal chat")
        print("─" * 40)
        print(f"  You are: {ANSI_BOLD}{self.name}{ANSI_RESET}")
        print(f"  Listening on port {self.port}")
        print("  /help for commands  |  /quit to exit")
        print("─" * 40 + "\n")

        if initial_receiver:
            self._set_receiver_from_input(initial_receiver, default_port)
        elif self.peers:
            _info(f"Known receivers: {self.peer_names()}")
            receiver = self._read_line("Select receiver: ")
            if receiver:
                self._set_receiver_from_input(receiver, default_port)
        else:
            _warn("No receivers are configured.")
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
                _warn("Select a receiver before sending.")
                _info("Use /receiver CALLSIGN=HOST[:PORT] to define one.")

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
                with self.peer_lock:
                    host, port = self.peers[name]
                _info(f"Receiver defined: {name} at {host}:{port}")
            elif self.select_receiver(receiver):
                _info(f"Receiver selected: {receiver}")
            else:
                _warn(f"Unknown receiver: {receiver}")
                _info("Define it with /receiver CALLSIGN=HOST[:PORT]")
        except ValueError as error:
            _warn(f"Invalid receiver: {error}")

    def _run_terminal_command(self, line, default_port):
        command, _, argument = line.partition(" ")
        argument = argument.strip()

        if command in ("/help", "/?"):
            self._print_help()
        elif command == "/receiver":
            self._command_receiver(argument, default_port)
        elif command == "/to":
            self._command_to(argument)
        elif command in ("/contacts", "/peers"):
            self._command_contacts()
        elif command == "/callsign":
            self._command_callsign(argument)
        elif command == "/status":
            self._command_status()
        else:
            _warn("Unknown command. Type /help for commands.")

    @staticmethod
    def _print_help():
        print("  Just type to send a message to your active contact.")
        print("  /receiver node-b       — switch to node-b")
        print("  /receiver node-b=host  — add node-b and switch to them")
        print("  /to node-b hello       — one-off message")
        print("  /contacts              — list everyone you know")
        print("  /status                — who you are and who you're talking to")
        print("  /callsign newname      — change your name")
        print("  /quit                  — close the terminal")

    def _command_receiver(self, argument, default_port):
        if argument:
            self._set_receiver_from_input(argument, default_port)
        else:
            _warn("Usage: /receiver CALLSIGN or /receiver CALLSIGN=HOST[:PORT]")

    def _command_to(self, argument):
        target, separator, message = argument.partition(" ")
        if separator and message:
            self.send_to(target, message)
        else:
            _warn("Usage: /to CALLSIGN message")

    def _command_contacts(self):
        with self.peer_lock:
            peers = sorted(self.peers.items())
        for name, (host, port) in peers:
            selected = " *" if name == self.active_receiver else ""
            print(f"{name}: {host}:{port}{selected}")
        if not peers:
            _info("No receivers defined")

    def _command_callsign(self, argument):
        try:
            self.name = validate_callsign(argument)
            _info(f"Sender callsign changed to {self.name}")
        except ValueError as error:
            _warn(f"Invalid callsign: {error}")

    def _command_status(self):
        receiver = self.active_receiver or "(not selected)"
        print(f"Sender: {self.name}")
        print(f"Receiver: {receiver}")
        print(f"UDP port: {self.port}")
        if self.contact_save_path:
            print(f"User contacts: {self.contact_save_path}")
        if self.inbox_path:
            print(f"Inbox: {self.inbox_path}")


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--callsign",
        "--name",
        dest="callsign",
        help="sender callsign; defaults to the system hostname",
    )
    parser.add_argument(
        "--receiver",
        help="initial receiver CALLSIGN or CALLSIGN=HOST[:PORT]",
    )
    parser.add_argument("--listen-ip", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--contacts", type=Path, default=DEFAULT_CONTACTS)
    parser.add_argument("--user-contacts", type=Path)
    parser.add_argument("--inbox", type=Path, action="append", default=[])
    parser.add_argument("--resolve-timeout", type=float, default=3.0)
    parser.add_argument("--bind-retries", type=int, default=3)
    parser.add_argument("--bind-retry-delay", type=float, default=0.2)
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
    mode.add_argument(
        "--show-inbox",
        type=int,
        nargs="?",
        const=20,
        metavar="COUNT",
        help="print the most recent inbox messages",
    )
    parser.add_argument("--ack-timeout", type=float, default=3.0)
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
    )
    return parser


def configure_logging(level):
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def prompt_for_callsign(default):
    while True:
        try:
            callsign = input(f"Create sender callsign [{default}]: ").strip() or default
        except (EOFError, KeyboardInterrupt):
            return default
        try:
            return validate_callsign(callsign)
        except ValueError as error:
            _warn(f"Invalid callsign: {error}")


def run_daemon(chat):
    stopped = threading.Event()

    def stop(_signal_number, _frame):
        stopped.set()

    def reload_contacts(_signal_number, _frame):
        chat.reload_contacts()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, reload_contacts)
    LOGGER.info("Krasp daemon ready; peers: %s", chat.peer_names())
    stopped.wait()


def create_chat_with_retries(args, callsign, peers):
    listen_port = 0 if args.send else args.port
    attempts = max(0, args.bind_retries) + 1
    last_error = None

    for attempt in range(1, attempts + 1):
        try:
            return KaonicChat(
                callsign,
                args.listen_ip,
                listen_port,
                peers,
                contacts_path=args.contacts,
                contact_save_path=args.user_contacts,
                inbox_path=args.inbox[0] if args.inbox else None,
                default_port=args.port,
                resolve_timeout=args.resolve_timeout,
            )
        except OSError as error:
            last_error = error
            if attempt == attempts:
                break
            time.sleep(max(0.0, args.bind_retry_delay))

    raise OSError(f"could not bind UDP port after {attempts} attempts: {last_error}")


def main():
    parser = build_parser()
    args = parser.parse_args()
    configure_logging(args.log_level)

    if args.show_inbox is not None:
        return show_inbox(args.inbox, args.show_inbox)

    try:
        validate_port(args.port)
        peers = load_contacts(args.contacts, args.port)
        if args.user_contacts:
            peers.update(load_contacts(args.user_contacts, args.port, warn_missing=False))
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
        chat = create_chat_with_retries(args, callsign, peers)
    except ValueError as error:
        parser.error(str(error))
    except OSError as error:
        parser.error(str(error))

    listener = threading.Thread(target=chat.listen_loop, name="krasp-udp", daemon=True)
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
