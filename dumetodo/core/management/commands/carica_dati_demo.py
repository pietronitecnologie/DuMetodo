"""Carica dati di prova realistici per provare DuMetodo (MAI su un'installazione in uso).

Gli ordini arrivano da un vero file di importazione nel formato del Cliente e tutto il resto passa dai
servizi dell'applicazione (stati, consegne, DDT, note, tracce, qualità, magazzino): i dati sono
coerenti come quelli prodotti usando il sito. Le date sono relative a oggi.

Uso:  python manage.py carica_dati_demo            (rifiuta se ci sono già ordini)
      python manage.py carica_dati_demo --forza    (aggiunge comunque)
"""

from __future__ import annotations

import datetime as dt
import logging
import random
import secrets
from decimal import Decimal
from typing import Any

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from dumetodo.anagrafica.models import Articolo, Tabulato, UnitaMisura
from dumetodo.comunicazioni import servizi as note
from dumetodo.comunicazioni.models import Visibilita
from dumetodo.consegne import servizi as consegne
from dumetodo.core.models import Reparto
from dumetodo.core.permessi import applica_profilo
from dumetodo.importazione import servizi as importazione
from dumetodo.importazione.models import OrigineFile
from dumetodo.magazzino import servizi as magazzino
from dumetodo.ordini import servizi as ordini
from dumetodo.ordini.models import Ordine, StatoOrdine
from dumetodo.report import servizi as report
from dumetodo.tracciatura.models import Modello, Tracciatura, Valore
from dumetodo.utilita import servizi as utilita

REPARTI = [
    ("GK01-PLS", "Pulsanti", "120"),
    ("GK01-ETT", "Elettronica", "160"),
    ("GK01-CBL", "Cablaggi", "80"),
    ("GK01-ASM", "Assemblaggio", "200"),
    ("GK01-COL", "Collaudo", "60"),
]

# codice, descrizione, reparto, um, prezzo di riferimento, tabulato
ARTICOLI = [
    ("18090054BR", "Pulsantiera 4 tasti IP65 bianca", "GK01-PLS", "PZ", "4.8500", Tabulato.NESSUNO),
    ("18090055BR", "Pulsantiera 6 tasti IP65 bianca", "GK01-PLS", "PZ", "6.2000", Tabulato.NESSUNO),
    ("18090061NR", "Pulsante di emergenza a fungo 40 mm", "GK01-PLS", "PZ", "3.1500", Tabulato.NESSUNO),
    ("18090072GR", "Tastiera a membrana 12 tasti grigia", "GK01-PLS", "PZ", "7.9000", Tabulato.NESSUNO),
    ("18090080AZ", "Pulsante luminoso LED blu 22 mm", "GK01-PLS", "PZ", "2.4500", Tabulato.NESSUNO),
    ("18090091RS", "Selettore a chiave 3 posizioni", "GK01-PLS", "PZ", "5.6000", Tabulato.NESSUNO),
    ("22010110", "Scheda controllo motore 24 V", "GK01-ETT", "PZ", "38.5000", Tabulato.NESSUNO),
    ("22010125", "Scheda I/O 16 canali", "GK01-ETT", "PZ", "52.0000", Tabulato.NESSUNO),
    ("22010133", "Alimentatore switching 48 W", "GK01-ETT", "PZ", "21.7500", Tabulato.NESSUNO),
    ("22010140", "Modulo display LCD 4x20 con interfaccia", "GK01-ETT", "PZ", "29.9000", Tabulato.NESSUNO),
    ("22010152", "Scheda relè 8 uscite", "GK01-ETT", "PZ", "33.4000", Tabulato.NESSUNO),
    ("22010168", "Sensore temperatura PT100 cablato", "GK01-ETT", "PZ", "12.3000", Tabulato.NESSUNO),
    ("31500201", "Cavo schermato 4x0,5 mm²", "GK01-CBL", "MT", "0.8200", Tabulato.NESSUNO),
    ("31500215", "Cavo unipolare 1,5 mm² nero", "GK01-CBL", "MT", "0.2900", Tabulato.NESSUNO),
    ("31500230", "Cablaggio quadro comandi serie C", "GK01-CBL", "PZ", "18.6000", Tabulato.NESSUNO),
    ("31500244", "Cablaggio pulsantiera con connettore M12", "GK01-CBL", "PZ", "9.4000", Tabulato.NESSUNO),
    ("40020010", "Quadro di comando assemblato QC-200", "GK01-ASM", "PZ", "245.0000", Tabulato.NESSUNO),
    ("40020025", "Box di derivazione IP67 cablato", "GK01-ASM", "PZ", "64.5000", Tabulato.NESSUNO),
    ("40020038", "Console operatore con display", "GK01-ASM", "PZ", "312.0000", Tabulato.NESSUNO),
    ("40020047", "Gruppo pulsantiera pensile", "GK01-ASM", "PZ", "88.0000", Tabulato.NESSUNO),
    ("90000001", "Collaudo funzionale quadro", "GK01-COL", "PZ", "15.0000", Tabulato.NESSUNO),
]

VARIANTI = ["", "", "", "01", "02"]

# Ordini: (appendice, numero di righe min-max, articoli possibili per prefisso)
FAMIGLIE = [
    ("", (2, 7), ("4002", "3150", "2201")),  # ordini del Cliente: assiemi e materiali misti
    ("T1", (1, 4), ("1809",)),  # pulsanti
    ("T2", (1, 3), ("1809",)),
    ("R1", (1, 4), ("2201",)),  # elettronica
    ("R2", (1, 3), ("2201",)),
    ("CL", (1, 3), ("3150", "4002")),  # conto lavoro
]

INTESTAZIONE = (
    "Periodo riferimento@Serie numerazione@N.ro Ordine@Appendice@N.ro prog. riga@Data registrazione@Data consegna@"
    "Cod. Articolo@Variante@Quantita@Prezzo@Sconto 1@Sconto 2@Sconto 3@Sconto 4@Sconto 5@Sconto unitario@Prezzo finale"
)

NOTE_FORNITORE = [
    "Materiale arrivato, partiamo con la produzione lunedì.",
    "Manca il connettore M12 a magazzino: previsto arrivo giovedì.",
    "Prima serie montata, in attesa di collaudo.",
    "Possiamo anticipare metà quantità a fine settimana?",
]
NOTE_CLIENTE = [
    "Confermate la data di consegna? È urgente per il nostro cantiere.",
    "Va bene la consegna parziale, grazie.",
    "Attenzione: nuova revisione del disegno allegata via email.",
    "Per questo ordine usare l'imballo singolo.",
]


def _data(d: dt.date) -> str:
    return d.strftime("%d%m%Y")


class Command(BaseCommand):
    help = "Carica dati di prova realistici (ordini, consegne, note, tracciabilità, magazzino, report)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--forza", action="store_true", help="Carica anche se esistono già ordini.")
        parser.add_argument("--seme", type=int, default=2026, help="Seme casuale (stessi dati a ogni prova).")

    def handle(self, *args: Any, **opts: Any) -> None:
        if Ordine.objects.exists() and not opts["forza"]:
            raise CommandError("Ci sono già ordini: i dati di prova vanno caricati su un database vuoto (--forza).")
        # Senza destinatari configurati ogni nota segnalerebbe un avviso: qui non servono.
        logging.getLogger("dumetodo.comunicazioni.email").setLevel(logging.ERROR)
        self.rnd = random.Random(opts["seme"])  # noqa: S311 - dati di prova, non crittografia
        self.oggi = timezone.localdate()
        self.password: dict[str, str] = {}
        with transaction.atomic():
            self._anagrafiche()
            self._utenti()
            self._ordini()
        # Ogni passo successivo usa i servizi, ognuno nella sua transazione (come dal sito).
        self._stati_e_consegne()
        self._note()
        self._tracciabilita()
        self._magazzino()
        self._utilita_e_report()
        self._gestionale()
        self._timbrature()
        self._tracciatura()

        self.stdout.write(self.style.SUCCESS("Dati di prova caricati."))
        self.stdout.write(f"  ordini: {Ordine.objects.count()}  ({self._riepilogo_stati()})")
        for nome, pw in self.password.items():
            self.stdout.write(f"  utente di prova '{nome}' password: {pw}")
        if self.password:
            self.stdout.write("Le password sono mostrate solo ora.")

    # ------------------------------------------------------------------ anagrafiche e utenti
    def _anagrafiche(self) -> None:
        for codice, nome, soglia in REPARTI:
            Reparto.objects.update_or_create(
                codice=codice, defaults={"nome": nome, "soglia_ore_settimanali": Decimal(soglia), "attivo": True}
            )
        reparti = {r.codice: r for r in Reparto.objects.all()}
        for codice, descrizione, reparto, um, prezzo, tabulato in ARTICOLI:
            Articolo.objects.update_or_create(
                codice=codice,
                defaults={
                    "descrizione": descrizione,
                    "reparto": reparti[reparto],
                    "um": UnitaMisura.METRI if um == "MT" else UnitaMisura.PEZZI,
                    "prezzo_riferimento": Decimal(prezzo),
                    "data_prezzo": self.oggi - dt.timedelta(days=90),
                    "tabulato": tabulato,
                    "nuovo": False,
                },
            )

    def _utenti(self) -> None:
        for nome, profilo, nominativo in [
            ("operatore", "Operatore", ("Marco", "Bianchi")),
            ("caporeparto", "Caporeparto", ("Laura", "Russo")),
            ("cliente", "Cliente", ("Ufficio", "Acquisti")),
            ("amministrazione", "Amministrazione", ("Giulia", "Conti")),
        ]:
            u, creato = User.objects.get_or_create(
                username=nome, defaults={"first_name": nominativo[0], "last_name": nominativo[1]}
            )
            if creato:
                pw = secrets.token_urlsafe(9)
                u.set_password(pw)
                u.save()
                self.password[nome] = pw
            applica_profilo(u, profilo)
        self.capo = User.objects.get(username="caporeparto")
        self.operatore = User.objects.get(username="operatore")
        self.cliente = User.objects.get(username="cliente")
        self.admin = User.objects.filter(is_superuser=True).first() or self.capo

    # ------------------------------------------------------------------ ordini (dal file del Cliente)
    def _ordini(self) -> None:
        articoli = list(Articolo.objects.values_list("codice", "prezzo_riferimento"))
        righe: list[str] = []
        numero = 4300
        anni = [self.oggi.year - 1] + [self.oggi.year] * 4
        for i in range(48):
            appendice, (rmin, rmax), prefissi = self.rnd.choice(FAMIGLIE)
            numero += self.rnd.randint(1, 9)
            anno = self.rnd.choice(anni)
            serie = "00040" if i % 7 else "00041"
            registrata = self.oggi - dt.timedelta(days=self.rnd.randint(5, 60))
            scadenza = self.oggi + dt.timedelta(days=self.rnd.randint(-40, 45))
            candidati = [a for a in articoli if a[0].startswith(prefissi)]
            for n in range(1, self.rnd.randint(rmin, rmax) + 1):
                codice, prezzo = self.rnd.choice(candidati)
                metri = codice.startswith("3150") and int(codice) < 31500230
                qta = (
                    self.rnd.choice([50, 100, 150, 250, 500])
                    if metri
                    else self.rnd.choice([1, 2, 5, 10, 12, 20, 24, 50])
                )
                # Qualche prezzo diverso dal riferimento: alimenta il report Controllo prezzi.
                p = prezzo * (Decimal("1.08") if self.rnd.random() < 0.15 else Decimal(1))
                righe.append(
                    f"{anno}@{serie}@{numero:07d}@{appendice or '0'}  @{n:07d}@{_data(registrata)}@{_data(scadenza)}@"
                    f"{codice}   @{self.rnd.choice(VARIANTI) or '__0'}   @{qta}   @@@@@@@@"
                    f"{str(p.quantize(Decimal('0.0001'))).replace('.', ',')}   @701 {numero:07d} 00001636"
                )
        # Una riga con un articolo vuoto: genera un errore di importazione da gestire in Utilità.
        righe.append(
            f"{self.oggi.year}@00040@0009999@0  @0000001@{_data(self.oggi)}@{_data(self.oggi)}@   "
            "@__0   @5   @@@@@@@@1,0000   @"
        )
        contenuto = ("\r\n".join([INTESTAZIONE, *righe]) + "\r\n").encode("latin-1")
        f = importazione.registra_file(
            f"ORDINI_{self.oggi:%Y%m%d}.txt", contenuto, OrigineFile.CARICAMENTO, utente=self.capo
        )
        importazione.importa(f)

    # ------------------------------------------------------------------ produzione e consegne
    def _stati_e_consegne(self) -> None:
        tutti = list(Ordine.objects.order_by("data_scadenza", "pk"))
        passati = [o for o in tutti if o.data_scadenza and o.data_scadenza < self.oggi]
        vicini = [o for o in tutti if o.data_scadenza and self.oggi <= o.data_scadenza <= self.oggi + dt.timedelta(10)]
        # Scaduti: la maggior parte già consegnata con bolla, alcuni con DDT registrato.
        da_consegnare = passati[: max(1, len(passati) * 2 // 3)]
        for o in da_consegnare:
            self._prova(ordini.cambia_stato, o.pk, StatoOrdine.IN_SPEDIZIONE, self.capo)
        metà = len(da_consegnare) // 2
        for gruppo in (da_consegnare[:metà], da_consegnare[metà:]):
            if gruppo:
                self._prova(consegne.consegna, self.capo, ordini_ids=[o.pk for o in gruppo])
        senza_ddt = list(consegne.righe_senza_ddt().values_list("pk", flat=True))
        if senza_ddt:
            self._prova(
                consegne.registra_ddt,
                f"{self.rnd.randint(300, 900)}",
                self.oggi - dt.timedelta(2),
                senza_ddt[::2],
                self.capo,
            )
        # Vicini alla scadenza: in lavorazione, uno pronto per la spedizione.
        for o in vicini[: len(vicini) // 2]:
            self._prova(ordini.cambia_stato, o.pk, StatoOrdine.IN_LAVORAZIONE, self.operatore)
        if vicini:
            self._prova(ordini.cambia_stato, vicini[-1].pk, StatoOrdine.IN_SPEDIZIONE, self.capo)
        # Un ordine posticipato con motivo e uno annullato.
        futuri = [o for o in tutti if o.data_scadenza and o.data_scadenza > self.oggi + dt.timedelta(20)]
        if futuri:
            o = futuri[0]
            self._prova(
                ordini.cambia_scadenza_ordine,
                o.pk,
                o.data_scadenza + dt.timedelta(7),
                "Ritardo del fornitore dei connettori",
                self.capo,
            )
        if len(futuri) > 1:
            self._prova(ordini.cambia_stato, futuri[-1].pk, StatoOrdine.ANNULLATO, self.admin)

    def _note(self) -> None:
        aperti = list(Ordine.objects.aperti().order_by("data_scadenza")[:12])
        for i, o in enumerate(aperti):
            if i % 2 == 0:
                self._prova(note.aggiungi_nota, o, self.cliente, self.rnd.choice(NOTE_CLIENTE))
            self._prova(note.aggiungi_nota, o, self.operatore, self.rnd.choice(NOTE_FORNITORE))
            if i % 4 == 0:
                self._prova(
                    note.aggiungi_nota, o, self.capo, "Verificare disponibilità del collaudo.", Visibilita.INTERNA
                )

    # ------------------------------------------------------------------ lotti e controlli qualità (tracciature)
    def _tracciabilita(self) -> None:
        from dumetodo.migrazione.tracciabilita import MODELLI
        from dumetodo.tracciatura import da_qualita as dq

        ordini_lav = list(Ordine.objects.exclude(stato=StatoOrdine.ANNULLATO).order_by("-data_scadenza")[:15])
        lotto = dq.modello_lotto(MODELLI)
        campi = list(lotto.campi.order_by("ordine"))
        for i, o in enumerate(ordini_lav):
            t = Tracciatura.objects.create(
                modello=lotto, momento=timezone.now() - dt.timedelta(days=i % 3), operatore=self.operatore, ordine=o
            )
            for campo, testo in zip(campi, (f"L{self.oggi:%y}{i + 10:03d}", f"B{i + 1:04d}"), strict=True):
                Valore.objects.create(tracciatura=t, campo=campo, testo=testo)
        if not Modello.objects.filter(nome="Pulsantiere").exists():
            mq = dq.crea_modello(
                MODELLI,
                "Pulsantiere",
                [
                    dq.Controllo("visivo", "Controllo visivo"),
                    dq.Controllo("funzionale", "Prova funzionale"),
                    dq.Controllo("distinta", "Distinta", presenza=True),
                    dq.Controllo("disegno", "Disegno", presenza=True),
                ],
                descrizione="Controllo a campione delle pulsantiere prima della consegna.",
            )
            mq.modello.codici = "180*"
            mq.modello.save(update_fields=["codici"])
            for o in ordini_lav[:6]:
                riga = o.righe.first()
                if riga is None:
                    continue
                ko = self.rnd.choice([0, 0, 0, 1])
                dq.registra(
                    MODELLI,
                    mq,
                    {"visivo": (10, ko), "funzionale": (10, 0), "distinta": True, "disegno": True},
                    operatore=self.operatore,
                    momento=timezone.now() - dt.timedelta(days=self.rnd.randint(0, 5)),
                    ordine=o,
                    articolo=riga.articolo,
                    qta=riga.qta,
                )

    # ------------------------------------------------------------------ magazzino
    def _magazzino(self) -> None:
        componenti = [
            ("CN-M12-4P", "Connettore M12 4 poli femmina", "Elettrocomponenti Srl", "1.8500"),
            ("PB-22-RS", "Pulsante 22 mm rosso", "Comandi Industriali SpA", "1.2000"),
            ("PB-22-VD", "Pulsante 22 mm verde", "Comandi Industriali SpA", "1.2000"),
            ("CS-ABS-4", "Custodia ABS 4 fori", "Plastiche Nord", "2.6000"),
            ("PCB-CM24", "Circuito stampato controllo motore", "PCB Italia", "11.4000"),
            ("VT-M3-10", "Vite M3x10 inox", "Viteria Rossi", "0.0300"),
        ]
        for codice, descr, fornitore, prezzo in componenti:
            self._prova(
                magazzino.crea_componente,
                self.admin,
                codice,
                descrizione=descr,
                fornitore=fornitore,
                prezzo_medio=prezzo,
            )
        for codice, qta in [("CN-M12-4P", "140"), ("PB-22-RS", "380"), ("PB-22-VD", "25"), ("VT-M3-10", "5000")]:
            self._prova(magazzino.registra_conteggio, self.operatore, codice, "", qta)
        d = self._prova(magazzino.crea_distinta, self.admin, "18090054BR", "", "Pulsantiera 4 tasti")
        if d is not None:
            for codice, qta in [("CS-ABS-4", "1"), ("PB-22-RS", "2"), ("PB-22-VD", "2"), ("VT-M3-10", "4")]:
                self._prova(magazzino.aggiungi_componente_distinta, self.admin, d.pk, codice, "", qta)
        pls = Reparto.objects.get(codice="GK01-PLS")
        self._prova(magazzino.richiedi_materiale, self.operatore, pls, "PB-22-VD", "", True)
        self._prova(magazzino.richiedi_materiale, self.operatore, pls, "CN-M12-4P", "")
        ddt = self._prova(
            magazzino.crea_ddt,
            self.admin,
            f"FT{self.rnd.randint(1000, 9999)}",
            self.oggi - dt.timedelta(3),
            "Elettrocomponenti Srl",
        )
        if ddt is not None:
            self._prova(
                magazzino.aggiungi_materiale,
                self.admin,
                ddt.pk,
                codice="CN-M12-4P",
                descrizione="Connettore M12 4 poli femmina",
                qta="200",
                prezzo_unitario="1.8200",
            )

    # ------------------------------------------------------------------ utilità e report
    def _utilita_e_report(self) -> None:
        self._prova(
            utilita.salva_annuncio,
            self.admin,
            None,
            data=self.oggi,
            testo="<b>Inventario</b> venerdì pomeriggio: magazzino chiuso dalle 14:00.",
            attivo=True,
            permanente=True,
        )
        for lunedi in report.settimane_indietro(8):
            self._prova(report.salva_ore_lavorate, lunedi, str(self.rnd.randint(140, 220)), self.admin)

    # ------------------------------------------------------------------ aiuti
    def _gestionale(self) -> None:
        """Dati fiscali di FANTASIA (partite IVA formalmente valide), un DDT di legge e una fattura in bozza."""
        from dumetodo.configurazione import servizi as config
        from dumetodo.gestionale import servizi as gestionale

        if not config.leggi("gestionale.fornitore_partita_iva"):
            config.scrivi_molti(
                {
                    "gestionale.fornitore_partita_iva": "01234567897",
                    "gestionale.fornitore_indirizzo": "Via dell'Industria 12",
                    "gestionale.fornitore_cap": "20090",
                    "gestionale.fornitore_comune": "Segrate",
                    "gestionale.fornitore_provincia": "MI",
                    "gestionale.fornitore_email": "amministrazione@fornitore.example",
                    "gestionale.cliente_partita_iva": "09876543217",
                    "gestionale.cliente_indirizzo": "Viale Europa 5",
                    "gestionale.cliente_cap": "27100",
                    "gestionale.cliente_comune": "Pavia",
                    "gestionale.cliente_provincia": "PV",
                    "gestionale.cliente_codice_destinatario": "ABC1234",
                    "gestionale.pagamento_iban": "IT60X0542811101000000123456",
                },
                self.admin,
            )
            config.invalida_cache()
        ufficio = User.objects.get(username="amministrazione")
        righe = list(consegne.righe_senza_ddt().values_list("pk", flat=True))
        if not righe:
            return
        campi = {**gestionale.valori_predefiniti_ddt(), "colli": 3, "peso_kg": Decimal("14.5")}
        ddt = self._prova(gestionale.emetti_ddt, ufficio, righe, self.oggi, campi)
        if ddt is not None:
            self._prova(gestionale.crea_fattura_differita, ufficio, [ddt.pk], self.oggi)

    def _timbrature(self) -> None:
        """Postazioni (ingresso + 3 reparti), 6 lavoratori con codice 1001…1006 e due settimane di timbrature."""
        import secrets as _s

        from dumetodo.timbrature import servizi as tim
        from dumetodo.timbrature.models import Lavoratore, Postazione, Timbratura

        if Lavoratore.objects.exists():
            return
        reparti = {r.codice: r for r in Reparto.objects.all()}
        Postazione.objects.create(nome="Ingresso", token=_s.token_urlsafe(24))
        for codice in ("GK01-PLS", "GK01-CBL", "GK01-ASM"):
            Postazione.objects.create(
                nome=f"Banco {reparti[codice].nome}", reparto=reparti[codice], token=_s.token_urlsafe(24)
            )
        persone = [
            ("Paolo", "Ferri", "GK01-PLS", "24.50"),
            ("Sara", "Gallo", "GK01-PLS", "23.00"),
            ("Luca", "Moretti", "GK01-CBL", "25.00"),
            ("Elena", "Costa", "GK01-ASM", "26.50"),
            ("Davide", "Rizzo", "GK01-ASM", "22.80"),
            ("Chiara", "Fontana", "GK01-CBL", None),  # senza costo orario: segnalata nei costi
        ]
        lavoratori = []
        for i, (nome, cognome, rep, costo) in enumerate(persone, start=1):
            lavoratori.append(
                Lavoratore.objects.create(
                    nome=nome,
                    cognome=cognome,
                    matricola=f"D{i:03d}",
                    reparto=reparti[rep],
                    costo_orario=Decimal(costo) if costo else None,
                    codice_impronta=tim.impronta(f"{1000 + i}"),
                    codice_impostato_il=timezone.now(),
                )
            )

        def alle(giorno: dt.date, h: int, m: int) -> dt.datetime:
            return timezone.make_aware(dt.datetime.combine(giorno, dt.time(h, m)))

        adesso = timezone.now()
        giorni = [self.oggi - dt.timedelta(days=n) for n in range(14, -1, -1)]
        for giorno in (g for g in giorni if g.weekday() < 5):
            for lav in lavoratori:
                if self.rnd.random() < 0.08:  # assente
                    continue
                e = self.rnd.randint(0, 12)
                eventi = [
                    ("ENT", alle(giorno, 7, 45 + e), None),
                    ("IRP", alle(giorno, 7, 45 + e) + dt.timedelta(minutes=3), lav.reparto),
                    ("IME", alle(giorno, 12, 30 + self.rnd.randint(0, 5)), None),
                    ("FME", alle(giorno, 13, 15 + self.rnd.randint(0, 5)), None),
                ]
                if self.rnd.random() < 0.35:  # pomeriggio in un altro reparto
                    altro = reparti[self.rnd.choice(["GK01-PLS", "GK01-CBL", "GK01-ASM"])]
                    eventi.append(("IRP", alle(giorno, 15, self.rnd.randint(0, 30)), altro))
                if not (giorno == self.oggi or self.rnd.random() < 0.03):  # oggi: ancora dentro; a volte dimenticata
                    eventi.append(("USC", alle(giorno, 17, self.rnd.randint(0, 20)), None))
                for tipo, momento, rep in eventi:
                    if momento <= adesso:
                        Timbratura.objects.create(lavoratore=lav, tipo=tipo, momento=momento, reparto=rep)
        self.stdout.write("  timbrature di prova: codici 1001…1006 (Timbrature → Postazioni per i link)")

    def _tracciatura(self) -> None:
        """Tre modelli (collaudo pulsantiere, controllo lotto in ingresso, saldatura) e alcune tracciature."""
        from dumetodo.tracciatura import servizi as tr

        if Modello.objects.filter(nome="Collaudo pulsantiera").exists():
            return
        admin = User.objects.filter(is_superuser=True).first()
        if admin is None:
            return
        collaudo = tr.salva_modello(
            admin,
            None,
            {
                "nome": "Collaudo pulsantiera",
                "categoria": "QUA",
                "uso_ordine": "O",
                "uso_articolo": "F",
                "codici": "18090*",
                "descrizione": "A fine linea, su un pezzo ogni 10.",
            },
        )
        c = {
            "visivo": tr.salva_campo(
                admin, collaudo.pk, None, {"etichetta": "Controllo visivo", "tipo": "ESI", "obbligatorio": True}
            ),
            "forza": tr.salva_campo(
                admin,
                collaudo.pk,
                None,
                {
                    "etichetta": "Forza di attuazione",
                    "tipo": "DEC",
                    "unita": "N",
                    "minimo": "2,5",
                    "massimo": "4",
                    "obbligatorio": True,
                },
            ),
            "pezzi": tr.salva_campo(admin, collaudo.pk, None, {"etichetta": "Pezzi controllati", "tipo": "INT"}),
            "colore": tr.salva_campo(
                admin,
                collaudo.pk,
                None,
                {
                    "etichetta": "Colore",
                    "tipo": "SCE",
                    "scelte": "Conforme al campione\nDifforme",
                    "scelte_non_conformi": "Difforme",
                },
            ),
            "foto": tr.salva_campo(admin, collaudo.pk, None, {"etichetta": "Foto del pezzo", "tipo": "FOT"}),
        }
        lotto = tr.salva_modello(
            admin,
            None,
            {
                "nome": "Controllo lotto in ingresso",
                "categoria": "LOT",
                "uso_ordine": "",
                "uso_articolo": "",
                "uso_ddt": "F",
                "uso_componente": "O",
                "uso_lotto": "O",
            },
        )
        tr.salva_campo(admin, lotto.pk, None, {"etichetta": "Quantità ricevuta", "tipo": "INT", "obbligatorio": True})
        tr.salva_campo(admin, lotto.pk, None, {"etichetta": "Certificato del fornitore", "tipo": "DOC"})
        tr.salva_campo(admin, lotto.pk, None, {"etichetta": "Imballo integro", "tipo": "BOO", "atteso": "si"})
        saldatura = tr.salva_modello(
            admin,
            None,
            {
                "nome": "Saldatura scheda",
                "categoria": "OPE",
                "uso_ordine": "O",
                "uso_riferimento": "F",
                "codici": "2201*",
            },
        )
        tr.salva_campo(
            admin,
            saldatura.pk,
            None,
            {"etichetta": "Temperatura stagnatore", "tipo": "INT", "unita": "°C", "minimo": "320", "massimo": "380"},
        )
        tr.salva_campo(admin, saldatura.pk, None, {"etichetta": "Osservazioni", "tipo": "TXL"})
        ordini_pls = list(Ordine.objects.filter(righe__articolo__startswith="18090").distinct()[:6])
        for i, o in enumerate(ordini_pls):
            forza = f"{Decimal('2.8') + Decimal(self.rnd.randint(0, 14)) / 10}"  # qualche valore fuori limite
            self._prova(
                tr.registra,
                self.operatore,
                collaudo.pk,
                collegamenti={"ordine": o},
                valori={
                    f"campo_{c['visivo'].pk}": "0" if i == 4 else "1",
                    f"campo_{c['forza'].pk}": forza,
                    f"campo_{c['pezzi'].pk}": str(self.rnd.randint(5, 30)),
                    f"campo_{c['colore'].pk}": "Conforme al campione",
                },
            )
        self.stdout.write(f"  tracciatura: 3 modelli e {len(ordini_pls)} collaudi di prova")

    def _prova(self, funzione: Any, *args: Any, **kwargs: Any) -> Any:
        """Esegue un servizio; un rifiuto (regola di business) non ferma il caricamento."""
        try:
            return funzione(*args, **kwargs)
        except ordini.OperazioneNonConsentita as exc:
            self.stderr.write(f"  saltato {funzione.__name__}: {exc}")
            return None

    def _riepilogo_stati(self) -> str:
        conteggi = {s.label: Ordine.objects.filter(stato=s.value).count() for s in StatoOrdine}
        return ", ".join(f"{k}: {v}" for k, v in conteggi.items() if v)
