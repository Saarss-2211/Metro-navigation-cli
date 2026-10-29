# MetroNav Data Pack

Do not edit these files. Your program must read them as provided.
All data is fictional sample data (station names are inspired by Hyderabad).

## network.json
- `interchange_penalty_minutes`: extra minutes when changing lines at a station (integer).
- `stations`: list of `{id, name}`. Ids are unique; names are unique. Match names case-insensitively.
- `lines`: list of `{name, stations}`. `stations` is the ordered list of station ids on that line.
- `connections`: list of `{from, to, line, minutes, km}`. Each is a two-way link between neighbouring stations on one line.
  Two stations can be linked on more than one line only if listed separately.
- A station appearing in more than one line is an interchange station.
- The network is NOT fully connected. Some lines are separate from the main network.

## fares.json
- `slabs`: ordered list of `{max_km, fare}`. The fare for a route is the fare of the first slab where
  `total_km <= max_km`. Total km is the sum of `km` over all connections used. Currency: INR.

## candidate_tracks.json
- `tracks`: list of `{id, from, to, cost_crore}`. Possible new tracks between two stations (two-way), cost in crore INR.
- Used only for the `expand` command. Treat the existing network as already built.

## journeys.txt
- CSV with a header line: `timestamp,origin,destination,peak`
- `origin` and `destination` are station NAMES (case-insensitive). `peak` is 1 (peak hour) or 0.
- The file contains a few INVALID lines on purpose (unknown station, missing field, malformed line,
  same origin and destination). A journey is valid only if it has 4 fields, both stations exist,
  origin != destination, and peak is 0 or 1. Skip invalid lines and report how many were skipped.
