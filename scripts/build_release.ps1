param(
    [string]$OutputDirectory = "$PSScriptRoot\..\dist",
    [string]$PythonVersion = "3.12.10"
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path "$PSScriptRoot\..").Path
$stage = Join-Path $OutputDirectory "reasonix-computer-use-0.8.0-alpha.0"
$runtime = Join-Path $stage "runtime"
$cache = Join-Path $OutputDirectory ".release-cache"

New-Item -ItemType Directory -Force -Path $OutputDirectory, $cache | Out-Null
if (Test-Path $stage) {
    Remove-Item -LiteralPath $stage -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $stage, $runtime | Out-Null

$versionDigits = $PythonVersion.Replace(".", "")
$pythonZip = Join-Path $cache "python-$PythonVersion-embed-amd64.zip"
if (-not (Test-Path $pythonZip)) {
    Invoke-WebRequest "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip" -OutFile $pythonZip
}
Expand-Archive -LiteralPath $pythonZip -DestinationPath $runtime -Force

$pth = Get-ChildItem -LiteralPath $runtime -Filter "python*._pth" | Select-Object -First 1
if (-not $pth) { throw "Embedded Python ._pth file was not found" }
$pthContent = Get-Content -LiteralPath $pth.FullName
$pthContent = $pthContent | ForEach-Object { if ($_ -eq "#import site") { "import site" } else { $_ } }
if ($pthContent -notcontains "Lib\site-packages") { $pthContent += "Lib\site-packages" }
Set-Content -LiteralPath $pth.FullName -Value $pthContent -Encoding ASCII

$getPip = Join-Path $cache "get-pip.py"
if (-not (Test-Path $getPip)) {
    Invoke-WebRequest "https://bootstrap.pypa.io/get-pip.py" -OutFile $getPip
}
& (Join-Path $runtime "python.exe") $getPip --disable-pip-version-check

$sitePackages = Join-Path $runtime "Lib\site-packages"
New-Item -ItemType Directory -Force -Path $sitePackages | Out-Null
& (Join-Path $runtime "python.exe") -m pip install --disable-pip-version-check --no-compile --target $sitePackages $root

$files = @(
    "reasonix-plugin.json", "reasonix-computer-use.bat", "README.md", "USER_GUIDE.md",
    "LICENSE", "CLAUDE.md", "hooks", "skills"
)
foreach ($file in $files) {
    Copy-Item -LiteralPath (Join-Path $root $file) -Destination $stage -Recurse -Force
}
New-Item -ItemType Directory -Force -Path (Join-Path $stage "memory") | Out-Null

$archive = "$stage.zip"
if (Test-Path $archive) { Remove-Item -LiteralPath $archive -Force }
Compress-Archive -Path (Join-Path $stage "*") -DestinationPath $archive -CompressionLevel Optimal
Write-Host "Created self-contained Reasonix plugin: $archive"
