@echo off
cd /d "%~dp0"
echo START> inno_log.txt
echo Downloading Inno Setup 6.7.3 from GitHub...>> inno_log.txt
curl -L -o inno_setup.exe https://github.com/jrsoftware/issrc/releases/download/is-6_7_3/innosetup-6.7.3.exe >> inno_log.txt 2>&1
echo CURL_EXIT=%errorlevel%>> inno_log.txt
dir inno_setup.exe >> inno_log.txt 2>&1
echo Launching installer (a Windows admin prompt may appear - click Yes)...>> inno_log.txt
inno_setup.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- >> inno_log.txt 2>&1
echo LAUNCH_EXIT=%errorlevel%>> inno_log.txt
echo DONE_LAUNCH>> inno_log.txt
