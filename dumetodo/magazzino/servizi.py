"""Logica di business del magazzino. Le viste chiamano SOLO queste funzioni.

Come negli altri moduli: ogni modifica gira in una transazione, blocca le righe che tocca dove
sono possibili corse, verifica i ruoli lato server e solleva ``OperazioneNonConsentita`` con un
messaggio leggibile. In Metodo le azioni "da amministratore" (ricevi, elimina, email, modifica
delle distinte) erano solo nascoste nei template: qui sono controllate davvero.
"""

from __future__ import annotations

import datetime as dt
import io
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, Exists, F, Max, OuterRef, Q, QuerySet, Subquery, Sum
from django.template.loader import render_to_string
from django.utils import timezone

from dumetodo.anagrafica import servizi as anagrafica
from dumetodo.anagrafica.models import Articolo, Tabulato, UnitaMisura
from dumetodo.comunicazioni import email
from dumetodo.configurazione import servizi as config
from dumetodo.core.models import Reparto
from dumetodo.core.permessi import F as Fn
from dumetodo.core.permessi import ha_permesso
from dumetodo.ordini.servizi import OperazioneNonConsentita

from . import formati_ddt
from .models import (
    Componente,
    ComponenteDistinta,
    ConteggioInventario,
    DDTIngresso,
    DistintaBase,
    MaterialeIngresso,
    OrigineDDT,
    RichiestaMateriale,
    TipoEmailRichiesta,
    codice_completo,
    normalizza_codice,
)

__all__ = ["OperazioneNonConsentita"]


# ----------------------------------------------------------------------------------------- aiuti
def _richiedi(utente: Any, codice: str, messaggio: str = "Non hai i permessi per questa operazione.") -> None:
    if not ha_permesso(utente, codice):
        raise OperazioneNonConsentita(messaggio)


def quantita(valore: Any, etichetta: str = "Quantità", *, decimali: int = 3, zero: bool = False) -> Decimal:
    """Quantità positiva (o non negativa con ``zero=True``), accetta anche la virgola decimale."""
    d = valore if isinstance(valore, Decimal) else formati_ddt.numero_decimale(None if valore is None else str(valore))
    if d is None:
        raise OperazioneNonConsentita(f"{etichetta}: numero non valido.")
    if d < 0 or (d == 0 and not zero):
        raise OperazioneNonConsentita(f"{etichetta}: deve essere {'zero o ' if zero else ''}maggiore di zero.")
    passo = Decimal(1).scaleb(-decimali)
    if d != d.quantize(passo):
        raise OperazioneNonConsentita(f"{etichetta}: al massimo {decimali} decimali.")
    if d >= Decimal(10) ** (14 - decimali):
        raise OperazioneNonConsentita(f"{etichetta}: valore troppo grande.")
    return d.quantize(passo)


def _prezzo(valore: Any) -> Decimal | None:
    if valore is None or str(valore).strip() == "":
        return None
    return quantita(valore, "Prezzo", decimali=4, zero=True)


def _codice(codice: str | None, etichetta: str = "Codice") -> str:
    c = normalizza_codice(codice)
    if not c:
        raise OperazioneNonConsentita(f"{etichetta}: obbligatorio.")
    if len(c) > 50:
        raise OperazioneNonConsentita(f"{etichetta}: massimo 50 caratteri.")
    return c


def _variante(variante: str | None) -> str:
    v = normalizza_codice(variante)
    if len(v) > 20:
        raise OperazioneNonConsentita("Variante: massimo 20 caratteri.")
    return v


def _unita(um: str | None) -> str:
    um = (um or UnitaMisura.PEZZI).strip().upper()
    if um not in UnitaMisura.values:
        raise OperazioneNonConsentita(f"Unità di misura {um!r} non valida.")
    return um


def um_da_documento(um: str | None) -> str:
    """Unità di misura di un documento esterno (NR, PZ, MT…) -> unità dell'anagrafica componenti."""
    u = (um or "").strip().upper().rstrip(".")
    return UnitaMisura.METRI if u in {"M", "MT", "MTR", "METRI"} else UnitaMisura.PEZZI


def assicura_componente(codice: str, variante: str = "", **valori: Any) -> Componente:
    """Componente esistente o creato con ``valori``. Sicuro in concorrenza grazie al vincolo unico.

    In Metodo ``get_or_create`` falliva con MultipleObjectsReturned per i doppioni ``(X, NULL)``
    e, nelle importazioni dei DDT, cercava senza variante. Qui la variante è sempre esplicita.
    """
    codice, variante = _codice(codice), _variante(variante)
    try:
        with transaction.atomic():
            componente, creato = Componente.objects.get_or_create(codice=codice, variante=variante, defaults=valori)
    except IntegrityError:
        componente, creato = Componente.objects.get(codice=codice, variante=variante), False
    if not creato and not componente.descrizione and valori.get("descrizione"):
        componente.descrizione = valori["descrizione"]
        componente.save(update_fields=["descrizione", "aggiornato_il"])
    return componente


def _xlsx(titolo: str, intestazione: Sequence[str], righe: Iterable[Sequence[Any]], formati: dict[int, str]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = titolo[:31]
    ws.append(list(intestazione))
    for cella in ws[1]:
        cella.font = Font(bold=True, color="FFFFFF")
        cella.fill = PatternFill("solid", fgColor="4472C4")
    for riga in righe:
        ws.append([timezone.localtime(v).replace(tzinfo=None) if isinstance(v, dt.datetime) else v for v in riga])
        for colonna, formato in formati.items():
            ws.cell(ws.max_row, colonna).number_format = formato
    for i, _ in enumerate(intestazione, start=1):
        ws.column_dimensions[ws.cell(1, i).column_letter].width = 20
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


FORMATO_QTA = "#,##0.###"  # in Metodo "#,##0" nascondeva i decimali
FORMATO_DATA_ORA = "DD/MM/YYYY HH:MM"
FORMATO_DATA = "DD/MM/YYYY"


# ----------------------------------------------------------------------------------------- componenti
CAMPI_COMPONENTE = ("descrizione", "um", "prezzo_medio", "fornitore", "note", "attivo")


def cerca_componenti(testo: str = "", solo_attivi: bool = False) -> QuerySet[Componente]:
    qs = Componente.objects.all()
    if solo_attivi:
        qs = qs.filter(attivo=True)
    for parola in (testo or "").split()[:5]:
        qs = qs.filter(
            Q(codice__icontains=parola)
            | Q(variante__icontains=parola)
            | Q(descrizione__icontains=parola)
            | Q(fornitore__icontains=parola)
        )
    return qs.order_by("codice", "variante")


def _valori_componente(dati: dict[str, Any]) -> dict[str, Any]:
    return {
        "descrizione": (dati.get("descrizione") or "").strip(),
        "um": _unita(dati.get("um")),
        "prezzo_medio": _prezzo(dati.get("prezzo_medio")),
        "fornitore": (dati.get("fornitore") or "").strip()[:100],
        "note": (dati.get("note") or "").strip(),
        "attivo": bool(dati.get("attivo", True)),
    }


@transaction.atomic
def crea_componente(utente: Any, codice: str, variante: str = "", **dati: Any) -> Componente:
    """Nuovo componente. Se esiste già NON viene sovrascritto (Metodo cancellava i campi lasciati vuoti)."""
    _richiedi(utente, Fn.MAGAZZINO_ANAGRAFICHE)
    codice, variante = _codice(codice), _variante(variante)
    valori = _valori_componente(dati)
    if Componente.objects.filter(codice=codice, variante=variante).exists():
        raise OperazioneNonConsentita(f"Il componente {codice_completo(codice, variante)} esiste già: modificalo.")
    try:
        with transaction.atomic():
            return Componente.objects.create(codice=codice, variante=variante, **valori)
    except IntegrityError as exc:
        raise OperazioneNonConsentita(f"Il componente {codice_completo(codice, variante)} esiste già.") from exc


@transaction.atomic
def modifica_componente(utente: Any, pk: int, **dati: Any) -> Componente:
    """Modifica descrizione, UM, prezzo, fornitore, note e stato attivo. Codice e variante non cambiano:
    sono l'identità del componente, a cui puntano inventario, distinte e DDT."""
    _richiedi(utente, Fn.MAGAZZINO_ANAGRAFICHE)
    componente = Componente.objects.select_for_update().get(pk=pk)
    for campo, valore in _valori_componente(dati).items():
        setattr(componente, campo, valore)
    componente.save()
    return componente


# ----------------------------------------------------------------------------------------- articoli finiti
@transaction.atomic
def modifica_articolo(
    utente: Any, pk: int, *, descrizione: str, reparto_id: int | None, um: str, tabulato: int
) -> Articolo:
    _richiedi(utente, Fn.MAGAZZINO_ANAGRAFICHE)
    articolo = Articolo.objects.select_for_update().get(pk=pk)
    reparto = None
    if reparto_id:
        reparto = Reparto.objects.filter(pk=reparto_id).first()
        if reparto is None:
            raise OperazioneNonConsentita("Reparto inesistente.")
    if tabulato not in Tabulato.values:
        raise OperazioneNonConsentita("Tabulato non valido.")
    articolo.descrizione = (descrizione or "").strip()
    articolo.reparto = reparto
    articolo.um = _unita(um)
    articolo.tabulato = tabulato
    articolo.save()
    return articolo


@transaction.atomic
def imposta_nuovo(utente: Any, pk: int, nuovo: bool) -> Articolo:
    """Valore esplicito invece di un "inverti": un doppio invio non annulla la modifica (in Metodo F5 la ripeteva)."""
    _richiedi(utente, Fn.MAGAZZINO_ANAGRAFICHE)
    articolo = Articolo.objects.select_for_update().get(pk=pk)
    articolo.nuovo = nuovo
    articolo.save()
    return articolo


# ----------------------------------------------------------------------------------------- inventario
@transaction.atomic
def registra_conteggio(utente: Any, codice: str, variante: str, qta: Any) -> ConteggioInventario:
    _richiedi(utente, Fn.MAGAZZINO_INVENTARIO)
    q = quantita(qta, zero=True)
    oggi = timezone.localdate()
    componente = assicura_componente(codice, variante, descrizione=f"Aggiunto da inventario il {oggi:%d/%m/%Y}")
    return ConteggioInventario.objects.create(componente=componente, qta=q, utente=utente)


def ultimi_conteggi(testo: str = "") -> QuerySet[ConteggioInventario]:
    """L'ultimo conteggio di ogni componente (la giacenza rilevata)."""
    qs = ConteggioInventario.objects.select_related("componente", "utente")
    if testo:
        qs = qs.filter(componente__in=cerca_componenti(testo))
    # Portabile (PostgreSQL e SQLite): per ogni componente il conteggio più recente.
    piu_recente = ConteggioInventario.objects.filter(componente_id=OuterRef("componente_id")).order_by("-data", "-id")
    ultimi = qs.annotate(ultimo=Subquery(piu_recente.values("pk")[:1])).filter(pk=F("ultimo")).values("pk")
    return (
        ConteggioInventario.objects.filter(pk__in=ultimi)
        .select_related("componente", "utente")
        .order_by("componente__codice", "componente__variante")
    )


def storico_conteggi(testo: str = "", anno: int | None = None) -> QuerySet[ConteggioInventario]:
    qs = ConteggioInventario.objects.select_related("componente", "utente").order_by("-data", "-id")
    if testo:
        qs = qs.filter(componente__in=cerca_componenti(testo))
    if anno:
        qs = qs.filter(data__year=anno)
    return qs


def anni_inventario() -> list[int]:
    return sorted({d.year for d in ConteggioInventario.objects.dates("data", "year")}, reverse=True)


def xlsx_inventario(tipo: str, anno: int | None = None) -> tuple[str, bytes]:
    """``ultimi``: giacenza = ultimo conteggio; ``somma``: somma di tutti i conteggi (come l'export 1
    di Metodo, ma per componente); ``tutti``: ogni conteggio, eventualmente di un solo anno."""
    suffisso = f"_{anno}" if anno else ""
    intestazione = ["Codice", "Variante", "Descrizione", "Quantità"]
    if tipo == "somma":
        qs = ConteggioInventario.objects.all()
        if anno:
            qs = qs.filter(data__year=anno)
        righe = (
            qs.values("componente__codice", "componente__variante", "componente__descrizione")
            .annotate(tot=Sum("qta"))
            .order_by("componente__codice", "componente__variante")
        )
        dati = [
            (r["componente__codice"], r["componente__variante"], r["componente__descrizione"], r["tot"]) for r in righe
        ]
        return f"Inventario_somma{suffisso}.xlsx", _xlsx("Somma", intestazione, dati, {4: FORMATO_QTA})
    if tipo == "ultimi":
        qs = ultimi_conteggi()
        nome, foglio = "Inventario_giacenze.xlsx", "Giacenze"
    else:
        qs = storico_conteggi(anno=anno)
        nome, foglio = f"Inventario{suffisso}.xlsx", "Conteggi"
    dati = (
        (c.componente.codice, c.componente.variante, c.componente.descrizione, c.qta, c.data, str(c.utente or ""))
        for c in qs.iterator(chunk_size=2000)
    )
    return nome, _xlsx(
        foglio,
        [*intestazione, "Data conteggio", "Utente"],
        dati,
        {4: FORMATO_QTA, 5: FORMATO_DATA_ORA},
    )


# ----------------------------------------------------------------------------------------- richieste materiali
TESTO_TIPO = {
    TipoEmailRichiesta.MANCANTE: "Materiale in esaurimento.",
    TipoEmailRichiesta.URGENTE: "Materiale Urgente per commessa.",
    TipoEmailRichiesta.SOLLECITO: "Sollecito Materiale.",
}
FLAG_TIPO = {
    TipoEmailRichiesta.MANCANTE: "mail_mancante",
    TipoEmailRichiesta.URGENTE: "mail_urgente",
    TipoEmailRichiesta.SOLLECITO: "mail_sollecito",
}


def reparti_richieste() -> list[Reparto]:
    """Reparti proposti in Richiesta materiali: quelli configurati, più quelli con richieste aperte."""
    codici = [c.strip() for c in (config.leggi("magazzino.reparti_richieste") or "").split(",") if c.strip()]
    reparti = Reparto.objects.filter(Q(codice__in=codici) | Q(richieste_materiali__ricevuta=False)).distinct()
    ordine = {c: i for i, c in enumerate(codici)}
    return sorted(reparti, key=lambda r: (ordine.get(r.codice, len(ordine)), r.nome))


def richieste_aperte(reparto: Reparto) -> QuerySet[RichiestaMateriale]:
    in_anagrafica = Componente.objects.filter(codice=OuterRef("codice"), variante=OuterRef("variante"))
    return (
        RichiestaMateriale.objects.filter(reparto=reparto, ricevuta=False)
        .annotate(in_anagrafica=Exists(in_anagrafica))
        .select_related("ultima_email", "richiesta_da")
        .order_by("-data_richiesta", "-id")
    )


@transaction.atomic
def richiedi_materiale(
    utente: Any, reparto: Reparto, codice: str, variante: str = "", urgente: bool = False
) -> tuple[RichiestaMateriale, bool]:
    """Nuova richiesta o ripetizione di una già aperta (contatore +1). Ritorna (richiesta, creata).

    Il vincolo ``richiesta_aperta_unica_per_reparto`` impedisce i doppioni anche con due richieste
    contemporanee: chi perde la corsa ritrova la richiesta dell'altro e la incrementa.
    """
    _richiedi(utente, Fn.MAGAZZINO_RICHIESTE)
    codice, variante = _codice(codice), _variante(variante)
    filtro = {"codice": codice, "variante": variante, "reparto": reparto, "ricevuta": False}
    esistente = RichiestaMateriale.objects.select_for_update().filter(**filtro).first()
    if esistente is None:
        try:
            with transaction.atomic():
                return (
                    RichiestaMateriale.objects.create(
                        codice=codice, variante=variante, reparto=reparto, urgente=urgente, richiesta_da=utente
                    ),
                    True,
                )
        except IntegrityError:
            esistente = RichiestaMateriale.objects.select_for_update().get(**filtro)
    esistente.numero_richieste = F("numero_richieste") + 1  # in Metodo restava fermo se era NULL/0
    esistente.data_richiesta = timezone.now()
    esistente.urgente = esistente.urgente or urgente
    esistente.richiesta_da = utente
    esistente.save()
    esistente.refresh_from_db()
    return esistente, False


def _richiesta_aperta(pk: int) -> RichiestaMateriale:
    r = RichiestaMateriale.objects.select_for_update().filter(pk=pk).first()
    if r is None:
        raise OperazioneNonConsentita("Richiesta non trovata: forse è già stata eliminata.")
    return r


@transaction.atomic
def ricevi_richiesta(utente: Any, pk: int) -> RichiestaMateriale:
    _richiedi(utente, Fn.MAGAZZINO_RICHIESTE_GESTIONE)
    r = _richiesta_aperta(pk)
    if r.ricevuta:
        raise OperazioneNonConsentita(f"Il materiale {r.codice_completo} risulta già ricevuto.")
    r.ricevuta, r.ricevuta_il, r.ricevuta_da = True, timezone.now(), utente  # Metodo non salvava la data
    r.save()
    return r


@transaction.atomic
def elimina_richiesta(utente: Any, pk: int) -> None:
    _richiedi(utente, Fn.MAGAZZINO_RICHIESTE_GESTIONE)
    _richiesta_aperta(pk).delete()


@transaction.atomic
def imposta_urgenza(utente: Any, pk: int, urgente: bool) -> RichiestaMateriale:
    """In Metodo l'urgenza, una volta messa, non si poteva più togliere."""
    _richiedi(utente, Fn.MAGAZZINO_RICHIESTE_GESTIONE)
    r = _richiesta_aperta(pk)
    r.urgente = urgente
    r.save(update_fields=["urgente"])
    return r


@dataclass
class EmailRichieste:
    tipo: TipoEmailRichiesta
    richieste: list[RichiestaMateriale]
    oggetto: str
    corpo_testo: str
    corpo_html: str
    mittente: str
    destinatari: list[str]
    rispondi_a: list[str]


def _seleziona_richieste(
    reparto: Reparto, ids: Sequence[int], tutte_da_comunicare: bool, blocca: bool
) -> list[RichiestaMateriale]:
    qs = RichiestaMateriale.objects.filter(reparto=reparto, ricevuta=False)
    if blocca:
        qs = qs.select_for_update()
    if tutte_da_comunicare:
        richieste = list(qs.filter(mail_mancante=False, mail_urgente=False, mail_sollecito=False))
    else:
        ids = sorted(set(ids))
        richieste = list(qs.filter(pk__in=ids)) if ids else []
        if len(richieste) != len(ids):
            raise OperazioneNonConsentita(
                "Alcune richieste selezionate non sono più aperte o non sono di questo reparto: ricarica la pagina."
            )
    if not richieste:
        raise OperazioneNonConsentita("Nessuna richiesta selezionata.")
    return sorted(richieste, key=lambda r: (r.codice, r.variante))


def componi_email(
    reparto: Reparto,
    tipo: str,
    ids: Sequence[int] = (),
    tutte_da_comunicare: bool = False,
    *,
    _blocca: bool = False,
) -> EmailRichieste:
    """Email per il magazzino del Cliente (anteprima e invio usano la stessa funzione).

    Il corpo HTML è generato da un template con l'autoescape attivo: in Metodo i codici inseriti
    dagli utenti finivano nell'HTML dell'email e nell'anteprima senza escape (XSS).
    """
    if tipo not in TipoEmailRichiesta.values:
        raise OperazioneNonConsentita("Tipo di comunicazione non valido.")
    t = TipoEmailRichiesta(tipo)
    richieste = _seleziona_richieste(reparto, ids, tutte_da_comunicare, _blocca)
    contesto = {
        "tipo": t,
        "reparto": reparto,
        "righe": [(r.codice_completo, TESTO_TIPO[t], r.urgente) for r in richieste],
    }
    return EmailRichieste(
        tipo=t,
        richieste=richieste,
        oggetto=f"Richiesta Materiale - {t.label}",
        corpo_testo=render_to_string("magazzino/email_richiesta.txt", contesto),
        corpo_html=render_to_string("magazzino/email_richiesta.html", contesto),
        mittente=config.leggi("email.mittente") or settings.DEFAULT_FROM_EMAIL,
        destinatari=list(config.leggi("email.magazzino_cliente") or []),
        rispondi_a=list(config.leggi("email.magazzino_fornitore") or []),
    )


@transaction.atomic
def invia_email_richieste(
    utente: Any, reparto: Reparto, tipo: str, ids: Sequence[int] = (), tutte_da_comunicare: bool = False
) -> EmailRichieste:
    """Accoda l'email e segna le richieste come comunicate, nella stessa transazione.

    In Metodo i flag venivano salvati prima dell'invio SMTP (un errore lasciava richieste segnate
    come comunicate senza email) e la risposta era "Mail inviata" anche quando non partiva nulla.
    """
    _richiedi(utente, Fn.MAGAZZINO_RICHIESTE_GESTIONE)
    messaggio = componi_email(reparto, tipo, ids, tutte_da_comunicare, _blocca=True)
    if not messaggio.destinatari:
        raise OperazioneNonConsentita(
            "Nessun destinatario configurato per le richieste materiali (Configurazione → Magazzino cliente)."
        )
    inviata = email.accoda(
        oggetto=messaggio.oggetto,
        corpo_testo=messaggio.corpo_testo,
        corpo_html=messaggio.corpo_html,
        mittente=messaggio.mittente,
        destinatari=messaggio.destinatari,
        rispondi_a=messaggio.rispondi_a,
        riferimento=f"richieste:{reparto.codice}:{messaggio.tipo.value}",
    )
    flag = FLAG_TIPO[messaggio.tipo]
    RichiestaMateriale.objects.filter(pk__in=[r.pk for r in messaggio.richieste]).update(
        **{flag: True}, ultima_email=inviata
    )
    return messaggio


def xlsx_richieste(reparto: Reparto) -> tuple[str, bytes]:
    def si(v: bool) -> str:
        return "Sì" if v else "No"

    righe = (
        (
            r.codice,
            r.variante,
            si(r.in_anagrafica),
            r.data_richiesta,
            r.numero_richieste,
            si(r.urgente),
            si(r.mail_mancante),
            si(r.mail_urgente),
            si(r.mail_sollecito),
        )
        for r in richieste_aperte(reparto)
    )
    intestazione = [
        "Codice",
        "Variante",
        "In Anagrafica",
        "Data Richiesta",
        "Numero Richieste",
        "Materiale Urgente",
        "Mail Mancante",
        "Mail Urgente",
        "Mail Sollecito",
    ]
    return f"RichiesteMateriali_{reparto.codice}.xlsx", _xlsx("Richieste", intestazione, righe, {4: FORMATO_DATA})


# ----------------------------------------------------------------------------------------- distinte base
def distinte_attive(testo: str = "") -> QuerySet[DistintaBase]:
    qs = DistintaBase.objects.attive().select_related("articolo", "modificata_da", "creata_da")
    for parola in (testo or "").split()[:5]:
        qs = qs.filter(
            Q(articolo__codice__icontains=parola)
            | Q(variante__icontains=parola)
            | Q(descrizione__icontains=parola)
            | Q(componenti__componente__codice__icontains=parola)
        ).distinct()
    return qs.annotate(n_componenti=Count("componenti", distinct=True)).order_by("articolo__codice", "variante")


def _articolo(codice: str) -> Articolo:
    codice = (codice or "").strip()
    if not codice:
        raise OperazioneNonConsentita("Codice articolo: obbligatorio.")
    if len(codice) > 50:
        raise OperazioneNonConsentita("Codice articolo: massimo 50 caratteri.")
    articolo = Articolo.objects.filter(codice__iexact=codice).order_by("pk").first()
    if articolo is None:
        # Come in Metodo il codice padre è libero; qui finisce comunque nell'anagrafica articoli.
        anagrafica.registra_articoli([codice.upper()])
        articolo = Articolo.objects.get(codice=codice.upper())
    return articolo


def _blocca_distinta(pk: int) -> DistintaBase:
    d = DistintaBase.objects.select_for_update().filter(pk=pk, eliminata_il__isnull=True).first()
    if d is None:
        raise OperazioneNonConsentita("Distinta non trovata o eliminata.")
    return d


def _salva_distinta(d: DistintaBase) -> None:
    try:
        with transaction.atomic():
            d.save()
    except IntegrityError as exc:  # in Metodo: errore 500, e il codice restava bloccato anche dopo l'eliminazione
        raise OperazioneNonConsentita(f"Esiste già una distinta attiva per {d.codice_completo}.") from exc


@transaction.atomic
def crea_distinta(
    utente: Any, codice_articolo: str, variante: str = "", descrizione: str = "", note: str = ""
) -> DistintaBase:
    _richiedi(utente, Fn.MAGAZZINO_DISTINTE_MODIFICA)
    d = DistintaBase(
        articolo=_articolo(codice_articolo),
        variante=_variante(variante),
        descrizione=(descrizione or "").strip(),
        note=(note or "").strip(),
        creata_da=utente,
        modificata_da=utente,
    )
    _salva_distinta(d)
    return d


@transaction.atomic
def modifica_distinta(
    utente: Any, pk: int, codice_articolo: str, variante: str = "", descrizione: str = "", note: str = ""
) -> DistintaBase:
    _richiedi(utente, Fn.MAGAZZINO_DISTINTE_MODIFICA)
    d = _blocca_distinta(pk)
    d.articolo = _articolo(codice_articolo)
    d.variante = _variante(variante)
    d.descrizione, d.note, d.modificata_da = (descrizione or "").strip(), (note or "").strip(), utente
    _salva_distinta(d)
    return d


@transaction.atomic
def elimina_distinta(utente: Any, pk: int) -> DistintaBase:
    """Eliminazione logica: i componenti restano per lo storico; il codice si può riusare."""
    _richiedi(utente, Fn.MAGAZZINO_DISTINTE_MODIFICA)
    d = _blocca_distinta(pk)
    d.eliminata_il, d.eliminata_da, d.modificata_da = timezone.now(), utente, utente
    d.save()
    return d


@transaction.atomic
def aggiungi_componente_distinta(
    utente: Any, distinta_pk: int, codice: str, variante: str = "", qta: Any = "1", um: str = "", note: str = ""
) -> ComponenteDistinta:
    _richiedi(utente, Fn.MAGAZZINO_DISTINTE_MODIFICA)
    d = _blocca_distinta(distinta_pk)  # il blocco serializza il calcolo della posizione
    q = quantita(qta, decimali=4)
    um = _unita(um)
    componente = assicura_componente(
        codice,
        variante,
        descrizione=f"Aggiunto da distinta base {d.codice_completo} il {timezone.localdate():%d/%m/%Y}",
        um=um,
    )
    if d.componenti.filter(componente=componente).exists():
        raise OperazioneNonConsentita(f"{componente} è già nella distinta: modificane la quantità.")
    posizione = (d.componenti.aggregate(m=Max("posizione"))["m"] or 0) + 1
    riga = ComponenteDistinta.objects.create(
        distinta=d, componente=componente, quantita=q, um=um, posizione=posizione, note=(note or "").strip()
    )
    d.modificata_da = utente
    d.save()
    return riga


def _blocca_riga_distinta(pk: int) -> ComponenteDistinta:
    riga = ComponenteDistinta.objects.select_related("distinta").filter(pk=pk).first()
    if riga is None:
        raise OperazioneNonConsentita("Componente della distinta non trovato.")
    d = _blocca_distinta(riga.distinta_id)
    riga = ComponenteDistinta.objects.select_for_update().get(pk=pk)
    riga.distinta = d
    return riga


@transaction.atomic
def modifica_componente_distinta(utente: Any, pk: int, qta: Any, um: str = "", note: str = "") -> ComponenteDistinta:
    _richiedi(utente, Fn.MAGAZZINO_DISTINTE_MODIFICA)
    riga = _blocca_riga_distinta(pk)
    riga.quantita, riga.um, riga.note = quantita(qta, decimali=4), _unita(um), (note or "").strip()
    riga.save()
    riga.distinta.modificata_da = utente
    riga.distinta.save()
    return riga


@transaction.atomic
def elimina_componente_distinta(utente: Any, pk: int) -> DistintaBase:
    _richiedi(utente, Fn.MAGAZZINO_DISTINTE_MODIFICA)
    riga = _blocca_riga_distinta(pk)
    d = riga.distinta
    riga.delete()
    d.modificata_da = utente
    d.save()
    return d


@transaction.atomic
def sposta_componente_distinta(utente: Any, pk: int, su: bool) -> ComponenteDistinta:
    _richiedi(utente, Fn.MAGAZZINO_DISTINTE_MODIFICA)
    riga = _blocca_riga_distinta(pk)
    righe = list(riga.distinta.componenti.select_for_update().order_by("posizione", "id"))
    for n, r in enumerate(righe, start=1):  # rinumera 1..n (sistema eventuali buchi o doppioni)
        r.posizione = n
    i = next(i for i, r in enumerate(righe) if r.pk == riga.pk)
    j = i - 1 if su else i + 1
    if 0 <= j < len(righe):
        righe[i].posizione, righe[j].posizione = righe[j].posizione, righe[i].posizione
    ComponenteDistinta.objects.bulk_update(righe, ["posizione"])
    return next(r for r in righe if r.pk == riga.pk)


def xlsx_distinte() -> tuple[str, bytes]:
    def righe() -> Iterable[Sequence[Any]]:
        distinte = DistintaBase.objects.attive().select_related("articolo").order_by("articolo__codice", "variante")
        for d in distinte.prefetch_related("componenti__componente"):
            componenti = list(d.componenti.all())
            if not componenti:
                yield (d.articolo.codice, d.variante, d.descrizione, "", "", None, "", "")
            for c in componenti:
                yield (
                    d.articolo.codice,
                    d.variante,
                    d.descrizione,
                    c.componente.codice,
                    c.componente.variante,
                    c.quantita,
                    c.get_um_display(),
                    c.note,
                )

    intestazione = [
        "Codice Padre",
        "Variante",
        "Descrizione",
        "Codice Componente",
        "Variante Componente",
        "Quantità",
        "UM",
        "Note Componente",
    ]
    nome = f"distinte_base_{timezone.localtime():%Y%m%d_%H%M%S}.xlsx"
    return nome, _xlsx("Distinte Base", intestazione, righe(), {6: "#,##0.####"})


# ----------------------------------------------------------------------------------------- DDT in ingresso
def _testo_breve(valore: str | None, massimo: int, etichetta: str, obbligatorio: bool = False) -> str:
    t = (valore or "").strip()
    if obbligatorio and not t:
        raise OperazioneNonConsentita(f"{etichetta}: obbligatorio.")
    if len(t) > massimo:
        raise OperazioneNonConsentita(f"{etichetta}: massimo {massimo} caratteri.")
    return t


@transaction.atomic
def crea_ddt(utente: Any, numero: str, data: dt.date, fornitore: str) -> DDTIngresso:
    _richiedi(utente, Fn.MAGAZZINO_DDT)
    numero = _testo_breve(numero, 50, "Numero DDT", obbligatorio=True)
    fornitore = _testo_breve(fornitore, 100, "Fornitore", obbligatorio=True)
    if data is None:
        raise OperazioneNonConsentita("Data DDT: obbligatoria.")
    try:
        with transaction.atomic():
            return DDTIngresso.objects.create(numero=numero, data=data, fornitore=fornitore, creato_da=utente)
    except IntegrityError as exc:
        raise OperazioneNonConsentita(
            f"Il DDT {numero} del {data:%d/%m/%Y} di {fornitore} è già registrato: aggiungi le righe a quello."
        ) from exc


@transaction.atomic
def aggiungi_materiale(
    utente: Any,
    ddt_pk: int,
    *,
    codice: str,
    variante: str = "",
    descrizione: str = "",
    qta: Any,
    unita_misura: str = "EA",
    prezzo_unitario: Any = None,
    data_ingresso: dt.date | None = None,
    note: str = "",
) -> MaterialeIngresso:
    """Una riga inserita a mano. Tutto in una transazione: in Metodo un errore sulla quantità
    lasciava un DDT vuoto, e "Salva e termina" mostrava un errore dopo aver salvato."""
    _richiedi(utente, Fn.MAGAZZINO_DDT)
    ddt = DDTIngresso.objects.select_for_update().get(pk=ddt_pk)
    q = quantita(qta)
    prezzo = _prezzo(prezzo_unitario)
    descrizione = _testo_breve(descrizione, 200, "Descrizione")
    um = _testo_breve(unita_misura or "EA", 10, "Unità di misura").upper()
    componente = assicura_componente(
        codice,
        variante,
        descrizione=descrizione,
        um=um_da_documento(um),
        prezzo_medio=prezzo,
        fornitore=ddt.fornitore,
    )
    materiale = MaterialeIngresso.objects.create(
        ddt=ddt,
        componente=componente,
        descrizione=descrizione or componente.descrizione[:200],
        quantita=q,
        unita_misura=um,
        prezzo_unitario=prezzo,
        data_ingresso=data_ingresso or timezone.localdate(),
        verificato=True,
        note=(note or "").strip(),
        creato_da=utente,
    )
    chiudi_richieste_per_materiale(materiale, utente)
    return materiale


@transaction.atomic
def imposta_verificato(utente: Any, ddt_pk: int, ids: Sequence[int] | None = None, verificato: bool = True) -> int:
    """Segna come verificate (o da verificare) alcune righe di un DDT, o tutte con ``ids=None``.
    In Metodo le righe importate da CSV restavano "da verificare" per sempre: la pagina non esisteva."""
    _richiedi(utente, Fn.MAGAZZINO_DDT)
    ddt = DDTIngresso.objects.select_for_update().get(pk=ddt_pk)
    qs = ddt.materiali.all()
    if ids is not None:
        qs = qs.filter(pk__in=ids)
    return qs.update(verificato=verificato)


@transaction.atomic
def elimina_materiale(utente: Any, pk: int) -> DDTIngresso:
    _richiedi(utente, Fn.MAGAZZINO_DDT_ELIMINA)
    m = MaterialeIngresso.objects.select_related("ddt").filter(pk=pk).first()
    if m is None:
        raise OperazioneNonConsentita("Riga non trovata.")
    DDTIngresso.objects.select_for_update().get(pk=m.ddt_id)
    m.delete()
    return m.ddt


@dataclass
class EsitoImportazioneDDT:
    ddt_creati: list[DDTIngresso] = field(default_factory=list)
    ddt_aggiornati: list[DDTIngresso] = field(default_factory=list)
    materiali_importati: int = 0
    materiali_gia_presenti: int = 0
    richieste_chiuse: int = 0
    scarti: list[str] = field(default_factory=list)

    @property
    def ddt(self) -> list[DDTIngresso]:
        return self.ddt_creati + self.ddt_aggiornati


def dimensione_massima_file() -> int:
    return int(config.leggi("magazzino.dimensione_massima_file_ddt_mb") or 5) * 1024 * 1024


def leggi_file_ddt(nome: str, contenuto: bytes) -> tuple[str, formati_ddt.Lettura]:
    """Riconosce il formato dall'estensione e legge il file (senza scrivere nulla)."""
    estensione = nome.lower().rsplit(".", 1)[-1] if "." in nome else ""
    if len(contenuto) > dimensione_massima_file():
        raise OperazioneNonConsentita(f"{nome}: file troppo grande.")
    try:
        if estensione == "xml":
            tipi = (config.leggi("magazzino.xml_tipi_documento") or "").split(",")
            return OrigineDDT.XML, formati_ddt.leggi_xml(contenuto, tipi)
        if estensione in ("csv", "txt"):
            return OrigineDDT.FILE, formati_ddt.leggi_csv(contenuto)
    except formati_ddt.ErroreFormato as exc:
        raise OperazioneNonConsentita(f"{nome}: {exc}") from exc
    raise OperazioneNonConsentita(f"{nome}: sono ammessi solo file .csv, .txt o .xml.")


@transaction.atomic
def importa_file_ddt(utente: Any, nome: str, contenuto: bytes) -> EsitoImportazioneDDT:
    """Importa un file di DDT in ingresso. Tutto o niente: in Metodo un errore a metà lasciava
    un'importazione parziale.

    Le righe sono raggruppate per (fornitore, numero, data). Un DDT già presente viene completato:
    si aggiungono solo le righe del documento non ancora importate, riconosciute dalla posizione
    nel documento (Metodo usava il codice, perdendo in silenzio le righe ripetute dello stesso codice).
    """
    _richiedi(utente, Fn.MAGAZZINO_DDT)
    origine, lettura = leggi_file_ddt(nome, contenuto)
    esito = EsitoImportazioneDDT(scarti=list(lettura.scarti))
    gruppi: dict[tuple[str, str, dt.date], list[formati_ddt.RigaDDT]] = defaultdict(list)
    for riga in lettura.righe:
        gruppi[riga.chiave_ddt].append(riga)
    for (fornitore, numero, data), righe in gruppi.items():
        try:
            with transaction.atomic():
                ddt, creato = DDTIngresso.objects.get_or_create(
                    fornitore=fornitore,
                    numero=numero,
                    data=data,
                    defaults={"origine": origine, "creato_da": utente},
                )
        except IntegrityError:
            ddt, creato = DDTIngresso.objects.get(fornitore=fornitore, numero=numero, data=data), False
        ddt = DDTIngresso.objects.select_for_update().get(pk=ddt.pk)  # importazioni concorrenti in fila
        (esito.ddt_creati if creato else esito.ddt_aggiornati).append(ddt)
        presenti = set(ddt.materiali.exclude(riga_documento=None).values_list("riga_documento", flat=True))
        for r in righe:
            if r.riga_documento in presenti:
                esito.materiali_gia_presenti += 1
                continue
            componente = assicura_componente(
                r.codice,
                "",
                descrizione=r.descrizione,
                um=um_da_documento(r.unita_misura),
                fornitore=fornitore,
                prezzo_medio=r.prezzo_unitario,
            )
            materiale = MaterialeIngresso.objects.create(
                ddt=ddt,
                componente=componente,
                riga_documento=r.riga_documento,
                descrizione=r.descrizione,
                quantita=r.quantita,
                unita_misura=r.unita_misura,
                prezzo_unitario=r.prezzo_unitario,
                data_ingresso=data,
                verificato=origine == OrigineDDT.XML,  # come in Metodo: il CSV va verificato a mano
                creato_da=utente,
            )
            esito.richieste_chiuse += chiudi_richieste_per_materiale(materiale, utente)
            presenti.add(r.riga_documento)
            esito.materiali_importati += 1
    return esito


def chiudi_richieste_per_materiale(materiale: MaterialeIngresso, utente: Any) -> int:
    """All'arrivo di un materiale con un DDT in ingresso si chiudono le richieste aperte per lo stesso
    codice e variante (di qualsiasi reparto). La giacenza NON viene aggiornata: DuMetodo, come Metodo,
    non gestisce i movimenti di magazzino."""
    richieste = list(
        RichiestaMateriale.objects.select_for_update().filter(
            codice=materiale.componente.codice, variante=materiale.componente.variante, ricevuta=False
        )
    )
    adesso = timezone.now()
    for r in richieste:
        r.ricevuta, r.ricevuta_il, r.ricevuta_da, r.ricevuta_con = True, adesso, utente, materiale
        r.save()
    return len(richieste)


def richieste_aperte_del_ddt(ddt: DDTIngresso) -> QuerySet[RichiestaMateriale]:
    """Richieste aperte per i materiali arrivati con questo DDT (da chiudere con "Ricevi")."""
    componenti = ddt.materiali.values("componente__codice", "componente__variante")
    filtro = Q()
    for c in componenti.distinct():
        filtro |= Q(codice=c["componente__codice"], variante=c["componente__variante"])
    if not filtro:
        return RichiestaMateriale.objects.none()
    return RichiestaMateriale.objects.filter(filtro, ricevuta=False).select_related("reparto")


def cerca_materiali(
    codice: str = "",
    fornitore: str = "",
    data_da: dt.date | None = None,
    data_a: dt.date | None = None,
    solo_da_verificare: bool = False,
) -> QuerySet[MaterialeIngresso]:
    qs = MaterialeIngresso.objects.select_related("ddt", "componente")
    if codice:
        c = normalizza_codice(codice)
        qs = qs.filter(Q(componente__codice__icontains=c) | Q(componente__variante__icontains=c))
    if fornitore:
        qs = qs.filter(ddt__fornitore__icontains=fornitore.strip())
    if data_da:
        qs = qs.filter(data_ingresso__gte=data_da)
    if data_a:
        qs = qs.filter(data_ingresso__lte=data_a)
    if solo_da_verificare:
        qs = qs.filter(verificato=False)
    return qs.order_by("-data_ingresso", "-ddt_id", "riga_documento", "id")


def xlsx_materiali(qs: QuerySet[MaterialeIngresso]) -> tuple[str, bytes]:
    righe = (
        (
            m.componente.codice,
            m.componente.variante,
            m.descrizione,
            m.quantita,
            m.unita_misura,
            m.prezzo_unitario,
            m.data_ingresso,
            m.ddt.numero,
            m.ddt.data,
            m.ddt.fornitore,
            "Verificato" if m.verificato else "Da verificare",
        )
        for m in qs.iterator(chunk_size=2000)
    )
    intestazione = [
        "Codice",
        "Variante",
        "Descrizione",
        "Quantità",
        "UM",
        "Prezzo",
        "Data ingresso",
        "DDT",
        "Data DDT",
        "Fornitore",
        "Stato",
    ]
    formati = {4: FORMATO_QTA, 6: "#,##0.00##", 7: FORMATO_DATA, 9: FORMATO_DATA}
    return f"materiali_ingresso_{timezone.localdate():%Y%m%d}.xlsx", _xlsx("Materiali", intestazione, righe, formati)
