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

# Copy project
COPY . /app/
