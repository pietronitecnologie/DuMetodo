"""Gestionale: emissione dei DDT di legge e delle fatture elettroniche.

Flusso: Spedizioni → Consegna segna le righe come consegnate (bolla per il gestionale, come prima) →
Gestionale → DDT da emettere assegna il numero progressivo, fotografa i dati di legge e genera/invia il
file tecnico (lo stesso di Spedizioni → DDT) → Gestionale → Da fatturare raccoglie i DDT di un mese in una
fattura differita (TD24) → Emetti assegna il numero e scrive l'XML FatturaPA. Una fattura emessa non si
modifica né si cancella: si storna con una nota di credito (TD04).

Tutte le operazioni sono transazionali e bloccano le righe che toccano; i numeri vengono da ``Numeratore``
bloccato in scrittura, quindi due emissioni contemporanee non prendono lo stesso numero.
"""

from __future__ import annotations

import calendar
import datetime as dt
import re
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from django.db import transaction
from django.db.models import Exists, Max, OuterRef, Q, QuerySet
from django.utils import timezone

from metodo.anagrafica.models import Articolo
from metodo.configurazione import servizi as config
from metodo.consegne import servizi as consegne
from metodo.consegne.models import DDT, OrigineDDT, RigaConsegnata
from metodo.core.permessi import F, ha_permesso
from metodo.ordini.servizi import OperazioneNonConsentita

from . import fatturapa
from .models import Fattura, Numeratore, Numerazione, RigaFattura, StatoFattura, StatoSdI, TipoNumerazione

CAMPI_FORNITORE = (
    "denominazione",
    "partita_iva",
    "codice_fiscale",
    "indirizzo",
    "cap",
    "comune",
    "provincia",
    "nazione",
    "regime_fiscale",
    "rea_ufficio",
    "rea_numero",
    "capitale_sociale",
    "telefono",
    "email",
)
CAMPI_CLIENTE = (
    "denominazione",
    "partita_iva",
    "codice_fiscale",
    "indirizzo",
    "cap",
    "comune",
    "provincia",
    "nazione",
    "codice_destinatario",
    "pec",
)


def _richiedi(utente: Any, codice: str) -> None:
    if not ha_permesso(utente, codice):
        raise OperazioneNonConsentita("Non hai i permessi per questa operazione (sezione Gestionale).")


# ------------------------------------------------------------------------------------ dati fiscali
def dati_fornitore() -> dict[str, str]:
    d = {c: str(config.leggi(f"gestionale.fornitore_{c}") or "").strip() for c in CAMPI_FORNITORE}
    d["denominazione"] = d["denominazione"] or str(config.leggi("azienda.nome_fornitore") or "")
    d["codice_fiscale"] = d["codice_fiscale"] or d["partita_iva"]
    return d


def dati_cliente(committente: Any = None) -> dict[str, str]:
    """Cessionario e destinatario: i dati fiscali del cliente committente (Clienti committenti → Impostazioni);
    per il predefinito, o senza committente, quelli di Gestionale → Impostazioni."""
    d = {c: str(config.leggi(f"gestionale.cliente_{c}", committente) or "").strip() for c in CAMPI_CLIENTE}
    if not d["denominazione"]:
        from metodo.committenti.models import Committente

        c = Committente.objects.filter(pk=getattr(committente, "pk", committente)).first() if committente else None
        d["denominazione"] = c.nome if c else str(config.leggi("azienda.nome_cliente") or "")
    d["codice_destinatario"] = d["codice_destinatario"].upper()
    return d


def indirizzo_completo(s: dict[str, str]) -> str:
    prov = f" ({s['provincia']})" if s.get("provincia") else ""
    return f"{s.get('indirizzo', '')}, {s.get('cap', '')} {s.get('comune', '')}{prov}".strip(" ,")


def problemi_dati_ddt(committente: Any = None) -> list[str]:
    """Il DDT deve riportare mittente e destinatario (DPR 472/1996)."""
    p = []
    for chi, d in (("Fornitore", dati_fornitore()), ("Cliente", dati_cliente(committente))):
        if not d["denominazione"]:
            p.append(f"{chi}: ragione sociale mancante.")
        if not (d["indirizzo"] and d["comune"]):
            p.append(f"{chi}: indirizzo e comune mancanti.")
    if not dati_fornitore()["partita_iva"]:
        p.append("Fornitore: partita IVA mancante.")
    return p


def problemi_dati_fattura(committente: Any = None) -> list[str]:
    """Controlli dei dati fiscali con un documento di prova (gli stessi che farà l'emissione)."""
    prova = _documento_prova(committente)
    return fatturapa.controlla(prova)


def _documento_prova(committente: Any = None) -> fatturapa.Documento:
    aliquota, natura = _iva(committente)
    return fatturapa.Documento(
        tipo="TD01",
        numero="1",
        data=timezone.localdate(),
        cedente=fatturapa.Soggetto.da_dict(dati_fornitore()),
        cessionario=fatturapa.Soggetto.da_dict(dati_cliente(committente)),
        linee=[fatturapa.Linea(1, "Prova", Decimal(1), Decimal(1), aliquota, natura=natura)],
        progressivo_invio="00001",
    )


def _iva(committente: Any = None) -> tuple[Decimal, str]:
    aliquota = Decimal(config.leggi("gestionale.aliquota_iva", committente) or 0)
    natura = (config.leggi("gestionale.natura_iva", committente) or "") if aliquota == 0 else ""
    return aliquota, natura


def scadenza(data: dt.date, giorni: int, fine_mese: bool) -> dt.date:
    s = data + dt.timedelta(days=giorni)
    if fine_mese:
        s = s.replace(day=calendar.monthrange(s.year, s.month)[1])
    return s


# ------------------------------------------------------------------------------------- numerazione
_RE_FORMATO_DDT = re.compile(r"^[A-Za-z0-9/_-]{1,20}$")


def numerazioni(tipo: str) -> QuerySet[Numerazione]:
    return Numerazione.objects.filter(tipo=tipo, attiva=True).order_by("-predefinita", "nome")


def scegli_numerazione(tipo: str, numerazione_id: int | None) -> Numerazione:
    qs = numerazioni(tipo)
    n = qs.filter(pk=numerazione_id).first() if numerazione_id else qs.filter(predefinita=True).first() or qs.first()
    if n is None:
        raise OperazioneNonConsentita("Numerazione non trovata o non attiva (Amministrazione → Numerazioni).")
    return n


def prossimo_numero(numerazione: Numerazione, anno: int) -> int:
    """Incrementa e restituisce il contatore (riga bloccata fino alla fine della transazione)."""
    Numeratore.objects.get_or_create(numerazione=numerazione, anno=anno)
    n = Numeratore.objects.select_for_update().get(numerazione=numerazione, anno=anno)
    n.ultimo += 1
    n.save(update_fields=["ultimo"])
    return n.ultimo


def ultimo_usato(numerazione: Numerazione, anno: int) -> int:
    """Progressivo più alto già assegnato a un documento (0 = nessuno)."""
    if numerazione.tipo == TipoNumerazione.DDT:
        qs = DDT.objects.filter(numerazione=numerazione, anno=anno)
    else:
        qs = Fattura.objects.filter(numerazione=numerazione, anno=anno, progressivo__isnull=False)
    return qs.aggregate(m=Max("progressivo"))["m"] or 0


def prossimo_previsto(numerazione: Numerazione, anno: int) -> int:
    contatore = Numeratore.objects.filter(numerazione=numerazione, anno=anno).first()
    return max(contatore.ultimo if contatore else 0, ultimo_usato(numerazione, anno)) + 1


def _richiedi_numerazioni(utente: Any, tipo: str) -> None:
    """Le numerazioni dei DDT si definiscono anche da Configurazione; quelle delle fatture solo dal Gestionale."""
    if tipo == TipoNumerazione.DDT and ha_permesso(utente, F.GESTIONALE_IMPOSTAZIONI, F.CONFIGURAZIONE):
        return
    _richiedi(utente, F.GESTIONALE_IMPOSTAZIONI)


@transaction.atomic
def imposta_prossimo(utente: Any, numerazione_id: int, anno: int, prossimo: int) -> Numeratore:
    """Fa partire la numerazione di quell'anno da ``prossimo`` (es. 10 se fino al 9 si è usato un altro programma).

    Non si può tornare su numeri già assegnati da Metodo v2: due documenti avrebbero lo stesso numero."""
    numerazione = Numerazione.objects.get(pk=numerazione_id)
    _richiedi_numerazioni(utente, numerazione.tipo)
    if not 2000 <= anno <= 2100:
        raise OperazioneNonConsentita("Anno non valido.")
    if prossimo < 1:
        raise OperazioneNonConsentita("Il numero di partenza deve essere almeno 1.")
    usato = ultimo_usato(numerazione, anno)
    if prossimo <= usato:
        raise OperazioneNonConsentita(
            f"«{numerazione}» {anno}: il numero {usato} è già stato assegnato, "
            f"il prossimo deve essere almeno {usato + 1}."
        )
    Numeratore.objects.get_or_create(numerazione=numerazione, anno=anno)
    contatore = Numeratore.objects.select_for_update().get(numerazione=numerazione, anno=anno)
    contatore.ultimo = prossimo - 1
    contatore.save(update_fields=["ultimo"])
    return contatore


def _controlla_formato(tipo: str, formato: str) -> None:
    if "{numero}" not in formato:
        raise OperazioneNonConsentita("Il formato deve contenere {numero}.")
    try:
        esempio = formato.format(numero=12345, anno=2026)
    except (KeyError, IndexError, ValueError) as exc:
        raise OperazioneNonConsentita("Nel formato si possono usare solo {numero} e {anno}.") from exc
    if len(esempio) > 20:
        raise OperazioneNonConsentita("Il numero risultante supera i 20 caratteri ammessi.")
    if tipo == TipoNumerazione.DDT and not _RE_FORMATO_DDT.match(esempio):
        raise OperazioneNonConsentita("Il numero di un DDT può contenere solo lettere, cifre, / - _ (niente spazi).")


@transaction.atomic
def salva_numerazione(
    utente: Any,
    numerazione_id: int | None,
    *,
    tipo: str,
    nome: str,
    formato: str,
    predefinita: bool,
    attiva: bool = True,
) -> Numerazione:
    nome, formato = (nome or "").strip()[:60], (formato or "").strip()[:30]
    n = Numerazione.objects.select_for_update().get(pk=numerazione_id) if numerazione_id else None
    _richiedi_numerazioni(utente, n.tipo if n else tipo)
    if not nome:
        raise OperazioneNonConsentita("Indica il nome della numerazione.")
    if n is None:
        if tipo not in TipoNumerazione.values:
            raise OperazioneNonConsentita("Tipo non valido.")
        n = Numerazione(tipo=tipo)
    _controlla_formato(n.tipo, formato)
    if n.pk and formato != n.formato and _usata(n):
        raise OperazioneNonConsentita(
            "Il formato di una numerazione già usata non si cambia (i documenti emessi hanno quel formato): "
            "crea una nuova numerazione."
        )
    if Numerazione.objects.filter(tipo=n.tipo, nome__iexact=nome).exclude(pk=n.pk).exists():
        raise OperazioneNonConsentita(f"Esiste già una numerazione «{nome}».")
    if Numerazione.objects.filter(tipo=n.tipo, formato=formato).exclude(pk=n.pk).exists():
        raise OperazioneNonConsentita("Un'altra numerazione ha lo stesso formato: i numeri si confonderebbero.")
    if n.pk and n.predefinita and not (predefinita and attiva):
        raise OperazioneNonConsentita("Per cambiare la predefinita, segna come predefinita un'altra numerazione.")
    if predefinita:
        Numerazione.objects.filter(tipo=n.tipo, predefinita=True).exclude(pk=n.pk).update(predefinita=False)
    n.nome, n.formato, n.predefinita, n.attiva = nome, formato, predefinita, attiva or predefinita
    n.save()
    return n


def _usata(n: Numerazione) -> bool:
    if n.tipo == TipoNumerazione.DDT:
        return DDT.objects.filter(numerazione=n).exists()
    return Fattura.objects.filter(numerazione=n).exists()


# --------------------------------------------------------------------------------------------- DDT
def righe_da_emettere() -> QuerySet[RigaConsegnata]:
    return consegne.righe_senza_ddt()


def valori_predefiniti_ddt(committente: Any = None) -> dict[str, Any]:
    def v(chiave: str) -> Any:
        return config.leggi(f"gestionale.{chiave}", committente)

    return {
        "causale": v("ddt_causale") or "Vendita",
        "aspetto_beni": v("ddt_aspetto_beni") or "",
        "trasporto_a_cura": v("ddt_trasporto_a_cura") or "Mittente",
        "porto": v("ddt_porto") or "Franco",
        "vettore": v("ddt_vettore") or "",
        "vettore_indirizzo": v("ddt_vettore_indirizzo") or "",
    }


def committente_delle_righe(righe_ids: Sequence[int]) -> int | None:
    """Il cliente committente delle righe consegnate (``None`` se nessuna riga o se sono di più committenti)."""
    ids = set(
        RigaConsegnata.objects.filter(pk__in=righe_ids)
        .values_list("riga__ordine__committente_id", flat=True)
        .distinct()
    )
    return ids.pop() if len(ids) == 1 else None


def _descrizione_articoli(codici: set[str]) -> dict[str, Articolo]:
    return {a.codice: a for a in Articolo.objects.filter(codice__in=codici)}


def _riga_documento(rc: RigaConsegnata, articoli: dict[str, Articolo]) -> dict[str, str]:
    r = rc.riga
    a = articoli.get(r.articolo)
    return {
        "ordine": r.ordine.codice,
        "riga": r.n_riga,
        "codice": r.articolo + (f" {r.variante}" if r.variante else ""),
        "descrizione": (a.descrizione if a and a.descrizione else r.articolo)[:300],
        "um": a.get_um_display() if a else "",
        "qta": quantita_testo(rc.qta),
    }


def quantita_testo(q: Decimal) -> str:
    """20.000 → «20», 2.500 → «2,5»: sul documento stampato «20.000» si leggerebbe ventimila."""
    testo = f"{Decimal(q).normalize():f}"
    return testo.replace(".", ",")


@transaction.atomic
def emetti_ddt(
    utente: Any, righe_ids: Sequence[int], data: dt.date, campi: dict[str, Any], numerazione_id: int | None = None
) -> DDT:
    """DDT con numerazione progressiva dell'anno e dati di legge; genera e accoda il file tecnico.

    Lo emette chi gestisce i DDT in Gestionale oppure chi registra i DDT in Spedizioni."""
    if not ha_permesso(utente, F.GESTIONALE_DDT, F.DDT):
        raise OperazioneNonConsentita("Non hai i permessi per questa operazione.")
    committente_id = committente_delle_righe(righe_ids)
    problemi = problemi_dati_ddt(committente_id)
    if problemi:
        raise OperazioneNonConsentita("Completa i dati fiscali (Gestionale → Impostazioni): " + " ".join(problemi))
    if data > timezone.localdate():
        raise OperazioneNonConsentita("La data del DDT non può essere nel futuro.")
    if campi.get("trasporto_a_cura") == "Vettore" and not (campi.get("vettore") or "").strip():
        raise OperazioneNonConsentita("Con il trasporto a cura del vettore indica il vettore.")
    numerazione = scegli_numerazione(TipoNumerazione.DDT, numerazione_id)
    ultimo = DDT.objects.filter(numerazione=numerazione, anno=data.year).order_by("-progressivo").first()
    if ultimo and ultimo.data > data:
        raise OperazioneNonConsentita(
            f"Il DDT n. {ultimo.numero} ha data {ultimo.data:%d/%m/%Y}: la numerazione deve seguire le date."
        )
    progressivo = prossimo_numero(numerazione, data.year)
    # Numero già usato da un DDT registrato a mano con lo stesso anno: si passa al successivo.
    while DDT.objects.filter(numero=numerazione.numero(progressivo, data.year), anno=data.year).exists():
        progressivo = prossimo_numero(numerazione, data.year)
    inizio = campi.get("inizio_trasporto") or timezone.now()
    ddt = consegne.crea_ddt(
        numerazione.numero(progressivo, data.year),
        data,
        righe_ids,
        utente,
        origine=OrigineDDT.INTERNO,
        progressivo=progressivo,
        numerazione=numerazione,
        causale=campi.get("causale") or "Vendita",
        aspetto_beni=(campi.get("aspetto_beni") or "")[:100],
        colli=campi.get("colli"),
        peso_kg=campi.get("peso_kg"),
        trasporto_a_cura=campi.get("trasporto_a_cura") or "Mittente",
        vettore=(campi.get("vettore") or "")[:150],
        vettore_indirizzo=(campi.get("vettore_indirizzo") or "")[:200],
        porto=campi.get("porto") or "",
        inizio_trasporto=inizio,
        note=(campi.get("note") or "")[:2000],
    )
    righe = list(
        ddt.righe.select_related("riga__ordine").order_by(
            "riga__ordine__serie", "riga__ordine__numero", "riga__ordine__appendice", "riga__n_riga"
        )
    )
    articoli = _descrizione_articoli({rc.riga.articolo for rc in righe})
    ddt.documento = {
        "mittente": dati_fornitore(),
        "destinatario": dati_cliente(ddt.committente_id),
        "destinazione": config.leggi("gestionale.cliente_destinazione", ddt.committente_id) or "",
        "righe": [_riga_documento(rc, articoli) for rc in righe],
    }
    ddt.save(update_fields=["documento"])
    return ddt


def ddt_fatturato(ddt: DDT) -> bool:
    return RigaFattura.objects.filter(ddt=ddt).exclude(fattura__tipo="TD04").exists()


@transaction.atomic
def annulla_ddt(utente: Any, ddt_id: int, motivo: str) -> DDT:
    """Annulla un DDT emesso da Metodo v2: il numero resta (consumato), le righe tornano da emettere."""
    _richiedi(utente, F.GESTIONALE_DDT)
    ddt = DDT.objects.select_for_update().get(pk=ddt_id)
    if not ddt.emesso_da_metodo:
        raise OperazioneNonConsentita("Si annullano solo i DDT emessi da Metodo v2.")
    if ddt.annullato:
        raise OperazioneNonConsentita("Il DDT è già annullato.")
    if ddt_fatturato(ddt):
        raise OperazioneNonConsentita("Il DDT è già in una fattura: prima elimina la bozza o storna la fattura.")
    motivo = (motivo or "").strip()
    if not motivo:
        raise OperazioneNonConsentita("Indica il motivo dell'annullamento.")
    ddt.annullato_il, ddt.annullato_da, ddt.motivo_annullamento = timezone.now(), utente, motivo[:200]
    ddt.save(update_fields=["annullato_il", "annullato_da", "motivo_annullamento"])
    RigaConsegnata.objects.filter(ddt=ddt).update(ddt=None)
    return ddt


# ----------------------------------------------------------------------------------------- fatture
def _righe_fatturate() -> QuerySet[RigaFattura]:
    return RigaFattura.objects.exclude(fattura__tipo="TD04")


def righe_da_fatturare(ddt: DDT) -> QuerySet[RigaConsegnata]:
    """Righe del DDT non ancora in fattura (le scorporate non si fatturano)."""
    return (
        ddt.righe.filter(riga__scorporata=False)
        .exclude(Exists(_righe_fatturate().filter(riga_consegnata=OuterRef("pk"))))
        .select_related("riga__ordine")
        .order_by("riga__ordine__serie", "riga__ordine__numero", "riga__ordine__appendice", "riga__n_riga")
    )


def ddt_da_fatturare() -> QuerySet[DDT]:
    da_fatturare = RigaConsegnata.objects.filter(ddt=OuterRef("pk"), riga__scorporata=False).exclude(
        Exists(_righe_fatturate().filter(riga_consegnata=OuterRef("pk")))
    )
    return (
        DDT.objects.filter(annullato_il__isnull=True)
        .filter(Q(causale="") | Q(causale="Vendita"))
        .filter(Exists(da_fatturare))
        .order_by("data", "anno", "progressivo", "numero")
    )


RAGGRUPPAMENTI: tuple[tuple[str, str], ...] = (
    ("mese", "Una fattura per tutti i DDT selezionati"),
    ("giorno", "Una fattura per ogni giorno"),
    ("ddt", "Una fattura per ogni DDT"),
    ("righe", "Al massimo N righe per fattura"),
)
DATE_FATTURA: tuple[tuple[str, str], ...] = (
    ("scelta", "La data indicata"),
    ("ultimo_ddt", "La data dell'ultimo DDT della fattura"),
    ("fine_mese", "L'ultimo giorno del mese dei DDT"),
)


@transaction.atomic
def crea_fattura_differita(utente: Any, ddt_ids: Sequence[int], data: dt.date) -> Fattura:
    """Una sola bozza di fattura differita (TD24) per i DDT indicati, con la data indicata."""
    return crea_fatture_differite(utente, ddt_ids, raggruppa="mese", tipo_data="scelta", data=data)[0]


@transaction.atomic
def crea_fatture_differite(
    utente: Any,
    ddt_ids: Sequence[int],
    *,
    raggruppa: str = "mese",
    max_righe: int | None = None,
    tipo_data: str = "scelta",
    data: dt.date | None = None,
) -> list[Fattura]:
    """Bozze di fattura differita (TD24) dai DDT di uno stesso mese, divise come scelto dall'utente.

    ``raggruppa``: ``mese`` (una fattura), ``giorno`` (una per data dei DDT), ``ddt`` (una per DDT), ``righe``
    (al massimo ``max_righe`` righe per fattura; un DDT può finire su più fatture, ognuna lo cita in DatiDDT).
    ``tipo_data``: ``scelta`` (``data``), ``ultimo_ddt`` (data dell'ultimo DDT della fattura), ``fine_mese``.
    Le bozze sono restituite in ordine di data: emetterle in quest'ordine rispetta la numerazione.
    """
    _richiedi(utente, F.GESTIONALE_FATTURE)
    if raggruppa not in dict(RAGGRUPPAMENTI) or tipo_data not in dict(DATE_FATTURA):
        raise OperazioneNonConsentita("Scelta non valida.")
    if raggruppa == "righe" and not (max_righe and max_righe >= 1):
        raise OperazioneNonConsentita("Indica il numero massimo di righe per fattura (almeno 1).")
    if tipo_data == "scelta" and data is None:
        raise OperazioneNonConsentita("Indica la data delle fatture.")
    ids = sorted(set(ddt_ids))
    if not ids:
        raise OperazioneNonConsentita("Seleziona almeno un DDT.")
    ddts = list(DDT.objects.select_for_update().filter(pk__in=ids).order_by("data", "progressivo", "numero"))
    if len(ddts) != len(ids) or any(d.annullato for d in ddts):
        raise OperazioneNonConsentita("Alcuni DDT non esistono più o sono annullati: ricarica la pagina.")
    if len({d.committente_id for d in ddts}) > 1:
        raise OperazioneNonConsentita(
            "I DDT selezionati sono di più clienti committenti: una fattura è di un solo cliente."
        )
    mesi = {(d.data.year, d.data.month) for d in ddts}
    if len(mesi) > 1:
        raise OperazioneNonConsentita("La fattura differita raccoglie i DDT di un solo mese: selezionali per mese.")

    # Tutte le righe da fatturare, nell'ordine dei DDT.
    voci: list[tuple[DDT, RigaConsegnata]] = []
    senza_prezzo = []
    for ddt in ddts:
        for rc in righe_da_fatturare(ddt).select_for_update(of=("self",)):
            if rc.prezzo_unitario is None:
                senza_prezzo.append(f"{rc.riga.ordine.codice} riga {rc.riga.n_riga}")
            else:
                voci.append((ddt, rc))
    if senza_prezzo:
        raise OperazioneNonConsentita(
            "Righe senza prezzo (correggi l'ordine prima di fatturare): " + ", ".join(senza_prezzo[:10])
        )
    if not voci:
        raise OperazioneNonConsentita("Nessuna riga da fatturare nei DDT selezionati.")

    gruppi: list[list[tuple[DDT, RigaConsegnata]]] = []
    if raggruppa == "mese":
        gruppi = [voci]
    elif raggruppa == "righe":
        gruppi = [voci[i : i + max_righe] for i in range(0, len(voci), max_righe)]  # type: ignore[arg-type]
    else:
        chiave = (lambda v: v[0].data) if raggruppa == "giorno" else (lambda v: v[0].pk)
        per_chiave: dict[Any, list[tuple[DDT, RigaConsegnata]]] = {}
        for v in voci:
            per_chiave.setdefault(chiave(v), []).append(v)
        gruppi = list(per_chiave.values())

    oggi = timezone.localdate()
    piano = []
    for gruppo in gruppi:
        ultimo = max(d.data for d, _ in gruppo)
        if tipo_data == "ultimo_ddt":
            giorno = ultimo
        elif tipo_data == "fine_mese":
            giorno = ultimo.replace(day=calendar.monthrange(ultimo.year, ultimo.month)[1])
        else:
            giorno = data  # type: ignore[assignment]
        if giorno < ultimo:
            raise OperazioneNonConsentita(
                f"La data {giorno:%d/%m/%Y} precede il DDT del {ultimo:%d/%m/%Y}: scegli una data successiva."
            )
        if giorno > oggi:
            raise OperazioneNonConsentita(
                f"La data {giorno:%d/%m/%Y} è nel futuro: le fatture si emettono a partire da quel giorno."
            )
        piano.append((giorno, gruppo))
    piano.sort(key=lambda x: x[0])
    return [_crea_bozza(utente, giorno, gruppo) for giorno, gruppo in piano]


def _crea_bozza(utente: Any, data: dt.date, voci: list[tuple[DDT, RigaConsegnata]]) -> Fattura:
    aliquota, natura = _iva(voci[0][0].committente_id)
    fattura = Fattura.objects.create(
        tipo="TD24", data=data, anno=data.year, creata_da=utente, committente_id=voci[0][0].committente_id
    )
    articoli = _descrizione_articoli({rc.riga.articolo for _, rc in voci})
    for n, (ddt, rc) in enumerate(voci, start=1):
        r = rc.riga
        sconto = r.sconto or Decimal("0")
        a = articoli.get(r.articolo)
        descr = a.descrizione if a and a.descrizione else r.articolo
        RigaFattura.objects.create(
            fattura=fattura,
            n_linea=n,
            ddt=ddt,
            riga_consegnata=rc,
            codice=r.articolo + (f" {r.variante}" if r.variante else ""),
            descrizione=f"{descr} – ordine {r.ordine.codice} riga {r.n_riga}"[:1000],
            qta=rc.qta,
            um=a.get_um_display()[:10] if a else "",
            prezzo_unitario=rc.prezzo_unitario,
            sconto_unitario=sconto,
            totale=fatturapa.arrotonda(rc.qta * (rc.prezzo_unitario - sconto)),
            aliquota=aliquota,
            natura=natura,
        )
    _ricalcola(fattura)
    return fattura


def emetti_bozze(
    utente: Any, fatture_ids: Sequence[int], numerazione_id: int | None = None
) -> tuple[list[Fattura], str]:
    """Emette le bozze indicate in ordine di data (così la numerazione segue le date).

    Si ferma alla prima che non si può emettere: restituisce le emesse e il motivo dell'interruzione."""
    _richiedi(utente, F.GESTIONALE_FATTURE)
    bozze = Fattura.objects.filter(pk__in=fatture_ids, stato=StatoFattura.BOZZA).order_by("data", "pk")
    emesse: list[Fattura] = []
    for b in bozze:
        try:
            emesse.append(emetti_fattura(utente, b.pk, numerazione_id))
        except OperazioneNonConsentita as exc:
            return emesse, f"Bozza del {b.data:%d/%m/%Y}: {exc}"
    return emesse, ""


def _ricalcola(fattura: Fattura) -> None:
    doc = documento(fattura, numero="1", progressivo_invio="00001")
    fattura.imponibile, fattura.imposta, fattura.totale = fatturapa.totali(doc)
    fattura.save(update_fields=["imponibile", "imposta", "totale"])


def documento(f: Fattura, *, numero: str, progressivo_invio: str) -> fatturapa.Documento:
    righe = list(f.righe.select_related("ddt"))
    cedente = f.cedente or dati_fornitore()
    cessionario = f.cessionario or dati_cliente(f.committente_id)
    rif: dict[int, fatturapa.RiferimentoDDT] = {}
    for r in righe:
        if r.ddt_id is not None:
            rif.setdefault(r.ddt_id, fatturapa.RiferimentoDDT(r.ddt.numero, r.ddt.data)).linee.append(r.n_linea)
    pag = f.pagamento or {}
    collegata = None
    if f.fattura_collegata_id:
        collegata = (f.fattura_collegata.numero, f.fattura_collegata.data)
    causali = [f.causale] if f.causale else []
    return fatturapa.Documento(
        tipo=f.tipo,
        numero=numero,
        data=f.data,
        cedente=fatturapa.Soggetto.da_dict(cedente),
        cessionario=fatturapa.Soggetto.da_dict(cessionario),
        linee=[
            fatturapa.Linea(
                numero=r.n_linea,
                descrizione=r.descrizione,
                quantita=r.qta,
                prezzo_unitario=r.prezzo_unitario,
                aliquota=r.aliquota,
                codice=r.codice,
                unita_misura=r.um,
                sconto_unitario=r.sconto_unitario,
                natura=r.natura,
            )
            for r in righe
        ],
        progressivo_invio=progressivo_invio,
        esigibilita=config.leggi("gestionale.esigibilita_iva", f.committente_id) or "I",
        riferimento_normativo=config.leggi("gestionale.riferimento_normativo", f.committente_id) or "",
        causali=causali,
        ddt=list(rif.values()),
        fattura_collegata=collegata,
        pagamento=fatturapa.Pagamento(
            modalita=pag.get("modalita", "MP05"),
            scadenza=dt.date.fromisoformat(pag["scadenza"]) if pag.get("scadenza") else None,
            iban=pag.get("iban", ""),
            banca=pag.get("banca", ""),
        )
        if pag
        else None,
    )


def anteprima_errori(fattura: Fattura) -> list[str]:
    return fatturapa.controlla(documento(fattura, numero="1", progressivo_invio="00001"))


@transaction.atomic
def emetti_fattura(utente: Any, fattura_id: int, numerazione_id: int | None = None) -> Fattura:
    """Assegna il numero, fotografa dati fiscali e pagamento, scrive l'XML. Da qui il documento è definitivo."""
    _richiedi(utente, F.GESTIONALE_FATTURE)
    f = Fattura.objects.select_for_update().get(pk=fattura_id)
    if not f.is_bozza:
        raise OperazioneNonConsentita("La fattura è già emessa.")
    if f.data > timezone.localdate():
        raise OperazioneNonConsentita("La data della fattura non può essere nel futuro.")
    numerazione = scegli_numerazione(TipoNumerazione.FATTURA, numerazione_id)
    ultima = (
        Fattura.objects.filter(numerazione=numerazione, anno=f.anno, stato=StatoFattura.EMESSA)
        .order_by("-progressivo")
        .first()
    )
    if ultima and ultima.data > f.data:
        raise OperazioneNonConsentita(
            f"L'ultima fattura «{numerazione}» emessa (n. {ultima.numero}) ha data {ultima.data:%d/%m/%Y}: "
            "la numerazione deve seguire le date. Cambia la data della bozza."
        )
    f.cedente, f.cessionario = dati_fornitore(), dati_cliente(f.committente_id)
    if not f.is_nota_di_credito:
        giorni = int(config.leggi("gestionale.pagamento_giorni", f.committente_id) or 0)
        f.scadenza = scadenza(f.data, giorni, bool(config.leggi("gestionale.pagamento_fine_mese", f.committente_id)))
        f.pagamento = {
            "modalita": config.leggi("gestionale.pagamento_modalita", f.committente_id) or "MP05",
            "scadenza": f.scadenza.isoformat(),
            "iban": config.leggi("gestionale.pagamento_iban") or "",
            "banca": config.leggi("gestionale.pagamento_banca") or "",
        }
    errori = fatturapa.controlla(documento(f, numero="1", progressivo_invio="00001"))
    if errori:
        raise OperazioneNonConsentita("Fattura non emessa: " + " ".join(errori))
    f.numerazione = numerazione
    f.progressivo = prossimo_numero(numerazione, f.anno)
    f.numero = numerazione.numero(f.progressivo, f.anno)
    if Fattura.objects.filter(anno=f.anno, numero=f.numero).exclude(pk=f.pk).exists():
        raise OperazioneNonConsentita(
            f"Il numero {f.numero} esiste già nel {f.anno} (un'altra numerazione): usa formati diversi."
        )
    f.progressivo_invio = fatturapa.progressivo_invio(f.pk)
    doc = documento(f, numero=f.numero, progressivo_invio=f.progressivo_invio)
    try:
        f.xml = fatturapa.genera(doc).decode("utf-8")
    except fatturapa.FatturaNonValida as exc:
        raise OperazioneNonConsentita(f"Fattura non emessa: {exc}") from exc
    f.xml_nome = fatturapa.nome_file(doc)
    f.imponibile, f.imposta, f.totale = fatturapa.totali(doc)
    f.stato, f.emessa_il, f.emessa_da = StatoFattura.EMESSA, timezone.now(), utente
    f.save()
    return f


@transaction.atomic
def modifica_bozza(utente: Any, fattura_id: int, data: dt.date, causale: str) -> Fattura:
    _richiedi(utente, F.GESTIONALE_FATTURE)
    f = Fattura.objects.select_for_update().get(pk=fattura_id)
    if not f.is_bozza:
        raise OperazioneNonConsentita("Una fattura emessa non si modifica: si storna con una nota di credito.")
    ultimo_ddt = f.righe.exclude(ddt=None).order_by("-ddt__data").values_list("ddt__data", flat=True).first()
    if ultimo_ddt and data < ultimo_ddt:
        raise OperazioneNonConsentita("La data della fattura non può precedere quella dei DDT.")
    f.data, f.anno, f.causale = data, data.year, (causale or "").strip()[:200]
    f.save(update_fields=["data", "anno", "causale"])
    return f


@transaction.atomic
def elimina_bozza(utente: Any, fattura_id: int) -> None:
    _richiedi(utente, F.GESTIONALE_FATTURE)
    f = Fattura.objects.select_for_update().get(pk=fattura_id)
    if not f.is_bozza:
        raise OperazioneNonConsentita("Una fattura emessa non si elimina: si storna con una nota di credito.")
    f.delete()


@transaction.atomic
def nota_di_credito(utente: Any, fattura_id: int) -> Fattura:
    """Bozza di nota di credito (TD04) che storna tutta la fattura: righe uguali, da ridurre se lo storno è parziale."""
    _richiedi(utente, F.GESTIONALE_FATTURE)
    origine = Fattura.objects.select_for_update().get(pk=fattura_id)
    if origine.is_bozza or origine.is_nota_di_credito:
        raise OperazioneNonConsentita("Si storna solo una fattura emessa.")
    if origine.note_di_credito.filter(stato=StatoFattura.BOZZA).exists():
        raise OperazioneNonConsentita("C'è già una nota di credito in bozza per questa fattura.")
    oggi = timezone.localdate()
    nota = Fattura.objects.create(
        tipo="TD04",
        data=oggi,
        anno=oggi.year,
        fattura_collegata=origine,
        committente_id=origine.committente_id,
        causale=f"Storno fattura n. {origine.numero} del {origine.data:%d/%m/%Y}",
        creata_da=utente,
    )
    for r in origine.righe.all():
        RigaFattura.objects.create(
            fattura=nota,
            n_linea=r.n_linea,
            codice=r.codice,
            descrizione=r.descrizione,
            qta=r.qta,
            um=r.um,
            prezzo_unitario=r.prezzo_unitario,
            sconto_unitario=r.sconto_unitario,
            totale=r.totale,
            aliquota=r.aliquota,
            natura=r.natura,
        )
    _ricalcola(nota)
    return nota


@transaction.atomic
def riduci_riga_nota(utente: Any, riga_id: int, qta: Decimal) -> RigaFattura:
    """Storno parziale: quantità della riga della nota di credito (0 = la riga non si storna)."""
    _richiedi(utente, F.GESTIONALE_FATTURE)
    r = RigaFattura.objects.select_related("fattura").select_for_update(of=("self",)).get(pk=riga_id)
    f = r.fattura
    if not (f.is_bozza and f.is_nota_di_credito):
        raise OperazioneNonConsentita("Si modificano solo le righe di una nota di credito in bozza.")
    massimo = f.fattura_collegata.righe.filter(n_linea=r.n_linea).values_list("qta", flat=True).first() or r.qta
    if qta < 0 or qta > massimo:
        raise OperazioneNonConsentita(f"Quantità tra 0 e {massimo}.")
    if qta == 0:
        r.delete()
        if not f.righe.exists():
            raise OperazioneNonConsentita("La nota di credito deve avere almeno una riga.")
    else:
        r.qta = qta
        r.totale = fatturapa.arrotonda(qta * (r.prezzo_unitario - r.sconto_unitario))
        r.save(update_fields=["qta", "totale"])
    _ricalcola(f)
    return r


@transaction.atomic
def aggiorna_stato_sdi(utente: Any, fattura_id: int, stato: str, note: str) -> Fattura:
    _richiedi(utente, F.GESTIONALE_FATTURE)
    f = Fattura.objects.select_for_update().get(pk=fattura_id)
    if f.is_bozza:
        raise OperazioneNonConsentita("La bozza non si invia: prima emetti la fattura.")
    if stato not in StatoSdI.values:
        raise OperazioneNonConsentita("Stato non valido.")
    f.stato_sdi, f.note_sdi = stato, (note or "").strip()[:300]
    f.save(update_fields=["stato_sdi", "note_sdi"])
    return f


def scadenza_fattura_differita(ddt_data: dt.date) -> dt.date:
    """Termine di emissione della fattura differita: il 15 del mese successivo a quello dei DDT."""
    anno, mese = (ddt_data.year + 1, 1) if ddt_data.month == 12 else (ddt_data.year, ddt_data.month + 1)
    return dt.date(anno, mese, 15)
