@echo off
:: SistemAlf -- Traer la base de produccion a la de pruebas, sin las huellas
::
:: Trabajar con datos de verdad es lo que hace que las pruebas sirvan. Pero
:: desde que las huellas se guardan en la base, copiarla tal cual se lleva los
:: datos biometricos de casi doscientas personas a esta maquina.
::
:: Este script copia y vacia las huellas en un solo paso. Si fueran dos
:: comandos, algun dia alguien hace el primero y se va a almorzar.
::
:: Produccion NO se toca: se abre en modo solo lectura.
::
:: Uso:
::    scripts\copiar_base_a_pruebas.bat

setlocal
cd /d "%~dp0.."

:: Encontrar el interprete: en una de las maquinas `python` no esta en el
:: PATH y Windows lo resuelve al alias del Microsoft Store.
call "%~dp0_python.bat"
if not defined PY exit /b 1

%PY% scripts\copiar_base_a_pruebas.py %*

echo.
pause
