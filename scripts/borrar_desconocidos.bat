@echo off
:: SistemAlf -- Borrar de las puertas a los que no existen en el sistema
::
:: BORRA EN EL EQUIPO. Guarda el registro completo de cada uno antes de tocarlo,
:: con sus huellas: son los unicos cuya huella puede no estar en ningun otro
:: lado, porque no tienen legajo.
::
:: Sin argumentos revisa TODAS las puertas. La lista sale de la union de los
:: equipos y no del maestro: un egresado al que le dieron la baja desaparecio
:: del maestro y quedo en las puertas, que es el caso mas comun.
::
:: El equipo de fichaje no se toca salvo que se pida: sacar a alguien de ahi le
:: quita la posibilidad de fichar, que es mas grave que perder una puerta.
::
:: Verifica despues que los demas usuarios y sus huellas siguen enteros.
::
:: Uso:
::    scripts\borrar_desconocidos.bat                     todas las puertas
::    scripts\borrar_desconocidos.bat 192.168.1.202       solo ese equipo
::    scripts\borrar_desconocidos.bat --incluir-fichaje   tambien el de fichaje
::
:: Conviene mirar antes quienes son, sin riesgo: Configuracion, Dispositivos,
:: Cargados. Los que dicen "No esta en el sistema" son los que esto borra.

setlocal
cd /d "%~dp0.."

python scripts\probar_escritura.py desconocidos %*

echo.
pause
