# 从 IIS 日志增量汇总公开统计，私有状态保存在网站目录之外。
param(
    [string]$LogDirectory = 'C:\inetpub\logs\LogFiles\W3SVC2',
    [string]$StateDirectory = 'C:\AZIMUTH-Web\statistics',
    [string]$OutputPath = 'C:\AZIMUTH-Web\statistics\public\stats.json'
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$mutex = New-Object Threading.Mutex($false, 'Global\AZIMUTH-Web-Statistics')
$locked = $false
try {
    try { $locked = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $locked = $true }
    if (-not $locked) { return }
    New-Item -ItemType Directory -Force -Path $StateDirectory,(Split-Path $OutputPath) | Out-Null
    $statePath = Join-Path $StateDirectory 'state.json'
    if (Test-Path -LiteralPath $statePath) {
        $state = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
    } else {
        $state = [pscustomobject]@{schema=1; visits=0L; downloads=0L; since=$null; salt=[Guid]::NewGuid().ToString('N'); files=[pscustomobject]@{}; recent=[pscustomobject]@{}}
    }
    $recent = @{}
    foreach ($p in $state.recent.PSObject.Properties) { $recent[$p.Name] = [long]$p.Value }
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        foreach ($file in (Get-ChildItem -LiteralPath $LogDirectory -Filter '*.log' -File | Sort-Object Name)) {
            $previous = $state.files.PSObject.Properties[$file.Name]
            $skip = if ($previous) { [long]$previous.Value } else { 0L }
            $number = 0L
            $fields = @{}
            $stream = [IO.File]::Open($file.FullName, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
            $terminated = $true
            if ($stream.Length -gt 0) { $stream.Position = $stream.Length - 1; $terminated = $stream.ReadByte() -eq 10; $stream.Position = 0 }
            $reader = New-Object IO.StreamReader($stream)
            try {
                while ($null -ne ($line = $reader.ReadLine())) {
                    # IIS 正在写入的最后一行留到下一轮，避免半行被标记为已处理。
                    if ($reader.EndOfStream -and -not $terminated) { break }
                    $number++
                    if ($line.StartsWith('#Fields: ')) {
                        $names = $line.Substring(9).Split(' ')
                        $fields = @{}
                        for ($i=0; $i -lt $names.Length; $i++) { $fields[$names[$i]] = $i }
                    }
                    if ($number -le $skip -or $line.StartsWith('#') -or -not $line -or $fields.Count -eq 0) { continue }
                    $v = $line.Split(' ')
                    if ($v.Length -lt $fields.Count) { throw 'Incomplete IIS log record; statistics left unchanged.' }
                    if ($v[$fields['cs-method']] -ne 'GET') { continue }
                    $status = $v[$fields['sc-status']]
                    if ($status -notin @('200','206','304')) { continue }
                    $path = $v[$fields['cs-uri-stem']].ToLowerInvariant()
                    $page = $path -in @('/','/index.html','/plugins.html','/skins.html')
                    $download = $path -eq '/downloads/azimuth-v1.0-setup.exe'
                    if (-not ($page -or $download)) { continue }
                    $agent = $v[$fields['cs(User-Agent)']]
                    # 排除可识别的机器人和技术探测，不将它们当作用户访问。
                    if ($agent -eq '-' -or $agent -match '(?i)bot|spider|crawler|python|curl|wget|httpclient|go-http|headless|uptime|monitor') { continue }
                    $date = [DateTime]::ParseExact(($v[$fields['date']]+' '+$v[$fields['time']]), 'yyyy-MM-dd HH:mm:ss', [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::AssumeUniversal).ToUniversalTime()
                    if (-not $state.since -or $date -lt [DateTime]::Parse($state.since).ToUniversalTime()) { $state.since = $date.ToString('o') }
                    if ($page -and $status -in @('200','304')) { $state.visits = [long]$state.visits + 1L }
                    if ($download -and $status -in @('200','206') -and $v[$fields['sc-win32-status']] -eq '0') {
                        $identity = $state.salt + '|' + $v[$fields['c-ip']] + '|' + $agent
                        $key = [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($identity))).Replace('-','')
                        $seconds = [long](($date - [DateTime]'1970-01-01').TotalSeconds)
                        if (-not $recent.ContainsKey($key) -or $seconds - $recent[$key] -ge 1800) { $state.downloads = [long]$state.downloads + 1L }
                        $recent[$key] = $seconds
                    }
                }
            } finally { $reader.Dispose() }
            $state.files | Add-Member -MemberType NoteProperty -Name $file.Name -Value ([Math]::Max($skip,$number)) -Force
        }
    } finally { $sha.Dispose() }
    $cutoff = [long](([DateTime]::UtcNow.AddDays(-2) - [DateTime]'1970-01-01').TotalSeconds)
    $kept = @{}
    foreach ($key in $recent.Keys) { if ($recent[$key] -ge $cutoff) { $kept[$key] = $recent[$key] } }
    $state.recent = [pscustomobject]$kept
    $public = [ordered]@{visits=[long]$state.visits; downloads=[long]$state.downloads; since=$state.since; updatedAt=[DateTime]::UtcNow.ToString('o'); source='iis'; downloadWindowMinutes=30}
    # 原子替换文件，读取统计时不会遇到只写了一半的 JSON。
    foreach ($item in @(@{Path=$statePath; Data=$state}, @{Path=$OutputPath; Data=$public})) {
        $temporary = $item.Path + '.tmp'
        [IO.File]::WriteAllText($temporary, ($item.Data | ConvertTo-Json -Depth 8 -Compress), (New-Object Text.UTF8Encoding($false)))
        if (Test-Path -LiteralPath $item.Path) { [IO.File]::Replace($temporary, $item.Path, $null) } else { [IO.File]::Move($temporary, $item.Path) }
    }
    $public | ConvertTo-Json -Compress
} finally {
    if ($locked) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
