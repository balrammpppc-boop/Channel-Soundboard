[Setup]
AppName=Channel Soundboard
AppVersion=1.1
DefaultDirName={autopf}\ChannelSoundboard
DefaultGroupName=Channel Soundboard
OutputDir=installer
OutputBaseFilename=ChannelSoundboard-Setup
Compression=lzma2
SolidCompression=yes
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\ChannelSoundboard.exe
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest

[Tasks]
Name: desktopicon; Description: "Create a desktop icon"

[Files]
Source: "dist\ChannelSoundboard\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{autoprograms}\Channel Soundboard"; Filename: "{app}\ChannelSoundboard.exe"
Name: "{autodesktop}\Channel Soundboard"; Filename: "{app}\ChannelSoundboard.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\ChannelSoundboard.exe"; Description: "Start Channel Soundboard"; Flags: nowait postinstall skipifsilent
