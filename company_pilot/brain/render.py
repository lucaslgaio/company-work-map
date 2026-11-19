"""Portuguese read models derived from canonical records, never a second map."""
from __future__ import annotations

import json

from .engine import FIELDS, LABELS, TOPICS
from .knowledge import marker_readback, recognition_gap
from .quotes import MANUAL_INTERVIEW_MESSAGE, awaiting_manual_interview
from .readout import questions_nora_would_ask, shown_person, shown_time

KINDS_PT = {"organization": "empresa", "responsibility": "responsabilidade", "job": "trabalho", "process": "processo", "task": "tarefa", "person": "pessoa", "tracking_record": "registro de acompanhamento", "project": "projeto", "observation": "observação", "answer": "resposta", "interview": "conversa inicial", "analysis": "interpretação", "coverage_gap": "contexto ainda desconhecido", "correction": "correção"}
KINDS_PT.update(disambiguation="esclarecimento da resposta", answer_assignment="vínculo de resposta")
KINDS_PT.update(interview_correction="correção de contexto pelo fundador", interview_deferred="contexto declarado desconhecido pelo fundador")
KINDS_PT.update(message="mensagem do fundador", work_object="objeto do trabalho", company_record="registro da empresa")
EDGES_PT = {"contains": "inclui", "performed_by": "executada por", "handoff": "repassa para", "depends_on": "depende de", "tracks": "acompanhada em", "pays_for": "paga por"}


def _founder_source_text(source):
    """What the founder reads. An analysis record stays in Portuguese, without the JSON."""
    text = source.get("text") or ""
    if source.get("kind") == "analysis":
        try:
            data = json.loads(text)
        except (TypeError, ValueError):
            data = None
        if isinstance(data, dict):
            lines = []
            for task in data.get("tasks") or []:
                lines.append("Atualizou a atividade «" + (task.get("name") or "uma atividade") + "».")
                for link in task.get("links") or []:
                    evidence = link.get("evidence") or []
                    quote = evidence[0].get("quote") if evidence else ""
                    if quote:
                        lines.append("Propôs um repasse com a frase «" + quote + "».")
            if not lines:
                lines.append("A leitura não acrescentou uma atividade nova.")
            return "\n".join("> " + clean(line) for line in lines) + "\n\n"
    return "\n".join("> " + clean(line) for line in text.splitlines()) + "\n\n"


def clean(value):
    text = str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    for character in ("\\", "`", "*", "_", "[", "]", "!", "#", "|"):
        text = text.replace(character, "\\" + character)
    return text


def render(state, store):
    files = {}
    warning = "**Exemplo sintético de desenvolvimento; não descreve uma empresa real.**\n\n" if state.company["synthetic"] else ""
    active = [task for task in state.tasks.values() if not task.get("superseded_by")]
    pending = [question for question in state.questions.values() if question["status"] not in ("resolved", "superseded", "awaiting_person")]
    delegated = [question for question in state.questions.values() if question["status"] == "awaiting_person"]
    jobs = [job for job in state.jobs.values() if job["status"] != "applied"]
    home = f"# {clean(state.company['name'])}\n\n" + warning
    home += "Este mapa descreve o trabalho aprendido nas fontes disponíveis. As áreas ainda não observadas e as dúvidas permanecem explícitas.\n\n"
    home += "- [O que a empresa faz](views/empresa.md)\n- [Quem faz o quê](views/pessoas.md)\n- [Mapa por pessoa](views/mapa.md)\n- [Processos e tarefas](views/tarefas.md)\n- [Leitura do trabalho](views/insights.md)\n- [Leitura de segunda](views/leitura-segunda.md)\n- [Perguntas pendentes](views/perguntas.md)\n- [Fontes e correções](views/fontes.md)\n- [Cérebro da empresa](notes/indice.md)\n- [Processamento pendente](views/fila.md)\n- [Histórico de mudanças](views/historico.md)\n"
    home += "\nA análise funciona quando o computador do operador está disponível. Capturas recebidas e respostas aguardam na fila enquanto o operador ou o modelo não estiver disponível.\n"
    home += "\n[Escolhas de coleta e acesso](views/controles.md). A política pedida e a permissão efetiva são verificações separadas.\n"
    home += "\n[Registros da empresa e revisões de origem](views/registros.md). Um arquivo descreve o que foi registrado; não comprova sozinho quem executou ou aprovou uma atividade.\n"
    files["README.md"] = home
    withheld = [source for source in state.sources.values() if source.get("safe_metadata", {}).get("privacy_disposition") == "withheld"]
    coverage = "# Cobertura\n\n" + warning
    coverage += "O computador de trabalho compartilha a captura como trabalho da empresa. O que é claramente pessoal fica só nesse computador e não aparece aqui.\n\n"
    if not withheld:
        coverage += "Não há registro antigo sem conteúdo. O mapa é preenchido pelas capturas compartilhadas.\n"
    else:
        coverage += (
            f"Há {len(withheld)} registro(s) antigo(s) sem conteúdo compartilhado. "
            "Eles não são atividades, não entram no mapa e não geram pergunta; "
            "não declara atividade pessoal nem trabalho conhecido.\n\n"
        )
        shown = withheld[:8]
        for source in shown:
            when = shown_time(source.get("received_at"))
            who = shown_person(source.get("actor") or "")
            coverage += f"- {clean(when or 'horário não registrado')}, {clean(who)}: conteúdo não compartilhado.\n"
        if len(withheld) > len(shown):
            coverage += f"\nE mais {len(withheld) - len(shown)} do mesmo tipo, omitidos desta página para não encobrir o mapa.\n"
    files["views/cobertura.md"] = coverage
    context = "# O que a empresa faz\n\n" + warning
    labels = {"activities": "Atividades", "people": "Pessoas e responsabilidades", "stages": "Etapas relevantes", "tracking": "Andamento, pagamentos e saldos", "documents": "Documentos úteis"}
    for topic in TOPICS:
        claim = state.context.get(topic)
        context += f"## {labels[topic]}\n\n"
        deferred = state.interview.get("deferred_topics", {}).get(topic)
        if deferred and deferred["status"] == "pending":
            context += "**Este tópico continua sem confirmação. A entrevista pode avançar; o esclarecimento permanece pendente com Nora.**\n\n"
            context += "Relato preservado: " + clean(deferred["literal_text"]) + f". [Abrir fonte](sources/{deferred['source_id']}.md); [abrir pergunta](questions/{deferred['question_id']}.md).\n\n"
        if not claim:
            context += "Ainda não esclarecido.\n\n"
        else:
            context += clean(claim["value"]) + "\n\n" + _references(claim.get("evidence", []), "") + "\n"
    for conflict in state.interview.get("context_conflicts", []):
        if conflict.get("status") == "resolved" and str(conflict.get("resolution", "")).startswith("repair_"):
            status = "Os dois relatos foram mantidos; a diferença não era uma contradição"
        elif conflict.get("status") == "resolved":
            status = "Esclarecido pelo fundador; os dois relatos foram preservados"
        else:
            status = "Aguardando esclarecimento"
        context += f"\n**Relatos conflitantes — {labels[conflict['topic']]}:** {clean(conflict['previous']['value'])} / {clean(conflict['reported_candidate']['value'])}. {status}.\n"
    signals = [value for key, value in state.context.items() if key.startswith("work-signal:")]
    if signals:
        context += "\n## Contexto de captura esclarecido pelo fundador\n\n"
        for signal in signals:
            context += clean(signal["value"]) + "\n\n" + _references(signal["evidence"], "")
    files["views/empresa.md"] = context
    markers = "# Nomes usados para reconhecer trabalho\n\n" + warning
    readback_markers = marker_readback(state.context, state.recognition_markers)
    for marker in readback_markers:
        if marker["status"] == "active":
            markers += f"- {clean(marker['value'])}: {clean(marker['kind'])}.\n" + _references(marker["evidence"], "")
            if marker.get("origin") == "legacy_literal_claim":
                markers += "Nome literal preservado de uma fonte anterior; confirmar no readback do setup.\n"
    if not readback_markers:
        markers += "Nenhum nome específico foi confirmado para reconhecimento. Um nome confirmado ajuda a ligar a janela ao projeto. A captura do computador de trabalho entra no mapa mesmo sem esse nome; o que é claramente pessoal continua só no computador.\n"
        gap = recognition_gap({"context": state.context, "recognition_markers": state.recognition_markers,
                               "nodes": list(state.groups["nodes"].values()), "sources": list(state.sources.values()),
                               "company": state.company})
        if gap:
            markers += "\n" + clean(gap["message"]) + "\n\nComando, com a configuração já usada: setup confirm-names --name NOME --kind TIPO. Tipos: record_name, record_ref, company_alias, business_account. Só entram os nomes que o fundador passar.\n"
            for candidate in gap["candidates"]:
                markers += f"\n- {clean(candidate['name'])}: ainda não confirmado; tipo {clean(candidate['kind'])}.\n"
    files["views/reconhecimento.md"] = markers
    policy = state.company.get("access_policy")
    controls = "# Escolhas de coleta e acesso\n\n" + warning
    controls += "O fundador escolhe quem pode ler e modificar os registros; cada participante confirma sua própria coleta e imagens. Exclusões pessoais ficam no computador da pessoa.\n\n"
    if policy:
        controls += "Estado da política: " + clean(policy["state"]) + ". A opção pedida não amplia automaticamente a permissão real.\n\n"
        for request in policy["requests"]:
            controls += f"- {clean(request['actor_id'])}: leitura pedida {clean(request['read_scope'])}; alteração pedida {clean(request['modify_scope'])}.\n"
        for effective in policy.get("effective", []):
            controls += f"- Efetivo — {clean(effective['actor_id'])}: leitura {clean(effective['read_scope'])}; alteração {clean(effective['modify_scope'])}; estado {clean(effective['state'])}.\n"
    else:
        controls += "Acesso escolhido e verificado ainda não registrado. Dados de exemplo não representam consentimento real.\n"
    for actor_id, choices in state.company.get("participation", {}).items():
        controls += f"\n{clean(actor_id)}: participação {'aceita' if choices['participating'] else 'recusada'}; imagens {'aceitas pela pessoa' if choices['screenshots'] else 'recusadas pela pessoa'}; versão reconhecida {choices['acknowledged_policy_version']}.\n"
    controls += "\n[Nomes efetivamente reconhecidos](reconhecimento.md). Configuração desconhecida, não suportada ou não verificada mantém a captura pendente.\n"
    files["views/controles.md"] = controls
    tasks = "# Processos e tarefas\n\n" + warning
    if not active:
        tasks += "Nenhuma tarefa foi descrita a partir das observações disponíveis. Isso não significa que a empresa não tenha trabalho.\n"
    for task in active:
        tasks += f"- [{clean(task['name'])}](tasks/{task['id']}.md) — {'provisória' if task['status'] == 'provisional' else 'estabelecida'}, versão {task['version']}.\n"
    for node in state.groups["nodes"].values():
        if not node.get("superseded_by") and node["kind"] != "person":
            tasks += f"\n{clean(KINDS_PT[node['kind']])}: {clean(node['name'])}.\n"
    activities = state.context.get("activities")
    if activities and activities.get("value"):
        tasks += "\n## O que a entrevista já descreveu\n\n" + clean(activities["value"]) + "\n"
    files["views/tarefas.md"] = tasks
    people = "# Quem faz o quê\n\n" + warning
    for task in active:
        performer = task["fields"].get("performer", {}).get("value") or "Responsável ainda não esclarecido"
        identity = task.get("performer_identity", {})
        person = state.groups["nodes"].get(identity.get("node_id"))
        if person and person.get("superseded_by"):
            person = state.groups["nodes"].get(person["superseded_by"][0], person)
        performer = person["name"] if person else identity.get("name") or performer
        people += f"- {clean(performer)}: [{clean(task['name'])}](tasks/{task['id']}.md).\n"
    described = state.context.get("people")
    if described and described.get("value"):
        people += "\n## Pessoas descritas na entrevista\n\n" + clean(described["value"]) + "\n"
    named = {line.split(":", 1)[0].replace("- ", "").strip() for line in people.splitlines() if line.startswith("- ")}
    for node in state.groups["nodes"].values():
        if node["kind"] == "person" and not node.get("superseded_by") and node["name"] not in named:
            people += f"\n- {clean(node['name'])}.\n"
    if not active and not (described and described.get("value")) and not any(node["kind"] == "person" and not node.get("superseded_by") for node in state.groups["nodes"].values()):
        people += "Responsabilidades ainda não observadas.\n"
    files["views/pessoas.md"] = people
    for task in state.tasks.values():
        page = f"# {clean(task['name'])}\n\n" + warning
        page += f"Situação: **{'provisória' if task['status'] == 'provisional' else 'estabelecida'}**. Versão {task['version']}.\n\n"
        if task.get("superseded_by"):
            page += "Esta entrada foi substituída por: " + ", ".join(f"[tarefa sucessora]({tid}.md)" for tid in task["superseded_by"]) + ". A base anterior continua preservada.\n\n"
        for field in FIELDS:
            claim = task["fields"].get(field, {})
            value = claim.get("value") or "Ainda não esclarecido"
            basis = {"observed": "observado", "reported": "relatado", "inferred": "inferência"}.get(claim.get("basis"), "sem base")
            if field in task.get("conflicts", {}):
                conflict = task["conflicts"][field]
                candidate = conflict["reported_candidate"]
                page += f"## {LABELS[field]}\n\n**Em conflito; o valor atual não está confirmado.**\n\n"
                page += "Relato anterior: " + clean(value) + ".\n\n" + _references(claim.get("evidence", []), "../")
                page += "Outro relato: " + clean(candidate["value"]) + ".\n\n" + _references(candidate["evidence"], "../")
                for alternative in conflict.get("alternatives", []):
                    page += "Relato ainda não conciliado: " + clean(alternative["value"]) + ".\n\n" + _references(alternative["evidence"], "../")
            else:
                page += f"## {LABELS[field]}\n\n{clean(value)} ({basis}).\n\n"
                page += _references(claim.get("evidence", []), "../") + "\n"
            if field == "performer" and task.get("performer_identity", {}).get("qualifier"):
                page += "Papel informado nesta tarefa: " + clean(task["performer_identity"]["qualifier"]) + ".\n\n"
        page += "## Conexões\n\n"
        if task.get("payment"):
            page += "Este registro foi identificado como pagamento.\n\n"
            for field, label in (("object", "O que foi pago"), ("tracking_record", "Registro e saldo")):
                claim = task["payment"][field]
                page += label + ": " + clean(claim["value"] or "ainda não esclarecido") + ".\n\n" + _references(claim["evidence"], "../")
        for issue in task.get("consistency_issues", []):
            page += "**Inconsistência pendente:** " + clean(issue["description"]) + ".\n\n" + _references(issue["evidence"], "../")
        for resolved in task.get("resolved_issues", []):
            page += "**Inconsistência esclarecida pelo fundador:** " + clean(resolved["resolution"]["value"]) + ".\n\n" + _references(resolved["resolution"]["evidence"], "../")
        for replay in task.get("issue_replays", []):
            if replay.get("status") == "awaiting_clarification":
                page += "**Este ponto ainda aguarda interpretação:** «" + clean(replay["description"]) + "». A passagem também sustenta uma divergência já esclarecida; ainda não foi confirmado se este é outro ponto ou uma repetição. A tarefa permanece provisória.\n\n" + _references(replay["evidence"], "../")
        links = [edge for edge in state.groups["edges"].values() if task["id"] in (edge["from"], edge["to"]) and edge.get("active")]
        for edge in links:
            other_id = edge["to"] if edge["from"] == task["id"] else edge["from"]
            other = state.tasks.get(other_id) or state.groups["nodes"].get(other_id)
            if other and other.get("superseded_by"):
                other = state.groups["nodes"].get(other["superseded_by"][0], other)
            page += f"- {clean(EDGES_PT[edge['kind']])}: {clean(other['name'] if other else other_id)}.\n"
            if edge.get("basis") == "inferred":
                page += "  Vínculo provisório; aguarda confirmação.\n"
        for omitted in task.get("omitted_relations") or []:
            other = state.tasks.get(omitted.get("target_id"))
            other_name = other["name"] if other else "outra atividade"
            page += f"- Um repasse proposto para {clean(other_name)} ficou de fora: {clean(omitted.get('reason', 'a fonte não confirma'))}. A captura desta atividade foi mantida.\n"
        if not links and not task.get("omitted_relations"):
            page += "Conexões tipadas ainda não registradas. Consulte os campos de encaixe, repasse e acompanhamento acima.\n"
        page += "\n## Evidências e versões\n\n"
        for source_id in task["evidence_ids"]:
            page += f"- [Fonte preservada](../sources/{source_id}.md).\n"
        for version in range(1, task["version"] + 1):
            path = f"records/tasks/{task['id']}/v{version:06}.json"
            if path not in getattr(state, "files", {}):
                continue
            page += f"- [Registro da versão {version}](../../{path}).\n"
        files[f"views/tasks/{task['id']}.md"] = page
    questions = "# Perguntas pendentes\n\n" + warning
    asking = questions_nora_would_ask(state)
    if not asking:
        questions += "A Nora não tem uma pergunta nova para mandar. Campo vazio de formulário não entra aqui, e um repasse que o mapa já mostra não é perguntado de novo.\n"
    for _tier, _name, _prompt, question in asking:
        questions += f"- [Pergunta](questions/{question['id']}.md): {clean(question['prompt'])}\n"
    if delegated:
        questions += "\n## Aguardando outra pessoa\n\n"
        for question in delegated:
            questions += "- Aguardando " + clean(question.get("response_disposition", {}).get("delegate_to", "pessoa indicada")) + ": " + clean(question["activity_summary"]) + ". O ponto segue sem esclarecimento; nenhum contato foi enviado automaticamente.\n"
    historic_questions = [question for question in state.questions.values() if question["status"] in ("resolved", "superseded")]
    if historic_questions:
        questions += "\n## Esclarecimentos anteriores e seus envios\n\n"
        for question in historic_questions:
            questions += f"- [Esclarecimento preservado](questions/{question['id']}.md): {clean(question['activity_summary'])}.\n"
    files["views/perguntas.md"] = questions
    for question in state.questions.values():
        page = "# Esclarecimento\n\n" + warning + clean(question["prompt"]) + "\n\n"
        status = {"pending": "pendente", "awaiting_person": "aguardando pessoa indicada", "resolved": "resolvida", "superseded": "substituída", "partially_answered": "parcialmente respondida"}.get(question["status"], question["status"])
        target = "../empresa.md" if question.get("target_kind", "").startswith("context") else f"../tasks/{question['task_id']}.md"
        if question.get("target_kind") == "answer_assignment":
            target = f"../sources/{question['unassigned_answer_id']}.md"
        page += f"Situação: **{status}**. [Abrir contexto]({target}).\n\n"
        disposition = question.get("response_disposition")
        if question.get("priority_hint"):
            reason = question["priority_hint"]["reason"]
            page += "O fundador pediu atenção a este ponto: " + clean(reason["value"]) + ".\n\n" + _references(reason["evidence"], "../")
        if question.get("application", {}).get("status") == "unresolved":
            page += "**A resposta foi preservada, mas ainda não esclarece este ponto.** Motivo: " + clean(question["application"]["reason"]) + ".\n\n"
        if question.get("resolution"):
            page += "Esclarecimento aplicado: " + clean(question["resolution"]["value"]) + ".\n\n" + _references(question["resolution"]["evidence"], "../")
        for evidence in question.get("evidence", []):
            page += "Trecho que fundamenta a pergunta: «" + clean(evidence["quote"]) + "».\n\n" + _references([evidence], "../")
        for round in state.nora.get("rounds", {}).values():
            for item in round.get("items", []):
                if item.get("question", {}).get("id") != question["id"]:
                    continue
                labels = {"prepared": "Preparada; envio ainda não confirmado", "sending": "Tentativa de envio em curso", "accepted": "Envio aceito pelo canal", "unknown": "Resultado do envio desconhecido", "failed": "Envio falhou", "expired": "Preparação expirada"}
                page += "\n## Seleção registrada por Nora\n\n" + clean(labels.get(round.get("status"), round.get("status"))) + ". Horário da rodada: " + clean(shown_time(round.get("slot")) or "horário não registrado") + ".\n\n"
                chosen = item.get("why_chosen") or {}
                if chosen:
                    page += "Motivo registrado na seleção: " + clean(chosen.get("reason", "não informado")) + ".\n\n"
                    if chosen.get("focus_description"):
                        page += "Ponto perguntado: " + clean(chosen["focus_description"]) + ".\n\n"
                else:
                    page += "Esta rodada histórica não registrou motivo estruturado; ele não foi reconstruído.\n\n"
                for evidence in item.get("evidence", []):
                    page += "Evidência usada naquela seleção: «" + clean(evidence["quote"]) + "».\n\n" + _references([evidence], "../")
                page += "Texto preservado da rodada:\n\n" + clean(round.get("text", "")) + "\n\n"
        if disposition:
            page += "Resposta registrada: " + clean(disposition["kind"]) + "; responsável indicado: " + clean(disposition.get("delegate_to") or "fundador") + ". A lacuna real permanece visível até esclarecimento.\n\n"
        for sid in question["evidence_ids"]:
            page += f"- [Fonte que fundamenta a pergunta](../sources/{sid}.md).\n"
        answers = [source for source in state.sources.values() if source["kind"] == "answer" and question["id"] in source.get("question_ids", [])]
        for answer in answers:
            page += f"\nResposta de {clean(shown_person(answer.get('speaker')))}: [ler resposta](../sources/{answer['id']}.md).\n"
        assignments = [source for source in state.sources.values() if source["kind"] == "answer_assignment" and question["id"] in source.get("question_ids", [])]
        for assignment in assignments:
            page += f"\n[Resposta original vinculada](../sources/{assignment['original_answer_id']}.md); [fundamento do vínculo](../sources/{assignment['id']}.md).\n"
        files[f"views/questions/{question['id']}.md"] = page
    sources = "# Fontes e correções\n\n" + warning
    gaps = [source for source in state.sources.values() if source.get("kind") == "coverage_gap"]
    if gaps:
        sources += f"- {len(gaps)} registro(s) antigo(s) sem conteúdo. Não são atividades e não geram pergunta. O detalhe resumido está em [cobertura](cobertura.md).\n"
    for source in state.sources.values():
        if source.get("kind") == "coverage_gap":
            continue
        sources += f"- [{clean(KINDS_PT.get(source['kind'], source['kind']))}: {clean(shown_time(source.get('received_at')) or 'horário não registrado')}](sources/{source['id']}.md).\n"
        page = "# Fonte preservada\n\n" + warning
        page += f"Tipo: {clean(KINDS_PT.get(source['kind'], source['kind']))}. Recebida em {clean(shown_time(source.get('received_at')) or 'horário não registrado')}.\n\n"
        page += f"Registrada por {clean(shown_person(source.get('actor')))}.\n\n"
        if source["kind"] == "analysis":
            page += "Esta leitura usou o texto da captura. Ela não liga uma conta e não envia mensagem.\n\n"
        if source.get("speaker"):
            page += f"Relatada por {clean(shown_person(source.get('speaker')))}. Um relato não se torna observação por confirmação.\n\n"
        page += "## Conteúdo\n\n" + _founder_source_text(source) + "\n"
        if source.get("source_record"):
            record = source["source_record"]
            page += "Origem: " + clean(record["system"]) + "; registro " + clean(record["record_id"]) + "; revisão " + clean(record["revision"]) + ".\n\nReferência original: " + clean(record["url"]) + ".\n\n"
            page += "Extração textual " + ("completa conforme o conector" if source.get("extract_complete") else "parcial; o binário completo não foi interpretado") + ".\n\n"
            catalog = state.company_records.get(source["logical_record_id"], {})
            if catalog.get("state") == "removed":
                page += "**Registro removido na origem.** Esta versão continua preservada; a remoção não prova que o trabalho deixou de existir.\n\n"
        page += f"SHA-256 do texto: `{source['sha256']}`.\n"
        if source.get("attachment"):
            page += f"\n[Captura preservada](../../{source['attachment']['path']}).\n"
        files[f"views/sources/{source['id']}.md"] = page
    files["views/fontes.md"] = sources
    catalog = "# Registros da empresa\n\n" + warning
    for record in state.company_records.values():
        catalog += f"- {clean(record['source_system'])}: {clean(record['record_id'])}; revisão {clean(record['revision'])}; {'removido na origem' if record['state'] == 'removed' else 'disponível na origem'}. [Fonte preservada](sources/{record['current_source_id']}.md); {len(record['history'])} revisões preservadas.\n"
    if not state.company_records:
        catalog += "Nenhum registro de arquivo/banco foi recebido. A ausência no catálogo não comprova que a empresa não possua outros dados.\n"
    files["views/registros.md"] = catalog
    queue = "# Processamento pendente\n\n" + warning
    queue += "Os itens abaixo estão preservados e aguardam o operador. A ausência de descrição pronta não significa ausência de atividade.\n\n"
    if not jobs:
        queue += "Fila sem itens pendentes.\n"
    for job in jobs:
        reason = job.get("reason") or ("Falha no processamento; o operador tentará novamente" if job.get("error_code") else "aguardando operador")
        if awaiting_manual_interview(job):
            reason = reason + " " + MANUAL_INTERVIEW_MESSAGE
        status = "aguardando esclarecimento em uma pergunta agendada" if job["status"] in ("unassigned", "awaiting_disambiguation") else "aguardando processamento"
        queue += f"- {clean(KINDS_PT[job['kind']])}; recebido em {clean(shown_time(job.get('created_at')) or 'horário não registrado')}; tentativas {job['attempts']}; situação {status}; motivo {reason}.\n"
    files["views/fila.md"] = queue
    history = "# Histórico de mudanças\n\n" + warning
    history += "Os registros anteriores e as fontes permanecem preservados. As descrições atuais são geradas a partir deles.\n\n"
    for path in sorted(p for p in state.files if p.startswith("ledger/")):
        history += f"- [Mudança registrada](../{path}).\n"
    files["views/historico.md"] = history
    files["views/para.md"] = "# Navegação por projetos, áreas, recursos e arquivo\n\n" + warning + "Projetos e responsabilidades podem organizar a leitura; o mapa e suas evidências continuam nos mesmos registros. Esta visão não define departamentos nem um ciclo obrigatório.\n\n" + "\n".join(f"- {clean(node['name'])}" for node in state.groups["nodes"].values() if node["kind"] in ("project", "responsibility")) + "\n"
    from .readout import insights, lanes, monday_readout
    from .vault import write_vault
    files["views/mapa.md"] = warning + lanes(state)
    files["views/insights.md"] = warning + insights(state)
    files["views/leitura-segunda.md"] = warning + monday_readout(state)
    note_files, task_slugs = write_vault(state)
    files.update(note_files)
    for task_id, slug in task_slugs.items():
        page = files.get(f"views/tasks/{task_id}.md")
        if page:
            files[f"views/tasks/{task_id}.md"] = page + f"\nNota no cérebro: [[{slug}]](../../notes/tarefas/{slug}.md).\n"
    cited = set()
    for task in state.tasks.values():
        cited.update(task.get("evidence_ids") or [])
    for node in state.groups["nodes"].values():
        cited.update(ref.get("source_id") for ref in node.get("evidence") or [])
    for claim in list(state.context.values()) + [record.get("claim") or {} for record in state.context_claims]:
        cited.update(ref.get("source_id") for ref in (claim or {}).get("evidence") or [])
    unmapped = ["# Capturas ainda sem cartão\n\n" + warning]
    for source in state.sources.values():
        if source["id"] in cited or source.get("kind") != "observation":
            continue
        if (source.get("safe_metadata") or {}).get("privacy_disposition") in ("personal", "excluded", "withheld"):
            continue
        when = shown_time((source.get("observation") or {}).get("occurred_at") or source.get("received_at"))
        who = shown_person(source.get("actor") or "")
        excerpt = " ".join((source.get("text") or "").split())[:180]
        unmapped.append(f"- Em {clean(when or 'um horário não registrado')}, {clean(who)}: {clean(excerpt)} Esta captura ainda não virou um cartão.\n")
    if len(unmapped) == 1:
        unmapped.append("Toda captura compartilhada já aparece num cartão ou ainda está na fila.\n")
    files["views/sem-cartao.md"] = "\n".join(unmapped)
    return files


def _references(evidence, prefix):
    return "\n".join(f"- [Fonte]({prefix}sources/{ref['source_id']}.md): {clean(ref['quote'])}" for ref in evidence) + ("\n" if evidence else "")
