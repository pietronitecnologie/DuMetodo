"""Area «sistema»: versione e fine supporto, aggiornamenti, risorse, dischi, servizi, orologio, log."""

from __future__ import annotations

import os
import re
from pathlib import Path

from .base import ATTENZIONE, ERRORE, OK, Contesto, Esito, esegui, fine_supporto, leggi, presente

G = "sistema"
# Fine del supporto standard (aggiornamenti di sicurezza gratuiti).
FINE_SUPPORTO = {
    "ubuntu": {"16.04": "2021-04", "18.04": "2023-05", "20.04": "2025-05", "22.04": "2027-06", "24.04": "2029-05"},
    "debian": {"9": "2022-06", "10": "2024-06", "11": "2026-08", "12": "2028-06", "13": "2030-06"},
}
FS_REALI = {"ext2", "ext3", "ext4", "xfs", "btrfs", "zfs", "f2fs", "jfs", "reiserfs"}


def presente_area(ctx: Contesto) -> bool:
    return True


def leggi_os_release(testo: str) -> dict[str, str]:
    return {k: v.strip().strip('"') for k, _, v in (r.partition("=") for r in testo.splitlines()) if v}


def leggi_meminfo(testo: str) -> dict[str, int]:
    out = {}
    for riga in testo.splitlines():
        chiave, _, valore = riga.partition(":")
        parti = valore.split()
        if parti and parti[0].isdigit():
            out[chiave.strip()] = int(parti[0]) * 1024
    return out


def misura(n: float) -> str:
    for unita in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unita == "TB":
            return f"{n:.0f} {unita}" if unita in ("B", "KB") else f"{n:.1f} {unita}"
        n /= 1024
    return str(n)


def dischi(testo_mounts: str) -> list[tuple[str, str]]:
    """(dispositivo, punto di montaggio) dei filesystem reali, uno per dispositivo."""
    visti, out = set(), []
    for riga in testo_mounts.splitlines():
        parti = riga.split()
        if len(parti) < 3 or parti[2] not in FS_REALI or parti[0] in visti:
            continue
        visti.add(parti[0])
        out.append((parti[0], parti[1].replace("\\040", " ")))
    return out


def controlli(ctx: Contesto) -> list[Esito]:
    e: list[Esito] = []
    agg = e.append

    # Versione del sistema e fine del supporto
    rel = leggi_os_release(leggi("/etc/os-release"))
    nome = rel.get("PRETTY_NAME", "sconosciuto")
    distro, versione = rel.get("ID", ""), rel.get("VERSION_ID", "")
    livello, testo = fine_supporto(FINE_SUPPORTO.get(distro, {}).get(versione), ctx.oggi, nome)
    agg(
        Esito(
            "Sistema operativo", livello, f"{nome}, kernel {os.uname().release}" + (f" — {testo}" if testo else ""), G
        )
    )

    if Path("/var/run/reboot-required").exists():
        pacchetti = [p for p in leggi("/var/run/reboot-required.pkgs").split() if p]
        agg(
            Esito(
                "Riavvio", ATTENZIONE, f"richiesto dopo gli aggiornamenti ({', '.join(pacchetti[:5]) or 'kernel'})", G
            )
        )
    else:
        agg(Esito("Riavvio", OK, "non richiesto", G))

    if presente("apt"):
        r = esegui(["apt", "list", "--upgradable"], timeout=60)
        righe = [x for x in r.testo.splitlines() if "/" in x and "upgradable" in x]
        sicurezza = [x.split("/")[0] for x in righe if "-security" in x]
        if sicurezza:
            agg(
                Esito(
                    "Aggiornamenti",
                    ATTENZIONE,
                    f"{len(sicurezza)} di sicurezza ({', '.join(sicurezza[:6])}…) su {len(righe)}: sudo apt upgrade",
                    G,
                )
            )
        else:
            agg(
                Esito(
                    "Aggiornamenti",
                    OK,
                    f"{len(righe)} disponibili, nessuno di sicurezza" if righe else "sistema aggiornato",
                    G,
                )
            )

    # Risorse
    with open("/proc/loadavg") as f:
        carico = [float(x) for x in f.read().split()[:3]]
    cpu = os.cpu_count() or 1
    livello = ERRORE if carico[1] > cpu * 2 else ATTENZIONE if carico[1] > cpu else OK
    agg(Esito("Carico CPU", livello, f"{carico[0]:.2f} / {carico[1]:.2f} / {carico[2]:.2f} su {cpu} CPU", G))
    mem = leggi_meminfo(leggi("/proc/meminfo"))
    totale, libera = mem.get("MemTotal", 0), mem.get("MemAvailable", 0)
    if totale:
        quota = libera / totale
        livello = ERRORE if quota < 0.05 else ATTENZIONE if quota < 0.10 else OK
        agg(Esito("Memoria", livello, f"{misura(libera)} disponibili su {misura(totale)} ({quota:.0%})", G))
    swap, swap_libera = mem.get("SwapTotal", 0), mem.get("SwapFree", 0)
    if swap:
        usata = 1 - swap_libera / swap
        agg(Esito("Swap", ATTENZIONE if usata > 0.5 else OK, f"usata al {usata:.0%} di {misura(swap)}", G))
    r = esegui(["journalctl", "-k", "--since", "-24h", "--no-pager", "-q"], timeout=30)
    oom = len(re.findall(r"Out of memory|oom-kill|Killed process", r.testo))
    if r.ok:
        agg(
            Esito(
                "Memoria esaurita (24 ore)",
                ATTENZIONE if oom else OK,
                f"{oom} processi chiusi per mancanza di memoria" if oom else "nessun processo chiuso",
                G,
            )
        )

    # Dischi
    for dispositivo, punto in dischi(leggi("/proc/mounts")):
        try:
            v = os.statvfs(punto)
        except OSError:
            continue
        totale, libero = v.f_blocks * v.f_frsize, v.f_bavail * v.f_frsize
        if not totale:
            continue
        quota = libero / totale
        inode = v.f_favail / v.f_files if v.f_files else 1
        # Sui dischi grandi conta anche lo spazio assoluto (1 GB); quelli piccoli (es. /boot) solo in percentuale.
        poco = totale > 20 << 30 and libero < 1 << 30
        livello = ERRORE if quota < 0.05 or poco or inode < 0.05 else ATTENZIONE if quota < 0.15 or inode < 0.15 else OK
        dettaglio = f"{misura(libero)} liberi su {misura(totale)} ({quota:.0%})"
        if inode < 0.15:
            dettaglio += f", inode liberi {inode:.0%}"
        agg(Esito(f"Disco {punto}", livello, f"{dettaglio} — {dispositivo}", G))

    # Servizi, orologio, log
    if presente("systemctl"):
        r = esegui(["systemctl", "--failed", "--no-legend", "--plain"], timeout=20)
        falliti = [x.split()[0] for x in r.testo.splitlines() if x.strip()]
        agg(
            Esito(
                "Servizi falliti",
                ERRORE if falliti else OK,
                ", ".join(falliti) + " — systemctl status NOME" if falliti else "nessuno",
                G,
            )
        )
    r = esegui(["timedatectl", "show", "-p", "NTPSynchronized", "--value"], timeout=10)
    if r.ok:
        agg(
            Esito(
                "Orologio",
                OK if r.testo.strip() == "yes" else ATTENZIONE,
                "sincronizzato (NTP)"
                if r.testo.strip() == "yes"
                else "non sincronizzato: date di documenti e log sbagliate",
                G,
            )
        )
    r = esegui(["journalctl", "--disk-usage"], timeout=20)
    m = re.search(r"take up ([\d.]+)([KMGT])", r.testo)
    if m:
        gb = float(m.group(1)) * {"K": 1e-6, "M": 1e-3, "G": 1, "T": 1000}[m.group(2)]
        agg(
            Esito(
                "Log di sistema (journal)",
                ATTENZIONE if gb > 4 else OK,
                f"{gb:.1f} GB" + (" — sudo journalctl --vacuum-size=1G" if gb > 4 else ""),
                G,
            )
        )
    secondi = float(leggi("/proc/uptime").split()[0] or 0) if leggi("/proc/uptime") else 0
    agg(Esito("Acceso da", OK, f"{int(secondi // 86400)} giorni, {int(secondi % 86400 // 3600)} ore", G))
    return e
