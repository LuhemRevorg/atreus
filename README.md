# atreus

![Atreus GIF](./assets/atreus_shoot_8x_1.png)

Local AI Assitant
I run qwen3:8b(think=True)
Holds tools and what not

P.S. Atreus is GoW reference

## Setup

```
./scripts/setup.sh
```

## What Atreus may run

The bash tool is driven by an 8b model reading speech-to-text, so a command can
be wrong twice over before it reaches a shell. Commands are sorted into three
tiers by `src/llama/tools/bash_policy.py`:

| tier | what it covers | what happens |
|---|---|---|
| refused | disk formats, `rm -rf ~`, fork bombs, disabling SIP | never runs |
| read-only | `ls`, `cat`, `df`, `git status`, `pmset -g batt` and friends | runs straight away |
| everything else | anything that writes, installs, deletes or elevates | a dialog asks you first |

The read-only tier is judged on the whole line, not the first word: `ls; rm -rf ~`
is not an `ls`, and a redirection, a backtick or a `$(...)` drops a command to
the ask tier on its own. Reads of credentials -- `.ssh`, `.env`, keychains --
also ask, because their output ends up in the model's context and out of the
speaker.

Set `ATREUS_BASH_POLICY` to pick what the ask tier does:

| value | behaviour |
|---|---|
| `ask` | default -- approve each one in a dialog |
| `strict` | refuse anything that is not read-only, no prompt. For a Mac left logged in with nobody in front of it |
| `open` | no prompts, refused list only. The old behaviour |

To set it for the daemon, add it to `scripts/atreus.sh` next to the other
exports, then `launchctl kickstart -k gui/$(id -u)/com.atreus.agent`.

None of this is a sandbox -- an approved command runs as you, with your access.
It is there so that a mishearing cannot approve itself.

```
python3 tests/test_bash_policy.py
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
