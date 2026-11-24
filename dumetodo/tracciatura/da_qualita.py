"""Vecchie funzioni (controlli qualità, foto e file, lotto/busta) e dati di Metodo come modelli e tracciature.

Un *test* diventa un **modello di tracciatura** di categoria «Controllo qualità»; ogni *controllo* diventa:

- controllo a campione: due campi interi «<nome> · campione» e «<nome> · KO»; un KO rende non conforme;
- controllo di presenza (es. distinta, disegno): un campo Sì/No «<nome>» che deve essere Sì.

Ogni esecuzione diventa una **tracciatura** collegata a ordine, codice finito e riferimento, con l'esito
calcolato come per le tracciature normali.

Le funzioni ricevono le classi dei modelli (``Modelli``): così le usano sia la migrazione dei dati (modelli
storici di ``apps.get_model``) sia l'importazione da Metodo. Per questo usano solo campi e valori grezzi.
"""

from __future__ import annotations

import datetime as dt
import secrets
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

CATEGORIA_QUALITA = "QUA"
INTERO, DECIMALE, SI_NO = "INT", "DEC", "BOO"
OK, KO = "OK", "KO"
CAMPO_QTA = "Quantità in ordine"
UTENTE_SISTEMA = "dumetodo-sistema"


@dataclass
class Modelli:
    Modello: Any
    Campo: Any
    Tracciatura: Any
    Valore: Any
    User: Any


@dataclass
class Controllo:
    chiave: Any
    nome: str
    presenza: bool = False


@dataclass
class ModelloQualita:
    modello: Any
    campi: dict[Any, dict[str, Any]] = field(default_factory=dict)  # chiave controllo -> {"campione"/"ko"/"presente"}
    qta: Any = None
    prossimo_ordine: int = 0


def utente_sistema(m: Modelli) -> Any:
    """Autore delle tracciature senza operatore (dati storici): utente disattivato, senza password utilizzabile."""
    u = m.User.objects.filter(username=UTENTE_SISTEMA).first()
    if u is None:
        u = m.User.objects.create(
            username=UTENTE_SISTEMA,
            first_name="DuMetodo",
            last_name="(dati importati)",
            is_active=False,
            password="!" + secrets.token_urlsafe(24),  # «!» iniziale = password non utilizzabile
        )
    return u


def _nome_libero(m: Modelli, nome: str) -> str:
    nome = nome.strip()[:70] or "Controllo qualità"
    candidato, n = nome, 2
    while m.Modello.objects.filter(nome=candidato).exists():
        candidato = f"{nome} ({n})"
        n += 1
    return candidato


def _campo(m: Modelli, mq: ModelloQualita, etichetta: str, tipo: str, **extra: Any) -> Any:
    mq.prossimo_ordine += 1
    return m.Campo.objects.create(
        modello=mq.modello, etichetta=etichetta[:80], tipo=tipo, ordine=mq.prossimo_ordine, **extra
    )


def aggiungi_controllo(m: Modelli, mq: ModelloQualita, c: Controllo) -> None:
    if c.chiave in mq.campi:
        return
    if c.presenza:
        mq.campi[c.chiave] = {"presente": _campo(m, mq, c.nome, SI_NO, atteso_si=True)}
    else:
        mq.campi[c.chiave] = {
            "campione": _campo(m, mq, f"{c.nome} · campione", INTERO, minimo=Decimal(0)),
            "ko": _campo(m, mq, f"{c.nome} · KO", INTERO, minimo=Decimal(0), massimo=Decimal(0)),
        }


def crea_modello(
    m: Modelli, nome: str, controlli: list[Controllo], descrizione: str = "", attivo: bool = True
) -> ModelloQualita:
    modello = m.Modello.objects.create(
        nome=_nome_libero(m, nome),
        categoria=CATEGORIA_QUALITA,
        descrizione=descrizione[:2000],
        uso_ordine="F",
        uso_articolo="F",
        uso_riferimento="F",
        attivo=attivo,
    )
    mq = ModelloQualita(modello)
    for c in controlli:
        aggiungi_controllo(m, mq, c)
    mq.qta = _campo(m, mq, CAMPO_QTA, DECIMALE)
    return mq


def registra(
    m: Modelli,
    mq: ModelloQualita,
    esiti: dict[Any, tuple[int, int] | bool],
    *,
    operatore: Any,
    momento: dt.datetime,
    ordine: Any = None,
    articolo: str = "",
    riferimento: str = "",
    qta: Decimal | None = None,
    note: str = "",
) -> Any:
    """Una tracciatura con gli esiti: (campione, KO) per i controlli a campione, True/False per la presenza."""
    t = m.Tracciatura.objects.create(
        modello=mq.modello,
        momento=momento,
        operatore=operatore,
        ordine=ordine,
        articolo=(articolo or "")[:80],
        riferimento=(riferimento or "")[:120],
        note=note,
    )
    valori, conformi = [], []
    for chiave, esito in esiti.items():
        campi = mq.campi[chiave]
        if isinstance(esito, bool):
            valori.append(m.Valore(tracciatura=t, campo=campi["presente"], booleano=esito, conforme=esito))
            conformi.append(esito)
        else:
            campione, ko = esito
            valori.append(m.Valore(tracciatura=t, campo=campi["campione"], intero=campione, conforme=True))
            valori.append(m.Valore(tracciatura=t, campo=campi["ko"], intero=ko, conforme=ko == 0))
            conformi.append(ko == 0)
    if qta is not None:
        valori.append(m.Valore(tracciatura=t, campo=mq.qta, decimale=qta))
    m.Valore.objects.bulk_create(valori)
    t.esito = KO if False in conformi else OK if conformi else ""
    t.save(update_fields=["esito"])
    return t


# ------------------------------------------------------------------------------------------- foto e file
NOME_MODELLO_FILE = "Foto e file dell'ordine"
NOME_MODELLO_LOTTO = "Lotto e busta"
DOCUMENTO = "DOC"


def modello_file(m: Modelli) -> tuple[Any, Any]:
    """Modello «Foto e file dell'ordine» (ordine obbligatorio, un campo documento): (modello, campo)."""
    modello = m.Modello.objects.filter(nome=NOME_MODELLO_FILE).first()
    if modello is None:
        modello = m.Modello.objects.create(
            nome=NOME_MODELLO_FILE,
            categoria=DOCUMENTO,
            descrizione="Foto o documento da allegare all'ordine.",
            uso_ordine="O",
            uso_articolo="",
        )
        m.Campo.objects.create(modello=modello, etichetta="Foto o documento", tipo=DOCUMENTO, obbligatorio=True)
    return modello, m.Campo.objects.filter(modello=modello, tipo=DOCUMENTO).order_by("ordine", "id").first()


def registra_file(
    m: Modelli,
    modello: Any,
    campo: Any,
    *,
    ordine: Any,
    percorso: str,
    nome: str,
    tipo: str,
    momento: Any,
    operatore: Any,
) -> Any:
    """Tracciatura con un file già presente nell'archivio dei file (non viene copiato né spostato)."""
    t = m.Tracciatura.objects.create(modello=modello, momento=momento, operatore=operatore, ordine=ordine)
    m.Valore.objects.create(
        tracciatura=t, campo=campo, file=percorso, nome_file=(nome or "")[:255], tipo_file=(tipo or "")[:100]
    )
    return t


def modello_lotto(m: Modelli) -> Any:
    """Modello «Lotto e busta» (prima: riquadro Tracciabilità della pagina dell'ordine)."""
    modello = m.Modello.objects.filter(nome=NOME_MODELLO_LOTTO).first()
    if modello is None:
        modello = m.Modello.objects.create(
            nome=NOME_MODELLO_LOTTO,
            categoria="LOT",
            descrizione="Lotto di produzione e busta dell'ordine.",
            uso_ordine="O",
            uso_articolo="",
        )
        for i, etichetta in enumerate(("Lotto produzione", "Busta"), start=1):
            m.Campo.objects.create(modello=modello, etichetta=etichetta, tipo="TXT", ordine=i, obbligatorio=i == 1)
    return modello
