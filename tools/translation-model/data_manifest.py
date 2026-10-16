"""The requested language set must survive failed downloads and partial retries."""
import json
from pathlib import Path

LANGS = 'ar bg cs da de el es et fi fr he hi hr hu it ja ko lt lv nl no pl pt ro ru sk sl sv th tr uk vi zh'.split()


def manifest_path(data):
    return Path(data) / 'clean' / 'manifest.json'


def write_manifest(data, languages, complete):
    path = manifest_path(data)
    tmp = path.with_suffix('.json.part')
    tmp.write_text(json.dumps({'languages': sorted(languages), 'complete': complete}, indent=2) + '\n')
    tmp.replace(path)


def expected_languages(data):
    path = manifest_path(data)
    if not path.exists():
        return list(LANGS)  # older work folders were built for all 33 languages
    manifest = json.loads(path.read_text())
    languages = manifest['languages']
    if not languages or not set(languages) <= set(LANGS):
        raise ValueError('Invalid language set in data/clean/manifest.json')
    if not manifest.get('complete'):
        raise ValueError('Training data is incomplete; rerun build_data.py for the failed languages')
    return sorted(set(languages))


def clean_languages(data):
    languages = expected_languages(data)
    missing = [lang for lang in languages if not (Path(data) / 'clean' / f'{lang}.tsv').is_file()
               or (Path(data) / 'clean' / f'{lang}.tsv').stat().st_size == 0]
    extra = {p.stem for p in (Path(data) / 'clean').glob('*.tsv')} - set(languages)
    if missing or extra:
        raise ValueError(f'Clean language set differs from the request: missing/empty={missing}, unexpected={sorted(extra)}')
    return languages


def validate_encoded(data):
    languages = expected_languages(data)
    meta = json.loads((Path(data) / 'bin' / 'meta.json').read_text())
    actual = {lang for lang in meta['langs'] if not lang.startswith('kd-')}
    counts = {row[0]: row[1] for row in meta['stats']}
    missing = set(languages) - actual
    empty = [lang for lang in languages if counts.get(lang, 0) <= 0]
    if missing or empty or actual - set(languages):
        raise ValueError(f'Encoded language set differs from the request: missing={sorted(missing)}, empty={empty}; rerun prepare.py encode')
