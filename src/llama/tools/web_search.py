"""Search the web and hand back a spoken-length summary.

Two design constraints drive everything here.

The first is that Atreus talks. The caller cannot show a list of ten blue links,
so this tool does the reading and returns prose -- no URLs, no markdown, no
numbered lists. A tool that returned raw results would just move the
summarising into an 8b model that is also trying to hold a conversation.

The second is latency. Every second here is a second of silence in the room, so
the pipeline is bounded at each stage rather than being thorough: one search
call, a few page fetches in parallel, one local summarisation pass. Anything
that misses its timeout is dropped and the summary is written from whatever
arrived, because a slightly thinner answer now beats a better one after the
user has given up.

No API key is required -- DuckDuckGo's HTML endpoint is the default backend.
Setting BRAVE_SEARCH_KEY in .env switches to Brave, which is markedly less
likely to rate-limit a machine that searches all day.
"""

import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import unescape
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlparse

import requests
from dotenv import load_dotenv
from ollama import chat

load_dotenv()

# Reuse the voice loop's model. A second model would be a second multi-gigabyte
# resident set on a laptop that is already holding one.
MODEL = "qwen3:8b"

_BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
_DDG_URL = "https://html.duckduckgo.com/html/"

# DDG's HTML endpoint serves a bot page to anything that looks automated, so
# present as a browser. Brave is a real API and ignores this.
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

SEARCH_TIMEOUT = 8

# Per-page budget. Pages are fetched in parallel, so this is roughly what the
# whole fetch stage costs -- a slow server delays nobody but itself.
PAGE_TIMEOUT = 5

# How many results to ask the backend for. More than we read: the top hit is
# regularly a login wall or a PDF, and the spares cover that without a second
# search.
RESULT_COUNT = 8

# How many pages to actually open. Three sources is enough to notice when they
# disagree, and past that the summariser is the bottleneck, not the network.
PAGES_TO_READ = 3

# A page's worth of text, trimmed to the part that mentions the query. Long
# enough to hold a real answer, short enough that three of them still leave the
# 8b model room to think.
MAX_DOC_CHARS = 1400

# Hard ceiling on the whole source block. Summarising is the slow stage by a
# wide margin and its cost tracks prompt length, so this is the main latency
# dial in the file -- a spoken answer does not get better for having read four
# more paragraphs. Comfortably inside the 8k window the model actually loads
# with, which matters because an overflow is silently truncated from the front,
# taking the instructions with it.
MAX_CONTEXT_CHARS = 4500

# Some servers stream megabytes to a GET. Stop reading once we have plenty.
MAX_DOWNLOAD_BYTES = 600_000

# The summary is spoken, so length is measured in seconds of talking, not
# tokens. Roughly four sentences.
MAX_SUMMARY_TOKENS = 220

_SUMMARY_PROMPT = (
    "You summarise web search results for a voice assistant that reads your "
    "answer out loud.\n"
    "\n"
    "Write two to four short spoken sentences answering the question directly, "
    "in plain speech: no markdown, no bullet points, no headings, no URLs, no "
    "citation numbers. Lead with the answer, then add only the detail that "
    "matters.\n"
    "\n"
    "Use only what the sources below actually say. If they disagree, say so in "
    "a few words. If they do not answer the question, say that plainly instead "
    "of guessing. When a fact depends on who is saying it, name the source the "
    "way a person would -- \"according to Wikipedia\" -- never as a domain or "
    "a link. Say numbers and dates the way they would be spoken."
)


class _ResultParser(HTMLParser):
    """Pull title, URL and snippet out of DDG's HTML result list.

    DDG has no key-free JSON API, so the HTML page is the interface. Parsing it
    with a real parser rather than a regex means a snippet containing <b> tags,
    or an attribute order that changes next week, does not silently return
    nothing.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.results: list[dict] = []
        self._field = ""
        self._buf: list[str] = []
        self._url = ""
        self._pending: dict | None = None

    def handle_starttag(self, tag, attrs):
        if tag != "a" and tag != "div" and tag != "td":
            return
        classes = dict(attrs).get("class", "")
        if "result__a" in classes:
            # A new title closes whatever came before it, so a result missing a
            # snippet cannot swallow the next result's.
            self._flush()
            self._field = "title"
            self._url = _clean_url(dict(attrs).get("href", ""))
        elif "result__snippet" in classes:
            self._field = "snippet"

    def handle_endtag(self, tag):
        # Titles live inside the <a> itself, so its close ends one. Snippets
        # nest both <b> for matched terms and <a> for inline links, so only the
        # enclosing block ends those.
        if self._field == "title" and tag == "a":
            self._store("title")
        elif self._field == "snippet" and tag in ("div", "td"):
            self._store("snippet")

    def close(self):
        # The last result on the page has nothing after it to trigger the flush.
        super().close()
        self._flush()

    def handle_data(self, data):
        if self._field:
            self._buf.append(data)

    def _store(self, field):
        text = _collapse("".join(self._buf))
        self._buf = []
        self._field = ""
        if field == "title":
            self._pending = {"title": text, "url": self._url, "snippet": ""}
        elif getattr(self, "_pending", None):
            self._pending["snippet"] = text
            self._flush()

    def _flush(self):
        pending = getattr(self, "_pending", None)
        self._pending = None
        # Ads and DDG's own "try this instead" rows come through without a real
        # http URL; drop them rather than fetching them.
        if pending and pending["title"] and pending["url"].startswith("http"):
            self.results.append(pending)


class _TextExtractor(HTMLParser):
    """Flatten a page to readable text.

    No readability heuristics -- just drop the tags that never contain prose and
    keep the rest. The summariser tolerates navigation cruft far better than it
    tolerates a missing answer, so this errs toward keeping text.
    """

    _SKIP = {"script", "style", "noscript", "svg", "head", "nav", "footer",
             "header", "form", "aside", "button", "select", "iframe"}
    _BREAK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
              "section", "article", "blockquote"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in self._BREAK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if not self._skip_depth:
            self.parts.append(data)

    def text(self) -> str:
        return _collapse("".join(self.parts))


def _collapse(text: str) -> str:
    """Normalise whitespace, keeping paragraph breaks as single newlines."""
    text = unescape(text)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    return re.sub(r"\n{2,}", "\n", text).strip()


def _clean_url(href: str) -> str:
    """Unwrap DDG's redirector, which hides the real URL behind /l/?uddg=..."""
    href = unescape(href or "")
    if href.startswith("//"):
        href = "https:" + href
    if "duckduckgo.com/l/" in href:
        target = parse_qs(urlparse(href).query).get("uddg", [""])[0]
        return target or href
    return href


def _search_brave(query: str, key: str) -> tuple[list[dict], str]:
    try:
        resp = requests.get(
            _BRAVE_URL,
            params={"q": query, "count": RESULT_COUNT},
            headers={"X-Subscription-Token": key, "Accept": "application/json"},
            timeout=SEARCH_TIMEOUT,
        )
    except requests.RequestException as e:
        return [], f"I couldn't reach the search engine: {e}"

    if resp.status_code in (401, 403):
        return [], "The Brave search key in the .env file was rejected."
    if resp.status_code == 429:
        return [], "The search engine is rate limiting me right now."
    if resp.status_code != 200:
        return [], f"The search engine returned an error ({resp.status_code})."

    results = []
    for item in resp.json().get("web", {}).get("results", []):
        results.append({
            "title": _collapse(re.sub(r"<[^>]+>", "", item.get("title", ""))),
            "url": item.get("url", ""),
            # Brave marks matched terms with <strong>.
            "snippet": _collapse(re.sub(r"<[^>]+>", "", item.get("description", ""))),
        })
    return results, ""


def _search_ddg(query: str) -> tuple[list[dict], str]:
    try:
        # POST, not GET: the GET form of this endpoint answers a bare query with
        # a redirect to the JS-only site, which has no results in its HTML.
        resp = requests.post(
            _DDG_URL,
            data={"q": query},
            headers={"User-Agent": _UA},
            timeout=SEARCH_TIMEOUT,
        )
    except requests.RequestException as e:
        return [], f"I couldn't reach the search engine: {e}"

    if resp.status_code == 202 or resp.status_code == 429:
        return [], (
            "The search engine is rate limiting me. Adding a BRAVE_SEARCH_KEY "
            "to the .env file would fix that for good."
        )
    if resp.status_code != 200:
        return [], f"The search engine returned an error ({resp.status_code})."

    parser = _ResultParser()
    parser.feed(resp.text)
    parser.close()
    return parser.results, ""


def _search(query: str) -> tuple[list[dict], str]:
    key = os.getenv("BRAVE_SEARCH_KEY", "").strip()
    if key:
        results, err = _search_brave(query, key)
        # A missing key is a config problem worth reporting, but a Brave outage
        # or a spent quota should not take web search down when there is a
        # key-free backend sitting right there.
        if results:
            return results, ""
        results, ddg_err = _search_ddg(query)
        return results, "" if results else (err or ddg_err)
    return _search_ddg(query)


def _relevant_slice(text: str, query: str) -> str:
    """Trim a page to the region that actually mentions the query.

    The answer to "when was X released" is rarely in a page's first 2000
    characters -- those are the cookie banner and the nav. Centre the window on
    the first query term we can find instead of taking the head blindly.
    """
    if len(text) <= MAX_DOC_CHARS:
        return text

    terms = [t for t in re.findall(r"\w+", query.lower()) if len(t) > 3]
    lowered = text.lower()
    hit = -1
    for term in terms:
        found = lowered.find(term)
        if found != -1 and (hit == -1 or found < hit):
            hit = found

    if hit == -1:
        return text[:MAX_DOC_CHARS]

    # A third of the window before the hit, so the sentence it sits in keeps
    # its lead-in rather than starting mid-clause.
    start = max(0, hit - MAX_DOC_CHARS // 3)
    return text[start:start + MAX_DOC_CHARS]


def _fetch(result: dict, query: str) -> dict | None:
    """Download one result and reduce it to a slice of readable text."""
    try:
        with requests.get(
            result["url"],
            headers={"User-Agent": _UA, "Accept": "text/html,*/*"},
            timeout=PAGE_TIMEOUT,
            # Cap the read rather than trusting Content-Length, which a chunked
            # response does not send at all.
            stream=True,
        ) as resp:
            if resp.status_code != 200:
                return None
            ctype = resp.headers.get("Content-Type", "")
            # PDFs and downloads would come through as mojibake and poison the
            # summary; the snippet for this result is still in play.
            if "html" not in ctype and "text/plain" not in ctype:
                return None
            body = resp.raw.read(MAX_DOWNLOAD_BYTES, decode_content=True)
    except (requests.RequestException, OSError):
        return None

    # requests falls back to ISO-8859-1 for any text/* that does not name a
    # charset, which turns every accent and dash on an unlabelled UTF-8 page
    # into mojibake. UTF-8 is both far likelier and self-validating, so try it
    # first and only take the header's word for it if that fails.
    encoding = resp.encoding
    if not encoding or encoding.lower() in ("iso-8859-1", "latin-1"):
        try:
            html = body.decode("utf-8")
        except UnicodeDecodeError:
            html = body.decode(encoding or "utf-8", errors="replace")
    else:
        html = body.decode(encoding, errors="replace")

    extractor = _TextExtractor()
    try:
        extractor.feed(html)
    except Exception:
        # A malformed page can trip the parser mid-document. Whatever it
        # collected before that is still usable.
        pass

    text = extractor.text()
    # Below this it is a consent wall or a JS shell, and passing it off as a
    # source would only dilute the ones that worked.
    if len(text) < 200:
        return None

    return {
        "title": result["title"],
        "source": urlparse(result["url"]).netloc.replace("www.", ""),
        "text": _relevant_slice(text, query),
    }


def _gather(results: list[dict], query: str) -> list[dict]:
    """Fetch the top results in parallel, keeping the search's ranking."""
    # One page per domain. A site that owns the top three hits would otherwise
    # spend the whole fetch budget agreeing with itself, and "both sources say"
    # means nothing when both sources are the same publisher.
    picks, seen = [], set()
    for r in results:
        domain = urlparse(r["url"]).netloc.replace("www.", "")
        if domain in seen:
            continue
        seen.add(domain)
        picks.append(r)
        if len(picks) == PAGES_TO_READ:
            break
    if not picks:
        return []
    docs: dict[int, dict] = {}
    with ThreadPoolExecutor(max_workers=len(picks)) as executor:
        futures = {executor.submit(_fetch, r, query): i for i, r in enumerate(picks)}
        for future in as_completed(futures):
            try:
                doc = future.result()
            except Exception:
                continue
            if doc:
                docs[futures[future]] = doc
    # as_completed yields by finish time; rank order is the more useful one, so
    # the best result leads the prompt.
    return [docs[i] for i in sorted(docs)]


def _context(results: list[dict], docs: list[dict]) -> str:
    """Build the source block, preferring fetched pages over snippets."""
    fetched = {d["source"] for d in docs}
    blocks = [f"Source: {d['source']}\nTitle: {d['title']}\n{d['text']}" for d in docs]

    # Snippets are gap-fill, not extra credit. A page we actually read beats a
    # two-line snippet on every axis, so only top up to the number of sources
    # we meant to have -- when a fetch failed, or when they all did and these
    # are the only evidence left.
    for r in results:
        if len(blocks) >= PAGES_TO_READ:
            break
        source = urlparse(r["url"]).netloc.replace("www.", "")
        if source not in fetched and r["snippet"]:
            fetched.add(source)
            blocks.append(f"Source: {source}\nTitle: {r['title']}\n{r['snippet']}")

    return "\n\n---\n\n".join(blocks)[:MAX_CONTEXT_CHARS]


def _summarise(query: str, context: str) -> str:
    messages = [
        {"role": "system", "content": _SUMMARY_PROMPT},
        {"role": "user", "content": f"Question: {query}\n\nSources:\n\n{context}"},
    ]
    options = {
        "temperature": 0.2,
        # Match the window the model is already loaded with. Asking for more
        # does not get it -- ollama clamps to what the model was started with --
        # it only invites a prompt this size cannot hold. MAX_CONTEXT_CHARS is
        # what actually keeps us inside it.
        "num_ctx": 8192,
        "num_predict": MAX_SUMMARY_TOKENS,
    }
    # think=False matters for latency: qwen3 will otherwise reason at length
    # about a summary the user is waiting to hear.
    response = chat(model=MODEL, messages=messages, think=False, options=options)
    return _spoken(response.message.content or "")


def _spoken(text: str) -> str:
    """Strip the formatting a model reaches for even when told not to."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"\[(\d+)\]", "", text)
    text = re.sub(r"[*_`#]+", "", text)
    # Leading list markers, once per line, without touching a minus sign in
    # front of a number.
    text = re.sub(r"(?m)^\s*(?:[-•]\s+|\d+[.)]\s+)", "", text)
    return _collapse(text).replace("\n", " ")


def _from_snippets(results: list[dict]) -> str:
    """Last resort when the summariser is unavailable: read the top snippets.

    Worse than a summary and knowingly so -- but the search did succeed, and
    the caller can still make something of it.
    """
    lines = []
    for r in results[:3]:
        if r["snippet"]:
            source = urlparse(r["url"]).netloc.replace("www.", "")
            lines.append(f"{source} says: {r['snippet']}")
    if not lines:
        return "I found results, but none of them had anything readable in them."
    return "I couldn't summarise these, so here's what the top results say. " + " ".join(lines)


def web_search(query: str) -> str:
    '''
    Search the web and return a short summary of what the results say.

    Use this for anything you do not already know or cannot know on your own:
    current events, today's news, prices, sports scores, weather, opening hours,
    release dates, "what is" and "who is" questions about things outside your
    training, or any fact the user expects to be up to date. The result comes
    back as a couple of plain sentences that you can read out as they are. Do
    NOT use it for questions you can answer yourself, for anything on the user's
    own machine -- that is the bash tool -- or to look up documentation for code
    you are about to have Claude write.

    Args:
        query: What to search for, phrased as a search query or a plain
            question, e.g. "canada tariff announcement today" or "how tall is
            the CN tower". Put everything that matters into this one string,
            including any place or date the user mentioned, since nothing else
            about the conversation is passed along.
    '''
    query = (query or "").strip()
    if not query:
        return "Error: no search query given."

    results, err = _search(query)
    if err:
        return err
    if not results:
        return f"I searched for '{query}' but didn't get any results back."

    docs = _gather(results, query)
    context = _context(results, docs)
    if not context:
        return f"I found results for '{query}', but couldn't read any of them."

    try:
        summary = _summarise(query, context)
    except Exception as e:
        print(f"web_search: summarisation failed: {e!r}")
        return _from_snippets(results)

    # An empty completion is rare but real -- a fallback the caller can speak
    # beats a blank turn.
    return summary or _from_snippets(results)


if __name__ == "__main__":
    import sys

    print(web_search(" ".join(sys.argv[1:]) or "what is the fastest animal"))
