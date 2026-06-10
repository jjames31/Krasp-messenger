# Krasp Messenger

Krasp Messenger is a small UDP chat tool for two nodes on a trusted local
network. Each node listens on UDP port `6969`, sends simple JSON packets, and
acks messages when they arrive.

I mainly use it for quick node-to-node testing, so it does not try to be a
secure chat app. Keep it on your LAN, VPN, or another network you trust.

## Install

Run the installer on each device:

```bash
./install.sh
```

The installer asks for this node's callsign and any receivers you want to save.
It also installs the terminal command, sets up the background receiver service,
and opens UDP port `6969` when UFW is active.

Run the installer again any time you want to reconfigure the node.

## Example setup

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

## Commands

```bash
kaonic                            # open terminal chat
kaonic send node-b "Hello"        # send one message
kaonic status                     # service status
kaonic logs                       # follow received messages
kaonic restart                    # reload configuration
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

Temporary receivers only last until the terminal exits. Add them to
`contacts.json`, or run the installer again, to keep them permanently.

Persistent contacts are stored in `/etc/kaonic-messenger/contacts.json`.
Devices that advertise `.local` hostnames do not need fixed IP addresses.
