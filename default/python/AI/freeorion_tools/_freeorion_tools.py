# This Python file uses the following encoding: utf-8
import cProfile, pstats, StringIO
import re
import logging
import sys
from functools import wraps

import freeOrionAIInterface as fo  # pylint: disable=import-error


# color wrappers for chat:
RED = '<rgba 255 0 0 255>%s</rgba>'
WHITE = '<rgba 255 255 255 255>%s</rgba>'


def dict_from_map(thismap):
    """Convert C++ map to python dict."""
    return {el.key(): el.data() for el in thismap}


def get_ai_tag_grade(tag_list, tag_type):
    """
    Accepts a list of string tags and a tag_type (like 'WEAPONS').
    Checks for the first tag in the list (if any), for tag_type "TYPE",
    having the structure X_TYPE
    and then returns 'X'
    X is most commonly (but not necessarily) one of [NO, BAD, AVERAGE, GOOD, GREAT, ULTIMATE]
    If no matching tags, returns empty string (which for most types should be considered equivalent to AVERAGE)
    """
    for tag in [tag for tag in tag_list if tag.count("_") > 0]:
        parts = tag.split("_", 1)
        if parts[1] == tag_type.upper():
            return parts[0]
    return ""


def UserString(label, default=None):  # this name left with C naming style for compatibility with translation assistance procedures  #pylint: disable=invalid-name
    """
    A translation assistance tool is intended to search for this method to identify translatable strings.

    :param label: a UserString key
    :param default: a default value to return if there is a key error
    :return: a translated string for the label
    """

    table_string = fo.userString(label)

    if "ERROR: " + label in table_string:  # implement test for string lookup not found error
        return default or table_string
    else:
        return table_string


def UserStringList(label):  # this name left with C naming style for compatibility with translation assistance procedures  #pylint: disable=invalid-name
    """
    A translation assistance tool is intended to search for this method to identify translatable strings.

    :param label: a UserString key
    :return: a python list of translated strings from the UserString list identified by the label
    """

    return fo.userStringList(label)


def tech_is_complete(tech):
    """
    Return if tech is complete.
    """
    return fo.getEmpire().getTechStatus(tech) == fo.techStatus.complete


def ppstring(foo):
    """
    Returns a string version of lists, dicts, sets, such that entries with special characters will be
    printed in legible string format rather than as hex escape characters, i.e.,
    ['Asimov α'] rather than ['Asimov \xce\xb1']."""

    if isinstance(foo, list):
        return "[" + ",".join(map(ppstring, foo)) + "]"
    elif isinstance(foo, dict):
        return "{" + ",".join([ppstring(k) + ":" + ppstring(v) for k, v in foo.iteritems()]) + "}"
    elif isinstance(foo, tuple):
        return "(" + ",".join(map(ppstring, foo)) + ")"
    elif isinstance(foo, set) or isinstance(foo, frozenset):
        return "{" + ",".join(map(ppstring, foo)) + "}"
    elif isinstance(foo, str):
        return "'" + foo + "'"
    else:
        return str(foo)


def chat_on_error(function):
    @wraps(function)
    def wrapper(*args, **kw):
        try:
            return function(*args, **kw)
        except Exception as e:
            logging.getLogger().exception(e)
            raise
    return wrapper


class ConsoleLogHandler(logging.Handler):
    """A log handler to send errors to the console. """
    def emit(self, record):
        """Emit a record.

        If a formatter is specified, it is used to format the record and then sent to human players. """
        try:
            human_ids = [x for x in fo.allPlayerIDs() if fo.playerIsHost(x)]
            if not human_ids:
                return
            msg = self.format(record)

            for human_id in human_ids:
                fo.sendChatMessage(human_id, msg)
        except (KeyboardInterrupt, SystemExit):
            raise
        # Hide errors from within the ConsoleLogHandler
        except:
            self.handleError(record)

# Create the log handler, format it and attach it to the root logger
console_handler = ConsoleLogHandler()

console_handler.setFormatter(
    logging.Formatter(RED % ('%s : %%(filename)s:%%(funcName)s():%%(lineno)d  - %%(message)s'
                             % fo.userString('AI_ERROR_MSG'))))

console_handler.setLevel(logging.ERROR)

logging.getLogger().addHandler(console_handler)


def remove_tags(message):
    """Remove tags described in Font.h from message."""
    expr = '</?(i|u|(rgba ([0-1]\.)?\d+ ([0-1]\.)?\d+ ([0-1]\.)?\d+ ([0-1]\.)?\d+)|rgba|left|center|right|pre)>'
    return re.sub(expr, '', message)


def chat_human(message):
    """
    Send chat message to human and print it to log.
    Log message cleared form tags.
    """
    human_id = [x for x in fo.allPlayerIDs() if fo.playerIsHost(x)][0]
    fo.sendChatMessage(human_id, message)
    print "\nChat Message to human: %s" % remove_tags(message)


def cache_by_session(function):
    """
    Cache a function value by session.
    Wraps only functions with hashable arguments.
    """
    _cache = {}

    @wraps(function)
    def wrapper(*args, **kwargs):
        key = (function, args, tuple(kwargs.items()))
        if key in _cache:
            return _cache[key]
        res = function(*args, **kwargs)
        _cache[key] = res
        return res
    wrapper._cache = _cache
    return wrapper


def cache_by_turn(function):
    """
    Cache a function value by turn, stored in foAIstate so also provides a history that may be analysed. The cache
    is keyed by the original function name.  Wraps only functions without arguments.
    Cache result is stored in savegame, will crash with picle error if result contains any boost object.
    """
    # avoid circular import
    import FreeOrionAI as foAI

    @wraps(function)
    def wrapper():
        if foAI.foAIstate is None:
            return function()
        else:
            cache = foAI.foAIstate.misc.setdefault('caches', {}).setdefault(function.__name__, {})
            this_turn = fo.currentTurn()
            return cache[this_turn] if this_turn in cache else cache.setdefault(this_turn, function())
    return wrapper


def dict_to_tuple(dic):
    return tuple(dic.iteritems())


def tuple_to_dict(tup):
    try:
        return dict(tup)
    except TypeError:
        try:
            return {k: v for k, v in [tup]}
        except:
            print >> sys.stderr, "Can't convert tuple_list to dict: ", tup
            return {}


def profile(function):

    @wraps(function)
    def wrapper(*args, **kwargs):
        pr = cProfile.Profile()
        pr.enable()
        retval = function(*args, **kwargs)
        pr.disable()
        s = StringIO.StringIO()
        sortby = 'cumulative'
        ps = pstats.Stats(pr, stream=s).sort_stats(sortby)
        ps.print_stats()
        print s.getvalue()
        return retval

    return wrapper
