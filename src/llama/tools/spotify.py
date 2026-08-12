"""Control the Spotify desktop app through AppleScript.

Spotify ships an AppleScript dictionary, so this drives the app the user is
already logged into -- no Web API, no OAuth, no premium requirement. The
tradeoff is that everything here only works while the desktop client is on this
Mac, which is the same machine the voice loop runs on anyway.
"""

import os
import re
import subprocess
import time

import requests
from dotenv import load_dotenv

load_dotenv()

# osascript talks to another app over Apple events, so a hung or beachballed
# Spotify would otherwise wedge the blocking voice loop. Well under a
# listener's patience, and generous next to a normal event round trip (~50ms).
TIMEOUT = 10

# Starting Spotify cold is far slower than talking to a running one, and it is
# the one case where waiting beats failing -- the user asked for music and the
# app is on its way up. Only the `play` path can pay this.
LAUNCH_TIMEOUT = 35

# Spotify's AppleScript dictionary can play a track id but cannot search for
# one, so naming a song means asking the Web API what it is first. That is a
# network round trip in front of a spoken request, so keep it short -- better to
# say the lookup timed out than to leave the room silent.
HTTP_TIMEOUT = 8

_TOKEN_URL = "https://accounts.spotify.com/api/token"
_SEARCH_URL = "https://api.spotify.com/v1/search"

# ASCII unit separator: a field delimiter that cannot show up inside a track,
# artist or album name, so splitting the reply back apart is unambiguous.
SEP = "\x1f"

_NOT_RUNNING = "NOTRUNNING"
_STOPPED = "STOPPED"

# Spoken requests arrive as whatever the transcriber heard, so accept the
# phrasings that mean the same thing rather than making the model guess the
# one blessed keyword.
_ALIASES = {
    "play": "play", "resume": "play", "start": "play", "unpause": "play",
    "pause": "pause", "stop": "pause", "hold": "pause",
    "toggle": "toggle", "playpause": "toggle", "playorpause": "toggle",
    "next": "next", "skip": "next", "forward": "next", "nexttrack": "next",
    "skiptrack": "next", "skipsong": "next", "nextsong": "next",
    "previous": "previous", "prev": "previous", "back": "previous",
    "goback": "previous", "previoustrack": "previous", "last": "previous",
    "current": "current", "status": "current", "nowplaying": "current",
    "whatisplaying": "current", "track": "current", "info": "current",
    "song": "current", "what": "current",
}

# Read the now-playing fields. Each one is guarded: ads and some podcast
# episodes expose a partial track record, and one missing property would
# otherwise abort the whole script instead of costing us a single field.
#
# Variable names are deliberately long. Inside a `tell application` block the
# app's dictionary shadows the namespace, and short names collide with its
# terms -- `st` alone is a syntax error against Spotify's sdef.
_READ_TRACK = '''
    set trackState to (player state as text)
    set trackName to ""
    set trackArtist to ""
    set trackAlbum to ""
    set trackDuration to 0
    set trackPosition to 0
    try
        set trackName to name of current track
    end try
    try
        set trackArtist to artist of current track
    end try
    try
        set trackAlbum to album of current track
    end try
    try
        set trackDuration to duration of current track
    end try
    try
        set trackPosition to player position
    end try
    set savedDelimiters to AppleScript's text item delimiters
    set AppleScript's text item delimiters to (ASCII character 31)
    set assembled to {trackState, trackName, trackArtist, trackAlbum, ¬
        (trackDuration as text), (trackPosition as text)} as text
    set AppleScript's text item delimiters to savedDelimiters
    return assembled
'''


# Both loops below are no-ops against an already-running Spotify (they exit on
# the first pass), so the warm path pays nothing for the cold path's care.
_PLAY = f'''
    play
    -- A just-launched Spotify accepts `play` before its track metadata exists,
    -- and reporting then would describe a blank track. Wait for a real one.
    repeat 40 times
        try
            if (name of current track) is not "" then exit repeat
        end try
        delay 0.25
    end repeat
    -- That same window silently swallows the `play` itself, leaving the app
    -- open and paused. Re-assert once it is genuinely ready.
    if (player state as text) is not "playing" then
        play
        delay 0.3
    end if
{_READ_TRACK}
'''


# Playing a named song is a two-step: the Web API turns "gravity" into a track
# id, then the desktop app plays it. Only the second half needs Spotify open,
# so a bad search never launches the app for nothing.
def _play_track(track_id: str) -> str:
    return f'''
    play track "spotify:track:{track_id}"
    -- The track loads asynchronously, and until it lands `current track` still
    -- describes whatever was there before. Poll for our own id so the read-back
    -- can only ever name the song we actually asked for -- matching on the name
    -- being non-empty would happily report the previous track.
    repeat 40 times
        try
            if (id of current track) contains "{track_id}" then exit repeat
        end try
        delay 0.25
    end repeat
    -- Same swallowed-command window as the resume path: a cold Spotify can take
    -- the track and stay paused.
    if (player state as text) is not "playing" then
        play
        delay 0.3
    end if
{_READ_TRACK}
'''


# Client credentials are good for an hour. Re-minting one per song would add a
# second round trip to every request, so hold it until it is nearly stale.
_token_cache = {"value": "", "expires_at": 0.0}


def _access_token(force: bool = False) -> tuple[str, str]:
    """Fetch an app-only token, or explain why we cannot. Returns (token, error)."""
    client_id = os.getenv("SPOTIFY_CLIENT_ID")
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
    if not client_id or not client_secret:
        return "", (
            "I can't search Spotify without API credentials. Add "
            "SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET to the .env file."
        )

    # 60s of headroom so a token that is valid when we check cannot expire in
    # the gap before the search actually lands.
    if not force and _token_cache["value"] and time.time() < _token_cache["expires_at"] - 60:
        return _token_cache["value"], ""

    try:
        resp = requests.post(
            _TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=(client_id, client_secret),
            timeout=HTTP_TIMEOUT,
        )
    except requests.RequestException as e:
        return "", f"I couldn't reach Spotify to search: {e}"

    if resp.status_code == 400 or resp.status_code == 401:
        return "", "Spotify rejected the API credentials in the .env file."
    if resp.status_code != 200:
        return "", f"Spotify's login returned an error ({resp.status_code})."

    payload = resp.json()
    token = payload.get("access_token", "")
    if not token:
        return "", "Spotify didn't hand back an access token."

    _token_cache["value"] = token
    _token_cache["expires_at"] = time.time() + float(payload.get("expires_in", 3600))
    return token, ""


def _search_track(query: str) -> tuple[str, str]:
    """Resolve a spoken song name to a track id. Returns (track_id, error)."""
    token, err = _access_token()
    if err:
        return "", err

    def _get(bearer: str):
        return requests.get(
            _SEARCH_URL,
            params={"q": query, "type": "track", "limit": 1},
            headers={"Authorization": f"Bearer {bearer}"},
            timeout=HTTP_TIMEOUT,
        )

    try:
        resp = _get(token)
        # A cached token can be revoked server-side before its stated expiry.
        # One forced re-mint is cheaper than failing a request the user spoke.
        if resp.status_code == 401:
            token, err = _access_token(force=True)
            if err:
                return "", err
            resp = _get(token)
    except requests.RequestException as e:
        return "", f"I couldn't reach Spotify to search: {e}"

    if resp.status_code != 200:
        return "", f"Spotify's search returned an error ({resp.status_code})."

    items = resp.json().get("tracks", {}).get("items", [])
    if not items:
        return "", f"I couldn't find anything on Spotify for '{query}'."

    track_id = items[0].get("id") or ""
    # The id goes straight into an AppleScript string literal. Spotify ids are
    # base62, so anything else is either a bad response or an injection attempt
    # -- refuse rather than escape.
    if not re.fullmatch(r"[A-Za-z0-9]{1,64}", track_id):
        return "", f"Spotify returned an unusable result for '{query}'."
    return track_id, ""


def _script(body: str, launch: bool = False) -> str:
    """Wrap an action in the guard that keeps us from launching Spotify.

    A bare `tell application "Spotify"` launches the app, so asking "is anything
    playing?" while Spotify is closed would start it up just to answer "no".
    Only `play` opts into launching.
    """
    if not launch:
        return f'''
if not (application "Spotify" is running) then return "{_NOT_RUNNING}"
tell application "Spotify"
{body}
end tell
'''
    return f'''
if not (application "Spotify" is running) then
    -- `launch` rather than `activate`: starting music should not steal the
    -- focus of whatever the user is actually looking at.
    launch application "Spotify"
    repeat 60 times
        if application "Spotify" is running then exit repeat
        delay 0.25
    end repeat
    -- Running is not the same as ready. A cold Spotify shows up in the process
    -- list well before it will answer for its player, and a `play` sent into
    -- that window is silently dropped. Poll a cheap property until it stops
    -- erroring, rather than guessing at a fixed delay.
    repeat 60 times
        try
            -- `player state` is Spotify's own terminology, so the probe has to
            -- sit inside a tell block to parse at all.
            tell application "Spotify" to get player state
            exit repeat
        on error
            delay 0.25
        end try
    end repeat
end if
tell application "Spotify"
{body}
end tell
'''


def _osascript(script: str, timeout: int = TIMEOUT) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            errors="replace",
            # Nothing here can answer a prompt, and osascript should never ask.
            stdin=subprocess.DEVNULL,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"Spotify did not respond within {timeout} seconds."
    except OSError as e:
        return False, f"Could not run osascript: {e}"

    if proc.returncode == 0:
        return True, proc.stdout.strip()

    err = (proc.stderr or "").strip()
    # TCC gate. Worth calling out precisely, because the fix is a checkbox in
    # System Settings and the raw AppleScript error says nothing about that.
    if "-1743" in err or "Not authorized" in err:
        return False, (
            "macOS is blocking me from controlling Spotify. Allow it under "
            "System Settings > Privacy & Security > Automation."
        )
    if "-600" in err or "isn't running" in err:
        return False, "Spotify isn't running."
    return False, f"Spotify returned an error: {err or 'unknown error'}"


def _num(value: str) -> float:
    """Parse a number out of AppleScript, which may hand back 1.234E+5."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _clock(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _state_of(payload: str) -> str:
    """The player state Spotify actually landed in, per the read-back."""
    return payload.split(SEP)[0] if SEP in payload else ""


def _describe(payload: str, prefix: str = "") -> str:
    """Turn the raw field dump into one spoken sentence."""
    if payload == _NOT_RUNNING:
        return "Spotify isn't running."
    if payload == _STOPPED:
        return "Spotify is open but nothing is queued up."

    fields = payload.split(SEP)
    if len(fields) < 6:
        return "Spotify didn't report a track."

    state, name, artist, _album, duration_ms, position_s = fields[:6]
    if not name:
        return "Spotify is playing something without track details -- probably an ad."

    song = f"{name} by {artist}" if artist else name

    if prefix:
        lead = f"{prefix} {song}"
    elif state == "playing":
        lead = f"Playing {song}"
    else:
        lead = f"Paused on {song}"

    # Spotify reports duration in milliseconds but position in seconds.
    total = _num(duration_ms) / 1000.0
    at = _num(position_s)
    # Only worth voicing once we are actually into the track; "0:03 of 3:20"
    # right after a skip is noise.
    if total > 0 and at >= 5:
        return f"{lead}, {_clock(at)} into {_clock(total)}."
    if total > 0:
        return f"{lead}, {_clock(total)} long."
    return f"{lead}."


def spotify(action: str = "current", query: str = "") -> str:
    '''
    Control the Spotify app on the user's Mac, or report what is playing.

    Use this for music playback requests: playing a specific song, starting or
    resuming music, pausing it, skipping to the next song, going back a song, or
    answering "what song is this?" and "what's playing?". Spotify must be the app
    the user plays music through. Report the result back in a short sentence. If
    asked to play a song call the end conversation tool right after playing.

    Args:
        action: One of "play", "pause", "next", "previous", or "current".
            "play" resumes playback and will open Spotify if it is closed.
            "pause" pauses it. "next" skips to the next song, "previous" goes
            back one. "current" reports the track, artist and progress without
            changing playback. Defaults to "current".
        query: The song to play, when the user names one -- for example
            "gravity" or "gravity by john mayer". Include the artist if the user
            said one. Leave this empty to resume whatever was already queued, and
            for every action other than "play".
    '''
    # The model sometimes sends "next track" or "now playing" instead of the
    # bare keyword; normalise before matching so those do not become errors.
    key = "".join(ch for ch in (action or "").lower() if ch.isalnum())
    # An omitted, empty or unset action is a status question, matching the
    # documented default. Models pass "" and None more often than they omit.
    verb = "current" if not key or key == "none" else _ALIASES.get(key)

    song = (query or "").strip()
    if verb is None:
        # "play gravity" arrives as one string often enough to be worth
        # rescuing: if the first word is a verb we know, the rest is the song.
        head, _, tail = (action or "").strip().partition(" ")
        head_key = "".join(ch for ch in head.lower() if ch.isalnum())
        if tail.strip() and _ALIASES.get(head_key) == "play":
            verb, song = "play", song or tail.strip()
        else:
            return (
                f"I don't know how to '{action}' Spotify. I can play, pause, "
                f"skip, go back, or say what's playing."
            )

    # Naming a song is a play request whatever verb came with it -- the model
    # sometimes pairs a query with "current" or leaves the action off entirely.
    if song and verb in ("current", "play"):
        verb = "play"
    elif song:
        # Pausing or skipping cannot honour a song name; do the action asked for
        # rather than silently playing something the user did not ask to start.
        song = ""

    if verb == "current":
        ok, out = _osascript(_script(_READ_TRACK))
        return out if not ok else _describe(out)

    if verb == "play":
        body = _PLAY
        if song:
            # Search before touching Spotify, so a song we cannot find never
            # launches the app or interrupts what is already playing.
            track_id, err = _search_track(song)
            if err:
                return err
            body = _play_track(track_id)
        # Read the track back in the same script: a second osascript call would
        # be a second Apple event round trip, and could race with the state we
        # just changed. The delay lets that state settle before we read it.
        ok, out = _osascript(_script(body, launch=True), timeout=LAUNCH_TIMEOUT)
        if not ok:
            return out
        if out == _NOT_RUNNING:
            return "I couldn't get Spotify to open."
        if out == _STOPPED:
            return (
                f"Spotify wouldn't start '{song}'." if song
                else "Spotify is open, but there's nothing queued to play."
            )
        # Trust the read-back over the command. A track still loading can land
        # paused, and claiming "Playing" while the room stays silent is worse
        # than saying it did not take.
        if _state_of(out) != "playing":
            return _describe(out, prefix="Spotify didn't start playing. It's still paused on")
        return _describe(out, prefix="Playing")

    if verb == "pause":
        ok, out = _osascript(_script(f"    pause\n    delay 0.3\n{_READ_TRACK}"))
        if not ok:
            return out
        if out == _NOT_RUNNING:
            return "Spotify isn't running, so there's nothing to pause."
        if out == _STOPPED:
            return "Nothing was playing."
        if _state_of(out) == "playing":
            # Seen after a track change: the load finishes and resumes right
            # after the pause lands, undoing it.
            return _describe(out, prefix="Spotify didn't pause. It's still playing")
        return _describe(out, prefix="Paused")

    # next / previous
    command = "next track" if verb == "next" else "previous track"
    # Spotify swaps the current track asynchronously, so reading it back in the
    # same breath returns the old one. A short delay is the difference between
    # naming the song we moved to and the song we moved off.
    ok, out = _osascript(_script(f"    {command}\n    delay 0.4\n{_READ_TRACK}"))
    if not ok:
        return out
    if out == _NOT_RUNNING:
        return "Spotify isn't running, so there's nothing to skip."
    if out == _STOPPED:
        return "Nothing is playing right now."
    return _describe(out, prefix="Skipped to" if verb == "next" else "Went back to")


if __name__ == "__main__":
    import sys

    print(spotify(
        sys.argv[1] if len(sys.argv) > 1 else "current",
        " ".join(sys.argv[2:]),
    ))
