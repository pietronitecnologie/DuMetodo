"""Codici delle specifiche tecniche FatturaPA (versione 1.9) e voci di legge dei DDT (DPR 472/1996)."""

from __future__ import annotations

REGIMI_FISCALI: tuple[tuple[str, str], ...] = (
    ("RF01", "RF01 – Ordinario"),
    ("RF02", "RF02 – Contribuenti minimi"),
    ("RF04", "RF04 – Agricoltura e pesca"),
    ("RF05", "RF05 – Vendita sali e tabacchi"),
    ("RF06", "RF06 – Commercio fiammiferi"),
    ("RF07", "RF07 – Editoria"),
    ("RF08", "RF08 – Gestione servizi telefonia pubblica"),
    ("RF09", "RF09 – Rivendita documenti di trasporto pubblico"),
    ("RF10", "RF10 – Intrattenimenti, giochi"),
    ("RF11", "RF11 – Agenzie viaggi e turismo"),
    ("RF12", "RF12 – Agriturismo"),
    ("RF13", "RF13 – Vendite a domicilio"),
    ("RF14", "RF14 – Rivendita beni usati, oggetti d'arte"),
    ("RF15", "RF15 – Agenzie di vendite all'asta"),
    ("RF16", "RF16 – IVA per cassa P.A."),
    ("RF17", "RF17 – IVA per cassa"),
    ("RF18", "RF18 – Altro"),
    ("RF19", "RF19 – Forfettario"),
)

NATURE_IVA: tuple[tuple[str, str], ...] = (
    ("", "— nessuna (aliquota maggiore di zero)"),
    ("N1", "N1 – Escluse ex art. 15"),
    ("N2.1", "N2.1 – Non soggette, artt. 7-7septies"),
    ("N2.2", "N2.2 – Non soggette, altri casi"),
    ("N3.1", "N3.1 – Non imponibili: esportazioni"),
    ("N3.2", "N3.2 – Non imponibili: cessioni intracomunitarie"),
    ("N3.3", "N3.3 – Non imponibili: verso San Marino"),
    ("N3.4", "N3.4 – Non imponibili: assimilate alle esportazioni"),
    ("N3.5", "N3.5 – Non imponibili: dichiarazione d'intento"),
    ("N3.6", "N3.6 – Non imponibili: altre"),
    ("N4", "N4 – Esenti"),
    ("N5", "N5 – Regime del margine"),
    ("N6.1", "N6.1 – Reverse charge: rottami"),
    ("N6.2", "N6.2 – Reverse charge: oro e argento"),
    ("N6.3", "N6.3 – Reverse charge: subappalto edilizia"),
    ("N6.4", "N6.4 – Reverse charge: cessione fabbricati"),
    ("N6.5", "N6.5 – Reverse charge: telefoni cellulari"),
    ("N6.6", "N6.6 – Reverse charge: prodotti elettronici"),
    ("N6.7", "N6.7 – Reverse charge: comparto edile"),
    ("N6.8", "N6.8 – Reverse charge: settore energetico"),
    ("N6.9", "N6.9 – Reverse charge: altri casi"),
    ("N7", "N7 – IVA assolta in altro stato UE"),
)

ESIGIBILITA_IVA: tuple[tuple[str, str], ...] = (
    ("I", "Immediata"),
    ("D", "Differita"),
    ("S", "Scissione dei pagamenti (split payment)"),
)

MODALITA_PAGAMENTO: tuple[tuple[str, str], ...] = (
    ("MP01", "MP01 – Contanti"),
    ("MP02", "MP02 – Assegno"),
    ("MP05", "MP05 – Bonifico"),
    ("MP08", "MP08 – Carta di pagamento"),
    ("MP09", "MP09 – RID"),
    ("MP12", "MP12 – RIBA"),
    ("MP19", "MP19 – SEPA Direct Debit"),
    ("MP23", "MP23 – PagoPA"),
)

TIPI_DOCUMENTO: tuple[tuple[str, str], ...] = (
    ("TD24", "Fattura differita (da DDT)"),
    ("TD01", "Fattura"),
    ("TD04", "Nota di credito"),
)

CAUSALI_TRASPORTO: tuple[tuple[str, str], ...] = (
    ("Vendita", "Vendita"),
    ("Conto lavorazione", "Conto lavorazione"),
    ("Reso", "Reso"),
    ("Riparazione", "Riparazione"),
    ("Conto visione", "Conto visione"),
    ("Comodato", "Comodato"),
    ("Omaggio", "Omaggio"),
    ("Trasferimento", "Trasferimento"),
)

TRASPORTO_A_CURA: tuple[tuple[str, str], ...] = (
    ("Mittente", "Mittente"),
    ("Destinatario", "Destinatario"),
    ("Vettore", "Vettore"),
)

PORTI: tuple[tuple[str, str], ...] = (("Franco", "Franco"), ("Assegnato", "Assegnato"))
