param(
    [string]$OutputDirectory = "$PSScriptRoot\..\dist",
    [string]$StageDirectory = "",
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path "$PSScriptRoot\..").Path
if (-not $Version) {
    $Version = [string]((Get-Content -Raw (Join-Path $root "reasonix-plugin.json") | ConvertFrom-Json).version)
}
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
if (-not $StageDirectory) {
    $StageDirectory = Join-Path $OutputDirectory "reasonix-computer-use-$Version-windows-x64"
}
$StageDirectory = [IO.Path]::GetFullPath($StageDirectory)
if (-not (Test-Path (Join-Path $StageDirectory "reasonix-plugin.json"))) {
    throw "Self-contained stage is missing: $StageDirectory. Run build_release.ps1 -KeepStage first."
}

$isccCandidates = @(
    (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
    (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
    (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe")
) | Where-Object { $_ -and (Test-Path $_) }
$iscc = $isccCandidates | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 was not found" }

$script = Join-Path $root "installer\reasonix-computer-use.iss"
& $iscc "/DAppVersion=$Version" "/DStageDir=$StageDirectory" "/DOutputDir=$OutputDirectory" $script
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed with exit code $LASTEXITCODE" }

$installer = Join-Path $OutputDirectory "reasonix-computer-use-$Version-windows-x64-setup.exe"
if (-not (Test-Path $installer)) { throw "Installer output was not created: $installer" }
$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $installer).Hash.ToLowerInvariant()
"$hash  $([IO.Path]::GetFileName($installer))" | Set-Content -LiteralPath "$installer.sha256" -Encoding ASCII
Write-Host "Created: $installer"
Write-Host "Checksum: $installer.sha256"
