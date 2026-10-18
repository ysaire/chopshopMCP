@echo off
setlocal
rem chopshopMCP installer, windows + premiere pro 2022 and up

set "repoDir=%~dp0"
set "repoDir=%repoDir:~0,-1%"
set "extDir=%APPDATA%\Adobe\CEP\extensions"
set "checkScript=%repoDir%\checkPython.py"
set "pyExe="

echo [1/7] allowing unsigned cep panels (csxs 11 and 12)
reg add "HKCU\Software\Adobe\CSXS.11" /v PlayerDebugMode /t REG_SZ /d 1 /f >nul
reg add "HKCU\Software\Adobe\CSXS.12" /v PlayerDebugMode /t REG_SZ /d 1 /f >nul

echo [2/7] linking the panel into premiere
if not exist "%extDir%" mkdir "%extDir%"
if exist "%extDir%\chopshopMCP" rmdir "%extDir%\chopshopMCP"
if exist "%extDir%\chopshopMCP" goto :notALink
mklink /J "%extDir%\chopshopMCP" "%repoDir%\panel" >nul || goto :fail

echo [3/7] looking for python 3.10+
for %%c in ("py -3.14" "py -3.13" "py -3.12" "py -3.11" "py -3.10" "py -3" "python" "python3") do (
    if not defined pyExe call :tryPython %%~c
)
if not defined pyExe goto :noPython
echo       using "%pyExe%"

echo [4/7] installing python packages (first time takes a few minutes)
"%pyExe%" -m pip --version >nul 2>nul || "%pyExe%" -m ensurepip --upgrade || goto :fail
"%pyExe%" -m pip install --upgrade -r "%repoDir%\sidecar\requirements.txt" || goto :fail
"%pyExe%" "%checkScript%" --save >nul || goto :fail

echo [5/7] nvidia gpu check for whisper
nvidia-smi >nul 2>nul && (
    echo       nvidia gpu found, installing cuda libs for fast transcription
    "%pyExe%" -m pip install nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*" || echo       gpu libs failed, cpu will be used
) || echo       no nvidia gpu, whisper will run on cpu

echo [6/7] gpu runtime for the subject matting models
rem faster-whisper pulls the cpu onnxruntime: swap it for cuda (nvidia 580+) or directml (any other dx12 gpu)
set "gpuRuntime=onnxruntime-directml"
set /a driverMajor=0
for /f "tokens=1 delims=." %%d in ('nvidia-smi --query-gpu^=driver_version --format^=csv^,noheader 2^>nul') do set /a driverMajor=%%d 2>nul
if %driverMajor% GEQ 580 set "gpuRuntime=onnxruntime-gpu[cuda,cudnn]"
echo       using %gpuRuntime% (nvidia driver %driverMajor%, cuda build needs 580+)
"%pyExe%" -m pip uninstall -y onnxruntime onnxruntime-gpu onnxruntime-directml >nul 2>nul
"%pyExe%" -m pip install "%gpuRuntime%" || (
    echo       gpu runtime failed, subject mattes will run on cpu in fast quality
    "%pyExe%" -m pip install onnxruntime || goto :fail
)
"%pyExe%" "%repoDir%\sidecar\cli.py" check

echo [7/7] motion graphics: hyperframes and its chrome (node 22 or newer)
set "nodeMajor=0"
for /f "tokens=1 delims=v." %%v in ('node --version 2^>nul') do set /a nodeMajor=%%v 2>nul
if %nodeMajor% LSS 22 (
    echo       node 22+ not found, motion graphics stay off: install node from https://nodejs.org then run install.bat again
) else (
    set "HYPERFRAMES_NO_TELEMETRY=1"
    set "DO_NOT_TRACK=1"
    set "HYPERFRAMES_NO_UPDATE_CHECK=1"
    call npm ci --prefix "%repoDir%\motion" --no-fund --no-audit || echo       npm failed, motion graphics stay off
    node "%repoDir%\motion\node_modules\hyperframes\bin\hyperframes.mjs" browser ensure || echo       chrome download failed, the first render will retry
)

echo.
echo done. restart premiere, then window ^> extensions ^> chopshopMCP
echo.
echo to let claude desktop drive premiere, add this under "mcpServers" in claude_desktop_config.json:
echo   "chopshopMCP": { "command": "%pyExe:\=\\%", "args": ["%repoDir:\=\\%\\sidecar\\mcpServer.py"] }
echo.
pause
exit /b 0

:tryPython
rem the microsoft store stub prints nothing, so only a real 3.10+ interpreter sets found
set "found="
for /f "usebackq delims=" %%p in (`%* "%checkScript%" 2^>nul`) do set "found=%%p"
if defined found set "pyExe=%found%"
exit /b 0

:noPython
echo.
echo no usable python 3.10+ found. what windows sees:
echo   where python:
where python 2>nul
echo   python --version:
python --version 2>&1
echo   py launcher:
py -0p 2>nul || echo   (no py launcher)
echo.
echo fix: install python 3.12 or newer from https://www.python.org/downloads/windows/
echo      tick "add python.exe to path" on the first screen, then run install.bat again
echo      the microsoft store "python" shortcut does not count, you can turn it off in
echo      settings ^> apps ^> advanced app settings ^> app execution aliases
goto :fail

:notALink
echo %extDir%\chopshopMCP already exists and is not a link, delete that folder and run again
goto :fail

:fail
echo.
echo install failed
pause
exit /b 1
