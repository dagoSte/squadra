"""Fixed system-skill catalogue, verified local audits and observed activity."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat

from forecast import instant
from registry import VERSION

ROOT = Path(__file__).resolve().parents[1]
SCANNER = 'cisco-ai-skill-scanner'
POLICY = {'name': 'balanced', 'behavioral': True, 'osv': True, 'fail_on_severity': 'high'}
COMMON_FILES = ('SKILL.md', 'scripts/registry.py', 'scripts/squadra.py', 'scripts/dashboard.py',
                'scripts/system_skills.py', 'assets/dashboard.html')
DEPENDENCIES = {'errata': ('scripts/errata.py',), 'rasoio': (), 'previsione': ('scripts/forecast.py',),
                'bilanciamento': ('scripts/balance.py', 'scripts/forecast.py', 'scripts/usage.py')}
ON_DEMAND = frozenset({'previsione', 'bilanciamento'})
CATALOGUE = (
    {'id': 'errata', 'tag': {'it': 'Esperienza', 'en': 'Experience'},
     'description': {'it': 'Ricorda gli errori già incontrati e richiama le lezioni utili.', 'en': 'Keeps lessons from past mistakes and recalls the relevant ones.'},
     'trigger': {'it': 'Quando un controllo fallisce e alla preparazione o all’avvio di un incarico.', 'en': 'When a check fails and when a task is briefed or started.'},
     'action': {'it': 'L’agente consulta le lezioni pertinenti, registra ciò che ha imparato e segnala le lezioni inefficaci.', 'en': 'The agent consults relevant lessons, records what it learned and marks ineffective lessons.'},
     'records': {'it': 'Registra lezioni, richiami, ritiri e segnalazioni di inefficacia.', 'en': 'Records lessons, recalls, retirements and ineffective marks.'},
     'limits': {'it': 'Una lezione non è un permesso e non sostituisce una prova.', 'en': 'A lesson is not permission and does not replace a proof.'},
     'command': 'squadra lesson --help', 'prompt': {'it': 'Richiama le lezioni pertinenti prima di iniziare questo incarico.', 'en': 'Recall relevant lessons before starting this task.'}},
    {'id': 'previsione', 'tag': {'it': 'Pianificazione', 'en': 'Planning'},
     'description': {'it': 'Confronta lavori simili conclusi per aiutare a pianificare il prossimo.', 'en': 'Compares similar completed work to help plan the next task.'},
     'trigger': {'it': 'Alla creazione, alla preparazione e all’avvio di un incarico; anche su richiesta.', 'en': 'At task creation, briefing and start; also on demand.'},
     'action': {'it': 'L’agente legge i confronti e segue il percorso suggerito, oppure registra perché se ne discosta.', 'en': 'The agent reads the comparisons and follows the suggested route, or records why it differs.'},
     'records': {'it': 'Non registra attivazioni: calcola la previsione al momento.', 'en': 'Records no activations: computes the forecast on demand.'},
     'limits': {'it': 'Servono almeno tre lavori confrontabili. I tempi trascorsi non sono garanzie o ore di lavoro attivo.', 'en': 'Needs at least three comparable tasks. Elapsed times are not guarantees or active working hours.'},
     'command': 'squadra forecast --task TASK', 'prompt': {'it': 'Confronta questo incarico con lavori simili e spiega i tempi osservati.', 'en': 'Compare this task with similar work and explain the observed times.'}},
    {'id': 'rasoio', 'tag': {'it': 'Semplicità', 'en': 'Simplicity'},
     'description': {'it': 'Cerca una soluzione più semplice che mantenga tutti i requisiti.', 'en': 'Looks for a simpler solution that preserves every requirement.'},
     'trigger': {'it': 'Durante la costruzione e la lettura di modifiche al codice.', 'en': 'During implementation and the review of code changes.'},
     'action': {'it': 'L’agente cerca duplicazioni e complessità evitabile e verifica ogni proposta di semplificazione.', 'en': 'The agent looks for duplication and avoidable complexity and verifies each simplification proposal.'},
     'records': {'it': 'Conta le letture con la lente rasoio e le righe nette dichiarate, non risparmi misurati.', 'en': 'Counts rasoio-lens reviews and declared net lines, not measured savings.'},
     'limits': {'it': 'Conserva controlli di sicurezza, accessibilità, comportamento e requisiti.', 'en': 'Preserves security checks, accessibility, behavior and requirements.'},
     'command': 'squadra review --lens rasoio --net-lines N …', 'prompt': {'it': 'Leggi questa modifica con rasoio: quali parti si possono semplificare mantenendo i requisiti?', 'en': 'Read this change with rasoio: what can be simplified while preserving the requirements?'}},
    {'id': 'bilanciamento', 'tag': {'it': 'Pianificazione', 'en': 'Planning'},
     'description': {'it': 'Suggerisce a quali finestre e modelli affidare un incarico, guardando carico, quota e lavori simili.', 'en': 'Suggests which windows and models to give a task, from load, quota and similar work.'},
     'trigger': {'it': 'Alla creazione, alla preparazione e all’avvio di un incarico; anche su richiesta.', 'en': 'At task creation, briefing and start; also on demand.'},
     'action': {'it': 'Il coordinatore legge l’ordine proposto e lo segue, oppure registra perché sceglie altro. Non assegna nulla da solo.', 'en': 'The coordinator reads the proposed order and follows it, or records why it chooses otherwise. It assigns nothing by itself.'},
     'records': {'it': 'Non registra attivazioni: calcola i suggerimenti al momento.', 'en': 'Records no activations: computes the advice on demand.'},
     'limits': {'it': 'La quota di Claude è solo una stima rispetto a un riferimento che dichiari tu. Un dato mancante non vale zero e un autore non viene mai proposto come lettore.', 'en': 'Claude quota is only an estimate against a reference you declare. Missing data is never zero, and an author is never suggested as reader.'},
     'command': 'squadra balance --task TASK', 'prompt': {'it': 'Quali finestre sono più libere per questo incarico e chi può leggerlo in modo indipendente?', 'en': 'Which windows are freest for this task, and who can read it independently?'}},
)


def required_files(skill):
    return (*COMMON_FILES, 'references/skills/' + skill + '.md', *DEPENDENCIES[skill])


def bounded_file(root, relative, limit):
    """Open each component without following links, then bound the regular file."""
    parts = relative.split('/')
    if relative.startswith('/') or any(p in ('', '.', '..') for p in parts) or '\\' in relative:
        raise ValueError('unsafe package path')
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        leaf = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            st = os.fstat(leaf)
            if not stat.S_ISREG(st.st_mode) or st.st_size > limit:
                raise ValueError('package file unavailable or too large')
            with os.fdopen(leaf, 'rb', closefd=False) as stream:
                data = stream.read(limit + 1)
            if len(data) > limit:
                raise ValueError('package file too large')
            return data
        finally:
            os.close(leaf)
    finally:
        os.close(fd)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate audit key')
        result[key] = value
    return result


def audit(root, skill, read_at):
    unavailable = {'status': 'not_audited', 'reason': 'missing_or_invalid_record'}
    try:
        bundle = json.loads(bounded_file(root, 'assets/skill-audits.json', 65536), object_pairs_hook=unique_object)
        if type(bundle.get('schema')) is not int or bundle['schema'] != 1 or not isinstance(bundle.get('records'), list) or len(bundle['records']) > len(CATALOGUE):
            return unavailable
        records = [r for r in bundle['records'] if r.get('skill') == skill]
        if len(records) != 1:
            return unavailable
        record = records[0]
        if (record.get('scanner') != SCANNER or record.get('version') != '2.2.2'
                or json.dumps(record.get('policy'), sort_keys=True) != json.dumps(POLICY, sort_keys=True)
                or set(record.get('files', {})) != set(required_files(skill))):
            return unavailable
        scanned = instant(record.get('scanned_at'))
        counts = record.get('findings', {})
        if (scanned is None or scanned > read_at or set(counts) != {'critical', 'high', 'medium', 'low'}
                or any(type(n) is not int or n < 0 or n > 1000000 for n in counts.values())):
            return unavailable
        expected = 'fail' if counts['critical'] or counts['high'] else 'warn' if counts['medium'] or counts['low'] else 'pass'
        if record.get('result') != expected:
            return unavailable
        for path, sha in record['files'].items():
            if hashlib.sha256(bounded_file(root, path, 2 * 1024 * 1024)).hexdigest() != sha:
                return {'status': 'not_audited', 'reason': 'files_changed'}
        return {**record, 'status': expected}
    except (OSError, ValueError, TypeError, AttributeError, KeyError, RecursionError):
        return unavailable


def catalogue(events, state, root=ROOT, read_at=None):
    stamp = datetime.now(timezone.utc).timestamp() if read_at is None else read_at
    day = int(stamp // 86400)
    origins = {e['data']['id']: e['data'].get('source', {}) for e in events if e['kind'] == 'lesson'}
    result = []
    for definition in CATALOGUE:
        skill = definition['id']
        row = {**definition, 'source': 'squadra built-in', 'version': VERSION, 'audit': audit(root, skill, stamp)}
        try:
            row['guide'] = bounded_file(root, 'references/skills/' + skill + '.md', 65536).decode('utf-8')
        except (OSError, ValueError):
            row['guide'] = None
        activations, bins, pairs = [], [0] * 56, set()
        counts = dict(lessons=0, recalled_pairs=0, recall_events=0, ineffective=0, reads=0, net_lines=0)
        for event in events if skill not in ON_DEMAND else []:
            kind, data = event['kind'], event['data']
            if not ((skill == 'errata' and kind in ('lesson', 'lesson.retire', 'lesson.recall', 'lesson.ineffective'))
                    or (skill == 'rasoio' and kind == 'review' and data.get('lens') == 'rasoio')):
                continue
            at = instant(event['utc'])
            if at is None or at > stamp:
                continue
            origin = data.get('source', {}) if kind == 'lesson' else origins.get(data.get('lesson'), {})
            task = data.get('task') or origin.get('task')
            item = {'event': event['seq'], 'utc': event['utc'], 'kind': kind,
                    'record': {key: event[key] for key in ('seq', 'utc', 'actor', 'kind', 'data')},
                    'task': task if task in state['tasks'] else None,
                    'task_relation': 'activation' if data.get('task') else 'origin',
                    'outside_standard_sources': origin.get('eligible') is False}
            activations.append(item)
            index = int(at // 86400) - day + 55
            if 0 <= index < 56:
                bins[index] += 1
            counts['lessons'] += kind == 'lesson'
            counts['recall_events'] += kind == 'lesson.recall'
            counts['ineffective'] += kind == 'lesson.ineffective'
            if kind == 'lesson.recall':
                pairs.update((data['task'], lesson) for lesson in data.get('lessons', []))
            if kind == 'review':
                counts['reads'] += 1
                if type(data.get('net_lines')) is int:
                    counts['net_lines'] += data['net_lines']
        counts['recalled_pairs'] = len(pairs)
        activations.sort(key=lambda item: (instant(item['utc']), item['event']))
        on_demand = skill in ON_DEMAND
        row.update(mode='on_demand' if on_demand else 'recorded',
                   total=None if on_demand else len(activations),
                   last_24h=None if on_demand else sum(instant(e['utc']) >= stamp - 86400 for e in activations),
                   daily=None if on_demand else bins, counts=counts,
                   first=activations[0] if activations else None, last=activations[-1] if activations else None,
                   recent=list(reversed(activations[-20:])))
        result.append(row)
    return {'skills': result, 'read_at': datetime.fromtimestamp(stamp, timezone.utc).isoformat(),
            'head': events[-1]['hash'] if events else None}
