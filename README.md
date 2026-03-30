# LegationesAdVaticanum — prototyp aplikacji Flask/SQLite

Prototyp aplikacji bazodanowej do badań historii dyplomatycznej Polski i Stolicy Apostolskiej w XV wieku.

## Zakres prototypu

Aplikacja obejmuje:

- moduł **Osoby** z listą rekordów, filtrowaniem i kartą osoby,
- moduł **Poselstwa** z listą, metadanymi, uczestnikami, literaturą i źródłami,
- moduł **Motywy** z kartą motywu i listą oznaczonych fragmentów,
- eksport CSV dla list osób i poselstw,


## Uruchomienie

```bash
cd prototyp_poselstwa
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app/seed.py
python run.py
```

Następnie należy otworzyć adres:

```text
http://127.0.0.1:5000/
```

## Uwagi

To jest **prototyp**, nie pełna aplikacja produkcyjna.

Projekt przygotowywany z użyciem AI (Codex 5.4)

## Zrzuty ekranu


![screeenshot](doc/01.png)

![screeenshot](doc/02.png)

![screeenshot](doc/03.png)

![screeenshot](doc/04.png)

![screeenshot](doc/05.png)

![screeenshot](doc/06.png)

![screeenshot](doc/07.png)

![screeenshot](doc/08.png)

![screeenshot](doc/09.png)

![screeenshot](doc/10.png)

![screeenshot](doc/11.png)

![screeenshot](doc/12.png)

![screeenshot](doc/12a.png)

![screeenshot](doc/13.png)

![screeenshot](doc/14.png)

![screeenshot](doc/15.png)

![screeenshot](doc/16.png)

![screeenshot](doc/17.png)

![screeenshot](doc/18.png)

![screeenshot](doc/19.png)

