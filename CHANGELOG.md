# Registre de canvis

## 0.3.0 (2026-11-03)

### Canvis i Adaptació al Nou Portal

- **Migració a la nova API REST JSON**: L'oficina virtual d'Aigües de Vic s'ha renovat completament com a SPA amb una API REST a `https://api-oficina.aiguesvic.com/`. La integració s'ha adaptat per comunicar-se directament amb aquesta API mitjançant autenticació JWT (`access` i `refresh` tokens), eliminant el raspallat d'HTML (HTML scraping).
- **Compatibilitat 1:1 d'identificadors**: Es manté el número de contracte comercial (`contract_number`, p. ex. `100000001`) com a identificador principal d'entitats i d'estadístiques a llarg termini (`aigues_vic:water_<contract_id>`), preservant de forma intacta totes les dades històriques emmagatzemades a Home Assistant.
- **Suport per a lectures decimals**: L'índex del comptador ara admet lectures amb decimals provinents de l'API REST preservant el format sencer per a valors rodons.
- **Suport d'anonimització HAR actualitzat**: L'script `scripts/anonymize_har.py` s'ha actualitzat per detectar i redactar automàticament tokens JWT, UUIDs de contracte, dades d'autenticació i metadades personals dels nous arxius HAR de l'oficina virtual.

## 0.2.0 (sense publicar)

### Canvis

- L'actualització diària es fa entre les 06:00 i les 06:59 (hora local de Home
  Assistant), amb un minut fix per a cada instal·lació.
- La lectura del comptador passa a ser l'**Índex del comptador** i ja no té
  classe d'estat: el consum s'ha d'afegir al panell d'energia des de
  l'estadística «<contracte> Consum d'aigua». Home Assistant avisa a
  *Reparacions* per esborrar les estadístiques antigues del sensor.
- **Data de lectura** és ara una entitat de diagnòstic.
- Cada importació torna a llegir els 14 últims dies (abans, 2).
- La primera importació inclou el mes anterior sencer i el mes en curs.

### Correccions

- L'històric s'importa del més recent al més antic, en trams de 31 dies
  (horari) i 92 dies (diari), i s'atura al primer tram antic sense dades.
  Abans començava el 2016 i fallava amb `ParseError` abans d'arribar a les
  dades. Si un tram antic falla o el portal hi respon amb una pàgina
  inesperada, els dies recents ja baixats es desen, la importació queda en
  error i l'històric no es marca com a complet fins que un reintent arriba al
  primer tram sense dades.
- La importació de l'històric fallava amb `ParseError` en arribar a dates sense
  dades, perquè el portal no mostra la taula de consums quan el període és
  buit. Ara aquests períodes es tracten com a buits, i l'avís del registre
  inclou el detall de l'error.
- Una importació fallida conserva l'estat i els indicadors de l'última
  importació correcta. El registre de depuració inclou la fase, el període
  i el traceback per diagnosticar errors de lectura del portal.
- La importació de l'històric d'un comptador horari inclou també els dies
  anteriors a les dades horàries, amb el valor diari.
- Les dades horàries es demanen en trams de 31 dies en lloc de 360, perquè les
  respostes grans podien superar el temps d'espera i fer fallar tota la
  importació.

### Novetats

- Reintents automàtics després d'un error (30 min, 1 h, 2 h i cada 4 h) i avís a
  *Reparacions* després de 3 errors seguits.
- Sensor de diagnòstic **Darrera importació d'estadístiques**.
- `start_date` de l'acció `aigues_vic.reimport` és opcional: sense data, torna
  a importar tot l'històric.
- Atribut «Històric des de» al sensor **Importació d'estadístiques**.
- Sensor de diagnòstic **Importació d'estadístiques** (*Al dia*, *Important*,
  *Important l'històric*, *Error*) amb la fase, el període i el progrés de la
  importació, el darrer error i el proper reintent.
- Atribut «Dia» al **Consum del darrer dia**.
- Els comptadors diaris es tornen a comprovar cada setmana i passen a
  importar-se en horari quan el portal en té dades.
- En reiniciar Home Assistant es reutilitzen les dades desades si són
  posteriors a l'última actualització programada, sense tornar a iniciar sessió
  al portal.
- Diagnòstics amb l'estat de la importació, l'hora d'actualització i el nombre
  d'errors seguits.
- Icones pròpies per a les entitats.
- Sensor **Consum mínim nocturn** i sensor binari **Possible fuita**, calculats
  a partir de les dades horàries.
- Sensors **Consum d'aquest mes**, **Consum del mes anterior**, **Mitjana
  diària (30 dies)** i **Dades disponibles fins a**.
- Opció de preu de l'aigua per crear l'estadística de cost
  `aigues_vic:cost_<contracte>` per al panell d'energia.
- Acció `aigues_vic.reimport` per tornar a importar des d'un dia concret.

## 0.1.0

- Primera versió: configuració des de la interfície, sensors per contracte,
  estadístiques de consum horari o diari i importació opcional de l'històric.
