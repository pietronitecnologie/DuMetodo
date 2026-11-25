"""MetodoConsole: interfaccia a schermo intero (Textual), da console o via SSH.

    metodoconsole                 (in un terminale; oppure «metodoconsole pannello»)
    ssh -t server metodoconsole

Schede (tasti 1-8): Panoramica · Docker · Log · Sicurezza · Disco · Backup · Diagnostica · Aggiornamenti.
Ovunque: A avvia, F ferma, R riavvia Metodo v2 · b backup · Ctrl+R aggiorna · q esci.

Le letture lente girano in thread separati: l'interfaccia non si blocca mai. Le operazioni lunghe o che chiedono
conferme (avvio, backup, ripristino, apt, freshclam…) sospendono l'interfaccia e mostrano l'output normale nel
terminale; con Invio si torna. Le azioni che cambiano il server chiedono sempre conferma.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import socket
import subprocess
import time
from collections.abc import Callable
from typing import Any

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Log,
    Select,
    Static,
    Switch,
    TabbedContent,
    TabPane,
)

from . import sistema as s

COLORE = {s.OK: "green", s.ATTENZIONE: "yellow", s.AVVIO: "yellow", s.ERRORE: "red", s.FERMO: "blue"}
SIMBOLO = {s.OK: "●", s.AVVIO: "◐", s.ERRORE: "✖", s.FERMO: "○", s.ATTENZIONE: "▲"}
ETICHETTA = {s.OK: "OK", s.ATTENZIONE: "ATTENZIONE", s.ERRORE: "ERRORE"}
SCHEDE = ("panoramica", "docker", "log", "sicurezza", "disco", "backup", "diagnostica", "aggiornamenti")


def livello(testo: str, liv: str) -> Text:
    return Text(testo, style=COLORE.get(liv, ""))


def con_barra(frazione: float, larghezza: int = 18) -> Text:
    liv = s.ERRORE if frazione >= 0.9 else s.ATTENZIONE if frazione >= 0.8 else s.OK
    return Text.assemble((s.barra(frazione, larghezza), COLORE[liv]), f" {frazione:4.0%}")


def admin(cmd: list[str]) -> list[str]:
    """Le operazioni di sistema chiedono root: senza, si passa da sudo (che chiede la password nel terminale)."""
    return cmd if s.root() or not s.presente("sudo") else ["sudo", *cmd]


def chiave_scelta(tabella: DataTable) -> str | None:
    if not tabella.row_count:
        return None
    try:
        return tabella.coordinate_to_cell_key(tabella.cursor_coordinate).row_key.value
    except Exception:
        return None


# ================================================================================================ finestre
class Conferma(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "no", "Annulla"), Binding("s", "si", "Sì", show=False)]

    def __init__(self, domanda: str, pericolosa: bool = False) -> None:
        super().__init__()
        self.domanda, self.pericolosa = domanda, pericolosa

    def compose(self) -> ComposeResult:
        with Vertical(classes="finestra"):
            yield Static(self.domanda, classes="domanda")
            with Horizontal(classes="pulsanti"):
                yield Button("Sì, procedi", id="si", variant="error" if self.pericolosa else "primary")
                yield Button("No", id="no")

    def on_mount(self) -> None:
        self.query_one("#no", Button).focus()

    @on(Button.Pressed)
    def scelta(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "si")

    def action_no(self) -> None:
        self.dismiss(False)

    def action_si(self) -> None:
        self.dismiss(True)


class Domanda(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "annulla", "Annulla")]

    def __init__(self, domanda: str, valore: str = "", segnaposto: str = "") -> None:
        super().__init__()
        self.domanda, self.valore, self.segnaposto = domanda, valore, segnaposto

    def compose(self) -> ComposeResult:
        with Vertical(classes="finestra"):
            yield Static(self.domanda, classes="domanda")
            yield Input(value=self.valore, placeholder=self.segnaposto, id="risposta")
            with Horizontal(classes="pulsanti"):
                yield Button("OK", id="ok", variant="primary")
                yield Button("Annulla", id="annulla")

    @on(Input.Submitted)
    def invio(self) -> None:
        self.dismiss(self.query_one("#risposta", Input).value.strip())

    @on(Button.Pressed)
    def scelta(self, event: Button.Pressed) -> None:
        self.dismiss(self.query_one("#risposta", Input).value.strip() if event.button.id == "ok" else None)

    def action_annulla(self) -> None:
        self.dismiss(None)


class Visore(ModalScreen[None]):
    """Testo lungo (log di un container, dettagli, registro): segui, solo errori, cerca."""

    BINDINGS = [
        Binding("escape,q", "chiudi", "Chiudi"),
        Binding("f", "segui", "Segui"),
        Binding("e", "errori", "Solo errori"),
        Binding("slash", "cerca", "Cerca"),
    ]

    def __init__(self, titolo: str, carica: Callable[[], list[str]], segui: bool = False) -> None:
        super().__init__()
        self.titolo, self.carica, self.segui = titolo, carica, segui
        self.solo_errori, self.cerca = False, ""
        self.righe: list[str] = []

    def compose(self) -> ComposeResult:
        with Vertical(classes="visore"):
            yield Label(self.titolo, id="titolo-visore")
            yield Log(highlight=True, max_lines=20000, id="testo")
            yield Footer()

    def on_mount(self) -> None:
        self.leggi()
        self.set_interval(2.5, lambda: self.segui and self.leggi())

    @work(thread=True, exclusive=True)
    def leggi(self) -> None:
        righe = self.carica()
        self.app.call_from_thread(self.mostra, righe)

    def mostra(self, righe: list[str] | None = None) -> None:
        if righe is not None:
            self.righe = righe
        vista = s.filtra(s.righe_di_errore(self.righe) if self.solo_errori else self.righe, self.cerca)
        log = self.query_one("#testo", Log)
        log.clear()
        log.write_lines(vista)
        stato = " · ".join(
            x
            for x in (
                "segue" if self.segui else "",
                "solo errori" if self.solo_errori else "",
                f"cerca «{self.cerca}»" if self.cerca else "",
                f"{len(vista)} righe",
            )
            if x
        )
        self.query_one("#titolo-visore", Label).update(f"{self.titolo} · {stato}")

    def action_chiudi(self) -> None:
        self.dismiss(None)

    def action_segui(self) -> None:
        self.segui = not self.segui
        self.leggi()

    def action_errori(self) -> None:
        self.solo_errori = not self.solo_errori
        self.mostra()

    def action_cerca(self) -> None:
        def fatto(testo: str | None) -> None:
            if testo is not None:
                self.cerca = testo
                self.mostra()

        self.app.push_screen(Domanda("Cerca nel testo (vuoto = tutto):", self.cerca), fatto)


# ================================================================================================ schede
class Riquadro(Static):
    def __init__(self, titolo: str, **kw: Any) -> None:
        super().__init__("…", classes="riquadro", **kw)
        self.border_title = titolo


class Panoramica(VerticalScroll):
    def compose(self) -> ComposeResult:
        with Grid(id="griglia"):
            yield Riquadro("Server", id="r-server")
            yield Riquadro("Metodo v2", id="r-metodo")
            yield Riquadro("Memoria e dischi", id="r-dischi")
            yield Riquadro("Sicurezza", id="r-sicurezza")
        yield Label("Avvisi", classes="sezione")
        yield DataTable(id="avvisi", cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        self.query_one("#avvisi", DataTable).add_columns("", "Cosa succede e cosa fare")


class SchedaDocker(Vertical):
    BINDINGS = [
        Binding("a", "container('start')", "Avvia"),
        Binding("f", "container('stop')", "Ferma"),
        Binding("r", "container('restart')", "Riavvia"),
        Binding("l", "log", "Log"),
        Binding("i", "dettagli", "Dettagli"),
        Binding("c", "console", "Console"),
    ]

    def compose(self) -> ComposeResult:
        with Horizontal(classes="comandi"):
            yield Button("Avvia", id="d-start")
            yield Button("Ferma", id="d-stop", variant="warning")
            yield Button("Riavvia", id="d-restart")
            yield Button("Log", id="d-log", variant="primary")
            yield Button("Dettagli", id="d-dettagli")
            yield Button("Console", id="d-console")
            yield Label("CPU e memoria", classes="etichetta")
            yield Switch(value=False, id="d-stats")
            yield Label("Altri progetti", classes="etichetta")
            yield Switch(value=True, id="d-altri")
        yield DataTable(id="container", cursor_type="row", zebra_stripes=True)
        with TabbedContent(id="docker-sotto"):
            with TabPane("Spazio usato", id="t-df"):
                with Horizontal(classes="comandi"):
                    yield Button("Pulisci immagini e cache inutilizzate", id="d-pulisci", variant="warning")
                yield DataTable(id="df", cursor_type="row")
            with TabPane("Immagini", id="t-immagini"):
                yield DataTable(id="immagini", cursor_type="row", zebra_stripes=True)
            with TabPane("Volumi", id="t-volumi"):
                yield DataTable(id="volumi", cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        self.query_one("#container", DataTable).add_columns(
            "", "Servizio / container", "Stato", "Porte", "CPU", "Memoria", "Immagine"
        )
        self.query_one("#df", DataTable).add_columns("Tipo", "Totale", "In uso", "Spazio", "Recuperabile")
        self.query_one("#immagini", DataTable).add_columns("Immagine", "Tag", "Dimensione", "Creata")
        self.query_one("#volumi", DataTable).add_columns("Volume", "Progetto", "Driver")

    def action_container(self, verbo: str) -> None:
        self.app.azione_container(verbo)  # type: ignore[attr-defined]

    def action_log(self) -> None:
        self.app.log_container()  # type: ignore[attr-defined]

    def action_dettagli(self) -> None:
        self.app.dettagli_container()  # type: ignore[attr-defined]

    def action_console(self) -> None:
        self.app.console_container()  # type: ignore[attr-defined]


SORGENTI_LOG = [
    ("Tutti i servizi di Metodo v2", "tutti"),
    *[(f"Servizio {x}", x) for x in s.ORDINE_SERVIZI],
    ("Sistema: avvisi ed errori (journal)", "journal"),
    ("Controlli automatici di MetodoConsole", "metodoconsole"),
    ("Antivirus: ultima scansione", "antivirus"),
]


class SchedaLog(Vertical):
    def compose(self) -> ComposeResult:
        with Horizontal(classes="comandi"):
            yield Select(SORGENTI_LOG, value="tutti", allow_blank=False, id="l-sorgente")
            yield Label("Segui", classes="etichetta")
            yield Switch(value=True, id="l-segui")
            yield Label("Solo errori", classes="etichetta")
            yield Switch(value=False, id="l-errori")
            yield Input(placeholder="cerca…", id="l-cerca")
        yield Log(highlight=True, max_lines=20000, id="l-testo")


class SchedaSicurezza(Vertical):
    def compose(self) -> ComposeResult:
        with Horizontal(classes="comandi"):
            yield Button("Ricontrolla", id="s-ricontrolla", variant="primary")
            yield Static("", id="s-esito", classes="esito")
        yield DataTable(id="controlli", cursor_type="row", zebra_stripes=True)
        with TabbedContent(id="sicurezza-sotto"):
            with TabPane("Firewall", id="t-firewall"):
                yield Static("", id="fw-stato")
                with Horizontal(classes="comandi"):
                    yield Button("Attiva", id="fw-attiva", variant="success")
                    yield Button("Disattiva", id="fw-disattiva", variant="error")
                    yield Button("Consenti porta", id="fw-consenti")
                    yield Button("Blocca IP", id="fw-blocca")
                    yield Button("Elimina regola", id="fw-elimina", variant="warning")
                    yield Button("Installa ufw", id="fw-installa")
                yield DataTable(id="fw-regole", cursor_type="row", zebra_stripes=True)
            with TabPane("Antivirus", id="t-antivirus"):
                yield Static("", id="av-stato")
                with Horizontal(classes="comandi"):
                    yield Button("Scansiona", id="av-scansiona", variant="primary")
                    yield Button("Aggiorna firme", id="av-firme")
                    yield Button("Log della scansione", id="av-log")
                    yield Button("Installa ClamAV", id="av-installa")
            with TabPane("Accessi", id="t-accessi"):
                yield Static("", id="ac-stato")
                with Horizontal(classes="comandi"):
                    yield Button("Blocca l'IP scelto", id="ac-blocca", variant="warning")
                    yield Button("Sblocca IP", id="ac-sblocca")
                    yield Button("Installa fail2ban", id="ac-installa")
                yield DataTable(id="ac-ip", cursor_type="row", zebra_stripes=True)
            with TabPane("Porte in ascolto", id="t-porte"):
                yield DataTable(id="porte", cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        self.query_one("#controlli", DataTable).add_columns("Esito", "Controllo", "Dettaglio", "Cosa fare")
        self.query_one("#fw-regole", DataTable).add_columns("Regola")
        self.query_one("#ac-ip", DataTable).add_columns("IP", "Accessi falliti (24 ore)", "Bloccato da fail2ban")
        self.query_one("#porte", DataTable).add_columns("Protocollo", "Indirizzo", "Porta", "Processo", "Raggiungibile")


class SchedaDisco(Vertical):
    def compose(self) -> ComposeResult:
        yield DataTable(id="dischi", cursor_type="row")
        with Horizontal(classes="comandi"):
            yield Button("Pulisci Docker", id="k-docker", variant="warning")
            yield Button("Riduci il registro di sistema", id="k-journal")
            yield Button("Pulisci la cache di apt", id="k-apt")
            yield Button("Ruota i backup", id="k-ruota")
        yield DataTable(id="occupazione", cursor_type="row")
        with Horizontal(classes="comandi"):
            yield Input(value="/", placeholder="cartella da analizzare", id="k-percorso")
            yield Button("Analizza cartella", id="k-analizza", variant="primary")
        yield DataTable(id="cartelle", cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        self.query_one("#dischi", DataTable).add_columns("Disco", "Montato su", "Usato", "Libero", "Totale", "")
        self.query_one("#occupazione", DataTable).add_columns("Chi occupa spazio", "Spazio", "Recuperabile")
        self.query_one("#cartelle", DataTable).add_columns("Dimensione", "Cartella (Invio per entrare)")


class SchedaBackup(Vertical):
    def compose(self) -> ComposeResult:
        yield Static("", id="b-info")
        with Horizontal(classes="comandi"):
            yield Button("Nuovo backup", id="b-nuovo", variant="primary")
            yield Button("Verifica", id="b-verifica")
            yield Button("Dettagli", id="b-dettagli")
            yield Button("Ripristina", id="b-ripristina", variant="error")
        yield DataTable(id="tabella-backup", cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        self.query_one("#tabella-backup", DataTable).add_columns(
            "Backup", "Data", "Età", "Dimensione", "Versione", "Nota"
        )


class SchedaDiagnostica(Vertical):
    def compose(self) -> ComposeResult:
        with Horizontal(classes="comandi"):
            yield Button("Esegui la diagnostica", id="g-esegui", variant="primary")
            yield Static("", id="g-esito", classes="esito")
        yield DataTable(id="tabella-diagnostica", cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        self.query_one("#tabella-diagnostica", DataTable).add_columns("Gruppo", "Esito", "Controllo", "Dettaglio")


class SchedaAggiornamenti(Vertical):
    def compose(self) -> ComposeResult:
        yield Static("", id="u-stato")
        with Horizontal(classes="comandi"):
            yield Button("Aggiorna il sistema", id="u-sistema", variant="primary")
            yield Button("Attiva aggiornamenti automatici", id="u-automatici")
            yield Button("Aggiorna Metodo v2", id="u-metodo")
            yield Button("Riavvia il server", id="u-riavvio", variant="error")
        yield DataTable(id="pacchetti", cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        self.query_one("#pacchetti", DataTable).add_columns("Pacchetto", "Nuova versione", "Sicurezza")


# ================================================================================================ applicazione
class MetodoConsole(App[None]):
    TITLE = "MetodoConsole"
    CSS = """
    #avviso-root { background: $warning 30%; color: $text; padding: 0 1; height: auto; }
    #griglia { grid-size: 2; grid-gutter: 0 1; height: auto; grid-rows: auto; }
    .riquadro { border: round $primary; padding: 0 1; height: auto; min-height: 7; }
    .sezione { text-style: bold; margin: 1 0 0 1; }
    .comandi { height: auto; padding: 0 0 0 1; }
    .comandi Button { margin: 0 1 0 0; min-width: 10; }
    .comandi Input { width: 40; }
    .comandi Select { width: 46; }
    .etichetta { padding: 1 0 0 2; }
    .esito { padding: 1 2; width: 1fr; }
    DataTable { height: auto; max-height: 20; margin: 0 1; }
    #container { max-height: 16; }
    #l-testo { height: 1fr; margin: 0 1; border: round $panel; }
    TabbedContent#docker-sotto, TabbedContent#sicurezza-sotto { height: auto; margin-top: 1; }
    Conferma, Domanda { align: center middle; background: $background 60%; }
    .finestra { width: 70; height: auto; border: thick $primary; background: $panel; padding: 1 2; }
    .domanda { margin-bottom: 1; }
    .pulsanti { height: auto; align-horizontal: right; }
    .pulsanti Button { margin-left: 1; }
    Visore { background: $surface; }
    .visore { height: 1fr; }
    #titolo-visore { background: $primary; color: $text; width: 1fr; padding: 0 1; }
    #testo { height: 1fr; }
    """
    BINDINGS = [
        Binding("q", "quit", "Esci"),
        *[Binding(str(n), f"scheda('{nome}')", nome.capitalize(), show=False) for n, nome in enumerate(SCHEDE, 1)],
        Binding("A", "metodo('avvia')", "Avvia tutto"),
        Binding("F", "metodo('ferma')", "Ferma tutto"),
        Binding("R", "metodo('riavvia')", "Riavvia tutto"),
        Binding("b", "nuovo_backup", "Backup"),
        Binding("ctrl+r", "aggiorna", "Aggiorna"),
    ]

    def __init__(self, conf: Any, dc: Any, mc: Any) -> None:
        super().__init__()
        self.conf, self.dc, self.mc = conf, dc, mc
        self.progetto = ""
        self.container: list[s.Container] = []
        self.statistiche_testo = ""
        self.ultimi_controlli: list[s.Controllo] = []
        self.sospeso = False  # True mentre l'interfaccia è sospesa

    # ------------------------------------------------------------------------------------------------ struttura
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        if not s.root():
            yield Static(
                "Senza root alcune informazioni (firewall, SSH, porte) non si leggono e le azioni di sistema "
                "passano da sudo. Per tutto: sudo metodoconsole",
                id="avviso-root",
            )
        with TabbedContent(id="schede", initial="panoramica"):
            with TabPane("1 Panoramica", id="panoramica"):
                yield Panoramica()
            with TabPane("2 Docker", id="docker"):
                yield SchedaDocker()
            with TabPane("3 Log", id="log"):
                yield SchedaLog()
            with TabPane("4 Sicurezza", id="sicurezza"):
                yield VerticalScroll(SchedaSicurezza())
            with TabPane("5 Disco", id="disco"):
                yield VerticalScroll(SchedaDisco())
            with TabPane("6 Backup", id="backup"):
                yield SchedaBackup()
            with TabPane("7 Diagnostica", id="diagnostica"):
                yield SchedaDiagnostica()
            with TabPane("8 Aggiornamenti", id="aggiornamenti"):
                yield SchedaAggiornamenti()
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = (
            f"Metodo v2 {self.mc.versione_codice(self.conf)} su {socket.gethostname()} · {self.conf.cartella}"
        )
        self.leggi_progetto()
        self.set_interval(3, self.aggiorna_periodico)
        self.leggi_sicurezza()  # serve anche alla Panoramica

    def scheda_attiva(self) -> str:
        return self.query_one("#schede", TabbedContent).active

    def action_scheda(self, nome: str) -> None:
        self.query_one("#schede", TabbedContent).active = nome

    @on(TabbedContent.TabActivated, "#schede")
    def cambio_scheda(self, event: TabbedContent.TabActivated) -> None:
        self.aggiorna_scheda(event.pane.id or "", prima_volta=True)

    def aggiorna_periodico(self) -> None:
        if self.sospeso:
            return
        attiva = self.scheda_attiva()
        if attiva in ("panoramica", "docker"):
            self.aggiorna_scheda(attiva)
        elif attiva == "log" and self.query_one("#l-segui", Switch).value:
            self.leggi_log()

    def action_aggiorna(self) -> None:
        self.aggiorna_scheda(self.scheda_attiva(), prima_volta=True, forza=True)
        self.notify("Aggiornato.")

    def aggiorna_scheda(self, nome: str, prima_volta: bool = False, forza: bool = False) -> None:
        if nome == "panoramica":
            self.leggi_panoramica()
        elif nome == "docker":
            self.leggi_container()
            if prima_volta:
                self.leggi_docker_spazio()
        elif nome == "log":
            self.leggi_log()
        elif nome == "sicurezza" and (prima_volta or forza):
            self.leggi_sicurezza()
        elif nome == "disco":
            self.leggi_disco()
        elif nome == "backup":
            self.leggi_backup()
        elif nome == "diagnostica" and (forza or not self.query_one("#tabella-diagnostica", DataTable).row_count):
            self.esegui_diagnostica()
        elif nome == "aggiornamenti":
            self.leggi_aggiornamenti()

    # ------------------------------------------------------------------------------------------------ utilità
    def nel_terminale(self, titolo: str, funzione: Callable[[], Any]) -> None:
        """Sospende l'interfaccia, esegue con l'output normale e torna dopo Invio."""
        self.sospeso = True
        try:
            with self.suspend():
                print(f"\n=== {titolo} ===\n", flush=True)
                esito = None
                try:
                    esito = funzione()
                except self.mc.Errore as exc:
                    print(f"\nErrore: {exc}")
                except KeyboardInterrupt:
                    print("\nInterrotto.")
                if isinstance(esito, int) and esito:
                    print(f"\nTerminato con codice {esito}.")
                with contextlib.suppress(EOFError, KeyboardInterrupt):
                    input("\nPremi Invio per tornare a MetodoConsole… ")
        finally:
            self.sospeso = False
        self.aggiorna_scheda(self.scheda_attiva(), prima_volta=True, forza=True)

    def shell(self, titolo: str, comando: str) -> None:
        """Comandi di sistema (apt, ufw, systemctl…) con root, nel terminale."""
        self.nel_terminale(titolo, lambda: subprocess.run(admin(["sh", "-c", comando])).returncode)

    def conferma(self, domanda: str, azione: Callable[[], Any], pericolosa: bool = False) -> None:
        self.push_screen(Conferma(domanda, pericolosa), lambda ok: azione() if ok else None)

    def chiedi(self, domanda: str, azione: Callable[[str], Any], valore: str = "", segnaposto: str = "") -> None:
        self.push_screen(Domanda(domanda, valore, segnaposto), lambda v: azione(v) if v is not None else None)

    def ns(self, **kw: Any) -> Any:
        return self.mc.argparse.Namespace(**kw)

    @work(thread=True)
    def leggi_progetto(self) -> None:
        self.progetto = s.progetto_compose(self.dc)
        self.call_from_thread(self.aggiorna_scheda, self.scheda_attiva(), True)

    # ------------------------------------------------------------------------------------------------ Metodo v2
    def action_metodo(self, azione: str) -> None:
        if azione == "avvia":
            self.nel_terminale(
                "Avvio di Metodo v2",
                lambda: self.mc.cmd_avvia(self.conf, self.dc, self.ns(ricostruisci=False, servizi=[])),
            )
        elif azione == "ferma":
            self.conferma(
                "Fermare tutto Metodo v2? Nessuno potrà lavorare finché non lo riavvii.",
                lambda: self.nel_terminale(
                    "Arresto di Metodo v2", lambda: self.mc.cmd_ferma(self.conf, self.dc, self.ns(servizi=[]))
                ),
                pericolosa=True,
            )
        else:
            self.conferma(
                "Riavviare tutto Metodo v2? Gli utenti collegati vedranno un'interruzione breve.",
                lambda: self.nel_terminale(
                    "Riavvio di Metodo v2", lambda: self.mc.cmd_riavvia(self.conf, self.dc, self.ns(servizi=[]))
                ),
            )

    def action_nuovo_backup(self) -> None:
        def con_nota(nota: str) -> None:
            self.nel_terminale(
                "Backup",
                lambda: self.mc.cmd_backup(
                    self.conf, self.dc, self.ns(nota=nota or None, solo_database=False, avvisa=False)
                ),
            )

        self.chiedi(
            "Nuovo backup di database, file e .env. Nota (facoltativa):", con_nota, segnaposto="es. prima-di-aggiornare"
        )

    # ------------------------------------------------------------------------------------------------ panoramica
    @work(thread=True, exclusive=True, group="panoramica")
    def leggi_panoramica(self) -> None:
        r = s.leggi_risorse()
        codice, testo = s.docker("info", "--format", "{{.ServerVersion}}|{{.DockerRootDir}}")
        versione_docker, radice = (testo.strip().split("|", 1) + [""])[:2] if codice == 0 else ("", "")
        dischi = s.leggi_dischi(
            [
                ("sistema", "/"),
                ("Docker", radice or "/var/lib/docker"),
                ("backup", str(self.conf.backup_cartella)),
                ("Metodo v2", str(self.conf.cartella)),
            ]
        )
        codice_ps, ps = s.docker("ps", "--all", "--no-trunc", "--format", "json")
        elenco = s.ordina(s.leggi_container(ps), self.progetto) if codice_ps == 0 else []
        backup = self.mc.elenca_backup(self.conf)
        eta = (dt.datetime.now() - backup[0].quando).total_seconds() / 3600 if backup else None
        timer = [t for t in (s.stato_timer(n) for n in s.TIMER) if t]
        avvisi = s.avvisi(r, dischi, elenco, self.progetto, eta, self.conf.backup_massima_eta_ore, timer)
        if codice_ps != 0:
            avvisi.insert(0, (s.ERRORE, "Docker non risponde: systemctl status docker"))
        self.call_from_thread(self.mostra_panoramica, r, versione_docker, dischi, elenco, backup, eta, timer, avvisi)

    def mostra_panoramica(self, r, versione_docker, dischi, elenco, backup, eta, timer, avvisi) -> None:  # type: ignore[no-untyped-def]
        server = Text()
        server.append(f"{socket.gethostname()}\n", style="bold")
        server.append(f"{s.sistema_operativo()} · kernel {os.uname().release}\n")
        server.append(f"Acceso da {s.durata(r.acceso_da)}\n")
        server.append(f"Carico {r.carico[0]:.2f} {r.carico[1]:.2f} {r.carico[2]:.2f} su {r.cpu} CPU  ")
        server.append_text(con_barra(min(1.0, r.carico[1] / max(1, r.cpu)), 12))
        server.append(f"\nDocker {versione_docker or 'non risponde'}")
        self.query_one("#r-server", Static).update(server)

        metodo = Text()
        propri = [c for c in elenco if c.progetto == self.progetto]
        if not propri:
            metodo.append("Non avviato (A per avviare)\n", style="red")
        for c in propri:
            st = s.stato(c)
            metodo.append(f"{SIMBOLO[st]} {c.servizio:<8}", style=COLORE[st])
            metodo.append(f" {c.dettaglio[:38]}\n")
        if backup:
            liv = s.ERRORE if eta and eta > self.conf.backup_massima_eta_ore else s.OK
            metodo.append("Ultimo backup: ")
            metodo.append(f"{s.durata((eta or 0) * 3600)} fa", style=COLORE[liv])
            metodo.append(f" ({s.misura(backup[0].dimensione)})\n")
        else:
            metodo.append("Nessun backup\n", style="red")
        for t in timer:
            metodo.append(
                f"{'●' if t.attivo else '○'} {t.nome.removeprefix('metodoconsole-')}: ",
                style=COLORE[s.OK if t.attivo else s.ATTENZIONE],
            )
            metodo.append(f"prossimo {t.prossimo or '—'}, ultimo {t.ultimo or '—'} {t.esito}\n")
        self.query_one("#r-metodo", Static).update(metodo)

        mem = Text()
        mem.append("Memoria  ")
        mem.append_text(con_barra(r.memoria_usata))
        mem.append(f"  {s.misura(r.memoria_totale - r.memoria_disponibile)} di {s.misura(r.memoria_totale)}\n")
        if r.swap_totale:
            mem.append("Swap     ")
            mem.append_text(con_barra(r.swap_usato))
            mem.append(f"  {s.misura(r.swap_totale - r.swap_libero)} di {s.misura(r.swap_totale)}\n")
        for d in dischi:
            mem.append(f"Disco {d.etichetta}\n  ")
            mem.append_text(con_barra(d.frazione))
            mem.append(f"  {s.misura(d.libero)} liberi di {s.misura(d.totale)}\n")
        self.query_one("#r-dischi", Static).update(mem)

        self.mostra_riepilogo_sicurezza()
        tabella = self.query_one("#avvisi", DataTable)
        tabella.clear()
        for liv, testo in avvisi:
            tabella.add_row(livello(SIMBOLO.get(liv, "•"), liv), livello(testo, liv) if liv != s.OK else testo)

    def mostra_riepilogo_sicurezza(self) -> None:
        sic = Text()
        if not self.ultimi_controlli:
            sic.append("Controllo in corso…")
        for c in self.ultimi_controlli:
            if c.livello == s.OK and c.nome not in ("Firewall", "Antivirus", "Fail2ban", "Aggiornamenti"):
                continue
            sic.append(f"{SIMBOLO.get(c.livello, '•')} {c.nome}: ", style=COLORE.get(c.livello, ""))
            sic.append(f"{c.dettaglio[:48]}\n")
        self.query_one("#r-sicurezza", Static).update(sic)

    # ------------------------------------------------------------------------------------------------ docker
    @work(thread=True, exclusive=True, group="container")
    def leggi_container(self) -> None:
        codice, testo = s.docker("ps", "--all", "--no-trunc", "--format", "json")
        if codice != 0:
            self.call_from_thread(self.notify, "Docker non risponde.", severity="error")
            return
        elenco = s.ordina(s.leggi_container(testo), self.progetto)
        if self.query_one("#d-stats", Switch).value:
            self.statistiche_testo = s.docker("stats", "--no-stream", "--format", "json", timeout=60)[1]
            s.unisci_statistiche(elenco, self.statistiche_testo)
        self.call_from_thread(self.mostra_container, elenco)

    def mostra_container(self, elenco: list[s.Container]) -> None:
        if not self.query_one("#d-altri", Switch).value:
            elenco = [c for c in elenco if c.progetto == self.progetto]
        self.container = elenco
        tabella = self.query_one("#container", DataTable)
        scelto = chiave_scelta(tabella)
        tabella.clear()
        for c in elenco:
            st = s.stato(c)
            nome = c.servizio if c.progetto == self.progetto else f"{c.nome}  (altro progetto)"
            tabella.add_row(
                livello(SIMBOLO[st], st),
                livello(nome, st) if st != s.OK else nome,
                c.dettaglio,
                s.porte(c),
                c.cpu,
                c.memoria,
                c.immagine[:40],
                key=c.id,
            )
        if scelto:
            for i, c in enumerate(elenco):
                if c.id == scelto:
                    tabella.move_cursor(row=i)

    @on(Switch.Changed, "#d-stats, #d-altri")
    def cambio_vista_container(self) -> None:
        self.leggi_container()

    def scelto(self) -> s.Container | None:
        chiave = chiave_scelta(self.query_one("#container", DataTable))
        return next((c for c in self.container if c.id == chiave), None)

    def azione_container(self, verbo: str) -> None:
        c = self.scelto()
        if c is None:
            return
        nomi = {"start": "Avviare", "stop": "Fermare", "restart": "Riavviare"}

        def lavoro() -> None:
            codice, testo = s.docker(verbo, c.id, timeout=180)
            if codice == 0:
                self.call_from_thread(self.notify, f"{c.nome}: fatto.")
            else:
                self.call_from_thread(self.notify, testo.strip()[-300:] or "Errore.", severity="error")
            self.call_from_thread(self.leggi_container)

        def esegui() -> None:
            self.notify(f"{c.nome}: in corso…")
            self.run_worker(lavoro, thread=True, group="azioni")

        if verbo == "start":
            esegui()
        else:
            self.conferma(f"{nomi[verbo]} il container {c.nome}?", esegui, pericolosa=verbo == "stop")

    def log_container(self) -> None:
        c = self.scelto()
        if c:
            self.push_screen(Visore(f"Log di {c.nome}", lambda: s.log_container(c.id), segui=True))

    def dettagli_container(self) -> None:
        c = self.scelto()
        if c:

            def carica() -> list[str]:
                _, testo = s.docker("inspect", c.id)
                with contextlib.suppress(ValueError):
                    dati = json.loads(testo)[0]
                    # Le variabili d'ambiente possono contenere password: non si mostrano.
                    if "Config" in dati:
                        dati["Config"]["Env"] = [e.split("=", 1)[0] + "=…" for e in dati["Config"].get("Env") or []]
                    return json.dumps(dati, indent=2, ensure_ascii=False).splitlines()
                return testo.splitlines()

            self.push_screen(Visore(f"Dettagli di {c.nome}", carica))

    def console_container(self) -> None:
        c = self.scelto()
        if c is None:
            return
        if c.stato != "running":
            self.notify("Il container è fermo: avvialo prima.", severity="warning")
            return
        self.nel_terminale(
            f"Console in {c.nome} (exit per uscire)",
            lambda: (
                subprocess.run(
                    ["docker", "exec", "-it", c.id, "sh", "-c", "command -v bash >/dev/null && exec bash || exec sh"]
                ).returncode
            ),
        )

    @work(thread=True, exclusive=True, group="docker-spazio")
    def leggi_docker_spazio(self) -> None:
        df = s.leggi_df(s.docker("system", "df", "--format", "json", timeout=120)[1])
        immagini = []
        for riga in s.docker("images", "--format", "json")[1].splitlines():
            with contextlib.suppress(ValueError):
                immagini.append(json.loads(riga))
        volumi = []
        for riga in s.docker("volume", "ls", "--format", "json")[1].splitlines():
            with contextlib.suppress(ValueError):
                volumi.append(json.loads(riga))
        self.call_from_thread(self.mostra_docker_spazio, df, immagini, volumi)

    def mostra_docker_spazio(self, df: list[dict], immagini: list[dict], volumi: list[dict]) -> None:
        t = self.query_one("#df", DataTable)
        t.clear()
        for r in df:
            t.add_row(
                r.get("Type", ""),
                str(r.get("TotalCount", "")),
                str(r.get("Active", "")),
                r.get("Size", ""),
                r.get("Reclaimable", ""),
            )
        t = self.query_one("#immagini", DataTable)
        t.clear()
        for r in immagini:
            t.add_row(r.get("Repository", ""), r.get("Tag", ""), r.get("Size", ""), r.get("CreatedSince", ""))
        t = self.query_one("#volumi", DataTable)
        t.clear()
        for r in volumi:
            etichette = s._etichette(r.get("Labels", "")) if isinstance(r.get("Labels"), str) else {}
            t.add_row(r.get("Name", ""), etichette.get("com.docker.compose.project", ""), r.get("Driver", ""))

    # ------------------------------------------------------------------------------------------------ log
    @on(Select.Changed, "#l-sorgente")
    @on(Switch.Changed, "#l-errori")
    @on(Input.Submitted, "#l-cerca")
    def cambio_log(self) -> None:
        self.leggi_log()

    @work(thread=True, exclusive=True, group="log")
    def leggi_log(self) -> None:
        sorgente = self.query_one("#l-sorgente", Select).value
        if sorgente == "journal":
            righe = s.esegui(["journalctl", "-p", "warning", "-n", str(s.RIGHE_LOG), "--no-pager"], timeout=60)[1]
        elif sorgente == "metodoconsole":
            righe = s.esegui(["journalctl", "-u", "metodoconsole-*", "-n", str(s.RIGHE_LOG), "--no-pager"], timeout=60)[
                1
            ]
        elif sorgente == "antivirus":
            righe = s.LOG_SCANSIONE.read_text(errors="replace") if s.LOG_SCANSIONE.exists() else "Nessuna scansione."
        else:
            servizi = [] if sorgente == "tutti" else [str(sorgente)]
            r = self.dc("logs", "--no-color", "--tail", "500", *servizi, controlla=False, timeout=60)
            righe = (r.stdout or "") + (r.stderr or "")
        self.call_from_thread(self.mostra_log, righe.splitlines())

    def mostra_log(self, righe: list[str]) -> None:
        if self.query_one("#l-errori", Switch).value:
            righe = s.righe_di_errore(righe)
        righe = s.filtra(righe, self.query_one("#l-cerca", Input).value.strip())
        log = self.query_one("#l-testo", Log)
        log.clear()
        log.write_lines(righe[-s.RIGHE_LOG :])

    # ------------------------------------------------------------------------------------------------ sicurezza
    @work(thread=True, exclusive=True, group="sicurezza")
    def leggi_sicurezza(self) -> None:
        fw, av, ac, ag, porte = s.firewall(), s.antivirus(), s.accessi(), s.aggiornamenti(), s.porte_in_ascolto()
        env = self.conf.cartella / ".env"
        permessi = env.stat().st_mode if env.exists() else None
        pubblicate = sorted(
            {s.porte(c) for c in s.leggi_container(s.docker("ps", "--format", "json")[1]) if "0.0.0.0" in c.porte}  # noqa: S104
        )
        controlli = s.controlli_sicurezza(fw, av, ac, ag, porte, permessi, [p for p in pubblicate if p])
        self.call_from_thread(self.mostra_sicurezza, fw, av, ac, porte, controlli)

    def mostra_sicurezza(self, fw: s.Firewall, av: s.Antivirus, ac: s.Accessi, porte: list[s.Porta], controlli) -> None:  # type: ignore[no-untyped-def]
        self.ultimi_controlli = controlli
        self.firewall, self.antivirus_stato, self.accessi_stato = fw, av, ac
        with contextlib.suppress(Exception):
            self.mostra_riepilogo_sicurezza()
        t = self.query_one("#controlli", DataTable)
        t.clear()
        for c in controlli:
            t.add_row(livello(ETICHETTA.get(c.livello, c.livello), c.livello), c.nome, c.dettaglio, c.rimedio)
        peggiore = s.peggiore([c.livello for c in controlli])
        self.query_one("#s-esito", Static).update(
            livello(
                f"Sicurezza: {ETICHETTA[peggiore]} · {sum(c.livello != s.OK for c in controlli)} punti da sistemare",
                peggiore,
            )
        )
        # firewall
        stato_fw = Text()
        if fw.strumento:
            stato_fw.append(f"{fw.strumento}: ")
            stato_fw.append("attivo" if fw.attivo else "spento", style="green" if fw.attivo else "red")
            if fw.nota:
                stato_fw.append(f" — {fw.nota}", style="yellow")
            if not fw.gestibile:
                stato_fw.append("\nLe azioni qui sotto funzionano con ufw; con altri firewall si usa il loro comando.")
        else:
            stato_fw.append("Nessun firewall installato. Consigliato: Installa ufw.", style="red")
        self.query_one("#fw-stato", Static).update(stato_fw)
        for id_ in ("#fw-attiva", "#fw-disattiva", "#fw-consenti", "#fw-blocca", "#fw-elimina"):
            self.query_one(id_, Button).disabled = not fw.gestibile
        self.query_one("#fw-installa", Button).display = fw.strumento != "ufw"
        t = self.query_one("#fw-regole", DataTable)
        t.clear()
        for regola in fw.regole:
            t.add_row(regola, key=regola)
        # antivirus
        stato_av = Text()
        if not av.installato:
            stato_av.append("ClamAV non è installato.", style="yellow")
        else:
            stato_av.append(f"ClamAV {av.versione} · firme del {av.firme_del or '?'}")
            if av.firme_giorni is not None:
                stato_av.append(f" ({av.firme_giorni} giorni)", style="yellow" if av.firme_giorni > 3 else "green")
            stato_av.append(f"\nAggiornamento automatico delle firme: {'attivo' if av.aggiornamento else 'spento'}")
            stato_av.append(f" · demone: {'attivo' if av.demone else 'spento'}")
            u = av.ultima_scansione
            if av.scansione_in_corso:
                stato_av.append("\nScansione in corso…", style="yellow")
            elif u:
                infetti = u.get("Infected files", "?")
                stato_av.append(f"\nUltima scansione: {u.get('Scanned files', '?')} file, ")
                stato_av.append(f"{infetti} infetti", style="green" if infetti == "0" else "red")
                stato_av.append(f", {u.get('Time', '')}")
                if u.get("trovati"):
                    stato_av.append("\n" + u["trovati"], style="red")
            else:
                stato_av.append("\nNessuna scansione fatta.")
        self.query_one("#av-stato", Static).update(stato_av)
        self.query_one("#av-installa", Button).display = not av.installato
        for id_ in ("#av-scansiona", "#av-firme"):
            self.query_one(id_, Button).disabled = not av.installato
        # accessi
        stato_ac = Text()
        if ac.ssh:
            stato_ac.append(
                f"SSH: porta {ac.ssh.get('port', '?')} · root {ac.ssh.get('permitrootlogin', '?')} · "
                f"password {ac.ssh.get('passwordauthentication', '?')} · "
                f"chiavi {ac.ssh.get('pubkeyauthentication', '?')}\n"
            )
        else:
            stato_ac.append("SSH: configurazione non leggibile (serve root).\n", style="yellow")
        stato_ac.append("Fail2ban: ")
        stato_ac.append("attivo" if ac.fail2ban else "non attivo", style="green" if ac.fail2ban else "yellow")
        stato_ac.append(f" · accessi falliti nelle ultime 24 ore: {ac.tentativi_24h}")
        stato_ac.append(f"\nUtenti amministratori (sudo): {', '.join(ac.sudo) or '—'}")
        if ac.ultimi:
            stato_ac.append("\nUltimi accessi:\n" + "\n".join(ac.ultimi[:6]), style="dim")
        self.query_one("#ac-stato", Static).update(stato_ac)
        self.query_one("#ac-installa", Button).display = not ac.fail2ban
        bloccati = {ip: jail for jail, ips in ac.bannati.items() for ip in ips}
        t = self.query_one("#ac-ip", DataTable)
        t.clear()
        visti = set()
        for ip, n in ac.ip_tentativi:
            visti.add(ip)
            t.add_row(ip, str(n), bloccati.get(ip, ""), key=ip)
        for ip, jail in bloccati.items():
            if ip not in visti:
                t.add_row(ip, "", jail, key=ip)
        # porte
        t = self.query_one("#porte", DataTable)
        t.clear()
        for p in porte:
            t.add_row(
                p.protocollo,
                p.indirizzo,
                str(p.porta),
                p.processo,
                livello("da fuori", s.ATTENZIONE) if p.pubblica else "solo locale",
            )

    def porta_ssh(self) -> str:
        return getattr(self, "accessi_stato", s.Accessi()).ssh.get("port", "22") or "22"

    @on(Button.Pressed)
    def pulsante(self, event: Button.Pressed) -> None:
        azione = getattr(self, f"p_{(event.button.id or '').replace('-', '_')}", None)
        if azione:
            azione()

    def p_s_ricontrolla(self) -> None:
        self.leggi_sicurezza()
        self.notify("Controllo di sicurezza avviato.")

    def p_fw_attiva(self) -> None:
        porta = self.porta_ssh()
        self.conferma(
            f"Attivare il firewall (ufw)? Prima viene consentita la porta SSH {porta}/tcp, "
            "così non resti chiuso fuori.",
            lambda: self.shell(
                "Attivazione del firewall", f"ufw allow {porta}/tcp && ufw --force enable && ufw status verbose"
            ),
        )

    def p_fw_disattiva(self) -> None:
        self.conferma(
            "Disattivare il firewall? Il server resta senza protezione.",
            lambda: self.shell("Firewall spento", "ufw disable"),
            True,
        )

    def p_fw_consenti(self) -> None:
        def fatto(regola: str) -> None:
            if not s.regola_ufw_valida(regola):
                self.notify("Scrivi una porta, es. 443/tcp o 8000.", severity="error")
                return
            self.shell(f"Consenti {regola}", f"ufw allow {regola} && ufw status numbered")

        self.chiedi("Porta da aprire (es. 443/tcp, 80, 8000/tcp):", fatto)

    def p_fw_blocca(self) -> None:
        def fatto(ip: str) -> None:
            if not s.ip_valido(ip):
                self.notify("Indirizzo IP non valido.", severity="error")
                return
            self.shell(f"Blocca {ip}", f"ufw insert 1 deny from {ip} && ufw status numbered")

        self.chiedi("Indirizzo IP (o rete, es. 203.0.113.0/24) da bloccare:", fatto)

    def p_fw_elimina(self) -> None:
        regola = chiave_scelta(self.query_one("#fw-regole", DataTable))
        numero = s.numero_regola_ufw(regola or "")
        if numero is None:
            self.notify("Scegli una regola nell'elenco.", severity="warning")
            return
        self.conferma(
            f"Eliminare la regola «{regola}»?",
            lambda: self.shell("Elimina regola", f"ufw --force delete {numero} && ufw status numbered"),
            True,
        )

    def p_fw_installa(self) -> None:
        self.conferma(
            "Installare ufw? Dopo l'installazione resta spento: lo attivi tu con «Attiva».",
            lambda: self.shell("Installazione di ufw", "apt-get update && apt-get install -y ufw"),
        )

    def p_av_installa(self) -> None:
        self.conferma(
            "Installare l'antivirus ClamAV con l'aggiornamento automatico delle firme?",
            lambda: self.shell(
                "Installazione di ClamAV",
                "apt-get update && apt-get install -y clamav clamav-daemon && systemctl enable --now clamav-freshclam",
            ),
        )

    def p_av_firme(self) -> None:
        self.shell(
            "Aggiornamento delle firme",
            "systemctl stop clamav-freshclam 2>/dev/null; freshclam; "
            "systemctl start clamav-freshclam 2>/dev/null; true",
        )

    def p_av_scansiona(self) -> None:
        predefiniti = " ".join(self.percorsi_scansione())

        def fatto(testo: str) -> None:
            percorsi = testo.split() or self.percorsi_scansione()
            if not s.root():
                self.notify("La scansione completa richiede root: sudo metodoconsole.", severity="warning")
            log = s.avvia_scansione(percorsi)
            self.notify(f"Scansione avviata in background (prosegue anche se chiudi). Risultato in {log}.")
            self.set_timer(2, self.leggi_sicurezza)

        self.chiedi("Cartelle da scansionare (separate da spazio):", fatto, predefiniti)

    def percorsi_scansione(self) -> list[str]:
        percorsi = [str(self.conf.cartella), "/tmp", "/home", "/root"]  # noqa: S108 (si scansionano)
        codice, testo = s.docker("volume", "inspect", "--format", "{{.Mountpoint}}", f"{self.progetto}_media")
        if codice == 0 and testo.strip():
            percorsi.insert(0, testo.strip())  # i file caricati dagli utenti
        return percorsi

    def p_av_log(self) -> None:
        self.push_screen(
            Visore(
                "Log della scansione antivirus",
                lambda: (
                    s.LOG_SCANSIONE.read_text(errors="replace") if s.LOG_SCANSIONE.exists() else "Nessuna scansione."
                ).splitlines(),
                segui=True,
            )
        )

    def p_ac_installa(self) -> None:
        self.conferma(
            "Installare fail2ban? Blocca per un po' gli indirizzi che sbagliano più volte l'accesso SSH.",
            lambda: self.shell(
                "Installazione di fail2ban",
                "apt-get update && apt-get install -y fail2ban && systemctl enable --now fail2ban",
            ),
        )

    def p_ac_blocca(self) -> None:
        ip = chiave_scelta(self.query_one("#ac-ip", DataTable))
        if not ip or not s.ip_valido(ip):
            self.notify("Scegli un IP nell'elenco.", severity="warning")
            return
        ac = getattr(self, "accessi_stato", s.Accessi())
        if ac.fail2ban and "sshd" in ac.bannati:
            comando = f"fail2ban-client set sshd banip {ip}"
        elif getattr(self, "firewall", s.Firewall()).gestibile:
            comando = f"ufw insert 1 deny from {ip}"
        else:
            self.notify("Serve fail2ban o ufw per bloccare un IP.", severity="error")
            return
        self.conferma(f"Bloccare {ip}?", lambda: self.shell(f"Blocca {ip}", comando))

    def p_ac_sblocca(self) -> None:
        ip = chiave_scelta(self.query_one("#ac-ip", DataTable)) or ""

        def fatto(valore: str) -> None:
            if not s.ip_valido(valore):
                self.notify("Indirizzo IP non valido.", severity="error")
                return
            self.shell(
                f"Sblocca {valore}", f"fail2ban-client unban {valore}; ufw delete deny from {valore} 2>/dev/null; true"
            )

        self.chiedi("IP da sbloccare (fail2ban e ufw):", fatto, ip)

    # ------------------------------------------------------------------------------------------------ disco
    @work(thread=True, exclusive=True, group="disco")
    def leggi_disco(self) -> None:
        codice, testo = s.docker("info", "--format", "{{.DockerRootDir}}")
        dischi = s.leggi_dischi(
            [
                ("sistema", "/"),
                ("Docker", testo.strip() if codice == 0 else "/var/lib/docker"),
                ("backup", str(self.conf.backup_cartella)),
                ("Metodo v2", str(self.conf.cartella)),
            ]
        )
        df = s.leggi_df(s.docker("system", "df", "--format", "json", timeout=120)[1])
        backup = sum(b.dimensione for b in self.mc.elenca_backup(self.conf))
        testo_apt = s.esegui(["du", "-s", "-B1", "/var/cache/apt/archives"])[1].split()
        apt_byte = int(testo_apt[0]) if testo_apt and testo_apt[0].isdigit() else 0
        self.call_from_thread(self.mostra_disco, dischi, df, s.spazio_journal(), backup, apt_byte)

    def mostra_disco(self, dischi: list[s.Disco], df: list[dict], journal: str, backup: int, apt: int) -> None:
        t = self.query_one("#dischi", DataTable)
        t.clear()
        for d in dischi:
            t.add_row(
                d.etichetta,
                d.percorso,
                s.misura(d.usato),
                s.misura(d.libero),
                s.misura(d.totale),
                con_barra(d.frazione, 24),
            )
        t = self.query_one("#occupazione", DataTable)
        t.clear()
        nomi = {
            "Images": "Immagini Docker",
            "Containers": "Container",
            "Local Volumes": "Volumi Docker (dati)",
            "Build Cache": "Cache di build Docker",
        }
        for r in df:
            t.add_row(nomi.get(r.get("Type", ""), r.get("Type", "")), r.get("Size", ""), r.get("Reclaimable", ""))
        t.add_row("Registro di sistema (journal)", journal or "?", "con «Riduci il registro»")
        t.add_row("Backup di Metodo v2", s.misura(backup), "con «Ruota i backup» (secondo la conservazione)")
        t.add_row("Cache di apt", s.misura(apt), s.misura(apt))

    @work(thread=True, exclusive=True, group="cartelle")
    def analizza(self, percorso: str) -> None:
        self.call_from_thread(self.notify, f"Analisi di {percorso}… (può richiedere qualche minuto)")
        righe = s.cartelle_grandi(percorso, 25)
        self.call_from_thread(self.mostra_cartelle, righe)

    def mostra_cartelle(self, righe: list[tuple[int, str]]) -> None:
        t = self.query_one("#cartelle", DataTable)
        t.clear()
        for byte, cartella in righe:
            t.add_row(s.misura(byte), cartella, key=cartella)
        if not righe:
            self.notify("Nessuna sottocartella (o permessi insufficienti).", severity="warning")

    def p_k_analizza(self) -> None:
        self.analizza(self.query_one("#k-percorso", Input).value.strip() or "/")

    @on(DataTable.RowSelected, "#cartelle")
    def entra_cartella(self, event: DataTable.RowSelected) -> None:
        percorso = str(event.row_key.value)
        self.query_one("#k-percorso", Input).value = percorso
        self.analizza(percorso)

    @on(DataTable.RowSelected, "#container")
    def apri_log(self) -> None:
        self.log_container()

    def p_k_docker(self) -> None:
        self.conferma(
            "Eliminare immagini Docker non usate e la cache di build? I dati e i container non vengono toccati.",
            lambda: self.nel_terminale(
                "Pulizia di Docker",
                lambda: (
                    subprocess.run(
                        ["sh", "-c", "docker image prune -f && docker builder prune -f && docker system df"]
                    ).returncode
                ),
            ),
        )

    def p_d_pulisci(self) -> None:
        self.p_k_docker()

    def p_k_journal(self) -> None:
        self.conferma(
            "Ridurre il registro di sistema alle ultime 2 settimane?",
            lambda: self.shell("Riduzione del registro", "journalctl --vacuum-time=14d && journalctl --disk-usage"),
        )

    def p_k_apt(self) -> None:
        self.shell("Pulizia della cache di apt", "apt-get clean && du -sh /var/cache/apt/archives")

    def p_k_ruota(self) -> None:
        c = self.conf
        self.conferma(
            f"Eliminare i backup vecchi tenendo {c.conserva_giornalieri} giornalieri, {c.conserva_settimanali} "
            f"settimanali e {c.conserva_mensili} mensili? (quelli con una nota restano)",
            lambda: self.nel_terminale("Rotazione dei backup", lambda: self.mc.ruota_backup(self.conf)),
        )

    # ------------------------------------------------------------------------------------------------ backup
    @work(thread=True, exclusive=True, group="backup")
    def leggi_backup(self) -> None:
        elenco = self.mc.elenca_backup(self.conf)
        self.call_from_thread(self.mostra_backup, elenco)

    def mostra_backup(self, elenco: list[Any]) -> None:
        c = self.conf
        info = Text(f"Cartella {c.backup_cartella}")
        with contextlib.suppress(OSError):
            d = s.leggi_dischi([("backup", str(c.backup_cartella))])[0]
            info.append(f" · {s.misura(d.libero)} liberi")
        info.append(
            f"\nConservazione: {c.conserva_giornalieri} giornalieri, {c.conserva_settimanali} settimanali, "
            f"{c.conserva_mensili} mensili · copia remota: {c.copia_remota or 'non configurata'}"
        )
        if not c.copia_remota:
            info.append(
                "\nConsiglio: configura la copia remota (copia_remota) per non perdere i backup con il disco.",
                style="yellow",
            )
        self.query_one("#b-info", Static).update(info)
        t = self.query_one("#tabella-backup", DataTable)
        t.clear()
        adesso = dt.datetime.now()
        for b in elenco:
            m = b.manifesto
            t.add_row(
                b.nome,
                b.quando.strftime("%d/%m/%Y %H:%M"),
                s.durata((adesso - b.quando).total_seconds()),
                s.misura(b.dimensione),
                self.mc.versione_backup(m),
                m.get("nota", ""),
                key=b.nome,
            )

    def backup_scelto(self) -> str | None:
        nome = chiave_scelta(self.query_one("#tabella-backup", DataTable))
        if not nome:
            self.notify("Scegli un backup nell'elenco.", severity="warning")
        return nome

    def p_b_nuovo(self) -> None:
        self.action_nuovo_backup()

    def p_b_verifica(self) -> None:
        nome = self.backup_scelto()
        if nome:
            self.nel_terminale(
                f"Verifica di {nome}", lambda: self.mc.cmd_backup_verifica(self.conf, self.dc, self.ns(nome=nome))
            )

    def p_b_dettagli(self) -> None:
        nome = self.backup_scelto()
        if nome:
            b = self.mc.trova_backup(self.conf, nome)
            self.push_screen(
                Visore(f"Backup {nome}", lambda: json.dumps(b.manifesto, indent=2, ensure_ascii=False).splitlines())
            )

    def p_b_ripristina(self) -> None:
        nome = self.backup_scelto()
        if nome:
            self.conferma(
                f"Ripristinare il backup {nome}? TUTTI i dati attuali saranno sostituiti (prima viene fatto un backup "
                "di sicurezza). Nel terminale ti verrà chiesto di scrivere RIPRISTINA.",
                lambda: self.nel_terminale(
                    f"Ripristino di {nome}",
                    lambda: self.mc.cmd_ripristina(
                        self.conf,
                        self.dc,
                        self.ns(nome=nome, solo_database=False, senza_backup_sicurezza=False, si=False),
                    ),
                ),
                pericolosa=True,
            )

    # ------------------------------------------------------------------------------------------------ diagnostica
    def p_g_esegui(self) -> None:
        self.esegui_diagnostica()

    @work(thread=True, exclusive=True, group="diagnostica")
    def esegui_diagnostica(self) -> None:
        self.call_from_thread(self.query_one("#g-esito", Static).update, "Diagnostica in corso…")
        try:
            esiti = self.mc.diagnostica_completa(self.conf, self.dc)
        except Exception as exc:
            self.call_from_thread(self.query_one("#g-esito", Static).update, livello(f"Errore: {exc}", s.ERRORE))
            return
        self.call_from_thread(self.mostra_diagnostica, esiti)

    def mostra_diagnostica(self, esiti: list[Any]) -> None:
        mappa = {"ok": s.OK, "attenzione": s.ATTENZIONE, "errore": s.ERRORE}
        t = self.query_one("#tabella-diagnostica", DataTable)
        t.clear()
        gruppi = {"server": "Server e Docker", "app": "Applicazione"}
        for e in esiti:
            liv = mappa.get(e.esito, e.esito)
            t.add_row(gruppi.get(e.gruppo, e.gruppo), livello(ETICHETTA.get(liv, liv), liv), e.nome, e.dettaglio)
        peggiore = mappa.get(self.mc.peggiore(esiti), s.OK)
        self.query_one("#g-esito", Static).update(
            livello(f"Esito: {ETICHETTA.get(peggiore, peggiore)} · {time.strftime('%H:%M:%S')}", peggiore)
        )

    # ------------------------------------------------------------------------------------------------ aggiornamenti
    @work(thread=True, exclusive=True, group="aggiornamenti")
    def leggi_aggiornamenti(self) -> None:
        self.call_from_thread(self.mostra_aggiornamenti, s.aggiornamenti())

    def mostra_aggiornamenti(self, ag: s.Aggiornamenti) -> None:
        stato_ag = Text()
        if not ag.disponibile:
            stato_ag.append("apt non disponibile su questo sistema.")
        else:
            stato_ag.append(f"{len(ag.pacchetti)} pacchetti da aggiornare")
            stato_ag.append(f", {ag.sicurezza} di sicurezza", style="red" if ag.sicurezza else "green")
            stato_ag.append(" (elenco dell'ultimo «apt update»)\nAggiornamenti automatici di sicurezza: ")
            stato_ag.append("attivi" if ag.automatici else "spenti", style="green" if ag.automatici else "yellow")
        if ag.riavvio_richiesto:
            stato_ag.append(
                "\nIl sistema chiede un riavvio "
                f"({', '.join(ag.riavvio_pacchetti[:5]) or 'aggiornamenti installati'}).",
                style="yellow",
            )
        stato_ag.append(f"\nMetodo v2: versione {self.mc.versione_codice(self.conf)}")
        self.query_one("#u-stato", Static).update(stato_ag)
        t = self.query_one("#pacchetti", DataTable)
        t.clear()
        for nome, versione, sicurezza in sorted(ag.pacchetti, key=lambda p: (not p[2], p[0])):
            t.add_row(nome, versione, livello("sì", s.ERRORE) if sicurezza else "")

    def p_u_sistema(self) -> None:
        self.conferma(
            "Aggiornare i pacchetti del sistema (apt update e apt upgrade)? Metodo v2 resta acceso.",
            lambda: self.shell(
                "Aggiornamento del sistema", "apt-get update && apt-get upgrade -y && apt-get autoremove -y"
            ),
        )

    def p_u_automatici(self) -> None:
        self.conferma(
            "Attivare gli aggiornamenti automatici di sicurezza (unattended-upgrades)?",
            lambda: self.shell(
                "Aggiornamenti automatici",
                "apt-get update && apt-get install -y unattended-upgrades && "
                "echo 'unattended-upgrades unattended-upgrades/enable_auto_updates boolean true' "
                "| debconf-set-selections && "
                "dpkg-reconfigure -f noninteractive unattended-upgrades && cat /etc/apt/apt.conf.d/20auto-upgrades",
            ),
        )

    def p_u_metodo(self) -> None:
        self.conferma(
            "Aggiornare Metodo v2 (git pull, backup, ricostruzione e riavvio)?",
            lambda: self.nel_terminale(
                "Aggiornamento di Metodo v2",
                lambda: self.mc.cmd_aggiorna(self.conf, self.dc, self.ns(senza_backup=False, forza=False)),
            ),
        )

    def p_u_riavvio(self) -> None:
        self.conferma(
            "Riavviare il SERVER? Metodo v2 e la connessione SSH si interrompono per qualche minuto.",
            lambda: self.conferma(
                "Sicuro? Il server si riavvia adesso.",
                lambda: self.shell("Riavvio del server", "systemctl reboot"),
                pericolosa=True,
            ),
            pericolosa=True,
        )


def avvia(conf: Any, dc: Any, mc: Any) -> int:
    MetodoConsole(conf, dc, mc).run()
    return 0
