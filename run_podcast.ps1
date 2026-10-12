param(
    [string]$Config,
    [string[]]$Audio,
    [string]$Output = 'podcast_output',
    [switch]$InstallAI,
    [switch]$SkipAI,
    [ValidateSet('sequence', 'multitrack')][string]$InputMode,
    [string]$EditPlan,
    [string]$TranscriptJson,
    [switch]$OpenAudition,
    [switch]$RequireTechnicalQA
)
$ErrorActionPreference = 'Stop'
$pipeline = Join-Path $PSScriptRoot '.codex\skills\multi-speaker-podcast\scripts\podcast_pipeline.py'
$python = (Get-Command python -ErrorAction Stop).Source
$pipelineArgs = @($pipeline)
if ($Config) {
    $pipelineArgs += @('--config', $Config)
}
if ($Audio) {
    $pipelineArgs += @('--input') + $Audio
}
if (-not $Config -and -not $Audio) {
    Write-Host 'Usage: .\run_podcast.ps1 -Config .\examples\podcast_config.example.json'
    Write-Host 'Or: .\run_podcast.ps1 -Audio recording.wav -Output outputs\episode01 -InstallAI'
    exit 2
}
if (-not $Config -or $PSBoundParameters.ContainsKey('Output')) {
    $pipelineArgs += @('--output-dir', $Output)
}
if ($InstallAI) { $pipelineArgs += '--install-ai-deps' }
if ($SkipAI) { $pipelineArgs += @('--skip-ai', '--no-install-ai-deps') }
if ($InputMode) { $pipelineArgs += @('--input-mode', $InputMode) }
if ($EditPlan) { $pipelineArgs += @('--edit-plan', $EditPlan) }
if ($TranscriptJson) { $pipelineArgs += @('--transcript-json', $TranscriptJson) }
if ($OpenAudition) { $pipelineArgs += @('--enable-audition', '--attempt-au-import') }
if ($RequireTechnicalQA) { $pipelineArgs += '--require-technical-qa' }
& $python @pipelineArgs
exit $LASTEXITCODE
