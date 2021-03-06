from itertools import tee

_nothing = 'NOTHING!!@#!#!@'


def only_one(iterable, default=_nothing, sentinel=_nothing):
    '''
    Return the first item from iterable, if and only if iterable contains a
    single element.  Raises ValueError if iterable contains more than a
    single element.  If iterable is empty, then return default value, if
    provided.  Otherwise raises ValueError.
    '''
    it = iter(iterable)

    try:
        item = next(it)
    except StopIteration:
        if default is not _nothing:
            return default
        raise ValueError('zero length sequence')

    try:
        next(it)
        if sentinel is not _nothing:
            return sentinel
        raise ValueError('there can be only one')
    except StopIteration:
        return item


# def ilen(iterable):
#     """Return the number of items in ``iterable``."""
#     return sum(1 for _ in iterable)


def split_on_condition(condition, seq):
    """return two generators, elements in seq that pass condition and elements in seq that do not pass the condition"""
    l1, l2 = tee((condition(item), item) for item in seq)
    return (i for p, i in l1 if p), (i for p, i in l2 if not p)


def partition(predicate, items):
    """
    Partition items into two generators, one who's predicate returns True and the other who's predicate returns False
    :param predicate: a function
    :param items: an iterable
    :return: two generators
    """
    a, b = tee((predicate(item), item) for item in items)
    return ((item for pred, item in b if pred), (item for pred, item in a if not pred))
