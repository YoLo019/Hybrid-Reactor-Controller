param(
    [string]$DataRoot = ""
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($DataRoot)) {
    $projectModelRoot = Split-Path -Parent $PSScriptRoot
    $DataRoot = Join-Path $projectModelRoot "data\wind"
}

$sourceUrl = "https://data.mendeley.com/public-api/zip/vtsgxnwswn/download/1"
$landingUrl = "https://data.mendeley.com/datasets/vtsgxnwswn/1"
$expected = @{
    "download.zip" = "2F51105D9566BCC728A3A981FB89D3EB15F73A631FF454EE3C40AC6AD2F8D4DA"
    "Raw_Data.rar" = "B7DAC380F01FE2E4D55CEB4365130FCF4E7D7EAED2F9BD2F3A3DBBD0E7C0953B"
    "NWP.csv" = "19C11D2A64924D2B48639780F5BCBE435FBAF9A7D4F886ACD9AA280C6707C722"
    "wind farm historical data.csv" = "2AB798258A566F2F2C6A4BCAB0023E6485E34C08C432A49D2C0F2D4DE4E09E6F"
}

function Assert-Hash {
    param(
        [string]$Path,
        [string]$ExpectedHash
    )

    $actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
    if ($actual -ne $ExpectedHash) {
        throw "SHA-256 mismatch: $Path expected=$ExpectedHash actual=$actual"
    }
    return $actual
}

function Install-VerifiedFile {
    param(
        [string]$Source,
        [string]$Destination,
        [string]$ExpectedHash
    )

    [void](Assert-Hash -Path $Source -ExpectedHash $ExpectedHash)
    if (Test-Path -LiteralPath $Destination) {
        [void](Assert-Hash -Path $Destination -ExpectedHash $ExpectedHash)
        return
    }
    Copy-Item -LiteralPath $Source -Destination $Destination
    [void](Assert-Hash -Path $Destination -ExpectedHash $ExpectedHash)
}

$rawDir = Join-Path $DataRoot "raw\sotavento_mendeley_v1"
$manifestDir = Join-Path $DataRoot "manifests"
New-Item -ItemType Directory -Force -Path $rawDir, $manifestDir | Out-Null

$tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$tempDir = Join-Path $tempRoot ("mpc_gpt_sotavento_" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tempDir | Out-Null

try {
    $tempZip = Join-Path $tempDir "download.zip"
    & curl.exe -L --fail --silent --show-error --max-time 180 `
        -A "Mozilla/5.0" -e $landingUrl -o $tempZip $sourceUrl
    if ($LASTEXITCODE -ne 0) {
        throw "curl download failed with exit code $LASTEXITCODE"
    }
    Install-VerifiedFile -Source $tempZip `
        -Destination (Join-Path $rawDir "download.zip") `
        -ExpectedHash $expected["download.zip"]

    $outerDir = Join-Path $tempDir "outer"
    Expand-Archive -LiteralPath $tempZip -DestinationPath $outerDir
    $rar = Get-ChildItem -LiteralPath $outerDir -Recurse -Filter "Raw_Data.rar" |
        Select-Object -First 1
    if (-not $rar) {
        throw "Raw_Data.rar was not found in the downloaded archive"
    }
    Install-VerifiedFile -Source $rar.FullName `
        -Destination (Join-Path $rawDir "Raw_Data.rar") `
        -ExpectedHash $expected["Raw_Data.rar"]

    $sevenZip = (Get-Command 7z.exe -ErrorAction Stop).Source
    $csvDir = Join-Path $tempDir "csv"
    New-Item -ItemType Directory -Path $csvDir | Out-Null
    & $sevenZip x -y "-o$csvDir" -- $rar.FullName | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "7z extraction failed with exit code $LASTEXITCODE"
    }

    foreach ($name in @("NWP.csv", "wind farm historical data.csv")) {
        $source = Join-Path $csvDir $name
        if (-not (Test-Path -LiteralPath $source)) {
            throw "Expected file missing after RAR extraction: $name"
        }
        Install-VerifiedFile -Source $source `
            -Destination (Join-Path $rawDir $name) `
            -ExpectedHash $expected[$name]
    }

    foreach ($name in $expected.Keys) {
        (Get-Item -LiteralPath (Join-Path $rawDir $name)).IsReadOnly = $true
    }

    $files = foreach ($name in $expected.Keys | Sort-Object) {
        $path = Join-Path $rawDir $name
        [ordered]@{
            name = $name
            bytes = (Get-Item -LiteralPath $path).Length
            sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
        }
    }
    $manifest = [ordered]@{
        dataset_id = "mendeley:vtsgxnwswn:1"
        doi = "10.17632/vtsgxnwswn.1"
        landing_url = $landingUrl
        download_url = $sourceUrl
        retrieved_utc = (Get-Date).ToUniversalTime().ToString("o")
        license = "CC BY 4.0 (as stated on the Mendeley Data landing page)"
        files = $files
    }
    $manifestPath = Join-Path $manifestDir "sotavento_mendeley_v1_acquisition.json"
    $manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
    Write-Output "ACQUISITION_PASS=true"
    Write-Output "RAW_DIR=$rawDir"
    Write-Output "MANIFEST=$manifestPath"
}
finally {
    $resolvedTemp = [IO.Path]::GetFullPath($tempDir)
    if ($resolvedTemp.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) -and
        (Test-Path -LiteralPath $resolvedTemp)) {
        Remove-Item -LiteralPath $resolvedTemp -Recurse -Force
    }
}
