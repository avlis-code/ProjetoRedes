@echo off
rem Rode como administrador. Libera a entrada TCP do lobby (5000) e do host (6000-6010).
netsh advfirewall firewall add rule name="Tron lobby" dir=in action=allow protocol=TCP localport=5000
netsh advfirewall firewall add rule name="Tron host" dir=in action=allow protocol=TCP localport=6000-6010
echo Para remover depois:
echo   netsh advfirewall firewall delete rule name="Tron lobby"
echo   netsh advfirewall firewall delete rule name="Tron host"
