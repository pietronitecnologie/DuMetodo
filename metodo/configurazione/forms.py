from typing import Any

from django import forms

from . import servizi
from .registro import PARAMETRI, Gruppo, Parametro, Tipo


def _campo(p: Parametro) -> forms.Field:
    comuni: dict[str, Any] = {"label": p.etichetta, "help_text": p.descrizione, "required": False}
    match p.tipo:
        case Tipo.BOOLEANO:
            return forms.BooleanField(**comuni)
        case Tipo.SCELTA:
            return forms.ChoiceField(**comuni, choices=p.scelte)
        case Tipo.INTERO:
            return forms.IntegerField(**comuni)
        case Tipo.DECIMALE:
            # CharField: accetta sia "12,5" sia "12.5"; la validazione vera è in Parametro.converti
            return forms.CharField(**comuni, widget=forms.TextInput(attrs={"inputmode": "decimal"}))
        case Tipo.EMAIL:
            return forms.EmailField(**comuni)
        case Tipo.TRACCIATO | Tipo.MENU_CLIENT | Tipo.SCORCIATOIE:
            attrs = {"rows": 14, "class": "font-monospace", "spellcheck": "false", "data-tracciato": p.chiave}
            return forms.CharField(**comuni, strip=False, widget=forms.Textarea(attrs=attrs))
        case Tipo.SEGRETO:
            return forms.CharField(
                **comuni,
                strip=False,
                widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
            )
        case Tipo.IMMAGINE:
            return forms.FileField(**comuni, widget=forms.FileInput(attrs={"accept": "image/png,image/jpeg"}))
        case Tipo.LISTA_EMAIL:
            comuni["help_text"] = f"{p.descrizione} Più indirizzi separati da virgola."
            return forms.CharField(**comuni, widget=forms.Textarea(attrs={"rows": 2}))
        case _:
            return forms.CharField(**comuni)


class ParametriGruppoForm(forms.Form):
    """Form generato dal registro per un gruppo di parametri."""

    def __init__(self, gruppo: Gruppo, *args: Any, **kwargs: Any) -> None:
        self.gruppo = gruppo
        self.parametri = [p for p in PARAMETRI if p.gruppo == gruppo]
        initial = {
            p.chiave: p.come_testo(servizi.leggi(p.chiave)) if p.tipo != Tipo.BOOLEANO else servizi.leggi(p.chiave)
            for p in self.parametri
            if p.tipo not in (Tipo.SEGRETO, Tipo.IMMAGINE)  # password e file non tornano nella pagina
        }
        super().__init__(*args, initial=initial, prefix=gruppo.name.lower(), **kwargs)
        self.segreti_impostati: set[str] = set()
        self.immagini: dict[str, str] = {}
        for p in self.parametri:
            self.fields[p.chiave] = _campo(p)
            if p.tipo == Tipo.IMMAGINE and (attuale := servizi.leggi(p.chiave)):
                self.immagini[p.chiave] = attuale
                self.fields[f"{p.chiave}__cancella"] = forms.BooleanField(
                    label=f"Rimuovi {p.etichetta.lower()}", required=False
                )
            if p.tipo == Tipo.SEGRETO:
                if servizi.segreto_impostato(p.chiave):
                    self.segreti_impostati.add(p.chiave)
                    self.fields[p.chiave].widget.attrs["placeholder"] = (
                        "•••••••• impostata: lascia vuoto per non cambiarla"
                    )
                    self.fields[f"{p.chiave}__cancella"] = forms.BooleanField(
                        label=f"Cancella {p.etichetta.lower()}", required=False
                    )
                else:
                    self.fields[p.chiave].widget.attrs["placeholder"] = "non impostata"
        self.predefiniti = {p.chiave: p.come_testo(p.valore_default()) for p in self.parametri}

    def clean(self) -> dict[str, Any]:
        dati = super().clean()
        for p in self.parametri:
            if p.chiave in self.errors:
                continue
            try:
                dati[p.chiave] = p.converti(dati.get(p.chiave))
            except forms.ValidationError as exc:
                self.add_error(p.chiave, exc)
        return dati


class ParametriCommittenteForm(forms.Form):
    """Valori propri di un cliente committente per un gruppo di parametri (vuoto = nessun valore suo)."""

    def __init__(self, gruppo: Gruppo, committente: Any, *args: Any, **kwargs: Any) -> None:
        self.gruppo, self.committente = gruppo, committente
        self.parametri = [p for p in PARAMETRI if p.gruppo == gruppo and p.per_committente]
        initial: dict[str, Any] = {}
        for p in self.parametri:
            if p.tipo == Tipo.SEGRETO or not servizi.ha_valore_proprio(p.chiave, committente):
                continue
            v = servizi.leggi(p.chiave, committente)
            initial[p.chiave] = ("1" if v else "0") if p.tipo == Tipo.BOOLEANO else p.come_testo(v)
        super().__init__(*args, initial=initial, prefix=gruppo.name.lower(), **kwargs)
        self.segreti_impostati: set[str] = set()
        # Cosa vale se il campo resta vuoto: il valore generale oppure niente (parametri PROPRI).
        self.se_vuoto: dict[str, str] = {}
        for p in self.parametri:
            eredita = servizi.eredita_generale(p.chiave, committente)
            # Vuoto: il valore generale, oppure (parametri propri del cliente) il valore predefinito del parametro.
            generale = servizi.leggi(p.chiave) if eredita else p.valore_default()
            vuoto = "Come il generale" if eredita else "Non impostato"
            if p.tipo == Tipo.SEGRETO:
                testo = "quella generale" if eredita and generale else ""
            elif p.tipo == Tipo.SCELTA:
                testo = p.etichetta_scelta(generale) if generale not in (None, "") else ""
            else:
                testo = p.come_testo(generale) if generale is not None else ""
            if p.tipo == Tipo.BOOLEANO:
                campo: forms.Field = forms.ChoiceField(
                    label=p.etichetta,
                    help_text=p.descrizione,
                    required=False,
                    choices=[("", f"{vuoto} ({testo or 'No'})"), ("1", "Sì"), ("0", "No")],
                )
            elif p.tipo == Tipo.SCELTA:
                campo = forms.ChoiceField(
                    label=p.etichetta,
                    help_text=p.descrizione,
                    required=False,
                    choices=[("", f"{vuoto} ({testo})" if testo else "—"), *p.scelte],
                )
            else:
                campo = _campo(p)
                campo.widget.attrs["placeholder"] = (
                    f"{vuoto}: {testo[:80]}" if testo and p.tipo != Tipo.TRACCIATO else "non impostato"
                )
            self.fields[p.chiave] = campo
            self.se_vuoto[p.chiave] = testo
            if p.tipo == Tipo.SEGRETO and servizi.ha_valore_proprio(p.chiave, committente):
                self.segreti_impostati.add(p.chiave)
                campo.widget.attrs["placeholder"] = "•••••••• impostata: lascia vuoto per non cambiarla"
                self.fields[f"{p.chiave}__cancella"] = forms.BooleanField(
                    label=f"Cancella {p.etichetta.lower()}", required=False
                )

    def clean(self) -> dict[str, Any]:
        dati = super().clean()
        for p in self.parametri:
            if p.chiave in self.errors:
                continue
            grezzo = dati.get(p.chiave)
            if grezzo in (None, "") or (isinstance(grezzo, str) and not grezzo.strip()):
                dati[p.chiave] = None
                continue
            try:
                dati[p.chiave] = p.converti(grezzo == "1" if p.tipo == Tipo.BOOLEANO else grezzo)
            except forms.ValidationError as exc:
                self.add_error(p.chiave, exc)
        return dati
