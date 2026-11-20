"""FatturaPA 1.2.2 (formato FPR12, fatture tra privati) da una struttura neutra.

Porting del crate ``fatturapa`` di IN-Assets (``writer.rs``), con l'ordine degli elementi dello schema
ufficiale ``Schema_del_file_xml_FatturaPA_v1.2.2.xsd`` e le specifiche tecniche 1.9:

* ``TD24`` fattura differita (art. 21 c.4 lett. a): un blocco ``DatiDDT`` per ogni DDT con le linee che vi si
  riferiscono; ``TD04`` nota di credito con ``DatiFattureCollegate``; ``TD01`` fattura immediata.
* ``Natura`` solo con aliquota zero, ``DatiRiepilogo`` per aliquota/natura, bollo virtuale da 2 € sui
  documenti senza IVA oltre 77,47 € (nature soggette a bollo).
* Importi con il punto e due decimali (quantità e prezzi fino a 8 decimali significativi).

Nessuna dipendenza esterna: ``xml.etree`` produce XML ben formato e con l'escape corretto.
"""

from __future__ import annotations

import datetime as dt
import xml.etree.ElementTree as ET
from collections import OrderedDict
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

NS = "http://ivaservizi.agenziaentrate.gov.it/docs/xsd/fatture/v1.2"
NS_DS = "http://www.w3.org/2000/09/xmldsig#"
NS_XSI = "http://www.w3.org/2001/XMLSchema-instance"
SOGLIA_BOLLO = Decimal("77.47")
# Nature per cui sulle operazioni oltre 77,47 € senza IVA è dovuta l'imposta di bollo.
NATURE_CON_BOLLO = {"N1", "N2.1", "N2.2", "N3.5", "N3.6", "N4", "N5"}
CENTESIMO = Decimal("0.01")


class FatturaNonValida(ValueError):
    pass


@dataclass
class Soggetto:
    denominazione: str
    partita_iva: str = ""
    codice_fiscale: str = ""
    indirizzo: str = ""
    cap: str = ""
    comune: str = ""
    provincia: str = ""
    nazione: str = "IT"
    regime_fiscale: str = "RF01"  # solo cedente
    rea_ufficio: str = ""  # solo cedente
    rea_numero: str = ""
    capitale_sociale: str = ""
    telefono: str = ""
    email: str = ""
    codice_destinatario: str = ""  # solo cessionario
    pec: str = ""

    @classmethod
    def da_dict(cls, d: dict) -> Soggetto:
        return cls(**{k: str(v or "") for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class Linea:
    numero: int
    descrizione: str
    quantita: Decimal
    prezzo_unitario: Decimal
    aliquota: Decimal
    codice: str = ""
    unita_misura: str = ""
    sconto_unitario: Decimal = Decimal("0")
    natura: str = ""

    @property
    def totale(self) -> Decimal:
        return arrotonda(self.quantita * (self.prezzo_unitario - self.sconto_unitario))


@dataclass
class RiferimentoDDT:
    numero: str
    data: dt.date
    linee: list[int] = field(default_factory=list)


@dataclass
class Pagamento:
    modalita: str = "MP05"
    scadenza: dt.date | None = None
    iban: str = ""
    banca: str = ""


@dataclass
class Documento:
    tipo: str  # TD24, TD01, TD04
    numero: str
    data: dt.date
    cedente: Soggetto
    cessionario: Soggetto
    linee: list[Linea]
    progressivo_invio: str
    esigibilita: str = "I"
    riferimento_normativo: str = ""
    causali: list[str] = field(default_factory=list)
    ddt: list[RiferimentoDDT] = field(default_factory=list)
    fattura_collegata: tuple[str, dt.date] | None = None
    pagamento: Pagamento | None = None
    sistema_emittente: str = "DuMetodo"


# ------------------------------------------------------------------------------------------ calcoli
def arrotonda(d: Decimal) -> Decimal:
    return Decimal(d).quantize(CENTESIMO, rounding=ROUND_HALF_UP)


def riepiloghi(doc: Documento) -> list[dict]:
    """Un riepilogo per (aliquota, natura): imponibile, imposta (arrotondata sul totale), esigibilità."""
    gruppi: OrderedDict[tuple[Decimal, str], Decimal] = OrderedDict()
    for linea in doc.linee:
        chiave = (arrotonda(linea.aliquota), linea.natura)
        gruppi[chiave] = gruppi.get(chiave, Decimal("0")) + linea.totale
    risultato = []
    for (aliquota, natura), imponibile in gruppi.items():
        imposta = Decimal("0") if natura else arrotonda(imponibile * aliquota / 100)
        risultato.append(
            {"aliquota": aliquota, "natura": natura, "imponibile": arrotonda(imponibile), "imposta": imposta}
        )
    return risultato


def totali(doc: Documento) -> tuple[Decimal, Decimal, Decimal]:
    r = riepiloghi(doc)
    imponibile = sum((x["imponibile"] for x in r), Decimal("0"))
    imposta = sum((x["imposta"] for x in r), Decimal("0"))
    return imponibile, imposta, imponibile + imposta


def bollo_dovuto(doc: Documento) -> bool:
    _, imposta, totale = totali(doc)
    return imposta == 0 and totale > SOGLIA_BOLLO and any(linea.natura in NATURE_CON_BOLLO for linea in doc.linee)


# ------------------------------------------------------------------------------------------- formati
def importo2(d: Decimal) -> str:
    return f"{arrotonda(d):.2f}"


def importo8(d: Decimal) -> str:
    """Da 2 a 8 decimali (Quantita, PrezzoUnitario, PrezzoTotale, sconti)."""
    d = Decimal(d).quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP).normalize()
    testo = f"{d:f}"
    intero, _, decimali = testo.partition(".")
    return f"{intero}.{decimali.ljust(2, '0')}"


def solo_codice(valore: str) -> str:
    """P.IVA o codice senza spazi, punti e prefisso del paese (IT01234567890 → 01234567890)."""
    v = "".join(c for c in (valore or "") if c.isalnum()).upper()
    if len(v) > 2 and v[:2].isalpha() and v[2:].isdigit():
        return v[2:]
    return v


def progressivo_invio(n: int) -> str:
    """5 caratteri alfanumerici univoci per ogni n (biunivoco su 62^5, come in IN-Assets): lo SdI scarta un
    nome di file già ricevuto, e i nomi non ricalcano i progressivi di altri gestionali."""
    cifre = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    spazio = 62**5
    v = (max(n, 0) * 387_420_489 + 104_729) % spazio
    out = []
    for _ in range(5):
        out.append(cifre[v % 62])
        v //= 62
    return "".join(reversed(out))


def id_trasmittente(cedente: Soggetto) -> str:
    return solo_codice(cedente.codice_fiscale or cedente.partita_iva)


def nome_file(doc: Documento) -> str:
    return f"IT{id_trasmittente(doc.cedente)}_{doc.progressivo_invio}.xml"


# ------------------------------------------------------------------------------------------ controlli
def controlla(doc: Documento) -> list[str]:
    """Errori che lo SdI scarterebbe (o che rendono il documento non conforme): lista vuota = valido."""
    e: list[str] = []
    ced, ces = doc.cedente, doc.cessionario
    if not ced.denominazione.strip():
        e.append("Fornitore: ragione sociale mancante.")
    if not partita_iva_valida(solo_codice(ced.partita_iva)):
        e.append("Fornitore: partita IVA mancante o non valida.")
    for chi, s in (("Fornitore", ced), ("Cliente", ces)):
        for campo, nome in (("indirizzo", "indirizzo"), ("cap", "CAP"), ("comune", "comune")):
            if not getattr(s, campo).strip():
                e.append(f"{chi}: {nome} mancante.")
        if s.nazione.upper() == "IT" and s.cap.strip() and not (len(s.cap.strip()) == 5 and s.cap.strip().isdigit()):
            e.append(f"{chi}: il CAP deve avere 5 cifre.")
        if s.nazione.upper() == "IT" and s.provincia.strip() and len(s.provincia.strip()) != 2:
            e.append(f"{chi}: la provincia va indicata con la sigla di 2 lettere.")
    if not ces.denominazione.strip():
        e.append("Cliente: ragione sociale mancante.")
    if not ces.partita_iva.strip() and not ces.codice_fiscale.strip():
        e.append("Cliente: serve la partita IVA o il codice fiscale.")
    if ces.nazione.upper() == "IT" and ces.partita_iva.strip() and not partita_iva_valida(solo_codice(ces.partita_iva)):
        e.append("Cliente: partita IVA non valida.")
    codice = (ces.codice_destinatario or "").strip().upper()
    if len(codice) != 7 or not codice.isalnum():
        e.append("Cliente: il codice destinatario SdI deve avere 7 caratteri (0000000 se si usa la PEC).")
    if not doc.linee:
        e.append("Il documento non ha righe.")
    for linea in doc.linee:
        if linea.aliquota == 0 and not linea.natura:
            e.append(f"Linea {linea.numero}: con aliquota 0 serve la natura dell'operazione.")
        if linea.aliquota > 0 and linea.natura:
            e.append(f"Linea {linea.numero}: la natura si indica solo con aliquota 0.")
        if linea.prezzo_unitario < 0 or linea.quantita < 0:
            e.append(f"Linea {linea.numero}: quantità e prezzo non possono essere negativi.")
    if doc.tipo == "TD24" and not doc.ddt:
        e.append("La fattura differita (TD24) deve indicare i DDT.")
    if doc.tipo == "TD04" and not doc.fattura_collegata:
        e.append("La nota di credito deve indicare la fattura stornata.")
    if not any(c.isdigit() for c in doc.numero) or len(doc.numero) > 20:
        e.append("Il numero del documento deve contenere almeno una cifra (massimo 20 caratteri).")
    return e


def partita_iva_valida(piva: str) -> bool:
    """Partita IVA italiana: 11 cifre con la cifra di controllo (algoritmo di Luhn modificato)."""
    if len(piva) != 11 or not piva.isdigit():
        return False
    somma = 0
    for i, c in enumerate(piva[:10]):
        n = int(c)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        somma += n
    return (10 - somma % 10) % 10 == int(piva[10])


# -------------------------------------------------------------------------------------------- scrittura
def _sub(padre: ET.Element, nome: str, testo: str | None = None) -> ET.Element:
    el = ET.SubElement(padre, nome)
    if testo is not None:
        el.text = testo
    return el


def _opz(padre: ET.Element, nome: str, testo: str) -> None:
    if testo and testo.strip():
        _sub(padre, nome, testo.strip())


def _sede(padre: ET.Element, s: Soggetto) -> None:
    sede = _sub(padre, "Sede")
    _sub(sede, "Indirizzo", s.indirizzo.strip()[:60])
    _sub(sede, "CAP", s.cap.strip() if s.nazione.upper() == "IT" else "00000")
    _sub(sede, "Comune", s.comune.strip()[:60])
    if s.nazione.upper() == "IT":
        _opz(sede, "Provincia", s.provincia.strip().upper()[:2])
    _sub(sede, "Nazione", (s.nazione or "IT").strip().upper()[:2])


def genera(doc: Documento) -> bytes:
    errori = controlla(doc)
    if errori:
        raise FatturaNonValida(" ".join(errori))
    ET.register_namespace("p", NS)
    ET.register_namespace("ds", NS_DS)
    ET.register_namespace("xsi", NS_XSI)
    radice = ET.Element(f"{{{NS}}}FatturaElettronica", {"versione": "FPR12"})
    radice.set("SistemaEmittente", doc.sistema_emittente[:20])
    ced, ces = doc.cedente, doc.cessionario
    codice_dest = ces.codice_destinatario.strip().upper()

    # ---------------------------------------------------------------- header
    header = _sub(radice, "FatturaElettronicaHeader")
    trasm = _sub(header, "DatiTrasmissione")
    idt = _sub(trasm, "IdTrasmittente")
    _sub(idt, "IdPaese", "IT")
    _sub(idt, "IdCodice", id_trasmittente(ced))
    _sub(trasm, "ProgressivoInvio", doc.progressivo_invio)
    _sub(trasm, "FormatoTrasmissione", "FPR12")
    _sub(trasm, "CodiceDestinatario", codice_dest)
    if ced.telefono.strip() or ced.email.strip():
        contatti = _sub(trasm, "ContattiTrasmittente")
        _opz(contatti, "Telefono", ced.telefono[:12])
        _opz(contatti, "Email", ced.email[:256])
    if codice_dest == "0000000" and ces.pec.strip():
        _sub(trasm, "PECDestinatario", ces.pec.strip())

    cp = _sub(header, "CedentePrestatore")
    da = _sub(cp, "DatiAnagrafici")
    iva = _sub(da, "IdFiscaleIVA")
    _sub(iva, "IdPaese", (ced.nazione or "IT").upper()[:2])
    _sub(iva, "IdCodice", solo_codice(ced.partita_iva))
    _opz(da, "CodiceFiscale", solo_codice(ced.codice_fiscale))
    _sub(_sub(da, "Anagrafica"), "Denominazione", ced.denominazione.strip()[:80])
    _sub(da, "RegimeFiscale", ced.regime_fiscale or "RF01")
    _sede(cp, ced)
    if ced.rea_ufficio.strip() and ced.rea_numero.strip():
        rea = _sub(cp, "IscrizioneREA")
        _sub(rea, "Ufficio", ced.rea_ufficio.strip().upper()[:2])
        _sub(rea, "NumeroREA", ced.rea_numero.strip()[:20])
        if ced.capitale_sociale.strip():
            _sub(rea, "CapitaleSociale", importo2(Decimal(ced.capitale_sociale.replace(",", "."))))
        _sub(rea, "StatoLiquidazione", "LN")
    if ced.telefono.strip() or ced.email.strip():
        contatti = _sub(cp, "Contatti")
        _opz(contatti, "Telefono", ced.telefono[:12])
        _opz(contatti, "Email", ced.email[:256])

    cc = _sub(header, "CessionarioCommittente")
    da = _sub(cc, "DatiAnagrafici")
    if ces.partita_iva.strip():
        iva = _sub(da, "IdFiscaleIVA")
        _sub(iva, "IdPaese", (ces.nazione or "IT").upper()[:2])
        _sub(iva, "IdCodice", solo_codice(ces.partita_iva)[:28])
    _opz(da, "CodiceFiscale", solo_codice(ces.codice_fiscale))
    _sub(_sub(da, "Anagrafica"), "Denominazione", ces.denominazione.strip()[:80])
    _sede(cc, ces)

    # ------------------------------------------------------------------ body
    body = _sub(radice, "FatturaElettronicaBody")
    generali = _sub(body, "DatiGenerali")
    dgd = _sub(generali, "DatiGeneraliDocumento")
    _sub(dgd, "TipoDocumento", doc.tipo)
    _sub(dgd, "Divisa", "EUR")
    _sub(dgd, "Data", doc.data.isoformat())
    _sub(dgd, "Numero", doc.numero)
    if bollo_dovuto(doc):
        bollo = _sub(dgd, "DatiBollo")
        _sub(bollo, "BolloVirtuale", "SI")
        _sub(bollo, "ImportoBollo", "2.00")
    _, _, totale = totali(doc)
    _sub(dgd, "ImportoTotaleDocumento", importo2(totale))
    for causale in doc.causali:
        testo = " ".join(causale.split())
        while testo:  # Causale: massimo 200 caratteri per elemento, ripetibile
            _sub(dgd, "Causale", testo[:200])
            testo = testo[200:]
    if doc.fattura_collegata:
        numero, data = doc.fattura_collegata
        collegata = _sub(generali, "DatiFattureCollegate")
        _sub(collegata, "IdDocumento", numero[:20])
        _sub(collegata, "Data", data.isoformat())
    for d in doc.ddt:
        blocco = _sub(generali, "DatiDDT")
        _sub(blocco, "NumeroDDT", d.numero[:20])
        _sub(blocco, "DataDDT", d.data.isoformat())
        for n in d.linee:
            _sub(blocco, "RiferimentoNumeroLinea", str(n))

    beni = _sub(body, "DatiBeniServizi")
    for linea in doc.linee:
        dl = _sub(beni, "DettaglioLinee")
        _sub(dl, "NumeroLinea", str(linea.numero))
        if linea.codice.strip():
            ca = _sub(dl, "CodiceArticolo")
            _sub(ca, "CodiceTipo", "INT")
            _sub(ca, "CodiceValore", linea.codice.strip()[:35])
        _sub(dl, "Descrizione", linea.descrizione.strip()[:1000] or "Articolo")
        _sub(dl, "Quantita", importo8(linea.quantita))
        _opz(dl, "UnitaMisura", linea.unita_misura[:10])
        _sub(dl, "PrezzoUnitario", importo8(linea.prezzo_unitario))
        if linea.sconto_unitario:
            sm = _sub(dl, "ScontoMaggiorazione")
            _sub(sm, "Tipo", "SC")
            _sub(sm, "Importo", importo8(linea.sconto_unitario))
        _sub(dl, "PrezzoTotale", importo8(linea.totale))
        _sub(dl, "AliquotaIVA", importo2(linea.aliquota))
        _opz(dl, "Natura", linea.natura)
    for r in riepiloghi(doc):
        rp = _sub(beni, "DatiRiepilogo")
        _sub(rp, "AliquotaIVA", importo2(r["aliquota"]))
        _opz(rp, "Natura", r["natura"])
        _sub(rp, "ImponibileImporto", importo2(r["imponibile"]))
        _sub(rp, "Imposta", importo2(r["imposta"]))
        if not r["natura"]:
            _sub(rp, "EsigibilitaIVA", doc.esigibilita or "I")
        elif doc.riferimento_normativo.strip():
            _sub(rp, "RiferimentoNormativo", doc.riferimento_normativo.strip()[:100])

    if doc.pagamento and totale > 0:
        p = doc.pagamento
        dp = _sub(body, "DatiPagamento")
        _sub(dp, "CondizioniPagamento", "TP02")
        det = _sub(dp, "DettaglioPagamento")
        _sub(det, "Beneficiario", ced.denominazione.strip()[:200])
        _sub(det, "ModalitaPagamento", p.modalita or "MP05")
        if p.scadenza:
            _sub(det, "DataRiferimentoTerminiPagamento", doc.data.isoformat())
            _sub(det, "GiorniTerminiPagamento", str(max((p.scadenza - doc.data).days, 0)))
            _sub(det, "DataScadenzaPagamento", p.scadenza.isoformat())
        _sub(det, "ImportoPagamento", importo2(totale))
        _opz(det, "IstitutoFinanziario", p.banca[:80])
        _opz(det, "IBAN", "".join(p.iban.split()).upper())

    ET.indent(radice)
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(radice, encoding="utf-8")
