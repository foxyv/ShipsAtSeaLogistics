# Starts the planner. Uses the py launcher because a bare "python" can resolve to the
# Microsoft Store alias stub, which fails with "cannot find the path specified".
Push-Location (Join-Path $PSScriptRoot "app")
try { py -m saslogi } finally { Pop-Location }
