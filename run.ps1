param(
	[switch]$ActivateOnly,
	[switch]$Interactive,
	[switch]$Chat,
	[switch]$Portfolio,
	[switch]$Events,
	[string]$Live,
	[string]$Backtest,
	[string]$Scan,
	[string]$ScanIndustry,
	[string]$ScanConcept,
	[switch]$ScanDeep,
	[switch]$ScanListRules,
	[switch]$ScanListIndustries,
	[switch]$ScanListConcepts,
	[Parameter(ValueFromRemainingArguments = $true)]
	[string[]]$CliArgs
)

$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

$activateScript = Join-Path $scriptDir '.venv\Scripts\activate.ps1'
if (Test-Path $activateScript) {
	. $activateScript
}

if ($ActivateOnly) {
	return
}

$pythonCmd = if (Test-Path (Join-Path $scriptDir '.venv\Scripts\python.exe')) {
	Join-Path $scriptDir '.venv\Scripts\python.exe'
} else {
	'python'
}

$argsList = New-Object System.Collections.Generic.List[string]

if ($Live) {
	$argsList.Add('--live')
	$argsList.Add($Live)
}

if ($Backtest) {
	$argsList.Add('--backtest')
	$argsList.Add($Backtest)
}

if ($Portfolio) {
	$argsList.Add('--portfolio')
}

if ($Events) {
	$argsList.Add('--events')
}

if ($Chat) {
	$argsList.Add('--chat')
}

if ($null -ne $Scan) {
	$scanRule = if ([string]::IsNullOrWhiteSpace($Scan)) { 'default' } else { $Scan }
	$argsList.Add('--scan')
	$argsList.Add($scanRule)
}

if ($ScanIndustry) {
	$argsList.Add('--scan-industry')
	$argsList.Add($ScanIndustry)
}

if ($ScanConcept) {
	$argsList.Add('--scan-concept')
	$argsList.Add($ScanConcept)
}

if ($ScanDeep) {
	$argsList.Add('--scan-deep')
}

if ($ScanListRules) {
	$argsList.Add('--scan-list-rules')
}

if ($ScanListIndustries) {
	$argsList.Add('--scan-list-industries')
}

if ($ScanListConcepts) {
	$argsList.Add('--scan-list-concepts')
}

if ($Interactive -and $argsList.Count -eq 0 -and (-not $CliArgs -or $CliArgs.Count -eq 0)) {
	$argsList.Clear()
}

if ($CliArgs) {
	foreach ($arg in $CliArgs) {
		$argsList.Add($arg)
	}
}

if ($argsList.Count -eq 0) {
	Write-Host 'Muyun launcher' -ForegroundColor Cyan
	Write-Host 'No arguments provided. Starting the CLI interactive mode.' -ForegroundColor DarkGray
	Write-Host ''
	Write-Host 'Examples:' -ForegroundColor Yellow
	Write-Host '  .\run.ps1 --live 600519'
	Write-Host '  .\run.ps1 --portfolio'
	Write-Host '  .\run.ps1 --scan default --scan-industry 半导 --scan-concept AI'
	Write-Host '  .\run.ps1 --scan-list-industries'
	Write-Host '  .\run.ps1 --scan-list-concepts'
	Write-Host '  .\run.ps1 --chat'
	Write-Host ''
}

& $pythonCmd -m src.cli.main @argsList