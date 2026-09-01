from django.db import models

class Tag(models.Model):
    name = models.CharField(max_length=50, unique=True)

    def __str__(self):
        return self.name

class Qasida(models.Model):
    title = models.CharField(max_length=200, blank=True)
    author = models.CharField(max_length=200, blank=True)
    language = models.CharField(max_length=50, blank=True)
    lyrics = models.TextField()
    source_url = models.URLField(max_length=500, blank=True, null=True)
    tags = models.ManyToManyField(Tag, blank=True, related_name='qasidas')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title or f"Qasida {self.id}"

class Suggestion(models.Model):
    qasida = models.ForeignKey(Qasida, on_delete=models.CASCADE, related_name='suggestions')
    suggested_lyrics = models.TextField(blank=True)
    suggested_tags = models.CharField(max_length=200, blank=True, help_text="Comma-separated suggested tags")
    email = models.EmailField(help_text="Email for contact regarding this suggestion")
    is_approved = models.BooleanField(default=False)
    is_reviewed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

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
            ('damas', 'Damas Nur (WordPress)')
        ],
        default='mynaatbook'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name
