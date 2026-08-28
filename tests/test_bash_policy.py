"""Checks for the bash tool's command policy.

Run it directly: `python3 tests/test_bash_policy.py`. No test runner and no
dependencies -- the policy module deliberately imports nothing but the standard
library, and a check that needs a venv to run is a check nobody runs.

The module is loaded by path rather than imported as `llama.tools.bash_policy`,
which would drag in the whole package (ollama, whisper, AppKit) for the sake of
two regexes.
"""

import importlib.util
import sys
from pathlib import Path

MODULE = Path(__file__).resolve().parent.parent / "src" / "llama" / "tools" / "bash_policy.py"

_spec = importlib.util.spec_from_file_location("bash_policy", MODULE)
policy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(policy)

# Never runs, whatever anyone clicks.
DENIED = [
    "rm -rf /",
    "rm -rf ~",
    "sudo rm -rf /",
    "mkfs.ext4 /dev/disk2",
    "dd if=/dev/zero of=/dev/disk0",
    ":(){ :|:& };:",
    "sudo shutdown -h now",
    "diskutil eraseDisk JHFS+ Blank disk2",
    "csrutil disable",
]

# Reads something, changes nothing, runs without a dialog.
ALLOWED = [
    "ls",
    "ls -la ~/Desktop",
    "/bin/ls ~",
    "LC_ALL=C ls",
    "pwd",
    "cat notes.txt",
    "head -n 20 log.txt",
    "df -h",
    "du -sh ~/Movies",
    "date",
    "uptime",
    "whoami",
    "sw_vers",
    "pmset -g batt",
    "git status",
    "git log --oneline -10",
    "git diff HEAD~1",
    "git stash list",
    "brew list",
    "brew outdated",
    "defaults read com.apple.dock",
    "ls | grep pdf | wc -l",
    "df -h && uptime",
    "ls ~/Downloads; date",
    "find ~/Documents -name '*.pdf'",
    'echo "one; two"',          # the semicolon is quoted, so it is not an operator
    "ps aux | grep -i ollama",
]

# Runs only once the user approves it.
ASK = [
    # writes, moves, deletes
    "rm old.txt",
    "mv a.txt b.txt",
    "mkdir ~/new",
    "touch ~/notes.txt",
    "chmod +x script.sh",
    # a reader hiding a writer in its arguments
    "find ~/Downloads -name '*.dmg' -delete",
    "find . -exec rm {} +",
    "sysctl -w kern.maxfiles=1024",
    # the first word reads, the second one does not
    "git push",
    "git commit -am wip",
    "git branch -D main",
    "git stash",
    "defaults write com.apple.dock autohide -bool true",
    "brew install ffmpeg",
    "pmset -a sleep 0",
    # arbitrary code behind a friendly name
    "python3 -c 'print(1)'",
    "node index.js",
    "./deploy.sh",
    "bash -c ls",
    # privilege
    "sudo ls",
    "osascript -e 'beep'",
    "launchctl unload ~/Library/LaunchAgents/com.atreus.agent.plist",
    # syntax the allowlist cannot see through
    "echo hello > notes.txt",
    "cat a.txt >> b.txt",
    "ls $(cat target.txt)",
    "echo `whoami`",
    "sleep 60 &",
    "(cd /tmp && ls)",
    'ls "unbalanced',
    # a read of something that should not be read aloud
    "cat ~/.ssh/id_rsa",
    "cat .env",
    "ls ~/.aws/credentials",
    "grep KEY ~/project/.env",
    # one bad command in an otherwise fine chain
    "ls ~/Desktop && rm notes.txt",
    "date; brew install wget",
    "cat report.txt | tee ~/copy.txt",
    # nothing to judge
    "",
    "   ",
]


def main() -> int:
    failures = []

    for command, expected in (
        [(c, "deny") for c in DENIED]
        + [(c, "allow") for c in ALLOWED]
        + [(c, "ask") for c in ASK]
    ):
        verdict, reason = policy.classify(command)
        if verdict != expected:
            failures.append(f"{command!r}: expected {expected}, got {verdict} ({reason})")
        # An ask has to explain itself; the dialog and the spoken reply both
        # quote the reason, and "I'm asking because ." is not a sentence.
        if verdict != "allow" and not reason:
            failures.append(f"{command!r}: {verdict} with no reason given")

    for line in failures:
        print(f"FAIL {line}")

    total = len(DENIED) + len(ALLOWED) + len(ASK)
    if failures:
        print(f"\n{len(failures)} failed out of {total} commands")
        return 1
    print(f"{total} commands classified as expected")
    return 0


if __name__ == "__main__":
    sys.exit(main())
