PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS app_user (
    id INTEGER PRIMARY KEY,
    uuid TEXT NOT NULL UNIQUE,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    display_name TEXT,
    role TEXT NOT NULL DEFAULT 'editor',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS app_schema_version (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    version INTEGER NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO app_schema_version (id, version) VALUES (1, 1);

CREATE TABLE IF NOT EXISTS historical_date (
    id INTEGER PRIMARY KEY,
    uuid TEXT NOT NULL UNIQUE,
    date_kind TEXT NOT NULL CHECK (
        date_kind IN ('exact', 'month', 'year', 'range', 'circa', 'before', 'after')
    ),
    start_date_iso TEXT,
    end_date_iso TEXT,
    display_label TEXT NOT NULL,
    sort_key_start TEXT,
    sort_key_end TEXT,
    certainty TEXT DEFAULT 'certain' CHECK (
        certainty IN ('certain', 'probable', 'possible', 'uncertain')
    ),
    comment TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS bibliography_item (
    id INTEGER PRIMARY KEY,
    uuid TEXT NOT NULL UNIQUE,
    item_type TEXT DEFAULT 'book',
    short_citation TEXT NOT NULL,
    full_citation TEXT,
    author_text TEXT,
    editor_text TEXT,
    title TEXT,
    publication_place TEXT,
    publication_year TEXT,
    volume_text TEXT,
    series_text TEXT,
    access_text TEXT,
    note TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    publisher_text TEXT,
    journal_title TEXT,
    book_title TEXT
);

CREATE TABLE IF NOT EXISTS person (
    id INTEGER PRIMARY KEY,
    uuid TEXT NOT NULL UNIQUE,
    canonical_name TEXT NOT NULL,
    display_name TEXT,
    gender TEXT,
    birth_date_id INTEGER REFERENCES historical_date(id),
    death_date_id INTEGER REFERENCES historical_date(id),
    education_note TEXT,
    activity_note TEXT,
    general_biographical_note TEXT,
    research_note TEXT,
    created_by INTEGER REFERENCES app_user(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS embassy (
    id INTEGER PRIMARY KEY,
    uuid TEXT NOT NULL UNIQUE,
    title TEXT,
    year_label TEXT,
    appointment_date_id INTEGER REFERENCES historical_date(id),
    arrival_in_rome_date_id INTEGER REFERENCES historical_date(id),
    audience_date_id INTEGER REFERENCES historical_date(id),
    departure_from_rome_date_id INTEGER REFERENCES historical_date(id),
    return_to_poland_date_id INTEGER REFERENCES historical_date(id),
    mission_subject TEXT,
    description_text TEXT,
    notes_text TEXT,
    created_by INTEGER REFERENCES app_user(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS person_name_variant (
    id INTEGER PRIMARY KEY,
    uuid TEXT NOT NULL UNIQUE,
    person_id INTEGER NOT NULL REFERENCES person(id) ON DELETE CASCADE,
    variant_text TEXT NOT NULL,
    language_code TEXT,
    normalized_form TEXT,
    is_primary INTEGER NOT NULL DEFAULT 0,
    note TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS biography_note (
    id INTEGER PRIMARY KEY,
    uuid TEXT NOT NULL UNIQUE,
    person_id INTEGER NOT NULL REFERENCES person(id) ON DELETE CASCADE,
    bibliography_item_id INTEGER REFERENCES bibliography_item(id),
    footnote_text TEXT,
    biography_text TEXT NOT NULL,
    sort_order INTEGER DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reference_locator TEXT
);

CREATE TABLE IF NOT EXISTS reference_mention (
    id INTEGER PRIMARY KEY,
    uuid TEXT NOT NULL UNIQUE,
    archive_signature TEXT,
    mention_date_id INTEGER REFERENCES historical_date(id),
    year_label TEXT,
    abstract_text TEXT,
    topic_text TEXT,
    item_no TEXT,
    page_no TEXT,
    printed_version_bibliography_id INTEGER REFERENCES bibliography_item(id),
    description_text TEXT,
    source_type TEXT,
    text_excerpt TEXT,
    normalized_form TEXT,
    other_editions_text TEXT,
    working_note TEXT,
    needs_verification INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS office_term (
    id INTEGER PRIMARY KEY,
    uuid TEXT NOT NULL UNIQUE,
    person_id INTEGER NOT NULL REFERENCES person(id) ON DELETE CASCADE,
    office_name TEXT NOT NULL,
    office_type TEXT,
    function_name TEXT,
    source_designation TEXT,
    start_date_id INTEGER REFERENCES historical_date(id),
    end_date_id INTEGER REFERENCES historical_date(id),
    date_note TEXT,
    certainty TEXT DEFAULT 'certain' CHECK (
        certainty IN ('certain', 'probable', 'possible', 'uncertain')
    ),
    bibliography_item_id INTEGER REFERENCES bibliography_item(id),
    comment TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reference_locator TEXT
);

CREATE TABLE IF NOT EXISTS curia_presence (
    id INTEGER PRIMARY KEY,
    uuid TEXT NOT NULL UNIQUE,
    person_id INTEGER NOT NULL REFERENCES person(id) ON DELETE CASCADE,
    start_date_id INTEGER REFERENCES historical_date(id),
    end_date_id INTEGER REFERENCES historical_date(id),
    year_label TEXT,
    place_name TEXT,
    presence_type TEXT,
    mention_type TEXT,
    office_at_curia TEXT,
    reference_mention_id INTEGER REFERENCES reference_mention(id),
    scholarly_comment TEXT,
    working_comment TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    bibliography_item_id INTEGER,
    reference_locator TEXT,
    papal_register_text TEXT,
    note_text TEXT
);

CREATE TABLE IF NOT EXISTS embassy_participant (
    id INTEGER PRIMARY KEY,
    uuid TEXT NOT NULL UNIQUE,
    embassy_id INTEGER NOT NULL REFERENCES embassy(id) ON DELETE CASCADE,
    person_id INTEGER NOT NULL REFERENCES person(id) ON DELETE CASCADE,
    role_in_embassy TEXT,
    participant_category TEXT,
    rank_order INTEGER,
    office_during_embassy TEXT,
    function_during_embassy TEXT,
    source_designation_latin TEXT,
    source_designation_polish TEXT,
    representation_note TEXT,
    comment TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (embassy_id, person_id, role_in_embassy, rank_order)
);

CREATE TABLE IF NOT EXISTS embassy_bibliography (
    id INTEGER PRIMARY KEY,
    uuid TEXT NOT NULL UNIQUE,
    embassy_id INTEGER NOT NULL REFERENCES embassy(id) ON DELETE CASCADE,
    bibliography_item_id INTEGER NOT NULL REFERENCES bibliography_item(id) ON DELETE CASCADE,
    comment TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    page_range TEXT,
    UNIQUE (embassy_id, bibliography_item_id)
);

CREATE TABLE IF NOT EXISTS source_text (
    id INTEGER PRIMARY KEY,
    uuid TEXT NOT NULL UNIQUE,
    embassy_id INTEGER NOT NULL REFERENCES embassy(id) ON DELETE CASCADE,
    bibliography_item_id INTEGER REFERENCES bibliography_item(id),
    source_type TEXT NOT NULL,
    archive_signature TEXT,
    edition_label TEXT,
    source_date_id INTEGER REFERENCES historical_date(id),
    original_language_code TEXT DEFAULT 'la',
    translation_language_code TEXT DEFAULT 'pl',
    original_text_full TEXT,
    polish_text_full TEXT,
    editorial_note TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reference_locator TEXT
);

CREATE TABLE IF NOT EXISTS source_segment (
    id INTEGER PRIMARY KEY,
    uuid TEXT NOT NULL UNIQUE,
    source_text_id INTEGER NOT NULL REFERENCES source_text(id) ON DELETE CASCADE,
    segment_no INTEGER NOT NULL,
    original_segment TEXT,
    polish_segment TEXT,
    alignment_group TEXT,
    comment TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (source_text_id, segment_no)
);

CREATE TABLE IF NOT EXISTS theme (
    id INTEGER PRIMARY KEY,
    uuid TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL UNIQUE,
    slug TEXT NOT NULL UNIQUE,
    description_text TEXT,
    parent_theme_id INTEGER REFERENCES theme(id),
    color_code TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS theme_annotation (
    id INTEGER PRIMARY KEY,
    uuid TEXT NOT NULL UNIQUE,
    theme_id INTEGER NOT NULL REFERENCES theme(id) ON DELETE CASCADE,
    source_text_id INTEGER NOT NULL REFERENCES source_text(id) ON DELETE CASCADE,
    source_segment_id INTEGER REFERENCES source_segment(id) ON DELETE SET NULL,
    text_language_code TEXT NOT NULL DEFAULT 'pl',
    char_start INTEGER,
    char_end INTEGER,
    annotated_text_snapshot TEXT NOT NULL,
    comment TEXT,
    created_by INTEGER REFERENCES app_user(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS entity_revision (
    id INTEGER PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_uuid TEXT NOT NULL,
    version_no INTEGER NOT NULL,
    change_type TEXT NOT NULL CHECK (
        change_type IN ('insert', 'update', 'delete')
    ),
    payload_json TEXT NOT NULL,
    changed_by INTEGER REFERENCES app_user(id),
    changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (entity_type, entity_uuid, version_no)
);

CREATE TABLE IF NOT EXISTS source_type_dictionary (
    id INTEGER PRIMARY KEY,
    uuid TEXT NOT NULL UNIQUE,
    value TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS office_type_dictionary (
    id INTEGER PRIMARY KEY,
    uuid TEXT NOT NULL UNIQUE,
    value TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_person_canonical_name ON person(canonical_name);
CREATE INDEX IF NOT EXISTS idx_person_display_name ON person(display_name);
CREATE INDEX IF NOT EXISTS idx_embassy_year_label ON embassy(year_label);
CREATE INDEX IF NOT EXISTS idx_curia_presence_person ON curia_presence(person_id);
CREATE INDEX IF NOT EXISTS idx_embassy_participant_embassy ON embassy_participant(embassy_id);
CREATE INDEX IF NOT EXISTS idx_embassy_participant_person ON embassy_participant(person_id);
CREATE INDEX IF NOT EXISTS idx_reference_mention_signature ON reference_mention(archive_signature);
CREATE INDEX IF NOT EXISTS idx_source_text_embassy ON source_text(embassy_id);
CREATE INDEX IF NOT EXISTS idx_source_segment_source_text ON source_segment(source_text_id);
CREATE INDEX IF NOT EXISTS idx_theme_annotation_theme ON theme_annotation(theme_id);
CREATE INDEX IF NOT EXISTS idx_theme_annotation_source_text ON theme_annotation(source_text_id);
