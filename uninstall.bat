@echo off
rem removes the panel link only, python packages stay installed
set "extDir=%APPDATA%\Adobe\CEP\extensions"
if exist "%extDir%\chopshopMCP" rmdir "%extDir%\chopshopMCP"
echo panel removed. delete %APPDATA%\chopshopMCP to clear the transcript cache and settings.
echo the chrome that renders motion graphics sits in %USERPROFILE%\.cache\hyperframes, delete it too if nothing else uses it.
pause
