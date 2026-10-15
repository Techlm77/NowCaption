#!/usr/bin/env python3
"""Translation to English, without models or GTK: the Translator, the
TranslationWorker thread, the TranslatingCaptioner and switching it on and off.
tests/test_translation_live.py checks the real models.
"""
import threading
import time
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

from nowcaption import engine
import test_reliability as fixtures
from test_reliability import Events, FakeRecognizer, SILENCE, SPEECH


class FakeModel:
    """CTranslate2's translate_batch: 'translates' by upper-casing the pieces."""

    def __init__(self):
        self.calls = []

    def translate_batch(self, batch, **options):
        self.calls.append((batch, options))
        return [NS(hypotheses=[[t.upper() for t in tokens]]) for tokens in batch]


class FakePieces:
    VOCAB = ['<unk>', '<s>', '\u2581the', 'é', '<0x41>', '<0xD8>', '\u2581\u0627\u0644', 'ο', '\u2581♪']

    def encode(self, text, out_type=str):
        return text.split()

    def get_piece_size(self):
        return len(self.VOCAB)

    def id_to_piece(self, i):
        return self.VOCAB[i]

    def decode_pieces(self, pieces):
        return ' '.join(pieces)


class EchoTranslator:
    """Stands in for Translator in thread tests: SPANISH WORDS -> upper case."""
    SURE_CHARS = engine.Translator.SURE_CHARS
    fixed = None

    def __init__(self, gate=None, fail=False):
        self.calls, self.gate, self.fail, self.prefixes = [], gate, fail, []

    def translate(self, text, previous=None, prefix=''):
        self.calls.append(text)
        self.prefixes.append(prefix)
        if self.gate is not None:
            self.gate.wait(5)
        if self.fail:
            raise RuntimeError('translation model failed')
        return text.upper(), 'es'


class TranslatorTests(unittest.TestCase):
    def translator(self, language):
        self.identified = []

        def identify(text):
            self.identified.append(text)
            return language
        self.model = FakeModel()
        return engine.Translator(self.model, FakePieces(), FakePieces(), identify)

    def test_translates_into_english_and_names_the_language(self):
        english, lang = self.translator('es').translate('hola a todo el mundo')
        self.assertEqual((english, lang), ('HOLA A TODO EL MUNDO', 'es'))
        (batch, options), = self.model.calls
        self.assertEqual(batch, [['hola', 'a', 'todo', 'el', 'mundo']])  # no language tag
        self.assertNotIn('target_prefix', options)

    def test_too_little_text_is_not_guessed_at(self):
        translator = self.translator('pt')
        self.assertEqual(translator.translate('sí'), ('SÍ', None))  # translated, language unknown
        self.assertEqual(translator.translate('sí', 'es'), ('SÍ', 'es'))  # the last one is kept
        self.assertEqual(self.identified, [])

    def test_a_named_language_is_used_without_guessing(self):
        translator = self.translator('pt')
        translator.fixed = 'es'
        self.assertEqual(translator.translate('hola'), ('HOLA', 'es'))
        self.assertEqual(self.identified, [])

    def test_english_cannot_be_written_in_another_script_or_run_away(self):
        translator = self.translator('ar')
        # Arabic and Greek pieces and the bytes of non-ASCII characters are never written
        self.assertEqual(translator.blocked, [['<0xD8>'], ['\u2581\u0627\u0644'], ['ο']])
        translator.translate('مرحبا بكم')
        (batch, options), = self.model.calls
        self.assertEqual(options['suppress_sequences'], translator.blocked)
        self.assertEqual(options['max_decoding_length'], 2 * 2 + 16)
        self.assertEqual(options['no_repeat_ngram_size'], 4)

    def test_repeated_letters_and_words_from_recognition_errors_become_one(self):
        translator = self.translator('zh')
        translator.translate('海地ออออออออออ T T T T T 研究所 cuesta 10000')
        (batch, _), = self.model.calls
        self.assertEqual(batch, [['海地อ', 'T', '研究所', 'cuesta', '10000']])

    def test_a_wrong_language_guess_does_not_change_the_english(self):
        self.assertEqual(self.translator('pt').translate('hola a todo el mundo')[0], 'HOLA A TODO EL MUNDO')

    def test_very_long_text_is_cut_to_what_the_model_can_take(self):
        self.translator('es').translate(' '.join(f'palabra{i}' for i in range(1000)))
        (batch, _), = self.model.calls
        self.assertEqual(len(batch[0]), engine.MT_MAX_PIECES)

    def test_english_passes_through_untouched(self):
        self.assertEqual(self.translator('en').translate('Hello there, everyone.'), ('Hello there, everyone.', 'en'))
        self.assertEqual(self.model.calls, [])

    def test_short_text_keeps_the_language_of_the_sentence_before(self):
        t = self.translator('de')
        self.assertEqual(t.translate('sí', previous='es'), ('SÍ', 'es'))
        self.assertEqual(self.identified, [])
        self.assertEqual(t.translate('a longer sentence', previous='es')[1], 'de')

    def test_every_language_maps_to_a_translation_code(self):
        self.assertEqual(engine.LANGID_CODES['nb'], 'no')
        self.assertTrue(set(engine.LANGID_CODES.values()) <= set(engine.LANGUAGES))
        self.assertEqual(len(engine.LANGUAGES), 33 + 1)  # with English


class SentenceTests(unittest.TestCase):
    def test_sentences_end_at_final_punctuation_in_many_scripts(self):
        for text in ('Hola.', '¿Qué?', 'Fin!»', 'これは日本語です。', 'यह है।', 'هل هذا؟', '"Done."'):
            self.assertTrue(engine.ends_sentence(text), text)
        for text in ('Hola,', 'y entonces', '', '日本語'):
            self.assertFalse(engine.ends_sentence(text), text)

    def test_chinese_and_japanese_pieces_join_without_a_space(self):
        self.assertEqual(engine.join_text('这是', '一个'), '这是一个')
        self.assertEqual(engine.join_text('hola', 'mundo'), 'hola mundo')
        self.assertEqual(engine.join_text('', 'mundo'), 'mundo')
        self.assertEqual(engine.join_text('안녕', '하세요'), '안녕 하세요')  # Korean uses spaces


class WorkerFixture(unittest.TestCase):
    def start(self, translator=None, gen=0):
        self.store = engine.TranscriptStore()
        self.captions, self.languages, self.errors = [], [], []
        self.tw = engine.TranslationWorker(translator or EchoTranslator(), self.store,
                                           on_caption=lambda text, gen: self.captions.append((gen, text)),
                                           on_language=self.languages.append, on_error=self.errors.append, gen=gen)
        self.tw.start()
        self.addCleanup(lambda: (self.tw.stop(), self.tw.join(3)))
        return self.tw

    def until(self, condition, timeout=3):
        end = time.monotonic() + timeout
        while not condition():
            if time.monotonic() > end:
                self.fail('timed out')
            time.sleep(0.005)


@patch.object(engine.TranslationWorker, 'PARTIAL_EVERY_S', 0)
class TranslationWorkerTests(WorkerFixture):
    def test_pieces_become_one_sentence_translated_with_its_original(self):
        tw = self.start()
        tw.line(100.0, 'la gente')
        tw.line(101.0, 'se fue a casa.')
        self.until(lambda: self.store.count == 1)
        self.assertEqual(self.store.lines(), [engine.stamp(100.0) + 'LA GENTE SE FUE A CASA. [es: la gente se fue a casa.]'])
        self.until(lambda: self.captions and self.captions[-1][1] == 'LA GENTE SE FUE A CASA.')
        self.assertEqual(self.languages, ['es'])

    def test_a_full_stop_finishes_a_sentence_before_the_speaker_pauses(self):
        tw = self.start()
        tw.partial_text('hola mundo. adiós')
        self.until(lambda: self.store.count == 1)  # no pause yet: the full stop was enough
        self.assertIn('HOLA MUNDO.', self.store.lines()[0])
        tw.partial_text('hola mundo. adiós amigo')
        tw.line(101.0, 'hola mundo. adiós amigo.')  # the pause commits the whole line
        self.until(lambda: self.store.count == 2)
        self.assertIn('ADIÓS AMIGO.', self.store.lines()[1])
        self.assertNotIn('HOLA', self.store.lines()[1])  # translated once, not twice

    def test_a_long_line_split_inside_finished_sentences_is_not_repeated(self):
        tw = self.start()
        tw.partial_text('uno dos. tres cuatro. cinco')
        self.until(lambda: self.store.count == 1)
        tw.line(100.0, 'uno dos.')  # MAX_LINE split inside what was already finished
        tw.partial_text('tres cuatro. cinco seis')
        tw.line(101.0, 'tres cuatro. cinco seis.')
        self.until(lambda: self.store.count == 2)
        english = [line.split('] ', 1)[1].split(' [')[0] for line in self.store.lines()]
        self.assertEqual(english, ['UNO DOS. TRES CUATRO.', 'CINCO SEIS.'])

    def test_sentence_breaks_ignore_numbers_and_wait_for_the_next_word(self):
        self.assertEqual(engine.complete_sentences('cuesta 3.5 euros'), 0)
        self.assertEqual(engine.complete_sentences('fin.'), 0)  # nothing follows it yet
        self.assertEqual(engine.complete_sentences('hola. adiós'), len('hola. '))
        self.assertEqual(engine.complete_sentences('今日は。明日'), len('今日は。'))

    def test_live_english_is_continued_from_what_was_shown(self):
        translator = EchoTranslator()
        tw = self.start(translator)
        tw.partial_text('uno dos tres cuatro')
        self.until(lambda: self.captions and self.captions[-1][1] == 'UNO DOS TRES CUATRO')
        tw.partial_text('uno dos tres cuatro cinco')
        self.until(lambda: self.captions[-1][1] == 'UNO DOS TRES CUATRO CINCO')
        # all but the last two words of the shown English are kept for the next translation
        self.assertEqual(translator.prefixes[:2], ['', 'UNO DOS'])

    def test_a_pause_shows_the_whole_english_of_an_unfinished_sentence(self):
        tw = self.start()
        tw.partial_text('la gente buena')
        self.until(lambda: self.captions and self.captions[-1][1] == 'LA GENTE BUENA')
        tw.partial_text('la gente buena se fue')  # the next translation only adds agreed words
        tw.line(100.0, 'la gente buena se fue')  # a pause, no full stop: the sentence stays open
        self.until(lambda: self.captions[-1][1] == 'LA GENTE BUENA SE FUE')
        self.assertEqual(self.store.count, 0)

    def test_sentences_are_translated_in_order_and_never_dropped(self):
        tw = self.start()
        for i in range(30):
            tw.line(float(i), f'frase número {i}.')
        self.until(lambda: self.store.count == 30)
        self.assertEqual([l.split('] ', 1)[1].split(' [')[0] for l in self.store.lines()],
                         [f'FRASE NÚMERO {i}.' for i in range(30)])

    def test_the_words_still_being_spoken_are_shown_live_then_replaced(self):
        tw = self.start()
        tw.partial_text('estamos hablando ahora mismo')
        self.until(lambda: self.captions and self.captions[-1][1] == 'ESTAMOS HABLANDO AHORA MISMO')
        self.assertEqual(self.store.count, 0)
        tw.line(5.0, 'estamos hablando ahora mismo.')
        tw.partial_text('')
        self.until(lambda: self.store.count == 1 and self.captions[-1][1] == 'ESTAMOS HABLANDO AHORA MISMO.')

    def test_the_first_word_is_translated_at_once(self):
        tw = self.start()
        tw.partial_text('sí')  # too short to tell the language: the model is not told one anyway
        self.until(lambda: tw.passage() == 'SÍ')

    def test_unfinished_live_english_has_no_full_stop(self):
        class Stopping(EchoTranslator):
            def translate(self, text, previous=None, prefix=''):
                english, lang = super().translate(text, previous, prefix)
                return english + '.', lang  # as the model does with every fragment
        tw = self.start(Stopping())
        tw.partial_text('esto parece')
        self.until(lambda: tw.passage() == 'ESTO PARECE')
        self.assertEqual(engine.UNFINISHED_STOP.sub('', 'It seems...'), 'It seems...')  # not an ellipsis

    def test_a_pause_ends_a_sentence_without_punctuation(self):
        tw = self.start()
        tw.line(1.0, 'y entonces nos fuimos')
        time.sleep(0.05)
        self.assertEqual(self.store.count, 0)
        tw.end_sentence()
        self.until(lambda: self.store.count == 1)

    def test_a_long_sentence_is_translated_before_it_ends(self):
        tw = self.start()
        tw.line(1.0, 'palabra ' * 40)
        self.until(lambda: self.store.count == 1)

    def test_nothing_from_before_a_clear_is_shown_or_kept(self):
        gate = threading.Event()
        translator = EchoTranslator(gate)
        tw = self.start(translator, gen=0)
        tw.line(1.0, 'algo privado antes de borrar.')
        self.until(lambda: translator.calls)  # being translated now
        with tw.lock:
            tw.reset(1)
            self.store.clear()
        gate.set()
        tw.line(2.0, 'después.')
        self.until(lambda: self.store.count == 1)
        self.assertNotIn('PRIVADO', ' '.join(self.store.lines()))
        self.assertTrue(all(gen == 1 for gen, text in self.captions))
        self.assertEqual(tw.passage(), 'DESPUÉS.')

    def test_a_translation_error_is_reported_and_the_thread_ends(self):
        tw = self.start(EchoTranslator(fail=True))
        tw.line(1.0, 'esto falla.')
        self.until(lambda: self.errors)
        tw.join(3)
        self.assertFalse(tw.is_alive())
        self.assertIn('failed', str(self.errors[0]))

    def test_closing_translates_what_was_said_then_stops(self):
        tw = self.start()
        tw.line(1.0, 'una frase sin terminar')
        tw.close()
        tw.join(3)
        self.assertFalse(tw.is_alive())
        self.assertEqual(self.store.count, 1)

    def test_pending_text_is_reported_until_it_is_translated(self):
        gate = threading.Event()
        tw = self.start(EchoTranslator(gate))
        tw.line(1.0, 'primera frase.')
        tw.line(2.0, 'y la segunda')
        self.until(lambda: len(tw.pending()) == 2)
        self.assertEqual([text for ts, text in tw.pending()], ['primera frase.', 'y la segunda'])
        gate.set()


class TranslatingCaptionerTests(WorkerFixture):
    def captioner(self, script):
        tw = self.start()
        self.rec = FakeRecognizer(script)
        streams = []
        create = self.rec.create_stream
        self.rec.create_stream = lambda: streams.append(create()) or streams[-1]
        self.streams = streams
        return engine.TranslatingCaptioner(self.rec, tw, gen=lambda: 0, store=self.store)

    def test_finished_lines_go_to_translation_not_to_the_screen(self):
        cap = self.captioner(['hola ', 'mundo. '])
        for chunk in (SPEECH, SPEECH, SILENCE * 9):
            cap.feed(chunk)
        self.until(lambda: self.store.count == 1)
        self.assertEqual(cap.caption_text(), '')
        self.assertIn('HOLA MUNDO.', self.store.lines()[0])

    def test_silence_starts_a_fresh_stream_for_the_next_language(self):
        cap = self.captioner(['hola. ', 'konnichiwa. '])
        cap.feed(SPEECH)
        for _ in range(12):  # past SILENCE_FLUSH_S of digital silence
            cap.feed(SILENCE)
        self.assertEqual(len(self.streams), 2)
        cap.feed(SPEECH)
        cap.flush()
        self.until(lambda: self.store.count == 2)
        self.assertIn('KONNICHIWA.', self.store.lines()[1])

    def test_a_long_pause_without_words_also_starts_a_fresh_stream(self):
        cap = self.captioner(['hola. '])
        cap.feed(SPEECH)
        quiet = (SPEECH[:2] + b'\x01' * (len(SPEECH) - 2))  # not digital silence: background noise
        self.rec.script.clear()
        for _ in range(int(cap.NEW_STREAM_QUIET_S / 0.1) + 12):
            cap.feed(quiet)
        self.assertGreaterEqual(len(self.streams), 2)

    def test_a_named_language_keeps_one_stream_through_pauses(self):
        cap = self.captioner(['hola. '])
        self.rec.language = 'es'  # nothing to detect, so a fresh stream would only lose words
        cap.feed(SPEECH)
        quiet = (SPEECH[:2] + b'\x01' * (len(SPEECH) - 2))
        self.rec.script.clear()
        for _ in range(int(cap.NEW_STREAM_QUIET_S / 0.1) + 12):
            cap.feed(quiet)
        self.assertEqual(len(self.streams), 1)

    def test_clear_forgets_what_was_waiting_for_translation(self):
        cap = self.captioner(['secreto ', 'privado. '])
        cap.feed(SPEECH)
        cap.clear()
        cap.feed(SPEECH)
        cap.flush()
        self.until(lambda: self.store.count == 1)
        self.assertNotIn('SECRETO', ' '.join(self.store.lines()))

    def test_transcript_includes_what_is_still_being_translated_and_spoken(self):
        gate = threading.Event()
        tw = self.start(EchoTranslator(gate))
        cap = engine.TranslatingCaptioner(FakeRecognizer(['uno. ', 'dos ']), tw, gen=lambda: 0, store=self.store)
        cap.feed(SPEECH)
        cap.feed(SILENCE * 9)  # 'uno.' finished: waiting for its translation
        cap.feed(SPEECH)       # 'dos' still being spoken
        lines = cap.transcript().splitlines()
        self.assertTrue(lines[0].endswith('uno.'), lines)
        self.assertTrue(lines[-1].endswith('dos'), lines)
        gate.set()


class WorkerModeTests(unittest.TestCase):
    def make(self, load_translation):
        events = Events()
        worker = engine.RecognitionWorker(events, load=lambda: (FakeRecognizer(['english ', 'words. ']), None),
                                          load_translation=load_translation)
        worker.start()
        self.addCleanup(lambda: (worker.stop(), worker.join(3)))
        events.wait('ready')
        return worker, events

    def translation(self, words=('hola ', 'mundo. '), translator=None):
        return lambda: (FakeRecognizer(list(words)), translator or EchoTranslator())

    def test_naming_the_spoken_language(self):
        translator = EchoTranslator()
        worker, events = self.make(lambda: (FakeRecognizer(['hola ', 'mundo. ']), translator))
        worker.set_spoken('es')
        worker.set_translation(True)
        self.assertIs(events.wait('translation', lambda v: v != 'loading'), True)
        self.assertEqual((worker.translation[0].language, translator.fixed), ('es', 'es'))
        stream = worker.captioner.stream
        worker.set_spoken('auto')  # a change takes a new stream in the new language
        worker.snapshot(None)  # answered after the change is handled
        events.wait('snapshot')
        self.assertEqual((worker.translation[0].language, translator.fixed), ('auto', None))
        self.assertIsNot(worker.captioner.stream, stream)

    def test_turning_translation_on_and_off(self):
        worker, events = self.make(self.translation())
        worker.feed(0, SPEECH)
        worker.set_translation(True)
        self.assertEqual(events.wait('translation'), 'loading')
        self.assertIs(events.wait('translation', lambda v: v != 'loading'), True)
        self.assertIsInstance(worker.captioner, engine.TranslatingCaptioner)
        worker.feed(0, SPEECH)
        worker.feed(0, SPEECH)
        worker.feed(0, SILENCE * 12)
        events.wait('caption', lambda v: 'HOLA MUNDO.' in v[1])
        worker.set_translation(False)
        self.assertIs(events.wait('translation', lambda v: v is False), False)
        self.assertIs(type(worker.captioner), engine.Captioner)
        self.assertIsNone(worker.translation)  # the models are released
        text = events.snapshot(worker)
        self.assertIn('English', text)      # said before translation was turned on
        self.assertIn('HOLA MUNDO.', text)  # translated while it was on

    def test_turning_translation_off_frees_its_models(self):
        import gc, weakref
        holder = {}

        def load():
            holder['models'] = (FakeRecognizer(['hola. ']), EchoTranslator())
            refs = tuple(weakref.ref(m) for m in holder['models'])
            holder['refs'] = refs
            return holder.pop('models')
        worker, events = self.make(load)
        worker.set_translation(True)
        events.wait('translation', lambda v: v is True)
        worker.set_translation(False)
        events.wait('translation', lambda v: v is False)
        for _ in range(100):
            if all(ref() is None for ref in holder['refs']):
                break
            time.sleep(0.02)
        self.assertEqual([ref() for ref in holder['refs']], [None, None])

    def test_shutdown_waits_for_translation_threads_to_let_go_of_their_models(self):
        worker, events = self.make(self.translation())
        worker.set_translation(True)
        events.wait('translation', lambda v: v is True)
        first = worker.translation_worker
        worker.set_translation(False)
        events.wait('translation', lambda v: v is False)
        self.assertIn(first, worker.retired)  # closed, maybe still freeing its model
        worker.stop()
        worker.join_all(3)
        self.assertFalse(worker.is_alive())
        self.assertFalse(first.is_alive())

    def test_translation_that_cannot_load_stays_off_and_says_why(self):
        def fail():
            raise OSError('no space for the models')
        worker, events = self.make(fail)
        worker.set_translation(True)
        self.assertIn('no space for the models', events.wait('translation-error'))
        self.assertIs(events.wait('translation', lambda v: v is False), False)
        self.assertIs(type(worker.captioner), engine.Captioner)
        worker.feed(0, SPEECH)
        self.assertIn('English', events.snapshot(worker))

    def test_recovery_keeps_translating_into_the_same_transcript(self):
        worker, events = self.make(self.translation(('uno. ', 'dos. ')))
        worker.set_translation(True)
        events.wait('translation', lambda v: v is True)
        tw, store = worker.translation_worker, worker.captioner.store
        worker.restart()
        events.snapshot(worker)
        self.assertIsInstance(worker.captioner, engine.TranslatingCaptioner)
        self.assertIs(worker.translation_worker, tw)
        self.assertIs(worker.captioner.store, store)

    def test_a_failing_translation_model_turns_translation_off(self):
        worker, events = self.make(self.translation(translator=EchoTranslator(fail=True)))
        worker.set_translation(True)
        events.wait('translation', lambda v: v is True)
        worker.feed(0, SPEECH)
        worker.feed(0, SPEECH)
        worker.feed(0, SILENCE * 12)
        self.assertIn('Translation stopped after an error', events.wait('translation-error'))
        self.assertIs(events.wait('translation', lambda v: v is False), False)
        self.assertIs(type(worker.captioner), engine.Captioner)

    def test_translation_missing_says_what_to_install(self):
        with patch.object(engine, 'models_dir', side_effect=FileNotFoundError('Speech model not found')):
            self.assertIn('not found', engine.translation_missing())


class AppTests(unittest.TestCase):
    """The menu switch and the app's handling of translation events."""

    def setUp(self):
        from test_regressions import app_with_fakes
        self.app = app_with_fakes()
        self.app.settings.set('translate', 0)
        self.state = False
        self.action = NS(get_state=lambda: NS(get_boolean=lambda: self.state),
                         set_state=lambda v: setattr(self, 'state', v.unpack()))
        self.app.lookup_action = lambda name: self.action
        self.requests = []
        self.app.worker = NS(set_translation=self.requests.append)

    def test_switching_on_without_the_models_says_how_to_get_them(self):
        with patch.object(engine, 'translation_missing', return_value='The translation models are not installed.'):
            self.app.on_translate(self.action, None)
        self.assertFalse(self.state)
        self.assertEqual(self.requests, [])
        self.assertIn('not installed', self.app.window.statuses[-1])

    def test_switching_on_and_off_asks_the_worker_and_is_remembered(self):
        with patch.object(engine, 'translation_missing', return_value=None):
            self.app.on_translate(self.action, None)
        self.assertTrue(self.state)
        self.assertEqual((self.requests, self.app.settings.get('translate')), ([True], 1))
        self.app.on_translate(self.action, None)
        self.assertFalse(self.state)
        self.assertEqual((self.requests, self.app.settings.get('translate')), ([True, False], 0))
        self.assertEqual(self.app.window.statuses[-1], 'Translation off.')

    def test_worker_events_update_the_switch_and_the_status_row(self):
        self.app.on_worker('translation', 'loading')
        self.assertEqual(self.app.window.statuses[-1], 'Loading translation…')
        self.app.on_worker('translation', True)
        self.assertTrue(self.state)
        self.assertEqual(self.app.settings.get('translate'), 1)
        self.app.on_worker('translation-language', 'ja')
        self.assertEqual(self.app.window.statuses[-1], 'Translating from Japanese.')
        self.app.on_worker('translation-error', 'Translation stopped after an error: boom')
        self.app.on_worker('translation', False)
        self.assertFalse(self.state)
        self.assertEqual(self.app.settings.get('translate'), 0)
        self.assertIn('boom', self.app.window.statuses[-1])


if __name__ == '__main__':
    unittest.main(verbosity=2)
