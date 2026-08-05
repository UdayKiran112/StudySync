; StudySync one-click installer (Inno Setup)
; ------------------------------------------
; Wraps the pre-built deployment package into a single Setup.exe that
; non-technical staff can run. Everything is fully automatic:
;   * files installed to C:\ProgramData\StudySync
;   * services registered (StudySync API + Caddy), auto-start on boot
;   * firewall opened on port 80
;   * daily backup + health-watch scheduled tasks created
;   * desktop shortcut to http://localhost
;
; Build with:  ISCC.exe studysync.iss   (or deploy\build-installer.ps1)

#define AppName "StudySync"
#define AppVersion "1.0.0"
#define AppPublisher "Study Center"
#define PackageDir "..\package"

[Setup]
AppId={{7C9D1B9E-3E2B-4A5A-9C1F-B7D2E0A56C01}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={commonappdata}\StudySync
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=output
OutputBaseFilename=StudySync-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ShowLanguageDialog=no
UninstallDisplayName={#AppName}
Uninstallable=yes
; Services are stopped/removed by uninstall.ps1, so don't let Inno touch them.
CloseApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut to StudySync"; GroupDescription: "Additional shortcuts:"; Flags: checkedonce

[Files]
; Stage the package into {tmp}; install.ps1 does the real copy into {app}.
; This keeps install.ps1 the single source of truth (no self-copy, no
; double-install) and lets Inno clean the staging dir automatically.
Source: "{#PackageDir}\app\*"; DestDir: "{tmp}\package\app"; Flags: recursesubdirs createallsubdirs skipifsourcedoesntexist
Source: "{#PackageDir}\config\*"; DestDir: "{tmp}\package\config"; Flags: recursesubdirs createallsubdirs skipifsourcedoesntexist
Source: "{#PackageDir}\scripts\*"; DestDir: "{tmp}\package\scripts"; Flags: recursesubdirs createallsubdirs skipifsourcedoesntexist
Source: "{#PackageDir}\data\*"; DestDir: "{tmp}\package\data"; Flags: recursesubdirs createallsubdirs skipifsourcedoesntexist

[Icons]
Name: "{autodesktop}\{#AppName}"; Filename: "http://localhost"; Tasks: desktopicon
Name: "{autoprograms}\{#AppName}"; Filename: "http://localhost"

[Run]
; Run the (idempotent) deployment script. It registers services, firewall,
; scheduled tasks and generates the API key. Inno runs elevated already.
Filename: "{cmd}"; Parameters: "/c mkdir ""{app}\logs\installer"" 2>nul & powershell -ExecutionPolicy Bypass -File ""{tmp}\package\scripts\install.ps1"" -PackageDir ""{tmp}\package"" > ""{app}\logs\installer\inno-install.log"" 2>&1"; StatusMsg: "Installing services..."; Flags: runhidden waituntilterminated
Filename: "http://localhost"; Description: "Open StudySync now"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; Stop + remove services, tasks, firewall, then delete remaining files.
; uninstall.ps1 handles the full cleanup before Inno deletes the folder.
Filename: "{cmd}"; Parameters: "/c powershell -ExecutionPolicy Bypass -File ""{app}\scripts\uninstall.ps1"" -Yes > ""{app}\logs\installer\inno-uninstall.log"" 2>&1"; Flags: runhidden waituntilterminated

[Code]
{ Stop the running services before [Files] tries to overwrite the API DLLs.
  taskkill /F is used instead of `net stop` because a service that fails to
  stop cleanly would block the install forever. Killing the WinSW wrapper
  marks the service Stopped; install.ps1 re-installs + starts it after. }
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Result := '';
  Exec('taskkill.exe', '/F /IM studysync-api.exe /IM studysync-caddy.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec('taskkill.exe', '/F /IM caddy.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;
