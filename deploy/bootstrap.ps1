param([switch]$PrepareOnly, [switch]$NoFrontend)

$ErrorActionPreference = 'Stop'
# A cmd launcher can inherit PowerShell 7 modules. Use this process's built-ins.
$env:PSModulePath = Join-Path $PSHOME 'Modules'
Import-Module Microsoft.PowerShell.Utility
Import-Module Microsoft.PowerShell.Archive
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$ProgressPreference = 'SilentlyContinue'
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
Set-Location -LiteralPath $taskRoot
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PIP_DISABLE_PIP_VERSION_CHECK = '1'
$taskCache = Join-Path $taskRoot 'log\installer\downloads'
New-Item -ItemType Directory -Path $taskCache -Force | Out-Null

function Get-VerifiedDownload($Name, $Urls, $Digest) {
    $target = Join-Path $taskCache $Name
    if ((Test-Path -LiteralPath $target) -and ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -eq $Digest)) {
        return $target
    }
    foreach ($url in $Urls) {
        try {
            Write-Host "Downloading $Name ..."
            Invoke-WebRequest -Uri $url -OutFile ($target + '.part') -UseBasicParsing -TimeoutSec 120
            if ((Get-FileHash -LiteralPath ($target + '.part') -Algorithm SHA256).Hash -ne $Digest) {
                throw "SHA-256 mismatch for $Name"
            }
            Move-Item -LiteralPath ($target + '.part') -Destination $target -Force
            return $target
        } catch {
            Write-Warning "Download failed: $Name. Trying the next source if available."
        }
    }
    throw "Cannot download or verify $Name. Check network/proxy, then run this file again."
}

function Expand-VerifiedZip($Archive, $Destination) {
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    Expand-Archive -LiteralPath $Archive -DestinationPath $Destination -Force
}

try {
    $taskLock = [IO.File]::Open((Join-Path $taskCache '..\bootstrap.lock'), [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    $taskLog = Join-Path $taskCache ('..\install-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.txt')
    Start-Transcript -LiteralPath $taskLog -Force | Out-Null
    $taskTranscriptStarted = $true
    if (-not [Environment]::Is64BitOperatingSystem) { throw 'Windows x64 is required.' }
    if (-not (Test-Path -LiteralPath (Join-Path $taskRoot 'server.py'))) {
        throw 'Extract the complete repository ZIP before running this file.'
    }
    $taskToolkit = Join-Path $taskRoot 'toolkit'
    $taskPython = Join-Path $taskToolkit 'python.exe'
    if (-not (Test-Path -LiteralPath $taskPython)) {
        $archive = Get-VerifiedDownload 'python-3.10.11-embed-amd64.zip' @('https://www.python.org/ftp/python/3.10.11/python-3.10.11-embed-amd64.zip') '608619f8619075629c9c69f361352a0da6ed7e62f83a0e19c63e0ea32eb7629d'
        Expand-VerifiedZip $archive $taskToolkit
        New-Item -ItemType Directory -Path (Join-Path $taskToolkit 'Lib\site-packages') -Force | Out-Null
        [IO.File]::WriteAllLines((Join-Path $taskToolkit 'python310._pth'), @('python310.zip', '.', 'Lib', 'Lib/site-packages', '..', 'import site'), [Text.Encoding]::ASCII)
    }
    & $taskPython -c "import sys,struct; assert sys.version_info[:2] == (3,10) and struct.calcsize('P') == 8, 'Python 3.10 x64 is required'"
    if ($LASTEXITCODE -ne 0) { throw 'The local Python runtime is incomplete or incompatible.' }
    & $taskPython -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('pip') else 1)"
    if ($LASTEXITCODE -ne 0) {
        $wheel = Get-VerifiedDownload 'pip-25.3.whl' @('https://files.pythonhosted.org/packages/44/3c/d717024885424591d5376220b5e836c2d5293ce2011523c9de23ff7bf068/pip-25.3-py3-none-any.whl', 'https://pypi.tuna.tsinghua.edu.cn/packages/44/3c/d717024885424591d5376220b5e836c2d5293ce2011523c9de23ff7bf068/pip-25.3-py3-none-any.whl') '9655943313a94722b7774661c21049070f6bbb0a1516bf02f7c8d5d9201514cd'
        & $taskPython -c "import sys,zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" $wheel (Join-Path $taskToolkit 'Lib\site-packages')
        if ($LASTEXITCODE -ne 0) { throw 'Failed to prepare pip.' }
    }
    $taskGit = Join-Path $taskToolkit 'Git\cmd\git.exe'
    $taskLegacyGit = Join-Path $taskToolkit 'Git\mingw64\bin\git.exe'
    if (-not ((Test-Path -LiteralPath $taskGit) -or (Test-Path -LiteralPath $taskLegacyGit))) {
        $archive = Get-VerifiedDownload 'MinGit-2.56.0.2-64-bit.zip' @('https://github.com/git-for-windows/git/releases/download/v2.56.0.windows.2/MinGit-2.56.0.2-64-bit.zip') 'da35e72aa21c005a5a0d298cfbae110bc1609a815730ea0dde84b01a1b3cd3be'
        Expand-VerifiedZip $archive (Join-Path $taskToolkit 'Git')
    }
    # The client archive also includes the Microsoft runtime DLLs it needs.
    $taskClient = Join-Path $taskToolkit 'OASX\oasx_luchi-2026.10.11\app\oasx.exe'
    if (-not $NoFrontend) {
        if (-not (Test-Path -LiteralPath $taskClient)) {
            $archive = Get-VerifiedDownload 'oasx_luchi-2026.10.11_windows_x64.zip' @('https://github.com/luchi5/OASX/releases/download/luchi-2026.10.11/oasx_luchi-2026.10.11_windows_x64.zip') '461bb0cd6ed2678fc3198153745b4bfe503e9c782d20fee7949463a92df89dd1'
            Expand-VerifiedZip $archive (Join-Path $taskToolkit 'OASX')
        }
        foreach ($dll in @('msvcp140.dll', 'vcruntime140_1.dll')) {
            $target = Join-Path $taskToolkit $dll
            if (-not (Test-Path -LiteralPath $target)) {
                Copy-Item -LiteralPath (Join-Path (Split-Path $taskClient) $dll) -Destination $target
            }
        }
    }
    Get-VerifiedDownload 'platform-tools-37.0.1.zip' @('https://dl.google.com/android/repository/platform-tools_r37.0.1-win.zip', 'https://redirector.gvt1.com/edgedl/android/repository/platform-tools_r37.0.1-win.zip') '45f4d63113e895ebde0c90f194099a4676b6ac653bd28d54314a9e022bbc1a99' | Out-Null
    $taskArgs = @('-X', 'utf8', '-B', '-m', 'deploy.bootstrap')
    if ($PrepareOnly) { $taskArgs += '--prepare-only' }
    if ($NoFrontend) { $taskArgs += '--no-frontend' }
    $ErrorActionPreference = 'Continue'
    & $taskPython @taskArgs
    $taskExit = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    if ($taskExit -ne 0) { throw 'OAS preparation or startup failed. See the details above.' }
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
} finally {
    if ($taskTranscriptStarted) { Stop-Transcript | Out-Null }
    if ($taskLock) { $taskLock.Dispose() }
}
