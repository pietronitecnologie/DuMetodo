# Analisi di sicurezza del server di Metodo v2

Due modi, con gli stessi controlli e lo stesso accesso senza root (sotto):

- **Senza Ansible** (anche da Windows, senza WSL: `ssh` e `scp` sono già inclusi):

  ```bash
  scp produzione/analisi-sicurezza.sh claude@SERVER:
  ssh -t claude@SERVER "sudo sh analisi-sicurezza.sh /opt/metodo"
  scp claude@SERVER:analisi-sicurezza-*.txt .
  ```

  `-t` serve perché `sudo` chieda la password; l'ultimo argomento è la cartella di Metodo v2 sul server.
- **Con Ansible** (Linux, macOS o Windows con WSL): `analisi.yml`, vedi «Uso» più sotto.

## Accesso senza root

Sul server, una volta sola (da amministratore):

```bash
sudo adduser --gecos "" claude          # scegli una password: servirà per sudo
sudo usermod -aG sudo claude
sudo install -d -m 700 -o claude -g claude /home/claude/.ssh
# incolla la chiave PUBBLICA (id_ed25519.pub) del computer che lancia Ansible:
sudo nano /home/claude/.ssh/authorized_keys
sudo chown claude:claude /home/claude/.ssh/authorized_keys && sudo chmod 600 /home/claude/.ssh/authorized_keys
```

- Si entra **solo con la chiave** come `claude`, mai come root.
- Ogni comando da amministratore passa da `sudo` **con la password**, che Ansible chiede al terminale a ogni
  esecuzione (`become_ask_pass` in `ansible.cfg`): senza di te non cambia niente.
- Per togliere l'accesso: `sudo deluser --remove-home claude`.

## Uso

```bash
cp inventario.example.ini inventario.ini   # metti l'indirizzo del server e la cartella di Metodo v2
ansible-playbook analisi.yml               # solo lettura: rapporto in rapporti/
```

`analisi.yml` non cambia niente sul server e non legge password né chiavi (niente `.env`, `docker inspect` o
journal completo). Il rapporto (in `rapporti/`, escluso da git) va riletto prima di condividerlo.
