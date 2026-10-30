@echo off
REM ============================================================
REM  build.bat  -  builds the Typer app + installer (with offline AI).
REM
REM  DistilGPT-2 is baked in, so the "revise" feature works with zero
REM  setup for whoever you give it to. It uses CPU-only PyTorch (Typer
REM  runs the model on the CPU), which keeps the installer around ~0.5 GB
REM  rather than the ~2 GB a GPU/CUDA build would be, with no loss of
REM  function.
REM
REM  Needs: Python 3.10+ on PATH, Inno Setup (run install_inno.bat once),
REM  and internet ONCE for the CPU-PyTorch download (~180 MB) + the model.
REM  Outputs go to C:\TyperBuild; the installer is Output\Typer-Setup.exe.
REM ============================================================
cd /d "%~dp0"
set "BD=C:\TyperBuild"
set "VENV=%BD%\venv_cpu"
set "LOG=%~dp0build_log.txt"
if not exist "%BD%" mkdir "%BD%"
echo START %date% %time%> "%LOG%"

echo [1/6] Creating a clean CPU-only Python environment...
python -m venv "%VENV%" >> "%LOG%" 2>&1
call "%VENV%\Scripts\activate.bat"
python -m pip install --upgrade pip >> "%LOG%" 2>&1

echo [2/6] Installing CPU-only PyTorch (no GPU libraries, ~180 MB)...
pip install torch --index-url https://download.pytorch.org/whl/cpu >> "%LOG%" 2>&1
if errorlevel 1 (echo    pytorch.org failed; trying SJTU mirror...>> "%LOG%" & pip install torch --index-url https://mirror.sjtu.edu.cn/pytorch-wheels/cpu >> "%LOG%" 2>&1)
if errorlevel 1 (echo    SJTU failed; trying Tsinghua mirror...>> "%LOG%" & pip install torch --index-url https://mirrors.tuna.tsinghua.edu.cn/pytorch-wheels/cpu >> "%LOG%" 2>&1)
python -c "import torch" >> "%LOG%" 2>&1
if errorlevel 1 (
  echo.
  echo  *** Could not install CPU PyTorch from any source. ***
  echo  Your network may be blocking download.pytorch.org and the mirrors.
  echo  Try again on a connection that can reach them, or on a VPN.
  echo CPU_TORCH_FAILED>> "%LOG%"
  pause
  exit /b 1
)
echo CPU_TORCH_OK>> "%LOG%"

echo [3/6] Installing transformers + PyInstaller...
pip install transformers pyinstaller >> "%LOG%" 2>&1
if errorlevel 1 pip install transformers pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple >> "%LOG%" 2>&1

echo [4/6] Getting the model to bundle (DistilGPT-2)...
set "HF_ENDPOINT=https://hf-mirror.com"
REM  clean DistilGPT-2-only folder, so an older two-model folder can't leak GPT-2 in
set "TYPER_MODELS_DIR=%BD%\models_slim"
if exist "%BD%\models_slim\distilgpt2\config.json" (
  echo     reusing model already in %BD%\models_slim>> "%LOG%"
) else (
  python typer_revise_local.py all --mirror --bundle="%BD%\models_slim" >> "%LOG%" 2>&1
)
echo BUNDLE_EXIT=%errorlevel%>> "%LOG%"

echo [5/6] Building the app with CPU PyTorch bundled (the long step)...
python -m PyInstaller --noconfirm --distpath "%BD%\dist_slim" --workpath "%BD%\build_slim" typer.spec >> "%LOG%" 2>&1
echo PYINSTALLER_EXIT=%errorlevel%>> "%LOG%"

echo [6/6] Building the installer...
set "ISCC=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=C:\Program Files\Inno Setup 6\ISCC.exe"
if exist "%ISCC%" "%ISCC%" /DSourceDir="%BD%\dist_slim" /DOutName="Typer-Setup" /O"%BD%\Output" installer.iss >> "%LOG%" 2>&1
if not exist "%ISCC%" echo INNO_NOT_FOUND - run install_inno.bat first>> "%LOG%"

echo.
if exist "%BD%\dist_slim\Typer\Typer.exe" (echo RESULT_APP=yes>> "%LOG%") else (echo RESULT_APP=no>> "%LOG%")
if exist "%BD%\Output\Typer-Setup.exe" (
  echo RESULT_INSTALLER=yes>> "%LOG%"
  for %%F in ("%BD%\Output\Typer-Setup.exe") do echo INSTALLER_BYTES=%%~zF>> "%LOG%"
  echo Done.  Installer: %BD%\Output\Typer-Setup.exe
  for %%F in ("%BD%\Output\Typer-Setup.exe") do echo Size: %%~zF bytes
) else (
  echo RESULT_INSTALLER=no>> "%LOG%"
  echo Something went wrong - see %LOG%
)
echo DONE>> "%LOG%"
pause
