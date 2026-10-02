@echo off
:: SistemAlf -- Borrar de una puerta los que no existen en el sistema
::
:: BORRA EN EL EQUIPO. Guarda el registro completo de cada uno antes de tocarlo,
:: con sus huellas: son los unicos cuya huella puede no estar en ningun otro
:: lado, porque no tienen legajo.
::
:: Se niega contra el equipo de asistencia, y verifica despues que los demas
:: usuarios y sus huellas siguen enteros.

setlocal
cd /d "%~dp0.."
if "%~1"=="" goto :uso
python scripts\probar_escritura.py desconocidos %*
echo.
pause
exit /b

:uso
echo.
echo   Falta la IP de la puerta.
echo.
echo       scripts\borrar_desconocidos.bat 192.168.1.202
echo.
echo   Mira antes quienes son: Configuracion, Dispositivos, Cargados.
echo   Los que dicen "No esta en el sistema" son los que esto borra.
echo.
pause
exit /b 1
