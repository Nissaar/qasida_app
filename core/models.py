from django.contrib.postgres.indexes import GinIndex
from django.db import models

from .search import build_document
from .youtube import extract_youtube_id

class Tag(models.Model):
    name = models.CharField(max_length=50, unique=True)

    def __str__(self):
        return self.name

class QasidaQuerySet(models.QuerySet):
    def approved(self):
        return self.filter(review_state=Qasida.REVIEW_APPROVED)

    def visible_to(self, user):
        """Staff review in context, so they see everything; readers see approved."""
        if getattr(user, 'is_staff', False):
            return self
        return self.approved()


class Qasida(models.Model):
    title = models.CharField(max_length=200, blank=True)
    arabic_title = models.CharField(max_length=200, blank=True)
    author = models.CharField(max_length=200, blank=True)
    language = models.CharField(max_length=50, blank=True)
    lyrics = models.TextField()
    # Latin-script rendering of the same verses, where the source publishes one.
    # Blank-line structure is kept aligned with `lyrics` so the two can be shown
    # stanza by stanza.
    transliteration = models.TextField(blank=True)
    # An English rendering of the meaning. Several sources interleave their own
    # translation with the original, so this is usually separated out of the
    # source text rather than generated.
    translation = models.TextField(blank=True)
    TRANSLATION_NONE = ''
    TRANSLATION_SOURCE = 'source'
    TRANSLATION_MACHINE = 'machine'
    TRANSLATION_ORIGIN_CHOICES = [
        (TRANSLATION_NONE, 'No translation'),
        (TRANSLATION_SOURCE, 'Published by the source'),
        (TRANSLATION_MACHINE, 'Machine translated'),
    ]
    translation_origin = models.CharField(max_length=8, blank=True,
                                         choices=TRANSLATION_ORIGIN_CHOICES,
                                         default=TRANSLATION_NONE)
    # How much the stored lyrics can be trusted. Several source PDFs place
    # glyphs individually with kashida padding, which shatters text extraction;
    # those are re-read with OCR and flagged so the page can say so.
    TEXT_OK = 'ok'
    TEXT_OCR = 'ocr'
    TEXT_POOR = 'poor'
    TEXT_QUALITY_CHOICES = [
        (TEXT_OK, 'Extracted text'),
        # Covers both geometric reflow and OCR: machine-reconstructed either way.
        (TEXT_OCR, 'Reconstructed text'),
        (TEXT_POOR, 'Unreliable - read the scans'),
    ]
    text_quality = models.CharField(max_length=8, choices=TEXT_QUALITY_CHOICES,
                                    default=TEXT_OK)
    # Where the text was published. For aggregator sources this is the original
    # site, not the aggregator, which is why the crawled-from site is recorded
    # separately in source_site.
    source_url = models.URLField(max_length=500, blank=True, null=True)
    source_site = models.ForeignKey('SourceWebsite', null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name='qasidas')

    # Nothing crawled is published until a person has checked it. Extraction is
    # imperfect - reconstructed text, OCR, machine translation - so the public
    # site only serves rows an admin has approved.
    REVIEW_PENDING = 'pending'
    REVIEW_APPROVED = 'approved'
    REVIEW_REJECTED = 'rejected'
    REVIEW_STATE_CHOICES = [
        (REVIEW_PENDING, 'Awaiting review'),
        (REVIEW_APPROVED, 'Approved for display'),
        (REVIEW_REJECTED, 'Rejected'),
    ]
    review_state = models.CharField(max_length=8, choices=REVIEW_STATE_CHOICES,
                                    default=REVIEW_PENDING, db_index=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    tags = models.ManyToManyField(Tag, blank=True, related_name='qasidas')
    created_at = models.DateTimeField(auto_now_add=True)

    # Diacritic-folded copy of every searchable field. Arabic is stored
    # vocalised but typed without vowel marks, so searches run against this
    # instead of the display text. Trigram-indexed, so substring matches on it
    # still use an index.
    search_text = models.TextField(blank=True, editable=False)

    class Meta:
        indexes = [
            GinIndex(name='qasida_search_trgm', fields=['search_text'],
                     opclasses=['gin_trgm_ops']),
        ]

    objects = QasidaQuerySet.as_manager()

    def save(self, *args, **kwargs):
        self.search_text = build_document(
            self.title, self.arabic_title, self.author, self.lyrics,
            self.transliteration, self.translation)
        update_fields = kwargs.get('update_fields')
        if update_fields:
            kwargs['update_fields'] = list(set(update_fields) | {'search_text'})
        super().save(*args, **kwargs)

    def __str__(self):
        return self.title or f"Qasida {self.id}"

class QasidaImage(models.Model):
    """
    A scanned page of a qasida.

    Some sources publish the poem only as photographed or scanned pages, with
    no machine-readable text anywhere on the post, so the image *is* the
    content rather than decoration for it.
    """
    qasida = models.ForeignKey(Qasida, on_delete=models.CASCADE, related_name='images')
    image = models.ImageField(upload_to='qasida_scans/')
    source_url = models.URLField(max_length=500, blank=True)
    caption = models.CharField(max_length=200, blank=True)
    position = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('position', 'id')
        constraints = [
            models.UniqueConstraint(fields=('qasida', 'source_url'),
                                    name='unique_qasida_image_source'),
        ]

    def __str__(self):
        return f"{self.caption or 'Scan'} for {self.qasida}"


class QasidaMedia(models.Model):
    """
    A recording of a qasida, added by an editor.

    Only the video id is stored, not a full URL, so a link pasted in any of
    YouTube's several forms ends up playable in an embed.
    """
    qasida = models.ForeignKey(Qasida, on_delete=models.CASCADE, related_name='media')
    url = models.URLField(max_length=500, help_text="Paste any YouTube link or the video id.")
    video_id = models.CharField(max_length=32, blank=True, editable=False)
    title = models.CharField(max_length=200, blank=True,
                             help_text="Reciter or recording name, shown above the player.")
    position = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('position', 'id')
        verbose_name_plural = 'qasida media'
        constraints = [
            models.UniqueConstraint(fields=('qasida', 'video_id'),
                                    name='unique_qasida_video'),
        ]

    def save(self, *args, **kwargs):
        self.video_id = extract_youtube_id(self.url) or ''
        super().save(*args, **kwargs)

    @property
    def embed_url(self):
        # youtube-nocookie avoids setting tracking cookies for readers.
        return f"https://www.youtube-nocookie.com/embed/{self.video_id}" if self.video_id else ''

    @property
    def thumbnail_url(self):
        return f"https://i.ytimg.com/vi/{self.video_id}/hqdefault.jpg" if self.video_id else ''

    def __str__(self):
        return self.title or self.video_id or self.url


class Suggestion(models.Model):
    qasida = models.ForeignKey(Qasida, on_delete=models.CASCADE, related_name='suggestions')
    suggested_lyrics = models.TextField(blank=True)
    suggested_tags = models.CharField(max_length=200, blank=True, help_text="Comma-separated suggested tags")
    email = models.EmailField(help_text="Email for contact regarding this suggestion")
    is_approved = models.BooleanField(default=False)
    is_reviewed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def apply(self):
        """Fold this suggestion into its qasida and mark it approved."""
        if self.suggested_lyrics:
            self.qasida.lyrics = self.suggested_lyrics
        if self.suggested_tags:
            for name in (t.strip() for t in self.suggested_tags.split(',')):
                if name:
                    tag, _ = Tag.objects.get_or_create(name=name)
                    self.qasida.tags.add(tag)
        self.qasida.save()
        self.is_approved = True
        self.is_reviewed = True
        self.save(update_fields=['is_approved', 'is_reviewed'])

    def reject(self):
        self.is_approved = False
        self.is_reviewed = True
        self.save(update_fields=['is_approved', 'is_reviewed'])

    def __str__(self):
        return f"Suggestion for {self.qasida} by {self.email}"

class SourceWebsite(models.Model):
    name = models.CharField(max_length=200)
    url = models.URLField(max_length=500, unique=True)
    is_active = models.BooleanField(default=True)
    parser_type = models.CharField(
        max_length=50,
        choices=[
            ('mynaatbook', 'My Naat Book (React JS)'),
            ('desertechoblog', 'Desert Echo Blog (WordPress)'),
            ('damas', 'Damas Nur (WordPress)'),
            ('midhah', 'Midhah lyrics (Next.js, JSON-LD)'),
            ('generic', 'Generic (JSON-LD, else densest text block)'),
            ('wayback', 'Internet Archive snapshots of a blocked site')
        ],
        default='mynaatbook'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name
