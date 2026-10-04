; StrixRun Windows installer (Inno Setup 6).
;
; Per-user install, no admin prompt. Bundles uv.exe and the StrixRun wheel; at install
; time uv sets up a private Python and StrixRun inside the install folder, so nothing
; else on the machine changes. Models and settings live in %USERPROFILE%\.strix and are
; kept on uninstall.
;
; Built by .github/workflows/windows-installer.yml:
;   ISCC.exe /DAppVersion=0.1.0 /DWheel=strixrun-0.1.0-py3-none-any.whl /DSourceDir=<staging> StrixRun.iss

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef Wheel
  #error Pass /DWheel=<wheel file name>
#endif
#ifndef SourceDir
  #define SourceDir "."
#endif

[Setup]
AppId={{6F5E9C1A-3B7D-4C2E-9A51-5D3E8B7F2C40}
AppName=StrixRun
AppVersion={#AppVersion}
AppPublisher=StrixRun
AppComments=Local LLM server with an OpenAI-compatible API
DefaultDirName={localappdata}\Programs\StrixRun
DefaultGroupName=StrixRun
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputBaseFilename=StrixRun-Setup-{#AppVersion}
SetupIconFile={#SourceDir}\strixrun.ico
UninstallDisplayIcon={app}\strixrun.ico
UninstallDisplayName=StrixRun
WizardStyle=modern
Compression=lzma2
SolidCompression=yes
ChangesEnvironment=yes
CloseApplications=no

[Tasks]
Name: "startup"; Description: "Start StrixRun when I sign in"; Flags: unchecked
Name: "addtopath"; Description: "Add the strixrun command to PATH"

[Files]
Source: "{#SourceDir}\uv.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\{#Wheel}"; DestDir: "{app}\wheel"; Flags: ignoreversion
Source: "{#SourceDir}\strixrun.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; pythonw: no console window. `strixrun open` starts the server if needed and opens the dashboard.
Name: "{group}\StrixRun"; Filename: "{app}\tools\strixrun\Scripts\pythonw.exe"; Parameters: "-m strixrun open"; \
  WorkingDir: "{app}"; IconFilename: "{app}\strixrun.ico"; Comment: "Start StrixRun and open the dashboard"
Name: "{group}\Stop StrixRun"; Filename: "{app}\tools\strixrun\Scripts\pythonw.exe"; Parameters: "-m strixrun stop"; \
  WorkingDir: "{app}"; IconFilename: "{app}\strixrun.ico"; Comment: "Stop the StrixRun server"

[Run]
; uv installs Python 3.12 and StrixRun's packages into the install folder (needs internet).
Filename: "{app}\uv.exe"; \
  Parameters: "tool install --force --python 3.12 ""{app}\wheel\{#Wheel}"""; \
  StatusMsg: "Installing StrixRun (downloads Python and packages, about a minute)..."; \
  Flags: runhidden waituntilterminated; BeforeInstall: SetUvEnv
Filename: "{app}\bin\strixrun.exe"; Parameters: "service install"; \
  StatusMsg: "Setting StrixRun to start when you sign in..."; Flags: runhidden waituntilterminated; Tasks: startup
Filename: "{app}\tools\strixrun\Scripts\pythonw.exe"; Parameters: "-m strixrun open"; \
  Description: "Start StrixRun and open the dashboard"; Flags: postinstall nowait skipifsilent

[UninstallRun]
Filename: "{app}\bin\strixrun.exe"; Parameters: "stop"; Flags: runhidden waituntilterminated; RunOnceId: "StopServer"
Filename: "{app}\bin\strixrun.exe"; Parameters: "service uninstall"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveService"

[UninstallDelete]
; The private Python, packages and uv cache. Models and settings in %USERPROFILE%\.strix are kept.
Type: filesandordirs; Name: "{app}"

[Code]
function SetEnvironmentVariable(lpName, lpValue: string): Boolean;
  external 'SetEnvironmentVariableW@kernel32.dll stdcall';

procedure SetUvEnv();
begin
  { Keep everything uv creates inside the install folder. }
  SetEnvironmentVariable('UV_TOOL_DIR', ExpandConstant('{app}\tools'));
  SetEnvironmentVariable('UV_TOOL_BIN_DIR', ExpandConstant('{app}\bin'));
  SetEnvironmentVariable('UV_PYTHON_INSTALL_DIR', ExpandConstant('{app}\python'));
  SetEnvironmentVariable('UV_CACHE_DIR', ExpandConstant('{app}\cache'));
  SetEnvironmentVariable('UV_PYTHON_PREFERENCE', 'only-managed');
end;

function InstallSucceeded(): Boolean;
begin
  Result := FileExists(ExpandConstant('{app}\bin\strixrun.exe'));
end;

const EnvKey = 'Environment';

function PathContains(Dir: string): Boolean;
var Paths: string;
begin
  Result := False;
  if RegQueryStringValue(HKCU, EnvKey, 'Path', Paths) then
    Result := Pos(';' + Uppercase(Dir) + ';', ';' + Uppercase(Paths) + ';') > 0;
end;

procedure AddToPath(Dir: string);
var Paths: string;
begin
  if PathContains(Dir) then exit;
  if not RegQueryStringValue(HKCU, EnvKey, 'Path', Paths) then Paths := '';
  if (Paths <> '') and (Copy(Paths, Length(Paths), 1) <> ';') then Paths := Paths + ';';
  RegWriteExpandStringValue(HKCU, EnvKey, 'Path', Paths + Dir);
end;

procedure RemoveFromPath(Dir: string);
var Paths: string; P: Integer;
begin
  if not RegQueryStringValue(HKCU, EnvKey, 'Path', Paths) then exit;
  P := Pos(';' + Uppercase(Dir), ';' + Uppercase(Paths));
  if P = 0 then exit;
  Delete(Paths, P, Length(Dir) + 1);
  if (Length(Paths) > 0) and (Paths[1] = ';') then Delete(Paths, 1, 1);
  if (Length(Paths) > 0) and (Paths[Length(Paths)] = ';') then Delete(Paths, Length(Paths), 1);
  RegWriteExpandStringValue(HKCU, EnvKey, 'Path', Paths);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    if not InstallSucceeded() then
      MsgBox('StrixRun could not finish installing. It needs an internet connection the first time, ' +
             'to download Python and its packages. Check your connection and run the installer again.',
             mbError, MB_OK)
    else if WizardIsTaskSelected('addtopath') then
      AddToPath(ExpandConstant('{app}\bin'));
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    RemoveFromPath(ExpandConstant('{app}\bin'));
end;
