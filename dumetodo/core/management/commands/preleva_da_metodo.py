"""Preleva i dati di produzione da Metodo (server remoto) a blocchi, senza saturare la rete.

    export METODO_DB_URL='postgres://utente:password@host:5432/metodo?sslmode=require'
    python manage.py preleva_da_metodo --verifica          # connessione, schema e volumi stimati
    python manage.py preleva_da_metodo                     # avvia o RIPRENDE il prelievo
    python manage.py preleva_da_metodo --max-kb 512 --pausa 1   # più leggero per la rete di Metodo
    python manage.py preleva_da_metodo --stato             # a che punto è (senza collegarsi a Metodo)
    python manage.py preleva_da_metodo --ricomincia ordini righe consegne   # rilegge e aggiorna

Può essere interrotto in qualsiasi momento (Ctrl+C, rete, riavvio): rilanciandolo riparte dall'ultimo
blocco salvato. Rilanciato in seguito importa solo i record nuovi. Metodo viene letto in sola lettura.
"""

import os
import sys
from typing import Any

import psycopg
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from psycopg.conninfo import conninfo_to_dict

from dumetodo.migrazione.prelievo import (
    PASSI,
    RILEGGIBILI,
    ConnessionePersa,
    GiaInCorso,
    Limitatore,
    Prelievo,
    ReportPrelievo,
    SorgenteRemota,
    stato_attuale,
)
from dumetodo.migrazione.sorgente import TABELLE, SchemaNonCompatibile


def controlla_url(url: str) -> None:
    """Errore chiaro (senza mostrare la password) se l'indirizzo di Metodo non è valido."""
    if not url.startswith(("postgres://", "postgresql://")):
        raise CommandError("Indirizzo di Metodo non valido: deve essere postgres://utente:password@host:5432/database")
    credenziali = url.split("://", 1)[1].split("/", 1)[0]
    if credenziali.count("@") > 1:
        raise CommandError(
            "La password (o l'utente) contiene «@»: nell'indirizzo va scritta %40. Codifica così anche gli altri "
            "caratteri speciali: ! = %21, # = %23, / = %2F, : = %3A, ? = %3F, % = %25."
        )
    try:
        conninfo_to_dict(url)
    except psycopg.ProgrammingError as exc:
        raise CommandError(
            "Indirizzo di Metodo non valido. Formato: postgres://utente:password@host:5432/database "
            "(i caratteri speciali nella password vanno codificati, es. @ = %40)."
        ) from exc


class Command(BaseCommand):
    help = "Preleva i dati da Metodo a blocchi, con limite di banda; interrompibile e riprendibile."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--db",
            default=os.environ.get("METODO_DB_URL", ""),
            help="URL PostgreSQL di Metodo (meglio la variabile METODO_DB_URL: non resta nella cronologia)",
        )
        parser.add_argument("--blocco", type=int, default=2000, help="Record per query (default 2000)")
        parser.add_argument("--max-kb", type=float, default=1024, help="Banda massima in KB/s (0 = nessun limite)")
        parser.add_argument("--pausa", type=float, default=0.2, help="Secondi di pausa tra due query")
        parser.add_argument("--timeout", type=int, default=120, help="Durata massima di una query su Metodo (s)")
        parser.add_argument("--tentativi", type=int, default=10, help="Tentativi se Metodo non risponde")
        parser.add_argument("--solo", nargs="+", choices=PASSI[1:], default=[], help="Esegue solo questi passi")
        parser.add_argument(
            "--ricomincia",
            nargs="+",
            choices=sorted(RILEGGIBILI),
            default=[],
            help="Rilegge dall'inizio questi passi aggiornando i dati già importati",
        )
        parser.add_argument("--log", help="File in cui scrivere tutti gli avvisi")
        parser.add_argument("--max-avvisi", type=int, default=20, help="Avvisi mostrati per categoria")
        parser.add_argument("--verifica", action="store_true", help="Controlla schema e volumi, non importa")
        parser.add_argument("--stato", action="store_true", help="Mostra l'avanzamento salvato ed esce")

    def handle(self, *args: Any, **opts: Any) -> None:
        if opts["stato"]:
            self._stato()
            return
        if not opts["db"]:
            raise CommandError(
                "Indica il database di Metodo con --db o con la variabile METODO_DB_URL. In Docker la variabile "
                "va nel .env oppure passata al container: docker compose exec -e METODO_DB_URL web python manage.py …"
            )
        controlla_url(opts["db"])
        if opts["blocco"] < 1:
            raise CommandError("--blocco deve essere almeno 1.")
        sorgente = SorgenteRemota(
            opts["db"],
            blocco=opts["blocco"],
            timeout=opts["timeout"],
            tentativi=opts["tentativi"],
            limitatore=Limitatore(opts["max_kb"], opts["pausa"]),
            log=lambda m: self.stdout.write(self.style.WARNING(m)),
        )
        if opts["verifica"]:
            self._verifica(sorgente)
            return
        log = open(opts["log"], "a", encoding="utf-8") if opts["log"] else None  # noqa: SIM115
        report = ReportPrelievo(massimo=max(opts["max_avvisi"], 200), file=log)
        prelievo = Prelievo(
            sorgente,
            report,
            solo=opts["solo"],
            ricomincia=opts["ricomincia"],
            avanzamento=lambda m: self.stdout.write(f"[{timezone.localtime():%H:%M:%S}] {m}"),
        )
        try:
            prelievo.esegui()
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING("\nInterrotto: rilancia lo stesso comando per riprendere."))
            self._stampa(report, opts["max_avvisi"])
            sys.exit(130)
        except ConnessionePersa as exc:
            self._stampa(report, opts["max_avvisi"])
            raise CommandError(f"Metodo non raggiungibile: {exc}. Rilancia il comando per riprendere.") from exc
        except SchemaNonCompatibile as exc:
            raise CommandError(f"Schema di Metodo non compatibile: {exc}") from exc
        except GiaInCorso as exc:
            raise CommandError(str(exc).capitalize() + ".") from exc
        finally:
            if log:
                log.close()
        self._stampa(report, opts["max_avvisi"])
        self.stdout.write(self.style.SUCCESS("Prelievo completato. Rilancialo quando vuoi per prendere i dati nuovi."))

    def _verifica(self, sorgente: SorgenteRemota) -> None:
        try:
            avvisi = sorgente.verifica_schema()
        except SchemaNonCompatibile as exc:
            raise CommandError(f"Schema di Metodo non compatibile: {exc}") from exc
        except ConnessionePersa as exc:
            raise CommandError(f"Metodo non raggiungibile: {exc}") from exc
        self.stdout.write(self.style.SUCCESS("Connessione a Metodo riuscita (sola lettura)."))
        for a in avvisi:
            self.stdout.write(self.style.WARNING(f"  {a}"))
        self.stdout.write(self.style.MIGRATE_HEADING("Record stimati in Metodo:"))
        totale = 0
        for tabella in TABELLE:
            if tabella in sorgente.colonne:
                n = sorgente.stima(tabella)
                totale += n
                self.stdout.write(f"  {tabella:<28} ~{n:>12,}")
        self.stdout.write(f"  {'totale':<28} ~{totale:>12,}")
        sorgente.chiudi()

    def _stato(self) -> None:
        righe = stato_attuale()
        if not righe:
            self.stdout.write("Nessun prelievo ancora eseguito.")
            return
        for a in righe:
            fine = "in corso"
            if a.completato_il:
                fine = f"completato il {timezone.localtime(a.completato_il):%d/%m/%Y %H:%M}"
            self.stdout.write(f"  {a.passo:<16} {a.elaborati:>12,} record · ultimo id {a.ultimo_id:<10} {fine}")

    def _stampa(self, report: ReportPrelievo, massimo: int) -> None:
        self.stdout.write(self.style.MIGRATE_HEADING("Importati in questa esecuzione:"))
        for k, v in report.conteggi.items():
            self.stdout.write(f"  {k}: {v:,}")
        for categoria, voci in report.avvisi.items():
            totale = report.totale_avvisi[categoria]
            self.stdout.write(self.style.WARNING(f"Avvisi '{categoria}' ({totale:,}):"))
            for v in voci[:massimo]:
                self.stdout.write(f"  - {v}")
            if totale > massimo:
                self.stdout.write(f"  ... altri {totale - massimo:,}")
