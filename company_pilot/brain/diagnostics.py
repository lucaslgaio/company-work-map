"""Closed founder-safe rejection reasons; never render raw exceptions/model text."""
REJECTIONS={
 'task_relation_contradicted_by_source':'A fonte nega o repasse proposto; a relação não foi aplicada e o registro segue para revisão.',
 'task_relation_output_input_bridge_required':'O resultado e a entrada citados ainda não sustentam o mesmo repasse; a relação precisa ser conferida.',
 'task_relation_explicit_current_evidence_required':'A fonte não confirma o repasse entre essas atividades; a relação precisa ser esclarecida.',

 'marker_name_must_be_literal_in_source':'O nome proposto não corresponde ao trecho disponível. O operador precisa rever essa interpretação.',
 'app_only_or_generic_marker_not_authorized':'Foi proposto um aplicativo genérico como sinal de trabalho; é necessário um nome específico sustentado pela fonte.',
 'source_quote_not_found':'Um trecho citado não foi encontrado na fonte preservada. A interpretação não foi aplicada.',
 'unavailable_or_out_of_scope_source':'A interpretação citou uma fonte não fornecida para este processamento.',
 'stale_message_correction':'A tarefa mudou depois do recebimento; a correção precisa ser conferida com a versão atual.',
 'complete_prior_field_evidence_required':'A comparação não incluiu toda a evidência do campo anterior; o ponto segue sem confirmação.',
}
