"""Conservative source bridges for model-proposed task-to-task relations."""
import re
import unicodedata
from .semantics import normalized
from .stores import BrainError

STOP = {
    'para', 'com', 'que', 'uma', 'por', 'das', 'dos', 'nao', 'sem', 'este', 'esta',
    'isso', 'essa', 'esse', 'sobre', 'como', 'mais', 'quando', 'onde', 'pelo', 'pela',
    'entre', 'tarefa', 'atividade', 'trabalho', 'resultado', 'entrada', 'depois', 'antes',
}


def _phrase(value):
    return r'(?<!\w)' + re.escape(value) + r'(?!\w)'


def _contains(text, value):
    return bool(value and re.search(_phrase(value), text))


def _content(value):
    return [token for token in re.findall(r'\w+', normalized(value or '')) if len(token) >= 4 and token not in STOP]


def _direction(clause, output, names):
    """Require a stated recipient or consumer, not a transfer verb anywhere."""
    obj = _phrase(output)
    delivery = r'\b(?:entrega|entregou|entregam|entregue|encaminha|encaminhou|encaminhei|encaminhada|encaminhado|repassa|repassou|envia|enviou|enviei|enviado|enviada|passa|passou|passei|segue|seguiu|manda|mandou|mandei)\b'
    for name in names:
        if not name:
            continue
        target = _phrase(name)
        patterns = (
            obj + r'.*?\b(?:entrada|insumo)\s+(?:da|de|do|para)\b.*?' + target,
            obj + r'.*?\b(?:inicia|iniciou|alimenta)\b.*?' + target,
            delivery + r'.*?' + obj + r'.*?\b(?:para|a|ao|pra)\b.*?' + target,
            obj + r'.*?' + delivery + r'.*?\b(?:para|a|ao|pra)\b.*?' + target,
            obj + r'.*?\bvai\s+(?:para|pra)\b.*?' + target,
            target + r'.*?\b(?:recebe|recebeu|usa|utiliza|depende)\b.*?' + obj,
        )
        if any(re.search(pattern, clause) for pattern in patterns):
            return True
    return False


def _stems(value):
    stems = set()
    for token in re.findall(r'\w+', normalized(value or '')):
        if len(token) >= 5 and token not in STOP:
            stems.add(token[:4])
    return stems


def _role_stems(task, keys):
    fields = task.get('fields') or {}
    parts = [task.get('name') or '']
    for key in keys:
        parts.append((fields.get(key) or {}).get('value') or '')
    return _stems(' '.join(str(part or '') for part in parts))


# A passing verb. "vai à obra" is motion, not a handoff.
_PASSING = re.compile(
    r'\b(?:mandou|manda|mando|mandei|envia|enviou|enviei|encaminhou|encaminha|encaminhei|'
    r'passa|passou|passei|entrega|entregou|repassa|repassou)\b')
_MOTION = re.compile(
    r'\bvai\s+(?:a|à|ao|para|pra)\s+(?:a\s+|o\s+)?(?:obra|escritorio|casa|rua|reuniao|predio|apto)\b')
_PERSON_SEQUENCE = re.compile(
    r'\b(?:confere|conferiu|monta|montou|rascunha|revisa|reviso)\b.{0,80}\be\b.{0,50}\b(?:eu|ele|ela)\b')


def _fold_lines(value):
    """Lowercase, strip accents, and keep line breaks. The first word is not the only word."""
    lines = []
    for line in str(value or '').split('\n'):
        folded = unicodedata.normalize('NFKD', line.casefold())
        folded = ''.join(char for char in folded if not unicodedata.combining(char))
        lines.append(' '.join(folded.split()))
    return '\n'.join(lines)


def _spots(text, stem):
    start = 0
    while True:
        at = text.find(stem, start)
        if at < 0:
            return
        yield at
        start = at + len(stem)


def _sentences(text):
    return [piece.strip() for piece in re.split(r'[.!?\n]', _fold_lines(text)) if piece.strip()]


def _stem_hit(sentence, value):
    return bool(_stems(value) & _stems(sentence))


def _governs(sentence, output, names):
    """The verb in this sentence moves the upstream result toward the downstream activity or person."""
    has_object = _stem_hit(sentence, output)
    has_target = any(_stem_hit(sentence, name) for name in names if name)
    if not has_object or not has_target:
        return False
    if _MOTION.search(sentence) and not _PASSING.search(sentence):
        return False
    if _PASSING.search(sentence) or _PERSON_SEQUENCE.search(sentence):
        return True
    if re.search(r'\bvai\s+(?:para|pra)\b', sentence):
        return True
    doing = re.search(r'\bvai\s+([a-z]{4,})', sentence)
    return bool(doing and any(doing.group(1)[:4] in name for name in names if name))


def _governed_transfer(quote, output, names):
    return any(_governs(sentence, output, names) for sentence in _sentences(quote))


def _transfer_between(text, start, end):
    """A person or result actually moves between these two words. A bare vai does not."""
    span = text[start:end]
    if '\n' in span and re.search(r'(?m)^[-*]|\w+:', span):
        return True
    if _MOTION.search(span) and not _PASSING.search(span):
        return False
    if _PASSING.search(span) or _PERSON_SEQUENCE.search(span):
        return True
    return bool(re.search(r'\bvai\s+(?:para|pra)\b', span))


def _named_sequence(quote, task, target):
    """The quote names the producer and, after that, the consumer.

    An earlier copy of the consumer's word does not count. 'cotação' in the
    first paragraph of an email does not come before 'Revisão técnica' just
    because it is the first match. The span between the two words has to be a
    transfer or a later step in the same register.
    """
    text = _fold_lines(quote)
    quoted = _stems(text.replace('\n', ' '))
    own_left = (_role_stems(task, ('action', 'result')) & quoted) - _role_stems(target, ('action',))
    own_right = (_role_stems(target, ('action',)) & quoted) - _role_stems(task, ('action', 'result'))
    if not own_left or not own_right:
        return False
    for left in own_left:
        for left_at in _spots(text, left):
            for right in own_right:
                for right_at in _spots(text, right):
                    if left_at < right_at and _transfer_between(text, left_at, right_at):
                        return True
    return False


def _shared_cited_object(output, incoming, quotes):
    """The same cited object in the producer's result and the consumer's input.

    The full result sentence does not have to appear verbatim in the quote.
    """
    quoted = set()
    for quote in quotes:
        quoted.update(_content(quote))
    produced = set(_content(output))
    consumed = set(_content(incoming))
    cited = produced & consumed & quoted
    if any(len(token) >= 5 for token in cited) or len(cited) >= 2:
        return True
    overlap = produced & consumed
    if (produced & quoted) and (consumed & quoted) and (any(len(token) >= 5 for token in overlap) or len(overlap) >= 2):
        return True
    return False


def validate_task_relation(task, target, link, source):
    """Scope/version/raw-quote checks precede this output/input/direction check.

    A common supported object supplies a hypothesis, not proof of a transfer.
    Unknown paraphrases/directions stay visible for confirmation. A link that
    the quote does not support is omitted by the caller; it does not discard
    the rest of the capture. Explicit contradictions still fail closed.
    """
    output = normalized(task['fields']['result']['value'] or '')
    incoming = normalized(target['fields']['input']['value'] or '')
    raw_quotes = [ref['quote'] for ref in link['evidence'] if ref['source_id'] == source['id']]
    quotes = [normalized(quote) for quote in raw_quotes]
    performer = normalized((target['fields'].get('performer') or {}).get('value') or '')
    names = [normalized(target['name']), normalized(target['fields']['action'].get('value') or ''), performer]
    clauses = [clause.strip() for quote in quotes for clause in re.split(r'[,.!?;\n]', quote) if clause.strip()]
    relation = r'(?:repasse|repass\w*|entreg\w*|encaminh\w*|envi\w*|receb\w*|entrada|insumo|depend\w*|vincul\w*|relacao|ligacao)'
    negative = r'\b(?:nao|nunca|nenhum|nenhuma)\b(?:\s+\w+){0,5}\s+' + relation + r'\b|\bsem\s+(?:(?:indicacao|evidencia|confirmacao)\s+de\s+|qualquer\s+)?(?:repasse|entrega|entrada|vinculo|relacao|ligacao|dependencia)\b'
    anaphora = r'\b(?:entre elas|entre eles|esse repasse|este repasse|essa entrega|esta entrega)\b'
    if any(re.search(negative, clause) and (_direction(clause, output, names) or re.search(anaphora, clause)) for clause in clauses):
        raise BrainError('task_relation_contradicted_by_source')
    producer_names = [normalized(task['name']), normalized(task['fields']['action'].get('value') or '')]
    if any(_direction(clause, output, producer_names) and not _direction(clause, output, names) for clause in clauses):
        raise BrainError('task_relation_contradicted_by_source')
    literal_object = bool(output and any(_contains(quote, output) for quote in quotes))
    directed = any(_direction(clause, output, names) and not re.search(negative, clause) for clause in clauses)
    alias_in_quote = any(_contains(quote, incoming) and any(_contains(quote, name) for name in names if name) for quote in quotes)
    transferred = directed or any(_governed_transfer(quote, output, names) for quote in raw_quotes)
    if literal_object and (output == incoming or directed or alias_in_quote):
        # The same words in one sentence are not a handoff. Someone or something
        # has to move: mandou, envia, passa, entrega, confere e eu libero.
        if not transferred:
            return 'omit'
        if link.get('basis') == 'inferred' or output != incoming or not directed:
            return 'inferred'
        return link.get('basis', 'reported')
    if any(_named_sequence(quote, task, target) and not re.search(negative, _fold_lines(quote)) for quote in raw_quotes):
        # The capture names both activities in a sequence. Keep it visible.
        # A delivery verb aimed at the consumer can keep the proposed basis.
        if any(_direction(clause, output, names) for clause in clauses) and link.get('basis') != 'inferred':
            return link.get('basis', 'reported')
        return 'inferred'
    if _shared_cited_object(output, incoming, quotes):
        anchors = [token for token in _content(output) if any(_contains(quote, token) for quote in quotes)]
        anchors.sort(key=len, reverse=True)
        anchor = anchors[0] if anchors else ''
        loose = bool(anchor) and any(_direction(clause, anchor, names) and not re.search(negative, clause) for clause in clauses)
        if loose and link.get('basis') != 'inferred':
            return link.get('basis', 'reported')
        if any(_governed_transfer(quote, output, names) for quote in raw_quotes):
            return 'inferred'
        return 'omit'
    return 'omit'
