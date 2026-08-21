"""Turning what Whisper heard into what you would have typed.

Whisper hands back a transcript, not a piece of writing. It writes numbers out in
words, it drops commas when the speaker runs sentences together, and it has no
way to know that "punto y aparte" was an instruction rather than something the
speaker said. This module closes that gap.

Everything here is a pure function of a string. No settings are read, no files
are touched, nothing is imported from kara.py. That is on purpose: this is the
part of the app most likely to be wrong in ways only a native speaker notices,
so it has to be the part that is cheapest to test. See tests/test_text.py.

The order matters and polish() fixes it:

    spoken_commands   before numbers, so "coma" is punctuation where it was meant
                      as punctuation and a decimal point where it was not
    words_to_digits   after, so it sees "tres coma cinco" with the comma intact
    fix_caps_and_spacing  last, because both of the above leave stray spaces
                      around the punctuation they inserted
"""
import re
import unicodedata

# Spanish first throughout this file. It is the default language, it is what the
# people testing the app speak, and it is the harder of the two -- English
# borrows the same machinery with a smaller table.
LANGUAGES = ("es", "en")


# ── Style prompt ──────────────────────────────────────────────────────────────
# Whisper imitates the formatting of whatever it is primed with. Give it a prompt
# full of commas, question marks and digits and it produces commas, question
# marks and digits; give it nothing, which is what the app did until now, and on
# a fast Spanish speaker it produces a wall of words. This is the cheapest
# accuracy fix in the whole project: one string, no code path, no runtime cost.
#
# These are written to look like ordinary dictated speech, not like a sample of
# the alphabet. A prompt that reads as a list of symbols makes Whisper produce
# lists of symbols.
STYLE_PROMPTS = {
    "es": ("Hola, ¿cómo estás? Te escribo para confirmar la reunión del martes 3 "
           "a las 15:30. Son 1.200 pesos, más o menos un 20% de descuento. "
           "¡Quedamos así, entonces! Avísame si cambia algo."),
    "en": ("Hi, how are you? I'm writing to confirm Tuesday's meeting on the 3rd "
           "at 15:30. It comes to 1,200 pesos, roughly a 20% discount. "
           "Let's leave it at that! Let me know if anything changes."),
}


def style_prompt(language):
    """The initial_prompt to hand Whisper, or None for a language without one."""
    return STYLE_PROMPTS.get(language)


# ── Normalising words for lookup ──────────────────────────────────────────────
def _norm(word):
    """Lowercase and strip accents, for table lookups only.

    Whisper is inconsistent about accents on the number words -- "veintidós" and
    "veintidos" both come back, sometimes in the same session -- and a table that
    only knows one spelling would convert one and not the other. The original
    word is never modified; this is used to decide, not to output.
    """
    decomposed = unicodedata.normalize("NFD", word.lower())
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def _tokenize(text):
    """[(text, is_word), ...] covering the string with nothing lost.

    Rebuilding is "".join(t for t, _ in tokens), which is what makes it safe to
    replace a span of tokens and leave everything around it exactly as it was.
    """
    out, i = [], 0
    for m in _WORD_RE.finditer(text):
        if m.start() > i:
            out.append((text[i:m.start()], False))
        out.append((m.group(), True))
        i = m.end()
    if i < len(text):
        out.append((text[i:], False))
    return out


def _is_plain_space(sep):
    """True for a gap between words that a number could span.

    " " yes, ", " no. A comma means the speaker stopped, and "treinta, cinco" is
    two numbers however much it looks like one.
    """
    return sep != "" and sep.strip() == "" and "\n" not in sep


# ── Numbers ───────────────────────────────────────────────────────────────────
_ES_UNITS = {
    "cero": 0, "un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4,
    "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
    "once": 11, "doce": 12, "trece": 13, "catorce": 14, "quince": 15,
    "dieciseis": 16, "diecisiete": 17, "dieciocho": 18, "diecinueve": 19,
    "veinte": 20, "veintiuno": 21, "veintiun": 21, "veintiuna": 21,
    "veintidos": 22, "veintitres": 23, "veinticuatro": 24, "veinticinco": 25,
    "veintiseis": 26, "veintisiete": 27, "veintiocho": 28, "veintinueve": 29,
    "treinta": 30, "cuarenta": 40, "cincuenta": 50, "sesenta": 60,
    "setenta": 70, "ochenta": 80, "noventa": 90,
    "cien": 100, "ciento": 100, "doscientos": 200, "doscientas": 200,
    "trescientos": 300, "trescientas": 300, "cuatrocientos": 400,
    "cuatrocientas": 400, "quinientos": 500, "quinientas": 500,
    "seiscientos": 600, "seiscientas": 600, "setecientos": 700,
    "setecientas": 700, "ochocientos": 800, "ochocientas": 800,
    "novecientos": 900, "novecientas": 900,
}
_ES_TENS = {"treinta", "cuarenta", "cincuenta", "sesenta", "setenta", "ochenta",
            "noventa"}
_ES_SCALES = {"mil": 1000, "millon": 10 ** 6, "millones": 10 ** 6,
              "billon": 10 ** 12, "billones": 10 ** 12}

_EN_UNITS = {
    "zero": 0, "oh": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30,
    "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
    "ninety": 90,
}
_EN_TENS = {"twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty",
            "ninety"}
_EN_SCALES = {"thousand": 1000, "million": 10 ** 6, "billion": 10 ** 9}
# "one hundred" is 1 x 100, where Spanish "ciento" is simply the number 100 and
# "doscientos" is its own word. Adding 100 to 1 and calling it 101 was the first
# thing the English tests caught.
_EN_MULTS = {"hundred": 100}

_TABLES = {
    "es": {
        "units": _ES_UNITS, "tens": _ES_TENS, "scales": _ES_SCALES,
        "connector": {"y"},
        # Words that are numbers in a sentence about counting and articles or
        # pronouns everywhere else. "Uno nunca sabe" must not become "1 nunca
        # sabe", and "un café" must not become "1 café". They still count inside
        # a longer number, which is why they are in the table at all: "un millón"
        # and "veintiún mil" need them.
        "alone": {"un", "una", "uno"},
        # "cien" combines to the left of nothing -- "cien mil" is 100000 -- but a
        # bare "cien por ciento" is fine to convert, so it is not listed here.
        "mults": {},
        "decimal": ("coma", "punto"),
        "decimal_mark": ",",
        "percent": r"\bpor\s+ciento\b",
    },
    "en": {
        "units": _EN_UNITS, "tens": _EN_TENS, "scales": _EN_SCALES,
        "connector": {"and"},
        "alone": {"one", "oh"},
        "mults": _EN_MULTS,
        "decimal": ("point",),
        "decimal_mark": ".",
        "percent": r"\bpercent\b",
    },
}


def _is_number_word(w, table):
    return w in table["units"] or w in table["scales"] or w in table["mults"]


def _run_words(tokens, start, table):
    """How far a number reads from token `start`, as an end index (exclusive).

    The rule that matters is that each added piece has to be smaller than the one
    before it. Spanish counts downwards -- doscientos, cincuenta, tres -- and a
    bigger piece after a smaller one means a new number has started. Without that
    check "las tres cuarenta y cinco" reads as one number and adds up to 48,
    which is how a request for clock times turns into nonsense.
    """
    units, scales, mults = table["units"], table["scales"], table["mults"]
    tens, connector = table["tens"], table["connector"]

    i, end = start, start
    last_word = None      # for the connector rule
    last_add = None       # magnitude of the last piece added, None after a scale
    while i < len(tokens):
        text, is_word = tokens[i]
        if not is_word:
            # Only a plain space may sit inside a number, and only if a number
            # word follows it.
            if not _is_plain_space(text):
                break
            i += 1
            continue

        w = _norm(text)
        if w in connector:
            # A connector belongs to the number only where the language actually
            # puts one: between a ten and a unit ("cuarenta y cinco"), or after a
            # hundred or a thousand in English ("one hundred and twenty").
            # Unrestricted it would swallow the "y" in "tres y cuatro" and answer
            # 7 to a sentence that meant "3 y 4".
            nxt = _next_word(tokens, i + 1)
            nxt_n = _norm(nxt) if nxt else None
            if last_word in tens and nxt_n in units and units[nxt_n] < 10:
                i += 1
                continue
            if (last_word in scales or last_word in mults) and nxt_n in units:
                i += 1
                continue
            break

        if w in scales or w in mults:
            # Both take whatever has accumulated and multiply it, so the
            # downward-counting rule starts again on the other side.
            last_word, last_add = w, None
            i += 1
            end = i
            continue

        if w in units:
            v = units[w]
            if last_add is not None and v >= last_add:
                break
            last_word, last_add = w, v
            i += 1
            end = i
            continue
        break
    return end


def _next_word(tokens, i):
    while i < len(tokens):
        text, is_word = tokens[i]
        if is_word:
            return text
        if not _is_plain_space(text):
            return None
        i += 1
    return None


def _parse_run(words, table):
    """Value of a sequence of number words, or None if it says nothing."""
    units, scales = table["units"], table["scales"]
    mults, connector = table["mults"], table["connector"]
    total = current = 0
    seen = False
    for w in words:
        if w in connector:
            continue
        if w in mults:
            current = (current or 1) * mults[w]
            seen = True
            continue
        if w in scales:
            mult = scales[w]
            if mult == 1000:
                # "mil" multiplies only what came directly before it, so
                # "doscientos mil" is 200000 and the 1200 in "mil doscientos"
                # is still to come.
                current = (current or 1) * mult
                total += current
                current = 0
            else:
                # A million and up takes everything counted so far with it.
                total = (total + current or 1) * mult
                current = 0
            seen = True
            continue
        current += units[w]
        seen = True
    return total + current if seen else None


def _group(n, language):
    """1200 -> '1.200' in Spanish, '1,200' in English. Below 10000, plain.

    Four-digit numbers are left alone because most of them are years, and "en
    1.998" reads as a mistake where "en 1998" reads as a date.
    """
    if n < 10000:
        return str(n)
    sep = "." if language == "es" else ","
    return f"{n:,}".replace(",", sep)


def words_to_digits(text, language="es"):
    """Rewrite spelled-out numbers as digits.

        "mil doscientos pesos"      -> "1.200 pesos"
        "son las tres cuarenta y cinco" -> "son las 3:45"
        "tres coma cinco"           -> "3,5"
        "un veinte por ciento"      -> "un 20%"

    A bare "un", "una" or "uno" is left alone: they are articles and pronouns far
    more often than they are the number one.
    """
    table = _TABLES.get(language)
    if not table:
        return text

    # "por ciento" before anything else, because "ciento" is the number 100 and
    # converting it first leaves "cincuenta por 100", which no later rule can
    # put back together.
    text = re.sub(table["percent"], "%", text, flags=re.IGNORECASE)

    tokens = _tokenize(text)
    out = []
    i = 0
    while i < len(tokens):
        chunk, is_word = tokens[i]
        if not is_word or not _is_number_word(_norm(chunk), table):
            out.append(chunk)
            i += 1
            continue

        end = _run_words(tokens, i, table)
        span = tokens[i:end]
        words = [_norm(t) for t, w in span if w]

        # One word, and that word is an article. Leave the sentence alone.
        if len(words) == 1 and words[0] in table["alone"]:
            out.append(chunk)
            i += 1
            continue

        value = _parse_run(words, table)
        if value is None:
            out.append(chunk)
            i += 1
            continue

        out.append(_group(value, language))
        i = end

    return _number_postpass("".join(out), language, table)


_TIME_RE = re.compile(r"\b(las|la|a las)\s+(\d{1,2})\s+(\d{1,2})\b", re.IGNORECASE)


def _number_postpass(text, language, table):
    """Joins that only make sense once the words either side are already digits."""
    # Decimals. Both sides have to be digits already, which is what keeps this
    # from firing on "termina el punto cinco" -- there the "cinco" would still be
    # a word if it were not a number.
    for word in table["decimal"]:
        text = re.sub(r"(\d)\s+%s\s+(\d)" % word,
                      r"\1%s\2" % table["decimal_mark"], text, flags=re.IGNORECASE)

    # The percent sign was put in before the numbers were; close the gap now
    # that there is a digit in front of it.
    text = re.sub(r"(\d)\s+%", r"\1%", text)

    # Times, Spanish only and only after "las". Without that anchor "tres
    # cuarenta y cinco" is just as likely to be a price or a page range, and
    # turning every pair of numbers into a clock would be a worse bug than the
    # one being fixed. Minutes above 59 are not a time.
    if language == "es":
        def clock(m):
            hh, mm = int(m.group(2)), int(m.group(3))
            if hh > 23 or mm > 59:
                return m.group(0)
            return "%s %d:%02d" % (m.group(1), hh, mm)
        text = _TIME_RE.sub(clock, text)
    return text


# ── Spoken punctuation ────────────────────────────────────────────────────────
# Phrases, not words. "Punto" and "coma" are ordinary Spanish -- "punto de
# partida", "la coma va acá" -- and a dictation tool that eats them is worse than
# one that never had the feature. So the default list holds only forms nobody
# says by accident, and the one-word commands live behind a setting for people
# who know they are dictating punctuation and want it.
_COMMANDS = {
    "es": [
        (("punto", "y", "aparte"), "\n\n"),
        (("punto", "y", "seguido"), ". "),
        (("punto", "aparte"), "\n\n"),
        (("nueva", "linea"), "\n"),
        (("salto", "de", "linea"), "\n"),
        (("nuevo", "parrafo"), "\n\n"),
        (("signo", "de", "interrogacion"), "?"),
        (("signo", "de", "exclamacion"), "!"),
        (("abre", "parentesis"), "("),
        (("abrir", "parentesis"), "("),
        (("cierra", "parentesis"), ")"),
        (("cerrar", "parentesis"), ")"),
        (("punto", "y", "coma"), ";"),
        (("dos", "puntos"), ":"),
        (("guion", "bajo"), "_"),
        (("arroba",), "@"),
    ],
    "en": [
        (("new", "paragraph"), "\n\n"),
        (("new", "line"), "\n"),
        (("question", "mark"), "?"),
        (("exclamation", "mark"), "!"),
        (("exclamation", "point"), "!"),
        (("open", "parenthesis"), "("),
        (("open", "paren"), "("),
        (("close", "parenthesis"), ")"),
        (("close", "paren"), ")"),
        (("semicolon",), ";"),
        (("underscore",), "_"),
        (("at", "sign"), "@"),
    ],
}

# Only with voice_commands set to "all". Every one of these is a word people say
# for its own sake, so switching this on trades one kind of mistake for another.
_COMMANDS_AGGRESSIVE = {
    "es": [
        (("coma",), ","),
        (("punto",), "."),
        (("comillas",), '"'),
    ],
    "en": [
        (("comma",), ","),
        (("period",), "."),
        (("colon",), ":"),
        (("quote",), '"'),
    ],
}


def spoken_commands(text, language="es", aggressive=False):
    """Replace dictated punctuation with the punctuation itself.

    Longest phrase first, so "punto y coma" is a semicolon rather than a full
    stop followed by the word coma.
    """
    phrases = list(_COMMANDS.get(language, []))
    if aggressive:
        phrases += _COMMANDS_AGGRESSIVE.get(language, [])
    if not phrases:
        return text
    phrases.sort(key=lambda p: len(p[0]), reverse=True)

    tokens = _tokenize(text)
    out, i = [], 0
    while i < len(tokens):
        chunk, is_word = tokens[i]
        if not is_word:
            out.append(chunk)
            i += 1
            continue
        hit = None
        for words, replacement in phrases:
            end = _match_phrase(tokens, i, words)
            if end is not None:
                hit = (end, replacement)
                break
        if hit:
            out.append(hit[1])
            i = hit[0]
        else:
            out.append(chunk)
            i += 1
    return "".join(out)


def _match_phrase(tokens, start, words):
    """End index if `words` sits at `start`, else None. Spaces only between."""
    i, w = start, 0
    while w < len(words):
        if i >= len(tokens):
            return None
        text, is_word = tokens[i]
        if not is_word:
            if not _is_plain_space(text):
                return None
            i += 1
            continue
        if _norm(text) != words[w]:
            return None
        i += 1
        w += 1
    return i


# ── Spacing and capitals ──────────────────────────────────────────────────────
_SPACE_BEFORE = re.compile(r"[ \t]+([,.;:!?%)\]])")
_SPACE_AFTER_OPEN = re.compile(r"([(\[¿¡])[ \t]+")
_MISSING_SPACE = re.compile(r"([,;:])(?=[^\s\d])")
_RUNS = re.compile(r"[ \t]{2,}")
# The optional group is the opening mark: in "¿cómo estás?" the first letter is
# not the first character, and without this it was never capitalised.
_SENTENCE_START = re.compile(r"(^|[.!?\n]\s*)([¿¡]?)([a-záéíóúñü])")


def fix_caps_and_spacing(text, language="es"):
    """Tidy up after the substitutions above, then fix the obvious capitals.

    Inserting "," between two tokens leaves "hola , que"; inserting a newline
    leaves a space in front of it. None of that is visible while writing the
    substitutions and all of it is visible in the pasted result.
    """
    text = _SPACE_BEFORE.sub(r"\1", text)
    text = _SPACE_AFTER_OPEN.sub(r"\1", text)
    text = _MISSING_SPACE.sub(r"\1 ", text)
    text = _RUNS.sub(" ", text)
    # A newline that picked up spaces on either side.
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    # "bryan arroba ejemplo" leaves "bryan @ ejemplo"; an address has no spaces
    # in it, and neither does an underscore joining two words.
    text = re.sub(r"[ \t]*([@_])[ \t]*", r"\1", text)
    text = _SENTENCE_START.sub(
        lambda m: m.group(1) + m.group(2) + m.group(3).upper(), text)
    if language == "es":
        text = _open_spanish_marks(text)
    return text.strip()


_SENTENCES = re.compile(r"[^.!?\n]+[.!?]*", re.UNICODE)


def _open_spanish_marks(text):
    """Add the opening ¿ and ¡ that Whisper leaves off.

    Spanish needs both marks and Whisper supplies the closing one perhaps half
    the time. This looks at each sentence, and where it ends in ? or ! without an
    opener, puts one at the front. It only ever adds a character, so the worst
    case is an inverted mark on a sentence that reads as a statement -- which is
    what the speaker's own intonation asked for anyway.
    """
    out, pos = [], 0
    for m in _SENTENCES.finditer(text):
        # Everything between the last sentence and this one -- newlines, mostly.
        # An earlier version rebuilt the string from the matches alone and threw
        # away every paragraph break in it.
        out.append(text[pos:m.start()])
        pos = m.end()
        s = m.group(0)
        stripped = s.strip()
        if not stripped:
            out.append(s)
            continue
        for close, open_ in (("?", "¿"), ("!", "¡")):
            if stripped.endswith(close) and open_ not in stripped:
                lead = s[:len(s) - len(s.lstrip())]
                s = lead + open_ + stripped
                break
        out.append(s)
    out.append(text[pos:])
    return "".join(out)


# ── The one function kara.py calls ────────────────────────────────────────────
def polish(text, language="es", numbers=True, voice_commands="safe"):
    """Everything above, in the order that works.

    voice_commands is "off", "safe" (the multi-word phrases) or "all" (adds the
    one-word ones, which will occasionally eat a real word).
    """
    if not text:
        return text
    if voice_commands != "off":
        text = spoken_commands(text, language, aggressive=(voice_commands == "all"))
    if numbers:
        text = words_to_digits(text, language)
    return fix_caps_and_spacing(text, language)
