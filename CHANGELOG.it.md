# Storia delle versioni

Tutte le versioni di Squadra, dalla più nuova. Il README tiene la più nuova e le tre precedenti.


**3.15.0 — una finestra stanca si sostituisce senza perdere nulla, e la squadra è più semplice da guidare**

Una finestra che lavora per ore diventa costosa e meno affidabile. Ora la si sostituisce con due comandi, coordinatore compreso, senza perdere incarichi, posta o decisioni. Intanto Squadra si guida con meno comandi, meno testo e istruzioni più corte, senza togliere nessuna funzione.

- **Passaggio di sede in due comandi.** `squadra handover prepare --from SEDE --to NUOVA` registra le parole del proprietario, le condizioni di partenza (`task:ID` chiuso, `after:ORARIO`, `confirm:"…"`) e lo «stato fuori dal registro»: server, sessioni aperte, rilasci in corso, promesse al proprietario. Senza questa sezione il comando si ferma, e rifiuta password e token riconoscibili, anche dentro liste e tabelle Markdown. Alla finestra nuova basta «riprendi la sede NUOVA»: `handover accept` eredita incarichi, letture, posta utile e ruolo in un solo evento, mette in stand-by la sede vecchia e presenta la nuova a ogni finestra. `context --seat` scrive la nota di ripresa dell'intera sede.
- **Il momento giusto.** `checklist --moment handover --actor SEDE` risponde «adesso no» se c'è un run in corso, una risorsa prenotata, una lettura o un rilascio a metà, modifiche non committate o condizioni non ancora vere, ed elenca cosa aspettare. Un passaggio deciso «ma non adesso» resta visibile in `status`, nella tabella e nella dashboard finché non è fatto; quando tutto è pronto compare «Adesso si può: apri una finestra nuova e scrivi «riprendi la sede …»».
- **Finestra stanca.** Dashboard, `doctor --resume` e `status` mostrano per ogni sede da quanto è attiva la sessione e quanto contesto occupa, letti dai transcript Claude e Codex, e avvisano «conviene ripartire» oltre `tired_hours` (8) o `tired_context_fraction` (0,6). Mai percentuali inventate: per Claude la frazione compare solo se `context_window` del modello è dichiarato. È solo un avviso, nulla cambia da solo.
- **La posta non resta più ferma senza che nessuno lo veda.** Chi ascolta la posta (`inbox --follow`) lascia un battito e la sua fine, anche se viene interrotto. Ogni finestra mostra «in ascolto», «fermo da X min» o «mai armato»; ogni messaggio da confermare mostra da quanto è recapitato e se il destinatario sta ascoltando. Il coordinatore trova «Aspetta te» con i verdetti da chiudere, gli handoff da inoltrare e i messaggi da confermare, ciascuno con il comando da lanciare.
- **Più semplice, senza perdere funzioni.** SKILL.md passa da 55 KB a 20,5 KB (−63%): ogni regola in una riga con il rimando alla sua sede e `references/regole.md` come inventario; un test confronta ogni clausola di ogni regola della 3.14.0 con la sua sede. `squadra assign` fa in un solo comando incarico, brief, consegna e checklist; `squadra join` entra nella squadra; `squadra pickup` riassume dove si è rimasti; una prova di parità garantisce gli stessi eventi della sequenza lunga. Ogni scrittura risponde con una riga (61–136 byte misurati su decision, send, transport, ack e ping, al massimo 200 per ogni scrittura, invece dei ~3,6 KB della tabella) e `--verbose` mostra tutto. `decision --round-scope` sostituisce il prefisso di testo, `squadra project --set PERCORSO` ripara un progetto spostato, `ask --evidence decision:ID` accetta un riferimento.
- **Correzioni.** Chi ha costruito una corsia non legge mai il padre: la regola ora vale con `reassign`, `replace`, il pool, i passaggi di sede, lungo tutta la catena dei successori e già alla creazione dell'incarico. `doctor --resume` non segnala più come fermo un successore che registra il piano ereditato né un incarico qualsiasi con «.L» nel nome.
- **Verificata così.** Ogni parte è stata letta da un modello diverso da quello che l'ha scritta (Codex legge Claude, Claude legge Codex); le letture finali hanno trovato tre problemi gravi, corretti e riletti prima del rilascio. Ogni correzione ha un test rosso prima e verde dopo; la suite completa conta oltre 2490 test.
- **Aggiorna tutte le finestre insieme.** I passaggi di sede scrivono il formato di journal 5: le finestre 3.14 lo rifiutano invece di ignorarlo.

**3.14.0 — corsie automatiche, budget per incarico e una ripresa più attenta**

- **Corsie automatiche.** `task --paths GLOB` (ripetibile) analizza i file da toccare — le co-modifiche Git pesano più degli import, le copie byte-identiche restano insieme, gli hub condivisi vanno in una corsia contratto L0 del coordinatore — e, quando ci sono due o più gruppi indipendenti e costruttori liberi, crea da sola le corsie: file posseduti disgiunti, un costruttore per corsia scelto da balance nel proprio checkout del progetto, un lettore dell'altro modello, dipendenze da L0, un brief per corsia, una decisione registrata e le assegnazioni, tutto o niente. Non sceglie mai finestre silenziose da due giorni, in stand-by o che leggono il padre. `lanes --task ID --paths …` mostra l'anteprima, `--apply` è solo del coordinatore, `--lanes none` tiene un solo incarico. Misurato sul repository reale: ritrova da sola in meno di un secondo le due corsie del giro storage della 3.13.
- **Budget di lettura per incarico.** `decision --extend-budget INCARICO --stage plan|code --by N` con le parole del proprietario aggiunge letture a un solo incarico; le letture fatte non si azzerano e l'estensione segue supersede e replace. Aggiorna tutte le finestre alla 3.14 prima di usarlo: le finestre più vecchie rifiutano le letture estese.
- **La ripresa vede il lavoro fermo.** `status` e `doctor --resume` elencano gli incarichi accettati da 30 minuti senza lavoro registrato (un padre con corsie aperte non è fermo).
- **Prove e risorse.** `prove` senza argomenti esegue la prova concordata; la suite completa è rifiutata subito, con un messaggio chiaro, dove localhost non si può aprire (client in sandbox). Le riserve indicano l'incarico: un altro incarico della stessa identità aspetta nella coda normale.
- **Spazio.** `storage adopt` adotta anche i vecchi pacchetti save; `storage compact` sigilla in sola lettura i vecchi candidati scrivibili, così anche i volumi senza copy-on-write li condividono; la purge legge le protezioni una volta per lotto e di nuovo solo quando un altro scrittore fa avanzare il registro.
- **Checklist.** `checklist --moment push --range A..B` verifica l'avanzamento in linea retta senza upstream.
- Trovati prima del rilascio da un council trasversale e da code-graph, e corretti con test: un lettore di corsia che aveva costruito il codice che leggeva, una riserva che un alias non poteva rilasciare, consigli di inattività sui padri di integrazione, pacchetti di contesto che nascondevano le estensioni e documentazione che contraddiceva il codice.

- **Per chi integra.** `storage_reuse.compact` è passata in `storage_compact.compact` (senza alias, per tenere i moduli storage senza cicli di import); il comando `storage compact` non cambia.

**Guarda il film delle novità della 3.10.2, due minuti — attiva l'audio nel lettore:**

https://github.com/user-attachments/assets/8f38a443-b738-48f8-b562-9b072419b5f0

**3.13.1 — una copia del registro non cancella mai i punti di recupero dell'originale**

- **Correzione.** Nella 3.13.0 i punti di recupero erano registrati solo con il percorso assoluto, quindi una copia del registro (clone, ripristino, copia aperta con `--root`) ereditava checkpoint e salvataggi dell'originale, e `storage purge --apply` lanciato nella copia poteva cancellarli. Ora ogni checkpoint, adozione e salvataggio registra la radice del registro che l'ha creato; altrove è elencato come estraneo e non è mai un bersaglio di purge. Le registrazioni senza radice valgono solo dentro il proprio registro. Un test di regressione cancella il checkpoint dell'originale con la 3.13.0 e lo conserva ora.
- Trovato compattando un registro reale da 7,2 GB con la 3.13.0: `storage adopt` ha registrato 7 vecchi checkpoint, e `storage compact` ha condiviso 163 794 file su 166 663 e liberato 2,62 GB misurati su disco, con l'audit completo superato dopo.
- **Recupera lo spazio dopo l'aggiornamento:** di' a una finestra Claude «Usa squadra: compatta lo spazio del registro /percorso/del/registro». Il coordinatore mostra in anteprima adopt, compact e purge e applica solo con il tuo via libera.

**3.13.0 — checkpoint che condividono i byte, una politica di conservazione e la compattazione dei dati che hai già**

- **Riuso dei contenuti immutabili.** Nuovi candidati congelati e checkpoint condividono i file identici invece di copiarli: clone copy-on-write (APFS, o `FICLONE` su Linux), hardlink solo per file in sola lettura verificati dove il clone non esiste, copia normale come ultima scelta. Byte, percorsi, impronte e formati dei pacchetti non cambiano, e le versioni precedenti continuano a ripristinarli. Misurato su una copia di un registro reale, 10 checkpoint consecutivi con scritture concorrenti: 8,97 GB → 0,60 GB su APFS (−93%), 9,22 GB → 3,60 GB senza copy-on-write (−61%).
- **Compattazione dei dati esistenti.** `storage adopt --scan CARTELLA` registra i checkpoint fatti prima della 3.13, solo se il loro ultimo evento coincide con questo registro. `storage compact` poi condivide sul posto i byte di candidati, checkpoint, salvataggi e log delle corse archiviati. Su una copia da 4,25 GB di un registro reale con sei vecchi checkpoint, 89 485 file su 93 684 sono stati condivisi e il registro è sceso a 0,98 GB (−77%) in 7 minuti. All'apertura non si compatta nulla: ogni passo mostra prima un'anteprima, e `--apply` spetta al coordinatore.
- **Politica di conservazione.** `storage status` e `storage purge --dry-run` mostrano cosa si può togliere. Per impostazione predefinita restano i tre checkpoint e i tre salvataggi più recenti, l'ultimo salvataggio ripristinato, tutto ciò che ha meno di 24 ore e i percorsi `storage keep`. Incarichi aperti, corse vive e il registro attivo sono sempre protetti, e le cartelle sconosciute non si toccano mai. `storage purge --apply` è solo del coordinatore. Un keep o un incarico che arriva durante una purge ferma la cancellazione o viene rifiutato, mai registrato su dati cancellati. Sulla stessa copia: 0,88 GB dopo la purge predefinita (−79%).
- **Checkpoint registrati e compatti.** `checkpoint --actor FINESTRA --compact` registra il punto di recupero e omette le viste ricostruite all'apertura; `save` riusa il pacchetto precedente. Un checkpoint non fallisce più quando un file temporaneo di uno scrittore concorrente sparisce durante la copia.
- **Basta una frase.** Dopo l'aggiornamento di' a una finestra Claude: «Usa squadra: compatta lo spazio del registro /percorso/del/registro». A ogni ripresa `doctor --resume` segnala i vecchi checkpoint non registrati o più di 1 GB di candidati, e il coordinatore te la propone.
- **Le istruzioni della skill** non chiedono più copie complete ripetute: i checkpoint sono compatti e registrati, e ogni giro lungo comincia con `storage purge --dry-run`.

**3.12.1 — la dashboard apre i registri con finestre sostituite**

- **Correzione della dashboard.** Dalla 3.11.1 `replace` registra incarichi successori, letture e avvii dentro un solo evento `replace`. L'avanzamento degli incarichi della dashboard ignorava queste operazioni annidate, e ogni registro con una sostituzione andava in errore con `KeyError: '<incarico>-takeover-…'`. Ora l'avanzamento le applica come fa il registro, e un test di regressione lo fissa.
- Le copie bridge dei plugin di `scripts/registry.py` hanno la stessa correzione; il pin di lettura di memoranda accetta i byte della 3.12.1.

**3.12.0 — la checklist conosce il giro, avvisi e consigli più chiari, e una storia delle versioni**

- **Perimetro del giro nella checklist.** `checklist --moment assign` aggiunge `round_scope`: un incarico creato dopo l'ultima decisione la cui motivazione inizia con `Round scope:`, e non nominato in essa, risulta `unverifiable` con il motivo (consultivo, mai `fail`).
- **Checklist più rigorosa.** `checklist --moment deliver` fallisce su un incarico già chiuso; `checklist --moment push --range A..B` fallisce quando `A` non è antenato di `B`, e un intervallo vuoto dice `0 commits`.
- **Nessun incarico senza lettore in silenzio.** `task` senza `--reviewer` (o un gruppo di lettori, o `--read-at-gate`) avvisa che una consegna senza lettore non potrà riceverne uno dopo con `reassign`.
- **`task --proof -- argv` funziona come documentato,** anche quando il comando della prova contiene opzioni come `--root` o `--task`.
- **Consiglio di composizione secondo i ruoli veri.** `status`, tabella e dashboard dicono chi costruisce e chi rilegge; con ruoli misti mostrano i conteggi reali, e le righe di abbinamento restano visibili quando la squadra è sbilanciata.
- **Storia delle versioni.** La storia completa è passata in [CHANGELOG.it.md](CHANGELOG.it.md); il README tiene la versione più nuova e le tre precedenti.
- **Audit delle skill non aggiornati.** Come nella 3.11.2, le scansioni registrate in `assets/skill-audits.json` precedono questa versione; la pagina System skills mostra ogni skill come non verificata (file cambiati) fino a una nuova scansione.

**3.11.2 — regole che si controllano da sole: nessuna sovrapposizione, ripresa in un passo, rossi noti, finestre ferme, checklist e perimetro del giro**

- **Nessuna sovrapposizione tra finestre.** Il coordinatore controlla checkout, ramo, file posseduti, base, risorse riservate e commit attesi prima di ogni assegnazione ([guida](references/skills/sovrapposizioni.md)).
- **Ripresa in un passo.** `doctor --resume` riporta l'interprete, ogni checkout di incarichi aperti e finestre attive, la distanza dal rilascio e le finestre silenziose; un checkout mancante di un incarico aperto blocca con codice 1.
- **Rossi noti di main.** `tests/known_reds.json` elenca i test già rossi su main pulito; il runner parallelo separa `KNOWN RED` da `NEW RED` e `--accept-known` fa passare una prova d'incarico solo sui rossi noti. La prova di rilascio resta rigorosa, e l'elenco è di nuovo vuoto.
- **Finestre ferme.** `doctor --resume` segna le finestre silenziose come `archivable` o dice cosa le trattiene (`keeps`), su tutti i posti di un'identità; solo la parola del proprietario le mette in stand-by con `standby`.
- **Checklist.** `squadra checklist --moment resume|assign|deliver|push` controlla in sola lettura i momenti critici: al push, un'email di autore o committer mai vista prima fa fallire.
- **Perimetro del giro.** Fissato il perimetro di un giro, ciò che emerge aspetta il giro successivo, salvo che blocchi una chiusura.
- **Correzioni.** `test_rf4a_upgrade` gira su una fixture verificata di 5099f0c; `test_challenge` non entra più in corsa con la manutenzione di Git.
- **Audit delle skill non aggiornati.** Le scansioni registrate in `assets/skill-audits.json` precedono le modifiche di questa versione a `SKILL.md` e `scripts/squadra.py`; la pagina System skills mostra ogni skill come non verificata (file cambiati) fino a una nuova scansione.

**3.11.1 — sostituzione delle finestre, permessi all’ingresso, una posta per finestra e contesto di rientro**

- **Sostituisci in un passo le finestre chiuse.** `replace` (o `--map`) passa incarichi, letture, posta e risorse dalle finestre vecchie alle nuove in un solo evento registrato, conservando autori, budget, il vincolo di approvazione del piano (il successore lo fa riapprovare) e le prenotazioni dei processi ancora vivi. `--dry-run` mostra l’anteprima e ripetere è sicuro; `census --unregistered` elenca le sessioni nuove.
- **Limiti del servizio visibili.** `wait --until ORA --cause 429|outage` mostra in stato, tabella e dashboard che una finestra aspetta il servizio del modello.
- **Permessi verificati all’ingresso.** `access probe` registra, dall’interno della finestra, se può scrivere nel registro e nel suo clone; `access status` mostra verificato, negato o non verificato.
- **Una posta per finestra.** `inbox` e il suo follower leggono tutti i ruoli della stessa finestra e conservano il destinatario di ogni ACK; `ready` mostra un lettore in attesa della consegna.
- **Contesto di rientro.** `context --for FINESTRA --since SEQ:HASH` mostra cosa è cambiato per una finestra e conserva sempre vincoli, ACK dovuti, budget e prove attuali.
- **Correzioni.** `transport` non riconsegna più in silenzio un messaggio vecchio con un ID riusato; `save --repo` segnala come avvisi i repository non selezionati mancanti.
- Plugin: memoranda 1.0.10, Playwright 1.0.11, Sentry 0.1.8.

**3.11.0 — consegne congelate, letture riassegnabili e migliora**

Aggiornamento: l’avvio della dashboard libera la lettura del WAL; risposte JSON e transport brevi, deroghe registrate per singolo percorso e rifiuti di recupero più chiari.

- **Il costruttore può continuare dopo la consegna.** La copia del candidato verificato resta nel registro e contiene anche le modifiche non committate.
- **Cambia lettore senza rifare la prova.** `reassign` conserva candidato, prove e budget, richiedendo il nuovo ACK.
- **Posta e risorse più leggibili.** Il cursore della posta si salva; `resource status` mostra titolare e coda. Le prenotazioni possono indicare l’incarico.
- **Comandi più brevi.** Verdetti identici senza duplicati, note a consegna e chiusura, prova dichiarabile con argomenti diretti e risposta breve di `send`.
- **Migliora.** Una skill automatica per gli attriti dimostrati: regole circoscritte, verifica e commit locale dei propri file dopo le letture richieste dal progetto.
- Plugin: memoranda 1.0.9, Playwright 1.0.10, Sentry 0.1.7.

**3.10.4 — le prove in una cartella temporanea privata dal percorso corto, e Claude e Codex rileggono l'uno il lavoro dell'altro**

- **Le prove in una cartella temporanea privata dal percorso corto.** Le prove, i controlli rapidi, `selftest` e i benchmark offline tengono i file temporanei in una cartella privata dal percorso corto, fuori dal registro, così un progetto tenuto in una cartella condivisa come `htdocs` di MAMP non fallisce più per percorsi temporanei troppo lunghi o scrivibili da altri.
- **Rifiuti che nominano i file.** Una lettura rifiutata dice quali file sono cambiati dopo la consegna.
- **Due modelli che si controllano a vicenda.** Se scrive Codex rilegge Claude, e viceversa: un lettore dello stesso modello viene rifiutato, a meno che tu non registri il motivo. Nel blocco finale, per ogni modifica conta la lettura dell'altro modello.
- **Un consiglio su come dividere le finestre.** Il coordinatore ti dice come dividere le finestre tra i due modelli, per esempio che con 2 finestre Claude e 1 Codex la finestra Codex rileggerebbe tutto.
- Plugin: memoranda 1.0.8, Playwright 1.0.9, Sentry 0.1.6.

**3.10.3**

- **Blocco finale di revisione predefinito.** I costruttori consegnano lavoro verificato; lettori indipendenti Claude e Codex rileggono il candidato riunito prima del rilascio.
- **Continuità per i lavori lunghi.** `squadra context --task ID` prepara un pacchetto di ripresa limitato all’incarico e alle sue prove.
- **Consumi circoscritti al progetto.** I totali dei token partono dall’ingresso registrato di ogni finestra nel progetto; la quota dell’account resta separata.

<details>
<summary><strong>3.10.3: dettagli su metodo, contesto e consumi</strong></summary>

**3.10.3 — il blocco finale diventa la modalità predefinita.** Contratto comune e
giunture si fissano prima di costruire. Le corsie si uniscono alla consegna con la
propria prova verde; un registro dei delta prepara il candidato congelato per le
letture non autrici di Claude e Codex su ogni delta e giuntura. Un giro di correzione
è seguito da un blocco di rilettura dei suoi delta. Bloccano solo i reperti gravi;
una decisione raccoglie i rinvii non gravi, trasformati in incarichi all'avvio della
tappa successiva. Le prove native richieste riguardano la testa del rilascio.
Le letture per corsia restano un'opzione scelta dal proprietario. Cambia il metodo
della skill, non la CLI o il formato del registro; una squadra attiva mantiene la
modalità registrata fino al cambio esplicito. [Metodo](references/work.md#final-block).

**Contesto più piccolo nei lavori lunghi.** `squadra context --task ID` prepara un
passaggio di consegne breve, in sola lettura, con riferimenti a incarico, perimetro
e prove. Il flusso usa la compattazione nativa di Codex/Claude e conserva le
verifiche previste. Non svuota la cache del fornitore e non garantisce risparmi.
[Gestione del contesto](references/context.md).

**Correzione consumi — 20 ottobre 2026.** I totali del progetto contano solo i token
successivi all'ingresso registrato di ogni finestra. Riutilizzare una sessione più
vecchia non porta più i giorni precedenti nei grafici o nelle stime di costo del
progetto. Gli intervalli che attraversano l'ingresso sono esclusi e segnalati come
parziali. Lo storico resta intatto; la quota dell'account rimane separata.
[Perimetro e limiti](references/usage.md#project-scope).

</details>

**3.10.2 — messaggi che arrivano sempre, e una dashboard che sta al passo con una squadra al lavoro**

Il coordinatore può consegnare ogni messaggio alla sua finestra, la dashboard aggiorna solo ciò che è cambiato e la casella di ricerca trova il lavoro passato dalle parole che le persone hanno scritto. Il formato del registro non cambia.

<details>
<summary><strong>3.10.2: miglioramenti e misure della dashboard</strong></summary>

- **Si installa con una frase.** Incolla nel tuo agente la frase in [Installazione](README.it.md#one-sentence): ti mostra il piano, aspetta il tuo sì, installa e verifica ([SETUP.md](SETUP.md)).
- **Le piccole modifiche richiedono minuti.** Prima di cominciare, l'agente ti dice quanti minuti servono. Una piccola modifica si fa e basta, con al massimo un controllo. La rilettura di un'altra finestra o la suite completa dei test partono solo se le chiedete tu o il coordinatore; per una modifica importante ti vengono proposte, con il loro tempo.
- **La squadra parte da Claude.** Una finestra Codex chiamata per prima non prova più a coordinare finestre che non vede e non può raggiungere: ti dice quale frase scrivere in una finestra Claude, e quella finestra si presenta a tutte, compresa quella Codex.
- **I messaggi arrivano sempre.** Il coordinatore può consegnare qualsiasi messaggio, anche uno scritto da una finestra Codex nella sandbox, e ogni tentativo di consegna resta registrato. Una finestra Claude riceve la propria posta appena arriva con `squadra inbox --follow`. Un messaggio per una finestra Codex che nessuno ha ancora consegnato compare per primo, come «da inoltrare», con da quanto tempo aspetta.
- **Attese che dicono il vero.** Le attese elencano solo ciò che è ancora aperto: le conferme di un incarico chiuso ne escono, e un piano che aspetta il suo lettore vi compare.
- **Ricerca.** La casella di ricerca trova il lavoro passato dalle parole che le persone hanno scritto, in qualsiasi ordine e con o senza accenti, comprese decisioni, domande e letture. Il menu a tendina mostra prima i risultati più recenti e finisce con una domanda pronta per il tuo agente.
- **I più recenti in cima, ovunque.** Ogni elenco e ogni scelta dell'incarico mostra per primi gli elementi più recenti.
- **Una dashboard che sta al passo.** Dopo un nuovo evento la pagina aggiorna solo ciò che è cambiato; una pagina ferma non costa quasi nulla, e una ricerca invia solo i suoi risultati.
- **Risposte più brevi.** `task`, `brief` e `start` stampano un riepilogo breve. `inbox` senza opzioni mostra i messaggi che aspettano te più gli ultimi 10; `--all` mostra tutto.
- **Un registro più sicuro.** Una nuova cartella del registro viene ignorata da Git, così non finisce in un commit per sbaglio. Una scrittura lenta trattiene le altre finestre solo per poco. `doctor` lavora in sola lettura e segnala una cache danneggiata; `doctor --repair` la ripara e `--quick` fa un controllo veloce. I comandi spiegano gli errori a parole semplici. Un file finito nel registro per errore si può rimuovere con una decisione registrata.
- **Salvataggio e ripristino affidabili.** `save` funziona su progetti grandi e sui repository che tracciano `.env.example`; il ritorno a una versione precedente funziona con l'una e con l'altra versione; un pacchetto di salvataggio si ripristina anche dopo che l'hai aperto, e il lavoro messo da parte con lo stash torna indietro.
- **Consumi e Bilanciamento misurano ogni finestra registrata come indica la guida** (`--session`), anche sui computer con molte conversazioni salvate.
- **Si installa ovunque.** L'installatore funziona con una umask di gruppo condivisa e con una cartella home collegata. Con Python più vecchio della 3.12 si rifiuta con una riga chiara.
- **L'inglese resta inglese.** Uno spazio di lavoro in inglese riceve l'inglese in ogni comando.
- **Ingresso e presenza.** Una nuova finestra può rispondere al suo primo appello di presenza, e il giro di presenza del coordinatore lascia attivo il coordinatore stesso.
- **In fila, invece che a tentativi.** `resource acquire --wait SECONDI` tiene il posto in fila per una risorsa occupata, come la suite dei test, invece di fallire finché non si libera: le finestre che aspettano con `--wait` la ottengono nell'ordine in cui l'hanno chiesta, e mentre aspettano non si registra nulla. `prove` controlla prima di partire che la prova copra i requisiti dell'incarico, così una prova lunga non va mai buttata per un `--covers` mancante.
- **Una sola prova per le due letture finali.** `prove --also <incarico>` fa valere una sola esecuzione della suite per più incarichi sullo stesso codice congelato, come le letture finali Claude e Codex di un rilascio; ogni incarico controlla comunque i propri requisiti. Usala quando lo Squadra di ogni lettore elenca `--also` in `squadra prove --help`; altrimenti prova ogni incarico per conto suo.
- **Le finestre che non ci sono più non riempiono le attese.** La posta per una finestra messa in stand-by compare come una riga per finestra, per esempio «12 messaggi a core-A in stand-by (finestra chiusa)», e torna appena la finestra rientra.
- **Un registro che resta piccolo.** Il file di appoggio del registro torna piccolo dopo le scritture, anche con la dashboard aperta; `doctor` ne riporta la dimensione e `doctor --repair` lo compatta. Non si cancella nulla.
- **Il coordinatore coordina, e i rilasci partono prima.** Il coordinatore costruisce solo l'unione e il rilascio, e affida le corsie ai costruttori. In 3.10.2 ogni corsia aveva una propria lettura completa e le correzioni si rileggevano insieme al rilascio; 3.10.3 rende invece predefinito il blocco finale.
- Plugin: memoranda 1.0.7, Playwright 1.0.8, Sentry 0.1.5.

**Misurato sulla nostra squadra.** Il nostro coordinatore ha lanciato lo stesso script sulla dashboard della 3.10.1 pubblicata e poi della 3.10.2, sulla stessa copia del registro di questa squadra (11.488 eventi), su un computer a 8 core con un carico moderato (load average tra 5 e 11 durante le misure): cinque aggiornamenti, ciascuno dopo un nuovo messaggio, e tre ricerche. Il tempo è quello che la dashboard impiega per elaborare una richiesta. Qui la dashboard era già aperta sulla copia prima della sua prima scrittura, quindi il primo aggiornamento ha pagato una lettura completa in più (8,5 s): un registro copiato o spostato ricollega la propria cache alla prima scrittura nella nuova posizione, e una dashboard già aperta lì paga una lettura completa all'aggiornamento successivo. La tabella mostra gli aggiornamenti seguenti.

| Cosa abbiamo misurato | 3.10.1 | 3.10.2 |
|---|---|---|
| Aggiornamento della pagina dopo un nuovo messaggio | 4,7–5,3 s, 8,2 MB | 0,17 s, 0,56 MB |
| Una ricerca | 3,1–6,6 s, 8,3 MB | 0,4 s, al massimo 0,13 MB |
| Aggiornamento della pagina quando nulla è cambiato | 0,02 s | circa 0,001 s |

</details>

**3.10.1 — vedi quanto spende davvero ogni finestra AI, e a chi conviene dare il prossimo lavoro**

Una squadra mescola finestre Claude Code e Codex, e ogni fornitore ti mostra solo i propri consumi. Squadra 3.10.1 li mette tutti in un'unica vista, lavoro per lavoro: quale finestra ha speso quanti token, con quale modello, rispetto a quale quota, e a quale finestra o modello conviene dare il prossimo lavoro. Per ogni consiglio vedi anche il perché. Il calcolo avviene sul tuo computer, a partire dai file di sessione che i programmi già tengono; il testo delle conversazioni non viene salvato, mostrato né inviato da nessuna parte.

- **Bilanciamento (nuova skill di sistema).** Consigli su finestre e modelli in base a carico, quota e storia, con i numeri su cui si basa ogni consiglio. Non assegna niente. La pagina dice a parole semplici cosa controlla, i valori di oggi e perché non c'è nessun consiglio quando non ce n'è.
- **I totali dei Consumi restano stabili mentre i file di sessione crescono.** I calcoli salvati si riusano al riavvio della dashboard; un file riscritto viene ricontato, e una cache assente o danneggiata viene ricostruita. Se una cartella nel percorso della cache è un collegamento simbolico, la cache resta spenta e la pagina Consumi lo segnala: i numeri restano corretti, ma a ogni riavvio la dashboard rilegge i file di sessione.
- **Comandi che costano meno token alle finestre.** Ogni comando stampava nella finestra che lo lanciava l'intero elenco degli avvisi aperti; ora stampa una risposta breve, e `squadra status --all` stampa l'elenco completo. `task`, `brief` e `start` aggiungono la previsione e un bilanciamento breve, la prima finestra per costruire e per leggere con il suo motivo; `squadra balance --task` stampa la classifica completa (le misure sono nella tabella qui sotto).
- **Un registro che resta veloce mentre la storia cresce.** Un comando ora legge e scrive solo ciò che è cambiato, non tutta la storia; su una copia del nostro registro di 8.500 eventi, l'invio di un messaggio a un'altra finestra è passato da 27 a 3,2 secondi. La verifica dell'intera catena resta, nei momenti indicati nella guida.
- **Una dashboard più leggera** che scarica solo ciò che è cambiato, e **grafici animati** nelle pagine Consumi e Bilanciamento.
- **Messaggi solo informativi** (`send --info`) che non chiedono risposta: non restano più aperti come attese.
- Plugin: memoranda 1.0.6, Playwright 1.0.7, Sentry 0.1.4.

**Misurato sulla nostra squadra.** Il nostro coordinatore ha preso queste misure sul registro di questa squadra (circa 9.980 eventi) dopo l'installazione della 3.10.1. I due tempi «prima» vengono dagli stessi comandi cronometrati su una copia precedente dello stesso registro, prima della modifica. Una cache mancante o spostata richiede una ricostruzione verificata alla prima scrittura; il costo dipende dal registro e dalla macchina. I comandi di sola lettura ricostruiscono dai dati verificati finché la cache non è disponibile. I tempi qui sotto descrivono i comandi successivi con cache pronta, non un tempo garantito di avvio.

| Cosa abbiamo misurato | Prima | Con la 3.10.1 |
|---|---|---|
| «A che punto siamo» (`squadra status`) | 8,6 s | 0,85 s |
| Salvare un aggiornamento (`squadra output`) | 10,7 s | 0,86 s |
| Risposta stampata da un comando | 75–95 KB | circa 2,2 KB; `task`, `brief` e `start` circa 2,0 KB con 10 finestre (squadra sintetica), previsione e bilanciamento compresi |
| Aggiornamento della dashboard quando nulla è cambiato | 8 MB | 0,2 KB |
| Totali dei Consumi mentre le finestre lavorano | salivano e scendevano | solo in crescita (50 campioni, nessuno più basso) |

**3.10.0**
- **Pagina «Consumi»: i token spesi dai modelli.** Squadra legge i file di sessione che Claude Code e Codex tengono già sul tuo computer e ne conserva solo i conteggi dei token, con modello, orario e gli identificativi di sessione e di messaggio che servono a contarli una volta sola: non salva, non mostra e non invia mai il testo delle conversazioni. Li mostra per finestra, giorno, modello e incarico, con grafici e suggerimenti per modello. La quota di Codex è misurata, per ogni finestra del fornitore; un costo stimato compare solo se inserisci i tuoi prezzi. `squadra usage` stampa gli stessi dati.
- Plugin: memoranda 1.0.5, Playwright 1.0.6, Sentry 0.1.3.

**3.9.1**
- **Ritocchi all'interfaccia.** Più spazio nella pagina «Skill di sistema» e tra i suoi blocchi; il grassetto nelle descrizioni delle skill appare come grassetto; l'obiettivo del progetto e la guida alla lettura compaiono solo nella panoramica; più badge in cima a questa pagina.
- Plugin: memoranda 1.0.4, Playwright 1.0.5, Sentry 0.1.2.

**3.9.0**
- **Skill di sistema che si attivano da sole.** *errata* riporta a galla le lezioni che la squadra ha registrato dopo una prova fallita, una lettura che chiedeva modifiche o la chiusura di una correzione, quando un nuovo incarico tocca gli stessi file o le stesse parole. *rasoio* è una lente per il codice snello nelle letture del codice, che dichiara quante righe si potrebbero togliere. *previsione* confronta un nuovo incarico con quelli simili già chiusi e suggerisce un percorso. errata e rasoio registrano ogni attivazione nel registro; previsione viene calcolata al momento e non registra nulla.
- **Una pagina «Skill di sistema» nella dashboard** elenca le tre skill con cosa fanno, la loro attività registrata e l'esito del controllo di sicurezza sui loro file.
- **Prove sull'intera suite più veloci.** `tools/parallel_tests.py` esegue la suite su più processi e fallisce se un test raccolto non è stato eseguito: circa tre volte più veloce sulla nostra macchina di prova.
- **Più lettori a disposizione.** Un incarico può indicare fino a quattro lettori; il primo che conferma prende la lettura. Le piccole modifiche leggere si possono leggere una sola volta, al controllo di rilascio, e fino ad allora risultano chiuse ma non ancora lette.
- **Controlli rapidi prima della prova e della consegna.** Un progetto può dichiarare controlli veloci, per esempio che i file copiati siano identici, eseguiti sui file esatti prima di `prove` e `deliver`.
- **Un README più leggero.** Stesse sezioni, circa metà della lettura; i dettagli sono passati nelle guide.
- **Il rilascio parte in orario.** È una regola, non una scelta: l'ora limite è due ore dopo che il contenuto è fissato; le riletture finali guardano solo come sono state unite le corsie quando ognuna è già stata letta; una lettura che nessuno prende passa a un lettore libero.
- Plugin: memoranda 1.0.3, Playwright 1.0.4, Sentry 0.1.1.

**3.8.0**
- **Le correzioni di difetti dimostrano che il loro test fallisce senza di loro.** Dichiara una correzione con `task --defect-fix`; prima della chiusura, `challenge` esegue i test concordati sul codice originale e su quello consegnato.
- **Plugin Sentry (anteprima).** Porta in Squadra riepiloghi ripuliti degli errori. Gratis · Sviluppo finanziato da memoranda. Provato solo con una connessione simulata.
- **Una vista Letture più comoda.** Mantiene lo scorrimento della tabella, mostra schede sul telefono e perde lo spazio vuoto; i passi numerati dei plugin hanno un solo segno.
- Plugin: memoranda 1.0.2, Playwright 1.0.3, Sentry 0.1.0.
