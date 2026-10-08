@echo off
:: SistemAlf -- Volver a poner a una persona en el equipo de fichaje
::
:: ESCRIBE EN EL .201. Es el unico lugar donde se le escribe a ese equipo, y
:: por eso es un script y no un boton: un boton invita a usarlo, esto hay que
:: ir a buscarlo.
::
:: Para el caso de alguien que se borro de ahi por error y cuya huella no se
:: puede volver a conseguir sin que la persona venga.
::
:: Saca la huella del respaldo que dejo el borrado, y si no hay, de otra puerta
:: donde la persona siga cargada.
::
:: No escribe si la lista vino cortada, ni si esa persona ya esta. Y verifica
:: releyendo: que quedo con todas sus huellas y que los demas quedaron enteros.
::
:: Uso:
::    scripts\reponer_en_fichaje.bat 57
::    scripts\reponer_en_fichaje.bat 57 --desde-puerta 192.168.1.208

setlocal
cd /d "%~dp0.."

if "%~1"=="" goto :uso

python scripts\reponer_en_fichaje.py %*

echo.
pause
exit /b

:uso
echo.
echo   Falta el numero de la persona.
echo.
echo       scripts\reponer_en_fichaje.bat 57
echo.
echo   Si no hay respaldo del borrado, decile de que puerta copiar:
echo       scripts\reponer_en_fichaje.bat 57 --desde-puerta 192.168.1.208
echo.
pause
exit /b 1
