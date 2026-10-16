@echo off
:: SistemAlf -- Guardar la configuracion de accesos en un archivo
::
:: SOLO LECTURA de la base. Guarda los equipos (IP, puerto, clave, para que se
:: usa cada uno) y los perfiles con sus puertas, en un archivo JSON.
::
:: Para que: esa configuracion se pierde cada vez que se reemplaza la base de
:: pruebas por una copia de produccion, y hay que volver a cargar los seis
:: lectores a mano. Con esto se vuelven a poner con un comando.
::
:: Por defecto usa la base de ESTA instalacion (data\pruebas.db), no la de
:: produccion. Con --base RUTA se le puede indicar otra.
::
:: Uso:
::    scripts\guardar_equipos.bat
::    scripts\guardar_equipos.bat --salida C:\ruta\accesos.json
::
:: Despues se restaura con:  scripts\restaurar_equipos.bat ARCHIVO

setlocal
cd /d "%~dp0.."

:: Encontrar el interprete: en una de las maquinas `python` no esta en el
:: PATH y Windows lo resuelve al alias del Microsoft Store.
call "%~dp0_python.bat"
if not defined PY exit /b 1

%PY% scripts\config_accesos.py exportar %*

echo.
pause
