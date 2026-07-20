@echo off
REM GUI: atualiza a base contornando o erro 403 do dbchangelog-latest.xsd.
REM Copia o build.zip do share, patcha os changelogs (latest -> versao empacotada),
REM rezipa no pacote do updater e dispara o updater-lite (sem versao = usa o local).
REM Se nao houver Python, baixa e instala automaticamente (com tkinter).
setlocal enabledelayedexpansion
set SCRIPT=%~dp0atualizar_versao_fix_liquibase.py

call :find_python
if defined PYW ( start "" "!PYW!" "%SCRIPT%" & goto :end )
if defined PY  ( "!PY!" "%SCRIPT%" & goto :end )

echo Python 3 nao encontrado. Baixando o instalador oficial...
set PYVER=3.12.4
set PYEXE=%TEMP%\python-%PYVER%-amd64.exe
set PYURL=https://www.python.org/ftp/python/%PYVER%/python-%PYVER%-amd64.exe

powershell -NoProfile -Command "try { [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%PYURL%' -OutFile '%PYEXE%' } catch { exit 1 }"
if not exist "%PYEXE%" (
    echo Falha ao baixar o Python. Instale manualmente de python.org e rode de novo.
    pause
    exit /b 2
)

echo Instalando Python %PYVER% (silencioso, com tkinter)...
"%PYEXE%" /quiet InstallAllUsers=0 PrependPath=1 Include_tcltk=1 Include_pip=1
REM aguarda o instalador terminar
timeout /t 8 /nobreak >nul

call :find_python
if defined PYW ( start "" "!PYW!" "%SCRIPT%" & goto :end )
if defined PY  ( "!PY!" "%SCRIPT%" & goto :end )
echo Python instalado mas nao encontrado no PATH. Abra um novo terminal e rode de novo.
pause
exit /b 3

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
