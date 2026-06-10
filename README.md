# Krasp Messenger

Krasp Messenger is a small UDP chat tool for nodes on a trusted local network.
Each node listens on UDP port `6969`, sends simple JSON packets, and acks
messages when they arrive.

It can run on a laptop, desktop, Raspberry Pi, or other Linux device. When using
a Kaonic radio, Krasp normally runs on the computer or Pi connected to the Kaonic
unit; the Kaonic just provides the network path between nodes.

This is for quick node-to-node testing, so it does not try to be a secure chat
app. Keep it on your LAN, VPN, Kaonic link, or another network you trust.

## Two ways to run it

Use the installer when you want an always-on Linux receiver service:

```bash
./install.sh
```

Use Python directly when you are testing from a computer, a Pi, or a temporary
Kaonic-connected host and do not want to install a service:

```bash
python3 app.py --callsign node-a \
  --peer node-b=192.168.1.42 \
  --user-contacts ./contacts.local.json \
  --inbox ./inbox.jsonl
```

Run a matching command on the other side with the callsigns and IP addresses
swapped.

## Installed setup

The installer asks for this node's callsign and any receivers you want to save.
It also installs the terminal command, sets up the background receiver service,
and opens UDP port `6969` when UFW is active.

Run the installer again any time you want to reconfigure the node.

For node A:

```bash
sudo ./install.sh --non-interactive \
  --callsign node-a \
  --peer node-b=node-b.local
```

For node B:

```bash
sudo ./install.sh --non-interactive \
  --callsign node-b \
  --peer node-a=node-a.local
```

`.local` hostnames depend on mDNS. If a network blocks multicast or the target
is not advertising mDNS, use a fixed IP address instead:

```bash
sudo ./install.sh --non-interactive \
  --callsign node-a \
  --peer node-b=192.168.1.42
```

## Kaonic-connected hosts

If each node is a computer or Pi connected to a Kaonic unit, use the IP address
that is reachable across the Kaonic network, VPN, or tunnel. Do not assume the
Kaonic dashboard address is the peer address; use the address assigned to the
host or the tunnel IP shown by the Kaonic network tools.

### Finding the correct IP address

On each Linux host, list IPv4 addresses:

```bash
ip -4 -br addr
```

Example output:

```text
lo               UNKNOWN        127.0.0.1/8
eth0             UP             192.168.1.55/24
wlan0            UP             192.168.1.80/24
usb0             UP             192.168.10.2/24
```

The Kaonic-facing interface is usually the interface that appears when the
Kaonic is plugged in, such as `usb0`, `eth1`, `enx...`, or `enp...`. To identify
it, run `ip -4 -br addr`, unplug the Kaonic, run it again, then plug the Kaonic
back in and run it a third time. The interface that disappears and reappears is
the local Kaonic-side interface.

Use the other node's reachable host IP as the peer address:

```text
My local Kaonic-side IP  = the address shown on this host
Peer destination IP      = the other host's Kaonic-side IP
```

For example, if node A is `10.42.0.10` and node B is `10.42.0.20`:

```bash
# On node A
python3 app.py --callsign node-a --peer node-b=10.42.0.20

# On node B
python3 app.py --callsign node-b --peer node-a=10.42.0.10
```

`--listen-ip` is normally not needed because Krasp listens on `0.0.0.0` by
default, which means all local interfaces. Only set `--listen-ip` when you want
Krasp to listen on one specific interface:

```bash
python3 app.py --callsign node-a \
  --listen-ip 10.42.0.10 \
  --peer node-b=10.42.0.20
```

A typical test looks like this:

```bash
# On the computer or Pi connected to Kaonic A
python3 app.py --callsign node-a --peer node-b=<node-b-reachable-ip>

# On the computer or Pi connected to Kaonic B
python3 app.py --callsign node-b --peer node-a=<node-a-reachable-ip>
```

Before blaming Krasp, verify UDP reachability between the two hosts:

```bash
# Receiver side
nc -ul 6969

# Sender side
printf 'test\n' | nc -u <receiver-ip> 6969
```

If the test does not appear on the receiver side, fix routing, firewall, VPN, or
Kaonic link settings first. Krasp uses the same UDP path.

## Commands

```bash
kaonic                            # open terminal chat
kaonic send node-b "Hello"        # send one message
kaonic inbox                      # show recent received messages
kaonic status                     # service status
kaonic logs                       # follow received messages
kaonic reload                     # reload contacts without restart
kaonic restart                    # restart the receiver service
```

Inside the terminal:

```text
/receiver node-b       switch to node-b
/receiver node-b=host  add node-b and switch to it
/to node-b hello       send a one-off message
/contacts              list saved receivers
/status                show this node and active receiver
/callsign newname      change the sender name
/quit                  exit
```

Receivers added in the terminal are saved in the user's config file under
`~/.config/krasp/contacts.json`. System contacts from the installer live in
`/etc/kaonic-messenger/contacts.json`.

Received messages are appended to an inbox file so they can be reviewed later.
Use `kaonic inbox` to show recent messages. The background service writes to
`/var/lib/kaonic-messenger/inbox.jsonl`; the interactive terminal writes to
`~/.local/state/krasp/inbox.jsonl`.
