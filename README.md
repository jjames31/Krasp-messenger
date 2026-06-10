# Krasp Messenger on Raspberry Pi

Krasp Messenger is a small UDP chat tool for two Raspberry Pi nodes on a trusted
local network. Each Pi listens on UDP port `6969`, sends simple JSON packets, and
acks messages when they arrive.

I mainly use it for quick Pi-to-Pi testing, so it does not try to be a secure
chat app. Keep it on your LAN, VPN, or another network you trust.

## Install

Run the installer on each Pi:

```bash
./install.sh
```

The installer asks for this Pi's callsign and any receivers you want to save. It
also installs the terminal command, sets up the background receiver service, and
opens UDP port `6969` when UFW is active.

Run the installer again any time you want to reconfigure the Pi.

## Example setup

For Pi A:

```bash
sudo ./install.sh --non-interactive \
  --callsign pi-a \
  --peer pi-b=pi-b.local
```

For Pi B:

```bash
sudo ./install.sh --non-interactive \
  --callsign pi-b \
  --peer pi-a=pi-a.local
```

## Commands

```bash
kaonic                         # open terminal chat
kaonic send pi-b "Hello"       # send one message
kaonic status                  # service status
kaonic logs                    # follow received messages
kaonic restart                 # reload configuration
```

Inside the terminal:

```text
/receiver pi-b       switch to pi-b
/receiver pi-b=host  add pi-b and switch to it
/to pi-b hello       send a one-off message
/contacts            list saved receivers
/status              show this Pi and active receiver
/callsign newname    change the sender name
/quit                exit
```

Temporary receivers only last until the terminal exits. Add them to
`contacts.json`, or run the installer again, to keep them permanently.

Persistent contacts are stored in `/etc/kaonic-messenger/contacts.json`.
Raspberry Pi OS usually advertises `.local` hostnames, so fixed IP addresses are
not required.
