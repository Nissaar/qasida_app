"""Password rules beyond the set Django ships with."""

from django.core.exceptions import ValidationError


class ComplexityValidator:
    """
    Require a password to mix letters with something that is not a letter.

    Django's own validators catch length, the commonest twenty thousand
    passwords, and strings made only of digits. What survives all of those and
    is still weak tends to be a single character class: a long lowercase word,
    or a name with nothing else in it. Asking for one number or symbol removes
    that family without imposing the four-class rules that are known to push
    people towards writing the password down.
    """

    MESSAGE = "Your password must mix letters with at least one number or symbol."

    def validate(self, password, user=None):
        has_letter = any(character.isalpha() for character in password)
        has_other = any(not character.isalpha() for character in password)
        if not (has_letter and has_other):
            raise ValidationError(self.MESSAGE, code="password_single_character_class")

    def get_help_text(self):
        return self.MESSAGE
