"""preleva-da-metodo.sh: codifica della password nell'indirizzo del database."""

import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "preleva-da-metodo.sh"


@pytest.mark.parametrize(
    ("testo", "atteso"),
    [
        ("Pass@2411!", "Pass%402411%21"),
        ("a/b:c?d#e%f", "a%2Fb%3Ac%3Fd%23e%25f"),
        ("ok-1_2.3~", "ok-1_2.3~"),
        ("è", "%C3%A8"),
        ("con spazio'", "con%20spazio%27"),
    ],
)
def test_urlencode(testo, atteso):
    r = subprocess.run(
        ["bash", "-c", f'source "{SCRIPT}"; urlencode "$1"', "_", testo], capture_output=True, text=True, check=True
    )
    assert r.stdout == atteso


def test_sintassi():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
