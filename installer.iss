; Inno Setup script for the Typer build (app folder + PyTorch + model bundled).
; Builds Typer-Setup.exe. build.bat compiles it for you; or by hand:
;   ISCC /DSourceDir=<folder containing the "Typer" app folder> /O<output dir> installer.iss

#define MyAppName "Typer"
#define MyAppVersion "1.1"
#define MyAppPublisher "Gan Chen"
#define MyAppExeName "Typer.exe"
#ifndef SourceDir
  #define SourceDir "dist"
#endif
#ifndef OutName
  #define OutName "Typer-Setup"
#endif

[Setup]
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputBaseFilename={#OutName}
SetupIconFile=typer_icon.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

; --- Update while Typer is running -------------------------------------------------
; Typer holds this named mutex the whole time it runs (see acquire_single_instance in
; typer_app.py). Naming it here lets the installer notice a running copy, and the Windows
; Restart Manager (CloseApplications) then shuts it down automatically before any files
; are replaced — so re-running the installer over an open Typer updates it cleanly instead
; of failing on a locked Typer.exe or a torch DLL. RestartApplications=no: we don't auto-
; relaunch (the finish page's "Launch Typer now" box covers that).
AppMutex=Typer_local_single_instance_v1
CloseApplications=yes
CloseApplicationsFilter=*.exe,*.dll,*.pyd
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
; the whole onedir app folder (Typer.exe + _internal with torch + models)
Source: "{#SourceDir}\Typer\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName} now"; Flags: nowait postinstall skipifsilent
