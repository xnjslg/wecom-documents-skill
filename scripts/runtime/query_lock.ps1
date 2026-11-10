function Invoke-WeComQuery {
    param(
        [Parameter(Mandatory = $true)][scriptblock]$Action,
        [ValidateRange(1,600)][int]$TimeoutSeconds = 600
    )
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $mutex = [Threading.Mutex]::new($false, ('Global\CodexWeComReadonly-' + $sid))
    $acquired = $false
    try {
        try {
            $acquired = $mutex.WaitOne([TimeSpan]::FromSeconds($TimeoutSeconds))
        } catch [Threading.AbandonedMutexException] {
            # WaitOne grants ownership when the previous owner has exited.
            $acquired = $true
        }
        if (-not $acquired) { throw 'Another WeCom read-only query is still running; queue wait timed out.' }
        & $Action
    } finally {
        if ($acquired) { $mutex.ReleaseMutex() }
        $mutex.Dispose()
    }
}
