; installer.iss — Inno Setup script for RadioCastOS.
; Build with: iscc installer.iss   (ISCC.exe from Inno Setup 6, on Windows or via CI)
; Produces:   Output\RadioCastOS-Setup.exe
;
; Expects the PyInstaller folder build at dist\RadioCastOS\ (see build.spec)
; to already exist before this runs — the CI workflow does both steps in order.

#define MyAppName "RadioCastOS"
#define MyAppVersion "1.12.1"
#define MyAppPublisher "RadioCastOS"
#define MyAppExeName "RadioCastOS.exe"

[Setup]
AppId={{B3E2B8B2-6B2C-4E4B-9C1A-7A1D5F4C9E20}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=Output
OutputBaseFilename=RadioCastOS-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=assets\icon.ico
; Comment ArchitecturesInstallIn64BitMode out if you need to support 32-bit
; Windows too; the bundled ffmpeg.exe determines what's actually required.
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
; Pull in the entire PyInstaller output folder (exe + ffmpeg.exe + libs).
Source: "dist\RadioCastOS\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
