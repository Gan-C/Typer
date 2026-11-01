; Inno Setup script for Typer -> builds Output\Typer-Setup.exe (the installer wizard).
;
; 1. Build the app first:   pyinstaller typer.spec   (creates dist\Typer.exe)
; 2. Install Inno Setup:    https://jrsoftware.org/isdl.php
; 3. Compile this script:   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
;
; build_installer.bat does all of this for you.
;
; The wizard this produces: a welcome page, a "choose install location" page
; (defaults to a new Typer folder), a progress bar while it copies, and a finish
; page with "Create a desktop shortcut" and "Launch Typer now" - just like a normal app.

#define MyAppName "Typer"
#define MyAppVersion "1.0"
#define MyAppPublisher "Gan Chen"
#define MyAppExeName "Typer.exe"

[Setup]
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputBaseFilename=Typer-Setup
SetupIconFile=typer_icon.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; let the user install per-user (no admin) or for all users
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
Source: "dist\Typer.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "typer_icon.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\typer_icon.ico"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\typer_icon.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName} now"; Flags: nowait postinstall skipifsilent
