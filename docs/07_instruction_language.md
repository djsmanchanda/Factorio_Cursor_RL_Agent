# Instruction Language (FIL)

A minimal declarative language for factory intent.

Example:

goal electronic_circuit +1000/s
area rect(120,-40,260,80)
layout grid
scale vertical
priority speed
avoid trains

FIL is parsed into structured planner input.
