$ErrorActionPreference = 'Stop'
# Only the pilot's own task is stopped; observations and user documents remain.
Disable-ScheduledTask -TaskName 'CompanyPilotDesktop' | Out-Null
Stop-ScheduledTask -TaskName 'CompanyPilotDesktop'
