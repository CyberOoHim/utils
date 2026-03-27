"""
POJ Two-Way Tone Converter

This script is a standalone tool designed to convert text between traditional
diacritics and numeric tonemarks for Taiwanese languages. It exclusively supports
the POJ (Pe̍h-ōe-jī) romanization system.

Key Terms:
    - POJ (Pe̍h-ōe-jī): A traditional orthography for Taiwanese Hokkien.
    - Diacritics: Accent marks used to indicate tones and special vowels (e.g., á, ò͘, ⁿ).
    - Number tonemarks: A simpler, ASCII-friendly representation where tones are indicated by numbers at the end of syllables (e.g., a2, oo3).

Examples:
    Command Line Usage:

    1. Auto-detect conversion (Numbers to Diacritics):
       $ python poj_converter.py "phai2nn"
       pháiⁿ

    2. Auto-detect conversion (Diacritics to Numbers):
       $ python poj_converter.py "pháiⁿ"
       phai2nn

    3. Explicit conversion to numeric:
       $ python poj_converter.py --to-numeric "kò͘-kheh"
       koo3-kheh

    4. Explicit conversion to diacritics:
       $ python poj_converter.py --to-diacritic "koo3-kheh"
       kò͘-kheh
"""

import re
import sys
import os
import argparse

# --- Embedded Converter Logic (Adapted from Cyber OÍ˜-hÃ®m ki-tÄ“) ---
# This ensures the script is standalone and does not depend on external paths.

TL_TONE6_USE_CARON       = True
TL_ALL_CAPS_SUPPORT      = True
TL_USE_NASAL_SUPERSCRIPT = True

_TL_BUE = '((?:rm|rng|rn|r|m|ng|n|nnh|nn|\u207f|N)?)'

def _rc(char): return re.compile(char + r'([aAeEgGhHiIkKmMnNoOpPtTuU]*)', re.UNICODE)
def _rc1(tailo): return re.compile(tailo[:-1] + _TL_BUE + tailo[-1], re.UNICODE)
def _rc2(tailo): return re.compile(tailo[0] + r'(u|i)((?:N|\u207f|nn)?)' + tailo[1], re.UNICODE)
def _rc3(pattern): return re.compile(pattern, re.UNICODE)
def _rc4(tailo): return re.compile(tailo[:-1] + r'(h(?:N|\u207f|nn)?|H(?:N|\u207f|nn)?|p|P|t|T|k|K)' + tailo[-1] + r'?', re.UNICODE)
def _rc8(tailo): return re.compile(tailo[:-1] + r'(h(?:N|\u207f|nn)?|H(?:N|\u207f|nn)?|p|P|t|T|k|K)' + tailo[-1], re.UNICODE)

def _tl_tiau_2_tl_soo(tl_tiau, nasal='nn', use14=False):
    s = tl_tiau
    if not s: return ''
    TL_T2S = [
        (_rc('\u00e1'), 'a\\g<1>2'),   (_rc('\u00c1'), 'A\\g<1>2'),
        (_rc('\u00e0'), 'a\\g<1>3'),   (_rc('\u00c0'), 'A\\g<1>3'),
        (_rc('\u00e2'), 'a\\g<1>5'),   (_rc('\u00c2'), 'A\\g<1>5'),
        (_rc('\u01ce'), 'a\\g<1>6'),   (_rc('\u01cd'), 'A\\g<1>6'),
        (_rc('\u0101'), 'a\\g<1>7'),   (_rc('\u0100'), 'A\\g<1>7'),
        (_rc('a\u030d'), 'a\\g<1>8'), (_rc('A\u030d'), 'A\\g<1>8'),
        (_rc('a\u030b'), 'a\\g<1>9'), (_rc('A\u030b'), 'A\\g<1>9'),
        (_rc('\u0103'), 'a\\g<1>9'),   (_rc('\u0102'), 'A\\g<1>9'),
        (_rc('a\u0306'), 'a\\g<1>9'), (_rc('A\u0306'), 'A\\g<1>9'),
        
        (_rc('\u00e9'), 'e\\g<1>2'),   (_rc('\u00c9'), 'E\\g<1>2'),
        (_rc('\u00e8'), 'e\\g<1>3'),   (_rc('\u00c8'), 'E\\g<1>3'),
        (_rc('\u00ea'), 'e\\g<1>5'),   (_rc('\u00ca'), 'E\\g<1>5'),
        (_rc('\u011b'), 'e\\g<1>6'),   (_rc('\u011a'), 'E\\g<1>6'),
        (_rc('\u0113'), 'e\\g<1>7'),   (_rc('\u0112'), 'E\\g<1>7'),
        (_rc('e\u030d'), 'e\\g<1>8'), (_rc('E\u030d'), 'E\\g<1>8'),
        (_rc('e\u030b'), 'e\\g<1>9'), (_rc('E\u030b'), 'E\\g<1>9'),
        (_rc('\u0115'), 'e\\g<1>9'),   (_rc('\u0114'), 'E\\g<1>9'),
        (_rc('e\u0306'), 'e\\g<1>9'), (_rc('E\u0306'), 'E\\g<1>9'),
        
        (_rc('\u00ed'), 'i\\g<1>2'),   (_rc('\u00cd'), 'I\\g<1>2'),
        (_rc('\u00ec'), 'i\\g<1>3'),   (_rc('\u00cc'), 'I\\g<1>3'),
        (_rc('\u00ee'), 'i\\g<1>5'),   (_rc('\u00ce'), 'I\\g<1>5'),
        (_rc('\u01d0'), 'i\\g<1>6'),   (_rc('\u01cf'), 'I\\g<1>6'),
        (_rc('\u012b'), 'i\\g<1>7'),   (_rc('\u012a'), 'I\\g<1>7'),
        (_rc('i\u030d'), 'i\\g<1>8'), (_rc('I\u030d'), 'I\\g<1>8'),
        (_rc('i\u030b'), 'i\\g<1>9'), (_rc('I\u030b'), 'I\\g<1>9'),
        (_rc('\u012d'), 'i\\g<1>9'),   (_rc('\u012c'), 'I\\g<1>9'),
        (_rc('i\u0306'), 'i\\g<1>9'), (_rc('I\u0306'), 'I\\g<1>9'),
        
        (_rc('\u00f3\u0358'), 'oo\\g<1>2'), (_rc('\u00d3\u0358'), 'Oo\\g<1>2'),
        (_rc('\u00f2\u0358'), 'oo\\g<1>3'), (_rc('\u00d2\u0358'), 'Oo\\g<1>3'),
        (_rc('\u00f4\u0358'), 'oo\\g<1>5'), (_rc('\u00d4\u0358'), 'Oo\\g<1>5'),
        (_rc('\u01d2\u0358'), 'oo\\g<1>6'), (_rc('\u01d1\u0358'), 'Oo\\g<1>6'),
        (_rc('\u014d\u0358'), 'oo\\g<1>7'), (_rc('\u014c\u0358'), 'Oo\\g<1>7'),
        (_rc('o\u030d\u0358'), 'oo\\g<1>8'), (_rc('O\u030d\u0358'), 'Oo\\g<1>8'),
        (_rc('\u0151\u0358'), 'oo\\g<1>9'), (_rc('\u0150\u0358'), 'Oo\\g<1>9'),
        (_rc('\u014f\u0358'), 'oo\\g<1>9'), (_rc('\u014e\u0358'), 'Oo\\g<1>9'),
        (_rc('o\u0306\u0358'), 'oo\\g<1>9'), (_rc('O\u0306\u0358'), 'Oo\\g<1>9'),
        (_rc('o\u0358\u0306'), 'oo\\g<1>9'), (_rc('O\u0358\u0306'), 'Oo\\g<1>9'),
        
        (_rc('\u00f3'), 'o\\g<1>2'),   (_rc('\u00d3'), 'O\\g<1>2'),
        (_rc('\u00f2'), 'o\\g<1>3'),   (_rc('\u00d2'), 'O\\g<1>3'),
        (_rc('\u00f4'), 'o\\g<1>5'),   (_rc('\u00d4'), 'O\\g<1>5'),
        (_rc('\u01d2'), 'o\\g<1>6'),   (_rc('\u01d1'), 'O\\g<1>6'),
        (_rc('\u014d'), 'o\\g<1>7'),   (_rc('\u014c'), 'O\\g<1>7'),
        (_rc('o\u030d'), 'o\\g<1>8'), (_rc('O\u030d'), 'O\\g<1>8'),
        (_rc('\u0151'), 'o\\g<1>9'),   (_rc('\u0150'), 'O\\g<1>9'),
        (_rc('\u014f'), 'o\\g<1>9'),   (_rc('\u014e'), 'O\\g<1>9'),
        (_rc('o\u0306'), 'o\\g<1>9'), (_rc('O\u0306'), 'O\\g<1>9'),
        
        (_rc('\u00fa'), 'u\\g<1>2'),   (_rc('\u00da'), 'U\\g<1>2'),
        (_rc('\u00f9'), 'u\\g<1>3'),   (_rc('\u00d9'), 'U\\g<1>3'),
        (_rc('\u00fb'), 'u\\g<1>5'),   (_rc('\u00db'), 'U\\g<1>5'),
        (_rc('\u01d4'), 'u\\g<1>6'),   (_rc('\u01d3'), 'U\\g<1>6'),
        (_rc('\u016b'), 'u\\g<1>7'),   (_rc('\u016a'), 'U\\g<1>7'),
        (_rc('u\u030d'), 'u\\g<1>8'), (_rc('U\u030d'), 'U\\g<1>8'),
        (_rc('\u0171'), 'u\\g<1>9'),   (_rc('\u0170'), 'U\\g<1>9'),
        (_rc('\u016d'), 'u\\g<1>9'),   (_rc('\u016c'), 'U\\g<1>9'),
        (_rc('u\u0306'), 'u\\g<1>9'), (_rc('U\u0306'), 'U\\g<1>9'),
        
        (_rc('\u1e3f'), 'm\\g<1>2'),   (_rc('\u1e3e'), 'M\\g<1>2'),
        (_rc('m\u0300'), 'm\\g<1>3'), (_rc('M\u0300'), 'M\\g<1>3'),
        (_rc('m\u0302'), 'm\\g<1>5'), (_rc('M\u0302'), 'M\\g<1>5'),
        (_rc('m\u030c'), 'm\\g<1>6'), (_rc('M\u030c'), 'M\\g<1>6'),
        (_rc('m\u0304'), 'm\\g<1>7'), (_rc('M\u0304'), 'M\\g<1>7'),
        (_rc('m\u030d'), 'm\\g<1>8'), (_rc('M\u030d'), 'M\\g<1>8'),
        (_rc('m\u030b'), 'm\\g<1>9'), (_rc('M\u030b'), 'M\\g<1>9'),
        (_rc('m\u0306'), 'm\\g<1>9'), (_rc('M\u0306'), 'M\\g<1>9'),
        
        (_rc('\u0144'), 'n\\g<1>2'),   (_rc('\u0143'), 'N\\g<1>2'),
        (_rc('\u01f9'), 'n\\g<1>3'),   (_rc('\u01f8'), 'N\\g<1>3'),
        (_rc('n\u0302'), 'n\\g<1>5'), (_rc('N\u0302'), 'N\\g<1>5'),
        (_rc('\u0148'), 'n\\g<1>6'),   (_rc('\u0147'), 'N\\g<1>6'),
        (_rc('n\u0304'), 'n\\g<1>7'), (_rc('N\u0304'), 'N\\g<1>7'),
        (_rc('n\u030d'), 'n\\g<1>8'), (_rc('N\u030d'), 'N\\g<1>8'),
        (_rc('n\u030b'), 'n\\g<1>9'), (_rc('N\u030b'), 'N\\g<1>9'),
        (_rc('n\u0306'), 'n\\g<1>9'), (_rc('N\u0306'), 'N\\g<1>9'),
    ]

    for (pat, repl) in TL_T2S:
        s = pat.sub(repl, s)

    s = s.replace('o\u0358', 'oo').replace('O\u0358', 'Oo')
    s = s.replace('\u207f', 'nn')

    if nasal == 'N':
        s = re.sub(r'(?<=[aeiouAEIOU])nn', 'N', s)

    if use14:
        s = re.sub(r'(a|A|e|E|i|I|o|O|u|U|m|M|ng|Ng|n)\b', r'\g<1>1', s)
        s = re.sub(r'(h|H|p|P|t|T|k|K)\b',                 r'\g<1>4', s)
    return s

def _poj_soo_2_poj_tiau(poj_soo):
    if not poj_soo: return ''
    s = poj_soo

    _nn_repl = '\\g<1>' + '\u207f' + '\\g<2>'
    s = re.sub(r'([aeiouAEIOUmM]\u0358?h?|[aeiouAEIOUmM]\u0358?|[hH])nn(\d?)\b', _nn_repl, s)

    if TL_USE_NASAL_SUPERSCRIPT:
        _N_repl = '\\g<1>' + '\u207f' + '\\g<2>'
        s = re.sub(r'([aeioumM]\u0358?h?|[aeioumM]\u0358?|[hH])N(\d?)\b', _N_repl, s)
        _Nh_repl = '\\g<1>h' + '\u207f' + '\\g<2>'
        s = re.sub(r'([aeioumM]\u0358?)Nh?(\d?)\b', _Nh_repl, s)

    if TL_TONE6_USE_CARON:
        t6 = {'a': '\u01ce', 'A': '\u01cd', 'e': '\u011b', 'E': '\u011a', 'i': '\u01d0', 'I': '\u01cf', 'o': '\u01d2', 'O': '\u01d1', 'u': '\u01d4', 'U': '\u01d3', 'o\u0358': '\u01d2\u0358', 'O\u0358': '\u01d1\u0358', 'm': 'm\u030c', 'M': 'M\u030c', 'n': '\u0148', 'N': '\u0147'}
    else:
        t6 = {'a': '\u00e1', 'A': '\u00c1', 'e': '\u00e9', 'E': '\u00c9', 'i': '\u00ed', 'I': '\u00cd', 'o': '\u00f3', 'O': '\u00d3', 'u': '\u00fa', 'U': '\u00da', 'o\u0358': '\u00f3\u0358', 'O\u0358': '\u00d3\u0358', 'm': '\u1e3f', 'M': '\u1e3e', 'n': '\u0144', 'N': '\u0143'}

    POJ_S2T = [
        (_rc2('a1'), 'a\\g<1>\\g<2>'), (_rc2('A1'), 'A\\g<1>\\g<2>'),
        (_rc2('a2'), '\u00e1\\g<1>\\g<2>'), (_rc2('A2'), '\u00c1\\g<1>\\g<2>'),
        (_rc2('a3'), '\u00e0\\g<1>\\g<2>'), (_rc2('A3'), '\u00c0\\g<1>\\g<2>'),
        (_rc3(r'a(i|u)((?:N|\u207f|nn)?)h4'), 'a\\g<1>\\g<2>h'), (_rc3(r'A(i|u)((?:N|\u207f|nn)?)h4'), 'A\\g<1>\\g<2>h'),
        (_rc2('a5'), '\u00e2\\g<1>\\g<2>'), (_rc2('A5'), '\u00c2\\g<1>\\g<2>'),
        (_rc2('a6'), t6['a'] + '\\g<1>\\g<2>'), (_rc2('A6'), t6['A'] + '\\g<1>\\g<2>'),
        (_rc2('a7'), '\u0101\\g<1>\\g<2>'), (_rc2('A7'), '\u0100\\g<1>\\g<2>'),
        (_rc3(r'a(i|u)h(?:N|\u207f|nn)8'), 'a\u030d\\g<1>h\u207f'), (_rc3(r'A(i|u)h(?:N|\u207f|nn)8'), 'A\u030d\\g<1>h\u207f'),
        (_rc3(r'a(i|u)h8'), 'a\u030d\\g<1>h'), (_rc3(r'A(i|u)h8'), 'A\u030d\\g<1>h'),
        (_rc2('a9'), '\u0103\\g<1>\\g<2>'), (_rc2('A9'), '\u0102\\g<1>\\g<2>'),

        (_rc2('u1'), 'u\\g<1>\\g<2>'), (_rc2('U1'), 'U\\g<1>\\g<2>'),
        (_rc2('u2'), '\u00fa\\g<1>\\g<2>'), (_rc2('U2'), '\u00da\\g<1>\\g<2>'),
        (_rc2('u3'), '\u00f9\\g<1>\\g<2>'), (_rc2('U3'), '\u00d9\\g<1>\\g<2>'),
        (_rc3(r'uih((?:N|\u207f|nn)?)4'), 'uih\\g<1>'), (_rc3(r'Uih((?:N|\u207f|nn)?)4'), 'Uih\\g<1>'),
        (_rc2('u5'), '\u00fb\\g<1>\\g<2>'), (_rc2('U5'), '\u00db\\g<1>\\g<2>'),
        (_rc2('u6'), t6['u'] + '\\g<1>\\g<2>'), (_rc2('U6'), t6['U'] + '\\g<1>\\g<2>'),
        (_rc2('u7'), '\u016b\\g<1>\\g<2>'), (_rc2('U7'), '\u016a\\g<1>\\g<2>'),
        (_rc3(r'uih((?:N|\u207f|nn)?)8'), 'u\u030dih\\g<1>'), (_rc3(r'Uih((?:N|\u207f|nn)?)8'), 'U\u030dih\\g<1>'),
        (_rc2('u9'), '\u016d\\g<1>\\g<2>'), (_rc2('U9'), '\u016c\\g<1>\\g<2>'),

        (_rc3(r'(o|O)([ae])((?:N|\u207f|nn)?)1?\b'), '\\g<1>\\g<2>\\g<3>'),
        (_rc3(r'o([ae])((?:N|\u207f|nn)?)2\b'), '\u00f3\\g<1>\\g<2>'), (_rc3(r'O([ae])((?:N|\u207f|nn)?)2\b'), '\u00d3\\g<1>\\g<2>'),
        (_rc3(r'o([ae])((?:N|\u207f|nn)?)3\b'), '\u00f2\\g<1>\\g<2>'), (_rc3(r'O([ae])((?:N|\u207f|nn)?)3\b'), '\u00d2\\g<1>\\g<2>'),
        (_rc3(r'o([ae])((?:N|\u207f|nn)?)5\b'), '\u00f4\\g<1>\\g<2>'), (_rc3(r'O([ae])((?:N|\u207f|nn)?)5\b'), '\u00d4\\g<1>\\g<2>'),
        (_rc3(r'o([ae])((?:N|\u207f|nn)?)6\b'), t6['o'] + '\\g<1>\\g<2>'), (_rc3(r'O([ae])((?:N|\u207f|nn)?)6\b'), t6['O'] + '\\g<1>\\g<2>'),
        (_rc3(r'o([ae])((?:N|\u207f|nn)?)7\b'), '\u014d\\g<1>\\g<2>'), (_rc3(r'O([ae])((?:N|\u207f|nn)?)7\b'), '\u014c\\g<1>\\g<2>'),
        (_rc3(r'o([ae])((?:N|\u207f|nn)?)9\b'), '\u014f\\g<1>\\g<2>'), (_rc3(r'O([ae])((?:N|\u207f|nn)?)9\b'), '\u014e\\g<1>\\g<2>'),

        (_rc1('a1'), 'a\\g<1>'), (_rc1('A1'), 'A\\g<1>'),
        (_rc1('a2'), '\u00e1\\g<1>'), (_rc1('A2'), '\u00c1\\g<1>'),
        (_rc1('a3'), '\u00e0\\g<1>'), (_rc1('A3'), '\u00c0\\g<1>'),
        (_rc4('a4'), 'a\\g<1>'), (_rc4('A4'), 'A\\g<1>'),
        (_rc1('a5'), '\u00e2\\g<1>'), (_rc1('A5'), '\u00c2\\g<1>'),
        (_rc1('a6'), t6['a'] + '\\g<1>'), (_rc1('A6'), t6['A'] + '\\g<1>'),
        (_rc1('a7'), '\u0101\\g<1>'), (_rc1('A7'), '\u0100\\g<1>'),
        (_rc8('a8'), 'a\u030d\\g<1>'), (_rc8('A8'), 'A\u030d\\g<1>'),
        (_rc1('a9'), '\u0103\\g<1>'), (_rc1('A9'), '\u0102\\g<1>'),

        (_rc1('e1'), 'e\\g<1>'), (_rc1('E1'), 'E\\g<1>'),
        (_rc1('e2'), '\u00e9\\g<1>'), (_rc1('E2'), '\u00c9\\g<1>'),
        (_rc1('e3'), '\u00e8\\g<1>'), (_rc1('E3'), '\u00c8\\g<1>'),
        (_rc4('e4'), 'e\\g<1>'), (_rc4('E4'), 'E\\g<1>'),
        (_rc1('e5'), '\u00ea\\g<1>'), (_rc1('E5'), '\u00ca\\g<1>'),
        (_rc1('e6'), t6['e'] + '\\g<1>'), (_rc1('E6'), t6['E'] + '\\g<1>'),
        (_rc1('e7'), '\u0113\\g<1>'), (_rc1('E7'), '\u0112\\g<1>'),
        (_rc8('e8'), 'e\u030d\\g<1>'), (_rc8('E8'), 'E\u030d\\g<1>'),
        (_rc1('e9'), '\u0115\\g<1>'), (_rc1('E9'), '\u0114\\g<1>'),

        (_rc1('i1'), 'i\\g<1>'), (_rc1('I1'), 'I\\g<1>'),
        (_rc1('i2'), '\u00ed\\g<1>'), (_rc1('I2'), '\u00cd\\g<1>'),
        (_rc1('i3'), '\u00ec\\g<1>'), (_rc1('I3'), '\u00cc\\g<1>'),
        (_rc4('i4'), 'i\\g<1>'), (_rc4('I4'), 'I\\g<1>'),
        (_rc1('i5'), '\u00ee\\g<1>'), (_rc1('I5'), '\u00ce\\g<1>'),
        (_rc1('i6'), t6['i'] + '\\g<1>'), (_rc1('I6'), t6['I'] + '\\g<1>'),
        (_rc1('i7'), '\u012b\\g<1>'), (_rc1('I7'), '\u012a\\g<1>'),
        (_rc8('i8'), 'i\u030d\\g<1>'), (_rc8('I8'), 'I\u030d\\g<1>'),
        (_rc1('i9'), '\u012d\\g<1>'), (_rc1('I9'), '\u012c\\g<1>'),

        (_rc1('o\u03581'), 'o\u0358\\g<1>'), (_rc1('O\u03581'), 'O\u0358\\g<1>'),
        (_rc1('o\u03582'), '\u00f3\u0358\\g<1>'), (_rc1('O\u03582'), '\u00d3\u0358\\g<1>'),
        (_rc1('o\u03583'), '\u00f2\u0358\\g<1>'), (_rc1('O\u03583'), '\u00d2\u0358\\g<1>'),
        (_rc4('o\u03584'), 'o\u0358\\g<1>'), (_rc4('O\u03584'), 'O\u0358\\g<1>'),
        (_rc1('o\u03585'), '\u00f4\u0358\\g<1>'), (_rc1('O\u03585'), '\u00d4\u0358\\g<1>'),
        (_rc1('o\u03586'), t6['o\u0358'] + '\\g<1>'), (_rc1('O\u03586'), t6['O\u0358'] + '\\g<1>'),
        (_rc1('o\u03587'), '\u014d\u0358\\g<1>'), (_rc1('O\u03587'), '\u014c\u0358\\g<1>'),
        (_rc8('o\u03588'), 'o\u030d\u0358\\g<1>'), (_rc8('O\u03588'), 'O\u030d\u0358\\g<1>'),
        (_rc1('o\u03589'), '\u014f\u0358\\g<1>'), (_rc1('O\u03589'), '\u014e\u0358\\g<1>'),

        (_rc1('o1'), 'o\\g<1>'), (_rc1('O1'), 'O\\g<1>'),
        (_rc1('o2'), '\u00f3\\g<1>'), (_rc1('O2'), '\u00d3\\g<1>'),
        (_rc1('o3'), '\u00f2\\g<1>'), (_rc1('O3'), '\u00d2\\g<1>'),
        (_rc4('o4'), 'o\\g<1>'), (_rc4('O4'), 'O\\g<1>'),
        (_rc1('o5'), '\u00f4\\g<1>'), (_rc1('O5'), '\u00d4\\g<1>'),
        (_rc1('o6'), t6['o'] + '\\g<1>'), (_rc1('O6'), t6['O'] + '\\g<1>'),
        (_rc1('o7'), '\u014d\\g<1>'), (_rc1('O7'), '\u014c\\g<1>'),
        (_rc8('o8'), 'o\u030d\\g<1>'), (_rc8('O8'), 'O\u030d\\g<1>'),
        (_rc1('o9'), '\u014f\\g<1>'), (_rc1('O9'), '\u014e\\g<1>'),

        (_rc1('u1'), 'u\\g<1>'), (_rc1('U1'), 'U\\g<1>'),
        (_rc1('u2'), '\u00fa\\g<1>'), (_rc1('U2'), '\u00da\\g<1>'),
        (_rc1('u3'), '\u00f9\\g<1>'), (_rc1('U3'), '\u00d9\\g<1>'),
        (_rc4('u4'), 'u\\g<1>'), (_rc4('U4'), 'U\\g<1>'),
        (_rc1('u5'), '\u00fb\\g<1>'), (_rc1('U5'), '\u00db\\g<1>'),
        (_rc1('u6'), t6['u'] + '\\g<1>'), (_rc1('U6'), t6['U'] + '\\g<1>'),
        (_rc1('u7'), '\u016b\\g<1>'), (_rc1('U7'), '\u016a\\g<1>'),
        (_rc8('u8'), 'u\u030d\\g<1>'), (_rc8('U8'), 'U\u030d\\g<1>'),
        (_rc1('u9'), '\u016d\\g<1>'), (_rc1('U9'), '\u016c\\g<1>'),

        (_rc3('m1'), 'm'), (_rc3('M1'), 'M'),
        (_rc3('m2'), '\u1e3f'), (_rc3('M2'), '\u1e3e'),
        (_rc3('m3'), 'm\u0300'), (_rc3('M3'), 'M\u0300'),
        (_rc3('mh4'), 'mh'), (_rc3('Mh4'), 'Mh'),
        (_rc3('m5'), 'm\u0302'), (_rc3('M5'), 'M\u0302'),
        (_rc3('m6'), t6['m']), (_rc3('M6'), t6['M']),
        (_rc3('m7'), 'm\u0304'), (_rc3('M7'), 'M\u0304'),
        (_rc3('mh8'), 'm\u030dh'), (_rc3('Mh8'), 'M\u030dh'),
        (_rc3('m9'), 'm\u0306'), (_rc3('M9'), 'M\u0306'),

        (_rc3('ng1'), 'ng'), (_rc3('Ng1'), 'Ng'),
        (_rc3('ng2'), '\u0144g'), (_rc3('Ng2'), '\u0143g'),
        (_rc3('ng3'), '\u01f9g'), (_rc3('Ng3'), '\u01f8g'),
        (_rc3('ngh4'), 'ngh'), (_rc3('Ngh4'), 'Ngh'),
        (_rc3('ng5'), 'n\u0302g'), (_rc3('Ng5'), 'N\u0302g'),
        (_rc3('ng6'), t6['n'] + 'g'), (_rc3('Ng6'), t6['N'] + 'g'),
        (_rc3('ng7'), 'n\u0304g'), (_rc3('Ng7'), 'N\u0304g'),
        (_rc3('ngh8'), 'n\u030dgh'), (_rc3('Ngh8'), 'N\u030dgh'),
        (_rc3('ng9'), 'n\u0306g'), (_rc3('Ng9'), 'N\u0306g'),
    ]

    if TL_ALL_CAPS_SUPPORT:
        POJ_S2T += [
            (_rc3('NG1'), 'NG'),
            (_rc3('NG2'), '\u0143G'),
            (_rc3('NG3'), '\u01f8G'),
            (_rc3('NGH4'), 'NGH'),
            (_rc3('NG5'), 'N\u0302G'),
            (_rc3('NG6'), t6['N'] + 'G'),
            (_rc3('NG7'), 'N\u0304G'),
            (_rc3('NGH8'), 'N\u030dGH'),
            (_rc3('NG9'), 'N\u0306G'),
        ]

    for (pat, repl) in POJ_S2T:
        s = pat.sub(repl, s)

    return s


# --- Core Two-Way Functions ---

def convert_to_numeric(text):
    """
    Converts text with POJ/Tailo diacritics into strict numeric format.
    - Strips accents -> tone numbers.
    - Converts ⁿ -> nn, o͘ -> oo
    """
    # 1. Convert diacritics to numbers (and normalize ⁿ/o͘)
    numeric_text = _tl_tiau_2_tl_soo(text)
    
    # 2. Shift the tone numbers to the very end of the syllable block
    # (e.g., 'phai2nn' -> 'phainn2')
    # This is required because tl_tiau_2_tl_soo often drops the digit before nasal markers
    # but the internal diacritic restorer expects the digit at the absolute end.
    shifted = re.sub(r'(\d)([a-zA-Z]+)', r'\2\1', numeric_text)
    
    return shifted

def convert_to_diacritic(text):
    """
    Converts numeric Tailo/POJ back to text with proper diacritics.
    - Reconstructs accents based on tone numbers.
    - Converts nn -> ⁿ, oo -> o͘
    """
    # 1. Prepare text for the internal _poj_soo_2_poj_tiau 
    # Convert 'oo' to 'o\u0358' before feeding into the internal engine
    # so that the engine treats it as 'o͘' and applies the correct tone rules.
    prepped_text = text.replace('oo', 'o\u0358').replace('Oo', 'O\u0358')
    
    # 2. Apply the embedded conversion (Numbers -> Diacritics)
    diacritic_text = _poj_soo_2_poj_tiau(prepped_text)
    
    # 3. Strict POJ Enforcement:
    # The _poj_soo_2_poj_tiau function fails on raw vowel+8 without a stop consonant.
    tone_8_map = {'a8': 'a\u030d', 'A8': 'A\u030d', 'e8': 'e\u030d', 'E8': 'E\u030d', 'i8': 'i\u030d', 'I8': 'I\u030d', 'o\u03588': 'o\u030d\u0358', 'O\u03588': 'O\u030d\u0358', 'o8': 'o\u030d', 'O8': 'O\u030d', 'u8': 'u\u030d', 'U8': 'U\u030d'}
    for k, v in tone_8_map.items():
        diacritic_text = diacritic_text.replace(k, v)
        
    # Fix rare cases like "Hănn" (a-breve + nn) where the core logic misses the nasal conversion
    # We apply a final pass to convert 'nn' to 'ⁿ' (\u207f) if it follows any vowel or combined diacritic.
    # We use a broad lookbehind checking for basic vowels and common pre-combined vowels (like ă, ǐ, etc).
    diacritic_text = re.sub(r'(?<=[aAeEiIoOuU\u0102\u0103\u0114\u0115\u012c\u012d\u014e\u014f\u016c\u016d\u0300-\u036f])nn', '\u207f', diacritic_text)
        
    return diacritic_text


# --- CLI Interface ---

def main():
    parser = argparse.ArgumentParser(description="Standalone POJ/Tailo Two-Way Tone Converter")
    parser.add_argument("text", type=str, nargs="?", help="The text to convert.")
    parser.add_argument("--to-numeric", action="store_true", help="Convert FROM diacritics TO numbers (e.g. pháiⁿ -> phai2nn)")
    parser.add_argument("--to-diacritic", action="store_true", help="Convert FROM numbers TO diacritics (e.g. phai2nn -> pháiⁿ)")
    parser.add_argument("--test", action="store_true", help="Run a suite of internal tests to verify behavior.")
    
    args = parser.parse_args()

    if args.test:
        test_cases = [
            ("pháiⁿ", "phai2nn", "pháiⁿ"),
            ("khòaⁿ", "khoa3nn", "khòaⁿ"),
            ("ō͘", "oo7", "ō͘"),
            ("o͘", "oo", "o͘"),
            ("a̍", "a8", "a̍"),
            ("pháiⁿ-khòaⁿ", "phai2nn-khoa3nn", "pháiⁿ-khòaⁿ"),
            ("kò͘-kheh", "koo3-kheh", "kò͘-kheh"),
            ("tsuí", "tsui2", "tsuí"),
            ("tshui", "tshui", "tshui"),
            ("ing", "ing", "ing"),
            ("chuí", "chui2", "chuí"),
            ("eng", "eng", "eng"),
        ]
        
        print("--- Running Tests ---")
        all_passed = True
        for original_dia, num, strict_dia in test_cases:
            # Test Dia -> Num
            res_num = convert_to_numeric(original_dia)
            if res_num != num:
                print(f"[FAIL] to-numeric: {original_dia} -> {res_num} (Expected: {num})")
                all_passed = False
            else:
                print(f"[PASS] to-numeric: {original_dia} -> {res_num}")
                
            # Test Num -> Dia
            res_dia = convert_to_diacritic(num)
            if res_dia != strict_dia:
                print(f"[FAIL] to-diacritic: {num} -> {res_dia} (Expected: {strict_dia})")
                all_passed = False
            else:
                print(f"[PASS] to-diacritic: {num} -> {res_dia}")
                
        if all_passed:
            print("\nAll tests passed successfully!")
        sys.exit(0 if all_passed else 1)

    if not args.text:
        parser.print_help()
        sys.exit(1)

    if args.to_numeric and args.to_diacritic:
        print("Error: Please specify either --to-numeric OR --to-diacritic, not both.")
        sys.exit(1)
        
    if args.to_numeric:
        result = convert_to_numeric(args.text)
        print(result)
    elif args.to_diacritic:
        result = convert_to_diacritic(args.text)
        print(result)
    else:
        # Default behavior: auto-detect based on presence of numbers
        has_digits = bool(re.search(r'\d', args.text))
        if has_digits:
            print(f"[Auto-detect: Numbers found -> Converting to Diacritics]")
            print(convert_to_diacritic(args.text))
        else:
            print(f"[Auto-detect: No numbers found -> Converting to Numeric]")
            print(convert_to_numeric(args.text))

if __name__ == "__main__":
    main()
