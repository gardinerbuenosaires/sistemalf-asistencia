@echo off
:: SistemAlf -- Prueba de escritura: alta de un usuario en una puerta
::
:: ESTE SCRIPT LE ESCRIBE A UN EQUIPO. Es el primero que lo hace.
:: Crea un usuario descartable en UNA puerta, sin huella, y despues vuelve a
:: leer el equipo para verificar que los demas quedaron intactos.
::
:: Se niega a correr contra el equipo de asistencia.

setlocal
cd /d "%~dp0.."

if "%~2"=="" goto :uso

python scripts\probar_escritura.py alta %*

echo.
pause
exit /b

:uso
echo.
echo   Faltan datos.
echo.
echo       scripts\probar_alta.bat IP NUMERO
echo.
echo   Por ejemplo, para crear el 9999 en la puerta .205:
echo       scripts\probar_alta.bat 192.168.1.205 9999
echo.
echo   Opcional:
echo       --grupo N   en que grupo crearlo. Por defecto, el mas frecuente
echo                   entre los que ya estan en ese equipo.
echo.
echo   Antes de correrlo tenes que tener el backup de esa puerta.
echo.
pause
exit /b 1
