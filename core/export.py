"""
Building a plain-text file of a work for the reader to keep.

Plain UTF-8 text rather than PDF: it opens anywhere, keeps Arabic and Urdu
intact without font embedding or right-to-left shaping problems, and stays
readable if a reader pastes it elsewhere.
"""

from .templatetags.qasida_extras import stanza_rows

# Query parameter -> (row key, heading shown above the layer)
LAYERS = {
    'original': ('original', None),
    'latin': ('latin', 'Transliteration'),
    'translation': ('translation', 'Translation'),
}

RULE = '=' * 60


def available_layers(qasida):
    """Which layers this work actually has, in reading order."""
    present = ['original']
    if qasida.transliteration:
        present.append('latin')
    if qasida.translation:
        present.append('translation')
    return present


def build_text(qasida, layers):
    """
    Render the requested layers as text.

    Stanzas are kept together with their transliteration and translation where
    those line up, so the file reads the way the page does.
    """
    wanted = [name for name in ('original', 'latin', 'translation')
              if name in layers and name in available_layers(qasida)]
    if not wanted:
        wanted = ['original']

    lines = [qasida.title or 'Untitled']
    if qasida.arabic_title:
        lines.append(qasida.arabic_title)
    if qasida.author:
        lines.append(f"by {qasida.author}")
    meta = []
    if qasida.language:
        meta.append(qasida.language)
    if qasida.collection:
        part = f", part {qasida.collection_position}" if qasida.collection_position else ''
        meta.append(f"{qasida.collection.name}{part}")
    if meta:
        lines.append(' · '.join(meta))
    lines.append(RULE)
    lines.append('')

    rows = stanza_rows(qasida)
    paired = set()
    for index, row in enumerate(rows):
        blocks = []
        for name in wanted:
            key, heading = LAYERS[name]
            text = row.get(key) or ''
            if not text.strip():
                continue
            paired.add(name)
            # Only label a layer when more than one is present, otherwise the
            # heading is noise.
            if heading and len(wanted) > 1:
                blocks.append(f"[{heading}]\n{text}")
            else:
                blocks.append(text)
        if blocks:
            lines.append('\n\n'.join(blocks))
            if index < len(rows) - 1:
                lines.append('')

    # A layer laid out differently to the verses cannot be paired stanza by
    # stanza, so it is appended whole rather than dropped.
    for name in wanted:
        if name in paired or name == 'original':
            continue
        whole = getattr(qasida, 'transliteration' if name == 'latin' else 'translation', '')
        if whole.strip():
            _, heading = LAYERS[name]
            lines += ['', RULE, heading or name.title(), '', whole.strip()]

    lines += ['', RULE]
    if qasida.source_url:
        lines.append(f"Source: {qasida.source_url}")
    if qasida.translation and qasida.translation_origin == 'machine':
        lines.append("The translation was produced by machine and may be inaccurate.")
    if qasida.text_quality != 'ok':
        lines.append("This text was reconstructed automatically and may contain errors.")

    return '\n'.join(lines).strip() + '\n'


def filename_for(qasida, layers):
    """A filename that says which layers are inside."""
    stem = (qasida.slug or f'qasida-{qasida.pk}')[:100]
    extras = [name for name in ('latin', 'translation') if name in layers]
    if extras:
        stem = f"{stem}-with-{'-and-'.join(extras)}"
    return f"{stem}.txt"
