from django.http import HttpRequest

MARCATORE = "DuMetodoClient/"


def client(request: HttpRequest) -> dict[str, object]:
    """True quando la pagina è aperta dal client desktop (che aggiunge il marcatore allo user agent)."""
    return {"is_client_desktop": MARCATORE in request.headers.get("User-Agent", "")}
