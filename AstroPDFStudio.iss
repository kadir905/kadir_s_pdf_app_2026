#define MyAppName "AstroPDF Studio"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "AstroPDF"
#define MyAppExeName "AstroPDFStudio.exe"

[Setup]
AppId={{B5A4B719-6BA7-4AF7-8E11-3F0C9D0A7B22}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\AstroPDF Studio
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=installer
OutputBaseFilename=AstroPDFStudio-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=kadir_pdf_logo.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "turkish"; MessagesFile: "compiler:Languages\Turkish.isl"

[Tasks]
Name: "desktopicon"; Description: "Masaüstü kısayolu oluştur"; GroupDescription: "Ek kısayollar:"

[Files]
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "kadir_pdf_logo.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "derscalismauygulamam-firebase-adminsdk-fbsvc-50de873e2a.json"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{#MyAppName} uygulamasını başlat"; Flags: nowait postinstall skipifsilent
