# Passos de validació de l'usuari

[English](user-steps.en.md)

Aquesta llista de verificació permet comprovar el funcionament de la integració d'Aigües de Vic en una instal·lació real de Home Assistant. Marca els passos a mesura que els completis.

## 1. Requisits previs

- [ ] Disposar d'un compte actiu a la nova [oficina virtual d'Aigües de Vic](https://oficina.aiguesvic.com) amb el correu electrònic i la contrasenya validats.
- [ ] Tenir Home Assistant actualitzat a la versió **2026.10.0** o posterior amb el component `recorder` actiu (activat per defecte).

## 2. Instal·lació

Tria una de les dues opcions:

- [ ] **Opció A (HACS - Recomanada):**
  1. A Home Assistant, obre **HACS → Integracions**.
  2. Al menú de tres punts (⋮), selecciona **Repositoris personalitzats**.
  3. Afegeix `https://github.com/cguadall/ha-aigues-vic-portal` amb la categoria **Integration**.
  4. Cerca «Aigües Vic», instal·la la versió **v0.3.1** i reinicia Home Assistant.
- [ ] **Opció B (Manual):**
  1. Copia la carpeta `custom_components/aigues_vic/` a la carpeta `config/custom_components/` de Home Assistant.
  2. Reinicia Home Assistant.

## 3. Prova i Configuració

- [ ] Obre **Configuració → Dispositius i serveis → Afegeix integració** i cerca **Aigües Vic**.
- [ ] Prova primer d'introduir una contrasenya incorrecta: ha de mostrar «El correu o la contrasenya no són correctes».
- [ ] Introdueix les credencials correctes: la configuració s'ha de completar correctament i crear un dispositiu per a cada contracte actiu (`ALTA`).
- [ ] Comprova que els contractes donats de baixa no tenen dispositiu ni entitats actives.
- [ ] Comprova que els sensors mostren les dades correctes:
  - **Índex del comptador** (m³)
  - **Data de lectura**
  - **Consum del darrer dia** (L)
  - **Darrer consum facturat** (m³)
  - **Consum mínim nocturn** (L/h) i **Possible fuita** (si el comptador té telelectura horària)
- [ ] Prem el botó **Sincronitza ara** des del dispositiu i verifica als registres que es fa una actualització sense errors.

## 4. Panell d'Energia i Aigua

- [ ] Ves a **Configuració → Taulers de control → Energia → Consum d'aigua**.
- [ ] Afegeix l'estadística `aigues_vic:water_<contracte>` («<contracte> Consum d'aigua»).
  *(Recorda que l'índex del comptador és només la lectura acumulada; no s'ha d'afegir directament al panell).*
- [ ] Opcionalment, a les opcions de la integració, activa «Importa tot l'històric de consums» per reconstruir l'històric complet des del primer registre disponible al portal.
- [ ] Comprova l'endemà que l'actualització automàtica diària s'executa correctament entre les 06:00 i les 06:59.
