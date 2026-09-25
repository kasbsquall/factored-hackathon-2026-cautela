"""Cautela agent core: identity, permissions, policy, tools, audit and handoff.

The core is plain Python so it can be tested without a web framework. Every control that decides what the system
may read or do lives here, in code, and never in model-generated prose.
"""
