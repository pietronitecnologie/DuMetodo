from typing import Any

from django import forms

from . import servizi
from .registro import PARAMETRI, Gruppo, Parametro, Tipo


def _campo(p: Parametro) -> forms.Field:
    comuni: dict[str, Any] = {"label": p.etichetta, "help_text": p.descrizione, "required": False}
    match p.tipo:
        case Tipo.BOOLEANO:
            return forms.BooleanField(**comuni)
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
