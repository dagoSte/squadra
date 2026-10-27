# Sostituire le finestre durante un milestone

La funzione arriva con la **3.11.1**. Aggiornare
tutte le finestre prima di usarla: replace, wait e le impostazioni di onboarding
scrivono il formato 4 del registro. Le build 3.11.0 e precedenti lo rifiutano, così
non ignorano lo stato nuovo. Conservare il backup e riprendere con il lettore aggiornato.

## Censimento e anteprima

```sh
squadra census --root REGISTRO --unregistered
squadra replace --root REGISTRO --actor coord --old codex-vecchio --new codex-nuovo \
  --reason 'Finestra esaurita, sostituita dal proprietario' --session SESSIONE_VERIFICATA --dry-run
```

Il censimento legge i metadati delle sessioni Codex con cwd uguale al progetto, a
un suo worktree Git o a un repository registrato. Esclude le sessioni già registrate;
non legge conversazioni e non dimostra che una finestra sia viva. Senza `--session`,
un nuovo destinatario Codex o Claude produce una proposta, senza modifiche.
Il coordinatore può esportare i peer Claude come array JSON di oggetti
`{"session":"ID","cwd":"/progetto"}` e passarli a
`census --unregistered --peers-file peers.json`. Se la fonte non è disponibile,
il limite viene dichiarato. Cursor usa il trasporto manuale.

`--dry-run` mostra l'intero piano, i messaggi e i riferimenti agli artefatti senza
scrivere il registro, le proiezioni o le code. Per applicarlo ripetere senza il flag.
`--client codex|claude|cursor` vale per le identità nuove; quelle note mantengono
sessione e client. Un ruolo di lettore su una finestra costruttrice usa il nome
`<nuovo>-read` con la stessa identità: non crea indipendenza fittizia.

## Che cosa viene trasferito

Un solo evento append-only mette in stand-by tutti gli alias delle vecchie
sessioni, crea gli incarichi successori e trasferisce le letture. Il successore
mantiene perimetro (con intestazione `taken over from <old>`), repository, comando
di prova, autori precedenti e budget consumati. La base diventa l'HEAD corrente;
serve una nuova prova. Un piano richiesto mantiene l'artefatto esatto e richiede una
nuova approvazione indipendente, anche se quello precedente era accettato. I verdetti
restano sul predecessore collegato da `taken_over_from` e i turni consumati non si
azzerano. Anche una seconda sostituzione conserva il vincolo. Le dipendenze aperte
seguono i successori. I contratti light coinvolti si ricalcolano dopo le dichiarazioni
finali di lettore e dipendenze: le vecchie prove non valgono per il nuovo contratto e
i limiti della policy light restano invariati. Una lettura già
consegnata conserva prova e copia congelata, ma richiede un nuovo ACK. Restano i
controlli di indipendenza e le regole sui client: un autore o alias non può leggere
il proprio lavoro come indipendente.

La posta pendente resta nello storico, collegata alla sostituzione, ed esce dalle
attese senza inventare ACK. Ogni nuova finestra riceve un digest con registro,
identità, regole del progetto e incarichi; assegnazioni e letture conservano i loro
messaggi e ACK obbligatori.

- `--carry useful`: contesto ordinario pendente, esclusi ping, assegnazioni e
  handoff generati e messaggi su incarichi già sostituiti.
- `--carry all`: include anche il contesto ordinario sugli incarichi già sostituiti.
- `--carry none`: solo presentazione e incarichi, senza contesto copiato.

Stop/resume/pause identificati dal prefisso dell'ID o dalla prima parola del testo
restano sempre nello storico. Il filtro è deterministico: verificare l'anteprima
se i messaggi hanno formulazioni diverse. Nelle impostazioni si può aggiungere
`"onboarding_template":"Leggi AGENTS.md e il perimetro; conferma prima di partire."`
insieme alle soglie, per esempio `"alive":300`. Il testo è letterale, massimo
8192 byte UTF-8, e non concede autorizzazioni.

## Sicurezza, trasporto e ripetizione

Un ACK recente (entro la soglia `alive`) o un run vivo/non verificato richiede
`--force` con una motivazione non vuota. Force non termina processi: i run incerti
restano visibili e bloccano l'esecuzione del successore fino alla riconciliazione.
Solo i processi verificati assenti, senza risultati né ricevuta terminale, vengono
registrati come abbandonati sotto il loro lock. Le riserve delle identità o sessioni
con run vivi/incerti restano occupate, incluse quelle dei loro alias di ruolo: altri
incarichi non possono acquisirle. Prima di rilasciarle esplicitamente occorre riconciliare
i run a uno stato terminale verificato. La sostituzione rilascia le riserve solo quando
nessun run irrisolto della stessa identità/sessione può ancora usarle.

L'atomicità riguarda il registro. Il trasporto esterno avviene dopo il commit, con
ID deterministici e ricevute separate del tentativo e dell'esito. Codex usa
`codex-queue` con sessione nota; per Claude/Cursor il coordinatore riceve istruzioni
manuali e usa i peer disponibili. La ricevuta di coda non è un ACK. Ripetere la stessa
sostituzione non duplica eventi o invii già tentati; riprende soltanto quelli mai
tentati. Dopo un'interruzione ispezionare la
ricevuta e la posta prima di un esplicito `transport --force`: l'invio potrebbe
essere già avvenuto prima dell'interruzione.

Se `send --id NOME` trova un ID esistente, genera e stampa un ID con suffisso:
usare quello stampato. Transport rifiuta senza force messaggi sostituiti o anteriori
alla registrazione della sessione attuale. Una nuova registrazione della stessa
sessione non rende vecchia la sua posta.

## Limiti del servizio e salvataggio parziale

```sh
squadra wait --root REGISTRO --actor codex-nuovo --until 19:30 --cause 429
squadra wait --root REGISTRO --actor codex-nuovo --until 2026-10-26T19:30:00+01:00 --cause outage
```

Il comando registra l'attesa, senza dormire, ritentare richieste o interrompere
processi. Status, board e dashboard la mostrano fino alla scadenza; lo storico
resta. HH:MM indica la prossima occorrenza nell'ora locale del chiamante; il formato
ISO richiede il fuso. La cattura automatica di 429/5xx non è implementata: Squadra
non vede gli errori HTTP nascosti dalle finestre interattive. Un hook del client
può invocare il comando con la scadenza effettivamente nota.

`save --repo ROOT` controlla i repository selezionati e i limiti della destinazione.
I repository registrati mancanti ma non selezionati producono avvisi nel MANIFEST;
un repository selezionato mancante continua a far fallire il salvataggio. Le fonti
omesse non vengono recuperate da un salvataggio parziale.

## Caso hotskills del 26 ottobre 2026

Il caso segnalato richiese circa venti comandi manuali: quattro vecchie identità
Codex verso tre nuove finestre, undici identità in stand-by. Gli ID e le sessioni
seguenti sono **illustrativi**, non identificatori ricostruiti dall'incidente.
Le quattro finestre vecchie hanno rispettivamente 3, 3, 3 e 2 nomi registrati.

```sh
squadra census --root REGISTRO --unregistered
squadra replace --root REGISTRO --actor coord \
  --map old-a=new-a,old-b=new-a,old-c=new-b,old-d=new-c \
  --session new-a=SESSIONE_A --session new-b=SESSIONE_B --session new-c=SESSIONE_C \
  --client codex --reason 'Il proprietario ha sostituito le finestre esaurite' --dry-run
# Verificare il piano e ripetere senza --dry-run.
squadra board --root REGISTRO
squadra inbox --root REGISTRO --agent new-a
```

Il risultato atteso è tre digest, undici vecchi nomi in stand-by e la provenienza
`new-a (sostituisce old-a, old-b)` sulla board. Le nuove finestre leggono e confermano
gli effettivi messaggi di assegnazione/lettura prima di partire. Usare force solo
dopo aver riconciliato gli avvisi su risposte recenti o run e conservarne la ragione.
Il selftest comprende questo esempio sintetico, oltre ai casi singolo/mappa,
indipendenza, digest, idempotenza, anteprima, trasporto, salvataggio e attese.
