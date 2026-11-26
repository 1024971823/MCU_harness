; MCU Harness Studio — Windows Installer Script (Inno Setup 6)
; Expects staged directory layout via /DStageDir=<path> containing:
;   mcu-harness-studio.exe + windeployqt Qt6 runtime DLLs/QML/plugins
;   engine\mcu_harness\... (copied from src/mcu_harness)
;   python\...             (Python 3.12 embeddable distribution)
;   icons\mcu-harness-studio.ico
;   licenses\*.txt

#define MyAppName "MCU Harness Studio"
#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif
#define MyAppPublisher "Lannuo"
#define MyAppURL "https://mcuharness.dev"
#define MyAppExeName "mcu-harness-studio.exe"
#ifndef StageDir
  #define StageDir "..\..\build\windows-stage"
#endif
#ifndef OutDir
  #define OutDir "..\..\..\dist"
#endif

[Setup]
AppId={{8F6C3E1A-9D4B-4E21-9A77-3B82C1D4F09A}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
UninstallDisplayIcon={app}\{#MyAppExeName}
SetupIconFile={#StageDir}\icons\mcu-harness-studio.ico
OutputDir={#OutDir}
OutputBaseFilename=MCU_Harness_Studio-{#MyAppVersion}-x86_64-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#StageDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
