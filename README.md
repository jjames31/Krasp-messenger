# Krasp Messenger

Krasp Messenger is a small UDP chat tool for nodes on a trusted local network.
Each node listens on UDP port `6969`, sends simple JSON packets, and acks
messages when they arrive.

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

`.local` hostnames depend on mDNS. If a network blocks multicast or the target
is not advertising mDNS, use a fixed IP address instead:

```bash
sudo ./install.sh --non-interactive \
  --callsign node-a \
  --peer node-b=192.168.1.42
```

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
