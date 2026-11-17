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
    # --- Magazzino (tutte facoltative)
    "Prod_codicecomponente": (
        {"id", "codice"},
        {"variante", "descrizione", "um", "prezzo_medio", "fornitore", "note", "attivo", "data_creazione"},
    ),
    "Prod_inventario": ({"id", "qta"}, {"componente_id", "articolo", "variante", "data"}),
    "Prod_richiestemateriali": (
        {"id", "codice", "reparto_id"},
        {
            "variante",
            "data_richiesta",
            "data_ricezione",
            "numero_richieste",
            "mail_inviata_1",
            "mail_inviata_2",
            "mail_inviata_3",
            "materiale_ricevuto",
            "materiale_urgente",
        },
    ),
    "Prod_ddtingresso": ({"id"}, {"numero_ddt", "data_ddt", "fornitore", "data_elaborazione", "utente_id"}),
    "Prod_materialeingresso": (
        {"id", "ddt_id", "codice", "quantita"},
        {"variante", "descrizione", "unita_misura", "prezzo_unitario", "data_ingresso", "verificato", "note"},
    ),
    "Prod_distintabase": (
        {"id", "codice_padre"},
        {
            "variante",
            "descrizione",
            "data_creazione",
            "data_modifica",
            "utente_creazione_id",
            "utente_modifica_id",
            "attiva",
            "note",
        },
    ),
    "Prod_componentedistinta": (
        {"id", "distinta_id", "quantita"},
        {"componente_id", "codice_componente", "variante", "um", "posizione", "note"},
    ),
}
FACOLTATIVE = {"Prod_anagrafica", "Plan_location", "Plan_locpath", "Prod_storicoscadenza", "Prod_documentitrasporto"}
FACOLTATIVE |= {
    "Prod_codicecomponente",
    "Prod_inventario",
    "Prod_richiestemateriali",
    "Prod_ddtingresso",
    "Prod_materialeingresso",
    "Prod_distintabase",
    "Prod_componentedistinta",
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
