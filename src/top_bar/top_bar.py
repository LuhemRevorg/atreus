import threading
from collections import deque
from pathlib import Path

import objc
import rumps
from AppKit import (
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSEventMaskLeftMouseDown,
    NSEventMaskRightMouseDown,
    NSEventModifierFlagControl,
    NSEventTypeRightMouseDown,
)
from Foundation import NSObject

from .chat_panel import INITIAL_CHAT_ID, ChatPanel

ICON_PATH = str(Path(__file__).parent / ".." / ".." / "assets" / "atreus_shoot_8x_1.png")
SYSTEM_PROMPT = (Path(__file__).parent / ".." / "TEXTPROMPT.txt").read_text(encoding="utf-8")


def _default_backend():
    """A `(respond, new_chat, delete_chat, poll)` set driving llama in-process.

    The fallback for a `top_bar` built without a handler. It answers on worker
    threads and hands replies back through the same queue-shaped `poll` the real
    handler uses, so `ChatPanel` never learns which of the two it is talking to.
    """
    from llama import llama

    chats = {INITIAL_CHAT_ID: [{"role": "system", "content": SYSTEM_PROMPT}]}
    replies = deque()

    def respond(text, chat_id):
        def work():
            try:
                reply = llama(text, chats[chat_id])
            except Exception as e:
                reply = f"[error] {e}"
            replies.append((chat_id, reply))

        threading.Thread(target=work, daemon=True).start()

    def new_chat():
        chat_id = max(chats) + 1
        chats[chat_id] = [{"role": "system", "content": SYSTEM_PROMPT}]
        return chat_id

    def delete_chat(chat_id):
        chats.pop(chat_id, None)

    def poll():
        drained = []
        while replies:
            drained.append(replies.popleft())
        return drained

    return respond, new_chat, delete_chat, poll


class _StatusItemTarget(NSObject):
    """Left click opens the chat popover, right click opens the rumps menu.

    The status item's menu has to be detached for this: while a menu is set,
    AppKit handles the click itself and the button's action never fires.
    """

    def initWithStatusItem_panel_menu_(self, status_item, panel, menu):
        self = objc.super(_StatusItemTarget, self).init()
        if self is None:
            return None
        self._status_item = status_item
        self._panel = panel
        self._menu = menu
        return self

    def clicked_(self, button):
        event = NSApplication.sharedApplication().currentEvent()
        secondary = event is not None and (
            event.type() == NSEventTypeRightMouseDown
            or event.modifierFlags() & NSEventModifierFlagControl
        )
        if secondary:
            # Re-attach just long enough for one click, then detach again so the
            # next left click still reaches us.
            self._status_item.setMenu_(self._menu)
            button.performClick_(None)
            self._status_item.setMenu_(None)
        else:
            self._panel.toggle(button)


class top_bar(rumps.App):
    """Menu bar app wrapping the chat popover.

    `responder`, `on_new_chat`, `on_delete_chat` and `on_poll` are the request
    handler's side of the conversation and are documented on `ChatPanel`; pass
    all four or none. With none, the bar drives llama itself through
    `_default_backend`.
    """

    def __init__(self, name="Atreus", title=None, icon=ICON_PATH, responder=None,
                 on_new_chat=None, on_delete_chat=None, on_poll=None,
                 template=None, menu=None, quit_button="Quit"):
        super().__init__(name, title, icon, template, menu, quit_button)
        self._responder = responder
        self._on_new_chat = on_new_chat
        self._on_delete_chat = on_delete_chat
        self._on_poll = on_poll
        self._panel = None
        self._target = None

    @rumps.clicked("New Chat")
    def new_chat(self, _sender):
        if self._panel is not None:
            self._panel.new_chat()

    def _install(self):
        """Runs once the status item exists but before the event loop starts."""
        # Accessory: menu bar only, no Dock icon, no menu bar takeover.
        NSApplication.sharedApplication().setActivationPolicy_(
            NSApplicationActivationPolicyAccessory
        )

        if self._responder is None:
            responder, on_new_chat, on_delete_chat, poll = _default_backend()
        else:
            responder = self._responder
            on_new_chat = self._on_new_chat
            on_delete_chat = self._on_delete_chat or (lambda _id: None)
            poll = self._on_poll or (lambda: ())
        self._panel = ChatPanel.alloc().initWithResponder_onNewChat_onDeleteChat_onPoll_(
            responder, on_new_chat, on_delete_chat, poll
        )

        status_item = self._nsapp.nsstatusitem
        menu = self._menu._menu
        status_item.setMenu_(None)

        self._target = _StatusItemTarget.alloc().initWithStatusItem_panel_menu_(
            status_item, self._panel, menu
        )
        button = status_item.button()
        button.setTarget_(self._target)
        button.setAction_("clicked:")
        button.sendActionOn_(NSEventMaskLeftMouseDown | NSEventMaskRightMouseDown)

    def run(self, **options):
        rumps.events.before_start.register(self._install)
        try:
            super().run(**options)
        finally:
            rumps.events.before_start.unregister(self._install)
