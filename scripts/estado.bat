@echo off
:: SistemAlf -- Que hay corriendo en esta maquina
::
:: Produccion corre como servicio de Windows en el 8000 y no se ve en ninguna
:: ventana. El servidor de pruebas se levanta a mano y usa otro puerto. Esto
:: muestra los dos, para saber que se puede parar y que no.
::
:: No cambia nada: solo mira.

powershell -ExecutionPolicy Bypass -Command ^
  "Write-Host ''; Write-Host '  Estado de SistemAlf' -ForegroundColor Cyan; Write-Host '  -------------------' -ForegroundColor Cyan; Write-Host '';" ^
  "$s = Get-Service SistemAlf -ErrorAction SilentlyContinue;" ^
  "if ($s) { $c = if ($s.Status -eq 'Running') {'Green'} else {'Red'};" ^
  "  Write-Host ('  PRODUCCION (servicio SistemAlf): ' + $s.Status) -ForegroundColor $c;" ^
  "  Write-Host '    No lo pares salvo que sepas por que: el restaurante deja de fichar.' -ForegroundColor Gray }" ^
  "else { Write-Host '  PRODUCCION: el servicio SistemAlf no esta instalado en esta maquina' -ForegroundColor Yellow };" ^
  "Write-Host '';" ^
  "$p = Get-NetTCPConnection -LocalPort 8000,8001,8002 -State Listen -ErrorAction SilentlyContinue;" ^
  "if ($p) { Write-Host '  Puertos escuchando:' -ForegroundColor Cyan;" ^
  "  foreach ($x in $p) { $pr = Get-Process -Id $x.OwningProcess -ErrorAction SilentlyContinue;" ^
  "    Write-Host ('    ' + $x.LocalPort + '  ' + $pr.ProcessName + '  (PID ' + $x.OwningProcess + ')') } }" ^
  "else { Write-Host '  No hay nada escuchando en 8000, 8001 ni 8002' -ForegroundColor Yellow };" ^
  "Write-Host '';" ^
  "$t = Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*uvicorn*' -and $_.CommandLine -notlike '*8000*' };" ^
  "if ($t) { Write-Host '  Servidores de prueba levantados a mano:' -ForegroundColor Cyan;" ^
  "  foreach ($x in $t) { Write-Host ('    PID ' + $x.ProcessId) -ForegroundColor Gray };" ^
  "  Write-Host '    Estos si se pueden parar: Ctrl+C en su ventana, o scripts\parar_pruebas.bat' -ForegroundColor Gray }" ^
  "else { Write-Host '  No hay ningun servidor de prueba corriendo' -ForegroundColor Gray };" ^
  "Write-Host ''"

pause
