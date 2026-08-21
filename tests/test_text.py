"""Tests for karatext.

The cases that matter most are the ones two beta testers ran into by accident,
and the ones a Spanish speaker would run into the moment the feature shipped: the
words "punto" and "coma" mean something on their own, and a dictation tool that
eats them is worse than one that never tried.

    py -3 -m pytest tests/test_text.py -q
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from karatext import (fix_caps_and_spacing, polish, spoken_commands,
                      style_prompt, words_to_digits)


# ── Numbers: what Felipe asked for ────────────────────────────────────────────
@pytest.mark.parametrize("said, typed", [
    ("mil doscientos", "1200"),
    ("dos mil", "2000"),
    ("veinticinco", "25"),
    ("veintidos", "22"),
    ("veintidós", "22"),                      # same word, accent or not
    ("cuarenta y cinco", "45"),
    ("ciento veinte", "120"),
    ("doscientos mil", "200.000"),
    ("un millón doscientos mil", "1.200.000"),
    ("dos millones", "2.000.000"),
    ("cero", "0"),
    ("quince", "15"),
    ("noventa y nueve", "99"),
    ("tres mil quinientos", "3500"),
])
def test_spanish_cardinals(said, typed):
    assert words_to_digits(said, "es") == typed


def test_four_digit_numbers_are_not_grouped():
    """Because most of them are years.

    There is no way to tell "mil novecientos noventa y ocho" the year from the
    same words meaning the quantity, so one of the two has to look slightly off.
    A year written 1.998 reads as a typo; a price written 1200 reads fine. Above
    ten thousand the ambiguity is gone and the separator comes back.
    """
    assert words_to_digits("mil novecientos noventa y ocho", "es") == "1998"
    assert words_to_digits("doscientos mil", "es") == "200.000"


@pytest.mark.parametrize("said, typed", [
    ("mil doscientos pesos", "1200 pesos"),
    ("son las tres cuarenta y cinco", "son las 3:45"),
    ("nos vemos a las ocho treinta", "nos vemos a las 8:30"),
    ("tres coma cinco", "3,5"),
    ("un veinte por ciento", "un 20%"),
    ("cincuenta por ciento", "50%"),
])
def test_spanish_numbers_in_sentences(said, typed):
    assert words_to_digits(said, "es") == typed


@pytest.mark.parametrize("said", [
    "uno nunca sabe",              # pronoun
    "un café por favor",           # article
    "una cosa más",                # article
    "es una de esas cosas",
])
def test_bare_un_una_uno_are_left_alone(said):
    assert words_to_digits(said, "es") == said


def test_un_still_counts_inside_a_longer_number():
    assert words_to_digits("un millón de pesos", "es") == "1.000.000 de pesos"
    assert words_to_digits("un millón doscientos mil", "es") == "1.200.000"
    assert words_to_digits("veintiún mil", "es") == "21.000"


def test_y_between_two_separate_numbers_is_not_a_connector():
    # "tres y cuatro" is two numbers with a conjunction, not thirty-four.
    assert words_to_digits("tres y cuatro", "es") == "3 y 4"


def test_a_comma_breaks_a_number():
    # The speaker stopped. "treinta, cinco" is not 35.
    assert words_to_digits("treinta, cinco", "es") == "30, 5"


def test_minutes_above_fifty_nine_are_not_a_clock():
    assert words_to_digits("las tres ochenta", "es") == "las 3 80"


@pytest.mark.parametrize("said, typed", [
    ("twenty five", "25"),
    ("one hundred and twenty", "120"),
    ("one thousand two hundred", "1200"),
    ("three point five", "3.5"),
    ("fifty percent", "50%"),
])
def test_english_cardinals(said, typed):
    assert words_to_digits(said, "en") == typed


def test_a_lone_dos_is_still_a_number_and_that_is_asymmetric():
    """Known, and accepted.

    "uno" is protected because it is also a pronoun; "dos" has no such second
    life, so a list dictated out loud comes back half words and half digits --
    "uno" and then "2". Protecting every small number would give "tengo dos
    hijos" back instead of "tengo 2 hijos", which is by far the more common
    sentence. The asymmetry is the cheaper of the two costs, and it is written
    down here so the next person to notice it knows it was a decision.
    """
    assert words_to_digits("uno", "es") == "uno"
    assert words_to_digits("dos", "es") == "2"


def test_bare_one_is_left_alone_in_english():
    assert words_to_digits("one of them left", "en") == "one of them left"


def test_unknown_language_is_untouched():
    assert words_to_digits("vingt cinq", "fr") == "vingt cinq"


# ── Spoken punctuation: what Mape ran into ────────────────────────────────────
@pytest.mark.parametrize("said, typed", [
    ("hola punto y aparte adiós", "hola \n\n adiós"),
    ("uno nueva línea dos", "uno \n dos"),
    ("abre paréntesis nota cierra paréntesis", "( nota )"),
    ("dime signo de interrogación", "dime ?"),
    ("bryan arroba ejemplo", "bryan @ ejemplo"),
])
def test_spanish_multiword_commands(said, typed):
    """Substitution only. The stray spaces are fix_caps_and_spacing's job, which
    is why polish() runs them in that order and why the next test exists."""
    assert spoken_commands(said, "es") == typed


@pytest.mark.parametrize("said, typed", [
    ("hola punto y aparte adiós", "Hola\n\nAdiós"),
    ("hola nueva línea chao", "Hola\nChao"),
    ("abre paréntesis nota cierra paréntesis", "(nota)"),
    ("dime signo de interrogación", "¿Dime?"),
    ("escríbeme a bryan arroba ejemplo", "Escríbeme a bryan@ejemplo"),
])
def test_commands_through_the_whole_pipeline(said, typed):
    assert polish(said, "es") == typed


def test_punto_y_coma_beats_punto():
    # Longest phrase wins, or this would come out as ". y coma".
    assert spoken_commands("a punto y coma b", "es", aggressive=True) == "a ; b"


@pytest.mark.parametrize("said", [
    "ese es el punto de partida",
    "la coma va acá",
    "hay que llegar al punto medio",
    "se me fue la coma otra vez",
])
def test_real_words_survive_the_safe_list(said):
    # The whole reason the one-word commands are off by default.
    assert spoken_commands(said, "es") == said


def test_aggressive_mode_does_eat_them_and_that_is_the_deal():
    got = spoken_commands("la coma va acá", "es", aggressive=True)
    assert got == "la , va acá"


def test_english_commands():
    assert polish("hello new paragraph goodbye", "en") == "Hello\n\nGoodbye"


# ── Spacing and capitals ──────────────────────────────────────────────────────
def test_spacing_around_inserted_punctuation():
    assert fix_caps_and_spacing("hola , qué tal", "es") == "Hola, qué tal"


def test_sentences_are_capitalised():
    assert fix_caps_and_spacing("hola. qué tal. bien", "es") == "Hola. Qué tal. Bien"


def test_spanish_opening_marks_are_added():
    assert fix_caps_and_spacing("cómo estás?", "es") == "¿Cómo estás?"
    assert fix_caps_and_spacing("qué bueno!", "es") == "¡Qué bueno!"


def test_existing_opening_mark_is_not_doubled():
    assert fix_caps_and_spacing("¿cómo estás?", "es") == "¿Cómo estás?"


def test_english_gets_no_inverted_marks():
    assert fix_caps_and_spacing("how are you?", "en") == "How are you?"


# ── The whole pipeline ────────────────────────────────────────────────────────
def test_polish_end_to_end():
    said = "te debo mil doscientos pesos punto y aparte te pago el viernes"
    assert polish(said, "es") == "Te debo 1200 pesos\n\nTe pago el viernes"


def test_polish_leaves_ordinary_speech_alone():
    said = "Ese es el punto de partida, y uno nunca sabe."
    assert polish(said, "es") == said


def test_polish_can_be_turned_off():
    said = "mil doscientos punto y aparte listo"
    assert polish(said, "es", numbers=False, voice_commands="off") == \
        "Mil doscientos punto y aparte listo"


def test_polish_handles_empty():
    assert polish("", "es") == ""
    assert polish(None, "es") is None


# ── The prompt ────────────────────────────────────────────────────────────────
def test_style_prompt_shows_the_formatting_we_want_back():
    p = style_prompt("es")
    assert "¿" in p and "," in p and "%" in p and any(c.isdigit() for c in p)
    assert style_prompt("fr") is None
