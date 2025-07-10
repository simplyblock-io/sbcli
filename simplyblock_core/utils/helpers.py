def ensure_singleton(xs):
    """Unwraps a value of a collection of one

    If the collection is empty or holds more than one values, a ValueError is thrown.
    """
    it = iter(xs)
    try:
        x = next(it)
    except StopIteration:
        raise ValueError('Collection is empty')

    try:
        next(it)
        raise ValueError('Collection has multiple values')
    except StopIteration:
        return x
