import io
import json
import urllib.error
from unittest import mock

from django.test import SimpleTestCase, override_settings

from core import gemini


def _answer(**fields):
    body = {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': json.dumps(
        {'language': 'Urdu', 'translation': '', 'transliteration': '', **fields})}]}}]}
    response = mock.MagicMock()
    response.read.return_value = json.dumps(body).encode()
    response.__enter__.return_value = response
    return response


@override_settings(GEMINI_API_KEY='sekret', GEMINI_MODEL='gemini-2.5-flash-lite')
class GeminiTests(SimpleTestCase):
    def test_disabled_without_key(self):
        with override_settings(GEMINI_API_KEY=''):
            self.assertFalse(gemini.enabled())
            with self.assertRaises(gemini.GeminiFailed):
                gemini.process('x')

    def test_process_sends_key_in_header_not_url(self):
        with mock.patch('urllib.request.urlopen', return_value=_answer(translation='Hello', transliteration='salam')) as op:
            out = gemini.process('سلام')
        req = op.call_args[0][0]
        self.assertNotIn('sekret', req.full_url)
        self.assertEqual(req.get_header('X-goog-api-key'), 'sekret')
        self.assertIn('gemini-2.5-flash-lite', req.full_url)
        self.assertEqual((out['translation'], out['transliteration'], out['engine']),
                         ('Hello', 'salam', 'Gemini (gemini-2.5-flash-lite)'))

    def test_only_what_was_asked_is_returned(self):
        with mock.patch('urllib.request.urlopen', return_value=_answer(translation='Hi', transliteration='x')):
            out = gemini.process('سلام', want_transliteration=False)
        self.assertEqual(out['transliteration'], '')

    def test_chunks_split_at_stanzas(self):
        text = 'a\nb\n\nc\nd\n\ne'
        self.assertEqual(gemini.split_chunks(text, limit=8), ['a\nb\n\nc\nd', 'e'])
        self.assertEqual(gemini.split_chunks('x' * 5 + '\n' + 'y' * 5, limit=6), ['xxxxx', 'yyyyy'])

    def test_http_error_never_leaks_key(self):
        err = urllib.error.HTTPError('u', 400, 'bad', {}, io.BytesIO(b'invalid key sekret'))
        with mock.patch('urllib.request.urlopen', side_effect=err):
            with self.assertRaises(gemini.GeminiFailed) as ctx:
                gemini.process('سلام')
        self.assertNotIn('sekret', str(ctx.exception))

    def test_retries_on_429_then_succeeds(self):
        err = urllib.error.HTTPError('u', 429, 'slow', {}, io.BytesIO(b''))
        with mock.patch('urllib.request.urlopen', side_effect=[err, _answer(translation='ok')]), \
                mock.patch('time.sleep'):
            self.assertEqual(gemini.process('سلام')['translation'], 'ok')

    def test_blocked_or_truncated_answer_fails(self):
        blocked = mock.MagicMock()
        blocked.read.return_value = json.dumps({'promptFeedback': {'blockReason': 'SAFETY'}}).encode()
        blocked.__enter__.return_value = blocked
        with mock.patch('urllib.request.urlopen', return_value=blocked):
            with self.assertRaises(gemini.GeminiFailed):
                gemini.process('سلام')
