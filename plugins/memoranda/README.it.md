# memoranda per Squadra

[English](README.md)

Conserva decisioni, vincoli e lezioni utili alla chiusura degli incarichi. Ogni
finestra dichiara cosa ricordare; quella coordinatrice raccoglie le dichiarazioni,
propone le bozze idonee e legge gli esiti. Il plugin non approva mai memorie.
L'approvazione segue la regola scelta per l'area, manuale per impostazione iniziale.
Se l'area approva automaticamente, altri invii si fermano finché il proprietario
non accetta esplicitamente quella regola.

Installare il plugin MIT è gratuito. Il servizio memoranda.dev inizia con una
prova gratuita, poi è a pagamento. Le condizioni attuali sono su
[memoranda.dev](https://memoranda.dev): qui non promettiamo durata o prezzo.
Editore: memoranda.

## Collegamento iniziale

1. Accedi alla console memoranda.dev con il secondo fattore. Apri **Configurazione
   guidata → Configura il tuo client** e copia **L'indirizzo del tuo tenant**,
   nella forma `https://mcp.memoranda.dev/t/<spazio>/mcp`.
2. In **Agenti e chiavi** assegna nome e identità all'agente e crea la chiave.
   È mostrata una sola volta. Non inserirla nella chat o in un comando.
3. Chiedi alla finestra coordinatrice l'installazione esplicita: `squadra plugin
   install memoranda`. Con un'installazione tramite plugin del client usa
   `python3 -B <release>/scripts/squadra.py plugin install memoranda`.
   L'installazione copia file: non contatta memoranda.
4. Apri **Plugin → memoranda → Impostazioni** in Squadra, salva indirizzo e chiave.
   La chiave resta in un file locale leggibile solo dall'utente e non è mostrata
   di nuovo: appare solo se è presente. Per scollegare, svuota il campo e salva.
   Una chiave fornita esplicitamente nell'ambiente o tramite file ha precedenza;
   se non è valida, l'operazione rifiuta senza usarne un'altra.
5. La finestra coordinatrice segue [AGENTS.md](AGENTS.md) all'avvio, alla ripresa
   e dopo ogni chiusura. La dashboard non esegue gli invii da sola: una finestra
   ferma, istruzioni mancanti o un rifiuto non equivalgono a un invio completato.

## Dichiarazioni ed esiti

Ogni incarico chiede «Da ricordare?»: decisione, vincolo o lezione durevole, chi
ha deciso, quanto è certa e se contiene dati privati. Ogni finestra risponde alla
chiusura con un artefatto `da-ricordare/1`, anche se la risposta è no. Non inventare
risposte altrui o ricavarle dal testo libero. L'[esempio sintetico](examples/da-ricordare.json)
richiede i riferimenti al proprio incarico e alle prove realmente registrate.
Le voci private, senza risposta sulla riservatezza o senza prove valide non sono
inviate; quelle contestate richiedono una decisione indipendente del coordinatore.

Parte solo la frase dichiarata non privata, tipo, area, certezza e riferimenti
alla prova. Non sono allegati file, chiavi o registri locali degli invii. Il filtro
si basa sulle dichiarazioni: non riconosce tutti i segreti. L'autore deve togliere
dati privati e percorsi dalla frase. Le anteprime locali restano dati privati.

I comandi usano Python 3 e la stessa HOME della dashboard. `<registry>` è la
cartella del registro Squadra, non quella del codice; proteggila con virgolette.

```sh
python3 -I -B "$HOME/.squadra/plugins/memoranda/bridge/memoranda_plugin.py" collect --registry <registry>
python3 -I -B "$HOME/.squadra/plugins/memoranda/bridge/memoranda_plugin.py" send --auto --registry <registry>
python3 -I -B "$HOME/.squadra/plugins/memoranda/bridge/memoranda_plugin.py" status --registry <registry>
```

Leggi `status` anche se l'invio si ferma per esito incerto o approvazione automatica
dell'area; registra ogni codice d'uscita. Un rifiuto della raccolta ferma la sequenza,
eccetto E-OUTPUT quando la stessa anteprima è già salvata: conservala e continua,
senza cancellare il registro degli invii. L'invio automatico lascia intatte le
richieste manuali. Puoi annullare la richiesta e sceglierne un'altra; annullarla
non ferma un gruppo già preso in carico né disattiva gli invii automatici.
Il gruppo impiega circa 2,4 secondi per voce, oltre ai tempi di rete.

Per la selezione manuale usa `send` senza `--auto`. Una richiesta di un altro
registro non viene consumata, e un vecchio invio non cancella una nuova scelta.
Uscita 0: completato, anche senza bozze idonee; 5: nessuna richiesta manuale;
3: rifiuto o invio fermato; 4: errore interno; 130: interrotto. Un esito incerto
non viene ripetuto automaticamente. `resolve`, `--resend` e `--accept-scope-policy`
richiedono istruzione esplicita del proprietario; una ripetizione autorizzata può
duplicare una proposta la cui risposta è andata persa. Non usare un secondo canale
per proporre le stesse voci. Il plugin propone soltanto, senza garanzia exactly-once.

## Aggiornamento e migrazione

Codice, manifest e istruzioni dello Store non sono riscritti dalla raccolta.
Anteprime e registri degli invii stanno in `data/` dentro la cartella installata;
`panels/` contiene proiezioni rigenerabili, `settings.json` e `secrets/key` le
impostazioni e la chiave. I file sono privati; collegamenti simbolici e file non
sicuri sono rifiutati. Mantieni lo stesso eventuale `--out-root` per tutti i comandi.
Non azzerare un registro per aggirare un rifiuto. La cartella deve appartenerti e
avere permessi 0700; registro degli invii e lock devono essere file regolari tuoi,
senza accesso per gruppo o altri. I percorsi non sicuri sono rifiutati prima dell’uso.
La migrazione accetta i collegamenti di sistema di root soltanto sopra la parte
del percorso controllata dall'utente; al suo interno li rifiuta, indicando
il componente collegato nel messaggio di errore.

Ferma produttore e consumatore prima di aggiornare. Reinstallare un bundle gestito
conserva dati, impostazioni e segreti. File gestiti modificati e cartelle preesistenti
non gestite sono rifiutati: non alterare la ricevuta dello Store.

Per passare dal precedente plugin autonomo, solo su istruzione del proprietario:

1. Ferma la routine e tutti gli invii. Conserva una copia privata della cartella
   del vecchio plugin e del suo output separato, con tutti i registri degli invii.
2. Sposta la cartella non gestita, installa dal Store e non riattivare la routine:
   partire con un registro vuoto potrebbe ripetere gli invii.
3. Trasferisci tramite il programma locale, senza rete:

```sh
python3 -I -B "$HOME/.squadra/plugins/memoranda/bridge/migrate.py" --from-plugin <vecchio-plugin-salvato> --from-data <vecchio-output>
```

Il programma conserva esattamente impostazioni, chiave e registri validati; rifiuta
registri mancanti, file non sicuri e destinazioni in conflitto. Non importa vecchio
codice o pannelli. Un marcatore durevole blocca la routine durante un trasferimento
parziale: riprova con gli stessi dati, senza cancellare il marcatore. Identità,
vincoli, proposte, decisioni, cursori, regole accettate ed esiti incerti restano.
Dopo il successo raccogli e leggi gli esiti, verifica la storia, poi riprendi gli
invii automatici. Non eseguire entrambe le versioni. Un ritorno alla versione
precedente deve usare il registro degli invii più recente e processi fermi.

È supportato soltanto il lettore incluso nel bundle. Prima del normale import,
il suo SHA-256 è verificato contro il pin: codice modificato viene rifiutato senza
caricarlo. Non sono accettati lettori esterni. Un nuovo registro richiede un
aggiornamento verificato del bundle e del pin.

E-LEDGER può significare consegna incerta anche se è fallito il salvataggio locale.
Conserva il registro, esegui status quando lo spazio privato è utilizzabile e
controlla la coda di revisione memoranda. Un intento rimasto sending diventa incerto
e non viene reinviato automaticamente. Segui i passi di recupero/resolve sopra
soltanto su istruzione del proprietario.
