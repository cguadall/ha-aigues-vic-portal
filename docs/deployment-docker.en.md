# Deploying on Home Assistant with Docker Compose

[Català](deployment-docker.md)

Guide to install the integration on a Home Assistant Container managed with
Docker Compose on a Linux server, such as a Raspberry Pi. The names
`homeassistant`, `/opt/homeassistant` and `/opt/ha-aigues-vic-portal` are
examples: adapt them to your `docker-compose.yml`.

## 1. Prerequisites

- **Architecture:** `uname -m` must print `aarch64`. Home Assistant no longer
  publishes images for 32-bit ARM systems; use 64-bit Raspberry Pi OS.
- **Version:** Home Assistant **2026.10.0** or later is required. Check it under
  *Settings → About* or with:

  ```bash
  docker compose exec homeassistant python -m homeassistant --version
  ```

- **Configuration volume:** find the host folder mounted on `/config`:

  ```bash
  docker compose config homeassistant | grep -B3 'target: /config'
  ```

  This guide uses `/opt/homeassistant/config`.
- **Recorder:** it must be enabled (it is by default). SQLite, MariaDB and
  PostgreSQL all work.
- **Internet access:** the container must reach the portal API over HTTPS:

  ```bash
  docker compose exec homeassistant python -c \
    "import urllib.request as u, urllib.error as e;
try: u.urlopen('https://api-oficina.aiguesvic.com/', timeout=20)
except e.HTTPError as err: print(f'Connection OK (HTTP {err.code})')
except Exception as err: print(f'Error: {err}')"
  ```
- The integration has no external Python dependencies (`requirements` is
  empty): nothing needs installing in the container or building for ARM.

## 2. Backup

The integration writes long-term statistics to the database. Before the first
installation, make a backup:

- From *Settings → System → Backups*, or
- With the container stopped:

  ```bash
  cd /opt/homeassistant
  docker compose stop homeassistant
  sudo tar -czf ~/ha-config-$(date +%F).tar.gz -C /opt/homeassistant config
  docker compose start homeassistant
  ```

The backup contains credentials (see section 8): store it somewhere protected.

## 3. Getting the code

The repository is public on GitHub (`https://github.com/cguadall/ha-aigues-vic-portal`). It can be added directly to HACS as a custom repository, or cloned onto the host:

```bash
sudo git clone https://github.com/cguadall/ha-aigues-vic-portal.git /opt/ha-aigues-vic-portal
```

For the test, pin the version to deploy (`git checkout <commit or tag>`). The
`.venv` and development tools are not needed on the Raspberry Pi.

Alternative without GitHub access from the Raspberry Pi: from the development
machine,

```bash
rsync -a --delete --exclude __pycache__ \
  custom_components/aigues_vic/ user@raspberry:/tmp/aigues_vic/
```

and then move it to the folder in the next section.

## 4. Installing the integration

There are two options. Choose only one.

### Option A: copy into the configuration (recommended)

```bash
sudo mkdir -p /opt/homeassistant/config/custom_components
sudo rsync -a --delete --exclude __pycache__ \
  /opt/ha-aigues-vic-portal/custom_components/aigues_vic/ \
  /opt/homeassistant/config/custom_components/aigues_vic/
```

The final folder must be `config/custom_components/aigues_vic/` and contain
`manifest.json` directly. It is included in Home Assistant backups.

Do not create a symlink to `/opt/ha-aigues-vic-portal`: that path does not
exist inside the container, so the link would be broken.

### Option A with the deployment script

From the development machine, [`scripts/deploy.sh`](../scripts/deploy.sh)
performs the option A copy over SSH, without cloning the repository on the
Raspberry Pi. By default it sends `custom_components/aigues_vic/` to
`carles@192.168.100.111:/root/home-assistant/config/custom_components/aigues_vic/`:

```bash
scripts/deploy.sh --dry-run    # show what would change, without touching anything
scripts/deploy.sh              # ask for confirmation and copy
scripts/deploy.sh --restart    # copy and restart the homeassistant container
```

- It first syncs with `rsync` into a temporary folder of the SSH user, then
  installs it with `sudo rsync` in an interactive session, so `sudo` can ask
  for the password. The temporary folder is removed at the end.
- It deletes files from the destination that are no longer in the
  repository and leaves files owned by `root:root`. It does not send
  `__pycache__`.
- It checks that `/root/home-assistant/config` exists before copying.
- It requires `rsync` on both machines (`sudo apt install rsync` on the
  Raspberry Pi). With an SSH key (`ssh-copy-id carles@192.168.100.111`) the
  SSH password is not needed; `sudo` may still ask for one.
- The destination can be changed with `AIGUES_VIC_REMOTE`, `AIGUES_VIC_DEST`,
  `AIGUES_VIC_CONTAINER` and `AIGUES_VIC_OWNER`.

### Option B: read-only mount

Add to the service in `docker-compose.yml`:

```yaml
services:
  homeassistant:
    volumes:
      - /opt/homeassistant/config:/config
      - /opt/ha-aigues-vic-portal/custom_components/aigues_vic:/config/custom_components/aigues_vic:ro
```

and apply it with `docker compose up -d homeassistant`. Updates are a
`git pull` and a restart, but the integration is not included in Home
Assistant backups and cannot move to HACS without removing the mount.

## 5. Restarting and checking it loads

```bash
cd /opt/homeassistant
docker compose restart homeassistant
docker compose logs -f --since 5m homeassistant | grep -i aigues_vic
```

The warning *"We found a custom integration aigues_vic which has not been
tested by Home Assistant"* is expected. If *"Error loading custom_components"*
or *"Integration not found"* appears, check the path from section 4.

## 6. Configuring and testing

1. Enable debug logging before adding the integration. You can add this to
   `configuration.yaml` and restart:

   ```yaml
   logger:
     logs:
       custom_components.aigues_vic: debug
   ```

   Once the integration exists, it can also be enabled from its page with
   *Enable debug logging*, without restarting.
2. Follow the tests in [section 3 of the user steps](user-steps.en.md):
   wrong password, setup with the real account, devices and sensors,
   statistics and the energy dashboard.
3. Check the log:

   ```bash
   docker compose logs --since 1h homeassistant | grep -i aigues_vic
   ```

4. Do not enable the full history import until the initial load is correct.
   On a Raspberry Pi, especially with an SD card, the import writes a lot to
   the database; an SSD is preferable.

## 7. Updating, rolling back and uninstalling

- **Update:** `git -C /opt/ha-aigues-vic-portal pull`, repeat the copy from
  section 4 (option A) and restart the container.
- **Roll back:** `git -C /opt/ha-aigues-vic-portal checkout <previous commit>`,
  copy and restart. If statistics are wrong, restore the backup from
  section 2.
- **Uninstall:**
  1. Delete the integration from *Settings → Devices & services*. This also
     deletes the import state stored in `.storage`.
  2. Delete `config/custom_components/aigues_vic/` (or remove the option B
     mount) and restart.
  3. The `aigues_vic:water_<contract>` statistics remain in the recorder. If
     you do not want them, delete them under *Developer tools → Statistics*
     and remove them from the energy dashboard.
- Once the repository is public, it can move to HACS: remove the manual copy,
  add the custom repository in HACS and install it from there.

## 8. Security

- Home Assistant stores the portal email and password in
  `config/.storage/core.config_entries`, unencrypted, like other integrations
  with credentials. Restrict access to the configuration folder and backups.
- The integration diagnostics and log are designed to exclude credentials and
  personal data, but when downloading device diagnostics, Home Assistant's
  automatic device and entity registry sections may still include the contract
  number, alias and meter serial number: review the file before sharing it.
- Do not copy the repository's `local/` folder to the Raspberry Pi: it contains
  HAR and CSV files with personal data.
