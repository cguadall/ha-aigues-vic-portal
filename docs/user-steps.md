# Passos pendents de l'usuari

[English](user-steps.en.md)

Aquests passos necessiten el teu compte del portal, el teu navegador o la teva
instal·lació de Home Assistant, i no es poden automatitzar des del
repositori. Marca'ls a mesura que els completis.

## 1. Seguretat del compte

- [ ] Canvia la contrasenya de l'oficina virtual. Durant l'anàlisi inicial es
      va veure la forma de l'actual (longitud i posició dels símbols).
- [ ] Desmarca «Recorda'm» i esborra les galetes d'`oficina.aiguesvic.com` al
      navegador. Amb aquesta opció activada, el portal desa el correu i la
      contrasenya en clar a les galetes `username` i `password`.

## 2. Captures HAR addicionals

El HAR del 16/10/2026 ja confirma el login fallit i el login correcte posterior.
El logout satisfactori està capturat al HAR del 15/10/2026 de les 23:40.
La captura de les 16:29, d'un contenidor Firefox, confirma també el primer
`GET /login`, l'aprovació de cookies i un altre login/logout complet.
No cal repetir aquests casos.

La captura del 20/10/2026 de les 23:04 confirma les consultes diàries anuals
i dades des de gener de 2022. La consulta horària de sis mesos té el cos
incomplet al HAR, però el CSV exportat conté 4.345 hores consecutives fins
al 18/10 i coincideix amb totes les files llegibles del HAR. Repetir amb un
mes és opcional per investigar la captura, no per demostrar aquestes dades.

Cal capturar els casos pendents de la taula. Per a cadascun:

1. Obre les eines de desenvolupament del navegador (F12) → pestanya
   **Xarxa**, i marca **Conserva el registre**.
2. Fes l'acció indicada, sense marcar «Recorda'm».
3. Exporta-ho amb **Desa-ho tot com a HAR** a `local/` (Git ignora aquesta
   carpeta), quan la petició hagi acabat. Per a l'horari llarg, comença amb
   un mes per evitar una resposta massa gran.
4. Executa `.venv/bin/python scripts/anonymize_har.py`. Si no pot garantir
   l'anonimització, acaba amb error i no escriu res.
5. Comunica quins fitxers nous hi ha a `local/anonymized/`. No comparteixis
   mai els HAR originals.

Els CSV exportats també contenen dades personals (número de comptador).
Mantén-los a `local/` i no els comparteixis sense anonimitzar: l'script
anterior només processa HAR, no CSV.

| Captura | Acció | Per què |
| --- | --- | --- |
| Login fallit — completat | Capturat el 16/10/2026 | Confirmat: 302 a `/login` i formulari amb «Dades d'accés incorrectes.» |
| Login correcte i logout — completats | Capturats el 16/10 i el 15/10/2026, respectivament | Redireccions confirmades |
| Formulari inicial i cookies — completats | Capturats el 16/10/2026 a les 16:29 en un contenidor Firefox | Formulari inicial i Cookiebot confirmats; falta validar el client HTTP sense `CookieConsent` |
| Sessió caducada | Deixa la pestanya oberta unes hores i torna a carregar `/abonat/consums` | Detectar la caducitat i la redirecció |
| Històric diari — completat | Capturat el 20/10/2026: intervals anuals de 2022, 2024 i 2025 | Taules completes; inici exclòs, dies absents i lectures de diversos dies |
| Històric antic — parcial | Confirmat des de gener de 2022; consulta un any anterior (p. ex. 2017) | Encara no es coneix el límit més antic |
| Històric horari — dades contrastades | CSV de sis mesos amb 4.345 hores, del 20/04 al 18/10/2026; coincideixen les 2.218 files llegibles del HAR | Captura d'un mes opcional per investigar els nuls del HAR; no hi ha un límit de files demostrat |
| Canvi d'hora | A `/abonat/consums/horaris`, consulta del 24 al 27 d'octubre de 2026 (després del 27) | Confirmar el fus horari i el dia de 25 hores |
| Altres contractes — completat | Capturat el 20/10/2026: `/abonat/contractes` amb l'estat `ALTA`/`BAIXA` | La integració només consulta els contractes `ALTA` |

## 3. Prova en una instal·lació real

La versió 0.1.0 ja és instal·lable. S'ha provat contra un portal simulat, no
contra el real.

- [ ] Copia `custom_components/aigues_vic/` a la carpeta `config/custom_components/`
      de Home Assistant (versió 2026.10 o posterior) i reinicia. Per a Docker
      Compose en una Raspberry Pi, segueix la
      [guia de desplegament](deployment-docker.md).
- [ ] Activa el registre de depuració a `configuration.yaml` abans de provar:

      ```yaml
      logger:
        logs:
          custom_components.aigues_vic: debug
      ```

- [ ] Prova primer una contrasenya incorrecta: ha de mostrar «El correu o la
      contrasenya no són correctes». El flux del portal ja està confirmat
      amb el HAR; si el resultat difereix, recull el registre de la integració.
- [ ] Afegeix la integració des de **Configuració → Dispositius i serveis** amb
      el teu correu i contrasenya. Comprova que pot iniciar sessió sense
      importar cookies del navegador ni aprovar Cookiebot: el client HTTP
      no executa JavaScript ni envia `CookieConsent`.
- [ ] Comprova que cada contracte actiu té un dispositiu i que els sensors
      coincideixen amb el que mostra el portal (lectura, data, consum del
      darrer dia i darrer consum facturat). Els contractes de baixa no han de
      tenir dispositiu.
- [ ] Prem el botó **Sincronitza ara** del dispositiu i comprova als registres
      que es fa una nova consulta sense errors.
- [ ] Al cap d'uns minuts, a **Eines de desenvolupament → Estadístiques**, cerca
      `aigues_vic:water_<contracte>` i compara uns quants consums horaris amb
      els del portal.
- [ ] Afegeix l'estadística «<contracte> Consum d'aigua» al panell d'energia.
      La lectura del comptador és només l'índex acumulat del mateix comptador
      (en m³ sencers), no un consum addicional; no l'afegeixis al panell.
- [ ] Activa l'opció d'importació de l'històric i comprova que, en tornar-la a
      executar o en recarregar la integració, no es dupliquen els consums.
- [ ] Deixa-la funcionant més d'un dia per comprovar l'actualització automàtica
      diària entre les 06:00 i les 06:59 (zona horària configurada a Home
      Assistant; l'hora exacta surt als diagnòstics), la renovació de la
      sessió i la importació incremental.
- [ ] Si alguna cosa falla, adjunta els diagnòstics de la integració i les
      línies de registre de `custom_components.aigues_vic`. Estan pensats per
      no incloure dades personals, però revisa'ls igualment abans de
      compartir-los.

      Si la configuració inicial queda en reintent, activa el registre
      `debug`, reinicia o recarrega i cerca `Portal data update failed`.
      Aquest missatge inclou el traceback de la causa original, encara que
      la interfície només mostri el tipus d'error. Amb Docker:

      ```bash
      sudo docker logs --since 10m homeassistant 2>&1 \
        | grep -A 45 -B 3 'Portal data update failed'
      ```

## 4. Decisions de publicació

- [ ] Decidir quan fer públic el repositori: HACS només admet repositoris
      públics.
- [ ] Crear la primera versió (`v0.1.0`) a GitHub quan la prova real sigui
      correcta.
