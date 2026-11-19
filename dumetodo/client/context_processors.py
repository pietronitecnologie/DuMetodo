import re

from django.http import HttpRequest

MARCATORE = "DuMetodoClient/"
_RE_VERSIONE = re.compile(r"DuMetodoClient/([0-9]+(?:\.[0-9]+){1,3})")


def client(request: HttpRequest) -> dict[str, object]:
    """Pagina aperta dal client desktop (marcatore nello user agent) e versione del client."""
    ua = request.headers.get("User-Agent", "")
    m = _RE_VERSIONE.search(ua)
    return {"is_client_desktop": MARCATORE in ua, "versione_client": m.group(1) if m else ""}
