"""C4/I8; R3/R12: consumption errors explain an answer contract without exposing code."""


class Mismatch(ValueError):
    def __init__(self, field, reason):
        self.field, self.reason = field, reason
        super().__init__(reason)


class ConsumptionFailure(ValueError):
    """The same question could not be consumed, even after its exact correction."""

