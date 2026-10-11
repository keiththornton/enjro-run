; Enjro Run Windows installer (Inno Setup 6).
;
; Per-user install, no admin prompt. Bundles uv.exe and the Enjro Run wheel; at install
; time uv sets up a private Python and Enjro Run inside the install folder, so nothing
; else on the machine changes. Models and settings live in %USERPROFILE%\.enjro and are
; kept on uninstall.
;
; Enjro Run was called StrixRun. This installer keeps StrixRun's AppId, so it upgrades a
; StrixRun install in place (one entry in Apps): it stops StrixRun and removes its old
; folder, shortcuts and PATH entry, then `enjrorun migrate` moves %USERPROFILE%\.strix\run
; and the sign-in task over.
;
; Built by .github/workflows/windows-installer.yml:
;   ISCC.exe /DAppVersion=0.1.0 /DWheel=enjrorun-0.1.0-py3-none-any.whl /DSourceDir=<staging> EnjroRun.iss

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
AppName=Enjro Run
AppVersion={#AppVersion}
AppPublisher=Enjro
AppComments=Local LLM server with an OpenAI-compatible API
DefaultDirName={localappdata}\Programs\EnjroRun
DefaultGroupName=Enjro Run
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputBaseFilename=EnjroRun-Setup-{#AppVersion}
SetupIconFile={#SourceDir}\enjrorun.ico
UninstallDisplayIcon={app}\enjrorun.ico
UninstallDisplayName=Enjro Run
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
Name: "startup"; Description: "Start Enjro Run when I sign in"; Flags: unchecked
Name: "addtopath"; Description: "Add the enjrorun command to PATH"

[Files]
Source: "{#SourceDir}\uv.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\{#Wheel}"; DestDir: "{app}\wheel"; Flags: ignoreversion
Source: "{#SourceDir}\enjrorun.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\EnjroRun.pyw"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; pythonw: no console window. `enjrorun open` starts the server if needed and opens the dashboard.
Name: "{group}\Enjro Run"; Filename: "{app}\tools\enjrorun\Scripts\pythonw.exe"; Parameters: """{app}\EnjroRun.pyw"" open"; \
  WorkingDir: "{app}"; IconFilename: "{app}\enjrorun.ico"; Comment: "Start Enjro Run and open the dashboard"
Name: "{group}\Stop Enjro Run"; Filename: "{app}\tools\enjrorun\Scripts\pythonw.exe"; Parameters: """{app}\EnjroRun.pyw"" stop"; \
  WorkingDir: "{app}"; IconFilename: "{app}\enjrorun.ico"; Comment: "Stop the Enjro Run server"

[Run]
; Installing Python and Enjro Run (and the optional sign-in task) happens in [Code] at
; ssPostInstall, so failures are logged, retried once and explained.
Filename: "{app}\tools\enjrorun\Scripts\pythonw.exe"; Parameters: """{app}\EnjroRun.pyw"" open"; \
  Description: "Start Enjro Run and open the dashboard"; Flags: postinstall nowait skipifsilent; Check: InstallSucceeded

[UninstallRun]
Filename: "{app}\bin\enjrorun.exe"; Parameters: "stop"; Flags: runhidden waituntilterminated; RunOnceId: "StopServer"
Filename: "{app}\bin\enjrorun.exe"; Parameters: "service uninstall"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveService"

[UninstallDelete]
; The private Python, packages and uv cache. Models and settings in %USERPROFILE%\.enjro are kept.
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

const
  LegacyName = 'StrixRun';

function LegacyAppDir(): string;
begin
  Result := ExpandConstant('{localappdata}\Programs\') + LegacyName;
end;

{ The AppId's previous folder and Start Menu group are StrixRun's: move those to the new
  names, and keep any other folder the user chose. Runs in silent installs too. }
procedure InitializeWizard();
begin
  if CompareText(RemoveBackslash(WizardForm.DirEdit.Text), LegacyAppDir()) = 0 then
    WizardForm.DirEdit.Text := ExpandConstant('{localappdata}\Programs\EnjroRun');
  if CompareText(WizardForm.GroupEdit.Text, LegacyName) = 0 then
    WizardForm.GroupEdit.Text := 'Enjro Run';
end;

function InstallSucceeded(): Boolean;
begin
  Result := FileExists(ExpandConstant('{app}\bin\enjrorun.exe'));
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

{ Upgrading over a running Enjro Run: its Python files are in use, so uv can't replace
  them and the install is left half-replaced. Stop it (and anything else running from
  the install folder) before touching files. }
procedure StopProcessesUnder(Dir: string);
var Code: Integer; Ps: string;
begin
  Ps := '-NoProfile -NonInteractive -Command "$d = ''' + Dir + '''; ' +
        'Get-Process | Where-Object { $_.Path -and $_.Path.StartsWith($d, [StringComparison]::OrdinalIgnoreCase) } | ' +
        'Stop-Process -Force -ErrorAction SilentlyContinue"';
  Exec('powershell.exe', Ps, '', SW_HIDE, ewWaitUntilTerminated, Code);
  Log(Format('closed leftover processes under %s (powershell exit %d)', [Dir, Code]));
end;

{ A StrixRun install (the name before Enjro Run): stop it, and remove its program
  folder, Start Menu group and PATH entry. Its data folder and sign-in task are moved
  over by `enjrorun migrate` after installing. }
procedure RemoveLegacyInstall();
var Code: Integer; Dir, Exe: string;
begin
  Dir := LegacyAppDir();
  if (not DirExists(Dir)) or (CompareText(Dir, ExpandConstant('{app}')) = 0) then exit;
  Log('Found a StrixRun install at ' + Dir + '; replacing it');
  Exe := Dir + '\bin\strixrun.exe';
  if FileExists(Exe) then
  begin
    Exec(Exe, 'stop', '', SW_HIDE, ewWaitUntilTerminated, Code);
    Log(Format('strixrun stop exited with %d', [Code]));
  end;
  StopProcessesUnder(Dir);
  { llama-server runs from the data folder, which can't be moved while it runs. }
  StopProcessesUnder(ExpandConstant('{%USERPROFILE}\.strix\run\bin'));
  RemoveFromPath(Dir + '\bin');
  if not DelTree(Dir, True, True, True) then
    Log('Could not fully remove ' + Dir);
  DelTree(ExpandConstant('{userprograms}\') + LegacyName, True, True, True);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var Code: Integer; Exe: string;
begin
  Result := '';
  RemoveLegacyInstall();
  Exe := ExpandConstant('{app}\bin\enjrorun.exe');
  if FileExists(Exe) then
  begin
    Log('Stopping a running Enjro Run before upgrading');
    Exec(Exe, 'stop', '', SW_HIDE, ewWaitUntilTerminated, Code);
    Log(Format('enjrorun stop exited with %d', [Code]));
  end;
  StopProcessesUnder(ExpandConstant('{app}'));
end;

procedure CurStepChanged(CurStep: TSetupStep);
var Code: Integer;
begin
  if CurStep <> ssPostInstall then exit;
  WizardForm.StatusLabel.Caption := 'Installing Enjro Run (downloads Python and packages, about a minute)...';
  WizardForm.ProgressGauge.Style := npbstMarquee;
  Code := RunUvInstall();
  if not InstallSucceeded() then
  begin
    Log('First attempt failed; retrying once.');
    WizardForm.StatusLabel.Caption := 'Retrying the Enjro Run install...';
    Code := RunUvInstall();
  end;
  WizardForm.ProgressGauge.Style := npbstNormal;
  if not InstallSucceeded() then
  begin
    MsgBox('Enjro Run could not finish installing (uv exited with ' + IntToStr(Code) + ').' + #13#10#13#10 +
           LogTail(12) + #13#10 +
           'The full log is at:' + #13#10 + UvLogPath() + #13#10#13#10 +
           'It needs an internet connection the first time. If antivirus software blocked uv.exe or ' +
           'enjrorun.exe, allow them and run the installer again.', mbError, MB_OK);
    exit;
  end;
  { Move StrixRun's data folder and sign-in task over (does nothing on a fresh install). }
  WizardForm.StatusLabel.Caption := 'Moving StrixRun models and settings over...';
  Exec(ExpandConstant('{cmd}'), '/s /c ""' + ExpandConstant('{app}\bin\enjrorun.exe') + '" migrate >> "' +
       ExpandConstant('{app}\migrate.log') + '" 2>&1"', ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, Code);
  Log(Format('enjrorun migrate exited with %d', [Code]));
  if WizardIsTaskSelected('startup') then
  begin
    WizardForm.StatusLabel.Caption := 'Setting Enjro Run to start when you sign in...';
    if not Exec(ExpandConstant('{app}\bin\enjrorun.exe'), 'service install', ExpandConstant('{app}'),
                SW_HIDE, ewWaitUntilTerminated, Code) or (Code <> 0) then
      MsgBox('Enjro Run is installed, but it could not be set to start when you sign in (exit ' +
             IntToStr(Code) + '). You can run "enjrorun service install" later.', mbInformation, MB_OK);
  end;
  if WizardIsTaskSelected('addtopath') then
    AddToPath(ExpandConstant('{app}\bin'));
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    RemoveFromPath(ExpandConstant('{app}\bin'));
end;
