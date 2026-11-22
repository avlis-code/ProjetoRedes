@echo off
rem Uso: scripts\jogador.bat IP_DO_LOBBY NOME [opcoes extras do cliente_teste.py]
cd /d "%~dp0.."
if "%2"=="" (echo Uso: %0 IP_DO_LOBBY NOME [opcoes] & exit /b 1)
set LOBBY=%1
set NOME=%2
shift
shift
python rede.py testar %LOBBY% 5000 --vezes 3 || exit /b 1
python cliente_teste.py --lobby-ip %LOBBY% --nome %NOME% --mostrar --log jogador_%NOME%.log -v %1 %2 %3 %4
