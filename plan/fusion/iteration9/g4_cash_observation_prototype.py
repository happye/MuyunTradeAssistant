"""Compatibility exports for the accepted offline G4 prototype.

Production uses the single algorithm in src.core.cash_observations.
"""
from src.core.cash_observations import (
    CONTRACT_VERSION, ComparableGroup, ExternallyQualifiedIndustryPremise,
    FieldObservation, ObservationPoint, ObservationResult, observe_cash,
)

__all__ = ["CONTRACT_VERSION", "ComparableGroup", "ExternallyQualifiedIndustryPremise",
           "FieldObservation", "ObservationPoint", "ObservationResult", "observe_cash"]
