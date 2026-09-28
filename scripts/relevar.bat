@echo off
:: SistemAlf -- Relevamiento y backup de los lectores
::
:: Envoltorio de relevar_lectores.py, que es SOLO LECTURA: no le escribe nada a
:: ningun equipo. Baja el inventario de cada lector y, salvo que se pida lo
:: contrario, tambien las huellas: ese backup es lo unico que hace reversible
:: un borrado mas adelante.
::
:: Los archivos quedan al lado de la base de produccion, en
::   C:\ProgramData\SistemAlf\relevamiento\<fecha-hora>\
:: y CONTIENEN HUELLAS Y NUMEROS DE LEGAJO. No salen de esta PC.

setlocal
cd /d "%~dp0.."

if "%~1"=="" goto :uso

echo.
echo   Relevando: %*
echo   Solo lectura: a los equipos no se les escribe nada.
echo.

python scripts\relevar_lectores.py %*

echo.
pause
exit /b

:uso
echo.
echo   Falta indicar los lectores.
echo.
echo   Primero, para ver cuales contestan (tarda segundos):
echo       scripts\relevar.bat 192.168.1.202 192.168.1.203 --solo-conexion
echo.
echo   Despues, el relevamiento completo con backup de huellas:
echo       scripts\relevar.bat 192.168.1.202 192.168.1.203 192.168.1.204
echo.
echo   Opciones utiles:
echo       --solo-conexion   solo ver quien responde, sin bajar nada
echo       --sin-backup      inventario sin huellas
echo       --maestro IP      si el lector de fichaje no es el del sistema
echo       IP@clave          si ese equipo tiene clave de comunicacion
echo.
pause
exit /b 1
