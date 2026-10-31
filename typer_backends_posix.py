"""
typer_backends_posix.py  -  macOS and Linux keyboard / focus backends.

These mirror the Windows backends in typer_engine.py but for the other platforms.
Keystrokes go through pynput (cross-platform, Unicode-aware); the global F8/F9
hotkeys use pynput's global listener; window focus uses NSWorkspace on macOS and
xdotool on Linux.  Everything degrades gracefully: if a window tool is missing,
Typer still types into whatever window is focused after the countdown, just without
the auto-pause-on-focus-loss guard.

NOTE: developed without a Mac or Linux box to test on.  The Windows backend
(typer_engine.py) is the tested one; treat these as a strong starting point.

Rules kept identical to Windows: corrections only ever use Backspace and the
Left/Right arrow keys.  Delete and Insert are never pressed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time

from typer_engine import SELF_TITLE, assert_key_allowed


# --------------------------------------------------------------------------- keyboard

class PynputKeyboard:
    """Unicode keystrokes and the five editing keys, via pynput."""

    def __init__(self, rng=None):
        import random
        from pynput.keyboard import Controller, Key
        self.rng = rng or random.Random()
        self._c = Controller()
        self._special = {"enter": Key.enter, "backspace": Key.backspace, "tab": Key.tab,
                         "left": Key.left, "right": Key.right}

    def _settle(self, lo=0.010, hi=0.018):
        time.sleep(self.rng.uniform(lo, hi))

    def type_char(self, ch: str) -> None:
        if ch == "\n":
            return self.press("enter")
        if ch == "\t":
            return self.press("tab")
        self._c.type(ch)            # Unicode-aware; bypasses layout like the Windows path
        self._settle()

    def press(self, name: str) -> None:
        assert_key_allowed(name)    # refuses anything but enter/backspace/tab/left/right
        self._c.tap(self._special[name])
        self._settle(0.014, 0.022)

    def chord(self, key: str) -> None:
        """A rich-text shortcut (bold/italic/underline/align): Cmd+<key> on macOS,
        Ctrl+<key> on Linux. Delete/Insert are never involved."""
        from pynput.keyboard import Key
        letter = str(key).strip().lower()
        if len(letter) != 1 or not ("a" <= letter <= "z"):
            return
        mod = Key.cmd if sys.platform == "darwin" else Key.ctrl
        try:
            with self._c.pressed(mod):
                self._c.tap(letter)
        except Exception:
            pass
        self._settle(0.016, 0.028)

    def set_mouse_lock(self, on: bool) -> None:
        # Blocking the physical mouse cleanly is Windows-specific; left as a no-op on
        # macOS/Linux (the feature simply has no effect there).
        return

    def read_document(self) -> str:
        """Select-all + copy, then read the clipboard (best effort; '' on failure)."""
        from pynput.keyboard import Key
        import subprocess
        mod = Key.cmd if sys.platform == "darwin" else Key.ctrl
        try:
            with self._c.pressed(mod):
                self._c.tap("a")
            self._settle(0.05, 0.08)
            with self._c.pressed(mod):
                self._c.tap("c")
            self._settle(0.1, 0.15)
        except Exception:
            return ""
        try:
            if sys.platform == "darwin":
                return subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=3).stdout
            for cmd in (["xclip", "-selection", "clipboard", "-o"], ["xsel", "--clipboard", "--output"]):
                if shutil.which(cmd[0]):
                    return subprocess.run(cmd, capture_output=True, text=True, timeout=3).stdout
        except Exception:
            pass
        return ""

    def caret_to_start(self) -> None:
        """Jump to the start of the document (Cmd+Up on macOS, Ctrl+Home elsewhere)."""
        from pynput.keyboard import Key
        try:
            if sys.platform == "darwin":
                with self._c.pressed(Key.cmd):
                    self._c.tap(Key.up)
            else:
                with self._c.pressed(Key.ctrl):
                    self._c.tap(Key.home)
        except Exception:
            pass
        self._settle(0.03, 0.05)

    def release_all(self) -> None:
        pass


# --------------------------------------------------------------------------- global hotkeys

class _Hotkeys:
    """F8 -> toggle, F9 -> stop, via pynput's global listener. Best-effort."""

    def __init__(self):
        self._listener = None
        self._handler = None
        self.available = False

    def set_handler(self, fn):
        self._handler = fn

    def start(self):
        if self._listener is not None:
            return
        try:
            from pynput import keyboard
            self._listener = keyboard.GlobalHotKeys({
                "<f8>": lambda: self._handler and self._handler("toggle"),
                "<f9>": lambda: self._handler and self._handler("stop"),
            })
            self._listener.start()
            self.available = True
        except Exception:
            self._listener = None
            self.available = False

    def stop(self):
        try:
            if self._listener is not None:
                self._listener.stop()
        except Exception:
            pass
        self._listener = None
        self.available = False


# --------------------------------------------------------------------------- focus base

class _FocusBase:
    """Shared behaviour; subclasses implement _active() and _activate()."""

    def __init__(self):
        self._history = []          # (handle, title), most recent first
        self._lock = threading.Lock()
        self._hotkeys = _Hotkeys()
        self.hotkeys_available = False
        self._degraded = False
        threading.Thread(target=self._loop, name="typer-focus", daemon=True).start()

    # -- to override
    def _active(self):
        """Return (handle, title) of the focused window/app, or None."""
        return None

    def _activate(self, handle) -> bool:
        return True

    def _is_self(self, title: str) -> bool:
        return bool(title) and title.startswith(SELF_TITLE)

    # -- interface used by the engine
    def foreground(self):
        cur = self._active()
        return cur[0] if cur else None

    def current(self, exclude=None):
        cur = self._active()
        if cur is None:
            return ("window", "the active window") if self._degraded else None
        handle, title = cur
        if (exclude is not None and handle == exclude) or self._is_self(title):
            return None
        return cur

    def target(self, exclude=None):
        with self._lock:
            for handle, title in self._history:
                if exclude is not None and handle == exclude:
                    continue
                return (handle, title)
        return ("window", "the active window") if self._degraded else None

    def target_title(self) -> str:
        t = self.target()
        return t[1] if t else ""

    def activate(self, target) -> bool:
        if not target or target[0] == "window":
            return True
        try:
            return self._activate(target[0])
        except Exception:
            return False

    def check(self, target, exclude=None):
        if self._degraded or not target or target[0] == "window":
            return True, ""
        cur = self._active()
        if cur is None:
            return True, ""
        handle, title = cur
        if handle != target[0]:
            if (exclude is not None and handle == exclude) or self._is_self(title):
                return False, "Paused: Typer's own window is in front."
            return False, f'Paused: focus moved to "{title}".'
        return True, ""

    def set_hotkey_handler(self, fn):
        self._hotkeys.set_handler(fn)

    def set_active(self, on: bool):
        if on:
            self._hotkeys.start()
        else:
            self._hotkeys.stop()
        self.hotkeys_available = self._hotkeys.available

    def _loop(self):
        while True:
            try:
                cur = self.current()
                if cur and cur[0] != "window":
                    with self._lock:
                        if not self._history or self._history[0][0] != cur[0]:
                            self._history = [cur] + [h for h in self._history if h[0] != cur[0]][:7]
            except Exception:
                pass
            time.sleep(0.1)


# --------------------------------------------------------------------------- Linux (xdotool)

class LinuxFocus(_FocusBase):
    def __init__(self):
        self._xdotool = shutil.which("xdotool")
        super().__init__()
        if not self._xdotool:
            self._degraded = True

    def _run(self, *args):
        return subprocess.run([self._xdotool, *args], capture_output=True, text=True, timeout=2).stdout.strip()

    def _active(self):
        if not self._xdotool:
            return None
        try:
            wid = self._run("getactivewindow")
            if not wid:
                return None
            name = self._run("getwindowname", wid)
            return (wid, name)
        except Exception:
            return None

    def _activate(self, wid) -> bool:
        try:
            self._run("windowactivate", "--sync", str(wid))
            time.sleep(0.15)
            return self._run("getactivewindow") == str(wid)
        except Exception:
            return False


# --------------------------------------------------------------------------- macOS (NSWorkspace)

class MacFocus(_FocusBase):
    """App-level focus on macOS: the 'window' is really the frontmost application."""

    def __init__(self):
        self._ok = False
        try:
            from AppKit import NSWorkspace  # noqa: F401
            self._ok = True
        except Exception:
            self._ok = False
        super().__init__()
        if not self._ok:
            self._degraded = True

    def _active(self):
        if not self._ok:
            return None
        try:
            from AppKit import NSWorkspace
            app = NSWorkspace.sharedWorkspace().frontmostApplication()
            if app is None:
                return None
            return (int(app.processIdentifier()), str(app.localizedName() or ""))
        except Exception:
            return None

    def _activate(self, pid) -> bool:
        try:
            from AppKit import NSRunningApplication, NSApplicationActivateIgnoringOtherApps
            app = NSRunningApplication.runningApplicationWithProcessIdentifier_(int(pid))
            if app is None:
                return False
            app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
            time.sleep(0.2)
            cur = self._active()
            return cur is not None and cur[0] == int(pid)
        except Exception:
            return False


def make_posix_backend():
    """(keyboard, focus) for macOS or Linux. Raises a helpful error if deps are missing."""
    try:
        import pynput  # noqa: F401
    except Exception:
        raise RuntimeError(
            "Typer needs the 'pynput' package on macOS/Linux for keystrokes.\n"
            "Install it with:  pip install pynput")
    keyboard = PynputKeyboard()
    if sys.platform == "darwin":
        return keyboard, MacFocus()
    return keyboard, LinuxFocus()
