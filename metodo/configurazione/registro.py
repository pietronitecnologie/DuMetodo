"""Registro dei parametri di configurazione.

Sostituisce la tabella chiave/valore ``Configuration`` di Metodo, che aveva questi problemi:
- valori tutti stringa (max 50 caratteri), parsati a mano ovunque con ``float(x.replace(",", "."))``;
- chiavi non uniche e non documentate, una chiave mancante faceva andare in errore 500 la pagina;
- password SMTP/IMAP in chiaro nel database;
- cursori tecnici (ID_EMAIL, IDX_SYNC) mescolati ai parametri di business.

Nuova organizzazione:
1. infrastruttura (database, chiavi) -> variabili d'ambiente (vedi ``.env.example``); le password che
   l'amministratore deve poter cambiare dall'app (casella ordini) sono parametri ``SEGRETO``, cifrati;
2. parametri di business -> dichiarati QUI, con tipo, default, validazione e descrizione;
   i valori modificati dagli amministratori stanno in ``ValoreParametro`` (storicizzato);
3. soglie di capacità -> campo ``Reparto.soglia_ore_settimanali``;
4. cursori tecnici -> tabelle dedicate (es. ``importazione.StatoCasella``).

Aggiungere un parametro = aggiungere una voce a ``PARAMETRI``. Nessuna migrazione necessaria.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from django.core.exceptions import ValidationError
from django.core.validators import validate_email


class Tipo(StrEnum):
    TESTO = "testo"
    INTERO = "intero"
    DECIMALE = "decimale"
    BOOLEANO = "booleano"
    EMAIL = "email"
    LISTA_EMAIL = "lista_email"
    MENU_CLIENT = "menu_client"  # menu del tasto destro del client desktop (JSON)
    SCORCIATOIE = "scorciatoie"  # scorciatoie da tastiera del client desktop (JSON)
    TRACCIATO = "tracciato"  # formato di un file esportato, nel linguaggio di metodo.tracciati
    IMMAGINE = "immagine"  # PNG normalizzato (base64), vedi configurazione/immagini.py
    SEGRETO = "segreto"  # password: salvata cifrata (SegretoParametro), mai mostrata né storicizzata
    SCELTA = "scelta"  # uno dei valori di Parametro.scelte


class Gruppo(StrEnum):
    AZIENDA = "Azienda"
    PRODUZIONE = "Produzione"
    ORDINI = "Ordini"
    IMPORTAZIONE = "Importazione ordini"
    CASELLA = "Casella ordini (IMAP)"
    COMUNICAZIONI = "Note e comunicazioni"
    EMAIL = "Indirizzi email"
    TRACCIABILITA = "Tracciabilità"
    CONSEGNA = "Consegna e file per il gestionale"
    PIANIFICAZIONE = "Pianificazione e tabulati"
    CLIENT = "Client desktop"
    MAGAZZINO = "Magazzino"
    # Sezione Gestionale (permessi riservati): si modificano da Gestionale → Impostazioni, non da Configurazione.
    GESTIONALE_FORNITORE = "Dati fiscali del Fornitore (cedente)"
    GESTIONALE_CLIENTE = "Dati fiscali del Cliente (cessionario e destinatario)"
    GESTIONALE_DOCUMENTI = "IVA, pagamento e DDT"


GRUPPI_GESTIONALE = (Gruppo.GESTIONALE_FORNITORE, Gruppo.GESTIONALE_CLIENTE, Gruppo.GESTIONALE_DOCUMENTI)


@dataclass(frozen=True)
class Parametro:
    chiave: str
    tipo: Tipo
    gruppo: Gruppo
    etichetta: str
    descrizione: str
    default: Any = None
    obbligatorio: bool = False
    minimo: Decimal | int | None = None
    legacy: tuple[str, ...] = field(default=())
    scelte: tuple[tuple[str, str], ...] = field(default=())

    # --- conversioni ---------------------------------------------------------------
    def converti(self, valore: Any) -> Any:
        """Converte e valida un valore in ingresso (stringa da form/import o valore Python)."""
        if valore is None or (isinstance(valore, str) and valore.strip() == "" and self.tipo != Tipo.BOOLEANO):
            if self.obbligatorio:
                raise ValidationError(f"{self.etichetta}: valore obbligatorio.")
            return [] if self.tipo == Tipo.LISTA_EMAIL else None
        match self.tipo:
            case Tipo.TESTO:
                return str(valore).strip()
            case Tipo.SCELTA:
                v = str(valore).strip()
                if v not in {codice for codice, _ in self.scelte}:
                    raise ValidationError(f"{self.etichetta}: valore non ammesso ({v!r}).")
                return v
            case Tipo.SEGRETO:
                return str(valore)
            case Tipo.IMMAGINE:
                from .immagini import normalizza

                if hasattr(valore, "read"):  # file caricato dal form
                    return normalizza(valore.read(), self.etichetta)
                return str(valore)  # già normalizzato (letto dal database)
            case Tipo.INTERO:
                try:
                    v = int(str(valore).strip())
                except ValueError as exc:
                    raise ValidationError(f"{self.etichetta}: deve essere un numero intero.") from exc
                self._controlla_minimo(v)
                return v
            case Tipo.DECIMALE:
                testo = str(valore).strip().replace(" ", "")
                if "," in testo:  # formato italiano: 1.234,56
                    testo = testo.replace(".", "").replace(",", ".")
                try:
                    d = Decimal(testo)
                except InvalidOperation as exc:
                    raise ValidationError(f"{self.etichetta}: numero non valido ({valore!r}).") from exc
                if not d.is_finite():
                    raise ValidationError(f"{self.etichetta}: numero non valido.")
                self._controlla_minimo(d)
                return d
            case Tipo.BOOLEANO:
                if isinstance(valore, bool):
                    return valore
                return str(valore).strip().lower() in {"1", "true", "si", "sì", "on", "yes"}
            case Tipo.EMAIL:
                v = str(valore).strip()
                validate_email(v)
                return v
            case Tipo.TRACCIATO:
                from metodo.tracciati.linguaggio import ErroreTracciato, compila

                testo = str(valore).replace("\r\n", "\n")
                try:
                    compila(testo)
                except ErroreTracciato as exc:
                    raise ValidationError(f"{self.etichetta}: {exc}") from exc
                return testo
            case Tipo.SCORCIATOIE:
                from metodo.client.scorciatoie import ScorciatoieNonValide
                from metodo.client.scorciatoie import valida as valida_scorciatoie

                testo = str(valore).replace("\r\n", "\n")
                try:
                    valida_scorciatoie(testo)
                except ScorciatoieNonValide as exc:
                    raise ValidationError(f"{self.etichetta}: {exc}") from exc
                return testo
            case Tipo.MENU_CLIENT:
                from metodo.client.menu import MenuNonValido, valida

                testo = str(valore).replace("\r\n", "\n")
                try:
                    valida(testo)
                except MenuNonValido as exc:
                    raise ValidationError(f"{self.etichetta}: {exc}") from exc
                return testo
            case Tipo.LISTA_EMAIL:
                voci = valore if isinstance(valore, list | tuple) else str(valore).replace(";", ",").split(",")
                indirizzi = []
                for voce in voci:
                    v = str(voce).strip()
                    if not v:
                        continue
                    try:
                        validate_email(v)
                    except ValidationError as exc:
                        raise ValidationError(f"{self.etichetta}: indirizzo non valido {v!r}.") from exc
                    if v.lower() not in {i.lower() for i in indirizzi}:
                        indirizzi.append(v)
                if self.obbligatorio and not indirizzi:
                    raise ValidationError(f"{self.etichetta}: almeno un indirizzo è obbligatorio.")
                return indirizzi
        raise AssertionError(self.tipo)  # pragma: no cover

    def _controlla_minimo(self, v: Decimal | int) -> None:
        if self.minimo is not None and v < self.minimo:
            raise ValidationError(f"{self.etichetta}: deve essere almeno {self.minimo}.")

    def valore_default(self) -> Any:
        if self.default is None:
            return [] if self.tipo == Tipo.LISTA_EMAIL else None
        return self.default

    def a_json(self, valore: Any) -> Any:
        if isinstance(valore, Decimal):
            return str(valore)
        return valore

    def da_json(self, grezzo: Any) -> Any:
        return self.converti(grezzo)

    def come_testo(self, valore: Any) -> str:
        if valore is None:
            return ""
        if self.tipo == Tipo.LISTA_EMAIL:
            return ", ".join(valore)
        if self.tipo == Tipo.BOOLEANO:
            return "Sì" if valore else "No"
        if self.tipo == Tipo.IMMAGINE:
            return "immagine caricata"
        return str(valore)

    def etichetta_scelta(self, valore: Any) -> str:
        return dict(self.scelte).get(str(valore), str(valore or ""))


from metodo.client.menu import PREDEFINITO as _MENU_PREDEFINITO  # noqa: E402
from metodo.client.scorciatoie import PREDEFINITE_JSON as _SCORCIATOIE_JSON  # noqa: E402
from metodo.tracciati.esempi import FILE_TECNICO_DDT, READYPRO_BOLLA  # noqa: E402

_MENU_CLIENT_PREDEFINITO = json.dumps(_MENU_PREDEFINITO, ensure_ascii=False, indent=2)
_SCORCIATOIE_PREDEFINITE = _SCORCIATOIE_JSON

PARAMETRI: tuple[Parametro, ...] = (
    # --- Azienda ------------------------------------------------------------------------
    Parametro(
        "azienda.nome_fornitore",
        Tipo.TESTO,
        Gruppo.AZIENDA,
        "Nome dell'azienda (Fornitore)",
        "Ragione sociale o nome breve dell'azienda che usa Metodo v2: compare nell'intestazione, nelle note "
        "e nelle email.",
        default="Fornitore",
    ),
    Parametro(
        "azienda.nome_cliente",
        Tipo.TESTO,
        Gruppo.AZIENDA,
        "Nome del Cliente",
        "Nome del Cliente che invia gli ordini, usato nelle note e nelle email.",
        default="Cliente",
    ),
    Parametro(
        "azienda.logo",
        Tipo.IMMAGINE,
        Gruppo.AZIENDA,
        "Logo dell'azienda",
        "PNG o JPEG (meglio quadrato, con fondo trasparente). Compare in testa al menu del tasto destro del "
        "client desktop. Viene ridotto a 256 px.",
    ),
    # --- Produzione -----------------------------------------------------------------
    Parametro(
        "produzione.prezzo_resa_oraria",
        Tipo.DECIMALE,
        Gruppo.PRODUZIONE,
        "Prezzo resa oraria (€/ora)",
        "Valore di produzione (prezzo × quantità) corrispondente a un'ora di lavoro. "
        "Ore teoriche = Σ(qtà × prezzo) / resa oraria. Usato da pianificazione e tabulati.",
        minimo=Decimal("0.01"),
        legacy=("PREZZO_RESA_ORARIA",),
    ),
    Parametro(
        "produzione.report_settimane",
        Tipo.INTERO,
        Gruppo.PRODUZIONE,
        "Settimane nel report «In scadenza e consegnato»",
        "Quante settimane mostra il report, contando la corrente (9 = la corrente e le 8 precedenti). "
        "Nella pagina si può comunque cambiare di volta in volta.",
        default=9,
        minimo=1,
    ),
    # --- Ordini -----------------------------------------------------------------------
    Parametro(
        "ordini.serie_fast",
        Tipo.TESTO,
        Gruppo.ORDINI,
        "Serie ordini Fast",
        "Numero di serie che identifica gli ordini Fast.",
        default="43",
    ),
    Parametro(
        "ordini.serie_express",
        Tipo.TESTO,
        Gruppo.ORDINI,
        "Serie ordini Express",
        "Numero di serie che identifica gli ordini Express.",
        default="42",
    ),
    Parametro(
        "ordini.soglia_pezzi_commesse_grandi",
        Tipo.INTERO,
        Gruppo.ORDINI,
        "Soglia pezzi commesse grandi",
        "Agenda Pulsanti: commesse con più pezzi sono 'grandi'.",
        default=60,
        minimo=1,
    ),
    Parametro(
        "anagrafica.reparto_nuovi_articoli",
        Tipo.TESTO,
        Gruppo.ORDINI,
        "Reparto dei nuovi articoli",
        "Codice del reparto assegnato agli articoli registrati automaticamente dall'importazione "
        "(in Metodo: GK01-UDEF, 'non definito').",
        default="GK01-UDEF",
    ),
    # --- Importazione -------------------------------------------------------------------
    Parametro(
        "importazione.lettura_mail_attiva",
        Tipo.BOOLEANO,
        Gruppo.IMPORTAZIONE,
        "Lettura automatica casella ordini",
        "Se disattivo, gli ordini si importano solo a mano.",
        default=True,
    ),
    Parametro(
        "importazione.ripristina_stato_su_reimport",
        Tipo.BOOLEANO,
        Gruppo.IMPORTAZIONE,
        "Reimportazione riporta l'ordine a 'Inserito'",
        "Comportamento di Metodo: quando un ordine già presente (non consegnato/annullato) viene "
        "reimportato, il suo stato torna a 'Inserito'. Disattivare per mantenere lo stato attuale.",
        default=True,
    ),
    Parametro(
        "importazione.dimensione_massima_allegato_mb",
        Tipo.INTERO,
        Gruppo.IMPORTAZIONE,
        "Dimensione massima allegato (MB)",
        "Allegati più grandi vengono scartati (protezione zip-bomb).",
        default=20,
        minimo=1,
    ),
    Parametro(
        "importazione.notifica_errori",
        Tipo.LISTA_EMAIL,
        Gruppo.IMPORTAZIONE,
        "Destinatari avvisi errori di importazione",
        "Chi riceve un'email quando un file d'ordine non può essere importato.",
    ),
    # --- Casella ordini (IMAP) ----------------------------------------------------------
    # Se il server è vuoto valgono le variabili ORDINI_IMAP_* del file .env (compatibilità).
    Parametro(
        "casella.server",
        Tipo.TESTO,
        Gruppo.CASELLA,
        "Server IMAP",
        "Es. imap.gmail.com, outlook.office365.com, mail.azienda.it. Vuoto = valori del file .env.",
        default="",
    ),
    Parametro("casella.porta", Tipo.INTERO, Gruppo.CASELLA, "Porta", "993 con SSL, 143 senza.", default=993, minimo=1),
    Parametro(
        "casella.ssl",
        Tipo.BOOLEANO,
        Gruppo.CASELLA,
        "Connessione cifrata (SSL/TLS)",
        "Lasciare attivo salvo indicazioni diverse del fornitore della posta.",
        default=True,
    ),
    Parametro(
        "casella.utente",
        Tipo.TESTO,
        Gruppo.CASELLA,
        "Utente / indirizzo della casella",
        "L'indirizzo a cui il Cliente invia gli ordini (es. ordini@azienda.it).",
        default="",
    ),
    Parametro(
        "casella.password",
        Tipo.SEGRETO,
        Gruppo.CASELLA,
        "Password",
        "Salvata cifrata e mai mostrata. Lascia vuoto per non cambiarla. Con Gmail/Microsoft 365 usa una "
        "'password per le app'.",
    ),
    Parametro(
        "casella.cartella",
        Tipo.TESTO,
        Gruppo.CASELLA,
        "Cartella",
        "Cartella da leggere (di solito INBOX).",
        default="INBOX",
    ),
    Parametro(
        "casella.mittenti_ammessi",
        Tipo.LISTA_EMAIL,
        Gruppo.CASELLA,
        "Mittenti ammessi",
        "Si importano solo gli ordini inviati da questi indirizzi; gli altri messaggi vengono scartati e "
        "registrati. Vuoto = qualunque mittente.",
    ),
    # --- Comunicazioni ------------------------------------------------------------------
    Parametro(
        "note.notifica_email",
        Tipo.BOOLEANO,
        Gruppo.COMUNICAZIONI,
        "Notifica email delle note",
        "Invia un'email alla controparte quando viene scritta una nota condivisa.",
        default=True,
    ),
    # --- Indirizzi email ----------------------------------------------------------------
    Parametro(
        "email.mittente",
        Tipo.EMAIL,
        Gruppo.EMAIL,
        "Mittente email di sistema",
        "Indirizzo 'From' delle email inviate da Metodo v2.",
        legacy=("EMAIL_METODO_ADDRESS",),
    ),
    Parametro(
        "email.fornitore",
        Tipo.LISTA_EMAIL,
        Gruppo.EMAIL,
        "Indirizzi Fornitore",
        "Ricevono le note scritte dal cliente.",
        legacy=("EMAIL_GEKO_ADDRESS",),
    ),
    Parametro(
        "email.cliente",
        Tipo.LISTA_EMAIL,
        Gruppo.EMAIL,
        "Indirizzi cliente",
        "Ricevono le note condivise scritte dal Fornitore.",
        legacy=("EMAIL_CUSTOMER_ADDRESS",),
    ),
    Parametro(
        "email.file_tecnico",
        Tipo.LISTA_EMAIL,
        Gruppo.EMAIL,
        "Destinatari file tecnico DDT",
        "Ricevono il file tecnico dei DDT.",
        legacy=("EMAIL_FIETECNICO_ADDRESS",),
    ),
    Parametro(
        "email.avvisi_server",
        Tipo.LISTA_EMAIL,
        Gruppo.EMAIL,
        "Destinatari avvisi del server",
        "Ricevono gli avvisi di MetodoConsole: servizi fermi, errori di diagnostica, backup non riusciti.",
    ),
    Parametro(
        "email.magazzino_fornitore",
        Tipo.LISTA_EMAIL,
        Gruppo.EMAIL,
        "Magazzino Fornitore",
        "Rispondi-a delle richieste materiali.",
        legacy=("EMAIL_MAGAZZINO_GEKO",),
    ),
    Parametro(
        "email.magazzino_cliente",
        Tipo.LISTA_EMAIL,
        Gruppo.EMAIL,
        "Magazzino cliente",
        "Destinatari delle richieste materiali.",
        legacy=("EMAIL_MAGAZZINO_DMG_1", "EMAIL_MAGAZZINO_DMG_2"),
    ),
    Parametro(
        "email.report_produzione_to",
        Tipo.LISTA_EMAIL,
        Gruppo.EMAIL,
        "Report pianificazione: A",
        "Destinatari del report giornaliero di pianificazione.",
        legacy=("EMAIL_REPORT_PRODUZIONE_TO",),
    ),
    Parametro(
        "email.report_produzione_cc",
        Tipo.LISTA_EMAIL,
        Gruppo.EMAIL,
        "Report pianificazione: CC",
        "Destinatari in copia del report di pianificazione.",
        legacy=("EMAIL_REPORT_PRODUZIONE_CC",),
    ),
    # --- Pianificazione ----------------------------------------------------------------
    Parametro(
        "pianificazione.settimane",
        Tipo.INTERO,
        Gruppo.PIANIFICAZIONE,
        "Settimane pianificate",
        "Numero di settimane mostrate nel piano (corrente compresa).",
        default=4,
        minimo=1,
    ),
    Parametro(
        "pianificazione.settimane_arretrato",
        Tipo.INTERO,
        Gruppo.PIANIFICAZIONE,
        "Settimane di arretrato",
        "Quante settimane passate considerare come arretrato (in Metodo: 8).",
        default=8,
        minimo=1,
    ),
    Parametro(
        "pianificazione.ora_inizio",
        Tipo.INTERO,
        Gruppo.PIANIFICAZIONE,
        "Inizio turno (ora)",
        "Ora di inizio lavoro, lunedì-venerdì: serve a calcolare la capacità residua della settimana.",
        default=8,
        minimo=0,
    ),
    Parametro(
        "pianificazione.ora_fine",
        Tipo.INTERO,
        Gruppo.PIANIFICAZIONE,
        "Fine turno (ora)",
        "Ora di fine lavoro, lunedì-venerdì.",
        default=17,
        minimo=1,
    ),
    Parametro(
        "pianificazione.soglia_attenzione",
        Tipo.INTERO,
        Gruppo.PIANIFICAZIONE,
        "Soglia di attenzione (%)",
        "Saturazione oltre la quale il reparto è in giallo (oltre 100% è rosso).",
        default=80,
        minimo=1,
    ),
    Parametro(
        "pianificazione.solo_ordini_con_appendice",
        Tipo.BOOLEANO,
        Gruppo.PIANIFICAZIONE,
        "Pianifica solo ordini con appendice",
        "Come in Metodo: si pianificano le commesse interne (T, R, CL...), non gli ordini del Cliente senza appendice.",
        default=True,
    ),
    Parametro(
        "pianificazione.parole_bloccanti",
        Tipo.TESTO,
        Gruppo.PIANIFICAZIONE,
        "Parole delle note bloccanti",
        "Una commessa con una nota che contiene una di queste parole (separate da virgola) compare tra le "
        "'Commesse bloccate' del report.",
        default="manca, bloccat",
    ),
    Parametro(
        "pianificazione.report_automatico",
        Tipo.BOOLEANO,
        Gruppo.PIANIFICAZIONE,
        "Invio automatico del report",
        "Invia ogni giorno lavorativo alle 6:00 il report ai destinatari configurati negli indirizzi email.",
        default=True,
    ),
    # --- Client desktop -----------------------------------------------------------------
    Parametro(
        "client.menu",
        Tipo.MENU_CLIENT,
        Gruppo.CLIENT,
        "Menu del tasto destro",
        "Voci del menu nativo del client desktop per ogni tipo di elemento (pagina, ordine, riga...), in JSON. "
        "Azioni: naviga, post, copia, stampa, ricarica, indietro, avanti, schermo_intero. Una voce con "
        "'permesso' compare solo a chi ha quella funzione. {nome} = dato dell'elemento cliccato.",
        default=_MENU_CLIENT_PREDEFINITO,
        obbligatorio=True,
    ),
    Parametro(
        "client.scorciatoie",
        Tipo.SCORCIATOIE,
        Gruppo.CLIENT,
        "Scorciatoie da tastiera",
        "Tasti del client desktop (F1-F12 o combinazioni con Ctrl/Alt), in JSON: tasto, etichetta, azione "
        "(naviga con url, cerca_ordine, nuova_scheda, ricarica, stampa, indietro, avanti, schermo_intero, aiuto) "
        "ed eventuale 'permesso'. F1 mostra l'elenco a chi usa il client.",
        default=_SCORCIATOIE_PREDEFINITE,
        obbligatorio=True,
    ),
    # --- Consegna --------------------------------------------------------------------
    Parametro(
        "consegna.tracciato_bolla",
        Tipo.TRACCIATO,
        Gruppo.CONSEGNA,
        "Tracciato del file bolla",
        "Formato del file generato alla consegna, da importare nel gestionale. "
        "Predefinito: ReadyPro (identico a Metodo). Vedi la guida al linguaggio dei tracciati.",
        default=READYPRO_BOLLA,
        obbligatorio=True,
    ),
    Parametro(
        "consegna.tracciato_file_tecnico_ddt",
        Tipo.TRACCIATO,
        Gruppo.CONSEGNA,
        "Tracciato del file tecnico DDT",
        "Formato del file inviato al Cliente quando si registra un DDT. Predefinito: formato di Metodo "
        "(codice Fornitore 3649). I campi documento.numero e documento.data sono numero e data del DDT.",
        default=FILE_TECNICO_DDT,
        obbligatorio=True,
    ),
    Parametro(
        "consegna.codice_fornitore_etichette",
        Tipo.TESTO,
        Gruppo.CONSEGNA,
        "Codice Fornitore sulle etichette",
        "Valore della colonna I del file etichette (in Metodo era fisso: 0003659).",
        default="0003659",
    ),
    # --- Magazzino ----------------------------------------------------------------------
    Parametro(
        "magazzino.reparti_richieste",
        Tipo.TESTO,
        Gruppo.MAGAZZINO,
        "Reparti con richiesta materiali",
        "Codici dei reparti proposti nella pagina Richiesta materiali, separati da virgola "
        "(in Metodo erano fissi: Pulsanti GK01-PLS ed Elettronica GK01-ETT).",
        default="GK01-PLS, GK01-ETT",
    ),
    Parametro(
        "magazzino.dimensione_massima_file_ddt_mb",
        Tipo.INTERO,
        Gruppo.MAGAZZINO,
        "Dimensione massima file DDT (MB)",
        "I file CSV/TXT/XML dei DDT in ingresso più grandi vengono rifiutati.",
        default=5,
        minimo=1,
    ),
    Parametro(
        "magazzino.xml_tipi_documento",
        Tipo.TESTO,
        Gruppo.MAGAZZINO,
        "Tipi documento XML accettati",
        "Valori di <DocType> importati come DDT in ingresso, separati da virgola (es. 5). "
        "Vuoto = tutti i documenti, come in Metodo.",
        default="",
    ),
    # --- Tracciabilità e qualità --------------------------------------------------------
    Parametro(
        "tracciabilita.dimensione_massima_file_mb",
        Tipo.INTERO,
        Gruppo.TRACCIABILITA,
        "Dimensione massima foto/file (MB)",
        "Foto e documenti allegati agli ordini.",
        default=10,
        minimo=1,
    ),
)

from metodo.gestionale.costanti import (  # noqa: E402
    CAUSALI_TRASPORTO,
    ESIGIBILITA_IVA,
    MODALITA_PAGAMENTO,
    NATURE_IVA,
    PORTI,
    REGIMI_FISCALI,
    TRASPORTO_A_CURA,
)


def _p(chiave: str, gruppo: Gruppo, etichetta: str, descrizione: str = "", **extra: Any) -> Parametro:
    return Parametro(f"gestionale.{chiave}", extra.pop("tipo", Tipo.TESTO), gruppo, etichetta, descrizione, **extra)


_FORN, _CLI, _DOC = GRUPPI_GESTIONALE
PARAMETRI_GESTIONALE: tuple[Parametro, ...] = (
    _p(
        "fornitore_denominazione", _FORN, "Ragione sociale", "Vuoto = il nome del Fornitore (Configurazione → Azienda)."
    ),
    _p("fornitore_partita_iva", _FORN, "Partita IVA", "11 cifre, senza IT."),
    _p("fornitore_codice_fiscale", _FORN, "Codice fiscale", "Vuoto = uguale alla partita IVA."),
    _p("fornitore_indirizzo", _FORN, "Indirizzo (via e civico)"),
    _p("fornitore_cap", _FORN, "CAP"),
    _p("fornitore_comune", _FORN, "Comune"),
    _p("fornitore_provincia", _FORN, "Provincia (sigla)"),
    _p("fornitore_nazione", _FORN, "Nazione (sigla)", default="IT"),
    _p("fornitore_regime_fiscale", _FORN, "Regime fiscale", tipo=Tipo.SCELTA, scelte=REGIMI_FISCALI, default="RF01"),
    _p(
        "fornitore_rea_ufficio", _FORN, "REA: provincia dell'ufficio", "Sigla, es. MI (vuoto = nessuna iscrizione REA)."
    ),
    _p("fornitore_rea_numero", _FORN, "REA: numero"),
    _p("fornitore_capitale_sociale", _FORN, "Capitale sociale versato", "Es. 10000.00 (solo società di capitali)."),
    _p("fornitore_telefono", _FORN, "Telefono"),
    _p("fornitore_email", _FORN, "Email"),
    _p("cliente_denominazione", _CLI, "Ragione sociale", "Vuoto = il nome del Cliente (Configurazione → Azienda)."),
    _p("cliente_partita_iva", _CLI, "Partita IVA", "Senza prefisso del paese."),
    _p("cliente_codice_fiscale", _CLI, "Codice fiscale"),
    _p("cliente_indirizzo", _CLI, "Indirizzo (via e civico)"),
    _p("cliente_cap", _CLI, "CAP"),
    _p("cliente_comune", _CLI, "Comune"),
    _p("cliente_provincia", _CLI, "Provincia (sigla)"),
    _p("cliente_nazione", _CLI, "Nazione (sigla)", default="IT"),
    _p("cliente_codice_destinatario", _CLI, "Codice destinatario SdI", "7 caratteri (0000000 se si usa la PEC)."),
    _p("cliente_pec", _CLI, "PEC", "Solo con codice destinatario 0000000."),
    _p(
        "cliente_destinazione",
        _CLI,
        "Luogo di destinazione delle merci",
        "Sui DDT, se diverso dalla sede (es. «Magazzino, via …, CAP Comune (PV)»).",
    ),
    _p("aliquota_iva", _DOC, "Aliquota IVA (%)", tipo=Tipo.DECIMALE, default=Decimal("22"), minimo=Decimal("0")),
    _p(
        "natura_iva",
        _DOC,
        "Natura (solo con aliquota 0)",
        "Motivo per cui l'IVA non si applica (esente, non imponibile, reverse charge…).",
        tipo=Tipo.SCELTA,
        scelte=NATURE_IVA,
        default="",
    ),
    _p("riferimento_normativo", _DOC, "Riferimento normativo (con aliquota 0)", "Es. «Art. 8 c.1 lett. c DPR 633/72»."),
    _p("esigibilita_iva", _DOC, "Esigibilità IVA", tipo=Tipo.SCELTA, scelte=ESIGIBILITA_IVA, default="I"),
    _p(
        "pagamento_modalita", _DOC, "Modalità di pagamento", tipo=Tipo.SCELTA, scelte=MODALITA_PAGAMENTO, default="MP05"
    ),
    _p("pagamento_giorni", _DOC, "Giorni di pagamento", tipo=Tipo.INTERO, default=60, minimo=0),
    _p(
        "pagamento_fine_mese",
        _DOC,
        "Scadenza a fine mese",
        "Es. 60 giorni fine mese.",
        tipo=Tipo.BOOLEANO,
        default=True,
    ),
    _p("pagamento_iban", _DOC, "IBAN (per i bonifici)"),
    _p("pagamento_banca", _DOC, "Banca"),
    _p(
        "ddt_causale",
        _DOC,
        "DDT: causale del trasporto predefinita",
        tipo=Tipo.SCELTA,
        scelte=CAUSALI_TRASPORTO,
        default="Vendita",
    ),
    _p("ddt_aspetto_beni", _DOC, "DDT: aspetto dei beni predefinito", default="Scatole"),
    _p(
        "ddt_trasporto_a_cura",
        _DOC,
        "DDT: trasporto a cura di",
        tipo=Tipo.SCELTA,
        scelte=TRASPORTO_A_CURA,
        default="Mittente",
    ),
    _p("ddt_porto", _DOC, "DDT: porto", tipo=Tipo.SCELTA, scelte=PORTI, default="Franco"),
    _p(
        "ddt_vettore",
        _DOC,
        "DDT: vettore predefinito",
        "Ragione sociale del corriere, se il trasporto è a cura del vettore.",
    ),
    _p("ddt_vettore_indirizzo", _DOC, "DDT: indirizzo del vettore"),
)
PARAMETRI = PARAMETRI + PARAMETRI_GESTIONALE

REGISTRO: dict[str, Parametro] = {p.chiave: p for p in PARAMETRI}
assert len(REGISTRO) == len(PARAMETRI), "chiavi duplicate nel registro parametri"

# Chiavi della vecchia tabella che NON devono finire nel database.
LEGACY_SEGRETI: dict[str, str] = {
    "EMAIL_SERVER": "EMAIL_HOST / ORDINI_IMAP_HOST",
    "EMAIL_METODO_USER": "EMAIL_HOST_USER",
    "EMAIL_METODO_PASSWORD": "EMAIL_HOST_PASSWORD",
    "EMAIL_ORDINI_USER": "ORDINI_IMAP_USER",
    "EMAIL_ORDINI_PASSWORD": "ORDINI_IMAP_PASSWORD",
    "MAIL_TICKET": "(app Tickets, fuori da Metodo v2)",
    "MAIL_TICKET_PSW": "(app Tickets, fuori da Metodo v2)",
}
# Cursori tecnici della vecchia tabella: sostituiti da tabelle dedicate, non vanno importati.
# ARTICOLO_TRACCIA_* e ITEM_CODE_CAVI: tracciabilità di batterie e cavi eliminata da Metodo v2.
LEGACY_IGNORATI = {
    "ID_EMAIL",
    "IDX_SYNC",
    "ID_MIGRAZIONE",
    "ARTICOLO_TRACCIA_BATTERIE",
    "ARTICOLO_TRACCIA_CAVI",
    "ITEM_CODE_CAVI",
}
LEGACY_PREFISSO_SOGLIE = "SOGLIA_"
