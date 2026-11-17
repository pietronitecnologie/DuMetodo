"""Lettura del database di Metodo (PostgreSQL), in sola lettura e con query parametrizzate."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

# Tabelle e colonne di Metodo usate dalla migrazione (nomi di default Django: <App>_<modello>).
TABELLE: dict[str, tuple[set[str], set[str]]] = {  # tabella -> (colonne obbligatorie, facoltative)
    "auth_user": (
        {"id", "username", "password", "is_active", "is_superuser"},
        {"first_name", "last_name", "email", "is_staff", "date_joined", "last_login"},
    ),
    "auth_group": ({"id", "name"}, set()),
    "auth_user_groups": ({"user_id", "group_id"}, set()),
    "Plan_location": ({"id", "area"}, {"path_id"}),
    "Plan_locpath": ({"id", "code"}, set()),
    "Prod_anagrafica": (
        {"id", "articolo"},
        {
            "location_id",
            "um",
            "prezzo",
            "description",
            "tabulato",
            "prezzo_ricalcolato",
            "data_prezzo",
            "nuovo_articolo",
        },
    ),
    "Prod_ordine": (
        {"id", "rif_ordine", "anno", "serie", "appendice", "stato"},
        {"data_consegna", "data_inserimento", "data_scadenza", "urgenza", "tipo", "location_id"},
    ),
    "Prod_linea": (
        {"id", "rif_ordine", "anno", "serie", "appendice", "articolo", "qta", "n_riga"},
        {
            "data_scadenza",
            "data_inserimento",
            "data_consegna",
            "prezzo_ea",
            "variante",
            "sconto_uni",
            "magazzino",
            "cliente",
            "evasione_parziale",
            "emessa_bolla",
            "blocca_linea",
            "duplicata",
            "annullata",
            "scorporato",
        },
    ),
    "Prod_note": ({"id", "user_id", "testo"}, {"data"}),
    "Prod_ordine_note": ({"ordine_id", "note_id"}, set()),
    "Prod_storicoscadenza": (
        {"id", "ordine_id", "linea_id", "data_scadenza_precedente", "data_scadenza_nuova"},
        {"data_modifica", "utente_id", "motivo"},
    ),
    "Prod_documentitrasporto": (
        {"id", "rif_ordine", "anno", "serie", "appendice", "articolo", "qta", "n_riga"},
        {"no_ddt", "data_ddt", "variante", "inviato", "data_inserimento"},
    ),
    # --- Tracciabilità e qualità (passo in migrazione/tracciabilita.py)
    "Prod_configuration": ({"id", "chiave", "valore"}, set()),
    "Plan_asset": ({"id", "code"}, {"name", "location_id"}),
    "Plan_coding": ({"id", "internalCode"}, {"rawMaterialCode"}),
    "Prod_stored_item": ({"id"}, {"codice_id", "data_in"}),
    "Prod_tracciabilita": (
        {"id", "chiave"},
        {"stored_item_id", "rif_ordine_id", "data_inserimento", "data", "valore", "location_id", "asset_id"},
    ),
    "Prod_check": ({"id", "nome"}, {"descrizione"}),
    "Prod_test": ({"id", "nome"}, {"descrizione", "tipo", "checkDistinta", "checkDisegno"}),
    "Prod_test_checks": ({"id", "test_id", "check_id"}, set()),
    "Prod_controlloqlta": (
        {"id", "codiceProdotto", "data_inserimento", "campione", "ko"},
        {"ordine", "check_rif_id", "qtaInOrdine"},
    ),
    "Prod_reclami": ({"id", "codice", "articolo", "ordine"}, {"data", "reparto_id", "note", "file"}),
    "Prod_displaytest": (
        {"id", "sito", "articolo", "esito", "data"},
        {"prodotto", "fw", "lotto", "note", "data_inserimento", "utente_id"},
    ),
    "Prod_fileordini": ({"id", "file", "rif_ordine"}, {"anno", "serie", "appendice", "data_inserimento"}),
}
FACOLTATIVE = {"Prod_anagrafica", "Plan_location", "Plan_locpath", "Prod_storicoscadenza", "Prod_documentitrasporto"}
# --- Tracciabilità e qualità: tutte facoltative
FACOLTATIVE |= {
    "Prod_configuration",
    "Plan_asset",
    "Plan_coding",
    "Prod_stored_item",
    "Prod_tracciabilita",
    "Prod_check",
    "Prod_test",
    "Prod_test_checks",
    "Prod_controlloqlta",
    "Prod_reclami",
    "Prod_displaytest",
    "Prod_fileordini",
}


class SchemaNonCompatibile(Exception):
    pass


class Metodo:
    def __init__(self, conn: psycopg.Connection[Any]) -> None:
        self.conn = conn
        self.colonne: dict[str, set[str]] = {}

    def verifica_schema(self) -> list[str]:
        """Ritorna gli avvisi; solleva SchemaNonCompatibile se manca qualcosa di indispensabile."""
        avvisi, errori = [], []
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = current_schema()"
            )
            presenti: dict[str, set[str]] = {}
            for tabella, colonna in cur.fetchall():
                presenti.setdefault(tabella, set()).add(colonna)
        for tabella, (obbligatorie, facoltative) in TABELLE.items():
            if tabella not in presenti:
                (avvisi if tabella in FACOLTATIVE else errori).append(f"tabella {tabella} assente")
                continue
            mancanti = obbligatorie - presenti[tabella]
            if mancanti:
                errori.append(f"{tabella}: mancano le colonne {', '.join(sorted(mancanti))}")
            assenti = facoltative - presenti[tabella]
            if assenti:
                avvisi.append(f"{tabella}: colonne assenti (ignorate) {', '.join(sorted(assenti))}")
            self.colonne[tabella] = (obbligatorie | facoltative) & presenti[tabella]
        if errori:
            raise SchemaNonCompatibile("; ".join(errori))
        return avvisi

    def righe(self, tabella: str, ordine: str = "id") -> Iterator[dict[str, Any]]:
        if tabella not in self.colonne:
            return
        colonne = sorted(self.colonne[tabella])
        query = sql.SQL("SELECT {} FROM {} ORDER BY {}").format(
            sql.SQL(", ").join(map(sql.Identifier, colonne)),
            sql.Identifier(tabella),
            sql.Identifier(ordine),
        )
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query)
            for r in cur:
                yield {c: r.get(c) for c in TABELLE[tabella][0] | TABELLE[tabella][1]}


@contextmanager
def connetti(url: str) -> Iterator[Metodo]:
    with psycopg.connect(url, autocommit=False) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        yield Metodo(conn)
        conn.rollback()
