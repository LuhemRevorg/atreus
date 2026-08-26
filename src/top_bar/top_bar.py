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

from .chat_panel import ChatPanel

ICON_PATH = str(Path(__file__).parent / ".." / ".." / "assets" / "atreus_shoot_8x_1.png")
SYSTEM_PROMPT = (Path(__file__).parent / ".." / "SYSTEMPROMPT.txt").read_text(encoding="utf-8")


def _default_responder():
    """A `str -> str` callable holding its own conversation history."""
    from llama import llama

    session = [{"role": "system", "content": SYSTEM_PROMPT}]

    def respond(text):
        return llama(text, session)

    return respond


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
    def __init__(self, name="Atreus", title=None, icon=ICON_PATH, responder=None,
                 template=None, menu=None, quit_button="Quit"):
        super().__init__(name, title, icon, template, menu, quit_button)
        self._responder = responder
        self._panel = None
        self._target = None

    @rumps.clicked("Clear chat")
    def clear_chat(self, _sender):
        if self._panel is not None:
            self._panel.clear()

    def _install(self):
        """Runs once the status item exists but before the event loop starts."""
        # Accessory: menu bar only, no Dock icon, no menu bar takeover.
        NSApplication.sharedApplication().setActivationPolicy_(
            NSApplicationActivationPolicyAccessory
        )

        responder = self._responder or _default_responder()
        self._panel = ChatPanel.alloc().initWithResponder_(responder)

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
