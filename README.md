# Aigües Vic per a Home Assistant

[English documentation](README.en.md)

Integració custom, independent i no oficial per consultar consums d'aigua
d'Aigües Vic (AVSA) des de la seva [oficina virtual](https://oficina.aiguesvic.com).

## Estat: versió 0.3.0, pendent de validar amb el portal real

La integració està implementada i provada contra un portal simulat construït a
partir de captures de la nova API REST d'Aigües de Vic, però encara està
pendent de validar amb el portal real en una instal·lació de Home Assistant.
Els fluxos d'autenticació JWT (login i refresc de tokens), llistat de contractes
i obtenció de consums horaris i diaris s'han contrastat amb captures HAR del
nou portal. Consulta [docs/user-steps.md](docs/user-steps.md) per a les proves
pendents.

## Funcionalitats

- Configuració des de la interfície, amb el correu i la contrasenya de
  l'oficina virtual, i reautenticació quan la contrasenya deixa de ser vàlida.
- Un dispositiu per cada contracte actiu (estat `ALTA` a la llista de
  contractes del portal); els de baixa no es consulten. Els contractes nous
  s'afegeixen sols; els que desapareixen o es donen de baixa queden no
  disponibles i es poden eliminar des del dispositiu.
- Sensors per contracte:
  - **Índex del comptador** (m³): l'última lectura diària completa o, si no
    n'hi ha, l'última lectura facturada. És un índex acumulat, no un consum;
    no s'ofereix com a font del panell d'energia.
  - **Data de lectura** (diagnòstic).
  - **Consum del darrer dia** (L): l'últim dia complet, indicat a l'atribut
    «Dia»; el dia en curs s'ignora.
  - **Consum mínim nocturn** (L/h): el consum horari més baix entre les 00:00 i
    les 06:00 de l'últim dia complet. Si no baixa mai a zero pot indicar una
    fuita o un aparell que consumeix contínuament.
  - **Darrer consum facturat** (m³), amb la data final i els dies.
  - **Consum d'aquest mes** i **Consum del mes anterior** (m³), calculats a
    partir de les estadístiques; el mes és el de l'últim dia amb dades (atribut
    «Mes»). El mes anterior queda desconegut si les estadístiques no el cobreixen
    sencer.
  - **Mitjana diària (30 dies)** (L), amb els dies comptats a l'atribut «Dies».
  - **Dades disponibles fins a** (diagnòstic): final de l'última hora (o dia)
    importada.
  - **Darrera importació d'estadístiques** (diagnòstic): quan s'han importat
    les estadístiques per darrera vegada.
  - **Importació d'estadístiques** (diagnòstic): *Al dia*, *Important*,
    *Important l'històric* o *Error*, amb el progrés. Vegeu
    [Estat de la importació](#estat-de-la-importació).
- Sensor binari **Possible fuita** per contracte: s'activa quan cap de les
  últimes 24 hores amb dades no té consum zero. Vegeu
  [Detecció de fuites](#detecció-de-fuites).
- Botó **Sincronitza ara** per contracte (a la secció de configuració del
  dispositiu) per consultar el portal sense esperar l'actualització periòdica.
- Estadístiques a llarg termini `aigues_vic:water_<contracte>` amb el consum
  horari, o diari si el comptador no té dades horàries. Cada setmana es torna a
  comprovar si un comptador diari ja té dades horàries; si en té, s'importen en
  horari a partir del primer dia complet i els dies anteriors es mantenen
  diaris.
- L'índex del comptador i l'estadística de consum són dues representacions del
  mateix comptador: afegeix al panell d'energia només l'estadística
  «<contracte> Consum d'aigua».
- Opció per importar tot l'històric disponible, del més recent al més antic.
- Preu de l'aigua opcional per obtenir l'estadística de cost
  `aigues_vic:cost_<contracte>` per al panell d'energia.
- Acció `aigues_vic.reimport` per tornar a importar les estadístiques des d'un
  dia concret o tot l'històric.
- Diagnòstics sense credencials, adreces, titulars ni números de comptador.
- Interfície i documentació en català i anglès.

No es preveuen pagaments, modificació de contractes ni enviament de lectures.
El portal podria canviar sense avís; la compatibilitat no està garantida per AVSA.

## Instal·lació

Requereix Home Assistant **2026.10.0** o posterior i el `recorder` actiu (ho
està per defecte).

- **HACS (repositori personalitzat):** a HACS, ves a *Integracions*, fes clic al menú de tres punts (a dalt a la dreta) i tria *Repositoris personalitzats*. Afegeix
  `https://github.com/cguadall/ha-aigues-vic-portal` com a repositori
  personalitzat de tipus *Integration*, cerca «Aigües Vic», instal·la'l i reinicia Home Assistant.
- **Manual:** copia `custom_components/aigues_vic/` dins de la carpeta
  `custom_components/` de la configuració de Home Assistant i reinicia.
  Per a Docker Compose (p. ex. en una Raspberry Pi), vegeu
  [docs/deployment-docker.md](docs/deployment-docker.md).

## Configuració

1. *Configuració → Dispositius i serveis → Afegeix integració → Aigües Vic*.
2. Introdueix el correu i la contrasenya de l'oficina virtual. Cada compte
   només es pot afegir una vegada.
3. Opcionalment, a *Opcions*, activa «Importa tot l'històric de consums». La
   integració es recarrega i reconstrueix les estadístiques des de les dades més
   antigues disponibles. Pot trigar uns minuts, perquè les consultes al portal
   es fan d'una en una i espaiades.
4. Opcionalment, a *Opcions*, indica el «Preu de l'aigua» per m³. Vegeu
   [Panell d'energia](#panell-denergia).

La primera importació inclou el mes anterior i el mes en curs (com a mínim els
30 últims dies).

No s'admet configuració YAML.

### Actualització de dades

El portal publica les dades d'un dia durant la matinada següent (el consum
horari arriba fins a les 2:00 aproximadament). La integració consulta el portal
cada dia entre les 06:00 i les 06:59, segons la zona horària configurada a Home
Assistant; el minut és fix per a cada instal·lació, perquè no totes consultin el
portal alhora. Per tant, les dades poden arribar amb fins a un dia de retard.
Els diagnòstics indiquen l'hora exacta (`update_time`).

En iniciar-se o recarregar-se, reutilitza les dades desades si s'han obtingut
després de l'última actualització programada; si no, consulta el portal
immediatament. Cada importació torna a llegir els 14 últims dies per recollir
correccions i continua la suma acumulada sense duplicats.

Si una actualització o una importació falla, es torna a provar al cap de 30
minuts, 1 hora, 2 hores i, després, cada 4 hores fins que funcioni. Després de
3 errors seguits apareix un avís a *Configuració → Reparacions*, que desapareix
sol amb la propera actualització correcta. Si el portal rebutja la contrasenya,
no es reintenta: s'inicia la reautenticació.

Per forçar una sincronització, prem el botó **Sincronitza ara** de qualsevol
contracte: actualitza tots els contractes del compte i les estadístiques. Això
no canvia l'actualització automàtica diària. Si el portal falla, el botó
mostra un error i continua disponible per tornar-ho a provar. També es pot usar
en automatitzacions amb l'acció `button.press`. No cal prémer-lo sovint: el
portal només publica les dades un cop al dia.

#### Estat de la importació

El sensor **Importació d'estadístiques** de cada contracte mostra:

| Estat | Significat |
| --- | --- |
| Al dia | L'última importació ha acabat bé. |
| Important | S'estan important els últims dies. |
| Important l'històric | S'està important més d'un any (històric o `aigues_vic.reimport`). |
| Error | L'última actualització o importació ha fallat; es reintentarà. |

Durant la importació, els atributs «Fase» (comprovant dades horàries, consum
horari, consum diari o cost), «Període» (tram que s'està demanant al portal) i
«Progrés» (%) indiquen per on va; no es desen a l'historial. També té
«Inici», «Darrera importació correcta», «Darrer error», «Errors consecutius»,
«Proper reintent», «Històric complet», «Dades horàries des de» i «Històric
des de». Després de
reiniciar Home Assistant comença a *Al dia* fins a la propera importació.

Si falla, es mantenen els valors de l'última importació correcta. Per investigar
la causa, activa `custom_components.aigues_vic: debug` amb l'acció
`logger.set_level` i torna a sincronitzar. El registre de depuració inclou la
fase, el període i el traceback de l'error, sense desar la resposta del portal.

Exemple d'automatització per avisar si la importació falla:

```yaml
triggers:
  - trigger: state
    entity_id: sensor.<contracte>_importacio_d_estadistiques
    to: error
    for: "01:00:00"
actions:
  - action: notify.notify
    data:
      message: >-
        Aigües Vic no pot importar el consum
        ({{ state_attr(trigger.entity_id, 'last_error') }}).
```

Segons l'avís del portal, un mateix valor repetit en hores consecutives és una
estimació, i un dia sense dades es mostra a 0 i el seu consum s'acumula al dia
següent. La integració importa els valors tal com els dona el portal.

### Detecció de fuites

Amb dades horàries, la integració calcula dos indicadors a cada importació:

- **Consum mínim nocturn**: el valor horari més baix entre les 00:00 i les
  06:00 de l'últim dia complet (atribut «Dia»).
- **Possible fuita**: s'activa si les 24 últimes hores amb dades han tingut
  totes algun consum. Els atributs indiquen el consum horari mínim d'aquestes
  hores i quan acaben.

Són indicadors orientatius, no una detecció certa: un aparell que consumeix
tota la nit (descalcificador, reg) també els activa, i les hores estimades pel
portal (el mateix valor repetit) poden amagar hores sense consum. Les dades
arriben l'endemà, per tant l'avís sempre és del dia anterior. Amb un comptador
sense dades horàries, tots dos queden com a desconeguts.

Es poden fer servir en una automatització per rebre un avís, per exemple quan
**Possible fuita** passa a «Problema».

### Panell d'energia

A *Panell d'energia → Consum d'aigua*, afegeix l'estadística
`aigues_vic:water_<contracte>` («<àlies o contracte> Consum d'aigua»). L'índex
del comptador i aquesta estadística són dues representacions del mateix
comptador; per això l'índex no té classe d'estat i no es pot afegir al panell.
L'estadística assigna cada valor a l'hora (o al dia) en què es va consumir, cosa
que un sensor no pot fer amb dades que arriben l'endemà.

Si abans de la versió 0.2.0 havies afegit la lectura del comptador al panell,
treu-la'n. Home Assistant mostrarà un avís a *Reparacions* perquè el sensor ja
no té classe d'estat; des d'*Eines de desenvolupament → Estadístiques* pots
esborrar-ne les estadístiques antigues.

L'històric s'importa del més recent al més antic: les dades horàries en trams
de 31 dies i les diàries en trams de 92. Només quan un tram més antic no té
dades es considera que l'històric comença allà; el sensor **Importació
d'estadístiques** n'indica el primer dia a «Històric des de». Si el portal
falla o respon amb una pàgina inesperada en un tram antic, la importació queda
en *Error* i no es marca com a completa: els dies més recents ja baixats es
desen igualment i el reintent torna a intentar l'històric. Si un període antic
falla sempre, es pot importar només des d'una data amb `aigues_vic.reimport` i
`start_date`.

Amb un comptador horari, els dies anteriors a les primeres dades horàries del
portal s'importen amb el valor diari, i a partir d'aleshores en horari. El
mateix passa amb un comptador que ha passat de diari a horari.

#### Cost

El panell d'energia només pot calcular el cost a partir d'un preu quan la font
és una entitat, no una estadística externa. Per això, si indiques un preu a les
opcions, la integració crea l'estadística `aigues_vic:cost_<contracte>`
(«<contracte> Cost de l'aigua»), en la moneda configurada a Home Assistant: el
consum de cada hora multiplicat pel preu. Al panell, en la font d'aigua, tria
«Utilitza una entitat que fa el seguiment del cost total» i selecciona aquesta
estadística.

És un preu únic: en canviar-lo es recalcula el cost de tot l'històric. Fes
servir la part variable de la factura (aigua, cànon i clavegueram per m³); les
quotes fixes i els trams no s'hi inclouen. Amb preu 0 no es crea ni s'actualitza
l'estadística; la que ja existeixi es manté fins que l'esborris des d'*Eines de
desenvolupament → Estadístiques*.

### Tornar a importar

L'acció **Reimporta les estadístiques** (`aigues_vic.reimport`) torna a llegir
del portal les dades de tots els contractes d'un compte des de la data indicada
fins ahir, i recalcula les sumes a partir d'aquell dia. És útil si el portal
corregeix dades més antigues que els 14 dies que es revisen cada dia. Sense
`start_date`, torna a importar tot l'històric que tingui el portal.

```yaml
action: aigues_vic.reimport
data:
  config_entry_id: <id de la integració>
  start_date: "2026-09-01"
```

## Eliminació

Elimina la integració des de *Dispositius i serveis*. Se n'esborra l'estat
intern d'importació i es tanca la sessió del portal. Les estadístiques a llarg
termini es conserven; es poden esborrar a *Eines de desenvolupament →
Estadístiques*. Si es va instal·lar a mà, elimina també
`custom_components/aigues_vic/` i reinicia.

## Desenvolupament

```sh
python3.14 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m mypy
.venv/bin/python -m pytest
```

Les dependències directes de desenvolupament estan fixades a
`requirements-dev.txt`. La integració no té dependències d'execució externes:
només utilitza les biblioteques i components estàndard de Home Assistant (`aiohttp`, etc.).

Les proves arrenquen Home Assistant amb `pytest-homeassistant-custom-component`
i un portal simulat (`tests/fake_portal.py`) que reprodueix l'API REST del portal
amb dades sintètiques: emissió i refresc de tokens JWT, llista de contractes amb l'estat
`ALTA`/`BAIXA`, contractes inactius, paginació i retorn de lectures i consums horaris i diaris
en format JSON.

GitHub Actions inclou comprovacions locals equivalents, Hassfest i validació
HACS. Els workflows s'executen al remot després de cada push. La guia és la
[Quality Scale](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/);
aquest projecte no té cap certificació oficial.

## Mostres necessàries i privacitat

Les credencials es desen a la configuració de Home Assistant i només s'envien
a l'API oficial (`api-oficina.aiguesvic.com`). Per analitzar canvis del portal poden caldre
captures noves, que es poden desar temporalment a `local/` (Git l'ignora).
Les proves només usen dades sintètiques.

- No comparteixis contrasenyes, tokens JWT, cookies ni capçaleres d'autorització.
- Elimina noms, correus, adreces, DNI/NIF, dades bancàries i identificadors
  personals; substitueix-los per valors sintètics consistents entre mostres.
- No adjuntis HAR complets: poden contenir sessions i dades personals.
- Conserva l'estructura, els formats JSON i les relacions entre dades.
- Si existeixen CAPTCHA o MFA, caldrà estudiar un accés compatible, no eludir-los.

Per anonimitzar captures HAR del portal, desa-les a `local/` i executa:

```sh
.venv/bin/python scripts/anonymize_har.py
```

L'script només usa la biblioteca estàndard. Escriu `local/anonymized/*.anon.har`
i fa el següent:

- Descarta les peticions a tercers i els cossos estàtics o binaris.
- Redacta galetes, capçaleres sensibles, tokens i credencials.
- Substitueix contractes, comptadors, factures i UUID per pseudònims
  consistents dins de cada execució.
- Substitueix noms, adreces i àlies per marcadors.

Si després de substituir troba algun valor original, no escriu res i acaba
amb error. Revisa igualment els fitxers abans de compartir-los.

L'anàlisi del portal és a [docs/portal-analysis.md](docs/portal-analysis.md) i
els passos que depenen de tu són a [docs/user-steps.md](docs/user-steps.md).

## Llicència i atribució

[MIT](LICENSE). Projecte no afiliat a AVSA ni a Home Assistant.
La icona és un disseny original del projecte, no el logotip d'AVSA.
La font editable és [assets/icon.svg](assets/icon.svg). Per regenerar el PNG
amb ImageMagick (opcional, no necessari per executar les proves):

```sh
magick -background none assets/icon.svg -strip PNG32:custom_components/aigues_vic/brand/icon.png
```
