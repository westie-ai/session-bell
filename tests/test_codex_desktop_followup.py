"""Offline shape/guard regressions, never connect to the desktop."""
import copy
import unittest
import uuid
from unittest.mock import patch
import smoke_codex_desktop_followup as probe


class FollowupContractTests(unittest.TestCase):
    def setUp(self):
        self.params = probe.build_followup(str(uuid.uuid4()), str(uuid.uuid4()))

    def test_plain_text_includes_desktop_elements_and_empty_attachments(self):
        probe.validate_followup(self.params)
        self.assertEqual(self.params['turnStart']['request']['input'][0]['text_elements'], [])
        self.assertEqual(self.params['turnStart']['context']['attachments'], [])

    def test_missing_null_or_nonempty_elements_are_rejected(self):
        for invalid in (None, 'missing', [{'unexpected': True}]):
            params = copy.deepcopy(self.params)
            item = params['turnStart']['request']['input'][0]
            if invalid == 'missing':
                item.pop('text_elements')
            else:
                item['text_elements'] = invalid
            with self.assertRaises(ValueError):
                probe.validate_followup(params)

    def test_mismatched_thread_and_configuration_override_are_rejected(self):
        for key, value in (('threadId', str(uuid.uuid4())), ('model', 'override'), ('approvalPolicy', 'never')):
            params = copy.deepcopy(self.params)
            params['turnStart']['request'][key] = value
            with self.assertRaises(ValueError):
                probe.validate_followup(params)

    def test_arbitrary_message_cannot_use_test_sender(self):
        self.params['turnStart']['request']['input'][0]['text'] = 'unrelated work'
        with self.assertRaises(ValueError):
            probe.validate_followup(self.params)

    def test_native_validation_fails_closed(self):
        with patch.object(probe.subprocess, 'run') as run:
            run.return_value.returncode = 1
            with self.assertRaises(ValueError):
                probe.validate_native_decoder(self.params)

    def test_builders_do_not_share_mutable_input(self):
        first = probe.build_followup(str(uuid.uuid4()), str(uuid.uuid4()))
        first['turnStart']['request']['input'][0]['text_elements'].append({})
        probe.validate_followup(self.params)

    def test_marker_accepts_only_exact_copy_escape_variant(self):
        self.assertTrue(probe.is_test_marker(probe.MARKER))
        self.assertTrue(probe.is_test_marker(probe.MARKER.replace('_', '\\_')))
        self.assertFalse(probe.is_test_marker(probe.MARKER + '\nDo unrelated work'))
        self.assertFalse(probe.is_test_marker('Quoted: ' + probe.MARKER))


if __name__ == '__main__':
    unittest.main()
