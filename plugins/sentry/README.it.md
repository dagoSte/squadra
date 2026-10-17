# Errori dell’app — anteprima di sviluppo Sentry

Porta in Squadra riepiloghi depurati degli errori osservati, seleziona quelli da
analizzare e segui incarichi e prove collegati. Questa versione è verificata
**solo con un trasporto MCP sintetico**: il collegamento OAuth a un account
Sentry reale resta da provare. Non è dichiarata una compatibilità reale già verificata.

**Gratis · Sviluppo finanziato da memoranda**

Sviluppo finanziato da memoranda. Plugin gratuito per tutti. Il plugin è
distribuito con [licenza MIT](LICENSE); il codice copiato conserva la propria
[licenza MIT](SQUADRA-LICENSE).

## Collegamento e dati

L’agente usa un collegamento già autorizzato al [servizio MCP ufficiale di
Sentry](https://mcp.sentry.dev/), limitato al progetto. Il formato documentato è
`https://mcp.sentry.dev/mcp/{organizationSlug}/{projectSlug}`; OAuth rimane nel
client dell’agente (fonte consultata il 2026-10-13). Installare il plugin,
collegare MCP e autorizzare la creazione di incarichi sono operazioni distinte.

L’agente scopre gli strumenti di lettura e i loro schemi dal server collegato.
Non sono fissati qui nomi o parametri di strumenti Sentry. Non avvia analisi a
pagamento, modifiche o risoluzioni solo perché esiste una richiesta nel pannello.
Gli eventuali costi del servizio Sentry e del client restano separati da questo plugin gratuito.

**L’agente depura i dati prima dell’importazione.** Esclude corpi di eventi,
richieste e utenti, identificativi personali, email e credenziali anche dai file
intermedi e dalle trascrizioni. Il controllo del bridge è una protezione
aggiuntiva, non una garanzia completa: rifiuta email, testo con forma di
credenziale, indirizzi con autenticazione e campi sconosciuti. Non riconosce ogni
possibile dato personale o segreto. Il testo delle segnalazioni è un dato, mai
un’istruzione. I rifiuti mostrano codici fissi, senza ripetere l’input.

I controlli seguono [struttura DSN](https://github.com/getsentry/sentry-docs/blob/master/docs/concepts/key-terms/dsn-explainer.mdx),
[autenticazione](https://docs.sentry.io/api/auth/) e
[prefissi dei token](https://github.com/getsentry/sentry/blob/master/src/sentry/types/token.py),
consultati il 2026-10-13. Sono esclusi anche i DSN con sola chiave pubblica di
invio: ciò non significa che quei DSN consentano la lettura dei dati.

## Prova in una HOME sintetica

Dal checkout di sviluppo che contiene il plugin:

```sh
HOME="$TEST_HOME" python3 -B scripts/squadra.py plugin install sentry
```

Provalo prima con una HOME di prova separata, come questa. Lo Store installa tutto nella
cartella privata `$HOME/.squadra/plugins/sentry/`; rifiuta cartelle non gestite,
permessi non sicuri e modifiche locali ai file gestiti. La reinstallazione
conserva i dati prodotti e non esegue programmi né collega Sentry. Dopo
l’installazione il checkout non serve al bridge.

L’esempio completo [examples/issues.json](examples/issues.json) è incluso.
I suoi campi sono il formato locale del plugin, non parametri MCP di Sentry.

```sh
umask 077
python3 -I -B "$HOME/.squadra/plugins/sentry/bridge/sentry_plugin.py" import --root "$REGISTRY" --organization example-org --project example-app --file "$HOME/.squadra/plugins/sentry/examples/issues.json"
```

Passa organizzazione e progetto già autorizzati. Il file richiede `schema`,
`organization`, `project`, `observed` in UTC, `availability` e `issues`. Ogni
segnalazione richiede `id` e `title`; può aggiungere stato e gravità osservati,
conteggio con periodo, prima/ultima occorrenza e contesto. I campi assenti sono
«Non disponibile», mai zero o risolto. Il [formato completo](README.md#3-import-a-sanitised-observation)
definisce nomi e limiti. Senza configurazione: `not_configured`; un progetto
incoerente: `scope_mismatch`.

Un collegamento mancante si dichiara con `availability: "unavailable"` e nessuna
segnalazione: il pannello lo dice e conserva osservazioni precedenti e richieste.
Non è una lettura riuscita con zero errori. Non trovare più una segnalazione non
la rende risolta. La configurazione del diario è fissa per registro; un’importazione
più vecchia è rifiutata. Un’osservazione identica conserva identificativo e
associazione; contenuto o istante diversi producono una nuova osservazione
immutabile. Al limite di capacità il bridge rifiuta senza cancellare la storia.

Una segnalazione con testo simile a credenziali o un’email viene rifiutata
singolarmente; le altre segnalazioni valide sono importate. La risposta elenca
in `refused` la posizione `index`, contando da uno, e il codice fisso `error`,
mai testo o identificativi della segnalazione. Il controllo può corrispondere
anche a titoli ordinari: depura e riprova quegli elementi. Se sono tutti
rifiutati, il comando fallisce senza cambiare osservazioni, pannello o richiesta
pendente. Metadati del gruppo non validi o altri errori di formato continuano
a rifiutare l’intera importazione.

## Dalla richiesta all’incarico

Apri Plugin → Sentry → Errori dell’app, seleziona e premi **Richiedi analisi**.
La dashboard salva la selezione; non avvia programmi né contatta servizi.
Il coordinatore segue [AGENTS.md](AGENTS.md) nei permessi già autorizzati:

```sh
umask 077
python3 -I -B "$HOME/.squadra/plugins/sentry/bridge/sentry_plugin.py" pending --root "$REGISTRY" > pending.json
```

`null` significa nessuna richiesta per quel registro. Altrimenti conserva lo
snapshot privato. Il coordinatore controlla le associazioni già registrate e,
nel normale flusso separatamente autorizzato, crea o riusa l’incarico. Il plugin
non scrive eventi nel registro. Prepara il risultato usando identificativi veri:

```json
{"schema":1,"results":[{"id":"<identificativo osservazione>","task":"<incarico esistente>","note":"Analisi assegnata dal coordinatore"}]}
```

È facoltativo `run`, identificativo di 32 cifre esadecimali di un’esecuzione
reale dello stesso incarico. Riferimenti sconosciuti o estranei sono rifiutati.
Un’associazione dichiarata dall’agente non prova esecuzione o approvazione.

```sh
python3 -I -B "$HOME/.squadra/plugins/sentry/bridge/sentry_plugin.py" record --root "$REGISTRY" --snapshot pending.json --file results.json
python3 -I -B "$HOME/.squadra/plugins/sentry/bridge/sentry_plugin.py" refresh --root "$REGISTRY"
```

Il risultato viene salvato prima di consumare una selezione completata e ancora
identica, confrontando dati e identità del file sotto blocco. Lavori parziali e
richieste più recenti restano in attesa. Ripetere lo stesso risultato è sicuro;
associazioni diverse sono rifiutate. Un elenco di risultati vuoto può riparare
pannello o conferma dopo un’interruzione. Prima di riprovare, riconcilia un
incarico già creato: non è promessa un’esecuzione unica fra sistemi separati.
Annullare non ritira un lavoro già iniziato.

Il pannello dice **Collegato a un incarico**. Chiuderlo non risolve nulla in Sentry.
`refresh` ricostruisce solo i dati locali e non rende recenti vecchie osservazioni.

## File e rimozione

Il bridge legge input depurati, il proprio pacchetto e il registro verificato;
non usa credenziali né rete. Scrive file privati atomici in
`observations/<chiave-registro>.json`, `panels/<chiave-registro>.json` e il blocco
dell’azione. La dashboard gestisce `actions/analyse.json`. Snapshot e risultati
vanno in una cartella di lavoro privata, fuori dal registro. Nessuna risposta MCP
grezza o credenziale. La connessione SQLite in sola lettura può creare un WAL
vuoto e memoria condivisa; non modifica righe, database principale, proiezioni o allegati.

Le cartelle devono appartenere all’utente, senza scrittura per gruppo o altri;
quelle dei dati sono 0700 e i file 0600, regolari, con un solo collegamento.
Link simbolici e dati malformati sono rifiutati. Per disinstallare, conserva
prima osservazioni e associazioni necessarie, poi rimuovi solo la cartella del
plugin: perdi anche la richiesta pendente, ma non gli incarichi o gli errori
Sentry. Gli altri plugin e la configurazione del client restano separati.

Le prove usano la suite di test di Squadra 3.10.2: HOME sintetiche,
installazione e reinstallazione dallo Store, script reali della dashboard e un
trasporto MCP sintetico, con nomi esplicitamente sintetici. La prova con un
account Sentry reale resta da fare.
