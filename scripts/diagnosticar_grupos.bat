@echo off
:: SistemAlf -- Que son los grupos y las franjas dentro de un lector
::
:: SOLO LECTURA. Le pregunta al equipo que franjas horarias tiene definidas y
:: que franjas usa cada grupo. Son comandos de lectura y no modifican nada.
::
:: Para que: veniamos copiando el grupo de cada usuario sin saber que significa,
:: y en dos puertas la gente esta repartida entre el 0 y el 1. Elegir uno ahi es
:: decidir sobre algo que nadie entiende.
::
:: Es la primera vez que le mandamos estos comandos a estos equipos, asi que
:: conviene correrlo cuando el local este tranquilo.
::
:: Uso:
::    scripts\diagnosticar_grupos.bat 192.168.1.203

setlocal
cd /d "%~dp0.."

if "%~1"=="" goto :uso

python scripts\diagnosticar_grupos.py %*

echo.
pause
exit /b

:uso
echo.
echo   Falta la IP del lector.
echo.
echo       scripts\diagnosticar_grupos.bat 192.168.1.203
echo.
echo   Conviene empezar por la .203, que es una de las que tiene la gente
echo   repartida entre dos grupos.
echo.
pause
exit /b 1
