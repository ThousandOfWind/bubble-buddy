<#
.SYNOPSIS
    Build the click-to-use Windows installer for copilot-voice-shell.

.DESCRIPTION
    1. Freezes the app with PyInstaller into dist\copilot-voice-shell (one folder).
    2. Wraps that folder into dist\installer\CopilotVoiceShell-Setup-<ver>.exe with
       Inno Setup (if ISCC.exe is found).

.EXAMPLE
    pwsh -File packaging\build.ps1
#>
[CmdletBinding()]
param(
    [switch]$SkipInstaller,
    [string]$Version,
    [ValidateSet("azure", "full")]
    [string]$Edition = "azure"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    Write-Host "==> Stopping any running instance (frees locked exe)..."
    Get-Process copilot-voice-shell -ErrorAction SilentlyContinue |
        ForEach-Object { Stop-Process -Id $_.Id -Force }

    # The "full" edition bundles the offline Whisper stack; the spec reads this env var.
    if ($Edition -eq "full") { $env:CVS_INCLUDE_LOCAL = "1" } else { $env:CVS_INCLUDE_LOCAL = "0" }
    Write-Host "==> Running PyInstaller ($Edition edition)..."
    uv run pyinstaller packaging\copilot-voice-shell.spec --noconfirm `
        --distpath dist --workpath build\pyi
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

    if ($SkipInstaller) {
        Write-Host "==> Done (portable folder): dist\copilot-voice-shell"
        return
    }

    $iscc = Get-ChildItem `
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe" `
        -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName

    if (-not $iscc) {
        Write-Warning "Inno Setup (ISCC.exe) not found. Install it with:`n  winget install --id JRSoftware.InnoSetup -e`nPortable folder is ready at dist\copilot-voice-shell"
        return
    }

    Write-Host "==> Building installer with Inno Setup ($Edition edition)..."
    $isccArgs = @("/DEdition=$Edition", "packaging\installer.iss")
    if ($Version) { $isccArgs = @("/DMyAppVersion=$Version") + $isccArgs }
    & $iscc @isccArgs
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }

    $out = Get-ChildItem dist\installer\CopilotVoiceShell*-Setup-*.exe |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    Write-Host "==> Done. Installer: $($out.FullName)"
}
finally {
    Pop-Location
}
