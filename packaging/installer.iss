; Inno Setup script for copilot-voice-shell
; Build with:  ISCC.exe packaging\installer.iss
; Requires the PyInstaller one-folder output in dist\copilot-voice-shell.

#define MyAppName "Copilot Voice Shell"
#ifndef MyAppVersion
#define MyAppVersion "0.1.0"
#endif
; Edition: "azure" (lean, cloud only) or "full" (bundles offline Whisper).
#ifndef Edition
#define Edition "azure"
#endif
#if Edition == "full"
  #define EditionSuffix "-Full"
  #define EditionLabel " (Full · 含离线 Whisper)"
#else
  #define EditionSuffix ""
  #define EditionLabel ""
#endif
#define MyAppPublisher "ThousandsOfWind"
#define MyAppExeName "copilot-voice-shell.exe"

[Setup]
AppId={{1C8E0F7B-FBC7-4847-965E-3B42240D766A}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}{#EditionLabel}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\CopilotVoiceShell
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist\installer
OutputBaseFilename=CopilotVoiceShell{#EditionSuffix}-Setup-{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
SetupIconFile=bb.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
; Simplified Chinese is vendored next to this script so CI builds don't depend on
; a network download; it is included only when present (graceful English fallback).
#if FileExists(AddBackslash(SourcePath) + "ChineseSimplified.isl")
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"
#endif

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\copilot-voice-shell\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
