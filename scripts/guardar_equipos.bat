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
:: Uso:
::    scripts\guardar_equipos.bat
::    scripts\guardar_equipos.bat --salida C:\ruta\accesos.json
::
:: Despues se restaura con:  scripts\restaurar_equipos.bat ARCHIVO

setlocal
cd /d "%~dp0.."

python scripts\config_accesos.py exportar %*

echo.
pause
