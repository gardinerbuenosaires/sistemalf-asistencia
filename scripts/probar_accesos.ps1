<#
.SYNOPSIS
    Levanta el sistema en modo prueba, en otro puerto y sobre una copia de la base.

.DESCRIPTION
    Para probar la rama de control de accesos en la mini PC sin tocar produccion.

    Existe por un motivo concreto: si DB_PATH no esta definida, config.py cae en
    "data/fichajes.db" relativo a la carpeta donde uno este parado. Esa ruta no
    existe en una copia de trabajo, sqlite la crea vacia sin decir nada, y el
    sistema arranca como una instalacion nueva. Este script fija la ruta y la
    verifica ANTES de arrancar, asi ese error no puede pasar.

    Produccion no se toca: otro puerto, otra base, y contra los lectores todo es
    solo lectura.

.EXAMPLE
    .\scripts\probar_accesos.ps1
    .\scripts\probar_accesos.ps1 -Puerto 8002
#>
param(
    [string]$Base   = "C:\ProgramData\SistemAlf\pruebas-accesos.db",
    [string]$Origen = "C:\ProgramData\SistemAlf\fichajes.db",
    [int]   $Puerto = 8001
)

$ErrorActionPreference = "Stop"

function Escribir($texto, $color = "Gray") { Write-Host $texto -ForegroundColor $color }

Escribir ""
Escribir "  Sistema de pruebas - control de accesos" "Cyan"
Escribir "  ---------------------------------------" "Cyan"

# La copia: si no esta, se ofrece hacerla desde produccion.
if (-not (Test-Path $Base)) {
    Escribir ""
    Escribir "  No existe la base de pruebas:" "Yellow"
    Escribir "    $Base"
    if (-not (Test-Path $Origen)) {
        Escribir ""
        Escribir "  Y tampoco encuentro la de produccion en $Origen" "Red"
        Escribir "  Pasa la ruta correcta con -Origen" "Red"
        exit 1
    }
    $tam = [math]::Round((Get-Item $Origen).Length / 1MB, 1)
    Escribir ""
    Escribir "  Se puede copiar desde produccion ($tam MB):" "Yellow"
    Escribir "    $Origen"
    $r = Read-Host "  Copiar ahora? (s/n)"
    if ($r -ne "s") { Escribir "  Cancelado." "Yellow"; exit 1 }
    Copy-Item $Origen $Base
    Escribir "  Copiada." "Green"
}

# Una base vacia pesa unos pocos KB. Con cientos de empleados y sus fichajes no
# hay forma de que baje de 1 MB, asi que es una senial clara de que se copio mal
# o de que en algun momento se arranco sobre una base recien creada.
$mb = [math]::Round((Get-Item $Base).Length / 1MB, 1)
if ($mb -lt 1) {
    Escribir ""
    Escribir "  La base de pruebas pesa $mb MB: esta vacia." "Red"
    Escribir "  Seguramente se creo sola por una ruta mal apuntada." "Red"
    Escribir ""
    Escribir "  Borrala y volve a correr esto:" "Yellow"
    Escribir "    del `"$Base`""
    exit 1
}

# Cuantos empleados tiene: es la confirmacion de que es la base de verdad.
$py = @"
import sqlite3
c = sqlite3.connect(r'$Base')
print(c.execute('SELECT COUNT(*) FROM empleados').fetchone()[0])
"@
try { $empleados = (python -c $py).Trim() } catch { $empleados = "?" }

Escribir ""
Escribir "  Base    : $Base  ($mb MB, $empleados empleados)" "Green"
Escribir "  Puerto  : $Puerto" "Green"
Escribir "  Carpeta : $(Get-Location)" "Green"
Escribir ""
Escribir "  Entra a  http://127.0.0.1:$Puerto" "Cyan"
Escribir "  Ctrl+C para cortar. Produccion no se toca." "Gray"
Escribir ""

$env:DB_PATH = $Base
if (-not $env:SECRET_KEY) { $env:SECRET_KEY = "pruebas-accesos-local" }
python -m uvicorn main:app --host 127.0.0.1 --port $Puerto
