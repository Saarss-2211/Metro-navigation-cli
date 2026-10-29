"""Network loading, routing, analysis, closures and usage-data processing."""
from __future__ import annotations

import csv
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .structures import LRUCache, MinHeap, PriorityQueue, Trie, UnionFind


@dataclass(frozen=True)
class Leg:
    source: str
    target: str
    line: str
    minutes: int
    km: float


class MetroNetwork:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.raw = json.loads((self.data_dir / "network.json").read_text(encoding="utf-8"))
        self.fares = json.loads((self.data_dir / "fares.json").read_text(encoding="utf-8"))
        self.candidates = json.loads((self.data_dir / "candidate_tracks.json").read_text(encoding="utf-8"))["tracks"]
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
        self.trie = Trie(self.stations.values())
        self.closed_stations, self.closed_edges = set(), set()
        self.revision = 0
        self.cache = LRUCache(256)

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
        for slab in self.fares["slabs"]:
            if km <= slab["max_km"]:
                return slab["fare"]
        return self.fares["slabs"][-1]["fare"]

    def _open(self, leg):
        return (leg.source not in self.closed_stations and leg.target not in self.closed_stations
                and (leg.source, leg.target, leg.line) not in self.closed_edges
                and (leg.target, leg.source, leg.line) not in self.closed_edges)

    def _route(self, origin, destination, strategy):
        start, goal = self.station_id(origin), self.station_id(destination)
        if start == goal:
            return {"stations": [start], "lines": [], "minutes": 0, "km": 0.0, "interchanges": 0}
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
        best, previous = {initial: (0, 0.0, 0)}, {}
        heap = MinHeap()
        heap.push((0, 0.0, 0), initial)
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
        labels = [{"state": initial_state, "km": 0.0, "minutes": 0, "changes": 0,
                   "prev": None, "leg": None, "leg_minutes": 0, "active": True}]
        by_state = {initial_state: [0]}
        heap = MinHeap()
        heap.push((0, 0.0, 0, 0), 0)
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
