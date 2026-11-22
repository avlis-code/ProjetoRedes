@echo off
rem Uso: scripts\host.bat IP_DO_LOBBY [nome] [porta]   (no terminal: iniciar ou cancelar)
cd /d "%~dp0.."
if "%1"=="" (echo Uso: %0 IP_DO_LOBBY [nome] [porta] & exit /b 1)
set NOME=%2
if "%NOME%"=="" set NOME=Ana
set PORTA=%3
if "%PORTA%"=="" set PORTA=6000
python rede.py testar %1 5000 --vezes 3 || exit /b 1
python host.py --nome %NOME% --porta %PORTA% --lobby-ip %1 --mostrar --log host_%NOME%.log -v
