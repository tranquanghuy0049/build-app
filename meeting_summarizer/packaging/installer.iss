; Inno Setup script for the Windows installer.
;
; Consumes dist\MeetingSummarizer\ as produced by packaging\MeetingSummarizer.spec
; and emits dist\MeetingSummarizer-<version>-win64-setup.exe.
;
;   ISCC.exe /DAppVersion=1.0.0 packaging\installer.iss
;
; Normally invoked by packaging\build_windows.ps1 rather than by hand.

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

#define AppName "Meeting Summarizer"
#define AppExe "MeetingSummarizer.exe"
#define AppPublisher "TNT"

[Setup]
AppId={{9F3C41B7-6E2A-4D58-9C10-2B7A5E0D8C43}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
VersionInfoVersion={#AppVersion}

DefaultDirName={autopf}\MeetingSummarizer
DefaultGroupName={#AppName}
; Nothing here needs machine-wide access, so install per-user by default and
; skip the UAC prompt entirely. A user who wants it in Program Files for
; everyone can still elevate from the installer's first dialog.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

; The payload is a Python runtime plus torch and the speech model. Requiring
; 64-bit is not a policy choice: no 32-bit torch wheel exists.
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

; Relative to this .iss file, so every path below starts at meeting_summarizer\.
; Not cosmetic: ISCC concatenates rather than normalises, so the "packaging\..\"
; that a relative Source would otherwise carry counted against the 260-character
; path limit on every single file it compressed.
SourceDir=..

OutputDir=dist
OutputBaseFilename=MeetingSummarizer-{#AppVersion}-win64-setup
; Solid LZMA2 across a multi-gigabyte payload costs real build time but takes a
; large bite out of what users have to download.
Compression=lzma2/max
SolidCompression=yes

DisableProgramGroupPage=yes
WizardStyle=modern
LicenseFile=
; Shown before install; the guide itself is installed alongside the app.
InfoBeforeFile=
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExe}
; The shortcuts and the uninstall entry take their icon from the exe, which
; PyInstaller stamps from packaging\icon.ico. This one covers the setup.exe the
; user double-clicks, which otherwise carries Inno's default icon. Relative to
; SourceDir above, hence the packaging\ prefix.
SetupIconFile=packaging\icon.ico

[Languages]
; Inno 6 ships no Vietnamese translation. The wizard is English; everything the
; user actually works in — the app itself and HUONG_DAN_SU_DUNG.txt — is not.
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; The exe first so the wizard's progress bar starts moving immediately, then the
; ~3 GB of runtime and model weights behind it.
Source: "dist\MeetingSummarizer\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\MeetingSummarizer\*"; DestDir: "{app}"; Excludes: "{#AppExe}"; Flags: ignoreversion recursesubdirs createallsubdirs
; The Windows-specific guide. HUONG_DAN_SU_DUNG.txt is the macOS one and is
; deliberately not shipped here — its install and quit steps are wrong for
; Windows, and a guide that does not match what the user sees is worse than none.
Source: "HUONG_DAN_SU_DUNG_WINDOWS.txt"; DestDir: "{app}"; DestName: "HUONG_DAN_SU_DUNG.txt"; Flags: ignoreversion isreadme

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Huong dan su dung"; Filename: "{app}\HUONG_DAN_SU_DUNG.txt"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; PyInstaller's onedir layout is fully covered by [Files], but a run leaves
; __pycache__ directories behind that would otherwise strand an empty {app}.
Type: filesandordirs; Name: "{app}\_internal\__pycache__"

; Deliberately not removed on uninstall: %APPDATA%\MeetingSummarizer holds the
; user's API keys and the model cache, and reinstalling should not ask for them
; again. ~\Documents\MeetingSummarizer holds their transcripts.
