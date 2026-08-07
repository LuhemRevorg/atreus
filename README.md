# atreus

Local Agent Orchestrator
Running local agents on my Mac
Holds tools and what not

P.S. Atreus is GoW reference

## Setup

```
./scripts/setup.sh
```

## Running as a daemon

Atreus runs as a launchd **user agent** (not a system daemon) -- it needs the
microphone and speakers, which only exist inside a logged-in GUI session.

```
./scripts/install-agent.sh
```

| | |
|---|---|
| logs | `tail -f ~/Library/Logs/atreus.log ~/Library/Logs/atreus.err.log` |
| status | `launchctl print gui/$(id -u)/com.atreus.agent` |
| restart | `launchctl kickstart -k gui/$(id -u)/com.atreus.agent` |
| stop | `launchctl bootout gui/$(id -u)/com.atreus.agent` |

I recommened adding these as an alias to your .zshrc(or .bashrc whatever you have)

The repo must live outside `~/Desktop`, `~/Documents` and `~/Downloads`.
Those are TCC-protected, and launchd agents can't read them -- the job dies
with exit 126 before Python even starts.

The plist is generated from `scripts/com.atreus.agent.plist.template`, with the
repo and home paths substituted in. Re-run `install-agent.sh` if the repo moves
-- editing the loaded plist in place does nothing, launchd caches it.

