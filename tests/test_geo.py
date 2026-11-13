"""The sign-up form offers the same cities as the shop's simulated customers."""
import unittest

from webapp.geo import GEO


class GeoTests(unittest.TestCase):
    def test_matches_the_simulator_cities(self):
        try:
            from ecommerce_sim.reference import CITIES
        except ImportError:
            self.skipTest("ecommerce-simulator is not installed")
        flat = {city: (state, region) for region, states in GEO.items() for state, cities in states.items() for city in cities}
        self.assertEqual(flat, {city: (info[0], info[1]) for city, info in CITIES.items()})
