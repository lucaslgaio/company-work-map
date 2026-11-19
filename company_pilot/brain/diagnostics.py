"""Founder-safe rejection reasons. Unknown stable codes are kept raw, never free text."""
import re

_CODE = re.compile(r"[a-z][a-z0-9_]{0,79}")

REJECTIONS={
 'task_relation_contradicted_by_source':'A fonte nega o repasse proposto; a relação não foi aplicada e o registro segue para revisão.',
 'task_relation_output_input_bridge_required':'O resultado e a entrada citados ainda não sustentam o mesmo repasse; a relação precisa ser conferida.',
 'task_relation_explicit_current_evidence_required':'A fonte não confirma o repasse entre essas atividades; a relação precisa ser esclarecida.',

 'marker_name_must_be_literal_in_source':'O nome proposto não corresponde ao trecho disponível. O operador precisa rever essa interpretação.',
 'app_only_or_generic_marker_not_authorized':'Foi proposto um aplicativo genérico como sinal de trabalho; é necessário um nome específico sustentado pela fonte.',
 'source_quote_not_found':'Um trecho citado não foi encontrado na fonte preservada. A interpretação não foi aplicada.',
 'source_not_previously_accepted_or_outside_bound':'A leitura citou uma fonte que este trabalho não autoriza. A interpretação não foi aplicada.',
 'observation_task_bound_required':'A captura precisa de uma a três tarefas, ou outcome no_work quando não há trabalho (aba nova, Finder, Ajustes ou título vazio).',
 'no_work_requires_empty_tasks':'outcome no_work não pode trazer tarefas. Envie tasks vazio, ou descreva o trabalho de verdade.',
 'no_work_cannot_add_map_content':'Uma captura sem trabalho não pode acrescentar contexto, nomes ou ligações. Deixe esses campos vazios.',
 'invalid_observation_outcome':'O resultado da captura só pode omitir outcome ou usar no_work.',
 'unavailable_or_out_of_scope_source':'A interpretação citou uma fonte não fornecida para este processamento.',
 'stale_message_correction':'A tarefa mudou depois do recebimento; a correção precisa ser conferida com a versão atual.',
 'complete_prior_field_evidence_required':'A comparação não incluiu toda a evidência do campo anterior; o ponto segue sem confirmação.',
 'invalid_analysis':'O JSON não segue o contrato desta análise. Confira o schema no arquivo do trabalho e envie de novo.',
}


def is_rejection_code(code):
    return isinstance(code, str) and _CODE.fullmatch(code) is not None


def rejection_label(code):
    """Portuguese text when known; the raw code when it is a stable token; else None."""
    if not is_rejection_code(code):
        return None
    return REJECTIONS.get(code, code)
