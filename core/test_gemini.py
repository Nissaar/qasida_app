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


@override_settings(GEMINI_API_KEY='sekret', GEMINI_MODEL='gemini-3.5-flash-lite')
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
        self.assertIn('gemini-3.5-flash-lite', req.full_url)
        self.assertEqual((out['translation'], out['transliteration'], out['engine']),
                         ('Hello', 'salam', 'Gemini (gemini-3.5-flash-lite)'))

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


@override_settings(GEMINI_API_KEY='sekret', GEMINI_MODEL='', GEMINI_MODELS='model-a, model-b')
class GeminiModelsTests(SimpleTestCase):
    def _models_called(self, op):
        return [c[0][0].full_url.split('/models/')[1].split(':')[0] for c in op.call_args_list]

    def test_models_come_from_the_list_in_order(self):
        self.assertEqual(gemini.models(), ['model-a', 'model-b'])
        self.assertEqual(gemini.model_name(), 'model-a')

    def test_single_model_setting_still_works(self):
        with override_settings(GEMINI_MODELS='', GEMINI_MODEL='solo'):
            self.assertEqual(gemini.models(), ['solo'])

    def test_next_model_answers_when_the_first_fails(self):
        err = urllib.error.HTTPError('u', 404, 'gone', {}, io.BytesIO(b''))
        with mock.patch('urllib.request.urlopen', side_effect=[err, _answer(translation='ok')]) as op:
            out = gemini.process('سلام')
        self.assertEqual(self._models_called(op), ['model-a', 'model-b'])
        self.assertEqual((out['translation'], out['engine']), ('ok', 'Gemini (model-b)'))

    def test_a_chosen_model_is_the_only_one_tried(self):
        err = urllib.error.HTTPError('u', 404, 'gone', {}, io.BytesIO(b''))
        with mock.patch('urllib.request.urlopen', side_effect=err) as op:
            with self.assertRaises(gemini.GeminiFailed):
                gemini.process('سلام', model='model-b')
        self.assertEqual(set(self._models_called(op)), {'model-b'})

    def test_an_unconfigured_model_is_refused(self):
        with mock.patch('urllib.request.urlopen') as op:
            with self.assertRaises(gemini.GeminiFailed):
                gemini.process('سلام', model='other')
        op.assert_not_called()

    def test_to_script_writes_the_original_script(self):
        with mock.patch('urllib.request.urlopen', return_value=_answer(text='سلام')) as op:
            out = gemini.to_script('salam', 'Urdu')
        self.assertEqual((out['text'], out['engine']), ('سلام', 'Gemini (model-a)'))
        sent = json.loads(op.call_args[0][0].data)
        self.assertIn('Urdu', sent['contents'][0]['parts'][0]['text'])

    def test_to_script_needs_a_language(self):
        with self.assertRaises(gemini.GeminiFailed):
            gemini.to_script('salam', '')

    def test_to_script_rejects_an_empty_answer(self):
        with mock.patch('urllib.request.urlopen', return_value=_answer(text='')):
            with self.assertRaises(gemini.GeminiFailed):
                gemini.to_script('salam', 'Urdu')
