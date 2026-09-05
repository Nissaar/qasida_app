"""
Tests for the library and for reader accounts.

The account tests pin the behaviour that would be expensive to get wrong on a
site that is already in production: who can see what, what the review gate
still hides once someone is signed in, and that nothing a reader saved can be
reached or changed by anyone else.
"""

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

    def test_download_offers_only_what_was_asked_for(self):
        qasida = make_qasida(title='Layered', lyrics='asl', language='Arabic',
                             transliteration='latin line',
                             translation='meaning line',
                             translation_origin=Qasida.TRANSLATION_SOURCE)
        url = reverse('qasida_download', args=[qasida.slug])

        plain = self.client.get(url, {'original': '1'})
        self.assertEqual(plain.status_code, 200)
        self.assertNotIn('latin line', plain.content.decode())

        full = self.client.get(url, {'original': '1', 'latin': '1', 'translation': '1'})
        body = full.content.decode()
        self.assertIn('latin line', body)
        self.assertIn('meaning line', body)


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
