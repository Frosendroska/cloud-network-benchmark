param([Parameter(Mandatory=$true)][string]$RunbookPath)
$ErrorActionPreference = 'Stop'
$global:vmReads = @{ a = 0; b = 0 }
$global:viewReads = @{ a = 0; b = 0 }
$global:posts = @{ a = 0; b = 0 }
function Connect-AzAccount { param([switch]$Identity, $ErrorAction); if (-not $Identity) { throw 'managed identity was not requested' } }
function Start-Sleep { param([int]$Seconds) }
function Invoke-AzRestMethod {
    param([string]$Method, [string]$Path, [string]$ErrorAction)
    $role = if ($Path -match '/f06-run-a(?:/|\?|$)') { 'a' } elseif ($Path -match '/f06-run-b(?:/|\?|$)') { 'b' } else { throw "unexpected target path: $Path" }
    if ($Method -eq 'GET' -and $Path -notmatch '/instanceView') {
        $global:vmReads[$role]++
        if ($role -eq 'a' -and $global:vmReads[$role] -eq 1) { return [pscustomobject]@{ StatusCode = 404; Content = '{}' } }
        return [pscustomobject]@{ StatusCode = 200; Content = '{}' }
    }
    if ($Method -eq 'GET' -and $Path -match '/instanceView') {
        $global:viewReads[$role]++
        $state = if ($global:posts[$role] -gt 0) { 'deallocated' } elseif ($role -eq 'b' -and $global:viewReads[$role] -eq 1) { 'creating' } else { 'running' }
        return [pscustomobject]@{ StatusCode = 200; Content = (@{ statuses = @(@{ code = "PowerState/$state" }) } | ConvertTo-Json -Compress -Depth 4) }
    }
    if ($Method -eq 'POST' -and $Path -match '/deallocate\?') {
        $global:posts[$role]++
        return [pscustomobject]@{ StatusCode = 202; Content = '{}' }
    }
    throw "unexpected request $Method $Path"
}
$subscription = '00000000-0000-0000-0000-000000000001'
$group = 'f06-run'
$base = "/subscriptions/$subscription/resourceGroups/$group/providers/Microsoft.Compute/virtualMachines/"
$result = & $RunbookPath -VmAResourceId ($base + 'f06-run-a') -VmBResourceId ($base + 'f06-run-b') -SubscriptionId $subscription -ResourceGroupName $group -DeploymentId 'f06-run-deployment' -RetryEndAt ([DateTime]::UtcNow.AddMinutes(2)) -PollSeconds 1
$result
