@echo off
:: Actualiza SistemaLF a la ultima version del repositorio.
:: Ejecutar como Administrador.

echo.
echo === Actualizando SistemaLF ===
echo.

cd C:\SistemaLF
git pull

echo.
echo Reiniciando servicio...
C:\SistemaLF\tools\nssm.exe restart SistemaLF

echo.
echo Actualizacion completada.
echo.
pause
