import signal
import sys
from io import BytesIO

from PIL import Image, ImageSequence

SPRITE = "assets/atreus_idle_8x_1.png"
MARGIN = 24


def _load_frames(sprite_path):
    """Flatten an animated GIF/APNG into [(png_bytes, seconds)] plus its size.

    Frames are encoded back to PNG so Cocoa can decode them with their alpha
    channel intact -- that alpha is what makes the window see-through.
    """
    img = Image.open(sprite_path)
    canvas = Image.new("RGBA", img.size, (0, 0, 0, 0))
    frames = []

    for frame in ImageSequence.Iterator(img):
        # Frames can be partial updates, so composite each onto a running canvas.
        canvas.alpha_composite(frame.convert("RGBA"))
        buf = BytesIO()
        canvas.save(buf, format="PNG")
        frames.append((buf.getvalue(), frame.info.get("duration", 100) / 1000.0))

    return frames, img.size


def pop_up(sprite_path=SPRITE, margin=MARGIN):
    """Play the sprite in a borderless, transparent, always-on-top window.

    Blocks until the window is closed, matching the previous Tk behaviour.
    """
    from AppKit import (
        NSApplication,
        NSApplicationActivationPolicyAccessory,
        NSBackingStoreBuffered,
        NSColor,
        NSData,
        NSImage,
        NSImageScaleNone,
        NSImageView,
        NSScreen,
        NSStatusWindowLevel,
        NSTimer,
        NSWindow,
        NSWindowCollectionBehaviorCanJoinAllSpaces,
        NSWindowCollectionBehaviorFullScreenAuxiliary,
        NSWindowCollectionBehaviorStationary,
        NSWindowStyleMaskBorderless,
    )
    from Foundation import NSMakeRect

    frames, (width, height) = _load_frames(sprite_path)
    images = [
        NSImage.alloc().initWithData_(NSData.dataWithBytes_length_(png, len(png)))
        for png, _ in frames
    ]

    app = NSApplication.sharedApplication()
    # Accessory: a floating overlay, so no Dock icon and no menu bar takeover.
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    # visibleFrame excludes the menu bar and Dock; Cocoa's origin is bottom-left.
    area = NSScreen.mainScreen().visibleFrame()
    x = area.origin.x + area.size.width - width - margin
    y = area.origin.y + margin

    window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(x, y, width, height),
        NSWindowStyleMaskBorderless,
        NSBackingStoreBuffered,
        False,
    )
    window.setBackgroundColor_(NSColor.clearColor())
    window.setOpaque_(False)
    window.setHasShadow_(False)
    # Status level floats above ordinary windows, including full-screen apps.
    window.setLevel_(NSStatusWindowLevel)
    # A window belongs to the Space it was created on unless told otherwise:
    # join every Space, ride along into full-screen apps, and don't slide
    # around during Space-switch animations.
    window.setCollectionBehavior_(
        NSWindowCollectionBehaviorCanJoinAllSpaces
        | NSWindowCollectionBehaviorFullScreenAuxiliary
        | NSWindowCollectionBehaviorStationary
    )
    # Clicks fall through to whatever is behind the sprite.
    window.setIgnoresMouseEvents_(True)

    view = NSImageView.alloc().initWithFrame_(NSMakeRect(0, 0, width, height))
    view.setImageScaling_(NSImageScaleNone)
    view.setImage_(images[0])
    window.setContentView_(view)
    window.orderFrontRegardless()

    index = 0

    def advance(_timer):
        nonlocal index
        index = (index + 1) % len(images)
        view.setImage_(images[index])
        schedule(frames[index][1])

    def schedule(delay):
        # Re-armed each tick so per-frame durations are honoured.
        NSTimer.scheduledTimerWithTimeInterval_repeats_block_(delay, False, advance)

    schedule(frames[0][1])

    # NSApp.run() is Objective-C code, so a plain Python signal handler never
    # gets a chance to run and Ctrl+C is swallowed. MachSignals installs the
    # handler at the Mach level, which can actually break the Cocoa run loop.
    from PyObjCTools import AppHelper, MachSignals

    interrupted = False

    def stop(_signum):
        nonlocal interrupted
        interrupted = True
        AppHelper.stopEventLoop()

    MachSignals.signal(signal.SIGINT, stop)
    MachSignals.signal(signal.SIGTERM, stop)

    try:
        AppHelper.runConsoleEventLoop()
    finally:
        window.orderOut_(None)

    # Stopping the loop only unblocks this call; re-raise so the interrupt
    # propagates out of main()'s cycle instead of silently continuing.
    if interrupted:
        raise KeyboardInterrupt


if __name__ == "__main__":
    pop_up(sys.argv[1] if len(sys.argv) > 1 else SPRITE)
