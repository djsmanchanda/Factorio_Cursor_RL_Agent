# Project Description

This project aims to build an autonomous agent for Factorio that can be
prompted with high-level production goals and execute them deterministically
and efficiently.

Example goals:
- Increase electronic circuit output by 1000/s in a given area
- Scale an existing smelting line vertically
- Replicate a high-efficiency grid layout N times
- Optimize for speed, power, or UPS

The system decomposes the problem into:
1. Goal interpretation
2. Factory planning and layout synthesis
3. Construction prioritization
4. Low-level execution
