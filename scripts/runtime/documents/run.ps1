param([Parameter(Mandatory=$true)][string]$Job)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$env:PYTHONIOENCODING='utf-8'
. (Join-Path $PSScriptRoot '..\query_lock.ps1')
$request=Get-Content -LiteralPath $Job -Raw -Encoding UTF8 | ConvertFrom-Json
Invoke-WeComQuery -Action {
    $processes=@(Get-CimInstance Win32_Process)
    $roots=@($processes | Where-Object {$_.Name -eq 'WXWork.exe' -and $_.ExecutablePath})
    $agents=@($processes | Where-Object {$_.Name -eq 'wecom-agent.exe' -and $_.ExecutablePath -and $_.ParentProcessId -in $roots.ProcessId})
    if($agents.Count -ne 1){throw 'Expected one checked logged-in WeCom companion agent'}
    $root=@($roots | Where-Object {$_.ProcessId -eq $agents[0].ParentProcessId})
    if($root.Count -ne 1){throw 'Could not identify the companion agent parent'}
    $install=[IO.Path]::GetDirectoryName($root[0].ExecutablePath)
    $installPrefix=[IO.Path]::GetFullPath($install).TrimEnd('\')+'\'
    if(-not [IO.Path]::GetFullPath($agents[0].ExecutablePath).StartsWith($installPrefix,[StringComparison]::OrdinalIgnoreCase)){
        throw 'The companion agent is outside the running WeCom installation'
    }
    $browsers=@($processes | Where-Object {$_.Name -eq 'WXWorkWeb.exe' -and $_.ParentProcessId -eq $root[0].ProcessId -and $_.CommandLine -notmatch '--type=' -and $_.ExecutablePath})
    if($browsers.Count -gt 1 -and $request.command -in @('state','locate','apply','refresh')){
        throw 'Multiple CEF browser processes require explicit selection for editor operations'
    }
    $env:WECOM_DOCUMENT_INSTALL=$install
    $env:WECOM_DOCUMENT_MAIN_PATH=$root[0].ExecutablePath
    $env:WECOM_DOCUMENT_AGENT_PATH=$agents[0].ExecutablePath
    $env:WECOM_DOCUMENT_CLIENT_VERSION=[Diagnostics.FileVersionInfo]::GetVersionInfo($root[0].ExecutablePath).FileVersion
    $env:WECOM_DOCUMENT_AGENT_PID=[string]$agents[0].ProcessId
    $env:WECOM_DOCUMENT_BROWSER_PID=if($browsers.Count -eq 1){[string]$browsers[0].ProcessId}else{'0'}
    & $request.python_windows (Join-Path $PSScriptRoot 'windows_entry.py') $Job
    $script:documentExitCode=$LASTEXITCODE
}
exit $script:documentExitCode
