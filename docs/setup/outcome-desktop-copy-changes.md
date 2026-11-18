# Desktop copy contribution for root consolidation

Internal input to the revised founder/team setup; not a second guide. Root owns the final documents and copy gate. These passages describe the implemented desktop commands and visible states.

## Participant-facing choices and readback

> Antes da primeira captura, cada participante escolhe se participa e se permite imagens, dentro do limite autorizado pela empresa. No próprio computador, revisa as expressões que devem excluir contextos pessoais e acrescenta seus nomes de contas, documentos ou outros sinais pessoais. O piloto começa com “cartão pessoal”, “conta pessoal” e “e-mail pessoal”; a lista não bloqueia todo banco nem todo navegador. Uma janela pessoal sem um desses sinais ainda pode ficar sem identificação.
>
> Os termos pessoais ficam neste computador. O brain recebe apenas as escolhas e a confirmação da versão desse arquivo. Sem a confirmação, com arquivo alterado ou com acesso empresarial pendente, a captura fica bloqueada. A escolha de não participar não modifica por si só quem já tem acesso aos registros da empresa; o fundador confere essa política separadamente.
>
> Confira o resultado antes de iniciar: participação efetiva, imagens efetivas, exclusões locais confirmadas, acesso empresarial verificado e agente pronto. Se uma escolha não puder ser aplicada, o setup informa o motivo e mantém a captura desativada.

## Exact assistant command and files

The assistant obtains the participant's own choices, reviews the default phrases and writes a **local private** JSON input:

```json
{
  "participating": true,
  "screenshots": false,
  "reviewed_defaults": true,
  "personal_exclusions": ["expressão privada escolhida pela pessoa"]
}
```

Use actual choices; these booleans are an example, not defaults to apply silently.

```sh
python -m company_pilot.desktop privacy --config CONFIG --choices-file PRIVATE_CHOICES.json
```

This saves `personal-policy.json` and `participation-choices.json` beside CONFIG and updates CONFIG's absolute `private_policy_file`. Do not copy the private input or `personal-policy.json` into the company repository, shared team setup or model context. The emitted list is for local review only. Share only `participation-choices.json` through the root CLI as the same authenticated participant:

```sh
python -m company_pilot --config BRAIN_CONFIG policy participation --json-file PARTICIPATION_CHOICES --event-id UUID
python -m company_pilot.desktop status --config CONFIG
```

The top-level invocation matches root's current parser on main; the desktop invocation/file formats above are implemented here. Re-fetch policy/status after recording the acknowledgement. A policy-version change requires renewed local review/acknowledgement. Keep existing documented capture/agent-health success checks; saved choices alone never prove native readiness.

`status.controls` exposes `capture_allowed`, `reason`, `policy_version`, `screenshots_allowed`, `local_exclusions_required`, `local_exclusions_bound`, `exclusion_count` and `compatibility`. It does not emit personal terms. `local_development_fixture_not_real_consent` labels old local fixtures. `status.coverage` shows unknown sample/window periods, expired unresolved metadata and pending expiry updates. `capture_enabled` reports the saved switch separately from effective permission.

## Setup values and failure recovery

Successful desktop configure returns `configured_values.repository`, `state_branch`, `mode`, `device_id` and `config_path`. Generate the team command from these verified values. Do not substitute example repository/branch names or enable synthetic mode after a company configuration failure.

> Se o destino falhar, confira repositório, ramificação e modo emitidos pelo setup. “Ramificação não localizada”, “brain não inicializado” e “modo diferente” exigem correções distintas. Nenhuma dessas falhas autoriza trocar para modo de teste. O diagnóstico também separa sistema, versão de Python, ferramentas locais e saúde do agente em segundo plano.
>
> Uma lacuna de contexto mostra quantas janelas e amostras este coletor observou sem ligação empresarial confirmada. A ausência de observações não comprova inatividade. Metadados temporários expiram em 24 horas; o aviso de expiração não recupera o conteúdo anterior. Exclusões pessoais não entram nessa contagem.

For integration prose use “verificado em teste local sintético” for this candidate. Native macOS/Windows, actual unattended startup and online delivery require their separate receipts; this patch supplies no new proof of those boundaries.
