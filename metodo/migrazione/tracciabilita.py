"""Importazione da Metodo di tracciabilità e qualità (passo dell'``Importatore``).

- ``Tracciabilita`` -> tracciature del modello «Tracce (da Metodo)»: evento, valore e reparto come campi,
  il codice dell'elemento (``Plan.Coding`` via ``Stored_Item``) come riferimento, l'ordine collegato;
- batterie (eventi IN/RC), cavi (eventi dei banchi o codici col prefisso ``ITEM_CODE_CAVI``) e
  ``Plan.Asset`` (postazioni) NON vengono importati: funzioni eliminate da Metodo v2;
- ``Test`` (con i suoi ``Check``) -> **modello di tracciatura** di categoria Controllo qualità: ogni check
  a campione diventa i campi «campione» e «KO», i Check 8 e 9 (distinta e disegno, usati dal codice di
  Metodo) campi Sì/No; vedi ``metodo.tracciatura.da_qualita``;
- ``ControlloQlta`` (una riga per check, senza test) -> **tracciatura**, raggruppando le righe della stessa
  registrazione; il modello è quello del test che contiene tutti i controlli registrati;
- ``FileOrdini`` -> tracciature del modello «Foto e file dell'ordine». Gli ordini scritti come testo vengono
  cercati per chiave; i file restano dove sono: va copiata la cartella ``file/`` della cartella media di
  Metodo in MEDIA_ROOT.
- ``Reclami`` e ``DisplayTest`` non vengono importati: funzioni non più usate.
- ``WorkOrderTraccia`` non viene importato (work order delle batterie).
"""

from __future__ import annotations

import datetime as dt
import mimetypes
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from django.contrib.auth.models import User
from django.utils import timezone

from metodo.ordini.models import Ordine
from metodo.tracciabilita import servizi
from metodo.tracciatura import da_qualita as dq
from metodo.tracciatura.models import Campo, Modello, Tracciatura, Valore

if TYPE_CHECKING:
    from .importatore import Importatore

# Chiavi primarie dei Check che il codice di Metodo usava per distinta e disegno (traccia.py:104,111).
CHECK_METODO_PRESENZA = {8: "Distinta", 9: "Disegno"}
MODELLI = dq.Modelli(Modello=Modello, Campo=Campo, Tracciatura=Tracciatura, Valore=Valore, User=User)

CAMPI_DATA_ORIGINALE: tuple[tuple[Any, str], ...] = ()  # le tracciature hanno già la data dell'evento


def _t(v: Any) -> str:
    return "" if v is None else str(v).strip()


def _aware(v: Any) -> dt.datetime | None:
    if isinstance(v, dt.datetime):
        return timezone.make_aware(v) if timezone.is_naive(v) else v
    if isinstance(v, dt.date):
        return timezone.make_aware(dt.datetime.combine(v, dt.time.min))
    return None


def _intero(v: Any) -> int | None:
    try:
        d = Decimal(_t(v).replace(",", "."))
    except InvalidOperation:
        return None
    if not d.is_finite() or d < 0 or d != d.to_integral_value():
        return None
    return int(d)


def _decimale(v: Any) -> Decimal | None:
    try:
        d = Decimal(_t(v).replace(",", "."))
    except InvalidOperation:
        return None
    return d.quantize(Decimal("0.001")) if d.is_finite() and d >= 0 else None


class ImportaTracciabilita:
    def __init__(self, imp: Importatore) -> None:
        self.imp, self.m, self.r = imp, imp.m, imp.r
        self.controlli: dict[int, dq.Controllo] = {}  # id Check di Metodo -> controllo
        self.test: list[tuple[set[int], dq.ModelloQualita]] = []  # (check del test, modello di tracciatura)
        self.generico: dq.ModelloQualita | None = None
        self.sistema: Any = None

    def esegui(self) -> None:
        from .importatore import _date_originali

        with _date_originali(*CAMPI_DATA_ORIGINALE):
            self._tracce()
            self._test()
            self._controlli_qualita()
            self._file()

    # ------------------------------------------------------------------ ordini scritti come testo
    def _ordine_da_testo(self, testo: str, quando: dt.date | None) -> Ordine | None:
        testo = testo.strip()
        if not testo:
            return None
        if servizi.FORMATO_RE.match(testo):
            try:
                return servizi.risolvi_ordine(testo)
            except servizi.OperazioneNonConsentita:
                return None
        anni = [quando.year, quando.year - 1] if quando else []
        m = re.fullmatch(r"(\d{1,5})\.(\d{1,10})", testo)
        if m:  # "SERIE.NUMERO" (come lo scrivevano gli operatori): anno dalla data
            for anno in anni:
                o = servizi.cerca_ordine(m[1], m[2], "", anno)
                if o:
                    return o
            return None
        if testo.isdigit() and anni:  # solo numero: accettato solo se non è ambiguo
            trovati = list(Ordine.objects.filter(numero__in={testo, testo.zfill(7)}, appendice="", anno__in=anni)[:2])
            return trovati[0] if len(trovati) == 1 else None
        return None

    # ------------------------------------------------------------------ elementi e tracce
    def _tracce(self) -> None:
        prefisso_cavi = ""
        for c in self.m.righe("Prod_configuration"):
            if _t(c["chiave"]) == "ITEM_CODE_CAVI":
                prefisso_cavi = _t(c["valore"])
        codifiche = {c["id"]: c for c in self.m.righe("Plan_coding")}
        item_codifica = {s["id"]: s.get("codice_id") for s in self.m.righe("Prod_stored_item")}
        tracce = list(self.m.righe("Prod_tracciabilita"))
        if not tracce:
            return
        # Tipo di ogni codice, dedotto dagli eventi: batterie e cavi non vengono importati.
        eventi: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for t in tracce:
            cod = item_codifica.get(t.get("stored_item_id"))
            if cod in codifiche:
                eventi[cod].append(t)
        codici: dict[int, str] = {}
        scartati: set[int] = set()
        for cod, lista in eventi.items():
            codice = _t(codifiche[cod]["internalCode"])[:50]
            if not codice:
                self.r.avviso("tracce", f"codifica id {cod} senza codice: eventi tenuti solo se hanno un ordine")
                continue
            batteria = any(_t(t["chiave"]).upper() in ("IN", "RC") for t in lista)
            cavo = any(t.get("asset_id") for t in lista) or bool(prefisso_cavi and codice.startswith(prefisso_cavi))
            if batteria or cavo:
                scartati.add(cod)
                self.r.conteggi["batterie e cavi (non importati)"] += 1
                continue
            codici[cod] = codice
        self.r.conteggi["elementi tracciati"] += len(codici)

        nuove: list[dict[str, Any]] = []
        for t in tracce:
            if t.get("asset_id") or item_codifica.get(t.get("stored_item_id")) in scartati:
                self.r.conteggi["tracce di batterie, cavi e banchi (non importate)"] += 1
                continue
            codice = codici.get(item_codifica.get(t.get("stored_item_id")))  # type: ignore[arg-type]
            ordine = self.imp.ordini_per_id.get(t.get("rif_ordine_id"))
            if codice is None and ordine is None:
                self.r.conteggi["tracce senza elemento né ordine (ignorate)"] += 1
                continue
            reparto = self.imp.reparti.get(t.get("location_id"))
            nuove.append(
                {
                    "momento": _aware(t.get("data")) or _aware(t.get("data_inserimento")) or timezone.now(),
                    "operatore_id": self._operatore().pk,
                    "ordine_id": ordine.pk if ordine else None,
                    "codice": codice or "",
                    "chiave": _t(t["chiave"])[:50] or "?",
                    "valore": _t(t.get("valore"))[:100],
                    "reparto": reparto.nome if reparto else "",
                }
            )
        if nuove:
            modello, campi = dq.modello_tracce(MODELLI)
            self.r.conteggi["tracce"] += dq.tracce_in_blocco(MODELLI, modello, campi, nuove)

    # ------------------------------------------------------------------ qualità
    def _test(self) -> None:
        for c in self.m.righe("Prod_check"):
            if c["id"] in CHECK_METODO_PRESENZA:
                self.controlli[c["id"]] = dq.Controllo(c["id"], CHECK_METODO_PRESENZA[c["id"]], presenza=True)
            else:
                nome = _t(c["nome"])[:60] or f"Controllo {c['id']}"
                self.controlli[c["id"]] = dq.Controllo(c["id"], nome)
        legami: dict[int, set[int]] = defaultdict(set)
        for tc in self.m.righe("Prod_test_checks"):
            legami[tc["test_id"]].add(tc["check_id"])
        for t in self.m.righe("Prod_test"):
            check = {c for c in legami.get(t["id"], set()) if c in self.controlli}
            for flag, id_presenza in (("checkDistinta", 8), ("checkDisegno", 9)):
                if t.get(flag) and id_presenza in self.controlli:
                    check.add(id_presenza)
            ordinati = sorted((self.controlli[c] for c in check), key=lambda c: (c.presenza, c.nome))
            mq = dq.crea_modello(
                MODELLI, _t(t["nome"]) or f"Test {t['id']}", ordinati, descrizione=_t(t.get("descrizione"))
            )
            self.test.append((check, mq))
            self.r.conteggi["modelli di controllo qualità"] += 1

    def _modello_per(self, check: set[int]) -> dq.ModelloQualita:
        """Il test più piccolo che contiene tutti i controlli registrati; altrimenti un modello generico."""
        adatti = [(len(c), mq.modello.pk, mq) for c, mq in self.test if check <= c]
        if adatti:
            return min(adatti, key=lambda x: (x[0], x[1]))[2]
        if self.generico is None:
            self.generico = dq.crea_modello(
                MODELLI,
                "Controllo qualità importato da Metodo",
                [],
                descrizione="Controlli di Metodo che non corrispondono a nessun test.",
                attivo=False,
            )
            self.r.avviso(
                "controlli qualità", "alcune registrazioni non corrispondono a un test: modello generico (non attivo)"
            )
        for c in sorted(check):
            dq.aggiungi_controllo(MODELLI, self.generico, self.controlli[c])
        return self.generico

    def _operatore(self) -> Any:
        self.sistema = self.sistema or dq.utente_sistema(MODELLI)
        return self.sistema

    def _controlli_qualita(self) -> None:
        # In Metodo ogni check era una riga a sé, senza riferimento al test: si raggruppano le righe
        # della stessa registrazione (stesso ordine, prodotto e data).
        gruppi: dict[tuple[str, str, Any], list[dict[str, Any]]] = defaultdict(list)
        for r in self.m.righe("Prod_controlloqlta"):
            gruppi[(_t(r.get("ordine"))[:50], _t(r["codiceProdotto"])[:80], r["data_inserimento"])].append(r)
        for (testo_ordine, articolo, quando), righe in gruppi.items():
            data = _aware(quando)
            giorno = timezone.localtime(data).date() if data else dt.date(2000, 1, 1)
            esiti: dict[int, tuple[int, int] | bool] = {}
            for r in righe:
                controllo = self.controlli.get(r.get("check_rif_id"))
                if controllo is None or controllo.chiave in esiti:
                    self.r.avviso("controlli qualità", f"riga id {r['id']}: controllo mancante o ripetuto, saltata")
                    continue
                if controllo.presenza:
                    esiti[controllo.chiave] = _t(r["ko"]).lower() == "presente"
                    continue
                campione, ko = _intero(r["campione"]), _intero(r["ko"])
                if campione is None or ko is None or ko > campione:
                    self.r.avviso(
                        "controlli qualità",
                        f"riga id {r['id']}: campione {r['campione']!r} / KO {r['ko']!r} non validi",
                    )
                    continue
                esiti[controllo.chiave] = (campione, ko)
            if not esiti:
                continue
            ordine = self._ordine_da_testo(testo_ordine, giorno)
            qta = next((_decimale(r.get("qtaInOrdine")) for r in righe if _t(r.get("qtaInOrdine"))), None)
            dq.registra(
                MODELLI,
                self._modello_per(set(esiti)),
                esiti,
                operatore=self._operatore(),
                momento=data or timezone.now(),
                ordine=ordine,
                articolo=articolo or "?",
                riferimento="" if ordine else (testo_ordine or "?"),
                qta=qta,
                note="Importato da Metodo",
            )
            self.r.conteggi["controlli qualità registrati"] += 1
            if testo_ordine and ordine is None:
                self.r.conteggi["controlli qualità con ordine non trovato (tenuto come riferimento)"] += 1

    # ------------------------------------------------------------------ file
    def _file(self) -> None:
        modello = campo = None
        for f in self.m.righe("Prod_fileordini"):
            anno = _intero(f.get("anno"))
            ordine = None
            if anno:
                ordine = servizi.cerca_ordine(_t(f.get("serie")), _t(f["rif_ordine"]), _t(f.get("appendice")), anno)
            nome = _t(f["file"])[:255]
            if ordine is None or not nome:
                self.r.avviso("file ordini", f"file id {f['id']} ({nome}): ordine non trovato, saltato")
                continue
            if modello is None:
                modello, campo = dq.modello_file(MODELLI)
            dq.registra_file(
                MODELLI,
                modello,
                campo,
                ordine=ordine,
                percorso=nome,
                nome=nome.rsplit("/", 1)[-1],
                tipo=mimetypes.guess_type(nome)[0] or "application/octet-stream",
                momento=_aware(f.get("data_inserimento")) or timezone.now(),
                operatore=self._operatore(),
            )
            self.r.conteggi["file ordini (da copiare dalla cartella media di Metodo)"] += 1
