"""Importazione del magazzino di Metodo: componenti, inventario, richieste, DDT in ingresso, distinte.

Chiamata da ``Importatore._magazzino`` dopo utenti, reparti e anagrafica articoli. Tutte le tabelle
sono facoltative. I doppioni che in Metodo i vincoli non impedivano vengono fusi qui, in memoria,
e segnalati nel report (l'importazione gira in un'unica transazione: niente IntegrityError):
- componenti ``(codice, NULL)`` ripetuti o con maiuscole/spazi diversi -> uno solo;
- richieste aperte doppie per (codice, variante, reparto) -> una sola, con le richieste sommate
  (come faceva il comando ``elimina_duplicati_richieste``);
- DDT con stesso (fornitore, numero, data) -> uno solo; numero vuoto -> ``METODO-<id>``;
- distinte attive doppie per (articolo, variante) -> resta attiva la più recente;
- componenti ripetuti nella stessa distinta -> quantità sommate.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Any

from django.utils import timezone

from metodo.anagrafica.models import Articolo, UnitaMisura
from metodo.magazzino import formati_ddt
from metodo.magazzino.models import (
    Componente,
    ComponenteDistinta,
    ConteggioInventario,
    DDTIngresso,
    DistintaBase,
    MaterialeIngresso,
    OrigineDDT,
    RichiestaMateriale,
    normalizza_codice,
)

if TYPE_CHECKING:
    from .importatore import Importatore

MIGRATO = "Migrato automaticamente da Metodo"


def _testo(v: Any) -> str:
    return "" if v is None else str(v).strip()


def _decimale(v: Any, decimali: int) -> Decimal | None:
    d = formati_ddt.numero_decimale(None if v is None else str(v))
    if d is None or abs(d) >= Decimal(10) ** (14 - decimali):
        return None
    return d.quantize(Decimal(1).scaleb(-decimali), rounding=ROUND_HALF_UP)


def _aware(v: Any) -> dt.datetime | None:
    if isinstance(v, dt.datetime):
        return timezone.make_aware(v) if timezone.is_naive(v) else v
    if isinstance(v, dt.date):
        return timezone.make_aware(dt.datetime.combine(v, dt.time(12)))
    return None


class ImportatoreMagazzino:
    def __init__(self, imp: Importatore) -> None:
        self.imp = imp
        self.m = imp.m
        self.r = imp.r
        self.componenti: dict[tuple[str, str], Componente] = {}
        self.componenti_per_id: dict[int, Componente] = {}

    def esegui(self) -> None:
        from .importatore import _date_originali

        with _date_originali(
            (Componente, "creato_il"),
            (RichiestaMateriale, "creata_il"),
            (DDTIngresso, "creato_il"),
            (DistintaBase, "creato_il"),
        ):
            self._componenti()
            self._inventario()
            self._richieste()
            self._ddt()
            self._distinte()

    # ------------------------------------------------------------------ aiuti
    def _componente(self, codice: Any, variante: Any, categoria: str, rif: str, **valori: Any) -> Componente | None:
        chiave = (normalizza_codice(_testo(codice)), normalizza_codice(_testo(variante)))
        if not chiave[0]:
            self.r.avviso(categoria, f"{rif}: codice componente vuoto, scartato")
            return None
        if len(chiave[0]) > 50 or len(chiave[1]) > 20:
            self.r.avviso(categoria, f"{rif}: codice {chiave[0]!r}.{chiave[1]!r} troppo lungo, scartato")
            return None
        if chiave not in self.componenti:
            valori.setdefault("descrizione", MIGRATO)
            valori.setdefault("creato_il", timezone.now())
            self.componenti[chiave] = Componente.objects.create(codice=chiave[0], variante=chiave[1], **valori)
            self.r.conteggi["componenti creati da righe"] += 1
        return self.componenti[chiave]

    # ------------------------------------------------------------------ componenti
    def _componenti(self) -> None:
        gruppi: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for c in self.m.righe("Prod_codicecomponente"):
            chiave = (normalizza_codice(_testo(c.get("codice"))), normalizza_codice(_testo(c.get("variante"))))
            if not chiave[0] or len(chiave[0]) > 50 or len(chiave[1]) > 20:
                self.r.avviso("componenti", f"componente id {c['id']}: codice {c.get('codice')!r} non valido, scartato")
                continue
            gruppi[chiave].append(c)
        for (codice, variante), gruppo in gruppi.items():
            gruppo.sort(key=lambda c: (bool(_testo(c.get("descrizione"))), bool(c.get("attivo", True)), c["id"]))
            c = gruppo[-1]
            if len(gruppo) > 1:
                self.r.avviso(
                    "componenti duplicati",
                    f"{codice}.{variante}: {len(gruppo)} record in Metodo, tenuto id {c['id']}",
                )
            prezzo = _decimale(c.get("prezzo_medio"), 4)
            componente = Componente.objects.create(
                codice=codice,
                variante=variante,
                descrizione=_testo(c.get("descrizione")),
                um=c.get("um") if c.get("um") in UnitaMisura.values else UnitaMisura.PEZZI,
                prezzo_medio=prezzo if prezzo is not None and prezzo >= 0 else None,
                fornitore=_testo(c.get("fornitore"))[:100],
                note=_testo(c.get("note")),
                attivo=c.get("attivo") is not False,
                creato_il=_aware(c.get("data_creazione")) or timezone.now(),
            )
            self.componenti[(codice, variante)] = componente
            for g in gruppo:
                self.componenti_per_id[g["id"]] = componente
            self.r.conteggi["componenti"] += 1

    # ------------------------------------------------------------------ inventario
    def _inventario(self) -> None:
        nuovi = []
        for i in self.m.righe("Prod_inventario"):
            rif = f"inventario id {i['id']}"
            componente = self.componenti_per_id.get(i.get("componente_id")) or self._componente(
                i.get("articolo"), i.get("variante"), "inventario", rif
            )
            qta = _decimale(i.get("qta"), 3)
            data = _aware(i.get("data"))
            if componente is None:
                continue
            if qta is None or qta < 0 or data is None:
                self.r.avviso("inventario", f"{rif}: quantità {i.get('qta')!r} o data non valida, scartato")
                continue
            nuovi.append(ConteggioInventario(componente=componente, qta=qta, data=data))
        ConteggioInventario.objects.bulk_create(nuovi, batch_size=1000)
        self.r.conteggi["conteggi di inventario"] += len(nuovi)

    # ------------------------------------------------------------------ richieste materiali
    def _richieste(self) -> None:
        aperte: dict[tuple[str, str, int], RichiestaMateriale] = {}
        for x in self.m.righe("Prod_richiestemateriali"):
            rif = f"richiesta id {x['id']}"
            reparto = self.imp.reparti.get(x.get("reparto_id"))
            codice, variante = normalizza_codice(_testo(x.get("codice"))), normalizza_codice(_testo(x.get("variante")))
            if reparto is None or not codice or len(codice) > 50 or len(variante) > 20:
                self.r.avviso("richieste materiali", f"{rif}: reparto o codice non valido, scartata")
                continue
            data = _aware(x.get("data_richiesta")) or timezone.now()
            ricevuta = bool(x.get("materiale_ricevuto"))
            valori = {
                "numero_richieste": max(int(x.get("numero_richieste") or 0), 1),
                "urgente": bool(x.get("materiale_urgente")),
                "mail_mancante": bool(x.get("mail_inviata_1")),
                "mail_urgente": bool(x.get("mail_inviata_2")),
                "mail_sollecito": bool(x.get("mail_inviata_3")),
            }
            if not ricevuta:
                chiave = (codice, variante, reparto.pk)
                esistente = aperte.get(chiave)
                if esistente is not None:
                    self.r.avviso("richieste duplicate", f"{codice}.{variante} ({reparto}): {rif} fusa")
                    esistente.numero_richieste += valori["numero_richieste"]
                    esistente.data_richiesta = max(esistente.data_richiesta, data)
                    for campo in ("urgente", "mail_mancante", "mail_urgente", "mail_sollecito"):
                        setattr(esistente, campo, getattr(esistente, campo) or valori[campo])
                    esistente.save()
                    continue
            r = RichiestaMateriale.objects.create(
                codice=codice,
                variante=variante,
                reparto=reparto,
                data_richiesta=data,
                ricevuta=ricevuta,
                ricevuta_il=(_aware(x.get("data_ricezione")) or data) if ricevuta else None,
                creata_il=data,
                **valori,
            )
            if not ricevuta:
                aperte[(codice, variante, reparto.pk)] = r
            self.r.conteggi["richieste materiali"] += 1

    # ------------------------------------------------------------------ DDT in ingresso
    def _ddt(self) -> None:
        ddt_per_id: dict[int, DDTIngresso] = {}
        per_chiave: dict[tuple[str, str, dt.date], DDTIngresso] = {}
        for d in self.m.righe("Prod_ddtingresso"):
            numero = _testo(d.get("numero_ddt"))[:50] or f"METODO-{d['id']}"
            elaborato = _aware(d.get("data_elaborazione"))
            data = d.get("data_ddt") or (timezone.localtime(elaborato).date() if elaborato else timezone.localdate())
            fornitore = _testo(d.get("fornitore"))[:100]
            chiave = (fornitore, numero, data)
            if chiave in per_chiave:
                self.r.avviso("DDT in ingresso", f"DDT id {d['id']}: doppione di {numero} del {data}, righe unite")
            else:
                per_chiave[chiave] = DDTIngresso.objects.create(
                    numero=numero,
                    data=data,
                    fornitore=fornitore,
                    origine=OrigineDDT.METODO,
                    creato_da=self.imp.utenti.get(d.get("utente_id")),
                    creato_il=elaborato or timezone.now(),
                )
                self.r.conteggi["DDT in ingresso"] += 1
            ddt_per_id[d["id"]] = per_chiave[chiave]
        nuovi = []
        for mi in self.m.righe("Prod_materialeingresso"):
            rif = f"materiale in ingresso id {mi['id']}"
            ddt = ddt_per_id.get(mi.get("ddt_id"))
            qta = _decimale(mi.get("quantita"), 3)
            if ddt is None or qta is None or qta <= 0:
                self.r.avviso("materiali in ingresso", f"{rif}: DDT mancante o quantità non valida, scartato")
                continue
            descrizione = _testo(mi.get("descrizione"))[:200]
            prezzo = _decimale(mi.get("prezzo_unitario"), 4)
            componente = self._componente(
                mi.get("codice"), mi.get("variante"), "materiali in ingresso", rif, descrizione=descrizione or MIGRATO
            )
            if componente is None:
                continue
            nuovi.append(
                MaterialeIngresso(
                    ddt=ddt,
                    componente=componente,
                    descrizione=descrizione,
                    quantita=qta,
                    unita_misura=(_testo(mi.get("unita_misura")) or "EA")[:10],
                    prezzo_unitario=prezzo if prezzo is not None and prezzo >= 0 else None,
                    data_ingresso=mi.get("data_ingresso") or ddt.data,
                    verificato=bool(mi.get("verificato")),
                    note=_testo(mi.get("note")),
                )
            )
        MaterialeIngresso.objects.bulk_create(nuovi, batch_size=1000)
        self.r.conteggi["materiali in ingresso"] += len(nuovi)

    # ------------------------------------------------------------------ distinte base
    def _articolo(self, codice: str) -> Articolo:
        articolo = Articolo.objects.filter(codice__iexact=codice).order_by("pk").first()
        if articolo is None:
            articolo = Articolo.objects.create(codice=codice)
            self.r.avviso("distinte base", f"articolo {codice} non in anagrafica: aggiunto")
        return articolo

    def _distinte(self) -> None:
        distinte: dict[int, DistintaBase] = {}
        attive: dict[tuple[int, str], DistintaBase] = {}
        righe_db = sorted(
            self.m.righe("Prod_distintabase"),
            key=lambda d: (d.get("attiva") is not False, _aware(d.get("data_modifica")) or timezone.now(), d["id"]),
            reverse=True,
        )
        for d in righe_db:  # prima le attive più recenti: vincono loro sui doppioni
            codice = _testo(d.get("codice_padre"))[:50]
            if not codice:
                self.r.avviso("distinte base", f"distinta id {d['id']}: codice padre vuoto, scartata")
                continue
            articolo = self._articolo(codice)
            variante = normalizza_codice(_testo(d.get("variante")))[:20]
            modificata = _aware(d.get("data_modifica")) or timezone.now()
            eliminata = None if d.get("attiva") is not False else modificata
            if eliminata is None and (articolo.pk, variante) in attive:
                self.r.avviso(
                    "distinte base", f"distinta id {d['id']} ({codice}.{variante}): doppione, segnata eliminata"
                )
                eliminata = modificata
            distinta = DistintaBase.objects.create(
                articolo=articolo,
                variante=variante,
                descrizione=_testo(d.get("descrizione")),
                note=_testo(d.get("note")),
                creata_da=self.imp.utenti.get(d.get("utente_creazione_id")),
                modificata_da=self.imp.utenti.get(d.get("utente_modifica_id")),
                eliminata_il=eliminata,
                creato_il=_aware(d.get("data_creazione")) or modificata,
            )
            if eliminata is None:
                attive[(articolo.pk, variante)] = distinta
            distinte[d["id"]] = distinta
            self.r.conteggi["distinte base"] += 1
        righe: dict[tuple[int, int], ComponenteDistinta] = {}
        posizioni: dict[int, int] = defaultdict(int)
        for c in sorted(self.m.righe("Prod_componentedistinta"), key=lambda c: (c.get("posizione") or 0, c["id"])):
            rif = f"componente distinta id {c['id']}"
            distinta = distinte.get(c.get("distinta_id"))
            qta = _decimale(c.get("quantita"), 4)
            if distinta is None or qta is None or qta <= 0:
                self.r.avviso("distinte base", f"{rif}: distinta mancante o quantità non valida, scartato")
                continue
            componente = self.componenti_per_id.get(c.get("componente_id")) or self._componente(
                c.get("codice_componente"), c.get("variante"), "distinte base", rif
            )
            if componente is None:
                continue
            chiave = (distinta.pk, componente.pk)
            if chiave in righe:
                self.r.avviso("distinte base", f"{rif}: {componente} ripetuto nella stessa distinta, quantità sommate")
                righe[chiave].quantita += qta
                righe[chiave].save()
                continue
            posizioni[distinta.pk] += 1
            righe[chiave] = ComponenteDistinta.objects.create(
                distinta=distinta,
                componente=componente,
                quantita=qta,
                um=c.get("um") if c.get("um") in UnitaMisura.values else UnitaMisura.PEZZI,
                posizione=posizioni[distinta.pk],
                note=_testo(c.get("note")),
            )
            self.r.conteggi["componenti delle distinte"] += 1
