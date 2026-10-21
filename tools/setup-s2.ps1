[CmdletBinding()]
param(
    [switch]$CheckOnly,
    [string]$CudaRoot,
    [string]$PythonExecutable,
    [string]$SourceDir,
    [string]$Revision = 'main'
)
$ErrorActionPreference = 'Stop'
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$runtimeRoot = Join-Path $repoRoot '.runtime'

function Find-CMake {
    $found = Get-Command cmake -ErrorAction SilentlyContinue
    if ($found) { return $found.Source }
    $locator = 'C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe'
    if (Test-Path -LiteralPath $locator) {
        $install = & $locator -latest -products '*' -property installationPath
        if ($install) {
            $candidate = Join-Path $install 'Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe'
            if (Test-Path -LiteralPath $candidate) { return $candidate }
        }
    }
    throw 'CMake is missing. Install Visual Studio C++ CMake tools or add cmake to PATH.'
}

$cmake = Find-CMake
if ($CudaRoot) {
    $CudaRoot = [IO.Path]::GetFullPath($CudaRoot)
    $nvcc = Join-Path $CudaRoot 'bin\nvcc.exe'
} else {
    $cudaCommand = Get-Command nvcc -ErrorAction SilentlyContinue
    if (-not $cudaCommand) { throw 'CUDA Toolkit >= 12.4 is required; nvcc was not found. Use -CudaRoot to select an installed toolkit.' }
    $nvcc = $cudaCommand.Source
    $CudaRoot = Split-Path (Split-Path $nvcc -Parent) -Parent
}
foreach ($required in @('bin\nvcc.exe', 'include\cuda_runtime.h', 'lib\x64\cublas.lib')) {
    if (-not (Test-Path -LiteralPath (Join-Path $CudaRoot $required) -PathType Leaf)) {
        throw "Incomplete CUDA Toolkit: missing $required under $CudaRoot"
    }
}
$cudaVersionText = (& $nvcc --version | Out-String)
if ($LASTEXITCODE -ne 0) { throw 'Unable to execute the selected CUDA compiler.' }
$match = [regex]::Match($cudaVersionText, 'release\s+(\d+)\.(\d+)')
$detectedVersion = if ($match.Success) { [version]($match.Groups[1].Value + '.' + $match.Groups[2].Value) } else { [version]'0.0' }
if ($detectedVersion -lt [version]'12.4') {
    # Use an explicit version check before any multi-GB downloads.
    if ($match.Success) { $detected = $match.Groups[1].Value + '.' + $match.Groups[2].Value } else { $detected = 'unknown' }
    throw "CUDA Toolkit >= 12.4 is required by s2.cpp; detected $detected. The NVIDIA driver CUDA version is a different value."
}
if ($CheckOnly) {
    [pscustomobject]@{ CMake = $cmake; CUDA = $match.Value; CudaRoot = $CudaRoot; Source = $SourceDir; ReadyToConfigure = $true } | ConvertTo-Json
    exit 0
}
New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null
if (-not $PythonExecutable) {
    $localPython = Join-Path $repoRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $localPython -PathType Leaf) -and (Get-Command uv -ErrorAction SilentlyContinue)) {
        # This is a uv project: create .venv from uv.lock rather than borrowing a system Python.
        & uv sync --project $repoRoot
        if ($LASTEXITCODE -ne 0) { throw 'uv sync failed.' }
    }
    if (Test-Path -LiteralPath $localPython -PathType Leaf) { $PythonExecutable = $localPython }
    else { $PythonExecutable = (Get-Command python -ErrorAction Stop).Source }
}
if (-not $SourceDir) {
    $SourceDir = Join-Path $runtimeRoot 's2.cpp'
    if (-not (Test-Path -LiteralPath $SourceDir)) {
        & git clone --recurse-submodules https://github.com/rodrigomatta/s2.cpp.git $SourceDir
        if ($LASTEXITCODE -ne 0) { throw 'Unable to download s2.cpp. Check GitHub connectivity.' }
        & git -C $SourceDir checkout $Revision
        if ($LASTEXITCODE -ne 0) { throw 'Unable to check out the requested s2.cpp revision.' }
        & git -C $SourceDir submodule update --init --recursive
        if ($LASTEXITCODE -ne 0) { throw 'Unable to download the ggml submodule.' }
    }
}
$SourceDir = [IO.Path]::GetFullPath($SourceDir)
$buildDir = Join-Path $runtimeRoot 's2-build'
& $cmake -S $SourceDir -B $buildDir -G 'Visual Studio 17 2022' -A x64 -T "cuda=$CudaRoot" "-DCUDAToolkit_ROOT=$CudaRoot" -DS2_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=86 -DBUILD_SHARED_LIBS=OFF
if ($LASTEXITCODE -ne 0) { throw 'CMake configuration failed.' }
& $cmake --build $buildDir --config Release --parallel 4
if ($LASTEXITCODE -ne 0) { throw 's2.cpp build failed.' }
# No BOM: Set-Content -Encoding utf8 adds one on Windows PowerShell 5.1.
[IO.File]::WriteAllText((Join-Path $runtimeRoot 's2-cuda-root.txt'), $CudaRoot)
& $PythonExecutable (Join-Path $PSScriptRoot 'fetch_s2.py') --directory (Join-Path $runtimeRoot 'models')
if ($LASTEXITCODE -ne 0) { throw 'Model download failed; the compiled engine was preserved.' }
& git -C $SourceDir rev-parse HEAD | Set-Content -Encoding ascii (Join-Path $runtimeRoot 's2-source-revision.txt')
Write-Output 'Setup finished. Run: uv run presenter-tts server start'
