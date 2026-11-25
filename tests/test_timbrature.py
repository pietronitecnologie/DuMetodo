"""Timbrature: calcolo dei turni, codici personali, postazioni, correzioni, presenze e costi per reparto."""

import datetime as dt
from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.core.cache import cache
from django.utils import timezone

from metodo.core.models import Reparto
from metodo.core.permessi import applica_profilo
from metodo.ordini.servizi import OperazioneNonConsentita
from metodo.timbrature import calcolo, servizi
from metodo.timbrature.models import Lavoratore, Timbratura, TipoTimbratura

T = TipoTimbratura
G = dt.date(2026, 3, 10)


def _ora(h: int, m: int = 0, giorno: dt.date = G) -> dt.datetime:
    return dt.datetime.combine(giorno, dt.time(h, m), tzinfo=dt.UTC)


def _e(h, m, tipo, rep=None, giorno=G):
    return calcolo.Evento(_ora(h, m, giorno), tipo, rep)


# --------------------------------------------------------------------------------------------- calcolo
def test_giornata_con_reparti_e_mensa():
    eventi = [
        _e(8, 0, "ENT"),
        _e(8, 5, "IRP", 1),
        _e(12, 0, "IME"),
        _e(12, 30, "FME"),  # alla fine della mensa si torna al reparto 1
        _e(14, 0, "IRP", 2),
        _e(17, 0, "USC"),
    ]
    [t], stato = calcolo.calcola(eventi, _ora(20))
    assert t.per_reparto == {1: (3 * 60 + 55 + 90) * 60, 2: 3 * 3600}
    assert (t.non_assegnato, t.mensa, t.pausa) == (5 * 60, 30 * 60, 0)
    assert t.lavorato == 8.5 * 3600 and t.presenza == 9 * 3600 and not t.anomalie
    assert not stato.presente


def test_turno_in_corso_e_di_notte():
    eventi = [_e(22, 0, "ENT"), _e(22, 10, "IRP", 1)]
    adesso = _ora(2, 0, G + dt.timedelta(days=1))
    [t], stato = calcolo.calcola(eventi, adesso)
    assert t.in_corso and t.giorno == G and t.per_reparto[1] == (3 * 60 + 50) * 60
    assert stato.presente and stato.reparto_id == 1


def test_anomalie():
    # uscita dimenticata: il turno si chiude all'ultima timbratura
    [t], _ = calcolo.calcola([_e(8, 0, "ENT"), _e(9, 0, "IRP", 1)], _ora(9, 0, G + dt.timedelta(days=2)))
    assert t.anomalie == ["uscita mancante"] and t.lavorato == 3600
    # il giorno dopo si rientra: due turni distinti
    eventi = [
        _e(8, 0, "ENT"),
        _e(8, 0, "ENT", giorno=G + dt.timedelta(days=1)),
        _e(16, 0, "USC", giorno=G + dt.timedelta(days=1)),
    ]
    t1, t2 = calcolo.calcola(eventi, _ora(20, 0, G + dt.timedelta(days=1)))[0]
    assert "uscita mancante" in t1.anomalie and t2.lavorato == 8 * 3600
    [t], _ = calcolo.calcola([_e(8, 0, "ENT"), _e(9, 0, "ENT"), _e(10, 0, "USC")], _ora(12))
    assert t.anomalie == ["entrata doppia alle 09:00"] and t.lavorato == 2 * 3600
    [t], _ = calcolo.calcola([_e(8, 0, "IRP", 1), _e(10, 0, "USC")], _ora(12))
    assert t.anomalie == ["entrata mancante"] and t.per_reparto[1] == 2 * 3600


# ---------------------------------------------------------------------------------------------- fixture
@pytest.fixture
def capo(db):
    u = User.objects.create_user("capo-tim")
    applica_profilo(u, "Amministratore")
    return User.objects.get(pk=u.pk)


@pytest.fixture
def impianto(db, capo):
    cache.clear()
    pulsanti = Reparto.objects.create(codice="PLS", nome="Pulsanti")
    cablaggi = Reparto.objects.create(codice="CAB", nome="Cablaggi")
    ingresso = servizi.salva_postazione(capo, None, "Ingresso", None, True)
    post_pls = servizi.salva_postazione(capo, None, "Banco pulsanti", pulsanti.pk, True)
    mario = servizi.salva_lavoratore(
        capo, None, {"nome": "Mario", "cognome": "Rossi", "matricola": "M1", "costo_orario": "20"}
    )
    servizi.imposta_codice(capo, mario.pk, "1234")
    return {"pulsanti": pulsanti, "cablaggi": cablaggi, "ingresso": ingresso, "pls": post_pls, "mario": mario}


# ----------------------------------------------------------------------------------------- codici
def test_codici_personali(impianto, capo):
    mario = impianto["mario"]
    mario.refresh_from_db()
    assert mario.codice_impronta and "1234" not in mario.codice_impronta  # mai in chiaro
    assert servizi.lavoratore_da_codice("1234") == mario
    nuovo = servizi.genera_codice(capo, mario.pk)
    assert len(nuovo) == 6 and nuovo.isdigit()
    assert servizi.lavoratore_da_codice("1234") is None and servizi.lavoratore_da_codice(nuovo) == mario
    luigi = servizi.salva_lavoratore(capo, None, {"nome": "Luigi", "cognome": "Verdi", "matricola": "M2"})
    with pytest.raises(OperazioneNonConsentita, match="già di un altro"):
        servizi.imposta_codice(capo, luigi.pk, nuovo)
    with pytest.raises(OperazioneNonConsentita, match="da 4 a 10 cifre"):
        servizi.imposta_codice(capo, luigi.pk, "12a")
    with pytest.raises(OperazioneNonConsentita, match="matricola m1 è già"):
        servizi.salva_lavoratore(capo, None, {"nome": "X", "cognome": "Y", "matricola": "m1"})
    luigi.attivo = False
    luigi.save()
    servizi.imposta_codice(capo, luigi.pk, "5555")
    assert servizi.lavoratore_da_codice("5555") is None  # non attivo: non timbra


# ------------------------------------------------------------------------------------------ postazioni
def test_regole_alle_postazioni(impianto):
    ing, pls = impianto["ingresso"], impianto["pls"]
    r = servizi.identifica(pls, "1234")
    # prima timbratura in reparto senza entrata: si accetta, con l'entrata aggiunta in automatico
    assert [a["tipo"] for a in r["azioni"]] == ["IRP"] and r["azioni"][0]["etichetta"].startswith("Entrata e inizio")
    pul, cab = impianto["pulsanti"], impianto["cablaggi"]
    assert [a["tipo"] for a in servizi.identifica(ing, "1234")["azioni"]] == [
        "ENT",
        *sorted(
            [f"IRP:{cab.pk}", f"IRP:{pul.pk}"], key=lambda x: {f"IRP:{pul.pk}": pul.nome, f"IRP:{cab.pk}": cab.nome}[x]
        ),
    ]
    adesso = timezone.now() - dt.timedelta(hours=3)
    servizi.timbra(ing, "1234", "ENT", adesso)
    with pytest.raises(OperazioneNonConsentita, match="non è possibile"):
        servizi.timbra(ing, "1234", "ENT", adesso + dt.timedelta(minutes=1))
    r = servizi.identifica(pls, "1234")
    assert [a["tipo"] for a in r["azioni"]] == ["IRP", "IME", "IPA"] and r["azioni"][0]["etichetta"].endswith(
        "Pulsanti"
    )
    servizi.timbra(pls, "1234", "IRP", adesso + dt.timedelta(minutes=5))
    assert [a["tipo"] for a in servizi.identifica(pls, "1234")["azioni"]] == ["FRP", "IME", "IPA"]
    assert servizi.identifica(pls, "1234")["stato"] == "Al lavoro in Pulsanti"
    servizi.timbra(pls, "1234", "IME", adesso + dt.timedelta(hours=1))
    assert [a["tipo"] for a in servizi.identifica(ing, "1234")["azioni"]] == ["FME"]
    servizi.timbra(ing, "1234", "FME", adesso + dt.timedelta(hours=1, minutes=30))
    assert servizi.stato(impianto["mario"]).reparto_id == impianto["pulsanti"].pk  # riprende il reparto
    servizi.timbra(ing, "1234", "USC", adesso + dt.timedelta(hours=2))
    t = Timbratura.objects.get(tipo="IRP")
    assert t.reparto == impianto["pulsanti"] and t.postazione == pls


def test_doppio_tocco_e_blocco_per_codici_sbagliati(impianto):
    ing = impianto["ingresso"]
    servizi.timbra(ing, "1234", "ENT")
    for _ in range(servizi.TENTATIVI_MASSIMI):
        with pytest.raises(OperazioneNonConsentita, match="non riconosciuto"):
            servizi.identifica(ing, "9999")
    with pytest.raises(OperazioneNonConsentita, match="Troppi codici"):
        servizi.identifica(ing, "1234")  # anche quello giusto, finché dura il blocco
    cache.clear()
    with pytest.raises(OperazioneNonConsentita, match="non è possibile"):
        servizi.timbra(ing, "1234", "ENT")


def test_senza_postazione_di_ingresso_si_entra_dal_reparto(impianto, capo):
    servizi.salva_postazione(capo, impianto["ingresso"].pk, "Ingresso", None, False)  # disattivata
    pls = impianto["pls"]
    assert [a["tipo"] for a in servizi.identifica(pls, "1234")["azioni"]] == ["IRP", "ENT"]
    servizi.timbra(pls, "1234", "IRP")
    assert list(Timbratura.objects.values_list("tipo", flat=True)) == ["ENT", "IRP"]  # entrata automatica
    assert "USC" in [a["tipo"] for a in servizi.identifica(pls, "1234")["azioni"]]


def test_correzioni_manuali(impianto, capo, operatore):
    mario = impianto["mario"]
    ieri = timezone.localdate() - dt.timedelta(days=1)
    inizio = timezone.make_aware(dt.datetime.combine(ieri, dt.time(8)))
    servizi.timbratura_manuale(capo, mario.pk, "ENT", inizio, None, "badge dimenticato")
    servizi.timbratura_manuale(capo, mario.pk, "IRP", inizio, impianto["cablaggi"].pk, "badge dimenticato")
    sbagliata = servizi.timbratura_manuale(capo, mario.pk, "USC", inizio + dt.timedelta(hours=2), None, "errore")
    servizi.annulla(capo, sbagliata.pk, "orario sbagliato")
    servizi.timbratura_manuale(capo, mario.pk, "USC", inizio + dt.timedelta(hours=8), None, "uscita dimenticata")
    [riga], reparti = servizi.presenze(ieri, ieri)
    assert riga.lavorato == Decimal("8.00") and riga.per_reparto == {impianto["cablaggi"].pk: Decimal("8.00")}
    assert reparti == [impianto["cablaggi"]] and not riga.anomalie
    with pytest.raises(OperazioneNonConsentita, match="motivo"):
        servizi.timbratura_manuale(capo, mario.pk, "ENT", inizio, None, " ")
    with pytest.raises(OperazioneNonConsentita, match="reparto"):
        servizi.timbratura_manuale(capo, mario.pk, "IRP", inizio, None, "x")
    with pytest.raises(OperazioneNonConsentita, match="permessi"):
        servizi.timbratura_manuale(operatore, mario.pk, "ENT", inizio, None, "x")


def test_costi_per_reparto(impianto, capo):
    from metodo.anagrafica.models import Articolo
    from metodo.configurazione import servizi as config
    from metodo.ordini.models import Ordine, RigaOrdine

    config.scrivi("produzione.prezzo_resa_oraria", "10")
    config.invalida_cache()
    mario, ieri = impianto["mario"], timezone.localdate() - dt.timedelta(days=1)
    inizio = timezone.make_aware(dt.datetime.combine(ieri, dt.time(8)))
    for tipo, ore, rep in (("ENT", 0, None), ("IRP", 0, impianto["pulsanti"].pk), ("USC", 2, None)):
        servizi.timbratura_manuale(capo, mario.pk, tipo, inizio + dt.timedelta(hours=ore), rep, "prova")
    Articolo.objects.create(codice="P1", reparto=impianto["pulsanti"])
    o = Ordine.objects.create(serie="00040", numero="9", anno=2026)
    RigaOrdine.objects.create(
        ordine=o,
        n_riga="1",
        articolo="P1",
        qta=Decimal(3),
        prezzo_unitario=Decimal(5),
        bolla_emessa=True,
        data_consegna=ieri,
    )
    dati = servizi.costi_per_reparto(ieri, ieri)
    [r] = dati["righe"]
    assert (r["reparto"], r["ore"], r["costo"], r["teoriche"]) == (
        "Pulsanti",
        Decimal("2.00"),
        Decimal("40.00"),
        Decimal("1.50"),
    )
    assert r["efficienza"] == Decimal("75") and r["costo_ora_teorica"] == Decimal("26.67")
    assert not dati["senza_costo"]


# --------------------------------------------------------------------------------------------- pagine
def test_pagina_della_postazione_senza_login(client, impianto):
    ing = impianto["ingresso"]
    base = f"/timbrature/p/{ing.token}/"
    html = client.get(base).content.decode()
    assert "Ingresso e uscita" in html and "Digita il tuo codice" in html
    assert client.get("/timbrature/p/sbagliato/").status_code == 404
    r = client.post(base + "identifica/", {"codice": "1234"}).json()
    assert r["ok"] and r["nome"] == "Mario Rossi" and r["azioni"][0]["tipo"] == "ENT"
    r = client.post(base + "timbra/", {"codice": "1234", "tipo": "ENT"}).json()
    assert r["ok"] and "Mario" in r["messaggio"] and "entrata alle" in r["messaggio"]
    assert client.post(base + "identifica/", {"codice": "0000"}).json() == {
        "ok": False,
        "errore": "Codice non riconosciuto.",
    }
    servizi.rigenera_link(User.objects.get(username="capo-tim"), ing.pk)
    assert client.get(base).status_code == 404  # il vecchio link non funziona più


def test_pagine_di_gestione_e_permessi(client, impianto, capo, caporeparto, operatore):
    client.force_login(operatore)
    assert client.get("/timbrature/").status_code == 403
    client.force_login(caporeparto)
    assert client.get("/timbrature/").status_code == 200
    assert client.get("/timbrature/postazioni/").status_code == 403
    assert client.get("/timbrature/costi/").status_code == 403
    assert "Costo orario" not in client.get("/timbrature/lavoratori/").content.decode()
    client.force_login(capo)
    for url in (
        "/timbrature/",
        "/timbrature/elenco/",
        "/timbrature/lavoratori/",
        "/timbrature/postazioni/",
        "/timbrature/costi/",
        f"/timbrature/lavoratori/{impianto['mario'].pk}/",
        "/timbrature/lavoratori/nuovo/",
    ):
        assert client.get(url).status_code == 200, url
    assert impianto["ingresso"].token in client.get("/timbrature/postazioni/").content.decode()
    r = client.post(
        "/timbrature/lavoratori/nuovo/", {"nome": "Anna", "cognome": "Bianchi", "matricola": "M9", "attivo": "on"}
    )
    assert "Codice personale di Anna" in r.content.decode() and r["Cache-Control"] == "no-store"
    assert Lavoratore.objects.get(matricola="M9").codice_impronta
    for url in ("/timbrature/?formato=xlsx", "/timbrature/costi/?formato=xlsx"):
        assert client.get(url)["Content-Type"].startswith("application/vnd.openxml"), url


# ------------------------------------------------------------------------------- badge, tessere e lettori
def test_badge_e_tessere(impianto, capo):
    from metodo.timbrature.models import Identificativo

    mario = impianto["mario"]
    nfc = servizi.aggiungi_identificativo(capo, mario.pk, "NFC", " 04:a2:b3 c1d2 ", "tessera blu")
    assert nfc.valore == "04A2B3C1D2"
    assert servizi.lavoratore_da_codice("04a2b3c1d2") == mario  # come lo scrive un altro lettore
    nfc.refresh_from_db()
    assert nfc.ultimo_uso is not None
    badge = servizi.genera_badge(capo, mario.pk)
    assert badge.valore.startswith("DM") and len(badge.valore) == 10 and badge.tipo == "BDG"
    assert servizi.lavoratore_da_codice(badge.valore.lower()) == mario
    luigi = servizi.salva_lavoratore(capo, None, {"nome": "Luigi", "cognome": "Verdi", "matricola": "M2"})
    with pytest.raises(OperazioneNonConsentita, match="già di Rossi Mario"):
        servizi.aggiungi_identificativo(capo, luigi.pk, "NFC", "04A2B3C1D2")
    with pytest.raises(OperazioneNonConsentita, match="codice personale"):
        servizi.aggiungi_identificativo(capo, luigi.pk, "BDG", "1234")  # è il codice di Mario
    servizi.aggiungi_identificativo(capo, luigi.pk, "BDG", "55556666")
    with pytest.raises(OperazioneNonConsentita, match="già un badge"):
        servizi.imposta_codice(capo, mario.pk, "55556666")
    with pytest.raises(OperazioneNonConsentita, match="da 4 a 64"):
        servizi.aggiungi_identificativo(capo, luigi.pk, "NFC", "ab")
    servizi.disattiva_identificativo(capo, nfc.pk)
    assert servizi.lavoratore_da_codice("04A2B3C1D2") is None
    assert Identificativo.objects.filter(lavoratore=mario).count() == 2


def test_timbrare_con_il_badge(impianto, capo):
    badge = servizi.genera_badge(capo, impianto["mario"].pk)
    r = servizi.identifica(impianto["ingresso"], badge.valore)
    assert r["nome"] == "Mario Rossi" and r["azioni"][0]["tipo"] == "ENT"
    t = servizi.timbra(impianto["ingresso"], badge.valore, "ENT")
    assert t.lavoratore == impianto["mario"]


def test_metodi_della_postazione(client, impianto, capo):
    p = impianto["ingresso"]
    with pytest.raises(OperazioneNonConsentita, match="almeno un modo"):
        servizi.salva_postazione(capo, p.pk, "Ingresso", None, True, {})
    servizi.salva_postazione(capo, p.pk, "Ingresso", None, True, {"lettori": True, "fotocamera": True})
    html = client.get(f"/timbrature/p/{p.token}/").content.decode()
    assert 'class="ok" data-ok' not in html and "Passa il badge o la tessera sul lettore" in html
    assert "jsQR.js" in html and "Inquadra il QR" in html
    servizi.salva_postazione(capo, p.pk, "Ingresso", None, True, {"tastierino": True})
    html = client.get(f"/timbrature/p/{p.token}/").content.decode()
    assert 'class="ok" data-ok' in html and "jsQR" not in html and "Digita il tuo codice</div>" in html
    client.force_login(capo)
    client.post(
        "/timbrature/postazioni/",
        {"postazione": p.pk, "nome": "Ingresso", "reparto": "", "attiva": "on", "lettori": "on"},
    )
    p.refresh_from_db()
    assert (p.usa_tastierino, p.usa_lettori, p.usa_fotocamera) == (False, True, False)


def test_pagine_badge(client, impianto, capo, caporeparto):
    mario = impianto["mario"]
    client.force_login(capo)
    client.post(f"/timbrature/lavoratori/{mario.pk}/", {"azione": "genera_badge"})
    client.post(f"/timbrature/lavoratori/{mario.pk}/", {"azione": "associa", "tipo": "NFC", "valore": "AABBCCDD"})
    badge = mario.identificativi.get(tipo="BDG")
    html = client.get(f"/timbrature/lavoratori/{mario.pk}/").content.decode()
    assert badge.valore in html and "AABBCCDD" in html
    stampa = client.get(f"/timbrature/badge/{badge.pk}/").content.decode()
    assert badge.valore in stampa and "Matricola M1" in stampa and "qrcode-generator.js" in stampa
    assert client.get(f"/timbrature/badge/{mario.identificativi.get(tipo='NFC').pk}/").status_code == 404
    client.force_login(caporeparto)
    assert client.get(f"/timbrature/badge/{badge.pk}/").status_code == 403


# ------------------------------------------------------------------ PC associato alla postazione
def test_pc_associato_usa_metodo_e_timbra_dal_menu(client, impianto, operatore):
    ing = impianto["ingresso"]
    client.force_login(operatore)  # nessun permesso sulle timbrature
    assert "/timbrature/timbra/" not in client.get("/ordini/").content.decode()
    r = client.get("/timbrature/timbra/")
    assert r.status_code == 302 and r["Location"] == "/"  # PC non associato

    html = client.get(f"/timbrature/p/{ing.token}/").content.decode()  # aprire il link associa il PC
    assert "Apri Metodo v2" in html
    pagina = client.get("/ordini/").content.decode()
    # unica voce di Timbrature per chi non ha altri permessi: il menu «Timbrature» porta diritto alla timbratura
    assert 'href="/timbrature/timbra/"' in pagina
    r = client.get("/timbrature/timbra/")
    assert r.status_code == 302 and r["Location"] == f"/timbrature/p/{ing.token}/"

    servizi.rigenera_link(User.objects.get(username="capo-tim"), ing.pk)  # link nuovo: PC da riassociare
    assert "/timbrature/timbra/" not in client.get("/ordini/").content.decode()


def test_associa_e_scollega_dalla_pagina_postazioni(client, impianto, capo):
    rep = impianto["pls"]
    client.force_login(capo)
    assert "non è una postazione" in client.get("/timbrature/postazioni/").content.decode()
    client.post("/timbrature/postazioni/", {"azione": "associa", "postazione": rep.pk})
    pagina = client.get("/timbrature/postazioni/").content.decode()
    assert f"è la postazione «{rep.nome}»" in pagina and f"Timbra · {rep.nome}" in pagina
    client.post("/timbrature/postazioni/", {"azione": "scollega"})
    assert "Timbra ·" not in client.get("/timbrature/postazioni/").content.decode()


def test_dall_ingresso_uscita_mensa_e_qualsiasi_reparto(impianto):
    ing, pul, cab = impianto["ingresso"], impianto["pulsanti"], impianto["cablaggi"]
    adesso = timezone.now() - dt.timedelta(hours=4)
    # Si entra scegliendo subito il reparto: entrata automatica + inizio lavoro
    r = servizi.identifica(ing, "1234")
    etichette = {a["tipo"]: a["etichetta"] for a in r["azioni"]}
    assert etichette[f"IRP:{pul.pk}"] == f"Entrata e inizio lavoro – {pul.nome}"
    servizi.timbra(ing, "1234", f"IRP:{pul.pk}", adesso)
    assert list(Timbratura.objects.order_by("id").values_list("tipo", "reparto")) == [("ENT", None), ("IRP", pul.pk)]
    assert servizi.stato(impianto["mario"]).reparto_id == pul.pk
    # Presente in Pulsanti: dall'ingresso uscita, mensa, pausa, fine lavoro e gli altri reparti
    tipi = [a["tipo"] for a in servizi.identifica(ing, "1234")["azioni"]]
    assert tipi == ["USC", "IME", "IPA", f"FRP:{pul.pk}", f"IRP:{cab.pk}"]
    servizi.timbra(ing, "1234", f"IRP:{cab.pk}", adesso + dt.timedelta(hours=1))  # cambio reparto
    assert servizi.stato(impianto["mario"]).reparto_id == cab.pk
    servizi.timbra(ing, "1234", "IME", adesso + dt.timedelta(hours=2))
    servizi.timbra(ing, "1234", "FME", adesso + dt.timedelta(hours=2, minutes=30))
    servizi.timbra(ing, "1234", "USC", adesso + dt.timedelta(hours=3))
    [turno] = servizi.turni(timezone.localdate(adesso), timezone.localdate())[impianto["mario"].pk]
    assert round(turno.per_reparto[pul.pk]) == 3600 and round(turno.per_reparto[cab.pk]) == 5400  # mensa esclusa
    with pytest.raises(OperazioneNonConsentita):
        servizi.timbra(ing, "1234", "IRP:999999", adesso + dt.timedelta(hours=5))  # reparto inesistente


def test_messaggio_della_postazione_col_reparto_scelto(client, impianto):
    ing, pul = impianto["ingresso"], impianto["pulsanti"]
    r = client.post(f"/timbrature/p/{ing.token}/timbra/", {"codice": "1234", "tipo": f"IRP:{pul.pk}"}).json()
    assert r["ok"] and f"inizio lavoro – {pul.nome}" in r["messaggio"]


def test_prima_timbratura_in_reparto_aggiunge_l_entrata(impianto):
    pls, ing = impianto["pls"], impianto["ingresso"]
    assert ing.attiva  # c'è una postazione di ingresso, ma il lavoratore va diretto in reparto
    adesso = timezone.now() - dt.timedelta(hours=2)
    t = servizi.timbra(pls, "1234", "IRP", adesso)
    entrata = Timbratura.objects.get(tipo="ENT")
    assert (entrata.momento, entrata.postazione, entrata.nota) == (adesso, pls, "entrata automatica")
    assert t.reparto == impianto["pulsanti"]
    s = servizi.stato(impianto["mario"])
    assert s.presente and s.reparto_id == impianto["pulsanti"].pk
    # da qui in poi tutto come sempre: dall'ingresso si esce
    servizi.timbra(ing, "1234", "USC", adesso + dt.timedelta(hours=1))
    [turno] = servizi.turni(timezone.localdate(adesso), timezone.localdate())[impianto["mario"].pk]
    assert round(turno.per_reparto[impianto["pulsanti"].pk]) == 3600 and not turno.anomalie
