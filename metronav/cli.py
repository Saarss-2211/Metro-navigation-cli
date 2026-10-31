"""Command-line interface for MetroNav."""
from __future__ import annotations

import argparse
from pathlib import Path
import shlex

from .network import MetroNetwork


def build_parser():
    p = argparse.ArgumentParser(prog="metronav", description="MetroNav journey planner")
    p.add_argument("--data", type=Path, default=Path(__file__).resolve().parent.parent / "data", help="folder containing supplied JSON and journey data")
    sub = p.add_subparsers(dest="command", required=True)
    route = sub.add_parser("route", help="find a route")
    route.add_argument("origin"); route.add_argument("destination")
    route.add_argument("--strategy", choices=("fastest", "interchanges", "cheapest"), default="fastest")
    auto = sub.add_parser("autocomplete", help="suggest station names")
    auto.add_argument("prefix"); auto.add_argument("--limit", type=int, default=10)
    for name in ("close-station", "reopen-station"):
        q = sub.add_parser(name); q.add_argument("station")
    for name in ("close-track", "reopen-track"):
        q = sub.add_parser(name); q.add_argument("station_a"); q.add_argument("station_b"); q.add_argument("--line")
    sub.add_parser("components", help="show connected network zones")
    sub.add_parser("critical", help="show articulation stations")
    reach = sub.add_parser("reachable", help="stations reachable within a time limit")
    reach.add_argument("station"); reach.add_argument("minutes", type=int)
    expand = sub.add_parser("expand", help="minimum-cost candidate network expansion")
    stats = sub.add_parser("stats", help="busiest stations from journey data")
    stats.add_argument("-k", type=int, default=10)
    top = sub.add_parser("top-stations", help="show the busiest stations")
    top.add_argument("-k", type=int, default=10)
    queue = sub.add_parser("requests", help="show journeys ordered by peak priority")
    queue.add_argument("--limit", type=int, default=20)
    sub.add_parser("shell", help="interactive session for routes and network closures")
    return p


def show_route(net, args):
    result = net.route(args.origin, args.destination, args.strategy)
    print(f"Strategy: {args.strategy}")
    print("Route: " + " -> ".join(net.station_name(s) for s in result["stations"]))
    if result["lines"]:
        sequence = []
        for line in result["lines"]:
            if not sequence or line != sequence[-1]:
                sequence.append(line)
        print("Lines: " + " -> ".join(sequence))
    print(f"Stops: {len(result['stations']) - 1} | Interchanges: {result['interchanges']}")
    print(f"Travel time: {result['minutes']} min | Distance: {result['km']:.1f} km | Fare: INR {net.fare(result['km'])}")


def run(args, net=None):
    net = net or MetroNetwork(args.data)
    if args.command == "route": show_route(net, args)
    elif args.command == "autocomplete":
        if args.limit < 0: raise ValueError("Autocomplete limit must be non-negative")
        for name in net.trie.autocomplete(args.prefix, max(args.limit, 0)): print(name)
    elif args.command == "close-station": net.close_station(args.station); print(f"Closed station: {args.station}")
    elif args.command == "reopen-station": net.reopen_station(args.station); print(f"Reopened station: {args.station}")
    elif args.command in {"close-track", "reopen-track"}:
        method = net.close_track if args.command == "close-track" else net.reopen_track
        method(args.station_a, args.station_b, args.line); print(f"Track updated: {args.station_a} <-> {args.station_b}")
    elif args.command == "components":
        for i, group in enumerate(net.components(), 1): print(f"Zone {i} ({len(group)} stations): " + ", ".join(group))
    elif args.command == "critical":
        points = net.articulation_points(); print("Critical stations: " + (", ".join(points) if points else "none"))
    elif args.command == "reachable":
        if args.minutes < 0: raise ValueError("Time limit must be non-negative")
        for name, mins in net.reachable_within(args.station, args.minutes): print(f"{name}: {mins} min")
    elif args.command == "expand":
        tracks = net.expansion()
        print("Tracks to connect existing zones:")
        for edge in tracks: print(f"{edge['id']}: {net.station_name(edge['from'])} <-> {net.station_name(edge['to'])} - INR {edge['cost_crore']} crore")
        print(f"Total: INR {sum(e['cost_crore'] for e in tracks)} crore")
    elif args.command in {"stats", "top-stations"}:
        if args.k < 1: raise ValueError("K must be at least 1")
        data = net.journey_stats(args.k)
        print(f"Valid journeys: {data['valid']} | Skipped invalid rows: {data['skipped']}")
        print("Busiest stations:")
        for name, count in data["busiest"]: print(f"{name}: {count} passenger endpoints")
    elif args.command == "requests":
        if args.limit < 0: raise ValueError("Request limit must be non-negative")
        rows, skipped = net.prioritize_journeys(max(args.limit, 0))
        print(f"Priority ordered requests (peak first); invalid rows skipped: {skipped}")
        for timestamp, origin, destination, peak in rows:
            print(f"{timestamp} | {'peak' if peak == '1' else 'off-peak'} | {origin} -> {destination}")


def main():
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "shell":
        try:
            net = MetroNetwork(args.data)
        except (ValueError, OSError, KeyError) as exc:
            parser.error(str(exc))
        print("MetroNav session. Enter commands (for example: route Miyapur LB Nagar); type exit to quit.")
        while True:
            try: line = input("metronav> ").strip()
            except (EOFError, KeyboardInterrupt):
                print(); break
            if line.casefold() in {"exit", "quit"}: break
            if not line: continue
            try:
                inner = parser.parse_args(shlex.split(line))
                if inner.command == "shell": raise ValueError("shell cannot be nested")
                run(inner, net)
            except SystemExit:
                continue
            except (ValueError, OSError, KeyError) as exc:
                print(f"Error: {exc}")
        return
    try:
        run(args)
    except (ValueError, OSError, KeyError) as exc:
        parser.error(str(exc))

