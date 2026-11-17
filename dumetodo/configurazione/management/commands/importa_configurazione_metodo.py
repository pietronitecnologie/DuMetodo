"""Importa i valori dalla vecchia tabella ``Configuration`` di Metodo.

Input: CSV (export dell'admin di Metodo, colonne ``chiave`` e ``valore``) o JSON
(lista di oggetti con ``chiave``/``valore``). Senza ``--applica`` mostra solo cosa farebbe.

Le password NON vengono importate: il comando dice in quale variabile d'ambiente metterle.
Le chiavi SOGLIA_<AREA> diventano la capacità settimanale del reparto con quel nome.
"""

import csv
import json
from pathlib import Path
from typing import Any

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from dumetodo.configurazione import servizi
from dumetodo.configurazione.registro import (
    LEGACY_IGNORATI,
    LEGACY_PREFISSO_SOGLIE,
    LEGACY_SEGRETI,
    PARAMETRI,
    Tipo,
)
from dumetodo.core.models import Reparto


def _leggi_file(percorso: Path) -> dict[str, str]:
    if percorso.suffix.lower() == ".json":
        righe = json.loads(percorso.read_text(encoding="utf-8"))
    else:
        with percorso.open(encoding="utf-8-sig", newline="") as fh:
            righe = list(csv.DictReader(fh))
    valori: dict[str, str] = {}
    for r in righe:
        chiave = (r.get("chiave") or "").strip()
        if chiave:
            valori[chiave] = (r.get("valore") or "").strip()
    return valori


class Command(BaseCommand):
    help = "Importa la vecchia tabella Configuration di Metodo (CSV o JSON)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("file", type=Path)
        parser.add_argument("--applica", action="store_true", help="Salva davvero (default: prova a secco).")

    def handle(self, *args: Any, **opts: Any) -> None:
        percorso: Path = opts["file"]
        if not percorso.exists():
            raise CommandError(f"File non trovato: {percorso}")
        vecchi = _leggi_file(percorso)
        da_scrivere: dict[str, Any] = {}
        usate: set[str] = set()

        for p in PARAMETRI:
            presenti = [vecchi[k] for k in p.legacy if vecchi.get(k)]
            if not presenti:
                continue
            usate.update(k for k in p.legacy if k in vecchi)
            grezzo: Any = presenti if p.tipo == Tipo.LISTA_EMAIL else presenti[0]
            try:
                da_scrivere[p.chiave] = p.converti(grezzo)
                self.stdout.write(f"  {p.chiave} = {p.come_testo(da_scrivere[p.chiave])}")
            except ValidationError as exc:
                self.stderr.write(self.style.ERROR(f"  {p.chiave}: valore non valido {grezzo!r}: {exc.messages}"))

        soglie: dict[str, str] = {}
        for chiave, valore in vecchi.items():
            if chiave in usate:
                continue
            if chiave in LEGACY_SEGRETI:
                self.stdout.write(
                    self.style.WARNING(
                        f"  {chiave}: credenziale NON importata -> impostala nel .env come {LEGACY_SEGRETI[chiave]}"
                    )
                )
            elif chiave in LEGACY_IGNORATI:
                self.stdout.write(f"  {chiave}: cursore tecnico di Metodo, non serve più")
            elif chiave.startswith(LEGACY_PREFISSO_SOGLIE):
                soglie[chiave.removeprefix(LEGACY_PREFISSO_SOGLIE)] = valore
            else:
                self.stdout.write(self.style.WARNING(f"  {chiave}: chiave sconosciuta, ignorata"))

        reparti_soglia = []
        for area, valore in soglie.items():
            reparto = next((r for r in Reparto.objects.all() if r.nome.upper().replace(" ", "_") == area), None)
            if reparto is None:
                self.stdout.write(self.style.WARNING(f"  SOGLIA_{area}: nessun reparto corrispondente"))
                continue
            from dumetodo.configurazione.registro import REGISTRO

            try:
                ore = REGISTRO["produzione.prezzo_resa_oraria"].converti(valore)  # stesso parsing decimale
            except ValidationError:
                self.stderr.write(self.style.ERROR(f"  SOGLIA_{area}: valore non valido {valore!r}"))
                continue
            reparti_soglia.append((reparto, ore))
            self.stdout.write(f"  reparto {reparto.nome}: capacità {ore} ore/settimana")

        if not opts["applica"]:
            self.stdout.write(self.style.NOTICE("Prova a secco: nulla è stato salvato. Usa --applica."))
            return
        with transaction.atomic():
            servizi.scrivi_molti(da_scrivere)
            for reparto, ore in reparti_soglia:
                reparto.soglia_ore_settimanali = ore
                reparto.save(update_fields=["soglia_ore_settimanali", "aggiornato_il"])
        self.stdout.write(self.style.SUCCESS("Configurazione importata."))
