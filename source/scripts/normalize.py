"""Lossless-source, conservative IPA extraction. Run with Python 3.10+.

All source records remain in raw archives. The IPA export is deliberately stricter
than Wiktionary's IPA field: extended conventions are quarantined, not guessed.
"""
import argparse
import collections
import gzip
import hashlib
import json
import re
import unicodedata as ud
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/normalized'
REPORT = ROOT / 'data/reports'
# IPA chart segmental symbols, suprasegmentals and chart diacritics.
# Orthographic g is normalized to IPA script g. Sinological apical-vowel letters,
# superscript beta/reduced vowels, EnPR, ARPABET and ASCII tone digits are excluded.
BASE = set('abcdefghijklmnopqrstuvwxyz' +
    'æçðøŋœɐɑɒɓɔɕɗɖɘəɚɛɜɝɞɟɠɡɢɣɤɥɦɧɨɪɫɬɭɮɯɰɱɲɳɴɵɸɹɺɻɽɾʀʁʂʃʄʈʉʊʋʌʍʎʏʐʑʒʔʕʘʙʛʜʝʟʡʢθβχǀǁǂǃ')
MARKS = set('ˈˌːˑ˞˥˦˧˨˩ꜛꜜʰʱʲʷˠˤⁿˡ' +
    '\u0303\u0306\u0308\u030a\u030b\u030c\u030f\u0311\u0318\u0319\u031a\u031c\u031d\u031e\u031f\u0320\u0324\u0325\u0329\u032a\u032c\u032f\u0330\u0334\u0339\u033a\u033b\u033c\u033d\u0361\u035c')
TONE = set('˥˦˧˨˩ꜛꜜ\u0300\u0301\u0302\u030b\u030c\u030f\u0311')
TONE |= set('\u0304')
MARKS |= TONE | set('\u0327\u0304')  # decomposed ç and level tone macron
TONE_NUMBERS = str.maketrans('¹²³⁴⁵', '˩˨˧˦˥')
HISTORIC = {'obsolete','archaic','historical','Early','Middle','Old','dialectal','nonstandard','reconstructed'}
EN_US = {'US','General-American'}
EN_UK = {'UK','Received-Pronunciation','British'}
EN_DIALECT = {'Australia','New-Zealand','Canada','Ireland','Scotland','Wales','South-Asia','India',
    'South-Africa','Singapore','Philippines','Jamaica','Inland-Northern-American','Northern-England',
    'Northumbria','Cockney','Southern-US','New-York-City','Scouse','Yorkshire','Northern-Ireland'}
ZH_DIALECT = {'Cantonese','Wu','Min-Nan','Min-Dong','Min-Bei','Hakka','Xiang','Gan','Jin',
    'Chengdu',"Xi'an",'Nanjing','Dungan','Beijing','Northeastern-Mandarin'}
JA_DIALECT = {'Kagoshima','Kansai','Kyoto','Osaka','Tohoku','Hiroshima','dialectal','historical'}

def compact(x):
    return json.dumps(x, ensure_ascii=False, separators=(',', ':'))

def strict_ipa(raw):
    """Return normalized IPA and the exact reason if unsafe; never infer a system."""
    if not isinstance(raw, str) or not raw.strip():
        return None, 'empty_ipa'
    text = ud.normalize('NFD', raw.strip())
    if text.startswith('/') and text.endswith('/'):
        text = text[1:-1]
    elif text.startswith('[') and text.endswith(']'):
        text = text[1:-1]
    text = text.replace('g','ɡ')
    invalid = sorted(set(text) - BASE - MARKS - set(' .'))
    if invalid:
        return None, 'non_chart_or_annotation:' + ''.join(invalid)
    if not any(c in BASE for c in text):
        return None, 'no_segment'
    return ud.normalize('NFC', text), None

def language_ipa_reason(ipa, lang):
    # English dictionary macrons can consist solely of IPA-compatible Unicode,
    # but are not accepted as lexical IPA without phonetic evidence of tone.
    if lang=='en' and '\u0304' in ud.normalize('NFD',ipa):
        return 'english_dictionary_macron_or_nonlexical_tone_requires_review'
    return None

def numeric_tone_variants(raw):
    """Only convert 1–5 Chao level sequences. Keep citation/sandhi as separate views.

    Superscript minus is a sandhi annotation, not IPA. Parenthesized tone digits
    and zero/neutral-tone conventions are left for review rather than invented.
    """
    if not re.search('[¹²³⁴⁵]', raw):
        return []
    out = []
    # Optional final rise (e.g. ²¹⁽⁴⁾) is an annotation: expand both variants.
    for optional in ('present','absent'):
        expanded = re.sub(r'⁽([¹²³⁴⁵]+)⁾', lambda m:m.group(1) if optional=='present' else '', raw)
        for side in ('citation','surface'):
            value = re.sub(r'([¹²³⁴⁵]+)⁻([¹²³⁴⁵]+)',
                           lambda m: m.group(1 if side=='citation' else 2), expanded)
            value = value.translate(TONE_NUMBERS)
            ipa, error = strict_ipa(value)
            if not error and ipa not in [r[0] for r in out]:
                out.append((ipa, 'chao_digits_to_ipa_tone_letters:'+side+':optional_'+optional))
    return out

def optional_segment_variants(raw):
    if '(' not in raw:return []
    parts=[raw]
    for _ in range(6):
        expanded=[];changed=False
        for part in parts:
            match=re.search(r'\(([^()]*)\)',part)
            if not match:expanded.append(part);continue
            changed=True
            for replacement in [match.group(1),'']:
                expanded.append(part[:match.start()]+replacement+part[match.end():])
        parts=expanded
        if not changed:break
    result=[]
    for part in dict.fromkeys(parts):
        ipa,error=strict_ipa(part)
        if not error:result.append((ipa,'source_optional_segment_expansion'))
    return result

def region_for(lang, sound):
    tags = set(sound.get('tags', []) + sound.get('raw_tags', []))
    note=str(sound.get('note',''))
    note_terms={
        'en': EN_DIALECT | {'Nuyorican','AAVE','African American','African-American','New York','Baltimore','Scottish','Irish','Australian','Canadian','Indian English'},
        'ja': JA_DIALECT | {'関西','京都','大阪','東北','方言'},
        'zh': ZH_DIALECT | {'Sichuan','Sichuanese','Beijing dialect','四川','成都','粤语','廣東話','方言'}
    }.get(lang,set())
    explicit_standard=(lang=='en' and bool({'General-American','Received-Pronunciation'} & tags))
    if note and not explicit_standard and any(re.search(r'(?<![A-Za-z])'+re.escape(term)+r'(?![A-Za-z])',note,re.I) for term in note_terms):
        return [], 'excluded_dialect_or_accent_in_note'
    if HISTORIC & tags:
        return [], 'historical_or_nonstandard'
    if lang == 'en':
        # If a shared transcription is explicitly also US/RP, keep only those
        # allowed regions. Other accents never become separate output regions.
        regions = []
        if EN_US & tags: regions.append('en-US')
        if EN_UK & tags: regions.append('en-GB')
        if regions: return regions, None
        if EN_DIALECT & tags: return [], 'excluded_accent'
        geographic = [x for x in tags if x[:1].isupper() and x not in {'IPA','Standard','Southern'}]
        if geographic: return [], 'unresolved_accent'
        return ['en-unspecified'], None
    if lang == 'zh':
        if ZH_DIALECT & tags: return [], 'excluded_dialect'
        if 'Mandarin' not in tags and 'Standard-Chinese' not in tags:
            return [], 'mandarin_not_attested'
        if 'Standard-Chinese' not in tags:
            return [], 'standard_mandarin_not_attested'
        if 'Taiwan' in tags:
            return (['zh-TW','zh-CN'] if 'Mainland-China' in tags else ['zh-TW']), None
        return ['zh-CN'], None
    if lang == 'ja':
        if JA_DIALECT & tags: return [], 'excluded_dialect'
        geographic = [x for x in tags if x[:1].isupper() and x not in {'Tokyo','Standard','IPA','Heiban','Atamadaka','Nakadaka','Odaka'}]
        if geographic: return [], 'unresolved_accent'
        return ['ja-JP'], None
    return [], 'excluded_language'

def kind_for(word, pos):
    if pos in {'prefix','suffix','infix','interfix','affix','root','combining-form','circumfix'}:
        return pos
    if len(word) == 1: return 'character'
    return 'word'

class Export:
    def __init__(self, name):
        OUT.mkdir(parents=True, exist_ok=True)
        REPORT.mkdir(parents=True, exist_ok=True)
        self.file = gzip.open(OUT/(name+'.jsonl.gz.part'), 'wt', encoding='utf-8', compresslevel=5)
        self.rejected = gzip.open(OUT/(name+'.quarantine.jsonl.gz.part'), 'wt', encoding='utf-8', compresslevel=5)
        self.name = name
        self.counts = collections.Counter()
    def reject(self, reason, record):
        self.counts['rejected:'+reason] += 1
        self.rejected.write(compact(dict(reason=reason, **record))+'\n')
    def write(self, record):
        reason=language_ipa_reason(record['ipa'],record['language'])
        if reason:
            self.reject(reason,record);return
        self.counts['accepted'] += 1
        self.counts['region:'+record['region']] += 1
        self.counts['kind:'+record['kind']] += 1
        self.counts['tone:'+str(record['has_tone'])] += 1
        self.counts['syllables:'+str(record['has_syllable_boundaries'])] += 1
        self.file.write(compact(record)+'\n')
    def close(self):
        self.file.close(); self.rejected.close()
        for suffix in ['.jsonl.gz','.quarantine.jsonl.gz']:
            (OUT/(self.name+suffix+'.part')).replace(OUT/(self.name+suffix))
        (REPORT/(self.name+'.json')).write_text(json.dumps(self.counts,ensure_ascii=False,indent=2)+'\n')
        print(self.name, dict(self.counts), flush=True)

def make_record(word, lang, region, pos, ipa, source, line, sound, method):
    tone = any(c in TONE for c in ud.normalize('NFD',ipa))
    return dict(word=word, language=lang, region=region, kind=kind_for(word,pos), pos=pos,
        ipa=ipa, ipa_no_tone=ud.normalize('NFC',''.join(c for c in ud.normalize('NFD',ipa) if c not in TONE)),
        ipa_no_syllable_boundaries=ipa.replace('.',''),
        has_tone=tone, has_syllable_boundaries='.' in ipa,
        has_stress=bool(set(ipa)&set('ˈˌ')), tone_notation=('ipa_tone_letters_or_diacritics' if tone else 'none'),
        source_tone_notation=('numeric' if method.startswith('chao_digits') else 'ipa_symbols' if tone else 'none'),
        source=source, source_line=line,
        source_sound=sound, method=method, review_status='automatic_provenance_and_character_screen')

def ipa_dict(source):
    path = ROOT/source['path']
    if not path.exists(): return
    code = path.stem
    lang = 'en' if code.startswith('en') else 'zh' if code.startswith('zh') else 'ja'
    region = {'en_US':'en-US','en_UK':'en-GB','zh_hans':'zh-CN','zh_hant':'zh-CN','ja':'ja-JP'}[code]
    export = Export(source['id'])
    with path.open(encoding='utf-8') as f:
        for line, text in enumerate(f, 1):
            if not text.strip(): continue
            word, sep, raw = text.rstrip('\n').partition('\t')
            if not sep:
                export.reject('malformed_tsv',dict(source_line=line,source=source['id']));continue
            # Every slash-delimited variant is retained; commas are separators.
            candidates = re.findall(r'/([^/]+)/',raw)
            if not candidates:
                export.reject('missing_delimited_ipa',dict(word=word,ipa_raw=raw,source_line=line));continue
            for index, candidate in enumerate(candidates):
                ipa,error = strict_ipa(candidate)
                if error:
                    export.reject(error,dict(word=word,ipa_raw=candidate,source_line=line));continue
                record = make_record(word,lang,region,'unknown',ipa,source['id'],line,index,'source_ipa')
                record.update(ipa_raw=candidate, orthography={'zh_hans':'Hans','zh_hant':'Hant'}.get(code),
                    taiwan_fallback_only=(code=='zh_hant'),
                    source_metadata=dict(source_variant=code,regional_basis='source_declared'))
                export.write(record)
    export.close()

def wiktextract(source):
    path = ROOT/source['path']
    if not path.exists(): return
    export = Export(source['id'])
    opener = gzip.open if path.suffix=='.gz' else open
    with opener(path,'rt',encoding='utf-8') as f:
        for line,text in enumerate(f,1):
            try:entry=json.loads(text)
            except json.JSONDecodeError:
                raise ValueError(f'{path}:{line}: corrupt or incomplete source JSON')
            lang=entry.get('lang_code')
            if lang not in {'en','zh','ja'}:continue
            export.counts['source_entries:'+lang]+=1
            for index,sound in enumerate(entry.get('sounds',[])):
                if 'ipa' not in sound:
                    export.counts['non_ipa_sound_fields_ignored']+=1;continue
                regions,reason=region_for(lang,sound)
                if reason:
                    export.counts[reason]+=1;continue
                raw=sound['ipa']
                ipa,error=strict_ipa(raw)
                variants=[(ipa,'source_ipa')] if not error else numeric_tone_variants(raw) or optional_segment_variants(raw)
                if not variants:
                    export.reject(error,dict(word=entry.get('word'),language=lang,source_line=line,
                        source_sound=index,ipa_raw=raw,tags=sound.get('tags'),raw_tags=sound.get('raw_tags')))
                    continue
                for region in regions:
                    for ipa,method in variants:
                        record=make_record(entry.get('word',''),lang,region,entry.get('pos','unknown'),ipa,
                            source['id'],line,index,method)
                        record.update(ipa_raw=raw,tags=sound.get('tags',[]),raw_tags=sound.get('raw_tags',[]),
                            source_metadata={k:entry[k] for k in ['head_templates','etymology_number','etymology_text','forms'] if k in entry},
                            sound_metadata=sound,
                            sense_glosses=[s.get('glosses',[]) for s in entry.get('senses',[])],
                            regional_basis=('unlabelled_source_default' if region in {'ja-JP','en-unspecified'} else 'source_tags'))
                        if record['source_tone_notation']=='numeric':
                            record['ipa_with_numeric_tone']=raw
                        export.write(record)
            if line%100000==0:print(source['id'],line,dict(export.counts.most_common(5)),flush=True)
    export.close()

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--source',action='append')
    args=parser.parse_args()
    sources=json.loads((ROOT/'catalog/sources.json').read_text())
    if (ROOT/'catalog/extra-sources.json').exists():sources+=json.loads((ROOT/'catalog/extra-sources.json').read_text())
    for source in sources:
        if args.source and source['id'] not in args.source:continue
        if source['kind']=='ipa-tsv':ipa_dict(source)
        elif source['kind'].startswith('wiktextract-jsonl'):wiktextract(source)

if __name__=='__main__':main()
