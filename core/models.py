import uuid

from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.db import IntegrityError, models, transaction
from django.utils import timezone
from django.utils.text import slugify

from .dedup import build_signature
from .search import build_document
from .youtube import extract_youtube_id

class Tag(models.Model):
    """
    One label from the harvested vocabulary.

    The tags arrive from the source sites as a single flat list mixing four
    unrelated things: what kind of poem it is, what language it is in, the
    melodic mode it is sung in, and the metre it is written in - plus a few
    that describe the state of our own record rather than the poem at all.
    Reading them as one list is what made the filters hard to use, so each
    tag carries which axis it belongs to.

    The axis was previously guessed from the tag's name every time it was
    displayed, which left anything the guesser did not recognise - manqbat,
    hamd, durood-o-salam - in a bucket called Other. Storing it means an
    editor can correct a tag once and have it stay corrected.
    """

    CATEGORY_FORM = 'form'
    CATEGORY_LANGUAGE = 'language'
    CATEGORY_MAQAM = 'maqam'
    CATEGORY_BAHR = 'bahr'
    CATEGORY_CONDITION = 'condition'
    CATEGORY_OTHER = 'other'
    CATEGORY_CHOICES = [
        (CATEGORY_FORM, 'Form and theme'),
        (CATEGORY_LANGUAGE, 'Language'),
        (CATEGORY_MAQAM, 'Maqam (melodic mode)'),
        (CATEGORY_BAHR, 'Bahr (metre)'),
        (CATEGORY_CONDITION, 'Condition of the text'),
        (CATEGORY_OTHER, 'Not yet filed'),
    ]
    # The order the groups are shown in: what the poem is, before how it is
    # performed, before notes about our copy of it.
    CATEGORY_ORDER = [CATEGORY_FORM, CATEGORY_LANGUAGE, CATEGORY_MAQAM,
                      CATEGORY_BAHR, CATEGORY_CONDITION, CATEGORY_OTHER]

    # Languages the sources tag in. Held as a set rather than guessed, because
    # a language name is not distinguishable from a theme by shape alone.
    LANGUAGE_NAMES = frozenset({
        'arabic', 'urdu', 'english', 'spanish', 'turkish', 'swedish',
        'french', 'german', 'persian', 'farsi', 'punjabi', 'sindhi',
    })
    # These say something about our record, not about the poem, so they are
    # kept off the axes a reader browses by.
    CONDITION_NAMES = frozenset({
        'transliterated', 'from-archive', 'lyrics-in-images', 'text-needs-review',
    })
    # Kinds of devotional poem and the occasions they belong to.
    FORM_NAMES = frozenset({
        'naat', 'qasida', 'hamd', 'manqbat', 'manqabat', 'manzhuma',
        'durood-o-salam', 'sufiyana-kalam', 'mawlid-hadra', 'tawassul',
        'around-the-year', 'madih', 'nasheed', 'ghazal',
    })
    # Prefixes the sources use to namespace a taxonomy.
    CATEGORY_PREFIXES = (
        ('maqam-', CATEGORY_MAQAM),
        ('bahr-', CATEGORY_BAHR),
        ('qasida-', CATEGORY_FORM),
    )

    # A few tags read badly when their slug is simply title-cased.
    DISPLAY_NAMES = {
        'lyrics-in-images': 'Lyrics only as scans',
        'text-needs-review': 'Text needs review',
        'from-archive': 'From the Internet Archive',
        'transliterated': 'Has a transliteration',
        'manqbat': 'Manqabat',
        'durood-o-salam': 'Durood o Salam',
    }

    name = models.CharField(max_length=50, unique=True)
    category = models.CharField(
        max_length=12, choices=CATEGORY_CHOICES, blank=True, db_index=True,
        help_text="Which axis this tag belongs to. Left blank, it is worked "
                  "out from the name when the tag is saved.")

    class Meta:
        ordering = ('name',)

    @classmethod
    def classify(cls, name):
        """
        Which axis a tag name belongs to.

        Only used to file a tag that has not been filed by hand: an editor's
        choice is never overwritten. Anything unrecognised is left unfiled
        rather than guessed into a group, so it shows up in the admin as
        something to look at instead of quietly sitting in the wrong place.
        """
        key = (name or '').strip().lower()
        for prefix, category in cls.CATEGORY_PREFIXES:
            if key.startswith(prefix):
                return category
        if key in cls.LANGUAGE_NAMES:
            return cls.CATEGORY_LANGUAGE
        if key in cls.CONDITION_NAMES:
            return cls.CATEGORY_CONDITION
        if key in cls.FORM_NAMES:
            return cls.CATEGORY_FORM
        return cls.CATEGORY_OTHER

    @classmethod
    def display_name(cls, name):
        """The tag as a reader should see it, without its taxonomy prefix."""
        if name in cls.DISPLAY_NAMES:
            return cls.DISPLAY_NAMES[name]
        for prefix, _ in cls.CATEGORY_PREFIXES:
            if name.startswith(prefix):
                name = name[len(prefix):]
                break
        return name.replace('-', ' ').title()

    @property
    def label(self):
        return self.display_name(self.name)

    def save(self, *args, **kwargs):
        if not self.category:
            self.category = self.classify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

class Collection(models.Model):
    """
    A work published in parts, such as the chapters of the Burdah.

    Sources list each chapter as its own entry, so the parts arrive unrelated;
    this gives them a common parent and an order to be read in.
    """
    name = models.CharField(max_length=200, unique=True)
    native_name = models.CharField(max_length=200, blank=True)
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


class Poet(models.Model):
    """
    Someone who wrote a qasida.

    A table of its own rather than a name typed onto each work, for the same
    reason as Dedication: typed by hand, one poet arrives in several
    spellings, and "everything by this poet" stops being a question anyone can
    answer. This library holds some 360 of them across 3,800 works, so the
    difference is not small.
    """
    name = models.CharField(max_length=200, unique=True)
    native_name = models.CharField(
        max_length=200, blank=True,
        help_text="The same name in its own script, where there is one.")
    notes = models.TextField(
        blank=True, help_text="Anything worth recording: dates, order, region.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('name',)

    @classmethod
    def named(cls, name):
        """
        The poet of this name, created if not already known.

        None for a blank name, because most of this library names no poet at
        all and an empty string is not a person. Matched without regard to
        case, so a crawler meeting the same name capitalised differently does
        not manufacture a second record of one person.
        """
        name = (name or '').strip()
        if not name:
            return None
        return cls.objects.filter(name__iexact=name).first() or cls.objects.create(name=name)

    def __str__(self):
        return self.name


class Dedication(models.Model):
    """
    Who a qasida is addressed to or written in praise of.

    Kept as its own table rather than as free text on the work. Much of this
    repertoire is grouped by whom it honours, and typed by hand the same
    dedication arrives half a dozen ways - so a chosen-from-a-list value is
    what makes "everything in praise of this person" a question that can be
    answered at all.
    """
    name = models.CharField(max_length=200, unique=True)
    native_name = models.CharField(
        max_length=200, blank=True,
        help_text="The same name in its own script, where there is one.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('name',)

    def __str__(self):
        return self.name


# The stored language is free text from the sources - "Urdu", "Arabic" - which
# is not a language tag. A browser given lang="urdu" ignores it, and with it any
# chance of choosing a font that suits the script. That matters here: Urdu is
# set in Nastaliq and Arabic in Naskh, both written in the Arabic script, and
# each looks wrong in the other's face. This library is 3,300 Urdu works to 470
# Arabic, so the common case was the one being mis-set.
LANGUAGE_CODES = {
    'arabic': 'ar',
    'urdu': 'ur',
    'persian': 'fa',
    'farsi': 'fa',
    'punjabi': 'pa',
    'sindhi': 'sd',
    'pashto': 'ps',
    'english': 'en',
    'turkish': 'tr',
    'french': 'fr',
    'german': 'de',
    'spanish': 'es',
    'swedish': 'sv',
}


def language_code(language):
    """A BCP-47 tag for a free-text language name, or '' if unrecognised."""
    return LANGUAGE_CODES.get((language or '').strip().lower(), '')


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
    native_title = models.CharField(max_length=200, blank=True)
    author = models.ForeignKey(
        'Poet', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='qasidas',
        help_text="Who wrote it. Choose one, or add a new one with the +.")
    # Who the poem is addressed to or written in praise of - the Prophet, a
    # saint, a teacher. Distinct from the poet, and often the thing a reader
    # is actually looking for: much of this repertoire is grouped by whom it
    # honours rather than by who wrote it.
    dedicated_to = models.ForeignKey(
        'Dedication', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='qasidas',
        help_text="Who the qasida is addressed to or written in praise of. "
                  "Choose one, or add a new one with the + button.")
    language = models.CharField(max_length=50, blank=True)
    # Optional, because a work legitimately arrives without it: a source that
    # publishes only a romanisation, or only photographed pages. Requiring it
    # would mean an editor could not save a tag on such a record without first
    # inventing an original text for it.
    lyrics = models.TextField(blank=True)
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
    TRANSLATION_READER = 'reader'
    TRANSLATION_ORIGIN_CHOICES = [
        (TRANSLATION_NONE, 'No translation'),
        (TRANSLATION_SOURCE, 'Published by the source'),
        (TRANSLATION_MACHINE, 'Machine translated'),
        (TRANSLATION_READER, 'Corrected by a reader'),
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

    # A folded, bounded copy of the poem's opening, used to recognise the same
    # work arriving from a second source. Kept separate from search_text
    # because that one deliberately includes the title and the poet, which are
    # the fields that vary most between sites publishing the same poem.
    dedup_signature = models.TextField(blank=True, editable=False)

    class Meta:
        indexes = [
            GinIndex(name='qasida_search_trgm', fields=['search_text'],
                     opclasses=['gin_trgm_ops']),
            GinIndex(name='qasida_dedup_trgm', fields=['dedup_signature'],
                     opclasses=['gin_trgm_ops']),
        ]

    objects = QasidaQuerySet.as_manager()

    @property
    def meta_description(self):
        """
        The sentence a search engine prints under this page's title.

        Composed from what the record *is* - who wrote it, what language, which
        layers it carries - and never from the verse. A snippet of the poem
        would read better and would be republishing the text into search
        results, which is not ours to do; it would also make every page of a
        multi-part work look alike.
        """
        opening = self.title or 'A qasida'
        if self.author_id:
            opening = f'{opening} by {self.author.name}'
        if self.dedicated_to_id:
            opening = f'{opening}, in praise of {self.dedicated_to.name}'

        carries = []
        if self.language:
            carries.append(f'{self.language} lyrics')
        else:
            carries.append('lyrics')
        if self.transliteration:
            carries.append('Latin transliteration')
        if self.translation:
            carries.append('English translation')
        if self.images.exists():
            carries.append('scanned pages')

        if len(carries) > 1:
            layers = ', '.join(carries[:-1]) + ' and ' + carries[-1]
        else:
            layers = carries[0]

        return f'{opening}. Read the {layers} on Qasida Library.'

    @property
    def language_code(self):
        """This work's language as a tag a browser understands."""
        return language_code(self.language)

    def _slug_base(self):
        """The readable part of the slug, or '' when nothing Latin is to hand."""
        base = slugify(self.title or '')
        if not base and self.transliteration:
            first_line = next(
                (line for line in self.transliteration.splitlines() if line.strip()), '')
            base = slugify(first_line)
        return base[:200]

    def build_slug(self):
        """
        A readable, unique URL fragment for this work.

        Falls back through the transliteration and finally the id, because a
        title in Arabic or Urdu script slugifies to nothing.
        """
        base = self._slug_base() or (f'qasida-{self.pk}' if self.pk else 'qasida')

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
        # Only touched when one is set, so an ordinary save does not fetch a
        # related row it has no use for.
        dedication = ''
        if self.dedicated_to_id:
            dedication = f'{self.dedicated_to.name} {self.dedicated_to.native_name}'
        poet = ''
        if self.author_id:
            poet = f'{self.author.name} {self.author.native_name}'
        self.search_text = build_document(
            self.title, self.native_title, poet, dedication,
            self.lyrics, self.transliteration, self.translation)
        # The original script identifies a work better than a romanisation, so
        # it is preferred; a source that publishes only a transliteration still
        # gets a signature rather than being left unmatchable.
        self.dedup_signature = build_signature(self.lyrics, self.transliteration)
        extra_fields = {'search_text', 'dedup_signature'}

        # The slug is settled before the row is written. It used to be filled
        # in by a second statement after an insert with slug='', and the column
        # is unique: a crash between the two, or two workers creating at once,
        # left a row holding '' for good, after which every new work collided
        # with it and the crawlers stopped importing anything.
        generated = needs_id = False
        if not self.slug:
            generated = True
            # A work with no Latin title is named after its id, which does not
            # exist yet. It is written under a placeholder that cannot collide
            # and renamed once the id is known, in the same transaction.
            needs_id = not self.pk and not self._slug_base()
            self.slug = f'qasida-new-{uuid.uuid4().hex}' if needs_id else self.build_slug()
            extra_fields.add('slug')

        update_fields = kwargs.get('update_fields')
        if update_fields:
            kwargs['update_fields'] = list(set(update_fields) | extra_fields)

        with transaction.atomic():
            self._save_claiming_slug(generated, *args, **kwargs)
            if needs_id:
                self.slug = self.build_slug()
                super().save(update_fields=['slug'])

    # How many times a generated slug is recomputed after another writer took
    # it first. Two is already a coincidence; five is only a backstop.
    SLUG_ATTEMPTS = 5

    def _save_claiming_slug(self, generated, *args, **kwargs):
        """
        Save, taking a fresh slug if another writer claimed ours meanwhile.

        build_slug checks for a free name and the insert takes it, and a
        second worker can take the same name in between. Only a slug this
        method generated is retried: one an editor typed is theirs to change.
        """
        for attempt in range(self.SLUG_ATTEMPTS):
            try:
                with transaction.atomic():
                    super().save(*args, **kwargs)
                return
            except IntegrityError:
                clashed = (Qasida.objects.exclude(pk=self.pk)
                           .filter(slug=self.slug).exists())
                if not (generated and clashed) or attempt == self.SLUG_ATTEMPTS - 1:
                    raise
                self.slug = self.build_slug()

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
    # Every part of a record a reader can put right. Only what actually
    # differs from the record is stored, so an editor reviewing one of these
    # sees the change rather than a copy of the whole work.
    suggested_title = models.CharField(max_length=200, blank=True)
    suggested_native_title = models.CharField(max_length=200, blank=True)
    suggested_author = models.CharField(max_length=200, blank=True)
    suggested_language = models.CharField(max_length=50, blank=True)
    suggested_lyrics = models.TextField(blank=True)
    suggested_transliteration = models.TextField(blank=True)
    suggested_translation = models.TextField(blank=True)
    suggested_tags = models.CharField(max_length=200, blank=True, help_text="Comma-separated suggested tags")
    # Why, in the sender's own words. Often the most useful part of a
    # correction: an editor who cannot read the script still learns what is
    # wrong with it.
    note = models.TextField(blank=True, help_text="What is wrong, and how you know")
    # Compulsory for an anonymous correction, which has no other way to be
    # followed up; taken from the account otherwise.
    email = models.EmailField(blank=True, help_text="Email for contact regarding this suggestion")
    is_approved = models.BooleanField(default=False)
    is_reviewed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    # Which field of the record each suggested field answers to, and what to
    # call it when showing an editor what would change.
    FIELDS = (
        ('suggested_title', 'title', 'Title'),
        ('suggested_native_title', 'native_title', 'Title in its own script'),
        ('suggested_author', 'author', 'Poet'),
        ('suggested_language', 'language', 'Language'),
        ('suggested_lyrics', 'lyrics', 'Lyrics'),
        ('suggested_transliteration', 'transliteration', 'Transliteration'),
        ('suggested_translation', 'translation', 'Translation'),
    )

    def changes(self):
        """
        What this suggestion would alter, for an editor to read before deciding.

        Only fields that were actually filled in appear, because the form
        prefills the record and the view keeps only what differs.
        """
        listed = []
        for field, target, label in self.FIELDS:
            proposed = getattr(self, field)
            if proposed.strip():
                current = getattr(self.qasida, target)
                listed.append({
                    'label': label,
                    'field': target,
                    # Rendered beside the text the reader typed, so a relation
                    # is shown by its name rather than as an object.
                    'current': '' if current is None else str(current),
                    'proposed': proposed,
                })
        return listed

    def _lock_if_unreviewed(self):
        """
        Take a row lock and re-read, reporting whether it is still undecided.

        Must run inside a transaction. Two editors pressing a button at once,
        or one pressing it twice, would otherwise each read "not reviewed" and
        both act - and applying an old correction a second time writes its
        stale text over whatever was corrected since.
        """
        type(self).objects.select_for_update().filter(pk=self.pk).first()
        self.refresh_from_db(fields=['is_reviewed', 'is_approved'])
        return not self.is_reviewed

    def apply(self):
        """
        Fold this suggestion into its qasida and mark it approved.

        Returns False, and changes nothing, when it had already been decided.
        """
        with transaction.atomic():
            if not self._lock_if_unreviewed():
                return False
            self.qasida.refresh_from_db()
            for field, target, _ in self.FIELDS:
                proposed = getattr(self, field)
                if not proposed.strip():
                    continue
                # The poet is a relation; a reader proposes a name, which becomes
                # a record of that poet if we do not already hold one.
                if target == 'author':
                    setattr(self.qasida, target, Poet.named(proposed))
                else:
                    setattr(self.qasida, target, proposed)

            # A translation a reader has corrected is no longer the machine's, and
            # the page must stop warning that it might be. Nor is it the source's.
            if self.suggested_translation.strip():
                self.qasida.translation_origin = Qasida.TRANSLATION_READER

            self.qasida.save()
            for name in self.tag_names():
                tag, _ = Tag.objects.get_or_create(name=name)
                self.qasida.tags.add(tag)

            self.is_approved = True
            self.is_reviewed = True
            self.save(update_fields=['is_approved', 'is_reviewed'])
        return True

    def reject(self):
        """Mark it declined. Returns False when it had already been decided."""
        with transaction.atomic():
            if not self._lock_if_unreviewed():
                return False
            self.is_approved = False
            self.is_reviewed = True
            self.save(update_fields=['is_approved', 'is_reviewed'])
        return True

    def tag_names(self):
        """
        The proposed tags, each one short enough to be a tag.

        Anything longer than a tag name can hold is left out rather than
        failing the whole approval on a database error.
        """
        limit = Tag._meta.get_field('name').max_length
        names = (t.strip() for t in self.suggested_tags.split(','))
        return [name for name in names if name and len(name) <= limit]

    def __str__(self):
        return f"Suggestion for {self.qasida} by {self.email}"

class DuplicateLink(models.Model):
    """
    Two works that read like the same poem, waiting on an editor's ruling.

    Both rows are kept and both stay usable. The second copy is frequently the
    better one - fuller, vocalised, carrying a translation - and just as often
    the two turn out to be genuinely different poems that open on the same
    formula, which this repertoire does constantly. Neither outcome can be
    decided mechanically, so nothing is merged or hidden: this only says "these
    two are worth looking at together".
    """

    MATCH_OPENING = 'opening'
    MATCH_FUZZY = 'fuzzy'
    MATCH_CHOICES = [
        (MATCH_OPENING, 'Identical opening'),
        (MATCH_FUZZY, 'Similar opening'),
    ]

    STATE_PENDING = 'pending'
    STATE_DUPLICATE = 'duplicate'
    STATE_DISTINCT = 'distinct'
    STATE_CHOICES = [
        (STATE_PENDING, 'Not yet reviewed'),
        (STATE_DUPLICATE, 'Confirmed duplicate'),
        (STATE_DISTINCT, 'Different works'),
    ]

    # The pair is unordered, so it is always stored lowest id first. That way
    # one pair is one row, rather than the same pair being recorded twice from
    # either end.
    first = models.ForeignKey(Qasida, on_delete=models.CASCADE,
                              related_name='duplicate_links_as_first')
    second = models.ForeignKey(Qasida, on_delete=models.CASCADE,
                               related_name='duplicate_links_as_second')
    score = models.FloatField(default=0,
                              help_text="How alike the two openings are, 0 to 1.")
    matched_on = models.CharField(max_length=8, choices=MATCH_CHOICES,
                                  default=MATCH_FUZZY)
    state = models.CharField(max_length=9, choices=STATE_CHOICES,
                             default=STATE_PENDING, db_index=True)
    note = models.CharField(max_length=280, blank=True,
                            help_text="Why you ruled the way you did.")
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        # Unreviewed first, and within those the likeliest pairs at the top.
        ordering = ('state', '-score', '-created_at')
        constraints = [
            models.UniqueConstraint(fields=('first', 'second'),
                                    name='unique_duplicate_pair'),
        ]

    def __str__(self):
        return f"{self.first} / {self.second} ({self.score:.2f})"


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
            ('wayback', 'Internet Archive snapshots of a blocked site'),
            ('wordpress_api', 'WordPress REST API (wp-json)')
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


class Contribution(models.Model):
    """
    A work a reader wants the library to hold: asked for, or brought in full.

    Both are the same act seen from two distances - "this is missing" and
    "this is missing, here it is" - and an editor works through them in one
    queue, so they are one table with a `kind` rather than two that would have
    to be merged on every listing. The difference that matters is whether
    there is a text: a submission carries one and can be turned into a record
    in a click, a request carries only enough to go and find it.

    Nothing here is ever published by being saved. Accepting a submission
    creates an ordinary Qasida in the same "awaiting review" state a crawled
    one arrives in, so reader-sent text passes the same gate as everything
    else before a visitor can read it.
    """

    KIND_REQUEST = 'request'
    KIND_SUBMISSION = 'submission'
    KIND_CHOICES = [
        (KIND_REQUEST, 'Request - please add this'),
        (KIND_SUBMISSION, 'Submission - here is the text'),
    ]

    STATUS_PENDING = 'pending'
    STATUS_ACCEPTED = 'accepted'
    STATUS_DECLINED = 'declined'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Waiting for an editor'),
        (STATUS_ACCEPTED, 'Accepted'),
        (STATUS_DECLINED, 'Declined'),
    ]

    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default=KIND_SUBMISSION)
    # Both forms are behind a sign-in, so there is always an account at the
    # time of sending. SET_NULL rather than CASCADE: a text someone brought
    # has become part of the library's record of where its contents came
    # from, and closing an account should not erase that history.
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                             on_delete=models.SET_NULL, related_name='contributions')

    title = models.CharField(max_length=200, blank=True)
    native_title = models.CharField(
        max_length=200, blank=True,
        help_text="The title in its own script, where you know it.")
    poet_name = models.CharField(
        max_length=200, blank=True, verbose_name='Poet',
        help_text="Who wrote it, if you know.")
    dedication_name = models.CharField(
        max_length=200, blank=True, verbose_name='In praise of',
        help_text="Who it is addressed to or written in praise of.")
    language = models.CharField(max_length=50, blank=True)

    # Filled in on a submission; empty on a request, which is the whole
    # difference between the two.
    lyrics = models.TextField(blank=True)
    transliteration = models.TextField(blank=True)
    translation = models.TextField(blank=True)

    source_url = models.URLField(
        max_length=500, blank=True,
        help_text="A link to where this was published, if there is one.")
    source_note = models.TextField(
        blank=True,
        help_text="Where it comes from: a book, a recording, a gathering.")
    note = models.TextField(
        blank=True, help_text="Anything the editors should know.")

    status = models.CharField(max_length=8, choices=STATUS_CHOICES,
                              default=STATUS_PENDING, db_index=True)
    # What an editor wants the sender to read: why it was declined, or what
    # was done with it. Shown to the contributor on their own page.
    staff_note = models.TextField(
        blank=True, verbose_name='Reply to the contributor',
        help_text="Shown to them on their contributions page, and emailed.")
    # The record this became, once one exists. A request can carry one too:
    # it is how a reader learns that what they asked for is now held.
    published_as = models.ForeignKey(Qasida, null=True, blank=True,
                                     on_delete=models.SET_NULL,
                                     related_name='contributions')
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL,
                                    related_name='contributions_reviewed')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-created_at',)
        # Named rather than left to Django, which derives a name from a hash
        # of the fields: the queue is read by status far more often than by
        # anything else, and a name says so to whoever reads the schema.
        indexes = [models.Index(fields=['status', 'kind'], name='contribution_queue_idx')]

    @property
    def is_submission(self):
        return self.kind == self.KIND_SUBMISSION

    @property
    def display_title(self):
        """Something to call this in a list, whichever fields were filled."""
        return self.title or self.native_title or f'Contribution {self.pk}'

    def can_publish(self):
        """Whether there is a text here to make a record out of."""
        return bool(self.lyrics.strip()) and self.published_as_id is None

    def _lock_if_pending(self):
        """
        Take a row lock and re-read, reporting whether it still awaits a decision.

        Must run inside a transaction. Without it a double-click on "create a
        record" made two records from one submission, and a batch action in
        the admin re-decided - and re-emailed - contributions that had long
        since been answered.
        """
        type(self).objects.select_for_update().filter(pk=self.pk).first()
        self.refresh_from_db(fields=['status', 'published_as'])
        return self.status == self.STATUS_PENDING

    def publish(self, by=None, note=None):
        """
        Turn a submission into a record of its own, awaiting review.

        Deliberately not approved: the review queue exists because text that
        arrives from outside has to be read by a person before the site
        serves it, and text typed in by a reader is no different from text a
        crawler found. The editor who accepts it lands on the new record and
        approves it there, having read it.

        Returns the new record, or None when there was nothing to make one
        from or the contribution had already been decided.
        """
        with transaction.atomic():
            if not self._lock_if_pending() or not self.can_publish():
                return None

            dedication = None
            name = self.dedication_name.strip()
            if name:
                dedication = (Dedication.objects.filter(name__iexact=name).first()
                              or Dedication.objects.create(name=name))

            qasida = Qasida.objects.create(
                title=self.title.strip(),
                native_title=self.native_title.strip(),
                author=Poet.named(self.poet_name),
                dedicated_to=dedication,
                language=self.language.strip(),
                lyrics=self.lyrics,
                transliteration=self.transliteration,
                translation=self.translation,
                # A reader who typed out a translation is the source of it, and
                # the page must not warn that a machine wrote it.
                translation_origin=(Qasida.TRANSLATION_READER
                                    if self.translation.strip() else Qasida.TRANSLATION_NONE),
                source_url=self.source_url or None,
                review_state=Qasida.REVIEW_PENDING,
            )
            self.published_as = qasida
            self._close(self.STATUS_ACCEPTED, by, note)
        return qasida

    def accept(self, by=None, note=None):
        """Accept without a record. Returns False when already decided."""
        return self._decide(self.STATUS_ACCEPTED, by, note)

    def decline(self, by=None, note=None):
        """Decline it. Returns False when already decided."""
        return self._decide(self.STATUS_DECLINED, by, note)

    def _decide(self, status, by, note):
        with transaction.atomic():
            if not self._lock_if_pending():
                return False
            self._close(status, by, note)
        return True

    def _close(self, status, by, note):
        self.status = status
        if note is not None:
            self.staff_note = note
        self.reviewed_at = timezone.now()
        self.reviewed_by = by if (by is not None and by.is_authenticated) else None
        self.save(update_fields=['status', 'staff_note', 'reviewed_at',
                                 'reviewed_by', 'published_as'])

    def __str__(self):
        return f'{self.get_kind_display()}: {self.display_title}'


class ContactMessage(models.Model):
    """
    A message sent from the contact page.

    Stored as well as emailed. Mail is the part of this that can fail
    silently - a rejected relay, a full mailbox, a rule that files it
    somewhere nobody looks - and a library that invites people to write in
    should not be able to lose what they wrote.
    """

    TOPIC_GENERAL = 'general'
    TOPIC_CORRECTION = 'correction'
    TOPIC_CONTRIBUTE = 'contribute'
    TOPIC_RIGHTS = 'rights'
    TOPIC_TECHNICAL = 'technical'
    TOPIC_CHOICES = [
        (TOPIC_GENERAL, 'General enquiry'),
        (TOPIC_CORRECTION, 'A mistake in a text'),
        (TOPIC_CONTRIBUTE, 'Offering a qasida or a scan'),
        (TOPIC_RIGHTS, 'Copyright or a request to take something down'),
        (TOPIC_TECHNICAL, 'Something is broken'),
    ]

    name = models.CharField(max_length=120)
    email = models.EmailField()
    topic = models.CharField(max_length=12, choices=TOPIC_CHOICES, default=TOPIC_GENERAL)
    message = models.TextField()
    # Set when the sender happened to be signed in, so a reply can be matched
    # to an account without asking them who they are.
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                             on_delete=models.SET_NULL, related_name='contact_messages')
    is_handled = models.BooleanField(
        default=False, verbose_name='Dealt with',
        help_text="Tick once this has been answered.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-created_at',)

    def __str__(self):
        return f'{self.get_topic_display()} from {self.email}'
