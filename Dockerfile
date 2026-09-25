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

# Install dependencies.
#
# Torch is installed first, and from the CPU index, on purpose. Nothing here
# asks for it directly: argostranslate needs stanza for sentence segmentation,
# stanza requires torch, and on Linux pip resolves that to the CUDA build -
# dragging in cudnn, nccl, cusparselt, nvshmem and triton, about 1.7GB of GPU
# runtime that a CPU-only server can never execute. Satisfying the requirement
# with the CPU wheel first means the resolver never reaches for the CUDA one.
# Translation is unaffected: it was always running on the CPU.
COPY requirements.txt /app/
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements.txt

# The unprivileged account everything runs as. The workers feed crawled HTML,
# PDFs and images from other people's sites into BeautifulSoup, MuPDF, Pillow
# and Tesseract; a flaw in any of those should land in an account that owns
# nothing but the app, not in root.
#
# uid 1000 on purpose: it is the usual first user on a Linux host, so the
# development bind mount of the checkout stays writable. On the server the
# bind-mounted media and beat directories must be owned by it:
#   sudo chown -R 1000:1000 media beat
RUN useradd --create-home --uid 1000 app

# Install the Argos translation models at build time so translation works
# offline and identically in every container. Each package is ~100MB.
#
# Installed as the app user, because Argos keeps its models under the home
# directory of whoever installs them, and root's is not where the running
# process will look.
USER app
RUN python -c "\
import argostranslate.package as pkg; \
pkg.update_package_index(); \
available = pkg.get_available_packages(); \
wanted = [('ar','en'), ('ur','en'), ('fa','en')]; \
[pkg.install_from_path(p.download()) for p in available \
 if (p.from_code, p.to_code) in wanted]; \
print('argos models installed')"
USER root

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

# Copy project. Owned by the app user so collectstatic can write STATIC_ROOT
# at start-up.
COPY --chown=app:app . /app/
RUN mkdir -p /app/media /app/staticfiles /app/beat && chown app:app /app /app/media /app/staticfiles /app/beat

USER app
