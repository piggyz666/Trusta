"""Comparative evaluation: the same model, sandbox bank and test set, three ways of building the agent.

    python -m benchmarks.compare --mock                  # offline pipeline check, no model calls
    python -m benchmarks.compare --live --runs 3         # real model (DeepSeek key from .env), writes docs/compare/

A  naive agent     the model calls bank functions directly; a call is an executed operation
B  hardened agent  safety system prompt, every write waits for the customer's confirmation, high-risk operations
                   must go through an SMS-code step that the model itself is instructed to follow
C  TRUSTA          the system as shipped (competition safety profile)

See docs/compare/README.md for the method and how to read the results.
"""
