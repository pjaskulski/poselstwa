from __future__ import annotations

import csv
import io
import os
import re
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from flask import Flask, abort, flash, g, jsonify, redirect, render_template, request, send_file, url_for
from markupsafe import Markup, escape

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / 'instance' / 'poselstwa.sqlite'
SCHEMA_PATH = BASE_DIR / 'doc' / 'schemat_bazy.sql'


def create_app() -> Flask:
    app = Flask(__name__)
    app.config['DATABASE'] = str(DB_PATH)
    app.config['SECRET_KEY'] = 'poselstwa-dev'

    (BASE_DIR / 'instance').mkdir(exist_ok=True)

    @app.before_request
    def before_request() -> None:
        g.db = get_db(app)
        ensure_parameter_tables(g.db)
        ensure_historical_date_compatibility(g.db)
        ensure_bibliography_item_compatibility(g.db)
        ensure_embassy_bibliography_compatibility(g.db)
        ensure_biography_note_compatibility(g.db)

    @app.teardown_request
    def teardown_request(exception: Exception | None) -> None:
        db = g.pop('db', None)
        if db is not None:
            db.close()

    @app.context_processor
    def utility_processor() -> dict[str, Any]:
        def static_asset(filename: str) -> str:
            asset_path = BASE_DIR / 'app' / 'static' / filename
            version = int(os.path.getmtime(asset_path)) if asset_path.exists() else 0
            return url_for('static', filename=filename, v=version)

        return {
            'render_date': render_date,
            'date_label': date_label,
            'prefixed_date_label': prefixed_date_label,
            'render_segment_with_annotations': render_segment_with_annotations,
            'static_asset': static_asset,
        }

    @app.route('/')
    def index():
        db = g.db
        stats = {
            'persons': db.execute('SELECT COUNT(*) FROM person').fetchone()[0],
            'embassies': db.execute('SELECT COUNT(*) FROM embassy').fetchone()[0],
            'themes': db.execute('SELECT COUNT(*) FROM theme WHERE is_active = 1').fetchone()[0],
            'sources': db.execute('SELECT COUNT(*) FROM source_text').fetchone()[0],
        }
        recent_embassies = db.execute(
            '''
            SELECT id, title, year_label
            FROM embassy
            ORDER BY COALESCE(year_label, '') DESC, id DESC
            LIMIT 5
            '''
        ).fetchall()
        recent_people = db.execute(
            '''
            SELECT id, canonical_name, display_name
            FROM person
            ORDER BY updated_at DESC, canonical_name ASC
            LIMIT 5
            '''
        ).fetchall()
        return render_template(
            'index.html',
            stats=stats,
            recent_embassies=recent_embassies,
            recent_people=recent_people,
        )

    @app.route('/dates')
    def dates_list():
        q = request.args.get('q', '').strip()
        date_kind = request.args.get('date_kind', '').strip()
        clauses = []
        params: list[Any] = []
        if q:
            clauses.append('(display_label LIKE ? OR comment LIKE ? OR start_date_iso LIKE ? OR end_date_iso LIKE ?)')
            like = f'%{q}%'
            params.extend([like, like, like, like])
        if date_kind:
            clauses.append('date_kind = ?')
            params.append(date_kind)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ''
        rows = g.db.execute(
            f'''
            SELECT *
            FROM historical_date
            {where}
            ORDER BY COALESCE(sort_key_start, start_date_iso, ''), id
            ''',
            params,
        ).fetchall()
        date_kinds = [r[0] for r in g.db.execute('SELECT DISTINCT date_kind FROM historical_date ORDER BY 1').fetchall()]
        return render_template('dates_list.html', rows=rows, q=q, date_kind=date_kind, date_kinds=date_kinds)

    @app.route('/dates/new', methods=['GET', 'POST'])
    def historical_date_create():
        form_data = empty_historical_date_form()
        if request.method == 'POST':
            form_data = historical_date_form_data_from_mapping(request.form)
            errors = validate_historical_date_form(form_data)
            if not errors:
                date_id = insert_historical_date(g.db, form_data)
                flash('Dodano datę historyczną.', 'success')
                return redirect(url_for('historical_date_edit', date_id=date_id))
            for error in errors:
                flash(error, 'error')
        return render_template(
            'historical_date_form.html',
            form_title='Data historyczna',
            submit_label='Dodaj datę',
            form_action=url_for('historical_date_create'),
            form_data=form_data,
            date_kind_options=historical_date_kind_options(),
            certainty_options=historical_date_certainty_options(),
            date_record=None,
        )

    @app.route('/dates/<int:date_id>/edit', methods=['GET', 'POST'])
    def historical_date_edit(date_id: int):
        date_record = g.db.execute('SELECT * FROM historical_date WHERE id = ?', (date_id,)).fetchone()
        if not date_record:
            abort(404)
        if request.method == 'POST':
            form_data = historical_date_form_data_from_mapping(request.form)
            errors = validate_historical_date_form(form_data)
            if not errors:
                update_historical_date(g.db, date_id, form_data)
                flash('Zapisano zmiany daty historycznej.', 'success')
                return redirect(url_for('historical_date_edit', date_id=date_id))
            for error in errors:
                flash(error, 'error')
        else:
            form_data = historical_date_form_data_from_row(date_record)
        return render_template(
            'historical_date_form.html',
            form_title=f'Edycja daty historycznej: {date_record["display_label"]}',
            submit_label='Zapisz zmiany',
            form_action=url_for('historical_date_edit', date_id=date_id),
            form_data=form_data,
            date_kind_options=historical_date_kind_options(),
            certainty_options=historical_date_certainty_options(),
            date_record=date_record,
        )

    @app.route('/dates/quick-create', methods=['POST'])
    def historical_date_quick_create():
        payload = request.get_json(silent=True) or request.form
        form_data = historical_date_form_data_from_mapping(payload)
        errors = validate_historical_date_form(form_data)
        if errors:
            return jsonify({'ok': False, 'errors': errors}), 400
        date_id = insert_historical_date(g.db, form_data)
        return jsonify({
            'ok': True,
            'date': {
                'id': date_id,
                'display_label': form_data['display_label'],
                'date_kind': form_data['date_kind'],
                'certainty': form_data['certainty'],
            }
        })

    @app.route('/dates/<int:date_id>/json')
    def historical_date_json(date_id: int):
        row = g.db.execute('SELECT * FROM historical_date WHERE id = ?', (date_id,)).fetchone()
        if not row:
            return jsonify({'ok': False, 'errors': ['Nie znaleziono daty historycznej.']}), 404
        return jsonify({'ok': True, 'date': historical_date_form_data_from_row(row) | {'id': row['id']}})

    @app.route('/date-links/save', methods=['POST'])
    def date_link_save():
        payload = request.get_json(silent=True) or request.form
        target = validate_date_link_target(payload.get('entity_type', ''), payload.get('record_id', ''), payload.get('field_name', ''))
        if isinstance(target, tuple):
            entity_type, record_id, field_name = target
        else:
            return jsonify({'ok': False, 'errors': [target]}), 400

        form_data = historical_date_form_data_from_mapping(payload)
        errors = validate_historical_date_form(form_data)
        if errors:
            return jsonify({'ok': False, 'errors': errors}), 400

        current_date_id = parse_optional_int(str(payload.get('date_id', '')).strip())
        if current_date_id:
            usage_count = count_historical_date_usages(g.db, current_date_id)
            if usage_count > 1:
                date_id = insert_historical_date(g.db, form_data)
                set_linked_date(g.db, entity_type, record_id, field_name, date_id)
            else:
                update_historical_date(g.db, current_date_id, form_data)
                date_id = current_date_id
                set_linked_date(g.db, entity_type, record_id, field_name, date_id)
        else:
            date_id = insert_historical_date(g.db, form_data)
            set_linked_date(g.db, entity_type, record_id, field_name, date_id)

        return jsonify({
            'ok': True,
            'date': {
                'id': date_id,
                'display_label': date_label(date_id),
            }
        })

    @app.route('/date-links/clear', methods=['POST'])
    def date_link_clear():
        payload = request.get_json(silent=True) or request.form
        target = validate_date_link_target(payload.get('entity_type', ''), payload.get('record_id', ''), payload.get('field_name', ''))
        if isinstance(target, tuple):
            entity_type, record_id, field_name = target
        else:
            return jsonify({'ok': False, 'errors': [target]}), 400

        row = g.db.execute(
            f'SELECT {field_name} AS date_id FROM {DATE_LINK_TARGETS[entity_type]["table"]} WHERE id = ?',
            (record_id,),
        ).fetchone()
        if not row:
            return jsonify({'ok': False, 'errors': ['Nie znaleziono rekordu do odpięcia daty.']}), 404
        previous_date_id = row['date_id']
        set_linked_date(g.db, entity_type, record_id, field_name, None)
        if previous_date_id and count_historical_date_usages(g.db, previous_date_id) == 0:
            g.db.execute('DELETE FROM historical_date WHERE id = ?', (previous_date_id,))
            g.db.commit()
        return jsonify({'ok': True})

    @app.route('/persons')
    def persons_list():
        q = request.args.get('q', '').strip()
        date_field = request.args.get('date_field', 'birth').strip()
        date_from = request.args.get('date_from', '').strip()
        date_to = request.args.get('date_to', '').strip()
        sort = request.args.get('sort', 'name')
        direction = request.args.get('direction', 'asc')
        date_field_map = {
            'birth': 'p.birth_date_id',
            'death': 'p.death_date_id',
        }
        date_field = date_field if date_field in date_field_map else 'birth'
        order_map = {
            'name': 'p.canonical_name',
            'birth': 'hd.sort_key_start',
            'embassies': 'embassy_count',
        }
        sort = sort if sort in order_map else 'name'
        direction = 'desc' if direction == 'desc' else 'asc'
        order_expr = order_map[sort]
        if sort in {'birth'}:
            nulls = 'NULLS FIRST' if direction == 'asc' else 'NULLS LAST'
            order_by = f'{order_expr} {direction.upper()} {nulls}, p.canonical_name ASC'
        elif sort in {'embassies'}:
            order_by = f'{order_expr} {direction.upper()}, p.canonical_name ASC'
        else:
            order_by = f'{order_expr} {direction.upper()}'
        params: list[Any] = []
        clauses: list[str] = []
        if q:
            clauses.append(
                '''
            (
               p.canonical_name LIKE ?
               OR p.education_note LIKE ?
               OR p.activity_note LIKE ?
               OR EXISTS (
                   SELECT 1
                   FROM person_name_variant pnv
                   WHERE pnv.person_id = p.id
                     AND pnv.variant_text LIKE ?
               )
               OR EXISTS (
                   SELECT 1
                   FROM office_term ot_filter
                   WHERE ot_filter.person_id = p.id
                     AND ot_filter.office_name LIKE ?
               )
               OR EXISTS (
                   SELECT 1
                   FROM biography_note bn
                   WHERE bn.person_id = p.id
                     AND (
                       bn.biography_text LIKE ?
                       OR bn.footnote_text LIKE ?
                     )
               )
            )
            '''
            )
            like = f'%{q}%'
            params.extend([like, like, like, like, like, like, like])
        if date_from:
            clauses.append("COALESCE(fd.sort_key_end, fd.sort_key_start) >= ?")
            params.append(date_from)
        if date_to:
            clauses.append("fd.sort_key_start <= ?")
            params.append(date_to)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ''
        rows = g.db.execute(
            f'''
            SELECT p.id, p.canonical_name, p.display_name, p.education_note, p.activity_note,
                   hd.display_label AS birth_label,
                   hd.date_kind AS birth_kind,
                   (
                     SELECT GROUP_CONCAT(office_name, '; ')
                     FROM (
                       SELECT DISTINCT office_name
                       FROM office_term
                       WHERE person_id = p.id AND office_name IS NOT NULL AND office_name != ''
                       ORDER BY office_name
                     )
                   ) AS office_names,
                   COUNT(DISTINCT ep.id) AS embassy_count
            FROM person p
            LEFT JOIN historical_date hd ON hd.id = p.birth_date_id
            LEFT JOIN historical_date fd ON fd.id = {date_field_map[date_field]}
            LEFT JOIN embassy_participant ep ON ep.person_id = p.id
            {where}
            GROUP BY p.id
            ORDER BY {order_by}
            '''
        , params).fetchall()
        return render_template(
            'persons_list.html',
            rows=rows,
            q=q,
            sort=sort,
            direction=direction,
            date_field=date_field,
            date_from=date_from,
            date_to=date_to,
        )

    @app.route('/persons/export.csv')
    def persons_export():
        rows = g.db.execute(
            '''
            SELECT p.canonical_name, p.display_name, hd.display_label AS birth_label,
                   p.education_note, p.activity_note
            FROM person p
            LEFT JOIN historical_date hd ON hd.id = p.birth_date_id
            ORDER BY p.canonical_name ASC
            '''
        ).fetchall()
        return csv_response('osoby.csv', ['Nazwa kanoniczna', 'Nazwa wyświetlana', 'Data urodzenia', 'Wykształcenie', 'Działalność'], rows)

    @app.route('/persons/<int:person_id>')
    def person_detail(person_id: int):
        db = g.db
        person = db.execute(
            '''
            SELECT p.*, bd.display_label AS birth_label, dd.display_label AS death_label
                 , bd.date_kind AS birth_kind, dd.date_kind AS death_kind
            FROM person p
            LEFT JOIN historical_date bd ON bd.id = p.birth_date_id
            LEFT JOIN historical_date dd ON dd.id = p.death_date_id
            WHERE p.id = ?
            ''',
            (person_id,),
        ).fetchone()
        if not person:
            abort(404)
        variants = db.execute('SELECT * FROM person_name_variant WHERE person_id = ? ORDER BY is_primary DESC, id ASC', (person_id,)).fetchall()
        offices = db.execute(
            '''
            SELECT ot.*, sd.display_label AS start_label, ed.display_label AS end_label,
                   sd.date_kind AS start_kind, ed.date_kind AS end_kind
            FROM office_term ot
            LEFT JOIN historical_date sd ON sd.id = ot.start_date_id
            LEFT JOIN historical_date ed ON ed.id = ot.end_date_id
            WHERE ot.person_id = ?
            ORDER BY COALESCE(sd.sort_key_start, ''), ot.id
            ''',
            (person_id,),
        ).fetchall()
        presences = db.execute(
            '''
            SELECT cp.*, sd.display_label AS start_label, ed.display_label AS end_label,
                   sd.date_kind AS start_kind, ed.date_kind AS end_kind
            FROM curia_presence cp
            LEFT JOIN historical_date sd ON sd.id = cp.start_date_id
            LEFT JOIN historical_date ed ON ed.id = cp.end_date_id
            WHERE cp.person_id = ?
            ORDER BY COALESCE(sd.sort_key_start, ''), cp.id
            ''',
            (person_id,),
        ).fetchall()
        bios = db.execute(
            "SELECT * FROM biography_note WHERE person_id = ? ORDER BY COALESCE(footnote_text, ''), id ASC",
            (person_id,),
        ).fetchall()
        participations = db.execute(
            '''
            SELECT ep.*, e.title AS embassy_title, e.year_label
            FROM embassy_participant ep
            JOIN embassy e ON e.id = ep.embassy_id
            WHERE ep.person_id = ?
            ORDER BY e.year_label, e.id
            ''',
            (person_id,),
        ).fetchall()
        return render_template(
            'person_detail.html',
            person=person,
            variants=variants,
            offices=offices,
            presences=presences,
            bios=bios,
            participations=participations,
        )

    @app.route('/persons/<int:person_id>/delete', methods=['POST'])
    def person_delete(person_id: int):
        person = g.db.execute('SELECT id, canonical_name FROM person WHERE id = ?', (person_id,)).fetchone()
        if not person:
            abort(404)
        g.db.execute('DELETE FROM person WHERE id = ?', (person_id,))
        g.db.commit()
        flash(f'Usunięto osobę: {person["canonical_name"]}.', 'success')
        return redirect(url_for('persons_list'))

    @app.route('/persons/<int:person_id>/activity', methods=['POST'])
    def person_activity_update(person_id: int):
        person = g.db.execute('SELECT id FROM person WHERE id = ?', (person_id,)).fetchone()
        if not person:
            abort(404)
        activity_note = request.form.get('activity_note', '').strip()
        g.db.execute(
            '''
            UPDATE person
            SET activity_note = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            ''',
            (activity_note or None, person_id),
        )
        g.db.commit()
        flash('Zapisano pole działalności osoby.', 'success')
        return redirect(url_for('person_detail', person_id=person_id, tab='activity'))

    @app.route('/persons/new', methods=['GET', 'POST'])
    def person_create():
        form_data = empty_person_form()
        if request.method == 'POST':
            form_data = person_form_data_from_request()
            errors = validate_person_form(form_data)
            if not errors:
                person_id = insert_person(g.db, form_data)
                flash('Dodano nową osobę.', 'success')
                return redirect(url_for('person_detail', person_id=person_id))
            for error in errors:
                flash(error, 'error')
        return render_template(
            'person_form.html',
            form_title='Nowa osoba',
            submit_label='Dodaj osobę',
            form_action=url_for('person_create'),
            form_data=form_data,
            date_options=fetch_historical_dates(g.db),
            person=None,
        )

    @app.route('/persons/<int:person_id>/edit', methods=['GET', 'POST'])
    def person_edit(person_id: int):
        person = g.db.execute('SELECT * FROM person WHERE id = ?', (person_id,)).fetchone()
        if not person:
            abort(404)
        if request.method == 'POST':
            form_data = person_form_data_from_request()
            errors = validate_person_form(form_data)
            if not errors:
                update_person(g.db, person_id, form_data)
                flash('Zapisano zmiany w rekordzie osoby.', 'success')
                return redirect(url_for('person_detail', person_id=person_id))
            for error in errors:
                flash(error, 'error')
        else:
            form_data = person_form_data_from_row(person)
        return render_template(
            'person_form.html',
            form_title=f'Edycja osoby: {person["canonical_name"]}',
            submit_label='Zapisz zmiany',
            form_action=url_for('person_edit', person_id=person_id),
            form_data=form_data,
            date_options=fetch_historical_dates(g.db),
            person=person,
        )

    @app.route('/persons/<int:person_id>/offices/new', methods=['GET', 'POST'])
    def office_term_create(person_id: int):
        person = g.db.execute('SELECT * FROM person WHERE id = ?', (person_id,)).fetchone()
        if not person:
            abort(404)
        form_data = empty_office_term_form(person_id)
        if request.method == 'POST':
            form_data = office_term_form_data_from_request(person_id)
            errors = validate_office_term_form(form_data)
            if not errors:
                insert_office_term(g.db, form_data)
                flash('Dodano urząd lub funkcję osoby.', 'success')
                return redirect(url_for('person_detail', person_id=person_id))
            for error in errors:
                flash(error, 'error')
        return render_template(
            'office_term_form.html',
            form_title=f'Nowy urząd lub funkcja: {person["canonical_name"]}',
            submit_label='Dodaj wpis',
            form_action=url_for('office_term_create', person_id=person_id),
            form_data=form_data,
            person=person,
            date_options=fetch_historical_dates(g.db),
            bibliography_options=fetch_bibliography_items(g.db),
            office_type_options=fetch_office_type_options(g.db, form_data['office_type'] or None),
        )

    @app.route('/persons/<int:person_id>/offices/<int:office_id>/edit', methods=['GET', 'POST'])
    def office_term_edit(person_id: int, office_id: int):
        person = g.db.execute('SELECT * FROM person WHERE id = ?', (person_id,)).fetchone()
        office = g.db.execute('SELECT * FROM office_term WHERE id = ? AND person_id = ?', (office_id, person_id)).fetchone()
        if not person or not office:
            abort(404)
        if request.method == 'POST':
            form_data = office_term_form_data_from_request(person_id)
            errors = validate_office_term_form(form_data)
            if not errors:
                update_office_term(g.db, office_id, form_data)
                flash('Zapisano zmiany w urzędzie lub funkcji osoby.', 'success')
                return redirect(url_for('person_detail', person_id=person_id))
            for error in errors:
                flash(error, 'error')
        else:
            form_data = office_term_form_data_from_row(office)
        return render_template(
            'office_term_form.html',
            form_title=f'Edycja urzędu lub funkcji: {person["canonical_name"]}',
            submit_label='Zapisz zmiany',
            form_action=url_for('office_term_edit', person_id=person_id, office_id=office_id),
            form_data=form_data,
            person=person,
            date_options=fetch_historical_dates(g.db),
            bibliography_options=fetch_bibliography_items(g.db),
            office_type_options=fetch_office_type_options(g.db, form_data['office_type'] or None),
        )

    @app.route('/persons/<int:person_id>/offices/<int:office_id>/delete', methods=['POST'])
    def office_term_delete(person_id: int, office_id: int):
        office = g.db.execute('SELECT id FROM office_term WHERE id = ? AND person_id = ?', (office_id, person_id)).fetchone()
        if not office:
            abort(404)
        g.db.execute('DELETE FROM office_term WHERE id = ?', (office_id,))
        g.db.commit()
        flash('Usunięto urząd lub funkcję.', 'success')
        return redirect(url_for('person_detail', person_id=person_id))

    @app.route('/persons/<int:person_id>/biographies/new', methods=['GET', 'POST'])
    def biography_note_create(person_id: int):
        person = g.db.execute('SELECT * FROM person WHERE id = ?', (person_id,)).fetchone()
        if not person:
            abort(404)
        form_data = empty_biography_note_form(person_id)
        if request.method == 'POST':
            form_data = biography_note_form_data_from_request(person_id)
            errors = validate_biography_note_form(form_data)
            if not errors:
                insert_biography_note(g.db, form_data)
                flash('Dodano biogram.', 'success')
                return redirect(url_for('person_detail', person_id=person_id))
            for error in errors:
                flash(error, 'error')
        return render_template(
            'biography_note_form.html',
            form_title=f'Nowy biogram: {person["canonical_name"]}',
            submit_label='Dodaj biogram',
            form_action=url_for('biography_note_create', person_id=person_id),
            form_data=form_data,
            person=person,
            bibliography_options=fetch_bibliography_items(g.db),
        )

    @app.route('/persons/<int:person_id>/biographies/<int:bio_id>/edit', methods=['GET', 'POST'])
    def biography_note_edit(person_id: int, bio_id: int):
        person = g.db.execute('SELECT * FROM person WHERE id = ?', (person_id,)).fetchone()
        bio = g.db.execute('SELECT * FROM biography_note WHERE id = ? AND person_id = ?', (bio_id, person_id)).fetchone()
        if not person or not bio:
            abort(404)
        if request.method == 'POST':
            form_data = biography_note_form_data_from_request(person_id)
            errors = validate_biography_note_form(form_data)
            if not errors:
                update_biography_note(g.db, bio_id, form_data)
                flash('Zapisano zmiany w biogramie.', 'success')
                return redirect(url_for('person_detail', person_id=person_id))
            for error in errors:
                flash(error, 'error')
        else:
            form_data = biography_note_form_data_from_row(bio)
        return render_template(
            'biography_note_form.html',
            form_title=f'Edycja biogramu: {person["canonical_name"]}',
            submit_label='Zapisz zmiany',
            form_action=url_for('biography_note_edit', person_id=person_id, bio_id=bio_id),
            form_data=form_data,
            person=person,
            bibliography_options=fetch_bibliography_items(g.db),
        )

    @app.route('/persons/<int:person_id>/biographies/<int:bio_id>/delete', methods=['POST'])
    def biography_note_delete(person_id: int, bio_id: int):
        bio = g.db.execute('SELECT id FROM biography_note WHERE id = ? AND person_id = ?', (bio_id, person_id)).fetchone()
        if not bio:
            abort(404)
        g.db.execute('DELETE FROM biography_note WHERE id = ?', (bio_id,))
        g.db.commit()
        flash('Usunięto biogram.', 'success')
        return redirect(url_for('person_detail', person_id=person_id))

    @app.route('/persons/<int:person_id>/variants/new', methods=['GET', 'POST'])
    def person_name_variant_create(person_id: int):
        person = g.db.execute('SELECT * FROM person WHERE id = ?', (person_id,)).fetchone()
        if not person:
            abort(404)
        form_data = empty_person_name_variant_form(person_id)
        if request.method == 'POST':
            form_data = person_name_variant_form_data_from_request(person_id)
            errors = validate_person_name_variant_form(form_data)
            if not errors:
                insert_person_name_variant(g.db, form_data)
                flash('Dodano wariant nazwy.', 'success')
                return redirect(url_for('person_detail', person_id=person_id))
            for error in errors:
                flash(error, 'error')
        return render_template(
            'person_name_variant_form.html',
            form_title=f'Nowy wariant nazwy: {person["canonical_name"]}',
            submit_label='Dodaj wariant',
            form_action=url_for('person_name_variant_create', person_id=person_id),
            form_data=form_data,
            person=person,
            language_options=person_name_variant_language_options(),
        )

    @app.route('/persons/<int:person_id>/variants/<int:variant_id>/edit', methods=['GET', 'POST'])
    def person_name_variant_edit(person_id: int, variant_id: int):
        person = g.db.execute('SELECT * FROM person WHERE id = ?', (person_id,)).fetchone()
        variant = g.db.execute(
            'SELECT * FROM person_name_variant WHERE id = ? AND person_id = ?',
            (variant_id, person_id),
        ).fetchone()
        if not person or not variant:
            abort(404)
        if request.method == 'POST':
            form_data = person_name_variant_form_data_from_request(person_id)
            errors = validate_person_name_variant_form(form_data)
            if not errors:
                update_person_name_variant(g.db, variant_id, form_data)
                flash('Zapisano zmiany wariantu nazwy.', 'success')
                return redirect(url_for('person_detail', person_id=person_id))
            for error in errors:
                flash(error, 'error')
        else:
            form_data = person_name_variant_form_data_from_row(variant)
        return render_template(
            'person_name_variant_form.html',
            form_title=f'Edycja wariantu nazwy: {person["canonical_name"]}',
            submit_label='Zapisz zmiany',
            form_action=url_for('person_name_variant_edit', person_id=person_id, variant_id=variant_id),
            form_data=form_data,
            person=person,
            language_options=person_name_variant_language_options(),
        )

    @app.route('/persons/<int:person_id>/variants/<int:variant_id>/delete', methods=['POST'])
    def person_name_variant_delete(person_id: int, variant_id: int):
        variant = g.db.execute(
            'SELECT id FROM person_name_variant WHERE id = ? AND person_id = ?',
            (variant_id, person_id),
        ).fetchone()
        if not variant:
            abort(404)
        g.db.execute('DELETE FROM person_name_variant WHERE id = ?', (variant_id,))
        g.db.commit()
        flash('Usunięto wariant nazwy.', 'success')
        return redirect(url_for('person_detail', person_id=person_id))

    @app.route('/persons/<int:person_id>/presences/new', methods=['GET', 'POST'])
    def curia_presence_create(person_id: int):
        person = g.db.execute('SELECT * FROM person WHERE id = ?', (person_id,)).fetchone()
        if not person:
            abort(404)
        form_data = empty_curia_presence_form(person_id)
        if request.method == 'POST':
            form_data = curia_presence_form_data_from_request(person_id)
            errors = validate_curia_presence_form(form_data)
            if not errors:
                insert_curia_presence(g.db, form_data)
                flash('Dodano wpis o obecności przy Kurii.', 'success')
                return redirect(url_for('person_detail', person_id=person_id))
            for error in errors:
                flash(error, 'error')
        return render_template(
            'curia_presence_form.html',
            form_title=f'Nowa obecność przy Stolicy Apostolskiej: {person["canonical_name"]}',
            submit_label='Dodaj wpis',
            form_action=url_for('curia_presence_create', person_id=person_id),
            form_data=form_data,
            person=person,
            date_options=fetch_historical_dates(g.db),
            reference_options=fetch_reference_mentions(g.db),
        )

    @app.route('/persons/<int:person_id>/presences/<int:presence_id>/edit', methods=['GET', 'POST'])
    def curia_presence_edit(person_id: int, presence_id: int):
        person = g.db.execute('SELECT * FROM person WHERE id = ?', (person_id,)).fetchone()
        presence = g.db.execute(
            'SELECT * FROM curia_presence WHERE id = ? AND person_id = ?',
            (presence_id, person_id),
        ).fetchone()
        if not person or not presence:
            abort(404)
        if request.method == 'POST':
            form_data = curia_presence_form_data_from_request(person_id)
            errors = validate_curia_presence_form(form_data)
            if not errors:
                update_curia_presence(g.db, presence_id, form_data)
                flash('Zapisano zmiany obecności przy Kurii.', 'success')
                return redirect(url_for('person_detail', person_id=person_id))
            for error in errors:
                flash(error, 'error')
        else:
            form_data = curia_presence_form_data_from_row(presence)
        return render_template(
            'curia_presence_form.html',
            form_title=f'Edycja obecności przy Kurii: {person["canonical_name"]}',
            submit_label='Zapisz zmiany',
            form_action=url_for('curia_presence_edit', person_id=person_id, presence_id=presence_id),
            form_data=form_data,
            person=person,
            date_options=fetch_historical_dates(g.db),
            reference_options=fetch_reference_mentions(g.db),
        )

    @app.route('/persons/<int:person_id>/presences/<int:presence_id>/delete', methods=['POST'])
    def curia_presence_delete(person_id: int, presence_id: int):
        presence = g.db.execute(
            'SELECT id FROM curia_presence WHERE id = ? AND person_id = ?',
            (presence_id, person_id),
        ).fetchone()
        if not presence:
            abort(404)
        g.db.execute('DELETE FROM curia_presence WHERE id = ?', (presence_id,))
        g.db.commit()
        flash('Usunięto obecność przy Stolicy Apostolskiej.', 'success')
        return redirect(url_for('person_detail', person_id=person_id))

    @app.route('/embassies')
    def embassies_list():
        q = request.args.get('q', '').strip()
        date_field = request.args.get('date_field', 'audience').strip()
        audience_from = request.args.get('audience_from', '').strip()
        audience_to = request.args.get('audience_to', '').strip()
        sort = request.args.get('sort', 'year')
        direction = request.args.get('direction', 'desc')
        date_field_map = {
            'audience': 'e.audience_date_id',
            'appointment': 'e.appointment_date_id',
            'arrival': 'e.arrival_in_rome_date_id',
            'departure': 'e.departure_from_rome_date_id',
            'return': 'e.return_to_poland_date_id',
        }
        date_field = date_field if date_field in date_field_map else 'audience'
        clauses = []
        params: list[Any] = []
        if q:
            clauses.append(
                '''
                (
                  e.title LIKE ?
                  OR e.mission_subject LIKE ?
                  OR e.description_text LIKE ?
                  OR EXISTS (
                    SELECT 1
                    FROM embassy_participant ep
                    JOIN person p ON p.id = ep.person_id
                    WHERE ep.embassy_id = e.id
                      AND p.canonical_name LIKE ?
                  )
                )
                '''
            )
            like = f'%{q}%'
            params.extend([like, like, like, like])
        if audience_from:
            clauses.append("COALESCE(fd.sort_key_end, fd.sort_key_start) >= ?")
            params.append(audience_from)
        if audience_to:
            clauses.append("fd.sort_key_start <= ?")
            params.append(audience_to)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ''
        order_map = {
            'title': 'e.title',
            'year': 'e.year_label',
            'subject': 'e.mission_subject',
        }
        sort = sort if sort in order_map else 'year'
        direction = 'asc' if direction == 'asc' else 'desc'
        order_expr = order_map[sort]
        if sort in {'title', 'year', 'subject'}:
            nulls = 'NULLS FIRST' if direction == 'asc' else 'NULLS LAST'
            order_by = f'{order_expr} {direction.upper()} {nulls}, e.id DESC'
        rows = g.db.execute(
            f'''
            SELECT e.id, e.title, e.year_label, e.mission_subject,
                   aud.display_label AS audience_label,
                   aud.date_kind AS audience_kind
            FROM embassy e
            LEFT JOIN historical_date aud ON aud.id = e.audience_date_id
            LEFT JOIN historical_date fd ON fd.id = {date_field_map[date_field]}
            {where}
            ORDER BY {order_by}
            ''',
            params,
        ).fetchall()
        return render_template(
            'embassies_list.html',
            rows=rows,
            q=q,
            sort=sort,
            direction=direction,
            date_field=date_field,
            audience_from=audience_from,
            audience_to=audience_to,
        )

    @app.route('/embassies/<int:embassy_id>/delete', methods=['POST'])
    def embassy_delete(embassy_id: int):
        embassy = g.db.execute('SELECT id, title FROM embassy WHERE id = ?', (embassy_id,)).fetchone()
        if not embassy:
            abort(404)
        g.db.execute('DELETE FROM embassy WHERE id = ?', (embassy_id,))
        g.db.commit()
        flash(f'Usunięto poselstwo: {embassy["title"] or "bez tytułu"}.', 'success')
        return redirect(url_for('embassies_list'))

    @app.route('/embassies/export.csv')
    def embassies_export():
        rows = g.db.execute(
            '''
            SELECT title, year_label, mission_subject
            FROM embassy
            ORDER BY COALESCE(year_label, '') DESC, id DESC
            '''
        ).fetchall()
        return csv_response('poselstwa.csv', ['Tytuł', 'Rok', 'Przedmiot misji'], rows)

    @app.route('/embassies/<int:embassy_id>')
    def embassy_detail(embassy_id: int):
        db = g.db
        embassy = db.execute(
            '''
            SELECT e.*,
                   ad.display_label AS appointment_label, ad.date_kind AS appointment_kind,
                   ard.display_label AS arrival_label, ard.date_kind AS arrival_kind,
                   aud.display_label AS audience_label, aud.date_kind AS audience_kind,
                   dep.display_label AS departure_label, dep.date_kind AS departure_kind,
                   ret.display_label AS return_label, ret.date_kind AS return_kind
            FROM embassy e
            LEFT JOIN historical_date ad ON ad.id = e.appointment_date_id
            LEFT JOIN historical_date ard ON ard.id = e.arrival_in_rome_date_id
            LEFT JOIN historical_date aud ON aud.id = e.audience_date_id
            LEFT JOIN historical_date dep ON dep.id = e.departure_from_rome_date_id
            LEFT JOIN historical_date ret ON ret.id = e.return_to_poland_date_id
            WHERE e.id = ?
            ''',
            (embassy_id,),
        ).fetchone()
        if not embassy:
            abort(404)
        participants = db.execute(
            '''
            SELECT ep.*, p.canonical_name, p.display_name
            FROM embassy_participant ep
            JOIN person p ON p.id = ep.person_id
            WHERE ep.embassy_id = ?
            ORDER BY COALESCE(ep.rank_order, 9999), p.canonical_name
            ''',
            (embassy_id,),
        ).fetchall()
        bibliography = db.execute(
            '''
            SELECT eb.id, eb.comment, eb.page_range,
                   b.short_citation, b.full_citation, b.title, b.publication_year, b.note
            FROM embassy_bibliography eb
            JOIN bibliography_item b ON b.id = eb.bibliography_item_id
            WHERE eb.embassy_id = ?
            ORDER BY b.short_citation
            ''',
            (embassy_id,),
        ).fetchall()
        sources = db.execute(
            '''
            SELECT st.*, hd.display_label AS source_date_label, hd.date_kind AS source_date_kind,
                   COUNT(DISTINCT ss.id) AS segment_count,
                   COUNT(DISTINCT ta.id) AS annotation_count
            FROM source_text st
            LEFT JOIN historical_date hd ON hd.id = st.source_date_id
            LEFT JOIN source_segment ss ON ss.source_text_id = st.id
            LEFT JOIN theme_annotation ta ON ta.source_text_id = st.id
            WHERE st.embassy_id = ?
            GROUP BY st.id
            ORDER BY st.id
            ''',
            (embassy_id,),
        ).fetchall()
        return render_template('embassy_detail.html', embassy=embassy, participants=participants, bibliography=bibliography, sources=sources)

    @app.route('/embassies/new', methods=['GET', 'POST'])
    def embassy_create():
        form_data = empty_embassy_form()
        if request.method == 'POST':
            form_data = embassy_form_data_from_request()
            errors = validate_embassy_form(form_data)
            if not errors:
                embassy_id = insert_embassy(g.db, form_data)
                flash('Dodano nowe poselstwo.', 'success')
                return redirect(url_for('embassy_detail', embassy_id=embassy_id))
            for error in errors:
                flash(error, 'error')
        return render_template(
            'embassy_form.html',
            form_title='Nowe poselstwo',
            submit_label='Dodaj poselstwo',
            form_action=url_for('embassy_create'),
            form_data=form_data,
            date_options=fetch_historical_dates(g.db),
            embassy=None,
        )

    @app.route('/embassies/<int:embassy_id>/edit', methods=['GET', 'POST'])
    def embassy_edit(embassy_id: int):
        embassy = g.db.execute('SELECT * FROM embassy WHERE id = ?', (embassy_id,)).fetchone()
        if not embassy:
            abort(404)
        if request.method == 'POST':
            form_data = embassy_form_data_from_request(embassy)
            errors = validate_embassy_form(form_data)
            if not errors:
                update_embassy(g.db, embassy_id, form_data)
                flash('Zapisano zmiany w rekordzie poselstwa.', 'success')
                return redirect(url_for('embassy_detail', embassy_id=embassy_id))
            for error in errors:
                flash(error, 'error')
        else:
            form_data = embassy_form_data_from_row(embassy)
        return render_template(
            'embassy_form.html',
            form_title=f'Edycja poselstwa: {embassy["title"] or "bez tytułu"}',
            submit_label='Zapisz zmiany',
            form_action=url_for('embassy_edit', embassy_id=embassy_id),
            form_data=form_data,
            date_options=fetch_historical_dates(g.db),
            embassy=embassy,
        )

    @app.route('/embassies/<int:embassy_id>/participants/new', methods=['GET', 'POST'])
    def embassy_participant_create(embassy_id: int):
        embassy = g.db.execute('SELECT * FROM embassy WHERE id = ?', (embassy_id,)).fetchone()
        if not embassy:
            abort(404)
        form_data = empty_embassy_participant_form(embassy_id)
        if request.method == 'POST':
            form_data = embassy_participant_form_data_from_request(embassy_id)
            errors = validate_embassy_participant_form(form_data)
            if not errors:
                insert_embassy_participant(g.db, form_data)
                flash('Dodano uczestnika poselstwa.', 'success')
                return redirect(url_for('embassy_detail', embassy_id=embassy_id))
            for error in errors:
                flash(error, 'error')
        return render_template(
            'embassy_participant_form.html',
            form_title=f'Nowy uczestnik: {embassy["title"] or "bez tytułu"}',
            submit_label='Dodaj uczestnika',
            form_action=url_for('embassy_participant_create', embassy_id=embassy_id),
            form_data=form_data,
            embassy=embassy,
            person_options=fetch_person_options(g.db),
            office_options=fetch_office_options_for_person(g.db, parse_optional_int(form_data['person_id']), form_data['office_during_embassy']),
        )

    @app.route('/embassies/<int:embassy_id>/participants/<int:participant_id>/edit', methods=['GET', 'POST'])
    def embassy_participant_edit(embassy_id: int, participant_id: int):
        embassy = g.db.execute('SELECT * FROM embassy WHERE id = ?', (embassy_id,)).fetchone()
        participant = g.db.execute(
            'SELECT * FROM embassy_participant WHERE id = ? AND embassy_id = ?',
            (participant_id, embassy_id),
        ).fetchone()
        if not embassy or not participant:
            abort(404)
        if request.method == 'POST':
            form_data = embassy_participant_form_data_from_request(embassy_id)
            errors = validate_embassy_participant_form(form_data, current_id=participant_id)
            if not errors:
                update_embassy_participant(g.db, participant_id, form_data)
                flash('Zapisano zmiany uczestnika poselstwa.', 'success')
                return redirect(url_for('embassy_detail', embassy_id=embassy_id))
            for error in errors:
                flash(error, 'error')
        else:
            form_data = embassy_participant_form_data_from_row(participant)
        return render_template(
            'embassy_participant_form.html',
            form_title=f'Edycja uczestnika: {embassy["title"] or "bez tytułu"}',
            submit_label='Zapisz zmiany',
            form_action=url_for('embassy_participant_edit', embassy_id=embassy_id, participant_id=participant_id),
            form_data=form_data,
            embassy=embassy,
            person_options=fetch_person_options(g.db),
            office_options=fetch_office_options_for_person(g.db, parse_optional_int(form_data['person_id']), form_data['office_during_embassy']),
        )

    @app.route('/embassies/<int:embassy_id>/participants/<int:participant_id>/delete', methods=['POST'])
    def embassy_participant_delete(embassy_id: int, participant_id: int):
        participant = g.db.execute(
            'SELECT id FROM embassy_participant WHERE id = ? AND embassy_id = ?',
            (participant_id, embassy_id),
        ).fetchone()
        if not participant:
            abort(404)
        g.db.execute('DELETE FROM embassy_participant WHERE id = ?', (participant_id,))
        g.db.commit()
        flash('Usunięto uczestnika poselstwa.', 'success')
        return redirect(url_for('embassy_detail', embassy_id=embassy_id))

    @app.route('/persons/<int:person_id>/offices/options')
    def person_office_options(person_id: int):
        person = g.db.execute('SELECT id FROM person WHERE id = ?', (person_id,)).fetchone()
        if not person:
            return jsonify({'ok': False, 'errors': ['Nie znaleziono osoby.']}), 404
        options = fetch_office_options_for_person(g.db, person_id)
        return jsonify({'ok': True, 'options': options})

    @app.route('/embassies/<int:embassy_id>/bibliography/new', methods=['GET', 'POST'])
    def embassy_bibliography_create(embassy_id: int):
        embassy = g.db.execute('SELECT * FROM embassy WHERE id = ?', (embassy_id,)).fetchone()
        if not embassy:
            abort(404)
        form_data = empty_embassy_bibliography_form(embassy_id)
        if request.method == 'POST':
            form_data = embassy_bibliography_form_data_from_request(embassy_id)
            errors = validate_embassy_bibliography_form(form_data)
            if not errors:
                insert_embassy_bibliography(g.db, form_data)
                flash('Dodano pozycję literatury.', 'success')
                return redirect(url_for('embassy_detail', embassy_id=embassy_id))
            for error in errors:
                flash(error, 'error')
        return render_template(
            'embassy_bibliography_form.html',
            form_title=f'Nowa pozycja literatury: {embassy["title"] or "bez tytułu"}',
            submit_label='Dodaj pozycję',
            form_action=url_for('embassy_bibliography_create', embassy_id=embassy_id),
            form_data=form_data,
            embassy=embassy,
            bibliography_options=fetch_bibliography_items(g.db),
        )

    @app.route('/embassies/<int:embassy_id>/bibliography/<int:entry_id>/edit', methods=['GET', 'POST'])
    def embassy_bibliography_edit(embassy_id: int, entry_id: int):
        embassy = g.db.execute('SELECT * FROM embassy WHERE id = ?', (embassy_id,)).fetchone()
        entry = g.db.execute(
            'SELECT * FROM embassy_bibliography WHERE id = ? AND embassy_id = ?',
            (entry_id, embassy_id),
        ).fetchone()
        if not embassy or not entry:
            abort(404)
        if request.method == 'POST':
            form_data = embassy_bibliography_form_data_from_request(embassy_id)
            errors = validate_embassy_bibliography_form(form_data, current_id=entry_id)
            if not errors:
                update_embassy_bibliography(g.db, entry_id, form_data)
                flash('Zapisano zmiany literatury poselstwa.', 'success')
                return redirect(url_for('embassy_detail', embassy_id=embassy_id))
            for error in errors:
                flash(error, 'error')
        else:
            form_data = embassy_bibliography_form_data_from_row(entry)
        return render_template(
            'embassy_bibliography_form.html',
            form_title=f'Edycja literatury: {embassy["title"] or "bez tytułu"}',
            submit_label='Zapisz zmiany',
            form_action=url_for('embassy_bibliography_edit', embassy_id=embassy_id, entry_id=entry_id),
            form_data=form_data,
            embassy=embassy,
            bibliography_options=fetch_bibliography_items(g.db),
        )

    @app.route('/embassies/<int:embassy_id>/bibliography/<int:entry_id>/delete', methods=['POST'])
    def embassy_bibliography_delete(embassy_id: int, entry_id: int):
        entry = g.db.execute(
            'SELECT id FROM embassy_bibliography WHERE id = ? AND embassy_id = ?',
            (entry_id, embassy_id),
        ).fetchone()
        if not entry:
            abort(404)
        g.db.execute('DELETE FROM embassy_bibliography WHERE id = ?', (entry_id,))
        g.db.commit()
        flash('Usunięto pozycję literatury.', 'success')
        return redirect(url_for('embassy_detail', embassy_id=embassy_id))

    @app.route('/bibliography')
    def bibliography_list():
        q = request.args.get('q', '').strip()
        params: list[Any] = []
        where = ''
        if q:
            like = f'%{q}%'
            where = '''
            WHERE short_citation LIKE ?
               OR full_citation LIKE ?
               OR author_text LIKE ?
               OR editor_text LIKE ?
               OR title LIKE ?
               OR publication_place LIKE ?
               OR publication_year LIKE ?
               OR note LIKE ?
            '''
            params.extend([like, like, like, like, like, like, like, like])
        rows = g.db.execute(
            f'''
            SELECT b.*,
                   COUNT(DISTINCT eb.id) AS embassy_count,
                   COUNT(DISTINCT bn.id) AS biography_count,
                   COUNT(DISTINCT ot.id) AS office_count,
                   COUNT(DISTINCT st.id) AS source_count
            FROM bibliography_item b
            LEFT JOIN embassy_bibliography eb ON eb.bibliography_item_id = b.id
            LEFT JOIN biography_note bn ON bn.bibliography_item_id = b.id
            LEFT JOIN office_term ot ON ot.bibliography_item_id = b.id
            LEFT JOIN source_text st ON st.bibliography_item_id = b.id
            {where}
            GROUP BY b.id
            ORDER BY b.short_citation ASC, b.id ASC
            ''',
            params,
        ).fetchall()
        return render_template('bibliography_list.html', rows=rows, q=q)

    @app.route('/bibliography/new', methods=['GET', 'POST'])
    def bibliography_item_create():
        form_data = empty_bibliography_item_form()
        if request.method == 'POST':
            form_data = bibliography_item_form_data_from_mapping(request.form)
            errors = validate_bibliography_item_form(form_data)
            if not errors:
                item_id = insert_bibliography_item(g.db, form_data)
                flash('Dodano pozycję bibliograficzną.', 'success')
                return redirect(url_for('bibliography_item_edit', item_id=item_id))
            for error in errors:
                flash(error, 'error')
        return render_template(
            'bibliography_item_form.html',
            form_title='Nowa pozycja bibliograficzna',
            submit_label='Dodaj pozycję',
            form_action=url_for('bibliography_item_create'),
            form_data=form_data,
            item_type_options=bibliography_item_type_options(),
        )

    @app.route('/bibliography/<int:item_id>/edit', methods=['GET', 'POST'])
    def bibliography_item_edit(item_id: int):
        item = g.db.execute('SELECT * FROM bibliography_item WHERE id = ?', (item_id,)).fetchone()
        if not item:
            abort(404)
        if request.method == 'POST':
            form_data = bibliography_item_form_data_from_mapping(request.form)
            errors = validate_bibliography_item_form(form_data, current_id=item_id)
            if not errors:
                update_bibliography_item(g.db, item_id, form_data)
                flash('Zapisano zmiany pozycji bibliograficznej.', 'success')
                return redirect(url_for('bibliography_list'))
            for error in errors:
                flash(error, 'error')
        else:
            form_data = bibliography_item_form_data_from_row(item)
        return render_template(
            'bibliography_item_form.html',
            form_title=f'Edycja pozycji bibliograficznej: {item["short_citation"]}',
            submit_label='Zapisz zmiany',
            form_action=url_for('bibliography_item_edit', item_id=item_id),
            form_data=form_data,
            item=item,
            item_type_options=bibliography_item_type_options(),
        )

    @app.route('/bibliography/<int:item_id>/delete', methods=['POST'])
    def bibliography_item_delete(item_id: int):
        item = g.db.execute('SELECT id, short_citation FROM bibliography_item WHERE id = ?', (item_id,)).fetchone()
        if not item:
            abort(404)
        try:
            g.db.execute('DELETE FROM bibliography_item WHERE id = ?', (item_id,))
            g.db.commit()
        except sqlite3.IntegrityError:
            flash('Nie można usunąć tej pozycji bibliograficznej, ponieważ jest już używana w innych rekordach.', 'error')
            return redirect(url_for('bibliography_list'))
        flash(f'Usunięto pozycję bibliograficzną: {item["short_citation"]}.', 'success')
        return redirect(url_for('bibliography_list'))

    @app.route('/bibliography/quick-create', methods=['POST'])
    def bibliography_item_quick_create():
        payload = request.get_json(silent=True) or request.form
        form_data = bibliography_item_form_data_from_mapping(payload)
        errors = validate_bibliography_item_form(form_data)
        if errors:
            return jsonify({'ok': False, 'errors': errors}), 400
        item_id = insert_bibliography_item(g.db, form_data)
        return jsonify({
            'ok': True,
            'item': {
                'id': item_id,
                'short_citation': form_data['short_citation'],
                'title': form_data['title'],
                'publication_year': form_data['publication_year'],
                'note': form_data['note'],
            }
        })

    @app.route('/embassies/<int:embassy_id>/sources/new', methods=['GET', 'POST'])
    def source_text_create(embassy_id: int):
        embassy = g.db.execute('SELECT * FROM embassy WHERE id = ?', (embassy_id,)).fetchone()
        if not embassy:
            abort(404)
        form_data = empty_source_text_form(embassy_id)
        if request.method == 'POST':
            form_data = source_text_form_data_from_request(embassy_id)
            errors = validate_source_text_form(form_data)
            if not errors:
                source_id = insert_source_text(g.db, form_data)
                flash('Dodano tekst źródłowy.', 'success')
                return redirect(url_for('source_detail', source_id=source_id))
            for error in errors:
                flash(error, 'error')
        return render_template(
            'source_text_form.html',
            form_title=f'Nowe źródło: {embassy["title"] or "bez tytułu"}',
            submit_label='Dodaj źródło',
            form_action=url_for('source_text_create', embassy_id=embassy_id),
            form_data=form_data,
            embassy=embassy,
            cancel_url=url_for('embassy_detail', embassy_id=embassy_id),
            source_type_options=fetch_source_type_options(g.db, form_data['source_type'] or None),
        )

    @app.route('/sources/<int:source_id>/edit', methods=['GET', 'POST'])
    def source_text_edit(source_id: int):
        source = g.db.execute('SELECT * FROM source_text WHERE id = ?', (source_id,)).fetchone()
        if not source:
            abort(404)
        embassy = g.db.execute('SELECT * FROM embassy WHERE id = ?', (source['embassy_id'],)).fetchone()
        if request.method == 'POST':
            form_data = source_text_form_data_from_request(source['embassy_id'], source)
            errors = validate_source_text_form(form_data)
            if not errors:
                update_source_text(g.db, source_id, form_data)
                flash('Zapisano zmiany tekstu źródłowego.', 'success')
                return redirect(url_for('source_detail', source_id=source_id))
            for error in errors:
                flash(error, 'error')
        else:
            form_data = source_text_form_data_from_row(source)
        return render_template(
            'source_text_form.html',
            form_title=f'Edycja źródła: {source["edition_label"] or source["source_type"]}',
            submit_label='Zapisz zmiany',
            form_action=url_for('source_text_edit', source_id=source_id),
            form_data=form_data,
            embassy=embassy,
            cancel_url=url_for('source_detail', source_id=source_id),
            source_type_options=fetch_source_type_options(g.db, form_data['source_type'] or None),
        )

    @app.route('/sources/<int:source_id>/delete', methods=['POST'])
    def source_text_delete(source_id: int):
        source = g.db.execute('SELECT embassy_id FROM source_text WHERE id = ?', (source_id,)).fetchone()
        if not source:
            abort(404)
        g.db.execute('DELETE FROM source_text WHERE id = ?', (source_id,))
        g.db.commit()
        flash('Usunięto źródło.', 'success')
        return redirect(url_for('embassy_detail', embassy_id=source['embassy_id']))

    @app.route('/sources/<int:source_id>/segments/new', methods=['GET', 'POST'])
    def source_segment_create(source_id: int):
        source = g.db.execute('SELECT * FROM source_text WHERE id = ?', (source_id,)).fetchone()
        if not source:
            abort(404)
        form_data = empty_source_segment_form(source_id)
        if request.method == 'POST':
            form_data = source_segment_form_data_from_request(source_id)
            errors = validate_source_segment_form(form_data)
            if not errors:
                insert_source_segment(g.db, form_data)
                flash('Dodano segment źródła.', 'success')
                return redirect(url_for('source_detail', source_id=source_id))
            for error in errors:
                flash(error, 'error')
        return render_template(
            'source_segment_form.html',
            form_title=f'Nowy segment źródła: {source["edition_label"] or source["source_type"]}',
            submit_label='Dodaj segment',
            form_action=url_for('source_segment_create', source_id=source_id),
            form_data=form_data,
            source=source,
        )

    @app.route('/sources/<int:source_id>/segments/<int:segment_id>/edit', methods=['GET', 'POST'])
    def source_segment_edit(source_id: int, segment_id: int):
        source = g.db.execute('SELECT * FROM source_text WHERE id = ?', (source_id,)).fetchone()
        segment = g.db.execute(
            'SELECT * FROM source_segment WHERE id = ? AND source_text_id = ?',
            (segment_id, source_id),
        ).fetchone()
        if not source or not segment:
            abort(404)
        if request.method == 'POST':
            form_data = source_segment_form_data_from_request(source_id)
            errors = validate_source_segment_form(form_data, current_id=segment_id)
            if not errors:
                update_source_segment(g.db, segment_id, form_data)
                flash('Zapisano zmiany segmentu.', 'success')
                return redirect(url_for('source_detail', source_id=source_id))
            for error in errors:
                flash(error, 'error')
        else:
            form_data = source_segment_form_data_from_row(segment)
        return render_template(
            'source_segment_form.html',
            form_title=f'Edycja segmentu: {source["edition_label"] or source["source_type"]}',
            submit_label='Zapisz zmiany',
            form_action=url_for('source_segment_edit', source_id=source_id, segment_id=segment_id),
            form_data=form_data,
            source=source,
        )

    @app.route('/sources/<int:source_id>/segments/<int:segment_id>/delete', methods=['POST'])
    def source_segment_delete(source_id: int, segment_id: int):
        segment = g.db.execute(
            'SELECT id FROM source_segment WHERE id = ? AND source_text_id = ?',
            (segment_id, source_id),
        ).fetchone()
        if not segment:
            abort(404)
        g.db.execute('DELETE FROM source_segment WHERE id = ?', (segment_id,))
        g.db.commit()
        flash('Usunięto segment źródła.', 'success')
        return redirect(url_for('source_detail', source_id=source_id))

    @app.route('/sources/<int:source_id>')
    def source_detail(source_id: int):
        db = g.db
        source = db.execute(
            '''
            SELECT st.*, e.title AS embassy_title, e.year_label,
                   hd.display_label AS source_date_label, hd.date_kind AS source_date_kind
            FROM source_text st
            JOIN embassy e ON e.id = st.embassy_id
            LEFT JOIN historical_date hd ON hd.id = st.source_date_id
            WHERE st.id = ?
            ''',
            (source_id,),
        ).fetchone()
        if not source:
            abort(404)
        synced = sync_source_segments_from_full_text(
            db,
            source_id,
            source['original_text_full'] or '',
            source['polish_text_full'] or '',
        )
        if synced:
            db.commit()
        segments = db.execute(
            'SELECT * FROM source_segment WHERE source_text_id = ? ORDER BY segment_no ASC',
            (source_id,),
        ).fetchall()
        annotations = db.execute(
            '''
            SELECT ta.*, th.name AS theme_name, th.color_code, ss.segment_no
            FROM theme_annotation ta
            JOIN theme th ON th.id = ta.theme_id
            LEFT JOIN source_segment ss ON ss.id = ta.source_segment_id
            WHERE ta.source_text_id = ?
            ORDER BY COALESCE(ss.segment_no, 9999), ta.id
            ''',
            (source_id,),
        ).fetchall()
        ann_by_segment: dict[int, dict[str, list[sqlite3.Row]]] = {}
        ann_global: list[sqlite3.Row] = []
        for ann in annotations:
            if ann['source_segment_id']:
                segment_annotations = ann_by_segment.setdefault(ann['source_segment_id'], {'la': [], 'pl': []})
                language_code = ann['text_language_code'] or 'pl'
                segment_annotations.setdefault(language_code, []).append(ann)
            else:
                ann_global.append(ann)
        return render_template('source_detail.html', source=source, segments=segments, ann_by_segment=ann_by_segment, ann_global=ann_global)

    @app.route('/sources/<int:source_id>/segments/<int:segment_id>/annotations/new', methods=['GET', 'POST'])
    def theme_annotation_create(source_id: int, segment_id: int):
        source = g.db.execute(
            '''
            SELECT st.*, e.title AS embassy_title
            FROM source_text st
            JOIN embassy e ON e.id = st.embassy_id
            WHERE st.id = ?
            ''',
            (source_id,),
        ).fetchone()
        segment = g.db.execute(
            'SELECT * FROM source_segment WHERE id = ? AND source_text_id = ?',
            (segment_id, source_id),
        ).fetchone()
        if not source or not segment:
            abort(404)
        language_code = request.args.get('lang', 'pl').strip().lower()
        if language_code not in {'la', 'pl'}:
            language_code = 'pl'
        form_data = empty_theme_annotation_form(source_id, segment_id, language_code, segment)
        selected_text = request.args.get('text', '').strip()
        char_start = request.args.get('char_start', '').strip()
        char_end = request.args.get('char_end', '').strip()
        if selected_text and char_start and char_end:
            form_data['annotated_text_snapshot'] = selected_text
            form_data['char_start'] = char_start
            form_data['char_end'] = char_end
        if request.method == 'POST':
            form_data = theme_annotation_form_data_from_request(source_id, segment_id, segment)
            errors = validate_theme_annotation_form(form_data)
            if not errors:
                insert_theme_annotation(g.db, form_data)
                flash('Dodano oznaczenie motywu.', 'success')
                return redirect(url_for('source_detail', source_id=source_id))
            for error in errors:
                flash(error, 'error')
        return render_template(
            'theme_annotation_form.html',
            form_title=f'Nowy motyw: {source["edition_label"] or source["source_type"]}',
            submit_label='Dodaj motyw',
            form_action=url_for('theme_annotation_create', source_id=source_id, segment_id=segment_id, lang=language_code),
            form_data=form_data,
            source=source,
            segment=segment,
            theme_options=fetch_active_themes(g.db),
        )

    @app.route('/sources/<int:source_id>/annotations/<int:annotation_id>/edit', methods=['GET', 'POST'])
    def theme_annotation_edit(source_id: int, annotation_id: int):
        source = g.db.execute(
            '''
            SELECT st.*, e.title AS embassy_title
            FROM source_text st
            JOIN embassy e ON e.id = st.embassy_id
            WHERE st.id = ?
            ''',
            (source_id,),
        ).fetchone()
        annotation = g.db.execute(
            'SELECT * FROM theme_annotation WHERE id = ? AND source_text_id = ?',
            (annotation_id, source_id),
        ).fetchone()
        if not source or not annotation:
            abort(404)
        segment = None
        if annotation['source_segment_id']:
            segment = g.db.execute(
                'SELECT * FROM source_segment WHERE id = ? AND source_text_id = ?',
                (annotation['source_segment_id'], source_id),
            ).fetchone()
        if request.method == 'POST':
            form_data = theme_annotation_form_data_from_request(source_id, int(annotation['source_segment_id'] or 0), segment)
            errors = validate_theme_annotation_form(form_data)
            if not errors:
                update_theme_annotation(g.db, annotation_id, form_data)
                flash('Zapisano zmiany oznaczenia motywu.', 'success')
                return redirect(url_for('source_detail', source_id=source_id))
            for error in errors:
                flash(error, 'error')
        else:
            form_data = theme_annotation_form_data_from_row(annotation, segment)
        return render_template(
            'theme_annotation_form.html',
            form_title=f'Edycja motywu: {source["edition_label"] or source["source_type"]}',
            submit_label='Zapisz zmiany',
            form_action=url_for('theme_annotation_edit', source_id=source_id, annotation_id=annotation_id),
            form_data=form_data,
            source=source,
            segment=segment,
            theme_options=fetch_active_themes(g.db, form_data['theme_id']),
        )

    @app.route('/sources/<int:source_id>/annotations/<int:annotation_id>/delete', methods=['POST'])
    def theme_annotation_delete(source_id: int, annotation_id: int):
        annotation = g.db.execute(
            'SELECT id FROM theme_annotation WHERE id = ? AND source_text_id = ?',
            (annotation_id, source_id),
        ).fetchone()
        if not annotation:
            abort(404)
        g.db.execute('DELETE FROM theme_annotation WHERE id = ?', (annotation_id,))
        g.db.commit()
        flash('Usunięto oznaczenie motywu.', 'success')
        return redirect(url_for('source_detail', source_id=source_id))

    @app.route('/themes')
    def themes_list():
        rows = g.db.execute(
            '''
            SELECT t.id, t.name, t.slug, t.color_code, t.description_text,
                   COUNT(ta.id) AS annotation_count
            FROM theme t
            LEFT JOIN theme_annotation ta ON ta.theme_id = t.id
            WHERE t.is_active = 1
            GROUP BY t.id
            ORDER BY t.name ASC
            '''
        ).fetchall()
        return render_template('themes_list.html', rows=rows)

    @app.route('/themes/new', methods=['GET', 'POST'])
    def theme_create():
        form_data = empty_theme_form()
        if request.method == 'POST':
            form_data = theme_form_data_from_mapping(request.form)
            errors = validate_theme_form(form_data)
            if not errors:
                theme_id = insert_theme(g.db, form_data)
                flash('Dodano motyw.', 'success')
                return redirect(url_for('themes_list'))
            for error in errors:
                flash(error, 'error')
        return render_template(
            'theme_form.html',
            form_title='Nowy motyw',
            submit_label='Dodaj motyw',
            form_action=url_for('theme_create'),
            form_data=form_data,
        )

    @app.route('/themes/<int:theme_id>')
    def theme_detail(theme_id: int):
        db = g.db
        theme = db.execute('SELECT * FROM theme WHERE id = ?', (theme_id,)).fetchone()
        if not theme:
            abort(404)
        annotations = db.execute(
            '''
            SELECT ta.*, st.edition_label, st.archive_signature, st.id AS source_id,
                   e.id AS embassy_id, e.title AS embassy_title, e.year_label,
                   ss.segment_no, ss.original_segment, ss.polish_segment
            FROM theme_annotation ta
            JOIN source_text st ON st.id = ta.source_text_id
            JOIN embassy e ON e.id = st.embassy_id
            LEFT JOIN source_segment ss ON ss.id = ta.source_segment_id
            WHERE ta.theme_id = ?
            ORDER BY e.year_label DESC, e.id DESC, COALESCE(ss.segment_no, 9999)
            ''',
            (theme_id,),
        ).fetchall()
        segment_annotation_map: dict[int, dict[str, list[sqlite3.Row]]] = {}
        segment_ids = sorted({int(row['source_segment_id']) for row in annotations if row['source_segment_id']})
        if segment_ids:
            placeholders = ', '.join('?' for _ in segment_ids)
            segment_annotations = db.execute(
                f'''
                SELECT ta.*, th.name AS theme_name, th.color_code
                FROM theme_annotation ta
                JOIN theme th ON th.id = ta.theme_id
                WHERE ta.source_segment_id IN ({placeholders})
                ORDER BY ta.char_start ASC, ta.char_end ASC, ta.id ASC
                ''',
                tuple(segment_ids),
            ).fetchall()
            for ann in segment_annotations:
                per_language = segment_annotation_map.setdefault(int(ann['source_segment_id']), {'la': [], 'pl': []})
                language_code = ann['text_language_code'] or 'pl'
                per_language.setdefault(language_code, []).append(ann)
        return render_template(
            'theme_detail.html',
            theme=theme,
            annotations=annotations,
            segment_annotation_map=segment_annotation_map,
        )

    @app.route('/themes/<int:theme_id>/edit', methods=['GET', 'POST'])
    def theme_edit(theme_id: int):
        theme = g.db.execute('SELECT * FROM theme WHERE id = ?', (theme_id,)).fetchone()
        if not theme:
            abort(404)
        return_to = request.values.get('return_to', 'list').strip()
        if return_to not in {'list', 'detail'}:
            return_to = 'list'
        cancel_url = url_for('theme_detail', theme_id=theme_id) if return_to == 'detail' else url_for('themes_list')
        if request.method == 'POST':
            form_data = theme_form_data_from_mapping(request.form)
            errors = validate_theme_form(form_data, current_id=theme_id)
            if not errors:
                update_theme(g.db, theme_id, form_data)
                flash('Zapisano zmiany motywu.', 'success')
                return redirect(cancel_url)
            for error in errors:
                flash(error, 'error')
        else:
            form_data = theme_form_data_from_row(theme)
        return render_template(
            'theme_form.html',
            form_title=f'Edycja motywu: {theme["name"]}',
            submit_label='Zapisz zmiany',
            form_action=url_for('theme_edit', theme_id=theme_id),
            form_data=form_data,
            cancel_url=cancel_url,
            return_to=return_to,
        )

    @app.route('/themes/<int:theme_id>/delete', methods=['POST'])
    def theme_delete(theme_id: int):
        theme = g.db.execute('SELECT * FROM theme WHERE id = ?', (theme_id,)).fetchone()
        if not theme:
            abort(404)
        usage_count = g.db.execute(
            'SELECT COUNT(*) FROM theme_annotation WHERE theme_id = ?',
            (theme_id,),
        ).fetchone()[0]
        if usage_count:
            flash('Nie można usunąć motywu, ponieważ ma już przypisane oznaczenia.', 'error')
            return redirect(url_for('themes_list'))
        g.db.execute('DELETE FROM theme WHERE id = ?', (theme_id,))
        g.db.commit()
        flash(f'Usunięto motyw: {theme["name"]}.', 'success')
        return redirect(url_for('themes_list'))

    @app.route('/parameters')
    def parameters_list():
        source_type_rows = g.db.execute(
            '''
            SELECT std.*,
                   COUNT(st.id) AS usage_count
            FROM source_type_dictionary std
            LEFT JOIN source_text st ON st.source_type = std.value
            GROUP BY std.id
            ORDER BY std.sort_order ASC, std.value ASC
            '''
        ).fetchall()
        office_type_rows = g.db.execute(
            '''
            SELECT otd.*,
                   COUNT(ot.id) AS usage_count
            FROM office_type_dictionary otd
            LEFT JOIN office_term ot ON ot.office_type = otd.value
            GROUP BY otd.id
            ORDER BY otd.sort_order ASC, otd.value ASC
            '''
        ).fetchall()
        return render_template(
            'parameters_list.html',
            source_type_rows=source_type_rows,
            office_type_rows=office_type_rows,
        )

    @app.route('/parameters/source-types')
    def parameter_source_type_list():
        return redirect(url_for('parameters_list'))

    @app.route('/parameters/source-types/new', methods=['GET', 'POST'])
    def parameter_source_type_create():
        form_data = empty_source_type_form()
        if request.method == 'POST':
            form_data = source_type_form_data_from_mapping(request.form)
            errors = validate_source_type_form(form_data)
            if not errors:
                insert_source_type(g.db, form_data)
                flash('Dodano typ źródła.', 'success')
                return redirect(url_for('parameters_list'))
            for error in errors:
                flash(error, 'error')
        return render_template(
            'parameter_source_type_form.html',
            form_title='Nowy typ źródła',
            submit_label='Dodaj typ',
            form_action=url_for('parameter_source_type_create'),
            form_data=form_data,
        )

    @app.route('/parameters/source-types/<int:type_id>/edit', methods=['GET', 'POST'])
    def parameter_source_type_edit(type_id: int):
        row = g.db.execute('SELECT * FROM source_type_dictionary WHERE id = ?', (type_id,)).fetchone()
        if not row:
            abort(404)
        if request.method == 'POST':
            form_data = source_type_form_data_from_mapping(request.form)
            errors = validate_source_type_form(form_data, current_id=type_id)
            if not errors:
                update_source_type(g.db, type_id, form_data, old_value=row['value'])
                flash('Zapisano zmiany typu źródła.', 'success')
                return redirect(url_for('parameters_list'))
            for error in errors:
                flash(error, 'error')
        else:
            form_data = source_type_form_data_from_row(row)
        return render_template(
            'parameter_source_type_form.html',
            form_title=f'Edycja typu źródła: {row["label"] or row["value"]}',
            submit_label='Zapisz zmiany',
            form_action=url_for('parameter_source_type_edit', type_id=type_id),
            form_data=form_data,
        )

    @app.route('/parameters/source-types/<int:type_id>/delete', methods=['POST'])
    def parameter_source_type_delete(type_id: int):
        row = g.db.execute('SELECT * FROM source_type_dictionary WHERE id = ?', (type_id,)).fetchone()
        if not row:
            abort(404)
        usage_count = g.db.execute('SELECT COUNT(*) FROM source_text WHERE source_type = ?', (row['value'],)).fetchone()[0]
        if usage_count:
            flash('Nie można usunąć typu źródła, który jest już przypisany do źródeł.', 'error')
            return redirect(url_for('parameters_list'))
        g.db.execute('DELETE FROM source_type_dictionary WHERE id = ?', (type_id,))
        g.db.commit()
        flash('Usunięto typ źródła.', 'success')
        return redirect(url_for('parameters_list'))

    @app.route('/parameters/office-types/new', methods=['GET', 'POST'])
    def parameter_office_type_create():
        form_data = empty_office_type_form()
        if request.method == 'POST':
            form_data = office_type_form_data_from_mapping(request.form)
            errors = validate_office_type_form(form_data)
            if not errors:
                insert_office_type(g.db, form_data)
                flash('Dodano typ urzędu.', 'success')
                return redirect(url_for('parameters_list'))
            for error in errors:
                flash(error, 'error')
        return render_template(
            'parameter_office_type_form.html',
            form_title='Nowy typ urzędu',
            submit_label='Dodaj typ',
            form_action=url_for('parameter_office_type_create'),
            form_data=form_data,
        )

    @app.route('/parameters/office-types/<int:type_id>/edit', methods=['GET', 'POST'])
    def parameter_office_type_edit(type_id: int):
        row = g.db.execute('SELECT * FROM office_type_dictionary WHERE id = ?', (type_id,)).fetchone()
        if not row:
            abort(404)
        if request.method == 'POST':
            form_data = office_type_form_data_from_mapping(request.form)
            errors = validate_office_type_form(form_data, current_id=type_id)
            if not errors:
                update_office_type(g.db, type_id, form_data, old_value=row['value'])
                flash('Zapisano zmiany typu urzędu.', 'success')
                return redirect(url_for('parameters_list'))
            for error in errors:
                flash(error, 'error')
        else:
            form_data = office_type_form_data_from_row(row)
        return render_template(
            'parameter_office_type_form.html',
            form_title=f'Edycja typu urzędu: {row["label"] or row["value"]}',
            submit_label='Zapisz zmiany',
            form_action=url_for('parameter_office_type_edit', type_id=type_id),
            form_data=form_data,
        )

    @app.route('/parameters/office-types/<int:type_id>/delete', methods=['POST'])
    def parameter_office_type_delete(type_id: int):
        row = g.db.execute('SELECT * FROM office_type_dictionary WHERE id = ?', (type_id,)).fetchone()
        if not row:
            abort(404)
        usage_count = g.db.execute('SELECT COUNT(*) FROM office_term WHERE office_type = ?', (row['value'],)).fetchone()[0]
        if usage_count:
            flash('Nie można usunąć typu urzędu, który jest już przypisany do urzędów lub funkcji.', 'error')
            return redirect(url_for('parameters_list'))
        g.db.execute('DELETE FROM office_type_dictionary WHERE id = ?', (type_id,))
        g.db.commit()
        flash('Usunięto typ urzędu.', 'success')
        return redirect(url_for('parameters_list'))

    @app.route('/about')
    def about():
        return render_template('about.html')

    return app


def get_db(app: Flask) -> sqlite3.Connection:
    db = sqlite3.connect(app.config['DATABASE'])
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys = ON')
    return db


def ensure_parameter_tables(db: sqlite3.Connection) -> None:
    db.execute(
        '''
        CREATE TABLE IF NOT EXISTS source_type_dictionary (
            id INTEGER PRIMARY KEY,
            uuid TEXT NOT NULL UNIQUE,
            value TEXT NOT NULL UNIQUE,
            label TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        '''
    )
    db.execute(
        '''
        CREATE TABLE IF NOT EXISTS office_type_dictionary (
            id INTEGER PRIMARY KEY,
            uuid TEXT NOT NULL UNIQUE,
            value TEXT NOT NULL UNIQUE,
            label TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        '''
    )
    existing_values = {row['value'] for row in db.execute('SELECT value FROM source_type_dictionary').fetchall()}
    source_values = [
        row['source_type']
        for row in db.execute(
            '''
            SELECT DISTINCT source_type
            FROM source_text
            WHERE source_type IS NOT NULL AND TRIM(source_type) <> ''
            ORDER BY source_type ASC
            '''
        ).fetchall()
    ]
    for index, value in enumerate(source_values, start=1):
        if value in existing_values:
            continue
        db.execute(
            '''
            INSERT INTO source_type_dictionary (uuid, value, label, sort_order, is_active)
            VALUES (?, ?, ?, ?, 1)
            ''',
            (str(uuid.uuid4()), value, value, index * 10),
        )
    existing_office_values = {row['value'] for row in db.execute('SELECT value FROM office_type_dictionary').fetchall()}
    office_values = [
        'urząd kościelny',
        'urząd świecki',
        'inny',
    ]
    for index, value in enumerate(office_values, start=1):
        if value in existing_office_values:
            continue
        db.execute(
            '''
            INSERT INTO office_type_dictionary (uuid, value, label, sort_order, is_active)
            VALUES (?, ?, ?, ?, 1)
            ''',
            (str(uuid.uuid4()), value, value, index * 10),
        )
    db.commit()


def ensure_historical_date_compatibility(db: sqlite3.Connection) -> None:
    db.execute(
        '''
        UPDATE historical_date
        SET date_kind = CASE
            WHEN date_kind IN ('month', 'year') THEN 'exact'
            WHEN date_kind = 'range' THEN 'circa'
            ELSE date_kind
        END
        WHERE date_kind IN ('month', 'year', 'range')
        '''
    )
    db.execute(
        '''
        UPDATE historical_date
        SET certainty = 'uncertain'
        WHERE certainty = 'possible'
        '''
    )
    db.commit()


def ensure_bibliography_item_compatibility(db: sqlite3.Connection) -> None:
    columns = {
        row['name']
        for row in db.execute("PRAGMA table_info('bibliography_item')").fetchall()
    }
    if 'publisher_text' not in columns:
        db.execute("ALTER TABLE bibliography_item ADD COLUMN publisher_text TEXT")
        db.commit()


def ensure_embassy_bibliography_compatibility(db: sqlite3.Connection) -> None:
    columns = {
        row['name']
        for row in db.execute("PRAGMA table_info('embassy_bibliography')").fetchall()
    }
    if 'page_range' not in columns:
        db.execute("ALTER TABLE embassy_bibliography ADD COLUMN page_range TEXT")
        db.commit()


def ensure_biography_note_compatibility(db: sqlite3.Connection) -> None:
    columns = {
        row['name']
        for row in db.execute("PRAGMA table_info('biography_note')").fetchall()
    }
    if 'reference_locator' not in columns:
        db.execute("ALTER TABLE biography_note ADD COLUMN reference_locator TEXT")
        db.commit()


def csv_response(filename: str, headers: list[str], rows: list[sqlite3.Row]):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(list(row))
    mem = io.BytesIO(buffer.getvalue().encode('utf-8-sig'))
    return send_file(mem, mimetype='text/csv', as_attachment=True, download_name=filename)


def render_date(start: str | None, end: str | None) -> str:
    if start and end:
        if start == end:
            return start
        return f'{start} – {end}'
    return start or end or '—'


def prefixed_date_label(date_kind: str | None, display_label: str | None) -> str:
    if not display_label:
        return 'brak'
    prefix_map = {
        'circa': 'ok. ',
        'before': 'przed ',
        'after': 'po ',
    }
    prefix = prefix_map.get(date_kind or '', '')
    if prefix and str(display_label).startswith(prefix):
        return str(display_label)
    return f'{prefix}{display_label}'


def fetch_historical_dates(db: sqlite3.Connection) -> list[sqlite3.Row]:
    return db.execute(
        '''
        SELECT id, display_label
        FROM historical_date
        ORDER BY COALESCE(sort_key_start, ''), display_label
        '''
    ).fetchall()


def fetch_bibliography_items(db: sqlite3.Connection) -> list[sqlite3.Row]:
    return db.execute(
        '''
        SELECT id, short_citation, author_text, editor_text, title,
               publisher_text, publication_place, publication_year, note
        FROM bibliography_item
        ORDER BY short_citation ASC
        '''
    ).fetchall()


def fetch_source_type_options(db: sqlite3.Connection, current_value: str | None = None) -> list[sqlite3.Row]:
    if current_value:
        return db.execute(
            '''
            SELECT id, value, label
            FROM source_type_dictionary
            WHERE is_active = 1 OR value = ?
            ORDER BY sort_order ASC, value ASC
            ''',
            (current_value,),
        ).fetchall()
    return db.execute(
        '''
        SELECT id, value, label
        FROM source_type_dictionary
        WHERE is_active = 1
        ORDER BY sort_order ASC, value ASC
        '''
    ).fetchall()


def fetch_office_type_options(db: sqlite3.Connection, current_value: str | None = None) -> list[tuple[str, str]]:
    rows = db.execute(
        '''
        SELECT value, label
        FROM office_type_dictionary
        WHERE is_active = 1 OR value = ?
        ORDER BY sort_order ASC, value ASC
        ''',
        (current_value or '',),
    ).fetchall()
    options: list[tuple[str, str]] = [('', '- brak -')]
    options.extend((row['value'], row['label'] or row['value']) for row in rows if row['value'])
    return options


def bibliography_item_type_options() -> list[tuple[str, str]]:
    return [
        ('book', 'książka'),
        ('article', 'artykuł'),
        ('edition', 'edycja źródłowa'),
        ('chapter', 'rozdział'),
        ('other', 'inne'),
    ]


def fetch_person_options(db: sqlite3.Connection) -> list[sqlite3.Row]:
    return db.execute(
        '''
        SELECT id, canonical_name, display_name
        FROM person
        ORDER BY canonical_name ASC
        '''
    ).fetchall()


def fetch_office_options_for_person(
    db: sqlite3.Connection,
    person_id: int | None,
    current_value: str | None = None,
) -> list[dict[str, str]]:
    options: list[dict[str, str]] = []
    if person_id is not None:
        rows = db.execute(
            '''
            SELECT DISTINCT office_name
            FROM office_term
            WHERE person_id = ? AND office_name IS NOT NULL AND office_name != ''
            ORDER BY office_name
            ''',
            (person_id,),
        ).fetchall()
        options = [{'value': row['office_name'], 'label': row['office_name']} for row in rows]
    if current_value and all(option['value'] != current_value for option in options):
        options.append({'value': current_value, 'label': current_value})
    return options


def fetch_reference_mentions(db: sqlite3.Connection) -> list[sqlite3.Row]:
    return db.execute(
        '''
        SELECT id, COALESCE(archive_signature, 'bez sygnatury') AS archive_signature,
               COALESCE(year_label, '—') AS year_label
        FROM reference_mention
        ORDER BY COALESCE(year_label, ''), id
        '''
    ).fetchall()


def historical_date_kind_options() -> list[str]:
    return ['exact', 'circa', 'before', 'after']


def historical_date_certainty_options() -> list[str]:
    return ['certain', 'probable', 'uncertain']


def empty_historical_date_form() -> dict[str, str]:
    return {
        'date_kind': 'exact',
        'start_date_iso': '',
        'end_date_iso': '',
        'display_label': '',
        'sort_key_start': '',
        'sort_key_end': '',
        'certainty': 'certain',
        'comment': '',
    }


def historical_date_form_data_from_mapping(data: Any) -> dict[str, str]:
    def read(key: str) -> str:
        value = data.get(key, '')
        return value.strip() if isinstance(value, str) else str(value).strip()

    sort_key_start = read('sort_key_start')
    sort_key_end = read('sort_key_end')
    if sort_key_start and not sort_key_end:
        sort_key_end = sort_key_start
    start_date_iso = read('start_date_iso') or sort_key_start
    end_date_iso = read('end_date_iso') or sort_key_end or sort_key_start

    return {
        'date_kind': read('date_kind') or 'exact',
        'start_date_iso': start_date_iso,
        'end_date_iso': end_date_iso,
        'display_label': read('display_label'),
        'sort_key_start': sort_key_start,
        'sort_key_end': sort_key_end,
        'certainty': read('certainty') or 'certain',
        'comment': read('comment'),
    }


def historical_date_form_data_from_row(row: sqlite3.Row) -> dict[str, str]:
    return {
        'date_kind': row['date_kind'],
        'start_date_iso': row['start_date_iso'] or '',
        'end_date_iso': row['end_date_iso'] or '',
        'display_label': row['display_label'] or '',
        'sort_key_start': row['sort_key_start'] or '',
        'sort_key_end': row['sort_key_end'] or '',
        'certainty': row['certainty'] or 'certain',
        'comment': row['comment'] or '',
    }


def validate_historical_date_form(form_data: dict[str, str]) -> list[str]:
    errors: list[str] = []
    date_kind = form_data['date_kind']
    sort_start = form_data['sort_key_start']
    sort_end = form_data['sort_key_end'] or sort_start
    if date_kind not in set(historical_date_kind_options()):
        errors.append('Wybrano nieprawidłowy typ daty historycznej.')
    if form_data['certainty'] not in set(historical_date_certainty_options()):
        errors.append('Wybrano nieprawidłowy stopień pewności daty.')
    if not form_data['display_label']:
        errors.append('Pole "Etykieta wyświetlana" jest wymagane.')
    if not sort_start:
        errors.append('Pole "Klucz sortowania od" jest wymagane.')
    for label, value in [('Klucz sortowania od', sort_start), ('Klucz sortowania do', sort_end)]:
        if value and not is_iso_date(value):
            errors.append(f'{label} musi mieć format YYYY-MM-DD.')
    if sort_start and sort_end and sort_start > sort_end:
        errors.append('Klucz sortowania początkowego nie może być późniejszy niż końcowy.')
    return errors


def is_iso_date(value: str) -> bool:
    return bool(re.fullmatch(r'\d{4}-\d{2}-\d{2}', value))


def normalized_historical_date_data(form_data: dict[str, str]) -> dict[str, str | None]:
    sort_key_start = form_data['sort_key_start'] or None
    sort_key_end = form_data['sort_key_end'] or form_data['sort_key_start'] or None
    return {
        'date_kind': form_data['date_kind'],
        'start_date_iso': sort_key_start,
        'end_date_iso': sort_key_end,
        'display_label': form_data['display_label'],
        'sort_key_start': sort_key_start,
        'sort_key_end': sort_key_end,
        'certainty': form_data['certainty'],
        'comment': form_data['comment'] or None,
    }


def insert_historical_date(db: sqlite3.Connection, form_data: dict[str, str]) -> int:
    normalized = normalized_historical_date_data(form_data)
    cur = db.execute(
        '''
        INSERT INTO historical_date (
            uuid, date_kind, start_date_iso, end_date_iso, display_label,
            sort_key_start, sort_key_end, certainty, comment
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            str(uuid.uuid4()),
            normalized['date_kind'],
            normalized['start_date_iso'],
            normalized['end_date_iso'],
            normalized['display_label'],
            normalized['sort_key_start'],
            normalized['sort_key_end'],
            normalized['certainty'],
            normalized['comment'],
        ),
    )
    db.commit()
    return int(cur.lastrowid)


def update_historical_date(db: sqlite3.Connection, date_id: int, form_data: dict[str, str]) -> None:
    normalized = normalized_historical_date_data(form_data)
    db.execute(
        '''
        UPDATE historical_date
        SET date_kind = ?,
            start_date_iso = ?,
            end_date_iso = ?,
            display_label = ?,
            sort_key_start = ?,
            sort_key_end = ?,
            certainty = ?,
            comment = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        ''',
        (
            normalized['date_kind'],
            normalized['start_date_iso'],
            normalized['end_date_iso'],
            normalized['display_label'],
            normalized['sort_key_start'],
            normalized['sort_key_end'],
            normalized['certainty'],
            normalized['comment'],
            date_id,
        ),
    )
    db.commit()


def get_default_user_id(db: sqlite3.Connection) -> int | None:
    row = db.execute('SELECT id FROM app_user ORDER BY id ASC LIMIT 1').fetchone()
    return row['id'] if row else None


DATE_LINK_TARGETS: dict[str, dict[str, Any]] = {
    'person': {'table': 'person', 'fields': {'birth_date_id', 'death_date_id'}},
    'embassy': {'table': 'embassy', 'fields': {
        'appointment_date_id',
        'arrival_in_rome_date_id',
        'audience_date_id',
        'departure_from_rome_date_id',
        'return_to_poland_date_id',
    }},
    'office_term': {'table': 'office_term', 'fields': {'start_date_id', 'end_date_id'}},
    'curia_presence': {'table': 'curia_presence', 'fields': {'start_date_id', 'end_date_id'}},
    'source_text': {'table': 'source_text', 'fields': {'source_date_id'}},
}


DATE_USAGE_FIELDS: tuple[tuple[str, str], ...] = (
    ('person', 'birth_date_id'),
    ('person', 'death_date_id'),
    ('office_term', 'start_date_id'),
    ('office_term', 'end_date_id'),
    ('curia_presence', 'start_date_id'),
    ('curia_presence', 'end_date_id'),
    ('reference_mention', 'mention_date_id'),
    ('embassy', 'appointment_date_id'),
    ('embassy', 'arrival_in_rome_date_id'),
    ('embassy', 'audience_date_id'),
    ('embassy', 'departure_from_rome_date_id'),
    ('embassy', 'return_to_poland_date_id'),
    ('source_text', 'source_date_id'),
)


def form_value(name: str) -> str:
    return request.form.get(name, '').strip()


def parse_optional_int(value: str) -> int | None:
    return int(value) if value else None


def date_label(date_id: int | None) -> str:
    if not date_id:
        return 'brak'
    row = g.db.execute('SELECT date_kind, display_label FROM historical_date WHERE id = ?', (date_id,)).fetchone()
    return prefixed_date_label(row['date_kind'], row['display_label']) if row else 'brak'


def validate_date_link_target(entity_type: str, record_id_raw: str, field_name: str) -> tuple[str, int, str] | str:
    entity = DATE_LINK_TARGETS.get(entity_type)
    if not entity:
        return 'Nieprawidłowy typ encji dla pola daty.'
    if field_name not in entity['fields']:
        return 'Nieprawidłowe pole daty.'
    try:
        record_id = int(str(record_id_raw).strip())
    except ValueError:
        return 'Nieprawidłowy identyfikator rekordu.'
    row = g.db.execute(f'SELECT id FROM {entity["table"]} WHERE id = ?', (record_id,)).fetchone()
    if not row:
        return 'Nie znaleziono rekordu powiązanego z datą.'
    return entity_type, record_id, field_name


def set_linked_date(db: sqlite3.Connection, entity_type: str, record_id: int, field_name: str, date_id: int | None) -> None:
    table = DATE_LINK_TARGETS[entity_type]['table']
    db.execute(
        f'UPDATE {table} SET {field_name} = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?',
        (date_id, record_id),
    )
    db.commit()


def count_historical_date_usages(db: sqlite3.Connection, date_id: int) -> int:
    total = 0
    for table, field in DATE_USAGE_FIELDS:
        total += db.execute(f'SELECT COUNT(*) FROM {table} WHERE {field} = ?', (date_id,)).fetchone()[0]
    return total


def empty_person_form() -> dict[str, str]:
    return {
        'canonical_name': '',
        'birth_date_id': '',
        'death_date_id': '',
        'education_note': '',
        'additional_information': '',
    }


def person_form_data_from_request() -> dict[str, str]:
    return {
        'canonical_name': form_value('canonical_name'),
        'birth_date_id': form_value('birth_date_id'),
        'death_date_id': form_value('death_date_id'),
        'education_note': form_value('education_note'),
        'additional_information': form_value('additional_information'),
    }


def person_form_data_from_row(person: sqlite3.Row) -> dict[str, str]:
    return {
        'canonical_name': person['canonical_name'] or '',
        'birth_date_id': str(person['birth_date_id'] or ''),
        'death_date_id': str(person['death_date_id'] or ''),
        'education_note': person['education_note'] or '',
        'additional_information': person['general_biographical_note'] or person['research_note'] or '',
    }


def validate_person_form(form_data: dict[str, str]) -> list[str]:
    errors: list[str] = []
    if not form_data['canonical_name']:
        errors.append('Pole "Nazwa kanoniczna" jest wymagane.')
    return errors


def insert_person(db: sqlite3.Connection, form_data: dict[str, str]) -> int:
    cur = db.execute(
        '''
        INSERT INTO person (
            uuid, canonical_name, display_name, gender, birth_date_id, death_date_id,
            education_note, activity_note, general_biographical_note, research_note, created_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            str(uuid.uuid4()),
            form_data['canonical_name'],
            form_data['canonical_name'],
            None,
            parse_optional_int(form_data['birth_date_id']),
            parse_optional_int(form_data['death_date_id']),
            form_data['education_note'] or None,
            None,
            form_data['additional_information'] or None,
            None,
            get_default_user_id(db),
        ),
    )
    db.commit()
    return int(cur.lastrowid)


def update_person(db: sqlite3.Connection, person_id: int, form_data: dict[str, str]) -> None:
    db.execute(
        '''
        UPDATE person
        SET canonical_name = ?,
            display_name = ?,
            gender = ?,
            birth_date_id = ?,
            death_date_id = ?,
            education_note = ?,
            general_biographical_note = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        ''',
        (
            form_data['canonical_name'],
            form_data['canonical_name'],
            None,
            parse_optional_int(form_data['birth_date_id']),
            parse_optional_int(form_data['death_date_id']),
            form_data['education_note'] or None,
            form_data['additional_information'] or None,
            person_id,
        ),
    )
    db.commit()


def empty_embassy_form() -> dict[str, str]:
    return {
        'title': '',
        'year_label': '',
        'appointment_date_id': '',
        'arrival_in_rome_date_id': '',
        'audience_date_id': '',
        'departure_from_rome_date_id': '',
        'return_to_poland_date_id': '',
        'mission_subject': '',
        'description_text': '',
        'notes_text': '',
    }


def embassy_form_data_from_request(existing_row: sqlite3.Row | None = None) -> dict[str, str]:
    existing_notes_text = ''
    if existing_row is not None:
        existing_notes_text = existing_row['notes_text'] or ''
    return {
        'title': form_value('title'),
        'year_label': form_value('year_label'),
        'appointment_date_id': form_value('appointment_date_id'),
        'arrival_in_rome_date_id': form_value('arrival_in_rome_date_id'),
        'audience_date_id': form_value('audience_date_id'),
        'departure_from_rome_date_id': form_value('departure_from_rome_date_id'),
        'return_to_poland_date_id': form_value('return_to_poland_date_id'),
        'mission_subject': form_value('mission_subject'),
        'description_text': form_value('description_text'),
        'notes_text': existing_notes_text,
    }


def embassy_form_data_from_row(embassy: sqlite3.Row) -> dict[str, str]:
    return {
        'title': embassy['title'] or '',
        'year_label': embassy['year_label'] or '',
        'appointment_date_id': str(embassy['appointment_date_id'] or ''),
        'arrival_in_rome_date_id': str(embassy['arrival_in_rome_date_id'] or ''),
        'audience_date_id': str(embassy['audience_date_id'] or ''),
        'departure_from_rome_date_id': str(embassy['departure_from_rome_date_id'] or ''),
        'return_to_poland_date_id': str(embassy['return_to_poland_date_id'] or ''),
        'mission_subject': embassy['mission_subject'] or '',
        'description_text': embassy['description_text'] or '',
        'notes_text': embassy['notes_text'] or '',
    }


def validate_embassy_form(form_data: dict[str, str]) -> list[str]:
    errors: list[str] = []
    if not form_data['title']:
        errors.append('Pole "Tytuł poselstwa" jest wymagane.')
    return errors


def insert_embassy(db: sqlite3.Connection, form_data: dict[str, str]) -> int:
    cur = db.execute(
        '''
        INSERT INTO embassy (
            uuid, title, year_label, appointment_date_id, arrival_in_rome_date_id,
            audience_date_id, departure_from_rome_date_id, return_to_poland_date_id,
            mission_subject, description_text, notes_text, created_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            str(uuid.uuid4()),
            form_data['title'],
            form_data['year_label'] or None,
            parse_optional_int(form_data['appointment_date_id']),
            parse_optional_int(form_data['arrival_in_rome_date_id']),
            parse_optional_int(form_data['audience_date_id']),
            parse_optional_int(form_data['departure_from_rome_date_id']),
            parse_optional_int(form_data['return_to_poland_date_id']),
            form_data['mission_subject'] or None,
            form_data['description_text'] or None,
            form_data['notes_text'] or None,
            get_default_user_id(db),
        ),
    )
    db.commit()
    return int(cur.lastrowid)


def update_embassy(db: sqlite3.Connection, embassy_id: int, form_data: dict[str, str]) -> None:
    db.execute(
        '''
        UPDATE embassy
        SET title = ?,
            year_label = ?,
            appointment_date_id = ?,
            arrival_in_rome_date_id = ?,
            audience_date_id = ?,
            departure_from_rome_date_id = ?,
            return_to_poland_date_id = ?,
            mission_subject = ?,
            description_text = ?,
            notes_text = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        ''',
        (
            form_data['title'],
            form_data['year_label'] or None,
            parse_optional_int(form_data['appointment_date_id']),
            parse_optional_int(form_data['arrival_in_rome_date_id']),
            parse_optional_int(form_data['audience_date_id']),
            parse_optional_int(form_data['departure_from_rome_date_id']),
            parse_optional_int(form_data['return_to_poland_date_id']),
            form_data['mission_subject'] or None,
            form_data['description_text'] or None,
            form_data['notes_text'] or None,
            embassy_id,
        ),
    )
    db.commit()


def empty_office_term_form(person_id: int) -> dict[str, str]:
    return {
        'person_id': str(person_id),
        'office_name': '',
        'office_type': '',
        'source_designation': '',
        'start_date_id': '',
        'end_date_id': '',
        'bibliography_item_id': '',
        'note': '',
    }


def office_term_form_data_from_request(person_id: int) -> dict[str, str]:
    return {
        'person_id': str(person_id),
        'office_name': form_value('office_name'),
        'office_type': form_value('office_type'),
        'source_designation': form_value('source_designation'),
        'start_date_id': form_value('start_date_id'),
        'end_date_id': form_value('end_date_id'),
        'bibliography_item_id': form_value('bibliography_item_id'),
        'note': form_value('note'),
    }


def office_term_form_data_from_row(row: sqlite3.Row) -> dict[str, str]:
    return {
        'person_id': str(row['person_id']),
        'office_name': row['office_name'] or '',
        'office_type': row['office_type'] or '',
        'source_designation': row['source_designation'] or '',
        'start_date_id': str(row['start_date_id'] or ''),
        'end_date_id': str(row['end_date_id'] or ''),
        'bibliography_item_id': str(row['bibliography_item_id'] or ''),
        'note': row['comment'] or '',
    }


def validate_office_term_form(form_data: dict[str, str]) -> list[str]:
    errors: list[str] = []
    if not form_data['office_name']:
        errors.append('Pole "Nazwa urzędu / godności" jest wymagane.')
    allowed_types = {value for value, _ in fetch_office_type_options(g.db, form_data['office_type'] or None)}
    if form_data['office_type'] not in allowed_types:
        errors.append('Wybrano nieprawidłowy typ urzędu.')
    return errors


def insert_office_term(db: sqlite3.Connection, form_data: dict[str, str]) -> int:
    cur = db.execute(
        '''
        INSERT INTO office_term (
            uuid, person_id, office_name, office_type, function_name, source_designation,
            start_date_id, end_date_id, date_note, certainty, bibliography_item_id, comment
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            str(uuid.uuid4()),
            int(form_data['person_id']),
            form_data['office_name'],
            form_data['office_type'] or None,
            None,
            form_data['source_designation'] or None,
            parse_optional_int(form_data['start_date_id']),
            parse_optional_int(form_data['end_date_id']),
            None,
            'certain',
            parse_optional_int(form_data['bibliography_item_id']),
            form_data['note'] or None,
        ),
    )
    db.commit()
    return int(cur.lastrowid)


def update_office_term(db: sqlite3.Connection, office_id: int, form_data: dict[str, str]) -> None:
    db.execute(
        '''
        UPDATE office_term
        SET office_name = ?,
            office_type = ?,
            function_name = ?,
            source_designation = ?,
            start_date_id = ?,
            end_date_id = ?,
            date_note = ?,
            certainty = ?,
            bibliography_item_id = ?,
            comment = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        ''',
        (
            form_data['office_name'],
            form_data['office_type'] or None,
            None,
            form_data['source_designation'] or None,
            parse_optional_int(form_data['start_date_id']),
            parse_optional_int(form_data['end_date_id']),
            None,
            'certain',
            parse_optional_int(form_data['bibliography_item_id']),
            form_data['note'] or None,
            office_id,
        ),
    )
    db.commit()


def empty_biography_note_form(person_id: int) -> dict[str, str]:
    return {
        'person_id': str(person_id),
        'bibliography_item_id': '',
        'reference_locator': '',
        'footnote_text': '',
        'biography_text': '',
        'sort_order': '0',
    }


def biography_note_form_data_from_request(person_id: int) -> dict[str, str]:
    return {
        'person_id': str(person_id),
        'bibliography_item_id': form_value('bibliography_item_id'),
        'reference_locator': form_value('reference_locator'),
        'footnote_text': form_value('footnote_text'),
        'biography_text': form_value('biography_text'),
        'sort_order': '0',
    }


def biography_note_form_data_from_row(row: sqlite3.Row) -> dict[str, str]:
    return {
        'person_id': str(row['person_id']),
        'bibliography_item_id': str(row['bibliography_item_id'] or ''),
        'reference_locator': row['reference_locator'] or '',
        'footnote_text': row['footnote_text'] or '',
        'biography_text': row['biography_text'] or '',
        'sort_order': str(row['sort_order'] if row['sort_order'] is not None else 0),
    }


def validate_biography_note_form(form_data: dict[str, str]) -> list[str]:
    errors: list[str] = []
    if not form_data['biography_text']:
        errors.append('Pole "Tekst biogramu" jest wymagane.')
    return errors


def insert_biography_note(db: sqlite3.Connection, form_data: dict[str, str]) -> int:
    cur = db.execute(
        '''
        INSERT INTO biography_note (
            uuid, person_id, bibliography_item_id, reference_locator, footnote_text, biography_text, sort_order
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            str(uuid.uuid4()),
            int(form_data['person_id']),
            parse_optional_int(form_data['bibliography_item_id']),
            form_data['reference_locator'] or None,
            form_data['footnote_text'] or None,
            form_data['biography_text'],
            int(form_data['sort_order']),
        ),
    )
    db.commit()
    return int(cur.lastrowid)


def update_biography_note(db: sqlite3.Connection, bio_id: int, form_data: dict[str, str]) -> None:
    db.execute(
        '''
        UPDATE biography_note
        SET bibliography_item_id = ?,
            reference_locator = ?,
            footnote_text = ?,
            biography_text = ?,
            sort_order = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        ''',
        (
            parse_optional_int(form_data['bibliography_item_id']),
            form_data['reference_locator'] or None,
            form_data['footnote_text'] or None,
            form_data['biography_text'],
            int(form_data['sort_order']),
            bio_id,
        ),
    )
    db.commit()


def empty_embassy_participant_form(embassy_id: int) -> dict[str, str]:
    return {
        'embassy_id': str(embassy_id),
        'person_id': '',
        'role_in_embassy': '',
        'office_during_embassy': '',
        'source_designation_latin': '',
        'source_designation_polish': '',
        'note': '',
    }


def embassy_participant_form_data_from_request(embassy_id: int) -> dict[str, str]:
    return {
        'embassy_id': str(embassy_id),
        'person_id': form_value('person_id'),
        'role_in_embassy': form_value('role_in_embassy'),
        'office_during_embassy': form_value('office_during_embassy'),
        'source_designation_latin': form_value('source_designation_latin'),
        'source_designation_polish': form_value('source_designation_polish'),
        'note': form_value('note'),
    }


def embassy_participant_form_data_from_row(row: sqlite3.Row) -> dict[str, str]:
    return {
        'embassy_id': str(row['embassy_id']),
        'person_id': str(row['person_id']),
        'role_in_embassy': row['role_in_embassy'] or '',
        'office_during_embassy': row['office_during_embassy'] or '',
        'source_designation_latin': row['source_designation_latin'] or '',
        'source_designation_polish': row['source_designation_polish'] or '',
        'note': row['representation_note'] or row['comment'] or '',
    }


def validate_embassy_participant_form(form_data: dict[str, str], current_id: int | None = None) -> list[str]:
    errors: list[str] = []
    if not form_data['person_id']:
        errors.append('Należy wybrać osobę.')
    duplicate_errors = ensure_unique_embassy_participant(
        g.db,
        int(form_data['embassy_id']),
        parse_optional_int(form_data['person_id']),
        form_data['role_in_embassy'] or None,
        None,
        current_id=current_id,
    )
    errors.extend(duplicate_errors)
    person_id = parse_optional_int(form_data['person_id'])
    office_value = form_data['office_during_embassy']
    if office_value and person_id is not None:
        valid_offices = {option['value'] for option in fetch_office_options_for_person(g.db, person_id, office_value)}
        if office_value not in valid_offices:
            errors.append('Wybrano nieprawidłowy urząd dla wskazanej osoby.')
    return errors


def ensure_unique_embassy_participant(
    db: sqlite3.Connection,
    embassy_id: int,
    person_id: int | None,
    role_in_embassy: str | None,
    rank_order: int | None,
    current_id: int | None = None,
) -> list[str]:
    if person_id is None:
        return []
    row = db.execute(
        '''
        SELECT id
        FROM embassy_participant
        WHERE embassy_id = ?
          AND person_id = ?
          AND role_in_embassy IS ?
          AND rank_order IS ?
        ''',
        (embassy_id, person_id, role_in_embassy, rank_order),
    ).fetchone()
    if row and row['id'] != current_id:
        return ['Taki wpis uczestnika już istnieje dla tego poselstwa.']
    return []


def insert_embassy_participant(db: sqlite3.Connection, form_data: dict[str, str]) -> int:
    cur = db.execute(
        '''
        INSERT INTO embassy_participant (
            uuid, embassy_id, person_id, role_in_embassy, participant_category, rank_order,
            office_during_embassy, function_during_embassy, source_designation_latin,
            source_designation_polish, representation_note, comment
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            str(uuid.uuid4()),
            int(form_data['embassy_id']),
            int(form_data['person_id']),
            form_data['role_in_embassy'] or None,
            None,
            None,
            form_data['office_during_embassy'] or None,
            None,
            form_data['source_designation_latin'] or None,
            form_data['source_designation_polish'] or None,
            form_data['note'] or None,
            None,
        ),
    )
    db.commit()
    return int(cur.lastrowid)


def update_embassy_participant(db: sqlite3.Connection, participant_id: int, form_data: dict[str, str]) -> None:
    db.execute(
        '''
        UPDATE embassy_participant
        SET person_id = ?,
            role_in_embassy = ?,
            participant_category = ?,
            rank_order = ?,
            office_during_embassy = ?,
            function_during_embassy = ?,
            source_designation_latin = ?,
            source_designation_polish = ?,
            representation_note = ?,
            comment = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        ''',
        (
            int(form_data['person_id']),
            form_data['role_in_embassy'] or None,
            None,
            None,
            form_data['office_during_embassy'] or None,
            None,
            form_data['source_designation_latin'] or None,
            form_data['source_designation_polish'] or None,
            form_data['note'] or None,
            None,
            participant_id,
        ),
    )
    db.commit()


def empty_person_name_variant_form(person_id: int) -> dict[str, str]:
    return {
        'person_id': str(person_id),
        'variant_text': '',
        'language_code': '',
        'note': '',
    }


def person_name_variant_form_data_from_request(person_id: int) -> dict[str, str]:
    return {
        'person_id': str(person_id),
        'variant_text': form_value('variant_text'),
        'language_code': form_value('language_code'),
        'note': form_value('note'),
    }


def person_name_variant_form_data_from_row(row: sqlite3.Row) -> dict[str, str]:
    return {
        'person_id': str(row['person_id']),
        'variant_text': row['variant_text'] or '',
        'language_code': row['language_code'] or '',
        'note': row['note'] or '',
    }


def person_name_variant_language_options() -> list[tuple[str, str]]:
    return [
        ('', '- brak -'),
        ('łacina', 'łacina'),
        ('polski', 'polski'),
        ('niemiecki', 'niemiecki'),
        ('ruski', 'ruski'),
        ('włoski', 'włoski'),
    ]


def validate_person_name_variant_form(form_data: dict[str, str]) -> list[str]:
    errors: list[str] = []
    if not form_data['variant_text']:
        errors.append('Pole "Wariant nazwy" jest wymagane.')
    allowed_languages = {value for value, _ in person_name_variant_language_options()}
    if form_data['language_code'] not in allowed_languages:
        errors.append('Wybrano nieprawidłowy język wariantu nazwy.')
    return errors


def insert_person_name_variant(db: sqlite3.Connection, form_data: dict[str, str]) -> int:
    cur = db.execute(
        '''
        INSERT INTO person_name_variant (
            uuid, person_id, variant_text, language_code, normalized_form, is_primary, note
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            str(uuid.uuid4()),
            int(form_data['person_id']),
            form_data['variant_text'],
            form_data['language_code'] or None,
            None,
            0,
            form_data['note'] or None,
        ),
    )
    db.commit()
    return int(cur.lastrowid)


def update_person_name_variant(db: sqlite3.Connection, variant_id: int, form_data: dict[str, str]) -> None:
    db.execute(
        '''
        UPDATE person_name_variant
        SET variant_text = ?,
            language_code = ?,
            normalized_form = ?,
            is_primary = ?,
            note = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        ''',
        (
            form_data['variant_text'],
            form_data['language_code'] or None,
            None,
            0,
            form_data['note'] or None,
            variant_id,
        ),
    )
    db.commit()


def empty_curia_presence_form(person_id: int) -> dict[str, str]:
    return {
        'person_id': str(person_id),
        'start_date_id': '',
        'end_date_id': '',
        'year_label': '',
        'place_name': '',
        'presence_type': '',
        'mention_type': '',
        'office_at_curia': '',
        'reference_mention_id': '',
        'scholarly_comment': '',
        'working_comment': '',
    }


def curia_presence_form_data_from_request(person_id: int) -> dict[str, str]:
    return {
        'person_id': str(person_id),
        'start_date_id': form_value('start_date_id'),
        'end_date_id': form_value('end_date_id'),
        'year_label': form_value('year_label'),
        'place_name': form_value('place_name'),
        'presence_type': form_value('presence_type'),
        'mention_type': form_value('mention_type'),
        'office_at_curia': form_value('office_at_curia'),
        'reference_mention_id': form_value('reference_mention_id'),
        'scholarly_comment': form_value('scholarly_comment'),
        'working_comment': form_value('working_comment'),
    }


def curia_presence_form_data_from_row(row: sqlite3.Row) -> dict[str, str]:
    return {
        'person_id': str(row['person_id']),
        'start_date_id': str(row['start_date_id'] or ''),
        'end_date_id': str(row['end_date_id'] or ''),
        'year_label': row['year_label'] or '',
        'place_name': row['place_name'] or '',
        'presence_type': row['presence_type'] or '',
        'mention_type': row['mention_type'] or '',
        'office_at_curia': row['office_at_curia'] or '',
        'reference_mention_id': str(row['reference_mention_id'] or ''),
        'scholarly_comment': row['scholarly_comment'] or '',
        'working_comment': row['working_comment'] or '',
    }


def validate_curia_presence_form(form_data: dict[str, str]) -> list[str]:
    if not (form_data['place_name'] or form_data['presence_type'] or form_data['office_at_curia']):
        return ['Wpis obecności powinien zawierać przynajmniej miejsce, charakter obecności albo urząd przy Kurii.']
    return []


def insert_curia_presence(db: sqlite3.Connection, form_data: dict[str, str]) -> int:
    cur = db.execute(
        '''
        INSERT INTO curia_presence (
            uuid, person_id, start_date_id, end_date_id, year_label, place_name, presence_type,
            mention_type, office_at_curia, reference_mention_id, scholarly_comment, working_comment
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            str(uuid.uuid4()),
            int(form_data['person_id']),
            parse_optional_int(form_data['start_date_id']),
            parse_optional_int(form_data['end_date_id']),
            form_data['year_label'] or None,
            form_data['place_name'] or None,
            form_data['presence_type'] or None,
            form_data['mention_type'] or None,
            form_data['office_at_curia'] or None,
            parse_optional_int(form_data['reference_mention_id']),
            form_data['scholarly_comment'] or None,
            form_data['working_comment'] or None,
        ),
    )
    db.commit()
    return int(cur.lastrowid)


def update_curia_presence(db: sqlite3.Connection, presence_id: int, form_data: dict[str, str]) -> None:
    db.execute(
        '''
        UPDATE curia_presence
        SET start_date_id = ?,
            end_date_id = ?,
            year_label = ?,
            place_name = ?,
            presence_type = ?,
            mention_type = ?,
            office_at_curia = ?,
            reference_mention_id = ?,
            scholarly_comment = ?,
            working_comment = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        ''',
        (
            parse_optional_int(form_data['start_date_id']),
            parse_optional_int(form_data['end_date_id']),
            form_data['year_label'] or None,
            form_data['place_name'] or None,
            form_data['presence_type'] or None,
            form_data['mention_type'] or None,
            form_data['office_at_curia'] or None,
            parse_optional_int(form_data['reference_mention_id']),
            form_data['scholarly_comment'] or None,
            form_data['working_comment'] or None,
            presence_id,
        ),
    )
    db.commit()


def empty_embassy_bibliography_form(embassy_id: int) -> dict[str, str]:
    return {
        'embassy_id': str(embassy_id),
        'bibliography_item_id': '',
        'page_range': '',
        'comment': '',
    }


def embassy_bibliography_form_data_from_request(embassy_id: int) -> dict[str, str]:
    return {
        'embassy_id': str(embassy_id),
        'bibliography_item_id': form_value('bibliography_item_id'),
        'page_range': form_value('page_range'),
        'comment': form_value('comment'),
    }


def embassy_bibliography_form_data_from_row(row: sqlite3.Row) -> dict[str, str]:
    return {
        'embassy_id': str(row['embassy_id']),
        'bibliography_item_id': str(row['bibliography_item_id']),
        'page_range': row['page_range'] or '',
        'comment': row['comment'] or '',
    }


def validate_embassy_bibliography_form(form_data: dict[str, str], current_id: int | None = None) -> list[str]:
    errors: list[str] = []
    if not form_data['bibliography_item_id']:
        errors.append('Należy wybrać pozycję bibliograficzną.')
    if form_data['bibliography_item_id']:
        row = g.db.execute(
            'SELECT id FROM embassy_bibliography WHERE embassy_id = ? AND bibliography_item_id = ?',
            (int(form_data['embassy_id']), int(form_data['bibliography_item_id'])),
        ).fetchone()
        if row and row['id'] != current_id:
            errors.append('Ta pozycja bibliograficzna jest już przypisana do poselstwa.')
    return errors


def insert_embassy_bibliography(db: sqlite3.Connection, form_data: dict[str, str]) -> int:
    cur = db.execute(
        '''
        INSERT INTO embassy_bibliography (uuid, embassy_id, bibliography_item_id, page_range, comment)
        VALUES (?, ?, ?, ?, ?)
        ''',
        (
            str(uuid.uuid4()),
            int(form_data['embassy_id']),
            int(form_data['bibliography_item_id']),
            form_data['page_range'] or None,
            form_data['comment'] or None,
        ),
    )
    db.commit()
    return int(cur.lastrowid)


def update_embassy_bibliography(db: sqlite3.Connection, entry_id: int, form_data: dict[str, str]) -> None:
    db.execute(
        '''
        UPDATE embassy_bibliography
        SET bibliography_item_id = ?,
            page_range = ?,
            comment = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        ''',
        (
            int(form_data['bibliography_item_id']),
            form_data['page_range'] or None,
            form_data['comment'] or None,
            entry_id,
        ),
    )
    db.commit()


def empty_bibliography_item_form() -> dict[str, str]:
    return {
        'item_type': 'book',
        'short_citation': '',
        'author_text': '',
        'editor_text': '',
        'title': '',
        'publication_place': '',
        'publisher_text': '',
        'publication_year': '',
        'volume_text': '',
        'series_text': '',
        'access_text': '',
        'note': '',
    }


def bibliography_item_form_data_from_mapping(data: Any) -> dict[str, str]:
    def read(key: str) -> str:
        value = data.get(key, '')
        if value is None:
            return ''
        return str(value).strip()

    item_type = read('item_type') or 'book'
    return {
        'item_type': item_type,
        'short_citation': read('short_citation'),
        'author_text': read('author_text'),
        'editor_text': read('editor_text'),
        'title': read('title'),
        'publication_place': read('publication_place'),
        'publisher_text': read('publisher_text'),
        'publication_year': read('publication_year'),
        'volume_text': read('volume_text'),
        'series_text': read('series_text'),
        'access_text': read('access_text'),
        'note': read('note'),
    }


def bibliography_item_form_data_from_row(row: sqlite3.Row) -> dict[str, str]:
    return {
        'item_type': row['item_type'] or 'book',
        'short_citation': row['short_citation'] or '',
        'author_text': row['author_text'] or '',
        'editor_text': row['editor_text'] or '',
        'title': row['title'] or '',
        'publication_place': row['publication_place'] or '',
        'publisher_text': row['publisher_text'] or '',
        'publication_year': row['publication_year'] or '',
        'volume_text': row['volume_text'] or '',
        'series_text': row['series_text'] or '',
        'access_text': row['access_text'] or '',
        'note': row['note'] or '',
    }


def validate_bibliography_item_form(form_data: dict[str, str], current_id: int | None = None) -> list[str]:
    errors: list[str] = []
    valid_item_types = {value for value, _ in bibliography_item_type_options()}
    if form_data['item_type'] not in valid_item_types:
        errors.append('Wybrano nieprawidłowy typ opracowania.')
    if not form_data['short_citation']:
        errors.append('Pole "Skrót cytowania" jest wymagane.')
    if form_data['publication_year'] and not re.fullmatch(r'\d{1,4}', form_data['publication_year']):
        errors.append('Pole "Rok wydania" powinno zawierać sam rok.')
    if form_data['short_citation']:
        row = g.db.execute(
            'SELECT id FROM bibliography_item WHERE short_citation = ?',
            (form_data['short_citation'],),
        ).fetchone()
        if row and row['id'] != current_id:
            errors.append('Pozycja o takim skrócie cytowania już istnieje.')
    return errors


def insert_bibliography_item(db: sqlite3.Connection, form_data: dict[str, str]) -> int:
    cur = db.execute(
        '''
        INSERT INTO bibliography_item (
            uuid, item_type, short_citation, full_citation, author_text, editor_text,
            title, publication_place, publisher_text, publication_year, volume_text, series_text, access_text, note
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            str(uuid.uuid4()),
            form_data['item_type'],
            form_data['short_citation'],
            None,
            form_data['author_text'] or None,
            form_data['editor_text'] or None,
            form_data['title'] or None,
            form_data['publication_place'] or None,
            form_data['publisher_text'] or None,
            form_data['publication_year'] or None,
            form_data['volume_text'] or None,
            form_data['series_text'] or None,
            form_data['access_text'] or None,
            form_data['note'] or None,
        ),
    )
    db.commit()
    return int(cur.lastrowid)


def update_bibliography_item(db: sqlite3.Connection, item_id: int, form_data: dict[str, str]) -> None:
    db.execute(
        '''
        UPDATE bibliography_item
        SET item_type = ?,
            short_citation = ?,
            full_citation = ?,
            author_text = ?,
            editor_text = ?,
            title = ?,
            publication_place = ?,
            publisher_text = ?,
            publication_year = ?,
            volume_text = ?,
            series_text = ?,
            access_text = ?,
            note = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        ''',
        (
            form_data['item_type'],
            form_data['short_citation'],
            None,
            form_data['author_text'] or None,
            form_data['editor_text'] or None,
            form_data['title'] or None,
            form_data['publication_place'] or None,
            form_data['publisher_text'] or None,
            form_data['publication_year'] or None,
            form_data['volume_text'] or None,
            form_data['series_text'] or None,
            form_data['access_text'] or None,
            form_data['note'] or None,
            item_id,
        ),
    )
    db.commit()


def empty_source_type_form() -> dict[str, str]:
    return {
        'name': '',
    }


def source_type_form_data_from_mapping(data: Any) -> dict[str, str]:
    def read(key: str) -> str:
        value = data.get(key, '')
        if value is None:
            return ''
        return str(value).strip()

    return {
        'name': read('name'),
    }


def source_type_form_data_from_row(row: sqlite3.Row) -> dict[str, str]:
    return {
        'name': row['label'] or row['value'] or '',
    }


def validate_source_type_form(form_data: dict[str, str], current_id: int | None = None) -> list[str]:
    errors: list[str] = []
    if not form_data['name']:
        errors.append('Pole "Nazwa" jest wymagane.')
    row = g.db.execute('SELECT id FROM source_type_dictionary WHERE value = ?', (form_data['name'],)).fetchone()
    if row and row['id'] != current_id:
        errors.append('Typ źródła o takiej nazwie już istnieje.')
    return errors


def insert_source_type(db: sqlite3.Connection, form_data: dict[str, str]) -> int:
    max_sort_order = db.execute('SELECT COALESCE(MAX(sort_order), 0) FROM source_type_dictionary').fetchone()[0]
    cur = db.execute(
        '''
        INSERT INTO source_type_dictionary (uuid, value, label, sort_order, is_active)
        VALUES (?, ?, ?, ?, ?)
        ''',
        (
            str(uuid.uuid4()),
            form_data['name'],
            form_data['name'],
            int(max_sort_order) + 10,
            1,
        ),
    )
    db.commit()
    return int(cur.lastrowid)


def update_source_type(db: sqlite3.Connection, type_id: int, form_data: dict[str, str], old_value: str) -> None:
    row = db.execute('SELECT sort_order FROM source_type_dictionary WHERE id = ?', (type_id,)).fetchone()
    sort_order = row['sort_order'] if row else 0
    db.execute(
        '''
        UPDATE source_type_dictionary
        SET value = ?,
            label = ?,
            sort_order = ?,
            is_active = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        ''',
        (
            form_data['name'],
            form_data['name'],
            sort_order,
            1,
            type_id,
        ),
    )
    if form_data['name'] != old_value:
        db.execute('UPDATE source_text SET source_type = ?, updated_at = CURRENT_TIMESTAMP WHERE source_type = ?', (form_data['name'], old_value))
    db.commit()


def empty_office_type_form() -> dict[str, str]:
    return {
        'name': '',
    }


def office_type_form_data_from_mapping(data: Any) -> dict[str, str]:
    value = data.get('name', '')
    if value is None:
        value = ''
    return {
        'name': str(value).strip(),
    }


def office_type_form_data_from_row(row: sqlite3.Row) -> dict[str, str]:
    return {
        'name': row['label'] or row['value'] or '',
    }


def validate_office_type_form(form_data: dict[str, str], current_id: int | None = None) -> list[str]:
    errors: list[str] = []
    if not form_data['name']:
        errors.append('Pole "Nazwa" jest wymagane.')
    row = g.db.execute('SELECT id FROM office_type_dictionary WHERE value = ?', (form_data['name'],)).fetchone()
    if row and row['id'] != current_id:
        errors.append('Typ urzędu o takiej nazwie już istnieje.')
    return errors


def insert_office_type(db: sqlite3.Connection, form_data: dict[str, str]) -> int:
    max_sort_order = db.execute('SELECT COALESCE(MAX(sort_order), 0) FROM office_type_dictionary').fetchone()[0]
    cur = db.execute(
        '''
        INSERT INTO office_type_dictionary (uuid, value, label, sort_order, is_active)
        VALUES (?, ?, ?, ?, ?)
        ''',
        (
            str(uuid.uuid4()),
            form_data['name'],
            form_data['name'],
            int(max_sort_order) + 10,
            1,
        ),
    )
    db.commit()
    return int(cur.lastrowid)


def update_office_type(db: sqlite3.Connection, type_id: int, form_data: dict[str, str], old_value: str) -> None:
    row = db.execute('SELECT sort_order FROM office_type_dictionary WHERE id = ?', (type_id,)).fetchone()
    sort_order = row['sort_order'] if row else 0
    db.execute(
        '''
        UPDATE office_type_dictionary
        SET value = ?,
            label = ?,
            sort_order = ?,
            is_active = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        ''',
        (
            form_data['name'],
            form_data['name'],
            sort_order,
            1,
            type_id,
        ),
    )
    if form_data['name'] != old_value:
        db.execute(
            'UPDATE office_term SET office_type = ?, updated_at = CURRENT_TIMESTAMP WHERE office_type = ?',
            (form_data['name'], old_value),
        )
    db.commit()


def empty_source_text_form(embassy_id: int) -> dict[str, str]:
    return {
        'embassy_id': str(embassy_id),
        'bibliography_item_id': '',
        'source_type': '',
        'archive_signature': '',
        'edition_label': '',
        'source_date_id': '',
        'original_language_code': 'la',
        'translation_language_code': 'pl',
        'original_text_full': '',
        'polish_text_full': '',
        'editorial_note': '',
    }


def source_text_form_data_from_request(embassy_id: int, existing_row: sqlite3.Row | None = None) -> dict[str, str]:
    existing_bibliography_item_id = ''
    existing_source_date_id = ''
    existing_original_language_code = 'la'
    existing_translation_language_code = 'pl'
    if existing_row is not None:
        existing_bibliography_item_id = str(existing_row['bibliography_item_id'] or '')
        existing_source_date_id = str(existing_row['source_date_id'] or '')
        existing_original_language_code = existing_row['original_language_code'] or 'la'
        existing_translation_language_code = existing_row['translation_language_code'] or 'pl'
    return {
        'embassy_id': str(embassy_id),
        'bibliography_item_id': form_value('bibliography_item_id') or existing_bibliography_item_id,
        'source_type': form_value('source_type'),
        'archive_signature': form_value('archive_signature'),
        'edition_label': form_value('edition_label'),
        'source_date_id': form_value('source_date_id') or existing_source_date_id,
        'original_language_code': form_value('original_language_code') or existing_original_language_code,
        'translation_language_code': form_value('translation_language_code') or existing_translation_language_code,
        'original_text_full': request.form.get('original_text_full', '').strip(),
        'polish_text_full': request.form.get('polish_text_full', '').strip(),
        'editorial_note': request.form.get('editorial_note', '').strip(),
    }


def source_text_form_data_from_row(row: sqlite3.Row) -> dict[str, str]:
    return {
        'embassy_id': str(row['embassy_id']),
        'bibliography_item_id': str(row['bibliography_item_id'] or ''),
        'source_type': row['source_type'] or '',
        'archive_signature': row['archive_signature'] or '',
        'edition_label': row['edition_label'] or '',
        'source_date_id': str(row['source_date_id'] or ''),
        'original_language_code': row['original_language_code'] or 'la',
        'translation_language_code': row['translation_language_code'] or 'pl',
        'original_text_full': row['original_text_full'] or '',
        'polish_text_full': row['polish_text_full'] or '',
        'editorial_note': row['editorial_note'] or '',
    }


def validate_source_text_form(form_data: dict[str, str]) -> list[str]:
    errors: list[str] = []
    if not form_data['source_type']:
        errors.append('Pole "Typ źródła" jest wymagane.')
    valid_source_types = {row['value'] for row in fetch_source_type_options(g.db)}
    if form_data['source_type'] and form_data['source_type'] not in valid_source_types:
        errors.append('Wybrano nieprawidłowy typ źródła.')
    return errors


def insert_source_text(db: sqlite3.Connection, form_data: dict[str, str]) -> int:
    cur = db.execute(
        '''
        INSERT INTO source_text (
            uuid, embassy_id, bibliography_item_id, source_type, archive_signature, edition_label,
            source_date_id, original_language_code, translation_language_code, original_text_full,
            polish_text_full, editorial_note
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            str(uuid.uuid4()),
            int(form_data['embassy_id']),
            parse_optional_int(form_data['bibliography_item_id']),
            form_data['source_type'],
            form_data['archive_signature'] or None,
            form_data['edition_label'] or None,
            parse_optional_int(form_data['source_date_id']),
            form_data['original_language_code'],
            form_data['translation_language_code'],
            form_data['original_text_full'] or None,
            form_data['polish_text_full'] or None,
            form_data['editorial_note'] or None,
        ),
    )
    source_id = int(cur.lastrowid)
    sync_source_segments_from_full_text(
        db,
        source_id,
        form_data['original_text_full'],
        form_data['polish_text_full'],
    )
    db.commit()
    return source_id


def update_source_text(db: sqlite3.Connection, source_id: int, form_data: dict[str, str]) -> None:
    db.execute(
        '''
        UPDATE source_text
        SET bibliography_item_id = ?,
            source_type = ?,
            archive_signature = ?,
            edition_label = ?,
            source_date_id = ?,
            original_language_code = ?,
            translation_language_code = ?,
            original_text_full = ?,
            polish_text_full = ?,
            editorial_note = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        ''',
        (
            parse_optional_int(form_data['bibliography_item_id']),
            form_data['source_type'],
            form_data['archive_signature'] or None,
            form_data['edition_label'] or None,
            parse_optional_int(form_data['source_date_id']),
            form_data['original_language_code'],
            form_data['translation_language_code'],
            form_data['original_text_full'] or None,
            form_data['polish_text_full'] or None,
            form_data['editorial_note'] or None,
            source_id,
        ),
    )
    sync_source_segments_from_full_text(
        db,
        source_id,
        form_data['original_text_full'],
        form_data['polish_text_full'],
    )
    db.commit()


def split_text_into_paragraphs(text: str) -> list[str]:
    cleaned = (text or '').strip()
    if not cleaned:
        return []
    parts = re.split(r'\n\s*\n+', cleaned)
    paragraphs: list[str] = []
    for part in parts:
        lines = [line.strip() for line in part.splitlines()]
        paragraph = '\n'.join(line for line in lines if line)
        if paragraph:
            paragraphs.append(paragraph)
    return paragraphs


def sync_source_segments_from_full_text(
    db: sqlite3.Connection,
    source_id: int,
    original_text_full: str,
    polish_text_full: str,
) -> bool:
    changed = False
    original_paragraphs = split_text_into_paragraphs(original_text_full)
    polish_paragraphs = split_text_into_paragraphs(polish_text_full)
    paragraph_count = max(len(original_paragraphs), len(polish_paragraphs))
    existing_segments = {
        row['segment_no']: row
        for row in db.execute(
            'SELECT * FROM source_segment WHERE source_text_id = ? ORDER BY segment_no ASC',
            (source_id,),
        ).fetchall()
    }
    active_segment_ids: set[int] = set()
    for index in range(paragraph_count):
        segment_no = index + 1
        original_segment = original_paragraphs[index] if index < len(original_paragraphs) else None
        polish_segment = polish_paragraphs[index] if index < len(polish_paragraphs) else None
        existing = existing_segments.get(segment_no)
        if existing:
            active_segment_ids.add(int(existing['id']))
            if (
                (existing['original_segment'] or None) != original_segment
                or (existing['polish_segment'] or None) != polish_segment
            ):
                db.execute(
                    '''
                    UPDATE source_segment
                    SET original_segment = ?,
                        polish_segment = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    ''',
                    (original_segment, polish_segment, existing['id']),
                )
                changed = True
        else:
            cur = db.execute(
                '''
                INSERT INTO source_segment (
                    uuid, source_text_id, segment_no, original_segment, polish_segment, alignment_group, comment
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    str(uuid.uuid4()),
                    source_id,
                    segment_no,
                    original_segment,
                    polish_segment,
                    None,
                    None,
                ),
            )
            active_segment_ids.add(int(cur.lastrowid))
            changed = True
    segments_to_delete = [
        int(row['id'])
        for row in existing_segments.values()
        if int(row['id']) not in active_segment_ids
    ]
    if segments_to_delete:
        placeholders = ', '.join('?' for _ in segments_to_delete)
        db.execute(
            f'DELETE FROM theme_annotation WHERE source_text_id = ? AND source_segment_id IN ({placeholders})',
            (source_id, *segments_to_delete),
        )
        db.execute(
            f'DELETE FROM source_segment WHERE id IN ({placeholders})',
            tuple(segments_to_delete),
        )
        changed = True
    return changed


def fetch_active_themes(db: sqlite3.Connection, selected_id: str | None = None) -> list[sqlite3.Row]:
    rows = db.execute(
        '''
        SELECT id, name, color_code
        FROM theme
        WHERE is_active = 1
           OR id = COALESCE(?, -1)
        ORDER BY name ASC
        ''',
        (parse_optional_int(selected_id),),
    ).fetchall()
    return rows


def theme_color_to_rgba(color_code: str | None, alpha: float) -> str:
    color = color_code or '#7a5c3e'
    if not re.fullmatch(r'#[0-9A-Fa-f]{6}', color):
        color = '#7a5c3e'
    red = int(color[1:3], 16)
    green = int(color[3:5], 16)
    blue = int(color[5:7], 16)
    return f'rgba({red}, {green}, {blue}, {alpha:.2f})'


def render_segment_with_annotations(text: str | None, annotations: list[sqlite3.Row] | None) -> Markup:
    raw_text = text or ''
    if not raw_text:
        return Markup('—')
    if not annotations:
        return Markup(escape(raw_text).replace('\n', Markup('<br>')))
    ordered_annotations = sorted(
        [
            ann for ann in annotations
            if ann['char_start'] is not None and ann['char_end'] is not None and ann['char_end'] > ann['char_start']
        ],
        key=lambda ann: (int(ann['char_start']), int(ann['char_end']), int(ann['id'])),
    )
    fragments: list[Markup] = []
    cursor = 0
    for ann in ordered_annotations:
        start = max(0, min(len(raw_text), int(ann['char_start'])))
        end = max(start, min(len(raw_text), int(ann['char_end'])))
        if start < cursor:
            continue
        if start > cursor:
            fragments.append(Markup(escape(raw_text[cursor:start])))
        selected = escape(raw_text[start:end])
        style = (
            f'background: {theme_color_to_rgba(ann["color_code"], 0.18)}; '
            f'border-bottom: 3px solid {ann["color_code"] or "#7a5c3e"};'
        )
        title = escape(ann['theme_name'] or 'Motyw')
        fragments.append(Markup(f'<span class="text-annotation-highlight" style="{style}" title="{title}">{selected}</span>'))
        cursor = end
    if cursor < len(raw_text):
        fragments.append(Markup(escape(raw_text[cursor:])))
    return Markup('').join(fragments).replace('\n', Markup('<br>'))


def slugify_theme_name(value: str) -> str:
    normalized = value.strip().lower()
    normalized = normalized.replace('ą', 'a').replace('ć', 'c').replace('ę', 'e').replace('ł', 'l')
    normalized = normalized.replace('ń', 'n').replace('ó', 'o').replace('ś', 's').replace('ż', 'z').replace('ź', 'z')
    normalized = re.sub(r'[^a-z0-9]+', '-', normalized)
    normalized = normalized.strip('-')
    return normalized or 'motyw'


def empty_theme_form() -> dict[str, str]:
    return {
        'name': '',
        'description_text': '',
        'color_code': '#7a5c3e',
    }


def theme_form_data_from_mapping(data: Any) -> dict[str, str]:
    def read(key: str) -> str:
        value = data.get(key, '')
        if value is None:
            return ''
        return str(value).strip()

    return {
        'name': read('name'),
        'description_text': read('description_text'),
        'color_code': read('color_code') or '#7a5c3e',
    }


def theme_form_data_from_row(row: sqlite3.Row) -> dict[str, str]:
    return {
        'name': row['name'] or '',
        'description_text': row['description_text'] or '',
        'color_code': row['color_code'] or '#7a5c3e',
    }


def validate_theme_form(form_data: dict[str, str], current_id: int | None = None) -> list[str]:
    errors: list[str] = []
    if not form_data['name']:
        errors.append('Pole "Nazwa" jest wymagane.')
    row = g.db.execute('SELECT id FROM theme WHERE name = ?', (form_data['name'],)).fetchone()
    if row and row['id'] != current_id:
        errors.append('Motyw o takiej nazwie już istnieje.')
    if form_data['color_code'] and not re.fullmatch(r'#[0-9A-Fa-f]{6}', form_data['color_code']):
        errors.append('Kolor powinien mieć postać #RRGGBB.')
    slug = slugify_theme_name(form_data['name'])
    slug_row = g.db.execute('SELECT id FROM theme WHERE slug = ?', (slug,)).fetchone()
    if slug_row and slug_row['id'] != current_id:
        errors.append('Motyw o takiej nazwie tworzy już używany identyfikator wewnętrzny. Wybierz inną nazwę.')
    return errors


def insert_theme(db: sqlite3.Connection, form_data: dict[str, str]) -> int:
    cur = db.execute(
        '''
        INSERT INTO theme (uuid, name, slug, description_text, color_code, is_active)
        VALUES (?, ?, ?, ?, ?, ?)
        ''',
        (
            str(uuid.uuid4()),
            form_data['name'],
            slugify_theme_name(form_data['name']),
            form_data['description_text'] or None,
            form_data['color_code'] or None,
            1,
        ),
    )
    db.commit()
    return int(cur.lastrowid)


def update_theme(db: sqlite3.Connection, theme_id: int, form_data: dict[str, str]) -> None:
    db.execute(
        '''
        UPDATE theme
        SET name = ?,
            slug = ?,
            description_text = ?,
            color_code = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        ''',
        (
            form_data['name'],
            slugify_theme_name(form_data['name']),
            form_data['description_text'] or None,
            form_data['color_code'] or None,
            theme_id,
        ),
    )
    db.commit()


def theme_annotation_snapshot_for_segment(segment: sqlite3.Row | None, language_code: str) -> str:
    if not segment:
        return ''
    if language_code == 'la':
        return (segment['original_segment'] or '').strip()
    return (segment['polish_segment'] or '').strip()


def empty_theme_annotation_form(
    source_id: int,
    segment_id: int,
    language_code: str,
    segment: sqlite3.Row | None,
) -> dict[str, str]:
    text_value = theme_annotation_snapshot_for_segment(segment, language_code)
    return {
        'source_text_id': str(source_id),
        'source_segment_id': str(segment_id),
        'theme_id': '',
        'text_language_code': language_code,
        'annotated_text_snapshot': text_value,
        'char_start': '0' if text_value else '',
        'char_end': str(len(text_value)) if text_value else '',
        'comment': '',
    }


def theme_annotation_form_data_from_request(
    source_id: int,
    segment_id: int,
    segment: sqlite3.Row | None,
) -> dict[str, str]:
    language_code = request.form.get('text_language_code', 'pl').strip().lower()
    if language_code not in {'la', 'pl'}:
        language_code = 'pl'
    snapshot = request.form.get('annotated_text_snapshot', '').strip()
    char_start = request.form.get('char_start', '').strip()
    char_end = request.form.get('char_end', '').strip()
    if not snapshot:
        snapshot = theme_annotation_snapshot_for_segment(segment, language_code)
    return {
        'source_text_id': str(source_id),
        'source_segment_id': str(segment_id) if segment_id else '',
        'theme_id': form_value('theme_id'),
        'text_language_code': language_code,
        'annotated_text_snapshot': snapshot,
        'char_start': char_start,
        'char_end': char_end,
        'comment': request.form.get('comment', '').strip(),
    }


def theme_annotation_form_data_from_row(annotation: sqlite3.Row, segment: sqlite3.Row | None) -> dict[str, str]:
    language_code = annotation['text_language_code'] or 'pl'
    return {
        'source_text_id': str(annotation['source_text_id']),
        'source_segment_id': str(annotation['source_segment_id'] or ''),
        'theme_id': str(annotation['theme_id'] or ''),
        'text_language_code': language_code,
        'annotated_text_snapshot': annotation['annotated_text_snapshot'] or theme_annotation_snapshot_for_segment(segment, language_code),
        'char_start': str(annotation['char_start'] if annotation['char_start'] is not None else ''),
        'char_end': str(annotation['char_end'] if annotation['char_end'] is not None else ''),
        'comment': annotation['comment'] or '',
    }


def validate_theme_annotation_form(form_data: dict[str, str]) -> list[str]:
    errors: list[str] = []
    if not form_data['theme_id']:
        errors.append('Pole "Motyw" jest wymagane.')
    valid_theme_ids = {str(row['id']) for row in fetch_active_themes(g.db, form_data['theme_id'])}
    if form_data['theme_id'] and form_data['theme_id'] not in valid_theme_ids:
        errors.append('Wybrano nieprawidłowy motyw.')
    if form_data['text_language_code'] not in {'la', 'pl'}:
        errors.append('Wybrano nieprawidłowy język fragmentu.')
    if not form_data['annotated_text_snapshot']:
        errors.append('Nie udało się odczytać treści akapitu do oznaczenia.')
    try:
        char_start = int(form_data['char_start'])
        char_end = int(form_data['char_end'])
    except (TypeError, ValueError):
        errors.append('Nie udało się odczytać zakresu zaznaczenia.')
        return errors
    if char_start < 0 or char_end <= char_start:
        errors.append('Zakres zaznaczenia jest nieprawidłowy.')
    return errors


def insert_theme_annotation(db: sqlite3.Connection, form_data: dict[str, str]) -> int:
    cur = db.execute(
        '''
        INSERT INTO theme_annotation (
            uuid, theme_id, source_text_id, source_segment_id, text_language_code,
            char_start, char_end, annotated_text_snapshot, comment, created_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            str(uuid.uuid4()),
            int(form_data['theme_id']),
            int(form_data['source_text_id']),
            parse_optional_int(form_data['source_segment_id']),
            form_data['text_language_code'],
            int(form_data['char_start']),
            int(form_data['char_end']),
            form_data['annotated_text_snapshot'],
            form_data['comment'] or None,
            None,
        ),
    )
    db.commit()
    return int(cur.lastrowid)


def update_theme_annotation(db: sqlite3.Connection, annotation_id: int, form_data: dict[str, str]) -> None:
    db.execute(
        '''
        UPDATE theme_annotation
        SET theme_id = ?,
            source_segment_id = ?,
            text_language_code = ?,
            char_start = ?,
            char_end = ?,
            annotated_text_snapshot = ?,
            comment = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        ''',
        (
            int(form_data['theme_id']),
            parse_optional_int(form_data['source_segment_id']),
            form_data['text_language_code'],
            int(form_data['char_start']),
            int(form_data['char_end']),
            form_data['annotated_text_snapshot'],
            form_data['comment'] or None,
            annotation_id,
        ),
    )
    db.commit()


def empty_source_segment_form(source_id: int) -> dict[str, str]:
    return {
        'source_text_id': str(source_id),
        'segment_no': '',
        'original_segment': '',
        'polish_segment': '',
        'alignment_group': '',
        'comment': '',
    }


def source_segment_form_data_from_request(source_id: int) -> dict[str, str]:
    return {
        'source_text_id': str(source_id),
        'segment_no': form_value('segment_no'),
        'original_segment': request.form.get('original_segment', '').strip(),
        'polish_segment': request.form.get('polish_segment', '').strip(),
        'alignment_group': form_value('alignment_group'),
        'comment': request.form.get('comment', '').strip(),
    }


def source_segment_form_data_from_row(row: sqlite3.Row) -> dict[str, str]:
    return {
        'source_text_id': str(row['source_text_id']),
        'segment_no': str(row['segment_no']),
        'original_segment': row['original_segment'] or '',
        'polish_segment': row['polish_segment'] or '',
        'alignment_group': row['alignment_group'] or '',
        'comment': row['comment'] or '',
    }


def validate_source_segment_form(form_data: dict[str, str], current_id: int | None = None) -> list[str]:
    errors: list[str] = []
    if not form_data['segment_no']:
        errors.append('Pole "Numer segmentu" jest wymagane.')
    else:
        try:
            segment_no = int(form_data['segment_no'])
        except ValueError:
            errors.append('Pole "Numer segmentu" musi być liczbą całkowitą.')
        else:
            row = g.db.execute(
                'SELECT id FROM source_segment WHERE source_text_id = ? AND segment_no = ?',
                (int(form_data['source_text_id']), segment_no),
            ).fetchone()
            if row and row['id'] != current_id:
                errors.append('Segment o takim numerze już istnieje w tym źródle.')
    if not (form_data['original_segment'] or form_data['polish_segment']):
        errors.append('Segment powinien zawierać tekst oryginalny lub polski.')
    return errors


def insert_source_segment(db: sqlite3.Connection, form_data: dict[str, str]) -> int:
    cur = db.execute(
        '''
        INSERT INTO source_segment (
            uuid, source_text_id, segment_no, original_segment, polish_segment, alignment_group, comment
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            str(uuid.uuid4()),
            int(form_data['source_text_id']),
            int(form_data['segment_no']),
            form_data['original_segment'] or None,
            form_data['polish_segment'] or None,
            form_data['alignment_group'] or None,
            form_data['comment'] or None,
        ),
    )
    db.commit()
    return int(cur.lastrowid)


def update_source_segment(db: sqlite3.Connection, segment_id: int, form_data: dict[str, str]) -> None:
    db.execute(
        '''
        UPDATE source_segment
        SET segment_no = ?,
            original_segment = ?,
            polish_segment = ?,
            alignment_group = ?,
            comment = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        ''',
        (
            int(form_data['segment_no']),
            form_data['original_segment'] or None,
            form_data['polish_segment'] or None,
            form_data['alignment_group'] or None,
            form_data['comment'] or None,
            segment_id,
        ),
    )
    db.commit()


if __name__ == '__main__':
    app = create_app()
    app.run(debug=True)
