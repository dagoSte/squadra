# Creare un plugin per Squadra

Un plugin porta nella dashboard i dati di un servizio: puoi vedere i risultati,
seguire il lavoro collegato e richiedere un'attività. La dashboard salva la scelta;
un agente autorizzato usa gli strumenti del servizio e riferisce l'esito.

La [guida tecnica in inglese](../references/plugin-authoring.md) è il riferimento
per preparare il pacchetto. Questa pagina aiuta a trovare le istruzioni:

- [Come funziona un plugin](../references/plugin-authoring.md#what-a-plugin-is).
- [File e organizzazione del pacchetto](../references/plugin-authoring.md#files-and-names).
- [Un esempio da seguire](../references/plugin-authoring.md#a-minimal-plugin).
- [Impostazioni e segreti](../references/plugin-authoring.md#settings-and-secrets).
- [Richieste e risultati](../references/plugin-authoring.md#the-optional-action).
- [Istruzioni per gli agenti](../references/plugin-authoring.md#agents-md).
- [Controlli di sicurezza](../references/plugin-authoring.md#safety-checklist).
- [Verifiche locali](../references/plugin-authoring.md#testing-locally).

Per proporlo allo store puoi aprire una pull request, cioè una proposta di modifica
al repository, oppure scrivere all'indirizzo di contatto del manutentore.
Segui [come inviare il plugin](../references/plugin-authoring.md#submitting-your-plugin)
e le [indicazioni per pubblicarlo](../references/plugin-authoring.md#publishing).
Il manutentore lo analizza prima dell'eventuale inclusione.

Per un caso completo, leggi il [plugin Playwright](../plugins/playwright/README.it.md):
collega una richiesta di controllo di una pagina al risultato osservato nel browser.
