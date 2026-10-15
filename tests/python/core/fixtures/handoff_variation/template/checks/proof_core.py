import unittest

from app.core import increment


class Proof(unittest.TestCase):
    def test_requirement(self):
        self.assertEqual(increment(2), 3)
