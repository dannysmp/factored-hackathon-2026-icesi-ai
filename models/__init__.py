"""Risk-model training and evaluation: signal probe, boosted comparison and calibration.

Each module is a command (``python -m models.probe``, ``models.boosted``, ``models.calibration``)
that reads the risk feature mart, appends to the experiment log and, for calibration, rewrites the
model card. Nothing here is imported by the running service.
"""
