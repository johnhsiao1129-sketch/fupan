# P3 Cleanup Script - delete after user authorization (use -Confirm to actually delete)
# Run dry-run (default):  powershell -ExecutionPolicy Bypass -File cleanup_p3.ps1
# Run actual delete:       powershell -ExecutionPolicy Bypass -File cleanup_p3.ps1 -Confirm

# Encoding: ASCII only (PowerShell 5.1 has UTF-8/Chinese parsing issues)

param(
    [switch]$Confirm
)

$ErrorActionPreference = "Stop"
$root = "D:\AI\my_programs\fupan"

$targets = @(
    @{
        Name   = ".git_corrupt_20260809"
        Path   = Join-Path $root ".git_corrupt_20260809"
        SizeMB = 1.7
        Why    = "git already re-cloned; backup is obsolete"
        Keep   = $false
    },
    @{
        Name   = "venv_broken_20260809"
        Path   = Join-Path $root "venv_broken_20260809"
        SizeMB = 230.6
        Why    = "site-packages corrupted; venv needs rebuild"
        Keep   = $false
    },
    @{
        Name   = ".codegraph"
        Path   = Join-Path $root ".codegraph"
        SizeMB = 0.1
        Why    = "CodeGraph index cache; can be regenerated"
        Keep   = $false
    },
    @{
        Name   = "backups"
        Path   = Join-Path $root "backups"
        SizeMB = 2.3
        Why    = "DB mirror backups; may contain usable snapshots pre-2026-08-08 wipe"
        Keep   = $true
    }
)

Write-Host "=== P3 Cleanup Preview ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "Directory sizes (current):"
Write-Host ("  {0,-30} {1,8} MB" -f ".git_corrupt_20260809", "1.7")
Write-Host ("  {0,-30} {1,8} MB" -f "venv_broken_20260809", "230.6")
Write-Host ("  {0,-30} {1,8} MB" -f ".codegraph", "0.1")
Write-Host ("  {0,-30} {1,8} MB" -f "backups", "2.3")
Write-Host ""
Write-Host "Reclaim (delete 3): ~232.4 MB" -ForegroundColor Green
Write-Host "Keep: backups/ (may contain usable history)" -ForegroundColor Yellow
Write-Host ""

if (-not $Confirm) {
    Write-Host "DRY RUN mode (no -Confirm = no deletion)" -ForegroundColor Yellow
    Write-Host ""
    foreach ($t in $targets) {
        if ($t.Keep) {
            Write-Host ("[KEEP] {0,-30} - {1}" -f $t.Name, $t.Why) -ForegroundColor Yellow
        } else {
            Write-Host ("[DEL ] {0,-30} ({1} MB) - would be removed" -f $t.Name, $t.SizeMB) -ForegroundColor Gray
        }
    }
    Write-Host ""
    Write-Host "To actually delete:" -ForegroundColor Cyan
    Write-Host "  powershell -ExecutionPolicy Bypass -File cleanup_p3.ps1 -Confirm"
    exit 0
}

Write-Host "=== Actual Deletion ===" -ForegroundColor Red
foreach ($t in $targets) {
    if ($t.Keep) {
        Write-Host ("[SKIP] {0}" -f $t.Name) -ForegroundColor Yellow
        continue
    }
    if (-not (Test-Path $t.Path)) {
        Write-Host ("[NONE] {0} (already gone)" -f $t.Name)
        continue
    }
    Write-Host ("[DEL ] {0} ({1} MB)..." -f $t.Name, $t.SizeMB) -NoNewline
    try {
        Remove-Item $t.Path -Recurse -Force -ErrorAction Stop
        Write-Host " OK" -ForegroundColor Green
    } catch {
        Write-Host (" FAILED: {0}" -f $_) -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "=== Done ===" -ForegroundColor Green
Write-Host "Verify: git status --ignored (should show backups/ ignored but not others)"
