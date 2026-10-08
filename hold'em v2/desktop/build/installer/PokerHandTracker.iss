; Inno Setup 6 script for the Windows x64 installer.
;
; Compile (build_windows.ps1 does this):  ISCC /DSourceDir=<dist\PokerHandTracker> /DAppVersion=1.0.0 /DBuildNumber=42 PokerHandTracker.iss
;
; Per-user install by default (no administrator rights needed); the user can choose "all users" in the first dialog.
; The application's DATA (settings, calibration, taught cards, logs, the offline outbox) lives in
; %LOCALAPPDATA%\PokerHandTracker and is NOT part of the installation: an upgrade or reinstall keeps it, and uninstalling
; asks before deleting it (a silent uninstall always keeps it).

#ifndef SourceDir
  #error SourceDir is required: the folder produced by PyInstaller (dist\PokerHandTracker).
#endif
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#ifndef BuildNumber
  #define BuildNumber "0"
#endif

#define AppName "Poker Hand Tracker"
#define AppExe  "PokerHandTracker.exe"

[Setup]
; Never change this GUID: it is how Windows (and later versions of this installer) recognise the same application.
AppId={{7C3DEB4D-E233-4733-AF85-303A235DA84E}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
; Windows version resources hold four numbers of at most 65535, so the long build number goes in the text version instead.
VersionInfoVersion={#AppVersion}.0
VersionInfoProductTextVersion={#AppVersion} (build {#BuildNumber})
AppPublisher=Poker Hand Tracker
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputBaseFilename=PokerHandTracker-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Start {#AppName}"; Flags: nowait postinstall skipifsilent

[Code]
// Uninstall: the program files always go; the user's own data is deleted only if they say so.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\PokerHandTracker');
    if DirExists(DataDir) and (not UninstallSilent) then
      if MsgBox('Also delete your settings, calibration, taught cards and logs?' + #13#10 + #13#10 + DataDir + #13#10 + #13#10 +
                'Choose No to keep them for a later reinstall.', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;
