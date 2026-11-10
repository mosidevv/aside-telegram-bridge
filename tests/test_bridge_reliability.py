"""Regression tests use private state and never call Telegram or Aside."""
import importlib.util
import json
import os
from pathlib import Path
import queue
import tempfile
import unittest
from unittest import mock


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        config = root / 'config.json'
        config.write_text(json.dumps({'token': 'test', 'chat_id': 1,
                                     'sessions_dir': str(root / 'sessions')}))
        spec = importlib.util.spec_from_file_location('reliability_bridge',
                    Path(__file__).resolve().parents[1] / 'bridge.py')
        self.b = importlib.util.module_from_spec(spec)
        with mock.patch.dict(os.environ, {'ASIDE_BRIDGE_DIR': str(root),
                                         'ASIDE_BRIDGE_CONFIG': str(config)}):
            spec.loader.exec_module(self.b)
        self.b.state['session_id'] = 'current'
        self.real_send_text = self.b.send_text
        self.sent = []
        self.b.send_text = self.sent.append
        self.b.tg = mock.Mock(return_value={'ok': True, 'result': {'message_id': 7}})
        self.b.log = lambda _: None

    def test_old_approval_cannot_authorize_current_session(self):
        self.b.state['approval'] = {'token': 'abc', 'session_id': 'old',
                                    'action': 'send', 'details': ''}
        self.b._handle_approval_tap('apv:approve:abc', 7)
        self.assertTrue(self.b.TASKS.empty())
        self.assertIsNotNone(self.b.state['approval'])

    def test_old_question_cannot_inject_into_current_session(self):
        self.b.state['question'] = {'token': 'abc', 'session_id': 'old',
                                    'options': ['yes'], 'header': 'Choice'}
        self.b._handle_question_tap('qst:abc:0', 7)
        self.assertTrue(self.b.TASKS.empty())

    def test_negative_question_index_is_rejected(self):
        self.b.state['question'] = {'token': 'abc', 'session_id': 'current',
                                    'options': ['yes'], 'header': 'Choice'}
        self.b._handle_question_tap('qst:abc:-1', 7)
        self.assertTrue(self.b.TASKS.empty())

    def test_worker_preserves_command_barrier(self):
        b = self.b
        b.TASKS = queue.Queue()
        for item in [('msg', 'before'), ('cmd', '/new'), ('msg', 'after')]:
            b.TASKS.put(item)
        seen = []
        b.handle_message = lambda text: seen.append(text)
        b.heavy_new = lambda: seen.append('/new')
        real_get = b.TASKS.get
        def bounded_get(*args, **kwargs):
            if b.TASKS.empty():
                raise SystemExit()
            return real_get(*args, **kwargs)
        b.TASKS.get = bounded_get
        with self.assertRaises(SystemExit):
            b.worker_loop()
        self.assertEqual(seen, ['before', '/new', 'after'])

    def test_failure_after_progress_is_reported(self):
        b = self.b
        b.session_msg_file = lambda _: None
        b.run_aside = lambda *a, **kw: (-1, '', 'turn timed out')
        b.stream_new = lambda path, pos, turn: (pos, True)
        b.TurnStream = mock.Mock()
        b.handle_message('work')
        self.assertTrue(any('timed out' in text for text in self.sent))

    def test_waiting_tasks_survive_reload(self):
        self.b.TASKS.put(('msg', 'first'))
        self.b.TASKS.put(('cmd', '/new'))
        saved = json.loads(Path(self.b.STATE_PATH).read_text())
        self.assertEqual(saved['queued_tasks'], [['msg', 'first'], ['cmd', '/new']])

    def test_dequeued_task_is_recoverable_until_completed(self):
        self.b.TASKS.put(('msg', 'first'))
        self.assertEqual(self.b.TASKS.get(), ('msg', 'first'))
        saved = json.loads(Path(self.b.STATE_PATH).read_text())
        self.assertEqual(saved['active_task'], ['msg', 'first'])
        self.assertEqual(saved['queued_tasks'], [])
        self.b.TASKS.task_done()
        saved = json.loads(Path(self.b.STATE_PATH).read_text())
        self.assertIsNone(saved['active_task'])

    def test_receipt_and_queue_are_committed_together(self):
        self.b.INCOMING.update_id = 42
        self.b.process_update({'update_id': 42, 'message': {'chat': {'id': 1}, 'text': 'hello'}})
        saved = json.loads(Path(self.b.STATE_PATH).read_text())
        self.assertEqual(saved['offset'], 43)
        self.assertEqual(saved['queued_tasks'], [['msg', 'hello']])

    def test_failed_receipt_write_does_not_ack_or_enqueue(self):
        self.b.INCOMING.update_id = 42
        self.b.save_json = mock.Mock(side_effect=OSError('disk full'))
        with self.assertRaises(OSError):
            self.b.process_update({'update_id': 42, 'message': {'chat': {'id': 1}, 'text': 'hello'}})
        self.assertEqual(self.b.state['offset'], 0)
        self.assertTrue(self.b.TASKS.empty())

    def test_reload_restores_fifo(self):
        self.b.TASKS.put(('msg', 'first'))
        self.b.TASKS.put(('cmd', '/new'))
        self.b.state = json.loads(Path(self.b.STATE_PATH).read_text())
        restored = self.b.DurableTaskQueue()
        self.assertEqual(restored.get(), ('msg', 'first'))
        restored.task_done()
        self.assertEqual(restored.get(), ('cmd', '/new'))

    def test_missing_session_does_not_start_untracked_exec(self):
        self.b.state['session_id'] = None
        self.b.run_aside = mock.Mock(return_value=(0, '', ''))
        self.b.TurnStream = mock.Mock()
        self.b.handle_message('work')
        self.b.run_aside.assert_not_called()
        self.assertTrue(any('/new' in text for text in self.sent))

    def test_session_switch_during_execution_does_not_rebind_question(self):
        b = self.b
        transcript = Path(self.tmp.name) / 'messages.jsonl'
        transcript.write_text('')
        b.session_msg_file = lambda _: str(transcript)
        def run(*args, **kwargs):
            b.state['session_id'] = 'different'
            transcript.write_text(json.dumps({'role': 'assistant', 'stopReason': 'stop',
                'content': [{'type': 'text', 'text': '[[QUESTION]]\n{"questions":[{"header":"Pick","question":"Which?","options":[{"label":"Yes"}]}]}\n[[/QUESTION]]'}]}) + '\n')
            return 0, '', ''
        b.run_aside = run
        b.handle_message('question')
        self.assertEqual(b.state['question']['session_id'], 'current')

    def test_approved_work_stays_bound_after_later_switch(self):
        b = self.b
        b.state['approval'] = {'token': 'abc', 'session_id': 'current', 'action': 'send', 'details': ''}
        b._handle_approval_tap('apv:approve:abc', 7)
        b.state['session_id'] = 'different'
        kind, payload = b.TASKS.get_nowait()
        self.assertEqual(kind, 'msg')
        self.assertEqual(payload['session_id'], 'current')
        self.assertIn('APPROVAL GRANTED', payload['text'])

    def test_restart_retains_interrupted_work_without_replaying(self):
        b = self.b
        b.state['active_task'] = ['msg', 'possibly already sent']
        b.TASKS.put(('msg', 'waiting'))
        with mock.patch.object(b, '_reap_stale_exec', create=True) as reap, \
             mock.patch.object(b.threading, 'Thread'), \
             mock.patch.object(b, 'tg', side_effect=SystemExit):
            with self.assertRaises(SystemExit):
                b.main()
        self.assertEqual(b.state['interrupted_tasks'], [['msg', 'possibly already sent']])
        self.assertIsNone(b.state['active_task'])
        self.assertEqual(b.TASKS.qsize(), 1)
        reap.assert_not_called()

    def test_failed_state_write_leaves_active_task_recoverable(self):
        self.b.TASKS.put(('msg', 'first'))
        self.b.TASKS.get()
        self.b.save_json = mock.Mock(side_effect=OSError('disk full'))
        with self.assertRaises(OSError):
            self.b.TASKS.task_done()
        self.assertEqual(self.b.state['active_task'], ['msg', 'first'])

    def test_invalid_effort_callback_does_not_change_settings(self):
        self.b.handle_callback({'id': 'x', 'from': {'id': 1}, 'data': 'eff:invalid',
                                'message': {'message_id': 7}})
        self.assertIsNone(self.b.state['effort_next'])

    def test_failed_telegram_delivery_is_retained_and_retried(self):
        b = self.b
        b.tg.side_effect = OSError('network unavailable')
        with mock.patch.object(b.time, 'sleep'):
            self.real_send_text('important answer')
        saved = json.loads(Path(b.STATE_PATH).read_text())
        self.assertEqual(saved['undelivered_texts'], ['important answer'])
        b.tg.side_effect = None
        b.flush_outbox()
        saved = json.loads(Path(b.STATE_PATH).read_text())
        self.assertEqual(saved['undelivered_texts'], [])
        self.assertEqual(b.tg.call_args.args[1]['text'], 'important answer')

    def test_telegram_not_ok_is_not_treated_as_delivery(self):
        self.b.tg.return_value = {'ok': False, 'description': 'Too Many Requests'}
        with mock.patch.object(self.b.time, 'sleep'):
            self.real_send_text('answer')
        saved = json.loads(Path(self.b.STATE_PATH).read_text())
        self.assertEqual(saved['undelivered_texts'], ['answer'])

    def test_later_reply_does_not_overtake_failed_delivery(self):
        b = self.b
        b.tg.side_effect = OSError('offline')
        with mock.patch.object(b.time, 'sleep'):
            self.real_send_text('first')
        b.tg.side_effect = None
        b.tg.reset_mock()
        self.real_send_text('second')
        self.assertEqual([call.args[1]['text'] for call in b.tg.call_args_list], ['first', 'second'])


    def _model_cmd(self, arg, providers, probe=(True, 'ok')):
        b = self.b
        b.state['model'] = 'claude-sonnet-5'
        b.run_in_background = lambda fn, *a: fn(*a)
        with mock.patch.object(b, 'read_connected_providers',
                               return_value=frozenset(providers)), \
                mock.patch.object(b, 'probe_model',
                                  return_value=probe) as self.probe:
            b.handle_command('/model ' + arg)

    def test_model_command_rejects_unconnected_provider(self):
        self._model_cmd('gpt-5.6-sol', ['claude-code'])
        self.assertEqual(self.b.state['model'], 'claude-sonnet-5')
        self.assertIn('not switching', self.sent[-1])

    def test_model_command_rejects_unmapped_model(self):
        self._model_cmd('gpt-6.1-sol', ['claude-code', 'openai-codex'])
        self.assertEqual(self.b.state['model'], 'claude-sonnet-5')

    def test_model_command_accepts_connected_provider(self):
        self._model_cmd('gpt-5.6-sol', ['claude-code', 'openai-codex'])
        self.assertEqual(self.b.state['model'], 'gpt-5.6-sol')
        self.assertIn('openai-codex/gpt-5.6-sol', self.sent[-1])

    def test_model_command_rejects_model_that_fails_probe(self):
        self._model_cmd('gpt-5.6-sol', ['claude-code', 'openai-codex'],
                        probe=(False, 'not available for this account'))
        self.assertEqual(self.b.state['model'], 'claude-sonnet-5')
        self.assertIn('not available for this account', self.sent[-1])

    def test_model_command_accepts_builtin_aside_provider(self):
        self._model_cmd('aside/gpt-6.1-sol', ['claude-code'])
        self.assertEqual(self.b.state['model'], 'aside/gpt-6.1-sol')
        self.probe.assert_called_once()

    def test_model_command_skips_probe_when_rejected_early(self):
        self._model_cmd('nonsense', ['claude-code'])
        self.probe.assert_not_called()

    def test_model_switch_does_not_block_polling_thread(self):
        b = self.b
        started = []
        b.run_in_background = lambda fn, *a: started.append((fn, a))
        b.state['model'] = 'claude-sonnet-5'
        with mock.patch.object(b, 'read_connected_providers',
                               return_value=frozenset(['claude-code'])), \
                mock.patch.object(b, 'probe_model') as probe:
            b.handle_command('/model sonnet')
            probe.assert_not_called()           # nothing slow ran inline
            self.assertEqual(len(started), 1)
            b.handle_command('/model opus')     # second request while busy
            self.assertEqual(len(started), 1)
            self.assertIn('still checking', self.sent[-1])
            probe.return_value = (True, 'ok')
            fn, args = started[0]
            fn(*args)                           # background work finishes
        self.assertTrue(b.MODEL_SWITCH_LOCK.acquire(blocking=False))
        b.MODEL_SWITCH_LOCK.release()

    def test_poison_update_is_retried_then_skipped_not_fatal(self):
        b = self.b
        b.state['offset'] = 5
        update = {'update_id': 5, 'message': {'chat': {'id': 1}, 'text': 'x'}}
        b.process_update = mock.Mock(side_effect=ValueError('boom'))
        with mock.patch.object(b.time, 'sleep'):
            b.dispatch_updates([update])
            self.assertEqual(b.state['offset'], 5)   # retried, same offset
            b.dispatch_updates([update])
            self.assertEqual(b.state['offset'], 5)
            b.dispatch_updates([update])             # third failure: skip
        self.assertEqual(b.state['offset'], 6)
        self.assertEqual(b.process_update.call_count, 3)
        self.assertTrue(any('skipped' in text for text in self.sent))
        self.assertNotIn(5, b.UPDATE_FAILURES)

    def test_transient_update_failure_recovers_without_skipping(self):
        b = self.b
        b.state['offset'] = 5
        update = {'update_id': 5}
        b.process_update = mock.Mock(side_effect=[ValueError('once'), None])
        with mock.patch.object(b.time, 'sleep'):
            b.dispatch_updates([update])
            b.dispatch_updates([update])
        self.assertEqual(b.state['offset'], 6)
        self.assertFalse(any('skipped' in text for text in self.sent))

    def test_question_card_survives_earlier_transcript_error(self):
        b = self.b
        transcript = Path(self.tmp.name) / 'messages.jsonl'
        transcript.write_text('')
        b.session_msg_file = lambda _: str(transcript)
        def run(*args, **kwargs):
            rows = [
                {'role': 'assistant', 'stopReason': 'error',
                 'errorMessage': '429 retried', 'content': []},
                {'role': 'assistant', 'stopReason': 'stop', 'content': [
                    {'type': 'text', 'text': '[[QUESTION]]\n{"questions":[{"header":"Pick","question":"Which?","options":[{"label":"Yes"}]}]}\n[[/QUESTION]]'}]},
            ]
            transcript.write_text(''.join(json.dumps(r) + '\n' for r in rows))
            return 0, '', ''
        b.run_aside = run
        b.handle_message('question')
        self.assertIsNotNone(b.state.get('question'))
        self.assertFalse(any('did not complete' in text for text in self.sent))

    def test_error_still_reported_when_progress_was_streamed(self):
        b = self.b
        transcript = Path(self.tmp.name) / 'messages.jsonl'
        transcript.write_text('')
        b.session_msg_file = lambda _: str(transcript)
        def run(*args, **kwargs):
            rows = [
                {'role': 'assistant', 'stopReason': 'stop', 'content': [
                    {'type': 'text', 'text': 'working on it'}]},
                {'role': 'assistant', 'stopReason': 'error',
                 'errorMessage': 'provider refused', 'content': []},
            ]
            transcript.write_text(''.join(json.dumps(r) + '\n' for r in rows))
            return 0, '', ''
        b.run_aside = run
        b.handle_message('work')
        self.assertTrue(any('provider refused' in text for text in self.sent))

    def test_permanent_400_drops_only_that_chunk(self):
        b = self.b
        b.state['undelivered_texts'] = ['bad', 'good']
        calls = []
        def fake_tg(method, params=None, timeout=65):
            calls.append(params['text'])
            if params['text'] == 'bad':
                raise b.urllib.error.HTTPError('u', 400, 'Bad Request', {}, None)
            return {'ok': True}
        b.tg = fake_tg
        b.flush_outbox()
        self.assertEqual(calls, ['bad', 'good'])
        self.assertEqual(b.state['undelivered_texts'], [])

    def test_transient_errors_keep_chunk_queued(self):
        b = self.b
        b.state['undelivered_texts'] = ['keep', 'later']
        for code in (429, 502, 403):
            b.tg = mock.Mock(side_effect=b.urllib.error.HTTPError(
                'u', code, 'x', {}, None))
            with mock.patch.object(b.time, 'sleep'):
                b.flush_outbox()
            self.assertEqual(b.state['undelivered_texts'], ['keep', 'later'],
                             code)

    def test_new_session_stops_early_when_model_probe_fails(self):
        b = self.b
        b.state['model'] = 'claude-sonnet-5'
        b.create_new_session = mock.Mock()
        with mock.patch.object(b, 'read_connected_providers',
                               return_value=frozenset(['claude-code'])), \
                mock.patch.object(b, 'probe_model',
                                  return_value=(False, 'sign-in expired')):
            b.heavy_new()
        b.create_new_session.assert_not_called()
        self.assertIn('sign-in expired', self.sent[-1])
        self.assertIn('sign in', self.sent[-1])

    def test_new_session_proceeds_when_model_probe_passes(self):
        b = self.b
        b.state['model'] = 'claude-sonnet-5'
        b.create_new_session = mock.Mock(side_effect=RuntimeError('reached'))
        with mock.patch.object(b, 'read_connected_providers',
                               return_value=frozenset(['claude-code'])), \
                mock.patch.object(b, 'probe_model', return_value=(True, 'ok')):
            with self.assertRaises(RuntimeError):
                b.heavy_new()
        b.create_new_session.assert_called_once()

    def test_model_command_accepts_alias_and_qualified_id(self):
        self._model_cmd('sonnet', ['claude-code'])
        self.assertEqual(self.b.state['model'], 'claude-sonnet-5')
        self._model_cmd('openai-codex/gpt-6.1-sol', ['openai-codex'])
        self.assertEqual(self.b.state['model'], 'openai-codex/gpt-6.1-sol')


if __name__ == '__main__':
    unittest.main()
