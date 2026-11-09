# R315 — Cambio di sessione delle finestre (handover): analisi e contratto

Stato: BOZZA del coordinatore coord-1109, 2026-11-09. Diventa vincolante (congelata) solo dopo
l'approvazione del proprietario, registrata come decisione. Fonte: proposta del proprietario dal caso
hotskills del 9/11/2026 (decisione r315-perimetro).

## 1. Cosa c'è già e cosa manca (letto nel codice di v3.14.0, 0e56ba3)

| Pezzo della proposta | Già presente | Manca |
|---|---|---|
| 1 Avviso «stanca» | `usage.py` legge già, per sede, i transcript Claude e Codex con orario e token. Codex scrive `last_token_usage.input_tokens` e `model_context_window` (frazione reale); Claude scrive input+cache_read+cache_creation dell'ultimo turno (token reali, dimensione della finestra assente). | Età della sessione, contesto attuale, soglia, avviso. |
| 2 Momento sicuro | `checklist --moment resume/assign/deliver/push`; `runs`, `resources`, `service.wait`, task `--release-gate` sono nel registro. | `--moment handover`. |
| 3 Passaggio | `replace` (replacement.py): task successori con autori e budget, letture riassegnate, posta utile, risorse, stand-by, onboarding, un solo evento atomico. | Rifiuta il coordinatore; rifiuta una sede che ha risposto da poco (è fatto per finestre morte); onboarding povero; nessuno stato fuori registro. |
| 4 Passaggio in sospeso | `waits()` calcola le attese al momento della lettura; «Ti serve» mostra richieste aperte. | Un passaggio deciso ma rinviato, con condizioni verificabili. |

Prova del problema: oggi stesso il subentro di coord-1109 a coord-3121 è stato fatto a mano
(eventi 5755 agent, 5760 decision, 5761 standby), perché `replace` risponde
«Coordinator replacement needs an explicit role handover».

## 2. Correzioni alla proposta (motivate)

1. **Due passi, non uno.** La finestra nuova non esiste ancora quando la vecchia scrive la nota, e la
   sua sessione non va inventata. Quindi: `handover prepare` (la sede vecchia, o il coordinatore se la
   vecchia è morta) e `handover accept` (la finestra nuova). Per il proprietario resta una frase per
   finestra: «riprendi la sede <nuova-sede>».
2. **Un solo motore.** `handover accept` riusa il `Plan` di replacement.py (successori, letture, posta,
   risorse, stand-by) con una modalità `handover`: niente secondo motore che diverga.
3. **Nessun nuovo flag «critico a tempo» sui task.** Il rilascio a metà è già marcato (`--release-gate`);
   gli impegni a orario (la newsletter delle 15:00) diventano **condizioni** del passaggio in sospeso
   (`after:`), e restano scritti nella sezione «promesse» della nota.
4. **Cursori inbox non si spostano.** Il cursore è per nome di sede; la sede nuova parte dalla lista dei
   messaggi pendenti che l'accept le porta. Gli altri non cambiano nulla, salvo ricevere la presentazione.
5. **La stanchezza non legge transcript a ogni `status`.** Leggere i transcript è il costo che oggi solo
   `balance`/dash pagano. `status` mostra l'avviso solo dal valore già calcolato e fresco; `doctor --resume`,
   `checklist --moment handover` e la dash lo calcolano.
6. **Mai percentuali inventate.** Per Claude la frazione compare solo se la dimensione della finestra è
   dichiarata in `settings`; altrimenti si mostrano età e token.

## 3. Contratto condiviso (L0, del coordinatore; congelato prima delle corsie)

### 3.1 Comandi

```
squadra handover prepare --actor ACT --from SEDE --to NUOVA-SEDE
        --outside-file FILE | --outside-unknown "motivo"
        (--owner-words TESTO --channel typed-here|relayed | --answers ASK-ID --choice OPZ)
        [--when task:ID]... [--when after:ISO8601]... [--when confirm:"testo"]...
squadra handover status [--from SEDE]              # sola lettura: condizioni, gate, nota
squadra handover note --from SEDE [--max-chars N]  # sola lettura: la nota generata
squadra handover confirm --actor COORD --id HID --condition N --reason TESTO   # solo condizioni confirm:
squadra handover accept --actor NUOVA-SEDE --id HID --session ID --client claude|codex|cursor [--dry-run]
squadra handover cancel --actor ACT --id HID --reason TESTO
squadra checklist --moment handover --actor SEDE
squadra context --seat SEDE [--since SEQ:HASH]     # nota di sede, estende context --task
```

- `prepare`: ACT è la sede stessa oppure il coordinatore. Registra il passaggio in sospeso con id
  `handover-<digest>`, le parole del proprietario (obbligatorie, stessa regola di `decision`), le
  condizioni e la sezione fuori registro come artefatto. Non cambia ruoli né stand-by.
- `accept`: ACT è il nome nuovo; il comando registra la sede (`agent`) e applica il passaggio in **un
  evento** `handover` (come `replace`). Rifiuta se il gate (3.3) fallisce o se una condizione non è vera.
  `--force` non esiste: per forzare si annulla e si usa `replace` (finestra morta) o si confermano le condizioni.
- `cancel`: la sede, il coordinatore o la nuova sede prima dell'accept.

### 3.2 Eventi e stato (formato journal 5)

Tipi nuovi: `handover.prepare`, `handover.confirm`, `handover.cancel`, `handover` (contenitore atomico
come `replace`, con `operations`). Tutti scrivono **format 5**: i client 3.14 rifiutano invece di ignorare.
Tutte le finestre passano alla 3.15 prima del primo evento handover.

```
state['handovers'][HID] = {
  'id', 'from', 'to', 'by', 'utc', 'owner_words', 'channel', 'answers'?, 'decision',
  'conditions': [{'n': 1, 'kind': 'task'|'after'|'confirm', 'value': str,
                  'confirmed': {'by','utc','reason'}?}],
  'outside': {'artifact': ref} | {'unknown': motivo},
  'status': 'pending'|'accepted'|'cancelled', 'accepted': {'seq','session','client'}?,
  'cancelled': {'by','utc','reason'}?
}
```

Invarianti: al massimo un passaggio `pending` per sede di origine; dopo un accept da un coordinatore
c'è un solo coordinatore attivo nel registro; nessun evento cambia lo stato da solo (le condizioni si
valutano in lettura, come `waits`).

### 3.3 Gate «momento sicuro» — `checklist --moment handover --actor SEDE`

Considera la sede e tutti i suoi alias (stessa identità o sessione). Ogni voce `fail` dice cosa aspettare.

| id | fail se | da aspettare |
|---|---|---|
| `runs` | run `running`/`cleanup-unknown` della sede | «ricevuta terminale del run X» |
| `resources` | risorse prenotate dalla sede | «rilascio di X» |
| `reading` | handoff di lettura ACK-ato dalla sede senza verdetto | «verdetto su task X» |
| `release` | task aperto `--release-gate` della sede | «chiusura del rilascio X» |
| `worktree` | checkout di un task aperto della sede con modifiche non committate | «commit o save del WIP in X» (`unverifiable` se il checkout non si vede) |
| `conditions` | condizioni del passaggio in sospeso non vere | elenco delle condizioni mancanti |
| `pending_acks` | — (`notice`) messaggi della sede in attesa di ACK altrui | passano alla sede nuova |
| `service_wait` | — (`notice`) attesa di servizio registrata | |

`ready` = nessun `fail`. È sola lettura.

### 3.4 Nota di sede — `context --seat SEDE`

Schema `squadra.seat-context/1`, testo limitato (default come `context --task`), costruita solo dal registro:
identità e ruolo; task aperti della sede come proprietario o lettore (id, titolo dal brief, stato, fase,
lettore, prossimo passo dall'ultimo `output.next`); ultime decisioni che riguardano quei task o firmate
dalla sede (max 10); messaggi senza ACK verso e dalla sede; risorse; domande aperte; passaggi in sospeso;
regole del progetto (`onboarding_template`); la sezione fuori registro, testuale; l'elenco di cosa è stato
omesso per il limite. `--since` filtra come in `context --task`.

### 3.5 Sezione «stato fuori dal registro» (`--outside-file`)

UTF-8, max 8192 byte, quattro intestazioni obbligatorie, ognuna non vuota (vale «nessuno»):
`## Server e sessioni aperte`, `## Rilasci o invii in corso`, `## Promesse al proprietario`, `## Altro`.
Rifiuto se manca una sezione o se il testo contiene un segreto riconoscibile (chiavi `sk-`, `ghp_`,
`AKIA`, blocchi `PRIVATE KEY`, righe `password=`/`token=` con valore): la nota dice *dove* è la sessione,
mai la password. Se la sede è morta: `--outside-unknown "motivo"`, che compare come perdita dichiarata.

### 3.6 Avviso di finestra stanca

Funzione `tiredness(seat_sources, now, settings)` in usage.py, per sede:
`{'session_started', 'age_hours', 'context_tokens', 'context_window'?, 'context_fraction'?, 'tired': bool,
'reasons': [...], 'source'}`. Età = primo campione della sessione registrata → ora. Contesto = ultimo turno
(Codex `last_token_usage.input_tokens`, finestra `model_context_window`; Claude
input+cache_read+cache_creation, finestra solo da `settings.context_window[<modello>]`).
Soglie in `settings`: `tired_hours` (default 8), `tired_context_fraction` (default 0.6). Avviso:
«conviene ripartire: <motivo>. Prepara il passaggio con squadra handover prepare …». Nessuna azione automatica.
Dove: dash (pagina finestre/Bilancio), `doctor --resume` (notice), `checklist --moment handover` (notice),
`status` solo da valore in cache fresco.

### 3.7 Passaggio in sospeso visibile

`status`, `stato.md`, dash e `board` mostrano ogni passaggio `pending`: sede, condizioni (vere/false),
gate. Quando tutto è vero: «adesso si può: apri una finestra nuova e scrivi «riprendi la sede <nuova>»».
Entra in «Ti serve» solo quando è pronto (prima è in «Chi aspetta»).

### 3.8 Accept: cosa fa l'evento `handover`

Riusa `Plan` di replacement.py in modalità `handover`: salta il rifiuto «answered recently» (la sede è viva
per definizione), permette il ruolo coordinatore (la sede nuova eredita il ruolo; la vecchia va in stand-by),
crea i successori e riassegna letture come oggi, porta i messaggi pendenti (`--carry useful`),
manda: alla sede nuova l'onboarding **con la nota di sede completa**; a ogni altra sede attiva una
presentazione con ACK richiesto («la sede X ora è Y, coordinatore/ruolo …»). Consegna come `deliver_outbox`.

## 4. Corsie proposte (dopo l'approvazione)

Assegnazione definitiva (decisione r315-corsie-handover): tutte le corsie partono da 0e56ba3 (v3.14.0) su
checkout propri; il contratto è congelato da questo file (sha256 nella decisione), L0 lo porta in
`references/handover.md` nell'integrazione. Nessuna corsia aspetta L0.

| Corsia | Costruisce | Possiede | Non tocca |
|---|---|---|---|
| R315-HO.L0 contratto | coord-1109 | `references/handover.md`, rimandi in SKILL.md/references, README/CHANGELOG al rilascio | codice |
| R315-HO.L3 motore e nota | codex-20261109-d793 | nuovi `scripts/handover.py`; `scripts/replacement.py` (modalità handover); `scripts/context_brief.py` (`--seat`); `scripts/selftest.py` (scenario coordinatore finto); in `scripts/registry.py` SOLO apply/validate dei tipi handover.* e la scelta format 5 in append; in `scripts/squadra.py` SOLO il parser e il dispatch di `handover` e di `context --seat`; test nuovi `tests/test_handover*.py` | checklist.py, usage.py, dashboard.py, ripresa.py |
| R315-HO.L2 gate e sospeso | codex-arrivo-20261109 (dopo RINVII-A) | `scripts/checklist.py` (`--moment handover`); nuovo `scripts/handover_conditions.py` (valutazione condizioni, pura: `evaluate(state, handover, now) -> list`); in `scripts/registry.py` SOLO `board()` e il testo «Serve a te/Chi aspetta» per i passaggi in sospeso; in `scripts/squadra.py` SOLO la riga `--moment` choices; in `scripts/dashboard.py` SOLO la vista dei passaggi in sospeso; test nuovi `tests/test_handover_gate*.py` | handover.py, replacement.py, usage.py |
| R315-HO.L1 stanchezza | claude-20261109-3bd9 (dopo RINVII-B) | `scripts/usage.py` (`tiredness`); in `scripts/registry.py` SOLO la validazione/applicazione delle chiavi settings `tired_hours`, `tired_context_fraction`, `context_window`; in `scripts/dashboard.py` SOLO la vista finestre/Bilancio; notice in `scripts/ripresa.py` dopo la chiusura di RINVII-A (stessa base rinnovata); test nuovi `tests/test_tiredness*.py` | handover.py, checklist.py |

Cuciture fissate: L3 chiama il gate come `checklist.run(store, 'handover', actor=SEDE)` e le condizioni come
`handover_conditions.evaluate(state, handover, now)`; finché L2 non è unita, L3 usa uno stub con la stessa firma
nei propri test. L2 legge `state['handovers']` con la forma del §3.2 (test con stato sintetico). L1 espone
`tiredness(...)` con la forma del §3.6; L2 la mostra come notice nel gate solo se presente.

Lettura finale (regola 7): candidato `3.15.0-rc1`; L0 e L1 (Claude) letti da Codex, L2 e L3 (Codex) letti da Claude.

## 5. Criteri di accettazione (della proposta, resi verificabili)

1. Coordinatore con task aperti, letture consegnate, risorsa liberata e messaggi pendenti: `prepare` +
   `accept` senza perdere storico, inbox, decisioni; dopo l'evento un solo coordinatore attivo.
2. Con un run della sede `running` il gate dà `fail runs` e `accept` rifiuta.
3. Selftest con coordinatore finto: dopo l'accept, la sede nuova trova nella nota ogni task aperto, il suo
   prossimo passo, ogni messaggio senza ACK e ogni risorsa, senza altre fonti (controllo strutturale: il
   test non può provare che un modello «capisca», lo dichiariamo).
4. Nessun cambio automatico: nessun timer, condizioni valutate solo in lettura, accept solo su comando.
5. `prepare` senza sezione fuori registro (o con un segreto riconoscibile) rifiuta.
6. Condizione `after:` falsa prima dell'orario e vera dopo; `task:` vera solo a task chiuso.

## Note d'implementazione della 3.15.0

- **status e la finestra stanca.** `status` mostra l'avviso leggendo la cache su disco scritta da chi l'ha calcolato (dashboard o `doctor --resume`), senza leggere transcript (§3.6), solo se il valore è fresco (entro 60 s) e legato a identità, sessione e settings. Se nessuno l'ha calcolato di recente, `status` non mostra nulla: resta veloce.
- **access probe** stampa il rapporto completo anche con l'output silenzioso: è un comando diagnostico (decisione r315-sout2-eccezione-access).
- **Comandi composti.** `assign`, `join` e `pickup` stanno sopra le primitive e non introducono tipi di evento nuovi; una prova di parità lo verifica.
