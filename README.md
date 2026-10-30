# Typer

Types your text into any other window the way a person would: a drifting speed, the
odd typo that gets fixed, pauses at line breaks, and (optionally) false starts that a
small local AI model writes and then deletes again.

Typer is a **native desktop app** — a real window, no browser, no server. It runs
**fully offline** and is **open source** (MIT). The only time it ever touches the
internet is the one-time download of the optional revise model.

Corrections use **Backspace and the arrow keys only** — Delete and Insert are never
pressed.

Only **one copy runs at a time**: launching Typer again (double-clicking it a few
times, or re-opening the shortcut) just brings the window that is already open to the
front instead of starting another.

## Platforms

| OS | Typing | Window focus / auto-pause | Global F8/F9 hotkeys |
| --- | --- | --- | --- |
| **Windows** | yes (tested) | yes | yes |
| **macOS** | yes (needs Accessibility permission) | app-level | yes |
| **Linux** | yes (needs `xdotool` for best results) | yes with `xdotool` | yes (X11) |

> Windows is the tested platform. macOS and Linux use the same engine with pynput /
> NSWorkspace / xdotool backends; they should work but have not been tested on real
> hardware. On Chrome OS the app can only type into other Linux-container apps, not
> Chrome tabs or Android apps, because of Chrome OS sandboxing.

## Install & run

```bash
# 1. (macOS/Linux only) install the keystroke dependency
pip install -r requirements.txt
#    Linux also needs xdotool:   sudo apt install xdotool
#    Linux also needs tkinter:   sudo apt install python3-tk

# 2. run it
python typer_app.py
```

On **Windows** you need nothing extra — just `python typer_app.py` (or build a
one-file `Typer.exe`, see below).

To launch it **without a console window** in the background, double-click
**`Typer.vbs`** instead of `run_typer.bat`. (`run_typer.bat` still exists and keeps
its console open, which is handy for seeing errors.)

### Enabling "revise while typing" (optional, offline AI)

```bash
pip install -r requirements-revise.txt          # PyTorch + transformers (CPU, ~200 MB)
python typer_revise_local.py all                # download DistilGPT-2 once
```

After that the models sit in your local Hugging Face cache and the feature works
fully offline. You can also download from inside the app with the **Download model**
button.

> **Mainland China:** Hugging Face is slow/blocked, so point the downloader at a
> mirror before that one-time download. `run_typer.bat` already sets it; from a
> terminal do `set HF_ENDPOINT=https://hf-mirror.com` (Windows) or
> `export HF_ENDPOINT=https://hf-mirror.com` (macOS/Linux), or add `--mirror`:
> `python typer_revise_local.py all --mirror`. Once cached, no network is used.

## Using it

1. Paste the text into the box.
2. Click into the document you want to type into (Word, a browser, an editor, …).
3. Press **Start**. Typer brings that window back to the front and begins after a
   three-second countdown. If another window steals the focus it pauses, then resumes
   on its own when you click back into your document.
4. **Pause/Resume**, **Stop**, and the global **F8** (pause/resume) / **F9** (stop)
   hotkeys work while it runs.

## Settings

| Setting | What it does |
| --- | --- |
| Rich text | A tick box beside **Text to type**. Turns the box into a small rich-text editor with **Bold / Italic / Underline** and **Left / Center / Right** alignment (toolbar buttons, or Ctrl+B/I/U). The formatting is reproduced in the target as it types, by sending the standard word-processor shortcuts (Ctrl+B/I/U, Ctrl+L/E/R). Works in editors that understand those shortcuts (Word, Google Docs, LibreOffice, Outlook, …); a plain-text field (Notepad, a code editor) has no formatting, so leave it off there. Off by default. |
| Keep on top | Top-of-window tick box. Keeps the Typer window above other windows so it is never hidden behind the document it types into. On by default; remembered between runs. |
| Words per minute | Average speed. The live speed drifts up to 30 WPM either side and averages the number you set. Adjustable while typing. |
| Line breaks | Seconds to pause after Enter. Tick **Random length** for a min/max picked each time. Untick **Pause at line breaks** to turn the pause off entirely — line breaks are still typed, just with no extra wait. |
| Error rate | Share of letters that go wrong. **Word errors** hit a neighbouring QWERTY key; **order errors** swap two letters. Both are fixed with Backspace. |
| Partial error preserve | Leaves a few typos and goes back to fix them in random order at the end. |
| Revise while typing | Occasionally sends the draft so far to a local model, types part of its continuation, then deletes it. Uses **DistilGPT-2 (82M)** — the lightest open model, CPU only, no GPU, fully offline after the one-time download (bundled into the packaged app). Set how often with **every N sentences**; tick **Random** to use a random gap between 0 and N instead. |

Settings are saved in `config.json` next to the app.

## Files

```
typer_app.py            native Tkinter GUI (entry point)
typer_engine.py         the OS-independent typing engine + the Windows backend
typer_backends_posix.py macOS + Linux keyboard/focus backends
typer_revise_local.py   offline DistilGPT-2 revise model
tests/test_typer.py     simulation tests (run: python -m pytest tests -q)
requirements*.txt       dependencies (core, and optional revise)
```

## Build a Windows app / installer (optional)

> **Prebuilt installer:** the `.exe` is large (~0.5 GB, because the AI model and
> PyTorch are bundled), so it is **not** kept in the repository — download it from
> this project's **GitHub Releases**, or build it yourself with the one step below.
> The build outputs land in `C:\TyperBuild` by default and are git-ignored.

To hand Typer to people who do not have Python, build a standalone `.exe` on a
Windows machine:

```bat
install_inno.bat      :: once - fetches & installs Inno Setup silently (for the installer)
build.bat             :: builds the app + installer
```

`build.bat` produces two things in `C:\TyperBuild`:

- **`dist_slim\Typer\Typer.exe`** — a portable, windowless app folder. Copy the whole
  `Typer` folder anywhere and double-click the exe; no Python, no install.
- **`Output\Typer-Setup.exe`** — an installer wizard: choose a location, progress bar,
  and a finish page with *Create a desktop shortcut* + *Launch now*. Per-user (no
  admin), typewriter icon baked in, no console window.

**DistilGPT-2 and PyTorch are bundled**, so the AI *revise* feature works with **zero
setup** on the recipient's machine (no Python, no pip, no model download). `build.bat`
makes a clean **CPU-only** PyTorch environment and downloads CPU PyTorch (~180 MB) and
the model once, then reuses them on later builds.

> **Why CPU-only:** Typer runs the model on the CPU and never touches a GPU. The default
> `pip install torch` on Windows pulls the CUDA (GPU) build — ~2 GB of `cudnn`/`cublas`/etc.
> that would sit unused. The CPU-only wheels drop all of it with no change in behaviour,
> which is why `build.bat` installs the CPU wheels from the start.

> A `.exe` is **Windows only**. For a macOS `.app` or a Linux binary, run the same
> PyInstaller spec (`typer.spec`) on a Mac or Linux machine — PyInstaller cannot
> cross-build. The Python source itself already runs on all three.

## Tests

```bash
python -m pytest tests -q
```

The tests drive the real engine against a simulated editor and check the typed text,
every key pressed, the speed statistics, the pauses, error-correction, preserve, and
the revise orchestration.

## License

MIT. DistilGPT-2 is licensed Apache-2.0 (Hugging Face); its weights are downloaded from
Hugging Face at first use, or bundled into the packaged app — not distributed in this
source tree.
