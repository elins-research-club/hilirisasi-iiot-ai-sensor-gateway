"""Versioned gateway-centric semantic preprocessing.

The node owns hardware acquisition/integrity. This package owns optional
semantic filtering before resampling, feature extraction, and model input.
"""

from .filters import FilterSpec
from .pipeline import GatewaySemanticPreprocessor

__all__ = ["FilterSpec", "GatewaySemanticPreprocessor"]
