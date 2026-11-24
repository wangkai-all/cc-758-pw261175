"""Roster-wise rating analysis for fictional team matches.

The package covers:

* a synthetic, reproducible match generator with known latent skill paths;
* per-match approximate Bayesian filtering (Scalar Kalman / Glicko-like);
* small-window joint least-squares refits for exact local comparison;
* dependence diagnostics (unplayed groups, perma-bound lineups, draws);
* checkpointed replay with an immutable published-board version history.
"""
