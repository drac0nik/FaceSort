; FaceSort installer (Inno Setup 6). Built by the GitHub release workflow, or by
; installer\build_installer.bat on a PC with Inno Setup installed.
;   iscc /DAppVersion=1.38.0 /DSourceDir=..\dist\FaceSort installer\FaceSort.iss
; Installs for the current user only (no administrator rights needed, so updates install
; quietly). Your library, settings and face data live in %LOCALAPPDATA%\FaceSort and are
; never touched by installing, updating or uninstalling.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\FaceSort"
#endif

[Setup]
AppId={{6F2C9C1E-4B7A-4E55-9C1D-FA5E50A7C0DE}
AppName=FaceSort
AppVersion={#AppVersion}
AppVerName=FaceSort {#AppVersion}
AppPublisher=FaceSort
VersionInfoVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\FaceSort
DefaultGroupName=FaceSort
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=FaceSort-Setup-{#AppVersion}
SetupIconFile=..\icon.ico
UninstallDisplayIcon={app}\FaceSort.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; an update closes a running FaceSort first (it saves on close) and starts it again afterwards
CloseApplications=force
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[InstallDelete]
; files from the previous version that this one no longer has
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\FaceSort"; Filename: "{app}\FaceSort.exe"
Name: "{autodesktop}\FaceSort"; Filename: "{app}\FaceSort.exe"; Tasks: desktopicon

[Run]
; after installing - and after a quiet update from inside FaceSort - start it (again)
Filename: "{app}\FaceSort.exe"; Description: "Start FaceSort"; Flags: nowait postinstall

[UninstallDelete]
Type: filesandordirs; Name: "{app}\_internal"
