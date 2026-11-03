# Anàlisi de l'oficina virtual d'Aigües Vic

[English](portal-analysis.en.md)

Anàlisi de l'oficina virtual i de l'API REST d'Aigües de Vic (AVSA), utilitzada per la integració de Home Assistant per obtenir consums i lectures de comptadors.

## Arquitectura

- **Portal Web (SPA):** `https://oficina.aiguesvic.com`
- **Host de l'API REST:** `https://api-oficina.aiguesvic.com`
- **Format:** JSON UTF-8
- **Autenticació:** JWT (JSON Web Tokens) mitjançant la capçalera estàndard `Authorization: Bearer <access_token>`

## Autenticació i Gestió de Sessió

L'autenticació es gestiona directament contra l'API REST mitjançant parells de tokens JWT d'accés i de refresc:

```text
POST /accounts/auth/login/       Obtenció de tokens d'accés i de refresc
POST /accounts/refresh-token/    Renovació del token d'accés caducat
POST /accounts/logout/           Tancament de sessió i invalidació del token
```

### 1. Inici de sessió (`POST /accounts/auth/login/`)
Envia les credencials en format JSON:
```json
{
  "email": "usuari@domini.cat",
  "password": "contrasenya_secreta"
}
```
Retorna un codi HTTP 200 amb el parell de tokens:
```json
{
  "access": "eyJhbGciOi...",
  "refresh": "eyJhbGciOi..."
}
```
Si les credencials són incorrectes, l'API respon amb HTTP 401:
```json
{
  "detail": "No s'ha trobat cap compte actiu amb les credencials proporcionades."
}
```

### 2. Renovació de token (`POST /accounts/refresh-token/`)
Quan el token d'accés expira, qualsevol petició retorna un codi HTTP 401. El client de la integració renova el token de manera transparent sense necessitat de tornar a enviar la contrasenya:
```json
{
  "refresh": "eyJhbGciOi..."
}
```
Retorna un nou token d'accés:
```json
{
  "access": "eyJhbGciOi..."
}
```

### 3. Tancament de sessió (`POST /accounts/logout/`)
Invalida el token de refresc al servidor:
```json
{
  "refresh": "eyJhbGciOi..."
}
```

## Endpoints de Dades

Totes les peticions a l'API requereixen la capçalera:
```http
Authorization: Bearer <access_token>
```

### Llista de contractes (`GET /contracts/`)
Retorna tots els contractes associats al compte de l'usuari:
```json
[
  {
    "uuid": "4bf0e4b8-2a12-42c2-80dc-3ec4db5f9a65",
    "contract_number": "100000001",
    "alias": "Casa",
    "address": "CARRER MAJOR 1, 08500 VIC",
    "cups": "ES0000000000000000AA",
    "contract_status": {
      "code": "ACTIVE",
      "description": "Alta"
    }
  }
]
```
- La integració només processa els contractes amb estat `ACTIVE`.
- `contract_number` s'utilitza com a identificador públic d'entitats i d'estadístiques a Home Assistant (`aigues_vic:water_<contract_id>`).
- `uuid` s'utilitza internament per a les consultes posteriors a l'API.

### Dades tècniques i facturació (`GET /contracts/billing-data/{uuid}/`)
Retorna les dades tècniques del subministrament:
```json
{
  "holder_name": "NOM COGNOM COGNOM",
  "meter_serial_number": "A12BC34567D",
  "caliber": 15,
  "payment_method": "DOMICILIACIO"
}
```

### Consums diaris (`GET /consumptions/daily-consumptions/`)
Paràmetres per query string:
- `contract_uuid`: Identificador UUID del contracte.
- `initial_date`: Data d'inici en format `YYYY-MM-DD`.
- `end_date`: Data final en format `YYYY-MM-DD`.

Exemple de resposta:
```json
[
  {
    "timestamp": "2026-10-30T23:00:00",
    "consumption": 235,
    "reading": 1014.52
  }
]
```
- `consumption`: Consum en litres consumits durant el dia.
- `reading`: Lectura acumulada de l'índex del comptador en m³ (pot contenir decimals).
- `timestamp`: Marca horària local corresponent al final de la jornada.

### Consums horaris (`GET /consumptions/hourly-consumptions/`)
Paràmetres per query string:
- `contract_uuid`: Identificador UUID del contracte.
- `initial_date`: Data d'inici en format `YYYY-MM-DD`.
- `end_date`: Data final en format `YYYY-MM-DD`.

Exemple de resposta:
```json
[
  {
    "timestamp": "2026-10-30T00:00:00",
    "consumption": 12.5
  },
  {
    "timestamp": "2026-10-30T01:00:00",
    "consumption": 0.0
  }
]
```
- `consumption`: Litres consumits durant l'interval d'una hora.
- `timestamp`: Data i hora local en format ISO sense zona horària (`YYYY-MM-DDTHH:MM:SS`).
- Suporta dies de 25 hores pel canvi d'horari d'hivern d'octubre (DST fallback).

### Consums facturats (`GET /consumptions/billed-consumptions/`)
Paràmetres per query string:
- `contract_uuid`: Identificador UUID del contracte.

Exemple de resposta:
```json
[
  {
    "initial_date": "2026-07-01",
    "end_date": "2026-09-30",
    "consumption": 21,
    "reading": 1014
  }
]
```
- `consumption`: Consum total facturat en m³.
- `reading`: Índex del comptador en m³ a la data final del període.

## Disseny de la Integració amb Home Assistant

1. **Persistència i compatibilitat d'identificadors:**
   - La integració mai utilitza els UUID interns de l'API com a clau d'entitat per evitar trencar les dades històriques emmagatzemades a la base de dades del gravador (`recorder`) de Home Assistant. Sempre s'utilitza el número de contracte comercial (`contract_number`).
2. **Consultes per trams (chunking):**
   - Per evitar sol·licituds que superin el temps d'espera del servidor, les consultes d'històric es realitzen en trams de 31 dies per a les dades horàries i de 92 dies per a les diàries.
3. **Optimització de xarxa i cau:**
   - En reiniciar Home Assistant, la integració reutilitza la memòria cau desada si les dades són posteriors a l'última actualització programada, evitant consultes innecessàries al portal.
4. **Resiliència davant d'errors:**
   - Si una crida retorna HTTP 401, el client renova el token mitjançant `/accounts/refresh-token/` i reintenta la petició de manera transparent.
   - En cas d'errors de xarxa o caigudes temporals del servei, la integració programa reintents automàtics amb retrocés exponencial (30 min, 1 h, 2 h, 4 h) i conserva els valors de l'última sincronització correcta.
