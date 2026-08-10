import os
import re
import signal
import subprocess

# The voice loop is blocking, so a runaway command would wedge the whole
# assistant. Cap it well under a listener's patience.
DEFAULT_TIMEOUT = 30

# Output is fed back into an 8b model's context and may be spoken aloud, so a
# `cat` of something large has to be trimmed before it gets there.
MAX_OUTPUT_CHARS = 4000

# Not a security boundary -- anything reaching this tool already runs as the
# user. This only catches the irreversible, "misheard the wake word" class of
# accident, where speech-to-text turns a harmless sentence into a destructive
# command. Anything genuinely dangerous should be typed by a human.
_BLOCKED = [
    (re.compile(r"\brm\s+(-[a-zA-Z]*\s+)*-[a-zA-Z]*[rR][a-zA-Z]*\s+(-[a-zA-Z]+\s+)*(/|~|\$HOME)\s*$"),
     "recursive delete of a home or root directory"),
    (re.compile(r"\bmkfs(\.\w+)?\b"), "filesystem format"),
    (re.compile(r"\bdd\b.*\bof=/dev/"), "raw write to a device"),
    (re.compile(r">\s*/dev/(disk|sd|nvme)"), "raw write to a device"),
    (re.compile(r":\(\)\s*\{.*\};\s*:"), "fork bomb"),
    (re.compile(r"\b(shutdown|reboot|halt)\b"), "shutdown or reboot"),
    (re.compile(r"\bdiskutil\s+(erase|reformat)"), "disk erase"),
]


def _blocked_reason(command: str) -> str | None:
    for pattern, reason in _BLOCKED:
        if pattern.search(command):
            return reason
    return None


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    # Keep both ends: the head usually says what ran, the tail usually says how
    # it failed. The middle of a long log is the least useful part.
    head = text[: MAX_OUTPUT_CHARS // 2]
    tail = text[-MAX_OUTPUT_CHARS // 2:]
    cut = len(text) - len(head) - len(tail)
    return f"{head}\n... [{cut} characters truncated] ...\n{tail}"


def bash(command: str, cwd: str = "") -> str:
    '''
    Run a shell command on the user's Mac and return its output.

    Use this for anything that needs the terminal or the local machine: checking
    files and directories, disk or battery status, git state, running a script,
    installing a package, opening an app. Prefer a single short command that
    answers the question directly, and read the output back in a sentence rather
    than reciting it. Do NOT use it to write or build new code or new tools --
    that is what the claude tool is for -- and do NOT use it for questions you
    can simply answer yourself.

    Args:
        command: The shell command to run, e.g. "ls ~/Desktop" or "git status".
        cwd: Directory to run the command in. Leave empty for the home directory.
    '''
    command = (command or "").strip()
    if not command:
        return "Error: no command given."

    reason = _blocked_reason(command)
    if reason:
        return (
            f"Refused: that command looks like a {reason}, which is not "
            f"something I will run from voice. Run it yourself if you meant it."
        )

    workdir = os.path.expanduser(cwd.strip()) if cwd and cwd.strip() else os.path.expanduser("~")
    if not os.path.isdir(workdir):
        return f"Error: directory not found: {workdir}"

    print(f"Running: {command}  (in {workdir})")

    try:
        proc = subprocess.Popen(
            ["/bin/bash", "-l", "-c", command],
            cwd=workdir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            # Nothing is here to answer a prompt; without this, anything that
            # reads stdin blocks until the timeout instead of failing fast.
            stdin=subprocess.DEVNULL,
            text=True,
            errors="replace",
            # Own process group, so a timeout can take down children too. A
            # bare proc.kill() would leave the grandchildren of a pipeline
            # running after we have given up on them.
            start_new_session=True,
        )
    except OSError as e:
        return f"Error: could not start the command: {e}"

    try:
        output = proc.communicate(timeout=DEFAULT_TIMEOUT)[0]
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        output = proc.communicate()[0] or ""
        return (
            f"Timed out after {DEFAULT_TIMEOUT} seconds and was stopped.\n"
            f"{_truncate(output.strip())}".strip()
        )

    output = (output or "").strip()
    code = proc.returncode

    if code == 0:
        return _truncate(output) if output else "Done. The command produced no output."
    return f"Exit code {code}.\n{_truncate(output)}".strip() if output else f"Exit code {code}, no output."


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        # Already dead, or it escaped its group -- either way the fallback is
        # the same and communicate() below still needs to drain the pipe.
        try:
            proc.kill()
        except ProcessLookupError:
            pass
