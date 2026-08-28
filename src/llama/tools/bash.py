"""Run a shell command for the user, once it is clear the user meant it.

What may run without asking, and what may not run at all, lives in
bash_policy.py. This file is the part that has to happen at the shell: putting
the question in front of the user when the policy calls for one, bounding how
long a command can hold the voice loop, and cutting the output down to
something a model can read and a speech engine can survive.
"""

import os
import signal
import subprocess

from .bash_policy import classify

# The voice loop is blocking, so a runaway command would wedge the whole
# assistant. Cap it well under a listener's patience.
DEFAULT_TIMEOUT = 30

# Output is fed back into an 8b model's context and may be spoken aloud, so a
# `cat` of something large has to be trimmed before it gets there.
MAX_OUTPUT_CHARS = 4000

# How long the approval dialog stays up. Long enough to walk back to the Mac,
# short enough that a prompt nobody answers does not hold the voice loop for the
# rest of the afternoon. Unanswered means no.
CONFIRM_TIMEOUT = 60

# A command too long to read is a command nobody can meaningfully approve, so
# the dialog shows this much and says how much it kept back.
MAX_DIALOG_CHARS = 700

# How the ask tier behaves, from the environment so it can be set per install in
# the launchd plist:
#
#   ask     (default) read-only commands run; everything else needs a click.
#   strict  read-only commands run; everything else is refused outright. For a
#           Mac that is often left logged in with nobody in front of it.
#   open    the old behaviour -- everything but the deny list runs unasked.
#
# Anything unrecognised falls back to `ask`, so a typo in the plist tightens the
# tool rather than opening it.
_MODES = ("ask", "strict", "open")


def _mode() -> str:
    mode = os.environ.get("ATREUS_BASH_POLICY", "ask").strip().lower()
    return mode if mode in _MODES else "ask"


# `on run argv` rather than a script built by string formatting: the command
# text arrives as an argument, so a quote or a newline in it cannot end up being
# read as AppleScript. `activate` brings the dialog forward -- without it the
# prompt can open behind whatever the user is looking at, which for a dialog
# that defaults to No means silently refusing every command.
_CONFIRM_SCRIPT = """
on run argv
    set theCommand to item 1 of argv
    set theDirectory to item 2 of argv
    set theReason to item 3 of argv
    set gap to return & return
    set theMessage to "Atreus wants to run:" & gap & theCommand & gap ¬
        & "in " & theDirectory & gap & theReason
    try
        activate
    end try
    set theAnswer to display dialog theMessage with title "Atreus" ¬
        buttons {"Don't run", "Run once"} default button "Don't run" ¬
        with icon caution giving up after %d
    if gave up of theAnswer then return "timeout"
    if button returned of theAnswer is "Run once" then return "approved"
    return "declined"
end run
""" % CONFIRM_TIMEOUT


def _confirm(command: str, workdir: str, reason: str) -> tuple[bool, str]:
    """Ask the user, at the screen, whether this command may run.

    Returns whether it was approved and, if not, a sentence saying why -- which
    the model reads back, so it has to make sense out loud.

    Fails closed on every error path. No dialog means no approval: the tool runs
    from a launchd agent that can be started before anyone logs in, and "nobody
    could be asked" has to mean no rather than yes.
    """
    shown = command if len(command) <= MAX_DIALOG_CHARS else (
        command[:MAX_DIALOG_CHARS] + f"\n... [{len(command) - MAX_DIALOG_CHARS} more characters]"
    )
    explanation = f"I'm asking because {reason}." if reason else "I'm asking before running it."

    try:
        proc = subprocess.run(
            # `-` reads the script from stdin, so the command text stays an
            # argument and never becomes part of the program.
            ["osascript", "-", shown, workdir, explanation],
            input=_CONFIRM_SCRIPT,
            capture_output=True,
            text=True,
            errors="replace",
            # The dialog gives up on its own; this only covers an osascript that
            # never got as far as drawing one.
            timeout=CONFIRM_TIMEOUT + 15,
        )
    except subprocess.TimeoutExpired:
        return False, "I couldn't get an answer about running that, so I left it alone."
    except OSError:
        return False, "I can't ask you to approve that from here, so I didn't run it."

    answer = (proc.stdout or "").strip()
    if proc.returncode != 0 or answer == "declined":
        # A non-zero exit is Escape on the dialog (-128), or no window server to
        # draw it. Both mean the same thing here.
        return False, "You didn't approve that command, so I didn't run it."
    if answer == "timeout":
        return False, "The approval prompt timed out, so I didn't run that."
    if answer == "approved":
        return True, ""
    return False, "I couldn't tell whether that was approved, so I didn't run it."


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

    Commands that only read something -- ls, cat, git status, df, pmset -g batt
    -- run straight away. Anything that could change the Mac makes the user
    approve it in a dialog first, so keep each command to the one thing you
    actually need: chained or clever commands are harder for them to say yes to.
    A few genuinely destructive commands are refused outright. If a command is
    refused or not approved, say so plainly and stop -- do not rephrase it and
    try again, and never split a refused command into smaller pieces to get
    around the refusal.

    Args:
        command: The shell command to run, e.g. "ls ~/Desktop" or "git status".
        cwd: Directory to run the command in. Leave empty for the home directory.
    '''
    command = (command or "").strip()
    if not command:
        return "Error: no command given."

    workdir = os.path.expanduser(cwd.strip()) if cwd and cwd.strip() else os.path.expanduser("~")
    if not os.path.isdir(workdir):
        return f"Error: directory not found: {workdir}"

    verdict, reason = classify(command)

    if verdict == "deny":
        return (
            f"Refused: that command looks like a {reason}, which is not "
            f"something I will run from voice. Run it yourself if you meant it."
        )

    if verdict == "ask":
        mode = _mode()
        if mode == "strict":
            return (
                f"Refused: I can only run commands that read something, and "
                f"{reason}. Run it yourself if you meant it."
            )
        if mode != "open":
            approved, message = _confirm(command, workdir, reason)
            if not approved:
                return message

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
