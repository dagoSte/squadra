# Informativa privacy di Squadra

Aggiornata il 15 ottobre 2026. [English](PRIVACY.md).

Squadra è un software che esegui sul tuo computer. Il suo autore, Stefano
D'Agostino, non gestisce un servizio che raccoglie dati da Squadra. Il registro
di coordinamento e la dashboard non richiedono un account presso l'autore.

## Cosa viene conservato sul tuo computer

Il registro scelto con `--root` conserva descrizioni di progetti e incarichi,
identità dei partecipanti, messaggi, decisioni, date e orari, revisioni,
registrazioni dei comandi, impronte dei sorgenti e riferimenti alle prove.
Gli allegati registrati, i prompt e gli output dei comandi possono contenere
file di progetto, nomi, percorsi, dati personali o materiale riservato forniti
da te o dai tuoi agenti. Servono a coordinare e riprendere il lavoro e a
collegarlo alla sua prova e alla sua revisione.

Il registro comprende `registry.sqlite3`, documenti derivati e la cartella
`artifacts/`. Squadra crea la cartella del registro con permessi `0700` e il
database con `0600`; le cartelle superiori già esistenti e le copie che fai
hanno i propri permessi. I permessi limitano l'accesso locale, ma non cifrano
il contenuto. Non inserire password, chiavi API o dati personali non necessari
in messaggi, prompt, allegati o output dei comandi. La dashboard non permette
di modificare selettivamente gli allegati registrati.

I plugin della dashboard si trovano in `~/.squadra/plugins/<name>/`, nella HOME
dell'utente che esegue Squadra. Le cartelle possono contenere manifesti, dati
del pannello e richieste di azione salvate. Lo Store crea le cartelle dei
plugin gestiti con `0700`. Le impostazioni dichiarate vengono salvate in
chiaro in `settings.json`, con `0600`; i segreti dichiarati sono file separati
in chiaro `secrets/<id>`, con `0600`, dentro una cartella `0700`. Non è una
cassaforte cifrata. L'API delle impostazioni non restituisce i valori dei
segreti salvati e non li copia nel registro. La dashboard lascia al produttore
o consumatore separato del plugin la gestione delle richieste salvate; non
esegue direttamente quei programmi.

La pagina «Consumi» e `squadra usage` leggono i file di sessione che Claude Code e
Codex tengono già nella tua HOME, per le finestre registrate nella squadra.
Leggono ogni riga e ne conservano in memoria solo i conteggi dei token, i nomi
dei modelli, gli orari e i dati di quota di Codex; il testo delle conversazioni
non viene mai salvato, mostrato né inviato. I prezzi e i valori di riferimento
che inserisci nella pagina restano nella memoria locale del tuo browser e non
vengono scritti nel registro.

## La dashboard e i servizi esterni

La dashboard ascolta su `127.0.0.1` e controlla il proprio token di accesso e
l'Host. Serve localmente caratteri, script e icone inclusi e mostra le
registrazioni e i dati dei plugin installati. Aprire un collegamento che
scegli può raggiungere un sito esterno. Il browser e il gestore di password
hanno le proprie impostazioni e modalità di trattamento dei dati.

Squadra non carica automaticamente il registro presso l'autore. Questo non
significa che ogni comando coordinato funzioni senza rete. I client degli
agenti che autorizzi possono inviare prompt, codice e contesto del lavoro ai
propri fornitori, con il tuo account. I plugin facoltativi e i servizi MCP
possono inviare i dati selezionati ai servizi che configuri. Le operazioni Git,
le azioni nel browser, i trasporti dei messaggi e i comandi di prova autorizzati
hanno i propri effetti. Prima di usarli, controlla permessi, destinazioni e
informative dei relativi strumenti.

Il plugin memoranda, disponibile nello Store, è uno di questi plugin facoltativi.
Non invia nulla finché non lo installi e non imposti la tua chiave di
memoranda.dev. Da quel momento invia all'indirizzo memoranda.dev che configuri le
voci che i tuoi agenti hanno dichiarato da ricordare, come proposte da rivedere:
per ogni voce la frase, il tipo, l'ambito, la confidenza, le etichette e i
riferimenti alle prove nel registro (numeri di evento, nomi e hash degli
allegati), non i file delle prove. memoranda.dev le tratta secondo la propria
informativa.

## Conservazione, rimozione e copie condivise

I dati locali restano finché non li rimuovi; non esiste una scadenza automatica
di conservazione. Prima di rimuoverli ferma la dashboard, gli agenti e il
produttore o consumatore del plugin interessati. Rimuovi la cartella del
registro scelto per eliminarne database, proiezioni e allegati registrati.
Rimuovi la cartella di un plugin per eliminarne impostazioni, segreti, dati
del pannello e richieste locali. Non rimuovere una HOME condivisa o altri
plugin. Disinstallare soltanto la skill Squadra non elimina queste cartelle.

Esportazioni, checkpoint, bundle Git, archivi del lavoro non ancora registrato
in Git, cronologie dei client e backup sono copie separate. Conservale o
eliminale secondo le tue regole. La cancellazione locale non elimina le copie
già inviate ad altri servizi: usa i controlli del servizio interessato. La
cancellazione dal filesystem non garantisce una cancellazione sicura.

## Domande e informazioni che scegli di condividere

Usa le [issue GitHub di Squadra](https://github.com/dagoSte/squadra/issues) per
domande su questa informativa o segnalazioni. Le issue sono pubbliche: oscura
i dati riservati in log, schermate e archivi prima di condividerli e non
pubblicare credenziali o informazioni private dei clienti. Ciò che invii viene
conservato da GitHub e può essere letto dall'autore e da altre persone; si
applicano l'[informativa privacy di GitHub](https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement)
e i suoi controlli di cancellazione. Squadra non impone una scadenza automatica
separata alle issue GitHub o alle copie fatte da altre persone.
