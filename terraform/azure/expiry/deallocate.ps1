param(
    [Parameter(Mandatory=$true)][string]$VmAResourceId,
    [Parameter(Mandatory=$true)][string]$VmBResourceId,
    [Parameter(Mandatory=$true)][string]$SubscriptionId,
    [Parameter(Mandatory=$true)][string]$ResourceGroupName,
    [Parameter(Mandatory=$true)][string]$DeploymentId,
    [Parameter(Mandatory=$true)][datetime]$RetryEndAt,
    [Parameter(Mandatory=$true)][int]$PollSeconds
)
$ErrorActionPreference = 'Stop'

function Assert-Target([string]$Id) {
    $expected = '^/subscriptions/' + [regex]::Escape($SubscriptionId) + '/resourceGroups/' + [regex]::Escape($ResourceGroupName) + '/providers/Microsoft\.Compute/virtualMachines/[^/]+$'
    if ($Id -notmatch $expected) { throw 'Target VM resource ID is outside the registered attempt subscription or is malformed.' }
}
Assert-Target $VmAResourceId
Assert-Target $VmBResourceId
if ($VmAResourceId -eq $VmBResourceId) { throw 'Target VM identities must be distinct.' }
if ($VmAResourceId -cnotmatch ('/' + [regex]::Escape($ResourceGroupName) + '-a$') -or $VmBResourceId -cnotmatch ('/' + [regex]::Escape($ResourceGroupName) + '-b$')) { throw 'Target VM identities do not match the exact attempt A/B names.' }
if ($DeploymentId -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,191}$') { throw 'Invalid deployment identity.' }
if ($PollSeconds -lt 1 -or $PollSeconds -gt 300) { throw 'Poll interval outside finite bounds.' }
if ($RetryEndAt.Kind -eq [DateTimeKind]::Unspecified) { throw 'Retry end must specify UTC.' }
$RetryEndAt = $RetryEndAt.ToUniversalTime()
Connect-AzAccount -Identity -ErrorAction Stop | Out-Null
$targets = @($VmAResourceId, $VmBResourceId)
$outcomes = @()
foreach ($target in $targets) {
    $outcomes += [ordered]@{ resource_id = $target; state = 'pending'; attempts = 0; error = $null }
}
while ([DateTime]::UtcNow -lt $RetryEndAt -and @($outcomes | Where-Object { $_.state -notin @('deallocated') }).Count -gt 0) {
    for ($index = 0; $index -lt $targets.Count; $index++) {
        if ($outcomes[$index].state -eq 'deallocated') { continue }
        $target = $targets[$index]
        $outcomes[$index].attempts++
        try {
            $status = Invoke-AzRestMethod -Method GET -Path ($target + '?api-version=2025-04-01') -ErrorAction Stop
            if ($status.StatusCode -eq 404) { $outcomes[$index].state = 'absent_at_observation'; continue }
            if ($status.StatusCode -lt 200 -or $status.StatusCode -ge 300) { throw "VM read returned HTTP $($status.StatusCode)." }
            $response = Invoke-AzRestMethod -Method GET -Path ($target + '/instanceView?api-version=2025-04-01') -ErrorAction Stop
            $state = (($response.Content | ConvertFrom-Json).statuses | Where-Object { $_.code -like 'PowerState/*' } | Select-Object -First 1).code
            if ($state -eq 'PowerState/deallocated') { $outcomes[$index].state = 'deallocated'; continue }
            if ($state -eq 'PowerState/creating' -or -not $state) { $outcomes[$index].state = 'creating_or_unknown'; continue }
            $deallocate = Invoke-AzRestMethod -Method POST -Path ($target + '/deallocate?api-version=2025-04-01') -ErrorAction Stop
            if ($deallocate.StatusCode -eq 202) { $outcomes[$index].state = 'accepted_async' }
            elseif ($deallocate.StatusCode -ge 200 -and $deallocate.StatusCode -lt 300) { $outcomes[$index].state = 'requested' }
            else { throw "Deallocate returned HTTP $($deallocate.StatusCode)." }
        } catch { $outcomes[$index].state = 'failed'; $outcomes[$index].error = $_.Exception.Message }
    }
    if ([DateTime]::UtcNow -lt $RetryEndAt) { Start-Sleep -Seconds ([Math]::Min($PollSeconds, [Math]::Max(1, ($RetryEndAt - [DateTime]::UtcNow).TotalSeconds))) }
}
foreach ($outcome in $outcomes) {
    if ($outcome.state -ne 'deallocated' -and $outcome.state -ne 'failed') { $outcome.state = 'retry_window_ended_unresolved' }
}
[ordered]@{ deployment_id = $DeploymentId; finished_at = [DateTime]::UtcNow.ToString('o'); targets = $outcomes; dispatch_and_completion_guaranteed = $false } | ConvertTo-Json -Depth 5 -Compress
