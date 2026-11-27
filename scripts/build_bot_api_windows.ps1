[CmdletBinding()]
param(
    [ValidatePattern('^[a-f0-9]{40}$')]
    [string]$BotApiCommit = '5df614c91dab8662cfbb24a501ec89949fdcfebb',
    [string]$VcpkgRoot = $env:VCPKG_INSTALLATION_ROOT,
    [ValidateRange(1, 64)][int]$Jobs = 2,
    [switch]$BuildOnly
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot

function Invoke-Checked {
    param([string]$Exe, [string[]]$Arguments)
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Exe failed (exit $LASTEXITCODE)." }
}

Push-Location $repo
try {
    $git = (Get-Command git -ErrorAction Stop).Source
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    if (-not (Test-Path -LiteralPath $vswhere)) {
        throw 'Install Visual Studio 2022 Build Tools with the Desktop development with C++ workload, then rerun.'
    }
    $vsArgs = @('-version', '[17.0,18.0)', '-latest', '-products', '*',
        '-requires', 'Microsoft.VisualStudio.Component.VC.Tools.x86.x64')
    $vs = & $vswhere @vsArgs -property installationPath | Select-Object -First 1
    if (-not $vs) { throw 'Visual Studio 2022 MSVC x64 build tools were not found.' }
    $dumpbin = & $vswhere @vsArgs -find 'VC\Tools\MSVC\**\bin\Hostx64\x64\dumpbin.exe' | Select-Object -First 1
    $cmake = (Get-Command cmake -ErrorAction SilentlyContinue).Source
    if (-not $cmake) {
        $cmake = Join-Path $vs 'Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe'
    }
    if (-not (Test-Path -LiteralPath $cmake) -or -not $dumpbin) {
        throw 'Install the Visual Studio CMake tools and MSVC x64 tools, then rerun.'
    }

    if (-not $VcpkgRoot) {
        $VcpkgRoot = Join-Path $repo 'tools\vcpkg'
        if (-not (Test-Path -LiteralPath (Join-Path $VcpkgRoot '.git'))) {
            Invoke-Checked $git @('clone', 'https://github.com/microsoft/vcpkg.git', $VcpkgRoot)
        }
        $vcpkgCommit = '9fa95bf687fa6ae7f2da0673a8067e5fc1515c27'
        $installedCommit = & $git -C $VcpkgRoot rev-parse HEAD
        if ($installedCommit -ne $vcpkgCommit) {
            Invoke-Checked $git @('-C', $VcpkgRoot, 'fetch', 'origin', $vcpkgCommit)
            Invoke-Checked $git @('-C', $VcpkgRoot, 'checkout', '--detach', $vcpkgCommit)
        }
        Invoke-Checked (Join-Path $VcpkgRoot 'bootstrap-vcpkg.bat') @('-disableMetrics')
    }
    $VcpkgRoot = (Resolve-Path -LiteralPath $VcpkgRoot).Path
    $vcpkg = Join-Path $VcpkgRoot 'vcpkg.exe'
    if (-not (Test-Path -LiteralPath $vcpkg)) { throw "vcpkg.exe was not found in $VcpkgRoot." }

    $source = Join-Path $repo '.native-source'
    if (-not (Test-Path -LiteralPath (Join-Path $source '.git'))) {
        Invoke-Checked $git @('init', $source)
        $sourceCommit = ''
    } else {
        $sourceCommit = & $git -C $source rev-parse HEAD
    }
    if ($sourceCommit -ne $BotApiCommit) {
        Invoke-Checked $git @('-C', $source, 'fetch', '--depth', '1', 'https://github.com/tdlib/telegram-bot-api.git', $BotApiCommit)
        Invoke-Checked $git @('-C', $source, 'checkout', '--detach', $BotApiCommit)
    }
    Invoke-Checked $git @('-C', $source, 'submodule', 'update', '--init', '--recursive', '--depth', '1')

    Invoke-Checked $vcpkg @('install', 'gperf:x64-windows', 'openssl:x64-windows-static', 'zlib:x64-windows-static')
    $build = Join-Path $repo '.native-build'
    $bundle = Join-Path $repo 'native-bundle'
    Invoke-Checked $cmake @('-S', $source, '-B', $build, '-G', 'Visual Studio 17 2022', '-A', 'x64',
        "-DCMAKE_GENERATOR_INSTANCE=$vs", "-DCMAKE_TOOLCHAIN_FILE=$VcpkgRoot/scripts/buildsystems/vcpkg.cmake",
        "-DGPERF_EXECUTABLE=$VcpkgRoot/installed/x64-windows/tools/gperf/gperf.exe",
        '-DVCPKG_TARGET_TRIPLET=x64-windows-static', '-DBUILD_TESTING=OFF', '-DCMAKE_POLICY_DEFAULT_CMP0091=NEW',
        '-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded$<$<CONFIG:Debug>:Debug>')
    Invoke-Checked $cmake @('--build', $build, '--config', 'Release', '--target', 'telegram-bot-api', '--parallel', "$Jobs")
    Invoke-Checked $cmake @('--install', $build, '--config', 'Release', '--prefix', $bundle)
    $binary = Join-Path $bundle 'bin\telegram-bot-api.exe'
    Invoke-Checked $binary @('--version')
    $dependencies = & $dumpbin /nologo /dependents $binary
    if ($LASTEXITCODE -ne 0) { throw 'Could not verify Windows DLL dependencies.' }
    $libraries = foreach ($line in $dependencies) {
        if ($line -match '^\s+([A-Za-z0-9_.-]+\.dll)\s*$') { $Matches[1] }
    }
    if (-not $libraries) { throw 'No DLL dependencies detected by dumpbin.' }
    foreach ($library in $libraries) {
        if ($library -match '^(vcruntime|msvcp|concrt)' -or
            ($library -notmatch '^api-ms-win-' -and -not (Test-Path -LiteralPath "$env:SystemRoot\System32\$library"))) {
            throw "Unexpected Windows runtime dependency: $library"
        }
    }

    $licenses = Join-Path $bundle 'licenses'
    New-Item -ItemType Directory -Path $licenses -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $source 'LICENSE_1_0.txt') -Destination (Join-Path $licenses 'Telegram-Bot-API.txt') -Force
    Copy-Item -LiteralPath (Join-Path $source 'td\LICENSE_1_0.txt') -Destination (Join-Path $licenses 'TDLib.txt') -Force
    $share = Join-Path $VcpkgRoot 'installed\x64-windows-static\share'
    Copy-Item -LiteralPath (Join-Path $share 'openssl\copyright') -Destination (Join-Path $licenses 'OpenSSL.txt') -Force
    Copy-Item -LiteralPath (Join-Path $share 'zlib\copyright') -Destination (Join-Path $licenses 'zlib.txt') -Force

    if (-not $BuildOnly) {
        $python = @('.venv\Scripts\python.exe', '..\TelegramSocialMediaDownloaderBot\.venv\Scripts\python.exe') |
            Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
        $packageArgs = @('scripts/package_release.py', '--ref', 'HEAD', '--output-dir', 'dist',
            '--native-dir', $bundle, '--native-target', 'windows-x64', '--bot-api-commit', $BotApiCommit)
        if ($python) {
            Invoke-Checked (Resolve-Path -LiteralPath $python).Path $packageArgs
        } else {
            Invoke-Checked 'py' (@('-3.10') + $packageArgs)
        }
    }
}
finally {
    Pop-Location
}
