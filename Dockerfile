FROM python:3.12-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Set work directory
WORKDIR /app

# Tesseract with the Arabic language pack: several source PDFs position glyphs
# individually with kashida padding, which shatters text-layer extraction, so
# those pages are rasterised and read with OCR instead.
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-ara \
        tesseract-ocr-urd \
        tesseract-ocr-fas \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Install the Argos translation models at build time so translation works
# offline and identically in every container. Each package is ~100MB.
RUN python -c "\
import argostranslate.package as pkg; \
pkg.update_package_index(); \
available = pkg.get_available_packages(); \
wanted = [('ar','en'), ('ur','en'), ('fa','en')]; \
[pkg.install_from_path(p.download()) for p in available \
 if (p.from_code, p.to_code) in wanted]; \
print('argos models installed')"

# The font the downloadable PDFs are set in: Amiri, a naskh face drawn for
# classical Arabic, and one of the few that also carries the Urdu letters
# (ٹ ڈ ڑ ں ے). Without it MuPDF falls back to its own font, which still shapes
# and joins the text correctly but does not look like a book of poetry.
#
# Its own layer, and last, deliberately: putting it with the tesseract packages
# above would invalidate the pip install and the 300MB of translation models
# behind it every time this line is touched.
RUN apt-get update && apt-get install -y --no-install-recommends \
        fonts-hosny-amiri \
    && rm -rf /var/lib/apt/lists/*

# Copy project
COPY . /app/
