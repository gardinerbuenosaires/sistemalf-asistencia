@echo off
:: SistemAlf -- Prueba de escritura: borrar el usuario de prueba
::
:: BORRA EN LA PUERTA, y el borrado no se deshace en ese equipo.
::
:: Solo borra un usuario llamado PRUEBA. Para sacar gente de verdad va a haber
:: otra herramienta, con backup obligatorio.
::
:: Despues de borrar vuelve a leer y verifica que los demas sigan enteros CON
:: SUS HUELLAS: los lectores viejos reordenan indices internos al borrar, y un
:: borrado mal hecho se nota recien cuando alguien no puede entrar.

setlocal
cd /d "%~dp0.."

if "%~2"=="" goto :uso

python scripts\probar_escritura.py borrado %*

echo.
pause
exit /b

:uso
echo.
echo   Faltan datos.
echo.
echo       scripts\probar_borrado.bat IP NUMERO
echo.
echo   Por ejemplo:
echo       scripts\probar_borrado.bat 192.168.1.205 9990
echo.
pause
exit /b 1
