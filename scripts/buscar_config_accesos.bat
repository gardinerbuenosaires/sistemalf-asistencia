@echo off
:: SistemAlf -- Buscar el archivo de configuracion de accesos exportado
::
:: El export se llama accesos-AAAAMMDD-HHMMSS.json y por defecto queda en la
:: raiz de la carpeta desde donde se corrio el script. Si no te acordas cual
:: era, esto busca en todo el disco C.
::
:: SOLO LECTURA. No borra ni mueve nada.
::
:: Si no aparece: no hace falta buscarlo. Correr
::    scripts\config_accesos.bat exportar
:: en la instalacion donde estan cargados los equipos genera uno nuevo, y mas
:: fresco es mejor: el viejo puede ser anterior al ultimo cambio de equipos.

setlocal
echo.
echo   Buscando en C:\ ... puede tardar un minuto.
echo.

dir C:\accesos-*.json /s /b 2>nul
dir C:\*.json /s /b 2>nul | findstr /i "accesos equipos dispositivos" | findstr /vi "node_modules AppData \.git Program Files Windows"

echo.
echo   Si no aparecio nada, generalo de nuevo con:
echo       scripts\config_accesos.bat exportar
echo.
pause
