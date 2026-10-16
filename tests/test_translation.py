#!/usr/bin/env python3
"""Translation to English, without models or GTK: the Translator, the
TranslationWorker thread, the TranslatingCaptioner and switching it on and off.
tests/test_translation_live.py checks the real models.
"""
import threading
import time
import unittest

import numpy as np
from types import SimpleNamespace as NS
from unittest.mock import patch

from nowcaption import engine
import test_reliability as fixtures
from test_reliability import Events, FakeRecognizer, FakeStream, SILENCE, SPEECH


class Lookahead(FakeRecognizer):
    """Like a real streaming model, a word comes out only once audio after it arrives."""

    def create_stream(self):
        stream, pending = FakeStream(self), []

        def accept_waveform(rate, samples):
            if pending:
                stream.text += pending.pop()
            if np.any(samples) and self.script:
                pending.append(self.script.pop(0))
            stream.ready = True
        stream.accept_waveform = accept_waveform
        return stream


class Unstartable(FakeRecognizer):
    def create_stream(self):
        raise RuntimeError('injected stream failure')


class FakeModel:
    """CTranslate2's translate_batch: 'translates' by upper-casing the pieces."""

    def __init__(self):
        self.calls = []

    def translate_batch(self, batch, **options):
        """Like CTranslate2, a target prefix is kept as given and the rest follows it."""
        self.calls.append((batch, options))
        prefixes = options.get('target_prefix') or [None] * len(batch)
        return [NS(hypotheses=[(prefix or []) + [t.upper() for t in tokens][len(prefix or []):]])
                for tokens, prefix in zip(batch, prefixes)]


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
        self.calls, self.gate, self.fail, self.prefixes, self.batches = [], gate, fail, [], []

    def translate(self, text, previous=None, prefix='', **kwargs):
        return self.translate_many([(text, prefix)], previous, **kwargs)[0]

    CUT_WORDS = 40  # stands in for MT_SECTION_PIECES: a word is a piece here

    def cut(self, text):
        words = text.split()
        return 0 if len(words) <= self.CUT_WORDS else len(' '.join(words[:self.CUT_WORDS])) + 1

    def translate_many(self, items, previous=None, *, fixed=None):
        """Like the real model, the English starts with the prefix it is given, whatever the
        text: a prefix from another sentence shows up in the English."""
        self.batches.append(len(items))
        for text, prefix in items:
            self.calls.append(text)
            self.prefixes.append(prefix)
        if self.gate is not None:
            self.gate.wait(5)
        if self.fail:
            raise RuntimeError('translation model failed')
        out = []
        for text, prefix in items:
            english = text.upper()
            if prefix and not english.startswith(prefix):
                english = prefix + ' ' + english
            out.append((english, fixed or 'es'))
        return out


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
        self.assertNotIn('no_repeat_ngram_size', options)  # it would also ban repetition people say

    def test_long_runs_from_recognition_errors_are_cut_but_real_repetition_is_kept(self):
        translator = self.translator('zh')
        translator.translate('海地ออออออออออ T T T T T T 研究所 no no no jajaja 100000')
        (batch, _), = self.model.calls
        self.assertEqual(batch, [['海地อออ', 'T', 'T', 'T', '研究所', 'no', 'no', 'no', 'jajaja', '100000']])

    def test_a_runaway_loop_in_the_english_is_trimmed(self):
        self.assertEqual(engine.trim_loops('The storm storm storm storm storm storm hit.'), 'The storm storm storm hit.')
        self.assertEqual(engine.trim_loops("I don't know, I don't know."), "I don't know, I don't know.")
        self.assertEqual(engine.trim_loops('Sorry, sorry, sorry, sorry, sorry.'), 'Sorry, sorry, sorry.')

    def test_text_too_long_for_the_model_is_translated_in_sections(self):
        translator = self.translator('ja')
        words = [f'w{i}' for i in range(150)]
        words[50] += '、'  # a clause end near where the first section would end
        english, _ = translator.translate(' '.join(words))
        (batch, _), = self.model.calls
        self.assertEqual([len(b) for b in batch], [51, 50, 49])  # about equal, cut at the clause end
        self.assertEqual(english.split(), [w.upper() for w in words])  # nothing is lost

    def test_where_a_sentence_too_long_to_translate_well_is_cut(self):
        translator = self.translator('es')
        self.assertEqual(translator.cut('uno dos tres'), 0)
        words = [f'w{i}' for i in range(100)]
        self.assertEqual(translator.cut(' '.join(words)), len(' '.join(words[:64])) + 1)  # at a space
        words[55] += ','
        self.assertEqual(translator.cut(' '.join(words)), len(' '.join(words[:56])))  # at the comma

    def test_a_short_phrase_after_english_is_still_translated(self):
        translator = self.translator('en')
        self.assertEqual(translator.translate('sí', previous='en'), ('SÍ', None))
        self.assertEqual(self.model.calls[0][0], [['sí']])

    def test_a_wrong_language_guess_does_not_change_the_english(self):
        self.assertEqual(self.translator('pt').translate('hola a todo el mundo')[0], 'HOLA A TODO EL MUNDO')

    def test_very_long_text_is_translated_whole_in_sections(self):
        english, _ = self.translator('es').translate(' '.join(f'palabra{i}' for i in range(1000)))
        (batch, _), = self.model.calls
        self.assertTrue(all(len(b) <= engine.MT_SECTION_PIECES for b in batch))
        self.assertEqual(len(english.split()), 1000)  # nothing is dropped

    def test_explicitly_fixed_english_passes_through_untouched(self):
        translator = self.translator('en')
        translator.fixed = 'en'
        self.assertEqual(translator.translate('Hello there, everyone.'), ('Hello there, everyone.', 'en'))
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
        tw.partial_text('Hola mundo. Adiós')
        self.until(lambda: self.store.count == 1)  # no pause yet: the full stop was enough
        self.assertIn('HOLA MUNDO.', self.store.lines()[0])
        tw.partial_text('Hola mundo. Adiós amigo')
        tw.line(101.0, 'Hola mundo. Adiós amigo.')  # the pause commits the whole line
        self.until(lambda: self.store.count == 2)
        self.assertIn('ADIÓS AMIGO.', self.store.lines()[1])
        self.assertNotIn('HOLA', self.store.lines()[1])  # translated once, not twice

    def test_a_long_line_split_inside_finished_sentences_is_not_repeated(self):
        tw = self.start()
        tw.partial_text('Uno dos. Tres cuatro. Cinco')
        self.until(lambda: self.store.count == 1)
        tw.line(100.0, 'Uno dos.')  # MAX_LINE split inside what was already finished
        tw.partial_text('Tres cuatro. Cinco seis')
        tw.line(101.0, 'Tres cuatro. Cinco seis.')
        self.until(lambda: self.store.count == 2)
        english = [line.split('] ', 1)[1].split(' [')[0] for line in self.store.lines()]
        self.assertEqual(english, ['UNO DOS. TRES CUATRO.', 'CINCO SEIS.'])

    def test_sentence_breaks_ignore_numbers_and_wait_for_the_next_word(self):
        self.assertEqual(engine.complete_sentences('cuesta 3.5 euros'), 0)
        self.assertEqual(engine.complete_sentences('fin.'), 0)  # nothing follows it yet
        self.assertEqual(engine.complete_sentences('Hola. Adiós'), len('Hola. '))
        self.assertEqual(engine.complete_sentences('No. Me quedo'), len('No. '))  # a short reply ends
        self.assertEqual(engine.complete_sentences('¿Vienes? Sí. Vale'), len('¿Vienes? Sí. '))
        self.assertTrue(engine.ends_sentence('Oui.'))
        # an initial or abbreviation, or a word in lower case after the full stop: no end
        self.assertEqual(engine.complete_sentences('El Sr. García llegó. Y luego'), len('El Sr. García llegó. '))
        self.assertEqual(engine.complete_sentences('Vive en EE. UU. desde 2010'), 0)
        self.assertEqual(engine.complete_sentences('J. K. Rowling'), 0)
        self.assertEqual(engine.complete_sentences('libros, etc. y más'), 0)
        self.assertFalse(engine.ends_sentence('Llegó el Sr.'))
        self.assertEqual(engine.complete_sentences('今日は。明日'), len('今日は。'))

    def test_live_english_is_continued_from_what_was_shown(self):
        translator = EchoTranslator()
        tw = self.start(translator)
        for text in ('uno dos tres cuatro', 'uno dos tres cuatro cinco', 'uno dos tres cuatro cinco seis'):
            tw.partial_text(text)
            self.until(lambda: tw.live_done == text)
        # words two translations in a row agree on are shown, and the next one continues from them
        self.assertEqual(translator.prefixes[:3], ['', '', 'UNO DOS TRES CUATRO'])
        self.assertEqual(self.captions[-1][1], 'UNO DOS TRES CUATRO CINCO')

    def test_a_pause_shows_the_whole_english_of_an_unfinished_sentence(self):
        tw = self.start()
        tw.partial_text('la gente buena se fue')
        self.until(lambda: tw.live_done)
        self.assertEqual(tw.passage(), '')  # one translation alone is not shown: nothing confirms it
        tw.line(100.0, 'la gente buena se fue')  # a pause, no full stop: the sentence stays open
        self.until(lambda: self.captions[-1][1] == 'LA GENTE BUENA SE FUE')
        self.assertEqual(self.store.count, 0)
        tw.partial_text('y')  # the sentence goes on after the pause, from the English shown
        tw.line(101.0, 'y volvió.')
        self.until(lambda: self.store.count == 1)
        self.assertIn('LA GENTE BUENA SE FUE Y VOLVIÓ.', self.store.lines()[0])

    def test_no_english_of_one_sentence_carries_into_the_next(self):
        translator = EchoTranslator()
        tw = self.start(translator)
        for text in ('primera frase larga', 'primera frase larga aquí'):
            tw.partial_text(text)
            self.until(lambda: tw.live_done == text)
        self.assertTrue(tw.pinned)
        tw.partial_text('primera frase larga aquí. Segunda')  # the full stop finishes the first
        self.until(lambda: self.store.count == 1)
        for text in ('Segunda frase', 'Segunda frase nueva'):
            tw.partial_text('primera frase larga aquí. ' + text)
            self.until(lambda: tw.live_done == text)
        for text, prefix in zip(translator.calls, translator.prefixes):
            if text.startswith('Segunda'):
                self.assertTrue(not prefix or prefix.startswith('SEGUNDA'), prefix)
        self.assertEqual(tw.passage(), 'PRIMERA FRASE LARGA AQUÍ. SEGUNDA FRASE')

    def test_a_long_stretch_without_punctuation_is_finished_in_parts(self):
        tw = self.start()
        said = [f'p{i}' for i in range(100)]
        for n in range(10, 101, 10):  # the words keep coming, with no full stop or pause
            tw.partial_text(' '.join(said[:n]))
        self.until(lambda: self.store.count == 2)
        english = [l.split('] ', 1)[1].split(' [')[0] for l in self.store.lines()]
        self.assertEqual(english, [' '.join(said[:40]).upper(), ' '.join(said[40:80]).upper()])
        self.assertEqual(tw.words, ' '.join(said[80:]))  # still being spoken


        gate = threading.Event()
        translator = EchoTranslator(gate)
        tw = self.start(translator)
        tw.line(0.0, 'cero.')
        self.until(lambda: translator.calls)  # the translator is busy with the first
        for i in range(1, 6):
            tw.line(float(i), f'frase {i}.')
        gate.set()
        self.until(lambda: self.store.count == 6)
        self.assertEqual(translator.batches[:2], [1, 5])

    def test_sentences_are_translated_in_order_and_never_dropped(self):
        tw = self.start()
        for i in range(30):
            tw.line(float(i), f'frase número {i}.')
        self.until(lambda: self.store.count == 30)
        self.assertEqual([l.split('] ', 1)[1].split(' [')[0] for l in self.store.lines()],
                         [f'FRASE NÚMERO {i}.' for i in range(30)])

    def test_the_words_still_being_spoken_are_shown_live_then_replaced(self):
        tw = self.start()
        for text in ('estamos hablando ahora', 'estamos hablando ahora mismo'):
            tw.partial_text(text)
            self.until(lambda: tw.live_done == text)
        self.assertEqual(self.captions[-1][1], 'ESTAMOS HABLANDO AHORA')
        self.assertEqual(self.store.count, 0)
        tw.line(5.0, 'estamos hablando ahora mismo.')
        tw.partial_text('')
        self.until(lambda: self.store.count == 1 and self.captions[-1][1] == 'ESTAMOS HABLANDO AHORA MISMO.')

    def test_unsure_words_are_shown_when_the_speaker_pauses(self):
        translator = EchoTranslator()
        tw = self.start(translator)
        tw.partial_text('sí')  # too short to tell the language: the model is not told one anyway
        self.until(lambda: 'sí' in translator.calls)
        self.assertEqual(tw.passage(), '')  # the last two words may still change
        tw.line(1.0, 'sí')
        self.until(lambda: tw.passage() == 'SÍ')

    def test_a_finished_sentence_shows_its_whole_translation(self):
        class Guessing(EchoTranslator):
            def translate(self, text, previous=None, prefix='', **kwargs):  # a live line, translated as it grows
                english, lang = super().translate(text, previous, prefix, **kwargs)
                return english.replace('TRES', 'THREE?'), lang  # guessed before the rest was said
        tw = self.start(Guessing())
        for text in ('uno dos tres cuatro', 'uno dos tres cuatro cinco'):
            tw.partial_text(text)
            self.until(lambda: tw.live_done == text)
        self.assertEqual(tw.passage(), 'UNO DOS THREE? CUATRO')
        tw.line(1.0, 'uno dos tres cuatro cinco.')
        self.until(lambda: self.store.count == 1)
        self.until(lambda: self.captions[-1][1] == 'UNO DOS TRES CUATRO CINCO.')  # corrected once, at its end
        self.assertIn('UNO DOS TRES CUATRO CINCO.', self.store.lines()[0])  # the screen matches the transcript

    def test_english_on_screen_only_ever_grows(self):
        class Wobbly(EchoTranslator):
            def translate(self, text, previous=None, prefix='', **kwargs):
                english, lang = super().translate(text, previous, prefix, **kwargs)
                words = english.split()
                return ' '.join(words[:-1] + [f'GUESS{len(text)}']), lang  # the last word wobbles
        tw = self.start(Wobbly())
        said = 'uno dos tres cuatro cinco seis siete ocho'.split()
        for n in range(1, len(said) + 1):
            tw.partial_text(' '.join(said[:n]))
            self.until(lambda: tw.live_done == tw.live_text)
        shown = [text for _, text in self.captions]
        for before, after in zip(shown, shown[1:]):
            self.assertTrue(after.startswith(before), (before, after))
        self.assertNotIn('GUESS', ' '.join(shown))

    def test_unfinished_live_english_has_no_full_stop(self):
        class Stopping(EchoTranslator):
            def translate(self, text, previous=None, prefix='', **kwargs):
                english, lang = super().translate(text, previous, prefix, **kwargs)
                return english + '.', lang  # as the model does with every fragment
        tw = self.start(Stopping())
        tw.partial_text('esto parece')
        tw.line(1.0, 'esto parece')  # a pause shows all of it, without the model's full stop
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


class Slow(EchoTranslator):
    """A translator that takes `seconds` per call; with disagree, each live translation words
    the sentence's end differently, as a real model does while the sentence grows."""
    def __init__(self, seconds, disagree=False, **kw):
        super().__init__(**kw)
        self.seconds, self.disagree, self.starts, self.n = seconds, disagree, [], 0

    def translate_many(self, items, previous=None, **kwargs):
        self.starts.append(time.monotonic())
        time.sleep(self.seconds)
        out = super().translate_many(items, previous, **kwargs)
        if self.disagree and len(items) == 1 and items[0][1] == '':
            self.n += 1
            out = [(f'GUESS{self.n} ' + english, lang) for english, lang in out]
        return out


class TimingTests(WorkerFixture):
    """With the real waiting between live translations, which the other tests switch off."""

    def test_english_shows_within_a_bounded_time_even_when_translations_disagree(self):
        tw = self.start(Slow(0.1, disagree=True))
        started = time.monotonic()
        words = []
        while time.monotonic() - started < 3:
            words.append(f'w{len(words)}')
            tw.partial_text(' '.join(words))
            time.sleep(0.1)
            if tw.passage():
                break
        self.assertTrue(tw.passage(), 'nothing shown after 3 s')
        self.assertLess(time.monotonic() - started, engine.TranslationWorker.FORCE_S + 0.8)

    def test_live_translations_are_spaced_from_their_start(self):
        translator = Slow(0.2)
        tw = self.start(translator)
        for i in range(30):
            tw.partial_text(' '.join(f'w{j}' for j in range(i + 1)))
            time.sleep(0.05)
        gaps = [b - a for a, b in zip(translator.starts, translator.starts[1:])]
        self.assertTrue(gaps)
        self.assertLess(max(gaps), 0.2 * 4 + 0.15)  # 4x the time it takes, from its start

    def test_a_pause_during_a_translation_still_shows_the_whole_english(self):
        gate = threading.Event()
        translator = EchoTranslator(gate)
        tw = self.start(translator)
        tw.partial_text('la gente buena se fue')
        self.until(lambda: translator.calls)  # being translated...
        tw.line(1.0, 'la gente buena se fue')  # ...when the speaker pauses
        gate.set()
        self.until(lambda: tw.passage() == 'LA GENTE BUENA SE FUE')

    def test_current_speech_gets_a_turn_while_old_sentences_are_caught_up(self):
        gate = threading.Event()
        translator = EchoTranslator(gate)
        tw = self.start(translator)
        tw.line(0.0, 'cero.')
        self.until(lambda: translator.calls)
        for i in range(1, 40):
            tw.line(float(i), f'frase {i}.')
        tw.partial_text('ahora mismo hablando')
        gate.set()
        self.until(lambda: self.store.count == 40)
        live = translator.calls.index('ahora mismo hablando')
        self.assertLess(live, translator.calls.index('frase 39.'))  # not after all of them
        self.assertEqual([l.split('] ', 1)[1].split(' [')[0] for l in self.store.lines()][-1], 'FRASE 39.')


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

    def test_a_named_language_keeps_its_stream_through_digital_silence(self):
        cap = self.captioner(['hola. '])
        self.rec.language = 'es'
        cap.feed(SPEECH)
        for _ in range(12):  # past SILENCE_FLUSH_S of digital silence
            cap.feed(SILENCE)
        self.assertEqual(len(self.streams), 1)
        self.until(lambda: self.store.count == 1)  # the sentence still ends there

    def test_copy_has_each_sentence_once_when_one_ended_at_a_full_stop(self):
        gate = threading.Event()
        tw = self.start(EchoTranslator(gate))
        cap = engine.TranslatingCaptioner(FakeRecognizer([]), tw, gen=lambda: 0, store=self.store)
        tw.partial_text('Uno dos. Tres')
        self.assertEqual([l.split('] ', 1)[1] for l in cap.transcript().splitlines()], ['Uno dos.', 'Tres'])
        gate.set()
        self.until(lambda: self.store.count == 1)
        self.assertEqual([l.split('] ', 1)[1].split(' [')[0] for l in cap.transcript().splitlines()], ['UNO DOS.', 'Tres'])

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

    def test_after_translation_is_off_old_sentences_never_reach_the_screen_or_a_cleared_transcript(self):
        gate = threading.Event()

        class SentencesWait(EchoTranslator):
            def translate(self, text, previous=None, prefix='', **kwargs):  # a live line is not held up
                held, self.gate = self.gate, None
                try:
                    return super().translate(text, previous, prefix, **kwargs)
                finally:
                    self.gate = held
        translator = SentencesWait(gate)
        worker, events = self.make(lambda: (FakeRecognizer(['secreto ', 'privado. ']), translator))
        worker.set_translation(True)
        events.wait('translation', lambda v: v is True)
        worker.feed(0, SPEECH)
        worker.feed(0, SPEECH)
        worker.feed(0, SILENCE * 12)
        end = time.monotonic() + 3
        while 'secreto privado.' not in translator.calls and time.monotonic() < end:
            time.sleep(0.01)
        self.assertIn('secreto privado.', translator.calls)  # still being translated...
        worker.set_translation(False)  # ...when translation is turned off
        events.wait('translation', lambda v: v is False)
        after = len(events.seen)
        worker.clear(7)
        gate.set()
        time.sleep(0.3)
        text = events.snapshot(worker)
        self.assertNotIn('secreto', text.lower())
        self.assertNotIn('SECRETO', ' '.join(str(v) for k, v in events.seen[after:] if k == 'caption'))

    def test_switching_off_keeps_pending_source_then_late_english_in_speech_order(self):
        gate, entered = threading.Event(), threading.Event()

        class HeldTranslator(EchoTranslator):
            def translate_many(self, items, previous=None, **kwargs):
                entered.set()
                return super().translate_many(items, previous, **kwargs)
        worker, events = self.make(self.translation(translator=HeldTranslator(gate)))
        self.addCleanup(gate.set)
        worker.set_translation(True)
        events.wait('translation', lambda v: v is True)
        old = worker.translation_worker
        old.line(time.time() - 10, 'palabras anteriores.')
        self.assertTrue(entered.wait(2))
        worker.set_translation(False)
        events.wait('translation', lambda v: v is False)
        worker.feed(0, SPEECH)
        worker.feed(0, SPEECH)
        worker.feed(0, SILENCE * 12)
        before = events.snapshot(worker).splitlines()
        self.assertEqual(len(before), 2, before)
        self.assertTrue(before[0].endswith('palabras anteriores.'), before)
        self.assertIn('English words.', before[1])
        gate.set()
        old.join(2)
        self.assertFalse(old.is_alive())
        after = events.snapshot(worker).splitlines()
        self.assertEqual(len(after), 2, after)
        self.assertIn('PALABRAS ANTERIORES.', after[0])
        self.assertIn('English words.', after[1])

    def test_stop_prevents_a_command_in_flight_from_starting_a_loader(self):
        entered, resume, loaded = threading.Event(), threading.Event(), threading.Event()

        def load():
            loaded.set()
            return FakeRecognizer(), EchoTranslator()
        worker, events = self.make(load)
        original = worker._load_translation

        def held_command():
            entered.set()
            resume.wait(2)
            original()
        worker._load_translation = held_command
        self.addCleanup(resume.set)
        worker.set_translation(True)
        self.assertTrue(entered.wait(2))
        worker.stop()
        resume.set()
        worker.join_all(2)
        self.assertFalse(worker.is_alive())
        self.assertFalse(loaded.is_set())
        self.assertIsNone(worker.translation_loader)

    def test_join_all_collects_children_after_joining_the_owner(self):
        create, entered, finish = threading.Event(), threading.Event(), threading.Event()
        joining, joined = threading.Event(), threading.Event()
        worker = engine.RecognitionWorker(lambda *args: None)

        def child():
            entered.set()
            finish.wait(3)

        def owner():
            create.wait(3)
            worker.translation_loader = threading.Thread(target=child, daemon=True)
            worker.translation_loader.start()
        worker.run = owner
        original_join = worker.join

        def owner_join(timeout=None):
            joining.set()
            original_join(timeout)
        worker.join = owner_join
        worker.start()
        worker.stop()

        def join_all():
            worker.join_all(3)
            joined.set()
        waiter = threading.Thread(target=join_all, daemon=True)
        waiter.start()
        try:
            self.assertTrue(joining.wait(1))
            create.set()
            self.assertTrue(entered.wait(1))
            self.assertFalse(joined.wait(0.1), 'join_all returned while the late loader was alive')
        finally:
            create.set()
            finish.set()
            waiter.join(3)
            worker.join_all(3)
        self.assertTrue(joined.is_set())
        self.assertFalse(worker.translation_loader.is_alive())

    def test_translation_that_cannot_start_leaves_english_captions_working(self):
        worker, events = self.make(lambda: (Unstartable(), EchoTranslator()))
        worker.set_translation(True)
        self.assertIn('could not start', events.wait('translation-error'))
        self.assertIs(events.wait('translation', lambda v: v != 'loading'), False)
        self.assertFalse(worker.broken)
        self.assertIs(type(worker.captioner), engine.Captioner)
        worker.feed(0, SPEECH)
        events.wait('caption', lambda v: 'english' in v[1].lower())  # captions carry on

    def test_a_replaced_translator_failing_does_not_turn_off_its_replacement(self):
        gate = threading.Event()
        old = EchoTranslator(gate, fail=True)  # fails, but only once released
        translators = [old, EchoTranslator()]
        worker, events = self.make(lambda: (FakeRecognizer(['hola ', 'mundo. ']), translators.pop(0)))
        worker.set_translation(True)
        events.wait('translation', lambda v: v is True)
        worker.feed(0, SPEECH)
        worker.feed(0, SPEECH)
        worker.feed(0, SILENCE * 12)
        end = time.monotonic() + 3
        while not old.calls and time.monotonic() < end:
            time.sleep(0.01)
        worker.set_translation(False)  # the old thread is still busy with that sentence
        events.wait('translation', lambda v: v is False)
        worker.set_translation(True)
        events.wait('translation', lambda v: v is True)
        gate.set()  # now the old thread fails
        time.sleep(0.3)
        worker.snapshot(None)
        events.wait('snapshot')
        self.assertTrue(worker.translate_on)
        self.assertIsInstance(worker.captioner, engine.TranslatingCaptioner)
        self.assertTrue(worker.translation_worker.is_alive())

    def test_switching_mode_keeps_the_words_still_inside_the_recognizer(self):
        events = Events()
        worker = engine.RecognitionWorker(events, load=lambda: (Lookahead(['hello ', 'world ']), None),
                                          load_translation=self.translation())
        worker.start()
        self.addCleanup(lambda: (worker.stop(), worker.join(3)))
        events.wait('ready')
        worker.feed(0, SPEECH)
        worker.feed(0, SPEECH)  # 'world' is still inside the recognizer, waiting for more audio
        worker.set_translation(True)
        events.wait('translation', lambda v: v is True)
        self.assertIn('world', events.snapshot(worker).lower())

    def test_quitting_stops_every_translation_thread_and_the_model_loading(self):
        gate = threading.Event()
        translator = EchoTranslator(gate)
        worker, events = self.make(lambda: (FakeRecognizer(['uno. ', 'dos. ', 'tres. ']), translator))
        worker.set_translation(True)
        events.wait('translation', lambda v: v is True)
        for _ in range(3):
            worker.feed(0, SPEECH)
        worker.feed(0, SILENCE * 12)
        end = time.monotonic() + 3
        while not translator.calls and time.monotonic() < end:
            time.sleep(0.01)
        retired = worker.translation_worker
        worker.set_translation(False)  # it is finishing its sentences, blocked on the first
        events.wait('translation', lambda v: v is False)

        def slow_load():
            time.sleep(0.5)
            return FakeRecognizer(), EchoTranslator()
        worker.load_translation = slow_load
        worker.set_translation(True)
        events.wait('translation', lambda v: v == 'loading')
        worker.stop()
        gate.set()
        worker.join_all(3)
        self.assertFalse(retired.is_alive())
        self.assertFalse(worker.translation_loader.is_alive())

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

    def test_live_inference_failure_keeps_unfinished_source_and_english_captions_working(self):
        worker, events = self.make(self.translation(
            words=['palabras sin terminar '], translator=EchoTranslator(fail=True)))
        worker.set_translation(True)
        events.wait('translation', lambda v: v is True)
        worker.feed(0, SPEECH)
        events.wait('translation-error')
        events.wait('translation', lambda v: v is False)
        self.assertFalse(worker.broken)
        self.assertIn('palabras sin terminar', events.snapshot(worker))
        worker.feed(0, SPEECH)
        events.wait('caption', lambda v: 'english' in v[1].lower())

    def test_translation_missing_says_what_to_install(self):
        with patch.object(engine, 'models_dir', side_effect=FileNotFoundError('Speech model not found')):
            self.assertIn('not found', engine.translation_missing())


class AppTests(unittest.TestCase):
    """The menu switch and the app's handling of translation events."""

    def setUp(self):
        from test_regressions import app_with_fakes
        self.app = app_with_fakes()
        self.app.settings.set('translate', 0)
        self.app.settings.set('spoken', 'auto')
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

    def test_a_queued_language_event_cannot_replace_the_users_new_selection(self):
        self.app.settings.set('spoken', 'ja')
        before = list(self.app.window.statuses)
        self.app.on_worker('translation-language', 'fr')
        self.assertEqual(self.app.window.statuses, before)
        self.app.on_worker('translation-language', 'ja')
        self.assertEqual(self.app.window.statuses[-1], 'Translating from Japanese.')
        self.app.settings.set('spoken', 'auto')
        self.app.on_worker('translation-language', 'fr')
        self.assertEqual(self.app.window.statuses[-1], 'Translating from French.')


if __name__ == '__main__':
    unittest.main(verbosity=2)
