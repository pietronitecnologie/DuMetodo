"""Report Excel di pianificazione: stesso impianto di Metodo (foglio Pianificazione + Commesse bloccate)."""

from __future__ import annotations

import io
from decimal import Decimal

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .piano import Piano, raggruppa_per_report

BLU, AZZURRO, AMBRA = "0A5796", "BDD7EE", "D4A017"
COLORI = {
    "ok": ("D1E7DD", "0F5132"),
    "attenzione": ("FFF3CD", "664D03"),
    "critico": ("F8D7DA", "842029"),
    "nd": ("E9ECEF", "6C757D"),
}
STATO = {"ok": "OK", "attenzione": "ATTENZIONE", "critico": "CRITICO", "nd": "N/D"}
BORDO = Border(*(Side(style="thin", color="BFBFBF"),) * 4)


def _fill(colore: str) -> PatternFill:
    return PatternFill("solid", start_color=colore, end_color=colore)


def _n(v: Decimal | None) -> float | None:
    return None if v is None else float(v)


def genera(piano: Piano, bloccate: list[dict[str, object]]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Pianificazione"
    n = len(piano.settimane)
    ultima = 3 + n * 4 + 3
    lettera = get_column_letter(ultima)
    ws.merge_cells(f"A1:{lettera}1")
    ws["A1"] = "PIANIFICAZIONE PRODUZIONE"
    ws["A1"].font = Font(bold=True, size=16, color="FFFFFF")
    ws["A1"].fill = _fill(BLU)
    ws.row_dimensions[1].height = 32
    ws.merge_cells(f"A2:{lettera}2")
    ws["A2"] = (
        f"Generato il {piano.generato_il:%d/%m/%Y %H:%M} | Periodo: {piano.settimane[0][0]:%d/%m/%Y} → "
        f"{piano.settimane[-1][1]:%d/%m/%Y}"
    )
    ws["A2"].fill = _fill(AZZURRO)
    ws.merge_cells(f"A3:{lettera}3")
    ws["A3"] = (
        f"VERDE = OK (≤ {piano.soglia_attenzione}% capacità)   GIALLO = Attenzione   ROSSO = Critico (> 100%)"
        f" | Arretrato = ore scadute non ancora evase (dal {piano.inizio_arretrato:%d/%m/%Y})"
    )
    ws["A3"].font = Font(italic=True, size=9)

    col = 4
    for etichetta in piano.etichette:
        ws.merge_cells(start_row=4, start_column=col, end_row=4, end_column=col + 3)
        ws.cell(4, col, etichetta).alignment = Alignment(horizontal="center")
        ws.cell(4, col).font = Font(bold=True)
        col += 4
    ws.merge_cells(start_row=4, start_column=col, end_row=4, end_column=col + 2)
    ws.cell(4, col, f"Riepilogo totale ({n} settimane)").font = Font(bold=True)

    intestazioni = [("Area di produzione", None), ("Arretrato (ore)", AMBRA), ("N° ord. arretrati", AMBRA)]
    for _ in piano.settimane:
        intestazioni += [
            ("Ore pianificate (h)", None),
            ("N° ordini", None),
            ("Capacità disp. (h)", None),
            ("Saturaz. (%)", None),
        ]
    intestazioni += [(f"Totale {n} sett. (h)", None), ("Capacità tot. (h)", None), ("Stato complessivo", None)]
    spiegazioni = {
        1: "Ore = Σ(quantità × prezzo) / resa oraria, per righe con scadenza passata non ancora in bolla.",
        2: "Ordini distinti con righe in arretrato.",
    }
    for i, (testo, colore) in enumerate(intestazioni, start=1):
        c = ws.cell(5, i, testo)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = _fill(colore or BLU)
        c.alignment = Alignment(wrap_text=True, horizontal="center", vertical="center")
        c.border = BORDO
        if i - 1 in spiegazioni:
            c.comment = Comment(spiegazioni[i - 1], "DuMetodo", width=220, height=90)
    ws.row_dimensions[5].height = 42

    riga = 6
    for rp in raggruppa_per_report(piano):
        valori: list[object] = [rp.nome, _n(rp.arretrato.ore), rp.arretrato.n_ordini]
        for s in rp.settimane:
            valori += [_n(s.ore), s.n_ordini, _n(s.capacita), _n(s.percentuale)]
        tot = rp.totale
        livello = tot.livello(piano.soglia_attenzione)
        valori += [_n(rp.ore_settimane), _n(tot.capacita), STATO[livello]]
        for i, v in enumerate(valori, start=1):
            c = ws.cell(riga, i, v)
            c.border = BORDO
        if rp.arretrato.ore > 0:
            for i in (2, 3):
                ws.cell(riga, i).fill = _fill("FFF8E1")
                ws.cell(riga, i).font = Font(bold=True, color="7F6000")
        for k, s in enumerate(rp.settimane):
            sfondo, testo = COLORI[s.livello(piano.soglia_attenzione)]
            for i in range(4 + k * 4, 8 + k * 4):
                ws.cell(riga, i).fill = _fill(sfondo)
                ws.cell(riga, i).font = Font(color=testo, italic=i == 6 + k * 4)
            ws.cell(riga, 7 + k * 4).number_format = '0.0"%"'
        sfondo, testo = COLORI[livello]
        stato = ws.cell(riga, ultima)
        stato.fill = _fill({"critico": "C00000", "attenzione": "FFC000", "ok": "00B050"}.get(livello, "E9ECEF"))
        stato.font = Font(bold=True, color="FFFFFF" if livello in ("critico", "ok") else "000000")
        ws.cell(riga, ultima - 2).fill = _fill(sfondo)
        for i in range(2, ultima):
            if ws.cell(riga, i).number_format == "General" and isinstance(ws.cell(riga, i).value, float):
                ws.cell(riga, i).number_format = "0.00"
        riga += 1
    larghezze = [22, 12, 10] + [13, 9, 12, 10] * n + [13, 13, 12]
    for i, w in enumerate(larghezze, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "B6"

    wb2 = wb.create_sheet("Commesse bloccate")
    wb2.merge_cells("A1:E1")
    wb2["A1"] = "COMMESSE BLOCCATE"
    wb2["A1"].font = Font(bold=True, size=14, color="FFFFFF")
    wb2["A1"].fill = _fill(BLU)
    wb2["A2"] = f"Generato il {piano.generato_il:%d/%m/%Y %H:%M} | Ordini aperti con note che segnalano blocchi"
    for i, t in enumerate(["Commessa", "Stato", "Data scadenza", "N° oggetti", "Note bloccanti"], start=1):
        c = wb2.cell(4, i, t)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = _fill(BLU)
    if not bloccate:
        wb2.merge_cells("A5:E5")
        wb2["A5"] = "Nessuna commessa bloccata."
    for r, b in enumerate(bloccate, start=5):
        for i, chiave in enumerate(["commessa", "stato", "scadenza", "pezzi", "note"], start=1):
            v = b[chiave]
            wb2.cell(r, i, float(v) if isinstance(v, Decimal) else v)
        wb2.cell(r, 3).number_format = "DD/MM/YYYY"
        wb2.cell(r, 5).alignment = Alignment(wrap_text=True)
    for lettera, w in zip("ABCDE", (24, 22, 14, 12, 90), strict=True):
        wb2.column_dimensions[lettera].width = w
    wb2.freeze_panes = "A5"

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
