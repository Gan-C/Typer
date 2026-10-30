"""
typer_revise_local.py  -  offline "revise while typing", on the CPU, no GPU.

Runs a small open-source language model locally to generate a short continuation of
the draft.  The typing engine types a slice of that continuation and then deletes it,
mimicking a writer who tries a wording and backs out of it.

The model is DistilGPT-2 (82M parameters) - free and open source (Apache-2.0) and the
lightest option, so it runs comfortably on the CPU with no GPU needed.

DistilGPT-2 is a plain text-continuation model, so "revise" simply lets it continue the
draft; no instruction-following is needed.  The model is downloaded once (the only
time the internet is used) into the local Hugging Face cache; after that everything
runs fully offline.

Mainland-China note: Hugging Face is slow/blocked there, so set the environment
variable HF_ENDPOINT=https://hf-mirror.com before the one-time download (run_typer.bat
does this for you).  Once cached, no network is used and the mirror no longer matters.
"""

from __future__ import annotations

import os
import sys
import threading

# In mainland China, Hugging Face is slow/blocked; a mirror makes the one-time
# download work. Honoured only if the user has not set HF_ENDPOINT themselves.
CHINA_MIRROR = "https://hf-mirror.com"

MODELS = {
    "distilgpt2": {"id": "distilgpt2", "label": "DistilGPT-2  (82M)"},
}
DEFAULT_MODEL = "distilgpt2"

_HERE = os.path.dirname(os.path.abspath(__file__))


class RevisionError(Exception):
    pass


def backend_available() -> bool:
    """True when PyTorch + transformers are importable (needed to run a model)."""
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401
        return True
    except Exception:
        return False


def _bundled_models_root() -> str:
    """Where models baked into the app live: next to the app, or inside a PyInstaller bundle."""
    base = getattr(sys, "_MEIPASS", None) or (
        os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else _HERE)
    return os.path.join(base, "models")


def bundled_model_path(model_key: str) -> str | None:
    """Path to a model shipped inside the package, or None. Checked before any download."""
    for root in {_bundled_models_root(), os.path.join(_HERE, "models")}:
        p = os.path.join(root, model_key)
        if os.path.isdir(p) and os.path.isfile(os.path.join(p, "config.json")):
            return p
    return None


def model_ready(model_key: str) -> bool:
    """True when the model can be loaded offline - bundled in the package, or already cached."""
    if not backend_available() or model_key not in MODELS:
        return False
    if bundled_model_path(model_key):
        return True
    try:
        from transformers import AutoConfig
        AutoConfig.from_pretrained(MODELS[model_key]["id"], local_files_only=True)
        return True
    except Exception:
        return False


def download_bundle(model_key: str, dest_root: str = "models", progress=print) -> str:
    """Download a model into dest_root/<key> as a self-contained copy, for bundling into the app."""
    if model_key not in MODELS:
        raise RevisionError(f"unknown model {model_key!r}")
    if not backend_available():
        raise RevisionError("PyTorch and transformers must be installed to fetch models.")
    from transformers import AutoModelForCausalLM, AutoTokenizer
    src = MODELS[model_key]["id"]
    dest = os.path.join(dest_root, model_key)
    os.makedirs(dest, exist_ok=True)
    progress(f"Downloading {src} -> {dest} ...")
    AutoTokenizer.from_pretrained(src).save_pretrained(dest)
    AutoModelForCausalLM.from_pretrained(src).save_pretrained(dest)
    progress(f"Saved {model_key} to {dest}")
    return dest


def download_model(model_key: str, progress=print) -> str:
    """Fetch a model into the local Hugging Face cache (needs the internet once)."""
    if model_key not in MODELS:
        raise RevisionError(f"unknown model {model_key!r}")
    if not backend_available():
        raise RevisionError("PyTorch and transformers must be installed first "
                            "(pip install torch transformers).")
    from transformers import AutoModelForCausalLM, AutoTokenizer
    src = MODELS[model_key]["id"]
    progress(f"Downloading {src} ...")
    AutoTokenizer.from_pretrained(src)
    AutoModelForCausalLM.from_pretrained(src)
    progress("Downloaded.")
    return src


class LocalReviser:
    """Loads a model on demand and generates draft continuations. Thread-safe."""

    def __init__(self):
        self._lock = threading.Lock()
        self._loaded_key = None
        self._tok = None
        self._model = None

    def status(self, model_key: str) -> str:
        if not backend_available():
            return "needs PyTorch (pip install torch transformers)"
        if not model_ready(model_key):
            return "not downloaded yet"
        return "ready"

    def ensure_loaded(self, model_key: str) -> None:
        with self._lock:
            if self._loaded_key == model_key and self._model is not None:
                return
            if not backend_available():
                raise RevisionError("PyTorch/transformers not installed")
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
            torch.set_num_threads(max(1, (os.cpu_count() or 2) // 2))
            bundled = bundled_model_path(model_key)
            if bundled:
                # Shipped inside the package -> load straight from it, fully offline.
                tok = AutoTokenizer.from_pretrained(bundled, local_files_only=True)
                model = AutoModelForCausalLM.from_pretrained(bundled, local_files_only=True)
            else:
                src = MODELS.get(model_key, MODELS[DEFAULT_MODEL])["id"]
                try:
                    # Already in the local cache -> load fully offline, no network.
                    tok = AutoTokenizer.from_pretrained(src, local_files_only=True)
                    model = AutoModelForCausalLM.from_pretrained(src, local_files_only=True)
                except Exception:
                    # First use -> download once (honours HF_ENDPOINT / the China mirror).
                    tok = AutoTokenizer.from_pretrained(src)
                    model = AutoModelForCausalLM.from_pretrained(src)
            model.eval()
            self._tok, self._model, self._loaded_key = tok, model, model_key

    def revise(self, draft: str, upcoming: str, n_sentences: int, settings: dict) -> str:
        """Continue the draft. Signature matches TyperEngine's revise_fn."""
        model_key = settings.get("model", DEFAULT_MODEL)
        if model_key not in MODELS:
            model_key = DEFAULT_MODEL
        self.ensure_loaded(model_key)
        import torch
        tail = draft[-400:]
        if not tail.strip():
            raise RevisionError("nothing to continue yet")
        inputs = self._tok(tail, return_tensors="pt", truncation=True, max_length=256)
        in_len = inputs["input_ids"].shape[1]
        with torch.no_grad():
            out = self._model.generate(
                **inputs,
                max_new_tokens=48,
                do_sample=True,
                temperature=0.9,
                top_k=40,
                top_p=0.95,
                repetition_penalty=1.3,
                no_repeat_ngram_size=3,
                pad_token_id=self._tok.eos_token_id,
            )
        text = self._tok.decode(out[0][in_len:], skip_special_tokens=True)
        text = " ".join(text.split())
        if not text:
            raise RevisionError("model produced no text")
        return text


def _prep_cli(argv):
    """`python typer_revise_local.py [distilgpt2|all] [--mirror] [--bundle[=DIR]]`.

    Downloads models. Add --mirror (or set HF_ENDPOINT) to use hf-mirror.com from China.
    Add --bundle to save self-contained copies into ./models (or --bundle=DIR) so they can
    be baked into a standalone build instead of living in the Hugging Face cache.
    """
    if "--mirror" in argv:
        os.environ.setdefault("HF_ENDPOINT", CHINA_MIRROR)
    bundle_dir = None
    for a in argv:
        if a == "--bundle":
            bundle_dir = "models"
        elif a.startswith("--bundle="):
            bundle_dir = a.split("=", 1)[1]
    args = [a for a in argv[1:] if not a.startswith("-")]
    which = args[0] if args else "all"
    keys = list(MODELS) if which in ("all", "both") else [which]
    if bundle_dir is not None:
        for key in keys:
            if key not in MODELS:
                print(f"unknown model: {key}; choices: {', '.join(MODELS)} or 'all'")
                return 1
        if os.environ.get("HF_ENDPOINT"):
            print("Using Hugging Face endpoint:", os.environ["HF_ENDPOINT"])
        import time
        for key in keys:
            try:
                t0 = time.time()
                download_bundle(key, bundle_dir)
                print(f"  {key}: bundled in {time.time() - t0:.1f} s")
            except RevisionError as e:
                print("ERROR:", e)
                return 1
        print(f"Done. Self-contained models saved under '{bundle_dir}' for bundling.")
        return 0
    for key in keys:
        if key not in MODELS:
            print(f"unknown model: {key}; choices: {', '.join(MODELS)} or 'all'")
            return 1
    if os.environ.get("HF_ENDPOINT"):
        print("Using Hugging Face endpoint:", os.environ["HF_ENDPOINT"])
    import time
    for key in keys:
        already = model_ready(key)
        try:
            t0 = time.time()
            download_model(key)
            dt = time.time() - t0
            if already:
                print(f"  {key}: already cached ({dt:.1f} s to verify)")
            else:
                print(f"  {key}: downloaded in {dt:.1f} s")
        except RevisionError as e:
            print("ERROR:", e)
            return 1
    print("Done. Models are cached in your local Hugging Face cache and work offline.")
    return 0


if __name__ == "__main__":
    sys.exit(_prep_cli(sys.argv))
