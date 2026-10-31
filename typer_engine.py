"""
typer_engine.py  -  the typing engine behind Typer.

Everything that decides WHAT keys to press and WHEN lives here, independent of the
operating system.  Two keyboard backends exist:

  * WindowsKeyboard  - real keystrokes through user32.SendInput (what the app uses)
  * FakeKeyboard     - writes into a VirtualDoc (what the tests use)

Hard rule: the Delete and Insert keys are never pressed.  Every path that sends a
key goes through assert_key_allowed / assert_vk_allowed, which refuse them.
Corrections only ever use Backspace plus the Left/Right arrow keys.
"""

from __future__ import annotations

import random
import re
import sys
import threading
import time

# The browser tab title of the control panel.  Any window whose title starts with
# this is "us" and is never typed into.
SELF_TITLE = "Typer (local)"

ALLOWED_KEYS = ("enter", "backspace", "left", "right", "tab")
VK_DELETE = 0x2E
VK_INSERT = 0x2D


def assert_key_allowed(name: str) -> None:
    if name not in ALLOWED_KEYS:
        raise ValueError(f"key {name!r} is not allowed (only {ALLOWED_KEYS})")


def assert_vk_allowed(vk: int) -> None:
    if vk in (VK_DELETE, VK_INSERT):
        raise ValueError("Delete and Insert are never pressed")


# --------------------------------------------------------------------------- settings

DEFAULT_SETTINGS = {
    "wpm": 60,                 # average words per minute (5 characters = 1 word)
    "error_rate": 2.0,         # percent of letters that get a typo
    "preserve": False,         # partial error preserve
    "linebreak_enabled": True, # pause at line breaks at all (off = type Enter, no extra wait)
    "linebreak": 1.0,          # seconds to pause after a line break
    "linebreak_random": False,
    "linebreak_min": 0.5,      # seconds
    "linebreak_max": 2.0,      # seconds
    "revise": False,           # revise while typing (offline local model)
    "revise_every": 5,         # about every N sentences
    "revise_random": False,    # if set, a random gap between 0 and revise_every sentences
    "model": "distilgpt2",     # local revise model (DistilGPT-2)
    "keep_on_top": True,       # keep the Typer window above other windows (UI-only)
    "rich_text": False,        # rich-text editor on/off (UI-only; formatting is passed separately)
    "lock_cursor": False,      # block the mouse while typing so accidental clicks can't disrupt it
    "match_document": False,   # "revise to match": edit what is already in the target to fit this text
}

# Formatting shortcuts emitted into the target document. These are the near-universal
# word-processor chords (Word, Google Docs, LibreOffice, Outlook, ...): Ctrl+B/I/U for
# character styles and Ctrl+L/E/R for paragraph alignment.
ALIGN_CHORD = {"left": "l", "center": "e", "right": "r"}


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def sanitize_settings(incoming: dict, base: dict | None = None) -> dict:
    """Coerce a settings dict coming from the UI (or config file) into safe values."""
    out = dict(base or DEFAULT_SETTINGS)
    for key, value in (incoming or {}).items():
        if key not in DEFAULT_SETTINGS:
            continue
        default = DEFAULT_SETTINGS[key]
        try:
            if isinstance(default, bool):
                out[key] = bool(value) if not isinstance(value, str) else value.lower() in ("1", "true", "on", "yes")
            elif isinstance(default, int):
                out[key] = int(float(value))
            elif isinstance(default, float):
                out[key] = float(value)
            else:
                out[key] = str(value)
        except (TypeError, ValueError):
            pass
    out["wpm"] = int(_clamp(out["wpm"], 5, 400))
    out["error_rate"] = float(_clamp(out["error_rate"], 0.0, 25.0))
    out["linebreak"] = float(_clamp(out["linebreak"], 0.0, 600.0))
    out["linebreak_min"] = float(_clamp(out["linebreak_min"], 0.0, 600.0))
    out["linebreak_max"] = float(_clamp(out["linebreak_max"], 0.0, 600.0))
    if out["linebreak_min"] > out["linebreak_max"]:
        out["linebreak_min"], out["linebreak_max"] = out["linebreak_max"], out["linebreak_min"]
    out["revise_every"] = int(_clamp(out["revise_every"], 1, 200))
    out["model"] = out["model"].strip() or DEFAULT_SETTINGS["model"]
    return out


# --------------------------------------------------------------------------- keyboard geometry

# Physical QWERTY rows with their horizontal stagger (in key widths).
_QWERTY_ROWS = [("qwertyuiop", 0.0), ("asdfghjkl", 0.25), ("zxcvbnm", 0.75)]


def _build_neighbors() -> dict[str, list[str]]:
    pos = {}
    for row_index, (row, offset) in enumerate(_QWERTY_ROWS):
        for col, ch in enumerate(row):
            pos[ch] = (row_index, col + offset)
    neighbors = {}
    for ch, (r, x) in pos.items():
        near = []
        for other, (r2, x2) in pos.items():
            if other != ch and abs(r - r2) <= 1 and abs(x - x2) <= 1.0:
                near.append(other)
        neighbors[ch] = sorted(near)
    return neighbors


NEIGHBORS = _build_neighbors()


def neighbor_key(ch: str, rng: random.Random) -> str | None:
    """A key physically next to `ch` on a QWERTY keyboard, keeping the letter case."""
    low = ch.lower()
    if low not in NEIGHBORS:
        return None
    pick = rng.choice(NEIGHBORS[low])
    return pick.upper() if ch.isupper() else pick


# --------------------------------------------------------------------------- text helpers

_SENTENCE_END = re.compile(r'[.!?。！？]+["\')\]]*\s+|\n+')


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """(start, end) of each sentence, end exclusive and including trailing whitespace."""
    spans = []
    start = 0
    for m in _SENTENCE_END.finditer(text):
        end = m.end()
        if text[start:end].strip():
            spans.append((start, end))
        start = end
    if start < len(text) and text[start:].strip():
        spans.append((start, len(text)))
    return spans


# --------------------------------------------------------------------------- fake backend (tests)

class VirtualDoc:
    """A minimal text editor model: a buffer and a caret."""

    def __init__(self):
        self.buf: list[str] = []
        self.caret = 0

    @property
    def text(self) -> str:
        return "".join(self.buf)

    def insert(self, ch: str) -> None:
        self.buf.insert(self.caret, ch)
        self.caret += 1

    def backspace(self) -> None:
        if self.caret > 0:
            del self.buf[self.caret - 1]
            self.caret -= 1

    def left(self) -> None:
        self.caret = max(0, self.caret - 1)

    def right(self) -> None:
        self.caret = min(len(self.buf), self.caret + 1)


class VirtualClock:
    """Deterministic time for tests. `real_scale` adds a tiny real sleep so threads interleave."""

    def __init__(self, real_scale: float = 0.0):
        self.t = 0.0
        self.real_scale = real_scale

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds
        if self.real_scale:
            time.sleep(seconds * self.real_scale)


class FakeKeyboard:
    """Applies keys to a VirtualDoc and logs every event with a timestamp."""

    def __init__(self, doc: VirtualDoc | None = None, clock: VirtualClock | None = None):
        self.doc = doc or VirtualDoc()
        self.clock = clock or VirtualClock()
        self.events: list[tuple[float, str, str]] = []

    def type_char(self, ch: str) -> None:
        if ch == "\n":
            return self.press("enter")
        if ch == "\t":
            return self.press("tab")
        self.doc.insert(ch)
        self.events.append((self.clock.now(), "char", ch))

    def press(self, name: str) -> None:
        assert_key_allowed(name)
        if name == "enter":
            self.doc.insert("\n")
        elif name == "tab":
            self.doc.insert("\t")
        elif name == "backspace":
            self.doc.backspace()
        elif name == "left":
            self.doc.left()
        elif name == "right":
            self.doc.right()
        self.events.append((self.clock.now(), "key", name))

    def chord(self, key: str) -> None:
        # a formatting shortcut (Ctrl+<key>); it changes styling, not the text buffer
        self.events.append((self.clock.now(), "chord", "ctrl+" + str(key).lower()))

    def set_mouse_lock(self, on: bool) -> None:
        self.events.append((self.clock.now(), "mouselock", bool(on)))

    def read_document(self) -> str:
        # models Ctrl+A + Ctrl+C: the whole document is what gets "copied"
        self.events.append((self.clock.now(), "readdoc", ""))
        return self.doc.text

    def caret_to_start(self) -> None:
        self.doc.caret = 0
        self.events.append((self.clock.now(), "caret", "start"))

    def release_all(self) -> None:
        pass


class NullFocus:
    """Focus tracker used when there is no window system (tests, non-Windows)."""

    hotkeys_available = False

    def __init__(self):
        self.ok = True
        self.reason = "Paused: focus left the target window."

    def foreground(self):
        return None

    def target(self, exclude=None):
        return ("doc", "document")

    def current(self, exclude=None):
        return ("doc", "document")

    def activate(self, target) -> bool:
        return True

    def check(self, target, exclude=None):
        return (True, "") if self.ok else (False, self.reason)

    def target_title(self) -> str:
        return "document"

    def set_active(self, on: bool) -> None:
        pass

    def set_hotkey_handler(self, fn) -> None:
        pass


# --------------------------------------------------------------------------- engine

ACTIVE_STATES = {"countdown", "typing", "paused", "revising", "fixing"}
_SENTENCE_PUNCT = ".!?。！？"
_CLAUSE_PUNCT = ",;:，；："


class Stopped(Exception):
    pass


class Aborted(Exception):
    pass


class TyperEngine:
    # error preservation ("partial error preserve")
    PRESERVE_CHANCE = 0.35       # share of errors that are left for later
    PRESERVE_MAX = 4             # never more than this many per run (keeps it small)
    PRESERVE_WINDOW = 600        # only errors inside the last N characters are kept
    # AI revision
    PREFETCH_CHARS = 150         # ask the model this many characters ahead
    REVISION_WAIT = 6.0          # longest "thinking pause" while waiting for the model
    # start / resume
    COUNTDOWN = 3.0
    RESUME_GRACE = 2.0

    def __init__(self, keyboard, focus=None, revise_fn=None,
                 sleep_fn=time.sleep, now_fn=time.perf_counter, rng: random.Random | None = None):
        self.kb = keyboard
        self.focus = focus or NullFocus()
        self.revise_fn = revise_fn
        self.sleep = sleep_fn
        self.now = now_fn
        self.rng = rng or random.Random()
        self.settings = dict(DEFAULT_SETTINGS)

        self.state = "idle"
        self.message = ""
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._running = threading.Event()   # cleared while paused
        self._running.set()
        self._target = None
        self.wpm_trace: list[float] = []
        self.linebreak_log: list[float] = []
        self.revision_requests: list[dict] = []
        self.focus.set_hotkey_handler(self._hotkey)
        self._reset_run()

    # ----------------------------------------------------------------- public API

    def update_settings(self, incoming: dict) -> dict:
        self.settings = sanitize_settings(incoming, self.settings)
        return self.settings

    def start(self, text: str, settings: dict | None = None, fmt: dict | None = None):
        if self.state in ACTIVE_STATES:
            return False, "Already running."
        text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
        if not text.strip():
            return False, "Paste some text first."
        if settings:
            self.update_settings(settings)
        if self.settings["revise"] and not self.revise_fn:
            return False, "Revise while typing is not available here. Untick it to type."
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._reset_run()
        # The Start click came from the control panel, so whatever window is in
        # front right now is the panel's own window: never type into it.
        self._self_hwnd = self.focus.foreground()
        self.text = text
        self.total = len(text)
        self._sentences = sentence_spans(text)
        self.fmt = self._coerce_fmt(fmt, len(text))
        self._match = bool(self.settings.get("match_document"))
        self._stop.clear()
        self._running.set()
        self._set_state("countdown", "Starting...")
        self._thread = threading.Thread(target=self._run, name="typer-run", daemon=True)
        self._thread.start()
        return True, ""

    def pause(self) -> bool:
        if self.state in ("typing", "revising", "fixing"):
            self._paused_by_focus = False   # a deliberate pause; do not auto-resume
            self._running.clear()
            self._set_state("paused", "Paused.")
            return True
        return False

    def resume(self) -> bool:
        if self.state == "paused":
            self._running.set()
            return True
        return False

    def toggle_pause(self) -> None:
        if self.state == "paused":
            self.resume()
        else:
            self.pause()

    def stop(self) -> bool:
        if self.state in ACTIVE_STATES:
            self._stop.set()
            self._running.set()
            return True
        return False

    def join(self, timeout=None) -> None:
        if self._thread:
            self._thread.join(timeout)

    def status(self) -> dict:
        typing_time = self._typing_time
        avg = (self._typed_chars * 12.0 / typing_time) if typing_time > 0 and self._typed_chars >= 5 else 0.0
        return {
            "state": self.state,
            "message": self.message,
            "progress": self.progress,
            "total": self.total,
            "live_wpm": round(self.live_wpm),
            "avg_wpm": round(avg),
            "target_title": self._target[1] if self._target else self.focus.target_title(),
            "countdown": round(self.countdown_left, 1),
            "errors_made": self.errors_made,
            "errors_fixed": self.errors_fixed,
            "errors_pending": len(self.pending),
            "revisions": self.revisions,
            "revision_failures": self.revision_failures,
            "last_revision_error": self.last_revision_error,
            "match": self._match,
            "edits_inserted": self.edits_inserted,
            "edits_deleted": self.edits_deleted,
            "hotkeys": bool(getattr(self.focus, "hotkeys_available", False)),
        }

    # ----------------------------------------------------------------- run lifecycle

    def _reset_run(self) -> None:
        self.text = ""
        self.total = 0
        self.progress = 0
        self.errors_made = 0
        self.errors_fixed = 0
        self.revisions = 0
        self.revision_failures = 0
        self.last_revision_error = ""
        self.pending: list[dict] = []
        self.live_wpm = 0.0
        self.countdown_left = 0.0
        self._typing_time = 0.0
        self._ideal_time = 0.0
        self._typed_chars = 0
        self._acct_mark = None
        self._drift = 0.0
        self._prev = ""
        self._sentences = []
        self._rev_point = None
        self._rev_requested = False
        self._rev_result = None
        self._rev_error = None
        self._last_focus_check = -1.0
        self._self_hwnd = None
        self._paused_by_focus = False
        self.wpm_trace = []
        self.linebreak_log = []
        # rich-text formatting (None = plain text). self.fmt holds per-character
        # bold/italic/underline/align arrays; the _fmt_* fields track what has
        # actually been toggled on in the target so far.
        self.fmt = None
        self._fmt_bold = False
        self._fmt_italic = False
        self._fmt_underline = False
        self._align = "left"
        self.format_log: list[str] = []   # every chord emitted, for tests/inspection
        self._match = False               # "revise to match" mode (edit the existing document)
        self.edits_inserted = 0
        self.edits_deleted = 0

    # typing is "live" (keys going out) in exactly these states
    _TYPING_STATES = ("typing", "revising", "fixing")

    def _set_state(self, state: str, message: str | None = None) -> None:
        with self._lock:
            self.state = state
            if message is not None:
                self.message = message
        # the mouse lock follows the typing state: on while keys go out, off otherwise
        self._mouse_lock(state in self._TYPING_STATES)

    def _mouse_lock(self, on: bool) -> None:
        """Block (or release) physical mouse clicks on the target while typing, if the
        backend supports it and the user turned 'lock cursor' on. Releasing is always
        allowed, so the mouse can never stay stuck off a stale setting."""
        fn = getattr(self.kb, "set_mouse_lock", None)
        if fn is None:
            return
        if on and not self.settings.get("lock_cursor"):
            return
        try:
            fn(on)
        except Exception:
            pass

    def _hotkey(self, action: str) -> None:
        if action == "toggle":
            self.toggle_pause()
        elif action == "stop":
            self.stop()

    def _run(self) -> None:
        try:
            self._countdown_phase()
            if self._match:
                self._match_document()
                summary = (f"Done matching. {self.edits_inserted} characters added, "
                           f"{self.edits_deleted} removed.")
            else:
                self._type_all()
                self._fix_preserved()
                self._clear_format()
                summary = (f"Done. {self.total} characters typed, {self.errors_made} errors made, "
                           f"{self.errors_fixed} fixed, {self.revisions} revisions.")
            self._set_state("done", summary)
        except Stopped:
            self._set_state("stopped", "Stopped.")
        except Aborted as e:
            self._set_state("idle", str(e))
        except Exception as e:  # pragma: no cover - defensive
            self._set_state("error", f"Error: {e}")
        finally:
            self._mouse_lock(False)   # never leave the mouse blocked
            try:
                self.kb.release_all()
            except Exception:
                pass
            self.focus.set_active(False)

    def _countdown_phase(self) -> None:
        self.focus.set_active(True)
        previous = self.focus.target(self._self_hwnd)
        if previous:
            self.focus.activate(previous)
        self._countdown(self.COUNTDOWN, "Starting in {n}. Click into your document if it is not in front.")
        current = self.focus.current(self._self_hwnd)
        if current is None:
            raise Aborted("Typer is still the active window. Click into your document, then press Start.")
        self._target = current

    def _countdown(self, seconds: float, template: str) -> None:
        remaining = seconds
        while remaining > 0:
            if self._stop.is_set():
                raise Stopped()
            self.countdown_left = remaining
            self._set_state("countdown", template.format(n=int(remaining + 0.999)))
            step = min(0.1, remaining)
            self.sleep(step)
            remaining -= step
        self.countdown_left = 0.0

    # ----------------------------------------------------------------- waiting, pausing, accounting

    def _pause_point(self) -> None:
        if self._stop.is_set():
            raise Stopped()
        if self._running.is_set():
            return
        self._acct_flush()
        while not self._running.is_set():
            if self._stop.is_set():
                raise Stopped()
            # If the pause was caused by focus leaving (e.g. a tray popup stole it),
            # resume on our own once focus is back on the target window. A deliberate
            # Pause (or a stolen focus that has not returned) keeps waiting.
            if self._paused_by_focus:
                ok, _ = self.focus.check(self._target, self._self_hwnd)
                if ok:
                    self._paused_by_focus = False
                    self._running.set()
                    break
            self._running.wait(0.1)
        if self._stop.is_set():
            raise Stopped()
        self._regain_focus()
        self._acct_mark = self.now()

    def _regain_focus(self) -> None:
        if self._target:
            self.focus.activate(self._target)
        self._countdown(self.RESUME_GRACE, "Resuming in {n}...")
        ok, reason = self.focus.check(self._target, self._self_hwnd)
        if not ok:
            self._paused_by_focus = True   # keep watching; resume on our own when focus returns
            self._running.clear()
            self._set_state("paused", reason + " It resumes on its own when you click back in.")
            return self._pause_point()
        self._set_state("typing", "")

    def _wait(self, seconds: float) -> None:
        remaining = seconds
        while remaining > 1e-4:
            self._pause_point()
            step = min(remaining, 0.05)
            t0 = self.now()
            self.sleep(step)
            remaining -= max(self.now() - t0, step)   # never spin on clock granularity

    def _guard_focus(self) -> None:
        t = self.now()
        if t - self._last_focus_check < 0.1:
            return
        self._last_focus_check = t
        ok, reason = self.focus.check(self._target, self._self_hwnd)
        if not ok:
            self._paused_by_focus = True   # resume on our own once focus returns
            self._running.clear()
            self._set_state("paused", reason)

    def _acct_flush(self) -> None:
        if self._acct_mark is not None:
            now = self.now()
            self._typing_time += now - self._acct_mark
            self._acct_mark = now

    def _commit(self, count: int) -> None:
        """`count` characters of the original text are now in the document."""
        self.progress += count
        self._typed_chars += count
        self._ideal_time += count * 12.0 / self._wpm()
        self._acct_flush()

    # ----------------------------------------------------------------- cadence

    def _wpm(self) -> float:
        return float(self.settings["wpm"])

    @staticmethod
    def _amplitude(wpm: float) -> float:
        return min(30.0, max(0.0, wpm - 5.0))

    def _cadence_delay(self, ch: str) -> float:
        """Seconds to wait after typing `ch`, at a rate that drifts +/-30 WPM around the setting."""
        W = self._wpm()
        A = self._amplitude(W)
        # slow drift: mean-reverting random walk, bounded to +/-A
        self._drift += -0.05 * self._drift + self.rng.gauss(0.0, 0.126 * A)
        self._drift = _clamp(self._drift, -A, A)
        # controller: keeps the long-run average at W (time lost to corrections is
        # recovered by typing faster, but never beyond the +/-A band)
        debt = self._typing_time - self._ideal_time
        ctrl = _clamp(debt * W / 6.0, -A / 2.0, A)
        jitter = self.rng.gauss(0.0, A / 6.0)
        wpm = _clamp(W + self._drift + ctrl + jitter, W - A, W + A)
        wpm = max(wpm, 4.0)
        self.live_wpm = wpm if self.live_wpm == 0 else 0.7 * self.live_wpm + 0.3 * wpm
        if len(self.wpm_trace) < 20000:
            self.wpm_trace.append(wpm)
        delay = 12.0 / wpm
        if ch in _SENTENCE_PUNCT:
            delay += self.rng.uniform(0.15, 0.45) * 60.0 / W
        elif ch in _CLAUSE_PUNCT:
            delay += self.rng.uniform(0.05, 0.20) * 60.0 / W
        return delay

    def _key(self, ch: str) -> None:
        t0 = self.now()
        self.kb.type_char(ch)
        self._prev = ch
        delay = self._cadence_delay(ch)
        # The keystroke itself takes real time on hardware; count it toward the
        # interval so the effective WPM matches the setting. (In tests the virtual
        # clock does not advance during type_char, so this subtracts nothing.)
        self._wait(delay - (self.now() - t0))

    def _speed_factor(self) -> float:
        """Fast typists also notice and fix mistakes faster. 1.0 at 60 WPM."""
        return _clamp((60.0 / self._wpm()) ** 0.5, 0.5, 1.5)

    def _backspaces(self, count: int) -> None:
        f = self._speed_factor()
        for index in range(count):
            self.kb.press("backspace")
            if index < 3:
                self._wait(self.rng.uniform(0.09, 0.14) * f)
            else:
                self._wait(self.rng.uniform(0.04, 0.06) * f)

    def _move_caret(self, current: int, target: int) -> int:
        delta = target - current
        key = "left" if delta < 0 else "right"
        for index in range(abs(delta)):
            self.kb.press(key)
            self._wait(self.rng.uniform(0.18, 0.28) if index == 0 else self.rng.uniform(0.028, 0.04))
        return target

    def _weighted(self, values, weights):
        return self.rng.choices(values, weights=weights, k=1)[0]

    # ----------------------------------------------------------------- rich-text formatting

    @staticmethod
    def _coerce_fmt(fmt, n):
        """Validate a formatting map from the UI; None unless it matches the text and has formatting."""
        if not fmt or n == 0:
            return None
        try:
            if not all(k in fmt and len(fmt[k]) == n for k in ("bold", "italic", "underline", "align")):
                return None
            has = (any(fmt["bold"]) or any(fmt["italic"]) or any(fmt["underline"])
                   or any(a != "left" for a in fmt["align"]))
            return fmt if has else None
        except (TypeError, KeyError):
            return None

    def _chord(self, key: str) -> None:
        """Send a Ctrl+<key> formatting shortcut into the target, if the backend supports it."""
        fn = getattr(self.kb, "chord", None)
        if fn is None:
            return
        fn(key)
        self.format_log.append(key)
        self._wait(self.rng.uniform(0.02, 0.07))

    def _apply_format(self, i: int) -> None:
        """Before typing the original character at index i, bring the target's active
        formatting into line with what this character should have."""
        f = self.fmt
        if not f:
            return
        # paragraph alignment: set it once at the start of each line
        if i == 0 or (0 < i <= len(self.text) and self.text[i - 1] == "\n"):
            want = f["align"][i]
            if want != self._align:
                self._chord(ALIGN_CHORD.get(want, "l"))
                self._align = want
        # character styles: toggle each one that differs
        for name, key in (("bold", "b"), ("italic", "i"), ("underline", "u")):
            attr = "_fmt_" + name
            if f[name][i] != getattr(self, attr):
                self._chord(key)
                setattr(self, attr, f[name][i])

    def _clear_format(self) -> None:
        """Turn off any style still active, so text typed later by the user is not affected."""
        if not self.fmt:
            return
        for name, key in (("bold", "b"), ("italic", "i"), ("underline", "u")):
            if getattr(self, "_fmt_" + name):
                self._chord(key)
                setattr(self, "_fmt_" + name, False)

    def _key_fmt(self, ch: str, idx: int) -> None:
        """Type one original character, first matching the target's formatting to index idx."""
        self._apply_format(idx)
        self._key(ch)

    # ----------------------------------------------------------------- the typing loop

    def _type_all(self) -> None:
        text = self.text
        n = len(text)
        i = 0
        self._set_state("typing", "")
        self._acct_mark = self.now()
        self._plan_next_revision(0)
        while i < n:
            self._pause_point()
            self._guard_focus()
            if self._rev_point is not None:
                if i >= self._rev_point:
                    self._do_revision(i)
                    self._plan_next_revision(i)
                elif not self._rev_requested and i >= self._rev_point - self.PREFETCH_CHARS:
                    self._request_revision()
            if text[i] == "\n":
                i = self._type_linebreak(i)
            else:
                i = self._type_at(i)
        self._acct_flush()

    def _type_at(self, i: int) -> int:
        ch = self.text[i]
        rate = self.settings["error_rate"] / 100.0
        if rate > 0 and ch.lower() in NEIGHBORS and self.rng.random() < rate:
            return self._type_with_error(i)
        self._key_fmt(ch, i)
        self._commit(1)
        return i + 1

    def _type_linebreak(self, i: int) -> int:
        text = self.text
        n = len(text)
        while i < n and text[i] == "\n":
            self.kb.press("enter")
            self._wait(self._cadence_delay("\n"))
            self._prev = "\n"
            self._commit(1)
            i += 1
        # the line-break pause: an explicit wait, not counted as typing time
        s = self.settings
        if not s.get("linebreak_enabled", True):
            pause = 0.0
        elif s["linebreak_random"]:
            pause = self.rng.uniform(s["linebreak_min"], s["linebreak_max"])
        else:
            pause = s["linebreak"]
        self.linebreak_log.append(pause)
        if pause > 0:
            self._acct_flush()
            self._acct_mark = None
            self._wait(pause)
            self._acct_mark = self.now()
        return i

    def _type_with_error(self, i: int) -> int:
        text = self.text
        n = len(text)
        ch = text[i]
        W = self._wpm()
        order_possible = i + 1 < n and text[i + 1].lower() in NEIGHBORS and text[i + 1] != ch
        p_order = 0.35 + 0.30 * min(1.0, W / 150.0)   # fast hands swap letters more often
        if order_possible and self.rng.random() < p_order:
            kind, correct, wrong = "order", text[i:i + 2], text[i + 1] + text[i]
        else:
            wrong_char = neighbor_key(ch, self.rng)
            if wrong_char is None:
                self._key_fmt(ch, i)
                self._commit(1)
                return i + 1
            kind, correct, wrong = "word", ch, wrong_char
        self.errors_made += 1
        self._apply_format(i)   # the wrong keys land in this character's formatting

        keep = (self.settings["preserve"]
                and len(self.pending) < self.PRESERVE_MAX
                and i >= n - self.PRESERVE_WINDOW
                and self.rng.random() < self.PRESERVE_CHANCE)
        if keep:
            for w in wrong:
                self._key(w)
            self.pending.append({"pos": i, "kind": kind, "wrong": wrong, "correct": correct})
            self._commit(len(correct))
            return i + len(correct)

        # type it wrong, maybe carry on a little, notice, backspace, retype
        for w in wrong:
            self._key(w)
        j = i + len(correct)
        extra = self._weighted([0, 1, 2, 3], [0.45, 0.30, 0.15, 0.10])
        carried = ""
        while extra > 0 and j < n and text[j] != "\n":
            self._key_fmt(text[j], j)
            carried += text[j]
            j += 1
            extra -= 1
        self._wait(self.rng.uniform(0.15, 0.40) * self._speed_factor())   # noticing it
        self._backspaces(len(wrong) + len(carried))
        self._wait(self.rng.uniform(0.05, 0.12) * self._speed_factor())   # settle before retyping
        for off, c in enumerate(correct + carried):
            self._key_fmt(c, i + off)
        self.errors_fixed += 1
        self._commit(len(correct) + len(carried))
        return j

    # ----------------------------------------------------------------- partial error preserve

    def _fix_preserved(self) -> None:
        if not self.pending:
            return
        self._set_state("fixing", "Going back to fix the leftover errors.")
        self._acct_flush()
        self._acct_mark = None
        n = len(self.text)
        caret = n
        order = list(self.pending)
        self.rng.shuffle(order)
        self._wait(self.rng.uniform(0.4, 0.9))
        for err in order:
            self._pause_point()
            self._guard_focus()
            caret = self._move_caret(caret, err["pos"] + len(err["wrong"]))
            self._wait(self.rng.uniform(0.1, 0.25))
            self._backspaces(len(err["wrong"]))
            self._wait(self.rng.uniform(0.05, 0.12))   # settle before retyping
            for c in err["correct"]:
                self._key(c)
            caret = err["pos"] + len(err["correct"])
            self.pending.remove(err)
            self.errors_fixed += 1
        self._move_caret(caret, n)

    # ----------------------------------------------------------------- revise while typing

    def _plan_next_revision(self, i: int) -> None:
        self._rev_point = None
        self._rev_requested = False
        self._rev_result = None
        self._rev_error = None
        if not (self.settings["revise"] and self.revise_fn):
            return
        every = max(1, int(self.settings["revise_every"]))
        if self.settings.get("revise_random"):
            # a random gap between 0 and the number the user set (0 collapses to the next sentence)
            k = max(1, self.rng.randint(0, every))
        else:
            k = self.rng.randint(max(1, every - 1), every + 1)
        spans = [s for s in self._sentences if s[0] >= i]
        for start, end in spans[k - 1:]:
            core_end = start + len(self.text[start:end].rstrip())
            spaces = [p for p in range(start, core_end) if self.text[p] == " "]
            if len(spaces) >= 4:
                self._rev_point = self.rng.choice(spaces[2:])
                return

    def _request_revision(self) -> None:
        self._rev_requested = True
        point = self._rev_point
        draft = self.text[:point]
        upcoming = self.text[point:point + 300].strip()
        m = list(re.finditer(r"[.!?。！？]", upcoming))
        if m and len(upcoming) >= 300:
            upcoming = upcoming[:m[-1].end()]
        n_sentences = self.rng.choice([1, 1, 2])
        settings = dict(self.settings)
        self.revision_requests.append({"point": point, "draft": draft, "upcoming": upcoming, "sentences": n_sentences})

        def worker():
            try:
                self._rev_result = self.revise_fn(draft, upcoming, n_sentences, settings)
            except Exception as e:  # any failure just skips the revision
                self._rev_error = str(e) or e.__class__.__name__

        threading.Thread(target=worker, name="typer-revise", daemon=True).start()

    def _pick_portion(self, answer: str) -> str:
        answer = " ".join(answer.split())
        if len(answer) < 12:
            return ""
        m = re.search(r"[.!?。！？]", answer)
        first = answer[:m.end()] if m else answer
        k = int(len(first) * self.rng.uniform(0.4, 1.0))
        k = max(12, min(k, 200, len(answer)))
        cut = answer.rfind(" ", 0, k)
        if cut >= 12:
            k = cut
        return answer[:k]

    def _do_revision(self, i: int) -> None:
        if not self._rev_requested:
            return
        # wait for the model (a thinking pause), but never stall for long
        self._acct_flush()
        self._acct_mark = None
        waited = 0.0
        while self._rev_result is None and self._rev_error is None and waited < self.REVISION_WAIT:
            self._wait(0.05)
            waited += 0.05
        if self._rev_result is None:
            self.revision_failures += 1
            self.last_revision_error = self._rev_error or "model did not answer in time"
            self._set_state("typing", f"Revision skipped: {self.last_revision_error}")
            self._acct_mark = self.now()
            return
        portion = self._pick_portion(self._rev_result)
        if not portion:
            self._acct_mark = self.now()
            return
        self._set_state("revising", "Trying a different wording...")
        lead = "" if (i == 0 or self.text[i - 1] in " \n\t") else " "
        for c in lead + portion:
            self._pause_point()
            self._key(c)
        self._wait(self.rng.uniform(0.7, 1.6))
        self._backspaces(len(lead) + len(portion))
        self._wait(self.rng.uniform(0.3, 0.8))
        self.revisions += 1
        self._set_state("typing", "")
        self._acct_mark = self.now()

    # ----------------------------------------------------------------- revise to match

    def _nav_right(self, n: int) -> None:
        """Move the caret right n characters, quickly, with the usual pause/focus checks."""
        for k in range(n):
            if (k & 31) == 0:
                self._pause_point()
                self._guard_focus()
            self.kb.press("right")
            self._wait(self.rng.uniform(0.004, 0.012))

    def _type_run(self, s: str) -> None:
        for ch in s:
            self._key(ch)

    def _match_document(self) -> None:
        """Read what is already in the target, then edit it in place - top to bottom -
        so it ends up matching self.text. Missing parts are typed in; extra parts are
        removed with Backspace. Caret moves only with the Left/Right arrows; the Delete
        and Insert keys are never used."""
        import difflib
        self._set_state("typing", "Reading the document...")
        self._acct_mark = self.now()
        read = getattr(self.kb, "read_document", None)
        existing = ""
        if read is not None:
            try:
                existing = read() or ""
            except Exception:
                existing = ""
        existing = existing.replace("\r\n", "\n").replace("\r", "\n")
        desired = self.text
        self.total = len(desired)
        # put the caret at the very start - the "starting point of the inspection"
        goto_start = getattr(self.kb, "caret_to_start", None)
        if goto_start is not None:
            try:
                goto_start()
            except Exception:
                pass
        self._wait(self.rng.uniform(0.2, 0.45))
        if existing == desired:
            self.progress = self.total
            return
        self._set_state("typing", "")
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, existing, desired, autojunk=False).get_opcodes():
            self._pause_point()
            self._guard_focus()
            if tag == "equal":
                self._nav_right(i2 - i1)                 # leave the matching text alone
                self.progress = min(self.total, self.progress + (j2 - j1))
            elif tag == "insert":
                self._type_run(desired[j1:j2])           # type the missing text
                self.edits_inserted += (j2 - j1)
                self.progress = min(self.total, self.progress + (j2 - j1))
            elif tag == "delete":
                self._nav_right(i2 - i1)                 # step past the extra text, then
                self._backspaces(i2 - i1)                # remove it from the right edge
                self.edits_deleted += (i2 - i1)
            else:  # replace
                self._nav_right(i2 - i1)
                self._backspaces(i2 - i1)
                self.edits_deleted += (i2 - i1)
                self._type_run(desired[j1:j2])
                self.edits_inserted += (j2 - j1)
                self.progress = min(self.total, self.progress + (j2 - j1))
        self._acct_flush()


# --------------------------------------------------------------------------- Windows backend

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    ULONG_PTR = ctypes.c_size_t

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]

    user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
    user32.SendInput.restype = wintypes.UINT
    user32.VkKeyScanW.argtypes = (wintypes.WCHAR,)
    user32.VkKeyScanW.restype = ctypes.c_short
    user32.MapVirtualKeyW.argtypes = (wintypes.UINT, wintypes.UINT)
    user32.MapVirtualKeyW.restype = wintypes.UINT
    user32.GetKeyState.argtypes = (ctypes.c_int,)
    user32.GetKeyState.restype = ctypes.c_short
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
    user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user32.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
    user32.BringWindowToTop.argtypes = (wintypes.HWND,)
    user32.IsWindow.argtypes = (wintypes.HWND,)
    user32.IsIconic.argtypes = (wintypes.HWND,)
    user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
    user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.AttachThreadInput.argtypes = (wintypes.DWORD, wintypes.DWORD, wintypes.BOOL)
    user32.SwitchToThisWindow.argtypes = (wintypes.HWND, wintypes.BOOL)
    user32.RegisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT)
    user32.UnregisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int)
    user32.PeekMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT)
    kernel32.GetConsoleWindow.restype = wintypes.HWND
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD

    # --- mouse lock (low-level mouse hook), clipboard read, and a message pump ---
    LRESULT = ctypes.c_ssize_t
    HOOKPROC = ctypes.CFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
    user32.SetWindowsHookExW.argtypes = (ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD)
    user32.SetWindowsHookExW.restype = ctypes.c_void_p
    user32.CallNextHookEx.argtypes = (ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
    user32.CallNextHookEx.restype = LRESULT
    user32.UnhookWindowsHookEx.argtypes = (ctypes.c_void_p,)
    user32.UnhookWindowsHookEx.restype = wintypes.BOOL
    user32.GetMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
    user32.GetMessageW.restype = ctypes.c_int
    user32.TranslateMessage.argtypes = (ctypes.POINTER(wintypes.MSG),)
    user32.DispatchMessageW.argtypes = (ctypes.POINTER(wintypes.MSG),)
    user32.PostThreadMessageW.argtypes = (wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    user32.OpenClipboard.argtypes = (wintypes.HWND,)
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.GetClipboardData.argtypes = (wintypes.UINT,)
    user32.GetClipboardData.restype = wintypes.HANDLE
    user32.CloseClipboard.restype = wintypes.BOOL
    kernel32.GlobalLock.argtypes = (wintypes.HGLOBAL,)
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalUnlock.argtypes = (wintypes.HGLOBAL,)

    INPUT_KEYBOARD = 1
    KEYEVENTF_EXTENDEDKEY = 0x0001
    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_UNICODE = 0x0004
    VK_BACK, VK_TAB, VK_RETURN = 0x08, 0x09, 0x0D
    VK_CONTROL = 0x11
    VK_CAPITAL = 0x14
    VK_LEFT, VK_RIGHT = 0x25, 0x27
    VK_LSHIFT, VK_RSHIFT, VK_LCONTROL, VK_RCONTROL, VK_LMENU, VK_RMENU = 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5
    VK_F8, VK_F9 = 0x77, 0x78
    VK_HOME = 0x24
    WM_HOTKEY = 0x0312
    WM_QUIT = 0x0012
    WH_MOUSE_LL = 14
    HC_ACTION = 0
    CF_UNICODETEXT = 13
    SW_RESTORE = 9
    _SPECIALS = {"enter": (VK_RETURN, False), "backspace": (VK_BACK, False), "tab": (VK_TAB, False),
                 "left": (VK_LEFT, True), "right": (VK_RIGHT, True)}
    _VK_PATH_PUNCT = set(".,;:'\"!?-()")
    # mouse messages to swallow while the lock is on: button down/up, double-clicks and
    # wheels. Plain movement (0x0200) is left alone, so the pointer still moves.
    _BLOCK_MOUSE = frozenset((0x0201, 0x0202, 0x0203, 0x0204, 0x0205, 0x0206,
                              0x0207, 0x0208, 0x0209, 0x020A, 0x020B, 0x020C, 0x020D, 0x020E))

    def _read_clipboard_text() -> str:
        """The target's text after Ctrl+A/Ctrl+C. '' if the clipboard has no text."""
        if not user32.OpenClipboard(None):
            return ""
        try:
            h = user32.GetClipboardData(CF_UNICODETEXT)
            if not h:
                return ""
            p = kernel32.GlobalLock(h)
            if not p:
                return ""
            try:
                return ctypes.wstring_at(p)
            finally:
                kernel32.GlobalUnlock(h)
        finally:
            user32.CloseClipboard()

    class _MouseLock:
        """Swallows physical mouse clicks (and the wheel) while active, via a low-level
        mouse hook on its own message-pump thread. The keyboard is untouched, so F8/F9
        and Ctrl+Alt+Del keep working, and engine keystrokes (SendInput) are unaffected.
        Low-level hooks fail OPEN: if anything goes wrong the worst case is that clicks
        are not blocked, never that the mouse stays stuck."""

        def __init__(self):
            self._on = False
            self._thread = None
            self._tid = 0
            self._proc = None      # keep the CFUNCTYPE alive while hooked
            self._lock = threading.Lock()

        def set(self, on: bool) -> None:
            with self._lock:
                if on and not self._on:
                    self._on = True
                    self._thread = threading.Thread(target=self._run, name="typer-mouselock", daemon=True)
                    self._thread.start()
                elif not on and self._on:
                    self._on = False
                    if self._tid:
                        try:
                            user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
                        except Exception:
                            pass

        def _run(self) -> None:
            def proc(nCode, wParam, lParam):
                if nCode == HC_ACTION and self._on and int(wParam) in _BLOCK_MOUSE:
                    return 1   # swallow the click / wheel
                return user32.CallNextHookEx(None, nCode, wParam, lParam)
            self._proc = HOOKPROC(proc)
            try:
                hmod = kernel32.GetModuleHandleW(None)
                hook = user32.SetWindowsHookExW(WH_MOUSE_LL, self._proc, hmod, 0)
            except Exception:
                hook = None
            if not hook:
                self._proc = None
                return
            self._tid = kernel32.GetCurrentThreadId()
            msg = wintypes.MSG()
            try:
                while self._on and user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
            finally:
                try:
                    user32.UnhookWindowsHookEx(hook)
                except Exception:
                    pass
                self._tid = 0
                self._proc = None

    class WindowsKeyboard:
        """Real keystrokes.

        Every printable character is injected as a Unicode codepoint
        (KEYEVENTF_UNICODE).  This is deliberate: it bypasses the keyboard layout
        AND any active input method (IME), so the exact character lands in the
        document whatever language it is and whatever IME mode the machine is in.
        Feeding letters through virtual-key codes instead would let a CJK IME
        swallow spaces and compose the letters into other characters.

        Only the five editing keys (Enter, Backspace, Tab, Left, Right) use
        virtual-key codes, since they are actions, not characters.  Because no
        character ever enters IME composition, those keys are never intercepted."""

        def __init__(self, rng: random.Random | None = None):
            self.rng = rng or random.Random()
            self._mouselock = None

        @staticmethod
        def _ki(vk: int, scan: int, flags: int) -> "INPUT":
            assert_vk_allowed(vk)
            inp = INPUT()
            inp.type = INPUT_KEYBOARD
            inp.ki = KEYBDINPUT(vk, scan, flags, 0, 0)
            return inp

        def _emit(self, inputs) -> None:
            """Send a whole keystroke (its down and up events) in ONE SendInput call,
            so the pair is atomic and cannot interleave with anything else."""
            n = len(inputs)
            arr = (INPUT * n)(*inputs)
            if user32.SendInput(n, arr, ctypes.sizeof(INPUT)) != n:
                raise ctypes.WinError(ctypes.get_last_error())

        def _settle(self, lo: float, hi: float) -> None:
            # A short, mandatory gap so the target window drains this keystroke's
            # message before the next event arrives.  Without it, a fast burst of
            # characters mixed with backspaces can drop or reorder on a busy system.
            time.sleep(self.rng.uniform(lo, hi))

        def type_char(self, ch: str) -> None:
            if ch == "\n":
                return self.press("enter")
            if ch == "\t":
                return self.press("tab")
            data = ch.encode("utf-16-le")
            units = [int.from_bytes(data[k:k + 2], "little") for k in range(0, len(data), 2)]
            downs = [self._ki(0, u, KEYEVENTF_UNICODE) for u in units]
            ups = [self._ki(0, u, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP) for u in units]
            self._emit(downs + ups)               # the whole character, atomically
            self._settle(0.010, 0.017)

        def press(self, name: str) -> None:
            assert_key_allowed(name)
            vk, extended = _SPECIALS[name]
            scan = user32.MapVirtualKeyW(vk, 0)
            flags = KEYEVENTF_EXTENDEDKEY if extended else 0
            self._emit([self._ki(vk, scan, flags), self._ki(vk, scan, flags | KEYEVENTF_KEYUP)])
            # editing keys change the caret/buffer; give the control a touch longer
            self._settle(0.014, 0.022)

        def chord(self, key: str) -> None:
            """Ctrl + a letter, for rich-text shortcuts: Ctrl+B/I/U (styles) and
            Ctrl+L/E/R (alignment).  Delete/Insert can never be produced this way;
            _ki still guards against them."""
            letter = (key or "").strip().lower()
            if len(letter) != 1 or not ("a" <= letter <= "z"):
                return
            vk = ord(letter.upper())        # VK codes for A-Z equal their ASCII uppercase value
            cs = user32.MapVirtualKeyW(VK_CONTROL, 0)
            ks = user32.MapVirtualKeyW(vk, 0)
            self._emit([
                self._ki(VK_CONTROL, cs, 0),
                self._ki(vk, ks, 0),
                self._ki(vk, ks, KEYEVENTF_KEYUP),
                self._ki(VK_CONTROL, cs, KEYEVENTF_KEYUP),
            ])
            self._settle(0.018, 0.030)

        def set_mouse_lock(self, on: bool) -> None:
            """Block (or release) physical mouse clicks on the target while typing."""
            if self._mouselock is None:
                if not on:
                    return
                self._mouselock = _MouseLock()
            self._mouselock.set(bool(on))

        def read_document(self) -> str:
            """Select all (Ctrl+A), copy (Ctrl+C), and return the target's text."""
            self.chord("a")
            self._settle(0.04, 0.07)
            self.chord("c")
            self._settle(0.08, 0.14)      # let the target put the text on the clipboard
            try:
                return _read_clipboard_text()
            except Exception:
                return ""

        def caret_to_start(self) -> None:
            """Ctrl+Home: collapse any selection and go to the very start of the document."""
            cs = user32.MapVirtualKeyW(VK_CONTROL, 0)
            hs = user32.MapVirtualKeyW(VK_HOME, 0)
            self._emit([
                self._ki(VK_CONTROL, cs, 0),
                self._ki(VK_HOME, hs, KEYEVENTF_EXTENDEDKEY),
                self._ki(VK_HOME, hs, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP),
                self._ki(VK_CONTROL, cs, KEYEVENTF_KEYUP),
            ])
            self._settle(0.03, 0.05)

        def release_all(self) -> None:
            for vk in (VK_LSHIFT, VK_RSHIFT, VK_LCONTROL, VK_RCONTROL, VK_LMENU, VK_RMENU):
                try:
                    flags = KEYEVENTF_KEYUP | (KEYEVENTF_EXTENDEDKEY if vk in (VK_RCONTROL, VK_RMENU) else 0)
                    self._emit([self._ki(vk, user32.MapVirtualKeyW(vk, 0), flags)])
                except Exception:
                    pass

    class WindowsFocus:
        """Remembers the last window the user worked in (never Typer's own), brings it
        back to the front, verifies it stays in front, and serves two global hotkeys
        (F8 pause/resume, F9 stop) while a run is active."""

        _SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}

        def __init__(self):
            self.console = kernel32.GetConsoleWindow()
            self._history = []          # distinct foreground windows, most recent first
            self._lock = threading.Lock()
            self._handler = None
            self._want_hotkeys = False
            self._registered = False
            self.hotkeys_available = False
            threading.Thread(target=self._loop, name="typer-focus", daemon=True).start()

        @staticmethod
        def _title(hwnd) -> str:
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            return buf.value

        @staticmethod
        def _cls(hwnd) -> str:
            buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, buf, 256)
            return buf.value

        def _is_self(self, hwnd, title: str, cls: str) -> bool:
            if not hwnd or hwnd == self.console:
                return True
            if title.startswith(SELF_TITLE):
                return True
            if cls in self._SHELL_CLASSES:
                return True
            return False

        def foreground(self):
            return user32.GetForegroundWindow()

        def current(self, exclude=None):
            hwnd = user32.GetForegroundWindow()
            title = self._title(hwnd)
            if (exclude and hwnd == exclude) or self._is_self(hwnd, title, self._cls(hwnd)):
                return None
            return (hwnd, title)

        def target(self, exclude=None):
            with self._lock:
                for hwnd, title in self._history:
                    if exclude and hwnd == exclude:
                        continue
                    if user32.IsWindow(hwnd):
                        return (hwnd, self._title(hwnd) or title)
            return None

        def target_title(self) -> str:
            t = self.target()
            return t[1] if t else ""

        def check(self, target, exclude=None):
            if not target:
                return False, "Paused: no target window."
            hwnd = user32.GetForegroundWindow()
            title = self._title(hwnd)
            if hwnd != target[0]:
                if (exclude and hwnd == exclude) or self._is_self(hwnd, title, self._cls(hwnd)):
                    return False, "Paused: Typer's own window is in front."
                return False, f'Paused: focus moved to "{title}".'
            if title.startswith(SELF_TITLE):
                return False, "Paused: the Typer tab is in front. Switch to your document tab."
            return True, ""

        def activate(self, target) -> bool:
            hwnd = target[0]
            if not user32.IsWindow(hwnd):
                return False
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, SW_RESTORE)
            if user32.GetForegroundWindow() == hwnd:
                return True
            fg = user32.GetForegroundWindow()
            fg_thread = user32.GetWindowThreadProcessId(fg, None) if fg else 0
            me = kernel32.GetCurrentThreadId()
            attached = bool(fg_thread and fg_thread != me and user32.AttachThreadInput(me, fg_thread, True))
            try:
                user32.BringWindowToTop(hwnd)
                user32.SetForegroundWindow(hwnd)
            finally:
                if attached:
                    user32.AttachThreadInput(me, fg_thread, False)
            time.sleep(0.15)
            if user32.GetForegroundWindow() == hwnd:
                return True
            user32.SwitchToThisWindow(hwnd, True)
            time.sleep(0.15)
            return user32.GetForegroundWindow() == hwnd

        def set_hotkey_handler(self, fn) -> None:
            self._handler = fn

        def set_active(self, on: bool) -> None:
            self._want_hotkeys = on

        def _loop(self) -> None:
            msg = wintypes.MSG()
            while True:
                try:
                    if self._want_hotkeys and not self._registered:
                        ok1 = bool(user32.RegisterHotKey(None, 1, 0x4000, VK_F8))
                        ok2 = bool(user32.RegisterHotKey(None, 2, 0x4000, VK_F9))
                        self.hotkeys_available = ok1 and ok2
                        self._registered = True
                    elif not self._want_hotkeys and self._registered:
                        user32.UnregisterHotKey(None, 1)
                        user32.UnregisterHotKey(None, 2)
                        self._registered = False
                        self.hotkeys_available = False
                    while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                        if msg.message == WM_HOTKEY and self._handler:
                            self._handler("toggle" if msg.wParam == 1 else "stop")
                    cur = self.current()
                    if cur:
                        with self._lock:
                            if not self._history or self._history[0][0] != cur[0]:
                                self._history = [cur] + [h for h in self._history if h[0] != cur[0]][:7]
                except Exception:
                    pass
                time.sleep(0.05)


def make_platform_backend():
    """(keyboard, focus) for this machine.

    Windows uses the user32 backend above (tested).  macOS and Linux use the
    pynput/xdotool/NSWorkspace backends in typer_backends_posix.py."""
    if sys.platform == "win32":
        return WindowsKeyboard(), WindowsFocus()
    from typer_backends_posix import make_posix_backend
    return make_posix_backend()
