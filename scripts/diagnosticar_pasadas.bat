@echo off
:: SistemAlf -- Que formato usa un lector para guardar las pasadas
::
:: SOLO LECTURA. Baja el bloque crudo, lo muestra en hexadecimal y prueba cada
:: formato conocido diciendo cuantas fechas creibles produce cada uno.

setlocal
cd /d "%~dp0.."
if "%~1"=="" goto :uso
python scripts\diagnosticar_pasadas.py %*
echo.
pause
exit /b

:uso
echo.
echo   Falta la IP del lector.
echo.
echo       scripts\diagnosticar_pasadas.bat 192.168.1.209
echo.
pause
exit /b 1
