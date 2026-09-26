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

    Produccion no se toca: otro puerto, otra base, y el scheduler apagado, asi
    que esta instancia no sincroniza sola ni le escribe nada a los lectores.

    La copia de prueba queda en data\pruebas.db de esta misma instalacion, no
    al lado de la base de produccion: en carpetas distintas no hay forma de
    confundirlas.

.EXAMPLE
    .\scripts\probar_accesos.ps1
    .\scripts\probar_accesos.ps1 -Puerto 8002
#>
param(
    [string]$Base    = "",
    [string]$Origen  = "C:\ProgramData\SistemAlf\fichajes.db",
    [string]$CodigoProd = "C:\SistemAlf",
    [int]   $Puerto  = 8001,
    [switch]$ConFotos
)

$ErrorActionPreference = "Stop"

# La copia de prueba vive DENTRO de esta instalacion, no al lado de la base de
# produccion. Tenerlas en la misma carpeta es pedir que algun dia alguien se
# equivoque de archivo; separadas, no hay forma. Y de paso esta instalacion
# queda autocontenida: se borra la carpeta y no queda nada dado vuelta.
$Raiz = Split-Path -Parent $PSScriptRoot
if (-not $Base) { $Base = Join-Path $Raiz "data\pruebas.db" }
$carpetaBase = Split-Path -Parent $Base
if (-not (Test-Path $carpetaBase)) {
    New-Item -ItemType Directory -Path $carpetaBase -Force | Out-Null
}

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

# El tropiezo tipico: arrancar uvicorn a mano sin DB_PATH, que cae en
# "data/fichajes.db" relativo a donde uno este parado y sqlite lo crea vacio.
# No se usa para nada, pero se parece al nombre de la base de produccion y
# conviene que no quede dando vueltas.
$intrusa = Join-Path $Raiz "data\fichajes.db"
if ((Test-Path $intrusa) -and ((Get-Item $intrusa).Length / 1MB -lt 1)) {
    Escribir ""
    Escribir "  Ojo: hay una base vacia en" "Yellow"
    Escribir "    $intrusa"
    Escribir "  Se creo sola al arrancar sin DB_PATH. No se usa; conviene borrarla" "Yellow"
    Escribir "  para que nadie la confunda con la de produccion." "Yellow"
}

# La base guarda la RUTA del logo, no la imagen: el archivo vive al lado del
# codigo, en web\static\uploads, que esta en .gitignore y por eso llega vacia.
# Sin esto la pantalla se ve sin logo y parece que algo esta roto.
$logoProd = Join-Path $CodigoProd "web\static\uploads"
$logoAca  = Join-Path $Raiz "web\static\uploads"
if (Test-Path $logoProd) {
    if (-not (Test-Path $logoAca)) { New-Item -ItemType Directory -Path $logoAca -Force | Out-Null }
    foreach ($f in Get-ChildItem $logoProd -File -ErrorAction SilentlyContinue) {
        $destino = Join-Path $logoAca $f.Name
        if (-not (Test-Path $destino)) { Copy-Item $f.FullName $destino }
    }
}

# Las fotos de empleados se buscan en la carpeta de la base, y la de pruebas
# esta en otro lado. No hacen falta para probar accesos, asi que solo se copian
# si se piden: son cientos de archivos y son fotos de gente real.
$fotosProd = Join-Path (Split-Path -Parent $Origen) "fotos"
$fotosAca  = Join-Path $carpetaBase "fotos"
if ($ConFotos -and (Test-Path $fotosProd) -and -not (Test-Path $fotosAca)) {
    Escribir ""
    Escribir "  Copiando las fotos de empleados..." "Gray"
    Copy-Item $fotosProd $fotosAca -Recurse
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
Escribir "  Scheduler: APAGADO - no sincroniza sola ni toca los lectores" "Green"
Escribir ""
Escribir "  Entra a  http://127.0.0.1:$Puerto" "Cyan"
Escribir "  Ctrl+C para cortar. Produccion no se toca." "Gray"
Escribir ""

$env:DB_PATH = $Base
if (-not $env:SECRET_KEY) { $env:SECRET_KEY = "pruebas-accesos-local" }

# Sin esto la instancia de prueba correria su propio scheduler contra los
# lectores de verdad: sincroniza sola, reinicia el equipo a las 4 AM, le cambia
# la hora y los dias 1 y 15 le borra los registros. Produccion ya hace todo
# eso; una copia no tiene por que hacerlo de nuevo. Para el modulo de accesos
# no hace falta: habla con los equipos cuando uno aprieta un boton.
$env:SCHEDULER = "0"
python -m uvicorn main:app --host 127.0.0.1 --port $Puerto
