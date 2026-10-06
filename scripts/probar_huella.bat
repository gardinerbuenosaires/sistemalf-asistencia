@echo off
:: SistemAlf -- Prueba de escritura: copiar una huella a una puerta
::
:: ESCRIBE EN LA PUERTA. Del equipo de asistencia solo LEE la huella.
::
:: Le copia al usuario de prueba las huellas que una persona tiene enrolada en
:: el .201. Despues hay que ir a esa puerta y apoyar el dedo: eso es lo que
:: prueba que la copia sirve.
::
:: Solo le escribe a un usuario llamado PRUEBA, creado antes con probar_alta.

setlocal
cd /d "%~dp0.."

if "%~3"=="" goto :uso

python scripts\probar_escritura.py huella %*

echo.
pause
exit /b

:uso
echo.
echo   Faltan datos.
echo.
echo       scripts\probar_huella.bat IP NUMERO_PRUEBA NUMERO_TUYO
echo.
echo   Por ejemplo, si creaste el 9990 en la .205 y tu numero en el .201 es 7:
echo       scripts\probar_huella.bat 192.168.1.205 9990 7
echo.
echo   El primer numero es el usuario de prueba que ya esta en la puerta.
echo   El segundo es el numero de la persona cuya huella se copia.
echo.
pause
exit /b 1
