@echo off
echo Actualizando SistemaLF...
git -C C:\SistemaLF pull
echo Reiniciando servicio...
C:\SistemaLF\nssm.exe restart SistemaLF
echo Listo.
pause
