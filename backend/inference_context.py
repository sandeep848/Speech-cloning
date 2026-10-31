"""Scope deterministic PyTorch settings to one inference, restoring prior policy."""
from contextlib import contextmanager

@contextmanager
def seeded_inference(seed):
    if seed is None:
        yield
        return
    import random
    import numpy as np
    import torch
    previous = torch.are_deterministic_algorithms_enabled()
    warn_only = torch.is_deterministic_algorithms_warn_only_enabled()
    python_state, numpy_state = random.getstate(), np.random.get_state()
    devices = list(range(torch.cuda.device_count())) if torch.cuda.is_available() else []
    try:
        with torch.random.fork_rng(devices=devices):
            random.seed(seed)
            np.random.seed(seed)
            torch.manual_seed(seed)
            torch.use_deterministic_algorithms(True)
            yield
    finally:
        random.setstate(python_state)
        np.random.set_state(numpy_state)
        torch.use_deterministic_algorithms(previous, warn_only=warn_only)
