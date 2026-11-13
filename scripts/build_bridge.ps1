param(
    [string]$Python = "py -3.12"
)
$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot ".."))
Set-Location $repo

function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments
    )
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Proceso falló ($LASTEXITCODE): $FilePath $($Arguments -join ' ')"
    }
}

# Entorno: venv, dependencias y PyInstaller.
Invoke-Native cmd.exe /c "$Python -m venv .bridge-venv"
$venvPython = Join-Path $repo ".bridge-venv\Scripts\python.exe"
Invoke-Native $venvPython -c "import sys; assert sys.version_info[:2] == (3, 12), f'Requiere Python 3.12, se encontró {sys.version}'"
Invoke-Native $venvPython -m pip install --upgrade pip
Invoke-Native $venvPython -m pip install -r backend\requirements.txt pyinstaller pywin32 comtypes
Invoke-Native $venvPython -m pip install -r backend\requirements-integrados.txt
# ifcopenshell: solo para la ruta /v1/sap/actions/cad/ifc del puente (docs/CAD_IFC.md).
Invoke-Native $venvPython -m pip install -r backend\requirements-puente-ifc.txt

# Cada símbolo win32* que usa la bandeja debe existir en pywin32 (si no, solo fallaba al abrir el .exe).
Invoke-Native $venvPython -m pip install pytest
Invoke-Native $venvPython -m pytest -q tests\test_tray_win32_api.py

Remove-Item -Recurse -Force -ErrorAction SilentlyContinue portable_dist, portable_build, bridge_only_dist, SAP2000Bridge.zip, SHA256SUMS.txt

# El puente lleva los generadores de informes (solo formato Word) y /v1/documentos/sync funciona en local.
# Los generadores se descubren con importlib y PyInstaller no los ve solo: hay que listarlos todos como
# hidden-imports (lista generada por scripts/exportar_puente.py del Workbench a partir de
# backend/report_builder/generators/*.py, sin base, formato ni __init__). Un generador que falte aquí
# solo fallaría al abrir el .exe, así que no se mantiene a mano. Los esquemas JSON y el catálogo van como datos.
$dataArgs = @(
    "--add-data", "backend\report_builder\generators\schemas;backend\report_builder\generators\schemas",
    "--add-data", "backend\report_builder\generators\catalogo.json;backend\report_builder\generators",
    "--collect-data", "docx",
    "--collect-data", "ezdxf",
    "--collect-all", "ifcopenshell"
)
$hiddenImports = @(
    "--hidden-import", "bridge.sap2000_agent",
    "--hidden-import", "bridge.tray",
    "--hidden-import", "bridge.pairing",
    "--hidden-import", "bridge.alcances",
    "--hidden-import", "bridge.com_orden",
    "--hidden-import", "bridge.com_tiempo",
    "--hidden-import", "bridge.graph",
    "--hidden-import", "bridge.viewer.routes",
    "--hidden-import", "bridge.documentos_locales",
    "--hidden-import", "bridge.documentos_fragmentos",
    "--hidden-import", "backend.linked_documents",
    "--hidden-import", "backend.report_builder.service",
    "--hidden-import", "backend.report_builder.carpeta_reporte",
    "--hidden-import", "backend.report_builder.modulos_exportacion",
    "--hidden-import", "backend.report_builder.manifest_ops",
    "--hidden-import", "backend.report_builder.engine.fragmento",
    "--hidden-import", "backend.report_builder.engine.generator_registry",
    "--hidden-import", "backend.report_builder.generators.base",
    "--hidden-import", "backend.report_builder.generators.formato",
    "--hidden-import", "comtypes",
    "--hidden-import", "comtypes.client",
    "--hidden-import", "pythoncom",
    "--hidden-import", "pywintypes",
    "--hidden-import", "win32com.client",
    "--hidden-import", "win32gui",
    "--hidden-import", "win32con",
    "--hidden-import", "win32api"
)

# Generadores de informes (un módulo por archivo de backend/report_builder/generators).
$generadoresInforme = @(
    "acero_aisc360",
    "bombas",
    "combinaciones",
    "diagrama_interaccion",
    "equilibrio_plastico",
    "esbelteces",
    "espectro",
    "fundaciones",
    "hormigon_simple",
    "nomogramas",
    "placas_base",
    "sulzberger",
    "zapata_biaxial"
)
foreach ($generador in $generadoresInforme) {
    $hiddenImports += @("--hidden-import", "backend.report_builder.generators.$generador")
}

# Orígenes de las webs alojadas: el repositorio no los lleva. Si el secreto ORIGENES_ALOJADOS (JSON, ver
# bridge/hosted.py) existe, se escribe junto al código y se incorpora al ejecutable.
$origenes = Join-Path $repo "bridge\origenes_alojados.json"
if ($env:ORIGENES_ALOJADOS) {
    Set-Content -Path $origenes -Value $env:ORIGENES_ALOJADOS -Encoding UTF8
}
if (Test-Path $origenes) {
    $dataArgs += @("--add-data", "bridge\origenes_alojados.json;bridge")
} else {
    Write-Warning "Sin ORIGENES_ALOJADOS: el puente solo aceptará los orígenes de origins.json y SAP2000_AGENT_ORIGINS."
}

$icon = Join-Path $repo "assets\sap2000_bridge.ico"
$iconArgs = @()
if (Test-Path $icon) {
    $iconArgs = @("--icon", $icon, "--add-data", "assets\sap2000_bridge.ico;assets")
} else {
    Write-Warning "No se encontró ${icon}: se compila sin icono propio."
}

Invoke-Native $venvPython -m PyInstaller --noconfirm --clean --onefile --paths . --windowed --name SAP2000Bridge --distpath portable_dist --workpath portable_build @dataArgs @hiddenImports @iconArgs run_sap_bridge.py

$exe = Join-Path $repo "portable_dist\SAP2000Bridge.exe"
if (-not (Test-Path $exe -PathType Leaf)) { throw "No se generó $exe (se esperaba un único archivo --onefile)." }

New-Item -ItemType Directory -Force bridge_only_dist | Out-Null
Copy-Item -Force $exe (Join-Path "bridge_only_dist" "SAP2000Bridge.exe")
Set-Content -Path (Join-Path "bridge_only_dist" "README.txt") -Encoding UTF8 -Value @"
SAP2000 Bridge portable

Ejecute SAP2000Bridge.exe (un único archivo). Aparece un icono en la bandeja del sistema. El primer arranque
puede tardar unos segundos. Se conecta con el Workbench mediante emparejamiento con un clic desde ese icono.

Windows puede mostrar una advertencia de SmartScreen porque el ejecutable no está firmado: «Más información»
y «Ejecutar de todas formas».

Los registros se guardan en %LOCALAPPDATA%\EngineeringWorkbench\logs. Para salir, clic derecho en el icono y «Salir».
"@
Compress-Archive -Path (Join-Path "bridge_only_dist" "*") -DestinationPath SAP2000Bridge.zip -Force
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue bridge_only_dist

$hash = (Get-FileHash -Path SAP2000Bridge.zip -Algorithm SHA256).Hash.ToLower()
Set-Content -Path SHA256SUMS.txt -Value "$hash  SAP2000Bridge.zip" -Encoding ASCII
Write-Host "Creado SAP2000Bridge.zip"
