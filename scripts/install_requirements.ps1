<#
Install requirements for this workspace.

This script will try to locate a Python executable and run:
    python -m pip install -r requirements.txt

It is idempotent and returns a non-zero exit code on failure.
#>

try {
    $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
    $requirementsPath = Join-Path $scriptDir "..\requirements.txt" | Resolve-Path -ErrorAction Stop
} catch {
    Write-Error "Cannot find requirements.txt relative to script location."
    exit 1
}

# workspace root (one level up from scripts folder)
$workspaceRoot = (Resolve-Path (Join-Path $scriptDir "..") ).Path

# virtual env path inside workspace
$venvPath = Join-Path $workspaceRoot ".venv"

# Try to use existing VIRTUAL_ENV or .venv if present
if ($env:VIRTUAL_ENV) {
    $candidate = Join-Path $env:VIRTUAL_ENV "Scripts\python.exe"
    if (Test-Path $candidate) { $python = $candidate }
}

if (-not $python -and (Test-Path $venvPath)) {
    $candidate = Join-Path $venvPath "Scripts\python.exe"
    if (Test-Path $candidate) { $python = $candidate }
}

# Try to use a virtualenv if present
if ($env:VIRTUAL_ENV) {
    $candidate = Join-Path $env:VIRTUAL_ENV "Scripts\python.exe"
    if (Test-Path $candidate) {
        $python = $candidate
    }
}

if (-not $python) {
    # Try to ask the PATH 'python' first
    try {
        $py = & python -c "import sys;print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $py) { $python = $py.Trim() }
    } catch {
        $python = $null
    }
}

if (-not $python) {
    # Last resort: try py launcher
    try {
        $py = & py -c "import sys;print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $py) { $python = $py.Trim() }
    } catch {
        $python = $null
    }
}

if (-not $python) {
    Write-Error "Python executable not found. Ensure Python is installed and available on PATH, or activate your virtual environment."
    exit 2
}

Write-Output "Using Python: $python"

# Create .venv inside the workspace if it doesn't exist, and prefer it for installs
if (-not (Test-Path $venvPath)) {
    Write-Output "Creating virtual environment at $venvPath"
    & $python -m venv $venvPath
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Failed to create virtual environment at $venvPath. Continuing with system python."
    }
}

# Use venv's python when available
$venvPython = Join-Path $venvPath "Scripts\python.exe"
if (Test-Path $venvPython) {
    $python = $venvPython
}

Write-Output "Using Python for installs: $python"

& $python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to upgrade pip"
    exit $LASTEXITCODE
}

function Install-FFmpeg {
    param(
        [string]$WorkspaceRoot = "${scriptDir}/.."
    )

    Write-Output "Checking for ffmpeg on PATH..."
    $ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
    if ($ffmpeg) {
        Write-Output "ffmpeg already available at $($ffmpeg.Path)"
        return $true
    }

    Write-Output "Attempting to install ffmpeg using winget..."
    try {
        & winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
        if ($LASTEXITCODE -eq 0) { Write-Output "ffmpeg installed via winget."; return $true }
    } catch { }

    Write-Output "Attempting to install ffmpeg using chocolatey..."
    try {
        & choco install ffmpeg -y
        if ($LASTEXITCODE -eq 0) { Write-Output "ffmpeg installed via chocolatey."; return $true }
    } catch { }

    Write-Output "Falling back to downloading a static ffmpeg build..."
    $dest = Join-Path (Resolve-Path (Join-Path $WorkspaceRoot "." )).Path "tools\ffmpeg"
    if (-not (Test-Path $dest)) { New-Item -ItemType Directory -Path $dest -Force | Out-Null }

    $zipUrl = 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip'
    $tempZip = Join-Path $env:TEMP "ffmpeg-release-essentials.zip"
    Write-Output "Downloading ffmpeg from $zipUrl to $tempZip"
    try {
        Invoke-WebRequest -Uri $zipUrl -OutFile $tempZip -UseBasicParsing -ErrorAction Stop
    } catch {
        Write-Error "Failed to download ffmpeg: $($_.Exception.Message)"
        return $false
    }

    Write-Output "Extracting ffmpeg..."
    try {
        Expand-Archive -Path $tempZip -DestinationPath $dest -Force
    } catch {
        Write-Error "Failed to extract ffmpeg: $($_.Exception.Message)"
        return $false
    }

    # The zip typically contains a top-level folder like ffmpeg-*-essentials_build
    $binDirs = Get-ChildItem -Path $dest -Directory -ErrorAction SilentlyContinue | ForEach-Object { Join-Path $_.FullName 'bin' }
    $ffmpegExe = $null
    foreach ($b in $binDirs) {
        $candidate = Join-Path $b 'ffmpeg.exe'
        if (Test-Path $candidate) { $ffmpegExe = $candidate; break }
    }

    if (-not $ffmpegExe) {
        # Maybe the zip extracted directly into dest\bin
        $candidate = Join-Path $dest 'bin\ffmpeg.exe'
        if (Test-Path $candidate) { $ffmpegExe = $candidate }
    }

    if (-not $ffmpegExe) {
        Write-Error "ffmpeg binary not found after extraction."
        return $false
    }

    $ffmpegBin = Split-Path -Parent $ffmpegExe
    Write-Output "ffmpeg installed to $ffmpegBin"

    # Add to current session PATH
    if ($env:PATH -notlike "*${ffmpegBin}*") { $env:PATH = "$env:PATH;${ffmpegBin}" }

    # Persist to user PATH via setx (note: setx has a 1024 char limit on older Windows)
    try {
        $currentUserPath = [Environment]::GetEnvironmentVariable('Path', 'User')
        if ($currentUserPath -notlike "*${ffmpegBin}*") {
            $newPath = if ($currentUserPath) { "$currentUserPath;${ffmpegBin}" } else { "${ffmpegBin}" }
            setx Path "$newPath" | Out-Null
            Write-Output "Added ffmpeg to user PATH (will be available in new shells)."
        } else {
            Write-Output "ffmpeg already present in user PATH."
        }
    } catch {
        Write-Warning "Failed to add ffmpeg to user PATH persistently: $($_.Exception.Message)"
    }

    return $true
}

if (-not (Install-FFmpeg -WorkspaceRoot (Join-Path $scriptDir ".."))) {
    Write-Warning "ffmpeg installation failed or was not completed. Some features (e.g., video processing) may not work until ffmpeg is installed."
} else {
    Write-Output "ffmpeg is available."
}

& $python -m pip install -r $requirementsPath
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to install requirements"
    exit $LASTEXITCODE
}

Write-Output "Requirements installed successfully."
exit 0
