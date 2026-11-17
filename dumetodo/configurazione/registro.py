"""Registro dei parametri di configurazione.

Sostituisce la tabella chiave/valore ``Configuration`` di Metodo, che aveva questi problemi:
- valori tutti stringa (max 50 caratteri), parsati a mano ovunque con ``float(x.replace(",", "."))``;
- chiavi non uniche e non documentate, una chiave mancante faceva andare in errore 500 la pagina;
- password SMTP/IMAP in chiaro nel database;
- cursori tecnici (ID_EMAIL, IDX_SYNC) mescolati ai parametri di business.

Nuova organizzazione:
1. credenziali e infrastruttura -> variabili d'ambiente (vedi ``.env.example``);
2. parametri di business -> dichiarati QUI, con tipo, default, validazione e descrizione;
   i valori modificati dagli amministratori stanno in ``ValoreParametro`` (storicizzato);
3. soglie di capacità -> campo ``Reparto.soglia_ore_settimanali``;
4. cursori tecnici -> tabelle dedicate (es. ``importazione.StatoCasella``).

Aggiungere un parametro = aggiungere una voce a ``PARAMETRI``. Nessuna migrazione necessaria.
"""

from __future__ import annotations

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
    TRACCIATO = "tracciato"  # formato di un file esportato, nel linguaggio di dumetodo.tracciati


class Gruppo(StrEnum):
    PRODUZIONE = "Produzione"
    ORDINI = "Ordini"
    IMPORTAZIONE = "Importazione ordini"
    COMUNICAZIONI = "Note e comunicazioni"
    EMAIL = "Indirizzi email"
    TRACCIABILITA = "Tracciabilità"
    CONSEGNA = "Consegna e file per il gestionale"


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
                from dumetodo.tracciati.linguaggio import ErroreTracciato, compila

                testo = str(valore).replace("\r\n", "\n")
                try:
                    compila(testo)
                except ErroreTracciato as exc:
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
        return str(valore)


from dumetodo.tracciati.esempi import FILE_TECNICO_DDT, READYPRO_BOLLA  # noqa: E402

PARAMETRI: tuple[Parametro, ...] = (
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
        "Indirizzo 'From' delle email inviate da DuMetodo.",
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
    # --- Tracciabilità ------------------------------------------------------------------
    Parametro(
        "tracciabilita.articolo_batterie",
        Tipo.TESTO,
        Gruppo.TRACCIABILITA,
        "Articolo batterie",
        "Codice articolo che identifica le batterie tracciate.",
        legacy=("ARTICOLO_TRACCIA_BATTERIE",),
    ),
    Parametro(
        "tracciabilita.articolo_cavi",
        Tipo.TESTO,
        Gruppo.TRACCIABILITA,
        "Articolo cavi",
        "Codice articolo che identifica i cavi tracciati.",
        legacy=("ARTICOLO_TRACCIA_CAVI",),
    ),
    Parametro(
        "tracciabilita.prefisso_codice_cavi",
        Tipo.TESTO,
        Gruppo.TRACCIABILITA,
        "Prefisso codice cavi",
        "Prefisso dei codici generati per i cavi.",
        legacy=("ITEM_CODE_CAVI",),
    ),
)

REGISTRO: dict[str, Parametro] = {p.chiave: p for p in PARAMETRI}
assert len(REGISTRO) == len(PARAMETRI), "chiavi duplicate nel registro parametri"

# Chiavi della vecchia tabella che NON devono finire nel database.
LEGACY_SEGRETI: dict[str, str] = {
    "EMAIL_SERVER": "EMAIL_HOST / ORDINI_IMAP_HOST",
    "EMAIL_METODO_USER": "EMAIL_HOST_USER",
    "EMAIL_METODO_PASSWORD": "EMAIL_HOST_PASSWORD",
    "EMAIL_ORDINI_USER": "ORDINI_IMAP_USER",
    "EMAIL_ORDINI_PASSWORD": "ORDINI_IMAP_PASSWORD",
    "MAIL_TICKET": "(app Tickets, fuori da DuMetodo)",
    "MAIL_TICKET_PSW": "(app Tickets, fuori da DuMetodo)",
}
# Cursori tecnici della vecchia tabella: sostituiti da tabelle dedicate, non vanno importati.
LEGACY_IGNORATI = {"ID_EMAIL", "IDX_SYNC", "ID_MIGRAZIONE"}
LEGACY_PREFISSO_SOGLIE = "SOGLIA_"
