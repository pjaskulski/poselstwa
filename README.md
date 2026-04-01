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

## Wdrożenie na Ubuntu z gunicorn i nginx

Repozytorium zawiera przykładowe pliki wdrożeniowe:

- [deploy/poselstwa.service](deploy/poselstwa.service)
- [deploy/nginx-poselstwa.conf](deploy/nginx-poselstwa.conf)

Zalecany wariant:

1. sklonować repozytorium do katalogu aplikacji, np. `/srv/poselstwa`,
2. utworzyć osobne środowisko `venv` i zainstalować zależności,
3. skonfigurować `systemd` dla `gunicorn`,
4. wystawić aplikację przez `nginx` pod prefiksem `/poselstwa/`.

Przykład:

```bash
cd /srv
git clone <repozytorium> poselstwa
cd poselstwa
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python app/seed.py
```

Następnie:

- dopasować ścieżki i użytkownika w `deploy/poselstwa.service`,
- skopiować plik do `/etc/systemd/system/poselstwa.service`,
- ustawić własny `POSELSTWA_SECRET_KEY`,
- włączyć usługę:

```bash
sudo systemctl daemon-reload
sudo systemctl enable poselstwa
sudo systemctl start poselstwa
```

W konfiguracji `nginx` należy użyć bloku z `deploy/nginx-poselstwa.conf`.
Obecna aplikacja jest już przygotowana do pracy za reverse proxy z nagłówkiem:

```text
X-Forwarded-Prefix: /poselstwa
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

![screeenshot](doc/11a.png)

![screeenshot](doc/12.png)

![screeenshot](doc/12a.png)

![screeenshot](doc/13.png)

![screeenshot](doc/14.png)

![screeenshot](doc/15.png)

![screeenshot](doc/16.png)

![screeenshot](doc/17.png)

![screeenshot](doc/18.png)

![screeenshot](doc/19.png)

![screeenshot](doc/20.png)

Generowanie wersji pdf instrukcji użytkownika:
`pandoc manual.md -o manual.pdf --pdf-engine=xelatex -V mainfont="DejaVu Serif" -V monofont="DejaVu Sans Mono"`
