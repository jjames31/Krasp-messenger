# Kaonic Messenger on Raspberry Pi

Kaonic Messenger is a small, dependency-free UDP messenger intended for
always-on Raspberry Pi nodes on a trusted local network. Each node listens on
UDP port `6969`, accepts versioned Kaonic JSON packets, and acknowledges valid
messages.

## Automatic Installation

Run the executable installer on each Pi:

```bash
./install.sh
```

The installer asks for this Pi's callsign and receivers, installs the `kaonic`
command, configures the background receiver service, and opens UDP port `6969`
when UFW is active. Run `./install.sh` again whenever you want to reconfigure
the Pi.

```bash
kaonic
```

For an unattended installation:

```bash
sudo ./install.sh --non-interactive \
  --callsign office-pi \
  --peer kitchen-pi=kitchen-pi.local \
  --peer garage-pi=garage-pi.local
```

## Commands

```bash
kaonic                              # open terminal chat
kaonic send kitchen-pi "Hello"      # send one message
kaonic status                       # service status
kaonic logs                         # follow received messages
kaonic restart                      # reload configuration
```

Inside the terminal, use `/receiver`, `/to`, `/callsign`, `/contacts`,
`/status`, and `/quit`. Defining a receiver in the terminal lasts until the
program exits; add it to `contacts.json` to keep it permanently.

Persistent contacts are stored in `/etc/kaonic-messenger/contacts.json`.
Raspberry Pi OS usually advertises `.local` hostnames, so fixed IP addresses are
not required. This protocol is not encrypted or authenticated; keep it on a
trusted LAN or VPN.
