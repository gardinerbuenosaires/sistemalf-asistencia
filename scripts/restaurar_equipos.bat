@echo off
:: SistemAlf -- Volver a poner la configuracion de accesos desde un archivo
::
:: Escribe en la BASE, no en los equipos. Agrega los lectores y los perfiles que
:: falten; lo que ya existe se deja como esta y se informa.
::
:: Los equipos se reconocen por IP y los perfiles por nombre, asi que correrlo
:: dos veces no duplica nada.
::
:: Por defecto escribe en la base de ESTA instalacion. Con --base RUTA, en otra.
::
:: Uso:
::    scripts\restaurar_equipos.bat accesos-20261002-194400.json
::
:: La base tiene que haber arrancado al menos una vez con la version nueva, que
:: es cuando se crean las tablas.

setlocal
cd /d "%~dp0.."

if "%~1"=="" goto :uso

python scripts\config_accesos.py importar %*

echo.
pause
exit /b

:uso
echo.
echo   Falta el archivo.
echo.
echo       scripts\restaurar_equipos.bat accesos-20261002-194400.json
echo.
echo   Lo genera scripts\guardar_equipos.bat
echo.
pause
exit /b 1
