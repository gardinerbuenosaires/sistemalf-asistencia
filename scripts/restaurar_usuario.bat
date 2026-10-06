@echo off
:: SistemAlf -- Volver a poner en una puerta a alguien que se borro
::
:: ESCRIBE EN LA PUERTA. Usa el respaldo que dejo el borrado: recrea al usuario
:: con su nombre, su grupo y sus huellas completas.
::
:: Es la vuelta atras de probar_borrado.

setlocal
cd /d "%~dp0.."

if "%~2"=="" goto :uso

python scripts\probar_escritura.py restaurar %*

echo.
pause
exit /b

:uso
echo.
echo   Faltan datos.
echo.
echo       scripts\restaurar_usuario.bat IP NUMERO
echo.
echo   Por ejemplo:
echo       scripts\restaurar_usuario.bat 192.168.1.205 999
echo.
pause
exit /b 1
