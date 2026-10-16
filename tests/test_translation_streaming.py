#!/usr/bin/env python3
"""Live translation regressions: progress, stale results, and stream boundaries.

These tests use controlled recognizers and translators, without models or GTK.
"""
import threading
import time
import unittest
from unittest.mock import patch

from nowcaption import engine
from test_reliability import Events, FakeRecognizer, SILENCE, SPEECH
from test_translation import EchoTranslator, FakeModel, FakePieces, Lookahead


def until(condition, timeout=2):
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() >= deadline:
            raise AssertionError('timed out waiting for translation')
        time.sleep(0.002)


class StreamingTests(unittest.TestCase):
    def worker(self, translator):
        store = engine.TranscriptStore()
        captions, errors = [], []
        tw = engine.TranslationWorker(translator, store,
                                      lambda text, gen: captions.append(text), on_error=errors.append)
        tw.start()
        def cleanup():
            tw.stop()
            tw.join(2)
            if not tw.is_alive() and store.file is not None:
                store.file.close()
        self.addCleanup(cleanup)
        return tw, store, captions, errors

    def test_an_automatic_english_guess_does_not_bypass_the_translator(self):
        model = FakeModel()
        translator = engine.Translator(model, FakePieces(), FakePieces(), lambda text: 'en')
        result, language = translator.translate('hola a todo el mundo')
        self.assertEqual(result, 'HOLA A TODO EL MUNDO')
        self.assertEqual(language, 'en')
        self.assertEqual(len(model.calls), 1)

    def test_a_request_can_freeze_its_language_or_explicitly_keep_auto_detection(self):
        model = FakeModel()
        translator = engine.Translator(model, FakePieces(), FakePieces(), lambda text: 'es')
        translator.fixed = 'en'
        self.assertEqual(translator.translate('hola a todo el mundo', fixed='fr')[1], 'fr')
        self.assertEqual(translator.translate('hola a todo el mundo', fixed=None)[1], 'es')
        self.assertEqual(len(model.calls), 2)  # the current English setting cannot bypass old work

    def test_queued_sentences_keep_their_language_when_settings_change(self):
        gate = threading.Event()
        translator = EchoTranslator(gate)
        tw, store, _, errors = self.worker(translator)
        self.addCleanup(gate.set)
        translator.fixed = 'fr'
        tw.line(1, 'bonjour tout le monde.')
        until(lambda: translator.calls)
        translator.fixed = 'ja'
        tw.line(2, 'konnichiwa sekai.')
        translator.fixed = 'de'
        tw.line(3, 'guten tag zusammen.')
        gate.set()
        until(lambda: store.count == 3)
        self.assertIn('[fr: bonjour tout le monde.]', store.lines()[0])
        self.assertIn('[ja: konnichiwa sekai.]', store.lines()[1])
        self.assertIn('[de: guten tag zusammen.]', store.lines()[2])
        self.assertFalse(errors)

    def test_live_english_progresses_after_its_deadline_without_more_source_updates(self):
        translator = EchoTranslator()
        with patch.object(engine.TranslationWorker, 'FORCE_S', 0.04):
            tw, store, captions, errors = self.worker(translator)
            tw.partial_text('una frase sin mas actualizaciones')
            until(lambda: tw.live_done)
            self.assertEqual(tw.passage(), '')
            until(lambda: tw.passage(), timeout=0.4)
            self.assertEqual(tw.passage(), 'UNA FRASE SIN MAS')
            self.assertEqual(translator.calls, ['una frase sin mas actualizaciones'])
            self.assertEqual(store.count, 0)
            self.assertFalse(errors)

    def test_clear_cancels_the_pending_live_display_deadline(self):
        with patch.object(engine.TranslationWorker, 'FORCE_S', 0.04):
            tw, store, captions, _ = self.worker(EchoTranslator())
            tw.partial_text('estas palabras son privadas')
            until(lambda: tw.live_done)
            with tw.lock:
                tw.reset(1)
                store.clear()
                before = list(captions)
            time.sleep(0.08)
            self.assertEqual(captions, before)
            self.assertEqual((tw.passage(), store.lines()), ('', []))

    def test_finishing_a_sentence_cancels_its_deadline_and_empty_inference(self):
        translator = EchoTranslator()
        with patch.object(engine.TranslationWorker, 'FORCE_S', 0.04):
            tw, store, captions, _ = self.worker(translator)
            tw.partial_text('una frase completa')
            until(lambda: tw.live_done)
            tw.line(1, 'una frase completa.')
            tw.partial_text('')
            until(lambda: store.count == 1)
            time.sleep(0.08)
            self.assertEqual(captions[-1], 'UNA FRASE COMPLETA.')
            self.assertEqual(captions.count('UNA FRASE COMPLETA.'), 1)
            self.assertNotIn('', translator.calls)

    def test_identical_speech_on_the_next_sentence_gets_a_new_live_translation(self):
        translator = EchoTranslator()
        with patch.object(engine.TranslationWorker, 'PARTIAL_EVERY_S', 0):
            tw, store, _, errors = self.worker(translator)
            phrase = 'estas palabras se repiten'
            tw.partial_text(phrase)
            until(lambda: tw.live_done == phrase)
            tw.line(1, phrase + '.')
            tw.partial_text('')
            until(lambda: store.count == 1)
            tw.partial_text(phrase)
            until(lambda: translator.calls.count(phrase) == 2)
            self.assertFalse(errors)

    def test_close_drains_the_sentence_without_a_late_live_deadline(self):
        with patch.object(engine.TranslationWorker, 'FORCE_S', 0.04):
            tw, store, captions, _ = self.worker(EchoTranslator())
            tw.partial_text('una frase sin final')
            until(lambda: tw.live_done)
            tw.line(1, 'una frase sin final')
            tw.close()
            before = list(captions)
            tw.join(1)
            self.assertFalse(tw.is_alive())
            time.sleep(0.08)
            self.assertEqual(captions, before)
            self.assertEqual(store.count, 1)

    def test_a_paused_job_is_not_treated_as_final_after_speech_has_resumed(self):
        gate, started = threading.Event(), threading.Event()

        class HeldFirst(EchoTranslator):
            def translate(self, text, previous=None, prefix='', **kwargs):
                if not started.is_set():
                    started.set()
                    gate.wait(2)
                    return 'A guessed ending', 'es'
                return super().translate(text, previous, prefix, **kwargs)

        translator = HeldFirst()
        tw, _, captions, _ = self.worker(translator)
        self.addCleanup(gate.set)
        tw.line(1, 'una frase sin terminar')  # paused when its live translation starts
        self.assertTrue(started.wait(1))
        tw.partial_text('ahora seguimos hablando')  # the pause has ended during inference
        gate.set()
        until(lambda: tw.live_done == 'una frase sin terminar')
        self.assertNotIn('A guessed ending', captions)
        self.assertNotEqual(tw.pinned, 'A guessed ending')

    def recognition(self, recognizer, translator=None):
        events = Events()
        worker = engine.RecognitionWorker(events, load=lambda: (FakeRecognizer(), None),
                                          load_translation=lambda: (recognizer, translator or EchoTranslator()))
        worker.start()
        def cleanup():
            worker.stop()
            worker.join_all(2)
            if worker.captioner and worker.captioner.store.file is not None:
                worker.captioner.store.file.close()
        self.addCleanup(cleanup)
        events.wait('ready')
        worker.set_translation(True)
        events.wait('translation', lambda value: value is True)
        return worker, events

    def test_restart_finishes_the_old_sentence_before_the_new_stream(self):
        worker, events = self.recognition(FakeRecognizer(['primero sin final ', 'segundo sin final. ']))
        worker.feed(0, SPEECH)
        events.snapshot(worker)
        worker.restart()
        events.snapshot(worker)
        worker.feed(0, SPEECH)
        worker.feed(0, SILENCE * 12)
        events.snapshot(worker)
        until(lambda: worker.captioner.store.count >= 2)
        lines = worker.captioner.store.lines()
        self.assertEqual(len(lines), 2)
        self.assertIn('PRIMERO SIN FINAL', lines[0])
        self.assertNotIn('SEGUNDO', lines[0])
        self.assertIn('SEGUNDO SIN FINAL.', lines[1])

    def test_restart_drains_words_buffered_inside_the_old_recognizer(self):
        worker, events = self.recognition(Lookahead(['hola ', 'mundo ']))
        worker.feed(0, SPEECH)
        worker.feed(0, SPEECH)
        events.snapshot(worker)
        worker.restart()
        events.snapshot(worker)
        until(lambda: worker.captioner.store.count == 1)
        self.assertIn('HOLA MUNDO', worker.captioner.store.lines()[0])

    def test_changing_spoken_language_finishes_old_words_with_the_old_setting(self):
        gate, entered = threading.Event(), threading.Event()

        class HeldModel(FakeModel):
            def translate_batch(self, batch, **options):
                entered.set()
                gate.wait(2)
                return super().translate_batch(batch, **options)

        translator = engine.Translator(HeldModel(), FakePieces(), FakePieces(), lambda text: 'fr')
        worker, events = self.recognition(FakeRecognizer(['bonjour tout le monde ']), translator)
        self.addCleanup(gate.set)
        worker.set_spoken('fr')
        events.snapshot(worker)
        worker.feed(0, SPEECH)
        self.assertTrue(entered.wait(1))
        before = len(events.seen)
        worker.set_spoken('ja')
        events.snapshot(worker)
        gate.set()
        until(lambda: worker.captioner.store.count == 1)
        line, = worker.captioner.store.lines()
        self.assertIn('[fr: bonjour tout le monde]', line)
        self.assertEqual(worker.translation[0].language, 'ja')
        self.assertEqual(translator.fixed, 'ja')
        events.snapshot(worker)
        self.assertNotIn(('translation-language', 'fr'), events.seen[before:])

    def test_switching_off_suppresses_old_results_before_the_new_caption_is_published(self):
        gate = threading.Event()
        translator = EchoTranslator(gate)
        worker, events = self.recognition(FakeRecognizer(), translator)
        self.addCleanup(gate.set)
        old = worker.translation_worker
        old.line(1, 'resultado anterior.')
        until(lambda: translator.calls)
        caption = worker._caption
        switched = threading.Event()

        def publish(text):
            caption(text)
            if not isinstance(worker.captioner, engine.TranslatingCaptioner):
                switched.set()
                gate.set()
                until(lambda: old.store.count == 1)
                with old.lock:
                    pass  # any caption from this result has now been delivered
        worker._caption = publish
        before = len(events.seen)
        worker.set_translation(False)
        events.wait('translation', lambda value: value is False)
        self.assertTrue(switched.is_set())
        later = [value[1] for kind, value in events.seen[before:] if kind == 'caption']
        self.assertFalse(any('RESULTADO ANTERIOR' in text for text in later), later)


if __name__ == '__main__':
    unittest.main(verbosity=2)
