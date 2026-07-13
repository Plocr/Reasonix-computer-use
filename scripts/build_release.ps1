param(
    [string]$OutputDirectory = "$PSScriptRoot\..\dist",
    [string]$PythonVersion = "3.12.10",
    [switch]$KeepStage
)

$ErrorActionPreference = "Stop"
if ($env:OS -ne "Windows_NT") {
    throw "The self-contained release can only be built on Windows."
}

$root = (Resolve-Path "$PSScriptRoot\..").Path
$manifestPath = Join-Path $root "reasonix-plugin.json"
$manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
$version = [string]$manifest.version
if (-not $version) { throw "reasonix-plugin.json does not declare a version" }

$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
$packageName = "reasonix-computer-use-$version-windows-x64"
$stage = Join-Path $OutputDirectory $packageName
$runtime = Join-Path $stage "runtime"
$cache = Join-Path $OutputDirectory ".release-cache"
$archive = Join-Path $OutputDirectory "$packageName.zip"
$checksum = "$archive.sha256"

New-Item -ItemType Directory -Force -Path $OutputDirectory, $cache | Out-Null
if (Test-Path $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
New-Item -ItemType Directory -Force -Path $stage, $runtime | Out-Null

$pythonZip = Join-Path $cache "python-$PythonVersion-embed-amd64.zip"
if (-not (Test-Path $pythonZip)) {
    $pythonUrl = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"
    Write-Host "Downloading embedded Python $PythonVersion..."
    Invoke-WebRequest $pythonUrl -OutFile $pythonZip
}
Expand-Archive -LiteralPath $pythonZip -DestinationPath $runtime -Force

$pth = Get-ChildItem -LiteralPath $runtime -Filter "python*._pth" | Select-Object -First 1
if (-not $pth) { throw "Embedded Python ._pth file was not found" }
$pthContent = Get-Content -LiteralPath $pth.FullName
$pthContent = $pthContent | ForEach-Object { if ($_ -eq "#import site") { "import site" } else { $_ } }
if ($pthContent -notcontains "Lib\site-packages") { $pthContent += "Lib\site-packages" }
Set-Content -LiteralPath $pth.FullName -Value $pthContent -Encoding ASCII

$python = Join-Path $runtime "python.exe"
$getPip = Join-Path $cache "get-pip.py"
if (-not (Test-Path $getPip)) {
    Invoke-WebRequest "https://bootstrap.pypa.io/get-pip.py" -OutFile $getPip
}
& $python $getPip --disable-pip-version-check --no-warn-script-location
if ($LASTEXITCODE -ne 0) { throw "Failed to bootstrap pip in the embedded runtime" }

$sitePackages = Join-Path $runtime "Lib\site-packages"
New-Item -ItemType Directory -Force -Path $sitePackages | Out-Null
$dependencyJson = & $python -c `
    "import json,pathlib,tomllib; print(json.dumps(tomllib.loads((pathlib.Path(r'$root')/'pyproject.toml').read_text(encoding='utf-8'))['project']['dependencies']))"
if ($LASTEXITCODE -ne 0) { throw "Failed to read runtime dependencies from pyproject.toml" }
$dependencies = @($dependencyJson | ConvertFrom-Json)
if (-not $dependencies.Count) { throw "pyproject.toml does not declare runtime dependencies" }
& $python -m pip install --disable-pip-version-check --no-warn-script-location `
    --no-compile --target $sitePackages @dependencies
if ($LASTEXITCODE -ne 0) { throw "Failed to install the plugin runtime dependencies" }
$packageTarget = Join-Path $sitePackages "reasonix_computer_use"
New-Item -ItemType Directory -Force -Path $packageTarget | Out-Null
Copy-Item -Path (Join-Path $root "reasonix_computer_use\*.py") `
    -Destination $packageTarget -Force

# pip is a build tool, not a runtime dependency. Do not ship an installer in the plugin.
Remove-Item -LiteralPath (Join-Path $sitePackages "pip") -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem -LiteralPath $sitePackages -Filter "pip-*.dist-info" | Remove-Item -Recurse -Force
Remove-Item -LiteralPath (Join-Path $runtime "Scripts") -Recurse -Force -ErrorAction SilentlyContinue

$files = @(
    "reasonix-plugin.json", "reasonix-computer-use.bat", "README.md", "USER_GUIDE.md",
    "LICENSE", "CLAUDE.md", "skills"
)
foreach ($file in $files) {
    $source = Join-Path $root $file
    if (-not (Test-Path $source)) { throw "Required release file is missing: $file" }
    Copy-Item -LiteralPath $source -Destination $stage -Recurse -Force
}
New-Item -ItemType Directory -Force -Path (Join-Path $stage "memory") | Out-Null

$buildInfo = [ordered]@{
    name = $manifest.name
    version = $version
    platform = "windows-x64"
    python = $PythonVersion
    built_at_utc = [DateTime]::UtcNow.ToString("o")
}
$buildInfo | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $stage "BUILD-INFO.json") -Encoding UTF8

& $python (Join-Path $root "scripts\generate_package_list.py") `
    --output (Join-Path $stage "THIRD-PARTY-PACKAGES.md")
if ($LASTEXITCODE -ne 0) { throw "Failed to generate the third-party package list" }

& $python -c "import comtypes, PIL, pyautogui, rapidocr_onnxruntime, reasonix_computer_use; assert reasonix_computer_use.__version__ == '$version'"
if ($LASTEXITCODE -ne 0) { throw "Embedded runtime import verification failed" }

$initialize = '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'
$smokeOutput = $initialize | & (Join-Path $stage "reasonix-computer-use.bat")
if ($LASTEXITCODE -ne 0) { throw "Packaged MCP server failed to initialize" }
$smoke = $smokeOutput | Select-Object -First 1 | ConvertFrom-Json
if ($smoke.result.serverInfo.version -ne $version) {
    throw "Packaged MCP version does not match manifest: $($smoke.result.serverInfo.version)"
}

Get-ChildItem -LiteralPath $runtime -Directory -Filter "__pycache__" -Recurse | `
    Remove-Item -Recurse -Force

if (Test-Path $archive) { Remove-Item -LiteralPath $archive -Force }
if (Test-Path $checksum) { Remove-Item -LiteralPath $checksum -Force }
Compress-Archive -Path (Join-Path $stage "*") -DestinationPath $archive -CompressionLevel Optimal
$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash.ToLowerInvariant()
"$hash  $([IO.Path]::GetFileName($archive))" | Set-Content -LiteralPath $checksum -Encoding ASCII

if (-not $KeepStage) { Remove-Item -LiteralPath $stage -Recurse -Force }
Write-Host "Created: $archive"
Write-Host "Checksum: $checksum"
