"""Network loading, routing, analysis, closures and usage-data processing."""
from __future__ import annotations

import csv
import json
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

from .structures import LRUCache, MinHeap, PriorityQueue, Trie, UnionFind


@dataclass(frozen=True)
class Leg:
    source: str
    target: str
    line: str
    minutes: int
    km: Decimal


class MetroNetwork:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.raw = self._read_json("network.json")
        self.fares = self._read_json("fares.json")
        candidate_data = self._read_json("candidate_tracks.json")
        self._validate_data(candidate_data)
        self.candidates = candidate_data["tracks"]
        self.stations = {s["id"]: s["name"] for s in self.raw["stations"]}
        self.ids_by_name = {name.casefold(): sid for sid, name in self.stations.items()}
        self.lines = {line["name"]: line["stations"] for line in self.raw["lines"]}
        self.station_lines = {sid: set() for sid in self.stations}
        for line, stops in self.lines.items():
            for sid in stops:
                self.station_lines[sid].add(line)
        self.interchange_penalty = self.raw["interchange_penalty_minutes"]
        self.legs = []
        self.adjacency = {sid: [] for sid in self.stations}
        for edge in self.raw["connections"]:
            leg = Leg(edge["from"], edge["to"], edge["line"], edge["minutes"], edge["km"])
            self.legs.append(leg)
            self.adjacency[leg.source].append(leg)
            self.adjacency[leg.target].append(Leg(leg.target, leg.source, leg.line, leg.minutes, leg.km))
        distance_places = max(
            (max(0, -leg.km.as_tuple().exponent) for leg in self.legs),
            default=0,
        )
        self._km_quantum = Decimal(1).scaleb(-distance_places)
        self.trie = Trie(self.stations.values())
        self.closed_stations, self.closed_edges = set(), set()
        self.revision = 0
        self.cache = LRUCache(256)

    def _read_json(self, filename):
        path = self.data_dir / filename
        try:
            return json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)
        except FileNotFoundError as exc:
            raise ValueError(f"Required data file is missing: {path}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in {filename} at line {exc.lineno}, column {exc.colno}: {exc.msg}") from exc
        except OSError as exc:
            raise ValueError(f"Cannot read {filename}: {exc}") from exc

    @staticmethod
    def _required(record, key, filename, context):
        if not isinstance(record, dict):
            raise ValueError(f"{filename}: {context} must be an object")
        if key not in record:
            raise ValueError(f"{filename}: {context} is missing required field '{key}'")
        return record[key]

    @classmethod
    def _decimal_value(cls, value, filename, context, *, positive=False):
        if isinstance(value, bool):
            raise ValueError(f"{filename}: {context} must be a number")
        try:
            number = value if isinstance(value, Decimal) else Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            raise ValueError(f"{filename}: {context} must be a number") from None
        if not number.is_finite():
            raise ValueError(f"{filename}: {context} must be finite")
        if number < 0 or (positive and number == 0):
            qualifier = "greater than zero" if positive else "non-negative"
            raise ValueError(f"{filename}: {context} must be {qualifier}")
        return number

    def _validate_data(self, candidate_data):
        raw = self.raw
        city = self._required(raw, "city", "network.json", "root")
        if not isinstance(city, str) or not city.strip():
            raise ValueError("network.json: 'city' must be a non-empty string")
        penalty = self._required(raw, "interchange_penalty_minutes", "network.json", "root")
        if isinstance(penalty, bool) or not isinstance(penalty, int) or penalty < 0:
            raise ValueError("network.json: 'interchange_penalty_minutes' must be a non-negative integer")

        stations = self._required(raw, "stations", "network.json", "root")
        lines = self._required(raw, "lines", "network.json", "root")
        connections = self._required(raw, "connections", "network.json", "root")
        if not isinstance(stations, list) or not stations:
            raise ValueError("network.json: 'stations' must be a non-empty list")
        if not isinstance(lines, list) or not lines:
            raise ValueError("network.json: 'lines' must be a non-empty list")
        if not isinstance(connections, list):
            raise ValueError("network.json: 'connections' must be a list")

        station_ids, station_names = set(), set()
        for index, station in enumerate(stations, 1):
            context = f"station record {index}"
            sid = self._required(station, "id", "network.json", context)
            name = self._required(station, "name", "network.json", context)
            if not isinstance(sid, str) or not sid.strip():
                raise ValueError(f"network.json: {context} field 'id' must be a non-empty string")
            if not isinstance(name, str) or not name.strip():
                raise ValueError(f"network.json: {context} field 'name' must be a non-empty string")
            if sid in station_ids:
                raise ValueError(f"network.json: duplicate station id '{sid}'")
            if name.casefold() in station_names:
                raise ValueError(f"network.json: duplicate station name '{name}'")
            station_ids.add(sid); station_names.add(name.casefold())

        line_stops = {}
        for index, line in enumerate(lines, 1):
            context = f"line record {index}"
            name = self._required(line, "name", "network.json", context)
            stops = self._required(line, "stations", "network.json", context)
            if not isinstance(name, str) or not name.strip():
                raise ValueError(f"network.json: {context} field 'name' must be a non-empty string")
            if name in line_stops:
                raise ValueError(f"network.json: duplicate line name '{name}'")
            if not isinstance(stops, list) or len(stops) < 2:
                raise ValueError(f"network.json: line '{name}' must contain at least two station ids")
            if any(not isinstance(sid, str) for sid in stops):
                raise ValueError(f"network.json: line '{name}' station ids must be strings")
            if len(stops) != len(set(stops)):
                raise ValueError(f"network.json: line '{name}' contains a repeated station id")
            for sid in stops:
                if sid not in station_ids:
                    raise ValueError(f"network.json: line '{name}' references unknown station id '{sid}'")
            line_stops[name] = stops

        for index, edge in enumerate(connections, 1):
            context = f"connection record {index}"
            source = self._required(edge, "from", "network.json", context)
            target = self._required(edge, "to", "network.json", context)
            line = self._required(edge, "line", "network.json", context)
            minutes = self._required(edge, "minutes", "network.json", context)
            km = self._required(edge, "km", "network.json", context)
            if not all(isinstance(value, str) for value in (source, target, line)):
                raise ValueError(f"network.json: {context} fields 'from', 'to', and 'line' must be strings")
            if source not in station_ids or target not in station_ids:
                raise ValueError(f"network.json: {context} references an unknown station id")
            if source == target:
                raise ValueError(f"network.json: {context} must connect two different stations")
            if line not in line_stops:
                raise ValueError(f"network.json: {context} references unknown line '{line}'")
            stops = line_stops[line]
            if not any({source, target} == {a, b} for a, b in zip(stops, stops[1:])):
                raise ValueError(f"network.json: {context} stations are not neighbors on line '{line}'")
            if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes <= 0:
                raise ValueError(f"network.json: {context} field 'minutes' must be a positive integer")
            self._decimal_value(km, "network.json", f"{context} field 'km'", positive=True)

        slabs = self._required(self.fares, "slabs", "fares.json", "root")
        if not isinstance(slabs, list) or not slabs:
            raise ValueError("fares.json: 'slabs' must be a non-empty list")
        previous_limit, previous_fare = Decimal("-1"), Decimal("-1")
        for index, slab in enumerate(slabs, 1):
            context = f"fare slab {index}"
            limit = self._decimal_value(self._required(slab, "max_km", "fares.json", context), "fares.json", f"{context} field 'max_km'", positive=True)
            fare = self._decimal_value(self._required(slab, "fare", "fares.json", context), "fares.json", f"{context} field 'fare'")
            if limit <= previous_limit:
                raise ValueError("fares.json: slab 'max_km' values must be strictly increasing")
            if fare < previous_fare:
                raise ValueError("fares.json: fares must not decrease as distance increases")
            previous_limit, previous_fare = limit, fare

        tracks = self._required(candidate_data, "tracks", "candidate_tracks.json", "root")
        if not isinstance(tracks, list):
            raise ValueError("candidate_tracks.json: 'tracks' must be a list")
        track_ids = set()
        for index, track in enumerate(tracks, 1):
            context = f"track record {index}"
            track_id = self._required(track, "id", "candidate_tracks.json", context)
            source = self._required(track, "from", "candidate_tracks.json", context)
            target = self._required(track, "to", "candidate_tracks.json", context)
            cost = self._required(track, "cost_crore", "candidate_tracks.json", context)
            if not isinstance(track_id, str) or not track_id.strip():
                raise ValueError(f"candidate_tracks.json: {context} field 'id' must be a non-empty string")
            if track_id in track_ids:
                raise ValueError(f"candidate_tracks.json: duplicate track id '{track_id}'")
            track_ids.add(track_id)
            if not isinstance(source, str) or not isinstance(target, str):
                raise ValueError(f"candidate_tracks.json: {context} fields 'from' and 'to' must be strings")
            if source not in station_ids or target not in station_ids:
                raise ValueError(f"candidate_tracks.json: {context} references an unknown station id")
            if source == target:
                raise ValueError(f"candidate_tracks.json: {context} must connect two different stations")
            self._decimal_value(cost, "candidate_tracks.json", f"{context} field 'cost_crore'")

    def station_id(self, name_or_id):
        value = name_or_id.strip()
        if value in self.stations:
            return value
        sid = self.ids_by_name.get(value.casefold())
        if sid is None:
            raise ValueError(f"Unknown station: {name_or_id}")
        return sid

    def station_name(self, sid):
        return self.stations[sid]

    def fare(self, km):
        original_km = km
        km = self._decimal_value(km, "fare calculation", "distance")
        # Route sums are Decimal already. Normalize external floats to the precision
        # represented in the source network (for example, 0.1 + 0.2 -> 0.3 km).
        if isinstance(original_km, float):
            km = km.quantize(self._km_quantum, rounding=ROUND_HALF_UP)
        for slab in self.fares["slabs"]:
            if km <= Decimal(str(slab["max_km"])):
                return slab["fare"]
        return self.fares["slabs"][-1]["fare"]

    def _open(self, leg):
        return (leg.source not in self.closed_stations and leg.target not in self.closed_stations
                and (leg.source, leg.target, leg.line) not in self.closed_edges
                and (leg.target, leg.source, leg.line) not in self.closed_edges)

    def _route(self, origin, destination, strategy):
        start, goal = self.station_id(origin), self.station_id(destination)
        if start == goal:
            return {"stations": [start], "lines": [], "minutes": 0, "km": Decimal("0"), "interchanges": 0}
        if start in self.closed_stations or goal in self.closed_stations:
            raise ValueError("Origin or destination station is closed")
        cache_key = (self.revision, start, goal, strategy)
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached
        if strategy == "cheapest":
            result = self._cheapest_route(start, goal)
            self.cache.put(cache_key, result)
            return result

        # State includes the line used to arrive, so a line change is charged at the correct station.
        initial = (start, None)
        best, previous = {initial: (0, Decimal("0"), 0)}, {}
        heap = MinHeap()
        heap.push((0, Decimal("0"), 0), initial)
        final = None
        while heap:
            cost, state = heap.pop()
            if best.get(state) != cost:
                continue
            sid, old_line = state
            if sid == goal:
                final = state
                break
            for leg in self.adjacency[sid]:
                if not self._open(leg):
                    continue
                change = int(old_line is not None and old_line != leg.line)
                mins = leg.minutes + (self.interchange_penalty if change else 0)
                if strategy == "fastest":
                    new_cost = (cost[0] + mins, cost[1] + leg.km, cost[2] + change)
                    priority = new_cost
                elif strategy == "interchanges":
                    new_cost = (cost[0] + change, cost[1] + mins, cost[2] + leg.km)
                    priority = new_cost
                else:
                    raise ValueError("Unknown route strategy")
                nxt = (leg.target, leg.line)
                if nxt not in best or new_cost < best[nxt]:
                    best[nxt] = new_cost
                    previous[nxt] = (state, leg, mins)
                    heap.push(priority, nxt)
        if final is None:
            raise ValueError(f"No open route from {self.stations[start]} to {self.stations[goal]}")
        legs, states = [], [final]
        cur = final
        while cur != initial:
            prev, leg, mins = previous[cur]
            legs.append((leg, mins))
            states.append(prev)
            cur = prev
        legs.reverse(); states.reverse()
        route = {"stations": [s[0] for s in states], "lines": [x[0].line for x in legs],
                 "minutes": sum(x[1] for x in legs), "km": sum(x[0].km for x in legs),
                 "interchanges": sum(a != b for a, b in zip([x[0].line for x in legs], [x[0].line for x in legs][1:]))}
        self.cache.put(cache_key, route)
        return route

    def _cheapest_route(self, start, goal):
        """Keep nondominated distance/time labels, then choose by fare and time."""
        initial_state = (start, None)
        labels = [{"state": initial_state, "km": Decimal("0"), "minutes": 0, "changes": 0,
                   "prev": None, "leg": None, "leg_minutes": 0, "active": True}]
        by_state = {initial_state: [0]}
        heap = MinHeap()
        heap.push((0, Decimal("0"), 0, 0), 0)
        while heap:
            _priority, idx = heap.pop()
            label = labels[idx]
            if not label["active"]:
                continue
            sid, old_line = label["state"]
            for leg in self.adjacency[sid]:
                if not self._open(leg):
                    continue
                change = int(old_line is not None and old_line != leg.line)
                mins = leg.minutes + (self.interchange_penalty if change else 0)
                values = (label["km"] + leg.km, label["minutes"] + mins, label["changes"] + change)
                state = (leg.target, leg.line)
                existing = by_state.setdefault(state, [])
                if any(labels[j]["active"] and all(a <= b for a, b in zip((labels[j]["km"], labels[j]["minutes"], labels[j]["changes"]), values)) for j in existing):
                    continue
                for j in existing:
                    other = labels[j]
                    if other["active"] and all(a <= b for a, b in zip(values, (other["km"], other["minutes"], other["changes"]))):
                        other["active"] = False
                new_idx = len(labels)
                labels.append({"state": state, "km": values[0], "minutes": values[1], "changes": values[2],
                               "prev": idx, "leg": leg, "leg_minutes": mins, "active": True})
                existing.append(new_idx)
                heap.push((values[1], values[0], values[2], new_idx), new_idx)
        goals = [i for i, label in enumerate(labels) if label["active"] and label["state"][0] == goal]
        if not goals:
            raise ValueError(f"No open route from {self.stations[start]} to {self.stations[goal]}")
        chosen = min(goals, key=lambda i: (self.fare(labels[i]["km"]), labels[i]["minutes"], labels[i]["km"], labels[i]["changes"]))
        path, legs = [], []
        idx = chosen
        while idx is not None:
            label = labels[idx]
            path.append(label["state"][0])
            if label["leg"] is not None:
                legs.append((label["leg"], label["leg_minutes"]))
            idx = label["prev"]
        path.reverse(); legs.reverse()
        return {"stations": path, "lines": [leg.line for leg, _ in legs],
                "minutes": labels[chosen]["minutes"], "km": labels[chosen]["km"],
                "interchanges": labels[chosen]["changes"]}

    def route(self, origin, destination, strategy="fastest"):
        return self._route(origin, destination, strategy)

    def close_station(self, station):
        self.closed_stations.add(self.station_id(station)); self._changed()

    def reopen_station(self, station):
        self.closed_stations.discard(self.station_id(station)); self._changed()

    def close_track(self, a, b, line=None):
        a, b = self.station_id(a), self.station_id(b)
        matches = [e for e in self.legs if {e.source, e.target} == {a, b} and (line is None or e.line.casefold() == line.casefold())]
        if not matches:
            raise ValueError("Those stations are not directly connected on that line")
        self.closed_edges.update((e.source, e.target, e.line) for e in matches); self._changed()

    def reopen_track(self, a, b, line=None):
        a, b = self.station_id(a), self.station_id(b)
        matches = [e for e in self.legs if {e.source, e.target} == {a, b} and (line is None or e.line.casefold() == line.casefold())]
        if not matches:
            raise ValueError("Those stations are not directly connected on that line")
        for e in matches:
            self.closed_edges.discard((e.source, e.target, e.line)); self.closed_edges.discard((e.target, e.source, e.line))
        self._changed()

    def _changed(self):
        self.revision += 1
        self.cache.clear()

    def components(self):
        uf = UnionFind(self.stations)
        for edge in self.legs:
            if self._open(edge):
                uf.union(edge.source, edge.target)
        groups = {}
        for sid in self.stations:
            groups.setdefault(uf.find(sid), []).append(self.stations[sid])
        return sorted((sorted(group) for group in groups.values()), key=lambda g: (-len(g), g[0]))

    def articulation_points(self):
        # Tarjan DFS, treating parallel line links between the same pair as one simple edge.
        adjacency = {s: set() for s in self.stations}
        for edge in self.legs:
            if self._open(edge):
                adjacency[edge.source].add(edge.target); adjacency[edge.target].add(edge.source)
        disc, low, parent, result = {}, {}, {}, set()
        clock = 0
        def visit(u):
            nonlocal clock
            clock += 1; disc[u] = low[u] = clock
            children = 0
            for v in adjacency[u]:
                if v not in disc:
                    parent[v] = u; children += 1; visit(v); low[u] = min(low[u], low[v])
                    if u not in parent and children > 1: result.add(u)
                    if u in parent and low[v] >= disc[u]: result.add(u)
                elif parent.get(u) != v:
                    low[u] = min(low[u], disc[v])
        for sid in self.stations:
            if sid not in disc: visit(sid)
        return sorted((self.stations[s] for s in result), key=str.casefold)

    def reachable_within(self, start, limit):
        start = self.station_id(start)
        initial = (start, None)
        best, heap = {initial: 0}, MinHeap(); heap.push((0,), initial)
        while heap:
            (time,), state = heap.pop()
            if time != best[state] or time > limit: continue
            sid, old_line = state
            for edge in self.adjacency[sid]:
                if not self._open(edge): continue
                extra = edge.minutes + (self.interchange_penalty if old_line is not None and old_line != edge.line else 0)
                nt = time + extra
                nxt = (edge.target, edge.line)
                if nt <= limit and nt < best.get(nxt, float("inf")):
                    best[nxt] = nt; heap.push((nt,), nxt)
        fastest = {}
        for (sid, _line), time in best.items():
            fastest[sid] = min(time, fastest.get(sid, float("inf")))
        return sorted(((self.stations[s], t) for s, t in fastest.items()), key=lambda x: (x[1], x[0]))

    def expansion(self):
        uf = UnionFind(self.stations)
        for edge in self.legs: uf.union(edge.source, edge.target)
        chosen = []
        for edge in sorted(self.candidates, key=lambda e: (e["cost_crore"], e["id"])):
            if uf.union(edge["from"], edge["to"]): chosen.append(edge)
        return chosen

    def journey_stats(self, k=10):
        counts, valid, skipped = Counter(), 0, 0
        path = self.data_dir / "journeys.txt"
        with path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            next(reader, None)
            for row in reader:
                if len(row) != 4 or row[3].strip() not in {"0", "1"}:
                    skipped += 1; continue
                try: a, b = self.station_id(row[1]), self.station_id(row[2])
                except ValueError:
                    skipped += 1; continue
                if a == b:
                    skipped += 1; continue
                counts[a] += 1; counts[b] += 1; valid += 1
        heap = MinHeap()
        for sid, count in counts.items():
            heap.push((count, self.stations[sid]), sid)
            if len(heap._items) > k: heap.pop()
        top = []
        while heap:
            (count, _), sid = heap.pop(); top.append((self.stations[sid], count))
        return {"valid": valid, "skipped": skipped, "busiest": sorted(top, key=lambda x: (-x[1], x[0]))}

    def prioritize_journeys(self, limit=20):
        queue, skipped, seq = PriorityQueue(), 0, 0
        with (self.data_dir / "journeys.txt").open(encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f); next(reader, None)
            for row in reader:
                if len(row) != 4 or row[3].strip() not in {"0", "1"}:
                    skipped += 1; continue
                try:
                    a, b = self.station_id(row[1]), self.station_id(row[2])
                    if a == b: raise ValueError()
                except ValueError:
                    skipped += 1; continue
                seq += 1; queue.push(0 if row[3].strip() == "1" else 1, (row[0], self.stations[a], self.stations[b], row[3].strip()))
        result = [queue.pop() for _ in range(min(limit, seq))]
        return result, skipped
