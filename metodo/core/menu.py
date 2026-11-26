"""Menu laterale dell'applicazione: un'unica definizione, filtrata per i permessi dell'utente.

Ogni voce indica il nome dell'URL e le funzioni che la rendono visibile (basta una). La voce attiva
è quella il cui indirizzo è il prefisso più lungo dell'indirizzo della pagina.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from django.urls import NoReverseMatch, reverse

from .permessi import F, ha_permesso


@dataclass(frozen=True)
class Voce:
    etichetta: str
    url: str
    permessi: tuple[str, ...] = ()
    solo_staff: bool = False


@dataclass(frozen=True)
class Sezione:
    titolo: str
    icona: str
    voci: tuple[Voce, ...] = field(default_factory=tuple)


MENU: tuple[Sezione, ...] = (
    Sezione(
        "Produzione",
        "produzione",
        (
            Voce("Elenco ordini", "ordini:lista", (F.ORDINI_VEDERE,)),
            Voce("Ricerca articoli", "ordini:articoli", (F.ORDINI_VEDERE,)),
            Voce("Agenda ordini", "pianificazione:agenda", (F.AGENDA,)),
            Voce("Agenda pulsanti", "pianificazione:agenda_pulsanti", (F.AGENDA,)),
            Voce("Ticket", "ticket:lista", (F.TICKET_VEDERE, F.TICKET_GESTIRE)),
            Voce("Pianificazione", "pianificazione:pagina", (F.PIANIFICAZIONE,)),
            Voce("Tabulato settimanale", "pianificazione:tabulato_settimanale", (F.PIANIFICAZIONE,)),
        ),
    ),
    Sezione(
        "Spedizioni",
        "consegna",
        (
            Voce("Consegna", "consegne:pagina", (F.CONSEGNA,)),
            Voce("DDT", "consegne:ddt", (F.DDT,)),
            Voce("Importazione ordini", "importazione:pagina", (F.IMPORTAZIONE,)),
        ),
    ),
    Sezione(
        "Magazzino",
        "magazzino",
        (
            Voce("Richiesta materiali", "magazzino:richieste", (F.MAGAZZINO_RICHIESTE,)),
            Voce("Inventario", "magazzino:inventario", (F.MAGAZZINO_INVENTARIO,)),
            Voce("Distinte base", "magazzino:distinte", (F.MAGAZZINO_DISTINTE,)),
            Voce("DDT in ingresso", "magazzino:ddt_lista", (F.MAGAZZINO_DDT,)),
            Voce("Importa DDT da file", "magazzino:ddt_importa", (F.MAGAZZINO_DDT,)),
            Voce("Materiali in ingresso", "magazzino:materiali", (F.MAGAZZINO_DDT,)),
            Voce("Componenti", "magazzino:componenti", (F.MAGAZZINO_ANAGRAFICHE,)),
            Voce("Articoli finiti", "magazzino:articoli", (F.MAGAZZINO_ANAGRAFICHE,)),
        ),
    ),
    Sezione(
        "Tracciature",
        "tracciatura",
        (
            # Tracciatura configurabile: controlli qualità, lotti, operazioni, foto e documenti.
            Voce("Nuova tracciatura", "tracciatura:nuova", (F.TRACCIATURA_REGISTRA,)),
            Voce("Tracciature", "tracciatura:elenco", (F.TRACCIATURA_VEDERE,)),
            Voce("Modelli di tracciatura", "tracciatura:modelli", (F.TRACCIATURA_CONFIGURA,)),
        ),
    ),
    Sezione(
        "Timbrature",
        "timbrature",
        (
            Voce("Presenze", "timbrature:presenze", (F.TIMBRATURE_VEDERE,)),
            Voce("Timbrature e correzioni", "timbrature:timbrature", (F.TIMBRATURE_VEDERE,)),
            Voce("Costi per reparto", "timbrature:costi", (F.TIMBRATURE_COSTI,)),
            Voce("Lavoratori", "timbrature:lavoratori", (F.TIMBRATURE_VEDERE,)),
            Voce("Postazioni", "timbrature:postazioni", (F.TIMBRATURE_GESTIRE,)),
        ),
    ),
    Sezione(
        "Report",
        "report",
        (
            Voce("Dashboard", "report:dashboard", (F.REPORT_DASHBOARD,)),
            Voce("Produttività", "report:produttivita", (F.REPORT_PRODUTTIVITA,)),
            Voce("Materiale per reparto", "report:materiale", (F.REPORT_MATERIALE,)),
            Voce("In scadenza e consegnato per settimana", "report:settimane", (F.REPORT_MATERIALE,)),
            Voce("Efficienza per reparto", "report:efficienza_reparti", (F.REPORT_PRODUTTIVITA,)),
            Voce("Puntualità e arretrato", "report:puntualita", (F.REPORT_PRODUTTIVITA,)),
            Voce("Ore per lavoratore", "report:ore_lavoratori", (F.TIMBRATURE_VEDERE,)),
            Voce("Controllo prezzi", "report:prezzi", (F.REPORT_PREZZI,)),
        ),
    ),
    Sezione(
        "Utilità",
        "utilita",
        (
            Voce(
                "Strumenti",
                "utilita:indice",
                (
                    F.UTILITA_ERRORI_IMPORTAZIONE,
                    F.UTILITA_RISTAMPA_ETICHETTE,
                    F.UTILITA_ANNUNCI,
                    F.UTILITA_SCORPORAZIONE,
                    F.UTILITA_CAMBIA_ORDINE,
                ),
            ),
        ),
    ),
    Sezione(
        "Gestionale",
        "gestionale",
        (
            Voce("Riepilogo", "gestionale:indice", (F.GESTIONALE_VEDERE,)),
            Voce("DDT da emettere", "gestionale:ddt_emetti", (F.GESTIONALE_DDT,)),
            Voce("Bolle (DDT)", "gestionale:ddt_lista", (F.GESTIONALE_VEDERE,)),
            Voce("Da fatturare", "gestionale:da_fatturare", (F.GESTIONALE_FATTURE,)),
            Voce("Fatture e note di credito", "gestionale:fatture", (F.GESTIONALE_VEDERE,)),
            Voce("Dati fiscali e impostazioni", "gestionale:impostazioni", (F.GESTIONALE_IMPOSTAZIONI,)),
            Voce("Numerazioni", "gestionale:numerazioni", (F.GESTIONALE_IMPOSTAZIONI,)),
        ),
    ),
    Sezione(
        "Amministrazione",
        "impostazioni",
        (
            Voce("Configurazione", "configurazione:pagina", (F.CONFIGURAZIONE,)),
            Voce("Numerazione DDT", "configurazione:numerazione_ddt", (F.CONFIGURAZIONE,)),
            Voce("Aggiornamenti del client", "client:versioni", (F.CONFIGURAZIONE,)),
            Voce("Utenti e permessi", "utenti:lista", (F.UTENTI_VEDERE, F.UTENTI_GESTIRE, F.UTENTI_PERMESSI)),
            Voce("Reparti", "reparti:lista", (F.REPARTI,)),
            Voce("Admin tecnico (Django)", "admin:index", solo_staff=True),
        ),
    ),
)


def menu_utente(user: Any, percorso: str, postazione: Any = None) -> dict[str, Any]:
    """Sezioni e voci visibili all'utente, con la voce attiva segnata.

    ``postazione``: la postazione di timbratura a cui è associato questo PC; se c'è, in Timbrature compare
    «Timbra» per chiunque usi il PC (la timbratura non richiede permessi: identifica il codice personale)."""
    if not getattr(user, "is_authenticated", False):
        return {"sezioni": [], "attiva": None}
    sezioni: list[dict[str, Any]] = []
    attiva: dict[str, Any] | None = None
    for s in MENU:
        voci = []
        for v in s.voci:
            # L'admin di Django serve solo per interventi tecnici: lo vede solo il superuser.
            visibile = user.is_superuser if v.solo_staff else ha_permesso(user, *v.permessi)
            if not visibile:
                continue
            try:
                url = reverse(v.url)
            except NoReverseMatch:
                continue
            voce = {"etichetta": v.etichetta, "url": url, "attiva": False, "sezione": s.titolo}
            # La radice di un'app ("/ordini/") vale anche per le sue sottopagine, ma perde contro
            # una voce più specifica ("/ordini/agenda/").
            if percorso.startswith(url) and (attiva is None or len(url) > len(attiva["url"])):
                attiva = voce
            voci.append(voce)
        if s.titolo == "Timbrature" and postazione is not None:
            url = reverse("timbrature:timbra_qui")
            voci.insert(
                0, {"etichetta": f"Timbra · {postazione.nome}", "url": url, "attiva": False, "sezione": s.titolo}
            )
        if voci:
            sezioni.append({"titolo": s.titolo, "icona": s.icona, "voci": voci})
    if attiva is not None:
        attiva["attiva"] = True
    for s in sezioni:
        s["aperta"] = any(v["attiva"] for v in s["voci"])
    return {"sezioni": sezioni, "attiva": attiva}
