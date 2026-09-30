# ---- the Arabic translation model -------------------------------------------
#
# OPUS-MT's large Arabic-to-English model (University of Helsinki, CC BY 4.0)
# reads classical verse far better than the small one Argos ships. It is
# published for PyTorch, and converted here for CTranslate2 - the inference
# engine Argos already brings - so the running image gains a 235MB model and
# not a single package: torch-for-conversion and transformers stay in this
# stage and are thrown away.
#
# Pinned three ways, because each has broken this before it was pinned: the
# model's revision; transformers below 5, which loads this model's weights
# wrongly and yields nonsense; and ctranslate2 to the version requirements.txt
# installs, so the converted model is one the runtime can read.
FROM python:3.12-slim-trixie AS opus-mt
RUN pip install --no-cache-dir torch==2.14.0 --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir transformers==4.57.6 sentencepiece==0.2.2 ctranslate2==4.8.2
RUN ct2-transformers-converter \
        --model Helsinki-NLP/opus-mt-tc-big-ar-en \
        --revision bcb4acd39ee8e3552e171653a8e31a10729b4330 \
        --output_dir /models/opus-mt-tc-big-ar-en \
        --quantization int8 \
        --copy_files source.spm target.spm \
    && rm -rf /root/.cache/huggingface


# ---- the application --------------------------------------------------------
#
# Pinned to the Debian release as well as the Python minor version, so a
# rebuild months from now gets the same system libraries (tesseract, the fonts)
# rather than whatever the floating tag has moved on to. Patch releases of
# both still arrive, which is what a rebuild is for.
FROM python:3.12-slim-trixie

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

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
#
# Pinned, because an unpinned torch is a few hundred megabytes that silently
# changes under every rebuild.
RUN pip install --no-cache-dir torch==2.14.0 --index-url https://download.pytorch.org/whl/cpu \
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

# Stanza, which Argos uses to split text into sentences, reads an index of its
# resources. The copy each Argos package ships is cut down - it lacks the
# "packages" section Stanza looks up - so Stanza fetches the full one from
# GitHub whenever a pipeline starts, and without a network every Urdu and
# Persian translation failed. The full index is fetched here, once, while the
# build has a network; the app then only ever reads it. See core/translating.py.
RUN python -c "\
import stanza; \
from argostranslate import package; \
[stanza.Pipeline(lang=p.from_code, dir=str(p.package_path / 'stanza'), processors='tokenize', \
  logging_level='WARNING', download_method=stanza.DownloadMethod.DOWNLOAD_RESOURCES) \
 for p in package.get_installed_packages() if (p.package_path / 'stanza').is_dir()]; \
print('stanza indexes ready')"
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

# The converted translation model from the stage above. See core/translating.py.
COPY --from=opus-mt /models /opt/translation-models

# Copy project. Owned by the app user so collectstatic can write STATIC_ROOT
# at start-up.
COPY --chown=app:app . /app/
RUN mkdir -p /app/media /app/staticfiles /app/beat && chown app:app /app /app/media /app/staticfiles /app/beat

USER app
