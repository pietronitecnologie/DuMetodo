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
        }
        super().__init__(*args, initial=initial, prefix=gruppo.name.lower(), **kwargs)
        for p in self.parametri:
            self.fields[p.chiave] = _campo(p)

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
