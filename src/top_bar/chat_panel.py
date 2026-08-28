"""The chat popover that hangs off the menu bar icon.

An editable NSTextField cannot live inside an NSMenu -- menu tracking runs its
own modal event loop and eats keystrokes -- so the chat is an NSPopover anchored
to the status item's button instead. A popover is a real window, so typing,
scrolling and first-responder handling all work normally.
"""

import time

import objc
from AppKit import (
    NSApplication,
    NSAttributedString,
    NSBezelBorder,
    NSBezelStyleInline,
    NSButton,
    NSColor,
    NSFont,
    NSFontWeightSemibold,
    NSFocusRingTypeNone,
    NSPopover,
    NSPopoverBehaviorTransient,
    NSScrollView,
    NSTextAlignmentCenter,
    NSTextField,
    NSTextView,
    NSView,
    NSViewController,
    NSViewWidthSizable,
    NSViewHeightSizable,
    NSViewMinXMargin,
    NSViewMinYMargin,
    NSTextFieldRoundedBezel,
)
from Foundation import (
    NSMakeRect,
    NSMakeRange,
    NSMakeSize,
    NSMutableAttributedString,
    NSObject,
    NSRunLoop,
    NSRunLoopCommonModes,
    NSTimer,
)

# NSMinYEdge -- makes the popover hang below the menu bar.
MIN_Y_EDGE = 3
# NSTextView wants a "no practical limit" height rather than a real bound.
UNBOUNDED = 1.0e7

WIDTH = 380
HEIGHT = 460
PAD = 12
INPUT_HEIGHT = 26
HEADER_HEIGHT = 22
ARROW_WIDTH = 18
COUNTER_WIDTH = 84
CHIP_GAP = 6

THINKING = "thinking…"

# Seconds after a dismissal during which a status item click is ignored.
REOPEN_GUARD = 0.35

# How often the main thread drains the handler's reply queue.
POLL_INTERVAL = 0.15

# The chat the backend already holds when the panel comes up.
INITIAL_CHAT_ID = 0


def _body_attrs():
    return {
        "NSFont": NSFont.systemFontOfSize_(13),
        "NSColor": NSColor.labelColor(),
    }


def _name_attrs(color):
    return {
        "NSFont": NSFont.systemFontOfSize_weight_(11, NSFontWeightSemibold),
        "NSColor": color,
    }


def _chip(title, target, action):
    button = NSButton.buttonWithTitle_target_action_(title, target, action)
    button.setBezelStyle_(NSBezelStyleInline)
    button.setFont_(NSFont.systemFontOfSize_(11))
    button.setFocusRingType_(NSFocusRingTypeNone)
    return button


class ChatPanel(NSObject):
    """Owns the popover, the transcript view and the input field.

    The panel talks to the request handler through four callables, and never
    blocks on any of them:

    * ``responder(text, chat_id)`` -- hand a message to the handler and return
      immediately. The reply comes back later, through ``poll``.
    * ``on_new_chat() -> chat_id`` -- start a fresh conversation on the backend
      and return the id it filed it under. Must return that id.
    * ``on_delete_chat(chat_id)`` -- drop that conversation on the backend.
    * ``poll() -> iterable of (chat_id, reply)`` -- drain whatever replies are
      ready, without waiting. Called on a timer on the main thread.

    Every conversation the backend holds is mirrored here as one entry in
    ``_chats``, keyed by the same id, so a reply that lands while the user is
    reading a different chat is still filed in the right transcript. Keying by
    id rather than position is what makes deletion cheap: closing one chat
    leaves every other id exactly where it was. The backend is assumed to start
    with exactly one chat, ``INITIAL_CHAT_ID``.
    """

    def initWithResponder_onNewChat_onDeleteChat_onPoll_(
        self, responder, on_new_chat, on_delete_chat, poll
    ):
        self = objc.super(ChatPanel, self).init()
        if self is None:
            return None

        self._responder = responder
        self._on_new_chat = on_new_chat
        self._on_delete_chat = on_delete_chat
        self._poll = poll
        # chat_id -> entry, plus the order they were created in, which is the
        # order the ‹ › buttons walk.
        self._chats = {}
        self._order = []
        self._active = None
        # A transient popover dismisses itself on the same click that fires the
        # status item's action, so a click while open would close and instantly
        # reopen it. Remember when it closed and swallow that follow-up click.
        self._closed_at = 0.0

        root = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, WIDTH, HEIGHT))

        self._input = NSTextField.alloc().initWithFrame_(
            NSMakeRect(PAD, PAD, WIDTH - 2 * PAD, INPUT_HEIGHT)
        )
        self._input.setBezelStyle_(NSTextFieldRoundedBezel)
        self._input.setPlaceholderString_("Ask Atreus…")
        self._input.setFont_(NSFont.systemFontOfSize_(13))
        self._input.setAutoresizingMask_(NSViewWidthSizable | NSViewMinYMargin)
        self._input.setTarget_(self)
        # NSTextField fires its action on Return.
        self._input.setAction_("send:")
        root.addSubview_(self._input)

        header_y = HEIGHT - PAD - HEADER_HEIGHT
        new_chat = _chip("New Chat", self, "newChat:")
        new_chat.sizeToFit()
        size = new_chat.frame().size
        new_chat.setFrame_(
            NSMakeRect(WIDTH - PAD - size.width, header_y, size.width, HEADER_HEIGHT)
        )
        new_chat.setAutoresizingMask_(NSViewMinXMargin | NSViewMinYMargin)
        root.addSubview_(new_chat)

        close = _chip("✕", self, "closeChat:")
        close.sizeToFit()
        close_width = close.frame().size.width
        close.setFrame_(
            NSMakeRect(
                WIDTH - PAD - size.width - CHIP_GAP - close_width,
                header_y,
                close_width,
                HEADER_HEIGHT,
            )
        )
        close.setAutoresizingMask_(NSViewMinXMargin | NSViewMinYMargin)
        root.addSubview_(close)

        prev = _chip("‹", self, "prevChat:")
        prev.setFrame_(NSMakeRect(PAD, header_y, ARROW_WIDTH, HEADER_HEIGHT))
        prev.setAutoresizingMask_(NSViewMinYMargin)
        root.addSubview_(prev)

        counter_x = PAD + ARROW_WIDTH + 2
        self._counter = NSTextField.labelWithString_("")
        self._counter.setFont_(NSFont.systemFontOfSize_(11))
        self._counter.setTextColor_(NSColor.secondaryLabelColor())
        self._counter.setAlignment_(NSTextAlignmentCenter)
        self._counter.setFrame_(
            NSMakeRect(counter_x, header_y, COUNTER_WIDTH, HEADER_HEIGHT)
        )
        self._counter.setAutoresizingMask_(NSViewMinYMargin)
        root.addSubview_(self._counter)

        next_chat = _chip("›", self, "nextChat:")
        next_chat.setFrame_(
            NSMakeRect(counter_x + COUNTER_WIDTH + 2, header_y, ARROW_WIDTH, HEADER_HEIGHT)
        )
        next_chat.setAutoresizingMask_(NSViewMinYMargin)
        root.addSubview_(next_chat)

        scroll_y = PAD + INPUT_HEIGHT + 8
        scroll = NSScrollView.alloc().initWithFrame_(
            NSMakeRect(PAD, scroll_y, WIDTH - 2 * PAD, header_y - scroll_y - 6)
        )
        scroll.setHasVerticalScroller_(True)
        scroll.setAutohidesScrollers_(True)
        scroll.setBorderType_(NSBezelBorder)
        scroll.setDrawsBackground_(False)
        scroll.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)

        inner = scroll.contentSize()
        text = NSTextView.alloc().initWithFrame_(
            NSMakeRect(0, 0, inner.width, inner.height)
        )
        text.setMinSize_(NSMakeSize(0, inner.height))
        text.setMaxSize_(NSMakeSize(UNBOUNDED, UNBOUNDED))
        text.setVerticallyResizable_(True)
        text.setHorizontallyResizable_(False)
        text.setAutoresizingMask_(NSViewWidthSizable)
        text.textContainer().setContainerSize_(NSMakeSize(inner.width, UNBOUNDED))
        text.textContainer().setWidthTracksTextView_(True)
        text.setEditable_(False)
        text.setSelectable_(True)
        text.setDrawsBackground_(False)
        text.setTextContainerInset_(NSMakeSize(4, 8))
        scroll.setDocumentView_(text)

        self._text = text
        root.addSubview_(scroll)

        controller = NSViewController.alloc().initWithNibName_bundle_(None, None)
        controller.setView_(root)

        self._popover = NSPopover.alloc().init()
        self._popover.setContentViewController_(controller)
        self._popover.setContentSize_(NSMakeSize(WIDTH, HEIGHT))
        # Transient: clicking anywhere outside dismisses it, like a menu would.
        self._popover.setBehavior_(NSPopoverBehaviorTransient)
        self._popover.setAnimates_(True)
        self._popover.setDelegate_(self)

        self._track(INITIAL_CHAT_ID)
        self._activate(INITIAL_CHAT_ID)

        # Replies are drained on the main thread, so `_deliver` can touch AppKit
        # directly. The timer runs whether or not the popover is open -- a chat
        # left in the background still has to collect its answer.
        self._timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            POLL_INTERVAL, self, "tick:", None, True
        )
        NSRunLoop.currentRunLoop().addTimer_forMode_(self._timer, NSRunLoopCommonModes)

        return self

    # -- showing / hiding ---------------------------------------------------

    @objc.python_method
    def is_shown(self):
        return self._popover.isShown()

    @objc.python_method
    def toggle(self, button):
        if self._popover.isShown():
            self.hide()
        elif time.monotonic() - self._closed_at > REOPEN_GUARD:
            self.show(button)

    @objc.python_method
    def show(self, button):
        # An accessory app is not frontmost, and a popover of a background app
        # never becomes key -- so claim activation before showing it.
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self._popover.showRelativeToRect_ofView_preferredEdge_(
            button.bounds(), button, MIN_Y_EDGE
        )
        window = self._popover.contentViewController().view().window()
        if window is not None:
            window.makeFirstResponder_(self._input)

    @objc.python_method
    def hide(self):
        self._popover.performClose_(None)

    # NSPopoverDelegate
    def popoverDidClose_(self, _notification):
        self._closed_at = time.monotonic()

    # -- chats --------------------------------------------------------------

    def newChat_(self, _sender):
        self.new_chat()

    def prevChat_(self, _sender):
        self._step(-1)

    def nextChat_(self, _sender):
        self._step(1)

    def closeChat_(self, _sender):
        self.close_chat()

    @objc.python_method
    def new_chat(self):
        """Ask the backend for a fresh conversation and switch to it.

        The old transcript is kept, not wiped: a reply still in flight for it
        arrives tagged with its chat id and lands where it belongs.
        """
        chat_id = self._on_new_chat()
        if chat_id in self._chats:
            # The backend handed back an id already in use; nothing sane to
            # show, so just go there rather than shadowing the old transcript.
            self._activate(chat_id)
            return
        self._track(chat_id)
        self._activate(chat_id)

    @objc.python_method
    def close_chat(self):
        """Drop the active chat, here and on the backend.

        Nothing is stored by position, so the chats either side keep their ids
        and their transcripts. A reply still in flight for this one comes back
        tagged with an id `_deliver` no longer knows, and is dropped.
        """
        if self._active is None:
            return
        chat_id = self._active
        index = self._order.index(chat_id)

        del self._chats[chat_id]
        self._order.pop(index)
        self._on_delete_chat(chat_id)

        # `_activate` parks the outgoing chat's draft, and skips that here
        # because the entry it would park into is already gone.
        if self._order:
            self._activate(self._order[min(index, len(self._order) - 1)])
        else:
            # There is always a chat to type into.
            self.new_chat()

    @objc.python_method
    def _track(self, chat_id):
        self._chats[chat_id] = {
            "id": chat_id,
            "transcript": NSMutableAttributedString.alloc().init(),
            # Set while the handler owes this chat a reply.
            "busy": False,
            # Where this chat's "thinking..." placeholder sits, so it can be cut
            # back out when the real answer lands.
            "pending": None,
            "draft": "",
        }
        self._order.append(chat_id)

    @objc.python_method
    def _step(self, delta):
        if self._active is None:
            return
        index = self._order.index(self._active) + delta
        if 0 <= index < len(self._order):
            self._activate(self._order[index])

    @objc.python_method
    def _activate(self, chat_id):
        """Swap the view over to `chat_id`, parking the current chat's draft."""
        if self._active is not None and self._active in self._chats:
            self._chats[self._active]["draft"] = self._input.stringValue()

        self._active = chat_id
        entry = self._chats[chat_id]

        self._sync()
        self._counter.setStringValue_(
            "Chat %d/%d" % (self._order.index(chat_id) + 1, len(self._order))
        )
        self._input.setStringValue_(entry["draft"])
        self._input.setEnabled_(not entry["busy"])

        window = self._text.window()
        if window is not None and not entry["busy"]:
            window.makeFirstResponder_(self._input)

    # -- transcript ---------------------------------------------------------

    @objc.python_method
    def _sync(self):
        """Mirror the active chat's transcript into the text view."""
        storage = self._text.textStorage()
        storage.setAttributedString_(self._chats[self._active]["transcript"])
        self._text.scrollRangeToVisible_(NSMakeRange(storage.length(), 0))

    @objc.python_method
    def _append(self, entry, name, name_color, body):
        transcript = entry["transcript"]
        transcript.beginEditing()
        transcript.appendAttributedString_(
            NSAttributedString.alloc().initWithString_attributes_(
                name + "\n", _name_attrs(name_color)
            )
        )
        transcript.appendAttributedString_(
            NSAttributedString.alloc().initWithString_attributes_(
                body.strip() + "\n\n", _body_attrs()
            )
        )
        transcript.endEditing()
        if entry["id"] == self._active:
            self._sync()

    # -- sending / receiving ------------------------------------------------

    def send_(self, _sender):
        text = self._input.stringValue().strip()
        entry = self._chats[self._active]
        if not text or entry["busy"]:
            return

        self._input.setStringValue_("")
        entry["draft"] = ""
        self._append(entry, "You", NSColor.secondaryLabelColor(), text)

        start = entry["transcript"].length()
        self._append(entry, "Atreus", NSColor.controlAccentColor(), THINKING)
        entry["pending"] = NSMakeRange(start, entry["transcript"].length() - start)

        entry["busy"] = True
        self._input.setEnabled_(False)
        # Fire and forget: the handler answers through `poll`, not by returning.
        self._responder(text, entry["id"])

    def tick_(self, _timer):
        try:
            replies = self._poll()
        except Exception as e:
            print(f"chat poll failed: {e!r}")
            return
        for chat_id, reply in replies or ():
            self._deliver(chat_id, reply)

    @objc.python_method
    def _deliver(self, chat_id, reply):
        entry = self._chats.get(chat_id)
        if entry is None:
            # A conversation this panel never opened -- the voice agent's, say.
            return

        if entry["pending"] is not None:
            entry["transcript"].deleteCharactersInRange_(entry["pending"])
            entry["pending"] = None
        self._append(entry, "Atreus", NSColor.controlAccentColor(), str(reply))
        entry["busy"] = False

        if chat_id == self._active:
            self._input.setEnabled_(True)
            window = self._text.window()
            if window is not None:
                window.makeFirstResponder_(self._input)
