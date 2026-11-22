@echo off
rem Uso: scripts\lobby.bat [porta]
cd /d "%~dp0.."
set PORTA=%1
if "%PORTA%"=="" set PORTA=5000
python rede.py ip
echo Passe um dos IPs acima para os colegas. Lobby na porta %PORTA%.
python lobby.py --porta %PORTA%
