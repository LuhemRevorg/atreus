"""Decides what the bash tool may run on its own, and what needs a human.

Atreus drives this from qwen3:8b, off speech-to-text, with no one necessarily
watching the screen. That is three separate ways for a command to be wrong
before it ever reaches a shell: the model can misread the request, the
transcription can turn one word into another, and the user can be in the next
room. So the tool cannot be a bare shell -- but it also cannot be so locked
down that "how much battery do I have left" stops working.

Every command therefore lands in one of three tiers:

    deny   irreversible and never worth a prompt -- a disk format, a fork bomb.
    allow  reads something and changes nothing, so it just runs.
    ask    everything else, which runs only once the user clicks Run once.

The allow tier is the interesting one, and it is deliberately small. A command
is on it only if every part of it is: `ls` is a read, `ls; rm -rf ~` is not, and
the second one has to fail the check even though it starts with the first. That
means actually splitting the line the way bash would, rather than looking at the
first word and hoping. Anything this module cannot parse with confidence falls
to `ask` -- a wrong verdict there costs a dialog, while a wrong verdict the
other way costs whatever the command did.

Nothing here is a sandbox. Anything that reaches the shell runs as the user, and
a determined command can still do damage once approved. The point is that a
mishearing cannot approve itself.
"""

import re
import shlex

# Never run, whoever asks. These are the commands with no undo and no plausible
# reason to arrive by voice -- approving one by mistake is worse than any
# inconvenience of refusing it outright.
_DENIED = [
    (re.compile(r"\brm\s+(-[a-zA-Z]*\s+)*-[a-zA-Z]*[rR][a-zA-Z]*\s+(-[a-zA-Z]+\s+)*(/|~|\$HOME)\s*$"),
     "recursive delete of a home or root directory"),
    (re.compile(r"\bmkfs(\.\w+)?\b"), "filesystem format"),
    (re.compile(r"\bdd\b.*\bof=/dev/"), "raw write to a device"),
    (re.compile(r">\s*/dev/(disk|sd|nvme)"), "raw write to a device"),
    (re.compile(r":\(\)\s*\{.*\};\s*:"), "fork bomb"),
    (re.compile(r"\b(shutdown|reboot|halt)\b"), "shutdown or reboot"),
    (re.compile(r"\bdiskutil\s+(erase|reformat|partitionDisk)"), "disk erase"),
    (re.compile(r"\bcsrutil\s+disable\b"), "disabling System Integrity Protection"),
    (re.compile(r"\bspctl\s+--master-disable\b"), "disabling Gatekeeper"),
    (re.compile(r"\bkillall\s+-9\s+(-u\s+\S+|\S*Finder|WindowServer|loginwindow)"), "killing the desktop session"),
]

# Reads only, changes nothing, and says nothing an eavesdropper could not read
# off the screen already. Everything here is allowed with any arguments unless
# it also appears in one of the two tables below.
_READ_ONLY = {
    # files and directories
    "ls", "pwd", "cat", "bat", "head", "tail", "wc", "file", "stat", "tree",
    "du", "df", "basename", "dirname", "realpath", "readlink",
    # text
    "grep", "egrep", "rg", "sort", "uniq", "cut", "tr", "column", "jq", "diff",
    "echo", "printf",
    # the machine
    "date", "cal", "uptime", "whoami", "id", "hostname", "uname", "sw_vers",
    "arch", "ps", "ioreg", "system_profiler", "vm_stat", "sysctl",
    # where things are
    "which", "type",
    # walked, not run -- see _FORBIDDEN_ARGS
    "find",
    # first word only -- see _ALLOWED_SUBCOMMANDS
    "git", "brew", "pmset", "defaults",
}

# Commands whose first word is harmless and whose second word decides
# everything: `git log` reads, `git push` publishes, and only one of those
# should happen without being asked for.
_ALLOWED_SUBCOMMANDS = {
    "git": {"status", "log", "diff", "show", "blame", "shortlog", "describe",
            "rev-parse", "branch", "stash"},
    "brew": {"list", "info", "outdated", "config", "--version"},
    # `pmset -g` prints power settings; every other form writes them.
    "pmset": {"-g"},
    # `defaults write` edits preference files, which is how you break an app.
    "defaults": {"read", "read-type", "domains"},
}

# Arguments that turn a reader into a writer. `find` is the whole reason this
# table exists: it walks the disk quietly until you hand it `-delete`.
_FORBIDDEN_ARGS = {
    "find": {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint",
             "-fprintf", "-fls"},
    # `git branch` lists, `git branch -d` deletes.
    "git": {"-d", "-D", "--delete", "-m", "-M", "--move", "-f", "--force"},
    # `sysctl -w` sets a kernel parameter.
    "sysctl": {"-w"},
}

# Read-only in the mechanical sense, and still not something to read aloud in a
# room. Credentials leave the machine the moment they enter the model's context
# or the speech engine, so a `cat` of one is worth a dialog even though `cat`
# itself is on the allowlist.
_SENSITIVE = re.compile(
    r"(\.ssh/|id_rsa|id_ed25519|\.aws/credentials|\.netrc|\.gnupg|"
    r"Keychains|login\.keychain|\.env(\.|\b)|secrets?\.(json|ya?ml|toml)|"
    r"credentials\.(json|ya?ml))",
    re.IGNORECASE,
)

# Shell syntax that makes the tokens lie about what will run. A backtick or a
# `$(...)` hides an entire second command inside what looks like an argument,
# and there is no reading either of them out of the token stream afterwards.
_SUBSTITUTION = re.compile(r"\$\(|`|\$\{[^}]*\(")

# Operators that keep each side visible as its own command, so the allowlist can
# judge them one at a time. `&` is absent on purpose: a backgrounded command
# outlives the timeout that is supposed to bound it.
_SAFE_OPERATORS = {";", "&&", "||", "|", "\n"}

# The characters shlex hands back as operator tokens once `punctuation_chars`
# is on. Spelled out here because the lexer only exposes it per instance.
_PUNCTUATION = "();<>|&"

# `VAR=value cmd` -- a prefix, not the command itself.
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

# Privilege, in every spelling that reaches a Mac. Never auto-run, whatever
# follows it: `sudo ls` is a read, but approving the habit is not.
_ELEVATED = {"sudo", "doas", "su", "osascript", "launchctl", "systemsetup"}


def _tokenize(command: str) -> list[str] | None:
    """Split a command line into words and operators, or None if it won't parse.

    `punctuation_chars` is what makes this worth doing over a `split()`: shlex
    then hands back `;`, `&&` and `>` as tokens of their own while leaving the
    same characters alone inside quotes, so `echo "a; b"` stays one argument and
    `ls; rm` becomes two commands. Comments are switched off -- a `#` should not
    be able to hide the rest of the line from us the way it would from a reader.
    """
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        return list(lexer)
    except ValueError:
        # Unbalanced quote. bash may still make sense of it; we cannot, and a
        # command we cannot read is not one we can vouch for.
        return None


def _segments(tokens: list[str]) -> list[list[str]] | None:
    """Break the token stream into simple commands, or None if it uses syntax
    the allowlist has no answer for -- redirections, subshells, backgrounding.
    """
    segments: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token in _SAFE_OPERATORS:
            if current:
                segments.append(current)
            current = []
            continue
        # Any other punctuation run: `>`, `>>`, `<`, `&`, `(`, `)`. Each one
        # either writes somewhere or hides a command, and none is common enough
        # in a spoken request to be worth teaching this function about.
        if token and all(character in _PUNCTUATION for character in token):
            return None
        current.append(token)
    if current:
        segments.append(current)
    return segments


def _head(segment: list[str]) -> str:
    """The command a segment actually runs, with `VAR=value` prefixes dropped
    and a path reduced to its name so `/bin/ls` is still `ls`.
    """
    for token in segment:
        if _ASSIGNMENT.match(token):
            continue
        return token.rsplit("/", 1)[-1]
    return ""


def _arguments(segment: list[str]) -> list[str]:
    head_seen = False
    arguments = []
    for token in segment:
        if not head_seen:
            if _ASSIGNMENT.match(token):
                continue
            head_seen = True
            continue
        arguments.append(token)
    return arguments


def _segment_reason(segment: list[str]) -> str | None:
    """Why this one simple command cannot just run, or None if it can.

    The phrase comes back rather than a bare False because it is the only
    explanation the user ever sees: it goes in the dialog, and the model reads
    it back if they say no.
    """
    head = _head(segment)
    if head in _ELEVATED:
        return f"it runs {head}"
    if head not in _READ_ONLY:
        return f"{head or 'that'} is not one of the commands I can run on my own"

    arguments = _arguments(segment)

    forbidden = _FORBIDDEN_ARGS.get(head, set())
    for argument in arguments:
        if argument in forbidden:
            return f"{head} {argument} changes things rather than reading them"

    allowed_subcommands = _ALLOWED_SUBCOMMANDS.get(head)
    if allowed_subcommands is not None:
        # The subcommand is the first argument that is not a global flag:
        # `git -C ~/repo status` is still a status.
        subcommand = next(
            (argument for argument in arguments
             if not argument.startswith("-") or argument in allowed_subcommands),
            "",
        )
        if subcommand not in allowed_subcommands:
            return f"{head} {subcommand}".strip() + " is not one of its read-only forms"
        # `git stash` on its own stashes; only its listing forms read.
        if head == "git" and subcommand == "stash" and not any(
            argument in ("list", "show") for argument in arguments
        ):
            return "git stash would put your changes away"

    return None


def classify(command: str) -> tuple[str, str]:
    """Sort a command into "deny", "allow" or "ask".

    Returns the verdict and a short phrase naming why, in the second person and
    fit to put in front of the user -- the dialog and the model's spoken reply
    both end up quoting it.
    """
    command = (command or "").strip()
    if not command:
        return "ask", "an empty command"

    for pattern, reason in _DENIED:
        if pattern.search(command):
            return "deny", reason

    if _SUBSTITUTION.search(command):
        return "ask", "it builds part of itself from another command"

    if _SENSITIVE.search(command):
        return "ask", "it touches credentials or keys"

    tokens = _tokenize(command)
    if tokens is None:
        return "ask", "it has an unbalanced quote"

    segments = _segments(tokens)
    if segments is None:
        return "ask", "it redirects output or runs a subshell"

    if not segments:
        return "ask", "an empty command"

    for segment in segments:
        reason = _segment_reason(segment)
        if reason:
            return "ask", reason

    return "allow", ""
