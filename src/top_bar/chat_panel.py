"""The chat popover that hangs off the menu bar icon.

An editable NSTextField cannot live inside an NSMenu -- menu tracking runs its
own modal event loop and eats keystrokes -- so the chat is an NSPopover anchored
to the status item's button instead. A popover is a real window, so typing,
scrolling and first-responder handling all work normally.
"""

import threading
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
from Foundation import NSMakeRect, NSMakeRange, NSMakeSize, NSObject
from PyObjCTools import AppHelper

# NSMinYEdge -- makes the popover hang below the menu bar.
MIN_Y_EDGE = 3
# NSTextView wants a "no practical limit" height rather than a real bound.
UNBOUNDED = 1.0e7

WIDTH = 380
HEIGHT = 460
PAD = 12
INPUT_HEIGHT = 26
HEADER_HEIGHT = 22

THINKING = "thinking…"

# Seconds after a dismissal during which a status item click is ignored.
REOPEN_GUARD = 0.35


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


class ChatPanel(NSObject):
    """Owns the popover, the transcript view and the input field.

    `responder` is a blocking ``str -> str`` callable (the LLM). It is run off
    the main thread so the UI keeps drawing while the model thinks. `on_new_chat`
    is called when the user starts over, and is what drops the old history.
    """

    def initWithResponder_onNewChat_(self, responder, on_new_chat):
        self = objc.super(ChatPanel, self).init()
        if self is None:
            return None

        self._responder = responder
        self._on_new_chat = on_new_chat
        self._busy = False
        # Bumped on every new chat. A reply that was already in flight carries
        # the generation it was asked under, and is dropped if that has moved on.
        self._generation = 0
        # A transient popover dismisses itself on the same click that fires the
        # status item's action, so a click while open would close and instantly
        # reopen it. Remember when it closed and swallow that follow-up click.
        self._closed_at = 0.0
        # Where the "thinking..." placeholder starts, so it can be cut out again.
        self._pending_range = None

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
        new_chat = NSButton.buttonWithTitle_target_action_("New Chat", self, "newChat:")
        new_chat.setBezelStyle_(NSBezelStyleInline)
        new_chat.setFont_(NSFont.systemFontOfSize_(11))
        new_chat.setFocusRingType_(NSFocusRingTypeNone)
        new_chat.sizeToFit()
        size = new_chat.frame().size
        new_chat.setFrame_(
            NSMakeRect(WIDTH - PAD - size.width, header_y, size.width, HEADER_HEIGHT)
        )
        new_chat.setAutoresizingMask_(NSViewMinXMargin | NSViewMinYMargin)
        root.addSubview_(new_chat)

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

    # -- transcript ---------------------------------------------------------

    def newChat_(self, _sender):
        self.new_chat()

    @objc.python_method
    def new_chat(self):
        """Wipe the transcript and the history behind it, and start over.

        A reply that is still in flight belongs to the old conversation, so the
        generation bump makes `_deliver` throw it away when it arrives.
        """
        self._generation += 1
        self._busy = False
        self._pending_range = None

        storage = self._text.textStorage()
        storage.deleteCharactersInRange_(NSMakeRange(0, storage.length()))

        self._on_new_chat()

        self._input.setStringValue_("")
        self._input.setEnabled_(True)
        window = self._text.window()
        if window is not None:
            window.makeFirstResponder_(self._input)

    @objc.python_method
    def _append(self, name, name_color, body):
        storage = self._text.textStorage()
        storage.beginEditing()
        storage.appendAttributedString_(
            NSAttributedString.alloc().initWithString_attributes_(
                name + "\n", _name_attrs(name_color)
            )
        )
        storage.appendAttributedString_(
            NSAttributedString.alloc().initWithString_attributes_(
                body.strip() + "\n\n", _body_attrs()
            )
        )
        storage.endEditing()
        self._text.scrollRangeToVisible_(NSMakeRange(storage.length(), 0))

    # -- sending ------------------------------------------------------------

    def send_(self, _sender):
        text = self._input.stringValue().strip()
        if not text or self._busy:
            return

        self._input.setStringValue_("")
        self._append("You", NSColor.secondaryLabelColor(), text)

        start = self._text.textStorage().length()
        self._append("Atreus", NSColor.controlAccentColor(), THINKING)
        self._pending_range = NSMakeRange(start, self._text.textStorage().length() - start)

        self._busy = True
        self._input.setEnabled_(False)
        threading.Thread(
            target=self._work, args=(text, self._generation), daemon=True
        ).start()

    @objc.python_method
    def _work(self, text, generation):
        """Runs on a worker thread -- never touch AppKit from here."""
        try:
            reply = self._responder(text)
        except Exception as e:
            reply = f"[error] {e}"
        AppHelper.callAfter(self._deliver, reply, generation)

    @objc.python_method
    def _deliver(self, reply, generation):
        if generation != self._generation:
            # Answer to a conversation the user has since thrown away.
            return
        if self._pending_range is not None:
            self._text.textStorage().deleteCharactersInRange_(self._pending_range)
            self._pending_range = None
        self._append("Atreus", NSColor.controlAccentColor(), str(reply))
        self._busy = False
        # A transient popover dismisses itself on the same click that fires the
        # status item's action, so a click while open would close and instantly
        # reopen it. Remember when it closed and swallow that follow-up click.
        self._closed_at = 0.0
        self._input.setEnabled_(True)
        window = self._text.window()
        if window is not None:
            window.makeFirstResponder_(self._input)
