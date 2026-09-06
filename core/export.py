"""
Which layers of a work can be handed to the reader, and what they are called.

Kept apart from the PDF builder in core.pdf so that the qasida page, the
download form and the file itself all agree on the same three layers and the
same headings.
"""

# Query parameter -> (row key, heading shown above the layer)
LAYERS = {
    'original': ('original', None),
    'latin': ('latin', 'Transliteration'),
    'translation': ('translation', 'Translation'),
}


def available_layers(qasida):
    """Which layers this work actually has, in reading order."""
    present = ['original']
    if qasida.transliteration:
        present.append('latin')
    if qasida.translation:
        present.append('translation')
    return present
