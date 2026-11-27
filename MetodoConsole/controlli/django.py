"""Area «django»: applicazioni Django servite senza container (gunicorn, uwsgi, daphne, uvicorn da systemd).

Si trovano da sole tra i servizi systemd attivi; in più (o al posto) si dichiarano in metodoconsole.conf:

    [app:metodo1]
    cartella = /root/Metodo            ; dove c'è manage.py
    python = /root/Metodo/venv/bin/python
    servizio = gunicorn_metodo         ; unità systemd
    url = https://metodo.esempio.it/   ; facoltativo
    utente = www-data                  ; con cui eseguire manage.py (di base quello del servizio)

Per ogni applicazione: servizio e riavvii, socket, risposta dell'indirizzo, ``manage.py check --deploy``
(DEBUG, SECRET_KEY, cookie sicuri, HSTS…), migrazioni non applicate, versione di Django e di Python con la
fine del supporto, permessi dei file con le password. ``manage.py`` gira con l'utente e l'ambiente del servizio.
"""

from __future__ import annotations

import os
import re
import shlex
import stat
from dataclasses import dataclass, field
from pathlib import Path

from .base import (
    ATTENZIONE,
    ERRORE,
    OK,
    SERVE_ROOT,
    Contesto,
    Esito,
    esegui,
    esito_servizio,
    fine_supporto,
    leggi,
    systemctl_show,
    unita_attive,
)
from .base import servizio as stato_servizio

G = "django"
SERVER_WSGI = ("gunicorn", "uwsgi", "daphne", "uvicorn", "waitress-serve")
FINE_DJANGO = {
    "3.2": "2024-04",
    "4.0": "2023-04",
    "4.1": "2023-12",
    "4.2": "2026-04",
    "5.0": "2025-04",
    "5.1": "2025-12",
    "5.2": "2028-04",
    "6.0": "2027-04",
}
FINE_PYTHON = {
    "3.7": "2023-06",
    "3.8": "2024-10",
    "3.9": "2025-10",
    "3.10": "2026-10",
    "3.11": "2027-10",
    "3.12": "2028-10",
    "3.13": "2029-10",
    "3.14": "2030-10",
}
# Avvisi di check --deploy che sono veri problemi (gli altri sono consigli).
GRAVI = {
    "security.W018": "DEBUG è attivo",
    "security.W009": "SECRET_KEY debole o di prova",
    "security.W020": "ALLOWED_HOSTS vuoto",
}


@dataclass
class App:
    nome: str
    cartella: Path | None = None
    python: str = ""
    servizio: str = ""
    url: str = ""
    utente: str = ""
    ambiente: dict[str, str] = field(default_factory=dict)
    socket: str = ""


def presente_area(ctx: Contesto) -> bool:
    return bool(ctx.sezioni("app")) or bool(ctx.memo("app_django", lambda: trova_app(ctx)))


def leggi_ambiente(testo: str) -> dict[str, str]:
    """File in stile EnvironmentFile / .env: KEY=valore, commenti e «export» ammessi."""
    out = {}
    for riga in testo.splitlines():
        riga = riga.strip()
        if not riga or riga.startswith("#") or "=" not in riga:
            continue
        chiave, valore = riga.removeprefix("export ").split("=", 1)
        valore = valore.strip()
        if len(valore) >= 2 and valore[0] == valore[-1] and valore[0] in "'\"":
            valore = valore[1:-1]
        out[chiave.strip()] = valore
    return out


def da_unita(unita: str) -> App | None:
    """Ricava cartella, python, utente, ambiente e socket di un'applicazione dal suo servizio systemd."""
    s = systemctl_show(unita, "ExecStart", "WorkingDirectory", "User", "Environment", "EnvironmentFiles")
    comando = s.get("ExecStart", "")
    m = re.search(r"argv\[\]=([^;]+)", comando)
    argomenti = shlex.split(m.group(1)) if m else []
    if not argomenti or not any(Path(a).name.startswith(SERVER_WSGI) for a in argomenti[:2]):
        return None
    eseguibile = Path(argomenti[0]) if Path(argomenti[0]).name.startswith(SERVER_WSGI) else Path(argomenti[1])
    python = eseguibile.parent / "python"
    cartella = Path(s["WorkingDirectory"]) if s.get("WorkingDirectory") and s["WorkingDirectory"] != "!/" else None
    for a in argomenti:
        if a.startswith("--chdir="):
            cartella = Path(a.split("=", 1)[1])
    if cartella is not None and not (cartella / "manage.py").exists():
        cartella = next((p for p in (cartella.parent, *cartella.glob("*/")) if (p / "manage.py").exists()), cartella)
    ambiente = {}
    for coppia in shlex.split(s.get("Environment", "")):
        if "=" in coppia:
            k, v = coppia.split("=", 1)
            ambiente[k] = v
    for f in re.findall(r"(/\S+) \(ignore_errors", s.get("EnvironmentFiles", "")):
        ambiente.update(leggi_ambiente(leggi(f)))
    sock = next((a.split("unix:", 1)[1] for a in argomenti if "unix:" in a), "")
    return App(
        nome=unita.removesuffix(".service"),
        cartella=cartella,
        python=str(python) if python.exists() else "",
        servizio=unita,
        utente=s.get("User", ""),
        ambiente=ambiente,
        socket=sock,
    )


def trova_app(ctx: Contesto) -> list[App]:
    app: dict[str, App] = {}
    for unita in unita_attive():
        a = da_unita(unita)
        if a is not None and a.cartella is not None:
            app[a.nome] = a
    for nome, v in ctx.sezioni("app").items():
        base = da_unita(v["servizio"]) if v.get("servizio") else None
        a = base or App(nome=nome)
        a.nome = nome
        a.cartella = Path(v["cartella"]) if v.get("cartella") else a.cartella
        a.python = v.get("python") or a.python or (str(a.cartella / "venv/bin/python") if a.cartella else "")
        a.servizio = v.get("servizio") or a.servizio
        a.url = v.get("url", "")
        a.utente = v.get("utente") or a.utente
        app.pop(a.servizio.removesuffix(".service"), None)
        app[nome] = a
    return list(app.values())


def manage(a: App, *argomenti: str, timeout: float = 120) -> tuple[int, str]:
    ambiente = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8", **a.ambiente}
    cmd = [a.python or "python3", "manage.py", *argomenti]
    if a.utente and a.utente != "root" and os.geteuid() == 0:
        cmd = ["runuser", "-u", a.utente, "--", *cmd]
    r = esegui(cmd, timeout, ambiente=ambiente, cartella=str(a.cartella))
    return r.codice, (r.testo + r.errore).strip()


def leggi_check(testo: str) -> tuple[list[str], int]:
    """(codici, numero di problemi) dall'uscita di ``check --deploy``."""
    codici = re.findall(r"\(([a-z_]+\.[EW]\d+)\)", testo)
    m = re.search(r"identified (\d+) issue", testo)
    return codici, int(m.group(1)) if m else len(codici)


def controlla_app(a: App, ctx: Contesto) -> list[Esito]:
    e: list[Esito] = []
    n = a.nome
    if a.servizio:
        stato = stato_servizio(a.servizio)
        if stato is None:
            e.append(Esito(f"{n}: servizio", ERRORE, f"{a.servizio} non esiste", G))
        else:
            e.append(esito_servizio(f"{n}: servizio", a.servizio, stato, G))
    if a.socket:
        p = Path(a.socket)
        ok = p.exists() and stat.S_ISSOCK(p.stat().st_mode)
        e.append(
            Esito(
                f"{n}: socket",
                OK if ok else ERRORE,
                a.socket if ok else f"{a.socket} mancante: nginx non riesce a raggiungere l'applicazione",
                G,
            )
        )
    if a.url:
        from .web import controlla_sito

        e += [Esito(x.nome.replace("Sito ", f"{n}: sito "), x.esito, x.dettaglio, G) for x in controlla_sito(a.url)[:2]]
    if a.cartella is None or not (a.cartella / "manage.py").exists():
        e.append(Esito(f"{n}: progetto", ATTENZIONE, "manage.py non trovato: indica [app:NOME] cartella", G))
        return e
    if not ctx.root and a.utente and a.utente != os.environ.get("USER"):
        e.append(Esito(f"{n}: controlli Django", OK, SERVE_ROOT, G))
        return e

    codice, testo = manage(a, "check", "--deploy")
    codici, problemi = leggi_check(testo)
    if codice != 0 and not codici:
        e.append(
            Esito(
                f"{n}: check --deploy",
                ATTENZIONE,
                f"non eseguibile: {testo.splitlines()[-1][:200] if testo else codice}",
                G,
            )
        )
    else:
        gravi = [GRAVI[c] for c in codici if c in GRAVI]
        livello = ERRORE if gravi or any(".E" in c for c in codici) else ATTENZIONE if problemi else OK
        dettaglio = (
            "; ".join(gravi)
            + ("; " if gravi else "")
            + (
                f"{problemi} avvisi ({', '.join(sorted(set(codici))[:8])}): manage.py check --deploy"
                if problemi
                else "nessun problema"
            )
        )
        e.append(Esito(f"{n}: check --deploy", livello, dettaglio, G))

    codice, testo = manage(a, "showmigrations", "--plan")
    if codice == 0:
        mancanti = len(re.findall(r"^\[ \]", testo, re.M))
        e.append(
            Esito(
                f"{n}: migrazioni",
                ATTENZIONE if mancanti else OK,
                f"{mancanti} non applicate: manage.py migrate" if mancanti else "tutte applicate",
                G,
            )
        )

    r = esegui(
        [
            a.python or "python3",
            "-c",
            "import sys, django; print(django.get_version()); print('%d.%d' % sys.version_info[:2])",
        ],
        30,
        cartella=str(a.cartella),
    )
    if r.ok and len(r.testo.split()) >= 2:
        versione, py = r.testo.split()[:2]
        principale = ".".join(versione.split(".")[:2])
        livello, testo = fine_supporto(FINE_DJANGO.get(principale), ctx.oggi, f"Django {principale}")
        e.append(Esito(f"{n}: Django", livello, f"{versione}" + (f" — {testo}" if testo else ""), G))
        livello, testo = fine_supporto(FINE_PYTHON.get(py), ctx.oggi, f"Python {py}")
        e.append(Esito(f"{n}: Python", livello, f"{py}" + (f" — {testo}" if testo else ""), G))

    esposti = []
    for nome in (".env", "local_settings.py", "settings_local.py", "secrets.json"):
        for f in [a.cartella / nome, *a.cartella.glob(f"*/{nome}")][:3]:
            if f.exists() and f.stat().st_mode & stat.S_IROTH:
                esposti.append(str(f))
    if esposti:
        e.append(
            Esito(
                f"{n}: file con le password",
                ATTENZIONE,
                f"leggibili da tutti gli utenti: {', '.join(esposti)} (chmod o-rwx)",
                G,
            )
        )
    return e


def controlli(ctx: Contesto) -> list[Esito]:
    app = ctx.memo("app_django", lambda: trova_app(ctx))
    if not app:
        return [
            Esito("Applicazioni Django", OK, "nessuna trovata (dichiarale con [app:NOME] in metodoconsole.conf)", G)
        ]
    e: list[Esito] = []
    for a in app:
        e += controlla_app(a, ctx)
    return e
