from __future__ import annotations

import datetime as dt
import io
import re
from collections.abc import Iterable, Sequence
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils import timezone

from dumetodo.comunicazioni import email
from dumetodo.configurazione import servizi as config
from dumetodo.core.ruoli import Ruolo, ha_ruolo
from dumetodo.ordini.models import Ordine, RigaOrdine, StatoOrdine
from dumetodo.ordini.servizi import OperazioneNonConsentita
from dumetodo.tracciati import dati, linguaggio

from .models import DDT, Consegna, RigaConsegnata

DA_CONSEGNARE = (StatoOrdine.IN_SPEDIZIONE, StatoOrdine.EVASIONE_PARZIALE)


def _richiedi_operatore(utente: Any) -> None:
    if not ha_ruolo(utente, Ruolo.OPERATORE):
        raise OperazioneNonConsentita("Non hai i permessi per questa operazione.")


def righe_consegnabili(ordine: Ordine) -> QuerySet[RigaOrdine]:
    """Ordine in spedizione: tutte le righe attive non bloccate e non ancora in bolla.
    Ordine in evasione parziale: solo le righe messe in evasione."""
    qs = ordine.righe.attive().filter(bloccata=False, bolla_emessa=False)
    if ordine.stato == StatoOrdine.EVASIONE_PARZIALE:
        return qs.filter(in_evasione=True).order_by("n_riga", "id")
    if ordine.stato == StatoOrdine.IN_SPEDIZIONE:
        return qs.order_by("n_riga", "id")
    return qs.none()


def ordini_da_consegnare(serie: Sequence[str] = (), ordini_ids: Sequence[int] = ()) -> QuerySet[Ordine]:
    qs = Ordine.objects.filter(stato__in=DA_CONSEGNARE)
    if serie:
        filtro = Q()
        for s in serie:
            filtro |= Q(serie__endswith=s.strip())
        qs = qs.filter(filtro)
    if ordini_ids:
        qs = qs.filter(pk__in=ordini_ids)
    return qs.order_by("serie", "numero", "appendice", "anno")


def _tracciato(chiave: str) -> linguaggio.Tracciato:
    try:
        return linguaggio.compila(config.leggi(chiave))
    except linguaggio.ErroreTracciato as exc:  # non dovrebbe succedere: è validato al salvataggio
        raise OperazioneNonConsentita(f"Il tracciato configurato non è valido: {exc}") from exc


def _nome_selezione(serie: Sequence[str], ordini: list[Ordine]) -> str:
    if len(ordini) == 1 and not serie:
        return ordini[0].serie[-2:]
    return "-".join(s.strip() for s in serie) if serie else "Tutto"


@transaction.atomic
def consegna(utente: Any, *, serie: Sequence[str] = (), ordini_ids: Sequence[int] = ()) -> Consegna:
    """Genera la bolla e segna come consegnate le righe. Tutto o niente.

    Le righe d'ordine sono bloccate (SELECT FOR UPDATE): un doppio clic o due utenti insieme non
    possono consegnare due volte la stessa riga; il secondo trova "nessuna riga da consegnare".
    """
    _richiedi_operatore(utente)
    bloccati = list(ordini_da_consegnare(serie, ordini_ids).select_for_update().order_by("pk"))
    bloccati.sort(key=lambda o: (o.serie, o.numero, o.appendice, o.anno))
    blocchi = [(o, list(righe_consegnabili(o).select_for_update())) for o in bloccati]
    blocchi = [(o, righe) for o, righe in blocchi if righe]
    if not blocchi:
        raise OperazioneNonConsentita("Nessuna riga da consegnare per la selezione indicata.")

    tracciato = _tracciato("consegna.tracciato_bolla")
    selezione = _nome_selezione(serie, [o for o, _ in blocchi])
    try:
        file = linguaggio.genera(
            tracciato,
            dati.documento(utente, selezione),
            [(dati.ordine(o), [dati.riga(r) for r in righe]) for o, righe in blocchi],
        )
    except linguaggio.ErroreTracciato as exc:
        raise OperazioneNonConsentita(f"Impossibile generare il file bolla: {exc}") from exc

    c = Consegna.objects.create(
        creata_da=utente,
        selezione=selezione,
        tracciato=tracciato.nome[:100],
        file_nome=file.nome,
        file_contenuto=file.contenuto,
    )
    oggi = timezone.localdate()
    for o, righe in blocchi:
        for r in righe:
            RigaConsegnata.objects.create(consegna=c, riga=r, qta=r.qta, prezzo_unitario=r.prezzo_unitario)
            r.bolla_emessa, r.in_evasione, r.data_consegna = True, False, oggi
            r.save()
        if o.righe.attive().filter(bolla_emessa=False).exists():
            # Restano righe (bloccate o non evase): l'ordine resta/diventa in evasione parziale.
            o.stato = StatoOrdine.EVASIONE_PARZIALE
        else:
            o.stato, o.data_consegna = StatoOrdine.CONSEGNATO, oggi
        o.save()
    return c


# ----------------------------------------------------------------------------------------- DDT
_RE_NUMERO_DDT = re.compile(r"^[A-Za-z0-9/_-]{1,20}$")


def righe_senza_ddt() -> QuerySet[RigaConsegnata]:
    return (
        RigaConsegnata.objects.filter(ddt__isnull=True)
        .select_related("riga__ordine", "consegna")
        .order_by("riga__ordine__serie", "riga__ordine__numero", "riga__ordine__appendice", "riga__n_riga")
    )


def _raggruppa_per_ordine(righe: Iterable[RigaConsegnata]) -> list[tuple[Ordine, list[RigaConsegnata]]]:
    gruppi: dict[int, tuple[Ordine, list[RigaConsegnata]]] = {}
    for rc in righe:
        gruppi.setdefault(rc.riga.ordine_id, (rc.riga.ordine, []))[1].append(rc)
    return list(gruppi.values())


def _file_tecnico(ddt: DDT, righe: list[RigaConsegnata], utente: Any) -> linguaggio.FileGenerato:
    tracciato = _tracciato("consegna.tracciato_file_tecnico_ddt")
    try:
        return linguaggio.genera(
            tracciato,
            dati.documento(utente, numero=ddt.numero, data=ddt.data),
            [
                (dati.ordine(o), [dati.riga(rc.riga, rc.qta, rc.prezzo_unitario) for rc in rr])
                for o, rr in _raggruppa_per_ordine(righe)
            ],
        )
    except linguaggio.ErroreTracciato as exc:
        raise OperazioneNonConsentita(f"Impossibile generare il file tecnico: {exc}") from exc


def _accoda_file_tecnico(ddt: DDT) -> Any:
    mittente = config.leggi("email.mittente") or settings.DEFAULT_FROM_EMAIL
    return email.accoda(
        oggetto=f"File tecnico DDT {ddt.numero} del {ddt.data:%d/%m/%Y}",
        corpo_testo=f"In allegato il file tecnico del DDT n. {ddt.numero} del {ddt.data:%d/%m/%Y}.\n",
        mittente=mittente,
        destinatari=config.leggi("email.file_tecnico"),
        rispondi_a=[mittente],
        riferimento=f"ddt:{ddt.pk}",
        allegati=[(ddt.file_nome, bytes(ddt.file_contenuto), "text/plain")],
    )


@transaction.atomic
def registra_ddt(numero: str, data: dt.date, righe_ids: Sequence[int], utente: Any) -> DDT:
    _richiedi_operatore(utente)
    numero = (numero or "").strip()
    if not _RE_NUMERO_DDT.match(numero):
        raise OperazioneNonConsentita("Numero DDT non valido (lettere, cifre, / - _, massimo 20 caratteri).")
    ids = set(righe_ids)
    if not ids:
        raise OperazioneNonConsentita("Seleziona almeno una riga.")
    if DDT.objects.filter(numero=numero, anno=data.year).exists():
        raise OperazioneNonConsentita(f"Il DDT {numero} del {data.year} è già registrato.")
    righe = list(RigaConsegnata.objects.select_for_update().filter(pk__in=ids, ddt__isnull=True))
    if len(righe) != len(ids):
        raise OperazioneNonConsentita("Alcune righe sono già state assegnate a un altro DDT: ricarica la pagina.")
    ddt = DDT.objects.create(numero=numero, anno=data.year, data=data, creato_da=utente)
    RigaConsegnata.objects.filter(pk__in=ids).update(ddt=ddt)
    righe = list(
        RigaConsegnata.objects.filter(ddt=ddt)
        .select_related("riga__ordine")
        .order_by("riga__ordine__serie", "riga__ordine__numero", "riga__ordine__appendice", "riga__n_riga")
    )
    file = _file_tecnico(ddt, righe, utente)
    ddt.file_nome, ddt.file_contenuto = file.nome, file.contenuto
    ddt.email = _accoda_file_tecnico(ddt)
    ddt.save()
    return ddt


@transaction.atomic
def reinvia_file_tecnico(ddt_id: int, utente: Any) -> DDT:
    if not ha_ruolo(utente, Ruolo.AMMINISTRATORE):
        raise OperazioneNonConsentita("Solo un amministratore può reinviare il file tecnico.")
    ddt = DDT.objects.select_for_update().get(pk=ddt_id)
    ddt.email = _accoda_file_tecnico(ddt)
    if ddt.email is None:
        raise OperazioneNonConsentita("Nessun destinatario configurato per il file tecnico (Configurazione).")
    ddt.save(update_fields=["email"])
    return ddt


def ordini_del_ddt(numero: str, anno: int) -> QuerySet[Ordine]:
    return Ordine.objects.filter(righe__consegnata__ddt__numero=numero, righe__consegnata__ddt__anno=anno).distinct()


# ----------------------------------------------------------------------------------------- etichette
def codice_a_barre(o: Ordine) -> str:
    """Come in Metodo: anno + serie senza i primi 2 caratteri + numero senza zeri a 5 cifre.
    Es. 2024 / 00040 / 0004300 -> 202404004300."""
    return f"{o.anno}{o.serie[2:]}{(o.numero.lstrip('0') or '0').zfill(5)}"


def etichette_xlsx(ordini: Iterable[Ordine]) -> bytes:
    """File per la stampa unione delle etichette: stesse colonne di Metodo, senza intestazione."""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Etichette"
    codice = config.leggi("consegna.codice_fornitore_etichette") or ""
    for o in ordini:
        ws.append(
            [
                o.numero.lstrip("0") or "0",
                o.data_consegna,
                o.data_scadenza,
                o.get_stato_display(),
                "",
                o.appendice,
                "",
                o.anno,
                codice,
                "",
                o.serie.lstrip("0") or "0",
                codice_a_barre(o),
            ]
        )
        for cella in (ws.cell(ws.max_row, 2), ws.cell(ws.max_row, 3)):
            cella.number_format = "DD/MM/YYYY"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
