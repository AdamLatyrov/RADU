#define SourceDir GetEnv("RADU_PACKAGE_DIR")
#define AppVersion GetEnv("RADU_APP_VERSION")

[Setup]
AppId={{9F4C9F06-8F20-455C-88BA-47E84A62E7A5}
AppName=RADU
AppVersion={#AppVersion}
AppVerName=RADU {#AppVersion}
AppPublisher=Adam Latyrov
AppPublisherURL=https://github.com/AdamLatyrov/RADU
DefaultDirName={localappdata}\Programs\RADU
DefaultGroupName=RADU
DisableProgramGroupPage=yes
OutputDir=..\artifacts
OutputBaseFilename=RADU-Setup
SetupIconFile={#SourceDir}\radu.ico
UninstallDisplayIcon={app}\RADU.exe
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
PrivilegesRequired=lowest
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
SetupLogging=yes

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
russian.DesktopIconTask=Создать ярлык на рабочем столе
english.DesktopIconTask=Create a desktop shortcut
russian.AdditionalOptions=Дополнительно
english.AdditionalOptions=Additional options

[Tasks]
Name: "desktopicon"; Description: "{cm:DesktopIconTask}"; GroupDescription: "{cm:AdditionalOptions}"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\RADU"; Filename: "{app}\RADU.exe"
Name: "{autodesktop}\RADU"; Filename: "{app}\RADU.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\RADU.exe"; Description: "Запустить RADU"; Flags: postinstall nowait skipifsilent; Languages: russian
Filename: "{app}\RADU.exe"; Description: "Launch RADU"; Flags: postinstall nowait skipifsilent; Languages: english
