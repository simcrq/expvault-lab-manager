param(
    [ValidateSet('status', 'start', 'stop', 'interfaces')]
    [string]$Action = 'status'
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding

function Get-LocalInterfaces {
    $privateIds = @()
    try {
        $sharing = New-Object -ComObject HNetCfg.HNetShare
        foreach ($connection in $sharing.EnumEveryConnection) {
            $configuration = $sharing.INetSharingConfigurationForINetConnection($connection)
            if ($configuration.SharingEnabled -and [int]$configuration.SharingConnectionType -eq 1) {
                $properties = $sharing.NetConnectionProps($connection)
                $privateIds += ([string]$properties.Guid).Trim('{}').ToLowerInvariant()
            }
        }
    } catch { }
    $result = @()
    foreach ($adapter in [System.Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces()) {
        if ($adapter.OperationalStatus -ne [System.Net.NetworkInformation.OperationalStatus]::Up) { continue }
        $hotspot = ($privateIds -contains $adapter.Id.Trim('{}').ToLowerInvariant()) -or
                   ($adapter.Description -match 'Wi-Fi Direct|Hosted Network')
        foreach ($entry in $adapter.GetIPProperties().UnicastAddresses) {
            if ($entry.Address.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork) { continue }
            $bytes = $entry.Address.GetAddressBytes()
            $private = $bytes[0] -eq 10 -or ($bytes[0] -eq 172 -and $bytes[1] -ge 16 -and $bytes[1] -le 31) -or
                       ($bytes[0] -eq 192 -and $bytes[1] -eq 168)
            if ($private) {
                $result += [pscustomobject]@{
                    address = $entry.Address.ToString()
                    name = $adapter.Name
                    hotspot = [bool]$hotspot
                }
            }
        }
    }
    return $result
}

function Await-WinRtOperation($Operation, [Type]$ResultType) {
    $method = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.IsGenericMethodDefinition -and
        $_.GetGenericArguments().Count -eq 1 -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    } | Select-Object -First 1
    if ($null -eq $method) { throw 'AsyncBridgeUnavailable' }
    $task = $method.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
    if (-not $task.Wait(25000)) { throw 'OperationTimeout' }
    return $task.GetAwaiter().GetResult()
}

try {
    if ($Action -eq 'interfaces') {
        @{ok = $true; interfaces = @(Get-LocalInterfaces)} | ConvertTo-Json -Depth 5 -Compress
        exit 0
    }
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    $null = [Windows.Networking.Connectivity.NetworkInformation, Windows.Networking.Connectivity, ContentType=WindowsRuntime]
    $null = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager, Windows.Networking.NetworkOperators, ContentType=WindowsRuntime]
    $resultType = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringOperationResult, Windows.Networking.NetworkOperators, ContentType=WindowsRuntime]
    $profile = [Windows.Networking.Connectivity.NetworkInformation]::GetInternetConnectionProfile()
    if ($null -eq $profile) {
        $profile = [Windows.Networking.Connectivity.NetworkInformation]::GetConnectionProfiles() | Select-Object -First 1
    }
    if ($null -eq $profile) {
        @{ok = $false; reason = 'NoConnectionProfile'; state = 'Unavailable'; interfaces = @(Get-LocalInterfaces)} | ConvertTo-Json -Depth 5 -Compress
        exit 0
    }
    $capability = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager]::GetTetheringCapabilityFromConnectionProfile($profile)
    if ($capability.ToString() -ne 'Enabled') {
        @{ok = $false; reason = $capability.ToString(); state = 'Unavailable'; interfaces = @(Get-LocalInterfaces)} | ConvertTo-Json -Depth 5 -Compress
        exit 0
    }
    $manager = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager]::CreateFromConnectionProfile($profile)
    $wasOn = $manager.TetheringOperationalState.ToString() -eq 'On'
    if ($Action -eq 'start' -and -not $wasOn) {
        $operation = Await-WinRtOperation ($manager.StartTetheringAsync()) $resultType
        if ($operation.Status.ToString() -ne 'Success') {
            @{ok = $false; reason = $operation.Status.ToString(); state = $manager.TetheringOperationalState.ToString(); interfaces = @(Get-LocalInterfaces)} | ConvertTo-Json -Depth 5 -Compress
            exit 0
        }
    } elseif ($Action -eq 'stop' -and $wasOn) {
        $operation = Await-WinRtOperation ($manager.StopTetheringAsync()) $resultType
        if ($operation.Status.ToString() -ne 'Success') {
            @{ok = $false; reason = $operation.Status.ToString(); state = $manager.TetheringOperationalState.ToString(); interfaces = @(Get-LocalInterfaces)} | ConvertTo-Json -Depth 5 -Compress
            exit 0
        }
    }
    $interfaces = @(Get-LocalInterfaces)
    if ($Action -eq 'start') {
        for ($attempt = 0; $attempt -lt 12 -and -not ($interfaces | Where-Object { $_.hotspot }); $attempt++) {
            Start-Sleep -Milliseconds 250
            $interfaces = @(Get-LocalInterfaces)
        }
    }
    $ssid = $null
    $password = $null
    try {
        $settings = $manager.GetCurrentAccessPointConfiguration()
        $ssid = $settings.Ssid
        $password = $settings.Passphrase
    } catch { }
    @{
        ok = $true
        state = $manager.TetheringOperationalState.ToString()
        was_on = [bool]$wasOn
        ssid = $ssid
        password = $password
        interfaces = $interfaces
    } | ConvertTo-Json -Depth 5 -Compress
} catch {
    # Do not echo exception strings, network passwords, or raw diagnostics.
    $reason = 'WindowsApiUnavailable'
    if ($_.Exception.Message -eq 'OperationTimeout') { $reason = 'OperationTimeout' }
    @{ok = $false; reason = $reason; state = 'Unknown'; interfaces = @(Get-LocalInterfaces)} | ConvertTo-Json -Depth 5 -Compress
}
