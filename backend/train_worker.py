"""Isolated, bounded training child. JSON in/out only; never deserialize code."""

import json
import sys
from datetime import date

from backend.ml_core import train_model


if __name__ == "__main__":
    request = json.load(sys.stdin)
    try:
        result = train_model(request["dataset"], date.fromisoformat(request["cutoff"]))
        print(json.dumps({"model": result}, allow_nan=False))
    except (ValueError, TypeError, ArithmeticError) as error:
        print(json.dumps({"error": str(error)}))
        sys.exit(2)
