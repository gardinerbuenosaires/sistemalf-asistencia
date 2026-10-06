@echo off
:: SistemAlf -- Detener los servidores de prueba
::
:: Solo los levantados a mano. Al servicio de produccion del puerto 8000 no lo
:: toca: si se para, el restaurante deja de registrar fichajes.

powershell -ExecutionPolicy Bypass -Command ^
  "$t = Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*uvicorn*' -and $_.CommandLine -notlike '*8000*' };" ^
  "if (-not $t) { Write-Host ''; Write-Host '  No hay ningun servidor de prueba corriendo.' -ForegroundColor Yellow }" ^
  "else { Write-Host ''; foreach ($x in $t) { Stop-Process -Id $x.ProcessId -Force;" ^
  "  Write-Host ('  Detenido PID ' + $x.ProcessId) -ForegroundColor Green } };" ^
  "$s = Get-Service SistemAlf -ErrorAction SilentlyContinue;" ^
  "if ($s) { Write-Host ''; Write-Host ('  Produccion sigue: ' + $s.Status) -ForegroundColor Cyan };" ^
  "Write-Host ''"

pause
