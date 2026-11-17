"""Importazione dei dati di Metodo in DuMetodo.

Regole principali (vedi docs/analisi-metodo/01-data-model.md):
- appendice "0", vuota o NULL -> "" ; anno a 2 cifre -> 20xx; chiave d'ordine UNICA:
  i duplicati di Metodo vengono fusi (resta il record più recente non annullato);
- le righe vengono collegate all'ordine tramite la chiave e diventano ForeignKey;
- prezzi testuali ("0,2596") -> Decimal; valori illeggibili -> vuoti, con avviso;
- "annullata" / "duplicata" -> stato riga ANNULLATA / SOSTITUITA;
- note: una per ogni ordine a cui erano collegate; segnate come già lette da tutti;
- DocumentiTrasporto -> righe consegnate, raggruppate in DDT (numero+anno) se inviate;
- utenti con la stessa password (hash Django compatibile) e i gruppi rinominati.
Nulla viene scritto su Metodo.
"""

from __future__ import annotations

import datetime as dt
import re
from collections import Counter, defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from django.contrib.auth.models import Group, User
from django.utils import timezone

from dumetodo.anagrafica.models import Articolo, Tabulato, UnitaMisura
from dumetodo.comunicazioni.models import Lato, LetturaNota, Nota, Visibilita
from dumetodo.consegne.models import DDT, Consegna, RigaConsegnata
from dumetodo.core.models import Reparto
from dumetodo.core.ruoli import GRUPPI_METODO, Ruolo
from dumetodo.ordini.models import (
    Ordine,
    OrigineModificaScadenza,
    OrigineRiga,
    RigaOrdine,
    StatoOrdine,
    StatoRiga,
    StoricoScadenza,
    TipoOrdine,
    Urgenza,
)

from .sorgente import Metodo

Chiave = tuple[str, str, str, int]
STATI_LEGACY = {"Lav": StatoOrdine.IN_LAVORAZIONE, "BLC": StatoOrdine.INSERITO}


@dataclass
class Report:
    conteggi: Counter[str] = field(default_factory=Counter)
    avvisi: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))

    def avviso(self, categoria: str, testo: str) -> None:
        self.avvisi[categoria].append(testo)


@contextmanager
def _date_originali(*modelli_campi: tuple[type, str]) -> Iterator[None]:
    """Disattiva temporaneamente auto_now_add per conservare le date originali di Metodo."""
    campi = [m._meta.get_field(c) for m, c in modelli_campi]
    salvati = [(f, f.auto_now_add) for f in campi]
    for f in campi:
        f.auto_now_add = False
    try:
        yield
    finally:
        for f, valore in salvati:
            f.auto_now_add = valore


def _testo(v: Any) -> str:
    return "" if v is None else str(v).strip()


def _appendice(v: Any) -> str:
    a = _testo(v).upper()
    return "" if a in ("", "0", "NONE", "NULL", "-") else a


def _anno(v: Any) -> int | None:
    t = _testo(v)
    if re.fullmatch(r"\d{4}", t) and 2000 <= int(t) <= 2100:
        return int(t)
    if re.fullmatch(r"\d{2}", t):
        return 2000 + int(t)
    return None


def _decimale(v: Any) -> Decimal | None:
    t = _testo(v).replace(" ", "")
    if not t:
        return None
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    try:
        d = Decimal(t)
    except InvalidOperation:
        return None
    return d if d.is_finite() else None


def _aware(v: Any) -> Any:
    if isinstance(v, dt.datetime) and timezone.is_naive(v):
        return timezone.make_aware(v)
    return v


class Importatore:
    def __init__(self, metodo: Metodo) -> None:
        self.m = metodo
        self.r = Report()
        self.utenti: dict[int, User] = {}
        self.clienti: set[int] = set()
        self.reparti: dict[int, Reparto] = {}
        self.ordini_per_id: dict[int, Ordine] = {}
        self.ordini_per_chiave: dict[Chiave, Ordine] = {}
        self.righe_per_id: dict[int, RigaOrdine] = {}

    def esegui(self) -> Report:
        for a in self.m.verifica_schema():
            self.r.avviso("schema", a)
        with _date_originali(
            (Ordine, "creato_il"),
            (RigaOrdine, "creato_il"),
            (StoricoScadenza, "creato_il"),
            (Consegna, "creata_il"),
            (DDT, "creato_il"),
        ):
            self._utenti()
            self._reparti()
            self._anagrafica()
            self._ordini()
            self._righe()
            self._note()
            self._storico()
            self._documenti_trasporto()
        self._magazzino()
        return self.r

    # ------------------------------------------------------------------ utenti e gruppi
    def _utenti(self) -> None:
        gruppi = {g["id"]: g["name"] for g in self.m.righe("auth_group")}
        gruppi_utente: dict[int, set[str]] = defaultdict(set)
        for ug in self.m.righe("auth_user_groups", ordine="user_id"):
            gruppi_utente[ug["user_id"]].add(gruppi.get(ug["group_id"], ""))
        ruoli = {r: Group.objects.get_or_create(name=r.value)[0] for r in Ruolo}
        for u in self.m.righe("auth_user"):
            utente, creato = User.objects.get_or_create(
                username=u["username"],
                defaults={
                    "password": u["password"],
                    "first_name": _testo(u.get("first_name"))[:150],
                    "last_name": _testo(u.get("last_name"))[:150],
                    "email": _testo(u.get("email")),
                    "is_active": u["is_active"],
                    "is_superuser": u["is_superuser"],
                    "is_staff": bool(u.get("is_staff")),
                    "date_joined": u.get("date_joined") or timezone.now(),
                    "last_login": u.get("last_login"),
                },
            )
            if not creato:
                self.r.avviso("utenti", f"{u['username']}: già presente in DuMetodo, non modificato")
            self.utenti[u["id"]] = utente
            for nome in gruppi_utente.get(u["id"], set()):
                if nome in GRUPPI_METODO:
                    utente.groups.add(ruoli[GRUPPI_METODO[nome]])
                    if GRUPPI_METODO[nome] == Ruolo.CLIENTE:
                        self.clienti.add(u["id"])
                elif nome:
                    self.r.avviso("utenti", f"{u['username']}: gruppo '{nome}' non usato in DuMetodo")
            self.r.conteggi["utenti"] += 1

    # ------------------------------------------------------------------ reparti
    def _reparti(self) -> None:
        percorsi = {p["id"]: _testo(p["code"]) for p in self.m.righe("Plan_locpath")}
        nomi_usati: Counter[str] = Counter()
        for loc in self.m.righe("Plan_location"):
            codice = percorsi.get(loc.get("path_id")) or f"LOC-{loc['id']}"
            nome = _testo(loc["area"]) or codice
            nomi_usati[nome] += 1
            if nomi_usati[nome] > 1:
                nome = f"{nome} ({codice})"
            reparto, _ = Reparto.objects.get_or_create(codice=codice[:30], defaults={"nome": nome[:80]})
            self.reparti[loc["id"]] = reparto
            self.r.conteggi["reparti"] += 1

    # ------------------------------------------------------------------ anagrafica articoli
    def _anagrafica(self) -> None:
        per_codice: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for a in self.m.righe("Prod_anagrafica"):
            codice = _testo(a.get("articolo"))[:50]
            if codice:
                per_codice[codice].append(a)
        for codice, gruppo in per_codice.items():
            # Doppioni: si tiene il record più completo (in Metodo un job ne cancellava uno a caso).
            gruppo.sort(key=lambda a: (bool(_testo(a.get("description"))), a.get("location_id") is not None, a["id"]))
            a = gruppo[-1]
            if len(gruppo) > 1:
                self.r.avviso("anagrafica", f"{codice}: {len(gruppo)} record in Metodo, tenuto id {a['id']}")
            try:
                tabulato = int(_testo(a.get("tabulato")) or 0)
            except ValueError:
                tabulato = 0
            Articolo.objects.create(
                codice=codice,
                descrizione=_testo(a.get("description")),
                reparto=self.reparti.get(a.get("location_id")),
                um=a.get("um") if a.get("um") in UnitaMisura.values else UnitaMisura.PEZZI,
                prezzo_riferimento=_decimale(a.get("prezzo")),
                prezzo_ricalcolato=_decimale(a.get("prezzo_ricalcolato")),
                data_prezzo=a.get("data_prezzo"),
                tabulato=tabulato if tabulato in Tabulato.values else 0,
                nuovo=bool(a.get("nuovo_articolo")) if a.get("nuovo_articolo") is not None else True,
            )
            self.r.conteggi["articoli"] += 1

    # ------------------------------------------------------------------ ordini
    def _chiave(self, riga: dict[str, Any], categoria: str, rif: str) -> Chiave | None:
        anno = _anno(riga.get("anno"))
        numero, serie = _testo(riga.get("rif_ordine")), _testo(riga.get("serie"))
        if anno is None or not numero or not serie:
            self.r.avviso(
                categoria,
                f"{rif}: chiave non valida (serie={serie!r} numero={numero!r} anno={riga.get('anno')!r}), scartato",
            )
            return None
        if _testo(riga.get("anno")) != str(anno):
            self.r.avviso(categoria, f"{rif}: anno {riga.get('anno')!r} interpretato come {anno}")
        return (serie[:5], numero[:30], _appendice(riga.get("appendice"))[:10], anno)

    def _ordini(self) -> None:
        candidati: dict[Chiave, list[dict[str, Any]]] = defaultdict(list)
        for o in self.m.righe("Prod_ordine"):
            chiave = self._chiave(o, "ordini", f"ordine id {o['id']}")
            if chiave:
                candidati[chiave].append(o)
        for chiave, gruppo in candidati.items():
            # Duplicati: vince il più recente non annullato.
            gruppo.sort(
                key=lambda o: (
                    o["stato"] != StatoOrdine.ANNULLATO,
                    o.get("data_inserimento") or dt.datetime.min.replace(tzinfo=dt.UTC),
                    o["id"],
                )
            )
            scelto = gruppo[-1]
            if len(gruppo) > 1:
                self.r.avviso(
                    "ordini duplicati",
                    f"{chiave}: {len(gruppo)} record in Metodo (id "
                    f"{', '.join(str(g['id']) for g in gruppo)}), tenuto id {scelto['id']}",
                )
            stato = STATI_LEGACY.get(scelto["stato"], scelto["stato"])
            if stato not in StatoOrdine.values:
                self.r.avviso("ordini", f"{chiave}: stato {scelto['stato']!r} sconosciuto -> Inserito")
                stato = StatoOrdine.INSERITO
            ordine = Ordine.objects.create(
                serie=chiave[0],
                numero=chiave[1],
                appendice=chiave[2],
                anno=chiave[3],
                stato=stato,
                urgenza=scelto.get("urgenza") if scelto.get("urgenza") in Urgenza.values else Urgenza.NORMALE,
                tipo=scelto.get("tipo") if scelto.get("tipo") in TipoOrdine.values else TipoOrdine.ASSEMBLAGGIO,
                reparto=self.reparti.get(scelto.get("location_id")),
                data_scadenza=scelto.get("data_scadenza"),
                data_consegna=scelto.get("data_consegna"),
                creato_il=_aware(scelto.get("data_inserimento")) or timezone.now(),
            )
            self.ordini_per_chiave[chiave] = ordine
            for g in gruppo:
                self.ordini_per_id[g["id"]] = ordine
            self.r.conteggi["ordini"] += 1

    # ------------------------------------------------------------------ righe
    def _ordine_per_riga(self, chiave: Chiave, riga: dict[str, Any]) -> Ordine:
        ordine = self.ordini_per_chiave.get(chiave)
        if ordine is None:
            stato = StatoOrdine.CONSEGNATO if riga.get("emessa_bolla") else StatoOrdine.INSERITO
            ordine = Ordine.objects.create(
                serie=chiave[0],
                numero=chiave[1],
                appendice=chiave[2],
                anno=chiave[3],
                stato=stato,
                data_scadenza=riga.get("data_scadenza"),
                creato_il=_aware(riga.get("data_inserimento")) or timezone.now(),
            )
            self.ordini_per_chiave[chiave] = ordine
            self.r.avviso("righe senza ordine", f"{chiave}: ordine mancante in Metodo, creato ({stato})")
            self.r.conteggi["ordini creati da righe"] += 1
        return ordine

    def _righe(self) -> None:
        attive: dict[tuple[int, str], RigaOrdine] = {}
        for ln in self.m.righe("Prod_linea"):
            chiave = self._chiave(ln, "righe", f"linea id {ln['id']}")
            if chiave is None:
                continue
            ordine = self._ordine_per_riga(chiave, ln)
            qta = _decimale(ln.get("qta"))
            if qta is None or qta < 0:
                self.r.avviso("righe", f"linea id {ln['id']}: quantità {ln.get('qta')!r} non valida -> 0")
                qta = Decimal(0)
            prezzo = _decimale(ln.get("prezzo_ea"))
            if _testo(ln.get("prezzo_ea")) and (prezzo is None or prezzo < 0):
                self.r.avviso("prezzi", f"linea id {ln['id']}: prezzo {ln.get('prezzo_ea')!r} illeggibile -> vuoto")
                prezzo = None
            sconto = _decimale(ln.get("sconto_uni"))
            n_riga = _testo(ln.get("n_riga"))[:30] or f"M{ln['id']}"
            stato = (
                StatoRiga.ANNULLATA
                if ln.get("annullata")
                else StatoRiga.SOSTITUITA
                if ln.get("duplicata")
                else StatoRiga.ATTIVA
            )
            bolla = bool(ln.get("emessa_bolla"))
            bloccata = bool(ln.get("blocca_linea")) and not bolla
            in_evasione = bool(ln.get("evasione_parziale")) and not bolla and not bloccata
            if stato == StatoRiga.ATTIVA:
                precedente = attive.get((ordine.pk, n_riga))
                if precedente is not None:  # due righe attive con lo stesso numero: vince la più recente
                    precedente.stato = StatoRiga.SOSTITUITA
                    precedente.save(update_fields=["stato"])
                    self.r.avviso("righe", f"{ordine}: riga {n_riga} doppia, la precedente diventa 'sostituita'")
            riga = RigaOrdine.objects.create(
                ordine=ordine,
                n_riga=n_riga,
                articolo=_testo(ln.get("articolo"))[:50] or "?",
                variante=_testo(ln.get("variante"))[:20],
                qta=qta.quantize(Decimal("0.001")),
                prezzo_unitario=prezzo.quantize(Decimal("0.0001")) if prezzo is not None else None,
                sconto=sconto.quantize(Decimal("0.0001")) if sconto is not None and sconto >= 0 else None,
                cliente=_testo(ln.get("cliente"))[:30],
                magazzino=_testo(ln.get("magazzino"))[:20],
                data_scadenza=ln.get("data_scadenza"),
                data_consegna=ln.get("data_consegna"),
                stato=stato,
                bloccata=bloccata,
                in_evasione=in_evasione,
                bolla_emessa=bolla,
                scorporata=bool(ln.get("scorporato")),
                origine=OrigineRiga.DIVISIONE if n_riga.startswith("D") else OrigineRiga.IMPORTAZIONE,
                creato_il=_aware(ln.get("data_inserimento")) or timezone.now(),
            )
            if stato == StatoRiga.ATTIVA:
                attive[(ordine.pk, n_riga)] = riga
            self.righe_per_id[ln["id"]] = riga
            self.r.conteggi["righe"] += 1
        # Il cliente dell'ordine è il più frequente tra le sue righe.
        for ordine in self.ordini_per_chiave.values():
            c = Counter(ordine.righe.exclude(cliente="").values_list("cliente", flat=True)).most_common(1)
            if c:
                Ordine.objects.filter(pk=ordine.pk).update(cliente=c[0][0])

    # ------------------------------------------------------------------ note
    def _note(self) -> None:
        legami: dict[int, list[int]] = defaultdict(list)
        for on in self.m.righe("Prod_ordine_note", ordine="note_id"):
            legami[on["note_id"]].append(on["ordine_id"])
        nuove: list[Nota] = []
        for n in self.m.righe("Prod_note"):
            ordini = {self.ordini_per_id[o] for o in legami.get(n["id"], []) if o in self.ordini_per_id}
            if not ordini:
                self.r.conteggi["note senza ordine (ignorate)"] += 1
                continue
            autore = self.utenti.get(n["user_id"])
            testo = _testo(n.get("testo")) or "(nota vuota)"
            for ordine in ordini:
                nuove.append(
                    Nota(
                        ordine=ordine,
                        autore=autore,
                        autore_nome=(autore.get_full_name() or autore.get_username()) if autore else "utente rimosso",
                        lato=Lato.CLIENTE if n["user_id"] in self.clienti else Lato.FORNITORE,
                        visibilita=Visibilita.CONDIVISA,
                        testo=testo,
                        creata_il=_aware(n.get("data")) or timezone.now(),
                    )
                )
        Nota.objects.bulk_create(nuove, batch_size=1000)
        self.r.conteggi["note"] += len(nuove)
        # Le note storiche risultano già lette, altrimenti ogni utente avrebbe centinaia di "non lette".
        utenti = list(self.utenti.values())
        letture = [LetturaNota(nota=n, utente=u) for n in Nota.objects.all() for u in utenti]
        LetturaNota.objects.bulk_create(letture, batch_size=5000, ignore_conflicts=True)

    # ------------------------------------------------------------------ storico scadenze
    def _storico(self) -> None:
        for s in self.m.righe("Prod_storicoscadenza"):
            riga = self.righe_per_id.get(s.get("linea_id"))
            ordine = self.ordini_per_id.get(s.get("ordine_id")) or (riga.ordine if riga else None)
            if ordine is None:
                self.r.conteggi["storico scadenze senza ordine (ignorato)"] += 1
                continue
            StoricoScadenza.objects.create(
                ordine=ordine,
                riga=riga,
                precedente=s.get("data_scadenza_precedente"),
                nuova=s.get("data_scadenza_nuova"),
                origine=OrigineModificaScadenza.UTENTE,
                motivo=_testo(s.get("motivo")),
                utente=self.utenti.get(s.get("utente_id")),
                creato_il=_aware(s.get("data_modifica")) or timezone.now(),
            )
            self.r.conteggi["storico scadenze"] += 1

    # ------------------------------------------------------------------ magazzino
    def _magazzino(self) -> None:
        from .magazzino import ImportatoreMagazzino

        ImportatoreMagazzino(self).esegui()

    # ------------------------------------------------------------------ righe consegnate e DDT
    def _documenti_trasporto(self) -> None:
        if "Prod_documentitrasporto" not in self.m.colonne:
            return
        consegna = Consegna.objects.create(
            creata_il=timezone.now(),
            selezione="Importata da Metodo",
            tracciato="Metodo",
            file_nome="(nessun file: dati importati da Metodo)",
            file_contenuto=b"",
        )
        indice: dict[tuple[int, str, str], list[RigaOrdine]] = defaultdict(list)
        for riga in self.righe_per_id.values():
            indice[(riga.ordine_id, riga.n_riga, riga.articolo)].append(riga)
        usate: set[int] = set()
        ddt_cache: dict[tuple[str, int], DDT] = {}
        for d in self.m.righe("Prod_documentitrasporto"):
            chiave = self._chiave(d, "righe consegnate", f"documento id {d['id']}")
            ordine = self.ordini_per_chiave.get(chiave) if chiave else None
            candidate = (
                []
                if ordine is None
                else [
                    r
                    for r in indice.get((ordine.pk, _testo(d.get("n_riga")), _testo(d.get("articolo"))), [])
                    if r.pk not in usate
                ]
            )
            if not candidate:
                self.r.avviso("righe consegnate", f"documento id {d['id']}: riga d'ordine non trovata o già usata")
                continue
            riga = max(candidate, key=lambda r: (r.stato == StatoRiga.ATTIVA, r.pk))
            usate.add(riga.pk)
            ddt = None
            numero = _testo(d.get("no_ddt"))[:20]
            if d.get("inviato") and numero:
                data = d.get("data_ddt") or (d.get("data_inserimento") or dt.date.today())
                if (numero, data.year) not in ddt_cache:
                    ddt_cache[(numero, data.year)], _ = DDT.objects.get_or_create(
                        numero=numero, anno=data.year, defaults={"data": data, "creato_il": timezone.now()}
                    )
                ddt = ddt_cache[(numero, data.year)]
            RigaConsegnata.objects.create(
                consegna=consegna, riga=riga, qta=riga.qta, prezzo_unitario=riga.prezzo_unitario, ddt=ddt
            )
            self.r.conteggi["righe consegnate"] += 1
        self.r.conteggi["DDT"] = len(ddt_cache)
