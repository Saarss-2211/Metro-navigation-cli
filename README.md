# MetroNav

MetroNav is a Python 3.11+ command-line metro journey planner built from the supplied fictional sample network. It uses only the Python standard library. The original data files are kept in `data/` and are read without modification.

## Run

From this folder, activate your virtual environment and run:

```powershell
python main.py --help
python main.py route Miyapur "LB Nagar"
python -m unittest discover -s tests -v
```

Python 3.11 or newer is required. If you use the Windows Python launcher, create an environment with `py -3.11 -m venv .venv`, activate it with `.\.venv\Scripts\Activate.ps1`, then use `python main.py ...`.

## Commands

```text
python main.py route <origin> <destination> [--strategy fastest|interchanges|cheapest]
python main.py autocomplete <prefix> [--limit N]
python main.py close-station <station>
python main.py reopen-station <station>
python main.py close-track <station-a> <station-b> [--line LINE]
python main.py reopen-track <station-a> <station-b> [--line LINE]
python main.py components
python main.py critical
python main.py reachable <station> <minutes>
python main.py expand
python main.py stats [-k N]
python main.py requests [--limit N]
python main.py shell
```

Station names are matched case-insensitively. Use `shell` to run several commands against one in-memory network; closures and reopenings made in that session affect subsequent route and analysis commands. Each standalone invocation starts with the original network state.

## Supplied data

- `data/network.json` contains stations, lines, connections, travel times, distances, and the interchange penalty.
- `data/fares.json` defines the distance-based fare slabs in INR.
- `data/candidate_tracks.json` lists possible expansion links and their costs in crore INR.
- `data/journeys.txt` contains journey requests. Invalid rows are skipped and counted by the statistics and request commands.
- `data/DATA_README.md` documents the input formats. Keep all supplied data files unchanged.

## Algorithms and data structures

- The network is an adjacency-list graph; each listed connection is available in both directions.
- A custom binary min-heap drives Dijkstra route searches. The route state includes the line used to arrive, so the configured interchange penalty is added when the passenger changes lines.
- Fastest route minimizes time, then distance. Fewest-interchanges route minimizes line changes, then time. Cheapest route minimizes the fare slab first, then travel time, distance, and interchange count.
- Station autocomplete uses a Trie. Route results use an LRU cache implemented with a dictionary and doubly linked list; network changes increment a revision and clear the cache.
- Connected zones and expansion use Union-Find. Expansion applies Kruskal's algorithm to connect the already-built network components with candidate tracks.
- Critical stations are found with Tarjan's articulation-point DFS. Time-limited reachability uses a shortest-time search.
- Journey requests use a stable priority queue, with peak journeys served before off-peak journeys. Busiest stations are computed with a size-K min-heap from valid origin/destination counts.

## Complexity

Let `S` be station/arrival-line states, `E'` their transitions, `C` candidate tracks, and `J` journey rows. Fastest and fewest-interchanges Dijkstra searches take `O((S + E') log S)` time and `O(S + E')` space. Cheapest-route search retains nondominated distance/time labels; if `L` labels are generated, its work is `O(L log L + transitions(L))`, with an exponential worst case. Trie autocomplete takes `O(P + R log R)` here, where `P` is prefix length and `R` is the number of matches. LRU get/put are `O(1)`. Components and articulation points are `O(V + E)`. Expansion is `O((E + C) alpha(V) + C log C)` time, where alpha is the inverse Ackermann factor. Journey statistics use `O(J log K)` heap work after counting, plus `O(V)` counts; request ordering is `O(J log J)`.

## Project files

- `main.py` starts the command-line application.
- `metronav/cli.py` defines commands and formats their output.
- `metronav/network.py` loads data and implements routing, closures, analysis, expansion, and journey processing.
- `metronav/structures.py` contains the custom heap, Trie, LRU cache, Union-Find, and priority queue.
- `tests/test_metronav.py` contains the automated tests.

## Example

```text
> python main.py route Miyapur "LB Nagar" --strategy fastest
Strategy: fastest
Route: Miyapur -> JNTU College -> KPHB Colony -> Kukatpally -> Balanagar -> Moosapet -> Bharat Nagar -> Erragadda -> ESI Hospital -> SR Nagar -> Ameerpet -> Punjagutta -> Irrum Manzil -> Khairatabad -> Lakdikapul -> Assembly -> Nampally -> Gandhi Bhavan -> Osmania Medical College -> MG Bus Station -> Malakpet -> New Market -> Musarambagh -> Dilsukhnagar -> Chaitanyapuri -> Victoria Memorial -> LB Nagar
Lines: Red
Stops: 26 | Interchanges: 0
Travel time: 77 min | Distance: 45.1 km | Fare: INR 130
```

