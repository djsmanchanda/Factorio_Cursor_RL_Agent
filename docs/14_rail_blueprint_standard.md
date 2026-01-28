# Rail Blueprint Standard

This document defines the canonical rail infrastructure used by the system.
All rail placement is template-driven. The agent never invents rail designs.

---

## 1. Design Goals

The rail system must be:
- Directional
- Predictable
- UPS-efficient
- Infinitely extensible
- Upgradeable without demolition

Rail infrastructure is treated as **permanent city infrastructure**.

---

## 2. Corridor Geometry

### 2.1 Canonical Cross-Section

From left to right:

[ Future Rapid Lane ][ Outbound Lane ][ Inbound Lane ][ Service / Buffer ]

Initial build:

[ EMPTY ][ ➡ ][ ⬅ ][ EMPTY ]

Tile width (example):
- Each lane: 2 rails + signals
- Total reserved width: fixed globally

Once defined, this width is never changed.

---

## 3. Directionality Rules

- Each rail lane has exactly one direction
- No bidirectional rails
- No lane switching outside junction templates
- Trains never reverse on mainlines

This simplifies signaling and routing logic.

---

## 4. Signaling Templates

Signaling is applied using **predefined templates**.

### 4.1 Straight Segment
- Rail signals at fixed block intervals
- Block length chosen for max train length

### 4.2 Intersection
- Chain signals before entry
- Rail signals on exit
- No station blocks inside intersections

### 4.3 Junction Types
- T-junction
- 4-way crossing
- Block entry/exit junction

Each junction has:
- A blueprint
- A known block graph effect
- A throughput estimate

---

## 5. Stations

Stations are **never placed on mainlines**.

Rules:
- Stations live on sidings
- Sidings reconnect after station
- No dead-ends on corridors

Station blueprints encode:
- Train length
- Stackers
- Entry/exit signaling

---

## 6. Upgrade Path (Rapid Lane)

The reserved rapid lane is activated by:
- Laying rails in the reserved space
- Applying express-only signal templates
- Restricting usage to high-priority trains

No existing traffic is interrupted.

---

## 7. Blueprint Naming Convention

Examples:

- RAIL_CORRIDOR_STRAIGHT_V1
- RAIL_JUNCTION_4WAY_V2
- RAIL_STATION_SIDING_L4_V1

Blueprints are immutable once released.
New versions are additive, never destructive.
