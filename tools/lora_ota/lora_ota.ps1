$ErrorActionPreference = 'Stop'
$scriptPath = Join-Path $PSScriptRoot 'lora_ota.py'
$otaArguments = @($args)

if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 $scriptPath @otaArguments
    exit $LASTEXITCODE
}

if (Get-Command python3 -ErrorAction SilentlyContinue) {
    & python3 $scriptPath @otaArguments
    exit $LASTEXITCODE
}

if (Get-Command python -ErrorAction SilentlyContinue) {
    & python $scriptPath @otaArguments
    exit $LASTEXITCODE
}

throw 'Python 3.10 or newer was not found on PATH.'
