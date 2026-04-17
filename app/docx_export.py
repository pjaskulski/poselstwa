from __future__ import annotations

import html
import re
import zipfile
from io import BytesIO

WORD_NAMESPACE = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'


def theme_docx_filename(theme_name: str, slug: str | None = None) -> str:
    base = (slug or slugify_for_filename(theme_name) or 'motyw').strip('-_')
    return f'motyw-{base}.docx'


def build_theme_docx(theme, annotations, segment_annotation_map: dict[int, dict[str, list]]) -> bytes:
    document_parts: list[str] = []
    document_parts.append(document_title_paragraph(f'Motyw: {theme["name"]}'))
    if theme['description_text']:
        document_parts.append(text_paragraph([plain_run(theme['description_text'], italic=True)], spacing_after=360))
    else:
        document_parts.append(text_paragraph([], spacing_after=360))

    if not annotations:
        document_parts.append(text_paragraph([plain_run('Brak oznaczeń dla tego motywu.')]))
    else:
        total = len(annotations)
        for index, row in enumerate(annotations, start=1):
            document_parts.extend(theme_annotation_block(theme, row, segment_annotation_map))
            if index < total:
                document_parts.append(separator_paragraph())
                document_parts.append(text_paragraph([]))

    document_xml = wrap_document(''.join(document_parts))

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', content_types_xml())
        archive.writestr('_rels/.rels', root_relationships_xml())
        archive.writestr('docProps/core.xml', core_properties_xml(theme['name']))
        archive.writestr('word/document.xml', document_xml)
    return buffer.getvalue()


def theme_annotation_block(theme, row, segment_annotation_map: dict[int, dict[str, list]]) -> list[str]:
    block: list[str] = []
    fragment_label = f'Akapit {row["segment_no"]}' if row['segment_no'] else 'Poziom całego tekstu'
    block.append(label_value_paragraph('Poselstwo: ', f'{row["embassy_title"] or "—"} ({row["year_label"] or "—"})'))
    block.append(label_value_paragraph('Edycja: ', row['edition_label'] or '—'))
    block.append(label_value_paragraph('Sygnatura: ', row['archive_signature'] or '—'))
    block.append(label_value_paragraph('Fragment: ', fragment_label))
    block.append(
        label_value_paragraph(
            'Zaznaczony fragment: ',
            row['annotated_text_snapshot'] or '—',
            indent=360,
            value_italic=True,
        )
    )
    if row['comment']:
        block.append(label_value_paragraph('Nota: ', row['comment'], indent=360))
    if row['source_segment_id']:
        per_language = segment_annotation_map.get(row['source_segment_id'], {})
        if row['original_segment']:
            block.append(label_value_paragraph('Tekst oryginalny: ', ''))
            block.append(
                highlighted_text_paragraph(
                    row['original_segment'],
                    per_language.get('la', []),
                    left_indent=360,
                )
            )
        if row['polish_segment']:
            block.append(label_value_paragraph('Tekst polski: ', ''))
            block.append(
                highlighted_text_paragraph(
                    row['polish_segment'],
                    per_language.get('pl', []),
                    left_indent=360,
                )
            )
    else:
        block.append(text_paragraph([plain_run('Brak podglądu akapitu dla oznaczenia na poziomie całego tekstu.', italic=True)], left_indent=360))
    return block


def highlighted_text_paragraph(text: str | None, annotations: list | None, left_indent: int = 0) -> str:
    raw_text = text or '—'
    if raw_text == '—':
        return text_paragraph([plain_run(raw_text)], left_indent=left_indent)
    if not annotations:
        return text_paragraph([plain_run(raw_text)], left_indent=left_indent)
    ordered_annotations = sorted(
        [
            ann for ann in annotations
            if ann['char_start'] is not None and ann['char_end'] is not None and ann['char_end'] > ann['char_start']
        ],
        key=lambda ann: (int(ann['char_start']), int(ann['char_end']), int(ann['id'])),
    )
    runs: list[str] = []
    cursor = 0
    for ann in ordered_annotations:
        start = max(0, min(len(raw_text), int(ann['char_start'])))
        end = max(start, min(len(raw_text), int(ann['char_end'])))
        if start < cursor:
            continue
        if start > cursor:
            runs.append(plain_run(raw_text[cursor:start]))
        runs.append(highlighted_run(raw_text[start:end], ann['color_code']))
        cursor = end
    if cursor < len(raw_text):
        runs.append(plain_run(raw_text[cursor:]))
    return text_paragraph(runs, left_indent=left_indent)


def heading_paragraph(text: str, level: int = 1) -> str:
    size = 32 if level == 1 else 24
    spacing_before = 0 if level == 1 else 120
    spacing_after = 120
    return text_paragraph(
        [plain_run(text, bold=True, size=size)],
        spacing_before=spacing_before,
        spacing_after=spacing_after,
    )


def document_title_paragraph(text: str) -> str:
    return text_paragraph(
        [plain_run(text, bold=True, size=40)],
        spacing_before=0,
        spacing_after=280,
        border_bottom=True,
    )


def label_value_paragraph(label: str, value: str, indent: int = 0, value_italic: bool = False) -> str:
    return text_paragraph(
        [
            plain_run(label, bold=True),
            plain_run(value or '—', italic=value_italic),
        ],
        left_indent=indent,
    )


def separator_paragraph() -> str:
    return text_paragraph([], spacing_before=120, spacing_after=120, border_bottom=True)


def text_paragraph(
    runs: list[str],
    *,
    spacing_before: int = 0,
    spacing_after: int = 120,
    left_indent: int = 0,
    border_bottom: bool = False,
) -> str:
    ppr_parts = [f'<w:spacing w:before="{spacing_before}" w:after="{spacing_after}"/>']
    if left_indent:
        ppr_parts.append(f'<w:ind w:left="{left_indent}"/>')
    if border_bottom:
        ppr_parts.append('<w:pBdr><w:bottom w:val="single" w:sz="6" w:space="1" w:color="B7B0A7"/></w:pBdr>')
    ppr_xml = f'<w:pPr>{"".join(ppr_parts)}</w:pPr>'
    content = ''.join(runs) if runs else ''
    return f'<w:p>{ppr_xml}{content}</w:p>'


def plain_run(text: str, *, bold: bool = False, italic: bool = False, size: int | None = None) -> str:
    return build_run(text, bold=bold, italic=italic, size=size)


def highlighted_run(text: str, color_code: str | None) -> str:
    stroke = normalize_hex_color(color_code)
    fill = light_fill_color(stroke)
    return build_run(text, shading=fill, underline_color=stroke)


def build_run(
    text: str,
    *,
    bold: bool = False,
    italic: bool = False,
    size: int | None = None,
    shading: str | None = None,
    underline_color: str | None = None,
) -> str:
    lines = text.split('\n')
    run_parts: list[str] = []
    if bold:
        run_parts.append('<w:b/>')
    if italic:
        run_parts.append('<w:i/>')
    if size is not None:
        run_parts.append(f'<w:sz w:val="{size}"/>')
    if shading:
        run_parts.append(f'<w:shd w:val="clear" w:color="auto" w:fill="{shading}"/>')
    if underline_color:
        run_parts.append(f'<w:u w:val="single" w:color="{underline_color}"/>')
    rpr_xml = f'<w:rPr>{"".join(run_parts)}</w:rPr>' if run_parts else ''
    body_parts: list[str] = []
    for index, line in enumerate(lines):
        if index > 0:
            body_parts.append('<w:br/>')
        body_parts.append(text_element(line))
    return f'<w:r>{rpr_xml}{"".join(body_parts)}</w:r>'


def text_element(text: str) -> str:
    escaped = html.escape(text or '')
    preserve = ' xml:space="preserve"' if needs_space_preservation(text) else ''
    return f'<w:t{preserve}>{escaped}</w:t>'


def needs_space_preservation(text: str) -> bool:
    return text.startswith(' ') or text.endswith(' ') or '  ' in text or '\t' in text


def normalize_hex_color(color_code: str | None) -> str:
    color = (color_code or '#7A5C3E').strip()
    if not re.fullmatch(r'#[0-9A-Fa-f]{6}', color):
        color = '#7A5C3E'
    return color[1:].upper()


def light_fill_color(hex_without_hash: str, ratio: float = 0.18) -> str:
    red = int(hex_without_hash[0:2], 16)
    green = int(hex_without_hash[2:4], 16)
    blue = int(hex_without_hash[4:6], 16)
    mix = lambda channel: round(255 * (1 - ratio) + channel * ratio)
    return f'{mix(red):02X}{mix(green):02X}{mix(blue):02X}'


def slugify_for_filename(value: str) -> str:
    normalized = value.strip().lower()
    replacements = {
        'ą': 'a',
        'ć': 'c',
        'ę': 'e',
        'ł': 'l',
        'ń': 'n',
        'ó': 'o',
        'ś': 's',
        'ż': 'z',
        'ź': 'z',
    }
    for source, target in replacements.items():
        normalized = normalized.replace(source, target)
    normalized = re.sub(r'[^a-z0-9]+', '-', normalized)
    return normalized.strip('-')


def wrap_document(body_xml: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{WORD_NAMESPACE}">'
        f'<w:body>{body_xml}{section_properties_xml()}</w:body>'
        '</w:document>'
    )


def section_properties_xml() -> str:
    return (
        '<w:sectPr>'
        '<w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="708" w:footer="708" w:gutter="0"/>'
        '</w:sectPr>'
    )


def content_types_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        '</Types>'
    )


def root_relationships_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
        '</Relationships>'
    )


def core_properties_xml(title: str) -> str:
    escaped_title = html.escape(title)
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cp:coreProperties '
        'xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:dcmitype="http://purl.org/dc/dcmitype/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f'<dc:title>Motyw: {escaped_title}</dc:title>'
        '<dc:creator>LegationesAdVaticanum</dc:creator>'
        '</cp:coreProperties>'
    )
