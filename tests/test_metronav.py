import json
import tempfile
import unittest
from pathlib import Path
from decimal import Decimal

from metronav.network import MetroNetwork
from metronav.structures import LRUCache, MinHeap, PriorityQueue, Trie, UnionFind


class StructuresTests(unittest.TestCase):
    def test_heap_orders_priorities(self):
        heap = MinHeap()
        for value in (5, 1, 4, 2):
            heap.push((value,), value)
        self.assertEqual([heap.pop()[1] for _ in range(4)], [1, 2, 4, 5])

    def test_trie_and_lru(self):
        trie = Trie(["Miyapur", "MG Bus Station", "Malakpet"])
        self.assertEqual(trie.autocomplete("m", 2), ["Malakpet", "MG Bus Station"])
        cache = LRUCache(2)
        cache.put("a", 1); cache.put("b", 2)
        self.assertEqual(cache.get("a"), 1)
        cache.put("c", 3)
        self.assertIsNone(cache.get("b"))

    def test_union_find(self):
        uf = UnionFind([1, 2, 3])
        self.assertTrue(uf.union(1, 2))
        self.assertFalse(uf.union(2, 1))
        self.assertNotEqual(uf.find(1), uf.find(3))

    def test_priority_queue_is_stable(self):
        queue = PriorityQueue()
        queue.push(1, "first")
        queue.push(0, "urgent")
        queue.push(1, "second")
        self.assertEqual([queue.pop(), queue.pop(), queue.pop()], ["urgent", "first", "second"])


class MetroNetworkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.write("network.json", {
            "city": "Test Metro",
            "interchange_penalty_minutes": 4,
            "stations": [{"id": x, "name": name} for x, name in
                         [("A", "Alpha"), ("B", "Bravo"), ("C", "Charlie"),
                          ("D", "Delta"), ("E", "Echo"), ("F", "Foxtrot")]],
            "lines": [{"name": "Red", "stations": ["A", "B", "D"]},
                      {"name": "Blue", "stations": ["A", "C"]},
                      {"name": "Green", "stations": ["C", "D"]},
                      {"name": "Gray", "stations": ["E", "F"]}],
            "connections": [
                {"from": "A", "to": "B", "line": "Red", "minutes": 10, "km": 1.0},
                {"from": "B", "to": "D", "line": "Red", "minutes": 10, "km": 1.0},
                {"from": "A", "to": "C", "line": "Blue", "minutes": 1, "km": 0.1},
                {"from": "C", "to": "D", "line": "Green", "minutes": 1, "km": 0.2},
                {"from": "E", "to": "F", "line": "Gray", "minutes": 2, "km": 1.0}]})
        self.write("fares.json", {"slabs": [{"max_km": 0.3, "fare": 5}, {"max_km": 10, "fare": 10}]})
        self.write("candidate_tracks.json", {"tracks": [
            {"id": "T1", "from": "D", "to": "E", "cost_crore": 20},
            {"id": "T2", "from": "A", "to": "F", "cost_crore": 5}]})
        (self.data / "journeys.txt").write_text(
            "timestamp,origin,destination,peak\n"
            "00:00,Alpha,Delta,1\n00:01,Alpha,Delta,0\n00:02,Alpha,Bravo,0\n00:03,Bravo,Charlie,0\n"
            "00:04,Unknown,Delta,0\n00:05,Alpha,Alpha,1\n00:06,Alpha\n", encoding="utf-8")
        self.net = MetroNetwork(self.data)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, data):
        (self.data / name).write_text(json.dumps(data), encoding="utf-8")

    def test_routes_honor_objective_and_fare_slabs(self):
        self.assertEqual(self.net.route("alpha", "DELTA", "fastest")["lines"], ["Blue", "Green"])
        fewest = self.net.route("Alpha", "Delta", "interchanges")
        self.assertEqual(fewest["lines"], ["Red", "Red"])
        cheapest = self.net.route("Alpha", "Delta", "cheapest")
        self.assertEqual(cheapest["lines"], ["Blue", "Green"])
        self.assertEqual(cheapest["km"], Decimal("0.3"))
        self.assertEqual(self.net.fare(cheapest["km"]), 5)
        self.assertEqual(self.net.fare(Decimal("0.30000000000000001")), 10)
        self.assertEqual(self.net.fare(0.1 + 0.2), 5)
        self.assertEqual(cheapest["minutes"], 6)

    def test_closures_invalidate_cached_route_and_reopen(self):
        self.assertEqual(self.net.route("Alpha", "Delta")["lines"], ["Blue", "Green"])
        self.net.close_track("Alpha", "Charlie", "Blue")
        self.assertEqual(self.net.route("Alpha", "Delta")["lines"], ["Red", "Red"])
        self.net.reopen_track("Alpha", "Charlie", "Blue")
        self.assertEqual(self.net.route("Alpha", "Delta")["lines"], ["Blue", "Green"])
        self.net.close_station("Charlie")
        self.assertEqual(self.net.route("Alpha", "Delta")["lines"], ["Red", "Red"])
        self.assertIn("Bravo", self.net.articulation_points())
        self.net.close_station("Bravo")
        with self.assertRaises(ValueError):
            self.net.route("Alpha", "Delta")
        self.net.reopen_station("Bravo")
        self.assertEqual(self.net.route("Alpha", "Delta")["lines"], ["Red", "Red"])
        self.net.reopen_station("Charlie")
        self.assertEqual(self.net.route("Alpha", "Delta")["lines"], ["Blue", "Green"])

    def test_components_articulation_reachability_and_expansion(self):
        self.assertEqual(len(self.net.components()), 2)
        self.assertEqual(self.net.articulation_points(), [])
        reachable = dict(self.net.reachable_within("Alpha", 6))
        self.assertEqual(reachable["Delta"], 6)
        tracks = self.net.expansion()
        self.assertEqual([t["id"] for t in tracks], ["T2"])

    def test_journey_validation_and_priority(self):
        stats = self.net.journey_stats(2)
        self.assertEqual((stats["valid"], stats["skipped"]), (4, 3))
        self.assertEqual(stats["busiest"], [("Alpha", 3), ("Delta", 2)])
        rows, skipped = self.net.prioritize_journeys(10)
        self.assertEqual(skipped, 3)
        self.assertEqual(rows[0][3], "1")

    def test_missing_fields_and_negative_data_are_reported(self):
        network_path = self.data / "network.json"
        network = json.loads(network_path.read_text(encoding="utf-8"))
        del network["interchange_penalty_minutes"]
        network_path.write_text(json.dumps(network), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "missing required field 'interchange_penalty_minutes'"):
            MetroNetwork(self.data)

        network["interchange_penalty_minutes"] = 4
        network["connections"][0]["km"] = -0.1
        network_path.write_text(json.dumps(network), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "km.*greater than zero"):
            MetroNetwork(self.data)

        network["connections"][0]["km"] = 1.0
        network["connections"][0]["minutes"] = -1
        network_path.write_text(json.dumps(network), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "minutes.*positive integer"):
            MetroNetwork(self.data)

        network["connections"][0]["minutes"] = 10
        network["interchange_penalty_minutes"] = -1
        network_path.write_text(json.dumps(network), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "interchange_penalty_minutes.*non-negative integer"):
            MetroNetwork(self.data)

        network["interchange_penalty_minutes"] = 4
        network_path.write_text(json.dumps(network), encoding="utf-8")
        fares_path = self.data / "fares.json"
        fares = json.loads(fares_path.read_text(encoding="utf-8"))
        fares["slabs"][0]["fare"] = -1
        fares_path.write_text(json.dumps(fares), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "fare slab 1 field 'fare'.*non-negative"):
            MetroNetwork(self.data)

        fares["slabs"][0]["fare"] = 5
        fares_path.write_text(json.dumps(fares), encoding="utf-8")
        tracks_path = self.data / "candidate_tracks.json"
        tracks = json.loads(tracks_path.read_text(encoding="utf-8"))
        tracks["tracks"][0]["cost_crore"] = -1
        tracks_path.write_text(json.dumps(tracks), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "cost_crore.*non-negative"):
            MetroNetwork(self.data)


if __name__ == "__main__":
    unittest.main()
