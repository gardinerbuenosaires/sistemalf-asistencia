@echo off
:: SistemAlf -- Que hay en la carpeta de respaldos de borrado
::
:: SOLO LECTURA. Mira adentro de cada archivo y muestra de quien era, de que
:: equipo y cuando.
::
:: Buscar por nombre de archivo engania: el archivo se llama con el numero tal
:: como lo tenia el equipo, que no siempre es el del legajo.
::
:: Uso:
::    scripts\ver_borrados.bat          todos, del mas nuevo al mas viejo
::    scripts\ver_borrados.bat 57       solo los que mencionen ese numero
::    scripts\ver_borrados.bat lavega   o ese texto

setlocal
cd /d "%~dp0.."

python scripts\ver_borrados.py %*

echo.
pause
