from app.core import increment
from app.nested.scale import scale


def combine(value):
    return scale(increment(value))
