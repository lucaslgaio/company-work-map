#!/usr/bin/env python3
"""Install delivered sources in an isolated user prefix. No implicit capture/startup."""
import argparse
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import sys
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from company_pilot.desktop.preflight import platform_check, runtime_check


def stable_interpreter(executable=None):
    current = Path(os.path.abspath(executable or sys.executable))
    for name in ("python3", "python"):
        candidate = shutil.which(name)
        if candidate and Path(candidate).is_absolute():
            try:
                if os.path.samefile(candidate, current) and "/Cellar/" not in candidate:
                    return Path(os.path.abspath(candidate))
            except OSError:
                continue
    return current


def startup_files(prefix: Path, config: Path, interpreter: Path, target: str):
    arguments = [str(interpreter), "-m", "company_pilot.desktop", "agent", "--config", str(config)]
    if target == "Darwin":
        return "com.company-pilot.desktop.plist", plistlib.dumps({
            "Label": "com.company-pilot.desktop", "ProgramArguments": arguments,
            "WorkingDirectory": str(prefix), "EnvironmentVariables": {"PYTHONPATH": str(prefix), "COMPANY_PILOT_AGENT_ID": "com.company-pilot.desktop"},
            "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 60,
            "StandardOutPath": str(prefix / "runtime.log"), "StandardErrorPath": str(prefix / "runtime-error.log"),
        })
    if target == "Windows":
        # The registration script inserts the current Windows user's exact SID.
        from subprocess import list2cmdline
        commandline = list2cmdline(arguments[1:])
        xml = '<?xml version="1.0" encoding="UTF-16"?>\n' + f'''<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
<Triggers><LogonTrigger><Enabled>true</Enabled><UserId>__CURRENT_USER_SID__</UserId></LogonTrigger></Triggers>
<Principals><Principal id="User"><UserId>__CURRENT_USER_SID__</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
<Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><ExecutionTimeLimit>PT0S</ExecutionTimeLimit><RestartOnFailure><Interval>PT1M</Interval><Count>3</Count></RestartOnFailure></Settings>
<Actions Context="User"><Exec><Command>{escape(str(interpreter))}</Command><Arguments>{escape(commandline)}</Arguments><WorkingDirectory>{escape(str(prefix))}</WorkingDirectory></Exec></Actions></Task>'''
        return "company-pilot-desktop.xml", xml.encode("utf-16")
    raise ValueError("unsupported_platform")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Instalar os arquivos entregues do piloto")
    parser.add_argument("--prefix", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--skip-native-build", action="store_true")
    args = parser.parse_args(argv)
    system_check, python_check = platform_check(), runtime_check()
    if not system_check["supported"]:
        parser.error(system_check["message"] + " " + python_check["message"])
    if not python_check["supported"]:
        parser.error(python_check["message"] + " " + python_check["next_step"])
    if platform.system() == "Darwin" and not args.skip_native_build and not shutil.which("xcrun"):
        parser.error("Faltam as Command Line Tools da Apple para compilar o helper. Instale-as e repita; nenhum serviço foi registrado.")
    if platform.system() == "Windows" and not shutil.which("powershell.exe"):
        parser.error("Windows PowerShell não foi localizado. Verifique a ferramenta antes de instalar; nenhum serviço foi registrado.")
    source = Path(__file__).resolve().parent.parent
    prefix = args.prefix.expanduser().resolve()
    if prefix.exists() and any(prefix.iterdir()) and not (prefix / "installation.json").is_file():
        parser.error("Escolha uma pasta vazia ou uma instalação existente do piloto.")
    if prefix == source or source in prefix.parents or prefix in source.parents:
        parser.error("A instalação deve ficar fora da árvore do código-fonte.")
    prefix.mkdir(parents=True, exist_ok=True, mode=0o700)
    shutil.copytree(source / "company_pilot", prefix / "company_pilot", dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    native_result = "not_built"
    if platform.system() == "Darwin" and not args.skip_native_build:
        native = prefix / "company_pilot" / "desktop" / "native"
        result = subprocess.run(["xcrun", "swiftc", "-parse-as-library", "-target", platform.machine() + "-apple-macos14.0", "-module-cache-path", str(prefix / "build-cache"), str(native / "mac-capture.swift"), "-o", str(native / "mac-capture")], capture_output=True, text=True)
        if result.returncode:
            native_result = "build_failed"
        else:
            native_result = "compiled_not_capture_verified"
    elif platform.system() == "Windows":
        native_result = "powershell_source_installed_not_native_verified"
    config = (args.config or prefix / "config.json").resolve()
    startup = prefix / "startup"
    startup.mkdir(exist_ok=True)
    interpreter = stable_interpreter()
    name, data = startup_files(prefix, config, interpreter, platform.system())
    (startup / name).write_bytes(data)
    for script in ("register-macos.sh", "register-windows.ps1", "disable-startup-macos.sh", "disable-startup-windows.ps1"):
        shutil.copy2(source / "install" / script, startup / script)
    receipt = {"schema_version": 1, "prefix": str(prefix), "python": sys.version.split()[0], "interpreter": str(interpreter), "config": str(config), "native": native_result,
               "startup": "files_generated_not_registered", "screen_capture_started": False, "online_configured": False}
    (prefix / "installation.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False))
    return 2 if native_result == "build_failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
