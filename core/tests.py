"""
Tests for the library and for reader accounts.

The account tests pin the behaviour that would be expensive to get wrong on a
site that is already in production: who can see what, what the review gate
still hides once someone is signed in, and that nothing a reader saved can be
reached or changed by anyone else.
"""

import re
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import (Favourite, Qasida, ReaderProfile, ReadingHistory,
                     Suggestion, Tag)

User = get_user_model()

# Long enough, mixed, and nothing like the usernames below, so it satisfies
# the configured policy without the tests having to think about it.
GOOD_PASSWORD = 'Marmalade-7-Kettle'

# Counting sign-in failures in a local cache keeps the tests independent of
# whatever a shared Redis happens to be holding.
LOCAL_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


def pdf_text(content):
    """The text layer of a generated PDF, for asserting on what it contains."""
    import pymupdf
    with pymupdf.open('pdf', content) as doc:
        return '\n'.join(page.get_text() for page in doc)


def make_qasida(**overrides):
    """An approved work, since anything else is invisible to a reader."""
    fields = {
        'title': 'Test Qasida',
        'author': 'Test Author',
        'language': 'English',
        'lyrics': 'These are the test lyrics.',
        'review_state': Qasida.REVIEW_APPROVED,
    }
    fields.update(overrides)
    return Qasida.objects.create(**fields)


class QasidaModelTest(TestCase):
    def setUp(self):
        self.tag = Tag.objects.create(name='spiritual')
        self.qasida = make_qasida()
        self.qasida.tags.add(self.tag)

    def test_qasida_creation(self):
        self.assertEqual(self.qasida.title, 'Test Qasida')
        self.assertEqual(self.qasida.tags.count(), 1)

    def test_slug_is_built_from_the_title(self):
        self.assertEqual(self.qasida.slug, 'test-qasida')

    def test_suggestion_creation(self):
        suggestion = Suggestion.objects.create(
            qasida=self.qasida, email='test@example.com',
            suggested_lyrics='New lyrics', suggested_tags='newtag')
        self.assertFalse(suggestion.is_approved)
        self.assertIsNone(suggestion.user)


class ReviewGateTest(TestCase):
    """Nothing unapproved reaches a reader, however they arrive at it."""

    def setUp(self):
        self.pending = Qasida.objects.create(
            title='Not Yet Checked', lyrics='pending text', language='Arabic')
        self.reader = User.objects.create_user(
            'reader', 'reader@example.com', GOOD_PASSWORD)

    def test_pending_work_is_hidden_from_anonymous(self):
        response = self.client.get(
            reverse('qasida_detail', args=[self.pending.slug]))
        self.assertEqual(response.status_code, 404)

    def test_pending_work_is_hidden_from_a_signed_in_reader(self):
        self.client.force_login(self.reader)
        response = self.client.get(
            reverse('qasida_detail', args=[self.pending.slug]))
        self.assertEqual(response.status_code, 404)

    def test_pending_work_cannot_be_favourited(self):
        self.client.force_login(self.reader)
        response = self.client.post(
            reverse('toggle_favourite', args=[self.pending.slug]))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(Favourite.objects.count(), 0)

    def test_pending_work_cannot_be_downloaded(self):
        self.client.force_login(self.reader)
        response = self.client.get(
            reverse('qasida_download', args=[self.pending.slug]))
        self.assertEqual(response.status_code, 404)

    def test_staff_do_see_a_pending_work(self):
        staff = User.objects.create_user('editor', 'editor@example.com',
                                         GOOD_PASSWORD, is_staff=True)
        self.client.force_login(staff)
        response = self.client.get(
            reverse('qasida_detail', args=[self.pending.slug]))
        self.assertEqual(response.status_code, 200)


class QasidaViewsTest(TestCase):
    def setUp(self):
        self.tag = Tag.objects.create(name='urdu')
        self.qasida = make_qasida(title='Searchable Naat', author='Known Author',
                                  language='Urdu',
                                  lyrics='Searchable content inside lyrics')
        self.qasida.tags.add(self.tag)

    def test_home_view(self):
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Searchable Naat')

    def test_search_by_lyrics(self):
        response = self.client.get(reverse('search'), {'q': 'content'})
        self.assertContains(response, 'Searchable Naat')

    def test_search_by_tag(self):
        response = self.client.get(reverse('search'), {'tag': 'urdu'})
        self.assertContains(response, 'Searchable Naat')

    def test_search_with_no_results(self):
        response = self.client.get(reverse('search'), {'q': 'nonexistent'})
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Searchable Naat')

    def test_detail_view(self):
        response = self.client.get(reverse('qasida_detail', args=[self.qasida.slug]))
        self.assertContains(response, 'Searchable Naat')
        self.assertContains(response, 'Searchable content inside lyrics')

    def test_numeric_url_redirects_to_the_slug(self):
        response = self.client.get(reverse('qasida_by_id', args=[self.qasida.pk]))
        self.assertRedirects(response, self.qasida.get_absolute_url(),
                             status_code=301)

    def test_anonymous_suggestion_needs_an_email(self):
        url = reverse('qasida_detail', args=[self.qasida.slug])
        response = self.client.post(url, {'suggested_lyrics': 'Better lyrics'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Suggestion.objects.count(), 0)

    def test_anonymous_suggestion_with_an_email_is_accepted(self):
        url = reverse('qasida_detail', args=[self.qasida.slug])
        response = self.client.post(url, {'email': 'user@test.com',
                                          'suggested_lyrics': 'Better lyrics'})
        self.assertEqual(response.status_code, 302)
        suggestion = Suggestion.objects.get()
        self.assertEqual(suggestion.email, 'user@test.com')
        self.assertIsNone(suggestion.user)

    def test_download_is_a_pdf(self):
        qasida = make_qasida(title='Layered')
        response = self.client.get(reverse('qasida_download', args=[qasida.slug]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(response.content.startswith(b'%PDF-'))
        self.assertIn('.pdf', response['Content-Disposition'])

    def test_download_offers_only_what_was_asked_for(self):
        qasida = make_qasida(title='Layered', lyrics='asl', language='Arabic',
                             transliteration='latin line',
                             translation='meaning line',
                             translation_origin=Qasida.TRANSLATION_SOURCE)
        url = reverse('qasida_download', args=[qasida.slug])

        plain = pdf_text(self.client.get(url, {'original': '1'}).content)
        self.assertNotIn('latin line', plain)
        self.assertNotIn('meaning line', plain)

        full = pdf_text(self.client.get(
            url, {'original': '1', 'latin': '1', 'translation': '1'}).content)
        self.assertIn('latin line', full)
        self.assertIn('meaning line', full)

    def test_the_pdf_embeds_the_arabic_font(self):
        """
        Guards the font being present in the image.

        Without it MuPDF silently substitutes: the download still succeeds and
        the Arabic is still shaped, so nothing fails - the poetry is simply set
        in the wrong face, which no other test would notice.
        """
        import pymupdf
        qasida = make_qasida(title='Arabic Work', language='Arabic',
                             lyrics='مكتبة القصائد')
        content = self.client.get(
            reverse('qasida_download', args=[qasida.slug])).content
        names = ' '.join(f[3] for f in pymupdf.open('pdf', content)[0].get_fonts())
        self.assertIn('Amiri', names, f'expected Amiri, embedded fonts were: {names}')

    def test_a_long_work_runs_to_several_pages(self):
        import pymupdf
        qasida = make_qasida(title='Long Work',
                             lyrics='\n\n'.join(f'stanza number {n}' for n in range(400)))
        content = self.client.get(
            reverse('qasida_download', args=[qasida.slug])).content
        self.assertGreater(pymupdf.open('pdf', content).page_count, 1)


# --------------------------------------------------------------------------
# Accounts
# --------------------------------------------------------------------------

@override_settings(CACHES=LOCAL_CACHE)
class RegistrationTest(TestCase):
    url = None

    def setUp(self):
        self.url = reverse('register')

    def post(self, **overrides):
        data = {'username': 'newreader', 'email': 'new@example.com',
                'password1': GOOD_PASSWORD, 'password2': GOOD_PASSWORD}
        data.update(overrides)
        return self.client.post(self.url, data)

    def test_registration_creates_an_account_and_signs_in(self):
        response = self.post()
        self.assertRedirects(response, reverse('my_library'))
        user = User.objects.get(username='newreader')
        self.assertEqual(user.email, 'new@example.com')
        self.assertEqual(int(self.client.session['_auth_user_id']), user.pk)

    def test_a_short_password_is_refused(self):
        response = self.post(password1='Ab3!', password2='Ab3!')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username='newreader').exists())

    def test_a_common_password_is_refused(self):
        response = self.post(password1='password123', password2='password123')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.exists())

    def test_an_all_letter_password_is_refused(self):
        response = self.post(password1='thistlewhistle', password2='thistlewhistle')
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context['form'], 'password2',
                             ['Your password must mix letters with at least one number or symbol.'])

    def test_an_all_numeric_password_is_refused(self):
        response = self.post(password1='98765432109', password2='98765432109')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.exists())

    def test_a_password_like_the_username_is_refused(self):
        response = self.post(username='marmaladekettle',
                             password1='marmaladekettle1', password2='marmaladekettle1')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.exists())

    def test_mismatched_passwords_are_refused(self):
        response = self.post(password2=GOOD_PASSWORD + 'x')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.exists())

    def test_a_username_differing_only_in_case_is_refused(self):
        User.objects.create_user('newreader', 'taken@example.com', GOOD_PASSWORD)
        response = self.post(username='NewReader', email='other@example.com')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.count(), 1)

    def test_an_address_already_in_use_is_refused(self):
        User.objects.create_user('someone', 'New@Example.com', GOOD_PASSWORD)
        response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.count(), 1)

    def test_an_email_shaped_username_is_refused(self):
        response = self.post(username='me@example.com')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.exists())

    def test_an_email_address_is_required(self):
        response = self.post(email='')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.exists())


@override_settings(CACHES=LOCAL_CACHE)
class SignInTest(TestCase):
    def setUp(self):
        # The attempt counters are deliberately longer-lived than a request,
        # so one test's failures would otherwise lock out the next test.
        cache.clear()
        self.user = User.objects.create_user('reader', 'Reader@Example.com',
                                             GOOD_PASSWORD)
        self.url = reverse('login')

    def signed_in(self):
        return '_auth_user_id' in self.client.session

    def test_sign_in_with_a_username(self):
        self.client.post(self.url, {'username': 'reader', 'password': GOOD_PASSWORD})
        self.assertTrue(self.signed_in())

    def test_sign_in_with_an_email_address(self):
        self.client.post(self.url, {'username': 'Reader@Example.com',
                                    'password': GOOD_PASSWORD})
        self.assertTrue(self.signed_in())

    def test_sign_in_with_a_differently_cased_email(self):
        self.client.post(self.url, {'username': 'reader@example.COM',
                                    'password': GOOD_PASSWORD})
        self.assertTrue(self.signed_in())

    def test_the_wrong_password_is_refused(self):
        self.client.post(self.url, {'username': 'reader', 'password': 'not it at all'})
        self.assertFalse(self.signed_in())

    def test_a_suspended_account_cannot_sign_in(self):
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])
        self.client.post(self.url, {'username': 'reader', 'password': GOOD_PASSWORD})
        self.assertFalse(self.signed_in())

    def test_an_ambiguous_address_does_not_sign_anyone_in(self):
        """Two accounts on one address must not resolve to a guess."""
        User.objects.create_user('other', 'reader@example.com', GOOD_PASSWORD)
        self.client.post(self.url, {'username': 'reader@example.com',
                                    'password': GOOD_PASSWORD})
        self.assertFalse(self.signed_in())

    def test_an_exact_username_still_works_when_an_address_collides(self):
        User.objects.create_user('other', 'reader@example.com', GOOD_PASSWORD)
        self.client.post(self.url, {'username': 'reader', 'password': GOOD_PASSWORD})
        self.assertTrue(self.signed_in())

    def test_repeated_failures_on_one_account_are_eventually_refused(self):
        for _ in range(9):
            self.client.post(self.url, {'username': 'reader', 'password': 'wrong wrong'})
        # The right password now, but the attempt limit has been reached.
        self.client.post(self.url, {'username': 'reader', 'password': GOOD_PASSWORD})
        self.assertFalse(self.signed_in())

    def test_a_successful_sign_in_clears_the_counter(self):
        for _ in range(3):
            self.client.post(self.url, {'username': 'reader', 'password': 'wrong wrong'})
        self.client.post(self.url, {'username': 'reader', 'password': GOOD_PASSWORD})
        self.assertTrue(self.signed_in())

    def test_next_is_honoured_only_for_this_site(self):
        response = self.client.post(
            self.url, {'username': 'reader', 'password': GOOD_PASSWORD,
                       'next': 'https://example.net/phish'})
        self.assertNotIn('example.net', response['Location'])

    def test_signing_out_needs_a_post(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse('logout')).status_code, 405)
        self.client.post(reverse('logout'))
        self.assertFalse(self.signed_in())


class FavouriteTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('reader', 'reader@example.com',
                                             GOOD_PASSWORD)
        self.other = User.objects.create_user('other', 'other@example.com',
                                              GOOD_PASSWORD)
        self.qasida = make_qasida(title='Keepsake')
        self.url = reverse('toggle_favourite', args=[self.qasida.slug])

    def test_saving_requires_signing_in(self):
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response['Location'])
        self.assertEqual(Favourite.objects.count(), 0)

    def test_saving_refuses_a_get(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_saving_then_unsaving(self):
        self.client.force_login(self.user)
        self.client.post(self.url)
        self.assertTrue(Favourite.objects.filter(user=self.user, qasida=self.qasida).exists())
        self.client.post(self.url)
        self.assertFalse(Favourite.objects.filter(user=self.user).exists())

    def test_saving_answers_json_when_asked(self):
        self.client.force_login(self.user)
        response = self.client.post(self.url, headers={'x-requested-with': 'fetch'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'saved': True, 'count': 1})

    def test_the_library_page_lists_what_was_saved(self):
        Favourite.objects.create(user=self.user, qasida=self.qasida)
        self.client.force_login(self.user)
        response = self.client.get(reverse('my_library'))
        self.assertContains(response, 'Keepsake')

    def test_one_reader_never_sees_another_readers_shelf(self):
        Favourite.objects.create(user=self.other, qasida=self.qasida)
        self.client.force_login(self.user)
        response = self.client.get(reverse('my_library'))
        self.assertNotContains(response, 'Keepsake')

    def test_a_note_cannot_be_written_onto_someone_elses_favourite(self):
        theirs = Favourite.objects.create(user=self.other, qasida=self.qasida)
        self.client.force_login(self.user)
        response = self.client.post(reverse('favourite_note', args=[theirs.pk]),
                                    {'note': 'not mine to write on'})
        self.assertEqual(response.status_code, 404)
        theirs.refresh_from_db()
        self.assertEqual(theirs.note, '')

    def test_a_reader_can_note_why_they_saved_something(self):
        mine = Favourite.objects.create(user=self.user, qasida=self.qasida)
        self.client.force_login(self.user)
        self.client.post(reverse('favourite_note', args=[mine.pk]),
                         {'note': 'for Thursday'})
        mine.refresh_from_db()
        self.assertEqual(mine.note, 'for Thursday')

    def test_searching_your_own_shelf(self):
        Favourite.objects.create(user=self.user, qasida=self.qasida)
        Favourite.objects.create(user=self.user, qasida=make_qasida(title='Elsewhere'))
        self.client.force_login(self.user)
        response = self.client.get(reverse('my_library'), {'q': 'keepsake'})
        self.assertContains(response, 'Keepsake')
        self.assertNotContains(response, 'Elsewhere')


class ReadingHistoryTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('reader', 'reader@example.com',
                                             GOOD_PASSWORD)
        self.qasida = make_qasida(title='Read Me')

    def test_nothing_is_recorded_for_a_visitor(self):
        self.client.get(self.qasida.get_absolute_url())
        self.assertEqual(ReadingHistory.objects.count(), 0)

    def test_opening_a_work_records_it(self):
        self.client.force_login(self.user)
        self.client.get(self.qasida.get_absolute_url())
        entry = ReadingHistory.objects.get()
        self.assertEqual(entry.qasida, self.qasida)
        self.assertEqual(entry.read_count, 1)

    def test_reading_it_again_moves_the_same_row(self):
        self.client.force_login(self.user)
        self.client.get(self.qasida.get_absolute_url())
        self.client.get(self.qasida.get_absolute_url())
        self.assertEqual(ReadingHistory.objects.count(), 1)
        self.assertEqual(ReadingHistory.objects.get().read_count, 2)

    def test_a_correction_is_not_a_visit(self):
        self.client.force_login(self.user)
        self.client.post(self.qasida.get_absolute_url(),
                         {'suggested_lyrics': 'fixed'})
        self.assertEqual(ReadingHistory.objects.count(), 0)

    def test_a_reader_can_clear_their_history(self):
        ReadingHistory.objects.create(user=self.user, qasida=self.qasida)
        self.client.force_login(self.user)
        self.client.post(reverse('clear_history'))
        self.assertEqual(ReadingHistory.objects.count(), 0)

    def test_history_is_trimmed_to_a_bound(self):
        keep = ReadingHistory.KEEP_PER_READER
        for index in range(keep + 3):
            ReadingHistory.record(self.user, make_qasida(title=f'Work {index}'))
        self.assertEqual(ReadingHistory.objects.filter(user=self.user).count(), keep)


class SignedInSuggestionTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('reader', 'reader@example.com',
                                             GOOD_PASSWORD)
        self.qasida = make_qasida(title='Needs A Fix')

    def test_a_correction_is_attached_to_the_account(self):
        self.client.force_login(self.user)
        self.client.post(self.qasida.get_absolute_url(),
                         {'suggested_lyrics': 'the corrected text'})
        suggestion = Suggestion.objects.get()
        self.assertEqual(suggestion.user, self.user)
        self.assertEqual(suggestion.email, 'reader@example.com')

    def test_a_reader_sees_only_their_own_corrections(self):
        Suggestion.objects.create(qasida=self.qasida, user=self.user,
                                  email='reader@example.com',
                                  suggested_tags='mine-to-see')
        stranger = User.objects.create_user('other', 'other@example.com', GOOD_PASSWORD)
        Suggestion.objects.create(qasida=self.qasida, user=stranger,
                                  email='other@example.com',
                                  suggested_tags='not-mine-to-see')
        self.client.force_login(self.user)
        response = self.client.get(reverse('my_corrections'))
        self.assertContains(response, 'mine-to-see')
        self.assertNotContains(response, 'not-mine-to-see')

    def test_applying_a_correction_still_works(self):
        suggestion = Suggestion.objects.create(
            qasida=self.qasida, user=self.user, email='reader@example.com',
            suggested_lyrics='the corrected text', suggested_tags='fixed')
        suggestion.apply()
        self.qasida.refresh_from_db()
        self.assertEqual(self.qasida.lyrics, 'the corrected text')
        self.assertTrue(self.qasida.tags.filter(name='fixed').exists())


class AccountSettingsTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('reader', 'reader@example.com',
                                             GOOD_PASSWORD)
        self.client.force_login(self.user)
        self.url = reverse('account_settings')

    def test_settings_need_signing_in(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertIn(reverse('login'), response['Location'])

    def test_changing_the_email_address(self):
        self.client.post(self.url, {'save_email': '1', 'email': 'moved@example.com'})
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'moved@example.com')

    def test_an_address_another_account_uses_is_refused(self):
        User.objects.create_user('other', 'taken@example.com', GOOD_PASSWORD)
        self.client.post(self.url, {'save_email': '1', 'email': 'Taken@example.com'})
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'reader@example.com')

    def test_reading_preferences_are_saved(self):
        self.client.post(self.url, {'save_preferences': '1', 'lyrics_size': 'lg'})
        profile = ReaderProfile.objects.get(user=self.user)
        self.assertEqual(profile.lyrics_size, 'lg')
        # Unticked checkboxes are absent from the post, which is what "off" is.
        self.assertFalse(profile.show_translation)

    def test_preferences_reach_the_qasida_page(self):
        ReaderProfile.objects.create(user=self.user, lyrics_size='lg',
                                     show_translation=False)
        qasida = make_qasida(title='Sized', transliteration='latin',
                             translation='meaning')
        response = self.client.get(qasida.get_absolute_url())
        self.assertContains(response, 'lyrics-lg')
        self.assertContains(response, 'data-default-translation="0"')


class DeleteAccountTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('reader', 'reader@example.com',
                                             GOOD_PASSWORD)
        self.qasida = make_qasida()
        Favourite.objects.create(user=self.user, qasida=self.qasida)
        ReadingHistory.objects.create(user=self.user, qasida=self.qasida)
        Suggestion.objects.create(qasida=self.qasida, user=self.user,
                                  email='reader@example.com')
        self.client.force_login(self.user)
        self.url = reverse('delete_account')

    def test_the_wrong_password_deletes_nothing(self):
        self.client.post(self.url, {'password': 'not the password'})
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())

    def test_the_right_password_removes_the_account_and_its_contents(self):
        self.client.post(self.url, {'password': GOOD_PASSWORD})
        self.assertFalse(User.objects.filter(pk=self.user.pk).exists())
        self.assertEqual(Favourite.objects.count(), 0)
        self.assertEqual(ReadingHistory.objects.count(), 0)

    def test_a_correction_survives_the_account_that_sent_it(self):
        self.client.post(self.url, {'password': GOOD_PASSWORD})
        suggestion = Suggestion.objects.get()
        self.assertIsNone(suggestion.user)

    def test_the_qasida_itself_is_untouched(self):
        self.client.post(self.url, {'password': GOOD_PASSWORD})
        self.assertTrue(Qasida.objects.filter(pk=self.qasida.pk).exists())


class PasswordResetTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('reader', 'Reader@Example.com',
                                             GOOD_PASSWORD)

    def test_a_link_is_sent_whatever_the_capitalisation(self):
        self.client.post(reverse('password_reset'), {'email': 'reader@example.com'})
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('reset', mail.outbox[0].body.lower())

    def test_an_unknown_address_says_nothing_and_sends_nothing(self):
        response = self.client.post(reverse('password_reset'),
                                    {'email': 'nobody@example.com'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 0)


class LandingPageTest(TestCase):
    def setUp(self):
        make_qasida(title='Something To Read')

    def test_a_visitor_is_told_what_an_account_is_for(self):
        response = self.client.get(reverse('home'))
        self.assertContains(response, 'Create an account')
        self.assertContains(response, 'Save what you find')

    def test_a_signed_in_reader_gets_their_shelf_instead(self):
        user = User.objects.create_user('reader', 'reader@example.com', GOOD_PASSWORD)
        self.client.force_login(user)
        response = self.client.get(reverse('home'))
        self.assertContains(response, 'Welcome back, reader')
        self.assertNotContains(response, 'Save what you find')


class AdminUserManagementTest(TestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser(
            'root', 'root@example.com', GOOD_PASSWORD)
        self.editor = User.objects.create_user(
            'editor', 'editor@example.com', GOOD_PASSWORD, is_staff=True)
        self.reader = User.objects.create_user(
            'reader', 'reader@example.com', GOOD_PASSWORD)

    def test_a_reader_cannot_reach_the_admin(self):
        self.client.force_login(self.reader)
        response = self.client.get('/admin/auth/user/')
        self.assertNotEqual(response.status_code, 200)

    def test_a_superuser_sees_the_user_list(self):
        self.client.force_login(self.superuser)
        response = self.client.get('/admin/auth/user/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'reader')

    def test_staff_cannot_edit_a_superuser(self):
        from django.contrib import admin as django_admin
        user_admin = django_admin.site._registry[User]
        request = type('R', (), {'user': self.editor})()
        self.assertFalse(user_admin.has_change_permission(request, self.superuser))

    def test_staff_cannot_hand_themselves_more_permission(self):
        from django.contrib import admin as django_admin
        user_admin = django_admin.site._registry[User]
        request = type('R', (), {'user': self.editor})()
        readonly = user_admin.get_readonly_fields(request, self.editor)
        for field in ('is_superuser', 'is_staff', 'user_permissions', 'groups'):
            self.assertIn(field, readonly)

    def test_a_superuser_may_still_set_those_fields(self):
        from django.contrib import admin as django_admin
        user_admin = django_admin.site._registry[User]
        request = type('R', (), {'user': self.superuser})()
        self.assertNotIn('is_staff', user_admin.get_readonly_fields(request, self.editor))


class TemplateCommentTest(TestCase):
    """
    Guard against a template comment being printed to the reader.

    Django's {# #} comment is matched by a lexer rule that does not cross a
    line break, so one wrapped onto a second line is not recognised as a
    comment at all and is emitted as literal text. It renders perfectly well
    in review - it simply appears on the page - and it had reached production
    in six places, including the site header, where it showed on every page.
    """

    # Opens {#, and does not close #} before the line ends.
    MULTILINE_COMMENT = re.compile(r'\{#(?:[^#\n]|#(?!\}))*$', re.M)

    def template_files(self):
        for root in (Path(settings.BASE_DIR) / 'core' / 'templates',
                     Path(settings.BASE_DIR) / 'templates'):
            yield from root.rglob('*.html')
            yield from root.rglob('*.txt')
            yield from root.rglob('*.js')

    def test_no_comment_spans_a_line_break(self):
        offenders = []
        for path in self.template_files():
            for number, line in enumerate(path.read_text().splitlines(), start=1):
                if self.MULTILINE_COMMENT.search(line):
                    offenders.append(f'{path.name}:{number}')
        self.assertEqual(
            offenders, [],
            "A {# #} comment must fit on one line, or the reader sees it. "
            "Use {% comment %}...{% endcomment %} for anything longer.")

    def test_the_detector_would_actually_catch_one(self):
        """A guard that cannot fail is not a guard."""
        self.assertTrue(self.MULTILINE_COMMENT.search('    {# opens here'))
        self.assertFalse(self.MULTILINE_COMMENT.search('    {# closes here #}'))


class RenderedOutputTest(TestCase):
    """No page may show the reader raw template syntax."""

    def setUp(self):
        self.user = User.objects.create_user('reader', 'reader@example.com',
                                             GOOD_PASSWORD)
        self.qasida = make_qasida(title='Rendered', transliteration='latin one',
                                  translation='meaning one')

    def assert_clean(self, response, where):
        body = response.content.decode()
        for leak in ('{#', '{%', '%}'):
            self.assertNotIn(leak, body, f'{where} is showing raw template syntax')

    def test_public_pages_are_clean(self):
        for name in ('home', 'browse', 'search', 'poets', 'categories',
                     'collections', 'login', 'register', 'password_reset'):
            self.assert_clean(self.client.get(reverse(name)), name)

    def test_the_qasida_page_is_clean(self):
        self.assert_clean(self.client.get(self.qasida.get_absolute_url()), 'qasida detail')

    def test_the_qasida_page_is_clean_when_signed_in(self):
        self.client.force_login(self.user)
        self.assert_clean(self.client.get(self.qasida.get_absolute_url()),
                          'qasida detail, signed in')

    def test_account_pages_are_clean(self):
        self.client.force_login(self.user)
        for name in ('my_library', 'my_history', 'my_corrections',
                     'account_settings', 'delete_account'):
            self.assert_clean(self.client.get(reverse(name)), name)

    def test_admin_pages_are_clean(self):
        """The admin has its own overridden templates, and one of them leaked."""
        staff = User.objects.create_superuser('root', 'root@example.com', GOOD_PASSWORD)
        self.client.force_login(staff)
        for path in ('/admin/', '/admin/core/qasida/', '/admin/auth/user/',
                     '/admin/core/collection/', '/admin/core/suggestion/'):
            self.assert_clean(self.client.get(path), path)


class MailConfigTest(TestCase):
    """
    The rules that turn environment variables into SMTP settings.

    Tested against the functions rather than by re-importing settings, because
    settings are read once at startup and a wrong answer here is only ever
    discovered when a password reset fails to arrive.
    """

    def setUp(self):
        from qasida_app import mailconf
        self.mailconf = mailconf

    def test_no_credentials_means_no_mailgun(self):
        self.assertEqual(self.mailconf.mailgun_config('', ''), {})
        self.assertEqual(self.mailconf.mailgun_config('user', ''), {})
        self.assertEqual(self.mailconf.mailgun_config('', 'password'), {})
        self.assertEqual(self.mailconf.mailgun_config(None, None), {})

    def test_credentials_produce_a_complete_smtp_configuration(self):
        config = self.mailconf.mailgun_config('postmaster@mg.example.com', 'secret')
        self.assertEqual(config['EMAIL_BACKEND'], self.mailconf.SMTP_BACKEND)
        self.assertEqual(config['EMAIL_HOST'], 'smtp.mailgun.org')
        self.assertEqual(config['EMAIL_PORT'], 587)
        self.assertEqual(config['EMAIL_HOST_USER'], 'postmaster@mg.example.com')
        self.assertEqual(config['EMAIL_HOST_PASSWORD'], 'secret')
        self.assertTrue(config['EMAIL_USE_TLS'])
        self.assertFalse(config['EMAIL_USE_SSL'])

    def test_the_eu_region_uses_its_own_host(self):
        """A domain created in one region cannot send through the other."""
        for region in ('eu', 'EU', ' Eu '):
            self.assertEqual(
                self.mailconf.mailgun_config('u', 'p', region)['EMAIL_HOST'],
                'smtp.eu.mailgun.org')

    def test_an_unknown_region_falls_back_rather_than_failing(self):
        self.assertEqual(self.mailconf.mailgun_config('u', 'p', 'mars')['EMAIL_HOST'],
                         'smtp.mailgun.org')

    def test_port_465_switches_to_implicit_tls(self):
        """465 is implicit TLS; STARTTLS on it hangs rather than erroring."""
        config = self.mailconf.mailgun_config('u', 'p', port_raw='465')
        self.assertTrue(config['EMAIL_USE_SSL'])
        self.assertFalse(config['EMAIL_USE_TLS'])

    def test_tls_and_ssl_are_never_both_set(self):
        """Django refuses to start if they are."""
        for port in (None, '587', '465', '2525', 'nonsense'):
            config = self.mailconf.mailgun_config('u', 'p', port_raw=port)
            self.assertNotEqual(config['EMAIL_USE_TLS'], config['EMAIL_USE_SSL'],
                                f'both flags agreed on port {port!r}')

    def test_a_blank_port_does_not_crash(self):
        """Compose writes an empty string for a variable left blank in .env."""
        self.assertEqual(self.mailconf.env_int('', 587), 587)
        self.assertEqual(self.mailconf.env_int(None, 587), 587)
        self.assertEqual(self.mailconf.env_int('not a number', 587), 587)
        self.assertEqual(self.mailconf.env_int('2525', 587), 2525)

    def test_flags_accept_the_spellings_people_write(self):
        for raw in ('1', 'true', 'True', 'YES', 'on', ' On '):
            self.assertTrue(self.mailconf.env_flag(raw, False), raw)
        for raw in ('0', 'false', 'no', 'off'):
            self.assertFalse(self.mailconf.env_flag(raw, True), raw)
        # Unset falls back to whatever the caller asked for.
        self.assertTrue(self.mailconf.env_flag('', True))
        self.assertFalse(self.mailconf.env_flag(None, False))

    def test_an_api_key_pasted_instead_of_an_smtp_password_is_recognised(self):
        for key in ('key-3ax6xnjp29jd6fds4gc373sgvjxleqe3',
                    'KEY-3ax6xnjp29jd6fds4gc373sgvjxleqe3',
                    '0123456789abcdef0123456789abcdef'):
            self.assertTrue(self.mailconf.looks_like_an_api_key(key), key)

    def test_a_real_password_is_not_mistaken_for_an_api_key(self):
        for password in ('', None, 'hunter2', 'a long but ordinary passphrase',
                         'Str0ng-SMTP-Password!'):
            self.assertFalse(self.mailconf.looks_like_an_api_key(password),
                             repr(password))

    def test_the_running_configuration_is_consistent(self):
        """Whatever this deployment is set to, it must be usable."""
        from django.conf import settings
        self.assertFalse(settings.EMAIL_USE_TLS and settings.EMAIL_USE_SSL)
        self.assertGreater(settings.EMAIL_TIMEOUT, 0)


class TagCategoryTest(TestCase):
    """Tags sit on one of four axes, and the axis is stored, not re-guessed."""

    def test_the_axes_are_told_apart(self):
        cases = {
            'naat': Tag.CATEGORY_FORM,
            'qasida': Tag.CATEGORY_FORM,
            'hamd': Tag.CATEGORY_FORM,
            'manqbat': Tag.CATEGORY_FORM,
            'qasida-sufi': Tag.CATEGORY_FORM,
            'qasida-hadra': Tag.CATEGORY_FORM,
            'durood-o-salam': Tag.CATEGORY_FORM,
            'urdu': Tag.CATEGORY_LANGUAGE,
            'arabic': Tag.CATEGORY_LANGUAGE,
            'maqam-hijaz': Tag.CATEGORY_MAQAM,
            'bahr-kamil': Tag.CATEGORY_BAHR,
            'lyrics-in-images': Tag.CATEGORY_CONDITION,
            'transliterated': Tag.CATEGORY_CONDITION,
        }
        for name, expected in cases.items():
            self.assertEqual(Tag.classify(name), expected, name)

    def test_sufi_hadra_and_manqabat_are_not_filed_with_maqams_or_languages(self):
        """The separation that was asked for, stated as a test."""
        for name in ('qasida-sufi', 'qasida-hadra', 'manqbat'):
            category = Tag.classify(name)
            self.assertEqual(category, Tag.CATEGORY_FORM, name)
            self.assertNotIn(category, (Tag.CATEGORY_MAQAM, Tag.CATEGORY_LANGUAGE))

    def test_an_unknown_tag_is_left_unfiled_rather_than_guessed(self):
        self.assertEqual(Tag.classify('something-nobody-anticipated'),
                         Tag.CATEGORY_OTHER)

    def test_a_new_tag_files_itself(self):
        self.assertEqual(Tag.objects.create(name='maqam-nahawand').category,
                         Tag.CATEGORY_MAQAM)

    def test_an_editors_choice_is_never_overwritten(self):
        """Filing happens once; a correction has to survive later saves."""
        tag = Tag.objects.create(name='maqam-hijaz', category=Tag.CATEGORY_FORM)
        tag.save()
        tag.refresh_from_db()
        self.assertEqual(tag.category, Tag.CATEGORY_FORM)

    def test_the_label_drops_the_taxonomy_prefix(self):
        self.assertEqual(Tag(name='maqam-hijaz').label, 'Hijaz')
        self.assertEqual(Tag(name='bahr-kamil').label, 'Kamil')
        self.assertEqual(Tag(name='qasida-sufi').label, 'Sufi')

    def test_awkward_slugs_get_a_readable_name(self):
        self.assertEqual(Tag(name='lyrics-in-images').label, 'Lyrics only as scans')
        self.assertEqual(Tag(name='manqbat').label, 'Manqabat')

    def test_the_categories_page_separates_the_axes(self):
        work = make_qasida(title='Tagged', language='Arabic')
        for name in ('qasida-sufi', 'urdu', 'maqam-hijaz', 'bahr-kamil'):
            work.tags.add(Tag.objects.create(name=name))
        response = self.client.get(reverse('categories'))
        self.assertEqual(response.status_code, 200)
        seen = [g['category'] for g in response.context['groups']]
        for expected in (Tag.CATEGORY_FORM, Tag.CATEGORY_LANGUAGE,
                         Tag.CATEGORY_MAQAM, Tag.CATEGORY_BAHR):
            self.assertIn(expected, seen)

    def test_each_tag_appears_under_exactly_one_axis(self):
        work = make_qasida(title='Tagged')
        for name in ('naat', 'urdu', 'maqam-rast'):
            work.tags.add(Tag.objects.create(name=name))
        groups = self.client.get(reverse('categories')).context['groups']
        placements = [item['name'] for group in groups for item in group['items']]
        self.assertEqual(sorted(placements), sorted(set(placements)))


class LiveSearchTest(TestCase):
    """The suggestions shown while the reader is still typing."""

    def setUp(self):
        self.url = reverse('search_suggest')
        self.qasida = make_qasida(title='Findable Work', author='Some Poet',
                                  language='Arabic', arabic_title='مكتبة القصائد')

    def test_it_answers_json(self):
        response = self.client.get(self.url, {'q': 'findable'})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['total'], 1)
        self.assertEqual(body['results'][0]['title'], 'Findable Work')
        self.assertEqual(body['results'][0]['url'], self.qasida.get_absolute_url())

    def test_a_single_letter_runs_no_query(self):
        """One letter matches most of the library and answers nothing."""
        body = self.client.get(self.url, {'q': 'f'}).json()
        self.assertEqual(body['results'], [])
        self.assertEqual(body['total'], 0)

    def test_an_empty_query_is_harmless(self):
        self.assertEqual(self.client.get(self.url).json()['results'], [])

    def test_it_never_suggests_an_unapproved_work(self):
        Qasida.objects.create(title='Findable Secret', lyrics='hidden',
                              language='Arabic')
        titles = [r['title'] for r in
                  self.client.get(self.url, {'q': 'findable'}).json()['results']]
        self.assertEqual(titles, ['Findable Work'])

    def test_results_are_capped_but_the_true_total_is_reported(self):
        from .views import SUGGEST_LIMIT
        for n in range(SUGGEST_LIMIT + 4):
            make_qasida(title=f'Findable Extra {n}')
        body = self.client.get(self.url, {'q': 'findable'}).json()
        self.assertEqual(len(body['results']), SUGGEST_LIMIT)
        self.assertGreater(body['total'], SUGGEST_LIMIT)

    def test_arabic_typed_without_vowel_marks_still_matches(self):
        body = self.client.get(self.url, {'q': 'مكتبة'}).json()
        self.assertEqual(body['total'], 1)


class AdminListingTest(TestCase):
    """How the admin lists things, which is where editors spend their time."""

    def setUp(self):
        self.staff = User.objects.create_superuser('root', 'root@example.com',
                                                   GOOD_PASSWORD)
        self.client.force_login(self.staff)

    def test_lists_are_twenty_to_a_page(self):
        for n in range(25):
            make_qasida(title=f'Work {n:02}')
        response = self.client.get('/admin/core/qasida/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['cl'].result_list), 20)

    def test_pagination_is_rendered_above_the_grid_as_well_as_below(self):
        for n in range(25):
            make_qasida(title=f'Work {n:02}')
        body = self.client.get('/admin/core/qasida/').content.decode()
        self.assertIn('q-paginator-top', body)
        self.assertEqual(body.count('class="paginator"'), 2)

    def test_the_tag_list_can_be_filtered_by_axis(self):
        Tag.objects.create(name='maqam-rast')
        Tag.objects.create(name='naat')
        response = self.client.get('/admin/core/tag/',
                                   {'category__exact': Tag.CATEGORY_MAQAM})
        self.assertEqual([t.name for t in response.context['cl'].result_list],
                         ['maqam-rast'])


class LayerPairingTest(TestCase):
    """
    How a transliteration and a translation are set against the verses.

    Sources are inconsistent about blank lines, so the same work can arrive
    with the original as one block and its transliteration split into verses,
    or the reverse. Whatever the shape, neither layer may go missing: it is
    paired if it honestly can be, and shown whole if it cannot.
    """

    def page(self, **fields):
        qasida = make_qasida(**fields)
        return qasida, self.client.get(qasida.get_absolute_url()).content.decode()

    def test_matching_stanza_counts_pair_up(self):
        qasida, body = self.page(
            lyrics='alif\n\nbaa',
            transliteration='ALEF-one\n\nBAA-two',
            translation='first\n\nsecond')
        from .templatetags.qasida_extras import (stanza_rows,
                                                 transliteration_is_aligned,
                                                 translation_is_aligned)
        self.assertTrue(transliteration_is_aligned(qasida))
        self.assertTrue(translation_is_aligned(qasida))
        rows = stanza_rows(qasida)
        self.assertEqual(rows[0]['latin'], 'ALEF-one')
        self.assertEqual(rows[1]['translation'], 'second')
        self.assertIn('ALEF-one', body)

    def test_differing_stanzas_but_equal_lines_are_paired_line_for_line(self):
        """One block against three verses is still the same poem, line by line."""
        from .templatetags.qasida_extras import stanza_rows, transliteration_is_aligned
        qasida, body = self.page(
            lyrics='alif\nbaa\njeem',
            transliteration='ONE-latin\n\nTWO-latin\n\nTHREE-latin')
        self.assertTrue(transliteration_is_aligned(qasida))
        self.assertEqual(stanza_rows(qasida)[0]['latin'],
                         'ONE-latin\nTWO-latin\nTHREE-latin')
        self.assertIn('ONE-latin', body)

    def test_a_layer_that_cannot_be_paired_is_still_shown(self):
        """
        The bug this class exists for.

        An unpairable transliteration used to vanish from the page entirely,
        while the downloadable file still contained it.
        """
        from .templatetags.qasida_extras import transliteration_is_aligned
        qasida, body = self.page(
            lyrics='alif\nbaa\njeem',
            transliteration='ONE-latin\n\nTWO-latin')
        self.assertFalse(transliteration_is_aligned(qasida))
        self.assertIn('ONE-latin', body)
        self.assertIn('TWO-latin', body)
        self.assertIn('Transliteration', body)

    def test_an_unpairable_translation_is_still_shown(self):
        # Two stanzas of two lines against a single line: neither the stanza
        # counts nor the line counts agree, so there is no honest pairing.
        from .templatetags.qasida_extras import translation_is_aligned
        qasida, body = self.page(lyrics='alif\n\nbaa',
                                 translation='only one line of meaning')
        self.assertFalse(translation_is_aligned(qasida))
        self.assertIn('only one line of meaning', body)

    def test_a_layer_is_never_shown_twice(self):
        """Paired above and repeated whole below would read as a duplicate."""
        _, body = self.page(lyrics='alif\n\nbaa',
                            transliteration='ALEF-one\n\nBAA-two')
        self.assertEqual(body.count('ALEF-one'), 1)

    def test_pairing_never_puts_the_wrong_verse_together(self):
        """Looser matching would misalign, which is worse than not pairing."""
        from .templatetags.qasida_extras import stanza_rows
        qasida = make_qasida(lyrics='alif\n\nbaa\n\njeem',
                             transliteration='ALEF-one\n\nBAA-two')
        for row in stanza_rows(qasida):
            self.assertEqual(row['latin'], '')

    def test_a_work_with_no_transliteration_gains_no_empty_section(self):
        _, body = self.page(lyrics='alif\nbaa', transliteration='')
        self.assertNotIn('Latin script', body)

    def test_every_layer_a_work_has_reaches_the_page_somehow(self):
        """Whatever the shape, nothing the record holds is silently lost."""
        shapes = [
            ('alif\n\nbaa', 'L-one\n\nL-two', 'T-one\n\nT-two'),
            ('alif\nbaa', 'L-one\n\nL-two', 'T-one\nT-two'),
            ('alif\nbaa\njeem', 'L-one\n\nL-two', 'T-one'),
            ('alif', 'L-one\n\nL-two\n\nL-three', 'T-one\n\nT-two'),
        ]
        for lyrics, latin, translation in shapes:
            _, body = self.page(lyrics=lyrics, transliteration=latin,
                                translation=translation)
            self.assertIn('L-one', body, f'transliteration lost for {lyrics!r}')
            self.assertIn('T-one', body, f'translation lost for {lyrics!r}')

    def test_the_download_and_the_page_agree_on_what_exists(self):
        """The file used to contain a layer the page had dropped."""
        qasida = make_qasida(lyrics='alif\nbaa\njeem',
                             transliteration='ONE-latin\n\nTWO-latin')
        body = self.client.get(qasida.get_absolute_url()).content.decode()
        pdf = pdf_text(self.client.get(
            reverse('qasida_download', args=[qasida.slug]),
            {'original': '1', 'latin': '1'}).content)
        self.assertIn('ONE-latin', body)
        self.assertIn('ONE-latin', pdf)
