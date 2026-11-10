param([Parameter(Mandatory=$true)][string]$Path)
$ErrorActionPreference='Stop'
$directory=Get-Item -LiteralPath $Path
if(-not $directory.PSIsContainer){throw 'Expected a document output directory'}
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
# Only the new attempt directory is changed; existing user folders are untouched.
$icacls=Join-Path $env:SystemRoot 'System32\icacls.exe'
& $icacls $directory.FullName '/reset' | Out-Null
if($LASTEXITCODE -ne 0){throw 'Could not initialize the new document output directory permissions'}
& $icacls $directory.FullName '/inheritance:r' '/grant:r' ('*'+$sid+':(OI)(CI)F') '*S-1-5-18:(OI)(CI)F' | Out-Null
if($LASTEXITCODE -ne 0){throw 'Could not restrict the new document output directory to the current user and SYSTEM'}
