#!/usr/bin/env python3
"""English caption boundaries, generation-tagged recovery, ordered saves and matching exports.

Runs unchanged application methods and the real recognition worker with controlled
models, events and asynchronous files. No GTK, audio server or downloaded models.
"""
import threading
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

from nowcaption import engine
from test_reliability import Events, FakePunct, FakeRecognizer, FakeStream, SPEECH, SILENCE
from test_regressions import APP, app_with_fakes
import test_fixes_104 as previous


def transcript_words(text):
    """The exported passage without its per-line timestamps."""
    return ' '.join(line.split('] ', 1)[1] for line in text.splitlines())


class StrippedRecognizer(FakeRecognizer):
    def get_result(self, stream):
        return super().get_result(stream).strip()  # as load_recognizer() returns native results


class WordBoundaryTests(unittest.TestCase):
    def make(self, prefix):
        self.rec = StrippedRecognizer([prefix])
        self.updates = []
        return engine.Captioner(self.rec, FakePunct(), on_update=self.updates.append)

    def wait_without_new_tokens(self, cap):
        for _ in range(10):
            cap.feed(SPEECH)  # sound continues; only the text is unchanged

    def test_a_pause_over_background_sound_finishes_the_whole_line(self):
        # A microphone or a video with music is never digitally silent: holding the last
        # word until digital silence moved every line's last word onto the next line.
        cap = self.make('we understand')
        cap.feed(SPEECH)
        self.wait_without_new_tokens(cap)
        self.assertEqual(cap.count, 1)
        self.assertFalse(cap.partial.strip())
        self.rec.script.append(' today')
        cap.feed(SPEECH)
        cap.flush()
        lines = [line.split('] ', 1)[1] for line in cap.transcript().splitlines()]
        self.assertEqual(lines, ['We understand', 'today.'])  # not ['We', 'understand today.']

    def test_real_digital_silence_can_finish_a_single_word(self):
        cap = self.make('hello')
        cap.feed(SPEECH)
        for _ in range(9):
            cap.feed(SILENCE)
        self.assertEqual(cap.count, 1)
        self.assertFalse(cap.partial.strip())
        self.assertEqual(transcript_words(cap.transcript()), 'Hello.')

    def test_length_limit_does_not_insert_spaces_inside_a_long_word(self):
        word = 'x' * (engine.MAX_LINE + 20)
        cap = self.make(word)
        cap.feed(SPEECH)
        self.wait_without_new_tokens(cap)
        self.assertEqual(cap.count, 0)
        self.rec.script.append(' end')
        cap.feed(SPEECH)
        cap.flush()
        self.assertEqual(transcript_words(cap.transcript()).lower(), word + ' end.')


class TranscriptFormattingTests(unittest.TestCase):
    def make(self, script, punctuation=None):
        self.rec, self.updates = FakeRecognizer(script), []
        return engine.Captioner(self.rec, punctuation or FakePunct(), on_update=self.updates.append)

    def test_two_lines_export_the_same_sentence_as_the_caption_passage(self):
        cap = self.make(['hello ', 'world '])
        cap.feed(SPEECH)
        cap.feed(SILENCE * 12)
        cap.feed(SPEECH)
        cap.flush()
        self.assertEqual(self.updates[-1], 'Hello world.')
        self.assertEqual(transcript_words(cap.transcript()), self.updates[-1])

    def test_copying_does_not_freeze_punctuation_before_the_next_words_arrive(self):
        cap = self.make(['hello ', 'world '])
        cap.feed(SPEECH)
        cap.feed(SILENCE * 12)
        before = cap.transcript()
        cap.feed(SPEECH)
        after = cap.transcript()
        self.assertEqual(transcript_words(before), 'Hello.')
        self.assertEqual(transcript_words(after), 'Hello world.')
        self.assertEqual(before.split(']')[0], after.split(']')[0])

    def test_punctuation_whitespace_changes_do_not_drop_exported_words(self):
        class ExpandedPunctuation(FakePunct):
            def add_punctuation_with_case(self, text):
                return super().add_punctuation_with_case(text.replace('cannot', 'can not'))

        cap = self.make(['cannot ', 'go '], ExpandedPunctuation())
        cap.feed(SPEECH)
        cap.feed(SILENCE * 12)
        cap.feed(SPEECH)
        cap.flush()
        self.assertEqual(self.updates[-1], 'Can not go.')
        self.assertEqual(transcript_words(cap.transcript()), self.updates[-1])

    def test_unchanged_exports_reuse_the_visible_punctuation(self):
        class ChangingPunctuation:
            calls = 0

            def add_punctuation_with_case(self, text):
                self.calls += 1
                return text.capitalize() + ('.' if self.calls % 2 else '?')

        punct = ChangingPunctuation()
        cap = self.make(['hello '], punct)
        cap.feed(SPEECH)
        calls = punct.calls
        for _ in range(3):
            self.assertEqual(transcript_words(cap.transcript()), self.updates[-1])
        self.assertEqual(punct.calls, calls)

    def test_settled_punctuation_survives_scrolling_and_every_word_is_exported_once(self):
        cap = self.make([f'line{i} word ' for i in range(200)])
        with patch.object(engine.Captioner, 'DISPLAY_LINES', 3):
            for _ in range(200):
                cap.feed(SPEECH)
                cap.feed(SILENCE * 12)
            lines = cap.transcript().splitlines()
            self.assertEqual(len(lines), 200)
            self.assertEqual(transcript_words('\n'.join(lines[-3:])), self.updates[-1])
            for i in range(200):
                self.assertEqual(transcript_words(lines[i]).lower().split()[0], f'line{i}')
            self.assertLessEqual(len(cap.recent), cap.LIVE_LINES + 1)
            self.assertEqual(cap.store.count, cap.settled)

    def test_recovery_keeps_pending_lines_and_the_same_exported_punctuation(self):
        events = Events()
        rec = FakeRecognizer(['hello ', 'world ', 'again '])
        worker = engine.RecognitionWorker(events, load=lambda: (rec, FakePunct()))
        worker.start()
        self.addCleanup(lambda: (worker.stop(), worker.join(3)))
        events.wait('ready')
        worker.feed(0, SPEECH)
        worker.feed(0, SILENCE * 12)
        worker.feed(0, SPEECH)
        before = events.snapshot(worker)
        rec.fail = True
        worker.feed(0, SPEECH)
        events.wait('error')
        rec.fail = False
        worker.restart()
        self.assertEqual(events.snapshot(worker), before)
        worker.feed(0, SPEECH)
        after = events.snapshot(worker)
        self.assertEqual(transcript_words(after), 'Hello world again.')
        worker.clear(1)
        self.assertEqual(events.snapshot(worker), '')


class RecoveryGenerationTests(unittest.TestCase):
    def setUp(self):
        self.timers = previous.Timers()
        patcher = patch.dict(APP, GLib=self.timers)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.app = app_with_fakes()
        self.app.ready = True

    def start_worker(self, rec):
        events = Events()
        worker = engine.RecognitionWorker(events, load=lambda: (rec, None))
        self.app.worker = worker
        worker.start()
        self.addCleanup(lambda: (worker.stop(), worker.join(3)))
        events.wait('ready')
        return worker, events

    def test_old_source_error_is_ignored_even_when_the_new_source_also_failed(self):
        rec = FakeRecognizer()
        rec.fail = True
        worker, events = self.start_worker(rec)
        worker.feed(0, SPEECH)
        old = events.wait('error')
        self.app.on_source(NS(set_state=lambda value: None), NS(get_string=lambda: 'mic'))
        worker.feed(self.app.source_gen, SPEECH)
        current = events.wait('error')
        self.app.on_worker('error', old)
        self.assertEqual(self.app.recognition_failures, 0)
        self.app.on_worker('error', current)
        self.assertEqual(self.app.recognition_failures, 1)
        self.assertEqual(self.timers.delays, [2])

    def test_switching_sources_during_a_decode_does_not_relabel_its_error(self):
        entered, release = threading.Event(), threading.Event()

        class BlockingStream(FakeStream):
            def accept_waveform(self, rate, samples):
                if not entered.is_set():
                    entered.set()
                    if not release.wait(3):
                        raise RuntimeError('test did not release the old decode')
                return super().accept_waveform(rate, samples)

        class Rec(FakeRecognizer):
            def create_stream(self):
                return BlockingStream(self)

        rec = Rec()
        rec.fail = True
        worker, events = self.start_worker(rec)
        self.addCleanup(release.set)
        worker.feed(0, SPEECH)
        self.assertTrue(entered.wait(2))
        self.app.on_source(NS(set_state=lambda value: None), NS(get_string=lambda: 'mic'))
        worker.feed(self.app.source_gen, SPEECH)
        release.set()
        old, current = events.wait('error'), events.wait('error')
        self.assertEqual(old[0], 0)
        self.assertEqual(current[0], self.app.source_gen)
        self.app.on_worker('error', old)
        self.app.on_worker('error', current)
        self.assertEqual(self.app.recognition_failures, 1)

    def test_old_stream_error_is_ignored_after_a_retry_on_the_same_source(self):
        rec = FakeRecognizer()
        rec.fail = True
        worker, events = self.start_worker(rec)
        worker.feed(0, SPEECH)
        old = events.wait('error')
        worker.restart()
        worker.feed(0, SPEECH)
        current = events.wait('error')
        self.assertNotEqual(old[1], current[1])
        self.app.on_worker('error', old)
        self.app.on_worker('error', current)
        self.assertEqual(self.timers.delays, [2])

    def test_obsolete_error_cannot_cancel_a_current_retry_timer(self):
        self.app.source_gen = 2
        self.app.worker = NS(broken=True, stream_gen=4)
        self.app.on_worker('error', (2, 4, 'current failure'))
        timer = self.app.recognition_retry_id
        self.app.on_worker('error', (1, 3, 'old failure'))
        self.assertEqual(self.app.recognition_retry_id, timer)
        self.assertIn(timer, self.timers.pending)
        self.assertEqual(self.app.recognition_failures, 1)


class OrderedSaveTests(unittest.TestCase):
    def setUp(self):
        self.holder = previous.SaveTests()
        self.holder.setUp()
        self.addCleanup(self.holder.tearDown)
        self.app = self.holder.app
        self.operations, self.disk = [], {}

    def file(self, uri, immediate_error=False):
        file = NS(get_uri=lambda: uri, get_basename=lambda: 'captions.txt')

        def write(contents, etag, backup, flags, cancellable, callback):
            if immediate_error:
                raise OSError('destination unavailable')
            self.operations.append((file, contents, callback))

        def finish(result):
            if result:
                raise self.holder.glib.Error('disk full')

        file.replace_contents_bytes_async, file.replace_contents_finish = write, finish
        return file

    def complete(self, index, fail=False):
        file, contents, callback = self.operations[index]
        if not fail:
            self.disk[file.get_uri()] = contents
        callback(file, fail)

    def test_same_destination_is_serialized_even_for_distinct_file_objects(self):
        uri = 'file:///captions.txt'
        self.app.write_transcript(self.file(uri), 'earlier')
        self.app.write_transcript(self.file(uri), 'earlier plus newer words')
        self.app.request_quit()
        self.assertEqual(len(self.operations), 1)
        self.assertEqual(self.app.pending_saves, 2)
        self.complete(0)
        self.assertEqual(len(self.operations), 2)
        self.assertEqual(self.app.quits, 0)
        self.complete(1)
        self.assertEqual(self.disk[uri], b'earlier plus newer words\n')
        self.assertEqual(self.app.quits, 1)
        self.assertEqual(self.app.save_queues, {})

    def test_different_destinations_can_finish_in_either_order(self):
        first, second = 'file:///first.txt', 'file:///second.txt'
        self.app.write_transcript(self.file(first), 'first')
        self.app.write_transcript(self.file(second), 'second')
        self.assertEqual(len(self.operations), 2)
        self.complete(1)
        self.complete(0)
        self.assertEqual(self.disk, {first: b'first\n', second: b'second\n'})
        self.assertEqual(self.app.pending_saves, 0)

    def test_a_failed_write_releases_its_hold_and_the_next_snapshot_can_still_save(self):
        uri = 'file:///captions.txt'
        self.app.write_transcript(self.file(uri), 'earlier')
        self.app.write_transcript(self.file(uri), 'newer')
        self.app.request_quit()
        self.complete(0, fail=True)
        self.assertFalse(self.app.quit_requested)
        self.assertEqual(len(self.operations), 2)
        self.complete(1)
        self.assertEqual(self.disk[uri], b'newer\n')
        self.assertEqual(self.app.quits, 0)
        self.assertEqual(self.app.save_queues, {})

    def test_failure_to_start_a_queued_write_does_not_leave_a_pending_save(self):
        uri = 'file:///captions.txt'
        self.app.write_transcript(self.file(uri), 'earlier')
        self.app.write_transcript(self.file(uri, immediate_error=True), 'newer')
        self.complete(0)
        self.assertEqual(self.app.pending_saves, 0)
        self.assertEqual(self.app.save_queues, {})
        self.assertIn('destination unavailable', self.app.window.statuses[-1])

    def test_accepted_snapshot_requests_keep_their_order_and_holds(self):
        callbacks = []
        self.app.worker = NS(snapshot=lambda callback, on_error=None: callbacks.append(callback))
        uri = 'file:///captions.txt'
        self.holder.accept_destination(self.file(uri))
        self.holder.accept_destination(self.file(uri))
        self.assertEqual(self.app.pending_saves, 2)
        for callback, text in zip(callbacks, ('earlier', 'newer')):
            self.app.on_worker('snapshot', (callback, text))
        self.assertEqual(len(self.operations), 1)
        self.complete(0)
        self.complete(1)
        self.assertEqual(self.disk[uri], b'Thursday 23 October 2026, 14:05\nnewer\n')  # with its heading since 1.1.9
        self.assertEqual(self.app.pending_saves, 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
