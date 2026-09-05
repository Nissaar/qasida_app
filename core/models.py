from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.db import IntegrityError, models, transaction
from django.utils import timezone
from django.utils.text import slugify

from .search import build_document
from .youtube import extract_youtube_id

class Tag(models.Model):
    name = models.CharField(max_length=50, unique=True)

    def __str__(self):
        return self.name

class Collection(models.Model):
    """
    A work published in parts, such as the chapters of the Burdah.

    Sources list each chapter as its own entry, so the parts arrive unrelated;
    this gives them a common parent and an order to be read in.
    """
    name = models.CharField(max_length=200, unique=True)
    arabic_name = models.CharField(max_length=200, blank=True)
    slug = models.SlugField(max_length=220, unique=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('name',)

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)[:220]
        super().save(*args, **kwargs)


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
    # Used in the URL in place of the id. Kept ASCII so it survives being
    # copied around; a title written only in Arabic script has no Latin text
    # to build from and falls back to the id.
    slug = models.SlugField(max_length=220, unique=True, blank=True)
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

    # Part of a larger work, where there is one.
    collection = models.ForeignKey('Collection', null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='parts')
    collection_position = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Which part of the collection this is, counting from 1.")

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

    def build_slug(self):
        """
        A readable, unique URL fragment for this work.

        Falls back through the transliteration and finally the id, because a
        title in Arabic or Urdu script slugifies to nothing.
        """
        base = slugify(self.title or '')
        if not base and self.transliteration:
            first_line = next(
                (line for line in self.transliteration.splitlines() if line.strip()), '')
            base = slugify(first_line)
        if not base:
            base = f'qasida-{self.pk}' if self.pk else 'qasida'
        base = base[:200]

        candidate = base
        suffix = 2
        siblings = Qasida.objects.exclude(pk=self.pk)
        while siblings.filter(slug=candidate).exists():
            candidate = f'{base}-{suffix}'[:220]
            suffix += 1
        return candidate

    def get_absolute_url(self):
        from django.urls import reverse
        if self.slug:
            return reverse('qasida_detail', kwargs={'slug': self.slug})
        return reverse('qasida_by_id', kwargs={'pk': self.pk})

    def save(self, *args, **kwargs):
        self.search_text = build_document(
            self.title, self.arabic_title, self.author, self.lyrics,
            self.transliteration, self.translation)
        update_fields = kwargs.get('update_fields')
        if update_fields:
            kwargs['update_fields'] = list(set(update_fields) | {'search_text'})
        super().save(*args, **kwargs)

        # A row with no title in Latin script needs its id to build a slug, so
        # this runs after the first save rather than before it.
        if not self.slug:
            self.slug = self.build_slug()
            super().save(update_fields=['slug'])

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
        # The nocookie host refuses more videos than the main one, which shows
        # up as an in-player error rather than anything we can catch, so the
        # main embed host is used.
        return f"https://www.youtube.com/embed/{self.video_id}" if self.video_id else ''

    @property
    def watch_url(self):
        """Somewhere to send the reader when the owner disallows embedding."""
        return f"https://www.youtube.com/watch?v={self.video_id}" if self.video_id else self.url

    @property
    def thumbnail_url(self):
        return f"https://i.ytimg.com/vi/{self.video_id}/hqdefault.jpg" if self.video_id else ''

    def __str__(self):
        return self.title or self.video_id or self.url


class Suggestion(models.Model):
    qasida = models.ForeignKey(Qasida, on_delete=models.CASCADE, related_name='suggestions')
    # Set when the correction came from a signed-in reader, so they can be
    # shown what became of it. Anonymous corrections are still accepted and
    # leave this empty; the account being deleted does not withdraw the
    # correction, it only detaches it.
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                             on_delete=models.SET_NULL, related_name='suggestions')
    suggested_lyrics = models.TextField(blank=True)
    suggested_tags = models.CharField(max_length=200, blank=True, help_text="Comma-separated suggested tags")
    # Compulsory for an anonymous correction, which has no other way to be
    # followed up; taken from the account otherwise.
    email = models.EmailField(blank=True, help_text="Email for contact regarding this suggestion")
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


class Favourite(models.Model):
    """
    A work someone signed in has saved.

    The point of an account on a library this size is being able to find your
    way back to something, so this is the smallest possible record: who, what,
    when, and an optional line of your own about why.
    """
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name='favourites')
    qasida = models.ForeignKey(Qasida, on_delete=models.CASCADE,
                               related_name='favourited_by')
    note = models.CharField(max_length=280, blank=True,
                            help_text="A private note, visible only to you.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-created_at',)
        constraints = [
            models.UniqueConstraint(fields=('user', 'qasida'), name='unique_favourite'),
        ]

    def __str__(self):
        return f"{self.user} saved {self.qasida}"


class ReadingHistory(models.Model):
    """
    The works a signed-in reader has opened, most recent first.

    One row per work rather than per visit: the useful question is "what was I
    reading", not "how did I get here", and collapsing repeat visits keeps the
    list short enough to be scanned. Nothing is recorded for anonymous
    visitors, and the reader can clear the whole list.
    """
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name='reading_history')
    qasida = models.ForeignKey(Qasida, on_delete=models.CASCADE,
                               related_name='read_by')
    # Explicit rather than auto_now, so a save with update_fields controls it.
    last_read_at = models.DateTimeField(default=timezone.now)
    read_count = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ('-last_read_at',)
        verbose_name_plural = 'reading history'
        constraints = [
            models.UniqueConstraint(fields=('user', 'qasida'), name='unique_reading_history'),
        ]

    # Kept per reader, so the list stays scannable and one account cannot grow
    # a row for every work in the library.
    KEEP_PER_READER = 200

    @classmethod
    def record(cls, user, qasida):
        """
        Note that this reader has just opened this work.

        Repeat visits move the existing row rather than adding one, so the
        list answers "what was I reading" instead of "how often". Trimming
        only runs when a row is genuinely new, which keeps the cost of an
        ordinary page view to a single upsert.
        """
        moved = cls.objects.filter(user=user, qasida=qasida).update(
            last_read_at=timezone.now(), read_count=models.F('read_count') + 1)
        if moved:
            return
        try:
            # Two tabs opening the same work at once both miss the update
            # above; the unique constraint settles it and the loser has
            # nothing left to do.
            with transaction.atomic():
                cls.objects.create(user=user, qasida=qasida)
        except IntegrityError:
            return

        cls.objects.filter(user=user).exclude(
            pk__in=cls.objects.filter(user=user)
            .order_by('-last_read_at')
            .values_list('pk', flat=True)[:cls.KEEP_PER_READER]
        ).delete()

    def __str__(self):
        return f"{self.user} read {self.qasida}"


class ReaderProfile(models.Model):
    """
    How one reader wants the verse laid out.

    The qasida page can already hide the transliteration or the translation,
    but that choice lives in the browser's local storage, so it is lost on
    another device and in a private window. An account makes it stick, and
    lets the type size be set for people who find the default hard to read -
    which for a page whose whole content is vocalised Arabic is not a small
    thing.
    """
    SIZE_SMALL = 'sm'
    SIZE_MEDIUM = 'md'
    SIZE_LARGE = 'lg'
    SIZE_CHOICES = [
        (SIZE_SMALL, 'Compact'),
        (SIZE_MEDIUM, 'Comfortable'),
        (SIZE_LARGE, 'Large'),
    ]

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name='reader_profile')
    show_transliteration = models.BooleanField(
        default=True, help_text="Show the Latin script beside the original.")
    show_translation = models.BooleanField(
        default=True, help_text="Show the translation beside the original.")
    lyrics_size = models.CharField(max_length=2, choices=SIZE_CHOICES, default=SIZE_MEDIUM,
                                   help_text="How large the verse itself is set.")

    @classmethod
    def for_user(cls, user):
        """The reader's preferences, created on first use.

        Built on demand rather than by a signal on user creation, so accounts
        made before this existed - and by createsuperuser, which fires no
        such thing - are covered by the same path.
        """
        profile, _ = cls.objects.get_or_create(user=user)
        return profile

    def __str__(self):
        return f"Reading preferences for {self.user}"
