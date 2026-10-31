"""
Simulation tests for Typer.  They drive the real engine with a fake keyboard that
writes into a virtual document and a virtual clock, so every behaviour can be checked
exactly: what ended up in the document, which keys were pressed, how long each wait
took, and how fast the typing was.

    python -m pytest tests -q
"""

import random
import statistics
import sys
import threading
import time

import pytest

sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__file__)))

from typer_engine import (ACTIVE_STATES, NEIGHBORS, FakeKeyboard, NullFocus, TyperEngine,  # noqa: E402
                          VirtualClock, VirtualDoc, assert_key_allowed, assert_vk_allowed,
                          neighbor_key, sanitize_settings, sentence_spans)


SAMPLE = (
    "The quick brown fox jumps over the lazy dog. She sells sea shells by the sea shore, "
    "and the shells she sells are surely sea shells. How much wood would a woodchuck chuck "
    "if a woodchuck could chuck wood? Peter Piper picked a peck of pickled peppers.\n"
    "A second paragraph begins here. It has a few more sentences in it, so that the "
    "revision logic has somewhere to land. Typing goes on for a while longer, with "
    "commas, and clauses; and then it stops.\n\n"
    "Third paragraph after a blank line. The end is near now. Almost there, really. Done."
)


def make_engine(seed=1, real_scale=0.0, focus=None, revise_fn=None, **settings):
    clock = VirtualClock(real_scale=real_scale)
    kb = FakeKeyboard(VirtualDoc(), clock)
    eng = TyperEngine(kb, focus or NullFocus(), revise_fn=revise_fn,
                      sleep_fn=clock.sleep, now_fn=clock.now, rng=random.Random(seed))
    eng.update_settings({"error_rate": 0.0, "linebreak": 0.0, **settings})
    return eng, kb, clock


def run_to_end(eng, text, timeout=30.0):
    ok, msg = eng.start(text)
    assert ok, msg
    eng.join(timeout)
    assert not eng._thread.is_alive(), "engine thread did not finish"
    return eng.status()


def keys_used(kb):
    return [v for (_, kind, v) in kb.events if kind == "key"]


# --------------------------------------------------------------------------- keyboard geometry

def test_neighbor_map_matches_a_qwerty_keyboard():
    assert set(NEIGHBORS["h"]) == {"g", "j", "y", "u", "b", "n"}
    assert set(NEIGHBORS["a"]) == {"q", "w", "s", "z"}
    assert set(NEIGHBORS["p"]) == {"o", "l"}
    assert set(NEIGHBORS["m"]) == {"n", "j", "k"}
    rng = random.Random(3)
    for _ in range(50):
        assert neighbor_key("h", rng) in NEIGHBORS["h"]
        assert neighbor_key("H", rng).isupper()
    assert neighbor_key("3", rng) is None
    assert neighbor_key("你", rng) is None


def test_delete_and_insert_are_refused_everywhere():
    with pytest.raises(ValueError):
        assert_key_allowed("delete")
    with pytest.raises(ValueError):
        assert_key_allowed("insert")
    with pytest.raises(ValueError):
        assert_vk_allowed(0x2E)   # VK_DELETE
    with pytest.raises(ValueError):
        assert_vk_allowed(0x2D)   # VK_INSERT
    kb = FakeKeyboard()
    with pytest.raises(ValueError):
        kb.press("delete")


# --------------------------------------------------------------------------- plain typing

def test_types_text_exactly_with_enter_for_newlines():
    eng, kb, clock = make_engine()
    st = run_to_end(eng, SAMPLE)
    assert st["state"] == "done"
    assert kb.doc.text == SAMPLE
    assert kb.doc.caret == len(SAMPLE)
    keys = keys_used(kb)
    assert keys.count("enter") == SAMPLE.count("\n")
    assert "backspace" not in keys and "left" not in keys and "right" not in keys
    assert st["progress"] == st["total"] == len(SAMPLE)
    assert st["errors_made"] == 0


def test_unicode_and_tabs_type_exactly():
    text = "Grüße aus Zürich.\n你好，世界。これは テスト です。\tTabbed\tline\nEmoji 🙂 ok"
    eng, kb, clock = make_engine(error_rate=5.0)
    run_to_end(eng, text)
    assert kb.doc.text == text


def test_start_rejects_empty_text_and_double_start():
    eng, kb, clock = make_engine(real_scale=0.05)
    ok, msg = eng.start("   \n")
    assert not ok and "Paste" in msg
    ok, _ = eng.start("hello world")
    assert ok
    ok, msg = eng.start("again")
    assert not ok and "running" in msg.lower()
    eng.join(10)


def test_revise_needs_a_revise_fn():
    # with a revise_fn present, revise is allowed to start (model issues surface at run time)
    eng, kb, clock = make_engine(revise=True, revise_fn=lambda *a: "x")
    ok, _ = eng.start("hello world")
    assert ok
    eng.join(10)
    # without a revise_fn, ticking revise refuses to start
    eng2, kb2, clock2 = make_engine(revise=True)
    eng2.revise_fn = None
    ok2, msg2 = eng2.start("hello world")
    assert not ok2 and "Revise" in msg2


# --------------------------------------------------------------------------- errors

def test_errors_are_made_and_corrected_immediately():
    text = SAMPLE * 3
    eng, kb, clock = make_engine(seed=7, error_rate=6.0)
    st = run_to_end(eng, text)
    assert st["state"] == "done"
    assert kb.doc.text == text, "every error must be corrected"
    assert st["errors_made"] >= 15
    assert st["errors_fixed"] == st["errors_made"]
    assert st["errors_pending"] == 0
    keys = keys_used(kb)
    assert keys.count("backspace") >= st["errors_made"]
    assert "left" not in keys and "right" not in keys
    assert "delete" not in keys and "insert" not in keys


def test_error_kinds_are_neighbor_keys_and_swapped_letters():
    """Replay the key events into a scratch document and inspect each wrong keystroke."""
    text = SAMPLE * 3
    eng, kb, clock = make_engine(seed=11, error_rate=8.0)
    run_to_end(eng, text)
    doc = VirtualDoc()
    expected_pos = 0
    word_errors = order_errors = 0
    chars_only = [e for e in kb.events]
    i = 0
    while i < len(chars_only):
        _, kind, v = chars_only[i]
        if kind == "char":
            correct = text[doc.caret]
            if v != correct:
                # wrong char: it is either a neighbour of the intended key or the next letter (swap)
                nxt = text[doc.caret + 1] if doc.caret + 1 < len(text) else ""
                if v == nxt and i + 1 < len(chars_only) and chars_only[i + 1][2] == correct:
                    order_errors += 1
                    doc.insert(v)
                    doc.insert(correct)
                    i += 2
                    continue
                else:
                    assert v.lower() in NEIGHBORS.get(correct.lower(), []), (v, correct)
                    assert v.isupper() == correct.isupper()
                    word_errors += 1
            doc.insert(v)
        elif v == "enter":
            doc.insert("\n")
        elif v == "tab":
            doc.insert("\t")
        elif v == "backspace":
            doc.backspace()
        i += 1
    assert doc.text == text
    assert word_errors > 0 and order_errors > 0


# --------------------------------------------------------------------------- partial error preserve

def test_partial_error_preserve_fixes_later_in_random_order():
    text = SAMPLE * 2
    eng, kb, clock = make_engine(seed=5, error_rate=8.0, preserve=True)
    seen_pending = 0
    states = set()
    original_set_state = eng._set_state
    original_key = eng._key

    def spy_state(state, message=None):
        states.add(state)
        return original_set_state(state, message)

    def spy_key(ch):
        nonlocal seen_pending
        seen_pending = max(seen_pending, len(eng.pending))
        return original_key(ch)

    eng._set_state = spy_state
    eng._key = spy_key
    ok, msg = eng.start(text)
    eng.join(30)
    st = eng.status()
    assert st["state"] == "done"
    assert kb.doc.text == text
    assert kb.doc.caret == len(text)
    assert seen_pending >= 1, "some errors should have been kept for later"
    assert seen_pending <= TyperEngine.PRESERVE_MAX
    assert "fixing" in states
    keys = keys_used(kb)
    assert "left" in keys and "right" in keys, "fixing later needs arrow navigation"
    assert st["errors_pending"] == 0 and st["errors_fixed"] == st["errors_made"]


def test_preserved_errors_are_fixed_in_a_shuffled_order():
    """With several preserved errors, the fix order must not always be left-to-right."""
    orders = []
    for seed in range(1, 13):
        eng, kb, clock = make_engine(seed=seed, error_rate=10.0, preserve=True)
        eng.PRESERVE_CHANCE = 1.0
        text = SAMPLE[-500:]
        fixed_at = []
        original_move = eng._move_caret

        def spy_move(current, target, _fixed_at=fixed_at):
            if target != len(text):
                _fixed_at.append(target)
            return original_move(current, target)

        eng._move_caret = spy_move
        run_to_end(eng, text)
        assert kb.doc.text == text
        assert kb.doc.caret == len(text)
        if len(fixed_at) >= 3:
            orders.append(fixed_at)
    assert orders, "expected runs with several preserved errors"
    assert any(o != sorted(o) for o in orders), "fix order should be random, not left-to-right"
    assert any(o != sorted(o, reverse=True) for o in orders)


# --------------------------------------------------------------------------- speed

@pytest.mark.parametrize("wpm", [20, 60, 120, 200])
def test_wpm_average_matches_setting_and_fluctuates_within_30(wpm):
    text = (SAMPLE + " ") * 4   # ~2.3k characters
    eng, kb, clock = make_engine(seed=wpm, wpm=wpm)
    st = run_to_end(eng, text)
    assert st["state"] == "done"
    typed_words = len(text) / 5.0
    minutes = eng._typing_time / 60.0
    effective = typed_words / minutes
    assert abs(effective - wpm) / wpm < 0.03, f"effective {effective:.1f} vs set {wpm}"
    A = min(30, wpm - 5)
    trace = eng.wpm_trace
    assert min(trace) >= wpm - A - 1e-6 and max(trace) <= wpm + A + 1e-6
    assert statistics.pstdev(trace) > A * 0.15, "speed must actually fluctuate"
    assert max(trace) - min(trace) > A, "speed should wander across the band"


def test_wpm_can_be_changed_while_running():
    text = (SAMPLE + " ") * 3
    eng, kb, clock = make_engine(seed=2, wpm=40, real_scale=0.0005)
    ok, _ = eng.start(text)
    assert ok
    while eng.progress < 400:
        time.sleep(0.001)
        assert eng._thread.is_alive()
    first_trace = len(eng.wpm_trace)
    eng.update_settings({"wpm": 160})
    eng.join(30)
    assert kb.doc.text == text
    later = eng.wpm_trace[first_trace + 50:]
    assert statistics.mean(later) > 120


# --------------------------------------------------------------------------- line breaks

def test_linebreak_pause_fixed_seconds():
    text = "line one\nline two\n\nline four"
    eng, kb, clock = make_engine(seed=3, wpm=60, linebreak=1.5)
    run_to_end(eng, text)
    assert kb.doc.text == text
    assert eng.linebreak_log == [1.5, 1.5]          # one pause per run of line breaks
    enters = [i for i, (_, k, v) in enumerate(kb.events) if k == "key" and v == "enter"]
    # gap between the last enter of a run and the next character includes the 1.5 s pause
    gaps = []
    for idx in enters:
        nxt = kb.events[idx + 1]
        if nxt[2] != "enter":
            gaps.append(nxt[0] - kb.events[idx][0])
    assert len(gaps) == 2
    for g in gaps:
        assert 1.5 + 12 / 90 <= g <= 1.5 + 12 / 30 + 0.05


def test_linebreak_pause_random_between_min_and_max():
    text = "\n".join(f"line {i}" for i in range(12))
    eng, kb, clock = make_engine(seed=4, linebreak_random=True, linebreak_min=0.4, linebreak_max=2.5)
    run_to_end(eng, text)
    assert kb.doc.text == text
    assert len(eng.linebreak_log) == 11
    assert all(0.4 <= p <= 2.5 for p in eng.linebreak_log)
    assert max(eng.linebreak_log) - min(eng.linebreak_log) > 0.5, "should vary"


def test_linebreak_pause_can_be_turned_off():
    text = "line one\nline two\n\nline four"
    eng, kb, clock = make_engine(seed=3, wpm=60, linebreak=5.0, linebreak_enabled=False)
    run_to_end(eng, text)
    assert kb.doc.text == text                       # newlines are still typed exactly
    assert eng.linebreak_log == [0.0, 0.0]           # but there is no pause at them
    enters = [i for i, (_, k, v) in enumerate(kb.events) if k == "key" and v == "enter"]
    gaps = []
    for idx in enters:
        nxt = kb.events[idx + 1]
        if nxt[2] != "enter":
            gaps.append(nxt[0] - kb.events[idx][0])
    assert gaps and all(g < 1.0 for g in gaps), "no multi-second line-break wait when turned off"


def test_linebreak_pause_does_not_count_as_typing_time():
    text = "a" * 200 + "\n" + "b" * 200
    eng, kb, clock = make_engine(seed=1, wpm=60, linebreak=30.0)
    run_to_end(eng, text)
    effective = (len(text) / 5.0) / (eng._typing_time / 60.0)
    assert abs(effective - 60) < 3
    assert clock.now() > 30


# --------------------------------------------------------------------------- pause / resume / stop

def test_pause_resume_and_stop():
    text = (SAMPLE + " ") * 2
    eng, kb, clock = make_engine(seed=9, real_scale=0.002)
    ok, _ = eng.start(text)
    assert ok
    deadline = time.time() + 10
    while eng.progress < 60 and time.time() < deadline:
        time.sleep(0.002)
    assert eng.pause()
    time.sleep(0.05)
    frozen = eng.progress
    assert eng.state == "paused"
    time.sleep(0.1)
    assert eng.progress <= frozen + 1, "no typing while paused"
    assert not eng.pause(), "pause is a no-op while paused"
    assert eng.resume()
    while eng.progress < frozen + 100 and time.time() < deadline:
        time.sleep(0.002)
    assert eng.state in ("typing", "countdown")
    assert eng.stop()
    eng.join(5)
    assert eng.state == "stopped"
    assert not eng._thread.is_alive()
    typed = kb.doc.text
    assert 0 < len(typed) < len(text)
    # a fresh start works after a stop
    kb.doc = VirtualDoc(); kb.events.clear()
    eng.update_settings({"wpm": 200})
    ok, _ = eng.start("short text here.")
    assert ok
    eng.join(10)
    assert kb.doc.text == "short text here."
    assert eng.state == "done"


def test_toggle_pause_hotkey_behaviour():
    eng, kb, clock = make_engine(seed=9, real_scale=0.002)
    eng.start((SAMPLE + " ") * 2)
    while eng.progress < 20:
        time.sleep(0.002)
    eng._hotkey("toggle")
    time.sleep(0.03)
    assert eng.state == "paused"
    eng._hotkey("toggle")
    time.sleep(0.05)
    assert eng.state in ("countdown", "typing")
    eng._hotkey("stop")
    eng.join(5)
    assert eng.state == "stopped"


def test_stop_during_countdown_and_during_linebreak_pause():
    eng, kb, clock = make_engine(seed=1, real_scale=0.01, linebreak=50.0)
    eng.start("abc\ndef")
    time.sleep(0.005)
    assert eng.state == "countdown"
    assert eng.stop()
    eng.join(5)
    assert eng.state == "stopped" and kb.doc.text == ""
    # now stop inside the 50 s line-break pause
    eng, kb, clock = make_engine(seed=1, real_scale=0.001, linebreak=50.0)
    eng.start("abc\ndef")
    while kb.doc.text != "abc\n":
        time.sleep(0.001)
    time.sleep(0.02)
    assert eng.state == "typing"
    t0 = time.time()
    eng.stop()
    eng.join(5)
    assert time.time() - t0 < 1.0
    assert kb.doc.text == "abc\n"


# --------------------------------------------------------------------------- focus guard

def test_focus_leaving_target_pauses_and_resume_requires_focus_back():
    focus = NullFocus()
    eng, kb, clock = make_engine(seed=9, real_scale=0.002, focus=focus)
    eng.start((SAMPLE + " ") * 2)
    while eng.progress < 30:
        time.sleep(0.002)
    focus.ok = False
    deadline = time.time() + 5
    while eng.state != "paused" and time.time() < deadline:
        time.sleep(0.002)
    assert eng.state == "paused"
    assert "focus" in eng.message.lower()
    p = eng.progress
    time.sleep(0.1)
    assert eng.progress <= p + 1, "no typing while focus is gone"
    # focus comes back on its own: the engine auto-resumes WITHOUT a manual resume
    focus.ok = True
    eng.join(20)
    assert eng.state == "done"
    assert kb.doc.text == (SAMPLE + " ") * 2


def test_manual_pause_does_not_auto_resume():
    """A deliberate Pause stays paused until Resume, even though focus is fine."""
    focus = NullFocus()
    eng, kb, clock = make_engine(seed=9, real_scale=0.002, focus=focus)
    eng.start((SAMPLE + " ") * 2)
    while eng.progress < 30:
        time.sleep(0.002)
    assert eng.pause()
    time.sleep(0.2)
    p = eng.progress
    assert eng.state == "paused"
    time.sleep(0.2)
    assert eng.progress <= p + 1, "manual pause must not auto-resume"
    assert eng.resume()
    eng.join(20)
    assert eng.state == "done"
    assert kb.doc.text == (SAMPLE + " ") * 2


def test_countdown_aborts_when_typer_itself_is_in_front():
    class SelfInFront(NullFocus):
        def current(self, exclude=None):
            return None
    eng, kb, clock = make_engine(seed=1, focus=SelfInFront())
    eng.start("hello")
    eng.join(5)
    assert eng.state == "idle"
    assert "Click into your document" in eng.message
    assert kb.doc.text == ""


# --------------------------------------------------------------------------- revise while typing

def test_revision_episode_types_part_of_model_answer_then_deletes_it():
    calls = []

    def fake_model(draft, upcoming, n, settings):
        calls.append({"draft": draft, "upcoming": upcoming, "n": n, "model": settings.get("model")})
        return "Here is a different idea about the very same thing, written by the model. And a second one."

    text = (SAMPLE + " ") * 2
    eng, kb, clock = make_engine(seed=21, revise=True, revise_every=2, revise_fn=fake_model)
    st = run_to_end(eng, text)
    assert st["state"] == "done"
    assert kb.doc.text == text, "the tried wording must be deleted again"
    assert st["revisions"] >= 2
    assert st["revision_failures"] == 0
    assert len(calls) >= st["revisions"]
    for c in calls:
        assert text.startswith(c["draft"])
        # the draft stops right after a whole word (the revision point is a space)
        assert text[len(c["draft"])] == " " and c["draft"][-1] != " "
        assert c["upcoming"] and text[len(c["draft"]):].strip().startswith(c["upcoming"][:20])
        assert c["n"] in (1, 2)
        assert c["model"] == "distilgpt2"
    # replay to confirm the model text really appeared in the document at some point
    doc = VirtualDoc()
    appeared = False
    for _, kind, v in kb.events:
        if kind == "char":
            doc.insert(v)
        elif v == "enter":
            doc.insert("\n")
        elif v == "backspace":
            doc.backspace()
        if "Here is a different" in doc.text:
            appeared = True
    assert appeared
    assert keys_used(kb).count("backspace") >= 12 * st["revisions"]


def test_revise_random_interval_runs_cleanly_and_restores_text():
    def fake_model(draft, upcoming, n, settings):
        return "A different wording the model tried out here. And a second sentence."

    text = (SAMPLE + " ") * 3
    # random gap from 0..N: revisions still happen, the tried text is deleted, nothing stalls
    eng, kb, clock = make_engine(seed=7, revise=True, revise_every=2, revise_random=True, revise_fn=fake_model)
    st = run_to_end(eng, text)
    assert st["state"] == "done"
    assert kb.doc.text == text, "every tried wording must be deleted again"
    assert st["revision_failures"] == 0
    assert st["revisions"] >= 1


def test_revision_failure_is_skipped_without_stalling():
    def broken_model(draft, upcoming, n, settings):
        raise RuntimeError("model failed to load")

    text = (SAMPLE + " ") * 2
    eng, kb, clock = make_engine(seed=21, revise=True, revise_every=1, revise_fn=broken_model)
    st = run_to_end(eng, text)
    assert st["state"] == "done"
    assert kb.doc.text == text
    assert st["revisions"] == 0
    assert st["revision_failures"] >= 1
    assert "model failed to load" in st["last_revision_error"]


def test_slow_model_is_abandoned_after_the_thinking_pause():
    gate = threading.Event()

    def slow_model(draft, upcoming, n, settings):
        gate.wait(5)
        return "late answer that should be ignored by the engine."

    text = (SAMPLE + " ") * 2
    eng, kb, clock = make_engine(seed=21, revise=True, revise_every=1, revise_fn=slow_model, real_scale=0.0005)
    st = run_to_end(eng, text, timeout=60)
    gate.set()
    assert st["state"] == "done"
    assert kb.doc.text == text
    assert st["revisions"] == 0 and st["revision_failures"] >= 1


def test_sentence_spans_and_portion_picker():
    spans = sentence_spans("One. Two! Three?\nFour 。Five")
    assert [s for s in spans][:3] == [(0, 5), (5, 10), (10, 17)]
    eng, kb, clock = make_engine(seed=1)
    p = eng._pick_portion("Short sentence here. Then another sentence that is longer.")
    assert 12 <= len(p) <= 200
    assert "Short sentence here. Then another sentence that is longer.".startswith(p)
    assert eng._pick_portion("tiny") == ""


# --------------------------------------------------------------------------- settings

def test_settings_are_sanitized():
    s = sanitize_settings({"wpm": "9999", "error_rate": -3, "linebreak_min": 5, "linebreak_max": 1,
                           "revise": "true", "revise_every": 0, "model": "  distilgpt2 ", "bogus": 1})
    assert s["wpm"] == 400 and s["error_rate"] == 0.0
    assert s["linebreak_min"] == 1.0 and s["linebreak_max"] == 5.0
    assert s["revise"] is True and s["revise_every"] == 1 and s["model"] == "distilgpt2"
    assert "bogus" not in s


# --------------------------------------------------------------------------- rich text

def chords_used(kb):
    return [v for (_, kind, v) in kb.events if kind == "chord"]


def test_rich_text_emits_formatting_shortcuts_without_changing_text():
    # "cd" bold, "ef" underlined, the second line centre-aligned
    text = "ab cd ef\nxy zw"
    n = len(text)
    bold = [False] * n;      bold[3] = bold[4] = True
    underline = [False] * n; underline[6] = underline[7] = True
    align = ["left"] * n
    for p in range(9, n):
        align[p] = "center"
    fmt = {"bold": bold, "italic": [False] * n, "underline": underline, "align": align}

    eng, kb, _ = make_engine(seed=1)
    ok, msg = eng.start(text, fmt=fmt)
    assert ok, msg
    eng.join(30.0)
    assert not eng._thread.is_alive()
    # the document holds exactly the text - the chords never add characters
    assert kb.doc.text == text
    # bold on/off around "cd", underline on before "ef", then on line 2: centre + underline off
    assert chords_used(kb) == ["ctrl+b", "ctrl+b", "ctrl+u", "ctrl+e", "ctrl+u"]


def test_plain_text_emits_no_formatting_shortcuts():
    eng, kb, _ = make_engine(seed=1)
    run_to_end(eng, "ab cd ef\nxy zw")
    assert chords_used(kb) == []


# --------------------------------------------------------------------------- lock cursor

def mouse_locks(kb):
    return [on for (_, kind, on) in kb.events if kind == "mouselock"]


def test_lock_cursor_blocks_mouse_while_typing_then_releases():
    eng, kb, _ = make_engine(seed=1, lock_cursor=True)
    run_to_end(eng, "The quick brown fox jumps over the lazy dog, again and again.")
    locks = mouse_locks(kb)
    assert True in locks, "the mouse should be locked while typing"
    assert locks[-1] is False, "the mouse must always be released at the end"


def test_lock_cursor_off_by_default_never_locks_the_mouse():
    eng, kb, _ = make_engine(seed=1)
    run_to_end(eng, "The quick brown fox jumps over the lazy dog.")
    assert True not in mouse_locks(kb)


# --------------------------------------------------------------------------- revise to match

def test_match_document_edits_existing_text_to_match():
    existing = ("The quick brown fox jumps over the lazy dog.\n"
                "This whole middle line should be removed.\n"
                "A shared closing line.")
    desired = ("The quick red fox leaps over the lazy dog.\n"
               "A shared closing line.\n"
               "Plus a brand new paragraph at the end.")
    eng, kb, _ = make_engine(seed=3, match_document=True)
    kb.doc.buf = list(existing)          # the target already holds this text
    kb.doc.caret = len(existing)
    ok, msg = eng.start(desired)
    assert ok, msg
    eng.join(60.0)
    assert not eng._thread.is_alive()
    assert kb.doc.text == desired        # the document now matches exactly
    st = eng.status()
    assert st["state"] == "done" and st["match"] is True
    assert st["edits_inserted"] > 0 and st["edits_deleted"] > 0


def test_hotstring_matcher_fires_on_trigger_words():
    from typer_engine import HotStringMatcher
    m = HotStringMatcher({"qqq": "hide", "www": "pauseresume", "": "ignored"})

    def feed(s):
        out = []
        for ch in s:
            out.append(m.feed(None if ch in " \n\t" else ch))
        return [r for r in out if r]

    assert feed("qq") == []                      # not yet
    assert feed("q") == [("hide", 3)]            # the third q completes qqq
    assert feed("type www here") == [("pauseresume", 3)]
    assert feed("aqqq") == [("hide", 3)]         # fires on the tail of a run
    assert feed("q q q") == []                   # spaces break the run, so no match
    m.set_triggers({})                           # no triggers -> never fires
    assert feed("qqqwww") == []


def test_hotstring_matcher_case_digits_and_repeat():
    from typer_engine import HotStringMatcher
    m = HotStringMatcher({"QqQ": "hide", "g0": "start"})   # mixed case + a digit

    def feed(s):
        return [r for r in (m.feed(None if ch in " \n\t" else ch) for ch in s) if r]

    assert feed("QQQ") == [("hide", 3)]          # trigger stored lower, input upper -> matches
    assert feed("qqq") == [("hide", 3)]          # fires again right after (buffer cleared)
    assert feed("g0") == [("start", 2)]          # digits are part of a trigger run
    assert feed("xxg0") == [("start", 2)]        # fires on the tail


def test_match_document_into_empty_target_types_it_all():
    desired = "Nothing was here before, so all of this gets typed in fresh."
    eng, kb, _ = make_engine(seed=4, match_document=True)
    # kb.doc starts empty; read_document returns ""
    ok, msg = eng.start(desired)
    assert ok, msg
    eng.join(60.0)
    assert kb.doc.text == desired
    assert eng.status()["edits_inserted"] == len(desired)


def test_rich_text_survives_typos_and_still_formats():
    # with a high typo rate, corrections happen inside the bold run; the final
    # document is still exactly the text and bold is toggled on then off once.
    text = "aaaa bbbb cccc dddd eeee"
    n = len(text)
    bold = [False] * n
    for p in range(5, 9):      # "bbbb" bold
        bold[p] = True
    fmt = {"bold": bold, "italic": [False] * n, "underline": [False] * n, "align": ["left"] * n}
    eng, kb, _ = make_engine(seed=7, error_rate=8.0)
    ok, msg = eng.start(text, fmt=fmt)
    assert ok, msg
    eng.join(30.0)
    assert kb.doc.text == text
    # bold is turned on and off an equal number of times, ending off
    bs = [c for c in chords_used(kb) if c == "ctrl+b"]
    assert len(bs) % 2 == 0 and len(bs) >= 2

