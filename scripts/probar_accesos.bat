@echo off
:: SistemAlf -- Servidor de pruebas del modulo de accesos
::
:: Existe porque el .ps1 no se puede ejecutar desde cmd: ahi un .ps1 se abre
:: como archivo de texto en vez de correr. Esto lo llama como corresponde.
::
:: NO toca produccion: otro puerto, otra base, y el scheduler apagado, asi que
:: esta instancia no sincroniza sola ni le escribe nada a los lectores.
::
:: Uso:  scripts\probar_accesos.bat            (puerto 8001)
::       scripts\probar_accesos.bat -Puerto 8002

powershell -ExecutionPolicy Bypass -File "%~dp0probar_accesos.ps1" %*

:: Si el script freno por algo (base vacia, ruta mal), la ventana no se cierra
:: sola: sin esto el mensaje se pierde y no se entiende que paso.
if %errorlevel% neq 0 (
    echo.
    echo El servidor de pruebas no arranco. El motivo esta arriba.
    pause
)
