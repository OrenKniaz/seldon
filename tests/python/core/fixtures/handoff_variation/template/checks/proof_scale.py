import unittest

from app.nested.scale import scale


class Proof(unittest.TestCase):
    def test_requirement(self):
        self.assertEqual(scale(2), 6)
