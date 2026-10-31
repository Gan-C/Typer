@echo off
REM ============================================================
REM  rebuild.bat  -  FAST installer update after a code change.
REM
REM  Re-bundles Typer with the current source and rebuilds the
REM  installer, REUSING the CPU-PyTorch venv + the DistilGPT-2
REM  model that build.bat already created in C:\TyperBuild.
REM  Takes ~1-2 min instead of the full ~15.  Use build.bat if
REM  you have never built before (this needs that environment).
REM
REM  Result: C:\TyperBuild\Output\Typer-Setup.exe
REM ============================================================

REM --- relaunch ourselves in a CLEAN cmd (/d) so the machine's
REM     conda AutoRun hook can't break the build, even on a
REM     double-click.  (That hook once made build.bat fail with
REM     "'build.bat' is not recognized".)
if "%TYPER_CLEAN%"=="1" goto :run
set "TYPER_CLEAN=1"
cmd /d /c call "%~f0" %*
exit /b %errorlevel%

:run
setlocal
cd /d "%~dp0"
set "BD=C:\TyperBuild"
set "VENV=%BD%\venv_cpu"
set "TYPER_MODELS_DIR=%BD%\models_slim"

if not exist "%VENV%\Scripts\activate.bat" goto :noenv
if not exist "%TYPER_MODELS_DIR%\distilgpt2\config.json" goto :nomodel

call "%VENV%\Scripts\activate.bat"

echo [1/2] Re-bundling the app with the updated code (PyInstaller)...
python -m PyInstaller --noconfirm --distpath "%BD%\dist_slim" --workpath "%BD%\build_slim" typer.spec
if errorlevel 1 goto :pyfail
if not exist "%BD%\dist_slim\Typer\Typer.exe" goto :pyfail

echo [2/2] Rebuilding the installer (Inno Setup)...
set "ISCC=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=C:\Program Files\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" goto :noinno
"%ISCC%" /DSourceDir="%BD%\dist_slim" /DOutName="Typer-Setup" /O"%BD%\Output" installer.iss
if errorlevel 1 goto :innofail

echo.
if exist "%BD%\Output\Typer-Setup.exe" (
  echo Done.  Updated installer: %BD%\Output\Typer-Setup.exe
  for %%F in ("%BD%\Output\Typer-Setup.exe") do echo Size: %%~zF bytes
) else (
  echo Finished, but the installer was not found - check the output above.
)
goto :done

:noenv
echo.
echo   The CPU build environment is missing:
echo     %VENV%
echo   Run a full build once first, then use rebuild.bat:
echo     cmd /d /c "build.bat ^< nul"
goto :done

:nomodel
echo.
echo   The bundled model is missing:
echo     %TYPER_MODELS_DIR%\distilgpt2
echo   Run a full build once first, then use rebuild.bat:
echo     cmd /d /c "build.bat ^< nul"
goto :done

:pyfail
echo.
echo   PyInstaller failed - see the messages above.
goto :done

:noinno
echo.
echo   Inno Setup (ISCC.exe) not found.  Install it once:
echo     cmd /d /c "install_inno.bat"
goto :done

:innofail
echo.
echo   Inno Setup failed - see the messages above.
goto :done

:done
endlocal
echo.
pause
