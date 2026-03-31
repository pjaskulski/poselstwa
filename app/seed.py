from __future__ import annotations

import sqlite3
from pathlib import Path
import uuid

from werkzeug.security import generate_password_hash

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / 'instance' / 'poselstwa.sqlite'
SCHEMA_PATH = BASE_DIR / 'doc' / 'schemat_bazy.sql'


def u() -> str:
    return str(uuid.uuid4())


def insert(cur, sql: str, params: tuple):
    cur.execute(sql, params)
    return cur.lastrowid


def main() -> None:
    DB_PATH.parent.mkdir(exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = sqlite3.connect(DB_PATH)
    conn.execute('PRAGMA foreign_keys = ON')
    conn.executescript(SCHEMA_PATH.read_text(encoding='utf-8'))
    cur = conn.cursor()

    user_id = insert(cur, 'INSERT INTO app_user (uuid, username, password_hash, display_name) VALUES (?, ?, ?, ?)', (u(), 'historyk', generate_password_hash('demo'), 'Badacz demonstracyjny'))

    dates = {}
    for key, kind, start, end, label in [
        ('1467', 'exact', '1467-01-01', '1467-12-31', '1467'),
        ('1471', 'exact', '1471-01-01', '1471-12-31', '1471'),
        ('1492-04', 'exact', '1492-04-01', '1492-04-30', 'kwiecień 1492'),
        ('1492-05-14', 'exact', '1492-05-14', '1492-05-14', '14 V 1492'),
        ('1492-05-20', 'exact', '1492-05-20', '1492-05-20', '20 V 1492'),
        ('1492-06', 'exact', '1492-06-01', '1492-06-30', 'czerwiec 1492'),
        ('1492-08', 'exact', '1492-08-01', '1492-08-31', 'sierpień 1492'),
        ('1493', 'exact', '1493-01-01', '1493-12-31', '1493'),
        ('1450-1460', 'circa', '1450-01-01', '1460-12-31', 'ok. 1450–1460'),
        ('1460-1470', 'circa', '1460-01-01', '1470-12-31', '1460–1470'),
    ]:
        dates[key] = insert(cur, '''INSERT INTO historical_date
            (uuid, date_kind, start_date_iso, end_date_iso, display_label, sort_key_start, sort_key_end)
            VALUES (?, ?, ?, ?, ?, ?, ?)''', (u(), kind, start, end, label, start, end))

    p1 = insert(cur, '''INSERT INTO person
        (uuid, canonical_name, display_name, birth_date_id, education_note, activity_note, general_biographical_note, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
        (u(), 'Mikołaj z Mirzyńca', 'Mikołaj z Mirzyńca', dates['1450-1460'], 'Studia prawnicze, przygotowanie kancelaryjne.', 'Dyplomata i pośrednik w kontaktach z Kurią Rzymską.', 'Postać demonstracyjna dla prototypu.', user_id))
    p2 = insert(cur, '''INSERT INTO person
        (uuid, canonical_name, display_name, education_note, activity_note, general_biographical_note, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?)''',
        (u(), 'Jan Lubrański', 'Jan Lubrański', 'Wykształcenie humanistyczne i prawnicze.', 'Duchowny i uczestnik misji dyplomatycznych.', 'Przykładowy rekord osoby z wieloma rolami.', user_id))

    for person_id, variant, lang, primary in [
        (p1, 'Nicolaus de Mirzyniec', 'la', 1),
        (p1, 'Mikołaj Mirzyniecki', 'pl', 0),
        (p2, 'Ioannes Lubranski', 'la', 1),
    ]:
        insert(cur, 'INSERT INTO person_name_variant (uuid, person_id, variant_text, language_code, is_primary) VALUES (?, ?, ?, ?, ?)', (u(), person_id, variant, lang, primary))

    b1 = insert(cur, 'INSERT INTO bibliography_item (uuid, short_citation, full_citation, author_text, title, publication_year, note) VALUES (?, ?, ?, ?, ?, ?, ?)',
        (u(), 'Kowalski 2024', 'Jan Kowalski, Poselstwa polskie do Rzymu w XV wieku, Warszawa 2024.', 'Jan Kowalski', 'Poselstwa polskie do Rzymu w XV wieku', '2024', 'Publikacja demonstracyjna.'))
    b2 = insert(cur, 'INSERT INTO bibliography_item (uuid, short_citation, full_citation, author_text, title, publication_year, note) VALUES (?, ?, ?, ?, ?, ?, ?)',
        (u(), 'Acta Romana', 'Acta Romana, t. 12, wyd. demonstracyjne.', 'red. A. Editor', 'Acta Romana', '1898', 'Przykładowa edycja źródłowa.'))

    insert(cur, 'INSERT INTO biography_note (uuid, person_id, bibliography_item_id, footnote_text, biography_text, sort_order) VALUES (?, ?, ?, ?, ?, ?)',
        (u(), p1, b1, 'Kowalski 2024, s. 55.', 'Krótki biogram osoby, używany na karcie osoby jako osobny rekord z przypisem.', 1))
    insert(cur, 'INSERT INTO biography_note (uuid, person_id, footnote_text, biography_text, sort_order) VALUES (?, ?, ?, ?, ?)',
        (u(), p1, 'Notatka robocza redakcji.', 'Drugi biogram o charakterze roboczym, pokazujący możliwość wielokrotnego opisu.', 2))

    insert(cur, 'INSERT INTO office_term (uuid, person_id, office_name, office_type, function_name, source_designation, start_date_id, end_date_id, bibliography_item_id, comment) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (u(), p1, 'notariusz królewski', 'urząd świecki', 'obsługa kancelarii', 'notarius regis', dates['1460-1470'], dates['1471'], b1, 'Przykład ogólnej funkcji pełnionej w czasie.'))
    insert(cur, 'INSERT INTO office_term (uuid, person_id, office_name, office_type, function_name, source_designation, start_date_id, end_date_id, bibliography_item_id, comment) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (u(), p2, 'biskup poznański', 'urząd kościelny', 'delegat kościelny', 'episcopus Posnaniensis', dates['1492-04'], dates['1493'], b1, 'Drugi rekord urzędu.'))

    ref1 = insert(cur, 'INSERT INTO reference_mention (uuid, archive_signature, mention_date_id, year_label, abstract_text, topic_text, description_text, source_type, text_excerpt) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (u(), 'ASV, Reg. Vat. demo 12', dates['1492-05-14'], '1492', 'Wzmianka o przybyciu posłów.', 'przybycie do Rzymu', 'Krótki opis pojedynczego świadectwa źródłowego.', 'list', 'Nicolaus orator regis Poloniae ad curiam venit.'))

    insert(cur, 'INSERT INTO curia_presence (uuid, person_id, start_date_id, end_date_id, year_label, place_name, presence_type, mention_type, office_at_curia, reference_mention_id, scholarly_comment) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (u(), p1, dates['1492-05-14'], dates['1492-06'], '1492', 'Rzym', 'pobyt dyplomatyczny', 'wzmianka źródłowa', 'orator regis Poloniae', ref1, 'Pobyt związany z misją demonstracyjną.'))

    e1 = insert(cur, '''INSERT INTO embassy
        (uuid, title, year_label, appointment_date_id, arrival_in_rome_date_id, audience_date_id, departure_from_rome_date_id, return_to_poland_date_id, mission_subject, description_text, notes_text, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (u(), 'Poselstwo do Aleksandra VI', '1492', dates['1492-04'], dates['1492-05-14'], dates['1492-05-20'], dates['1492-06'], dates['1492-08'], 'obediencja i negocjacje polityczne', 'Przykładowa misja pokazująca komplet sekcji na karcie poselstwa.', 'Wersja demonstracyjna danych.', user_id))
    e2 = insert(cur, '''INSERT INTO embassy
        (uuid, title, year_label, mission_subject, description_text, created_by)
        VALUES (?, ?, ?, ?, ?, ?)''',
        (u(), 'Misja informacyjna do Kurii', '1471', 'sprawy beneficjalne', 'Drugi rekord dla listy poselstw i filtrowania.', user_id))

    insert(cur, 'INSERT INTO embassy_participant (uuid, embassy_id, person_id, role_in_embassy, participant_category, rank_order, office_during_embassy, function_during_embassy, source_designation_latin, source_designation_polish, representation_note, comment) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (u(), e1, p1, 'poseł główny', 'dyplomata', 1, 'notariusz królewski', 'negocjator', 'orator regis Poloniae', 'orator króla polskiego', 'Manifestacja osoby w ramach konkretnego poselstwa.', 'Łączy byt osoby z kontekstem misji.'))
    insert(cur, 'INSERT INTO embassy_participant (uuid, embassy_id, person_id, role_in_embassy, participant_category, rank_order, office_during_embassy, function_during_embassy, source_designation_latin, source_designation_polish, representation_note, comment) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (u(), e1, p2, 'uczestnik duchowny', 'duchowieństwo', 2, 'biskup poznański', 'doradca', 'cubicularius papae', 'szambelan papieski', 'Przykład drugiej równoległej deskrypcji źródłowej.', 'Pokazuje różnicę między urzędem trwałym a rolą kontekstową.'))
    insert(cur, 'INSERT INTO embassy_participant (uuid, embassy_id, person_id, role_in_embassy, participant_category, rank_order, office_during_embassy, function_during_embassy, source_designation_latin, source_designation_polish) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (u(), e2, p1, 'wysłannik', 'dyplomata', 1, 'notariusz królewski', 'reprezentant', 'nuntius', 'wysłannik'))

    insert(cur, 'INSERT INTO embassy_bibliography (uuid, embassy_id, bibliography_item_id, comment) VALUES (?, ?, ?, ?)',
        (u(), e1, b1, 'Opracowanie syntetyczne.'))
    insert(cur, 'INSERT INTO embassy_bibliography (uuid, embassy_id, bibliography_item_id, comment) VALUES (?, ?, ?, ?)',
        (u(), e1, b2, 'Edycja źródłowa wykorzystana w module źródeł.'))

    insert(cur, 'INSERT INTO source_type_dictionary (uuid, value, label, sort_order, is_active) VALUES (?, ?, ?, ?, ?)',
        (u(), 'mowa', 'mowa', 10, 1))

    st1 = insert(cur, '''INSERT INTO source_text
        (uuid, embassy_id, bibliography_item_id, source_type, archive_signature, edition_label, source_date_id, original_text_full, polish_text_full, editorial_note)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (u(), e1, b2, 'mowa', 'ASV, Reg. Vat. demo 12', 'Acta Romana, t. 12', dates['1492-05-20'],
         'Sanctissime Pater.\nNicolaus orator regis Poloniae dona obtulit.\nFidem et obedientiam promisit.',
         'Ojcze Święty.\nMikołaj, poseł króla polskiego, ofiarował dary.\nZapewnił o wierze i posłuszeństwie.',
         'Tekst zsegmentowany akapitami dla widoku równoległego.'))

    seg1 = insert(cur, 'INSERT INTO source_segment (uuid, source_text_id, segment_no, original_segment, polish_segment, alignment_group, comment) VALUES (?, ?, ?, ?, ?, ?, ?)',
        (u(), st1, 1, 'Sanctissime Pater.', 'Ojcze Święty.', 'A', 'Incipit mowy.'))
    seg2 = insert(cur, 'INSERT INTO source_segment (uuid, source_text_id, segment_no, original_segment, polish_segment, alignment_group, comment) VALUES (?, ?, ?, ?, ?, ?, ?)',
        (u(), st1, 2, 'Nicolaus orator regis Poloniae dona obtulit.', 'Mikołaj, poseł króla polskiego, ofiarował dary.', 'B', 'Fragment o darach.'))
    seg3 = insert(cur, 'INSERT INTO source_segment (uuid, source_text_id, segment_no, original_segment, polish_segment, alignment_group, comment) VALUES (?, ?, ?, ?, ?, ?, ?)',
        (u(), st1, 3, 'Fidem et obedientiam promisit.', 'Zapewnił o wierze i posłuszeństwie.', 'C', 'Fragment o wierze.'))

    th1 = insert(cur, 'INSERT INTO theme (uuid, name, slug, description_text, color_code) VALUES (?, ?, ?, ?, ?)',
        (u(), 'Dary', 'dary', 'Motyw wymiany darów i znaków łaski.', '#2f855a'))
    th2 = insert(cur, 'INSERT INTO theme (uuid, name, slug, description_text, color_code) VALUES (?, ?, ?, ?, ?)',
        (u(), 'Obrona wiary', 'obrona-wiary', 'Motyw wyznania wiary i posłuszeństwa.', '#1d4ed8'))
    th3 = insert(cur, 'INSERT INTO theme (uuid, name, slug, description_text, color_code) VALUES (?, ?, ?, ?, ?)',
        (u(), 'Obediencja', 'obediencja', 'Motyw uznania autorytetu papieskiego.', '#7c3aed'))

    insert(cur, 'INSERT INTO theme_annotation (uuid, theme_id, source_text_id, source_segment_id, text_language_code, char_start, char_end, annotated_text_snapshot, comment, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (u(), th1, st1, seg2, 'pl', 36, 50, 'ofiarował dary', 'Przykładowa adnotacja motywu na fragmencie przekładu.', user_id))
    insert(cur, 'INSERT INTO theme_annotation (uuid, theme_id, source_text_id, source_segment_id, text_language_code, char_start, char_end, annotated_text_snapshot, comment, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (u(), th2, st1, seg3, 'pl', 12, 31, 'wierze i posłuszeństwie', 'Drugi motyw dla innego segmentu.', user_id))
    insert(cur, 'INSERT INTO theme_annotation (uuid, theme_id, source_text_id, source_segment_id, text_language_code, char_start, char_end, annotated_text_snapshot, comment, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (u(), th3, st1, seg1, 'la', 0, 16, 'Sanctissime Pater', 'Przykład adnotacji w tekście oryginalnym.', user_id))

    conn.commit()
    conn.close()
    print(f'Utworzono bazę demonstracyjną: {DB_PATH}')


if __name__ == '__main__':
    main()
