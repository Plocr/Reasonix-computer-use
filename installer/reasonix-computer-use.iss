#ifndef AppVersion
  #error AppVersion must be provided by build_installer.ps1
#endif
#ifndef StageDir
  #error StageDir must be provided by build_installer.ps1
#endif
#ifndef OutputDir
  #error OutputDir must be provided by build_installer.ps1
#endif

[Setup]
AppId={{A67C33C4-4865-4A7E-B98E-E78AA19E7892}
AppName=Reasonix Computer Use
AppVersion={#AppVersion}
AppPublisher=Plocr
AppPublisherURL=https://github.com/Plocr/Reasonix-computer-use
DefaultDirName={localappdata}\ReasonixPlugins\computer-use
DefaultGroupName=Reasonix Computer Use
OutputDir={#OutputDir}
OutputBaseFilename=reasonix-computer-use-{#AppVersion}-windows-x64-setup
Compression=lzma2/ultra64
SolidCompression=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
UninstallDisplayIcon={app}\runtime\python.exe
DisableProgramGroupPage=yes

[Files]
Source: "{#StageDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\打开插件目录"; Filename: "{app}"
Name: "{group}\项目主页"; Filename: "https://github.com/Plocr/Reasonix-computer-use"

[Run]
Filename: "{cmd}"; Parameters: "/d /c reasonix plugin install ""{app}"" --link --replace --yes"; WorkingDir: "{app}"; StatusMsg: "正在注册 Reasonix 插件..."; Flags: runhidden waituntilterminated; Check: ReasonixCliAvailable
Filename: "{cmd}"; Parameters: "/d /c reasonix plugin doctor computer-use"; WorkingDir: "{app}"; StatusMsg: "正在验证 Reasonix 插件..."; Flags: runhidden waituntilterminated; Check: ReasonixCliAvailable
Filename: "{app}"; Description: "打开插件目录"; Flags: shellexec postinstall skipifsilent unchecked

[Code]
function ReasonixCliAvailable(): Boolean;
var
  ResultCode: Integer;
begin
  Result := Exec(ExpandConstant('{cmd}'), '/d /c where reasonix >nul 2>nul', '',
    SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and (not ReasonixCliAvailable()) then
    MsgBox('插件文件已安装。Reasonix CLI 不在 PATH 中，请在 Reasonix Desktop 的“设置 → 插件 → 本地目录”中选择：' + #13#10 + ExpandConstant('{app}'),
      mbInformation, MB_OK);
end;
