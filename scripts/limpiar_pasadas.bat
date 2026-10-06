@echo off
:: SistemAlf -- Borrar las pasadas guardadas en una puerta
::
:: BORRA EN EL EQUIPO y no se puede deshacer.
::
:: Para un lector que estuvo con el reloj roto: sus pasadas quedaron fechadas en
:: fechas imposibles y no hay forma de arreglarlas. Con el reloj ya en hora,
:: empezar de cero deja un registro que sirve.
::
:: NO toca usuarios ni huellas, y lo verifica releyendo despues.
:: Se niega contra el equipo de asistencia: sus registros son los fichajes.

setlocal
cd /d "%~dp0.."
if "%~1"=="" goto :uso
python scripts\probar_escritura.py limpiar %*
echo.
pause
exit /b

:uso
echo.
echo   Falta la IP de la puerta.
echo.
echo       scripts\limpiar_pasadas.bat 192.168.1.203
echo.
echo   Antes conviene ponerle la hora: Configuracion, Dispositivos, Relojes.
echo   Si no, lo que registre despues va a quedar mal fechado igual.
echo.
pause
exit /b 1
