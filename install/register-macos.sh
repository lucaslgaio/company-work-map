#!/bin/sh
set -eu
if [ "$(uname -s)" != "Darwin" ]; then
    echo 'Este passo é só para macOS. Use o script de Windows no Windows 11; Linux não é suportado.' >&2
    exit 2
fi
pilot_macos_version=$(sw_vers -productVersion)
pilot_macos_major=${pilot_macos_version%%.*}
case "$pilot_macos_major" in ''|*[!0-9]*) echo 'Não foi possível identificar a versão do macOS; registro bloqueado.' >&2; exit 2 ;; esac
if [ "$pilot_macos_major" -lt 14 ]; then
    echo 'O piloto requer macOS 14 ou posterior; registro bloqueado.' >&2
    exit 2
fi
if [ ! -x /usr/libexec/PlistBuddy ] || ! command -v launchctl >/dev/null 2>&1; then
    echo 'Falta PlistBuddy ou launchctl neste macOS. Confira as ferramentas do sistema antes de registrar o agente.' >&2
    exit 2
fi
pilot_startup_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
pilot_prefix=$(dirname "$pilot_startup_dir")
pilot_plist="$pilot_startup_dir/com.company-pilot.desktop.plist"
pilot_python=$(/usr/libexec/PlistBuddy -c 'Print :ProgramArguments:0' "$pilot_plist")
pilot_config=$(/usr/libexec/PlistBuddy -c 'Print :ProgramArguments:5' "$pilot_plist")
# Registers a diagnostic-only agent; capture is enabled separately after its own health passes.
PYTHONPATH="$pilot_prefix" "$pilot_python" -m company_pilot.desktop prepare-agent --config "$pilot_config"
# Explicit setup action only; never called by bootstrap or doctor.
launchctl bootstrap "gui/$(id -u)" "$pilot_plist"
launchctl enable "gui/$(id -u)/com.company-pilot.desktop"
launchctl print "gui/$(id -u)/com.company-pilot.desktop"
