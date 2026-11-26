"""Prelievo a blocchi dei dati di Metodo (produzione remota) verso Metodo v2.

Pensato per milioni di righe (anni di produzione) letti da un server remoto:

- **a blocchi**: ogni query legge al massimo ``blocco`` righe in ordine di id (``WHERE id > ultimo``), usa la
  chiave primaria ed è breve, quindi non va in timeout e non tiene aperta una transazione lunga su Metodo;
- **banda limitata**: tra un blocco e l'altro si attende quanto serve a non superare ``kb_al_secondo``
  (stima sulla dimensione dei dati letti) più una ``pausa`` fissa, per non disturbare chi lavora su Metodo;
- **riprendibile**: ogni blocco viene salvato in Metodo v2 in una transazione insieme al punto raggiunto
  (``Avanzamento``); un'interruzione (rete, Ctrl+C, riavvio) perde al massimo il blocco in corso e
  rilanciando il comando si riparte da lì;
- **riconnessione automatica**: errori di rete e timeout ritentano la stessa query con attese crescenti;
- **incrementale**: rilanciato dopo giorni legge solo i record nuovi; con ``ricomincia`` si rileggono anche
  quelli già importati e ordini, righe e consegne vengono aggiornati (le ``Corrispondenza`` evitano doppioni);
- **allineamento** (passo ``allinea``, per la coesistenza con Metodo): a ogni prelievo si rileggono anche gli
  ordini ancora «vivi» (aperti in Metodo, aperti in Metodo v2 o con date negli ultimi ``giorni_allinea`` giorni)
  e tutte le loro righe, così stati, scadenze, evasioni e bolle cambiati in Metodo arrivano in Metodo v2 senza
  rileggere milioni di record;
- **sola lettura** su Metodo (``default_transaction_read_only``).

Le regole di conversione sono quelle dell'``Importatore`` (vedi importatore.py), riusate qui blocco per blocco.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Iterator
from typing import Any, TextIO

import psycopg
from django.db import connection, transaction
from django.db.models import Count
from django.utils import timezone
from psycopg import sql
from psycopg.rows import dict_row

from metodo.comunicazioni.models import Lato, LetturaNota, Nota, Visibilita
from metodo.consegne.models import DDT, Consegna, RigaConsegnata
from metodo.ordini.models import (
    Ordine,
    OrigineModificaScadenza,
    RigaOrdine,
    StatoOrdine,
    StatoRiga,
    StoricoScadenza,
)
from metodo.timbrature.models import Lavoratore, OrigineTimbratura, Timbratura
from metodo.tracciatura import da_qualita as dq

from . import timbrature as tb
from .importatore import (
    Chiave,
    Importatore,
    _aware,
    _date_originali,
    _testo,
    chiave_vincente,
    valori_ordine,
    valori_ordine_da_riga,
    valori_riga,
)
from .models import Avanzamento, CodificaMetodo, Corrispondenza
from .sorgente import QUERY_COLONNE, TABELLE, Metodo
from .tracciabilita import MODELLI

# Passi nell'ordine di esecuzione. "a blocchi" = tabelle grandi, lette per id e riprendibili.
PASSI = (
    "utenti",
    "anagrafica",
    "ordini",
    "righe",
    "note",
    "storico",
    "consegne",
    "magazzino",
    "qualita",
    "tracce",
    "timbrature",
    "allinea",
)
GIORNI_ALLINEA = 60  # oltre agli ordini aperti, si ricontrollano quelli con date negli ultimi N giorni
STATI_CHIUSI_METODO = ("CON", "ANN")
# Passi rileggibili con --ricomincia (aggiornano ciò che è già stato importato).
RILEGGIBILI = {"ordini", "righe", "note", "storico", "consegne"}
# Passi eseguiti una volta sola (non sono idempotenti): rilanciandoli si creerebbero doppioni.
UNA_VOLTA = {"magazzino", "qualita"}
TABELLE_ORDINE = ("Prod_ordine", "Prod_linea:ordine")
CAMPI_ORDINE_AGGIORNABILI = (
    "stato",
    "urgenza",
    "tipo",
    "reparto",
    "data_scadenza",
    "data_consegna",
    "creato_il",
)
CAMPI_RIGA = (
    "n_riga",
    "articolo",
    "variante",
    "qta",
    "prezzo_unitario",
    "sconto",
    "cliente",
    "magazzino",
    "data_scadenza",
    "data_consegna",
    "stato",
    "bloccata",
    "in_evasione",
    "bolla_emessa",
    "scorporata",
    "origine",
    "creato_il",
)


class ConnessionePersa(Exception):
    pass


class GiaInCorso(Exception):
    pass


# ======================================================================================== report


class ReportPrelievo:
    """Come ``Report`` dell'importatore, ma tiene in memoria solo i primi avvisi di ogni categoria
    (con milioni di righe sarebbero troppi) e li scrive tutti, se richiesto, in un file."""

    def __init__(self, massimo: int = 200, file: TextIO | None = None) -> None:
        self.conteggi: Counter[str] = Counter()
        self.avvisi: dict[str, list[str]] = defaultdict(list)
        self.totale_avvisi: Counter[str] = Counter()
        self.massimo, self.file = massimo, file

    def avviso(self, categoria: str, testo: str) -> None:
        self.totale_avvisi[categoria] += 1
        if len(self.avvisi[categoria]) < self.massimo:
            self.avvisi[categoria].append(testo)
        if self.file is not None:
            self.file.write(f"[{categoria}] {testo}\n")


# ======================================================================================== sorgente


class Limitatore:
    """Rallenta la lettura per non superare la banda indicata (KB/s, 0 = nessun limite)."""

    def __init__(self, kb_al_secondo: float = 0, pausa: float = 0, dormi: Callable[[float], None] | None = None):
        self.byte_al_secondo = kb_al_secondo * 1024
        self.pausa = pausa
        self.dormi = dormi or (lambda secondi: time.sleep(secondi))
        self.totale = 0
        self._ultimo = time.monotonic()

    @staticmethod
    def stima_byte(righe: list[dict[str, Any]]) -> int:
        return sum(16 + sum(len(str(v)) + 4 for v in r.values()) for r in righe)

    def consuma(self, righe: list[dict[str, Any]]) -> None:
        n = self.stima_byte(righe)
        self.totale += n
        attesa = self.pausa
        if self.byte_al_secondo:
            trascorso = time.monotonic() - self._ultimo
            attesa = max(attesa, n / self.byte_al_secondo - trascorso)
        if attesa > 0:
            self.dormi(attesa)
        self._ultimo = time.monotonic()


class SorgenteRemota(Metodo):
    """Metodo letto a blocchi, con riconnessione, timeout per query e limite di banda."""

    def __init__(
        self,
        url: str,
        *,
        blocco: int = 2000,
        timeout: int = 120,
        tentativi: int = 10,
        limitatore: Limitatore | None = None,
        log: Callable[[str], None] = lambda _m: None,
        dormi: Callable[[float], None] | None = None,
    ) -> None:
        super().__init__(None)
        self.url, self.blocco, self.timeout, self.tentativi = url, blocco, timeout, tentativi
        self.limitatore = limitatore or Limitatore()
        self.log = log
        self.dormi = dormi or (lambda secondi: time.sleep(secondi))
        self.query_eseguite = 0

    # ---------------------------------------------------------------- connessione
    def _connetti(self) -> psycopg.Connection[Any]:
        conn = psycopg.connect(
            self.url,
            autocommit=True,  # ogni query è una transazione breve: nessuno snapshot lungo su Metodo
            connect_timeout=30,
            keepalives=1,
            keepalives_idle=30,
            keepalives_interval=10,
            keepalives_count=5,
            application_name="metodo-prelievo",
        )
        conn.execute("SET default_transaction_read_only = on")
        conn.execute(sql.SQL("SET statement_timeout = {}").format(sql.Literal(f"{self.timeout}s")))
        return conn

    def chiudi(self) -> None:
        if self.conn is not None:
            with contextlib.suppress(psycopg.Error):
                self.conn.close()
            self.conn = None

    def esegui(self, query: Any, parametri: Any = None, *, conta: bool = True) -> list[dict[str, Any]]:
        attesa = 2.0
        for tentativo in range(1, self.tentativi + 1):
            try:
                if self.conn is None or self.conn.closed:
                    self.conn = self._connetti()
                with self.conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(query, parametri)
                    righe = cur.fetchall()
            except (psycopg.OperationalError, psycopg.InterfaceError) as exc:
                self.chiudi()
                if tentativo == self.tentativi:
                    raise ConnessionePersa(f"{exc} (dopo {tentativo} tentativi)") from exc
                self.log(f"Metodo non risponde ({str(exc).strip()[:120]}): nuovo tentativo tra {attesa:.0f} s")
                self.dormi(attesa)
                attesa = min(attesa * 2, 300)
                continue
            self.query_eseguite += 1
            if conta:
                self.limitatore.consuma(righe)
            return righe
        raise ConnessionePersa("nessun tentativo eseguito")

    def _colonne_presenti(self) -> dict[str, set[str]]:
        presenti: dict[str, set[str]] = {}
        for r in self.esegui(QUERY_COLONNE, conta=False):
            presenti.setdefault(r["table_name"], set()).add(r["column_name"])
        return presenti

    # ---------------------------------------------------------------- letture
    def _colonne(self, tabella: str) -> list[str]:
        return sorted(self.colonne[tabella])

    def _normalizza(self, tabella: str, righe: list[dict[str, Any]]) -> list[dict[str, Any]]:
        tutte = TABELLE[tabella][0] | TABELLE[tabella][1]
        return [{c: r.get(c) for c in tutte} for r in righe]

    def blocchi(self, tabella: str, dopo: int = 0) -> Iterator[list[dict[str, Any]]]:
        """Blocchi di al massimo ``self.blocco`` righe con id > ``dopo``, in ordine di id."""
        if tabella not in self.colonne:
            return
        query = sql.SQL("SELECT {} FROM {} WHERE {} > %s ORDER BY {} LIMIT %s").format(
            sql.SQL(", ").join(map(sql.Identifier, self._colonne(tabella))),
            sql.Identifier(tabella),
            sql.Identifier("id"),
            sql.Identifier("id"),
        )
        while True:
            righe = self.esegui(query, (dopo, self.blocco))
            if not righe:
                return
            yield self._normalizza(tabella, righe)
            dopo = righe[-1]["id"]

    def righe(self, tabella: str, ordine: str = "id") -> Iterator[dict[str, Any]]:
        if tabella not in self.colonne:
            return
        if ordine == "id" and "id" in self.colonne[tabella]:
            for b in self.blocchi(tabella):
                yield from b
            return
        # Tabelle di collegamento piccole senza id: una sola query.
        query = sql.SQL("SELECT {} FROM {} ORDER BY {}").format(
            sql.SQL(", ").join(map(sql.Identifier, self._colonne(tabella))),
            sql.Identifier(tabella),
            sql.Identifier(ordine),
        )
        yield from self._normalizza(tabella, self.esegui(query))

    def per_valori(self, tabella: str, colonna: str, valori: Iterable[Any]) -> list[dict[str, Any]]:
        """Righe con ``colonna`` in ``valori`` (a gruppi di 5000, per query sempre brevi)."""
        if tabella not in self.colonne or colonna not in self.colonne[tabella]:
            return []
        valori = sorted({v for v in valori if v is not None})
        query = sql.SQL("SELECT {} FROM {} WHERE {} = ANY(%s)").format(
            sql.SQL(", ").join(map(sql.Identifier, self._colonne(tabella))),
            sql.Identifier(tabella),
            sql.Identifier(colonna),
        )
        risultato: list[dict[str, Any]] = []
        for i in range(0, len(valori), 5000):
            risultato += self._normalizza(tabella, self.esegui(query, (valori[i : i + 5000],)))
        return risultato

    def blocchi_dove(
        self, tabella: str, condizione: sql.Composable, parametri: list[Any]
    ) -> Iterator[list[dict[str, Any]]]:
        """Come ``blocchi``, ma solo le righe che soddisfano ``condizione`` (con i suoi ``parametri``)."""
        if tabella not in self.colonne:
            return
        query = sql.SQL("SELECT {} FROM {} WHERE ({}) AND {} > %s ORDER BY {} LIMIT %s").format(
            sql.SQL(", ").join(map(sql.Identifier, self._colonne(tabella))),
            sql.Identifier(tabella),
            condizione,
            sql.Identifier("id"),
            sql.Identifier("id"),
        )
        dopo = 0
        while True:
            righe = self.esegui(query, (*parametri, dopo, self.blocco))
            if not righe:
                return
            yield self._normalizza(tabella, righe)
            dopo = righe[-1]["id"]

    def righe_per_numero(self, tabella: str, numeri: Iterable[tuple[str, str]]) -> list[dict[str, Any]]:
        """Righe con (rif_ordine, anno) tra ``numeri`` (testi già ripuliti), a gruppi di 1000.

        Unione con una tabella di valori invece di ``= ANY``: PostgreSQL usa un hash join anche senza indici,
        quindi la lettura resta veloce su milioni di righe."""
        if tabella not in self.colonne or not {"rif_ordine", "anno"} <= self.colonne[tabella]:
            return []
        query = sql.SQL(
            "SELECT {} FROM {} AS t JOIN unnest(%s::text[], %s::text[]) AS k(rif, anno) "
            "ON btrim(t.rif_ordine::text) = k.rif AND btrim(t.anno::text) = k.anno"
        ).format(
            sql.SQL(", ").join(sql.Identifier("t", c) for c in self._colonne(tabella)),
            sql.Identifier(tabella),
        )
        numeri = sorted(set(numeri))
        risultato: list[dict[str, Any]] = []
        for i in range(0, len(numeri), 1000):
            gruppo = numeri[i : i + 1000]
            risultato += self._normalizza(tabella, self.esegui(query, ([n for n, _ in gruppo], [a for _, a in gruppo])))
        return risultato

    def massimo_id(self, tabella: str) -> int:
        if tabella not in self.colonne:
            return 0
        query = sql.SQL("SELECT max({}) AS m FROM {}").format(sql.Identifier("id"), sql.Identifier(tabella))
        return self.esegui(query, conta=False)[0]["m"] or 0

    def stima(self, tabella: str) -> int:
        """Numero di righe stimato dalle statistiche di PostgreSQL (istantaneo, niente COUNT(*))."""
        r = self.esegui(
            "SELECT GREATEST(reltuples, 0)::bigint AS n FROM pg_class WHERE oid = to_regclass(%s)",
            (sql.Identifier(tabella).as_string(None),),
            conta=False,
        )
        return r[0]["n"] if r else 0


# ======================================================================================== prelievo


def _chiave_testo(k: Chiave) -> str:
    return f"{k[0]}.{k[1]}{'.' + k[2] if k[2] else ''}/{k[3]}"


class Prelievo:
    def __init__(
        self,
        sorgente: SorgenteRemota,
        report: ReportPrelievo,
        *,
        solo: Iterable[str] = (),
        ricomincia: Iterable[str] = (),
        avanzamento: Callable[[str], None] = lambda _m: None,
        ogni_secondi: float = 30,
        giorni_allinea: int = GIORNI_ALLINEA,
    ) -> None:
        self.m, self.r = sorgente, report
        self.imp = Importatore(sorgente)
        self.imp.r = report  # type: ignore[assignment]
        self.solo = set(solo) or set(PASSI)
        self.ricomincia = set(ricomincia)
        self.scrivi, self.ogni_secondi = avanzamento, ogni_secondi
        self._ddt: dict[tuple[str, int], DDT] = {}
        self._consegna: Consegna | None = None
        self._tracce: tuple[Any, dict[str, Any]] | None = None
        self._id_sistema: int | None = None
        self.giorni_allinea = giorni_allinea

    # ---------------------------------------------------------------- esecuzione
    def esegui(self) -> ReportPrelievo:
        with connection.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", [7_316_002])
            if not cur.fetchone()[0]:
                raise GiaInCorso("un altro prelievo da Metodo è già in corso")
        try:
            for a in self.m.verifica_schema():
                self.r.avviso("schema", a)
            for passo in self.ricomincia:
                Avanzamento.objects.filter(passo=passo).update(ultimo_id=0, elaborati=0, completato_il=None)
            with _date_originali(
                (Ordine, "creato_il"),
                (RigaOrdine, "creato_il"),
                (StoricoScadenza, "creato_il"),
                (Consegna, "creata_il"),
                (DDT, "creato_il"),
            ):
                self._utenti_e_reparti()  # sempre: servono a tutti gli altri passi
                for passo in PASSI[1:]:
                    if passo in self.solo:
                        getattr(self, f"_passo_{passo}")()
        finally:
            with connection.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s)", [7_316_002])
            self.m.chiudi()
        return self.r

    def _avanzamento(self, passo: str) -> Avanzamento:
        return Avanzamento.objects.get_or_create(passo=passo)[0]

    def _a_blocchi(self, passo: str, tabella: str, elabora: Callable[[list[dict[str, Any]]], None]) -> None:
        if tabella not in self.m.colonne:
            return
        av = self._avanzamento(passo)
        stima = self.m.stima(tabella)
        partenza, inizio, ultimo_messaggio = av.elaborati, time.monotonic(), 0.0
        self.scrivi(f"{passo}: riparto dall'id {av.ultimo_id} ({av.elaborati:,} già fatti, ~{stima:,} in Metodo)")
        for blocco in self.m.blocchi(tabella, dopo=av.ultimo_id):
            with transaction.atomic():
                elabora(blocco)
                av.ultimo_id = blocco[-1]["id"]
                av.elaborati += len(blocco)
                av.save(update_fields=["ultimo_id", "elaborati", "aggiornato_il"])
            adesso = time.monotonic()
            if adesso - ultimo_messaggio >= self.ogni_secondi:
                ultimo_messaggio = adesso
                self.scrivi(self._riga_avanzamento(passo, av, stima, partenza, adesso - inizio))
        av.completato_il = timezone.now()
        av.save(update_fields=["completato_il", "aggiornato_il"])
        self.scrivi(f"{passo}: completato ({av.elaborati:,} record).")

    def _riga_avanzamento(self, passo: str, av: Avanzamento, stima: int, partenza: int, secondi: float) -> str:
        fatti = av.elaborati - partenza
        velocita = fatti / secondi if secondi else 0
        testo = f"{passo}: {av.elaborati:,}"
        if stima:
            testo += f" / ~{stima:,} ({min(av.elaborati / stima, 1):.0%})"
        testo += f" · {velocita:,.0f} record/s · {self.m.limitatore.totale / 1_048_576:,.1f} MB letti"
        if stima and velocita and av.elaborati < stima:
            resto = dt.timedelta(seconds=int((stima - av.elaborati) / velocita))
            testo += f" · mancano ~{resto}"
        return testo

    def _una_volta(self, passo: str, fai: Callable[[], None]) -> None:
        av = self._avanzamento(passo)
        if av.completato_il:
            self.scrivi(f"{passo}: già importato il {timezone.localtime(av.completato_il):%d/%m/%Y %H:%M}, saltato.")
            return
        self.scrivi(f"{passo}: in corso…")
        with transaction.atomic():
            fai()
            av.completato_il = timezone.now()
            av.save()
        self.scrivi(f"{passo}: completato.")

    # ---------------------------------------------------------------- corrispondenze
    @staticmethod
    def _mappa(tabella: str | tuple[str, ...], ids: Iterable[int]) -> dict[int, int]:
        tabelle = (tabella,) if isinstance(tabella, str) else tabella
        return dict(
            Corrispondenza.objects.filter(tabella__in=tabelle, id_metodo__in=list(ids)).values_list(
                "id_metodo", "id_locale"
            )
        )

    @staticmethod
    def _salva_mappe(tabella: str, coppie: Iterable[tuple[int, int, bool]]) -> None:
        Corrispondenza.objects.bulk_create(
            [Corrispondenza(tabella=tabella, id_metodo=m, id_locale=d, creato=c) for m, d, c in coppie],
            batch_size=5000,
            ignore_conflicts=True,
        )

    @staticmethod
    def _ordini_per_chiave(chiavi: Iterable[Chiave]) -> dict[Chiave, Ordine]:
        chiavi = set(chiavi)
        if not chiavi:
            return {}
        qs = Ordine.objects.filter(numero__in={k[1] for k in chiavi}, anno__in={k[3] for k in chiavi})
        return {k: o for o in qs if (k := (o.serie, o.numero, o.appendice, o.anno)) in chiavi}

    # ---------------------------------------------------------------- utenti, reparti, anagrafica
    def _utenti_e_reparti(self) -> None:
        av = self._avanzamento("utenti")
        vero = self.imp.r
        if av.completato_il:  # già fatto: si ricostruiscono solo le corrispondenze, senza ripetere gli avvisi
            self.imp.r = ReportPrelievo()  # type: ignore[assignment]
        with transaction.atomic():
            self.imp._utenti()
            self.imp._reparti()
            av.completato_il = av.completato_il or timezone.now()
            av.save()
        self.imp.r = vero

    def _passo_anagrafica(self) -> None:
        with transaction.atomic():  # idempotente: i codici già presenti vengono saltati
            self.imp._anagrafica()
            av = self._avanzamento("anagrafica")
            av.completato_il = timezone.now()
            av.save()
        self.scrivi("anagrafica: completata.")

    # ---------------------------------------------------------------- ordini
    def _passo_ordini(self) -> None:
        self._a_blocchi("ordini", "Prod_ordine", self._blocco_ordini)

    def _blocco_ordini(self, blocco: list[dict[str, Any]]) -> None:
        chiavi = {}
        for o in blocco:
            k = self.imp._chiave(o, "ordini", f"ordine id {o['id']}")
            if k:
                chiavi[o["id"]] = k
        esistenti = self._ordini_per_chiave(chiavi.values())
        propri = set(
            Corrispondenza.objects.filter(
                tabella__in=TABELLE_ORDINE, id_locale__in=[o.pk for o in esistenti.values()], creato=True
            ).values_list("id_locale", flat=True)
        )
        gia = self._mappa("Prod_ordine", chiavi)
        nuovi: dict[Chiave, tuple[Ordine, list[int], tuple[bool, dt.datetime]]] = {}
        aggiornati: dict[int, Ordine] = {}
        coppie: list[tuple[int, int, bool]] = []
        for o in blocco:
            k = chiavi.get(o["id"])
            if k is None:
                continue
            if k in nuovi:  # duplicato di un ordine nuovo dello stesso blocco
                ordine, ids, vincente = nuovi[k]
                self.r.avviso("ordini duplicati", f"{_chiave_testo(k)}: più record in Metodo (id {ids[0]}, {o['id']})")
                if chiave_vincente(o) >= vincente:
                    ordine = Ordine(**valori_ordine(o, k, self.imp.reparti, self.r))
                    vincente = chiave_vincente(o)
                nuovi[k] = (ordine, [*ids, o["id"]], vincente)
                continue
            esistente = esistenti.get(k)
            if esistente is None:
                nuovi[k] = (Ordine(**valori_ordine(o, k, self.imp.reparti, self.r)), [o["id"]], chiave_vincente(o))
                self.r.conteggi["ordini"] += 1
            elif esistente.pk not in propri:
                self.r.avviso(
                    "ordini già presenti in Metodo v2",
                    f"{_chiave_testo(k)} (id {o['id']}): non modificato; note e storico vengono collegati",
                )
                coppie.append((o["id"], esistente.pk, False))
            else:
                if o["id"] not in gia:
                    self.r.avviso("ordini duplicati", f"{_chiave_testo(k)}: altro record in Metodo (id {o['id']})")
                attuale = (esistente.stato != StatoOrdine.ANNULLATO, esistente.creato_il)
                if chiave_vincente(o) >= attuale:
                    valori = valori_ordine(o, k, self.imp.reparti, self.r)
                    for campo in CAMPI_ORDINE_AGGIORNABILI:
                        setattr(esistente, campo, valori[campo])
                    aggiornati[esistente.pk] = esistente
                coppie.append((o["id"], esistente.pk, True))
        Ordine.objects.bulk_create([n[0] for n in nuovi.values()])
        for ordine, ids, _ in nuovi.values():
            coppie += [(i, ordine.pk, True) for i in ids]
        if aggiornati:
            Ordine.objects.bulk_update(aggiornati.values(), CAMPI_ORDINE_AGGIORNABILI)
            self.r.conteggi["ordini aggiornati"] += len(aggiornati)
        self._salva_mappe("Prod_ordine", coppie)

    # ---------------------------------------------------------------- righe
    def _passo_righe(self) -> None:
        self._a_blocchi("righe", "Prod_linea", self._blocco_righe)

    def _blocco_righe(self, blocco: list[dict[str, Any]]) -> None:
        chiavi = {}
        for ln in blocco:
            k = self.imp._chiave(ln, "righe", f"linea id {ln['id']}")
            if k:
                chiavi[ln["id"]] = k
        ordini = self._ordini_per_chiave(chiavi.values())
        gia = self._mappa("Prod_linea", chiavi)
        righe_gia = RigaOrdine.objects.in_bulk(list(gia.values()))
        valori_per_id = {ln["id"]: valori_riga(ln, self.r) for ln in blocco if ln["id"] in chiavi}
        nuovi_ordini: list[tuple[int, int, bool]] = []
        for ln in blocco:  # ordini mancanti in Metodo: si ricreano dalla riga
            k = chiavi.get(ln["id"])
            if k is not None and k not in ordini:
                ordini[k] = Ordine.objects.create(**valori_ordine_da_riga(ln, k))
                nuovi_ordini.append((ln["id"], ordini[k].pk, True))
                self.r.avviso(
                    "righe senza ordine", f"{_chiave_testo(k)}: ordine mancante in Metodo, creato ({ordini[k].stato})"
                )
                self.r.conteggi["ordini creati da righe"] += 1
        self._salva_mappe("Prod_linea:ordine", nuovi_ordini)
        attive: dict[tuple[int, str], RigaOrdine] = {
            (r.ordine_id, r.n_riga): r
            for r in RigaOrdine.objects.filter(
                ordine_id__in={o.pk for o in ordini.values()},
                n_riga__in={v["n_riga"] for v in valori_per_id.values()},
                stato=StatoRiga.ATTIVA,
            ).order_by("pk")
        }
        nuove: list[tuple[int, RigaOrdine]] = []
        da_salvare: dict[int, RigaOrdine] = {}
        for ln in blocco:
            if ln["id"] not in chiavi:
                continue
            ordine, valori = ordini[chiavi[ln["id"]]], valori_per_id[ln["id"]]
            k = (ordine.pk, valori["n_riga"])
            riga = righe_gia.get(gia.get(ln["id"], 0))
            if riga is not None:  # riletta: si aggiorna
                altra = attive.get(k)
                if valori["stato"] == StatoRiga.ATTIVA and altra is not None and altra.pk != riga.pk:
                    if altra.pk and riga.pk < altra.pk:
                        valori["stato"] = StatoRiga.SOSTITUITA  # esiste una riga attiva più recente
                    elif altra.pk:
                        altra.stato = StatoRiga.SOSTITUITA
                        da_salvare[altra.pk] = altra
                for campo, v in valori.items():
                    setattr(riga, campo, v)
                da_salvare[riga.pk] = riga
                if riga.stato == StatoRiga.ATTIVA:
                    attive[k] = riga
                self.r.conteggi["righe aggiornate"] += 1
                continue
            if valori["stato"] == StatoRiga.ATTIVA:
                precedente = attive.get(k)
                if precedente is not None:  # due righe attive con lo stesso numero: vince la più recente
                    precedente.stato = StatoRiga.SOSTITUITA
                    if precedente.pk:
                        da_salvare[precedente.pk] = precedente
                    self.r.avviso("righe", f"{ordine}: riga {k[1]} doppia, la precedente diventa 'sostituita'")
            riga = RigaOrdine(ordine=ordine, **valori)
            nuove.append((ln["id"], riga))
            if valori["stato"] == StatoRiga.ATTIVA:
                attive[k] = riga
            self.r.conteggi["righe"] += 1
        # Prima le righe che smettono di essere attive, poi le altre: c'è una sola riga attiva per numero.
        for attiva in (False, True):
            gruppo = [r for r in da_salvare.values() if (r.stato == StatoRiga.ATTIVA) == attiva]
            if gruppo:
                RigaOrdine.objects.bulk_update(gruppo, CAMPI_RIGA, batch_size=2000)
        RigaOrdine.objects.bulk_create([r for _, r in nuove], batch_size=2000)
        self._salva_mappe("Prod_linea", [(i, r.pk, True) for i, r in nuove])
        self._cliente_degli_ordini({o.pk for o in ordini.values()})

    @staticmethod
    def _cliente_degli_ordini(pks: set[int]) -> None:
        """Il cliente dell'ordine è il più frequente tra le sue righe (solo dove non è ancora impostato)."""
        vuoti = list(Ordine.objects.filter(pk__in=pks, cliente="").values_list("pk", flat=True))
        if not vuoti:
            return
        migliori: dict[int, tuple[int, str]] = {}
        for r in (
            RigaOrdine.objects.filter(ordine_id__in=vuoti)
            .exclude(cliente="")
            .values("ordine_id", "cliente")
            .annotate(n=Count("id"))
            .order_by("ordine_id", "cliente")
        ):
            if r["n"] > migliori.get(r["ordine_id"], (0, ""))[0]:
                migliori[r["ordine_id"]] = (r["n"], r["cliente"])
        Ordine.objects.bulk_update([Ordine(pk=pk, cliente=c) for pk, (_, c) in migliori.items()], ["cliente"])

    # ---------------------------------------------------------------- note
    def _passo_note(self) -> None:
        self._a_blocchi("note", "Prod_note", self._blocco_note)

    def _blocco_note(self, blocco: list[dict[str, Any]]) -> None:
        gia = self._mappa("Prod_note", [n["id"] for n in blocco])
        da_fare = [n for n in blocco if n["id"] not in gia]
        if not da_fare:
            return
        legami: dict[int, list[int]] = defaultdict(list)
        for on in self.m.per_valori("Prod_ordine_note", "note_id", [n["id"] for n in da_fare]):
            legami[on["note_id"]].append(on["ordine_id"])
        ordini = self._mappa("Prod_ordine", {o for lista in legami.values() for o in lista})
        nuove: list[tuple[int, Nota]] = []
        for n in da_fare:
            destinazioni = {ordini[o] for o in legami.get(n["id"], []) if o in ordini}
            if not destinazioni:
                self.r.conteggi["note senza ordine (ignorate)"] += 1
                continue
            autore = self.imp.utenti.get(n["user_id"])
            for pk in sorted(destinazioni):
                nuove.append(
                    (
                        n["id"],
                        Nota(
                            ordine_id=pk,
                            autore=autore,
                            autore_nome=(autore.get_full_name() or autore.get_username())
                            if autore
                            else "utente rimosso",
                            lato=Lato.CLIENTE if n["user_id"] in self.imp.clienti else Lato.FORNITORE,
                            visibilita=Visibilita.CONDIVISA,
                            testo=_testo(n.get("testo")) or "(nota vuota)",
                            creata_il=_aware(n.get("data")) or timezone.now(),
                        ),
                    )
                )
        Nota.objects.bulk_create([x for _, x in nuove], batch_size=2000)
        self.r.conteggi["note"] += len(nuove)
        # Le note storiche risultano già lette, altrimenti ogni utente avrebbe migliaia di "non lette".
        utenti = list(self.imp.utenti.values())
        LetturaNota.objects.bulk_create(
            [LetturaNota(nota=x, utente=u) for _, x in nuove for u in utenti], batch_size=5000, ignore_conflicts=True
        )
        self._salva_mappe("Prod_note", list({i: (i, x.pk, True) for i, x in nuove}.values()))

    # ---------------------------------------------------------------- storico scadenze
    def _passo_storico(self) -> None:
        self._a_blocchi("storico", "Prod_storicoscadenza", self._blocco_storico)

    def _blocco_storico(self, blocco: list[dict[str, Any]]) -> None:
        gia = self._mappa("Prod_storicoscadenza", [s["id"] for s in blocco])
        da_fare = [s for s in blocco if s["id"] not in gia]
        ordini = self._mappa("Prod_ordine", {s.get("ordine_id") for s in da_fare})
        righe_id = self._mappa("Prod_linea", {s.get("linea_id") for s in da_fare})
        ordine_della_riga = dict(RigaOrdine.objects.filter(pk__in=righe_id.values()).values_list("pk", "ordine_id"))
        nuovi: list[tuple[int, StoricoScadenza]] = []
        for s in da_fare:
            riga = righe_id.get(s.get("linea_id"))
            ordine = ordini.get(s.get("ordine_id")) or ordine_della_riga.get(riga)
            if ordine is None:
                self.r.conteggi["storico scadenze senza ordine (ignorato)"] += 1
                continue
            utente = self.imp.utenti.get(s.get("utente_id"))
            nuovi.append(
                (
                    s["id"],
                    StoricoScadenza(
                        ordine_id=ordine,
                        riga_id=riga,
                        precedente=s.get("data_scadenza_precedente"),
                        nuova=s.get("data_scadenza_nuova"),
                        origine=OrigineModificaScadenza.UTENTE,
                        motivo=_testo(s.get("motivo")),
                        utente=utente,
                        creato_il=_aware(s.get("data_modifica")) or timezone.now(),
                    ),
                )
            )
        StoricoScadenza.objects.bulk_create([x for _, x in nuovi], batch_size=2000)
        self.r.conteggi["storico scadenze"] += len(nuovi)
        self._salva_mappe("Prod_storicoscadenza", [(i, x.pk, True) for i, x in nuovi])

    # ---------------------------------------------------------------- righe consegnate e DDT
    def _passo_consegne(self) -> None:
        self._a_blocchi("consegne", "Prod_documentitrasporto", self._blocco_consegne)

    def _consegna_importata(self) -> Consegna:
        if self._consegna is None:
            av = self._avanzamento("consegne")
            self._consegna = Consegna.objects.filter(pk=av.dati.get("consegna")).first()
            if self._consegna is None:
                self._consegna = Consegna.objects.create(
                    creata_il=timezone.now(),
                    selezione="Importata da Metodo",
                    tracciato="Metodo",
                    file_nome="(nessun file: dati importati da Metodo)",
                    file_contenuto=b"",
                )
                av.dati = {**av.dati, "consegna": self._consegna.pk}
                av.save(update_fields=["dati"])
        return self._consegna

    def _ddt_per(self, d: dict[str, Any]) -> DDT | None:
        numero = _testo(d.get("no_ddt"))[:20]
        if not (d.get("inviato") and numero):
            return None
        data = d.get("data_ddt") or d.get("data_inserimento") or dt.date.today()
        if isinstance(data, dt.datetime):
            data = data.date()
        chiave = (numero, data.year)
        if chiave not in self._ddt:
            self._ddt[chiave], creato = DDT.objects.get_or_create(
                numero=numero, anno=data.year, defaults={"data": data, "creato_il": timezone.now()}
            )
            self.r.conteggi["DDT"] += int(creato)
        return self._ddt[chiave]

    def _blocco_consegne(self, blocco: list[dict[str, Any]]) -> None:
        consegna = self._consegna_importata()
        gia = self._mappa("Prod_documentitrasporto", [d["id"] for d in blocco])
        gia_rc = RigaConsegnata.objects.in_bulk(list(gia.values()))
        chiavi = {}
        for d in blocco:
            if d["id"] in gia:
                continue
            k = self.imp._chiave(d, "righe consegnate", f"documento id {d['id']}")
            if k:
                chiavi[d["id"]] = k
        ordini = self._ordini_per_chiave(chiavi.values())
        indice: dict[tuple[int, str, str], list[RigaOrdine]] = defaultdict(list)
        for riga in RigaOrdine.objects.filter(
            ordine_id__in={o.pk for o in ordini.values()},
            n_riga__in={_testo(d.get("n_riga")) for d in blocco},
            consegnata__isnull=True,
        ):
            indice[(riga.ordine_id, riga.n_riga, riga.articolo)].append(riga)
        usate: set[int] = set()
        nuove: list[tuple[int, RigaConsegnata]] = []
        for d in blocco:
            rc = gia_rc.get(gia.get(d["id"], 0))
            if rc is not None:  # riletta: il DDT può essere stato emesso dopo il primo prelievo
                ddt = self._ddt_per(d)
                if ddt is not None and rc.ddt_id is None:
                    rc.ddt = ddt
                    rc.save(update_fields=["ddt"])
                    self.r.conteggi["righe consegnate aggiornate col DDT"] += 1
                continue
            if d["id"] in gia:
                continue
            ordine = ordini.get(chiavi.get(d["id"]))  # type: ignore[arg-type]
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
            nuove.append(
                (
                    d["id"],
                    RigaConsegnata(
                        consegna=consegna,
                        riga=riga,
                        qta=riga.qta,
                        prezzo_unitario=riga.prezzo_unitario,
                        ddt=self._ddt_per(d),
                    ),
                )
            )
        RigaConsegnata.objects.bulk_create([x for _, x in nuove], batch_size=2000)
        self.r.conteggi["righe consegnate"] += len(nuove)
        self._salva_mappe("Prod_documentitrasporto", [(i, x.pk, True) for i, x in nuove])

    # ---------------------------------------------------------------- magazzino e qualità (tabelle piccole)
    def _passo_magazzino(self) -> None:
        from .magazzino import ImportatoreMagazzino

        self._una_volta("magazzino", ImportatoreMagazzino(self.imp).esegui)

    def _passo_qualita(self) -> None:
        from .tracciabilita import CAMPI_DATA_ORIGINALE, ImportaTracciabilita

        def fai() -> None:
            t = ImportaTracciabilita(self.imp)
            with _date_originali(*CAMPI_DATA_ORIGINALE):
                t._test()
                t._controlli_qualita()
                t._file()

        self._una_volta("qualita", fai)

    # ---------------------------------------------------------------- tracce (tabella molto grande)
    def _passo_tracce(self) -> None:
        """Tre fasi, tutte riprendibili:
        1. riepilogo per codice tracciato calcolato da Metodo a intervalli di id (pochi dati in rete);
        2. codice di ogni elemento (esclusi batterie e cavi, come nell'importatore);
        3. tracce, a blocchi, come tracciature del modello «Tracce (da Metodo)»."""
        if "Prod_tracciabilita" not in self.m.colonne:
            return
        self._tracce_riepilogo()
        self._tracce_elementi()
        self._a_blocchi("tracce", "Prod_tracciabilita", self._blocco_tracce)

    def _tracce_riepilogo(self) -> None:
        col_t = self.m.colonne["Prod_tracciabilita"]
        col_s = self.m.colonne.get("Prod_stored_item", set())
        if "stored_item_id" not in col_t or "codice_id" not in col_s:
            return
        data = [c for c in ("data", "data_inserimento") if c in col_t]
        prima = (
            sql.SQL("min(COALESCE({}))").format(sql.SQL(", ").join(sql.Identifier("t", c) for c in data))
            if data
            else sql.SQL("NULL::timestamptz")
        )
        banco = sql.SQL("bool_or(t.asset_id IS NOT NULL)") if "asset_id" in col_t else sql.SQL("false")
        query = sql.SQL(
            "SELECT s.codice_id AS cod, bool_or(upper(trim(t.chiave)) IN ('IN', 'RC')) AS batteria, "
            "{banco} AS banco, {prima} AS prima "
            'FROM "Prod_tracciabilita" t JOIN "Prod_stored_item" s ON s.id = t.stored_item_id '
            "WHERE t.id > %s AND t.id <= %s AND s.codice_id IS NOT NULL GROUP BY s.codice_id"
        ).format(banco=banco, prima=prima)
        av = self._avanzamento("tracce: codici")
        massimo = self.m.massimo_id("Prod_tracciabilita")
        passo = self.m.blocco * 10  # il server aggrega: in rete passa una riga per codice
        self.scrivi(f"tracce: riepilogo dei codici dall'id {av.ultimo_id} a {massimo}")
        while av.ultimo_id < massimo:
            fine = min(av.ultimo_id + passo, massimo)
            righe = self.m.esegui(query, (av.ultimo_id, fine))
            with transaction.atomic():
                self._unisci_codifiche(righe)
                av.ultimo_id, av.elaborati = fine, av.elaborati + len(righe)
                av.save(update_fields=["ultimo_id", "elaborati", "aggiornato_il"])
        av.completato_il = timezone.now()
        av.save(update_fields=["completato_il", "aggiornato_il"])

    @staticmethod
    def _unisci_codifiche(righe: list[dict[str, Any]]) -> None:
        if not righe:
            return
        tabella = CodificaMetodo._meta.db_table
        with connection.cursor() as cur:
            cur.executemany(
                f"INSERT INTO {tabella} (id_metodo, batteria, banco, prima, elaborata, scartata, codice) "  # noqa: S608
                "VALUES (%s, %s, %s, %s, false, false, '') ON CONFLICT (id_metodo) DO UPDATE SET "
                f"batteria = {tabella}.batteria OR EXCLUDED.batteria, banco = {tabella}.banco OR EXCLUDED.banco, "
                f"prima = LEAST({tabella}.prima, EXCLUDED.prima)",
                [(r["cod"], bool(r["batteria"]), bool(r["banco"]), _aware(r["prima"])) for r in righe],
            )

    def _tracce_elementi(self) -> None:
        prefisso_cavi = ""
        for c in self.m.righe("Prod_configuration"):
            if _testo(c["chiave"]) == "ITEM_CODE_CAVI":
                prefisso_cavi = _testo(c["valore"])
        while True:
            gruppo = list(CodificaMetodo.objects.filter(elaborata=False).order_by("id_metodo")[: self.m.blocco])
            if not gruppo:
                return
            codifiche = {
                c["id"]: _testo(c["internalCode"])[:50]
                for c in self.m.per_valori("Plan_coding", "id", [g.id_metodo for g in gruppo])
            }
            with transaction.atomic():
                for g in gruppo:
                    g.elaborata = True
                    if g.id_metodo not in codifiche:
                        continue
                    codice = codifiche[g.id_metodo]
                    if not codice:
                        self.r.avviso(
                            "tracce", f"codifica id {g.id_metodo} senza codice: eventi tenuti solo se hanno un ordine"
                        )
                        continue
                    if g.batteria or g.banco or (prefisso_cavi and codice.startswith(prefisso_cavi)):
                        g.scartata = True
                        self.r.conteggi["batterie e cavi (non importati)"] += 1
                        continue
                    g.codice = codice
                    self.r.conteggi["elementi tracciati"] += 1
                CodificaMetodo.objects.bulk_update(gruppo, ["elaborata", "scartata", "codice"], batch_size=2000)

    def _blocco_tracce(self, blocco: list[dict[str, Any]]) -> None:
        item_codifica = {
            s["id"]: s.get("codice_id")
            for s in self.m.per_valori("Prod_stored_item", "id", [t.get("stored_item_id") for t in blocco])
        }
        codifiche = {
            c.id_metodo: c
            for c in CodificaMetodo.objects.filter(id_metodo__in={v for v in item_codifica.values() if v is not None})
        }
        ordini = self._mappa("Prod_ordine", {t.get("rif_ordine_id") for t in blocco})
        nuove: list[dict[str, Any]] = []
        for t in blocco:
            cod = codifiche.get(item_codifica.get(t.get("stored_item_id")))  # type: ignore[arg-type]
            if t.get("asset_id") or (cod is not None and cod.scartata):
                self.r.conteggi["tracce di batterie, cavi e banchi (non importate)"] += 1
                continue
            codice = cod.codice if cod is not None and cod.codice else None
            ordine = ordini.get(t.get("rif_ordine_id"))
            if codice is None and ordine is None:
                self.r.conteggi["tracce senza elemento né ordine (ignorate)"] += 1
                continue
            reparto = self.imp.reparti.get(t.get("location_id"))
            nuove.append(
                {
                    "momento": _aware(t.get("data")) or _aware(t.get("data_inserimento")) or timezone.now(),
                    "operatore_id": self._sistema(),
                    "ordine_id": ordine,
                    "codice": codice or "",
                    "chiave": _testo(t["chiave"])[:50] or "?",
                    "valore": _testo(t.get("valore"))[:100],
                    "reparto": reparto.nome if reparto else "",
                }
            )
        if nuove:
            if self._tracce is None:
                self._tracce = dq.modello_tracce(MODELLI)
            self.r.conteggi["tracce"] += dq.tracce_in_blocco(MODELLI, *self._tracce, nuove)

    # ---------------------------------------------------------------- lavoratori e timbrature
    def _passo_timbrature(self) -> None:
        if "Timbratura_timbratura" not in self.m.colonne or "Plan_resource" not in self.m.colonne:
            self.scrivi("timbrature: tabelle non presenti in Metodo, saltate.")
            return
        self._lavoratori = self._importa_lavoratori()
        self._luoghi = {loc_id: tb.luogo(r.codice, r.nome) for loc_id, r in self.imp.reparti.items()}
        self._stati: dict[int, tb.Stato] = {}
        self._a_blocchi("timbrature", "Timbratura_timbratura", self._blocco_timbrature)

    def _importa_lavoratori(self) -> dict[int, Lavoratore]:
        """Plan.Resource → Lavoratore (matricola = codice della risorsa). Idempotente: i lavoratori già presenti
        non vengono modificati (in Metodo v2 hanno codici personali, badge e costi propri)."""
        mappa: dict[int, Lavoratore] = {}
        for r in self.m.righe("Plan_resource"):
            matricola = (_testo(r.get("code")) or f"METODO-{r['id']}")[:30]
            lav, creato = Lavoratore.objects.get_or_create(
                matricola=matricola,
                defaults={
                    "nome": _testo(r.get("name"))[:60] or "?",
                    "cognome": _testo(r.get("lastname"))[:60],
                    "attivo": r.get("active") is not False,
                },
            )
            mappa[r["id"]] = lav
            if creato:
                self.r.conteggi["lavoratori creati"] += 1
        return mappa

    def _stato(self, lav: Lavoratore) -> tb.Stato:
        """Dove si trova il lavoratore secondo l'ultima timbratura già in Metodo v2 (ripresa del prelievo)."""
        if lav.pk not in self._stati:
            ultima = (
                Timbratura.objects.filter(lavoratore=lav, annullata_il__isnull=True)
                .order_by("-momento", "-id")
                .values_list("tipo", "reparto_id", "momento")
                .first()
            )
            self._stati[lav.pk] = tb.stato_da_evento(*ultima) if ultima else tb.Stato()
        return self._stati[lav.pk]

    def _blocco_timbrature(self, blocco: list[dict[str, Any]]) -> None:
        minimo = dt.datetime.min.replace(tzinfo=dt.UTC)
        righe = [(r, _aware(r.get("date")) or _aware(r.get("date_created"))) for r in blocco]
        righe.sort(key=lambda x: (x[0]["resource_id"] or 0, x[1] or minimo, x[0]["id"]))
        nuove: list[Timbratura] = []
        for r, quando in righe:
            lav = self._lavoratori.get(r["resource_id"])
            if lav is None or quando is None:
                self.r.avviso("timbrature", f"timbratura {r['id']}: lavoratore o ora mancanti, saltata")
                continue
            reparto = self.imp.reparti.get(r["location_id"])
            arrivo = self._luoghi.get(r["location_id"], tb.INGRESSO)
            prima = self._stato(lav)
            if prima.quando and quando < prima.quando:
                self.r.avviso(
                    "timbrature",
                    f"{lav}: timbratura {r['id']} del {timezone.localtime(quando):%d/%m/%Y %H:%M} precede "
                    "l'ultima importata: controllala in Timbrature",
                )
            eventi, self._stati[lav.pk] = tb.converti(prima, arrivo, reparto.pk if reparto else None, quando)
            for tipo, reparto_id in eventi:
                nuove.append(
                    Timbratura(
                        lavoratore=lav,
                        tipo=tipo,
                        momento=quando,
                        reparto_id=reparto_id,
                        origine=OrigineTimbratura.METODO,
                        nota=f"Metodo n. {r['id']}",
                    )
                )
        Timbratura.objects.bulk_create(nuove, batch_size=2000)
        self.r.conteggi["timbrature (eventi creati)"] += len(nuove)

    # ---------------------------------------------------------------- allineamento (coesistenza)
    def _passo_allinea(self) -> None:
        """Rilegge gli ordini ancora vivi e le loro righe, e aggiorna Metodo v2 con i cambi fatti in Metodo.

        Vivi: aperti in Metodo, aperti in Metodo v2 (magari chiusi nel frattempo in Metodo) o con inserimento,
        scadenza o consegna negli ultimi ``giorni_allinea`` giorni. Gli ordini nati in Metodo v2 non si toccano
        (come nel resto del prelievo)."""
        if "Prod_ordine" not in self.m.colonne:
            return
        self.scrivi(f"allinea: ricontrollo gli ordini aperti e quelli degli ultimi {self.giorni_allinea} giorni…")
        vero, silenzioso = self.r, ReportPrelievo(massimo=0)
        self.r = self.imp.r = silenzioso  # type: ignore[assignment]  # gli avvisi sono già stati dati all'import
        try:
            ordini = self._ordini_vivi()
            chiavi = {k for o in ordini if (k := self.imp._chiave(o, "ordini", ""))}
            pk_ordini = {o.pk for o in self._ordini_per_chiave(chiavi).values()}
            prima_ordini = self._foto_ordini(pk_ordini)
            prima_righe = self._foto_righe(pk_ordini)
            for i in range(0, len(ordini), self.m.blocco):
                with transaction.atomic():
                    self._blocco_ordini(ordini[i : i + self.m.blocco])
            numeri = {(_testo(o.get("rif_ordine")), _testo(o.get("anno"))) for o in ordini}
            righe = [
                ln
                for ln in self.m.righe_per_numero("Prod_linea", numeri)
                if self.imp._chiave(ln, "righe", "") in chiavi
            ]
            righe.sort(key=lambda ln: ln["id"])
            for i in range(0, len(righe), self.m.blocco):
                with transaction.atomic():
                    self._blocco_righe(righe[i : i + self.m.blocco])
        finally:
            self.r = self.imp.r = vero  # type: ignore[assignment]
        pk_ordini = {o.pk for o in self._ordini_per_chiave(chiavi).values()}
        dopo_ordini, dopo_righe = self._foto_ordini(pk_ordini), self._foto_righe(pk_ordini)
        cambiati = [pk for pk, v in dopo_ordini.items() if prima_ordini.get(pk) != v]
        stati = sum(1 for pk in cambiati if pk in prima_ordini and prima_ordini[pk][0] != dopo_ordini[pk][0])
        righe_cambiate = sum(1 for pk, v in dopo_righe.items() if pk in prima_righe and prima_righe[pk] != v)
        righe_nuove = sum(1 for pk in dopo_righe if pk not in prima_righe)
        c = self.r.conteggi
        c["allineamento: ordini ricontrollati"] += len(ordini)
        c["allineamento: ordini modificati"] += len(cambiati)
        c["allineamento: cambi di stato"] += stati
        c["allineamento: righe modificate"] += righe_cambiate
        c["allineamento: righe nuove"] += righe_nuove
        av = self._avanzamento("allinea")
        av.elaborati, av.completato_il = len(ordini), timezone.now()
        av.save(update_fields=["elaborati", "completato_il", "aggiornato_il"])
        self.scrivi(
            f"allinea: {len(ordini):,} ordini ricontrollati, {len(cambiati):,} modificati ({stati:,} cambi di stato), "
            f"{righe_cambiate:,} righe modificate, {righe_nuove:,} righe nuove."
        )

    def _ordini_vivi(self) -> list[dict[str, Any]]:
        colonne = self.m.colonne["Prod_ordine"]
        dal = timezone.localdate() - dt.timedelta(days=self.giorni_allinea)
        condizioni = [
            sql.SQL("btrim({}) NOT IN ({})").format(
                sql.Identifier("stato"), sql.SQL(", ").join(map(sql.Literal, STATI_CHIUSI_METODO))
            )
        ]
        parametri: list[Any] = []
        for campo in ("data_inserimento", "data_scadenza", "data_consegna"):
            if campo in colonne:
                condizioni.append(sql.SQL("{} >= %s").format(sql.Identifier(campo)))
                parametri.append(dal)
        per_id: dict[int, dict[str, Any]] = {}
        for blocco in self.m.blocchi_dove("Prod_ordine", sql.SQL(" OR ").join(condizioni), parametri):
            per_id.update((o["id"], o) for o in blocco)
        # Aperti in Metodo v2 ma forse chiusi in Metodo da tempo: si rileggono per id.
        aperti_v2 = Ordine.objects.exclude(stato__in=[StatoOrdine.CONSEGNATO, StatoOrdine.ANNULLATO])
        ids = set(
            Corrispondenza.objects.filter(
                tabella="Prod_ordine", creato=True, id_locale__in=aperti_v2.values("pk")
            ).values_list("id_metodo", flat=True)
        ) - set(per_id)
        per_id.update((o["id"], o) for o in self.m.per_valori("Prod_ordine", "id", ids))
        return [per_id[i] for i in sorted(per_id)]

    @staticmethod
    def _foto_ordini(pks: set[int]) -> dict[int, tuple[Any, ...]]:
        return {r[0]: r[1:] for r in Ordine.objects.filter(pk__in=pks).values_list("pk", *CAMPI_ORDINE_AGGIORNABILI)}

    @staticmethod
    def _foto_righe(pks: set[int]) -> dict[int, tuple[Any, ...]]:
        return {r[0]: r[1:] for r in RigaOrdine.objects.filter(ordine_id__in=pks).values_list("pk", *CAMPI_RIGA)}

    def _sistema(self) -> int:
        if self._id_sistema is None:
            self._id_sistema = dq.utente_sistema(MODELLI).pk
        return self._id_sistema


def stato_attuale() -> list[Avanzamento]:
    return list(Avanzamento.objects.order_by("id"))
