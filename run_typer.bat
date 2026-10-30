@echo off
cd /d "%~dp0"
rem --- Mainland-China users: Hugging Face is slow/blocked, so the optional
rem --- revise model is downloaded once through a mirror. Delete the next line
rem --- if you are elsewhere and prefer the official huggingface.co.
set HF_ENDPOINT=https://hf-mirror.com
python typer_app.py 2>typer_error.log
if %errorlevel% neq 0 (echo Typer exited with an error - see typer_error.log & pause)
