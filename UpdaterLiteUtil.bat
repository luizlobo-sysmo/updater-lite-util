@echo off
REM GUI: atualiza a base contornando o erro 403 do dbchangelog-latest.xsd.
REM Copia o build.zip do share, patcha os changelogs (latest -> versao empacotada),
REM rezipa no pacote do updater e dispara o updater-lite (sem versao = usa o local).
setlocal enabledelayedexpansion
set SCRIPT=%~dp0UpdaterLiteUtil.py

call :find_python
if defined PYW ( start "" "!PYW!" "%SCRIPT%" & goto :end )
if defined PY  ( "!PY!" "%SCRIPT%" & goto :end )

REM Sem Python: mostra mensagem na tela (popup) e encerra.
set "MSG=Python 3 nao encontrado nesta maquina.\n\nInstale o Python 3 de https://python.org e, no instalador, marque:\n  - Add python.exe to PATH\n  - tcl/tk and IDLE (necessario para a interface)\n\nDepois execute o UpdaterLiteUtil.bat novamente."
mshta "javascript:alert('%MSG%');close();" 2>nul
if errorlevel 1 (
    echo(
    echo Python 3 nao encontrado. Instale de https://python.org
    echo marcando "Add to PATH" e "tcl/tk", e rode de novo.
    echo(
    pause
)
exit /b 2

:find_python
set PYW=
set PY=
for %%P in (pythonw.exe) do if not defined PYW set PYW=%%~$PATH:P
for %%P in (python.exe)  do if not defined PY  set PY=%%~$PATH:P
if not defined PY (
    for %%D in ("%LocalAppData%\Programs\Python\Python312" "%LocalAppData%\Programs\Python\Python311" "C:\Python312" "C:\Python311") do (
        if exist "%%~D\python.exe"  set PY=%%~D\python.exe
        if exist "%%~D\pythonw.exe" set PYW=%%~D\pythonw.exe
    )
)
exit /b 0

:end
endlocal
