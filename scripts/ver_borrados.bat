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

:: Encontrar el interprete: en una de las maquinas `python` no esta en el
:: PATH y Windows lo resuelve al alias del Microsoft Store.
call "%~dp0_python.bat"
if not defined PY exit /b 1

%PY% scripts\ver_borrados.py %*

echo.
pause
