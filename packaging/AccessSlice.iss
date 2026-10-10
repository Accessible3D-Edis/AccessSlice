; AccessSlice.iss - the Inno Setup script for AccessSlice-Setup.exe
;
; Compiled by packaging\build.py (which passes /DAppVersion from
; app\version.py) after the PyInstaller bundle exists in
; packaging\dist\AccessSlice\. The installer:
;
;   - installs per user by default (no administrator prompt; the
;     Program Files choice is still offered),
;   - checks for the Microsoft Edge WebView2 runtime the window needs
;     (Windows 11 ships it; on Windows 10 the installer sends the user
;     to Microsoft's download page if it is missing) - without it
;     AccessSlice still runs, in the browser,
;   - makes a Start menu entry and an optional desktop shortcut,
;   - registers an uninstaller in Apps and Features,
;   - never touches the user's AccessSlice configuration in AppData.
;
; The wizard is Inno Setup's standard one, which screen readers read.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

; Code signing: build.py passes /DSign=1 and defines the "accessslice"
; sign tool on the command line (/S) when packaging\signing.json
; exists. Then Inno signs Setup.exe and the uninstaller it embeds with
; the same command that signed AccessSlice.exe. Without it: unsigned.

[Setup]
AppId={{A0221D36-E43F-434C-A164-A26D57074E0B}
AppName=AccessSlice
AppVersion={#AppVersion}
AppVerName=AccessSlice {#AppVersion}
AppPublisher=AccessSlice
AppPublisherURL=https://github.com/Accessible3D-Edis/AccessSlice
AppSupportURL=https://github.com/Accessible3D-Edis/AccessSlice/issues
AppUpdatesURL=https://github.com/Accessible3D-Edis/AccessSlice/releases
DefaultDirName={autopf}\AccessSlice
DefaultGroupName=AccessSlice
UninstallDisplayIcon={app}\AccessSlice.exe
UninstallDisplayName=AccessSlice
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputBaseFilename=AccessSlice-Setup-{#AppVersion}
SetupIconFile=accessslice.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes
CloseApplications=yes
#ifdef Sign
SignTool=accessslice
SignedUninstaller=yes
#endif

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "dist\AccessSlice\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion
Source: "..\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\AccessSlice"; Filename: "{app}\AccessSlice.exe"; Comment: "Accessible slicing for 3D printing"
Name: "{group}\Uninstall AccessSlice"; Filename: "{uninstallexe}"
Name: "{autodesktop}\AccessSlice"; Filename: "{app}\AccessSlice.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\AccessSlice.exe"; Description: "Start AccessSlice now"; Flags: nowait postinstall skipifsilent

[Code]
// The WebView2 runtime writes its version under one of these keys
// (per machine, per user, and the WOW6432Node view on 64-bit Windows).
function WebView2Installed(): Boolean;
var
  Version: String;
begin
  Result :=
    RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version) or
    RegQueryStringValue(HKLM, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version) or
    RegQueryStringValue(HKCU, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version);
  if Result then
    Result := (Version <> '') and (Version <> '0.0.0.0');
end;

function InitializeSetup(): Boolean;
var
  ErrorCode: Integer;
begin
  Result := True;
  if not WebView2Installed() then
  begin
    if MsgBox('AccessSlice opens in its own window using the Microsoft Edge WebView2 runtime, which is not installed on this computer.' + #13#10#13#10 +
              'Windows 11 includes it; on Windows 10 it is a free download from Microsoft.' + #13#10#13#10 +
              'Press Yes to open the download page in your browser (then run this installer again), or No to install AccessSlice anyway - it will open in your web browser instead of its own window.',
              mbConfirmation, MB_YESNO) = IDYES then
    begin
      ShellExec('open', 'https://developer.microsoft.com/microsoft-edge/webview2/', '', '', SW_SHOWNORMAL, ewNoWait, ErrorCode);
      Result := False;
    end;
  end;
end;
