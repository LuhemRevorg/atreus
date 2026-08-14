"""Open the Arc browser, optionally on a web page.

This is a one-command tool and deliberately stays that way. The bash tool could
run `open -a Arc` just as well, but only if the model composes the right command
on the spot; a named tool makes the common request a lookup rather than a bit of
improvisation, which an 8b model gets wrong more often than it should.

`open` returns as soon as the app is handed off to the launcher, so this does
not block while Arc actually starts.

The URL is passed as an argument to that same command. `open -a Arc <url>` is
one call that already covers both states: a running Arc gets the page as a new
tab, a closed one is launched and opens the page once it is up. We still look at
whether Arc was running beforehand, but only to say which of the two happened --
branching on it would mean writing two paths where macOS provides one.

`-a Arc` matters, and a bare `open <url>` would be wrong here: that hands the URL
to whatever the *default* browser is, which is Safari on a fresh Mac and only
Arc by coincidence.
"""

import re
import subprocess
from urllib.parse import urlsplit

# `open` exits once launchd has accepted the app, not once Arc has drawn a
# window, so this only has to cover a busy launcher -- not a cold start off a
# slow disk.
LAUNCH_TIMEOUT = 10

# Deciding whether Arc was already up. `pgrep` rather than asking AppleScript:
# `application "Arc" is running` needs Automation consent, and a tool that only
# opens tabs should not be the thing that trips a TCC prompt. -x so the helper
# and renderer processes ("Arc Helper (GPU)") don't count as the app.
RUNNING_TIMEOUT = 5

# A scheme, only when it is followed by `//`. Kept separate from the check below
# so `localhost:3000` is read as a host and port, not as a `localhost:` scheme.
_SCHEME = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.\-]*)://")

# A scheme with no `//` after it -- `mailto:`, `javascript:`, `x-apple-...:`.
# The negative lookahead spares `host:8080`, whose colon is a port.
_BARE_SCHEME = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.\-]*):(?!\d)")


def _normalise(raw: str) -> tuple[str, str]:
    """Turn a spoken or typed address into a URL. Returns (url, error).

    The argument reaches us from a model that is reading back speech, so it
    arrives as "github.com", "github dot com" or "<https://github.com>" about as
    often as it arrives as a URL.
    """
    text = raw.strip().strip("<>").strip("\"'")
    if not text:
        return "", ""

    # "github dot com" survives transcription intact often enough to be worth
    # rescuing; a real URL has no spaces, so this cannot damage one.
    text = re.sub(r"\s+dot\s+", ".", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+slash\s+", "/", text, flags=re.IGNORECASE)

    # Whitespace means two different things either side of the first slash. In
    # the host it is transcription noise ("github .com", "github. com") and gets
    # dropped; in the path or query it is a real space the user dictated
    # ("google.com/search?q=cold brew") and has to be encoded, since deleting it
    # would quietly change the search.
    scheme_end = text.find("://")
    cut = text.find("/", scheme_end + 3 if scheme_end != -1 else 0)
    host, path = (text, "") if cut == -1 else (text[:cut], text[cut:])
    text = "".join(host.split()) + re.sub(r"\s+", "%20", path.strip())

    scheme_match = _SCHEME.match(text)
    if scheme_match:
        scheme = scheme_match.group(1).lower()
        if scheme not in ("http", "https"):
            # Not a shell-injection guard -- there is no shell, and argv is safe.
            # This is about `open` being a general dispatcher: it will happily
            # hand `file://`, `ssh://` or a custom scheme to some other app
            # entirely, and a misheard word should not be able to reach one.
            return "", f"I can only open web pages in Arc, not {scheme} links."
    elif _BARE_SCHEME.match(text):
        return "", f"I can only open web pages in Arc, not {_BARE_SCHEME.match(text).group(1)} links."
    else:
        # Bare host: "github.com". Nothing else assumes a scheme for us -- `open`
        # would treat this as a relative file path and fail.
        text = "https://" + text

    parts = urlsplit(text)
    host = parts.hostname or ""
    # A hostname with no dot is either a typo or a local name. Allowing
    # localhost keeps "open localhost 3000" working while still rejecting the
    # stray sentence fragment that a mishearing turns into `https://open the`.
    if not host or ("." not in host and host != "localhost"):
        return "", f"'{raw.strip()}' doesn't look like a web address."

    return text, ""


def _running() -> bool:
    """Whether Arc is already up. False on any doubt -- see the caller."""
    try:
        proc = subprocess.run(
            ["pgrep", "-x", "Arc"],
            capture_output=True,
            timeout=RUNNING_TIMEOUT,
        )
    except (subprocess.TimeoutExpired, OSError):
        # This only picks the wording of a success message, so a failed probe
        # is not worth failing the actual request over.
        return False
    return proc.returncode == 0


def _failure(proc: subprocess.CompletedProcess, subject: str) -> str:
    """Explain a non-zero `open` in one speakable line."""
    # `open` puts its diagnosis on stderr -- most often that Arc is not
    # installed. Passing it along beats a bare exit code the user has to guess
    # at, but it is one line, so it stays speakable.
    detail = (proc.stderr or proc.stdout or "").strip().splitlines()
    if detail and "unable to find application" in detail[0].lower():
        return "I couldn't find Arc installed on this Mac."
    return f"I couldn't open {subject}: {detail[0]}" if detail else f"I couldn't open {subject}."


def arc(url: str = "") -> str:
    '''
    Open the Arc browser on the user's Mac, or open a web page in a new Arc tab.

    Use this whenever the user asks for Arc or for their browser by name, e.g.
    "open Arc", "launch my browser", "bring up Arc", or asks for a site by name,
    e.g. "open github", "pull up gmail", "open youtube dot com". If Arc is
    already running this brings it to the front and adds a tab; if it is closed
    it starts Arc first. Safe to call either way. Do NOT use it to search the web
    or look something up -- that is the web_search tool, which answers the
    question instead of leaving the user to read it themselves.

    Args:
        url: The web page to open, e.g. "github.com" or
            "https://news.ycombinator.com". The scheme may be left off. Only
            http and https pages can be opened. Leave this empty to just bring
            up Arc without opening anything.
    '''
    target, err = _normalise(url or "")
    if err:
        return err

    # No URL: the original behaviour, an app launch and nothing more.
    if not target:
        command = ["open", "-a", "Arc"]
        subject = "Arc"
    else:
        was_running = _running()
        # One call for both states. `open` launches Arc if it has to and passes
        # the URL along either way; splitting this into "launch, wait, then send
        # the URL" would add a race for no gain.
        command = ["open", "-a", "Arc", target]
        subject = "that page"

    try:
        proc = subprocess.run(
            # No shell: argv goes to `open` verbatim, so a URL with `&` or `?`
            # in it needs no quoting and cannot become a second command.
            command,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=LAUNCH_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return "Arc didn't finish opening in time."
    except OSError as e:
        return f"I couldn't run the open command: {e}"

    if proc.returncode != 0:
        return _failure(proc, subject)

    if not target:
        return "Arc is open."

    # Speak the site, not the URL: the reply is read aloud, and "h t t p s colon
    # slash slash" is not something anyone wants to hear.
    host = (urlsplit(target).hostname or "").removeprefix("www.")
    if was_running:
        return f"Opened {host} in a new Arc tab."
    return f"Arc is starting up with {host}."


if __name__ == "__main__":
    import sys

    print(arc(sys.argv[1] if len(sys.argv) > 1 else ""))
