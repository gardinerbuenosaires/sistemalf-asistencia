@echo off
:: SistemAlf -- Guardar y restaurar la configuracion de accesos
::
:: Guarda los equipos (IP, puerto, clave, para que se usa cada uno) y los
:: perfiles con sus puertas. Sirve para no volver a cargar los seis lectores a
:: mano cada vez que se reemplaza una base, y para llevar lo configurado en
:: pruebas a produccion.
::
:: NO guarda el perfil de cada empleado ni las excepciones: eso depende de que
:: empleados tenga la base de destino y copiarlo sin mirar seria arrastrar
:: decisiones sobre personas concretas.
::
:: OJO: el archivo que genera tiene las IP y las claves de los lectores. No lo
:: subas al repo ni lo mandes por mail.
::
:: Uso:
::    scripts\config_accesos.bat exportar
::    scripts\config_accesos.bat exportar --salida C:\ruta\equipos.json
::    scripts\config_accesos.bat importar C:\ruta\equipos.json
::
:: Al importar en produccion hay que agregar --si-es-produccion. Es a proposito:
:: mientras se prueba contra los equipos, produccion queda afuera por accidente
:: imposible y no por cuidado.

setlocal
cd /d "%~dp0.."

if "%~1"=="" goto :uso

python scripts\config_accesos.py %*

echo.
pause
exit /b

:uso
echo.
echo   Falta decir que hacer.
echo.
echo       scripts\config_accesos.bat exportar
echo       scripts\config_accesos.bat importar archivo.json
echo.
pause
exit /b 1
