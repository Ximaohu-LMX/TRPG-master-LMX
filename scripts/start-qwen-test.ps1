param(
    [int]$BackendPort = 8001,
    [int]$FrontendPort = 9878
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$projectRoot = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $projectRoot 'trpg-backend'
$frontendDir = Join-Path $projectRoot 'trpg-frontend'
$pythonPath = Join-Path $backendDir '.venv\Scripts\python.exe'
$vitePath = Join-Path $frontendDir 'node_modules\vite\bin\vite.js'

function Invoke-SetupCommand {
    param(
        [string]$FileName,
        [string]$Arguments,
        [string]$WorkingDirectory,
        [hashtable]$Environment
    )

    $info = [System.Diagnostics.ProcessStartInfo]::new()
    $info.FileName = $FileName
    $info.Arguments = $Arguments
    $info.WorkingDirectory = $WorkingDirectory
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    foreach ($name in $Environment.Keys) {
        $info.Environment[$name] = $Environment[$name]
    }

    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $info
    [void]$process.Start()
    $stdout = $process.StandardOutput.ReadToEnd()
    $stderr = $process.StandardError.ReadToEnd()
    $process.WaitForExit()
    if ($process.ExitCode -ne 0) {
        throw ($stderr + $stdout).Trim()
    }
}

function Start-ServerProcess {
    param(
        [string]$FileName,
        [string]$Arguments,
        [string]$WorkingDirectory,
        [hashtable]$Environment
    )

    $info = [System.Diagnostics.ProcessStartInfo]::new()
    $info.FileName = $FileName
    $info.Arguments = $Arguments
    $info.WorkingDirectory = $WorkingDirectory
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    foreach ($name in $Environment.Keys) {
        $info.Environment[$name] = $Environment[$name]
    }

    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $info
    if (-not $process.Start()) {
        throw "Unable to start $FileName"
    }
}

function Get-AvailablePort {
    param([int]$PreferredPort)

    for ($port = $PreferredPort; $port -lt $PreferredPort + 20; $port++) {
        $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $port)
        try {
            $listener.Start()
            return $port
        } catch {
            continue
        } finally {
            $listener.Stop()
        }
    }
    throw "No available port near $PreferredPort"
}

$form = [System.Windows.Forms.Form]::new()
$form.Text = 'TRPG-master Qwen Test Launcher'
$form.Size = [System.Drawing.Size]::new(520, 300)
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false

$title = [System.Windows.Forms.Label]::new()
$title.Text = 'Enter a Qwen API key to start the local test environment'
$title.Location = [System.Drawing.Point]::new(24, 20)
$title.Size = [System.Drawing.Size]::new(460, 24)
$title.Font = [System.Drawing.Font]::new('Segoe UI', 11, [System.Drawing.FontStyle]::Bold)
$form.Controls.Add($title)

$hint = [System.Windows.Forms.Label]::new()
$hint.Text = 'The key is passed only to the local backend process and is never written to disk.'
$hint.Location = [System.Drawing.Point]::new(24, 50)
$hint.Size = [System.Drawing.Size]::new(460, 32)
$form.Controls.Add($hint)

$keyLabel = [System.Windows.Forms.Label]::new()
$keyLabel.Text = 'Qwen API Key'
$keyLabel.Location = [System.Drawing.Point]::new(24, 90)
$keyLabel.Size = [System.Drawing.Size]::new(120, 22)
$form.Controls.Add($keyLabel)

$keyBox = [System.Windows.Forms.TextBox]::new()
$keyBox.Location = [System.Drawing.Point]::new(24, 114)
$keyBox.Size = [System.Drawing.Size]::new(455, 26)
$keyBox.UseSystemPasswordChar = $true
$form.Controls.Add($keyBox)

$startButton = [System.Windows.Forms.Button]::new()
$startButton.Text = 'Start Qwen Test Environment'
$startButton.Location = [System.Drawing.Point]::new(24, 158)
$startButton.Size = [System.Drawing.Size]::new(200, 36)
$form.Controls.Add($startButton)

$status = [System.Windows.Forms.Label]::new()
$status.Location = [System.Drawing.Point]::new(24, 212)
$status.Size = [System.Drawing.Size]::new(455, 48)
$status.AutoEllipsis = $true
$form.Controls.Add($status)

$startButton.Add_Click({
    $apiKey = $keyBox.Text.Trim()
    if (-not $apiKey) {
        $status.Text = 'Enter a Qwen API key first.'
        return
    }
    if (-not (Test-Path $pythonPath)) {
        $status.Text = 'Missing trpg-backend/.venv. Run uv sync --locked once.'
        return
    }
    if (-not (Test-Path $vitePath)) {
        $status.Text = 'Missing frontend dependencies. Run npm ci once in trpg-frontend.'
        return
    }

    $node = Get-Command node -ErrorAction SilentlyContinue
    if ($null -eq $node) {
        $status.Text = 'Node.js was not found.'
        return
    }

    $activeBackendPort = Get-AvailablePort $BackendPort
    $activeFrontendPort = Get-AvailablePort $FrontendPort
    $backendUrl = "http://127.0.0.1:$activeBackendPort"
    $frontendUrl = "http://127.0.0.1:$activeFrontendPort"
    $backendEnvironment = @{
        'HOST_MODEL_PROVIDER' = 'qwen'
        'QWEN_API_KEY' = $apiKey
        'QWEN_BASE_URL' = 'https://dashscope.aliyuncs.com/compatible-mode/v1'
        'QWEN_MODEL' = 'qwen3.7-plus'
        'CORS_ORIGINS' = ('["{0}","http://localhost:{1}"]' -f $frontendUrl, $FrontendPort)
    }

    try {
        $startButton.Enabled = $false
        $status.Text = 'Migrating the database and loading Paper Chase...'
        [System.Windows.Forms.Application]::DoEvents()
        Invoke-SetupCommand $pythonPath '-m alembic upgrade head' $backendDir $backendEnvironment
        Invoke-SetupCommand $pythonPath 'scripts\load_paper_chase.py' $backendDir $backendEnvironment

        $status.Text = 'Starting backend and frontend...'
        [System.Windows.Forms.Application]::DoEvents()
        Start-ServerProcess $pythonPath "-m uvicorn app.main:app --host 127.0.0.1 --port $activeBackendPort" $backendDir $backendEnvironment
        Start-ServerProcess $node.Source "node_modules\vite\bin\vite.js --host 127.0.0.1 --port $activeFrontendPort" $frontendDir @{ 'VITE_API_BASE_URL' = "$backendUrl/api/v1" }

        $status.Text = "Ready: frontend $frontendUrl  backend $backendUrl/docs"
        $keyBox.Clear()
    } catch {
        $status.Text = "Startup failed: $($_.Exception.Message)"
    } finally {
        $startButton.Enabled = $true
    }
})

[void]$form.ShowDialog()
