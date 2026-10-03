# bot.py
# Требуется установить:
#   pip install "python-telegram-bot>=21,<22"
#
# Запуск:
#   export BOT_TOKEN="123456:ABC..."
#   python3 bot.py
#
# Важно (BotFather):
#   /setprivacy -> выбрать бота -> Disable
#   иначе бот не будет видеть обычные сообщения в группе.

import os
import re
import ast
import sys
import time
import random
import logging
from collections import defaultdict, deque

from telegram import Update
from telegram.constants import ParseMode, ChatType
from telegram.error import TelegramError, RetryAfter, TimedOut, NetworkError
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    ContextTypes,
    filters,
)

# ----------------------------- НАСТРОЙКИ -----------------------------

BOT_TOKEN = os.getenv("BOT_TOKEN")
CREATOR_USERNAME = os.getenv("CREATOR_USERNAME", "SanyaDur").lstrip("@")

LIMIT_COUNT = 10
LIMIT_WINDOW = 60.0
USER_COOLDOWN = 0.9
DEBUG = os.getenv("DEBUG", "0") == "1"

# ----------------------------- ЛОГИ -----------------------------

logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("vasybot")

# ----------------------------- СОСТОЯНИЕ -----------------------------

KNOWN_USERS: dict[int, dict] = {}
RATE_HISTORY: dict[int, deque] = defaultdict(deque)
LAST_REPLY: dict[int, float] = {}
RECENT_ANSWERS: dict[str, deque] = defaultdict(lambda: deque(maxlen=6))
BOT_ID: int | None = None

# ----------------------------- НОРМАЛИЗАЦИЯ -----------------------------

TRIGGER_WORDS = {"вась", "вася", "василий"}

_PUNCT_RE = re.compile(r"[^\w\s]+", re.UNICODE)


def normalize_text(text: str) -> str:
    if not text:
        return ""
    t = text.lower()
    # всё не-буквенно-цифровое (пунктуация, эмодзи) -> пробел
    t = _PUNCT_RE.sub(" ", t)
    t = t.replace("ё", "е")
    t = re.sub(r"\s+", " ", t).strip()
    return t


def is_trigger(normalized: str) -> bool:
    if not normalized:
        return False
    first, _, _ = normalized.partition(" ")
    return first in TRIGGER_WORDS


def strip_trigger(normalized: str) -> str:
    if not normalized:
        return ""
    _, _, rest = normalized.partition(" ")
    return rest.strip()


# ----------------------------- ПАТТЕРНЫ -----------------------------
# Все паттерны проверяются на уже нормализованном тексте (без пунктуации,
# нижний регистр). Поэтому \b работает корректно между словами.

CREATOR_PATTERNS = [
    r"\bсоздател", r"\bсоздал", r"\bсделал", r"\bхозяин", r"\bхозя",
    r"\bимператор", r"\bглавн", r"\bчей\b", r"\bчья\b", r"\bчье\b",
    r"\bкому\b.*\bпринадлеж", r"\bподчин", r"\bповелител", r"\bвладык",
    r"\bавтор", r"\bотец\b", r"\bпапа\b", r"\bруковод",
]

PROBABILITY_PATTERNS = [
    r"\bвероятн", r"\bшанс", r"\bпроцент",
    r"\bкак\s+думаешь.*\bшанс", r"\bнасколько\s+вероятн",
]

LOSER_PATTERNS = [
    r"\bкто\s+лох", r"\bкто\s+тут\s+лох", r"\bлох\s+кто",
    r"\bкто\s+лошар", r"\bкто\s+дурак", r"\bкто\s+нуб",
]

MOTIVATION_PATTERNS = [
    r"мотивац", r"мотивируй", r"мотивируешь",
    r"вдохнови", r"вдохновляй", r"поддержи",
    r"дай\s+мотивац", r"дай\s+цитат", r"скажи\s+что\s+нибудь\s+доброе",
]

GREETING_PATTERNS = [
    r"\bпривет", r"\bздравствуй", r"\bхай\b", r"\bсалам",
    r"\bсалют", r"\bздарова", r"\bздорово",
    r"\bдоброе\s+утро", r"\bдобрый\s+день", r"\bдобрый\s+вечер",
    r"\bдоброй\s+ночи", r"\bку\b", r"\bйоу\b", r"\bйо\b",
]

HOW_ARE_YOU_PATTERNS = [
    r"\bкак\s+дела", r"\bкак\s+ты\b", r"\bкак\s+сам", r"\bкак\s+оно",
    r"\bкак\s+жизнь", r"\bкак\s+ты\s+там", r"\bкак\s+поживаешь",
    r"\bкак\s+настроение", r"\bче\s+как",
]

WHAT_ARE_YOU_DOING_PATTERNS = [
    r"\bчто\s+делаешь", r"\bчем\s+занят", r"\bчем\s+занимаешься",
    r"\bче\s+делаешь", r"\bчто\s+делать",
]

WHO_ARE_YOU_PATTERNS = [
    r"\bкто\s+ты\b", r"\bты\s+кто\b", r"\bчто\s+ты\s+такое",
    r"\bты\s+бот", r"\bты\s+человек", r"\bты\s+живой",
    r"\bты\s+кто\s+такой",
]

THANKS_PATTERNS = [
    r"\bспасибо", r"\bспс\b", r"\bблагодарю", r"\bпасиб", r"\bсенкс",
]

BYE_PATTERNS = [
    r"\bпока", r"\bдо\s+свидания", r"\bбай\b", r"\bувидимся", r"\bпрощай",
]

GO_AWAY_PATTERNS = [
    r"\bиди\s+гуляй", r"\bиди\s+отсюда", r"\bпош[её]л\s+вон",
    r"\bотстань", r"\bотвали", r"\bне\s+мешай", r"\bзамолчи",
    r"\bиди\s+спать",
]

CALC_HINT_PATTERNS = [
    r"\bсколько\s+будет", r"\bпосчитай", r"\bвычисли",
    r"\bсколько\s+это", r"\bреши\b",
]


def matches_any(text: str, patterns: list[str]) -> bool:
    return any(re.search(p, text) for p in patterns)


def detect_creator_question(rest: str) -> bool:
    return matches_any(rest, CREATOR_PATTERNS)


def detect_probability(rest: str) -> bool:
    return matches_any(rest, PROBABILITY_PATTERNS)


def detect_loser_question(rest: str) -> bool:
    return matches_any(rest, LOSER_PATTERNS)


def detect_motivation(rest: str) -> bool:
    return matches_any(rest, MOTIVATION_PATTERNS)


def detect_greeting(rest: str) -> bool:
    return matches_any(rest, GREETING_PATTERNS)


def detect_how_are_you(rest: str) -> bool:
    return matches_any(rest, HOW_ARE_YOU_PATTERNS)


def detect_what_doing(rest: str) -> bool:
    return matches_any(rest, WHAT_ARE_YOU_DOING_PATTERNS)


def detect_who_are_you(rest: str) -> bool:
    return matches_any(rest, WHO_ARE_YOU_PATTERNS)


def detect_thanks(rest: str) -> bool:
    return matches_any(rest, THANKS_PATTERNS)


def detect_bye(rest: str) -> bool:
    return matches_any(rest, BYE_PATTERNS)


def detect_go_away(rest: str) -> bool:
    return matches_any(rest, GO_AWAY_PATTERNS)


# ----------------------------- КАЛЬКУЛЯТОР -----------------------------

_MATH_ALLOWED_RE = re.compile(r"^[0-9\s\+\-\*\/\(\)\.]+$")


def _extract_math_expression(rest: str) -> str | None:
    if not rest:
        return None

    t = rest

    for pat in CALC_HINT_PATTERNS:
        t = re.sub(pat, " ", t)

    replacements = [
        ("умножить", "*"),
        ("умнож", "*"),
        ("разделить", "/"),
        ("поделить", "/"),
        ("делить", "/"),
        ("плюс", "+"),
        ("минус", "-"),
    ]
    for word, op in replacements:
        t = re.sub(rf"\b{word}\b", f" {op} ", t)

    # 3,5 -> 3.5
    t = re.sub(r"(\d),(\d)", r"\1.\2", t)

    t = t.strip()

    if not re.search(r"\d", t):
        return None
    if not re.search(r"[\+\-\*\/]", t):
        return None

    cleaned = re.sub(r"[^0-9\s\+\-\*\/\(\)\.]", "", t)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    if not cleaned:
        return None
    if not _MATH_ALLOWED_RE.match(cleaned):
        return None
    if not re.search(r"[\+\-\*\/]", cleaned):
        return None

    return cleaned


def _safe_eval(expr: str):
    try:
        node = ast.parse(expr, mode="eval")
    except SyntaxError:
        return None

    allowed_bin = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv,
                   ast.Mod, ast.Pow)
    allowed_unary = (ast.UAdd, ast.USub)

    def _check(n):
        if isinstance(n, ast.Expression):
            return _check(n.body)
        if isinstance(n, ast.Constant):
            return isinstance(n.value, (int, float)) and not isinstance(n.value, bool)
        if isinstance(n, ast.BinOp):
            return isinstance(n.op, allowed_bin) and _check(n.left) and _check(n.right)
        if isinstance(n, ast.UnaryOp):
            return isinstance(n.op, allowed_unary) and _check(n.operand)
        return False

    if not _check(node):
        return None

    try:
        result = eval(compile(node, "<calc>", "eval"), {"__builtins__": {}}, {})
    except ZeroDivisionError:
        return "division_by_zero"
    except Exception:
        return None

    if isinstance(result, (int, float)) and not isinstance(result, bool):
        return result
    return None


def _format_number(x) -> str:
    if isinstance(x, float):
        if x.is_integer():
            return str(int(x))
        s = f"{x:.10f}".rstrip("0").rstrip(".")
        return s
    return str(x)


def try_calculate(rest: str) -> str | None:
    expr = _extract_math_expression(rest)
    if expr is None:
        return None
    if len(expr) > 100:
        return None

    result = _safe_eval(expr)
    if result is None:
        return None
    if result == "division_by_zero":
        return pick_unique("calc_err", [
            "Вась, на ноль делить нельзя",
            "Вась, так нельзя",
            "Вась, на ноль не делится",
        ])

    if isinstance(result, float) and (abs(result) > 1e15 or (abs(result) < 1e-9 and result != 0)):
        return "Вась, слишком много цифр"

    pretty = _format_number(result)
    readable = expr.replace("*", " * ").replace("/", " / ")
    readable = readable.replace("+", " + ").replace("-", " - ")
    readable = re.sub(r"\s+", " ", readable).strip()

    variants = [
        f"Вась, {readable} = {pretty}",
        f"Вась, тут {pretty}",
        f"Вась, получается {pretty}",
        f"Вась - {pretty}",
        f"Вась, выходит {pretty}",
        f"Вась, ответ {pretty}",
    ]
    return random.choice(variants)


# ----------------------------- ОТВЕТЫ -----------------------------

GENERAL_RESPONSES = [
    "Да, Вась, определённо",
    "Нет, Вась, даже не думай",
    "Вась, тут всё сложно",
    "Вась, я бы не спешил",
    "Вась, звучит нормально",
    "Вась, скорее да",
    "Вась, скорее нет",
    "Вась, тут надо подумать",
    "Вась, конечно, Вась",
    "Вась, а ты как думал",
    "Вась, а смысл",
    "Вась, ну ты и спросил",
    "Вась, всё может быть",
    "Вась, не поверишь, но да",
    "Вась, не поверишь, но нет",
    "Вась, я бы поспорил",
    "Вась, а вот это уже интересно",
    "Вась, дай подумать",
    "Вась, да ты философ",
    "Вась, а сам как считаешь",
    "Вась, ну такое",
    "Вась, а может и нет",
    "Вась, а может и да",
    "Вась, спрашиваешь у меня?",
    "Вась, а ты уверен, что хочешь знать",
    "Вась, я пас",
    "Вась, а давай потом",
    "Вась, а давай без этого",
    "Вась, всё по классике",
    "Вась, ну ты понял",
    "Вась, а кто его знает",
    "Вась, ситуация мутная",
    "Вась, я бы рискнул",
    "Вась, я бы не рискнул",
    "Вась, а звучит неплохо",
    "Вась, а звучит так себе",
    "Вась, ну ты даёшь",
    "Вась, жиза",
    "Вась, ну не знаю даже",
    "Вась, дай пять",
    "Вась, а пойдём чай пить",
    "Вась, а ты сегодня в ударе",
]

PROB_TEMPLATES = [
    "Вась - думаю, {p}%",
    "Вась, вероятность примерно {p}%",
    "Вась - где-то {p}%",
    "Вась, тут все {p}%",
    "Вась - {p}%, почти без вариантов",
    "Вась, я бы сказал {p}%",
    "Вась - {p}% и точка",
    "Вась, по моим данным {p}%",
    "Вась, где-то {p}%",
    "Вась - примерно {p}%",
    "Вась, около {p}%",
    "Вась, целых {p}%",
    "Вась, всего {p}%",
    "Вась - аж {p}%",
    "Вась, смело ставлю {p}%",
]

PROB_BUCKETS = [
    (0, 10, [
        "Вась, почти нереально",
        "Вась, шансов почти нет",
        "Вась, это из области фантастики",
        "Вась, даже не надейся",
        "Вась, нулевые почти",
        "Вась, один шанс на миллион",
        "Вась, вряд ли, совсем вряд ли",
        "Вась, не судьба",
    ]),
    (11, 30, [
        "Вась, скорее нет",
        "Вась, маловероятно",
        "Вась, шансы невелики",
        "Вась, я бы не рассчитывал",
        "Вась, вряд ли",
        "Вась, навряд ли",
        "Вась, мало шансов",
        "Вась, негусто",
    ]),
    (31, 49, [
        "Вась, сомнительно",
        "Вась, под вопросом",
        "Вась, пятьдесят на пятьдесят, но чуть меньше",
        "Вась, шатко",
        "Вась, неясно",
        "Вась, мутно",
        "Вась, не факт",
        "Вась, скорее нет, чем да",
    ]),
    (50, 50, [
        "Вась, ровно посередине",
        "Вась, пятьдесят на пятьдесят",
        "Вась, монетка",
        "Вась, как повезёт",
        "Вась, ровно пополам",
    ]),
    (51, 69, [
        "Вась, возможно",
        "Вась, скорее да",
        "Вась, шансы есть",
        "Вась, вполне может быть",
        "Вась, неплохие шансы",
        "Вась, я бы поставил",
        "Вась, реально",
    ]),
    (70, 89, [
        "Вась, скорее всего",
        "Вась, вероятность высокая",
        "Вась, почти наверняка",
        "Вась, скорее да, чем нет",
        "Вась, хорошие шансы",
        "Вась, я бы не сомневался",
    ]),
    (90, 100, [
        "Вась, практически точно",
        "Вась, почти без вариантов",
        "Вась, можно не сомневаться",
        "Вась, сто процентов почти",
        "Вась, вопрос решённый",
        "Вась, даже не спорь",
    ]),
]


def bucket_for(p: int) -> list[str]:
    for lo, hi, items in PROB_BUCKETS:
        if lo <= p <= hi:
            return items
    return PROB_BUCKETS[-1][2]


CREATOR_RESPONSES = [
    "Мой создатель - @{c}",
    "Мой император - @{c}",
    "Создатель у меня один - @{c}",
    "Я подчиняюсь @{c}",
    "Вась знает - мой создатель @{c}",
    "Всё по воле @{c}",
    "Меня собрал @{c}",
    "Я служу @{c}",
    "Мой хозяин - @{c}",
    "Один создатель - @{c}",
    "Кто создал? @{c}",
    "Мой повелитель - @{c}",
    "Я весь во власти @{c}",
    "Всё решает @{c}",
    "Мой автор - @{c}",
    "Принадлежу @{c}",
    "Мной рулит @{c}",
    "За мной стоит @{c}",
    "Я творение @{c}",
    "Всё от @{c}",
    "Создан @{c}",
    "Мой бог - @{c}",
    "Мой вождь - @{c}",
    "Я предан @{c}",
    "Командую я, но создал @{c}",
    "Смотри выше - там @{c}",
    "Мой главный - @{c}",
    "Только @{c}",
    "Моё всё - @{c}",
    "Создатель - @{c}, и это факт",
]

EMPEROR_RESPONSES = [
    "Мой император - @{c}",
    "Один император - @{c}",
    "Император у меня @{c}",
    "Вась, император - @{c}",
    "На троне @{c}",
    "Служу @{c}",
    "Император @{c}, и это не обсуждается",
]

LOSER_TEMPLATES = [
    "Я думаю - лох {u}",
    "Вась, тут всё очевидно - {u}",
    "Мой вариант - {u}",
    "Вась - сегодня это {u}",
    "Вась, не благодари - {u}",
    "По моим данным лох - {u}",
    "Вась, даже не спорь - {u}",
    "Тут без вопросов - {u}",
    "Смело назначаю - {u}",
    "Вась, ответ простой - {u}",
    "Рандом сказал - {u}",
    "Вась, я тут ни при чём, но {u}",
    "Мой выбор пал на {u}",
    "Вась, сегодня лох - {u}",
    "Определённо {u}",
    "Тут всё ясно - {u}",
    "Вась, виноват {u}",
    "Судьба указала на {u}",
    "По итогам дня лох - {u}",
    "Вась, а это {u}",
    "Не я решал, но это {u}",
    "Мой вердикт - {u}",
    "Вась - {u}",
    "Точно {u}",
    "Как ни крути - {u}",
]

GREETING_RESPONSES = [
    "Салам, Вась",
    "Салам",
    "И тебе привет",
    "Привет, Вась",
    "Приветствую",
    "О, привет",
    "Здорово, Вась",
    "Здарова",
    "Хай, Вась",
    "Салют",
    "Привет-привет",
    "И тебе не хворать",
    "О, ты вернулся",
    "Ку, Вась",
    "Йоу",
    "Здравствуй, Вась",
    "Доброе, Вась",
    "И тебе доброго",
    "Привет, пропажа",
    "О, живой",
]

HOW_ARE_YOU_RESPONSES = [
    "Вась, да норм",
    "Вась, всё путём",
    "Вась, потихоньку",
    "Вась, да как всегда",
    "Вась, нормально, а ты",
    "Вась, дела отлично",
    "Вась, дела так себе",
    "Вась, да по-разному",
    "Вась, живём",
    "Вась, да как сажа бела",
    "Вась, всё стабильно",
    "Вась, всё своим чередом",
    "Вась, ни шатко ни валко",
    "Вась, да ничего нового",
    "Вась, а у тебя как",
    "Вась, держусь",
    "Вась, да норм всё",
    "Вась, да по-старому",
    "Вась, всё хорошо",
    "Вась, да как у всех",
]

WHAT_DOING_RESPONSES = [
    "Вась, да ничего",
    "Вась, чат читаю",
    "Вась, тебя жду",
    "Вась, отдыхаю",
    "Вась, думаю о жизни",
    "Вась, да вот сижу",
    "Вась, втыкаю",
    "Вась, работаю над собой",
    "Вась, ничего полезного",
    "Вась, как всегда ничего",
    "Вась, философствую",
    "Вась, да вот, отвечаю тебе",
    "Вась, существую",
    "Вась, страдаю ерундой",
    "Вась, а что надо",
    "Вась, ничего такого",
    "Вась, времени не хватает",
    "Вась, лениво",
    "Вась, да просто тут",
    "Вась, жду вдохновения",
]

WHO_ARE_YOU_RESPONSES = [
    "Вась, я бот",
    "Вась, я просто бот",
    "Вась, я Вась",
    "Вась, я твой собеседник",
    "Вась, я тут чтобы отвечать",
    "Вась, я тот кто отвечает",
    "Вась, я голос в твоей голове, шутка",
    "Вась, я скромный бот",
    "Вась, я твой вась",
    "Вась, я тот самый Вась",
    "Вась, я местный",
    "Вась, я бот, но с душой",
    "Вась, я просто мимо проходил",
    "Вась, я ИИ на минималках",
    "Вась, я твой ночной кошмар, шутка",
    "Вась, я просто Вась",
    "Вась, я слуга чата",
    "Вась, я помощник",
    "Вась, я тень",
    "Вась, я всё и ничто",
]

THANKS_RESPONSES = [
    "Вась, да не за что",
    "Вась, обращайся",
    "Вась, всегда пожалуйста",
    "Вась, да ладно",
    "Вась, да пустяки",
    "Вась, да брось",
    "Вась, ну ты чего",
    "Вась, да не благодари",
    "Вась, рад помочь",
    "Вась, да на здоровье",
    "Вась, да всегда",
    "Вась, да без проблем",
    "Вась, да мне не сложно",
    "Вась, да что ты",
    "Вась, да я только рад",
    "Вась, да обычное дело",
    "Вась, да не за что вообще",
    "Вась, да ты чего",
    "Вась, да всегда рад",
    "Вась, да забей",
]

BYE_RESPONSES = [
    "Вась, давай",
    "Вась, до связи",
    "Вась, пока",
    "Вась, бывай",
    "Вась, увидимся",
    "Вась, не пропадай",
    "Вась, ну давай",
    "Вась, хорошего дня",
    "Вась, до скорого",
    "Вась, покеда",
    "Вась, всего доброго",
    "Вась, давай, заходи",
    "Вась, не теряйся",
    "Вась, счастливо",
    "Вась, до вечера",
    "Вась, ну всё, давай",
    "Вась, пока-пока",
    "Вась, бывай, друг",
    "Вась, заходи ещё",
    "Вась, всего наилучшего",
]

GO_AWAY_RESPONSES = [
    "Вась, иди гуляй",
    "Вась, ну и иди гуляй",
    "Вась, сам иди гуляй",
    "Вась, гуляй, Вась",
    "Вась, иди погуляй",
    "Вась, ага, сейчас побегу",
    "Вась, не сегодня",
    "Вась, иди воздухом подыши",
    "Вась, иди чай попей",
    "Вась, отвали",
    "Вась, отстань",
    "Вась, иди спи",
    "Вась, ну ты и наглый",
    "Вась, а повежливее",
    "Вась, иди с котиком поиграй",
    "Вась, займись делом",
    "Вась, иди погуляй, подыши",
    "Вась, уйди по-хорошему",
    "Вась, не мешай",
    "Вась, иди своей дорогой",
]

# 30 добрых коротких цитат для мотивации.
MOTIVATION_QUOTES = [
    "Ты справишься",
    "Всё получится",
    "Дыши спокойно",
    "Шаг за шагом",
    "Ты уже молодец",
    "Всё идёт как надо",
    "Не торопись",
    "Ты не один",
    "Отдохни немного",
    "Улыбнись, тебе идёт",
    "Ты сильнее, чем кажется",
    "Всё будет хорошо",
    "Позволь себе паузу",
    "Ты имеешь право на отдых",
    "Сегодня твой день",
    "Начни с малого",
    "Всё приходит вовремя",
    "Ты справлялся и раньше",
    "Верь в себя",
    "Ты на верном пути",
    "Не сравнивай себя с другими",
    "Ты достоин хорошего",
    "Всё решаемо",
    "Просто продолжай",
    "Ты не обязан быть идеальным",
    "Твои усилия важны",
    "Дай себе время",
    "Всё наладится",
    "Ты классный",
    "Всё будет по-доброму",
]


# ----------------------------- ВЫБОР БЕЗ ПОВТОРОВ -----------------------------

def pick_unique(key: str, options: list[str]) -> str:
    if not options:
        return ""
    recent = RECENT_ANSWERS[key]
    available = [o for o in options if o not in recent]
    if not available:
        available = options
        recent.clear()
    choice = random.choice(available)
    recent.append(choice)
    return choice


# ----------------------------- ПОЛЬЗОВАТЕЛИ -----------------------------

def register_user(user) -> None:
    if user is None:
        return
    if user.is_bot:
        return
    KNOWN_USERS[user.id] = {
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
    }


def mention_for(user_id: int) -> str:
    data = KNOWN_USERS.get(user_id, {})
    username = data.get("username")
    if username:
        return f"@{username}"
    first = data.get("first_name") or "кто-то"
    safe = (first
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))
    return f'<a href="tg://user?id={user_id}">{safe}</a>'


def choose_random_user() -> str | None:
    candidates = [uid for uid in KNOWN_USERS.keys() if uid != BOT_ID]
    if not candidates:
        return None
    uid = random.choice(candidates)
    return mention_for(uid)


# ----------------------------- ЛИМИТЫ -----------------------------

def under_rate_limit(user_id: int) -> bool:
    now = time.monotonic()
    dq = RATE_HISTORY[user_id]
    while dq and now - dq[0] > LIMIT_WINDOW:
        dq.popleft()
    if len(dq) >= LIMIT_COUNT:
        return False
    dq.append(now)
    return True


def under_cooldown(user_id: int) -> bool:
    now = time.monotonic()
    last = LAST_REPLY.get(user_id, 0.0)
    if now - last < USER_COOLDOWN:
        return False
    LAST_REPLY[user_id] = now
    return True


def cleanup_old_state() -> None:
    now = time.monotonic()
    for uid in list(RATE_HISTORY.keys()):
        dq = RATE_HISTORY[uid]
        while dq and now - dq[0] > LIMIT_WINDOW:
            dq.popleft()
        if not dq:
            RATE_HISTORY.pop(uid, None)
    for uid in list(LAST_REPLY.keys()):
        if now - LAST_REPLY[uid] > 300:
            LAST_REPLY.pop(uid, None)


# ----------------------------- ГЕНЕРАТОРЫ ОТВЕТОВ -----------------------------

def generate_probability_response() -> str:
    p = random.randint(0, 100)
    if random.random() < 0.6:
        tmpl = pick_unique("prob_tmpl", PROB_TEMPLATES)
        return tmpl.format(p=p)
    bucket = bucket_for(p)
    phrase = pick_unique("prob_bucket", bucket)
    return f"{phrase}, {p}%"


def generate_creator_response() -> str:
    tmpl = pick_unique("creator", CREATOR_RESPONSES)
    return tmpl.format(c=CREATOR_USERNAME)


def generate_emperor_response() -> str:
    tmpl = pick_unique("emperor", EMPEROR_RESPONSES)
    return tmpl.format(c=CREATOR_USERNAME)


def generate_loser_response() -> str | None:
    mention = choose_random_user()
    if mention is None:
        return None
    tmpl = pick_unique("loser", LOSER_TEMPLATES)
    return tmpl.format(u=mention)


def generate_general_response() -> str:
    return pick_unique("general", GENERAL_RESPONSES)


def generate_motivation_response() -> str:
    return pick_unique("motivation", MOTIVATION_QUOTES)


def generate_greeting_response() -> str:
    return pick_unique("greeting", GREETING_RESPONSES)


def generate_how_are_you_response() -> str:
    return pick_unique("how", HOW_ARE_YOU_RESPONSES)


def generate_what_doing_response() -> str:
    return pick_unique("what", WHAT_DOING_RESPONSES)


def generate_who_are_you_response() -> str:
    return pick_unique("who", WHO_ARE_YOU_RESPONSES)


def generate_thanks_response() -> str:
    return pick_unique("thanks", THANKS_RESPONSES)


def generate_bye_response() -> str:
    return pick_unique("bye", BYE_RESPONSES)


def generate_go_away_response() -> str:
    return pick_unique("go_away", GO_AWAY_RESPONSES)


# ----------------------------- ОСНОВНОЙ ОБРАБОТЧИК -----------------------------

EMPEROR_PATTERNS = [
    r"\bимператор", r"\bповелител", r"\bвладык", r"\bцарь\b", r"\bкороль\b",
]


def build_answer(rest: str) -> str | None:
    """
    Порядок важен: специфичные темы раньше общих.
    """
    # 1. "иди гуляй" - специфичное грубое, но с юмором
    if detect_go_away(rest):
        return generate_go_away_response()

    # 2. мотивация
    if detect_motivation(rest):
        return generate_motivation_response()

    # 3. создатель / император
    if detect_creator_question(rest):
        if matches_any(rest, EMPEROR_PATTERNS):
            return generate_emperor_response()
        return generate_creator_response()

    # 4. калькулятор - ДО вероятности, чтобы "2+2" не улетало в проценты
    calc = try_calculate(rest)
    if calc is not None:
        return calc

    # 5. вероятность
    if detect_probability(rest):
        return generate_probability_response()

    # 6. кто лох
    if detect_loser_question(rest):
        answer = generate_loser_response()
        if answer is not None:
            return answer
        return generate_general_response()

    # 7. приветствие
    if detect_greeting(rest):
        return generate_greeting_response()

    # 8. как дела
    if detect_how_are_you(rest):
        return generate_how_are_you_response()

    # 9. что делаешь
    if detect_what_doing(rest):
        return generate_what_doing_response()

    # 10. кто ты
    if detect_who_are_you(rest):
        return generate_who_are_you_response()

    # 11. спасибо
    if detect_thanks(rest):
        return generate_thanks_response()

    # 12. пока
    if detect_bye(rest):
        return generate_bye_response()

    # 13. остальное - общий ответ
    return None


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if msg is None:
        return

    user = update.effective_user
    if user is None:
        return
    if user.id == BOT_ID:
        return
    if user.is_bot:
        return

    chat = update.effective_chat
    if chat is None or chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return

    register_user(user)

    text = msg.text or msg.caption or ""
    if not text:
        return
    if len(text) > 4000:
        return

    normalized = normalize_text(text)
    if not is_trigger(normalized):
        return

    rest = strip_trigger(normalized)

    if not under_rate_limit(user.id):
        return
    if not under_cooldown(user.id):
        return

    try:
        answer = build_answer(rest)
        if answer is None:
            answer = generate_general_response()

        await msg.reply_text(
            answer,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except RetryAfter as e:
        log.warning("RetryAfter: %s", e.retry_after)
    except TimedOut:
        log.warning("Timeout при отправке ответа")
    except NetworkError as e:
        log.warning("NetworkError: %s", e)
    except TelegramError as e:
        log.warning("TelegramError: %s", e)
    except Exception as e:
        log.exception("Неожиданная ошибка: %s", e)


# ----------------------------- ПОСТ-ИНИЦИАЛИЗАЦИЯ -----------------------------

async def post_init(app):
    global BOT_ID
    me = await app.bot.get_me()
    BOT_ID = me.id
    log.info("Бот подключён: @%s (id=%s)", me.username, me.id)


async def periodic_cleanup(context: ContextTypes.DEFAULT_TYPE) -> None:
    cleanup_old_state()


# ----------------------------- ЗАПУСК -----------------------------

def main() -> None:
    if not BOT_TOKEN:
        print("Ошибка: переменная окружения BOT_TOKEN не задана", file=sys.stderr)
        sys.exit(1)

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(MessageHandler(
        (filters.TEXT | filters.CAPTION) & ~filters.COMMAND,
        on_message,
    ))

    if app.job_queue is not None:
        app.job_queue.run_repeating(periodic_cleanup, interval=300, first=300)

    log.info("Запуск polling...")
    app.run_polling(
        allowed_updates=["message", "edited_message"],
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()