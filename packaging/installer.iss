; Inno Setup script for copilot-voice-shell
; Build with:  ISCC.exe packaging\installer.iss
; Requires the PyInstaller one-folder output in dist\copilot-voice-shell.

#define MyAppName "Bubble Buddy"
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
DefaultDirName={autopf}\BubbleBuddy
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist\installer
OutputBaseFilename=BubbleBuddy{#EditionSuffix}-Setup-{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
SetupIconFile=bb.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequiredOverridesAllowed=dialog
; Don't show Inno's own "Select Setup Language" dialog up front; the wizard
; chrome follows the detected OS language, and the app's interface language is
; chosen on the dedicated Interface-language page instead.
ShowLanguageDialog=no
LanguageDetectionMethod=uilanguage

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

[Code]
{ ---- Optional configuration wizard -------------------------------------- }
{ Lets the user import an existing config.json or enter basic Azure settings  }
{ during install. The result is written to %USERPROFILE%\.copilot-voice-shell }
{ \config.json, which the app reads on startup.                              }
var
  ChoicePage: TInputOptionWizardPage;
  FilePage: TInputFileWizardPage;
  BasicPage: TInputQueryWizardPage;
  LangPage: TInputOptionWizardPage;

procedure InitializeWizard;
begin
  ChoicePage := CreateInputOptionPage(wpSelectTasks,
    'Configure {#MyAppName}',
    'How would you like to set it up?',
    'Bubble Buddy uses an Azure OpenAI endpoint for speech-to-text and polishing. ' +
    'You can configure it now, or skip and set it up inside the app later.',
    True, False);
  ChoicePage.Add('Import an existing config.json');
  ChoicePage.Add('Basic setup (enter Azure endpoint now)');
  ChoicePage.Add('Skip - I will configure it in the app');
  ChoicePage.SelectedValueIndex := 2;

  FilePage := CreateInputFilePage(ChoicePage.ID,
    'Import configuration',
    'Select your config.json',
    'Choose the config.json to use. It will be copied to your user profile.');
  FilePage.Add('Config file:', 'JSON files|*.json|All files|*.*', '.json');

  BasicPage := CreateInputQueryPage(FilePage.ID,
    'Basic setup',
    'Azure OpenAI endpoint',
    'Enter your Azure OpenAI resource endpoint. You can sign in to Azure from ' +
    'inside the app after installation.');
  BasicPage.Add('Endpoint (https://<resource>.cognitiveservices.azure.com/):', False);

  LangPage := CreateInputOptionPage(BasicPage.ID,
    'Interface language',
    'Choose the language for the Bubble Buddy interface',
    'This sets the "ui_language" option. You can change it later in Settings.',
    True, False);
  LangPage.Add('Auto (follow system language)');
  LangPage.Add('Chinese / 中文');
  LangPage.Add('English');
  LangPage.SelectedValueIndex := 0;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  if PageID = FilePage.ID then
    Result := ChoicePage.SelectedValueIndex <> 0
  else if PageID = BasicPage.ID then
    Result := ChoicePage.SelectedValueIndex <> 1
  else if PageID = LangPage.ID then
    // Show the interface-language page for basic setup and skip; imported
    // config.json files already carry their own ui_language.
    Result := ChoicePage.SelectedValueIndex = 0;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (CurPageID = FilePage.ID) and (ChoicePage.SelectedValueIndex = 0) then
  begin
    if (Trim(FilePage.Values[0]) = '') or (not FileExists(FilePage.Values[0])) then
    begin
      MsgBox('Please select an existing config.json file, or go back and choose ' +
        'a different option.', mbError, MB_OK);
      Result := False;
    end;
  end;
end;

function ConfigDir(): String;
begin
  Result := ExpandConstant('{%USERPROFILE}') + '\.copilot-voice-shell';
end;

function JsonEscape(const S: String): String;
var
  R: String;
begin
  R := S;
  StringChangeEx(R, '\', '\\', True);
  StringChangeEx(R, '"', '\"', True);
  Result := R;
end;

procedure WriteBasicConfig();
var
  Dir, Path, Endpoint, Lang, Json: String;
begin
  Dir := ConfigDir();
  ForceDirectories(Dir);
  Path := Dir + '\config.json';
  Endpoint := Trim(BasicPage.Values[0]);
  case LangPage.SelectedValueIndex of
    1: Lang := 'zh';
    2: Lang := 'en';
  else
    Lang := 'auto';
  end;
  Json :=
    '{' + #13#10 +
    '  "backend": "azure",' + #13#10 +
    '  "polish": "auto",' + #13#10 +
    '  "polish_engine": "azure",' + #13#10 +
    '  "ui_language": "' + Lang + '",' + #13#10 +
    '  "azure": {' + #13#10 +
    '    "endpoint": "' + JsonEscape(Endpoint) + '",' + #13#10 +
    '    "auth": "aad"' + #13#10 +
    '  }' + #13#10 +
    '}' + #13#10;
  SaveStringToFile(Path, Json, False);
end;

procedure WriteLangOnlyConfig();
var
  Dir, Path, Lang, Json: String;
begin
  case LangPage.SelectedValueIndex of
    1: Lang := 'zh';
    2: Lang := 'en';
  else
    Lang := '';
  end;
  // Only persist an explicit choice; leaving "Auto" writes nothing so skip stays clean.
  if Lang = '' then
    exit;
  Dir := ConfigDir();
  ForceDirectories(Dir);
  Path := Dir + '\config.json';
  Json := '{' + #13#10 + '  "ui_language": "' + Lang + '"' + #13#10 + '}' + #13#10;
  SaveStringToFile(Path, Json, False);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Dir, Path, Src: String;
begin
  if CurStep = ssPostInstall then
  begin
    if ChoicePage.SelectedValueIndex = 0 then
    begin
      Src := Trim(FilePage.Values[0]);
      if (Src <> '') and FileExists(Src) then
      begin
        Dir := ConfigDir();
        ForceDirectories(Dir);
        Path := Dir + '\config.json';
        CopyFile(Src, Path, False);
      end;
    end
    else if ChoicePage.SelectedValueIndex = 1 then
      WriteBasicConfig()
    else
      WriteLangOnlyConfig();
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Dir: String;
begin
  // The app stores config.json + the Azure sign-in record under the user profile,
  // outside the install folder, so Setup does not remove it automatically.
  if CurUninstallStep = usUninstall then
  begin
    Dir := ConfigDir();
    if DirExists(Dir) then
    begin
      if MsgBox('Also remove your Bubble Buddy settings and Azure sign-in' + #13#10 +
        '(' + Dir + ')?' + #13#10 +
        'Choose No to keep them for a future re-install.',
        mbConfirmation, MB_YESNO) = IDYES then
        DelTree(Dir, True, True, True);
    end;
  end;
end;
