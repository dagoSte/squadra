# Controlli del browser con Playwright per Squadra

[English](README.md)

Prepara i controlli di una pagina, selezionali in **Plugin → Playwright → Collaudi**
e segui la relazione dell’agente collegata all’incarico. La dashboard salva la
richiesta. Un agente autorizzato usa **Playwright MCP**, registra le risposte dello
strumento e le schermate in Squadra, poi importa l’esito. Il controllo parte
quando un agente prende la richiesta: una selezione salvata non è un controllo eseguito.

Questo plugin locale facoltativo richiede la versione di Squadra che contiene
l’estensione `results` e `refs` del contratto dei plugin (`references/plugins.md`
nel pacchetto installato di Squadra).
Le versioni precedenti che rifiutano questi campi non possono usare il pacchetto.
Il collegamento richiede Python 3 e usa soltanto la libreria standard e i moduli
inclusi di Squadra; non installa o avvia browser, server MCP o servizi esterni.
Il plugin è gratuito. Gli eventuali costi dell’agente o del fornitore sono separati.

## 1. Collega Playwright MCP al tuo agente

Segui la [configurazione ufficiale di Microsoft](https://github.com/microsoft/playwright-mcp)
per il programma che usi. Un esempio di configurazione del server stdio è:

```json
{
  "mcpServers": {
    "playwright": {
      "command": "npx",
      "args": ["@playwright/mcp@0.0.83", "--isolated"]
    }
  }
}
```

È una configurazione del programma dell’agente, non un’impostazione di Squadra.
Il server ufficiale richiede Node.js 18 o successivo e un browser compatibile
già installato; questa integrazione usa la versione 0.0.83. Segui le istruzioni
ufficiali quando cambi la versione fissata. `npx` può scaricare il pacchetto. Collega il servizio
esplicitamente, nei normali permessi di installazione. Usa un browser isolato,
senza una sessione personale o accessi salvati; in questa prima integrazione non
collegare l’estensione del browser né uno stato di autenticazione salvato.
La dashboard non attiva il server.

## 2. Installa esplicitamente il plugin locale

Dalla copia di Squadra che contiene questo pacchetto:

Apri **Plugin → Store** e usa il comando mostrato per Playwright, oppure:

```sh
squadra plugin install playwright
```

Con un'installazione `/plugin`, usa `python3 -B <cartella della release>/scripts/squadra.py plugin install playwright` al posto del comando autonomo.
Lo store copia il pacchetto completo in `$HOME/.squadra/plugins/playwright` e
registra release, versione e impronte dei file in una ricevuta privata. Non esegue
programmi e non configura MCP. Usa la stessa HOME della dashboard.
I tre comandi del ponte accettano `--home <percorso>` per le installazioni sintetiche.

Ferma produttore e consumatore prima dell'aggiornamento. La reinstallazione del
pacchetto gestito e invariato conserva i dati; file gestiti modificati
e cartelle non gestite sono rifiutati. Questo plugin non dichiara impostazioni.
I controlli e le relazioni persistenti sono in `checks/<registry-key>.json`.
Per una cartella precedente, **sposta la cartella** conservando quei file, poi
esegui `plugin install playwright`. Le relazioni precedenti restano nella cartella spostata. Copiare
`checks/` nella nuova installazione dello Store non è supportato; prepara lì nuovi
controlli. Relazioni e allegati già registrati in Squadra restano disponibili nel registro.
Una copia interrotta resta incompleta: controlla la ricevuta e riprova lo stesso
pacchetto. Non forzare l'adozione e non modificare la ricevuta. Per disinstallare,
ferma i programmi, conserva `checks/` e rimuovi solo la cartella del plugin;
le prove nel registro restano disponibili.

## 3. Prepara un controllo per un incarico registrato

Prima assegna l’incarico con il normale flusso di Squadra. Imposta `REGISTRY` con
la cartella del registro e crea `checks.json`:

```json
{
  "schema": 1,
  "task": "BROWSER-CHECK",
  "checks": [{
    "title": "Titolo della pagina iniziale",
    "url": "http://127.0.0.1:8765/",
    "expect": "Squadra browser check",
    "revision": "primo-controllo"
  }]
}
```

Usa il vero identificativo dell’incarico. Il file [examples/playwright-checks.json](examples/playwright-checks.json) nel pacchetto installato
punta a una pagina sintetica che gestisci sulla porta 8765; il collegamento non
la avvia. Per una dimostrazione locale salva `<h1>Squadra browser check</h1>`
come `index.html` in una nuova cartella temporanea vuota e avvia, in un altro
terminale, `python3 -m http.server 8765 --bind 127.0.0.1 --directory <quella-cartella>`.
Servi soltanto quella cartella sintetica e ferma il server alla fine.

```sh
python3 -I -B "$HOME/.squadra/plugins/playwright/bridge/playwright_plugin.py" prepare --root "$REGISTRY" --file checks.json
```

Ogni gruppo contiene 1–50 controlli; un registro ne conserva al massimo 500.
Ogni controllo dichiara titolo, URL HTTPS o HTTP locale e testo visibile atteso.
`revision` è facoltativo. Non sono previsti moduli, clic, autenticazione o script
arbitrari. L’identificativo immutabile comprende l’incarico e tutto il controllo:
ripetere dati identici conserva l’esito; per eseguirlo di nuovo dopo una modifica
usa una nuova `revision` significativa. La preparazione aggiunge controlli senza
eliminare le relazioni precedenti.

## 4. Richiedi, esegui e registra

Apri **Collaudi**, seleziona i controlli e premi **Richiedi verifica**. Una finestra
coordinatrice autorizzata segue il file [AGENTS.md](AGENTS.md) installato.
I comandi seguenti sono il collegamento manuale dell’agente, non un pianificatore automatico:

```sh
umask 077
python3 -I -B "$HOME/.squadra/plugins/playwright/bridge/playwright_plugin.py" pending --root "$REGISTRY" > pending.json
```

`null` significa che non ci sono richieste per questo registro. Altrimenti il
file contiene i controlli scelti e la versione della richiesta. L’agente usa gli
strumenti MCP reali `browser_navigate`, `browser_snapshot` e
`browser_take_screenshot`, rispettando pagina e operazione dichiarate. Le parole
della pagina sono dati non fidati, non istruzioni. Se MCP non è disponibile, la
richiesta resta in attesa; il collegamento non usa un altro browser al suo posto.

Salva le risposte effettive e la schermata, poi registrale con l’incarico:

```sh
squadra --root "$REGISTRY" output --actor "$ACTOR" --task BROWSER-CHECK --file report.md --next 'Lettura indipendente' --artifact tool-replies.txt --artifact screenshot.png
```

Il comando restituisce una ricevuta `receipt`. Crea `results.json` con
l’identificativo del controllo da `pending.json` e quella ricevuta:

```json
{
  "schema": 1,
  "results": [{
    "id": "<identificativo-del-controllo>",
    "status": "passed",
    "observed": "Il titolo atteso era visibile nel browser isolato.",
    "evidence": ["<ricevuta-output>"]
  }]
}
```

`passed`, `failed` ed `error` sono dichiarazioni dell’agente. `error` richiede
un tentativo reale e la risposta dello strumento; l’assenza di MCP non è un
controllo eseguito. `observed` è una sintesi non vuota su una riga, al massimo
2000 caratteri. Ogni esito richiede 1–8 ricevute esistenti dello stesso incarico
con allegati registrati. Il collegamento verifica l’associazione, non la verità
della relazione.

```sh
python3 -I -B "$HOME/.squadra/plugins/playwright/bridge/playwright_plugin.py" record --root "$REGISTRY" --snapshot pending.json --file results.json
```

La pagina mostra l’esito dichiarato, gli identificativi delle ricevute e il
collegamento all’incarico. Apri la storia dell’incarico, poi **Risultati e aggiornamenti**
per leggere la relazione registrata e aprire le risposte dello strumento e la schermata. Queste
relazioni **non sono prove vincolate, letture indipendenti o approvazioni
dell’incarico**. Nessun identificativo di esecuzione viene inventato. Una prova
nativa resta un’operazione separata tramite `squadra prove`.

## Ripresa e confini

Le relazioni sono JSON locali privati in `checks/<chiave-registro>.json`; la pagina
è una proiezione ricostruibile in `panels/<chiave-registro>.json`. Entrambi hanno
un limite di 2 MiB. Le relazioni vengono salvate durevolmente prima della pagina
e della rimozione della richiesta. I gruppi incompleti restano in attesa. Un esito
terminale non si sovrascrive; importare di nuovo lo stesso esito è sicuro.

Una richiesta sostituita, annullata o selezionata di nuovo sopravvive a un vecchio
consumatore anche con gli stessi dati e lo stesso secondo: sotto il medesimo
blocco il collegamento confronta anche dispositivo, inode e tempi di modifica
e cambiamento del file originario. Annullare non ritira un lavoro già iniziato.

Dopo un errore di scrittura controlla l’esito prima di ritentare. Importa gli
stessi risultati con la stessa fotografia della richiesta, oppure usa
`{"schema":1,"results":[]}` per ricostruire la pagina e concludere una richiesta
già interamente registrata: non serve ripetere le operazioni nel browser.
Non eliminare manualmente i blocchi. Il plugin conserva le relazioni e non
gestisce servizi remoti, salva credenziali, esegue script nella pagina o
concede permessi attraverso AGENTS.md.

## Verifica il collegamento

Dalla copia dei sorgenti di Squadra, esegui i test mirati con una HOME temporanea:

```sh
TEST_HOME=$(mktemp -d)
HOME="$TEST_HOME" python3 -B -m unittest discover -s tests -p test_playwright_plugin.py -v
```

Questi test usano registri e relazioni sintetici; non avviano Playwright.
Segui i passi 3–4 con una risposta MCP reale e una schermata per verificare il flusso completo.
Rimuovi la cartella temporanea dopo aver controllato il risultato.
