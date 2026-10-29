import io
import unittest
from contextlib import redirect_stdout, redirect_stderr
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import list_models
from core import assistant


class ModelsTests(unittest.TestCase):
    def test_client_uses_configured_model_and_endpoint(self):
        config = SimpleNamespace(GIGACHAT_BASE_URL='https://api.giga.chat/v1',
                                 GIGACHAT_CREDENTIALS='test', GIGACHAT_MODEL='GigaChat-2')
        with patch.object(assistant, 'settings', config), patch.object(assistant, 'GigaChat') as client:
            assistant.build_client()
        self.assertEqual(client.call_args.kwargs['model'], 'GigaChat-2')
        self.assertEqual(client.call_args.kwargs['base_url'], config.GIGACHAT_BASE_URL)
        self.assertFalse(client.call_args.kwargs['verify_ssl_certs'])

    def test_model_selection_is_not_hardcoded(self):
        config = SimpleNamespace(GIGACHAT_BASE_URL='https://example.test/v1',
                                 GIGACHAT_CREDENTIALS='test', GIGACHAT_MODEL='GigaChat-3-Pro')
        with patch.object(assistant, 'settings', config), patch.object(assistant, 'GigaChat') as client:
            assistant.build_client()
        self.assertEqual(client.call_args.kwargs['model'], 'GigaChat-3-Pro')

    def test_list_models_without_other_settings(self):
        client = MagicMock()
        client.__enter__.return_value.get_models.return_value.data = [SimpleNamespace(id_='GigaChat-2'), SimpleNamespace(id_='Embeddings')]
        output = io.StringIO()
        with patch.object(list_models, 'settings', SimpleNamespace(GIGACHAT_CREDENTIALS='test', GIGACHAT_MODEL='GigaChat-2')), \
             patch.object(list_models, 'build_client', return_value=client), redirect_stdout(output):
            result = list_models.main([])
        self.assertEqual(result, 0)
        self.assertIn('GigaChat-2', output.getvalue())

    def test_list_models_failure_does_not_print_secrets(self):
        output = io.StringIO()
        with patch.object(list_models, 'settings', SimpleNamespace(GIGACHAT_CREDENTIALS='secret')), \
             patch.object(list_models, 'build_client', side_effect=ValueError('secret')), redirect_stderr(output):
            result = list_models.main([])
        self.assertEqual(result, 1)
        self.assertNotIn('secret', output.getvalue())

    def test_check_sends_configured_model_and_lists_on_error(self):
        client = MagicMock()
        api = client.__enter__.return_value
        api.get_models.return_value.data = [SimpleNamespace(id_='GigaChat-2')]
        api.chat.side_effect = RuntimeError('secret')
        out, err = io.StringIO(), io.StringIO()
        with patch.object(list_models, 'settings', SimpleNamespace(GIGACHAT_CREDENTIALS='test', GIGACHAT_MODEL='unknown')), \
             patch.object(list_models, 'build_client', return_value=client), redirect_stdout(out), redirect_stderr(err):
            result = list_models.main(['--check'])
        self.assertEqual(result, 1)
        self.assertIn('GigaChat-2', out.getvalue())
        self.assertEqual(api.chat.call_args.args[0].model, 'unknown')
        self.assertNotIn('secret', err.getvalue())

    def test_check_success(self):
        client = MagicMock()
        api = client.__enter__.return_value
        api.get_models.return_value.data = [SimpleNamespace(id_='GigaChat-2')]
        api.chat.return_value.choices = [SimpleNamespace(message=SimpleNamespace(content='Здравствуйте!'))]
        out = io.StringIO()
        with patch.object(list_models, 'settings', SimpleNamespace(GIGACHAT_CREDENTIALS='test', GIGACHAT_MODEL='GigaChat-2')), \
             patch.object(list_models, 'build_client', return_value=client), redirect_stdout(out):
            result = list_models.main(['--check'])
        self.assertEqual(result, 0)
        self.assertIn('успешно', out.getvalue())
