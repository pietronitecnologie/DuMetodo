"""Area «database» (installati sul server, non in container): PostgreSQL, MySQL/MariaDB, Redis.

Le interrogazioni sono di sola lettura e passano dall'utente amministratore locale del database (postgres,
root via socket): servono i permessi di root."""

from __future__ import annotations

import re

from .base import ATTENZIONE, ERRORE, OK, SERVE_ROOT, Contesto, Esito, esegui, esito_servizio, presente, primo_servizio

G = "database"
SQL_PG = {
    "versione": "SHOW server_version",
    "ascolto": "SHOW listen_addresses",
    "connessioni": "SELECT count(*) || '/' || current_setting('max_connections') FROM pg_stat_activity",
    "lente": (
        "SELECT count(*) FROM pg_stat_activity WHERE state = 'active' AND backend_type = 'client backend' "
        "AND now() - query_start > interval '5 minutes'"
    ),
    "bloccate": (
        "SELECT count(*) FROM pg_stat_activity WHERE state LIKE 'idle in transaction%' "
        "AND now() - state_change > interval '10 minutes'"
    ),
    "eta_xid": "SELECT coalesce(max(age(datfrozenxid)), 0) FROM pg_database",
    "dimensioni": (
        "SELECT string_agg(datname || ' ' || pg_size_pretty(pg_database_size(datname)), ', ' "
        "ORDER BY pg_database_size(datname) DESC) FROM pg_database WHERE datallowconn AND NOT datistemplate"
    ),
    "trust": (
        "SELECT count(*) FROM pg_hba_file_rules WHERE auth_method = 'trust' AND type LIKE 'host%' "
        "AND coalesce(address, '') NOT IN ('127.0.0.1', '::1', 'localhost', '')"
    ),
    "password_in_chiaro": "SELECT count(*) FROM pg_hba_file_rules WHERE auth_method = 'password'",
}


def presente_area(ctx: Contesto) -> bool:
    return bool(primo_servizio("postgresql", "mariadb", "mysql", "redis-server", "redis")) or presente("pg_lsclusters")


def leggi_cluster(testo: str) -> list[tuple[str, str, int, str]]:
    """``pg_lsclusters --no-header`` → [(versione, nome, porta, stato)]."""
    out = []
    for riga in testo.splitlines():
        p = riga.split()
        if len(p) >= 4 and p[2].isdigit():
            out.append((p[0], p[1], int(p[2]), p[3]))
    return out


def _psql(porta: int, sql: str) -> str | None:
    r = esegui(["runuser", "-u", "postgres", "--", "psql", "-p", str(porta), "-XAtqc", sql], timeout=30)
    return r.testo.strip() if r.ok else None


def postgresql(ctx: Contesto) -> list[Esito]:
    e: list[Esito] = []
    cluster = (
        leggi_cluster(esegui(["pg_lsclusters", "--no-header"], timeout=15).testo) if presente("pg_lsclusters") else []
    )
    if not cluster:
        trovato = primo_servizio("postgresql")
        if trovato is None:
            return e
        cluster = [("", "main", 5432, "online" if trovato[1].get("ActiveState") == "active" else "down")]
    for versione, nome, porta, stato in cluster:
        etichetta = f"PostgreSQL {versione}/{nome}".replace(" /", " ")
        if stato.startswith("down"):
            e.append(Esito(etichetta, ERRORE, f"fermo (porta {porta}): sudo systemctl start postgresql", G))
            continue
        if not ctx.root:
            e.append(Esito(etichetta, OK, f"attivo sulla porta {porta}; dettagli: {SERVE_ROOT}", G))
            continue
        v = {k: _psql(porta, q) for k, q in SQL_PG.items()}
        if v["versione"] is None:
            e.append(
                Esito(
                    etichetta, ATTENZIONE, f"attivo sulla porta {porta}, ma non interrogabile come utente postgres", G
                )
            )
            continue
        e.append(
            Esito(etichetta, OK, f"versione {v['versione']}, porta {porta}; {v['dimensioni'] or 'nessun database'}", G)
        )
        ascolto = v["ascolto"] or ""
        tutti = "*" in ascolto or "0.0.0.0" in ascolto
        e.append(
            Esito(
                f"{etichetta}: ascolto",
                ATTENZIONE if tutti else OK,
                f"listen_addresses = '{ascolto}'"
                + (" (su tutti gli indirizzi: proteggilo col firewall o usa 'localhost')" if tutti else ""),
                G,
            )
        )
        if v["connessioni"] and "/" in v["connessioni"]:
            usate, massime = (int(x) for x in v["connessioni"].split("/"))
            quota = usate / massime if massime else 0
            e.append(
                Esito(
                    f"{etichetta}: connessioni",
                    ERRORE if quota > 0.95 else ATTENZIONE if quota > 0.8 else OK,
                    f"{usate} su {massime}",
                    G,
                )
            )
        lente, bloccate = int(v["lente"] or 0), int(v["bloccate"] or 0)
        if lente or bloccate:
            e.append(
                Esito(
                    f"{etichetta}: query",
                    ATTENZIONE,
                    f"{lente} query attive da oltre 5 minuti, {bloccate} transazioni ferme da oltre 10",
                    G,
                )
            )
        eta = int(v["eta_xid"] or 0)
        if eta > 1_000_000_000:
            e.append(
                Esito(
                    f"{etichetta}: wraparound",
                    ERRORE if eta > 1_500_000_000 else ATTENZIONE,
                    f"età delle transazioni {eta:,}: serve un VACUUM (rischio blocco del database)",
                    G,
                )
            )
        if int(v["trust"] or 0):
            e.append(
                Esito(
                    f"{etichetta}: pg_hba.conf",
                    ERRORE,
                    f"{v['trust']} regole «trust» per indirizzi di rete: chiunque da lì entra senza password",
                    G,
                )
            )
        elif int(v["password_in_chiaro"] or 0):
            e.append(
                Esito(
                    f"{etichetta}: pg_hba.conf",
                    ATTENZIONE,
                    "metodo «password»: la password viaggia in chiaro (usa scram-sha-256)",
                    G,
                )
            )
    return e


def _mysql(sql: str) -> str | None:
    r = esegui(["mysql", "-NBe", sql], timeout=30)
    return r.testo.strip() if r.ok else None


def mysql(ctx: Contesto) -> list[Esito]:
    trovato = primo_servizio("mariadb", "mysql", "mysqld")
    if trovato is None:
        return []
    e = [esito_servizio("MySQL/MariaDB", *trovato, G)]
    if trovato[1].get("ActiveState") != "active":
        return e
    if not ctx.root:
        e.append(Esito("MySQL/MariaDB: dettagli", OK, SERVE_ROOT, G))
        return e
    versione = _mysql("SELECT VERSION()")
    if versione is None:
        e.append(Esito("MySQL/MariaDB: dettagli", ATTENZIONE, "non interrogabile come root via socket", G))
        return e
    ascolto = _mysql("SELECT @@bind_address") or "?"
    tutti = ascolto in ("*", "0.0.0.0", "::", "")
    e.append(
        Esito(
            "MySQL/MariaDB: versione e ascolto",
            ATTENZIONE if tutti else OK,
            f"{versione}, bind-address {ascolto or '(tutti)'}",
            G,
        )
    )
    stato = dict(
        (r.split("\t") + [""])[:2]
        for r in (_mysql("SHOW GLOBAL STATUS WHERE Variable_name IN ('Threads_connected')") or "").splitlines()
    )
    massime = int(_mysql("SELECT @@max_connections") or 0)
    usate = int(stato.get("Threads_connected") or 0)
    if massime:
        quota = usate / massime
        e.append(
            Esito(
                "MySQL/MariaDB: connessioni",
                ERRORE if quota > 0.95 else ATTENZIONE if quota > 0.8 else OK,
                f"{usate} su {massime}",
                G,
            )
        )
    anonimi = _mysql("SELECT count(*) FROM mysql.user WHERE user = ''")
    if anonimi and anonimi != "0":
        e.append(
            Esito("MySQL/MariaDB: utenti anonimi", ERRORE, f"{anonimi} utenti senza nome: mysql_secure_installation", G)
        )
    remoti = _mysql(
        "SELECT group_concat(host) FROM mysql.user "
        "WHERE user = 'root' AND host NOT IN ('localhost', '127.0.0.1', '::1')"
    )
    if remoti and remoti != "NULL":
        e.append(Esito("MySQL/MariaDB: root da remoto", ATTENZIONE, f"root può entrare da {remoti}", G))
    lente = _mysql("SELECT count(*) FROM information_schema.processlist WHERE command <> 'Sleep' AND time > 300")
    if lente and lente != "0":
        e.append(Esito("MySQL/MariaDB: query", ATTENZIONE, f"{lente} query attive da oltre 5 minuti", G))
    return e


def redis(ctx: Contesto) -> list[Esito]:
    trovato = primo_servizio("redis-server", "redis")
    if trovato is None:
        return []
    e = [esito_servizio("Redis", *trovato, G)]
    if trovato[1].get("ActiveState") != "active" or not presente("redis-cli"):
        return e
    r = esegui(["redis-cli", "--no-auth-warning", "CONFIG", "GET", "*"], timeout=15)
    if not r.ok or "NOAUTH" in r.testo:
        e.append(Esito("Redis: protezione", OK, "con password (configurazione non leggibile senza)", G))
        return e
    righe = r.testo.splitlines()
    conf = dict(zip(righe[::2], righe[1::2], strict=False))
    bind, protetto, password = conf.get("bind", ""), conf.get("protected-mode", "yes"), conf.get("requirepass", "")
    esposto = (not bind or "0.0.0.0" in bind or "*" in bind) and not password
    e.append(
        Esito(
            "Redis: protezione",
            ERRORE if esposto and protetto == "no" else ATTENZIONE if esposto else OK,
            f"bind {bind or '(tutti)'}, protected-mode {protetto}, password {'sì' if password else 'no'}",
            G,
        )
    )
    memoria = re.search(r"used_memory_human:(\S+)", esegui(["redis-cli", "INFO", "memory"], timeout=15).testo)
    if memoria:
        e.append(Esito("Redis: memoria", OK, memoria.group(1), G))
    return e


def controlli(ctx: Contesto) -> list[Esito]:
    return postgresql(ctx) + mysql(ctx) + redis(ctx)
