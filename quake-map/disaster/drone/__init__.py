"""Simulated drone for the disaster dashboard (a showcase: there is no real drone).

Pure-Python modules (config, georef, energy, clock, world, truth, scan, priority, planner, feedback, sim, service) import no web
framework and no torch, so they can be tested alone. `bridge` is the only module that touches FastAPI. `worker/` holds the optional
Drone-SLAM physics process (torch).
"""
