Two utterances from LibriSpeech dev-clean (speaker 1272), CC-BY-4.0,
https://www.openslr.org/12 (Panayotov et al., 2015).

`repeats-v4.json` (tests/engine/test_tokens.py, test_recognizer_text.py): the tokens and frame
times (seconds) that NowCaption's v4 model gave through sherpa-onnx 1.13.8's greedy search, fed
as the app feeds it (0.96 s of warm-up silence, then 40 ms blocks), for words said several times.
Each word was cut from the AMI Meeting Corpus as distributed in the ESB test sets, CC-BY-4.0,
https://groups.inf.ed.ac.uk/ami/corpus/ (Carletta et al., 2005): one recording of the word
repeated 0-300 ms apart over quiet background noise, or, for "varied recordings", each time one
of four recordings of the word by the same speaker, up to 10% faster or slower and 3 dB louder or
quieter, 30-250 ms apart. "said" is how many times the word was said.
