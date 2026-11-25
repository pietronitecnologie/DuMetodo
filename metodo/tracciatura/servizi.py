"""Tracciatura: configurazione dei modelli, registrazione con validazione ed esito, annullamento, ricerca."""

from __future__ import annotations

import contextlib
import datetime as dt
import fnmatch
from collections.abc import Iterable, Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from django.db.models import Max, Q, QuerySet
from django.utils import timezone

from metodo.core.permessi import F, ha_permesso
from metodo.ordini.models import Ordine
from metodo.ordini.servizi import OperazioneNonConsentita

from .models import (
    TIPI_FILE,
    TIPI_NUMERO,
    Campo,
    Categoria,
    Collegamento,
    Esito,
    Modello,
    TipoCampo,
    Tracciatura,
    Uso,
    Valore,
)


def _richiedi(utente: Any, codice: str) -> None:
    if not ha_permesso(utente, codice):
        raise OperazioneNonConsentita("Non hai i permessi per questa operazione.")


# --------------------------------------------------------------------------------------- regole sui codici
def schemi(modello: Modello) -> list[str]:
    return [r.strip().upper() for r in modello.codici.splitlines() if r.strip()]


def si_applica(modello: Modello, codici: Iterable[str]) -> bool:
    """Il modello vale per almeno uno dei codici? (nessuna regola = vale per tutti)"""
    regole = schemi(modello)
    if not regole:
        return True
    return any(fnmatch.fnmatchcase(c.strip().upper(), r) for c in codici if c and c.strip() for r in regole)


def modelli_per(codici: Iterable[str] = (), solo_specifici: bool = False) -> list[Modello]:
    """Modelli attivi da proporre per questi codici: prima quelli previsti per il codice, poi quelli generici."""
    codici = [c for c in codici if c]
    specifici, generici = [], []
    for m in Modello.objects.filter(attivo=True).prefetch_related("campi"):
        if schemi(m):
            if codici and si_applica(m, codici):
                specifici.append(m)
        elif not solo_specifici:
            generici.append(m)
    return specifici + generici


# ------------------------------------------------------------------------------------------ configurazione
@transaction.atomic
def salva_modello(utente: Any, modello_id: int | None, dati: Mapping[str, Any]) -> Modello:
    _richiedi(utente, F.TRACCIATURA_CONFIGURA)
    m = Modello.objects.select_for_update().get(pk=modello_id) if modello_id else Modello()
    nome = str(dati.get("nome", "")).strip()[:80]
    if not nome:
        raise OperazioneNonConsentita("Indica il nome del modello.")
    if Modello.objects.filter(nome__iexact=nome).exclude(pk=m.pk).exists():
        raise OperazioneNonConsentita(f"Esiste già un modello «{nome}».")
    categoria = str(dati.get("categoria", Categoria.QUALITA))
    if categoria not in Categoria.values:
        raise OperazioneNonConsentita("Categoria non valida.")
    m.nome, m.categoria = nome, categoria
    m.descrizione = str(dati.get("descrizione", "")).strip()[:4000]
    for c in Collegamento:
        uso = str(dati.get(f"uso_{c.value}", ""))
        setattr(m, f"uso_{c.value}", uso if uso in Uso.values else "")
    righe = [r.strip() for r in str(dati.get("codici", "")).splitlines() if r.strip()]
    m.codici = "\n".join(dict.fromkeys(righe))[:4000]
    m.attivo = bool(dati.get("attivo", True))
    m.save()
    return m


def _decimale(v: Any, etichetta: str) -> Decimal | None:
    testo = str(v or "").strip().replace(" ", "")
    if not testo:
        return None
    if "," in testo:
        testo = testo.replace(".", "").replace(",", ".")
    try:
        d = Decimal(testo)
    except InvalidOperation as exc:
        raise OperazioneNonConsentita(f"{etichetta}: numero non valido ({v}).") from exc
    if not d.is_finite():
        raise OperazioneNonConsentita(f"{etichetta}: numero non valido.")
    return d


@transaction.atomic
def salva_campo(utente: Any, modello_id: int, campo_id: int | None, dati: Mapping[str, Any]) -> Campo:
    _richiedi(utente, F.TRACCIATURA_CONFIGURA)
    modello = Modello.objects.get(pk=modello_id)
    c = Campo.objects.select_for_update().get(pk=campo_id, modello=modello) if campo_id else Campo(modello=modello)
    etichetta = str(dati.get("etichetta", "")).strip()[:80]
    if not etichetta:
        raise OperazioneNonConsentita("Indica l'etichetta del campo.")
    tipo = str(dati.get("tipo", c.tipo or ""))
    if tipo not in TipoCampo.values:
        raise OperazioneNonConsentita("Tipo di campo non valido.")
    if c.pk and tipo != c.tipo and c.valori.exists():
        raise OperazioneNonConsentita(
            "Il campo ha già dei valori registrati: il tipo non si cambia. Disattivalo e creane uno nuovo."
        )
    c.etichetta, c.tipo = etichetta, tipo
    c.obbligatorio = bool(dati.get("obbligatorio"))
    c.aiuto = str(dati.get("aiuto", "")).strip()[:200]
    c.unita = str(dati.get("unita", "")).strip()[:15] if tipo in TIPI_NUMERO else ""
    c.minimo = _decimale(dati.get("minimo"), "Minimo") if tipo in TIPI_NUMERO else None
    c.massimo = _decimale(dati.get("massimo"), "Massimo") if tipo in TIPI_NUMERO else None
    if c.minimo is not None and c.massimo is not None and c.minimo > c.massimo:
        raise OperazioneNonConsentita("Il minimo non può superare il massimo.")
    c.scelte = c.scelte_non_conformi = ""
    if tipo == TipoCampo.SCELTA:
        scelte = list(dict.fromkeys(s.strip() for s in str(dati.get("scelte", "")).splitlines() if s.strip()))
        if len(scelte) < 2:
            raise OperazioneNonConsentita("Una scelta da elenco ha bisogno di almeno 2 voci (una per riga).")
        non_conformi = [s.strip() for s in str(dati.get("scelte_non_conformi", "")).splitlines() if s.strip()]
        estranee = [s for s in non_conformi if s not in scelte]
        if estranee:
            raise OperazioneNonConsentita(f"Scelte non conformi che non sono nell'elenco: {', '.join(estranee)}.")
        c.scelte, c.scelte_non_conformi = "\n".join(scelte), "\n".join(non_conformi)
    atteso = str(dati.get("atteso", ""))
    c.atteso_si = {"si": True, "no": False}.get(atteso) if tipo == TipoCampo.SI_NO else None
    if not c.pk:
        c.ordine = (modello.campi.aggregate(m=Max("ordine"))["m"] or 0) + 10
    c.attivo = bool(dati.get("attivo", True))
    c.save()
    return c


@transaction.atomic
def sposta_campo(utente: Any, campo_id: int, verso: int) -> None:
    _richiedi(utente, F.TRACCIATURA_CONFIGURA)
    c = Campo.objects.get(pk=campo_id)
    campi = list(c.modello.campi.order_by("ordine", "id"))
    i = campi.index(c)
    j = i + (1 if verso > 0 else -1)
    if 0 <= j < len(campi):
        campi[i], campi[j] = campi[j], campi[i]
        for n, x in enumerate(campi):
            Campo.objects.filter(pk=x.pk).update(ordine=(n + 1) * 10)


def elimina_campo(utente: Any, campo_id: int) -> str:
    """Un campo mai usato si elimina; uno già usato si disattiva (i valori registrati restano)."""
    _richiedi(utente, F.TRACCIATURA_CONFIGURA)
    c = Campo.objects.get(pk=campo_id)
    if c.valori.exists():
        c.attivo = False
        c.save(update_fields=["attivo"])
        return "disattivato"
    c.delete()
    return "eliminato"


# -------------------------------------------------------------------------------------------- registrazione
def _risolvi_ddt(testo: str) -> Any:
    from metodo.consegne.models import DDT

    t = (testo or "").strip()
    if not t:
        return None
    numero, _, anno = t.partition("/")
    qs = DDT.objects.filter(numero__iexact=numero.strip(), annullato_il__isnull=True)
    if anno.strip().isdigit():
        qs = qs.filter(anno=int(anno))
    trovati = list(qs.order_by("-anno")[:2])
    if not trovati:
        raise OperazioneNonConsentita(f"DDT «{t}» non trovato (scrivi numero/anno, es. 12/2026).")
    if len(trovati) > 1 and not anno.strip():
        raise OperazioneNonConsentita(
            f"Più DDT con numero {numero}: indica anche l'anno (es. {numero}/{trovati[0].anno})."
        )
    return trovati[0]


def _controlla_codice(tipo: str, codice: str) -> str:
    codice = (codice or "").strip()[:80]
    if not codice:
        return ""
    if tipo == Collegamento.ARTICOLO:
        from metodo.anagrafica.models import Articolo

        if not Articolo.objects.filter(codice__iexact=codice).exists():
            raise OperazioneNonConsentita(f"Il codice finito «{codice}» non è nell'anagrafica degli articoli.")
    else:
        from metodo.magazzino.models import Componente

        if not Componente.objects.filter(codice__iexact=codice).exists():
            raise OperazioneNonConsentita(f"Il codice articolo «{codice}» non è nell'anagrafica dei componenti.")
    return codice


def _valore(campo: Campo, grezzo: Any, file: UploadedFile | None) -> dict[str, Any] | None:
    """Converte e controlla il valore inviato; None se vuoto. Restituisce i campi del Valore (con conforme)."""
    t = campo.tipo
    if t in TIPI_FILE:
        if not file:
            return None
        from metodo.tracciabilita.servizi import valida_file

        tipo_mime, _ = valida_file(file)
        if t == TipoCampo.FOTO and not tipo_mime.startswith("image/"):
            raise OperazioneNonConsentita(f"{campo.etichetta}: serve una fotografia (JPEG, PNG, GIF o WebP).")
        return {"file": file, "nome_file": (file.name or "file")[-200:], "tipo_file": tipo_mime}
    testo = str(grezzo if grezzo is not None else "").strip()
    if not testo:
        return None
    v: dict[str, Any] = {}
    if t in (TipoCampo.TESTO, TipoCampo.TESTO_LUNGO):
        v["testo"] = testo[:200] if t == TipoCampo.TESTO else testo[:10000]
    elif t == TipoCampo.INTERO:
        try:
            v["intero"] = int(testo.replace(".", "").replace(" ", ""))
        except ValueError as exc:
            raise OperazioneNonConsentita(f"{campo.etichetta}: serve un numero intero.") from exc
    elif t == TipoCampo.DECIMALE:
        v["decimale"] = _decimale(testo, campo.etichetta)
    elif t in (TipoCampo.SI_NO, TipoCampo.ESITO):
        if testo not in ("1", "0"):
            raise OperazioneNonConsentita(f"{campo.etichetta}: valore non valido.")
        v["booleano"] = testo == "1"
    elif t == TipoCampo.SCELTA:
        if testo not in campo.elenco_scelte:
            raise OperazioneNonConsentita(f"{campo.etichetta}: scelta non valida.")
        v["testo"] = testo
    elif t == TipoCampo.DATA:
        try:
            v["data"] = dt.date.fromisoformat(testo)
        except ValueError as exc:
            raise OperazioneNonConsentita(f"{campo.etichetta}: data non valida.") from exc
    elif t == TipoCampo.DATA_ORA:
        try:
            v["data_ora"] = timezone.make_aware(dt.datetime.fromisoformat(testo))
        except ValueError as exc:
            raise OperazioneNonConsentita(f"{campo.etichetta}: data e ora non valide.") from exc
    v["conforme"] = conformita(campo, v)
    return v


def conformita(campo: Campo, v: Mapping[str, Any]) -> bool | None:
    """True/False se il campo ha un controllo, None altrimenti."""
    if not campo.ha_controllo:
        return None
    t = campo.tipo
    if t == TipoCampo.ESITO:
        return bool(v.get("booleano"))
    if t == TipoCampo.SI_NO:
        return v.get("booleano") == campo.atteso_si
    if t == TipoCampo.SCELTA:
        return v.get("testo") not in campo.elenco_non_conformi
    numero = v.get("intero") if t == TipoCampo.INTERO else v.get("decimale")
    if numero is None:
        return None
    return (campo.minimo is None or numero >= campo.minimo) and (campo.massimo is None or numero <= campo.massimo)


@transaction.atomic
def registra(
    utente: Any,
    modello_id: int,
    *,
    collegamenti: Mapping[str, Any],
    valori: Mapping[str, Any],
    files: Mapping[str, UploadedFile] | None = None,
    note: str = "",
) -> Tracciatura:
    """Registra una tracciatura. ``valori`` e ``files`` hanno chiave ``campo_<id>``; ``collegamenti`` ha
    ``ordine`` (Ordine o pk), ``ddt`` (numero/anno), ``componente``, ``articolo``, ``lotto``, ``riferimento``."""
    _richiedi(utente, F.TRACCIATURA_REGISTRA)
    modello = Modello.objects.get(pk=modello_id)
    if not modello.attivo:
        raise OperazioneNonConsentita("Il modello non è attivo.")
    files = files or {}
    errori: list[str] = []
    t = Tracciatura(modello=modello, momento=timezone.now(), operatore=utente, note=(note or "").strip()[:4000])

    # ---- collegamenti
    for c in Collegamento:
        uso, grezzo = modello.uso(c.value), collegamenti.get(c.value)
        try:
            if c == Collegamento.ORDINE:
                valore = (
                    grezzo if isinstance(grezzo, Ordine) or grezzo is None else Ordine.objects.filter(pk=grezzo).first()
                )
                t.ordine = valore if uso else None
            elif c == Collegamento.DDT:
                t.ddt = _risolvi_ddt(str(grezzo or "")) if uso else None
            elif c in (Collegamento.COMPONENTE, Collegamento.ARTICOLO):
                setattr(t, c.value, _controlla_codice(c.value, str(grezzo or "")) if uso else "")
            else:
                setattr(t, c.value, str(grezzo or "").strip()[:120] if uso else "")
        except OperazioneNonConsentita as exc:
            errori.append(str(exc))
            continue
        presente = getattr(t, c.value) not in (None, "")
        if uso == "O" and not presente:
            errori.append(f"{c.label}: obbligatorio per «{modello}».")
    codici = [t.componente, t.articolo]
    if t.ordine is not None and not any(codici):
        codici = list(t.ordine.righe.values_list("articolo", flat=True))
    if schemi(modello) and any(codici) and not si_applica(modello, codici):
        errori.append(f"«{modello}» non è previsto per i codici indicati ({', '.join(c for c in codici if c)[:120]}).")

    # ---- valori
    da_salvare: list[tuple[Campo, dict[str, Any]]] = []
    for campo in modello.campi.filter(attivo=True).order_by("ordine", "id"):
        chiave = f"campo_{campo.pk}"
        try:
            v = _valore(campo, valori.get(chiave), files.get(chiave))
        except OperazioneNonConsentita as exc:
            errori.append(str(exc))
            continue
        if v is None:
            if campo.obbligatorio:
                errori.append(f"{campo.etichetta}: obbligatorio.")
            continue
        da_salvare.append((campo, v))
    if errori:
        raise OperazioneNonConsentita(" ".join(errori))

    esiti = [v["conforme"] for _, v in da_salvare if v.get("conforme") is not None]
    t.esito = Esito.NON_CONFORME if False in esiti else Esito.CONFORME if esiti else Esito.SENZA
    t.save()
    for campo, v in da_salvare:
        file = v.pop("file", None)
        valore = Valore(tracciatura=t, campo=campo, **v)
        if file is not None:
            valore.file.save(file.name or "file", file, save=False)
        valore.save()
    return t


@transaction.atomic
def annulla(utente: Any, tracciatura_id: int, motivo: str) -> Tracciatura:
    """Le tracciature non si cancellano: si annullano con il motivo (chi le ha registrate o chi configura)."""
    t = Tracciatura.objects.select_for_update().get(pk=tracciatura_id)
    if not (
        ha_permesso(utente, F.TRACCIATURA_CONFIGURA)
        or (t.operatore_id == utente.pk and ha_permesso(utente, F.TRACCIATURA_REGISTRA))
    ):
        raise OperazioneNonConsentita(
            "Annulla le tue tracciature; quelle degli altri le annulla chi configura i modelli."
        )
    if t.annullata:
        raise OperazioneNonConsentita("La tracciatura è già annullata.")
    motivo = (motivo or "").strip()
    if not motivo:
        raise OperazioneNonConsentita("Indica il motivo dell'annullamento.")
    t.annullata_il, t.annullata_da, t.motivo_annullamento = timezone.now(), utente, motivo[:200]
    t.save(update_fields=["annullata_il", "annullata_da", "motivo_annullamento"])
    return t


# --------------------------------------------------------------------------------------------------- ricerca
def cerca(filtri: Mapping[str, str]) -> QuerySet[Tracciatura]:
    qs = Tracciatura.objects.select_related("modello", "operatore", "ordine", "ddt")
    f = {k: (v or "").strip() for k, v in filtri.items()}
    if f.get("modello", "").isdigit():
        qs = qs.filter(modello_id=int(f["modello"]))
    if f.get("categoria") in Categoria.values:
        qs = qs.filter(modello__categoria=f["categoria"])
    if f.get("ordine", "").isdigit():
        qs = qs.filter(ordine_id=int(f["ordine"]))
    if f.get("ddt", "").isdigit():
        qs = qs.filter(ddt_id=int(f["ddt"]))
    if f.get("codice"):
        c = f["codice"]
        qs = qs.filter(Q(componente__icontains=c) | Q(articolo__icontains=c))
    for campo in ("lotto", "riferimento"):
        if f.get(campo):
            qs = qs.filter(**{f"{campo}__icontains": f[campo]})
    if f.get("esito") in ("OK", "KO", "NA"):
        qs = qs.filter(esito="" if f["esito"] == "NA" else f["esito"])
    for chiave, op in (("dal", "gte"), ("al", "lte")):
        with contextlib.suppress(KeyError, ValueError):
            qs = qs.filter(**{f"momento__date__{op}": dt.date.fromisoformat(f[chiave])})
    if f.get("annullate") != "1":
        qs = qs.filter(annullata_il__isnull=True)
    return qs.order_by("-momento", "-id")
