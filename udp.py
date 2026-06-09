"""Small IPv4 UDP transport for Kaonic messenger."""

import socket


class UdpTransport:
    """Own the UDP socket used by a Kaonic node."""

    def __init__(self, listen_ip, port, receive_size):
        self.receive_size = receive_size
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind((listen_ip, port))
        self.socket.settimeout(0.5)

    @property
    def local_address(self):
        """Return the socket's bound address."""
        return self.socket.getsockname()

    def send(self, data, address):
        """Send one datagram."""
        return self.socket.sendto(data, address)

    def receive(self):
        """Receive one datagram and its source address."""
        return self.socket.recvfrom(self.receive_size)

    def close(self):
        """Close the transport."""
        self.socket.close()
