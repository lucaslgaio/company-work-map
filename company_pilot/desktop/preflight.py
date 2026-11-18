"""o6/I1/I5/I6/I7: local facts and safe, actionable failure explanations."""
import os
from pathlib import Path
import platform
import shutil
import sys


def platform_check():
    system = platform.system()
    version = platform.mac_ver()[0] if system == "Darwin" else platform.release()
    supported, code = system in {"Darwin", "Windows"}, "platform_supported"
    if not supported:
        code = "unsupported_platform"
        message = "Sistema não suportado: " + system + ". Use macOS 14+ ou Windows 11+."
    elif system == "Darwin":
        try: supported = int(version.split(".")[0]) >= 14
        except (ValueError, IndexError): supported = False
        code = "platform_supported" if supported else "macos_version_unsupported"
        message = "macOS " + (version or "não identificado") + (" compatível." if supported else ": este piloto requer macOS 14 ou posterior.")
    else:
        try: build = sys.getwindowsversion().build
        except AttributeError: build = None
        supported = build is not None and build >= 22000
        code = "platform_supported" if supported else "windows_version_unsupported"
        message = "Windows " + (str(build) if build is not None else "não identificado") + (" compatível." if supported else ": este piloto requer Windows 11 ou posterior.")
    return {"system": system, "version": version, "supported": supported, "code": code, "message": message,
            "next_step": None if supported else "Use um computador com um dos sistemas suportados; não execute o registro de início automático neste sistema."}


def runtime_check():
    supported = sys.version_info >= (3, 12)
    version = ".".join(str(x) for x in sys.version_info[:3])
    return {"version": version, "supported": supported, "code": "python_ready" if supported else "python_312_required",
            "message": "Python " + version + (" compatível." if supported else ": requer Python 3.12 ou posterior."),
            "next_step": None if supported else "Instale Python 3.12+ e repita este comando com esse interpretador."}


def executable_check(name):
    path = shutil.which(name)
    return {"available": bool(path), "path": path}


def local_tools(config, system, injected_native=False):
    tools = {}
    transport = config.get("transport", {})
    if transport.get("kind") == "github":
        gh = transport.get("gh", "gh")
        tools["github_cli"] = {**executable_check(gh), "next_step": "Instale o GitHub CLI e entre com a própria conta; não copie a credencial do proprietário."}
    if injected_native:
        tools["native_helper"] = {"available": True, "evidence": "injected_fixture_not_native_verification"}
    elif system == "Darwin":
        path = Path(__file__).parent / "native" / "mac-capture"
        tools["native_helper"] = {"available": path.is_file() and os.access(path, os.X_OK), "path": str(path),
                                  "next_step": "Execute o instalador entregue para compilar o helper; o build por fonte requer as Command Line Tools da Apple."}
    elif system == "Windows":
        tools["powershell"] = {**executable_check("powershell.exe"), "next_step": "Verifique a disponibilidade do Windows PowerShell nesta sessão do Windows."}
        helper = Path(__file__).parent / "native" / "windows-capture.ps1"
        tools["native_helper"] = {"available": helper.is_file(), "path": str(helper), "next_step": "Reinstale os arquivos entregues do piloto."}
    for check in tools.values():
        if check.get("available") is True:
            check.pop("next_step", None)
    return tools


def failure_details(error, config=None):
    """Return only known codes; never echo arbitrary backend exception strings."""
    config = config or {}
    code = str(error)
    kind = type(error).__name__
    messages = {
        "software_repository_is_not_company_store": ("Este destino contém o código do software na raiz; não é o repositório de registros da empresa.", "Peça ao operador o link privado preparado da empresa. Preserve a configuração existente e mantenha captura desativada."),
        "private_company_repository_required": ("O destino precisa ser o repositório privado da empresa.", "Confira o link preparado com o operador; a origem do software e o destino dos registros são separados."),
        "company_release_unavailable": ("Não foi possível confirmar a identidade do repositório da empresa.", "Confira sua própria conta e o link preparado com o operador; preserve os arquivos e mantenha captura desativada."),
        "python_312_required": ("Python incompatível.", "Use Python 3.12 ou posterior."),
        "unsupported_platform": ("Sistema não suportado.", "Use macOS 14+ ou Windows 11+."),
        "configuration_exists": ("Já existe uma configuração neste caminho.", "Inspecione status e os valores existentes antes de escolher outro caminho ou substituir a configuração."),
        "invalid_repository": ("Repositório inválido.", "Use o valor completo proprietário/repositório emitido pelo setup do fundador."),
        "invalid_state_branch": ("Ramificação inválida.", "Use o valor exato de ramificação emitido pelo setup; não troque para modo de teste."),
        "brain_not_initialized": ("O brain da ramificação escolhida ainda não está inicializado.", "Confira com o setup do fundador os valores exatos de repositório, ramificação e modo; mantenha a captura desativada."),
        "synthetic_real_boundary_mismatch": ("Modo da configuração diferente do modo do brain.", "Obtenha os valores corretos do setup. Não use synthetic/development para contornar uma configuração de empresa."),
        "state_branch_missing": ("A ramificação de dados não foi localizada neste repositório.", "Use o repositório e a ramificação efetivamente verificados no setup do fundador; mantenha a captura desativada."),
        "repository_write_access_required": ("A conta autenticada não pode contribuir neste repositório.", "Peça ao responsável pelo setup que confira o acesso efetivo para esta identidade; não use a credencial do dono."),
        "repository_contribution_no_longer_permitted": ("O acesso de contribuição foi retirado.", "Mantenha a captura desativada e confira o acesso efetivo da própria conta."),
        "fixture_requires_development": ("Uma fixture foi indicada fora do modo de desenvolvimento.", "Use somente arquivos sintéticos no teste explícito; para a empresa use a configuração emitida pelo setup."),
        "select_github_or_explicit_fixture": ("A origem da configuração não foi definida de forma válida.", "Use --repo com os valores reais do setup ou uma fixture explicitamente sintética; não misture os dois caminhos."),
        "online_transport_not_configured": ("O transporte online não está configurado.", "Configure o repositório e a ramificação reais emitidos pelo setup do fundador."),
        "device_policy_unavailable": ("Não foi possível obter uma política vigente.", "Confira a conexão e a própria autenticação. A captura permanece bloqueada quando a política não é válida."),
        "device_policy_refused_or_invalid": ("A política foi recusada ou não pôde ser validada.", "Confira o acesso efetivo e a política do participante; uma recusa não é contornada pelo cache."),
        "brain_access_or_integrity_denied": ("O acesso ou a integridade do brain foi recusado.", "Mantenha a captura desativada e solicite a verificação do proprietário; preserve os registros locais."),
        "native_helper_missing": ("O helper nativo não está disponível.", "Execute o instalador entregue no sistema suportado e repita o diagnóstico."),
        "permission_denied": ("O helper não tem permissão de Acessibilidade.", "Conceda Acessibilidade ao executável correto nas configurações do sistema e repita o diagnóstico do agente registrado."),
        "screen_permission_denied": ("A captura de imagem não tem permissão do sistema.", "Se a empresa e você permitirem imagens, confira a permissão do executável registrado e repita o diagnóstico."),
        "private_choices_required": ("Faltam escolhas explícitas do participante.", "Registre participating, screenshots e reviewed_defaults como booleanos no arquivo privado de escolhas."),
        "private_defaults_review_required": ("As exclusões pessoais iniciais ainda não foram revisadas.", "Revise as expressões e inclua seus contextos pessoais antes de confirmar reviewed_defaults."),
        "invalid_private_exclusions": ("A lista privada de exclusões é inválida.", "Use até 100 expressões pessoais, cada uma entre 3 e 200 caracteres; mantenha o arquivo neste computador."),
        "fixture_clock_requires_development_once": ("O relógio de teste não é permitido neste caminho.", "Use --clock apenas com --development --fixture --once e o receptor local de desenvolvimento. O agente de produção usa o relógio do sistema."),
    }
    if code in messages:
        message, action = messages[code]
    elif kind == "GitHubError" and getattr(error, "status", None) == 404:
        code = "repository_or_branch_not_found"
        message, action = "Repositório ou ramificação não localizado para esta conta.", "Confira repositório, ramificação e acesso a partir dos valores reais do setup; não adivinhe outro modo."
    elif isinstance(error, FileNotFoundError):
        code = "required_file_or_tool_missing"
        message, action = "Falta um arquivo ou executável necessário.", "Confira o caminho da configuração e as ferramentas indicadas no preflight."
    elif kind in {"AccessDenied", "PermanentDeliveryError"}:
        code = "access_or_policy_denied"
        message, action = "O acesso ou a política não permite concluir esta etapa.", "Confira o acesso efetivo e as escolhas do participante; não reutilize credenciais de outra pessoa."
    else:
        code = "configuration_or_runtime_error"
        message, action = "A etapa não foi concluída.", "Execute doctor com a mesma configuração e preserve o identificador do evento para uma repetição idêntica."
    return {"code": code, "message": message, "next_step": action}
