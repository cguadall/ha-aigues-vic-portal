# Desplegament en Home Assistant amb Docker Compose

[English](deployment-docker.en.md)

Guia per instal·lar la integració en un Home Assistant Container gestionat amb
Docker Compose en un servidor Linux, per exemple una Raspberry Pi. Els noms
`homeassistant`, `/opt/homeassistant` i `/opt/ha-aigues-vic-portal` són
exemples: adapta'ls al teu `docker-compose.yml`.

## 1. Comprovacions prèvies

- **Arquitectura:** `uname -m` ha de retornar `aarch64`. Home Assistant ja no
  publica imatges per a sistemes ARM de 32 bits; cal Raspberry Pi OS de 64 bits.
- **Versió:** cal Home Assistant **2026.10.0** o posterior. Comprova-ho a
  *Configuració → Quant a* o amb:

  ```bash
  docker compose exec homeassistant python -m homeassistant --version
  ```

- **Volum de configuració:** localitza la carpeta de l'amfitrió muntada a
  `/config`:

  ```bash
  docker compose config homeassistant | grep -B3 'target: /config'
  ```

  En aquesta guia és `/opt/homeassistant/config`.
- **Recorder:** ha d'estar actiu (ho està per defecte). Funciona amb SQLite,
  MariaDB o PostgreSQL.
- **Sortida a Internet:** el contenidor ha d'arribar a l'API del portal per HTTPS:

  ```bash
  docker compose exec homeassistant python -c \
    "import urllib.request as u, urllib.error as e;
try: u.urlopen('https://api-oficina.aiguesvic.com/', timeout=20)
except e.HTTPError as err: print(f'Connexió OK (HTTP {err.code})')
except Exception as err: print(f'Error: {err}')"
  ```
- La integració no té dependències de Python externes (`requirements` és
  buit): no cal instal·lar res al contenidor ni compilar per a ARM.

## 2. Còpia de seguretat

La integració escriu estadístiques a llarg termini a la base de dades. Abans de
la primera instal·lació, fes una còpia de seguretat:

- Des de *Configuració → Sistema → Còpies de seguretat*, o bé
- Amb el contenidor aturat:

  ```bash
  cd /opt/homeassistant
  docker compose stop homeassistant
  sudo tar -czf ~/ha-config-$(date +%F).tar.gz -C /opt/homeassistant config
  docker compose start homeassistant
  ```

La còpia conté credencials (vegeu la secció 8): guarda-la en un lloc protegit.

## 3. Obtenir el codi

El repositori és públic a GitHub (`https://github.com/cguadall/ha-aigues-vic-portal`). Es pot afegir directament a HACS com a repositori personalitzat, o bé clonar-lo a l'amfitrió:

```bash
sudo git clone https://github.com/cguadall/ha-aigues-vic-portal.git /opt/ha-aigues-vic-portal
```

Per a la prova, fixa la versió que vols desplegar (`git checkout <commit o
etiqueta>`). No cal el `.venv` ni les eines de desenvolupament a la Raspberry.

Alternativa sense accés a GitHub des de la Raspberry: des de l'ordinador de
desenvolupament,

```bash
rsync -a --delete --exclude __pycache__ \
  custom_components/aigues_vic/ usuari@raspberry:/tmp/aigues_vic/
```

i després mou-la a la carpeta de la secció següent.

## 4. Instal·lar la integració

Hi ha dues opcions. Tria'n només una.

### Opció A: còpia a la configuració (recomanada)

```bash
sudo mkdir -p /opt/homeassistant/config/custom_components
sudo rsync -a --delete --exclude __pycache__ \
  /opt/ha-aigues-vic-portal/custom_components/aigues_vic/ \
  /opt/homeassistant/config/custom_components/aigues_vic/
```

La carpeta final ha de ser `config/custom_components/aigues_vic/` i contenir
`manifest.json` directament. Queda inclosa a les còpies de seguretat de Home
Assistant.

No facis un enllaç simbòlic cap a `/opt/ha-aigues-vic-portal`: dins del
contenidor aquesta ruta no existeix i l'enllaç quedaria trencat.

### Opció A amb l'script de desplegament

Des de l'ordinador de desenvolupament, [`scripts/deploy.sh`](../scripts/deploy.sh)
fa la còpia de l'opció A per SSH, sense clonar el repositori a la Raspberry.
Per defecte envia `custom_components/aigues_vic/` a
`carles@192.168.100.111:/root/home-assistant/config/custom_components/aigues_vic/`:

```bash
scripts/deploy.sh --dry-run    # mostra què canviaria, sense tocar res
scripts/deploy.sh              # demana confirmació i copia
scripts/deploy.sh --restart    # copia i reinicia el contenidor homeassistant
```

- Primer sincronitza amb `rsync` a una carpeta temporal de l'usuari SSH i
  després la instal·la amb `sudo rsync` en una sessió interactiva, de manera
  que `sudo` pot demanar la contrasenya. Esborra la carpeta temporal en
  acabar.
- Elimina del destí els fitxers que ja no són al repositori i deixa els
  fitxers amb propietari `root:root`. No envia `__pycache__`.
- Comprova que existeix `/root/home-assistant/config` abans de copiar res.
- Requereix `rsync` als dos equips (`sudo apt install rsync` a la Raspberry).
  Amb una clau SSH (`ssh-copy-id carles@192.168.100.111`) no cal escriure la
  contrasenya SSH; `sudo` la pot continuar demanant.
- Es pot canviar el destí amb `AIGUES_VIC_REMOTE`, `AIGUES_VIC_DEST`,
  `AIGUES_VIC_CONTAINER` i `AIGUES_VIC_OWNER`.

### Opció B: muntatge de només lectura

Afegeix al servei de `docker-compose.yml`:

```yaml
services:
  homeassistant:
    volumes:
      - /opt/homeassistant/config:/config
      - /opt/ha-aigues-vic-portal/custom_components/aigues_vic:/config/custom_components/aigues_vic:ro
```

i aplica-ho amb `docker compose up -d homeassistant`. Les actualitzacions són
un `git pull` i un reinici, però la integració no queda a les còpies de
seguretat de Home Assistant i no es pot passar a HACS sense treure el
muntatge.

## 5. Reiniciar i comprovar la càrrega

```bash
cd /opt/homeassistant
docker compose restart homeassistant
docker compose logs -f --since 5m homeassistant | grep -i aigues_vic
```

És normal l'avís *«We found a custom integration aigues_vic which has not been
tested by Home Assistant»*. Si apareix *«Error loading custom_components»* o
*«Integration not found»*, revisa la ruta de la secció 4.

## 6. Configurar i provar

1. Activa el registre de depuració abans d'afegir la integració. Pots afegir
   això a `configuration.yaml` i reiniciar:

   ```yaml
   logger:
     logs:
       custom_components.aigues_vic: debug
   ```

   Quan la integració ja existeix, també es pot activar des de la seva pàgina
   amb *Activa el registre de depuració*, sense reiniciar.
2. Segueix les proves de la [secció 3 dels passos pendents](user-steps.md):
   contrasenya incorrecta, alta amb el compte real, dispositius i sensors,
   estadístiques i panell d'energia.
3. Consulta el registre:

   ```bash
   docker compose logs --since 1h homeassistant | grep -i aigues_vic
   ```

4. No activis la importació de tot l'històric fins que la càrrega inicial
   sigui correcta. A la Raspberry, sobretot amb targeta SD, la importació fa
   moltes escriptures a la base de dades; és preferible un SSD.

## 7. Actualitzar, desfer i desinstal·lar

- **Actualitzar:** `git -C /opt/ha-aigues-vic-portal pull`, repeteix la còpia
  de la secció 4 (opció A) i reinicia el contenidor.
- **Tornar a la versió anterior:** `git -C /opt/ha-aigues-vic-portal checkout
  <commit anterior>`, copia i reinicia. Si les estadístiques han quedat
  malament, restaura la còpia de seguretat de la secció 2.
- **Desinstal·lar:**
  1. Elimina la integració des de *Configuració → Dispositius i serveis*.
     Això també esborra l'estat d'importació guardat a `.storage`.
  2. Esborra `config/custom_components/aigues_vic/` (o treu el muntatge de
     l'opció B) i reinicia.
  3. Les estadístiques `aigues_vic:water_<contracte>` es conserven al
     recorder. Si no les vols, esborra-les des de *Eines de desenvolupament →
     Estadístiques* i treu-les del panell d'energia.
- Quan el repositori sigui públic, es podrà passar a HACS: elimina la còpia
  manual, afegeix el repositori personalitzat a HACS i instal·la-la des d'allà.

## 8. Seguretat

- Home Assistant desa el correu i la contrasenya del portal a
  `config/.storage/core.config_entries`, sense xifrar, com fan les altres
  integracions amb credencials. Limita l'accés a la carpeta de configuració i
  a les còpies de seguretat.
- Els diagnòstics i el registre de la integració estan pensats per no incloure
  credencials ni dades personals, però quan es descarreguen els diagnòstics d'un
  dispositiu, les seccions automàtiques del registre de dispositius i entitats
  de Home Assistant poden incloure igualment el número de contracte, l'àlies i
  el número de sèrie del comptador: revisa el fitxer abans de compartir-lo.
- No copiïs a la Raspberry la carpeta `local/` del repositori: conté HAR i
  CSV amb dades personals.
