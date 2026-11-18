$ErrorActionPreference = 'Stop'
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) { throw 'Este passo é só para Windows 11; não foi registrado nenhum agente.' }
if ([Environment]::OSVersion.Version.Build -lt 22000) { throw 'O piloto requer Windows 11 ou posterior; registro bloqueado.' }
if (-not (Get-Command Register-ScheduledTask -ErrorAction SilentlyContinue)) { throw 'O Agendador de Tarefas não está disponível nesta sessão; confira as ferramentas do Windows.' }
$pilotPrefix = Split-Path $PSScriptRoot -Parent
$pilotInstallation = Get-Content (Join-Path $pilotPrefix 'installation.json') -Raw | ConvertFrom-Json
$env:PYTHONPATH = $pilotPrefix
& $pilotInstallation.interpreter -m company_pilot.desktop prepare-agent --config $pilotInstallation.config
if ($LASTEXITCODE -ne 0) { throw 'Conclua a configuração antes de registrar o diagnóstico automático.' }
$pilotSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$pilotXml = Get-Content (Join-Path $PSScriptRoot 'company-pilot-desktop.xml') -Raw
$pilotXml = $pilotXml.Replace('__CURRENT_USER_SID__', $pilotSid)
Register-ScheduledTask -TaskName 'CompanyPilotDesktop' -Xml $pilotXml | Out-Null
Start-ScheduledTask -TaskName 'CompanyPilotDesktop'
Get-ScheduledTaskInfo -TaskName 'CompanyPilotDesktop'
