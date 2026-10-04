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
; Writes "Setup Log <date>.txt" to %TEMP% for troubleshooting.
SetupLogging=yes
; Inno 6.5+ applies a redirection-guard mitigation that child processes inherit; it
; stops uv from following the NTFS junctions it creates for managed Python installs.
RedirectionGuard=no

[Tasks]
Name: "startup"; Description: "Start StrixRun when I sign in"; Flags: unchecked
Name: "addtopath"; Description: "Add the strixrun command to PATH"

[Files]
Source: "{#SourceDir}\uv.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\{#Wheel}"; DestDir: "{app}\wheel"; Flags: ignoreversion
Source: "{#SourceDir}\strixrun.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\StrixRun.pyw"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; pythonw: no console window. `strixrun open` starts the server if needed and opens the dashboard.
Name: "{group}\StrixRun"; Filename: "{app}\tools\strixrun\Scripts\pythonw.exe"; Parameters: """{app}\StrixRun.pyw"" open"; \
  WorkingDir: "{app}"; IconFilename: "{app}\strixrun.ico"; Comment: "Start StrixRun and open the dashboard"
Name: "{group}\Stop StrixRun"; Filename: "{app}\tools\strixrun\Scripts\pythonw.exe"; Parameters: """{app}\StrixRun.pyw"" stop"; \
  WorkingDir: "{app}"; IconFilename: "{app}\strixrun.ico"; Comment: "Stop the StrixRun server"

[Run]
; Installing Python and StrixRun (and the optional sign-in task) happens in [Code] at
; ssPostInstall, so failures are logged, retried once and explained.
Filename: "{app}\tools\strixrun\Scripts\pythonw.exe"; Parameters: """{app}\StrixRun.pyw"" open"; \
  Description: "Start StrixRun and open the dashboard"; Flags: postinstall nowait skipifsilent; Check: InstallSucceeded

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

function UvLogPath(): string;
begin
  Result := ExpandConstant('{app}\install-uv.log');
end;

function RunLogged(Args: string): Integer;
var Cmd: string; Code: Integer;
begin
  Cmd := '/s /c ""' + ExpandConstant('{app}\uv.exe') + '" ' + Args + ' >> "' + UvLogPath() + '" 2>&1"';
  Log('Running: cmd.exe ' + Cmd);
  if not Exec(ExpandConstant('{cmd}'), Cmd, ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, Code) then
    Code := -1;
  Log(Format('exit code %d', [Code]));
  Result := Code;
end;

{ The python.exe inside the full-version folder (cpython-3.12.N-windows-...), never the
  minor-version junction (cpython-3.12-windows-...) that uv also creates. }
function ManagedPythonExe(): string;
var Rec: TFindRec; Root, Name: string;
begin
  Result := '';
  Root := ExpandConstant('{app}\python\');
  if FindFirst(Root + 'cpython-3.12.*', Rec) then
  try
    repeat
      Name := Rec.Name;
      if ((Rec.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0) and
         ((Rec.Attributes and $400) = 0) { not a junction (FILE_ATTRIBUTE_REPARSE_POINT) } and
         FileExists(Root + Name + '\python.exe') then
        Result := Root + Name + '\python.exe';
    until (Result <> '') or not FindNext(Rec);
  finally
    FindClose(Rec);
  end;
end;

function RunUvInstall(): Integer;
var Python: string;
begin
  SetUvEnv();
  Result := RunLogged('python install 3.12');
  Python := ManagedPythonExe();
  Log('Managed Python: ' + Python);
  if Python = '' then
  begin
    if Result = 0 then Result := -2;
    exit;
  end;
  Result := RunLogged('tool install --force --python "' + Python + '" "' + ExpandConstant('{app}\wheel\{#Wheel}') + '"');
end;

function LogTail(Lines: Integer): string;
var All: TArrayOfString; I, First: Integer;
begin
  Result := '';
  if not LoadStringsFromFile(UvLogPath(), All) then exit;
  First := GetArrayLength(All) - Lines;
  if First < 0 then First := 0;
  for I := First to GetArrayLength(All) - 1 do
    Result := Result + All[I] + #13#10;
end;

{ Upgrading over a running StrixRun: its Python files are in use, so uv can't replace
  them and the install is left half-replaced. Stop it (and anything else running from
  the install folder) before touching files. }
function PrepareToInstall(var NeedsRestart: Boolean): String;
var Code: Integer; Exe, Ps: string;
begin
  Result := '';
  Exe := ExpandConstant('{app}\bin\strixrun.exe');
  if FileExists(Exe) then
  begin
    Log('Stopping a running StrixRun before upgrading');
    Exec(Exe, 'stop', '', SW_HIDE, ewWaitUntilTerminated, Code);
    Log(Format('strixrun stop exited with %d', [Code]));
  end;
  Ps := '-NoProfile -NonInteractive -Command "$d = ''' + ExpandConstant('{app}') + '''; ' +
        'Get-Process | Where-Object { $_.Path -and $_.Path.StartsWith($d, [StringComparison]::OrdinalIgnoreCase) } | ' +
        'Stop-Process -Force -ErrorAction SilentlyContinue"';
  Exec('powershell.exe', Ps, '', SW_HIDE, ewWaitUntilTerminated, Code);
  Log(Format('closed leftover StrixRun processes (powershell exit %d)', [Code]));
end;

procedure CurStepChanged(CurStep: TSetupStep);
var Code: Integer;
begin
  if CurStep <> ssPostInstall then exit;
  WizardForm.StatusLabel.Caption := 'Installing StrixRun (downloads Python and packages, about a minute)...';
  WizardForm.ProgressGauge.Style := npbstMarquee;
  Code := RunUvInstall();
  if not InstallSucceeded() then
  begin
    Log('First attempt failed; retrying once.');
    WizardForm.StatusLabel.Caption := 'Retrying the StrixRun install...';
    Code := RunUvInstall();
  end;
  WizardForm.ProgressGauge.Style := npbstNormal;
  if not InstallSucceeded() then
  begin
    MsgBox('StrixRun could not finish installing (uv exited with ' + IntToStr(Code) + ').' + #13#10#13#10 +
           LogTail(12) + #13#10 +
           'The full log is at:' + #13#10 + UvLogPath() + #13#10#13#10 +
           'It needs an internet connection the first time. If antivirus software blocked uv.exe or ' +
           'strixrun.exe, allow them and run the installer again.', mbError, MB_OK);
    exit;
  end;
  if WizardIsTaskSelected('startup') then
  begin
    WizardForm.StatusLabel.Caption := 'Setting StrixRun to start when you sign in...';
    if not Exec(ExpandConstant('{app}\bin\strixrun.exe'), 'service install', ExpandConstant('{app}'),
                SW_HIDE, ewWaitUntilTerminated, Code) or (Code <> 0) then
      MsgBox('StrixRun is installed, but it could not be set to start when you sign in (exit ' +
             IntToStr(Code) + '). You can run "strixrun service install" later.', mbInformation, MB_OK);
  end;
  if WizardIsTaskSelected('addtopath') then
    AddToPath(ExpandConstant('{app}\bin'));
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    RemoveFromPath(ExpandConstant('{app}\bin'));
end;
